//! Streamable-HTTP wiring shared by the binary and the conformance tests: the
//! per-request Bearer→tenant gate and the stateless (MCP 2026-07-28) router.
//!
//! Stateless by construction: `NeverSessionManager` + `legacy_session_mode =
//! false`, so no `Mcp-Session-Id` is ever minted, every POST is
//! self-contained (any machine can answer any request, restarts drop
//! nothing), and older (2025-03-26..2025-11-25) clients get their
//! `initialize` handshake answered per-request without a session.
//! `json_response = true` answers single-shot tool calls as plain
//! `application/json` (rmcp still falls back to SSE if a handler streams
//! notifications first).
//!
//! Multi-tenant: the transport holds no process-wide key. The auth middleware
//! resolves the presented Bearer to a tenant per request, stashes the resolved
//! `BoundTenant` in a task-local for the life of `next.run`, and the rmcp
//! service factory reads it to build a handler bound to THAT tenant. rmcp calls
//! the factory synchronously on the request task (before its internal spawn),
//! so the task-local is always in scope when the handler is built — see the
//! stateless invariant note on `streamable_http_router`.

use std::sync::Arc;

use axum::extract::{Request, State};
use axum::middleware::{Next, from_fn_with_state};
use axum::response::{IntoResponse, Response};
use memphant_core::service::MemoryService;
use memphant_runtime::AnyStore;
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService, session::never::NeverSessionManager,
};

use crate::{BoundTenant, McpAuthReject, MemphantMcp, resolve_http_principal, unbound_tenant};

tokio::task_local! {
    /// The tenant resolved for the in-flight streamable-HTTP request. The auth
    /// middleware sets it for the duration of `next.run`; the rmcp service
    /// factory reads it to build the per-request handler. Task-local, so
    /// concurrent requests never share a principal.
    static CURRENT_PRINCIPAL: BoundTenant;
}

/// Per-request auth state for the streamable-HTTP transport. Holds the SAME
/// `MemoryService` the handler factory builds from, so a key is always
/// authenticated against the exact store that will serve the request — there is
/// no authenticate-here / serve-there split. stdio needs none (per-principal by
/// construction).
#[derive(Clone)]
struct McpAuth {
    service: MemoryService<AnyStore>,
    /// When set (`MEMPHANT_DEV_TENANT`), auth is disabled and every request is
    /// bound to this dev tenant; no Bearer is required.
    dev_tenant: Option<BoundTenant>,
}

async fn require_auth(State(auth): State<McpAuth>, request: Request, next: Next) -> Response {
    if let Some(dev) = auth.dev_tenant.clone() {
        return CURRENT_PRINCIPAL.scope(dev, next.run(request)).await;
    }
    let header = request
        .headers()
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok());
    match resolve_http_principal(auth.service.store(), header).await {
        Ok(bound) => CURRENT_PRINCIPAL.scope(bound, next.run(request)).await,
        Err(McpAuthReject::Unauthorized) => (
            axum::http::StatusCode::UNAUTHORIZED,
            "unauthorized: MCP streamable-http requires `Authorization: Bearer mk_<api-key>`\n",
        )
            .into_response(),
        Err(McpAuthReject::Unavailable) => (
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            "service unavailable: memory store lookup failed\n",
        )
            .into_response(),
    }
}

/// The `Host` authorities the transport accepts, from an optional
/// comma-separated raw value (`MEMPHANT_MCP_ALLOWED_HOSTS` in the binary).
/// Loopback authorities are always accepted; rmcp's Host validation rejects
/// everything else (DNS-rebinding defense), so any non-loopback name clients
/// dial — e.g. `memphant-prod.internal:3333` — must be listed.
pub fn allowed_hosts(extra: Option<&str>) -> Vec<String> {
    let mut hosts: Vec<String> = ["localhost", "127.0.0.1", "::1", "[::1]"]
        .into_iter()
        .map(String::from)
        .collect();
    if let Some(raw) = extra {
        hosts.extend(
            raw.split(',')
                .map(str::trim)
                .filter(|host| !host.is_empty())
                .map(String::from),
        );
    }
    hosts
}

/// The `/mcp` router: a stateless streamable-HTTP service behind the
/// per-request Bearer→tenant gate. This is the exact wiring the binary serves;
/// tests bind it to an ephemeral port to probe wire-level conformance and
/// tenant isolation.
///
/// `dev_tenant`: `Some` only under `MEMPHANT_DEV_TENANT` (auth disabled, every
/// request bound to it); `None` in production (per-request auth).
///
/// STATELESS IS A HARD REQUIREMENT for per-request auth. `NeverSessionManager` +
/// `legacy_session_mode = false` mean no session id is ever minted, so a handler
/// bound to request A's key can never be reused to answer request B — the
/// principal is not associated with any session id. Do NOT enable legacy session
/// mode here without first binding the principal to the session id; the
/// conformance tests `stateless_tools_list_has_no_session_and_carries_cache_hints`
/// and `legacy_initialize_is_served_without_a_session` assert no session id is
/// ever minted, and `per_request_bearer_binds_each_call_to_its_own_tenant`
/// proves per-request tenant isolation on the wire.
pub fn streamable_http_router(
    service: MemoryService<AnyStore>,
    dev_tenant: Option<BoundTenant>,
    allowed_hosts: Vec<String>,
) -> axum::Router {
    let config = StreamableHttpServerConfig::default()
        .with_legacy_session_mode(false)
        .with_json_response(true)
        .with_allowed_hosts(allowed_hosts);
    let factory_service = service.clone();
    let http_service = StreamableHttpService::new(
        move || {
            // Infallible: with no scoped principal (a request that bypassed the
            // middleware, or rmcp filling its shared tool_schema cache) fall back
            // to an UNBOUND handler that fails every tool/resource call closed but
            // still serves the static tool list, so the schema cache is never
            // poisoned with None. Fail-closed lives at the tool layer.
            let principal = CURRENT_PRINCIPAL
                .try_with(BoundTenant::clone)
                .unwrap_or_else(|_| unbound_tenant());
            Ok(MemphantMcp::new(factory_service.clone(), principal))
        },
        Arc::new(NeverSessionManager::default()),
        config,
    );
    axum::Router::new()
        .nest_service("/mcp", http_service)
        .layer(from_fn_with_state(
            McpAuth {
                service,
                dev_tenant,
            },
            require_auth,
        ))
}
