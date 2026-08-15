use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use axum::response::IntoResponse;
use memphant_core::MemoryStore;
use memphant_core::service::file_sync_plan_sha256;
use memphant_server::AppState;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingResponse,
    ContextBindingScopeRef, MemoryKind, NewMemoryUnit, TenantId, TrustLevel, UnitState,
};
use sha2::{Digest, Sha256};

const TENANT: &str = "00000000-0000-0000-0000-00000000b204";

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sync_round_trips_all_four_edit_classes_as_one_deterministic_plan() {
    let (url, binding, state) = spawn_server().await;
    let corrected = seed_unit(&state, &binding, "decision:queue", "Poll the queue.").await;
    let deleted = seed_unit(&state, &binding, "profile:obsolete", "Delete me.").await;
    let contradicted = seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));

    replace_body(
        &out.path()
            .join("units")
            .join(format!("{}.md", corrected.as_uuid())),
        "Use LISTEN/NOTIFY.",
    );
    fs::remove_file(
        out.path()
            .join("units")
            .join(format!("{}.md", deleted.as_uuid())),
    )
    .unwrap();
    fs::write(
        out.path().join("inbox/new-fact.md"),
        "# decision:worker\n\nRun one durable worker.\n",
    )
    .unwrap();
    fs::write(
        out.path().join("inbox/city.md"),
        "# profile:city\n\nCity is Kyoto.\n",
    )
    .unwrap();

    let first = sync(&url, &binding, out.path(), false);
    assert_success(&first);
    let second = sync(&url, &binding, out.path(), false);
    assert_success(&second);
    assert_eq!(
        first.stdout, second.stdout,
        "dry-run plan must be byte-stable"
    );
    let plan: serde_json::Value = serde_json::from_slice(&first.stdout).unwrap();
    assert_eq!(plan["plan_sha256"].as_str().unwrap().len(), 64);
    assert_eq!(plan["operations"].as_array().unwrap().len(), 4);
    let typed_operations: Vec<memphant_types::FileSyncOperation> =
        serde_json::from_value(plan["operations"].clone()).unwrap();
    assert_eq!(
        plan["plan_sha256"],
        file_sync_plan_sha256(&typed_operations).unwrap()
    );
    assert_eq!(
        plan["operations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|operation| operation["op"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["correct", "forget", "retain", "retain"]
    );
    assert_eq!(
        plan["destructive"],
        serde_json::json!([format!("forget:{}", deleted.as_uuid())])
    );

    let applied = sync(&url, &binding, out.path(), true);
    assert_success(&applied);
    assert!(String::from_utf8_lossy(&applied.stdout).contains("sync=applied"));
    assert!(
        fs::read_dir(out.path().join("inbox"))
            .unwrap()
            .next()
            .is_none()
    );
    assert_success(&verify(out.path()));

    let empty = sync(&url, &binding, out.path(), false);
    assert_success(&empty);
    let empty: serde_json::Value = serde_json::from_slice(&empty.stdout).unwrap();
    assert_eq!(empty["operations"], serde_json::json!([]));

    let once = tree_bytes(out.path());
    assert_success(&compile(&url, &binding, out.path()));
    assert_eq!(tree_bytes(out.path()), once);
    let units = fs::read_dir(out.path().join("units"))
        .unwrap()
        .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
        .collect::<Vec<_>>();
    assert!(units.iter().any(|unit| unit.contains("Use LISTEN/NOTIFY.")));
    assert!(
        units
            .iter()
            .any(|unit| unit.contains("Run one durable worker."))
    );
    assert!(units.iter().any(|unit| unit.contains("City is Kyoto.")));
    assert!(
        !out.path()
            .join("units")
            .join(format!("{}.md", contradicted.as_uuid()))
            .exists()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sync_fails_closed_on_immutable_and_inbox_format_edits() {
    let (url, binding, state, posts) = spawn_counted_server("b2-invalid-edits").await;
    let id = seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;

    for mutation in [
        "footer",
        "footer_whitespace",
        "manifest",
        "manifest_whitespace",
        "manifest_key_order",
        "manifest_file_sha",
        "manifest_duplicate_key",
        "memory",
        "inbox_footer",
        "inbox_crlf",
        "inbox_blank",
        "inbox_extra_blank",
        "inbox_whitespace_first_line",
        "duplicate_inbox_key",
        "overlap_correct",
        "nested_inbox",
        "reserved_inbox",
        "unexpected_unit",
    ] {
        let out = tempfile::tempdir().unwrap();
        assert_success(&compile(&url, &binding, out.path()));
        match mutation {
            "footer" => {
                let path = out
                    .path()
                    .join("units")
                    .join(format!("{}.md", id.as_uuid()));
                let text = fs::read_to_string(&path).unwrap();
                fs::write(
                    path,
                    text.replace("\"confidence\":1.0", "\"confidence\":0.5"),
                )
                .unwrap();
            }
            "footer_whitespace" => {
                let path = out
                    .path()
                    .join("units")
                    .join(format!("{}.md", id.as_uuid()));
                let text = fs::read_to_string(&path).unwrap();
                fs::write(path, text.replace("<!-- memphant {", "<!-- memphant { ")).unwrap();
            }
            "manifest" => {
                let path = out.path().join("memphant-export.json");
                let text = fs::read_to_string(&path).unwrap();
                fs::write(
                    path,
                    text.replace("\"confidence\": 1.0", "\"confidence\": 0.5"),
                )
                .unwrap();
            }
            "manifest_whitespace" => {
                let path = out.path().join("memphant-export.json");
                let mut bytes = fs::read(&path).unwrap();
                bytes.push(b'\n');
                fs::write(path, bytes).unwrap();
            }
            "manifest_key_order" => {
                let path = out.path().join("memphant-export.json");
                let text = fs::read_to_string(&path).unwrap();
                let mut lines = text.lines().collect::<Vec<_>>();
                lines.swap(1, 2);
                fs::write(path, format!("{}\n", lines.join("\n"))).unwrap();
            }
            "manifest_file_sha" => {
                let unit = out
                    .path()
                    .join("units")
                    .join(format!("{}.md", id.as_uuid()));
                replace_body(&unit, "City is Kyoto.");
                let path = out.path().join("memphant-export.json");
                let text = fs::read_to_string(&path).unwrap();
                let manifest: serde_json::Value = serde_json::from_str(&text).unwrap();
                let original = manifest["entries"][0]["file_sha256"].as_str().unwrap();
                let replacement = sha256(&fs::read(unit).unwrap());
                assert_eq!(original.len(), replacement.len());
                let changed = text.replacen(original, &replacement, 1);
                assert_eq!(changed.len(), text.len());
                fs::write(path, changed).unwrap();
            }
            "manifest_duplicate_key" => {
                let path = out.path().join("memphant-export.json");
                let text = fs::read_to_string(&path).unwrap();
                fs::write(
                    path,
                    text.replacen(
                        "\"schema_version\": 2,",
                        "\"schema_version\": 2,\n  \"schema_version\": 2,",
                        1,
                    ),
                )
                .unwrap();
            }
            "memory" => fs::write(out.path().join("MEMORY.md"), "# changed\n").unwrap(),
            "inbox_footer" => fs::write(
                out.path().join("inbox/bad.md"),
                "# bad\n\nbody\n\n<!-- memphant {} -->\n",
            )
            .unwrap(),
            "inbox_crlf" => {
                fs::write(out.path().join("inbox/bad.md"), "# bad\r\n\r\nbody\r\n").unwrap()
            }
            "inbox_blank" => fs::write(out.path().join("inbox/bad.md"), "# bad\n\n \n").unwrap(),
            "inbox_extra_blank" => {
                fs::write(out.path().join("inbox/bad.md"), "# bad\n\n\nbody\n").unwrap()
            }
            "inbox_whitespace_first_line" => {
                fs::write(out.path().join("inbox/bad.md"), "# bad\n\n  \nbody\n").unwrap()
            }
            "duplicate_inbox_key" => {
                fs::write(out.path().join("inbox/one.md"), "# same\n\none\n").unwrap();
                fs::write(out.path().join("inbox/two.md"), "# same\n\ntwo\n").unwrap();
            }
            "overlap_correct" => {
                let path = out
                    .path()
                    .join("units")
                    .join(format!("{}.md", id.as_uuid()));
                replace_body(&path, "City is Kyoto.");
                fs::write(
                    out.path().join("inbox/city.md"),
                    "# profile:city\n\nCity is Osaka.\n",
                )
                .unwrap();
            }
            "nested_inbox" => {
                fs::create_dir(out.path().join("inbox/nested")).unwrap();
                fs::write(out.path().join("inbox/nested/bad.md"), "# bad\n\nbody\n").unwrap();
            }
            "reserved_inbox" => {
                fs::write(out.path().join("inbox/con.md"), "# bad\n\nbody\n").unwrap()
            }
            "unexpected_unit" => {
                fs::write(out.path().join("units/not-a-unit.md"), "# bad\n\nbody\n").unwrap()
            }
            _ => unreachable!(),
        }
        if mutation == "manifest_file_sha" {
            let result = verify(out.path());
            assert!(
                !result.status.success(),
                "semantic manifest drift verified clean"
            );
            assert!(
                String::from_utf8_lossy(&result.stderr).contains("body hash differs from manifest"),
                "{}",
                String::from_utf8_lossy(&result.stderr)
            );
        }
        let before = tree_bytes(out.path());
        let posts_before = posts.load(Ordering::SeqCst);
        for apply in [false, true] {
            let result = sync(&url, &binding, out.path(), apply);
            assert!(
                !result.status.success(),
                "{mutation} was accepted with apply={apply}"
            );
            assert!(
                String::from_utf8_lossy(&result.stderr).contains("sync=invalid"),
                "{mutation} apply={apply}: {}",
                String::from_utf8_lossy(&result.stderr)
            );
            assert_eq!(
                tree_bytes(out.path()),
                before,
                "{mutation} mutated files with apply={apply}"
            );
            assert_eq!(
                posts.load(Ordering::SeqCst),
                posts_before,
                "{mutation} reached POST with apply={apply}"
            );
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn aggregate_oversize_inbox_batch_is_rejected_before_post() {
    let (url, binding, _state, posts) = spawn_counted_server("b2-oversize-request").await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));
    let body = "x".repeat(1_100_000);
    for name in ["one", "two"] {
        fs::write(
            out.path().join("inbox").join(format!("{name}.md")),
            format!("# decision:{name}\n\n{body}\n"),
        )
        .unwrap();
    }
    let before = tree_bytes(out.path());

    for apply in [false, true] {
        let result = sync(&url, &binding, out.path(), apply);

        assert!(!result.status.success(), "apply={apply}");
        let stderr = String::from_utf8_lossy(&result.stderr);
        assert!(stderr.contains("sync=invalid"), "apply={apply}: {stderr}");
        assert!(
            stderr.contains("exceeds the 2097152 byte limit"),
            "apply={apply}: {stderr}"
        );
        assert_eq!(posts.load(Ordering::SeqCst), 0, "apply={apply}");
        assert_eq!(tree_bytes(out.path()), before, "apply={apply}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sync_reports_stale_base_without_local_or_remote_mutation() {
    let (url, binding, state) = spawn_server().await;
    let id = seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));
    replace_body(
        &out.path()
            .join("units")
            .join(format!("{}.md", id.as_uuid())),
        "City is Kyoto.",
    );
    seed_unit(&state, &binding, "decision:new", "Server moved ahead.").await;
    let before = tree_bytes(out.path());

    let result = sync(&url, &binding, out.path(), true);
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("sync=conflict"));
    assert_eq!(tree_bytes(out.path()), before);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn late_server_failure_rolls_back_multi_operation_batch_and_keeps_local_tree() {
    let (url, binding, state) = spawn_server().await;
    let corrected = seed_unit(
        &state,
        &binding,
        "decision:queue",
        "Use the original queue policy.",
    )
    .await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));
    replace_body(
        &out.path()
            .join("units")
            .join(format!("{}.md", corrected.as_uuid())),
        "Use the staged correction.",
    );
    fs::write(
        out.path().join("inbox/new.md"),
        "# decision:new\n\nThe staged retain must roll back too.\n",
    )
    .unwrap();
    let before = tree_bytes(out.path());
    state.store().fail_next_mutation_response();

    let result = sync(&url, &binding, out.path(), true);

    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("sync=outcome_unknown"));
    assert_eq!(tree_bytes(out.path()), before);

    let canonical = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, canonical.path()));
    let units = fs::read_dir(canonical.path().join("units"))
        .unwrap()
        .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
        .collect::<Vec<_>>();
    assert!(
        units
            .iter()
            .any(|unit| unit.contains("Use the original queue policy."))
    );
    assert!(
        !units
            .iter()
            .any(|unit| unit.contains("Use the staged correction."))
    );
    assert!(
        !units
            .iter()
            .any(|unit| unit.contains("The staged retain must roll back too."))
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn rejected_and_ambiguous_batches_preserve_the_exact_local_plan() {
    for (status, code, expected) in [
        (
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            "sync_invalid",
            "sync=invalid",
        ),
        (
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            "backend_error",
            "sync=outcome_unknown",
        ),
    ] {
        let (binding, state) = bound_state(code).await;
        let id = seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;
        let app = memphant_server::app(state.clone()).layer(axum::middleware::from_fn(
            move |request: axum::extract::Request, next: axum::middleware::Next| async move {
                if request.uri().path() == "/v1/file-sync" {
                    (
                        status,
                        axum::Json(serde_json::json!({
                            "error": {
                                "code": code,
                                "message": "injected batch failure",
                                "request_id": "req_test",
                                "details": {}
                            }
                        })),
                    )
                        .into_response()
                } else {
                    next.run(request).await
                }
            },
        ));
        let url = serve(app).await;
        let out = tempfile::tempdir().unwrap();
        assert_success(&compile(&url, &binding, out.path()));
        replace_body(
            &out.path()
                .join("units")
                .join(format!("{}.md", id.as_uuid())),
            "City is Kyoto.",
        );
        fs::write(
            out.path().join("inbox/new.md"),
            "# decision:new\n\nCreate this only atomically.\n",
        )
        .unwrap();
        let before = tree_bytes(out.path());

        let result = sync(&url, &binding, out.path(), true);
        assert!(!result.status.success());
        assert!(
            String::from_utf8_lossy(&result.stderr).contains(expected),
            "{code}: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        assert_eq!(tree_bytes(out.path()), before);

        let canonical = tempfile::tempdir().unwrap();
        assert_success(&compile(&url, &binding, canonical.path()));
        let units = fs::read_dir(canonical.path().join("units"))
            .unwrap()
            .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
            .collect::<Vec<_>>();
        assert!(units.iter().any(|unit| unit.contains("City is Taipei.")));
        assert!(
            !units
                .iter()
                .any(|unit| unit.contains("Create this only atomically."))
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn empty_apply_completes_preflight_without_posting_a_batch() {
    let (binding, state) = bound_state("b2-empty-apply").await;
    seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;
    let posts = Arc::new(AtomicUsize::new(0));
    let counted = posts.clone();
    let app = memphant_server::app(state).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| {
            let counted = counted.clone();
            async move {
                if request.uri().path() == "/v1/file-sync" {
                    counted.fetch_add(1, Ordering::SeqCst);
                }
                next.run(request).await
            }
        },
    ));
    let url = serve(app).await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));

    let result = sync(&url, &binding, out.path(), true);
    assert_success(&result);
    assert!(String::from_utf8_lossy(&result.stdout).contains("sync=noop"));
    assert_eq!(posts.load(Ordering::SeqCst), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn preflight_get_503_is_unavailable_without_post_or_local_mutation() {
    let (binding, state) = bound_state("b2-preflight-503").await;
    seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;
    let projections = Arc::new(AtomicUsize::new(0));
    let posts = Arc::new(AtomicUsize::new(0));
    let counted_projections = projections.clone();
    let counted_posts = posts.clone();
    let app = memphant_server::app(state).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| {
            let projections = counted_projections.clone();
            let posts = counted_posts.clone();
            async move {
                if request.uri().path().ends_with("/projection")
                    && projections.fetch_add(1, Ordering::SeqCst) > 0
                {
                    return (
                        axum::http::StatusCode::SERVICE_UNAVAILABLE,
                        axum::Json(serde_json::json!({
                            "error": {"code": "temporarily_unavailable"}
                        })),
                    )
                        .into_response();
                }
                if request.uri().path() == "/v1/file-sync" {
                    posts.fetch_add(1, Ordering::SeqCst);
                }
                next.run(request).await
            }
        },
    ));
    let url = serve(app).await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));
    fs::write(
        out.path().join("inbox/new.md"),
        "# decision:new\n\nKeep this local.\n",
    )
    .unwrap();
    let before = tree_bytes(out.path());

    let result = sync(&url, &binding, out.path(), true);

    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("sync=unavailable"));
    assert_eq!(posts.load(Ordering::SeqCst), 0);
    assert_eq!(tree_bytes(out.path()), before);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn invalid_projection_evaluated_at_is_rejected_before_plan_or_post() {
    for (label, evaluated_at) in [
        ("malformed", "not-a-timestamp"),
        ("non-utc", "2026-07-23T01:00:00+01:00"),
    ] {
        let (binding, state) = bound_state(&format!("b2-evaluated-at-{label}")).await;
        seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;
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
        let mut stub = state
            .service()
            .canonical_projection(&context)
            .await
            .unwrap();
        stub.evaluated_at = evaluated_at.to_string();
        let projections = Arc::new(AtomicUsize::new(0));
        let posts = Arc::new(AtomicUsize::new(0));
        let counted_projections = projections.clone();
        let counted_posts = posts.clone();
        let app = memphant_server::app(state).layer(axum::middleware::from_fn(
            move |request: axum::extract::Request, next: axum::middleware::Next| {
                let projections = counted_projections.clone();
                let posts = counted_posts.clone();
                let stub = stub.clone();
                async move {
                    if request.uri().path().ends_with("/projection")
                        && projections.fetch_add(1, Ordering::SeqCst) > 0
                    {
                        return axum::Json(stub).into_response();
                    }
                    if request.uri().path() == "/v1/file-sync" {
                        posts.fetch_add(1, Ordering::SeqCst);
                    }
                    next.run(request).await
                }
            },
        ));
        let url = serve(app).await;
        let out = tempfile::tempdir().unwrap();
        assert_success(&compile(&url, &binding, out.path()));
        fs::write(
            out.path().join("inbox/new.md"),
            "# decision:new\n\nKeep this local.\n",
        )
        .unwrap();
        let before = tree_bytes(out.path());

        for apply in [false, true] {
            let result = sync(&url, &binding, out.path(), apply);
            assert!(!result.status.success(), "{label} apply={apply}");
            let stderr = String::from_utf8_lossy(&result.stderr);
            assert!(
                stderr.contains("sync=invalid"),
                "{label} apply={apply}: {stderr}"
            );
            assert_eq!(posts.load(Ordering::SeqCst), 0, "{label} apply={apply}");
            assert_eq!(tree_bytes(out.path()), before, "{label} apply={apply}");
        }

        let compile_out = tempfile::tempdir().unwrap();
        let result = compile(&url, &binding, compile_out.path());
        assert!(!result.status.success(), "{label} compile");
        assert!(
            String::from_utf8_lossy(&result.stderr)
                .contains("projection evaluated_at must be RFC3339 UTC"),
            "{label} compile: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        assert!(tree_bytes(compile_out.path()).is_empty(), "{label} compile");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn delayed_preflight_get_times_out_without_post_or_local_mutation() {
    let (binding, state) = bound_state("b2-preflight-timeout").await;
    seed_unit(&state, &binding, "profile:city", "City is Taipei.").await;
    let projections = Arc::new(AtomicUsize::new(0));
    let posts = Arc::new(AtomicUsize::new(0));
    let counted_projections = projections.clone();
    let counted_posts = posts.clone();
    let app = memphant_server::app(state).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| {
            let projections = counted_projections.clone();
            let posts = counted_posts.clone();
            async move {
                if request.uri().path().ends_with("/projection")
                    && projections.fetch_add(1, Ordering::SeqCst) > 0
                {
                    tokio::time::sleep(std::time::Duration::from_millis(300)).await;
                }
                if request.uri().path() == "/v1/file-sync" {
                    posts.fetch_add(1, Ordering::SeqCst);
                }
                next.run(request).await
            }
        },
    ));
    let url = serve(app).await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));
    fs::write(
        out.path().join("inbox/new.md"),
        "# decision:new\n\nKeep this local.\n",
    )
    .unwrap();
    let before = tree_bytes(out.path());

    let result = sync_with_timeout(&url, &binding, out.path(), true, "100");

    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("sync=unavailable"));
    assert_eq!(posts.load(Ordering::SeqCst), 0);
    assert_eq!(tree_bytes(out.path()), before);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn committed_then_delayed_post_is_outcome_unknown_and_keeps_local_tree() {
    let (binding, state) = bound_state("b2-post-timeout").await;
    let app = memphant_server::app(state).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| async move {
            let is_sync = request.uri().path() == "/v1/file-sync";
            let response = next.run(request).await;
            if is_sync {
                tokio::time::sleep(std::time::Duration::from_millis(300)).await;
            }
            response
        },
    ));
    let url = serve(app).await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));
    fs::write(
        out.path().join("inbox/new.md"),
        "# decision:timeout\n\nThe server commits before the reply delay.\n",
    )
    .unwrap();
    let before = tree_bytes(out.path());

    let result = sync_with_timeout(&url, &binding, out.path(), true, "100");

    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("sync=outcome_unknown"));
    assert_eq!(tree_bytes(out.path()), before);
    let canonical = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, canonical.path()));
    assert!(
        fs::read_dir(canonical.path().join("units"))
            .unwrap()
            .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
            .any(|unit| unit.contains("The server commits before the reply delay."))
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn non_contract_success_and_invalid_200_receipts_are_outcome_unknown() {
    for (label, status, commit) in [
        ("accepted", axum::http::StatusCode::ACCEPTED, true),
        ("invalid-json", axum::http::StatusCode::OK, false),
    ] {
        let (binding, state) = bound_state(&format!("b2-{label}-receipt")).await;
        let app = memphant_server::app(state).layer(axum::middleware::from_fn(
            move |request: axum::extract::Request, next: axum::middleware::Next| async move {
                if request.uri().path() != "/v1/file-sync" {
                    return next.run(request).await;
                }
                if !commit {
                    return (status, "not a file-sync receipt").into_response();
                }
                let mut response = next.run(request).await;
                *response.status_mut() = status;
                response
            },
        ));
        let url = serve(app).await;
        let out = tempfile::tempdir().unwrap();
        assert_success(&compile(&url, &binding, out.path()));
        let body = format!("The {label} response is not a proven receipt.");
        fs::write(
            out.path().join("inbox/new.md"),
            format!("# decision:{label}\n\n{body}\n"),
        )
        .unwrap();
        let before = tree_bytes(out.path());

        let result = sync(&url, &binding, out.path(), true);

        assert!(!result.status.success());
        assert!(
            String::from_utf8_lossy(&result.stderr).contains("sync=outcome_unknown"),
            "{label}: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        assert_eq!(tree_bytes(out.path()), before, "{label}");
        if commit {
            let canonical = tempfile::tempdir().unwrap();
            assert_success(&compile(&url, &binding, canonical.path()));
            assert!(
                fs::read_dir(canonical.path().join("units"))
                    .unwrap()
                    .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
                    .any(|unit| unit.contains(&body))
            );
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn post_commit_inbox_replacement_is_preserved_and_never_overwritten() {
    let (binding, state) = bound_state("b2-post-commit-edit").await;
    let out = tempfile::tempdir().unwrap();
    let inbox = out.path().join("inbox/new.md");
    let replacement = "# replacement\n\nDo not consume me.\n".to_string();
    let target = inbox.clone();
    let replacement_for_server = replacement.clone();
    let app = memphant_server::app(state.clone()).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| {
            let target = target.clone();
            let replacement = replacement_for_server.clone();
            async move {
                let is_sync = request.uri().path() == "/v1/file-sync";
                let response = next.run(request).await;
                if is_sync {
                    fs::write(target, replacement).unwrap();
                }
                response
            }
        },
    ));
    let url = serve(app).await;
    assert_success(&compile(&url, &binding, out.path()));
    fs::write(&inbox, "# original\n\nCommit this fact.\n").unwrap();

    let result = sync(&url, &binding, out.path(), true);
    assert!(!result.status.success());
    let stderr = String::from_utf8_lossy(&result.stderr);
    assert!(stderr.contains("sync=post_commit_error remote_committed=true"));
    assert!(stderr.contains("committed_snapshot="));
    assert_eq!(fs::read_to_string(&inbox).unwrap(), replacement);

    let canonical = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, canonical.path()));
    assert!(
        fs::read_dir(canonical.path().join("units"))
            .unwrap()
            .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
            .any(|unit| unit.contains("Commit this fact."))
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn apply_reports_committed_and_later_canonical_snapshots_separately() {
    let (binding, state) = bound_state("b2-concurrent-final").await;
    let out = tempfile::tempdir().unwrap();
    let state_for_server = state.clone();
    let binding_for_server = binding.clone();
    let app = memphant_server::app(state.clone()).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| {
            let state = state_for_server.clone();
            let binding = binding_for_server.clone();
            async move {
                let is_sync = request.uri().path() == "/v1/file-sync";
                let response = next.run(request).await;
                if is_sync {
                    seed_unit(
                        &state,
                        &binding,
                        "decision:concurrent",
                        "A later writer committed.",
                    )
                    .await;
                }
                response
            }
        },
    ));
    let url = serve(app).await;
    assert_success(&compile(&url, &binding, out.path()));
    fs::write(
        out.path().join("inbox/new.md"),
        "# decision:ours\n\nOur batch committed.\n",
    )
    .unwrap();

    let result = sync(&url, &binding, out.path(), true);
    assert_success(&result);
    let stdout = String::from_utf8_lossy(&result.stdout);
    let committed = field(&stdout, "committed_snapshot");
    let final_snapshot = field(&stdout, "final_snapshot");
    assert_ne!(committed, final_snapshot);
    let units = fs::read_dir(out.path().join("units"))
        .unwrap()
        .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
        .collect::<Vec<_>>();
    assert!(
        units
            .iter()
            .any(|unit| unit.contains("Our batch committed."))
    );
    assert!(
        units
            .iter()
            .any(|unit| unit.contains("A later writer committed."))
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sync_corrects_and_forgets_validated_procedures_without_creating_one_from_inbox() {
    let (url, binding, state) = spawn_server().await;
    let corrected = seed_unit_kind(
        &state,
        &binding,
        MemoryKind::Procedural,
        "procedure:build",
        "Run the old build.",
    )
    .await;
    let forgotten = seed_unit_kind(
        &state,
        &binding,
        MemoryKind::Procedural,
        "procedure:obsolete",
        "Run nothing.",
    )
    .await;
    let out = tempfile::tempdir().unwrap();
    assert_success(&compile(&url, &binding, out.path()));
    replace_body(
        &out.path()
            .join("units")
            .join(format!("{}.md", corrected.as_uuid())),
        "Run the verified build.",
    );
    fs::remove_file(
        out.path()
            .join("units")
            .join(format!("{}.md", forgotten.as_uuid())),
    )
    .unwrap();

    let dry = sync(&url, &binding, out.path(), false);
    assert_success(&dry);
    let plan: serde_json::Value = serde_json::from_slice(&dry.stdout).unwrap();
    assert_eq!(
        plan["operations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|operation| operation["op"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["correct", "forget"]
    );
    assert_success(&sync(&url, &binding, out.path(), true));
    assert_success(&verify(out.path()));
}

fn field<'a>(line: &'a str, name: &str) -> &'a str {
    line.split_ascii_whitespace()
        .find_map(|field| field.strip_prefix(&format!("{name}=")))
        .unwrap()
}

fn replace_body(path: &Path, body: &str) {
    let text = fs::read_to_string(path).unwrap();
    let footer = text.rfind("\n\n<!-- memphant ").unwrap();
    let title = text.find("\n\n").unwrap();
    fs::write(
        path,
        format!("{}\n\n{}{}", &text[..title], body, &text[footer..]),
    )
    .unwrap();
}

async fn spawn_server() -> (
    String,
    ContextBindingResponse,
    AppState<memphant_core::InMemoryStore>,
) {
    let (binding, state) = bound_state("b2-file-plane-contract").await;
    let url = serve(memphant_server::app(state.clone())).await;
    (url, binding, state)
}

async fn spawn_counted_server(
    label: &str,
) -> (
    String,
    ContextBindingResponse,
    AppState<memphant_core::InMemoryStore>,
    Arc<AtomicUsize>,
) {
    let (binding, state) = bound_state(label).await;
    let posts = Arc::new(AtomicUsize::new(0));
    let counted = posts.clone();
    let app = memphant_server::app(state.clone()).layer(axum::middleware::from_fn(
        move |request: axum::extract::Request, next: axum::middleware::Next| {
            let counted = counted.clone();
            async move {
                if request.uri().path() == "/v1/file-sync" {
                    counted.fetch_add(1, Ordering::SeqCst);
                }
                next.run(request).await
            }
        },
    ));
    (serve(app).await, binding, state, posts)
}

async fn bound_state(
    label: &str,
) -> (
    ContextBindingResponse,
    AppState<memphant_core::InMemoryStore>,
) {
    let tenant = TenantId::from_u128(uuid::Uuid::parse_str(TENANT).unwrap().as_u128());
    let state = AppState::new_in_memory().with_dev_tenant(tenant);
    let binding = state
        .store()
        .resolve_context_binding(
            tenant,
            label.to_string(),
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
    (binding, state)
}

async fn serve(app: axum::Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{address}")
}

async fn seed_unit(
    state: &AppState<memphant_core::InMemoryStore>,
    binding: &ContextBindingResponse,
    fact_key: &str,
    body: &str,
) -> memphant_types::UnitId {
    seed_unit_kind(state, binding, MemoryKind::Semantic, fact_key, body).await
}

async fn seed_unit_kind(
    state: &AppState<memphant_core::InMemoryStore>,
    binding: &ContextBindingResponse,
    kind: MemoryKind,
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
                capture: None,
                tenant_id: tenant,
                data_subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                kind,
                state: if kind == MemoryKind::Procedural {
                    UnitState::Validated
                } else {
                    UnitState::Active
                },
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

fn compile(url: &str, binding: &ContextBindingResponse, out: &Path) -> Output {
    cli(url, binding, out, "compile", &[])
}

fn sync(url: &str, binding: &ContextBindingResponse, out: &Path, apply: bool) -> Output {
    cli(
        url,
        binding,
        out,
        "sync",
        if apply { &["--apply"] } else { &[] },
    )
}

fn sync_with_timeout(
    url: &str,
    binding: &ContextBindingResponse,
    out: &Path,
    apply: bool,
    timeout_ms: &str,
) -> Output {
    cli_with_env(
        url,
        binding,
        out,
        "sync",
        if apply { &["--apply"] } else { &[] },
        &[("MEMPHANT_HTTP_TIMEOUT_MS", timeout_ms)],
    )
}

fn cli(
    url: &str,
    binding: &ContextBindingResponse,
    out: &Path,
    verb: &str,
    tail: &[&str],
) -> Output {
    cli_with_env(url, binding, out, verb, tail, &[])
}

fn cli_with_env(
    url: &str,
    binding: &ContextBindingResponse,
    out: &Path,
    verb: &str,
    tail: &[&str],
    extra_env: &[(&str, &str)],
) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_memphant-cli"));
    command
        .env("MEMPHANT_URL", url)
        .env_remove("MEMPHANT_API_KEY")
        .envs(extra_env.iter().copied())
        .arg(verb)
        .args(["--agent-node"])
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

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
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
