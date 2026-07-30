# Coding lane — the first banked accuracy win (2026-07-30)

Cost: **$0**. No reader, no judge, no paid model call, no new corpus.
Instrument: the 180-golden Track R bank (`6f549daa…`), attempt-scoped haystack.
Supersedes nothing; extends `docs/build-log/2026-07-30-phase1-golden-banks-and-retrieval-probe.md`.

**No checkbox, default, cutover, deployment, or SOTA claim moves.** Nothing here
is publishable: the Track R spot-check remains `emitted_pending_owner_review`,
and §4 bounds what these numbers can mean.

## Result

Two independently developed fixes, measured together for the first time:

| stage | r@5 | r@10 |
|---|---:|---:|
| fused (ranking) | 0.9500 (171/180) | **0.9611 (173/180)** |
| packed (what reaches a reader) | 0.7278 (131/180) | **0.7722 (139/180)** |

Against the committed baselines, all paired exact McNemar, all recomputed
independently from the per-question vectors:

| comparison | k | gains | losses | exact p |
|---|---|---:|---:|---:|
| combined packed vs **original packed (91)** | @10 | 49 | 1 | **9.06e-14** |
| combined packed vs **packing-fix-only (113)** | @10 | 28 | 2 | **8.68e-07** |
| combined packed vs **retrieval-fix-only (97)** | @10 | 43 | 1 | **5.12e-12** |
| combined **fused** vs **scoped BM25 control** | @10 | 15 | 3 | **0.00754** |
| combined **fused** vs **scoped BM25 control** | @5 | 24 | 2 | **1.05e-05** |

Served-path recall went **91 → 139 of 180**. This is the first banked accuracy
win of the campaign, and the first time MemPhant has beaten a neutral
deterministic control on the coding lane at either stage.

## The fixes

**Retrieval.** The final lexical score was **Jaccard token-set overlap**
(`token_set_overlap_text_score`, `memphant-core/src/lib.rs:10645`) —
`intersection / union`: no term frequency, no IDF, and a union denominator that
penalises long documents *monotonically*, the opposite of BM25's calibrated `b`.
Compounded by `to_tsvector('english', body)` (`bootstrap:367`) stemming and
stopword-stripping code, and a tokenizer splitting `snake_case` but not
`camelCase`. Replaced with textbook BM25 (k1=1.2, b=0.75) plus code-aware
tokenization and ingest-time identifier expansion.

**Attribution matters here and is easy to overstate.** Implementing BM25 alone
reached fused 0.8722/0.9056 — a clean **null** against the BM25 control
(p=0.727 @10), exactly as it must, since we then *are* the control's algorithm.
**The entire win over the control comes from code-aware tokenization**, not from
BM25. Saying "we beat BM25" without that sentence would be close to circular.

**Packing.** `admit_or_drop`'s output-full branch let a candidate at fused rank
11–64 **evict an already-packed top-10 item** when it scored higher under
`packing_relevance_score` — an order different from the one the pack was handed.
Worst case observed: a gold at fused rank 3 evicted. Now the established order
wins and late candidates are dropped, never swapped in; `rank_based_ordering_active`
existed only to disable that contest and is deleted with it. Budget-driven
replacement is untouched — that one is a genuine substitution.

**Dense embeddings did not work on this lane.** Alone they reach only a null vs
BM25 (p=0.200 @5 / 1.000 @10); stacked on `bm25-code` they are −10/+3 at @5. The
best configuration uses **no embeddings at all**. This kills the hybrid-fusion
assumption for the coding lane and should be re-tested on the W0 paraphrase
variant before being generalised — a lexically biased bank is exactly where
dense would be expected to underperform.

## The interaction — super-additive, and why

Packed hits of 180, full 2×2:

| | old packer | fixed packer |
|---|---:|---:|
| **old retrieval** | 91 | 113 (+22) |
| **fixed retrieval** | 97 (+6) | **139** |

Additive prediction 119; actual **139**; interaction **+20 questions** at k=10
(+33 at k=5).

The mechanism is the point: retrieval-only gained just **6** packed questions
despite gaining 26 fused ones, because the old packer threw the ranking win away
— 79 in-pool golds unpacked, 44 of them `rerank`-evicted. Better ranking simply
produced better candidates for the packer to discard. Neither fix could show its
value while the other was broken, and **either one measured alone understates
itself by a factor of three or more.**

This is a general warning for this program: staged fixes measured against an
unfixed downstream stage systematically under-report. Fused was identical across
both packing arms (173/173 fixed-retrieval, 147/147 unfixed), confirming neither
stage bled into the other.

## What remains — 34 questions, and 30 of them are one defect

Fused→packed gap at k=10: **173 → 139**. Composition of the 41 total misses:

| cause | n | assessment |
|---|---:|---|
| **render loss** | **30** | gold unit *is* in a packed slot, span not rendered |
| budget | 4 | genuine budget pressure |
| rerank | 3 | all at fused rank ≥ 11 — genuinely below the cut |
| absent from pool | 4 | policy ceiling, see below |

**Render loss is now the whole of the next target.** `packed_render` charges each
chunk block its provenance header *on top of* its body, so full coverage always
costs more than the whole body and an uncapped chunked item can never emit all of
itself — it drops chunks while charging nearly the whole-body price, and the gold
span goes with them. The one-line fix was implemented, measured, and
**deliberately reverted**: it raises per-item cost, which is precisely wrong for
the chat lane's budget-bound pack (64/64 Budget drops there), and it breaks
`sibling_gather`'s invariant test. It needs its own paired chat-lane arm before
it can land. Closing it would take packed from 139 toward the 173 ceiling.

**The 4 pool misses are a policy ceiling, not a retrieval one.** All are
`pool_size=0` `below_trust_floor` drops from benign queries tripping
`high_risk_action_query` (`create`+`registry`, `script`+`library`,
`create`+`claim`, `script`+`support`). A safety guard was **not** weakened for a
benchmark number, and it should not be.

## Cost and non-regression

- **Chat lane, retrieval fix:** LME-S n=120, 111 graded, **66 → 75, p=0.0117** —
  an improvement on an independent corpus, which is the main evidence that the
  Jaccard→BM25 fix is a genuine substrate improvement rather than bank-fitting.
- **Chat lane, packing fix:** inert, not merely neutral. LME-S packs 2–9 items
  and never reaches k=10, so the branch is mechanically unreachable; packed
  context byte-identical on all 178 questions, 0 flips.
- **Migration**, measured on 64,013 real bodies: `english`→`simple` is a
  drop+add of a generated column plus a GIN rebuild — **8.0 s under ACCESS
  EXCLUSIVE**, table 128.9 → 149.6 MB (**+16.0%**), scaling linearly with rows.
- Worker fully drained on every arm (`compiled=64056`, `pending_jobs=0`,
  `dead_jobs=0`), all scratch DBs dropped.

## 4. What these numbers cannot be used for

**They are not production-representative, and the magnitude is inflated by the
instrument.** Track R's questions carry **0.396** of their tokens into the target
event versus **0.094** into a random non-target in the same attempt — 4.2×
concentration — with 105/180 questions narrowing to exactly one event, because
the preregistered identification gate was satisfied by copying identifiers out of
the target. Third-party CLARC measures BM25 at R@10 ≈ 18 on genuine NL→code
queries against our control's 0.8944.

So: we fixed lexical scoring and won large on a bank built to reward lexical
matching. The **defect fix is corroborated** off-bank by the chat lane; the
**magnitude is not**. Per the program spec, W0's paraphrase variant is the
instrument that decides ownership question (d), and no promotion may be taken
against this bank. The honest summary is that two real defects were found and
fixed, and that we do not yet know their production size.

## Provenance

Commits on `accuracy-first`, none pushed: `03fa1266` (packing) · `6f785957` →
`09781936` (retrieval) · `3da3e05a` (combined measurement). Artifacts:
`docs/build-log/artifacts/track-r/track_r_phase1{d,r,e}_*.json` (committed,
derived, no third-party bodies); per-question outputs gitignored.

Two frozen sha256 pins collided with these edits and were **parked, not
restamped**: the v5 campaign census (`memphant-core`) and the SWE-ContextBench
tranche-1 rehearsal (`gate_runtime.py`). Both records describe closed or parked
work; re-pinning would restamp them with the identity of code they never
exercised. Each requires a fresh census/rehearsal on resume. Full suite: 1021
passed, 14 skipped.
