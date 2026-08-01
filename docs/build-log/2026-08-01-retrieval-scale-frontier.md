# 2026-08-01 — The retrieval scale frontier: repo sizes, enumeration cost, crossover

**Lane `s9-scale` · $0 · `paid_model_calls: 0` · no server, no scratch DB, no port.**

Answers the owner's question — *"what exact values do coding repos actually want
from our retrievers? We may have repos with 50 files, we may have repos with a
million files"* — by measuring the real distribution rather than assuming it, and
by deriving where enumerate-then-rank stops being affordable.

Full write-up: `.superpowers/sdd/s9-scale-report.md`.

---

## Reproduction

```sh
python3 scripts/s9_scale_frontier.py --write     # ~2 s, stdlib only, no network
# -> docs/build-log/artifacts/s9-scale/frontier.json
```

Inputs (all read-only, none re-run):

| input | sha / id |
|---|---|
| `~/.memphant-private/track-r-paraphrase/run-s4/agentic-final-provenance.json` | s4-controls banked run, `reported_spend_usd` 25.9318 |
| `~/.memphant-private/track-r-paraphrase/run-fusion/fusion_probe-provenance.json` | treatment provenance `c7f3d311…` |
| `~/.memphant-private/track-r/artifacts/corpus.jsonl` | corpus sha256 `c008142e9921…`, 495 attempts / 64,055 events |
| `~/.memphant-private/track-r-paraphrase/track_r_paraphrase_golden.jsonl` | golden `4aed8e99dbf1`, n=180 |
| `docs/build-log/artifacts/s9-scale/track-r-repo-sizes.json` | 134 repos, GitHub `git/trees/HEAD?recursive=1`, fetched 2026-08-01, 0 truncated |

Repo sizes were collected with one `gh api repos/<owner>/<name>/git/trees/HEAD?recursive=1`
call per repo over the 134 distinct `repository` values in the golden bank, keeping
`type == "blob"` paths. The GitHub tree API truncates above ~100k entries; the
`truncated` flag was checked and is `false` for all 134. It is `true` for
`torvalds/linux` (67,653 blobs returned), so that number is a **lower bound**.

## The classification rule (dispute this, not the numbers)

- **source file** — extension in a fixed 58-entry set (`py rs go js jsx ts tsx
  mjs cjs java kt kts scala c h cc cpp cxx hh hpp hxx m mm swift rb php cs fs ex
  exs erl hs ml mli clj cljs lua pl pm r jl dart sh bash zsh sql vue svelte proto
  nim zig`).
- **vendored/generated** — any *directory* segment in `{node_modules, vendor,
  vendored, third_party, thirdparty, 3rdparty, target, build, dist, out, .venv,
  venv, site-packages, bower_components, Pods, .tox, .nox, eggs, .eggs,
  __pycache__, externals, deps, _deps, bundled, submodules, coverage, .next,
  .nuxt, .cache}`, or a basename matching `*.min.{js,css}`, `*.pb.{go,cc,h}`,
  `*_pb2[_grpc].py`, `*.pb.ts`, `*.generated.*`, `*_generated.*`, `*.g.dart`,
  `*.designer.cs`, `package-lock.json`, `yarn.lock`, `Cargo.lock`,
  `poetry.lock`, `pnpm-lock.yaml`.
- **authored source** = source ∧ ¬vendored, evaluated over the **VCS-known** file
  set (`git ls-files`), never the working tree.

The last clause is the load-bearing one. For `/Users/sidsharma/Syndai`:

| rule | count |
|---|---:|
| `find` over the working tree, all files | 307,732 |
| … with a source extension | 175,783 |
| … minus vendored/generated dirs | 21,238 |
| … of which **not** git-tracked | 14,349 (14,040 in `.claude/worktrees` — duplicate agent checkouts) |
| `git ls-files` | 9,329 |
| `git ls-files` ∧ source | 6,980 |
| **`git ls-files` ∧ source ∧ ¬vendored** | **6,889** |

A 100k-file answer and a 7k-file answer for the same repo differ only in whether
you asked the VCS or the filesystem. `.gitignore` already encodes the boundary.

## Headline measurements

**Repo sizes, 134 Track R repositories** (authored source): p10 19 · p25 43 ·
**p50 100** · p75 282 · p90 656 · p95 1,253 · p99 3,085 · max 4,829 · mean 297.
By total files: p50 183, p99 13,634, max 25,693. Buckets: 66 repos < 100
authored source files, 58 at 100–999, 10 at 1k–9.9k, **0 at ≥ 10k**.
MemPhant itself: 1,473 tracked / **258 authored source** (95 `.rs`).

Convergent public medians: **211** files/repo across 10K GitHub projects
([arXiv:2605.16701](https://arxiv.org/abs/2605.16701), body); **198** across
2.4K industrial repos ([arXiv:2605.12153](https://arxiv.org/pdf/2605.12153)
Table 4). Track R's 183 sits inside 15% of both.

**The enumeration unit, recomputed from S4** (`list_events` = `seq\trole\t` +
120-char preview, cap 200, and **no attempt exceeds 200 events** so every
question saw its haystack in full):

| | value |
|---|---:|
| chars per enumerated item | **121.19** mean (159 calls) |
| tokens per char | **0.2710** (4,554,054 prompt tok / 16,805,422 resent chars) — an *upper* bound |
| tokens per item | **32.84** |
| $/question at N = 140.6 | **$0.14407** (measured; model reproduces $0.14409) |
| agentic amplification vs single-pass | **6.241×** |
| wall seconds/question | **16.22** (486.62 s ÷ 180 × concurrency 6) |

**Price, FETCHED 2026-08-01** — `platform.claude.com/docs/en/about-claude/pricing`,
Claude Opus 5: **$5/MTok in, $25/MTok out**, 1M context. Cross-checked against
`openrouter.ai/anthropic/claude-opus-5` (the route S4 billed through): identical.

**The three thresholds** (preview index, 32.84 tok/item):

| wall | N |
|---|---:|
| 10 s latency | **already breached at N = 140.6**; linear model → N = 87 |
| $1/query, agentic loop | **976** |
| 200k context | **6,090** |
| $1/query, single-pass | **6,090** (identical — 200k input tokens *is* $1 at $5/MTok) |
| 1M context (Opus 5's real window) | 30,448 |

**$/query at scale**: 10² $0.10 · 10³ $1.02 · 10⁴ $10.25 · 10⁵ $102.48 ·
10⁶ $1,024.85 (agentic). Single-pass: $0.02 / $0.16 / $1.64 / $16.42 / $164.21.

**Banked Coverage(k)**, recomputed from the fusion probe, n=180: k=5 0.4333 ·
**k=10 0.6278** · k=16 0.7056 · k=32 0.8444 · **k=64 0.9111** · k=128 0.9333 ·
in-pool 0.9389. Median gold rank 6, p90 34, max 131. Recall costs **0 LLM calls,
$0**, 4.39 s/question in this harness.

## The crossover

```
A_enum(N)   = RankAcc(N)                 gold is in the haystack by construction
A_hybrid(k) = Coverage(k) · RankAcc(k)

hybrid wins  <=>   RankAcc(N) / RankAcc(k)  <  Coverage(k)
```

Minimum relative ranker decay required for hybrid to win: k=64 → **8.9%**;
k=32 → 15.6%; k=16 → 29.4%; k=10 → **37.2%**; k=5 → 56.7%.

One measured point: `RankAcc(140.6) = 174/180 = 0.9667` (s4-controls). It passes
the sanity check — for k=64 to have won there, `RankAcc(64)` would need to be
≥ 1.061, impossible. `RankAcc(m)` elsewhere is **s8-hybrid's** to measure; no
value is invented here. Substitute it into the inequality above; do not
re-derive.

**Sensitivity.** Accuracy conclusions are entirely contingent on RankAcc — if it
is flat in N, retrieval never wins on accuracy at any N. Cost and latency
conclusions are not contingent at all, because recall costs 0 LLM calls: the
LLM-cost ratio enumerate:retrieve is exactly **N : k** (156× at k=64, N=10⁴).

## The granularity caveat that moves the answer 10×

At **file** granularity no Track R repo exhausts even a 200k window. At **chunk**
granularity (×10, MODELLED from measured lines-per-authored-file: Syndai mean
282, MemPhant mean 627, Linux 406 implied; band ×7–×16) the **median** repo
breaks $1/query agentic, the **p90** repo breaks 200k tokens, and the **p99**
repo breaks 1M. This is the single most load-bearing assumption in the lane. The
$0 follow-up is to count chunks per repo at MemPhant's real chunker settings.

## Recommendation

```
k(N) = N     for N <=  64      # retrieval is a no-op; hand over everything
k(N) = 64    for N  >  64      # the banked coverage knee
```

Raising k from 10 to 64 costs **$0** at recall and ~$0.011 of downstream
prefill, and buys **+0.2833 coverage** (+51/180, banked). The knee is at 64:
64→128 buys only +0.0222. Not continuous in N — `Coverage(k)` is a property of
the ranker, not of corpus size, and nothing measured supports a schedule.
**Ship behind a flag defaulted to the current k=10 until s8-hybrid lands
`RankAcc(64)`; flip the default then.**

## What is not claimed

Track R is one attempt per question; its paraphrase bank **fails** its own
preregistered leakage bar (`concentration 2.018` vs ≤ 1.50, `bar_passed: false`)
and its q→target coverage brackets `paraphrase 0.1346 < human 0.175–0.287 <
original 0.396`. **`Coverage(k)` here is a shape, not a production magnitude** —
this lane leans on its ordering and its ratios, not its absolute values. File
counts and token arithmetic are unaffected.

Chromium, LLVM and rust-lang file counts: **UNVERIFIED**. No authoritative
public count exists and our own API calls were rate-limited before completing.
