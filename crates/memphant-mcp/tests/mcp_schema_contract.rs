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

fn dev_handler(store: InMemoryStore, tenant: TenantId) -> MemphantMcp {
    let service = MemoryService::new(
        Arc::new(AnyStore::Mem(store)),
        Arc::new(SystemClock),
        Arc::new(NoopEmbedding),
    );
    MemphantMcp::new(
        service,
        BoundTenant {
            tenant,
            max_trust: TrustLevel::TrustedSystem,
            subject_id: None,
            subject_generation: None,
            actor_id: None,
            scope_id: None,
            agent_node_id: None,
            dev_mode: true,
        },
    )
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
    let handler = dev_handler(store, tenant);

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
    let recall_args = json!({
        "subject_id": binding.subject_id,
        "scope_id": binding.scope_id,
        "actor_id": binding.actor_id,
        "agent_node_id": binding.agent_node_id,
        "subject_generation": binding.subject_generation,
        "query": "Where is the release region?",
    });
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
