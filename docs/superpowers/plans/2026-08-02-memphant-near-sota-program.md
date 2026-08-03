# MemPhant Near-SOTA Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one authoritative MemPhant trunk and advance the first externally defensible behavior-level benchmark to a near-SOTA decision.

**Architecture:** Keep PostgreSQL as the sole authoritative substrate. Reuse existing retain, correct, recall, receipt, Fast, Deep, evidence-contract, and benchmark-runner boundaries. Add only the minimum adapter or harness code needed by a pinned public instrument.

**Tech Stack:** Rust workspace, PostgreSQL 17, Python standard library/pytest harnesses, existing benchmark manifests, GitHub Actions.

## Global Constraints

- Accuracy/UX > cost > performance/latency > security; do not weaken trust boundaries.
- Pre-production: delete obsolete paths rather than add compatibility shims.
- No second memory engine, storage service, graph backend, or benchmark-specific product mutation path.
- Paid work stays within the previously authorized campaign ceiling and uses `doppler run --project syndai --config dev -- ...` only around the secret-consuming command.
- `STATUS.md` is the single state ledger; checkbox movement requires the named proof artifact.
- Benchmark caches under `~/.cache/memphant-bench/` and `~/.cache/memphant/` are never cleanup targets.

---

### Task 1: Consolidate completed local evidence

**Files:**
- Modify: `benchmarks/manifests/evidence_contract_registry.json`
- Modify: `docs/build-log/artifacts/evidence-contract-retrofit.json`
- Create: `docs/build-log/2026-08-02-s1b-tau-live.md`
- Create: `docs/build-log/artifacts/2026-08-02-s1b-tau-live/`
- Create: `docs/build-log/artifacts/track-r-minilm/`
- Modify/Create: current S1b and MiniLM runner/test files shown by `git status`

**Interfaces:**
- Consumes: banked MemoryCode and S8 inputs with pinned hashes.
- Produces: registered negative artifacts that validate with `check_evidence_contract.py`.

- [ ] Verify the stash is contained by current `main` file-by-file and record the result in the consolidation build log.
- [ ] Remove `L.txt`, `rw.csv`, and `rwl.txt`; they are dirty-path observations, not inputs.
- [ ] Remove transient server/log files while retaining paired arm outputs, liveness, preregistration, and analysis.
- [ ] Run focused S1b, MiniLM, evidence-contract, and power checks.
- [ ] Commit S1b and MiniLM as separate bisectable evidence commits.

### Task 2: Publish the authoritative trunk

**Files:**
- Modify: `docs/superpowers/specs/memphant/STATUS.md`
- Create: `docs/build-log/2026-08-02-trunk-consolidation.md`

**Interfaces:**
- Consumes: all unique local commits and completed evidence.
- Produces: one pushed `main`, zero unresolved local branches/worktrees/stashes, and a current dashboard.

- [ ] Add a compact dashboard to `STATUS.md` for surfaces, memory kinds, storage, pipeline layers, and near-SOTA gates.
- [ ] Run the complete verification contract from `AGENTS.md`, using the scratch Postgres wrapper for ignored live tests.
- [ ] Commit only named files; preserve unrelated data until its cleanup classification is proven.
- [ ] Push `main` with a normal fast-forward push.
- [ ] Poll GitHub Actions no more often than every two minutes until the exact `main` run completes.

### Task 3: Qualify the public preference instrument

**Files:**
- Create: `benchmarks/manifests/perma.lock.json` only if a real license artifact and immutable revision are verified.
- Create: `docs/build-log/2026-08-02-perma-stage0.md`
- Create: `scripts/run_perma.py` only after the acquisition gate passes.
- Test: `tests/test_perma_adapter_contract.py`
- Reuse: `scripts/gate_runtime.py` and the existing public retain/correct/recall boundary.

**Interfaces:**
- Consumes: PERMA events and official evaluator at a pinned revision.
- Produces: an exact no-model retain/correct/recall round trip or a rejection artifact.

- [ ] Clone the official repository into a temporary directory and verify commit, LICENSE file, dataset revision, sample counts, gold construction, and evaluator inputs.
- [ ] Reject the instrument if its license is card-only, its gold is derivable from supplied statements, or required independent sample size is unreachable.
- [ ] Write one failing adapter-contract test covering evolving preference, scoped exception, and negative constraint.
- [ ] Implement the minimum mapping through existing public MemPhant verbs.
- [ ] Run a no-model Stage-0 round trip and archive exact lineage.
- [ ] Commit the adapter and Stage-0 proof separately.

### Task 4: Run the smallest decisive preference gate

**Files:**
- Create: one preregistration under `docs/build-log/artifacts/`.
- Create: one compact result artifact registered in `evidence_contract_registry.json`.
- Modify: `STATUS.md` only if the preregistered predicate passes.

**Interfaces:**
- Consumes: qualified public preference instrument and packaged Postgres runtime.
- Produces: behavior-level paired delta with accuracy, scope violations, correction errors, cost, and latency.

- [ ] Compute power from the actual discordance assumptions and freeze the smallest decisive sample before model calls.
- [ ] Run a no-memory or raw-dialogue control and MemPhant on identical rows and reader/judge pins.
- [ ] Stop immediately when the maximum remaining gain cannot clear the gate.
- [ ] Validate the evidence contract, settle cost, and report every failed/error row.
- [ ] Promote only if end behavior improves without scope, negative-constraint, or correction regressions.

### Task 5: Resume LongMemEval-V2 v5 economically

**Files:**
- Modify: the existing retry/adjudication code only after tracing all callers.
- Test: live-shaped retry and captured-response regression tests.
- Update: `docs/handoff/2026-07-27-state-aware-memory-v5-handoff.md` or its superseding proof.

**Interfaces:**
- Consumes: exact unresolved v5 set and existing local response cache.
- Produces: complete candidate bank, paired evaluation, and official 451-row Fast/Deep matrix.

- [ ] Recensus captured bodies, terminal ledger rows, provider liability, and exact unresolved IDs at zero spend.
- [ ] Write a regression test reproducing the unreachable retry/adjudication path.
- [ ] Fix the shared root cause, then resume only the unresolved set under the remaining ceiling.
- [ ] Run the paired current/candidate reader and scorer before any official frontier claim.
- [ ] Run the complete 451-row Fast/Deep matrix with latency and settled cost.
- [ ] Reverify the official frontier and calculate LAFS or the closest reproducible proxy.

### Task 6: Decide and document near-SOTA status

**Files:**
- Modify: `docs/superpowers/specs/memphant/STATUS.md`
- Create: final build log and compact machine result.

**Interfaces:**
- Consumes: complete preference and/or LongMemEval-V2 result.
- Produces: one narrow public claim or an explicit next blocker.

- [ ] Mark near-SOTA only for positive LAFS or accuracy within 3pp of the reverified best comparable result with materially lower latency/cost.
- [ ] Keep global, storage, and tri-domain SOTA false unless separately proven.
- [ ] Record which surfaces and layers remain unmeasured and the exact reopen condition for every retired idea.
- [ ] Run the full repository gate, commit, push, and verify CI on `main`.
