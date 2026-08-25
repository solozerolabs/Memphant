use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use memphant_core::service::{MemoryService, file_sync_plan_sha256};
use memphant_core::{
    FixedClock, InMemoryStore, MemoryStore, NoopEmbedding,
    service::MAX_CANONICAL_PROJECTION_ENCODED_BYTES,
};
use memphant_types::{
    ActorId, CanonicalProjectionResponse, ContextBindingAgentRef, ContextBindingEntityRef,
    ContextBindingRequest, ContextBindingResponse, ContextBindingScopeRef, CorrectRequest,
    CorrectSelector, CorrectionPayload, EraseSubjectRequest, FileSyncOperation, FileSyncRequest,
    FileSyncResult, ForgetRequest, ForgetSelector, HealthResponse,
    MAX_FILE_SYNC_REQUEST_ENCODED_BYTES, MarkOutcome, MarkRequest, MemoryKind, NewMemoryUnit,
    RecallHttpRequest, RecallResponse, ReflectRequest, RetainEpisodeHttpRequest,
    RetainEpisodeHttpResponse, RetainEpisodePayload, RetainPayload, RetainResourcePayload,
    RetainUnitPayload, SCHEMA_COMPAT_REVISION, ScopeId, ScopeMemoryResponse, TaskCompletionStatus,
    TaskId, TaskMemoryAttribution, TaskMemoryEventInput, TaskMemoryEventKind,
    TaskMemoryEventsRequest, TaskMemoryEventsResult, TaskOutcomeRequest, TaskOutcomeResult,
    TaskValidatorStatus, TenantId, TrustLevel, UnitState,
};
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use tower::ServiceExt;

static REQUEST_ID: AtomicU64 = AtomicU64::new(1);
const REST_TEST_CLOCK: FixedClock = FixedClock("2026-07-22T00:00:00Z");

fn add_idempotency_header(
    mut builder: axum::http::request::Builder,
    path: &str,
) -> axum::http::request::Builder {
    if matches!(
        path,
        "/v1/episodes"
            | "/v1/reflect"
            | "/v1/correct"
            | "/v1/forget"
            | "/v1/subjects/erase"
            | "/v1/mark"
            | "/v1/task-outcomes"
            | "/v1/task-memory-events"
            | "/v1/file-sync"
    ) {
        builder = builder.header(
            "idempotency-key",
            format!("rest-test-{}", REQUEST_ID.fetch_add(1, Ordering::Relaxed)),
        );
    }
    builder
}

#[tokio::test]
async fn file_sync_route_enforces_its_exact_encoded_body_ceiling() {
    async fn rejection_for(body_len: usize) -> (StatusCode, Value) {
        let mut body = vec![b' '; body_len];
        body[0] = b'{';
        body[1] = b'}';
        let response = dev_app(tenant(96_501))
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/file-sync")
                    .header("content-type", "application/json")
                    .header("idempotency-key", "file-sync-body-ceiling")
                    .body(Body::from(body))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        let body =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        (status, body)
    }

    let (status, body) = rejection_for(MAX_FILE_SYNC_REQUEST_ENCODED_BYTES).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "sync_invalid");
    assert!(
        body["error"]["message"]
            .as_str()
            .unwrap()
            .contains("missing field")
    );

    let (status, body) = rejection_for(MAX_FILE_SYNC_REQUEST_ENCODED_BYTES + 1).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "sync_invalid");
    assert!(
        body["error"]["message"]
            .as_str()
            .unwrap()
            .contains("length limit exceeded")
    );
}

fn file_sync_request(
    binding: &ContextBindingResponse,
    base_fingerprint: String,
    operations: Vec<FileSyncOperation>,
) -> FileSyncRequest {
    FileSyncRequest {
        subject_id: binding.subject_id,
        scope_id: binding.scope_id,
        actor_id: binding.actor_id,
        agent_node_id: binding.agent_node_id,
        subject_generation: binding.subject_generation,
        base_fingerprint,
        plan_sha256: file_sync_plan_sha256(&operations).unwrap(),
        observed_at: REST_TEST_CLOCK.0.to_string(),
        operations,
    }
}

#[tokio::test]
async fn file_sync_route_is_atomic_strict_and_uses_stable_error_codes() {
    let tenant_id = tenant(96_500);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "file-sync-route").await;
    let projection: CanonicalProjectionResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
            binding.scope_id.as_uuid(),
            binding.subject_id.as_uuid(),
            binding.actor_id.as_uuid(),
            binding.agent_node_id.as_uuid(),
            binding.subject_generation
        ),
        Option::<()>::None,
    )
    .await
    .1;
    let operation = FileSyncOperation::Retain {
        fact_key: "profile:route".to_string(),
        predicate: "states".to_string(),
        body: "The authenticated route writes one semantic unit.".to_string(),
        confidence: 1.0,
        valid_from: None,
        valid_to: None,
    };
    let request = file_sync_request(&binding, projection.fingerprint, vec![operation]);
    let result: FileSyncResult = json_request(&app, "POST", "/v1/file-sync", Some(request))
        .await
        .1;
    assert_eq!(result.operations.len(), 1);

    let stale = file_sync_request(
        &binding,
        "0".repeat(64),
        vec![FileSyncOperation::Retain {
            fact_key: "profile:stale-route".to_string(),
            predicate: "states".to_string(),
            body: "The stale route write must not land.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        }],
    );
    let (status, body): (StatusCode, Value) =
        error_json_request(&app, "POST", "/v1/file-sync", stale).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"]["code"], "sync_conflict");

    let invalid_timestamp = file_sync_request(
        &binding,
        result.fingerprint,
        vec![FileSyncOperation::Retain {
            fact_key: "profile:invalid-time-route".to_string(),
            predicate: "states".to_string(),
            body: "The invalid timestamp must use the sync error contract.".to_string(),
            confidence: 1.0,
            valid_from: Some("2026-07-22T00:00:00-07:00".to_string()),
            valid_to: None,
        }],
    );
    let (status, body): (StatusCode, Value) =
        error_json_request(&app, "POST", "/v1/file-sync", invalid_timestamp).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "sync_invalid");

    let raw = Request::builder()
        .method("POST")
        .uri("/v1/file-sync")
        .header("content-type", "application/json")
        .header("idempotency-key", "file-sync-unknown-field")
        .body(Body::from(
            r#"{"subject_id":"00000000-0000-0000-0000-000000000001","unknown":true}"#,
        ))
        .unwrap();
    let response = app.oneshot(raw).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let body: Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(body["error"]["code"], "sync_invalid");
}

fn tenant(value: u128) -> TenantId {
    TenantId::from_u128(value)
}

fn scope(value: u128) -> ScopeId {
    ScopeId::from_u128(value)
}

fn actor(value: u128) -> ActorId {
    ActorId::from_u128(value)
}

fn dev_state(tenant_id: TenantId) -> memphant_server::AppState<InMemoryStore> {
    memphant_server::AppState::from_service(
        MemoryService::new(
            Arc::new(InMemoryStore::default()),
            Arc::new(REST_TEST_CLOCK),
            Arc::new(NoopEmbedding),
        ),
        "memory",
    )
    .with_dev_tenant(tenant_id)
}

fn dev_app(tenant_id: TenantId) -> axum::Router {
    memphant_server::app(dev_state(tenant_id))
}

fn dev_app_with_state(
    tenant_id: TenantId,
) -> (
    axum::Router,
    memphant_server::AppState<memphant_core::InMemoryStore>,
) {
    let state = dev_state(tenant_id);
    (memphant_server::app(state.clone()), state)
}

fn episode_request(
    scope_id: ScopeId,
    actor_id: ActorId,
    body: &str,
    _subject: Option<&str>,
) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: memphant_types::SubjectId::new(),
        scope_id,
        actor_id,
        agent_node_id: memphant_types::AgentNodeId::new(),
        subject_generation: 0,
        source_ref: format!("rest:test:{}", REQUEST_ID.fetch_add(1, Ordering::Relaxed)),
        observed_at: "2026-07-15T00:00:00Z".to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
            subject: None,
            predicate: None,
        }),
    }
}

fn recall_request(
    tenant_id: TenantId,
    scope_id: ScopeId,
    actor_id: ActorId,
    query: &str,
) -> RecallHttpRequest {
    RecallHttpRequest {
        compact_only: false,
        serve_captures: false,
        subject_id: memphant_types::SubjectId::from_u128(tenant_id.as_uuid().as_u128()),
        scope_id,
        agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
        subject_generation: 0,
        actor_id,
        query: query.to_string(),
        limit: Some(4),
        budget_tokens: Some(120),
        mode: None,
        include_beliefs: None,
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

#[tokio::test]
async fn explicit_deep_without_provider_returns_stable_503() {
    let tenant_id = tenant(41_000);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "deep-unavailable").await;
    let mut request = bind_recall_request(
        recall_request(
            tenant_id,
            binding.scope_id,
            binding.actor_id,
            "search deeply",
        ),
        &binding,
    );
    request.mode = Some(memphant_types::RecallMode::Deep);

    let http_request = Request::builder()
        .method("POST")
        .uri("/v1/recall")
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(&request).unwrap()))
        .unwrap();
    let response = app.clone().oneshot(http_request).await.unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body: Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(body["error"]["code"], "deep_unavailable");
    assert_eq!(body["error"]["message"], "deep recall is unavailable");
}

async fn bind_context(app: &axum::Router, client_ref: &str) -> ContextBindingResponse {
    json_request(
        app,
        "PUT",
        &format!("/v1/context-bindings/{client_ref}"),
        Some(ContextBindingRequest {
            subject: ContextBindingEntityRef {
                external_ref: format!("subject:{client_ref}"),
                kind: "user".to_string(),
            },
            actor: ContextBindingEntityRef {
                external_ref: format!("actor:{client_ref}"),
                kind: "system".to_string(),
            },
            scope: ContextBindingScopeRef {
                external_ref: format!("scope:{client_ref}"),
                kind: "user_root".to_string(),
                parent_external_ref: None,
            },
            agent_node: ContextBindingAgentRef {
                external_ref: format!("agent:{client_ref}"),
                parent_external_ref: None,
            },
            access_policies: vec![],
        }),
    )
    .await
    .1
}

fn bind_episode_request(
    mut request: RetainEpisodeHttpRequest,
    binding: &ContextBindingResponse,
) -> RetainEpisodeHttpRequest {
    request.subject_id = binding.subject_id;
    request.scope_id = binding.scope_id;
    request.actor_id = binding.actor_id;
    request.agent_node_id = binding.agent_node_id;
    request.subject_generation = binding.subject_generation;
    request
}

fn bind_recall_request(
    mut request: RecallHttpRequest,
    binding: &ContextBindingResponse,
) -> RecallHttpRequest {
    request.subject_id = binding.subject_id;
    request.scope_id = binding.scope_id;
    request.actor_id = binding.actor_id;
    request.agent_node_id = binding.agent_node_id;
    request.subject_generation = binding.subject_generation;
    request
}

fn active_projection_unit(
    tenant_id: TenantId,
    binding: &ContextBindingResponse,
    source_ref: &str,
    fact_key: &str,
    body: &str,
) -> NewMemoryUnit {
    NewMemoryUnit {
        capture: None,
        tenant_id,
        data_subject_id: binding.subject_id,
        scope_id: binding.scope_id,
        agent_node_id: binding.agent_node_id,
        subject_generation: binding.subject_generation,
        kind: MemoryKind::Semantic,
        state: UnitState::Active,
        fact_key: Some(fact_key.to_string()),
        predicate: Some("states".to_string()),
        body: body.to_string(),
        confidence: Some(1.0),
        trust_level: TrustLevel::TrustedSystem,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(binding.actor_id),
        source_kind: Some("test".to_string()),
        source_ref: source_ref.to_string(),
        observed_at: "2026-07-22T00:00:00Z".to_string(),
        source_episode_id: None,
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: Vec::new(),
        valid_from: Some("2026-07-01T00:00:00Z".to_string()),
        valid_to: Some("2026-08-01T00:00:00Z".to_string()),
        transaction_from: None,
        transaction_to: None,
    }
}

#[tokio::test]
async fn canonical_projection_is_a_dedicated_unranked_visible_snapshot() {
    let tenant_id = tenant(96_000);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "canonical-projection").await;

    let _: RetainEpisodeHttpResponse = json_request(
        &app,
        "POST",
        "/v1/episodes",
        Some(RetainEpisodeHttpRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            actor_id: binding.actor_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            source_ref: "rest:canonical-projection".to_string(),
            observed_at: "2026-07-22T00:00:00Z".to_string(),
            payload: RetainPayload::Unit(RetainUnitPayload {
                kind: MemoryKind::Semantic,
                fact_key: Some("projection:visible".to_string()),
                subject: None,
                predicate: "states".to_string(),
                body: "This is the visible canonical fact.".to_string(),
                confidence: 1.0,
                valid_from: Some("2026-07-01T00:00:00Z".to_string()),
                valid_to: Some("2026-08-01T00:00:00Z".to_string()),
                target_unit_ids: None,
            }),
        }),
    )
    .await
    .1;

    let projection: CanonicalProjectionResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
            binding.scope_id.as_uuid(),
            binding.subject_id.as_uuid(),
            binding.actor_id.as_uuid(),
            binding.agent_node_id.as_uuid(),
            binding.subject_generation,
        ),
        None::<()>,
    )
    .await
    .1;

    assert_eq!(projection.tenant_id, tenant_id);
    assert_eq!(projection.subject_id, binding.subject_id);
    assert_eq!(projection.actor_id, binding.actor_id);
    assert_eq!(projection.scope_id, binding.scope_id);
    assert_eq!(projection.agent_node_id, binding.agent_node_id);
    assert_eq!(projection.subject_generation, binding.subject_generation);
    assert_eq!(projection.evaluated_at, REST_TEST_CLOCK.0);
    assert_eq!(projection.items.len(), 1);
    assert_eq!(
        projection.items[0].body,
        "This is the visible canonical fact."
    );
    assert_eq!(
        projection.items[0].body_sha256,
        "cd18204f20a65227152e24543607e22722c3631ca8b76c46343c7c6d65c3081f"
    );
    let encoded = serde_json::to_value(&projection).expect("projection JSON");
    assert_eq!(encoded["items"][0]["valid_from"], "2026-07-01T00:00:00Z");
    assert_eq!(encoded["items"][0]["valid_to"], "2026-08-01T00:00:00Z");
    assert_eq!(projection.fingerprint.len(), 64);

    let repeated: CanonicalProjectionResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
            binding.scope_id.as_uuid(),
            binding.subject_id.as_uuid(),
            binding.actor_id.as_uuid(),
            binding.agent_node_id.as_uuid(),
            binding.subject_generation,
        ),
        None::<()>,
    )
    .await
    .1;
    assert_eq!(repeated.items, projection.items);
    assert_eq!(repeated.fingerprint, projection.fingerprint);
}

#[tokio::test]
async fn canonical_projection_route_orders_multiple_units_by_unit_id() {
    let tenant_id = tenant(96_150);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "canonical-projection-order").await;
    let context = state
        .store()
        .resolve_memory_context(
            tenant_id,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve projection context");

    let mut first_tx = state.store().begin(&context).await.expect("begin first");
    let first_id = state
        .store()
        .stage_memory_unit(
            &mut first_tx,
            active_projection_unit(
                tenant_id,
                &binding,
                "rest:canonical-projection-order:first",
                "projection:z",
                "first generated id",
            ),
        )
        .await
        .expect("stage first");
    let mut second_tx = state.store().begin(&context).await.expect("begin second");
    let second_id = state
        .store()
        .stage_memory_unit(
            &mut second_tx,
            active_projection_unit(
                tenant_id,
                &binding,
                "rest:canonical-projection-order:second",
                "projection:a",
                "second generated id",
            ),
        )
        .await
        .expect("stage second");
    assert!(first_id.as_uuid() < second_id.as_uuid());

    // Commit in reverse to prove the HTTP read is ordered by unit id, rather
    // than by insertion order in the backing store.
    state
        .store()
        .commit(second_tx)
        .await
        .expect("commit second");
    state.store().commit(first_tx).await.expect("commit first");

    let projection: CanonicalProjectionResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
            binding.scope_id.as_uuid(),
            binding.subject_id.as_uuid(),
            binding.actor_id.as_uuid(),
            binding.agent_node_id.as_uuid(),
            binding.subject_generation,
        ),
        None::<()>,
    )
    .await
    .1;

    assert_eq!(
        projection
            .items
            .iter()
            .map(|item| item.unit_id)
            .collect::<Vec<_>>(),
        vec![first_id, second_id]
    );
}

#[tokio::test]
async fn task_outcome_links_exposure_and_accepts_only_causal_delayed_evidence() {
    let tenant_id = tenant(96_175);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "task-outcome").await;
    let context = state
        .store()
        .resolve_memory_context(
            tenant_id,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve context");
    let mut tx = state.store().begin(&context).await.expect("begin");
    let unit_id = state
        .store()
        .stage_memory_unit(
            &mut tx,
            active_projection_unit(
                tenant_id,
                &binding,
                "rest:task-outcome:unit",
                "procedure:scoped-tests",
                "Run the narrow validator before the full repository gate.",
            ),
        )
        .await
        .expect("stage unit");
    state.store().commit(tx).await.expect("commit unit");

    let task_id = TaskId::new();
    let outcome: TaskOutcomeResult = json_request(
        &app,
        "POST",
        "/v1/task-outcomes",
        Some(TaskOutcomeRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            actor_id: binding.actor_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            task_id,
            harness_id: "claude-code".to_string(),
            model_id: "claude-opus-5".to_string(),
            started_at: "2026-08-08T10:00:00Z".to_string(),
            ended_at: "2026-08-08T10:05:00Z".to_string(),
            completion_status: TaskCompletionStatus::Completed,
            validator_status: TaskValidatorStatus::Passed,
            tool_count: 3,
            failure_count: 0,
            retry_count: 0,
            planned_files: Some(vec![
                "./src//lib.rs".to_string(),
                "tests/test.rs".to_string(),
            ]),
            actual_files: vec!["src/lib.rs".to_string()],
            transcript_sha256: "a".repeat(64),
            shown_unit_ids: vec![unit_id],
            activated_unit_ids: vec![unit_id],
        }),
    )
    .await
    .1;
    assert_eq!(outcome.scope_recall, Some(0.5));
    assert_eq!(outcome.scope_precision, Some(1.0));
    assert_eq!(outcome.scope_jaccard, Some(0.5));

    let delayed: TaskMemoryEventsResult = json_request(
        &app,
        "POST",
        "/v1/task-memory-events",
        Some(TaskMemoryEventsRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            actor_id: binding.actor_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            task_id,
            events: vec![TaskMemoryEventInput {
                unit_id,
                event: TaskMemoryEventKind::Helpful,
                attribution: TaskMemoryAttribution::DeterministicScorer,
            }],
        }),
    )
    .await
    .1;
    assert_eq!(delayed.recorded, 1);
    assert_eq!(state.store().task_outcomes(tenant_id).len(), 1);
    assert_eq!(state.store().task_memory_events(tenant_id).len(), 3);

    let body = TaskMemoryEventsRequest {
        subject_id: binding.subject_id,
        scope_id: binding.scope_id,
        actor_id: binding.actor_id,
        agent_node_id: binding.agent_node_id,
        subject_generation: binding.subject_generation,
        task_id,
        events: vec![TaskMemoryEventInput {
            unit_id,
            event: TaskMemoryEventKind::Harmful,
            attribution: TaskMemoryAttribution::Observational,
        }],
    };
    let request = Request::builder()
        .method("POST")
        .uri("/v1/task-memory-events")
        .header("idempotency-key", "reject-observational")
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(&body).unwrap()))
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn canonical_projection_rejects_an_encoded_payload_over_the_ceiling() {
    let tenant_id = tenant(96_200);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "canonical-projection-ceiling").await;
    let context = state
        .store()
        .resolve_memory_context(
            tenant_id,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve projection context");
    let mut tx = state
        .store()
        .begin(&context)
        .await
        .expect("begin projection seed");
    state
        .store()
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("projection:ceiling".to_string()),
                predicate: Some("states".to_string()),
                body: "x".repeat(MAX_CANONICAL_PROJECTION_ENCODED_BYTES),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(binding.actor_id),
                source_kind: Some("test".to_string()),
                source_ref: "rest:canonical-projection-ceiling".to_string(),
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
        .expect("stage projection seed");
    state
        .store()
        .commit(tx)
        .await
        .expect("commit projection seed");

    let request = Request::builder()
        .method("GET")
        .uri(format!(
            "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
            binding.scope_id.as_uuid(),
            binding.subject_id.as_uuid(),
            binding.actor_id.as_uuid(),
            binding.agent_node_id.as_uuid(),
            binding.subject_generation,
        ))
        .body(Body::empty())
        .expect("projection request");
    let response = app
        .clone()
        .oneshot(request)
        .await
        .expect("projection response");
    let status = response.status();
    let body: Value = serde_json::from_slice(
        &response
            .into_body()
            .collect()
            .await
            .expect("projection error body")
            .to_bytes(),
    )
    .expect("projection error json");
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE, "body={body}");
    assert_eq!(body["error"]["code"], "projection_too_large");
}

#[tokio::test]
async fn rest_examples_round_trip_through_retain_reflect_recall_trace_and_mutations() {
    let tenant_id = tenant(90_000);
    let scope_id = scope(90_001);
    let actor_id = actor(90_002);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "rest-roundtrip").await;

    let health: HealthResponse = json_request(&app, "GET", "/v1/health", None::<()>).await.1;
    assert_eq!(health.status, "ok");
    assert_eq!(health.store, "memory");
    assert_eq!(health.schema_compat_revision, SCHEMA_COMPAT_REVISION);

    let retained: RetainEpisodeHttpResponse = json_request(
        &app,
        "POST",
        "/v1/episodes",
        Some(bind_episode_request(
            episode_request(
                scope_id,
                actor_id,
                "Release region is Taipei.",
                Some("release region"),
            ),
            &binding,
        )),
    )
    .await
    .1;
    assert_eq!(retained.enqueued, vec!["reflect_episode"]);
    assert!(retained.episode_id.is_some());

    let (status, reflected): (_, Value) = json_request(
        &app,
        "POST",
        "/v1/reflect",
        Some(ReflectRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            actor_id: binding.actor_id,
        }),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    assert!(reflected["job_id"].is_string());
    state
        .service()
        .run_worker_tick(usize::MAX)
        .await
        .expect("worker reflects accepted job");

    let recalled: RecallResponse = json_request(
        &app,
        "POST",
        "/v1/recall",
        Some(bind_recall_request(
            recall_request(
                tenant_id,
                scope_id,
                actor_id,
                "Where is the release region?",
            ),
            &binding,
        )),
    )
    .await
    .1;
    assert_eq!(recalled.items[0].body, "Release region is Taipei.");
    assert!(!recalled.degraded);

    let trace_path = format!(
        "/v1/traces/{}?subject_id={}&subject_generation={}&scope_id={}&actor_id={}&agent_node_id={}",
        recalled.trace_id.as_uuid(),
        binding.subject_id.as_uuid(),
        binding.subject_generation,
        binding.scope_id.as_uuid(),
        binding.actor_id.as_uuid(),
        binding.agent_node_id.as_uuid(),
    );
    let trace: Value = json_request(&app, "GET", &trace_path, None::<()>).await.1;
    assert_eq!(trace["id"], recalled.trace_id.as_uuid().to_string());

    let corrected: Value = json_request(
        &app,
        "POST",
        "/v1/correct",
        Some(CorrectRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            actor_id: binding.actor_id,
            selector: CorrectSelector {
                memory_unit_id: recalled.items[0].unit_id,
            },
            correction: CorrectionPayload {
                value: "Release region is Singapore.".to_string(),
                reason: "stale_fact".to_string(),
                source_ref: "rest:correction:release-region".to_string(),
                observed_at: "2026-07-15T00:00:00Z".to_string(),
                valid_from: None,
                valid_to: None,
            },
        }),
    )
    .await
    .1;
    assert_eq!(
        corrected["superseded"][0],
        recalled.items[0].unit_id.as_uuid().to_string()
    );

    let forgotten: Value = json_request(
        &app,
        "POST",
        "/v1/forget",
        Some(ForgetRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            actor_id: binding.actor_id,
            selector: ForgetSelector {
                memory_unit_id: Some(recalled.items[0].unit_id),
                episode_id: None,
                resource_id: None,
                scope_id: binding.scope_id,
            },
            reason: "user_request".to_string(),
        }),
    )
    .await
    .1;
    assert_eq!(
        forgotten["verification"],
        "authorized_transaction_committed"
    );

    let marked: Value = json_request(
        &app,
        "POST",
        "/v1/mark",
        Some(MarkRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            actor_id: binding.actor_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            trace_id: recalled.trace_id,
            caller_id: "rest-contract".to_string(),
            used_ids: vec![recalled.items[0].unit_id],
            outcome: MarkOutcome::Success,
        }),
    )
    .await
    .1;
    assert_eq!(marked["accepted"], true);
}

#[tokio::test]
async fn resource_retain_reflect_recall_returns_resource_kind_item() {
    let tenant_id = tenant(91_000);
    let scope_id = scope(91_001);
    let actor_id = actor(91_002);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "resource-roundtrip").await;

    let mut request = bind_episode_request(episode_request(scope_id, actor_id, "", None), &binding);
    request.payload = RetainPayload::Resource(RetainResourcePayload {
        uri: "https://example.test/runbooks/deploy.md".to_string(),
        mime_type: "text/markdown".to_string(),
        content_hash: "sha256:15cae6da6e921e32a0dd42efdb3fcec54e8de2b4b45824a46f2b7768dd78448b"
            .to_string(),
        kind: Some(memphant_types::ResourceKind::Document),
        revision: Some("rev-42".to_string()),
        body: Some("Deploy runbook: canary first, then roll forward regions.".to_string()),
    });
    let retained: RetainEpisodeHttpResponse =
        json_request(&app, "POST", "/v1/episodes", Some(request))
            .await
            .1;
    assert_eq!(retained.enqueued, vec!["reflect_resource"]);
    assert!(retained.resource_id.is_some());

    let (status, reflected): (_, Value) = json_request(
        &app,
        "POST",
        "/v1/reflect",
        Some(ReflectRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            actor_id: binding.actor_id,
        }),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    assert!(reflected["job_id"].is_string());
    state
        .service()
        .run_worker_tick(usize::MAX)
        .await
        .expect("worker reflects accepted job");

    let recalled: RecallResponse = json_request(
        &app,
        "POST",
        "/v1/recall",
        Some(bind_recall_request(
            recall_request(
                tenant_id,
                scope_id,
                actor_id,
                "How does the deploy runbook roll forward?",
            ),
            &binding,
        )),
    )
    .await
    .1;
    let item = recalled
        .items
        .iter()
        .find(|item| item.kind == MemoryKind::Resource)
        .expect("resource-derived item recalled");
    assert_eq!(item.citation_resource_id, retained.resource_id);
}

#[tokio::test]
async fn forget_by_episode_empties_recall_and_second_reflect_does_not_resurrect() {
    let tenant_id = tenant(92_000);
    let scope_id = scope(92_001);
    let actor_id = actor(92_002);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "forget-roundtrip").await;

    let retained: RetainEpisodeHttpResponse = json_request(
        &app,
        "POST",
        "/v1/episodes",
        Some(bind_episode_request(
            episode_request(
                scope_id,
                actor_id,
                "Payment processor is AcmePay.",
                Some("payment processor"),
            ),
            &binding,
        )),
    )
    .await
    .1;
    let episode_id = retained.episode_id.expect("episode id");

    let reflect = ReflectRequest {
        subject_id: binding.subject_id,
        scope_id: binding.scope_id,
        agent_node_id: binding.agent_node_id,
        subject_generation: binding.subject_generation,
        actor_id: binding.actor_id,
    };
    let _: Value = json_request(&app, "POST", "/v1/reflect", Some(reflect.clone()))
        .await
        .1;
    state
        .service()
        .run_worker_tick(usize::MAX)
        .await
        .expect("worker reflects episode before forgetting it");

    let forgotten: Value = json_request(
        &app,
        "POST",
        "/v1/forget",
        Some(ForgetRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            actor_id: binding.actor_id,
            selector: ForgetSelector {
                memory_unit_id: None,
                episode_id: Some(episode_id),
                resource_id: None,
                scope_id: binding.scope_id,
            },
            reason: "user_request".to_string(),
        }),
    )
    .await
    .1;
    assert_eq!(
        forgotten["verification"],
        "authorized_transaction_committed"
    );

    let recalled: RecallResponse = json_request(
        &app,
        "POST",
        "/v1/recall",
        Some(bind_recall_request(
            recall_request(
                tenant_id,
                scope_id,
                actor_id,
                "Which payment processor do we use?",
            ),
            &binding,
        )),
    )
    .await
    .1;
    assert!(recalled.items.is_empty(), "forgotten memory must stay gone");

    // A second reflect (recompile) must not resurrect the tombstoned episode.
    let _: Value = json_request(&app, "POST", "/v1/reflect", Some(reflect))
        .await
        .1;
    let recalled_again: RecallResponse = json_request(
        &app,
        "POST",
        "/v1/recall",
        Some(bind_recall_request(
            recall_request(
                tenant_id,
                scope_id,
                actor_id,
                "Which payment processor do we use?",
            ),
            &binding,
        )),
    )
    .await
    .1;
    assert!(
        recalled_again.items.is_empty(),
        "reflect must not resurrect forgotten memory"
    );
}

/// End-to-end subject erasure over HTTP: an owner request hard-deletes the whole
/// subject (200 + `policy: hard_delete`), replay of the same idempotency key at
/// the ledger returns the identical receipt, and any follow-up operation on the
/// erased subject is refused with `410 Gone` / `subject_erased`.
#[tokio::test]
async fn erase_subject_over_http_hard_deletes_and_blocks_followups() {
    let tenant_id = tenant(92_100);
    let scope_id = scope(92_101);
    let actor_id = actor(92_102);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "erase-roundtrip").await;

    // Seed the subject with an episode and a compiled belief.
    let _: RetainEpisodeHttpResponse = json_request(
        &app,
        "POST",
        "/v1/episodes",
        Some(bind_episode_request(
            episode_request(
                scope_id,
                actor_id,
                "Payment processor is AcmePay.",
                Some("payment processor"),
            ),
            &binding,
        )),
    )
    .await
    .1;
    let _: Value = json_request(
        &app,
        "POST",
        "/v1/reflect",
        Some(ReflectRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            actor_id: binding.actor_id,
        }),
    )
    .await
    .1;
    state
        .service()
        .run_worker_tick(usize::MAX)
        .await
        .expect("worker compiles the subject before erasure");

    // Capture the pre-erasure resolved context so we can assert ledger replay
    // returns the exact receipt (the HTTP handler resolves context first, which
    // after erasure refuses with SubjectErased — so replay is proven at the
    // service/ledger layer that actually owns idempotency).
    let context = state
        .store()
        .resolve_memory_context(
            tenant_id,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("subject resolves before erasure");

    let request = EraseSubjectRequest {
        subject_id: binding.subject_id,
        scope_id: binding.scope_id,
        actor_id: binding.actor_id,
        agent_node_id: binding.agent_node_id,
        subject_generation: binding.subject_generation,
        reason: "user_request".to_string(),
    };

    // (b) Owner erase over HTTP → 200 with the erasure receipt (hard delete +
    // deletion-generation bump).
    let http = Request::builder()
        .method("POST")
        .uri("/v1/subjects/erase")
        .header("content-type", "application/json")
        .header("idempotency-key", "erase-idem-fixed")
        .body(Body::from(serde_json::to_vec(&request).unwrap()))
        .unwrap();
    let response = app.clone().oneshot(http).await.unwrap();
    let status = response.status();
    let erased: Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(status, StatusCode::OK, "erase body={erased}");
    assert_eq!(erased["generation"], binding.subject_generation + 1);
    let erased_at = erased["erased_at"].as_str().expect("erased_at").to_string();

    // (c) Ledger replay of the same idempotency key returns the identical receipt.
    let replay = state
        .service()
        .erase_subject(&context, "erase-idem-fixed", request)
        .await
        .expect("replayed erasure resolves");
    assert_eq!(replay.status(), 200);
    let replay_body: Value = serde_json::from_slice(replay.body()).unwrap();
    assert_eq!(replay_body, erased);
    assert_eq!(replay_body["erased_at"], erased_at);

    // (d) Any follow-up on the erased subject is refused with 410 subject_erased.
    let followup = Request::builder()
        .method("POST")
        .uri("/v1/recall")
        .header("content-type", "application/json")
        .body(Body::from(
            serde_json::to_vec(&bind_recall_request(
                recall_request(
                    tenant_id,
                    scope_id,
                    actor_id,
                    "Which payment processor do we use?",
                ),
                &binding,
            ))
            .unwrap(),
        ))
        .unwrap();
    let followup_response = app.clone().oneshot(followup).await.unwrap();
    assert_eq!(followup_response.status(), StatusCode::GONE);
    let followup_body: Value = serde_json::from_slice(
        &followup_response
            .into_body()
            .collect()
            .await
            .unwrap()
            .to_bytes(),
    )
    .unwrap();
    assert_eq!(followup_body["error"]["code"], "subject_erased");
}

#[tokio::test]
async fn scope_memory_cursor_pagination_yields_two_disjoint_pages() {
    let tenant_id = tenant(93_000);
    let scope_id = scope(93_001);
    let actor_id = actor(93_002);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "scope-pagination").await;

    for index in 0..5 {
        let _: RetainEpisodeHttpResponse = json_request(
            &app,
            "POST",
            "/v1/episodes",
            Some(bind_episode_request(
                episode_request(
                    scope_id,
                    actor_id,
                    &format!("Paginated fact number {index} for the export surface."),
                    Some(&format!("paginated fact {index}")),
                ),
                &binding,
            )),
        )
        .await
        .1;
    }
    let _: Value = json_request(
        &app,
        "POST",
        "/v1/reflect",
        Some(ReflectRequest {
            subject_id: binding.subject_id,
            scope_id: binding.scope_id,
            agent_node_id: binding.agent_node_id,
            subject_generation: binding.subject_generation,
            actor_id: binding.actor_id,
        }),
    )
    .await
    .1;
    loop {
        let tick = state
            .service()
            .run_worker_tick(usize::MAX)
            .await
            .expect("worker reflects paginated episodes");
        assert!(tick.is_clean(), "reflect drain hit errors: {tick:?}");
        if tick.is_idle() {
            break;
        }
    }

    let context_query = format!(
        "subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
        binding.subject_id.as_uuid(),
        binding.actor_id.as_uuid(),
        binding.agent_node_id.as_uuid(),
        binding.subject_generation,
    );
    let base = format!(
        "/v1/scopes/{}/memory?{context_query}&limit=3",
        binding.scope_id.as_uuid()
    );
    let page_one: ScopeMemoryResponse = json_request(&app, "GET", &base, None::<()>).await.1;
    assert_eq!(page_one.items.len(), 3);
    assert!(page_one.has_more);
    let cursor = page_one.next_cursor.clone().expect("cursor for page two");

    let page_two: ScopeMemoryResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/memory?{context_query}&limit=3&cursor={cursor}",
            binding.scope_id.as_uuid()
        ),
        None::<()>,
    )
    .await
    .1;
    assert!(!page_two.items.is_empty());
    assert!(!page_two.has_more);

    let ids_one: std::collections::HashSet<_> = page_one
        .items
        .iter()
        .map(|unit| unit.id.as_uuid())
        .collect();
    let ids_two: std::collections::HashSet<_> = page_two
        .items
        .iter()
        .map(|unit| unit.id.as_uuid())
        .collect();
    assert!(ids_one.is_disjoint(&ids_two), "pages must not overlap");
    assert_eq!(ids_one.len() + ids_two.len(), 5);

    // kind filter: episodic-only returns everything here (all units episodic),
    // a kind with no units returns an empty page, and junk is a 400 — never a
    // silent unfiltered response.
    let episodic: ScopeMemoryResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/memory?{context_query}&limit=100&kind=episodic",
            binding.scope_id.as_uuid()
        ),
        None::<()>,
    )
    .await
    .1;
    assert_eq!(episodic.items.len(), 5);
    assert!(
        episodic
            .items
            .iter()
            .all(|unit| unit.kind == memphant_types::MemoryKind::Episodic)
    );

    let none: ScopeMemoryResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/memory?{context_query}&limit=100&kind=preference",
            binding.scope_id.as_uuid()
        ),
        None::<()>,
    )
    .await
    .1;
    assert!(none.items.is_empty());

    let junk = format!(
        "/v1/scopes/{}/memory?{context_query}&kind=not-a-kind",
        binding.scope_id.as_uuid()
    );
    let request = add_idempotency_header(Request::builder().method("GET").uri(&junk), &junk)
        .body(Body::empty())
        .expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    assert_eq!(
        response.status(),
        StatusCode::UNPROCESSABLE_ENTITY,
        "junk kind must be rejected, not ignored"
    );
}

#[tokio::test]
async fn retain_then_immediate_recall_serves_degraded_read_your_own_writes() {
    let tenant_id = tenant(94_000);
    let scope_id = scope(94_001);
    let actor_id = actor(94_002);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "degraded-roundtrip").await;

    let _: RetainEpisodeHttpResponse = json_request(
        &app,
        "POST",
        "/v1/episodes",
        Some(bind_episode_request(
            episode_request(
                scope_id,
                actor_id,
                "Fallback rollout window is Thursday night.",
                Some("rollout window"),
            ),
            &binding,
        )),
    )
    .await
    .1;

    // No reflect between retain and recall: the fact must still be readable.
    let recalled: RecallResponse = json_request(
        &app,
        "POST",
        "/v1/recall",
        Some(bind_recall_request(
            recall_request(
                tenant_id,
                scope_id,
                actor_id,
                "When is the fallback rollout window?",
            ),
            &binding,
        )),
    )
    .await
    .1;
    assert!(recalled.degraded, "recall must flag the degraded fallback");
    assert_eq!(
        recalled.items[0].body,
        "Fallback rollout window is Thursday night."
    );
    assert!(recalled.items[0].citation_episode_id.is_none());
    assert!(recalled.items[0].citation_resource_id.is_none());
    assert!(recalled.citations.is_empty());
    assert!(recalled.candidate_whitelist.is_empty());
    assert_eq!(recalled.consolidation_lag_ms, 1);
    let trace_path = format!(
        "/v1/traces/{}?subject_id={}&subject_generation={}&scope_id={}&actor_id={}&agent_node_id={}",
        recalled.trace_id.as_uuid(),
        binding.subject_id.as_uuid(),
        binding.subject_generation,
        binding.scope_id.as_uuid(),
        binding.actor_id.as_uuid(),
        binding.agent_node_id.as_uuid(),
    );
    let trace: Value = json_request(&app, "GET", &trace_path, None::<()>).await.1;
    assert_eq!(trace["citations"], serde_json::json!([]));
    assert_eq!(trace["context_items"], serde_json::json!([]));
    assert_eq!(trace["consolidation_lag_ms"], 1);
    assert_eq!(
        trace["degradation"]["reason"],
        "pending_reflection_read_your_own_writes"
    );
    assert_eq!(
        trace["degradation"]["items"][0]["body"],
        "Fallback rollout window is Thursday night."
    );

    let request = Request::builder()
        .method("POST")
        .uri("/v1/mark")
        .header("content-type", "application/json")
        .header("idempotency-key", "degraded-mark")
        .body(Body::from(
            serde_json::to_vec(&MarkRequest {
                subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                actor_id: binding.actor_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                trace_id: recalled.trace_id,
                caller_id: "degraded-rest-contract".to_string(),
                used_ids: vec![recalled.items[0].unit_id],
                outcome: MarkOutcome::Success,
            })
            .expect("serialize degraded mark"),
        ))
        .expect("degraded mark request");
    let response = app.clone().oneshot(request).await.expect("mark response");
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
}

#[test]
fn openapi_document_contains_wsd_paths_and_component_schemas() {
    let document = memphant_server::openapi_document();
    for path in [
        "/v1/episodes",
        "/v1/recall",
        "/v1/reflect",
        "/v1/correct",
        "/v1/forget",
        "/v1/mark",
        "/v1/traces/{id}",
        "/v1/scopes/{id}/memory",
        "/v1/health",
    ] {
        assert!(document["paths"].get(path).is_some(), "missing {path}");
    }
    for schema in [
        "RetainEpisodeHttpRequest",
        "RetainResourcePayload",
        "RetainUnitPayload",
        "RecallHttpRequest",
        "RecallResponse",
        "CorrectRequest",
        "ForgetRequest",
        "MarkRequest",
    ] {
        assert!(
            document["components"]["schemas"].get(schema).is_some(),
            "missing schema {schema}"
        );
    }
    assert_eq!(
        document["components"]["securitySchemes"]["bearerApiKey"]["scheme"],
        "bearer"
    );
    assert_eq!(
        document["security"][0]["bearerApiKey"],
        serde_json::json!([])
    );
}

#[test]
fn openapi_request_schemas_exclude_server_derived_and_engine_control_fields() {
    let document = memphant_server::openapi_document();
    for name in [
        "RetainEpisodeHttpRequest",
        "ReflectRequest",
        "RecallHttpRequest",
        "CorrectRequest",
        "ForgetRequest",
        "MarkRequest",
    ] {
        let encoded =
            serde_json::to_string(&document["components"]["schemas"][name]).expect("schema JSON");
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
                "schema {name} exposes {forbidden}"
            );
        }
    }
    let retain =
        serde_json::to_string(&document["components"]["schemas"]["RetainEpisodeHttpRequest"])
            .expect("retain schema");
    assert!(!retain.contains("source_trust"));
    assert!(!retain.contains("compiler_version"));
}

#[test]
fn reflect_openapi_declares_accepted_instead_of_ok() {
    let document = memphant_server::openapi_document();
    let responses = &document["paths"]["/v1/reflect"]["post"]["responses"];
    assert!(responses.get("202").is_some());
    assert!(responses.get("200").is_none());
    for path in ["/v1/episodes", "/v1/reflect"] {
        let parameters = document["paths"][path]["post"]["parameters"]
            .as_array()
            .expect("mutation header parameters");
        assert!(parameters.iter().any(|parameter| {
            parameter["name"] == "Idempotency-Key"
                && parameter["in"] == "header"
                && parameter["required"] == true
        }));
    }
}

#[test]
fn openapi_paths_match_public_contract_and_gets_have_no_request_body() {
    let document = memphant_server::openapi_document();
    let paths = document["paths"].as_object().expect("paths object");

    assert_eq!(
        paths
            .keys()
            .cloned()
            .collect::<std::collections::BTreeSet<_>>(),
        memphant_server::documented_openapi_paths()
            .iter()
            .map(|path| path.to_string())
            .collect::<std::collections::BTreeSet<_>>()
    );

    for (path, item) in paths {
        if let Some(get) = item.get("get") {
            assert!(
                get.get("requestBody").is_none(),
                "{path} GET has requestBody"
            );
        }
        for name in path_template_names(path) {
            for operation in item
                .as_object()
                .expect("path item object")
                .values()
                .filter(|value| value.is_object())
            {
                let parameters = operation
                    .get("parameters")
                    .and_then(Value::as_array)
                    .expect("templated operation has parameters");
                assert!(
                    parameters.iter().any(|parameter| {
                        parameter["name"] == name
                            && parameter["in"] == "path"
                            && parameter["required"] == true
                    }),
                    "{path} is missing required path parameter {name}"
                );
            }
        }
    }
    assert_eq!(
        document["paths"]["/v1/traces/{id}"]["get"]["parameters"]
            .as_array()
            .expect("trace params")
            .len(),
        1
    );
    assert_eq!(
        document["paths"]["/v1/scopes/{id}/memory"]["get"]["parameters"]
            .as_array()
            .expect("scope params")
            .len(),
        8
    );
}

#[tokio::test]
async fn openapi_endpoint_serves_generated_document() {
    let app = dev_app(tenant(95_000));
    let served: Value = json_request(&app, "GET", "/v1/openapi.json", None::<()>)
        .await
        .1;

    assert_eq!(served, memphant_server::openapi_document());
}

#[tokio::test]
async fn direct_unit_retain_rejects_malformed_and_empty_valid_intervals() {
    let tenant_id = tenant(95_000);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "direct-intervals").await;
    for (valid_from, valid_to) in [
        ("bad", "2025-02-01T00:00:00Z"),
        ("2025-02-01T00:00:00Z", "2025-02-01T00:00:00Z"),
        ("2025-03-01T00:00:00Z", "2025-02-01T00:00:00Z"),
    ] {
        let (status, body) = error_json_request(
            &app,
            "POST",
            "/v1/episodes",
            Some(serde_json::json!({
                "subject_id": binding.subject_id,
                "scope_id": binding.scope_id,
                "actor_id": binding.actor_id,
                "agent_node_id": binding.agent_node_id,
                "subject_generation": binding.subject_generation,
                "source_ref": "rest:direct:interval",
                "observed_at": "2026-07-15T00:00:00Z",
                "payload": { "unit": {
                    "kind": "semantic",
                    "fact_key": "profile:city",
                    "predicate": "is",
                    "body": "lives in Oslo",
                    "confidence": 0.9,
                    "valid_from": valid_from,
                    "valid_to": valid_to
                }}
            })),
        )
        .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(body["error"]["code"], "invalid_request");
    }
}

#[tokio::test]
async fn episode_retain_rejects_source_kinds_outside_the_database_contract() {
    let tenant_id = tenant(96_000);
    let scope_id = scope(96_001);
    let actor_id = actor(96_002);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "invalid-source-kind").await;
    let mut request = episode_request(scope_id, actor_id, "hello", None);
    request = bind_episode_request(request, &binding);
    request.payload = RetainPayload::Episode(RetainEpisodePayload {
        source_kind: "memora-dialogue".to_string(),
        body: "hello".to_string(),
        subject: None,
        predicate: None,
    });

    let (status, body) = error_json_request(&app, "POST", "/v1/episodes", Some(request)).await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "invalid_request");
}

#[tokio::test]
async fn public_requests_reject_unknown_fields_with_the_standard_error_envelope() {
    let tenant_id = tenant(96_100);
    let scope_id = scope(96_101);
    let actor_id = actor(96_102);
    let app = dev_app(tenant_id);
    for (field, value) in [
        ("tenant_id", serde_json::json!(tenant_id)),
        ("allowed_scope_ids", serde_json::json!([scope_id])),
        ("edge_expansion_enabled", serde_json::json!(true)),
        ("rerank_enabled", serde_json::json!(true)),
        ("query_decomposition_enabled", serde_json::json!(false)),
        ("decay_enabled", serde_json::json!(false)),
    ] {
        let mut request = serde_json::to_value(recall_request(
            tenant_id,
            scope_id,
            actor_id,
            "strict contract",
        ))
        .expect("serialize request");
        request[field] = value;

        let (status, body) = error_json_request(&app, "POST", "/v1/recall", Some(request)).await;

        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "field {field}");
        assert_eq!(body["error"]["code"], "invalid_request");
        assert!(
            body["error"]["message"]
                .as_str()
                .is_some_and(|message| message.contains("unknown field"))
        );
    }
}

#[tokio::test]
async fn ledger_backed_mutations_require_one_valid_idempotency_header() {
    let app = dev_app(tenant(96_200));
    for path in [
        "/v1/episodes",
        "/v1/reflect",
        "/v1/correct",
        "/v1/forget",
        "/v1/mark",
    ] {
        let request = Request::builder()
            .method("POST")
            .uri(path)
            .header("content-type", "application/json")
            .body(Body::from("{}"))
            .expect("request");
        let response = app.clone().oneshot(request).await.expect("response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "path {path}");
    }

    for path in ["/v1/episodes", "/v1/reflect", "/v1/correct"] {
        for key in ["", "   "] {
            let request = Request::builder()
                .method("POST")
                .uri(path)
                .header("content-type", "application/json")
                .header("idempotency-key", key)
                .body(Body::from("{}"))
                .expect("request");
            let response = app.clone().oneshot(request).await.expect("response");
            assert_eq!(response.status(), StatusCode::BAD_REQUEST, "path {path}");
        }
    }

    for path in ["/v1/episodes", "/v1/reflect"] {
        let request = Request::builder()
            .method("POST")
            .uri(path)
            .header("content-type", "application/json")
            .header("idempotency-key", "first")
            .header("idempotency-key", "second")
            .body(Body::from("{}"))
            .expect("request");
        let response = app.clone().oneshot(request).await.expect("response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "path {path}");
    }
}

#[tokio::test]
async fn retain_and_reflect_replay_exact_http_receipts() {
    let app = dev_app(tenant(96_300));
    let binding = bind_context(&app, "mutation-replay").await;
    let retain = bind_episode_request(
        episode_request(binding.scope_id, binding.actor_id, "replay", None),
        &binding,
    );
    let reflect = ReflectRequest {
        subject_id: binding.subject_id,
        scope_id: binding.scope_id,
        actor_id: binding.actor_id,
        agent_node_id: binding.agent_node_id,
        subject_generation: binding.subject_generation,
    };

    for (path, key, expected_status, body) in [
        (
            "/v1/episodes",
            "rest-retain-replay",
            StatusCode::OK,
            serde_json::to_vec(&retain).expect("retain body"),
        ),
        (
            "/v1/reflect",
            "rest-reflect-replay",
            StatusCode::ACCEPTED,
            serde_json::to_vec(&reflect).expect("reflect body"),
        ),
    ] {
        let mut receipts = Vec::new();
        for _ in 0..2 {
            let request = Request::builder()
                .method("POST")
                .uri(path)
                .header("content-type", "application/json")
                .header("idempotency-key", key)
                .body(Body::from(body.clone()))
                .expect("request");
            let response = app.clone().oneshot(request).await.expect("response");
            let status = response.status();
            let bytes = response
                .into_body()
                .collect()
                .await
                .expect("body")
                .to_bytes();
            receipts.push((status, bytes));
        }
        assert_eq!(receipts[0].0, expected_status);
        assert_eq!(receipts[0], receipts[1]);
    }
}

async fn error_json_request<T: Serialize>(
    app: &axum::Router,
    method: &str,
    path: &str,
    body: T,
) -> (StatusCode, Value) {
    let request = add_idempotency_header(Request::builder().method(method).uri(path), path)
        .header("content-type", "application/json")
        .body(Body::from(
            serde_json::to_vec(&body).expect("serialize body"),
        ))
        .expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let bytes = response
        .into_body()
        .collect()
        .await
        .expect("body")
        .to_bytes();
    (status, serde_json::from_slice(&bytes).expect("error json"))
}

#[test]
fn openapi_document_refs_resolve() {
    let document = memphant_server::openapi_document();
    let mut refs = Vec::new();
    collect_refs(&document, &mut refs);

    assert!(!refs.is_empty(), "expected OpenAPI refs");
    for reference in refs {
        assert!(
            reference.starts_with("#/"),
            "external refs are not part of this checked-in snapshot: {reference}"
        );
        assert!(
            document
                .pointer(reference.trim_start_matches('#'))
                .is_some(),
            "dangling OpenAPI ref {reference}"
        );
    }
}

#[test]
fn openapi_component_schemas_are_codegen_friendly() {
    let document = memphant_server::openapi_document();
    let schemas = document["components"]["schemas"]
        .as_object()
        .expect("component schemas");
    let mut refs = Vec::new();
    collect_refs(&document, &mut refs);

    for (name, schema) in schemas {
        assert!(schema.get("$schema").is_none(), "{name} leaks $schema");
        assert!(schema.get("$defs").is_none(), "{name} leaks nested $defs");
    }
    for reference in refs {
        assert!(
            reference.starts_with("#/components/schemas/"),
            "OpenAPI ref should target a named component schema: {reference}"
        );
        assert_eq!(
            reference
                .trim_start_matches("#/components/schemas/")
                .split('/')
                .count(),
            1,
            "OpenAPI ref should target a top-level component schema: {reference}"
        );
        assert!(
            !reference.contains("/$defs/"),
            "OpenAPI ref should not target nested $defs: {reference}"
        );
    }
    for shared_schema in ["TenantId", "ScopeId", "ActorId", "UnitId", "TraceId"] {
        assert!(
            schemas.contains_key(shared_schema),
            "missing shared component schema {shared_schema}"
        );
    }
}

#[test]
fn openapi_snapshot_matches_generator() {
    let snapshot_path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../openapi/memphant.v1.json");
    let snapshot: Value =
        serde_json::from_str(&std::fs::read_to_string(snapshot_path).expect("snapshot"))
            .expect("snapshot json");

    assert_eq!(snapshot, memphant_server::openapi_document());
}

fn collect_refs(value: &Value, refs: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            if let Some(reference) = map.get("$ref").and_then(Value::as_str) {
                refs.push(reference.to_string());
            }
            for child in map.values() {
                collect_refs(child, refs);
            }
        }
        Value::Array(items) => {
            for child in items {
                collect_refs(child, refs);
            }
        }
        _ => {}
    }
}

fn path_template_names(path: &str) -> Vec<&str> {
    path.split('{')
        .skip(1)
        .filter_map(|part| part.split_once('}').map(|(name, _)| name))
        .collect()
}

async fn json_request<T, R>(
    app: &axum::Router,
    method: &str,
    path: &str,
    body: Option<T>,
) -> (StatusCode, R)
where
    T: Serialize,
    R: DeserializeOwned,
{
    let mut builder = add_idempotency_header(Request::builder().method(method).uri(path), path);
    let request = if let Some(body) = body {
        builder = builder.header("content-type", "application/json");
        builder
            .body(Body::from(
                serde_json::to_vec(&body).expect("serialize body"),
            ))
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
    assert!(
        status.is_success(),
        "status={status} body={}",
        String::from_utf8_lossy(&bytes)
    );
    (
        status,
        serde_json::from_slice(&bytes).expect("deserialize response"),
    )
}

fn scope_core_path(binding: &ContextBindingResponse, query: &str, token_budget: u32) -> String {
    format!(
        "/v1/scopes/{}/core?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}&query={}&token_budget={}",
        binding.scope_id.as_uuid(),
        binding.subject_id.as_uuid(),
        binding.actor_id.as_uuid(),
        binding.agent_node_id.as_uuid(),
        binding.subject_generation,
        query,
        token_budget,
    )
}

/// The conservative server-side token estimator's contract, restated here so
/// the budget assertions below are grounded in the same arithmetic.
fn conservative_tokens(text: &str) -> usize {
    text.split_whitespace().count().max(text.len().div_ceil(3))
}

#[tokio::test]
async fn scope_core_is_ranked_budget_fitted_deterministic_and_projection_anchored() {
    let tenant_id = tenant(97_000);
    let (app, state) = dev_app_with_state(tenant_id);
    let binding = bind_context(&app, "scope-core").await;
    let context = state
        .store()
        .resolve_memory_context(
            tenant_id,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve core context");

    let small_body = "The favorite deploy region is eu-west-1.";
    let bodies = [
        small_body,
        "A second favorite deploy region note pinning us-east-2 for batch workloads.",
        "A third favorite deploy region observation about ap-south-1 latency for the nightly sync.",
    ];
    let mut tx = state.store().begin(&context).await.expect("begin seed");
    for (index, body) in bodies.iter().enumerate() {
        state
            .store()
            .stage_memory_unit(
                &mut tx,
                active_projection_unit(
                    tenant_id,
                    &binding,
                    &format!("rest:scope-core:{index}"),
                    &format!("core:{index}"),
                    body,
                ),
            )
            .await
            .expect("stage core unit");
    }
    state.store().commit(tx).await.expect("commit seed");

    // A budget that covers everything returns every ranked item, never more
    // than the budget, and anchors to the projection's integrity semantics.
    let full_path = scope_core_path(&binding, "favorite%20deploy%20region", 1_000);
    let full: Value = json_request(&app, "GET", &full_path, None::<()>).await.1;
    let full_bodies: Vec<&str> = full["items"]
        .as_array()
        .expect("items")
        .iter()
        .map(|item| item["body"].as_str().expect("body"))
        .collect();
    assert_eq!(full_bodies.len(), 3, "{full}");
    for body in &full_bodies {
        assert!(bodies.contains(body), "mid-item truncation: {body}");
    }
    assert_eq!(full["subject_generation"], binding.subject_generation);
    assert_eq!(full["token_budget"], 1_000);
    assert_eq!(full["evaluated_at"], REST_TEST_CLOCK.0);
    assert_eq!(full["degraded"], false);

    let projection: CanonicalProjectionResponse = json_request(
        &app,
        "GET",
        &format!(
            "/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
            binding.scope_id.as_uuid(),
            binding.subject_id.as_uuid(),
            binding.actor_id.as_uuid(),
            binding.agent_node_id.as_uuid(),
            binding.subject_generation,
        ),
        None::<()>,
    )
    .await
    .1;
    assert_eq!(
        full["fingerprint"].as_str().expect("fingerprint"),
        projection.fingerprint,
        "core must carry the projection's fingerprint"
    );

    // A budget that only the smallest item fits under returns exactly that
    // item, whole: the server stops before the item that would exceed the
    // budget and never truncates mid-item.
    let small_budget = conservative_tokens(small_body) as u32;
    assert!(
        bodies[1..]
            .iter()
            .all(|body| conservative_tokens(body) > small_budget as usize)
    );
    let tight_path = scope_core_path(&binding, "favorite%20deploy%20region", small_budget);
    let tight: Value = json_request(&app, "GET", &tight_path, None::<()>).await.1;
    let tight_bodies: Vec<&str> = tight["items"]
        .as_array()
        .expect("items")
        .iter()
        .map(|item| item["body"].as_str().expect("body"))
        .collect();
    assert_eq!(tight_bodies, vec![small_body], "{tight}");

    // Deterministic: identical inputs produce an identical envelope.
    let repeat: Value = json_request(&app, "GET", &full_path, None::<()>).await.1;
    assert_eq!(repeat["items"], full["items"]);
    assert_eq!(repeat["fingerprint"], full["fingerprint"]);
}

#[tokio::test]
async fn scope_core_stale_subject_generation_fails_closed() {
    let tenant_id = tenant(97_100);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "scope-core-stale").await;

    let stale = format!(
        "/v1/scopes/{}/core?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}&query=q&token_budget=100",
        binding.scope_id.as_uuid(),
        binding.subject_id.as_uuid(),
        binding.actor_id.as_uuid(),
        binding.agent_node_id.as_uuid(),
        binding.subject_generation + 1,
    );
    let request = Request::builder()
        .method("GET")
        .uri(stale)
        .body(Body::empty())
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body: Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(body["error"]["code"], "context_binding_conflict");
}

/// The `serve_captures` query param is accepted (not rejected by
/// `deny_unknown_fields`) and yields a well-formed 200 — the wire contract the
/// Syndai coding-lane caller depends on. Behavior (captures actually served) is
/// proven at the service level in `capture_write_seam.rs`.
#[tokio::test]
async fn scope_core_accepts_the_serve_captures_query_param() {
    let tenant_id = tenant(97_300);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "scope-core-captures").await;

    let path = format!(
        "{}&serve_captures=true",
        scope_core_path(&binding, "q", 1_000)
    );
    let (status, body): (StatusCode, Value) = json_request(&app, "GET", &path, None::<()>).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(body["items"].is_array(), "{body}");
}

#[tokio::test]
async fn scope_core_rejects_a_zero_token_budget() {
    let tenant_id = tenant(97_200);
    let app = dev_app(tenant_id);
    let binding = bind_context(&app, "scope-core-zero").await;

    let request = Request::builder()
        .method("GET")
        .uri(scope_core_path(&binding, "q", 0))
        .body(Body::empty())
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let body: Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(body["error"]["code"], "invalid_request");
}
