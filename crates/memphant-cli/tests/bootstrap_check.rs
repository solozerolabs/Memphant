use std::fs;
use std::path::Path;
use std::process::Command;

fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("workspace root")
}

#[test]
fn default_provider_profiles_pass_bootstrap_check() {
    for provider in ["plain-postgres", "supabase", "neon"] {
        let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
            .current_dir(repo_root())
            .args(["db", "bootstrap-check", "--provider", provider])
            .output()
            .expect("run bootstrap check");

        assert!(
            output.status.success(),
            "provider {provider} failed\nstdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn bootstrap_check_rejects_region_mismatch() {
    let dir = tempfile::tempdir().expect("tempdir");
    let profile = dir.path().join("plain.env");
    fs::write(
        &profile,
        r#"
MEMPHANT_PROVIDER=plain-postgres
DATABASE_URL=postgresql://memphant:secret@db.example.com:5432/memphant?sslmode=require
MEMPHANT_SCHEMA=memphant
MEMPHANT_PG_REGION=us-east-1
MEMPHANT_OBJECT_STORE=s3
MEMPHANT_OBJECT_STORE_BUCKET=customer-memphant-prod
MEMPHANT_OBJECT_STORE_REGION=eu-west-1
MEMPHANT_OBJECT_VERSIONING_REQUIRED=true
MEMPHANT_PITR_WINDOW_DAYS=7
MEMPHANT_OBJECT_RETENTION_DAYS=14
"#,
    )
    .expect("write profile");

    let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .current_dir(repo_root())
        .args([
            "db",
            "bootstrap-check",
            "--provider",
            "plain-postgres",
            "--profile",
            profile.to_str().expect("profile path"),
        ])
        .output()
        .expect("run bootstrap check");

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("residency:region_mismatch"),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

/// W3.3: a served credential that bypasses RLS must never reach a maintenance
/// window. The Neon profile shipped exactly this mistake — `memphant_owner`,
/// which holds the `memphant_*_owner` `using(true)` policies.
#[test]
fn bootstrap_check_rejects_a_served_credential_that_bypasses_rls() {
    let dir = tempfile::tempdir().expect("tempdir");
    let profile = dir.path().join("neon.env");
    let poisoned =
        fs::read_to_string(repo_root().join("deploy/provider-profiles/neon.env.example"))
            .expect("read default profile")
            .replace(
                "MEMPHANT_APP_DATABASE_URL=postgresql://memphant_app_login:",
                "MEMPHANT_APP_DATABASE_URL=postgresql://memphant_owner:",
            );
    fs::write(&profile, poisoned).expect("write profile");

    let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .current_dir(repo_root())
        .args([
            "db",
            "bootstrap-check",
            "--provider",
            "neon",
            "--profile",
            profile.to_str().expect("profile path"),
        ])
        .output()
        .expect("run bootstrap check");

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!output.status.success(), "stderr:\n{stderr}");
    assert!(
        stderr.contains("MEMPHANT_APP_DATABASE_URL:rls_bypassing_login:memphant_owner"),
        "stderr:\n{stderr}"
    );
    assert!(
        stderr.contains("MEMPHANT_APP_DATABASE_URL:reuses_migrator_credential:memphant_owner"),
        "stderr:\n{stderr}"
    );
}

#[test]
fn bootstrap_check_rejects_supabase_memphant_schema_exposure() {
    let dir = tempfile::tempdir().expect("tempdir");
    let profile = dir.path().join("supabase.env");
    fs::write(
        &profile,
        r#"
MEMPHANT_PROVIDER=supabase
DATABASE_URL=postgresql://postgres.example:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require
MEMPHANT_SCHEMA=memphant
MEMPHANT_PG_REGION=us-east-1
MEMPHANT_OBJECT_STORE=s3
MEMPHANT_OBJECT_STORE_BUCKET=customer-memphant-prod
MEMPHANT_OBJECT_STORE_REGION=us-east-1
MEMPHANT_OBJECT_VERSIONING_REQUIRED=true
MEMPHANT_PITR_WINDOW_DAYS=7
MEMPHANT_OBJECT_RETENTION_DAYS=14
MEMPHANT_SUPABASE_EXPOSED_SCHEMAS=public,memphant
MEMPHANT_SUPABASE_ANON_HAS_MEMPHANT_ACCESS=false
MEMPHANT_SUPABASE_AUTHENTICATED_HAS_MEMPHANT_ACCESS=false
MEMPHANT_SUPABASE_ADVISORS_REQUIRED=true
MEMPHANT_SUPABASE_LINT_COMMAND="supabase db lint --db-url $DATABASE_URL --schema memphant --fail-on warning"
"#,
    )
    .expect("write profile");

    let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .current_dir(repo_root())
        .args([
            "db",
            "bootstrap-check",
            "--provider",
            "supabase",
            "--profile",
            profile.to_str().expect("profile path"),
        ])
        .output()
        .expect("run bootstrap check");

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("supabase:memphant_schema_exposed_to_postgrest"),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn bootstrap_check_rejects_supabase_transaction_pooler_for_persistent_runtime() {
    let dir = tempfile::tempdir().expect("tempdir");
    let profile = dir.path().join("supabase.env");
    let default_profile =
        fs::read_to_string(repo_root().join("deploy/provider-profiles/supabase.env.example"))
            .expect("read default profile")
            .replace(":5432/", ":6543/");
    fs::write(&profile, default_profile).expect("write profile");

    let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .current_dir(repo_root())
        .args([
            "db",
            "bootstrap-check",
            "--provider",
            "supabase",
            "--profile",
            profile.to_str().expect("profile path"),
        ])
        .output()
        .expect("run bootstrap check");

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("supabase:database_url_transaction_pooler_forbidden"),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
}
