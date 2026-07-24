//! LongMemEval retrieval-only benchmark lane against the packaged Postgres
//! runtime (`MemoryService<PgStore>`).
//!
//! Per sampled question (stratified by `question_type`, seeded, identical
//! sample for every run at the same seed): create a fresh tenant, ingest each
//! haystack session chronologically as ONE episode (turns concatenated as
//! `role: content`, body prefixed with the session id and date), reflect via
//! the worker claim/complete path, then recall the question and score
//! Recall@5/@10 by provenance: a top-k item hits when its
//! `citation_episode_id` maps back to a session in `answer_session_ids`.
//! Abstention questions (`_abs` in the question id) are scored separately.
//!
//! Honesty header: every report records the dataset sha256, sample seed,
//! `runtime: "postgres"`, `retrieval_only: true` and the exact command line.
//! This lane makes NO reader/QA-accuracy claim by itself.
//!
//! Reader lane: `--emit-qa <path>` additionally writes one JSONL row per
//! question (question, question_date, gold answer, top-k evidence bodies with
//! provenance) so `scripts/run_reader.py` can drive an external reader/judge
//! (`claude -p`) without re-running ingestion. QA accuracy is computed and
//! labeled by that script, never by this lane.
//!
//! Granularity: `--granularity session` (the lane default again — the product
//! path is session ingestion + service-side runtime contextual chunks, see
//! `DEFAULT_GRANULARITY`) ingests each haystack session as ONE episode;
//! `--granularity turns` (still available for the ablation) ingests each
//! session as multiple episodes of up to `--turns-window` consecutive turns
//! (default `DEFAULT_TURNS_WINDOW`=4, same `[session <id>]` prefix), mapping
//! every minted episode back to its session for provenance scoring.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{EmbeddingProvider, MemoryStore, NoopEmbedding, SystemClock};
use memphant_store_postgres::PgStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    RecallDropReason, RecallMode, RecallRequest, RetainEpisodeHttpRequest,
    RetainEpisodeHttpResponse, RetainEpisodePayload, RetainPayload, TenantId, TrustLevel, UnitId,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const BOOTSTRAP_RESAMPLES: usize = 1000;
/// Default turn-window size for `--granularity turns`, overridable via
/// `--turns-window`.
pub const DEFAULT_TURNS_WINDOW: usize = 4;
/// Default packing token budget threaded to the recall call, overridable via
/// `--budget-tokens`.
pub const DEFAULT_BUDGET_TOKENS: usize = 8192;
/// Lane default ingestion granularity, back to "session" as of the 2026-07-10
/// rung 4 promotion: the lane now measures the PRODUCT path (session ingestion
/// plus service-side runtime contextual chunks, default-on). The earlier
/// same-day "turns" promotion is SUPERSEDED by the runtime embodiment: runtime
/// chunks tie client-side turns windowing (ΔQA +0.000 [−0.080, +0.080] ns)
/// while needing no caller-side windowing, so the product path is measured
/// directly. Proof:
/// `docs/build-log/artifacts/real-retrieval-20260710/scaled-reader-or-session-chunkpack-rerank-off.json`.
/// `--granularity turns` stays available for the ablation. The serde
/// `default_granularity` below also reads "session", but for an independent
/// reason (pre-granularity REPORTS were session runs), not this lane default.
pub const DEFAULT_GRANULARITY: &str = "session";

#[derive(Debug, Clone, Deserialize)]
pub struct LmeQuestion {
    pub question_id: String,
    pub question_type: String,
    pub question: String,
    /// Gold answer (string or number in the published dataset).
    #[serde(default)]
    pub answer: serde_json::Value,
    #[serde(default)]
    pub question_date: Option<String>,
    pub haystack_session_ids: Vec<String>,
    pub haystack_dates: Vec<String>,
    pub haystack_sessions: Vec<Vec<LmeTurn>>,
    pub answer_session_ids: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LmeTurn {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BenchLmeOptions {
    pub database_url: String,
    pub data_path: String,
    pub sample: usize,
    pub seed: u64,
    pub k: usize,
    /// One of: vector, edge_expansion, procedure_recall, decay, packing.
    pub disable: Option<String>,
    pub mode: RecallMode,
    /// Baseline report path for paired per-question deltas.
    pub baseline: Option<String>,
    /// Ingestion granularity: "session" (one episode per haystack session)
    /// or "turns" (episodes of up to `turns_window` consecutive turns).
    pub granularity: String,
    /// Turn-window size for `--granularity turns` (no-op for "session").
    pub turns_window: usize,
    /// Packing token budget threaded to the recall call.
    pub budget_tokens: usize,
    /// R1.5-T0 recall-pool-depth (`--pool`) threaded to the recall service
    /// via `with_recall_pool_depth` — the ONE knob every internal
    /// channel/fusion limit in the recall path derives from (vector-channel
    /// KNN fan-out, packing scan window, rerank rescoring cap), never `k`.
    /// The flag name is unchanged (pre-R1.5-T0 it only set the vector-channel
    /// fan-out via `with_candidate_pool_size`); its semantics widened to the
    /// unified knob so there is one honest pool concept, not two. Default
    /// `DEFAULT_RECALL_POOL_DEPTH` (64).
    pub pool: usize,
    /// W4 sibling-gather packing lever (`--sibling-gather`, default off) threaded
    /// via `with_sibling_gather_enabled`. The measurement-campaign flag; off
    /// reproduces today's packing.
    pub sibling_gather: bool,
    /// W4 per-session diversity quota (`--session-quota <n>`, default off =
    /// `None`) threaded via `with_session_quota`.
    pub session_quota: Option<usize>,
    /// Rung-7 per-item render cap (`--pack-render-cap <n>`, default off = `None`)
    /// threaded via `with_pack_render_cap`. Bounds each packed item's chunk-render
    /// budget so a large chunk-matched body cannot hog the pack budget.
    pub pack_render_cap: Option<usize>,
    /// Budgeted submodular evidence ordering (`--pack-submodular-ordering`,
    /// default off).
    pub pack_submodular_ordering: bool,
    /// W5 temporal-grounding flag (`--temporal-grounding`, default off) threaded
    /// via `with_temporal_grounding_enabled` to BOTH the ingest service (so
    /// `valid_from` and chunk headers are date-grounded at reflect) and the
    /// recall service (query-date windowing + dated packs).
    pub temporal_grounding: bool,
    /// W6 deterministic fact-extraction flag (`--fact-extraction`, default off)
    /// threaded via `with_fact_extraction_enabled` to the INGEST service so the
    /// reflect stage mines preference/attribute facts. Recall needs no flag —
    /// mined facts are ordinary units.
    pub fact_extraction: bool,
    /// Rung 4 runtime contextual-chunk write path opt-in flag
    /// (`--runtime-chunks`, default true = the product path). The EFFECTIVE
    /// state also depends on `--disable runtime_chunks`, which forces the
    /// chunks-off control arm; see `runtime_chunks_enabled` in `run_bench_lme`.
    pub runtime_chunks: bool,
    /// W8 embedding arm (`--embed-model`, `small` [default] | `base`). Derived
    /// ONCE and shared by the ingest and recall services so their embedding
    /// profiles always match (mixing 384d/768d vectors is incoherent).
    pub embed_model: String,
    /// W8 cross-encoder rerank flag (`--cross-rerank`, default off). Requires the
    /// fastembed feature; installs the real reranker on the recall service via
    /// `with_cross_reranker`. Off reproduces today's fusion order.
    pub cross_rerank: bool,
    /// Cross-rerank doc granularity (`--rerank-granularity body|chunk`, default
    /// body = today's behavior) threaded via `with_cross_rerank_granularity`.
    /// `chunk` reranks each candidate's flattened `contextual_chunks` bodies
    /// and max-pools back per candidate. Inert without `--cross-rerank`.
    pub rerank_granularity: memphant_core::CrossRerankGranularity,
    /// When set, write one QA-evidence JSONL row per question to this path
    /// (question + gold answer + top-k evidence bodies) for the external
    /// reader/judge in `scripts/run_reader.py`.
    pub emit_qa: Option<String>,
    /// A1 (tri-domain-sota-plan §3.A1): when set, write one trace-classification
    /// JSONL row per question to this path (Fast-miss bucket + pool/top-k gold
    /// membership) from the retrieval trace. FREE — no reader/model call.
    pub emit_trace_classification: Option<String>,
    pub command: String,
}

/// One top-k evidence item handed to the external reader.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QaEvidenceItem {
    pub rank: usize,
    /// Haystack session this item's citation maps back to, when known.
    pub session_id: Option<String>,
    pub body: String,
}

/// One QA-evidence JSONL row (input contract of `scripts/run_reader.py`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QaEvidenceRow {
    pub question_id: String,
    pub question_type: String,
    pub is_abstention: bool,
    pub question: String,
    pub question_date: Option<String>,
    pub gold_answer: serde_json::Value,
    pub abstained: bool,
    pub granularity: String,
    pub k: usize,
    pub evidence: Vec<QaEvidenceItem>,
}

/// One A1 trace-classification JSONL row (tri-domain-sota-plan §3.A1). Written
/// per question when `--emit-trace-classification <path>` is set; FREE
/// (retrieval-trace only, no reader/model call). `gold_in_pool` and
/// `gold_in_topk` are the two membership signals the bucket derives from,
/// carried explicitly so the classification is auditable per question.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceClassificationRow {
    pub question_id: String,
    pub question_type: String,
    pub bucket: FastMissBucket,
    pub gold_in_pool: bool,
    pub gold_in_topk: bool,
    /// 1-based rank of the first gold-bearing packed item, if any.
    pub first_answer_rank: Option<usize>,
    /// Distinct pool candidate units traced for this recall.
    pub pool_size: usize,
    /// Packed top-k items returned for this recall.
    pub packed_size: usize,
    /// Rung-7 diagnosis (populated for every scored row; the in_pool_unpacked
    /// bucket is where it matters). Fused rank (1-based) of the best-ranked
    /// gold-bearing pool unit, if any gold unit reached the fused candidate
    /// trace.
    #[serde(default)]
    pub gold_fused_rank: Option<usize>,
    /// Fused score of that same best-ranked gold pool unit.
    #[serde(default)]
    pub gold_fused_score: Option<f32>,
    /// Why the best-ranked gold pool unit was dropped during packing
    /// (Duplicate / Budget / Rerank / …), or `None` if it never appears in the
    /// pack `dropped_items` (it either survived into the pack but below the
    /// answer, or was never reached).
    #[serde(default)]
    pub gold_drop_reason: Option<RecallDropReason>,
    /// The recall `k` this run packed against, for budget/ordering context.
    #[serde(default)]
    pub k: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QuestionResult {
    pub question_id: String,
    pub question_type: String,
    pub is_abstention: bool,
    /// None for abstention questions (scored separately).
    pub hit_at_5: Option<bool>,
    pub hit_at_10: Option<bool>,
    /// Some(...) only for abstention questions: correct when recall abstained
    /// or returned no answer-session item.
    pub abstention_correct: Option<bool>,
    /// 1-based rank of the first answer-bearing item, if any.
    pub first_answer_rank: Option<usize>,
    pub returned_items: usize,
    pub degraded: bool,
    pub ingested_sessions: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StratumMetrics {
    pub question_type: String,
    pub n: usize,
    pub n_scored: usize,
    pub recall_at_5: Option<f64>,
    pub recall_at_10: Option<f64>,
    pub abstention_n: usize,
    pub abstention_correct: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeltaCi {
    pub mean: f64,
    pub ci95_low: f64,
    pub ci95_high: f64,
    /// True when the bootstrap 95% CI excludes zero.
    pub ci_excludes_zero: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PairedComparison {
    pub baseline_path: String,
    pub n_paired: usize,
    pub delta_recall_at_5: DeltaCi,
    pub delta_recall_at_10: DeltaCi,
    pub bootstrap_resamples: usize,
    pub bootstrap_seed: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BenchLmeReport {
    pub benchmark: String,
    pub dataset_path: String,
    pub dataset_sha256: String,
    pub dataset_questions: usize,
    pub sample_seed: u64,
    pub sample_n: usize,
    pub k: usize,
    pub runtime: String,
    pub retrieval_only: bool,
    pub embeddings: String,
    pub ingestion: String,
    pub reflect: String,
    /// "session" or "turns" (defaults to "session" for pre-granularity reports).
    #[serde(default = "default_granularity")]
    pub granularity: String,
    /// Turn-window size used for `--granularity turns` (defaults to 4 for
    /// pre-flag reports — see `default_turns_window`).
    #[serde(default = "default_turns_window")]
    pub turns_window: usize,
    /// Packing token budget threaded to the recall call (defaults to 8192
    /// for pre-flag reports — see `default_budget_tokens`).
    #[serde(default = "default_budget_tokens")]
    pub budget_tokens: usize,
    /// R1.5-T0 recall-pool-depth (`--pool`) used for this run — the ONE knob
    /// every internal channel/fusion limit in the recall path derives from.
    /// Renamed from `candidate_pool_size` (which pre-R1.5-T0 only set the
    /// vector-channel KNN fan-out); `#[serde(alias)]` keeps old reports
    /// (written under the old field name) parseable. Defaults, for reports
    /// written before EITHER field existed, to 32 — the historical
    /// vector-channel-only fan-out those runs actually used
    /// (`VECTOR_CANDIDATE_LIMIT`, NOT today's `DEFAULT_RECALL_POOL_DEPTH` —
    /// see `default_recall_pool_depth_for_legacy_reports`).
    #[serde(alias = "candidate_pool_size")]
    #[serde(default = "default_recall_pool_depth_for_legacy_reports")]
    pub recall_pool_depth: usize,
    /// Whether the W4 sibling-gather packing lever was on for this run. The serde
    /// default is `false`: every report written before the lever existed was a
    /// sibling-gather-off run, so an absent field ⇒ off.
    #[serde(default)]
    pub sibling_gather: bool,
    /// The W4 per-session diversity quota used for this run (`--session-quota`),
    /// or `None` when off. Serde default `None` for pre-flag reports.
    #[serde(default)]
    pub session_quota: Option<usize>,
    /// The rung-7 per-item render cap used for this run (`--pack-render-cap`), or
    /// `None` when off. Serde default `None` for pre-flag reports.
    #[serde(default)]
    pub pack_render_cap: Option<usize>,
    /// Whether budgeted submodular evidence ordering was enabled.
    #[serde(default)]
    pub pack_submodular_ordering: bool,
    /// Whether W5 temporal grounding (`--temporal-grounding`) was on for this
    /// run. Serde default `false`: every report written before the flag existed
    /// was a temporal-grounding-off run, so an absent field ⇒ off.
    #[serde(default)]
    pub temporal_grounding: bool,
    /// Whether W6 deterministic fact extraction (`--fact-extraction`) was on for
    /// this run. Serde default `false`: every report written before the flag
    /// existed was a fact-extraction-off run, so an absent field ⇒ off.
    #[serde(default)]
    pub fact_extraction: bool,
    /// Whether the rung 4 runtime contextual-chunk write path was enabled for
    /// this run — records the EFFECTIVE state (default-on since the 2026-07-10
    /// promotion; `--disable runtime_chunks` records false). The serde default
    /// STAYS false: every pre-promotion report was a chunks-off run unless it
    /// recorded otherwise, so an absent field ⇒ false, never following the lane
    /// default.
    #[serde(default)]
    pub runtime_chunks: bool,
    /// W8 embedding arm selector for this run (`small` | `base`). Serde default
    /// `small`: every report written before the arm existed used bge-small.
    #[serde(default = "default_embed_model")]
    pub embed_model: String,
    /// The active embedder's dimensionality (bge-small=384, bge-base=768) — the
    /// second half of the embedder id+dims provenance. Serde default 384 for
    /// pre-flag reports (all of which were bge-small runs).
    #[serde(default = "default_embedding_dimensions")]
    pub embedding_dimensions: usize,
    /// Whether the W8 cross-encoder rerank (`--cross-rerank`) was on for this
    /// run. Serde default `false`: every report written before the flag existed
    /// was a rerank-off run, so an absent field ⇒ off.
    #[serde(default)]
    pub cross_rerank: bool,
    pub mode: String,
    pub disabled: Option<String>,
    pub command: String,
    pub generated_at_unix: u64,
    pub overall: StratumMetrics,
    pub strata: Vec<StratumMetrics>,
    pub per_question: Vec<QuestionResult>,
    pub paired_vs_baseline: Option<PairedComparison>,
}

fn default_granularity() -> String {
    "session".to_string()
}

/// Parsing default for pre-flag reports (no `turns_window` field). Unlike
/// `default_granularity`, this genuinely coincides with the lane default
/// (`DEFAULT_TURNS_WINDOW`): every report ever written before this field
/// existed actually used window 4.
fn default_turns_window() -> usize {
    DEFAULT_TURNS_WINDOW
}

/// Parsing default for pre-flag reports (no `budget_tokens` field). As with
/// `default_turns_window`, this coincides with the lane default
/// (`DEFAULT_BUDGET_TOKENS`): every report ever written before this field
/// existed actually used budget 8192.
fn default_budget_tokens() -> usize {
    DEFAULT_BUDGET_TOKENS
}

/// Parsing default for reports written before EITHER `candidate_pool_size`
/// (pre-R1.5-T0) or `recall_pool_depth` (R1.5-T0+) existed. UNLIKE
/// `default_budget_tokens`, this deliberately does NOT coincide with today's
/// lane default (`DEFAULT_RECALL_POOL_DEPTH`, 64): every report old enough to
/// be missing both fields predates this flag entirely and actually ran with
/// the historical vector-channel-only fan-out, `VECTOR_CANDIDATE_LIMIT` (32).
fn default_recall_pool_depth_for_legacy_reports() -> usize {
    memphant_core::VECTOR_CANDIDATE_LIMIT
}

/// Parsing default for pre-flag reports (no `embed_model` field): every such
/// report was a bge-small run.
fn default_embed_model() -> String {
    "small".to_string()
}

/// Parsing default for pre-flag reports (no `embedding_dimensions` field): the
/// bge-small dimensionality every such report used.
fn default_embedding_dimensions() -> usize {
    384
}

/// Deterministic splitmix64 PRNG — no external randomness anywhere in the
/// lane, so a (seed, sample) pair always names the same question set.
pub struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    pub fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z ^ (z >> 31)
    }

    pub fn next_below(&mut self, bound: usize) -> usize {
        (self.next_u64() % bound.max(1) as u64) as usize
    }
}

fn stratum_seed(seed: u64, stratum: &str) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for byte in stratum.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    seed ^ hash
}

/// Stratified, seeded sample: proportional allocation (largest remainder,
/// minimum one per stratum when the budget allows), then a seeded
/// Fisher-Yates shuffle inside each stratum sorted by question id.
pub fn stratified_sample_ids(
    questions: &[(String, String)],
    sample: usize,
    seed: u64,
) -> Vec<String> {
    let mut strata: Vec<(String, Vec<String>)> = Vec::new();
    for (id, stratum) in questions {
        match strata.iter_mut().find(|(name, _)| name == stratum) {
            Some((_, ids)) => ids.push(id.clone()),
            None => strata.push((stratum.clone(), vec![id.clone()])),
        }
    }
    strata.sort_by(|left, right| left.0.cmp(&right.0));
    for (_, ids) in &mut strata {
        ids.sort();
    }
    let total = questions.len();
    let sample = sample.min(total);

    // Largest-remainder proportional allocation.
    let mut allocations: Vec<(usize, f64)> = strata
        .iter()
        .map(|(_, ids)| {
            let exact = sample as f64 * ids.len() as f64 / total as f64;
            (exact.floor() as usize, exact - exact.floor())
        })
        .collect();
    if sample >= strata.len() {
        for (index, (floor, _)) in allocations.iter_mut().enumerate() {
            if *floor == 0 && !strata[index].1.is_empty() {
                *floor = 1;
            }
        }
    }
    let mut assigned: usize = allocations.iter().map(|(floor, _)| *floor).sum();
    let mut order: Vec<usize> = (0..allocations.len()).collect();
    order.sort_by(|a, b| {
        allocations[*b]
            .1
            .partial_cmp(&allocations[*a].1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| strata[*a].0.cmp(&strata[*b].0))
    });
    let mut cursor = 0;
    while assigned < sample {
        let index = order[cursor % order.len()];
        if allocations[index].0 < strata[index].1.len() {
            allocations[index].0 += 1;
            assigned += 1;
        }
        cursor += 1;
        if cursor > order.len() * (sample + 1) {
            break; // every stratum exhausted
        }
    }
    while assigned > sample {
        let index = order[cursor % order.len()];
        if allocations[index].0 > 1 {
            allocations[index].0 -= 1;
            assigned -= 1;
        }
        cursor += 1;
    }

    let mut picked = Vec::new();
    for ((stratum, ids), (count, _)) in strata.iter().zip(allocations.iter()) {
        let mut pool = ids.clone();
        let mut rng = SplitMix64::new(stratum_seed(seed, stratum));
        for index in (1..pool.len()).rev() {
            let swap = rng.next_below(index + 1);
            pool.swap(index, swap);
        }
        picked.extend(pool.into_iter().take((*count).min(ids.len())));
    }
    picked.sort();
    picked
}

/// A1 (tri-domain-sota-plan §3.A1) Fast-miss classification bucket. A scored
/// question lands in exactly one bucket; `Abstention` is set aside from the
/// miss denominator. The binding verdict is `AbsentFromPool` share vs the rest:
/// only `AbsentFromPool` is a recall-DEPTH miss Deep can fix; `InPoolUnpacked`
/// is a packing/ordering miss and `InTopK` is a reader-utilization miss.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FastMissBucket {
    /// No pool candidate maps to a gold session — recall never surfaced it.
    AbsentFromPool,
    /// A gold candidate is in the pool but not in the packed top-k.
    InPoolUnpacked,
    /// A gold-bearing item is in the packed top-k (retrieval hit).
    InTopK,
    /// Abstention question (`_abs`): classified separately, never a miss.
    Abstention,
}

/// Pure classifier: given the sessions of ALL pool candidates (`pool_sessions`)
/// and of the packed top-k items (`topk_sessions`), decide the A1 bucket. Both
/// slices are rank-ordered session ids, `None` when an item has no episode
/// provenance. `InTopK` is checked first (a hit is a hit regardless of the
/// pool), then pool membership splits the misses.
pub fn classify_question(
    pool_sessions: &[Option<String>],
    topk_sessions: &[Option<String>],
    answer_session_ids: &[String],
    is_abstention: bool,
) -> FastMissBucket {
    if is_abstention {
        return FastMissBucket::Abstention;
    }
    let gold = |session: &Option<String>| {
        session
            .as_ref()
            .is_some_and(|s| answer_session_ids.iter().any(|answer| answer == s))
    };
    if topk_sessions.iter().any(gold) {
        FastMissBucket::InTopK
    } else if pool_sessions.iter().any(gold) {
        FastMissBucket::InPoolUnpacked
    } else {
        FastMissBucket::AbsentFromPool
    }
}

/// Rung-7 packing diagnosis (FREE, retrieval-trace only). Of the gold-bearing
/// pool units, pick the BEST-ranked one (min `fused_rank`) and report its fused
/// rank and its pack drop reason. `candidate_ranks` is `(unit, fused_rank)` from
/// the trace's fused candidates; `drops` is `(unit, reason)` from the pack
/// `dropped_items`. A gold unit absent from `drops` returns `None` for the
/// reason — it either survived the pack loop but sat below the answer, or was
/// never reached. A gold unit absent from `candidate_ranks` (or with no fused
/// rank) returns `(None, ...)`.
pub fn classify_gold_drop_cause(
    gold_unit_ids: &[UnitId],
    candidate_ranks: &[(UnitId, Option<usize>)],
    drops: &[(UnitId, RecallDropReason)],
) -> (Option<usize>, Option<RecallDropReason>) {
    let best = gold_unit_ids
        .iter()
        .filter_map(|gold| {
            candidate_ranks
                .iter()
                .find(|(unit, _)| unit == gold)
                .and_then(|(_, rank)| rank.map(|r| (*gold, r)))
        })
        .min_by_key(|(_, rank)| *rank);
    match best {
        Some((gold, rank)) => {
            let reason = drops
                .iter()
                .find(|(unit, _)| *unit == gold)
                .map(|(_, reason)| *reason);
            (Some(rank), reason)
        }
        None => (None, None),
    }
}

/// Pure scorer: `item_sessions` is the recalled items' provenance
/// (rank-ordered session ids, None when an item has no episode citation).
pub fn score_question(
    item_sessions: &[Option<String>],
    answer_session_ids: &[String],
    is_abstention: bool,
    abstained: bool,
) -> (Option<bool>, Option<bool>, Option<bool>, Option<usize>) {
    let first_answer_rank = item_sessions.iter().enumerate().find_map(|(index, item)| {
        item.as_ref()
            .filter(|session| answer_session_ids.iter().any(|answer| answer == *session))
            .map(|_| index + 1)
    });
    if is_abstention {
        let correct = abstained || first_answer_rank.is_none();
        (None, None, Some(correct), first_answer_rank)
    } else {
        let hit5 = first_answer_rank.is_some_and(|rank| rank <= 5);
        let hit10 = first_answer_rank.is_some_and(|rank| rank <= 10);
        (Some(hit5), Some(hit10), None, first_answer_rank)
    }
}

/// Bootstrap 95% CI over per-question paired deltas (seeded, deterministic).
pub fn bootstrap_ci(deltas: &[f64], resamples: usize, seed: u64) -> DeltaCi {
    let n = deltas.len();
    let mean = if n == 0 {
        0.0
    } else {
        deltas.iter().sum::<f64>() / n as f64
    };
    if n == 0 {
        return DeltaCi {
            mean,
            ci95_low: 0.0,
            ci95_high: 0.0,
            ci_excludes_zero: false,
        };
    }
    let mut rng = SplitMix64::new(seed);
    let mut means = Vec::with_capacity(resamples);
    for _ in 0..resamples {
        let mut sum = 0.0;
        for _ in 0..n {
            sum += deltas[rng.next_below(n)];
        }
        means.push(sum / n as f64);
    }
    means.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    let low = means[((resamples as f64 * 0.025).floor() as usize).min(resamples - 1)];
    let high = means[((resamples as f64 * 0.975).ceil() as usize - 1).min(resamples - 1)];
    DeltaCi {
        mean,
        ci95_low: low,
        ci95_high: high,
        ci_excludes_zero: low > 0.0 || high < 0.0,
    }
}

fn aggregate(name: &str, rows: &[&QuestionResult]) -> StratumMetrics {
    let scored: Vec<_> = rows.iter().filter(|row| !row.is_abstention).collect();
    let abstentions: Vec<_> = rows.iter().filter(|row| row.is_abstention).collect();
    let ratio = |hits: usize, n: usize| (n > 0).then(|| hits as f64 / n as f64);
    StratumMetrics {
        question_type: name.to_string(),
        n: rows.len(),
        n_scored: scored.len(),
        recall_at_5: ratio(
            scored
                .iter()
                .filter(|row| row.hit_at_5 == Some(true))
                .count(),
            scored.len(),
        ),
        recall_at_10: ratio(
            scored
                .iter()
                .filter(|row| row.hit_at_10 == Some(true))
                .count(),
            scored.len(),
        ),
        abstention_n: abstentions.len(),
        abstention_correct: abstentions
            .iter()
            .filter(|row| row.abstention_correct == Some(true))
            .count(),
    }
}

fn paired_comparison(
    current: &[QuestionResult],
    baseline: &BenchLmeReport,
    baseline_path: &str,
    seed: u64,
) -> PairedComparison {
    let baseline_rows: HashMap<&str, &QuestionResult> = baseline
        .per_question
        .iter()
        .map(|row| (row.question_id.as_str(), row))
        .collect();
    let mut deltas5 = Vec::new();
    let mut deltas10 = Vec::new();
    for row in current.iter().filter(|row| !row.is_abstention) {
        let Some(base) = baseline_rows.get(row.question_id.as_str()) else {
            continue;
        };
        let (Some(hit5), Some(hit10), Some(base5), Some(base10)) =
            (row.hit_at_5, row.hit_at_10, base.hit_at_5, base.hit_at_10)
        else {
            continue;
        };
        deltas5.push(f64::from(u8::from(hit5)) - f64::from(u8::from(base5)));
        deltas10.push(f64::from(u8::from(hit10)) - f64::from(u8::from(base10)));
    }
    PairedComparison {
        baseline_path: baseline_path.to_string(),
        n_paired: deltas5.len(),
        delta_recall_at_5: bootstrap_ci(&deltas5, BOOTSTRAP_RESAMPLES, seed),
        delta_recall_at_10: bootstrap_ci(&deltas10, BOOTSTRAP_RESAMPLES, seed),
        bootstrap_resamples: BOOTSTRAP_RESAMPLES,
        bootstrap_seed: seed,
    }
}

/// Normalize an upstream LongMemEval haystack date to RFC3339 for `observed_at`.
/// Upstream format is `YYYY/MM/DD (Day) HH:MM` (e.g. `2023/05/20 (Sat) 07:47`);
/// the `retain` contract requires RFC3339 (`service.rs` `parse_rfc3339`). The
/// real `HH:MM` is preserved — within-day chronological ordering depends on it —
/// and reshaped to `YYYY-MM-DDTHH:MM:00Z`. A value already containing `T` (an
/// RFC3339 timestamp) passes through unchanged; a bare `YYYY/MM/DD` becomes
/// midnight.
fn normalize_haystack_date(raw: &str) -> String {
    // Already RFC3339 (`YYYY-MM-DDThh:mm:ssZ`): dashes in the date, no slashes,
    // a `T` separator. NB the upstream `(Tue)`/`(Thu)` day tokens also contain a
    // capital `T`, so a bare `contains('T')` check is wrong here.
    if raw.contains('T') && raw.contains('-') && !raw.contains('/') {
        return raw.to_string();
    }
    let mut parts = raw.split_whitespace();
    let date = parts.next().unwrap_or(raw).replace('/', "-");
    // The `(Day)` token, when present, is the second whitespace field; the time
    // is the last field. Skip anything parenthesized and take the first HH:MM.
    let time = parts
        .find(|token| !token.starts_with('('))
        .filter(|token| token.contains(':'))
        .unwrap_or("00:00");
    format!("{date}T{time}:00Z")
}

fn session_body(session_id: &str, date: &str, turns: &[LmeTurn]) -> String {
    let mut body = format!("[session {session_id}] [date {date}]\n");
    for turn in turns {
        body.push_str(&turn.role);
        body.push_str(": ");
        body.push_str(&turn.content);
        body.push('\n');
    }
    body
}

/// Episode bodies for one haystack session at the requested granularity:
/// "session" yields one body; "turns" yields one body per window of up to
/// `turns_window` consecutive turns, each keeping the `[session <id>]`
/// prefix plus a `[turns a-b]` marker so provenance stays per-session.
pub fn session_bodies(
    granularity: &str,
    turns_window: usize,
    session_id: &str,
    date: &str,
    turns: &[LmeTurn],
) -> Vec<String> {
    if granularity != "turns" {
        return vec![session_body(session_id, date, turns)];
    }
    if turns.is_empty() {
        return vec![session_body(session_id, date, turns)];
    }
    turns
        .chunks(turns_window)
        .enumerate()
        .map(|(window_index, window)| {
            let first = window_index * turns_window + 1;
            let last = window_index * turns_window + window.len();
            let mut body = format!("[session {session_id}] [date {date}] [turns {first}-{last}]\n");
            for turn in window {
                body.push_str(&turn.role);
                body.push_str(": ");
                body.push_str(&turn.content);
                body.push('\n');
            }
            body
        })
        .collect()
}

/// Builds the `--embed-model` arm through the shared
/// [`memphant_runtime::embedder_from_id`] grammar — the SAME single source of
/// truth the runtime `MEMPHANT_EMBEDDINGS` env var resolves through, so a bench
/// arm and a served arm are byte-identical for a given id (no longer
/// fastembed-only, hence the rename from `build_fastembed`). It covers the local
/// fastembed/qwen3 arms (cargo-feature gated: a clear build-instruction error
/// when absent) and the R0-T2 hosted-API arms (always compiled: a clear
/// missing-key error when the provider's env var is unset).
fn build_embedder_arm(embed_model: &str) -> Result<Arc<dyn EmbeddingProvider>, String> {
    memphant_runtime::embedder_from_id(embed_model)
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let bytes = std::fs::read(path).map_err(|error| format!("read {}: {error}", path.display()))?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    Ok(format!("{:x}", hasher.finalize()))
}

pub fn run_bench_lme(options: &BenchLmeOptions) -> Result<BenchLmeReport, String> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| format!("tokio runtime: {error}"))?;
    runtime.block_on(run_bench_lme_async(options))
}

async fn run_bench_lme_async(options: &BenchLmeOptions) -> Result<BenchLmeReport, String> {
    let data_path = Path::new(&options.data_path);
    let dataset_sha256 = sha256_file(data_path)?;
    let raw = std::fs::read_to_string(data_path)
        .map_err(|error| format!("read {}: {error}", data_path.display()))?;
    let questions: Vec<LmeQuestion> =
        serde_json::from_str(&raw).map_err(|error| format!("parse dataset: {error}"))?;
    let dataset_questions = questions.len();

    let id_pairs: Vec<(String, String)> = questions
        .iter()
        .map(|question| (question.question_id.clone(), question.question_type.clone()))
        .collect();
    let sampled_ids = stratified_sample_ids(&id_pairs, options.sample, options.seed);
    let by_id: HashMap<&str, &LmeQuestion> = questions
        .iter()
        .map(|question| (question.question_id.as_str(), question))
        .collect();

    let store = Arc::new(
        PgStore::connect(&options.database_url)
            .await
            .map_err(|error| format!("postgres connect: {error}"))?,
    );
    // W8: derive the embedding arm ONCE and share it — ingest and recall must
    // embed under the same profile (id+dims), else the vector channel compares
    // incompatible spaces.
    let embedder = build_embedder_arm(&options.embed_model)?;
    // Effective runtime contextual-chunk state: default-on (the product path)
    // unless the control arm explicitly disables it. `--disable runtime_chunks`
    // forces the builder off; `--runtime-chunks` is redundant with the default
    // but kept as an explicit opt-in. The report records THIS effective value.
    let runtime_chunks_enabled =
        options.runtime_chunks && options.disable.as_deref() != Some("runtime_chunks");
    let ingest_service = MemoryService::new(
        Arc::clone(&store),
        Arc::new(SystemClock),
        Arc::clone(&embedder),
    )
    .with_contextual_chunks_write_enabled(runtime_chunks_enabled)
    // W5: the ingest path grounds `valid_from` + dates chunk headers at reflect,
    // so the flag must be set here as well as on the recall service.
    .with_temporal_grounding_enabled(options.temporal_grounding)
    // W6: fact extraction is INGEST-time only — the reflect stage mines the facts
    // as ordinary units, so only the ingest service carries the flag.
    .with_fact_extraction_enabled(options.fact_extraction);
    let vector_disabled = options.disable.as_deref() == Some("vector");
    // Vector ablation: same store/units, but the recall-side service embeds
    // with Noop so `query_vec` is None and the vector channel is honestly off.
    let recall_service = if vector_disabled {
        MemoryService::new(
            Arc::clone(&store),
            Arc::new(SystemClock),
            Arc::new(NoopEmbedding),
        )
    } else {
        ingest_service.clone()
    }
    // R1.5-T0 recall-pool-depth knob (`--pool`): widens every internal
    // channel/fusion limit in the recall path (vector KNN fan-out, packing
    // scan window, rerank rescoring cap). Recall-time only; ingestion is
    // unaffected.
    .with_recall_pool_depth(options.pool)
    // W4 packing levers (`--sibling-gather` / `--session-quota`): recall-time
    // only; both default off so the campaign measures each independently.
    .with_sibling_gather_enabled(options.sibling_gather)
    .with_session_quota(options.session_quota)
    .with_pack_render_cap(options.pack_render_cap)
    .with_pack_submodular_ordering_enabled(options.pack_submodular_ordering)
    // W5 temporal grounding: query-date windowing + dated packs at recall. Set
    // explicitly here too so the vector-disabled fresh recall service (which is
    // not a clone of `ingest_service`) also carries the flag.
    .with_temporal_grounding_enabled(options.temporal_grounding)
    // W8 cross-rerank doc granularity (`--rerank-granularity`): recall-time
    // only, inert unless `--cross-rerank` installs a reranker below.
    .with_cross_rerank_granularity(options.rerank_granularity);

    // W8 cross-encoder rerank: recall-time only. When on, install the real
    // fastembed reranker so it reorders the widened pool before packing.
    // R1.5-T1: routed through the SAME `memphant_runtime::build_cross_reranker`
    // factory `build_service`'s `MEMPHANT_CROSS_RERANK` env wiring uses, so
    // this bench arm and a served recall install byte-identical reranker
    // construction (unified — this bench previously had its own private
    // `build_cross_reranker`/`TimedCrossReranker`, and the server had NO
    // wiring at all; per-call latency is now measured once, in
    // `recall_with_pool` itself, landing in `RetrievalTrace::cross_rerank_ms`
    // for both instead of only printing to bench's stderr).
    let recall_service = if options.cross_rerank {
        recall_service.with_cross_reranker(memphant_runtime::build_cross_reranker()?)
    } else {
        recall_service
    };

    if options.granularity != "session" && options.granularity != "turns" {
        return Err(format!(
            "unknown --granularity: {} (known: session, turns)",
            options.granularity
        ));
    }

    let disable = options.disable.as_deref();
    if let Some(stage) = disable {
        let known = [
            "vector",
            "edge_expansion",
            "procedure_recall",
            "decay",
            "packing",
            "runtime_chunks",
        ];
        if !known.contains(&stage) {
            return Err(format!(
                "unknown --disable stage: {stage} (known: {known:?})"
            ));
        }
    }

    // Unique per-run slug nonce: every run ingests into FRESH tenants (fresh
    // tenant per question), so repeated runs never collide or share state.
    let run_nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);

    let mut per_question = Vec::new();
    let mut qa_rows: Vec<QaEvidenceRow> = Vec::new();
    let mut classification_rows: Vec<TraceClassificationRow> = Vec::new();
    for (index, question_id) in sampled_ids.iter().enumerate() {
        let question = by_id
            .get(question_id.as_str())
            .ok_or_else(|| format!("sampled id missing from dataset: {question_id}"))?;
        eprintln!(
            "bench-lme [{}/{}] {} ({}) sessions={}",
            index + 1,
            sampled_ids.len(),
            question.question_id,
            question.question_type,
            question.haystack_sessions.len()
        );

        let tenant_uuid = store
            .create_tenant(&format!(
                "lme-{run_nonce}-{}-{}",
                options.seed, question.question_id
            ))
            .await
            .map_err(|error| format!("create_tenant: {error}"))?;
        let tenant = TenantId::from_u128(tenant_uuid.as_u128());
        let binding = store
            .resolve_context_binding(
                tenant,
                format!("lme:{}", question.question_id),
                ContextBindingRequest {
                    subject: ContextBindingEntityRef {
                        external_ref: format!("subject:{}", question.question_id),
                        kind: "user".to_string(),
                    },
                    actor: ContextBindingEntityRef {
                        external_ref: "actor:lme".to_string(),
                        kind: "system".to_string(),
                    },
                    scope: ContextBindingScopeRef {
                        external_ref: "scope:lme".to_string(),
                        kind: "user_root".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: ContextBindingAgentRef {
                        external_ref: "agent:lme".to_string(),
                        parent_external_ref: None,
                    },
                    access_policies: vec![],
                },
            )
            .await
            .map_err(|error| format!("bind context: {error}"))?;
        let context = store
            .resolve_memory_context(
                tenant,
                binding.subject_id,
                binding.actor_id,
                binding.scope_id,
                binding.agent_node_id,
            )
            .await
            .map_err(|error| format!("resolve context: {error}"))?;
        let scope = context.scope_id;

        // Chronological ingestion: one episode per haystack session.
        let mut order: Vec<usize> = (0..question.haystack_sessions.len()).collect();
        order.sort_by(|left, right| {
            question.haystack_dates[*left]
                .cmp(&question.haystack_dates[*right])
                .then(left.cmp(right))
        });
        let mut episode_sessions = HashMap::new();
        for session_index in order {
            let session_id = &question.haystack_session_ids[session_index];
            let bodies = session_bodies(
                &options.granularity,
                options.turns_window,
                session_id,
                &question.haystack_dates[session_index],
                &question.haystack_sessions[session_index],
            );
            for (turn_index, body) in bodies.into_iter().enumerate() {
                let response = ingest_service
                    .retain(
                        &context,
                        // `session_index` (haystack position), not just `session_id`:
                        // LongMemEval haystacks can repeat a session_id at two
                        // timestamps (e.g. dev `001be529` has `sharegpt_SYbLHTK_0`
                        // twice — identical body, different date). Each occurrence
                        // must ingest as its own episode, so the idempotency key
                        // is keyed on the haystack position, which is unique.
                        &format!("lme:{session_index}:{session_id}:{turn_index}"),
                        TrustLevel::TrustedUser,
                        RetainEpisodeHttpRequest {
                            subject_id: context.data_subject_id,
                            scope_id: scope,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            source_ref: format!("longmemeval:session:{session_id}"),
                            observed_at: normalize_haystack_date(
                                &question.haystack_dates[session_index],
                            ),
                            payload: RetainPayload::Episode(RetainEpisodePayload {
                                source_kind: "user".to_string(),
                                body,
                            }),
                        },
                    )
                    .await
                    .map_err(|error| format!("retain {session_id}: {error}"))?;
                let retained: RetainEpisodeHttpResponse =
                    serde_json::from_slice(response.body())
                        .map_err(|error| format!("retain {session_id}: {error}"))?;
                if let Some(episode_id) = retained.episode_id {
                    episode_sessions
                        .entry(episode_id)
                        .or_insert_with(|| session_id.clone());
                }
            }
        }

        // Each question has a fresh tenant above; drain only this scratch
        // workload through the production worker surface.
        while ingest_service
            .run_worker_tick(usize::MAX)
            .await
            .map_err(|error| format!("reflect: {error}"))?
            > 0
        {}

        let response = recall_service
            .recall_internal(RecallRequest {
                context: context.clone(),
                query: question.question.clone(),
                k: options.k,
                budget_tokens: options.budget_tokens,
                mode: options.mode,
                include_beliefs: false,
                edge_expansion_enabled: disable != Some("edge_expansion"),
                context_packing_abstention_enabled: disable != Some("packing"),
                procedure_recall_enabled: disable != Some("procedure_recall"),
                decay_enabled: disable != Some("decay"),
                engine_version: "bench-lme".to_string(),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            })
            .await
            .map_err(|error| format!("recall: {error}"))?;

        let item_sessions: Vec<Option<String>> = response
            .items
            .iter()
            .map(|item| {
                item.citation_episode_id
                    .and_then(|episode| episode_sessions.get(&episode).cloned())
            })
            .collect();
        let is_abstention = question.question_id.contains("_abs");
        if options.emit_qa.is_some() {
            qa_rows.push(QaEvidenceRow {
                question_id: question.question_id.clone(),
                question_type: question.question_type.clone(),
                is_abstention,
                question: question.question.clone(),
                question_date: question.question_date.clone(),
                gold_answer: question.answer.clone(),
                abstained: response.abstention,
                granularity: options.granularity.clone(),
                k: options.k,
                evidence: response
                    .items
                    .iter()
                    .zip(item_sessions.iter())
                    .enumerate()
                    .map(|(rank_index, (item, session))| QaEvidenceItem {
                        rank: rank_index + 1,
                        session_id: session.clone(),
                        body: item.body.clone(),
                    })
                    .collect(),
            });
        }
        if options.emit_trace_classification.is_some() {
            // A1: classify from the FULL candidate pool (the trace), not the
            // packed top-k. `candidate_whitelist` is the packed set; the pool
            // lives in `trace.candidates`. A gold unit that is in the pool but
            // unpacked (bucket B) is typically uncited, so map every pool unit
            // to its episode directly from the store (`source_episode_id`),
            // never from the trace citations (which cover only packed units).
            let trace = store
                .trace_by_id(&context, response.trace_id)
                .await
                .map_err(|error| format!("trace fetch: {error}"))?
                .ok_or_else(|| format!("trace missing for {}", question.question_id))?;
            let mut seen = std::collections::HashSet::new();
            let pool_unit_ids: Vec<_> = trace
                .candidates
                .iter()
                .map(|c| c.unit_id)
                .filter(|id| seen.insert(*id))
                .collect();
            let pool_units = store
                .fetch_units_by_ids(&context, &pool_unit_ids)
                .await
                .map_err(|error| format!("fetch pool units: {error}"))?;
            let pool_sessions: Vec<Option<String>> = pool_units
                .iter()
                .map(|unit| {
                    unit.source_episode_id
                        .and_then(|episode| episode_sessions.get(&episode).cloned())
                })
                .collect();
            let gold = |session: &Option<String>| {
                session
                    .as_ref()
                    .is_some_and(|s| question.answer_session_ids.iter().any(|answer| answer == s))
            };
            let bucket = classify_question(
                &pool_sessions,
                &item_sessions,
                &question.answer_session_ids,
                is_abstention,
            );
            // Rung-7 drop-cause: the gold-bearing pool unit ids (pool_units and
            // pool_sessions are index-aligned), their fused ranks/scores from
            // the candidate trace, and the pack drop reasons.
            let gold_unit_ids: Vec<UnitId> = pool_units
                .iter()
                .zip(pool_sessions.iter())
                .filter(|(_, session)| gold(session))
                .map(|(unit, _)| unit.id)
                .collect();
            let candidate_ranks: Vec<(UnitId, Option<usize>)> = trace
                .candidates
                .iter()
                .map(|c| (c.unit_id, c.fused_rank))
                .collect();
            let drops: Vec<(UnitId, RecallDropReason)> = trace
                .dropped_items
                .iter()
                .map(|d| (d.unit_id, d.reason))
                .collect();
            let (gold_fused_rank, gold_drop_reason) =
                classify_gold_drop_cause(&gold_unit_ids, &candidate_ranks, &drops);
            let gold_fused_score = gold_fused_rank.and_then(|rank| {
                // The best-ranked gold unit's fused score (same unit the rank
                // came from). `fused_rank` is 1-based; find the candidate whose
                // fused_rank matches and read its score.
                trace
                    .candidates
                    .iter()
                    .find(|c| c.fused_rank == Some(rank) && gold_unit_ids.contains(&c.unit_id))
                    .and_then(|c| c.fused_score)
            });
            classification_rows.push(TraceClassificationRow {
                question_id: question.question_id.clone(),
                question_type: question.question_type.clone(),
                bucket,
                gold_in_pool: pool_sessions.iter().any(gold),
                gold_in_topk: item_sessions.iter().any(gold),
                first_answer_rank: item_sessions.iter().position(gold).map(|i| i + 1),
                pool_size: pool_unit_ids.len(),
                packed_size: response.items.len(),
                gold_fused_rank,
                gold_fused_score,
                gold_drop_reason,
                k: options.k,
            });
        }
        let (hit_at_5, hit_at_10, abstention_correct, first_answer_rank) = score_question(
            &item_sessions,
            &question.answer_session_ids,
            is_abstention,
            response.abstention,
        );
        per_question.push(QuestionResult {
            question_id: question.question_id.clone(),
            question_type: question.question_type.clone(),
            is_abstention,
            hit_at_5,
            hit_at_10,
            abstention_correct,
            first_answer_rank,
            returned_items: response.items.len(),
            degraded: response.degraded,
            ingested_sessions: question.haystack_sessions.len(),
        });
    }

    let mut stratum_names: Vec<String> = per_question
        .iter()
        .map(|row| row.question_type.clone())
        .collect();
    stratum_names.sort();
    stratum_names.dedup();
    let strata = stratum_names
        .iter()
        .map(|name| {
            let rows: Vec<&QuestionResult> = per_question
                .iter()
                .filter(|row| &row.question_type == name)
                .collect();
            aggregate(name, &rows)
        })
        .collect();
    let overall = aggregate("overall", &per_question.iter().collect::<Vec<_>>());

    if let Some(path) = &options.emit_qa {
        let mut lines = String::new();
        for row in &qa_rows {
            lines.push_str(
                &serde_json::to_string(row)
                    .map_err(|error| format!("serialize qa row: {error}"))?,
            );
            lines.push('\n');
        }
        std::fs::write(path, lines).map_err(|error| format!("write qa jsonl {path}: {error}"))?;
        eprintln!("bench-lme qa evidence rows={} out={path}", qa_rows.len());
    }

    if let Some(path) = &options.emit_trace_classification {
        let mut lines = String::new();
        for row in &classification_rows {
            lines.push_str(
                &serde_json::to_string(row)
                    .map_err(|error| format!("serialize classification row: {error}"))?,
            );
            lines.push('\n');
        }
        std::fs::write(path, lines)
            .map_err(|error| format!("write classification jsonl {path}: {error}"))?;
        // A1 verdict summary to stderr (the JSONL is the durable artifact).
        let count = |bucket: FastMissBucket| {
            classification_rows
                .iter()
                .filter(|row| row.bucket == bucket)
                .count()
        };
        let (absent, unpacked, in_topk, abstention) = (
            count(FastMissBucket::AbsentFromPool),
            count(FastMissBucket::InPoolUnpacked),
            count(FastMissBucket::InTopK),
            count(FastMissBucket::Abstention),
        );
        let scored = absent + unpacked + in_topk;
        let not_absent = unpacked + in_topk;
        let share = |n: usize| {
            if scored == 0 {
                0.0
            } else {
                n as f64 / scored as f64
            }
        };
        eprintln!(
            "bench-lme trace-classification rows={} out={path}",
            classification_rows.len()
        );
        eprintln!(
            "A1 buckets (scored={scored}, abstention={abstention}): absent_from_pool={absent} ({:.3}) in_pool_unpacked={unpacked} ({:.3}) in_top_k={in_topk} ({:.3})",
            share(absent),
            share(unpacked),
            share(in_topk),
        );
        eprintln!(
            "A1 present-but-unpacked-or-unread (B+C) = {not_absent}/{scored} = {:.3} — verdict>=0.70 defers Deep: {}",
            share(not_absent),
            share(not_absent) >= 0.70,
        );
    }

    let paired_vs_baseline = match &options.baseline {
        Some(path) => {
            let raw = std::fs::read_to_string(path)
                .map_err(|error| format!("read baseline {path}: {error}"))?;
            let baseline: BenchLmeReport =
                serde_json::from_str(&raw).map_err(|error| format!("parse baseline: {error}"))?;
            if baseline.dataset_sha256 != dataset_sha256 {
                return Err("baseline dataset sha256 differs from current dataset".to_string());
            }
            if baseline.sample_seed != options.seed || baseline.sample_n != options.sample {
                return Err(
                    "baseline sample seed/size differs — deltas would be unpaired".to_string(),
                );
            }
            Some(paired_comparison(
                &per_question,
                &baseline,
                path,
                options.seed,
            ))
        }
        None => None,
    };

    Ok(BenchLmeReport {
        benchmark: "longmemeval_retrieval_only".to_string(),
        dataset_path: options.data_path.clone(),
        dataset_sha256,
        dataset_questions,
        sample_seed: options.seed,
        sample_n: sampled_ids.len(),
        k: options.k,
        runtime: "postgres".to_string(),
        retrieval_only: true,
        embeddings: if vector_disabled {
            format!(
                "{} for ingestion; query vector disabled (query_vec=None)",
                embedder.id()
            )
        } else {
            embedder.id().to_string()
        },
        ingestion: if options.granularity == "turns" {
            format!(
                "episodes of up to {} consecutive turns per haystack session, chronological by haystack_dates; turns concatenated as `role: content`; body prefixed with [session <id>] [date <date>] [turns a-b]",
                options.turns_window
            )
        } else {
            "one episode per haystack session, chronological by haystack_dates; turns concatenated as `role: content`; body prefixed with [session <id>] [date <date>]".to_string()
        },
        reflect: "MemoryService::reflect (worker claim/complete path), synchronous after ingestion"
            .to_string(),
        granularity: options.granularity.clone(),
        turns_window: options.turns_window,
        budget_tokens: options.budget_tokens,
        recall_pool_depth: options.pool,
        sibling_gather: options.sibling_gather,
        session_quota: options.session_quota,
        pack_render_cap: options.pack_render_cap,
        pack_submodular_ordering: options.pack_submodular_ordering,
        temporal_grounding: options.temporal_grounding,
        fact_extraction: options.fact_extraction,
        runtime_chunks: runtime_chunks_enabled,
        embed_model: options.embed_model.clone(),
        // The active embedder's dims — for the vector-disabled arm the recall
        // side is Noop, but ingestion still embedded under `embedder`, so its
        // dimensionality is the honest provenance.
        embedding_dimensions: embedder.dimensions(),
        cross_rerank: options.cross_rerank,
        mode: match options.mode {
            RecallMode::Fast => "fast",
            RecallMode::Deep => "deep",
        }
        .to_string(),
        disabled: options.disable.clone(),
        command: options.command.clone(),
        generated_at_unix: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_secs())
            .unwrap_or(0),
        overall,
        strata,
        per_question,
        paired_vs_baseline,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use memphant_core::DEFAULT_RECALL_POOL_DEPTH;

    /// R0-T1b feature-gated error path: `--embed-model qwen3` in a binary
    /// built with `--features fastembed` but WITHOUT `--features qwen3` (the
    /// shipped memphant-server/-worker default: `default = ["fastembed"]`,
    /// `qwen3` is additive-only) must name the missing `qwen3` feature —
    /// never silently fall back to another arm. This only compiles/runs
    /// under exactly that feature combination (`fastembed` on, `qwen3`
    /// off) — the default `cargo test -p memphant-eval --features fastembed`
    /// invocation, not `--all-features` (which turns qwen3 on too).
    #[cfg(all(feature = "fastembed", not(feature = "qwen3")))]
    #[test]
    fn embed_model_qwen3_without_qwen3_feature_names_the_missing_feature() {
        // `Arc<dyn EmbeddingProvider>` isn't `Debug`, so `expect_err` (which
        // needs `T: Debug` to format the Ok case) doesn't apply here.
        let error = match build_embedder_arm("qwen3") {
            Err(error) => error,
            Ok(_) => panic!("qwen3 feature is not compiled in — expected an error"),
        };
        assert!(
            error.contains("qwen3"),
            "error must name the missing feature: {error}"
        );
        assert!(
            error.contains("--features qwen3"),
            "error must tell the caller how to fix it: {error}"
        );
    }

    /// The other three T1 arms (and qwen3's own parse-through) keep working
    /// via the unknown-model error message's known-arms list, regardless of
    /// which of `fastembed`/`qwen3` are compiled in.
    #[cfg(feature = "fastembed")]
    #[test]
    fn embed_model_unknown_selector_lists_qwen3_as_known() {
        let error = match build_embedder_arm("nope") {
            Err(error) => error,
            Ok(_) => panic!("nope is not a known arm — expected an error"),
        };
        assert!(
            error.contains("qwen3"),
            "unknown-arm error must list qwen3 among known selectors: {error}"
        );
    }

    fn fixture_rows() -> Vec<QuestionResult> {
        // Synthetic 3-question fixture: one hit@5, one hit@10-only, one abstention.
        let (h5a, h10a, absa, ranka) = score_question(
            &[
                Some("s_other".to_string()),
                Some("s_answer".to_string()),
                None,
            ],
            &["s_answer".to_string()],
            false,
            false,
        );
        let (h5b, h10b, absb, rankb) = score_question(
            &[
                None,
                Some("s1".to_string()),
                Some("s2".to_string()),
                Some("s3".to_string()),
                Some("s4".to_string()),
                Some("s5".to_string()),
                Some("s_answer".to_string()),
            ],
            &["s_answer".to_string()],
            false,
            false,
        );
        let (h5c, h10c, absc, rankc) = score_question(
            &[Some("s_noise".to_string())],
            &["answer_missing_abs".to_string()],
            true,
            false,
        );
        vec![
            QuestionResult {
                question_id: "q1".to_string(),
                question_type: "multi-session".to_string(),
                is_abstention: false,
                hit_at_5: h5a,
                hit_at_10: h10a,
                abstention_correct: absa,
                first_answer_rank: ranka,
                returned_items: 3,
                degraded: false,
                ingested_sessions: 3,
            },
            QuestionResult {
                question_id: "q2".to_string(),
                question_type: "multi-session".to_string(),
                is_abstention: false,
                hit_at_5: h5b,
                hit_at_10: h10b,
                abstention_correct: absb,
                first_answer_rank: rankb,
                returned_items: 7,
                degraded: false,
                ingested_sessions: 3,
            },
            QuestionResult {
                question_id: "q3_abs".to_string(),
                question_type: "knowledge-update".to_string(),
                is_abstention: true,
                hit_at_5: h5c,
                hit_at_10: h10c,
                abstention_correct: absc,
                first_answer_rank: rankc,
                returned_items: 1,
                degraded: false,
                ingested_sessions: 2,
            },
        ]
    }

    #[test]
    fn normalize_haystack_date_produces_rfc3339() {
        // Upstream LongMemEval dates are `YYYY/MM/DD (Day) HH:MM` — NOT RFC3339,
        // so `observed_at` must be normalized or `retain` rejects the row
        // ("observed_at must be RFC3339"). Preserve the real HH:MM (chronological
        // ordering within a day depends on it), just reshape to RFC3339.
        assert_eq!(
            normalize_haystack_date("2023/05/20 (Sat) 07:47"),
            "2023-05-20T07:47:00Z"
        );
        assert_eq!(
            normalize_haystack_date("2023/06/13 (Tue) 19:33"),
            "2023-06-13T19:33:00Z"
        );
        // A bare date with no time still normalizes (midnight).
        assert_eq!(
            normalize_haystack_date("2023/05/20"),
            "2023-05-20T00:00:00Z"
        );
        // An already-RFC3339 value passes through unchanged.
        assert_eq!(
            normalize_haystack_date("2023-05-20T07:47:00Z"),
            "2023-05-20T07:47:00Z"
        );
    }

    #[test]
    fn classify_bucket_covers_absent_unpacked_topk_and_abstention() {
        // A1 (tri-domain-sota-plan §3.A1): the three-bucket Fast-miss classifier.
        // Bucket A absent-from-pool: no pool unit maps to a gold session.
        assert_eq!(
            classify_question(
                &[Some("s_noise".to_string()), None],
                &[Some("s_noise".to_string())],
                &["s_gold".to_string()],
                false,
            ),
            FastMissBucket::AbsentFromPool
        );
        // Bucket B in-pool-but-unpacked: a gold unit is in the pool but the
        // packed top-k does not surface it (packing/ordering dropped it).
        assert_eq!(
            classify_question(
                &[Some("s_noise".to_string()), Some("s_gold".to_string())],
                &[Some("s_noise".to_string())],
                &["s_gold".to_string()],
                false,
            ),
            FastMissBucket::InPoolUnpacked
        );
        // Bucket C in-top-k: retrieval succeeded — a gold item is packed.
        assert_eq!(
            classify_question(
                &[Some("s_gold".to_string())],
                &[Some("s_gold".to_string())],
                &["s_gold".to_string()],
                false,
            ),
            FastMissBucket::InTopK
        );
        // Abstention questions are classified separately, never in the miss buckets.
        assert_eq!(
            classify_question(&[], &[], &["s_gold".to_string()], true),
            FastMissBucket::Abstention
        );
        // Empty-pool edge: absent-from-pool, not a panic.
        assert_eq!(
            classify_question(&[], &[], &["s_gold".to_string()], false),
            FastMissBucket::AbsentFromPool
        );
    }

    #[test]
    fn classify_gold_drop_cause_picks_best_ranked_gold_and_its_drop() {
        // Rung-7 diagnosis: for an in-pool-unpacked question, WHY did the
        // gold-bearing pool unit not reach the packed top-k? The pure helper
        // takes the gold unit ids, the pool candidates' (unit, fused_rank), and
        // the pack `dropped_items` (unit, reason), and returns the best-ranked
        // (min fused_rank) gold unit's fused_rank + its drop reason.
        let g_hi = UnitId::from_u128(1); // fused_rank 12, dropped Duplicate
        let g_lo = UnitId::from_u128(2); // fused_rank 30, never reached pack loop
        let decoy = UnitId::from_u128(9);
        let candidates = vec![
            (g_lo, Some(30usize)),
            (decoy, Some(3usize)),
            (g_hi, Some(12usize)),
        ];
        let drops = vec![(g_hi, RecallDropReason::Duplicate)];
        // Best-ranked gold is g_hi (fused_rank 12 < 30); it was dropped Duplicate.
        assert_eq!(
            classify_gold_drop_cause(&[g_hi, g_lo], &candidates, &drops),
            (Some(12), Some(RecallDropReason::Duplicate))
        );
        // A gold unit present in the pool but absent from dropped_items never
        // reached (or survived) the pack loop drop path: rank known, reason None.
        assert_eq!(
            classify_gold_drop_cause(&[g_lo], &candidates, &drops),
            (Some(30), None)
        );
        // Gold not in the candidate trace at all: no rank, no reason.
        assert_eq!(
            classify_gold_drop_cause(&[UnitId::from_u128(42)], &candidates, &drops),
            (None, None)
        );
    }

    #[test]
    fn scorer_ranks_hits_and_abstentions() {
        let rows = fixture_rows();
        assert_eq!(rows[0].hit_at_5, Some(true));
        assert_eq!(rows[0].hit_at_10, Some(true));
        assert_eq!(rows[0].first_answer_rank, Some(2));
        assert_eq!(rows[1].hit_at_5, Some(false));
        assert_eq!(rows[1].hit_at_10, Some(true));
        assert_eq!(rows[1].first_answer_rank, Some(7));
        assert_eq!(rows[2].hit_at_5, None);
        assert_eq!(rows[2].abstention_correct, Some(true));
    }

    #[test]
    fn abstention_fails_when_answer_session_returned_without_abstaining() {
        let (_, _, correct, rank) = score_question(
            &[Some("s_answer".to_string())],
            &["s_answer".to_string()],
            true,
            false,
        );
        assert_eq!(correct, Some(false));
        assert_eq!(rank, Some(1));
        // But abstaining is always correct for an abstention question.
        let (_, _, correct, _) = score_question(
            &[Some("s_answer".to_string())],
            &["s_answer".to_string()],
            true,
            true,
        );
        assert_eq!(correct, Some(true));
    }

    #[test]
    fn aggregate_splits_abstentions_from_scored() {
        let rows = fixture_rows();
        let refs: Vec<&QuestionResult> = rows.iter().collect();
        let overall = aggregate("overall", &refs);
        assert_eq!(overall.n, 3);
        assert_eq!(overall.n_scored, 2);
        assert_eq!(overall.recall_at_5, Some(0.5));
        assert_eq!(overall.recall_at_10, Some(1.0));
        assert_eq!(overall.abstention_n, 1);
        assert_eq!(overall.abstention_correct, 1);
    }

    #[test]
    fn bootstrap_ci_is_deterministic_and_brackets_mean() {
        let deltas = vec![1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0];
        let first = bootstrap_ci(&deltas, 1000, 20260710);
        let second = bootstrap_ci(&deltas, 1000, 20260710);
        assert_eq!(first.ci95_low, second.ci95_low);
        assert_eq!(first.ci95_high, second.ci95_high);
        assert!(first.ci95_low <= first.mean && first.mean <= first.ci95_high);
        assert!(first.ci_excludes_zero);
        let null = bootstrap_ci(&[0.0, 0.0, 1.0, -1.0], 1000, 7);
        assert!(!null.ci_excludes_zero);
    }

    #[test]
    fn lane_default_granularity_is_session_and_old_reports_parse_as_session() {
        // Back to "session" as of the 2026-07-10 rung 4 promotion: the lane
        // measures the product path (session ingestion + service-side runtime
        // chunks). The earlier same-day "turns" promotion is superseded by the
        // runtime embodiment (ΔQA +0.000 ns tie, no client-side windowing).
        assert_eq!(DEFAULT_GRANULARITY, "session");
        // The serde parsing default is ALSO "session" here, but for an
        // independent reason: reports written before the granularity field
        // existed were session runs. It must never merely track the lane
        // default.
        assert_eq!(default_granularity(), "session");
    }

    #[test]
    fn session_bodies_windows_turns_and_keeps_session_prefix() {
        let turns: Vec<LmeTurn> = (0..9)
            .map(|index| LmeTurn {
                role: if index % 2 == 0 { "user" } else { "assistant" }.to_string(),
                content: format!("turn {index}"),
            })
            .collect();
        let session = session_bodies("session", DEFAULT_TURNS_WINDOW, "s1", "2023/05/30", &turns);
        assert_eq!(session.len(), 1);
        assert!(session[0].starts_with("[session s1] [date 2023/05/30]\n"));

        let windows = session_bodies("turns", DEFAULT_TURNS_WINDOW, "s1", "2023/05/30", &turns);
        // 9 turns at window 4 -> 4 + 4 + 1.
        assert_eq!(windows.len(), 3);
        assert!(windows[0].starts_with("[session s1] [date 2023/05/30] [turns 1-4]\n"));
        assert!(windows[1].starts_with("[session s1] [date 2023/05/30] [turns 5-8]\n"));
        assert!(windows[2].starts_with("[session s1] [date 2023/05/30] [turns 9-9]\n"));
        assert!(windows[2].contains("user: turn 8"));
        // Every turn appears exactly once across windows.
        let joined = windows.join("");
        for index in 0..9 {
            assert_eq!(joined.matches(&format!("turn {index}\n")).count(), 1);
        }
        // Empty sessions still produce one (header-only) episode body.
        assert_eq!(
            session_bodies("turns", DEFAULT_TURNS_WINDOW, "s2", "2023/06/01", &[]).len(),
            1
        );
    }

    #[test]
    fn session_bodies_windows_turns_with_custom_window_size() {
        // Mirrors `session_bodies_windows_turns_and_keeps_session_prefix` but
        // pins a non-default `turns_window` (2) to prove the window size is a
        // real parameter, not a re-read of `DEFAULT_TURNS_WINDOW`.
        let turns: Vec<LmeTurn> = (0..9)
            .map(|index| LmeTurn {
                role: if index % 2 == 0 { "user" } else { "assistant" }.to_string(),
                content: format!("turn {index}"),
            })
            .collect();
        let windows = session_bodies("turns", 2, "s1", "2023/05/30", &turns);
        // 9 turns at window 2 -> 2 + 2 + 2 + 2 + 1.
        assert_eq!(windows.len(), 5);
        assert!(windows[0].starts_with("[session s1] [date 2023/05/30] [turns 1-2]\n"));
        assert!(windows[1].starts_with("[session s1] [date 2023/05/30] [turns 3-4]\n"));
        assert!(windows[2].starts_with("[session s1] [date 2023/05/30] [turns 5-6]\n"));
        assert!(windows[3].starts_with("[session s1] [date 2023/05/30] [turns 7-8]\n"));
        assert!(windows[4].starts_with("[session s1] [date 2023/05/30] [turns 9-9]\n"));
        assert!(windows[4].contains("user: turn 8"));
        // Every turn appears exactly once across windows.
        let joined = windows.join("");
        for index in 0..9 {
            assert_eq!(joined.matches(&format!("turn {index}\n")).count(), 1);
        }
    }

    #[test]
    fn turn_window_and_budget_tokens_defaults_are_pinned() {
        assert_eq!(DEFAULT_TURNS_WINDOW, 4);
        assert_eq!(DEFAULT_BUDGET_TOKENS, 8192);
        // R1.5-T0: the recall-pool-depth default is the ONE knob every
        // internal channel/fusion limit in the recall path derives from —
        // pre-registered at 64, up from the historical vector-only 32
        // (`VECTOR_CANDIDATE_LIMIT`, unchanged, still used directly by tests
        // that call `fetch_vector_candidates` without going through the
        // service).
        assert_eq!(DEFAULT_RECALL_POOL_DEPTH, 64);
        assert_eq!(memphant_core::VECTOR_CANDIDATE_LIMIT, 32);
    }

    #[test]
    fn pre_flag_report_json_parses_turns_window_and_budget_tokens_as_defaults() {
        // A report written before `turns_window`/`budget_tokens` existed
        // (also missing `granularity`, for the same reason) must still
        // parse, with both new fields defaulting to the values every such
        // report actually used: window 4, budget 8192.
        let json = r#"{
            "benchmark": "longmemeval_retrieval_only",
            "dataset_path": "data.json",
            "dataset_sha256": "abc123",
            "dataset_questions": 10,
            "sample_seed": 20260710,
            "sample_n": 1,
            "k": 10,
            "runtime": "postgres",
            "retrieval_only": true,
            "embeddings": "noop",
            "ingestion": "one episode per haystack session",
            "reflect": "MemoryService::reflect",
            "mode": "fast",
            "disabled": null,
            "command": "bench-lme --sample 1 --seed 20260710",
            "generated_at_unix": 0,
            "overall": {
                "question_type": "overall",
                "n": 0,
                "n_scored": 0,
                "recall_at_5": null,
                "recall_at_10": null,
                "abstention_n": 0,
                "abstention_correct": 0
            },
            "strata": [],
            "per_question": [],
            "paired_vs_baseline": null
        }"#;
        let report: BenchLmeReport = serde_json::from_str(json).expect("pre-flag report parses");
        assert_eq!(report.granularity, "session");
        assert_eq!(report.turns_window, 4);
        assert_eq!(report.budget_tokens, 8192);
        assert_eq!(report.turns_window, DEFAULT_TURNS_WINDOW);
        assert_eq!(report.budget_tokens, DEFAULT_BUDGET_TOKENS);
        // R1.5-T0 (superseding the W3 note this replaces): a report written
        // before EITHER `candidate_pool_size` or `recall_pool_depth` existed
        // must parse with the field defaulting to the vector-only KNN
        // fan-out (32) every such report actually ran — NOT today's 64
        // default, which postdates every such report.
        assert_eq!(report.recall_pool_depth, 32);
        assert_eq!(
            report.recall_pool_depth,
            memphant_core::VECTOR_CANDIDATE_LIMIT
        );
        assert_ne!(report.recall_pool_depth, DEFAULT_RECALL_POOL_DEPTH);
        // The runtime_chunks report field serde default STAYS false even after
        // the write path was promoted to default-on: every pre-promotion report
        // was a chunks-off run, so an absent field must parse chunks-OFF and
        // never follow the default-on lane behavior.
        assert!(
            !report.runtime_chunks,
            "absent runtime_chunks must parse false (pre-promotion runs were chunks-off)"
        );
        // W4: a report written before the packing levers existed must parse with
        // both off — sibling_gather false, session_quota absent (None) — since
        // every such report ran today's unrestricted packing.
        assert!(
            !report.sibling_gather,
            "absent sibling_gather must parse false (pre-lever runs were sibling-gather-off)"
        );
        assert_eq!(
            report.session_quota, None,
            "absent session_quota must parse None (pre-lever runs had no quota)"
        );
        // W5: a report written before the temporal-grounding flag existed must
        // parse with it off — every such report ran without content-date
        // grounding, windowing, or dated packs.
        assert!(
            !report.temporal_grounding,
            "absent temporal_grounding must parse false (pre-flag runs were grounding-off)"
        );
        // W6: a report written before the fact-extraction flag existed must parse
        // with it off — every such report ran without mined preference/attribute
        // facts.
        assert!(
            !report.fact_extraction,
            "absent fact_extraction must parse false (pre-flag runs mined no facts)"
        );
        // W8: a report written before the embedding-arm / cross-rerank fields
        // existed must parse as a bge-small (384d), rerank-off run — the arm
        // every such report actually ran.
        assert_eq!(
            report.embed_model, "small",
            "absent embed_model must parse as the historical bge-small arm"
        );
        assert_eq!(
            report.embedding_dimensions, 384,
            "absent embedding_dimensions must parse as bge-small's 384"
        );
        assert!(
            !report.cross_rerank,
            "absent cross_rerank must parse false (pre-flag runs were rerank-off)"
        );
    }

    /// R1.5-T0: a report written under the OLD field name (`candidate_pool_size`,
    /// present since W3, before this task's rename to `recall_pool_depth`) must
    /// still parse — the `#[serde(alias)]` keeps those reports readable without
    /// falling back to the (wrong, pre-W3) legacy default.
    #[test]
    fn old_candidate_pool_size_field_name_still_parses_via_alias() {
        let json = r#"{
            "benchmark": "longmemeval_retrieval_only",
            "dataset_path": "data.json",
            "dataset_sha256": "abc123",
            "dataset_questions": 10,
            "sample_seed": 20260710,
            "sample_n": 1,
            "k": 10,
            "runtime": "postgres",
            "retrieval_only": true,
            "embeddings": "noop",
            "ingestion": "one episode per haystack session",
            "reflect": "MemoryService::reflect",
            "mode": "fast",
            "disabled": null,
            "command": "bench-lme --sample 1 --seed 20260710 --pool 96",
            "generated_at_unix": 0,
            "candidate_pool_size": 96,
            "overall": {
                "question_type": "overall",
                "n": 0,
                "n_scored": 0,
                "recall_at_5": null,
                "recall_at_10": null,
                "abstention_n": 0,
                "abstention_correct": 0
            },
            "strata": [],
            "per_question": [],
            "paired_vs_baseline": null
        }"#;
        let report: BenchLmeReport =
            serde_json::from_str(json).expect("old-field-name report parses");
        assert_eq!(
            report.recall_pool_depth, 96,
            "the serde alias maps the old `candidate_pool_size` field onto `recall_pool_depth`"
        );
    }

    #[test]
    fn stratified_sample_is_deterministic_and_proportional() {
        let mut questions = Vec::new();
        for index in 0..60 {
            questions.push((format!("a{index:03}"), "multi-session".to_string()));
        }
        for index in 0..30 {
            questions.push((format!("b{index:03}"), "knowledge-update".to_string()));
        }
        for index in 0..10 {
            questions.push((
                format!("c{index:03}"),
                "single-session-preference".to_string(),
            ));
        }
        let first = stratified_sample_ids(&questions, 10, 20260710);
        let second = stratified_sample_ids(&questions, 10, 20260710);
        assert_eq!(first, second);
        assert_eq!(first.len(), 10);
        let count = |prefix: &str| first.iter().filter(|id| id.starts_with(prefix)).count();
        assert_eq!(count("a"), 6);
        assert_eq!(count("b"), 3);
        assert_eq!(count("c"), 1);
        let other_seed = stratified_sample_ids(&questions, 10, 1);
        assert_ne!(first, other_seed);
    }
}
