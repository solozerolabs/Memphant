# State-aware STALE pilot: rejected, no broadening

## Decision

`REJECTED_STOP_NO_BROADENING`.

The answer-blind development selection contained two Type-I and two Type-II
official scenarios, 200 sessions, and all three probes per scenario. The
current arm completed 12/12 queries, but every Deep recall ended
`InvalidOutput`. Its reader still returned answers over fallback evidence, with
25/25 returned items carrying verified receipts, but the zero-provider-failure
predicate was already false. Deep provider cost was not retained in the STALE
proof and therefore could not reconcile, which is a second independent gate
failure.

The candidate then stopped during its first four structured extractions: one
request returned HTTP 400 and three paid Google AI Studio responses failed
decode. No candidate recall or reader call ran. The immutable candidate ledger
records 16,503 prompt tokens, 4,846 completion tokens, and $0.1313685 settled
extractor cost. Current reader evidence records 302,608 prompt tokens, 17,772
completion tokens, $0.2931619 settled cost, and 12 unique first attempts.
Known settled cost is therefore $0.4245304 plus unreconciled current Deep cost.

The native judge was not run because correctness cannot rescue a pilot that
fails the required provider, receipt-settlement, and completed-pair predicates.
The 400-scenario / 1,200-query expansion is forbidden. No STALE score or SOTA
claim is made.

## Prepaid operational repair

The first authorization stopped with zero provider attempts when an official
timezone-naive timestamp reached MemPhant's UTC-offset boundary. The adapter
now interprets STALE's synthetic naive timestamps as UTC and emits canonical
RFC 3339. The failed packet was closed before the replacement packet was
committed.

## Remaining architectural blocker

Before another STALE campaign can be authorized, Deep must produce valid
finishes on this frozen selection and the benchmark proof must retain and
reconcile Deep provider attempts separately from reader attempts. Structured
extraction also needs a no-oracle contract smoke against the exact current
schema/model route. Those are architecture/protocol prerequisites, not reasons
to tune or resend this pilot.

## Evidence

- Selection: `benchmarks/manifests/stale_paired_pilot.v1.json`
- Authorization: `docs/build-log/artifacts/state-memory-sota/stale-pilot/AUTHORIZATION-2.json`
- Closure: `docs/build-log/artifacts/state-memory-sota/stale-pilot/AUTHORIZATION-2-CLOSURE.json`
- Current proof: `docs/build-log/artifacts/state-memory-sota/stale-pilot/run-2/current/proof.json`
- Candidate extractor ledger: `docs/build-log/artifacts/state-memory-sota/stale-pilot/run-2/candidate/structured-attempts.jsonl`
