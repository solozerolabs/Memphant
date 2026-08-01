# R6 — the docs-lane unlock: decidable, and at what price

**Date:** 2026-08-01 · **Branch:** `af-r6-mine` (base `accuracy-first` @ `d01affad`)
**Spend:** $0. No paid model call was made in producing this document.
**Machine-readable:** `docs/build-log/artifacts/r6-docs-decision/packet.json`
(regenerate and verify with `python3 scripts/derive_r6_docs_packet.py [--check]`)

---

## Verdict

> **UPDATE, 2026-08-01, after kill-gate (b) ran (§K.5, §7): the recommendation
> is NO-GO on the $57.72–$121.39.** The $0 pre-check §5 required first has now
> been completed and it fails on both legs — full-pool MiniLM-L6-int8 chunk
> rerank costs 2,142 ms p50 / 3,275 ms p95 against a 1,500 ms ceiling on a quiet
> release host, and returns δ = −0.058 (n = 120, n_d = 19, p = 0.167) where the
> L1XC advantage it had to reproduce was +0.083. The pricing below stands as
> correct arithmetic for a run that should not be authorized.

**R6 is NOT decidable at $0. The blocker is money, and the price is
$57.72 floor / $121.39 ceiling.** It is not a corpus blocker, not a licence
blocker, and — contrary to the C2-era record — no longer an infrastructure
blocker. Every input needed to decide R6 properly exists and is runnable today.
What does not exist is a $0 path to the reader/judge calls, and the mining
calls, that a homogeneous n = 370 requires.

Three findings change how the decision should be taken, in descending order of
consequence:

1. **The premise "two sets exist, mine four more" is false. Zero usable sets
   exist.** The 120 scored rows behind R6 were graded against a golden bank and
   a corpus revision that the C2 re-pin retired. Not one of the 120 survives
   into the currently pinned bank as the same question. Pooling them with
   anything new would be a lineage violation of exactly the kind this program
   names as its dominant failure mode.
2. **n = 370 is confirmed from the realized ψ, but 370 is the optimistic end.**
   ψ is estimated from 26 discordant pairs; its Wilson interval runs to 0.299,
   at which 7pt needs **n = 500**. Sizing at 370 is sizing at the point estimate
   of a noisy nuisance parameter.
3. **The two existing sets are not demonstrably homogeneous, and one of them
   carries the entire result.** v1 is b=8/c=1, exact p = 0.039 — significant on
   its own. v2 is b=10/c=7, exact p = 0.63 — a flat null. The pooled p = 0.0755
   is one strong set averaged with one null set. Homogeneity tests do not
   reject, but they are not powered to; that is absence of evidence, not
   evidence of homogeneity.

---

## 1. The actual state, recomputed from rows on disk

The R6 contrast is the r15 best arm **L1XC** against the **Syndai incumbent**,
pooled over the two disjoint 60-question docs sets. The null-review ledger entry
`r15-r6-parity` records `rows_recovered: true`; those rows were re-recovered
independently here and reproduce the ledger exactly.

| set | arm | baseline | n | b (arm only) | c (Syndai only) | n_d | ψ | δ | exact p |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | `r15-L1XC-v1` | `syndai-gate-syndai` | 60 | 8 | 1 | 9 | 0.1500 | +0.1167 | **0.0391** |
| v2 | `r15-L1XC-v2` | `r1-syndai-v2` | 60 | 10 | 7 | 17 | 0.2833 | +0.0500 | 0.6291 |
| **pooled** | | | **120** | **18** | **8** | **26** | **0.2167** | **+0.0833** | **0.0755** |

Pooled ψ = 0.216667, δ = +0.0833, exact two-sided McNemar p = 0.075519 — matching
the ledger's `psi`, `delta` and `p_exact` to six places. n_d = 26 clears the
n_d ≥ 6 structural floor comfortably; the problem was never the floor.

### Homogeneity — the pooled n is not yet earned

A pooled n is admissible only across homogeneous sets. Two exact tests:

- **Effect direction**, Fisher on the discordant cells `[[8,1],[10,7]]`: p = 0.190.
- **Discordance rate**, Fisher on `[[9,51],[17,43]]`: p = 0.120.

Neither rejects. Neither is remotely powered to detect heterogeneity at n_d = 9
and 17, so **non-rejection here carries no information**. What is visible without
a test is that ψ nearly doubles between the sets (0.150 → 0.283) and δ more than
halves (+0.117 → +0.050), and that the entire nominal significance of the pooled
result comes from v1. If v1 is the atypical set, R6's observed effect is an
artifact of set choice. Six sets from one generator on one pin, with per-set ψ
reported, is the only way to settle that — which is precisely what the program's
"do not run a third set and call it an answer" note demands.

## 2. The corrected required n

Computed by `scripts/instrument_power.py` (two-sided exact conditional-binomial
McNemar, α = 0.05, power integrated unconditionally over N_d ~ Binomial(n, ψ)),
driven by the **realized** ψ rather than an assumed one:

| ψ | source | MDE @80% at n=120 | n for 7pt | n for the observed 8.33pt |
|---:|---|---:|---:|---:|
| 0.1524 | Wilson lower bound | 10.1pt | 265 | 185 |
| **0.2167** | **realized, pooled** | **12.3pt** | **370** | **260** |
| 0.2985 | Wilson upper bound | 14.4pt | **500** | 355 |

**The register's n = 370 is confirmed, not assumed** — it falls out of the
realized ψ to the unit. Power at the current n = 120 against the observed
+8.33pt is 0.424; the true MDE is 12.3pt against a 7pt preregistration.

The honest planning number, however, is **500, not 370.** ψ is itself an
estimate from 26 discordant pairs. Sizing at the ψ point estimate gives 80%
power only if ψ lands exactly on 0.2167; at the top of its own interval the
same run delivers ~65%. This is the identical mistake the register catalogues
for Phase 2 (a ψ assumption with no run behind it), one rung less severe.

## 3. Is n = 370 reachable at $0?

### 3.1 The corpus is available and licence-irrelevant — and that is not the good news it sounds like

The pinned corpus is **Syndai product documentation**, private, at
`96a26f1f`: 114 files, 4,920 sections, **3,299 mining-candidate sections**.
120 goldens have been mined from it. There is ample corpus headroom for 370 —
roughly 27× the sections consumed so far. The Syndai checkout is present and the
pinned revision resolves. **Corpus availability is not the blocker.**

Licence: private, owner's own project, used read-only. Not a blocker, and not
publishable — bodies stay gitignored, which this branch honors.

### 3.2 No public corpus can substitute, and this is structural rather than a matter of finding the right dataset

The task named the C3 route — public HF trajectory datasets as a drop-in via a
schema adapter. It does not transfer, for a reason that is about R6's definition
rather than about dataset availability:

**R6 asks whether MemPhant beats the Syndai incumbent stack, at comparable
reader volume, on Syndai's own documentation.** The baseline arm is Syndai's
production pipeline (`search_knowledge_detached`: HNSW + BM25 + RRF K=60 +
Jina rerank) ingesting Syndai's own docs through its own sectionizer and
chunker. Swap the corpus for a public one and the incumbent arm evaporates —
Syndai's stack has no privileged relationship to `nebius/SWE-rebench`, and
beating it there answers a question nobody asked. A public docs corpus would
produce a *different, cheaper, and less decision-relevant* measurement, not more
n for this one.

Sources evaluated, with licences:

| source | licence | verdict for R6 |
|---|---|---|
| Syndai product docs @ `96a26f1f` (pinned) | private, owner's own | **the only corpus that can answer R6**; 3,299 mining candidates, ample headroom |
| `scripts/ingest_public_bench.py` (LongMemEval-V2, GateMem, PS-Bench) | mixed; LME-V2 Apache-2.0 with a pinned LICENSE blob | **DISQUALIFIED by its own contract** — it emits `source_status: synthetic_contract_fixture`, answer-seeded cases for regression gating that its docstring forbids using as benchmark evidence |
| Nebius OpenHands trajectories via `scripts/materialize_public_code_lane.py` | CC-BY-4.0 (clean) | licence-clean, wrong domain (coding trajectories) and wrong comparison — no incumbent arm |
| `nebius/SWE-rebench` and siblings | permissive per the C3 memo | same as above: viable for the *code* lane, cannot host a docs-lane incumbent contrast |
| Track R / GitHub-lane / LME-S pinned corpora | CC-BY-4.0 / mixed / unverified | wrong lane; none is a product-documentation corpus with a competing production retriever attached |

Nothing was disqualified for being non-commercial. Everything was disqualified
for being unable to carry the incumbent arm.

### 3.3 The C2-era infrastructure blocker is gone — verified, not assumed

The 2026-07-22 C2 log recorded that a live Syndai incumbent re-run was blocked
by a Syndai dev-DB migration drift (`knowledge_source_versions.content_sha256`
absent, alembic head newer than the column-adding migration) and therefore fell
back to the committed 2026-07-11 figure. **That is no longer true.** Checked
read-only against the local dev database at `127.0.0.1:55432/syndai_local`:
`content_sha256` is present on `knowledge_source_versions`, and the alembic head
is `2026_07_30_003_drop_retired_orphan_tables`. The database accepts
connections. `scripts/gate_run_syndai.py` has a live target again.

This matters: it means R6's blocker is *only* price. Had the incumbent arm still
been unrunnable, no amount of money would have produced a paired row, and the
verdict would have been "undecidable at any price."

### 3.4 The lineage break — why "two sets exist" is wrong

The C2 re-pin (2026-07-22) moved the corpus from `fb650da` (109 files) to
`96a26f1f` (114 files / 4,920 sections) and **re-mined both golden sets against
the new pin**. The r15 rows predate it:

| | r15-scored rows | currently pinned bank |
|---|---|---|
| golden sha256 (v1) | `c424b08f…` | `8cb21da4…` |
| golden sha256 (v2) | `30b354c5…` | v2 lock |
| haystack sections at scoring time | 3,257 | 4,920 (3,299 mining candidates) |
| question_ids also present in the current bank | 14 / 60 per set | — |
| **same question AND same gold answer** | **0 / 60 per set** | — |

Zero of 120. Even the 14 per-set id collisions are different questions reusing a
sequential id. **The two "existing sets" are not two sets on the current pin;
they are zero.** Any pooled n that mixes them with newly mined rows would mix
two corpus revisions and two golden banks under one p-value.

Consequence for cost: reaching n = 370 means **mining 250 new goldens** (120 are
already mined on the current pin) and **reader-scoring all 370 on both arms**,
not 250. The already-paid r15 scoring is sunk.

## 4. The price

Derived by `scripts/derive_r6_docs_packet.py` under the
`derive_phase2_packet.py` convention, unchanged: one byte per prompt token at
the **widest measured** evidence row, 1,024 completion tokens, the recorded
provider maxima ($2.75 / $16.50 per million), times an enumerated call budget.
Widths are measured from the shipped evidence JSONLs, not estimated.

| stage | model | prompt bound | calls (floor → ceiling) | USD floor | USD ceiling |
|---|---|---:|---|---:|---:|
| A · mine 250 new goldens | `google/gemini-3.1-pro-preview` | 8,000 | 250 → 1,667 | $9.72 | $64.84 |
| B · reader, MemPhant L1XC arm | `openai/gpt-5.6-terra` | 19,000 | 370 | $25.58 | $25.58 |
| C · reader, Syndai incumbent arm | `openai/gpt-5.6-terra` | 8,000 | 370 | $14.39 | $14.39 |
| D · judge | `anthropic/claude-sonnet-5` | 2,000 | 358 → 740 | $8.02 | $16.57 |
| **total for n = 370** | | | | **$57.72** | **$121.39** |

Derivations behind each bound:

- **A.** The mining prompt is at most two mining-candidate sections, and the
  corpus lock's own rule caps a candidate body at 3,200 chars → 8,000 tokens
  bounds it. The call floor is one accepted golden per generator call; the
  ceiling is the miner's own recorded default budget, `--max-calls 400` per
  60-golden set. **No `google/gemini-3.1-pro-preview` price is recorded anywhere
  in this repo**, so it is bounded at the gpt-5.6-terra maxima — the same
  substitution the register applies to STATE-Bench's unpriced gpt-5.4. Pin the
  real price at authorization.
- **B/C.** Widest measured rows: 18,975 B (MemPhant L1XC) and 7,763 B (Syndai),
  read from the shipped evidence JSONLs.
- **D.** Grading is containment-first with one judge call on non-match. The fire
  rate is measured, not assumed: the r15 L1XC v1 run made 89 total calls over 60
  rows (70 fresh + 19 cached) = 29 judge calls, 48.3%. Floor uses that rate;
  ceiling assumes every row. The judge prompt width has never been measured on
  this lane and is bounded generously at 2,000 tokens.

**At the honest planning n = 500** (ψ at its Wilson upper bound), the same
derivation gives **$79.62 floor / $174.94 ceiling**
(`price_block(..., 500)` in the same script).

**Unpriced inputs, declared:** the Syndai incumbent ingest runs
`text-embedding-3-small` over ~9,890 chunks and `search_knowledge_detached` may
issue Jina rerank calls. No OpenAI-embedding or Jina price is recorded in this
repo; both are **UNVERIFIED** and small relative to the stages above. They are
not folded into the totals.

**Independent cross-check.** The r15 wave itself settled ≈$25–35 reader/judge
for four arms × 120 rows plus a chat pair. This packet's floor is the same order
of magnitude for two arms × 370 rows, which is the sanity check one wants on a
byte-bounded ceiling.

## 5. What should happen — and the $0 step that comes first

R6 is decidable for roughly the cost of a takeout dinner. Before authorizing it,
one **$0** question should be settled, because it can make the whole spend
worthless:

**The arm under test may be disqualified before the reader ever sees it.** L1XC
carries the retired cross-encoder at 12.9–13.6 s/query against a preregistered
1.5 s ceiling. The C2 kill-gate then established that the win lives in the 16–64
candidate band, unreachable by the top-16 rank compression that was the only
configuration under the ceiling with that model. But C2 also recorded that the
"latency-dead" premise had gone stale — MiniLM-L6 int8 at **chunk granularity**
reranks all 64 candidates in ~450 ms, comfortably inside 1.5 s. **Kill-gate (b),
the leg that would test whether a full-pool sub-second rerank reproduces the
L1XC advantage, was set up and deliberately abandoned as corroborating-only.**

So the ordering is:

1. **$0, first.** Complete C2 kill-gate (b) on the currently pinned 120-golden
   bank: MemPhant base vs `MEMPHANT_RERANKER=byo` MiniLM-int8,
   `--rerank-granularity chunk`, `--resource-chunks`, full 64 pool, scored on the
   **retrieval** endpoint (deterministic span containment — no reader, no judge,
   no money). The scorer and launchers are archived at
   `docs/build-log/artifacts/p1-c2-killgate/`. Budget the measured ~50 min per
   modernbert drain and guard against machine sleep, which is what killed it
   before. If a sub-second full-pool rerank does not reproduce the retrieval
   advantage, **R6 is moot and $57–121 is saved.**
2. **Only then, $57.72–$121.39.** Mine four disjoint sets (250 goldens) on the
   pinned `96a26f1f` corpus with `scripts/gate_mine_goldens.py` at the same seed
   protocol, same generator, `--exclude-golden` against the existing 120; score
   all 370 on both arms; report **per-set ψ and δ alongside the pooled** so
   homogeneity is provable rather than assumed; commit `b` and `c`, never a
   bootstrap CI (register Z6).
3. **Size at 500, not 370,** or preregister the acceptance at the MDE that 370
   actually delivers. Do not repeat Phase 2's error of preregistering a
   resolution the n cannot reach.

## K. Kill-gate (b) — the $0 pre-check, now run

§5 said this had to happen before anything was priced. It has. Full method,
lineage handling, and the process failures caught along the way are recorded
here; the result and the revised recommendation follow in §K.5 and §7.

### K.1 What (b) asks, and why it was worth running

Kill-gate (a) (2026-07-22) established that the r15 cross-encoder's QA win lives
at candidate ranks **17–64** — 23 of 26 winning flips sit there — and is
therefore unreachable by the top-16 rank compression that was the only shape
affordable under the 1.5 s ceiling with the 13 s bge model. (b) asks the
complementary question that C2 set up and then abandoned as
"corroborating-only": now that a small int8 cross-encoder can rerank the **full
64-candidate pool**, is that band affordable in practice — and does reaching it
actually move retrieval?

Arms, differing in exactly one variable, on the currently pinned 120-golden bank
(v1 + v2), scored on the **retrieval** endpoint with `gate_common.provenance_hit`
span containment. No reader, no judge, **$0**:

- **base** — modernbert, deep mode, `--resource-chunks`, k=10, budget 8192, pool 64
- **rerank** — identical, plus `--cross-rerank --reranker byo
  --rerank-granularity chunk --rerank-candidate-limit 64 --rerank-max-length 512`
  with `MEMPHANT_RERANK_BYO_DIR` on the ms-marco-MiniLM-L6-v2 int8 ONNX

### K.2 The latency premise did not survive contact — retracted, not merely unreproduced

This is the finding that matters most, because it is the premise (b) rests on.
Same `#[ignore]`d matrix test the spike used, same model dir, same `--release`
profile, four consecutive samples at 64 × 512 on this host:

| sample | 1 | 2 | 3 | 4 | median |
|---|---:|---:|---:|---:|---:|
| `elapsed_ms` | 1140 | 1871 | 1448 | 1472 | **~1460** |

Against the pre-registered **1500 ms** ceiling that is **0.97× headroom, not the
recorded 3×**, and **two of four samples breach it**. The 449 ms figure is now
carried as **RETRACTED** at the top of
`docs/build-log/2026-07-22-reranker-latency-spike.md` and in the C2 log that
inherited it. What survives is the *model-swap* conclusion — bge-base is the
wrong model and MiniLM-L6-int8 is roughly an order of magnitude faster. What does
not survive is the margin.

Two caveats, kept because they cut both ways: the host carried concurrent load
from sibling worktrees, and four samples is a small matrix. Neither rescues a 3×
claim that measures 0.97×. The point of the retraction is that the margin was
never measured carefully enough to carry a spend decision. And note this matrix
is **body granularity on synthetic ~1.5 KB documents** — the cheapest case. The
arm below runs **chunk** granularity on real sections, where each candidate
flattens into several scored docs, so its own `cross_rerank_ms` is the
authoritative number and can only be worse.

### K.3 Two lineage hazards, one of them a machine for manufacturing findings

**The corpus pin has drifted again.** Syndai HEAD is now `7cbcd13e` and the gate
hard-failed `corpus file set mismatch` on the first attempt — the same failure
that opened the C2 wave. The C2 response was to re-pin. **That was not available
here**: re-pinning re-mines the goldens, which destroys the bank the whole
comparison rests on and costs paid generator calls. Instead the pinned tree
`96a26f1f` was `git archive`d into a scratch directory — the Syndai checkout was
read only, nothing written to it — and **114/114 files verified byte-identical to
`benchmarks/manifests/syndai_docs_gate.lock.json`** before ingest, yielding 4,920
sections exactly as the lock records. The pin is preserved without a re-mine.

**The abandoned (b) artifacts are mostly unusable, and one is dangerous.**

| artifact | lineage | reusable? |
|---|---|---|
| `verdict-a.json`, `flips-source.json` | derived from r15 reader rows on the **retired** golden bank (`c424b08f`/`30b354c5`), reproduced on the **old** `fb650da` corpus archive | internally consistent, but about a bank that no longer exists — usable only as the qualitative claim "the win sits at ranks 17–64" |
| `repro-prov-v{1,2}.json` | **no `corpus_revision`, no `golden_sha256`, no label** | **no** — unstampable, therefore unverifiable |
| `score_killgate_b.py` (archived) | see below | **no — superseded and disarmed** |

The archived scorer silently falls back to a hardcoded `SYNDAI_HIT10=0.200`,
taken from the 2026-07-11 gate on a **different corpus pin**, whenever the live
incumbent arm is absent — and then reports a difference of two aggregates as a
"gap-closure fraction". A scorer that substitutes a constant from another pin
when its comparison arm is missing cannot fail closed, and the number it emits
carries no lineage at all.

**Exposure audit: nothing banked came through that path.** The scorer never ran
to completion; no `verdict.json` exists in `p1-c2-killgate/` and none was ever
committed (`git log --all --diff-filter=A` → 0 commits). The only surviving trace
is *prose* in `STATUS.md:88` and the C2 log describing the intended comparison
against "the incumbent's committed 0.200 hit@10" — a plan, not a result. So
there is **nothing to retract downstream**, and the trap has been disarmed with a
`SUPERSEDED — DO NOT RUN` banner rather than deleted, since it is a committed
artifact of that wave. Its replacement, `scripts/score_killgate_b.py`, refuses to
mix pins and reports a paired exact McNemar on per-question vectors.

### K.4 A near-miss worth recording: the first arm ran on a debug binary

The first base-arm launch used `target/debug/memphant-server`, the runner's
default. It was killed mid-ingest and both arms were rebuilt and re-run on
`--release`. **A latency claim measured on a debug binary is worthless**, and
this one was a single command away from being banked beside a 1.5 s ceiling. The
accuracy half of the arm would have been unaffected — retrieval output does not
depend on the optimization level — which is exactly what makes the failure mode
dangerous: the run would have looked entirely successful and produced one number
that was silently meaningless.

The generalizable fix is the same shape as the register's adapter gate: **a
harness that can emit a latency figure should refuse to run against a debug
build, or stamp the profile into the artifact so the reader can see it.**
`gate_run_memphant.py` currently stamps neither the binary path nor its hash; the
scorer added here stamps `server_bin_sha256` and `worker_bin_sha256` itself, but
that is a patch at the wrong layer.

Scratch-DB hygiene held throughout: the killed run's ephemeral database was
dropped by `with_scratch_db.sh`'s own trap, and no sibling worktree, shared
database, or Syndai file was touched.

### K.4b A third process failure: `--mode deep` is no longer a $0 endpoint

§K.1 specifies both arms in "deep mode". That specification is dead and the
first re-run died on it: every `/v1/recall` returned
`503 deep_unavailable`. `RecallMode::Exhaustive` — what "deep" meant when the
r15/r1 docs runs were scored, and what their provenance headers actually record
(`recall_mode: "exhaustive"`) — was removed; `deep` now routes through the L4
agentic provider and requires `MEMPHANT_DEEP=on` with a paid OpenRouter model.

The gate does not want that pass and cannot afford it. The cross-encoder rerank
is not behind the deep branch: it runs in the shared retrieval path
(`cross_rerank_candidates` over the top `recall_pool_depth` = 64 fused
candidates) before packing, identically under either mode. Both arms therefore
ran `--mode fast`, which is the retrieval endpoint (b) asks for and the only one
reachable at $0. The deviation is recorded in the launcher and here rather than
absorbed silently, because "deep" in a §K.1 arm spec and "deep" in today's
server are two different things and the collision will bite the next reader too.

### K.5 Result — **(b) FAILS, on both legs, and R6 as specified is moot**

Both arms completed on the pinned corpus (4,920 sections, section_revision
`82a1eeca…`, 114/114 files verified) against the currently pinned 120-golden
bank (v1 `8cb21da4…`, v2 `1dc12fbb…`), `--release` binaries, sha256 of the
served server/worker cross-checked against `target/release` on disk from each
arm's own `generation_identity`. Machine-readable:
`docs/build-log/artifacts/p1-c2-killgate/killgate-b/verdict-b.json`.

**Mechanism liveness — the reranker demonstrably fired.** From the server's own
per-question traces, not from the flags passed to it: 120/120 questions carry a
`cross_rerank` trace, `candidate_count` = 64 on every one of them against
`candidate_limit` = 64 (the full pool was scored, not a top-16 head),
`provider: byo`, `model: byo:model_quantized.onnx`, zero reranker failures, and
the ordered returned-body digest differs from the base arm on **120/120**
questions against a 50% bar. An inert reranker was ruled out before any score
was read.

**Latency — breaches the ceiling by 2.2×, on a quiet host.**

| | p50 | p95 | max | ceiling | profile | cpu_count | loadavg (1/5/15) |
|---|---:|---:|---:|---:|---|---:|---|
| `cross_rerank_ms`, n=120 | **2,142 ms** | **3,275 ms** | 3,852 ms | 1,500 ms | release | 12 | 10.19 / 11.63 / 14.55 |

That is **0.85 loadavg per core** — below the 1.5/core quiet bar the scorer
enforces — and the rerank arm started at loadavg 9.58 and ended at 10.91, so the
sample neither started nor finished contended. This is not the 22,166 ms
contention artifact this repo recorded elsewhere at loadavg 120–140; it is the
real cost of the work. It lands exactly where §K.2 predicted: the retracted
449 ms and the re-measured ~1,460 ms were both **body** granularity on synthetic
~1.5 KB documents, and **chunk** granularity on real sections — where each of the
64 candidates flattens into several scored chunks — is 1.5× slower again than the
already-at-ceiling body case. **There is no sub-second full-pool chunk-rerank on
this host.** The premise (b) was built on does not exist.

**Effect — the L1XC retrieval advantage is not reproduced.** Paired exact
McNemar on hit@10, per-question vectors, deterministic `provenance_hit` span
containment, no reader, no judge:

| set | n | b (rerank only) | c (base only) | n_d | ψ | δ | exact p |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | 60 | 2 | 7 | 9 | 0.1500 | −0.0833 | 0.1797 |
| v2 | 60 | 4 | 6 | 10 | 0.1667 | −0.0333 | 0.7539 |
| **pooled** | **120** | **6** | **13** | **19** | **0.1583** | **−0.0583** | **0.1671** |

Aggregate hit@10: base **0.2250**, rerank **0.1667**. hit@5 pooled: b=6, c=9,
n_d=15, δ = −0.0250, p = 0.607. n_d = 19 clears the structural floor of 6, so
this **is** a measurement; the run's own MDE at 80% power is 10.4pt.

Read it precisely. The test does not reject in either direction, so this is not
a demonstration that full-pool chunk-rerank *harms* retrieval, and an effect
smaller than 10.4pt cannot be excluded at n = 120. What it is not, is the thing
(b) had to find: **the point estimate is −5.8pt, and both sets point the same
way.** Passing required a positive rejection. A gate looking for a reproduction
of a +8.3pt advantage found a negative point estimate on 120 paired questions
with a live, full-pool, 100%-reordering cross-encoder. Nothing here argues for
spending money on the arm.

**Both legs fail independently, and the accuracy leg is the one that would still
fail on infinite hardware.** A faster machine could in principle move the latency
figure under the ceiling; nothing about a faster machine changes a −5.8pt δ.

This is also the published norm rather than an anomaly. The 2026-08-01
arms-and-landscape review records CoREB finding 4 of 5 off-the-shelf rerankers
net-negative on *every* code task, and no published number supporting top-64 CPU
rerank under 200 ms on ~512-token documents — the size class that fits the budget
(<35M params) and the class with any evidence behind it (149M–4B) are disjoint.
Our bge negative and this MiniLM negative are what the field reports.

## 6. What must stop being said

Per the null review's own instruction: the 2026-07-12 refusal
(`+0.083 [+0.000, +0.167]`, "NOT unlocked") must **stop being cited as evidence
of no effect.** It is a bootstrap CI whose floor touched exactly zero on an
instrument with a 12.3pt MDE against an 8.3pt effect. The exact test now run on
those rows gives p = 0.0755 — not a rejection, but not a null either. The
correct statement is: *R6 has never been measured at a resolution capable of
answering it, the corpus and the infrastructure to do so both exist, and the
measurement costs $57.72–$121.39.*

## 7. Revised spend recommendation — **NO-GO. Do not authorize the $57.72–$121.39.**

§5 preregistered the decision rule before the run: *"If a sub-second full-pool
rerank does not reproduce the retrieval advantage, R6 is moot and $57–121 is
saved."* It does not, on either half of that sentence. The rerank is not
sub-second — it is 2,142 ms p50 / 3,275 ms p95 against a 1,500 ms ceiling on a
quiet release host — and it does not reproduce the advantage, returning
δ = −0.058 where +0.083 was the thing being chased. **The recommendation is
no-go, and the money is saved.**

What that does and does not decide, stated narrowly:

- **Decided.** The R6 contrast *as specified* has no shippable arm. Its best arm
  was L1XC, whose win came from a cross-encoder that costs 12.9–13.6 s/query;
  kill-gate (a) showed the win lives at candidate ranks 17–64, out of reach of
  the top-16 compression that was the only affordable shape; (b) has now shown
  that the cheap full-pool substitute is neither affordable nor better. Paying
  $57–121 to measure, at n = 370, an arm that cannot ship at any n is the
  clearest kind of waste this program exists to avoid.
- **Not decided.** Whether MemPhant *without any cross-rerank* beats the Syndai
  incumbent. That is a different arm with a different, entirely unmeasured
  effect size — the packet's n = 370 was derived from L1XC's realized ψ and does
  not transfer. If that question is wanted, it needs its own $0 pre-check and
  its own sizing, not this packet's number with a different arm substituted in.
- **Not decided.** Whether a *better* reranker exists in the affordable class.
  The landscape review's finding — that the <35M size class has no published
  code-lane evidence and the 149M–4B class has no published sub-200 ms top-64
  CPU number — says the search space is empty today, not that it is empty
  forever. Revisit on a new model, not on a new hope.

**If a go is ever taken on any docs-lane arm, size at n = 500, not 370.** §2's
reasoning is untouched by this gate: 370 falls out of a ψ point estimate from 26
discordant pairs whose Wilson interval reaches 0.299, where the same run
delivers ~65% power instead of 80%. The alternative — preregistering acceptance
at the MDE that 370 actually delivers, i.e. **12.3pt, not 7pt** — is acceptable
only if stated in advance and in those words. Do not preregister 7pt and run 370.

One standing instrument note, not a blocker for this decision: the base arm's own
hit@10 on this bank is 0.225. A lane whose incumbent-free baseline retrieves the
gold section in under a quarter of questions has room to move that is not about
reranking at all, and any future docs-lane spend is better aimed there.

---

### Evidence contract compliance

- **Leakage five-tuple.** No new set was mined, so no new leakage
  characterization is claimed. The existing docs bank's leakage is recorded as
  **unverified** in the instrument register (§2A row 11) and this document does
  not upgrade that. Any set mined under §5 step 2 must be characterized on all
  five fields — unit definition (markdown heading-leaf section), absolute target
  coverage, floor with **exhaustive vs sampled named explicitly**, concentration,
  and provenance class (agent-generated from the target section, terms withheld
  by the miner's paraphrase instruction) — against a measured achievable floor,
  not a transcribed bar.
- **Provenance vs concentration.** Kept separate throughout. No contamination
  claim is made here; none was tested.
- **Paired exact McNemar** with per-question vectors recovered, realized ψ, MDE,
  and per-set as well as pooled cells: §1.
- **n_d ≥ 6 structural floor:** cleared at n_d = 26 pooled; v1 alone at n_d = 9
  also clears it, v2 at 17.
- **Lineage stamped** in `packet.json`: MemPhant git head, sha256 of
  `instrument_power.py` and of the deriving script, evidence root. No Rust binary
  is involved in this derivation.
- **Spend: $0.** No paid model call, no live benchmark run, read-only access to
  the Syndai checkout and dev database, no shared MemPhant database touched.
- **Kill-gate (b)'s own artifact.** `killgate-b/verdict-b.json` carries a
  schema-valid `evidence_contract` with `decisional: true` (n_d = 19 clears the
  floor; every field is read off a banked artifact, none is `unverified`) and is
  registered under `contracted`. It stamps git head, branch, dirty flag, build
  profile, the sha256 of the binaries the arms actually served cross-checked
  against `target/release` on disk, corpus revision, and cpu_count + the
  governing run-time loadavg beside the latency figures. Two known gaps are
  recorded in it rather than papered over: `CrossRerankTrace.docs_scored` and
  the granularity are not propagated into the provenance row by
  `gate_run_memphant._cross_rerank_facts`, so both read `unverified` in the
  mechanism block — chunk granularity is evidenced by the arm command and by the
  measured cost, not by a server-side field. Closing that propagation gap is the
  right next instrumentation fix; it does not affect this verdict, which turns on
  `candidate_count` = 64 (verified) and on wall-clock.
- **Spend for (b): $0.** Retrieval endpoint only, no reader, no judge, no paid
  provider — which is precisely why `--mode deep` had to be dropped (§K.4b).
- **Evidence-contract ratchet:** `packet.json` carries a schema-valid
  `evidence_contract` block with `decisional: false` — it decides nothing, it
  re-derives banked cells and prices an unauthorized run — and is registered in
  `benchmarks/manifests/evidence_contract_registry.json` under `contracted`.
  `check_contract` returns zero violations.

### Test gate

`python -m pytest tests/` → **1109 passed, 3 failed, 15 skipped**. All three
failures are pre-existing on the base commit and were reproduced with this
branch's artifact removed:

- `test_public_launch_gate.py::test_public_sota_claim_policy_...` — `playwright`
  is not installed in this environment (`sh: playwright: command not found`).
- `test_check_evidence_contract.py::test_every_decisional_artifact_is_contracted_or_declared_debt`
  and `::test_the_retrofit_report_is_current` — three artifacts committed by
  earlier waves (`sibling-gather-deletion/sibling_gather_deletion.json`,
  `track-r-paraphrase/w02-trunk-arms.json`, `track-r-paraphrase/z1-ladder-power.json`)
  are unregistered, which also leaves the retrofit report three candidates
  stale. Not this branch's debt and deliberately not swept in here, because
  regenerating that report would fold three other sessions' artifacts into this
  commit.

No Rust changed, so `cargo test` / `clippy` / `fmt` are not in scope for this
commit.

**Re-run after kill-gate (b), on the merged base:**
`python -m pytest tests/test_check_evidence_contract.py` → 43 passed, 2 failed.
Kill-gate (b)'s artifact is not among the failures — it validates and is
registered. Both failures are inherited from the trunk merge (the
`2026-08-01-key-production/recovery.json` artifact is unregistered, which also
leaves the retrofit report stale) and were reproduced with this branch's changes
stashed. Not this branch's debt, and deliberately not swept in: regenerating that
report would fold another session's artifact into this commit.
