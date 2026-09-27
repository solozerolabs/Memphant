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

fn backoff_delay(consecutive_errors: u32) -> Duration {
    let multiplier = 1_u32.checked_shl(consecutive_errors).unwrap_or(u32::MAX);
    TICK.saturating_mul(multiplier).min(MAX_BACKOFF)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DaemonLogAction {
    None,
    Error {
        consecutive_errors: u32,
        delay: Duration,
    },
    Recovered {
        failed_ticks: u32,
    },
}

fn daemon_tick_transition(consecutive_errors: u32, succeeded: bool) -> (u32, DaemonLogAction) {
    if succeeded {
        let action = if consecutive_errors == 0 {
            DaemonLogAction::None
        } else {
            DaemonLogAction::Recovered {
                failed_ticks: consecutive_errors,
            }
        };
        return (0, action);
    }

    let consecutive_errors = consecutive_errors.saturating_add(1);
    let action = if consecutive_errors == 1 || consecutive_errors.is_multiple_of(10) {
        DaemonLogAction::Error {
            consecutive_errors,
            delay: backoff_delay(consecutive_errors),
        }
    } else {
        DaemonLogAction::None
    };
    (consecutive_errors, action)
}

fn daemon_log_message(action: DaemonLogAction, error: Option<&str>) -> Option<String> {
    match action {
        DaemonLogAction::None => None,
        DaemonLogAction::Error {
            consecutive_errors,
            delay,
        } => Some(format!(
            "memphant-worker: tick error: {} (consecutive errors: {consecutive_errors}, delay: {delay:?})",
            error.unwrap_or("unknown error")
        )),
        DaemonLogAction::Recovered { failed_ticks } => Some(format!(
            "memphant-worker: recovered after {failed_ticks} failed ticks"
        )),
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
    let mut consecutive_errors = 0;
    loop {
        let delay = backoff_delay(consecutive_errors);
        tokio::select! {
            _ = sigterm.recv() => {
                eprintln!("memphant-worker: SIGTERM — draining and shutting down");
                break;
            }
            _ = tokio::signal::ctrl_c() => {
                eprintln!("memphant-worker: interrupt — shutting down");
                break;
            }
            _ = tokio::time::sleep(delay) => {
                match service.run_worker_tick(batch).await {
                    Ok(tick) => {
                        let (next_errors, action) =
                            daemon_tick_transition(consecutive_errors, true);
                        consecutive_errors = next_errors;
                        if let Some(message) = daemon_log_message(action, None) {
                            eprintln!("{message}");
                        }
                        if !tick.is_idle() {
                            eprintln!(
                                "memphant-worker: completed={} failed={} retried={} deferred={}",
                                tick.completed, tick.failed, tick.retried, tick.deferred
                            );
                        }
                    }
                    Err(error) => {
                        let (next_errors, action) =
                            daemon_tick_transition(consecutive_errors, false);
                        consecutive_errors = next_errors;
                        let error = error.to_string();
                        if let Some(message) = daemon_log_message(action, Some(&error)) {
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
        DaemonLogAction, WorkerMode, backoff_delay as calculate_backoff_delay,
        daemon_log_message as format_daemon_log_message,
        daemon_tick_transition as apply_daemon_tick_transition, drain_finished,
        parse_env_flag as parse_flag, worker_batch_from_value, worker_mode,
    };

    #[test]
    fn parse_env_flag_accepts_supported_spellings() {
        for value in ["1", "true", "yes", "on", " TRUE ", "YeS", "\ton\n"] {
            assert_eq!(parse_flag("FLAG", Some(value)), Ok(true), "{value:?}");
        }
        for value in ["0", "false", "no", "off", " FALSE ", "nO", "\toFf\n"] {
            assert_eq!(parse_flag("FLAG", Some(value)), Ok(false), "{value:?}");
        }
        assert_eq!(parse_flag("FLAG", None), Ok(false));
        assert_eq!(parse_flag("FLAG", Some("")), Ok(false));
        assert_eq!(parse_flag("FLAG", Some(" \t\n")), Ok(false));
    }

    #[test]
    fn parse_env_flag_rejects_unsupported_spellings() {
        for value in ["maybe", "2", "-1", "truthy", "1.0"] {
            assert_eq!(
                parse_flag("MEMPHANT_WORKER_DRAIN", Some(value)),
                Err(format!(
                    "MEMPHANT_WORKER_DRAIN must be one of 1/0/true/false/yes/no/on/off, got {value:?}"
                ))
            );
        }
        assert_eq!(
            parse_flag("MEMPHANT_WORKER_ONCE", Some("  MAYBE  ")),
            Err(
                "MEMPHANT_WORKER_ONCE must be one of 1/0/true/false/yes/no/on/off, got \"MAYBE\""
                    .to_owned()
            )
        );
    }

    #[test]
    fn backoff_delay_is_exponential_and_capped() {
        for (errors, expected) in [
            (0, Duration::from_millis(500)),
            (1, Duration::from_secs(1)),
            (2, Duration::from_secs(2)),
            (5, Duration::from_secs(16)),
            (6, Duration::from_secs(30)),
            (7, Duration::from_secs(30)),
            (u32::MAX, Duration::from_secs(30)),
        ] {
            assert_eq!(calculate_backoff_delay(errors), expected, "errors={errors}");
        }
    }

    #[test]
    fn daemon_tick_transition_tracks_logging_and_recovery() {
        let (errors, action) = apply_daemon_tick_transition(0, false);
        assert_eq!(errors, 1);
        assert_eq!(
            action,
            DaemonLogAction::Error {
                consecutive_errors: 1,
                delay: Duration::from_secs(1),
            }
        );

        let (errors, action) = apply_daemon_tick_transition(errors, false);
        assert_eq!((errors, action), (2, DaemonLogAction::None));

        let (errors, action) = apply_daemon_tick_transition(9, false);
        assert_eq!(errors, 10);
        assert_eq!(
            action,
            DaemonLogAction::Error {
                consecutive_errors: 10,
                delay: Duration::from_secs(30),
            }
        );

        let (errors, action) = apply_daemon_tick_transition(19, false);
        assert_eq!(errors, 20);
        assert_eq!(
            action,
            DaemonLogAction::Error {
                consecutive_errors: 20,
                delay: Duration::from_secs(30),
            }
        );

        let (errors, action) = apply_daemon_tick_transition(u32::MAX, false);
        assert_eq!(errors, u32::MAX);
        assert_eq!(action, DaemonLogAction::None);

        let (errors, action) = apply_daemon_tick_transition(20, true);
        assert_eq!(errors, 0);
        assert_eq!(action, DaemonLogAction::Recovered { failed_ticks: 20 });
        assert_eq!(calculate_backoff_delay(errors), Duration::from_millis(500));
        assert_eq!(
            apply_daemon_tick_transition(errors, true),
            (0, DaemonLogAction::None)
        );
    }

    #[test]
    fn daemon_log_message_includes_error_count_delay_and_recovery() {
        assert_eq!(
            format_daemon_log_message(
                DaemonLogAction::Error {
                    consecutive_errors: 10,
                    delay: Duration::from_secs(30),
                },
                Some("database unavailable")
            ),
            Some(
                "memphant-worker: tick error: database unavailable (consecutive errors: 10, delay: 30s)"
                    .to_owned()
            )
        );
        assert_eq!(
            format_daemon_log_message(DaemonLogAction::Recovered { failed_ticks: 10 }, None),
            Some("memphant-worker: recovered after 10 failed ticks".to_owned())
        );
        assert_eq!(format_daemon_log_message(DaemonLogAction::None, None), None);
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
