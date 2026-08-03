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

- [x] Verify the stash is contained by current `main` file-by-file and record the result in the consolidation build log.
- [x] Remove `L.txt`, `rw.csv`, and `rwl.txt`; they are dirty-path observations, not inputs.
- [x] Remove transient server/log files while retaining paired arm outputs, liveness, preregistration, and analysis.
- [x] Run focused S1b, MiniLM, evidence-contract, and power checks.
- [x] Commit S1b and MiniLM as separate bisectable evidence commits.

### Task 2: Publish the authoritative trunk

**Files:**
- Modify: `docs/superpowers/specs/memphant/STATUS.md`
- Create: `docs/build-log/2026-08-02-trunk-consolidation.md`

**Interfaces:**
- Consumes: all unique local commits and completed evidence.
- Produces: one pushed `main`, zero unresolved local branches/worktrees/stashes, and a current dashboard.

- [x] Add a compact dashboard to `STATUS.md` for surfaces, memory kinds, storage, pipeline layers, and near-SOTA gates.
- [x] Run the complete verification contract from `AGENTS.md`, using the scratch Postgres wrapper for ignored live tests.
- [x] Commit only named files; preserve unrelated data until its cleanup classification is proven.
- [x] Push `main` with a normal fast-forward push.
- [x] Poll GitHub Actions no more often than every two minutes until the exact `main` run completes.

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

- [x] Clone the official repository into a temporary directory and verify commit, LICENSE file, dataset revision, sample counts, gold construction, and evaluator inputs.
- [x] Reject the instrument if its license is card-only, its gold is derivable from supplied statements, or required independent sample size is unreachable.
- [x] Close adapter, contract-test, round-trip, and adapter-commit work as not applicable after the acquisition/evidence gate rejected the release; proof: `docs/build-log/2026-08-02-perma-stage0.md`.

### Task 4: Run the smallest decisive preference gate

**Outcome:** Not runnable after Stage-0 qualification rejected both PERMA and
HorizonBench. No model call, provider call, benchmark-specific product path, or
spend was authorized. HorizonBench is the preferred reopen target because it
has 360 linked user graphs and non-saturating behavior gold; proof:
`docs/build-log/2026-08-02-horizonbench-stage0.md`.

**Interfaces:**
- Consumes: qualified public preference instrument and packaged Postgres runtime.
- Produces: behavior-level paired delta with accuracy, scope violations, correction errors, cost, and latency.

- [x] Stop at the acquisition gate. Power preregistration, control/candidate
  execution, result registration, and promotion are not applicable until a
  qualified release exists.

### Task 5: Keep LongMemEval-V2 retired

**Outcome:** Superseded before this plan by Phase C commit
`17700a381ba17a725182429250b7bf3f9ad09045`. The 9,405-line harness and run
payloads were deliberately deleted after zero official outputs; v1/v3/v4 are
`ABANDONED_NEVER_RESUME`, and the retained maximum is not recoverable by
resuming. The July 27 v5 handoff remains historical accounting provenance.

- [x] Verify current instrument-register, power, abandonment, and Phase C proof.
- [x] Close the stale recensus/resume plan; do not restore deleted apparatus.
- [x] Require a fresh licensed, fail-closed public instrument before any new
  behavior campaign.

### Task 6: Decide and document near-SOTA status

**Files:**
- Modify: `docs/superpowers/specs/memphant/STATUS.md`
- Create: final build log and compact machine result.

**Interfaces:**
- Consumes: complete behavior evidence or a qualified external-data gate.
- Produces: one narrow public claim or an explicit next blocker.

- [x] Keep near-SOTA false: no complete preference result or comparable official frontier exists.
- [x] Keep global, storage, code, tri-domain, and LongMemEval-V2 SOTA false.
- [x] Record unmeasured surfaces/layers and exact reopen conditions for PERMA,
  HorizonBench, and the retired LongMemEval-V2 lane.
- [x] Run the full repository gate on the consolidated code head and the
  docs/evidence gates on this decision; commit, push, and verify CI on `main`.
