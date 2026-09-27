use std::process::Command;

#[test]
fn worker_rejects_invalid_mode_flags_before_store_construction() {
    for name in ["MEMPHANT_WORKER_ONCE", "MEMPHANT_WORKER_DRAIN"] {
        let output = Command::new(env!("CARGO_BIN_EXE_memphant-worker"))
            .env_remove("MEMPHANT_WORKER_DATABASE_URL")
            .env_remove("DATABASE_URL")
            .env_remove("MEMPHANT_WORKER_ONCE")
            .env_remove("MEMPHANT_WORKER_DRAIN")
            .env(name, " maybe ")
            .output()
            .expect("memphant-worker binary runs");

        assert!(!output.status.success(), "{name} must reject invalid input");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(&format!(
                "memphant-worker: {name} must be one of 1/0/true/false/yes/no/on/off, got \"maybe\""
            )),
            "stderr: {stderr}"
        );
        assert!(
            !stderr.contains("store="),
            "invalid {name} must fail before store construction: {stderr}"
        );
    }
}
