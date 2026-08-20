//! Streamable-HTTP wiring shared by the binary and the conformance tests:
//! the per-request Bearer gate and the stateless (MCP 2026-07-28) router.
//!
//! Stateless by construction: `NeverSessionManager` + `legacy_session_mode =
//! false`, so no `Mcp-Session-Id` is ever minted, every POST is
//! self-contained (any machine can answer any request, restarts drop
//! nothing), and older (2025-03-26..2025-11-25) clients get their
//! `initialize` handshake answered per-request without a session.
//! `json_response = true` answers single-shot tool calls as plain
//! `application/json` (rmcp still falls back to SSE if a handler streams
//! notifications first).

use std::sync::Arc;

use axum::extract::{Request, State};
use axum::middleware::{Next, from_fn_with_state};
use axum::response::{IntoResponse, Response};
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService, session::never::NeverSessionManager,
};

use crate::MemphantMcp;

/// Per-request auth gate for the streamable-HTTP transport (stdio is
/// per-principal by construction and needs none).
#[derive(Clone)]
pub struct McpAuth {
    pub dev_mode: bool,
    pub expected_key: Option<String>,
}

async fn require_auth(State(auth): State<McpAuth>, request: Request, next: Next) -> Response {
    let header = request
        .headers()
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok());
    if crate::mcp_http_authorized(auth.dev_mode, auth.expected_key.as_deref(), header) {
        next.run(request).await
    } else {
        (
            axum::http::StatusCode::UNAUTHORIZED,
            "unauthorized: MCP streamable-http requires `Authorization: Bearer <MEMPHANT_API_KEY>`\n",
        )
            .into_response()
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

/// The `/mcp` router: stateless streamable-HTTP service behind the Bearer
/// gate. This is the exact wiring the binary serves; tests bind it to an
/// ephemeral port to probe wire-level conformance.
pub fn streamable_http_router(
    handler: MemphantMcp,
    auth: McpAuth,
    allowed_hosts: Vec<String>,
) -> axum::Router {
    let config = StreamableHttpServerConfig::default()
        .with_legacy_session_mode(false)
        .with_json_response(true)
        .with_allowed_hosts(allowed_hosts);
    let service = StreamableHttpService::new(
        move || Ok(handler.clone()),
        Arc::new(NeverSessionManager::default()),
        config,
    );
    axum::Router::new()
        .nest_service("/mcp", service)
        .layer(from_fn_with_state(auth, require_auth))
}
