use std::{
    ffi::OsString,
    os::unix::ffi::OsStringExt,
    process::{Command, Output},
};

fn run_worker(once: Option<&str>, drain: Option<&str>) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_memphant-worker"));
    command
        .env_remove("MEMPHANT_WORKER_ONCE")
        .env_remove("MEMPHANT_WORKER_DRAIN")
        .env_remove("MEMPHANT_WORKER_DATABASE_URL")
        .env_remove("DATABASE_URL");
    if let Some(value) = once {
        command.env("MEMPHANT_WORKER_ONCE", value);
    }
    if let Some(value) = drain {
        command.env("MEMPHANT_WORKER_DRAIN", value);
    }
    command.output().expect("memphant-worker binary runs")
}

fn assert_pre_store_failure(output: &Output, expected: &str) {
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains(expected), "stderr: {stderr}");
    assert!(
        !stderr.contains("store="),
        "configuration must fail before store construction: {stderr}"
    );
}

#[test]
fn invalid_once_flag_fails_with_context_before_store_construction() {
    let output = run_worker(Some(" maybe "), None);
    assert_pre_store_failure(
        &output,
        "memphant-worker: MEMPHANT_WORKER_ONCE must be one of 1/0/true/false/yes/no/on/off, got \"maybe\"",
    );
}

#[test]
fn non_unicode_once_flag_fails_before_store_construction() {
    let mut command = Command::new(env!("CARGO_BIN_EXE_memphant-worker"));
    let output = command
        .env_remove("MEMPHANT_WORKER_ONCE")
        .env_remove("MEMPHANT_WORKER_DRAIN")
        .env_remove("MEMPHANT_WORKER_DATABASE_URL")
        .env_remove("DATABASE_URL")
        .env("MEMPHANT_WORKER_ONCE", OsString::from_vec(vec![0xff]))
        .output()
        .expect("memphant-worker binary runs");
    assert_pre_store_failure(
        &output,
        "memphant-worker: MEMPHANT_WORKER_ONCE must be valid Unicode",
    );
}

#[test]
fn invalid_drain_flag_fails_with_context_before_store_construction() {
    let output = run_worker(None, Some("sometimes"));
    assert_pre_store_failure(
        &output,
        "memphant-worker: MEMPHANT_WORKER_DRAIN must be one of 1/0/true/false/yes/no/on/off, got \"sometimes\"",
    );
}

#[test]
fn accepted_aliases_reach_mode_conflict() {
    let output = run_worker(Some(" YeS "), Some("\tON\n"));
    assert_pre_store_failure(
        &output,
        "MEMPHANT_WORKER_ONCE and MEMPHANT_WORKER_DRAIN are mutually exclusive",
    );
}
