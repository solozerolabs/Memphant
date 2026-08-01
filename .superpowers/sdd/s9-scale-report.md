# S9 — What coding repos actually look like, and where enumeration stops being affordable

**Branch `s9-scale` · 2026-08-01 · $0, `paid_model_calls: 0` · NOT merged, NOT pushed.**

---

## The four answers

**1. The real distribution is small, and it is not close.** Across Track R's
**134 distinct repositories** (155 bound attempts, 64,055 emitted events), the
median repo carries **100 authored source files** and **183 files total**. p90 is
656 authored source files; p99 is 3,085; the largest is 4,829
(`bridgecrewio/checkov`). Two independent 2026 measurements of the wider
population land in the same place — median 211 files across 10K GitHub projects,
median 198 across 2.4K industrial repos. **The "million-file repo" is real but is
0/134 of the measured distribution and five orders of magnitude off its median.**

**2. Enumeration is affordable far longer than it looks, and its wall is
latency, not money.** At S4's own measured unit cost, a single-pass index of N
items breaks a 200k context window at **N = 6,090** and breaks $1/query at the
same **N = 6,090** (identical by construction: 200k input tokens *is* $1 at
$5/MTok). The S4-realistic agentic loop, which resends the index every turn,
breaks $1/query at **N = 976**. But the 10-second latency budget is **already
breached at N = 140.6** — S4 measured **16.2 s/question**. Latency is the first
wall and it is behind us, not ahead.

**3. The crossover is a budget crossover, not an accuracy crossover — and on
this evidence enumeration never loses on accuracy at repo scale.** With
Coverage(k) banked and RankAcc unmeasured, retrieve-then-rank beats
enumerate-then-rank on accuracy iff `RankAcc(N)/RankAcc(k) < Coverage(k)`. At
k=64 that demands the ranker lose **≥ 8.9% relative** between depth 64 and depth
N before retrieval is even level. At k=10 it demands **≥ 37.2%**. Every
conclusion about *accuracy* is sensitive to s8-hybrid's RankAcc(N) curve. No
conclusion about *cost* or *latency* is, because MemPhant recall costs **0 LLM
calls and $0 marginal**, measured.

**4. `k` must be a function of haystack size, but the function is a floor, not a
sweep.** Fixed k=10 throws away 26.6 points of banked coverage that k=64 already
holds (0.6278 → 0.9111) at a marginal LLM cost of **$0.00**. The recommendation
is `k = 10` only when N ≤ ~64 (where k ≥ N means retrieval is a no-op), and
`k = 64` otherwise — a step function, floored, not a continuous schedule. Below
N ≈ 64 a retriever is measurably pointless: the agent can hold the whole
haystack. At N ≥ ~1,000 items it is mandatory on latency and on cost.

---

## 1. What real coding repos actually look like — MEASURED

### 1a. Track R's 134 repositories

Counts taken from the GitHub `git/trees/HEAD?recursive=1` API on 2026-08-01, one
call per repo, 134/134 resolved, **0 truncated trees**. Artifact:
`docs/build-log/artifacts/s9-scale/track-r-repo-sizes.json`.

The classification rule, stated so it can be disputed
(`scripts/s9_scale_frontier.py` imports nothing; the rule is reproduced in the
build log):

- **source file** = extension in a fixed 58-entry code-extension set.
- **vendored/generated** = any *directory* segment in
  `{node_modules, vendor, third_party, target, build, dist, .venv, site-packages,
  Pods, __pycache__, deps, .next, coverage, …}`, or a basename matching
  `*.min.js`, `*.pb.go`, `*_pb2.py`, `*.generated.*`, `package-lock.json`,
  `Cargo.lock`, …
- **authored source** = source ∧ ¬vendored, over the VCS-known file set.

| statistic | files | source files | **authored source** |
|---|---:|---:|---:|
| min | 4 | 0 | 0 |
| p10 | 46 | 19 | 19 |
| p25 | 85 | 44 | 43 |
| **p50** | **183** | **101** | **100** |
| p75 | 563 | 309 | 282 |
| p90 | 1,940 | 656 | 656 |
| p95 | 3,188 | 1,284 | 1,253 |
| p99 | 13,634 | 3,086 | 3,085 |
| max | 25,693 | 4,829 | 4,829 |
| mean | 907 | 298 | 297 |

Bucketed: **66/134 repos have fewer than 100 authored source files**, 58 have
100–999, 10 have 1k–9.9k, **0 have ≥ 10k**. The mean (297) is 3× the median
(100) — the distribution is right-skewed, which is the whole point, but the skew
tops out three orders of magnitude below the monorepo tail.

Per-attempt haystack, recomputed from `corpus.jsonl` (495 attempts, 64,055
events, sha256 `c008142e9921…`):

| | min | p50 | p90 | max | mean |
|---|---:|---:|---:|---:|---:|
| events / attempt (all 495) | 66 | 122 | 200 | 200 | 129.4 |
| events / attempt (155 bound) | 68 | 132 | 200 | 200 | 139.5 |
| MemPhant pool size (fusion probe) | 0 | 122.5 | 177 | 199 | 124.2 |

Mean **857.4 bytes per event** over the bound attempts; median event is 264
bytes, p99 is 4,000 (the clip). Median attempt is 116 KB of text.

### 1b. MemPhant and Syndai — and the vendored-vs-authored gap

| repo | tracked files | source files | **authored source** | on-disk files |
|---|---:|---:|---:|---:|
| MemPhant (`s9-scale` HEAD) | 1,473 | 258 | **258** | — |
| Syndai (`/Users/sidsharma/Syndai`) | 9,329 | 6,980 | **6,889** | 307,732 |

**The Syndai number the brief flagged is a factor-of-15 artefact.** A naive
walk of the working tree finds **307,732 files**, of which **175,783** carry a
source extension — that is the regime the reported 104,288 came from. Of those
175,783, **154,545 are vendored/generated by the directory rule**, and of the
21,238 survivors **14,349 are not git-tracked at all** — 14,040 of them live in
`.claude/worktrees`, i.e. duplicate agent checkouts of the same repo. Applying
the tracked-file rule leaves **6,889 authored source files**.

**Rule of record: `git ls-files` is the authority.** `.gitignore` already
encodes the authored/vendored boundary the repo's own maintainers drew, and it
beat every heuristic here. The extension+directory filter then removes a further
5% (9,329 → 6,889). A 100k-file answer and a 7k-file answer differ by whether
you asked the VCS or the filesystem.

MemPhant itself is a **small** repo by this measure: 95 `.rs` files, 144 `.py`,
258 authored source in total — p75 of the Track R distribution. Its 1,473
tracked files are mostly fixtures (725 `.json`) and docs (271 `.md`).

Measured lines-per-source-file, needed in §3: MemPhant p50 290 / mean 627;
Syndai p50 176 / mean 282.

### 1c. The upper tail — public reference points

| fact | number | source | date | status |
|---|---:|---|---|---|
| Google monorepo, **total files** | 1 billion | Potvin & Levenberg, *CACM* 59(7):78–87, [PDF](https://bowringj.people.charleston.edu/classes/csci%20362/docs/GoogleCodeRepo-78-potvin.pdf) | Jan 2015 stats | VERIFIED |
| Google monorepo, **source files** | **9 million** | same | Jan 2015 | VERIFIED |
| Google monorepo, lines of code | 2 billion | same | Jan 2015 | VERIFIED |
| Google monorepo, content size | 86 TB | same | Jan 2015 | VERIFIED |
| Windows repo, files | ~3.5 million | [devblogs.microsoft.com/bharry](https://devblogs.microsoft.com/bharry/the-largest-git-repo-on-the-planet/) | 2017-05-24 | VERIFIED |
| Windows repo, size | ~300 GB | same | 2017-05-24 | VERIFIED |
| Meta monorepo | "tens of millions of files and commits" | [engineering.fb.com Sapling](https://engineering.fb.com/2022/11/15/open-source/sapling-source-control-scalable/) | 2022-11-15 | VERIFIED as order-of-magnitude only; no precise count published |
| Linux kernel, files | 108,158 (cloc); **≥ 67,653 blobs** by our own `git/trees` call (GitHub truncates) | [phoronix](https://www.phoronix.com/news/Linux-7.2-43-Million-Lines) / this lane | 2026-06-28 / 2026-08-01 | VERIFIED (third-party cloc) / MEASURED-lower-bound |
| Linux kernel, lines | 43,898,743 total / 33,653,681 code | same | 2026-06-28 | VERIFIED |
| **Median files per GitHub repo** | **211** (2026); 131 (2021); 111 (2016) | Hora et al., [arXiv:2605.16701](https://arxiv.org/abs/2605.16701) | 2026-07-13 v2 | VERIFIED — paper body, not abstract. Sample is ≥100★/≥100-commit repos, i.e. the *popular* tail |
| Files per industrial repo | median 198, mean 931, P10 28, P75 597, P90 1,723 | Savenkov, [arXiv:2605.12153](https://arxiv.org/pdf/2605.12153) Table 4 | 2026 | VERIFIED — but private repos from 12 partner orgs, single-author vendor paper, internal n inconsistency (2,440 vs 2,545) |
| Chromium file count | — | — | — | **UNVERIFIED** — no authoritative count exists; nearest real measurement is 30,137 compiled C++ translation units (Bruce Dawson, 2019 code) |
| LLVM / rust-lang file counts | — | — | — | **UNVERIFIED** — nothing published; our own API calls were rate-limited before completing |

**The conflation, named.** The CACM paper's own text: 2 billion lines live in
**9 million source files**; the 1-billion figure is a path count at head that
"also includes source files copied into release branches, files that are deleted
at the latest revision, configuration files, documentation, and supporting data
files." And the 86 TB figure *excludes* release branches while the 1-billion
count *includes* them — do not divide one by the other. For a retrieval-scale
argument the load-bearing number is **9 million**, not 1 billion.

**Convergent validation.** Track R's p50 of 183 files sits within 15% of two
independently-sampled 2026 medians (211 and 198). Track R is a SWE-bench-style
sample of Python OSS and is not a random draw from GitHub — but on the one
statistic that matters here it does not look pathological.

---

## 2. The affordability frontier for enumeration

### 2a. What "enumerate" actually cost in S4 — MEASURED

The S4 agentic control's `list_events` emits one line per event:
`seq\trole\t` + a **120-character whitespace-collapsed preview**, capped at
`LIST_EVENTS_MAX = 200` events. **No attempt in the corpus exceeds 200 events**,
so every question saw its haystack in full. This is the measured enumeration
unit, and it is not a full body.

Recomputed from `run-s4/agentic-final-provenance.json` (159 `list_events` calls
across 180 questions):

| quantity | value | how |
|---|---:|---|
| chars per enumerated item | **121.19** mean (121.47 p50, 113.4–127.8) | `result_chars / events_available` |
| items enumerated / question | 140.6 mean, 200 max | `events_available` |
| prompt tokens (total) | 4,554,054 | `usage.prompt_tokens` |
| completion tokens (total) | 126,460 | `usage.completion_tokens` |
| tool text resent across calls | 16,805,422 chars | prefix-sum over `tool_call_log` |
| **tokens per char** | **0.2710** (≈ 3.69 chars/token) | 4,554,054 / 16,805,422 |
| **$/question** | **$0.14407** | 25,300.3 in × $5/M + 702.6 out × $25/M |
| reported spend | $25.9318 (180 q) | matches to 5 decimals |
| mean tool calls | 3.989 | `liveness` |
| **wall seconds / question** | **16.22** | 486.62 s × concurrency 6 ÷ 180 |
| **agentic amplification** | **6.241×** | measured $/q ÷ single-pass index cost |

Two notes on rigour. **0.2710 tok/char is an upper bound**, not a point
estimate: it attributes the system prompt, tool schemas, question text and
assistant turns to the tool text. It is nonetheless close to right — the docs
warn that Opus 4.7+ uses a tokenizer producing ~30% more tokens per character
than the classic ~4-chars/token rule, and 3.69 chars/token is exactly that. The
**6.241× amplification** is the price of the loop: the index is resent on every
turn and grep/read results pile on top. Both are MEASURED at N ≈ 140 and
**assumed constant in N** when projected — which is optimistic, because bigger
haystacks provoke more tool calls.

### 2b. Catalogue price — FETCHED 2026-08-01

`https://platform.claude.com/docs/en/about-claude/pricing`, "Model pricing" row
**Claude Opus 5**: **$5 / MTok input**, **$25 / MTok output**, cache read $0.50,
5-min cache write $6.25, batch $2.50 / $12.50. Context window **1M tokens** for
4.6-and-later at standard pricing. Cross-checked against
`https://openrouter.ai/anthropic/claude-opus-5` ($5 / $25, 1M) — which is the
route S4 actually billed through, and the two agree.

### 2c. The frontier — MODELLED (arithmetic on §2a and §2b)

`tokens_per_item = chars_per_item × 0.2710`. Preview index: **32.84 tok/item**.
Path-only index (measured mean blob path length 60.9 B + newline over 121k blobs
in 134 repos): **16.77 tok/item**.

| N | index tokens (preview) | $ single-pass | $ agentic (×6.241) | index tokens (path-only) | $ single-pass |
|---:|---:|---:|---:|---:|---:|
| 10² | 3,284 | $0.02 | $0.10 | 1,677 | $0.01 |
| 10³ | 32,842 | $0.16 | **$1.02** | 16,775 | $0.08 |
| 10⁴ | 328,425 | **$1.64** | $10.25 | 167,749 | $0.84 |
| 10⁵ | 3,284,249 | $16.42 | $102.48 | 1,677,490 | $8.39 |
| 10⁶ | 32,842,490 | $164.21 | $1,024.85 | 16,774,900 | $83.87 |

Sanity: at N = 140.6 the model returns $0.14409 against a measured $0.14407.

**The three thresholds.**

| wall | preview index (32.84 tok/item) | path-only index (16.77 tok/item) |
|---|---:|---:|
| **(a) 200k context** | **N = 6,090** | N = 11,923 |
| (a′) 1M context (Opus 5's actual window) | N = 30,448 | N = 59,613 |
| **(b) $1/query, single-pass** | **N = 6,090** | N = 11,923 |
| **(b′) $1/query, agentic loop** | **N = 976** | N = 1,910 |
| **(c) 10s latency** | **breached at N = 140.6 (16.2 s measured)**; linear model puts the 10 s point at **N = 87** | same |

(a) and (b) coincide at 6,090 for an exact reason, not a coincidence of
rounding: 200,000 input tokens at $5/MTok *is* $1.00. **Under Opus 5 pricing the
200k-context wall and the $1-per-query wall are the same wall.**

**Latency is the binding constraint and it is already behind us.** Enumeration
at N = 140 — the smallest interesting haystack — took 16.2 seconds of wall
clock per question. That is not a projection. Even the optimistic
linear-in-N model puts a 10-second budget at **N = 87**, i.e. *below* the
haystack S4 actually ran. Latency is worse than linear in practice: each extra
turn is a serial round trip, and the index is re-prefilled every time.

For contrast, MemPhant recall on the same 180 questions took **789.65 s total =
4.39 s/question** in the fusion-probe harness (`timings.recall_seconds`) at
**0 LLM calls and $0 marginal spend**. That harness number is not an SLO — it
includes cold fastembed-on-CPU query embedding, and the packaged C1 measurement
is p50 ≈ 34 ms — but taken like-for-like against an equally unoptimised agentic
harness, retrieval is **3.7× faster at N = 140 and approximately flat in N**,
while enumeration is at best linear.

### 2d. Where the real distribution sits against the frontier

| threshold | Track R repos over it, by authored source | by total files |
|---|---:|---:|
| 10 s latency (N = 87) | **72 / 134 = 53.7%** | 96 / 134 = 71.6% |
| $1/query agentic (N = 976) | 11 / 134 = 8.2% | 18 / 134 = 13.4% |
| 200k context & $1 single-pass (N = 6,090) | **0 / 134 = 0.0%** | 4 / 134 = 3.0% |
| 1M context (N = 30,448) | 0 / 134 | 0 / 134 |

**At file granularity, no repo in the measured distribution can exhaust a 1M
context window, and none can exhaust 200k either.** That is the honest reading
and it is uncomfortable for a retriever.

---

## 3. The crossover — THE HEADLINE

### 3a. The granularity that decides everything

§2d holds only if the enumeration unit is **one file**. It is not, for a
question that needs code *content*. A retriever indexes chunks, and an agent
that has seen 3,000 paths has read nothing. Measured lines per authored source
file: **282 mean (Syndai, n=6,889)**, **627 mean (MemPhant, n=258)**, **406**
implied for Linux (43.9M lines / 108,158 files, Phoronix cloc). At a 40-line
chunk that is **7–16 chunks per file**; the table below uses **×10**, MODELLED,
with the band stated.

| repo point | files | tok (path idx) | $ 1-pass | $ agentic | chunks ×10 | tok (preview idx) | $ 1-pass | $ agentic |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Track R p10 | 19 | 319 | $0.00 | $0.01 | 190 | 6,240 | $0.03 | $0.19 |
| **Track R p50** | **100** | 1,677 | $0.01 | $0.05 | 1,000 | 32,840 | $0.16 | **$1.02** |
| Track R p75 | 282 | 4,729 | $0.02 | $0.15 | 2,820 | 92,609 | $0.46 | $2.89 |
| Track R p90 | 656 | 11,001 | $0.06 | $0.34 | 6,560 | **215,430** | $1.08 | $6.72 |
| Track R p95 | 1,253 | 21,013 | $0.11 | $0.66 | 12,530 | 411,485 | $2.06 | $12.84 |
| Track R p99 | 3,085 | 51,735 | $0.26 | $1.61 | 30,850 | **1,013,114** | $5.07 | $31.61 |
| Track R max (checkov) | 4,829 | 80,982 | $0.40 | $2.53 | 48,290 | 1,585,844 | $7.93 | $49.49 |
| MemPhant | 258 | 4,327 | $0.02 | $0.14 | 2,580 | 84,727 | $0.42 | $2.64 |
| Syndai authored | 6,889 | 115,529 | $0.58 | $3.61 | 68,890 | 2,262,348 | $11.31 | $70.60 |
| Linux (≥, truncated) | 43,342 | 726,845 | $3.63 | $22.68 | 433,420 | 14,233,513 | $71.17 | $444.16 |
| Windows (3.5M files) | 3,500,000 | 58,695,000 | $293.48 | $1,831.58 | 35,000,000 | 1,149,400,000 | $5,747 | $35,867 |
| Google (9M source files) | 9,000,000 | 150,930,000 | $754.65 | $4,709.77 | 90,000,000 | 2,955,600,000 | $14,778 | $92,230 |

Read the two halves against each other:

- **At file granularity**, enumeration survives the *entire* measured
  distribution and dies only in the monorepo tail (Linux breaks $1 single-pass
  at 43k files; Windows and Google break the context window outright by 60× and
  150×).
- **At chunk granularity**, the **median repo already breaks $1/query** in the
  agentic loop ($1.02 at 1,000 chunks), the **p90 repo already breaks a 200k
  window** (215,430 tokens), and the **p99 repo already breaks a 1M window**
  (1,013,114 tokens).

**This one modelling choice moves the answer by a factor of ten and moves the
verdict from "a retriever is optional for every repo we measured" to "a
retriever is mandatory at the median."** It is MODELLED, from measured
lines-per-file, and it is the single most load-bearing assumption in this
report. If it matters to a ship decision, measure chunks-per-repo directly at
MemPhant's actual chunker settings — that is a $0 measurement and it is not
done here.

### 3b. The accuracy crossover, parameterised in RankAcc

Definitions, kept honest:

- `Coverage(k)` = P(gold within the retriever's top-k). **MEASURED**, n = 180,
  recomputed here from `fusion_probe-provenance.json`.
- `RankAcc(m)` = P(agent puts gold in its top-10 | gold is among the m items
  shown). **MEASURED at exactly one point**; the curve is s8-hybrid's.

| k | 1 | 3 | 5 | 10 | 16 | 32 | 64 | 128 | 256 (= in-pool) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Coverage(k) | 0.1667 | 0.3500 | 0.4333 | **0.6278** | 0.7056 | 0.8444 | **0.9111** | 0.9333 | **0.9389** |
| hits / 180 | 30 | 63 | 78 | 113 | 127 | 152 | 164 | 168 | 169 |

Median gold rank 6, p90 34, max 131. MemPhant's shipped default is k = 10, so
its 0.5889 hits@10 is `Coverage(10)` (0.6278) minus packing losses.

The two systems:

```
A_enum(N)    = RankAcc(N)                    gold is in the haystack by construction
A_hybrid(k)  = Coverage(k) · RankAcc(k)
```

so retrieve-then-rank beats enumerate-then-rank **iff**

```
        RankAcc(N)
        ----------  <  Coverage(k)
        RankAcc(k)
```

The required relative decay of the ranker between depth k and depth N:

| k | Coverage(k) | max tolerable `RankAcc(N)/RankAcc(k)` | minimum relative decay for hybrid to win |
|---:|---:|---:|---:|
| 5 | 0.4333 | 0.4333 | **56.7%** |
| 10 | 0.6278 | 0.6278 | **37.2%** |
| 16 | 0.7056 | 0.7056 | 29.4% |
| 32 | 0.8444 | 0.8444 | 15.6% |
| 64 | **0.9111** | 0.9111 | **8.9%** |
| 128 | 0.9333 | 0.9333 | 6.7% |

**The one measured point, and the sanity check it passes.** S4 measured
`RankAcc(140.6) = 174/180 = 0.9667`. For hybrid at k = 64 to have won there,
`RankAcc(64)` would have to have been ≥ 0.9667/0.9111 = **1.061 > 1** —
impossible. So the framework *predicts* S4's outcome: at N ≈ 140, no retrieval
depth beats enumeration on accuracy. Good; the model reproduces the one fact it
has.

**Which conclusions are sensitive to RankAcc, stated plainly.**

- *Sensitive.* Every claim about accuracy. If s8 finds RankAcc is **flat** in N
  — the agent ranks gold into its top-10 just as reliably at 10,000 items as at
  140 — then **retrieval never wins on accuracy at any N**, and MemPhant's case
  rests entirely on cost, latency, and context-window feasibility. If s8 finds
  RankAcc decays steeply (say 0.97 → 0.60 between 10² and 10⁴), then k = 64
  overtakes enumeration somewhere near where `RankAcc(N) < 0.9111·RankAcc(64)`,
  and MemPhant has an accuracy story as well.
- *Insensitive.* Every claim about cost and latency. MemPhant recall is **0 LLM
  calls, $0 marginal, measured**, so the LLM-cost ratio of enumerate:retrieve is
  exactly **N : k** regardless of what RankAcc turns out to be. At k = 64 and
  N = 10⁴ that is **156×**. And enumeration's 16.2 s at N = 140 is measured,
  not modelled.

**Substitute, do not re-derive.** When s8 lands a `RankAcc(m)` curve, the
crossover N is the smallest N satisfying `RankAcc(N) < Coverage(k)·RankAcc(k)`
with `Coverage` read straight from the table above. Nothing else in this section
needs redoing.

### 3c. Bound, ahead of the number

**Track R is one attempt per question, and its paraphrase bank overshot its own
correction** — q→target coverage brackets `paraphrase 0.1346 < human
0.175–0.287 < original 0.396`, and the bank **fails** its preregistered leakage
bar (`concentration 2.018` against ≤ 1.50, `bar_passed: false`). Two goldens have
spans in no event of their own attempt, capping every arm at 178/180.
Consequently: **`Coverage(k)` here is a shape, not a production magnitude.** The
*ordering* of the coverage curve and the *ratios* between depths are what this
report leans on; the absolute 0.6278 and 0.9111 are not claimed to transfer.
Repo-size and token-cost measurements are unaffected — they are file counts and
arithmetic, not model outcomes.

---

## 4. What MemPhant should ship

### 4a. Does a 50-file repo need a retriever? No — measurably.

A 50-file repo is p25 of the measured distribution. At file granularity its
index is ~840 tokens ($0.004 single-pass); at ×10 chunk granularity ~16,400
tokens ($0.08 single-pass, $0.51 agentic). It fits a 200k window **12×** over
and a 1M window **61×** over. And at N ≤ ~64 items, `k = 64` returns the whole
haystack anyway — **retrieval is a no-op by definition**. Below roughly 64
retrievable units, MemPhant's correct behaviour is to hand over everything and
not pretend to rank.

### 4b. When does it become mandatory?

Three answers, in the order they bite:

1. **Latency, at N ≈ 90–140** — MEASURED. 16.2 s at N = 140.6 already fails a
   10 s interactive budget, and 53.7% of Track R's repos exceed the linear
   10-second point at *file* granularity alone. This is the first wall and the
   only one that is already behind us.
2. **$/query, at N ≈ 976** (agentic loop) — MODELLED on measured unit cost.
   8.2% of Track R repos by authored source, **and the median repo once
   chunk granularity is applied.**
3. **Context window, at N ≈ 6,090 (200k) / 30,448 (1M)** — MODELLED. No repo in
   the measured distribution at file granularity; the p90 repo at chunk
   granularity for 200k; the p99 repo at chunk granularity for 1M.

So: **mandatory from ~1,000 retrievable units, which is the median real repo at
chunk granularity and the p95 repo at file granularity.** Between 64 and 1,000
units it is a latency and cost optimisation, not a correctness requirement.

### 4c. Fixed `k` or a function of N?

**A function of N — specifically a step function with a floor, not a sweep.**

The arithmetic. MemPhant recall costs **0 LLM calls and $0** (measured,
`llm_calls_at_recall: 0`, `reported_spend_usd: 0.0`). So raising k from 10 to 64
costs **nothing at the retrieval stage** and buys **+0.2833 coverage** (0.6278 →
0.9111, +51 questions of 180, banked). What it costs is downstream: the reader
or the agent must now rank 64 candidates instead of 10, which is exactly the
`RankAcc(64)` s8 is measuring, plus 64 × 32.84 ≈ 2,100 tokens ≈ **$0.011** of
prefill — a rounding error against a $0.14 enumeration.

Recommendation:

```
k(N) = N              for N ≤ 64      # retrieval is a no-op; hand over everything
k(N) = 64             for N > 64      # the banked coverage knee
```

- **Why 64 and not 128.** Coverage gains 0.0667 going 10→16, 0.1389 going
  16→32, 0.0667 going 32→64, and only **0.0222** going 64→128. The knee is at
  64; 128 buys four more questions of 180 for double the reader load.
- **Why not continuous in N.** Nothing measured supports a k that grows with
  haystack size. `Coverage(k)` is a property of the *ranker*, not of N — the
  gold's rank distribution (p50 6, p90 34, max 131) is what sets the useful
  depth, and there is no evidence here that it shifts with corpus size. A
  continuous schedule would be invented machinery justifying itself.
- **What would change this.** If s8 finds `RankAcc(64)` materially below
  `RankAcc(16)` — i.e. the agent *degrades* when handed 64 candidates — the
  floor should drop to the depth where `Coverage(k)·RankAcc(k)` is maximised,
  which is a one-line re-read of the table in §3b against s8's numbers. **Ship
  the step function behind a flag defaulted to the current k = 10 until s8
  lands; flip the default when it does.**

### 4d. Measured vs modelled, in one place

| claim | status |
|---|---|
| 134-repo size distribution; Syndai/MemPhant counts; lines per file | **MEASURED** (file counts, this host + GitHub API 2026-08-01) |
| 121.19 chars/item, 0.2710 tok/char, 6.241× amplification, $0.14407/q, 16.22 s/q | **MEASURED** (recomputed from banked S4 provenance) |
| Coverage(k) curve, 0 LLM calls at recall, 4.39 s/q recall | **MEASURED** (recomputed from banked fusion provenance) |
| Opus 5 $5/$25 per MTok, 1M context | **FETCHED** 2026-08-01, two independent pages agree |
| Google 9M source files, Windows 3.5M, Linux 108k, GitHub median 211 | **VERIFIED** public sources, cited |
| Chromium / LLVM / rust file counts | **UNVERIFIED** |
| $/query at N = 10²…10⁶ and the three thresholds | **MODELLED** — arithmetic on the measured unit cost, holding amplification constant in N (optimistic) |
| ×10 chunks-per-file | **MODELLED** from measured lines-per-file; band ×7–×16; the single most load-bearing assumption here |
| Any accuracy crossover N | **NOT DERIVED** — requires s8-hybrid's RankAcc(N). Only the threshold *condition* is derived |

---

## Cost and hygiene

**$0, `paid_model_calls: 0`.** One stdlib-only script, 134 unauthenticated-class
GitHub API tree reads, four public page fetches, and arithmetic. No server, no
scratch database, no port, no embedding model, no reader, no judge — no
cross-lane hazard touched. Nothing was re-run; every model-derived number is a
recomputation from an artifact another lane already paid for.

**No promotion-capable artifact is produced.** `frontier.json` is a
measurement-and-arithmetic artifact with `paid_model_calls: 0` and no arm, no
gate, and no contrast; it moves no default, checkbox, cutover or SOTA claim.
`scripts/check_evidence_contract.py --report` was regenerated and its candidate
set is unchanged by this lane.

**No paid arm is proposed.** The two open questions are both $0: (i) s8-hybrid
already owns RankAcc(N); (ii) chunks-per-repo at MemPhant's real chunker
settings is a local ingest measurement, not a model call.

Report: this file. Build log:
`docs/build-log/2026-08-01-retrieval-scale-frontier.md`. Artifacts:
`docs/build-log/artifacts/s9-scale/{frontier.json,track-r-repo-sizes.json}`.
Script: `scripts/s9_scale_frontier.py`. Branch `s9-scale`, **not merged, not
pushed.**
