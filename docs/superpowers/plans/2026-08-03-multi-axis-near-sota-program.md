# Multi-Axis Near-SOTA Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan in order. Create a
> benchmark-specific implementation plan only after its free qualification
> gate passes; do not scaffold speculative adapters.

**Goal:** Earn the scoped statement “Near the 2026 public frontier across five
independently measured memory axes” without allowing one strong benchmark to
hide a weak product surface.

**Architecture:** Keep PostgreSQL 17 and the public `MemoryService` as the one
authoritative substrate. Treat exact keys, scope, supersession, bitemporal
lineage, deletion, provenance, and tenancy as correctness floors. Measure five
user-facing outcome axes with public benchmarks and measure latency, tokens,
cost, stored bytes, and failure rate as a required UX rail on every axis.

**Tech stack:** Existing Rust workspace and packaged Postgres runtime; Python
standard library plus existing pytest/evidence helpers; pinned upstream public
datasets and official scorers; fixed readers/controllers per comparison.

## Claim Contract

There is no universal memory-SOTA number. The only broad claim allowed by this
program is the exact five-axis statement above, and only when all of these are
true:

1. Revisions, splits, model snapshots, prompts, readers/controllers, strata,
   budgets, stop rules, and current published comparators were frozen before
   scoring.
2. On every axis, the user-clustered one-sided 95% lower confidence bound is no
   worse than five absolute percentage points below the best comparable public
   point estimate.
3. All five axes pass. Scores are never averaged across axes.
4. At least two axes equal or exceed the comparable published point estimate,
   or establish a strictly better accuracy-latency-cost Pareto point.
5. No critical stratum regresses against its fixed control: evolved-state
   errors, obsolete-memory use, misleading answers, premise-awareness failure,
   unsupported answers, or code mislocalization.
6. Each row reports ingestion cost, stored bytes, recall and end-to-end p50/p95,
   prompt tokens, paid cost, retries, and unsettled liability. A Pareto claim is
   permitted only when no published comparator is both more accurate and
   cheaper or faster on comparable measurements.
7. Whole-stack comparisons use the published reader/controller configuration.
   A fixed-reader comparison with a different outer model supports only a
   substrate-gain claim.

“SOTA,” “near-SOTA,” and “frontier” remain false for an axis until its complete
official protocol and evidence contract pass. Storage is a supporting rail,
not an accuracy axis.

## Required Portfolio

| Axis | Primary public instrument | Current external reference | Current MemPhant status |
|---|---|---|---|
| Evolving preference and belief state | HorizonBench | 52.8% overall | **CLOSED negative.** Fix-on interim (2026-08-06, fresh 60u/120i, $107.06): Fast 25.8% vs full 35.0%, delta -9.17pp, CI [-18.33, 0]; non-inferiority, evolved-lift, and distractor non-regression all fail. The subject-resolution fix moved the point estimate (-15.8->-9.17) but did not rescue Fast; interim look accepted, n_max declined. Prior v7: -15.8pp, CI [-24.2, -7.5] (ran with the fix off). |
| Longitudinal remembering, reasoning, recommendation, and obsolete-state avoidance | Memora / FAMA | Task- and horizon-specific frontier; no valid universal aggregate winner | Mechanism signal exists, but the raw 43/71 versus 44/71 result is not frontier evidence |
| Heterogeneous dialogue, documents, and email | RHELM | 38.1 overall with external sources; 33.6 best evaluated memory framework | No pinned adapter or official run; current private docs gate is negative |
| Procedural/environment experience | LongMemEval-V2 | AgentRunbook-C 72.5 average; strongest reported RAG point 58.6/57.0 | Prior local campaign is retired and must never be resumed; any return begins with a new free qualification and clean adapter plan |
| Code-repository experience | RepoMem on SWE-bench Verified/Live | 76.5/66.2 Acc@5; 40.4% Verified resolution | Current internal MemPhant path loses to agentic grep; discovery/routing is the binding product gap |

ForgetEval retention/purge behavior, exact-ID deletion, supersession/as-of
correctness, tenant isolation, provenance, and packaged Postgres liveness are
mandatory cross-cutting floors. They do not add bonus points to the five-axis
claim and cannot compensate for a failed lane.

## Benchmark Hygiene

- Prefer reader checkpoints predating each public release. The existing
  November 2025 Opus snapshot is retained for HorizonBench.
- Quarantine gold answers, provenance graphs, answer trajectories, and scorer
  internals from retain, recall, prompts, tuning, and routing.
- A scored sample is burned. Do not tune on it or relabel a rerun as held out.
- For RepoMem, prefer SWE-bench-Live issues after the model cutoff and truncate
  repository history before each issue timestamp. Report Verified separately
  because training contamination is plausible.
- Report synthetic-persona and LLM-judge limitations for HorizonBench, Memora,
  and RHELM. A high score is not evidence about real-user distribution shift.
- Dataset bodies and paid response caches remain outside git under the existing
  protected cache roots. Commit only locks, compact census, authorization,
  closure, analysis, and evidence-contract artifacts.

## Ordered Program

### Task 1: Confirm evolving preferences without buying the full benchmark

**Files:**

- Modify: `scripts/run_horizonbench.py`
- Modify: `tests/test_horizonbench_contract.py`
- Create: `benchmarks/manifests/horizonbench.benchmark.v1.json`
- Create: `docs/build-log/artifacts/horizonbench-confirmation/*.json`
- Modify: `benchmarks/manifests/evidence_contract_registry.json`
- Modify: `docs/build-log/artifacts/evidence-contract-retrofit.json`
- Modify: `docs/superpowers/specs/memphant/STATUS.md`

- [x] Census the complete pinned 4,245-row release for free, validate its six
  source objects, row/user/generator counts, schema, options, gold shape, and
  one conversation-byte identity per user.
- [x] Reconcile the 346 benchmark-contributing users with the 360-user graph
  population using only `user_id` and `generator`; never acquire graph gold.
- [x] Exclude all ten exposed sample users and the two timeline-identity
  collisions found by the census. Deterministically select 20 users
  per generator with one evolved and one static item each, for 60 users and
  120 frozen items. Selected histories must form monotone per-question prefixes;
  retain them incrementally so an earlier question cannot see future turns.
- [x] Ingest monotone timeline prefixes per user, issue two gold-blind Fast
  recalls per user, and require complete non-degraded evidence before paid work.
- [x] Commit a hash-bound authorization for exactly two arms—full context and
  Fast—240 logical reader calls, at most 480 provider attempts, no Deep,
  uncached independent prompts, Opus 4.6's 1M context for both arms, and a $140
  fail-safe ceiling. Preserve every earlier failed-closed authorization.
- [x] Run the paid command only through
  `doppler run --project syndai --config dev -- ...`; stop on model/provider/
  price drift, incomplete pricing, unsettled liability, or the spend cap.
- [x] Require complete paired rows, overall delta >= 0, evolved delta > 0, no
  increase in evolved distractor choices, and at least six discordant pairs.
  The complete bank failed three outcome predicates and stopped here. The
  4,245-item reader run is not authorized.

Focused check:

```sh
python3 -m pytest tests/test_horizonbench_contract.py -q
python3 scripts/check_evidence_contract.py
python3 scripts/instrument_power.py --check
```

### Task 2: Freeze the breadth sequence before building more adapters

**Files:**

- Modify: `docs/superpowers/specs/memphant/STATUS.md`
- Create after each free gate:
  `docs/superpowers/plans/YYYY-MM-DD-<instrument>-qualification.md`
- Create after each passing gate:
  `benchmarks/manifests/<instrument>.lock.json`

- [x] Record the five-axis matrix and claim predicate in `STATUS.md`; keep the
  existing dashboard as the single tracker.
- [x] Qualify instruments in this order: Memora/FAMA replay integrity, RHELM,
  fresh LongMemEval-V2, then RepoMem. Qualification is download/hash/schema/
  scorer/contamination/power/cost work only and spends $0.
- [x] Prefer reuse: existing Memora and evidence helpers first; no generic
  benchmark framework, second store, or new product abstraction.
- [x] Reject or defer any instrument whose official split, scorer, immutable
  source, independent sample size, or cost ceiling cannot be verified.

### Task 3: Repair the largest product gaps on exposed development evidence

- [x] Temporal: the subject-identity fix is built, paid-measured, and the axis
  is **closed negative** — no further spend targets it. The diagnosed first
  bottleneck was current-state compilation: supersession keys on lexical phrase
  identity (`{scope}:{family}:{subject_phrase}`), so a restated preference never
  collided with the belief it replaced
  (`docs/build-log/2026-08-05-horizon-stage1-supersession-defect.md`). Semantic
  subject identity now ships behind `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD`
  (default off, calibrated 0.85). Re-measured with the fix on across a fresh
  60u/120i tranche (2026-08-06, $107.06 settled): supersession fires (398 on
  fresh users) and the point estimate moved -15.8->-9.17pp, but Fast stays
  clearly inferior (delta -9.17pp, CI [-18.33, 0]; non-inferiority, evolved
  lift, and distractor non-regression all fail). The O'Brien-Fleming interim
  gave no efficacy stop; the owner accepted the look and declined the powered
  n_max. The mechanism ships default-off; do not reopen this lane without a new
  mechanism (`docs/build-log/2026-08-06-horizonbench-interim-fix-on.md`).
- [ ] Docs: use RHELM development data to diagnose retrieval versus answer
  composition. Do not revive the rejected full-pool reranker.
- [ ] Procedural: build a clean minimal adapter only if the new LongMemEval-V2
  free qualification passes. Never reuse the retired v1-v5 authorization or
  paid bodies.
- [ ] Code: keep `rg`/`grep` authoritative. Improve discovery/routing on exposed
  RepoMem development tasks before any paid task-resolution run; the reader is
  already 106/106 when gold is retrieved.
- [ ] Promote a shared mechanism only when every already-green lane is
  non-inferior. Lane-specific read policy is allowed; a second truth store is
  not.

### Task 4: Buy only passing sealed confirmations

- [ ] For each qualified lane, write and commit a separate powered plan and
  authorization packet with a pilot kill gate before its official run.
- [ ] Order paid confirmations by expected information per dollar: Memora,
  RHELM, procedural, then code. Code spend remains blocked until free/exposed
  retrieval beats the deterministic search control.
- [ ] After each run, settle accounting, register the evidence contract, update
  only that row in `STATUS.md`, and stop if its predicate fails.

### Task 5: Reverify the public frontier and publish the scoped claim

- [ ] Re-search primary sources and rerun comparable public baselines at the
  frozen final tree; leaderboard values captured earlier are not permanent.
- [ ] Run the full repository verification contract and exact production binary
  probes on scratch Postgres.
- [ ] Claim only passing axes. Publish the five-axis statement only if every
  claim-contract condition passes; otherwise publish the matrix with explicit
  gaps and the narrower passing claims.

## Explicit Deferrals

BEAM, MemoryAgentBench, and MemoryArena are second-wave independent replications,
not first-wave blockers. LoCoMo and LongMemEval-V1 are historical comparators,
not primary 2026 evidence. No full HorizonBench treatment, Deep routing, second
storage plane, hosted vector service, graph service, or learned reranker is
authorized by this plan.

## Primary Sources

- HorizonBench: <https://arxiv.org/abs/2604.17283>
- Memora / FAMA: <https://arxiv.org/abs/2604.20006>
- RHELM: <https://arxiv.org/abs/2605.31086>
- LongMemEval-V2: <https://arxiv.org/abs/2605.12493>
- RepoMem: <https://openreview.net/forum?id=8yjWLJy2eX>
