# Reranker latency-cut spike — 2026-07-22

> **SUPERSEDED, 2026-08-01 — the 449 ms figure and the "3× headroom" claim are
> RETRACTED. Do not cite either.**
>
> Re-measured on 2026-08-01 by the same `#[ignore]`d test in this document
> (`rerank_byo_model_latency_matrix`), same model directory, same
> `--release` profile, same host, four consecutive samples at the same
> **64 × 512** configuration:
>
> | sample | 1 | 2 | 3 | 4 | median |
> |---|---:|---:|---:|---:|---:|
> | `elapsed_ms` | 1140 | 1871 | 1448 | 1472 | **~1460** |
>
> Against the pre-registered **1500 ms** ceiling that is **0.97× headroom, not
> 3×**, and **two of four samples breach the ceiling outright**. The recorded
> 449 ms is not reproducible here. The honest reading is that MiniLM-L6-int8 at
> the full 64 × 512 pool sits *on* the ceiling on this machine, not comfortably
> under it — and this matrix is **body granularity on synthetic ~1.5 KB docs**,
> the cheapest case. Under `MEMPHANT_RERANK_GRANULARITY=chunk` each candidate
> flattens into several scored docs, so the real arm can only be slower.
>
> Two caveats kept, because they cut both ways: the host was under concurrent
> load from sibling worktrees when re-measured, and a single-run matrix is a
> small sample either way. Neither rescues a 3× headroom claim — the point of
> the retraction is that **the margin was never measured with enough care to
> carry a spend decision**, and a figure quoted as 3× that measures 0.97× is
> exactly the kind of number that propagates into one.
>
> Authoritative replacement: the per-question `cross_rerank_ms` distribution
> from a real arm on the real corpus, recorded in
> `docs/build-log/artifacts/p1-c2-killgate/killgate-b/verdict-b.json` and
> discussed in `docs/build-log/2026-08-01-r6-docs-decision.md` §K.
>
> What still stands from this spike: the *model-swap* conclusion (bge-base is
> the wrong model, MiniLM-L6-int8 is ~an order of magnitude faster), the
> licence findings, and the fastembed/ort knob map. It is the absolute number
> and the headroom claim that do not survive.

The cross-encoder rerank seam is the campaign's largest single QA lever (+0.158,
`2026-07-12-r15-rank-compression.md`) but was **latency-retired at 12.9–13.6 s/query**
(9× the pre-registered 1.5 s p95 ceiling), so it ships flag-gated, default OFF.
This spike asked: can the latency be cut 4–8× to fit the ceiling? Owner priority
**accuracy > cost > speed**, self-host, Apache/MIT.

## Verdict

**Yes — decisively, via a model swap, not tuning.** The 13 s is not a fundamental
limit; it is the wrong model. `bge-reranker-base` (278M params) is overkill.
Swapping to **`ms-marco-MiniLM-L-6-v2` int8 ONNX (22M params, Apache-2.0)** reranks
the **full 64-candidate pool at 512 tokens in 449 ms** on this CPU — **~13× faster**,
under the ceiling with 3× headroom, no candidate cut, no truncation — at comparable
BEIR accuracy (MiniLM-L12 52.0 vs bge-base 51.6; L6 ~1 pt below L12).

> **[2026-08-01] The 449 ms and the "3× headroom" in the paragraph above are
> RETRACTED — see the banner at the top of this file. Re-measurement gives a
> median ~1460 ms at 64 × 512, i.e. 0.97× the ceiling, with 2 of 4 samples over
> it. The model-swap conclusion stands; the margin does not.**

## Web research (2026 landscape, verified)

Deep-research (`memphant-reranker-landscape-2026-07` memory) + targeted searches +
the owner's `awesome-rerankers`/FlashRank pointers converged:

- **Voyage `rerank-2.5` is still the latest** (no rerank-3); our `api_reranking.rs`
  is current. `rerank-2.5-lite` is the latency variant.
- **The fast, Apache/MIT, CPU-friendly cross-encoders**: `ms-marco-MiniLM-L6/L12`
  (22M/33M, Apache-2.0), FlashRank's `ms-marco-TinyBERT-L-2` (~4MB), `mxbai-rerank-
  base-v2` (0.5B). Jina rerankers are the accuracy leaders but **CC-BY-NC (non-
  commercial → disqualified)**. `bge-reranker-v2-m3` (568M) is *bigger* than
  bge-base, not faster.
- **INT8 ONNX ≈ 1.75–3× on CPU**; Sentence-Transformers v4.1 ONNX backend ≈ 2–3×.
- Newer efficiency architectures (CROSS-JEM "4× lower latency", Set-Encoder) exist
  but lack drop-in ONNX; MiniLM-L6-int8 is the pragmatic pick today.

## fastembed / ort knob map (5.17.2 / ort 2.0.0-rc.12)

- fastembed's `RerankerModel` enum has only 4 models (bge-base, bge-v2-m3, two
  Jina) — **no MiniLM, no quantized reranker**. All hardcode full-precision
  `model.onnx`.
- **The escape hatch: `TextRerank::try_new_from_user_defined(OnnxSource::File, TokenizerFiles)`**
  loads ANY local ONNX + tokenizer *without* an ort dependency and without an enum
  entry. This is how the MiniLM int8 arm is wired.
- fastembed hardcodes ONNX graph-opt Level3 (already near-max) and passes through
  only `execution_providers` + `intra_threads`. Inter-op threads / CoreML / CUDA
  need dropping to ort directly (CoreML is Mac-dev-only; CI is Linux) — not pursued.

## Measured latency (this Mac, cached models, synthetic 64×~1.5 KB docs)

`crates/memphant-runtime/src/embeddings.rs` — `rerank_real_model_latency_matrix`
(bge-base) + `rerank_byo_model_latency_matrix` (BYO), both `#[ignore]`d,
`MEMPHANT_RERANK_SMOKE=1` / `MEMPHANT_RERANK_BYO_DIR`.

| candidates × max_len | bge-reranker-base | ms-marco-MiniLM-L6 int8 | speedup |
|---|---|---|---|
| **64 × 512** (prod default) | **5954 ms** | **449 ms** | **13.3×** |
| 32 × 512 | 3024 ms | — | |
| 24 × 512 | 2166 ms | — | |
| 16 × 512 | 1420 ms | — | |
| 32 × 256 | 2041 ms | 188 ms | 10.9× |
| 24 × 256 | 1556 ms | 130 ms | 12.0× |
| 16 × 256 | 1039 ms | 89 ms | 11.7× |
| 32 × 128 | 1146 ms | — | |
| 24 × 128 | 857 ms | — | |

Notes: the local bge-base baseline (5954 ms) is faster than the July 12.9–13.6 s
figure — shorter synthetic docs / a faster machine — but the *relative* levers hold.
**Tuning bge-base alone** can reach the ceiling (16×512 = 1420 ms, 32×128 = 1146 ms)
but each option sacrifices either candidate depth or context; **the model swap needs
neither** and keeps full 64×512.

## Retrieval accuracy (free, LME-S, n=20 seed=3, recall@10)

Paired `bench-lme --cross-rerank` on ephemeral scratch PG, same sample/seed, local
`small` embedder:

| Arm | recall@10 | approx latency/query |
|---|---|---|
| no-rerank (baseline) | 0.474 | — |
| bge-reranker-base 24×256 | 0.474 | ~1556 ms |
| **ms-marco-MiniLM-L6 int8 24×256** | **0.526** | ~130 ms |
| **ms-marco-MiniLM-L6 int8 64×512** (full pool) | **0.526** | ~449 ms |

**MiniLM is at least parity — directionally better here.** At the *identical* 24×256
config it beat bge-base (0.526 vs 0.474, +0.053) at ~12× lower latency; at full
64×512 pool it held 0.526 in 449 ms (a config bge-base can't afford at ~5.9 s). The
no-rerank and bge-base arms tied at 0.474, so on this sample **rerank quality — not
just presence — is what moved retrieval, and MiniLM's speed is what makes full-pool
reranking affordable.**

**Caveats (honest):** n=20 is small — one question ≈ 0.053, so the +0.053 edge is a
single-question flip, directional not promotion-grade. This is the **retrieval** axis
only (free). Reader-QA — the binding adoption gate for flipping rerank default-on —
is a separate paid step, still gated per the plan; run it at n≥100 with CIs before
any default flip. recall@10 is also a weak discriminator when gold is already
in-pool (rung-7), so a rank-sensitive metric (first-answer-rank / recall@5) at
larger n is the better next screen.

## Cohere & Contextual Reranker v2 (owner-requested comparison)

**Cohere Rerank v3.5** — wired as `MEMPHANT_RERANKER=cohere-rerank-3.5` (owner
supplied a key; `CohereReranker` mirrors `VoyageReranker`, `MEMPHANT_COHERE_MODEL`
overrides the model for the v4.0-pro accuracy tier). Direct probe, 24 realistic
~1.5 KB docs, 5 calls: **p50 306 ms / p95 361 ms**, correct top-1, **1 search unit
= $0.001/query** (`billed_units`). It is genuinely fast and cheap — but **API-only
(not self-hostable) and it egresses every candidate body to Cohere**. In the LME-S
bench arm at the hard 1.5 s recall budget, some queries hit the reranker's global
timeout (cold-connection/TLS on first calls) and degraded → recall@10 0.333; the
steady-state probe latency above is the fair number. Pricing tiers (per search
unit): v3.5 $0.001, Fast $0.002, v4.0-pro $0.0025; v4.0-pro is higher-latency
premium quality.

**Contextual AI Reranker v2** (`ctxl-rerank-v2-instruct-multilingual-1b/2b/6b`,
Aug 2025 — the "multilingual instruction-following reranker" from the Reddit post):
**CC-BY-NC-SA-4.0 (non-commercial)** AND GPU-only (1B+ causal LM, BF16/vLLM/NVFP4;
CPU fallback "impractical for production"). Doubly disqualified for our path — an
accuracy-frontier/GPU option, not a latency-fit self-host one.

### Fixed-pool micro-benchmark — the accuracy/latency/cost head-to-head

Metric = rank of the gold doc → MRR, R@1/5/10. Local arms via the Rust `#[ignore]`
test (`rerank_fixed_pool_accuracy`); API arms via `rr_api_score.py`. Same machine,
same pools, per model.

**Two test generations — the first was INVALID (a data-quality bug worth recording):**

- **v1** (`build_pools.py`): 8 pools = 1 whole answer session + 43–56 whole distractor
  sessions, each truncated to 1500 chars. LME-S sessions are **9–18 KB** long and the
  answer is usually a single turn deep inside — so the 1500-char truncation **cut the
  answer out of the gold doc in 6 of 8 pools**. The reranker was scoring a gold snippet
  that no longer contained the signal → noisy, low, mis-ordered scores (e.g. Cohere
  v4.0-pro *below* v4.0-fast, v3.5 at MRR 0.41). **Do not trust the v1 numbers.**
- **v2** (`build_pools_v2.py`, the valid test): **12 pools of 48 same-length 1200-char
  CHUNKS** (MemPhant's runtime-chunk unit). GOLD = the chunk that actually **contains
  the answer** (verified 12/12); distractors = 47 random real chunks from other
  sessions. This is the honest, harder test — 48 same-shaped candidates, answer truly
  present in gold.

**v2 results (valid; 12 pools × 48 chunks):**

| Reranker | MRR | R@1 | R@10 | latency/query | cost/query | self-host |
|---|---|---|---|---|---|---|
| ZeroEntropy zerank-2 | **0.944** | 0.92 | **1.00** | **265 ms** | ~$0.0004 | ❌ non-commercial |
| bge-reranker-base (local) | 0.927 | 0.92 | **1.00** | 4813 ms | free | ✅ but slow |
| **ms-marco-MiniLM-L6 int8 (local)** | **0.926** | 0.92 | **1.00** | **391 ms** | **free** | ✅ Apache, CPU |
| Cohere rerank-v4.0-pro | 0.925 | 0.92 | **1.00** | 646 ms | $0.0025 | ❌ API |
| Cohere rerank-v4.0-fast | 0.921 | 0.92 | 0.92 | 376 ms | $0.002 | ❌ API |
| Cohere rerank-v3.5 | 0.866 | 0.83 | 0.92 | 390 ms | $0.001 | ❌ API |

(Voyage not run — no key. n=12, so a 1-question move ≈ 0.083 MRR — directional.)

**What the valid test shows (answers "were the docs similar or wildly different?"):**
- **On a valid test the whole field is tightly bunched (MRR 0.92–0.94).** 11 of 12
  questions are rank-1 for *every* reranker; only one hard question ("bought my tennis
  racket from the sports store downtown") separates them — zerank-2 ranks it 3,
  MiniLM 9, bge 8, cohere-v4-pro 10, v4-fast 21, v3.5 16. So the rerankers ARE similar
  on this workload; the v1 "wild differences" were the truncation artifact, not skill.
- **MiniLM-L6-int8 (free, Apache, local) ties bge-base and Cohere v4** (MRR 0.926 vs
  bge 0.927, cohere-v4-pro 0.925) at **~12× lower latency than bge** and zero cost.
- On the *valid* data bge is no longer "worse than MiniLM" — it's **equal accuracy but
  12× slower**, which still makes MiniLM the right default (accuracy-neutral, huge
  latency/cost win). zerank-2 is the accuracy leader but non-commercial.
- **Caveat: this workload may not stress rerankers enough** — 11/12 golds are trivially
  top-1, so the test can't separate the top models. A harder next test (adversarial
  near-duplicate distractors, or the full LME-S "hard" question subset) is needed to
  rank zerank-2 vs MiniLM vs Cohere-v4 with confidence; today they are a statistical tie.

<details><summary>v1 numbers (INVALID — truncation removed the answer; kept for the record)</summary>

| Reranker | MRR | R@10 | note |
|---|---|---|---|
| cohere-v4.0-fast | 0.704 | 0.88 | mis-ordered vs v4-pro |
| zerank-2 | 0.672 | 0.88 | |
| MiniLM-L6 int8 | 0.660 | 0.75 | |
| bge-base | 0.569 | 0.75 | |
| cohere-v4.0-pro | 0.560 | 0.75 | below v4-fast (artifact) |
| cohere-v3.5 | 0.410 | 0.38 | |

</details>

**Legacy note (superseded by v2):**
- ~~bge is slower AND less accurate~~ → on valid data bge **ties** on accuracy, still 12× slower.
- Cohere **v3.5 is the weakest** arm; the accuracy is in the **v4** family. **zerank-2**
  is the fastest/cheapest hosted and the accuracy leader on v2 — but non-commercial
  (its Apache sibling `zerank-1-small` is untested here).

### v3/v4 — the long-document stress test (the load-bearing finding)

Owner asked to test on **FULL documents** in a 48-doc pool (`build_pools_v3.py`: 12
pools of 48 untruncated LME-S sessions, 9–22 KB each, gold session contains the
answer 12/12). This exposed what the chunk test hid:

| Model | v2 (48 chunks) MRR | **v3 (48 FULL docs) MRR** | v3 latency | max context |
|---|---|---|---|---|
| cohere-v4.0-pro | 0.925 | **1.000** | 1342 ms | long / internal chunk |
| cohere-v4.0-fast | 0.921 | 0.958 | 721 ms | long |
| cohere-v3.5 | 0.866 | 0.958 | 1164 ms | long |
| zerank-2 | 0.944 | 0.872 | 1592 ms | chunks internally |
| **MiniLM-L6 int8 (local)** | 0.926 | **0.572** | 734 ms | **512-token wall** |
| bge-base (local) | 0.927 | 0.570 | 7077 ms | 512-token wall |

**The local rerankers COLLAPSE on full docs** (0.92 → 0.57). Root cause (verified):
`ms-marco-MiniLM-L6-v2` and `bge-reranker-base` are `BertForSequenceClassification`
with **`max_position_embeddings: 512`** — a hard architectural wall (~2000 chars).
`max_length=2048` gave byte-identical results to 512 (the model ignores it). Only
**6/12 answers sit in the first ~2000 chars**, so the 6 buried answers are unreachable.
The hosted rerankers see the full/chunked doc and stay strong.

**Does chunking recover it? YES — completely** (`build_pools_v4.py` + the
`rerank_chunked_pool_accuracy` test: chunk every doc into ~1200-char windows, rerank
all chunks, score a doc by its MAX chunk score):

| Model, v3 full docs | direct MRR | **CHUNKED (max-pool) MRR** |
|---|---|---|
| MiniLM-L6 int8 (local) | 0.572 | **1.000** |

Chunk-level reranking takes MiniLM from 0.572 to a **perfect 1.000** — matching
Cohere v4.0-pro — because every chunk fits inside the 512-token window.

**The architecture point (owner's insight):** chunking is done at **reflect/ingest
time** (free); it does NOT add rerank latency *by itself*. BUT today MemPhant's
reranker (`cross_rerank_candidates`) feeds `candidate.unit.body` = the **whole session
body** — `contextual_chunks` is a *field on the session unit* (`Vec<ContextualChunk>`,
`types/src/lib.rs:1057`), NOT a standalone retrievable unit. So the reranker currently
sees full sessions (~11k chars — exactly the r15 13 s measurement), and MiniLM/bge
truncate them. The **fix is to rerank the chunks, not the body**: feed the reranker
the `contextual_chunks` and max-pool to the unit. Latency then depends on **chunk
count reranked**, not doc length — rerank the ~64 chunk-candidates the retriever
already narrowed to (≈ 449 ms for MiniLM), NOT all chunks of all docs (the 4032 ms in
the test was because it chunked all 48 full docs — unrealistic; production narrows first).

## Wiring landed (spike → usable seam)

- `FastEmbedCrossReranker::from_user_defined(dir, onnx_name, config)` — loads a
  local ONNX + tokenizer through fastembed's user-defined path
  (`crates/memphant-runtime/src/embeddings.rs`).
- `MEMPHANT_RERANKER=byo` dispatch (`crates/memphant-runtime/src/lib.rs`), reading
  `MEMPHANT_RERANK_BYO_DIR` + optional `MEMPHANT_RERANK_BYO_ONNX`
  (default `model_quantized.onnx`), respecting the existing
  `MEMPHANT_RERANK_CANDIDATE_LIMIT`/`_MAX_LENGTH` knobs. The `--cross-rerank` bench
  arm and served recall both route through `build_cross_reranker`, so a bench and a
  served recall install byte-identical construction.
- Two `#[ignore]`d latency-matrix tests as reusable guards.

The model files are NOT committed (23 MB int8 ONNX + tokenizer; download from
`Xenova/ms-marco-MiniLM-L-6-v2`). The seam is data-agnostic.

## Final suggestion

Two coupled decisions — the **model** and the **granularity**. The granularity is the
one that actually unlocks the win.

**1. Rerank at CHUNK granularity, not whole-session bodies.** Today
`cross_rerank_candidates` feeds the reranker `candidate.unit.body` (a whole ~11k-char
session — the source of both the 13 s latency AND the 512-token truncation failure).
Change it to rerank each candidate's `contextual_chunks` (already computed at ingest,
free) and max-pool to the unit. On the full-doc test this took MiniLM from **MRR 0.572
→ 1.000**. Latency scales with *chunks reranked*, so rerank the chunks of the ~64
narrowed candidates (~hundreds of short chunks, still << the 13 s full-body cost), not
every chunk of every doc. This is a small change to one function and is the real fix.

**2. Adopt `ms-marco-MiniLM-L6-v2` int8 as the default reranker model** (behind the
existing default-OFF flag). On chunk-sized inputs it is the best point on the
accuracy × latency × cost × license × privacy frontier:
- **Accuracy**: ties the best hosted rerankers on chunks (MRR 0.926 v2; 1.000 chunked-v3).
- **Latency**: ~449 ms for 64 chunk-candidates, ~12× faster than bge-base.
- **Cost**: $0/query. **License/privacy**: Apache-2.0, CPU, no egress.

**Retire bge-reranker-base as the default** — equal accuracy to MiniLM on chunks but
~12× slower (and it hits the same 512-token wall on full docs). Keep it selectable for
parity regression only.

**Hosted arms stay opt-in** (`MEMPHANT_RERANKER=cohere-rerank-3.5` +
`MEMPHANT_COHERE_MODEL`). Their edge is **long-document robustness** (they don't need
chunking): Cohere v4.0-pro was perfect on full docs (MRR 1.000). If a managed API is
ever preferred, or if reranking un-chunked long docs is required: **Cohere v4.0-fast**
is the balanced pick; **v4.0-pro** the accuracy-max; **zerank-2** fastest/cheapest but
**non-commercial** (test its Apache `zerank-1-small` sibling first); **avoid v3.5**.

**Still gated (unchanged):** flipping `--cross-rerank` default-ON is a **paid reader-QA**
decision. This spike removed the *latency* blocker, picked the *model*, and identified
the *granularity* fix; the binding accuracy gate is a reader-QA run at n≥100 with CIs on
the chunk-reranking path. (These fixed-pool numbers are n=8–12, directional — and this
LME-S workload is easy: 11/12 golds are trivially top-1 on the valid tests, so it can't
separate the top models. A harder adversarial set is needed to rank MiniLM vs zerank-2
vs Cohere-v4 with confidence.)

## Wiring landed (all committed, gate green)

- `MEMPHANT_RERANKER=byo` + `FastEmbedCrossReranker::from_user_defined` (local ONNX via
  fastembed's user-defined path — no ort dep). Commit `127e131e`.
- `MEMPHANT_RERANKER=cohere-rerank-3.5` + `CohereReranker` + `MEMPHANT_COHERE_MODEL` +
  `MEMPHANT_RERANK_TIMEOUT_MS` (0 = unbounded, for offline benching). Commit `2e7fa26a`.
- Reusable `#[ignore]` tests: `rerank_real_model_latency_matrix` (bge),
  `rerank_byo_model_latency_matrix` (BYO latency), `rerank_fixed_pool_accuracy`
  (local accuracy on fixed pools). API arms: `docs/build-log/artifacts/reranker-spike/`
  (`rr_api_score.py` + `rr_pools.json`).

Model files (MiniLM int8 ONNX) and the LME-S corpus are NOT committed; the pools JSON
+ scorer are archived under the build-log artifacts so the bench is reproducible.
