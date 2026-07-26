# Task 5 Report: Official State-Memory Campaign Gate

## Status

Complete. No paid/model calls, secrets, production systems, or databases were
used. The official 451-question LongMemEval-V2 Medium 200K census was
query-blind and no-model. Historical P1-T6 files remained byte-identical.

## Delivered behavior

- Froze the official LongMemEval-V2 code, dataset, leaderboard, tokenizer,
  prompts, model/provider routes, prices, and exact reader/judge request shapes.
- Added a production-identity no-model census over all canonical resource uses.
  Exact Qwen tokenization and greedy 128-KiB request batching reduce the unique
  construction plans from the one-slice baseline to 11,578.
- Bound the Qwen construction and Deep routes to DeepInfra only with no
  fallbacks. Runtime receipts reject a different served provider/model.
- Froze the native judge's upstream-exposed 2,048-token default explicitly.
- Removed hidden structured-provider retries. Every Rust invocation performs
  one HTTP attempt and records its externally supplied campaign attempt 1..=3.
- The Python campaign ledger makes one fsynced aggregate reservation for all
  exact first attempts plus the bounded retry pool before a credential-bearing
  launcher runs. Retry subsets consume the prepaid pool and never append a
  second campaign reservation.
- The Rust subledger independently enforces the same aggregate cap before each
  HTTP call: under a cross-process file lock it validates all prior rows, sums
  every started per-attempt reservation without refunds, and rejects malformed,
  restarted, duplicate, or over-cap work before transport.
- Aggregate settlement requires exact ordered-plan coverage, per-key attempt
  chains, requested/served route identity, unique generation receipts, decoded
  observation proofs, and actual cost no greater than each plan or the aggregate
  reservation. Ambiguous transport, accepted-generation, and generation-stats
  failures remain unresolved and block settlement.
- A narrow hash-bound `not_charged` result is allowed only for typed
  pre-generation HTTP 429/502/503 with no generation ID, usage, choices, or
  route identity, and only when a later prepaid retry decodes successfully.
  OpenRouter documents that non-200 status precedes model processing, recommends
  retrying 429/503, and provides zero-completion insurance:
  [errors and debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging),
  [zero completion insurance](https://openrouter.ai/docs/guides/features/zero-completion-insurance),
  and [failover billing caveats](https://openrouter.ai/blog/insights/reliability-failover/).

## Cost evidence

- Preserved one-slice baseline: `C = $3,136.9995807`; rejected.
- Preserved exact greedy-batched construction with Luna Deep:
  full three-wave liability `C = $165.6618075`; total `$454.6847047`; rejected.
- Final admission uses the exact first-attempt sum plus a prepaid `$10` retry
  pool. The independent `$10` campaign contingency remains untouched.
- The final exact terms and total are recorded in
  `docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/CAMPAIGN-CENSUS.json`.
- The final packet self-hash is
  `3924ea807c0fabd25514c25de86693f49216749e7b406fbe70d878daa33a38a7`;
  its file hash is
  `0f01e1eaf8bf3eeef543a46d9e97f6a956d48f915265542a6d7af2c8195b104e`,
  and it binds manifest hash
  `b9e80ec3e91aa13f0c077a7f3b98975696b7501feef3409d3dfa1e684c732fce`.
- The admitted total is `$199.5353461`, leaving `$0.4646539` below the
  `$200` hard ceiling after preserving the independent `$10` contingency.
- `paid_models_run = false` and `spend_nanos = 0` in every census artifact.

## TDD evidence

RED failures were observed for:

- Qwen Deep rejected because the runtime allowed only Azure.
- Deep request fallback policy was still enabled.
- structured extraction retried a 503 internally.
- recost and aggregate-wave functions did not exist.
- aggregate settlement lacked exact subledger coverage and route/cost bounds.

Focused GREEN:

```sh
cargo test -p memphant-runtime structured_state_openrouter --lib
cargo test -p memphant-runtime deep_recall_openrouter --lib
cargo test -p memphant-cli --test structured_state_census
python3 -m pytest tests/test_run_lme_v2_state_aware.py -q
```

The focused suites cover exact DeepInfra routing, no fallbacks, one internal
attempt, typed no-charge errors, aggregate cap exhaustion across restart,
malformed-ledger fail-closed behavior, exact tokenizer/template identity,
liability arithmetic, retry-wave limits, settlement coverage, paired
statistics, construction-proof tampering, and oracle-safe prefix sealing.

Additional GREEN evidence:

- Python repository gate: `887 passed, 12 skipped`.
- `cargo clippy --all-targets --all-features -- -D warnings`.
- `cargo test --all-targets --all-features`, `cargo fmt --check`,
  `cargo test --doc`, and `git diff --check`.
- Provider lint for `plain-postgres`, `supabase`, and `neon`.
- Migration dry-run over all three MemPhant migrations.
- The full Rust gate exposed two stale v1 compiler-identity fixtures. Both now
  name the authoritative greedy-batches v2 identity, passed in isolation, and
  the complete aggregate gate passed afterward.
- Scratch-Postgres and binary E2E probes were not run because the required
  `memphant-postgres-1` container was not running.

## Scope boundaries

- The final census is an admission proof, not a claim that the paid campaign ran
  or that MemPhant achieved SOTA.
- Paid execution remains a Task 6 operation under the single frozen campaign
  authorization and aggregate reservation contract.
- No compatibility shim, alternate execution path, secret-bearing census, or
  sampled/SOTA promotion was added.
- `.superpowers/sdd/2026-07-25-state-aware-resource-compiler/progress.md` was not
  modified.
