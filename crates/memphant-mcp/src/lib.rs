//! MemPhant MCP server on rmcp 2.2 (MCP 2025-11-25): seven tools and
//! tenant-bound memory resources over the
//! shared `MemoryService<AnyStore>`, a persistent stdio session, and an
//! optional streamable-HTTP transport. The tenant is fixed at startup from
//! `MEMPHANT_API_KEY` (sha256 → api_key lookup) or `MEMPHANT_DEV_TENANT`
//! (dev) — stdio is a per-principal transport; a missing/revoked key refuses
//! to start rather than serving an unauthenticated session.

use memphant_core::service::{MemoryService, ServiceError, clamp_trust};
use memphant_core::{CoreError, MemoryStore, MutationResponse, StoreError};
use memphant_runtime::AnyStore;
use memphant_types::{
    AgentNodeId, CorrectRequest, CorrectResult, ENGINE_VERSION, ForgetRequest, ForgetResult,
    MarkRequest, MarkResult, RecallHttpRequest, RecallResponse, ReflectAccepted, ReflectRequest,
    ResolvedMemoryContext, RetainEpisodeHttpRequest, RetainEpisodeHttpResponse, RetrievalTrace,
    ScopeId, SubjectId, TenantId, TraceRequest, TrustLevel,
};
use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::handler::server::wrapper::Parameters;
use rmcp::model::{
    CallToolResult, Implementation, ListResourceTemplatesResult, ListResourcesResult,
    PaginatedRequestParams, ReadResourceRequestParams, ReadResourceResult, Resource,
    ResourceContents, ResourceTemplate, ServerCapabilities, ServerInfo,
};
use rmcp::service::RequestContext;
use rmcp::{ErrorData, Json, RoleServer, ServerHandler, tool, tool_handler, tool_router};
use schemars::JsonSchema;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

mod file_memory;
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
    /// The presented key's hash. Recall rechecks this row on every call so a
    /// persistent stdio session cannot outlive key revocation.
    pub api_key_hash: Option<String>,
    pub dev_mode: bool,
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
        api_key_hash: Some(row.key_hash),
        dev_mode: false,
    })
}

const MCP_RECALL_BUDGET_TOKENS: usize = 128;

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct McpRecallRequest {
    query: String,
}

#[derive(Debug, Clone, Copy, Serialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
enum McpRecallState {
    Hit,
    Empty,
}

#[derive(Debug, Serialize, JsonSchema)]
struct McpRecallResponse {
    state: McpRecallState,
    #[serde(flatten)]
    response: RecallResponse,
}

enum McpRecallFailure {
    Auth(&'static str),
    Scope(&'static str),
    Unavailable,
}

impl McpRecallFailure {
    fn result(self) -> CallToolResult {
        match self {
            Self::Auth(message) => CallToolResult::structured_error(serde_json::json!({
                "error": {"code": "auth_required", "message": message},
            })),
            Self::Scope(message) => CallToolResult::structured_error(serde_json::json!({
                "error": {"code": "scope_denied", "message": message},
            })),
            Self::Unavailable => CallToolResult::structured_error(serde_json::json!({
                "state": "unavailable",
                "error": {"code": "backend_unavailable", "message": "memory store unavailable"},
            })),
        }
    }
}

/// The MCP tool surface: seven verbs over the shared application layer.
#[derive(Clone)]
pub struct MemphantMcp {
    service: MemoryService<AnyStore>,
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
            service,
            bound,
            tool_router: Self::tool_router(),
        }
    }

    fn bind_principal(
        &self,
        actor_id: memphant_types::ActorId,
        scope_id: memphant_types::ScopeId,
    ) -> Result<(), String> {
        if self.bound.dev_mode
            || (self.bound.actor_id.is_none() && self.bound.scope_id.is_none())
            || (self.bound.actor_id == Some(actor_id) && self.bound.scope_id == Some(scope_id))
        {
            Ok(())
        } else {
            Err("scope_denied: request is outside the API key principal binding".to_string())
        }
    }

    async fn recall_context(&self) -> Result<ResolvedMemoryContext, McpRecallFailure> {
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
        let context = self
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
        Ok(context)
    }

    #[tool(
        description = "Store exactly one episode, resource, or direct unit with provenance.",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn retain(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<RetainEpisodeHttpRequest>>,
    ) -> Result<Json<RetainEpisodeHttpResponse>, String> {
        let tenant = self.bound.tenant;
        self.bind_principal(request.actor_id, request.scope_id)?;
        let context = self
            .service
            .store()
            .resolve_memory_context(
                tenant,
                request.subject_id,
                request.actor_id,
                request.scope_id,
                request.agent_node_id,
            )
            .await
            .map_err(|_| "scope_denied: unresolved memory context".to_string())?;
        if request.subject_generation != context.subject_generation {
            return Err("context_binding_conflict: subject generation is stale".to_string());
        }
        let response = self
            .service
            .retain(
                &context,
                &idempotency_key,
                clamp_trust(context.actor_trust, self.bound.max_trust),
                request,
            )
            .await
            .map_err(mcp_error)?;
        decode_mutation_response(response)
    }

    #[tool(
        description = "Retrieve cited memory evidence for a query (budgeted, salience-ranked, with provenance).",
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
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            query: request.query,
            limit: Some(1),
            budget_tokens: Some(MCP_RECALL_BUDGET_TOKENS),
            mode: Some(memphant_types::RecallMode::Fast),
            include_beliefs: Some(false),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        };
        let response = match self.service.recall(context, request).await {
            Ok(response) => response,
            Err(ServiceError::Core(CoreError::Store(_))) => {
                return McpRecallFailure::Unavailable.result();
            }
            Err(error) => {
                return CallToolResult::structured_error(serde_json::json!({
                    "error": {"code": "scope_denied", "message": mcp_error(error)},
                }));
            }
        };
        let state = if response.items.is_empty() {
            McpRecallState::Empty
        } else {
            McpRecallState::Hit
        };
        CallToolResult::structured(
            serde_json::to_value(McpRecallResponse { state, response })
                .expect("MCP recall response serializes"),
        )
    }

    #[tool(
        description = "Consolidate a scope's pending episodes/resources into memory units.",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn reflect(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<ReflectRequest>>,
    ) -> Result<Json<ReflectAccepted>, String> {
        let tenant = self.bound.tenant;
        self.bind_principal(request.actor_id, request.scope_id)?;
        let context = self
            .service
            .store()
            .resolve_memory_context(
                tenant,
                request.subject_id,
                request.actor_id,
                request.scope_id,
                request.agent_node_id,
            )
            .await
            .map_err(|_| "scope_denied: unresolved memory context".to_string())?;
        if request.subject_generation != context.subject_generation {
            return Err("context_binding_conflict: subject generation is stale".to_string());
        }
        let response = self
            .service
            .reflect(&context, &idempotency_key, request)
            .await
            .map_err(mcp_error)?;
        decode_mutation_response(response)
    }

    #[tool(
        description = "Supersede or invalidate selected memory through an auditable correction.",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn correct(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<CorrectRequest>>,
    ) -> Result<Json<CorrectResult>, String> {
        let tenant = self.bound.tenant;
        self.bind_principal(request.actor_id, request.scope_id)?;
        let context = self
            .service
            .store()
            .resolve_memory_context(
                tenant,
                request.subject_id,
                request.actor_id,
                request.scope_id,
                request.agent_node_id,
            )
            .await
            .map_err(|_| "scope_denied: unresolved memory context".to_string())?;
        if request.subject_generation != context.subject_generation {
            return Err("context_binding_conflict: subject generation is stale".to_string());
        }
        decode_mutation_response(
            self.service
                .correct(&context, &idempotency_key, request)
                .await
                .map_err(mcp_error)?,
        )
    }

    #[tool(
        description = "Forget by memory unit, episode or resource selector; tombstones block re-derivation.",
        annotations(
            read_only_hint = false,
            destructive_hint = true,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn forget(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<ForgetRequest>>,
    ) -> Result<Json<ForgetResult>, String> {
        let tenant = self.bound.tenant;
        if request.scope_id != request.selector.scope_id {
            return Err(
                "context_binding_conflict: forget scope does not match selector scope".to_string(),
            );
        }
        self.bind_principal(request.actor_id, request.selector.scope_id)?;
        let context = self
            .service
            .store()
            .resolve_memory_context(
                tenant,
                request.subject_id,
                request.actor_id,
                request.scope_id,
                request.agent_node_id,
            )
            .await
            .map_err(|_| "scope_denied: unresolved memory context".to_string())?;
        if request.subject_generation != context.subject_generation {
            return Err("context_binding_conflict: subject generation is stale".to_string());
        }
        decode_mutation_response(
            self.service
                .forget(&context, &idempotency_key, request)
                .await
                .map_err(mcp_error)?,
        )
    }

    #[tool(
        description = "Inspect a retrieval trace (tenant-bound).",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = false,
            open_world_hint = false
        )
    )]
    async fn trace(
        &self,
        Parameters(request): Parameters<TraceRequest>,
    ) -> Result<Json<RetrievalTrace>, String> {
        let tenant = self.bound.tenant;
        self.bind_principal(request.actor_id, request.scope_id)?;
        let context = self
            .service
            .store()
            .resolve_memory_context(
                tenant,
                request.subject_id,
                request.actor_id,
                request.scope_id,
                request.agent_node_id,
            )
            .await
            .map_err(|_| "scope_denied: unresolved memory context".to_string())?;
        if request.subject_generation != context.subject_generation {
            return Err("context_binding_conflict: subject generation is stale".to_string());
        }
        let trace = self
            .service
            .trace(&context, request.trace_id)
            .await
            .map_err(mcp_error)?
            .ok_or_else(|| "trace not found".to_string())?;
        self.bind_principal(trace.actor_id, trace.scope_id)?;
        Ok(Json(trace))
    }

    #[tool(
        description = "Report what the caller did with a recall pack (feeds decay/reinforcement).",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn mark(
        &self,
        Parameters(McpMutation {
            idempotency_key,
            request,
        }): Parameters<McpMutation<MarkRequest>>,
    ) -> Result<Json<MarkResult>, String> {
        let tenant = self.bound.tenant;
        self.bind_principal(request.actor_id, request.scope_id)?;
        let context = self
            .service
            .store()
            .resolve_memory_context(
                tenant,
                request.subject_id,
                request.actor_id,
                request.scope_id,
                request.agent_node_id,
            )
            .await
            .map_err(|_| "scope_denied: unresolved memory context".to_string())?;
        if request.subject_generation != context.subject_generation {
            return Err("context_binding_conflict: subject generation is stale".to_string());
        }
        decode_mutation_response(
            self.service
                .mark(&context, &idempotency_key, request)
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
                "MemPhant memory service: seven governed tools plus read-only tenant-bound memory resources.",
            )
    }

    async fn list_resources(
        &self,
        request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
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
        Ok(ListResourcesResult {
            meta: None,
            next_cursor,
            resources: items
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
        })
    }

    async fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResult, ErrorData> {
        let content = self
            .read_bound_resource(&request.uri)
            .await
            .map_err(memory_mcp_error)?;
        Ok(ReadResourceResult::new(vec![
            ResourceContents::text(content.text, content.uri).with_mime_type(content.mime_type),
        ]))
    }

    async fn list_resource_templates(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListResourceTemplatesResult, ErrorData> {
        Ok(ListResourceTemplatesResult::with_all_items(
            resource_templates(),
        ))
    }
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

    #[test]
    fn unavailable_recall_is_a_typed_tool_result() {
        let result = McpRecallFailure::Unavailable.result();
        assert_eq!(result.is_error, Some(true));
        assert_eq!(
            result.structured_content.expect("structured unavailable")["state"],
            "unavailable"
        );
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
