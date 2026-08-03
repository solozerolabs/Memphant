# HorizonBench powered confirmation plan

**Status:** owner authorized gates 1-2 on 2026-08-03 and explicitly removed the
original $92 constraint. The runner retains a $120 fail-safe ceiling and uses
Anthropic explicit prompt caching; its pilot-calibrated estimate is $86.11
before a 5% planning buffer ($90.41 buffered).
The first authorization failed closed at $0 before any score when Opus 4.5
rejected a 268,591-token input against its 200k window. The replacement uses
Opus 4.6's 1M window for both arms; truncation and compression remain forbidden.
Its first response then exposed a separate 256-token output-cap failure after
one $1.3563 settled call; an interrupted second attempt retains a $10.252875
unsettled upper bound. The final authorization raises only output headroom to
1,024 and stops immediately on any parse failure.
That authorization exposed one last shared-boundary bug after 52 valid rows:
a content-filter refusal was treated as an unpriced instrument error. Those
partial rows remain unscored. The final runner records refusals as priced-at-$0
terminal abstentions, binds `run_reader.py` into authorization lineage, and
starts from a clean authorization.
The complete 4,245-item treatment run remains unauthorized.

## Decision

Target one lane only: MemPhant Fast is competitive on HorizonBench's
evolving-preference axis while materially improving reader latency and cost.
This result feeds, but can never establish by itself, the five-axis claim in
`2026-08-03-multi-axis-near-sota-program.md`.
Do not pursue storage SOTA, restore LongMemEval-V2, or add another memory
engine. PostgreSQL remains the authority and the product path remains Fast.

## Ordered gates

1. **Free full-split census.** Pin the 4,245-row benchmark revision, enumerate
   the 346 benchmark-contributing users, reconcile them against the 360 released
   graph users without acquiring graph gold, and verify exact IDs and
   scoring-field quarantine. The census found per-question monotone prefixes,
   not identical conversation bytes; exclude the two identity-collision users
   and require strict prefix consistency for every selected user. Ingest each
   selected timeline incrementally once, not once per question.
2. **Held-out paired confirmation.** Before reading answers, exclude all ten
   exposed sample users and select 20 users per generator, 60 users total, with
   one evolved and one static question each. Ingest each user's monotone
   timeline prefixes incrementally, then run the same Opus 4.6 snapshot on full
   context and Fast.
   The authorization permits 240 logical reader calls, at most 480 provider
   attempts, no Deep, and at most $120 combined spend. Exact shared
   full-context prefixes must produce an observed cache write then read for
   every user pair; a missing cache event stops the run. Require complete
   priced rows, user-clustered intervals, non-negative overall delta, positive
   evolved delta, no increase in evolved distractor selections, and at least
   six discordant pairs.
3. **Router falsification — not authorized now.** Do not reuse the failed self-abstention router.
   On the same frozen predictions, evaluate only gold-blind candidates derived
   from retrieval telemetry (coverage, current-state evidence, conflict, and
   packing displacement). Any learned/calibrated rule is trained outside the
   confirmation users and frozen before their scores are joined.
4. **Official treatment run — not authorized now.** Only after a separate owner
   authorization following gate 2, run Fast plus the fixed
   reader on all 4,245 items. At the pilot rate this is about $298, versus about
   $2,940 for full context. Compare the complete official score with the
   paper's 52.8% overall and 51.3% evolved references; use user-clustered
   uncertainty and report generator strata.
5. **SOTA wording.** Claim HorizonBench SOTA only if the complete official
   treatment exceeds the reverified best comparable score with complete
   accounting. Otherwise claim only a mechanism or UX/cost result. No result
   on this axis changes storage, code, docs, or tri-domain SOTA.

## Stop rules

- No paid command runs without a new committed authorization packet.
- Stop after the paired confirmation if overall accuracy regresses, evolved
  distractors increase, discordance remains below six, or any provider/ledger
  row is incomplete.
- Do not tune on the ten-row sample; it is now analysis-exposed.
- Do not invoke Deep merely to consume the remaining pilot budget. Deep earns
  a role only after a gold-blind router identifies cases and completed Deep
  evidence improves a held-out paired result.
