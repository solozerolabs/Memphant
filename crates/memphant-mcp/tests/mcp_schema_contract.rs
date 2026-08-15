//! MCP contract (Task 7): the committed artifact carries camelCase
//! `inputSchema` for all seven tools; a persistent in-process rmcp session
//! completes initialize → tools/list → tools/call retain → recall without
//! closing the transport first; startup refuses to bind without a tenant.

use std::path::Path;
use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{ApiKeyRow, InMemoryStore, MemoryStore, NoopEmbedding, SystemClock};
use memphant_mcp::{BoundTenant, MemphantMcp, api_key_hash, resolve_tenant};
use memphant_runtime::AnyStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    TenantId, TrustLevel,
};
use rmcp::ServiceExt;
use rmcp::model::CallToolRequestParams;
use serde_json::{Value, json};

const TOOL_NAMES: [&str; 7] = [
    "retain", "recall", "reflect", "correct", "forget", "trace", "mark",
];

async fn recall_tool_result(handler: MemphantMcp) -> rmcp::model::CallToolResult {
    let (server_io, client_io) = tokio::io::duplex(64 * 1024);
    let server = tokio::spawn(async move {
        handler
            .serve(server_io)
            .await
            .expect("server initializes")
            .waiting()
            .await
            .expect("server runs until client disconnect")
    });
    let client = ().serve(client_io).await.expect("client initializes");
    let result = client
        .call_tool(
            CallToolRequestParams::new("recall").with_arguments(
                json!({"query": "principal recheck"})
                    .as_object()
                    .cloned()
                    .expect("args object"),
            ),
        )
        .await
        .expect("tools/call recall");
    client.cancel().await.expect("client shuts down");
    server.await.expect("server task joins");
    result
}

#[test]
fn artifact_has_camel_case_input_schema_for_all_seven_tools() {
    let generated = memphant_mcp::tools_artifact();
    let committed: Value = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../mcp/memphant.tools.v1.json"),
        )
        .expect("committed artifact readable"),
    )
    .expect("committed artifact is JSON");

    for artifact in [&generated, &committed] {
        let tools = artifact.as_array().expect("artifact is a tool array");
        let names: Vec<&str> = tools
            .iter()
            .map(|tool| tool["name"].as_str().expect("tool name"))
            .collect();
        for name in TOOL_NAMES {
            assert!(names.contains(&name), "missing tool {name}");
        }
        for tool in tools {
            let name = tool["name"].as_str().unwrap_or_default();
            assert!(
                tool.get("inputSchema").is_some_and(Value::is_object),
                "tool {name} must expose camelCase inputSchema"
            );
            assert!(
                tool.get("input_schema").is_none(),
                "tool {name} must not use snake_case input_schema"
            );
            assert!(
                tool["inputSchema"].get("properties").is_some()
                    || tool["inputSchema"].get("$ref").is_some(),
                "tool {name} inputSchema must be a real schema"
            );
        }
    }

    assert_eq!(
        generated, committed,
        "mcp/memphant.tools.v1.json is stale — regenerate via `memphant-mcp --list-tools-json`"
    );
}

#[test]
fn resources_artifact_matches_capability_and_stable_templates() {
    let generated = memphant_mcp::resources_artifact();
    let committed: Value = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../mcp/memphant.resources.v1.json"),
        )
        .expect("committed resources artifact readable"),
    )
    .expect("committed resources artifact is JSON");
    assert_eq!(
        generated, committed,
        "regenerate with `memphant-mcp --list-resources-json`"
    );
    assert_eq!(
        generated["capabilities"]["resources"],
        serde_json::json!({})
    );
    assert_eq!(
        generated["resourceTemplates"]
            .as_array()
            .expect("templates")
            .len(),
        4
    );
}

#[test]
fn public_tool_schemas_exclude_server_derived_and_engine_control_fields() {
    let tools = memphant_mcp::tools_artifact();
    let tools = tools.as_array().expect("tool array");
    for tool in tools {
        let name = tool["name"].as_str().expect("tool name");
        let schema = &tool["inputSchema"];
        let encoded = serde_json::to_string(schema).expect("schema JSON");
        for forbidden in [
            "tenant_id",
            "allowed_scope_ids",
            "edge_expansion_enabled",
            "rerank_enabled",
            "query_decomposition_enabled",
            "decay_enabled",
        ] {
            assert!(
                !encoded.contains(forbidden),
                "tool {name} exposes forbidden field {forbidden}"
            );
        }
        if name == "retain" {
            for forbidden in ["source_trust", "compiler_version"] {
                assert!(
                    !encoded.contains(forbidden),
                    "retain exposes server-derived field {forbidden}"
                );
            }
        }
    }

    for name in ["retain", "reflect", "correct", "forget", "mark"] {
        let schema = &tools
            .iter()
            .find(|tool| tool["name"] == name)
            .expect("ledger-backed mutation tool")["inputSchema"];
        assert_eq!(schema["additionalProperties"], false, "tool {name}");
        let required = schema["required"]
            .as_array()
            .expect("required fields")
            .iter()
            .map(|field| field.as_str().expect("required name"))
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(
            required,
            ["idempotency_key", "request"].into_iter().collect()
        );
        let properties = schema["properties"].as_object().expect("properties");
        assert_eq!(properties.len(), 2, "tool {name}");
        assert!(properties.contains_key("idempotency_key"));
        assert!(properties.contains_key("request"));
    }
}

#[test]
fn recall_schema_accepts_only_a_query() {
    let tools = memphant_mcp::tools_artifact();
    let recall = tools
        .as_array()
        .expect("tool array")
        .iter()
        .find(|tool| tool["name"] == "recall")
        .expect("recall tool");
    let schema = &recall["inputSchema"];
    assert_eq!(schema["additionalProperties"], false);
    assert_eq!(schema["required"], json!(["query"]));
    assert_eq!(
        schema["properties"]
            .as_object()
            .expect("properties")
            .keys()
            .collect::<Vec<_>>(),
        vec!["query"],
        "MCP derives all identity and recall controls from its principal"
    );
    assert!(
        recall["outputSchema"].is_object(),
        "recall publishes its wire schema"
    );
    assert_eq!(recall["outputSchema"]["type"], "object");
    let output = serde_json::to_string(&recall["outputSchema"]).expect("output schema JSON");
    for state in ["hit", "empty", "unavailable", "error"] {
        assert!(
            output.contains(&format!("\"{state}\"")),
            "recall output schema must declare the {state} response state"
        );
    }
    let variants = recall["outputSchema"]["oneOf"]
        .as_array()
        .expect("recall output has discriminated envelopes");
    for state in ["hit", "empty", "unavailable", "error"] {
        let variant = variants
            .iter()
            .find(|variant| variant["properties"]["state"]["const"] == state)
            .unwrap_or_else(|| panic!("missing {state} output envelope"));
        assert_eq!(variant["type"], "object", "{state} envelope root");
        assert!(
            variant["required"]
                .as_array()
                .expect("required fields")
                .iter()
                .any(|field| field == "state"),
            "{state} envelope requires its discriminant"
        );
        if matches!(state, "unavailable" | "error") {
            assert!(
                variant["required"]
                    .as_array()
                    .expect("required fields")
                    .iter()
                    .any(|field| field == "error"),
                "{state} envelope requires a typed error"
            );
        }
    }
}

#[tokio::test]
async fn persistent_session_round_trips_retain_then_recall() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = store
        .resolve_context_binding(
            tenant,
            "mcp-persistent-session".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "mcp-test-user".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "mcp-test-user".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "mcp-test-root".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "mcp-test-l0".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("seed MCP memory context");
    let key_hash = "mcp-persistent-session-key".to_string();
    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: key_hash.clone(),
        label: "persistent session".to_string(),
        max_trust: TrustLevel::TrustedSystem,
        data_subject_id: Some(binding.subject_id),
        subject_generation: Some(binding.subject_generation),
        actor_id: Some(binding.actor_id),
        scope_id: Some(binding.scope_id),
        agent_node_id: Some(binding.agent_node_id),
        // This round-trip exercises an *authorized* forget, so the key carries
        // the owner-only erasure capability. Coding-agent keys default false.
        can_forget: true,
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

    // One duplex pipe carries the whole session — nothing is closed between
    // calls, proving the stdio session is persistent, not one-shot.
    let (server_io, client_io) = tokio::io::duplex(64 * 1024);
    let server = tokio::spawn(async move {
        handler
            .serve(server_io)
            .await
            .expect("server initializes")
            .waiting()
            .await
            .expect("server runs until client disconnect")
    });

    let client = ().serve(client_io).await.expect("client initialize handshake succeeds");

    let tools = client.list_all_tools().await.expect("tools/list");
    let names: Vec<&str> = tools.iter().map(|tool| tool.name.as_ref()).collect();
    for name in TOOL_NAMES {
        assert!(names.contains(&name), "tools/list missing {name}");
    }

    let retain_args = json!({
        "idempotency_key": "mcp-retain-release-region",
        "request": {
            "subject_id": binding.subject_id,
            "scope_id": binding.scope_id,
            "actor_id": binding.actor_id,
            "agent_node_id": binding.agent_node_id,
            "subject_generation": binding.subject_generation,
            "source_ref": "mcp:test:release-region",
            "observed_at": "2026-07-15T00:00:00Z",
            "payload": { "episode": {
                "source_kind": "user",
                "body": "Release region is Taipei."
            }}
        }
    });
    let retained = client
        .call_tool(
            CallToolRequestParams::new("retain")
                .with_arguments(retain_args.as_object().cloned().expect("args object")),
        )
        .await
        .expect("tools/call retain");
    assert_ne!(retained.is_error, Some(true), "retain succeeded");
    let structured = retained
        .structured_content
        .as_ref()
        .expect("retain returns structured content");
    assert!(structured["episode_id"].is_string());
    let episode_id = structured["episode_id"].clone();

    let reflect_args = json!({
        "idempotency_key": "mcp-reflect-release-region",
        "request": {
            "subject_id": binding.subject_id,
            "scope_id": binding.scope_id,
            "actor_id": binding.actor_id,
            "agent_node_id": binding.agent_node_id,
            "subject_generation": binding.subject_generation
        }
    });
    let reflected = client
        .call_tool(
            CallToolRequestParams::new("reflect")
                .with_arguments(reflect_args.as_object().cloned().expect("args object")),
        )
        .await
        .expect("tools/call reflect");
    assert_ne!(reflected.is_error, Some(true), "reflect accepted");
    assert!(
        reflected
            .structured_content
            .as_ref()
            .is_some_and(|body| body["job_id"].is_string())
    );

    // Recall on the SAME session (stdin never closed): the degraded
    // read-your-own-writes path returns the un-reflected episode body.
    let recall_args = json!({"query": "Where is the release region?"});
    let recalled = client
        .call_tool(
            CallToolRequestParams::new("recall")
                .with_arguments(recall_args.as_object().cloned().expect("args object")),
        )
        .await
        .expect("tools/call recall");
    assert_ne!(recalled.is_error, Some(true), "recall succeeded");
    let structured = recalled
        .structured_content
        .as_ref()
        .expect("recall returns structured content");
    assert_eq!(structured["state"], "hit");
    assert_eq!(
        structured["items"][0]["body"].as_str(),
        Some("Release region is Taipei.")
    );

    let forget_args = |key: &str, subject_generation| {
        json!({
            "idempotency_key": key,
            "request": {
                "subject_id": binding.subject_id,
                "scope_id": binding.scope_id,
                "actor_id": binding.actor_id,
                "agent_node_id": binding.agent_node_id,
                "subject_generation": subject_generation,
                "selector": {
                    "memory_unit_id": null,
                    "episode_id": episode_id.clone(),
                    "resource_id": null,
                    "scope_id": binding.scope_id,
                },
                "reason": "user_request"
            }
        })
    };
    let stale = client
        .call_tool(
            CallToolRequestParams::new("forget").with_arguments(
                forget_args("mcp-forget-stale", binding.subject_generation + 1)
                    .as_object()
                    .cloned()
                    .expect("args object"),
            ),
        )
        .await
        .expect("tools/call stale forget");
    assert_eq!(stale.is_error, Some(true));

    let forgotten = client
        .call_tool(
            CallToolRequestParams::new("forget").with_arguments(
                forget_args("mcp-forget-valid", binding.subject_generation)
                    .as_object()
                    .cloned()
                    .expect("args object"),
            ),
        )
        .await
        .expect("tools/call forget");
    assert_ne!(
        forgotten.is_error,
        Some(true),
        "authorized forget succeeded"
    );

    client.cancel().await.expect("client shuts down");
    server.await.expect("server task joins");
}

#[tokio::test]
async fn bound_recall_derives_context_from_the_principal() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = store
        .resolve_context_binding(
            tenant,
            "mcp-bound-recall".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "bound-user".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "bound-user".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "bound-scope".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "bound-agent".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("seed MCP memory context");
    let key_hash = "mcp-bound-recall-key".to_string();
    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: key_hash.clone(),
        label: "bound recall".to_string(),
        max_trust: TrustLevel::TrustedSystem,
        data_subject_id: Some(binding.subject_id),
        subject_generation: Some(binding.subject_generation),
        actor_id: Some(binding.actor_id),
        scope_id: Some(binding.scope_id),
        agent_node_id: Some(binding.agent_node_id),
        can_forget: false,
        can_audit_history: false,
        revoked: false,
    });
    let handler = MemphantMcp::new(
        MemoryService::new(
            Arc::new(AnyStore::Mem(store.clone())),
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
            api_key_hash: Some(key_hash.clone()),
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
            .expect("server runs until client disconnect")
    });
    let client = ().serve(client_io).await.expect("client initializes");

    let recalled = client
        .call_tool(
            CallToolRequestParams::new("recall").with_arguments(
                json!({"query": "nothing stored in this bound scope"})
                    .as_object()
                    .cloned()
                    .expect("args object"),
            ),
        )
        .await
        .expect("tools/call recall");
    assert_ne!(recalled.is_error, Some(true), "bound recall succeeds");
    assert_eq!(
        recalled
            .structured_content
            .as_ref()
            .expect("structured recall")["state"],
        "empty"
    );

    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        key_hash,
        label: "bound recall".to_string(),
        max_trust: TrustLevel::TrustedSystem,
        data_subject_id: Some(binding.subject_id),
        subject_generation: Some(binding.subject_generation),
        actor_id: Some(binding.actor_id),
        scope_id: Some(binding.scope_id),
        agent_node_id: Some(binding.agent_node_id),
        can_forget: false,
        can_audit_history: false,
        revoked: true,
    });
    let revoked = client
        .call_tool(
            CallToolRequestParams::new("recall").with_arguments(
                json!({"query": "must not survive key revocation"})
                    .as_object()
                    .cloned()
                    .expect("args object"),
            ),
        )
        .await
        .expect("tools/call revoked recall");
    assert_eq!(revoked.is_error, Some(true));
    assert_eq!(
        revoked
            .structured_content
            .as_ref()
            .expect("structured auth error")["error"]["code"],
        "auth_required"
    );

    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: "mcp-bound-recall-key".to_string(),
        label: "tenant only".to_string(),
        max_trust: TrustLevel::TrustedSystem,
        data_subject_id: None,
        subject_generation: None,
        actor_id: None,
        scope_id: None,
        agent_node_id: None,
        can_forget: false,
        can_audit_history: false,
        revoked: false,
    });
    let partial = client
        .call_tool(
            CallToolRequestParams::new("recall").with_arguments(
                json!({"query": "must require the complete binding"})
                    .as_object()
                    .cloned()
                    .expect("args object"),
            ),
        )
        .await
        .expect("tools/call partially bound recall");
    assert_eq!(partial.is_error, Some(true));
    assert_eq!(
        partial
            .structured_content
            .as_ref()
            .expect("structured scope error")["error"]["code"],
        "scope_denied"
    );

    client.cancel().await.expect("client shuts down");
    server.await.expect("server task joins");
}

#[tokio::test]
async fn recall_tool_rechecks_the_complete_principal_and_trust_ceiling() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let first = store
        .resolve_context_binding(
            tenant,
            "mcp-principal-first".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "principal-first-subject".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "principal-first-actor".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "principal-first-scope".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "principal-first-agent".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("first context");
    let second = store
        .resolve_context_binding(
            tenant,
            "mcp-principal-second".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "principal-second-subject".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "principal-second-actor".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "principal-second-scope".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "principal-second-agent".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("second context");
    let key_id = uuid::Uuid::new_v4();
    let key_hash = "mcp-principal-recheck".to_string();
    let full_row = |binding: &memphant_types::ContextBindingResponse, max_trust| ApiKeyRow {
        id: key_id,
        tenant_id: tenant,
        key_hash: key_hash.clone(),
        label: "principal recheck".to_string(),
        max_trust,
        data_subject_id: Some(binding.subject_id),
        subject_generation: Some(binding.subject_generation),
        actor_id: Some(binding.actor_id),
        scope_id: Some(binding.scope_id),
        agent_node_id: Some(binding.agent_node_id),
        can_forget: false,
        can_audit_history: false,
        revoked: false,
    };
    let bound = |binding: &memphant_types::ContextBindingResponse, max_trust| BoundTenant {
        tenant,
        max_trust,
        subject_id: Some(binding.subject_id),
        subject_generation: Some(binding.subject_generation),
        actor_id: Some(binding.actor_id),
        scope_id: Some(binding.scope_id),
        agent_node_id: Some(binding.agent_node_id),
        api_key_id: Some(key_id),
        api_key_hash: Some(key_hash.clone()),
        dev_mode: false,
    };
    let handler = |bound| {
        MemphantMcp::new(
            MemoryService::new(
                Arc::new(AnyStore::Mem(store.clone())),
                Arc::new(SystemClock),
                Arc::new(NoopEmbedding),
            ),
            bound,
        )
    };

    store.insert_api_key(full_row(&first, TrustLevel::TrustedUser));
    assert_ne!(
        recall_tool_result(handler(bound(&first, TrustLevel::TrustedUser)))
            .await
            .is_error,
        Some(true),
        "the startup principal can recall"
    );

    let mut partial_row = full_row(&first, TrustLevel::TrustedUser);
    partial_row.data_subject_id = None;
    store.insert_api_key(partial_row);
    let mut partial_bound = bound(&first, TrustLevel::TrustedUser);
    partial_bound.subject_id = None;
    let partial = recall_tool_result(handler(partial_bound)).await;
    assert_eq!(partial.is_error, Some(true));
    let partial = partial.structured_content.expect("partial error");
    assert_eq!(partial["state"], "error");
    assert_eq!(partial["error"]["code"], "scope_denied");

    store.insert_api_key(full_row(&second, TrustLevel::TrustedUser));
    let drifted = recall_tool_result(handler(bound(&first, TrustLevel::TrustedUser))).await;
    assert_eq!(drifted.is_error, Some(true));
    assert_eq!(
        drifted.structured_content.expect("drift error")["error"]["code"],
        "scope_denied"
    );

    store.insert_api_key(full_row(&first, TrustLevel::TrustedSystem));
    let gained_trust = recall_tool_result(handler(bound(&first, TrustLevel::TrustedUser))).await;
    assert_eq!(gained_trust.is_error, Some(true));
    assert_eq!(
        gained_trust.structured_content.expect("trust error")["error"]["code"],
        "scope_denied"
    );

    store.insert_api_key(full_row(&first, TrustLevel::VerifiedTool));
    assert_ne!(
        recall_tool_result(handler(bound(&first, TrustLevel::TrustedUser)))
            .await
            .is_error,
        Some(true),
        "a lower live ceiling remains valid"
    );

    let mut replaced_key = full_row(&first, TrustLevel::VerifiedTool);
    replaced_key.key_hash = "replacement-key-hash".to_string();
    store.insert_api_key(replaced_key);
    let missing = recall_tool_result(handler(bound(&first, TrustLevel::TrustedUser))).await;
    assert_eq!(missing.is_error, Some(true));
    assert_eq!(
        missing.structured_content.expect("missing error")["error"]["code"],
        "auth_required"
    );

    let mut dev = bound(&first, TrustLevel::TrustedUser);
    dev.dev_mode = true;
    dev.api_key_hash = None;
    let dev = recall_tool_result(handler(dev)).await;
    assert_eq!(dev.is_error, Some(true));
    assert_eq!(
        dev.structured_content.expect("dev error")["error"]["code"],
        "scope_denied"
    );
}

#[tokio::test]
async fn startup_refuses_without_api_key_or_dev_tenant() {
    // NOTE: env mutation — this is the only test in the binary touching
    // these variables and the round-trip tests never read them.
    unsafe {
        std::env::remove_var("MEMPHANT_API_KEY");
        std::env::remove_var("MEMPHANT_DEV_TENANT");
    }
    let store = AnyStore::Mem(InMemoryStore::default());
    let error = resolve_tenant(&store)
        .await
        .expect_err("no key + no dev tenant must refuse to start");
    assert!(
        error.contains("refusing to start"),
        "refusal is explicit: {error}"
    );

    // A revoked key must also refuse.
    let mem = InMemoryStore::default();
    let tenant = TenantId::new();
    mem.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: api_key_hash("mk_revoked"),
        label: "test".to_string(),
        max_trust: TrustLevel::TrustedUser,
        data_subject_id: None,
        subject_generation: None,
        actor_id: None,
        scope_id: None,
        agent_node_id: None,
        can_forget: false,
        can_audit_history: false,
        revoked: true,
    });
    unsafe {
        std::env::set_var("MEMPHANT_API_KEY", "mk_revoked");
    }
    let error = resolve_tenant(&AnyStore::Mem(mem))
        .await
        .expect_err("revoked key must refuse to start");
    assert!(error.contains("revoked"), "revocation is explicit: {error}");
    unsafe {
        std::env::remove_var("MEMPHANT_API_KEY");
    }
}
