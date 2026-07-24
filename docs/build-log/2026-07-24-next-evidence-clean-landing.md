# Tri-domain next-evidence clean review handoff

Date: 2026-07-24. This is a clean **unlanded** handoff. It records the reviewed
branch state; it does not claim merge, push, deployment, production use, paid
execution, or overall SOTA.

## Terminal decisions

| Domain | Minimum evidence run | Decision |
|---|---|---|
| Packing | frozen n=12 free screen | reject all tested candidates; cap 1200 recovered 8/8 scored retrieval cases but exact abstention regressed 3/4 to 1/4; paid surface deleted |
| Forgetting | frozen n=12 technique screen plus one 385-case winner expansion | reject deterministic semantic execution; operation-boundary triage leaves product RCA open; exact-ID confirmation remains authoritative |
| Coding | n=12 SWE-ContextBench transport rehearsal plus DeepSWE 12-pair admission audit | task efficacy blocked by license/authorization; paired DeepSWE rejected at 3/12 causal pairs |

No model call was made and settled cost is $0. The only staged paid request is
16 non-executing ForgetEval proposals, capped at $0.50. Its status remains
`AWAITING_EXPLICIT_PAID_AUTHORIZATION` with `authorization: null`; mutation
execution is excluded.

## Reviewed state

- Public branch: `codex/memphant-tri-sota-completion`.
- Reviewed public head: `b3d98795536310a4a7794e0c6e9d72077326e15b`.
- Next-evidence commit chain: `665a1be3`, `fc873e75`, `adbf55ef`,
  `7dfae997`, `5ebd868e`, `ece26197`, `702097ee`, `f25a6805`, `5a99a0e3`,
  `a1f89b18`, `b3d98795`.
- Private status-mirror branch: `codex/memphant-active-read` at
  `0637d839fe552f6ab46d622f9f3ed6c95f9e64a7`.
- Public worktree, private mirror worktree, and `/Users/sidsharma/Memphant`
  were clean before this documentation-only handoff commit.

## Verification after the last implementation/test change

- Python: 808 passed, 12 skipped. The skipped tests were reported as skipped,
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
- Fresh pinned DeepSWE checkout validator: three accepted pairs, twelve
  required, zero model calls, zero container runs.

## Independent specialist review

- Forgetting: PASS at `a1f89b18`; the 385-case triage reproduces byte-for-byte,
  all 126 failures contain assertion evidence, root cause remains explicitly
  open, and confirmation/authorization paths fail closed.
- Coding: PASS at `a1f89b18`; live compare projections match both canonical
  ancestry hashes, hidden target material remains excluded, and artifact hashes
  match the inventory/authorization packet.
- Packing: PASS at `b3d98795`; the rejected paid surface is absent, both old and
  current packets are non-authorizing, the old page is a regression-protected
  tombstone, and only forgetting proposals remain potentially authorizable.

The change after the coding and forgetting reviews only tombstoned the stale
packing document and added its regression assertion. The complete gate was
rerun after that change. No specialist reported a remaining blocker.

## Landing boundary

This branch remains intentionally unmerged and unpushed. It inherits prior
program history and must not be merged wholesale. Any landing should follow the
ordered dependency chain in
`docs/build-log/2026-07-24-tri-sota-terminal-reconciliation.md`, cherry-pick the
next-evidence commits as one reviewed terminal group after their dependencies,
then rerun the complete public gate and compatible private gate on the actual
landing branch. Remote CI, deployment, production enablement, model execution,
and SOTA claims remain separate gates.
