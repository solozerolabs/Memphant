# S5 — SWE-ContextBench stage 0 report

- Source branch: `s5-swecb`, branch point `0e874da0`. **Locally merged into integration `main` at
  `a3f4fe51`; not pushed.**
- Paid model calls: **0**. Settled cost: **$0.00**. Unsettled: **$0.00**.
- Preregistration: `docs/build-log/artifacts/s5-swecb/stage0-prereg.json` — committed at `fd2ebd7c`
  **before the first ingest**, amended at `0e137b54` **before any cell existed**.
- Build log: `docs/build-log/2026-08-01-swe-contextbench-retranche.md`, S5 section appended.
- **Stage 1 ($40) and stage 2 ($504) are both CANCELLED.** The ~$1-5 scaffold probe was approved but
  never started, and was skipped. **Nothing was spent on this lane.**

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
The Lite-scoped arm reproduces the published configuration exactly (99 targets, 300-row pool):
Mem0 39.39 → **68.69** at k=3, OpenViking 51.52 → **68.69** at k=3, LangMem 73.34 → **79.80** at
k=10; Supermemory's k=15 is left **bracketed** (79.80@10 to 87.88@25) rather than interpolated.
MemPhant leads every published method at matched or smaller k. The metric alignment was *verified*
rather than assumed: Mem0's per-rank cells sum to 49.49 against an Overall of 39.39, so Overall is a
union over top-k — the same ANY-PARENT recall@k reported here. That reasoning must stay attached to
the number wherever it travels, as must the caveats: n=99, the authors' own runs of other people's
systems, no per-instance data, so **nothing can be paired and no significance test is possible**.

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

## Why stage 2 was already unbuyable before s4 reported

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

## Endpoint adjudication and final disposition

**Which endpoint can this instrument resolve at n=357?** Ceiling = published baseline → best memory
arm; expected = ceiling × measured recall (0.7591).

| endpoint | baseline → best memory | ceiling | expected | verdict |
| --- | --- | --- | --- | --- |
| `Resolved` | 26.26 → 30.30 | 4.04pp | 2.82pp | **NOT resolvable at any ψ** |
| **`FAIL_TO_PASS` Tasks** | 29.29 → 40.40 | **11.11pp** | **8.43pp** | power **0.75–0.99** across all ψ ∈ [0.15, 0.40] |
| `Patch N/A` | 3.03 → 10.10 | 7.07pp | 5.37pp | borderline; diagnostic only |
| `PASS_TO_PASS` Tasks | 88.89 → 88.89 | 0.00pp | — | nothing to measure |

**A trap avoided.** Table 4's `FAIL_TO_PASS` **Tests** column (19.64 → 55.95) looks like ~10× the
`Resolved` effect and must not be the endpoint: tests cluster within tasks — one correct patch flips
all of a task's F2P tests together — so per-test n is not independent and the effect is inflated by
within-task correlation. The per-**task** column is the honest version, and it is the one costed.

**Stage 1 ($40): CANCELLED.** Its preregistered purpose was ψ estimation for stage 2. Void twice:
`Resolved` stage 2 is cancelled, and the surviving F2P-Tasks stage 2 is powered *whatever ψ turns out
to be*, so ψ changes no decision. It could not have delivered anyway — n=30 gives 4.5–9.0 discordant
pairs against the n_d ≥ 6 floor, power 0.12.

**Stage 2 ($504): CANCELLED.** s4-controls reported: on the Track R paraphrase bank (n=180) agentic
`grep` hits@10 **96.67%** against MemPhant's **58.89%**; paired b=1, c=69, n_d=70,
**McNemar p = 1.2e-19**, delta **−37.78pp** against a realized MDE of 13.34pp. The packing objection
is pre-closed: MemPhant's pre-packing fused@10 against the control's packed@10 still loses 33.89pp.

**Scaffold liveness probe (~$1–5): approved but NEVER STARTED, and skipped.** Its only value was
retiring execution risk for a run that will not happen. **Total lane spend: $0.00.**

## The honest reading of the comparator table

The like-for-like result stands, and it must travel with its companion sentence:

> MemPhant beats other memory systems at retrieving the gold parent — **and all of them are losing to
> an agent with a shell.**

Mem0 scores *below* no-context on this very benchmark (24.24 vs 26.26 Resolved); on SWE-Explore an
agent with `grep` gets HitFile@5 0.667 against dense-RAG's 0.088. Winning among memory systems is not
winning the task.

**Three lanes, one conclusion.** S6/S7 die from *below* (gold computable from the fact statements, so
a short rule saturates the baseline). S5 dies from *above* (baseline wide open at 19.68%, addressable
headroom 3.72pp, under its own MDE at max n). S4 shows the substrate losing to a shell loop by
37.78pp on repo-recoverable facts. **Track R and SWE-ContextBench both test facts recoverable from
files, and an agent with `grep` is extremely good at that.** The niche the substrate wins is what is
*not* in the repo — corrections, preferences, rejected approaches, rationale.

## Recommendation to the plan of record: pair a ceiling check with every baseline check

A baseline rate alone is not sufficient evidence that an instrument can express an effect. Two free
checks are needed at acquisition:

1. **Baseline check** (already standard) — is the no-memory baseline far from ceiling? Guards against
   the S6/S7 saturation failure.
2. **Ceiling check** (proposed) — take the largest *published* effect any comparable system achieves,
   ideally an oracle arm since that upper-bounds retrieval-based memory, and compare it to the
   instrument's MDE at its **maximum available n**. If addressable headroom is below the MDE, the
   instrument cannot express the effect **at any budget** and no tranche fixes it.

This lane is the worked example: check 1 passed correctly (80pp of headroom) and $545 was scoped on
it; check 2 takes ten minutes from the *same table* (oracle 23.40 vs baseline 19.68 → 3.72pp against
MDE 3.38–8.32pp) and shows the effect was never resolvable. Concretely, `benchmarks/manifests/
*.lock.json` should carry an `effect_ceiling` block beside the existing `power` block, and
`scripts/instrument_power.py` should refuse to emit a staging plan when ceiling/MDE < 1.

## Verification

`tests/test_swecb_stage0_recall.py`, 18 tests, all passing: both upstream timestamp formats and
refusal of a stated non-UTC offset; both body arms and refusal of an unknown one; recall@k as a rank
threshold; ANY-PARENT not double-counting; duplicate Related rows collapsing to one task; the miss
taxonomy separating never-retrieved from retrieved-but-unpacked; the 357/1,007 census; the Lite
configuration; an out-of-pool gold parent recorded as unreachable rather than scored as a miss; and
that the committed decision bands still partition the unit interval.

Scratch DBs only, dropped on exit. No STATUS, ledger, default, or SOTA claim moves. Local integration
does not promote the result or make it a shipped claim.

## Artifacts

- `docs/build-log/artifacts/s5-swecb/stage0-prereg.json` — preregistration + amendment
- `docs/build-log/artifacts/s5-swecb/stage0-recall-patchfree.json` — the GREEN gate run
- `docs/build-log/artifacts/s5-swecb/published-comparators.json` — Tables 3/4/5 re-extracted
- `scripts/swecb_stage0_recall.py`, `scripts/swecb_stage0_run.sh`, `tests/test_swecb_stage0_recall.py`
