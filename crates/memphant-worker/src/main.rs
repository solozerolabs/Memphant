//! The reflect worker: claims queued jobs (SKIP LOCKED in Postgres) and
//! compiles them through the same `MemoryService` path the public reflect
//! verb uses. `MEMPHANT_WORKER_ONCE=1` runs one tick; `MEMPHANT_WORKER_DRAIN=1`
//! runs ticks to empty. Both exit deterministically.

use std::{fmt::Display, time::Duration};

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

fn parse_env_flag(name: &str, value: Option<&str>) -> Result<bool, String> {
    let Some(value) = value.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(false);
    };

    if ["1", "true", "yes", "on"]
        .iter()
        .any(|candidate| value.eq_ignore_ascii_case(candidate))
    {
        Ok(true)
    } else if ["0", "false", "no", "off"]
        .iter()
        .any(|candidate| value.eq_ignore_ascii_case(candidate))
    {
        Ok(false)
    } else {
        Err(format!(
            "{name} must be one of 1/0/true/false/yes/no/on/off, got {value:?}"
        ))
    }
}

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

fn backoff_delay(consecutive_errors: u32) -> Duration {
    if consecutive_errors >= 6 {
        MAX_BACKOFF
    } else {
        (TICK * (1 << consecutive_errors)).min(MAX_BACKOFF)
    }
}

#[derive(Default)]
struct DaemonRetryState {
    consecutive_errors: u32,
}

impl DaemonRetryState {
    fn delay(&self) -> Duration {
        backoff_delay(self.consecutive_errors)
    }

    fn record_error(&mut self, error: &impl Display) -> Option<String> {
        self.consecutive_errors = self.consecutive_errors.saturating_add(1);
        (self.consecutive_errors == 1 || self.consecutive_errors.is_multiple_of(10)).then(|| {
            let consecutive_errors = self.consecutive_errors;
            let delay = self.delay();
            format!(
                "memphant-worker: tick error (consecutive={consecutive_errors}, delay={delay:?}): {error}"
            )
        })
    }

    fn record_success(&mut self) -> u32 {
        std::mem::take(&mut self.consecutive_errors)
    }
}

#[tokio::main]
async fn main() {
    let batch =
        worker_batch_from_value(std::env::var("MEMPHANT_WORKER_BATCH_SIZE").ok().as_deref())
            .unwrap_or_else(|error| panic!("memphant-worker: MEMPHANT_WORKER_BATCH_SIZE: {error}"));
    let once = parse_env_flag(
        "MEMPHANT_WORKER_ONCE",
        std::env::var("MEMPHANT_WORKER_ONCE").ok().as_deref(),
    )
    .unwrap_or_else(|error| panic!("memphant-worker: {error}"));
    let drain = parse_env_flag(
        "MEMPHANT_WORKER_DRAIN",
        std::env::var("MEMPHANT_WORKER_DRAIN").ok().as_deref(),
    )
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
    let mut interrupt = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())
        .expect("install interrupt handler");
    let mut retry_state = DaemonRetryState::default();
    loop {
        tokio::select! {
            _ = sigterm.recv() => {
                eprintln!("memphant-worker: SIGTERM — draining and shutting down");
                break;
            }
            _ = interrupt.recv() => {
                eprintln!("memphant-worker: interrupt — shutting down");
                break;
            }
            _ = tokio::time::sleep(retry_state.delay()) => {
                match service.run_worker_tick(batch).await {
                    Ok(tick) => {
                        let failed_ticks = retry_state.record_success();
                        if failed_ticks > 0 {
                            eprintln!("memphant-worker: recovered after {failed_ticks} failed ticks");
                        }
                        if !tick.is_idle() {
                            eprintln!(
                                "memphant-worker: completed={} failed={} retried={} deferred={}",
                                tick.completed, tick.failed, tick.retried, tick.deferred
                            );
                        }
                    }
                    Err(error) => {
                        if let Some(message) = retry_state.record_error(&error) {
                            eprintln!("{message}");
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{
        DaemonRetryState, WorkerMode, backoff_delay, drain_finished, parse_env_flag,
        worker_batch_from_value, worker_mode,
    };

    #[test]
    fn parse_env_flag_accepts_documented_values() {
        for value in [None, Some(""), Some("   ")] {
            assert_eq!(parse_env_flag("TEST_FLAG", value), Ok(false));
        }
        for (value, expected) in [
            ("1", true),
            ("true", true),
            ("yes", true),
            ("on", true),
            ("0", false),
            ("false", false),
            ("no", false),
            ("off", false),
        ] {
            assert_eq!(parse_env_flag("TEST_FLAG", Some(value)), Ok(expected));
            let padded_uppercase = format!(" \t{}\n", value.to_ascii_uppercase());
            assert_eq!(
                parse_env_flag("TEST_FLAG", Some(&padded_uppercase)),
                Ok(expected),
                "value={padded_uppercase:?}"
            );
        }
    }

    #[test]
    fn parse_env_flag_rejects_other_values_with_the_variable_name() {
        for (value, displayed) in [("maybe", "maybe"), ("2", "2"), (" true-ish ", "true-ish")] {
            assert_eq!(
                parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(value)),
                Err(format!(
                    "MEMPHANT_WORKER_DRAIN must be one of 1/0/true/false/yes/no/on/off, got {displayed:?}"
                ))
            );
        }
    }

    #[test]
    fn backoff_delay_is_exponential_and_capped() {
        assert_eq!(backoff_delay(0), Duration::from_millis(500));
        assert_eq!(backoff_delay(1), Duration::from_secs(1));
        assert_eq!(backoff_delay(2), Duration::from_secs(2));
        assert_eq!(backoff_delay(5), Duration::from_secs(16));
        assert_eq!(backoff_delay(u32::MAX), Duration::from_secs(30));
    }

    #[test]
    fn daemon_retry_state_tracks_logging_recovery_and_reset() {
        let mut state = DaemonRetryState::default();
        assert_eq!(state.delay(), Duration::from_millis(500));

        for count in 1..=20 {
            let message = state.record_error(&"database unavailable");
            if matches!(count, 1 | 10 | 20) {
                assert_eq!(
                    message,
                    Some(format!(
                        "memphant-worker: tick error (consecutive={count}, delay={:?}): database unavailable",
                        state.delay()
                    ))
                );
            } else {
                assert_eq!(message, None, "count={count}");
            }
        }
        assert_eq!(state.delay(), Duration::from_secs(30));
        assert_eq!(state.record_success(), 20);
        assert_eq!(state.record_success(), 0);
        assert_eq!(state.delay(), Duration::from_millis(500));

        assert!(state.record_error(&"database unavailable").is_some());
        assert_eq!(state.delay(), Duration::from_secs(1));
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
