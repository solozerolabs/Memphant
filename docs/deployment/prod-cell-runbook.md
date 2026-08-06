# memphant-prod cell — runbook (2026-08-05)

One cell serving the Syndai dogfood (Phase A repo profiles), owner-approved
BYOC into the existing Supabase project rather than a new instance ($0).

## Topology

| piece | value |
|---|---|
| Fly app | `memphant-prod` (org personal, sjc), **private-only** — no public service; reached at `http://memphant-prod.internal:3000` over 6PN |
| processes | `server` (memphant-server) + `worker` (memphant-worker), shared-1x/512MB each, `restart=always` (serviceless app — never scale to zero, see Syndai LEARNINGS `fly-serviceless-worker-autostop-reaps-it`) |
| DB | `memphant` schema in Supabase project `wmnzjmrysnzjthldgffh` (Finn) — BYOC per `deploy/provider-profiles/supabase.env.example`; 28 tables, migrations 9/9, `bootstrap-check=clean` |
| served creds | `memphant_{app,authn,worker}_login` NOINHERIT non-superuser roles (RLS is real); provisioned via `scripts/provision_login_roles.sh`; secrets on the Fly app |
| tenant | `syndai-dogfood` `fe7b06e6-…`, api key `syndai-prod-worker` (max_trust `trusted_system` — required by the repo-profiler system actor) |
| consumer config | syndai/prod doppler: `MEMPHANT_API_BASE_URL=http://memphant-prod.internal:3000`, `MEMPHANT_API_KEY=mk_…`; `MEMPHANT_REPO_PROFILE_ENABLED` set only after the slice deploys |
| CI | `.github/workflows/deploy.yml` redeploys on main pushes touching runtime paths; app-scoped `FLY_API_TOKEN` (1y) in repo secrets |

Credentials live in `~/.memphant-private/prod-cell/login_roles.env` (0600) and
in Fly/doppler secrets — never in git.

## Supabase-specific traps (all hit during bring-up)

1. **`postgres` cannot `SET ROLE memphant_owner` until granted membership.**
   Pre-create the six capability roles and `grant <role> to postgres` before
   the first migration run (the same `set_role` class the evalrank deploy hit).
2. **Migrations refuse the 6543 transaction pooler** — use the direct
   `db.<ref>.supabase.co:5432` host. Syndai's `DATABASE_URL` is the pooler
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

## Standing caveats

- **Object store is DECLARED, not provisioned.** The profile declares
  s3/`memphant-prod` to satisfy `bootstrap-check`; no bucket exists. The Phase
  A profile slice never writes blobs. Provision a real versioned bucket before
  any blob-writing feature ships.
- **Migrator-password exposure (2026-08-05):** `apply_memphant_migrations.py`
  echoed the failing psql command line — including the Supabase `postgres`
  password — into a local session transcript once. The script is patched to
  redact; **rotating the Finn `postgres` password is recommended.**
- Shared-DB blast radius is the accepted trade of the $0 BYOC decision: heavy
  MemPhant load or a bad migration contends with Syndai prod. Revisit at the
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

# reach it from syndai-prod (the only network that can)
flyctl ssh console -a syndai-prod -C "curl -s http://memphant-prod.internal:3000/v1/health"

# rotate the api key: provision a new one, set doppler, revoke the old id
# (memphant.revoke_api_key('<key_id>'))
```
