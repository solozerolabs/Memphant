use std::io::Write;
use std::process::Command;

use serde_json::Value;
use sha2::Digest;

#[test]
fn structured_state_census_deduplicates_exact_production_plans() {
    let mut input = tempfile::NamedTempFile::new().unwrap();
    writeln!(
        input,
        "{{\"source_body\":\"Launch code: ORCHID-17.\",\"uses\":2}}"
    )
    .unwrap();
    let mut prompt = tempfile::NamedTempFile::new().unwrap();
    write!(prompt, "Extract grounded state.").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .args([
            "structured-state",
            "census",
            "--input-jsonl",
            input.path().to_str().unwrap(),
            "--model",
            "qwen/qwen3.5-9b-20260310",
            "--prompt-file",
            prompt.path().to_str().unwrap(),
            "--input-price-nanos-per-million",
            "100000000",
            "--output-price-nanos-per-million",
            "150000000",
        ])
        .env_clear()
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let census: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(census["resource_uses"], 2);
    assert_eq!(census["unique_source_bodies"], 1);
    assert_eq!(census["planned_requests"], 2);
    assert_eq!(census["unique_extraction_keys"], 1);
    assert_eq!(census["maximum_request_bytes"], 2_244);
    assert_eq!(census["maximum_per_attempt_reservation_nanos"], 838_800);
    assert_eq!(census["first_attempt_liability_nanos"], 838_800);
    assert_eq!(census["full_three_wave_liability_nanos"], 2_516_400);
    assert_eq!(census["construction_liability_nanos"], 2_516_400);
    assert_eq!(census["maximum_attempts"], 3);
    let plans = census["plan_inventory"].as_array().unwrap();
    assert_eq!(plans.len(), 1);
    assert_eq!(plans[0]["requested_model"], "qwen/qwen3.5-9b-20260310");
    assert_eq!(plans[0]["maximum_attempts"], 3);
    assert_eq!(plans[0]["per_attempt_reservation_nanos"], 838_800);
    assert_eq!(plans[0]["source_kind"], "resource");
    assert_eq!(plans[0]["batch_index"], 0);
    assert!(plans[0]["source_body_sha256"].as_str().unwrap().len() == 64);
    assert!(plans[0]["evidence_slices_sha256"].as_str().unwrap().len() == 64);
    assert_eq!(
        census["plan_inventory_sha256"],
        format!(
            "{:x}",
            sha2::Sha256::digest(serde_json::to_vec(plans).unwrap())
        )
    );
}

#[test]
fn structured_state_execute_rejects_plan_drift_before_credentials() {
    let mut input = tempfile::NamedTempFile::new().unwrap();
    writeln!(
        input,
        "{{\"source_body\":\"Launch code: ORCHID-17.\",\"uses\":1}}"
    )
    .unwrap();
    let mut prompt = tempfile::NamedTempFile::new().unwrap();
    write!(prompt, "Extract grounded state.").unwrap();
    let census = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .args([
            "structured-state",
            "census",
            "--input-jsonl",
            input.path().to_str().unwrap(),
            "--model",
            "qwen/qwen3.5-9b-20260310",
            "--prompt-file",
            prompt.path().to_str().unwrap(),
            "--input-price-nanos-per-million",
            "100000000",
            "--output-price-nanos-per-million",
            "150000000",
        ])
        .env_clear()
        .output()
        .unwrap();
    let census: Value = serde_json::from_slice(&census.stdout).unwrap();
    let mut plans = census["plan_inventory"].clone();
    plans[0]["request_sha256"] = Value::String("0".repeat(64));
    let plans_file = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(plans_file.path(), serde_json::to_vec(&plans).unwrap()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .args([
            "structured-state",
            "execute",
            "--input-jsonl",
            input.path().to_str().unwrap(),
            "--allowed-plans-json",
            plans_file.path().to_str().unwrap(),
            "--max-workers",
            "32",
        ])
        .env_clear()
        .env(
            "MEMPHANT_STRUCTURED_STATE_MODEL",
            "qwen/qwen3.5-9b-20260310",
        )
        .env("MEMPHANT_STRUCTURED_STATE_PROMPT_PATH", prompt.path())
        .env(
            "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION",
            "100000000",
        )
        .env(
            "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION",
            "150000000",
        )
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("differs from frozen authority"), "{stderr}");
    assert!(!stderr.contains("OPENROUTER_API_KEY"));
}
