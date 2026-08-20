# memphant-prod cell — runbook (2026-08-05)

One private consumer cell (its app-specific tenant/consumer wiring is in
`porting.md` § Syndai MCP Integration — kept out of this public repo),
owner-approved BYOC into the existing Supabase project rather than a new
instance ($0).

## Topology

| piece | value |
|---|---|
| Fly app | `memphant-prod` (org personal, sjc), **private-only** — no public service; reached at `http://memphant-prod.internal:3000` over 6PN |
| processes | `server` (memphant-server) + `worker` (memphant-worker) + `mcp` (memphant-mcp streamable-http, port 3333), shared-1x/512MB each, `restart=always` (serviceless app — never scale to zero; a Fly serviceless app with autostop would be reaped) |
| DB | `memphant` schema in the BYOC Supabase project — per `deploy/provider-profiles/supabase.env.example`; migration head `20260817_013`, `bootstrap-check=clean` |
| served creds | `memphant_{app,authn,worker}_login` NOINHERIT non-superuser roles (RLS is real); provisioned via `scripts/provision_login_roles.sh`; secrets on the Fly app |
| tenant / consumer config | app-specific — see `porting.md` § Syndai MCP Integration (tenant id, the two key kinds, consumer Doppler config). Kept out of this public repo. |
| CI | `.github/workflows/deploy.yml` redeploys on main pushes touching runtime paths; app-scoped `FLY_API_TOKEN` (1y) in repo secrets |

Credentials live in `~/.memphant-private/prod-cell/login_roles.env` (0600) and
in Fly/doppler secrets — never in git.

## Supabase-specific traps (all hit during bring-up)

1. **`postgres` cannot `SET ROLE memphant_owner` until granted membership.**
   Pre-create the six capability roles and `grant <role> to postgres` before
   the first migration run (the same `set_role` class the evalrank deploy hit).
2. **Migrations refuse the 6543 transaction pooler** — use the direct
   `db.<ref>.supabase.co:5432` host. The BYOC app's `DATABASE_URL` is the pooler
   form; rewrite user/host (strip the `.<ref>` username suffix).
3. **Session-pooler usernames need the `<role>.<ref>` suffix** if the served
   URLs are later moved onto the pooler; today they use the direct host.
4. `provision_login_roles.sh` previously died of SIGPIPE (exit 141) under
   macOS pipefail — fixed with a bounded urandom read.
5. **`memphant-cli admin` cannot run as `memphant_provisioner_login`**
   (NOINHERIT member; the CLI pool never does `SET ROLE`). Provision via the
   SECURITY DEFINER functions as `postgres` instead:
   `select memphant.provision_tenant(...)` / `memphant.provision_api_key(...)`
   (key hash = sha256 hex of the `mk_…` token).

## MCP process group (`mcp`)

`memphant-mcp streamable-http` runs as a third process group on the same image,
serving MCP (streamable-HTTP, path `/mcp`) on `MEMPHANT_MCP_BIND=[::]:3333` —
private 6PN only, no `[http_service]`, same never-scale-to-zero rule as the
other groups. Tools: `recall`, `remember`, `correct_memory`,
`invalidate_memory`, `report_memory_use`.

**Stateless (MCP 2026-07-28, rmcp 3.x).** The transport is sessionless: no
`Mcp-Session-Id` is ever minted, every POST is self-contained, and single-shot
tool calls are answered as plain `application/json`. Ops consequences: the
`mcp` group can scale to N machines behind plain round-robin with zero
affinity, restarts/redeploys drop no client state, and legacy
(2025-03-26..2025-11-25) clients are still served — their `initialize` is
answered per-request without a session.

**Host allow-list.** rmcp validates the HTTP `Host` header (DNS-rebinding
defense) and accepts only loopback by default. `MEMPHANT_MCP_ALLOWED_HOSTS`
(fly.toml `[env]`, comma-separated `host[:port]`) must list every non-loopback
authority clients dial — currently `memphant-prod.internal:3333` and
`mcp.process.memphant-prod.internal:3333`. A missing entry surfaces as a 4xx
on every request BEFORE auth; add the authority there, not by disabling the
check. `fly proxy` traffic arrives as `127.0.0.1:3333` and is always accepted.

**Env / secrets.** The process reuses the existing app secrets
`MEMPHANT_APP_DATABASE_URL` + `MEMPHANT_AUTHN_DATABASE_URL` (role-scoped served
URLs shared with server/worker; `DATABASE_URL` is refused). It additionally
REQUIRES a **new** app secret:

- `MEMPHANT_API_KEY` — the `mk_…` token of a **context-BOUND** key for the
  fixed tenant. The binary resolves its tenant from this key at startup
  (`resolve_tenant`, sha256 → `api_key` row) and refuses to start if the key is
  missing, unknown, or revoked. Every streamable-HTTP request must also present
  it as `Authorization: Bearer <MEMPHANT_API_KEY>` — a 401 otherwise. Mint via
  `select memphant.provision_api_key(...)` as `postgres` (trap 5 above), then
  `fly secrets set MEMPHANT_API_KEY=mk_… -a memphant-prod`.

**Key kinds — do not mix them up.** The cell now uses two distinct key shapes:

- **Tenant-service key (UNBOUND)** — what the REST consumer (repo-profiler) uses for
  `PUT /v1/context-bindings/…` on the server (`require_tenant_service_key`);
  it must NOT be context-bound.
- **Context-BOUND key** — what the MCP process needs: bound to subject,
  generation, actor, scope, and agent node. MCP `recall` refuses a key that is
  not fully context-bound. This is the `MEMPHANT_API_KEY` secret above.

One key cannot serve both paths; the REST-consumer key (see `porting.md`; server
PUT path) is not a substitute for the MCP key.

**Reaching it from a laptop** (there is no public route):

```bash
fly proxy 3333 -a memphant-prod
# then point the MCP client at http://127.0.0.1:3333/mcp with
# Authorization: Bearer $MEMPHANT_API_KEY
```

No health check is configured for the `mcp` group: the only route (`/mcp`)
sits behind the bearer gate, so an unauthenticated HTTP check would 401.
Liveness signal is `flyctl status` + the startup log line
`memphant-mcp: streamable-http on http://[::]:3333/mcp`.

## Standing caveats

- **Object store is DECLARED, not provisioned.** The profile declares
  s3/`memphant-prod` to satisfy `bootstrap-check`; no bucket exists. The Phase
  A profile slice never writes blobs. Provision a real versioned bucket before
  any blob-writing feature ships.
- **Migrator-password exposure (2026-08-05):** `apply_memphant_migrations.py`
  echoed the failing psql command line — including the Supabase `postgres`
  password — into a local session transcript once. The script is patched to
  redact; **rotating the BYOC project's `postgres` password is recommended.**
- Shared-DB blast radius is the accepted trade of the $0 BYOC decision: heavy
  MemPhant load or a bad migration contends with the shared BYOC project. Revisit at the
  first sign of contention; the exit is the $10/mo dedicated project.

## Bring-up failures (2026-08-05, all fixed — check here first on redeploy issues)

1. `flyctl deploy | tail` masks the exit code — pipeline reports tail's 0.
2. glibc: ONNX Runtime binaries need >= 2.38 (`__isoc23_strto*`) — images are trixie.
3. Cold start crashed downloading the bge model from HuggingFace —
   `MEMPHANT_EMBEDDINGS=off` on this cell; bake the model in before vectors.
4. A lease-timeout deploy can leave a machine updated-but-stopped and call it
   "good state" — `flyctl machine start <id>`.
5. `MEMPHANT_BIND=0.0.0.0` refuses cross-app connections — 6PN is IPv6, bind `[::]:3000`.

## Operations

```bash
# deploy (manual; CI does this on main)
flyctl deploy --remote-only --app memphant-prod

# health / logs
flyctl status --app memphant-prod
flyctl logs --app memphant-prod

# reach it from the consumer app (the only network that can)
flyctl ssh console -a <consumer-app> -C "curl -s http://memphant-prod.internal:3000/v1/health"

# rotate the api key: provision a new one, set doppler, revoke the old id
# (memphant.revoke_api_key('<key_id>'))
```
