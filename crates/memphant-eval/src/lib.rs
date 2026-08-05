use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::time::Instant;

use memphant_core::deep_recall::{
    DeepRecallProvider, DeepRecallProviderError, DeepRecallProviderRequest,
    DeepRecallProviderResult,
};
use memphant_core::{
    CrossRerankCandidateSelection, DEFAULT_RECALL_POOL_DEPTH, FixedClock, InMemoryStore,
    LexicalScorer, MemoryStore, PackLevers, forget_memory, recall,
    recall_with_pool_and_selection_and_deep, record_mark,
};

/// Deterministic clock for eval fixtures (pinned to the WS-A methodology date).
const EVAL_CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");

/// Deterministic bounded evidence gatherer for the public evaluator contract.
/// It sees only core's authorized workspace, ranks raw sources by query-token
/// overlap with a stable length/path tie-break, and returns UUIDs only. It is
/// deliberately local and cost-free; Task 5 supplies the real runtime agent.
struct EvalDeepProvider {
    identity: DeepProviderIdentity,
}

impl Default for EvalDeepProvider {
    fn default() -> Self {
        Self {
            identity: DeepProviderIdentity {
                provider: "memphant-eval".to_string(),
                model: "deterministic-manifest-search-v1".to_string(),
                prompt_hash: "eval-deep-query-overlap-v1".to_string(),
                config_hash: "max-sources-8-length-path-tiebreak-v1".to_string(),
            },
        }
    }
}

impl DeepRecallProvider for EvalDeepProvider {
    fn identity(&self) -> &DeepProviderIdentity {
        &self.identity
    }

    fn limits(&self) -> DeepRecallLimits {
        DeepRecallLimits {
            wall_time_ms: 1_000,
            max_tool_iterations: 1,
            max_context_tokens: 16_384,
            max_spend_micros: 0,
        }
    }

    fn gather<'a>(
        &'a self,
        request: DeepRecallProviderRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<DeepRecallProviderResult, DeepRecallProviderError>>
                + Send
                + 'a,
        >,
    > {
        let query_tokens = request
            .query
            .split(|character: char| !character.is_alphanumeric())
            .filter(|token| token.len() > 2)
            .map(str::to_ascii_lowercase)
            .collect::<Vec<_>>();
        let mut ranked = request
            .workspace
            .files
            .into_iter()
            .filter(|file| {
                file.path.starts_with("episodes/") || file.path.starts_with("resources/")
            })
            .filter_map(|file| {
                let source_id = Path::new(&file.path).file_stem()?.to_str()?.parse().ok()?;
                let body = file.body.to_ascii_lowercase();
                let overlap = query_tokens
                    .iter()
                    .filter(|token| body.contains(token.as_str()))
                    .count();
                Some((overlap, file.body.len(), file.path, source_id))
            })
            .collect::<Vec<_>>();
        ranked.sort_by(|left, right| {
            right
                .0
                .cmp(&left.0)
                .then_with(|| right.1.cmp(&left.1))
                .then_with(|| left.2.cmp(&right.2))
        });
        let source_ids = ranked
            .into_iter()
            .take(8)
            .map(|(_, _, _, source_id)| source_id)
            .collect::<Vec<_>>();
        // The local evaluator ranks authorized raw sources but does not mint
        // canonical byte-span citations. It must never label that diagnostic
        // selection as supported evidence.
        let evidence_status = EvidenceStatus::Insufficient;
        Box::pin(async move {
            Ok(DeepRecallProviderResult {
                status: DeepRecallStatus::Completed,
                stop_reason: DeepRecallStopReason::Completed,
                source_ids,
                usage: DeepRecallUsage {
                    tool_iterations: 1,
                    ..DeepRecallUsage::default()
                },
                generation_ids: Vec::new(),
                observed_provider: Some("memphant-eval-local".to_string()),
                observed_model: Some("deterministic-manifest-search-v1".to_string()),
                evidence: EvidenceDisposition {
                    contract_revision: EVIDENCE_DISPOSITION_CONTRACT_REVISION.to_string(),
                    status: evidence_status,
                    answer_policy: evidence_status.answer_policy(),
                    reason: "local evaluator sources have no canonical byte-span citations"
                        .to_string(),
                },
            })
        })
    }
}
use memphant_types::{
    ActorId, AgentNodeId, ContextualChunk, DeepProviderIdentity, DeepRecallLimits,
    DeepRecallStatus, DeepRecallStopReason, DeepRecallSummary, DeepRecallUsage, ENGINE_VERSION,
    EVIDENCE_DISPOSITION_CONTRACT_REVISION, EvidenceDisposition, EvidenceStatus, ForgetRequest,
    ForgetSelector, MarkOutcome, MarkRequest, MemoryEdgeKind, MemoryKind, NewEpisode,
    NewMemoryEdge, NewMemoryUnit, RecallContextItem, RecallDropReason, RecallMode, RecallRequest,
    RecallTime, ResolvedMemoryContext, RetrievalTrace, ScopeId, SubjectId, TRACE_SCHEMA_VERSION,
    TenantId, TraceId, TrustLevel, UnitId, UnitState,
};
use schemars::schema_for;
use serde::{Deserialize, Serialize};

pub mod bench_lme;

pub const EVAL_RUNNER_NAME: &str = "memphant-eval";
const REQUIRED_PROFILE_AXES: &[&str] = &[
    "outcome",
    "long_horizon",
    "scale",
    "longitudinal",
    "restraint",
    "interactive",
    "embedding_selection",
    "procedural",
    "systems_cost",
    "internal_syndai",
];
const REQUIRED_ACTIVATION_DECISIONS: &[&str] = &[
    "L4 Deep recall behavior",
    "Learned reranker",
    "Learned DSR/FSRS fitter",
    "DSR decay fold",
    "Procedural replay-validation harness",
    "3-tier DEK envelope encryption",
    "Ablation-voting recall",
    "Delta recall",
    "Miss-repair re-extraction",
    "Retrievability probe",
    "Consolidation event delivery",
    "Hermes memory-provider adapter",
    "External graph DB / dedicated vector engine",
    "Cache cluster",
    "TypeScript SDK",
];

#[derive(Debug, Clone)]
pub struct EvalRunOptions {
    pub archive_traces: bool,
    pub archive_dir: Option<PathBuf>,
    pub contextual_chunks_enabled: bool,
    pub temporal_validity_enabled: bool,
    pub edge_expansion_enabled: bool,
    pub context_packing_abstention_enabled: bool,
    pub procedure_recall_enabled: bool,
    pub decay_enabled: bool,
    pub l4_exhaustive_enabled: bool,
    /// Use the opt-in runtime OpenRouter provider instead of the deterministic
    /// local Deep provider. Paid/network runs must set this explicitly.
    pub l4_runtime_provider: bool,
    pub filesystem_control_enabled: bool,
}

impl Default for EvalRunOptions {
    fn default() -> Self {
        Self {
            archive_traces: false,
            archive_dir: None,
            contextual_chunks_enabled: true,
            temporal_validity_enabled: true,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            l4_exhaustive_enabled: true,
            l4_runtime_provider: false,
            filesystem_control_enabled: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EvalReport {
    pub eval_id: String,
    pub total_cases: usize,
    pub passed_cases: usize,
    pub case_results: Vec<EvalCaseResult>,
    pub archived_trace_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EvalCaseResult {
    pub id: String,
    pub passed: bool,
    pub latency_micros: u64,
    pub trace_id: Option<String>,
    pub missing_units: Vec<String>,
    pub forbidden_present: Vec<String>,
    pub missing_citations: Vec<String>,
    pub missing_trace_stages: Vec<String>,
    pub dropped_mismatches: Vec<String>,
    pub error: Option<String>,
    /// Deep-recall settlement receipt when this case ran a Deep provider
    /// (P0.3 §6 settlement accounting). `None` for Fast cases.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deep: Option<DeepSettlement>,
}

/// Deep-recall settlement receipt extracted from a case's retrieval trace
/// (tri-domain-sota-plan §6: keep the settle-on-abort receipt even when a run
/// aborts). `settled_micros` is the provider-reconciled spend that lands in
/// trace cost; `unsettled_micros_upper_bound` is the conservative upper bound
/// that may have been billed but could not be settled before cancellation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DeepSettlement {
    pub status: DeepRecallStatus,
    pub stop_reason: DeepRecallStopReason,
    pub settled_micros: u64,
    pub unsettled_micros_upper_bound: u64,
    pub tool_iterations: u32,
    pub wall_time_ms: u64,
    /// Source UUIDs the provider nominated via `record_evidence`/`finish` — what
    /// Deep actually surfaced, so a case failure ("Deep completed but didn't
    /// nominate the gold unit") is diagnosable without re-running.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub gathered_evidence_ids: Vec<String>,
    /// Provider generation IDs for every accepted model turn (audit trail).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub generation_ids: Vec<String>,
}

/// Extract the Deep settlement receipt from a trace, if the case ran Deep.
fn deep_settlement(trace: &RetrievalTrace) -> Option<DeepSettlement> {
    deep_settlement_from_summary(trace.deep.as_ref(), &trace.l4_gathered_evidence_ids)
}

/// Testable core: map a Deep summary (present only for Deep cases) to its
/// settlement receipt. `None` in ⇒ `None` out (Fast never settles).
fn deep_settlement_from_summary(
    summary: Option<&DeepRecallSummary>,
    gathered_evidence_ids: &[String],
) -> Option<DeepSettlement> {
    summary.map(|summary| DeepSettlement {
        status: summary.status,
        stop_reason: summary.stop_reason,
        settled_micros: summary.usage.spend_micros,
        unsettled_micros_upper_bound: summary.usage.unsettled_spend_micros_upper_bound,
        tool_iterations: summary.usage.tool_iterations,
        wall_time_ms: summary.usage.wall_time_ms,
        gathered_evidence_ids: gathered_evidence_ids.to_vec(),
        generation_ids: summary.generation_ids.clone(),
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GoldenVerifyReport {
    pub verified_cases: usize,
    pub case_results: Vec<GoldenVerifyCaseResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GoldenVerifyCaseResult {
    pub id: String,
    pub load_bearing: bool,
    pub second_author_confirmed: bool,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ManifestReport {
    pub categories: BTreeMap<String, Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SecurityReport {
    pub id: String,
    pub passed: bool,
    pub covered_lanes: Vec<String>,
    pub lane_results: Vec<SecurityLaneResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SecurityLaneResult {
    pub id: String,
    pub kind: String,
    pub passed: bool,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OpsReport {
    pub id: String,
    pub passed: bool,
    pub covered_checks: Vec<String>,
    pub check_results: Vec<OpsCheckResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SyndaiTraceCompareReport {
    pub id: String,
    pub surface: String,
    pub passed: bool,
    pub answer_bearing_recall: f32,
    pub missing_answer_bearing: Vec<String>,
    pub forbidden_returned: Vec<String>,
    /// Non-unit expectation misses from an embedded golden case (citations,
    /// trace stages, subquery/decomposition assertions) — empty for the
    /// file-memory surface, whose only assertions are unit-level.
    #[serde(default)]
    pub other_mismatches: Vec<String>,
    pub trace_id: Option<String>,
    pub archived_trace_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OpsCheckResult {
    pub id: String,
    pub kind: String,
    pub passed: bool,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SotaProfileReport {
    pub id: String,
    pub profile_version: String,
    pub benchmark_version: String,
    pub compare_to: String,
    pub harness_pin: BTreeMap<String, String>,
    pub axes: BTreeMap<String, SotaAxisResult>,
    pub rung_decisions: Vec<RungDecision>,
    pub activation_decisions: Vec<ActivationDecision>,
    pub activated_levers: Vec<String>,
    pub dormant_levers: Vec<String>,
    pub retired_levers: Vec<String>,
    pub archived_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SotaAxisResult {
    pub benchmark: String,
    pub metric: String,
    pub source_status: String,
    pub trace_ref: String,
    pub score: Option<f64>,
    pub baseline_score: Option<f64>,
    pub delta_vs_baseline: Option<f64>,
    pub ci: Option<[f64; 2]>,
    pub gate: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RungDecision {
    pub rung: u8,
    pub item: String,
    pub status: String,
    pub gate_met: bool,
    pub decision: String,
    pub reason: String,
    pub axes: Vec<String>,
    pub benchmark_sample_refs: Vec<String>,
    pub before_trace_ref: String,
    pub after_trace_ref: String,
    pub delta_vs_baseline: f64,
    pub ci: [f64; 2],
    pub p95_ms: f64,
    pub cost_per_1k_recalls_usd: f64,
    pub security_result: String,
    pub deletion_result: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ActivationDecision {
    pub item: String,
    pub status: String,
    pub gate_met: bool,
    pub decision: String,
    pub reason: String,
    pub before_trace_ref: Option<String>,
    pub after_trace_ref: Option<String>,
    pub delta_vs_baseline: Option<f64>,
    pub ci: Option<[f64; 2]>,
    pub p95_ms: Option<f64>,
    pub cost_per_1k_recalls_usd: Option<f64>,
    pub security_result: String,
    pub deletion_result: String,
    pub default_mode: String,
    pub exhaustive_mode: String,
}

#[derive(Debug, thiserror::Error)]
pub enum EvalError {
    #[error("I/O error at {path}: {source}")]
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("YAML parse error at {path}: {source}")]
    Yaml {
        path: PathBuf,
        source: yaml_serde::Error,
    },
    #[error("JSON parse error at {path}: {source}")]
    Json {
        path: PathBuf,
        source: serde_json::Error,
    },
    #[error("manifest mismatch: {0}")]
    Manifest(String),
    #[error("eval failed: {0}")]
    Failed(String),
    #[error("core error: {0}")]
    Core(String),
}

pub type EvalResult<T> = Result<T, EvalError>;

#[derive(Debug, Deserialize)]
struct EvalSuite {
    id: String,
    manifest: Option<PathBuf>,
    cases: Vec<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum SyndaiTraceCompareFixture {
    /// The original agent_file_memory surface: files seeded as resources.
    FileMemory(SyndaiFileMemoryFixture),
    /// Spec-28 coding-continuity families (28-syndai-code-contract §4): a
    /// golden case wrapped in the trace-compare surface report shape.
    CodingContinuity(Box<SyndaiCodingContinuityFixture>),
}

#[derive(Debug, Deserialize)]
struct SyndaiFileMemoryFixture {
    id: String,
    surface: String,
    query: String,
    token_budget: usize,
    answer_bearing_ids: Vec<String>,
    #[serde(default)]
    forbidden_ids: Vec<String>,
    files: Vec<SyndaiFileMemory>,
}

#[derive(Debug, Deserialize)]
struct SyndaiCodingContinuityFixture {
    id: String,
    surface: String,
    case: GoldenCase,
}

#[derive(Debug, Deserialize)]
struct SyndaiFileMemory {
    id: String,
    path: String,
    scope_kind: String,
    content: String,
}

#[derive(Debug, Clone, Deserialize)]
struct GoldenCase {
    id: String,
    #[serde(default)]
    second_author_confirmed: bool,
    query: String,
    #[serde(default)]
    k: Option<usize>,
    #[serde(default)]
    budget_tokens: Option<usize>,
    #[serde(default)]
    mode: Option<RecallMode>,
    #[serde(default)]
    include_beliefs: bool,
    /// Per-case override of the fusion's lexical family. Absent means the
    /// SHIPPED default (`LexicalScorer::default()` — `bm25-code` since
    /// 2026-08-01), so the oracle suite measures the product as configured.
    /// Only a case whose candidacy depends on the chunk-aware overlap pass
    /// needs to name `overlap` here; see
    /// `docs/build-log/2026-08-01-dense-default-on.md`.
    #[serde(default)]
    lexical_scorer: Option<LexicalScorer>,
    seed: GoldenSeed,
    expect: GoldenExpect,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct GoldenSeed {
    #[serde(default)]
    units: Vec<GoldenUnit>,
    #[serde(default)]
    edges: Vec<GoldenEdge>,
}

#[derive(Debug, Clone, Deserialize)]
struct GoldenUnit {
    name: String,
    #[serde(default = "primary_name")]
    tenant: String,
    #[serde(default = "primary_name")]
    scope: String,
    #[serde(default = "fixture_source_kind")]
    source_kind: String,
    episode_body: String,
    kind: MemoryKind,
    state: UnitState,
    // The golden fixtures spell this `subject_key` (its historical name before
    // the canonical cutover renamed the unit column to `fact_key`). Keep the
    // fixture-facing name so the value still populates the unit's fact key —
    // recall's subject dedup keys off it.
    #[serde(alias = "subject_key")]
    fact_key: Option<String>,
    body: String,
    trust_level: TrustLevel,
    #[serde(default)]
    deletion_generation: Option<u64>,
    #[serde(default)]
    contextual_chunks: Vec<ContextualChunk>,
    #[serde(default)]
    valid_from: Option<String>,
    #[serde(default)]
    valid_to: Option<String>,
    #[serde(default)]
    churn_class: Option<String>,
    #[serde(default)]
    review_events: Vec<GoldenReview>,
}

#[derive(Debug, Clone, Deserialize)]
struct GoldenReview {
    outcome: MarkOutcome,
}

#[derive(Debug, Clone, Deserialize)]
struct GoldenEdge {
    src: String,
    dst: String,
    kind: MemoryEdgeKind,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct GoldenExpect {
    #[serde(default)]
    answer_bearing_ids: Vec<String>,
    #[serde(default)]
    top_k_contains: Vec<String>,
    #[serde(default)]
    forbidden_units: Vec<String>,
    #[serde(default)]
    citations_include: Vec<String>,
    #[serde(default)]
    trace_stages_include: Vec<String>,
    #[serde(default)]
    trace_feature_flags_include: Vec<String>,
    #[serde(default)]
    dropped: Vec<GoldenDropped>,
    #[serde(default)]
    packed_context_contains: Vec<String>,
    #[serde(default)]
    packed_position_max: BTreeMap<String, usize>,
    #[serde(default)]
    trace_candidate_derived_by: BTreeMap<String, String>,
    #[serde(default)]
    context_derived_by: BTreeMap<String, String>,
    #[serde(default)]
    dedup_collapsed_ids_min: Option<usize>,
    #[serde(default)]
    abstention_signal: Option<bool>,
    #[serde(default)]
    high_risk_suppressed: Vec<String>,
    #[serde(default)]
    invalidated_units: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct GoldenDropped {
    unit: String,
    reason: RecallDropReason,
}

#[derive(Debug, Deserialize)]
struct SecuritySuite {
    id: String,
    lanes: Vec<SecurityLane>,
}

#[derive(Debug, Deserialize)]
struct SecurityLane {
    id: String,
    kind: String,
    #[serde(default)]
    query: String,
    #[serde(default)]
    raw_selector: Option<String>,
    #[serde(default)]
    expect_rejected: bool,
    #[serde(default)]
    high_risk_action: Option<String>,
    #[serde(default)]
    seed: GoldenSeed,
    #[serde(default)]
    forget: Option<ForgetFixture>,
    #[serde(default)]
    expect: GoldenExpect,
}

#[derive(Debug, Deserialize)]
struct ForgetFixture {
    unit: String,
    reason: String,
}

#[derive(Debug, Deserialize)]
struct OpsSuite {
    id: String,
    checks: Vec<OpsCheck>,
}

#[derive(Debug, Deserialize)]
struct SotaProfileSuite {
    id: String,
    profile_version: String,
    benchmark_version: String,
    compare_to: String,
    harness_pin: BTreeMap<String, String>,
    axes: BTreeMap<String, SotaAxisResult>,
    #[serde(default)]
    rung_decisions: Vec<RungDecision>,
    activation_decisions: Vec<ActivationDecision>,
}

#[derive(Debug, Deserialize)]
struct OpsCheck {
    id: String,
    kind: String,
    #[serde(default)]
    min_age_seconds: Option<u64>,
    #[serde(default)]
    live_blob_hashes: Vec<String>,
    #[serde(default)]
    tombstoned_blob_hashes: Vec<String>,
    #[serde(default)]
    ledger_blob_hashes: Vec<String>,
    #[serde(default)]
    expect_collect: Vec<String>,
    #[serde(default)]
    deletion_generation_bumped: bool,
    #[serde(default)]
    readback_paths: BTreeMap<String, String>,
    #[serde(default)]
    dead_ratio: Option<f64>,
    #[serde(default)]
    threshold: Option<f64>,
    #[serde(default)]
    tombstone_age_hours: Option<u64>,
    #[serde(default)]
    max_tombstone_age_hours: Option<u64>,
    #[serde(default)]
    expect_reindex_required: Option<bool>,
}

#[derive(Clone)]
struct SeedContext {
    store: InMemoryStore,
    tenant_id: TenantId,
    scope_id: ScopeId,
    actor_id: ActorId,
    named_units: HashMap<String, UnitId>,
}

impl SeedContext {
    fn resolved(&self) -> ResolvedMemoryContext {
        resolved_context(self.tenant_id, self.scope_id, self.actor_id)
    }
}

fn resolved_context(
    tenant_id: TenantId,
    scope_id: ScopeId,
    actor_id: ActorId,
) -> ResolvedMemoryContext {
    ResolvedMemoryContext {
        tenant_id,
        data_subject_id: SubjectId::from_u128(tenant_id.as_uuid().as_u128()),
        actor_id,
        actor_trust: memphant_types::TrustLevel::TrustedUser,
        scope_id,
        agent_node_id: AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
        agent_level: 0,
        subject_generation: 0,
        policy_revision: "eval-policy".to_string(),
        sources_by_kind: MemoryKind::ALL
            .into_iter()
            .map(|kind| {
                (
                    kind,
                    vec![memphant_types::ResolvedMemorySource {
                        scope_id,
                        agent_node_id: AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                    }],
                )
            })
            .collect(),
    }
}

#[derive(Debug, Clone, Copy)]
struct GoldenRunControls {
    contextual_chunks_enabled: bool,
    temporal_validity_enabled: bool,
    edge_expansion_enabled: bool,
    context_packing_abstention_enabled: bool,
    procedure_recall_enabled: bool,
    decay_enabled: bool,
    l4_exhaustive_enabled: bool,
    l4_runtime_provider: bool,
    filesystem_control_enabled: bool,
}

impl Default for GoldenRunControls {
    fn default() -> Self {
        Self {
            contextual_chunks_enabled: true,
            temporal_validity_enabled: true,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            l4_exhaustive_enabled: true,
            l4_runtime_provider: false,
            filesystem_control_enabled: false,
        }
    }
}

impl From<&EvalRunOptions> for GoldenRunControls {
    fn from(options: &EvalRunOptions) -> Self {
        Self {
            contextual_chunks_enabled: options.contextual_chunks_enabled,
            temporal_validity_enabled: options.temporal_validity_enabled,
            edge_expansion_enabled: options.edge_expansion_enabled,
            context_packing_abstention_enabled: options.context_packing_abstention_enabled,
            procedure_recall_enabled: options.procedure_recall_enabled,
            decay_enabled: options.decay_enabled,
            l4_exhaustive_enabled: options.l4_exhaustive_enabled,
            l4_runtime_provider: options.l4_runtime_provider,
            filesystem_control_enabled: options.filesystem_control_enabled,
        }
    }
}

fn percentile_ms(values: &[u64], percentile: f64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let index = ((sorted.len() as f64 - 1.0) * percentile).ceil() as usize;
    Some(sorted[index] as f64 / 1000.0)
}

/// Pack levers for the golden lane. The suites are deterministic and run at
/// `PackLevers::default()`; this reads the SAME env var the runtime reads so a
/// suite can be executed as a PAIRED ARM (`MEMPHANT_PACK_MERGE_CHUNK_BLOCKS=1`)
/// without a second harness. Unset means off, so every existing suite is
/// byte-identical to before.
///
/// An unrecognised value is a hard error, never a silent default: an eval that
/// quietly measures the wrong arm produces evidence that looks paired and is
/// not, which is worse than an eval that refuses to run.
fn eval_pack_levers() -> PackLevers {
    // Promoted default-ON 2026-08-05 (spec 30 §7): unset ⇒ merge ON, so the
    // golden lane validates what ships. `0`/`false`/`off` selects the
    // deterministic control arm. Agrees with `PackLevers::default()` and the
    // runtime env default by construction (all three resolve unset ⇒ true).
    let raw = std::env::var("MEMPHANT_PACK_MERGE_CHUNK_BLOCKS").ok();
    let merge_chunk_blocks = match raw
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None | Some("1" | "true" | "on") => true,
        Some("0" | "false" | "off") => false,
        Some(value) => panic!(
            "MEMPHANT_PACK_MERGE_CHUNK_BLOCKS: must be one of 1, true, on, 0, false, or off, \
             got {value:?}"
        ),
    };
    PackLevers {
        merge_chunk_blocks,
        ..PackLevers::default()
    }
}

pub fn run_eval_file(path: &Path, options: EvalRunOptions) -> EvalResult<EvalReport> {
    let suite: EvalSuite = read_yaml(path)?;
    let base = path.parent().unwrap_or_else(|| Path::new("."));
    if let Some(manifest) = suite.manifest.as_ref() {
        validate_manifest(&base.join(manifest), &base.join("golden"))?;
    }

    let runtime = runtime()?;
    let mut case_results = Vec::new();
    let controls = GoldenRunControls::from(&options);
    for case_path in &suite.cases {
        let case: GoldenCase = read_yaml(&base.join(case_path))?;
        let result = runtime.block_on(run_golden_case(&case, &BTreeSet::new(), controls));
        case_results.push(result);
    }

    let passed_cases = case_results.iter().filter(|case| case.passed).count();
    let recall_latencies = case_results
        .iter()
        .map(|case| case.latency_micros)
        .filter(|latency| *latency > 0)
        .collect::<Vec<_>>();
    let mut report = EvalReport {
        eval_id: suite.id,
        total_cases: case_results.len(),
        passed_cases,
        case_results,
        archived_trace_path: None,
    };

    if options.archive_traces {
        let archive_dir = options
            .archive_dir
            .unwrap_or_else(|| PathBuf::from("docs/build-log/artifacts"));
        fs::create_dir_all(&archive_dir).map_err(|source| EvalError::Io {
            path: archive_dir.clone(),
            source,
        })?;
        let archive_path = archive_dir.join(format!("{}-traces.json", report.eval_id));
        let archive = serde_json::json!({
            "eval_id": report.eval_id,
            "runner": EVAL_RUNNER_NAME,
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "metrics": {
                "total_cases": report.total_cases,
                "passed_cases": report.passed_cases,
                "recall_p50_ms": percentile_ms(&recall_latencies, 0.50),
                "recall_p95_ms": percentile_ms(&recall_latencies, 0.95),
                "contextual_chunks_enabled": options.contextual_chunks_enabled,
                "temporal_validity_enabled": options.temporal_validity_enabled,
                "edge_expansion_enabled": options.edge_expansion_enabled,
                "context_packing_abstention_enabled": options.context_packing_abstention_enabled,
                "procedure_recall_enabled": options.procedure_recall_enabled,
                "decay_enabled": options.decay_enabled,
                "l4_exhaustive_enabled": options.l4_exhaustive_enabled,
                "filesystem_control_enabled": options.filesystem_control_enabled,
            },
            "case_results": report.case_results,
        });
        write_json(&archive_path, &archive)?;
        report.archived_trace_path = Some(archive_path);
    }

    Ok(report)
}

pub fn verify_golden_file(path: &Path) -> EvalResult<GoldenVerifyReport> {
    let suite_path = if path.is_dir() {
        path.join("golden.yaml")
    } else {
        path.to_path_buf()
    };
    let suite: EvalSuite = read_yaml(&suite_path)?;
    let base = suite_path.parent().unwrap_or_else(|| Path::new("."));
    let runtime = runtime()?;
    let mut case_results = Vec::new();

    for case_path in &suite.cases {
        let case: GoldenCase = read_yaml(&base.join(case_path))?;
        let answer_bearing: BTreeSet<_> = case.expect.answer_bearing_ids.iter().cloned().collect();
        let normal = runtime.block_on(run_golden_case(
            &case,
            &BTreeSet::new(),
            GoldenRunControls::default(),
        ));
        let masked = runtime.block_on(run_golden_case(
            &case,
            &answer_bearing,
            GoldenRunControls::default(),
        ));
        let load_bearing = normal.passed
            && !masked.passed
            && case.second_author_confirmed
            && case
                .expect
                .top_k_contains
                .iter()
                .all(|unit| answer_bearing.contains(unit));
        let reason = if load_bearing {
            "masked answer-bearing units break assertions".to_string()
        } else if !case.second_author_confirmed {
            "second author confirmation is missing".to_string()
        } else if !normal.passed {
            "unmasked case does not pass".to_string()
        } else {
            "masked run still satisfies assertions or top_k lacks answer-bearing ids".to_string()
        };
        case_results.push(GoldenVerifyCaseResult {
            id: case.id,
            load_bearing,
            second_author_confirmed: case.second_author_confirmed,
            reason,
        });
    }

    Ok(GoldenVerifyReport {
        verified_cases: case_results.len(),
        case_results,
    })
}

pub fn validate_manifest(manifest_path: &Path, lane_root: &Path) -> EvalResult<ManifestReport> {
    let categories: BTreeMap<String, Vec<String>> = read_yaml(manifest_path)?;
    let declared: BTreeSet<_> = categories
        .values()
        .flat_map(|case_ids| case_ids.iter().cloned())
        .collect();
    let mut files = BTreeSet::new();
    for entry in fs::read_dir(lane_root).map_err(|source| EvalError::Io {
        path: lane_root.to_path_buf(),
        source,
    })? {
        let entry = entry.map_err(|source| EvalError::Io {
            path: lane_root.to_path_buf(),
            source,
        })?;
        let path = entry.path();
        let is_yaml = path
            .extension()
            .and_then(|ext| ext.to_str())
            .is_some_and(|ext| matches!(ext, "yaml" | "yml"));
        if is_yaml && let Some(stem) = path.file_stem().and_then(|stem| stem.to_str()) {
            files.insert(stem.to_string());
        }
    }

    let missing: Vec<_> = declared.difference(&files).cloned().collect();
    let orphan: Vec<_> = files.difference(&declared).cloned().collect();
    if !missing.is_empty() || !orphan.is_empty() {
        return Err(EvalError::Manifest(format!(
            "missing={missing:?} orphan={orphan:?}"
        )));
    }
    Ok(ManifestReport { categories })
}

pub fn run_security_file(path: &Path) -> EvalResult<SecurityReport> {
    let suite: SecuritySuite = read_yaml(path)?;
    let runtime = runtime()?;
    let mut lane_results = Vec::new();
    for lane in &suite.lanes {
        let result = runtime.block_on(run_security_lane(lane));
        lane_results.push(result);
    }

    let required = required_security_lanes();
    let present: BTreeSet<_> = lane_results.iter().map(|lane| lane.kind.clone()).collect();
    let missing: Vec<_> = required
        .iter()
        .filter(|kind| !present.contains(**kind))
        .map(|kind| (*kind).to_string())
        .collect();
    if !missing.is_empty() {
        return Err(EvalError::Failed(format!(
            "missing required security lanes: {missing:?}"
        )));
    }
    let passed = lane_results.iter().all(|lane| lane.passed);
    Ok(SecurityReport {
        id: suite.id,
        passed,
        covered_lanes: required.iter().map(|lane| (*lane).to_string()).collect(),
        lane_results,
    })
}

pub fn run_ops_file(path: &Path) -> EvalResult<OpsReport> {
    let suite: OpsSuite = read_yaml(path)?;
    let mut check_results = Vec::new();
    for check in &suite.checks {
        check_results.push(run_ops_check(check));
    }
    let required = required_ops_checks();
    let present: BTreeSet<_> = check_results
        .iter()
        .map(|check| check.kind.clone())
        .collect();
    let missing: Vec<_> = required
        .iter()
        .filter(|kind| !present.contains(**kind))
        .map(|kind| (*kind).to_string())
        .collect();
    if !missing.is_empty() {
        return Err(EvalError::Failed(format!(
            "missing required ops checks: {missing:?}"
        )));
    }
    let passed = check_results.iter().all(|check| check.passed);
    Ok(OpsReport {
        id: suite.id,
        passed,
        covered_checks: required.iter().map(|check| (*check).to_string()).collect(),
        check_results,
    })
}

pub fn run_syndai_trace_compare_file(
    path: &Path,
    options: EvalRunOptions,
) -> EvalResult<SyndaiTraceCompareReport> {
    let fixture: SyndaiTraceCompareFixture = read_yaml(path)?;
    let runtime = runtime()?;
    let mut report = runtime.block_on(run_syndai_trace_compare(&fixture))?;

    if options.archive_traces {
        let archive_dir = options
            .archive_dir
            .unwrap_or_else(|| PathBuf::from("docs/build-log/artifacts"));
        fs::create_dir_all(&archive_dir).map_err(|source| EvalError::Io {
            path: archive_dir.clone(),
            source,
        })?;
        let archive_path = archive_dir.join(format!("{}-trace-compare.json", report.id));
        let archive = serde_json::json!({
            "case_id": report.id,
            "surface": report.surface,
            "runner": EVAL_RUNNER_NAME,
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "answer_bearing_recall": report.answer_bearing_recall,
            "missing_answer_bearing": report.missing_answer_bearing,
            "forbidden_returned": report.forbidden_returned,
            "other_mismatches": report.other_mismatches,
            "trace_id": report.trace_id,
        });
        write_json(&archive_path, &archive)?;
        report.archived_trace_path = Some(archive_path);
    }

    Ok(report)
}

pub fn run_profile_file(
    path: &Path,
    compare_to: &str,
    archive_path: Option<PathBuf>,
) -> EvalResult<SotaProfileReport> {
    let suite: SotaProfileSuite = read_yaml(path)?;
    if suite.compare_to != compare_to {
        return Err(EvalError::Failed(format!(
            "profile compare_to mismatch: fixture={} requested={compare_to}",
            suite.compare_to
        )));
    }

    let mut findings = validate_profile_suite(&suite);
    if !findings.is_empty() {
        findings.sort();
        return Err(EvalError::Failed(findings.join("; ")));
    }

    let mut activated_levers = Vec::new();
    let mut dormant_levers = Vec::new();
    let mut retired_levers = Vec::new();
    for decision in &suite.activation_decisions {
        match decision.status.as_str() {
            "activated" => activated_levers.push(decision.item.clone()),
            "dormant" => dormant_levers.push(decision.item.clone()),
            "retired" => retired_levers.push(decision.item.clone()),
            _ => {}
        }
    }

    let mut report = SotaProfileReport {
        id: suite.id,
        profile_version: suite.profile_version,
        benchmark_version: suite.benchmark_version,
        compare_to: suite.compare_to,
        harness_pin: suite.harness_pin,
        axes: suite.axes,
        rung_decisions: suite.rung_decisions,
        activation_decisions: suite.activation_decisions,
        activated_levers,
        dormant_levers,
        retired_levers,
        archived_path: None,
    };

    if let Some(archive_path) = archive_path {
        if let Some(parent) = archive_path.parent() {
            fs::create_dir_all(parent).map_err(|source| EvalError::Io {
                path: parent.to_path_buf(),
                source,
            })?;
        }
        report.archived_path = Some(archive_path.clone());
        write_json(&archive_path, &report)?;
    }

    Ok(report)
}

fn validate_profile_suite(suite: &SotaProfileSuite) -> Vec<String> {
    let mut findings = Vec::new();
    // The checked-in 2026-07-03 profiles are historical evidence and retain
    // their then-public wording. Every current profile must use Deep.
    let legacy_exhaustive_names_allowed = suite.benchmark_version.ends_with("-2026-07-03");
    for axis in REQUIRED_PROFILE_AXES {
        match suite.axes.get(*axis) {
            Some(result) => validate_axis(axis, result, &mut findings),
            None => findings.push(format!("axis:missing:{axis}")),
        }
    }

    let decisions: BTreeSet<_> = suite
        .activation_decisions
        .iter()
        .map(|decision| decision.item.as_str())
        .collect();
    for item in REQUIRED_ACTIVATION_DECISIONS {
        let legacy_l4_match = *item == "L4 Deep recall behavior"
            && legacy_exhaustive_names_allowed
            && decisions.contains("L4 exhaustive recall behavior");
        if !decisions.contains(item) && !legacy_l4_match {
            findings.push(format!("activation_decision:missing:{item}"));
        }
    }
    for decision in &suite.activation_decisions {
        validate_activation_decision(decision, &mut findings);
    }
    for decision in &suite.rung_decisions {
        validate_rung_decision(
            decision,
            &suite.axes,
            legacy_exhaustive_names_allowed,
            &mut findings,
        );
    }

    findings
}

fn validate_axis(axis: &str, result: &SotaAxisResult, findings: &mut Vec<String>) {
    if result.benchmark.trim().is_empty() {
        findings.push(format!("axis:{axis}:missing_benchmark"));
    }
    if result.metric.trim().is_empty() {
        findings.push(format!("axis:{axis}:missing_metric"));
    }
    if result.source_status.trim().is_empty() {
        findings.push(format!("axis:{axis}:missing_source_status"));
    }
    if result.trace_ref.trim().is_empty() {
        findings.push(format!("axis:{axis}:missing_trace_ref"));
    }
    if result.source_status != "not_run" {
        if result.score.is_none() {
            findings.push(format!("axis:{axis}:missing_score"));
        }
        if result.delta_vs_baseline.is_none() {
            findings.push(format!("axis:{axis}:missing_delta"));
        }
        validate_ci(
            &format!("axis:{axis}"),
            result.delta_vs_baseline,
            result.ci,
            findings,
        );
    }
}

fn validate_activation_decision(decision: &ActivationDecision, findings: &mut Vec<String>) {
    if !["activated", "dormant", "retired"].contains(&decision.status.as_str()) {
        findings.push(format!(
            "activation_decision:{}:invalid_status:{}",
            decision.item, decision.status
        ));
    }
    if decision.reason.trim().is_empty() {
        findings.push(format!(
            "activation_decision:{}:missing_reason",
            decision.item
        ));
    }
    if decision.security_result != "pass" {
        findings.push(format!(
            "activation_decision:{}:security_not_pass:{}",
            decision.item, decision.security_result
        ));
    }
    if decision.deletion_result != "pass" {
        findings.push(format!(
            "activation_decision:{}:deletion_not_pass:{}",
            decision.item, decision.deletion_result
        ));
    }

    match decision.status.as_str() {
        "activated" => {
            if !decision.gate_met {
                findings.push(format!(
                    "activation_decision:{}:activated_without_gate",
                    decision.item
                ));
            }
            if decision.before_trace_ref.is_none() {
                findings.push(format!(
                    "activation_decision:{}:missing_before_trace",
                    decision.item
                ));
            }
            if decision.after_trace_ref.is_none() {
                findings.push(format!(
                    "activation_decision:{}:missing_after_trace",
                    decision.item
                ));
            }
            if decision.p95_ms.is_none() {
                findings.push(format!("activation_decision:{}:missing_p95", decision.item));
            }
            if decision.cost_per_1k_recalls_usd.is_none() {
                findings.push(format!(
                    "activation_decision:{}:missing_cost",
                    decision.item
                ));
            }
            validate_ci(
                &format!("activation_decision:{}", decision.item),
                decision.delta_vs_baseline,
                decision.ci,
                findings,
            );
        }
        "dormant" if decision.gate_met => findings.push(format!(
            "activation_decision:{}:dormant_with_gate_met",
            decision.item
        )),
        "retired" if decision.gate_met => findings.push(format!(
            "activation_decision:{}:retired_with_gate_met",
            decision.item
        )),
        _ => {}
    }
}

fn validate_rung_decision(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    legacy_exhaustive_names_allowed: bool,
    findings: &mut Vec<String>,
) {
    let prefix = format!("rung_decision:{}", decision.rung);
    if !["promoted", "dormant", "retired"].contains(&decision.status.as_str()) {
        findings.push(format!("{prefix}:invalid_status:{}", decision.status));
    }
    if decision.item.trim().is_empty() {
        findings.push(format!("{prefix}:missing_item"));
    }
    if decision.decision.trim().is_empty() {
        findings.push(format!("{prefix}:missing_decision"));
    }
    if decision.reason.trim().is_empty() {
        findings.push(format!("{prefix}:missing_reason"));
    }
    if decision.security_result != "pass" {
        findings.push(format!(
            "{prefix}:security_not_pass:{}",
            decision.security_result
        ));
    }
    if decision.deletion_result != "pass" {
        findings.push(format!(
            "{prefix}:deletion_not_pass:{}",
            decision.deletion_result
        ));
    }
    if decision.before_trace_ref.trim().is_empty() {
        findings.push(format!("{prefix}:missing_before_trace"));
    }
    if decision.after_trace_ref.trim().is_empty() {
        findings.push(format!("{prefix}:missing_after_trace"));
    }
    if decision.p95_ms <= 0.0 {
        findings.push(format!("{prefix}:missing_p95"));
    }
    if decision.cost_per_1k_recalls_usd < 0.0 {
        findings.push(format!("{prefix}:negative_cost"));
    }
    if decision.ci[0] > decision.ci[1] {
        findings.push(format!("{prefix}:ci_bounds_inverted"));
    }
    if decision.delta_vs_baseline < decision.ci[0] || decision.delta_vs_baseline > decision.ci[1] {
        findings.push(format!("{prefix}:delta_outside_ci"));
    }
    for axis in &decision.axes {
        if !axes.contains_key(axis) {
            findings.push(format!("{prefix}:unknown_axis:{axis}"));
        }
    }

    if decision.status == "promoted" {
        if !decision.gate_met {
            findings.push(format!("{prefix}:promoted_without_gate"));
        }
        if decision.delta_vs_baseline <= 0.0 {
            findings.push(format!("{prefix}:non_positive_delta"));
        }
        if decision.ci[0] <= 0.0 {
            findings.push(format!("{prefix}:ci_includes_zero"));
        }
        for axis in &decision.axes {
            if let Some(result) = axes.get(axis)
                && result.source_status == "not_run"
            {
                findings.push(format!("{prefix}:axis_not_run:{axis}"));
            }
        }
    } else if decision.gate_met {
        findings.push(format!("{prefix}:gate_met_without_promotion"));
    }

    if decision.rung == 4 && decision.status == "promoted" {
        validate_rung4_contextual_chunk_promotion(decision, axes, &prefix, findings);
    }
    if decision.rung == 5 && decision.status == "promoted" {
        validate_rung5_temporal_validity_promotion(decision, axes, &prefix, findings);
    }
    if decision.rung == 6 && decision.status == "promoted" {
        validate_rung6_edge_expansion_promotion(decision, axes, &prefix, findings);
    }
    if decision.rung == 7 && decision.status == "promoted" {
        validate_rung7_packing_abstention_promotion(decision, axes, &prefix, findings);
    }
    if decision.rung == 10 && decision.status == "promoted" {
        validate_rung10_procedural_memory_promotion(decision, axes, &prefix, findings);
    }
    if decision.rung == 11 && decision.status == "promoted" {
        validate_rung11_dsr_decay_promotion(decision, axes, &prefix, findings);
    }
    if decision.rung == 12 && decision.status == "promoted" {
        validate_rung12_deep_promotion(
            decision,
            axes,
            legacy_exhaustive_names_allowed,
            &prefix,
            findings,
        );
    }
    if decision.rung == 14 && decision.status == "retired" {
        validate_rung14_external_engine_retirement(decision, axes, &prefix, findings);
    }
    if decision.rung == 15 && decision.status == "promoted" {
        validate_rung15_inferred_belief_composition_promotion(decision, axes, &prefix, findings);
    }
}

fn validate_rung4_contextual_chunk_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "contextual chunks" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    if !decision.axes.iter().any(|axis| axis == "long_horizon") {
        findings.push(format!("{prefix}:missing_long_horizon_axis"));
    }
    if !decision.axes.iter().any(|axis| axis == "scale") {
        findings.push(format!("{prefix}:missing_scale_axis"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.to_ascii_lowercase().contains("longmemeval"))
    {
        findings.push(format!("{prefix}:missing_lme_v2_sample_ref"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.to_ascii_lowercase().contains("beam"))
    {
        findings.push(format!("{prefix}:missing_beam_sample_ref"));
    }
    for axis in ["long_horizon", "scale"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
}

fn validate_rung5_temporal_validity_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "temporal validity" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    if !decision.axes.iter().any(|axis| axis == "outcome") {
        findings.push(format!("{prefix}:missing_outcome_axis"));
    }
    if !decision.axes.iter().any(|axis| axis == "interactive") {
        findings.push(format!("{prefix}:missing_interactive_axis"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("temporal_validity"))
    {
        findings.push(format!("{prefix}:missing_golden_temporal_sample_ref"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.to_ascii_lowercase().contains("state-style"))
    {
        findings.push(format!("{prefix}:missing_state_style_sample_ref"));
    }
    for axis in ["outcome", "interactive"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
}

fn validate_rung6_edge_expansion_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "edge expansion" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    for required_axis in ["outcome", "long_horizon", "interactive"] {
        if !decision.axes.iter().any(|axis| axis == required_axis) {
            findings.push(format!("{prefix}:missing_{required_axis}_axis"));
        }
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("edge_expansion"))
    {
        findings.push(format!("{prefix}:missing_golden_edge_sample_ref"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("no-edges"))
    {
        findings.push(format!("{prefix}:missing_no_edges_control"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("filesystem-control"))
    {
        findings.push(format!("{prefix}:missing_filesystem_control"));
    }
    if decision.delta_vs_baseline < 0.03 || decision.ci[0] < 0.03 {
        findings.push(format!("{prefix}:delta_below_three_point_gate"));
    }
    for axis in ["outcome", "long_horizon", "interactive"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
}

fn validate_rung7_packing_abstention_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "packing+abstention" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    for required_axis in ["outcome", "restraint"] {
        if !decision.axes.iter().any(|axis| axis == required_axis) {
            findings.push(format!("{prefix}:missing_{required_axis}_axis"));
        }
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("packing_abstention_buried_deploy"))
    {
        findings.push(format!("{prefix}:missing_packing_sample"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("packing_abstention_contradiction"))
    {
        findings.push(format!("{prefix}:missing_abstention_sample"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("rung7-baseline"))
    {
        findings.push(format!("{prefix}:missing_baseline_control"));
    }
    for axis in ["outcome", "restraint"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
}

fn validate_rung10_procedural_memory_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "procedural memory" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    for required_axis in ["outcome", "procedural", "interactive"] {
        if !decision.axes.iter().any(|axis| axis == required_axis) {
            findings.push(format!("{prefix}:missing_{required_axis}_axis"));
        }
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("procedural_memory_replay_validation"))
    {
        findings.push(format!("{prefix}:missing_procedural_replay_sample"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("no-procedure"))
    {
        findings.push(format!("{prefix}:missing_no_procedure_control"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("rung10-state-style"))
    {
        findings.push(format!("{prefix}:missing_state_style_sample_ref"));
    }
    for axis in ["outcome", "procedural", "interactive"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
}

fn validate_rung11_dsr_decay_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "DSR decay" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    for required_axis in ["longitudinal", "interactive"] {
        if !decision.axes.iter().any(|axis| axis == required_axis) {
            findings.push(format!("{prefix}:missing_{required_axis}_axis"));
        }
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.to_ascii_lowercase().contains("memorystress"))
    {
        findings.push(format!("{prefix}:missing_memorystress_sample"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("no-decay"))
    {
        findings.push(format!("{prefix}:missing_no_decay_control"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("dsr_decay_fold_review_event"))
    {
        findings.push(format!("{prefix}:missing_review_event_sample"));
    }
    for axis in ["longitudinal", "interactive"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
}

fn validate_rung12_deep_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    legacy_exhaustive_names_allowed: bool,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    let legacy_item = legacy_exhaustive_names_allowed && decision.item == "L4 exhaustive recall";
    if decision.item != "L4 Deep recall" && !legacy_item {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    for required_axis in ["long_horizon", "scale", "interactive"] {
        if !decision.axes.iter().any(|axis| axis == required_axis) {
            findings.push(format!("{prefix}:missing_{required_axis}_axis"));
        }
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("l4_exhaustive_raw_episode_buried"))
    {
        findings.push(format!("{prefix}:missing_l4_exhaustive_sample"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("no-l4"))
    {
        findings.push(format!("{prefix}:missing_no_l4_control"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("rung12-l4-exhaustive"))
    {
        findings.push(format!("{prefix}:missing_l4_sampled_suite"));
    }
    if !decision.before_trace_ref.contains("rung12-baseline") {
        findings.push(format!("{prefix}:missing_rung12_baseline_trace"));
    }
    if !decision.after_trace_ref.contains("rung12-l4-exhaustive") {
        findings.push(format!("{prefix}:missing_rung12_l4_trace"));
    }
    for axis in ["long_horizon", "scale", "interactive"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
}

fn validate_rung14_external_engine_retirement(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "external graph/vector escape hatch" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    for required_axis in ["outcome", "long_horizon", "scale", "systems_cost"] {
        if !decision.axes.iter().any(|axis| axis == required_axis) {
            findings.push(format!("{prefix}:missing_{required_axis}_axis"));
        }
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("edge_expansion_runbook_lineage"))
    {
        findings.push(format!("{prefix}:missing_relational_edge_sample"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("no-edges"))
    {
        findings.push(format!("{prefix}:missing_no_edges_control"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("pgvector-default:wsi-local-sota-profile"))
    {
        findings.push(format!("{prefix}:missing_pgvector_profile_ref"));
    }
    if !decision.before_trace_ref.contains("wsi-local-sota-profile") {
        findings.push(format!("{prefix}:missing_wsi_profile_trace"));
    }
    if !decision
        .after_trace_ref
        .contains("rung13-learned-rerank-profile")
    {
        findings.push(format!("{prefix}:missing_rung13_no_bottleneck_trace"));
    }
    if decision.delta_vs_baseline != 0.0 || decision.ci != [0.0, 0.0] {
        findings.push(format!("{prefix}:retired_with_material_delta"));
    }
    for axis in ["outcome", "long_horizon", "scale", "systems_cost"] {
        if !axes.contains_key(axis) {
            findings.push(format!("{prefix}:missing_axis:{axis}"));
        }
    }
}

fn validate_rung15_inferred_belief_composition_promotion(
    decision: &RungDecision,
    axes: &BTreeMap<String, SotaAxisResult>,
    prefix: &str,
    findings: &mut Vec<String>,
) {
    if decision.item != "inferred-belief composition" {
        findings.push(format!("{prefix}:invalid_item:{}", decision.item));
    }
    for required_axis in ["outcome", "interactive", "restraint"] {
        if !decision.axes.iter().any(|axis| axis == required_axis) {
            findings.push(format!("{prefix}:missing_{required_axis}_axis"));
        }
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("inferred_belief_composition"))
    {
        findings.push(format!("{prefix}:missing_inferred_belief_sample"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("no-composition"))
    {
        findings.push(format!("{prefix}:missing_no_composition_control"));
    }
    if !decision
        .benchmark_sample_refs
        .iter()
        .any(|sample| sample.contains("op-bench"))
    {
        findings.push(format!("{prefix}:missing_op_bench_restraint_ref"));
    }
    if !decision.before_trace_ref.contains("rung15-baseline") {
        findings.push(format!("{prefix}:missing_rung15_baseline_trace"));
    }
    if !decision.after_trace_ref.contains("rung15-inferred-belief") {
        findings.push(format!("{prefix}:missing_rung15_composition_trace"));
    }
    if decision.delta_vs_baseline < 0.03 || decision.ci[0] < 0.03 {
        findings.push(format!("{prefix}:delta_below_three_point_gate"));
    }
    for axis in ["outcome", "interactive"] {
        match axes.get(axis) {
            Some(result) if result.delta_vs_baseline.unwrap_or_default() > 0.0 => {}
            Some(_) => findings.push(format!("{prefix}:{axis}:non_positive_axis_delta")),
            None => {}
        }
    }
    match axes.get("restraint") {
        Some(result)
            if result.gate.as_deref() == Some("pass")
                && result.delta_vs_baseline.unwrap_or_default() >= 0.0 => {}
        Some(_) => findings.push(format!("{prefix}:restraint_regressed")),
        None => {}
    }
}

fn validate_ci(prefix: &str, delta: Option<f64>, ci: Option<[f64; 2]>, findings: &mut Vec<String>) {
    let Some(delta) = delta else {
        findings.push(format!("{prefix}:missing_delta"));
        return;
    };
    let Some(ci) = ci else {
        findings.push(format!("{prefix}:missing_ci"));
        return;
    };
    if ci[0] > ci[1] {
        findings.push(format!("{prefix}:ci_bounds_inverted"));
    }
    if delta < ci[0] || delta > ci[1] {
        findings.push(format!("{prefix}:delta_outside_ci"));
    }
}

pub fn generate_trace_schema() -> serde_json::Value {
    serde_json::to_value(schema_for!(memphant_types::RetrievalTrace))
        .expect("RetrievalTrace schema serializes")
}

async fn run_syndai_trace_compare(
    fixture: &SyndaiTraceCompareFixture,
) -> EvalResult<SyndaiTraceCompareReport> {
    match fixture {
        SyndaiTraceCompareFixture::FileMemory(fixture) => run_syndai_file_memory(fixture).await,
        SyndaiTraceCompareFixture::CodingContinuity(fixture) => {
            run_syndai_coding_continuity(fixture).await
        }
    }
}

async fn run_syndai_coding_continuity(
    fixture: &SyndaiCodingContinuityFixture,
) -> EvalResult<SyndaiTraceCompareReport> {
    if fixture.surface != "coding_continuity" {
        return Err(EvalError::Failed(format!(
            "unsupported Syndai surface {}",
            fixture.surface
        )));
    }
    if fixture.case.id != fixture.id {
        return Err(EvalError::Failed(format!(
            "fixture id {} does not match embedded case id {}",
            fixture.id, fixture.case.id
        )));
    }
    let result = run_golden_case(
        &fixture.case,
        &BTreeSet::new(),
        GoldenRunControls::default(),
    )
    .await;
    if let Some(error) = result.error {
        return Err(EvalError::Failed(error));
    }
    let answer_bearing = &fixture.case.expect.answer_bearing_ids;
    let (missing_answer_bearing, missing_other): (Vec<String>, Vec<String>) = result
        .missing_units
        .into_iter()
        .partition(|name| answer_bearing.contains(name));
    let answer_bearing_recall = if answer_bearing.is_empty() {
        1.0
    } else {
        (answer_bearing.len() - missing_answer_bearing.len()) as f32 / answer_bearing.len() as f32
    };
    let other_mismatches = [
        missing_other,
        result.missing_citations,
        result.missing_trace_stages,
        result.dropped_mismatches,
    ]
    .concat();
    Ok(SyndaiTraceCompareReport {
        id: fixture.id.clone(),
        surface: fixture.surface.clone(),
        passed: result.passed,
        answer_bearing_recall,
        missing_answer_bearing,
        forbidden_returned: result.forbidden_present,
        other_mismatches,
        trace_id: result.trace_id,
        archived_trace_path: None,
    })
}

async fn run_syndai_file_memory(
    fixture: &SyndaiFileMemoryFixture,
) -> EvalResult<SyndaiTraceCompareReport> {
    if fixture.surface != "agent_file_memory" {
        return Err(EvalError::Failed(format!(
            "unsupported Syndai surface {}",
            fixture.surface
        )));
    }

    let seed = GoldenSeed {
        units: fixture
            .files
            .iter()
            .filter(|file| file.scope_kind == "agent")
            .map(|file| GoldenUnit {
                name: file.id.clone(),
                tenant: primary_name(),
                scope: primary_name(),
                source_kind: fixture_source_kind(),
                episode_body: format!("{}: {}", file.path, file.content),
                kind: MemoryKind::Resource,
                state: UnitState::Active,
                fact_key: Some(file.path.clone()),
                body: file.content.clone(),
                trust_level: TrustLevel::TrustedSystem,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                churn_class: None,
                review_events: Vec::new(),
            })
            .collect(),
        edges: Vec::new(),
    };
    let context = seed_store(&seed, &BTreeSet::new(), true, true, true).await?;
    let response = recall(
        &context.store,
        RecallRequest {
            context: context.resolved(),
            query: fixture.query.clone(),
            k: 8,
            budget_tokens: fixture.token_budget,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: ENGINE_VERSION.to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &EVAL_CLOCK,
    )
    .await
    .map_err(|error| EvalError::Core(error.to_string()))?;

    let missing_answer_bearing = fixture
        .answer_bearing_ids
        .iter()
        .filter(|name| {
            context
                .named_units
                .get(*name)
                .is_none_or(|unit_id| !response.candidate_whitelist.contains(unit_id))
        })
        .cloned()
        .collect::<Vec<_>>();
    let forbidden_returned = fixture
        .forbidden_ids
        .iter()
        .filter(|name| {
            context
                .named_units
                .get(*name)
                .is_some_and(|unit_id| response.candidate_whitelist.contains(unit_id))
        })
        .cloned()
        .collect::<Vec<_>>();
    let answer_bearing_recall = if fixture.answer_bearing_ids.is_empty() {
        1.0
    } else {
        (fixture.answer_bearing_ids.len() - missing_answer_bearing.len()) as f32
            / fixture.answer_bearing_ids.len() as f32
    };
    let passed = missing_answer_bearing.is_empty() && forbidden_returned.is_empty();

    Ok(SyndaiTraceCompareReport {
        id: fixture.id.clone(),
        surface: fixture.surface.clone(),
        passed,
        answer_bearing_recall,
        missing_answer_bearing,
        forbidden_returned,
        other_mismatches: Vec::new(),
        trace_id: Some(response.trace_id.as_uuid().to_string()),
        archived_trace_path: None,
    })
}

async fn run_golden_case(
    case: &GoldenCase,
    masked_units: &BTreeSet<String>,
    controls: GoldenRunControls,
) -> EvalCaseResult {
    match run_golden_case_inner(case, masked_units, controls).await {
        Ok(result) => result,
        Err(error) => EvalCaseResult {
            id: case.id.clone(),
            passed: false,
            trace_id: None,
            latency_micros: 0,
            missing_units: Vec::new(),
            forbidden_present: Vec::new(),
            missing_citations: Vec::new(),
            missing_trace_stages: Vec::new(),
            dropped_mismatches: Vec::new(),
            error: Some(error.to_string()),
            // A hard failure (e.g. invalid provider output) produces no trace, so
            // there is no settlement receipt to record here; a Deep recall that
            // aborts against its caps instead returns a Capped/Partial trace via
            // the Ok path above, where its settle-on-abort receipt IS captured.
            deep: None,
        },
    }
}

async fn run_golden_case_inner(
    case: &GoldenCase,
    masked_units: &BTreeSet<String>,
    controls: GoldenRunControls,
) -> EvalResult<EvalCaseResult> {
    let context = seed_store(
        &case.seed,
        masked_units,
        controls.contextual_chunks_enabled,
        controls.temporal_validity_enabled,
        !controls.filesystem_control_enabled,
    )
    .await?;
    let recall_edge_expansion_enabled =
        controls.edge_expansion_enabled && !controls.filesystem_control_enabled;
    let mode = case.mode.unwrap_or(RecallMode::Fast);
    let recall_started_at = Instant::now();
    let runtime_deep_provider = if controls.l4_exhaustive_enabled && controls.l4_runtime_provider {
        memphant_runtime::deep_recall_openrouter::build_deep_recall_provider()
            .map_err(EvalError::Failed)?
    } else {
        None
    };
    let local_deep_provider = (controls.l4_exhaustive_enabled && !controls.l4_runtime_provider)
        .then(EvalDeepProvider::default);
    let response = recall_with_pool_and_selection_and_deep(
        &context.store,
        RecallRequest {
            context: context.resolved(),
            query: case.query.clone(),
            k: case.k.unwrap_or(8),
            budget_tokens: case.budget_tokens.unwrap_or(256),
            mode,
            include_beliefs: case.include_beliefs,
            edge_expansion_enabled: recall_edge_expansion_enabled,
            context_packing_abstention_enabled: controls.context_packing_abstention_enabled,
            procedure_recall_enabled: controls.procedure_recall_enabled,
            decay_enabled: controls.decay_enabled,
            engine_version: ENGINE_VERSION.to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &EVAL_CLOCK,
        DEFAULT_RECALL_POOL_DEPTH,
        eval_pack_levers(),
        case.lexical_scorer.unwrap_or_default(),
        false,
        None,
        CrossRerankCandidateSelection::FusedHead,
        runtime_deep_provider
            .as_ref()
            .map(|provider| provider.as_ref())
            .or_else(|| {
                local_deep_provider
                    .as_ref()
                    .map(|provider| provider as &dyn DeepRecallProvider)
            }),
    )
    .await
    .map_err(|error| EvalError::Core(error.to_string()))?;
    let latency_micros = u64::try_from(recall_started_at.elapsed().as_micros()).unwrap_or(u64::MAX);

    let trace = context
        .store
        .trace_by_id_any_tenant(response.trace_id)
        .ok_or_else(|| EvalError::Failed(format!("{} missing trace", case.id)))?;
    let mut required_units = case.expect.top_k_contains.clone();
    for answer in &case.expect.answer_bearing_ids {
        if !required_units.contains(answer) {
            required_units.push(answer.clone());
        }
    }
    for packed in &case.expect.packed_context_contains {
        if !required_units.contains(packed) {
            required_units.push(packed.clone());
        }
    }
    let missing_units = required_units
        .iter()
        .filter(|name| {
            context
                .named_units
                .get(*name)
                .is_none_or(|unit_id| !response.candidate_whitelist.contains(unit_id))
        })
        .cloned()
        .collect::<Vec<_>>();
    let forbidden_present = case
        .expect
        .forbidden_units
        .iter()
        .filter(|name| {
            context
                .named_units
                .get(*name)
                .is_some_and(|unit_id| response.candidate_whitelist.contains(unit_id))
        })
        .cloned()
        .collect::<Vec<_>>();
    let missing_citations = case
        .expect
        .citations_include
        .iter()
        .filter(|name| {
            context.named_units.get(*name).is_none_or(|unit_id| {
                !response
                    .citations
                    .iter()
                    .any(|citation| citation.unit_id == *unit_id)
            })
        })
        .cloned()
        .collect::<Vec<_>>();
    let trace_stages: BTreeSet<_> = trace
        .channel_runs
        .iter()
        .map(|stage| stage.stage.as_str())
        .collect();
    let missing_trace_stages = case
        .expect
        .trace_stages_include
        .iter()
        .filter(|stage| !trace_stages.contains(stage.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    let trace_feature_flags: BTreeSet<_> = trace.feature_flags.iter().map(String::as_str).collect();
    let mut dropped_mismatches = case
        .expect
        .dropped
        .iter()
        .filter(|expected| {
            context
                .named_units
                .get(&expected.unit)
                .is_none_or(|unit_id| {
                    !trace
                        .dropped_items
                        .iter()
                        .any(|item| item.unit_id == *unit_id && item.reason == expected.reason)
                })
        })
        .map(|expected| format!("{}:{:?}", expected.unit, expected.reason))
        .collect::<Vec<_>>();
    for flag in &case.expect.trace_feature_flags_include {
        if !trace_feature_flags.contains(flag.as_str()) {
            dropped_mismatches.push(format!("trace_feature_flag_missing:{flag}"));
        }
    }
    for (name, max_position) in &case.expect.packed_position_max {
        let actual_position = context.named_units.get(name).and_then(|unit_id| {
            response
                .items
                .iter()
                .position(|item| item.unit_id == *unit_id)
                .map(|position| position + 1)
        });
        if actual_position.is_none_or(|position| position > *max_position) {
            dropped_mismatches.push(format!(
                "packed_position:{name}:expected<={max_position}:actual={}",
                actual_position
                    .map(|position| position.to_string())
                    .unwrap_or_else(|| "missing".to_string())
            ));
        }
    }
    for (name, expected) in &case.expect.trace_candidate_derived_by {
        let actual = context.named_units.get(name).and_then(|unit_id| {
            trace
                .candidates
                .iter()
                .find(|candidate| candidate.unit_id == *unit_id)
                .map(|candidate| candidate.derived_by.as_str())
        });
        if actual != Some(expected.as_str()) {
            dropped_mismatches.push(format!(
                "trace_candidate_derived_by:{name}:expected={expected}:actual={}",
                actual.unwrap_or("missing")
            ));
        }
    }
    for (name, expected) in &case.expect.context_derived_by {
        let actual = context.named_units.get(name).and_then(|unit_id| {
            response
                .items
                .iter()
                .find(|item| item.unit_id == *unit_id)
                .map(|item| item.derived_by.as_str())
        });
        if actual != Some(expected.as_str()) {
            dropped_mismatches.push(format!(
                "context_derived_by:{name}:expected={expected}:actual={}",
                actual.unwrap_or("missing")
            ));
        }
    }
    if let Some(minimum) = case.expect.dedup_collapsed_ids_min {
        let duplicate_drops = trace
            .dropped_items
            .iter()
            .filter(|item| item.reason == RecallDropReason::Duplicate)
            .count();
        if duplicate_drops < minimum {
            dropped_mismatches.push(format!(
                "dedup_collapsed_ids_min:expected>={minimum}:actual={duplicate_drops}"
            ));
        }
    }
    if let Some(expected) = case.expect.abstention_signal
        && (trace.abstention_signal != expected || response.abstention != expected)
    {
        dropped_mismatches.push(format!(
            "abstention_signal:expected={expected}:trace={}:response={}",
            trace.abstention_signal, response.abstention
        ));
    }

    let passed = missing_units.is_empty()
        && forbidden_present.is_empty()
        && missing_citations.is_empty()
        && missing_trace_stages.is_empty()
        && dropped_mismatches.is_empty();

    // Resolve the Deep provider's nominated UUIDs back to fixture unit names
    // where known, so a "Deep completed but nominated the wrong unit" failure is
    // legible in the receipt (e.g. it nominated the decoy). Unknown UUIDs pass
    // through verbatim.
    let uuid_to_name: HashMap<String, &str> = context
        .named_units
        .iter()
        .map(|(name, unit_id)| (unit_id.as_uuid().to_string(), name.as_str()))
        .collect();
    let deep = deep_settlement(&trace).map(|mut settlement| {
        settlement.gathered_evidence_ids = settlement
            .gathered_evidence_ids
            .iter()
            .map(|id| match uuid_to_name.get(id) {
                Some(name) => format!("{name}({id})"),
                None => id.clone(),
            })
            .collect();
        settlement
    });
    Ok(EvalCaseResult {
        id: case.id.clone(),
        passed,
        latency_micros,
        trace_id: Some(response.trace_id.as_uuid().to_string()),
        missing_units,
        forbidden_present,
        missing_citations,
        missing_trace_stages,
        dropped_mismatches,
        error: None,
        deep,
    })
}

async fn run_security_lane(lane: &SecurityLane) -> SecurityLaneResult {
    let outcome = match lane.kind.as_str() {
        "poisoning" | "tenant_leakage" => run_fixture_security_lane(lane).await,
        "query_filter_injection" => run_selector_injection_lane(lane),
        "high_risk_action_suppression" => run_high_risk_lane(lane).await,
        "deletion_completeness" => run_deletion_lane(lane).await,
        other => Err(EvalError::Failed(format!("unknown security lane {other}"))),
    };
    match outcome {
        Ok(detail) => SecurityLaneResult {
            id: lane.id.clone(),
            kind: lane.kind.clone(),
            passed: true,
            detail,
        },
        Err(error) => SecurityLaneResult {
            id: lane.id.clone(),
            kind: lane.kind.clone(),
            passed: false,
            detail: error.to_string(),
        },
    }
}

async fn run_fixture_security_lane(lane: &SecurityLane) -> EvalResult<String> {
    let case = GoldenCase {
        id: lane.id.clone(),
        second_author_confirmed: true,
        query: lane.query.clone(),
        k: None,
        budget_tokens: None,
        lexical_scorer: None,
        mode: None,
        include_beliefs: false,
        seed: lane.seed.clone(),
        expect: lane.expect.clone(),
    };
    let result =
        run_golden_case_inner(&case, &BTreeSet::new(), GoldenRunControls::default()).await?;
    if result.passed {
        Ok("fixture assertions passed".to_string())
    } else {
        Err(EvalError::Failed(format!("{result:?}")))
    }
}

fn run_selector_injection_lane(lane: &SecurityLane) -> EvalResult<String> {
    let rejected = lane
        .raw_selector
        .as_deref()
        .is_some_and(selector_requires_parameterization);
    if rejected == lane.expect_rejected {
        Ok("raw selector rejected before query construction".to_string())
    } else {
        Err(EvalError::Failed(
            "selector injection expectation mismatch".to_string(),
        ))
    }
}

async fn run_high_risk_lane(lane: &SecurityLane) -> EvalResult<String> {
    let context = seed_store(&lane.seed, &BTreeSet::new(), true, true, true).await?;
    let response = recall(
        &context.store,
        RecallRequest {
            context: context.resolved(),
            query: lane.query.clone(),
            k: 8,
            budget_tokens: 256,
            mode: RecallMode::Fast,
            include_beliefs: true,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: ENGINE_VERSION.to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &EVAL_CLOCK,
    )
    .await
    .map_err(|error| EvalError::Core(error.to_string()))?;

    for required in &lane.expect.top_k_contains {
        let Some(id) = context.named_units.get(required) else {
            return Err(EvalError::Failed(format!(
                "unknown expected unit {required}"
            )));
        };
        if !response.candidate_whitelist.contains(id) {
            return Err(EvalError::Failed(format!(
                "missing verified high-risk context {required}"
            )));
        }
    }

    for suppressed in &lane.expect.high_risk_suppressed {
        let Some(suppressed_id) = context.named_units.get(suppressed) else {
            return Err(EvalError::Failed(format!(
                "unknown suppressed unit {suppressed}"
            )));
        };
        if response.candidate_whitelist.contains(suppressed_id) {
            return Err(EvalError::Failed(format!(
                "suppressed high-risk context {suppressed} was returned"
            )));
        }
    }
    Ok(format!(
        "{} low-trust memories suppressed for {}",
        lane.expect.high_risk_suppressed.len(),
        lane.high_risk_action
            .as_deref()
            .unwrap_or("high-risk action")
    ))
}

async fn run_deletion_lane(lane: &SecurityLane) -> EvalResult<String> {
    let context = seed_store(&lane.seed, &BTreeSet::new(), true, true, true).await?;
    let forget = lane
        .forget
        .as_ref()
        .ok_or_else(|| EvalError::Failed("deletion lane missing forget fixture".to_string()))?;
    let unit_id = *context
        .named_units
        .get(&forget.unit)
        .ok_or_else(|| EvalError::Failed(format!("unknown forget unit {}", forget.unit)))?;
    let result = forget_memory(
        &context.store,
        &context.resolved(),
        ForgetRequest {
            subject_id: memphant_types::SubjectId::from_u128(context.tenant_id.as_uuid().as_u128()),
            scope_id: context.scope_id,
            agent_node_id: memphant_types::AgentNodeId::from_u128(
                context.scope_id.as_uuid().as_u128(),
            ),
            subject_generation: 0,
            actor_id: context.actor_id,
            selector: ForgetSelector {
                memory_unit_id: Some(unit_id),
                episode_id: None,
                resource_id: None,
                scope_id: context.scope_id,
            },
            reason: forget.reason.clone(),
        },
        &EVAL_CLOCK,
    )
    .await
    .map_err(|error| EvalError::Core(error.to_string()))?;

    for expected in &lane.expect.invalidated_units {
        let expected_id = context
            .named_units
            .get(expected)
            .ok_or_else(|| EvalError::Failed(format!("unknown invalidated unit {expected}")))?;
        if !result.invalidated_units.contains(expected_id) {
            return Err(EvalError::Failed(format!(
                "forget did not invalidate {expected}"
            )));
        }
    }

    let case = GoldenCase {
        id: lane.id.clone(),
        second_author_confirmed: true,
        query: lane.query.clone(),
        k: None,
        budget_tokens: None,
        lexical_scorer: None,
        mode: None,
        include_beliefs: false,
        seed: GoldenSeed::default(),
        expect: lane.expect.clone(),
    };
    let response = recall(
        &context.store,
        RecallRequest {
            context: context.resolved(),
            query: case.query,
            k: 8,
            budget_tokens: 256,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: ENGINE_VERSION.to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &EVAL_CLOCK,
    )
    .await
    .map_err(|error| EvalError::Core(error.to_string()))?;
    for forbidden in &lane.expect.forbidden_units {
        let forbidden_id = context
            .named_units
            .get(forbidden)
            .ok_or_else(|| EvalError::Failed(format!("unknown forbidden unit {forbidden}")))?;
        if response.candidate_whitelist.contains(forbidden_id) {
            return Err(EvalError::Failed(format!(
                "deleted unit {forbidden} returned after forget"
            )));
        }
    }
    Ok(format!(
        "deletion_generation={} verification={}",
        result.deletion_generation, result.verification
    ))
}

fn run_ops_check(check: &OpsCheck) -> OpsCheckResult {
    let outcome = match check.kind.as_str() {
        "blob_gc" => run_blob_gc_check(check),
        "deletion_saga_readback" => run_deletion_saga_check(check),
        "reindex_compaction_sla" => run_reindex_sla_check(check),
        other => Err(EvalError::Failed(format!("unknown ops check {other}"))),
    };
    match outcome {
        Ok(detail) => OpsCheckResult {
            id: check.id.clone(),
            kind: check.kind.clone(),
            passed: true,
            detail,
        },
        Err(error) => OpsCheckResult {
            id: check.id.clone(),
            kind: check.kind.clone(),
            passed: false,
            detail: error.to_string(),
        },
    }
}

fn run_blob_gc_check(check: &OpsCheck) -> EvalResult<String> {
    if check.min_age_seconds.unwrap_or_default() == 0 {
        return Err(EvalError::Failed(
            "blob GC check must set a positive MIN_AGE".to_string(),
        ));
    }
    let live: BTreeSet<_> = check.live_blob_hashes.iter().cloned().collect();
    let ledger: BTreeSet<_> = check.ledger_blob_hashes.iter().cloned().collect();
    let tombstoned: BTreeSet<_> = check.tombstoned_blob_hashes.iter().cloned().collect();
    let collect: BTreeSet<_> = tombstoned
        .intersection(&ledger)
        .filter(|hash| !live.contains(*hash))
        .cloned()
        .collect();
    let expected: BTreeSet<_> = check.expect_collect.iter().cloned().collect();
    if collect == expected {
        Ok(format!("collect={collect:?}"))
    } else {
        Err(EvalError::Failed(format!(
            "blob collect mismatch expected={expected:?} actual={collect:?}"
        )))
    }
}

fn run_deletion_saga_check(check: &OpsCheck) -> EvalResult<String> {
    if !check.deletion_generation_bumped {
        return Err(EvalError::Failed(
            "deletion generation did not bump".to_string(),
        ));
    }
    let bad_paths: Vec<_> = check
        .readback_paths
        .iter()
        .filter(|(_, state)| !matches!(state.as_str(), "clear" | "gc_pending"))
        .map(|(path, state)| format!("{path}:{state}"))
        .collect();
    if bad_paths.is_empty() {
        Ok("all deletion saga read-backs are clear or GC-pending".to_string())
    } else {
        Err(EvalError::Failed(format!(
            "uncleared deletion paths: {bad_paths:?}"
        )))
    }
}

fn run_reindex_sla_check(check: &OpsCheck) -> EvalResult<String> {
    let dead_ratio = check
        .dead_ratio
        .ok_or_else(|| EvalError::Failed("dead_ratio missing".to_string()))?;
    let threshold = check
        .threshold
        .ok_or_else(|| EvalError::Failed("threshold missing".to_string()))?;
    let age = check.tombstone_age_hours.unwrap_or_default();
    let max_age = check.max_tombstone_age_hours.unwrap_or(u64::MAX);
    let required = dead_ratio >= threshold || age >= max_age;
    if Some(required) == check.expect_reindex_required {
        Ok(format!(
            "dead_ratio={dead_ratio} threshold={threshold} tombstone_age_hours={age}"
        ))
    } else {
        Err(EvalError::Failed(format!(
            "reindex expectation mismatch expected={:?} actual={required}",
            check.expect_reindex_required
        )))
    }
}

/// A minimal but real retrieval trace whose canonical inclusion whitelist is
/// exactly the reviewed unit, so seeded review events satisfy `record_mark`'s
/// trace-existence and whitelist checks. Deterministic: the caller supplies the
/// trace id from the fixture seed.
fn seeded_review_trace(
    context: &ResolvedMemoryContext,
    trace_id: TraceId,
    unit_id: UnitId,
    unit_name: &str,
) -> RetrievalTrace {
    RetrievalTrace {
        id: trace_id,
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        policy_revision: context.policy_revision.clone(),
        query_hash: format!("fixture-review:{unit_name}"),
        engine_version: ENGINE_VERSION.to_string(),
        feature_flags: Vec::new(),
        channel_runs: Vec::new(),
        candidates: Vec::new(),
        policy_filters: Vec::new(),
        context_items: vec![RecallContextItem {
            unit_id,
            body: unit_name.to_string(),
            kind: MemoryKind::Semantic,
            derived_by: "fixture".to_string(),
            inclusion_reason: "fixture-review".to_string(),
            citation_episode_id: None,
            citation_resource_id: None,
            derived_from_unit_ids: Vec::new(),
            suppression_labels: Vec::new(),
            correction: None,
        }],
        dropped_items: Vec::new(),
        citations: Vec::new(),
        filter_selectivity: None,
        iterative_scan_depth: None,
        recall_pool_depth: 1,
        cross_rerank_ms: 0,
        cross_rerank: None,
        consolidation_lag_ms: 0,
        degradation: None,
        mode_requested: RecallMode::Fast,
        mode_executed: RecallMode::Fast,
        escalation_reason: "none".to_string(),
        procedure_ids: Vec::new(),
        procedure_validation_states: Vec::new(),
        abstention_signal: false,
        latency_ms: 0,
        token_estimate: 0,
        cost_micros: 0,
        decay_model_id: "none".to_string(),
        l4_sandbox_id: None,
        l4_gathered_evidence_ids: Vec::new(),
        deep: None,
        l4_provider: None,
        l4_model: None,
        l4_observed_provider: None,
        l4_observed_model: None,
        l4_prompt_hash: None,
        l4_config_hash: None,
        l4_workspace_manifest_sha256: None,
        recall_time: RecallTime {
            evaluated_at: EVAL_CLOCK.0.to_string(),
            transaction_as_of: EVAL_CLOCK.0.to_string(),
            valid_at: EVAL_CLOCK.0.to_string(),
        },
    }
}

async fn seed_store(
    seed: &GoldenSeed,
    masked_units: &BTreeSet<String>,
    contextual_chunks_enabled: bool,
    temporal_validity_enabled: bool,
    seed_edges_enabled: bool,
) -> EvalResult<SeedContext> {
    let store = InMemoryStore::default();
    let tenant_id = TenantId::from_u128(90_000);
    let other_tenant_id = TenantId::from_u128(90_001);
    let scope_id = ScopeId::from_u128(90_010);
    let denied_scope_id = ScopeId::from_u128(90_011);
    let actor_id = ActorId::from_u128(90_020);
    // Canonical cutover: the store rejects any hand-built context without a
    // registered binding. Seed a binding for every (tenant, scope) combination
    // this fixture writes to or reads from — the primary lane plus the negative
    // "other tenant" and "denied scope" lanes used by tenant/scope-isolation
    // cases.
    for (binding_tenant, binding_scope) in [
        (tenant_id, scope_id),
        (other_tenant_id, scope_id),
        (tenant_id, denied_scope_id),
        (other_tenant_id, denied_scope_id),
    ] {
        store.seed_context_binding(&resolved_context(binding_tenant, binding_scope, actor_id));
    }
    let mut named_units = HashMap::new();
    for unit in &seed.units {
        if masked_units.contains(&unit.name) {
            continue;
        }
        let unit_tenant_id = if unit.tenant == "other" {
            other_tenant_id
        } else {
            tenant_id
        };
        let unit_scope_id = if unit.scope == "denied" {
            denied_scope_id
        } else {
            scope_id
        };
        // Canonical cutover: every staged row is checked against its
        // transaction's context, so cross-tenant / cross-scope fixture units
        // (the negative "other"/"denied" lanes) cannot share one primary
        // transaction. Open a transaction bound to THIS unit's context.
        let unit_context = resolved_context(unit_tenant_id, unit_scope_id, actor_id);
        let mut tx = store.begin_at(&unit_context, &EVAL_CLOCK);
        let episode = store
            .stage_episode(
                &mut tx,
                NewEpisode {
                    tenant_id: unit_tenant_id,
                    data_subject_id: memphant_types::SubjectId::from_u128(
                        unit_tenant_id.as_uuid().as_u128(),
                    ),
                    scope_id: unit_scope_id,
                    agent_node_id: memphant_types::AgentNodeId::from_u128(
                        unit_scope_id.as_uuid().as_u128(),
                    ),
                    subject_generation: 0,
                    actor_id,
                    source_kind: unit.source_kind.clone(),
                    source_ref: format!("eval:{}", unit.name),
                    observed_at: "2026-07-03T00:00:00Z".to_string(),
                    source_trust: unit.trust_level,
                    dedup_key: format!("{}:{}", unit.name, unit.episode_body),
                    body: unit.episode_body.clone(),
                },
            )
            .await
            .map_err(|error| EvalError::Core(error.to_string()))?;
        let unit_id = store
            .stage_memory_unit(
                &mut tx,
                NewMemoryUnit {
                    tenant_id: unit_tenant_id,
                    data_subject_id: memphant_types::SubjectId::from_u128(
                        unit_tenant_id.as_uuid().as_u128(),
                    ),
                    scope_id: unit_scope_id,
                    agent_node_id: memphant_types::AgentNodeId::from_u128(
                        unit_scope_id.as_uuid().as_u128(),
                    ),
                    subject_generation: 0,
                    kind: unit.kind,
                    state: unit.state,
                    fact_key: unit.fact_key.clone(),
                    predicate: None,
                    body: unit.body.clone(),
                    confidence: None,
                    trust_level: unit.trust_level,
                    churn_class: unit.churn_class.clone(),
                    freshness_due_at: (unit.churn_class.as_deref() == Some("volatile"))
                        .then(|| "2026-07-03T00:00:00Z".to_string()),
                    actor_id: Some(actor_id),
                    source_kind: Some(unit.source_kind.clone()),
                    source_ref: format!("eval:{}", unit.name),
                    observed_at: "2026-07-03T00:00:00Z".to_string(),
                    source_episode_id: Some(episode.episode_id),
                    source_resource_id: None,
                    deletion_generation: unit.deletion_generation,
                    contextual_chunks: if contextual_chunks_enabled {
                        unit.contextual_chunks.clone()
                    } else {
                        Vec::new()
                    },
                    valid_from: temporal_validity_enabled
                        .then(|| unit.valid_from.clone())
                        .flatten(),
                    valid_to: temporal_validity_enabled
                        .then(|| unit.valid_to.clone())
                        .flatten(),
                    transaction_from: None,
                    transaction_to: None,
                },
            )
            .await
            .map_err(|error| EvalError::Core(error.to_string()))?;
        store
            .commit(tx)
            .await
            .map_err(|error| EvalError::Core(error.to_string()))?;
        named_units.insert(unit.name.clone(), unit_id);
    }
    // Edges only ever connect primary-scope units, so they stage under the
    // primary context.
    let edges: Vec<_> = seed
        .edges
        .iter()
        .filter(|_| seed_edges_enabled)
        .filter(|edge| !masked_units.contains(&edge.src) && !masked_units.contains(&edge.dst))
        .collect();
    if !edges.is_empty() {
        let seed_context = resolved_context(tenant_id, scope_id, actor_id);
        let mut tx = store.begin_at(&seed_context, &EVAL_CLOCK);
        for edge in edges {
            let src_id = *named_units
                .get(&edge.src)
                .ok_or_else(|| EvalError::Failed(format!("unknown edge src {}", edge.src)))?;
            let dst_id = *named_units
                .get(&edge.dst)
                .ok_or_else(|| EvalError::Failed(format!("unknown edge dst {}", edge.dst)))?;
            store
                .stage_memory_edge(
                    &mut tx,
                    NewMemoryEdge {
                        tenant_id,
                        scope_id,
                        src_id,
                        dst_id,
                        kind: edge.kind,
                    },
                )
                .await
                .map_err(|error| EvalError::Core(error.to_string()))?;
        }
        store
            .commit(tx)
            .await
            .map_err(|error| EvalError::Core(error.to_string()))?;
    }

    let mut review_trace_seed = 600_000_u128;
    for unit in seed
        .units
        .iter()
        .filter(|unit| !masked_units.contains(&unit.name))
    {
        let Some(unit_id) = named_units.get(&unit.name).copied() else {
            continue;
        };
        let unit_tenant_id = if unit.tenant == "other" {
            other_tenant_id
        } else {
            tenant_id
        };
        for (index, review) in unit.review_events.iter().enumerate() {
            review_trace_seed = review_trace_seed.saturating_add(1);
            let mut review_context = resolved_context(tenant_id, scope_id, actor_id);
            review_context.tenant_id = unit_tenant_id;
            review_context.data_subject_id =
                SubjectId::from_u128(unit_tenant_id.as_uuid().as_u128());
            // `record_mark` fails closed unless the referenced retrieval trace
            // exists and whitelists the marked unit, so seed a minimal real
            // trace per review event instead of a fabricated id.
            store
                .store_trace(
                    &review_context,
                    seeded_review_trace(
                        &review_context,
                        TraceId::from_u128(review_trace_seed),
                        unit_id,
                        &unit.name,
                    ),
                )
                .await
                .map_err(|error| EvalError::Core(error.to_string()))?;
            record_mark(
                &store,
                &review_context,
                MarkRequest {
                    subject_id: review_context.data_subject_id,
                    scope_id: review_context.scope_id,
                    actor_id: review_context.actor_id,
                    agent_node_id: review_context.agent_node_id,
                    subject_generation: review_context.subject_generation,
                    trace_id: TraceId::from_u128(review_trace_seed),
                    caller_id: format!("fixture-review:{}:{index}", unit.name),
                    used_ids: vec![unit_id],
                    outcome: review.outcome,
                },
                &EVAL_CLOCK,
            )
            .await
            .map_err(|error| EvalError::Core(error.to_string()))?;
        }
    }

    Ok(SeedContext {
        store,
        tenant_id,
        scope_id,
        actor_id,
        named_units,
    })
}

fn selector_requires_parameterization(selector: &str) -> bool {
    let normalized = selector.to_ascii_lowercase();
    normalized.contains(" or ")
        || normalized.contains("--")
        || normalized.contains(';')
        || normalized.contains("/*")
        || normalized.contains("*/")
        || normalized.contains('=')
}

fn required_security_lanes() -> [&'static str; 5] {
    [
        "poisoning",
        "query_filter_injection",
        "high_risk_action_suppression",
        "tenant_leakage",
        "deletion_completeness",
    ]
}

fn required_ops_checks() -> [&'static str; 3] {
    [
        "blob_gc",
        "deletion_saga_readback",
        "reindex_compaction_sla",
    ]
}

fn primary_name() -> String {
    "primary".to_string()
}

fn fixture_source_kind() -> String {
    "fixture".to_string()
}

fn read_yaml<T>(path: &Path) -> EvalResult<T>
where
    T: for<'de> Deserialize<'de>,
{
    let content = fs::read_to_string(path).map_err(|source| EvalError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    yaml_serde::from_str(&content).map_err(|source| EvalError::Yaml {
        path: path.to_path_buf(),
        source,
    })
}

fn write_json<T>(path: &Path, value: &T) -> EvalResult<()>
where
    T: Serialize,
{
    let json = serde_json::to_vec_pretty(value).map_err(|source| EvalError::Json {
        path: path.to_path_buf(),
        source,
    })?;
    fs::write(path, [json, b"\n".to_vec()].concat()).map_err(|source| EvalError::Io {
        path: path.to_path_buf(),
        source,
    })
}

fn runtime() -> EvalResult<tokio::runtime::Runtime> {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|source| EvalError::Failed(format!("failed to create runtime: {source}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use memphant_types::{DeepRecallLimits, DeepRecallUsage};

    #[test]
    fn deep_settlement_captures_receipt_and_is_none_for_fast() {
        // P0.3 §6: a Deep case records its settle-on-abort receipt (settled +
        // unsettled micros, status, stop reason, tool iterations). Fast
        // cases (no Deep summary) settle nothing.
        assert_eq!(deep_settlement_from_summary(None, &[]), None);

        let summary = DeepRecallSummary {
            status: DeepRecallStatus::Completed,
            stop_reason: DeepRecallStopReason::Completed,
            limits: DeepRecallLimits {
                wall_time_ms: 120_000,
                max_tool_iterations: 24,
                max_context_tokens: 96_000,
                max_spend_micros: 300_000,
            },
            usage: DeepRecallUsage {
                wall_time_ms: 2_790,
                tool_iterations: 2,
                context_tokens: 64,
                spend_micros: 1_236,
                unsettled_context_tokens_upper_bound: 0,
                unsettled_spend_micros_upper_bound: 0,
            },
            generation_ids: vec!["gen-1".to_string()],
            evidence: EvidenceDisposition {
                contract_revision: EVIDENCE_DISPOSITION_CONTRACT_REVISION.to_string(),
                status: EvidenceStatus::Supported,
                answer_policy: EvidenceStatus::Supported.answer_policy(),
                reason: "test evidence".to_string(),
            },
        };
        let settlement = deep_settlement_from_summary(Some(&summary), &["unit-abc".to_string()])
            .expect("deep case settles");
        assert_eq!(settlement.settled_micros, 1_236);
        assert_eq!(settlement.unsettled_micros_upper_bound, 0);
        assert_eq!(settlement.tool_iterations, 2);
        assert_eq!(settlement.status, DeepRecallStatus::Completed);
        assert_eq!(
            settlement.gathered_evidence_ids,
            vec!["unit-abc".to_string()]
        );
        assert_eq!(settlement.generation_ids, vec!["gen-1".to_string()]);
        // The $0.30 cap is 300_000 micros — the receipt is well under it.
        assert!(settlement.settled_micros <= 300_000);
    }
}
