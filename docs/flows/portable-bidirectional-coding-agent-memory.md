# Portable bidirectional coding-agent memory

> **For implementation agents:** execute this plan in order with
> `superpowers:subagent-driven-development`. Each numbered task is a separate,
> reviewable commit. Do not open a later task until the preceding task's focused
> behavior passes. This document is the living implementation contract.

**Goal:** Make MemPhant a portable, token-efficient read/write memory service
for coding agents, with safe bitemporal correction/invalidation, owner-only
erasure, and automatic one-card delivery at the first Codex task boundary.

**Architecture:** PostgreSQL remains the single durable authority. Existing
memory units, scope policy, lineage, recall, trace, outcome, and file-projection
primitives are reused. The public coding-agent MCP exposes five intent tools;
all identity and authority come from one live bound-principal resolver. A thin
Codex plugin calls the same Streamable HTTP MCP server from the native
`UserPromptSubmit` hook and injects at most one 512-token card.

**Stack:** Rust 1.96, Axum, rmcp/MCP 2025-11-25, PostgreSQL 15+, pgvector,
Schemars-generated OpenAPI/MCP JSON, Python 3 stdlib for the portable hook.

## Spec

### Authority and product boundary

The approved design is
`docs/superpowers/specs/2026-08-14-portable-coding-agent-memory-design.md`.
This flow implements that design without adding another memory plane:

- PostgreSQL is the authority; Markdown and MCP resources are read-only
  projections.
- Git, repository files, `rg`, and LSP remain authoritative for current code.
- `memory` is the umbrella over the six existing kinds: episodic, semantic,
  procedural, resource, preference, and belief. Chat, docs/KB, coding turns,
  repository history, and outcomes are sources or evidence, not more kinds.
- Normal coding recall is provider-free, returns zero or one eligible compact
  memory, and is bounded to 512 tokens.
- There is one continuously ranked corpus. Do not add warm/cold state, a cache,
  a graph service, another vector store, an object-store dependency, or a
  coding-stage enum.
- Syndai is an optional client. No implementation in this flow may require its
  source tree, database, credentials, or runtime.

### Public coding-agent contract

The coding-agent MCP advertises exactly these tools:

1. `recall({query})`
2. `remember({kind, body, trigger, verification, target_scope_id?, valid_from?,
   valid_to?, source})`
3. `correct_memory({memory_unit_id, body, trigger, verification, reason,
   valid_from?, valid_to?, source})`
4. `invalidate_memory({memory_unit_id, reason_kind, reason, source})`
5. `report_memory_use({trace_id, outcome, used_ids})`

Where:

- `kind` is one of the existing six `MemoryKind` values; belief remains
  excluded from default recall.
- `target_scope_id` is an optional existing `ScopeId`. Omission means
  the live key's bound scope. It is applicability, not caller identity.
- `source` is `{kind, ref, observed_at, episode_id?, resource_id?}`. At most one
  canonical source ID may be present. Free-form provenance never grants
  authority.
- `reason_kind` is exactly `stale` or `harmful`.
- `preference` is accepted only when `source.kind` identifies an explicit user
  declaration or correction; inferred preferences remain invalid input.
- `outcome` reuses the existing `success`, `failure`, `corrected`, or `ignored`
  review outcomes.
- every mutation reuses the existing strict
  `McpMutation { idempotency_key, request }` envelope and mutation ledger; do
  not invent a second replay mechanism.

The server derives tenant, subject, subject generation, actor, agent node,
live trust ceiling, reporter identity, transaction time, stable content/fact
keys, and hashes. Public coding-agent payloads contain none of those identity
fields. The old MCP verbs `retain`, `reflect`, `correct`, `forget`, `trace`, and
`mark` have no compatibility aliases.

### Principal and capability invariants

- Every MCP tool call and resource read re-looks up one fully bound API key and
  resolves one live context. Startup-cached identity is comparison state, not
  continuing authority.
- Revocation, binding drift, subject-generation drift, missing context, or a
  raised live trust ceiling fails closed and asks for restart. A lowered
  ceiling applies immediately.
- `can_forget` and `can_audit_history` are independent API-key booleans,
  default false. Coding-agent keys receive neither.
- Every permanent-deletion HTTP/SDK/CLI path requires `can_forget` after live
  authentication. Absence of an MCP delete tool is not the security boundary.
- Every HTTP path with an explicit `transaction_as_of` or `valid_at` selector
  requires `can_audit_history`; ordinary
  coding recall cannot recover stale, harmful, superseded, expired, or erased
  bytes by supplying an earlier time.
- A mutation may act only inside its resolved scope policy and never above the
  live trust ceiling. A scope read grant never implies write authority.
- Arbitrary agent-supplied source references are informational and never widen
  eligibility. Only a canonical resource ID resolved server-side may carry a
  source ACL, and that ACL may narrow eligibility but never broaden it.

### Lifecycle invariants

`remember` creates one self-contained `Active` unit, including procedural
memory. The server deterministically derives the stable identity from the live
context, resolved target scope/node, semantic subject/normalized trigger, and kind,
and persists the compact-body digest. Exact idempotent replay returns the
original result.

`correct_memory` locks one open unit, closes its transaction interval as
`Superseded`, and creates one `Active` successor with explicit `Supersedes`
lineage. Changed bytes get the correction's source provenance and no cloned
citations, contextual chunks, or embedding. Valid-time remainders that preserve
the old bytes may preserve their old evidence.

`invalidate_memory` locks one open unit, closes it as `Superseded`, and appends
one current, bodyless `Invalidated` tombstone with the same stable identity.
Store `{kind: stale|harmful, reason}` under the existing
`payload.invalidation`; reuse `source_ref` and `observed_at` for its evidence.
No invalidation table, column, or new lifecycle state is justified.

An open invalidation or deletion tombstone blocks direct remember, reflect,
resource/episode compilation, replay, re-embedding, and writable file-sync for
the same stable identity or exact compact-body digest. This is an exact
lineage/content guarantee, not a claim that deterministic code recognizes every
semantic paraphrase. Only `correct_memory` may close an invalidation tombstone
and create a successor. Ranking and retrieval can never reopen it.

Owner forget is true erasure. It scrubs selected MemPhant-held bodies,
excerpts/citation payloads, contextual chunks, embeddings, blobs/caches, and
writable projections, while retaining only a content-free deletion tombstone
and mutation receipt. Historical audit cannot recover erased content. Source
and compact-unit selections remain explicit; forgetting one does not silently
expand to separately retained material.

### Retrieval and delivery invariants

- Lifecycle, valid-time, tenant/scope, trust, source-ACL, and deletion
  eligibility are applied in each bounded store query before `LIMIT`/cursor;
  core checks the same eligibility defensively before packing.
- Exact, lexical, vector, temporal, edge, deep, projection, and degraded paths
  cannot serve excluded states.
- Normal recall never renders raw episode/resource bodies. When only a pending
  raw source matches, return typed `unavailable` with
  `consolidation_pending`; do not turn backend/source lag into honest empty.
- Active procedural memories participate in normal recall and canonical
  projection. `Validated` remains available for evidence-backed workflows but
  is not required for fallible agent-authored memory.
- Portable normal recall admits only units carrying the typed compact-envelope
  marker in existing payload JSONB. A source compiler must emit a bounded
  compact envelope or leave the source pending/non-recallable; copying a full
  episode/resource body into an Active unit is not condensation.
- The one-card 512-token pack includes body, trigger, verification, provenance,
  lifecycle/currentness cues, and trace ID. If it cannot fit, it is rejected at
  write time as non-compact rather than truncated into misleading context.

Codex automatic delivery uses the first-party `UserPromptSubmit` hook. A
bundled stdlib-only helper sends a standard MCP initialize/initialized/recall
sequence to the already-running Streamable HTTP endpoint, then returns a Codex
hook envelope whose `additionalContext` contains either zero bytes or the one
honest recalled card. It does not parse transcripts, infer stages, start a
second MemPhant service, or access PostgreSQL directly. Unavailable/auth errors
remain visible on stderr and inject no memory bytes.

The plugin bundles the same MCP server configuration for explicit agent calls.
Claude portability is proven later with its native `UserPromptSubmit`
`additionalContext` hook using the same helper semantics; do not build a
second retrieval client before the Codex slice survives.

Official host references:

- Codex hooks and `UserPromptSubmit` additional context:
  <https://learn.chatgpt.com/docs/hooks>
- Codex plugin manifest, bundled MCP, skills, and hooks:
  <https://developers.openai.com/plugins/build/plugins>
- Claude Code hook input/output contract:
  <https://code.claude.com/docs/en/hooks>

### BDD acceptance

Given a fully bound live coding key, when an agent remembers one compact
procedural experience, then the same principal can immediately recall at most
one cited `Active` unit through MCP and the canonical projection; no validation
or background job is required.

Given any MCP call, when its key is revoked or its binding changes after server
startup, then the next call fails closed without reading or mutating memory.

Given a current unit, when an agent corrects it, then normal recall serves only
the successor; an authorized audit before the correction sees the predecessor;
and changed bytes carry only fresh provenance.

Given a current unit, when an agent marks it stale or harmful, then neither the
predecessor nor tombstone can appear in normal recall, source replay cannot
recreate it, and only an explicit correction can restore the identity.

Given a coding-agent bearer key, when it calls the owner forget endpoint or
supplies historical recall selectors, then the server returns 403 and changes
nothing. Given separately authorized owner keys, forget erases content while
audit can inspect non-erased history only.

Given a pending raw source with no eligible compact unit, when normal recall
runs through HTTP or MCP, then no raw source bytes appear and the result is
typed `unavailable(consolidation_pending)`, never empty. The Codex hook injects
zero bytes and preserves unavailable in diagnostics; projections omit the raw
source without representing that omission as a completed search.

Given the Codex plugin and an M1 scope containing one exact compact card, when a
new task prompt enters `UserPromptSubmit`, then the card is injected once before
the first material decision. Given a C0 empty scope, the same hook/tool/config
overhead injects no card.

### Bounded evidence sequence after implementation

These are product-learning screens, not validators or a general agent-
improvement campaign. A human reviews the natural trajectories and task result;
agent self-report is mechanism evidence only. Flat defaults off. Harm blocks
the lane. No screen opens its successor automatically.

| Order | Screen | Paid whole-agent calls | Advance only when |
| --- | --- | ---: | --- |
| 0 | Lifecycle smoke: remember, recall, correct, invalidate, report, owner forget | 0 | no stale resurrection, identity widening, raw leak, or agent delete |
| 1 | C0 empty scope vs M1 one reviewed procedural card through automatic Codex boundary | 2, at most $10 | exact card arrives before the decision and changes a material decision or avoids substantive work with no worse result |
| 2 | Same pair usefulness review | 0 | human judgment confirms the material effect; retrieval polish or self-report alone is flat |
| 3 | Same surviving M1 task through Claude | 1, at most $5 | same card and lifecycle contract work without representation changes |
| 4 | Exactly one real unmet memory kind/source lane | 2, at most $10 | one non-procedural lane adds material value unavailable as cheaply from current files/rg/git/docs |
| 5 | Explicit remember vs one bounded task-end nomination | 2, at most $10 | one independently useful card is produced without noise/privacy expansion |

The first tranche stops after Screen 3: three paid whole-agent calls and $15
maximum. Screens 4 and 5 each require fresh authorization plus concrete demand
observed in manual writes; if both are later authorized, the cumulative ceiling
remains seven calls and $35. Stop earlier on any kill gate. Record
repository/task/model/config hashes, exact card hash, MCP/hook
timing, decisions/actions, task-specific focused checks, tokens, cost, use
report, and short human judgment. Do not add a statistical judge, paired-result
validator, broad distractor corpus, or “smarter agent/SOTA” claim.

### Explicitly deferred

- warm/cold tiers, cache workers, and a second serving state;
- stage enums or ANALYZE/REPRODUCE/EDIT/VERIFY ranking priors;
- automatic transcript ingestion, general preference inference, or belief
  serving;
- automatic condensation/promotion before explicit writes show value;
- all memory-kind screens in one campaign;
- alternate graph/vector/KB/file authorities;
- mobile or Syndai product integration;
- dashboards and a generic administration UI;
- backwards-compatible aliases for the old MCP tools.

## Plan

### Global execution constraints

- Work only in this isolated linked worktree and preserve unrelated changes.
- Read all callers before changing a shared type or lifecycle predicate.
- For each nontrivial task: add the smallest failing behavioral test, confirm
  the intended failure, implement the root cause once at the shared seam, then
  run only that focused test while iterating.
- Use PostgreSQL constraints/transactions for concurrency and half-open time
  intervals; do not reproduce those rules in every handler.
- Reuse existing `MemoryStore`, mutation ledger, correction rectangles, scope
  policy, `Invalidated`/`Active` states, lineage edges, review events, trace,
  provider-free recall, Streamable HTTP, and projection code.
- Regenerate `openapi/memphant.v1.json` and `mcp/memphant.tools.v1.json` from
  binaries. Never hand-edit them.
- Do not create experiment validators or run paid models during implementation.
- Do not update `STATUS.md` until the named product proof and full repository
  gate exist in the same change.

### Task 1: Add operation capabilities and one live-principal resolver

**Files:**

- Create: `memphant_migrations/versions/20260814_011_portable_agent_memory.sql`
- Modify: `memphant_migrations/versions/20260703_001_wsa_bootstrap.sql`
- Modify: `crates/memphant-core/src/lib.rs`
- Modify: `crates/memphant-store-postgres/src/store.rs`
- Modify: `crates/memphant-server/src/lib.rs`
- Modify: `crates/memphant-mcp/src/lib.rs`
- Modify: `crates/memphant-server/tests/auth_contract.rs`
- Modify: `crates/memphant-mcp/tests/mcp_schema_contract.rs`
- Modify: `tests/test_wsa_migration_contract.py`
- Modify only as required by changed function signatures:
  `scripts/check_memphant_migration_contract.py`,
  `scripts/check_memphant_live_catalog.py`, and API-key provisioning callers.

**Contract:**

```rust
pub struct ApiKeyRow {
    // existing fields
    pub can_forget: bool,
    pub can_audit_history: bool,
}

pub struct LivePrincipal {
    pub context: ResolvedMemoryContext,
    pub api_key_id: Uuid,
    pub max_trust: TrustLevel,
    pub can_forget: bool,
    pub can_audit_history: bool,
}
```

`LivePrincipal` is returned by one resolver after re-looking up the key and
comparing every startup binding. It replaces `recall_context()` and
`bind_principal()` for MCP operations; do not add a resolver trait or role enum.

**Steps:**

1. Add the two `boolean not null default false` columns to the new migration
   and bootstrap `memphant.api_key` definition. In the same migration, widen
   the existing mutation-ledger verb CHECK to admit `invalidate`, and add
   `scope_policy.allow_write boolean not null default false`. Add a partial
   unique index for one open compact generation per stable fact key and a
   partial expression index over open invalidation `payload.compact.body_sha256`
   for the exact-body blockade. The unique key is
   `(tenant_id,data_subject_id,scope_id,agent_node_id,fact_key)` where
   `transaction_to is null`, `payload ? 'compact'`, and state is Active,
   Validated, or Invalidated. Extend
   `memphant.provision_api_key` with default-false capability arguments and
   preserve least privilege at every current caller. Update the store migration
   head/include list and `SCHEMA_COMPAT_REVISION` once.
2. Add a migration check that old rows remain false and only the provisioner
   can mint an authorized owner key or owner-managed write grant.
3. Extend `ApiKeyRow` and both in-memory/PostgreSQL lookups.
4. Replace the MCP split binding logic with `live_principal()`. Require all five
   context fields on every MCP call/resource read; compare key ID, tenant,
   subject, generation, actor, scope, node, and ceiling. Apply a lower live
   ceiling immediately; reject silent ceiling expansion until restart.
5. Extend `AuthedTenant` with key ID and capabilities. Re-check capabilities at
   the handler boundary for owner forget and any request with explicit
   `transaction_as_of` or `valid_at`; do not trust body identity.
6. Add focused tests for default-false migration, unbound key, revocation
   between calls, principal drift, trust-ceiling drift, agent HTTP forget 403,
   and agent historical recall 403.
7. Commit: `feat: bind coding memory operations to live capabilities`.

**Do not add:** a host/agent role, policy engine, capability table, wildcard
capabilities, or a second authentication middleware.

### Task 2: Add compact agent intent types and direct Active writes

**Files:**

- Modify: `crates/memphant-types/src/lib.rs`
- Modify: `crates/memphant-core/src/lib.rs`
- Modify: `crates/memphant-core/src/service.rs`
- Modify: `crates/memphant-store-testkit/src/lib.rs`
- Modify: `crates/memphant-store-postgres/src/store.rs`
- Modify: `crates/memphant-runtime/src/lib.rs`
- Modify: `crates/memphant-core/tests/embedding_channel.rs` only if shared
  fixtures require the new eligibility contract.

**Types:**

```rust
pub struct MemorySourceInput {
    pub kind: String,
    pub r#ref: String,
    pub observed_at: String,
    pub episode_id: Option<EpisodeId>,
    pub resource_id: Option<ResourceId>,
}

pub struct RememberRequest {
    pub kind: MemoryKind,
    pub body: String,
    pub trigger: String,
    pub verification: String,
    pub target_scope_id: Option<ScopeId>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub source: MemorySourceInput,
}
```

Add equally compact `CorrectMemoryRequest`, `InvalidateMemoryRequest`, and
`ReportMemoryUseRequest`. Correction/invalidation select by `UnitId` only.
Their edge adapters resolve `LivePrincipal`, then pass only the authorized
`ResolvedMemoryContext`, live trust ceiling/key ID where needed, request, and
idempotency key into `MemoryService`; core does not depend on an MCP auth type.
No public intent type carries identity.

Store trigger in the existing `memory_unit.predicate`. Store verification in
the existing unit `payload.compact` JSONB with `schema_version = 1`,
`verification`, `body_sha256`, and `write_channel`; body stays the compact
primary rendering. Extend unit mappers to preserve the typed compact
metadata rather than creating a table or columns for two strings. The public
content hash is the existing compact-body SHA-256; do not claim it hashes an
unprovided external source body.

**Steps:**

1. Add strict size/format validation: nonblank body/trigger/verification/source,
   RFC3339 times, nonempty valid interval, and a deterministic rendered
   512-token upper bound using the existing packer accounting. Reject
   preference memory unless its source is an explicit user declaration or
   correction.
2. Resolve omitted `target_scope_id` to the bound scope/node. A different
   target is authorized only by one owner-created `scope_policy` row whose
   grantee scope/node is the live binding, whose source scope is the target,
   whose kind matches, and whose `allow_write` is true; derive the target agent
   node from that row. It also requires one canonical episode/resource source
   resolved in the bound context; a resource ACL must authorize the target.
   Free-form source refs and read-only grants are denied for cross-scope writes.
   Never create scopes/grants from this call.
3. Derive the stable key from resolved context, target scope/node, kind, and
   semantic subject/normalized trigger with the existing SHA-256
   primitives. Persist the compact-body SHA-256 in typed payload metadata so an
   open tombstone blocks exact-body recreation even if caller provenance drifts.
4. Reuse the direct-unit persistence path but mint `UnitState::Active`. Preserve
   the caller's evidence source kind/ref in existing provenance columns and set
   `payload.compact.write_channel = agent_memory`. Do not enqueue reflection
   for a compact unit.
5. Enforce read-your-write behavior and exact mutation-ledger replay.
6. Add one table-driven in-memory-store test for all six kinds, compactness,
   scope containment, read-grant denial, explicit write-grant placement, and
   idempotency. Assert belief persists but is not in default recall.
7. Commit: `feat: add compact principal-derived memory intents`.

### Task 3: Canonicalize correction, invalidation, and no-resurrection

**Files:**

- Modify: `crates/memphant-core/src/lib.rs`
- Modify: `crates/memphant-core/src/service.rs`
- Modify: `crates/memphant-store-postgres/src/store.rs`
- Modify: `crates/memphant-store-testkit/src/lib.rs`
- Modify: `crates/memphant-runtime/src/lib.rs`
- Add focused cases to existing core/store test modules; do not create a new
  lifecycle framework.

**Store seam:**

```rust
pub enum AgentMemoryMutation {
    Remember(RememberWrite),
    Correct(CorrectionWrite),
    Invalidate(InvalidationWrite),
}

pub struct InvalidationWrite {
    pub target: UnitId,
    pub reason_kind: InvalidationReason,
    pub reason: String,
    pub source_ref: String,
    pub observed_at: String,
}
```

If the existing transaction trait is clearer with three methods, keep those
methods; do not introduce `AgentMemoryMutation` merely to have an enum. The
required abstraction is one shared open-generation/tombstone predicate used by
both stores and all ingress paths.

**Steps:**

1. Change correction generation so changed bytes receive fresh source fields,
   empty contextual chunks/citations, and trust clamped to the minimum of
   target, actor, and live-key ceiling. Persist the explicit correction reason,
   refresh compact metadata/body hash, and preserve old evidence only on
   unchanged valid-time remainders.
2. Add `stage_invalidation`: `SELECT ... FOR UPDATE` the open target, close it
   as `Superseded` at database transaction time, append an `Invalidated`
   tombstone with same stable key, empty body, typed reason in the existing
   payload, source fields, and a
   `Supersedes` edge.
3. Reject correction/invalidation across subject/scope/actor or above the live
   trust ceiling. Allow correction to select an open invalidation tombstone and
   atomically replace it; no other write may do so.
4. Add one shared open-tombstone lookup to direct remember, compiled-unit
   persistence, reflect/resource paths, replay, re-embedding, and any remaining
   writable file-sync service path. Match both stable identity and exact
   compact-body digest. Delete per-caller partial guards.
5. Pin half-open transaction-time behavior in both in-memory and scratch-store
   testkit: active before transition, no normal value after invalidation, one
   successor only after correction, no dual current generations under a
   correction/invalidation race.
6. Assert exact, lexical, vector, edge, deep, restart, and source replay cannot
   recreate or serve an open tombstone identity.
7. Commit: `feat: enforce bitemporal coding memory lifecycle`.

### Task 4: Make owner forget erase content and remain owner-only

**Files:**

- Modify: `crates/memphant-core/src/lib.rs`
- Modify: `crates/memphant-core/src/service.rs`
- Modify: `crates/memphant-store-postgres/src/store.rs`
- Modify: `crates/memphant-server/src/lib.rs`
- Modify: existing forget tests in core, store testkit, and
  `crates/memphant-server/tests/auth_contract.rs`.

**Steps:**

1. Keep `ForgetTarget::{MemoryUnit,Episode,Resource}` and the existing mutation
   ledger/deletion generation. Do not add delete variants.
2. At the shared HTTP/service boundary require `live.can_forget`; CLI/SDK gain
   no alternate bypass. Apply the same check to an administrative file-sync
   request containing `FileSyncOperation::Forget`.
3. In one PostgreSQL transaction, lock the explicit target, persist the
   forgotten-source/no-resurrection marker, traverse the selected stable
   supersedes lineage in both directions, and scrub every lineage member plus
   composition dependents. Episode/resource erasure also scrubs directly
   derived units and their correction descendants. Delete embeddings,
   citations, chunks, and derived payloads; blank/null stored content and
   source excerpts; retain only content-free `Deleted` tombstones/receipt.
4. Ensure authorized as-of reads return no erased bytes at any time. Preserve
   non-erased related source material unless explicitly selected.
5. Add one scratch-store behavioral test covering 403/no mutation, authorized
   erasure, idempotent replay, key revocation, historical non-recovery, and
   source replay blockade.
6. Commit: `feat: make owner forget a capability-gated erasure`.

### Task 5: Centralize normal eligibility and retire raw fallback

**Files:**

- Modify: `crates/memphant-core/src/lib.rs`
- Modify: `crates/memphant-core/src/service.rs`
- Modify: `crates/memphant-store-postgres/src/store.rs`
- Modify: `crates/memphant-store-testkit/src/lib.rs`
- Modify: `crates/memphant-types/src/lib.rs`
- Modify: `crates/memphant-server/src/lib.rs`
- Modify: `crates/memphant-server/tests/rest_contract.rs`
- Modify: `crates/memphant-mcp/src/lib.rs`
- Modify: focused recall tests under `crates/memphant-core/tests/` and existing
  service/store test modules.

**Steps:**

1. Define `normal_recall_eligible(unit, time, policy)` admitting only typed
   compact envelopes in current permitted states/kinds. Active and Validated
   procedural compact units are eligible; belief remains default-off.
   Invalidated, Superseded, Expired, Deleted, Quarantined, Candidate, and
   uncondensed/raw-body units are ineligible.
   Separately define `audit_visible_at(unit, time)` so an authorized audit may
   see a Superseded predecessor inside its half-open transaction interval while
   always excluding erased content. Never reuse the normal predicate for audit.
2. Mirror the normal predicate in every PostgreSQL candidate query before cursor and
   `LIMIT`, including exact/lexical, vector, temporal, edge, and deep seeds.
   Core applies it again before scoring/packing.
3. Remove `degraded_episode_items` and the raw-body call site. Stop compiler
   projection paths from minting an Active compact marker when they merely copy
   the source body. If matching raw sources are pending consolidation and no
   compact unit exists, return a typed service error
   `ConsolidationPending`; otherwise retain honest empty.
4. Update canonical projection to include current Active procedural memories
   and exclude every tombstone/archive state.
5. Require `can_audit_history` whenever `transaction_as_of` or `valid_at` is
   explicitly supplied.
   Authorized audit uses `audit_visible_at`; erased bytes remain absent
   regardless of capability.
6. Add one table-driven matrix for create/correct/invalidate/expire/erase across
   normal now, authorized transaction-as-of, and valid-at reads. Add a pending
   raw-source regression asserting HTTP maps `ConsolidationPending` to a typed
   503 error, MCP maps it to typed unavailable, hooks inject no body and log the
   code, and projections expose no source bytes.
7. Commit: `feat: make compact lifecycle eligibility fail closed`.

### Task 6: Replace the MCP router with the five intent tools

**Files:**

- Modify: `crates/memphant-mcp/src/lib.rs`
- Modify: `crates/memphant-mcp/src/file_memory.rs`
- Modify: `crates/memphant-mcp/tests/mcp_schema_contract.rs`
- Modify: `crates/memphant-mcp/tests/distribution_wedge.rs`
- Modify: `crates/memphant-mcp/tests/edge_auth.rs`
- Regenerate: `mcp/memphant.tools.v1.json`
- Regenerate: `mcp/memphant.resources.v1.json`

**Steps:**

1. Keep the query-only `McpRecallRequest` and fixed limit-one/512/provider-free
   defaults. Map the four new intent DTOs to the Task 2 service methods.
2. Reuse the existing validated `McpMutation<T>` idempotency envelope and derive
   reporter identity from `live.api_key_id`. Reject duplicate reports
   for the same trace/principal using the existing review-event uniqueness;
   remove caller-controlled `caller_id` from this surface.
3. Register exactly the five tool names. Update server metadata and generated
   schema; delete old handler methods rather than keeping aliases.
4. Remove `MemoryCommand`/`anthropic_memory_tool` from the coding server because
   MCP resources already cover list/read. Delete
   Create/StrReplace/Insert/Delete/Rename and their dispatcher; keep only shared
   projection generation used by resources.
5. Make resource listing/reading call `live_principal()` per request.
6. Add schema tests asserting exact tool names, no identity/delete/audit fields,
   strict unknown-field rejection, and typed hit/empty/unavailable/error
   results. Add a persistent-process test that revokes the key between each of
   the five calls.
7. Commit: `feat: expose five portable coding memory tools`.

### Task 7: Add the automatic Codex plugin boundary

**Files:**

- Create: `plugins/codex-memphant/.codex-plugin/plugin.json`
- Create: `plugins/codex-memphant/.mcp.json`
- Create: `plugins/codex-memphant/hooks/hooks.json`
- Create: `plugins/codex-memphant/hooks/user_prompt_submit.py`
- Create: `plugins/codex-memphant/tests/test_user_prompt_submit.py`
- Modify: `README.md`

**Transport contract:**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "<zero or one MemPhant card>"
  }
}
```

**Steps:**

1. Configure one Streamable HTTP MCP endpoint in `.mcp.json`; the hook and
   Codex explicit tools use the same URL and bearer key. Do not launch a second
   stdio server from the hook.
2. Implement the hook with Python stdlib only. Read one JSON object from stdin;
   require string `prompt` and `cwd`; construct a bounded query from those two
   fields; perform MCP initialize, initialized notification, and `tools/call`
   `recall` over the existing endpoint/session header.
3. Parse only the typed recall result. On hit, return its already-packed card in
   `additionalContext`; on empty, return empty context; on auth/unavailable,
   write one concise stderr diagnostic and return empty context without
   mislabeling the backend result as an empty memory search.
4. Set strict timeouts and a hard response-size ceiling. Never log bearer keys,
   prompts, recalled bodies, or raw responses. Do not retry a mutation or a
   partially accepted response.
5. Add one stdlib fake-HTTP behavioral test for hit, empty, unavailable,
   malformed/oversized response, timeout, and secret-free diagnostics. Add one
   scratch real-process smoke proving the card is present before the first task
   action and injected once.
6. Document installation, server startup, key binding, inspection, correction,
   invalidation, and owner deletion. Make the no-Syndai path the first example.
7. Commit: `feat: inject one memory at the Codex prompt boundary`.

### Task 8: Prove the standalone value chain before paid work

**Files:**

- Modify: `scripts/e2e_probe.sh`
- Add the narrowest existing Rust/Python contract test needed by the probe;
  do not create an experiment harness.
- Create only after the smoke passes:
  `docs/build-log/artifacts/portable-coding-memory-lifecycle.json`

**Steps:**

1. In ephemeral scratch PostgreSQL, provision one fully bound coding key, one
   owner-forget key, and one owner-audit key. Assert coding capabilities false.
2. Through real MCP/HTTP binaries execute:
   `remember → recall → correct → recall → invalidate → recall → blocked replay
   → report`, then owner audit and owner forget.
3. Assert exact stable IDs/hashes, fresh correction provenance, no old/current
   recall after invalidation, no resurrection after restart, agent 403 for
   forget/audit, and no content recovery after owner erasure.
4. Exercise the Codex hook against C0 and M1 scopes. C0 is exact empty; M1 is
   exactly one complete card under 512 tokens. Any header/rendering difference
   is a transport defect and blocks paid calls. Record cold-process and
   warm-service latency separately; the automatic boundary remains default-off
   if representative local p95 exceeds 1 second cold or 300 ms warm. Optimize
   handshake/query work before considering a cache.
5. Write a public-safe artifact containing versions, hashes, counts, predicates,
   and no prompt/card/source/secret bodies. Register an evidence contract only
   if repository policy classifies the artifact as decisional; do not invent an
   efficacy validator.
6. Commit: `test: prove portable coding memory lifecycle end to end`.

### Task 9: Documentation, generated artifacts, and ledger closure

**Files:**

- Modify: `README.md`
- Modify: relevant files under `docs/superpowers/specs/memphant/`
- Confirm generated parity: `openapi/memphant.v1.json`
- Confirm generated parity: `mcp/memphant.tools.v1.json`
- Confirm generated parity: `mcp/memphant.resources.v1.json`
- Modify only with named passing proof:
  `docs/superpowers/specs/memphant/STATUS.md`
- Modify `AGENTS.md` only if implementation reveals a concise durable invariant
  not already covered. Do not restate this flow.

**Steps:**

1. Document the five tools, read-only resources, capability boundaries,
   bitemporal behavior, compact envelope, source-vs-kind model, and automatic
   Codex setup.
2. Remove normative claims that agent deletion, writable file memory, raw
   fallback, or Validated-only procedural recall are current behavior.
3. Confirm generated schemas match the final binaries and provider docs show
   the same contract. Regenerate only an artifact whose owning generator
   reports a diff; Task 6 owns the MCP artifact change.
4. Run the Harness exactly. Fix only failures caused by this work; report
   unrelated baseline failures without rewriting them.
5. Update STATUS only for product capabilities proven by Task 8 plus the full
   Harness. Do not mark automatic nomination, Claude portability, cross-repo
   value, general improvement, or SOTA complete.
6. Commit: `docs: ship portable coding-agent memory contract`.

### Post-implementation experiment dispatch

Experiments are separate follow-up work requiring explicit paid-run
authorization. Execute the Spec table in order. Screen 0 reuses Task 8 and costs
$0. Screen 1 is exactly one C0/M1 Codex pair. Do not run Screen 3–5 unless the
prior human judgment advances. Do not modify product code to rescue a flat
card/task; first decide whether the source lane itself was redundant.

Do not run a storage-substrate experiment until representative PostgreSQL
traffic misses a written latency or cost objective. Identical agent-visible
bytes are not a reason to spend model calls or introduce another authority.

### Engineering review coverage map

```text
CODING WRITE
MCP mutation envelope
  -> live_principal
     -> revoked/drifted/unbound --------------------> typed auth/scope error
     -> bound context
        -> bound target ----------------------------> remember Active compact
        -> other target
           -> no/read-only grant -------------------> deny
           -> owner write grant + canonical source
              -> resource ACL denies ---------------> deny
              -> authorized target scope/node ------> remember Active compact
  -> duplicate idempotency key
     -> same request --------------------------------> original receipt
     -> different request ---------------------------> conflict

LIFECYCLE
Active compact
  -> correct ----------------------------------------> closed predecessor + fresh Active successor
  -> invalidate(stale|harmful) ----------------------> closed predecessor + open bodyless tombstone
Open tombstone
  -> remember/reflect/replay/exact-body recreation --> conflict
  -> correct ----------------------------------------> closed tombstone + fresh Active successor
Any stable lineage
  -> owner forget without capability ----------------> 403, no mutation
  -> owner forget with capability -------------------> scrub lineage/derivatives + content-free receipt

CODING READ
query -> live principal -> store predicates before LIMIT
  -> eligible compact current -----------------------> one <=512-token hit + trace
  -> no relevant compact/current --------------------> honest empty
  -> only pending raw source ------------------------> unavailable(consolidation_pending), zero raw bytes
  -> normal stale/archive/deleted -------------------> excluded
  -> owner audit + capability -----------------------> audit_visible_at half-open history, never erased bytes

AUTOMATIC DELIVERY
Codex UserPromptSubmit -> stdlib MCP client -> recall
  -> hit --------------------------------------------> one additionalContext card
  -> empty ------------------------------------------> zero context
  -> unavailable/auth/timeout/malformed/oversized ---> zero context + secret-free diagnostic
```

Focused tests in Tasks 1–8 cover every branch above. The scratch real-process
probe covers the three-component auth/service/PostgreSQL and destructive paths;
the stdlib fake covers hook network/error branches. No LLM evaluator is needed
for these deterministic product contracts. Natural-agent usefulness remains the
separate bounded human-reviewed sequence in the Spec.

## Harness

No command in this harness is an efficacy judge. Focused behavioral tests prove
the product contract; repository gates protect the public runtime. Paid agent
experiments are not part of the harness.

```sh
python3 -m pytest tests/ -q
python3 scripts/check_spec_drift.py
python3 scripts/instrument_power.py --check
python3 scripts/check_evidence_contract.py
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
cargo test --doc
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant MEMPHANT_TEST_DATABASE_URL cargo test -p memphant-store-postgres -p memphant-worker --all-targets -- --ignored --test-threads=1
cargo run -p memphant-cli -- db lint --provider plain-postgres
cargo run -p memphant-cli -- db lint --provider supabase
cargo run -p memphant-cli -- db lint --provider neon
python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run
DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant bash scripts/e2e_probe.sh
```

The full harness runs only after implementation. This planning turn does not
run it or any paid/quality campaign.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture, lifecycle, tests, performance | 1 | CLEAR | 11 material findings folded; 0 critical gaps remain |
| Reverse Code-Fit | parallel code review | Exact current seams and migration ownership | 2 | CLEAR | Compact discriminator, typed lag, store/runtime mappings applied |
| Reverse Security | parallel lifecycle review | Capability, ACL, erasure, resurrection | 2 | CLEAR | Both temporal axes, explicit write grants, lineage erasure applied |
| Reverse KISS | parallel scope review | Remove duplicate surfaces and speculative work | 2 | CLEAR | REST duplication, early docs, tiers, stores, and extra experiments removed |

**VERDICT:** ENG REVIEW CLEARED — the plan is ordered, bounded, and ready for implementation.

NO UNRESOLVED DECISIONS
