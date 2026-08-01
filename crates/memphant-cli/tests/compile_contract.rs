use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use memphant_core::MemoryStore;
use memphant_server::AppState;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingResponse,
    ContextBindingScopeRef, MemoryKind, NewMemoryUnit, TenantId, TrustLevel, UnitState,
};
use sha2::{Digest, Sha256};

const TENANT: &str = "00000000-0000-0000-0000-00000000b203";

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_is_a_deterministic_server_backed_projection_and_verify_is_complete() {
    let (url, binding, state) = spawn_server().await;
    let current_id = seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "decision:queue",
        "Use LISTEN/NOTIFY.",
    )
    .await;
    let historical_id = seed_unit(
        &state,
        &binding,
        UnitState::Superseded,
        "decision:old-queue",
        "Poll every second.",
    )
    .await;
    let out = tempfile::tempdir().unwrap();

    let compiled = compile(&url, &binding, out.path(), &[]);
    assert_success(&compiled);
    assert!(String::from_utf8_lossy(&compiled.stdout).contains("compile=written"));

    let names = root_names(out.path());
    assert_eq!(
        names,
        ["MEMORY.md", "inbox", "memphant-export.json", "units"]
    );
    let unit_path = out
        .path()
        .join("units")
        .join(format!("{}.md", current_id.as_uuid()));
    assert!(unit_path.is_file());
    assert!(
        !out.path()
            .join("units")
            .join(format!("{}.md", historical_id.as_uuid()))
            .exists()
    );

    let unit = fs::read_to_string(&unit_path).unwrap();
    assert!(unit.starts_with("# decision:queue\n\nUse LISTEN/NOTIFY.\n\n"));
    let footer = unit
        .lines()
        .last()
        .unwrap()
        .strip_prefix("<!-- memphant ")
        .unwrap()
        .strip_suffix(" -->")
        .unwrap();
    let footer: serde_json::Value = serde_json::from_str(footer).unwrap();
    assert_eq!(footer["unit_id"], current_id.as_uuid().to_string());
    assert_eq!(footer["kind"], "semantic");
    assert_eq!(footer["fact_key"], "decision:queue");
    assert_eq!(footer["predicate"], "states");
    assert_eq!(footer["subject_generation"], 0);
    assert_eq!(footer["body_sha256"].as_str().unwrap().len(), 64);

    let manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(out.path().join("memphant-export.json")).unwrap())
            .unwrap();
    assert_eq!(manifest["schema_version"], 2);
    assert_eq!(manifest["snapshot_sha256"].as_str().unwrap().len(), 64);
    assert_eq!(manifest["memory_sha256"].as_str().unwrap().len(), 64);
    assert_eq!(manifest["entries"].as_array().unwrap().len(), 1);
    assert_eq!(
        manifest["entries"][0]["path"],
        format!("units/{}.md", current_id.as_uuid())
    );

    let verified = verify(out.path());
    assert_success(&verified);
    assert!(String::from_utf8_lossy(&verified.stdout).contains("export=clean"));

    let first = tree_bytes(out.path());
    let repeated = compile(&url, &binding, out.path(), &[]);
    assert_success(&repeated);
    assert_eq!(tree_bytes(out.path()), first);
    assert!(
        !String::from_utf8_lossy(&repeated.stdout).contains("recovery="),
        "byte-identical compile unexpectedly reported recovery: {}",
        String::from_utf8_lossy(&repeated.stdout)
    );

    seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "decision:second-queue",
        "Use a durable consumer cursor.",
    )
    .await;
    let changed = compile(&url, &binding, out.path(), &[]);
    assert_success(&changed);
    let stdout = String::from_utf8_lossy(&changed.stdout);
    let recovery = stdout
        .split_ascii_whitespace()
        .find_map(|field| field.strip_prefix("recovery="))
        .expect("changed compile must report its durable recovery path");
    let recovery = Path::new(recovery);
    assert!(recovery.is_absolute(), "recovery path must be absolute");
    assert!(recovery.is_dir(), "reported recovery path must exist");
    assert!(recovery.join("MEMORY.md").is_file());
    assert!(recovery.join("memphant-export.json").is_file());

    let legacy = compile(&url, &binding, out.path(), &["--source", "anything.json"]);
    assert!(!legacy.status.success());
    assert!(
        !repo_root()
            .join("examples/evals/compiled-memory-source.json")
            .exists()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_refuses_dirty_tampered_duplicate_traversal_and_unmanaged_trees() {
    let (url, binding, state) = spawn_server().await;
    let id = seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;

    for mutation in [
        "body",
        "missing",
        "memory",
        "unexpected",
        "duplicate_id",
        "duplicate_path",
        "duplicate_fact_key",
        "duplicate_json_key",
        "traversal",
        "footer",
        "generation",
        "footer_duplicate_json",
    ] {
        let out = tempfile::tempdir().unwrap();
        assert_success(&compile(&url, &binding, out.path(), &[]));
        let unit = out
            .path()
            .join("units")
            .join(format!("{}.md", id.as_uuid()));
        match mutation {
            "body" => fs::write(
                &unit,
                fs::read_to_string(&unit)
                    .unwrap()
                    .replace("Taipei", "Kyoto"),
            )
            .unwrap(),
            "missing" => fs::remove_file(&unit).unwrap(),
            "memory" => fs::write(out.path().join("MEMORY.md"), "tampered\n").unwrap(),
            "unexpected" => fs::write(out.path().join("notes.md"), "unmanaged\n").unwrap(),
            "duplicate_id" => {
                let path = out.path().join("memphant-export.json");
                let mut manifest: serde_json::Value =
                    serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
                let entry = manifest["entries"][0].clone();
                manifest["entries"].as_array_mut().unwrap().push(entry);
                fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();
            }
            "duplicate_path" | "duplicate_fact_key" => {
                let path = out.path().join("memphant-export.json");
                let mut manifest: serde_json::Value =
                    serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
                let mut entry = manifest["entries"][0].clone();
                let new_id = "ffffffff-ffff-4fff-8fff-ffffffffffff";
                entry["unit_id"] = serde_json::json!(new_id);
                if mutation == "duplicate_fact_key" {
                    entry["path"] = serde_json::json!(format!("units/{new_id}.md"));
                } else {
                    entry["fact_key"] = serde_json::json!("different:key");
                }
                manifest["entries"].as_array_mut().unwrap().push(entry);
                fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();
            }
            "duplicate_json_key" => {
                let path = out.path().join("memphant-export.json");
                let manifest = fs::read_to_string(&path).unwrap();
                fs::write(
                    path,
                    manifest.replacen(
                        "\"schema_version\": 2,",
                        "\"schema_version\": 2,\n  \"schema_version\": 2,",
                        1,
                    ),
                )
                .unwrap();
            }
            "traversal" => {
                let path = out.path().join("memphant-export.json");
                let mut manifest: serde_json::Value =
                    serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
                manifest["entries"][0]["path"] = serde_json::json!("../escape.md");
                fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();
            }
            "footer" => {
                let content = fs::read_to_string(&unit).unwrap();
                fs::write(
                    &unit,
                    content.replace("\"confidence\":1.0", "\"confidence\":0.5"),
                )
                .unwrap();
            }
            "generation" => {
                let content = fs::read_to_string(&unit).unwrap();
                fs::write(
                    &unit,
                    content.replace("\"subject_generation\":0", "\"subject_generation\":1"),
                )
                .unwrap();
            }
            "footer_duplicate_json" => {
                let content = fs::read_to_string(&unit).unwrap();
                fs::write(
                    &unit,
                    content.replacen("\"unit_id\":", "\"unit_id\":\"duplicate\",\"unit_id\":", 1),
                )
                .unwrap();
            }
            _ => unreachable!(),
        }

        let rejected = compile(&url, &binding, out.path(), &[]);
        assert!(!rejected.status.success(), "{mutation} was overwritten");
        let stderr = String::from_utf8_lossy(&rejected.stderr);
        assert!(stderr.contains("compile=dirty"), "{mutation}: {stderr}");
        assert!(
            stderr.contains("run `memphant sync` or restore"),
            "{mutation}: {stderr}"
        );
        let expected = match mutation {
            "duplicate_id" => Some("duplicate unit_id"),
            "duplicate_path" => Some("duplicate path"),
            "duplicate_fact_key" => Some("duplicate fact_key"),
            "footer_duplicate_json" => Some("duplicate JSON key"),
            _ => None,
        };
        if let Some(expected) = expected {
            assert!(stderr.contains(expected), "{mutation}: {stderr}");
        }
        let dirty = verify(out.path());
        assert!(!dirty.status.success(), "{mutation} verified clean");
    }
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_and_verify_reject_managed_symlinks() {
    use std::os::unix::fs::symlink;

    let (url, binding, state) = spawn_server().await;
    let id = seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;
    for target_kind in ["root", "units", "inbox", "memory", "manifest", "unit"] {
        let base = tempfile::tempdir().unwrap();
        let out = base.path().join("nested/memory");
        assert_success(&compile(&url, &binding, &out, &[]));
        match target_kind {
            "root" => {
                let target = base.path().join("moved-root");
                fs::rename(&out, &target).unwrap();
                symlink(&target, &out).unwrap();
            }
            "units" | "inbox" => {
                let path = out.join(target_kind);
                let target = base.path().join(format!("moved-{target_kind}"));
                fs::rename(&path, &target).unwrap();
                symlink(&target, &path).unwrap();
            }
            "memory" | "manifest" | "unit" => {
                let path = match target_kind {
                    "memory" => out.join("MEMORY.md"),
                    "manifest" => out.join("memphant-export.json"),
                    "unit" => out.join("units").join(format!("{}.md", id.as_uuid())),
                    _ => unreachable!(),
                };
                let target = base.path().join(format!("moved-{target_kind}.file"));
                fs::rename(&path, &target).unwrap();
                symlink(&target, &path).unwrap();
            }
            _ => unreachable!(),
        }

        let rejected = compile(&url, &binding, &out, &[]);
        assert!(!rejected.status.success(), "{target_kind} symlink accepted");
        assert!(
            String::from_utf8_lossy(&rejected.stderr).contains("symlink"),
            "{target_kind}: {}",
            String::from_utf8_lossy(&rejected.stderr)
        );
        assert!(!verify(&out).status.success());
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn verify_rejects_coordinated_manifest_semantic_and_memory_tampering() {
    let (url, binding, state) = spawn_server().await;
    let id = seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;

    for mutation in ["snapshot", "context", "validity", "body", "memory"] {
        let out = tempfile::tempdir().unwrap();
        assert_success(&compile(&url, &binding, out.path(), &[]));
        let manifest_path = out.path().join("memphant-export.json");
        let unit_path = out
            .path()
            .join("units")
            .join(format!("{}.md", id.as_uuid()));
        let mut manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
        match mutation {
            "snapshot" => manifest["snapshot_sha256"] = serde_json::json!("a".repeat(64)),
            "context" => {
                manifest["scope_id"] = serde_json::json!("00000000-0000-0000-0000-00000000dead")
            }
            "validity" => {
                manifest["entries"][0]["valid_from"] = serde_json::json!("2026-07-23T00:00:00Z")
            }
            "body" => {
                let old = fs::read_to_string(&unit_path).unwrap();
                let new_body = "City is Kyoto.";
                let body_hash = sha256(new_body.as_bytes());
                let footer_start = old.rfind("<!-- memphant ").unwrap();
                let footer_end = old.rfind(" -->").unwrap();
                let mut footer: serde_json::Value =
                    serde_json::from_str(&old[footer_start + "<!-- memphant ".len()..footer_end])
                        .unwrap();
                footer["body_sha256"] = serde_json::json!(body_hash);
                let rendered = format!(
                    "# profile:city\n\n{new_body}\n\n<!-- memphant {} -->\n",
                    serde_json::to_string(&footer).unwrap()
                );
                fs::write(&unit_path, &rendered).unwrap();
                manifest["entries"][0]["body_sha256"] = serde_json::json!(body_hash);
                manifest["entries"][0]["file_sha256"] =
                    serde_json::json!(sha256(rendered.as_bytes()));
            }
            "memory" => {
                let memory_path = out.path().join("MEMORY.md");
                let memory = fs::read_to_string(&memory_path)
                    .unwrap()
                    .replace("# MemPhant Memory", "# Forged Memory");
                fs::write(&memory_path, &memory).unwrap();
                manifest["memory_sha256"] = serde_json::json!(sha256(memory.as_bytes()));
            }
            _ => unreachable!(),
        }
        fs::write(
            &manifest_path,
            format!("{}\n", serde_json::to_string_pretty(&manifest).unwrap()),
        )
        .unwrap();

        let result = verify(out.path());
        assert!(
            !result.status.success(),
            "coordinated {mutation} tamper verified clean"
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_binds_an_existing_manifest_to_requested_and_server_context() {
    let (url, binding, state) = spawn_server().await;
    seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;

    let requested = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, requested.path(), &[]));
    let mut wrong_binding = binding.clone();
    wrong_binding.scope_id = memphant_types::ScopeId::new();
    let wrong_request = compile(&url, &wrong_binding, requested.path(), &[]);
    assert!(!wrong_request.status.success());
    assert!(String::from_utf8_lossy(&wrong_request.stderr).contains("manifest context mismatch"));

    let server = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, server.path(), &[]));
    let manifest_path = server.path().join("memphant-export.json");
    let manifest = fs::read_to_string(&manifest_path).unwrap();
    let changed = manifest.replace(TENANT, "00000000-0000-0000-0000-00000000dead");
    assert_ne!(changed, manifest);
    fs::write(&manifest_path, changed).unwrap();
    let wrong_server_context = compile(&url, &binding, server.path(), &[]);
    assert!(!wrong_server_context.status.success());
    assert!(
        String::from_utf8_lossy(&wrong_server_context.stderr)
            .contains("server context differs from manifest")
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_refuses_a_local_edit_while_projection_request_is_in_flight() {
    let (url, binding, state, projection_started) = spawn_delayed_server().await;
    let id = seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path(), &[]));
    let unit = out
        .path()
        .join("units")
        .join(format!("{}.md", id.as_uuid()));
    let changed = fs::read_to_string(&unit)
        .unwrap()
        .replace("Taipei", "changed while fetching");
    let target = unit.clone();
    let editor = std::thread::spawn(move || {
        projection_started.recv().unwrap();
        fs::write(target, &changed).unwrap();
        changed
    });

    let result = compile(&url, &binding, out.path(), &[]);
    let changed = editor.join().unwrap();
    assert!(
        !result.status.success(),
        "concurrent local edit was overwritten"
    );
    assert_eq!(fs::read_to_string(unit).unwrap(), changed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_refuses_an_absent_output_that_appears_while_fetching() {
    let (url, binding, state, projection_started) = spawn_delayed_server_at(0).await;
    seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;
    let base = tempfile::tempdir().unwrap();
    let sentinel = base.path().join("sentinel");
    fs::write(&sentinel, "parent-sentinel").unwrap();
    let out = base.path().join("memory");
    let appeared = out.clone();
    let editor = std::thread::spawn(move || {
        projection_started.recv().unwrap();
        fs::create_dir(&appeared).unwrap();
    });

    let result = compile(&url, &binding, &out, &[]);
    editor.join().unwrap();
    assert!(
        !result.status.success(),
        "new output directory was overwritten"
    );
    assert!(root_names(&out).is_empty());
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "parent-sentinel");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_refuses_an_empty_output_swapped_while_fetching() {
    let (url, binding, state, projection_started) = spawn_delayed_server_at(0).await;
    seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;
    let base = tempfile::tempdir().unwrap();
    let sentinel = base.path().join("sentinel");
    fs::write(&sentinel, "parent-sentinel").unwrap();
    let out = base.path().join("memory");
    let moved = base.path().join("moved-memory");
    fs::create_dir(&out).unwrap();
    let out_for_editor = out.clone();
    let moved_for_editor = moved.clone();
    let editor = std::thread::spawn(move || {
        projection_started.recv().unwrap();
        fs::rename(&out_for_editor, &moved_for_editor).unwrap();
        fs::create_dir(&out_for_editor).unwrap();
    });

    let result = compile(&url, &binding, &out, &[]);
    editor.join().unwrap();
    assert!(
        !result.status.success(),
        "swapped empty output was overwritten"
    );
    assert!(root_names(&out).is_empty());
    assert!(root_names(&moved).is_empty());
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "parent-sentinel");
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compile_refuses_root_and_units_redirection_while_fetching() {
    use std::os::unix::fs::symlink;

    for swapped in ["root", "units"] {
        let (url, binding, state, projection_started) = spawn_delayed_server().await;
        let id = seed_unit(
            &state,
            &binding,
            UnitState::Active,
            "profile:city",
            "City is Taipei.",
        )
        .await;
        let base = tempfile::tempdir().unwrap();
        let out = base.path().join("memory");
        assert_success(&compile(&url, &binding, &out, &[]));
        let before = tree_bytes(&out);
        let moved = base.path().join(format!("moved-{swapped}"));
        let outside = base.path().join(format!("outside-{swapped}"));
        let out_for_editor = out.clone();
        let editor = std::thread::spawn(move || {
            projection_started.recv().unwrap();
            match swapped {
                "root" => {
                    fs::rename(&out_for_editor, &moved).unwrap();
                    fs::create_dir(&outside).unwrap();
                    fs::write(outside.join("sentinel"), "outside-root").unwrap();
                    symlink(&outside, &out_for_editor).unwrap();
                }
                "units" => {
                    fs::rename(out_for_editor.join("units"), &moved).unwrap();
                    fs::create_dir(&outside).unwrap();
                    fs::write(
                        outside.join(format!("{}.md", id.as_uuid())),
                        "outside-units",
                    )
                    .unwrap();
                    symlink(&outside, out_for_editor.join("units")).unwrap();
                }
                _ => unreachable!(),
            }
            (moved, outside)
        });

        let result = compile(&url, &binding, &out, &[]);
        let (moved, outside) = editor.join().unwrap();
        assert!(
            !result.status.success(),
            "{swapped} redirection was accepted"
        );
        if swapped == "root" {
            assert_eq!(tree_bytes(&moved), before);
            assert_eq!(
                fs::read_to_string(outside.join("sentinel")).unwrap(),
                "outside-root"
            );
        } else {
            assert_eq!(
                fs::read_to_string(outside.join(format!("{}.md", id.as_uuid()))).unwrap(),
                "outside-units"
            );
            assert!(moved.join(format!("{}.md", id.as_uuid())).is_file());
        }
    }
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn verify_rejects_fifo_without_blocking() {
    let (url, binding, state) = spawn_server().await;
    seed_unit(
        &state,
        &binding,
        UnitState::Active,
        "profile:city",
        "City is Taipei.",
    )
    .await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path(), &[]));
    fs::remove_file(out.path().join("MEMORY.md")).unwrap();
    assert!(
        Command::new("mkfifo")
            .arg(out.path().join("MEMORY.md"))
            .status()
            .unwrap()
            .success()
    );

    let mut child = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .current_dir(repo_root())
        .args(["verify", "--lock", "memphant.lock", "--export"])
        .arg(out.path())
        .spawn()
        .unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(!status.success());
            break;
        }
        if std::time::Instant::now() >= deadline {
            child.kill().unwrap();
            panic!("verify blocked while opening a FIFO");
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}

async fn spawn_server() -> (
    String,
    ContextBindingResponse,
    AppState<memphant_core::InMemoryStore>,
) {
    let tenant = TenantId::from_u128(uuid::Uuid::parse_str(TENANT).unwrap().as_u128());
    let state = AppState::new_in_memory().with_dev_tenant(tenant);
    let binding = state
        .store()
        .resolve_context_binding(
            tenant,
            "b2-cli-contract".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "b2-user".into(),
                    kind: "user".into(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "b2-user".into(),
                    kind: "user".into(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "b2-root".into(),
                    kind: "user_root".into(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "b2-l0".into(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = memphant_server::app(state.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (format!("http://{address}"), binding, state)
}

async fn spawn_delayed_server() -> (
    String,
    ContextBindingResponse,
    AppState<memphant_core::InMemoryStore>,
    std::sync::mpsc::Receiver<()>,
) {
    spawn_delayed_server_at(1).await
}

async fn spawn_delayed_server_at(
    delayed_request: usize,
) -> (
    String,
    ContextBindingResponse,
    AppState<memphant_core::InMemoryStore>,
    std::sync::mpsc::Receiver<()>,
) {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    let tenant = TenantId::from_u128(uuid::Uuid::parse_str(TENANT).unwrap().as_u128());
    let state = AppState::new_in_memory().with_dev_tenant(tenant);
    let binding = state
        .store()
        .resolve_context_binding(
            tenant,
            "b2-cli-delayed-contract".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "b2-user".into(),
                    kind: "user".into(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "b2-user".into(),
                    kind: "user".into(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "b2-root".into(),
                    kind: "user_root".into(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "b2-l0".into(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .unwrap();
    let (started_tx, started_rx) = std::sync::mpsc::sync_channel(1);
    let started_tx = Arc::new(Mutex::new(Some(started_tx)));
    let requests = Arc::new(AtomicUsize::new(0));
    let app = memphant_server::app(state.clone()).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| {
            let started_tx = started_tx.clone();
            let requests = requests.clone();
            async move {
                if request.uri().path().ends_with("/projection")
                    && requests.fetch_add(1, Ordering::SeqCst) == delayed_request
                {
                    if let Some(sender) = started_tx.lock().unwrap().take() {
                        sender.send(()).unwrap();
                    }
                    tokio::time::sleep(Duration::from_millis(200)).await;
                }
                next.run(request).await
            }
        },
    ));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (format!("http://{address}"), binding, state, started_rx)
}

async fn seed_unit(
    state: &AppState<memphant_core::InMemoryStore>,
    binding: &ContextBindingResponse,
    unit_state: UnitState,
    fact_key: &str,
    body: &str,
) -> memphant_types::UnitId {
    let tenant = TenantId::from_u128(uuid::Uuid::parse_str(TENANT).unwrap().as_u128());
    let context = state
        .store()
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .unwrap();
    let mut transaction = state.store().begin(&context).await.unwrap();
    let id = state
        .store()
        .stage_memory_unit(
            &mut transaction,
            NewMemoryUnit {
                tenant_id: tenant,
                data_subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                kind: MemoryKind::Semantic,
                state: unit_state,
                fact_key: Some(fact_key.into()),
                predicate: Some("states".into()),
                body: body.into(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(binding.actor_id),
                source_kind: Some("test".into()),
                source_ref: format!("test:{fact_key}"),
                observed_at: "2026-07-22T00:00:00Z".into(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .unwrap();
    state.store().commit(transaction).await.unwrap();
    id
}

fn compile(url: &str, binding: &ContextBindingResponse, out: &Path, tail: &[&str]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_memphant-cli"));
    command
        .env("MEMPHANT_URL", url)
        .env_remove("MEMPHANT_API_KEY")
        .args(["compile", "--agent-node"])
        .arg(binding.agent_node_id.as_uuid().to_string())
        .args(["--out"])
        .arg(out)
        .args(["--scope"])
        .arg(binding.scope_id.as_uuid().to_string())
        .args([
            "--subject-generation",
            &binding.subject_generation.to_string(),
        ])
        .args(["--actor"])
        .arg(binding.actor_id.as_uuid().to_string())
        .args(["--subject-id"])
        .arg(binding.subject_id.as_uuid().to_string())
        .args(tail)
        .output()
        .unwrap()
}

fn verify(export: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .current_dir(repo_root())
        .args(["verify", "--lock", "memphant.lock", "--export"])
        .arg(export)
        .output()
        .unwrap()
}

fn root_names(root: &Path) -> Vec<String> {
    let mut names = fs::read_dir(root)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .collect::<Vec<_>>();
    names.sort();
    names
}

fn tree_bytes(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn visit(root: &Path, current: &Path, files: &mut BTreeMap<String, Vec<u8>>) {
        for entry in fs::read_dir(current).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                visit(root, &path, files);
            } else {
                files.insert(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                    fs::read(path).unwrap(),
                );
            }
        }
    }
    let mut files = BTreeMap::new();
    visit(root, root, &mut files);
    files
}

fn assert_success(output: &Output) {
    assert!(
        output.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}
