# State-memory Task 6 runner checkpoint: free implementation only

This checkpoint implements the authorization, money, cache, and resumability
boundaries required before the LongMemEval-V2 campaign may make a paid call. It
does **not** authorize a model call, report a prefix result, update STATUS, or
claim Task 6 complete.

Implemented and regression-tested:

- the census emits a deterministic exact construction-plan inventory, including
  request, extraction, reservation, model, source, batch, and evidence-slice
  identities;
- the production planner can execute an allowed inventory subset with at most
  32 workers and no hidden retry, rejecting plan drift before provider or
  credential access;
- the structured-state cache is immutable, per-key locked, authorization,
  campaign, and namespace bound, and validates request, source, evidence quote,
  observation, served route, source attempt, and source result identities;
- cache hits write cost-zero receipts and never write a paid attempt; cache-only
  case materialization fails on a miss before transport;
- the aggregate construction wave is created and fsynced before its sole central
  reservation, and resumes the same reservation across crashes before the
  append, after the append, or inside the launch;
- final construction settlement accepts only exact paid-chain/cache-receipt
  coverage and rejects duplicate, foreign, ungrounded, unresolved, or route-
  drifted evidence;
- the adapter validates static authorization and canonical cache, subledger, and
  campaign-journal paths at initialization, revalidates them before worker
  launch, then derives proof-v2 cache and ledger fields from post-worker
  receipts;
- immutable campaign authorization uses the current exact census, manifest,
  plan inventory, public Qwen/DeepInfra and native GPT-5.2 authority refresh,
  one ProviderAttemptLedger journal, exact historical opening reservations, and
  canonical artifact paths;
- `prewarm-prefix` materializes the exact census input, commits the remaining
  439 IDs without oracle fields, selects the first-12 construction keys, and
  launches only that subset under the already-reserved full construction cap.

Still required before any paid command is safe:

1. complete scratch case-bank construction from the prewarmed cache;
2. run official Fast reader, Deep recall plus Qwen reader, and native GPT-5.2
   judge for the sealed 12 without exposing oracle or score fields;
3. seal and operationally validate the prefix, then resume the remaining 439;
4. settle all construction, reader, Deep, and judge attempts and build the
   native official package and closure;
5. update all final source hashes and run one fresh no-model census, then mint
   `CAMPAIGN-AUTHORIZATION.json` and run the complete free gate.

No credential-bearing command was run and no campaign authorization artifact
was minted in this checkpoint.
