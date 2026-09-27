//! The reflect worker: claims queued jobs (SKIP LOCKED in Postgres) and
//! compiles them through the same `MemoryService` path the public reflect
//! verb uses. `MEMPHANT_WORKER_ONCE=1` runs one tick; `MEMPHANT_WORKER_DRAIN=1`
//! runs ticks to empty. Both exit deterministically.

use std::time::Duration;

const DEFAULT_BATCH: usize = 64;
const MAX_BATCH: usize = 1024;
const TICK: Duration = Duration::from_millis(500);
/// Ceiling for the daemon error-loop backoff.
const BACKOFF_CAP: Duration = Duration::from_secs(30);
/// 500 ms << 6 = 32 s already exceeds `BACKOFF_CAP`, so larger shifts cannot
/// change the result; clamping the shift keeps the arithmetic in-bounds for
/// any `u32` consecutive-error count.
const BACKOFF_MAX_SHIFT: u32 = 6;
/// Log the first consecutive tick error, then only every 10th one after it.
const ERROR_LOG_STRIDE: u32 = 10;

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

/// Parses a boolean worker mode flag (`MEMPHANT_WORKER_ONCE` / `DRAIN`)
/// strictly: unset or empty is `false`; the accepted spellings are
/// 1/true/yes/on and 0/false/no/off, matched case-insensitively after
/// trimming. Anything else is an error naming the variable and the value —
/// a typo must not silently select Daemon mode.
fn parse_env_flag(name: &str, value: Option<&str>) -> Result<bool, String> {
    let Some(value) = value else {
        return Ok(false);
    };
    match value.trim().to_ascii_lowercase().as_str() {
        "" => Ok(false),
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(format!(
            "{name} must be one of 1/0/true/false/yes/no/on/off, got {value:?}"
        )),
    }
}

/// Delay before the next daemon tick after `consecutive_errors` failed
/// ticks: `min(TICK * 2^N, 30s)`.
fn backoff_delay(consecutive_errors: u32) -> Duration {
    let shifted = TICK.as_millis() << consecutive_errors.min(BACKOFF_MAX_SHIFT);
    Duration::from_millis(shifted as u64).min(BACKOFF_CAP)
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
        // The delay is selected over, not awaited directly, so SIGTERM and
        // ctrl_c interrupt a backoff sleep immediately.
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
                        consecutive_errors = consecutive_errors.saturating_add(1);
                        if consecutive_errors == 1 || consecutive_errors.is_multiple_of(ERROR_LOG_STRIDE) {
                            eprintln!(
                                "memphant-worker: tick error (consecutive={consecutive_errors}, next tick in {:?}): {error}",
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
    use super::{
        TICK, WorkerMode, backoff_delay, drain_finished, parse_env_flag, worker_batch_from_value,
        worker_mode,
    };
    use std::time::Duration;

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
    fn parse_env_flag_accepts_unset_and_empty_as_false() {
        assert_eq!(parse_env_flag("MEMPHANT_WORKER_ONCE", None), Ok(false));
        assert_eq!(parse_env_flag("MEMPHANT_WORKER_DRAIN", None), Ok(false));
        assert_eq!(parse_env_flag("MEMPHANT_WORKER_ONCE", Some("")), Ok(false));
        assert_eq!(
            parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(" ")),
            Ok(false)
        );
    }

    #[test]
    fn parse_env_flag_accepts_true_spellings_case_and_whitespace_insensitive() {
        for value in [
            "1", "true", "TRUE", "True", "yes", "YES", "on", "ON", " 1 ", "\tOn\n", " yes ",
        ] {
            assert_eq!(
                parse_env_flag("MEMPHANT_WORKER_ONCE", Some(value)),
                Ok(true),
                "value {value:?} must parse as true"
            );
        }
    }

    #[test]
    fn parse_env_flag_accepts_false_spellings_case_and_whitespace_insensitive() {
        for value in [
            "0", "false", "FALSE", "False", "no", "NO", "off", "OFF", " 0 ", "\tOFF\n", " no ",
        ] {
            assert_eq!(
                parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(value)),
                Ok(false),
                "value {value:?} must parse as false"
            );
        }
    }

    #[test]
    fn parse_env_flag_rejects_anything_else_naming_variable_and_value() {
        for value in ["maybe", "2", "ye", "1.0", "true-ish", "  enable  ", "壹"] {
            let error = parse_env_flag("MEMPHANT_WORKER_DRAIN", Some(value))
                .expect_err("rejected spelling must be an Err");
            assert!(
                error.contains("MEMPHANT_WORKER_DRAIN"),
                "error must name the variable: {error}"
            );
            assert!(
                error.contains("must be one of 1/0/true/false/yes/no/on/off"),
                "error must name the accepted spellings: {error}"
            );
            assert!(
                error.contains(value.trim()),
                "error must echo the value: {error}"
            );
        }
    }

    #[test]
    fn backoff_delay_doubles_then_caps_at_thirty_seconds() {
        assert_eq!(backoff_delay(0), Duration::from_millis(500));
        assert_eq!(backoff_delay(1), Duration::from_secs(1));
        assert_eq!(backoff_delay(2), Duration::from_secs(2));
        assert_eq!(backoff_delay(5), Duration::from_secs(16));
        for n in [16, 32, 100, u32::MAX] {
            assert_eq!(backoff_delay(n), Duration::from_secs(30), "n = {n}");
        }
    }

    #[test]
    fn backoff_delay_matches_tick_at_zero_errors() {
        assert_eq!(backoff_delay(0), TICK);
    }
}
