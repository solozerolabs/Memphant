# State-aware resource compiler and bounded LME-V2 proof design (2026-07-25)

**Status:** proposed; no implementation or paid execution is authorized by this
document. **Branch:** `codex/memphant-state-memory-sota`. **Budget:** USD 200
cumulative campaign liability, not per run.

## 1. Decision

Implement one source-typed structured-observation compiler on the existing
resource reflection path. Reuse the existing contextual chunks, canonical
mutation fold, bitemporal units, lineage, citations, receipts, and Deep
workspace. Do not add a store, query-time extractor, benchmark-specific memory
service, or second spend ledger.

If the no-model liability census proves that it fits, run one fixed, paired
LongMemEval-V2 Medium census (all 451 questions). The
first 12 frozen cases are an output-sealed operational prefix: they may stop the
campaign for contract, oracle, settlement, or cost failure, but nobody may read
answers or scores before the remaining 439 are irrevocably started or the lane
is rejected. They are reused in the final census, so the pilot makes no duplicate
model calls and cannot select the method on efficacy.

This is the only route considered here that could both fit the user's single
$200 ceiling and support an official full-benchmark claim. Its feasibility is
not assumed. A sampled N=300 comparison would be statistically useful but
cannot establish the official full-benchmark result, so it is not the primary
route.

## 2. Why this boundary

The current adapter retains each trajectory fragment as a Resource and queues
`ReflectResource`. The structured compiler accepts only `ReflectEpisode`, and
its evidence validator accepts only episode user-turn spans. Relabeling a tool
trajectory as a user episode would corrupt provenance. Sending whole resources
also cannot work: 5,931 of 7,934 uses in the frozen 12-case slice exceed the
128-KiB request limit.

LongMemEval-V2 explicitly gives memory backends ordered full trajectories and
expects compact evidence for a fixed reader. Its abilities include static and
dynamic state, workflows, gotchas, and premise awareness. That is a typed
observation-compilation workload, not conversational fact extraction.

Rejected alternatives:

- Query-time extraction is cheaper initially but makes canonical state depend
  on the test question and creates an oracle-leakage surface.
- Model-authored mutations per chunk use stale active-state targets across
  batches and make crash recovery ambiguous.
- Deterministic benchmark-field heuristics are cheaper but benchmark-specific
  and do not provide a durable Resource compiler.

## 3. Source-typed extraction contract

Generalize `StructuredStateRequest` to carry:

```text
local_source = Episode(episode_id) | Resource(resource_id)
evidence_slices[] = {
  neutral_slice_id, evidence_kind, body, canonical_start, canonical_end
}
```

Episode construction remains unchanged semantically: only existing accepted
user/user-agent ranges become slices. Resource construction uses existing
`ContextualChunk` bodies and exact parent-resource UTF-8 byte spans. Slices are
ordered by canonical span and packed deterministically. The fully serialized
request—including prompt, schema, slices, and JSON escaping—must fit 128 KiB;
an oversized slice is split only at UTF-8 boundaries while preserving offsets.
When contextual chunking returns no chunk for a short resource, the compiler
uses one canonical full-body fallback slice spanning `0..body.len()`.
`local_source` is trusted adapter context, not provider input. Provider-visible
slice IDs are derived from source kind, canonical span, and slice-body hash;
they never contain tenant-local episode, resource, or contextual-chunk UUIDs.

The provider returns source-neutral observations, not mutations:

```text
StructuredObservation {
  namespace, item_key, fields,
  disposition: state | event,
  evidence_slice_id, evidence_quote,
  valid_from, valid_to
}
```

The trusted adapter resolves slice ID plus exact quote to the original source
span and rejects substitution, mismatch, overlap ambiguity, invalid UTF-8, or
content-hash drift. The model never supplies tenant IDs, unit IDs, target IDs,
or authoritative spans.

Extraction is source-neutral: tenant-local `active_items` are not provider
input. After all batches succeed, one deterministic source-ordered fold consults
the current tenant-local active state and creates the existing
`StructuredStateOp`s:

- `state`: the last ordered observation creates or replaces the exact currently
  active unit; no batch may independently replace stale targets;
- `event`, workflow, and gotcha: append-only with a predicate stable from source
  kind, source ID, and canonical span. It never requires a synthetic episode ID.

The existing projection, mutation receipt, `source_resource_id`, citation mint,
tenant binding, bitemporal validity, forget/no-resurrection, and verified-recall
paths remain authoritative and unchanged.

## 4. Reuse and construction proof

Cache only validated source-neutral observations. The extraction key is:

```text
SHA256(contract revision, source kind, source body hash,
       ordered neutral slice IDs/spans/body hashes, requested model/provider policy,
       prompt hash, schema hash, request parameters, batching parameters)
```

A cache hit is allowed only for same-packet crash recovery or the sealed trusted
benchmark construction artifact. It must revalidate source content and every
quote/span plus the cached receipt's served model/provider against the frozen
policy, then perform fresh tenant-local folding, mutation, citation minting, and
receipts. Product tenants never receive a cross-tenant cache surface.

Construction proof schema v2 binds the authorization/campaign/screen hashes;
selection and source hashes; state mode; compiler, adapter, prompt, schema, and
provider-code hashes; requested/served model and provider; price/output/attempt
bounds; cache namespace and source receipt; exact paid-ledger attempt IDs and
before/after hashes; and settled plus unresolved cumulative totals. Prepared job
state also binds the input manifest and extraction receipt hashes.

## 5. One cumulative USD 200 guard

Extend the existing `ProviderAttemptLedger`; do not add a spend database. One
canonical campaign journal is keyed by the authorization-scope hash, with a
screen ID on each attempt. While holding its existing file lock, a cache miss
must atomically reject before credential or provider access when:

```text
opening liability + settled cost + unresolved reservations + next reservation
> 200_000_000_000 nano-USD
```

All provider paths, including the OpenAI SDK meter, must reserve their
worst-case liability through this API. A terminal priced result replaces its
reservation with authoritative cost. Error, crash, or unpriced output retains
the full reservation. Money is integer nano-USD end to end; float totals are
non-authoritative. Any external amount finer than one nano-USD is rounded up,
never down, before admission or settlement accounting.

The campaign opens with `$4.2580024`: `$0.4245304` settled STALE cost,
`$3.6000000` conservative liability for twelve unreconciled Deep calls, and
`$0.2334720` for the unpriced HTTP-400 structured attempt. Only an append-only
reconciliation event bound to authoritative receipt/proof hashes may reduce a
carried reservation.

Under the campaign lock, runners first validate canonical paths and
authorization, reject a terminal `closed` journal event, and then inspect a
same-packet cache entry. Only a validated cache hit skips `start`; a miss appends
and fsyncs its reservation before credentials or provider access. Closing is an
append-and-fsync terminal journal event binding the authorization hash, prior
journal hash, settled cost, and unresolved liability. A deterministic JSON
tombstone may project that event, but the journal remains authoritative if
projection crashes. Alternate journal/cache/output paths fail closed; old
packets that still say `AUTHORIZED_FOR_PAID_EXECUTION` cannot replay.

## 6. Frozen cost admission equation

No paid authorization is minted until a no-model full-data census proves the
construction bound and current official pricing/model identities are locked.
The campaign retains a minimum `$10` unallocated contingency. Let:

```text
C = full-corpus deduplicated construction liability, including every retry
R = one arm's reader-plus-native-judge liability at the official 200K profile
S = one complete Deep recall's enforced recall-wide spend ceiling

4.2580024 + C + 451 * (2R + S) + 10 <= 200
```

The old `$0.0874` value is forbidden for this admission decision: it was derived
from the 32,768-token development profile, not the official 200,000-token
profile. Likewise, pricing 96K input plus one completion is not a Deep ceiling;
the existing Deep contract permits multiple model/tool turns. `S` must be a
recall-wide hard stop covering every turn and token mix, reserved before recall.

The no-model census computes `C` and the maximum official reader/judge request
shapes that determine `R`. A provisional cheap Deep model is tested only after
free contract fixtures, and its packet must enforce `S` across the whole recall.
Provider fallback is disabled; exact requested/served identities, price caps,
and all three values are proof-bound. Cache discounts and expected use never
justify authorization.

If the inequality fails, no paid operational prefix or sampled consolation run
is allowed. Research a cheaper official-compatible provider/model, reduce the
serialized Deep workspace without reducing required evidence, or improve
query-blind construction packing, then rerun the free census. Qwen3.5-9B's
current `$0.10/M` input and `$0.15/M` output price and GPT-5.6 Luna's current
`$1/M` input and `$6/M` output price are candidates to evaluate, not proof that
the campaign fits.

## 7. Statistical and claim gate

The primary unit is the paired official judge result for each of all 451
questions:

```text
W = Deep correct, Fast wrong
L = Fast correct, Deep wrong
D = W + L
effect = (W - L) / 451
p = P[Binomial(D, 0.5) >= W]
```

The superiority hypothesis is directional and frozen before results, so the
primary test is one-sided exact McNemar at alpha `0.05`. Report the full paired
table, exact p-value, a one-sided 95% exact-compatible interval for paired risk
difference, and an exact interval for `W/D`. Promote only if:

- every one of 451 pairs is present, native-judge valid, receipt-backed, and
  fully settled, with no imputation or dropped failures;
- exact McNemar `p <= 0.05`;
- the paired risk-difference lower bound is above zero;
- the observed Deep gain is at least 5 percentage points;
- the official package has positive LAFS gain against the exact fixed reference
  frontier embedded in the pinned upstream leaderboard code;
- premise-awareness regressions are zero.

Domain and ability slices are descriptive only unless separately adjusted for
multiplicity. The prior `N=12`, two-net-win rule is retired as an efficacy gate;
with zero losses it has one-sided `p=0.25`. Five wins and zero losses are the
smallest discordant result that can cross one-sided 0.05, but the full official
census—not early stopping—owns the claim. Positive LAFS plus Deep-over-Fast
significance is an official benchmark result, not external SOTA by itself. A
SOTA claim additionally requires an accepted official submission and a frozen
official leaderboard snapshot showing this operating point strictly above every
published entry; ties are not SOTA.

For `N=451`, exact one-sided McNemar 80%-power minimum detectable effects are
5.42, 6.60, 7.58, and 8.46 percentage points when total paired discordance is
20%, 30%, 40%, and 50%, respectively. These are superiority-over-zero power
figures, not power to place the confidence lower bound above 5 points. The
official census is the largest available sample; no smaller pilot is presented
as statistically conclusive.

## 8. Execution gates

1. Implement ledger/closure enforcement first and close its regression tests.
2. Implement source-typed slices, observation validation/fold, and resource
   compiler identity under TDD.
3. Run all secret-free unit/integration/provider-lint/E2E gates.
4. Run a no-model, query-blind full-data census. It must prove request sizes,
   unique extraction keys, exact per-attempt reservations, `C`, official-profile
   `R`, feasible recall-wide `S`, the admission inequality, and no target-answer
   access.
5. Mint one immutable authorization packet for the single campaign journal.
6. Execute the frozen 12 cases as an output-sealed operational prefix. Continue
   only with zero provider/parser/receipt/settlement errors, valid Deep outputs,
   exact proof hashes, and cumulative liability within the envelope.
7. Without reading answers or scores, resume the other 439 cases. Then run the
   native judge once, settle every attempt, validate the full proof, and only
   then compute the paired statistics and official metrics.

Any budget, construction, validity, settlement, oracle-isolation, or promotion
failure closes the packet and records a benchmark-specific rejection. It does
not authorize a sampled SOTA claim or a broader run.

## 9. Minimum regression proof

- Resource evidence sets `source_resource_id`, never a synthetic episode/user
  identity; episode non-user evidence remains rejected.
- Slice substitution, span shift, quote mismatch, invalid UTF-8, stale content,
  reordered batches, and compiler-identity drift fail closed.
- Multi-batch folding equals one-batch folding and never targets stale unit IDs.
- Cached observations replay into fresh tenant-local units/receipts without
  cross-tenant visibility.
- Serialized requests stay within 128 KiB including all overhead.
- Two screens share one atomic ceiling and crash/error reservations survive
  restart. Closure/auth/path checks precede cache; a valid same-packet cache hit
  adds no attempt even at the ceiling; an over-cap cache miss rejects before
  credentials or provider.
- Closed packets and alternate artifact paths reject; same-packet crash resume
  adds no paid attempt.
- Construction-proof tampering with any v2 identity or ledger binding rejects.
- Existing resource forget/no-resurrection, ACL, verified receipt, provider lint,
  scratch-Postgres, and packaged E2E gates stay green.

## 10. Primary references

- LongMemEval-V2 official repository and protocol:
  https://github.com/xiaowu0162/LongMemEval-V2
- LongMemEval-V2 paper: https://arxiv.org/abs/2605.12493
- SciPy exact binomial test API:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html
- statsmodels exact McNemar API:
  https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html
- Fay and Lumbard, exact-compatible paired risk-difference intervals:
  https://pubmed.ncbi.nlm.nih.gov/33263202/
- Current model price records used only for this feasibility envelope:
  https://openrouter.ai/openai/gpt-5.6-luna-20260709 and
  https://openrouter.ai/qwen/qwen3.5-9b/api
