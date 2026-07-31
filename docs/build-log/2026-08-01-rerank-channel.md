# The rerank loss channel — what it actually is, and what fixes it

Date: 2026-08-01 · Branch: `af-rerank` (worktree `Memphant-af-rerank`, cut from
`accuracy-first` @ `d01affad`)
Cost: **$0 paid API spend.** No reader, no judge, no hosted reranker, no API key.
The only model in this document is a local ONNX cross-encoder out of the on-disk
fastembed cache.

> # DIAGNOSTIC — NOT PROMOTION-GRADE
>
> Measured on the Track R **paraphrase** bank, which fails its own preregistered
> headline leakage criterion (concentration **2.0180** vs a bar of ≤1.50,
> `benchmarks/data/track_r_paraphrase_golden.lock.json`, `bar_passed: false`).
> Used deliberately and with the failure declared, on the standing finding that
> the bar sits below the achievable floor of 1.79×. Retrieval and packing only —
> **no reader**, so nothing here says what an answer-generating model would do.

---

## Headline

**The "rerank" loss channel is not a reranker.** `RecallDropReason::Rerank` is
the label the packing loop attaches to *any* candidate it meets after the output
slots are already full. There is exactly one emitter
(`crates/memphant-core/src/lib.rs:9170`), it sits under
`if acc.items.len() >= ctx.output_limit`, and it fires whether or not a reranker
was ever installed. On every arm in the W0.2 trunk run **no reranker was
installed at all** — `MEMPHANT_CROSS_RERANK` is default-OFF, `gate_runtime.Server`
*pops* it out of the child environment, and the code-lane harness had no flag to
set it.

So "rerank is the dominant recoverable loss channel, 4× the next largest" is a
true statement about a **mislabelled bucket**. Read correctly it says: *on 50 of
180 questions the gold unit was retrieved into the candidate pool but the fusion
stage ranked it below the top 10.* That is a ranking-quality deficit in
retrieval, not a defect in a rerank stage — and it is the right thing to attack,
because a real reranker is precisely the instrument that fixes it.

---

## 1. The diagnosis, with numbers

### 1.1 The complete miss decomposition, recomputed from the banked per-question rows

Source: `~/.memphant-private/track-r-paraphrase/run-trunk/bm25code_dense-provenance.json`
(the `bm25code_dense` arm of `docs/build-log/2026-07-31-w02-trunk-arms.md`, HEAD
`4a39ce5f`). n = 180.

| bucket | n | what it means |
|---|---:|---|
| hit | 106 | gold span reached the packed top-10 |
| absent from pool | 11 | gold never entered the candidate pool |
| `budget` | 7 | gold reached the pack and was evicted on token budget |
| **`Rerank`** | **50** | **gold was scanned by packing and never admitted** |
| `not_in_dropped_items` | 6 | gold appears in no drop record |

`dropped_items` is **54 on every single question**, without exception. That is
the tell: the packing scan window is 64 (`recall_pool_depth`), ten slots are
filled, and the other 54 scanned candidates are each recorded as a `Rerank`
drop. The bucket is a *scan-window arithmetic constant*, not a scoring event.

The 6 `not_in_dropped_items` rows split further, and the W0.2 log's "render = 6"
column is wrong about them:

- **5** sit at fused rank **70, 75, 82, 93, 131** — past the 64-deep scan
  window, so packing never saw them and no drop record exists.
- **1** sits at fused rank 5 with `gold_units_packed = 1` — the gold unit took a
  slot and rendered without its span. **The true render bucket on this arm is 1,
  not 6.** The render channel is not "nearly closed"; on the best arm it is
  closed to a single question.

### 1.2 The rank distribution of the 50 — this settles cut-depth vs scoring

| gold's fused rank | `bm25code_dense` | `bm25code_off` |
|---|---:|---:|
| 1–10 | **0** | **0** |
| 11–16 | 13 | 20 |
| 17–32 | 25 | 28 |
| 33–64 | 12 | 21 |
| 65+ | 0 (by construction — outside the scan) | 0 |
| median | **22** | **24** |
| max | 63 | 60 |

**Not one of the 50 has the gold at rank ≤ 10.** Packing is faithful to the
order it is handed: on the 106 hits the gold's fused rank has median 3 and
maximum 12. The selection stage is doing its job. The order it is given is the
problem.

**This is not a cut-depth problem.** Only 13 of 50 sit in the 11–16 band, and
reaching them means moving the metric from @10 to @16 rather than fixing
anything. The other 37 are at rank 17–63, where no defensible cut reaches them.

### 1.3 The fused recall@k curve — the headroom a reranker can address

Computed from `gold_fused_rank` on the same banked rows, so it is the exact
ceiling of any pure re-ordering of the existing pool:

| k | `bm25code_dense` fused recall@k | `bm25code_off` |
|---:|---:|---:|
| 5 | 0.4333 (78) | 0.3333 (60) |
| 10 | 0.6278 (113) | 0.4944 (89) |
| 16 | 0.7056 (127) | 0.6167 (111) |
| 32 | 0.8444 (152) | 0.7722 (139) |
| **64** | **0.9111 (164)** | 0.8889 (160) |
| in pool at any rank | 0.9389 (169) | 0.9389 (169) |

A perfect reordering of the **64-deep head** would put **164 of 180** golds in
the top 10, against today's packed 106. **The recoverable headroom is +58
questions (+0.322)** and it is entirely a ranking-quality question. The default
`candidate_limit` of 64 is well chosen: it already covers 164 of the 169 in-pool
golds, and raising it to the full ~124-candidate pool would buy at most 5 more.

### 1.4 Systematic features of the missed golds — there are almost none

| question type | hit | `Rerank` miss | absent | budget | other |
|---|---:|---:|---:|---:|---:|
| file-symbol-grounding | 36 | 18 | 3 | 3 | 0 |
| state-churn | 34 | 16 | 3 | 2 | 5 |
| task-resumption | 36 | 16 | 5 | 2 | 1 |

The channel is **uniform across all three question shapes** — no shape-specific
fix is indicated. Pool size is also indistinguishable (median 124.5 on hits vs
123.0 on misses). The one weak signal: misses carry a median of **1**
gold-bearing pool unit against **2** on hits, i.e. a question with only one
correct unit has fewer chances to land a slot. Median gold fused score is 0.0483
on misses vs 0.06156 on hits — a continuum, not a cliff.

### 1.5 The two known-broken ranking components do NOT feed this

The program has twice found a ranking component scoring on noise. Checked, and
neither is the cause here:

- **`exact_score`** (`lib.rs:10726`) has already been repaired by `3fc4eede` —
  it now divides by the token count of the unit's `fact_key`, not by anything
  carrying a scope UUID. Independently, these arms run with
  `MEMPHANT_FACT_EXTRACTION=0`, so episodic units carry no `fact_key` at all and
  the function returns `0.0` on the first line for every candidate. **The Exact
  channel is inert on this lane.**
- **`temporal_score`** (`lib.rs:10920`) returns `1.0` only for an *active
  semantic* unit under a `current`/`latest`/`now` query, or for a unit inside an
  explicit query date window. This corpus is 100% **episodic** units and the
  temporal-grounding flag is off, so both branches are unreachable. **The
  Temporal channel is inert on this lane.**

What is actually ranking is the Lexical channel (`bm25-code`) and the Vector
channel (`bge-small-en-v1.5`), combined by weighted RRF and then scaled by
`decay.retrievability`. Fix (a) — repair a broken scoring component — has no
target. The loss is genuine semantic-ranking quality, which is fix (c).

---

## 2. What changed

### 2.1 The mechanism was already in the codebase and had no way to reach this lane

The W8 cross-encoder seam is complete and shipped: the `CrossReranker` trait
(`memphant-core/src/lib.rs:421`), `FastEmbedCrossReranker` over
`BAAI/bge-reranker-base` (`memphant-runtime/src/embeddings.rs:369`), the
`MEMPHANT_CROSS_RERANK` / `MEMPHANT_RERANKER` env wiring
(`memphant-runtime/src/lib.rs:273`), a fail-open contract, and a
`CrossRerankTrace` block on every recall trace. What was missing was a way to
select it from the code lane. Three small changes:

1. **`scripts/code_lane_run_memphant.py`** — `--cross-rerank`, `--reranker`
   (deliberately restricted to the local `fastembed`/`byo` arms; the hosted
   `voyage`/`cohere` arms are *not* offered, because this lane runs at $0), and
   `--rerank-candidate-limit`. Same select-here-and-nowhere-else contract as
   `--pack-render-cap`.
2. **`scripts/gate_runtime.py`** — threads `rerank_candidate_limit`, and pops
   `MEMPHANT_RERANK_CANDIDATE_LIMIT` from the child environment so an ambient
   value can never leak into an arm.
3. **`RecallCandidateTrace.cross_rerank_rank`** (`memphant-types`, stamped in
   `memphant-core` right after the rerank stage). Without it a post-rerank miss
   cannot be attributed: "the reranker never saw the gold" (raise
   `candidate_limit`) and "the reranker saw it and still ranked it below the
   cut" (model quality) are different defects with different fixes, and
   `fused_rank` distinguishes neither.

The harness now also emits a **`cross_rerank_liveness`** block in every
provenance report, read off the server's own traces rather than off the flag the
harness passed — the seam fails *open* to the pre-rerank order, so an all-`error`
arm is byte-identical to the control while claiming to be a reranked arm.

RESULTS_PLACEHOLDER

