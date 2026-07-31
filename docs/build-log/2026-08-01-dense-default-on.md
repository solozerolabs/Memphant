# Dense retrieval is ON by default — `bm25code_dense` is the shipped configuration

**Date:** 2026-08-01 · **Branch:** `af-dense-on` (base `accuracy-first` @ `d01affad`)
**Spend:** $0 — no paid model call, no hosted embedder, no hosted reranker.
**Artifact:** `docs/build-log/artifacts/dense-default-on/liveness.json`
**Probe:** `scripts/dense_default_liveness_probe.sh` (scratch DB, re-runnable)

Owner directive, verbatim: *"Turn on dense right now (don't gate it anymore). No
more gates. If things work, just turn them on and default them."* Priorities in
force: **Accuracy/UX > Cost > perf/latency > security.**

---

## 0. What was actually off

Two separate switches, and only one of them was where the 2026-07-30 null said
it was:

1. **The dense embedder was already default-ON for the served binaries.**
   `memphant-server` and `memphant-worker` both ship `default = ["fastembed"]`
   (`crates/memphant-server/Cargo.toml:25`, `crates/memphant-worker/Cargo.toml:17`),
   and `build_embedder()` resolves an unset `MEMPHANT_EMBEDDINGS` to local
   bge-small-en-v1.5 (`crates/memphant-runtime/src/lib.rs:357-396`). The vector
   channel is gated purely on `embedder.dimensions() > 0`
   (`crates/memphant-core/src/lib.rs:6067`, `:11904`), so it was live on the
   served path already. Nothing was gating dense at the product boundary.

2. **The lexical family defaulted to `overlap`, so the shipped product was
   `overlap_dense`, not `bm25code_dense`.** That is the arm the paraphrase
   ladder measured at **83 fused hits@10** — 30 behind the winning arm. The
   harness that measures the code lane additionally defaulted to
   `--embed-model off`, so its default arm was `overlap_off` — the **worst of
   the four**, at 40.

The full measured 2×2 from `docs/build-log/artifacts/track-r-paraphrase/w0-2-five-arm.json`
(n=180, decontaminated paraphrase bank, fused hits@10):

| | dense OFF | dense ON |
|---|---:|---:|
| `overlap` | **40** ← old harness default | 83 ← old shipped default |
| `bm25-code` | 89 | **113** ← now the shipped default |

`bm25code_dense` vs `bm25code_off`: b=29 / c=5, McNemar exact **p = 3.86e-05**.
Packed 67 → 88 (r@10 0.4889), p = 1.69e-07 against the deterministic BM25
control. The flip moves the shipped default from cell (83) to cell (113).

No gate, no flag-behind-experiment, no opt-in was added. The only escape hatches
are the two env vars that already existed, and they exist because the eval
harness needs deterministic control arms.

---

## 1. The full flip surface

| file:line | before | after |
|---|---|---|
| `crates/memphant-core/src/lib.rs:612-631` | `#[default] Overlap` | `#[default] Bm25Code` — this is the single source of truth; `MemoryService::new` picks it up via `service.rs:3522`, and the free `recall()` via `lib.rs:6228` |
| `crates/memphant-runtime/src/lib.rs:570-580` | `None \| Some("overlap") => Overlap` | `None \| Some("bm25-code") => Bm25Code`; `overlap` is now an explicit opt-out |
| `crates/memphant-runtime/src/lib.rs:1-17` | module doc | states the shipped `bm25code_dense` default and its p-value |
| `crates/memphant-eval/src/main.rs:458` | `LexicalScorer::Overlap` | `LexicalScorer::default()` — the bench cannot drift from the server |
| `crates/memphant-eval/src/main.rs:764` | usage says `default: overlap` | usage says `default: bm25-code` |
| `crates/memphant-eval/src/bench_lme.rs:123` | doc says default Overlap | doc says default Bm25Code |
| `crates/memphant-eval/src/lib.rs:2040` | `LexicalScorer::default()` hard-wired | `case.lexical_scorer.unwrap_or_default()` — new per-case override, absent ⇒ shipped default |
| `scripts/code_lane_run_memphant.py:655` | `--embed-model default="off"` | `default="small"` — the harness's default arm now measures the shipped product |
| `scripts/code_lane_run_memphant.py:682` | help says "omit for the default overlap arm" | help says "omit to inherit the server's shipped bm25-code default" |
| `scripts/code_lane_run_memphant.py:870` | provenance records `or "overlap"` | records `or "bm25-code"` — records what RAN, not the flag |

Unchanged on purpose:

- `MEMPHANT_EMBEDDINGS` semantics. `off`/`noop` is the dense escape hatch and it
  already worked; the CI `fastembed-off` leg uses feature-off builds, not this var.
- `crates/memphant-eval/src/main.rs:472` — `--embed-model` already defaulted to
  `small`. The chat-lane bench was never dense-off.
- The BM25 algorithm itself. See §4 — I deliberately shipped the arm that was
  measured rather than a better-looking one that was not.

**Env surface added: zero.** `MEMPHANT_LEXICAL_SCORER=overlap` and
`MEMPHANT_EMBEDDINGS=off` both pre-existed.

---

## 2. Mechanism liveness — the default arm is not inert

An inert pass and a neutral pass produce the same number and mean opposite
things, so `scripts/dense_default_liveness_probe.sh` runs **two arms against the
same scratch database and requires them to disagree on every axis**. Six
episodes with deliberately shared vocabulary; one paraphrase query
(`"what stops a gradual release when too many requests are failing?"`) that
shares **no content word** with its target
(`"The staging rollout halts when the canary error budget is exhausted."`).

| axis | default arm (no env) | control arm (`MEMPHANT_EMBEDDINGS=off MEMPHANT_LEXICAL_SCORER=overlap`) |
|---|---|---|
| `memphant.embedding` rows written | **6** | **0** |
| compiled units (ingest completeness) | 6/6 | 6/6 |
| trace `feature_flags` | includes **`lexical_scorer:bm25-code`**, `vector_enabled` | no bm25 flag, `vector_disabled` |
| `channel_runs` vector stage | `completed` | `disabled` |
| candidates on the `vector` channel | **6**, max channel_score **0.641464** | **0** |
| rank-1 item for the paraphrase query | **the correct target** | wrong item (`"Invoices are reconciled nightly…"`) |

Every one of those six axes moves, and moves in the direction that says the
mechanism ran. The trace flag `lexical_scorer:bm25-code` is emitted by
`LexicalScorer::flag()` and is only reachable when the BM25 pass actually
replaced the overlap passes; the non-zero `vector` channel score is only
reachable when a query vector was embedded AND matched stored vectors under the
same embedding profile. The rank-1 flip is a single anecdote, not a
measurement — it is reported as illustration of the mechanism, not as evidence
of effect size. The effect size is §0's table.

Reproduced twice (independent scratch DBs, ports 39421/39423) with identical
flags, identical `max_vector_score`, identical rank-1 outcome.

---

## 3. Cost measured, not asserted

Worker compile wall-clock, six episodes, same machine, same scratch DB, back to
back:

| run | default (dense) | control (dense off) | ratio |
|---|---:|---:|---:|
| 1 | 4305 ms | 2157 ms | **1.996×** |
| 2 | 6383 ms | 3114 ms | **2.050×** |

This independently reproduces the ~2.09× ingest wall-clock figure the flip was
authorised against. Accepted under the stated priority order (Accuracy > Cost);
not re-litigated. Note this is a *small-n, cold-ish* measurement on six
episodes — it measures the per-batch embed cost, not amortised throughput on a
large corpus, and the second run is slower on both arms because the machine was
loaded. The **ratio** is the stable quantity; the absolute milliseconds are not.

Recall latency was not separately measured. The vector channel was already
running on the served default before this change, so recall-side cost is
unchanged by the flip; only the lexical pass changed, and BM25 over the
candidate pool replaces two overlap passes rather than adding a third.

---

## 4. The one thing I could not make clean: BM25 does not read `contextual_chunks`

**This is the honest caveat, and it is a real product gap, not a test nit.**

`token_set_overlap_score` (`crates/memphant-core/src/lib.rs:10774-10784`) is the
**only** scoring pass that reads `unit.contextual_chunks`. Every BM25 variant
replaces BOTH overlap passes (`lib.rs:7270-7276`) and `bm25_unit_scores` scores
**unit bodies only** (`lib.rs:10857-10906`). Consequence: under the new default,
a unit whose **body** does not match the query but whose **chunk** does is not a
candidate at all. Chunk-only candidacy is lost.

This surfaced as **five test failures, all one mechanism**:

| test | disposition |
|---|---|
| `memphant-core recall_trace_golden::contextual_chunk_recall_finds_source_unit_and_traces_flag` | rewritten to run BOTH arms: it now **asserts the default arm returns nothing** and then proves the chunk channel under `Overlap`. The gap is pinned, not hidden. |
| `memphant-core cross_reranker::oversized_chunks_are_sub_split_so_a_tail_needle_survives_truncation` | selects `LexicalScorer::Overlap` explicitly — it is a rerank-sub-split test whose fixture happens to depend on chunk candidacy; testing two things at once was the bug. |
| `memphant-eval eval_contract::oracle_suite_runs_and_verifies_load_bearing_labels` | the failing case is `contextual_chunk_breaker`; the fixture now names `lexical_scorer: overlap`. The other 9 oracle cases still run on the shipped default. |
| `memphant-eval eval_contract::verify_golden_accepts_whole_corpus_directory` | same fixture, same fix |
| `memphant-eval eval_contract::sampled_public_rung4_suite_proves_contextual_chunk_delta` | rung 4 exists to prove the chunk delta; both its fixtures now name `overlap`, because the shipped scorer cannot express that delta at all |

**None of these were passing vacuously before** — each genuinely exercised the
chunk path and each genuinely stopped doing so under BM25. What changed is that
the dependency is now *named in the fixture* instead of inherited silently.

**Why I shipped it anyway.** The measured code-lane arms ran with runtime
contextual chunks ON and with this gap present, and `bm25-code` still beat
`overlap` **89 vs 40** dense-off and **113 vs 83** dense-on. On the one lane
where we have real evidence, the chunk-candidacy loss is already priced in and
the flip is strongly net-positive. Making BM25 chunk-aware would be a *different
retrieval algorithm from the one that was measured*, and inventing one to make
tests green is exactly the failure mode the instrument register was written
against.

**What is genuinely unverified.** The docs plane. C2 was dropped, so there is no
docs-lane arm, and the docs plane is precisely where chunk-only candidacy would
matter most (~80% of prod `contextual_chunks` exceed 512 tokens per the
rerank-granularity work). **I cannot tell you what this costs on docs, because
nobody has measured it.** Recommended follow-up, in priority order:

1. Run an `overlap` vs `bm25-code` arm on a docs/resource-chunk corpus with
   chunks live. That is a $0 retrieval-only comparison.
2. If it regresses, the fix is one function: extend `bm25_unit_scores` to index
   `body + contextual_chunks` per document. That is a new arm, and it must be
   measured, not assumed.

---

## 5. Gate status

| gate | result |
|---|---|
| `cargo test --workspace --no-fail-fast` | **GREEN** (the known `rung12_l4_exhaustive_suite_proves_raw_episode_delta` is `ignored` — paid rung, not the wiped-fixture failure) |
| `cargo clippy --all-targets --all-features -- -D warnings` | **GREEN** |
| `cargo fmt --check` | **GREEN** |
| `pytest tests/` | **1109 passed, 3 failed** — all three fail identically on a clean `git stash` of this branch: two evidence-contract retrofit assertions and one `playwright: command not found`. **Pre-existing, not caused here.** |
| CI leg `cargo test -p memphant-eval --no-default-features` | **GREEN** |
| CI leg `cargo build -p memphant-server --no-default-features` | **GREEN** |
| liveness probe (scratch DB, ×2) | **GREEN**, zero `problems` |

**CI implication.** The `fastembed-off` leg is a *cargo-feature* off-switch, not
an env one, and it still compiles and passes: `default_embedder()` falls through
to `NoopEmbedding` with its loud warning when the feature is absent
(`runtime/src/lib.rs:391-396`). The lexical-scorer flip is feature-independent,
so the off-leg exercises `bm25-code` **without** a vector channel — which is a
configuration nothing measured, but it is a build-honesty leg, not a scoring
leg. The env escape hatch (`MEMPHANT_EMBEDDINGS=off`) remains exercisable and is
exercised by `worker_once.rs:88`, `http_verbs.rs:240`, `mcp/src/lib.rs:937` and
by the liveness probe's control arm.

---

## 6. Base defect fixed in passing

`crates/memphant-worker/src/main.rs:124` now prints
`drain completed=N failed=N retried=N deferred=N`, but
`scripts/gate_runtime.py:drain_worker` matched only the bare `completed=N` form
and raised `worker drain completion output is malformed` before any probe could
run. Fixed to accept both forms **and to raise on a non-zero `failed=` count** —
the counts exist so "drained nothing" is distinguishable from "failed
everything", and parsing-then-discarding them would re-collapse that distinction
one layer up. Covered by
`tests/test_gate_runtime.py::test_drain_worker_accepts_the_tick_honesty_line_and_raises_on_failed_jobs`.

The liveness probe carries the same contract independently: it greps the drain
line for `failed=0`, asks the database for `queued|running` job count, and
asserts 6/6 compiled units per arm — so the numbers in §2 and §3 are not from a
partially compiled corpus.

---

## 7. Chat-lane (LME-S) non-regression — passes, and the strong evidence already existed

$0 throughout: local `fastembed` bge-small, retrieval-only `bench-lme`, no
reader, no paid model, ephemeral scratch DB per arm. Dense is ON in **both**
arms — the only thing that moves on the chat lane is the lexical scorer, because
`bench-lme` already defaulted to `--embed-model small`.

**Primary evidence (pre-existing, n=120, and I did not need to spend to get it):**
`docs/build-log/artifacts/track-r/track_r_phase1r_lme_s_nonregression.json`,
arm `lme_s_n120_seed1`, `--embed-model small`, k=10, graded n=111 after
abstention exclusion:

| | overlap | bm25-code | both | arm-only | control-only | exact p |
|---|---:|---:|---:|---:|---:|---:|
| @5 | 66 (0.5946) | **75 (0.6757)** | 65 | **10** | **1** | **0.0117** |
| @10 | 66 (0.5946) | **75 (0.6757)** | 65 | **10** | **1** | **0.0117** |

That is not merely a non-regression: on the chat lane `bm25-code` is a
**significant improvement**, +9 net questions, 11 discordant pairs (above the
n_d ≥ 6 floor the instrument register sets for a test that can reject at all).

**Confirmatory re-run on the flipped default**, this branch, n=20 seed 1 k=10,
artifact `docs/build-log/artifacts/dense-default-on/lme-s-n20-nonregression.json`:

| | overlap | bm25-code | both | arm-only | control-only | discordant | exact p |
|---|---:|---:|---:|---:|---:|---:|---:|
| @5 | 11 | 12 | 11 | 1 | 0 | **1** | 1.0 |
| @10 | 11 | 12 | 11 | 1 | 0 | **1** | 1.0 |

**This n=20 run is a smoke, not a test, and must not be cited as one.** One
discordant pair cannot reject at any effect size (the two-sided exact binomial
has no rejection region below n_d = 6 — instrument register §0.2). Its value is
narrow and specific: it confirms the flipped default runs end-to-end on the chat
lane, that `lexical_scorer` is recorded as `bm25-code` in the arm report and
`overlap` in the control report (so the lever is threaded, not inert), and that
the direction of the single flip is *toward* bm25-code, consistent with n=120.
No regression was observed on any question.

Cost: 2 × ~13 min wall-clock, $0.

---

## 8. What I could NOT verify

1. **The docs plane under the new default.** See §4. No arm exists; C2 was
   dropped. This is the one place the flip could plausibly cost accuracy and I
   have no number for it.
2. **Any lane other than code and chat.** Preference/user-learning, procedural,
   temporal/state and forgetting have no `overlap` vs `bm25-code` arm. Three of
   those had no banked paired result at all as of the instrument register.
3. **The `fastembed`-off *scoring* configuration.** CI's off-leg builds and
   tests, but `bm25-code` with a Noop embedder is a combination nothing has
   measured. It is a build-honesty leg, not a product configuration.
4. **Recall-side latency.** Not measured. Argued unchanged in §3 (the vector
   channel was already live; BM25 replaces rather than adds a pass), but argued
   is not measured.
5. **Large-corpus ingest cost.** §3's 2.0× ratio is six episodes on one machine.
   The ratio reproduced twice, but throughput at corpus scale was not measured.
