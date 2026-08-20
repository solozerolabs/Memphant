# Flow: Syndai ⇄ MemPhant MCP integration

## Spec

**Outcome.** Syndai coding sessions (local Claude Code harness first, run-engine second)
get working MemPhant memory over MCP, and the currently-broken prod write path is fixed:

1. **Fix the 503**: every Syndai dogfood run's repo-profile
   `PUT /v1/context-bindings/…` to `memphant-prod.internal:3000` returns HTTP 503 at
   teardown (`repo_profile_capture_failed`, confirmed in fly-logs on all 3 runs
   2026-08-20; machines are up and the `/v1/health` check passes, so this is
   route/app-level, possibly the .internal DNS resolving to the WORKER machine which
   serves no HTTP, or a server-side dependency gate on that route).
2. **Serve the MCP**: expose `memphant-mcp` (streamable-HTTP, path `/mcp`) for
   authorized clients. Binary confirmed working locally (5 tools: recall, remember,
   correct_memory, invalidate_memory, report_memory_use). It needs
   `MEMPHANT_APP_DATABASE_URL` + `MEMPHANT_AUTHN_DATABASE_URL` (role-scoped; live as
   Fly secrets on memphant-prod) + a tenant-bound `MEMPHANT_API_KEY`.
3. **Wire the local harness**: run `plugins/install.py --repo <syndai>` (hooks into
   `~/.claude`, AGENTS.md block, .gitignore), set `MEMPHANT_MCP_URL` /
   `MEMPHANT_CAPTURE_URL` / `MEMPHANT_API_KEY`, and verify the manual hook check from
   the plugin README returns a `<memphant_memory>` block (or honest-empty) end-to-end.

**Non-goals.** No new Memphant features; no schema changes beyond what serving MCP
needs; no Syndai run-engine prompt changes (injection path is the plugin's); no
benchmark/eval work. Memphant DB objects stay in the `memphant` schema; tests use
scratch Postgres, never Syndai prod DB.

**Priority order.** 1) Fix the silent prod write loss (503). 2) A working, auth-gated
MCP endpoint. 3) Local harness integration verified. 4) Latency/cost of recall.

## Plan

1. **Diagnose 503 with evidence**: hit `/v1/health` and the context-bindings route from
   inside the Fly private network (`fly ssh console -a memphant-prod -C ...` or
   `fly proxy`), read server logs at a Syndai-run teardown timestamp, and decide between
   (a) .internal-resolves-to-worker, (b) route-level dependency 503, (c) auth/tenant
   rejection surfacing as 503. Fix at the root: if (a), Syndai's base URL moves to the
   process-scoped DNS name (`server.process.memphant-prod.internal`) — a Doppler value
   change, no code; if (b)/(c), fix the server route.
2. **MCP serving decision (KISS)**: run `memphant-mcp streamable-http` as a third
   process group on memphant-prod (same image already contains the binary? verify;
   else extend Dockerfile CMD matrix), private-network only, `MEMPHANT_MCP_BIND=[::]:3333`,
   reusing the existing app/authn DB secrets. Local laptop access via
   `fly proxy 3333 -a memphant-prod`. No public exposure in this flow.
3. **Tenant key**: mint/confirm the Syndai coding tenant API key (reuse the key already
   in Doppler `syndai`/prod as `MEMPHANT_API_KEY` if it is tenant-bound; otherwise mint
   via the server's key path) and verify `resolve_tenant` accepts it.
4. **Local harness install**: run `plugins/install.py --repo /Users/sidsharma/Syndai
   --harness claude-code`; set env (shell profile or direnv, values from Doppler, never
   committed); run the README's manual hook verification; then a live `recall` round-trip
   through the proxied endpoint.
5. **Syndai-side repo edits** (AGENTS.md MemPhant block + `.memphant/` gitignore) land
   through a Syndai worktree + preflight, not directly.
6. Record ops runbook notes in the Memphant repo docs (how the MCP process is served,
   how to mint keys, how the proxy is opened).

## Review

Adversarial plan review (fresh context) + parent verification, 2026-08-19:

- **BLOCKER (verified in Dockerfile:10-13,38-42):** the prod image builds/copies only
  `memphant-server`/`memphant-worker`/`memphant-cli` — no `memphant-mcp`. Plan step 2
  now includes the Dockerfile change (add `-p memphant-mcp` + COPY) and a NEW Fly
  secret for the MCP tenant key.
- **503 ranking (verified lib.rs:301, 1345-51):** `backend_unavailable` (503) maps from
  `StoreError::Backend/Poisoned/TransactionAlreadyCommitted` — including the
  `lookup_api_key` call on EVERY authed route — while `/v1/health` returns 200 even
  degraded. Store/DB-role failure is the leading hypothesis; the .internal-to-worker
  theory is dead (worker has no listener → ConnectError, not HTTP 503). Live scout
  confirms before any fix ships.
- **HIGH — key kinds split:** context-bindings PUT needs an UNBOUND tenant-service key
  (`require_tenant_service_key`); MCP recall needs a context-BOUND key
  (`live_principal`). Step 3 mints/verifies BOTH; "reuse if tenant-bound" was the wrong
  predicate for the PUT key.
- **HIGH — installer writes `~/.claude/hooks.json`, which Claude Code does not read**
  (hooks live in settings.json / plugin hooks). Step 4 must LIVE-verify a hook fires;
  if it is a silent no-op, wire via settings.json and fix `plugins/install.py` upstream
  in this flow.
- **MEDIUM:** the AGENTS.md STABLE_BLOCK is 249 B against Syndai's ~348 B chain
  headroom (fits, ~99 B left — measure again at install); Syndai-side writes go through
  a Syndai worktree (step 5), never `--repo /Users/sidsharma/Syndai` directly.

## Harness

```sh
cd /Users/sidsharma/Memphant-syndai-mcp && cargo build --release -p memphant-mcp
/Users/sidsharma/Memphant-syndai-mcp/target/release/memphant-mcp --list-tools-json | python3 -c "import sys,json; ts=json.load(sys.stdin); names=sorted(t['name'] for t in ts); assert names==sorted(['recall','remember','correct_memory','invalidate_memory','report_memory_use']), names; print('tools-ok')"
curl -fsS --max-time 10 https://api.syndai.ai/healthz >/dev/null && echo syndai-up
```

## MCP 2026-07-28 conformance

IMPLEMENTED 2026-08-19 (research notes: session scratchpad `probe/mcp-spec-research.md`; sources:
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
cd /Users/sidsharma/Memphant-syndai-mcp && cargo test -p memphant-mcp --test http_conformance
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
