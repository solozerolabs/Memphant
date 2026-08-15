//! Task 8 (in-process real-MCP lifecycle): drive all five tools in sequence
//! over one persistent rmcp session — remember -> recall(hit) -> correct_memory
//! -> recall(successor) -> invalidate_memory -> recall(empty) -> report_memory_use
//! -> replay(idempotent). Proves the whole compact lifecycle through the actual
//! MCP wire surface, not the service layer directly.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{ApiKeyRow, InMemoryStore, MemoryStore, NoopEmbedding, SystemClock};
use memphant_mcp::{BoundTenant, MemphantMcp};
use memphant_runtime::AnyStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    TenantId, TrustLevel,
};
use rmcp::ServiceExt;
use rmcp::model::CallToolRequestParams;
use serde_json::{Value, json};

#[tokio::test]
async fn five_tool_lifecycle_over_one_persistent_session() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = store
        .resolve_context_binding(
            tenant,
            "mcp-lifecycle".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "lifecycle-user".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "lifecycle-user".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "lifecycle-root".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "lifecycle-l0".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("seed context");
    let key_hash = "mcp-lifecycle-key".to_string();
    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: key_hash.clone(),
        label: "lifecycle".to_string(),
        max_trust: TrustLevel::TrustedSystem,
        data_subject_id: Some(binding.subject_id),
        subject_generation: Some(binding.subject_generation),
        actor_id: Some(binding.actor_id),
        scope_id: Some(binding.scope_id),
        agent_node_id: Some(binding.agent_node_id),
        // A coding-agent key: no owner capabilities.
        can_forget: false,
        can_audit_history: false,
        revoked: false,
    });
    let handler = MemphantMcp::new(
        MemoryService::new(
            Arc::new(AnyStore::Mem(store)),
            Arc::new(SystemClock),
            Arc::new(NoopEmbedding),
        ),
        BoundTenant {
            tenant,
            max_trust: TrustLevel::TrustedSystem,
            subject_id: Some(binding.subject_id),
            subject_generation: Some(binding.subject_generation),
            actor_id: Some(binding.actor_id),
            scope_id: Some(binding.scope_id),
            agent_node_id: Some(binding.agent_node_id),
            api_key_id: None,
            api_key_hash: Some(key_hash),
            dev_mode: false,
        },
    );

    let (server_io, client_io) = tokio::io::duplex(64 * 1024);
    let server = tokio::spawn(async move {
        handler
            .serve(server_io)
            .await
            .expect("server initializes")
            .waiting()
            .await
            .expect("server runs")
    });
    let client = ().serve(client_io).await.expect("client initializes");

    let tool = |name: &'static str, args: Value| {
        let client = &client;
        async move {
            client
                .call_tool(
                    CallToolRequestParams::new(name)
                        .with_arguments(args.as_object().cloned().expect("args object")),
                )
                .await
                .unwrap_or_else(|error| panic!("tools/call {name}: {error}"))
        }
    };
    let source = |r: &str, at: &str| json!({"kind": "user", "ref": r, "observed_at": at});

    // 1. remember one compact procedural memory.
    let remembered = tool(
        "remember",
        json!({
            "idempotency_key": "life-remember",
            "request": {
                "kind": "procedural",
                "body": "Deploy with `make ship` after CI is green.",
                "trigger": "shipping a release",
                "verification": "the pipeline shows a green run before deploy",
                "source": source("chat:1", "2026-07-15T00:00:00Z")
            }
        }),
    )
    .await;
    assert_ne!(remembered.is_error, Some(true), "remember ok");
    let unit_id = remembered.structured_content.as_ref().expect("structured")["unit_ids"][0]
        .as_str()
        .expect("unit id")
        .to_string();

    // 2. recall serves it (compact coding lane, Active procedural).
    let recalled = tool("recall", json!({"query": "shipping a release"})).await;
    let body = recalled.structured_content.as_ref().expect("structured");
    assert_eq!(body["state"], "hit", "recall hit: {body}");
    assert!(
        body["items"][0]["body"]
            .as_str()
            .is_some_and(|b| b.contains("make ship")),
        "recall serves the remembered body: {body}"
    );

    // 3. correct_memory: a bitemporal successor with a fresh compact envelope.
    let corrected = tool(
        "correct_memory",
        json!({
            "idempotency_key": "life-correct",
            "request": {
                "memory_unit_id": unit_id,
                "body": "Deploy with `make release` after CI is green.",
                "trigger": "shipping a release",
                "verification": "the pipeline shows a green run before deploy",
                "reason": "the target was renamed",
                "source": source("chat:2", "2026-07-16T00:00:00Z")
            }
        }),
    )
    .await;
    assert_ne!(corrected.is_error, Some(true), "correct ok");

    // 4. recall now serves only the successor.
    let after_correct = tool("recall", json!({"query": "shipping a release"})).await;
    let body = after_correct
        .structured_content
        .as_ref()
        .expect("structured");
    assert_eq!(body["state"], "hit");
    assert!(
        body["items"][0]["body"]
            .as_str()
            .is_some_and(|b| b.contains("make release") && !b.contains("make ship")),
        "recall serves the corrected successor: {body}"
    );
    let successor_id = body["items"][0]["unit_id"]
        .as_str()
        .expect("successor id")
        .to_string();
    let trace_id = body["trace_id"].as_str().expect("trace id").to_string();

    // 5. report_memory_use against the trace (ranking evidence only).
    let reported = tool(
        "report_memory_use",
        json!({
            "idempotency_key": "life-report",
            "request": {
                "trace_id": trace_id,
                "outcome": "success",
                "used_ids": [successor_id]
            }
        }),
    )
    .await;
    assert_ne!(reported.is_error, Some(true), "report ok");

    // 6. invalidate_memory: the successor is archived.
    let invalidated = tool(
        "invalidate_memory",
        json!({
            "idempotency_key": "life-invalidate",
            "request": {
                "memory_unit_id": successor_id,
                "reason_kind": "stale",
                "reason": "we no longer ship this way",
                "source": source("chat:3", "2026-07-17T00:00:00Z")
            }
        }),
    )
    .await;
    assert_ne!(invalidated.is_error, Some(true), "invalidate ok");

    // 7. recall is now empty — the identity is gone.
    let after_invalidate = tool("recall", json!({"query": "shipping a release"})).await;
    assert_eq!(
        after_invalidate
            .structured_content
            .as_ref()
            .expect("structured")["state"],
        "empty",
        "invalidated identity is no longer served"
    );

    // 8. idempotent replay of remember returns the original receipt (no new unit).
    let replay = tool(
        "remember",
        json!({
            "idempotency_key": "life-remember",
            "request": {
                "kind": "procedural",
                "body": "Deploy with `make ship` after CI is green.",
                "trigger": "shipping a release",
                "verification": "the pipeline shows a green run before deploy",
                "source": source("chat:1", "2026-07-15T00:00:00Z")
            }
        }),
    )
    .await;
    assert_eq!(
        replay.structured_content.as_ref().expect("structured")["unit_ids"][0]
            .as_str()
            .expect("replay unit id"),
        unit_id,
        "idempotent replay returns the original unit"
    );

    client.cancel().await.expect("client shuts down");
    server.await.expect("server joins");
}
