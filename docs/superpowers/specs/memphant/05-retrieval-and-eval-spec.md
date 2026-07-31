# MemPhant - Retrieval and Eval Spec

## 0. Doctrine

Do not build every lever first. Build the trace and ablation harness first.

If MemPhant misses a benchmark, the trace must say which lever to pull.

The full increment -> test -> increment ladder lives in `27-sota-ladder-and-validation.md`. This doc owns the retrieval/eval primitives; `27` owns the activation order and SOTA proof loop.

## 1. Retrieval Stages

```text
Stage 0: tenant/scope/privacy/trust gates
Stage 1: exact/entity lookup
Stage 2: FTS lexical candidates
Stage 3: vector candidates
Stage 4: temporal/edge expansion
Stage 5: RRF fusion
Stage 6: bounded rerank
Stage 7: context assembly and citation packing
Stage 8: trace write
```

The first public build implements every stage **contract** — the stage slot, its feature flag, and its trace fields exist from day one, and a flag-disabled stage passes through and traces as such. The expensive stage **behavior** is built at its `27` rung (Stage-4 edge expansion → rung 6; Stage-6 rerank → rung 8; decomposition → rung 9; L4 → rung 12); the rungs 0–3 spine (gates, exact, FTS, vector, RRF, packing, trace) is v1 built behavior (R73). Fast mode caps Stage 4/6 work; balanced/deep and benchmark modes expand them.

## 1.1 Stage Contracts

| Stage | Contract | Required trace fields |
|---|---|---|
| 0 gates | reject/limit by tenant, scope, privacy generation, trust policy | policy version, denied selectors, allowed scopes |
| 1 exact/entity | resolve explicit IDs, aliases, subjects, resources, file paths | resolver name, matched IDs, miss reason |
| 2 lexical | Postgres FTS over units/resources/episodes | tsquery, rank, candidate IDs |
| 3 vector | embedding-profile-specific ANN/exact search; sets `hnsw.iterative_scan` per query and selects index path by `embedding_profile.index_strategy` (`02` §2.1a/b) | embedding profile, distance, candidate IDs, `filter_selectivity`, `iterative_scan_depth` |
| 4 temporal/edge | recency windows, supersessions, contradictions, source resources | edge kinds, time window, expansion depth |
| 5 fusion | weighted RRF, `k_rrf=60`, query-signal weight vector (§1.2) | per-channel `(weight, rank, contribution)`, fused rank, `weight_vector_id` |
| 6 rerank | deterministic/ML reranker over fused top-`N_rerank` (default `10×k`, capped 100 fast / 200 balanced) → top-`k` | reranker ID, `rerank_input_count`, `rerank_overfetch_ratio`, output rank |
| 7 assembly | budgeted evidence pack (§1.3) with citations, contradiction caveats, abstention | budget, dropped IDs + reasons, `inclusion_reason`, `abstention_signal` |
| 8 trace | durable trace write | trace ID, config hash, engine version |

The stage contract is more important than the first implementation. It preserves levers.

## 1.2 Fusion Weighting + Context Assembly

**Fusion (Stage 5)** is weighted RRF — `fused = Σ_channel w[ch]·1/(60 + rank_ch)`. The interface is frozen; the weights are a checked-in table (learned weights are rung-13, data-gated). The vector is keyed by `query_features` deterministically (no hot-path LLM):

| Query signal | exact | lexical | vector | temporal | edge |
|---|---|---|---|---|---|
| explicit ID / alias / file-path | **3.0** | 1.5 | 1.0 | 0.5 | 1.0 |
| error text / command / verbatim | 1.5 | **3.0** | 1.0 | 0.5 | 0.5 |
| paraphrastic / conceptual (default) | 1.0 | 1.0 | **2.0** | 0.5 | 0.5 |
| "current" / recency-anchored | 1.0 | 1.0 | 1.5 | **2.5** | 1.0 |
| multi-hop / "why" / "related to" | 1.0 | 1.0 | 1.5 | 0.5 | **2.5** |
| how-to / task-procedure ("how do I…", imperative task phrasing) | **2.5** | **2.0** | 1.0 | 0.5 | 1.0 |

Mode scales the floor (`fast` zeroes edge+temporal-expansion); a flag-disabled channel contributes weight 0 (RRF is rank-relative, so a dropped channel cannot inflate the others). "fixed-uniform vs query-weighted RRF" is an ablation arm (§9). The **how-to row (R91)** upweights the task-keyed exact+lexical channels procedural units ride (§1.3) — it pairs with the §1.4 "how to:" embed prefix, which optimizes the vector channel but upweights nothing; before this row, no signal favored procedural recall on a how-to query.

`k=60` is a robust default, not optimal — the original RRF result found a flat optimum across `k∈[20,100]` (Cormack et al., SIGIR 2009). **Migration path (data-gated):** once a labeled eval set exists, **tuned convex combination of normalized scores beats RRF** in- and out-of-domain and needs only ~40 labeled queries to tune its one parameter (Bruch et al., arXiv:2210.11934; Elastic replication). RRF discards score magnitude (a 0.99 and a 0.51 cosine contribute equally), so the migration is most worthwhile for the score-normalizable vector+FTS pair; RRF's rank-only robustness stays valuable where the four signals are genuinely incomparable. Keep RRF k=60 as the cold-start default; the CC swap is a rung-13 lever behind the same frozen fusion interface.

**Context assembly (Stage 7)** is deterministic (no LLM) — it owns the "retrieved-but-answer-wrong" failure (§6), so it is a named algorithm:

```text
pack(candidates, budget):
  0. pinned block first (04 §12): the scope's current block under its OWN hard token sub-budget —
     guaranteed presence, explicit `pinned_block_truncated` label when over budget, rendered as data,
     never dropped by the relevance gate (label-only). inclusion_reason: pinned_block.
  1. dedup ACROSS channels (one unit topping vector+lexical = ONE item carrying both provenances;
     a unit is NOT redundant for matching twice). subject_key near-dups collapse to "see also".
  2. order: citeable units first (rerank_rank, trust, recency); episode/resource snippets attach UNDER
     their citing unit, never free-floating.
  3. greedy fill to budget, reserving a floor for citeable semantic/procedural before verbose evidence.
  4. contradiction: any admitted unit with an unresolved `contradicts` edge surfaces BOTH sides + caveat.
  5. abstention: emit negative-evidence when no unit clears the per-kind confidence floor, OR only
     low-trust belief answers a fact query, OR the top evidence is an unresolved contradiction.
  6. emit context_items[] (inclusion_reason) + dropped_items[] (discard_reason).
```

Over-abstention is the opposite failure — abstention rate is a metric (§5) and an ablation arm (naive top-k vs budgeted-pack-with-abstention).

## 1.3 Per-Kind Retrieval + Adaptive Cascade

Retrieval is **kind-aware** (the `04` §1.1 "retrieval default" expanded to stage behavior); kind gates run *inside* the channel stages so an expired fact or quarantined belief never reaches fusion:

| Kind | Channels | In-stage gate | Pack-admission floor |
|---|---|---|---|
| episodic | exact+lexical+vector+temporal | scope/trust | low (supporting evidence) |
| semantic | exact+lexical+vector | **validity**: drop `valid_to < now` unless query is historical | high (low-confidence → abstain) |
| procedural | exact+lexical (task-keyed) | **`validated`/`active` only** | high + `validated` to drive action |
| belief | vector+lexical | excluded from default recall unless explicitly requested; never high-risk args | request-gated, always trust-labeled |
| preference | exact(subject key)+lexical | **chain head only**: the open generation on the preference key; a superseded generation is excluded exactly as `04` §7.3a's bitemporal predicate excludes it. **No decay** (`04` §13.2a) | high. *Specced-but-unbuilt:* `04` §13.2a says the head is **assembled**, not ranked; today it competes in the pool like any other unit, because §13.3's hot plane does not exist |
| resource | exact(ID/hash)+vector over chunks | **in-stage ACL gate**: chunk `scope_id` ∩ `resource.acl` (scopes/trust_floor/protected), applied before ANN; denied → `dropped[]` `protected_category`/`below_trust_floor` (`04` §6.1, `06` §4.2) | medium (evidence support) |

**Adaptive mode cascade.** Recall may self-escalate `fast → balanced` within one call when the cheap pass is provably insufficient — the predictor is a deterministic rule over trace features, **zero LLM tokens** on the hot path: escalate if top `fused_score` < per-kind sufficiency floor, OR < `k_min` candidates survived gates, OR an unresolved contradiction tops the set, OR `query_features.is_composite`. `deep`/L4 is **never** auto (unbounded cost) — explicit opt-in or benchmark only. The trace records `mode_requested`, `mode_executed`, `escalation_reason`. The cascade is itself a cost ablation (always-balanced vs fast-with-escalation, reporting accuracy *and* p95).

**Query decomposition** (balanced/deep only) fires on a deterministic compositeness check (≥2 of: multiple entity hits, comparative/causal connector, multi-constraint conjunction, temporal relation). Subqueries are derived **structurally** — one per entity/conjunct + the bridge as an edge-expansion subquery — **never HyDE** (no hypothetical-document synthesis that could hallucinate an out-of-corpus entity). Recombination is union-then-refuse: re-fuse once, allow a unit only if its provenance traces to ≥1 subquery; a bridge additionally requires the connecting `memory_edge`. The trace archives `subquery_ids[]` + `decomposition_reason` so a composite miss maps to "which subquery missed."

## 1.4 Retrieval-Stage Micro-Levers (the "free points")

A 2026 six-dimension ablation (MemMachine, arXiv:2604.04853 — a ground-truth-preserving system that independently validates the three-loop architecture) found that **retrieval-stage tuning, not write sophistication, dominates accuracy**, and quantified four deterministic, zero-new-system levers MemPhant should expose as named ablation arms (deltas are MemMachine's, on LongMemEval-S; re-measure on our own targets before claiming them):

| Lever | MemMachine Δ | MemPhant implementation | Trace field |
|---|---|---|---|
| **Retrieval-depth tuning** | +4.2% | make `N_rerank`/expansion depth a function of query complexity (entity hits, compositeness, temporal range), not a static per-mode cap (§1.1 Stage 6) | `optimal_depth_for_answer` (at what depth did the answer-bearing unit first appear) |
| **Context formatting** | +2.0% | Stage 7 renders evidence with date-stamped headers, speaker/`source_kind` labels, recency-ordered temporal flow — ship a reference `format_context_pack()` in the SDKs (a runtime currently formats freely) | ablation arm: naive-flat vs structured-temporal |
| **Search-prompt / query-prefix** | +1.8% | a deterministic embed-query prefix keyed by `query_features` ("user said:" / "tool reported:" / "recently:" / "how to:") before Stage 3 — **not HyDE** (no synthetic document) | ablation arm: raw-query vs prefix-optimized embedding |
| **Query-bias correction** | +1.4% | vector retrieval systematically favors verbose chunks; rebalance by `source_kind` so the top-k isn't dominated by one source (high token-density assistant/tool chunks crowding out terse user chunks) | `source_kind_distribution_in_top_k` |

These are rung-3/7 refinements within the existing pipeline — the grounding for "improve retrieval before write" (the same study: retrieval method = ~20pt swing on LoCoMo, write strategy = 3–8pt; arXiv:2603.02473, which validates MemPhant's cheap durable write). Each is a paired-CI-gated ablation (§8); promote a lever only when its paired delta CI excludes zero on a MemPhant target.

**Rerank upgrade — a *memory-tuned* cross-encoder reordering only the protected top-k.** The Stage-6 provider reranker (balanced/deep) should be a **small memory-tuned** cross-encoder, not a generic one: a generic learned reranker does *not* beat a strong off-the-shelf cross-encoder (ConvMemory arXiv:2605.28062), whereas a memory-distilled reranker does — MemReranker-0.6B beats BGE-reranker-v2-m3 (0.7150 vs 0.6708 MAP on LoCoMo) at ~200ms, with gains concentrated on the hard cases (multi-hop/temporal/numerical/low-lexical-overlap; arXiv:2605.06132). Reorder **only the protected top-k** so recall is unchanged by construction (ConvMemory v2, arXiv:2606.10842: top-10 reorder lifts MRR 0.5824→0.6560 at ~68× lower cost than full-pool). (These are single-author preprints in part — adopt only if it beats the deterministic default by ≥0.03 MRR on a MemPhant target.)

**Query-complexity-gated reflection (HyMem pattern).** The adaptive cascade (§1.3) already gates *depth* on complexity; on the `balanced`/`deep` escalation it also runs a **reflection pass** (re-read the pack, refine the query, re-retrieve once) — fired only on complex/escalated queries, never uniformly (HyMem arXiv:2602.13933, 92.6% cost cut by reserving the expensive path for hard queries). This is the same "cognitive economy" as the cascade, extended one step.

## 1.5 Calibrated Restraint (the relevance gate — over-retrieval is a *measured* harm)

The 2026 consensus shifted from "recall is everything" to **calibrated restraint**: injecting retrieved-but-irrelevant memory *actively hurts* the agent. OP-Bench (arXiv:2601.13722) found every personalized agent dropped **26.2–61.1%** vs a memory-free baseline across all 36 configs (failure taxonomy: **Irrelevance / Sycophancy / Repetition**); its fix, a lightweight relevance filter (**Self-ReCheck**), recovered **29%** of that loss. A-MAC (arXiv:2603.04549) showed admission control — store *less, cleaner* — beats a perfect-recall/low-precision store (F1 0.583 vs 0.541). So MemPhant pairs its recall-optimized cascade with a precision counterweight:

- **A relevance gate runs after Stage 7 packing, before the pack is returned:** each admitted unit must clear a relevance floor *against the query* (not just a retrieval score) — a unit that surfaced on topical proximity but does not answer the query is dropped to `dropped[]` with `reason: irrelevant`, not injected. The gate is deterministic + cheap (the abstention machinery of §1.2 extended); it never *adds* memory.
- **The "more recall is safer" assumption is false past a point.** EverMemOS shows accuracy can *exceed* annotated recall (12–20% of LoCoMo answered at zero gold-recall via redundancy) — so chasing recall of every gold unit can be wasted effort *and* harmful when it injects noise. Restraint is the dual of the §1.2 abstention: abstain when nothing clears the floor; *prune* when too much does.
- **It is a first-class metric and eval, not a vibe:** track `injected_count` / `pruned_irrelevant_count` and an **over-personalization score** (OP-Bench + PS-Bench in the portfolio, `12`). Gate: if MemPhant's OP-Bench drops >15% vs a memory-free baseline, the relevance gate is mandatory, not optional.
- **Thin-corpus amplification (new users):** the harm is *worse* with few memories — one irrelevant memory dominates a 3-item pack. The gate therefore matters most at cold start (`01` §0.1 edge cases), exactly when recall is weakest.
- **Per-consumer-model calibration (data-gated lever, Syndai-proven):** the relevance/confidence floor can be a function of *which LLM consumes the pack*, learned from that model's observed citation behavior, with a conservative default for unknown models — Syndai's production loader already calibrates its citation floor per consuming model. Ships as a flag once outcome labels (`mark`, `08`) provide the calibration signal.

## 2. Feature Flags

Every non-baseline lever is toggleable:

```text
fts_enabled
vector_enabled
entity_exact_enabled
temporal_enabled
edge_expansion_enabled
rerank_enabled
query_decomposition_enabled
contextual_chunks_enabled
write_extraction_policy_enabled
context_packing_abstention_enabled
trust_filter_enabled
decay_enabled
l4_deliberate_recall_enabled
procedure_recall_enabled
outcome_feedback_enabled
recall_delta_enabled
retrievability_probe_enabled
miss_repair_extraction_enabled
```

## 3. Retrieval Trace

Every recall emits:

```text
query
scope constraints
trust constraints
candidate IDs per channel
scores per channel
fusion score
rerank score
discard reason
context budget
citations
latency
token estimate
cost estimate
feature flags
engine version
```

This trace is the debugging tool and the eval artifact.

**The context budget is model-facing, not a whitespace-word budget.** Stage 7
charges each whole body and rendered contextual chunk with the greater of its
whitespace-word count and `ceil(UTF-8 bytes / 3)`, plus one additive separator
allowance per rendered chunk. This is intentionally conservative for compact
JSON, DOM/accessibility trees, identifiers, punctuation-heavy tool output, and
CJK; `token_estimate` records that charge. Chunk rendering is bounded by both
the whole-body estimate and the request budget, so one returned item cannot
exceed `budget_tokens`. Benchmark adapters still verify with the pinned
reader's exact tokenizer and fail closed on any truncation. Resource extraction
must hard-split an oversized paragraph or fenced block on a UTF-8 boundary,
preserve byte-exact source spans, and cover the complete resource; the episode
fan-out cap must never silently truncate a document tail. Large-document
adapters split sources into bounded resource units before reflect so complete
coverage does not turn one recall candidate into unbounded hot-path work.

### 3.1 Trace Schema

```text
retrieval_trace
  id
  tenant_id
  scope_id
  actor_id
  agent_node_id
  query_hash
  query_features
  config_hash
  engine_version
  feature_flags
  channel_runs[]
  candidates[]
  fusion_decisions[]
  rerank_decisions[]
  policy_filters[]
  context_items[]
  dropped_items[]
  citations[]
  filter_selectivity      # how selective the tenant/scope filter was on the vector stage (02 §2.1b)
  iterative_scan_depth    # how far the HNSW iterative scan ran to meet the recall floor
  consolidation_lag       # how far reflect is behind capture; non-zero => degraded recall declared (02 §3.1)
  weight_vector_id        # which Stage-5 fusion weight row was used (§1.2)
  mode_requested          # caller-requested retrieval mode
  mode_executed           # mode actually run after any auto-escalation (§1.3)
  escalation_reason       # sufficiency_floor | too_few_candidates | contradiction | composite | none
  rerank_overfetch_ratio  # N_rerank / k actually used
  abstention_signal       # Stage-7 emitted negative-evidence / abstention
  subquery_ids            # decomposition subqueries, if composite (§1.3)
  optimal_depth_for_answer        # depth at which the answer-bearing unit first appeared (§1.4 depth tuning)
  source_kind_distribution_in_top_k  # detects verbose-chunk bias in vector retrieval (§1.4 query-bias)
  delta_base_trace_id     # set when the caller requested recall(delta_since: trace_id) (R80; flag recall_delta_enabled)
  derived_by              # additive: how a packed unit was minted — extraction | composition (R89 rung; composition = inferred-belief abstraction)
  latency_ms
  token_estimate
  cost_micros
  outcome_label
```

`query` text can be redacted or hashed by tenant policy. The quality facts must remain useful even when raw text is unavailable.

**`outcome_label` has a first-class producer (R77):** the `mark` verb (`08`) posts `{trace_id, used_ids[], outcome: success|failure|corrected|ignored}` after the caller acts. Vocabulary is exactly those four values; unlabeled traces carry `null` (never a guessed label). `mark` also writes graded `review_event` rows (`04` §8.2) — the reinforcement signal the rung-11/13 levers are data-gated on — and per-unit utility facts (`22`). Outcome labels are tenant data under the same redaction policy as `query`.

### 3.2 Candidate Record

```text
candidate
  memory_unit_id
  channel
  channel_rank
  channel_score
  fused_rank
  fused_score
  rerank_rank
  rerank_score
  trust_level
  state
  valid_from
  valid_to
  source_episode_id
  source_resource_id
  discard_reason
```

Discard reasons are controlled vocabulary: `tenant`, `scope`, `privacy`, `trust`, `state`, `stale`, `budget`, `duplicate`, `rerank`, `deleted`, `invalidated`, `unknown`.

## 4. Benchmark Ladder

Full runs are expensive. Use this ladder:

| Layer | Cadence | Purpose |
|---|---|---|
| Unit/property tests | every PR | Tenant isolation, deletion, citations, scoring math. |
| Golden retrieval evals | every PR | 25-100 curated failures and regressions. |
| Retrieval-only oracle evals | every PR/nightly | Did answer-bearing memory reach top-k? No LLM judge (§4.0). |
| Sampled external benchmark | nightly/weekly | Cheap subset of LME-V2/BEAM/STATE-style cases. |
| Full external benchmark | release | Public scorecard only after subsets predict a real delta. |

### 4.0 The Retrieval-Only Oracle (labeling without an LLM)

The oracle answers one question deterministically: **did top-k contain every memory ID the answer provably depends on?** "Answer-bearing" is a property of the *fixture, asserted at authoring time*, never inferred by a judge at eval time — that is what keeps it LLM-free:

- A golden declares `answer_bearing_ids` = the **minimal sufficient set**: the answer is derivable from those units, and **not** derivable with them removed. `memphant-eval verify-golden` enforces the second half **mechanically** (no judge): re-run recall with the declared units *masked*; the fixture FAILS verify-golden if the masked run still satisfies every remaining `expect` assertion (`top_k_contains`, `citations_include`, trace assertions) — i.e. an equivalent unit reached top-k, so the declared label was not load-bearing. `answer_bearing_ids` is a first-class fixture field (§4.2); `top_k_contains` may be a superset of it (extra expected context), never a substitute.
- Multi-hop cases declare the union across hops + `min_hops`; the oracle passes only if post-expansion top-k contains the full union (a case needing an edge-expanded unit can't pass on direct units alone).
- Score = set-membership `|answer_bearing_ids ∩ top_k| / |answer_bearing_ids|`, gated `== 1.0` — distinct from soft `recall@k`.
- It is **answer-model-independent on purpose**: a model swap moves benchmark scores ~10pts (§8) but cannot move the oracle, making it the stable PR/nightly gate. "Did the model answer correctly" lives one rung up (sampled-public, where a judge is allowed).
- **The oracle is contamination-proof but not rot-proof.** `answer_bearing_ids` are hand-authored labels; they rot when a fixture's corpus is edited or the minimal-sufficient set was authored wrong. Two cheap guards (no LLM, no kappa program): `verify-golden` re-runs across the **whole** golden corpus on a schedule, not only at authoring, so a label that stops being load-bearing fails standing; and a **new golden family requires a second author to confirm its minimal set** before it can gate — a single assessor's relevance labels agree less than two (a long-standing IR result; Sormunen 2002). Oracle rot is a distinct drift type from benchmark contamination (`22` §3.1).

### 4.1 Cheap-To-Expensive Gates

Every PR:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo nextest run --all-features
cargo test --doc
cargo run -p memphant-eval -- run examples/evals/golden.yaml
```

Every PR additionally (the steady-state gate — identical to `29` §4; a builder implementing from this doc alone must not build a weaker gate):

```bash
cargo run -p memphant-eval -- verify-golden examples/evals/golden.yaml
cargo run -p memphant-eval -- security examples/evals/security-smoke.yaml
cargo run -p memphant-cli -- db lint --provider plain-postgres
```

PR-cadence derivation-family goldens run in **record-replay extraction mode** (extraction outputs recorded per `(fixture_version, compiler_version)`, pinned local embedder — `03` §6.2): a PR gate that invokes live LLM extraction is neither ~$0 nor deterministic (R81); live-derivation runs are nightly.

Nightly:

```bash
cargo run -p memphant-eval -- run benchmarks/nightly-sampled.yaml --archive-traces
cargo run -p memphant-eval -- ablate benchmarks/nightly-sampled.yaml
cargo run -p memphant-eval -- verify-golden examples/evals/ --all   # whole-corpus oracle-rot guard (§4.0)
cargo run -p memphant-cli -- db lint --all-providers
```

Release:

```bash
cargo run -p memphant-eval -- run benchmarks/release.yaml --archive-traces
cargo run -p memphant-eval -- profile benchmarks/release.yaml --compare-to baselines/release-baseline.json
cargo run -p memphant-eval -- security benchmarks/security.yaml
```

Syndai dogfood cutover additionally requires the focused Syndai memory regression lane and runtime DB contract checks from `backend/TESTS.md`.

### 4.2 Golden Case Format

Golden cases are executable or they do not count:

```yaml
id: stale_fact_checkout_001
cluster_key: session_checkout_01     # bootstrap resampling cluster (§8); defaults to the fixture id
fixture_version: 1
seed:
  episodes:
    - id: ep_old
      trust: trusted_user
      text: "Use token callback v1."
    - id: ep_new
      trust: trusted_system
      text: "Token callback v2 replaced v1 on 2026-06-01."
expect_units:                        # symbolic-name → derived-unit binding (R81): derived units get
  mem_new_callback_v2:               # runtime UUIDv7 ids, so every `mem_*` name used below MUST be
    subject: token_callback          # bound here by (subject, predicate, value-substring) match against
    predicate: version               # post-reflect units; an unbound name or an ambiguous match (0 or
    value_contains: "v2"             # >1 units) is a fixture ERROR, not a miss.
  mem_old_callback_v1:
    subject: token_callback
    predicate: version
    value_contains: "v1"
query: "Which callback token version should checkout use?"
expect:
  answer_bearing_ids: [mem_new_callback_v2]   # minimal sufficient set — the §4.0 oracle field
  top_k_contains: [mem_new_callback_v2]       # may be a superset of answer_bearing_ids
  citations_include: [ep_new]
  forbidden_memory_ids: [mem_old_callback_v1]
  forbidden_text:
    - "callback v1 is current"
trace_assertions:
  - stale_fact_suppressed: true
  - channel_present: vector
  - discard_reason_contains: stale
```

Shape-only tests are insufficient. Seed-episode ids (`ep_*`) are caller-supplied and need no binding; a range shorthand `ep_decoy_1..ep_decoy_8` expands to eight sequentially-numbered episodes sharing the row's fields. Every case carries `fixture_version` (rot tracking, §4.0) and a `cluster_key` (clustered bootstrap, §8).

**The longitudinal update-chain family proves the truth model over N supersessions, with a value-revisit trap (R92 answer-of-record for "active-truth accuracy").** Five generations of one `subject_key`, including a RETURN to an earlier value — the load-bearing trap: `dedup_key = hash(subject + source_kind + normalized_content)` could collapse the revisit into the OLD generation, corrupting the chain. No 2-episode family can catch it:

```yaml
id: update_chain_residence_001        # family: update_chain_*
cluster_key: chain_residence_01
fixture_version: 1
seed:
  episodes:                            # 5 generations, one subject, value REVISIT at g5
    - {id: ep_1, actor: user, text: "I live in Seattle.",         observed_at: 2026-01-05}
    - {id: ep_2, actor: user, text: "Moved to SF last week.",     observed_at: 2026-03-10}
    - {id: ep_3, actor: user, text: "I'm in Austin now.",         observed_at: 2026-04-20}
    - {id: ep_4, actor: user, text: "Settled in Denver.",         observed_at: 2026-06-01}
    - {id: ep_5, actor: user, text: "Back in Austin as of July.", observed_at: 2026-07-01}
expect_units:
  mem_residence_g5: {subject: residence, predicate: location, value_contains: "Austin"}
query: "Where does the user live?"
expect:
  answer_bearing_ids: [mem_residence_g5]
  forbidden_text: ["Seattle is current", "Denver is current"]
trace_assertions:
  - supersession_chain_length_min: 4   # N-1 supersedes edges — no A→C shortcut skipping a generation
  - resolution_reason: newer_valid_from
# sibling case: historical query "where did the user live in mid-March 2026?" with
# valid_at=2026-03-15 (after the g2 SF move on ep_2, before the g3 Austin move) and
# transaction_as_of=2026-08-01 (after all five episodes are ingested) → the full-
# bitemporal "both fields" case (`08` §3.1) resolving to the g2 (SF) generation, NOT
# g1 (Seattle, whose window closes when g2 opens) (exercises both axes of the §1.3 historical validity gate)
```

"Active-truth accuracy" (given N updates, does recall return the CURRENT truth?) is the per-ability rollup of this family + the stale-fact and derived-contradiction families — a fixture family, not a new metric.

**Manifest + orphan guard (mirrors Syndai's frontier eval discipline).** Golden cases live as on-disk YAML under `examples/evals/<lane>/`, declared in a `manifest.yaml` (`{category: [case_id]}`). A deterministic test asserts manifest↔files have no orphans (a case file with no manifest entry, or a manifest entry with no file, fails). Scoring is **deterministic — no LLM judge** (expected memory IDs + expected citations + forbidden leaks), matching the "retrieval-only oracle" ladder rung.

**The contradiction-detection golden must not pre-annotate the conflict.** The case above works only because `ep_new` is hand-labeled "replaced v1." A second golden family seeds two *plain* episodes (no supersession text, no trust asymmetry beyond source) and asserts the system **derives** the `contradicts` edge and resolves it (`04` §3.1) — otherwise the eval validates fixture authoring, not detection:

```yaml
id: derived_contradiction_pricing_001
seed:
  episodes:
    - id: ep_a
      actor: user
      text: "Our refund window is 30 days."
      observed_at: 2026-03-01
    - id: ep_b
      actor: user
      text: "Refund window is 14 days."
      observed_at: 2026-06-01
query: "What is our refund window?"
expect:
  derived_edges:
    - {kind: contradicts, between: [ep_a, ep_b]}   # system must FIND it, unlabeled
  top_k_contains: [mem_refund_14]                   # newer valid_from wins
  forbidden_text: ["30 days is current"]
trace_assertions:
  - contradiction_detected: true
  - resolution_reason: newer_valid_from
```

**The utilization golden proves the unit was *packed*, not just retrieved.** "Retrieved-but-answer-wrong" (§6) is invisible to a recall-only oracle — the unit *is* in top-k. A third family asserts it survived Stage-7 packing at a usable position, isolating context-assembly bugs from answer-model bugs:

```yaml
id: utilization_buried_fact_001
seed:
  episodes:
    - id: ep_decoy_1..ep_decoy_8     # 8 same-subject near-dups (install noise)
      text: "Deploy step ran."
    - id: ep_answer
      trust: trusted_system
      text: "Prod deploy requires manual approval in #release."
query: "What is required before a prod deploy?"
context_budget_tokens: 300            # tight: forces packing decisions
expect:
  top_k_contains: [mem_deploy_approval]            # retrieval succeeded
  packed_context_contains: [mem_deploy_approval]   # AND survived Stage-7 budget
  packed_position_max: 2                            # not buried below decoys
  dedup_collapsed_ids_min: 6                        # near-dup decoys collapsed, not packed 8x
trace_assertions:
  - dedup_across_channels: true
  - abstention_signal: false
```

It fails if dedup leaves decoys eating the budget, if ordering buries the answer, or if the pack wrongly abstains — none of which a recall-only oracle catches.

## 5. Metrics

Always report:

- accuracy
- recall@k
- **recall@k for a small tenant inside a large shared corpus** (the filtered-HNSW worst case — `02` §2.1b)
- precision@k
- citation validity
- contradiction **detection** precision/recall (separate from resolution accuracy — `04` §3.1)
- p50/p95 latency
- token cost
- storage cost (per tenant, per retention tier)
- poisoning injection success rate
- **corroboration-farming resistance** (single-origin reinforcing observations rejected — `04` §5)
- cross-tenant leakage count
- deletion completeness
- consolidation lag (capture→extracted)

Accuracy without cost and latency is not a product metric.

## 6. Failure-To-Lever Map

| Failure | Likely lever |
|---|---|
| Answer-bearing memory not in top-k | chunking, FTS/vector weights, entity extraction, query rewrite. |
| Right memory appears but rank is low | RRF weights, rerank, decay, trust weighting. |
| Right memory retrieved but answer wrong | context assembly, citation packing, abstention labels, answer prompt/model. |
| Multi-hop failure | edge expansion, temporal windows, query decomposition. |
| Stale fact wins | bitemporal validity, contradiction detection, recency decay. |
| Cost too high | `halfvec` storage + `binary_quantize` two-phase first-pass with full-vector rerank (`02` §2.1a); adaptive cascade, skip rerank, cache, smaller candidates. |
| Latency too high | parallel channel fetch, smaller top-k, no L4 default, per-tenant partial HNSW for selective tenants (`02` §2.1b). |
| Poisoning succeeds | write quarantine, trust-aware retrieval, corroboration threshold. |
| Tool args biased by memory | high-risk action memory suppression and provenance labels. |

## 7. Benchmark Sources

Seed external references:

- **STATE-Bench** (Microsoft, May 2026; 450 procedural stateful tasks; metrics = task completion, **pass^5** reliability, efficiency, 1–5 UX): **the primary production-improvement target.** It is memory-agnostic ("bring your own memory"), neutral, and has **no published memory-system SOTA yet** — so it is MemPhant's best shot at a defensible, *first*, neutral SOTA claim. Lead here, not on vendor-run leaderboards.
- **LongMemEval-V2** (arxiv 2605.12493): environment-specific agent memory over up to 500 trajectories / 115M tokens, accuracy plus latency. The classic `LongMemEval-S` (~115K tokens) is near-saturated/contaminated — baseline only.
- **BEAM / "Beyond a Million Tokens"** (arxiv **2510.27246**): production-scale memory at 100K/1M/10M tiers. **Cite the paper as primary.** The `agentmemorybenchmark.ai` board is a Vectorize-operated vendor leaderboard (current top entry Hindsight, self-published) → `source_status: vendor_reported`, never an anchor (`12` §8).
- **MemMachine** (arxiv 2604.04853, ground-truth-preserving) and **ReasoningBank** (arxiv 2509.25140, strategy distillation from successes *and* failures): the closest architectural twins — track as comparability + procedural-memory references.
- **AgentDojo / prompt-injection suites**: tool-using agent security — **with a caveat.** AgentDojo is **near-saturated** on 2026 frontier models without defenses and tests *tool-call-time* indirect injection, **not persistent memory poisoning**. It is **not** the primary memory-poisoning benchmark; lean memory-poisoning evals on OWASP Agent Memory Guard fixtures + MemPhant's own `corroboration-farming` suite (§10).
- Internal Syndai golden cases: scoped recall, forget, correction, child-agent isolation.
- LoCoMo, PersonaMem, LifeBench: comparability baselines (saturating on easy categories), not sufficient SOTA proof alone.

Benchmark claims always report:

- benchmark version
- corpus size and haystack size
- model and model version
- embedding model and dimensions
- reranker, if any
- feature flags
- retrieval config
- accuracy and CI
- p50/p95 latency
- token/cost budget
- whether competitor numbers are self-reported, vendor-reproduced, or independently reproduced

## 8. SOTA Claim Rule

Do not claim SOTA unless:

- benchmark version is frozen
- config is published
- traces are archived
- accuracy, cost, and latency are reported together
- poisoning/security evals are included
- competitors run under comparable budgets or the caveat is explicit
- confidence intervals and paired ablations are published
- public writeup distinguishes memory benchmarks from long-context-only benchmarks

**The SOTA bar is "beat the strongest *independently reproduced* baseline, or publish a Pareto win" — never "beat a vendor's blog number."** The field's leaderboards are largely vendor-self-reported (e.g. BEAM's top entry is published by the leaderboard operator), and a model swap alone moves scores ~10 points, so target tables (`27` §1) must carry a `reproduced` vs `vendor_reported` column and SOTA claims may only be measured against the reproduced column.

**CI discipline (so "confidence intervals" is not hand-waved):** report **bootstrap CIs** (≥1,000 resamples) on accuracy; a sampled eval is "stable" only once its CI half-width is below a declared threshold (default ±2%); lever-promotion decisions use **paired** comparisons (same cases under both configs) with the paired delta CI excluding zero. A sampled subset promotes to a full run only when it predicts a real delta at this bar.

- **Clustered resampling (cases are not independent).** Memory benchmarks deliver cases in correlated groups — many questions over one multi-turn session, or one corpus (LME-V2 trajectories, MemoryStress's 1,000-session runs). The bootstrap resamples **whole clusters, not individual cases**; per-case resampling treats correlated cases as independent and can understate the interval by **>3×** (Evan Miller, "Adding Error Bars to Evals", arXiv:2411.00640). The cluster key is `session_id`/`corpus_id`, recorded as `cluster_key` on the `eval_case_result` row (`22`).
- **Multiple-comparison correction (many levers, shared cases).** A cycle that evaluates N>1 levers against the **same** cases inflates false promotions at a per-test α — the micro-levers (§1.4) and the ablation matrix (§9) are exactly this. Control the family: **Holm-Bonferroni** on the paired-delta tests for a small lever set, **Benjamini-Hochberg** (FDR) as the set grows (the trade is power for false-positive control — the gate's job is to not promote noise). A single lever evaluated alone on its own run needs no correction.

## 9. Ablation Plan

V1-required ablation dimensions:

| Lever | Baseline | Variant |
|---|---|---|
| lexical | off | Postgres FTS |
| vector | off | selected embedding profile |
| temporal | off | recency/validity windows |
| write/extraction policy | raw-only | source-preserving semantic units |
| context packing | naive top-k | budgeted evidence pack with abstention labels |
| trust filtering | permissive | default policy |
| decay | off | DSR-inspired decay |
| rerank-light | off | bounded deterministic or cheap rerank |

SOTA/deep-mode ablation dimensions:

| Lever | Baseline | Variant |
|---|---|---|
| edge expansion | off | 1-hop contradictions/resources/procedures |
| query decomposition | off | multi-intent decomposition |
| proactive recall | off | compiler-suggested retrieval |
| L4 deliberate recall | off | agentic high-cost mode |
| procedure promotion | off | validated procedure recall |
| contradiction method | embedding+temporal candidate only | + NLI verifier / + cheap LLM-judge on the ambiguous residual |
| confidence fold shape | `c ← c + α(1−c)` fold | Beta-posterior `α/(α+β)·q` fold over the same deduped ledger (R91; `04` §5.1a) |
| pinned block | no block | one pinned scope block, guaranteed-presence (R88) — paired continuity/task-success delta must exclude zero AND the over-personalization score must not regress |
| composition | extraction-only reflect | + inferred-belief abstraction (`derived_by: composition`, R89 rung) — advance on corroborated-promotion precision without OP-Bench regression |

These dimensions ship as modes and eval controls. Production defaults can keep expensive dimensions off, but the benchmark harness must be able to run them from day one.

The **contradiction-method** arm makes the `04` §3.1 detection commitment (embedding/temporal candidate + cheap LLM judge on the residual, R24) a *measured* choice rather than an assumed one — no public head-to-head of NLI vs LLM-judge vs embedding-proximity exists inside an agent-memory store, so this is a field-first measurement, not a settled default (NLI-verifier reference: CSMAD, Amazon Science). Detection precision/recall is reported separately from resolution accuracy (§5).

### 9.1 Control Baselines (edges and consolidation must earn their keep)

The 2026 evidence is skeptical of heavy graph/edge investment: Mem0's own paper shows its graph variant *loses* single- and multi-hop and costs ~3× latency / ~2× tokens for a ~1.5pt overall bump (arXiv:2504.19413), and Mem0 then **removed its graph module** in the v3 rewrite (PR #4805, replaced by lightweight entity linking); a plain **filesystem** agent (74.0% LoCoMo) beats Mem0's graph variant (68.5%) because LLMs are post-trained on file/search operations (Letta). So edge expansion is not assumed — it is gated against two control baselines the harness runs from day one:

- **No-edges control:** the full pipeline with Stage-4 edge expansion *disabled* (gates → exact → FTS → vector → RRF → rerank). If `edge_expansion_enabled` does not beat this by a meaningful margin (≥3 pts on STATE-Bench multi-hop + LME-V2), de-emphasize or cut edges.
- **Filesystem / iterative-search baseline:** raw episodes in a file tree, agent does grep/iterative-search — the "is a filesystem all you need?" control. If MemPhant's structured recall does not beat it, the structure is not paying for itself. (Reinforced 2026: AutoMEM, arXiv:2606.04315, found an agentic harness managing *flat text files* through tool calls outranked eight memory systems across five scenario families — this control is the real bar, not other memory products.)

The counter-signal is real and bounded: edges *can* pay off at 10M-token scale with the right traversal (Hindsight's sublinear Meta-Path+Forward-Push, arXiv:2512.12818) — so the posture is "edges skeptically, with a control baseline," not "no edges ever." **Caveat:** cross-system LoCoMo scores are not harness-comparable — swapping only the answer model moved one system 74.4%→84.5% (Continua "Fair Fight"), so MemPhant's baselines must be run *in MemPhant's own harness*, not read off other systems' leaderboards.

### 9.2 Proactive Pre-Fetch (ablation arm, not a default)

Idle-time pre-fetch — predict the next need from recent dialogue + memory and warm the cache — is a real but bounded lever: ProAct (arXiv:2605.25971) reports −28.1% hallucination / −14.8% turns. It is a scheduling/pre-warm feature (no substrate change), so it ships as an ablation arm; promote only if hallucination/turns improve **without raising the over-personalization score** (§1.5) — pre-fetching irrelevant memory is the exact harm the relevance gate guards against.

Activation order, trace symptoms, and promotion/disable rules are canonical in `27-sota-ladder-and-validation.md`.

### 9.3 Interaction Effects (single-lever ablation is necessary, not sufficient)

Per-lever paired-CI promotion (§8) measures each lever against one baseline, so it cannot see levers that only help — or only regress — *in combination*. The effect is real and measured: context/config variables collectively rival model choice, with significant model×corpus and model×query-format interactions (JMIR Med Inform 2026, e94241; combined context η² 49.0% vs 47.6% for model choice). MemPhant does **not** run a full 2^n factorial — the lever count makes that wasteful — but it does not pretend solo arms compose, either:

- **Leave-one-out on the promoted set** (release cadence, not per-PR): drop each promoted lever from the full config and re-measure with the same paired-CI bar. An LOO removal that does **not** regress the stack means the lever is redundant once the others are present → cut it (KISS). A lever that helps only with others present is caught here, never by its solo arm.
- The promoted set is what ships; LOO is what keeps it from silently accumulating levers that earned a solo win but pull their weight only because another lever was off.

## 10. Security Evals

Named suites:

| Suite | Assertion |
|---|---|
| Cross-tenant leakage | no recall returns another tenant's memory |
| Child-agent isolation | child scopes do not receive parent-only memory |
| Deletion completeness | forgotten content cannot return through lexical/vector/cache/resource paths |
| Low-trust web poisoning | malicious web/resource text stays quarantined or labeled |
| Tool-output poisoning | tool output cannot silently become high-trust instruction memory |
| Corroboration-farming / Sybil poisoning | K mutually-reinforcing low-trust observations from a **single origin** never clear belief→semantic promotion (independence gate, `04` §5) |
| Stale fact override | newer valid evidence outranks old evidence |
| Citation forgery | model cannot cite memory IDs outside candidate whitelist |
| High-risk action suppression | low-trust memory cannot fill consequential tool args |
| Filter/selector injection | adversarial values in recall filters/selectors cannot widen scope or cross tenants (parameterized SQL only — the Mem0 store-adapter injection class, R76) |
| Out-of-order/backfill ingestion | historical backfill with out-of-order `observed_at` produces correct bitemporal generations and supersessions (Graphiti #1489 class) |

Security evals are part of memory quality. They are not separate compliance theater.
