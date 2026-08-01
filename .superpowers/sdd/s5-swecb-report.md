# S5 — SWE-ContextBench stage 0 report

- Branch: `s5-swecb`, branch point `0e874da0`. **Not merged to main.**
- Paid model calls: **0**. Settled cost: **$0.00**. Unsettled: **$0.00**.
- Preregistration: `docs/build-log/artifacts/s5-swecb/stage0-prereg.json` — committed at `fd2ebd7c`
  **before the first ingest**, amended at `0e137b54` **before any cell existed**.
- Build log: `docs/build-log/2026-08-01-swe-contextbench-retranche.md`, S5 section appended.
- Stage 2's $504 is **held** and is **recommended for cancellation**, on evidence independent of s4-controls.

## Outcome

**Stage 0 gate: GREEN.** Preregistered primary statistic — packed recall@5 of the official
Relationship parent, 357 distinct targets, ANY-PARENT, whole 1,007-row experience pool as one
haystack — is **0.7591** against a GREEN band of ≥ 0.50.

| k | packed recall@k | retrieval recall@k |
| --- | --- | --- |
| 1 | 0.5602 | 0.5602 |
| 3 | 0.7143 | 0.7283 |
| **5** | **0.7591** | 0.7787 |
| 10 | 0.8291 | 0.8459 |
| 25 | 0.8824 | 0.9048 |

Miss taxonomy at k=5 over 357 tasks — 271 hits; of the 86 misses, **74 ranked below the cut**, 8
retrieved but not packed, 4 never a candidate. Candidate coverage is 98.9%. **The bottleneck is
ranking**, not candidate generation and not the packing budget, so `pack_render_cap` is worth at
most ~2pp here and a reranker is the lever that matters.

Mechanism liveness, every item asserted in code with the run aborting on failure: 1,007 retains,
1,007 distinct episode ids, worker completed 1,007, queue drained **verified on the bench
credential** (pending=0, dead=0), 1,007/1,007 instances carrying a compiled unit, 1,053 units, 1,053
embeddings, **0 degraded recalls**, 376/376 traces fetched. Latency p50 1,398 ms / p95 4,368 ms.

## What changed about the plan, and why

**The tranche is 357, not 376.** `Related` is Lite (99) ⊎ Verified (166) ⊎ Multilingual (111)
concatenated, and Lite ∩ Verified is exactly the 19 duplicated `instance_id`s — which are *not*
byte-identical, differing in `version`, `problem_statement`, `FAIL_TO_PASS`, `PASS_TO_PASS`, and one
pair in `patch`/`test_patch`/`base_commit`. Confirmed independently by 357 Docker instance tags, by
357 files in `cases/SWEContextBench Full/`, and by the sub-split arithmetic. `Experience` is
likewise 1,100 rows over 1,007 distinct ids. The existing `run_swe_contextbench_memphant.py` would
abort on the full split for exactly this reason.

**The "$0 gold-patch pool" was inadmissible and was changed before any cell existed.** The brief's
premise — gold patches predate their targets, so no leakage — is false in both halves. Only 131 of
376 edges strictly predate (120 tie, 125 postdate), and the Related split's `created_at` is
internally inconsistent (23.6% within-repo inversions against PR number, versus 0 of 60,372 in the
Experience pool), so the temporal relation cannot be established from shipped metadata at all. The
load-bearing evidence needs no timestamps: **75.5% of gold parents touch a target patch file, 32.4%
have an identical touched-file set, 37.2% contain an exact target added line and 29.8% share ≥50% of
them**, against a same-repo random control of 9.1% and 0.13%. The patch-free body leaks at 9.0%
against a 6.9% floor set by the target quoting its own patch — clean, and still $0.

**Stage 0 has published comparators.** Table 5 reports Matched (%) — retrieved the gold parent — per
method on Lite: Mem0 39.39 @k=3, OpenViking 51.52 @k=3, Supermemory 59.60 @k=15, LangMem 73.34 @k=10.
The retranche log's "both arms must be ours" is correct for `Resolved` and **wrong for retrieval**.
A like-for-like Lite-scoped arm is running; the full-pool number above is not directly comparable
(harder pool, different k) and must not be cited as if it were.

## Live defects found, at $0

1. **HTTP 422 `observed_at must use a UTC offset`** — 300 of 1,007 experience rows ship `created_at`
   with a space separator and no timezone; 707 ship `...Z`. The split is exactly the 41 multilingual
   repos versus the 12 original Python repos, zero overlap. Fixed in `canonical_observed_at`, which
   refuses a *stated* non-UTC offset rather than silently shifting it.
2. **`rc` taken from the wrong end of a pipe** — the first invocation reported `EXITCODE=0` for a run
   that died with a traceback. `scripts/swecb_stage0_run.sh` now captures `rc` first and asserts a
   non-empty artifact.
3. **Cross-worktree `pkill`** — my reaper matched sibling lanes' `memphant-server` binaries and killed
   three s1-unitswap arms. Now scoped to this worktree's absolute path. s1-unitswap independently
   dropped this lane's scratch DB mid-drain by a `like 'memphant_scratch%'` pattern; both faults are
   fixed and the convention is recorded.
4. **Shared `with_scratch_db.sh` held a host-wide lock for whole runs** — adopted trunk's fix
   verbatim at `6fdcaf9d` rather than committing a third derivation, plus s1-unitswap's diagnostic
   note. This lane's first arm head-of-line blocked four lanes at loadavg 56; the with-patch arm was
   deliberately killed to release it (rc=143, artifact correctly refused, no DB orphaned).

## Why stage 2 should be cancelled as scoped

Table 3's own numbers: `Claude Sonnet 4.5 / Claude Code` resolves **19.68%** with no memory and
**23.40%** when handed the gold parent. **+3.72pp is the ceiling** for any retrieval-based memory
system on this split with this scaffold. Our expected effect is `0.759 × 3.72 = ` **2.82pp**, an
upper bound. Two-sided exact McNemar at the maximum available n of **357**:

| ψ | power (3.72pp ceiling) | power (2.82pp expected) | MDE@357 |
| --- | --- | --- | --- |
| 0.05 | 0.887 | 0.609 | 3.38pp |
| 0.15 | 0.395 | 0.239 | 5.89pp |
| 0.30 | 0.220 | 0.140 | 8.32pp |

**The expected effect sits below the MDE at every ψ, including 0.05**, and for agentic runs ψ is
0.15–0.34 (independence bound 0.339). Power 0.14–0.24. The instrument is too small for the effect it
measures — which is equally true of Table 4's own ranking and Supermemory's 4.04pp at n=99.

Three resource facts the plan did not carry: **~453 GB** of Docker pulls for a census run against
220 GB free; **no Claude Code scaffold exists in this repo**, so the pinned-scaffold requirement is
unbuilt and unpriced work; and the paper's trajectory pool is **public and free** for the 300 Lite
experiences, so the "~$737 rebuild" applies only to the other 707.

## Recommendation

- **Stage 1 (~$40): proceed, re-scoped.** Its value is the Claude Code + MemPhant round trip (the
  largest unpriced risk) and the same-arm re-run leg that measures ψ empirically, not ψ for a stage 2
  that should not happen.
- **Stage 2 ($504): CANCEL as scoped**, independently of s4-controls.
- **Promote FAIL_TO_PASS to primary** if a paid run is still wanted: Table 4 moves it 19.64 → 55.95
  versus Resolved's 26.26 → 30.30. ~10× the effect, measured per test. Already preregistered here as
  secondary; promoting it is an owner decision, not a drift.
- **Publish the retrieval result and the instrument audit.** Both are $0, neutral, public-instrument
  evidence on the endpoint MemPhant actually claims.

## Verification

`tests/test_swecb_stage0_recall.py`, 18 tests, all passing: both upstream timestamp formats and
refusal of a stated non-UTC offset; both body arms and refusal of an unknown one; recall@k as a rank
threshold; ANY-PARENT not double-counting; duplicate Related rows collapsing to one task; the miss
taxonomy separating never-retrieved from retrieved-but-unpacked; the 357/1,007 census; the Lite
configuration; an out-of-pool gold parent recorded as unreachable rather than scored as a miss; and
that the committed decision bands still partition the unit interval.

Scratch DBs only, dropped on exit. No STATUS, ledger, default, or SOTA claim moves. No merge to main.

## Artifacts

- `docs/build-log/artifacts/s5-swecb/stage0-prereg.json` — preregistration + amendment
- `docs/build-log/artifacts/s5-swecb/stage0-recall-patchfree.json` — the GREEN gate run
- `docs/build-log/artifacts/s5-swecb/published-comparators.json` — Tables 3/4/5 re-extracted
- `scripts/swecb_stage0_recall.py`, `scripts/swecb_stage0_run.sh`, `tests/test_swecb_stage0_recall.py`
