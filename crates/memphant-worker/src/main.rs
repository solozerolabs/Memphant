//! The reflect worker: claims queued jobs (SKIP LOCKED in Postgres) and
//! compiles them through the same `MemoryService` path the public reflect
//! verb uses. `MEMPHANT_WORKER_ONCE=1` runs one tick; `MEMPHANT_WORKER_DRAIN=1`
//! runs ticks to empty. Both exit deterministically.

use std::time::Duration;

const DEFAULT_BATCH: usize = 64;
const MAX_BATCH: usize = 1024;
const TICK: Duration = Duration::from_millis(500);
const MAX_BACKOFF: Duration = Duration::from_secs(30);

/// Running totals across the ticks of one drain.
#[derive(Default)]
struct TickTotals {
    completed: usize,
    failed: usize,
    retried: usize,
    deferred: usize,
}

impl TickTotals {
    fn add(&mut self, tick: &memphant_core::WorkerTickOutcome) {
        self.completed += tick.completed;
        self.failed += tick.failed;
        self.retried += tick.retried;
        self.deferred += tick.deferred;
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WorkerMode {
    Daemon,
    Once,
    Drain,
}

// The drain-exit decision lives in `memphant-core` so this binary and the
// in-process bench drain (`memphant-eval::bench_lme`) share ONE mechanism.
use memphant_core::service::drain_finished;

fn worker_mode(once: bool, drain: bool) -> Result<WorkerMode, &'static str> {
    match (once, drain) {
        (false, false) => Ok(WorkerMode::Daemon),
        (true, false) => Ok(WorkerMode::Once),
        (false, true) => Ok(WorkerMode::Drain),
        (true, true) => {
            Err("MEMPHANT_WORKER_ONCE and MEMPHANT_WORKER_DRAIN are mutually exclusive")
        }
    }
}

fn parse_env_flag(name: &str, value: Option<&str>) -> Result<bool, String> {
    let value = value.unwrap_or_default().trim();
    match value.to_ascii_lowercase().as_str() {
        "" | "0" | "false" | "no" | "off" => Ok(false),
        "1" | "true" | "yes" | "on" => Ok(true),
        _ => Err(format!(
            "{name} must be one of 1/0/true/false/yes/no/on/off, got {value:?}"
        )),
    }
}

fn env_flag(name: &str) -> Result<bool, String> {
    match std::env::var(name) {
        Ok(value) => parse_env_flag(name, Some(&value)),
        Err(std::env::VarError::NotPresent) => parse_env_flag(name, None),
        Err(std::env::VarError::NotUnicode(value)) => {
            Err(format!("{name} must be valid Unicode, got {value:?}"))
        }
    }
}

fn backoff_delay(consecutive_errors: u32) -> Duration {
    let multiplier = 1_u32.checked_shl(consecutive_errors).unwrap_or(u32::MAX);
    TICK.saturating_mul(multiplier).min(MAX_BACKOFF)
}

#[derive(Default)]
struct DaemonErrorState {
    consecutive_errors: u32,
}

enum DaemonTick<'a> {
    Idle,
    Completed(&'a memphant_core::WorkerTickOutcome),
    Error(&'a str),
}

impl DaemonErrorState {
    fn delay(&self) -> Duration {
        backoff_delay(self.consecutive_errors)
    }

    fn tick_lines(&mut self, tick: DaemonTick<'_>) -> Vec<String> {
        match tick {
            DaemonTick::Error(error) => {
                self.consecutive_errors = self.consecutive_errors.saturating_add(1);
                let count = self.consecutive_errors;
                if count == 1 || count.is_multiple_of(10) {
                    vec![format!(
                        "memphant-worker: tick error: {error} (consecutive={count}, delay={:?})",
                        self.delay()
                    )]
                } else {
                    Vec::new()
                }
            }
            DaemonTick::Idle | DaemonTick::Completed(_) => {
                let failed_ticks = std::mem::take(&mut self.consecutive_errors);
                let mut lines = Vec::with_capacity(2);
                if failed_ticks > 0 {
                    lines.push(format!(
                        "memphant-worker: recovered after {failed_ticks} failed ticks"
                    ));
                }
                if let DaemonTick::Completed(tick) = tick {
                    lines.push(format!(
                        "memphant-worker: completed={} failed={} retried={} deferred={}",
                        tick.completed, tick.failed, tick.retried, tick.deferred
                    ));
                }
                lines
            }
        }
    }
}

fn worker_batch_from_value(value: Option<&str>) -> Result<usize, String> {
    let Some(value) = value else {
        return Ok(DEFAULT_BATCH);
    };
    value
        .trim()
        .parse::<usize>()
        .ok()
        .filter(|value| (1..=MAX_BATCH).contains(value))
        .ok_or_else(|| format!("must be an integer from 1 through {MAX_BATCH}, got {value:?}"))
}

#[tokio::main]
async fn main() {
    let batch =
        worker_batch_from_value(std::env::var("MEMPHANT_WORKER_BATCH_SIZE").ok().as_deref())
            .unwrap_or_else(|error| panic!("memphant-worker: MEMPHANT_WORKER_BATCH_SIZE: {error}"));
    let once =
        env_flag("MEMPHANT_WORKER_ONCE").unwrap_or_else(|error| panic!("memphant-worker: {error}"));
    let drain = env_flag("MEMPHANT_WORKER_DRAIN")
        .unwrap_or_else(|error| panic!("memphant-worker: {error}"));
    let mode = worker_mode(once, drain).unwrap_or_else(|error| panic!("memphant-worker: {error}"));
    let store = memphant_runtime::build_worker_store()
        .await
        .expect("memphant-worker: store construction failed");
    eprintln!("memphant-worker: store={}", store.name());
    let service = memphant_runtime::build_worker_service(store);

    if mode == WorkerMode::Once {
        let tick = service
            .run_worker_tick(batch)
            .await
            .expect("memphant-worker: tick failed");
        println!(
            "memphant-worker: once completed={} failed={} retried={} deferred={}",
            tick.completed, tick.failed, tick.retried, tick.deferred
        );
        return;
    }
    if mode == WorkerMode::Drain {
        let mut total = crate::TickTotals::default();
        let dead_letters_before = service
            .worker_dead_letter_count()
            .await
            .expect("memphant-worker: dead-letter baseline failed");
        loop {
            let tick = service
                .run_worker_tick(batch)
                .await
                .expect("memphant-worker: drain tick failed");
            total.add(&tick);
            let pending = service
                .pending_worker_job_count()
                .await
                .expect("memphant-worker: pending-job count failed");
            let dead_letters_after = service
                .worker_dead_letter_count()
                .await
                .expect("memphant-worker: dead-letter count failed");
            if drain_finished(pending, dead_letters_before, dead_letters_after)
                .unwrap_or_else(|error| panic!("memphant-worker: {error}"))
            {
                break;
            }
            if tick.is_idle() {
                tokio::time::sleep(TICK).await;
            }
        }
        // Failure counts are printed on the drain line, not just logged per
        // job: a drain that completed zero and failed everything used to be
        // indistinguishable here from a drain with nothing to do.
        println!(
            "memphant-worker: drain completed={} failed={} retried={} deferred={}",
            total.completed, total.failed, total.retried, total.deferred
        );
        return;
    }

    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .expect("install SIGTERM handler");
    let mut error_state = DaemonErrorState::default();
    loop {
        tokio::select! {
            _ = sigterm.recv() => {
                eprintln!("memphant-worker: SIGTERM — draining and shutting down");
                break;
            }
            _ = tokio::signal::ctrl_c() => {
                eprintln!("memphant-worker: interrupt — shutting down");
                break;
            }
            _ = tokio::time::sleep(error_state.delay()) => {
                let tick = service.run_worker_tick(batch).await;
                let outcome = match &tick {
                    Ok(tick) if tick.is_idle() => DaemonTick::Idle,
                    Ok(tick) => DaemonTick::Completed(tick),
                    Err(error) => DaemonTick::Error(&error.to_string()),
                };
                for line in error_state.tick_lines(outcome) {
                    eprintln!("{line}");
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use memphant_core::WorkerTickOutcome;

    use super::{
        DaemonErrorState, DaemonTick, WorkerMode, backoff_delay, drain_finished, parse_env_flag,
        worker_batch_from_value, worker_mode,
    };

    #[test]
    fn parse_env_flag_accepts_documented_spellings() {
        for value in ["1", "true", "yes", "on", " TRUE ", "YeS", "\tON\n"] {
            assert_eq!(parse_env_flag("FLAG", Some(value)), Ok(true), "{value:?}");
        }
        for value in [
            "", " ", "0", "false", "no", "off", " FALSE ", "nO", "\tOFF\n",
        ] {
            assert_eq!(parse_env_flag("FLAG", Some(value)), Ok(false), "{value:?}");
        }
        assert_eq!(parse_env_flag("FLAG", None), Ok(false));
    }

    #[test]
    fn parse_env_flag_rejects_other_values_with_context() {
        for value in ["maybe", "2", "y", "enabled"] {
            assert_eq!(
                parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(value)),
                Err(format!(
                    "MEMPHANT_WORKER_DRAIN must be one of 1/0/true/false/yes/no/on/off, got {value:?}"
                ))
            );
        }
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(" maybe ")),
            Err(
                "MEMPHANT_WORKER_DRAIN must be one of 1/0/true/false/yes/no/on/off, got \"maybe\""
                    .into()
            )
        );
    }

    #[test]
    fn backoff_is_exponential_and_capped() {
        assert_eq!(backoff_delay(0), Duration::from_millis(500));
        assert_eq!(backoff_delay(1), Duration::from_secs(1));
        assert_eq!(backoff_delay(2), Duration::from_secs(2));
        assert_eq!(backoff_delay(5), Duration::from_secs(16));
        assert_eq!(backoff_delay(6), Duration::from_secs(30));
        assert_eq!(backoff_delay(u32::MAX), Duration::from_secs(30));
    }

    #[test]
    fn daemon_error_state_controls_delay_logging_and_recovery() {
        let mut state = DaemonErrorState::default();
        assert_eq!(state.delay(), Duration::from_millis(500));
        assert_eq!(
            state.tick_lines(DaemonTick::Error("boom")),
            ["memphant-worker: tick error: boom (consecutive=1, delay=1s)"]
        );
        assert_eq!(state.delay(), Duration::from_secs(1));
        assert!(state.tick_lines(DaemonTick::Error("boom")).is_empty());
        for _ in 3..10 {
            assert!(state.tick_lines(DaemonTick::Error("boom")).is_empty());
        }
        assert_eq!(
            state.tick_lines(DaemonTick::Error("still broken")),
            ["memphant-worker: tick error: still broken (consecutive=10, delay=30s)"]
        );
        assert_eq!(
            state.tick_lines(DaemonTick::Idle),
            ["memphant-worker: recovered after 10 failed ticks"]
        );
        assert!(state.tick_lines(DaemonTick::Idle).is_empty());
        assert_eq!(state.delay(), Duration::from_millis(500));
        assert_eq!(
            state.tick_lines(DaemonTick::Error("again")),
            ["memphant-worker: tick error: again (consecutive=1, delay=1s)"]
        );
    }

    #[test]
    fn daemon_error_state_orders_recovery_before_completed_tick() {
        let mut state = DaemonErrorState::default();
        state.tick_lines(DaemonTick::Error("boom"));
        let tick = WorkerTickOutcome {
            completed: 2,
            failed: 1,
            retried: 3,
            deferred: 4,
        };
        assert_eq!(
            state.tick_lines(DaemonTick::Completed(&tick)),
            [
                "memphant-worker: recovered after 1 failed ticks",
                "memphant-worker: completed=2 failed=1 retried=3 deferred=4",
            ]
        );
        assert_eq!(
            state.tick_lines(DaemonTick::Completed(&tick)),
            ["memphant-worker: completed=2 failed=1 retried=3 deferred=4"]
        );
    }

    #[test]
    fn daemon_error_count_saturates() {
        let mut state = DaemonErrorState {
            consecutive_errors: u32::MAX,
        };
        assert!(state.tick_lines(DaemonTick::Error("boom")).is_empty());
        assert_eq!(state.consecutive_errors, u32::MAX);
        assert_eq!(state.delay(), Duration::from_secs(30));
    }

    #[test]
    fn worker_modes_are_distinct_and_conflicts_fail() {
        assert_eq!(worker_mode(false, false).unwrap(), WorkerMode::Daemon);
        assert_eq!(worker_mode(true, false).unwrap(), WorkerMode::Once);
        assert_eq!(worker_mode(false, true).unwrap(), WorkerMode::Drain);
        assert!(worker_mode(true, true).is_err());
    }

    #[test]
    fn worker_batch_is_configurable_and_bounded() {
        assert_eq!(worker_batch_from_value(None), Ok(64));
        assert_eq!(worker_batch_from_value(Some("1")), Ok(1));
        assert_eq!(worker_batch_from_value(Some("1024")), Ok(1024));
        assert!(worker_batch_from_value(Some("0")).is_err());
        assert!(worker_batch_from_value(Some("1025")).is_err());
        assert!(worker_batch_from_value(Some("wide")).is_err());
    }

    #[test]
    fn drain_waits_for_delayed_retries_and_rejects_new_dead_letters() {
        assert!(!drain_finished(1, 0, 0).unwrap());
        assert!(drain_finished(0, 0, 0).unwrap());
        assert_eq!(
            drain_finished(0, 2, 3).unwrap_err(),
            "drain produced dead-lettered jobs"
        );
    }
}
