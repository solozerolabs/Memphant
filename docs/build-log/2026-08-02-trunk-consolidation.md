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

## Remaining publication gate

Run the complete `AGENTS.md` verification contract, push `main`, and verify the
exact GitHub Actions run. Any unavailable live-Postgres or private-Syndai check
must remain named as unavailable rather than reported green.
