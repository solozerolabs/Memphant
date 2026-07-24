# Tri-domain next-evidence reconciliation

Date: 2026-07-24. This sequence was intentionally economical: every technique
was screened on a frozen n=12 set, only one forgetting mixture was broadened,
and no model call was made. This is not an overall SOTA, deployment, production
dogfood, tenant-safety, or real-user-value claim.

## Terminal decisions

| domain | free result | decision | next boundary |
|---|---|---|---|
| packing | render cap 1200 recovered 8/8 scored retrieval cases but regressed deterministic exact abstention from 3/4 to 1/4; naive density rejected; submodular ordering tied cap-only | reject all tested candidates before paid calls; defaults unchanged | research a new technique, then require both free predicates before any reader request |
| forgetting | cross-rerank + rank-one improved n=12 from 6/12 to 9/12 and full aggregate 133 to 182 passes, but regressed 16 prior passes | deterministic semantic deletion selector rejected; exact-ID mutation stays authoritative | frozen 16-proposal, $0.50 non-executing control-plane request |
| coding | SWE-ContextBench packaged adapter rehearsed 24 related/unrelated experiences with receipts/traces and future mark-payload construction; DeepSWE admitted only 3/12 causal pairs | SWE task efficacy blocked; DeepSWE paired memory rejected | license clarification plus separately frozen agent request; DeepSWE unpaired only |

## Packing proof boundary

The adapter uses only public retain, recall, receipt, trace, and outcome-mark
paths. The five frozen arms are no retrieval, current MemPhant, cap 1200,
cap+submodular ordering, and the exact reversed-order negative control. The
official paired reader and judge see the same questions, source data, prompts,
models, and context budget. The official shared-haystack lifecycle exposed a
real adapter defect: six sequential queries reuse one constructed memory
object. The root fix now preserves the hash-bound construction while resetting
only query-local state; a regression test proves sequential query isolation.

The free exact-abstention regression rejects the current packing treatment
before downstream calls. Retrieval alone is insufficient.

## Forgetting proof boundary

All 385 baseline outcomes received operation-boundary triage: 0 observed
exact-mutation acknowledgement failures, 77 adapter semantic-selection
boundaries, 126 intentionally unsupported operations, 49 destructive
ambiguities that should fail closed, 0 benchmark limitations, and 133
already-correct cases. This does not adjudicate target correctness, projection
freshness, lineage, or final recall. Product root cause remains open. Existing
deterministic ForgetContract, Memora, and STALE public API adapters remain the
separate lineage/state and end-answer gates.

Similarity may propose candidates but cannot execute destructive mutation.
Exact current unit IDs, confirmation, drift detection, and the existing public
mutation primitive remain mandatory.

## Coding proof boundary

SWE-ContextBench free rehearsal proves the memory transport and visibility
contract, not validator success. Target patch/tests/outcomes remain hidden;
only the permitted prior outcome is retained. Exact evaluator-image digests are
frozen. Official task execution remains blocked by license and authorization.

DeepSWE's 113 tasks are valuable validator-backed work, but the release has no
relation graph. Same repository was not treated as causality. Only three
earlier-to-target pairs survived manual chronology/subsystem/non-leakage review,
so the paired hypothesis is rejected at the admission gate.

## Artifacts

- Inventory: `docs/build-log/artifacts/next-evidence/benchmark-inventory.json`.
- Authorization: `docs/build-log/artifacts/next-evidence/authorization-request.json`.
- Packing report: `docs/build-log/2026-07-24-packing-technique-screen.md`.
- Forgetting report: `docs/build-log/2026-07-24-forgeteval-next-evidence.md`.
- Coding report: `docs/build-log/2026-07-24-swe-contextbench-next-evidence.md`.

The complete AGENTS verification gate and final specialist review must be
recorded only after the last code change.
