use std::io::Write;
use std::process::Command;

use serde_json::Value;

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
    assert_eq!(census["maximum_request_bytes"], 1_645);
    assert_eq!(census["maximum_per_attempt_reservation_nanos"], 778_900);
    assert_eq!(census["first_attempt_liability_nanos"], 778_900);
    assert_eq!(census["full_three_wave_liability_nanos"], 2_336_700);
    assert_eq!(census["construction_liability_nanos"], 2_336_700);
    assert_eq!(census["maximum_attempts"], 3);
}
