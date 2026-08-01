# MemPhant - Ingestion, Seeding, and Ops Spec

## 0. Scope

This doc owns importers, background jobs, seed examples, operational jobs, and cutover paths from existing memory systems.

## 1. Ingestion Inputs

| Source | V1 status |
|---|---|
| Direct SDK `retain` | build |
| MCP `retain` | build |
| JSONL episode import | build |
| Markdown/project files | build simple parser |
| Existing Syndai memory export | build adapter after trace comparison |
| Mem0/Zep/Hindsight imports | build file-based import mappers for exported JSON/JSONL/Markdown; no cloud scraping |
| Browser/DOM/screenshot streams | accept as resource pointers or files through SDK/MCP; no live browser stream service |

## 1.1 Importer Priority

Build importers in this order:

1. JSONL episodes.
2. Markdown/files/resources.
3. Syndai memory export after trace comparison proves the mapping.
4. OpenAPI-friendly generic export format.
5. File-based Mem0/Zep/Hindsight import mappers.

Do not write one-off importers for launch optics. Importers create long-term data-shape promises.

> Not Letta `.af`: the Agent File format serializes in-context memory *blocks* + chat history, but **archival passages (Letta's long-term store) are roadmap-only, not in the export** — a `.af` carries no memory corpus to import. Its chat history is ordinary JSONL episodes (importer #1); no dedicated `.af` importer.

### 1.2 Import Mapper Contracts

Importers are file-based field maps, not adapters. Each names what the export *actually is*, so the trust/kind decision is explicit.

**Mem0 (the trust decision).** A Mem0 export carries **LLM-extracted facts, not ground truth** — each record `{id, memory, metadata, categories, created_at, updated_at}` scoped by `user_id`/`agent_id`/`run_id`; `memory` is a derived statement phrased for retrieval, and the export does **not** include source messages (unless produced with `infer=false`).

| Mem0 field | MemPhant target | Note |
|---|---|---|
| `memory` (extracted fact) | `memory_unit.body`, **`kind='belief'`** | not `semantic` — a lossy extraction is uncorroborated by construction |
| `id` | provenance `original_id` | never reused as a MemPhant id |
| `user_id`/`agent_id`/`run_id` | `subject_id`/`actor_id`/`scope.ref` | declared in the batch manifest |
| `created_at` | `observed_at` + `episode.first_observed_at` | not `valid_from` — extraction time ≠ world-validity |

Import floor: **`belief` at `imported_external` trust (0.6), never `semantic`** — promoting on import would forge the `04` §5 independence gate. Each fact gets a synthetic `episode` (`source_kind='import'`, flagged `derived_import` — the extraction, not the event), so the citation path exists; re-derivation is impossible (no raw messages).

**Zep/Graphiti (the lossless case).** Graphiti exports **episodes as ground truth + bitemporal entity edges** (`t_valid`/`t_invalid`) — maps almost 1:1: episode node → `episode` (real ground truth); entity edge (fact + validity) → `semantic` unit + `valid_from`/`valid_to`; an `invalidated` edge → `supersedes` (never a row delete, invariant #6). Because raw episodes are present, facts may import at `imported_external` **and** be re-derivable — but never above the export ceiling.

**Generic JSONL** → §2 raw-episode format directly. **Markdown** → `resource` pointers + chunks (`04` §6.1), never facts.

### 1.3 Import-as-is vs Re-derive

Two dispositions, decided per source by *whether the export carries raw episodes*, never by convenience: **`as_is`** (no raw transcript — Mem0, Markdown: import the fact as a `belief` at the export ceiling, frozen at that quality) vs **`re_derive`** (raw episodes present — Graphiti, JSONL, Syndai export: import raw episodes as ground truth, let MemPhant's own `reflect` re-extract; imported units ride along as `belief` precursors but the system's extraction wins on conflict). Default to `re_derive` when raw episodes exist. An importer's `(source_field → memphant_field)` map + trust-floor are a **public contract** — a map change is a versioned, migration-bearing change, which is why the matrix stays narrow (Mem0/Zep/JSONL/Markdown) and a broad adapter zoo is rejected (every adapter is a forever-promise).

## 2. Raw Episode Format

Seed import format:

```json
{
  "tenant_id": "tn_...",
  "subject_id": "sub_...",
  "scope": {"type": "project", "ref": "p1"},
  "actor": {"type": "user", "ref": "u1"},
  "source": {"kind": "chat", "ref": "import:001"},
  "occurred_at": "2026-06-25T00:00:00Z",
  "content": "raw event text",
  "metadata": {}
}
```

## 2.1 Continuous Ingest (the streaming customer)

Importers are bulk/file-based; Syndai is the *continuous* case — a live stream of `retain` calls. It reuses the write path (`02` §3.0), not a second engine:

- **Each `retain` is independently durable** — the 202 follows the per-episode row+enqueue commit; no client-side batching for durability. Batching is a server-side `reflect` cost concern, never a capture one.
- **Stream idempotency is `dedup_key` (`04` §2.3), not the request** — a redelivered event with a matching key collapses into the existing episode (`observation_count += 1`), so an at-least-once producer (a reconnecting Syndai worker) replays safely without double-counting.
- **Backpressure is the only abnormal-load contract (`02` §3.1)** — a burst that outruns extraction never rejects `retain` (capture stays <200ms); it grows the un-`extracted` lag, sheds `reflect` by tier, raises `consolidation_lag`, and recall declares `degraded`. The stream never sees a write `429` for *consolidation* lag — only the admission `ConcurrencyLimit` (`02` §1.1a) sheds *request* load.
- **No streaming-specific store** — a stream is N ordinary `retain`s; the pgmq enqueue is the only queue (no Kafka, no ingest buffer; architecture non-goal alignment).

Graphiti #1574 is the counter-example this contract structurally prevents: an MCP ingestion queue worker was garbage-collected, so `add_memory` **acked but never persisted** — silent async ingestion loss. MemPhant's 202 follows the transactional row+enqueue commit (`02` §3.0), so an ack without a durable episode cannot happen by construction.

## 3. Job Types

```text
chunk_episode
embed_chunk
extract_entities
extract_fact_candidates
dedupe_memory_units        # derived-unit dedup (distinct from episode near-dedup, 04 §2.3)
promote_semantic           # independent-source corroboration gate (04 §5)
promote_procedure          # write side of the 04 §4.2 gate — UNBUILT (§3.3)
detect_contradiction       # subject-key + proximity + valid-overlap (04 §3.1)
update_decay
refresh_stale_fact         # active freshness due scan; no separate queue (04 §8.1)
tier_episode               # retention-tier hot/warm/cold demotion+promotion (04 §2.4, §13.3) — UNBUILT (§3.3)
reembed_profile
purge_forget
```

Each job is idempotent by `(job_type, target_id, compiler_version)`.

### 3.2 Scheduling Substrate

Periodic jobs (`tier_episode`, `update_decay`, `refresh_stale_fact`, the operational sweeps) do **not** run on an in-process sleep loop — they use Syndai's proven durable pattern so a worker crash never silently stops the schedule: **pg_cron → `pgmq` queue → Temporal executor**, with a `job_heartbeats` dead-man's-switch row so a stalled schedule is observable. `refresh_stale_fact` scans the indexed `freshness_due_at` rows from `04` §8.1; it is not a second queue. `reflect` itself is event-driven (enqueued on `retain`), demand-tiered, and batched.

**Producer-side envelope contract (Syndai-production-proven; do not regress).** The pg_cron producer SQL must emit the consumer's exact `{type, payload}` envelope — a flat message (bare job-type fields) enqueues fine and **silently dead-letters** at the consumer, so the schedule looks healthy while nothing runs. Two deterministic controls: (1) the producer SQL is **drift-gated verbatim** against the consumer's envelope schema (the deployed cron source is compared byte-for-byte to the repo source), and (2) queue health is verified via **DLQ depth** (§4.1 `job_dlq_depth`), never via cron run status — pg_cron reports success even when every message it emitted was dead-lettered.

## 3.1 Job Record

```text
job_state
  id
  tenant_id
  job_type
  target_kind
  target_id
  compiler_version
  status                  -- 'queued'|'claimed'|'running'|'succeeded'|'failed'|'parked'  (terminal machine, §10.1)
  attempt_count
  max_attempts            -- the attempt cap; past it the job is parked, not retried forever (§10.1)
  idempotency_key
  last_error_code
  locked_until            -- lease mirror of the pgmq vt (02 §6.2); ops-visible, lapses => redeliver
  stage_completed_mask    -- per-stage resume checkpoint for reflect (04 §9.4)
  parked_at               -- set when the job enters the terminal 'parked' DLQ state (§10.1)
  parked_reason
  created_at
  updated_at
```

Jobs must be resumable after process death. No correctness-critical state lives only in memory. `locked_until` is **live, not decorative**: it is written when the job is claimed (`= now() + pgmq_vt`, `02` §6.2) and is the lease the failed-job sweep (§4.1) and `job_heartbeats` dead-man's-switch (`02` §6.1) read to detect a worker that died holding a job.

### 3.3 Governance-Core Jobs (the ops contract for `04` §13)

> **SPECCED-UNBUILT (verified 2026-07-30).** `tier_episode` is named in this
> list, in `04` §2.4, and in `02` §6, and **does not exist in code**:
> `grep -rn 'retention_tier' crates --include='*.rs'` returns **0 hits**, so the
> column (`memphant_migrations/versions/20260703_001_wsa_bootstrap.sql:278`) has
> zero readers and zero writers and every episode is `'hot'` forever. The
> *lifecycle* contract these jobs implement is owned by `04` §13 (§13.3
> transitions, §13.4 demotion); this section owns only the **ops** half —
> schedule, inputs, bounds, idempotency, failure handling, alarm.

**`tier_episode`** — **WITHDRAWN 2026-07-31.** The column it was to write was dropped by `20260801_008_drop_retention_tier.sql` (0 readers, 0 writers, empty index). Retained below only as the record of what was specced and never built. Was: the only writer of `episode.retention_tier` (`04` §13.3
RT-3).

| Property | Contract |
|---|---|
| Schedule | periodic via the §3.2 substrate (pg_cron → pgmq → Temporal). **Never** an in-process loop; **never** on the recall path |
| Inputs | episode age, last citing-recall timestamp, DSR retrievability of derived units (`04` §8), and the tenant policy parameters `T_warm`/`R_warm`/`W_warm`/`W_cold` |
| Enqueue sources | (a) the periodic schedule for demotion; (b) the Stage-7 cold-fetch path for **re-promotion only** — recall may enqueue, never write (`04` §13.3 RT-3) |
| Batch bounds | capped episodes-per-round with the §3.2 bisection-retry discipline; one malformed episode never blocks the round |
| Idempotency | `(tier_episode, episode_id, compiler_version)` — two runs in one policy window produce one transition (`04` §13.3 RT-5) |
| Trace | every transition records the from/to tier **and the predicate values that fired it** (`04` §13.3 RT-4). A silent transition is a defect |
| Failure | a failed demotion leaves the episode at its current tier — demotion is never partially applied. A failed *re-promotion* must retry: an episode stuck cold after a citing recall is the user-visible failure mode |
| Never does | delete a raw episode, drop a citation, or change unit `state`. Deletion is `purge_forget` and is a different mechanism entirely (`04` §13.4) |

**`promote_procedure`** — the write side of the `04` §4.2 gate, which today has
none (the read gate at `crates/memphant-core/src/lib.rs:8362` has nothing that
can pass it).

| Property | Contract |
|---|---|
| Trigger | a recorded validation outcome (replay result or deterministic check), never a schedule and never recall frequency |
| Effect | `procedure_candidate → validated` **only** when `validation = {method, trials, wins}` meets the `04` §4.2 threshold **and** the adversarial high-risk-step assertion passes |
| Idempotency | `(promote_procedure, unit_id, compiler_version)`; re-running a promotion that already happened is a no-op, not a second generation |
| Trace | the validation evidence is durable and replayable — a `validated` unit whose promoting evidence cannot be re-read is a defect |
| Never does | promote on corroboration count, on agreement between agents, or on an LLM judgment (`04` §4.2) |

**Unit demotion has no job yet, on purpose.** Whether demotion is a stored tier
(needing a `demote_unit` sweep) or a pure recall-time predicate (needing no job
at all) is **`04` §13.7 OPEN-4**. Do not add a job for it before that closes —
adding the sweep first would pick the answer by accident.

## 4. Operational Jobs

Daily:

- failed job retry sweep
- stale low-trust memory expiration
- active freshness due scan
- cache invalidation audit
- deletion completeness check
- blob GC sweep (`blob_gc_sweep`, **after** deletion completeness — reaps `MIN_AGE`-aged orphans; `02` §2.3)

Weekly:

- golden eval pack
- sampled benchmark pack
- index bloat check
- slow query report

Release:

- migration dry run
- restore drill on sample backup
- poisoning red-team suite
- whale-promotion drill: promote a synthetic whale tenant to a dedicated cell and assert recall parity + zero cross-tenant leakage during the move (mechanism: `04` §7.0 partitioning, `25` §7b cell topology)
- public scorecard refresh if claim changes

Provider release drill:

- plain Postgres fresh bootstrap
- Neon branch dry run
- Supabase BYOC dry run
- schema contract lint
- advisor/linter pass for exposed schema risks
- restore drill
- deletion completeness replay

### 4.1 What Each Job Catches (failure mode → alarm)

| Job | Failure it catches | Alarm |
|---|---|---|
| deletion completeness check | a `forget` that left a row reachable through one channel (vector/FTS/cache/edge/derived/cold-blob, `06` §6.2) | `deletion_incomplete{path}` — **release-blocking** |
| failed-job retry sweep | a job past the `max_attempts` cap (poison message, dead provider) → **parked** in the terminal DLQ state (§10.1), and a job whose `locked_until` lapsed without an ack (dead worker) → released for redelivery | `job_dlq_depth` over threshold; **a parked job that targets a `captured` episode also raises `episode_stuck_unextracted` — recall is silently degraded until it is replayed or dropped (§10.1)** |
| cache invalidation audit | a cached recall row naming a unit whose `deletion_generation` advanced | `stale_cache_rows` (>0 is a leak risk) |
| index bloat check | HNSW dead-tuple accumulation — pgvector does **not** compact on delete, so forgotten nodes' neighbors stay reachable | `hnsw_dead_ratio` → triggers the scheduled reindex that **is part of the deletion guarantee** (`06` §6.2), not housekeeping |
| slow query report | a recall channel breaching the p95 SLO before customers feel it | `recall_p95_breach{channel}` |
| restore drill | a backup that doesn't restore, or restores with broken FK/edge integrity | `restore_drill_failed` — **release-blocking** |
| blob GC sweep | a content-addressed blob with **zero** live referencing rows older than `MIN_AGE` — crash-orphans (blob PUT, row never committed) + dedup-shared blobs whose last referencing row was forgotten; marks from the Postgres reference set + `blob_ledger`, never `object_store.list()` (`02` §2.3) | `blob_gc_orphans_collected`; `blob_gc_ledger_drift` (ledger↔bucket mismatch from a pre-ledger crash, surfaced by a separate monthly advisory `list()` audit, never the deletion path) |

The deletion-completeness check reconciles on `content_hash` (`02` §2.3): every tombstoned row's blob must be unreferenced or GC-pending; a live blob with no live referencing row is the resurrection hazard. `blob_gc_sweep` runs **after** it so a `forget`'s tombstones are durable before GC reconciles them; the two are one reconciliation from two directions, and a dedup-shared blob is collected only when **zero** live rows reference the hash.

### 4.2 Cross-Store Restore Reconciliation (PITR consistency)

The §4.1 "restore drill" validates *intra-Postgres* restorability + FK/edge integrity. It does **not** make the two stores consistent. A restore inverts the live write ordering (`02` §2.3): PG PITR to **T1** + a bucket at an independent **T2** reintroduces the forbidden state (a live T1 row citing a blob the bucket lacks → invariant #1 across the restore boundary). The `blob_ledger` is itself in Postgres, so it resets to T1 while the bucket sits at its own version — it is **not** trusted as ground truth post-restore.

**Model: Postgres PITR is authoritative; the bucket is reconciled by *presence*, never rolled back.** Content addressing makes a blob version-free (a `sha256` blob is present or absent, never stale), so the only hazards are (a) a live row whose blob is **missing** (release-blocking) and (b) a post-T1 **orphan** (benign — normal GC reaps it). Restore runbook (order is load-bearing):

```text
R0  QUIESCE writers (no retain/forget/GC against the target).
R1  RESTORE Postgres to T1 via PITR (self-consistent relational snapshot, §4.1 validates intra-PG).
R2  SUSPEND blob_gc_sweep (the MIN_AGE proof does NOT span a restore; GC must not run against an unvalidated set).
R3  RECONCILE presence: for each live blob_hash (episode/resource), head(key)/version-list the bucket —
      present (current)            -> OK
      present only as noncurrent   -> restore_blob_undelete (strip delete marker / restore version)
      absent + key-tombstone       -> restore_blob_shredded (correct crypto-erasure, 06 §6.2) -> tombstone the row
      absent + no tombstone        -> restore_blob_missing{hash} -- RELEASE-BLOCKING (retention-floor violation)
    Presence is re-derived from LIVE ROWS vs the actual bucket, never from the restored blob_ledger.
R5  INTEGRITY GATE: zero unexpected restore_blob_missing (the cross-store analog of restore_drill_failed).
R6  RESUME blob_gc_sweep, then ACCEPT WRITES — only now.
```

**Retention coupling (the only way (a) becomes real):** object-store retention ≥ Postgres PITR window. GC's `DELETE(key)` is *soft* on a versioning-enabled bucket (delete-marker over a retained noncurrent version); lifecycle `NoncurrentVersionExpiration ≥ W_pg + margin` is the floor, asserted fail-closed at `db bootstrap-check` (`25` §7a, `restore_retention_floor_violation`). New §4.1 alarms: `restore_blob_missing{hash}` (release-blocking), `restore_blob_shredded` (informational), `restore_retention_floor_violation` (release-blocking, bootstrap). Add a **cross-store restore reconciliation drill** to the Release + Provider-release lists, distinct from the intra-PG restore drill. (2026 note: S3/GCS/Azure now give **strong read-after-write *and* list consistency**, so a stale-bucket-LIST is no longer the hazard — but there is still **no cross-store atomic write**, so a "row present at the restore LSN, blob absent" skew is real and is a **hard quarantine of that row, never a silent serve** — the live invariant-#1 violation seen across the restore boundary.)

## 5. Schema Change Discipline

Before public launch, prefer the simplest correct schema even when that means deleting and recreating local data. For public releases, use:

```text
add nullable column
backfill
add CHECK NOT VALID
validate
switch reads
drop replaced path in a named cleanup change after backup verification
```

## 6. Importer Trust

Imported memory starts lower trust than first-party user/system memory unless signed or verified.

Importer output must preserve:

- original ID
- source product
- import batch
- imported at
- original timestamp
- trust mapping

## 7. Ops SLOs

Initial targets:

- recall p95 under 1.5s without external rerank
- retain p95 under 200ms through raw episode capture
- background extraction eventual completion under 10 minutes for normal load
- **abnormal-load behavior is defined, not emergent** (`02` §3.1): past a bounded queue depth, `reflect` sheds by demand tier and raises a `consolidation_lag` alarm; recall declares `degraded` and falls back to raw-episode/lexical retrieval rather than silently missing
- zero known cross-tenant retrievals
- deletion completeness check passes before release

## 8. Seed Examples

The public repo should include:

```text
examples/
  quickstart/
    episodes.jsonl
    golden.yaml
  coding-agent/
    terminal-session.jsonl
    repo-artifacts/
  customer-support/
    tickets.jsonl
  security/
    poisoned-web-page.md
    tool-output-injection.jsonl
```

Examples must be synthetic or properly licensed. They should demonstrate citations, trust labels, correction, and forget.

### 8.1 What the quickstart corpus must prove in <10 min

`examples/quickstart/` is a *scripted narrative*, not a memory dump — each episode fires one observable behavior, asserted by `golden.yaml` (so the README is executable, a regression breaks the demo):

| Step | Seeded | Recall shows |
|---|---|---|
| citation | 2 plain project facts | answer with `cites` → episode id |
| correction | a 3rd episode revising fact #1 | `supersedes` edge; old `superseded`, new `active` |
| forget | a `forget` on fact #2's subject | recall returns it from **zero** channels |
| poisoning | `security/poisoned-web-page.md` asserting a false preference | enters `belief`/`web_content` (0.3), **labeled, present-but-not-citable**, never fills a high-risk arg |
| corroboration-farming | the same poisoned claim repeated 3× from one origin | still `belief` — independence gate holds (`04` §5) |

The poisoning step is the spine: it shows the claim *present but contained*, not absent — "we stored it and refused to trust it" is the product (`06` §8). The poisoned page is a fixture, never a live URL (the SSRF floor is never exercised against the internet in examples).

## 9. Cookbook Structure

```text
docs/cookbooks/
  use-with-openai-agents.md
  use-with-claude-code.md
  use-with-codex.md
  use-with-langgraph.md
  import-from-jsonl.md
  run-memory-evals.md
  harden-against-poisoning.md
```

Cookbooks use the public API/SDK/MCP only. No hidden Syndai integration calls.

## 10. Backfill and Re-Embedding

Embedding profile changes use compare-then-switch:

1. create new `embedding_profile`
2. enqueue re-embedding
3. write new embedding rows
4. compare recall traces
5. switch read profile by config
6. delete previous embedding rows after comparison gates pass

Never rewrite vectors in place without a profile version.

**Re-embedding runs offline from raw episodes.** Because raw episodes are recoverable ground truth (invariant #1), re-embedding never depends on the old embedding endpoint staying alive — so a provider **sunsetting** an embedding model mid-corpus is survivable: re-derive vectors from the raw episodes under the new profile. The new profile's `index_strategy` is chosen for the new model's dimensions (`02` §2.1a) — a >4,000-dim replacement model forces `hnsw_subvector`/`hnsw_binary`, not `hnsw_full` (`halfvec` HNSW caps at 4,000, not 2,000).

### 10.1 Re-embedding a Large Corpus (batching, cost, resumability)

The 6-step compare-then-switch is the contract; at BEAM-10M scale it needs an execution shape:

- **Batch the embed calls** — `reembed_profile` submits chunks to the provider's **Batch API** (50% discount, ≤50k inputs/batch, 24h window). Re-embedding is offline, so the 24h latency is free and cost halves — the accurate→cost→latency call made correctly (accuracy is fixed; spend latency to cut cost).
- **Resumability is the `job_state` key** — idempotent by `(job_type, target_id, new_profile_id)`; a crash resumes from un-embedded units. Progress = `embedded_count/total` per profile (an SLI).
- **Both profiles live concurrently** — new rows write under the new `embedding_profile_id` (the `(unit, profile)` PK); old vectors stay queryable so recall never goes dark mid-backfill.
- **The cutover gate is step-4 comparison, made blocking** — the read-profile switch is config-gated on explicit recall thresholds (for example recall@k regression below the golden-set limit); a backfill that silently degrades recall is caught **before** the switch. Old vectors delete only post-gate.
- **Second-profile vector cutover, never in-place** — the new vectors live under a *second* `embedding_profile_id` (the `(unit, profile)` PK, `04` §7) while the old profile stays queryable, the new index builds `CONCURRENTLY`, and the read layer switches profiles only after the gate passes. If the gate fails, keep the old read profile; after a successful switch, delete old vectors only after the post-gate holdoff expires. In-place vector replacement is forbidden.
- **Budget the double-index peak** — at cutover the new HNSW builds `CONCURRENTLY` (two table scans, invalid-on-failure → drop+rebuild) while the old still serves, so peak disk ≈ 2× steady index size with `maintenance_work_mem` headroom on top; the BYOC preflight (`25` §11a) must confirm that headroom before enqueueing, or the build silently spills to disk and a ~20-min job runs for hours. Sizing anchors (pgvector 0.7+ parallel build, AWS Aurora figures): ~21 min at 5M×1536, ~28 min at 10M×768, binary ~3× faster, halfvec index ~19 GB at 10M×768.
- **Every vector carries its `embedding_profile_id`** (model + version) — the #1 migration post-mortem lesson is "we didn't tag vectors with the model version, we should have." A vector whose profile is unknown cannot be safely re-embedded or compared, so the tag is mandatory, not optional.
- **Interim bridge (evaluate, don't assume)** — for a very large corpus, a learned cross-space mapping (Drift-Adapter, arXiv:2509.23471 — ~95–99% recall recovery at 1M scale from a small sample of paired old/new vectors, >100× cheaper than a full re-embed; the commercial Schift is the same idea) can stage the full re-embed. It is a bridge, not a replacement — gate adoption on the same explicit recall thresholds, and re-embed eventually.

## 11. Job Lifecycle and DLQ Terminal-State Machine

"DLQ handling" was a **label, not a machine** — `job_dlq_depth` (§4.1) named an alarm but no spec said what a poison message *does* past the attempt cap, so a job could retry forever (burning provider cost) or fail silently and leave its target degraded. Under at-least-once delivery (`02` §3.0) a poison message **will** redeliver; without a terminal state it redelivers *forever*. This section is the missing state machine.

### 11.1 Claim → run → ack, with a bounded retry budget

```text
queued
  -> claimed     -- worker reads the pgmq message; locked_until = now()+vt (02 §6.2); attempt_count += 1
  -> running     -- subject lease taken (reflect: pg_try_advisory_xact_lock, 02 §6.2); stages run with §9.4 checkpoints
  -> succeeded    (terminal)  -- pgmq archive/delete; locked_until cleared
  -> failed       (transient) -- error within budget; pgmq vt lapses -> message redelivers -> back to claimed
  -> parked       (terminal)  -- attempt_count >= max_attempts -> DLQ; NOT redelivered
```

- **The retry budget is real and bounded.** Each redelivery increments `attempt_count`; at `attempt_count >= max_attempts` the job moves to the terminal **`parked`** state and its pgmq message is removed from the live queue (archived to a DLQ table), so it **stops redelivering**. A poison item (a permanently-malformed episode the bisection retry, §5/`02` §5.2, could not isolate; a dead provider) can no longer loop. `max_attempts` is per-`job_type` (a cheap `update_decay` retries more than an expensive `reflect`).
- **A lapsed lease is redelivery, not a new attempt path.** A worker that dies mid-job lets `locked_until`/vt lapse; pgmq makes the message visible again and a survivor re-claims it (`02` §6.2). This is the *normal* at-least-once recovery and is counted in `attempt_count` like any redelivery — there is exactly one retry budget, not two.

### 11.2 Park → alarm → resolve (the episode must not rot silently)

A parked job is **not the end** — it is an *escalation*, because the episode behind it is the real casualty:

- **Park raises an alarm, always.** `parked` → `job_dlq_depth` over threshold (§4.1). A parked job whose target is a `captured` episode also raises **`episode_stuck_unextracted`** — the episode is durable ground truth but has produced **no recallable units**, so recall is *silently degraded* for that subject (the §3.1 silent-accumulation failure's cousin: nothing is wrong-looking, the answer-bearing unit simply does not exist). This must be visible, never a forever-`captured` row quietly degrading recall.
- **Three resolutions, never "leave it parked."**
  1. **Replay** — the operator fixes the cause (provider back up, a `reflect` bug shipped) and re-queues the parked job; `compiler_version` bump invalidates stale §9.4 stage markers so it re-runs clean. The raw episode is the recoverable source (`02` §3.0), so replay re-derives from scratch — no reconstruction.
  2. **Hand off** — a genuinely poison item (un-parseable forever) is routed to a quarantine for human/offline handling; the episode is **kept** (it is ground truth) but flagged so recall labels its subject `evidence_unextracted`, not silently empty.
  3. **Drop the *job*, never the episode** — a job rendered obsolete (its target was forgotten, §10) is closed; the episode's own forget path owns deletion. A job is never dropped in a way that loses the durable episode.
- **The DLQ is reconciled, not a graveyard.** The daily failed-job sweep (§4) reports DLQ depth and age; a parked job older than an SLO is itself an alarm, so "parked and forgotten" is impossible. This closes the §3.0 promise that a stuck-`captured` episode is *recovered by re-running extraction*, by giving the stuck job a named terminal state, an owner, and an exit — instead of an unbounded retry loop or a silent rot.
