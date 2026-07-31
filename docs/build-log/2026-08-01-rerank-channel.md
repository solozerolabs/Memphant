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

### 2.2 A base defect that had to be fixed before any arm could run

`accuracy-first` @ `d01affad` carries the worker tick-honesty change, which made
the drain line print `drain completed=N failed=N retried=N deferred=N`
(`crates/memphant-worker/src/main.rs:124`). `gate_runtime.drain_worker` matched
only the bare `completed=N` form and raised *"worker drain completion output is
malformed"* before any probe ran. Fixed on this branch (`0ef2e464`), with the
three counts parsed as an optional group so an older binary still parses, and
with **`failed` asserted rather than discarded**: the worker prints that count
precisely so "drained nothing" stays distinguishable from "failed everything",
and a partially compiled corpus silently inflates the absent-from-pool bucket of
every retrieval measurement taken against it — which is exactly how a ranking
problem gets misread as a retrieval problem.

---

## 3. The result — the mechanism fired, and it made things worse

Two arms, both `bm25-code + dense` (the best arm in the trunk ladder), differing
in exactly one variable. `rerank_bge` installs `BAAI/bge-reranker-base` over the
64-deep fused head; `rerank_off` is the same run with the stage absent.

### 3.1 Mechanism liveness — proven, not assumed

Read off the server's own traces (`cross_rerank_liveness` in the provenance
report), never off the flag the harness passed. This matters more than usual
here: the cross-rerank seam **fails open to the pre-rerank order**, so an arm
whose reranker errored on every call is byte-identical to the control while
still calling itself a reranked arm.

| field | `rerank_bge` | `rerank_off` (negative control) |
|---|---|---|
| queries | 180 | 180 |
| **queries carrying a `cross_rerank` trace** | **180 / 180** | **0 / 180** |
| **failure histogram** | **`{none: 180}`** — zero fail-open events | `{}` |
| provider / model | `fastembed` / `fastembed:bge-reranker-base` | `null` |
| mean candidates in the scored head | 60.8 | — |
| docs scored | 10,944 | 0 |

The stage ran on every question and succeeded on every question, and the control
shows the instrument reads zero when the stage is absent. This is a **live**
pass, so its number means what it says.

### 3.2 The restricted ingest is inert — checked, not asserted

Both arms ingest the 155 gold-referenced attempts rather than all 495.
`bind_attempt_context` gives each attempt its own subject/scope/actor/agent_node
and recall binds to that context, so a non-gold attempt's units cannot enter any
golden's pool. The prediction is that the fused stage is then **bit-identical**
to the banked full-corpus arm. It is:

> **`gold_fused_rank` mismatches between the banked 495-attempt
> `bm25code_dense` arm and this 155-attempt arm: 0 of 180.**

Fused@10 is **113** in both. And the `rerank_off` arm, run end-to-end at this
branch's HEAD on 155 attempts, reproduces the banked 495-attempt arm **per
question, not merely in aggregate**:

| | mine (155 attempts, `af-rerank`) | banked (495 attempts, `4a39ce5f`) | per-question mismatches |
|---|---:|---:|---:|
| packed hit@5 | 73 | 73 | **0 / 180** |
| packed hit@10 | **106** | **106** | **0 / 180** |
| miss decomposition (absent / budget / `Rerank` / other) | 11 / 7 / 50 / 6 | 11 / 7 / 50 / 6 | — |

That check simultaneously validates the ingest restriction *and* the retrieval
lineage across two worktrees and two HEADs. It is stronger evidence than a
commit-ancestry assertion, because it compares outputs rather than provenance.

### 3.3 The decisive contrast — fused order vs cross-encoder order, within one arm

This is the cleanest comparison available anywhere in the program: **the same
recall, the same pool, the same 180 questions, one arm, two orderings**. No
lineage, corpus, binary, or run-to-run confound can enter it, because both
orderings come out of the same trace. `cross_rerank_rank` is what makes it
computable.

| | gold in top-10 |
|---|---:|
| existing weighted-RRF **fused** order | **113** |
| **`bge-reranker-base`** order | **79** |

| b (reranker only) | c (fusion only) | n_d | ψ | Δ | exact McNemar p | MDE@80% | power |
|---:|---:|---:|---:|---:|---:|---:|---:|
| **12** | **46** | **58** | 0.3222 | **−0.1889** | **8.22e-06** | 0.1215 | **0.996** |

**`bge-reranker-base` is significantly worse than the ranking it replaces.** It
promoted the gold into the top 10 on 12 questions and demoted it out on 46. The
demoted golds land at a median rank of 21.5 (max 51). n_d = 58 is an order of
magnitude above the n_d ≥ 6 structural floor and power against the realized
effect is 0.996 — **this is a powered negative, not an n_d artifact.**

### 3.4 The packed-stage endpoint

Both arms at the same HEAD, same tree, same corpus, same bank.

| arm | packed hits@10 | packed hits@5 |
|---|---:|---:|
| `rerank_off` | **106** (0.5889) | 73 (0.4056) |
| `rerank_bge` | **95** (0.5278) | 71 (0.3944) |

| stage | b (bge) | c (off) | n_d | ψ | Δ | exact p | MDE@80% | power |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| packed@10 | 16 | 27 | 43 | 0.2389 | −0.0611 | 0.126 | 0.1048 | 0.337 |
| packed@5 | 26 | 28 | 54 | 0.3000 | −0.0111 | 0.892 | 0.1172 | 0.044 |

The reranked arm's own miss decomposition shows where the damage lands:
`gold_rank_within_k_unpacked` rises from **8 to 32** — i.e. on 32 questions the
gold sat in the *fused* top-10 and still failed to be packed, because the
reranker had moved it out. The `budget` bucket collapses 7 → 1 for the same
reason: golds that used to reach the pack and lose on tokens no longer reach it
at all.

The endpoint is **directionally negative and underpowered** — n_d = 43 clears the
structural floor so both rows are measurements, but at ψ = 0.24 this lane cannot
resolve a 6-point effect (MDE 10.5pt). **The endpoint alone would be an honest
"cannot tell".** What settles the question is §3.3, where the same mechanism is
measured against the thing it replaces on identical inputs and loses at p = 8e-06.

### 3.5 Latency — the fix is not deployable even if it had worked

Measured from each recall's own trace, on this host, at the shipped
`candidate_limit` of 64:

| | `cross_rerank_ms` |
|---|---:|
| p50 | **22,166 ms** |
| p95 | 34,399 ms |
| max | 53,593 ms |

**Contention caveat, stated rather than buried:** `loadavg1` sat near 120–140
across this window, driven by four other worktrees' servers and concurrent
rustc. These figures are an **upper bound**, not a clean measurement. The
independent uncontended figure on this same host is **~1,460 ms median**
(1140/1871/1448/1472 at 64×512, R6 lane) — which is itself **3.3× the 449 ms
figure recorded in older docs, and breaches a 1.5 s ceiling on two of four
samples.** The 449 ms number should not be cited again.

Against the HTTP-boundary SLO of **p50 32.59 ms / p95 37.18 ms**
(`artifacts/c1-episodic/slo-bar1-http-provenance.json`), the uncontended
cross-encoder is **~45× the p50 budget** and the contended one ~680×. A local
CPU cross-encoder at pool 64 cannot go on the hot path at any accuracy.

### 3.6 Why it probably fails here, and what is *not* concluded

One mechanical contributor is measurable: bge-reranker-base runs at
`max_length = 512` tokens under `CrossRerankGranularity::UnitBody`, and **35.3%
of the bodies it scores exceed the ~2,048-char wall** (median 1,466, p90 4,087,
max 8,560). A third of every long candidate is truncated away before scoring,
and on trajectory bodies the discriminating span is often not in the first 512
tokens. Beyond that, this is a corpus of agent transcripts and tool output being
scored by an encoder trained on natural-language web/QA passages, against
paraphrased queries with identifiers withheld — while the order it is replacing
is BM25-code + dense, both of which the trunk ladder already showed to be strong
on exactly this material.

**What is NOT concluded:** that reranking cannot help this lane. §1.3 shows the
headroom is real and large (+58 questions from a perfect reordering of the head
we already retrieve). What is concluded is narrower and firm: **`bge-reranker-base`
is not the instrument that captures it, and it is not close.**

---

## 4. Verdict, and the owner's "turn it on" instruction

The standing instruction is *"if things work, just turn them on and default
them."* This did not work, so nothing is defaulted:

- `MEMPHANT_CROSS_RERANK` **stays default-OFF**. The change here is that the
  code lane can now select it, and that a reranked arm can now be told apart
  from an inert one.
- **`RecallDropReason::Rerank` should be renamed.** It names a mechanism that
  does not run and has cost this program a wrong headline finding at least once.
  `OutputLimit` (or `ScanCutoff`) is what it means. Not done here — it is a
  public enum on three generated schema artifacts and belongs in its own change.
- **Register item: "the reranker is the dominant loss channel" must be
  reworded**, not deleted. The loss is real and it is the largest recoverable
  pool on the coding lane; it is a **fusion-ranking** loss, and it lives at fused
  ranks 11–63.

### What could not be resolved

1. **The endpoint contrast is underpowered.** At n = 180 and ψ = 0.24 the packed
   stage resolves 10.5pt; the observed −6.1pt is inside that. The powered result
   is the ordering contrast, which is one stage upstream of the endpoint.
2. **One reranker, one corpus, one granularity.** `ContextualChunks` granularity
   was not tried; on this episodic corpus it falls back to the body for
   candidates with no chunks, so it is unlikely to be the answer, but it is
   untested. A smaller/faster BYO model (ms-marco-MiniLM-L6-int8) is wired
   (`MEMPHANT_RERANKER=byo`) and untested here.
3. **Hosted rerankers were not tried, deliberately.** Voyage `rerank-2.5` and
   Cohere v4-pro lead the chat-lane bench by a wide margin, but they are paid
   and this lane is $0. That is a budget boundary, not a measurement.
4. **No chat-lane (LME-S) non-regression was run.** Nothing regressed to check:
   the default path is unchanged, and the only shipped behaviour change is an
   added optional trace field.
5. **No reader.** Everything here is retrieval and packing.

## 5. Lineage

| field | value |
|---|---|
| branch / worktree | `af-rerank` / `Memphant-af-rerank`, cut from `accuracy-first` @ `d01affad` |
| **both arms' `runtime_identity.git_head`** | **`88b6e5f8`** — identical |
| **both arms' `server_sha256`** | **`15d11d19fac42608…`** — byte-identical binary |
| drain, both arms | `done_jobs=21630`, `pending_jobs=0`, `dead_jobs=0`, 13 dedups |
| binaries | sha256 of server/worker/cli stamped in `run-rerank/binaries.sha256` |
| golden bank | `4aed8e99…4326`, 180 goldens, lock verified by the runner |
| corpus | `c008142e…9669` |
| harness env | `MEMPHANT_FACT_EXTRACTION=0` (see §2 of the trunk-arms log — trunk's default-ON extraction breaks full-corpus drain) |
| retrieval-lineage cross-check | **0/180 `gold_fused_rank` mismatches** against the banked `4a39ce5f` arm (§3.2) — the strongest lineage evidence in this document, because it compares outputs rather than commits |
| paid spend | **$0** |

**One lineage blemish, stated rather than buried.** `rerank_bge` recorded
`tracked_dirty: false`; `rerank_off` recorded `tracked_dirty: true`, because
this build log was being written while that arm ran. The **binary sha256s are
byte-identical across both arms**, so no engine behaviour differed, and the
empirical proof is stronger than the provenance argument: `rerank_off`
reproduces the banked arm on **all 180 questions** at both k (§3.2). The dirty
flag covers a markdown file no binary reads. It is recorded because the rule is
to record it, not because it is load-bearing.

**Also for the record: the first `rerank_off` attempt was destroyed at its final
step** by `IsADirectoryError` in `repository_identity` — `.gitignore` carried
`.fastembed_cache/` with a trailing slash, which does not match a *symlink* to a
cache directory, so the link got tracked and the identity function tried to
sha256 a directory. It fires **after** every recall has executed. Fixed in
`88b6e5f8`; the arm was re-run from scratch.

## 6. Artifacts

Committed: `docs/build-log/artifacts/track-r-rerank/rerank-channel.json`, schema
`memphant.eval.track-r-rerank-channel.v1` — every contrast as a full 2×2 with
`b`/`c`/`n_d`/realized ψ/Δ/exact p/MDE/power and an explicit `is_a_measurement`
flag against the n_d ≥ 6 floor, both arms' liveness blocks, the lineage block,
the contention-flagged latency block, and the per-question `gold_fused_rank` /
`gold_cross_rerank_rank` vectors. It carries ids, ranks and booleans only — no
corpus body and no question text.

Gitignored, at `~/.memphant-private/track-r-paraphrase/run-rerank/`: both arms'
evidence JSONL and provenance reports (with `cross_rerank_liveness`,
`gold_cross_rerank_rank` per question, and the full drain block), the server
logs carrying the per-query `cross_rerank_ms` lines, and the binary hashes.


