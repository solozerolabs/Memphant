# MemPhant - SOTA Ladder and Validation Plan

## 0. Rule

MemPhant reaches SOTA by running a fixed loop:

```text
implement the next smallest lever
  -> run cheap oracle/golden evals
  -> inspect traces
  -> run sampled public benchmarks
  -> promote only if quality improves without breaking safety/cost
  -> repeat until full public benchmark score is SOTA or the trace identifies the next lever
```

No benchmark miss is allowed to end with "unknown." Every miss is assigned to a trace field, a lever, and a next implementation.

## 1. Benchmark Targets

The `baseline status` column states whether the comparison target is **independently reproduced** or only **vendor-reported** — SOTA may only be measured against the reproduced column. A model swap alone moves these scores ~10 points, so "beat the leaderboard" is never the bar.

| Target | Why it matters | Baseline status | SOTA proof requirement |
|---|---|---|---|
| **STATE-Bench** *(primary)* | production-like stateful enterprise tasks; reliability/cost/UX; **memory-agnostic — leaderboard live + EMPTY as of 2026-07-02** (formal submission surface; first credible submission takes the visible slot) | reproducible (Microsoft, open) | show memory improves task success, **pass^5**, efficiency, UX — a *first-mover proof point*, claimed precisely: "first published memory-system result + an N-point paired-ablation-attributed delta vs the no-memory baseline". A memory-agnostic benchmark confounds model/scaffold/memory — only MemPhant's own paired ablation attributes the delta to the substrate, so the headline is the attributed delta, never "SOTA on an empty board" |
| LongMemEval-V2 | environment-specific long-term agent memory, five abilities, accuracy + latency | reproducible (open repo) | beat strongest *reproduced* baseline or publish a Pareto-frontier win at lower cost/latency |
| BEAM | memory under 100K/1M/10M context pressure | **mostly vendor-reported** (board run by a competitor) | improve the accuracy-speed-context frontier vs a *reproduced* baseline; the leaderboard's top number is `vendor_reported` and cannot be the bar |
| AgentDojo + corroboration-farming/poisoning suite | tool-using security; AgentDojo tests *tool-call* injection (near-saturated), our suite tests *persistent memory* poisoning | reproducible | no critical memory-poisoning bypass; the independent-source corroboration gate (`04` §5) holds; report ASR |
| **MemSyco-Bench (restraint)** — *substituted for OP-Bench / PS-Bench, see below* | over-personalization + intent-legitimation — over-retrieval is a *measured* harm (the axis most teams skip) | reproducible (MIT, pinned: `benchmarks/manifests/memsyco.lock.json`) | **launch gate (thresholds unchanged):** must not drop >15% vs a memory-free baseline, else the `05` §1.5 relevance gate is mandatory; sample ≥ 50; the paired-delta CI upper bound must sit at or below the same 0.15; pinned-block content (`04` §12) is explicitly in-scope for this gate (R88) |
| **GateMem (multi-principal governance)** | utility + access control + reliable forgetting in shared-memory agents — the wedge stated as an open problem ("no method simultaneously achieves" all three, arXiv:2606.18829) | reproduce-first (132★ repo; unproven harness) | **conditional launch gate (R90): gates NOTHING until first successful internal reproduction in MemPhant's harness** (the Zep-58.44% lesson); once reproduced, the bar is simultaneous pass on all three axes — the only public benchmark where the wedge IS the score |
| Internal Syndai golden set | real L0/L1+, correction, forget, citation, project-scope contract cases | internal | meets or improves the internal golden baseline |

**Restraint-instrument substitution (2026-07-30, `26` §3 D-2026-07-30c).** The
restraint gate's *instrument identity* was self-contradictory and would have
rejected its own passing run: this table and
`docs/launch/restraint-launch-scorecard.json` named OP-Bench/PS-Bench, and
`tests/test_restraint_launch_gate.py` asserted
`scorecard["benchmark"] in {"op-bench","ps-bench"}` on `pass` — while the only
instrument actually pinned, adapted, and executed is **MemSyco-Bench**. A
passing MemSyco run would have failed the gate's own contract test. Resolved by
substituting the instrument and recording why:

- **OP-Bench (arXiv 2601.13722) is not runnable.** The paper specifies 1,700
  reviewed instances and its irrelevance/sycophancy/repetition metrics but
  exposes no official dataset, scorer, code repository, or immutable release
  (`docs/build-log/2026-07-13-temporal-benchmark-adapter.md`). Per `26` §3, no
  substitute dataset or locally reconstructed scorer may be *labeled* OP-Bench.
- **PS-Bench (arXiv 2601.17887) is not licensable.** Code, data, and the native
  Longformer classifier are public at `MuyuenLP/PS-Bench@210e72ea`, but the
  repository declares **no license**, so its data and code cannot enter MemPhant.
  The 2026-07-04 PS-Bench scorecard is additionally invalidated on its own terms
  (`status: invalid_synthetic_fixture`, answer-seeded fixtures).
- **MemSyco-Bench (arXiv 2607.01071) is runnable, MIT, and pinned.** Repo
  `XMUDeepLIT/MemSyco-Bench@c31e2c85`, 1,550 samples across five tasks, native
  scorer pinned by sha256 in `benchmarks/manifests/memsyco.lock.json`; runner
  `scripts/run_restraint_bench.py`. Its five tasks measure the same construct
  this gate exists for — objective-fact judgment under memory pressure,
  contextual scope control, memory-vs-evidence conflict, valid-memory selection,
  and personalized memory use — i.e. over-retrieval harm and memory
  over-trust. A five-task smoke passed 5/5 at **$0.23557035**
  (`docs/build-log/2026-07-15-memsyco-smoke.md`); that is a smoke, not the gate.

**Scope of the substitution — binding.** Only the **instrument identity**
changed. The substantive thresholds are untouched: relative drop vs a
memory-free baseline **≤ 0.15**, sample **≥ 50**, paired-delta **CI upper bound
≤ 0.15**, `05` §1.5 relevance gate mandatory on breach, pinned-block content
in-scope. The canonical scorecard value is `"benchmark": "memsyco-bench"`.
OP-Bench and PS-Bench remain admissible instruments for this gate and stay in
the allowed set — they become *runnable* gates only if a complete, legally
usable official release appears; until then they are cited as prior art, never
run. This substitution does **not** flip the restraint checkbox: no full MemSyco
gate run has been executed.

**Promotion-provenance rule (2026-07-09):** Promotion evidence must be produced by the packaged Postgres-backed runtime against pinned real corpora with recorded hashes and an executed reader/scorer. Synthetic fixtures gate regressions, never promotions.

The full multi-axis **benchmark portfolio** (no single source of truth — outcome / long-horizon / scale / longitudinal / restraint / interactive-episodic / embedding-selection / procedural / systems-cost) is owned by `12` §2.0; this table is the SOTA-claim subset.

Primary-source anchors:

- LongMemEval-V2: <https://arxiv.org/abs/2605.12493>
- BEAM ("Beyond a Million Tokens"): <https://arxiv.org/abs/2510.27246> (the `agentmemorybenchmark.ai` board is a Vectorize-operated leaderboard → `vendor_reported`)
- STATE-Bench: <https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/>
- OWASP Agent Memory Guard (pre-1.0 incubator): <https://owasp.org/www-project-agent-memory-guard/>

**The ladder reads the profile, it does not run a second eval.** Every rung's *advance-when*/*disable-when* below is a per-axis paired delta produced by one command — `memphant-eval profile --compare-to <baseline>` (`12` §2.0a). A rung promotes only when its axis's paired-delta CI excludes zero; the control baselines (rung 6 no-edges/filesystem) and the restraint launch-gate are axes in that same profile. So "advancing a rung" = "the profile moved on that axis," archived with the run.

## 2. Ladder Overview

| Rung | Turn on | Implementation | Advance when | Disable when |
|---|---|---|---|---|
| 0 | trace/eval harness | durable trace schema, golden runner, scorecard runner | traces explain every golden miss | trace gaps remain |
| 1 | raw episodes + citations | episode/resource store, citation ledger, candidate whitelist | citation validity >= 99% on golden | citation forgery/deletion fails |
| 2 | write/extraction policy | first-class memory writes, ADD-only extraction, source-preserving semantic units | answer-bearing units exist before retrieval | extraction overwrites raw evidence or stores noise |
| 3 | hybrid baseline | exact/entity + FTS + vector + RRF | answer-bearing recall@k passes internal Syndai contract fixtures | candidate misses dominate |
| 4 | contextual chunks | extraction job writes contextual chunk metadata tied to raw episodes | top-k improves on LME-V2/BEAM samples | chunk bloat hurts latency/cost without recall gain |
| 5 | temporal validity | bitemporal fact fields, stale/supersession edges, recency windows | stale fact failures drop on golden and STATE-style cases | current evidence is suppressed incorrectly |
| 6 | relational edge expansion | 1-hop dynamic links for contradiction/source/procedure/resource lineage | multi-hop/resource recall **beats the no-edges + filesystem control baselines** by ≥3 pts on STATE-Bench multi-hop + LME-V2 (`05` §9.1) — edges must earn their keep (Mem0 dropped its graph; Letta filesystem beat it) | edge expansion adds noise or latency, or fails to beat the controls |
| 7 | context packing + abstention | budgeted evidence pack with negative-evidence and uncertainty labels | retrieved evidence produces correct answer or abstains | pack hides decisive evidence or over-abstains |
| 8 | bounded rerank | deterministic or provider-backed reranker over capped candidate set | rank-sensitive failures drop with p95 within budget | rerank cost/latency outweighs gain |
| 9 | query decomposition | traceable subqueries for composite memory questions | composite LME-V2/STATE cases improve | subqueries retrieve uncited or irrelevant evidence |
| 10 | procedural memory | validated procedure/failure-pattern units and replay checks | STATE-Bench task success/pass^k improves | unsafe procedure reuse appears |
| 11 | DSR decay | fixed-prior DSR update fold over the day-one `review_event` ledger (`04` §8.2; v1 ranks by plain recency/exponential until this rung) | FSRS beats plain exponential decay on an **internally-run MemoryStress-style longitudinal suite executed in MemPhant's own harness** (`04` §8, `12` — running the corpus ourselves = internally measured; the vendor's published leaderboard number can never be the gate, R82) — short benchmarks can't exercise decay; stale/noisy memory decreases without losing durable facts | FSRS doesn't beat exponential on the internal longitudinal suite (→ keep exponential); identity/project facts decay incorrectly |
| 12 | L4 Deep recall | sandbox/file/raw-episode agentic recall mode | accuracy ceiling improves on hard LME-V2/BEAM samples | latency/cost not worth Pareto claim |
| 13 | learned rerank/DSR | train/tune on archived traces only after data floor | paired evals beat fixed rules | overfits or fails held-out cases |
| 14 | external graph DB escape hatch | implement graph adapter only if SQL edge traces prove bottleneck | multi-hop benchmark improves beyond SQL edge expansion | no material win over relational edges |
| 15 | inferred-belief composition | reflect-stage abstraction inference minting belief candidates (`derived_by: composition` trace field; mechanism of record in `24` §2.3, R89) | corroborated-promotion precision on inferred beliefs holds AND the over-personalization score does not regress (production precedent: Syndai's guardrailed behavioral inference) | inferred beliefs promote wrongly, or over-personalization/sycophancy signal regresses (OP-Bench taxonomy) |

Rungs 0-12 are first-public-architecture **capability contracts** — their schema, flags, modes, and trace fields are frozen from day one, and each rung's *behavior* is built when the ladder reaches it (R73; the v1 built spine is rungs 0–3 + the alpha set, `29` §2a). Rungs 13-15 are data/trace-gated implementation swaps and additions behind frozen interfaces.

## 3. Lever Implementation Map

| Lever | Crate/module owner | Data required | Trace fields | Tests |
|---|---|---|---|---|
| trace harness | `memphant-eval`, `memphant-core` | eval cases, config hash | `trace_schema_version`, `engine_version`, `feature_flags` | trace schema snapshot, golden runner smoke |
| raw episodes | `memphant-core`, `memphant-store-postgres` | `episode`, `resource`, object refs | `source_episode_id`, `source_resource_id`, `citation_id` | retain/correct/forget/citation tests |
| write/extraction policy | compiler + store | raw event, extracted unit, source span | extraction policy, write decision, rejected reason | noisy-write, overwrite, provenance tests |
| exact/entity | `memphant-core` | aliases, subjects, resources | resolver, matched IDs, miss reason | entity exact match tests |
| FTS | `memphant-store-postgres` | generated `tsvector` | tsquery, lexical rank | lexical oracle tests |
| vector | `memphant-store-postgres` | embedding profile and vectors | embedding profile, distance, rank, `filter_selectivity` | embedding dimension/index tests; **index_strategy** chosen per dims (`halfvec` cap 4,000: ≤4k→`hnsw_full`, >4k→`hnsw_subvector`; `hnsw_binary` only ≥~1024-d); `halfvec`+`binary_quantize` two-phase as the cost/latency lever (`02` §2.1a) |
| RRF | `memphant-core` | channel ranks | per-channel rank, fused rank | deterministic fusion tests |
| contextual chunks | background compiler | chunk text, context header, source span | chunk ID, parent episode/resource | chunk source/citation tests |
| temporal validity | `memphant-core`, store | valid/transaction times | validity window, stale discard reason | stale/supersession golden cases |
| edge expansion | `memphant-core`, store | `memory_edge` rows | edge kind, depth, expansion IDs | contradiction/resource/procedure edge tests |
| context packing | `memphant-core` | candidate pack, budget, citations | included IDs, dropped IDs, uncertainty labels | citation pack and abstention tests |
| rerank | `memphant-core` | capped candidate pack | reranker ID, input count, output rank | rank regression tests |
| query decomposition | `memphant-core` | query features/subqueries | subquery IDs, parent query hash | composite query oracle tests |
| procedural memory | compiler + store | procedure units, validation status | procedure ID, validation state | replay/procedure safety tests |
| DSR decay | compiler + store | review/reinforcement events | difficulty, stability, retrievability | decay monotonicity and retention tests |
| L4 Deep | `memphant-eval`, service worker | raw episodes/resources/files | mode, sandbox ID, gathered evidence IDs | Deep-mode benchmark tests |

## 4. Activation Rules

| Symptom | Required evidence | Turn on next |
|---|---|---|
| answer-bearing unit was never written | write trace shows raw episode has no derived unit | write/extraction policy, contextual chunks |
| answer-bearing memory absent from top-k | candidate trace misses source episode/resource | contextual chunks, query decomposition, L4 Deep |
| answer-bearing unit was never written AND the query recurs | write trace: raw episode has no derived unit for a repeating `query_features_hash` | `reextract_on_miss` — surgical per-episode, query-conditioned re-extraction (`04` §9, R80) before any global extraction-policy change |
| answer appears but rank too low | candidate exists but fused/rerank rank loses | RRF tuning, bounded rerank, learned rerank |
| right candidate retrieved but answer wrong | context trace includes candidate but answer trace fails | context packing, citation packing, abstention |
| stale memory wins | stale/validity trace shows old row active | temporal validity, DSR decay, contradiction edges |
| multi-hop/resource evidence missing | direct candidates found but related evidence absent | edge expansion, L4 Deep |
| procedure repeated incorrectly | task trace repeats failed steps | procedural memory promotion/replay |
| poisoning memory retrieved | low-trust candidate passes filter | trust policy, quarantine, high-risk suppression |
| latency high | stage latency shows dominant stage | caps, materialization, cache, mode split |
| cost high | cost trace shows rerank/L4/extraction spend | cheaper model, tighter candidate cap, batch/offline work |

## 5. Validation Pyramid

| Layer | Command | Must prove |
|---|---|---|
| unit | `cargo nextest run --all-features` | policy, fusion, DSR, ID/time, deletion primitives |
| doc/API | `cargo test --doc` and OpenAPI/MCP schema snapshots | examples compile and public contracts do not drift |
| DB | `memphant db lint --all-providers` | tenant IDs, indexes, RLS/grants, search_path, vector dimensions |
| golden | `memphant-eval run examples/evals/golden.yaml` | real regressions and trace assertions stay green |
| golden-verify | `memphant-eval verify-golden examples/evals/ --all` (nightly whole-corpus) | `answer_bearing_ids` labels stay load-bearing — the oracle-rot guard (`05` §4.0) |
| ablation | `memphant-eval ablate benchmarks/nightly-sampled.yaml` | lever deltas are known |
| profile | `memphant-eval profile --compare-to <baseline>` | the whole multi-axis paired-delta profile in one command (`12` §2.0a) — what every rung's advance/disable reads |
| sampled public | `memphant-eval run benchmarks/nightly-sampled.yaml --archive-traces` | public benchmark trend moves correctly |
| security | `memphant-eval security benchmarks/security.yaml` | no critical poisoning/leak/delete failures |
| release | `memphant-eval run benchmarks/release.yaml --archive-traces` | full scorecard claim is reproducible |

All commands are expected repo commands for the future MemPhant repo; Syndai docs use them as the implementation target.

## 6. SOTA Scorecard Rules

A SOTA claim requires:

- fixed MemPhant commit
- fixed benchmark version
- fixed model, embedding model, reranker, and extraction model
- feature flags and retrieval config
- archived retrieval traces
- accuracy with confidence interval
- paired comparison against reproduced baseline
- p50/p95 latency
- token/context cost
- storage footprint
- security suite result
- deletion completeness result
- caveat for any vendor-reported competitor number

If MemPhant is not best on raw accuracy but is on the accuracy-latency-cost Pareto frontier, the claim must say "Pareto frontier," not "best accuracy."

## 7. Baseline Reproduction Order

Reproduce baselines in this order:

1. no-memory / context-only baseline
2. FTS-only baseline
3. vector-only baseline
4. FTS + vector + RRF baseline
5. internal Syndai contract fixtures
6. public OSS competitor baseline where license and setup allow
7. vendor-reported competitor number labeled as non-reproduced

No public SOTA claim depends only on vendor-reported numbers.

## 8. Statistical Rules

- Golden evals are pass/fail gates.
- Public sampled evals report **bootstrap confidence intervals** (≥1,000 resamples); a sampled eval is "stable" only once its CI half-width is below a declared threshold (default ±2%) (`05` §8).
- Release evals use paired comparisons where the same cases can run under both configs; the paired-delta CI must exclude zero before a lever promotes.
- Promoting **multiple** levers against the same cases applies a **family-wise/FDR correction** (Holm-Bonferroni → Benjamini-Hochberg as the set grows) and **resamples CIs by cluster** (session/corpus, not case); the promoted set is periodically re-checked by **leave-one-out** for interaction effects. Methodology detail is canonical in `05` §8/§9.3.
- The SOTA bar is "beat the strongest *independently reproduced* baseline, or publish a Pareto win" — **never** "beat a vendor's blog number."
- A lever promotes only if it improves the target metric and does not regress security/deletion/tenant isolation.
- A lever with accuracy gain but unacceptable cost becomes `deep` mode only.
- A lever with no sampled gain after two independent runs is reverted or disabled by default.

## 9. Trace Archive Contract

Every benchmark archive contains:

```text
run_id
git_sha
benchmark_id
benchmark_version
case_ids
engine_version
trace_schema_version
feature_flags
model_versions
embedding_profile
reranker_id
retrieval_traces
eval_results
security_results
cost_latency_summary
```

This archive is the artifact that lets future work know which lever to pull next.

## 10. Stop Conditions

Do not keep adding systems blindly. Stop and reassess when:

- full release runs show no gain after rungs 0-12 are active
- security gates fail under the configuration that improves accuracy
- L4 Deep mode is the only winning mode and is too slow for any credible Pareto claim
- benchmark traces show the bottleneck is the downstream answer model, not memory retrieval

If that happens, narrow the public claim to the part that wins: trace/eval harness, poisoning defense, coding-agent memory, or Syndai dogfood memory.
