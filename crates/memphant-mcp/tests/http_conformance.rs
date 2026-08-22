//! Wire-level MCP 2026-07-28 conformance probes against the REAL streamable-
//! HTTP router (the exact wiring `memphant-mcp streamable-http` serves),
//! bound to an ephemeral loopback port and driven with raw HTTP/1.1:
//!
//! - `server/discover` advertises 2026-07-28;
//! - a stateless `tools/list` POST round-trips without any `Mcp-Session-Id`
//!   and carries the required `resultType` + `ttlMs`/`cacheScope` fields as
//!   plain `application/json`;
//! - a legacy (pre-2026-07-28) `initialize` is still answered, statelessly;
//! - the Bearer gate stays fail-closed (401 without the key);
//! - Host validation rejects non-allow-listed authorities (DNS rebinding).

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{ApiKeyRow, InMemoryStore, NoopEmbedding, SystemClock};
use memphant_mcp::api_key_hash;
use memphant_mcp::http::{allowed_hosts, streamable_http_router};
use memphant_runtime::AnyStore;
use memphant_types::{
    ActorId, MemoryKind, MemorySourceInput, RememberRequest, ScopeId, TenantId, TrustLevel,
};
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

const TEST_KEY: &str = "mk_http_conformance_test_key";

/// A store + service sharing one backing InMemoryStore. The returned `store`
/// handle seeds keys / context bindings / memories that the router — built from
/// a clone of the same `service` — will authenticate against and serve.
fn store_and_service() -> (InMemoryStore, MemoryService<AnyStore>) {
    let store = InMemoryStore::default();
    let service = MemoryService::new(
        Arc::new(AnyStore::Mem(store.clone())),
        Arc::new(SystemClock),
        Arc::new(NoopEmbedding),
    );
    (store, service)
}

/// Seed a tenant-bound (no context) key at `token` — enough for transport-level
/// probes (tools/list, discover) that never resolve a full principal.
fn seed_key(store: &InMemoryStore, token: &str, revoked: bool) {
    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: TenantId::new(),
        key_hash: api_key_hash(token),
        label: token.to_string(),
        max_trust: TrustLevel::TrustedSystem,
        data_subject_id: None,
        subject_generation: None,
        actor_id: None,
        scope_id: None,
        agent_node_id: None,
        can_forget: false,
        can_audit_history: false,
        revoked,
    });
}

/// Seed one FULLY context-bound key at `token` plus one procedural memory whose
/// body carries `sentinel`, recallable by the shared trigger `"shared deploy
/// runbook"`. Two tenants seeded this way share the trigger, so a single recall
/// query hits both bodies lexically — proving that TENANT scoping (not query
/// specificity) is what isolates the served results.
async fn seed_bound_memory(
    store: &InMemoryStore,
    service: &MemoryService<AnyStore>,
    token: &str,
    sentinel: &str,
) {
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);
    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: api_key_hash(token),
        label: token.to_string(),
        max_trust: TrustLevel::TrustedSystem,
        data_subject_id: Some(context.data_subject_id),
        subject_generation: Some(context.subject_generation),
        actor_id: Some(actor),
        scope_id: Some(scope),
        agent_node_id: Some(context.agent_node_id),
        can_forget: false,
        can_audit_history: false,
        revoked: false,
    });
    service
        .remember(
            &context,
            &format!("seed-{sentinel}"),
            TrustLevel::TrustedSystem,
            RememberRequest {
                kind: MemoryKind::Procedural,
                body: format!("{sentinel} shared deploy runbook: run the migration then deploy."),
                trigger: "shared deploy runbook".to_string(),
                verification: "the runbook is followed".to_string(),
                target_scope_id: None,
                valid_from: None,
                valid_to: None,
                source: MemorySourceInput {
                    kind: "user".to_string(),
                    r#ref: format!("test:{sentinel}"),
                    observed_at: "2026-08-21T00:00:00Z".to_string(),
                    episode_id: None,
                    resource_id: None,
                },
            },
        )
        .await
        .expect("seed bound memory");
}

/// Serve `service`'s router (per-request auth, no dev tenant) on an ephemeral
/// loopback port.
async fn serve_service(service: MemoryService<AnyStore>) -> std::net::SocketAddr {
    let router = streamable_http_router(
        service,
        None,
        allowed_hosts(Some("memphant-prod.internal:3333")),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind ephemeral port");
    let addr = listener.local_addr().expect("local addr");
    tokio::spawn(async move {
        axum::serve(listener, router).await.expect("serve router");
    });
    addr
}

/// The production router on an ephemeral port, with `TEST_KEY` seeded so the
/// transport-level probes authenticate.
async fn serve() -> std::net::SocketAddr {
    let (store, service) = store_and_service();
    seed_key(&store, TEST_KEY, false);
    serve_service(service).await
}

struct RawResponse {
    status: u16,
    headers: Vec<(String, String)>,
    body: String,
}

impl RawResponse {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(key, _)| key.eq_ignore_ascii_case(name))
            .map(|(_, value)| value.as_str())
    }

    /// The JSON body; SSE-framed bodies (`data: {...}`) are unwrapped so the
    /// assertion targets the JSON-RPC payload either way.
    fn json(&self) -> Value {
        let payload = self
            .body
            .lines()
            .find_map(|line| line.strip_prefix("data: "))
            .unwrap_or(self.body.as_str());
        serde_json::from_str(payload.trim()).unwrap_or_else(|error| {
            panic!("body is not JSON ({error}): {:?}", self.body);
        })
    }
}

/// One raw HTTP/1.1 request over a fresh connection (Connection: close), so
/// every probe is transport-level self-contained — exactly the stateless
/// claim under test.
async fn post(
    addr: std::net::SocketAddr,
    host: &str,
    extra_headers: &[(&str, &str)],
    body: &Value,
) -> RawResponse {
    let body = body.to_string();
    let mut request = format!(
        "POST /mcp HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n",
        body.len()
    );
    for (name, value) in extra_headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    request.push_str("\r\n");
    request.push_str(&body);

    let mut stream = tokio::net::TcpStream::connect(addr).await.expect("connect");
    stream
        .write_all(request.as_bytes())
        .await
        .expect("write request");
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).await.expect("read response");
    let raw = String::from_utf8_lossy(&raw).to_string();
    let (head, body) = raw
        .split_once("\r\n\r\n")
        .unwrap_or_else(|| panic!("malformed response: {raw:?}"));
    let mut lines = head.lines();
    let status_line = lines.next().expect("status line");
    let status: u16 = status_line
        .split_whitespace()
        .nth(1)
        .expect("status code")
        .parse()
        .expect("numeric status");
    let headers = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.trim().to_string(), value.trim().to_string()))
        .collect();
    // Undo chunked transfer-encoding framing if present (tolerant: chunk-size
    // lines are hex-only).
    let body = body
        .lines()
        .filter(|line| !line.trim().is_empty())
        .filter(|line| !(line.len() <= 8 && line.trim().chars().all(|ch| ch.is_ascii_hexdigit())))
        .collect::<Vec<_>>()
        .join("\n");
    RawResponse {
        status,
        headers,
        body,
    }
}

fn meta_2026_07_28() -> Value {
    json!({
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "conformance-probe", "version": "0"},
    })
}

fn bearer() -> (&'static str, String) {
    ("Authorization", format!("Bearer {TEST_KEY}"))
}

#[tokio::test]
async fn server_discover_advertises_2026_07_28() {
    let addr = serve().await;
    let auth = bearer();
    let response = post(
        addr,
        &addr.to_string(),
        &[
            (auth.0, &auth.1),
            ("Mcp-Method", "server/discover"),
            ("MCP-Protocol-Version", "2026-07-28"),
        ],
        &json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {"_meta": meta_2026_07_28()},
        }),
    )
    .await;
    assert_eq!(response.status, 200, "body: {}", response.body);
    let json = response.json();
    let supported = json["result"]["supportedVersions"]
        .as_array()
        .unwrap_or_else(|| panic!("supportedVersions in {json}"));
    assert!(
        supported.iter().any(|version| version == "2026-07-28"),
        "2026-07-28 missing from {supported:?}"
    );
}

#[tokio::test]
async fn stateless_tools_list_has_no_session_and_carries_cache_hints() {
    let addr = serve().await;
    let auth = bearer();
    let response = post(
        addr,
        &addr.to_string(),
        &[
            (auth.0, &auth.1),
            ("Mcp-Method", "tools/list"),
            ("MCP-Protocol-Version", "2026-07-28"),
        ],
        &json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": meta_2026_07_28()},
        }),
    )
    .await;
    assert_eq!(response.status, 200, "body: {}", response.body);
    assert!(
        response.header("mcp-session-id").is_none(),
        "stateless serving must not mint Mcp-Session-Id, got {:?}",
        response.header("mcp-session-id")
    );
    assert!(
        response
            .header("content-type")
            .is_some_and(|value| value.starts_with("application/json")),
        "json_response mode must answer single-shot requests as application/json, got {:?}",
        response.header("content-type")
    );
    let json = response.json();
    let result = &json["result"];
    assert_eq!(result["resultType"], "complete", "in {json}");
    assert!(result.get("ttlMs").is_some(), "ttlMs required: {json}");
    assert!(
        result.get("cacheScope").is_some(),
        "cacheScope required: {json}"
    );
    let tools: Vec<&str> = result["tools"]
        .as_array()
        .expect("tools array")
        .iter()
        .map(|tool| tool["name"].as_str().expect("tool name"))
        .collect();
    for name in [
        "recall",
        "remember",
        "correct_memory",
        "invalidate_memory",
        "report_memory_use",
    ] {
        assert!(tools.contains(&name), "missing {name} in {tools:?}");
    }
}

#[tokio::test]
async fn legacy_initialize_is_served_without_a_session() {
    let addr = serve().await;
    let auth = bearer();
    let response = post(
        addr,
        &addr.to_string(),
        &[(auth.0, &auth.1)],
        &json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "legacy-probe", "version": "0"},
            },
        }),
    )
    .await;
    assert_eq!(response.status, 200, "body: {}", response.body);
    assert!(
        response.header("mcp-session-id").is_none(),
        "legacy clients must be served statelessly too"
    );
    let json = response.json();
    assert_eq!(json["result"]["protocolVersion"], "2025-11-25", "{json}");
}

#[tokio::test]
async fn missing_bearer_is_401_before_any_protocol_handling() {
    let addr = serve().await;
    let response = post(
        addr,
        &addr.to_string(),
        &[
            ("Mcp-Method", "tools/list"),
            ("MCP-Protocol-Version", "2026-07-28"),
        ],
        &json!({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/list",
            "params": {"_meta": meta_2026_07_28()},
        }),
    )
    .await;
    assert_eq!(response.status, 401, "body: {}", response.body);
}

#[tokio::test]
async fn unlisted_host_authority_is_rejected() {
    let addr = serve().await;
    let auth = bearer();
    let response = post(
        addr,
        "rebind.evil.example:3333",
        &[
            (auth.0, &auth.1),
            ("Mcp-Method", "tools/list"),
            ("MCP-Protocol-Version", "2026-07-28"),
        ],
        &json!({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/list",
            "params": {"_meta": meta_2026_07_28()},
        }),
    )
    .await;
    assert!(
        (400..500).contains(&response.status),
        "non-allow-listed Host must be rejected, got {} body {}",
        response.status,
        response.body
    );
}

/// Drive a `tools/call recall` over the wire with `token`'s Bearer and return
/// the raw response body. `MCP-Protocol-Version: 2025-11-25` (< STANDARD_HEADERS
/// = 2026-07-28) is a known version, so it passes protocol validation while
/// skipping SEP-2243 `Mcp-Method`/`Mcp-Name` header checks — recall is served
/// version-independently.
async fn recall_over_wire(addr: std::net::SocketAddr, token: &str, query: &str) -> String {
    let auth = format!("Bearer {token}");
    let response = post(
        addr,
        &addr.to_string(),
        &[
            ("Authorization", &auth),
            ("MCP-Protocol-Version", "2025-11-25"),
        ],
        &json!({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "recall", "arguments": {"query": query}},
        }),
    )
    .await;
    assert_eq!(response.status, 200, "recall body: {}", response.body);
    response.body
}

/// THE isolation proof: two tenants, each with a bound key and a memory sharing
/// the SAME recall trigger. Per-request auth must bind each `tools/call` to the
/// key's OWN tenant, so tenant A's recall serves only A's memory and never B's,
/// and vice-versa. If the transport were still single-tenant-per-process (the
/// bug this reworks), one tenant's key would serve the startup tenant's data.
#[tokio::test]
async fn per_request_bearer_binds_each_call_to_its_own_tenant() {
    let (store, service) = store_and_service();
    seed_bound_memory(&store, &service, "mk_tenant_a_key", "SENTINEL_A").await;
    seed_bound_memory(&store, &service, "mk_tenant_b_key", "SENTINEL_B").await;
    let addr = serve_service(service).await;

    let body_a = recall_over_wire(addr, "mk_tenant_a_key", "shared deploy runbook").await;
    assert!(
        body_a.contains("SENTINEL_A"),
        "tenant A recalls its own memory: {body_a}"
    );
    assert!(
        !body_a.contains("SENTINEL_B"),
        "tenant A must NOT recall tenant B's memory: {body_a}"
    );

    let body_b = recall_over_wire(addr, "mk_tenant_b_key", "shared deploy runbook").await;
    assert!(
        body_b.contains("SENTINEL_B"),
        "tenant B recalls its own memory: {body_b}"
    );
    assert!(
        !body_b.contains("SENTINEL_A"),
        "tenant B must NOT recall tenant A's memory: {body_b}"
    );
}

/// The per-request gate rejects an unknown or revoked key at the wire, before
/// any protocol handling — the same fail-closed posture as the missing-bearer
/// probe, now proven per key rather than per process.
#[tokio::test]
async fn unknown_and_revoked_bearer_are_401() {
    let (store, service) = store_and_service();
    seed_key(&store, TEST_KEY, false);
    seed_key(&store, "mk_revoked_key", true);
    let addr = serve_service(service).await;

    let list_body = json!({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/list",
        "params": {"_meta": meta_2026_07_28()},
    });

    let unknown_auth = "Bearer mk_never_minted".to_string();
    let unknown = post(
        addr,
        &addr.to_string(),
        &[
            ("Authorization", &unknown_auth),
            ("Mcp-Method", "tools/list"),
            ("MCP-Protocol-Version", "2026-07-28"),
        ],
        &list_body,
    )
    .await;
    assert_eq!(unknown.status, 401, "unknown key: {}", unknown.body);

    let revoked_auth = "Bearer mk_revoked_key".to_string();
    let revoked = post(
        addr,
        &addr.to_string(),
        &[
            ("Authorization", &revoked_auth),
            ("Mcp-Method", "tools/list"),
            ("MCP-Protocol-Version", "2026-07-28"),
        ],
        &list_body,
    )
    .await;
    assert_eq!(revoked.status, 401, "revoked key: {}", revoked.body);
}
