use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use memphant_core::service::file_sync_plan_sha256;
use memphant_core::{ApiKeyRow, MemoryStore};
use memphant_server::{AppState, api_key_hash};
use memphant_types::{
    ActorId, FileSyncOperation, FileSyncRequest, MemoryKind, NewMemoryUnit, RecallResponse,
    RetainEpisodeHttpRequest, RetainEpisodeHttpResponse, RetainEpisodePayload, RetainPayload,
    ScopeId, TenantId, TrustLevel, UnitState,
};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};
use tower::ServiceExt;

static REQUEST_ID: AtomicU64 = AtomicU64::new(1);
use uuid::Uuid;

const KEY_A: &str = "mk_tenant_a_key";
const KEY_B: &str = "mk_tenant_b_key";
const KEY_REVOKED: &str = "mk_revoked_key";
const KEY_SCOPED: &str = "mk_scoped_key";

fn tenant(value: u128) -> TenantId {
    TenantId::from_u128(value)
}

fn scope(value: u128) -> ScopeId {
    ScopeId::from_u128(value)
}

fn actor(value: u128) -> ActorId {
    ActorId::from_u128(value)
}

fn key_row(token: &str, tenant_id: TenantId, max_trust: TrustLevel, revoked: bool) -> ApiKeyRow {
    ApiKeyRow {
        id: Uuid::now_v7(),
        tenant_id,
        key_hash: api_key_hash(token),
        label: token.to_string(),
        max_trust,
        data_subject_id: None,
        subject_generation: None,
        actor_id: None,
        scope_id: None,
        agent_node_id: None,
        can_forget: false,
        can_audit_history: false,
        revoked,
    }
}

/// Two tenants, three keys (A, B, revoked-on-A).
fn authed_app(tenant_a: TenantId, tenant_b: TenantId) -> axum::Router {
    let state = AppState::new_in_memory();
    state
        .store()
        .insert_api_key(key_row(KEY_A, tenant_a, TrustLevel::TrustedUser, false));
    state
        .store()
        .insert_api_key(key_row(KEY_B, tenant_b, TrustLevel::TrustedUser, false));
    state.store().insert_api_key(key_row(
        KEY_REVOKED,
        tenant_a,
        TrustLevel::TrustedUser,
        true,
    ));
    memphant_server::app(state)
}

fn scoped_app(tenant_id: TenantId, actor_id: ActorId, scope_id: ScopeId) -> axum::Router {
    let state = AppState::new_in_memory();
    let mut row = key_row(KEY_SCOPED, tenant_id, TrustLevel::TrustedUser, false);
    row.actor_id = Some(actor_id);
    row.scope_id = Some(scope_id);
    state.store().insert_api_key(row);
    memphant_server::app(state)
}

#[tokio::test]
async fn scoped_key_cannot_claim_another_same_tenant_scope_for_current_or_history() {
    let tenant_id = tenant(80_600);
    let bound_scope = scope(80_601);
    let other_scope = scope(80_602);
    let actor_id = actor(80_603);
    let app = scoped_app(tenant_id, actor_id, bound_scope);

    for transaction_as_of in [None, Some("2026-01-01T00:00:00Z")] {
        let body = serde_json::json!({
            "subject_id": memphant_types::SubjectId::new(),
            "scope_id": other_scope,
            "actor_id": actor_id,
            "agent_node_id": memphant_types::AgentNodeId::new(),
            "subject_generation": 0,
            "query": "private fact",
            "transaction_as_of": transaction_as_of,
        });
        let (status, response) =
            send(&app, "POST", "/v1/recall", Some(KEY_SCOPED), Some(body)).await;
        assert_eq!(status, StatusCode::FORBIDDEN);
        assert_eq!(response["error"]["code"], "scope_denied");
    }
}

fn retain_body(_tenant_id: TenantId, scope_id: ScopeId, actor_id: ActorId) -> Value {
    serde_json::to_value(RetainEpisodeHttpRequest {
        subject_id: memphant_types::SubjectId::new(),
        scope_id,
        actor_id,
        agent_node_id: memphant_types::AgentNodeId::new(),
        subject_generation: 0,
        source_ref: "auth:test:retain".to_string(),
        observed_at: "2026-07-15T00:00:00Z".to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: "Auth contract fact body.".to_string(),
            subject: None,
            predicate: None,
        }),
    })
    .expect("serialize")
}

fn retain_body_from_binding(binding: &Value) -> Value {
    serde_json::json!({
        "subject_id": binding["subject_id"],
        "scope_id": binding["scope_id"],
        "actor_id": binding["actor_id"],
        "agent_node_id": binding["agent_node_id"],
        "subject_generation": binding["subject_generation"],
        "source_ref": "auth:test:binding-retain",
        "observed_at": "2026-07-15T00:00:00Z",
        "payload": { "episode": {
            "source_kind": "user",
            "body": "Auth contract fact body."
        }}
    })
}

async fn send(
    app: &axum::Router,
    method: &str,
    path: &str,
    bearer: Option<&str>,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut builder = Request::builder().method(method).uri(path);
    if let Some(token) = bearer {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    if matches!(
        path,
        "/v1/episodes"
            | "/v1/reflect"
            | "/v1/correct"
            | "/v1/forget"
            | "/v1/mark"
            | "/v1/file-sync"
    ) {
        builder = builder.header(
            "idempotency-key",
            format!("auth-test-{}", REQUEST_ID.fetch_add(1, Ordering::Relaxed)),
        );
    }
    let request = if let Some(body) = body {
        builder
            .header("content-type", "application/json")
            .body(Body::from(serde_json::to_vec(&body).expect("body")))
            .expect("request")
    } else {
        builder.body(Body::empty()).expect("request")
    };
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let bytes = response
        .into_body()
        .collect()
        .await
        .expect("body")
        .to_bytes();
    let value = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap_or(Value::Null)
    };
    (status, value)
}

fn context_binding_body() -> Value {
    serde_json::json!({
        "subject": {
            "external_ref": "syndai:user:user-123",
            "kind": "user"
        },
        "actor": {
            "external_ref": "syndai:user:user-123",
            "kind": "user"
        },
        "scope": {
            "external_ref": "syndai:user:user-123:root",
            "kind": "user_root"
        },
        "agent_node": {
            "external_ref": "syndai:user:user-123:l0"
        },
        "access_policies": []
    })
}

#[tokio::test]
async fn context_binding_is_tenant_derived_replay_safe_and_conflict_detecting() {
    let tenant_a = tenant(79_000);
    let app = authed_app(tenant_a, tenant(79_001));
    let path = "/v1/context-bindings/syndai%3Auser%3Auser-123%3Al0";

    let (created_status, created) =
        send(&app, "PUT", path, Some(KEY_A), Some(context_binding_body())).await;
    assert_eq!(created_status, StatusCode::OK, "{created}");
    assert!(created.get("tenant_id").is_none());
    assert_eq!(created["subject_generation"], 0);
    assert_eq!(created["agent_level"], 0);
    assert!(created["subject_id"].is_string());
    assert!(created["actor_id"].is_string());
    assert!(created["scope_id"].is_string());
    assert!(created["agent_node_id"].is_string());
    assert!(created["policy_revision"].is_string());

    let (replay_status, replay) =
        send(&app, "PUT", path, Some(KEY_A), Some(context_binding_body())).await;
    assert_eq!(replay_status, StatusCode::OK);
    assert_eq!(replay, created, "identical replay must be stable");

    let mut source = context_binding_body();
    source["agent_node"] = serde_json::json!({
        "external_ref": "syndai:user:user-123:l1-source",
        "parent_external_ref": "syndai:user:user-123:l0"
    });
    let (source_status, _) = send(
        &app,
        "PUT",
        "/v1/context-bindings/l1-source",
        Some(KEY_A),
        Some(source),
    )
    .await;
    assert_eq!(source_status, StatusCode::OK);

    let policies = serde_json::json!([
        { "mode": "grant", "source_scope_external_ref": "syndai:user:user-123:root", "source_agent_node_external_ref": "syndai:user:user-123:l1-source", "kind": "semantic" },
        { "mode": "grant", "source_scope_external_ref": "syndai:user:user-123:root", "source_agent_node_external_ref": "syndai:user:user-123:l1-source", "kind": "episodic" }
    ]);
    let mut with_policies = context_binding_body();
    with_policies["access_policies"] = policies.clone();
    let (policy_status, policy_response) =
        send(&app, "PUT", path, Some(KEY_A), Some(with_policies)).await;
    assert_eq!(policy_status, StatusCode::OK);

    let mut reordered = context_binding_body();
    reordered["access_policies"] = serde_json::json!([policies[1], policies[0]]);
    let (reordered_status, reordered_response) =
        send(&app, "PUT", path, Some(KEY_A), Some(reordered)).await;
    assert_eq!(reordered_status, StatusCode::OK);
    assert_eq!(
        reordered_response, policy_response,
        "policy order is not semantic"
    );

    let mut policy_update = context_binding_body();
    policy_update["access_policies"] = serde_json::json!([{
        "mode": "grant",
        "source_scope_external_ref": "syndai:user:user-123:root",
        "source_agent_node_external_ref": "syndai:user:user-123:l1-source",
        "kind": "resource"
    }]);
    let (update_status, updated) = send(&app, "PUT", path, Some(KEY_A), Some(policy_update)).await;
    assert_eq!(update_status, StatusCode::OK);
    assert_eq!(updated["subject_id"], created["subject_id"]);
    assert_eq!(updated["actor_id"], created["actor_id"]);
    assert_eq!(updated["scope_id"], created["scope_id"]);
    assert_eq!(updated["agent_node_id"], created["agent_node_id"]);
    assert_ne!(
        updated["policy_revision"],
        policy_response["policy_revision"]
    );

    let (alias_status, alias_body) = send(
        &app,
        "PUT",
        "/v1/context-bindings/alias-for-same-identity",
        Some(KEY_A),
        Some(context_binding_body()),
    )
    .await;
    assert_eq!(alias_status, StatusCode::CONFLICT);
    assert_eq!(alias_body["error"]["code"], "context_binding_conflict");

    let mut conflicting = context_binding_body();
    conflicting["scope"]["kind"] = Value::String("agent_workspace".to_string());
    let (conflict_status, conflict) = send(&app, "PUT", path, Some(KEY_A), Some(conflicting)).await;
    assert_eq!(conflict_status, StatusCode::CONFLICT);
    assert_eq!(conflict["error"]["code"], "context_binding_conflict");
}

#[tokio::test]
async fn scoped_key_cannot_create_context_bindings() {
    let app = scoped_app(tenant(79_100), actor(79_101), scope(79_102));
    let (status, body) = send(
        &app,
        "PUT",
        "/v1/context-bindings/scoped-key-attempt",
        Some(KEY_SCOPED),
        Some(context_binding_body()),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"]["code"], "scope_denied");
}

#[tokio::test]
async fn child_and_shared_scope_bindings_reuse_subject_actor_and_scope_identity() {
    let app = authed_app(tenant(79_150), tenant(79_151));
    let (_, root) = send(
        &app,
        "PUT",
        "/v1/context-bindings/root",
        Some(KEY_A),
        Some(context_binding_body()),
    )
    .await;

    let mut child_body = context_binding_body();
    child_body["scope"] = serde_json::json!({
        "external_ref": "syndai:user:user-123:workspace",
        "kind": "agent_workspace",
        "parent_external_ref": "syndai:user:user-123:root"
    });
    child_body["agent_node"] = serde_json::json!({
        "external_ref": "syndai:user:user-123:l1-a",
        "parent_external_ref": "syndai:user:user-123:l0"
    });
    child_body["access_policies"] = serde_json::json!([]);
    let (child_status, child) = send(
        &app,
        "PUT",
        "/v1/context-bindings/l1-a",
        Some(KEY_A),
        Some(child_body.clone()),
    )
    .await;
    assert_eq!(child_status, StatusCode::OK, "{child}");
    assert_eq!(child["subject_id"], root["subject_id"]);
    assert_eq!(child["actor_id"], root["actor_id"]);
    assert_ne!(child["scope_id"], root["scope_id"]);
    assert_eq!(child["agent_level"], 1);

    child_body["agent_node"]["external_ref"] =
        Value::String("syndai:user:user-123:l1-b".to_string());
    let (sibling_status, sibling) = send(
        &app,
        "PUT",
        "/v1/context-bindings/l1-b",
        Some(KEY_A),
        Some(child_body),
    )
    .await;
    assert_eq!(sibling_status, StatusCode::OK, "{sibling}");
    assert_eq!(sibling["subject_id"], root["subject_id"]);
    assert_eq!(sibling["scope_id"], child["scope_id"]);
    assert_ne!(sibling["agent_node_id"], child["agent_node_id"]);
}

#[tokio::test]
async fn context_binding_rejects_client_controlled_identity_and_policy_fields() {
    let app = authed_app(tenant(79_200), tenant(79_201));
    let forbidden = [
        ("tenant_id", serde_json::json!(tenant(79_200))),
        ("subject_id", serde_json::json!(Uuid::now_v7())),
        ("allowed_scope_ids", serde_json::json!([])),
        (
            "inherit_to_descendants",
            serde_json::json!([{"kind": "semantic", "max_agent_level": 0}]),
        ),
    ];
    for (field, value) in forbidden {
        let mut body = context_binding_body();
        body[field] = value;
        let (status, _) = send(
            &app,
            "PUT",
            "/v1/context-bindings/forbidden-root-field",
            Some(KEY_A),
            Some(body),
        )
        .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "field {field}");
    }

    let nested_forbidden = [
        ("actor", "trust_level", serde_json::json!("trusted_system")),
        ("scope", "id", serde_json::json!(Uuid::now_v7())),
        ("agent_node", "level", serde_json::json!(0)),
    ];
    for (section, field, value) in nested_forbidden {
        let mut body = context_binding_body();
        body[section][field] = value;
        let (status, _) = send(
            &app,
            "PUT",
            "/v1/context-bindings/forbidden-nested-field",
            Some(KEY_A),
            Some(body),
        )
        .await;
        assert_eq!(
            status,
            StatusCode::UNPROCESSABLE_ENTITY,
            "field {section}.{field}"
        );
    }

    let mut duplicate_policy = context_binding_body();
    duplicate_policy["access_policies"] = serde_json::json!([
        { "mode": "inherit", "source_scope_external_ref": "scope:source", "source_agent_node_external_ref": "agent:source", "kind": "semantic" },
        { "mode": "grant", "source_scope_external_ref": "scope:source", "source_agent_node_external_ref": "agent:source", "kind": "semantic" }
    ]);
    let (status, body) = send(
        &app,
        "PUT",
        "/v1/context-bindings/duplicate-policy",
        Some(KEY_A),
        Some(duplicate_policy),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "invalid_request");
}

#[tokio::test]
async fn retain_derives_tenant_and_rejects_cross_subject_context_composition() {
    let app = authed_app(tenant(79_300), tenant(79_301));
    let (_, subject_a) = send(
        &app,
        "PUT",
        "/v1/context-bindings/user-a",
        Some(KEY_A),
        Some(context_binding_body()),
    )
    .await;
    let mut subject_b_body = context_binding_body();
    subject_b_body["subject"]["external_ref"] = Value::String("syndai:user:user-456".to_string());
    subject_b_body["actor"]["external_ref"] = Value::String("syndai:user:user-456".to_string());
    subject_b_body["scope"]["external_ref"] =
        Value::String("syndai:user:user-456:root".to_string());
    subject_b_body["agent_node"]["external_ref"] =
        Value::String("syndai:user:user-456:l0".to_string());
    let (_, subject_b) = send(
        &app,
        "PUT",
        "/v1/context-bindings/user-b",
        Some(KEY_A),
        Some(subject_b_body),
    )
    .await;

    let retain = serde_json::json!({
        "subject_id": subject_a["subject_id"],
        "scope_id": subject_a["scope_id"],
        "actor_id": subject_a["actor_id"],
        "agent_node_id": subject_a["agent_node_id"],
        "subject_generation": subject_a["subject_generation"],
        "source_ref": "auth:subject-a:episode",
        "observed_at": "2026-07-15T00:00:00Z",
        "payload": { "episode": {
            "source_kind": "user",
            "body": "Subject A prefers green."
        }}
    });
    let (status, response) = send(
        &app,
        "POST",
        "/v1/episodes",
        Some(KEY_A),
        Some(retain.clone()),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{response}");
    assert!(response["episode_id"].is_string());

    let mut crossed = retain;
    crossed["scope_id"] = subject_b["scope_id"].clone();
    crossed["agent_node_id"] = subject_b["agent_node_id"].clone();
    for payload in [
        crossed.clone(),
        serde_json::json!({
            "subject_id": subject_a["subject_id"],
            "scope_id": subject_b["scope_id"],
            "actor_id": subject_a["actor_id"],
            "agent_node_id": subject_b["agent_node_id"],
            "subject_generation": subject_a["subject_generation"],
            "source_ref": "auth:subject-a:resource",
            "observed_at": "2026-07-15T00:00:00Z",
            "payload": { "resource": {
                "uri": "syndai://memory/file-1",
                "mime_type": "text/plain",
                "content_hash": "sha256:resource"
            }}
        }),
        serde_json::json!({
            "subject_id": subject_a["subject_id"],
            "scope_id": subject_b["scope_id"],
            "actor_id": subject_a["actor_id"],
            "agent_node_id": subject_b["agent_node_id"],
            "subject_generation": subject_a["subject_generation"],
            "source_ref": "auth:subject-a:unit",
            "observed_at": "2026-07-15T00:00:00Z",
            "payload": { "unit": {
                "kind": "semantic",
                "fact_key": "preference",
                "predicate": "color",
                "body": "green",
                "confidence": 0.9,
                "valid_from": null,
                "valid_to": null
            }}
        }),
    ] {
        let (crossed_status, crossed_body) =
            send(&app, "POST", "/v1/episodes", Some(KEY_A), Some(payload)).await;
        assert_eq!(crossed_status, StatusCode::FORBIDDEN);
        assert_eq!(crossed_body["error"]["code"], "scope_denied");
    }

    let mut stale = crossed;
    stale["scope_id"] = subject_a["scope_id"].clone();
    stale["agent_node_id"] = subject_a["agent_node_id"].clone();
    stale["subject_generation"] = serde_json::json!(1);
    let (stale_status, stale_body) =
        send(&app, "POST", "/v1/episodes", Some(KEY_A), Some(stale)).await;
    assert_eq!(stale_status, StatusCode::CONFLICT);
    assert_eq!(stale_body["error"]["code"], "context_binding_conflict");
}

#[tokio::test]
async fn missing_key_yields_401_with_error_envelope() {
    let tenant_a = tenant(80_000);
    let app = authed_app(tenant_a, tenant(80_001));

    let (status, body) = send(
        &app,
        "POST",
        "/v1/episodes",
        None,
        Some(retain_body(tenant_a, scope(80_002), actor(80_003))),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "auth_required");
    assert!(body["error"]["message"].is_string());
    assert!(body["error"]["request_id"].is_string());
}

#[tokio::test]
async fn invalid_key_yields_401() {
    let tenant_a = tenant(80_100);
    let app = authed_app(tenant_a, tenant(80_101));

    let (status, body) = send(
        &app,
        "POST",
        "/v1/episodes",
        Some("mk_not_a_real_key"),
        Some(retain_body(tenant_a, scope(80_102), actor(80_103))),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "auth_required");
}

#[tokio::test]
async fn revoked_key_yields_401() {
    let tenant_a = tenant(80_200);
    let app = authed_app(tenant_a, tenant(80_201));

    let (status, body) = send(
        &app,
        "POST",
        "/v1/episodes",
        Some(KEY_REVOKED),
        Some(retain_body(tenant_a, scope(80_202), actor(80_203))),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "auth_required");
}

#[tokio::test]
async fn tenant_id_is_rejected_from_the_retain_body() {
    let tenant_a = tenant(80_300);
    let tenant_b = tenant(80_301);
    let app = authed_app(tenant_a, tenant_b);

    let mut request = retain_body(tenant_b, scope(80_302), actor(80_303));
    request["tenant_id"] = serde_json::json!(tenant_b);
    let (status, body) = send(&app, "POST", "/v1/episodes", Some(KEY_A), Some(request)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "invalid_request");
}

#[tokio::test]
async fn retain_rejects_caller_controlled_trust_and_compiler_metadata() {
    let tenant_a = tenant(80_310);
    let app = authed_app(tenant_a, tenant(80_311));
    let (_, binding) = send(
        &app,
        "PUT",
        "/v1/context-bindings/strict-retain-metadata",
        Some(KEY_A),
        Some(context_binding_body()),
    )
    .await;

    for (field, value) in [
        ("source_trust", serde_json::json!("trusted_system")),
        ("compiler_version", serde_json::json!("caller-controlled")),
    ] {
        let mut request = retain_body_from_binding(&binding);
        request[field] = value;
        let (status, body) = send(&app, "POST", "/v1/episodes", Some(KEY_A), Some(request)).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "field {field}");
        assert_eq!(body["error"]["code"], "invalid_request");
        assert!(
            body["error"]["message"]
                .as_str()
                .is_some_and(|message| message.contains(field))
        );
    }
}

#[tokio::test]
async fn tenant_b_key_cannot_fetch_tenant_a_trace() {
    let tenant_a = tenant(80_400);
    let tenant_b = tenant(80_401);
    let app = authed_app(tenant_a, tenant_b);

    let (_, binding) = send(
        &app,
        "PUT",
        "/v1/context-bindings/trace-owner",
        Some(KEY_A),
        Some(context_binding_body()),
    )
    .await;

    let (status, _) = send(
        &app,
        "POST",
        "/v1/episodes",
        Some(KEY_A),
        Some(retain_body_from_binding(&binding)),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let recall_body = serde_json::json!({
        "subject_id": binding["subject_id"],
        "scope_id": binding["scope_id"],
        "actor_id": binding["actor_id"],
        "agent_node_id": binding["agent_node_id"],
        "subject_generation": binding["subject_generation"],
        "query": "auth fact"
    });
    let (status, recalled) = send(&app, "POST", "/v1/recall", Some(KEY_A), Some(recall_body)).await;
    assert_eq!(status, StatusCode::OK);
    let recalled: RecallResponse = serde_json::from_value(recalled).expect("recall response");
    let trace_path = format!(
        "/v1/traces/{}?subject_id={}&scope_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
        recalled.trace_id.as_uuid(),
        binding["subject_id"].as_str().unwrap(),
        binding["scope_id"].as_str().unwrap(),
        binding["actor_id"].as_str().unwrap(),
        binding["agent_node_id"].as_str().unwrap(),
        binding["subject_generation"].as_u64().unwrap(),
    );

    let (own_status, _) = send(&app, "GET", &trace_path, Some(KEY_A), None).await;
    assert_eq!(own_status, StatusCode::OK, "owner tenant sees its trace");

    let (_, binding_b) = send(
        &app,
        "PUT",
        "/v1/context-bindings/trace-other-subject",
        Some(KEY_B),
        Some(context_binding_body()),
    )
    .await;
    let cross_trace_path = format!(
        "/v1/traces/{}?subject_id={}&scope_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
        recalled.trace_id.as_uuid(),
        binding_b["subject_id"].as_str().unwrap(),
        binding_b["scope_id"].as_str().unwrap(),
        binding_b["actor_id"].as_str().unwrap(),
        binding_b["agent_node_id"].as_str().unwrap(),
        binding_b["subject_generation"].as_u64().unwrap(),
    );
    let (cross_status, cross_body) = send(&app, "GET", &cross_trace_path, Some(KEY_B), None).await;
    assert_eq!(cross_status, StatusCode::NOT_FOUND);
    assert_eq!(cross_body["error"]["code"], "not_found");

    let scope_path = |context: &Value| {
        format!(
            "/v1/scopes/{}/memory?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
            binding["scope_id"].as_str().unwrap(),
            context["subject_id"].as_str().unwrap(),
            context["actor_id"].as_str().unwrap(),
            context["agent_node_id"].as_str().unwrap(),
            context["subject_generation"].as_u64().unwrap(),
        )
    };
    let (own_scope_status, _) = send(&app, "GET", &scope_path(&binding), Some(KEY_A), None).await;
    assert_eq!(own_scope_status, StatusCode::OK);
    let (cross_scope_status, cross_scope) =
        send(&app, "GET", &scope_path(&binding_b), Some(KEY_B), None).await;
    assert_eq!(cross_scope_status, StatusCode::FORBIDDEN);
    assert_eq!(cross_scope["error"]["code"], "scope_denied");

    let (unknown_status, unknown) = send(
        &app,
        "GET",
        &format!("{}&unexpected=true", scope_path(&binding)),
        Some(KEY_A),
        None,
    )
    .await;
    assert_eq!(unknown_status, StatusCode::BAD_REQUEST);
    assert!(unknown.is_null());
}

#[tokio::test]
async fn assigned_trust_comes_from_the_bound_actor_and_key_ceiling() {
    let tenant_a = tenant(80_500);
    let app = authed_app(tenant_a, tenant(80_501));

    let (_, binding) = send(
        &app,
        "PUT",
        "/v1/context-bindings/trust-clamp",
        Some(KEY_A),
        Some(context_binding_body()),
    )
    .await;

    let (status, retained) = send(
        &app,
        "POST",
        "/v1/episodes",
        Some(KEY_A),
        Some(retain_body_from_binding(&binding)),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let retained: RetainEpisodeHttpResponse =
        serde_json::from_value(retained).expect("retain response");
    assert_eq!(
        retained.assigned_trust,
        Some(TrustLevel::TrustedUser),
        "the bound user actor is trusted_user and cannot exceed the key ceiling"
    );
}

#[tokio::test]
async fn file_sync_retain_cannot_exceed_the_api_key_trust_ceiling() {
    const LOW_TRUST_KEY: &str = "mk_low_trust_file_sync";
    let tenant_id = tenant(80_550);
    let state = AppState::new_in_memory();
    state.store().insert_api_key(key_row(
        LOW_TRUST_KEY,
        tenant_id,
        TrustLevel::AgentOutput,
        false,
    ));
    let store = state.store().clone();
    let app = memphant_server::app(state);
    let (_, binding) = send(
        &app,
        "PUT",
        "/v1/context-bindings/low-trust-file-sync",
        Some(LOW_TRUST_KEY),
        Some(context_binding_body()),
    )
    .await;
    let projection_path = format!(
        "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
        binding["scope_id"].as_str().unwrap(),
        binding["subject_id"].as_str().unwrap(),
        binding["actor_id"].as_str().unwrap(),
        binding["agent_node_id"].as_str().unwrap(),
        binding["subject_generation"].as_u64().unwrap(),
    );
    let (projection_status, projection) =
        send(&app, "GET", &projection_path, Some(LOW_TRUST_KEY), None).await;
    assert_eq!(projection_status, StatusCode::OK, "{projection}");
    let operation = FileSyncOperation::Retain {
        fact_key: "profile:trust-ceiling".to_string(),
        predicate: "states".to_string(),
        body: "A low-trust key must not mint trusted direct memory.".to_string(),
        confidence: 1.0,
        valid_from: None,
        valid_to: None,
    };
    let request = serde_json::to_value(FileSyncRequest {
        subject_id: serde_json::from_value(binding["subject_id"].clone()).unwrap(),
        scope_id: serde_json::from_value(binding["scope_id"].clone()).unwrap(),
        actor_id: serde_json::from_value(binding["actor_id"].clone()).unwrap(),
        agent_node_id: serde_json::from_value(binding["agent_node_id"].clone()).unwrap(),
        subject_generation: binding["subject_generation"].as_u64().unwrap(),
        base_fingerprint: projection["fingerprint"].as_str().unwrap().to_string(),
        plan_sha256: file_sync_plan_sha256(std::slice::from_ref(&operation)).unwrap(),
        observed_at: "2026-07-22T00:00:00Z".to_string(),
        operations: vec![operation],
    })
    .unwrap();

    let (status, response) = send(
        &app,
        "POST",
        "/v1/file-sync",
        Some(LOW_TRUST_KEY),
        Some(request),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{response}");
    assert_eq!(response["error"]["code"], "sync_invalid");
    assert!(store.memory_units(tenant_id).is_empty());
}

#[tokio::test]
async fn level_one_file_sync_cannot_read_or_write_semantic_memory() {
    let tenant_id = tenant(80_575);
    let state = AppState::new_in_memory();
    state
        .store()
        .insert_api_key(key_row(KEY_A, tenant_id, TrustLevel::TrustedUser, false));
    let store = state.store().clone();
    let app = memphant_server::app(state);

    let (_, root) = send(
        &app,
        "PUT",
        "/v1/context-bindings/file-sync-l0",
        Some(KEY_A),
        Some(context_binding_body()),
    )
    .await;
    let mut child_body = context_binding_body();
    child_body["scope"] = serde_json::json!({
        "external_ref": "syndai:user:user-123:file-sync-workspace",
        "kind": "agent_workspace",
        "parent_external_ref": "syndai:user:user-123:root"
    });
    child_body["agent_node"] = serde_json::json!({
        "external_ref": "syndai:user:user-123:file-sync-l1",
        "parent_external_ref": "syndai:user:user-123:l0"
    });
    let (child_status, child) = send(
        &app,
        "PUT",
        "/v1/context-bindings/file-sync-l1",
        Some(KEY_A),
        Some(child_body),
    )
    .await;
    assert_eq!(child_status, StatusCode::OK, "{child}");
    assert_eq!(child["subject_id"], root["subject_id"]);
    assert_eq!(child["agent_level"], 1);

    let context = store
        .resolve_memory_context(
            tenant_id,
            serde_json::from_value(child["subject_id"].clone()).unwrap(),
            serde_json::from_value(child["actor_id"].clone()).unwrap(),
            serde_json::from_value(child["scope_id"].clone()).unwrap(),
            serde_json::from_value(child["agent_node_id"].clone()).unwrap(),
        )
        .await
        .expect("resolved level-one context");
    let mut tx = store.begin(&context).await.expect("begin seed transaction");
    store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("profile:hidden-at-l1".to_string()),
                predicate: Some("states".to_string()),
                body: "A pre-existing semantic row must remain hidden at level one.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("direct".to_string()),
                source_ref: "auth:test:l1-hidden".to_string(),
                observed_at: "2026-07-22T00:00:00Z".to_string(),
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
        .expect("seed legacy semantic row");
    store.commit(tx).await.expect("commit seed transaction");

    let projection_path = format!(
        "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
        child["scope_id"].as_str().unwrap(),
        child["subject_id"].as_str().unwrap(),
        child["actor_id"].as_str().unwrap(),
        child["agent_node_id"].as_str().unwrap(),
        child["subject_generation"].as_u64().unwrap(),
    );
    let (projection_status, projection) =
        send(&app, "GET", &projection_path, Some(KEY_A), None).await;
    assert_eq!(projection_status, StatusCode::OK, "{projection}");
    assert_eq!(projection["items"], serde_json::json!([]));

    let operation = FileSyncOperation::Retain {
        fact_key: "profile:blocked-at-l1".to_string(),
        predicate: "states".to_string(),
        body: "Level one must not create semantic memory.".to_string(),
        confidence: 1.0,
        valid_from: None,
        valid_to: None,
    };
    let request = serde_json::to_value(FileSyncRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        base_fingerprint: projection["fingerprint"].as_str().unwrap().to_string(),
        plan_sha256: file_sync_plan_sha256(std::slice::from_ref(&operation)).unwrap(),
        observed_at: "2026-07-22T00:00:00Z".to_string(),
        operations: vec![operation],
    })
    .unwrap();
    let (status, response) = send(&app, "POST", "/v1/file-sync", Some(KEY_A), Some(request)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{response}");
    assert_eq!(response["error"]["code"], "sync_invalid");
    assert_eq!(store.memory_units(tenant_id).len(), 1);
}

#[tokio::test]
async fn dev_mode_retain_still_requires_a_resolved_context() {
    let dev_tenant = tenant(80_600);
    let other_tenant = tenant(80_601);
    let scope_id = scope(80_602);
    let actor_id = actor(80_603);
    let state = AppState::new_in_memory().with_dev_tenant(dev_tenant);
    let store = state.store().clone();
    let app = memphant_server::app(state);

    // Relaxed development authentication must not relax memory isolation.
    let (status, response) = send(
        &app,
        "POST",
        "/v1/episodes",
        None,
        Some(retain_body(other_tenant, scope_id, actor_id)),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(response["error"]["code"], "scope_denied");
    assert!(store.episodes(dev_tenant).is_empty());
    assert!(store.episodes(other_tenant).is_empty());
}

#[tokio::test]
async fn dev_mode_historical_recall_still_requires_a_resolved_context() {
    let dev_tenant = tenant(80_700);
    let app = memphant_server::app(AppState::new_in_memory().with_dev_tenant(dev_tenant));
    let (status, response) = send(
        &app,
        "POST",
        "/v1/recall",
        None,
        Some(serde_json::json!({
            "subject_id": memphant_types::SubjectId::new(),
            "scope_id": ScopeId::new(),
            "actor_id": ActorId::new(),
            "agent_node_id": memphant_types::AgentNodeId::new(),
            "subject_generation": 0,
            "query": "historical private fact",
            "transaction_as_of": "2026-01-01T00:00:00Z"
        })),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(response["error"]["code"], "scope_denied");
}

#[tokio::test]
async fn dev_mode_correction_and_forgetting_still_require_a_resolved_context() {
    let app = memphant_server::app(AppState::new_in_memory().with_dev_tenant(tenant(80_800)));
    let subject_id = memphant_types::SubjectId::new();
    let scope_id = ScopeId::new();
    let actor_id = ActorId::new();
    let agent_node_id = memphant_types::AgentNodeId::new();
    let unit_id = memphant_types::UnitId::new();
    let requests = [
        (
            "/v1/correct",
            serde_json::json!({
                "subject_id": subject_id,
                "scope_id": scope_id,
                "actor_id": actor_id,
                "agent_node_id": agent_node_id,
                "subject_generation": 0,
                "selector": { "memory_unit_id": unit_id },
                "correction": {
                    "value": "replacement",
                    "reason": "test",
                    "source_ref": "auth:test:correction",
                    "observed_at": "2026-07-15T00:00:00Z",
                    "valid_from": null,
                    "valid_to": null
                }
            }),
        ),
        (
            "/v1/forget",
            serde_json::json!({
                "subject_id": subject_id,
                "scope_id": scope_id,
                "actor_id": actor_id,
                "agent_node_id": agent_node_id,
                "subject_generation": 0,
                "selector": {
                    "memory_unit_id": unit_id,
                    "episode_id": null,
                    "resource_id": null,
                    "scope_id": scope_id
                },
                "reason": "test"
            }),
        ),
    ];

    for (path, body) in requests {
        let (status, response) = send(&app, "POST", path, None, Some(body)).await;
        assert_eq!(status, StatusCode::FORBIDDEN, "{path}");
        assert_eq!(response["error"]["code"], "scope_denied", "{path}");
    }
}

/// A scoped app whose single key carries the given capabilities. Coding-agent
/// keys default both false; owner keys are minted with them true.
fn capability_app(
    tenant_id: TenantId,
    actor_id: ActorId,
    scope_id: ScopeId,
    can_forget: bool,
    can_audit_history: bool,
) -> axum::Router {
    let state = AppState::new_in_memory();
    let mut row = key_row(KEY_SCOPED, tenant_id, TrustLevel::TrustedUser, false);
    row.actor_id = Some(actor_id);
    row.scope_id = Some(scope_id);
    row.can_forget = can_forget;
    row.can_audit_history = can_audit_history;
    state.store().insert_api_key(row);
    memphant_server::app(state)
}

fn forget_body(
    subject_id: memphant_types::SubjectId,
    scope_id: ScopeId,
    actor_id: ActorId,
    agent_node_id: memphant_types::AgentNodeId,
) -> Value {
    serde_json::json!({
        "subject_id": subject_id,
        "scope_id": scope_id,
        "actor_id": actor_id,
        "agent_node_id": agent_node_id,
        "subject_generation": 0,
        "selector": {
            "memory_unit_id": memphant_types::UnitId::new(),
            "episode_id": null,
            "resource_id": null,
            "scope_id": scope_id
        },
        "reason": "capability test"
    })
}

/// An ordinary coding-agent key (`can_forget` default false) is refused at the
/// forget boundary before any context resolution, even acting inside its own
/// bound scope. The absence of an MCP delete tool is a convenience; this
/// capability check is the boundary.
#[tokio::test]
async fn agent_key_without_can_forget_is_refused_at_the_forget_boundary() {
    let tenant_id = tenant(80_900);
    let scope_id = scope(80_901);
    let actor_id = actor(80_902);
    let subject_id = memphant_types::SubjectId::new();
    let agent_node_id = memphant_types::AgentNodeId::new();

    // Coding-agent key: both capabilities false.
    let app = capability_app(tenant_id, actor_id, scope_id, false, false);
    let (status, response) = send(
        &app,
        "POST",
        "/v1/forget",
        Some(KEY_SCOPED),
        Some(forget_body(subject_id, scope_id, actor_id, agent_node_id)),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(response["error"]["code"], "capability_denied");

    // Contrast: an owner key WITH can_forget clears the capability gate — it
    // fails later on context resolution (scope_denied), never on capability.
    let owner_app = capability_app(tenant_id, actor_id, scope_id, true, false);
    let (owner_status, owner_response) = send(
        &owner_app,
        "POST",
        "/v1/forget",
        Some(KEY_SCOPED),
        Some(forget_body(subject_id, scope_id, actor_id, agent_node_id)),
    )
    .await;
    assert_eq!(owner_status, StatusCode::FORBIDDEN);
    assert_ne!(
        owner_response["error"]["code"], "capability_denied",
        "an owner key must clear the capability gate; the failure is downstream"
    );
}

/// Supplying an explicit `transaction_as_of`/`valid_at` selector turns recall
/// into a historical-audit read. A coding-agent key (default false) is refused
/// on its OWN scope — so it cannot recover stale/superseded/expired bytes by
/// supplying an earlier time. A no-selector recall never reaches this gate.
#[tokio::test]
async fn agent_key_without_can_audit_history_cannot_read_as_of_its_own_scope() {
    let tenant_id = tenant(81_000);
    let scope_id = scope(81_001);
    let actor_id = actor(81_002);
    let app = capability_app(tenant_id, actor_id, scope_id, false, false);

    for selector in [
        serde_json::json!({ "transaction_as_of": "2026-01-01T00:00:00Z" }),
        serde_json::json!({ "valid_at": "2026-01-01T00:00:00Z" }),
    ] {
        let mut body = serde_json::json!({
            "subject_id": memphant_types::SubjectId::new(),
            "scope_id": scope_id,
            "actor_id": actor_id,
            "agent_node_id": memphant_types::AgentNodeId::new(),
            "subject_generation": 0,
            "query": "history probe",
        });
        for (key, value) in selector.as_object().unwrap() {
            body[key] = value.clone();
        }
        let (status, response) =
            send(&app, "POST", "/v1/recall", Some(KEY_SCOPED), Some(body)).await;
        assert_eq!(status, StatusCode::FORBIDDEN, "{selector}");
        assert_eq!(response["error"]["code"], "capability_denied", "{selector}");
    }
}
