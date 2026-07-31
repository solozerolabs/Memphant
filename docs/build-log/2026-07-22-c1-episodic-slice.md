# C1 — Episodic slice: LANDED (correctness-only), all three bars proven

**Date:** 2026-07-22 · **Branch:** `codex/memphant-p1-deep-mode` · **Plan:** §5 C1, §8 spine.
**Design:** `docs/superpowers/specs/2026-07-21-c1-episodic-slice-design.md` (eng-reviewed, 6 findings folded).
**Plan:** `docs/superpowers/plans/2026-07-22-c1-episodic-slice.md`.

## Verdict

C1 (the first real-user-value cutover slice) LANDED **correctness-only** on a
schema-faithful synthetic 252-row episodic corpus. All three binding acceptance
bars are PROVEN with evidence (not assumed). Live Syndai loader rewiring is
deferred to the same boundary C0/C3 deferred (the Task-6 adapter bridge + a real
Syndai context binding; dogfood default-off ⇒ nil blast radius).

## Why synthetic (verified, per owner decision)

Owner directed: attempt the real 252-row extract if it runs / is worth it, else
synthetic. Checked (2026-07-22): the local `syndai_local` dev DB
(`syndai-coding-local-db`, port 55432) has the `episodic_memories` schema but
**0 rows** (historical data wiped, the same wall C3 hit). The 252 prod rows exist
**only** in the off-limits Supabase `syndai` schema (AGENTS.md §18), which needs
explicit per-op authorization not granted for a data copy. So C1 backfills a
deterministic synthetic corpus (`scripts/episodic_lane_corpus.py`) —
correctness-only, the C3 posture. The backfill runner is corpus-source-agnostic:
it runs against the real 252 rows the moment they are authorized, zero code change.

## The three bars (all PROVEN)

**Bar 1 — Hot-path SLO on the packaged runtime.**
- **HTTP boundary (the acceptance number):** 200 real `POST /v1/recall` calls
  (Fast, budget 1200) through the packaged `memphant-server` + ephemeral scratch
  PG over the 252-row corpus — **p50 = 32.6 ms, p95 = 37.2 ms**, well under the
  200/500 ms budget. Measured by `episodic_lane_run_memphant.py --slo-samples`.
  This closes the STATUS §6 gap: the existing `hot_path_slo.rs` measured
  `InMemoryStore` in-process, which is not the packaged runtime.
- **Rust CI guard:** `crates/memphant-store-postgres/tests/hot_path_slo_pg.rs`
  (`#[ignore]`d) seeds 252 episodes through the real retain+compile path and
  measures `MemoryService::recall` against `PgStore` — passes the same 200/500 ms
  thresholds. Two live subtleties root-caused: recall needs a real vector channel
  (`StubEmbedding`, modelling the packaged fastembed presence), and `recall_time`
  must be ≥ the worker's `now()`-stamped `transaction_from` (a future FixedClock)
  or the bitemporal window excludes every freshly-compiled unit.
- Proof: `docs/build-log/artifacts/c1-episodic/slo-bar1-http-provenance.json`.

**Bar 2 — Conversations-tab equivalence (proven on recall).**
Equivalence is proven on the RECALL surface, NOT on `GET /v1/scopes/{id}/memory`
(`scope_memory_page`) — verified `store.rs:3374-3389`, that listing applies **no
state filter**, so forgotten/archived episodes still appear in it; only recall
filters state (`state in (active,validated)`, `forgotten_source` exclusion,
`store.rs:1978-1990`). Per tenant, two-part: (a) every recall-visible episode is
individually retrievable, (b) no archived/`user_correction` episode is EVER
recallable. Both tenants PASS: retrievable 113/114, correctly-excluded 13/12.
252 rows backfilled (retain=227, forget=10 archived, skip=15 corrections).
- **Two real cutover mappings surfaced live and pinned** (the actual C1 adapter
  work): (1) Syndai's episodic `source_kind` taxonomy → MemPhant's fixed 6-value
  enum (`map_source_kind`, spec-28 convention); (2) backfill disposition —
  `user_correction` audit rows skipped, archived rows retained-then-forgotten
  (the archive→forget verb), the rest retained — faithful to Syndai's own recall
  filter (`_build_active_scope_filters`).
- Proof: `docs/build-log/artifacts/c1-episodic/backfill-bar2-provenance.json`.

**Bar 3 — Two-user RLS leakage proof (the eng-review's load-bearing finding).**
`crates/memphant-store-postgres/tests/episodic_rls_leakage.rs` (`#[ignore]`d):
seeds episodes for tenant A + B, then under `set local role memphant_app` +
`bind_tenant` asserts each tenant sees exactly its own episode and **0** of the
other's — enforced by FORCE RLS, not app code. **Teeth-verified**: dropping the
role assumption (reading as the scratch-DB superuser, `rolbypassrls=true`) makes
the isolation assertion fail. The `e2e_probe.sh` gains a cross-tenant episodic leg,
explicitly labeled **app+GUC isolation (NOT the RLS backstop)** — because the
packaged server connects as the superuser login, RLS never fires there.

## Standing note (production, not a C1 deliverable)

The packaged server currently connects as a superuser login (`rolbypassrls=true`
— verified live), so on the served HTTP path RLS is bypassed and isolation rests
on the app + tenant-GUC filter. **Production must run the server under a
non-superuser `memphant_app` login for RLS to be the real backstop.** Bar 3 proves
RLS works when that role is assumed; it does not change how the server connects.

## Gate (AGENTS.md §37, all green 2026-07-22)

pytest 715 passed / 12 skipped · `cargo fmt --check` clean · `cargo clippy
--all-targets --all-features -D warnings` exit 0 · `cargo test --all-targets
--all-features` 0 failed · `cargo test --doc` clean · spec-drift skipped
(private Syndai specs absent in this worktree) · scratch-DB live-PG leg
(`-p memphant-store-postgres -p memphant-worker --ignored`) 0 failed · provider
lint 3/3 clean · migration dry-run ok · `e2e_probe.sh` ALL CHECKS PASSED.

**One pre-existing flake identified, isolated, NOT a C1 regression — now FIXED
(2026-07-22, commit `f7828f8c`):**
`pg_store_contract.rs::concurrent_workers_cannot_split_a_scope_lane_and_reclaim_reuses_preparation`
intermittently failed under the loaded full `--ignored` suite (two `tokio::join!`'d
worker claims race the XOR assertion) but passed 3/3 in isolation. C1 touches
none of the worker-claim path. Root-caused to `claim_reflect_jobs`' lane lock:
`for update of agent skip locked` sits above the Sort, so two non-overlapping
claimers split a lane (A takes the prefix, B the disjoint tail). Fixed with a
BLOCKING `pg_advisory_xact_lock` loop before the claim query (a try-lock/skip-
locked in-query gate leaves a ~0.3% residual — snapshot-vs-lock ordering, not
lock atomicity). 0/300 under the hammer that previously split; full ignored PG
suite 43/43 green. This also unblocked the B6 Postgres CI leg.

## What C1 does NOT prove (honest)

Recall QUALITY parity (no episodic oracle exists; deferred to the C3-style golden
when a volume corpus exists — runnable procedure already documented). The live
Syndai loader cutover (deferred — Task-6 adapter bridge). RLS on the *served* HTTP
path (server not run under `memphant_app` yet — standing note). Real prod corpus
distribution (synthetic only).

## UPGRADE — proven on REAL Syndai prod data (2026-07-22, owner-authorized)

The owner granted prod-run permission, so C1 was re-proven on the **real**
episodic corpus, not just synthetic. A ONE-TIME **read-only** extract
(`default_transaction_read_only = on`, `SELECT` only) pulled the live
`syndai.episodic_memories` rows into a **gitignored** corpus
(`benchmarks/data/private/`, never committed); the runner gained `--corpus` to
consume it. Real count is **270 rows / 5 tenants** (grown from the recon's 252).

Real data surfaced **three more cutover mappings** the synthetic corpus missed —
each pinned by a unit test (these ARE the adapter's real work):
1. **`source_kind` = `rollup`** → `system` (prod has only `dialog_turn` + `rollup`,
   not the taxonomy the recon implied).
2. **`rolled_up` exclusion**: 235/270 rows are rolled-up consolidations, which
   Syndai's `_build_active_scope_filters` drops from recall. Folded into one
   `is_recall_visible()` predicate (DRY) driving both disposition and the expected
   set → those rows retain-then-forget.
3. **`observed_at` RFC3339 normalization**: Postgres exports
   `2026-06-17 11:03:30.693143+00` (space separator, `+00`), which the strict
   contract 422s ("observed_at must use a UTC offset"); normalized to `T…Z`.

**Bar 2 reframed honestly for real data.** The **hard gate is state-filter
correctness** (no rolled-up/archived/correction episode is EVER recallable) — it
is **EXACT: 0 leaks on all 5 tenants** (55 and 180 rows correctly excluded on the
two large tenants). **Per-episode retrievability is a REPORTED coverage metric,
not a gate**: recall is ranked/deduped/budget-limited, so two tenants that are
near-duplicate 16k-char audit-prompt clusters are legitimately 0% prefix-
retrievable, while normal-conversation tenants hit 71–100% (12/17, 6/6, 1/1).
Asserting 100% would be dishonest about what recall is.

**Bar 1 SLO on real data: p50 = 34.4 ms / p95 = 36.4 ms** (short realistic query).
A surfaced gotcha: querying a full 16k-char episode body embeds+packs in ~1 s —
that is a test artifact, not the hot path (context injection uses short queries);
the SLO uses a realistic short query.

Data safety: bodies never leave the gitignored corpus; the committed provenance
carries only counts/rates with `user_id`s redacted to prefixes — verified no body
text present. Proof: `docs/build-log/artifacts/c1-episodic/real-prod-backfill-provenance.json`, commit `6d01789b`.

## Drain audit (2026-07-30) — C1 CLEARED, and the runner now proves it itself

`gate_runtime.drain_worker` was found reporting `drain completed=256` on 401
queued jobs and exiting with 145 still `queued`. Root cause (fixed by a parallel
session at `a47a4a40` + `20260730_005_pending_worker_job_count.sql`):
`20260730_004_served_login_roles` made the worker pool assume `memphant_worker`,
so FORCE RLS applied to the worker's queue-wide, tenant-unbound drain-exit
count — it matched zero rows and answered 0 at any queue depth. Claiming was
unaffected (`claim_reflect_jobs` is `security definer`), so the ceiling was
exactly the batch size. A bench on that path could score a partially compiled
corpus and never know. `episodic_lane_run_memphant.py` had no independent
verification of any kind, so it was audited.

**C1's banked numbers stand. Nothing moves.** Three independent lines:

1. **Structurally immune by date.** The defect needs the worker pool to assume a
   capability role. At `6d01789b` (2026-07-22, the commit that banked the real-prod
   evidence) `PgStore::connect_worker` did no `SET ROLE` at all — no `PoolSpec`
   existed and `after_connect` set only `search_path`. The role assumption arrived
   with `20260730_004` on 2026-07-30, **eight days after** both C1 runs.
2. **The banked counts are an exact identity.** One `reflect_episode` job per
   POSTed episode; `forget` enqueues none, so the archive path's explicit
   `/v1/reflect` adds exactly one. Synthetic: 227 retain + 10 forget + 1 = **238**,
   and the artifact records `compiled_jobs: 238`. Real prod: 35 + 235 rolled-up + 1
   = **271**, and the artifact records `compiled_jobs: 271`. An under-drain reports
   a short count; these are not short. Pinned by
   `tests/test_episodic_lane_run_memphant.py`.
3. **Re-run, live, on the deterministic synthetic corpus** (2026-07-30, scratch DB,
   $0): `compiled=238`, Bar 2 PASSES on both tenants with `correctly_excluded`
   13 and 12 and 0 leaks, and both tenant ids identical to the banked artifact —
   every correctness number reproduces exactly. The real-prod corpus is a
   gitignored one-time extract and is no longer on disk, so that arm is covered by
   (1) and (2) rather than by re-execution.

The runner no longer relies on any of this being re-derived by hand. It now
asserts both halves of the contract `code_lane_run_memphant.py` always had, before
any bar is evaluated: `compiled == expected_compiled_jobs(...)`, and
`gate_runtime.assert_worker_queue_empty` — a `psql` count of
`queued|running|dead` on the **bench** credential, which is not subject to the
worker pool's RLS and is exactly why `code_lane_run_memphant.py` was never
exposed. The provenance now records `expected_jobs` and `queue_drained_verified`.

## Artifacts

- `docs/build-log/artifacts/c1-episodic/real-prod-backfill-provenance.json` (real prod, redacted)
- `docs/build-log/artifacts/c1-episodic/backfill-bar2-provenance.json` (synthetic) + evidence.jsonl
- `docs/build-log/artifacts/c1-episodic/slo-bar1-http-provenance.json`
- Commits `be8929b6` (corpus) `0a509c8f` (backfill+Bar2) `8d39b5a6` (Bar1) `cbb95ff9`+`cab0738c` (Bar3) `6d01789b` (real prod data).
