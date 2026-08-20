# MemPhant Porting Notes

MemPhant is the public product boundary. Syndai integration code stays in the
private Syndai repository until a surface is generalized enough to belong here.

## Repo Boundary

- Public MemPhant work lives here: Rust crates, migrations, SDKs, public docs,
  public fixtures, provider lint, and the self-hostable runtime.
- Private Syndai work lives in Syndai. Do not track a local Syndai worktree path
  or commit SHA in this repo.
- When mirrored specs need a drift check, run `python3 scripts/check_spec_drift.py`.
  The script compares this repo with `MEMPHANT_PRIVATE_SPEC_DIR` when set, then
  falls back to a sibling checkout at `../Syndai/docs/superpowers/specs/memphant`
  if it exists.

## Porting Rule

Port code from Syndai into MemPhant only when it is product-neutral and can be
tested through public MemPhant contracts. Keep Syndai tenant wiring, private
fixtures, hosted credentials, and app-specific adapters out of this repository.

## Syndai MCP Integration (private wiring)

The MCP server (`memphant-mcp`, stateless streamable-HTTP `/mcp`) is served as the
`mcp` Fly process group on the prod cell; the product-neutral conformance record is
`docs/mcp-2026-07-28-conformance.md` and the ops are in
`docs/deployment/prod-cell-runbook.md`. The Syndai-specific consumer wiring lives here,
out of the public surface:

- **Consumer**: Syndai's coding run-engine (repo-profiler system actor) + the local
  Claude Code harness recall/capture hooks. Reached over 6PN at
  `memphant-prod.internal:3000` (REST) / `:3333/mcp` (MCP).
- **Tenant**: `syndai-dogfood` (`fe7b06e6-…`). Two key KINDS, do not mix:
  the repo-profiler's context-bindings `PUT` uses an UNBOUND tenant-service key
  (`require_tenant_service_key`); MCP recall uses a context-BOUND key (`live_principal`,
  `resolve_tenant` at boot). The `syndai-prod-worker` key serves the REST path; a separate
  context-bound key (`syndai-harness-mcp`) backs the `mcp` process's `MEMPHANT_API_KEY`.
- **Consumer config** (Syndai `syndai/prod` Doppler, never committed):
  `MEMPHANT_API_BASE_URL=http://memphant-prod.internal:3000`, `MEMPHANT_API_KEY=mk_…`,
  `MEMPHANT_REPO_PROFILE_ENABLED` (set after the slice deploys). Local harness adds
  `MEMPHANT_MCP_URL`/`MEMPHANT_CAPTURE_URL` pointing at a `fly proxy 3333 -a memphant-prod`
  tunnel; hooks install via `plugins/install.py --repo <syndai-worktree>` into
  `~/.claude/settings.json` (NOT hooks.json). Syndai-side AGENTS.md block + `.memphant/`
  gitignore land through a Syndai worktree + preflight.
- **503-fix provenance (2026-08-20)**: every dogfood run's repo-profile `PUT` 503'd because
  the prod binary was at migration head `20260817_013` while the DB was at `…_009`
  (migrations 010–013 unapplied). `authenticate_api_key` selected columns that did not yet
  exist → `StoreError::Backend` → `backend_unavailable` (503) on every authed route, while
  the unauthenticated `/v1/health` stayed 200 (`degraded` in body). Fix: applied 010–013
  out-of-band per the runbook; health flipped to `ok@…_013`. Reminder: DB migrations are
  out-of-band from the image deploy — a schema-breaking image needs its migrations applied
  first, or every authed route 503s.
- The full integration flow (spec/plan/review) is tracked Syndai-side, not in this repo.
