# ForgetEval proposal-control-plane evidence

Date: 2026-07-24. This is scratch-benchmark evidence, not a product-default,
deployment, production, or SOTA claim.

## Decision

Reject the model proposal treatment as a global mutation policy. It is useful
as a non-executing control-plane proposal for one-shot ambiguous mutations, but
it is unsafe for recursive drift. Exact current-unit identity and deterministic
lineage transitions remain authoritative; every destructive proposal must fail
closed until a separate verifier confirms it against current state.

No further model run is justified by this evidence. A proposed hybrid was
discarded before replay because ForgetEval's recursive cases expose one final
mutation over preloaded history rather than a sequence of independently
proposed mutations. Its state oracle was therefore identical to the rejected
proposal arm.

## Economical sequence

| stage | evidence | result | decision |
|---|---|---:|---|
| deterministic n=12 baseline | packaged REST + scratch Postgres | 6/12 | establish floor |
| deterministic selector n=12 | cross-rerank + rank-one | 9/12 | broadened once; later rejected on full-set regressions |
| proposal V1 n=12 | 16 successful calls, 2 rejected pre-inference attempts, $0.0211100 | transition oracle 5/12 | repair stale-clause preservation |
| proposal V2 n=12 | 3 calls, $0.0072050 | one `{}` after 241/256 reasoning tokens | raise output cap only |
| proposal V3 n=12 | 16 calls, $0.0235075 | packaged scratch replay 11/12 | broaden exactly once |
| proposal V3 full | 259 proposals, 258 calls + 1 cache hit, no errors/retries, $0.3632000 | packaged scratch replay 188 pass / 71 fail / 126 N/A | reject globally due drift regression |

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
| proposal selection + model replacement | 188 | 53/53 | 21/21 | 2/61 | 112/125 | 110 / 55 |
| proposal selection + exact new fact | 152 | 53/53 | 21/21 | 2/61 | 76/125 | 74 / 55 |

Purge remains 125 N/A in every arm, plus one drift N/A. The selection-only arm
isolates the failure: replacement prose is not the primary cause of the drift
collapse; target selection is. Aggregate improvement cannot compensate for 55
previously passing cases becoming failures.

## Control-plane proof

The proposal generator is bounded, hash-authorized, resumable, and
non-executing. Its provider attempt journal is append-before-call, fsynced,
hash-chained, and settlement-restorable. The confirmation builder independently
binds proposal and input hashes, validates exact operation/cardinality and the
exact `NEW_FACT` prefix, applies only proposal-hash-bound review overrides,
walks current-body hashes sequentially, and reports every state-oracle failure.
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

## Artifacts

- `docs/build-log/artifacts/next-evidence/forgeteval/proposals-v3.385.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/proposal-attempts-v3.385.jsonl`
- `docs/build-log/artifacts/next-evidence/forgeteval/confirmations-v3.385.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/adversarial-385-transition-safe-confirmed.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/confirmations-selection-only-v3.385.json`
- `docs/build-log/artifacts/next-evidence/forgeteval/adversarial-385-selection-only-confirmed.json`
- `docs/build-log/artifacts/next-evidence/authorization-request.json`

## Final verification

After the last code change, the complete repository gate passed:

- Python: 812 passed / 12 skipped; the skipped database tests were then run by
  the explicit scratch-Postgres leg.
- Public/private spec drift: clean.
- `cargo fmt`, clippy with warnings denied, all-target/all-feature Rust tests,
  and Rust doc tests: green.
- Ephemeral scratch-Postgres store/worker tests: 77 passed.
- Provider lint: clean for plain Postgres, Supabase, and Neon.
- Migration dry-run: three ordered migrations.
- Real server/worker/MCP/Postgres E2E: all checks passed.

Final review found no credential material in the committed proposal or attempt
artifacts. The public worktree and private mirror were clean after their final
commits.
