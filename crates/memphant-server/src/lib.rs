use std::sync::Arc;

use axum::body::Body;
use axum::extract::{DefaultBodyLimit, FromRequest, FromRequestParts, Path, Query, Request, State};
use axum::http::request::Parts;
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use memphant_core::service::{
    MAX_CANONICAL_PROJECTION_ENCODED_BYTES, MemoryService, ServiceError, clamp_trust,
};
use memphant_core::{
    CoreError, InMemoryStore, MemoryStore, MutationLedgerStore, MutationResponse, NoopEmbedding,
    StoreError, SystemClock, validate_idempotency_key,
};
use memphant_types::{
    ActorId, AgentNodeId, CanonicalProjectionResponse, ContextBindingRequest,
    ContextBindingResponse, CorrectRequest, ENGINE_VERSION, ErrorBody, ErrorEnvelope,
    FileSyncRequest, FileSyncResult, HealthResponse, MAX_FILE_SYNC_REQUEST_ENCODED_BYTES,
    MarkRequest, RecallHttpRequest, ReflectAccepted, ReflectRequest, RetainEpisodeHttpRequest,
    RetainEpisodeHttpResponse, RetrievalTrace, SCHEMA_COMPAT_REVISION, ScopeId,
    ScopeMemoryResponse, SubjectId, TRACE_SCHEMA_VERSION, TaskMemoryEventsRequest,
    TaskMemoryEventsResult, TaskOutcomeRequest, TaskOutcomeResult, TenantId, TrustLevel,
};
use schemars::JsonSchema;
use schemars::generate::{SchemaGenerator, SchemaSettings};
use serde::Deserialize;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

const HEALTH_PATH: &str = "/v1/health";
const OPENAPI_PATH: &str = "/v1/openapi.json";
const EPISODES_PATH: &str = "/v1/episodes";
const RECALL_PATH: &str = "/v1/recall";
const REFLECT_PATH: &str = "/v1/reflect";
const CORRECT_PATH: &str = "/v1/correct";
const FORGET_PATH: &str = "/v1/forget";
const MARK_PATH: &str = "/v1/mark";
const TASK_OUTCOMES_PATH: &str = "/v1/task-outcomes";
const TASK_MEMORY_EVENTS_PATH: &str = "/v1/task-memory-events";
const FILE_SYNC_PATH: &str = "/v1/file-sync";
const TRACE_PATH: &str = "/v1/traces/{id}";
const SCOPE_MEMORY_PATH: &str = "/v1/scopes/{id}/memory";
const CANONICAL_PROJECTION_PATH: &str = "/v1/scopes/{id}/projection";
const CONTEXT_BINDING_PATH: &str = "/v1/context-bindings/{client_ref}";

const DOCUMENTED_OPENAPI_PATHS: &[&str] = &[
    EPISODES_PATH,
    RECALL_PATH,
    REFLECT_PATH,
    CORRECT_PATH,
    FORGET_PATH,
    MARK_PATH,
    TASK_OUTCOMES_PATH,
    TASK_MEMORY_EVENTS_PATH,
    FILE_SYNC_PATH,
    TRACE_PATH,
    SCOPE_MEMORY_PATH,
    CANONICAL_PROJECTION_PATH,
    CONTEXT_BINDING_PATH,
    HEALTH_PATH,
];

pub fn documented_openapi_paths() -> &'static [&'static str] {
    DOCUMENTED_OPENAPI_PATHS
}

/// Hashes a presented bearer token into the stored `api_key.key_hash` form.
pub fn api_key_hash(token: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(token.as_bytes());
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub struct AppState<S: MemoryStore> {
    service: MemoryService<S>,
    store_name: &'static str,
    dev_tenant: Option<TenantId>,
}

impl<S: MemoryStore> Clone for AppState<S> {
    fn clone(&self) -> Self {
        Self {
            service: self.service.clone(),
            store_name: self.store_name,
            dev_tenant: self.dev_tenant,
        }
    }
}

impl AppState<InMemoryStore> {
    pub fn new_in_memory() -> Self {
        Self::from_service(
            MemoryService::new(
                Arc::new(InMemoryStore::default()),
                Arc::new(SystemClock),
                Arc::new(NoopEmbedding),
            ),
            "memory",
        )
    }
}

impl<S: MemoryStore> AppState<S> {
    pub fn from_service(service: MemoryService<S>, store_name: &'static str) -> Self {
        Self {
            service,
            store_name,
            dev_tenant: None,
        }
    }

    /// Dev mode: binds ALL requests to this tenant, ignoring body tenant ids.
    /// Wired from `MEMPHANT_DEV_TENANT` in `main`; loud by design.
    pub fn with_dev_tenant(mut self, tenant: TenantId) -> Self {
        eprintln!(
            "memphant-server: AUTH DISABLED (dev) — all requests bound to tenant {}",
            tenant.as_uuid()
        );
        self.dev_tenant = Some(tenant);
        self
    }

    pub fn service(&self) -> &MemoryService<S> {
        &self.service
    }

    pub fn store(&self) -> &S {
        self.service.store()
    }
}

/// The tenant binding + trust ceiling resolved from `Authorization: Bearer
/// mk_…` (or the dev-mode tenant). All tenant-scoped reads/writes are bound
/// server-side to this value, never to client-declared body fields.
#[derive(Debug, Clone, Copy)]
pub struct AuthedTenant {
    pub tenant: TenantId,
    pub max_trust: TrustLevel,
    actor_id: Option<memphant_types::ActorId>,
    scope_id: Option<memphant_types::ScopeId>,
    /// Permanent-erasure capability, resolved from the live key. Default false;
    /// coding-agent keys never receive it. Gated at the forget handler.
    can_forget: bool,
    /// Historical-audit capability. Default false; gated on any request carrying
    /// an explicit `transaction_as_of` or `valid_at` selector.
    can_audit_history: bool,
    dev_mode: bool,
}

struct StrictJson<T>(T);

struct FileSyncJson(FileSyncRequest);

struct IdempotencyKey(String);

impl<S: Send + Sync> FromRequestParts<S> for IdempotencyKey {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        let mut values = parts.headers.get_all("idempotency-key").iter();
        let value = values
            .next()
            .ok_or_else(|| ApiError::bad_request("Idempotency-Key header is required"))?;
        if values.next().is_some() {
            return Err(ApiError::bad_request(
                "exactly one Idempotency-Key header is required",
            ));
        }
        let key = value
            .to_str()
            .map_err(|_| ApiError::bad_request("Idempotency-Key must be valid UTF-8"))?;
        validate_idempotency_key(key)
            .map_err(|_| ApiError::bad_request("Idempotency-Key must contain 1 to 255 bytes"))?;
        Ok(Self(key.to_string()))
    }
}

impl<S, T> FromRequest<S> for StrictJson<T>
where
    S: Send + Sync,
    T: DeserializeOwned,
{
    type Rejection = ApiError;

    async fn from_request(request: Request, state: &S) -> Result<Self, Self::Rejection> {
        Json::<T>::from_request(request, state)
            .await
            .map(|Json(value)| Self(value))
            .map_err(|error| ApiError::invalid(error.body_text()))
    }
}

impl<S> FromRequest<S> for FileSyncJson
where
    S: Send + Sync,
{
    type Rejection = ApiError;

    async fn from_request(request: Request, state: &S) -> Result<Self, Self::Rejection> {
        Json::<FileSyncRequest>::from_request(request, state)
            .await
            .map(|Json(value)| Self(value))
            .map_err(|error| ApiError::sync_invalid(error.body_text()))
    }
}

impl AuthedTenant {
    fn check_principal(
        &self,
        actor_id: memphant_types::ActorId,
        scope_id: memphant_types::ScopeId,
    ) -> Result<(), ApiError> {
        if self.dev_mode
            || (self.actor_id.is_none() && self.scope_id.is_none())
            || (self.actor_id == Some(actor_id) && self.scope_id == Some(scope_id))
        {
            Ok(())
        } else {
            Err(ApiError::scope_denied())
        }
    }

    fn check_scope(&self, scope_id: memphant_types::ScopeId) -> Result<(), ApiError> {
        if self.dev_mode || self.scope_id.is_none() || self.scope_id == Some(scope_id) {
            Ok(())
        } else {
            Err(ApiError::scope_denied())
        }
    }

    fn require_tenant_service_key(&self) -> Result<(), ApiError> {
        if self.dev_mode || (self.actor_id.is_none() && self.scope_id.is_none()) {
            Ok(())
        } else {
            Err(ApiError::scope_denied())
        }
    }

    /// Permanent erasure is owner-only. The absence of an MCP delete tool is a
    /// convenience, not the boundary; this capability check is.
    fn require_can_forget(&self) -> Result<(), ApiError> {
        if self.can_forget {
            Ok(())
        } else {
            Err(ApiError::capability_denied("can_forget"))
        }
    }

    /// Any request supplying an explicit `transaction_as_of`/`valid_at` selector
    /// is a historical-audit read and requires the audit capability. Ordinary
    /// coding recall (no selector) never reaches this gate.
    fn require_can_audit_history(&self) -> Result<(), ApiError> {
        if self.can_audit_history {
            Ok(())
        } else {
            Err(ApiError::capability_denied("can_audit_history"))
        }
    }
}

impl<S: MemoryStore + 'static> FromRequestParts<AppState<S>> for AuthedTenant {
    type Rejection = ApiError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState<S>,
    ) -> Result<Self, Self::Rejection> {
        if let Some(tenant) = state.dev_tenant {
            return Ok(Self {
                tenant,
                max_trust: TrustLevel::TrustedSystem,
                actor_id: None,
                scope_id: None,
                can_forget: true,
                can_audit_history: true,
                dev_mode: true,
            });
        }
        let header = parts
            .headers
            .get(axum::http::header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(ApiError::auth_required)?;
        let token = header
            .strip_prefix("Bearer ")
            .filter(|token| token.starts_with("mk_"))
            .ok_or_else(ApiError::auth_required)?;
        let row = state
            .service
            .store()
            .lookup_api_key(&api_key_hash(token))
            .await
            .map_err(|_| ApiError::backend_unavailable())?
            .ok_or_else(ApiError::auth_required)?;
        if row.revoked {
            return Err(ApiError::auth_required());
        }
        Ok(Self {
            tenant: row.tenant_id,
            max_trust: row.max_trust,
            actor_id: row.actor_id,
            scope_id: row.scope_id,
            can_forget: row.can_forget,
            can_audit_history: row.can_audit_history,
            dev_mode: false,
        })
    }
}

pub fn app<S: MutationLedgerStore + 'static>(state: AppState<S>) -> Router {
    Router::new()
        .route(HEALTH_PATH, get(health::<S>))
        .route(OPENAPI_PATH, get(openapi))
        .route(EPISODES_PATH, post(retain_handler::<S>))
        .route(RECALL_PATH, post(recall_handler::<S>))
        .route(REFLECT_PATH, post(reflect_handler::<S>))
        .route(CORRECT_PATH, post(correct_handler::<S>))
        .route(FORGET_PATH, post(forget_handler::<S>))
        .route(MARK_PATH, post(mark_handler::<S>))
        .route(TASK_OUTCOMES_PATH, post(task_outcome_handler::<S>))
        .route(
            TASK_MEMORY_EVENTS_PATH,
            post(task_memory_events_handler::<S>),
        )
        .route(
            FILE_SYNC_PATH,
            post(file_sync_handler::<S>)
                .layer(DefaultBodyLimit::max(MAX_FILE_SYNC_REQUEST_ENCODED_BYTES)),
        )
        .route(TRACE_PATH, get(trace_handler::<S>))
        .route(SCOPE_MEMORY_PATH, get(scope_memory_handler::<S>))
        .route(
            CANONICAL_PROJECTION_PATH,
            get(canonical_projection_handler::<S>),
        )
        .route(CONTEXT_BINDING_PATH, put(context_binding_handler::<S>))
        .with_state(state)
}

async fn context_binding_handler<S: MemoryStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    Path(client_ref): Path<String>,
    StrictJson(request): StrictJson<ContextBindingRequest>,
) -> Result<Json<ContextBindingResponse>, ApiError> {
    authed.require_tenant_service_key()?;
    validate_context_binding(&client_ref, &request)?;
    let response = state
        .store()
        .resolve_context_binding(authed.tenant, client_ref, request)
        .await
        .map_err(ApiError::from)?;
    Ok(Json(response))
}

fn validate_context_binding(
    client_ref: &str,
    request: &ContextBindingRequest,
) -> Result<(), ApiError> {
    if [
        client_ref,
        request.subject.external_ref.as_str(),
        request.actor.external_ref.as_str(),
        request.scope.external_ref.as_str(),
        request.agent_node.external_ref.as_str(),
    ]
    .iter()
    .any(|value| value.trim().is_empty())
    {
        return Err(ApiError::invalid(
            "context binding references cannot be empty",
        ));
    }
    if !matches!(
        request.actor.kind.as_str(),
        "user" | "agent" | "tool" | "web" | "system"
    ) {
        return Err(ApiError::invalid("unsupported actor kind"));
    }
    for (index, policy) in request.access_policies.iter().enumerate() {
        if policy.source_scope_external_ref().trim().is_empty()
            || policy.source_agent_node_external_ref().trim().is_empty()
        {
            return Err(ApiError::invalid(
                "access policy source references cannot be empty",
            ));
        }
        if request.access_policies[..index].iter().any(|existing| {
            existing.source_scope_external_ref() == policy.source_scope_external_ref()
                && existing.source_agent_node_external_ref()
                    == policy.source_agent_node_external_ref()
                && existing.kind() == policy.kind()
        }) {
            return Err(ApiError::invalid("duplicate access policy"));
        }
    }
    Ok(())
}

async fn health<S: MemoryStore + 'static>(
    State(state): State<AppState<S>>,
) -> Json<HealthResponse> {
    let db_ok = state.store().ping().await.is_ok();
    let dead_letter_jobs = state.store().dead_letter_count().await.ok();
    Json(HealthResponse {
        status: if db_ok { "ok" } else { "degraded" }.to_string(),
        store: state.store_name.to_string(),
        dead_letter_jobs,
        engine_version: ENGINE_VERSION.to_string(),
        trace_schema_version: TRACE_SCHEMA_VERSION.to_string(),
        schema_compat_revision: SCHEMA_COMPAT_REVISION.to_string(),
    })
}

async fn openapi() -> Json<Value> {
    Json(openapi_document())
}

async fn retain_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    StrictJson(request): StrictJson<RetainEpisodeHttpRequest>,
) -> Result<Response, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    mutation_http_response(
        state
            .service
            .retain(
                &context,
                &idempotency_key,
                clamp_trust(context.actor_trust, authed.max_trust),
                request,
            )
            .await?,
    )
}

async fn reflect_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    StrictJson(request): StrictJson<ReflectRequest>,
) -> Result<Response, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    mutation_http_response(
        state
            .service
            .reflect(&context, &idempotency_key, request)
            .await?,
    )
}

async fn recall_handler<S: MemoryStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    StrictJson(request): StrictJson<RecallHttpRequest>,
) -> Result<Json<memphant_types::RecallResponse>, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    // Any explicit historical selector turns recall into an audit read: gate it
    // on the audit capability before touching the store, so an ordinary coding
    // key cannot recover stale/superseded/expired bytes by supplying an earlier
    // time.
    if request.transaction_as_of.is_some() || request.valid_at.is_some() {
        authed.require_can_audit_history()?;
    }
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    Ok(Json(state.service.recall(context, request).await?))
}

async fn correct_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    StrictJson(request): StrictJson<CorrectRequest>,
) -> Result<Response, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    mutation_http_response(
        state
            .service
            .correct(&context, &idempotency_key, request)
            .await?,
    )
}

async fn forget_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    StrictJson(request): StrictJson<memphant_types::ForgetRequest>,
) -> Result<Response, ApiError> {
    authed.require_can_forget()?;
    if request.scope_id != request.selector.scope_id {
        return Err(ApiError::context_binding_conflict(
            "forget scope does not match selector scope".to_string(),
        ));
    }
    authed.check_principal(request.actor_id, request.selector.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    mutation_http_response(
        state
            .service
            .forget(&context, &idempotency_key, request)
            .await?,
    )
}

async fn file_sync_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    FileSyncJson(request): FileSyncJson,
) -> Result<Response, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    let mut context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::sync_conflict("subject generation is stale"));
    }
    context.actor_trust = clamp_trust(context.actor_trust, authed.max_trust);
    mutation_http_response(
        state
            .service
            .file_sync(&context, &idempotency_key, request)
            .await?,
    )
}

async fn mark_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    StrictJson(request): StrictJson<MarkRequest>,
) -> Result<Response, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    mutation_http_response(
        state
            .service
            .mark(&context, &idempotency_key, request)
            .await?,
    )
}

async fn task_outcome_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    StrictJson(request): StrictJson<TaskOutcomeRequest>,
) -> Result<Response, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    mutation_http_response(
        state
            .service
            .record_task_outcome(&context, &idempotency_key, request)
            .await?,
    )
}

async fn task_memory_events_handler<S: MutationLedgerStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    IdempotencyKey(idempotency_key): IdempotencyKey,
    StrictJson(request): StrictJson<TaskMemoryEventsRequest>,
) -> Result<Response, ApiError> {
    authed.check_principal(request.actor_id, request.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            request.subject_id,
            request.actor_id,
            request.scope_id,
            request.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if request.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    mutation_http_response(
        state
            .service
            .record_task_memory_events(&context, &idempotency_key, request)
            .await?,
    )
}

fn mutation_http_response(response: MutationResponse) -> Result<Response, ApiError> {
    let status =
        StatusCode::from_u16(response.status()).map_err(|_| ApiError::backend_unavailable())?;
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(response.body().to_vec()))
        .map_err(|_| ApiError::backend_unavailable())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TraceContextQuery {
    subject_id: SubjectId,
    scope_id: ScopeId,
    actor_id: ActorId,
    agent_node_id: AgentNodeId,
    subject_generation: u64,
}

async fn trace_handler<S: MemoryStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    Path(id): Path<String>,
    Query(query): Query<TraceContextQuery>,
) -> Result<Json<RetrievalTrace>, ApiError> {
    let uuid = Uuid::parse_str(&id).map_err(|_| ApiError::invalid("invalid trace id"))?;
    let trace_id = memphant_types::TraceId::from_u128(uuid.as_u128());
    authed.check_principal(query.actor_id, query.scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            query.subject_id,
            query.actor_id,
            query.scope_id,
            query.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if query.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    let trace = state
        .service
        .trace(&context, trace_id)
        .await?
        .ok_or_else(|| ApiError::not_found("trace"))?;
    authed.check_principal(trace.actor_id, trace.scope_id)?;
    Ok(Json(trace))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ScopeMemoryQuery {
    subject_id: SubjectId,
    actor_id: ActorId,
    agent_node_id: AgentNodeId,
    subject_generation: u64,
    #[serde(default)]
    cursor: Option<Uuid>,
    #[serde(default)]
    limit: Option<usize>,
    /// Optional memory-kind filter (e.g. "preference"); applied per page, so a
    /// filtered page may return fewer than `limit` items while `has_more` is true.
    #[serde(default)]
    kind: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CanonicalProjectionQuery {
    subject_id: SubjectId,
    actor_id: ActorId,
    agent_node_id: AgentNodeId,
    subject_generation: u64,
}

async fn scope_memory_handler<S: MemoryStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    Path(id): Path<String>,
    Query(query): Query<ScopeMemoryQuery>,
) -> Result<Json<ScopeMemoryResponse>, ApiError> {
    let uuid = Uuid::parse_str(&id).map_err(|_| ApiError::invalid("invalid scope id"))?;
    let scope_id = memphant_types::ScopeId::from_u128(uuid.as_u128());
    authed.check_scope(scope_id)?;
    authed.check_principal(query.actor_id, scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            query.subject_id,
            query.actor_id,
            scope_id,
            query.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if query.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    let cursor = query
        .cursor
        .map(|cursor| memphant_types::UnitId::from_u128(cursor.as_u128()));
    let limit = query.limit.unwrap_or(100).clamp(1, 500);
    let kind_filter = query
        .kind
        .as_deref()
        .map(|k| {
            serde_json::from_value::<memphant_types::MemoryKind>(serde_json::Value::String(
                k.to_string(),
            ))
            .map_err(|_| ApiError::invalid("invalid kind"))
        })
        .transpose()?;
    let page = state
        .service
        .scope_memory_page(&context, cursor, limit)
        .await?;
    let items = match kind_filter {
        Some(kind) => page.items.into_iter().filter(|u| u.kind == kind).collect(),
        None => page.items,
    };
    Ok(Json(ScopeMemoryResponse {
        tenant_id: authed.tenant,
        scope_id,
        items,
        next_cursor: page.next_cursor.map(|cursor| cursor.as_uuid().to_string()),
        has_more: page.has_more,
    }))
}

async fn canonical_projection_handler<S: MemoryStore + 'static>(
    State(state): State<AppState<S>>,
    authed: AuthedTenant,
    Path(id): Path<String>,
    Query(query): Query<CanonicalProjectionQuery>,
) -> Result<Json<CanonicalProjectionResponse>, ApiError> {
    let uuid = Uuid::parse_str(&id).map_err(|_| ApiError::invalid("invalid scope id"))?;
    let scope_id = ScopeId::from_u128(uuid.as_u128());
    authed.check_scope(scope_id)?;
    authed.check_principal(query.actor_id, scope_id)?;
    let context = state
        .store()
        .resolve_memory_context(
            authed.tenant,
            query.subject_id,
            query.actor_id,
            scope_id,
            query.agent_node_id,
        )
        .await
        .map_err(|error| match error {
            StoreError::NotFound(_) => ApiError::scope_denied(),
            other => ApiError::from(other),
        })?;
    if query.subject_generation != context.subject_generation {
        return Err(ApiError::context_binding_conflict(
            "subject generation is stale".to_string(),
        ));
    }
    Ok(Json(state.service.canonical_projection(&context).await?))
}

pub fn openapi_document() -> Value {
    json!({
        "openapi": "3.1.0",
        "info": {
            "title": "MemPhant API",
            "version": ENGINE_VERSION
        },
        "paths": openapi_paths(),
        "security": [ { "bearerApiKey": [] } ],
        "components": {
            "securitySchemes": {
                "bearerApiKey": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "mk_<key>",
                    "description": "MemPhant API key; the key binds the tenant and caps the bound actor trust at its max_trust tier."
                }
            },
            "schemas": component_schemas()
        }
    })
}

fn openapi_paths() -> serde_json::Map<String, Value> {
    let mut paths = serde_json::Map::new();
    paths.insert(
        EPISODES_PATH.to_string(),
        mutation_path_item_with_success_status(
            "RetainEpisodeHttpRequest",
            "RetainEpisodeHttpResponse",
            "200",
        ),
    );
    paths.insert(
        RECALL_PATH.to_string(),
        path_item("post", "RecallHttpRequest", "RecallResponse"),
    );
    paths.insert(
        REFLECT_PATH.to_string(),
        mutation_path_item_with_success_status("ReflectRequest", "ReflectAccepted", "202"),
    );
    paths.insert(
        CORRECT_PATH.to_string(),
        mutation_path_item("CorrectRequest", "CorrectResult"),
    );
    paths.insert(
        FORGET_PATH.to_string(),
        mutation_path_item("ForgetRequest", "ForgetResult"),
    );
    paths.insert(
        MARK_PATH.to_string(),
        mutation_path_item("MarkRequest", "MarkResult"),
    );
    paths.insert(
        TASK_OUTCOMES_PATH.to_string(),
        mutation_path_item("TaskOutcomeRequest", "TaskOutcomeResult"),
    );
    paths.insert(
        TASK_MEMORY_EVENTS_PATH.to_string(),
        mutation_path_item("TaskMemoryEventsRequest", "TaskMemoryEventsResult"),
    );
    paths.insert(
        FILE_SYNC_PATH.to_string(),
        mutation_path_item("FileSyncRequest", "FileSyncResult"),
    );
    paths.insert(
        TRACE_PATH.to_string(),
        get_path_item("RetrievalTrace", vec![path_param("id")]),
    );
    paths.insert(
        SCOPE_MEMORY_PATH.to_string(),
        get_path_item(
            "ScopeMemoryResponse",
            vec![
                path_param("id"),
                required_query_param("subject_id", "uuid"),
                required_query_param("actor_id", "uuid"),
                required_query_param("agent_node_id", "uuid"),
                required_query_param("subject_generation", "integer"),
                optional_query_param("cursor", "uuid"),
                optional_query_param("limit", "integer"),
                optional_query_param("kind", "string"),
            ],
        ),
    );
    let mut canonical_projection = get_path_item(
        "CanonicalProjectionResponse",
        vec![
            path_param("id"),
            required_query_param("subject_id", "uuid"),
            required_query_param("actor_id", "uuid"),
            required_query_param("agent_node_id", "uuid"),
            required_query_param("subject_generation", "integer"),
        ],
    );
    canonical_projection["get"]["description"] = json!(format!(
        "Returns the complete bitemporally-current canonical projection at one server-clock instant, returned as evaluated_at. Responses exceeding {MAX_CANONICAL_PROJECTION_ENCODED_BYTES} encoded JSON bytes fail with 413 and are never truncated."
    ));
    paths.insert(CANONICAL_PROJECTION_PATH.to_string(), canonical_projection);
    paths.insert(
        HEALTH_PATH.to_string(),
        get_path_item("HealthResponse", Vec::new()),
    );
    paths.insert(
        CONTEXT_BINDING_PATH.to_string(),
        path_item_with_params(
            "put",
            "ContextBindingRequest",
            "ContextBindingResponse",
            vec![string_path_param("client_ref")],
        ),
    );
    paths
}

fn component_schemas() -> serde_json::Map<String, Value> {
    let mut generator = openapi_schema_generator();
    seed_component::<RetainEpisodeHttpRequest>(&mut generator);
    seed_component::<RetainEpisodeHttpResponse>(&mut generator);
    seed_component::<RecallHttpRequest>(&mut generator);
    seed_component::<memphant_types::RecallResponse>(&mut generator);
    seed_component::<ReflectRequest>(&mut generator);
    seed_component::<ReflectAccepted>(&mut generator);
    seed_component::<CorrectRequest>(&mut generator);
    seed_component::<memphant_types::CorrectResult>(&mut generator);
    seed_component::<memphant_types::ForgetRequest>(&mut generator);
    seed_component::<memphant_types::ForgetResult>(&mut generator);
    seed_component::<MarkRequest>(&mut generator);
    seed_component::<TaskOutcomeRequest>(&mut generator);
    seed_component::<TaskOutcomeResult>(&mut generator);
    seed_component::<TaskMemoryEventsRequest>(&mut generator);
    seed_component::<TaskMemoryEventsResult>(&mut generator);
    seed_component::<memphant_types::MarkResult>(&mut generator);
    seed_component::<FileSyncRequest>(&mut generator);
    seed_component::<FileSyncResult>(&mut generator);
    seed_component::<RetrievalTrace>(&mut generator);
    seed_component::<ScopeMemoryResponse>(&mut generator);
    seed_component::<CanonicalProjectionResponse>(&mut generator);
    seed_component::<HealthResponse>(&mut generator);
    seed_component::<ContextBindingRequest>(&mut generator);
    seed_component::<ContextBindingResponse>(&mut generator);
    seed_component::<ErrorEnvelope>(&mut generator);
    generator.take_definitions(true)
}

fn openapi_schema_generator() -> SchemaGenerator {
    SchemaSettings::draft2020_12()
        .with(|settings| {
            settings.definitions_path = "/components/schemas".into();
            settings.meta_schema = None;
        })
        .into_generator()
}

fn seed_component<T: JsonSchema>(generator: &mut SchemaGenerator) {
    let _ = generator.subschema_for::<T>();
}

fn path_item(method: &str, input_schema: &str, output_schema: &str) -> Value {
    path_item_with_success_status(method, input_schema, output_schema, "200")
}

fn path_item_with_success_status(
    method: &str,
    input_schema: &str,
    output_schema: &str,
    success_status: &str,
) -> Value {
    json!({
        method: {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": { "$ref": format!("#/components/schemas/{input_schema}") }
                    }
                }
            },
            "responses": {
                success_status: {
                    "content": {
                        "application/json": {
                            "schema": { "$ref": format!("#/components/schemas/{output_schema}") }
                        }
                    }
                },
                "default": {
                    "content": {
                        "application/json": {
                            "schema": { "$ref": "#/components/schemas/ErrorEnvelope" }
                        }
                    }
                }
            }
        }
    })
}

fn mutation_path_item(input_schema: &str, output_schema: &str) -> Value {
    mutation_path_item_with_success_status(input_schema, output_schema, "200")
}

fn mutation_path_item_with_success_status(
    input_schema: &str,
    output_schema: &str,
    success_status: &str,
) -> Value {
    let mut item =
        path_item_with_success_status("post", input_schema, output_schema, success_status);
    item["post"]["parameters"] = json!([{
        "name": "Idempotency-Key",
        "in": "header",
        "required": true,
        "schema": { "type": "string", "minLength": 1, "maxLength": 255 }
    }]);
    item
}

fn path_item_with_params(
    method: &str,
    input_schema: &str,
    output_schema: &str,
    parameters: Vec<Value>,
) -> Value {
    let mut item = path_item(method, input_schema, output_schema);
    item[method]["parameters"] = Value::Array(parameters);
    item
}

fn get_path_item(output_schema: &str, parameters: Vec<Value>) -> Value {
    json!({
        "get": {
            "parameters": parameters,
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": { "$ref": format!("#/components/schemas/{output_schema}") }
                        }
                    }
                },
                "default": {
                    "content": {
                        "application/json": {
                            "schema": { "$ref": "#/components/schemas/ErrorEnvelope" }
                        }
                    }
                }
            }
        }
    })
}

fn path_param(name: &str) -> Value {
    json!({
        "name": name,
        "in": "path",
        "required": true,
        "schema": { "type": "string", "format": "uuid" }
    })
}

fn string_path_param(name: &str) -> Value {
    json!({
        "name": name,
        "in": "path",
        "required": true,
        "schema": { "type": "string" }
    })
}

fn optional_query_param(name: &str, format: &str) -> Value {
    let schema = if format == "integer" {
        json!({ "type": "integer" })
    } else {
        json!({ "type": "string", "format": format })
    };
    json!({
        "name": name,
        "in": "query",
        "required": false,
        "schema": schema
    })
}

fn required_query_param(name: &str, format: &str) -> Value {
    let mut parameter = optional_query_param(name, format);
    parameter["required"] = Value::Bool(true);
    parameter
}

pub struct ApiError {
    status: StatusCode,
    code: &'static str,
    message: String,
}

impl ApiError {
    fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            code: "invalid_request",
            message: message.into(),
        }
    }

    fn invalid(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            code: "invalid_request",
            message: message.into(),
        }
    }

    fn not_found(entity: &str) -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            code: "not_found",
            message: format!("{entity} not found"),
        }
    }

    fn auth_required() -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            code: "auth_required",
            message: "a valid Authorization: Bearer mk_<key> header is required".to_string(),
        }
    }

    fn capability_denied(capability: &'static str) -> Self {
        Self {
            status: StatusCode::FORBIDDEN,
            code: "capability_denied",
            message: format!(
                "this API key lacks the {capability} capability; it is owner-only and default false"
            ),
        }
    }

    fn scope_denied() -> Self {
        Self {
            status: StatusCode::FORBIDDEN,
            code: "scope_denied",
            message: "actor_id or scope_id is outside the API key principal binding".to_string(),
        }
    }

    fn backend_unavailable() -> Self {
        Self {
            status: StatusCode::SERVICE_UNAVAILABLE,
            code: "backend_unavailable",
            message: "backend unavailable".to_string(),
        }
    }

    fn context_binding_conflict(message: String) -> Self {
        Self {
            status: StatusCode::CONFLICT,
            code: "context_binding_conflict",
            message,
        }
    }

    fn projection_too_large(max_bytes: usize) -> Self {
        Self {
            status: StatusCode::PAYLOAD_TOO_LARGE,
            code: "projection_too_large",
            message: format!("canonical projection exceeds {max_bytes} encoded bytes"),
        }
    }

    fn sync_invalid(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            code: "sync_invalid",
            message: message.into(),
        }
    }

    fn sync_conflict(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::CONFLICT,
            code: "sync_conflict",
            message: message.into(),
        }
    }

    fn conflict(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::CONFLICT,
            code,
            message: message.into(),
        }
    }
}

impl From<StoreError> for ApiError {
    fn from(error: StoreError) -> Self {
        match error {
            StoreError::Conflict(message) => Self::context_binding_conflict(message),
            StoreError::PolicyDenied(message) => Self {
                status: StatusCode::FORBIDDEN,
                code: "scope_denied",
                message,
            },
            StoreError::IdempotencyConflict => Self::conflict(
                "idempotency_conflict",
                "idempotency key was already used for a different mutation",
            ),
            StoreError::StaleSubjectGeneration => {
                Self::conflict("stale_subject_generation", "subject generation is stale")
            }
            StoreError::SubjectErased => Self {
                status: StatusCode::GONE,
                code: "subject_erased",
                message: "subject has been erased".to_string(),
            },
            StoreError::NotFound(entity) => Self::not_found(entity),
            StoreError::SerializationConflict => {
                Self::sync_conflict("serializable transaction conflicted")
            }
            StoreError::TransactionAlreadyCommitted
            | StoreError::Poisoned
            | StoreError::Backend(_) => Self::backend_unavailable(),
        }
    }
}

impl From<CoreError> for ApiError {
    fn from(error: CoreError) -> Self {
        match error {
            CoreError::EmptyBody | CoreError::Invalid(_) => Self {
                status: StatusCode::UNPROCESSABLE_ENTITY,
                code: "invalid_request",
                message: error.to_string(),
            },
            CoreError::NotFound(_) => Self {
                status: StatusCode::NOT_FOUND,
                code: "not_found",
                message: error.to_string(),
            },
            CoreError::PolicyDenied(_) => Self {
                status: StatusCode::FORBIDDEN,
                code: "scope_denied",
                message: error.to_string(),
            },
            CoreError::DeepUnavailable => Self {
                status: StatusCode::SERVICE_UNAVAILABLE,
                code: "deep_unavailable",
                message: error.to_string(),
            },
            CoreError::DeepProviderInvalidOutput => Self {
                status: StatusCode::BAD_GATEWAY,
                code: "deep_provider_invalid_output",
                message: error.to_string(),
            },
            CoreError::Store(store) => store.into(),
            CoreError::ProviderUnavailable(_) | CoreError::ProviderInvalid(_) => {
                Self::backend_unavailable()
            }
        }
    }
}

impl From<ServiceError> for ApiError {
    fn from(error: ServiceError) -> Self {
        match error {
            ServiceError::Core(core) => core.into(),
            ServiceError::Invalid(message) => Self::invalid(message),
            ServiceError::SyncInvalid(message) => Self::sync_invalid(message),
            ServiceError::SyncConflict(message) => Self::sync_conflict(message),
            ServiceError::ProjectionTooLarge { max_bytes } => Self::projection_too_large(max_bytes),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let body = ErrorEnvelope {
            error: ErrorBody {
                code: self.code.to_string(),
                message: self.message,
                request_id: "req_local".to_string(),
                details: json!({}),
            },
        };
        (self.status, Json(body)).into_response()
    }
}

#[cfg(test)]
mod error_mapping_tests {
    use super::{ApiError, CoreError, StatusCode, StoreError};

    #[test]
    fn subject_safety_errors_have_stable_public_status_and_code() {
        let cases = [
            (
                StoreError::IdempotencyConflict,
                StatusCode::CONFLICT,
                "idempotency_conflict",
            ),
            (
                StoreError::StaleSubjectGeneration,
                StatusCode::CONFLICT,
                "stale_subject_generation",
            ),
            (
                StoreError::SubjectErased,
                StatusCode::GONE,
                "subject_erased",
            ),
            (
                StoreError::PolicyDenied("denied".to_string()),
                StatusCode::FORBIDDEN,
                "scope_denied",
            ),
        ];

        for (source, expected_status, expected_code) in cases {
            let error = ApiError::from(source);
            assert_eq!(error.status, expected_status);
            assert_eq!(error.code, expected_code);
        }

        let wrapped = ApiError::from(CoreError::Store(StoreError::SubjectErased));
        assert_eq!(wrapped.status, StatusCode::GONE);
        assert_eq!(wrapped.code, "subject_erased");
    }

    #[test]
    fn deep_provider_errors_have_stable_public_status_and_code() {
        let unavailable = ApiError::from(CoreError::DeepUnavailable);
        assert_eq!(unavailable.status, StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(unavailable.code, "deep_unavailable");

        let invalid = ApiError::from(CoreError::DeepProviderInvalidOutput);
        assert_eq!(invalid.status, StatusCode::BAD_GATEWAY);
        assert_eq!(invalid.code, "deep_provider_invalid_output");
    }
}
