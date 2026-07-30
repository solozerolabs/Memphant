# MemPhant - DB Provider, BYOC, and App Surface Spec

## 0. Rule

MemPhant must run on ordinary Postgres and must not rely on a host app's browser-facing database semantics. Hosted, BYOC, and local deployments share the same schema contract.

## 1. Provider Posture

| Provider | Role | Notes |
|---|---|---|
| Plain Postgres | portability baseline | every migration and bootstrap must pass here |
| Neon | managed/eval/replay default | branching is useful for eval/replay, but not required by core |
| Supabase BYOC | customer-owned app-platform option | requires explicit RLS/grant posture if exposed to API/browser roles |
| Local Docker Postgres | development | should mirror production extensions and roles |

Supabase is a supported provider. It is not the default managed substrate assumption for MemPhant core.

### 1.1 What Each Provider Gives, Lacks, and What MemPhant Must Not Assume

| Provider | Gives | Lacks / risk | Must NOT assume |
|---|---|---|---|
| Plain Postgres | full control, every extension installable | customer runs HA/backup | a control plane, branching, or pre-installed extensions |
| Neon | pgvector pre-installed (per-**database**, `CREATE EXTENSION vector`); branch-per-eval | per-DB (not per-project) extension scope; branching is Neon-only | branching exists (it's an accelerator, not a dependency) |
| Supabase BYOC | pgvector pre-installed; RLS/PostgREST tooling | **no true superuser** (elevated via `supautils`); PostgREST can expose `memphant` if misconfigured | `CREATE ROLE`/superuser DDL works; the browser surface is safe by default |
| Cloud SQL / RDS / Azure | managed HA/backups | extension **allowlist** (`azure.extensions`), `CREATE EXTENSION` gated behind a managed-superuser role, no `CREATE ROLE` | any extension is installable; the migrator can create roles |
| Local Docker | full control, mirrors prod | dev only | it differs from prod — must mirror prod extensions/roles |

The one capability MemPhant assumes is "an adequate pgvector + the migrator can create objects in `memphant`," and §11a checks both. Everything else is provider sugar.

### 1.2 The BYOC Deployment Contract

**Customer provides:** one **Postgres connection URL** where the `migrator` role can create objects (privilege reality in §11a); **object-store credentials + bucket + region** the customer owns, in the customer's region (§7a); network reachability to both. **MemPhant installs and nothing else:** the `memphant` schema, the three NOLOGIN roles, required extensions *if grantable*, the typed tables + indexes (`03` §5.1), RLS policies, the `memphant.schema_migrations` ledger — **never** `public`/`syndai`/provider auth tables/any pre-existing customer schema. **Bootstrap is one idempotent re-runnable command** (`memphant db bootstrap`) that runs the §12 triad and stops fail-closed on the §11a preflight; upgrade is the same command on a newer binary (§11b). The single static binary (`03` §0.1) is what makes this tractable.

## 2. Schema Boundary

Preferred: dedicated MemPhant database.

Allowed: shared physical Postgres with dedicated `memphant` schema.

Forbidden:

- MemPhant objects in `public`
- MemPhant objects in Syndai's `syndai` schema
- cross-FKs from MemPhant tables into Syndai tables
- using `public.alembic_version` or any host-app migration table
- provider-specific auth tables as core dependencies

Migration ledger:

```text
memphant.schema_migrations
```

## 3. Roles

| Role | Capability |
|---|---|
| migrator | create/alter MemPhant schema objects |
| runtime | read/write tenant memory through service path |
| readonly | inspect allowed metadata/health only |
| analyst | aggregate quality facts only, no raw memory by default |

Browser/mobile/client roles should not exist in the core schema. If a provider creates them, they must have no direct table access unless covered by explicit RLS policies and tests.

## 4. Extension Strategy

Required or likely extensions:

- `vector`
- `pg_trgm`
- `unaccent` if text normalization needs it
- provider-specific queue extension only outside core contract

Rules:

- extension schema is explicit per provider
- migrations never assume extensions live in `public`
- advisory warnings for extension-in-public are tracked and waived only deliberately
- vector dimensions are checked against `embedding_profile`, and the profile's **`index_strategy`** must match the dimension count: **`halfvec` HNSW caps at 4,000 dims** (`vector` caps at 2,000; `02` §2.1a), so only a **>4,000-dim** model requires `hnsw_subvector` or `hnsw_binary` (expression index) — a model ≤4,000 dims (e.g. 3,072) uses `hnsw_full` directly. `sparsevec` storage supports 16,000 non-zero elements, but HNSW indexing supports 1,000 non-zero elements. A migration that creates an HNSW index on a >4,000-dim `halfvec` column, or a >1,000-non-zero `sparsevec`, without the required strategy is rejected by `db lint`.

## 5. Table Rules

Every tenant table:

- has `tenant_id`
- has tenant-prefixed hot-path indexes
- has explicit FK indexes
- participates in deletion generation where recall-affecting
- is covered by grant/RLS tests

Every SQL function:

- pins `search_path`
- avoids security-definer unless reviewed
- has a test for privilege behavior if security-definer is unavoidable

## 6. Exposure Gate

RLS is the **default** on hosted multi-tenant tables (defense-in-depth behind the application `WHERE tenant_id`, `06` §6.1), relaxed only for a provably single-tenant deployment. The DB exposure gate fails if:

- any tenant table lacks an RLS policy under a hosted-mode config
- any memory table is selectable by an unauthenticated/browser role without RLS
- any tenant table lacks `tenant_id`
- any hot-path tenant query lacks a tenant-prefixed index
- any FK lacks an index
- any function has mutable `search_path`
- MemPhant objects appear in `public` or `syndai`
- extension placement drifts from the provider strategy
- deletion completeness checks fail
- cross-tenant recall count is nonzero

This gate blocks alpha and public launch.

## 7. Supabase BYOC

Supabase BYOC requirements:

- schema is `memphant`
- PostgREST/browser exposure is off by default
- if exposed, RLS is enabled and policies are tested
- service role keys stay server-side
- generated SDKs never embed service keys
- Supabase advisors are reviewed for `memphant` findings
- unrelated `public` or host-app schemas are not mutated

Live Syndai inspection found `syndai` tables where RLS posture and grant-lockdown tests must be interpreted together. MemPhant should avoid ambiguous exposure by making the intended access path explicit.

## 7a. Object-Store BYOC and Residency

The two-store split (`02` §2.3) means BYOC has **two** data planes: the Postgres URL (§1.2) and a customer-owned object store (raw episode bodies, resource blobs, cold-tier blobs, eval-trace exports — never on a recall channel).

- **Customer brings the bucket** — S3/GCS/Azure Blob/local-FS through the `object_store` crate (`02` §2.3), one config switch. MemPhant writes only under the content-addressed `tenant_id/<hash[:2]>/<hash>` prefix tree; it never assumes bucket ownership beyond that.
- **Residency is the customer's region, asserted not inferred** — the store config pins an explicit region/endpoint; MemPhant does not auto-detect or cross-region replicate. DB-in-region-X + blobs-in-region-Y is a **bootstrap-check finding**, not a silent runtime egress.
- **Restore is cross-store and Postgres-authoritative** (`14` §4.2): provider PG PITR covers only the Postgres plane; the customer bucket is reconciled to the restored reference set, never rolled back independently. A green PG restore is *not* a green system restore.
- **Retention floor — object-store retention ≥ Postgres PITR window.** Bucket **versioning enabled** + **noncurrent-version expiration ≥ the PITR window + margin** is a fail-closed `db bootstrap-check` assertion (`restore_retention_floor_violation`). Versioning-off or a short floor silently guarantees an un-restorable system: a PITR can resurrect a row whose blob lifecycle already hard-deleted.
- **Encryption** — provider SSE, plus the **3-tier envelope encryption of `06` §6.1.1** (per-user DEK ← per-tenant KEK ← KMS/TEE root KEK; **the BYOC customer holds their own KEK**) for sensitive `body` before the blob leaves the process; vectors stay plaintext (HNSW, `02` §2.1a). **Credentials stay server-side** (never in a generated SDK or browser surface).
- **The telemetry boundary is part of residency** (`22` §1.2 deletes `resource_uri`; only content-hashes/counts cross the telemetry plane) — observability export cannot leak the customer's data out of region.
- **Deletion spans both planes in the safe order** (`02` §2.3 / `06` §6.2): tombstone the row first, delete the blob second — `forget` on BYOC must reach the customer's bucket, covered by the §6 exposure gate's deletion-completeness check.

## 7b. Hosted Multi-Region Topology (cell-per-region)

Data residency for the **hosted managed service** is a **closed-control-plane** concern; the open core stays single-region (one deployment = one region). Multi-region hosting is **N independent single-region MemPhant cells + a thin tenant→region router** — no multi-region schema, no cross-region replication, no distributed transactions. The open-core binary is identical in every cell; "multi-region" is purely a composition of single-region deployments.

- **A cell = one full single-region MemPhant stack:** a region-pinned **Supabase** Postgres project + a same-region object-store bucket + a regional **Temporal** namespace/workers (the `pg_cron→pgmq→Temporal` reflect/GC loop, `02` §6.1) + a regional **Fly** app running `memphant-server`. Every cell is a vanilla single-region deployment — the §7a residency rules apply within it unchanged. (MemPhant runs **no** agent sandboxes — Modal/Daytona are a consumer's coding-lane concern, not the memory substrate's.)
- **Hosted runtime is a FULL backend on Fly Machines — Supabase Edge Functions are REJECTED for MemPhant core (final, R93).** The hosted service runs the *same single static binary* self-hosters run: `memphant-server` (Axum) + `memphant-worker` as two Fly process groups per cell (bluegreen deploy, secrets via `doppler run` at boot — the ops pattern proven by Syndai production). Supabase's role is Postgres + Storage (the S3-compatible bucket behind the `object_store` crate) — never compute. Edge functions structurally cannot host this architecture: Deno/TypeScript isolates cannot run the Rust core; per-request isolates cannot hold the `pg_try_advisory_xact_lock` reflect leases across long consolidation rounds (`02` §6.2), cannot run pgmq consumers, Temporal workers, or `spawn_blocking` pools (`02` §1.1a), and cannot serve stateful MCP Streamable HTTP sessions; and any edge-function layer would fork "hosted = the public self-host contract" (invariant #11 — one binary, no hidden hosted runtime). There is **no Deno/edge-function layer anywhere in MemPhant** — the only edge component is the thin `fly-replay` region router below, which contains zero business logic.
- **`tenant.region` is set at creation and immutable** (`03` §5.1): it is the tenant's home cell. A cell **refuses** a tenant whose `region` ≠ the cell's configured region (extending §7a's residency bootstrap-check from store-region to tenant-region — a misrouted tenant is a finding, never silent cross-region service). Region migration is an explicit **export-from-cell-A → import-into-cell-B** (the `17` data-portability path) + tombstone in A — **never** a live cross-region copy.
- **Tenant→region directory (global, no PII):** the control plane keeps one tiny global mapping `tenant_id → home_region` (+ status). It holds **only the routing key**, never memory/PII, so it is GDPR-safe to replicate globally — the single piece of global state.
- **Edge region-router (kept deliberately thin):** the public API (Fly Anycast / a global entry app) resolves the tenant from the auth key, looks up `home_region`, and routes to that cell via Fly **`fly-replay`**; a misrouted request is bounced to the home cell and **never served cross-region**. Memory bytes never leave their cell. The router does **only** key-extraction + cell-mapping — no business logic (adding logic here nullifies cell-based isolation); it is the one shared component, so it stays a thin lookup. `tenant.region` is **effectively immutable** post-onboarding: a region change breaks every integration whose URLs/assumptions are tied to the home cell, so migration is an explicit export→import, never a live re-route.
- **No cross-region data flow.** Postgres, bucket, Temporal jobs, recall, and `reflect`/GC all stay in-cell. Global aggregation (dashboards, billing) is limited to the **PII-stripped telemetry plane** (`22` §1.2), so cross-region operations never move memory.
- **Self-host/BYOC is one cell.** A self-hoster (Syndai included) deploys a single cell in their region; the directory + router are unnecessary at single-region scale. So the OSS library never carries multi-region machinery — it is added, closed-source, only by the operator running ≥2 cells.
- **Dogfood-neutrality holds** (`09` §9.1): Syndai's hosted tenant is region-pinned and routed through the same directory + router as any external Pro/Team tenant — no private cross-region path.

## 8. Neon

Neon is useful for:

- branch-per-eval replay
- migration dry runs
- restoring archived benchmark traces against historical data
- previewing schema changes

Neon-specific branching is an ops accelerator, not a core dependency.

## 9. App Surfaces

Public MemPhant web/dashboard is a product surface that talks to MemPhant HTTP APIs.

Syndai web/mobile are Syndai product surfaces:

- they call Syndai backend
- they do not call MemPhant DB
- they do not own memory policy
- they render citations/corrections returned by Syndai
- route changes require nearest `NAVIGATION.md` and regression coverage at implementation time

Mobile v1 should reuse Syndai Memory Hub behavior during dogfood.

## 10. Drift Alerts

Observability must emit alerts for:

- RLS/grant drift
- migration revision mismatch
- function `search_path` drift
- extension-in-public warning
- vector dimension mismatch
- deleted content appearing in recall
- cross-tenant recall count nonzero
- advisor critical finding in the `memphant` schema

DB drift is a memory security issue.

## 11. Migration Discipline (concrete mechanics)

The schema-isolation rules above say *what* schema; these say *how* to change it safely. Adopted verbatim from Syndai's CI-enforced discipline so MemPhant is correct from migration #1:

- **Constraint/index names ≤ 63 bytes** (Postgres truncates silently → drift). A name-length check gates CI.
- **`CHECK` on a populated table**: `ADD CONSTRAINT … NOT VALID` then a later `VALIDATE CONSTRAINT` (no full-table lock); always `DROP … IF EXISTS` first.
- **Index on a populated table**: `CREATE INDEX CONCURRENTLY … IF NOT EXISTS` inside an autocommit block, not a transaction.
- **Every table** gets `created_at DEFAULT now()` + an `updated_at` column with a shared `set_updated_at` trigger; a validator catches missing triggers/CHECKs/partial-indexes (autogen does not see them).
- **Descriptive long revision IDs** (text version column) so `memphant.schema_migrations` is human-readable.
- **Enum changes ship as CHECK edits**, ordered `drop old CHECK → UPDATE rows → add new CHECK`.
- **Apply-to-env-DB-before-push**: a green local build does not apply migrations; the live-DB contract check (`db_revision == expected_revision`) catches an unapplied migration before it becomes red CI.
- **Never `DROP TABLE`** without an explicit, current instruction; the boundary check (§12) rejects it.

### 11a. Provider-Adequacy Preflight (fail-closed before the first migration)

`db bootstrap-check --provider <p>` is a **gate that refuses an inadequate Postgres** before any DDL runs. **The no-superuser reality (the dominant BYOC case):** the §12 bootstrap SQL — mirrored from EvalRank's `2026_06_25_001` — runs `CREATE ROLE`, `CREATE EXTENSION`, and `ALTER DEFAULT PRIVILEGES FOR ROLE postgres`, **none of which a non-superuser migrator can run on managed Postgres** (Supabase elevates `postgres` via `supautils`; Cloud SQL gates `CREATE EXTENSION` behind `cloudsqlsuperuser`; RDS uses `rds_superuser`; Azure requires the extension in the `azure.extensions` allowlist). A raw customer URL whose role lacks these fails that bootstrap. The preflight probes, per provider:

- **Can the migrator `CREATE EXTENSION vector`/`pg_trgm`?** If not → `extension_not_installable`: emit the exact provider step and stop. MemPhant never runs without `vector`. If already present (Neon/Supabase ship it) → `IF NOT EXISTS` is a no-op; record the version.
- **Can the migrator `CREATE ROLE`?** Often forbidden → degrade to a **roles-as-grants fallback**: the three capabilities map onto roles the customer pre-creates and names in config; schema-boundary + lockdown grants still apply, only role *creation* moves out of band.
- **Can it `ALTER DEFAULT PRIVILEGES FOR ROLE <owner>`?** The EvalRank lockdown hardcodes `FOR ROLE postgres`; on BYOC the DDL owner is the customer's role — the preflight resolves the **actual owner** (`current_user`) and targets it.

**Version floors (checked, not assumed):** Postgres ≥ 17 (17 and 18 are both supported — pgvector supports PG18 since 0.8.1); **pgvector ≥ 0.8.4** (the `halfvec`/`binary_quantize`/`sparsevec`/iterative-scan levers require the 0.8 line, and 0.8.3/0.8.4 fixed HNSW vacuum corruption and maintenance errors — load-bearing here because `forget` makes the workload delete-heavy and every partition carries a local HNSW index; an older `vector` lacking `halfvec` makes the `02` §2.3 storage contract unrepresentable — a **hard reject**, not a degrade; compare on the feature set, not a guessed `>=`); `pg_trgm` present, `unaccent` optional. `db bootstrap-check` runs all three provider profiles in CI against MemPhant's dev Postgres (proves the *SQL* is portable) **but the same preflight also runs at `db bootstrap` time against the live customer URL — that is the real gate** (a green CI check against Syndai's own elevated DB would otherwise lie about a real customer's unprivileged role).

- **Build-memory headroom (pgvector HNSW).** A managed PG whose `maintenance_work_mem` is too small turns a ~20-min parallel HNSW build into hours of disk-spill or fails it — and the re-embed second-profile build needs that headroom *on top of* the live index (`14` §10.1). The preflight probes whether the migrator can raise `maintenance_work_mem` and estimates the build need (`rows × profile.dimensions × ~2 bytes`, a heuristic, not pgvector-official); if it can't reach it for `hnsw_full`/`hnsw_subvector` → `build_memory_insufficient` with remediation (raise the GUC, or switch the profile to `hnsw_binary`, or `exact`). **Warn-with-remediation, not a hard reject** — binary/exact are always-available fallbacks — but it must fire *before* a multi-hour silent disk build (Cloud SQL/Azure/RDS gate the GUC, sometimes non-session-settable by a non-superuser).

### 11b. Version-Skew Handshake (BYOC runs a pinned binary)

A BYOC customer upgrades the binary on their own cadence, so `memphant-server` routinely meets a schema it didn't just migrate. The handshake is two integers in `memphant.schema_migrations` (borrowed from Matrix/Synapse's `SCHEMA_VERSION`/`SCHEMA_COMPAT_VERSION`, which is the only studied design that protects against *downgrade* and needs *zero* central coordination — so it survives no-CLA forks): the binary's embedded **migration head**, and a **`schema_compat_revision`** floor stored in the DB (the oldest binary head the current schema still serves). On boot the binary compares both — **schema behind the binary** → refuse writes, emit "run `memphant db bootstrap` to apply N pending," exit fail-closed; **schema ahead** (binary rolled back / DB upgraded first) → serve only if the binary's embedded head ≥ the DB's `schema_compat_revision`, else refuse fail-closed (a memory substrate must not write rows a too-old reader will mis-read). The floor replaces an undefined per-migration "marked additive-only" scan a fork or hurried contributor mis-sets; it is bumped only by the *contract* step of a breaking change (§11c). No auto-migrate on a customer's DB — migrations apply only on the explicit `db bootstrap`/upgrade command (the customer owns the maintenance window; the BYOC corollary of `22` §4.1's "cannot auto-apply schema migrations").

### 11c. Additive-vs-Breaking Taxonomy + Forward-Compat Read Contract

`schema_compat_revision` (§11b) is honest only if "additive" is *defined*, not asserted. The rule: **can a binary embedding the OLD schema still read and write correctly against the NEW schema?** Forward-read tolerance (below) is the gate; lock cost (§11) is a separate axis that can *demote* a reader-additive change to a safe-recipe but never *promote* a breaking one.

| Change | Additive-only? | Why (old reader/writer behavior) |
|---|---|---|
| add NULLable column (no / non-volatile default) | **yes** | old reader's explicit column list ignores it; PG metadata-only, no rewrite |
| add column with **volatile** default (`now()`, `gen_random_uuid()`) | **banned** | reader-safe but rewrites the whole table under ACCESS EXCLUSIVE → rewrite as add-nullable + backfill |
| `CREATE INDEX CONCURRENTLY` / add new table | **yes** | invisible to old readers |
| widen **binary-coercible** type (`varchar(n)`→`text`) | **yes** | no rewrite; old value still representable. `int`→`bigint` is a rewrite + overflows an old `i32` reader → **breaking** |
| add enum value (TEXT+CHECK column) | **conditional** | SQL-additive, but a closed Rust `sqlx::Type` enum *errors at decode* on an unknown value → additive ONLY under the TEXT-with-fallback read rule; the frozen kind enum is the deliberate exception (frozen ⇒ no unknown value within a major) |
| add CHECK / NOT NULL / UNIQUE | **breaking** (default) | old writer can now produce a row the constraint rejects; safe only if proven old-writer-can't-violate |
| drop / rename / narrow column | **breaking** | old reader errors or truncates → expand-contract across two majors |

**Forward-compat READ contract (the rule that *earns* "additive"):** ① reads on frozen tables use runtime `query_as` with **explicit column lists** — never `SELECT *`, never the `query_as!` compile-time macro (it fails to compile against a newer column set, so a pinned binary couldn't read a self-hoster's newer DB); ② evolvable enum-like columns read as `text`/`&str` with an application fallback variant, never a closed Rust enum (sqlx decode is fail-closed on unknown values) — the frozen kind enum is the one allowed closed enum; ③ writers name only the columns they own, so a new nullable/defaulted column fills itself. A reader/writer that violates this forfeits the `additive-only` guarantee. (`03` §4 carries the adapter-side rule; parallel-change / expand-contract is the lineage.)

**Breaking-change protocol.** *Pre-launch (now):* **freeze hard, no window** — break frozen tables freely to reach a clean v1; this is the cheap moment, and the deliverable is a v1 so well-frozen the window rarely fires. *Post-launch (forks exist, no CLA):* **expand → migrate → contract** across two majors — expand adds the new shape beside the old (dual-write), the migrate window lets self-hosters and forks move on their own cadence (`memphant verify` surfaces "still on the deprecated shape" as drift), contract drops the old shape in a release that **bumps `schema_compat_revision`** so any too-old binary self-gates at boot. Drops never co-locate with the expand. The contract travels *in the data* (the floor in the ledger), so no central coordinator is needed — exactly what survives no-CLA forks.

## 12. Migration Scaffolding (the EvalRank-proven triad)

MemPhant's eventual repo carries its own migration runner (it does not join Syndai's Alembic chain). The design mirrors the shipped `evalrank_migrations/` triad — replicate it for `memphant`:

1. **`memphant_migrations/versions/*.sql`** — plain idempotent SQL, lexicographically ordered. The bootstrap migration `create schema if not exists memphant` + the six NOLOGIN capability roles (`memphant_owner`/`memphant_app`/`memphant_worker`/`memphant_authn`/`memphant_readonly`/`memphant_provisioner`) — per-role `statement_timeout`/`lock_timeout`/`idle_in_transaction_session_timeout` on the three that hold sessions (`memphant_app`/`memphant_worker`/`memphant_readonly`) — and **lockdown grants** (revoke all from `public` + browser roles `anon`/`authenticated`/`authenticator`; grant `usage` only to the five non-owner capability roles). There is no `memphant_cron` role. The capability roles are NOLOGIN and NOINHERIT, so `20260730_004_served_login_roles` adds one NOINHERIT **login** role per served capability (`memphant_app_login`, `memphant_authn_login`, `memphant_worker_login`, `memphant_provisioner_login`), passwordless in the migration and given credentials by `scripts/provision_login_roles.sh`. The served pool issues an explicit `SET ROLE` and refuses to serve if the effective role is SUPERUSER or BYPASSRLS — without that, FORCE RLS never fires on the served path.
2. **`apply_memphant_migrations.py`** — a minimal hand-rolled runner (not Alembic): applies `*.sql` in order, tracks the `memphant.schema_migrations(version, applied_at)` ledger, commits per migration, idempotent.
3. **`check_memphant_migration_boundary.py`** — the schema firewall: line-scans every migration and **fails** on `DROP TABLE`, any `public.` reference, or any `syndai.` reference. Wired into `make check` alongside the existing evalrank boundary check.
4. **`check_memphant_migration_class.py`** — the additive-vs-breaking classifier (a "cargo-semver-checks for SQL" analog, mirroring squawk/Atlas rule families onto the §11c taxonomy). It emits `additive`/`breaking`/`rewrite` per migration and enforces **floor-monotonicity**: a `breaking` migration MUST bump `schema_compat_revision`, an `additive` one must NOT. The classifier *computes* the flag — the author never asserts it (an attestation a fork mis-sets — the same lesson as preflight marker integrity). Its one blind spot, add-enum-value vs a closed Rust reader (§11c), is caught by a read-contract grep gate (no closed Rust enum / no `SELECT *` / no `query_as!` on frozen tables), not the SQL linter. Wired into `make check`.

The **doc-side firewall already exists** — `scripts/validate_docs.py` carries a live MemPhant rule set (`validate_memphant_specs`) and is extended in this pass (`28` cross-ref) — so only the migration triad above is net-new scaffolding.

**BYOC-portability caveat (the one place the EvalRank copy is not verbatim).** EvalRank's bootstrap `2026_06_25_001` runs `CREATE ROLE` and `ALTER DEFAULT PRIVILEGES FOR ROLE postgres` — both rely on Finn/Supabase pre-elevating `postgres`, which a raw customer Postgres does not (§11a). The MemPhant bootstrap migration must (a) resolve the DDL owner from `current_user`, not hardcode `postgres`, and (b) tolerate the no-`CREATE ROLE` provider via the roles-as-grants fallback (§11a). The schema-firewall check (`check_memphant_migration_boundary.py`) stays verbatim — it is provider-independent line-scanning.
