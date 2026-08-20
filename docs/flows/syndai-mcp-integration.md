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
