//! Worker binary env-flag parsing tests: `MEMPHANT_WORKER_ONCE` and
//! `MEMPHANT_WORKER_DRAIN` go through the strict truthy/falsy table and
//! must reject anything else BEFORE store construction, so these run the
//! compiled binary as a subprocess with no database configured at all.

use std::process::{Command, Output};

fn run_worker_with(envs: &[(&str, &str)]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_memphant-worker"));
    command
        .env_remove("MEMPHANT_WORKER_DATABASE_URL")
        .env_remove("DATABASE_URL")
        // Keep the flag-parsing assertions hermetic: an inherited batch-size
        // variable would make the binary panic on batch parsing first.
        .env_remove("MEMPHANT_WORKER_BATCH_SIZE");
    for (name, value) in envs {
        command.env(name, value);
    }
    command.output().expect("memphant-worker binary runs")
}

#[test]
fn worker_once_rejects_unknown_flag_value_before_store_construction() {
    let output = run_worker_with(&[("MEMPHANT_WORKER_ONCE", "maybe")]);

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("memphant-worker: MEMPHANT_WORKER_ONCE"),
        "stderr: {stderr}"
    );
    assert!(
        stderr.contains("must be one of 1/0/true/false/yes/no/on/off"),
        "stderr: {stderr}"
    );
    assert!(stderr.contains("got \"maybe\""), "stderr: {stderr}");
    assert!(
        !stderr.contains("store="),
        "bad flag value must fail before store construction: {stderr}"
    );
}

#[test]
fn worker_drain_rejects_unknown_flag_value_before_store_construction() {
    let output = run_worker_with(&[("MEMPHANT_WORKER_DRAIN", "maybe")]);

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("memphant-worker: MEMPHANT_WORKER_DRAIN"),
        "stderr: {stderr}"
    );
    assert!(
        stderr.contains("must be one of 1/0/true/false/yes/no/on/off"),
        "stderr: {stderr}"
    );
    assert!(
        !stderr.contains("store="),
        "bad flag value must fail before store construction: {stderr}"
    );
}

#[test]
fn worker_truthy_spelling_parses_as_true_not_daemon() {
    // Before strict parsing, ONCE="true" silently selected Daemon mode
    // (which never exits). It must now parse as true, so ONCE=true +
    // DRAIN=1 trips the mutual-exclusion guard instead of a daemon.
    let output = run_worker_with(&[
        ("MEMPHANT_WORKER_ONCE", "true"),
        ("MEMPHANT_WORKER_DRAIN", "1"),
    ]);

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("mutually exclusive"), "stderr: {stderr}");
    assert!(
        !stderr.contains("store="),
        "mode conflict must fail before store construction: {stderr}"
    );
}
