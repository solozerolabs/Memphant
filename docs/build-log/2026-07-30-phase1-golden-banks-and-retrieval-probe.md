# Phase 1 — golden banks, substrate-transfer replay, retrieval probe (2026-07-30)

Cost: **$0 paid API spend** across the whole phase. Mining and adjudication ran
on subscription-model agent calls through content-hash reply caches; every run
here is deterministic retrieval with no reader and no judge.
Plan: `docs/superpowers/plans/2026-07-27-accuracy-first-program.md` §Phase 1.
Prerequisite: Phase 0 landed (`docs/build-log/2026-07-30-packing-gate-amendment.md`).

No checkbox, default, cutover, deployment, or SOTA claim moves in this document.
No number here is publishable: the Track R spot-check state is
`emitted_pending_owner_review`, and the Track U bank derives from private
material under a paraphrase-scrubbed-variant rule.

## Headline

The coding lane now has its first real measurement, and it is a **negative
result that survives correction**:

- On a like-for-like construct — same ranking stage, same haystack — **BM25 beats
  MemPhant on the Track R bank**: r@10 0.8944 vs 0.8167 (paired 28 BM25-only vs
  14 MemPhant-only, McNemar exact **p = 0.0436**), and r@5 0.8278 vs 0.6278
  (paired 50 vs 14, **p = 0.00001**).
- The Phase 1 kill gate therefore **fires**: ownership question (d) defaults to
  "Syndai keeps its tables until the substrate wins."
- A **first attempt at this comparison reported a much larger gap (0.506 vs
  0.806) for the wrong reason** and had to be discarded. §3 records why, because
  the error is the same class of error Phase 0 existed to correct.
- Independently, `pack_render_cap` produced a **valid measured null** on code
  bodies. It is a chat-lane footnote; Phase 2 drops in priority.

Two localized, $0-discovered engineering targets fall out, in §5.

## 1. Golden banks

**Track R (repo memory) — 180 goldens, bar 14/15.**
`benchmarks/data/track_r_repo_memory_golden.lock.json`; bank sha256
`6f549daa…`; 60 each of state-churn / file-symbol-grounding / task-resumption;
156 distinct attempts, 129 distinct repositories. Source
`nebius/SWE-rebench-openhands-trajectories` rev `35455389ab51bf5e2306bfd436ef72d0f98bf882`
(CC-BY-4.0), 495 attempts / 64,055 events, corpus sha256 `c008142e…`, via the
proven `materialize_public_code_lane.py` adapter. Accept rate 0.4369 of 412
generation calls. Bar preregistered and committed **before** mining
(`docs/build-log/2026-07-30-track-r-golden-bar.md`, `0cf468eb`).

The three v3 rejection-receipt failure modes are closed by measurement:
identification 100%, every golden agent-adjudicated, **zero** shipped with an
unadjudicated distractor, **zero** with a distractor judged to also answer, 180
distinct question skeletons for 180 goldens (max skeleton share 0.56%), mean
question/answer lexical overlap 0.052 (max 0.323).

The single failing check is **`with_distractors_ge_50pct`: 75/180 = 41.7%**
against a 50% floor. This is not softness. It is the identification gate proving
*stronger* than the bar assumed — most accepted questions narrow the whole
64k-event corpus to the target alone, leaving no plausible distractor to
adjudicate. `bar_passed: false` **stands**; amending that one threshold is an
owner decision and was deliberately not self-approved. §4 measures whether the
miss matters.

**Track U (user learning) — 51 goldens.** `benchmarks/data/user_lane_golden.lock.json`;
bank sha256 `e29821b2…`; correction-retention 27 / staleness 12 /
scope-contradiction 12 — the three slice-1 axes only; the four deferred axes were
not built. Category mix procedural 34 / semantic 10 / guardrail-exception 5 /
identity 2, within 1.7pp of the measured 65/20/10/5 power-user distribution (the
extractor fails the run past 8pp). 60 candidates → 51 accepted, 9 rejected with
recorded reasons. Privacy preregistration committed before extraction
(`docs/build-log/2026-07-30-track-u-privacy-prereg.md`).

Source counts pinned at extraction, and **the plan's figures were stale**:
`feedback_*` files number **90**, not "~60 (verified count)" — Syndai 67 /
Yurivan 14 / Ideas 3 / Ideas-tacitry 3 / sprntly 2 / Namera 1. `LEARNINGS.md`
61 entries; Syndai `AGENTS.md` 17 hard rules + 9 session-execution; MemPhant
`AGENTS.md` 25 bullets across 6 sections.

Construct properties worth keeping: correction goldens are enforced **bundles**
(rule + incident + how-to-apply; a bare triple is rejected); scope goldens are
**mirror pairs with identical temptations**, so a global-preference baseline is
forced to fail one side; every golden records `observable_correct_behavior` and
`forbidden_behavior`, so a future scorer grades **end behavior**, not
retrieval@k, per the plan's adherence rule.

## 2. Phase 1b — substrate-transfer replay: a valid null

Instrument (`a2838bf1`): `code_lane_run_memphant.py` records per-question
`dropped_items`/`RecallDropReason`, a `hit`/`in_pool_unpacked`/
`absent_from_pool` bucket, and run-level `budget_share_of_in_pool_unpacked`;
`--pack-render-cap` is admitted explicitly, without reopening the blanket
packing-env inheritance the shared harness deliberately closes (there is a test
proving an ambient `MEMPHANT_PACK_RENDER_CAP=9999` never reaches a cap-OFF arm).

**The inertness trap was checked before any retrieval delta was read**, because
`episode_contextual_chunks` mints no chunks for a single-segment body and
`packed_render` only compacts chunk-rendered items — so on single-line bodies the
cap is inert *by construction*, and a null would be uninterpretable. Witness that
the cap genuinely ran on this corpus: 30,768/64,055 bodies (48.0%) exceed the
4-segment window and mint chunks; per-item render chars fell 2,018,765 →
1,926,115 (mean 1147.0 → 1094.4) and the largest single render 7,039 → 4,011.

With the cap demonstrably live, it moved nothing: `packed_items_total` **1,760 in
both arms**, r@5 and r@10 identical to four decimals, **zero flips in either
direction**.

**The chat-lane Budget-drop pathology does not recur on code bodies.**
`budget_share_of_in_pool_unpacked` is **0.0118** (1 of 85) cap-OFF and **0.0**
cap-1200, against **1.00** (64/64) on the chat lane. The binding constraint here
is the **k=10 slot limit**, not the 8192-token budget: 176/180 questions pack
exactly 10 items, and the dominant drop reason is `rerank` (57 of 85), which
`admit_or_drop` emits only from the `items.len() >= output_limit` branch — never
the budget branch.

Per the plan's own conditional, this makes `pack_render_cap` a **chat-lane
footnote** and **Phase 2 drops in priority**. This is a real measured null, not
an artifact of an instrument that never fired.

## 3. The discarded first comparison — recorded, because it is the Phase 0 error again

The first pass at Phase 1c reported **MemPhant r@10 0.5056 vs BM25 0.8056** and
concluded the kill gate was tripped. **That conclusion was invalid**: the two
arms were not scored on the same construct.

- BM25's `hit_at_10` was over a plain **ranked top-10** (`returned_items: 10`) —
  no packing stage at all.
- MemPhant's `hit_at_10` was over the 10 items that **survived packing**
  (`packed_size` is exactly 0 or 10; histogram `{0: 4, 10: 176}`).
- MemPhant's own **fused ranked top-10** contains gold **147/180 = 0.8167**
  (`gold_fused_rank <= 10`), identical in both cap arms.

So a *packing* statistic was compared against a *retrieval* statistic, while the
gate it was tripping is worded "does not beat BM25 **on retrieval**". On the
like-for-stage comparison the paired result was 25 MemPhant-only vs 23
BM25-only — parity, not a 30-point loss.

This is the same failure mode Phase 0 spent the morning rescinding: a
mis-specified proxy about to retire a lane. It is recorded here so the discarded
number never resurfaces as evidence. To its credit the committed artifact
(`track_r_phase1_retrieval_summary.json`) recorded the honest stage
decomposition — `in_pool_unpacked_gold_rank_within_k: 56` — and explicitly left
the gate decision to the owner; the false conclusion lived only in the
narrative, which is precisely where such conclusions are hardest to catch.

## 4. Phase 1c corrected — one stage, one haystack

Fixing the stage asymmetry alone gave parity, which was still not an answer,
because a **second and opposite** asymmetry remained: MemPhant's candidates are
scoped to one attempt (median pool 124) while BM25 was ranking all 64,055 events.
Parity on a ~500×-smaller haystack is not a win.

MemPhant's actual scoping rule was read from the runner rather than assumed:
`bind_attempt_context()` (`scripts/code_lane_run_memphant.py:511`) binds
`scope_ref`/`actor_ref`/`agent_node_ref` to `code-lane:*:{attempt_id}`, and all
180 goldens have single-attempt provenance — so the haystack is exactly one
attempt. BM25 was then scoped identically (`--scope attempt`), with the match
verified empirically on all 180: scoped-BM25 `documents_searched` equals the
bound attempt's event count, and MemPhant `pool_size <= documents_searched` with
ratio mean 0.953 (median 124 vs 126). The residual asymmetry now runs **against
BM25**: MemPhant's pool is a lexically prefiltered subset (`websearch_to_tsquery`
+ `ts_rank_cd`, limit 200, `crates/memphant-store-postgres/src/store.rs:2148`) of
the same attempt, so the control searches the *larger* haystack and still wins.

Gold predicates verified identical (both grade through
`gate_common.provenance_hit`; all 180 goldens carry exactly one required span, so
`gold_fused_rank <= k` *is* `provenance_hit` at k). The query string is identical
by construction; the control's switch to the shared `retrieval_query()` seam was
proven inert — the re-run produced byte-identical evidence and an identical
per-question hit vector.

| arm | haystack | r@5 | r@10 |
|---|---|---:|---:|
| MemPhant fused rank | attempt (pool median 124) | 0.6278 | 0.8167 |
| **BM25 scoped** | attempt (median 126) | **0.8278** | **0.8944** |
| BM25 whole corpus | 64,055 events | 0.7611 | 0.8056 |
| MemPhant after packing | attempt | 0.4500 | 0.5056 |

Paired, MemPhant fused vs scoped BM25:

| k | both | MemPhant only | BM25 only | neither | McNemar exact p |
|---|---:|---:|---:|---:|---:|
| @10 | 133 | 14 | **28** | 5 | **0.0436** |
| @5 | 99 | 14 | **50** | 17 | **0.00001** |

(McNemar recomputed independently from the per-question vectors, not taken from
the run report.) Gold at rank 1: BM25 91/180, MemPhant 59/180.

By shape, scoped BM25 r@5/r@10 vs MemPhant fused: file-symbol-grounding
0.900/0.917 vs 0.567/0.833; state-churn 0.717/0.833 vs 0.650/0.783;
task-resumption 0.867/0.933 vs 0.667/0.833. BM25 leads every shape at both k.

**Kill gate: FIRES.** MemPhant does not beat BM25 on retrieval on this bank.
Per the plan, ownership question (d) defaults to **"Syndai keeps its tables until
the substrate wins"**, and the plan's own remedy applies: *the roadmap pivots to
closing the measured gap, not to migration or benchmarks.*

### The bank is not saturated, and the bar miss is not the cause

Whole-corpus BM25 leaves 35/180 goldens undefeated — 19.4 points of headroom in
which a MemPhant win could have been expressed. This is **not** the
SWE-ContextBench baseline-saturation failure mode.

The `with_distractors_ge_50pct` miss also does not inflate the control. On the
105 goldens **without** an adjudicated distractor, whole-corpus BM25 scores
0.8190 vs 0.7867 on the 75 **with** one — a 3.2pt gap, not a give-away subset.
The coverage miss therefore reads as a **threshold artifact of a
stronger-than-assumed identification gate**, not a defect. Scoped BM25 shows the
same ordering (0.9143 without / 0.8667 with). The threshold decision remains the
owner's; nothing in the lock was touched.

## 5. What this localizes — two $0-discovered targets

**(a) Ranking, worst at small k.** MemPhant's fused ranking trails lexical BM25
on the same attempt-scoped haystack, and the deficit is far larger at k=5
(0.628 vs 0.828) than at k=10 (0.817 vs 0.894), with gold at rank 1 on 59/180 vs
91/180. On code-lane questions — which are dense in identifiers, paths, and error
strings — lexical matching is currently the stronger signal, and fusion is
diluting it. Note this run used `--embed-model off`, so it measures the lexical
and fusion machinery, **not** dense embeddings; a dense arm is the obvious next
free probe and is not yet run.

**(b) Packing displaces gold it already ranked.** Gold reaches the candidate pool
on 176/180 (0.978) and the fused top-10 on 147/180, but survives into the packed
context only 91 times: **56 of 147 top-10 golds (38%) are displaced by packing**,
with 176/180 questions packing exactly 10 items and `rerank` — the
`items.len() >= output_limit` branch — the dominant drop reason. This is the same
"packing, not retrieval, is the bottleneck" conclusion the chat lane reached at
rung 7, now reproduced independently on code bodies by a different instrument.

Both are concrete, both were found for $0, and both sit upstream of anything
Phase 3 would have paid to measure.

## 6. Recommendations (owner decisions, not taken here)

1. **Do not launch Phase 3 as designed.** Its primary comparison is "best
   MemPhant arm vs BM25", and that comparison already resolves negatively for $0
   at the retrieval stage. Paying a reader-QA run to confirm a free negative
   spends the scarce resource (question exposure and credibility) on a question
   already answered. Revisit after (a) or (b) moves.
2. **Phase 2 drops in priority**, per §2 and the plan's own conditional. If it
   runs, it runs on the chat lane's own merit via the OR branch, which requires a
   decision-register entry naming who valued it and why.
3. **Decide the `with_distractors_ge_50pct` threshold.** The evidence says the
   floor mis-modelled a strong identification gate rather than that the bank is
   soft. Either amend the threshold with this rationale recorded, or accept a
   180-golden bank flagged `bar_passed: false`. The bank is usable for free
   diagnostics either way; no external claim may cite it until the 15-golden
   spot-check is reviewed.
4. **Next free probes, in order:** a dense-embedding arm (this run was
   lexical-only), and a packing-displacement fix measured against the 56 known
   displaced golds — a preregistered, already-localized target.

## Provenance

Commits on `accuracy-first`, none pushed: `0cf468eb` (Track R bar) → `bdc76b35`
(miner) → `acc7e225` (Track R lock) · `10ea21aa` (Track U prereg) → `b72d2082`
(extractor) → `1dddf0de` · `a2838bf1` (1b instrument) → `312c522a` ·
`27c00c95` → `d2f99e01` → `1b941a21` (three-arm run) · `b64fe651` → `db8629c9`
→ `e6e205b9` (scoped BM25 + comparison) · `58ded7fc` (off-worktree mirror of
irreplaceable gitignored inputs).

Artifacts: `docs/build-log/artifacts/track-r/track_r_phase1_retrieval_summary.json`,
`…/track_r_phase1c_scoped_bm25_comparison.json` (both committed, no third-party
bodies); per-question evidence under `…/track-r/phase1/` is gitignored.
Each MemPhant arm ingested 64,055 events + 1 isolation sentinel with
`compiled=64056`, `pending_jobs=0`, `dead_jobs=0`, 64,014 units after 42
exact-duplicate dedups — the worker fully drained, so no figure is a half-drain
artifact. All scratch databases auto-dropped.
