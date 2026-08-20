# MemPhant MCP — 2026-07-28 spec conformance

Streamable-HTTP tools+resources server on rmcp 3.1.3, stateless. Product-neutral
record of how the 2026-07-28 revision maps to `crates/memphant-mcp`. (Private Syndai
consumer wiring lives in `porting.md`.)

IMPLEMENTED 2026-08-19 (research notes: internal research notes; sources:
spec 2026-07-28 changelog + streamable-http transport page, rmcp migration guide rust-sdk
discussion #969, docs.rs/crates.io rmcp 3.1.3). rmcp upgraded **2.2.0 → 3.1.3** (stable; the
`3.0.0-beta.*` line graduated to `3.0.0` on 2026-07-28 — "Rust (beta)" in the MCP blog is the
SDK support-tier label, not a channel). Serving is now **stateless**: `NeverSessionManager` +
`legacy_session_mode=false` + `json_response=true`, shared router in
`crates/memphant-mcp/src/http.rs` (binary and conformance tests serve the identical wiring).

### Normative mapping (streamable-HTTP tools+resources server)

| Spec requirement (2026-07-28) | Level | Status |
| --- | --- | --- |
| Single POST MCP endpoint | MUST | Conforms — `/mcp`, POST only (GET/DELETE 405 via rmcp) |
| Sessions/`Mcp-Session-Id` removed; list endpoints connection-invariant | MUST | Conforms — NeverSessionManager; probe asserts no session header |
| No `initialize` handshake; per-request `_meta` protocolVersion/clientCapabilities | MUST | Conforms — rmcp 3.x stateless dispatch; `UnsupportedProtocolVersionError` (-32022) on mismatch |
| `server/discover` implemented | MUST | Conforms — rmcp default impl derived from `get_info()`; probe asserts `supportedVersions` ⊇ 2026-07-28 |
| `MCP-Protocol-Version` header validated; header/body mismatch → 400 `HeaderMismatch` (-32020) | MUST | Conforms — rmcp transport validation; absent header treated as legacy 2025-03-26 (allowed for servers supporting pre-2025-06-18 clients; `stateless_protocol_metadata_required` stays false on purpose) |
| `Mcp-Method` (all) + `Mcp-Name` (tools/call, resources/read) headers validated, Base64 sentinel decoding | MUST | Conforms — rmcp 3.x `ServerHandler`-bound validation (SEP-2243) |
| Origin validation, 403 when present+invalid | MUST | Conforms with caveat — rmcp `allowed_origins=[]` skips the check; non-browser 6PN clients send no Origin, Host allow-list + Bearer gate cover rebinding. Set `allowed_origins` if a browser client ever appears |
| Host validation (DNS rebinding) | (transport security) | Conforms — loopback + `MEMPHANT_MCP_ALLOWED_HOSTS`; probe asserts unlisted authority is rejected |
| `resultType` on every result; cleared for legacy peers | MUST | Conforms — rmcp emits/gates by negotiated version |
| `ttlMs` + `cacheScope` on tools/list, resources/list, resources/read, templates/list | MUST | Conforms — tools/list via `#[tool_handler]` (ttl 0/Public, version-gated); resources list/read ttl 15s/Private, templates ttl 300s/Public (`lib.rs supports_cache_hints`) |
| Deterministic tools/list order | SHOULD | Conforms — static `ToolRouter` order |
| MRTR replaces server-initiated requests | MUST | N/A — no sampling/elicitation/roots; tools return `Complete` |
| `subscriptions/listen` for change notifications | MUST if offered | N/A — no subscriptions capability |
| `ping`/`logging/setLevel`/roots notifications removed; no unsolicited `notifications/message` | MUST | Conforms — never emitted; rmcp drops removed methods under 07-28 |
| SSE resumability/`Last-Event-ID` removed; stream close = cancellation | MUST | Conforms — rmcp 3.x; `json_response=true` avoids SSE for single-shot calls entirely |
| Resource not-found −32002 → −32602 | MUST | Conforms — `ErrorData::resource_not_found` (rmcp 3.x renumbered) |
| Tasks extension | optional | N/A |
| OAuth: RFC 9207 iss, CIMD/DCR, issuer-bound creds | MUST (client/AS-side) | N/A — static per-request Bearer gate (spec: servers SHOULD authenticate; kept byte-for-byte: constant-time compare, fail-closed 401 before protocol handling) |
| Deprecated: Roots/Sampling/Logging, HTTP+SSE transport | SHOULD NOT adopt | Conforms — none present |

Unsatisfied MUSTs: none for the surface served. The one deliberate leniency is accepting
header-less legacy requests as 2025-03-26 peers — explicitly allowed by the transport spec for
servers that support pre-2025-06-18 clients (the local Claude Code harness still negotiates
2025-11-25).

### What changed

- `crates/memphant-mcp/Cargo.toml` — rmcp `2.2` → `3.1` (dep + client dev-dep); `Cargo.lock`
  `2.2.0` → `3.1.3`.
- `crates/memphant-mcp/src/http.rs` (NEW) — shared Bearer gate + stateless router
  (`streamable_http_router`, `allowed_hosts`).
- `crates/memphant-mcp/src/main.rs` — uses the shared router; reads
  `MEMPHANT_MCP_ALLOWED_HOSTS`; stdio mode unchanged.
- `crates/memphant-mcp/src/lib.rs` — resources list/read/templates carry version-gated
  `ttlMs`/`cacheScope`; `read_resource` returns `ReadResourceResponse` (MRTR enum).
- `crates/memphant-mcp/tests/http_conformance.rs` (NEW) — 5 wire-level probes (below).
- `fly.toml` — `[env] MEMPHANT_MCP_ALLOWED_HOSTS`; `docs/deployment/prod-cell-runbook.md` —
  stateless + allow-list ops notes. `mcp/*.json` artifacts regenerated via the release binary:
  byte-identical, no commit needed.

Ops win: any `mcp` machine answers any POST — multi-machine round-robin safe, restarts drop
nothing, zero warm-up beyond per-process store/tenant construction at boot.

### Conformance probes

In-repo (run these; they serve the production router on an ephemeral port):

```sh
cd . && cargo test -p memphant-mcp --test http_conformance
```

Against a live instance (fly proxy or localhost; same five assertions by hand):

```sh
B=http://127.0.0.1:3333/mcp; A="Authorization: Bearer $MEMPHANT_API_KEY"; CT="Content-Type: application/json"; AC="Accept: application/json, text/event-stream"
META='{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}'
curl -fsS -X POST $B -H "$A" -H "$CT" -H "$AC" -H "Mcp-Method: server/discover" -H "MCP-Protocol-Version: 2026-07-28" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"server/discover\",\"params\":$META}" | grep -q 2026-07-28 && echo discover-ok
curl -fsSi -X POST $B -H "$A" -H "$CT" -H "$AC" -H "Mcp-Method: tools/list" -H "MCP-Protocol-Version: 2026-07-28" -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":$META}" > /tmp/mcp-p2 && ! grep -qi mcp-session-id /tmp/mcp-p2 && grep -q ttlMs /tmp/mcp-p2 && grep -q resultType /tmp/mcp-p2 && echo stateless-ok
curl -fsS -X POST $B -H "$A" -H "$CT" -H "$AC" -d '{"jsonrpc":"2.0","id":3,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' >/dev/null && echo legacy-ok
test "$(curl -s -o /dev/null -w '%{http_code}' -X POST $B -H "$CT" -H "$AC" -d '{}')" = 401 && echo auth-ok
```
