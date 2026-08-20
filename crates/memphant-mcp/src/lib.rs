//! MemPhant MCP server on rmcp 3.1 (MCP 2026-07-28, stateless streamable-HTTP
//! with legacy-client compatibility): five portable memory tools and
//! tenant-bound memory resources over the
//! shared `MemoryService<AnyStore>`, a persistent stdio session, and an
//! optional streamable-HTTP transport. The tenant is fixed at startup from
//! `MEMPHANT_API_KEY` (sha256 → api_key lookup) or `MEMPHANT_DEV_TENANT`
//! (dev) — stdio is a per-principal transport; a missing/revoked key refuses
//! to start rather than serving an unauthenticated session.

use memphant_core::service::{MemoryService, ServiceError, clamp_trust, trust_rank};
use memphant_core::{CoreError, MemoryStore, MutationResponse, StoreError};
use memphant_runtime::AnyStore;
use memphant_types::{
    AgentNodeId, CorrectResult, ENGINE_VERSION, MarkResult, RecallHttpRequest, RecallResponse,
    ResolvedMemoryContext, RetainEpisodeHttpResponse, ScopeId, SubjectId, TenantId, TrustLevel,
};
use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::handler::server::wrapper::Parameters;
use rmcp::model::{
    CacheScope, CallToolResult, Implementation, ListResourceTemplatesResult, ListResourcesResult,
    PaginatedRequestParams, ReadResourceRequestParams, ReadResourceResponse, ReadResourceResult,
    Resource, ResourceContents, ResourceTemplate, ServerCapabilities, ServerInfo,
};
use rmcp::service::RequestContext;
use rmcp::{ErrorData, Json, RoleServer, ServerHandler, tool, tool_handler, tool_router};
use schemars::JsonSchema;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

mod file_memory;
pub mod http;
pub use file_memory::{
    ANTHROPIC_MEMORY_TOOL_TYPE, MAX_DIRECTORY_BYTES, MAX_DIRECTORY_ENTRIES, MAX_MEMORY_INDEX_BYTES,
    MAX_MEMORY_INDEX_LINES, MAX_RESOURCE_BYTES, MAX_TOPIC_BYTES, MAX_VIEW_CHARACTERS, MEMORY_ROOT,
    MemoryCommand, MemoryProjection, MemoryResourceContent, MemoryToolError, anthropic_memory_tool,
    memory_index, memory_resource_uri, parse_memory_resource_uri, resource_page, topic_path,
};

/// Hashes a presented API key into the stored `api_key.key_hash` form
/// (identical to the REST edge).
pub fn api_key_hash(token: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(token.as_bytes());
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Client-facing error string for MCP tools, mirroring the REST edge: raw
/// backend/store errors are hidden behind a generic message; validation,
/// not-found, and policy errors carry caller-relevant, safe-to-surface text.
pub fn mcp_error(error: ServiceError) -> String {
    match error {
        ServiceError::Core(CoreError::Store(StoreError::IdempotencyConflict)) => {
            "idempotency_conflict: key was already used with a different request".to_string()
        }
        ServiceError::Core(CoreError::Store(StoreError::StaleSubjectGeneration)) => {
            "stale_subject_generation: subject generation is stale".to_string()
        }
        ServiceError::Core(CoreError::Store(StoreError::SubjectErased)) => {
            "subject_erased: subject has been erased".to_string()
        }
        ServiceError::Core(CoreError::Store(StoreError::PolicyDenied(_))) => {
            "scope_denied: request is outside the resolved memory policy".to_string()
        }
        ServiceError::Core(CoreError::DeepUnavailable) => {
            "deep_unavailable: deep recall is unavailable".to_string()
        }
        ServiceError::Core(CoreError::DeepProviderInvalidOutput) => {
            "deep_provider_invalid_output: deep recall provider returned invalid output".to_string()
        }
        ServiceError::Core(CoreError::Store(_)) => "backend unavailable".to_string(),
        other => other.to_string(),
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct McpMutation<T> {
    #[schemars(length(min = 1, max = 255))]
    idempotency_key: String,
    request: T,
}

fn decode_mutation_response<T: DeserializeOwned>(
    response: MutationResponse,
) -> Result<Json<T>, String> {
    serde_json::from_slice(response.body())
        .map(Json)
        .map_err(|_| "backend unavailable".to_string())
}

/// Constant-time string equality (length may leak). Compares a presented bearer
/// token to the process key without a timing side channel.
pub fn constant_time_eq(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Whether an MCP streamable-HTTP request is authorized. Dev mode (auth
/// explicitly disabled and logged loudly at startup) allows all; otherwise the
/// `Authorization: Bearer <token>` header must equal the process key. This
/// gives the HTTP transport the same per-request gate the REST edge has, so a
/// widened `MEMPHANT_MCP_BIND` never serves the bound tenant unauthenticated.
pub fn mcp_http_authorized(
    dev_mode: bool,
    expected_key: Option<&str>,
    auth_header: Option<&str>,
) -> bool {
    if dev_mode {
        return true;
    }
    let Some(expected) = expected_key else {
        return false;
    };
    let Some(token) = auth_header.and_then(|header| {
        header
            .strip_prefix("Bearer ")
            .or_else(|| header.strip_prefix("bearer "))
    }) else {
        return false;
    };
    constant_time_eq(token.trim(), expected.trim())
}

/// The tenant binding resolved at startup. Stdio serves exactly one
/// principal; there is no per-request Authorization header.
#[derive(Debug, Clone)]
pub struct BoundTenant {
    pub tenant: TenantId,
    pub max_trust: TrustLevel,
    pub subject_id: Option<SubjectId>,
    pub subject_generation: Option<u64>,
    pub actor_id: Option<memphant_types::ActorId>,
    pub scope_id: Option<ScopeId>,
    pub agent_node_id: Option<AgentNodeId>,
    pub api_key_id: Option<uuid::Uuid>,
    /// The presented key's hash. Recall rechecks this row on every call so a
    /// persistent stdio session cannot outlive key revocation.
    pub api_key_hash: Option<String>,
    pub dev_mode: bool,
}

/// The fully bound principal resolved live on a single MCP call. Carries the
/// authorized context plus the live key's id, trust ceiling, and the two
/// operation capabilities. Returned by `live_principal()`; the capabilities are
/// default-false and coding-agent keys never receive them.
#[derive(Debug, Clone)]
pub struct LivePrincipal {
    pub context: ResolvedMemoryContext,
    pub api_key_id: uuid::Uuid,
    pub max_trust: TrustLevel,
    pub can_forget: bool,
    pub can_audit_history: bool,
}

/// Resolves the fixed tenant from the environment:
/// - `MEMPHANT_DEV_TENANT=<uuid>` → dev mode (loud, trust ceiling
///   `trusted_system`, body tenant ids ignored);
/// - `MEMPHANT_API_KEY=mk_…` → sha256 → `api_key` lookup (missing or revoked
///   → error: the process must refuse to start);
/// - neither → error.
pub async fn resolve_tenant(store: &AnyStore) -> Result<BoundTenant, String> {
    if let Ok(raw) = std::env::var("MEMPHANT_DEV_TENANT")
        && !raw.trim().is_empty()
    {
        let uuid = uuid::Uuid::parse_str(raw.trim())
            .map_err(|error| format!("MEMPHANT_DEV_TENANT must be a UUID: {error}"))?;
        let tenant = TenantId::from_u128(uuid.as_u128());
        eprintln!(
            "memphant-mcp: AUTH DISABLED (dev) — all tool calls bound to tenant {}",
            tenant.as_uuid()
        );
        return Ok(BoundTenant {
            tenant,
            max_trust: TrustLevel::TrustedSystem,
            subject_id: None,
            subject_generation: None,
            actor_id: None,
            scope_id: None,
            agent_node_id: None,
            api_key_id: None,
            api_key_hash: None,
            dev_mode: true,
        });
    }
    let key = std::env::var("MEMPHANT_API_KEY").ok().filter(|key| !key.trim().is_empty()).ok_or_else(|| {
        "no tenant binding: set MEMPHANT_API_KEY=mk_<key> (or MEMPHANT_DEV_TENANT=<uuid> for dev); refusing to start an unauthenticated MCP session".to_string()
    })?;
    let row = store
        .lookup_api_key(&api_key_hash(key.trim()))
        .await
        .map_err(|error| format!("api key lookup failed: {error}"))?
        .ok_or_else(|| {
            "MEMPHANT_API_KEY does not match any api_key row; refusing to start".to_string()
        })?;
    if row.revoked {
        return Err("MEMPHANT_API_KEY is revoked; refusing to start".to_string());
    }
    Ok(BoundTenant {
        tenant: row.tenant_id,
        max_trust: row.max_trust,
        subject_id: row.data_subject_id,
        subject_generation: row.subject_generation,
        actor_id: row.actor_id,
        scope_id: row.scope_id,
        agent_node_id: row.agent_node_id,
        api_key_id: Some(row.id),
        api_key_hash: Some(row.key_hash),
        dev_mode: false,
    })
}

const MCP_RECALL_BUDGET_TOKENS: usize = 512;

/// How many compact items the coding-agent recall lane may serve per call.
///
/// Peer injection systems (memori, mem0, claude-mem, ...) default to 5–10
/// budgeted items; we were serving exactly one, so every trace showed
/// `dropped: output_limit` and validated procedures never travelled together.
/// The 512-token budget above is the binding ceiling — three items is what fits
/// without diluting precision (SWE-ContextBench: misfired context is
/// net-negative), and the harness renders each with a confirmed/unconfirmed
/// label. Hard cap 5.
const MCP_RECALL_ITEM_LIMIT: usize = 3;
const _: () = assert!(MCP_RECALL_ITEM_LIMIT <= 5);

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct McpRecallRequest {
    query: String,
}

#[derive(Debug, Clone, Copy, Serialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
enum McpRecallErrorCode {
    AuthRequired,
    ScopeDenied,
    BackendUnavailable,
    StaleSubjectGeneration,
    SubjectErased,
    NotFound,
    InvalidRequest,
}

#[derive(Debug, Serialize, JsonSchema)]
struct McpRecallError {
    code: McpRecallErrorCode,
    message: &'static str,
}

#[derive(Debug, Serialize, JsonSchema)]
#[serde(tag = "state", rename_all = "snake_case")]
#[schemars(extend("type" = "object"))]
enum McpRecallOutput {
    Hit {
        #[serde(flatten)]
        response: RecallResponse,
    },
    Empty {
        #[serde(flatten)]
        response: RecallResponse,
    },
    Unavailable {
        error: McpRecallError,
    },
    Error {
        error: McpRecallError,
    },
}

enum McpRecallFailure {
    Auth(&'static str),
    Scope(&'static str),
    Unavailable,
}

impl McpRecallFailure {
    fn result(self) -> CallToolResult {
        match self {
            Self::Auth(message) => mcp_recall_error(McpRecallErrorCode::AuthRequired, message),
            Self::Scope(message) => mcp_recall_error(McpRecallErrorCode::ScopeDenied, message),
            Self::Unavailable => mcp_recall_error(
                McpRecallErrorCode::BackendUnavailable,
                "memory store unavailable",
            ),
        }
    }

    /// String rendering for the `Result<_, String>` mutation-tool surface, so a
    /// live-principal failure on a capability-gated tool returns the same typed
    /// prefix the other mutation errors use.
    fn as_error_string(&self) -> String {
        match self {
            Self::Auth(message) => format!("auth_required: {message}"),
            Self::Scope(message) => format!("scope_denied: {message}"),
            Self::Unavailable => "backend_unavailable: memory store unavailable".to_string(),
        }
    }
}

fn mcp_recall_error(code: McpRecallErrorCode, message: &'static str) -> CallToolResult {
    let output = match code {
        McpRecallErrorCode::BackendUnavailable => McpRecallOutput::Unavailable {
            error: McpRecallError { code, message },
        },
        _ => McpRecallOutput::Error {
            error: McpRecallError { code, message },
        },
    };
    CallToolResult::structured_error(
        serde_json::to_value(output).expect("typed MCP recall error serializes"),
    )
}

fn mcp_recall_service_error(error: ServiceError) -> CallToolResult {
    let (code, message) = match error {
        ServiceError::Core(error) => match error {
            CoreError::EmptyBody
            | CoreError::Invalid(_)
            | CoreError::ProviderInvalid(_)
            | CoreError::DeepUnavailable
            | CoreError::DeepProviderInvalidOutput => (
                McpRecallErrorCode::InvalidRequest,
                "recall request is invalid",
            ),
            CoreError::NotFound(_) => (
                McpRecallErrorCode::NotFound,
                "requested memory context was not found",
            ),
            CoreError::PolicyDenied(_) => (
                McpRecallErrorCode::ScopeDenied,
                "request is outside the resolved memory policy",
            ),
            CoreError::ProviderUnavailable(_) => (
                McpRecallErrorCode::BackendUnavailable,
                "memory store unavailable",
            ),
            CoreError::Store(error) => match error {
                StoreError::Poisoned
                | StoreError::SerializationConflict
                | StoreError::Backend(_) => (
                    McpRecallErrorCode::BackendUnavailable,
                    "memory store unavailable",
                ),
                StoreError::NotFound(_) => (
                    McpRecallErrorCode::NotFound,
                    "requested memory context was not found",
                ),
                StoreError::PolicyDenied(_) => (
                    McpRecallErrorCode::ScopeDenied,
                    "request is outside the resolved memory policy",
                ),
                StoreError::StaleSubjectGeneration => (
                    McpRecallErrorCode::StaleSubjectGeneration,
                    "subject generation is stale; restart with a current bound API key",
                ),
                StoreError::SubjectErased => {
                    (McpRecallErrorCode::SubjectErased, "subject has been erased")
                }
                StoreError::TransactionAlreadyCommitted
                | StoreError::Conflict(_)
                | StoreError::IdempotencyConflict => (
                    McpRecallErrorCode::InvalidRequest,
                    "recall request is invalid",
                ),
            },
        },
        ServiceError::Invalid(_)
        | ServiceError::SyncInvalid(_)
        | ServiceError::SyncConflict(_)
        | ServiceError::ProjectionTooLarge { .. } => (
            McpRecallErrorCode::InvalidRequest,
            "recall request is invalid",
        ),
    };
    mcp_recall_error(code, message)
}

/// The MCP tool surface: seven verbs over the shared application layer.
#[derive(Clone)]
pub struct MemphantMcp {
    service: MemoryService<AnyStore>,
    recall_service: MemoryService<AnyStore>,
    bound: BoundTenant,
    tool_router: ToolRouter<Self>,
}

impl MemphantMcp {
    /// Whether the process resolved a dev tenant (auth explicitly disabled).
    /// The HTTP transport uses this to decide whether to enforce per-request
    /// bearer auth.
    pub fn dev_mode(&self) -> bool {
        self.bound.dev_mode
    }

    async fn memory_projection(&self) -> Result<MemoryProjection, MemoryToolError> {
        let (
            Some(subject_id),
            Some(subject_generation),
            Some(actor_id),
            Some(scope_id),
            Some(agent_node_id),
        ) = (
            self.bound.subject_id,
            self.bound.subject_generation,
            self.bound.actor_id,
            self.bound.scope_id,
            self.bound.agent_node_id,
        )
        else {
            return Err(MemoryToolError {
                code: "scope_denied",
                message: "MCP resources and memory files require a fully context-bound API key"
                    .to_string(),
            });
        };
        let mut context = self
            .service
            .store()
            .resolve_memory_context(
                self.bound.tenant,
                subject_id,
                actor_id,
                scope_id,
                agent_node_id,
            )
            .await
            .map_err(|error| {
                let (code, message) = match error {
                    StoreError::NotFound(_) => ("scope_denied", "unresolved memory context"),
                    _ => ("backend_unavailable", "memory store unavailable"),
                };
                MemoryToolError {
                    code,
                    message: message.to_string(),
                }
            })?;
        if context.subject_generation != subject_generation {
            return Err(MemoryToolError {
                code: "context_binding_conflict",
                message: "subject generation is stale".to_string(),
            });
        }
        context.actor_trust = clamp_trust(context.actor_trust, self.bound.max_trust);
        Ok(MemoryProjection::new(self.service.clone(), context))
    }

    /// Executes one GA Anthropic `memory_20250818` command over the same
    /// canonical projection and atomic file-sync path as `memphant compile`.
    pub async fn handle_memory_command(
        &self,
        command: MemoryCommand,
    ) -> Result<String, MemoryToolError> {
        self.memory_projection().await?.handle(command).await
    }

    pub async fn read_bound_resource(
        &self,
        uri: &str,
    ) -> Result<MemoryResourceContent, MemoryToolError> {
        self.memory_projection().await?.read_resource(uri).await
    }
}

#[tool_router(router = tool_router)]
impl MemphantMcp {
    pub fn new(service: MemoryService<AnyStore>, bound: BoundTenant) -> Self {
        Self {
            recall_service: service.ambient_free_recall_clone(),
            service,
            bound,
            tool_router: Self::tool_router(),
        }
    }

    /// Re-resolve the fully bound principal on THIS call. Every startup binding
    /// is re-looked-up and compared: a revoked key, binding drift, subject-
    /// generation drift, or a *raised* live trust ceiling fails closed and asks
    /// for restart; a lowered ceiling applies immediately. Startup-cached
    /// identity (`self.bound`) is comparison state, never continuing authority.
    /// This is the one resolver behind recall, resource reads, and the
    /// capability-gated tools — the split `bind_principal`/startup-`self.bound`
    /// path is not authority on its own.
    async fn live_principal(&self) -> Result<LivePrincipal, McpRecallFailure> {
        if self.bound.dev_mode {
            return Err(McpRecallFailure::Scope(
                "MCP recall requires a fully context-bound API key; set MEMPHANT_API_KEY to a key bound to subject, generation, actor, scope, and agent node",
            ));
        }
        let Some(key_hash) = self.bound.api_key_hash.as_deref() else {
            return Err(McpRecallFailure::Auth(
                "MCP recall requires an active fully context-bound API key; restart with MEMPHANT_API_KEY",
            ));
        };
        let row = self
            .service
            .store()
            .lookup_api_key(key_hash)
            .await
            .map_err(|_| McpRecallFailure::Unavailable)?
            .ok_or(McpRecallFailure::Auth(
                "API key is no longer valid; restart with an active fully context-bound API key",
            ))?;
        if row.revoked {
            return Err(McpRecallFailure::Auth(
                "API key is revoked; restart with an active fully context-bound API key",
            ));
        }
        if self.bound.api_key_id.is_some_and(|id| id != row.id)
            || self.bound.tenant != row.tenant_id
            || self.bound.subject_id != row.data_subject_id
            || self.bound.subject_generation != row.subject_generation
            || self.bound.actor_id != row.actor_id
            || self.bound.scope_id != row.scope_id
            || self.bound.agent_node_id != row.agent_node_id
            // A process may safely honor a lower live ceiling, but it must not
            // silently acquire a broader authority after startup.
            || trust_rank(row.max_trust) > trust_rank(self.bound.max_trust)
        {
            return Err(McpRecallFailure::Scope(
                "API key principal changed after MCP startup; restart with a newly bound API key",
            ));
        }
        let (
            Some(subject_id),
            Some(subject_generation),
            Some(actor_id),
            Some(scope_id),
            Some(agent_node_id),
        ) = (
            row.data_subject_id,
            row.subject_generation,
            row.actor_id,
            row.scope_id,
            row.agent_node_id,
        )
        else {
            return Err(McpRecallFailure::Scope(
                "MCP recall requires an API key bound to subject, generation, actor, scope, and agent node",
            ));
        };
        let mut context = self
            .service
            .store()
            .resolve_memory_context(row.tenant_id, subject_id, actor_id, scope_id, agent_node_id)
            .await
            .map_err(|error| match error {
                StoreError::NotFound(_) => McpRecallFailure::Scope(
                    "API key binding does not resolve a live memory context; issue a key for that context",
                ),
                _ => McpRecallFailure::Unavailable,
            })?;
        if context.subject_generation != subject_generation {
            return Err(McpRecallFailure::Scope(
                "API key subject generation is stale; issue a key for the current context",
            ));
        }
        context.actor_trust = clamp_trust(context.actor_trust, row.max_trust);
        Ok(LivePrincipal {
            context,
            api_key_id: row.id,
            max_trust: row.max_trust,
            can_forget: row.can_forget,
            can_audit_history: row.can_audit_history,
        })
    }

    async fn recall_context(&self) -> Result<ResolvedMemoryContext, McpRecallFailure> {
        Ok(self.live_principal().await?.context)
    }

    #[tool(
        description = "Create exactly one self-contained, compact, typed memory (identity-free; the server derives all identity from the live key).",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn remember(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<memphant_types::RememberRequest>>,
    ) -> Result<Json<RetainEpisodeHttpResponse>, String> {
        let live = self
            .live_principal()
            .await
            .map_err(|error| error.as_error_string())?;
        let response = self
            .service
            .remember(
                &live.context,
                &idempotency_key,
                live.context.actor_trust,
                request,
            )
            .await
            .map_err(mcp_error)?;
        decode_mutation_response(response)
    }

    #[tool(
        description = "Retrieve cited memory evidence for a query (budgeted, salience-ranked, with provenance).",
        output_schema = rmcp::handler::server::tool::schema_for_type::<McpRecallOutput>(),
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = false,
            open_world_hint = false
        )
    )]
    async fn recall(&self, Parameters(request): Parameters<McpRecallRequest>) -> CallToolResult {
        let context = match self.recall_context().await {
            Ok(context) => context,
            Err(error) => return error.result(),
        };
        let request = RecallHttpRequest {
            // This is the portable coding-agent lane: only typed compact
            // envelopes are eligible, and Active procedural compact units are
            // served. Beliefs are served too: cross-harness CAPTURE mints
            // `kind=Belief` candidates, so excluding beliefs here would make
            // captured coding memory permanently un-injectable on this lane.
            compact_only: true,
            serve_captures: false,
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            query: request.query,
            limit: Some(MCP_RECALL_ITEM_LIMIT),
            budget_tokens: Some(MCP_RECALL_BUDGET_TOKENS),
            mode: Some(memphant_types::RecallMode::Fast),
            include_beliefs: Some(true),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        };
        let response = match self.recall_service.recall(context, request).await {
            Ok(response) => response,
            Err(error) => return mcp_recall_service_error(error),
        };
        let output = if response.items.is_empty() {
            McpRecallOutput::Empty { response }
        } else {
            McpRecallOutput::Hit { response }
        };
        CallToolResult::structured(
            serde_json::to_value(output).expect("MCP recall response serializes"),
        )
    }

    #[tool(
        description = "Append a corrected bitemporal successor to one open memory (or an open invalidation tombstone) selected by id.",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn correct_memory(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<memphant_types::CorrectMemoryRequest>>,
    ) -> Result<Json<CorrectResult>, String> {
        let live = self
            .live_principal()
            .await
            .map_err(|error| error.as_error_string())?;
        decode_mutation_response(
            self.service
                .correct_memory(
                    &live.context,
                    &idempotency_key,
                    live.context.actor_trust,
                    request,
                )
                .await
                .map_err(mcp_error)?,
        )
    }

    #[tool(
        description = "Archive one open memory as stale or harmful; a bodyless tombstone blocks re-derivation until an explicit correction.",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn invalidate_memory(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<memphant_types::InvalidateMemoryRequest>>,
    ) -> Result<Json<CorrectResult>, String> {
        let live = self
            .live_principal()
            .await
            .map_err(|error| error.as_error_string())?;
        decode_mutation_response(
            self.service
                .invalidate_memory(&live.context, &idempotency_key, request)
                .await
                .map_err(mcp_error)?,
        )
    }

    #[tool(
        description = "Report how a recall pack was used (used, ignored, corrected, or failed); ranking evidence only.",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn report_memory_use(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<memphant_types::ReportMemoryUseRequest>>,
    ) -> Result<Json<MarkResult>, String> {
        let live = self
            .live_principal()
            .await
            .map_err(|error| error.as_error_string())?;
        // Reporter identity is derived server-side from the live key, never
        // caller-supplied.
        let reporter_id = live.api_key_id.to_string();
        decode_mutation_response(
            self.service
                .report_memory_use(&live.context, &idempotency_key, reporter_id, request)
                .await
                .map_err(mcp_error)?,
        )
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for MemphantMcp {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(
            ServerCapabilities::builder()
                .enable_tools()
                .enable_resources()
                .build(),
        )
            .with_server_info(Implementation::new("memphant", ENGINE_VERSION))
            .with_instructions(
                "MemPhant memory service: five portable memory tools (recall, remember, correct_memory, invalidate_memory, report_memory_use) plus read-only tenant-bound memory resources.",
            )
    }

    async fn list_resources(
        &self,
        request: Option<PaginatedRequestParams>,
        context: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        let projection = self.memory_projection().await.map_err(memory_mcp_error)?;
        let snapshot = projection.snapshot().await.map_err(memory_mcp_error)?;
        let (items, next_cursor) = resource_page(
            &snapshot,
            request
                .as_ref()
                .and_then(|request| request.cursor.as_deref()),
            100,
        )
        .map_err(memory_mcp_error)?;
        let mut result = ListResourcesResult::with_all_items(
            items
                .into_iter()
                .map(|item| {
                    Resource::new(
                        memory_resource_uri(item.unit_id),
                        item.fact_key
                            .clone()
                            .unwrap_or_else(|| item.unit_id.as_uuid().to_string()),
                    )
                    .with_title(
                        item.fact_key
                            .clone()
                            .unwrap_or_else(|| item.unit_id.as_uuid().to_string()),
                    )
                    .with_description(format!("Governed {:?} memory unit", item.kind))
                    .with_mime_type("text/markdown")
                    .with_size(item.body.len() as u64)
                })
                .collect(),
        );
        result.next_cursor = next_cursor;
        if supports_cache_hints(&context) {
            // Tenant memory listing: private to this principal, short freshness
            // window (SEP-2549 requires the fields for 2026-07-28 peers).
            result = result
                .with_ttl_ms(RESOURCE_TTL_MS)
                .with_cache_scope(CacheScope::Private);
        }
        Ok(result)
    }

    async fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        context: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResponse, ErrorData> {
        let content = self
            .read_bound_resource(&request.uri)
            .await
            .map_err(memory_mcp_error)?;
        let mut result = ReadResourceResult::new(vec![
            ResourceContents::text(content.text, content.uri).with_mime_type(content.mime_type),
        ]);
        if supports_cache_hints(&context) {
            result = result
                .with_ttl_ms(RESOURCE_TTL_MS)
                .with_cache_scope(CacheScope::Private);
        }
        Ok(result.into())
    }

    async fn list_resource_templates(
        &self,
        _request: Option<PaginatedRequestParams>,
        context: RequestContext<RoleServer>,
    ) -> Result<ListResourceTemplatesResult, ErrorData> {
        let mut result = ListResourceTemplatesResult::with_all_items(resource_templates());
        if supports_cache_hints(&context) {
            // Templates are static per build: shared caching is safe, generous ttl.
            result = result
                .with_ttl_ms(TEMPLATE_TTL_MS)
                .with_cache_scope(CacheScope::Public);
        }
        Ok(result)
    }
}

/// Freshness window for tenant-bound resource listings/reads (SEP-2549).
const RESOURCE_TTL_MS: u64 = 15_000;
/// Freshness window for the static resource-template list (SEP-2549).
const TEMPLATE_TTL_MS: u64 = 300_000;

/// Whether the peer negotiated 2026-07-28+ and therefore expects the
/// `ttlMs`/`cacheScope` cache hints (mirrors the `#[tool_handler]`-generated
/// `tools/list` gating).
fn supports_cache_hints(context: &RequestContext<RoleServer>) -> bool {
    context
        .protocol_version()
        .is_some_and(|version| version >= rmcp::model::ProtocolVersion::V_2026_07_28)
}

fn memory_mcp_error(error: MemoryToolError) -> ErrorData {
    match error.code {
        "not_found" => ErrorData::resource_not_found(error.message, None),
        "backend_unavailable" => ErrorData::internal_error("memory store unavailable", None),
        _ => ErrorData::invalid_params(error.to_string(), None),
    }
}

/// The committed `mcp/memphant.tools.v1.json` artifact: rmcp's own tool list
/// (camelCase `inputSchema`/`outputSchema`), never hand-edited.
pub fn tools_artifact() -> Value {
    serde_json::to_value(MemphantMcp::tool_router().list_all()).expect("MCP tools serialize")
}

pub fn resource_templates() -> Vec<ResourceTemplate> {
    ["memory", "trace", "episode", "resource"]
        .into_iter()
        .map(|kind| {
            ResourceTemplate::new(
                format!("memphant://{kind}/{{id}}"),
                format!("memphant-{kind}"),
            )
            .with_title(format!("MemPhant {kind}"))
            .with_description("Tenant-bound read-only MemPhant resource")
        })
        .collect()
}

/// The generated MCP resources artifact. It records only declared protocol
/// capability and stable templates; tenant data is never emitted at build time.
pub fn resources_artifact() -> Value {
    serde_json::json!({
        "capabilities": {"resources": {}},
        "resourceTemplates": resource_templates(),
    })
}

#[cfg(test)]
mod recall_wire_contract {
    use super::*;
    use memphant_core::{ApiKeyRow, InMemoryStore, NoopEmbedding, SystemClock};
    use memphant_runtime::AnyStore;
    use memphant_types::{ActorId, MemoryKind, ScopeId, TenantId, TrustLevel};
    use std::sync::Arc;

    fn mapped(error: ServiceError) -> serde_json::Value {
        mcp_recall_service_error(error)
            .structured_content
            .expect("structured recall error")
    }

    #[test]
    fn unavailable_recall_is_a_typed_tool_result() {
        let result = McpRecallFailure::Unavailable.result();
        assert_eq!(result.is_error, Some(true));
        assert_eq!(
            result.structured_content.expect("structured unavailable"),
            serde_json::json!({
                "state": "unavailable",
                "error": {"code": "backend_unavailable", "message": "memory store unavailable"},
            })
        );
    }

    #[test]
    fn recall_error_mapping_preserves_retryable_and_terminal_codes() {
        for error in [
            ServiceError::Core(CoreError::Store(StoreError::Backend("down".to_string()))),
            ServiceError::Core(CoreError::Store(StoreError::Poisoned)),
            ServiceError::Core(CoreError::Store(StoreError::SerializationConflict)),
            ServiceError::Core(CoreError::ProviderUnavailable("down".to_string())),
        ] {
            let result = mapped(error);
            assert_eq!(result["state"], "unavailable");
            assert_eq!(result["error"]["code"], "backend_unavailable");
        }

        for (error, code) in [
            (
                ServiceError::Core(CoreError::Store(StoreError::PolicyDenied(
                    "denied".to_string(),
                ))),
                "scope_denied",
            ),
            (
                ServiceError::Core(CoreError::PolicyDenied("denied".to_string())),
                "scope_denied",
            ),
            (
                ServiceError::Core(CoreError::Store(StoreError::StaleSubjectGeneration)),
                "stale_subject_generation",
            ),
            (
                ServiceError::Core(CoreError::Store(StoreError::SubjectErased)),
                "subject_erased",
            ),
            (
                ServiceError::Core(CoreError::NotFound("unit".to_string())),
                "not_found",
            ),
            (
                ServiceError::Core(CoreError::Store(StoreError::NotFound("unit"))),
                "not_found",
            ),
            (
                ServiceError::Core(CoreError::Store(StoreError::TransactionAlreadyCommitted)),
                "invalid_request",
            ),
            (
                ServiceError::Core(CoreError::Store(StoreError::Conflict(
                    "conflict".to_string(),
                ))),
                "invalid_request",
            ),
            (
                ServiceError::Core(CoreError::Store(StoreError::IdempotencyConflict)),
                "invalid_request",
            ),
            (ServiceError::Core(CoreError::EmptyBody), "invalid_request"),
            (
                ServiceError::Core(CoreError::Invalid("bad".to_string())),
                "invalid_request",
            ),
            (ServiceError::Invalid("bad".to_string()), "invalid_request"),
            (
                ServiceError::Core(CoreError::ProviderInvalid("bad".to_string())),
                "invalid_request",
            ),
            (
                ServiceError::Core(CoreError::DeepProviderInvalidOutput),
                "invalid_request",
            ),
            (
                ServiceError::Core(CoreError::DeepUnavailable),
                "invalid_request",
            ),
            (
                ServiceError::SyncInvalid("bad".to_string()),
                "invalid_request",
            ),
            (
                ServiceError::SyncConflict("bad".to_string()),
                "invalid_request",
            ),
            (
                ServiceError::ProjectionTooLarge { max_bytes: 1 },
                "invalid_request",
            ),
        ] {
            let result = mapped(error);
            assert_eq!(result["state"], "error", "{code} is terminal");
            assert_eq!(result["error"]["code"], code);
        }
    }

    #[tokio::test]
    async fn lower_live_trust_ceiling_clamps_the_recalled_principal() {
        let tenant = TenantId::new();
        let scope = ScopeId::new();
        let actor = ActorId::new();
        let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
        let store = InMemoryStore::default();
        store.seed_context_binding(&context);
        let key_id = uuid::Uuid::new_v4();
        let key_hash = "mcp-live-trust-ceiling".to_string();
        store.insert_api_key(ApiKeyRow {
            id: key_id,
            tenant_id: tenant,
            key_hash: key_hash.clone(),
            label: "live trust ceiling".to_string(),
            max_trust: TrustLevel::VerifiedTool,
            data_subject_id: Some(context.data_subject_id),
            subject_generation: Some(context.subject_generation),
            actor_id: Some(context.actor_id),
            scope_id: Some(context.scope_id),
            agent_node_id: Some(context.agent_node_id),
            can_forget: false,
            can_audit_history: false,
            revoked: false,
        });
        let mcp = MemphantMcp::new(
            MemoryService::new(
                Arc::new(AnyStore::Mem(store)),
                Arc::new(SystemClock),
                Arc::new(NoopEmbedding),
            ),
            BoundTenant {
                tenant,
                max_trust: TrustLevel::TrustedUser,
                subject_id: Some(context.data_subject_id),
                subject_generation: Some(context.subject_generation),
                actor_id: Some(context.actor_id),
                scope_id: Some(context.scope_id),
                agent_node_id: Some(context.agent_node_id),
                api_key_id: Some(key_id),
                api_key_hash: Some(key_hash),
                dev_mode: false,
            },
        );

        let resolved = match mcp.recall_context().await {
            Ok(context) => context,
            Err(_) => panic!("lower ceiling remains valid"),
        };
        assert_eq!(resolved.actor_trust, TrustLevel::VerifiedTool);
    }

    /// One bound tenant over an in-memory store: the service (for seeding
    /// through sanctioned write paths) plus the `BoundTenant` an MCP session
    /// would resolve. `max_trust` is the api key ceiling.
    fn bound_fixture(
        key_hash: &str,
        max_trust: TrustLevel,
    ) -> (
        memphant_types::ResolvedMemoryContext,
        MemoryService<AnyStore>,
        BoundTenant,
    ) {
        let tenant = TenantId::new();
        let scope = ScopeId::new();
        let actor = ActorId::new();
        let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
        let store = InMemoryStore::default();
        store.seed_context_binding(&context);
        let key_id = uuid::Uuid::new_v4();
        store.insert_api_key(ApiKeyRow {
            id: key_id,
            tenant_id: tenant,
            key_hash: key_hash.to_string(),
            label: key_hash.to_string(),
            max_trust,
            data_subject_id: Some(context.data_subject_id),
            subject_generation: Some(context.subject_generation),
            actor_id: Some(actor),
            scope_id: Some(scope),
            agent_node_id: Some(context.agent_node_id),
            can_forget: false,
            can_audit_history: false,
            revoked: false,
        });
        let service = MemoryService::new(
            Arc::new(AnyStore::Mem(store)),
            Arc::new(SystemClock),
            Arc::new(NoopEmbedding),
        );
        let bound = BoundTenant {
            tenant,
            max_trust,
            subject_id: Some(context.data_subject_id),
            subject_generation: Some(context.subject_generation),
            actor_id: Some(actor),
            scope_id: Some(scope),
            agent_node_id: Some(context.agent_node_id),
            api_key_id: Some(key_id),
            api_key_hash: Some(key_hash.to_string()),
            dev_mode: false,
        };
        (context, service, bound)
    }

    /// Seed one compact procedural memory through the sanctioned write path.
    async fn seed_procedure(
        service: &MemoryService<AnyStore>,
        context: &memphant_types::ResolvedMemoryContext,
        idempotency_key: &str,
        trigger: &str,
        body: &str,
    ) {
        service
            .remember(
                context,
                idempotency_key,
                TrustLevel::TrustedSystem,
                memphant_types::RememberRequest {
                    kind: MemoryKind::Procedural,
                    body: body.to_string(),
                    trigger: trigger.to_string(),
                    verification: "the consumer workflow creates the expected job".to_string(),
                    target_scope_id: None,
                    valid_from: None,
                    valid_to: None,
                    source: memphant_types::MemorySourceInput {
                        kind: "user".to_string(),
                        r#ref: format!("test:{idempotency_key}"),
                        observed_at: "2026-08-14T00:00:00Z".to_string(),
                        episode_id: None,
                        resource_id: None,
                    },
                },
            )
            .await
            .expect("seed compact procedure");
    }

    async fn recall_structured(mcp: &MemphantMcp, query: &str) -> Value {
        let result = mcp
            .recall(Parameters(McpRecallRequest {
                query: query.to_string(),
            }))
            .await;
        assert_ne!(result.is_error, Some(true), "recall succeeds");
        result.structured_content.expect("structured recall")
    }

    #[tokio::test]
    async fn recall_delivers_the_complete_validated_procedure_in_source_order() {
        const BODY_CHUNK: &str = "BODY_SENTINEL recall-budget-anchor. Inspect the consumer workflow and zero-job run before choosing the integration boundary. Preserve the exact repository ref and determine whether failure occurred during workflow resolution. This context is deliberately padded so the old MCP budget leaves no room for later procedure steps.";
        const ACTION_CHUNK: &str = "ACTION_SENTINEL package the required gate as a versioned step-level action, then invoke it from the consumer workflow.";
        const CHECK_CHUNK: &str = "CHECK_SENTINEL exercise the consumer call site and confirm the workflow creates the expected job before accepting the change.";

        let (context, service, bound) =
            bound_fixture("mcp-recall-budget", TrustLevel::TrustedSystem);
        // Its single body carries the three sentinels in source order, so the
        // compact-only MCP recall lane serves the complete procedure.
        seed_procedure(
            &service,
            &context,
            "mcp-recall-budget-seed",
            "recall budget anchor",
            &[BODY_CHUNK, ACTION_CHUNK, CHECK_CHUNK].join("\n"),
        )
        .await;
        let mcp = MemphantMcp::new(service, bound);

        let structured = recall_structured(&mcp, "recall budget anchor").await;
        assert_eq!(structured["state"], "hit", "recall envelope: {structured}");
        let body = structured
            .pointer("/items/0/body")
            .and_then(Value::as_str)
            .expect("recalled procedure body")
            .to_string();
        assert!(body.contains(BODY_CHUNK), "body chunk is complete: {body}");
        assert!(
            body.contains(ACTION_CHUNK),
            "action chunk is complete: {body}"
        );
        assert!(
            body.contains(CHECK_CHUNK),
            "check chunk is complete: {body}"
        );
        assert!(
            body.find("BODY_SENTINEL") < body.find("ACTION_SENTINEL")
                && body.find("ACTION_SENTINEL") < body.find("CHECK_SENTINEL"),
            "procedure chunks preserve source order: {body}"
        );
    }

    #[tokio::test]
    async fn recall_serves_several_distinct_procedures_up_to_the_item_limit() {
        // Four short procedures on the same anchor: the lane serves more than
        // one (the old `limit: 1` dropped the rest as `output_limit`) but never
        // more than MCP_RECALL_ITEM_LIMIT, each a distinct unit with its own
        // body intact — this is what the harness renders as N labelled lines.
        let (context, service, bound) =
            bound_fixture("mcp-recall-n-items", TrustLevel::TrustedSystem);
        for step in 1..=4 {
            seed_procedure(
                &service,
                &context,
                &format!("mcp-recall-n-items-{step}"),
                &format!("recall budget anchor step {step}"),
                &format!("STEP{step}_SENTINEL recall budget anchor: run step {step} of the deploy checklist."),
            )
            .await;
        }
        let mcp = MemphantMcp::new(service, bound);

        let structured = recall_structured(&mcp, "recall budget anchor deploy checklist").await;
        assert_eq!(structured["state"], "hit", "recall envelope: {structured}");
        let items = structured["items"].as_array().expect("items array");
        assert!(
            items.len() > 1 && items.len() <= MCP_RECALL_ITEM_LIMIT,
            "serves 1 < n <= {MCP_RECALL_ITEM_LIMIT} items, got {}: {structured}",
            items.len()
        );
        let unit_ids = items
            .iter()
            .map(|item| {
                item["unit_id"]
                    .as_str()
                    .expect("unit_id is a string")
                    .to_string()
            })
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(
            unit_ids.len(),
            items.len(),
            "every served item is a distinct unit"
        );
        for item in items {
            let body = item["body"].as_str().expect("body");
            assert!(
                body.contains("_SENTINEL recall budget anchor: run step "),
                "each served body is one intact procedure: {body}"
            );
        }
        // Honest empty is unchanged: an unrelated query serves nothing.
        let empty = recall_structured(&mcp, "zzqx unrelated nonsense token").await;
        assert_eq!(empty["state"], "empty", "unrelated query: {empty}");
    }

    #[tokio::test]
    async fn mcp_recall_serves_a_captured_belief() {
        // The product-lane e2e: a cross-harness CAPTURE lands exactly as
        // plugins/_shared/memphant_capture.py::http_poster posts it (a `retain`
        // Episode tagged `capture://summary`, `source_kind = "agent"`, with a
        // subject key), the reflect tick mints the captured Belief, and the
        // coding-lane MCP recall serves it.
        const CAPTURED_BODY: &str = "CAPTURE_SENTINEL deploy runbook: run `make deploy` only after the migration job reports zero pending rows.";
        let (context, service, bound) =
            bound_fixture("mcp-recall-capture", TrustLevel::TrustedSystem);
        service
            .retain(
                &context,
                "capture:summary:deploy-runbook",
                TrustLevel::TrustedSystem,
                memphant_types::RetainEpisodeHttpRequest {
                    subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    actor_id: context.actor_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    source_ref: "capture://summary".to_string(),
                    observed_at: "2026-08-14T00:00:00Z".to_string(),
                    payload: memphant_types::RetainPayload::Episode(
                        memphant_types::RetainEpisodePayload {
                            source_kind: "agent".to_string(),
                            body: CAPTURED_BODY.to_string(),
                            subject: Some("deploy-runbook".to_string()),
                            predicate: None,
                        },
                    ),
                },
            )
            .await
            .expect("retain capture");
        let outcome = service
            .run_worker_tick_scoped(
                memphant_core::JobFilter {
                    tenant: Some(context.tenant_id),
                    scope: Some(context.scope_id),
                },
                16,
            )
            .await
            .expect("worker tick");
        assert_eq!(
            outcome.failed, 0,
            "capture job does not dead-letter: {outcome:?}"
        );
        let mcp = MemphantMcp::new(service, bound);

        let structured = recall_structured(&mcp, "deploy runbook make deploy").await;
        assert_eq!(
            structured["state"], "hit",
            "captured belief is served: {structured}"
        );
        let items = structured["items"].as_array().expect("items array");
        let item = items
            .iter()
            .find(|item| {
                item["body"]
                    .as_str()
                    .is_some_and(|body| body.contains("CAPTURE_SENTINEL"))
            })
            .unwrap_or_else(|| panic!("captured body is served: {structured}"));
        assert!(
            item["unit_id"].as_str().is_some(),
            "served item names its unit id (exposure receipt)"
        );
        // Whether the capture lands Active (trusted key) or Candidate
        // (`captured_unconfirmed`), the inclusion reason must be one the
        // harness can label — never an unlabelled third state.
        let reason = item["inclusion_reason"].as_str().expect("inclusion_reason");
        if reason.contains("captured_") {
            assert!(
                reason.contains("captured_confirmed") || reason.contains("captured_unconfirmed"),
                "captured inclusion reason is labelable: {reason}"
            );
        }
    }
}

#[cfg(test)]
mod deep_runtime_smoke {
    use super::*;
    use memphant_core::{FixedClock, InMemoryStore, MemoryStore};
    use memphant_types::{
        ActorId, MemoryKind, NewEpisode, NewMemoryUnit, RecallMode, ScopeId, TrustLevel, UnitState,
    };
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Arc;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{Duration, Instant};

    const CLOCK: FixedClock = FixedClock("2026-07-20T00:00:00Z");

    fn scripted_openrouter() -> (String, Arc<AtomicUsize>, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let observed_calls = calls.clone();
        let server = std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            for turn in 1..=2 {
                let (mut socket, _) = loop {
                    match listener.accept() {
                        Ok(connection) => break connection,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            if Instant::now() >= deadline {
                                return;
                            }
                            std::thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("provider accept failed: {error}"),
                    }
                };
                socket.set_nonblocking(false).unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut request = Vec::new();
                let mut buffer = [0u8; 8192];
                loop {
                    let read = socket.read(&mut buffer).unwrap();
                    request.extend_from_slice(&buffer[..read]);
                    let Some(header_end) =
                        request.windows(4).position(|window| window == b"\r\n\r\n")
                    else {
                        continue;
                    };
                    let headers = String::from_utf8_lossy(&request[..header_end + 4]);
                    let length = headers
                        .lines()
                        .find_map(|line| {
                            line.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(str::trim)
                                .and_then(|value| value.parse::<usize>().ok())
                        })
                        .unwrap();
                    if request.len() >= header_end + 4 + length {
                        break;
                    }
                }
                observed_calls.fetch_add(1, Ordering::SeqCst);
                let (name, arguments) = if turn == 1 {
                    ("list_files", "{\"prefix\":\"episodes/\"}".to_string())
                } else {
                    (
                        "finish",
                        "{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"fixture exercises calibrated abstention\"}".to_string(),
                    )
                };
                let event = serde_json::json!({
                    "model":"anthropic/claude-sonnet-5","provider":"Azure",
                    "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":format!("call-{turn}"),"function":{"name":name,"arguments":arguments}}]}}],
                    "usage":{"prompt_tokens":10,"completion_tokens":1,"cost":0.00001}
                });
                let response_body = format!("data: {event}\n\ndata: [DONE]\n\n");
                write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nX-Generation-Id: gen-mcp-{turn}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", response_body.len(), response_body).unwrap();
            }
        });
        (format!("http://{address}/api/v1"), calls, server)
    }

    static DEEP_ENV_LOCK: Mutex<()> = Mutex::new(());

    struct ScopedEnv {
        saved: Vec<(&'static str, Option<String>)>,
    }

    impl ScopedEnv {
        fn set(variables: &[(&'static str, String)]) -> Self {
            let saved = variables
                .iter()
                .map(|(name, _)| (*name, std::env::var(name).ok()))
                .collect::<Vec<_>>();
            unsafe {
                for (name, value) in variables {
                    std::env::set_var(name, value);
                }
            }
            Self { saved }
        }
    }

    impl Drop for ScopedEnv {
        fn drop(&mut self) {
            unsafe {
                for (name, value) in self.saved.drain(..) {
                    match value {
                        Some(value) => std::env::set_var(name, value),
                        None => std::env::remove_var(name),
                    }
                }
            }
        }
    }

    #[tokio::test]
    async fn runtime_deep_recall_surfaces_summary_and_provenance() {
        let tenant = TenantId::from_u128(91_000);
        let scope = ScopeId::from_u128(91_001);
        let actor = ActorId::from_u128(91_002);
        let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
        let store = InMemoryStore::default();
        store.seed_context_binding(&context);
        let mut tx = store.begin(&context).await.unwrap();
        let episode = store
            .stage_episode(
                &mut tx,
                NewEpisode {
                    tenant_id: tenant,
                    data_subject_id: context.data_subject_id,
                    scope_id: scope,
                    agent_node_id: context.agent_node_id,
                    subject_generation: 0,
                    actor_id: actor,
                    source_kind: "fixture".into(),
                    source_ref: "mcp:deep".into(),
                    observed_at: CLOCK.0.into(),
                    source_trust: TrustLevel::TrustedSystem,
                    dedup_key: "mcp-deep".into(),
                    body: "Buried archive says launch code is heliotrope.".into(),
                },
            )
            .await
            .unwrap();
        store
            .stage_memory_unit(
                &mut tx,
                NewMemoryUnit {
                    capture: None,
                    tenant_id: tenant,
                    data_subject_id: context.data_subject_id,
                    scope_id: scope,
                    agent_node_id: context.agent_node_id,
                    subject_generation: 0,
                    kind: MemoryKind::Semantic,
                    state: UnitState::Active,
                    fact_key: Some("launch_code".into()),
                    predicate: None,
                    body: "Launch code is heliotrope.".into(),
                    confidence: Some(1.0),
                    trust_level: TrustLevel::TrustedSystem,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(actor),
                    source_kind: Some("fixture".into()),
                    source_ref: "mcp:deep".into(),
                    observed_at: CLOCK.0.into(),
                    source_episode_id: Some(episode.episode_id),
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
        store.commit(tx).await.unwrap();

        let (base_url, provider_calls, provider_server) = scripted_openrouter();
        let prompt = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(prompt.path(), "Use tools only.").unwrap();
        let variables = [
            ("MEMPHANT_DEEP", "on".to_string()),
            ("OPENROUTER_API_KEY", "test-key".to_string()),
            (
                "MEMPHANT_DEEP_MODEL",
                "anthropic/claude-sonnet-5-20260630".to_string(),
            ),
            (
                "MEMPHANT_DEEP_RESPONSE_MODEL",
                "anthropic/claude-sonnet-5".to_string(),
            ),
            (
                "MEMPHANT_DEEP_PROMPT_PATH",
                prompt.path().display().to_string(),
            ),
            ("MEMPHANT_DEEP_PROVIDERS", "azure".to_string()),
            (
                "MEMPHANT_DEEP_INPUT_PRICE_MICROS_PER_MILLION",
                "2000000".to_string(),
            ),
            (
                "MEMPHANT_DEEP_OUTPUT_PRICE_MICROS_PER_MILLION",
                "10000000".to_string(),
            ),
            ("MEMPHANT_DEEP_OPENROUTER_BASE_URL", base_url),
            ("MEMPHANT_EMBEDDINGS", "off".to_string()),
            // Pin rerank off: this scripts an exact deep-provider interaction and
            // rerank (on by default) would reorder the snapshot non-deterministically.
            ("MEMPHANT_CROSS_RERANK", "off".to_string()),
        ];
        let service = {
            let _env_lock = DEEP_ENV_LOCK
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            let _env = ScopedEnv::set(&variables);
            memphant_runtime::build_service(AnyStore::Mem(store.clone()))
        };
        let response = service
            .recall(
                context.clone(),
                RecallHttpRequest {
                    compact_only: false,
                    serve_captures: false,
                    subject_id: context.data_subject_id,
                    scope_id: scope,
                    agent_node_id: context.agent_node_id,
                    subject_generation: 0,
                    actor_id: actor,
                    query: "What is the buried launch code?".into(),
                    limit: Some(4),
                    budget_tokens: Some(128),
                    mode: Some(RecallMode::Deep),
                    include_beliefs: None,
                    transaction_as_of: None,
                    valid_at: None,
                    aggregation_window: None,
                },
            )
            .await
            .unwrap();
        provider_server.join().unwrap();
        assert_eq!(provider_calls.load(Ordering::SeqCst), 2);
        assert_eq!(
            response.deep.as_ref().unwrap().status,
            memphant_types::DeepRecallStatus::Completed,
            "unexpected deep summary: {:?}",
            response.deep
        );
        assert_eq!(
            response.deep.as_ref().unwrap().generation_ids,
            vec!["gen-mcp-1", "gen-mcp-2"]
        );
        assert_eq!(
            response.deep.as_ref().unwrap().evidence.status,
            memphant_types::EvidenceStatus::Insufficient
        );
        assert!(response.items[0].body.contains("heliotrope"));
        let trace = store.trace_by_id_any_tenant(response.trace_id).unwrap();
        assert_eq!(trace.l4_observed_provider.as_deref(), Some("Azure"));
        assert_eq!(trace.deep.unwrap(), response.deep.unwrap());
    }
}
