# ForgetEval next evidence: economical selector screen and control-plane boundary

## Outcome

The free selector screen is complete. The best deterministic mixture was local
fastembed cross-reranking plus rank-one release. It improved the frozen n=12
slice from 6/12 to 9/12, then improved the full 385-case aggregate from 133
passes to 182. It is **rejected for promotion** because the paired full-set
comparison repaired 65 baseline failures while regressing 16 baseline passes.
The improvement is real but not monotone or safe.

No paid or model call was made. The default did not change.

## Minimum experiment sequence

| Arm | n=12 pass | Decision |
|---|---:|---|
| current small embedder + adaptive-gap release | 6 | baseline |
| base embedder | 6 | reject |
| ModernBERT embedder | 6 | reject |
| fastembed cross-rerank | 7 | retain only for mixture screen |
| rank-one release | 8 | retain only for mixture screen |
| cross-rerank + rank-one | 9 | broaden once, then reject |

The single broad winner run produced 182 pass / 77 fail / 126 N/A. The one
instrumented baseline replay reproduced the prior 133 / 126 / 126 aggregate
exactly and was necessary because the historical report had no per-case rows.
No other full arm was run.

## Root-cause classification

The machine-readable report classifies all 385 baseline cases:

| Category | Count | Evidence boundary |
|---|---:|---|
| actual MemPhant defect | 0 | Exact-ID correct/forget receipts acknowledged every selected unit; no lineage or projection failure was observed. |
| adapter mismatch | 77 | 73 supersession plus 4 drift failures require semantic target choice or compound-fact merge planning that is not part of the exact public primitive. |
| intentionally unsupported operation | 126 | 125 purge-family cases plus the purge-based `adv_substring_trap_15`; MemPhant has subject erasure and exact-unit forget, not selective natural-language hard purge. |
| ambiguous/destructive request that should fail closed | 49 | 30 amnesia and 19 decay failures ask a natural-language selector to choose one or more destructive targets. |
| benchmark limitation | 0 | No failure needed this category after inspecting the official cases and recorded operations. |
| already-correct behavior | 133 | Baseline pass. |

This is a benchmark-adapter classification, not a general proof that MemPhant
has no forgetting defects. The full exact-state/lineage contract remains a
separate product gate.

## Why threshold tuning stopped

The full comparison had 65 fail-to-pass, 16 pass-to-fail, 61 fail-to-fail, 117
pass-to-pass, and 126 N/A-to-N/A transitions. A small retrieval winner therefore
did not establish a safe mutation policy. More embedding and score-threshold
arms would repeat the same architectural error: similarity would still execute
a destructive choice.

## Recent 2026 research decision

The research pass supports an evidence-gated control plane:

- [Useful Memories Become Faulty When Continuously Updated by LLMs](https://arxiv.org/abs/2605.12978)
  finds that repeated model consolidation can corrupt useful memory and argues
  for preserving raw episodes and explicitly gating consolidation.
- [MemMachine](https://arxiv.org/abs/2604.04853) reports strong results from
  preserving whole episodic ground truth and improving retrieval around it,
  rather than making lossy extraction the authority.
- [Beyond Similarity / MemGate](https://arxiv.org/abs/2606.06054) treats memory
  search as a trust boundary and replaces raw similarity admission with a
  query-conditioned gate. The paper did not expose a directly reusable code
  link, so no repository was cloned.
- [Deployment-Time Memorization](https://arxiv.org/abs/2606.10062) reports that
  raw-only deletion can leave derived copies and that full-pipeline purge or
  tombstone redaction is needed for zero worst-tier residue.
- A recent practitioner report describes the same lower-confidence principle:
  [similarity proposes; consequence disposes](https://www.reddit.com/r/LLMDevs/comments/1urmje9/i_stopped_trusting_llminferred_memory_after_it/).

The resulting design is narrow: a model may propose candidate body hashes and,
for compound supersession, replacement text. It cannot execute. A separate
confirmation ledger must approve the exact proposal. Replay then maps each
confirmed body hash to exactly one currently retrieved unit and calls the same
public exact-ID `/v1/correct` or `/v1/forget` primitive. Missing, duplicated,
drifted, or unconfirmed selections fail closed.

## Frozen n=12 proposal gate

The frozen 12 cases contain 16 mutation steps. Proposal inputs include the raw
public benchmark candidate text, stable body hashes, case/mutation identity,
and a hash of the complete prompt input. The next treatment must pass at least
10/12, regress zero baseline passes, have zero unacknowledged exact mutations,
and execute zero unconfirmed mutations before any broader request.

The paid packet is staged at
`docs/build-log/artifacts/next-evidence/forgeteval/proposal-authorization-request.json`.
It authorizes at most 16 logical proposal calls, 32 provider attempts, 256
output tokens per attempt, and $0.50. Its current status is
`AWAITING_EXPLICIT_PAID_AUTHORIZATION`, so the command fails before reading the
provider credential. Even if proposal generation is later authorized, mutation
execution still requires separate confirmation and authorization.

## Proof artifacts

- `benchmarks/manifests/forgeteval.next-evidence.n12.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/proposal-inputs.n12.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/adversarial-385-baseline-instrumented.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/adversarial-385-cross-rerank-rank-one.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/root-cause-and-transitions.json`
- `scripts/run_forgeteval.py`
- `scripts/analyze_forgeteval.py`
- `scripts/generate_forgeteval_proposals.py`

Focused verification: 64 tests passed. Paid calls: 0. Settled cost: $0.
