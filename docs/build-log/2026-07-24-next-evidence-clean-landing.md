# Tri-domain next-evidence clean review handoff

Date: 2026-07-24. This is a clean **unlanded** handoff. It records the reviewed
branch state; it does not claim merge, push, deployment, production use, or
overall SOTA.

## Terminal decisions

| Domain | Minimum evidence run | Decision |
|---|---|---|
| Packing | frozen free n=12 screen, then 5/12 authorized answer-quality calls | reject all tested candidates; cap 1200 plus rerank stayed at 8/8 scored retrieval and 1/4 abstention, while the decision-aware treatment stopped as soon as 8/8 supported became impossible |
| Forgetting | bounded proposal screens, 259-row proposal expansion, and deterministic 385-case lineage replay | sleep-inspired offline consolidation reached 244 pass / 15 fail / 126 N/A, with 111 gains and zero baseline regressions; this ties Lethe and is not SOTA |
| Coding | four frozen SWE-ContextBench tasks across three arms, 12 authorized Codex calls | reject first tranche at the no-memory baseline ceiling: the official evaluator resolved the first 3/3 baselines, so the required +2 related-arm gain was mathematically impossible; nine patches remained intentionally ungraded and the 24-call broader tranche did not run |

The closed program executed 310 calls: 293 metered forgetting-provider calls,
5 metered packing-provider calls, and 12 Codex subscription task calls. Settled
provider cost is **$0.529098125** ($0.4150225 forgetting plus $0.114075625
packing); Codex did not expose an itemized dollar settlement. No campaign
remains authorizable. Natural-language forgetting proposals never directly
executed a destructive mutation.

## Reviewed state

- Public branch: `codex/memphant-tri-sota-completion`.
- Reviewed public fail-closed evidence checkpoint:
  `0fa5cb7f` (coding execution checkpoint
  `c70feead5b748383c587fc5f46b15161ff92af12`). The documentation-only
  final-head reconciliation follows it.
- The complete ordered next-evidence chain is recorded in the terminal
  reconciliation manifest; it includes the authorization, forgetting,
  packing, coding, and fail-closed closure commits through `0fa5cb7f`.
- Private status-mirror branch: `codex/memphant-active-read` at
  implementation/evidence checkpoint
  `ac9c1c979cc525220213474f93facc021a025016`.
- Public worktree, private mirror worktree, and the user's main checkout were
  clean before this final fail-closed evidence and reconciliation change.

## Verification after the last implementation/test change

- Python: 844 passed, 12 skipped. The skipped tests were reported as skipped,
  not passing.
- Public/private spec drift: clean with the private mirror path explicit.
- `cargo fmt --check`: passed.
- `cargo clippy --all-targets --all-features -- -D warnings`: passed.
- `cargo test --all-targets --all-features`: passed; database-dependent tests
  ignored by the default invocation were executed separately below.
- `cargo test --doc`: passed.
- Ephemeral migrated Postgres ignored-test gate: 77 passed.
- Provider lint: plain-postgres, Supabase, and Neon passed.
- Migration dry run: three migrations planned.
- Real server/CLI/worker/Postgres/MCP E2E probe: all checks passed.
- Official SWE-ContextBench Docker grading: the first 3/3 no-memory baselines
  resolved, with 10/10 FAIL_TO_PASS and 315/315 PASS_TO_PASS tests. The
  impossible-gain stop rule then prevented further grading and the broad
  tranche.

## Independent specialist review

- Forgetting: PASS. The historical screen is explicitly superseded; lineage,
  arithmetic, dependency graph, claims, and public/private STATUS mirror
  reconcile.
- Packing: PASS. Logical-proposal versus provider-call accounting, total calls,
  cost, child/result/bundle hashes, empty authorization state, handoff, and
  landing graph reconcile.
- Coding: PASS. Only the canonical consumed packet can reach execution; the
  historical authorization is inert bundle evidence. The six-entry bundle is
  bounded, license-safe, and parent/result hash-bound, with independently
  derived path/test/reference, usage, ledger, and grader checks.

No specialist reported a remaining blocker on the final diff.

The complete gate above ran after the final evidence-preservation, authorization
closure, test-path-policy, and regression-test changes. Later edits to this
handoff record verification results only.

## Landing boundary

This branch remains intentionally unmerged and unpushed. It inherits prior
program history and must not be merged wholesale. Any landing should follow the
ordered dependency chain in
`docs/build-log/2026-07-24-tri-sota-terminal-reconciliation.md`, cherry-pick the
next-evidence commits as the ordered groups recorded there, then rerun the
complete public gate and compatible private gate on the actual landing branch.
Remote CI, deployment, production enablement, another paid/model campaign, and
SOTA claims remain separate gates.
