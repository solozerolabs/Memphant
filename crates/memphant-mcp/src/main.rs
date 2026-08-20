use std::process::ExitCode;

use memphant_mcp::MemphantMcp;
use memphant_mcp::http::{McpAuth, allowed_hosts, streamable_http_router};
use rmcp::ServiceExt;

#[tokio::main]
async fn main() -> ExitCode {
    match std::env::args().nth(1).as_deref() {
        Some("--list-tools-json") => {
            println!(
                "{}",
                serde_json::to_string_pretty(&memphant_mcp::tools_artifact())
                    .expect("MCP tools serialize")
            );
            ExitCode::SUCCESS
        }
        Some("--list-resources-json") => {
            println!(
                "{}",
                serde_json::to_string_pretty(&memphant_mcp::resources_artifact())
                    .expect("MCP resources serialize")
            );
            ExitCode::SUCCESS
        }
        Some("stdio") | None => run_stdio().await,
        Some("streamable-http") => run_streamable_http().await,
        Some(_) => {
            eprintln!(
                "usage: memphant-mcp [--list-tools-json|--list-resources-json|stdio|streamable-http]"
            );
            ExitCode::from(2)
        }
    }
}

/// Builds the store, resolves the fixed tenant (refusing to start without a
/// valid key or dev tenant) and returns the tool handler.
async fn build_handler() -> Result<MemphantMcp, String> {
    let store = memphant_runtime::build_app_store()
        .await
        .map_err(|error| error.to_string())?;
    let bound = memphant_mcp::resolve_tenant(&store).await?;
    let service = memphant_runtime::build_service(store);
    Ok(MemphantMcp::new(service, bound))
}

/// Persistent stdio session: serves JSON-RPC over stdin/stdout until the
/// client disconnects.
async fn run_stdio() -> ExitCode {
    let handler = match build_handler().await {
        Ok(handler) => handler,
        Err(error) => {
            eprintln!("memphant-mcp: {error}");
            return ExitCode::from(1);
        }
    };
    let running = match handler.serve(rmcp::transport::io::stdio()).await {
        Ok(running) => running,
        Err(error) => {
            eprintln!("memphant-mcp: {error}");
            return ExitCode::from(1);
        }
    };
    match running.waiting().await {
        Ok(_) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("memphant-mcp: {error}");
            ExitCode::from(1)
        }
    }
}

/// Streamable-HTTP transport (MCP 2026-07-28, stateless) on
/// `MEMPHANT_MCP_BIND` (default 127.0.0.1:3333), path `/mcp`.
///
/// Stateless by construction: `NeverSessionManager` + `legacy_session_mode =
/// false`, so no `Mcp-Session-Id` is ever minted, every POST is self-contained
/// (any machine can answer any request, restarts drop nothing), and older
/// (2025-03-26..2025-11-25) clients are served their `initialize` handshake
/// per-request without a session. `json_response = true` answers single-shot
/// tool calls as plain `application/json` (rmcp still falls back to SSE if a
/// handler streams notifications).
async fn run_streamable_http() -> ExitCode {
    let handler = match build_handler().await {
        Ok(handler) => handler,
        Err(error) => {
            eprintln!("memphant-mcp: {error}");
            return ExitCode::from(1);
        }
    };
    let dev_mode = handler.dev_mode();
    let expected_key = std::env::var("MEMPHANT_API_KEY")
        .ok()
        .map(|key| key.trim().to_string())
        .filter(|key| !key.is_empty());
    let extra_hosts = std::env::var("MEMPHANT_MCP_ALLOWED_HOSTS").ok();
    let router = streamable_http_router(
        handler,
        McpAuth {
            dev_mode,
            expected_key,
        },
        allowed_hosts(extra_hosts.as_deref()),
    );
    let bind = std::env::var("MEMPHANT_MCP_BIND").unwrap_or_else(|_| "127.0.0.1:3333".to_string());
    match tokio::net::TcpListener::bind(&bind).await {
        Ok(listener) => {
            eprintln!("memphant-mcp: streamable-http on http://{bind}/mcp");
            if let Err(error) = axum::serve(listener, router).await {
                eprintln!("memphant-mcp: {error}");
                return ExitCode::from(1);
            }
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("memphant-mcp: {error}");
            ExitCode::from(1)
        }
    }
}
