# B5 recall-stage retirement

Date: 2026-07-23

Implementation commit: `2cd157a4`

## Decision

The deterministic heuristic reranker and structural query decomposition are
retired. `RecallMode::Balanced` is removed rather than retained as a silent
alias. Public recall now exposes only `fast` and explicit diagnostic `deep`.

This follows the answer-blind real Postgres result in
`2026-07-10-real-retrieval-campaign.md`: disabling the heuristic improved
Recall@5 by 0.143 with a 95% confidence interval of [0.036, 0.286], while
disabling query decomposition changed no scored question at Recall@5 or
Recall@10. The earlier positive decomposition fixture was synthetic and does
not outweigh the real zero-delta result.

## Durable boundary

- Removed the heuristic stage, learned-profile wrapper, decomposition stage,
  request controls, trace fields, eval flags, promotion validators, and their
  synthetic rung 8/9/13 fixtures.
- Removed `balanced` from Rust, REST/OpenAPI, MCP, CLI, eval, and Python runner
  choices. Unknown legacy fields and values still fail through the strict
  public deserializers; no compatibility alias was added.
- Preserved the construction-time cross-encoder seam, its explicit
  `MEMPHANT_CROSS_RERANK` default-off boundary, failure trace, and
  vector/lexical candidate-selection strategy.
- Folded DSR retrievability into fused score before canonical sorting. The
  deletion audit exposed that review-derived decay had been computed and
  traced but depended on the retired heuristic to affect subject-deduplicated
  ordering. The independent decay golden now passes with the heuristic absent.
- Regenerated OpenAPI, MCP tools, and retrieval-trace schemas through their
  owning binaries.
- Updated the local Deep evaluator and packaged CLI smoke to classify raw
  source selection as `insufficient` unless a canonical byte-span receipt
  exists. They no longer manufacture `supported` evidence.

## Verification

- `python3 -m pytest tests/ -q`: 723 passed, 11 skipped, 0 failed.
- `cargo test --all-targets --all-features`: full run reached the updated
  recall golden and exposed one stale stage expectation; after its correction,
  the focused golden passed 13/13. A final full gate remains for program exit.
- `cargo test -p memphant-eval --test eval_contract --all-features`: 16 passed,
  1 ignored paid-network test, 0 failed.
- `cargo clippy --all-targets --all-features -- -D warnings`: passed.
- `python3 scripts/check_spec_drift.py`: skipped because the private mirror is
  absent from this MemPhant worktree; this is not counted as a pass.
- No model or paid provider calls were made. Spend: USD 0.

## Non-claims

This retires disproven mechanisms. It does not promote the cross-encoder,
prove reader-QA gain, promote Deep, close cutover, establish a public benchmark
claim, or authorize push/deploy.
