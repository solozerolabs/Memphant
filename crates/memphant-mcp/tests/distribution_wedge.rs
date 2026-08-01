use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{InMemoryStore, MemoryStore, NoopEmbedding, SystemClock};
use memphant_mcp::{
    BoundTenant, MAX_MEMORY_INDEX_BYTES, MAX_MEMORY_INDEX_LINES, MAX_RESOURCE_BYTES,
    MAX_TOPIC_BYTES, MAX_VIEW_CHARACTERS, MemoryCommand, MemphantMcp, anthropic_memory_tool,
};
use memphant_runtime::AnyStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingResponse,
    ContextBindingScopeRef, MemoryKind, NewResource, ResourceAcl, ResourceKind,
    ResourceProtectedCategory, RetainEpisodeHttpRequest, RetainPayload, RetainUnitPayload,
    TenantId, TrustLevel,
};
use rmcp::model::{CallToolRequestParams, PaginatedRequestParams, ReadResourceRequestParams};
use rmcp::{ServerHandler, ServiceExt};
use sha2::{Digest, Sha256};

fn handler(
    store: InMemoryStore,
    tenant: TenantId,
    binding: &ContextBindingResponse,
) -> MemphantMcp {
    MemphantMcp::new(
        MemoryService::new(
            Arc::new(AnyStore::Mem(store)),
            Arc::new(SystemClock),
            Arc::new(NoopEmbedding),
        ),
        BoundTenant {
            tenant,
            max_trust: TrustLevel::TrustedUser,
            subject_id: Some(binding.subject_id),
            subject_generation: Some(binding.subject_generation),
            actor_id: Some(binding.actor_id),
            scope_id: Some(binding.scope_id),
            agent_node_id: Some(binding.agent_node_id),
            dev_mode: false,
        },
    )
}

async fn binding(store: &InMemoryStore, tenant: TenantId, name: &str) -> ContextBindingResponse {
    store
        .resolve_context_binding(
            tenant,
            format!("b3-{name}"),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: format!("{name}-subject"),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: format!("{name}-actor"),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: format!("{name}-scope"),
                    kind: "project".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: format!("{name}-agent"),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("bind memory context")
}

#[test]
fn ga_command_json_contract_is_exact_and_rejects_unknown_fields() {
    assert_eq!(
        anthropic_memory_tool(),
        serde_json::json!({"type": "memory_20250818", "name": "memory"})
    );
    let command: MemoryCommand = serde_json::from_value(serde_json::json!({
        "command": "view",
        "path": "/memories/notes.md",
        "view_range": [1, -1]
    }))
    .expect("GA view shape");
    assert_eq!(
        command,
        MemoryCommand::View {
            path: "/memories/notes.md".to_string(),
            view_range: Some([1, -1]),
        }
    );
    assert!(
        serde_json::from_value::<MemoryCommand>(serde_json::json!({
            "command": "view",
            "path": "/memories",
            "unexpected": true
        }))
        .is_err()
    );
    assert!(
        serde_json::from_value::<MemoryCommand>(serde_json::json!({
            "command": "copy",
            "path": "/memories/a.md"
        }))
        .is_err()
    );
}

#[tokio::test]
async fn all_six_memory_commands_use_one_governed_projection() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = binding(&store, tenant, "commands").await;
    let handler = handler(store, tenant, &binding);

    let empty = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories".to_string(),
            view_range: None,
        })
        .await
        .expect("view empty root");
    assert!(empty.contains("/memories/MEMORY.md"));

    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Create {
                path: "/memories/release.md".to_string(),
                file_text: "region: Taipei\nstatus: draft\n".to_string(),
            })
            .await
            .expect("create"),
        "File created successfully at: /memories/release.md"
    );
    let viewed = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/release.md".to_string(),
            view_range: Some([1, 1]),
        })
        .await
        .expect("view file");
    assert!(viewed.contains("     1\tregion: Taipei"));

    let replaced = handler
        .handle_memory_command(MemoryCommand::StrReplace {
            path: "/memories/release.md".to_string(),
            old_str: "draft".to_string(),
            new_str: Some("ready".to_string()),
        })
        .await
        .expect("str_replace");
    assert!(replaced.starts_with("The memory file has been edited."));
    assert!(replaced.contains("     2\tstatus: ready"));
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Insert {
                path: "/memories/release.md".to_string(),
                insert_line: 1,
                insert_text: "owner: platform\n".to_string(),
            })
            .await
            .expect("insert"),
        "The file /memories/release.md has been edited."
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Rename {
                old_path: "/memories/release.md".to_string(),
                new_path: "/memories/launch.md".to_string(),
            })
            .await
            .expect("rename"),
        "Successfully renamed /memories/release.md to /memories/launch.md"
    );

    let index_before = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/MEMORY.md".to_string(),
            view_range: None,
        })
        .await
        .expect("index");
    let body = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/launch.md".to_string(),
            view_range: None,
        })
        .await
        .expect("renamed topic");
    assert!(body.contains("owner: platform"));
    assert!(body.contains("status: ready"));
    let index_after = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/MEMORY.md".to_string(),
            view_range: None,
        })
        .await
        .expect("fixed-point index");
    assert_eq!(
        index_before, index_after,
        "read-only canonical projection is a fixed point"
    );

    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Delete {
                path: "/memories/launch.md".to_string(),
            })
            .await
            .expect("delete"),
        "Successfully deleted /memories/launch.md"
    );
    let missing = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/launch.md".to_string(),
            view_range: None,
        })
        .await
        .expect_err("deleted topic is absent");
    assert_eq!(missing.code, "not_found");

    handler
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/projects/acme/notes.txt".to_string(),
            file_text: "nested governed text".to_string(),
        })
        .await
        .expect("create nested text projection");
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Create {
                path: "/memories/projects".to_string(),
                file_text: "cannot replace an implicit directory".to_string(),
            })
            .await
            .unwrap_err()
            .code,
        "already_exists"
    );
    let directory = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/projects".to_string(),
            view_range: None,
        })
        .await
        .expect("view virtual directory");
    assert!(directory.contains("/memories/projects/acme/"));
    handler
        .handle_memory_command(MemoryCommand::Rename {
            old_path: "/memories/projects/acme".to_string(),
            new_path: "/memories/archive/acme".to_string(),
        })
        .await
        .expect("rename virtual directory");
    let nested = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/archive/acme/notes.txt".to_string(),
            view_range: None,
        })
        .await
        .expect("view renamed nested file");
    assert!(nested.contains("nested governed text"));
    handler
        .handle_memory_command(MemoryCommand::Delete {
            path: "/memories/archive".to_string(),
        })
        .await
        .expect("recursively delete virtual directory");
    assert!(
        handler
            .handle_memory_command(MemoryCommand::View {
                path: "/memories/archive/acme/notes.txt".to_string(),
                view_range: None,
            })
            .await
            .is_err()
    );
}

#[tokio::test]
async fn commands_return_typed_failures_and_enforce_bounds() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = binding(&store, tenant, "failures").await;
    let handler = handler(store, tenant, &binding);

    for command in [
        MemoryCommand::Create {
            path: "/memories/../escape.md".to_string(),
            file_text: "secret".to_string(),
        },
        MemoryCommand::Create {
            path: "/memories/huge.md".to_string(),
            file_text: "x".repeat(MAX_TOPIC_BYTES + 1),
        },
        MemoryCommand::Create {
            path: "/memories/.hidden.md".to_string(),
            file_text: "hidden".to_string(),
        },
        MemoryCommand::Create {
            path: "/memories/empty.md".to_string(),
            file_text: String::new(),
        },
        MemoryCommand::Delete {
            path: "/memories".to_string(),
        },
    ] {
        assert!(handler.handle_memory_command(command).await.is_err());
    }

    handler
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/dup.md".to_string(),
            file_text: "same same".to_string(),
        })
        .await
        .expect("seed duplicate text");
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::View {
                path: "/memories/missing.md".to_string(),
                view_range: None,
            })
            .await
            .unwrap_err()
            .code,
        "not_found"
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Create {
                path: "/memories/dup.md".to_string(),
                file_text: "replacement".to_string(),
            })
            .await
            .unwrap_err()
            .code,
        "already_exists"
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::StrReplace {
                path: "/memories/dup.md".to_string(),
                old_str: "absent".to_string(),
                new_str: None,
            })
            .await
            .unwrap_err()
            .code,
        "text_not_found"
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::StrReplace {
                path: "/memories/dup.md".to_string(),
                old_str: "same".to_string(),
                new_str: Some("new".to_string()),
            })
            .await
            .unwrap_err()
            .code,
        "ambiguous_match"
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Insert {
                path: "/memories/dup.md".to_string(),
                insert_line: 9,
                insert_text: "x".to_string(),
            })
            .await
            .unwrap_err()
            .code,
        "invalid_line"
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Rename {
                old_path: "/memories/dup.md".to_string(),
                new_path: "/memories/MEMORY.md".to_string(),
            })
            .await
            .unwrap_err()
            .code,
        "read_only_path"
    );
    handler
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/destination.md".to_string(),
            file_text: "occupied".to_string(),
        })
        .await
        .expect("seed rename destination");
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Rename {
                old_path: "/memories/dup.md".to_string(),
                new_path: "/memories/destination.md".to_string(),
            })
            .await
            .unwrap_err()
            .code,
        "already_exists"
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::Delete {
                path: "/memories/missing.md".to_string(),
            })
            .await
            .unwrap_err()
            .code,
        "not_found"
    );

    let index = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/MEMORY.md".to_string(),
            view_range: None,
        })
        .await
        .expect("bounded index");
    assert!(index.len() <= MAX_MEMORY_INDEX_BYTES);
    assert!(index.lines().count() <= MAX_MEMORY_INDEX_LINES);

    handler
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/long-line.md".to_string(),
            file_text: "é".repeat(10_000),
        })
        .await
        .expect("seed long UTF-8 line");
    let long_view = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/long-line.md".to_string(),
            view_range: None,
        })
        .await
        .expect("bounded long-line view");
    assert_eq!(long_view.matches('é').count(), 10_000);
    handler
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/long-ascii.md".to_string(),
            file_text: "x".repeat(MAX_VIEW_CHARACTERS + 1_000),
        })
        .await
        .expect("seed long text file");
    let truncated = handler
        .handle_memory_command(MemoryCommand::View {
            path: "/memories/long-ascii.md".to_string(),
            view_range: None,
        })
        .await
        .expect("default view truncates file text by characters");
    assert_eq!(truncated.matches('x').count(), MAX_VIEW_CHARACTERS);
}

#[tokio::test]
async fn idempotency_is_bound_to_the_full_context() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding_a = binding(&store, tenant, "idempotency-a").await;
    let binding_b = binding(&store, tenant, "idempotency-b").await;
    let handler_a = handler(store.clone(), tenant, &binding_a);
    let handler_b = handler(store, tenant, &binding_b);

    for handler in [&handler_a, &handler_b] {
        handler
            .handle_memory_command(MemoryCommand::Create {
                path: "/memories/same.md".to_string(),
                file_text: "identical operation in a distinct context".to_string(),
            })
            .await
            .expect("same-tenant contexts do not collide in the mutation ledger");
    }
}

#[tokio::test]
async fn canonical_projection_rejects_native_reserved_and_prefix_collisions() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let collision_binding = binding(&store, tenant, "native-path-collision").await;
    let context = store
        .resolve_memory_context(
            tenant,
            collision_binding.subject_id,
            collision_binding.actor_id,
            collision_binding.scope_id,
            collision_binding.agent_node_id,
        )
        .await
        .expect("resolve collision context");
    let service = MemoryService::new(
        Arc::new(AnyStore::Mem(store)),
        Arc::new(SystemClock),
        Arc::new(NoopEmbedding),
    );
    for (index, fact_key) in ["memory_path:folder", "memory_path:folder/item.txt"]
        .into_iter()
        .enumerate()
    {
        service
            .retain(
                &context,
                &format!("native-path-collision-{index}"),
                TrustLevel::TrustedUser,
                RetainEpisodeHttpRequest {
                    subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    actor_id: context.actor_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    source_ref: format!("b3:test:native-path-collision:{index}"),
                    observed_at: "2026-07-23T00:00:00Z".to_string(),
                    payload: RetainPayload::Unit(RetainUnitPayload {
                        kind: MemoryKind::Semantic,
                        fact_key: Some(fact_key.to_string()),
                        subject: None,
                        predicate: "memory_file".to_string(),
                        body: format!("collision {index}"),
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                        target_unit_ids: None,
                    }),
                },
            )
            .await
            .expect("native retain");
    }
    let handler = MemphantMcp::new(
        service,
        BoundTenant {
            tenant,
            max_trust: TrustLevel::TrustedUser,
            subject_id: Some(collision_binding.subject_id),
            subject_generation: Some(collision_binding.subject_generation),
            actor_id: Some(collision_binding.actor_id),
            scope_id: Some(collision_binding.scope_id),
            agent_node_id: Some(collision_binding.agent_node_id),
            dev_mode: false,
        },
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::View {
                path: "/memories".to_string(),
                view_range: None,
            })
            .await
            .unwrap_err()
            .code,
        "invalid_projection"
    );

    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let index_binding = binding(&store, tenant, "native-index-collision").await;
    let context = store
        .resolve_memory_context(
            tenant,
            index_binding.subject_id,
            index_binding.actor_id,
            index_binding.scope_id,
            index_binding.agent_node_id,
        )
        .await
        .expect("resolve index context");
    let service = MemoryService::new(
        Arc::new(AnyStore::Mem(store)),
        Arc::new(SystemClock),
        Arc::new(NoopEmbedding),
    );
    service
        .retain(
            &context,
            "native-index-collision",
            TrustLevel::TrustedUser,
            RetainEpisodeHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_ref: "b3:test:native-index-collision".to_string(),
                observed_at: "2026-07-23T00:00:00Z".to_string(),
                payload: RetainPayload::Unit(RetainUnitPayload {
                    kind: MemoryKind::Semantic,
                    fact_key: Some("memory_path:MEMORY.md".to_string()),
                    subject: None,
                    predicate: "memory_file".to_string(),
                    body: "reserved index collision".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                    target_unit_ids: None,
                }),
            },
        )
        .await
        .expect("native reserved retain");
    let handler = MemphantMcp::new(
        service,
        BoundTenant {
            tenant,
            max_trust: TrustLevel::TrustedUser,
            subject_id: Some(index_binding.subject_id),
            subject_generation: Some(index_binding.subject_generation),
            actor_id: Some(index_binding.actor_id),
            scope_id: Some(index_binding.scope_id),
            agent_node_id: Some(index_binding.agent_node_id),
            dev_mode: false,
        },
    );
    assert_eq!(
        handler
            .handle_memory_command(MemoryCommand::View {
                path: "/memories".to_string(),
                view_range: None,
            })
            .await
            .unwrap_err()
            .code,
        "invalid_projection"
    );
}

#[tokio::test]
async fn mcp_declares_paginates_and_reads_tenant_bound_resources() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = binding(&store, tenant, "resources").await;
    let context = store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve resource context");
    let mut tx = store
        .begin(&context)
        .await
        .expect("begin protected resources");
    let protected_id = store
        .stage_resource(
            &mut tx,
            NewResource {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                uri: "https://example.invalid/protected".to_string(),
                source_ref: "b3:test:protected-resource".to_string(),
                observed_at: "2026-07-23T00:00:00Z".to_string(),
                kind: ResourceKind::Document,
                content_hash: "sha256:protected".to_string(),
                mime_type: "text/plain".to_string(),
                revision: None,
                body: Some("must not be exposed".to_string()),
                source_trust: TrustLevel::TrustedUser,
                acl: ResourceAcl {
                    scopes: vec![context.scope_id],
                    trust_floor: Some(TrustLevel::VerifiedTool),
                    protected: Some(ResourceProtectedCategory::CredentialsSecrets),
                },
            },
        )
        .await
        .expect("stage protected resource");
    let binary_id = store
        .stage_resource(
            &mut tx,
            NewResource {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                uri: "https://example.invalid/image".to_string(),
                source_ref: "b3:test:binary-resource".to_string(),
                observed_at: "2026-07-23T00:00:00Z".to_string(),
                kind: ResourceKind::Document,
                content_hash: "sha256:image".to_string(),
                mime_type: "image/png".to_string(),
                revision: None,
                body: Some("not actually text".to_string()),
                source_trust: TrustLevel::TrustedUser,
                acl: ResourceAcl::default(),
            },
        )
        .await
        .expect("stage binary resource");
    store.commit(tx).await.expect("commit protected resources");
    let handler = handler(store, tenant, &binding);
    for index in 0..101 {
        handler
            .handle_memory_command(MemoryCommand::Create {
                path: format!("/memories/topic-{index:03}.md"),
                file_text: format!("body {index:03}"),
            })
            .await
            .expect("seed resource");
    }
    assert!(handler.get_info().capabilities.resources.is_some());
    assert!(handler.get_info().capabilities.tools.is_some());

    let (server_io, client_io) = tokio::io::duplex(256 * 1024);
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
    let templates = client
        .list_resource_templates(None)
        .await
        .expect("resource templates");
    assert_eq!(
        templates
            .resource_templates
            .iter()
            .map(|template| template.uri_template.as_str())
            .collect::<std::collections::BTreeSet<_>>(),
        [
            "memphant://episode/{id}",
            "memphant://memory/{id}",
            "memphant://resource/{id}",
            "memphant://trace/{id}",
        ]
        .into_iter()
        .collect()
    );
    let first = client
        .list_resources(None)
        .await
        .expect("first resource page");
    assert_eq!(first.resources.len(), 100);
    let repeated = client
        .list_resources(None)
        .await
        .expect("repeat first resource page");
    assert_eq!(
        first
            .resources
            .iter()
            .map(|resource| resource.uri.as_str())
            .collect::<Vec<_>>(),
        repeated
            .resources
            .iter()
            .map(|resource| resource.uri.as_str())
            .collect::<Vec<_>>(),
        "resource order and identifiers are deterministic"
    );
    let cursor = first.next_cursor.expect("second page cursor");
    let second = client
        .list_resources(Some(
            PaginatedRequestParams::default().with_cursor(Some(cursor)),
        ))
        .await
        .expect("second resource page");
    assert_eq!(second.resources.len(), 1);
    assert!(second.next_cursor.is_none());

    let resource = &first.resources[0];
    let read = client
        .read_resource(ReadResourceRequestParams::new(resource.uri.clone()))
        .await
        .expect("read resource");
    assert_eq!(read.contents.len(), 1);
    let encoded = serde_json::to_value(&read.contents[0]).expect("resource content JSON");
    assert_eq!(encoded["uri"], resource.uri);
    assert!(
        encoded["text"]
            .as_str()
            .is_some_and(|text| text.starts_with("body "))
    );

    let retain = |key: &str, source_ref: &str, payload: serde_json::Value| {
        serde_json::json!({
            "idempotency_key": key,
            "request": {
                "subject_id": binding.subject_id,
                "scope_id": binding.scope_id,
                "actor_id": binding.actor_id,
                "agent_node_id": binding.agent_node_id,
                "subject_generation": binding.subject_generation,
                "source_ref": source_ref,
                "observed_at": "2026-07-23T00:00:00Z",
                "payload": payload
            }
        })
    };
    let episode = client
        .call_tool(
            CallToolRequestParams::new("retain").with_arguments(
                retain(
                    "b3-episode-resource",
                    "b3:test:episode",
                    serde_json::json!({"episode": {
                        "source_kind": "user",
                        "body": "Episode resource body"
                    }}),
                )
                .as_object()
                .cloned()
                .unwrap(),
            ),
        )
        .await
        .expect("retain episode");
    let episode_id = episode
        .structured_content
        .as_ref()
        .and_then(|value| value["episode_id"].as_str())
        .expect("episode id");
    let episode_read = client
        .read_resource(ReadResourceRequestParams::new(format!(
            "memphant://episode/{episode_id}"
        )))
        .await
        .expect("read episode resource");
    assert!(
        serde_json::to_value(&episode_read.contents[0]).unwrap()["text"]
            .as_str()
            .is_some_and(|text| text == "Episode resource body")
    );

    let resource_body = "Canonical resource body";
    let resource = client
        .call_tool(
            CallToolRequestParams::new("retain").with_arguments(
                retain(
                    "b3-stored-resource",
                    "b3:test:resource",
                    serde_json::json!({"resource": {
                        "uri": "https://example.invalid/b3",
                        "mime_type": "text/plain",
                        "content_hash": format!("{:x}", Sha256::digest(resource_body.as_bytes())),
                        "body": resource_body
                    }}),
                )
                .as_object()
                .cloned()
                .unwrap(),
            ),
        )
        .await
        .expect("retain resource");
    let resource_id = resource
        .structured_content
        .as_ref()
        .and_then(|value| value["resource_id"].as_str())
        .expect("resource id");
    let resource_read = client
        .read_resource(ReadResourceRequestParams::new(format!(
            "memphant://resource/{resource_id}"
        )))
        .await
        .expect("read stored resource");
    let resource_json = serde_json::to_value(&resource_read.contents[0]).unwrap();
    assert_eq!(resource_json["mimeType"], "text/plain");
    assert_eq!(resource_json["text"], resource_body);

    assert!(
        client
            .read_resource(ReadResourceRequestParams::new(format!(
                "memphant://resource/{}",
                protected_id.as_uuid()
            )))
            .await
            .is_err(),
        "non-empty resource ACLs fail closed until the canonical evaluator exists"
    );
    assert!(
        client
            .read_resource(ReadResourceRequestParams::new(format!(
                "memphant://resource/{}",
                binary_id.as_uuid()
            )))
            .await
            .is_err(),
        "binary resources are not mislabeled as MCP text"
    );

    assert!(
        client
            .read_resource(ReadResourceRequestParams::new(
                "memphant://memory/not-a-uuid".to_string(),
            ))
            .await
            .is_err(),
        "malformed resource URIs are rejected"
    );
    let oversized_body = "x".repeat(MAX_RESOURCE_BYTES + 1);
    let oversized = client
        .call_tool(
            CallToolRequestParams::new("retain").with_arguments(
                retain(
                    "b3-oversized-resource",
                    "b3:test:oversized-resource",
                    serde_json::json!({"resource": {
                        "uri": "https://example.invalid/b3-oversized",
                        "mime_type": "text/plain",
                        "content_hash": format!("{:x}", Sha256::digest(oversized_body.as_bytes())),
                        "body": oversized_body
                    }}),
                )
                .as_object()
                .cloned()
                .unwrap(),
            ),
        )
        .await
        .expect("retain oversized resource");
    let oversized_id = oversized
        .structured_content
        .as_ref()
        .and_then(|value| value["resource_id"].as_str())
        .expect("oversized resource id");
    assert!(
        client
            .read_resource(ReadResourceRequestParams::new(format!(
                "memphant://resource/{oversized_id}"
            )))
            .await
            .is_err(),
        "oversized resource reads are rejected"
    );

    let recalled = client
        .call_tool(
            CallToolRequestParams::new("recall").with_arguments(
                serde_json::json!({
                    "subject_id": binding.subject_id,
                    "scope_id": binding.scope_id,
                    "actor_id": binding.actor_id,
                    "agent_node_id": binding.agent_node_id,
                    "subject_generation": binding.subject_generation,
                    "query": "Episode resource"
                })
                .as_object()
                .cloned()
                .unwrap(),
            ),
        )
        .await
        .expect("recall for trace");
    let trace_id = recalled
        .structured_content
        .as_ref()
        .and_then(|value| value["trace_id"].as_str())
        .expect("trace id");
    let trace_read = client
        .read_resource(ReadResourceRequestParams::new(format!(
            "memphant://trace/{trace_id}"
        )))
        .await
        .expect("read trace resource");
    let trace_json = serde_json::to_value(&trace_read.contents[0]).unwrap();
    assert_eq!(trace_json["mimeType"], "application/json");
    assert!(
        trace_json["text"]
            .as_str()
            .is_some_and(|text| text.contains(trace_id))
    );

    client.cancel().await.expect("client shuts down");
    server.await.expect("server joins");
}

#[tokio::test]
async fn projection_is_cross_tenant_isolated_and_requires_full_key_binding() {
    let store = InMemoryStore::default();
    let tenant_a = TenantId::new();
    let tenant_b = TenantId::new();
    let binding_a = binding(&store, tenant_a, "tenant-a").await;
    let binding_b = binding(&store, tenant_b, "tenant-b").await;
    let handler_a = handler(store.clone(), tenant_a, &binding_a);
    let handler_b = handler(store.clone(), tenant_b, &binding_b);
    handler_a
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/alpha.md".to_string(),
            file_text: "tenant A only".to_string(),
        })
        .await
        .expect("tenant A write");
    handler_b
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/beta.md".to_string(),
            file_text: "tenant B only".to_string(),
        })
        .await
        .expect("tenant B write");
    let root_a = handler_a
        .handle_memory_command(MemoryCommand::View {
            path: "/memories".to_string(),
            view_range: None,
        })
        .await
        .expect("tenant A list");
    assert!(root_a.contains("alpha.md"));
    assert!(!root_a.contains("beta.md"));

    let (server_io, client_io) = tokio::io::duplex(64 * 1024);
    let handler_a_for_server = handler_a.clone();
    let server = tokio::spawn(async move {
        handler_a_for_server
            .serve(server_io)
            .await
            .expect("server initializes")
            .waiting()
            .await
            .expect("server runs")
    });
    let client = ().serve(client_io).await.expect("client initializes");
    let uri_a = client
        .list_resources(None)
        .await
        .expect("tenant A resources")
        .resources[0]
        .uri
        .clone();
    client.cancel().await.expect("client shuts down");
    server.await.expect("server joins");
    assert_eq!(
        handler_b
            .read_bound_resource(&uri_a)
            .await
            .unwrap_err()
            .code,
        "not_found"
    );

    let unbound = MemphantMcp::new(
        MemoryService::new(
            Arc::new(AnyStore::Mem(store)),
            Arc::new(SystemClock),
            Arc::new(NoopEmbedding),
        ),
        BoundTenant {
            tenant: tenant_a,
            max_trust: TrustLevel::TrustedUser,
            subject_id: None,
            subject_generation: None,
            actor_id: None,
            scope_id: None,
            agent_node_id: None,
            dev_mode: false,
        },
    );
    assert_eq!(
        unbound
            .handle_memory_command(MemoryCommand::View {
                path: "/memories".to_string(),
                view_range: None,
            })
            .await
            .unwrap_err()
            .code,
        "scope_denied"
    );
}

#[tokio::test]
async fn memory_mutations_honor_the_api_key_trust_ceiling() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = binding(&store, tenant, "trust-ceiling").await;
    let context = store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve memory context");
    let service = MemoryService::new(
        Arc::new(AnyStore::Mem(store)),
        Arc::new(SystemClock),
        Arc::new(NoopEmbedding),
    );
    let handler = MemphantMcp::new(
        service.clone(),
        BoundTenant {
            tenant,
            max_trust: TrustLevel::AgentOutput,
            subject_id: Some(binding.subject_id),
            subject_generation: Some(binding.subject_generation),
            actor_id: Some(binding.actor_id),
            scope_id: Some(binding.scope_id),
            agent_node_id: Some(binding.agent_node_id),
            dev_mode: false,
        },
    );
    let denied = handler
        .handle_memory_command(MemoryCommand::Create {
            path: "/memories/ceiling.md".to_string(),
            file_text: "bounded trust".to_string(),
        })
        .await
        .expect_err("low-trust key cannot mint semantic memory");
    assert_eq!(denied.code, "mutation_failed");

    let snapshot = service
        .canonical_projection(&context)
        .await
        .expect("canonical projection");
    assert!(snapshot.items.is_empty());
}
