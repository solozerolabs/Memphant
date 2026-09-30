# Spec: served-block precision — graphify + Understand-Anything + napkin digest

Status: BUILT, Batches 0-4 (2026-09-30, branch `precision-served-block`), **NOT
SHIPPABLE AS-IS**: the content-term filter (idea 1) regresses the Syndai docs gate and
trips the Batch 1 stop rule (see **Build record → Docs gate**). Batch 5 (paid
behavioural guard) not run.
North star: coding-agent UX. Priorities: good UX > cost > perf/latency; KISS/DRY.
Scope rule: accept only ideas that fit MemPhant's **measured** niche — memory for
decisions and directives an agent cannot re-derive from the repo, and whether the
agent adheres to them. Code retrieval is not the niche (grep wins it).

## Build record (2026-09-30)

Offline gate (the ship bar): `crates/memphant-core/tests/directive_precision.rs` and the
`#[ignore]` bge-small arm `crates/memphant-runtime/tests/directive_precision_bge.rs`, both
over the fixture in `crates/memphant-store-testkit/src/directive_precision.rs`. Sums over
the 29 tasks at `/core` shape (`token_budget=1200`, `serve_captures=false`):

| Arm | Gold in pack, before → after | Served units | Served tokens (`token_estimate`) | Duplicates served |
|---|---|---|---|---|
| bge-small (prod dense channel) | 29 → **29** | 837 → **496** (-41%) | 34,590 → **19,121** (-45%) | 95 → 45 |
| compressed cosine (CI) | 28 → **28** | 828 → **675** (-18%) | 34,571 → **29,041** (-16%) | 90 → 21 |
| lexical only (no embedder) | 28 → **28** | 759 → **137** (-82%) | 32,222 → **5,906** (-82%) | 83 → 5 |

Per task on bge-small: ~28.9 → ~17.1 units, ~1,193 → ~659 tokens. The one gold never
served on the CI arms (`money_int_cents`: "price field" vs "Monetary amounts … integer
cents") shares no content term with its task and is budget-dropped before and after;
bge-small serves it in both. Sanity gate
`fixture_gold_is_recalled_at_unbounded_budget`: all 29 golds recalled on the dense arm.

Per-idea sweep (bge-small, served tokens / gold): filter only 34,281 / 29; + collapse at
τ 0.8 34,322 / 29 (the budget refills what the collapse frees); + gap cutoff at ratio 0.2
32,000, 0.35 26,333, 0.5 **19,121**, all 29 gold. The filter alone moves the lexical arm
(-82% tokens) but not the dense arms, because the dense channel still votes for the whole
store: that is the build condition of idea 2, and it held.

Independent guards ($0, local scratch Postgres, `--embed-model small`, before = `5b0a79f8`,
after = this branch):

- **LME-S chat lane** (`bench-lme --sample 178 --seed 20260710 --k 10 --budget-tokens 8192
  --pool 64`, then `scripts/analyze_lme_pack_nonregression.py`): r@5 **0.687 → 0.855**
  (paired over 166 scored: 31 after-only hits vs 3 before-only, exact McNemar
  p = 7.7e-7); mean packed items 4.58 → 3.33. Not a loss, so the Batch 1 stop rule does
  not fire. The 12 abstention questions score "correct" 7 → 4, but that metric counts
  a question correct when the topically related session is NOT retrieved
  (`bench_lme::score_question`), so sharper retrieval lowers it by construction; whether
  the reader then abstains is a reader-lane question, not measured here.
- **Syndai docs gate** (`scripts/gate_run_memphant.py`, v1 + v2 goldens, 120 goldens,
  corpus `6fe7f78f` archived and verified 114/114 files, 4,920 sections, `--embed-model
  small --mode fast --k 10`, stock 120 s client timeout, no override; main = `5b0a79f8`
  plus the 409 fix `52bc2b71`). **Regression:**

  | Goldens | r@5 main → branch | r@10 main → branch | Lost / gained (hit@10) |
  |---|---|---|---|
  | v1 (60) | 0.150 → 0.100 | **0.250 → 0.167** | 6 lost (`s005_ops`, `s028_root`, `s032_tools`, `s037_plans`, `s048_root`, `m003`) / 1 gained (`m001`) |
  | v2 (60) | 0.233 → 0.217 | **0.283 → 0.267** | 1 lost (`v2_s044_runbooks`) / 0 gained |

  Paired over both sets: 7 lost vs 1 gained, exact McNemar p ≈ 0.07. Not significant at
  0.05, but it is a dense-arm gold loss, so the Batch 1 stop rule fires.

  **Attribution (v1 ablations, same corpus and binaries):** the gap cutoff disabled
  (`PACK_GAP_RATIO = 0`) and the gap cutoff exempting cross-reranked candidates both
  reproduce the branch's v1 result exactly: the same 6 lost, 1 gained. The
  near-duplicate collapse cannot fire on doc sections (resource units carry no subject,
  so their fact key is `auto` and `Restatement::of` returns `None`). **The content-term
  filter is the whole regression.** Mechanism: the docs goldens are deliberately
  paraphrased (gold lexical overlap ≈ 0.07). With rank-only RRF, a stopword-only BM25
  match still gave the vector-found gold a BM25 rank. Once stopwords earn nothing, units
  with weak content overlap outrank it, and it falls out of the fused top-64 that the
  cross-encoder reranks (the server's default; `MEMPHANT_CROSS_RERANK` is unset in
  `fly.toml`).

  **Why no subset ships cleanly:** on the `/core` fixture the filter is load-bearing. With
  raw BM25 query terms (filter kept elsewhere, gap cutoff and collapse on), the bge-small
  arm serves 33,760 tokens (vs 19,121) and loses a gold (28 vs 29): stopword BM25 votes
  make nearly every unit lexical, so the dense-only gap cutoff has nothing to cut. `/core`
  never runs the cross-encoder (`should_rerank` skips when the fused pool is ≤ `k`, and
  `/core` asks for `k = 1000`), so the fixture is the prod `/core` shape. The docs gate
  (k = 10) is where the reranker runs. Open decision for the owner: a filter that keeps
  the precision without the paraphrase loss (e.g. a per-candidate vote only on content
  overlap while keeping stopword ranks as a tie-prior), or shipping ideas 5 and 6 alone.
- **Negative slice:** the 409 is fixed (`52bc2b71`, one agent node per gate scope). It now
  fails later, on both arms, at the first time-travel negative query: `/v1/recall` → 403
  `capability_denied` (`can_audit_history` is owner-only and default false), and
  `memphant-cli admin create-key` has no way to mint such a key. Pre-existing on main; the
  fix is a CLI flag plus a harness change, not attempted here. The v1/v2 numbers above
  were run without `--negative-slice`.
- **Spec drift:** `python3 scripts/check_spec_drift.py` → `spec_drift=dirty
  05-retrieval-and-eval-spec.md:content`, expected until the Syndai mirror lands on
  Syndai main.

Deviations from the spec below, each with its reason:

1. **Gap cutoff cuts only dense-only candidates (idea 2).** As specified (strength = max
   over all channels, per-channel min-max, anchored on the first admitted item), it lost
   gold at every ratio on the CI dense arm (`no_pii_logs` at 0.2; a weakly matched BM25
   gold scores ~0 after min-max) and at 0.5 on bge-small, while cutting little. Cutting
   only candidates that no lexical channel matched loses no gold on any arm across the
   grid and cuts more. With min-max the top cosine is 1.0 by construction, so the anchor is gone: a
   dense-only candidate is served only when its normalized cosine is at least
   `PACK_GAP_RATIO` (0.5, the largest grid value with zero gold loss). `MIN_STRENGTH_RANGE`
   (0.1) and the exemptions are as specified.
2. **Near-duplicate collapse (idea 3).** (a) It compares non-stopword tokens of any length,
   not `content_terms` (length ≥ 3), so `eu`/`us`, `v1`/`v2` stay distinct. (b) Markers
   that must match are negations **and digit-bearing tokens**: at τ 0.6 the collapse merged
   MemPhant's own goldens (`evidence_integrity_suppressed_read_no_refresh_*`: "checkout
   flag is legacy_pay" vs "… express_pay", Alice vs Bob as owner, Jaccard exactly 0.6) and
   the MCP test's four numbered checklist steps. (c) Only units with a curated subject
   (non-`auto` fact key) collapse; unkeyed episode evidence from two sessions can share
   words and both matter (`temporal_grounding` windowing test). (d) τ = 0.8: 0.7 and 0.8
   collapse the same fixture restatements, so the stricter one. (e) A newer restatement
   replaces the admitted one only if it fits in its place, and is exempt from the gap
   cutoff (the pack already chose to serve that decision). Residual risk, accepted: a
   one-value swap in a long claim (≥ 8 shared terms) still collapses, freshest wins, as
   subject supersession would; a `contradicts` edge keeps both.
3. **Sanity gate is "recalled", not "served" (Batch 0).** With the gap cutoff a correctly
   seeded gold may be rightly cut, so "served at unbounded budget" would conflate seeding
   with pack policy. The gate asserts every gold is a recall candidate on the dense arm
   (where the vector channel votes for the whole store, so only a seeding failure misses).
4. **Fixture shape (Batch 0).** Case subjects are `decision:<case id as words>` (Syndai
   subjects are short LLM topic phrases under `decision:`). The stale-vs-fresh pair is an
   older restatement with the same number, not a changed number: a changed number is now
   never collapsed (2b), and a value update is subject supersession's job. The cases are
   embedded in the testkit (the jsonl is gitignored; one fake key in a pressure task is
   redacted). The fixture lives in `memphant-store-testkit` so the core and runtime arms
   measure one store.
5. **Filter scope (idea 1).** Beyond the four named scorers it covers the Edge channel,
   pack relevance and chunk selection (one query-term list after the candidate fetch) and
   the degraded raw-episode fallback (`degraded_episode_items`). The store's FTS fetch keeps
   the raw tokens (Postgres `english` FTS already drops stopwords). The predicted BM25-only
   loss of the three morphology golds did not happen: the Exact channel's 5-character
   prefix match on the case subject still reaches them.
6. **Compiler pin (idea 6)** hashes the golden's observed outputs as specified; the golden
   drives admission (`reflect_recorded`) with hand-built candidates, so it pins admission
   output, not episode extraction.

Observed, not fixed (recall reach is a non-goal here): the BM25 tokenizers keep
sentence-final punctuation on a single-run token (`sandbox.` never matches `sandbox`), so
the last word of every sentence in a Syndai task summary earns no BM25 vote. Fix in
`bm25_control_tokens` with its own measurement.

## Evidence

### The open problem (prior measurements)

- Grep beat MemPhant by **-37.8pp** on repo-recoverable facts (Syndai
  `SPEC-memory-skills-runs.md` §1.5).
- Adherence: turn-1 injection of the *oracle* decision rescued **14/20 = 0.70 of
  headroom** at n=29 (`benchmarks/data/fidelity_ab_result.json`).
- Syndai prod A/B: the directive-recall arm was **worse** (72.4% vs 80.0%
  completion over finished runs, p=0.017; 82.9% vs 87.6% excluding cancels,
  p≈0.09) and served **~21 units vs ~12**. Syndai is retiring that arm
  (`Syndai/docs/flows/memory-directive-recall-verdict.md`), so every Syndai run
  now gets the capture-free turn-1 block (`serve_captures=false`, ~12 units
  median on the Syndai repo). The open problem is that block's **precision and
  size**, not recall. Prod measures it per run as `memory_core_units` on the
  `lifecycle.terminal` span (`Syndai/docs/flows/run-failure-attribution-observability.md`).

### The served path that reaches Syndai prod (verified read-only)

- Syndai calls `GET /v1/scopes/{id}/core` with `token_budget=1200`
  (`Syndai backend/src/features/memory/memory_core.py:113,188-209`). The raw
  projection path is only a 404 version-lag fallback.
- MemPhant `scope_core` (`crates/memphant-core/src/service.rs:6185`) calls `recall`
  with `limit = MAX_RECALL_LIMIT = 1000`, the caller's budget, `Fast`,
  `context_packing_abstention_enabled: true`, and `serve_captures` (on for the
  memory-on cohort). **The token budget is the only cap on served count.** The
  `k=8 / 512` service defaults never apply to Syndai.
- Prod has the dense channel **ON** (`fly.toml:25`, `MEMPHANT_EMBEDDINGS='small'`,
  bge-small-en-v1.5, since `2c494080`). The vector KNN adds up to
  `recall_pool_depth` (64) candidates per recall with no lexical overlap required
  (`lib.rs:7816-7843`); only `Procedural` units face the 0.62 cosine floor
  (`lib.rs:7853-7872`, `:9096`).
- The plugin `.memphant/MEMORY.md` projection (`plugins/_shared/memphant_projection.py`)
  is a **local-harness** surface (Claude Code/Codex/opencode/pi). It does not
  reach Syndai and cannot move the served-block size Syndai measures.

### What the code does today

- **Fusion is rank-only weighted RRF**; only Exact is scaled by its own 0..1
  magnitude (`lib.rs:7953-7976`). Any positive channel score is a candidate
  (`channel_candidates`, `lib.rs:10979-11048`).
- **Stopwords leak into two lexical channels.** `bm25_unit_scores` (`lib.rs:11885`)
  keeps stopword query terms. `exact_score` (`lib.rs:11753`) matches the query
  (stopwords included) against `fact_key` tokens, and Syndai directive captures
  key `fact_key` on the **first 60 chars of prose** (`memory_core.py:352`,
  `service.rs:8183`), so "the/to/a" in a subject earns a magnitude-scaled Exact
  vote on almost any query. The content-token filter already exists but only
  inside the procedural floor (`content_query`, `lib.rs:7860`).
  - Caveat (eng review): those prose-prefix subjects belong to Syndai's
    `capture://directive` lane, which Syndai is retiring, and captured
    Candidates are invisible at `serve_captures=false` anyway. The Exact figure
    below is therefore an upper bound for the retired shape. Batch 0 re-measures
    Exact on the shapes that are still served (lane-3 decision learnings with
    explicit subject+predicate, profile facts). The filter still covers Exact:
    one query-term predicate for every lexical channel is the DRY form.
- **Two stoplists:** `is_stopword` (`lib.rs:9131`, 54 words) and a second
  `STOP` inside `content_terms` (`lib.rs:10448`, 28 words, len≥3).
- **Pack fill is greedy to `k` or budget** (`pack_recall_context`, `lib.rs:9709`;
  `admit_or_drop`, `:9868`). No relative relevance cutoff; abstention fires only
  on empty or unresolved-contradiction packs.
- **Ties already break toward the cheaper item** on the served path:
  `fused_score`, then `packing_density_score` = fused/tokens (`lib.rs:9750-9758`,
  `:10175`).
- **Dedup is by `fact_key` only** (`lib.rs:9877-9892`). Paraphrased restatements
  get different keys and are both served. Submodular ordering
  (`submodular_pack_order`, `lib.rs:10203`) is off by default and only *reorders*
  (it appends every unselected candidate), so it never drops a near-duplicate.
- **Projection** sorts each group by (confirmed, topic A–Z)
  (`memphant_projection.py:122-130`) and the 8 KB cap drops the *last* block
  (`:161-171`) — eviction is alphabetical, not by relevance. It requests
  `budget_tokens: 4096` (≈16 KB), so the cap bites by construction.
- **Corrections are only survival marks.** When `_CORRECTION_MARKERS`
  (`memphant_capture.py:553`) fires, `post_survival_mark` (`:696`) labels every
  served unit `corrected`; the correction's content is never captured. The server
  already accepts `capture://directive` → `Procedural` Candidate
  (`service.rs:8098,8116`); the plugin's `CAPTURE_SOURCES` (`:90`) lacks it.
- **Mirror capture is configurable** by basename via `MEMPHANT_CAPTURE_MIRROR_FILES`
  (`plugins/claude-code-memphant/hooks/capture_file_mirror.py:43-50`).
- **Deterministic compiler is unsalted.** `COMPILER_VERSION = "compiler-0.1.0-ws0"`
  (`crates/memphant-types/src/lib.rs:1763`) never changed; `job_state` dedups on it.
  Jobs are enqueued at retain time only, so a bump alone re-derives nothing already
  reflected. LLM extraction is already salted (`structured_state.rs:237-250`).

### Scratch measurements (2026-09-29, $0, deterministic)

Python replicas of `bm25_unit_scores` (bm25-code tokens) and `exact_score`
(tokens + 5-char `tokens_related`), 29 adherence decisions as the store, each
case's `task` as the query:

| Measure | Result |
|---|---|
| BM25-matched units per task | 22.3 of 29, of which **18.5 stopword-only** |
| After content-term query filter | 3.8 per task |
| Exact>0 units per task (subject = first 60 chars, as Syndai) | 16.7, of which **12.7 stopword-only** |
| Gold missing from BM25, raw → filtered | 1 → 4 (`email`/`emails`, `tombstone`/`tombstoned`, `hardcode`/`hardcoded`: reached only via stopword noise, at ranks 7, 17, 9) |
| Gold IDF-weighted coverage of task terms | median ≈0.12 (range 0.05-0.40) |
| Gold rank with coverage² scaling | 2 worse, 0 better |
| BM25-only gap ratio 0.1 / 0.2 / 0.3 after filtering | 3.8 / 3.7 / 3.4 kept — filtering already did the work |

- BGE's own FAQ puts bge similarity roughly in **[0.6, 1]** and says only relative
  order matters (FlagEmbedding `research/baai_general_embedding/README.md`). A
  top-normalized cosine therefore stays ≥ ~0.6 for unrelated units, so a
  `score/top ≥ 0.2` gap can never cut a vector-surfaced unit.

### graphify (Graphify-Labs/graphify @ `1cd9a36c0c`, Apache-2.0; pre-relicense parts MIT)

- Tree-sitter + optional LLM extraction into a NetworkX graph; MCP/CLI query layer.
- Ranking mechanisms (`graphify/serve.py`): IDF-weighted query terms (`:358`);
  tiered label matches scaled by **coverage²** (`:725`, #1602); shorter-label
  tie-break (`:730`); seeds stop at a **relative gap** `score < top*0.2`
  (`:822`), after collapsing duplicate labels (#1766), with one guaranteed seed
  per matched query term (#1445); hub-avoiding BFS above p99 degree (`:1036`).
- Caching (`graphify/cache.py:25-39,957`): AST cache keyed by version plus a
  hand-bumped `_AST_CACHE_SCHEMA`; LLM cache keyed by prompt fingerprint.
- `dedup.py`: NFKC/casefold, MinHash/LSH, Jaro-Winkler (`rapidfuzz`).
- Benchmarks (`BENCHMARKS.md`): LOCOMO 45.3% (n=300), LME-S 76% (n=50), code n=6.
  The harness is absent from the repo, the judge is single-model non-standard,
  and graph-expand vs its own hybrid-RRF is ≈1 SE. **Take mechanisms, not numbers.**
  The credible in-harness ablation says graph expansion adds ≈0 over BM25+dense RRF.

### Understand-Anything (Egonex-AI/Understand-Anything @ `b05cc3b209`, MIT)

- TypeScript Claude Code plugin: tree-sitter structure, per-batch LLM file
  summaries that see only neighbours' exported symbols, a knowledge-graph dashboard.
- Incremental rules: structural fingerprints (SKIP/PARTIAL/ARCHITECTURE/FULL), no
  LLM for cosmetic-only commits, "empty extraction never proves deletion", baseline
  advances only after validation.
- Benchmarks cover scan speed only; no memory or QA claims.

### napkin (blader/napkin @ `27fa60a`, MIT, 106-line skill, no code, no evals)

- A per-repo `.claude/napkin.md` runbook the agent curates each session:
  priority-sorted categories, **max 10 items per category** (lowest priority
  dropped), every entry `[date] rule` + an explicit **`Do instead:`** action,
  including a "User Directives" category.
- No evidence it works; the transferable ideas are *format* (an observable action
  per directive), *eviction by priority*, and *capturing user corrections*.

## Spec

### Accepted ideas (ranked by UX impact on the served block)

**1. One content-term query filter for every lexical channel** (graphify IDF-term
weighting, done the MemPhant way; the root fix).

- **What.**
  - Hoist `content_query` (today computed only inside the vector branch at
    `lib.rs:7860`) to the top of recall and pass it to `bm25_unit_scores` (query
    terms), `exact_score`, `lexical_score` and `token_set_overlap_score`.
    Document-side tokens and BM25 df/avgdl are unchanged.
  - A query that is *all* stopwords falls back to the raw tokens, so no query
    becomes unanswerable.
  - **DRY:** delete the private `STOP` list in `content_terms` (`lib.rs:10448`) and
    make it `!is_stopword(t) && t.len() >= 3`. One stoplist, one predicate.
- **Why first.** Measured: 83% of BM25 matches and 76% of Exact matches are
  stopword-only; with `limit=1000`, each is a candidate competing for the 1200-token
  budget.
- **Where.** `lib.rs` recall body (~7790-7870), `bm25_unit_scores` (:11885),
  `channel_candidates` (:10979), `content_terms` (:10448).
- **Metric.** Candidates and served units per recall, and gold-in-pack, on the
  Batch 0 fixture at the `/core` shape (both embedder arms).
- **Cost.** ~25 lines. Tests intentionally updated:
  `bm25_scores_only_units_that_match_a_query_term` (`lib.rs:16219`); new
  behaviour tests `stopword_only_overlap_earns_no_bm25_vote`,
  `stopword_only_subject_overlap_earns_no_exact_vote`,
  `all_stopword_query_still_recalls_by_raw_tokens`.

**2. Relative score-gap cutoff at pack admission** (graphify `_pick_seeds`).

- **Conditional.** Build only if Batch 1 leaves the dense arm serving weak
  vector-only tail units (served units that no lexical channel matched and that
  are not gold). Scratch data already shows the content-term filter alone cuts
  BM25 candidates from 22.3 to 3.8 per task, and a gap cutoff adds nothing on
  the lexical side (3.8 → 3.4); a second scoring policy must earn its place.
- **What.** In `pack_recall_context`, stop admitting ordinary candidates whose
  evidence strength is below `ratio × strength(first admitted ordinary item)`.
  - **Strength** = max over the candidate's `channels` of that channel's
    **per-recall min-max-normalized** score, `(s - min) / (max - min)` over the
    channel's candidates. Exact is used raw (already a calibrated 0..1).
    **Degenerate ranges never cut:** when a channel's `max - min` is below
    `MIN_STRENGTH_RANGE` (a named constant, starting at 0.1 in that channel's raw
    units), every candidate from it gets strength 1.0. This covers one candidate,
    ties (no division by zero) and near-ties such as cosines 0.899 vs 0.900,
    which plain min-max would map to 0 and 1. Min-max, not `s/top`, because bge cosines sit
    in ~[0.6, 1]: `s/top` would never cut a vector-surfaced unit.
  - **Exempt:** deep-ranked units and authoritative projections (same partition
    the pack already uses, `lib.rs:9765-9774`). **No per-query-term guarantee**
    (graphify #1445): Syndai queries are ≤600-char task summaries with dozens of
    content terms, so one guaranteed unit per matched term re-inflates the block.
  - The cut is relative, never absolute (jcode: a 0.5 absolute embedding threshold
    gave 0.000 recall@5). `ratio` is a named constant chosen by measurement.
  - Dropped units trace as `RecallDropReason::Budget` (no schema or OpenAPI/MCP
    regeneration). Add a `pack_gap_cutoff` flag in `append_pack_feature_flags`
    (`lib.rs:12300`) so traces show it ran.
- **Where.** `lib.rs`: min/max per channel collected in the fusion loop
  (:7953-8010) onto `CandidateAccumulator`; the check in `admit_or_drop` (:9868)
  before render cost is computed.
- **Metric.** Served units and `token_estimate` at the `/core` shape with gold-in-pack
  non-inferior (the ship bar). In prod, Syndai's 28-day core holdout
  (`memory_core_cohort = served|held_out` on `lifecycle.terminal`) is the control
  arm: a change that ships mid-holdout reads as a difference-in-differences,
  served-arm completion before vs after, net of the held-out arm over the same
  window. Fewer units alone is not a UX win; a served-arm completion drop beyond
  the held-out arm's is a revert signal.
- **Cost.** ~50 lines. Tests: `gap_cutoff_drops_weak_tail_under_compressed_cosines`,
  `gap_cutoff_never_drops_authoritative_projection`,
  `single_candidate_channel_counts_as_full_strength`,
  `tied_channel_scores_never_cut`, `near_tied_cosines_are_not_split_into_zero_and_one`.

**3. Near-duplicate directive collapse, freshest wins** (graphify `dedup.py`
idea; none of its machinery).

- **What.** At admission, a candidate whose `content_terms` Jaccard with an
  already-admitted item is ≥ τ **and whose negation markers match** collides.
  Negation markers (`not`, `no`, `never`, `don't`/`do not`, `avoid`, `without`,
  `instead`) are compared as a set before Jaccard, because the shared stoplist
  drops `not` (`lib.rs:9162`): "Use Stripe for billing" and "Do not use Stripe
  for billing" have Jaccard 1.0 on content terms (verified) and are opposite
  rules. Different subjects need not have a contradiction edge (cross-check
  groups by fact key, `lib.rs:13233`), so the edge check alone does not catch
  this. Keep the newer `observed_at`: if the
  newcomer is newer, `acc.evict` the old one and admit the newcomer; else drop the
  newcomer. Never collapse a pair joined by a contradiction/supersedes edge
  (`has_contradiction_with_any` stays authoritative). Trace as
  `RecallDropReason::Duplicate` (`crates/memphant-types/src/lib.rs:468`).
- **Why exact Jaccard.** The pool is ≤ the served set (≤ ~25), so O(n²) over the
  existing `content_terms`/`jaccard` helpers (`lib.rs:10448-10466`) is trivial;
  MinHash/LSH/`rapidfuzz` would be dead weight.
- **Where.** `admit_or_drop` next to the subject-key dedup (:9877).
- **Metric.** Duplicate-served count and served tokens on the paraphrase split;
  the stale-vs-fresh pair serves the newer body; gold-in-pack does not drop.
- **Cost.** ~40 lines. Tests: `paraphrased_restatement_serves_only_the_newest`,
  `contradicting_pair_is_never_collapsed`,
  `negated_restatement_is_never_collapsed`.

**4. Correction → directive with `Do instead` (napkin N2 + N3): recorded, not built.**

- Syndai just retired its directive lane: serving unverified captured
  directives cost −7.6pp completion (p=0.017), and Syndai's re-entry bar
  requires any new directive lane to be non-repo-derivable, render its trust
  label, carry an observable action with polarity, and show ≥50 held-out
  checkable instances per 14 days **before** it serves anything
  (`Syndai/docs/flows/memory-directive-recall-verdict.md`, Verdict 5). A new
  plugin capture lane without that evidence would repeat the failure.
- What is kept: the `do_instead` arm in Batch 5 measures, offline and at $1 scale,
  whether the `rule` + `Do instead:` format improves adherence over the plain
  decision. Build the capture only if that arm wins and the re-entry bar is met.
- Facts recorded for that day: `_CORRECTION_MARKERS` includes `"no,"`/`"no."`
  (fires on "no, that's fine"), so a summarizer `NONE` gate is needed; the
  server already mints `capture://directive` as a `Procedural` Candidate
  (`service.rs:8098,8116`); `CAPTURE_SOURCES` (`memphant_capture.py:90`) lacks it.

**5. Projection evicts by relevance, not alphabet** (napkin N1).

- **What.** Request `budget_tokens` that fits the 8 KB cap (≈1800, so the
  server's ranker, including ideas 1-3, decides what is served; DRY). Keep the
  stable grouped display order, but when the byte cap still bites, evict the
  **lowest recall-rank** item (the response order), not the last-sorted one.
- **Where.** `memphant_projection.py:113-171,212`.
- **Metric.** `test_cap_evicts_lowest_ranked_not_last_alphabetical`;
  `test_render_groups_labels_and_is_byte_stable` still passes.
- **Cost.** ~15 lines. Local-harness UX only; does not move the Syndai signal.

**6. Deterministic-compiler output pin + gated re-reflect** (graphify
`_AST_CACHE_SCHEMA`).

- **What.** In `write_compiler_golden.rs`, hash the canonical per-case observed
  outputs (actions, bodies, edge kinds; version fields excluded) and compare with
  `const COMPILER_OUTPUT_PIN: (&str, &str) = ("compiler-0.1.0-ws0", "<sha>")`. If
  the hash differs while `COMPILER_VERSION == PIN.0`, the test fails with "compiler
  output changed: bump COMPILER_VERSION and re-pin". That is the whole pin.
- **The pin alone re-derives nothing:** jobs enqueue only at retain. The value is
  delivered by a separate operator command that enqueues reflect for episodes whose
  latest `job_state.compiler_version` ≠ current (owner go, scratch DB tests only).
- **Cost.** ~20 lines for the pin. The backfill is sized separately.

**N4 (conditional napkin importer): no code.** A napkin user sets
`MEMPHANT_CAPTURE_MIRROR_FILES=MEMORY.md,AGENTS.md,napkin.md`; the mirror hook
already captures by basename. Document it in the plugin READMEs.

### Rejected

- **Coverage² BM25 scaling** (graphify #1602). Designed for short label queries.
  Syndai queries are task summaries: gold coverage median ≈0.12, so coverage² ≈0.015
  collapses the whole lexical vote (weight 3.0) below Vector (2.0). Measured 2
  gold ranks worse, 0 better. It is also not the Exact precedent: the magnitude
  would not be the ranking score, so within-channel order would change.
- **Cost tie-break** (graphify shorter-label). Already on the served path as the
  `packing_density_score` tie-break (`lib.rs:9750-9758`).
- **Per-query-term guaranteed slot** (graphify #1445). Re-inflates the block on
  long queries (see idea 2).
- **Per-unit survival marks from `Do instead` string matching** (N3 part). Matching
  an action string in a transcript to a unit is unsound attribution; marks stay
  per-trace.
- **Tree-sitter/NetworkX code graph, per-file LLM summaries** (graphify,
  Understand-Anything). Code is repo-derivable; grep wins by 37.8pp.
- **Hub-avoiding BFS.** Edge expansion is off on the served path (`service.rs:4604`).
- **Structural fingerprints / "empty extraction never proves deletion".** Already
  enforced: normalized episode dedup (`lib.rs:10897`), prompt/schema-salted LLM
  path, fail-closed exact-target replacement (`write_compiler_golden.rs:531`).
- **Corroboration-gated promotion, time-decayed outcomes** (graphify `reflect.py`).
  Shipped as the capture ladder plus DSR decay (`lib.rs:12084-12200`).
- **Napkin's per-category top-10 cap and in-file curation.** Idea 2 is the
  relevance-relative form of the cap; an agent editing its own memory file is
  what the capture trust ladder exists to gate.
- **MinHash/LSH, Jaro-Winkler, `rapidfuzz`.** The pool is too small.
- **graphify's LOCOMO/LME numbers as targets.** Not reproducible; ≈1 SE margins.

### Non-goals

- No change to recall reach (pool depth, embedder, reranker, stemming). If the
  dense arm still misses the three morphology golds after idea 1, the remedy is
  reusing `tokens_related` in BM25 term matching (DRY), never re-admitting stopwords.
- No flags or kill-switches (pre-prod). `ratio` and τ are named constants.
- No Syndai change. Observed for the owner, not fixed here: Syndai directive
  subjects are prose prefixes (restatements fragment), and `shown_unit_ids`
  includes items the 6000-char renderer clips.
- No code-lane benchmarks. No vendoring, so no `THIRD-PARTY-NOTICES` entry.

### Priority

| Rank | Idea | Reaches Syndai prod | Build order |
|---|---|---|---|
| 1 | Content-term filter, all lexical channels | Yes (`/core`) | 1st |
| 2 | Gap cutoff, min-max strength | Yes (`/core`) | 2nd |
| 3 | Near-dup collapse, freshest wins | Yes (`/core`) | 3rd |
| 4 | Correction → directive with `Do instead` | Plugins only | Not built (re-entry bar) |
| 5 | Projection evicts by rank | Plugins only | Independent |
| 6 | Compiler pin (+ gated backfill) | Correctness | Independent |

## Plan

- **Batch 0: measurement first ($0, deterministic).**
  - Add `crates/memphant-core/tests/directive_precision.rs`. It drives
    `MemoryService::scope_core` (the real Syndai path): `token_budget=1200`,
    `serve_captures=false`, the only shape Syndai calls once the directive arm
    is retired.
    - Store: the 29 `benchmarks/data/adherence_cases.jsonl` decisions written
      **the way Syndai's still-served lane writes them**: lane-3 learning
      episodes (`source_ref=syndai:coding-run:<id>`, explicit `subject` +
      `predicate`, `source_kind=agent`), then the reflect queue drained through
      the real admission path. Not `capture://directive`: those are Candidates,
      and the general lane (`serve_captures=false`) never serves a Candidate
      (`recallable`, `lib.rs:11106`); general procedural recall also requires
      `Validated` (`lib.rs:11141`). Plus a paraphrase split (one restatement per
      10 cases, distinct subject), one stale-vs-fresh pair and one negated pair.
      Gold is the case's own decision (1:1 by `id`).
    - Sanity gate, first assertion in the file:
      `fixture_gold_is_served_at_unbounded_budget`. If admission leaves the gold
      unserved, the fixture is measuring nothing: stop and fix the seeding
      (never hand-mint promoted units) before any batch runs. The queue must be
      drained, so the raw-episode fallback (`service.rs:4709`) never stands in
      for reflected units.
    - Embedder: a test `CompressedCosineEmbedding` that maps every unit into
      [0.6, 1] cosine (the bge shape), so the vector channel votes for everything,
      as in prod.
    - Records: gold-in-pack, served count, `token_estimate`, duplicate-served, and
      the served units' channel attribution (from the trace).
  - Add the real-model arm as an `#[ignore]` test in `crates/memphant-runtime`
    under `--features fastembed` (bge-small, CPU, deterministic, no network after
    the model cache is warm).
  - Record both baselines and assert them until Batches 1-3 move them.
- **Sequencing.** Batches 1-3 deploy to prod only after Syndai's core holdout
  has run 7 days (a pre-change window for both arms). Offline batches can start
  at once.
- **Batch 1: idea 1.**
  - Re-run both arms and the LME-S guard (`bench-lme` before/after, then
    `scripts/analyze_lme_pack_nonregression.py`, exact McNemar).
  - Stop rule: stop if the **dense arm** loses any gold from the pack, or McNemar
    shows an r@5 loss. The BM25-only loss of the 3 morphology golds is expected
    and is judged on the dense arm only.
- **Batch 2: idea 2, only if Batch 1 meets its build condition.**
  - Sweep `ratio` ∈ {0.2, 0.35, 0.5} on the fixture's dense arms. Take the largest
    ratio with zero gold loss.
  - The sweep selects on the same 29 cases, so confirm the chosen ratio on
    independent sets: syndai gate hit@5 not reduced (`scripts/gate_run_memphant.py`
    + `scripts/gate_compare.py`, scratch DB via `with_scratch_db.sh`),
    `syndai_docs_negative` forbidden hits stay 0, LME-S guard as in Batch 1.
- **Batch 3: idea 3.** Sweep τ ∈ {0.6, 0.7, 0.8} on the paraphrase split. The
  stale pair must serve the newer body; gold-in-pack must not drop.
- **Batch 4 (independent): ideas 5 and 6-pin.** Python tests run with
  `python3 -m pytest tests/test_projection.py -q`; the pin runs in
  `write_compiler_golden.rs`. The re-reflect backfill waits for owner go.
- **Batch 5: paid behavioural guard (one run).**
  - `scripts/adherence_bench.py` gains `--block retrieved` (inject the actual
    `/core` pack per task, before vs after Batches 1-3) and a `do_instead` arm (the
    decision rewritten as `rule` + `Do instead:`). Otherwise it is identical to the
    fidelity run: n=29, two cross-family judges,
    `doppler run --project syndai --config dev`.
  - This is a **guard, not a decision**: at n=29 with ~20 headroom cases the
    paired MDE is far above any plausible effect. Record the MDE via
    `scripts/instrument_power.py`. Pass = after-arm adherence not below before-arm
    while serving fewer tokens. The ship decision rests on the offline fixture
    (gold non-inferior, fewer tokens) and this guard; Syndai's core holdout is
    the prod revert signal. If that holdout ends with lane 1 deleted, these
    batches stop mattering to Syndai and remain plugin/MCP UX work only.
  - Commit only the compact result with an `evidence_contract` block and register
    it in `benchmarks/manifests/evidence_contract_registry.json` (`contracted`).
- **Docs (same change as the code they describe).**
  - `docs/superpowers/specs/memphant/05-retrieval-and-eval-spec.md` Stage 7: the
    content-term filter, gap cutoff and near-dup collapse. Mirror it to the private
    Syndai checkout so `scripts/check_spec_drift.py` stays clean.
  - `docs/flows/cross-harness-capture-adapters.md`: the projection budget/eviction
    rule, and a one-line note that directive-on-correction capture is deferred
    behind Syndai's re-entry bar.
  - `plugins/claude-code-memphant/README.md`, `plugins/opencode-memphant/README.md`:
    the `napkin.md` mirror recipe.
  - `AGENTS.md` Verification: add the `#[ignore]` dense-arm command. MemPhant has
    no `TESTS.md`; AGENTS.md is the test doc, so do not create one.
  - `docs/superpowers/specs/memphant/STATUS.md`: flip a box only with its proof
    artifact.

## Harness

Each line is independent and exits 0 on the current tree (run 2026-09-29;
`check_evidence_contract.py` takes ~2.5 min).

```sh
cargo test -p memphant-core --test directive_precision
FASTEMBED_CACHE_DIR=<warm cache> cargo test -p memphant-runtime --features fastembed \
  --test directive_precision_bge -- --ignored
# Syndai docs gate (corpus: `git -C <Syndai> archive 6fe7f78f docs | tar -x`, then git init it):
PYTHONPATH=. python3 scripts/gate_run_memphant.py --syndai-root <archived corpus> \
  --golden benchmarks/data/syndai_docs_golden.jsonl --golden benchmarks/data/syndai_docs_golden_v2.jsonl \
  --out-evidence v1-ev.jsonl --out-evidence v2-ev.jsonl --out-provenance v1-prov.json --out-provenance v2-prov.json \
  --embed-model small --mode fast --k 10 --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker --cli-bin target/release/memphant-cli
cargo test -p memphant-core --lib -- bm25_
python3 -m pytest tests/test_projection.py tests/test_shared_capture.py tests/test_claude_code_capture.py -q
python3 -m pytest tests/test_gate_compare.py tests/test_packing_sufficiency_screen.py -q
python3 scripts/check_evidence_contract.py
python3 scripts/instrument_power.py --check
python3 scripts/check_spec_drift.py
```

## Critique log

Kept (finding → change):

- The served path is `/core`, with `limit=1000` and `budget=1200`; the old fixture
  used `k=8/512` → Batch 0 drives `scope_core` at the prod shape.
- "Prod embeddings off" was false; dense has been on since `2c494080` → added a
  compressed-cosine CI embedder and a bge-small arm.
- The gap strength `s/top` cannot cut vector units (bge ~[0.6,1]) → min-max
  normalization; the sweep is widened.
- The per-term exemption re-inflates on ≤600-char task queries → removed.
- Coverage² crushes the lexical vote on long queries (measured) and breaks the
  "order unchanged" claim → rejected.
- The cost tie-break duplicates `packing_density_score` → rejected.
- The stopword leak also hits Exact via prose subjects (12.7/29 per task) → idea 1
  covers every lexical channel.
- Two stoplists → unified (DRY).
- The fixture's NoopEmbedding loses 3 morphology golds → the stop rule is judged on
  the dense arm.
- Selecting `ratio` on n=29 overfits → confirm on the gate goldens and LME.
- The paid n=29 run is underpowered → relabelled a guard.
- The compiler bump alone re-derives nothing → the claim is corrected, and the pin
  is kept minimal.
- The projection is not on the Syndai path → N1 is ranked as plugin UX.
- N4 is already configurable → docs only.
- Fused tie-break line cite (`:7943`) was the per-channel sort → removed.
- Docs updates were missing → added.

Eng review + outside voice (codex, 2026-09-30), kept:

- Batch 0 seeded `capture://directive` Candidates and queried `serve_captures=false`,
  which never serves a Candidate → seed lane-3 learnings through real admission,
  with a gold-is-served sanity gate.
- Near-dup collapse would merge opposite rules (stoplist drops `not`; verified
  Jaccard 1.0) → negation markers must match.
- Min-max strength splits near-ties into 0/1 and divides by zero on ties → a
  minimum-range guard; and idea 2 is conditional on Batch 1, since filtering
  already does the lexical work.
- Idea 4 would open a new directive lane right after Syndai retired one for
  harm → recorded behind the re-entry bar; Batch 5's `do_instead` arm is the
  measurement it needs first.
- The prod "decisional A/B" no longer exists (Syndai deletes the cohort split),
  and the ~12-unit "before" included captures → offline fixture is the ship bar,
  prod is an observational guardrail against a post-retirement baseline.
- The Exact stopword figure came from the retired directive subject shape →
  re-measured on still-served shapes.

Rejected findings:

- "Gap cutoff duplicates submodular ordering": submodular only reorders and never
  drops.
- "The 29-case fixture has no identifiable gold": it has 1:1 decision↔task by `id`.
- "File-size limit on `lib.rs`": MemPhant has no size gate; new tests go in
  `tests/` anyway.

## GSTACK REVIEW REPORT

Eng review of three linked specs (2026-09-30): Syndai `docs/flows/memory-directive-recall-verdict.md` (A),
Syndai `docs/flows/run-failure-attribution-observability.md` (B), MemPhant
`docs/specs/graphify-understand-anything-napkin-digest.md` (C). Every finding was checked against code
before it was folded in. Issues were decided in-session under the repo's "decide rather than ask" rule;
each is recorded in its spec's Critique log so Sid can override any one.

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | codex exec (gpt, read-only) | Independent 2nd opinion | 1 | completed | 7 findings (2 P1, 5 P2), 7 verified and folded |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues folded | 13 issues, 0 critical gaps open |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | n/a (no UI) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

Findings folded (spec: finding → change):
- A: raw-episode fallback (`degraded_read_your_own_writes`) bypassed the `captured_*` filter → dropped too (codex P1).
- A: drop count logged where `coding_run_id` is out of scope → rides `memory_recall` as `dropped_units`.
- A: deleting `partial` silently kills decision capture → kept + regression test (codex).
- A: two `DIRECTIVE_EXTRACTION` map rows, not one → both named.
- A: no post-retirement baseline for C → Batch 3 records one.
- B: chosen blocker could be a failure later repaired and passed → per-validator current outcome first (codex).
- B: `unknown_executor_failure` is the unmatched fallback → `unknown`, not `provider` (codex).
- B: idle reap also fires on an agent's own silent command → `stalled` label.
- B: "fault" labels asserted blame → `blocked_by` with observed-source labels (codex).
- C: Batch 0 seeded Candidates the general lane never serves → lane-3 learnings through admission + sanity gate (mine + codex).
- C: Jaccard dedup merges opposite rules (`not` is a stopword) → negation markers must match (codex P1).
- C: min-max strength splits near-ties / divides by zero on ties → range guard; idea 2 conditional on Batch 1 (codex).
- C: idea 4 opens a directive lane right after A retires one for harm → recorded behind A's re-entry bar.
- C: prod "decisional A/B" deleted by A; ~12-unit "before" included captures → offline fixture is the ship bar, prod is an observational guardrail (codex).

Test coverage (new/changed paths → named tests in each Plan):
```
A  load_turn1_memory_core filter ─ captured_* [T] ─ degraded raw episode [T] ─ lane-3 kept [T] ─ count [T]
A  memory_recall emit ─ coding_run_id + dropped_units [T]
A  finalize ─ no directive LLM call [T] ─ decision capture still calls LLM [T, REGRESSION]
B  edit_progress fold ─ claude/pi/codex/unknown [T] ─ dedupe by tool id [T]
B  blocked_by rules ─ 9 labels [T each] ─ repaired-then-passed never blamed [T] ─ absent-never-null [T]
B  terminalize pillar stamp ─ happy [T] ─ DB error keeps terminal flip [T]
C  fixture ─ gold served at unbounded budget [T, gate] ─ negated pair kept [T] ─ ties never cut [T]
```

NOT in scope: MemPhant forget/erasure of old captures (A's filter makes them inert); decision capture's
per-pass re-extraction cost (separate fix); `failure_class`/repair-guard changes; classifying `errors`
stack text; enhancer/localizer signals (intake-localizer spec); a new directive capture lane anywhere.

What already exists (reused): `inclusion_reason` labels (MemPhant), `memory_recall` emit,
`terminalize` single chokepoint + `RUN_SPANS` registry, `pillar_rollup`, `coding_execution_telemetry`
stream readers, `packing_density_score` tie-break, `content_terms`/`jaccard`, `acc.evict`,
`MEMPHANT_CAPTURE_MIRROR_FILES` (napkin import needs no code).

Failure modes: MemPhant response missing `inclusion_reason` → parsed as `""`, item served (lenient;
covered by `test_core_item_without_inclusion_reason_still_parses`); attribution SELECT error → fail-open
inside the existing SAVEPOINT (tested); fixture seeding yields no served gold → sanity gate fails loudly.
0 critical gaps (no silent + untested + unhandled path).

Parallelization: Lane 1 = A (Syndai `features/memory`, `engine_loop/*memory*`); Lane 2 = B (Syndai
`features/coding` spine/telemetry/land) — independent, separate CaaS runs; Lane 3 = C offline batches
(MemPhant repo). C prod deploy waits for A's Batch 3 baseline.

- **OUTSIDE COVERAGE:** codex, plan-review phase, completed, 7 findings, all verified against code and folded.
- **CROSS-MODEL:** both reviewers independently found the Batch 0 seeding bug in C; codex alone found the
  raw-episode bypass, the negation merge and the `partial` break; no finding was disputed.
- **VERDICT:** ENG + OUTSIDE CLEARED for build after the folded changes — awaiting Sid's "build".

NO UNRESOLVED DECISIONS
