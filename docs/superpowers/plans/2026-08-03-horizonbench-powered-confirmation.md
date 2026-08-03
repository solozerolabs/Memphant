# HorizonBench powered confirmation plan

**Status:** designed, not authorized for paid execution.

## Decision

Target one claim only: MemPhant Fast is competitive on HorizonBench's
evolving-preference axis while materially improving reader latency and cost.
Do not pursue storage SOTA, restore LongMemEval-V2, or add another memory
engine. PostgreSQL remains the authority and the product path remains Fast.

## Ordered gates

1. **Free full-split census.** Pin the 4,245-row benchmark revision, enumerate
   the 360 released users, verify exact IDs and scoring-field quarantine, and
   prove that each user's repeated conversation bytes are identical. Ingest
   once per user, not once per question. Abort on any within-user timeline
   drift.
2. **Held-out paired confirmation.** Before reading answers, select 60 users
   and two questions per user, stratified to one evolved and one static item
   where available. Run the same Opus snapshot on full context and Fast. This
   is approximately $92 at the pilot's observed token prices. Require complete
   priced rows, user-clustered intervals, non-negative overall delta, positive
   evolved delta, no increase in evolved distractor selections, and at least
   six discordant pairs.
3. **Router falsification.** Do not reuse the failed self-abstention router.
   On the same frozen predictions, evaluate only gold-blind candidates derived
   from retrieval telemetry (coverage, current-state evidence, conflict, and
   packing displacement). Any learned/calibrated rule is trained outside the
   confirmation users and frozen before their scores are joined.
4. **Official treatment run.** Only after gate 2 passes, run Fast plus the fixed
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
