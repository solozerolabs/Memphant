# Tri-domain next-evidence reconciliation

Date: 2026-07-24. This sequence was intentionally economical: every technique
was screened on a frozen n=12 set, only one forgetting mixture was broadened,
and the subsequently authorized proposal campaign is now closed. This is not an overall SOTA, deployment, production
dogfood, tenant-safety, or real-user-value claim.

## Terminal decisions

| domain | free result | decision | next boundary |
|---|---|---|---|
| packing | render cap, submodular ordering, and local cross-rerank all reached 8/8 retrieval but only 1/4 abstention; a decision-aware sufficiency-card screen was then killed after 5/12 calls at 1/2 supported and 2/2 abstention, plus one invalid overlapping selection/rejection | reject all tested candidates; defaults unchanged | no broader packing or reader run; research must produce a materially different small-gate candidate |
| forgetting | model proposal reached 11/12; deterministic lineage consolidation then reached a decisive drift-heavy 12/12 and full 244 pass / 15 fail / 126 N/A, with 111 gains and zero baseline regressions | retain proposals as non-executing first-transition input and exact-hash lineage as verifier-led evidence; exact-ID mutation stays authoritative | two genuine multi-unit conflicts remain; official score ties Lethe and is not SOTA |
| coding | SWE-ContextBench adapter rehearsed 24/24 receipt/trace chains; 12 frozen Codex task calls completed; official grading resolved the first 3/3 no-memory baselines | reject at baseline ceiling: related gain could be at most +1, below required +2; DeepSWE paired memory rejected at 3/12 causal pairs | no broader coding run; materially harder small gate required before another paid request |

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

Recent sufficiency-aware and decision-aware research justified one separately
authorized n=12 control-plane screen. It stopped after five calls because the
8/8 supported predicate was already impossible; the fifth response also
violated the disjoint selection/negative-transfer invariant. Cost was
$0.114075625 settled with zero unsettled. No broader or reader run followed.
Proof: `docs/build-log/2026-07-24-packing-technique-screen.md`.

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

The authorized full proposal campaign is complete: 259 proposals from 258
provider calls plus one cache hit, $0.3632000 settled, zero unsettled. Including
the bounded n=12 prompt screens, the sequence used 293 calls and $0.4150225.
The initial full replay scored 188 pass / 71 fail / 126 N/A because its 259-row
ledger covered only the first transition in each non-purge case, not all 321
supported transitions. The new completeness guard catches this fail-closed.
Deterministic offline lineage completion added the missing 62 transitions by
exactly matching the immediately prior confirmed replacement body. It required
no new model calls and produced 244 pass / 15 fail / 126 N/A, 111 gains and zero
regressions versus baseline. Thirteen residual official misses are evaluator
substring conflicts and two are genuine multi-unit contradictions. Proof and
recent consolidation research:
`docs/build-log/2026-07-24-forgeteval-proposal-control-plane.md`.

## Coding proof boundary

SWE-ContextBench's free rehearsal proves the memory transport and visibility
contract. Target patch/tests/outcomes stayed hidden; only permitted prior
outcomes were retained, and exact evaluator-image digests were frozen. After
explicit owner authorization, all 12 first-tranche Codex calls completed with
zero retries or patch-policy violations. The official evaluator resolved the
first three no-memory baselines with 10/10 aggregate fail-to-pass and 315/315
aggregate pass-to-pass tests. That made the required +2 related-arm gain
impossible on four targets, so grading stopped and the remaining 24 task calls
were not run. This is validator-backed rejection at the baseline ceiling, not
an estimate of memory efficacy.

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
