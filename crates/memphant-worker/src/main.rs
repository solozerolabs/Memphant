//! The reflect worker: claims queued jobs (SKIP LOCKED in Postgres) and
//! compiles them through the same `MemoryService` path the public reflect
//! verb uses. `MEMPHANT_WORKER_ONCE` runs one tick and `MEMPHANT_WORKER_DRAIN`
//! runs ticks to empty (both accept the strict spellings 1/0/true/false/
//! yes/no/on/off, case-insensitive). Both exit deterministically.

use std::time::Duration;

const DEFAULT_BATCH: usize = 64;
const MAX_BATCH: usize = 1024;
const TICK: Duration = Duration::from_millis(500);

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
    let trimmed = value.unwrap_or_default().trim();
    match trimmed.to_ascii_lowercase().as_str() {
        "" => Ok(false),
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(format!(
            "{name} must be one of 1/0/true/false/yes/no/on/off, got {trimmed:?}"
        )),
    }
}

/// Delay before the next daemon tick after `consecutive_errors` failed ticks
/// in a row: `TICK * 2^N`, capped at 30 seconds.
fn backoff_delay(consecutive_errors: u32) -> Duration {
    const BACKOFF_CAP_MS: u64 = 30_000;
    // 500 ms << 6 = 32 s already exceeds the cap, so larger shifts are
    // redundant; capping the shift also makes the math overflow-proof.
    let shift = consecutive_errors.min(6);
    Duration::from_millis(((TICK.as_millis() as u64) << shift).min(BACKOFF_CAP_MS))
}

/// Throttle for daemon tick-error logging: the first error, then every
/// 10th consecutive error.
fn should_log_tick_error(consecutive_errors: u32) -> bool {
    consecutive_errors == 1 || consecutive_errors.is_multiple_of(10)
}

#[tokio::main]
async fn main() {
    let batch =
        worker_batch_from_value(std::env::var("MEMPHANT_WORKER_BATCH_SIZE").ok().as_deref())
            .unwrap_or_else(|error| panic!("memphant-worker: MEMPHANT_WORKER_BATCH_SIZE: {error}"));
    let mode = worker_mode(
        parse_env_flag(
            "MEMPHANT_WORKER_ONCE",
            std::env::var("MEMPHANT_WORKER_ONCE").ok().as_deref(),
        )
        .unwrap_or_else(|error| panic!("memphant-worker: {error}")),
        parse_env_flag(
            "MEMPHANT_WORKER_DRAIN",
            std::env::var("MEMPHANT_WORKER_DRAIN").ok().as_deref(),
        )
        .unwrap_or_else(|error| panic!("memphant-worker: {error}")),
    )
    .unwrap_or_else(|error| panic!("memphant-worker: {error}"));
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
    let mut consecutive_errors: u32 = 0;
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
            _ = tokio::time::sleep(backoff_delay(consecutive_errors)) => {
                match service.run_worker_tick(batch).await {
                    Ok(tick) => {
                        if consecutive_errors > 0 {
                            eprintln!(
                                "memphant-worker: recovered after {consecutive_errors} failed ticks"
                            );
                            consecutive_errors = 0;
                        }
                        if !tick.is_idle() {
                            eprintln!(
                                "memphant-worker: completed={} failed={} retried={} deferred={}",
                                tick.completed, tick.failed, tick.retried, tick.deferred
                            );
                        }
                    }
                    Err(error) => {
                        consecutive_errors += 1;
                        if should_log_tick_error(consecutive_errors) {
                            eprintln!(
                                "memphant-worker: tick error: {error} ({consecutive_errors} consecutive, next tick in {:?})",
                                backoff_delay(consecutive_errors)
                            );
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
        WorkerMode, backoff_delay, drain_finished, parse_env_flag, should_log_tick_error,
        worker_batch_from_value, worker_mode,
    };

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

    #[test]
    fn parse_env_flag_accepts_truthy_and_falsy_spellings() {
        // Unset and blank values are false, never an error.
        assert_eq!(parse_env_flag("MEMPHANT_WORKER_ONCE", None), Ok(false));
        assert_eq!(parse_env_flag("MEMPHANT_WORKER_ONCE", Some("")), Ok(false));
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_ONCE", Some("   ")),
            Ok(false)
        );
        for value in ["1", "true", "yes", "on"] {
            assert_eq!(
                parse_env_flag("MEMPHANT_WORKER_ONCE", Some(value)),
                Ok(true),
                "{value:?} must parse as true"
            );
        }
        for value in ["0", "false", "no", "off"] {
            assert_eq!(
                parse_env_flag("MEMPHANT_WORKER_ONCE", Some(value)),
                Ok(false),
                "{value:?} must parse as false"
            );
        }
    }

    #[test]
    fn parse_env_flag_trims_and_matches_case_insensitively() {
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_ONCE", Some(" 1 ")),
            Ok(true)
        );
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_ONCE", Some("\ttrue\n")),
            Ok(true)
        );
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(" YES ")),
            Ok(true)
        );
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_DRAIN", Some("On")),
            Ok(true)
        );
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_ONCE", Some(" FALSE ")),
            Ok(false)
        );
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_ONCE", Some("No")),
            Ok(false)
        );
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_ONCE", Some(" off\t")),
            Ok(false)
        );
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_ONCE", Some("OFF")),
            Ok(false)
        );
    }

    #[test]
    fn parse_env_flag_rejects_unknown_values_naming_variable_and_value() {
        for value in ["maybe", "2", "y", "01", "enabled"] {
            let error = parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(value)).unwrap_err();
            assert!(
                error.contains("MEMPHANT_WORKER_DRAIN"),
                "{value:?}: {error}"
            );
            assert!(error.contains(value), "{value:?}: {error}");
            assert!(
                error.contains("must be one of 1/0/true/false/yes/no/on/off"),
                "{value:?}: {error}"
            );
        }
        // The reported value is the trimmed spelling.
        let error = parse_env_flag("MEMPHANT_WORKER_ONCE", Some(" maybe ")).unwrap_err();
        assert!(error.contains("got \"maybe\""), "{error}");
    }

    #[test]
    fn backoff_delay_doubles_with_each_error_and_caps_at_thirty_seconds() {
        assert_eq!(backoff_delay(0), Duration::from_millis(500));
        assert_eq!(backoff_delay(1), Duration::from_secs(1));
        assert_eq!(backoff_delay(2), Duration::from_secs(2));
        assert_eq!(backoff_delay(5), Duration::from_secs(16));
        assert_eq!(backoff_delay(6), Duration::from_secs(30));
        assert_eq!(backoff_delay(7), Duration::from_secs(30));
        assert_eq!(backoff_delay(100), Duration::from_secs(30));
        assert_eq!(backoff_delay(u32::MAX), Duration::from_secs(30));
    }

    #[test]
    fn tick_error_logging_logs_first_and_then_every_tenth_error() {
        assert!(should_log_tick_error(1));
        for consecutive in [2u32, 3, 4, 5, 6, 7, 8, 9, 11, 12, 19, 21] {
            assert!(!should_log_tick_error(consecutive), "{consecutive}");
        }
        assert!(should_log_tick_error(10));
        assert!(should_log_tick_error(20));
    }
}
