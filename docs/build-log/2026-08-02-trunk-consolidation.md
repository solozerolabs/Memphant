# Trunk consolidation

Date: 2026-08-02

## Scope

Consolidate every remaining local MemPhant change onto one authoritative `main`,
remove only proven-disposable observations, and preserve every benchmark cache and
decisional evidence body.

## Branch and worktree census

- Local branches: `main` only.
- Worktrees: `/Users/sidsharma/Memphant` only.
- Local branches not merged into `main`: zero.
- `origin/main` was fetched before integration and was an ancestor of local
  `main`; the push path is a normal fast-forward, never a force push.
- The ten prior work lanes were already merged and retired. No branch body
  remained to cherry-pick.

## Stash disposition

The sole stash was `dfba1cf077037eeff156b38abcde3f068f73cdb7`
(`s3-full`). Its 28 tracked paths were compared by Git blob identity with the
current tree:

- 25 were byte-identical.
- `crates/memphant-core/src/service.rs`,
  `crates/memphant-types/src/lib.rs`, and `openapi/memphant.v1.json` had later
  current-tree evolution; inspection found no intended stash behavior absent
  from the current implementation.
- The stash's untracked
  `crates/memphant-core/tests/caller_authored_keys.rs` was byte-identical to the
  current tracked file.

The stash was therefore fully contained and dropped. Nothing was replayed.

## Cleanup disposition

Moved to macOS Trash so recovery remains possible:

- `L.txt`
- `rw.csv` (Retraction Watch observation, 63 MiB)
- `rwl.txt`
- S1b arm/process logs and server logs

The three root files appeared only in dirty-path lineage observations; no
registered artifact named them as an input or bound their hash. The transient
logs were not proof inputs. The paired S1b arm JSON bodies, liveness gate,
analysis, MiniLM preregistration/result, and all out-of-repo benchmark caches
under `~/.cache/memphant-bench/` and `~/.cache/memphant/` were preserved.

## Evidence landed

- S1b tau=0.53 vs fresh tau=0.42: negative, same-tree live paired result;
  no default or status checkbox moved.
- Track R MiniLM top-64: worse accuracy and latency than the shipped fused
  order; diagnostic-only instrument; no default or status checkbox moved.
- Both machine results are registered in the evidence contract and their
  focused regression tests pass.

## Verification

- `python3 -m pytest tests/ -q`: 808 passed, 8 skipped.
- Spec drift, power instrumentation, and evidence-contract checks: clean;
  59 contracted artifacts and 45 named retrofit-pending artifacts.
- `cargo fmt --check`: clean.
- `cargo clippy --all-targets --all-features -- -D warnings`: clean.
- `cargo test --workspace --all-targets --all-features`: exit 0.
- `cargo test --doc`: exit 0.
- Scratch-Postgres store and worker executable tests: 93 passed, 0 failed.
- Provider lint: clean for `plain-postgres`, `supabase`, and `neon`.
- Migration dry-run: nine migrations ordered through
  `20260801_009_drop_dead_schema.sql`.
- Real server, worker, CLI, MCP, and PostgreSQL E2E probe: all checks passed.
- The private Syndai mirror was synchronized through its required worktree and
  preflight path; remote `main` accepted `e824dff66` and CI passed.

The scratch gate exposed one stale test assertion: the worker's current drain
contract prints `completed`, `failed`, `retried`, and `deferred`, while the old
process test still parsed everything after `completed=` as one integer. The
producer and benchmark consumers already shared the stronger four-field
contract, so only the stale test was corrected. The focused live regression and
the complete 93-test scratch suite then passed.

Rust 1.96.1 on this macOS host repeatedly left compiler and zero-case rustdoc
children sleeping between Cargo jobs. A temporary out-of-repo wrapper cleared
inherited jobserver variables and serialized builds; no repository or toolchain
change was made. The official scratch command's substantive tests all passed,
but its trailing zero-case rustdoc stalled. The same packages therefore ran
with `--tests` to a clean exit, alongside the separate clean workspace doctest
gate. This is an execution-environment caveat, not product or evidence proof.

## Publication result

`main` was pushed by normal fast-forward through `9ce85041`. GitHub Actions run
`30808877476` completed successfully at exact head
`9ce85041116150250a6aca2781a48713230a2136`; both `public-gates` and
`postgres-contracts` passed. Subsequent zero-spend external-instrument audits
are recorded separately; their current releases did not pass acquisition.
