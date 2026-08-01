# Phase C deletions (2026-07-31)

Execution record for §3 "Phase C — delete" of
`docs/superpowers/plans/2026-07-31-one-plan.md`, on branch `w1-phasec` off
`main@89cc22c4`. Seven individually-revertable commits, one per plan row. Every
"zero readers / zero writers / zero callers" claim was re-verified against this
tree before the deletion; where verification contradicted the plan, nothing was
deleted and the contradiction is recorded below.

## What came out

| Plan row | Commit | Result |
|---|---|---|
| `public-launch-scorecard.json` + siblings | `8de923bd` | 3 JSONs + 2 sole-purpose test files; retraction receipt written |
| `web/` | `ab9dbc05` | 1,463 lines / 9 files / 68 KB; `docs/quickstart.md` written |
| LongMemEval-V2 harness | `17700a38` | ~30,600 LOC + ~57 MB of run payloads |
| MemSyco / STALE / SWE-Explore | `c79e8036` | ~10,200 LOC + 3.8 MB |
| `retention_tier` | `5196c68c` | migration `20260801_008` |
| Dead schema | `9869b2d3` | migration `20260801_009` + 3 `UnitState` variants |
| Dead Rust | `42b8e3ee` | net −206 lines |

Measured against the plan's estimate:

| | Plan estimate | Actual |
|---|---:|---:|
| script LOC | ~35,000 | **22,306** |
| test LOC | ~17,000 | **15,456** (−603 re-added) |
| Rust LOC | ~660 | **220** (−52 re-added; **net −168**) |
| repo bytes | ~65 MB | **61.1 MB** (108.7 → 47.7) |

Total: 504 files, 1,102,458 deletions, 1,022 insertions.

The script shortfall (22.3 k vs 35 k) is the SWE-ContextBench exemption plus
`ingest_public_bench.py`, `run_longmemeval_official_eval.py` and the LME-S chain
being kept. The Rust shortfall is explained in §"Refused" below.

## Refused, with reasons

Three of the plan's Rust claims did not survive verification:

1. **`recall_with_pool` is not callerless.** `lib.rs:6228` (`recall` itself)
   calls it and `memphant-core/tests/recall_trace_golden.rs` calls it twice.
   Kept. Only the thin `recall_with_pool_and_selection` wrapper — one internal
   caller, three defaulted arguments — was collapsed.
2. **`sibling_gather` was already deleted** at `2552d4c1` (2026-07-30). Nothing
   to do; the surviving hits are docs, frozen report artifacts, and the
   deliberate `bench_lme.rs:1900` regression test that a frozen report carrying
   the removed field still parses.
3. **Four of the six reranker benches are kept.**
   `rerank_real_model_latency_matrix` and `rerank_byo_model_latency_matrix` are
   cited by name in `docs/build-log/2026-07-22-reranker-latency-spike.md` as the
   instruments that produced a recorded measurement;
   `rerank_fixed_pool_accuracy` and `rerank_chunked_pool_accuracy` are the only
   local instrument for the reranker choice, which is an open decision. Their
   env var `MEMPHANT_RERANK_BYO_DIR` is also read by production code at
   `memphant-runtime/src/lib.rs:314`. Only the two `MEMPHANT_RERANK_SMOKE`
   micro-benchmarks — that variable is set nowhere in the tree — were deleted.

**SWE-ContextBench was exempted by the task owner** and is entirely untouched: a
dataset audit found its "saturation" was a tranche-selection artifact, not an
instrument defect (the published third-party n=99 Lite run has no-context at
26.26%), and a parallel agent is re-tranching it.

**`InMemoryStore` was not touched.** The plan flags it as "a decision, not a
cleanup" (~4,400 lines) and recommends deletion, but that recommendation is not
owner-approved and it would change what every DB-free test measures. It remains
the outstanding decision from Phase C.

## The provenance rule, applied

Nothing that records a rejection, invalidation or retraction was deleted. Kept
in full: all 13 `INVALIDATION-PROOF.json`, every `PROOF.json`,
`pre-execution-proof.json`, `frozen-run-order.json`, `spend-ledger/`,
`case-leases/`, `case-construction/`, every `*-AUTHORIZATION*.json` and
`PRE-EXECUTION-AMENDMENT*.md`, the three `longmemeval-v2-v{1,3,4}-abandonment.json`
receipts, all 12 `longmemeval_v2*` manifests, and the `memsyco` / `stale` /
`swe_explore` locks with their `instrument_register` and `instrument_power`
entries.

Where a deletion would have removed a record rather than a mechanism, a
replacement record was written instead of removing a registry row:

- `docs/launch/RETRACTED-2026-07-03-fixture-scorecards.md` replaces the three
  fabricated scorecards, naming each file, its id, what it claimed, and the
  three invariants it was actually enforcing. Those invariants now bind against
  `STATUS.md` in `tests/test_launch_evidence_contract.py`, which is where they
  were load-bearing.
- `docs/quickstart.md` replaces `web/`, covering the same operations against a
  real server with field lists read from `openapi/memphant.v1.json`.

`check_evidence_contract.py` reports `evidence_contract_ok contracted=11
pending_retrofit=45` — unchanged — and `instrument_power.py --check` reports
`power_ok`. No registry row was orphaned, so no registry edit was needed.

Three test files were rescued rather than deleted with their hosts:
`tests/test_provider_attempt_ledger_contract.py` (12 tests for the shared
`provider_attempts.py` ledger, imported by seven live scripts) and
`tests/test_evomembench_acquisition_gate.py` (the standing refusal to integrate
an unlicensed benchmark).

## Verification state

- `cargo fmt --check` clean; `cargo clippy --all-targets --all-features` no
  warnings; `cargo build --workspace` clean; `cargo test --workspace` 0 failed.
- `python3 -m pytest -q tests/` — **1 failed, 729 passed, 12 skipped**. The one
  failure is `test_repo_contract.py::test_spec_drift_check_passes_against_linked_syndai_docs`,
  a state-of-the-sibling-Syndai-repo condition and out of scope. The baseline
  before this branch was **2 failed / 1116 passed**; the second failure was
  `test_public_launch_gate.py::test_public_sota_claim_policy_...`, which shelled
  to `npm test` in `web/` and failed with `playwright: command not found`. The
  suite has no non-environmental red test left.
- Both migrations applied for real against an ephemeral migrated database via
  `scripts/with_scratch_db.sh`, and the absences confirmed in
  `information_schema` / `pg_indexes` / `pg_constraint`.
- `cargo test -p memphant-store-postgres --test pg_store_contract -- --ignored`
  against a scratch DB: 49 passed / 3 failed, the same count as the pre-change
  baseline. All three are pre-existing or flaky and each was reproduced with the
  new migrations removed or shown to pass in isolation.

## Debt surfaced, not fixed

`crates/memphant-store-postgres/src/lib.rs:27`'s embedded `MIGRATIONS` const and
`MIGRATION_HEAD` still stop at `20260730_004`, four migrations behind the
directory. This predates this branch and is why
`ping_rejects_bootstrap_only_schema_until_required_revision_is_applied` is
permanently red against a live Postgres. It also means `lint_migrations()` lints
only a stale subset.
