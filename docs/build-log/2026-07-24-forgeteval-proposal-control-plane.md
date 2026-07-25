# ForgetEval proposal-control-plane evidence

Date: 2026-07-24. This is scratch-benchmark evidence, not a product-default,
deployment, production, or SOTA claim.

## Decision

The earlier global rejection was caused by an incomplete confirmation ledger,
not a demonstrated target-selection regression. The 259-row full proposal run
covered one transition per non-purge case, while the official suite contains
321 supported transitions. Fifty-nine cases require a second transition and
three require a third. Replay failed closed when those 62 confirmations were
missing, which appeared as 55 paired regressions.

Keep the model proposal treatment as a bounded, non-executing first-transition
control plane. Complete later transitions without new inference by selecting
the exact body hash written by the immediately preceding confirmed transition
and applying the official replacement text. This deterministic offline
consolidation reached 12/12 on the frozen drift-heavy screen and 244 pass / 15
fail / 126 N/A on the full suite, with 111 paired gains and zero regressions
against baseline. Exact current-unit identity and fail-closed confirmation
remain authoritative. This is useful mechanism evidence, not SOTA: the official
raw score ties Lethe v1 (244/385), trails Mem0 (263/385), and trails the published
LLM-assisted arms (353 and 359/385).

## Economical sequence

| stage | evidence | result | decision |
|---|---|---:|---|
| deterministic n=12 baseline | packaged REST + scratch Postgres | 6/12 | establish floor |
| deterministic selector n=12 | cross-rerank + rank-one | 9/12 | broadened once; later rejected on full-set regressions |
| proposal V1 n=12 | 16 successful calls, 2 rejected pre-inference attempts, $0.0211100 | transition oracle 5/12 | repair stale-clause preservation |
| proposal V2 n=12 | 3 calls, $0.0072050 | one `{}` after 241/256 reasoning tokens | raise output cap only |
| proposal V3 n=12 | 16 calls, $0.0235075 | packaged scratch replay 11/12 | broaden exactly once |
| proposal V3 full, incomplete ledger | 259 proposals, 258 calls + 1 cache hit, no errors/retries, $0.3632000 | packaged scratch replay 188 pass / 71 fail / 126 N/A | diagnose missing chained transitions |
| deterministic lineage n=12 | exact previous confirmed body hash; no model calls | 12/12 | broaden once |
| deterministic lineage full | 62 chained confirmations; no model calls | 244 pass / 15 fail / 126 N/A | retain as verifier-led evidence; no default/SOTA move |

The sole V3 n=12 miss is an evaluator substring collision: forbidden
`architect` also matches the valid new phrase `architecture firm`. It remains
counted as a miss.

Total paid work in this sequence was 293 provider calls and **$0.4150225**
settled, with zero unsettled liability. The full run stayed far below its $5
ceiling, so no cap increase was needed.

## Full-set adjudication

| arm | aggregate pass | amnesia | decay | drift | supersession | paired gains / regressions vs baseline |
|---|---:|---:|---:|---:|---:|---:|
| current adaptive-gap baseline | 133 | 23/53 | 2/21 | 56/61 | 52/125 | reference |
| cross-rerank + rank-one | 182 | 42/53 | 19/21 | 55/61 | 66/125 | 65 / 16 |
| proposal selection + model replacement, incomplete | 188 | 53/53 | 21/21 | 2/61 | 112/125 | 110 / 55 |
| proposal selection + exact new fact, incomplete | 152 | 53/53 | 21/21 | 2/61 | 76/125 | 74 / 55 |
| proposal + deterministic lineage completion | 244 | 53/53 | 21/21 | 57/61 | 113/125 | 111 / 0 |

Purge remains 125 N/A in every arm, plus one drift N/A. The selection-only arm
did not isolate target-selection quality because it inherited the same missing
transition chain. The completion guard now rejects any proposal/confirmation
set that omits a supported official transition.

All 15 remaining official failures were already baseline failures. Thirteen are
literal-substring benchmark conflicts in which the required final fact contains
the forbidden token (for example `architect` in `architecture`); they remain
counted as official misses. Two negation cases are genuine multi-unit conflicts:
the corrected negation fact and a contradictory current sibling both survive.
The official score is therefore 244/385. A separate semantic adjudication is
257/259 on supported non-purge cases, but it is diagnostic only and does not
replace the official metric.

## Control-plane proof

The proposal generator is bounded, hash-authorized, resumable, and
non-executing. Its provider attempt journal is append-before-call, fsynced,
hash-chained, and settlement-restorable. The confirmation builder independently
binds proposal and input hashes, validates exact operation/cardinality and the
exact `NEW_FACT` prefix, applies only proposal-hash-bound review overrides,
walks current-body hashes sequentially, and reports every state-oracle failure.
It now also rejects incomplete supported transition chains. The lineage
extension accepts only official supersession rows, requires the immediately
prior confirmed replacement, matches exactly one current candidate by SHA-256,
and uses the official new text exactly; no semantic selector or model call is
allowed on this path.
Official replay then maps a confirmed body hash to exactly one current unit ID
and uses the existing public mutation primitive against run-owned ephemeral
scratch Postgres.

Two prompt-label contaminations were corrected by hash-bound review overrides;
they were not silently accepted. The raw proposal output remains
`confirmed=false`.

Key immutable hashes:

- proposals: `c3bc56ec780a86759eb71f66038ab1f8015ad196f1d2170b7bdea5f281d0e5d2`
- paid attempt ledger: `d91b19098da47d09fac29b318e4e87fe6c549afc1462d89ab9cab16a4f8c5bac`
- proposal confirmation ledger: `1ed197821c5756ce3b511f7890ded6f4db1e5ab0f54ebe799f0eb85271e298e0`
- proposal full replay: `3ed4d3fb48323455f1c0a545a0f6092691dbeb2b903520c44de2af4ac8bdf89c`
- selection-only confirmation ledger: `e16ec1c868150e77ed7975663f6c6f42668318344dfb5cb46a8ef4fa317d492f`
- selection-only full replay: `96be3a9bbf522016a5abd37b00bf9d209ce5a35465ee4ea2711b9635c4b7336d`
- decisive lineage n=12 replay: `a6dee62ad72564177f79475417372b40deb5f81c2af8ab7440c295011f0cb771`
- full lineage pass-1 capture: `848a7f2589af9502aaaa20a37f2a7943a5734b914883519996aeeec73d1d7cc8`
- full lineage pass-2 capture: `ba77704bb8e887c3dd310fa5fa16175e2820cefe3b586fdec41871c8da5711cb`
- complete 321-transition ledger: `474932f4840fc2a87fec3a109617a388301412c365b35370886e610cd8f2cbe4`
- final full replay: `bc753f4bc3bbc3ac61a7652374ea5de08a8e0f8371f342b3ef624978d6dc18f4`

## Research boundary

Recent work supports a verifier-led, non-destructive consolidation boundary:

- [TrustMem](https://arxiv.org/abs/2606.25161) centers a transition verifier
  around memory updates.
- [All-Mem](https://arxiv.org/abs/2603.19595) proposes non-destructive memory
  consolidation rather than immediate erasure.
- [SCM](https://arxiv.org/abs/2604.20943) and
  [Language Models Need Sleep](https://arxiv.org/abs/2606.03979) report offline
  or sleep-inspired consolidation, while
  [Infini Memory](https://arxiv.org/abs/2606.10677) targets model-side memory
  capacity.
- Human complementary-learning-systems and active-systems-consolidation
  accounts separate fast episodic capture from slower integration
  ([CLS review](https://pmc.ncbi.nlm.nih.gov/articles/PMC3416886/),
  [sleep consolidation review](https://pmc.ncbi.nlm.nih.gov/articles/PMC3278619/)).

The transferable pattern is preserve raw episodes, consolidate offline, and
verify transitions before changing current state. Weight training, KV-cache
sleep, and model-internal capacity methods do not address MemPhant's exact-ID,
tenant-bound, auditable mutation contract and are not imported here.

This is the sleep-inspired technique that was tested: offline consolidation of
confirmed episodes into deterministic lineage transitions. Model-weight sleep,
KV-cache replay, and training-time consolidation were not needed for this gate.

## Artifacts

- `docs/build-log/artifacts/next-evidence/forgeteval/proposals-v3.385.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/proposal-attempts-v3.385.jsonl`
- `docs/build-log/artifacts/next-evidence/forgeteval/confirmations-v3.385.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/adversarial-385-transition-safe-confirmed.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/confirmations-selection-only-v3.385.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/adversarial-385-selection-only-confirmed.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/drift-n12-lineage-decisive.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/lineage-inputs-385-pass1.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/lineage-inputs-385-pass2.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/lineage-confirmations-385-complete.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/adversarial-385-lineage-complete.json`
- `docs/build-log/artifacts/next-evidence/authorization-request.json`

## Final verification

The lineage helper was developed test-first. Focused confirmation-builder tests
first failed for missing completeness enforcement, predecessor selection, and
multi-pass provenance; all pass after the root fixes. The complete repository
gate is rerun after the final evidence edits and recorded in the final handoff.
