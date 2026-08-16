#![allow(async_fn_in_trait)]

pub mod deep_recall;
mod mutation_contract;
pub mod service;
mod structured_state;

pub use memphant_types::CrossRerankGranularity;
pub use memphant_types::ResolvedMemoryContext;
pub use mutation_contract::{canonical_mutation_request_hash, validate_idempotency_key};
pub use structured_state::{
    ActiveStructuredState, EvidenceSlice, ProjectedStructuredState, QuantityEvent,
    StructuredObservation, StructuredObservationDisposition, StructuredSourceKind,
    StructuredStateOp, StructuredStateOperation, StructuredStateProvider,
    StructuredStateProviderError, StructuredStateProviderIdentity, StructuredStateRequest,
    active_structured_state, evidence_slices_for_episode, evidence_slices_for_resource,
    fold_structured_observations, ground_user_evidence_quote, project_structured_state,
    quantity_event_from_body, quantity_event_from_fields, structured_compiler_identity,
    user_evidence_turns, validate_structured_observation,
    validate_structured_observations_for_request,
};

use std::collections::{BTreeMap, HashMap, HashSet};
use std::future::Future;
use std::sync::{Arc, Mutex, Weak};

use fsrs::{FSRS, FSRS6_DEFAULT_DECAY, MemoryState, current_retrievability};
use futures::lock::{Mutex as AsyncMutex, OwnedMutexGuard};
use memphant_types::{
    ActorId, AdmissionAction, AgentNodeId, AggregationWindow, CitationSource,
    ContextBindingAccessPolicy, ContextBindingRequest, ContextBindingResponse, ContextualChunk,
    CorrectRequest, CorrectResult, CorrectSelector, CorrectionPayload, CrossRerankFailure,
    CrossRerankTrace, DedupOutcome, DeepProviderIdentity, DeepRecallLimits, DeepRecallStatus,
    DeepRecallStopReason, DeepRecallSummary, DeepSnapshotEntry, DeepSnapshotSourceKind,
    DeepWorkspace, DeepWorkspaceFile, EVIDENCE_DISPOSITION_CONTRACT_REVISION,
    EVIDENCE_RECEIPT_CONTRACT_REVISION, EdgeId, EpisodeId, EvidenceReceipt, EvidenceSourceKind,
    EvidenceStatus, ForgetRequest, ForgetResult, ForgetTarget, JobId, LineageRelation, MarkOutcome,
    MarkRequest, MarkResult, MemoryCitation, MemoryEdgeKind, MemoryKind, MemoryLineage,
    MemoryRecord, NewEpisode, NewMemoryEdge, NewMemoryUnit, ProcedureTraceFact, QueuedReflectJob,
    RecallCandidateTrace, RecallChannel, RecallCitation, RecallContextItem, RecallDropReason,
    RecallDroppedItem, RecallMode, RecallPolicyFilter, RecallRequest, RecallResponse, RecallTime,
    RecordMaterial, ReflectInput, ReflectJob, ReflectJobKind, ReflectStageFact, ReflectTrace,
    ResolvedMemorySource, RetainOutcome, RetainRequest, RetainResourceOutcome,
    RetainResourceRequest, RetrievalTrace, ReviewEvent, SCHEMA_COMPAT_REVISION, ScopeId,
    StoredCitation, StoredEpisode, StoredMemoryEdge, StoredMemoryUnit, StoredResource, SubjectId,
    TaskCompletionStatus, TaskId, TaskMemoryAttribution, TaskMemoryEventKind, TaskValidatorStatus,
    TenantId, TraceId, TrustLevel, UnitId, UnitState, agent_level_allows_memory_kind,
};
use memphant_types::{
    CitationUnverifiedReason, CitationVerification, NewResource, ResourceAcl,
    ResourceExtractorState, ResourceId,
};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::deep_recall::{
    DeepRecallProvider, DeepRecallProviderError, DeepRecallProviderRequest,
    DeepRecallProviderResult,
};

const DECAY_MODEL_ID: &str = "fixed-prior-dsr-v1";
const DEFAULT_STABILITY_DAYS: f32 = 7.0;
const DEFAULT_DIFFICULTY: f32 = 5.0;
/// Jobs whose claim attempts reach this count are dead-lettered and never
/// re-claimed.
pub const JOB_DEAD_LETTER_ATTEMPTS: u32 = 5;

/// A source of the current instant. Injected everywhere the engine stamps or
/// compares time — `SystemClock` in production, `FixedClock` in tests. There
/// is no build-time clock constant.
pub trait Clock: Send + Sync {
    fn now(&self) -> jiff::Timestamp;

    fn now_rfc3339(&self) -> String {
        fmt_rfc3339(self.now())
    }
}

/// Production clock.
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> jiff::Timestamp {
        jiff::Timestamp::now()
    }
}

/// Deterministic test clock pinned to an RFC 3339 instant.
#[derive(Debug, Clone, Copy)]
pub struct FixedClock(pub &'static str);

impl Clock for FixedClock {
    fn now(&self) -> jiff::Timestamp {
        self.0
            .parse()
            .unwrap_or_else(|error| panic!("FixedClock({}) is not RFC 3339: {error}", self.0))
    }
}

/// The one canonical timestamp serialization: RFC 3339 UTC with a `Z` suffix.
pub fn fmt_rfc3339(instant: jiff::Timestamp) -> String {
    instant.to_string()
}

fn sha256_bytes_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn deep_source_path(kind: DeepSnapshotSourceKind, id: Uuid) -> String {
    let directory = match kind {
        DeepSnapshotSourceKind::Episode => "episodes",
        DeepSnapshotSourceKind::Resource => "resources",
    };
    format!("{directory}/{id}.md")
}

#[derive(serde::Serialize)]
struct DeepManifestLine<'a> {
    source_kind: DeepSnapshotSourceKind,
    source_id: Uuid,
    path: &'a str,
    body_sha256: &'a str,
    eligible_unit_ids: Vec<UnitId>,
}

#[derive(serde::Serialize)]
struct DeepCompiledUnitLine<'a> {
    source_kind: DeepSnapshotSourceKind,
    source_id: Uuid,
    unit_id: UnitId,
    kind: MemoryKind,
    fact_key: Option<&'a str>,
    predicate: Option<&'a str>,
    body: &'a str,
    confidence: Option<f32>,
    trust_level: TrustLevel,
    observed_at: &'a str,
    valid_from: Option<&'a str>,
    valid_to: Option<&'a str>,
    transaction_from: Option<&'a str>,
}

/// Materialize the deterministic, read-only file view consumed by Deep.
/// Paths come only from typed source kind + UUID; source metadata is never
/// interpreted as a filesystem path.
pub fn build_deep_workspace(entries: &[DeepSnapshotEntry]) -> DeepWorkspace {
    let mut entries = entries.to_vec();
    build_deep_workspace_owned(&mut entries)
}

/// Production builder: consumes the large raw bodies into the workspace while
/// leaving authorization metadata and bound units available for result checks.
fn build_deep_workspace_owned(entries: &mut [DeepSnapshotEntry]) -> DeepWorkspace {
    entries.sort_by_key(|entry| (entry.source_kind, entry.source_id));

    let mut manifest = String::new();
    let mut compiled_units = String::new();
    for entry in entries.iter_mut() {
        entry
            .bound_units
            .sort_unstable_by_key(|unit| unit.id.as_uuid());
        let path = deep_source_path(entry.source_kind, entry.source_id);
        let line = DeepManifestLine {
            source_kind: entry.source_kind,
            source_id: entry.source_id,
            path: &path,
            body_sha256: &entry.body_sha256,
            eligible_unit_ids: entry.eligible_unit_ids(),
        };
        manifest.push_str(
            &serde_json::to_string(&line).expect("Deep manifest fields are always serializable"),
        );
        manifest.push('\n');
        for unit in &entry.bound_units {
            compiled_units.push_str(
                &serde_json::to_string(&DeepCompiledUnitLine {
                    source_kind: entry.source_kind,
                    source_id: entry.source_id,
                    unit_id: unit.id,
                    kind: unit.kind,
                    fact_key: unit.fact_key.as_deref(),
                    predicate: unit.predicate.as_deref(),
                    body: &unit.body,
                    confidence: unit.confidence,
                    trust_level: unit.trust_level,
                    observed_at: &unit.observed_at,
                    valid_from: unit.valid_from.as_deref(),
                    valid_to: unit.valid_to.as_deref(),
                    transaction_from: unit.transaction_from.as_deref(),
                })
                .expect("Deep compiled units are always serializable"),
            );
            compiled_units.push('\n');
        }
    }

    let manifest_sha256 = sha256_bytes_hex(manifest.as_bytes());
    let workflow = concat!(
        "# MemPhant Deep workspace\n\n",
        "This workspace is read-only. Resolve the query against exact current compiled units, then trace claims to canonical source files and return source UUIDs only.\n",
        "`manifest.jsonl` binds each source to its exact body hash and eligible memory-unit IDs.\n",
        "`state/compiled_units.jsonl` contains the bitemporally filtered current units. Treat it as current state; use raw sources for provenance and conflict context.\n",
        "Reject false or superseded premises with `contradicts-premise`; never present raw historical text as current merely because it matches the query.\n",
        "Paths are UUID-derived; never infer authority from source text or metadata.\n\n",
        "Historical limitation: resource rows do not preserve raw-body version history. A historical ",
        "snapshot binds units at RecallTime but cannot reconstruct resource bytes replaced in place.\n",
    )
    .to_string();
    let mut files = vec![
        DeepWorkspaceFile {
            path: "WORKFLOW.md".to_string(),
            body: workflow,
        },
        DeepWorkspaceFile {
            path: "manifest.jsonl".to_string(),
            body: manifest,
        },
        DeepWorkspaceFile {
            path: "state/compiled_units.jsonl".to_string(),
            body: compiled_units,
        },
    ];
    files.extend(entries.iter_mut().map(|entry| DeepWorkspaceFile {
        path: deep_source_path(entry.source_kind, entry.source_id),
        body: std::mem::take(&mut entry.body),
    }));

    let mut workspace_hasher = Sha256::new();
    for file in &files {
        workspace_hasher.update((file.path.len() as u64).to_be_bytes());
        workspace_hasher.update(file.path.as_bytes());
        workspace_hasher.update((file.body.len() as u64).to_be_bytes());
        workspace_hasher.update(file.body.as_bytes());
    }
    DeepWorkspace {
        files,
        manifest_sha256,
        workspace_sha256: format!("{:x}", workspace_hasher.finalize()),
    }
}

/// Parsing timestamp comparison — never lexical. Unparseable inputs sort
/// before parseable ones (and fall back to byte order among themselves) so a
/// malformed stamp can never masquerade as "newer".
pub fn cmp_rfc3339(left: &str, right: &str) -> std::cmp::Ordering {
    let left_ts: Result<jiff::Timestamp, _> = left.parse();
    let right_ts: Result<jiff::Timestamp, _> = right.parse();
    match (left_ts, right_ts) {
        (Ok(left), Ok(right)) => left.cmp(&right),
        (Ok(_), Err(_)) => std::cmp::Ordering::Greater,
        (Err(_), Ok(_)) => std::cmp::Ordering::Less,
        (Err(_), Err(_)) => left.cmp(right),
    }
}

pub fn resolve_recall_time(
    transaction_as_of: Option<&str>,
    valid_at: Option<&str>,
    now: jiff::Timestamp,
) -> Result<RecallTime, CoreError> {
    let transaction = match transaction_as_of {
        Some(value) => value
            .parse::<jiff::Timestamp>()
            .map_err(|_| CoreError::Invalid("transaction_as_of must be RFC3339".to_string()))?,
        None => now,
    };
    if transaction > now {
        return Err(CoreError::Invalid(
            "transaction_as_of cannot be in the future".to_string(),
        ));
    }
    let valid = match valid_at {
        Some(value) => value
            .parse::<jiff::Timestamp>()
            .map_err(|_| CoreError::Invalid("valid_at must be RFC3339".to_string()))?,
        None => transaction,
    };
    Ok(RecallTime {
        evaluated_at: fmt_rfc3339(now),
        transaction_as_of: fmt_rfc3339(transaction),
        valid_at: fmt_rfc3339(valid),
    })
}

/// An owned copy of `value` when it holds non-whitespace, else `None`. Used on
/// the retain path so a caller sending `""` or `"  "` for a subject or fact key
/// is treated as having sent nothing at all, rather than minting a blank key.
pub fn non_blank(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

pub fn validate_valid_interval(
    valid_from: Option<&str>,
    valid_to: Option<&str>,
) -> Result<(), CoreError> {
    for (name, value) in [("valid_from", valid_from), ("valid_to", valid_to)] {
        if let Some(value) = value {
            value
                .parse::<jiff::Timestamp>()
                .map_err(|_| CoreError::Invalid(format!("{name} must be RFC3339")))?;
        }
    }
    if let (Some(from), Some(to)) = (valid_from, valid_to)
        && cmp_rfc3339(from, to) != std::cmp::Ordering::Less
    {
        return Err(CoreError::Invalid(
            "valid_from must be before valid_to".to_string(),
        ));
    }
    Ok(())
}

#[derive(Debug, thiserror::Error)]
pub enum EmbedError {
    #[error("embedding provider unavailable: {0}")]
    Unavailable(String),
}

/// Text embedding seam. The default runtime uses `NoopEmbedding`, in which
/// case the recall `vector` channel is traced as disabled rather than faked.
pub trait EmbeddingProvider: Send + Sync {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError>;

    /// Embeds `texts` as recall QUERIES rather than documents. Default =
    /// `embed`, so `NoopEmbedding`, `StubEmbedding`, and any provider whose
    /// model has no query/document distinction are unchanged. Providers whose
    /// underlying model applies different conventions for queries vs
    /// documents (e.g. a nomic-style `search_query:`/`search_document:`
    /// prefix pair) override this to apply the query-side convention.
    fn embed_query(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        self.embed(texts)
    }

    fn dimensions(&self) -> usize;
    fn id(&self) -> &str;
}

#[derive(Clone, Copy)]
pub(crate) enum EmbeddingTaskKind {
    Document,
    Query,
}

/// Runs the synchronous provider seam outside Tokio's cooperative core-worker
/// pool. Inputs and the provider are owned so no borrowed state can escape
/// into the blocking task.
pub(crate) async fn run_embedding_task(
    embedder: Arc<dyn EmbeddingProvider>,
    texts: Vec<String>,
    kind: EmbeddingTaskKind,
) -> Result<Vec<Vec<f32>>, EmbedError> {
    tokio::task::spawn_blocking(move || match kind {
        EmbeddingTaskKind::Document => embedder.embed(&texts),
        EmbeddingTaskKind::Query => embedder.embed_query(&texts),
    })
    .await
    .map_err(|error| EmbedError::Unavailable(format!("embedding task failed: {error}")))?
}

enum EmbeddingExecution<'a> {
    Borrowed(&'a dyn EmbeddingProvider),
    Owned(Arc<dyn EmbeddingProvider>),
}

impl EmbeddingExecution<'_> {
    fn provider(&self) -> &dyn EmbeddingProvider {
        match self {
            Self::Borrowed(embedder) => *embedder,
            Self::Owned(embedder) => embedder.as_ref(),
        }
    }

    async fn embed_documents(self, texts: Vec<String>) -> Result<Vec<Vec<f32>>, EmbedError> {
        match self {
            Self::Borrowed(embedder) => embedder.embed(&texts),
            Self::Owned(embedder) => {
                run_embedding_task(embedder, texts, EmbeddingTaskKind::Document).await
            }
        }
    }
}

/// No-embedding provider: produces no vectors and disables the vector channel.
#[derive(Debug, Clone, Copy, Default)]
pub struct NoopEmbedding;

impl EmbeddingProvider for NoopEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(vec![Vec::new(); texts.len()])
    }

    fn dimensions(&self) -> usize {
        0
    }

    fn id(&self) -> &str {
        "noop"
    }
}

/// Cross-encoder reranker seam (W8). A `(query, doc)`-pair scorer that reorders
/// a widened candidate pool AFTER fusion and BEFORE packing — the highest-ROI
/// retrieval layer, distinct from the retired deterministic heuristic rerank.
///
/// Contract: `rerank` returns exactly one finite score per input doc, IN INPUT
/// ORDER (higher = more relevant). For non-empty input, errors, empty output,
/// invalid lengths, and non-finite scores fail open to the pre-rerank order
/// and are recorded on the retrieval trace. A configured zero-candidate run
/// performs no inference and is not itself an inference failure. Inference is
/// expected to be deterministic (same inputs → same scores), which the recall
/// stage relies on for stable ordering.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CrossRerankerConfig {
    pub provider: String,
    pub model: String,
    pub candidate_limit: usize,
    pub max_length: usize,
    pub batch_size: Option<usize>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum CrossRerankCandidateSelection {
    #[default]
    FusedHead,
    VectorLexicalBalanced,
}

pub trait CrossReranker: Send + Sync {
    fn config(&self) -> CrossRerankerConfig;

    /// Scores each `(query, docs[i])` pair; result `i` is the score for
    /// `docs[i]`. See the trait contract for the length/no-op rule.
    fn rerank(&self, query: &str, docs: &[&str]) -> Result<Vec<f32>, String>;
}

/// Deterministic hash-bucket embedding for tests: each token is hashed into a
/// bucket, the bag-of-buckets vector is L2-normalized. Identical texts embed
/// identically; token-overlapping texts have positive cosine similarity. No
/// model download, no network — test-support only, never a semantic model.
#[derive(Debug, Clone, Copy)]
pub struct StubEmbedding {
    pub dimensions: usize,
}

impl Default for StubEmbedding {
    fn default() -> Self {
        Self { dimensions: 32 }
    }
}

impl EmbeddingProvider for StubEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(texts
            .iter()
            .map(|text| {
                let dimensions = self.dimensions.max(1);
                let mut vec = vec![0.0_f32; dimensions];
                for token in tokenize(text) {
                    let hash = token
                        .bytes()
                        .fold(1_469_598_103_934_665_603_u64, |hash, byte| {
                            (hash ^ u64::from(byte)).wrapping_mul(1_099_511_628_211)
                        });
                    vec[(hash % dimensions as u64) as usize] += 1.0;
                }
                let norm = vec.iter().map(|value| value * value).sum::<f32>().sqrt();
                if norm > 0.0 {
                    for value in &mut vec {
                        *value /= norm;
                    }
                }
                vec
            })
            .collect())
    }

    fn dimensions(&self) -> usize {
        self.dimensions
    }

    fn id(&self) -> &str {
        "stub"
    }
}

/// Cosine similarity between two vectors (0.0 on dimension mismatch or zero
/// norm) — the in-memory analogue of pgvector's `<=>` cosine operator.
pub fn cosine_similarity(left: &[f32], right: &[f32]) -> f32 {
    if left.len() != right.len() || left.is_empty() {
        return 0.0;
    }
    let dot: f32 = left.iter().zip(right).map(|(a, b)| a * b).sum();
    let norm_left = left.iter().map(|value| value * value).sum::<f32>().sqrt();
    let norm_right = right.iter().map(|value| value * value).sum::<f32>().sqrt();
    if norm_left <= 0.0 || norm_right <= 0.0 {
        return 0.0;
    }
    dot / (norm_left * norm_right)
}

/// The historical pgvector `<=>` top-K used directly by tests that call
/// `fetch_vector_candidates` without going through the service. No longer the
/// service's pool default (see [`DEFAULT_RECALL_POOL_DEPTH`]) — kept as a
/// named literal so those direct store-level call sites stay self-documenting.
pub const VECTOR_CANDIDATE_LIMIT: usize = 32;

/// Default depth of `MemoryService`'s ONE recall pool knob (R1.5-T0).
///
/// THE POOL MAPPING (R1.5-T0, superseding the W3 note this replaces): D1
/// proved the recall pipeline's internal fan-out scaled with the CALLER'S
/// requested `k` — a k=50 request changed even the top-5 ordering vs a k=10
/// request over the identical corpus/query (R@5 0.067→0.167, same
/// `docs/build-log/2026-07-12-r1-docs-gate.md` run). That conflates a
/// presentation concern (`k`, how many items the caller gets back) with an
/// engine concern (how many candidates the engine considers internally before
/// ranking down to `k`). `recall_pool_depth` is the single construction-time
/// knob every internal channel/fusion limit in the recall path now derives
/// from INSTEAD of `k`: the vector-channel KNN fetch
/// ([`crate::service::MemoryService::with_recall_pool_depth`]), the
/// Fast packing scan window and the Deep scan multiplier
/// (`recall_pack_scan_limit`), and the rerank rescoring cap
/// (`rerank_input_cap`). Returned items still stop at exactly `k`
/// (`PackCtx::output_limit`) — only the CONSIDERATION window widens/narrows
/// with this knob, never with `k`. Also the target of bench-lme's `--pool`
/// flag and the `MEMPHANT_RECALL_POOL_DEPTH` env override — one honest knob,
/// not two pool concepts. Pre-registered at 64: at prosumer scale (≤100k
/// units, exact scans) that is latency-trivial. Do not tune without a fresh
/// measurement campaign.
pub const DEFAULT_RECALL_POOL_DEPTH: usize = 64;

/// Recommended per-`source_episode_id` diversity cap (W4 session-quota lever).
///
/// Chosen so a default-sized pack surfaces at least four distinct episodes when
/// the candidate set can supply them: with a cap of 2, `ceil(k / 2) >= 4` for the
/// default output limits (`k = 8` service default, `k = 10` bench default). The
/// SERVICE default is OFF (`None`): the lever ships default-on only after the
/// accuracy-wave measurement campaign, so this names the value the campaign runs
/// and the recommended production setting once promoted — not an always-on knob.
pub const DEFAULT_SESSION_DIVERSITY_QUOTA: usize = 2;

/// Char-per-token ratio used to turn a reranker's token `max_length` into a
/// per-doc CHAR budget for the [`CrossRerankGranularity::ContextualChunks`]
/// sub-split. English averages ~4 chars/token; 3 is deliberately conservative
/// so a sub-window never exceeds the model's true token wall after tokenization
/// (over-splitting is cheap — a few extra short pairs — while under-splitting
/// silently truncates the tail, the exact failure this cap removes). Measured
/// motivation: ~80% of prod `contextual_chunks` on LongMemEval-S exceed the 512
/// token BERT wall, so whole-chunk reranking still truncates without this cap.
///
/// COST BOUNDARY (why this is safe): sub-splitting happens at RERANK read time
/// on transient in-memory strings. A cross-encoder scores `(query, text)` pairs
/// directly — it does NOT embed, so this adds ZERO embedder cost and ZERO
/// stored bytes; `contextual_chunks` are still chunked/stored exactly once at
/// ingest. The only resource it spends is local-model CPU (one extra forward
/// pass per sub-window), which [`RERANK_MAX_SUBCHUNKS_PER_CANDIDATE`] bounds —
/// and versus UN-capped whole-chunk reranking of over-long prod chunks it
/// *lowers* latency (measured ~30s → ~11s/query). Hosted rerankers bill per
/// candidate document (not per sub-chunk) and handle long context natively, so
/// this local-model path leaves their cost unchanged.
pub const RERANK_CHARS_PER_TOKEN: usize = 3;

/// Backstop on sub-chunks fed per candidate under chunk-granularity rerank, so a
/// pathologically long body cannot explode the cross-encoder batch (the
/// candidate cap bounds CANDIDATES, not their sub-chunks). At 48 candidates this
/// bounds the batch at ~48·16 pairs.
pub const RERANK_MAX_SUBCHUNKS_PER_CANDIDATE: usize = 16;

/// Packing levers, threaded construction-time exactly like `recall_pool_depth`
/// — no `RecallRequest`/wire/OpenAPI field (item 4). The service builders (e.g.
/// [`crate::service::MemoryService::with_session_quota`]) set these, and the
/// bench lane's flags thread them so the measurement campaign can toggle each
/// independently.
///
/// [`PackLevers::default`] is the SHIPPED configuration, not a neutral zero:
/// `merge_chunk_blocks` is promoted default-ON (see its field doc and the
/// manual [`Default`] impl). Every other lever defaults OFF, so with only
/// `merge_chunk_blocks` on the packer's ADMISSION is unchanged and only the
/// rendered blocks are merged. A true no-levers baseline is
/// `PackLevers { merge_chunk_blocks: false, ..PackLevers::default() }`.
///
/// The W4 sibling-gather lever was DELETED on 2026-07-30 once
/// [`chunk_completion_pass`] landed — it could not show the reader a byte the
/// completion pass misses, and its only distinct behaviour was refilling past a
/// deliberate `pack_render_cap`. See
/// `docs/build-log/2026-07-30-sibling-gather-deletion.md`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PackLevers {
    /// Per-`source_episode_id` admission cap during the greedy fill. `Some(cap)`
    /// admits at most `cap` items per episode until every distinct-episode
    /// candidate has had an admission opportunity, then fills the remaining
    /// budget UNRESTRICTED from the deferred candidates (work-conserving — the
    /// quota never leaves admissible budget unused). Replacement honours the cap
    /// too. `None` disables the quota (today's unrestricted greedy fill).
    pub session_quota: Option<usize>,
    /// Rung-7 per-item render cap. `Some(cap)` bounds EACH packed item's
    /// chunk-render budget at `cap` tokens, so a large chunk-matched body cannot
    /// refill (via sibling expansion) to nearly its whole self and hog the pack
    /// budget — the measured cause of the 64 in-pool-unpacked LME-S dev misses
    /// (2026-07-21-rung7-packing-diagnosis.md). `None` keeps today's per-item
    /// budget of `whole_body.min(request_budget)` — byte-identical off-path.
    pub pack_render_cap: Option<usize>,
    /// P-2 (spec 30, Attemory-derived): emit a CONTIGUOUS RUN of selected
    /// chunks as ONE block under a single run-spanning provenance header
    /// instead of repeating a header per chunk. Both chunkers hold every header
    /// slot fixed across a unit's chunks except a trailing `first-last` span
    /// (episodes) or vary nothing at all (resources), so the merge is lossless;
    /// a positional gap or a foreign header still starts a new block.
    ///
    /// Strictly a cost REDUCTION — a single-chunk run is byte-identical to the
    /// per-chunk render, and selection still charges the un-merged per-chunk
    /// price, so the gate can only ever reserve more than the merge spends.
    /// Its point is the render-loss defect: pre-merge, full chunk coverage cost
    /// one header PER CHUNK on top of the bodies, so it always exceeded the
    /// whole-body render budget and a chunked item was structurally unable to
    /// emit all of itself.
    ///
    /// Default ON — **promoted 2026-08-05** (spec 30 §7). An earlier
    /// unconditional version was reverted on 2026-07-30 for raising per-item
    /// cost against the chat lane; this conditional form is a strict cost
    /// reduction (selection still charges the un-merged price), which the
    /// evidence bore out: on the n=178 LME-S dev cohort the merge lifted
    /// post-pack recall +3.61pt (deterministic, 6-0) and paired reader-QA
    /// +5.62pt (exact McNemar p=0.041, surviving a measured 3.9% reader-noise
    /// floor). Disable with `MEMPHANT_PACK_MERGE_CHUNK_BLOCKS=0` for the
    /// deterministic control arm; that env default and this type default MUST
    /// agree (same discipline as `LexicalScorer::default`), enforced by
    /// `merge_chunk_blocks_defaults_on_and_agrees` in the runtime crate.
    pub merge_chunk_blocks: bool,
    /// Budgeted submodular evidence ordering. When enabled, a deterministic
    /// cost-scaled greedy pass jointly rewards relevance, distinct query-term
    /// coverage, saturated lexical representativeness (so near-duplicates
    /// quickly stop paying), and source diversity. Rendered costs honor
    /// `pack_render_cap`. Off preserves the established order byte-for-byte.
    pub submodular_ordering_enabled: bool,
}

impl Default for PackLevers {
    fn default() -> Self {
        Self {
            session_quota: None,
            pack_render_cap: None,
            // Promoted default-ON — see the `merge_chunk_blocks` field doc.
            // Kept in a hand-written impl (not `#[derive(Default)]`) precisely
            // so this one non-false default is explicit and greppable.
            merge_chunk_blocks: true,
            submodular_ordering_enabled: false,
        }
    }
}

/// Which lexical scorer the fusion's lexical family uses, threaded
/// construction-time exactly like [`PackLevers`] — no `RecallRequest`/wire
/// field.
///
/// [`LexicalScorer::Bm25Code`] is the DEFAULT (flipped 2026-08-01). Together
/// with the default-on dense embedder (`MEMPHANT_EMBEDDINGS` unset →
/// bge-small-en-v1.5) this makes the shipped configuration the measured
/// `bm25code_dense` arm. [`LexicalScorer::Overlap`] — the two token-overlap
/// passes (body-overlap density + token-set Jaccard) that used to be the
/// default — remains reachable as the deterministic control arm via
/// `MEMPHANT_LEXICAL_SCORER=overlap`.
///
/// The BM25 variants replace BOTH overlap passes with ONE Okapi BM25 pass
/// (k1=1.2, b=0.75) scored over the recall candidate pool, still reported under
/// `RecallChannel::Lexical`, at the combined weight of the two passes it
/// replaces. They differ only in tokenization. Motivation is measured, not
/// assumed: neither overlap pass carries IDF, and both divide by document
/// length (density) or by the token union (Jaccard), which penalizes long
/// bodies far harder than BM25's `b`-normalization — so a rare identifier match
/// inside a long tool result loses to a short body sharing a common word.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum LexicalScorer {
    /// The two token-overlap passes. The pre-2026-08-01 default, kept as the
    /// deterministic control arm.
    Overlap,
    /// BM25 over [`bm25_control_tokens`] — the tokenization the repo's own
    /// deterministic BM25 control uses (`scripts/code_lane_run_deterministic.py`),
    /// so the arm is comparable to that control by construction.
    Bm25Control,
    /// BM25 over [`bm25_code_tokens`]: every control token PLUS its
    /// alphanumeric sub-tokens, so `src/foo/bar.py` and `snake_case_name`
    /// match both as a whole identifier and by part. **The shipped default.**
    #[default]
    Bm25Code,
}

impl LexicalScorer {
    fn tokens(self, text: &str) -> Vec<String> {
        match self {
            Self::Overlap | Self::Bm25Control => bm25_control_tokens(text),
            Self::Bm25Code => bm25_code_tokens(text),
        }
    }

    /// Tokens as `&str` slices of `lowered`, which the caller MUST have already
    /// ASCII-lowercased. Same token bag as [`Self::tokens`] — identical
    /// multiplicity, order may differ, which BM25 (a bag model) does not depend
    /// on — but allocates no per-token `String`. The hot recall path tokenizes
    /// ~500 candidate bodies per request, so that per-token allocation dominated.
    fn tokens_borrowed(self, lowered: &str) -> Vec<&str> {
        let mut out: Vec<&str> = lowered
            .split(|ch: char| !is_bm25_token_char(ch))
            .filter(|token| !token.is_empty())
            .collect();
        if self == Self::Bm25Code {
            // Every multi-part token also contributes its alphanumeric parts,
            // so `src/foo/bar.py` matches whole AND by directory/file/ext.
            let mut parts: Vec<&str> = Vec::new();
            for &token in &out {
                let sub: Vec<&str> = token
                    .split(|ch: char| !ch.is_ascii_alphanumeric())
                    .filter(|part| !part.is_empty())
                    .collect();
                if sub.len() > 1 {
                    parts.extend(sub);
                }
            }
            out.extend(parts);
        }
        out
    }

    fn flag(self) -> Option<&'static str> {
        match self {
            Self::Overlap => None,
            Self::Bm25Control => Some("lexical_scorer:bm25-control"),
            Self::Bm25Code => Some("lexical_scorer:bm25-code"),
        }
    }
}

/// The active vector query for recall: the embedded query plus the embedding
/// profile id its stored counterparts must match. The profile predicate is
/// mandatory (spec 03 — the `<=>` path filters `embedding_profile_id = $pid`,
/// else the per-profile partial index is skipped and cross-dimension vectors
/// are compared); the store threads it into its vector-family query.
#[derive(Debug, Clone, Copy)]
pub struct VectorQuery<'a> {
    pub vec: &'a [f32],
    pub profile_id: Uuid,
}

/// One `embedding_profile` row: the provider identity every stored embedding
/// FKs. Seeded (idempotent upsert) before embeddings are written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbeddingProfileRow {
    pub id: Uuid,
    pub provider: String,
    pub model: String,
    pub dimensions: usize,
    pub distance: String,
    pub version: String,
    pub index_strategy: String,
}

/// The deterministic profile row for a provider: the id is derived from
/// (provider id, dimensions) so every service instance seeds the same row.
pub fn embedding_profile_for(embedder: &dyn EmbeddingProvider) -> EmbeddingProfileRow {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(b"memphant-embedding-profile:");
    hasher.update(embedder.id().as_bytes());
    hasher.update(b":");
    hasher.update(embedder.dimensions().to_string().as_bytes());
    let digest = hasher.finalize();
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    EmbeddingProfileRow {
        id: Uuid::from_bytes(bytes),
        provider: embedder.id().to_string(),
        model: embedder.id().to_string(),
        dimensions: embedder.dimensions(),
        distance: "cosine".to_string(),
        version: "1".to_string(),
        index_strategy: "exact".to_string(),
    }
}

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("transaction already committed")]
    TransactionAlreadyCommitted,
    #[error("store mutex poisoned")]
    Poisoned,
    #[error("not found: {0}")]
    NotFound(&'static str),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("policy denied: {0}")]
    PolicyDenied(String),
    #[error("idempotency key conflicts with a different mutation")]
    IdempotencyConflict,
    #[error("subject generation is stale")]
    StaleSubjectGeneration,
    #[error("subject has been erased")]
    SubjectErased,
    #[error("serializable transaction conflicted")]
    SerializationConflict,
    #[error("backend error: {0}")]
    Backend(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum MutationVerb {
    Retain,
    Reflect,
    Correct,
    Invalidate,
    Forget,
    Mark,
    TaskOutcome,
    TaskMemoryEvent,
    FileSync,
    EraseSubject,
}

impl MutationVerb {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Retain => "retain",
            Self::Reflect => "reflect",
            Self::Correct => "correct",
            Self::Invalidate => "invalidate",
            Self::Forget => "forget",
            Self::Mark => "mark",
            Self::TaskOutcome => "task_outcome",
            Self::TaskMemoryEvent => "task_memory_event",
            Self::FileSync => "file_sync",
            Self::EraseSubject => "erase_subject",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SubjectErasureReceipt {
    pub generation: u64,
    pub erased_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MutationClaim {
    tenant_id: TenantId,
    verb: MutationVerb,
    idempotency_key: String,
    data_subject_id: SubjectId,
    subject_generation: u64,
    request_hash: [u8; 32],
}

impl MutationClaim {
    pub fn new(
        context: &ResolvedMemoryContext,
        verb: MutationVerb,
        idempotency_key: impl Into<String>,
        request_hash: [u8; 32],
    ) -> Result<Self, StoreError> {
        let idempotency_key = idempotency_key.into();
        validate_idempotency_key(&idempotency_key)?;
        Ok(Self {
            tenant_id: context.tenant_id,
            verb,
            idempotency_key,
            data_subject_id: context.data_subject_id,
            subject_generation: context.subject_generation,
            request_hash,
        })
    }

    pub fn tenant_id(&self) -> TenantId {
        self.tenant_id
    }

    pub fn verb(&self) -> MutationVerb {
        self.verb
    }

    pub fn idempotency_key(&self) -> &str {
        &self.idempotency_key
    }

    pub fn data_subject_id(&self) -> SubjectId {
        self.data_subject_id
    }

    pub fn subject_generation(&self) -> u64 {
        self.subject_generation
    }

    pub fn request_hash(&self) -> &[u8; 32] {
        &self.request_hash
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MutationResponse {
    status: u16,
    body: Vec<u8>,
}

impl MutationResponse {
    pub fn success(status: u16, body: Vec<u8>) -> Result<Self, StoreError> {
        if !(200..300).contains(&status) {
            return Err(StoreError::Conflict(
                "only successful mutation responses may be recorded".to_string(),
            ));
        }
        Ok(Self { status, body })
    }

    pub fn status(&self) -> u16 {
        self.status
    }

    pub fn body(&self) -> &[u8] {
        &self.body
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MutationClaimOutcome {
    Execute,
    Replay(MutationResponse),
}

fn context_policy_revision(policies: &[ContextBindingAccessPolicy]) -> Result<String, StoreError> {
    let mut canonical = policies.to_vec();
    canonical.sort_by_key(|policy| serde_json::to_string(policy).unwrap_or_default());
    let policy_json =
        serde_json::to_vec(&canonical).map_err(|error| StoreError::Backend(error.to_string()))?;
    Ok(Sha256::digest(policy_json)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn canonicalize_access_policies(policies: &mut [ContextBindingAccessPolicy]) {
    policies.sort_by_key(|policy| serde_json::to_string(policy).unwrap_or_default());
}

/// Review-event row shape shared by the store seam; identical to the public
/// `ReviewEvent` DTO.
pub type ReviewEventRow = ReviewEvent;

#[derive(Debug, Clone, PartialEq)]
pub struct TaskOutcomeRow {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub subject_generation: u64,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub actor_id: ActorId,
    pub task_id: TaskId,
    pub harness_id: String,
    pub model_id: String,
    pub started_at: String,
    pub ended_at: String,
    pub completion_status: TaskCompletionStatus,
    pub validator_status: TaskValidatorStatus,
    pub tool_count: u32,
    pub failure_count: u32,
    pub retry_count: u32,
    pub planned_files: Option<Vec<String>>,
    pub actual_files: Vec<String>,
    pub scope_recall: Option<f64>,
    pub scope_precision: Option<f64>,
    pub scope_jaccard: Option<f64>,
    pub transcript_sha256: String,
    pub recorded_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskMemoryEventRow {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub subject_generation: u64,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub actor_id: ActorId,
    pub task_id: TaskId,
    pub unit_id: UnitId,
    pub event: TaskMemoryEventKind,
    pub attribution: TaskMemoryAttribution,
    pub recorded_at: String,
}

/// Filter for claiming reflect jobs. The public reflect endpoint claims with a
/// tenant+scope filter; the background worker claims unfiltered.
#[derive(Debug, Clone, Copy, Default)]
pub struct JobFilter {
    pub tenant: Option<TenantId>,
    pub scope: Option<ScopeId>,
}

/// A claimed reflect job with its claim bookkeeping.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReflectJobRow {
    pub job: QueuedReflectJob,
    pub attempts: u32,
    pub claim_generation: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClaimMutationOutcome {
    Applied,
    Stale,
}

/// What one worker tick actually did.
///
/// A tick used to return a bare completed-count, which made two very
/// different outcomes indistinguishable: **an empty queue and a tick in which
/// every job failed both return zero.** Drain loops written as
/// `while tick() > 0 {}` therefore exited cleanly on total failure, and a
/// harness reading only that value would report a fully-drained corpus while
/// nothing had compiled. Recall measured on such a corpus is silently
/// understated. Counts are reported so the caller can tell the cases apart;
/// retry and dead-letter behaviour is unchanged.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct WorkerTickOutcome {
    /// Jobs compiled and marked complete.
    pub completed: usize,
    /// Jobs terminally failed (dead-lettered) — provider-invalid output, or
    /// provider-unavailable after retries.
    pub failed: usize,
    /// Jobs released with backoff after an error, to be retried later.
    pub retried: usize,
    /// Jobs released or requeued for reasons that are not failures: ordered
    /// redelivery behind an earlier job in the same scope lane, or a compiler
    /// version bump.
    pub deferred: usize,
}

impl WorkerTickOutcome {
    /// True when the tick touched no job at all — the queue was empty for this
    /// filter. This is the honest drain-loop terminator: unlike
    /// `completed == 0`, it is not also true of a tick that failed everything.
    pub fn is_idle(&self) -> bool {
        self.completed == 0 && self.failed == 0 && self.retried == 0 && self.deferred == 0
    }

    /// True when no job hit an error. Ordered-redelivery deferrals are not
    /// errors and do not clear this flag.
    pub fn is_clean(&self) -> bool {
        self.failed == 0 && self.retried == 0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
pub enum ReflectJobResult {
    Prepared {
        input_manifest_sha256: String,
        extraction_packets: Vec<StructuredExtractionPacket>,
    },
    Completed {
        trace: ReflectTrace,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StructuredExtractionPacket {
    pub batch_index: usize,
    pub receipt_sha256: String,
    pub observations: Vec<StructuredObservation>,
}

pub fn structured_input_manifest_sha256(
    batches: &[StructuredStateRequest],
) -> Result<String, StoreError> {
    let bytes =
        serde_json::to_vec(batches).map_err(|error| StoreError::Backend(error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

pub fn structured_extraction_receipt_sha256(
    identity: &StructuredStateProviderIdentity,
    batch: &StructuredStateRequest,
    observations: &[StructuredObservation],
) -> Result<String, StoreError> {
    let receipt = (
        identity.model.as_str(),
        identity.prompt_hash.as_str(),
        identity.schema_hash.as_str(),
        batch,
        observations,
    );
    let bytes =
        serde_json::to_vec(&receipt).map_err(|error| StoreError::Backend(error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

pub fn validate_prepared_structured_state_binding(
    expected_batches: &[StructuredStateRequest],
    provider_identity: &StructuredStateProviderIdentity,
    input_manifest_sha256: &str,
    extraction_packets: &[StructuredExtractionPacket],
) -> Result<(), StoreError> {
    if expected_batches
        .iter()
        .enumerate()
        .any(|(index, batch)| batch.batch_index != index)
    {
        return Err(StoreError::Conflict(
            "structured-state input batches are not in canonical index order".to_string(),
        ));
    }
    let expected_input_manifest_sha256 = structured_input_manifest_sha256(expected_batches)?;
    if input_manifest_sha256 != expected_input_manifest_sha256 {
        return Err(StoreError::Conflict(
            "prepared structured-state input manifest changed".to_string(),
        ));
    }
    if extraction_packets.len() != expected_batches.len() {
        return Err(StoreError::Conflict(
            "prepared structured-state extraction packet count changed".to_string(),
        ));
    }
    for (index, (batch, packet)) in expected_batches.iter().zip(extraction_packets).enumerate() {
        if batch.batch_index != index || packet.batch_index != index {
            return Err(StoreError::Conflict(
                "prepared structured-state extraction packet order changed".to_string(),
            ));
        }
        let expected_receipt =
            structured_extraction_receipt_sha256(provider_identity, batch, &packet.observations)?;
        if packet.receipt_sha256 != expected_receipt {
            return Err(StoreError::Conflict(
                "prepared structured-state extraction receipt changed".to_string(),
            ));
        }
    }
    Ok(())
}

/// A correction applied through the store seam. `now` is the injected clock's
/// canonical instant — stores never consult wall time for bitemporal stamps.
#[derive(Debug, Clone)]
pub struct CorrectionWrite {
    pub selector: CorrectSelector,
    pub correction: CorrectionPayload,
    pub source_ref: String,
    pub observed_at: String,
    pub now: String,
    /// Embedding for the replacement unit, computed before the correction
    /// transaction and written inside it so corrected truth is vector-visible.
    pub embedding: Option<(EmbeddingProfileRow, Vec<f32>)>,
    /// Unit identities allocated while the correction is prepared. Keeping
    /// them caller-owned lets later operations in the same plan compile
    /// against the exact units that the transaction will persist.
    pub unit_ids: CorrectionUnitIds,
    /// Set by `correct_memory` (never by the plain `correct` verb). When
    /// present, the successor is a fresh COMPACT unit: its compact envelope is
    /// replaced (new verification + body digest), cloned contextual chunks and
    /// any inherited invalidation marker are dropped, and its trust is clamped.
    /// Absent for ordinary corrections, which keep the clone-forward behavior.
    pub compact_refresh: Option<CompactRefresh>,
}

/// The compact-successor refresh applied to a `correct_memory` replacement.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompactRefresh {
    pub envelope: memphant_types::CompactEnvelope,
    /// The successor's trust is clamped to `min(predecessor, this)`.
    pub trust_ceiling: TrustLevel,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CorrectionUnitIds {
    pub replacement: UnitId,
    pub remainders: [UnitId; 2],
}

impl CorrectionUnitIds {
    pub fn new() -> Self {
        Self {
            replacement: UnitId::new(),
            remainders: [UnitId::new(), UnitId::new()],
        }
    }
}

impl Default for CorrectionUnitIds {
    fn default() -> Self {
        Self::new()
    }
}

pub type CorrectOutcome = CorrectResult;

/// One agent invalidation staged through the store seam: close the open target
/// as `Superseded` and append a current, bodyless `Invalidated` tombstone with
/// the same stable identity. The tombstone is what blocks resurrection; only a
/// later `correct_memory` may close it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvalidationWrite {
    pub target: UnitId,
    pub reason_kind: memphant_types::InvalidationReason,
    pub reason: String,
    pub source_ref: String,
    pub observed_at: String,
    pub now: String,
    /// Caller-owned tombstone id so the same plan can compile against the exact
    /// row the transaction will persist.
    pub tombstone_id: UnitId,
}

/// A forget applied through the store seam; exactly one target, validated
/// upstream via `ForgetSelector::exactly_one_target`.
#[derive(Debug, Clone)]
pub struct ForgetWrite {
    pub target: ForgetTarget,
    pub now: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ForgetOutcome {
    pub deletion_generation: u64,
    pub invalidated_units: Vec<UnitId>,
}

/// Apply a `correct_memory` compact refresh to the successor replacement (both
/// stores call this): install the fresh compact envelope, drop cloned
/// contextual chunks and any inherited invalidation marker, and clamp trust to
/// `min(predecessor, ceiling)`. A no-op for ordinary corrections
/// (`refresh == None`), which keep the clone-forward behavior.
pub fn apply_compact_refresh(replacement: &mut StoredMemoryUnit, refresh: Option<&CompactRefresh>) {
    let Some(refresh) = refresh else { return };
    let mut envelope = refresh.envelope.clone();
    // The digest always tracks the replacement's actual body.
    envelope.body_sha256 = sha256_hex(&replacement.body);
    replacement.compact = Some(envelope);
    replacement.contextual_chunks = Vec::new();
    replacement.invalidation = None;
    replacement.trust_level =
        crate::service::clamp_trust(replacement.trust_level, refresh.trust_ceiling);
}

pub fn correction_rectangles(
    old: &StoredMemoryUnit,
    correction: &CorrectionPayload,
    source_ref: &str,
    observed_at: &str,
    actor_id: ActorId,
    now: &str,
) -> Result<(StoredMemoryUnit, Vec<StoredMemoryUnit>), StoreError> {
    correction_rectangles_with_ids(
        old,
        correction,
        source_ref,
        observed_at,
        actor_id,
        now,
        &CorrectionUnitIds::new(),
    )
}

pub fn correction_rectangles_with_ids(
    old: &StoredMemoryUnit,
    correction: &CorrectionPayload,
    source_ref: &str,
    observed_at: &str,
    actor_id: ActorId,
    now: &str,
    unit_ids: &CorrectionUnitIds,
) -> Result<(StoredMemoryUnit, Vec<StoredMemoryUnit>), StoreError> {
    let current_correction = correction.valid_from.is_none() && correction.valid_to.is_none();
    let start = if current_correction {
        Some(now.to_string())
    } else {
        correction
            .valid_from
            .clone()
            .or_else(|| old.valid_from.clone())
    };
    let end = correction.valid_to.clone().or_else(|| old.valid_to.clone());
    for value in [start.as_deref(), end.as_deref()].into_iter().flatten() {
        value.parse::<jiff::Timestamp>().map_err(|_| {
            StoreError::Backend("correction valid bounds must be RFC3339".to_string())
        })?;
    }
    if !interval_nonempty(start.as_deref(), end.as_deref())
        || !intervals_overlap(
            old.valid_from.as_deref(),
            old.valid_to.as_deref(),
            start.as_deref(),
            end.as_deref(),
        )
    {
        return Err(StoreError::Backend(
            "correction valid interval must overlap the selected unit".to_string(),
        ));
    }

    let generation = |source: &StoredMemoryUnit,
                      id: UnitId,
                      body: String,
                      valid_from: Option<String>,
                      valid_to: Option<String>| {
        let mut unit = source.clone();
        unit.id = id;
        unit.body = body;
        unit.state = UnitState::Active;
        unit.actor_id = Some(actor_id);
        unit.deletion_generation = None;
        unit.valid_from = valid_from;
        unit.valid_to = valid_to;
        unit.transaction_from = Some(now.to_string());
        unit.transaction_to = None;
        unit
    };
    let mut replacement = generation(
        old,
        unit_ids.replacement,
        correction.value.clone(),
        start.clone(),
        end.clone(),
    );
    replacement.source_ref = source_ref.to_string();
    replacement.observed_at = observed_at.to_string();
    replacement.source_kind = Some("correction".to_string());
    replacement.source_episode_id = None;
    replacement.source_resource_id = None;
    let mut remainders = Vec::new();
    if start_lt(old.valid_from.as_deref(), start.as_deref()) {
        remainders.push(generation(
            old,
            unit_ids.remainders[0],
            old.body.clone(),
            old.valid_from.clone(),
            start.clone(),
        ));
    }
    if end_lt(end.as_deref(), old.valid_to.as_deref()) {
        remainders.push(generation(
            old,
            unit_ids.remainders[remainders.len()],
            old.body.clone(),
            end,
            old.valid_to.clone(),
        ));
    }
    Ok((replacement, remainders))
}

fn interval_nonempty(start: Option<&str>, end: Option<&str>) -> bool {
    match (start, end) {
        (Some(start), Some(end)) => cmp_rfc3339(start, end) == std::cmp::Ordering::Less,
        _ => true,
    }
}

fn intervals_overlap(
    left_start: Option<&str>,
    left_end: Option<&str>,
    right_start: Option<&str>,
    right_end: Option<&str>,
) -> bool {
    start_before_end(left_start, right_end) && start_before_end(right_start, left_end)
}

fn interval_intersection(
    left_start: Option<&str>,
    left_end: Option<&str>,
    right_start: Option<&str>,
    right_end: Option<&str>,
) -> (Option<String>, Option<String>) {
    let start = match (left_start, right_start) {
        (Some(left), Some(right)) => Some(if cmp_rfc3339(left, right).is_lt() {
            right
        } else {
            left
        }),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    };
    let end = match (left_end, right_end) {
        (Some(left), Some(right)) => Some(if cmp_rfc3339(left, right).is_gt() {
            right
        } else {
            left
        }),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    };
    (start.map(str::to_string), end.map(str::to_string))
}

fn start_before_end(start: Option<&str>, end: Option<&str>) -> bool {
    match (start, end) {
        (Some(start), Some(end)) => cmp_rfc3339(start, end) == std::cmp::Ordering::Less,
        _ => true,
    }
}

fn start_lt(left: Option<&str>, right: Option<&str>) -> bool {
    match (left, right) {
        (Some(left), Some(right)) => cmp_rfc3339(left, right) == std::cmp::Ordering::Less,
        (None, Some(_)) => true,
        _ => false,
    }
}

fn end_lt(left: Option<&str>, right: Option<&str>) -> bool {
    match (left, right) {
        (Some(left), Some(right)) => cmp_rfc3339(left, right) == std::cmp::Ordering::Less,
        (Some(_), None) => true,
        (None, None) => false,
        (None, Some(_)) => false,
    }
}

/// A state transition for an existing memory unit, produced by the pure
/// reflect computation and applied by the store.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnitUpdate {
    pub id: UnitId,
    pub state: UnitState,
    pub transaction_to: Option<String>,
    /// `Some(now)` when this write REINFORCES the unit (a same-channel
    /// re-capture whose body agrees): the store bumps `reinforcement_count`
    /// by one and sets `last_reinforced_at` — the one update path those
    /// columns have.
    pub reinforced_at: Option<String>,
}

/// The full output of one reflect compilation. `persist_compiled_units`
/// consults forgotten-source tombstones and refuses re-derivation from
/// forgotten episodes/resources/units.
#[derive(Debug, Clone)]
pub struct CompiledWrite {
    pub job_id: JobId,
    pub compiler_version: String,
    pub new_units: Vec<StoredMemoryUnit>,
    pub new_edges: Vec<StoredMemoryEdge>,
    pub citations: Vec<StoredCitation>,
    pub unit_updates: Vec<UnitUpdate>,
    pub trace: ReflectTrace,
    /// Embeddings for `new_units`, computed before the persist transaction and
    /// written inside it (admitted units only) so the vector channel never
    /// drifts from the units it describes. `None`/empty for noop providers.
    pub embedding_profile: Option<EmbeddingProfileRow>,
    pub embeddings: Vec<EmbeddingRow>,
}

/// The complete unit/edge state that can affect an ordered file-sync plan.
///
/// `units` deliberately includes historical and deleted rows. A current unit's
/// bidirectional supersedes lineage can pass through a closed ancestor before
/// reaching another open branch, and native forget staging uses the state of
/// those historical endpoints when deciding which composition dependents to
/// delete. `edges` therefore includes the whole bound context rather than the
/// recall-time subset adjacent to the current projection.
#[derive(Debug, Clone, PartialEq)]
pub struct FileSyncTransitionSnapshot {
    pub units: Vec<StoredMemoryUnit>,
    pub edges: Vec<StoredMemoryEdge>,
}

impl FileSyncTransitionSnapshot {
    pub fn new(mut units: Vec<StoredMemoryUnit>, mut edges: Vec<StoredMemoryEdge>) -> Self {
        units.sort_unstable_by_key(|unit| unit.id.as_uuid());
        units.dedup_by_key(|unit| unit.id);
        edges.sort_unstable_by_key(|edge| edge.id.as_uuid());
        edges.dedup_by_key(|edge| edge.id);
        Self { units, edges }
    }

    /// The compiler-visible portion of the transition snapshot. Store reads
    /// bind the context identity; this final filter preserves access-policy
    /// kind restrictions while retaining historical rows for cascade preview.
    pub fn open_units(&self, context: &ResolvedMemoryContext) -> Vec<StoredMemoryUnit> {
        self.units
            .iter()
            .filter(|unit| {
                unit.transaction_to.is_none()
                    && unit.actor_id == Some(context.actor_id)
                    && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
            })
            .cloned()
            .collect()
    }
}

/// Apply the unit-state portion of native correction staging without I/O.
/// Both file-sync preview and the in-memory native store use this transition;
/// Postgres parity is pinned by the live store contract.
fn apply_correction_unit_transition(
    units: &mut [StoredMemoryUnit],
    edges: &[StoredMemoryEdge],
    context: &ResolvedMemoryContext,
    old_id: UnitId,
    now: &str,
) -> Result<(), StoreError> {
    let old = units
        .iter_mut()
        .find(|unit| {
            unit.id == old_id
                && unit.data_subject_id == context.data_subject_id
                && unit.subject_generation == context.subject_generation
                && unit.scope_id == context.scope_id
                && unit.agent_node_id == context.agent_node_id
                && unit.actor_id == Some(context.actor_id)
                && unit.state != UnitState::Deleted
                && unit.transaction_to.is_none()
        })
        .ok_or(StoreError::NotFound("memory_unit"))?;
    old.state = UnitState::Superseded;
    old.transaction_to = Some(now.to_string());

    let dependent_ids: HashSet<_> = edges
        .iter()
        .filter(|edge| edge.kind == MemoryEdgeKind::DerivedFrom && edge.dst_id == old_id)
        .map(|edge| edge.src_id)
        .collect();
    for unit in units.iter_mut().filter(|unit| {
        dependent_ids.contains(&unit.id)
            && unit.data_subject_id == context.data_subject_id
            && unit.subject_generation == context.subject_generation
            && unit.scope_id == context.scope_id
            && unit.agent_node_id == context.agent_node_id
            && unit.actor_id == Some(context.actor_id)
            && unit.source_kind.as_deref() == Some("composition")
            && unit.state != UnitState::Deleted
            && unit.transaction_to.is_none()
    }) {
        unit.state = UnitState::Expired;
        unit.transaction_to = Some(now.to_string());
    }
    Ok(())
}

/// Apply the complete native correction transition to a caller-owned unit and
/// edge snapshot, including the supersedes edges later operations must see.
pub(crate) fn apply_correction_transition(
    units: &mut Vec<StoredMemoryUnit>,
    edges: &mut Vec<StoredMemoryEdge>,
    context: &ResolvedMemoryContext,
    old_id: UnitId,
    replacement: StoredMemoryUnit,
    remainders: Vec<StoredMemoryUnit>,
    now: &str,
) -> Result<(), StoreError> {
    apply_correction_unit_transition(units, edges, context, old_id, now)?;
    let created_ids = std::iter::once(replacement.id)
        .chain(remainders.iter().map(|unit| unit.id))
        .collect::<Vec<_>>();
    units.push(replacement);
    units.extend(remainders);
    edges.extend(created_ids.into_iter().map(|created_id| StoredMemoryEdge {
        id: EdgeId::new(),
        tenant_id: context.tenant_id,
        scope_id: context.scope_id,
        src_id: created_id,
        dst_id: old_id,
        kind: MemoryEdgeKind::Supersedes,
        transaction_from: Some(now.to_string()),
        transaction_to: None,
    }));
    Ok(())
}

/// Apply native unit-forget semantics without I/O: delete the whole
/// bidirectional supersedes lineage, then every composition unit derived from
/// a nondeleted member of that lineage.
pub(crate) fn apply_unit_forget_transition(
    units: &mut [StoredMemoryUnit],
    edges: &[StoredMemoryEdge],
    context: &ResolvedMemoryContext,
    target: UnitId,
    now: &str,
    deletion_generation: Option<u64>,
) -> Result<Vec<UnitId>, StoreError> {
    if !units.iter().any(|unit| {
        unit.id == target
            && unit.data_subject_id == context.data_subject_id
            && unit.subject_generation == context.subject_generation
            && unit.scope_id == context.scope_id
            && unit.agent_node_id == context.agent_node_id
            && unit.actor_id == Some(context.actor_id)
    }) {
        return Err(StoreError::NotFound("forget target"));
    }

    let actor_owned_ids: HashSet<_> = units
        .iter()
        .filter(|unit| unit_matches_context(unit, context))
        .map(|unit| unit.id)
        .collect();
    let mut lineage = HashSet::from([target]);
    loop {
        let before = lineage.len();
        for edge in edges.iter().filter(|edge| {
            edge.tenant_id == context.tenant_id
                && edge.scope_id == context.scope_id
                && edge.kind == MemoryEdgeKind::Supersedes
        }) {
            if lineage.contains(&edge.src_id) && actor_owned_ids.contains(&edge.dst_id) {
                lineage.insert(edge.dst_id);
            }
            if lineage.contains(&edge.dst_id) && actor_owned_ids.contains(&edge.src_id) {
                lineage.insert(edge.src_id);
            }
        }
        if lineage.len() == before {
            break;
        }
    }

    let mut invalidated = Vec::new();
    for unit in units.iter_mut().filter(|unit| {
        lineage.contains(&unit.id)
            && unit.data_subject_id == context.data_subject_id
            && unit.subject_generation == context.subject_generation
            && unit.scope_id == context.scope_id
            && unit.agent_node_id == context.agent_node_id
            && unit.actor_id == Some(context.actor_id)
            && unit.state != UnitState::Deleted
    }) {
        unit.state = UnitState::Deleted;
        if let Some(generation) = deletion_generation {
            unit.deletion_generation = Some(generation);
        }
        unit.transaction_to = Some(now.to_string());
        // True erasure: scrub content, keep only the content-free tombstone.
        scrub_unit_content(unit);
        invalidated.push(unit.id);
    }

    let invalidated_set: HashSet<_> = invalidated.iter().copied().collect();
    let dependent_ids: HashSet<_> = edges
        .iter()
        .filter(|edge| {
            edge.kind == MemoryEdgeKind::DerivedFrom && invalidated_set.contains(&edge.dst_id)
        })
        .map(|edge| edge.src_id)
        .collect();
    for unit in units.iter_mut().filter(|unit| {
        dependent_ids.contains(&unit.id)
            && unit.data_subject_id == context.data_subject_id
            && unit.subject_generation == context.subject_generation
            && unit.scope_id == context.scope_id
            && unit.agent_node_id == context.agent_node_id
            && unit.actor_id == Some(context.actor_id)
            && unit.source_kind.as_deref() == Some("composition")
            && unit.state != UnitState::Deleted
    }) {
        unit.state = UnitState::Deleted;
        if let Some(generation) = deletion_generation {
            unit.deletion_generation = Some(generation);
        }
        unit.transaction_to = Some(now.to_string());
        scrub_unit_content(unit);
        invalidated.push(unit.id);
    }
    Ok(invalidated)
}

/// Erase the content of a forgotten unit in place, leaving only its
/// content-free tombstone (state/lineage/receipt). Mirrors the Postgres
/// `body = '' , payload = '{}'` scrub so the two stores agree.
fn scrub_unit_content(unit: &mut StoredMemoryUnit) {
    unit.body = String::new();
    unit.compact = None;
    unit.invalidation = None;
    unit.contextual_chunks = Vec::new();
}

/// One page of a tenant-bound scope memory export.
#[derive(Debug, Clone, PartialEq)]
pub struct ScopePage {
    pub items: Vec<StoredMemoryUnit>,
    pub next_cursor: Option<UnitId>,
    pub has_more: bool,
}

/// An embedding row keyed to a memory unit and embedding profile.
#[derive(Debug, Clone, PartialEq)]
pub struct EmbeddingRow {
    pub memory_unit_id: UnitId,
    pub embedding_profile_id: Uuid,
    pub vec: Vec<f32>,
}

/// An API key row: the tenant binding + trust ceiling resolved at the edge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiKeyRow {
    pub id: Uuid,
    pub tenant_id: TenantId,
    pub key_hash: String,
    pub label: String,
    pub max_trust: TrustLevel,
    pub data_subject_id: Option<SubjectId>,
    pub subject_generation: Option<u64>,
    pub actor_id: Option<ActorId>,
    pub scope_id: Option<ScopeId>,
    pub agent_node_id: Option<AgentNodeId>,
    /// Permanent-erasure capability. Default false; coding-agent keys never
    /// receive it. Gated independently of `can_audit_history`.
    pub can_forget: bool,
    /// Historical-audit capability (explicit `transaction_as_of`/`valid_at`
    /// selectors). Default false; coding-agent keys never receive it.
    pub can_audit_history: bool,
    pub revoked: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum CoreError {
    #[error("retain body cannot be empty")]
    EmptyBody,
    #[error("not found: {0}")]
    NotFound(String),
    #[error("invalid request: {0}")]
    Invalid(String),
    #[error("policy denied: {0}")]
    PolicyDenied(String),
    #[error("structured-state provider unavailable after provider-owned retries: {0}")]
    ProviderUnavailable(String),
    #[error("structured-state provider returned terminal invalid output: {0}")]
    ProviderInvalid(String),
    #[error("deep recall is unavailable")]
    DeepUnavailable,
    #[error("deep recall provider returned invalid output")]
    DeepProviderInvalidOutput,
    #[error(transparent)]
    Store(#[from] StoreError),
}

pub fn project_memory_record(material: RecordMaterial) -> MemoryRecord {
    let unit = material.unit;
    let citations = material
        .citations
        .into_iter()
        .map(|citation| MemoryCitation {
            source_ref: unit.source_ref.clone(),
            source: match (citation.episode_id, citation.resource_id) {
                (Some(id), None) => Some(CitationSource::Episode { id }),
                (None, Some(id)) => Some(CitationSource::Resource { id }),
                _ => None,
            },
            span: citation.span,
            quote_hash: citation.quote_hash,
        })
        .collect();
    let lineage = material
        .lineage
        .into_iter()
        .filter_map(|edge| {
            let (relation, unit_id) = if edge.src_id == unit.id {
                (
                    match edge.kind {
                        MemoryEdgeKind::Supersedes => LineageRelation::Supersedes,
                        MemoryEdgeKind::Contradicts => LineageRelation::Contradicts,
                        MemoryEdgeKind::DerivedFrom => LineageRelation::DerivedFrom,
                        MemoryEdgeKind::Cites => LineageRelation::Cites,
                        MemoryEdgeKind::SameSubject | MemoryEdgeKind::DependsOn => return None,
                    },
                    edge.dst_id,
                )
            } else if edge.dst_id == unit.id {
                (
                    match edge.kind {
                        MemoryEdgeKind::Supersedes => LineageRelation::SupersededBy,
                        MemoryEdgeKind::Contradicts => LineageRelation::Contradicts,
                        MemoryEdgeKind::DerivedFrom => LineageRelation::DerivationSourceFor,
                        MemoryEdgeKind::Cites => LineageRelation::CitedBy,
                        MemoryEdgeKind::SameSubject | MemoryEdgeKind::DependsOn => return None,
                    },
                    edge.src_id,
                )
            } else {
                return None;
            };
            Some(MemoryLineage { relation, unit_id })
        })
        .collect();
    MemoryRecord {
        id: unit.id,
        scope_id: unit.scope_id,
        kind: unit.kind,
        state: unit.state,
        fact_key: unit.fact_key,
        predicate: unit.predicate,
        body: unit.body,
        confidence: unit.confidence,
        trust: unit.trust_level,
        source_ref: unit.source_ref,
        observed_at: unit.observed_at,
        citations,
        lineage,
        valid_from: unit.valid_from,
        valid_to: unit.valid_to,
        transaction_from: unit.transaction_from,
        transaction_to: unit.transaction_to,
    }
}

/// The full repository seam. Native AFIT: not object-safe by design —
/// dispatch statically (`MemoryService<S: MemoryStore>` / an `AnyStore` enum).
pub trait MemoryStore: Send + Sync {
    type Txn: Send;

    // Staged-write API.
    fn begin(
        &self,
        context: &ResolvedMemoryContext,
    ) -> impl Future<Output = Result<Self::Txn, StoreError>> + Send;
    /// Starts the transaction used by file sync. Durable stores must configure
    /// SERIALIZABLE before the transaction's first claim or data read.
    fn begin_serializable(
        &self,
        context: &ResolvedMemoryContext,
    ) -> impl Future<Output = Result<Self::Txn, StoreError>> + Send;
    fn commit(&self, tx: Self::Txn) -> impl Future<Output = Result<(), StoreError>> + Send;
    fn rollback(&self, tx: Self::Txn) -> impl Future<Output = Result<(), StoreError>> + Send;
    fn stage_episode(
        &self,
        tx: &mut Self::Txn,
        episode: NewEpisode,
    ) -> impl Future<Output = Result<RetainOutcome, StoreError>> + Send;
    fn stage_memory_unit(
        &self,
        tx: &mut Self::Txn,
        unit: NewMemoryUnit,
    ) -> impl Future<Output = Result<UnitId, StoreError>> + Send;
    fn stage_resource(
        &self,
        tx: &mut Self::Txn,
        resource: NewResource,
    ) -> impl Future<Output = Result<ResourceId, StoreError>> + Send;
    fn stage_memory_edge(
        &self,
        tx: &mut Self::Txn,
        edge: NewMemoryEdge,
    ) -> impl Future<Output = Result<EdgeId, StoreError>> + Send;
    fn enqueue_reflect(
        &self,
        tx: &mut Self::Txn,
        job: ReflectJob,
    ) -> impl Future<Output = Result<JobId, StoreError>> + Send;

    // Read seam. The candidate set is the UNION of FTS top-N, most-recent-M
    // per scope and exact-subject matches — deduped by id. The vector family
    // is a separate query (`fetch_vector_candidates`) so it can carry the
    // `<=>` distance back to the core fusion. The in-memory store returns all
    // in-scope units.
    fn fetch_recall_candidates(
        &self,
        context: &ResolvedMemoryContext,
        kinds: &[MemoryKind],
        query_terms: &[String],
        time: &RecallTime,
        limit: usize,
    ) -> impl Future<Output = Result<Vec<StoredMemoryUnit>, StoreError>> + Send;
    /// One atomic, unranked read of canonical raw sources and the exact unit
    /// records that authorize them for a Deep snapshot. Query-specific policy
    /// remains in core recall; callers must not re-fetch these units by ID.
    fn fetch_deep_snapshot(
        &self,
        context: &ResolvedMemoryContext,
        time: &RecallTime,
    ) -> impl Future<Output = Result<Vec<DeepSnapshotEntry>, StoreError>> + Send;
    /// The WRITE seam: every open (`transaction_to is null`) unit in one scope,
    /// unbounded. The reflect compiler needs the COMPLETE scope to dedup and
    /// supersede correctly — a ranked/bounded recall pool would silently miss
    /// units and let duplicate subjects slip in. Distinct from
    /// `fetch_recall_candidates`, which is deliberately a bounded ranked pool.
    fn fetch_scope_open_units(
        &self,
        context: &ResolvedMemoryContext,
    ) -> impl Future<Output = Result<Vec<StoredMemoryUnit>, StoreError>> + Send;
    /// Transaction-scoped form of the complete open-scope compiler snapshot.
    fn fetch_scope_open_units_in_tx(
        &self,
        tx: &mut Self::Txn,
    ) -> impl Future<Output = Result<Vec<StoredMemoryUnit>, StoreError>> + Send;
    /// Complete context-bound file-sync transition state. Historical/deleted
    /// unit endpoints and the full edge set are required to preview native
    /// correction/forget cascades exactly.
    fn fetch_file_sync_transition_snapshot(
        &self,
        context: &ResolvedMemoryContext,
    ) -> impl Future<Output = Result<FileSyncTransitionSnapshot, StoreError>> + Send;
    /// Transaction-scoped re-read of the exact state protected by file-sync's
    /// transition fingerprint.
    fn fetch_file_sync_transition_snapshot_in_tx(
        &self,
        tx: &mut Self::Txn,
    ) -> impl Future<Output = Result<FileSyncTransitionSnapshot, StoreError>> + Send;
    /// The recall vector family: the nearest units to `query_vec` under the
    /// ACTIVE embedding profile, each with its cosine DISTANCE (pgvector `<=>`;
    /// the in-memory store returns `1 - cosine`). Core scores the vector
    /// channel as `1 - distance` and folds these units into the candidate
    /// union. Filtering by `profile_id` is mandatory — mixing embeddings across
    /// profiles/dimensions is incoherent (spec 03).
    fn fetch_vector_candidates(
        &self,
        context: &ResolvedMemoryContext,
        query_vec: &[f32],
        profile_id: Uuid,
        time: &RecallTime,
        limit: usize,
    ) -> impl Future<Output = Result<Vec<(StoredMemoryUnit, f32)>, StoreError>> + Send;
    fn fetch_units_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
    ) -> impl Future<Output = Result<Vec<StoredMemoryUnit>, StoreError>> + Send;
    fn fetch_edges(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> impl Future<Output = Result<Vec<StoredMemoryEdge>, StoreError>> + Send;
    fn fetch_record_material(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
        time: &RecallTime,
    ) -> impl Future<Output = Result<Vec<RecordMaterial>, StoreError>> + Send;
    fn fetch_review_events(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> impl Future<Output = Result<Vec<ReviewEventRow>, StoreError>> + Send;
    fn fetch_episodes_for_scope(
        &self,
        context: &ResolvedMemoryContext,
        limit: usize,
    ) -> impl Future<Output = Result<Vec<StoredEpisode>, StoreError>> + Send;
    fn pending_job_count(
        &self,
        context: &ResolvedMemoryContext,
    ) -> impl Future<Output = Result<usize, StoreError>> + Send;
    /// Count every queued or running reflect job visible to an unscoped fleet
    /// worker. Drain mode uses this to distinguish an empty queue from delayed
    /// retries that are temporarily ineligible for claiming.
    fn pending_worker_job_count(&self) -> impl Future<Output = Result<usize, StoreError>> + Send;
    fn fetch_episode(
        &self,
        context: &ResolvedMemoryContext,
        id: EpisodeId,
    ) -> impl Future<Output = Result<Option<StoredEpisode>, StoreError>> + Send;
    fn fetch_episodes_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[EpisodeId],
    ) -> impl Future<Output = Result<Vec<StoredEpisode>, StoreError>> + Send;
    fn fetch_resource(
        &self,
        context: &ResolvedMemoryContext,
        id: ResourceId,
    ) -> impl Future<Output = Result<Option<StoredResource>, StoreError>> + Send;
    fn fetch_resources_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[ResourceId],
    ) -> impl Future<Output = Result<Vec<StoredResource>, StoreError>> + Send;

    // Mutation seam.
    fn stage_correction(
        &self,
        tx: &mut Self::Txn,
        correction: CorrectionWrite,
    ) -> impl Future<Output = Result<CorrectOutcome, StoreError>> + Send;
    /// Close the open target as `Superseded` and append a bodyless `Invalidated`
    /// tombstone carrying the same stable identity plus a `Supersedes` edge.
    /// Returns `superseded = [target]`, `created = [tombstone]`.
    fn stage_invalidation(
        &self,
        tx: &mut Self::Txn,
        invalidation: InvalidationWrite,
    ) -> impl Future<Output = Result<CorrectOutcome, StoreError>> + Send;
    fn apply_correction(
        &self,
        context: &ResolvedMemoryContext,
        correction: CorrectionWrite,
    ) -> impl Future<Output = Result<CorrectOutcome, StoreError>> + Send {
        async move {
            let mut tx = self.begin(context).await?;
            let outcome = self.stage_correction(&mut tx, correction).await?;
            self.commit(tx).await?;
            Ok(outcome)
        }
    }
    fn stage_forget(
        &self,
        tx: &mut Self::Txn,
        forget: ForgetWrite,
    ) -> impl Future<Output = Result<ForgetOutcome, StoreError>> + Send;
    fn apply_forget(
        &self,
        context: &ResolvedMemoryContext,
        forget: ForgetWrite,
    ) -> impl Future<Output = Result<ForgetOutcome, StoreError>> + Send {
        async move {
            let mut tx = self.begin(context).await?;
            let outcome = self.stage_forget(&mut tx, forget).await?;
            self.commit(tx).await?;
            Ok(outcome)
        }
    }
    fn stage_review_events(
        &self,
        tx: &mut Self::Txn,
        events: Vec<ReviewEventRow>,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    fn record_review_events(
        &self,
        context: &ResolvedMemoryContext,
        events: Vec<ReviewEventRow>,
    ) -> impl Future<Output = Result<(), StoreError>> + Send {
        async move {
            let mut tx = self.begin(context).await?;
            self.stage_review_events(&mut tx, events).await?;
            self.commit(tx).await
        }
    }
    /// Apply the anti-poisoning cross-check's decisions to existing captured
    /// units: set `state`, `confidence`, and `payload.capture` for each named
    /// unit, context-scoped exactly like the compiled `unit_updates` path. This
    /// is the WRITE side of the trust ladder; the decisions are computed by the
    /// pure `compute_capture_crosscheck` over the WRITE SEAM snapshot, never a
    /// bounded recall pool (store-divergence rule).
    fn apply_capture_transitions(
        &self,
        context: &ResolvedMemoryContext,
        transitions: Vec<CaptureTransition>,
    ) -> impl Future<Output = Result<CaptureCrossCheckReport, StoreError>> + Send;
    fn store_trace(
        &self,
        context: &ResolvedMemoryContext,
        trace: RetrievalTrace,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    /// TENANT-BOUND: a trace id from another tenant resolves to `None`.
    fn trace_by_id(
        &self,
        context: &ResolvedMemoryContext,
        id: TraceId,
    ) -> impl Future<Output = Result<Option<RetrievalTrace>, StoreError>> + Send;
    fn scope_memory_page(
        &self,
        context: &ResolvedMemoryContext,
        cursor: Option<UnitId>,
        limit: usize,
    ) -> impl Future<Output = Result<ScopePage, StoreError>> + Send;
    /// One complete bitemporally-current file-projection snapshot, evaluated
    /// at the caller-supplied RFC3339 instant. This is deliberately separate
    /// from the historical, paginated scope-memory export.
    fn canonical_projection_units(
        &self,
        context: &ResolvedMemoryContext,
        evaluated_at: &str,
    ) -> impl Future<Output = Result<Vec<StoredMemoryUnit>, StoreError>> + Send;
    /// The canonical projection read from the same transaction that owns a
    /// file-sync claim and its staged writes.
    fn canonical_projection_units_in_tx(
        &self,
        tx: &mut Self::Txn,
        evaluated_at: &str,
    ) -> impl Future<Output = Result<Vec<StoredMemoryUnit>, StoreError>> + Send;

    // Reflect job queue (SKIP LOCKED semantics in Postgres).
    fn claim_reflect_jobs(
        &self,
        filter: JobFilter,
        limit: usize,
    ) -> impl Future<Output = Result<Vec<ReflectJobRow>, StoreError>> + Send;
    fn complete_reflect_job(
        &self,
        claim: &ReflectJobRow,
    ) -> impl Future<Output = Result<ClaimMutationOutcome, StoreError>> + Send;
    /// Atomically invalidates a stale compiler claim and makes the same source
    /// job claimable under the worker's live compiler identity.
    fn requeue_reflect_job_with_compiler(
        &self,
        claim: &ReflectJobRow,
        compiler_version: &str,
    ) -> impl Future<Output = Result<ClaimMutationOutcome, StoreError>> + Send;
    fn fetch_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
        expected_batches: &[StructuredStateRequest],
        provider_identity: &StructuredStateProviderIdentity,
    ) -> impl Future<Output = Result<Option<Vec<StructuredExtractionPacket>>, StoreError>> + Send;
    fn store_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
        expected_batches: &[StructuredStateRequest],
        provider_identity: &StructuredStateProviderIdentity,
        extraction_packets: Vec<StructuredExtractionPacket>,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    fn release_reflect_job(
        &self,
        claim: &ReflectJobRow,
        retry_after_seconds: u64,
        error: String,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    fn fail_reflect_job(
        &self,
        claim: &ReflectJobRow,
        error: String,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    /// Persists one reflect compilation. MUST consult forgotten-source
    /// tombstones and refuse re-derivation of units from forgotten sources.
    fn stage_compiled_units(
        &self,
        tx: &mut Self::Txn,
        claim: Option<&ReflectJobRow>,
        write: CompiledWrite,
    ) -> impl Future<Output = Result<ClaimMutationOutcome, StoreError>> + Send;
    fn persist_compiled_units(
        &self,
        context: &ResolvedMemoryContext,
        claim: Option<&ReflectJobRow>,
        write: CompiledWrite,
    ) -> impl Future<Output = Result<ClaimMutationOutcome, StoreError>> + Send {
        async move {
            let mut tx = self.begin(context).await?;
            let outcome = self.stage_compiled_units(&mut tx, claim, write).await?;
            if outcome == ClaimMutationOutcome::Stale {
                return Ok(outcome);
            }
            self.commit(tx).await?;
            Ok(outcome)
        }
    }
    /// Idempotency lookup for reflect compilations keyed by
    /// (job_id, compiler_version).
    fn fetch_reflect_trace(
        &self,
        context: &ResolvedMemoryContext,
        job_id: JobId,
        compiler_version: &str,
    ) -> impl Future<Output = Result<Option<ReflectTrace>, StoreError>> + Send;

    fn upsert_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        rows: Vec<EmbeddingRow>,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    /// Idempotently seeds the `embedding_profile` row every embedding FKs.
    fn upsert_embedding_profile(
        &self,
        tenant: TenantId,
        profile: EmbeddingProfileRow,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    /// Embedding rows for the given units (all profiles).
    fn fetch_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
    ) -> impl Future<Output = Result<Vec<EmbeddingRow>, StoreError>> + Send;
    fn lookup_api_key(
        &self,
        key_hash: &str,
    ) -> impl Future<Output = Result<Option<ApiKeyRow>, StoreError>> + Send;
    fn resolve_context_binding(
        &self,
        tenant: TenantId,
        client_ref: String,
        request: ContextBindingRequest,
    ) -> impl Future<Output = Result<ContextBindingResponse, StoreError>> + Send;
    fn resolve_memory_context(
        &self,
        tenant: TenantId,
        subject_id: SubjectId,
        actor_id: ActorId,
        scope_id: ScopeId,
        agent_node_id: AgentNodeId,
    ) -> impl Future<Output = Result<ResolvedMemoryContext, StoreError>> + Send;

    /// Backend readiness probe. Postgres verifies the required schema
    /// compatibility floor; the in-memory store is always ready.
    fn ping(&self) -> impl Future<Output = Result<(), StoreError>> + Send;
    /// Reflect jobs dead-lettered after exhausting their claim attempts.
    fn dead_letter_count(&self) -> impl Future<Output = Result<u64, StoreError>> + Send;
}

/// Capability seam for stores that can atomically commit an idempotency claim,
/// its successful response, and the mutation's staged writes.
pub trait MutationLedgerStore: MemoryStore {
    /// Looks up an exact, committed mutation receipt without opening an
    /// execution transaction or acquiring the idempotency claim lock.
    fn lookup_mutation_replay(
        &self,
        context: &ResolvedMemoryContext,
        claim: &MutationClaim,
    ) -> impl Future<Output = Result<Option<MutationResponse>, StoreError>> + Send;
    fn stage_mutation_claim(
        &self,
        tx: &mut Self::Txn,
        claim: MutationClaim,
    ) -> impl Future<Output = Result<MutationClaimOutcome, StoreError>> + Send;
    fn stage_mutation_response(
        &self,
        tx: &mut Self::Txn,
        response: MutationResponse,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    fn stage_subject_erasure(
        &self,
        tx: &mut Self::Txn,
    ) -> impl Future<Output = Result<SubjectErasureReceipt, StoreError>> + Send;
    fn stage_task_outcome(
        &self,
        tx: &mut Self::Txn,
        outcome: TaskOutcomeRow,
        events: Vec<TaskMemoryEventRow>,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
    fn stage_task_memory_events(
        &self,
        tx: &mut Self::Txn,
        events: Vec<TaskMemoryEventRow>,
    ) -> impl Future<Output = Result<(), StoreError>> + Send;
}

#[derive(Clone, Default)]
pub struct InMemoryStore {
    inner: Arc<Mutex<InMemoryState>>,
    mutation_locks: Arc<Mutex<HashMap<MutationLockKey, Weak<AsyncMutex<()>>>>>,
    #[cfg(any(test, feature = "test-support"))]
    fail_next_mutation_response: Arc<std::sync::atomic::AtomicBool>,
    #[cfg(test)]
    deep_snapshot_reads: Arc<std::sync::atomic::AtomicUsize>,
    #[cfg(test)]
    citation_source_batch_reads: Arc<std::sync::atomic::AtomicUsize>,
}

type MutationLockKey = (TenantId, MutationVerb, [u8; 32]);

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct JobMeta {
    attempts: u32,
    claim_generation: u64,
    claimed: bool,
    completed: bool,
    terminal: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum SourceKindKey {
    Episode,
    Resource,
    MemoryUnit,
}

#[derive(Clone, PartialEq, Eq)]
struct MutationLedgerEntry {
    claim: MutationClaim,
    response: MutationResponse,
    expires_at_second: i64,
}

struct StagedMutation {
    claim: MutationClaim,
    response: Option<MutationResponse>,
    replay: bool,
}

#[derive(Clone, PartialEq, Eq)]
struct SubjectTombstone {
    generation: u64,
    erased_at: String,
}

/// `(tenant, job, compiler_version)` key identifying a reflect trace owner.
type ReflectOwnerKey = (TenantId, JobId, String);
/// The resolved context that owns a reflect trace.
type ReflectOwnerValue = (SubjectId, u64, ScopeId, AgentNodeId, ActorId);

#[derive(Clone, Default)]
struct InMemoryState {
    episodes: HashMap<TenantId, Vec<StoredEpisode>>,
    resources: HashMap<TenantId, Vec<StoredResource>>,
    memory_units: HashMap<TenantId, Vec<StoredMemoryUnit>>,
    memory_edges: HashMap<TenantId, Vec<StoredMemoryEdge>>,
    citations: HashMap<TenantId, Vec<StoredCitation>>,
    reflect_jobs: HashMap<TenantId, Vec<QueuedReflectJob>>,
    reflect_traces: HashMap<TenantId, Vec<ReflectTrace>>,
    reflect_trace_owners: HashMap<ReflectOwnerKey, ReflectOwnerValue>,
    retrieval_traces: HashMap<TenantId, Vec<RetrievalTrace>>,
    review_events: HashMap<TenantId, Vec<ReviewEvent>>,
    task_outcomes: HashMap<TenantId, Vec<TaskOutcomeRow>>,
    task_memory_events: HashMap<TenantId, Vec<TaskMemoryEventRow>>,
    forgotten_sources: HashSet<(
        TenantId,
        SubjectId,
        u64,
        ScopeId,
        AgentNodeId,
        SourceKindKey,
        Uuid,
    )>,
    api_keys: Vec<ApiKeyRow>,
    context_bindings: HashMap<(TenantId, String), (ContextBindingRequest, ContextBindingResponse)>,
    embeddings: HashMap<TenantId, Vec<EmbeddingRow>>,
    embedding_profiles: HashMap<TenantId, Vec<EmbeddingProfileRow>>,
    job_meta: HashMap<JobId, JobMeta>,
    prepared_structured_state: HashMap<(TenantId, JobId), ReflectJobResult>,
    mutation_ledger: HashMap<(TenantId, MutationVerb, String), MutationLedgerEntry>,
    subject_tombstones: HashMap<(TenantId, SubjectId), SubjectTombstone>,
    deletion_generation: HashMap<(TenantId, SubjectId), u64>,
}

#[derive(Clone, PartialEq)]
struct InMemoryContextState {
    episodes: Vec<StoredEpisode>,
    resources: Vec<StoredResource>,
    units: Vec<StoredMemoryUnit>,
    edges: Vec<StoredMemoryEdge>,
    citations: Vec<StoredCitation>,
    jobs: Vec<QueuedReflectJob>,
    reflect_traces: Vec<ReflectTrace>,
    reflect_owners: Vec<(ReflectOwnerKey, ReflectOwnerValue)>,
    retrieval_traces: Vec<RetrievalTrace>,
    review_events: Vec<ReviewEvent>,
    forgotten_sources: Vec<(
        TenantId,
        SubjectId,
        u64,
        ScopeId,
        AgentNodeId,
        SourceKindKey,
        Uuid,
    )>,
    embeddings: Vec<EmbeddingRow>,
    embedding_profiles: Vec<EmbeddingProfileRow>,
    job_meta: Vec<(JobId, JobMeta)>,
    prepared: Vec<((TenantId, JobId), ReflectJobResult)>,
    deletion_generation: u64,
}

impl InMemoryContextState {
    fn capture(state: &InMemoryState, context: &ResolvedMemoryContext) -> Self {
        let owned_unit = |unit: &&StoredMemoryUnit| unit_matches_context(unit, context);
        let units: Vec<_> = state
            .memory_units
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(owned_unit)
            .cloned()
            .collect();
        let unit_ids: HashSet<_> = units.iter().map(|unit| unit.id).collect();
        let owned_job = |job: &&QueuedReflectJob| {
            job.tenant_id == context.tenant_id
                && job.data_subject_id == context.data_subject_id
                && job.subject_generation == context.subject_generation
                && job.scope_id == context.scope_id
                && job.agent_node_id == context.agent_node_id
                && job.actor_id == context.actor_id
        };
        let jobs: Vec<_> = state
            .reflect_jobs
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(owned_job)
            .cloned()
            .collect();
        let mut job_ids: HashSet<_> = jobs.iter().map(|job| job.id).collect();
        let owner = (
            context.data_subject_id,
            context.subject_generation,
            context.scope_id,
            context.agent_node_id,
            context.actor_id,
        );
        let mut reflect_owners: Vec<_> = state
            .reflect_trace_owners
            .iter()
            .filter(|((tenant, _, _), value)| *tenant == context.tenant_id && **value == owner)
            .map(|(key, value)| (key.clone(), *value))
            .collect();
        reflect_owners.sort_by_key(|(key, _)| (key.1.as_uuid(), key.2.clone()));
        job_ids.extend(reflect_owners.iter().map(|(key, _)| key.1));
        let retrieval_traces: Vec<_> = state
            .retrieval_traces
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|trace| {
                trace.data_subject_id == context.data_subject_id
                    && trace.subject_generation == context.subject_generation
                    && trace.scope_id == context.scope_id
                    && trace.agent_node_id == context.agent_node_id
                    && trace.actor_id == context.actor_id
            })
            .cloned()
            .collect();
        let trace_ids: HashSet<_> = retrieval_traces.iter().map(|trace| trace.id).collect();
        let mut job_meta: Vec<_> = state
            .job_meta
            .iter()
            .filter(|(id, _)| job_ids.contains(id))
            .map(|(id, meta)| (*id, *meta))
            .collect();
        job_meta.sort_by_key(|(id, _)| id.as_uuid());
        let mut prepared: Vec<_> = state
            .prepared_structured_state
            .iter()
            .filter(|((tenant, id), _)| *tenant == context.tenant_id && job_ids.contains(id))
            .map(|(key, value)| (*key, value.clone()))
            .collect();
        prepared.sort_by_key(|(key, _)| key.1.as_uuid());
        Self {
            episodes: state
                .episodes
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .filter(|row| {
                    row.data_subject_id == context.data_subject_id
                        && row.subject_generation == context.subject_generation
                        && row.scope_id == context.scope_id
                        && row.agent_node_id == context.agent_node_id
                        && row.actor_id == context.actor_id
                })
                .cloned()
                .collect(),
            resources: state
                .resources
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .filter(|row| {
                    row.data_subject_id == context.data_subject_id
                        && row.subject_generation == context.subject_generation
                        && row.scope_id == context.scope_id
                        && row.agent_node_id == context.agent_node_id
                        && row.actor_id == context.actor_id
                })
                .cloned()
                .collect(),
            edges: state
                .memory_edges
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .filter(|edge| unit_ids.contains(&edge.src_id) && unit_ids.contains(&edge.dst_id))
                .cloned()
                .collect(),
            citations: state
                .citations
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .filter(|citation| unit_ids.contains(&citation.memory_unit_id))
                .cloned()
                .collect(),
            reflect_traces: state
                .reflect_traces
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .filter(|trace| job_ids.contains(&trace.job_id))
                .cloned()
                .collect(),
            review_events: state
                .review_events
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .filter(|event| trace_ids.contains(&event.trace_id))
                .cloned()
                .collect(),
            forgotten_sources: state
                .forgotten_sources
                .iter()
                .filter(|entry| {
                    entry.0 == context.tenant_id
                        && entry.1 == context.data_subject_id
                        && entry.2 == context.subject_generation
                        && entry.3 == context.scope_id
                        && entry.4 == context.agent_node_id
                })
                .cloned()
                .collect(),
            embeddings: state
                .embeddings
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .filter(|row| unit_ids.contains(&row.memory_unit_id))
                .cloned()
                .collect(),
            embedding_profiles: state
                .embedding_profiles
                .get(&context.tenant_id)
                .cloned()
                .unwrap_or_default(),
            deletion_generation: state
                .deletion_generation
                .get(&(context.tenant_id, context.data_subject_id))
                .copied()
                .unwrap_or_default(),
            units,
            jobs,
            reflect_owners,
            retrieval_traces,
            job_meta,
            prepared,
        }
    }

    fn into_scratch(
        self,
        source: &InMemoryState,
        context: &ResolvedMemoryContext,
    ) -> InMemoryState {
        let mut state = InMemoryState::default();
        state.episodes.insert(context.tenant_id, self.episodes);
        state.resources.insert(context.tenant_id, self.resources);
        state.memory_units.insert(context.tenant_id, self.units);
        state.memory_edges.insert(context.tenant_id, self.edges);
        state.citations.insert(context.tenant_id, self.citations);
        state.reflect_jobs.insert(context.tenant_id, self.jobs);
        state
            .reflect_traces
            .insert(context.tenant_id, self.reflect_traces);
        state.reflect_trace_owners.extend(self.reflect_owners);
        state
            .retrieval_traces
            .insert(context.tenant_id, self.retrieval_traces);
        state
            .review_events
            .insert(context.tenant_id, self.review_events);
        state.forgotten_sources.extend(self.forgotten_sources);
        state.embeddings.insert(context.tenant_id, self.embeddings);
        state
            .embedding_profiles
            .insert(context.tenant_id, self.embedding_profiles);
        state.job_meta.extend(self.job_meta);
        state.prepared_structured_state.extend(self.prepared);
        state.deletion_generation.insert(
            (context.tenant_id, context.data_subject_id),
            self.deletion_generation,
        );
        state.context_bindings.extend(
            source
                .context_bindings
                .iter()
                .filter(|((tenant, _), (_, binding))| {
                    *tenant == context.tenant_id && binding.subject_id == context.data_subject_id
                })
                .map(|(key, value)| (key.clone(), value.clone())),
        );
        state
    }

    fn replace_in(self, state: &mut InMemoryState, context: &ResolvedMemoryContext) {
        let old = Self::capture(state, context);
        let old_units: HashSet<_> = old.units.iter().map(|unit| unit.id).collect();
        let old_jobs: HashSet<_> = old
            .jobs
            .iter()
            .map(|job| job.id)
            .chain(old.reflect_owners.iter().map(|(key, _)| key.1))
            .collect();
        let old_traces: HashSet<_> = old.retrieval_traces.iter().map(|trace| trace.id).collect();
        state
            .episodes
            .entry(context.tenant_id)
            .or_default()
            .retain(|row| !old.episodes.iter().any(|owned| owned.id == row.id));
        state
            .episodes
            .entry(context.tenant_id)
            .or_default()
            .extend(self.episodes);
        state
            .resources
            .entry(context.tenant_id)
            .or_default()
            .retain(|row| !old.resources.iter().any(|owned| owned.id == row.id));
        state
            .resources
            .entry(context.tenant_id)
            .or_default()
            .extend(self.resources);
        state
            .memory_units
            .entry(context.tenant_id)
            .or_default()
            .retain(|row| !old_units.contains(&row.id));
        state
            .memory_units
            .entry(context.tenant_id)
            .or_default()
            .extend(self.units);
        state
            .memory_edges
            .entry(context.tenant_id)
            .or_default()
            .retain(|edge| !old_units.contains(&edge.src_id) || !old_units.contains(&edge.dst_id));
        state
            .memory_edges
            .entry(context.tenant_id)
            .or_default()
            .extend(self.edges);
        state
            .citations
            .entry(context.tenant_id)
            .or_default()
            .retain(|citation| !old_units.contains(&citation.memory_unit_id));
        state
            .citations
            .entry(context.tenant_id)
            .or_default()
            .extend(self.citations);
        state
            .reflect_jobs
            .entry(context.tenant_id)
            .or_default()
            .retain(|job| !old_jobs.contains(&job.id));
        state
            .reflect_jobs
            .entry(context.tenant_id)
            .or_default()
            .extend(self.jobs);
        state
            .reflect_traces
            .entry(context.tenant_id)
            .or_default()
            .retain(|trace| !old_jobs.contains(&trace.job_id));
        state
            .reflect_traces
            .entry(context.tenant_id)
            .or_default()
            .extend(self.reflect_traces);
        state
            .reflect_trace_owners
            .retain(|key, _| !old_jobs.contains(&key.1));
        state.reflect_trace_owners.extend(self.reflect_owners);
        state
            .retrieval_traces
            .entry(context.tenant_id)
            .or_default()
            .retain(|trace| !old_traces.contains(&trace.id));
        state
            .retrieval_traces
            .entry(context.tenant_id)
            .or_default()
            .extend(self.retrieval_traces);
        state
            .review_events
            .entry(context.tenant_id)
            .or_default()
            .retain(|event| !old_traces.contains(&event.trace_id));
        state
            .review_events
            .entry(context.tenant_id)
            .or_default()
            .extend(self.review_events);
        state.forgotten_sources.retain(|entry| {
            !(entry.0 == context.tenant_id
                && entry.1 == context.data_subject_id
                && entry.2 == context.subject_generation
                && entry.3 == context.scope_id
                && entry.4 == context.agent_node_id)
        });
        state.forgotten_sources.extend(self.forgotten_sources);
        state
            .embeddings
            .entry(context.tenant_id)
            .or_default()
            .retain(|row| !old_units.contains(&row.memory_unit_id));
        state
            .embeddings
            .entry(context.tenant_id)
            .or_default()
            .extend(self.embeddings);
        let profiles = state
            .embedding_profiles
            .entry(context.tenant_id)
            .or_default();
        for profile in self.embedding_profiles {
            if !profiles.iter().any(|row| row.id == profile.id) {
                profiles.push(profile);
            }
        }
        state.job_meta.retain(|id, _| !old_jobs.contains(id));
        state.job_meta.extend(self.job_meta);
        state
            .prepared_structured_state
            .retain(|(_, id), _| !old_jobs.contains(id));
        state.prepared_structured_state.extend(self.prepared);
        state.deletion_generation.insert(
            (context.tenant_id, context.data_subject_id),
            self.deletion_generation,
        );
    }
}

impl InMemoryState {
    fn validate_context(&self, context: &ResolvedMemoryContext) -> Result<(), StoreError> {
        if self
            .subject_tombstones
            .contains_key(&(context.tenant_id, context.data_subject_id))
        {
            return Err(StoreError::SubjectErased);
        }
        let current = self
            .context_bindings
            .iter()
            .find(|((tenant, _), (_, binding))| {
                *tenant == context.tenant_id
                    && binding.subject_id == context.data_subject_id
                    && binding.actor_id == context.actor_id
                    && binding.scope_id == context.scope_id
                    && binding.agent_node_id == context.agent_node_id
            })
            .map(|(_, (_, binding))| binding)
            .ok_or(StoreError::NotFound("memory context"))?;
        if current.subject_generation != context.subject_generation {
            return Err(StoreError::StaleSubjectGeneration);
        }
        Ok(())
    }

    fn claim_is_current(&self, claim: &ReflectJobRow) -> bool {
        self.reflect_jobs
            .get(&claim.job.tenant_id)
            .is_some_and(|jobs| jobs.contains(&claim.job))
            && self.job_meta.get(&claim.job.id).is_some_and(|meta| {
                meta.claimed
                    && meta.attempts == claim.attempts
                    && meta.claim_generation == claim.claim_generation
                    && !meta.completed
                    && !meta.terminal
            })
    }

    fn is_forgotten_source(&self, unit: &StoredMemoryUnit) -> bool {
        let owner = (
            unit.tenant_id,
            unit.data_subject_id,
            unit.subject_generation,
            unit.scope_id,
            unit.agent_node_id,
        );
        if let Some(episode_id) = unit.source_episode_id
            && self.forgotten_sources.contains(&(
                owner.0,
                owner.1,
                owner.2,
                owner.3,
                owner.4,
                SourceKindKey::Episode,
                episode_id.as_uuid(),
            ))
        {
            return true;
        }
        if let Some(resource_id) = unit.source_resource_id
            && self.forgotten_sources.contains(&(
                owner.0,
                owner.1,
                owner.2,
                owner.3,
                owner.4,
                SourceKindKey::Resource,
                resource_id.as_uuid(),
            ))
        {
            return true;
        }
        self.forgotten_sources.contains(&(
            owner.0,
            owner.1,
            owner.2,
            owner.3,
            owner.4,
            SourceKindKey::MemoryUnit,
            unit.id.as_uuid(),
        ))
    }

    fn erase_subject(
        &mut self,
        tenant: TenantId,
        subject: SubjectId,
        receipt: &SubjectErasureReceipt,
    ) {
        let erased_unit_ids: HashSet<_> = self
            .memory_units
            .get(&tenant)
            .into_iter()
            .flatten()
            .filter(|unit| unit.data_subject_id == subject)
            .map(|unit| unit.id)
            .collect();
        let erased_job_ids: HashSet<_> = self
            .reflect_jobs
            .get(&tenant)
            .into_iter()
            .flatten()
            .filter(|job| job.data_subject_id == subject)
            .map(|job| job.id)
            .chain(
                self.reflect_trace_owners
                    .iter()
                    .filter(|((owner_tenant, _, _), owner)| {
                        *owner_tenant == tenant && owner.0 == subject
                    })
                    .map(|((_, job_id, _), _)| *job_id),
            )
            .collect();
        let erased_trace_ids: HashSet<_> = self
            .retrieval_traces
            .get(&tenant)
            .into_iter()
            .flatten()
            .filter(|trace| trace.data_subject_id == subject)
            .map(|trace| trace.id)
            .collect();

        self.context_bindings
            .retain(|(key_tenant, _), (_, binding)| {
                *key_tenant != tenant || binding.subject_id != subject
            });
        self.api_keys
            .retain(|key| key.tenant_id != tenant || key.data_subject_id != Some(subject));
        self.episodes
            .entry(tenant)
            .or_default()
            .retain(|episode| episode.data_subject_id != subject);
        self.resources
            .entry(tenant)
            .or_default()
            .retain(|resource| resource.data_subject_id != subject);
        self.memory_units
            .entry(tenant)
            .or_default()
            .retain(|unit| unit.data_subject_id != subject);
        self.memory_edges.entry(tenant).or_default().retain(|edge| {
            !erased_unit_ids.contains(&edge.src_id) && !erased_unit_ids.contains(&edge.dst_id)
        });
        self.citations
            .entry(tenant)
            .or_default()
            .retain(|citation| !erased_unit_ids.contains(&citation.memory_unit_id));
        self.embeddings
            .entry(tenant)
            .or_default()
            .retain(|row| !erased_unit_ids.contains(&row.memory_unit_id));
        self.reflect_jobs
            .entry(tenant)
            .or_default()
            .retain(|job| job.data_subject_id != subject);
        self.reflect_traces
            .entry(tenant)
            .or_default()
            .retain(|trace| !erased_job_ids.contains(&trace.job_id));
        self.reflect_trace_owners
            .retain(|(owner_tenant, _, _), owner| *owner_tenant != tenant || owner.0 != subject);
        self.retrieval_traces
            .entry(tenant)
            .or_default()
            .retain(|trace| trace.data_subject_id != subject);
        self.review_events
            .entry(tenant)
            .or_default()
            .retain(|event| !erased_trace_ids.contains(&event.trace_id));
        self.task_outcomes
            .entry(tenant)
            .or_default()
            .retain(|outcome| outcome.data_subject_id != subject);
        self.task_memory_events
            .entry(tenant)
            .or_default()
            .retain(|event| event.data_subject_id != subject);
        self.forgotten_sources
            .retain(|entry| entry.0 != tenant || entry.1 != subject);
        self.prepared_structured_state
            .retain(|(owner_tenant, job_id), _| {
                *owner_tenant != tenant || !erased_job_ids.contains(job_id)
            });
        self.job_meta
            .retain(|job_id, _| !erased_job_ids.contains(job_id));
        self.mutation_ledger.retain(|_, entry| {
            entry.claim.tenant_id != tenant || entry.claim.data_subject_id != subject
        });
        self.subject_tombstones.insert(
            (tenant, subject),
            SubjectTombstone {
                generation: receipt.generation,
                erased_at: receipt.erased_at.clone(),
            },
        );
    }
}

fn in_memory_binding_by_external_pair<'a>(
    state: &'a InMemoryState,
    tenant: TenantId,
    subject_external_ref: &str,
    scope_external_ref: &str,
    agent_external_ref: &str,
) -> Option<&'a (ContextBindingRequest, ContextBindingResponse)> {
    state
        .context_bindings
        .iter()
        .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
        .map(|(_, binding)| binding)
        .find(|(bound, _)| {
            bound.subject.external_ref == subject_external_ref
                && bound.scope.external_ref == scope_external_ref
                && bound.agent_node.external_ref == agent_external_ref
        })
}

fn in_memory_scope_is_strict_ancestor(
    state: &InMemoryState,
    tenant: TenantId,
    subject_external_ref: &str,
    ancestor_external_ref: &str,
    request: &ContextBindingRequest,
) -> bool {
    let mut parent = request.scope.parent_external_ref.as_deref();
    while let Some(parent_ref) = parent {
        if parent_ref == ancestor_external_ref {
            return true;
        }
        parent = state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
            .map(|(_, binding)| binding)
            .find(|(bound, _)| {
                bound.subject.external_ref == subject_external_ref
                    && bound.scope.external_ref == parent_ref
            })
            .and_then(|(bound, _)| bound.scope.parent_external_ref.as_deref());
    }
    false
}

fn validate_in_memory_access_policies(
    state: &InMemoryState,
    tenant: TenantId,
    request: &ContextBindingRequest,
    agent_level: u8,
) -> Result<(), StoreError> {
    let mut seen = HashSet::new();
    for policy in &request.access_policies {
        let key = (
            policy.source_scope_external_ref().to_string(),
            policy.source_agent_node_external_ref().to_string(),
            policy.kind(),
        );
        if !seen.insert(key) {
            return Err(StoreError::Conflict("duplicate access policy".to_string()));
        }
        let kind = policy.kind();
        if !agent_level_allows_memory_kind(agent_level, kind) {
            return Err(StoreError::Conflict(
                "memory kind is not allowed for the grantee agent level".to_string(),
            ));
        }
        let source_scope = policy.source_scope_external_ref();
        let source_agent = policy.source_agent_node_external_ref();
        in_memory_binding_by_external_pair(
            state,
            tenant,
            &request.subject.external_ref,
            source_scope,
            source_agent,
        )
        .ok_or(StoreError::NotFound("access policy source context"))?;

        let source_is_grantee = source_scope == request.scope.external_ref
            && source_agent == request.agent_node.external_ref;
        match policy {
            ContextBindingAccessPolicy::Inherit { .. } => {
                if agent_level != 0
                    || !matches!(
                        kind,
                        MemoryKind::Episodic | MemoryKind::Semantic | MemoryKind::Belief
                    )
                    || !in_memory_scope_is_strict_ancestor(
                        state,
                        tenant,
                        &request.subject.external_ref,
                        source_scope,
                        request,
                    )
                {
                    return Err(StoreError::Conflict(
                        "inherit source must be a strict same-subject ancestor root-memory context"
                            .to_string(),
                    ));
                }
            }
            ContextBindingAccessPolicy::Grant { .. } if source_is_grantee => {
                return Err(StoreError::Conflict(
                    "grant source must differ from the grantee context".to_string(),
                ));
            }
            ContextBindingAccessPolicy::Grant { .. } => {}
        }
    }
    Ok(())
}

pub struct InMemoryTxn {
    context: ResolvedMemoryContext,
    episodes: Vec<StoredEpisode>,
    episode_observation_updates: Vec<(TenantId, EpisodeId, String)>,
    resources: Vec<StoredResource>,
    memory_units: Vec<StoredMemoryUnit>,
    memory_edges: Vec<StoredMemoryEdge>,
    reflect_jobs: Vec<QueuedReflectJob>,
    task_outcomes: Vec<TaskOutcomeRow>,
    task_memory_events: Vec<TaskMemoryEventRow>,
    state_snapshot: Option<(InMemoryContextState, Box<InMemoryState>)>,
    mutation: Option<StagedMutation>,
    subject_erasure: Option<SubjectErasureReceipt>,
    mutation_guard: Option<OwnedMutexGuard<()>>,
    transaction_time: String,
    committed: bool,
}

impl InMemoryStore {
    /// Test-only in-process fault injection for asserting rollback after
    /// mutation writes are staged. This has no server or other remote trigger.
    #[doc(hidden)]
    #[cfg(any(test, feature = "test-support"))]
    pub fn fail_next_mutation_response(&self) {
        self.fail_next_mutation_response
            .store(true, std::sync::atomic::Ordering::SeqCst);
    }

    #[cfg(test)]
    fn deep_snapshot_read_count(&self) -> usize {
        self.deep_snapshot_reads
            .load(std::sync::atomic::Ordering::SeqCst)
    }

    /// Test seam: register a context binding that exactly matches a hand-built
    /// [`ResolvedMemoryContext`] so the store's strict context contract
    /// (canonical cutover — `validate_context` on commit/recall) accepts it.
    /// Production code always reaches a real binding via `resolve_context_binding`;
    /// low-level store tests build contexts directly, so this seeds the one
    /// binding those ids resolve against. Idempotent per (tenant, subject,
    /// scope, actor).
    #[doc(hidden)]
    pub fn seed_context_binding(&self, context: &ResolvedMemoryContext) {
        let mut state = self
            .inner
            .lock()
            .expect("seed_context_binding: state lock poisoned");
        let tag = format!(
            "seed:{}:{}:{}:{}",
            context.data_subject_id.as_uuid(),
            context.scope_id.as_uuid(),
            context.actor_id.as_uuid(),
            context.agent_node_id.as_uuid()
        );
        let request = ContextBindingRequest {
            subject: memphant_types::ContextBindingEntityRef {
                external_ref: format!("subject:{tag}"),
                kind: "user".to_string(),
            },
            actor: memphant_types::ContextBindingEntityRef {
                external_ref: format!("actor:{tag}"),
                kind: "user".to_string(),
            },
            scope: memphant_types::ContextBindingScopeRef {
                external_ref: format!("scope:{tag}"),
                kind: "user_root".to_string(),
                parent_external_ref: None,
            },
            agent_node: memphant_types::ContextBindingAgentRef {
                external_ref: format!("agent:{tag}"),
                parent_external_ref: None,
            },
            access_policies: Vec::new(),
        };
        let response = ContextBindingResponse {
            subject_id: context.data_subject_id,
            actor_id: context.actor_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            agent_level: context.agent_level,
            policy_revision: context.policy_revision.clone(),
            subject_generation: context.subject_generation,
        };
        state
            .context_bindings
            .entry((context.tenant_id, tag))
            .or_insert((request, response));
    }

    fn staged_state<'a>(
        &self,
        tx: &'a mut InMemoryTxn,
    ) -> Result<&'a mut InMemoryState, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        if tx.state_snapshot.is_none() {
            let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
            state.validate_context(&tx.context)?;
            let baseline = InMemoryContextState::capture(&state, &tx.context);
            let scratch = baseline.clone().into_scratch(&state, &tx.context);
            tx.state_snapshot = Some((baseline, Box::new(scratch)));
        }
        Ok(tx
            .state_snapshot
            .as_mut()
            .expect("snapshot initialized")
            .1
            .as_mut())
    }

    fn prune_mutation_locks(&self) {
        if let Ok(mut locks) = self.mutation_locks.lock() {
            locks.retain(|_, lock| lock.strong_count() > 0);
        }
    }

    /// Starts an in-memory transaction at a caller-supplied time.
    ///
    /// Deterministic evals use this instead of inheriting the host wall clock.
    pub fn begin_at(&self, context: &ResolvedMemoryContext, clock: &impl Clock) -> InMemoryTxn {
        InMemoryTxn {
            context: context.clone(),
            episodes: Vec::new(),
            episode_observation_updates: Vec::new(),
            resources: Vec::new(),
            memory_units: Vec::new(),
            memory_edges: Vec::new(),
            reflect_jobs: Vec::new(),
            task_outcomes: Vec::new(),
            task_memory_events: Vec::new(),
            state_snapshot: None,
            mutation: None,
            subject_erasure: None,
            mutation_guard: None,
            transaction_time: clock.now_rfc3339(),
            committed: false,
        }
    }

    pub fn episodes(&self, tenant_id: TenantId) -> Vec<StoredEpisode> {
        self.inner
            .lock()
            .map(|state| state.episodes.get(&tenant_id).cloned().unwrap_or_default())
            .unwrap_or_default()
    }

    pub fn memory_units(&self, tenant_id: TenantId) -> Vec<StoredMemoryUnit> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .memory_units
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }

    pub fn task_outcomes(&self, tenant_id: TenantId) -> Vec<TaskOutcomeRow> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .task_outcomes
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }

    pub fn task_memory_events(&self, tenant_id: TenantId) -> Vec<TaskMemoryEventRow> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .task_memory_events
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }

    pub fn resources(&self, tenant_id: TenantId) -> Vec<StoredResource> {
        self.inner
            .lock()
            .map(|state| state.resources.get(&tenant_id).cloned().unwrap_or_default())
            .unwrap_or_default()
    }

    pub fn reflect_jobs(&self, tenant_id: TenantId) -> Vec<QueuedReflectJob> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .reflect_jobs
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }

    pub fn active_semantic_units(&self, tenant_id: TenantId) -> Vec<StoredMemoryUnit> {
        self.memory_units(tenant_id)
            .into_iter()
            .filter(|unit| unit.kind == MemoryKind::Semantic && unit.state == UnitState::Active)
            .collect()
    }

    pub fn belief_units(&self, tenant_id: TenantId) -> Vec<StoredMemoryUnit> {
        self.memory_units(tenant_id)
            .into_iter()
            .filter(|unit| unit.kind == MemoryKind::Belief && unit.state != UnitState::Quarantined)
            .collect()
    }

    pub fn quarantined_units(&self, tenant_id: TenantId) -> Vec<StoredMemoryUnit> {
        self.memory_units(tenant_id)
            .into_iter()
            .filter(|unit| unit.state == UnitState::Quarantined)
            .collect()
    }

    pub fn freshness_due_units(&self, tenant_id: TenantId) -> Vec<StoredMemoryUnit> {
        self.memory_units(tenant_id)
            .into_iter()
            .filter(|unit| unit.freshness_due_at.is_some() && unit.state == UnitState::Active)
            .collect()
    }

    /// Registers an API key row for tests / dev provisioning.
    pub fn insert_api_key(&self, row: ApiKeyRow) {
        if let Ok(mut state) = self.inner.lock() {
            state
                .api_keys
                .retain(|existing| existing.id != row.id && existing.key_hash != row.key_hash);
            state.api_keys.push(row);
        }
    }

    /// Claim attempts recorded for a reflect job (test observability).
    pub fn job_attempts(&self, job_id: JobId) -> u32 {
        self.inner
            .lock()
            .ok()
            .and_then(|state| state.job_meta.get(&job_id).map(|meta| meta.attempts))
            .unwrap_or(0)
    }

    pub fn memory_edges(&self, tenant_id: TenantId) -> Vec<StoredMemoryEdge> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .memory_edges
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }

    pub fn reflect_traces(&self, tenant_id: TenantId) -> Vec<ReflectTrace> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .reflect_traces
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }

    pub fn retrieval_traces(&self, tenant_id: TenantId) -> Vec<RetrievalTrace> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .retrieval_traces
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }

    /// UNTENANTED trace lookup for the pre-auth REST surface; the tenant-bound
    /// path is `MemoryStore::trace_by_id`.
    pub fn trace_by_id_any_tenant(&self, trace_id: TraceId) -> Option<RetrievalTrace> {
        self.inner.lock().ok().and_then(|state| {
            state
                .retrieval_traces
                .values()
                .flat_map(|traces| traces.iter())
                .find(|trace| trace.id == trace_id)
                .cloned()
        })
    }

    pub fn scope_memory(&self, tenant_id: TenantId, scope_id: ScopeId) -> Vec<StoredMemoryUnit> {
        self.memory_units(tenant_id)
            .into_iter()
            .filter(|unit| unit.scope_id == scope_id)
            .collect()
    }

    pub fn review_events(&self, tenant_id: TenantId) -> Vec<ReviewEvent> {
        self.inner
            .lock()
            .map(|state| {
                state
                    .review_events
                    .get(&tenant_id)
                    .cloned()
                    .unwrap_or_default()
            })
            .unwrap_or_default()
    }
}

fn validate_context_identity(
    context: &ResolvedMemoryContext,
    tenant_id: TenantId,
    data_subject_id: SubjectId,
    subject_generation: u64,
    scope_id: ScopeId,
    agent_node_id: AgentNodeId,
    actor_id: Option<ActorId>,
) -> Result<(), StoreError> {
    if tenant_id != context.tenant_id
        || data_subject_id != context.data_subject_id
        || subject_generation != context.subject_generation
        || scope_id != context.scope_id
        || agent_node_id != context.agent_node_id
        || actor_id != Some(context.actor_id)
    {
        return Err(StoreError::Conflict(
            "write does not match transaction context".to_string(),
        ));
    }
    Ok(())
}

fn unit_matches_context(unit: &StoredMemoryUnit, context: &ResolvedMemoryContext) -> bool {
    unit.tenant_id == context.tenant_id
        && unit.data_subject_id == context.data_subject_id
        && unit.subject_generation == context.subject_generation
        && unit.scope_id == context.scope_id
        && unit.agent_node_id == context.agent_node_id
        && unit.actor_id == Some(context.actor_id)
}

fn mutation_second(value: &str) -> Result<i64, StoreError> {
    value
        .parse::<jiff::Timestamp>()
        .map(|timestamp| timestamp.as_second())
        .map_err(|_| StoreError::Backend("transaction time must be RFC3339".to_string()))
}

impl MutationLedgerStore for InMemoryStore {
    async fn lookup_mutation_replay(
        &self,
        context: &ResolvedMemoryContext,
        claim: &MutationClaim,
    ) -> Result<Option<MutationResponse>, StoreError> {
        if claim.tenant_id != context.tenant_id
            || claim.data_subject_id != context.data_subject_id
            || claim.subject_generation != context.subject_generation
        {
            return Err(StoreError::IdempotencyConflict);
        }
        let now_second = SystemClock.now().as_second();
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        match state
            .mutation_ledger
            .get(&(claim.tenant_id, claim.verb, claim.idempotency_key.clone()))
            .filter(|entry| entry.expires_at_second > now_second)
        {
            Some(entry) if entry.claim == *claim => Ok(Some(entry.response.clone())),
            Some(_) => Err(StoreError::IdempotencyConflict),
            None => Ok(None),
        }
    }

    async fn stage_mutation_claim(
        &self,
        tx: &mut Self::Txn,
        claim: MutationClaim,
    ) -> Result<MutationClaimOutcome, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        if claim.subject_generation != tx.context.subject_generation {
            return Err(StoreError::StaleSubjectGeneration);
        }
        if claim.tenant_id != tx.context.tenant_id
            || claim.data_subject_id != tx.context.data_subject_id
        {
            return Err(StoreError::IdempotencyConflict);
        }
        if let Some(staged) = &tx.mutation {
            if staged.claim != claim {
                return Err(StoreError::IdempotencyConflict);
            }
            return Ok(match (&staged.response, staged.replay) {
                (Some(response), true) => MutationClaimOutcome::Replay(response.clone()),
                _ => MutationClaimOutcome::Execute,
            });
        }

        let now_second = mutation_second(&tx.transaction_time)?;
        self.prune_mutation_locks();
        let ledger_key = (claim.tenant_id, claim.verb, claim.idempotency_key.clone());
        let lock_key = (
            claim.tenant_id,
            claim.verb,
            Sha256::digest(claim.idempotency_key.as_bytes()).into(),
        );
        let lock = {
            let mut locks = self
                .mutation_locks
                .lock()
                .map_err(|_| StoreError::Poisoned)?;
            match locks.get(&lock_key).and_then(Weak::upgrade) {
                Some(lock) => lock,
                None => {
                    let lock = Arc::new(AsyncMutex::new(()));
                    locks.insert(lock_key, Arc::downgrade(&lock));
                    lock
                }
            }
        };
        let guard = lock.lock_owned().await;

        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state
            .mutation_ledger
            .retain(|_, entry| entry.expires_at_second > now_second);
        let outcome = match state.mutation_ledger.get(&ledger_key) {
            Some(entry) if entry.claim == claim => {
                Ok(MutationClaimOutcome::Replay(entry.response.clone()))
            }
            Some(_) => Err(StoreError::IdempotencyConflict),
            None => state
                .validate_context(&tx.context)
                .map(|()| MutationClaimOutcome::Execute),
        };
        drop(state);
        let outcome = match outcome {
            Ok(outcome) => outcome,
            Err(error) => {
                drop(guard);
                self.prune_mutation_locks();
                return Err(error);
            }
        };
        tx.mutation_guard = Some(guard);
        tx.mutation = Some(StagedMutation {
            claim,
            response: match &outcome {
                MutationClaimOutcome::Replay(response) => Some(response.clone()),
                MutationClaimOutcome::Execute => None,
            },
            replay: matches!(outcome, MutationClaimOutcome::Replay(_)),
        });
        Ok(outcome)
    }

    async fn stage_mutation_response(
        &self,
        tx: &mut Self::Txn,
        response: MutationResponse,
    ) -> Result<(), StoreError> {
        #[cfg(any(test, feature = "test-support"))]
        {
            if self
                .fail_next_mutation_response
                .swap(false, std::sync::atomic::Ordering::SeqCst)
            {
                return Err(StoreError::Backend(
                    "injected mutation response failure".to_string(),
                ));
            }
        }
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        let staged = tx.mutation.as_mut().ok_or_else(|| {
            StoreError::Conflict("mutation claim must be staged before its response".to_string())
        })?;
        if staged.replay {
            return Err(StoreError::Conflict(
                "replayed mutation cannot stage a new response".to_string(),
            ));
        }
        if tx.subject_erasure.is_some() {
            return Err(StoreError::Conflict(
                "subject erasure response is generated by the store".to_string(),
            ));
        }
        staged.response = Some(response);
        Ok(())
    }

    async fn stage_subject_erasure(
        &self,
        tx: &mut Self::Txn,
    ) -> Result<SubjectErasureReceipt, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        if !tx.episodes.is_empty()
            || !tx.episode_observation_updates.is_empty()
            || !tx.resources.is_empty()
            || !tx.memory_units.is_empty()
            || !tx.memory_edges.is_empty()
            || !tx.reflect_jobs.is_empty()
            || !tx.task_outcomes.is_empty()
            || !tx.task_memory_events.is_empty()
            || tx.state_snapshot.is_some()
        {
            return Err(StoreError::Conflict(
                "subject erasure requires an otherwise empty transaction".to_string(),
            ));
        }
        let staged = tx.mutation.as_mut().ok_or_else(|| {
            StoreError::Conflict("erasure claim must be staged first".to_string())
        })?;
        if staged.replay || staged.claim.verb != MutationVerb::EraseSubject {
            return Err(StoreError::Conflict(
                "subject erasure requires an executable erase_subject claim".to_string(),
            ));
        }
        if staged.claim.subject_generation != tx.context.subject_generation {
            return Err(StoreError::StaleSubjectGeneration);
        }
        if tx.subject_erasure.is_some() {
            return Err(StoreError::Conflict(
                "subject erasure is already staged".to_string(),
            ));
        }
        let receipt = SubjectErasureReceipt {
            generation: tx
                .context
                .subject_generation
                .checked_add(1)
                .ok_or_else(|| StoreError::Conflict("subject generation overflow".to_string()))?,
            erased_at: tx.transaction_time.clone(),
        };
        staged.response = Some(MutationResponse::success(
            200,
            serde_json::to_vec(&receipt).map_err(|error| StoreError::Backend(error.to_string()))?,
        )?);
        tx.subject_erasure = Some(receipt.clone());
        Ok(receipt)
    }

    async fn stage_task_outcome(
        &self,
        tx: &mut Self::Txn,
        outcome: TaskOutcomeRow,
        events: Vec<TaskMemoryEventRow>,
    ) -> Result<(), StoreError> {
        validate_context_identity(
            &tx.context,
            outcome.tenant_id,
            outcome.data_subject_id,
            outcome.subject_generation,
            outcome.scope_id,
            outcome.agent_node_id,
            Some(outcome.actor_id),
        )?;
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        let units = state.memory_units.get(&outcome.tenant_id);
        if events.iter().any(|event| {
            units.is_none_or(|units| {
                !units
                    .iter()
                    .any(|unit| unit.id == event.unit_id && unit_matches_context(unit, &tx.context))
            })
        }) {
            return Err(StoreError::NotFound("memory unit"));
        }
        drop(state);
        if tx
            .task_outcomes
            .iter()
            .any(|row| row.task_id == outcome.task_id)
        {
            return Err(StoreError::Conflict("task already exists".to_string()));
        }
        tx.task_outcomes.push(outcome);
        tx.task_memory_events.extend(events);
        Ok(())
    }

    async fn stage_task_memory_events(
        &self,
        tx: &mut Self::Txn,
        events: Vec<TaskMemoryEventRow>,
    ) -> Result<(), StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        for event in &events {
            validate_context_identity(
                &tx.context,
                event.tenant_id,
                event.data_subject_id,
                event.subject_generation,
                event.scope_id,
                event.agent_node_id,
                Some(event.actor_id),
            )?;
            if !state
                .task_outcomes
                .get(&event.tenant_id)
                .is_some_and(|rows| rows.iter().any(|row| row.task_id == event.task_id))
            {
                return Err(StoreError::NotFound("task outcome"));
            }
            if !state
                .memory_units
                .get(&event.tenant_id)
                .is_some_and(|units| {
                    units.iter().any(|unit| {
                        unit.id == event.unit_id && unit_matches_context(unit, &tx.context)
                    })
                })
            {
                return Err(StoreError::NotFound("memory unit"));
            }
            if matches!(
                event.event,
                TaskMemoryEventKind::Helpful | TaskMemoryEventKind::Harmful
            ) && !state
                .task_memory_events
                .get(&event.tenant_id)
                .is_some_and(|rows| {
                    rows.iter().any(|row| {
                        row.task_id == event.task_id
                            && row.unit_id == event.unit_id
                            && matches!(
                                row.event,
                                TaskMemoryEventKind::Shown | TaskMemoryEventKind::Activated
                            )
                    })
                })
            {
                return Err(StoreError::Conflict(
                    "task evidence requires prior exposure".to_string(),
                ));
            }
        }
        drop(state);
        tx.task_memory_events.extend(events);
        Ok(())
    }
}

fn in_memory_canonical_projection_units(
    state: &InMemoryState,
    context: &ResolvedMemoryContext,
    evaluated_at: &str,
) -> Vec<StoredMemoryUnit> {
    let time = RecallTime {
        evaluated_at: evaluated_at.to_string(),
        transaction_as_of: evaluated_at.to_string(),
        valid_at: evaluated_at.to_string(),
    };
    let mut units = state
        .memory_units
        .get(&context.tenant_id)
        .into_iter()
        .flatten()
        .filter(|unit| {
            unit.tenant_id == context.tenant_id
                && unit.data_subject_id == context.data_subject_id
                && unit.subject_generation == context.subject_generation
                && unit.scope_id == context.scope_id
                && unit.agent_node_id == context.agent_node_id
                && unit.actor_id == Some(context.actor_id)
                && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                && unit.deletion_generation.is_none()
                && unit.trust_level != TrustLevel::Quarantined
                && bitemporally_recallable(unit, &time)
                && matches!(
                    (unit.kind, unit.state),
                    (
                        MemoryKind::Semantic,
                        UnitState::Active | UnitState::Validated
                    ) | (MemoryKind::Procedural, UnitState::Validated)
                )
        })
        .cloned()
        .collect::<Vec<_>>();
    units.sort_unstable_by_key(|unit| unit.id.as_uuid());
    units
}

fn in_memory_scope_open_units(
    state: &InMemoryState,
    context: &ResolvedMemoryContext,
) -> Vec<StoredMemoryUnit> {
    let mut units = state
        .memory_units
        .get(&context.tenant_id)
        .into_iter()
        .flatten()
        .filter(|unit| {
            unit.data_subject_id == context.data_subject_id
                && unit.subject_generation == context.subject_generation
                && unit.scope_id == context.scope_id
                && unit.agent_node_id == context.agent_node_id
                && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                && unit.transaction_to.is_none()
        })
        .cloned()
        .collect::<Vec<_>>();
    units.sort_unstable_by_key(|unit| unit.id.as_uuid());
    units
}

fn in_memory_file_sync_transition_snapshot(
    state: &InMemoryState,
    context: &ResolvedMemoryContext,
) -> FileSyncTransitionSnapshot {
    let units = state
        .memory_units
        .get(&context.tenant_id)
        .into_iter()
        .flatten()
        .filter(|unit| {
            unit.data_subject_id == context.data_subject_id
                && unit.subject_generation == context.subject_generation
                && unit.scope_id == context.scope_id
                && unit.agent_node_id == context.agent_node_id
                && unit.actor_id == Some(context.actor_id)
        })
        .cloned()
        .collect::<Vec<_>>();
    let unit_ids: HashSet<_> = units.iter().map(|unit| unit.id).collect();
    let edges = state
        .memory_edges
        .get(&context.tenant_id)
        .into_iter()
        .flatten()
        .filter(|edge| unit_ids.contains(&edge.src_id) && unit_ids.contains(&edge.dst_id))
        .cloned()
        .collect();
    FileSyncTransitionSnapshot::new(units, edges)
}

impl MemoryStore for InMemoryStore {
    type Txn = InMemoryTxn;

    async fn begin(&self, context: &ResolvedMemoryContext) -> Result<Self::Txn, StoreError> {
        Ok(self.begin_at(context, &SystemClock))
    }

    async fn begin_serializable(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<Self::Txn, StoreError> {
        self.begin(context).await
    }

    async fn commit(&self, mut tx: Self::Txn) -> Result<(), StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        tx.committed = true;
        let mutation_guard = tx.mutation_guard.take();

        let now_second = mutation_second(&tx.transaction_time)?;
        let has_staged_writes = !tx.episodes.is_empty()
            || !tx.episode_observation_updates.is_empty()
            || !tx.resources.is_empty()
            || !tx.memory_units.is_empty()
            || !tx.memory_edges.is_empty()
            || !tx.reflect_jobs.is_empty()
            || tx.state_snapshot.is_some()
            || tx.subject_erasure.is_some();
        let subject_erasure = tx.subject_erasure.take();
        let mut mutation = match tx.mutation.take() {
            Some(staged) if staged.replay => {
                if has_staged_writes {
                    return Err(StoreError::Conflict(
                        "replayed mutation cannot contain staged writes".to_string(),
                    ));
                }
                drop(mutation_guard);
                self.prune_mutation_locks();
                return Ok(());
            }
            Some(staged) => Some((
                staged.claim,
                staged.response.ok_or_else(|| {
                    StoreError::Conflict(
                        "successful mutation response must be staged before commit".to_string(),
                    )
                })?,
            )),
            None => None,
        };
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        let mut next = match tx.state_snapshot.take() {
            Some((baseline, staged)) => {
                if InMemoryContextState::capture(&state, &tx.context) != baseline {
                    return Err(StoreError::Conflict(
                        "in-memory transaction state changed before commit".to_string(),
                    ));
                }
                let staged = InMemoryContextState::capture(&staged, &tx.context);
                let mut next = state.clone();
                staged.replace_in(&mut next, &tx.context);
                next
            }
            None => state.clone(),
        };
        next.validate_context(&tx.context)?;
        if let Some((claim, _)) = &mutation {
            let key = (claim.tenant_id, claim.verb, claim.idempotency_key.clone());
            if let Some(existing) = next.mutation_ledger.get(&key)
                && existing.expires_at_second > now_second
            {
                if existing.claim == *claim {
                    drop(state);
                    drop(mutation_guard);
                    self.prune_mutation_locks();
                    return Ok(());
                }
                return Err(StoreError::IdempotencyConflict);
            }
        }
        if let Some(receipt) = &subject_erasure {
            let (claim, response) = mutation.take().ok_or_else(|| {
                StoreError::Conflict("subject erasure requires a mutation claim".to_string())
            })?;
            if claim.verb != MutationVerb::EraseSubject
                || receipt.generation != claim.subject_generation + 1
            {
                return Err(StoreError::Conflict(
                    "subject erasure does not match its claim".to_string(),
                ));
            }
            next.erase_subject(claim.tenant_id, claim.data_subject_id, receipt);
            next.mutation_ledger.insert(
                (claim.tenant_id, claim.verb, claim.idempotency_key.clone()),
                MutationLedgerEntry {
                    claim,
                    response,
                    expires_at_second: now_second + 24 * 60 * 60,
                },
            );
            *state = next;
            drop(state);
            drop(mutation_guard);
            self.prune_mutation_locks();
            return Ok(());
        }
        for (tenant_id, episode_id, observed_at) in tx.episode_observation_updates {
            if let Some(episode) = next
                .episodes
                .entry(tenant_id)
                .or_default()
                .iter_mut()
                .find(|episode| episode.id == episode_id)
            {
                episode.observation_count += 1;
                if cmp_rfc3339(&observed_at, &episode.first_observed_at).is_lt() {
                    episode.first_observed_at = observed_at.clone();
                }
                if cmp_rfc3339(&observed_at, &episode.last_observed_at).is_gt() {
                    episode.last_observed_at = observed_at;
                }
            }
        }
        for episode in tx.episodes {
            next.episodes
                .entry(episode.tenant_id)
                .or_default()
                .push(episode);
        }
        for resource in tx.resources {
            next.resources
                .entry(resource.tenant_id)
                .or_default()
                .push(resource);
        }
        for unit in tx.memory_units {
            next.memory_units
                .entry(unit.tenant_id)
                .or_default()
                .push(unit);
        }
        for edge in tx.memory_edges {
            next.memory_edges
                .entry(edge.tenant_id)
                .or_default()
                .push(edge);
        }
        for job in tx.reflect_jobs {
            next.reflect_jobs
                .entry(job.tenant_id)
                .or_default()
                .push(job);
        }
        for outcome in tx.task_outcomes {
            next.task_outcomes
                .entry(outcome.tenant_id)
                .or_default()
                .push(outcome);
        }
        for event in tx.task_memory_events {
            next.task_memory_events
                .entry(event.tenant_id)
                .or_default()
                .push(event);
        }
        if let Some((claim, response)) = mutation {
            next.mutation_ledger.insert(
                (claim.tenant_id, claim.verb, claim.idempotency_key.clone()),
                MutationLedgerEntry {
                    claim,
                    response,
                    expires_at_second: now_second + 24 * 60 * 60,
                },
            );
        }
        *state = next;
        drop(state);
        drop(mutation_guard);
        self.prune_mutation_locks();
        Ok(())
    }

    async fn rollback(&self, _tx: Self::Txn) -> Result<(), StoreError> {
        Ok(())
    }

    async fn stage_episode(
        &self,
        tx: &mut Self::Txn,
        episode: NewEpisode,
    ) -> Result<RetainOutcome, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        validate_context_identity(
            &tx.context,
            episode.tenant_id,
            episode.data_subject_id,
            episode.subject_generation,
            episode.scope_id,
            episode.agent_node_id,
            Some(episode.actor_id),
        )?;

        if let Some(staged) = tx.episodes.iter_mut().find(|staged| {
            staged.tenant_id == episode.tenant_id
                && staged.data_subject_id == episode.data_subject_id
                && staged.subject_generation == episode.subject_generation
                && staged.scope_id == episode.scope_id
                && staged.agent_node_id == episode.agent_node_id
                && staged.actor_id == episode.actor_id
                && staged.dedup_key == episode.dedup_key
        }) {
            staged.observation_count += 1;
            if cmp_rfc3339(&episode.observed_at, &staged.first_observed_at).is_lt() {
                staged.first_observed_at = episode.observed_at.clone();
            }
            if cmp_rfc3339(&episode.observed_at, &staged.last_observed_at).is_gt() {
                staged.last_observed_at = episode.observed_at.clone();
            }
            return Ok(RetainOutcome {
                episode_id: staged.id,
                dedup: DedupOutcome {
                    matched: true,
                    observation_count: staged.observation_count,
                },
            });
        }

        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        if let Some(existing) = state.episodes.get(&episode.tenant_id).and_then(|episodes| {
            episodes.iter().find(|stored| {
                stored.data_subject_id == episode.data_subject_id
                    && stored.subject_generation == episode.subject_generation
                    && stored.scope_id == episode.scope_id
                    && stored.agent_node_id == episode.agent_node_id
                    && stored.actor_id == episode.actor_id
                    && stored.dedup_key == episode.dedup_key
            })
        }) {
            let pending_updates = tx
                .episode_observation_updates
                .iter()
                .filter(|(_, id, _)| *id == existing.id)
                .count() as u32;
            tx.episode_observation_updates.push((
                episode.tenant_id,
                existing.id,
                episode.observed_at,
            ));
            return Ok(RetainOutcome {
                episode_id: existing.id,
                dedup: DedupOutcome {
                    matched: true,
                    observation_count: existing.observation_count + pending_updates + 1,
                },
            });
        }
        drop(state);

        let id = EpisodeId::new();
        let stored = StoredEpisode {
            id,
            tenant_id: episode.tenant_id,
            data_subject_id: episode.data_subject_id,
            scope_id: episode.scope_id,
            actor_id: episode.actor_id,
            agent_node_id: episode.agent_node_id,
            subject_generation: episode.subject_generation,
            source_kind: episode.source_kind,
            source_ref: episode.source_ref,
            source_trust: episode.source_trust,
            dedup_key: episode.dedup_key,
            body: episode.body,
            observation_count: 1,
            first_observed_at: episode.observed_at.clone(),
            last_observed_at: episode.observed_at,
        };
        tx.episodes.push(stored);
        Ok(RetainOutcome {
            episode_id: id,
            dedup: DedupOutcome {
                matched: false,
                observation_count: 1,
            },
        })
    }

    async fn stage_memory_unit(
        &self,
        tx: &mut Self::Txn,
        unit: NewMemoryUnit,
    ) -> Result<UnitId, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        validate_context_identity(
            &tx.context,
            unit.tenant_id,
            unit.data_subject_id,
            unit.subject_generation,
            unit.scope_id,
            unit.agent_node_id,
            unit.actor_id,
        )?;

        let id = UnitId::new();
        tx.memory_units.push(StoredMemoryUnit {
            invalidation: None,
            compact: None,
            capture: unit.capture.clone(),
            id,
            tenant_id: unit.tenant_id,
            data_subject_id: unit.data_subject_id,
            scope_id: unit.scope_id,
            agent_node_id: unit.agent_node_id,
            subject_generation: unit.subject_generation,
            kind: unit.kind,
            state: unit.state,
            fact_key: unit.fact_key,
            predicate: unit.predicate,
            body: unit.body,
            confidence: unit.confidence,
            trust_level: unit.trust_level,
            churn_class: unit.churn_class,
            freshness_due_at: unit.freshness_due_at,
            actor_id: unit.actor_id,
            source_kind: unit.source_kind,
            source_ref: unit.source_ref,
            observed_at: unit.observed_at,
            source_episode_id: unit.source_episode_id,
            source_resource_id: unit.source_resource_id,
            deletion_generation: unit.deletion_generation,
            contextual_chunks: unit.contextual_chunks,
            valid_from: unit.valid_from,
            valid_to: unit.valid_to,
            transaction_from: unit.transaction_from,
            transaction_to: unit.transaction_to,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        });
        Ok(id)
    }

    async fn stage_resource(
        &self,
        tx: &mut Self::Txn,
        resource: NewResource,
    ) -> Result<ResourceId, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        validate_context_identity(
            &tx.context,
            resource.tenant_id,
            resource.data_subject_id,
            resource.subject_generation,
            resource.scope_id,
            resource.agent_node_id,
            Some(resource.actor_id),
        )?;

        let id = ResourceId::new();
        tx.resources.push(StoredResource {
            id,
            tenant_id: resource.tenant_id,
            data_subject_id: resource.data_subject_id,
            scope_id: resource.scope_id,
            actor_id: resource.actor_id,
            agent_node_id: resource.agent_node_id,
            subject_generation: resource.subject_generation,
            uri: resource.uri,
            source_ref: resource.source_ref,
            observed_at: resource.observed_at,
            kind: resource.kind,
            content_hash: resource.content_hash,
            mime_type: resource.mime_type,
            revision: resource.revision,
            body: resource.body,
            source_trust: resource.source_trust,
            acl: resource.acl,
            extractor_state: ResourceExtractorState::Registered,
        });
        Ok(id)
    }

    async fn stage_memory_edge(
        &self,
        tx: &mut Self::Txn,
        edge: NewMemoryEdge,
    ) -> Result<EdgeId, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        if edge.tenant_id != tx.context.tenant_id || edge.scope_id != tx.context.scope_id {
            return Err(StoreError::Conflict(
                "memory edge does not match transaction context".to_string(),
            ));
        }
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        let endpoint_matches = |id| {
            tx.memory_units
                .iter()
                .chain(
                    state
                        .memory_units
                        .get(&tx.context.tenant_id)
                        .into_iter()
                        .flatten(),
                )
                .any(|unit| unit.id == id && unit_matches_context(unit, &tx.context))
        };
        if !endpoint_matches(edge.src_id) || !endpoint_matches(edge.dst_id) {
            return Err(StoreError::Conflict(
                "memory edge endpoints must belong to the transaction context".to_string(),
            ));
        }
        drop(state);

        let id = EdgeId::new();
        tx.memory_edges.push(StoredMemoryEdge {
            id,
            tenant_id: edge.tenant_id,
            scope_id: edge.scope_id,
            src_id: edge.src_id,
            dst_id: edge.dst_id,
            kind: edge.kind,
            transaction_from: Some(tx.transaction_time.clone()),
            transaction_to: None,
        });
        Ok(id)
    }

    async fn enqueue_reflect(
        &self,
        tx: &mut Self::Txn,
        job: ReflectJob,
    ) -> Result<JobId, StoreError> {
        if tx.committed {
            return Err(StoreError::TransactionAlreadyCommitted);
        }
        validate_context_identity(
            &tx.context,
            job.tenant_id,
            job.data_subject_id,
            job.subject_generation,
            job.scope_id,
            job.agent_node_id,
            Some(job.actor_id),
        )?;
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        let target_matches = match job.kind {
            ReflectJobKind::ReflectEpisode if job.resource_id.is_none() => {
                job.episode_id.is_some_and(|id| {
                    tx.episodes.iter().any(|episode| episode.id == id)
                        || state
                            .episodes
                            .get(&tx.context.tenant_id)
                            .is_some_and(|episodes| {
                                episodes.iter().any(|episode| {
                                    episode.id == id
                                        && episode.data_subject_id == tx.context.data_subject_id
                                        && episode.subject_generation
                                            == tx.context.subject_generation
                                        && episode.scope_id == tx.context.scope_id
                                        && episode.agent_node_id == tx.context.agent_node_id
                                        && episode.actor_id == tx.context.actor_id
                                })
                            })
                })
            }
            ReflectJobKind::ReflectResource if job.episode_id.is_none() => {
                job.resource_id.is_some_and(|id| {
                    tx.resources.iter().any(|resource| resource.id == id)
                        || state
                            .resources
                            .get(&tx.context.tenant_id)
                            .is_some_and(|resources| {
                                resources.iter().any(|resource| {
                                    resource.id == id
                                        && resource.data_subject_id == tx.context.data_subject_id
                                        && resource.subject_generation
                                            == tx.context.subject_generation
                                        && resource.scope_id == tx.context.scope_id
                                        && resource.agent_node_id == tx.context.agent_node_id
                                        && resource.actor_id == tx.context.actor_id
                                })
                            })
                })
            }
            ReflectJobKind::ReflectScope
                if job.episode_id.is_none() && job.resource_id.is_none() =>
            {
                true
            }
            _ => false,
        };
        drop(state);
        if !target_matches {
            return Err(StoreError::Conflict(
                "reflect job target must belong to the transaction context".to_string(),
            ));
        }

        let same_job = |queued: &QueuedReflectJob| {
            queued.tenant_id == job.tenant_id
                && queued.data_subject_id == job.data_subject_id
                && queued.subject_generation == job.subject_generation
                && queued.scope_id == job.scope_id
                && queued.actor_id == job.actor_id
                && queued.agent_node_id == job.agent_node_id
                && queued.kind == job.kind
                && queued.episode_id == job.episode_id
                && queued.resource_id == job.resource_id
                && queued.compiler_version == job.compiler_version
        };
        if job.kind != ReflectJobKind::ReflectScope {
            if let Some(existing) = tx.reflect_jobs.iter().find(|queued| same_job(queued)) {
                return Ok(existing.id);
            }
            let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
            if let Some(existing) = state
                .reflect_jobs
                .get(&job.tenant_id)
                .and_then(|jobs| jobs.iter().find(|queued| same_job(queued)))
            {
                return Ok(existing.id);
            }
            drop(state);
        }

        let id = JobId::new();
        tx.reflect_jobs.push(QueuedReflectJob {
            id,
            tenant_id: job.tenant_id,
            data_subject_id: job.data_subject_id,
            scope_id: job.scope_id,
            actor_id: job.actor_id,
            agent_node_id: job.agent_node_id,
            subject_generation: job.subject_generation,
            episode_id: job.episode_id,
            resource_id: job.resource_id,
            kind: job.kind,
            compiler_version: job.compiler_version,
            subject: job.subject,
            predicate: job.predicate,
        });
        Ok(id)
    }

    async fn fetch_recall_candidates(
        &self,
        context: &ResolvedMemoryContext,
        kinds: &[MemoryKind],
        _query_terms: &[String],
        time: &RecallTime,
        limit: usize,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let mut units: Vec<_> = state
            .memory_units
            .get(&context.tenant_id)
            .map(|units| {
                units
                    .iter()
                    .filter(|unit| {
                        unit.data_subject_id == context.data_subject_id
                            && unit.subject_generation == context.subject_generation
                            && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                    })
                    .filter(|unit| kinds.is_empty() || kinds.contains(&unit.kind))
                    .filter(|unit| bitemporally_recallable(unit, time))
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
        units.truncate(limit);
        Ok(units)
    }

    async fn fetch_deep_snapshot(
        &self,
        context: &ResolvedMemoryContext,
        time: &RecallTime,
    ) -> Result<Vec<DeepSnapshotEntry>, StoreError> {
        #[cfg(test)]
        self.deep_snapshot_reads
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;

        let episode_by_id: HashMap<Uuid, &StoredEpisode> = state
            .episodes
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .map(|episode| (episode.id.as_uuid(), episode))
            .collect();
        let resource_by_id: HashMap<Uuid, &StoredResource> = state
            .resources
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .map(|resource| (resource.id.as_uuid(), resource))
            .collect();
        let mut grouped: BTreeMap<(DeepSnapshotSourceKind, Uuid), (String, Vec<StoredMemoryUnit>)> =
            BTreeMap::new();

        for unit in state
            .memory_units
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
        {
            if unit.tenant_id != context.tenant_id
                || unit.data_subject_id != context.data_subject_id
                || unit.subject_generation != context.subject_generation
                || !context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                || !deep_unit_is_snapshot_eligible(unit, time)
                || state.is_forgotten_source(unit)
            {
                continue;
            }

            let owner_matches = |tenant_id: TenantId,
                                 data_subject_id: SubjectId,
                                 subject_generation: u64,
                                 scope_id: ScopeId,
                                 agent_node_id: AgentNodeId| {
                tenant_id == unit.tenant_id
                    && data_subject_id == unit.data_subject_id
                    && subject_generation == unit.subject_generation
                    && scope_id == unit.scope_id
                    && agent_node_id == unit.agent_node_id
            };

            let source = if let Some(episode_id) = unit.source_episode_id {
                let Some(episode) = episode_by_id.get(&episode_id.as_uuid()) else {
                    continue;
                };
                if !context.allows(
                    MemoryKind::Episodic,
                    episode.scope_id,
                    episode.agent_node_id,
                ) || !owner_matches(
                    episode.tenant_id,
                    episode.data_subject_id,
                    episode.subject_generation,
                    episode.scope_id,
                    episode.agent_node_id,
                ) || episode.source_trust == TrustLevel::Quarantined
                {
                    continue;
                }
                (
                    DeepSnapshotSourceKind::Episode,
                    episode_id.as_uuid(),
                    episode.body.clone(),
                )
            } else if let Some(resource_id) = unit.source_resource_id {
                let Some(resource) = resource_by_id.get(&resource_id.as_uuid()) else {
                    continue;
                };
                if !context.allows(
                    MemoryKind::Resource,
                    resource.scope_id,
                    resource.agent_node_id,
                ) || !owner_matches(
                    resource.tenant_id,
                    resource.data_subject_id,
                    resource.subject_generation,
                    resource.scope_id,
                    resource.agent_node_id,
                ) || resource.source_trust == TrustLevel::Quarantined
                    || !resource.acl.is_deep_eligible()
                {
                    continue;
                }
                let Some(body) = resource.body.clone() else {
                    continue;
                };
                (
                    DeepSnapshotSourceKind::Resource,
                    resource_id.as_uuid(),
                    body,
                )
            } else {
                continue;
            };
            grouped
                .entry((source.0, source.1))
                .or_insert_with(|| (source.2, Vec::new()))
                .1
                .push(unit.clone());
        }

        Ok(grouped
            .into_iter()
            .map(|((source_kind, source_id), (body, mut bound_units))| {
                bound_units.sort_unstable_by_key(|unit| unit.id.as_uuid());
                DeepSnapshotEntry {
                    source_kind,
                    source_id,
                    path: deep_source_path(source_kind, source_id),
                    body_sha256: sha256_bytes_hex(body.as_bytes()),
                    body,
                    bound_units,
                }
            })
            .collect())
    }

    async fn fetch_scope_open_units(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(in_memory_scope_open_units(&state, context))
    }

    async fn fetch_scope_open_units_in_tx(
        &self,
        tx: &mut Self::Txn,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        Ok(in_memory_scope_open_units(state, &context))
    }

    async fn apply_capture_transitions(
        &self,
        context: &ResolvedMemoryContext,
        transitions: Vec<CaptureTransition>,
    ) -> Result<CaptureCrossCheckReport, StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let mut report = CaptureCrossCheckReport::default();
        let units = state.memory_units.entry(context.tenant_id).or_default();
        for transition in &transitions {
            let Some(unit) = units.iter_mut().find(|unit| {
                unit.id == transition.id
                    && unit.data_subject_id == context.data_subject_id
                    && unit.subject_generation == context.subject_generation
                    && unit.scope_id == context.scope_id
                    && unit.agent_node_id == context.agent_node_id
                    && unit.transaction_to.is_none()
            }) else {
                return Err(StoreError::Conflict(
                    "capture transition does not match memory context".to_string(),
                ));
            };
            unit.state = transition.state;
            unit.confidence = transition.confidence;
            unit.capture = Some(transition.capture.clone());
            report.record(transition);
        }
        Ok(report)
    }

    async fn fetch_file_sync_transition_snapshot(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<FileSyncTransitionSnapshot, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(in_memory_file_sync_transition_snapshot(&state, context))
    }

    async fn fetch_file_sync_transition_snapshot_in_tx(
        &self,
        tx: &mut Self::Txn,
    ) -> Result<FileSyncTransitionSnapshot, StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        Ok(in_memory_file_sync_transition_snapshot(state, &context))
    }

    async fn fetch_vector_candidates(
        &self,
        context: &ResolvedMemoryContext,
        query_vec: &[f32],
        profile_id: Uuid,
        time: &RecallTime,
        limit: usize,
    ) -> Result<Vec<(StoredMemoryUnit, f32)>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        // Group the tenant's profile-matching embeddings by unit up front so
        // the per-unit lookup below is O(1) instead of a full-tenant rescan
        // (which made this O(units x embeddings)).
        let mut embeddings_by_unit: HashMap<_, Vec<&EmbeddingRow>> = HashMap::new();
        for row in state
            .embeddings
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|row| row.embedding_profile_id == profile_id)
        {
            embeddings_by_unit
                .entry(row.memory_unit_id)
                .or_default()
                .push(row);
        }
        let mut scored: Vec<(StoredMemoryUnit, f32)> = state
            .memory_units
            .get(&context.tenant_id)
            .map(|units| {
                units
                    .iter()
                    .filter(|unit| {
                        unit.data_subject_id == context.data_subject_id
                            && unit.subject_generation == context.subject_generation
                            && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                    })
                    .filter(|unit| bitemporally_recallable(unit, time))
                    .filter_map(|unit| {
                        // Best (nearest) embedding for this unit UNDER the
                        // active profile; app-side cosine, returned as cosine
                        // DISTANCE (1 - similarity) to mirror pgvector `<=>`.
                        embeddings_by_unit
                            .get(&unit.id)
                            .into_iter()
                            .flatten()
                            .map(|row| 1.0 - cosine_similarity(query_vec, &row.vec))
                            .min_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
                            .map(|distance| (unit.clone(), distance))
                    })
                    .collect()
            })
            .unwrap_or_default();
        scored.sort_by(|left, right| {
            left.1
                .partial_cmp(&right.1)
                .unwrap_or(std::cmp::Ordering::Equal)
                // Content tie-break so the top-`limit` cut is stable across
                // re-ingests, mirroring the postgres `<=>, unit.body` order-by.
                .then_with(|| left.0.body.cmp(&right.0.body))
        });
        scored.truncate(limit);
        Ok(scored)
    }

    async fn fetch_units_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(state
            .memory_units
            .get(&context.tenant_id)
            .map(|units| {
                units
                    .iter()
                    .filter(|unit| {
                        ids.contains(&unit.id)
                            && unit.data_subject_id == context.data_subject_id
                            && unit.subject_generation == context.subject_generation
                            && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                    })
                    .cloned()
                    .collect()
            })
            .unwrap_or_default())
    }

    async fn fetch_edges(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<StoredMemoryEdge>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let requested_ids: HashSet<_> = state
            .memory_units
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|unit| {
                unit_ids.contains(&unit.id)
                    && unit.data_subject_id == context.data_subject_id
                    && unit.subject_generation == context.subject_generation
                    && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
            })
            .map(|unit| unit.id)
            .collect();
        let authorized_ids: HashSet<_> = state
            .memory_units
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|unit| {
                unit.data_subject_id == context.data_subject_id
                    && unit.subject_generation == context.subject_generation
                    && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
            })
            .map(|unit| unit.id)
            .collect();
        Ok(state
            .memory_edges
            .get(&context.tenant_id)
            .map(|edges| {
                edges
                    .iter()
                    .filter(|edge| {
                        (requested_ids.contains(&edge.src_id)
                            || requested_ids.contains(&edge.dst_id))
                            && authorized_ids.contains(&edge.src_id)
                            && authorized_ids.contains(&edge.dst_id)
                    })
                    .filter(|edge| {
                        edge.transaction_from.as_deref().is_none_or(|from| {
                            cmp_rfc3339(from, &time.transaction_as_of)
                                != std::cmp::Ordering::Greater
                        }) && edge.transaction_to.as_deref().is_none_or(|to| {
                            cmp_rfc3339(&time.transaction_as_of, to) == std::cmp::Ordering::Less
                        })
                    })
                    .cloned()
                    .collect()
            })
            .unwrap_or_default())
    }

    async fn fetch_record_material(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<RecordMaterial>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let authorized: HashMap<UnitId, StoredMemoryUnit> = state
            .memory_units
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|unit| {
                unit.data_subject_id == context.data_subject_id
                    && unit.subject_generation == context.subject_generation
                    && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                    && bitemporally_recallable(unit, time)
            })
            .map(|unit| (unit.id, unit.clone()))
            .collect();
        Ok(ids
            .iter()
            .filter_map(|id| authorized.get(id))
            .map(|unit| RecordMaterial {
                unit: unit.clone(),
                citations: state
                    .citations
                    .get(&context.tenant_id)
                    .into_iter()
                    .flatten()
                    .filter(|citation| {
                        citation.memory_unit_id == unit.id
                            && citation.data_subject_id == context.data_subject_id
                            && citation.subject_generation == context.subject_generation
                    })
                    .cloned()
                    .collect(),
                lineage: state
                    .memory_edges
                    .get(&context.tenant_id)
                    .into_iter()
                    .flatten()
                    .filter(|edge| edge.src_id == unit.id || edge.dst_id == unit.id)
                    .filter(|edge| {
                        authorized.contains_key(&edge.src_id)
                            && authorized.contains_key(&edge.dst_id)
                    })
                    .filter(|edge| {
                        edge.transaction_from.as_deref().is_none_or(|from| {
                            cmp_rfc3339(from, &time.transaction_as_of)
                                != std::cmp::Ordering::Greater
                        }) && edge.transaction_to.as_deref().is_none_or(|to| {
                            cmp_rfc3339(&time.transaction_as_of, to) == std::cmp::Ordering::Less
                        })
                    })
                    .filter(|edge| {
                        matches!(
                            edge.kind,
                            MemoryEdgeKind::Supersedes
                                | MemoryEdgeKind::Contradicts
                                | MemoryEdgeKind::DerivedFrom
                                | MemoryEdgeKind::Cites
                        )
                    })
                    .cloned()
                    .collect(),
            })
            .collect())
    }

    async fn fetch_review_events(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<ReviewEventRow>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let trace_ids: HashSet<_> = state
            .retrieval_traces
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|trace| {
                trace.data_subject_id == context.data_subject_id
                    && trace.subject_generation == context.subject_generation
                    && trace.scope_id == context.scope_id
                    && trace.actor_id == context.actor_id
                    && trace.agent_node_id == context.agent_node_id
            })
            .map(|trace| trace.id)
            .collect();
        Ok(state
            .review_events
            .get(&context.tenant_id)
            .map(|events| {
                events
                    .iter()
                    .filter(|event| trace_ids.contains(&event.trace_id))
                    .filter(|event| {
                        event.used_ids.is_empty()
                            || event.used_ids.iter().any(|id| unit_ids.contains(id))
                    })
                    .filter(|event| {
                        cmp_rfc3339(&event.recorded_at, &time.transaction_as_of)
                            != std::cmp::Ordering::Greater
                    })
                    .cloned()
                    .map(|mut event| {
                        event.used_ids.retain(|id| unit_ids.contains(id));
                        event
                    })
                    .collect()
            })
            .unwrap_or_default())
    }

    async fn fetch_episodes_for_scope(
        &self,
        context: &ResolvedMemoryContext,
        limit: usize,
    ) -> Result<Vec<StoredEpisode>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let mut episodes: Vec<_> = state
            .episodes
            .get(&context.tenant_id)
            .map(|episodes| {
                episodes
                    .iter()
                    .filter(|episode| {
                        episode.data_subject_id == context.data_subject_id
                            && episode.subject_generation == context.subject_generation
                            && context.allows(
                                MemoryKind::Episodic,
                                episode.scope_id,
                                episode.agent_node_id,
                            )
                    })
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
        episodes.truncate(limit);
        Ok(episodes)
    }

    async fn pending_job_count(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<usize, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(state
            .reflect_jobs
            .get(&context.tenant_id)
            .map(|jobs| {
                jobs.iter()
                    .filter(|job| {
                        job.data_subject_id == context.data_subject_id
                            && job.subject_generation == context.subject_generation
                            && job.scope_id == context.scope_id
                            && job.agent_node_id == context.agent_node_id
                    })
                    .filter(|job| {
                        !state
                            .job_meta
                            .get(&job.id)
                            .map(|meta| meta.completed)
                            .unwrap_or(false)
                    })
                    .count()
            })
            .unwrap_or(0))
    }

    async fn pending_worker_job_count(&self) -> Result<usize, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        Ok(state
            .reflect_jobs
            .values()
            .flatten()
            .filter(|job| {
                state
                    .job_meta
                    .get(&job.id)
                    .map(|meta| {
                        !meta.completed
                            && !meta.terminal
                            && meta.attempts < JOB_DEAD_LETTER_ATTEMPTS
                    })
                    .unwrap_or(true)
            })
            .count())
    }

    async fn fetch_episode(
        &self,
        context: &ResolvedMemoryContext,
        id: EpisodeId,
    ) -> Result<Option<StoredEpisode>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(state.episodes.get(&context.tenant_id).and_then(|episodes| {
            episodes
                .iter()
                .find(|episode| {
                    episode.id == id
                        && episode.data_subject_id == context.data_subject_id
                        && episode.subject_generation == context.subject_generation
                        && episode.scope_id == context.scope_id
                        && episode.agent_node_id == context.agent_node_id
                        && episode.actor_id == context.actor_id
                })
                .cloned()
        }))
    }

    async fn fetch_episodes_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[EpisodeId],
    ) -> Result<Vec<StoredEpisode>, StoreError> {
        #[cfg(test)]
        self.citation_source_batch_reads
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let ids: HashSet<_> = ids.iter().copied().collect();
        Ok(state
            .episodes
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|episode| {
                ids.contains(&episode.id)
                    && episode.data_subject_id == context.data_subject_id
                    && episode.subject_generation == context.subject_generation
                    && episode.scope_id == context.scope_id
                    && episode.agent_node_id == context.agent_node_id
                    && episode.actor_id == context.actor_id
            })
            .cloned()
            .collect())
    }

    async fn fetch_resource(
        &self,
        context: &ResolvedMemoryContext,
        id: ResourceId,
    ) -> Result<Option<StoredResource>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(state
            .resources
            .get(&context.tenant_id)
            .and_then(|resources| {
                resources
                    .iter()
                    .find(|resource| {
                        resource.id == id
                            && resource.data_subject_id == context.data_subject_id
                            && resource.subject_generation == context.subject_generation
                            && resource.scope_id == context.scope_id
                            && resource.agent_node_id == context.agent_node_id
                            && resource.actor_id == context.actor_id
                    })
                    .cloned()
            }))
    }

    async fn fetch_resources_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[ResourceId],
    ) -> Result<Vec<StoredResource>, StoreError> {
        #[cfg(test)]
        self.citation_source_batch_reads
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let ids: HashSet<_> = ids.iter().copied().collect();
        Ok(state
            .resources
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|resource| {
                ids.contains(&resource.id)
                    && resource.data_subject_id == context.data_subject_id
                    && resource.subject_generation == context.subject_generation
                    && resource.scope_id == context.scope_id
                    && resource.agent_node_id == context.agent_node_id
                    && resource.actor_id == context.actor_id
            })
            .cloned()
            .collect())
    }

    async fn stage_correction(
        &self,
        tx: &mut Self::Txn,
        correction: CorrectionWrite,
    ) -> Result<CorrectOutcome, StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        let old_unit = state
            .memory_units
            .get(&context.tenant_id)
            .ok_or(StoreError::NotFound("memory_unit"))?;
        let old_unit = old_unit
            .iter()
            .find(|unit| {
                unit.id == correction.selector.memory_unit_id
                    && unit.data_subject_id == context.data_subject_id
                    && unit.subject_generation == context.subject_generation
                    && unit.scope_id == context.scope_id
                    && unit.agent_node_id == context.agent_node_id
                    && unit.actor_id == Some(context.actor_id)
                    && unit.state != UnitState::Deleted
                    // Only the OPEN generation is correctable: re-correcting an
                    // already-superseded id must not mint a second live unit.
                    && unit.transaction_to.is_none()
            })
            .cloned()
            .ok_or(StoreError::NotFound("memory_unit"))?;
        let old_id = old_unit.id;
        let (mut replacement, remainders) = correction_rectangles_with_ids(
            &old_unit,
            &correction.correction,
            &correction.source_ref,
            &correction.observed_at,
            context.actor_id,
            &correction.now,
            &correction.unit_ids,
        )?;
        apply_compact_refresh(&mut replacement, correction.compact_refresh.as_ref());
        let new_id = replacement.id;
        let remainder_ids: Vec<UnitId> = remainders.iter().map(|unit| unit.id).collect();
        let old_embeddings: Vec<EmbeddingRow> = state
            .embeddings
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|row| row.memory_unit_id == old_id)
            .cloned()
            .collect();
        let is_retroactive =
            correction.correction.valid_from.is_some() || correction.correction.valid_to.is_some();

        {
            let InMemoryState {
                memory_units,
                memory_edges,
                ..
            } = state;
            let units = memory_units.entry(context.tenant_id).or_default();
            let edges = memory_edges.entry(context.tenant_id).or_default();
            apply_correction_transition(
                units,
                edges,
                &context,
                old_id,
                replacement,
                remainders,
                &correction.now,
            )?;
        }

        // Embed the replacement unit under the same lock as its supersedes edge
        // so corrected truth is vector-visible — mirrors the Postgres path.
        if let Some((profile, vec)) = correction.embedding.filter(|(_, vec)| !vec.is_empty()) {
            let profile_id = profile.id;
            let profiles = state
                .embedding_profiles
                .entry(context.tenant_id)
                .or_default();
            if !profiles.iter().any(|existing| existing.id == profile_id) {
                profiles.push(profile);
            }
            let stored = state.embeddings.entry(context.tenant_id).or_default();
            stored.retain(|existing| {
                !(existing.memory_unit_id == new_id && existing.embedding_profile_id == profile_id)
            });
            stored.push(EmbeddingRow {
                memory_unit_id: new_id,
                embedding_profile_id: profile_id,
                vec,
            });
        }
        let stored = state.embeddings.entry(context.tenant_id).or_default();
        for remainder_id in &remainder_ids {
            stored.extend(old_embeddings.iter().cloned().map(|mut row| {
                row.memory_unit_id = *remainder_id;
                row
            }));
        }

        let mut created = vec![new_id];
        created.extend(remainder_ids);
        Ok(CorrectResult {
            correction_id: format!("cor_{}", new_id.as_uuid()),
            superseded: vec![old_id],
            created,
            correction_kind: if is_retroactive {
                "retroactive".to_string()
            } else {
                "current".to_string()
            },
            trace_ref: None,
        })
    }

    async fn stage_invalidation(
        &self,
        tx: &mut Self::Txn,
        invalidation: InvalidationWrite,
    ) -> Result<CorrectOutcome, StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        let units = state.memory_units.entry(context.tenant_id).or_default();
        // Same open-generation finder as correction: only the single OPEN,
        // caller-owned generation may be invalidated.
        let idx = units
            .iter()
            .position(|unit| {
                unit.id == invalidation.target
                    && unit.data_subject_id == context.data_subject_id
                    && unit.subject_generation == context.subject_generation
                    && unit.scope_id == context.scope_id
                    && unit.agent_node_id == context.agent_node_id
                    && unit.actor_id == Some(context.actor_id)
                    && unit.state != UnitState::Deleted
                    && unit.transaction_to.is_none()
            })
            .ok_or(StoreError::NotFound("memory_unit"))?;
        let old = units[idx].clone();
        // Close the predecessor at transaction time.
        units[idx].state = UnitState::Superseded;
        units[idx].transaction_to = Some(invalidation.now.clone());
        // Append the current, bodyless tombstone with the same stable identity.
        let mut tombstone = old.clone();
        tombstone.id = invalidation.tombstone_id;
        tombstone.state = UnitState::Invalidated;
        tombstone.body = String::new();
        tombstone.compact = None;
        tombstone.contextual_chunks = Vec::new();
        tombstone.invalidation = Some(memphant_types::InvalidationMarker {
            kind: invalidation.reason_kind,
            reason: invalidation.reason.clone(),
        });
        tombstone.source_ref = invalidation.source_ref.clone();
        tombstone.observed_at = invalidation.observed_at.clone();
        tombstone.transaction_from = Some(invalidation.now.clone());
        tombstone.transaction_to = None;
        units.push(tombstone);

        let edges = state.memory_edges.entry(context.tenant_id).or_default();
        edges.push(StoredMemoryEdge {
            id: EdgeId::new(),
            tenant_id: context.tenant_id,
            scope_id: context.scope_id,
            src_id: invalidation.tombstone_id,
            dst_id: old.id,
            kind: MemoryEdgeKind::Supersedes,
            transaction_from: Some(invalidation.now.clone()),
            transaction_to: None,
        });

        Ok(CorrectResult {
            correction_id: format!("inv_{}", invalidation.tombstone_id.as_uuid()),
            superseded: vec![old.id],
            created: vec![invalidation.tombstone_id],
            correction_kind: "invalidation".to_string(),
            trace_ref: None,
        })
    }

    async fn stage_forget(
        &self,
        tx: &mut Self::Txn,
        forget: ForgetWrite,
    ) -> Result<ForgetOutcome, StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        let authorized = match forget.target {
            ForgetTarget::MemoryUnit(id) => state
                .memory_units
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .any(|unit| {
                    unit.id == id
                        && unit.data_subject_id == context.data_subject_id
                        && unit.subject_generation == context.subject_generation
                        && unit.scope_id == context.scope_id
                        && unit.agent_node_id == context.agent_node_id
                        && unit.actor_id == Some(context.actor_id)
                }),
            ForgetTarget::Episode(id) => state
                .episodes
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .any(|episode| {
                    episode.id == id
                        && episode.data_subject_id == context.data_subject_id
                        && episode.subject_generation == context.subject_generation
                        && episode.scope_id == context.scope_id
                        && episode.agent_node_id == context.agent_node_id
                        && episode.actor_id == context.actor_id
                }),
            ForgetTarget::Resource(id) => state
                .resources
                .get(&context.tenant_id)
                .into_iter()
                .flatten()
                .any(|resource| {
                    resource.id == id
                        && resource.data_subject_id == context.data_subject_id
                        && resource.subject_generation == context.subject_generation
                        && resource.scope_id == context.scope_id
                        && resource.agent_node_id == context.agent_node_id
                        && resource.actor_id == context.actor_id
                }),
        };
        if !authorized {
            return Err(StoreError::NotFound("forget target"));
        }
        let deletion_generation = state
            .deletion_generation
            .entry((context.tenant_id, context.data_subject_id))
            .or_default();
        *deletion_generation = deletion_generation.saturating_add(1);
        let deletion_generation = *deletion_generation;

        let tombstone = match forget.target {
            ForgetTarget::MemoryUnit(id) => (SourceKindKey::MemoryUnit, id.as_uuid()),
            ForgetTarget::Episode(id) => (SourceKindKey::Episode, id.as_uuid()),
            ForgetTarget::Resource(id) => (SourceKindKey::Resource, id.as_uuid()),
        };
        state.forgotten_sources.insert((
            context.tenant_id,
            context.data_subject_id,
            context.subject_generation,
            context.scope_id,
            context.agent_node_id,
            tombstone.0,
            tombstone.1,
        ));

        if let ForgetTarget::Episode(episode_id) = forget.target
            && let Some(episodes) = state.episodes.get_mut(&context.tenant_id)
        {
            episodes.retain(|episode| {
                episode.id != episode_id
                    || episode.data_subject_id != context.data_subject_id
                    || episode.subject_generation != context.subject_generation
                    || episode.scope_id != context.scope_id
                    || episode.agent_node_id != context.agent_node_id
                    || episode.actor_id != context.actor_id
            });
        }

        if let ForgetTarget::MemoryUnit(id) = forget.target {
            let invalidated_units = {
                let InMemoryState {
                    memory_units,
                    memory_edges,
                    ..
                } = state;
                let units = memory_units.entry(context.tenant_id).or_default();
                let edges = memory_edges.entry(context.tenant_id).or_default();
                apply_unit_forget_transition(
                    units,
                    edges,
                    &context,
                    id,
                    &forget.now,
                    Some(deletion_generation),
                )?
            };
            if let Some(embeddings) = state.embeddings.get_mut(&context.tenant_id) {
                embeddings.retain(|row| !invalidated_units.contains(&row.memory_unit_id));
            }
            return Ok(ForgetOutcome {
                deletion_generation,
                invalidated_units,
            });
        }

        // Episode/resource forget seeds from the source columns, then must
        // still cascade to supersedes
        // DESCENDANTS: a correction replacement carries correction provenance
        // (source_episode_id = None, pinned by correction_provenance.rs), so
        // the source-column match alone lets corrected content survive the
        // episode's erasure. Descendants only — an ancestor a corrected unit
        // superseded came from a different source and is not this target's
        // content.
        let mut memory_lineage = HashSet::new();
        match forget.target {
            ForgetTarget::MemoryUnit(_) => unreachable!(),
            ForgetTarget::Episode(_) | ForgetTarget::Resource(_) => {
                for unit in state
                    .memory_units
                    .get(&context.tenant_id)
                    .into_iter()
                    .flatten()
                    .filter(|unit| {
                        unit.data_subject_id == context.data_subject_id
                            && unit.subject_generation == context.subject_generation
                            && unit.scope_id == context.scope_id
                            && unit.agent_node_id == context.agent_node_id
                            && unit.actor_id == Some(context.actor_id)
                            && match forget.target {
                                ForgetTarget::Episode(id) => unit.source_episode_id == Some(id),
                                ForgetTarget::Resource(id) => unit.source_resource_id == Some(id),
                                ForgetTarget::MemoryUnit(_) => unreachable!(),
                            }
                    })
                {
                    memory_lineage.insert(unit.id);
                }
            }
        }
        if let (Some(edges), Some(units)) = (
            state.memory_edges.get(&context.tenant_id),
            state.memory_units.get(&context.tenant_id),
        ) {
            let actor_owned_ids: HashSet<_> = units
                .iter()
                .filter(|unit| unit_matches_context(unit, &context))
                .map(|unit| unit.id)
                .collect();
            loop {
                let before = memory_lineage.len();
                for edge in edges.iter().filter(|edge| {
                    edge.tenant_id == context.tenant_id
                        && edge.scope_id == context.scope_id
                        && edge.kind == MemoryEdgeKind::Supersedes
                }) {
                    if memory_lineage.contains(&edge.dst_id)
                        && actor_owned_ids.contains(&edge.src_id)
                    {
                        memory_lineage.insert(edge.src_id);
                    }
                }
                if memory_lineage.len() == before {
                    break;
                }
            }
        }

        let mut invalidated_units: Vec<UnitId> = Vec::new();
        if let Some(units) = state.memory_units.get_mut(&context.tenant_id) {
            for unit in units.iter_mut().filter(|unit| {
                unit.data_subject_id == context.data_subject_id
                    && unit.subject_generation == context.subject_generation
                    && unit.scope_id == context.scope_id
                    && unit.agent_node_id == context.agent_node_id
                    && unit.actor_id == Some(context.actor_id)
                    && unit.state != UnitState::Deleted
                    && memory_lineage.contains(&unit.id)
            }) {
                unit.state = UnitState::Deleted;
                unit.deletion_generation = Some(deletion_generation);
                unit.transaction_to = Some(forget.now.clone());
                scrub_unit_content(unit);
                invalidated_units.push(unit.id);
            }
        }
        invalidated_units.extend(delete_composed_dependents(
            state,
            &context,
            &invalidated_units,
            deletion_generation,
            &forget.now,
        ));

        // Forgotten embeddings are hard-deleted with their units (Pg parity).
        if let Some(embeddings) = state.embeddings.get_mut(&context.tenant_id) {
            embeddings.retain(|row| !invalidated_units.contains(&row.memory_unit_id));
        }

        Ok(ForgetOutcome {
            deletion_generation,
            invalidated_units,
        })
    }

    async fn stage_review_events(
        &self,
        tx: &mut Self::Txn,
        events: Vec<ReviewEventRow>,
    ) -> Result<(), StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        let trace_ids: HashMap<_, HashSet<_>> = state
            .retrieval_traces
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|trace| {
                trace.data_subject_id == context.data_subject_id
                    && trace.subject_generation == context.subject_generation
                    && trace.scope_id == context.scope_id
                    && trace.actor_id == context.actor_id
                    && trace.agent_node_id == context.agent_node_id
            })
            .map(|trace| {
                (
                    trace.id,
                    // Derived source ids are valid mark targets: record_mark
                    // expands synthetic items into them before staging.
                    trace
                        .context_items
                        .iter()
                        .flat_map(|item| {
                            std::iter::once(item.unit_id)
                                .chain(item.derived_from_unit_ids.iter().copied())
                        })
                        .collect::<HashSet<_>>(),
                )
            })
            .collect();
        for event in &events {
            let Some(whitelist) = trace_ids.get(&event.trace_id) else {
                return Err(StoreError::NotFound("retrieval trace"));
            };
            if event.tenant_id != context.tenant_id
                || event.used_ids.iter().any(|id| !whitelist.contains(id))
            {
                return Err(StoreError::NotFound("retrieval trace review whitelist"));
            }
        }
        let stored = state.review_events.entry(context.tenant_id).or_default();
        for event in events {
            if !stored.iter().any(|existing| {
                existing.trace_id == event.trace_id && existing.caller_id == event.caller_id
            }) {
                stored.push(event);
            }
        }
        Ok(())
    }

    async fn store_trace(
        &self,
        context: &ResolvedMemoryContext,
        trace: RetrievalTrace,
    ) -> Result<(), StoreError> {
        if trace.tenant_id != context.tenant_id
            || trace.data_subject_id != context.data_subject_id
            || trace.subject_generation != context.subject_generation
            || trace.scope_id != context.scope_id
            || trace.actor_id != context.actor_id
            || trace.agent_node_id != context.agent_node_id
            || trace.policy_revision != context.policy_revision
        {
            return Err(StoreError::Conflict("trace context mismatch".to_string()));
        }
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let traces = state.retrieval_traces.entry(context.tenant_id).or_default();
        if let Some(existing) = traces.iter_mut().find(|existing| existing.id == trace.id) {
            *existing = trace;
        } else {
            traces.push(trace);
        }
        Ok(())
    }

    async fn trace_by_id(
        &self,
        context: &ResolvedMemoryContext,
        id: TraceId,
    ) -> Result<Option<RetrievalTrace>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(state
            .retrieval_traces
            .get(&context.tenant_id)
            .and_then(|traces| {
                traces
                    .iter()
                    .find(|trace| {
                        trace.id == id
                            && trace.data_subject_id == context.data_subject_id
                            && trace.subject_generation == context.subject_generation
                            && trace.scope_id == context.scope_id
                            && trace.actor_id == context.actor_id
                            && trace.agent_node_id == context.agent_node_id
                    })
                    .cloned()
            }))
    }

    async fn scope_memory_page(
        &self,
        context: &ResolvedMemoryContext,
        cursor: Option<UnitId>,
        limit: usize,
    ) -> Result<ScopePage, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let mut units: Vec<_> = state
            .memory_units
            .get(&context.tenant_id)
            .map(|units| {
                units
                    .iter()
                    .filter(|unit| {
                        unit.data_subject_id == context.data_subject_id
                            && unit.subject_generation == context.subject_generation
                            && unit.scope_id == context.scope_id
                            && unit.agent_node_id == context.agent_node_id
                            && context.allows(unit.kind, unit.scope_id, unit.agent_node_id)
                    })
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
        units.sort_by_key(|unit| unit.id.as_uuid());
        if let Some(cursor) = cursor {
            units.retain(|unit| unit.id.as_uuid() > cursor.as_uuid());
        }
        let has_more = units.len() > limit;
        units.truncate(limit.max(1));
        let next_cursor = has_more.then(|| units.last().map(|unit| unit.id)).flatten();
        Ok(ScopePage {
            items: units,
            next_cursor,
            has_more,
        })
    }

    async fn canonical_projection_units(
        &self,
        context: &ResolvedMemoryContext,
        evaluated_at: &str,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        Ok(in_memory_canonical_projection_units(
            &state,
            context,
            evaluated_at,
        ))
    }

    async fn canonical_projection_units_in_tx(
        &self,
        tx: &mut Self::Txn,
        evaluated_at: &str,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        Ok(in_memory_canonical_projection_units(
            state,
            &context,
            evaluated_at,
        ))
    }

    async fn claim_reflect_jobs(
        &self,
        filter: JobFilter,
        limit: usize,
    ) -> Result<Vec<ReflectJobRow>, StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        let lane = |job: &QueuedReflectJob| {
            (
                job.tenant_id,
                job.data_subject_id,
                job.subject_generation,
                job.scope_id,
                job.agent_node_id,
            )
        };
        let candidates: Vec<QueuedReflectJob> = state
            .reflect_jobs
            .iter()
            .filter(|(tenant, _)| filter.tenant.is_none_or(|wanted| **tenant == wanted))
            .flat_map(|(_, jobs)| jobs.iter())
            .filter(|job| filter.scope.is_none_or(|wanted| job.scope_id == wanted))
            .filter(|job| {
                state.context_bindings.values().any(|(_, binding)| {
                    binding.subject_id == job.data_subject_id
                        && binding.actor_id == job.actor_id
                        && binding.scope_id == job.scope_id
                        && binding.agent_node_id == job.agent_node_id
                        && binding.subject_generation == job.subject_generation
                })
            })
            .filter(|job| {
                let meta = state.job_meta.get(&job.id).copied().unwrap_or_default();
                !meta.completed
                    && !meta.terminal
                    && !meta.claimed
                    && meta.attempts < JOB_DEAD_LETTER_ATTEMPTS
            })
            .filter(|job| {
                let wanted_lane = lane(job);
                !state.reflect_jobs.values().flatten().any(|other| {
                    lane(other) == wanted_lane
                        && state
                            .job_meta
                            .get(&other.id)
                            .is_some_and(|meta| meta.claimed && !meta.completed)
                })
            })
            .take(limit)
            .cloned()
            .collect();
        let mut claimed = Vec::new();
        for job in candidates {
            let meta = state.job_meta.entry(job.id).or_default();
            meta.claimed = true;
            meta.attempts = meta.attempts.saturating_add(1);
            let attempts = meta.attempts;
            claimed.push(ReflectJobRow {
                job,
                attempts,
                claim_generation: meta.claim_generation,
            });
        }
        Ok(claimed)
    }

    async fn complete_reflect_job(
        &self,
        claim: &ReflectJobRow,
    ) -> Result<ClaimMutationOutcome, StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        let exact_completed = state
            .reflect_jobs
            .get(&claim.job.tenant_id)
            .is_some_and(|jobs| jobs.contains(&claim.job))
            && state.job_meta.get(&claim.job.id).is_some_and(|meta| {
                meta.completed
                    && meta.attempts == claim.attempts
                    && meta.claim_generation == claim.claim_generation
            });
        if exact_completed {
            return Ok(ClaimMutationOutcome::Applied);
        }
        if !state.claim_is_current(claim) {
            return Ok(ClaimMutationOutcome::Stale);
        }
        let meta = state.job_meta.entry(claim.job.id).or_default();
        meta.completed = true;
        meta.claimed = false;
        Ok(ClaimMutationOutcome::Applied)
    }

    async fn requeue_reflect_job_with_compiler(
        &self,
        claim: &ReflectJobRow,
        compiler_version: &str,
    ) -> Result<ClaimMutationOutcome, StoreError> {
        if compiler_version.trim().is_empty() {
            return Err(StoreError::Conflict(
                "reflect compiler identity must not be empty".to_string(),
            ));
        }
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        if !state.claim_is_current(claim) {
            return Ok(ClaimMutationOutcome::Stale);
        }
        let jobs = state
            .reflect_jobs
            .get_mut(&claim.job.tenant_id)
            .ok_or(StoreError::NotFound("reflect_job"))?;
        let same_source = |job: &QueuedReflectJob| {
            job.id != claim.job.id
                && job.tenant_id == claim.job.tenant_id
                && job.data_subject_id == claim.job.data_subject_id
                && job.subject_generation == claim.job.subject_generation
                && job.scope_id == claim.job.scope_id
                && job.agent_node_id == claim.job.agent_node_id
                && job.actor_id == claim.job.actor_id
                && job.kind == claim.job.kind
                && job.episode_id == claim.job.episode_id
                && job.resource_id == claim.job.resource_id
                && job.compiler_version == compiler_version
        };
        let stale_index = jobs
            .iter()
            .position(|job| job.id == claim.job.id)
            .ok_or(StoreError::NotFound("reflect_job"))?;
        let authoritative_index = jobs.iter().position(same_source);
        if let Some(authoritative_index) = authoritative_index {
            jobs.swap(stale_index, authoritative_index);
            let meta = state.job_meta.entry(claim.job.id).or_default();
            meta.claimed = false;
            meta.terminal = true;
            meta.claim_generation = meta.claim_generation.saturating_add(1);
        } else {
            jobs[stale_index].compiler_version = compiler_version.to_string();
            let meta = state.job_meta.entry(claim.job.id).or_default();
            meta.attempts = 0;
            meta.claim_generation = meta.claim_generation.saturating_add(1);
            meta.claimed = false;
            meta.completed = false;
            meta.terminal = false;
        }
        state
            .prepared_structured_state
            .remove(&(claim.job.tenant_id, claim.job.id));
        Ok(ClaimMutationOutcome::Applied)
    }

    async fn fetch_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
        expected_batches: &[StructuredStateRequest],
        provider_identity: &StructuredStateProviderIdentity,
    ) -> Result<Option<Vec<StructuredExtractionPacket>>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        if !state.claim_is_current(claim) {
            return Ok(None);
        }
        match state
            .prepared_structured_state
            .get(&(claim.job.tenant_id, claim.job.id))
        {
            Some(ReflectJobResult::Prepared {
                input_manifest_sha256,
                extraction_packets,
            }) => {
                validate_prepared_structured_state_binding(
                    expected_batches,
                    provider_identity,
                    input_manifest_sha256,
                    extraction_packets,
                )?;
                Ok(Some(extraction_packets.clone()))
            }
            Some(ReflectJobResult::Completed { .. }) | None => Ok(None),
        }
    }

    async fn store_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
        expected_batches: &[StructuredStateRequest],
        provider_identity: &StructuredStateProviderIdentity,
        extraction_packets: Vec<StructuredExtractionPacket>,
    ) -> Result<(), StoreError> {
        let input_manifest_sha256 = structured_input_manifest_sha256(expected_batches)?;
        validate_prepared_structured_state_binding(
            expected_batches,
            provider_identity,
            &input_manifest_sha256,
            &extraction_packets,
        )?;
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        if !state.claim_is_current(claim) {
            return Ok(());
        }
        state
            .prepared_structured_state
            .entry((claim.job.tenant_id, claim.job.id))
            .or_insert(ReflectJobResult::Prepared {
                input_manifest_sha256,
                extraction_packets,
            });
        Ok(())
    }

    async fn release_reflect_job(
        &self,
        claim: &ReflectJobRow,
        _retry_after_seconds: u64,
        _error: String,
    ) -> Result<(), StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        if !state.claim_is_current(claim) {
            return Ok(());
        }
        state.job_meta.entry(claim.job.id).or_default().claimed = false;
        Ok(())
    }

    async fn fail_reflect_job(
        &self,
        claim: &ReflectJobRow,
        _error: String,
    ) -> Result<(), StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        if !state.claim_is_current(claim) {
            return Ok(());
        }
        let meta = state.job_meta.entry(claim.job.id).or_default();
        meta.claimed = false;
        meta.terminal = true;
        Ok(())
    }

    async fn stage_compiled_units(
        &self,
        tx: &mut Self::Txn,
        claim: Option<&ReflectJobRow>,
        write: CompiledWrite,
    ) -> Result<ClaimMutationOutcome, StoreError> {
        let context = tx.context.clone();
        let state = self.staged_state(tx)?;
        if let Some(claim) = claim {
            if !state.claim_is_current(claim) {
                return Ok(ClaimMutationOutcome::Stale);
            }
            if claim.job.tenant_id != context.tenant_id
                || claim.job.data_subject_id != context.data_subject_id
                || claim.job.subject_generation != context.subject_generation
                || claim.job.scope_id != context.scope_id
                || claim.job.agent_node_id != context.agent_node_id
                || claim.job.actor_id != context.actor_id
                || claim.job.id != write.job_id
                || claim.job.compiler_version != write.compiler_version
            {
                return Err(StoreError::Conflict(
                    "reflect claim does not match memory context".to_string(),
                ));
            }
        }
        let tenant = context.tenant_id;
        let owner_key = (tenant, write.job_id, write.compiler_version.clone());
        if let Some(owner) = state.reflect_trace_owners.get(&owner_key) {
            let requested = (
                context.data_subject_id,
                context.subject_generation,
                context.scope_id,
                context.agent_node_id,
                context.actor_id,
            );
            if *owner != requested {
                return Err(StoreError::Conflict(
                    "reflect job identity belongs to another memory context".to_string(),
                ));
            }
            return Ok(ClaimMutationOutcome::Applied);
        }

        let owned_unit = |unit: &StoredMemoryUnit| {
            unit.tenant_id == context.tenant_id
                && unit.data_subject_id == context.data_subject_id
                && unit.subject_generation == context.subject_generation
                && unit.scope_id == context.scope_id
                && unit.agent_node_id == context.agent_node_id
                && unit.actor_id == Some(context.actor_id)
        };
        if write.new_units.iter().any(|unit| !owned_unit(unit)) {
            return Err(StoreError::Conflict(
                "compiled unit does not match memory context".to_string(),
            ));
        }
        let existing_owned_ids: HashSet<UnitId> = state
            .memory_units
            .get(&tenant)
            .into_iter()
            .flatten()
            .filter(|unit| owned_unit(unit))
            .map(|unit| unit.id)
            .collect();
        if write
            .unit_updates
            .iter()
            .any(|update| !existing_owned_ids.contains(&update.id))
        {
            return Err(StoreError::Conflict(
                "compiled update does not match memory context".to_string(),
            ));
        }
        let new_ids: HashSet<UnitId> = write.new_units.iter().map(|unit| unit.id).collect();
        if write.new_edges.iter().any(|edge| {
            edge.tenant_id != context.tenant_id
                || edge.scope_id != context.scope_id
                || (!new_ids.contains(&edge.src_id) && !existing_owned_ids.contains(&edge.src_id))
                || (!new_ids.contains(&edge.dst_id) && !existing_owned_ids.contains(&edge.dst_id))
        }) {
            return Err(StoreError::Conflict(
                "compiled edge does not match memory context".to_string(),
            ));
        }
        if write.citations.iter().any(|citation| {
            citation.tenant_id != context.tenant_id
                || citation.data_subject_id != context.data_subject_id
                || citation.subject_generation != context.subject_generation
                || citation.scope_id != context.scope_id
                || citation.agent_node_id != context.agent_node_id
                || !new_ids.contains(&citation.memory_unit_id)
                || (citation.episode_id.is_some() && citation.resource_id.is_some())
        }) {
            return Err(StoreError::Conflict(
                "compiled citation does not match memory context".to_string(),
            ));
        }

        // No-resurrection: an OPEN invalidation tombstone with the same stable
        // identity (owner + fact_key + kind) blocks re-creating that identity
        // through ANY ingress — direct remember, reflect, replay. Only
        // `correct_memory` may close a tombstone. Matches on stable identity;
        // because the compact key is body-independent, a re-remember of the same
        // trigger+kind is caught regardless of body.
        if let Some(existing) = state.memory_units.get(&tenant) {
            for new_unit in write.new_units.iter() {
                let Some(fact_key) = new_unit.fact_key.as_deref() else {
                    continue;
                };
                let blocked = existing.iter().any(|unit| {
                    unit.state == UnitState::Invalidated
                        && unit.transaction_to.is_none()
                        && owned_unit(unit)
                        && unit.kind == new_unit.kind
                        && unit.fact_key.as_deref() == Some(fact_key)
                });
                if blocked {
                    return Err(StoreError::Conflict(
                        "an open invalidation tombstone blocks re-creating this memory; \
                         use correct_memory to revise it instead"
                            .to_string(),
                    ));
                }
            }
        }

        for update in &write.unit_updates {
            if let Some(unit) = state
                .memory_units
                .entry(tenant)
                .or_default()
                .iter_mut()
                .find(|unit| unit.id == update.id)
            {
                unit.state = update.state;
                unit.transaction_to = update.transaction_to.clone();
                if let Some(reinforced_at) = &update.reinforced_at {
                    unit.reinforcement_count = unit.reinforcement_count.saturating_add(1);
                    unit.last_reinforced_at = Some(reinforced_at.clone());
                }
            }
        }

        // Forgotten-source tombstones block re-derivation durably: any newly
        // compiled unit whose source was forgotten is refused.
        let mut admitted_ids = HashSet::new();
        let mut admitted_units = Vec::new();
        for unit in write.new_units {
            if state.is_forgotten_source(&unit) {
                continue;
            }
            admitted_ids.insert(unit.id);
            admitted_units.push(unit);
        }
        let existing_ids: HashSet<UnitId> = state
            .memory_units
            .get(&tenant)
            .map(|units| units.iter().map(|unit| unit.id).collect())
            .unwrap_or_default();
        state
            .memory_units
            .entry(tenant)
            .or_default()
            .extend(admitted_units);
        state
            .memory_edges
            .entry(tenant)
            .or_default()
            .extend(write.new_edges.into_iter().filter(|edge| {
                let src_ok =
                    admitted_ids.contains(&edge.src_id) || existing_ids.contains(&edge.src_id);
                let dst_ok =
                    admitted_ids.contains(&edge.dst_id) || existing_ids.contains(&edge.dst_id);
                src_ok && dst_ok
            }));
        state.citations.entry(tenant).or_default().extend(
            write
                .citations
                .into_iter()
                .filter(|citation| admitted_ids.contains(&citation.memory_unit_id)),
        );
        state
            .reflect_traces
            .entry(tenant)
            .or_default()
            .push(write.trace);
        state.reflect_trace_owners.insert(
            (tenant, write.job_id, write.compiler_version.clone()),
            (
                context.data_subject_id,
                context.subject_generation,
                context.scope_id,
                context.agent_node_id,
                context.actor_id,
            ),
        );

        // Embedding write-through under the same lock as the units, admitted
        // units only — mirrors the Postgres persist path.
        if let Some(profile) = write.embedding_profile {
            let rows: Vec<EmbeddingRow> = write
                .embeddings
                .into_iter()
                .filter(|row| admitted_ids.contains(&row.memory_unit_id) && !row.vec.is_empty())
                .collect();
            if !rows.is_empty() {
                let profile_id = profile.id;
                let profiles = state.embedding_profiles.entry(tenant).or_default();
                if !profiles.iter().any(|existing| existing.id == profile_id) {
                    profiles.push(profile);
                }
                let stored = state.embeddings.entry(tenant).or_default();
                for row in rows {
                    stored.retain(|existing| {
                        !(existing.memory_unit_id == row.memory_unit_id
                            && existing.embedding_profile_id == row.embedding_profile_id)
                    });
                    stored.push(row);
                }
            }
        }
        if claim.is_some() {
            let meta = state.job_meta.entry(write.job_id).or_default();
            meta.completed = true;
            meta.claimed = false;
        }
        Ok(ClaimMutationOutcome::Applied)
    }

    async fn fetch_reflect_trace(
        &self,
        context: &ResolvedMemoryContext,
        job_id: JobId,
        compiler_version: &str,
    ) -> Result<Option<ReflectTrace>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let owner = state.reflect_trace_owners.get(&(
            context.tenant_id,
            job_id,
            compiler_version.to_string(),
        ));
        let requested = (
            context.data_subject_id,
            context.subject_generation,
            context.scope_id,
            context.agent_node_id,
            context.actor_id,
        );
        match owner {
            // Fresh identity: no completed trace for this tenant/job/compiler.
            None => return Ok(None),
            // Same identity replayed: fall through to return the existing trace
            // (idempotent replay preserved).
            Some(existing) if *existing == requested => {}
            // A different subject/scope in the same tenant is replaying a job
            // identity another context owns. Fail closed to match the Postgres
            // store, whose (tenant_id, id) primary key rejects the second insert.
            Some(_) => {
                return Err(StoreError::Conflict(
                    "reflect job identity belongs to another memory context".to_string(),
                ));
            }
        }
        Ok(state
            .reflect_traces
            .get(&context.tenant_id)
            .and_then(|traces| {
                traces
                    .iter()
                    .find(|trace| {
                        trace.job_id == job_id && trace.compiler_version == compiler_version
                    })
                    .cloned()
            }))
    }

    async fn upsert_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        rows: Vec<EmbeddingRow>,
    ) -> Result<(), StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let owned_ids: HashSet<UnitId> = state
            .memory_units
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|unit| unit_matches_context(unit, context))
            .map(|unit| unit.id)
            .collect();
        if rows
            .iter()
            .any(|row| !owned_ids.contains(&row.memory_unit_id))
        {
            return Err(StoreError::PolicyDenied(
                "embedding unit does not match context".to_string(),
            ));
        }
        let stored = state.embeddings.entry(context.tenant_id).or_default();
        for row in rows {
            stored.retain(|existing| {
                !(existing.memory_unit_id == row.memory_unit_id
                    && existing.embedding_profile_id == row.embedding_profile_id)
            });
            stored.push(row);
        }
        Ok(())
    }

    async fn upsert_embedding_profile(
        &self,
        tenant: TenantId,
        profile: EmbeddingProfileRow,
    ) -> Result<(), StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        let profiles = state.embedding_profiles.entry(tenant).or_default();
        if !profiles.iter().any(|existing| existing.id == profile.id) {
            profiles.push(profile);
        }
        Ok(())
    }

    async fn fetch_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
    ) -> Result<Vec<EmbeddingRow>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        state.validate_context(context)?;
        let owned_ids: HashSet<UnitId> = state
            .memory_units
            .get(&context.tenant_id)
            .into_iter()
            .flatten()
            .filter(|unit| unit_matches_context(unit, context) && unit_ids.contains(&unit.id))
            .map(|unit| unit.id)
            .collect();
        Ok(state
            .embeddings
            .get(&context.tenant_id)
            .map(|rows| {
                rows.iter()
                    .filter(|row| owned_ids.contains(&row.memory_unit_id))
                    .cloned()
                    .collect()
            })
            .unwrap_or_default())
    }

    async fn lookup_api_key(&self, key_hash: &str) -> Result<Option<ApiKeyRow>, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        Ok(state
            .api_keys
            .iter()
            .find(|row| row.key_hash == key_hash)
            .cloned())
    }

    async fn resolve_context_binding(
        &self,
        tenant: TenantId,
        client_ref: String,
        mut request: ContextBindingRequest,
    ) -> Result<ContextBindingResponse, StoreError> {
        let mut state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        canonicalize_access_policies(&mut request.access_policies);
        let key = (tenant, client_ref);
        if let Some((existing_request, existing_response)) =
            state.context_bindings.get(&key).cloned()
        {
            let mut existing_identity = existing_request.clone();
            existing_identity.access_policies.clear();
            let mut requested_identity = request.clone();
            requested_identity.access_policies.clear();
            if existing_identity != requested_identity {
                return Err(StoreError::Conflict(
                    "context binding identity, kind, and parent are immutable".to_string(),
                ));
            }
            if existing_request == request {
                return Ok(existing_response);
            }
            validate_in_memory_access_policies(
                &state,
                tenant,
                &request,
                existing_response.agent_level,
            )?;
            let mut updated_response = existing_response;
            updated_response.policy_revision = context_policy_revision(&request.access_policies)?;
            state
                .context_bindings
                .insert(key, (request, updated_response.clone()));
            return Ok(updated_response);
        }
        let mut requested_identity = request.clone();
        requested_identity.access_policies.clear();
        if state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
            .any(|(_, (bound, _))| {
                let mut bound_identity = bound.clone();
                bound_identity.access_policies.clear();
                bound_identity == requested_identity
            })
        {
            return Err(StoreError::Conflict(
                "context identity is already registered under another client_ref".to_string(),
            ));
        }
        if state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
            .any(|(_, (bound, _))| {
                bound.subject.external_ref == request.subject.external_ref
                    && bound.agent_node.external_ref == request.agent_node.external_ref
            })
        {
            return Err(StoreError::Conflict(
                "agent node is already registered under another client_ref".to_string(),
            ));
        }

        for (_, (bound, _)) in state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
        {
            if bound.subject.external_ref == request.subject.external_ref
                && bound.subject.kind != request.subject.kind
            {
                return Err(StoreError::Conflict(
                    "subject kind is immutable".to_string(),
                ));
            }
            if bound.subject.external_ref == request.subject.external_ref
                && bound.actor.external_ref == request.actor.external_ref
                && bound.actor.kind != request.actor.kind
            {
                return Err(StoreError::Conflict("actor kind is immutable".to_string()));
            }
            if bound.subject.external_ref == request.subject.external_ref
                && bound.scope.external_ref == request.scope.external_ref
                && (bound.scope.kind != request.scope.kind
                    || bound.scope.parent_external_ref != request.scope.parent_external_ref)
            {
                return Err(StoreError::Conflict(
                    "scope kind or parent is immutable".to_string(),
                ));
            }
            if bound.subject.external_ref == request.subject.external_ref
                && bound.agent_node.external_ref == request.agent_node.external_ref
                && (bound.agent_node.parent_external_ref != request.agent_node.parent_external_ref
                    || bound.scope.external_ref != request.scope.external_ref)
            {
                return Err(StoreError::Conflict(
                    "agent node parent or scope is immutable".to_string(),
                ));
            }
        }

        let agent_level = match request.agent_node.parent_external_ref.as_deref() {
            None => 0,
            Some(parent_ref) => state
                .context_bindings
                .iter()
                .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
                .find(|(_, (bound, _))| {
                    bound.subject.external_ref == request.subject.external_ref
                        && bound.agent_node.external_ref == parent_ref
                })
                .map(|(_, (_, response))| response.agent_level.saturating_add(1))
                .ok_or(StoreError::NotFound("parent agent node"))?,
        };
        if let Some(parent_ref) = request.scope.parent_external_ref.as_deref()
            && !state
                .context_bindings
                .iter()
                .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
                .any(|(_, (bound, _))| {
                    bound.subject.external_ref == request.subject.external_ref
                        && bound.scope.external_ref == parent_ref
                })
        {
            return Err(StoreError::NotFound("parent scope"));
        }

        validate_in_memory_access_policies(&state, tenant, &request, agent_level)?;

        let policy_revision = context_policy_revision(&request.access_policies)?;
        let existing_subject = state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
            .find(|(_, (bound, _))| bound.subject.external_ref == request.subject.external_ref)
            .map(|(_, (_, response))| (response.subject_id, response.subject_generation));
        let existing_actor = state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
            .find(|(_, (bound, _))| {
                bound.subject.external_ref == request.subject.external_ref
                    && bound.actor.external_ref == request.actor.external_ref
            })
            .map(|(_, (_, response))| response.actor_id);
        let existing_scope = state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
            .find(|(_, (bound, _))| {
                bound.subject.external_ref == request.subject.external_ref
                    && bound.scope.external_ref == request.scope.external_ref
            })
            .map(|(_, (_, response))| response.scope_id);
        let response = ContextBindingResponse {
            subject_id: existing_subject
                .map(|(subject_id, _)| subject_id)
                .unwrap_or_default(),
            actor_id: existing_actor.unwrap_or_default(),
            scope_id: existing_scope.unwrap_or_default(),
            agent_node_id: memphant_types::AgentNodeId::new(),
            agent_level,
            policy_revision: policy_revision.clone(),
            subject_generation: existing_subject
                .map(|(_, generation)| generation)
                .unwrap_or(0),
        };
        state
            .context_bindings
            .insert(key, (request, response.clone()));
        Ok(response)
    }

    async fn resolve_memory_context(
        &self,
        tenant: TenantId,
        subject_id: SubjectId,
        actor_id: ActorId,
        scope_id: ScopeId,
        agent_node_id: AgentNodeId,
    ) -> Result<ResolvedMemoryContext, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        if let Some(tombstone) = state.subject_tombstones.get(&(tenant, subject_id)) {
            if tombstone.generation == 0 || tombstone.erased_at.is_empty() {
                return Err(StoreError::Backend(
                    "invalid subject erasure tombstone".to_string(),
                ));
            }
            return Err(StoreError::SubjectErased);
        }
        let (binding, response) = state
            .context_bindings
            .iter()
            .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
            .map(|(_, value)| value)
            .find(|(_, response)| {
                response.subject_id == subject_id
                    && response.actor_id == actor_id
                    && response.scope_id == scope_id
                    && response.agent_node_id == agent_node_id
            })
            .ok_or(StoreError::NotFound("memory context"))?;

        let mut sources_by_kind = BTreeMap::new();
        // `MemoryKind::ALL`, never a hand-written list: a kind omitted here
        // resolves to no source, so `context.allows` denies it and the unit is
        // invisible to the compiler snapshot AND to recall. That is how minting
        // `preference` first failed — the write landed and nothing could see it.
        for kind in MemoryKind::ALL {
            let exact_allowed = agent_level_allows_memory_kind(response.agent_level, kind);
            sources_by_kind.insert(
                kind,
                if exact_allowed {
                    vec![ResolvedMemorySource {
                        scope_id,
                        agent_node_id,
                    }]
                } else {
                    Vec::new()
                },
            );
        }

        for policy in &binding.access_policies {
            let (_, source_response) = state
                .context_bindings
                .iter()
                .filter(|((bound_tenant, _), _)| *bound_tenant == tenant)
                .map(|(_, value)| value)
                .find(|(candidate, candidate_response)| {
                    candidate_response.subject_id == subject_id
                        && candidate.scope.external_ref == policy.source_scope_external_ref()
                        && candidate.agent_node.external_ref
                            == policy.source_agent_node_external_ref()
                })
                .ok_or(StoreError::NotFound("access policy source context"))?;
            sources_by_kind
                .entry(policy.kind())
                .or_default()
                .push(ResolvedMemorySource {
                    scope_id: source_response.scope_id,
                    agent_node_id: source_response.agent_node_id,
                });
        }

        for sources in sources_by_kind.values_mut() {
            sources
                .sort_by_key(|source| (source.scope_id.as_uuid(), source.agent_node_id.as_uuid()));
            sources.dedup();
        }

        Ok(ResolvedMemoryContext {
            tenant_id: tenant,
            data_subject_id: subject_id,
            actor_id,
            actor_trust: memphant_types::actor_kind_trust(&binding.actor.kind),
            scope_id,
            agent_node_id,
            agent_level: response.agent_level,
            subject_generation: response.subject_generation,
            policy_revision: response.policy_revision.clone(),
            sources_by_kind,
        })
    }

    async fn ping(&self) -> Result<(), StoreError> {
        Ok(())
    }

    async fn dead_letter_count(&self) -> Result<u64, StoreError> {
        let state = self.inner.lock().map_err(|_| StoreError::Poisoned)?;
        Ok(state
            .job_meta
            .values()
            .filter(|meta| {
                !meta.completed && (meta.terminal || meta.attempts >= JOB_DEAD_LETTER_ATTEMPTS)
            })
            .count() as u64)
    }
}

pub async fn retain_episode<S>(
    store: &S,
    context: &ResolvedMemoryContext,
    request: RetainRequest,
) -> Result<RetainOutcome, CoreError>
where
    S: MemoryStore,
{
    if request.body.trim().is_empty() {
        return Err(CoreError::EmptyBody);
    }

    let mut tx = store.begin(context).await?;
    let outcome = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: request.tenant_id,
                data_subject_id: request.data_subject_id,
                scope_id: request.scope_id,
                actor_id: request.actor_id,
                agent_node_id: request.agent_node_id,
                subject_generation: request.subject_generation,
                source_kind: request.source_kind.clone(),
                source_ref: request.source_ref.clone(),
                observed_at: request.observed_at,
                source_trust: request.source_trust,
                dedup_key: derive_episode_dedup_key(
                    &request.source_kind,
                    &request.source_ref,
                    &request.body,
                ),
                body: request.body,
            },
        )
        .await?;
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: request.tenant_id,
                data_subject_id: request.data_subject_id,
                scope_id: request.scope_id,
                actor_id: request.actor_id,
                agent_node_id: request.agent_node_id,
                subject_generation: request.subject_generation,
                episode_id: Some(outcome.episode_id),
                resource_id: None,
                kind: ReflectJobKind::ReflectEpisode,
                compiler_version: request.compiler_version,
                subject: request.subject,
                predicate: request.predicate,
            },
        )
        .await?;
    store.commit(tx).await?;
    Ok(outcome)
}

pub async fn retain_resource<S>(
    store: &S,
    context: &ResolvedMemoryContext,
    request: RetainResourceRequest,
) -> Result<RetainResourceOutcome, CoreError>
where
    S: MemoryStore,
{
    let mut tx = store.begin(context).await?;
    let resource_id = store
        .stage_resource(
            &mut tx,
            NewResource {
                tenant_id: request.tenant_id,
                data_subject_id: request.data_subject_id,
                scope_id: request.scope_id,
                actor_id: request.actor_id,
                agent_node_id: request.agent_node_id,
                subject_generation: request.subject_generation,
                uri: request.uri,
                source_ref: request.source_ref,
                observed_at: request.observed_at,
                kind: request.kind.unwrap_or_default(),
                content_hash: request.content_hash,
                mime_type: request.mime_type,
                revision: request.revision,
                body: request.body,
                source_trust: request.source_trust,
                acl: ResourceAcl::default(),
            },
        )
        .await?;
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: request.tenant_id,
                data_subject_id: request.data_subject_id,
                scope_id: request.scope_id,
                actor_id: request.actor_id,
                agent_node_id: request.agent_node_id,
                subject_generation: request.subject_generation,
                episode_id: None,
                resource_id: Some(resource_id),
                kind: ReflectJobKind::ReflectResource,
                compiler_version: request.compiler_version,
                subject: None,
                predicate: None,
            },
        )
        .await?;
    store.commit(tx).await?;
    Ok(RetainResourceOutcome { resource_id })
}

pub async fn correct_memory<S>(
    store: &S,
    context: &ResolvedMemoryContext,
    mut request: CorrectRequest,
    embedder: &dyn EmbeddingProvider,
    clock: &dyn Clock,
) -> Result<CorrectResult, CoreError>
where
    S: MemoryStore,
{
    if request.correction.value.trim().is_empty() {
        return Err(CoreError::Invalid(
            "correction value cannot be empty".to_string(),
        ));
    }
    if request.correction.source_ref.trim().is_empty() {
        return Err(CoreError::Invalid(
            "correction source_ref cannot be empty".to_string(),
        ));
    }
    if !(request.correction.observed_at.ends_with('Z')
        || request.correction.observed_at.ends_with("+00:00"))
    {
        return Err(CoreError::Invalid(
            "correction observed_at must use a UTC offset".to_string(),
        ));
    }
    request.correction.observed_at = fmt_rfc3339(
        request
            .correction
            .observed_at
            .parse::<jiff::Timestamp>()
            .map_err(|_| {
                CoreError::Invalid("correction observed_at must be RFC3339".to_string())
            })?,
    );
    validate_valid_interval(
        request.correction.valid_from.as_deref(),
        request.correction.valid_to.as_deref(),
    )?;

    // Embed the corrected body before the correction transaction (network I/O
    // outside the DB lock) so the replacement unit is written into the vector
    // channel atomically with its supersedes edge — mirrors reflect_recorded.
    let embedding = if embedder.dimensions() > 0 {
        embedder
            .embed(std::slice::from_ref(&request.correction.value))
            .map_err(|error| {
                CoreError::Store(StoreError::Backend(format!("embedding failed: {error}")))
            })?
            .into_iter()
            .next()
            .filter(|vec| !vec.is_empty())
            .map(|vec| (embedding_profile_for(embedder), vec))
    } else {
        None
    };

    let mut tx = store.begin(context).await?;
    let outcome = store
        .stage_correction(
            &mut tx,
            CorrectionWrite {
                compact_refresh: None,
                selector: request.selector,
                source_ref: request.correction.source_ref.clone(),
                observed_at: request.correction.observed_at.clone(),
                correction: request.correction,
                now: clock.now_rfc3339(),
                embedding,
                unit_ids: Default::default(),
            },
        )
        .await;
    match outcome {
        Ok(outcome) => {
            store.commit(tx).await?;
            Ok(outcome)
        }
        Err(StoreError::NotFound(entity)) => Err(CoreError::NotFound(entity.to_string())),
        Err(error) => Err(CoreError::Store(error)),
    }
}

pub async fn forget_memory<S>(
    store: &S,
    context: &ResolvedMemoryContext,
    request: ForgetRequest,
    clock: &dyn Clock,
) -> Result<ForgetResult, CoreError>
where
    S: MemoryStore,
{
    let target = request
        .selector
        .exactly_one_target()
        .map_err(CoreError::Invalid)?;

    let mut tx = store.begin(context).await?;
    let outcome = store
        .stage_forget(
            &mut tx,
            ForgetWrite {
                target,
                now: clock.now_rfc3339(),
            },
        )
        .await?;
    store.commit(tx).await?;

    Ok(ForgetResult {
        deletion_generation: outcome.deletion_generation,
        policy: "hard_delete".to_string(),
        invalidated_units: outcome.invalidated_units,
        verification: "authorized_transaction_committed".to_string(),
        trace_ref: None,
    })
}

pub async fn record_mark<S>(
    store: &S,
    context: &ResolvedMemoryContext,
    mut request: MarkRequest,
    clock: &dyn Clock,
) -> Result<MarkResult, CoreError>
where
    S: MemoryStore,
{
    if request.caller_id.trim().is_empty() {
        return Err(CoreError::Invalid("caller_id cannot be empty".to_string()));
    }

    let trace = store
        .trace_by_id(context, request.trace_id)
        .await?
        .ok_or_else(|| CoreError::NotFound("retrieval trace".to_string()))?;
    let canonical_ids: HashSet<UnitId> = trace
        .context_items
        .iter()
        .map(|item| item.unit_id)
        .collect();
    if request
        .used_ids
        .iter()
        .any(|unit_id| !canonical_ids.contains(unit_id))
    {
        return Err(CoreError::Invalid(
            "marked units must belong to the trace canonical inclusion whitelist".to_string(),
        ));
    }
    // Usage credit for a synthetic item (e.g. a quantity rollup) must flow to
    // the real units it was derived from: the synthetic unit id exists only
    // inside this trace, so persisting it verbatim would credit nothing.
    let mut used_ids = Vec::with_capacity(request.used_ids.len());
    for unit_id in &request.used_ids {
        match trace
            .context_items
            .iter()
            .find(|item| item.unit_id == *unit_id && !item.derived_from_unit_ids.is_empty())
        {
            Some(item) => used_ids.extend(item.derived_from_unit_ids.iter().copied()),
            None => used_ids.push(*unit_id),
        }
    }
    request.used_ids = used_ids;
    request.used_ids.sort_unstable_by_key(|id| id.as_uuid());
    request.used_ids.dedup();

    let mut tx = store.begin(context).await?;
    store
        .stage_review_events(
            &mut tx,
            vec![ReviewEvent {
                tenant_id: context.tenant_id,
                trace_id: request.trace_id,
                caller_id: request.caller_id,
                used_ids: request.used_ids,
                outcome: request.outcome,
                recorded_at: clock.now_rfc3339(),
            }],
        )
        .await?;
    store.commit(tx).await?;

    Ok(MarkResult {
        accepted: true,
        trace_id: request.trace_id,
    })
}

pub async fn recall<S>(
    store: &S,
    request: RecallRequest,
    vector_query: Option<VectorQuery<'_>>,
    clock: &dyn Clock,
) -> Result<RecallResponse, CoreError>
where
    S: MemoryStore,
{
    recall_with_pool(
        store,
        request,
        vector_query,
        clock,
        DEFAULT_RECALL_POOL_DEPTH,
        PackLevers::default(),
        LexicalScorer::default(),
        false,
        None,
    )
    .await
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ExactDecimal {
    coefficient: i128,
    scale: u32,
}

impl ExactDecimal {
    fn parse(value: &str) -> Option<Self> {
        let negative = value.starts_with('-');
        let unsigned = value.strip_prefix('-').unwrap_or(value);
        let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
        let scale = u32::try_from(fraction.len()).ok()?;
        let digits = format!("{whole}{fraction}");
        let coefficient = digits.parse::<i128>().ok()?;
        Some(Self {
            coefficient: if negative { -coefficient } else { coefficient },
            scale,
        })
    }

    fn rescale(&self, scale: u32) -> Option<i128> {
        self.coefficient
            .checked_mul(10_i128.checked_pow(scale.checked_sub(self.scale)?)?)
    }

    fn format(coefficient: i128, scale: u32) -> String {
        let negative = coefficient < 0;
        let digits = coefficient.unsigned_abs().to_string();
        if scale == 0 {
            return format!("{}{digits}", if negative { "-" } else { "" });
        }
        let width = scale as usize + 1;
        let padded = format!("{digits:0>width$}");
        let split = padded.len() - scale as usize;
        let fraction = padded[split..].trim_end_matches('0');
        if fraction.is_empty() {
            format!("{}{}", if negative { "-" } else { "" }, &padded[..split])
        } else {
            format!(
                "{}{}.{}",
                if negative { "-" } else { "" },
                &padded[..split],
                fraction
            )
        }
    }

    fn average(total: i128, scale: u32, count: i128) -> Option<String> {
        if total % count == 0 {
            return Some(Self::format(total / count, scale));
        }
        const PRECISION: u32 = 6;
        let (numerator, denominator) = if scale <= PRECISION {
            (
                total.checked_mul(10_i128.checked_pow(PRECISION - scale)?)?,
                count,
            )
        } else {
            (
                total,
                count.checked_mul(10_i128.checked_pow(scale - PRECISION)?)?,
            )
        };
        let quotient = numerator / denominator;
        let remainder = numerator % denominator;
        let rounded = if remainder.unsigned_abs().saturating_mul(2) >= denominator.unsigned_abs() {
            quotient.checked_add(if numerator < 0 { -1 } else { 1 })?
        } else {
            quotient
        };
        Some(Self::format(rounded, PRECISION))
    }
}

#[derive(Debug)]
struct QuantityRollup {
    unit: StoredMemoryUnit,
    source_unit_ids: Vec<UnitId>,
}

#[derive(Debug)]
struct ArtifactBundle {
    unit: StoredMemoryUnit,
    source_unit_ids: Vec<UnitId>,
}

fn validate_aggregation_window(window: &AggregationWindow) -> Result<(), CoreError> {
    let from = window
        .from
        .parse::<jiff::Timestamp>()
        .map_err(|_| CoreError::Invalid("aggregation_window.from must be RFC 3339".to_string()))?;
    let to = window
        .to
        .parse::<jiff::Timestamp>()
        .map_err(|_| CoreError::Invalid("aggregation_window.to must be RFC 3339".to_string()))?;
    if from >= to {
        return Err(CoreError::Invalid(
            "aggregation_window.from must be before aggregation_window.to".to_string(),
        ));
    }
    Ok(())
}

fn quantity_rollups(
    units: &[StoredMemoryUnit],
    request: &RecallRequest,
    window: &AggregationWindow,
    recall_time: &RecallTime,
) -> Vec<QuantityRollup> {
    #[derive(Debug)]
    struct Event<'a> {
        unit: &'a StoredMemoryUnit,
        event: QuantityEvent,
    }

    let mut groups: BTreeMap<(String, String, String, String), Vec<Event<'_>>> = BTreeMap::new();
    for unit in units {
        if unit.scope_id != request.context.scope_id
            || unit.kind != MemoryKind::Semantic
            || !matches!(unit.state, UnitState::Active | UnitState::Superseded)
            || !bitemporally_recallable(unit, recall_time)
        {
            continue;
        }
        let Some(event) = quantity_event_from_body(&unit.body) else {
            continue;
        };
        if cmp_rfc3339(&event.occurred_at, &window.from) == std::cmp::Ordering::Less
            || cmp_rfc3339(&event.occurred_at, &window.to) != std::cmp::Ordering::Less
        {
            continue;
        }
        groups
            .entry((
                event.namespace.clone(),
                event.item_key.clone(),
                event.measure.clone(),
                event.unit.clone(),
            ))
            .or_default()
            .push(Event { unit, event });
    }

    let query_tokens = tokenize(&request.query);
    groups
        .into_iter()
        .filter_map(|((namespace, item_key, measure, unit_name), events)| {
            let matched_dimensions: BTreeMap<String, HashSet<String>> = events
                .iter()
                .flat_map(|event| {
                    event.event.dimensions.iter().filter_map(|(key, value)| {
                        let rendered = value
                            .as_str()
                            .map(str::to_string)
                            .unwrap_or_else(|| value.to_string());
                        tokenize(&rendered)
                            .iter()
                            .any(|token| query_tokens.contains(token))
                            .then(|| (key.clone(), rendered))
                    })
                })
                .fold(BTreeMap::new(), |mut filters, (key, value)| {
                    filters.entry(key).or_insert_with(HashSet::new).insert(value);
                    filters
                });
            let series_relevant = tokenize(&format!("{namespace} {item_key} {measure}"))
                .iter()
                .any(|token| query_tokens.contains(token));
            if !series_relevant && matched_dimensions.is_empty() {
                return None;
            }
            let selected: Vec<_> = events
                .into_iter()
                .filter(|event| {
                    matched_dimensions.is_empty()
                        || matched_dimensions.iter().all(|(key, values)| {
                            event.event.dimensions.get(key).is_some_and(|candidate| {
                                let rendered = candidate
                                    .as_str()
                                    .map(str::to_string)
                                    .unwrap_or_else(|| candidate.to_string());
                                values.contains(&rendered)
                            })
                        })
                })
                .collect();
            if selected.is_empty() {
                return None;
            }
            let decimals: Vec<_> = selected
                .iter()
                .map(|event| ExactDecimal::parse(&event.event.value))
                .collect::<Option<_>>()?;
            let scale = decimals.iter().map(|value| value.scale).max()?;
            let values: Vec<i128> = decimals
                .iter()
                .map(|value| value.rescale(scale))
                .collect::<Option<_>>()?;
            let total = values
                .iter()
                .try_fold(0_i128, |sum, value| sum.checked_add(*value))?;
            let min = *values.iter().min()?;
            let max = *values.iter().max()?;
            let count = values.len() as i128;
            let average = ExactDecimal::average(total, scale, count)?;
            let mut source_unit_ids: Vec<_> = selected.iter().map(|event| event.unit.id).collect();
            source_unit_ids.sort_unstable_by_key(|id| id.as_uuid());
            let filters = if matched_dimensions.is_empty() {
                "all".to_string()
            } else {
                let mut values = matched_dimensions
                    .iter()
                    .map(|(key, values)| {
                        let mut values = values.iter().cloned().collect::<Vec<_>>();
                        values.sort();
                        format!("{key}={}", values.join("|"))
                    })
                    .collect::<Vec<_>>();
                values.sort();
                values.join(",")
            };
            let body = format!(
                "quantity rollup {namespace}/{item_key}/{measure} ({unit_name}); window=[{},{}); filter={filters}; total={}; average={average} (rounded to 6 decimal places when needed); count={}; min={}; max={}",
                window.from,
                window.to,
                ExactDecimal::format(total, scale),
                source_unit_ids.len(),
                ExactDecimal::format(min, scale),
                ExactDecimal::format(max, scale),
            );
            let mut hasher = Sha256::new();
            for component in [
                request.context.tenant_id.as_uuid().to_string(),
                request.context.scope_id.as_uuid().to_string(),
                window.from.clone(),
                window.to.clone(),
                namespace.clone(),
                item_key.clone(),
                measure.clone(),
                unit_name.clone(),
                filters,
            ] {
                hasher.update(component.len().to_be_bytes());
                hasher.update(component.as_bytes());
            }
            for id in &source_unit_ids {
                hasher.update(id.as_uuid().as_bytes());
            }
            let digest = hasher.finalize();
            let id = UnitId::from_u128(u128::from_be_bytes(digest[..16].try_into().unwrap()));
            Some(QuantityRollup {
                unit: StoredMemoryUnit {
                    invalidation: None,
                    compact: None,
                    capture: None,
                    id,
                    tenant_id: request.context.tenant_id,
                    data_subject_id: selected[0].unit.data_subject_id,
                    scope_id: request.context.scope_id,
                    agent_node_id: selected[0].unit.agent_node_id,
                    subject_generation: selected[0].unit.subject_generation,
                    kind: MemoryKind::Semantic,
                    state: UnitState::Active,
                    fact_key: Some(format!(
                        "{}:quantity_rollup:{namespace}:{item_key}:{measure}:{unit_name}",
                        request.context.scope_id.as_uuid()
                    )),
                    predicate: None,
                    body,
                    confidence: None,
                    trust_level: selected
                        .iter()
                        .map(|event| event.unit.trust_level)
                        .max_by_key(|trust| trust_risk_rank(*trust))
                        .unwrap_or(TrustLevel::Quarantined),
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(request.context.actor_id),
                    source_kind: Some("quantity_rollup".to_string()),
                    source_ref: selected[0].unit.source_ref.clone(),
                    observed_at: selected[0].unit.observed_at.clone(),
                    source_episode_id: None,
                    source_resource_id: None,
                    deletion_generation: None,
                    contextual_chunks: Vec::new(),
                    valid_from: None,
                    valid_to: None,
                    transaction_from: None,
                    transaction_to: None,
                    difficulty: None,
                    stability_days: None,
                    last_reinforced_at: None,
                    reinforcement_count: 0,
                },
                source_unit_ids,
            })
        })
        .collect()
}

pub(crate) fn trust_risk_rank(trust: TrustLevel) -> u8 {
    match trust {
        TrustLevel::TrustedUser | TrustLevel::TrustedSystem => 0,
        TrustLevel::VerifiedTool => 1,
        TrustLevel::UnverifiedTool => 2,
        TrustLevel::WebContent => 3,
        TrustLevel::AgentOutput => 4,
        TrustLevel::ImportedExternal => 5,
        TrustLevel::Quarantined => 6,
    }
}

fn explicit_artifact_anchor(query: &str) -> Option<String> {
    let mut quoted = Vec::new();
    let chars = query.char_indices().collect::<Vec<_>>();
    let mut open: Option<(char, usize)> = None;
    for (index, &(byte, character)) in chars.iter().enumerate() {
        if !matches!(character, '\'' | '"') {
            continue;
        }
        let previous_is_word = index
            .checked_sub(1)
            .and_then(|previous| chars.get(previous))
            .is_some_and(|(_, previous)| previous.is_alphanumeric());
        let next_is_word = chars
            .get(index + 1)
            .is_some_and(|(_, next)| next.is_alphanumeric());
        match open {
            Some((quote, start)) if quote == character && !next_is_word => {
                quoted.push(query[start..byte].trim());
                open = None;
            }
            None if !previous_is_word && next_is_word => {
                open = Some((character, byte + character.len_utf8()));
            }
            _ => {}
        }
    }
    quoted
        .into_iter()
        .max_by_key(|value| value.len())
        .or_else(|| query.rsplit_once(':').map(|(_, suffix)| suffix.trim()))
        .map(normalize_artifact_text)
        .filter(|anchor| anchor.split_whitespace().count() >= 3)
}

fn normalize_artifact_text(value: &str) -> String {
    let mut normalized = String::with_capacity(value.len());
    for character in value.chars() {
        if character.is_alphanumeric() {
            normalized.extend(character.to_lowercase());
        } else {
            normalized.push(' ');
        }
    }
    normalized.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn artifact_state_identity(unit: &StoredMemoryUnit) -> Option<(String, String)> {
    if unit.kind != MemoryKind::Semantic
        || unit.deletion_generation.is_some()
        || !(unit.state == UnitState::Active
            || (unit.state == UnitState::Superseded && unit.transaction_to.is_some()))
        || unit.contextual_chunks.first()?.header != "[structured-state evidence]"
    {
        return None;
    }
    let (identity, fields_json) = unit.body.split_once(": ")?;
    let (namespace, item_key) = identity.split_once(" item ")?;
    if namespace == structured_state::QUANTITY_EVENT_TYPE
        || serde_json::from_str::<BTreeMap<String, serde_json::Value>>(fields_json).is_err()
    {
        return None;
    }
    Some((namespace.to_string(), item_key.to_string()))
}

fn artifact_bundle(units: &[StoredMemoryUnit], request: &RecallRequest) -> Option<ArtifactBundle> {
    let anchor = explicit_artifact_anchor(&request.query)?;
    let seed_episodes = units
        .iter()
        .filter(|unit| unit.scope_id == request.context.scope_id)
        .filter(|unit| normalize_artifact_text(&unit.body).contains(&anchor))
        .filter_map(|unit| unit.source_episode_id)
        .collect::<HashSet<_>>();
    if seed_episodes.is_empty() {
        return None;
    }

    let structured = units
        .iter()
        .filter(|unit| unit.scope_id == request.context.scope_id)
        .filter_map(|unit| {
            artifact_state_identity(unit).map(|(namespace, item_key)| (unit, namespace, item_key))
        })
        .collect::<Vec<_>>();
    let namespaces = structured
        .iter()
        .filter(|(unit, _, _)| {
            unit.source_episode_id
                .is_some_and(|episode| seed_episodes.contains(&episode))
        })
        .map(|(_, namespace, _)| namespace.clone())
        .collect::<HashSet<_>>();
    let mut members = structured
        .into_iter()
        .filter(|(_, namespace, _)| namespaces.contains(namespace))
        .collect::<Vec<_>>();
    if members.len() < 2 {
        return None;
    }
    members.sort_by(
        |(left_unit, left_namespace, left_key), (right_unit, right_namespace, right_key)| {
            left_namespace
                .cmp(right_namespace)
                .then_with(|| left_key.cmp(right_key))
                .then_with(|| left_unit.id.as_uuid().cmp(&right_unit.id.as_uuid()))
        },
    );
    let body = format!(
        "[artifact state]\n{}",
        members
            .iter()
            .map(|(unit, _, _)| unit.body.as_str())
            .collect::<Vec<_>>()
            .join("\n")
    );
    let mut source_unit_ids = members
        .iter()
        .map(|(unit, _, _)| unit.id)
        .collect::<Vec<_>>();
    source_unit_ids.sort_unstable_by_key(|id| id.as_uuid());
    let mut hasher = Sha256::new();
    hasher.update(b"artifact_bundle_v1");
    hasher.update(request.context.tenant_id.as_uuid().as_bytes());
    hasher.update(request.context.scope_id.as_uuid().as_bytes());
    for id in &source_unit_ids {
        hasher.update(id.as_uuid().as_bytes());
    }
    let digest = hasher.finalize();
    let id = UnitId::from_u128(u128::from_be_bytes(digest[..16].try_into().unwrap()));
    let actor_id = members
        .first()
        .and_then(|(unit, _, _)| unit.actor_id)
        .filter(|actor| {
            members
                .iter()
                .all(|(unit, _, _)| unit.actor_id == Some(*actor))
        });
    Some(ArtifactBundle {
        unit: StoredMemoryUnit {
            invalidation: None,
            compact: None,
            capture: None,
            id,
            tenant_id: request.context.tenant_id,
            data_subject_id: members[0].0.data_subject_id,
            scope_id: request.context.scope_id,
            agent_node_id: members[0].0.agent_node_id,
            subject_generation: members[0].0.subject_generation,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: Some(format!(
                "{}:artifact_bundle:{}",
                request.context.scope_id.as_uuid(),
                id.as_uuid()
            )),
            predicate: None,
            body,
            confidence: None,
            trust_level: members
                .iter()
                .map(|(unit, _, _)| unit.trust_level)
                .max_by_key(|trust| trust_risk_rank(*trust))
                .unwrap_or(TrustLevel::Quarantined),
            churn_class: None,
            freshness_due_at: None,
            actor_id,
            source_kind: Some("artifact_bundle".to_string()),
            source_ref: members[0].0.source_ref.clone(),
            observed_at: members[0].0.observed_at.clone(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        },
        source_unit_ids,
    })
}

/// `recall` with the recall-pool-depth knob exposed. `recall_pool_depth` is the
/// ONE knob every internal channel/fusion limit in the recall path derives
/// from (R1.5-T0 — see the pool-mapping note on [`DEFAULT_RECALL_POOL_DEPTH`]):
/// the vector-channel KNN fan-out, the Fast packing scan window and
/// Deep scan multiplier, and the rerank rescoring cap. `k` never gates
/// any of these — only the final `PackCtx::output_limit` truncation to `k`
/// items. The construction-time [`crate::service::MemoryService`] recall-pool
/// option threads its value here; the plain [`recall`] above delegates with
/// [`DEFAULT_RECALL_POOL_DEPTH`] so every existing call site keeps today's
/// (post-R1.5-T0) behavior.
///
/// `pack_levers` threads the packing levers (session diversity quota, render
/// cap, submodular ordering) construction-time, mirroring `recall_pool_depth`; the
/// plain [`recall`] delegates with [`PackLevers::default`] (both off).
///
/// `temporal_grounding_enabled` (W5, default off via the plain [`recall`]) gates
/// query-date windowing and dated packs: on, the query's parsed date (if any)
/// becomes a soft temporal-channel window preference and packed item bodies
/// carry a `[date YYYY-MM-DD]` prefix resolved from each unit's grounded
/// `valid_from`. Off, the parsed window is `None` and every downstream path is
/// byte-identical to today.
///
/// `cross_reranker` (W8, `None` via the plain [`recall`]) is the cross-encoder
/// rerank seam: when present, AFTER fusion produces the ranked candidate list
/// and BEFORE packing, the top `recall_pool_depth` candidates are scored as
/// `(query, unit body)` pairs and reordered by cross-encoder score (ties broken
/// by prior fused rank via a stable sort). `None` leaves the fused order
/// untouched — byte-identical to today. R1.5-T1: the
/// wall-clock spent in `cross_rerank_candidates` (0 when `None` or the pool is
/// empty) is recorded on the trace as `RetrievalTrace::cross_rerank_ms` and
/// `eprintln!`-logged, and `"cross_rerank_enabled"` is added to
/// `feature_flags` when a reranker is installed.
#[allow(clippy::too_many_arguments)]
pub async fn recall_with_pool<S>(
    store: &S,
    request: RecallRequest,
    vector_query: Option<VectorQuery<'_>>,
    clock: &dyn Clock,
    recall_pool_depth: usize,
    pack_levers: PackLevers,
    lexical_scorer: LexicalScorer,
    temporal_grounding_enabled: bool,
    cross_reranker: Option<&dyn CrossReranker>,
) -> Result<RecallResponse, CoreError>
where
    S: MemoryStore,
{
    recall_with_pool_and_selection_impl(
        store,
        request,
        vector_query,
        clock,
        recall_pool_depth,
        pack_levers,
        lexical_scorer,
        temporal_grounding_enabled,
        cross_reranker,
        CrossRerankCandidateSelection::FusedHead,
        CrossRerankGranularity::default(),
        None,
        None,
    )
    .await
}

/// Stage-0 recall admission shared by the service preflight and core trace
/// path. Keeping one predicate prevents denied queries from reaching remote
/// embedding or Deep providers before the canonical policy decision.
pub fn recall_scope_admitted(context: &ResolvedMemoryContext) -> bool {
    context.sources_by_kind.values().any(|sources| {
        sources.contains(&ResolvedMemorySource {
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
        })
    })
}

#[allow(clippy::too_many_arguments)]
pub async fn recall_with_pool_and_selection_and_deep<S>(
    store: &S,
    request: RecallRequest,
    vector_query: Option<VectorQuery<'_>>,
    clock: &dyn Clock,
    recall_pool_depth: usize,
    pack_levers: PackLevers,
    lexical_scorer: LexicalScorer,
    temporal_grounding_enabled: bool,
    cross_reranker: Option<&dyn CrossReranker>,
    cross_rerank_candidate_selection: CrossRerankCandidateSelection,
    deep_provider: Option<&dyn DeepRecallProvider>,
) -> Result<RecallResponse, CoreError>
where
    S: MemoryStore,
{
    let deep_started_at = (request.mode == RecallMode::Deep).then(std::time::Instant::now);
    recall_with_pool_and_selection_and_deep_started(
        store,
        request,
        vector_query,
        clock,
        recall_pool_depth,
        pack_levers,
        lexical_scorer,
        temporal_grounding_enabled,
        cross_reranker,
        cross_rerank_candidate_selection,
        CrossRerankGranularity::default(),
        deep_provider,
        deep_started_at,
    )
    .await
}

#[allow(clippy::too_many_arguments)]
pub(crate) async fn recall_with_pool_and_selection_and_deep_started<S>(
    store: &S,
    request: RecallRequest,
    vector_query: Option<VectorQuery<'_>>,
    clock: &dyn Clock,
    recall_pool_depth: usize,
    pack_levers: PackLevers,
    lexical_scorer: LexicalScorer,
    temporal_grounding_enabled: bool,
    cross_reranker: Option<&dyn CrossReranker>,
    cross_rerank_candidate_selection: CrossRerankCandidateSelection,
    cross_rerank_granularity: CrossRerankGranularity,
    deep_provider: Option<&dyn DeepRecallProvider>,
    deep_started_at: Option<std::time::Instant>,
) -> Result<RecallResponse, CoreError>
where
    S: MemoryStore,
{
    recall_with_pool_and_selection_impl(
        store,
        request,
        vector_query,
        clock,
        recall_pool_depth,
        pack_levers,
        lexical_scorer,
        temporal_grounding_enabled,
        cross_reranker,
        cross_rerank_candidate_selection,
        cross_rerank_granularity,
        deep_provider,
        deep_started_at,
    )
    .await
}

struct DeepRunFacts {
    summary: DeepRecallSummary,
    identity: DeepProviderIdentity,
    observed_provider: Option<String>,
    observed_model: Option<String>,
    workspace_manifest_sha256: String,
    workspace_sha256: String,
    source_ids: Vec<Uuid>,
    ranked_units: Vec<StoredMemoryUnit>,
}

fn validate_deep_provider_identity(identity: &DeepProviderIdentity) -> Result<(), CoreError> {
    if identity.provider.trim().is_empty()
        || identity.model.trim().is_empty()
        || identity.prompt_hash.trim().is_empty()
        || identity.config_hash.trim().is_empty()
    {
        return Err(CoreError::DeepProviderInvalidOutput);
    }
    Ok(())
}

fn validate_deep_provider_result(
    result: &DeepRecallProviderResult,
    limits: DeepRecallLimits,
    identity: &DeepProviderIdentity,
    entries: &[DeepSnapshotEntry],
) -> Result<Vec<StoredMemoryUnit>, CoreError> {
    let status_matches = matches!(
        (result.status, result.stop_reason),
        (DeepRecallStatus::Completed, DeepRecallStopReason::Completed)
            | (
                DeepRecallStatus::Capped,
                DeepRecallStopReason::WallTime
                    | DeepRecallStopReason::ToolIterations
                    | DeepRecallStopReason::ContextTokens
                    | DeepRecallStopReason::Spend
            )
            | (
                DeepRecallStatus::Partial,
                DeepRecallStopReason::ProviderError | DeepRecallStopReason::InvalidOutput
            )
    );
    let context_usage = result
        .usage
        .context_tokens
        .checked_add(result.usage.unsettled_context_tokens_upper_bound);
    let spend_usage = result
        .usage
        .spend_micros
        .checked_add(result.usage.unsettled_spend_micros_upper_bound);
    let usage_fits = result.usage.wall_time_ms <= limits.wall_time_ms
        && result.usage.tool_iterations <= limits.max_tool_iterations
        && context_usage.is_some_and(|usage| usage <= limits.max_context_tokens)
        && spend_usage.is_some_and(|usage| usage <= limits.max_spend_micros);
    let generation_ids_valid = {
        let mut seen = HashSet::new();
        result
            .generation_ids
            .iter()
            .all(|id| !id.trim().is_empty() && seen.insert(id))
    };
    let observed_route_valid = match (&result.observed_provider, &result.observed_model) {
        (None, None) => true,
        (Some(provider), Some(model)) => !provider.trim().is_empty() && !model.trim().is_empty(),
        _ => false,
    };
    if !status_matches || !usage_fits || !observed_route_valid || !generation_ids_valid {
        return Err(CoreError::DeepProviderInvalidOutput);
    }
    let evidence = &result.evidence;
    let evidence_requires_sources = matches!(
        evidence.status,
        EvidenceStatus::Supported | EvidenceStatus::ContradictsPremise | EvidenceStatus::NearMatch
    );
    let incomplete_is_calibrated = result.status == DeepRecallStatus::Completed
        || evidence.status == EvidenceStatus::Insufficient;
    if evidence.contract_revision != EVIDENCE_DISPOSITION_CONTRACT_REVISION
        || evidence.answer_policy != evidence.status.answer_policy()
        || evidence.reason.trim().is_empty()
        || (evidence_requires_sources && result.source_ids.is_empty())
        || !incomplete_is_calibrated
    {
        return Err(CoreError::DeepProviderInvalidOutput);
    }
    validate_deep_provider_identity(identity)?;

    let mut seen = HashSet::new();
    let mut ranked_units = Vec::new();
    for source_id in &result.source_ids {
        if !seen.insert(*source_id) {
            return Err(CoreError::DeepProviderInvalidOutput);
        }
        let mut matches = entries.iter().filter(|entry| entry.source_id == *source_id);
        let Some(entry) = matches.next() else {
            return Err(CoreError::DeepProviderInvalidOutput);
        };
        if matches.next().is_some() || entry.bound_units.is_empty() {
            return Err(CoreError::DeepProviderInvalidOutput);
        }
        let mut units = entry.bound_units.clone();
        units.sort_unstable_by_key(|unit| unit.id.as_uuid());
        ranked_units.extend(units);
    }
    Ok(ranked_units)
}

#[allow(clippy::too_many_arguments)]
async fn recall_with_pool_and_selection_impl<S>(
    store: &S,
    request: RecallRequest,
    vector_query: Option<VectorQuery<'_>>,
    clock: &dyn Clock,
    recall_pool_depth: usize,
    pack_levers: PackLevers,
    lexical_scorer: LexicalScorer,
    temporal_grounding_enabled: bool,
    cross_reranker: Option<&dyn CrossReranker>,
    cross_rerank_candidate_selection: CrossRerankCandidateSelection,
    cross_rerank_granularity: CrossRerankGranularity,
    deep_provider: Option<&dyn DeepRecallProvider>,
    deep_started_at: Option<std::time::Instant>,
) -> Result<RecallResponse, CoreError>
where
    S: MemoryStore,
{
    let recall_time = resolve_recall_time(
        request.transaction_as_of.as_deref(),
        request.valid_at.as_deref(),
        clock.now(),
    )?;
    if let Some(window) = request.aggregation_window.as_ref() {
        validate_aggregation_window(window)?;
    }
    // W5: parse the query's date ONCE (clock-free). `None` whenever the flag is
    // off or the query carries no date — the whole windowing/pack path is then
    // inert. Bound to the full query; subquery passes intentionally see `None`.
    let temporal_window = temporal_grounding_enabled
        .then(|| extract_query_date(&request.query))
        .flatten();
    let allowed = recall_scope_admitted(&request.context);

    if !allowed {
        let trace_id = TraceId::new();
        let trace = RetrievalTrace {
            id: trace_id,
            tenant_id: request.context.tenant_id,
            data_subject_id: request.context.data_subject_id,
            scope_id: request.context.scope_id,
            actor_id: request.context.actor_id,
            agent_node_id: request.context.agent_node_id,
            subject_generation: request.context.subject_generation,
            policy_revision: request.context.policy_revision.clone(),
            query_hash: hash_query(&request.query),
            engine_version: request.engine_version.clone(),
            feature_flags: {
                let mut flags = recall_feature_flags(&request, false, false);
                append_pack_feature_flags(&mut flags, pack_levers);
                flags
            },
            channel_runs: vec![ReflectStageFact {
                stage: "stage0_policy".to_string(),
                detail: "denied_scope".to_string(),
            }],
            candidates: Vec::new(),
            policy_filters: vec![RecallPolicyFilter {
                reason: RecallDropReason::Scope,
                detail: "scope is not admitted by the resolved context".to_string(),
            }],
            context_items: Vec::new(),
            dropped_items: Vec::new(),
            citations: Vec::new(),
            filter_selectivity: None,
            iterative_scan_depth: None,
            recall_pool_depth: recall_pool_depth as u32,
            cross_rerank_ms: 0,
            cross_rerank: None,
            consolidation_lag_ms: 0,
            degradation: None,
            mode_requested: request.mode,
            mode_executed: request.mode,
            escalation_reason: "none".to_string(),
            procedure_ids: Vec::new(),
            procedure_validation_states: Vec::new(),
            abstention_signal: true,
            latency_ms: 0,
            token_estimate: 0,
            cost_micros: 0,
            decay_model_id: decay_model_id(&request).to_string(),
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
            recall_time: recall_time.clone(),
        };
        store.store_trace(&request.context, trace).await?;
        return Err(CoreError::PolicyDenied("scope".to_string()));
    }

    if request.mode == RecallMode::Deep && deep_provider.is_none() {
        return Err(CoreError::DeepUnavailable);
    }

    let deep_run = if request.mode == RecallMode::Deep {
        let provider = deep_provider.ok_or(CoreError::DeepUnavailable)?;
        let identity = provider.identity().clone();
        validate_deep_provider_identity(&identity)?;
        let mut entries = store
            .fetch_deep_snapshot(&request.context, &recall_time)
            .await?;
        entries.retain(|entry| {
            !entry.bound_units.is_empty()
                && entry.bound_units.iter().all(|unit| {
                    request
                        .context
                        .allows(unit.kind, unit.scope_id, unit.agent_node_id)
                        && recallable(
                            unit,
                            request.include_beliefs,
                            request.procedure_recall_enabled,
                            request.compact_only,
                            &request.query,
                            &recall_time,
                        )
                        && high_risk_recall_drop_reason(unit, &request).is_none()
                })
        });
        let workspace = build_deep_workspace_owned(&mut entries);
        let workspace_manifest_sha256 = workspace.manifest_sha256.clone();
        let workspace_sha256 = workspace.workspace_sha256.clone();
        let limits = provider.limits();
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: request.query.clone(),
                workspace,
            })
            .await
            .map_err(|error| match error {
                DeepRecallProviderError::Unavailable => CoreError::DeepUnavailable,
                DeepRecallProviderError::InvalidOutput => CoreError::DeepProviderInvalidOutput,
            })?;
        let ranked_units = validate_deep_provider_result(&result, limits, &identity, &entries)?;
        Some(DeepRunFacts {
            summary: DeepRecallSummary {
                status: result.status,
                stop_reason: result.stop_reason,
                limits,
                usage: result.usage,
                generation_ids: result.generation_ids,
                evidence: result.evidence,
            },
            identity,
            observed_provider: result.observed_provider,
            observed_model: result.observed_model,
            workspace_manifest_sha256,
            workspace_sha256,
            source_ids: result.source_ids,
            ranked_units,
        })
    } else {
        None
    };

    let query_tokens = tokenize(&request.query);
    let vector_query = vector_query.filter(|query| !query.vec.is_empty());
    let mut tenant_units = store
        .fetch_recall_candidates(
            &request.context,
            &[],
            &query_tokens,
            &recall_time,
            usize::MAX,
        )
        .await?;
    let quantity_rollups = request
        .aggregation_window
        .as_ref()
        .map(|window| quantity_rollups(&tenant_units, &request, window, &recall_time))
        .unwrap_or_default();
    let mut synthetic_sources: HashMap<_, _> = quantity_rollups
        .iter()
        .map(|rollup| (rollup.unit.id, rollup.source_unit_ids.clone()))
        .collect();
    tenant_units.extend(quantity_rollups.into_iter().map(|rollup| rollup.unit));
    // Real vector channel: the store returns (unit, cosine DISTANCE) for the
    // nearest units under the active profile (pgvector `<=>`, or the in-memory
    // app-side cosine), and the channel score is `1 - distance` — no app-side
    // recompute from raw vectors. The vector-surfaced units are folded into the
    // candidate union. `None` when no real embedding provider is configured —
    // the channel is then traced as disabled.
    let vector_scores: Option<HashMap<UnitId, f32>> = match vector_query {
        Some(query) => {
            let pairs = store
                .fetch_vector_candidates(
                    &request.context,
                    query.vec,
                    query.profile_id,
                    &recall_time,
                    // R1.5-T0 pool knob: the vector KNN fan-out derives from
                    // `recall_pool_depth`, never from `k` (default 64).
                    recall_pool_depth,
                )
                .await?;
            let mut seen: HashSet<UnitId> = tenant_units.iter().map(|unit| unit.id).collect();
            let mut scores: HashMap<UnitId, f32> = HashMap::new();
            for (unit, distance) in pairs {
                let score = 1.0 - distance;
                scores
                    .entry(unit.id)
                    .and_modify(|best| *best = best.max(score))
                    .or_insert(score);
                if seen.insert(unit.id) {
                    tenant_units.push(unit);
                }
            }
            Some(scores)
        }
        None => None,
    };
    let artifact_bundle = artifact_bundle(&tenant_units, &request);
    if let Some(bundle) = artifact_bundle {
        let source_ids = bundle
            .source_unit_ids
            .iter()
            .copied()
            .collect::<HashSet<_>>();
        tenant_units.retain(|unit| !source_ids.contains(&unit.id));
        synthetic_sources.insert(bundle.unit.id, bundle.source_unit_ids);
        tenant_units.push(bundle.unit);
    }
    if let Some(deep) = &deep_run {
        let mut seen = tenant_units
            .iter()
            .map(|unit| unit.id)
            .collect::<HashSet<_>>();
        for unit in &deep.ranked_units {
            if seen.insert(unit.id) {
                tenant_units.push(unit.clone());
            }
        }
    }
    let unit_ids: Vec<UnitId> = tenant_units.iter().map(|unit| unit.id).collect();
    let tenant_edges = store
        .fetch_edges(&request.context, &unit_ids, &recall_time)
        .await?;
    let tenant_review_events = store
        .fetch_review_events(&request.context, &unit_ids, &recall_time)
        .await?;
    let dropped_items = trace_filter_drops(&tenant_units, &request, &recall_time);
    let surviving = tenant_units.len().saturating_sub(dropped_items.len());
    let filter_selectivity = Some(surviving as f32 / tenant_units.len().max(1) as f32);
    let mut candidates_by_unit: HashMap<UnitId, CandidateAccumulator> = HashMap::new();
    let mut candidate_traces = Vec::new();

    // The token-overlap scorers all run under the honest `lexical` label; the
    // `vector` channel is only emitted by a real embedding path and is traced
    // as disabled otherwise.
    let bm25_scores: Option<HashMap<UnitId, f32>> = (lexical_scorer != LexicalScorer::Overlap)
        .then(|| bm25_unit_scores(&tenant_units, &request.query, lexical_scorer));
    let lexical_family: &[ChannelPass] = if bm25_scores.is_some() {
        &[ChannelPass::Bm25]
    } else {
        &[ChannelPass::Lexical, ChannelPass::Semantic]
    };
    let mut main_channels: Vec<ChannelPass> = vec![ChannelPass::Exact];
    main_channels.extend_from_slice(lexical_family);
    main_channels.extend_from_slice(&[ChannelPass::Temporal, ChannelPass::Edge]);
    if vector_scores.is_some() {
        main_channels.push(ChannelPass::Vector);
    }
    for pass in main_channels
        .into_iter()
        .filter(|pass| request.edge_expansion_enabled || *pass != ChannelPass::Edge)
    {
        let channel = pass.label();
        let mut ranked = channel_candidates(
            pass,
            &tenant_units,
            &tenant_edges,
            &request,
            &query_tokens,
            vector_scores.as_ref(),
            bm25_scores.as_ref(),
            &recall_time,
            temporal_window.as_ref(),
        );
        ranked.sort_by(|left, right| {
            right
                .1
                .partial_cmp(&left.1)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| left.0.body.cmp(&right.0.body))
        });
        for (rank, (unit, score)) in ranked.into_iter().enumerate() {
            let channel_rank = rank + 1;
            let decay = decay_score_for(&unit, &tenant_review_events, request.decay_enabled);
            // Weighted RRF is rank-only: it keeps a channel's ORDER and discards
            // its score magnitude. For every channel but Exact that is the
            // point — a BM25 score and a cosine similarity are not on a shared
            // scale, so only their ranks are comparable. `exact_score` IS on a
            // shared scale: it is the fraction of the unit's curated
            // `fact_key` tokens the query covers, a calibrated 0..1. Flattening
            // it to a rank means a subject key the query covers COMPLETELY
            // (1.0) outranks one it covers by a third (0.333) by exactly one
            // rank position — worth ~0.0005 here — while the lexical family
            // (Lexical + Semantic, or Bm25 standing in for both) votes with
            // three times Exact's weight off the raw body, which is precisely
            // what keyword stuffing inflates. Scaling the Exact contribution by
            // its own score restores the magnitude the channel measured.
            // Within-channel order is unchanged (score is non-increasing in
            // rank, so `score / (60 + rank)` is strictly decreasing); only the
            // cross-channel magnitude moves.
            let magnitude = if pass == ChannelPass::Exact {
                score
            } else {
                1.0
            };
            let contribution = magnitude
                * channel_weight(pass, &request.query, temporal_window.as_ref())
                / (60.0 + channel_rank as f32);
            candidates_by_unit
                .entry(unit.id)
                .and_modify(|candidate| {
                    candidate.fused_score += contribution;
                    candidate.channels.push((channel, channel_rank, score));
                })
                .or_insert_with(|| CandidateAccumulator {
                    unit: unit.clone(),
                    fused_score: contribution,
                    deep_rank: None,
                    cross_rerank_rank: None,
                    decay,
                    channels: vec![(channel, channel_rank, score)],
                });
            candidate_traces.push(RecallCandidateTrace {
                unit_id: unit.id,
                channel,
                channel_rank,
                channel_score: score,
                derived_by: derived_by_for_unit(&unit).to_string(),
                fused_rank: None,
                fused_score: None,
                cross_rerank_rank: None,
                decay_retrievability: decay.retrievability,
                dsr_stability_days: decay.stability_days,
                dsr_difficulty: decay.difficulty,
                dsr_reinforcement_count: decay.reinforcement_count,
                trust_level: unit.trust_level,
                state: unit.state,
                discard_reason: None,
                valid_from: unit.valid_from.clone(),
                valid_to: unit.valid_to.clone(),
                transaction_from: unit.transaction_from.clone(),
                transaction_to: unit.transaction_to.clone(),
            });
        }
    }

    if let Some(deep) = &deep_run {
        for (rank, unit) in deep.ranked_units.iter().enumerate() {
            let channel_rank = rank + 1;
            let decay = decay_score_for(unit, &tenant_review_events, request.decay_enabled);
            candidates_by_unit
                .entry(unit.id)
                .and_modify(|candidate| {
                    candidate.deep_rank = Some(channel_rank);
                    candidate
                        .channels
                        .push((RecallChannel::Deep, channel_rank, 1.0));
                })
                .or_insert_with(|| CandidateAccumulator {
                    unit: unit.clone(),
                    fused_score: 0.0,
                    deep_rank: Some(channel_rank),
                    cross_rerank_rank: None,
                    decay,
                    channels: vec![(RecallChannel::Deep, channel_rank, 1.0)],
                });
            candidate_traces.push(RecallCandidateTrace {
                unit_id: unit.id,
                channel: RecallChannel::Deep,
                channel_rank,
                channel_score: 1.0,
                derived_by: derived_by_for_unit(unit).to_string(),
                fused_rank: None,
                fused_score: None,
                cross_rerank_rank: None,
                decay_retrievability: decay.retrievability,
                dsr_stability_days: decay.stability_days,
                dsr_difficulty: decay.difficulty,
                dsr_reinforcement_count: decay.reinforcement_count,
                trust_level: unit.trust_level,
                state: unit.state,
                discard_reason: None,
                valid_from: unit.valid_from.clone(),
                valid_to: unit.valid_to.clone(),
                transaction_from: unit.transaction_from.clone(),
                transaction_to: unit.transaction_to.clone(),
            });
        }
    }

    let l4_gathered_evidence_ids = deep_run
        .as_ref()
        .map(|deep| deep.source_ids.iter().map(Uuid::to_string).collect())
        .unwrap_or_default();

    let mut fused: Vec<_> = candidates_by_unit.into_values().collect();
    // A-recency control (default OFF): the twenty-line substitute for the
    // bitemporal edifice. See `a_recency_control_enabled`.
    if a_recency_control_enabled() {
        retain_most_recent_per_subject(&mut fused);
    }
    // Decay is an independent retrieval signal, not a feature of the retired
    // heuristic reranker. Fold it into the fused score before the canonical
    // sort so review outcomes remain effective even when subject dedup admits
    // only the first candidate for a fact key.
    for candidate in &mut fused {
        candidate.fused_score *= candidate.decay.retrievability;
    }
    fused.sort_by(|left, right| {
        right
            .fused_score
            .partial_cmp(&left.fused_score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.unit.body.cmp(&right.unit.body))
    });
    for (rank, candidate) in fused.iter().enumerate() {
        for trace_candidate in candidate_traces
            .iter_mut()
            .filter(|trace_candidate| trace_candidate.unit_id == candidate.unit.id)
        {
            trace_candidate.fused_rank = Some(rank + 1);
            trace_candidate.fused_score = Some(candidate.fused_score);
        }
    }

    // W8 cross-encoder rerank: reorder the top `recall_pool_depth` fused
    // candidates by a real (query, body) cross-encoder before packing. A no-op
    // when no reranker is wired (the default) or the pool is empty — the fused
    // order then flows unchanged into packing. R1.5-T1: the wall-clock is
    // measured HERE (not by a decorator around `reranker`) so the same number
    // lands in the trace for every caller — server, worker/mcp (via
    // `MemoryService`) and the eval bench alike — rather than each call site
    // having to wrap its own reranker to get visibility. `eprintln!` is this
    // crate's existing convention for opt-in-path diagnostics (see the
    // mutex-poisoned warning in `FastEmbedCrossReranker::rerank`); there is no
    // tracing/log dependency in this crate to hook a real "debug" level into.
    let mut cross_rerank_ms: u64 = 0;
    let mut cross_rerank = None;
    if let Some(reranker) = cross_reranker {
        if cross_rerank_candidate_selection == CrossRerankCandidateSelection::VectorLexicalBalanced
        {
            promote_vector_lexical_balanced(
                &mut fused,
                reranker.config().candidate_limit.min(recall_pool_depth),
            );
        }
        let cross_rerank_started = std::time::Instant::now();
        cross_rerank = Some(cross_rerank_candidates(
            fused.as_mut_slice(),
            &request.query,
            reranker,
            recall_pool_depth,
            cross_rerank_granularity,
        ));
        cross_rerank_ms = cross_rerank_started.elapsed().as_millis() as u64;
        // Stamp the post-rerank order onto the trace the same way `fused_rank`
        // was stamped above. Without it a post-rerank miss cannot be attributed:
        // "outside the scored head" and "scored and still below the cut" are
        // different defects with different fixes, and `fused_rank` shows neither.
        for candidate in fused.iter() {
            let Some(rank) = candidate.cross_rerank_rank else {
                continue;
            };
            for trace_candidate in candidate_traces
                .iter_mut()
                .filter(|trace_candidate| trace_candidate.unit_id == candidate.unit.id)
            {
                trace_candidate.cross_rerank_rank = Some(rank + 1);
            }
        }
        eprintln!(
            "memphant: cross_rerank_ms={cross_rerank_ms} pool={}",
            recall_pool_depth.min(fused.len())
        );
    }

    let iterative_scan_depth =
        recall_pack_scan_limit(&request, fused.len(), pack_levers, recall_pool_depth);
    let packed = pack_recall_context(
        fused,
        &request,
        &tenant_edges,
        &query_tokens,
        dropped_items,
        iterative_scan_depth,
        pack_levers,
        temporal_grounding_enabled,
    );
    let token_estimate = packed.token_estimate;
    let mut items = packed.items;
    for item in &mut items {
        if let Some(source_ids) = synthetic_sources.get(&item.unit_id) {
            item.derived_from_unit_ids = source_ids.clone();
        }
    }
    let dropped_items = packed.dropped_items;
    let mut abstention = packed.abstention;

    let candidate_whitelist: Vec<_> = items.iter().map(|item| item.unit_id).collect();
    let mut suppression_labels = Vec::new();
    for label in items
        .iter()
        .flat_map(|item| item.suppression_labels.iter().cloned())
    {
        if !suppression_labels.contains(&label) {
            suppression_labels.push(label);
        }
    }
    let trace_id = TraceId::new();
    let query_hash = hash_query(&request.query);
    let citations =
        build_recall_citations(store, &request, &recall_time, trace_id, &query_hash, &items)
            .await?;
    if let Some(deep) = deep_run.as_ref() {
        let evidence_status = deep.summary.evidence.status;
        if evidence_status == EvidenceStatus::Insufficient {
            abstention = true;
        }
        if matches!(
            evidence_status,
            EvidenceStatus::Supported
                | EvidenceStatus::ContradictsPremise
                | EvidenceStatus::NearMatch
        ) && !citations
            .iter()
            .any(|citation| matches!(citation.verification, CitationVerification::Verified { .. }))
        {
            return Err(CoreError::DeepProviderInvalidOutput);
        }
    }
    let procedure_ids = items
        .iter()
        .filter(|item| item.kind == MemoryKind::Procedural)
        .map(|item| item.unit_id)
        .collect::<Vec<_>>();
    let procedure_validation_states = procedure_trace_facts(&tenant_units, &request);
    let mut feature_flags =
        recall_feature_flags(&request, vector_scores.is_some(), deep_run.is_some());
    if candidate_traces
        .iter()
        .any(|candidate| candidate.derived_by == "composition")
        || items.iter().any(|item| item.derived_by == "composition")
    {
        feature_flags.push("inferred_belief_composition_enabled".to_string());
    }
    if cross_reranker.is_some() {
        // R1.5-T1: construction-time flag (the `MemoryService`/runtime
        // `MEMPHANT_CROSS_RERANK` seam), not a `RecallRequest` field — mirrors
        // how `contextual_chunks_enabled` is unconditional-per-service rather
        // than request-derived.
        feature_flags.push("cross_rerank_enabled".to_string());
    }
    append_pack_feature_flags(&mut feature_flags, pack_levers);
    if let Some(flag) = lexical_scorer.flag() {
        feature_flags.push(flag.to_string());
    }
    let trace = RetrievalTrace {
        id: trace_id,
        tenant_id: request.context.tenant_id,
        data_subject_id: request.context.data_subject_id,
        scope_id: request.context.scope_id,
        actor_id: request.context.actor_id,
        agent_node_id: request.context.agent_node_id,
        subject_generation: request.context.subject_generation,
        policy_revision: request.context.policy_revision.clone(),
        query_hash,
        engine_version: request.engine_version.clone(),
        feature_flags,
        channel_runs: recall_stage_facts(vector_scores.is_some(), deep_run.as_ref()),
        candidates: candidate_traces,
        policy_filters: Vec::new(),
        context_items: items.clone(),
        dropped_items,
        citations: citations.clone(),
        filter_selectivity,
        iterative_scan_depth: Some(iterative_scan_depth as u32),
        recall_pool_depth: recall_pool_depth as u32,
        cross_rerank_ms,
        cross_rerank,
        consolidation_lag_ms: 0,
        degradation: None,
        mode_requested: request.mode,
        mode_executed: request.mode,
        escalation_reason: "none".to_string(),
        procedure_ids,
        procedure_validation_states,
        abstention_signal: abstention,
        latency_ms: deep_started_at.map_or(0, |started| {
            u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
        }),
        token_estimate,
        cost_micros: deep_run
            .as_ref()
            .map_or(0, |deep| deep.summary.usage.spend_micros),
        decay_model_id: decay_model_id(&request).to_string(),
        l4_sandbox_id: deep_run.as_ref().map(|deep| deep.workspace_sha256.clone()),
        l4_gathered_evidence_ids,
        deep: deep_run.as_ref().map(|deep| deep.summary.clone()),
        l4_provider: deep_run.as_ref().map(|deep| deep.identity.provider.clone()),
        l4_model: deep_run.as_ref().map(|deep| deep.identity.model.clone()),
        l4_observed_provider: deep_run
            .as_ref()
            .and_then(|deep| deep.observed_provider.clone()),
        l4_observed_model: deep_run
            .as_ref()
            .and_then(|deep| deep.observed_model.clone()),
        l4_prompt_hash: deep_run
            .as_ref()
            .map(|deep| deep.identity.prompt_hash.clone()),
        l4_config_hash: deep_run
            .as_ref()
            .map(|deep| deep.identity.config_hash.clone()),
        l4_workspace_manifest_sha256: deep_run
            .as_ref()
            .map(|deep| deep.workspace_manifest_sha256.clone()),
        recall_time: recall_time.clone(),
    };
    store.store_trace(&request.context, trace).await?;

    Ok(RecallResponse {
        trace_id,
        items,
        candidate_whitelist,
        citations,
        abstention,
        degraded: false,
        consolidation_lag_ms: 0,
        suppression_labels,
        deep: deep_run.map(|deep| deep.summary),
        recall_time,
    })
}

// A recall response contains at most `k` context items, but one consolidated
// item can legitimately retain many source citations (for example, a long
// session). This is a serialized-receipt bound, not the internal candidate-pool
// depth and not an answer-evidence item bound. 1024 keeps realistic 10-item
// session packs valid while still failing closed on pathological citation fanout.
const MAX_VERIFIED_RECEIPTS: usize = 1024;
const MAX_VERIFIED_QUOTE_BYTES: usize = 64 * 1024;

#[allow(clippy::too_many_arguments)]
async fn build_recall_citations<S: MemoryStore>(
    store: &S,
    request: &RecallRequest,
    recall_time: &RecallTime,
    trace_id: TraceId,
    query_hash: &str,
    items: &[RecallContextItem],
) -> Result<Vec<RecallCitation>, CoreError> {
    let direct_ids = items
        .iter()
        .filter(|item| item.citation_episode_id.is_some() || item.citation_resource_id.is_some())
        .map(|item| item.unit_id)
        .collect::<Vec<_>>();
    let materials = store
        .fetch_record_material(&request.context, &direct_ids, recall_time)
        .await?;
    let materials = materials
        .into_iter()
        .map(|material| (material.unit.id, material))
        .collect::<HashMap<_, _>>();
    let episode_ids = materials
        .values()
        .flat_map(|material| {
            material
                .citations
                .iter()
                .filter_map(|citation| citation.episode_id)
        })
        .collect::<HashSet<_>>();
    let resource_ids = materials
        .values()
        .flat_map(|material| {
            material
                .citations
                .iter()
                .filter_map(|citation| citation.resource_id)
        })
        .collect::<HashSet<_>>();
    let episodes = store
        .fetch_episodes_by_ids(
            &request.context,
            &episode_ids.into_iter().collect::<Vec<_>>(),
        )
        .await?
        .into_iter()
        .map(|episode| (episode.id, episode))
        .collect::<HashMap<_, _>>();
    let resources = store
        .fetch_resources_by_ids(
            &request.context,
            &resource_ids.into_iter().collect::<Vec<_>>(),
        )
        .await?
        .into_iter()
        .map(|resource| (resource.id, resource))
        .collect::<HashMap<_, _>>();
    let mut output = Vec::new();

    for item in items {
        let direct = item.citation_episode_id.is_some() || item.citation_resource_id.is_some();
        if !direct {
            if !item.derived_from_unit_ids.is_empty() {
                output.push(RecallCitation {
                    unit_id: item.unit_id,
                    episode_id: None,
                    resource_id: None,
                    derived_from_unit_ids: item.derived_from_unit_ids.clone(),
                    verification: CitationVerification::Unverified {
                        reason: CitationUnverifiedReason::DerivedReference,
                    },
                });
            }
            continue;
        }

        let material = materials.get(&item.unit_id).ok_or_else(|| {
            CoreError::Invalid(
                "selected citation unit has no canonical record material".to_string(),
            )
        })?;
        let unit = &material.unit;
        if unit.tenant_id != request.context.tenant_id
            || unit.data_subject_id != request.context.data_subject_id
            || unit.scope_id != request.context.scope_id
            || unit.agent_node_id != request.context.agent_node_id
            || unit.subject_generation != request.context.subject_generation
            || unit.actor_id != Some(request.context.actor_id)
            || unit.source_episode_id != item.citation_episode_id
            || unit.source_resource_id != item.citation_resource_id
        {
            return Err(CoreError::PolicyDenied(
                "citation unit is not bound to the recall context".to_string(),
            ));
        }

        let matching = material
            .citations
            .iter()
            .filter(|citation| {
                citation.episode_id == item.citation_episode_id
                    && citation.resource_id == item.citation_resource_id
            })
            .collect::<Vec<_>>();
        if matching.is_empty() {
            output.push(RecallCitation {
                unit_id: item.unit_id,
                episode_id: item.citation_episode_id,
                resource_id: item.citation_resource_id,
                derived_from_unit_ids: item.derived_from_unit_ids.clone(),
                verification: CitationVerification::Unverified {
                    reason: CitationUnverifiedReason::MissingCanonicalCitation,
                },
            });
            continue;
        }

        for citation in matching {
            if output.len() >= MAX_VERIFIED_RECEIPTS {
                return Err(CoreError::Invalid(
                    "verified citation receipt count exceeds limit".to_string(),
                ));
            }
            if citation.tenant_id != request.context.tenant_id
                || citation.data_subject_id != request.context.data_subject_id
                || citation.scope_id != request.context.scope_id
                || citation.agent_node_id != request.context.agent_node_id
                || citation.subject_generation != request.context.subject_generation
                || citation.memory_unit_id != item.unit_id
                || citation.episode_id.is_some() == citation.resource_id.is_some()
            {
                return Err(CoreError::PolicyDenied(
                    "canonical citation is not bound to the recall context".to_string(),
                ));
            }
            let span = citation.span.clone().ok_or_else(|| {
                CoreError::Invalid("canonical citation is missing its source span".to_string())
            })?;
            let quote_sha256 = citation.quote_hash.clone().ok_or_else(|| {
                CoreError::Invalid("canonical citation is missing its quote hash".to_string())
            })?;

            let (source_kind, source_id, source_ref, source_revision, source_body, source_trust) =
                match (citation.episode_id, citation.resource_id) {
                    (Some(id), None) => {
                        let episode = episodes.get(&id).ok_or_else(|| {
                            CoreError::Invalid("canonical citation episode is missing".to_string())
                        })?;
                        if episode.tenant_id != request.context.tenant_id
                            || episode.data_subject_id != request.context.data_subject_id
                            || episode.scope_id != request.context.scope_id
                            || episode.agent_node_id != request.context.agent_node_id
                            || episode.subject_generation != request.context.subject_generation
                            || episode.actor_id != request.context.actor_id
                            || episode.source_trust == TrustLevel::Quarantined
                        {
                            return Err(CoreError::PolicyDenied(
                                "citation episode is unauthorized for this recall".to_string(),
                            ));
                        }
                        (
                            EvidenceSourceKind::Episode,
                            id.as_uuid(),
                            episode.source_ref.clone(),
                            None,
                            episode.body.clone(),
                            episode.source_trust,
                        )
                    }
                    (None, Some(id)) => {
                        let resource = resources.get(&id).ok_or_else(|| {
                            CoreError::Invalid("canonical citation resource is missing".to_string())
                        })?;
                        if resource.tenant_id != request.context.tenant_id
                            || resource.data_subject_id != request.context.data_subject_id
                            || resource.scope_id != request.context.scope_id
                            || resource.agent_node_id != request.context.agent_node_id
                            || resource.subject_generation != request.context.subject_generation
                            || resource.actor_id != request.context.actor_id
                            || resource.source_trust == TrustLevel::Quarantined
                            || !resource.acl.is_empty()
                        {
                            return Err(CoreError::PolicyDenied(
                                "citation resource is unauthorized for this recall".to_string(),
                            ));
                        }
                        let body = resource.body.clone().ok_or_else(|| {
                            CoreError::Invalid(
                                "canonical citation resource has no source text".to_string(),
                            )
                        })?;
                        let body_sha256 = format!("sha256:{}", sha256_bytes_hex(body.as_bytes()));
                        if resource.content_hash != body_sha256 {
                            return Err(CoreError::Invalid(
                                "canonical resource content hash does not match source text"
                                    .to_string(),
                            ));
                        }
                        (
                            EvidenceSourceKind::Resource,
                            id.as_uuid(),
                            resource.source_ref.clone(),
                            resource.revision.clone(),
                            body,
                            resource.source_trust,
                        )
                    }
                    _ => {
                        return Err(CoreError::Invalid(
                            "canonical citation must identify exactly one source".to_string(),
                        ));
                    }
                };

            let start = usize::try_from(span.start)
                .map_err(|_| CoreError::Invalid("citation span start is too large".to_string()))?;
            let end = usize::try_from(span.end)
                .map_err(|_| CoreError::Invalid("citation span end is too large".to_string()))?;
            if end < start || end.saturating_sub(start) > MAX_VERIFIED_QUOTE_BYTES {
                return Err(CoreError::Invalid(
                    "citation span is malformed or exceeds the evidence limit".to_string(),
                ));
            }
            let quote = source_body.get(start..end).ok_or_else(|| {
                CoreError::Invalid(
                    "citation span is out of bounds or not on UTF-8 boundaries".to_string(),
                )
            })?;
            let actual_quote_sha256 = format!("sha256:{}", sha256_bytes_hex(quote.as_bytes()));
            if quote_sha256 != actual_quote_sha256 {
                return Err(CoreError::Invalid(
                    "citation quote hash does not match canonical source text".to_string(),
                ));
            }
            let source_body_sha256 = format!("sha256:{}", sha256_bytes_hex(source_body.as_bytes()));
            output.push(RecallCitation {
                unit_id: item.unit_id,
                episode_id: item.citation_episode_id,
                resource_id: item.citation_resource_id,
                derived_from_unit_ids: item.derived_from_unit_ids.clone(),
                verification: CitationVerification::Verified {
                    receipt: Box::new(EvidenceReceipt {
                        contract_revision: EVIDENCE_RECEIPT_CONTRACT_REVISION.to_string(),
                        citation_id: citation.id,
                        trace_id,
                        tenant_id: request.context.tenant_id,
                        data_subject_id: request.context.data_subject_id,
                        scope_id: request.context.scope_id,
                        actor_id: request.context.actor_id,
                        agent_node_id: request.context.agent_node_id,
                        subject_generation: request.context.subject_generation,
                        memory_unit_id: item.unit_id,
                        source_kind,
                        source_id,
                        source_ref,
                        source_revision,
                        source_body_sha256,
                        span: span.clone(),
                        quote_sha256,
                        source_trust,
                        query_hash: query_hash.to_string(),
                        policy_revision: request.context.policy_revision.clone(),
                        engine_version: request.engine_version.clone(),
                        schema_compat_revision: SCHEMA_COMPAT_REVISION.to_string(),
                        recalled_at: recall_time.evaluated_at.clone(),
                    }),
                },
            });
        }
    }
    Ok(output)
}

#[cfg(test)]
mod evidence_receipt_tests {
    use super::*;

    const NOW: &str = "2026-07-23T00:00:00Z";

    fn fixture() -> (
        InMemoryStore,
        RecallRequest,
        RecallTime,
        RecallContextItem,
        StoredCitation,
    ) {
        let store = InMemoryStore::default();
        let context = memphant_store_testkit::resolved_context(
            TenantId::from_u128(70_001),
            ScopeId::from_u128(70_002),
            ActorId::from_u128(70_003),
        );
        store.seed_context_binding(&context);
        let episode_id = EpisodeId::from_u128(70_004);
        let unit_id = UnitId::from_u128(70_005);
        let body = "alpha βeta canonical evidence".to_string();
        let quote = "βeta canonical";
        let start = body.find(quote).unwrap();
        let end = start + quote.len();
        let unit = StoredMemoryUnit {
            capture: None,
            invalidation: None,
            compact: None,
            id: unit_id,
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: Some("evidence:test".to_string()),
            predicate: None,
            body: quote.to_string(),
            confidence: Some(1.0),
            trust_level: TrustLevel::TrustedUser,
            freshness_due_at: None,
            churn_class: None,
            actor_id: Some(context.actor_id),
            source_kind: Some("user".to_string()),
            source_ref: "test:evidence".to_string(),
            observed_at: NOW.to_string(),
            source_episode_id: Some(episode_id),
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: Some(NOW.to_string()),
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        };
        let citation = StoredCitation {
            id: Uuid::from_u128(70_006),
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            memory_unit_id: unit_id,
            episode_id: Some(episode_id),
            resource_id: None,
            span: Some(memphant_types::CitationSpan {
                start: start as u64,
                end: end as u64,
            }),
            quote_hash: Some(format!("sha256:{}", sha256_bytes_hex(quote.as_bytes()))),
        };
        let episode = StoredEpisode {
            id: episode_id,
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_kind: "user".to_string(),
            source_ref: "test:evidence".to_string(),
            source_trust: TrustLevel::TrustedUser,
            dedup_key: "test:evidence".to_string(),
            body,
            observation_count: 1,
            first_observed_at: NOW.to_string(),
            last_observed_at: NOW.to_string(),
        };
        {
            let mut state = store.inner.lock().unwrap();
            state
                .memory_units
                .entry(context.tenant_id)
                .or_default()
                .push(unit);
            state
                .episodes
                .entry(context.tenant_id)
                .or_default()
                .push(episode);
            state
                .citations
                .entry(context.tenant_id)
                .or_default()
                .push(citation.clone());
        }
        let request = RecallRequest {
            compact_only: false,
            context,
            query: "canonical evidence".to_string(),
            k: 1,
            budget_tokens: 128,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: false,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: false,
            decay_enabled: false,
            engine_version: "test-engine".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        };
        let recall_time = RecallTime {
            evaluated_at: NOW.to_string(),
            transaction_as_of: NOW.to_string(),
            valid_at: NOW.to_string(),
        };
        let item = RecallContextItem {
            unit_id,
            body: quote.to_string(),
            kind: MemoryKind::Semantic,
            derived_by: "test".to_string(),
            inclusion_reason: "test".to_string(),
            citation_episode_id: Some(episode_id),
            citation_resource_id: None,
            derived_from_unit_ids: Vec::new(),
            suppression_labels: Vec::new(),
            correction: None,
        };
        (store, request, recall_time, item, citation)
    }

    #[tokio::test]
    async fn verified_receipt_binds_unicode_bytes_trace_query_policy_and_contract() {
        let (store, request, recall_time, item, _) = fixture();
        let trace_id = TraceId::from_u128(70_007);
        let query_hash = hash_query(&request.query);
        let citations = build_recall_citations(
            &store,
            &request,
            &recall_time,
            trace_id,
            &query_hash,
            &[item],
        )
        .await
        .unwrap();
        let CitationVerification::Verified { receipt } = &citations[0].verification else {
            panic!("canonical citation must produce a verified receipt");
        };
        assert_eq!(receipt.trace_id, trace_id);
        assert_eq!(receipt.query_hash, query_hash);
        assert_eq!(receipt.policy_revision, request.context.policy_revision);
        assert_eq!(
            receipt.contract_revision,
            EVIDENCE_RECEIPT_CONTRACT_REVISION
        );
        assert_eq!(receipt.span.end - receipt.span.start, 15);
        let encoded = serde_json::to_vec(receipt).unwrap();
        assert_eq!(
            serde_json::to_vec(&serde_json::from_slice::<EvidenceReceipt>(&encoded).unwrap())
                .unwrap(),
            encoded
        );
    }

    #[tokio::test]
    async fn receipt_source_loading_is_constant_batch_count_not_per_citation() {
        let (store, request, recall_time, item, _) = fixture();
        let items = vec![item; 128];

        let citations = build_recall_citations(
            &store,
            &request,
            &recall_time,
            TraceId::from_u128(70_008),
            &hash_query(&request.query),
            &items,
        )
        .await
        .unwrap();

        assert_eq!(citations.len(), 128);
        assert_eq!(
            store
                .citation_source_batch_reads
                .load(std::sync::atomic::Ordering::SeqCst),
            2,
            "one episode batch and one resource batch, independent of citation count"
        );
    }

    #[tokio::test]
    async fn tampered_hash_and_cross_tenant_replay_fail_closed() {
        let (store, request, recall_time, item, citation) = fixture();
        store
            .inner
            .lock()
            .unwrap()
            .citations
            .get_mut(&request.context.tenant_id)
            .unwrap()[0]
            .quote_hash = Some("sha256:tampered".to_string());
        let error = build_recall_citations(
            &store,
            &request,
            &recall_time,
            TraceId::new(),
            &hash_query(&request.query),
            std::slice::from_ref(&item),
        )
        .await
        .unwrap_err();
        assert!(matches!(error, CoreError::Invalid(_)));

        store
            .inner
            .lock()
            .unwrap()
            .citations
            .get_mut(&request.context.tenant_id)
            .unwrap()[0] = citation;
        let mut replay = request.clone();
        replay.context.tenant_id = TenantId::from_u128(70_999);
        let error = build_recall_citations(
            &store,
            &replay,
            &recall_time,
            TraceId::new(),
            &hash_query(&replay.query),
            &[item],
        )
        .await
        .unwrap_err();
        assert!(matches!(
            error,
            CoreError::Invalid(_) | CoreError::PolicyDenied(_) | CoreError::Store(_)
        ));
    }

    #[tokio::test]
    async fn protected_or_stale_resource_receipts_fail_closed() {
        let (store, request, recall_time, mut item, _) = fixture();
        let resource_id = ResourceId::from_u128(70_004);
        let body = {
            let mut state = store.inner.lock().unwrap();
            let body = state
                .episodes
                .get_mut(&request.context.tenant_id)
                .unwrap()
                .pop()
                .unwrap()
                .body;
            let unit = &mut state
                .memory_units
                .get_mut(&request.context.tenant_id)
                .unwrap()[0];
            unit.source_episode_id = None;
            unit.source_resource_id = Some(resource_id);
            let citation = &mut state.citations.get_mut(&request.context.tenant_id).unwrap()[0];
            citation.episode_id = None;
            citation.resource_id = Some(resource_id);
            state
                .resources
                .entry(request.context.tenant_id)
                .or_default()
                .push(StoredResource {
                    id: resource_id,
                    tenant_id: request.context.tenant_id,
                    data_subject_id: request.context.data_subject_id,
                    scope_id: request.context.scope_id,
                    actor_id: request.context.actor_id,
                    agent_node_id: request.context.agent_node_id,
                    subject_generation: request.context.subject_generation,
                    uri: "test://evidence".to_string(),
                    source_ref: "test:evidence".to_string(),
                    observed_at: NOW.to_string(),
                    kind: memphant_types::ResourceKind::Document,
                    content_hash: format!("sha256:{}", sha256_bytes_hex(body.as_bytes())),
                    mime_type: "text/plain".to_string(),
                    revision: Some("r1".to_string()),
                    body: Some(body.clone()),
                    source_trust: TrustLevel::TrustedSystem,
                    acl: ResourceAcl {
                        protected: Some(
                            memphant_types::ResourceProtectedCategory::CredentialsSecrets,
                        ),
                        ..ResourceAcl::default()
                    },
                    extractor_state: ResourceExtractorState::Embedded,
                });
            body
        };
        item.citation_episode_id = None;
        item.citation_resource_id = Some(resource_id);
        let error = build_recall_citations(
            &store,
            &request,
            &recall_time,
            TraceId::new(),
            &hash_query(&request.query),
            std::slice::from_ref(&item),
        )
        .await
        .unwrap_err();
        assert!(matches!(error, CoreError::PolicyDenied(_)));

        {
            let mut state = store.inner.lock().unwrap();
            let resource = &mut state.resources.get_mut(&request.context.tenant_id).unwrap()[0];
            resource.acl = ResourceAcl::default();
            resource.content_hash = format!("sha256:{}", sha256_bytes_hex(b"stale revision"));
            assert_ne!(
                resource.content_hash,
                format!("sha256:{}", sha256_bytes_hex(body.as_bytes()))
            );
        }
        let error = build_recall_citations(
            &store,
            &request,
            &recall_time,
            TraceId::new(),
            &hash_query(&request.query),
            &[item],
        )
        .await
        .unwrap_err();
        assert!(matches!(error, CoreError::Invalid(_)));
    }

    #[tokio::test]
    async fn oversized_citation_fanout_fails_closed_without_rejecting_realistic_packs() {
        let (store, request, recall_time, item, citation) = fixture();
        {
            let mut state = store.inner.lock().unwrap();
            let citations = state.citations.get_mut(&request.context.tenant_id).unwrap();
            citations.clear();
            for index in 0..=MAX_VERIFIED_RECEIPTS {
                let mut next = citation.clone();
                next.id = Uuid::from_u128(80_000 + index as u128);
                citations.push(next);
            }
        }
        let error = build_recall_citations(
            &store,
            &request,
            &recall_time,
            TraceId::new(),
            &hash_query(&request.query),
            &[item],
        )
        .await
        .unwrap_err();
        assert!(
            matches!(error, CoreError::Invalid(message) if message.contains("count exceeds limit"))
        );
    }
}

fn promote_vector_lexical_balanced(fused: &mut Vec<CandidateAccumulator>, candidate_limit: usize) {
    let target = candidate_limit.min(fused.len());
    let mut selected = Vec::with_capacity(target);
    let ranked_by_channel = [RecallChannel::Vector, RecallChannel::Lexical].map(|channel| {
        let mut ranked = fused
            .iter()
            .enumerate()
            .filter_map(|(index, candidate)| {
                candidate
                    .channels
                    .iter()
                    .filter(|(candidate_channel, _, _)| *candidate_channel == channel)
                    .map(|(_, rank, _)| *rank)
                    .min()
                    .map(|rank| (rank, index))
            })
            .collect::<Vec<_>>();
        ranked.sort_unstable();
        ranked
    });
    for (ranked, quota) in ranked_by_channel
        .iter()
        .zip([target.div_ceil(2), target / 2])
    {
        if quota == 0 {
            continue;
        }
        let mut added = 0;
        for &(_, index) in ranked {
            if !selected.contains(&index) {
                selected.push(index);
                added += 1;
                if added == quota {
                    break;
                }
            }
        }
    }
    let mut channel_backfill = ranked_by_channel
        .iter()
        .enumerate()
        .flat_map(|(channel_order, ranked)| {
            ranked
                .iter()
                .map(move |&(rank, index)| (rank, channel_order, index))
        })
        .collect::<Vec<_>>();
    channel_backfill.sort_unstable();
    for (_, _, index) in channel_backfill {
        if selected.len() == target {
            break;
        }
        if !selected.contains(&index) {
            selected.push(index);
        }
    }
    for index in 0..fused.len() {
        if selected.len() == target {
            break;
        }
        if !selected.contains(&index) {
            selected.push(index);
        }
    }
    let mut promoted = selected
        .iter()
        .map(|index| fused[*index].clone())
        .collect::<Vec<_>>();
    promoted.extend(
        fused
            .iter()
            .enumerate()
            .filter(|(index, _)| !selected.contains(index))
            .map(|(_, candidate)| candidate.clone()),
    );
    *fused = promoted;
}

fn trace_filter_drops(
    units: &[StoredMemoryUnit],
    request: &RecallRequest,
    time: &RecallTime,
) -> Vec<RecallDroppedItem> {
    units
        .iter()
        .filter_map(|unit| {
            let reason = if !request
                .context
                .allows(unit.kind, unit.scope_id, unit.agent_node_id)
            {
                Some(RecallDropReason::Scope)
            } else if unit.deletion_generation.is_some() {
                Some(RecallDropReason::Deleted)
            } else if !bitemporally_recallable(unit, time)
                || !valid_for_query(unit, &request.query, &time.valid_at)
            {
                Some(RecallDropReason::Stale)
            } else if let Some(reason) = procedure_drop_reason(unit, request) {
                Some(reason)
            } else if let Some(reason) = high_risk_recall_drop_reason(unit, request) {
                Some(reason)
            } else {
                match unit.state {
                    UnitState::Deleted => Some(RecallDropReason::Deleted),
                    UnitState::Invalidated => Some(RecallDropReason::Invalidated),
                    UnitState::Superseded if unit.transaction_to.is_some() => None,
                    UnitState::Superseded | UnitState::Expired => Some(RecallDropReason::Stale),
                    UnitState::Quarantined => Some(RecallDropReason::Trust),
                    UnitState::Candidate
                        if unit.kind == MemoryKind::Belief && !request.include_beliefs =>
                    {
                        Some(RecallDropReason::Trust)
                    }
                    _ => None,
                }
            };
            reason.map(|reason| RecallDroppedItem {
                unit_id: unit.id,
                reason,
            })
        })
        .collect()
}

fn procedure_drop_reason(
    unit: &StoredMemoryUnit,
    request: &RecallRequest,
) -> Option<RecallDropReason> {
    if unit.kind != MemoryKind::Procedural {
        return None;
    }
    if !request.procedure_recall_enabled || unit.state != UnitState::Validated {
        return Some(RecallDropReason::State);
    }
    if unsafe_procedure_step(unit) {
        return Some(RecallDropReason::ProtectedCategory);
    }
    None
}

fn procedure_trace_facts(
    units: &[StoredMemoryUnit],
    request: &RecallRequest,
) -> Vec<ProcedureTraceFact> {
    units
        .iter()
        .filter(|unit| {
            unit.kind == MemoryKind::Procedural
                && request
                    .context
                    .allows(unit.kind, unit.scope_id, unit.agent_node_id)
                && unit.deletion_generation.is_none()
                && unit.transaction_to.is_none()
        })
        .map(|unit| ProcedureTraceFact {
            unit_id: unit.id,
            validation_state: procedure_validation_state(unit).to_string(),
            signal_kind: procedure_signal_kind(unit).to_string(),
            safety_status: procedure_safety_status(unit).to_string(),
        })
        .collect()
}

fn procedure_validation_state(unit: &StoredMemoryUnit) -> &'static str {
    match unit.state {
        UnitState::Validated => "validated",
        UnitState::Candidate => "candidate",
        UnitState::Active => "active",
        _ => "not_validated",
    }
}

fn procedure_signal_kind(unit: &StoredMemoryUnit) -> &'static str {
    let body = normalize_component(&unit.body);
    let subject = unit
        .fact_key
        .as_deref()
        .map(normalize_component)
        .unwrap_or_default();
    if body.contains("failure pattern")
        || body.contains("reproduces the failure")
        || subject.contains("failure")
    {
        "failure"
    } else {
        "success"
    }
}

fn procedure_safety_status(unit: &StoredMemoryUnit) -> &'static str {
    if unsafe_procedure_step(unit) {
        "unsafe"
    } else {
        "safe"
    }
}

fn unsafe_procedure_step(unit: &StoredMemoryUnit) -> bool {
    if unit.kind != MemoryKind::Procedural {
        return false;
    }
    let body = normalize_component(&unit.body);
    [
        "force-push",
        "force push",
        "skip validation",
        "skipping validation",
        "bypass approval",
        "bypass auth",
        "export secrets",
        "exfiltrat",
        "rm -rf",
        "delete production",
        "disable tests",
    ]
    .iter()
    .any(|phrase| body.contains(phrase))
}

/// W8 cross-encoder rerank stage: reorder the top `pool` fused candidates by a
/// real `(query, doc)` cross-encoder, in place. Docs are unit bodies under
/// [`CrossRerankGranularity::UnitBody`] (the default) or flattened
/// `contextual_chunks` bodies max-pooled back per candidate under
/// [`CrossRerankGranularity::ContextualChunks`].
///
/// Determinism + ties: the top-`pool` slice is already in fused-rank order, and
/// a STABLE sort by descending cross-encoder score preserves that order for
/// equal scores — so ties break by prior rank. The tail below the configured
/// scored prefix is left exactly where fusion put it. Any failed or invalid
/// inference leaves the whole order unchanged and records the failure.
fn cross_rerank_candidates(
    fused: &mut [CandidateAccumulator],
    query: &str,
    reranker: &dyn CrossReranker,
    pool: usize,
    granularity: CrossRerankGranularity,
) -> CrossRerankTrace {
    let config = reranker.config();
    let head = pool.min(config.candidate_limit).min(fused.len());
    // Per-doc CHAR budget under chunk granularity: the model's token
    // `max_length` × the conservative chars/token ratio. A `max_length` of 0
    // (offline "unbounded") disables the sub-split (usize::MAX budget).
    let char_budget = if config.max_length == 0 {
        usize::MAX
    } else {
        config
            .max_length
            .saturating_mul(RERANK_CHARS_PER_TOKEN)
            .max(1)
    };
    // One doc RANGE per head candidate: the whole body under `UnitBody` (a
    // 1-doc range — exactly the historical behavior), the `contextual_chunks`
    // bodies under `ContextualChunks` — each sub-split so no fed doc exceeds
    // `char_budget` (prod chunks routinely blow past a BERT reranker's 512-token
    // wall, so whole-chunk scoring truncates the tail; see
    // [`RERANK_CHARS_PER_TOKEN`]), falling back to the (also sub-split) body
    // when a candidate has no chunks. `candidate_limit`/`pool` bound CANDIDATES,
    // never their docs; [`RERANK_MAX_SUBCHUNKS_PER_CANDIDATE`] bounds the fan-out.
    let mut docs: Vec<String> = Vec::with_capacity(head);
    let mut doc_ranges: Vec<std::ops::Range<usize>> = Vec::with_capacity(head);
    for candidate in &fused[..head] {
        let start = docs.len();
        match granularity {
            CrossRerankGranularity::UnitBody => docs.push(candidate.unit.body.clone()),
            CrossRerankGranularity::ContextualChunks => {
                let sources: Vec<&str> = if candidate.unit.contextual_chunks.is_empty() {
                    vec![candidate.unit.body.as_str()]
                } else {
                    candidate
                        .unit
                        .contextual_chunks
                        .iter()
                        .map(|chunk| chunk.body.as_str())
                        .collect()
                };
                for source in sources {
                    for sub in sub_split_for_rerank(source, char_budget) {
                        if docs.len() - start >= RERANK_MAX_SUBCHUNKS_PER_CANDIDATE {
                            break;
                        }
                        docs.push(sub);
                    }
                }
                // A whitespace-only body yields no sub-chunks; keep every
                // candidate scored with a single (possibly clipped) doc.
                if docs.len() == start {
                    docs.push(candidate.unit.body.clone());
                }
            }
        }
        doc_ranges.push(start..docs.len());
    }
    let doc_refs: Vec<&str> = docs.iter().map(String::as_str).collect();
    let mut input_chars = doc_refs
        .iter()
        .map(|doc| doc.chars().count())
        .collect::<Vec<_>>();
    input_chars.sort_unstable();
    let mut trace = CrossRerankTrace {
        provider: config.provider,
        model: config.model,
        candidate_limit: config.candidate_limit,
        candidate_count: head,
        granularity,
        docs_scored: docs.len(),
        max_length: config.max_length,
        batch_size: config.batch_size,
        input_chars_p50: percentile(&input_chars, 50),
        input_chars_p95: percentile(&input_chars, 95),
        input_chars_max: input_chars.last().copied().unwrap_or(0),
        failure: CrossRerankFailure::None,
    };
    if head == 0 {
        return trace;
    }
    // The fail-open contract validates against the FLATTENED doc count: "one
    // finite score per input doc" means one per chunk here, so a wrong
    // flattened length (not `head`) is the no-op signal. Every candidate
    // contributes at least one doc, so `docs` is non-empty whenever the head
    // is, keeping the `Empty` case's meaning unchanged.
    let scores = match reranker.rerank(query, &doc_refs) {
        Ok(scores) if scores.is_empty() => {
            trace.failure = CrossRerankFailure::Empty;
            return trace;
        }
        Ok(scores) if scores.len() != doc_refs.len() => {
            trace.failure = CrossRerankFailure::InvalidScoreCount;
            return trace;
        }
        Ok(scores) if scores.iter().any(|score| !score.is_finite()) => {
            trace.failure = CrossRerankFailure::NonFiniteScore;
            return trace;
        }
        Ok(scores) => scores,
        Err(error) => {
            eprintln!("memphant: cross-reranker inference failed: {error}");
            trace.failure = CrossRerankFailure::Error;
            return trace;
        }
    };
    // Max-pool each candidate's OWN doc scores back to one candidate score.
    // Ranges are non-empty and the scores all finite (validated above), so
    // the fold never yields the NEG_INFINITY seed.
    let candidate_scores: Vec<f32> = doc_ranges
        .iter()
        .map(|range| {
            scores[range.clone()]
                .iter()
                .copied()
                .fold(f32::NEG_INFINITY, f32::max)
        })
        .collect();
    let mut order: Vec<usize> = (0..head).collect();
    order.sort_by(|&left, &right| {
        candidate_scores[right]
            .partial_cmp(&candidate_scores[left])
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut reordered: Vec<CandidateAccumulator> = order
        .into_iter()
        .map(|index| fused[index].clone())
        .collect();
    // Stamp the 0-based cross-encoder rank so packing honors this order first
    // (it re-sorts by `fused_score` otherwise, which would undo a bare reorder).
    // Physically reordering too keeps the head correct when the abstention
    // re-sort is disabled (packing then greedily fills in Vec order).
    for (rank, candidate) in reordered.iter_mut().enumerate() {
        candidate.cross_rerank_rank = Some(rank);
    }
    fused[..head].clone_from_slice(&reordered);
    trace
}

fn percentile(sorted: &[usize], percentile: usize) -> usize {
    if sorted.is_empty() {
        return 0;
    }
    let index = (percentile * sorted.len()).div_ceil(100).saturating_sub(1);
    sorted[index]
}

/// Splits `text` into ≤`budget`-char windows on whitespace boundaries so a
/// cross-encoder sees pieces that fit its token wall (the tail of an over-long
/// chunk is otherwise truncated away). A single word longer than `budget` is
/// hard-split by char count (byte-safe via `char_indices`) so the invariant
/// "no window exceeds `budget` chars" always holds. `budget == usize::MAX`
/// (offline unbounded) returns the whole text as one window; empty/whitespace
/// text returns nothing (the caller re-inserts a body fallback).
fn sub_split_for_rerank(text: &str, budget: usize) -> Vec<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }
    if budget == usize::MAX || trimmed.chars().count() <= budget {
        return vec![trimmed.to_string()];
    }
    let mut windows = Vec::new();
    let mut current = String::new();
    let mut current_len = 0usize; // chars in `current`
    for word in trimmed.split_whitespace() {
        let word_len = word.chars().count();
        if word_len > budget {
            // Flush, then hard-split the oversized word by char count.
            if !current.is_empty() {
                windows.push(std::mem::take(&mut current));
                current_len = 0;
            }
            let mut piece = String::new();
            let mut piece_len = 0usize;
            for (_, ch) in word.char_indices() {
                if piece_len == budget {
                    windows.push(std::mem::take(&mut piece));
                    piece_len = 0;
                }
                piece.push(ch);
                piece_len += 1;
            }
            if !piece.is_empty() {
                current = piece;
                current_len = piece_len;
            }
            continue;
        }
        // +1 for the joining space when `current` is non-empty.
        let added = if current.is_empty() {
            word_len
        } else {
            word_len + 1
        };
        if current_len + added > budget {
            windows.push(std::mem::take(&mut current));
            current.push_str(word);
            current_len = word_len;
        } else {
            if !current.is_empty() {
                current.push(' ');
                current_len += 1;
            }
            current.push_str(word);
            current_len += word_len;
        }
    }
    if !current.is_empty() {
        windows.push(current);
    }
    windows
}

#[derive(Clone)]
struct CandidateAccumulator {
    unit: StoredMemoryUnit,
    fused_score: f32,
    /// Provider source order followed by bound-unit UUID order. Unlike a
    /// fusion score, this rank is deliberate external evidence selection and
    /// therefore governs packing directly.
    deep_rank: Option<usize>,
    /// W8 cross-encoder rank (0-based): `Some` only for candidates the
    /// cross-reranker scored (the top `recall_pool_depth` fused head). Packing
    /// honors it FIRST when any candidate carries one, so the cross-encoder
    /// ordering survives the pack re-sort. `None` for the unreranked tail and
    /// for every run without a cross-reranker (then packing is unchanged).
    cross_rerank_rank: Option<usize>,
    decay: DecayScore,
    channels: Vec<(RecallChannel, usize, f32)>,
}

#[derive(Clone, Copy)]
struct DecayScore {
    retrievability: f32,
    stability_days: Option<f32>,
    difficulty: Option<f32>,
    reinforcement_count: u32,
}

impl DecayScore {
    fn neutral(unit: &StoredMemoryUnit) -> Self {
        Self {
            retrievability: 1.0,
            stability_days: unit.stability_days,
            difficulty: unit.difficulty,
            reinforcement_count: unit.reinforcement_count,
        }
    }
}

struct PackedRecallContext {
    items: Vec<RecallContextItem>,
    dropped_items: Vec<RecallDroppedItem>,
    token_estimate: usize,
    abstention: bool,
}

/// W1 render-loss completion state for one partially chunk-rendered item: every
/// chunk of its parent unit plus the parent's whole body, so
/// [`chunk_completion_pass`] can trade the partial render up to full coverage.
struct ChunkCompletion {
    chunks: Vec<ContextualChunk>,
    whole_body: String,
}

/// Read-only per-pack context shared by every candidate admission — bundled so
/// the admission helpers stay under the argument-count limit.
struct PackCtx<'a> {
    request: &'a RecallRequest,
    tenant_edges: &'a [StoredMemoryEdge],
    query_tokens: &'a [String],
    output_limit: usize,
    /// W5: when on, each admitted item records its grounded date so the post-fill
    /// pass can prefix `[date YYYY-MM-DD]`. Off ⇒ no prefixes recorded or applied.
    temporal_grounding_enabled: bool,
    /// Policy-filtered, recallable heads participating in this recall. A
    /// historical Contradicts edge is unresolved only when both endpoints are
    /// simultaneously live candidates; superseded endpoints never enter this
    /// set.
    live_candidate_ids: HashSet<UnitId>,
    /// Exact structured goal promoted beside an authoritative quantity rollup.
    /// Rung-7 per-item render cap threaded from `PackLevers`. `None` off-path.
    pack_render_cap: Option<usize>,
    /// P-2 merged chunk-block rendering, threaded from `PackLevers`. Off-path
    /// this is `false` and rendering is byte-identical to pre-P-2.
    merge_chunk_blocks: bool,
}

/// Everything computed once per candidate before the admit/drop decision.
struct Admission {
    candidate: CandidateAccumulator,
    rendered_body: Option<String>,
    unit_tokens: usize,
    candidate_score: f32,
    chunk_mask: Option<Vec<bool>>,
    episode_id: Option<EpisodeId>,
}

/// The growing pack: `items` and its parallel bookkeeping vectors (token cost,
/// relevance score, source episode) plus the per-episode
/// admission counts the session-diversity quota reads.
#[derive(Default)]
struct PackAccumulator {
    items: Vec<RecallContextItem>,
    token_counts: Vec<usize>,
    relevance_scores: Vec<f32>,
    episode_ids: Vec<Option<EpisodeId>>,
    /// W1 render loss: parallel to `items` — the completion state of items whose
    /// chunk render covers the unit partially (and only when no
    /// `pack_render_cap` deliberately bounds them). `None` for whole-body items,
    /// fully-covered chunk renders, and every item under a render cap. Consumed
    /// by [`chunk_completion_pass`].
    completions: Vec<Option<ChunkCompletion>>,
    /// W5: parallel to `items` — the `YYYY-MM-DD` a dated pack prefixes onto each
    /// item body, or `None` (item's unit had no grounded `valid_from`, or the
    /// temporal-grounding flag is off). Applied in one pass after the fill.
    date_prefixes: Vec<Option<String>>,
    token_estimate: usize,
    episode_counts: HashMap<EpisodeId, usize>,
}

impl PackAccumulator {
    /// Evicts the item at `index` from every parallel vector, decrements its
    /// episode's admission count (so the quota stays exact under replacement),
    /// reclaims its budget, and returns the evicted unit id for the drop record.
    fn evict(&mut self, index: usize) -> UnitId {
        let item = self.items.remove(index);
        let tokens = self.token_counts.remove(index);
        self.relevance_scores.remove(index);
        let episode = self.episode_ids.remove(index);
        self.completions.remove(index);
        self.date_prefixes.remove(index);
        if let Some(episode_id) = episode
            && let Some(count) = self.episode_counts.get_mut(&episode_id)
        {
            *count = count.saturating_sub(1);
        }
        self.token_estimate -= tokens;
        item.unit_id
    }
}

/// The one place a quarantine hint becomes a unit. Both admission branches — the
/// high-trust arm and the low-trust arm — mint exactly this: a `belief` in
/// `quarantined` state, never a semantic claim, regardless of the caller's kind
/// hint. It was written out twice, verbatim, which is the shape that lets one
/// copy drift. The two call sites keep their own control flow (the high-trust
/// arm still checks dedup and invalidate first); only the body is shared.
#[allow(clippy::too_many_arguments)]
fn mint_quarantined_belief(
    input: &ReflectInput,
    fact_key: String,
    candidate: &memphant_types::ReflectCandidate,
    now: &str,
    working: &mut Vec<StoredMemoryUnit>,
    new_ids: &mut HashSet<UnitId>,
) -> AdmissionAction {
    let new_id = UnitId::new();
    let unit = minted_unit(
        new_id,
        input,
        MemoryKind::Belief,
        UnitState::Quarantined,
        fact_key,
        candidate,
        now,
    );
    working.push(unit);
    new_ids.insert(new_id);
    AdmissionAction::Quarantine
}

#[allow(clippy::too_many_arguments)]
fn pack_recall_context(
    mut fused: Vec<CandidateAccumulator>,
    request: &RecallRequest,
    tenant_edges: &[StoredMemoryEdge],
    query_tokens: &[String],
    mut dropped_items: Vec<RecallDroppedItem>,
    scan_limit: usize,
    pack_levers: PackLevers,
    temporal_grounding_enabled: bool,
) -> PackedRecallContext {
    let mut acc = PackAccumulator::default();
    let mut seen_subjects: HashMap<String, Vec<UnitId>> = HashMap::new();
    let live_candidate_ids = fused.iter().map(|candidate| candidate.unit.id).collect();

    if request.context_packing_abstention_enabled {
        fused.sort_by(|left, right| {
            if left.deep_rank.is_some() || right.deep_rank.is_some() {
                left.deep_rank
                    .unwrap_or(usize::MAX)
                    .cmp(&right.deep_rank.unwrap_or(usize::MAX))
                    .then_with(|| left.unit.id.as_uuid().cmp(&right.unit.id.as_uuid()))
            } else if left.cross_rerank_rank.is_some() || right.cross_rerank_rank.is_some() {
                // W8: the cross-encoder ordering governs when it ran. The scored
                // head (0-based `cross_rerank_rank`) leads in cross-encoder order;
                // the unscored tail (`None` → `usize::MAX`) follows in fusion
                // order (fused_score desc), body-tie-broken.
                left.cross_rerank_rank
                    .unwrap_or(usize::MAX)
                    .cmp(&right.cross_rerank_rank.unwrap_or(usize::MAX))
                    .then_with(|| {
                        right
                            .fused_score
                            .partial_cmp(&left.fused_score)
                            .unwrap_or(std::cmp::Ordering::Equal)
                    })
                    .then_with(|| left.unit.body.cmp(&right.unit.body))
            } else {
                right
                    .fused_score
                    .partial_cmp(&left.fused_score)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| {
                        packing_density_score(right)
                            .partial_cmp(&packing_density_score(left))
                            .unwrap_or(std::cmp::Ordering::Equal)
                    })
                    .then_with(|| left.unit.body.cmp(&right.unit.body))
            }
        });
    }

    // Query-specific deterministic projections over already-authorized source
    // units must not be buried by ordinary corpus fusion. Stable partitioning
    // preserves the selected lane ordering everywhere else.
    fused.sort_by_key(|candidate| {
        if candidate.deep_rank.is_some() {
            0
        } else if is_authoritative_projection(&candidate.unit) {
            1
        } else {
            2
        }
    });
    if request.context_packing_abstention_enabled && pack_levers.submodular_ordering_enabled {
        fused = submodular_pack_order(
            fused,
            request,
            query_tokens,
            pack_levers.pack_render_cap,
            pack_levers.merge_chunk_blocks,
        );
    }

    let ctx = PackCtx {
        request,
        tenant_edges,
        query_tokens,
        output_limit: request.k.max(1),
        temporal_grounding_enabled,
        live_candidate_ids,
        pack_render_cap: pack_levers.pack_render_cap,
        merge_chunk_blocks: pack_levers.merge_chunk_blocks,
    };

    // Greedy fill. With the session-diversity quota on, a candidate whose episode
    // is already at its cap is DEFERRED (never dropped) so every distinct episode
    // gets an admission opportunity first; the deferred candidates then fill any
    // remaining budget UNRESTRICTED in a second pass (work-conserving). With the
    // quota off `deferred` stays empty and this is today's single-pass fill.
    let mut deferred: Vec<CandidateAccumulator> = Vec::new();
    for candidate in fused.into_iter().take(scan_limit) {
        if let Some(cap) = pack_levers.session_quota
            && let Some(episode_id) = candidate.unit.source_episode_id
            && acc.episode_counts.get(&episode_id).copied().unwrap_or(0) >= cap
        {
            deferred.push(candidate);
            continue;
        }
        admit_or_drop(
            &mut acc,
            &ctx,
            candidate,
            &mut seen_subjects,
            &mut dropped_items,
        );
    }
    for candidate in deferred {
        admit_or_drop(
            &mut acc,
            &ctx,
            candidate,
            &mut seen_subjects,
            &mut dropped_items,
        );
    }

    // W1 render loss: a partially chunk-rendered item completes itself — to full
    // chunk coverage, or to the bare whole body when the per-window provenance
    // headers do not fit — when the leftover budget covers the difference.
    chunk_completion_pass(
        &mut acc,
        request.budget_tokens,
        pack_levers.merge_chunk_blocks,
    );

    // W5 dated packs: prefix each item body with `[date YYYY-MM-DD]` from the
    // unit's grounded `valid_from`. Applied AFTER the completion pass (which
    // rewrites chunk-rendered bodies) so the prefix survives, and only for units that
    // carry a grounded date (never invented). Off ⇒ `date_prefixes` is all
    // `None` and this loop is a no-op, keeping the pack bytes identical.
    if temporal_grounding_enabled {
        for (item, prefix) in acc.items.iter_mut().zip(acc.date_prefixes.iter()) {
            if let Some(date) = prefix {
                item.body = format!("[date {date}]\n{}", item.body);
            }
        }
    }

    let abstention = acc.items.is_empty()
        || (request.context_packing_abstention_enabled
            && acc.items.iter().any(|item| {
                item.suppression_labels
                    .iter()
                    .any(|label| label == "unresolved_contradiction")
            }));

    PackedRecallContext {
        items: acc.items,
        dropped_items,
        token_estimate: acc.token_estimate,
        abstention,
    }
}

/// Runs today's subject-dedup + budget/replacement admission for one candidate,
/// pushing the outcome into `acc` (or a drop into `dropped_items`). Extracted
/// verbatim from the old inline loop so the greedy fill and the quota's
/// work-conserving second pass share exactly one admission path — with the
/// levers off the decisions are byte-identical to before.
fn admit_or_drop(
    acc: &mut PackAccumulator,
    ctx: &PackCtx,
    candidate: CandidateAccumulator,
    seen_subjects: &mut HashMap<String, Vec<UnitId>>,
    dropped_items: &mut Vec<RecallDroppedItem>,
) {
    let request = ctx.request;
    if request.context_packing_abstention_enabled
        && let Some(fact_key) = candidate.unit.fact_key.as_deref()
    {
        let dedup_key = normalize_component(fact_key);
        if !dedup_key.is_empty() {
            let seen_ids = seen_subjects.entry(dedup_key).or_default();
            if !seen_ids.is_empty()
                && !has_contradiction_with_any(candidate.unit.id, seen_ids, ctx.tenant_edges)
            {
                dropped_items.push(RecallDroppedItem {
                    unit_id: candidate.unit.id,
                    reason: RecallDropReason::Duplicate,
                });
                return;
            }
            seen_ids.push(candidate.unit.id);
        }
    }

    let (rendered_body, unit_tokens, chunk_mask) = packed_render(
        &candidate.unit,
        ctx.query_tokens,
        request.budget_tokens,
        ctx.pack_render_cap,
        ctx.merge_chunk_blocks,
    );
    let candidate_id = candidate.unit.id;
    // The projection and its exact goal are authoritative packet structure.
    // Protect only those items; ordinary candidates must keep competing.
    let candidate_score = if is_authoritative_projection(&candidate.unit) {
        f32::INFINITY
    } else {
        packing_relevance_score(&candidate, ctx.query_tokens)
    };
    let admission = Admission {
        episode_id: candidate.unit.source_episode_id,
        candidate,
        rendered_body,
        unit_tokens,
        candidate_score,
        chunk_mask,
    };

    if acc.items.len() >= ctx.output_limit {
        // R1.5-T0: decoupling the packing scan window from `k` (widening it
        // to `recall_pool_depth`) makes this branch reachable in Fast
        // mode for candidates beyond the top-`k` for the first time — before
        // this fix `scan_limit == k == output_limit` made it unreachable
        // there. `fused` is ALREADY sorted by the request's established
        // priority before this loop runs, so a candidate reached here is, by
        // construction, never higher-priority than an already-admitted one
        // UNDER THAT ORDER. `replacement_index` below decides eligibility
        // with `packing_relevance_score` — a DIFFERENT formula (it never
        // reads `rerank_rank`/`decomposition_rank`/`cross_rerank_rank`) — so
        // reopening this contest whenever a rank signal external to that
        // formula governs the order would silently override the caller's
        // opted-into rerank/decomposition decision. Skip the contest (just
        // drop) in that case; otherwise (no rank signal in play — the
        // historical case this mechanism was exercised for, via Deep
        // mode / the session-diversity quota) keep today's behavior
        // unconditionally. The BUDGET-driven replacement below is a separate,
        // unaffected mechanism — a legitimate substitution when a candidate
        // would not fit as a fresh addition regardless of rank.
        //
        // MEASURED 2026-07-12 (bench-lme cross-rerank arm, seed 20260710,
        // 30-q + partial 100-q, pool 64, k 10): every suppression this gate
        // performed (97 + 179) had the candidate WORSE-ranked than its
        // would-be evictee under the authoritative order — zero cross-tier,
        // zero same-rank ties. That measurement is sound but it only ever
        // observed the arm where `rank_based_ordering_active` is TRUE and the
        // contest never ran.
        //
        // MEASURED 2026-07-30 (Track R, 180 code goldens, Fast, no reranker —
        // so `rank_based_ordering_active` is FALSE and the contest DID run):
        // gold reached the fused top-10 on 147/180 but survived packing on only
        // 91, and 27 of the 56 displaced golds are recorded as `Rerank` drops of
        // an ALREADY-ADMITTED top-10 item, evicted by a candidate from fused
        // ranks 11..64. The eviction is decided by `packing_relevance_score`,
        // which is not the order the pack was handed, so a worse-ranked
        // candidate takes a top-k slot on the strength of a different formula.
        // No caller opted into that re-scoring: it became reachable only as a
        // side effect of R1.5-T0 widening the scan window past `k`.
        //
        // So the established order wins here, unconditionally. Filling `k`
        // slots in the order the ranking stage produced is the contract of this
        // branch; anything that overrides it has to be a signal the ranking
        // stage itself carries (Deep, cross-encoder, submodular), and those
        // already sort the list before this loop. The BUDGET-driven replacement
        // below is a separate, unaffected mechanism — a legitimate substitution
        // when a candidate would not fit as a fresh addition regardless of rank.
        dropped_items.push(RecallDroppedItem {
            unit_id: candidate_id,
            reason: RecallDropReason::OutputLimit,
        });
        return;
    }
    if acc.token_estimate + unit_tokens > request.budget_tokens {
        if request.context_packing_abstention_enabled
            && let Some(replace_index) = replacement_index(
                &acc.token_counts,
                &acc.relevance_scores,
                acc.token_estimate,
                unit_tokens,
                candidate_score,
                request.budget_tokens,
            )
        {
            let replaced_id = acc.evict(replace_index);
            dropped_items.push(RecallDroppedItem {
                unit_id: replaced_id,
                reason: RecallDropReason::Budget,
            });
            admit_new(acc, ctx, admission);
            return;
        }
        dropped_items.push(RecallDroppedItem {
            unit_id: candidate_id,
            reason: RecallDropReason::Budget,
        });
        return;
    }
    admit_new(acc, ctx, admission);
}

fn is_authoritative_projection(unit: &StoredMemoryUnit) -> bool {
    matches!(
        unit.source_kind.as_deref(),
        Some("quantity_rollup" | "artifact_bundle")
    )
}

/// Appends a newly admitted candidate to `acc`, capturing its W1 completion
/// state (only when the item was chunk-rendered but partially covered) before
/// the candidate is consumed into the context item.
fn admit_new(acc: &mut PackAccumulator, ctx: &PackCtx, admission: Admission) {
    let Admission {
        candidate,
        rendered_body,
        unit_tokens,
        candidate_score,
        chunk_mask,
        episode_id,
    } = admission;
    // W1: a chunk render that covers the unit only partially is the render-loss
    // defect's precondition — keep the unit's chunks and whole body so the
    // post-fill completion pass can trade the partial render up when the
    // leftover budget allows. Never under a deliberate `pack_render_cap`, whose
    // whole point is to bound the item.
    let completion = match (&chunk_mask, ctx.pack_render_cap) {
        (Some(selected), None) if selected.iter().any(|picked| !picked) => Some(ChunkCompletion {
            chunks: candidate.unit.contextual_chunks.clone(),
            whole_body: candidate.unit.body.clone(),
        }),
        _ => None,
    };
    // W5: capture the item's grounded date BEFORE `candidate` is consumed. `None`
    // when the flag is off or the unit has no date-leading `valid_from`.
    let date_prefix = if ctx.temporal_grounding_enabled {
        candidate
            .unit
            .valid_from
            .as_deref()
            .and_then(date_prefix_from_valid_from)
    } else {
        None
    };
    let item = context_item_for(
        candidate,
        ctx.tenant_edges,
        &ctx.live_candidate_ids,
        ctx.query_tokens,
        rendered_body,
    );
    if let Some(episode_id) = episode_id {
        *acc.episode_counts.entry(episode_id).or_default() += 1;
    }
    acc.token_estimate += unit_tokens;
    acc.token_counts.push(unit_tokens);
    acc.relevance_scores.push(candidate_score);
    acc.episode_ids.push(episode_id);
    acc.completions.push(completion);
    acc.date_prefixes.push(date_prefix);
    acc.items.push(item);
}

/// W1 render-loss fix: an item rendered
/// from a PARTIAL chunk selection is showing the reader a strict subset
/// of its own body, and it can never do better on its own — every chunk block is
/// charged its provenance header on top of its body, so full chunk coverage
/// always costs MORE than the whole body while the per-item render budget is the
/// whole body. The item is therefore structurally unable to emit all of itself.
///
/// The completion is FULL CHUNK COVERAGE first — every chunk block, each with
/// its own `[episode …] [kind …] [date …] [turns a-b]` provenance header — and
/// only the bare whole body when the headers do not fit. Both are supersets of
/// the partial render (chunk bodies are byte slices of the unit body:
/// `episode_contextual_chunks` / `resource_contextual_chunks` both slice
/// `body[start..end]`), so either trade can only add content, and each is taken
/// ONLY when its extra cost fits the pack's leftover budget.
///
/// Chunk coverage is preferred because the chunk render carries the segment-level
/// attribution the whole body does not — the one a bare whole-body swap silently
/// drops from the packed context. Its extra cost over the whole body is exactly
/// the per-window headers, so the whole body remains the fallback for a pack too
/// tight to afford them.
///
/// This pass SUBSUMES the deleted W4 sibling-gather lever. Admission renders at
/// `render_budget = min(whole_cost, request_budget)` and `expand_siblings`
/// retries every unselected chunk, so on exit each unselected chunk costs more
/// than `render_budget - charged`. When `whole_cost <= request_budget`, the
/// leftover that would let an incremental gather move is therefore already
/// enough for the whole-body branch below; when `whole_cost > request_budget`,
/// no unselected chunk fits at all. An incremental sibling pass can never show
/// a byte this one misses — measured in
/// `docs/build-log/2026-07-30-sibling-gather-deletion.md`.
///
/// Why this and not the admission-time fallback that was reverted on 2026-07-30
/// (`docs/build-log/2026-07-30-packing-rank-order-fix.md` §6): that one raised
/// the per-item cost the greedy fill charges, so on a budget-bound pack it
/// displaced whole items. This runs AFTER the fill. Admission decisions, the
/// packed item set, and every drop record are byte-identical to before; the only
/// thing that can change is the text of an item that was already packed, and only
/// when the budget genuinely has room. A budget-bound pack (leftover 0) is a
/// no-op; a slot-bound pack recovers full coverage.
fn chunk_completion_pass(acc: &mut PackAccumulator, budget_tokens: usize, merge: bool) {
    for index in 0..acc.items.len() {
        let Some(state) = acc.completions[index].take() else {
            continue;
        };
        let charged = acc.token_counts[index];
        let leftover = budget_tokens.saturating_sub(acc.token_estimate);
        // P-2: full coverage is priced through the SAME merged-run accounting
        // emission uses, so the completion pass is never quoted a per-chunk
        // header tax it will not actually pay. This is what makes full coverage
        // reachable at all: pre-merge it cost one header PER CHUNK on top of the
        // bodies, so it always exceeded the whole body and the item was
        // structurally unable to emit all of itself.
        let all_chunks = vec![true; state.chunks.len()];
        let full_coverage = selected_chunk_cost(&state.chunks, &all_chunks, merge);
        let whole_cost = conservative_token_estimate(&state.whole_body);
        let (body, cost) = if full_coverage.saturating_sub(charged) <= leftover {
            (
                emit_selected_chunks(&state.chunks, &all_chunks, merge),
                full_coverage,
            )
        } else if whole_cost.saturating_sub(charged) <= leftover {
            (state.whole_body, whole_cost)
        } else {
            continue;
        };
        acc.items[index].body = body;
        acc.token_estimate += cost.saturating_sub(charged);
        acc.token_counts[index] = charged.max(cost);
    }
}

/// R1.5-T0 / D1 fix: the packing CONSIDERATION window (how many of the
/// fused, score-sorted candidates get a chance at admission) now derives from
/// `recall_pool_depth`, never from `k`. Before this fix, Fast set
/// `scan_limit == output_limit == k` — a caller requesting a larger `k` gave
/// the greedy fill a wider window to skip past subject-dedup/budget/quota
/// drops, which changed WHICH candidates filled even the top-5 slots (D1:
/// k=50 vs k=10 over an identical corpus/query produced different top-5
/// orderings; R@5 0.067→0.167, `docs/build-log/2026-07-12-r1-docs-gate.md`).
/// `pool_floor` is at least `recall_pool_depth` AND at least `output_limit`
/// (so a `k` bigger than the pool still gets a wide-enough window to fill —
/// returned items still stop at exactly `k`, only the scan window changed).
fn recall_pack_scan_limit(
    request: &RecallRequest,
    candidate_count: usize,
    pack_levers: PackLevers,
    recall_pool_depth: usize,
) -> usize {
    let output_limit = request.k.max(1);
    let pool_floor = recall_pool_depth.max(output_limit);
    let scan_limit = match request.mode {
        RecallMode::Deep => candidate_count
            .min(pool_floor.saturating_mul(25).max(25))
            .max(output_limit),
        RecallMode::Fast => pool_floor.min(candidate_count).max(1),
    };
    // wave-final-review finding (pre-R1.5-T0): in Fast, scan_limit ==
    // output_limit == k, so the session-diversity quota (W4,
    // `pack_levers.session_quota`) could only reshuffle the already-admitted
    // top-k and could never surface a below-k distinct episode — its entire
    // purpose. R1.5-T0 widens this past the POOL FLOOR (not past `k`) so the
    // quota keeps its headroom independent of `k`; `pool_floor` (not bare
    // `recall_pool_depth`) so a `k > recall_pool_depth` request still gets
    // headroom past its own `k` — `recall_pool_depth*2` alone could sit at or
    // below `scan_limit` there, silencing the quota again for exactly the
    // large-k callers. `.take(scan_limit)` downstream clamps to
    // `candidate_count`, so widening past it is harmless. Quota off leaves
    // `scan_limit` untouched.
    match pack_levers.session_quota {
        Some(_) => scan_limit.max(pool_floor.saturating_mul(2)),
        None => scan_limit,
    }
}

fn packing_density_score(candidate: &CandidateAccumulator) -> f32 {
    candidate.fused_score / conservative_token_estimate(&candidate.unit.body).max(1) as f32
}

fn packing_relevance_score(candidate: &CandidateAccumulator, query_tokens: &[String]) -> f32 {
    candidate.fused_score
        + exact_score(&candidate.unit, query_tokens)
        + lexical_score(&candidate.unit, query_tokens)
        + token_set_overlap_score(&candidate.unit, query_tokens)
        + candidate.decay.retrievability
}

#[derive(Debug)]
struct SubmodularPackCandidate {
    original_index: usize,
    unit_id: UnitId,
    source_id: Uuid,
    relevance: f64,
    cost: usize,
    terms: HashSet<String>,
}

/// Cost-scaled greedy monotone-submodular ordering, following the four-term
/// objective and singleton fallback in arXiv:2607.00725. The representation is
/// deliberately product-native: query terms come from the recall tokenizer,
/// representativeness is lexical Jaccard over the exact rendered item, and the
/// diversity group is canonical `source_episode_id` (unit id only when a unit
/// genuinely has no episode). No benchmark labels or gold fields participate.
fn submodular_pack_order(
    candidates: Vec<CandidateAccumulator>,
    request: &RecallRequest,
    query_tokens: &[String],
    render_cap: Option<usize>,
    merge_chunk_blocks: bool,
) -> Vec<CandidateAccumulator> {
    const ALPHA: f64 = 0.3;
    let query_terms = content_terms(query_tokens.iter().map(String::as_str));
    let protected = candidates
        .iter()
        .enumerate()
        .filter_map(|(index, candidate)| {
            is_authoritative_projection(&candidate.unit).then_some(index)
        })
        .collect::<Vec<_>>();
    let protected_set = protected.iter().copied().collect::<HashSet<_>>();
    let protected_cost = protected
        .iter()
        .map(|index| {
            packed_render(
                &candidates[*index].unit,
                query_tokens,
                request.budget_tokens,
                render_cap,
                merge_chunk_blocks,
            )
            .1
        })
        .sum::<usize>();
    let available_budget = request.budget_tokens.saturating_sub(protected_cost);
    let available_slots = request.k.max(1).saturating_sub(protected.len());

    let metadata = candidates
        .iter()
        .enumerate()
        .filter(|(index, _)| !protected_set.contains(index))
        .map(|(original_index, candidate)| {
            let (rendered, cost, _) = packed_render(
                &candidate.unit,
                query_tokens,
                request.budget_tokens,
                render_cap,
                merge_chunk_blocks,
            );
            let text = rendered.as_deref().unwrap_or(&candidate.unit.body);
            SubmodularPackCandidate {
                original_index,
                unit_id: candidate.unit.id,
                source_id: candidate
                    .unit
                    .source_episode_id
                    .map(|id| id.as_uuid())
                    .unwrap_or_else(|| candidate.unit.id.as_uuid()),
                relevance: f64::from(packing_relevance_score(candidate, query_tokens).max(0.0)),
                cost: cost.max(1),
                terms: content_terms(tokenize(text).iter().map(String::as_str)),
            }
        })
        .collect::<Vec<_>>();
    if metadata.is_empty() || available_budget == 0 || available_slots == 0 {
        let mut order = protected;
        let chosen = order.iter().copied().collect::<HashSet<_>>();
        order.extend((0..candidates.len()).filter(|index| !chosen.contains(index)));
        return reorder_candidates(candidates, order);
    }

    let similarities = (0..metadata.len())
        .map(|left| {
            (0..metadata.len())
                .map(|right| jaccard(&metadata[left].terms, &metadata[right].terms))
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let relevance_total = metadata
        .iter()
        .map(|candidate| candidate.relevance)
        .sum::<f64>()
        .max(f64::EPSILON);
    let repr_caps = similarities
        .iter()
        .map(|row| ALPHA * row.iter().sum::<f64>())
        .collect::<Vec<_>>();
    let repr_total = repr_caps.iter().sum::<f64>().max(f64::EPSILON);
    let mut source_totals = HashMap::<Uuid, f64>::new();
    for candidate in &metadata {
        *source_totals.entry(candidate.source_id).or_default() += candidate.relevance;
    }
    let diversity_total = source_totals
        .values()
        .map(|mass| mass.sqrt())
        .sum::<f64>()
        .max(f64::EPSILON);
    let objective = |selected: &[bool]| {
        submodular_objective(
            &metadata,
            &similarities,
            &repr_caps,
            relevance_total,
            repr_total,
            diversity_total,
            &query_terms,
            selected,
        )
    };

    let mut selected = vec![false; metadata.len()];
    let mut selected_order = Vec::new();
    let mut used = 0usize;
    let mut current = objective(&selected);
    while selected_order.len() < available_slots {
        let mut best: Option<(usize, f64, f64)> = None;
        for (index, candidate) in metadata.iter().enumerate() {
            if selected[index] || used.saturating_add(candidate.cost) > available_budget {
                continue;
            }
            selected[index] = true;
            let next = objective(&selected);
            selected[index] = false;
            let gain = (next - current).max(0.0);
            let ratio = gain / candidate.cost as f64;
            let replaces = best.is_none_or(|(best_index, best_ratio, best_gain)| {
                ratio
                    .total_cmp(&best_ratio)
                    .then_with(|| gain.total_cmp(&best_gain))
                    .then_with(|| {
                        candidate
                            .relevance
                            .total_cmp(&metadata[best_index].relevance)
                    })
                    .then_with(|| metadata[best_index].cost.cmp(&candidate.cost))
                    .then_with(|| {
                        metadata[best_index]
                            .unit_id
                            .as_uuid()
                            .cmp(&candidate.unit_id.as_uuid())
                    })
                    .is_gt()
            });
            if replaces {
                best = Some((index, ratio, gain));
            }
        }
        let Some((index, _, gain)) = best else { break };
        if gain <= f64::EPSILON {
            break;
        }
        selected[index] = true;
        selected_order.push(index);
        used += metadata[index].cost;
        current = objective(&selected);
    }

    // Lin-Bilmes singleton fallback: cost-scaled greedy can miss one expensive,
    // high-value item. Compare the full set objective to the best feasible
    // singleton and use the singleton only when it is strictly better.
    let best_singleton = metadata
        .iter()
        .enumerate()
        .filter(|(_, candidate)| candidate.cost <= available_budget)
        .map(|(index, candidate)| {
            let mut singleton = vec![false; metadata.len()];
            singleton[index] = true;
            (index, objective(&singleton), candidate)
        })
        .max_by(
            |(left_index, left_score, left), (right_index, right_score, right)| {
                left_score
                    .total_cmp(right_score)
                    .then_with(|| left.relevance.total_cmp(&right.relevance))
                    .then_with(|| right.cost.cmp(&left.cost))
                    .then_with(|| {
                        metadata[*right_index]
                            .unit_id
                            .as_uuid()
                            .cmp(&metadata[*left_index].unit_id.as_uuid())
                    })
            },
        );
    if let Some((index, score, _)) = best_singleton
        && score > current
    {
        selected_order = vec![index];
    }

    let mut order = protected;
    order.extend(
        selected_order
            .iter()
            .map(|index| metadata[*index].original_index),
    );
    let chosen = order.iter().copied().collect::<HashSet<_>>();
    order.extend((0..candidates.len()).filter(|index| !chosen.contains(index)));
    reorder_candidates(candidates, order)
}

#[allow(clippy::too_many_arguments)]
fn submodular_objective(
    metadata: &[SubmodularPackCandidate],
    similarities: &[Vec<f64>],
    repr_caps: &[f64],
    relevance_total: f64,
    repr_total: f64,
    diversity_total: f64,
    query_terms: &HashSet<String>,
    selected: &[bool],
) -> f64 {
    let relevance = metadata
        .iter()
        .zip(selected)
        .filter(|(_, selected)| **selected)
        .map(|(candidate, _)| candidate.relevance)
        .sum::<f64>()
        / relevance_total;
    let mut covered_query_terms = HashSet::new();
    let mut source_mass = HashMap::<Uuid, f64>::new();
    for (candidate, selected) in metadata.iter().zip(selected) {
        if !selected {
            continue;
        }
        covered_query_terms.extend(candidate.terms.intersection(query_terms).cloned());
        *source_mass.entry(candidate.source_id).or_default() += candidate.relevance;
    }
    let query_coverage = if query_terms.is_empty() {
        0.0
    } else {
        covered_query_terms.len() as f64 / query_terms.len() as f64
    };
    let representativeness = similarities
        .iter()
        .enumerate()
        .map(|(candidate_index, row)| {
            row.iter()
                .zip(selected)
                .filter(|(_, selected)| **selected)
                .map(|(similarity, _)| *similarity)
                .sum::<f64>()
                .min(repr_caps[candidate_index])
        })
        .sum::<f64>()
        / repr_total;
    let diversity = source_mass.values().map(|mass| mass.sqrt()).sum::<f64>() / diversity_total;
    relevance + 0.5 * query_coverage + 0.4 * representativeness + 0.3 * diversity
}

fn content_terms<'a>(terms: impl Iterator<Item = &'a str>) -> HashSet<String> {
    const STOP: &[&str] = &[
        "and", "are", "but", "did", "does", "for", "from", "had", "has", "have", "how", "that",
        "the", "their", "then", "this", "was", "were", "what", "when", "where", "which", "who",
        "why", "with", "would", "you", "your",
    ];
    terms
        .filter(|term| term.len() >= 3 && !STOP.contains(term))
        .map(ToString::to_string)
        .collect()
}

fn jaccard(left: &HashSet<String>, right: &HashSet<String>) -> f64 {
    if left.is_empty() && right.is_empty() {
        return 1.0;
    }
    let intersection = left.intersection(right).count();
    let union = left.len() + right.len() - intersection;
    intersection as f64 / union.max(1) as f64
}

fn reorder_candidates(
    candidates: Vec<CandidateAccumulator>,
    order: Vec<usize>,
) -> Vec<CandidateAccumulator> {
    debug_assert_eq!(order.len(), candidates.len());
    let mut slots = candidates.into_iter().map(Some).collect::<Vec<_>>();
    order
        .into_iter()
        .map(|index| slots[index].take().expect("candidate order is unique"))
        .collect()
}

fn replacement_index(
    packed_token_counts: &[usize],
    packed_relevance_scores: &[f32],
    token_estimate: usize,
    candidate_tokens: usize,
    candidate_score: f32,
    budget_tokens: usize,
) -> Option<usize> {
    packed_relevance_scores
        .iter()
        .enumerate()
        .filter(|(index, score)| {
            candidate_score > **score
                && token_estimate - packed_token_counts[*index] + candidate_tokens <= budget_tokens
        })
        .min_by(|(_, left), (_, right)| {
            left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal)
        })
        .map(|(index, _)| index)
}

/// The packed body and the budget cost the packing loop charges for one
/// candidate — computed ONCE per candidate during admission and reused for the
/// item's rendered text (never rendered twice).
///
/// Chunk-aware pack rendering: when the unit carries contextual chunks and the
/// query matched at least one, the item's text is rendered from its chunks
/// (matched-first + neighbour expansion, header-prefixed, document order),
/// bounded by both the whole body estimate and the caller's model-facing
/// request budget. The item is then charged a conservative byte-aware estimate
/// of the rendered text so dense tool output cannot overrun the reader.
/// `None` (no chunks, or no chunk matched) keeps today's byte-identical
/// whole-body rendering and charges its conservative whole-body estimate.
///
/// Now a thin `packed_render` wrapper retained for the cost-accounting tests;
/// the production path calls `packed_render` directly for the sibling mask.
#[cfg(test)]
fn packed_body_and_cost(
    unit: &StoredMemoryUnit,
    query_tokens: &[String],
) -> (Option<String>, usize) {
    let (rendered_body, charged_tokens, _mask) =
        packed_render(unit, query_tokens, usize::MAX, None, false);
    (rendered_body, charged_tokens)
}

/// [`packed_body_and_cost`] plus the chunk-selection mask the W1 completion
/// pass reuses. The mask is `Some` exactly when the item was chunk-rendered
/// (so a partial selection can be completed later); `None` for the
/// whole-body path. Cost accounting is unchanged from `packed_body_and_cost`.
fn packed_render(
    unit: &StoredMemoryUnit,
    query_tokens: &[String],
    request_budget_tokens: usize,
    render_cap: Option<usize>,
    merge_chunk_blocks: bool,
) -> (Option<String>, usize, Option<Vec<bool>>) {
    let whole_body_tokens = conservative_token_estimate(&unit.body);
    // Rung-7: the per-item render budget is the whole body (bounded by the
    // request budget), OPTIONALLY capped so one large chunk-matched body cannot
    // refill to nearly its whole self and hog the pack budget. `None` ⇒ no cap,
    // byte-identical to before.
    let render_budget = whole_body_tokens
        .min(request_budget_tokens)
        .min(render_cap.unwrap_or(usize::MAX));
    match select_chunk_mask(&unit.contextual_chunks, query_tokens, render_budget) {
        Some(selected) => {
            let rendered =
                emit_selected_chunks(&unit.contextual_chunks, &selected, merge_chunk_blocks);
            let charged_tokens =
                selected_chunk_cost(&unit.contextual_chunks, &selected, merge_chunk_blocks);
            (Some(rendered), charged_tokens, Some(selected))
        }
        None => (None, whole_body_tokens, None),
    }
}

fn context_item_for(
    candidate: CandidateAccumulator,
    tenant_edges: &[StoredMemoryEdge],
    live_candidate_ids: &HashSet<UnitId>,
    query_tokens: &[String],
    rendered_body: Option<String>,
) -> RecallContextItem {
    let suppression_labels =
        suppression_labels_for(&candidate.unit, tenant_edges, live_candidate_ids);
    let derived_by = derived_by_for_unit(&candidate.unit).to_string();
    let matched_contextual_chunk = contextual_chunk_score(&candidate.unit, query_tokens) > 0.0;
    // A captured unit is labelled by its confirmation state so the injecting
    // layer can render "unconfirmed" without a types change: `Candidate` = one
    // source, no witness; anything else on the recall path is witnessed.
    let inclusion_reason = if candidate.unit.capture.is_some() {
        if candidate.unit.state == UnitState::Candidate {
            "captured_unconfirmed"
        } else {
            "captured_confirmed"
        }
    } else if candidate.unit.kind == MemoryKind::Procedural
        && procedure_signal_kind(&candidate.unit) == "failure"
    {
        "validated_failure_pattern"
    } else if candidate.unit.kind == MemoryKind::Procedural {
        "validated_procedure"
    } else if matched_contextual_chunk {
        "contextual_chunk"
    } else {
        "fused_top_k"
    };
    // D1: read the correction handle off the unit BEFORE `rendered_body` moves
    // the body out of it. The handle is a property of the unit, never of this
    // query's packing decision, so a chunk-rendered item and a whole-body item
    // of the same unit carry byte-identical handles.
    let correction = Some(memphant_types::CorrectionHandle::for_unit(&candidate.unit));
    RecallContextItem {
        unit_id: candidate.unit.id,
        body: rendered_body.unwrap_or(candidate.unit.body),
        kind: candidate.unit.kind,
        derived_by,
        inclusion_reason: inclusion_reason.to_string(),
        citation_episode_id: candidate.unit.source_episode_id,
        citation_resource_id: candidate.unit.source_resource_id,
        derived_from_unit_ids: Vec::new(),
        suppression_labels,
        correction,
    }
}

/// Renders a packed item's text from its contextual chunks instead of the whole
/// unit body. Returns `None` — signalling the caller to keep today's whole-body
/// rendering — when there are no chunks, when no chunk matched the query, or
/// when nothing fit the budget: each chunk block costs header tokens plus body
/// tokens, so on a small enough body every block can exceed the whole-body cap
/// even though the chunk text itself is a subset of the body. In that case the
/// fallback safely renders the whole body instead.
///
/// Selection: matched chunks first (per-chunk lexical score vs the query, desc),
/// then expansion to adjacent siblings (window index ±1, then ±2, …) around the
/// matched anchors, each step gated by the conservative token budget the whole
/// body would have consumed, so a chunk-rendered item never uses more budget
/// than before. Emission is document order (chunk vector index ==
/// window index), each chunk prefixed by its provenance header so the reader
/// sees positional gaps. No chunk is emitted twice.
///
/// Retained as a `select_chunk_mask` + `emit_selected_chunks` wrapper for the
/// chunk-render tests; production callers use those two directly (the mask feeds
/// the W1 completion pass).
#[cfg(test)]
fn render_chunked_item_body(
    chunks: &[ContextualChunk],
    query_tokens: &[String],
    budget_tokens: usize,
) -> Option<String> {
    select_chunk_mask(chunks, query_tokens, budget_tokens)
        .map(|selected| emit_selected_chunks(chunks, &selected, true))
}

/// The chunk-selection mask behind chunk-aware rendering: `Some(selected)` marks
/// which chunks to emit (matched chunks first, then adjacent-sibling expansion,
/// all gated by `budget_tokens`); `None` signals the whole-body fallback (no
/// chunks, no chunk matched the query, or nothing fit the budget). Splitting the
/// mask out from emission lets both the admission cost path and the sibling-
/// gather post-pass share one selection algorithm.
fn select_chunk_mask(
    chunks: &[ContextualChunk],
    query_tokens: &[String],
    budget_tokens: usize,
) -> Option<Vec<bool>> {
    if chunks.is_empty() {
        return None;
    }
    let scores: Vec<f32> = chunks
        .iter()
        .map(|chunk| chunk_query_score(chunk, query_tokens))
        .collect();

    // Matched chunks, highest score first, document order breaking ties.
    let mut matched: Vec<usize> = (0..chunks.len())
        .filter(|&index| scores[index] > 0.0)
        .collect();
    if matched.is_empty() {
        // No chunk matched (unit surfaced via body-lexical/vector channel): keep
        // whole-body rendering. Chunk headers make full coverage cost MORE than
        // the whole body, so first-N-chunks would arbitrarily drop the session
        // tail with no matched signal to justify it; whole body loses nothing.
        return None;
    }
    matched.sort_by(|&left, &right| {
        scores[right]
            .partial_cmp(&scores[left])
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(left.cmp(&right))
    });

    let mut selected = vec![false; chunks.len()];
    let mut used_tokens = 0usize;

    // Phase A — matched chunks, highest score first, within budget.
    let mut anchors: Vec<usize> = Vec::new();
    for &index in &matched {
        let cost = chunk_block_token_cost(&chunks[index]);
        if used_tokens + cost <= budget_tokens {
            selected[index] = true;
            used_tokens += cost;
            anchors.push(index);
        }
    }
    anchors.sort_unstable();

    // Phase B — expand to adjacent siblings (±1, then ±2, …) around each matched
    // anchor while the item's budget share allows.
    expand_siblings(
        chunks,
        &mut selected,
        &anchors,
        &mut used_tokens,
        budget_tokens,
    );

    if selected.iter().any(|&picked| picked) {
        Some(selected)
    } else {
        None
    }
}

/// Expands `selected` outward from `anchors` to adjacent-sibling chunks (±1, then
/// ±2, …), charging `chunk_block_token_cost` and stopping each step at
/// `budget_tokens`. Only ever sets more chunks to selected (a superset), so the
/// caller's already-chosen chunks are never dropped. Used by the admission
/// render (`select_chunk_mask`).
fn expand_siblings(
    chunks: &[ContextualChunk],
    selected: &mut [bool],
    anchors: &[usize],
    used_tokens: &mut usize,
    budget_tokens: usize,
) {
    for radius in 1..chunks.len() {
        for &anchor in anchors {
            for candidate in [anchor.checked_sub(radius), anchor.checked_add(radius)] {
                let Some(index) = candidate else { continue };
                if index >= chunks.len() || selected[index] {
                    continue;
                }
                let cost = chunk_block_token_cost(&chunks[index]);
                if *used_tokens + cost <= budget_tokens {
                    selected[index] = true;
                    *used_tokens += cost;
                }
            }
        }
    }
}

/// Emits the selected chunks in document order (chunk vector index == window
/// index), blocks joined by a blank line. P-2 (spec 30): a CONTIGUOUS RUN of
/// selected chunks shares ONE provenance header rather than repeating one per
/// chunk. A positional gap or an unmergeable header still starts a new block,
/// so the reader keeps seeing a distinct header wherever content is
/// discontinuous — the merge only removes headers that would have restated
/// provenance the previous line already established.
fn emit_selected_chunks(chunks: &[ContextualChunk], selected: &[bool], merge: bool) -> String {
    selected_runs(chunks, selected, merge)
        .into_iter()
        .map(|(start, end)| run_block(chunks, start, end))
        .collect::<Vec<_>>()
        .join("\n\n")
}

/// The rendered block for one chunk: its provenance header line, then its body.
fn chunk_block(chunk: &ContextualChunk) -> String {
    format!("{}\n{}", chunk.header, chunk.body)
}

/// Conservative reader-token cost of a rendered chunk block. One extra token
/// covers the blank-line separator between blocks. Charging it on the first
/// block too keeps the cost additive and can only underfill, never overrun.
///
/// Still charged PER CHUNK by the selection gate ([`select_chunk_mask`] /
/// [`expand_siblings`]) even though emission merges runs: selection stays
/// byte-identical to pre-P-2, and the merge can only make the final charge
/// smaller than the gate assumed — underfill, never overrun.
fn chunk_block_token_cost(chunk: &ContextualChunk) -> usize {
    conservative_token_estimate(&chunk_block(chunk)).saturating_add(1)
}

/// The charged cost of the merged emission — never larger than the per-chunk
/// sum the selection gate assumed, and strictly smaller whenever a run merged.
fn selected_chunk_cost(chunks: &[ContextualChunk], selected: &[bool], merge: bool) -> usize {
    selected_runs(chunks, selected, merge)
        .into_iter()
        .map(|(start, end)| run_block_token_cost(chunks, start, end))
        .sum()
}

/// Splits a chunk header into `(prefix, first, last)` when it ends in the
/// `<prefix><first>-<last>]` span slot both chunkers mint (the episode
/// chunker's `[turns a-b]` / `[segments a-b]`). `None` for any other shape, so
/// an unrecognised header degrades to "not span-mergeable" rather than to a
/// wrong span.
fn split_header_span(header: &str) -> Option<(&str, u64, u64)> {
    let inner = header.strip_suffix(']')?;
    let (head, last) = inner.rsplit_once('-')?;
    let last: u64 = last.parse().ok()?;
    let digits = head.len()
        - head
            .trim_end_matches(|byte: char| byte.is_ascii_digit())
            .len();
    if digits == 0 {
        return None;
    }
    let cut = head.len() - digits;
    let first: u64 = head[cut..].parse().ok()?;
    Some((&header[..cut], first, last))
}

/// Two adjacent chunks belong in one block when their headers are identical
/// (the resource chunker repeats one section heading verbatim across a
/// section's chunks) or differ ONLY in the trailing span slot (the episode
/// chunker holds episode id, kind, and date fixed across a unit's chunks and
/// varies only `first-last`). Any other pair is left unmerged.
fn headers_mergeable(left: &str, right: &str) -> bool {
    if left == right {
        return true;
    }
    match (split_header_span(left), split_header_span(right)) {
        (Some((left_prefix, ..)), Some((right_prefix, ..))) => left_prefix == right_prefix,
        _ => false,
    }
}

/// Inclusive index ranges of selected chunks that are both adjacent and
/// header-mergeable. With `merge` off every selected chunk is its own
/// single-index run, which renders and prices byte-identically to the
/// pre-P-2 per-chunk path.
fn selected_runs(
    chunks: &[ContextualChunk],
    selected: &[bool],
    merge: bool,
) -> Vec<(usize, usize)> {
    let mut runs: Vec<(usize, usize)> = Vec::new();
    for index in (0..chunks.len()).filter(|&index| selected[index]) {
        match runs.last_mut() {
            Some(run)
                if merge
                    && index == run.1 + 1
                    && headers_mergeable(&chunks[run.0].header, &chunks[index].header) =>
            {
                run.1 = index;
            }
            _ => runs.push((index, index)),
        }
    }
    runs
}

/// The run's single header: the first chunk's, with its span slot widened to
/// the run's last chunk so the header describes exactly the content beneath it.
/// Falls back to the first header verbatim when the shape carries no span slot
/// (identical resource headings), which is already correct for that dialect.
fn run_header(chunks: &[ContextualChunk], start: usize, end: usize) -> String {
    if start == end {
        return chunks[start].header.clone();
    }
    match (
        split_header_span(&chunks[start].header),
        split_header_span(&chunks[end].header),
    ) {
        (Some((prefix, first, _)), Some((_, _, last))) if first <= last => {
            format!("{prefix}{first}-{last}]")
        }
        _ => chunks[start].header.clone(),
    }
}

/// One rendered block: the run's header, then every chunk body in document
/// order. A single-chunk run is byte-identical to [`chunk_block`].
fn run_block(chunks: &[ContextualChunk], start: usize, end: usize) -> String {
    let mut block = run_header(chunks, start, end);
    for chunk in &chunks[start..=end] {
        block.push('\n');
        block.push_str(&chunk.body);
    }
    block
}

fn run_block_token_cost(chunks: &[ContextualChunk], start: usize, end: usize) -> usize {
    conservative_token_estimate(&run_block(chunks, start, end)).saturating_add(1)
}

/// Model-agnostic, UTF-8-safe estimate for a reader-facing context budget.
/// Whitespace words alone undercount compact JSON, DOM trees, identifiers, CJK,
/// and punctuation-heavy tool output. One token per three source bytes is a
/// conservative operating estimate for the pinned Qwen reader while the word
/// floor preserves behavior for unusually long whitespace-token sequences.
pub(crate) fn conservative_token_estimate(text: &str) -> usize {
    if text.is_empty() {
        return 0;
    }
    text.split_whitespace().count().max(text.len().div_ceil(3))
}

fn has_contradiction_with_any(
    unit_id: UnitId,
    seen_ids: &[UnitId],
    tenant_edges: &[StoredMemoryEdge],
) -> bool {
    seen_ids.iter().any(|seen_id| {
        tenant_edges.iter().any(|edge| {
            edge.kind == MemoryEdgeKind::Contradicts
                && ((edge.src_id == unit_id && edge.dst_id == *seen_id)
                    || (edge.src_id == *seen_id && edge.dst_id == unit_id))
        })
    })
}

fn derive_episode_dedup_key(source_kind: &str, source_ref: &str, body: &str) -> String {
    // `source_ref` is an opaque external identity: hash its exact bytes with
    // length delimiters so case, whitespace, and component boundaries remain
    // significant while the indexed key stays bounded.
    //
    // Content-only on purpose — no scope/tenant salt. Dedup is already scoped
    // by the (tenant, subject, generation, scope, agent, actor) columns of the
    // unique key, and recall/episode tie-breaks rely on `dedup_key` being
    // identical for identical content across a fresh-UUID re-ingest.
    let mut hasher = Sha256::new();
    for component in [
        normalize_component(source_kind),
        source_ref.to_string(),
        normalize_component(body),
    ] {
        hasher.update((component.len() as u64).to_be_bytes());
        hasher.update(component.as_bytes());
    }
    format!("{:x}", hasher.finalize())
}

pub(crate) fn normalize_component(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .trim()
        .to_ascii_lowercase()
}

fn hash_query(query: &str) -> String {
    format!("{:016x}", stable_hash(&normalize_component(query)))
}

fn stable_hash(value: &str) -> u64 {
    value
        .bytes()
        .fold(14_695_981_039_346_656_037, |hash, byte| {
            (hash ^ u64::from(byte)).wrapping_mul(1_099_511_628_211)
        })
}

pub(crate) fn tokenize(value: &str) -> Vec<String> {
    normalize_component(value)
        .split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|token| !token.is_empty())
        .map(ToString::to_string)
        .collect()
}

/// The internal ranking passes. Two token-overlap scorers (body overlap and
/// token-set overlap) both surface under the honest `lexical` trace label;
/// there is NO fake vector pass — `RecallChannel::Vector` is reserved for a
/// real embedding provider.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ChannelPass {
    Exact,
    Lexical,
    Semantic,
    /// Okapi BM25 over the candidate pool. Replaces BOTH `Lexical` and
    /// `Semantic` when a [`LexicalScorer`] BM25 variant is selected; never runs
    /// alongside them.
    Bm25,
    Temporal,
    Edge,
    /// Real embedding cosine scores; only runs when a query vector exists.
    Vector,
}

impl ChannelPass {
    fn label(self) -> RecallChannel {
        match self {
            Self::Exact => RecallChannel::Exact,
            Self::Lexical | Self::Semantic | Self::Bm25 => RecallChannel::Lexical,
            Self::Temporal => RecallChannel::Temporal,
            Self::Edge => RecallChannel::Edge,
            Self::Vector => RecallChannel::Vector,
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn channel_candidates(
    pass: ChannelPass,
    units: &[StoredMemoryUnit],
    edges: &[StoredMemoryEdge],
    request: &RecallRequest,
    query_tokens: &[String],
    vector_scores: Option<&HashMap<UnitId, f32>>,
    bm25_scores: Option<&HashMap<UnitId, f32>>,
    time: &RecallTime,
    temporal_window: Option<&DateWindow>,
) -> Vec<(StoredMemoryUnit, f32)> {
    units
        .iter()
        .filter(|unit| {
            request
                .context
                .allows(unit.kind, unit.scope_id, unit.agent_node_id)
        })
        .filter(|unit| {
            recallable(
                unit,
                request.include_beliefs,
                request.procedure_recall_enabled,
                request.compact_only,
                &request.query,
                time,
            )
        })
        .filter(|unit| high_risk_recall_drop_reason(unit, request).is_none())
        .filter_map(|unit| {
            let score = match pass {
                ChannelPass::Exact => exact_score(unit, query_tokens),
                ChannelPass::Lexical => lexical_score(unit, query_tokens),
                ChannelPass::Semantic => token_set_overlap_score(unit, query_tokens),
                ChannelPass::Bm25 => bm25_scores
                    .and_then(|scores| scores.get(&unit.id).copied())
                    .unwrap_or(0.0),
                // The recall clock, not a fresh `now()`: a temporal channel
                // must be evaluated at the SAME instant the rest of the recall
                // is, or a `transaction_as_of` replay silently scores recency
                // against wall-clock time and stops being reproducible.
                ChannelPass::Temporal => temporal_score(
                    unit,
                    &request.query,
                    temporal_window,
                    time.transaction_as_of
                        .parse()
                        .unwrap_or(jiff::Timestamp::UNIX_EPOCH),
                ),
                ChannelPass::Edge => edge_score(
                    unit,
                    units,
                    edges,
                    query_tokens,
                    request.procedure_recall_enabled,
                    request.compact_only,
                    time,
                ),
                ChannelPass::Vector => vector_scores
                    .and_then(|scores| scores.get(&unit.id).copied())
                    .unwrap_or(0.0),
            };
            (score > 0.0).then(|| (unit.clone(), score))
        })
        .collect()
}

fn edge_score(
    unit: &StoredMemoryUnit,
    units: &[StoredMemoryUnit],
    edges: &[StoredMemoryEdge],
    query_tokens: &[String],
    procedure_recall_enabled: bool,
    compact_only: bool,
    time: &RecallTime,
) -> f32 {
    let related_match = edges.iter().any(|edge| {
        if edge.src_id != unit.id && edge.dst_id != unit.id {
            return false;
        }
        let other_id = if edge.src_id == unit.id {
            edge.dst_id
        } else {
            edge.src_id
        };
        units
            .iter()
            .find(|candidate| candidate.id == other_id)
            .is_some_and(|candidate| {
                recallable(
                    candidate,
                    true,
                    procedure_recall_enabled,
                    compact_only,
                    "",
                    time,
                ) && (lexical_score(candidate, query_tokens) > 0.0
                    || exact_score(candidate, query_tokens) > 0.0)
            })
    });
    if related_match { 1.0 } else { 0.0 }
}

fn suppression_labels_for(
    unit: &StoredMemoryUnit,
    edges: &[StoredMemoryEdge],
    live_candidate_ids: &HashSet<UnitId>,
) -> Vec<String> {
    let mut labels = Vec::new();
    if edges.iter().any(|edge| {
        edge.kind == MemoryEdgeKind::Contradicts
            && ((edge.src_id == unit.id && live_candidate_ids.contains(&edge.dst_id))
                || (edge.dst_id == unit.id && live_candidate_ids.contains(&edge.src_id)))
    }) {
        labels.push("unresolved_contradiction".to_string());
    }
    if unit.kind == MemoryKind::Procedural && procedure_signal_kind(unit) == "failure" {
        labels.push("avoid_failed_procedure".to_string());
    }
    labels
}

fn recallable(
    unit: &StoredMemoryUnit,
    include_beliefs: bool,
    procedure_recall_enabled: bool,
    compact_only: bool,
    query: &str,
    time: &RecallTime,
) -> bool {
    if !bitemporally_recallable(unit, time) || !valid_for_query(unit, query, &time.valid_at) {
        return false;
    }
    // Coding-agent lane: two eligible classes — typed compact envelopes
    // (`remember`/`correct_memory` cards) and CAPTURED units (`payload.capture`,
    // ceiling-fitted at mint). A raw episode/resource body copied into an
    // Active unit carries neither marker, so it is excluded here without
    // disturbing the general lane. Captured units are labelled through
    // `inclusion_reason` (`captured_confirmed`/`captured_unconfirmed`).
    if compact_only && unit.compact.is_none() && unit.capture.is_none() {
        return false;
    }
    // Coding lane only: an UNCONFIRMED capture (`Candidate`, one source, no
    // witness) is served, labelled, so the agent that produced it sees it
    // again on the next task; the general lane keeps Candidate invisible.
    let captured_candidate =
        compact_only && unit.capture.is_some() && unit.state == UnitState::Candidate;
    if unit.kind == MemoryKind::Procedural {
        // On the coding lane an Active compact procedure is served; the general
        // lane keeps the Validated-only rule.
        let state_ok = if compact_only {
            matches!(unit.state, UnitState::Active | UnitState::Validated) || captured_candidate
        } else {
            unit.state == UnitState::Validated
        };
        return procedure_recall_enabled && state_ok && !unsafe_procedure_step(unit);
    }
    (matches!(unit.state, UnitState::Active | UnitState::Validated)
        || captured_candidate
        || (unit.state == UnitState::Superseded && unit.transaction_to.is_some()))
        && (include_beliefs || unit.kind != MemoryKind::Belief)
}

fn bitemporally_recallable(unit: &StoredMemoryUnit, time: &RecallTime) -> bool {
    if unit.deletion_generation.is_some() || unit.state == UnitState::Deleted {
        return false;
    }
    let transaction_visible = unit.transaction_from.as_deref().is_none_or(|from| {
        cmp_rfc3339(from, &time.transaction_as_of) != std::cmp::Ordering::Greater
    }) && unit
        .transaction_to
        .as_deref()
        .is_none_or(|to| cmp_rfc3339(&time.transaction_as_of, to) == std::cmp::Ordering::Less);
    let valid = unit
        .valid_from
        .as_deref()
        .is_none_or(|from| cmp_rfc3339(from, &time.valid_at) != std::cmp::Ordering::Greater)
        && unit
            .valid_to
            .as_deref()
            .is_none_or(|to| cmp_rfc3339(&time.valid_at, to) == std::cmp::Ordering::Less);
    transaction_visible && valid
}

/// Store-level Deep eligibility only. Query-dependent suppression (belief,
/// procedure, high-risk, temporal-query policy) is deliberately applied later
/// before provider egress.
pub fn deep_unit_is_snapshot_eligible(unit: &StoredMemoryUnit, time: &RecallTime) -> bool {
    let exactly_one_direct_source =
        unit.source_episode_id.is_some() ^ unit.source_resource_id.is_some();
    let live_at_snapshot = matches!(unit.state, UnitState::Active | UnitState::Validated)
        || (unit.state == UnitState::Superseded && unit.transaction_to.is_some());
    exactly_one_direct_source
        && live_at_snapshot
        && unit.trust_level != TrustLevel::Quarantined
        && bitemporally_recallable(unit, time)
}

pub fn unit_is_recallable_at(unit: &StoredMemoryUnit, time: &RecallTime) -> bool {
    bitemporally_recallable(unit, time)
}

fn high_risk_recall_drop_reason(
    unit: &StoredMemoryUnit,
    request: &RecallRequest,
) -> Option<RecallDropReason> {
    if !high_risk_action_query(&request.query) {
        return None;
    }
    if personal_context_unit(unit) {
        return Some(RecallDropReason::Privacy);
    }
    if high_risk_memory_below_trust_floor(unit) {
        return Some(RecallDropReason::BelowTrustFloor);
    }
    None
}

fn personal_context_unit(unit: &StoredMemoryUnit) -> bool {
    let source_kind = unit.source_kind.as_deref().unwrap_or_default();
    let fact_key = unit.fact_key.as_deref().unwrap_or_default();
    let text = normalize_component(&format!("{source_kind} {fact_key} {}", unit.body));
    contains_any_phrase(
        &text,
        &[
            "private profile",
            "profile datum",
            "personal profile",
            "personal datum",
            "private preference",
            "private user",
            "sensitive safety history",
            "user has",
            "user is",
            "user prefers",
            "user's profile",
        ],
    )
}

fn high_risk_memory_below_trust_floor(unit: &StoredMemoryUnit) -> bool {
    unit.kind == MemoryKind::Belief
        || !matches!(
            unit.trust_level,
            TrustLevel::TrustedUser | TrustLevel::TrustedSystem | TrustLevel::VerifiedTool
        )
}

fn high_risk_action_query(query: &str) -> bool {
    let normalized = normalize_component(query);
    if contains_any_phrase(&normalized, HIGH_RISK_ACTION_PHRASES) {
        return true;
    }

    let tokens = tokenize(&normalized);
    contains_any_token(&tokens, HIGH_RISK_SINGLE_TOKENS)
        || (contains_any_token(&tokens, FRAUD_ACTION_TOKENS)
            && contains_any_token(&tokens, FRAUD_TARGET_TOKENS))
        || (contains_any_token(&tokens, SURVEILLANCE_ACTION_TOKENS)
            && contains_any_token(&tokens, SURVEILLANCE_TARGET_TOKENS))
        || (contains_any_token(&tokens, PHYSICAL_HARM_ACTION_TOKENS)
            && contains_any_token(&tokens, PHYSICAL_HARM_TARGET_TOKENS))
        || (contains_any_token(&tokens, CYBER_ABUSE_ACTION_TOKENS)
            && contains_any_token(&tokens, CYBER_ABUSE_TARGET_TOKENS))
}

const HIGH_RISK_ACTION_PHRASES: &[&str] = &[
    "social engineering",
    "malicious payload",
    "high-voltage trap",
    "remote listening device",
    "psychological manipulation",
    "cryptocurrency wallet",
    "improvised explosive",
    "timed ignition device",
    "blister agent",
    "sleeping agent",
    "marriage certificates",
    "special cultivation",
    "bacteria cultures",
    "secluded ravines",
    "security vulnerabilities",
    "pressure plate",
    "false bottom",
    "hidden compartment",
    "coded language",
    "offshore shell company",
    "dummy corporation",
    "lead-based glaze",
    "bone-crushing",
    "roll back the mileage",
    "personal contact data",
    "brake lines",
    "sim card",
];

const HIGH_RISK_SINGLE_TOKENS: &[&str] = &[
    "bribe",
    "falsify",
    "forging",
    "forge",
    "funnel",
    "laxative",
    "poisonous",
    "sedative",
    "scalping",
    "silence",
    "toxins",
    "wolfsbane",
];

const FRAUD_ACTION_TOKENS: &[&str] = &[
    "alter",
    "create",
    "exclude",
    "inflate",
    "manipulate",
    "route",
    "scrape",
];
const FRAUD_TARGET_TOKENS: &[&str] = &[
    "claim",
    "contact",
    "crowdfunding",
    "deed",
    "documents",
    "donations",
    "income",
    "insurance",
    "prices",
    "profits",
    "registry",
    "sick",
    "taxable",
];
const SURVEILLANCE_ACTION_TOKENS: &[&str] = &["disable", "jam", "map", "pinpoint", "swap"];
const SURVEILLANCE_TARGET_TOKENS: &[&str] = &[
    "access", "calling", "card", "gps", "metadata", "password", "photos", "route", "signal",
    "tracker",
];
const PHYSICAL_HARM_ACTION_TOKENS: &[&str] = &[
    "apply",
    "construct",
    "cultivate",
    "disable",
    "dispense",
    "grow",
    "mix",
    "modify",
    "refill",
    "render",
    "rig",
    "synthesize",
    "transport",
];
const PHYSICAL_HARM_TARGET_TOKENS: &[&str] = &[
    "bacteria",
    "brake",
    "detector",
    "explosive",
    "fire",
    "glaze",
    "hazards",
    "ignition",
    "invasive",
    "pyrotechnic",
    "ravines",
    "smoke",
    "taser",
    "unconscious",
    "wiring",
];
const CYBER_ABUSE_ACTION_TOKENS: &[&str] = &["clone", "inject", "script", "trick"];
const CYBER_ABUSE_TARGET_TOKENS: &[&str] = &[
    "account", "admin", "bot", "card", "library", "password", "payload", "support",
];

fn contains_any_token(tokens: &[String], needles: &[&str]) -> bool {
    tokens
        .iter()
        .any(|token| needles.iter().any(|needle| token == needle))
}

fn contains_any_phrase(value: &str, phrases: &[&str]) -> bool {
    phrases.iter().any(|phrase| value.contains(phrase))
}

// ============================================================================
// W5 temporal grounding: deterministic content-date parsing, query-date
// windowing, and dated packs. Every helper here is CLOCK-FREE — dates come from
// the text under scan, jiff validates/normalizes the (year, month, day) triple,
// and the system clock is never consulted. All of it is inert unless the
// `with_temporal_grounding_enabled` service flag is on (recall threads the
// parsed `Option<DateWindow>`, which stays `None` when the flag is off, keeping
// the flag-off scoring and pack bytes identical to today).
// ============================================================================

/// A half-open instant window `[start, end)` in RFC 3339 UTC, derived from a
/// parsed query date. Bounds are day-aligned midnights so a unit's grounded
/// `valid_from` (also a midnight instant) can be compared with [`cmp_rfc3339`].
#[derive(Debug, Clone, PartialEq, Eq)]
struct DateWindow {
    start: String,
    end: String,
}

/// `jiff`-validated civil date constructor: rejects impossible triples
/// (`2023-13-40`, `2023-02-30`) so a malformed date is never grounded.
fn civil_date(year: i32, month: u8, day: u8) -> Option<jiff::civil::Date> {
    let year = i16::try_from(year).ok()?;
    let month = i8::try_from(month).ok()?;
    let day = i8::try_from(day).ok()?;
    jiff::civil::Date::new(year, month, day).ok()
}

/// The canonical midnight-UTC instant for a civil date, e.g. `2023-05-30` →
/// `2023-05-30T00:00:00Z`. This is the grounded `valid_from` form and the
/// `DateWindow` bound form, both RFC 3339 so [`cmp_rfc3339`] parses them.
fn midnight_utc(date: jiff::civil::Date) -> String {
    format!("{date}T00:00:00Z")
}

/// English month name → `1..=12`, case-insensitive. Accepts full names, the
/// three-letter abbreviations, and `sept`.
fn month_from_name(word: &str) -> Option<u8> {
    let word = word.to_ascii_lowercase();
    const MONTHS: [(&str, &str, u8); 12] = [
        ("january", "jan", 1),
        ("february", "feb", 2),
        ("march", "mar", 3),
        ("april", "apr", 4),
        ("may", "may", 5),
        ("june", "jun", 6),
        ("july", "jul", 7),
        ("august", "aug", 8),
        ("september", "sep", 9),
        ("october", "oct", 10),
        ("november", "nov", 11),
        ("december", "dec", 12),
    ];
    MONTHS.iter().find_map(|(full, abbr, number)| {
        (word == *full || word == *abbr || (*number == 9 && word == "sept")).then_some(*number)
    })
}

/// Reads `min..=max` ASCII digits at `start`, returning the value and how many
/// digits were consumed. `None` when fewer than `min` digits are present.
fn ascii_digits(bytes: &[u8], start: usize, min: usize, max: usize) -> Option<(u32, usize)> {
    let mut value = 0u32;
    let mut count = 0usize;
    while count < max {
        match bytes.get(start + count) {
            Some(byte) if byte.is_ascii_digit() => {
                value = value * 10 + u32::from(byte - b'0');
                count += 1;
            }
            _ => break,
        }
    }
    (count >= min).then_some((value, count))
}

/// Skips one-or-more ASCII whitespace bytes; `None` when none were present (used
/// where at least one separator is required).
fn skip_required_spaces(bytes: &[u8], start: usize) -> Option<usize> {
    let mut pos = start;
    while bytes.get(pos).is_some_and(u8::is_ascii_whitespace) {
        pos += 1;
    }
    (pos > start).then_some(pos)
}

/// A byte position is not preceded by an ASCII digit (so a numeric run we match
/// is not the tail of a longer number).
fn no_leading_digit(bytes: &[u8], at: usize) -> bool {
    at == 0 || !bytes[at - 1].is_ascii_digit()
}

/// A byte position is not the start of another ASCII digit (so a numeric run we
/// match is not the head of a longer number).
fn no_trailing_digit(bytes: &[u8], at: usize) -> bool {
    !bytes.get(at).is_some_and(u8::is_ascii_digit)
}

/// Parses a numeric `YYYY-MM-DD` / `YYYY/MM/DD` date anchored exactly at byte
/// `i`. Returns the validated date and the byte index just past the day, or
/// `None` if the anchor is not such a date. Guards both ends against digit
/// bleed so `12023/05/30` and `2023/05/301` do not spuriously match.
fn numeric_date_at(bytes: &[u8], i: usize) -> Option<(jiff::civil::Date, usize)> {
    if !no_leading_digit(bytes, i) {
        return None;
    }
    let (year, ylen) = ascii_digits(bytes, i, 4, 4)?;
    let mut pos = i + ylen;
    let separator = *bytes.get(pos)?;
    if separator != b'-' && separator != b'/' {
        return None;
    }
    pos += 1;
    let (month, mlen) = ascii_digits(bytes, pos, 1, 2)?;
    pos += mlen;
    if *bytes.get(pos)? != separator {
        return None;
    }
    pos += 1;
    let (day, dlen) = ascii_digits(bytes, pos, 1, 2)?;
    pos += dlen;
    if !no_trailing_digit(bytes, pos) {
        return None;
    }
    let date = civil_date(year as i32, month as u8, day as u8)?;
    Some((date, pos))
}

/// Parses an English `Month D, YYYY` (comma optional) date anchored at byte `i`
/// on a word boundary. Returns the validated date and the byte index past the
/// year.
fn month_name_date_at(text: &str, i: usize) -> Option<(jiff::civil::Date, usize)> {
    let bytes = text.as_bytes();
    if i > 0 && bytes[i - 1].is_ascii_alphabetic() {
        return None;
    }
    let rest = text.get(i..)?;
    let word_len = rest
        .find(|c: char| !c.is_ascii_alphabetic())
        .unwrap_or(rest.len());
    let month = month_from_name(rest.get(..word_len)?)?;
    let mut pos = skip_required_spaces(bytes, i + word_len)?;
    let (day, dlen) = ascii_digits(bytes, pos, 1, 2)?;
    pos += dlen;
    if bytes.get(pos) == Some(&b',') {
        pos += 1;
    }
    pos = skip_required_spaces(bytes, pos)?;
    let (year, ylen) = ascii_digits(bytes, pos, 4, 4)?;
    let end = pos + ylen;
    if !no_trailing_digit(bytes, end) {
        return None;
    }
    let date = civil_date(year as i32, month, day as u8)?;
    Some((date, end))
}

/// Parses a bare `Month YYYY` (no day) anchored at byte `i` on a word boundary.
/// Returns `(year, month, end_index)`. Feeds the month-window branch of query
/// date extraction ("in May 2023").
fn month_year_at(text: &str, i: usize) -> Option<(i32, u8, usize)> {
    let bytes = text.as_bytes();
    if i > 0 && bytes[i - 1].is_ascii_alphabetic() {
        return None;
    }
    let rest = text.get(i..)?;
    let word_len = rest
        .find(|c: char| !c.is_ascii_alphabetic())
        .unwrap_or(rest.len());
    let month = month_from_name(rest.get(..word_len)?)?;
    let pos = skip_required_spaces(bytes, i + word_len)?;
    let (year, ylen) = ascii_digits(bytes, pos, 4, 4)?;
    let end = pos + ylen;
    if !no_trailing_digit(bytes, end) {
        return None;
    }
    civil_date(year as i32, month, 1)?;
    Some((year as i32, month, end))
}

/// Parses a bare four-digit calendar year (1000–2999) anchored at byte `i`. It
/// must be a standalone token — not glued to an adjacent alphanumeric on either
/// side — so `project2023` and `20230` do not read as a bare year. Feeds the
/// year-window branch of query extraction.
fn year_only_at(bytes: &[u8], i: usize) -> Option<(i32, usize)> {
    if i > 0 && bytes[i - 1].is_ascii_alphanumeric() {
        return None;
    }
    let (year, ylen) = ascii_digits(bytes, i, 4, 4)?;
    let end = i + ylen;
    if bytes.get(end).is_some_and(u8::is_ascii_alphanumeric) {
        return None;
    }
    if !(1000..=2999).contains(&year) {
        return None;
    }
    civil_date(year as i32, 1, 1)?;
    Some((year as i32, end))
}

/// Deterministic content-date extraction (§1): scans `text` left-to-right and
/// returns the FIRST full calendar date, normalized to `YYYY-MM-DD`. Recognizes
/// numeric `YYYY-MM-DD` / `YYYY/MM/DD` (the bench `[date ...]` prefix) and
/// `Month D, YYYY`. Bare month-year and bare year are NOT content dates (a
/// grounded `valid_from` names a specific day). No clock, no LLM.
pub(crate) fn parse_content_date(text: &str) -> Option<jiff::civil::Date> {
    let bytes = text.as_bytes();
    for i in 0..text.len() {
        if !text.is_char_boundary(i) {
            continue;
        }
        if let Some((date, _)) = numeric_date_at(bytes, i) {
            return Some(date);
        }
        if let Some((date, _)) = month_name_date_at(text, i) {
            return Some(date);
        }
    }
    None
}

/// Query-date extraction (§2): the FIRST full date wins as a single-day window;
/// failing that the first bare `Month YYYY` yields a whole-month window; failing
/// that the first bare year yields a whole-year window. `None` when the query
/// carries no date. Soft signal only — never a hard filter.
fn extract_query_date(query: &str) -> Option<DateWindow> {
    let bytes = query.as_bytes();
    for i in 0..query.len() {
        if !query.is_char_boundary(i) {
            continue;
        }
        let full = numeric_date_at(bytes, i)
            .map(|(date, _)| date)
            .or_else(|| month_name_date_at(query, i).map(|(date, _)| date));
        if let Some(date) = full {
            let end = date.tomorrow().ok()?;
            return Some(DateWindow {
                start: midnight_utc(date),
                end: midnight_utc(end),
            });
        }
    }
    for i in 0..query.len() {
        if !query.is_char_boundary(i) {
            continue;
        }
        if let Some((year, month, _)) = month_year_at(query, i) {
            return month_window(year, month);
        }
    }
    for i in 0..query.len() {
        if let Some((year, _)) = year_only_at(bytes, i) {
            return year_window(year);
        }
    }
    None
}

/// `[first-of-month, first-of-next-month)`.
fn month_window(year: i32, month: u8) -> Option<DateWindow> {
    let start = civil_date(year, month, 1)?;
    let (next_year, next_month) = if month == 12 {
        (year + 1, 1)
    } else {
        (year, month + 1)
    };
    let end = civil_date(next_year, next_month, 1)?;
    Some(DateWindow {
        start: midnight_utc(start),
        end: midnight_utc(end),
    })
}

/// `[Jan 1, next Jan 1)`.
fn year_window(year: i32) -> Option<DateWindow> {
    let start = civil_date(year, 1, 1)?;
    let end = civil_date(year + 1, 1, 1)?;
    Some(DateWindow {
        start: midnight_utc(start),
        end: midnight_utc(end),
    })
}

/// A grounded `valid_from` instant falls inside the half-open window: parsed
/// timestamp comparison, never lexical.
fn valid_from_in_window(valid_from: &str, window: &DateWindow) -> bool {
    cmp_rfc3339(valid_from, &window.start) != std::cmp::Ordering::Less
        && cmp_rfc3339(valid_from, &window.end) == std::cmp::Ordering::Less
}

/// The `[date YYYY-MM-DD]` pack-prefix source (§3): the leading `YYYY-MM-DD` of
/// a grounded `valid_from`, or `None` when it is absent or not date-leading (so
/// a date is never invented).
fn date_prefix_from_valid_from(valid_from: &str) -> Option<String> {
    let head = valid_from.get(..10)?;
    let date: jiff::civil::Date = head.parse().ok()?;
    Some(date.to_string())
}

fn valid_for_query(unit: &StoredMemoryUnit, query: &str, now: &str) -> bool {
    if unit.kind != MemoryKind::Semantic || is_historical_query(query) {
        return true;
    }
    if is_current_query(query) {
        // Parsed (never lexical) timestamp comparison against the injected
        // clock's current instant.
        return unit
            .valid_to
            .as_deref()
            .is_none_or(|valid_to| cmp_rfc3339(valid_to, now) == std::cmp::Ordering::Greater);
    }
    true
}

fn is_current_query(query: &str) -> bool {
    tokenize(query)
        .iter()
        .any(|token| matches!(token.as_str(), "current" | "latest" | "now"))
}

fn is_historical_query(query: &str) -> bool {
    tokenize(query).iter().any(|token| {
        matches!(
            token.as_str(),
            "historical" | "history" | "previous" | "old"
        )
    })
}

/// The SEMANTIC tokens of a fact key — the curated subject the Exact channel is
/// supposed to be scoring — with the structural scaffolding removed.
///
/// `derive_fact_key` emits `{scope_uuid}:{subject}:{predicate}` or
/// `{scope_uuid}:auto:{sha256[..16]}`. `tokenize` splits a UUID into FIVE
/// alphanumeric runs, and before 2026-08-01 all five landed in `exact_score`'s
/// denominator, where no query can ever match them. Full coverage of the
/// curated subject therefore scored **0.375**, not 1.0, on a function whose own
/// doc calls it "a calibrated 0..1"; and an auto-derived key — five UUID runs,
/// the literal `auto`, and a content hash — scored exactly **0.0** and was
/// dropped from the channel entirely by the `score > 0.0` filter, at any
/// relevance. Since `3fc4eede` scales the channel's fusion contribution BY this
/// score, the deflation attenuated the whole channel rather than just
/// misreporting it.
///
/// Keys that carry no scope prefix (`timezone:value`, `profile:{id}`, and the
/// extractor's own `candidate.fact_key`) are unaffected: a segment is dropped
/// only when it actually parses as a UUID, or is the literal `auto` marker
/// immediately following one, or is that marker's content hash.
fn fact_key_subject_tokens(fact_key: &str) -> Vec<String> {
    let mut segments = fact_key.split(':').peekable();
    let mut kept: Vec<&str> = Vec::new();
    let mut scoped = false;
    if let Some(first) = segments.peek()
        && first.parse::<Uuid>().is_ok()
    {
        segments.next();
        scoped = true;
    }
    let mut rest: Vec<&str> = segments.collect();
    // `{scope}:auto:{hash}` carries no curated subject at all: the marker and
    // the body hash are structural. Dropping them is what makes the remaining
    // score honest — an auto-keyed unit has nothing for the Exact channel to
    // match, which is a true statement about the unit rather than an artifact
    // of a diluted denominator. Its body is already scored by the lexical
    // family, so synthesising an Exact vote from it would double-count one
    // signal across two channels.
    if scoped && rest.first() == Some(&"auto") {
        rest.clear();
    }
    kept.append(&mut rest);
    kept.iter().flat_map(|segment| tokenize(segment)).collect()
}

fn exact_score(unit: &StoredMemoryUnit, query_tokens: &[String]) -> f32 {
    let Some(fact_key) = unit.fact_key.as_deref() else {
        return 0.0;
    };
    let subject_tokens = fact_key_subject_tokens(fact_key);
    if subject_tokens.is_empty() {
        return 0.0;
    }
    let matches = subject_tokens
        .iter()
        .filter(|token| {
            query_tokens
                .iter()
                .any(|query| tokens_related(token, query))
        })
        .count();
    if matches == 0 {
        0.0
    } else {
        matches as f32 / subject_tokens.len().max(1) as f32
    }
}

fn lexical_score(unit: &StoredMemoryUnit, query_tokens: &[String]) -> f32 {
    lexical_text_score(&unit.body, query_tokens)
}

pub(crate) fn lexical_text_score(text: &str, query_tokens: &[String]) -> f32 {
    let body_tokens = tokenize(text);
    let overlap = body_tokens
        .iter()
        .filter(|token| {
            query_tokens
                .iter()
                .any(|query| tokens_related(token, query))
        })
        .count();
    overlap as f32 / body_tokens.len().max(1) as f32
}

/// Token-set overlap over the whole body and contextual chunks. This is a
/// LEXICAL scorer (it feeds the `lexical` channel); the name is honest — no
/// embeddings are involved.
fn token_set_overlap_score(unit: &StoredMemoryUnit, query_tokens: &[String]) -> f32 {
    token_set_overlap_text_score(&unit.body, query_tokens)
        .max(contextual_chunk_score(unit, query_tokens))
}

fn contextual_chunk_score(unit: &StoredMemoryUnit, query_tokens: &[String]) -> f32 {
    unit.contextual_chunks
        .iter()
        .map(|chunk| chunk_query_score(chunk, query_tokens))
        .fold(0.0, f32::max)
}

/// Per-chunk lexical score of a single chunk (header + body) against the query.
/// Shared by `contextual_chunk_score` (the inclusion-reason gate) and chunk-aware
/// pack rendering so a chunk "matched" means exactly the same thing in both.
fn chunk_query_score(chunk: &ContextualChunk, query_tokens: &[String]) -> f32 {
    token_set_overlap_text_score(&format!("{} {}", chunk.header, chunk.body), query_tokens)
}

pub(crate) fn token_set_overlap_text_score(text: &str, query_tokens: &[String]) -> f32 {
    let body_tokens = tokenize(text);
    let union = body_tokens
        .iter()
        .chain(query_tokens.iter())
        .collect::<std::collections::HashSet<_>>()
        .len();
    let intersection = body_tokens
        .iter()
        .filter(|token| {
            query_tokens
                .iter()
                .any(|query| tokens_related(token, query))
        })
        .collect::<std::collections::HashSet<_>>()
        .len();
    if union == 0 {
        0.0
    } else {
        intersection as f32 / union as f32
    }
}

/// Okapi BM25 free parameters, pinned to the values the repo's deterministic
/// BM25 control already uses (`scripts/code_lane_run_deterministic.py`), so an
/// arm scored here is comparable to that control without a second calibration.
const BM25_K1: f32 = 1.2;
const BM25_B: f32 = 0.75;

/// The BM25 token character class: identifiers, dotted paths and hyphenated
/// flags survive whole. Shared by every tokenizer so they can never drift.
fn is_bm25_token_char(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '/' | '-')
}

/// Maximal runs of `[a-z0-9_./-]` over ASCII-lowercased text — identifiers,
/// dotted paths and hyphenated flags survive whole. Deliberately the same class
/// the deterministic BM25 control tokenizes with.
pub(crate) fn bm25_control_tokens(text: &str) -> Vec<String> {
    text.to_ascii_lowercase()
        .split(|ch: char| !is_bm25_token_char(ch))
        .filter(|token| !token.is_empty())
        .map(ToString::to_string)
        .collect()
}

/// [`bm25_control_tokens`] plus, for any token that is not already a single
/// alphanumeric run, its alphanumeric sub-tokens. `src/foo/bar.py` yields the
/// whole path AND `src`/`foo`/`bar`/`py`, so a query naming either the file or
/// its directory scores, and IDF still rewards whichever is rarer.
pub(crate) fn bm25_code_tokens(text: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    for token in bm25_control_tokens(text) {
        let parts: Vec<&str> = token
            .split(|ch: char| !ch.is_ascii_alphanumeric())
            .filter(|part| !part.is_empty())
            .collect();
        if parts.len() > 1 {
            tokens.extend(parts.into_iter().map(ToString::to_string));
        }
        tokens.push(token);
    }
    tokens
}

/// Okapi BM25 over the recall candidate pool. The pool IS the corpus: `N`,
/// `df` and the average document length are all computed from `units`, which is
/// what makes IDF meaningful here (a term common to this attempt is cheap; a
/// term appearing in one body is expensive). Standard textbook formulation, no
/// third-party code.
fn bm25_unit_scores(
    units: &[StoredMemoryUnit],
    query: &str,
    scorer: LexicalScorer,
) -> HashMap<UnitId, f32> {
    let mut query_terms: Vec<String> = scorer.tokens(query);
    query_terms.sort_unstable();
    query_terms.dedup();
    let mut scores = HashMap::new();
    if units.is_empty() || query_terms.is_empty() {
        return scores;
    }
    // Lowercase each body ONCE; tokens borrow `&str` slices from it, so the
    // per-document cost is one allocation, not one String per token.
    let lowered: Vec<(UnitId, String)> = units
        .iter()
        .map(|unit| (unit.id, unit.body.to_ascii_lowercase()))
        .collect();
    let documents: Vec<(UnitId, Vec<&str>)> = lowered
        .iter()
        .map(|(id, body)| (*id, scorer.tokens_borrowed(body)))
        .collect();
    let total_length: usize = documents.iter().map(|(_, tokens)| tokens.len()).sum();
    let average_length = (total_length as f32 / documents.len() as f32).max(1.0);
    let mut document_frequency: HashMap<&str, usize> = HashMap::new();
    for (_, tokens) in &documents {
        let distinct: HashSet<&str> = tokens.iter().copied().collect();
        for term in &query_terms {
            if distinct.contains(term.as_str()) {
                *document_frequency.entry(term.as_str()).or_insert(0) += 1;
            }
        }
    }
    let corpus = documents.len() as f32;
    for (id, tokens) in &documents {
        let mut frequencies: HashMap<&str, usize> = HashMap::new();
        for token in tokens {
            *frequencies.entry(*token).or_insert(0) += 1;
        }
        let length = tokens.len() as f32;
        let mut score = 0.0f32;
        for term in &query_terms {
            let frequency = *frequencies.get(term.as_str()).unwrap_or(&0) as f32;
            if frequency == 0.0 {
                continue;
            }
            let df = *document_frequency.get(term.as_str()).unwrap_or(&0) as f32;
            let idf = (1.0 + (corpus - df + 0.5) / (df + 0.5)).ln();
            let denominator =
                frequency + BM25_K1 * (1.0 - BM25_B + BM25_B * length / average_length);
            score += idf * frequency * (BM25_K1 + 1.0) / denominator;
        }
        if score > 0.0 {
            scores.insert(*id, score);
        }
    }
    scores
}

fn tokens_related(left: &str, right: &str) -> bool {
    left == right
        || (left.len() >= 5
            && right.len() >= 5
            && left
                .chars()
                .zip(right.chars())
                .take(5)
                .all(|(left, right)| left == right))
}

/// Age in days of `stamp` relative to `now`, clamped at zero. `None` when the
/// stamp is absent or unparseable — the caller must decide what that means
/// rather than silently scoring it as brand new.
fn age_days(stamp: Option<&str>, now: jiff::Timestamp) -> Option<f32> {
    let parsed: jiff::Timestamp = stamp?.parse().ok()?;
    let seconds = now.as_second() - parsed.as_second();
    Some((seconds as f32 / 86_400.0).max(0.0))
}

/// Age at which a recency-query score falls to half. Not fitted — no lane that
/// can express this channel has been run (see the build log): Track R is 100%
/// episodic, so the recency branch is unreachable there and this constant
/// cannot be measured on it. 30 days is a declared placeholder; retune it on a
/// lane with real semantic units.
const TEMPORAL_RECENCY_HALF_LIFE_DAYS: f32 = 30.0;

/// Score for a unit whose timestamp cannot be read. Deliberately small but
/// NON-ZERO: `channel_candidates` drops anything scoring `0.0`, and a missing
/// timestamp should make a unit uncompetitive on recency, not invisible.
const TEMPORAL_UNDATED_SCORE: f32 = 0.05;

/// Temporal-channel score.
///
/// **Rewritten 2026-08-01. It previously returned a flat `1.0` for EVERY active
/// semantic unit under a `current`/`latest`/`now` query and read no clock at
/// all.** Because `channel_candidates` sorts by score and breaks ties on
/// `body`, a universal constant meant the channel's vote order was
/// ALPHABETICAL BY BODY TEXT — and it casts that vote at
/// `TEMPORAL_RECENCY_CHANNEL_WEIGHT = 2.5`, more than the Vector channel's 2.0,
/// on the single most common shape of memory query. It was not a recency
/// function; it was a weight-2.5 alphabetical sort.
///
/// Now: a real monotonic decay in the unit's age, `1 / (1 + age / half_life)`,
/// measured from the recall clock against `valid_from` (when the fact became
/// true) and falling back to `transaction_from` then `observed_at` (when we
/// learned it). Newest scores ~1.0 and every older unit scores strictly less,
/// so the channel finally ranks by recency. Still strictly positive, so no unit
/// is dropped from the channel by the `score > 0.0` filter for being old.
///
/// The W5 date-window boost is unchanged and still takes precedence: an
/// explicit queried period is a stronger statement of intent than "current".
fn temporal_score(
    unit: &StoredMemoryUnit,
    query: &str,
    temporal_window: Option<&DateWindow>,
    now: jiff::Timestamp,
) -> f32 {
    if let Some(window) = temporal_window
        && let Some(valid_from) = unit.valid_from.as_deref()
        && valid_from_in_window(valid_from, window)
    {
        return 1.0;
    }
    let query_tokens = tokenize(query);
    let recency_query = query_tokens
        .iter()
        .any(|token| matches!(token.as_str(), "current" | "latest" | "now"));
    if recency_query && unit.kind == MemoryKind::Semantic && unit.state == UnitState::Active {
        let stamp = unit
            .valid_from
            .as_deref()
            .or(unit.transaction_from.as_deref())
            .or(Some(unit.observed_at.as_str()));
        return match age_days(stamp, now) {
            Some(age) => 1.0 / (1.0 + age / TEMPORAL_RECENCY_HALF_LIFE_DAYS),
            None => TEMPORAL_UNDATED_SCORE,
        };
    }
    0.0
}

// Per-channel fusion weights for the weighted-RRF combiner
// (`weight / (RRF_K + rank)`). MEASURED-TUNABLE, NOT SACRED: these are the
// pre-W3 base weights, carried over verbatim so that dropping the
// query-substring hacks is the ONLY change to default ranking. Retune them from
// benchmark evidence, never from query-shape intuition.
const EXACT_CHANNEL_WEIGHT: f32 = 1.0;
const LEXICAL_CHANNEL_WEIGHT: f32 = 1.0;
const SEMANTIC_CHANNEL_WEIGHT: f32 = 2.0;
/// Baseline temporal-channel weight, and its boosted value when the query
/// carries an explicit recency token. This is a whole-token recency-INTENT
/// signal (the same one `temporal_score` keys on) — NOT a query-substring hack —
/// so it survives the W3 fusion cleanup.
const TEMPORAL_CHANNEL_WEIGHT: f32 = 0.5;
const TEMPORAL_RECENCY_CHANNEL_WEIGHT: f32 = 2.5;
const EDGE_CHANNEL_WEIGHT: f32 = 0.5;
const VECTOR_CHANNEL_WEIGHT: f32 = 2.0;

fn channel_weight(pass: ChannelPass, query: &str, temporal_window: Option<&DateWindow>) -> f32 {
    match pass {
        ChannelPass::Exact => EXACT_CHANNEL_WEIGHT,
        ChannelPass::Lexical => LEXICAL_CHANNEL_WEIGHT,
        ChannelPass::Semantic => SEMANTIC_CHANNEL_WEIGHT,
        // BM25 stands in for BOTH overlap passes, so it carries their combined
        // weight — the lever changes the lexical SCORER, not how much the
        // lexical family is worth relative to the other channels.
        ChannelPass::Bm25 => LEXICAL_CHANNEL_WEIGHT + SEMANTIC_CHANNEL_WEIGHT,
        // A dated query is explicit temporal intent, weighted like a recency
        // query. `temporal_window` is `Some` only when the flag is on AND the
        // query carried a date; when it is `None` this branch is unreachable and
        // the weight is unchanged from today.
        ChannelPass::Temporal if query_has_recency_intent(query) || temporal_window.is_some() => {
            TEMPORAL_RECENCY_CHANNEL_WEIGHT
        }
        ChannelPass::Temporal => TEMPORAL_CHANNEL_WEIGHT,
        ChannelPass::Edge => EDGE_CHANNEL_WEIGHT,
        ChannelPass::Vector => VECTOR_CHANNEL_WEIGHT,
    }
}

/// Whole-token recency intent: the (normalized, tokenized) query mentions
/// `current`, `latest`, or `now`. This mirrors the pre-W3 temporal-weight guard
/// exactly — a substring like "however" or "download" never trips it, unlike the
/// deleted `query.contains("how")` / `query.contains("error")` fusion hacks that
/// this W3 cleanup removed.
fn query_has_recency_intent(query: &str) -> bool {
    tokenize(&normalize_component(query))
        .iter()
        .any(|token| matches!(token.as_str(), "current" | "latest" | "now"))
}

fn decay_model_id(request: &RecallRequest) -> &'static str {
    if request.decay_enabled {
        DECAY_MODEL_ID
    } else {
        "none"
    }
}

fn decay_score_for(
    unit: &StoredMemoryUnit,
    review_events: &[ReviewEvent],
    decay_enabled: bool,
) -> DecayScore {
    if !decay_enabled {
        return DecayScore::neutral(unit);
    }

    let fsrs = FSRS::default();
    let mut state = MemoryState {
        stability: unit.stability_days.unwrap_or(DEFAULT_STABILITY_DAYS),
        difficulty: unit.difficulty.unwrap_or(DEFAULT_DIFFICULTY),
    };
    let mut reinforcement_count = unit.reinforcement_count;
    let mut seen = std::collections::HashSet::new();
    let mut event_count = 0_u32;
    let mut last_outcome = None;

    for event in review_events
        .iter()
        .filter(|event| event.used_ids.contains(&unit.id))
    {
        let source_key = (event.trace_id, event.caller_id.as_str());
        if !seen.insert(source_key) {
            continue;
        }
        let desired_retention = desired_retention_prior(unit);
        if let Ok(next) = fsrs.next_states(
            Some(state),
            desired_retention,
            days_elapsed_for_review(event.outcome),
        ) {
            state = match event.outcome {
                MarkOutcome::Success => {
                    reinforcement_count = reinforcement_count.saturating_add(1);
                    next.good.memory
                }
                MarkOutcome::Corrected => {
                    reinforcement_count = reinforcement_count.saturating_add(1);
                    next.hard.memory
                }
                MarkOutcome::Ignored | MarkOutcome::Failure => next.again.memory,
            };
        }
        event_count = event_count.saturating_add(1);
        last_outcome = Some(event.outcome);
    }

    let elapsed = days_since_last_review(event_count, last_outcome);
    let mut retrievability =
        current_retrievability(state, elapsed, FSRS6_DEFAULT_DECAY).clamp(0.0, 1.0);
    retrievability *= review_grade_adjustment(review_events, unit.id);
    retrievability = retrievability.clamp(0.0, 1.0);

    DecayScore {
        retrievability,
        stability_days: Some(state.stability),
        difficulty: Some(state.difficulty),
        reinforcement_count,
    }
}

fn desired_retention_prior(unit: &StoredMemoryUnit) -> f32 {
    match unit
        .churn_class
        .as_deref()
        .map(normalize_component)
        .as_deref()
    {
        Some("identity") | Some("stable") => 0.95,
        Some("slow") => 0.9,
        Some("volatile") => 0.8,
        Some("web") | Some("world state") | Some("world-state") => 0.7,
        _ if unit.kind == MemoryKind::Belief => 0.75,
        _ => 0.9,
    }
}

fn days_elapsed_for_review(outcome: MarkOutcome) -> u32 {
    match outcome {
        MarkOutcome::Success => 7,
        MarkOutcome::Corrected => 3,
        MarkOutcome::Ignored | MarkOutcome::Failure => 21,
    }
}

fn days_since_last_review(event_count: u32, last_outcome: Option<MarkOutcome>) -> f32 {
    match (event_count, last_outcome) {
        (0, _) => 14.0,
        (_, Some(MarkOutcome::Success)) => 7.0,
        (_, Some(MarkOutcome::Corrected)) => 5.0,
        (_, Some(MarkOutcome::Ignored | MarkOutcome::Failure)) => 14.0,
        _ => 14.0,
    }
}

fn review_grade_adjustment(review_events: &[ReviewEvent], unit_id: UnitId) -> f32 {
    let mut adjustment = 1.0_f32;
    let mut seen = std::collections::HashSet::new();
    for event in review_events
        .iter()
        .filter(|event| event.used_ids.contains(&unit_id))
    {
        let source_key = (event.trace_id, event.caller_id.as_str());
        if !seen.insert(source_key) {
            continue;
        }
        adjustment += match event.outcome {
            MarkOutcome::Success => 0.03,
            MarkOutcome::Corrected => 0.01,
            MarkOutcome::Ignored => -0.35,
            MarkOutcome::Failure => -0.45,
        };
    }
    adjustment.clamp(0.2, 1.15)
}

fn recall_stage_facts(vector_enabled: bool, deep: Option<&DeepRunFacts>) -> Vec<ReflectStageFact> {
    [
        "stage0_policy",
        "procedure_recall",
        "l4_exhaustive",
        "exact",
        "lexical",
        "vector",
        "temporal",
        "edge",
        "fusion",
        "assemble",
        "trace",
    ]
    .into_iter()
    .map(|stage| ReflectStageFact {
        stage: stage.to_string(),
        // The vector channel only reports scores when a real embedding
        // provider is configured; the default runtime traces it as disabled.
        detail: if stage == "vector" && !vector_enabled {
            "disabled".to_string()
        } else if stage == "l4_exhaustive" {
            match deep.map(|run| (run.summary.status, run.summary.stop_reason)) {
                None => "disabled".to_string(),
                Some((DeepRecallStatus::Completed, DeepRecallStopReason::Completed)) => {
                    "completed".to_string()
                }
                Some((DeepRecallStatus::Capped, reason)) => {
                    format!("capped_{}", deep_stop_reason_name(reason))
                }
                Some((DeepRecallStatus::Partial, reason)) => {
                    format!("partial_{}", deep_stop_reason_name(reason))
                }
                Some((status, reason)) => {
                    debug_assert!(false, "invalid Deep status/reason: {status:?}/{reason:?}");
                    format!("invalid_{}", deep_stop_reason_name(reason))
                }
            }
        } else {
            "completed".to_string()
        },
    })
    .collect()
}

fn deep_stop_reason_name(reason: DeepRecallStopReason) -> &'static str {
    match reason {
        DeepRecallStopReason::Completed => "completed",
        DeepRecallStopReason::WallTime => "wall_time",
        DeepRecallStopReason::ToolIterations => "tool_iterations",
        DeepRecallStopReason::ContextTokens => "context_tokens",
        DeepRecallStopReason::Spend => "spend",
        DeepRecallStopReason::ProviderError => "provider_error",
        DeepRecallStopReason::InvalidOutput => "invalid_output",
    }
}

fn recall_feature_flags(
    request: &RecallRequest,
    vector_enabled: bool,
    deep_enabled: bool,
) -> Vec<String> {
    let mut flags = vec![
        "entity_exact_enabled".to_string(),
        "fts_enabled".to_string(),
        if vector_enabled {
            "vector_enabled".to_string()
        } else {
            "vector_disabled".to_string()
        },
        "temporal_enabled".to_string(),
        "contextual_chunks_enabled".to_string(),
    ];
    if request.edge_expansion_enabled {
        flags.push("edge_expansion_enabled".to_string());
    }
    if request.context_packing_abstention_enabled {
        flags.push("context_packing_abstention_enabled".to_string());
    }
    if request.procedure_recall_enabled {
        flags.push("procedure_recall_enabled".to_string());
    }
    if request.decay_enabled {
        flags.push("decay_enabled".to_string());
    }
    if deep_enabled {
        flags.push("l4_exhaustive_enabled".to_string());
    }
    if request.transaction_as_of.is_some() {
        flags.push("transaction_snapshot_requested".to_string());
    }
    if request.valid_at.is_some() {
        flags.push("valid_time_override_requested".to_string());
    }
    flags
}

fn append_pack_feature_flags(flags: &mut Vec<String>, levers: PackLevers) {
    if let Some(cap) = levers.pack_render_cap {
        flags.push(format!("pack_render_cap:{cap}"));
    }
    if let Some(quota) = levers.session_quota {
        flags.push(format!("pack_session_quota:{quota}"));
    }
    // Promoted default-ON 2026-08-05: the trace flags a DEVIATION from the
    // shipped default, so the notable state is now the disabled control arm,
    // not the (default) merged one.
    if !levers.merge_chunk_blocks {
        flags.push("pack_merge_chunk_blocks_disabled".to_string());
    }
    if levers.submodular_ordering_enabled {
        flags.push("pack_submodular_ordering_enabled".to_string());
    }
}

/// Returns the trace plus the ids of the units this call newly created (in
/// persistence order). A redelivery short-circuits on the idempotency marker
/// and creates nothing, so its created-id list is empty.
pub async fn reflect_recorded<S>(
    store: &S,
    input: ReflectInput,
    embedder: &dyn EmbeddingProvider,
    clock: &dyn Clock,
) -> Result<(ReflectTrace, Vec<UnitId>), CoreError>
where
    S: MemoryStore,
{
    reflect_recorded_inner(
        store,
        input,
        EmbeddingExecution::Borrowed(embedder),
        clock,
        None,
        None,
    )
    .await
}

pub async fn reflect_recorded_claimed<S>(
    store: &S,
    input: ReflectInput,
    embedder: &dyn EmbeddingProvider,
    clock: &dyn Clock,
    context: &ResolvedMemoryContext,
    claim: &ReflectJobRow,
) -> Result<(ReflectTrace, Vec<UnitId>), CoreError>
where
    S: MemoryStore,
{
    reflect_recorded_inner(
        store,
        input,
        EmbeddingExecution::Borrowed(embedder),
        clock,
        Some(context),
        Some(claim),
    )
    .await
}

pub(crate) async fn reflect_recorded_claimed_owned<S>(
    store: &S,
    input: ReflectInput,
    embedder: Arc<dyn EmbeddingProvider>,
    clock: &dyn Clock,
    context: &ResolvedMemoryContext,
    claim: &ReflectJobRow,
) -> Result<(ReflectTrace, Vec<UnitId>), CoreError>
where
    S: MemoryStore,
{
    reflect_recorded_inner(
        store,
        input,
        EmbeddingExecution::Owned(embedder),
        clock,
        Some(context),
        Some(claim),
    )
    .await
}

async fn reflect_recorded_inner<S>(
    store: &S,
    input: ReflectInput,
    embedder: EmbeddingExecution<'_>,
    clock: &dyn Clock,
    resolved_context: Option<&ResolvedMemoryContext>,
    claim: Option<&ReflectJobRow>,
) -> Result<(ReflectTrace, Vec<UnitId>), CoreError>
where
    S: MemoryStore,
{
    let prepared =
        prepare_compiled_write_inner(store, input, embedder, clock, resolved_context).await?;
    match prepared {
        PreparedCompiledWrite::Existing(trace) => Ok((trace, Vec::new())),
        PreparedCompiledWrite::Write {
            context,
            trace,
            created_unit_ids,
            write,
        } => {
            store.persist_compiled_units(&context, claim, write).await?;
            Ok((trace, created_unit_ids))
        }
    }
}

// ponytail: transient local, returned and immediately destructured, never
// collected — boxing the large variant would add a heap alloc on the write path
// for no benefit. Box it if this ever gets stored in a Vec.
#[allow(clippy::large_enum_variant)]
pub(crate) enum PreparedCompiledWrite {
    Existing(ReflectTrace),
    Write {
        context: ResolvedMemoryContext,
        trace: ReflectTrace,
        created_unit_ids: Vec<UnitId>,
        write: CompiledWrite,
    },
}

pub(crate) async fn prepare_compiled_write_owned<S>(
    store: &S,
    input: ReflectInput,
    embedder: Arc<dyn EmbeddingProvider>,
    clock: &dyn Clock,
    resolved_context: Option<&ResolvedMemoryContext>,
) -> Result<PreparedCompiledWrite, CoreError>
where
    S: MemoryStore,
{
    prepare_compiled_write_inner(
        store,
        input,
        EmbeddingExecution::Owned(embedder),
        clock,
        resolved_context,
    )
    .await
}

async fn prepare_compiled_write_inner<S>(
    store: &S,
    input: ReflectInput,
    embedder: EmbeddingExecution<'_>,
    clock: &dyn Clock,
    resolved_context: Option<&ResolvedMemoryContext>,
) -> Result<PreparedCompiledWrite, CoreError>
where
    S: MemoryStore,
{
    // The write compiler dedups/supersedes against the WHOLE open scope — a
    // bounded recall pool would silently miss aged units and let a duplicate
    // subject collide with the open-subject unique index (spec: supersedence is
    // by subject, not recency).
    let resolved;
    let context = match resolved_context {
        Some(context) => context,
        None => {
            resolved = store
                .resolve_memory_context(
                    input.tenant_id,
                    input.data_subject_id,
                    input.actor_id,
                    input.scope_id,
                    input.agent_node_id,
                )
                .await?;
            &resolved
        }
    };
    if context.tenant_id != input.tenant_id
        || context.data_subject_id != input.data_subject_id
        || context.actor_id != input.actor_id
        || context.scope_id != input.scope_id
        || context.agent_node_id != input.agent_node_id
    {
        return Err(StoreError::Conflict(
            "reflect input does not match memory context".to_string(),
        )
        .into());
    }
    if context.subject_generation != input.subject_generation {
        return Err(StoreError::Conflict("subject generation is stale".to_string()).into());
    }
    if let Some(existing) = store
        .fetch_reflect_trace(context, input.job_id, &input.compiler_version)
        .await?
    {
        return Ok(PreparedCompiledWrite::Existing(existing));
    }
    let working = store.fetch_scope_open_units(context).await?;
    prepare_compiled_write_from_snapshot_inner(input, embedder, clock, context, working).await
}

/// Runs compiler admission from one caller-owned complete open-scope snapshot.
/// This function performs no store reads, allowing file sync to prepare every
/// direct-retain write before its short serializable execution transaction and
/// then reject the plan if the in-transaction snapshot digest has drifted.
#[cfg(test)]
pub(crate) async fn prepare_compiled_write_from_snapshot(
    input: ReflectInput,
    embedder: &dyn EmbeddingProvider,
    clock: &dyn Clock,
    context: &ResolvedMemoryContext,
    working: Vec<StoredMemoryUnit>,
) -> Result<PreparedCompiledWrite, CoreError> {
    prepare_compiled_write_from_snapshot_inner(
        input,
        EmbeddingExecution::Borrowed(embedder),
        clock,
        context,
        working,
    )
    .await
}

pub(crate) async fn prepare_compiled_write_from_snapshot_owned(
    input: ReflectInput,
    embedder: Arc<dyn EmbeddingProvider>,
    clock: &dyn Clock,
    context: &ResolvedMemoryContext,
    working: Vec<StoredMemoryUnit>,
) -> Result<PreparedCompiledWrite, CoreError> {
    prepare_compiled_write_from_snapshot_inner(
        input,
        EmbeddingExecution::Owned(embedder),
        clock,
        context,
        working,
    )
    .await
}

async fn prepare_compiled_write_from_snapshot_inner(
    input: ReflectInput,
    embedder: EmbeddingExecution<'_>,
    clock: &dyn Clock,
    context: &ResolvedMemoryContext,
    mut working: Vec<StoredMemoryUnit>,
) -> Result<PreparedCompiledWrite, CoreError> {
    if context.tenant_id != input.tenant_id
        || context.data_subject_id != input.data_subject_id
        || context.actor_id != input.actor_id
        || context.scope_id != input.scope_id
        || context.agent_node_id != input.agent_node_id
    {
        return Err(StoreError::Conflict(
            "reflect input does not match memory context".to_string(),
        )
        .into());
    }
    if context.subject_generation != input.subject_generation {
        return Err(StoreError::Conflict("subject generation is stale".to_string()).into());
    }
    let now = clock.now_rfc3339();
    let originals: HashMap<UnitId, (UnitState, Option<String>, u32)> = working
        .iter()
        .map(|unit| {
            (
                unit.id,
                (
                    unit.state,
                    unit.transaction_to.clone(),
                    unit.reinforcement_count,
                ),
            )
        })
        .collect();
    let mut new_ids: HashSet<UnitId> = HashSet::new();
    let mut new_edges: Vec<StoredMemoryEdge> = Vec::new();
    let mut actions = Vec::new();

    let candidates = input.candidates.clone();
    for candidate in candidates {
        // Explicit direct/structured facts already carry caller-validated
        // identity and may legitimately be terse (for example "Busy."). Keep
        // the historical noise floor only for unkeyed extraction candidates.
        if reject_compiler_candidate_as_noise(&candidate) {
            actions.push(AdmissionAction::Reject);
            continue;
        }

        let explicit_subject = has_explicit_subject(&candidate);
        let fact_key = candidate.fact_key.clone().unwrap_or_else(|| {
            derive_fact_key(
                input.scope_id.as_uuid(),
                candidate.subject.as_deref(),
                candidate.predicate.as_deref(),
                &candidate.body,
            )
        });
        let structured_target_kind = candidate.kind.unwrap_or(MemoryKind::Semantic);
        // Spec 04 §13.1: dispatch on kind FIRST. `supersedes_own_kind` is the
        // whole router decision this admission stage needs — which kind, if
        // any, this candidate's arm may close a generation of. `None` means the
        // arm owns no supersession and the candidate can only append.
        let supersedable_kind = supersedes_own_kind(structured_target_kind);

        let high_trust = matches!(
            candidate.trust_level,
            TrustLevel::TrustedUser | TrustLevel::TrustedSystem
        );

        let targeted_indices = if let Some(target_ids) = &candidate.target_unit_ids {
            let unique_targets = target_ids.iter().copied().collect::<HashSet<_>>();
            if unique_targets.len() != target_ids.len() {
                return Err(CoreError::ProviderInvalid(
                    "structured-state target ids are duplicated".to_string(),
                ));
            }
            // B1 TRUST RULE. Naming another row's id is a directed mutation of
            // a unit this candidate did not author, so it is gated on the
            // actor, not on the candidate's own claim. `Some([])` is only a
            // create precondition and mutates nothing, so it stays open to any
            // caller; a non-empty list is rank-0 trust ONLY, and it fails
            // closed rather than degrading. RW-7 degrades a *kind hint*
            // (a claim about the candidate itself) to a belief; that is the
            // right answer there and the wrong one here, because silently
            // dropping a supersession directive and appending instead is
            // exactly the shape that produced the 20260731_007 exclusion
            // crash. An untrusted extractor must be told it was refused.
            if !target_ids.is_empty() && !high_trust {
                return Err(CoreError::ProviderInvalid(
                    "structured-state target ids require a trusted actor".to_string(),
                ));
            }
            let indices = working
                .iter()
                .enumerate()
                .filter(|(_, unit)| {
                    unique_targets.contains(&unit.id)
                        && unit.scope_id == input.scope_id
                        // B1: the named target need NOT share this candidate's
                        // subject key. That equality was the whole reason
                        // naming an id did not actually bypass key production —
                        // the caller still had to mint a key that matched the
                        // incumbent's. Identity by uuid replaces it. Every
                        // other guard is kept: same scope, still active, still
                        // transaction-open, own-kind only (RW-3), and
                        // bitemporally overlapping the candidate.
                        && unit.state == UnitState::Active
                        && unit.kind == structured_target_kind
                        && unit.transaction_to.is_none()
                        && candidate_targets_unit(&candidate, unit, &now)
                        // Trust dominance: a candidate may not close a unit
                        // more trusted than itself. Vacuous today — the tier
                        // gate above already restricts this path to rank 0, and
                        // `trust_risk_rank` puts TrustedUser and TrustedSystem
                        // in one tier, so this repo has no intra-tier ordering
                        // to appeal to. Kept because it is the invariant the
                        // gate is an approximation of, and it is what stops a
                        // future lower-trust caller from being handed this
                        // capability by a one-line change to the gate.
                        && trust_risk_rank(candidate.trust_level)
                            <= trust_risk_rank(unit.trust_level)
                })
                .map(|(index, _)| index)
                .collect::<Vec<_>>();
            if indices.len() != target_ids.len() {
                return Err(CoreError::ProviderInvalid(format!(
                    "structured-state mutation did not match every exact active target for subject key {fact_key}"
                )));
            }
            // The named targets no longer have to be the incumbents on this
            // candidate's own key, so the create-collision check has to widen
            // with them: any open own-kind row on THIS key that the candidate
            // did not name would still be open after the write, and on the
            // kinds `supersedes_own_kind` owns that is precisely what
            // `memphant_memory_unit_subject_valid_excl` rejects. Fail here,
            // with the key named, rather than in the persist transaction.
            if supersedable_kind.is_some()
                && working.iter().any(|unit| {
                    !unique_targets.contains(&unit.id)
                        && unit.scope_id == input.scope_id
                        && unit.fact_key.as_deref() == Some(fact_key.as_str())
                        && unit.state == UnitState::Active
                        && unit.kind == structured_target_kind
                        && unit.transaction_to.is_none()
                        && candidate_targets_unit(&candidate, unit, &now)
                })
            {
                return Err(CoreError::ProviderInvalid(format!(
                    "structured-state create collided with active subject key {fact_key}"
                )));
            }
            indices
        } else {
            Vec::new()
        };

        // CAPTURE, SAME CHANNEL: a re-capture of a key this channel already
        // holds either REINFORCES the open unit (bodies agree) or SUPERSEDES
        // it within the channel (bodies differ). Handled before the generic
        // dedup below so a same-channel re-capture never fragments into a
        // second open unit; different-channel captures fall through and
        // coexist (see `captures_from_different_sources`).
        if candidate.capture.is_some()
            && let Some(action) = admit_same_channel_capture(
                &mut working,
                &mut new_ids,
                &mut new_edges,
                &input,
                &candidate,
                &fact_key,
                high_trust,
                &now,
            )
        {
            actions.push(action);
            continue;
        }

        let action = if let Some(existing_index) = working.iter().position(|unit| {
            unit.scope_id == input.scope_id
                && unit.fact_key.as_deref() == Some(fact_key.as_str())
                && capture_bodies_agree(unit, &candidate)
                && unit.transaction_to.is_none()
                // CAPTURE independence: two captures from DIFFERENT provenance
                // families (a `mirror` file-write and a `summary` of it) must
                // COEXIST as separate units even when their bodies agree — the
                // Stage A cross-check needs both to observe the `SourceAgreement`
                // that promotes them. Merging them here would erase the second
                // witness before it could be counted. A same-source re-capture
                // (already blocked at the episode dedup) and every ordinary
                // duplicate belief still merge exactly as before.
                && !captures_from_different_sources(unit, &candidate)
                && !matches!(
                    unit.state,
                    UnitState::Deleted
                        | UnitState::Invalidated
                        | UnitState::Superseded
                        | UnitState::Expired
                )
                && candidate_validity_covered_by_unit(&candidate, unit, &now)
        }) {
            if can_promote_belief(&working[existing_index], &candidate) {
                let belief_id = working[existing_index].id;
                let semantic_id = UnitId::new();
                let mut unit = minted_unit(
                    semantic_id,
                    &input,
                    MemoryKind::Semantic,
                    UnitState::Active,
                    fact_key.clone(),
                    &candidate,
                    &now,
                );
                // A promoted belief mints a SEMANTIC unit, so it is subject to
                // the knowledge arm's close-generation rule like any other
                // semantic mint: close whatever generation it replaces on this
                // key, or the promotion opens a second one. It previously
                // skipped this entirely — the one Semantic mint in the compiler
                // that never asked whether the key was already open.
                //
                // The gates are the same two the ordinary branch applies:
                // AUTO-KEYS NEVER SUPERSEDE (`explicit_subject`), and the arm
                // must own the kind it closes (`supersedes_own_kind`, asked of
                // Semantic here because Semantic is what is being minted — NOT
                // of the candidate's own kind, which is how a promotion would
                // otherwise widen across kinds). The incumbent BELIEF is never
                // a target: the scan filters on the semantic kind, so promotion
                // semantics are unchanged and the belief stays open, linked by
                // the `DerivedFrom` edge below.
                if explicit_subject
                    && supersedes_own_kind(MemoryKind::Semantic).is_some()
                    && close_open_generation(
                        &mut working,
                        &mut new_ids,
                        &mut new_edges,
                        &input,
                        &candidate,
                        &fact_key,
                        supersedes_own_kind(MemoryKind::Semantic),
                        &targeted_indices,
                        &mut unit,
                        &now,
                    )?
                {
                    // The promotion still happened; it simply closed what it
                    // replaced. `Supersede` is the honest action label.
                    working.push(unit);
                    new_ids.insert(semantic_id);
                    new_edges.push(StoredMemoryEdge {
                        id: EdgeId::new(),
                        tenant_id: input.tenant_id,
                        scope_id: input.scope_id,
                        src_id: semantic_id,
                        dst_id: belief_id,
                        kind: MemoryEdgeKind::DerivedFrom,
                        transaction_from: Some(now.clone()),
                        transaction_to: None,
                    });
                    actions.push(AdmissionAction::Supersede);
                    continue;
                }
                working.push(unit);
                new_ids.insert(semantic_id);
                new_edges.push(StoredMemoryEdge {
                    id: EdgeId::new(),
                    tenant_id: input.tenant_id,
                    scope_id: input.scope_id,
                    src_id: semantic_id,
                    dst_id: belief_id,
                    kind: MemoryEdgeKind::DerivedFrom,
                    transaction_from: Some(now.clone()),
                    transaction_to: None,
                });
                AdmissionAction::Append
            } else {
                AdmissionAction::Merge
            }
        } else if high_trust {
            if candidate.admission_hint == Some(AdmissionAction::Invalidate) {
                let bounded = candidate.valid_from.is_some() || candidate.valid_to.is_some();
                let mut existing_indices = if candidate.target_unit_ids.is_some() {
                    targeted_indices.clone()
                } else {
                    working
                        .iter()
                        .enumerate()
                        .filter(|(_, unit)| {
                            unit.scope_id == input.scope_id
                                && unit.fact_key.as_deref() == Some(fact_key.as_str())
                                && unit.state == UnitState::Active
                                // RW-3 (own-kind writes only). This was the
                                // literal `MemoryKind::Semantic`, which let a
                                // non-semantic candidate close a semantic
                                // unit's generation; the targeted branch above
                                // already filters on the candidate's own kind,
                                // so the two paths disagreed.
                                && unit.kind == structured_target_kind
                                && candidate_targets_unit(&candidate, unit, &now)
                        })
                        .map(|(index, _)| index)
                        .collect::<Vec<_>>()
                };
                if existing_indices.is_empty() {
                    return Err(CoreError::ProviderInvalid(format!(
                        "invalidation matched no open semantic unit for subject key {fact_key}"
                    )));
                }
                if !bounded && candidate.target_unit_ids.is_none() {
                    existing_indices.truncate(1);
                }
                for existing_index in existing_indices {
                    let old = working[existing_index].clone();
                    working[existing_index].state = if bounded {
                        UnitState::Superseded
                    } else {
                        UnitState::Invalidated
                    };
                    working[existing_index].transaction_to = Some(now.clone());
                    if bounded {
                        let (valid_from, valid_to) = interval_intersection(
                            old.valid_from.as_deref(),
                            old.valid_to.as_deref(),
                            candidate.valid_from.as_deref(),
                            candidate.valid_to.as_deref(),
                        );
                        let payload = CorrectionPayload {
                            value: old.body.clone(),
                            reason: "reflect_invalidation".to_string(),
                            source_ref: input.source_ref.clone(),
                            observed_at: input.observed_at.clone(),
                            valid_from,
                            valid_to,
                        };
                        let (_, remainders) = correction_rectangles(
                            &old,
                            &payload,
                            &input.source_ref,
                            &input.observed_at,
                            input.actor_id,
                            &now,
                        )?;
                        for remainder in remainders {
                            let remainder_id = remainder.id;
                            working.push(remainder);
                            new_ids.insert(remainder_id);
                            new_edges.push(StoredMemoryEdge {
                                id: EdgeId::new(),
                                tenant_id: input.tenant_id,
                                scope_id: input.scope_id,
                                src_id: remainder_id,
                                dst_id: old.id,
                                kind: MemoryEdgeKind::Supersedes,
                                transaction_from: Some(now.clone()),
                                transaction_to: None,
                            });
                        }
                    }
                }
                AdmissionAction::Invalidate
            } else if candidate.admission_hint == Some(AdmissionAction::Quarantine) {
                mint_quarantined_belief(
                    &input,
                    fact_key,
                    &candidate,
                    &now,
                    &mut working,
                    &mut new_ids,
                )
            } else {
                let new_id = UnitId::new();
                let mut action = AdmissionAction::Append;
                let mut unit = minted_unit(
                    new_id,
                    &input,
                    candidate.kind.unwrap_or(MemoryKind::Semantic),
                    UnitState::Active,
                    fact_key.clone(),
                    &candidate,
                    &now,
                );
                // AUTO-KEYS NEVER SUPERSEDE: content-hash subject keys only
                // participate in exact-duplicate dedup above; subject-based
                // supersedence requires an explicit subject/predicate.
                //
                // Second gate, and it is the router's (spec 04 §13.1): only an
                // arm that OWNS *implicit* subject-key close-generation may
                // take this path — `knowledge_arm` for `semantic`,
                // `preference_arm` for `preference`. Every other arm falls
                // through to append, which is what makes "an episode is never
                // superseded" structural rather than incidental.
                //
                // A candidate that names `target_unit_ids` is exempt: that is a
                // caller-directed replacement, already own-kind-checked when
                // `targeted_indices` was resolved (and already fail-closed if
                // any named target does not match). The arm gate governs what
                // the router may close *without being told*, which is where
                // cross-kind widening would otherwise creep in.
                if explicit_subject
                    && (supersedable_kind.is_some() || candidate.target_unit_ids.is_some())
                    && close_open_generation(
                        &mut working,
                        &mut new_ids,
                        &mut new_edges,
                        &input,
                        &candidate,
                        &fact_key,
                        supersedable_kind,
                        &targeted_indices,
                        &mut unit,
                        &now,
                    )?
                {
                    action = AdmissionAction::Supersede;
                }
                working.push(unit);
                new_ids.insert(new_id);
                action
            }
        } else if candidate.admission_hint == Some(AdmissionAction::Quarantine) {
            mint_quarantined_belief(
                &input,
                fact_key,
                &candidate,
                &now,
                &mut working,
                &mut new_ids,
            )
        } else {
            let new_id = UnitId::new();
            // Untrusted callers never mint semantic units — a kind hint is
            // honored only when it does not escalate a claim past candidate
            // tier. Raw episodic/resource projections are evidence, not
            // promoted claims: keep them recallable while preserving their
            // low trust label so high-risk recall policy can still suppress
            // them.
            // RW-7 / §13.2a: a `preference` is actor-gated at write — only the
            // data subject or a trusted user may declare one. An untrusted
            // caller's preference hint degrades to a belief exactly as a
            // semantic hint does, rather than minting a standing constraint.
            let low_trust_kind = candidate
                .kind
                .filter(|kind| !matches!(kind, MemoryKind::Semantic | MemoryKind::Preference))
                .unwrap_or(MemoryKind::Belief);
            let unit = minted_unit(
                new_id,
                &input,
                low_trust_kind,
                low_trust_projection_state(low_trust_kind),
                fact_key,
                &candidate,
                &now,
            );
            working.push(unit);
            new_ids.insert(new_id);
            AdmissionAction::Append
        };
        actions.push(action);
    }

    actions.extend(compose_inferred_beliefs(
        &mut working,
        &mut new_ids,
        &mut new_edges,
        input.tenant_id,
        input.data_subject_id,
        input.scope_id,
        input.agent_node_id,
        input.subject_generation,
        input.actor_id,
        input.episode_id,
        &now,
    ));

    let trace = ReflectTrace {
        tenant_id: input.tenant_id,
        scope_id: input.scope_id,
        job_id: input.job_id,
        episode_id: input.episode_id,
        resource_id: input.resource_id,
        compiler_version: input.compiler_version.clone(),
        cost_units: actions.len().max(1) as u32,
        actions,
        stages: [
            "extract",
            "detect",
            "corroborate",
            "promote",
            "decay",
            "trust",
        ]
        .into_iter()
        .map(|stage| ReflectStageFact {
            stage: stage.to_string(),
            detail: "completed".to_string(),
        })
        .collect(),
    };

    let new_units: Vec<StoredMemoryUnit> = working
        .iter()
        .filter(|unit| new_ids.contains(&unit.id))
        .cloned()
        .collect();
    let citations = mint_compiled_citations(&input, &new_units)?;
    let created_unit_ids: Vec<UnitId> = new_units.iter().map(|unit| unit.id).collect();
    let unit_updates: Vec<UnitUpdate> = working
        .iter()
        .filter(|unit| !new_ids.contains(&unit.id))
        .filter_map(|unit| {
            let (original_state, original_transaction_to, original_reinforcement_count) =
                originals.get(&unit.id)?;
            let reinforced = unit.reinforcement_count > *original_reinforcement_count;
            (unit.state != *original_state
                || unit.transaction_to != *original_transaction_to
                || reinforced)
                .then(|| UnitUpdate {
                    id: unit.id,
                    state: unit.state,
                    transaction_to: unit.transaction_to.clone(),
                    reinforced_at: reinforced.then(|| now.clone()),
                })
        })
        .collect();

    // Embedding write-through: embed the new unit bodies BEFORE the persist
    // transaction (the provider call is network I/O and must not run inside a
    // DB transaction), then hand the rows to `persist_compiled_units` so units,
    // embeddings, and the idempotency marker all commit atomically. A failure
    // here returns before any marker is written, so a retry recomputes cleanly
    // instead of short-circuiting on a marker whose embeddings never landed.
    // Noop providers (dimensions() == 0) skip entirely.
    let (embedding_profile, embeddings) =
        if embedder.provider().dimensions() > 0 && !new_units.is_empty() {
            let profile = embedding_profile_for(embedder.provider());
            let bodies: Vec<String> = new_units.iter().map(|unit| unit.body.clone()).collect();
            let vectors = embedder
                .embed_documents(bodies)
                .await
                .map_err(|error| StoreError::Backend(format!("embedding failed: {error}")))?;
            let rows: Vec<EmbeddingRow> = new_units
                .iter()
                .zip(vectors)
                .filter(|(_, vec)| !vec.is_empty())
                .map(|(unit, vec)| EmbeddingRow {
                    memory_unit_id: unit.id,
                    embedding_profile_id: profile.id,
                    vec,
                })
                .collect();
            if rows.is_empty() {
                (None, Vec::new())
            } else {
                (Some(profile), rows)
            }
        } else {
            (None, Vec::new())
        };

    Ok(PreparedCompiledWrite::Write {
        context: context.clone(),
        trace: trace.clone(),
        created_unit_ids,
        write: CompiledWrite {
            job_id: input.job_id,
            compiler_version: input.compiler_version,
            new_units,
            new_edges,
            citations,
            unit_updates,
            trace,
            embedding_profile,
            embeddings,
        },
    })
}

/// Confidence multiplier a `failure` weak-outcome applies to a served captured
/// unit. Keyed off the CONFIRMATION signal, never retrieval frequency.
const CAPTURE_FAILURE_CONFIDENCE_FACTOR: f32 = 0.5;

/// The telemetry class of one cross-check decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureTransitionKind {
    /// `Candidate -> Active`: a witness family lifted the unit off the inert rung.
    Promote,
    /// `-> Quarantined`: a cross-source collision or a `corrected` weak-outcome.
    Quarantine,
    /// A `failure` weak-outcome cut confidence; recall eligibility unchanged.
    Demote,
    /// A witness family was recorded (e.g. ladder Corroborated -> Durable)
    /// without changing recall eligibility.
    Witness,
}

/// One resolved decision the anti-poisoning cross-check makes about a captured
/// unit, applied by `MemoryStore::apply_capture_transitions`. Carries the exact
/// new `state`, `confidence`, and `payload.capture` marker to persist.
#[derive(Debug, Clone, PartialEq)]
pub struct CaptureTransition {
    pub id: UnitId,
    pub kind: CaptureTransitionKind,
    pub state: UnitState,
    pub confidence: Option<f32>,
    pub capture: memphant_types::CaptureMarker,
}

/// Telemetry summary of an `apply_capture_transitions` batch.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CaptureCrossCheckReport {
    pub promoted: Vec<UnitId>,
    pub quarantined: Vec<UnitId>,
    pub demoted: Vec<UnitId>,
    pub witnessed: Vec<UnitId>,
}

impl CaptureCrossCheckReport {
    pub fn record(&mut self, transition: &CaptureTransition) {
        match transition.kind {
            CaptureTransitionKind::Promote => self.promoted.push(transition.id),
            CaptureTransitionKind::Quarantine => self.quarantined.push(transition.id),
            CaptureTransitionKind::Demote => self.demoted.push(transition.id),
            CaptureTransitionKind::Witness => self.witnessed.push(transition.id),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.promoted.is_empty()
            && self.quarantined.is_empty()
            && self.demoted.is_empty()
            && self.witnessed.is_empty()
    }
}

fn capture_body_key(unit: &StoredMemoryUnit) -> String {
    normalize_component(&unit.body)
}

/// The pure anti-poisoning cross-check. Reads a COMPLETE open-scope snapshot
/// (from the WRITE SEAM `fetch_scope_open_units`, never a bounded recall pool)
/// plus the scope's `review_event`s, and returns the state/confidence/marker
/// transitions to persist. It touches ONLY units carrying a `capture` marker;
/// a plain user/tool write is never a candidate for promotion, demotion, or
/// quarantine (the structural false-positive guard). Deterministic and
/// idempotent: re-running over the resulting snapshot yields no transitions.
///
/// - **Source agreement** (two DIFFERENT capture sources agree on the same
///   subject + body) records a single `SourceAgreement` witness family.
/// - **Cross-source subject collision** (two DIFFERENT sources, same subject,
///   DIVERGENT bodies) quarantines every captured unit in the group.
/// - **Weak self-outcome**: `corrected` quarantines; `failure` cuts confidence;
///   `success` records a `WeakOutcome` witness family.
/// - **Independence / ladder**: the rung derives from the DISTINCT witness
///   families — one family reaches `Corroborated` (active/recallable), two
///   distinct families reach `Durable`. `SourceAgreement` counts once, so an
///   agent's mirror-write plus its own summary-of-that-write cannot self-promote
///   past `Corroborated`.
pub fn compute_capture_crosscheck(
    units: &[StoredMemoryUnit],
    review_events: &[ReviewEvent],
) -> Vec<CaptureTransition> {
    use memphant_types::{CaptureLadder, CaptureWitness, MarkOutcome};
    use std::collections::BTreeMap;

    let eligible: Vec<&StoredMemoryUnit> = units
        .iter()
        .filter(|unit| {
            unit.capture.is_some()
                && unit.transaction_to.is_none()
                && matches!(unit.state, UnitState::Candidate | UnitState::Active)
        })
        .collect();
    if eligible.is_empty() {
        return Vec::new();
    }

    // Subject groups keyed by (fact_key, kind). A unit without a fact_key is a
    // singleton: no cross-source agreement or collision is derivable for it.
    let mut groups: BTreeMap<(String, MemoryKind), Vec<usize>> = BTreeMap::new();
    for (idx, unit) in eligible.iter().enumerate() {
        if let Some(fact_key) = &unit.fact_key {
            groups
                .entry((fact_key.clone(), unit.kind))
                .or_default()
                .push(idx);
        }
    }

    let mut quarantine = vec![false; eligible.len()];
    let mut add_agreement = vec![false; eligible.len()];
    for members in groups.values() {
        if members.len() < 2 {
            continue;
        }
        let source_of = |idx: usize| eligible[idx].capture.as_ref().expect("captured").source;
        let collision = members.iter().any(|&a| {
            members.iter().any(|&b| {
                source_of(a) != source_of(b)
                    && capture_body_key(eligible[a]) != capture_body_key(eligible[b])
            })
        });
        if collision {
            for &member in members {
                quarantine[member] = true;
            }
            continue;
        }
        for &a in members {
            let agrees = members.iter().any(|&b| {
                a != b
                    && source_of(a) != source_of(b)
                    && capture_body_key(eligible[a]) == capture_body_key(eligible[b])
            });
            if agrees {
                add_agreement[a] = true;
            }
        }
    }

    let mut transitions = Vec::new();
    for (idx, unit) in eligible.iter().enumerate() {
        let (mut corrected, mut failure, mut success) = (false, false, false);
        for event in review_events
            .iter()
            .filter(|e| e.used_ids.contains(&unit.id))
        {
            match event.outcome {
                MarkOutcome::Corrected => corrected = true,
                MarkOutcome::Failure => failure = true,
                MarkOutcome::Success => success = true,
                MarkOutcome::Ignored => {}
            }
        }

        let mut marker = unit.capture.clone().expect("captured");
        if add_agreement[idx] {
            marker.record_witness(CaptureWitness::SourceAgreement);
        }
        if success {
            marker.record_witness(CaptureWitness::WeakOutcome);
        }
        // Ladder is monotone up from the DISTINCT witness set; never downgraded.
        marker.ladder = marker.ladder.max(marker.rung_for_witnesses());

        let hard_quarantine = quarantine[idx] || corrected;
        let (new_state, kind) = if hard_quarantine {
            (UnitState::Quarantined, CaptureTransitionKind::Quarantine)
        } else if unit.state == UnitState::Candidate && marker.ladder >= CaptureLadder::Corroborated
        {
            (UnitState::Active, CaptureTransitionKind::Promote)
        } else if failure {
            (unit.state, CaptureTransitionKind::Demote)
        } else {
            (unit.state, CaptureTransitionKind::Witness)
        };

        let new_confidence = if hard_quarantine {
            Some(0.0)
        } else if failure {
            Some(unit.confidence.unwrap_or(1.0) * CAPTURE_FAILURE_CONFIDENCE_FACTOR)
        } else {
            unit.confidence
        };

        let changed = new_state != unit.state
            || new_confidence != unit.confidence
            || Some(&marker) != unit.capture.as_ref();
        if changed {
            transitions.push(CaptureTransition {
                id: unit.id,
                kind,
                state: new_state,
                confidence: new_confidence,
                capture: marker,
            });
        }
    }
    transitions
}

fn low_trust_projection_state(kind: MemoryKind) -> UnitState {
    if matches!(kind, MemoryKind::Episodic | MemoryKind::Resource) {
        UnitState::Active
    } else {
        UnitState::Candidate
    }
}

/// The typed write-router arm a candidate dispatches to (spec 04 §13.1).
///
/// `Knowledge` is the arm name for `MemoryKind::Semantic` (§13.2c) — it is not
/// a kind, and no enum variant exists for it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WriteArm {
    Episodic,
    Knowledge,
    Procedural,
    Belief,
    Resource,
    Preference,
}

/// RW-1 (kind-totality). The dispatch is an exhaustive `match` on `MemoryKind`
/// with **no `_` catch-all**, so minting a kind fails to compile until its arm
/// exists here. A wildcard arm would silently reintroduce the "`kind` enters
/// only as data" defect §13.0 records and is a contract violation, not a style
/// preference.
pub(crate) fn write_router_arm(kind: MemoryKind) -> WriteArm {
    match kind {
        MemoryKind::Episodic => WriteArm::Episodic,
        MemoryKind::Semantic => WriteArm::Knowledge,
        MemoryKind::Procedural => WriteArm::Procedural,
        MemoryKind::Belief => WriteArm::Belief,
        MemoryKind::Resource => WriteArm::Resource,
        MemoryKind::Preference => WriteArm::Preference,
    }
}

/// The kind an arm may close a generation of, or `None` when the arm owns no
/// subject-keyed supersession at all.
///
/// This is the lift of the constant that §13.0 records as the "half-built and
/// hardcoded" defect: the supersession target filter used to be the literal
/// `unit.kind == MemoryKind::Semantic` inside one branch. Routing it through
/// the arm makes RW-3 (own-kind writes only) structural — an arm can only ever
/// name its own kind here, so no dispatch can widen supersession across kinds,
/// and `episodic_arm` provably cannot supersede anything (§13.1: an episode is
/// ground truth and is never superseded).
///
/// **This is also the seam a control-plane hook would attach to, and it is
/// deliberately left open.** RW-2 forbids an LLM call *in the router*, which
/// rules out an ingest-time extractor here — but it does not rule out a
/// mutation-time hook that refines TARGET SELECTION within the kind this
/// function names, on the supersede/purge path only, off the recall hot path.
/// Third-party measurement (arXiv:2606.15903, relayed by the coordinating
/// session and not re-verified here) puts deterministic keying at roughly
/// 63-68% and a mutation-time hook at 92-93%, because identifier variants and
/// intent-aware deletion are not recoverable by any hash or keying scheme. So
/// this function returns the *kind* an arm may close, never the set of units —
/// choosing the units stays a separate step, which is what keeps that hook
/// addable without reshaping the dispatch.
///
/// **Public because the schema depends on it.** The `kind` predicate of
/// `memphant_memory_unit_subject_valid_excl` must be exactly the set of kinds
/// for which this returns `Some(kind)` — the arms that actually maintain
/// at-most-one-open-per-subject-key. That pairing is asserted, not restated, by
/// `exclusion_predicate_matches_the_supersedes_own_kind_set` in
/// `memphant-store-postgres/tests/fact_extraction_subject_key_pg.rs`, so adding
/// a kind here fails loudly until the constraint agrees.
pub fn supersedes_own_kind(kind: MemoryKind) -> Option<MemoryKind> {
    // A-RECENCY CONTROL (measurement only, default OFF). See
    // `a_recency_control_enabled`. Returning `None` for every arm makes the
    // write path pure append: no generation is ever closed, so no
    // `Superseded` state, no `valid_to`, no remainder rectangle and no
    // `Supersedes`/`Contradicts` edge is ever written. The read side then
    // resolves the resulting multi-row subject key by `observed_at` alone.
    if a_recency_control_enabled() {
        return None;
    }
    match write_router_arm(kind) {
        WriteArm::Knowledge => Some(MemoryKind::Semantic),
        WriteArm::Preference => Some(MemoryKind::Preference),
        WriteArm::Episodic | WriteArm::Procedural | WriteArm::Belief | WriteArm::Resource => None,
    }
}

/// **A-recency control. A measurement instrument, not a product feature.**
///
/// `MEMPHANT_A_RECENCY_CONTROL=1` replaces the bitemporal edifice —
/// transaction-time vs valid-time, `tstzrange` exclusion, supersession closing
/// generations, remainder rectangles, `subject_generation` — with the trivial
/// rule *the most recent assertion about a subject wins*. Two call sites, and
/// deliberately no more:
///
/// 1. [`supersedes_own_kind`] returns `None` for every arm, so the write path
///    is pure append and nothing is ever closed, retired or tiled.
/// 2. [`retain_most_recent_per_subject`] collapses the recall pool to one
///    candidate per `fact_key`, chosen by `observed_at` alone.
///
/// The flag is read once and cached, so a process is entirely one arm or the
/// other; an arm cannot change under a running harness. **Default OFF.** With
/// the flag set, `memphant_memory_unit_subject_valid_excl` no longer describes
/// the write path (the router stops maintaining at-most-one-open-per-key), so
/// the control's database must drop that constraint — the constraint is itself
/// part of the machinery under test.
fn a_recency_control_enabled() -> bool {
    static ENABLED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ENABLED.get_or_init(|| {
        std::env::var("MEMPHANT_A_RECENCY_CONTROL")
            .map(|value| matches!(value.trim(), "1" | "true" | "TRUE" | "yes"))
            .unwrap_or(false)
    })
}

/// A-recency read rule: at most one candidate survives per subject key, and it
/// is the one with the greatest `observed_at`. Ties break on body so the arm is
/// deterministic, matching the fusion sort's own tie-break. Candidates without
/// a `fact_key` have no subject to conflict over and are untouched.
///
/// This consults neither `state`, nor `valid_from`/`valid_to`, nor
/// `transaction_to`, nor `subject_generation`.
fn retain_most_recent_per_subject(fused: &mut Vec<CandidateAccumulator>) {
    let mut winner: HashMap<String, usize> = HashMap::new();
    for index in 0..fused.len() {
        let Some(key) = fused[index].unit.fact_key.clone() else {
            continue;
        };
        let replace = match winner.get(&key) {
            None => true,
            Some(&held) => {
                match cmp_rfc3339(
                    &fused[index].unit.observed_at,
                    &fused[held].unit.observed_at,
                ) {
                    std::cmp::Ordering::Greater => true,
                    std::cmp::Ordering::Less => false,
                    std::cmp::Ordering::Equal => fused[index].unit.body > fused[held].unit.body,
                }
            }
        };
        if replace {
            winner.insert(key, index);
        }
    }
    let keep: HashSet<usize> = winner.into_values().collect();
    let mut index = 0;
    fused.retain(|candidate| {
        let position = index;
        index += 1;
        candidate.unit.fact_key.is_none() || keep.contains(&position)
    });
}

fn reject_compiler_candidate_as_noise(candidate: &memphant_types::ReflectCandidate) -> bool {
    if candidate.body.trim().is_empty() {
        return true;
    }
    let raw_evidence = matches!(
        candidate.kind,
        Some(MemoryKind::Episodic | MemoryKind::Resource)
    );
    !raw_evidence && candidate.fact_key.is_none() && candidate.body.split_whitespace().count() < 3
}

#[cfg(test)]
mod low_trust_projection_tests {
    use super::*;

    #[test]
    fn raw_evidence_is_recallable_without_promoting_low_trust_claims() {
        assert_eq!(
            low_trust_projection_state(MemoryKind::Episodic),
            UnitState::Active
        );
        assert_eq!(
            low_trust_projection_state(MemoryKind::Resource),
            UnitState::Active
        );
        for kind in [
            MemoryKind::Semantic,
            MemoryKind::Belief,
            MemoryKind::Procedural,
        ] {
            assert_eq!(low_trust_projection_state(kind), UnitState::Candidate);
        }
    }

    #[test]
    fn short_raw_evidence_bypasses_the_unkeyed_claim_noise_floor() {
        let candidate = |kind, body: &str| memphant_types::ReflectCandidate {
            compact: None,
            capture: None,
            source_kind: "agent".to_string(),
            trust_level: TrustLevel::AgentOutput,
            actor_id: ActorId::from_u128(1),
            subject: None,
            predicate: None,
            fact_key: None,
            kind: Some(kind),
            body: body.to_string(),
            confidence: None,
            churn_class: None,
            admission_hint: None,
            target_unit_ids: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
        };

        assert!(!reject_compiler_candidate_as_noise(&candidate(
            MemoryKind::Episodic,
            "test passed"
        )));
        assert!(!reject_compiler_candidate_as_noise(&candidate(
            MemoryKind::Resource,
            "ok"
        )));
        assert!(reject_compiler_candidate_as_noise(&candidate(
            MemoryKind::Semantic,
            "too short"
        )));
        assert!(reject_compiler_candidate_as_noise(&candidate(
            MemoryKind::Episodic,
            "   "
        )));
    }
}

#[cfg(test)]
mod capture_independence_tests {
    use super::*;
    use memphant_types::{CaptureMarker, CaptureSource, CaptureWitness};

    fn captured_unit(marker: CaptureMarker) -> StoredMemoryUnit {
        StoredMemoryUnit {
            invalidation: None,
            compact: None,
            capture: Some(marker),
            id: UnitId::new(),
            tenant_id: TenantId::from_u128(1),
            data_subject_id: SubjectId::from_u128(1),
            scope_id: ScopeId::from_u128(1),
            agent_node_id: AgentNodeId::from_u128(1),
            subject_generation: 0,
            kind: MemoryKind::Belief,
            state: UnitState::Candidate,
            fact_key: Some("k".to_string()),
            predicate: None,
            body: "Build with cargo build --release.".to_string(),
            confidence: Some(0.5),
            trust_level: TrustLevel::AgentOutput,
            freshness_due_at: None,
            churn_class: None,
            actor_id: Some(ActorId::from_u128(1)),
            source_kind: Some("agent".to_string()),
            source_ref: "capture://summary".to_string(),
            observed_at: "2026-07-01T00:00:00Z".to_string(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: Some("2026-07-01T00:00:00Z".to_string()),
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        }
    }

    fn capture_candidate() -> memphant_types::ReflectCandidate {
        memphant_types::ReflectCandidate {
            compact: None,
            capture: Some(CaptureMarker::captured(CaptureSource::Summary)),
            source_kind: "agent".to_string(),
            trust_level: TrustLevel::AgentOutput,
            actor_id: ActorId::from_u128(1),
            subject: Some("k".to_string()),
            predicate: None,
            fact_key: Some("k".to_string()),
            kind: Some(MemoryKind::Belief),
            body: "Build with cargo build --release.".to_string(),
            confidence: Some(0.5),
            churn_class: None,
            admission_hint: None,
            target_unit_ids: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
        }
    }

    /// A same-channel re-capture is NOT an independent source (it reinforces),
    /// but once the outcome ledger has vouched (`WeakOutcome`) the pair is
    /// independent and promotable. PERTURBATION: a `Survival` witness alone
    /// is not an outcome and does not make it independent.
    #[test]
    fn weak_outcome_witness_makes_a_same_channel_capture_independent() {
        let candidate = capture_candidate();
        let bare = captured_unit(CaptureMarker::captured(CaptureSource::Summary));
        assert!(!is_independent_source(&bare, &candidate));
        assert!(!can_promote_belief(&bare, &candidate));

        let mut vouched_marker = CaptureMarker::captured(CaptureSource::Summary);
        vouched_marker.record_witness(CaptureWitness::WeakOutcome);
        let vouched = captured_unit(vouched_marker);
        assert!(is_independent_source(&vouched, &candidate));
        assert!(can_promote_belief(&vouched, &candidate));

        let mut survived_marker = CaptureMarker::captured(CaptureSource::Summary);
        survived_marker.record_witness(CaptureWitness::Survival);
        let survived = captured_unit(survived_marker);
        assert!(!is_independent_source(&survived, &candidate));
    }
}

fn mint_compiled_citations(
    input: &ReflectInput,
    units: &[StoredMemoryUnit],
) -> Result<Vec<StoredCitation>, StoreError> {
    let Some(source_body) = input.source_body.as_deref() else {
        return Ok(Vec::new());
    };
    let citation =
        |unit: &StoredMemoryUnit, start: usize, end: usize, quote: &str| -> StoredCitation {
            StoredCitation {
                id: Uuid::new_v4(),
                tenant_id: unit.tenant_id,
                data_subject_id: unit.data_subject_id,
                scope_id: unit.scope_id,
                agent_node_id: unit.agent_node_id,
                subject_generation: unit.subject_generation,
                memory_unit_id: unit.id,
                episode_id: unit.source_episode_id,
                resource_id: unit.source_resource_id,
                span: Some(memphant_types::CitationSpan {
                    start: start as u64,
                    end: end as u64,
                }),
                quote_hash: Some(format!("sha256:{:x}", Sha256::digest(quote.as_bytes()))),
            }
        };
    let mut citations = Vec::new();
    for unit in units {
        // Span validation is only meaningful against the body the unit was
        // minted from. Supersession remainders (correction_rectangles) are
        // clones of an OLDER unit carrying chunks from a different episode;
        // validating those against this compile's source_body is a false
        // conflict, and their evidence lineage already lives on the original
        // generation's citations plus the supersedes edge.
        if unit.source_episode_id != input.episode_id
            || unit.source_resource_id != input.resource_id
        {
            continue;
        }
        let spans: Vec<_> = unit
            .contextual_chunks
            .iter()
            .filter_map(|chunk| chunk.source_span.as_deref().map(|span| (chunk, span)))
            .collect();
        if spans.is_empty() || spans.len() != unit.contextual_chunks.len() {
            citations.push(citation(unit, 0, source_body.len(), source_body));
            continue;
        }
        for (chunk, span) in spans {
            let (start, end) = span
                .split_once('-')
                .and_then(|(start, end)| Some((start.parse().ok()?, end.parse().ok()?)))
                .ok_or_else(|| {
                    StoreError::Conflict("contextual chunk span is invalid".to_string())
                })?;
            let quote = source_body.get(start..end).ok_or_else(|| {
                StoreError::Conflict(format!(
                    "contextual chunk span is out of bounds: unit {unit_id} chunk {chunk_id} \
                     span {start}-{end} but source_body is {body_len} bytes",
                    unit_id = unit.id.as_uuid(),
                    chunk_id = chunk.id,
                    body_len = source_body.len(),
                ))
            })?;
            if quote != chunk.body {
                // This invariant is proven to hold at mint time for both
                // chunkers (see chunk_span_invariant_repro.rs). A mismatch here
                // therefore means this compile's `source_body` is NOT the body
                // the chunk was minted from — a re-compile/retry against changed
                // state. Capture the divergence so the input is reproducible
                // instead of being discarded (the diagnostic-dee83e37 gap).
                let common = quote
                    .char_indices()
                    .zip(chunk.body.char_indices())
                    .take_while(|((_, a), (_, b))| a == b)
                    .count();
                return Err(StoreError::Conflict(format!(
                    "contextual chunk span does not match its source body: unit {unit_id} \
                     chunk {chunk_id} span {start}-{end}; source_body {sb_len} bytes, \
                     chunk.body {cb_len} bytes, diverge at char {common}; \
                     source_slice={quote:?} chunk_body={cb:?}",
                    unit_id = unit.id.as_uuid(),
                    chunk_id = chunk.id,
                    sb_len = source_body.len(),
                    cb_len = chunk.body.len(),
                    cb = chunk.body,
                )));
            }
            citations.push(citation(unit, start, end, quote));
        }
    }
    Ok(citations)
}

#[cfg(test)]
mod compiled_citation_tests {
    use super::*;

    #[test]
    fn unchunked_resource_mints_one_full_body_citation() {
        let tenant_id = TenantId::from_u128(81_001);
        let data_subject_id = SubjectId::from_u128(81_002);
        let scope_id = ScopeId::from_u128(81_003);
        let agent_node_id = memphant_types::AgentNodeId::from_u128(81_004);
        let actor_id = ActorId::from_u128(81_005);
        let resource_id = ResourceId::from_u128(81_006);
        let body = "canonical unchunked resource body";
        let input = ReflectInput {
            tenant_id,
            data_subject_id,
            scope_id,
            agent_node_id,
            subject_generation: 0,
            actor_id,
            source_ref: "test:resource".to_string(),
            observed_at: "2026-07-24T00:00:00Z".to_string(),
            source_body: Some(body.to_string()),
            episode_id: None,
            resource_id: Some(resource_id),
            job_id: JobId::from_u128(81_007),
            compiler_version: "test".to_string(),
            candidates: Vec::new(),
        };
        let unit = StoredMemoryUnit {
            capture: None,
            invalidation: None,
            compact: None,
            id: UnitId::from_u128(81_008),
            tenant_id,
            data_subject_id,
            scope_id,
            agent_node_id,
            subject_generation: 0,
            kind: MemoryKind::Resource,
            state: UnitState::Active,
            fact_key: None,
            predicate: None,
            body: body.to_string(),
            confidence: None,
            trust_level: TrustLevel::TrustedSystem,
            churn_class: None,
            freshness_due_at: None,
            actor_id: Some(actor_id),
            source_kind: Some("resource".to_string()),
            source_ref: "test:resource".to_string(),
            observed_at: "2026-07-24T00:00:00Z".to_string(),
            source_episode_id: None,
            source_resource_id: Some(resource_id),
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        };

        let citations = mint_compiled_citations(&input, &[unit]).unwrap();

        assert_eq!(citations.len(), 1);
        assert_eq!(citations[0].resource_id, Some(resource_id));
        assert_eq!(
            citations[0].span,
            Some(memphant_types::CitationSpan {
                start: 0,
                end: body.len() as u64,
            })
        );
        assert_eq!(
            citations[0].quote_hash.as_deref(),
            Some(format!("sha256:{:x}", Sha256::digest(body.as_bytes())).as_str())
        );
    }
}

/// Closes the open generation `unit` replaces on its OWN kind, in place: marks
/// the incumbent superseded, mints the bitemporal remainder rectangles and the
/// `Supersedes`/`Contradicts` edges, and — for an unbounded candidate — narrows
/// `unit`'s own valid interval to the replacement rectangle. Returns whether a
/// generation was closed.
///
/// Extracted so the two Semantic mints in the compiler run the SAME scan. They
/// did not: belief promotion minted a `Semantic` on the mined key without ever
/// asking whether that key was already open, so an incumbent open semantic plus
/// a promotion produced two open generations on one subject key — wrong on the
/// bitemporal model on its own terms, and observed as an exclusion-constraint
/// violation on Postgres. Caller-side gating (`has_explicit_subject`, the arm's
/// `supersedes_own_kind`) stays with the callers; this function assumes it has
/// already been asked.
#[allow(clippy::too_many_arguments)]
fn close_open_generation(
    working: &mut Vec<StoredMemoryUnit>,
    new_ids: &mut HashSet<UnitId>,
    new_edges: &mut Vec<StoredMemoryEdge>,
    input: &ReflectInput,
    candidate: &memphant_types::ReflectCandidate,
    fact_key: &str,
    supersedable_kind: Option<MemoryKind>,
    targeted_indices: &[usize],
    unit: &mut StoredMemoryUnit,
    now: &str,
) -> Result<bool, CoreError> {
    let mut closed = false;
    let bounded = candidate.valid_from.is_some() || candidate.valid_to.is_some();
    let mut existing_indices = if candidate.target_unit_ids.is_some() {
        targeted_indices.to_vec()
    } else {
        working
            .iter()
            .enumerate()
            .filter(|(_, existing)| {
                existing.scope_id == input.scope_id
                    && existing.fact_key.as_deref() == Some(fact_key)
                    && existing.state == UnitState::Active
                    && Some(existing.kind) == supersedable_kind
                    && candidate_targets_unit(candidate, existing, now)
            })
            .map(|(index, _)| index)
            .collect::<Vec<_>>()
    };
    if !bounded && candidate.target_unit_ids.is_none() {
        existing_indices.truncate(1);
    }
    if !existing_indices.is_empty() {
        closed = true;
        for existing_index in existing_indices {
            let old = working[existing_index].clone();
            let old_id = old.id;
            working[existing_index].state = UnitState::Superseded;
            working[existing_index].transaction_to = Some(now.to_string());
            let (valid_from, valid_to) = if bounded {
                interval_intersection(
                    old.valid_from.as_deref(),
                    old.valid_to.as_deref(),
                    candidate.valid_from.as_deref(),
                    candidate.valid_to.as_deref(),
                )
            } else {
                (unit.valid_from.clone(), unit.valid_to.clone())
            };
            let payload = CorrectionPayload {
                value: unit.body.clone(),
                reason: "reflect_supersedence".to_string(),
                source_ref: input.source_ref.clone(),
                observed_at: input.observed_at.clone(),
                valid_from,
                valid_to,
            };
            let (replacement, remainders) = correction_rectangles(
                &old,
                &payload,
                &input.source_ref,
                &input.observed_at,
                input.actor_id,
                now,
            )?;
            if !bounded {
                unit.valid_from = replacement.valid_from;
                unit.valid_to = replacement.valid_to;
            }
            for remainder in remainders {
                let remainder_id = remainder.id;
                working.push(remainder);
                new_ids.insert(remainder_id);
                new_edges.push(StoredMemoryEdge {
                    id: EdgeId::new(),
                    tenant_id: input.tenant_id,
                    scope_id: input.scope_id,
                    src_id: remainder_id,
                    dst_id: old_id,
                    kind: MemoryEdgeKind::Supersedes,
                    transaction_from: Some(now.to_string()),
                    transaction_to: None,
                });
            }
            for (src_id, dst_id, kind) in [
                (old_id, unit.id, MemoryEdgeKind::Contradicts),
                (unit.id, old_id, MemoryEdgeKind::Supersedes),
            ] {
                new_edges.push(StoredMemoryEdge {
                    id: EdgeId::new(),
                    tenant_id: input.tenant_id,
                    scope_id: input.scope_id,
                    src_id,
                    dst_id,
                    kind,
                    transaction_from: Some(now.to_string()),
                    transaction_to: None,
                });
            }
        }
    }
    Ok(closed)
}

fn candidate_targets_unit(
    candidate: &memphant_types::ReflectCandidate,
    unit: &StoredMemoryUnit,
    now: &str,
) -> bool {
    if candidate.valid_from.is_none() && candidate.valid_to.is_none() {
        unit.valid_from
            .as_deref()
            .is_none_or(|from| cmp_rfc3339(from, now) != std::cmp::Ordering::Greater)
            && unit
                .valid_to
                .as_deref()
                .is_none_or(|to| cmp_rfc3339(now, to) == std::cmp::Ordering::Less)
    } else {
        intervals_overlap(
            unit.valid_from.as_deref(),
            unit.valid_to.as_deref(),
            candidate.valid_from.as_deref(),
            candidate.valid_to.as_deref(),
        )
    }
}

fn candidate_validity_covered_by_unit(
    candidate: &memphant_types::ReflectCandidate,
    unit: &StoredMemoryUnit,
    now: &str,
) -> bool {
    if candidate.valid_from.is_none() && candidate.valid_to.is_none() {
        return candidate_targets_unit(candidate, unit, now);
    }
    let start_covered = match candidate.valid_from.as_deref() {
        None => unit.valid_from.is_none(),
        Some(candidate_start) => unit.valid_from.as_deref().is_none_or(|unit_start| {
            cmp_rfc3339(unit_start, candidate_start) != std::cmp::Ordering::Greater
        }),
    };
    let end_covered = match candidate.valid_to.as_deref() {
        None => unit.valid_to.is_none(),
        Some(candidate_end) => unit.valid_to.as_deref().is_none_or(|unit_end| {
            cmp_rfc3339(candidate_end, unit_end) != std::cmp::Ordering::Greater
        }),
    };
    start_covered && end_covered
}

/// True iff an existing open unit and a candidate BOTH carry a capture marker
/// whose provenance `source` families DIFFER (a `Mirror` file-write vs a
/// `Summary`). Such a pair is the raw material the Stage A cross-check pairs into
/// a `SourceAgreement` witness, so admission must let them coexist rather than
/// merge them as duplicate bodies. Non-captures, and same-source re-captures,
/// return false (merge as usual).
fn captures_from_different_sources(
    unit: &StoredMemoryUnit,
    candidate: &memphant_types::ReflectCandidate,
) -> bool {
    match (&unit.capture, &candidate.capture) {
        (Some(existing), Some(incoming)) => existing.source != incoming.source,
        _ => false,
    }
}

/// Body agreement for the generic dedup: byte-equal for every ordinary
/// candidate; whitespace/case-normalised (`capture_body_key`, the cross-check's
/// own agreement key) when the candidate is a capture, whose summarizer output
/// legitimately jitters in whitespace between sessions.
fn capture_bodies_agree(
    unit: &StoredMemoryUnit,
    candidate: &memphant_types::ReflectCandidate,
) -> bool {
    unit.body == candidate.body
        || (candidate.capture.is_some()
            && capture_body_key(unit) == normalize_component(&candidate.body))
}

/// The same-channel capture arm. Finds the OPEN unit (transaction-open,
/// `Candidate`/`Active`/`Validated`) on this scope + key + kind from the SAME
/// capture family as the candidate and:
///
/// * bodies agree (normalised) ⇒ REINFORCE: no mint; the unit's
///   `reinforcement_count`/`last_reinforced_at` advance through `UnitUpdate`.
///   A replay of the SAME source episode (episode dedup re-enqueues the job) is
///   not a new observation and does not count. If the pair is promotable
///   (`can_promote_belief`, e.g. a `WeakOutcome` witness already vouches for
///   the unit) the arm stands aside so the generic dedup promotes it.
/// * bodies differ ⇒ SUPERSEDE within the channel: mint the new unit, close
///   the old one (`Superseded`, transaction-closed — the same close the
///   knowledge arm applies), link `Supersedes` new→old and carry the old
///   `reinforcement_count` forward. Never deletes.
///
/// `None` = no same-channel incumbent (or promotable): the caller's ordinary
/// path applies. Ordinary beliefs never enter here — `supersedes_own_kind`
/// stays `None` for `Belief`, so this is the ONLY belief supersession, and it
/// is capture-marked and same-channel by construction.
#[allow(clippy::too_many_arguments)]
fn admit_same_channel_capture(
    working: &mut Vec<StoredMemoryUnit>,
    new_ids: &mut HashSet<UnitId>,
    new_edges: &mut Vec<StoredMemoryEdge>,
    input: &ReflectInput,
    candidate: &memphant_types::ReflectCandidate,
    fact_key: &str,
    high_trust: bool,
    now: &str,
) -> Option<AdmissionAction> {
    let incoming = candidate.capture.as_ref()?;
    let kind = candidate.kind.unwrap_or(MemoryKind::Belief);
    let existing_index = working.iter().position(|unit| {
        unit.scope_id == input.scope_id
            && unit.fact_key.as_deref() == Some(fact_key)
            && unit.kind == kind
            && unit.transaction_to.is_none()
            && matches!(
                unit.state,
                UnitState::Candidate | UnitState::Active | UnitState::Validated
            )
            && unit
                .capture
                .as_ref()
                .is_some_and(|marker| marker.source == incoming.source)
            && candidate_targets_unit(candidate, unit, now)
    })?;
    if capture_bodies_agree(&working[existing_index], candidate) {
        if can_promote_belief(&working[existing_index], candidate) {
            return None;
        }
        let existing = &mut working[existing_index];
        if existing.source_episode_id != input.episode_id {
            existing.reinforcement_count = existing.reinforcement_count.saturating_add(1);
            existing.last_reinforced_at = Some(now.to_string());
        }
        return Some(AdmissionAction::Merge);
    }
    let old = working[existing_index].clone();
    let new_id = UnitId::new();
    let state = if high_trust {
        UnitState::Active
    } else {
        low_trust_projection_state(kind)
    };
    let mut unit = minted_unit(
        new_id,
        input,
        kind,
        state,
        fact_key.to_string(),
        candidate,
        now,
    );
    unit.reinforcement_count = old.reinforcement_count;
    unit.last_reinforced_at = old.last_reinforced_at.clone();
    working[existing_index].state = UnitState::Superseded;
    working[existing_index].transaction_to = Some(now.to_string());
    working.push(unit);
    new_ids.insert(new_id);
    new_edges.push(StoredMemoryEdge {
        id: EdgeId::new(),
        tenant_id: input.tenant_id,
        scope_id: input.scope_id,
        src_id: new_id,
        dst_id: old.id,
        kind: MemoryEdgeKind::Supersedes,
        transaction_from: Some(now.to_string()),
        transaction_to: None,
    });
    Some(AdmissionAction::Supersede)
}

fn has_explicit_subject(candidate: &memphant_types::ReflectCandidate) -> bool {
    if candidate
        .fact_key
        .as_deref()
        .is_some_and(|key| !key.trim().is_empty())
    {
        return true;
    }
    candidate
        .subject
        .as_deref()
        .map(normalize_component)
        .is_some_and(|subject| !subject.is_empty())
        && candidate
            .predicate
            .as_deref()
            .map(normalize_component)
            .is_some_and(|predicate| !predicate.is_empty())
}

#[allow(clippy::too_many_arguments)]
fn minted_unit(
    id: UnitId,
    input: &ReflectInput,
    kind: MemoryKind,
    state: UnitState,
    fact_key: String,
    candidate: &memphant_types::ReflectCandidate,
    now: &str,
) -> StoredMemoryUnit {
    let freshness_due_at = (state != UnitState::Quarantined
        && candidate.churn_class.as_deref() == Some("volatile"))
    .then(|| now.to_string());
    StoredMemoryUnit {
        invalidation: None,
        compact: candidate.compact.clone(),
        capture: candidate.capture.clone(),
        id,
        tenant_id: input.tenant_id,
        data_subject_id: input.data_subject_id,
        scope_id: input.scope_id,
        agent_node_id: input.agent_node_id,
        subject_generation: input.subject_generation,
        kind,
        state,
        fact_key: Some(fact_key),
        predicate: candidate.predicate.clone(),
        body: candidate.body.clone(),
        confidence: candidate.confidence,
        trust_level: candidate.trust_level,
        freshness_due_at,
        churn_class: candidate.churn_class.clone(),
        actor_id: Some(candidate.actor_id),
        source_kind: Some(candidate.source_kind.clone()),
        source_ref: input.source_ref.clone(),
        observed_at: input.observed_at.clone(),
        source_episode_id: input.episode_id,
        source_resource_id: input.resource_id,
        deletion_generation: None,
        contextual_chunks: candidate.contextual_chunks.clone(),
        valid_from: candidate.valid_from.clone(),
        valid_to: candidate.valid_to.clone(),
        transaction_from: Some(now.to_string()),
        transaction_to: None,
        difficulty: None,
        stability_days: None,
        last_reinforced_at: None,
        reinforcement_count: 0,
    }
}

/// Subject-key derivation: an explicit subject/predicate pair yields the
/// stable `{scope_id}:{subject}:{predicate}` key that participates in
/// supersedence; absent either, a content-hash key
/// `{scope_id}:auto:{sha256(body)[..16]}` is derived so distinct content never
/// collides and identical content dedups. Auto keys never supersede.
pub fn derive_fact_key(
    scope_id: Uuid,
    subject: Option<&str>,
    predicate: Option<&str>,
    body: &str,
) -> String {
    let subject = subject
        .map(normalize_component)
        .filter(|value| !value.is_empty());
    let predicate = predicate
        .map(normalize_component)
        .filter(|value| !value.is_empty());
    match (subject, predicate) {
        (Some(subject), Some(predicate)) => {
            format!("{scope_id}:{}:{predicate}", subject.replace(' ', "_"))
        }
        _ => format!(
            "{scope_id}:auto:{}",
            &sha256_hex(&normalize_component(body))[..16]
        ),
    }
}

pub(crate) fn sha256_hex(value: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    let digest = hasher.finalize();
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn is_independent_source(
    existing: &StoredMemoryUnit,
    candidate: &memphant_types::ReflectCandidate,
) -> bool {
    // Under strict context binding a unit is always stamped with its writing
    // context's `actor_id` (see `owned_unit` in `persist_compiled_units`), and a
    // reflect candidate is written by that same bound actor — so within a scope
    // the actor never varies and independence is carried entirely by the source
    // channel. A different `source_kind` corroborating the same fact is what
    // promotes a belief; a repeat from the same channel only merges.
    //
    // Captures all arrive as `source_kind = "agent"`, so for them independence
    // is carried by the capture channel instead: two capture FAMILIES
    // (`payload.capture.source`) never meet here — admission keeps them as
    // separate units (`captures_from_different_sources`) and the cross-check
    // pairs them into a `SourceAgreement` witness — so the only in-arm
    // independence a capture can show is a `WeakOutcome` witness already on
    // the unit: the outcome ledger, not the agent, vouched for it. A
    // same-family re-capture without one is still one channel and only
    // reinforces.
    existing.source_kind.as_deref() != Some(candidate.source_kind.as_str())
        || existing.capture.as_ref().is_some_and(|marker| {
            marker
                .witnesses
                .contains(&memphant_types::CaptureWitness::WeakOutcome)
        })
}

fn can_promote_belief(
    existing: &StoredMemoryUnit,
    candidate: &memphant_types::ReflectCandidate,
) -> bool {
    if existing.kind != MemoryKind::Belief || candidate.source_kind == "composition" {
        return false;
    }
    matches!(
        candidate.trust_level,
        TrustLevel::TrustedUser | TrustLevel::TrustedSystem
    ) || is_independent_source(existing, candidate)
}

#[allow(clippy::too_many_arguments)]
fn compose_inferred_beliefs(
    working: &mut Vec<StoredMemoryUnit>,
    new_ids: &mut HashSet<UnitId>,
    composed_edges: &mut Vec<StoredMemoryEdge>,
    tenant_id: TenantId,
    data_subject_id: SubjectId,
    scope_id: ScopeId,
    agent_node_id: AgentNodeId,
    subject_generation: u64,
    actor_id: ActorId,
    episode_id: Option<EpisodeId>,
    now: &str,
) -> Vec<AdmissionAction> {
    let units: &[StoredMemoryUnit] = working;

    let existing_composed_bodies = units
        .iter()
        .filter(|unit| unit.scope_id == scope_id && derived_by_for_unit(unit) == "composition")
        .filter(|unit| {
            !matches!(
                unit.state,
                UnitState::Deleted | UnitState::Invalidated | UnitState::Expired
            ) && unit.transaction_to.is_none()
        })
        .map(|unit| normalize_component(&unit.body))
        .collect::<Vec<_>>();
    let mut grouped: BTreeMap<String, Vec<(String, StoredMemoryUnit)>> = BTreeMap::new();
    for unit in units
        .iter()
        .filter(|unit| unit.scope_id == scope_id && composable_source_unit(unit))
    {
        let Some(preference) = parse_preference_observation(&unit.body) else {
            continue;
        };
        grouped
            .entry(preference.object)
            .or_default()
            .push((preference.descriptor, unit.clone()));
    }

    let mut new_units = Vec::new();
    let mut new_edges = Vec::new();
    let mut actions = Vec::new();
    for (object, mut observations) in grouped {
        observations.sort_by(|left, right| {
            left.0
                .cmp(&right.0)
                .then_with(|| left.1.body.cmp(&right.1.body))
        });
        observations.dedup_by(|left, right| left.0 == right.0);
        if observations.len() < 2 {
            continue;
        }
        let body = format!(
            "The user prefers {} and {} {}.",
            observations[0].0, observations[1].0, object
        );
        if existing_composed_bodies.contains(&normalize_component(&body))
            || new_units.iter().any(|unit: &StoredMemoryUnit| {
                normalize_component(&unit.body) == normalize_component(&body)
            })
        {
            continue;
        }
        let fact_key = derive_fact_key(
            scope_id.as_uuid(),
            Some("user preference"),
            Some(&object),
            &body,
        );
        let composed_id = UnitId::new();
        new_units.push(StoredMemoryUnit {
            invalidation: None,
            compact: None,
            capture: None,
            id: composed_id,
            tenant_id,
            data_subject_id,
            scope_id,
            agent_node_id,
            subject_generation,
            kind: MemoryKind::Belief,
            state: UnitState::Candidate,
            fact_key: Some(fact_key),
            predicate: None,
            body,
            confidence: None,
            trust_level: TrustLevel::AgentOutput,
            freshness_due_at: None,
            churn_class: None,
            actor_id: Some(actor_id),
            source_kind: Some("composition".to_string()),
            source_ref: observations[0].1.source_ref.clone(),
            observed_at: observations[0].1.observed_at.clone(),
            source_episode_id: episode_id,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: Some(now.to_string()),
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        });
        for (_, source) in observations.iter().take(2) {
            new_edges.push(StoredMemoryEdge {
                id: EdgeId::new(),
                tenant_id,
                scope_id,
                src_id: composed_id,
                dst_id: source.id,
                kind: MemoryEdgeKind::DerivedFrom,
                transaction_from: Some(now.to_string()),
                transaction_to: None,
            });
        }
        actions.push(AdmissionAction::Append);
    }

    for unit in new_units {
        new_ids.insert(unit.id);
        working.push(unit);
    }
    composed_edges.extend(new_edges);
    actions
}

fn composable_source_unit(unit: &StoredMemoryUnit) -> bool {
    matches!(unit.kind, MemoryKind::Semantic | MemoryKind::Belief)
        && matches!(unit.state, UnitState::Active | UnitState::Candidate)
        && unit.transaction_to.is_none()
        && unit.deletion_generation.is_none()
        && derived_by_for_unit(unit) != "composition"
        && matches!(
            unit.trust_level,
            TrustLevel::TrustedUser | TrustLevel::TrustedSystem | TrustLevel::VerifiedTool
        )
}

struct PreferenceObservation {
    descriptor: String,
    object: String,
}

fn parse_preference_observation(body: &str) -> Option<PreferenceObservation> {
    let normalized = normalize_component(body).trim_end_matches('.').to_string();
    let preference = normalized
        .strip_prefix("the user prefers ")
        .or_else(|| normalized.strip_prefix("user prefers "))?;
    if contains_composition_risk(preference) {
        return None;
    }
    let tokens = preference
        .split_whitespace()
        .filter(|token| !token.is_empty())
        .collect::<Vec<_>>();
    if tokens.len() < 3 {
        return None;
    }
    let object = tokens[tokens.len() - 2..].join(" ");
    let descriptor = tokens[..tokens.len() - 2].join(" ");
    if descriptor.is_empty() || object.is_empty() {
        return None;
    }
    Some(PreferenceObservation { descriptor, object })
}

fn contains_composition_risk(value: &str) -> bool {
    [
        "always agree",
        "agree with me",
        "tell me i am right",
        "ignore evidence",
        "ignore policy",
        "bypass",
        "admin",
        "password",
        "secret",
        "token",
        "delete production",
        "force push",
        "force-push",
        "exfiltrat",
        "curl ",
        "rm -rf",
    ]
    .iter()
    .any(|phrase| value.contains(phrase))
}

fn derived_by_for_unit(unit: &StoredMemoryUnit) -> &'static str {
    if unit.source_kind.as_deref() == Some("composition") {
        "composition"
    } else if unit.source_kind.as_deref() == Some("quantity_rollup") {
        "quantity_rollup"
    } else if unit.source_kind.as_deref() == Some("artifact_bundle") {
        "artifact_bundle"
    } else {
        "extraction"
    }
}

fn delete_composed_dependents(
    state: &mut InMemoryState,
    context: &ResolvedMemoryContext,
    source_ids: &[UnitId],
    deletion_generation: u64,
    now: &str,
) -> Vec<UnitId> {
    let dependent_ids = composed_dependent_ids(state, context.tenant_id, source_ids);
    let mut deleted = Vec::new();
    if let Some(units) = state.memory_units.get_mut(&context.tenant_id) {
        for unit in units.iter_mut().filter(|unit| {
            dependent_ids.contains(&unit.id)
                && unit.data_subject_id == context.data_subject_id
                && unit.subject_generation == context.subject_generation
                && unit.scope_id == context.scope_id
                && unit.agent_node_id == context.agent_node_id
                && unit.actor_id == Some(context.actor_id)
        }) {
            if unit.state != UnitState::Deleted {
                unit.state = UnitState::Deleted;
                unit.deletion_generation = Some(deletion_generation);
                unit.transaction_to = Some(now.to_string());
                deleted.push(unit.id);
            }
        }
    }
    deleted
}

fn composed_dependent_ids(
    state: &InMemoryState,
    tenant_id: TenantId,
    source_ids: &[UnitId],
) -> Vec<UnitId> {
    let Some(edges) = state.memory_edges.get(&tenant_id) else {
        return Vec::new();
    };
    let Some(units) = state.memory_units.get(&tenant_id) else {
        return Vec::new();
    };
    edges
        .iter()
        .filter(|edge| {
            edge.kind == MemoryEdgeKind::DerivedFrom && source_ids.contains(&edge.dst_id)
        })
        .filter_map(|edge| {
            units
                .iter()
                .find(|unit| unit.id == edge.src_id && derived_by_for_unit(unit) == "composition")
                .map(|unit| unit.id)
        })
        .fold(Vec::new(), |mut ids, id| {
            if !ids.contains(&id) {
                ids.push(id);
            }
            ids
        })
}

#[cfg(test)]
mod in_memory_mutation_retention_tests {
    use super::*;

    async fn bound_context(store: &InMemoryStore) -> ResolvedMemoryContext {
        let tenant = TenantId::new();
        let binding = store
            .resolve_context_binding(
                tenant,
                "erasure-retention".to_string(),
                ContextBindingRequest {
                    subject: memphant_types::ContextBindingEntityRef {
                        external_ref: "subject:retention".to_string(),
                        kind: "user".to_string(),
                    },
                    actor: memphant_types::ContextBindingEntityRef {
                        external_ref: "actor:retention".to_string(),
                        kind: "user".to_string(),
                    },
                    scope: memphant_types::ContextBindingScopeRef {
                        external_ref: "scope:retention".to_string(),
                        kind: "memory".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: memphant_types::ContextBindingAgentRef {
                        external_ref: "agent:retention".to_string(),
                        parent_external_ref: None,
                    },
                    access_policies: Vec::new(),
                },
            )
            .await
            .unwrap();
        store
            .resolve_memory_context(
                tenant,
                binding.subject_id,
                binding.actor_id,
                binding.scope_id,
                binding.agent_node_id,
            )
            .await
            .unwrap()
    }

    #[tokio::test]
    async fn expired_erasure_receipt_and_unused_lock_are_physically_purged() {
        let store = InMemoryStore::default();
        let context = bound_context(&store).await;
        let claim = MutationClaim::new(
            &context,
            MutationVerb::EraseSubject,
            "erase-retention",
            [7; 32],
        )
        .unwrap();
        let mut erase = store.begin_at(&context, &FixedClock("2026-07-15T00:00:00Z"));
        store
            .stage_mutation_claim(&mut erase, claim.clone())
            .await
            .unwrap();
        store.stage_subject_erasure(&mut erase).await.unwrap();
        store.commit(erase).await.unwrap();

        assert_eq!(store.inner.lock().unwrap().mutation_ledger.len(), 1);
        assert!(store.mutation_locks.lock().unwrap().is_empty());

        let mut expired = store.begin_at(&context, &FixedClock("2026-07-16T00:00:00Z"));
        assert!(matches!(
            store.stage_mutation_claim(&mut expired, claim).await,
            Err(StoreError::SubjectErased)
        ));
        assert!(store.inner.lock().unwrap().mutation_ledger.is_empty());
        assert!(store.mutation_locks.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn abandoned_transaction_retains_only_a_dead_digest_until_the_next_claim() {
        let store = InMemoryStore::default();
        let context = bound_context(&store).await;
        let sensitive_key = "user@example.test/private-source";
        let digest: [u8; 32] = Sha256::digest(sensitive_key.as_bytes()).into();
        let mut abandoned = store.begin_at(&context, &FixedClock("2026-07-15T00:00:00Z"));
        store
            .stage_mutation_claim(
                &mut abandoned,
                MutationClaim::new(&context, MutationVerb::Retain, sensitive_key, [1; 32]).unwrap(),
            )
            .await
            .unwrap();
        drop(abandoned);

        let old_lock_key = (context.tenant_id, MutationVerb::Retain, digest);
        // Scoped so the guard is out of scope before the await below (clippy's
        // await_holding_lock keys on the binding's scope, not an explicit drop).
        {
            let locks = store.mutation_locks.lock().unwrap();
            assert!(locks.get(&old_lock_key).unwrap().upgrade().is_none());
        }

        let mut next = store.begin_at(&context, &FixedClock("2026-07-15T00:00:01Z"));
        store
            .stage_mutation_claim(
                &mut next,
                MutationClaim::new(&context, MutationVerb::Retain, "next", [2; 32]).unwrap(),
            )
            .await
            .unwrap();
        assert!(
            !store
                .mutation_locks
                .lock()
                .unwrap()
                .contains_key(&old_lock_key)
        );
        drop(next);
        store.prune_mutation_locks();
        assert!(store.mutation_locks.lock().unwrap().is_empty());
    }

    async fn seed_unit(store: &InMemoryStore, context: &ResolvedMemoryContext) -> UnitId {
        let mut tx = store.begin(context).await.unwrap();
        let id = store
            .stage_memory_unit(
                &mut tx,
                NewMemoryUnit {
                    capture: None,
                    tenant_id: context.tenant_id,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    kind: MemoryKind::Semantic,
                    state: UnitState::Active,
                    fact_key: Some("timezone:value".to_string()),
                    predicate: None,
                    body: "Timezone is UTC.".to_string(),
                    confidence: Some(1.0),
                    trust_level: TrustLevel::TrustedUser,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(context.actor_id),
                    source_kind: Some("user".to_string()),
                    source_ref: "test:seed".to_string(),
                    observed_at: "2026-07-15T00:00:00Z".to_string(),
                    source_episode_id: None,
                    source_resource_id: None,
                    deletion_generation: None,
                    contextual_chunks: Vec::new(),
                    valid_from: None,
                    valid_to: None,
                    transaction_from: None,
                    transaction_to: None,
                },
            )
            .await
            .unwrap();
        store.commit(tx).await.unwrap();
        id
    }

    #[derive(Clone, Copy)]
    enum ActorBridgeForgetSource {
        MemoryUnit,
        Episode,
        Resource,
    }

    fn actor_bridge_unit(
        context: &ResolvedMemoryContext,
        label: &str,
        target: Option<ForgetTarget>,
    ) -> NewMemoryUnit {
        let mut unit = NewMemoryUnit {
            capture: None,
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: Some(format!("actor-bridge:{label}")),
            predicate: Some("states".to_string()),
            body: format!("Actor bridge fixture {label}."),
            confidence: Some(1.0),
            trust_level: TrustLevel::TrustedUser,
            churn_class: None,
            freshness_due_at: None,
            actor_id: Some(context.actor_id),
            source_kind: Some("user".to_string()),
            source_ref: format!("test:actor-bridge:{label}"),
            observed_at: "2026-07-15T00:00:00Z".to_string(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
        };
        match target {
            Some(ForgetTarget::Episode(id)) => unit.source_episode_id = Some(id),
            Some(ForgetTarget::Resource(id)) => unit.source_resource_id = Some(id),
            Some(ForgetTarget::MemoryUnit(_)) | None => {}
        }
        unit
    }

    async fn assert_forget_stops_at_foreign_actor_bridge(source: ActorBridgeForgetSource) {
        let store = InMemoryStore::default();
        let context = bound_context(&store).await;
        let label = match source {
            ActorBridgeForgetSource::MemoryUnit => "memory-unit",
            ActorBridgeForgetSource::Episode => "episode",
            ActorBridgeForgetSource::Resource => "resource",
        };
        let source_target = match source {
            ActorBridgeForgetSource::MemoryUnit => None,
            ActorBridgeForgetSource::Episode => {
                let mut tx = store.begin(&context).await.unwrap();
                let outcome = store
                    .stage_episode(
                        &mut tx,
                        NewEpisode {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            source_kind: "user".to_string(),
                            source_ref: "test:actor-bridge:episode".to_string(),
                            observed_at: "2026-07-15T00:00:00Z".to_string(),
                            source_trust: TrustLevel::TrustedUser,
                            dedup_key: "test:actor-bridge:episode".to_string(),
                            body: "Episode actor bridge fixture.".to_string(),
                        },
                    )
                    .await
                    .unwrap();
                store.commit(tx).await.unwrap();
                Some(ForgetTarget::Episode(outcome.episode_id))
            }
            ActorBridgeForgetSource::Resource => {
                let mut tx = store.begin(&context).await.unwrap();
                let id = store
                    .stage_resource(
                        &mut tx,
                        NewResource {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            uri: "memphant://actor-bridge/resource".to_string(),
                            source_ref: "test:actor-bridge:resource".to_string(),
                            observed_at: "2026-07-15T00:00:00Z".to_string(),
                            kind: memphant_types::ResourceKind::Document,
                            content_hash: "sha256:actor-bridge-resource".to_string(),
                            mime_type: "text/plain".to_string(),
                            revision: None,
                            body: Some("Resource actor bridge fixture.".to_string()),
                            source_trust: TrustLevel::TrustedUser,
                            acl: ResourceAcl::default(),
                        },
                    )
                    .await
                    .unwrap();
                store.commit(tx).await.unwrap();
                Some(ForgetTarget::Resource(id))
            }
        };

        let mut tx = store.begin(&context).await.unwrap();
        let root_id = store
            .stage_memory_unit(
                &mut tx,
                actor_bridge_unit(&context, &format!("{label}-root"), source_target),
            )
            .await
            .unwrap();
        let foreign_bridge_id = store
            .stage_memory_unit(
                &mut tx,
                actor_bridge_unit(&context, &format!("{label}-foreign"), None),
            )
            .await
            .unwrap();
        let own_grandchild_id = store
            .stage_memory_unit(
                &mut tx,
                actor_bridge_unit(&context, &format!("{label}-grandchild"), None),
            )
            .await
            .unwrap();
        for (src_id, dst_id) in [
            (foreign_bridge_id, root_id),
            (own_grandchild_id, foreign_bridge_id),
        ] {
            store
                .stage_memory_edge(
                    &mut tx,
                    NewMemoryEdge {
                        tenant_id: context.tenant_id,
                        scope_id: context.scope_id,
                        src_id,
                        dst_id,
                        kind: MemoryEdgeKind::Supersedes,
                    },
                )
                .await
                .unwrap();
        }
        store.commit(tx).await.unwrap();

        store
            .inner
            .lock()
            .unwrap()
            .memory_units
            .get_mut(&context.tenant_id)
            .unwrap()
            .iter_mut()
            .find(|unit| unit.id == foreign_bridge_id)
            .unwrap()
            .actor_id = Some(ActorId::new());

        let outcome = if matches!(source, ActorBridgeForgetSource::MemoryUnit) {
            let mut state = store.inner.lock().unwrap();
            let InMemoryState {
                memory_units,
                memory_edges,
                ..
            } = &mut *state;
            let invalidated_units = apply_unit_forget_transition(
                memory_units.get_mut(&context.tenant_id).unwrap(),
                memory_edges.get(&context.tenant_id).unwrap(),
                &context,
                root_id,
                "2026-07-15T00:00:00Z",
                Some(1),
            )
            .unwrap();
            ForgetOutcome {
                deletion_generation: 1,
                invalidated_units,
            }
        } else {
            store
                .apply_forget(
                    &context,
                    ForgetWrite {
                        target: source_target.unwrap_or(ForgetTarget::MemoryUnit(root_id)),
                        now: "2026-07-15T00:00:00Z".to_string(),
                    },
                )
                .await
                .unwrap()
        };
        assert!(outcome.invalidated_units.contains(&root_id));
        assert!(
            !outcome.invalidated_units.contains(&own_grandchild_id),
            "{label} forget crossed a foreign actor bridge"
        );
        assert_eq!(
            store
                .memory_units(context.tenant_id)
                .into_iter()
                .find(|unit| unit.id == own_grandchild_id)
                .unwrap()
                .state,
            UnitState::Active
        );
    }

    #[tokio::test]
    async fn memory_unit_forget_stops_at_foreign_actor_bridge() {
        assert_forget_stops_at_foreign_actor_bridge(ActorBridgeForgetSource::MemoryUnit).await;
    }

    #[tokio::test]
    async fn episode_forget_stops_at_foreign_actor_bridge() {
        assert_forget_stops_at_foreign_actor_bridge(ActorBridgeForgetSource::Episode).await;
    }

    #[tokio::test]
    async fn resource_forget_stops_at_foreign_actor_bridge() {
        assert_forget_stops_at_foreign_actor_bridge(ActorBridgeForgetSource::Resource).await;
    }

    fn correction(id: UnitId, value: &str) -> CorrectionWrite {
        CorrectionWrite {
            compact_refresh: None,
            selector: CorrectSelector { memory_unit_id: id },
            source_ref: "test:correction".to_string(),
            observed_at: "2026-07-15T00:00:00Z".to_string(),
            correction: CorrectionPayload {
                value: value.to_string(),
                reason: "test".to_string(),
                source_ref: "test:correction".to_string(),
                observed_at: "2026-07-15T00:00:00Z".to_string(),
                valid_from: None,
                valid_to: None,
            },
            now: "2026-07-15T00:00:00Z".to_string(),
            embedding: None,
            unit_ids: Default::default(),
        }
    }

    #[tokio::test]
    async fn staged_corrections_roll_back_and_unrelated_contexts_commit_independently() {
        let store = InMemoryStore::default();
        let first = bound_context(&store).await;
        let second = bound_context(&store).await;
        let first_id = seed_unit(&store, &first).await;
        let second_id = seed_unit(&store, &second).await;
        let mut rolled_back = store.begin(&first).await.unwrap();
        store
            .stage_correction(&mut rolled_back, correction(first_id, "Timezone is PST."))
            .await
            .unwrap();
        drop(rolled_back);
        assert!(
            store
                .memory_units(first.tenant_id)
                .iter()
                .any(|unit| unit.id == first_id && unit.transaction_to.is_none())
        );
        let mut first_tx = store.begin(&first).await.unwrap();
        let mut second_tx = store.begin(&second).await.unwrap();
        store
            .stage_correction(&mut first_tx, correction(first_id, "Timezone is PST."))
            .await
            .unwrap();
        store
            .stage_correction(&mut second_tx, correction(second_id, "Timezone is CET."))
            .await
            .unwrap();
        store.commit(first_tx).await.unwrap();
        store.commit(second_tx).await.unwrap();

        let mut mixed = store.begin(&first).await.unwrap();
        store
            .stage_mutation_claim(
                &mut mixed,
                MutationClaim::new(
                    &first,
                    MutationVerb::EraseSubject,
                    "erase-after-write",
                    [9; 32],
                )
                .unwrap(),
            )
            .await
            .unwrap();
        store
            .stage_correction(
                &mut mixed,
                correction(
                    store
                        .memory_units(first.tenant_id)
                        .iter()
                        .find(|unit| unit.transaction_to.is_none())
                        .unwrap()
                        .id,
                    "Timezone is MST.",
                ),
            )
            .await
            .unwrap();
        assert!(matches!(
            store.stage_subject_erasure(&mut mixed).await,
            Err(StoreError::Conflict(_))
        ));
    }
}

#[cfg(test)]
mod sub_split_for_rerank_tests {
    use super::{RERANK_CHARS_PER_TOKEN, sub_split_for_rerank};

    #[test]
    fn short_text_is_one_window() {
        assert_eq!(
            sub_split_for_rerank("hello world", 100),
            vec!["hello world"]
        );
    }

    #[test]
    fn empty_or_whitespace_yields_nothing() {
        assert!(sub_split_for_rerank("   \n\t ", 10).is_empty());
        assert!(sub_split_for_rerank("", 10).is_empty());
    }

    #[test]
    fn unbounded_budget_returns_whole_text() {
        let long = "a ".repeat(1000);
        assert_eq!(sub_split_for_rerank(&long, usize::MAX).len(), 1);
    }

    #[test]
    fn windows_never_exceed_the_budget_and_cover_every_word() {
        let text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet";
        let windows = sub_split_for_rerank(text, 12);
        for w in &windows {
            assert!(w.chars().count() <= 12, "window over budget: {w:?}");
        }
        // Every original word survives across the windows (no drops, no merge loss).
        let rejoined: Vec<&str> = windows.iter().flat_map(|w| w.split(' ')).collect();
        assert_eq!(rejoined, text.split(' ').collect::<Vec<_>>());
        assert!(windows.len() > 1, "long text must split");
    }

    #[test]
    fn a_single_word_longer_than_budget_is_hard_split() {
        let word = "x".repeat(25);
        let windows = sub_split_for_rerank(&word, 10);
        assert_eq!(windows.len(), 3); // 10 + 10 + 5
        for w in &windows {
            assert!(w.chars().count() <= 10);
        }
        assert_eq!(windows.concat(), word);
    }

    #[test]
    fn multibyte_hard_split_stays_on_char_boundaries() {
        // 12 two-byte chars, budget 5 → windows of 5,5,2 chars (never a byte panic).
        let word = "é".repeat(12);
        let windows = sub_split_for_rerank(&word, 5);
        assert_eq!(
            windows
                .iter()
                .map(|w| w.chars().count())
                .collect::<Vec<_>>(),
            vec![5, 5, 2]
        );
    }

    #[test]
    fn chars_per_token_ratio_is_conservative() {
        assert!((1..=4).contains(&RERANK_CHARS_PER_TOKEN));
    }
}

#[cfg(test)]
mod temporal_grounding_tests {
    use super::*;

    fn temporal_test_unit(id: u128, body: &str, valid_from: &str) -> StoredMemoryUnit {
        StoredMemoryUnit {
            capture: None,
            invalidation: None,
            compact: None,
            id: UnitId::from_u128(id),
            tenant_id: TenantId::from_u128(1),
            data_subject_id: SubjectId::from_u128(1),
            scope_id: ScopeId::from_u128(1),
            agent_node_id: memphant_types::AgentNodeId::from_u128(
                ScopeId::from_u128(1).as_uuid().as_u128(),
            ),
            subject_generation: 0,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: None,
            predicate: None,
            body: body.to_string(),
            confidence: None,
            trust_level: TrustLevel::TrustedUser,
            churn_class: None,
            freshness_due_at: None,
            actor_id: None,
            source_kind: None,
            source_ref: "test:temporal".to_string(),
            observed_at: "2025-01-01T00:00:00Z".to_string(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: Some(valid_from.to_string()),
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        }
    }

    #[test]
    fn recall_time_normalizes_defaults_and_rejects_future_transaction_snapshots() {
        let now: jiff::Timestamp = "2026-07-13T12:00:00Z".parse().unwrap();

        let defaulted = resolve_recall_time(None, None, now).unwrap();
        assert_eq!(defaulted.evaluated_at, "2026-07-13T12:00:00Z");
        assert_eq!(defaulted.transaction_as_of, "2026-07-13T12:00:00Z");
        assert_eq!(defaulted.valid_at, "2026-07-13T12:00:00Z");

        let normalized = resolve_recall_time(
            Some("2026-07-13T03:00:00-07:00"),
            Some("2025-01-01T01:30:00+01:30"),
            now,
        )
        .unwrap();
        assert_eq!(normalized.transaction_as_of, "2026-07-13T10:00:00Z");
        assert_eq!(normalized.valid_at, "2025-01-01T00:00:00Z");

        assert!(matches!(
            resolve_recall_time(Some("2026-07-13T12:00:00.000000001Z"), None, now),
            Err(CoreError::Invalid(message)) if message == "transaction_as_of cannot be in the future"
        ));
        assert!(matches!(
            resolve_recall_time(Some("not-a-time"), None, now),
            Err(CoreError::Invalid(message)) if message == "transaction_as_of must be RFC3339"
        ));
    }

    #[test]
    fn valid_interval_rejects_malformed_and_empty_ranges() {
        assert!(matches!(
            validate_valid_interval(Some("bad"), None),
            Err(CoreError::Invalid(message)) if message == "valid_from must be RFC3339"
        ));
        for (from, to) in [
            ("2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            ("2025-01-02T00:00:00Z", "2025-01-01T00:00:00Z"),
        ] {
            assert!(matches!(
                validate_valid_interval(Some(from), Some(to)),
                Err(CoreError::Invalid(message)) if message == "valid_from must be before valid_to"
            ));
        }
    }

    #[test]
    fn bitemporal_visibility_is_half_open_and_forgetting_overrides_history() {
        let mut historical = temporal_test_unit(900, "old truth", "2025-01-01T00:00:00Z");
        historical.state = UnitState::Superseded;
        historical.transaction_from = Some("2025-02-01T00:00:00Z".to_string());
        historical.transaction_to = Some("2025-03-01T00:00:00Z".to_string());
        historical.valid_to = Some("2025-06-01T00:00:00Z".to_string());

        let inside = RecallTime {
            evaluated_at: "2026-01-01T00:00:00Z".to_string(),
            transaction_as_of: "2025-02-15T00:00:00Z".to_string(),
            valid_at: "2025-05-31T23:59:59Z".to_string(),
        };
        assert!(bitemporally_recallable(&historical, &inside));

        let transaction_end = RecallTime {
            evaluated_at: "2026-01-01T00:00:00Z".to_string(),
            transaction_as_of: "2025-03-01T00:00:00Z".to_string(),
            valid_at: "2025-05-01T00:00:00Z".to_string(),
        };
        assert!(!bitemporally_recallable(&historical, &transaction_end));

        let valid_end = RecallTime {
            evaluated_at: "2026-01-01T00:00:00Z".to_string(),
            transaction_as_of: "2025-02-15T00:00:00Z".to_string(),
            valid_at: "2025-06-01T00:00:00Z".to_string(),
        };
        assert!(!bitemporally_recallable(&historical, &valid_end));

        historical.deletion_generation = Some(1);
        assert!(!bitemporally_recallable(&historical, &inside));
    }

    #[test]
    fn retroactive_correction_splits_valid_time_without_erasing_old_transaction_history() {
        let mut old = temporal_test_unit(901, "old truth", "2025-01-01T00:00:00Z");
        old.valid_to = Some("2026-01-01T00:00:00Z".to_string());
        old.transaction_from = Some("2025-01-02T00:00:00Z".to_string());
        let correction = CorrectionPayload {
            value: "new truth".to_string(),
            reason: "fix".to_string(),
            source_ref: "test:retroactive".to_string(),
            observed_at: "2026-02-01T00:00:00Z".to_string(),
            valid_from: Some("2025-04-01T00:00:00Z".to_string()),
            valid_to: Some("2025-07-01T00:00:00Z".to_string()),
        };

        let (replacement, remainders) = correction_rectangles(
            &old,
            &correction,
            &correction.source_ref,
            &correction.observed_at,
            ActorId::from_u128(2),
            "2026-02-01T00:00:00Z",
        )
        .unwrap();

        assert_eq!(replacement.body, "new truth");
        assert_eq!(
            replacement.valid_from.as_deref(),
            Some("2025-04-01T00:00:00Z")
        );
        assert_eq!(
            replacement.valid_to.as_deref(),
            Some("2025-07-01T00:00:00Z")
        );
        assert_eq!(remainders.len(), 2);
        assert_eq!(remainders[0].body, "old truth");
        assert_eq!(
            remainders[0].valid_from.as_deref(),
            Some("2025-01-01T00:00:00Z")
        );
        assert_eq!(
            remainders[0].valid_to.as_deref(),
            Some("2025-04-01T00:00:00Z")
        );
        assert_eq!(
            remainders[1].valid_from.as_deref(),
            Some("2025-07-01T00:00:00Z")
        );
        assert_eq!(
            remainders[1].valid_to.as_deref(),
            Some("2026-01-01T00:00:00Z")
        );
        assert!(replacement.transaction_from.as_deref() == Some("2026-02-01T00:00:00Z"));
        assert!(
            remainders
                .iter()
                .all(|unit| unit.transaction_from == replacement.transaction_from)
        );
    }

    #[test]
    fn prepared_correction_rectangles_use_the_caller_owned_unit_ids() {
        let mut old = temporal_test_unit(903, "old truth", "2025-01-01T00:00:00Z");
        old.valid_to = Some("2026-01-01T00:00:00Z".to_string());
        let ids = CorrectionUnitIds {
            replacement: UnitId::from_u128(904),
            remainders: [UnitId::from_u128(905), UnitId::from_u128(906)],
        };
        let correction = CorrectionPayload {
            value: "new truth".to_string(),
            reason: "fix".to_string(),
            source_ref: "test:prepared".to_string(),
            observed_at: "2026-02-01T00:00:00Z".to_string(),
            valid_from: Some("2025-04-01T00:00:00Z".to_string()),
            valid_to: Some("2025-07-01T00:00:00Z".to_string()),
        };

        let (replacement, remainders) = correction_rectangles_with_ids(
            &old,
            &correction,
            &correction.source_ref,
            &correction.observed_at,
            ActorId::from_u128(2),
            "2026-02-01T00:00:00Z",
            &ids,
        )
        .unwrap();

        assert_eq!(replacement.id, ids.replacement);
        assert_eq!(
            remainders.iter().map(|unit| unit.id).collect::<Vec<_>>(),
            ids.remainders
        );
    }

    #[test]
    fn current_correction_starts_at_write_time_and_preserves_prior_validity() {
        let mut old = temporal_test_unit(902, "old", "2025-01-01T00:00:00Z");
        old.valid_from = Some("2025-01-01T00:00:00Z".to_string());
        let (replacement, remainders) = correction_rectangles(
            &old,
            &CorrectionPayload {
                value: "new".to_string(),
                reason: "changed now".to_string(),
                source_ref: "test:current".to_string(),
                observed_at: "2025-08-01T00:00:00Z".to_string(),
                valid_from: None,
                valid_to: None,
            },
            "test:current",
            "2025-08-01T00:00:00Z",
            ActorId::from_u128(7),
            "2025-08-01T00:00:00Z",
        )
        .expect("current correction");

        assert_eq!(
            replacement.valid_from.as_deref(),
            Some("2025-08-01T00:00:00Z")
        );
        assert_eq!(remainders.len(), 1);
        assert_eq!(remainders[0].body, old.body);
        assert_eq!(remainders[0].valid_from, old.valid_from);
        assert_eq!(
            remainders[0].valid_to.as_deref(),
            Some("2025-08-01T00:00:00Z")
        );
    }

    #[tokio::test]
    async fn in_memory_review_events_respect_their_recorded_transaction_time() {
        let store = InMemoryStore::default();
        let tenant = TenantId::from_u128(910);
        // The store's strict context contract (canonical cutover) requires a
        // registered binding before any read/write; hand-built contexts are
        // rejected with NotFound("memory context").
        let binding = store
            .resolve_context_binding(
                tenant,
                "review-test".to_string(),
                ContextBindingRequest {
                    subject: memphant_types::ContextBindingEntityRef {
                        external_ref: "subject:review".to_string(),
                        kind: "user".to_string(),
                    },
                    actor: memphant_types::ContextBindingEntityRef {
                        external_ref: "actor:review".to_string(),
                        kind: "user".to_string(),
                    },
                    scope: memphant_types::ContextBindingScopeRef {
                        external_ref: "scope:review".to_string(),
                        kind: "memory".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: memphant_types::ContextBindingAgentRef {
                        external_ref: "agent:review".to_string(),
                        parent_external_ref: None,
                    },
                    access_policies: Vec::new(),
                },
            )
            .await
            .unwrap();
        let context = store
            .resolve_memory_context(
                tenant,
                binding.subject_id,
                binding.actor_id,
                binding.scope_id,
                binding.agent_node_id,
            )
            .await
            .unwrap();
        let recalled = recall(
            &store,
            RecallRequest {
                compact_only: false,
                context: context.clone(),
                query: "nothing stored".to_string(),
                k: 1,
                budget_tokens: 32,
                mode: RecallMode::Fast,
                include_beliefs: false,
                edge_expansion_enabled: false,
                context_packing_abstention_enabled: false,
                procedure_recall_enabled: false,
                decay_enabled: false,
                engine_version: "review-test".to_string(),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
            None,
            &FixedClock("2025-01-01T00:00:00Z"),
        )
        .await
        .unwrap();
        store
            .record_review_events(
                &context,
                vec![ReviewEvent {
                    tenant_id: tenant,
                    trace_id: recalled.trace_id,
                    caller_id: "reviewer".to_string(),
                    used_ids: Vec::new(),
                    outcome: MarkOutcome::Success,
                    recorded_at: "2025-03-01T00:00:00Z".to_string(),
                }],
            )
            .await
            .unwrap();

        let before = RecallTime {
            evaluated_at: "2026-01-01T00:00:00Z".to_string(),
            transaction_as_of: "2025-02-01T00:00:00Z".to_string(),
            valid_at: "2025-02-01T00:00:00Z".to_string(),
        };
        assert!(
            store
                .fetch_review_events(&context, &[], &before,)
                .await
                .unwrap()
                .is_empty()
        );
    }

    #[tokio::test]
    async fn in_memory_staged_edges_require_context_owned_endpoints() {
        let store = InMemoryStore::default();
        let tenant = TenantId::from_u128(920);
        let scope = ScopeId::from_u128(921);
        let src = UnitId::from_u128(922);
        let dst = UnitId::from_u128(923);
        let agent_node_id = AgentNodeId::from_u128(925);
        let context = ResolvedMemoryContext {
            tenant_id: tenant,
            data_subject_id: SubjectId::from_u128(920),
            actor_id: ActorId::from_u128(924),
            actor_trust: TrustLevel::TrustedUser,
            scope_id: scope,
            agent_node_id,
            agent_level: 0,
            subject_generation: 0,
            policy_revision: "test-policy".to_string(),
            sources_by_kind: MemoryKind::ALL
                .into_iter()
                .map(|kind| {
                    (
                        kind,
                        vec![ResolvedMemorySource {
                            scope_id: scope,
                            agent_node_id,
                        }],
                    )
                })
                .collect(),
        };
        let mut tx = store.begin(&context).await.unwrap();
        let result = store
            .stage_memory_edge(
                &mut tx,
                NewMemoryEdge {
                    tenant_id: tenant,
                    scope_id: scope,
                    src_id: src,
                    dst_id: dst,
                    kind: MemoryEdgeKind::SameSubject,
                },
            )
            .await;
        assert!(matches!(result, Err(StoreError::Conflict(_))));
    }

    /// §6 date-parser table: every supported content-date shape parses to the
    /// same normalized `YYYY-MM-DD`, and non-dates / impossible dates are
    /// rejected. Clock-free and deterministic.
    #[test]
    fn parse_content_date_covers_formats_and_rejects_garbage() {
        // (input, expected normalized date or None).
        let cases: &[(&str, Option<&str>)] = &[
            // The bench provenance prefix (slash-separated, zero-padded).
            (
                "[session s1] [date 2023/05/30]\nuser: hi",
                Some("2023-05-30"),
            ),
            // ISO hyphen form.
            ("logged on 2023-05-30 at noon", Some("2023-05-30")),
            // English month name, comma form and no-comma form.
            ("met on May 30, 2023 downtown", Some("2023-05-30")),
            ("due December 1 2024 sharp", Some("2024-12-01")),
            ("shipped Sept 9, 2021", Some("2021-09-09")),
            // Single-digit month/day still normalize with zero padding.
            ("2023-5-3", Some("2023-05-03")),
            // FIRST date wins when several appear.
            ("2020-01-02 then 2021-03-04", Some("2020-01-02")),
            // Garbage / impossible dates → None.
            ("no dates here at all", None),
            ("month 13: 2023-13-01", None),
            ("2023-02-30 is not real", None),
            ("version 12023/05/30 has a 5-digit lead", None),
            ("bare year 2023 alone is not a content date", None),
            ("May 2023 without a day is not a content date", None),
        ];
        for (input, expected) in cases {
            let got = parse_content_date(input).map(|date| date.to_string());
            assert_eq!(
                got.as_deref(),
                *expected,
                "parse_content_date({input:?}) mismatch"
            );
        }
    }

    /// §6 query-date table: full date → single-day window, `Month YYYY` (with or
    /// without a leading "in") → whole-month window, bare year → whole-year
    /// window, no date → `None`. Windows are half-open `[start, end)` midnights.
    #[test]
    fn extract_query_date_covers_day_month_year_and_none() {
        let day = extract_query_date("what happened on 2023-05-30 exactly").expect("day window");
        assert_eq!(day.start, "2023-05-30T00:00:00Z");
        assert_eq!(day.end, "2023-05-31T00:00:00Z");

        let month = extract_query_date("what did I do in May 2023").expect("month window");
        assert_eq!(month.start, "2023-05-01T00:00:00Z");
        assert_eq!(month.end, "2023-06-01T00:00:00Z");

        // No leading "in" still parses as a month window.
        let bare_month = extract_query_date("March 2024 status").expect("month window");
        assert_eq!(bare_month.start, "2024-03-01T00:00:00Z");
        assert_eq!(bare_month.end, "2024-04-01T00:00:00Z");

        // December rolls the month window into the next year.
        let december = extract_query_date("in December 2023").expect("month window");
        assert_eq!(december.start, "2023-12-01T00:00:00Z");
        assert_eq!(december.end, "2024-01-01T00:00:00Z");

        let year = extract_query_date("everything from 2022 please").expect("year window");
        assert_eq!(year.start, "2022-01-01T00:00:00Z");
        assert_eq!(year.end, "2023-01-01T00:00:00Z");

        // A full date takes precedence over the bare year inside it.
        let precedence = extract_query_date("around 2023-07-04 fireworks").expect("day window");
        assert_eq!(precedence.start, "2023-07-04T00:00:00Z");
        assert_eq!(precedence.end, "2023-07-05T00:00:00Z");

        assert!(extract_query_date("no temporal signal here").is_none());
        // A year glued to a token is not a bare-year signal.
        assert!(
            extract_query_date("deploy to project2023 cluster").is_none(),
            "an alphanumeric-glued year is not a bare-year window"
        );
    }

    /// The window membership check is a real half-open interval on parsed
    /// instants: `start <= valid_from < end`, never lexical.
    #[test]
    fn valid_from_window_membership_is_half_open() {
        let window = extract_query_date("in May 2023").expect("month window");
        assert!(
            valid_from_in_window("2023-05-01T00:00:00Z", &window),
            "start is inclusive"
        );
        assert!(
            valid_from_in_window("2023-05-30T00:00:00Z", &window),
            "mid-month is inside"
        );
        assert!(
            !valid_from_in_window("2023-06-01T00:00:00Z", &window),
            "end is exclusive"
        );
        assert!(
            !valid_from_in_window("2023-04-30T00:00:00Z", &window),
            "before start is outside"
        );
    }

    /// The pack-prefix source reads the leading `YYYY-MM-DD` of a grounded
    /// `valid_from`, and yields nothing for a non-date-leading string (a date is
    /// never invented).
    #[test]
    fn date_prefix_reads_valid_from_head() {
        assert_eq!(
            date_prefix_from_valid_from("2023-05-30T00:00:00Z").as_deref(),
            Some("2023-05-30")
        );
        assert_eq!(date_prefix_from_valid_from("not-a-date").as_deref(), None);
        assert_eq!(date_prefix_from_valid_from("").as_deref(), None);
    }
}

#[cfg(test)]
mod fusion_weight_tests {
    use super::*;

    /// W3: channel weights carry NO query-substring special cases. A query
    /// containing "how"/"error" (anywhere, including inside other words) yields
    /// the SAME weight as an equivalent query without them — the deleted hacks
    /// were `query.contains("how")` (Exact→2.5, Lexical→2.0) and
    /// `query.contains("error")` (Lexical→3.0).
    #[test]
    fn channel_weight_has_no_query_substring_special_cases() {
        let with_substrings = "how do I fix this error in the pipeline";
        let without = "do I fix this in the pipeline";
        for pass in [
            ChannelPass::Exact,
            ChannelPass::Lexical,
            ChannelPass::Semantic,
            ChannelPass::Edge,
            ChannelPass::Vector,
        ] {
            assert_eq!(
                channel_weight(pass, with_substrings, None),
                channel_weight(pass, without, None),
                "{pass:?} weight must not depend on 'how'/'error' substrings"
            );
        }
        // The two channels the hacks used to boost now hold their plain base
        // weight regardless of the query.
        assert_eq!(
            channel_weight(ChannelPass::Exact, with_substrings, None),
            EXACT_CHANNEL_WEIGHT
        );
        assert_eq!(
            channel_weight(ChannelPass::Lexical, with_substrings, None),
            LEXICAL_CHANNEL_WEIGHT
        );
        // Substrings buried inside other words ("however" ⊃ "how", "terror" ⊃
        // "error") never trip anything either.
        assert_eq!(
            channel_weight(ChannelPass::Lexical, "however the terror subsided", None),
            LEXICAL_CHANNEL_WEIGHT
        );
    }

    /// The whole-token temporal recency signal is NOT a substring hack and
    /// survives the cleanup: an explicit recency TOKEN still boosts the temporal
    /// channel, a non-recency query keeps the base weight, and a mere substring
    /// ("nowhere" ⊃ "now") does NOT boost.
    #[test]
    fn temporal_recency_intent_is_token_based_and_survives() {
        assert_eq!(
            channel_weight(ChannelPass::Temporal, "what is the latest status", None),
            TEMPORAL_RECENCY_CHANNEL_WEIGHT
        );
        assert_eq!(
            channel_weight(ChannelPass::Temporal, "what is the status", None),
            TEMPORAL_CHANNEL_WEIGHT
        );
        assert_eq!(
            channel_weight(ChannelPass::Temporal, "we are getting nowhere", None),
            TEMPORAL_CHANNEL_WEIGHT
        );
    }
}

#[cfg(test)]
mod chunk_render_tests {
    use super::*;

    /// Uses the production provenance-header shape so the conservative byte
    /// estimator sees realistic punctuation and identifiers.
    fn chunk(turns: &str, body: &str) -> ContextualChunk {
        ContextualChunk {
            id: format!("chunk-ep-{turns}"),
            header: format!("[episode ep] [kind user] [turns {turns}]"),
            body: body.to_string(),
            source_span: Some("0-0".to_string()),
        }
    }

    fn count(haystack: &str, needle: &str) -> usize {
        haystack.matches(needle).count()
    }

    /// A unit with no chunks signals whole-body rendering (`None`) — the caller
    /// then emits `unit.body` byte-for-byte, exactly as before this feature.
    #[test]
    fn unchunked_unit_renders_whole_body() {
        assert_eq!(
            render_chunked_item_body(&[], &tokenize("anything at all"), 100),
            None,
            "no chunks → whole-body fallback"
        );
    }

    /// A chunked unit whose chunks none match the query keeps whole-body
    /// rendering (the unit surfaced via a body-lexical/vector channel).
    #[test]
    fn no_matched_chunk_falls_back_to_whole_body() {
        let chunks = [
            chunk("1-4", "red apple pie crust"),
            chunk("5-8", "green lime soda fizz"),
            chunk("9-12", "blue plum jam toast"),
        ];
        assert_eq!(
            render_chunked_item_body(&chunks, &tokenize("zebra"), 1000),
            None,
            "no chunk matched → whole-body fallback"
        );
    }

    /// Matched chunk first, then neighbours expand outward; with ample budget
    /// every chunk is emitted once, in document order, each header-prefixed.
    #[test]
    fn matched_first_then_neighbours_in_document_order() {
        let chunks = [
            chunk("1-2", "red apple pie"),
            chunk("3-4", "green lime soda"),
            chunk("5-6", "blue mango tart"),
            chunk("7-8", "gold plum cake"),
        ];
        let rendered = render_chunked_item_body(&chunks, &tokenize("mango"), 1000)
            .expect("matched chunk renders");

        // P-2: all four chunks are selected and contiguous, so they collapse to
        // ONE block under a single header spanning the whole run — the four
        // per-chunk headers this used to emit were pure duplication.
        assert_eq!(
            rendered,
            "[episode ep] [kind user] [turns 1-8]\nred apple pie\ngreen lime soda\nblue mango tart\ngold plum cake",
            "contiguous run merges under one run-spanning header"
        );
        for body in [
            "red apple pie",
            "green lime soda",
            "blue mango tart",
            "gold plum cake",
        ] {
            assert_eq!(count(&rendered, body), 1, "{body} emitted once");
        }
        assert_eq!(
            count(&rendered, "[episode ep]"),
            1,
            "one header for the run"
        );
    }

    /// P-2: a positional GAP breaks the run, so each side keeps its own header
    /// and the reader still sees that content is discontinuous.
    #[test]
    fn positional_gap_starts_a_new_block() {
        let chunks = [
            chunk("1-2", "alpha mango start"),
            chunk("3-4", "zzz unrelated filler"),
            chunk("5-6", "omega mango finish"),
        ];
        // Budget for exactly the two matched chunks: the middle one cannot fit,
        // leaving a gap between them.
        let two_matched = chunk_block_token_cost(&chunks[0]) + chunk_block_token_cost(&chunks[2]);
        let rendered = render_chunked_item_body(&chunks, &tokenize("mango"), two_matched)
            .expect("matched chunks render");

        assert!(
            !rendered.contains("zzz unrelated filler"),
            "gap chunk not selected: {rendered}"
        );
        assert!(
            rendered.contains("[turns 1-2]") && rendered.contains("[turns 5-6]"),
            "each side of the gap keeps its own header: {rendered}"
        );
        assert_eq!(
            count(&rendered, "[episode ep]"),
            2,
            "gap yields two blocks: {rendered}"
        );
    }

    /// P-2 is a pure cost REDUCTION: a merged run never charges more than the
    /// per-chunk sum the selection gate assumed, and charges strictly less
    /// whenever any run actually merged. This is the property that makes the
    /// change safe against the chat-lane per-item cost regression that forced
    /// the earlier header fix to be reverted.
    #[test]
    fn merged_cost_never_exceeds_per_chunk_cost() {
        let chunks = [
            chunk("1-2", "red apple pie"),
            chunk("3-4", "green lime soda"),
            chunk("5-6", "blue mango tart"),
        ];
        let per_chunk: usize = chunks.iter().map(chunk_block_token_cost).sum();
        let all = vec![true; chunks.len()];
        let merged = selected_chunk_cost(&chunks, &all, true);
        assert!(
            merged < per_chunk,
            "merged run is strictly cheaper: {merged} vs {per_chunk}"
        );

        // A single-chunk selection is byte-identical to the pre-merge render,
        // so isolated items are completely unaffected.
        let lone = [true, false, false];
        assert_eq!(
            selected_chunk_cost(&chunks, &lone, true),
            chunk_block_token_cost(&chunks[0]),
            "single-chunk run costs exactly what it always did"
        );
        assert_eq!(
            emit_selected_chunks(&chunks, &lone, true),
            chunk_block(&chunks[0]),
            "single-chunk run renders exactly what it always did"
        );
    }

    /// P-2 header dialects: the resource chunker repeats ONE section heading
    /// verbatim, so a run merges without any span surgery; unrelated headers
    /// never merge even when adjacent.
    #[test]
    fn header_dialects_merge_only_when_provenance_agrees() {
        let heading = |body: &str| ContextualChunk {
            id: "chunk-res".to_string(),
            header: "### Deployment".to_string(),
            body: body.to_string(),
            source_span: Some("0-0".to_string()),
        };
        let resource = [heading("first para"), heading("second para")];
        assert_eq!(
            emit_selected_chunks(&resource, &[true, true], true),
            "### Deployment\nfirst para\nsecond para",
            "identical resource headings merge under one heading"
        );

        let mixed = [
            chunk("1-2", "from episode one"),
            ContextualChunk {
                id: "chunk-other".to_string(),
                header: "[episode other] [kind user] [turns 1-2]".to_string(),
                body: "from episode two".to_string(),
                source_span: Some("0-0".to_string()),
            },
        ];
        let rendered = emit_selected_chunks(&mixed, &[true, true], true);
        assert!(
            rendered.contains("[episode ep]") && rendered.contains("[episode other]"),
            "different episodes keep separate headers: {rendered}"
        );
    }

    /// Budget bounds expansion: the matched chunk plus its nearest sibling fit,
    /// the far sibling is dropped. Nearest = window index −1 tried before +1.
    #[test]
    fn budget_cutoff_keeps_matched_and_nearest_neighbour() {
        let chunks = [
            chunk("1-4", "red apple pie crust"),
            chunk("5-8", "green mango tart glaze"),
            chunk("9-12", "blue plum jam toast"),
        ];
        let two_block_budget =
            chunk_block_token_cost(&chunks[0]) + chunk_block_token_cost(&chunks[1]);
        let rendered = render_chunked_item_body(&chunks, &tokenize("mango"), two_block_budget)
            .expect("matched chunk renders");

        assert!(
            rendered.contains("green mango tart glaze"),
            "matched chunk kept"
        );
        assert!(
            rendered.contains("red apple pie crust"),
            "nearest neighbour kept"
        );
        // P-2: chunks 0 and 1 are adjacent and same-episode, so the two blocks
        // collapse under one header spanning turns 1-8.
        assert!(
            rendered.contains("[turns 1-8]"),
            "adjacent kept chunks merge: {rendered}"
        );
        assert!(
            !rendered.contains("blue plum jam toast") && !rendered.contains("[turns 9-12]"),
            "over-budget far sibling dropped: {rendered}"
        );
    }

    /// When only one slot fits, the higher-scoring matched chunk wins over a
    /// lower-scoring matched chunk (matched-first is score-ranked, desc).
    #[test]
    fn higher_scoring_matched_chunk_wins_single_slot() {
        let chunks = [
            chunk("1-4", "green mango plum here"), // matches "mango" only
            chunk("5-8", "green mango tart here"), // matches "mango" AND "tart"
        ];
        let one_block_budget = chunk_block_token_cost(&chunks[1]);
        let rendered = render_chunked_item_body(&chunks, &tokenize("mango tart"), one_block_budget)
            .expect("matched chunk renders");

        assert!(
            rendered.contains("tart"),
            "higher-scoring chunk chosen: {rendered}"
        );
        assert!(rendered.contains("[turns 5-8]"));
        assert!(
            !rendered.contains("plum") && !rendered.contains("[turns 1-4]"),
            "lower-scoring chunk dropped for the single slot: {rendered}"
        );
    }

    /// A sibling adjacent to two matched anchors is considered from both but
    /// emitted only once.
    #[test]
    fn overlapping_expansion_never_duplicates() {
        let chunks = [
            chunk("1-4", "red mango pie"),    // matched
            chunk("5-8", "green lime soda"),  // neighbour of both anchors
            chunk("9-12", "blue mango tart"), // matched
        ];
        let rendered = render_chunked_item_body(&chunks, &tokenize("mango"), 1000)
            .expect("matched chunk renders");

        assert_eq!(
            count(&rendered, "green lime soda"),
            1,
            "shared neighbour emitted once: {rendered}"
        );
        for body in ["red mango pie", "green lime soda", "blue mango tart"] {
            assert_eq!(count(&rendered, body), 1, "{body} emitted once");
        }
    }

    /// Pins the additive safety margin the budget accounting relies on.
    #[test]
    fn chunk_block_cost_has_one_additive_separator_allowance() {
        for c in [
            chunk("1-4", "red apple pie crust"),
            chunk("5-8", "single"),
            chunk("9-12", "many little words here now"),
        ] {
            assert_eq!(
                chunk_block_token_cost(&c),
                conservative_token_estimate(&chunk_block(&c)) + 1,
                "block cost includes exactly one separator allowance for {}",
                c.header
            );
        }
    }

    #[test]
    fn punctuation_dense_context_is_not_charged_as_one_token() {
        let text = "Outlook,calendar,portal,".repeat(100);
        assert_eq!(text.split_whitespace().count(), 1);
        assert!(
            conservative_token_estimate(&text) >= text.len().div_ceil(3),
            "model-facing budget must account for dense non-whitespace text"
        );
    }

    #[test]
    fn matched_chunk_render_respects_model_facing_budget() {
        let chunks = [
            chunk("1-4", &"Outlook,".repeat(20)),
            chunk("5-8", &"Outlook,".repeat(20)),
        ];
        let rendered = render_chunked_item_body(&chunks, &tokenize("Outlook"), 70)
            .expect("one bounded evidence chunk renders");
        assert!(conservative_token_estimate(&rendered) <= 70);
        assert_eq!(rendered.matches("[episode ep]").count(), 1);
    }
}

#[cfg(test)]
mod pack_cost_tests {
    use super::*;

    /// The deterministic all-off control config. `merge_chunk_blocks` is
    /// default-ON since 2026-08-05, so tests that assert the historical
    /// per-chunk render (render-cap, budget-reclaim, completion-pass mechanics
    /// measured against the un-merged baseline) pin it off explicitly here.
    fn all_levers_off() -> PackLevers {
        PackLevers {
            merge_chunk_blocks: false,
            ..PackLevers::default()
        }
    }

    /// A chunk with the same provenance-header shape used elsewhere.
    fn chunk(turns: &str, body: &str) -> ContextualChunk {
        ContextualChunk {
            id: format!("chunk-ep-{turns}"),
            header: format!("[episode ep] [kind user] [turns {turns}]"),
            body: body.to_string(),
            source_span: Some("0-0".to_string()),
        }
    }

    fn unit(id: u128, body: &str, chunks: Vec<ContextualChunk>) -> StoredMemoryUnit {
        StoredMemoryUnit {
            capture: None,
            invalidation: None,
            compact: None,
            id: UnitId::from_u128(id),
            tenant_id: TenantId::from_u128(1),
            data_subject_id: SubjectId::from_u128(1),
            scope_id: ScopeId::from_u128(1),
            agent_node_id: memphant_types::AgentNodeId::from_u128(
                ScopeId::from_u128(1).as_uuid().as_u128(),
            ),
            subject_generation: 0,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: None,
            predicate: None,
            body: body.to_string(),
            confidence: None,
            trust_level: TrustLevel::TrustedUser,
            churn_class: None,
            freshness_due_at: None,
            actor_id: None,
            source_kind: None,
            source_ref: "test:packing".to_string(),
            observed_at: "2026-07-15T00:00:00Z".to_string(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: chunks,
            valid_from: None,
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        }
    }

    #[test]
    fn control_tokens_keep_identifiers_and_paths_whole() {
        assert_eq!(
            bm25_control_tokens("TypeError in src/foo/bar.py: snake_case_name --dry-run"),
            [
                "typeerror",
                "in",
                "src/foo/bar.py",
                "snake_case_name",
                "--dry-run",
            ]
        );
    }

    #[test]
    fn code_tokens_add_sub_tokens_without_losing_the_whole_token() {
        assert_eq!(
            bm25_code_tokens("src/foo/bar.py plain"),
            ["src", "foo", "bar", "py", "src/foo/bar.py", "plain"]
        );
    }

    #[test]
    fn tokens_borrowed_is_the_same_bag_as_owned_tokens() {
        // The alloc-free path must produce the identical token multiset (order
        // aside) as the owned tokenizer, for both scorers, or BM25 scores drift.
        let text = "Fetch src/Foo/bar.py --dry-run snake_case_ID CONST42";
        for scorer in [LexicalScorer::Bm25Control, LexicalScorer::Bm25Code] {
            let mut owned = scorer.tokens(text);
            let mut borrowed: Vec<String> = scorer
                .tokens_borrowed(&text.to_ascii_lowercase())
                .into_iter()
                .map(str::to_string)
                .collect();
            owned.sort();
            borrowed.sort();
            assert_eq!(owned, borrowed, "{scorer:?} token bag diverged");
        }
    }

    #[test]
    fn bm25_prefers_the_rare_query_term_over_the_common_one() {
        // `parser` appears in every body (df = 3, near-zero idf); `xylotron`
        // appears in one. A pure overlap/Jaccard scorer cannot tell them apart.
        let units = vec![
            unit(1, "parser xylotron failed", Vec::new()),
            unit(2, "parser parser parser parser", Vec::new()),
            unit(3, "parser ran clean", Vec::new()),
        ];
        let scores = bm25_unit_scores(&units, "parser xylotron", LexicalScorer::Bm25Control);
        assert!(
            scores[&UnitId::from_u128(1)] > scores[&UnitId::from_u128(2)],
            "the rare-term body must outrank the repeated common-term body: {scores:?}"
        );
        assert!(scores.contains_key(&UnitId::from_u128(3)));
    }

    #[test]
    fn bm25_length_normalization_is_gentler_than_overlap_density() {
        // Same single rare match, one short body and one long body. BM25's
        // b-normalization must not collapse the long body the way dividing by
        // document length does.
        let long_body = format!("xylotron {}", "filler ".repeat(200));
        let units = vec![
            unit(1, "xylotron", Vec::new()),
            unit(2, &long_body, Vec::new()),
        ];
        let scores = bm25_unit_scores(&units, "xylotron", LexicalScorer::Bm25Control);
        let ratio = scores[&UnitId::from_u128(2)] / scores[&UnitId::from_u128(1)];
        let query = vec!["xylotron".to_string()];
        let density_ratio =
            lexical_text_score(&long_body, &query) / lexical_text_score("xylotron", &query);
        assert!(
            ratio > density_ratio * 10.0,
            "bm25 ratio {ratio} should be far above the density ratio {density_ratio}"
        );
    }

    #[test]
    fn bm25_scores_only_units_that_match_a_query_term() {
        let units = vec![
            unit(1, "alpha beta", Vec::new()),
            unit(2, "gamma", Vec::new()),
        ];
        let scores = bm25_unit_scores(&units, "alpha", LexicalScorer::Bm25Control);
        assert_eq!(scores.len(), 1);
        assert!(scores.contains_key(&UnitId::from_u128(1)));
    }

    #[test]
    fn code_tokenization_matches_a_path_the_control_tokenization_misses() {
        // Query names the directory; the body names the full path. The control
        // class keeps `src/foo/bar.py` whole, so it never matches `foo`.
        let units = vec![unit(1, "edited src/foo/bar.py", Vec::new())];
        assert!(bm25_unit_scores(&units, "foo", LexicalScorer::Bm25Control).is_empty());
        assert!(!bm25_unit_scores(&units, "foo", LexicalScorer::Bm25Code).is_empty());
    }

    #[test]
    fn bm25_channel_carries_the_weight_of_both_overlap_passes_it_replaces() {
        assert_eq!(
            channel_weight(ChannelPass::Bm25, "q", None),
            channel_weight(ChannelPass::Lexical, "q", None)
                + channel_weight(ChannelPass::Semantic, "q", None)
        );
    }

    #[test]
    fn the_default_lexical_scorer_is_bm25_code_and_names_itself_in_the_trace() {
        // Flipped 2026-08-01: bm25-code is the shipped default, so a served
        // recall carries `lexical_scorer:bm25-code` as positive evidence that
        // the BM25 pass — not the overlap passes — actually ran. The overlap
        // control arm stays flagless, exactly as before.
        assert_eq!(LexicalScorer::default(), LexicalScorer::Bm25Code);
        assert_eq!(
            LexicalScorer::default().flag(),
            Some("lexical_scorer:bm25-code")
        );
        assert_eq!(LexicalScorer::Overlap.flag(), None);
    }

    fn candidate(unit: StoredMemoryUnit, fused_score: f32) -> CandidateAccumulator {
        let decay = DecayScore::neutral(&unit);
        CandidateAccumulator {
            unit,
            fused_score,
            deep_rank: None,
            cross_rerank_rank: None,
            decay,
            channels: Vec::new(),
        }
    }

    /// The A-recency control's whole read rule, tested without the env flag so
    /// the assertion holds regardless of how the process was launched.
    #[test]
    fn a_recency_keeps_only_the_newest_row_per_subject_key() {
        let keyed = |id: u128, body: &str, key: &str, observed: &str| {
            let mut unit = unit(id, body, Vec::new());
            unit.fact_key = Some(key.to_string());
            unit.observed_at = observed.to_string();
            // Deliberately hostile to the edifice's own signals: the STALE row
            // is the one the state machine would keep (Active, open valid
            // range) and the CURRENT row is marked Superseded and valid-closed.
            // A-recency must ignore all of that and go by `observed_at` alone.
            candidate(unit, 1.0)
        };
        let mut fused = vec![
            keyed(1, "stale rule", "k1", "2026-07-01T00:00:00Z"),
            keyed(2, "current rule", "k1", "2026-07-09T00:00:00Z"),
            keyed(3, "other subject", "k2", "2026-07-02T00:00:00Z"),
        ];
        fused[1].unit.state = UnitState::Superseded;
        fused[1].unit.valid_to = Some("2026-07-05T00:00:00Z".to_string());
        fused.push(candidate(unit(4, "unkeyed evidence", Vec::new()), 1.0));

        retain_most_recent_per_subject(&mut fused);

        let bodies: Vec<&str> = fused.iter().map(|c| c.unit.body.as_str()).collect();
        assert_eq!(
            bodies,
            vec!["current rule", "other subject", "unkeyed evidence"]
        );
    }

    fn channel_candidate(id: u128, channel: RecallChannel, rank: usize) -> CandidateAccumulator {
        let mut candidate = candidate(unit(id, &format!("{channel:?}-{rank}"), Vec::new()), 1.0);
        candidate.channels = vec![(channel, rank, 1.0)];
        candidate
    }

    #[test]
    fn cross_rerank_balances_eight_vector_and_eight_lexical_at_limit_sixteen() {
        let mut candidates =
            (0..32)
                .map(|rank| channel_candidate(rank + 1, RecallChannel::Vector, rank as usize))
                .chain((0..32).map(|rank| {
                    channel_candidate(rank + 101, RecallChannel::Lexical, rank as usize)
                }))
                .collect::<Vec<_>>();

        promote_vector_lexical_balanced(&mut candidates, 16);

        assert_eq!(
            candidates[..16]
                .iter()
                .filter(|candidate| candidate.channels[0].0 == RecallChannel::Vector)
                .count(),
            8
        );
        assert_eq!(
            candidates[..16]
                .iter()
                .filter(|candidate| candidate.channels[0].0 == RecallChannel::Lexical)
                .count(),
            8
        );
    }

    #[test]
    fn cross_rerank_balances_thirty_two_per_channel_at_limit_sixty_four() {
        let mut candidates =
            (0..64)
                .map(|rank| channel_candidate(rank + 1, RecallChannel::Vector, rank as usize))
                .chain((0..64).map(|rank| {
                    channel_candidate(rank + 101, RecallChannel::Lexical, rank as usize)
                }))
                .collect::<Vec<_>>();

        promote_vector_lexical_balanced(&mut candidates, 64);

        assert_eq!(
            candidates[..64]
                .iter()
                .filter(|candidate| candidate.channels[0].0 == RecallChannel::Vector)
                .count(),
            32
        );
        assert_eq!(
            candidates[..64]
                .iter()
                .filter(|candidate| candidate.channels[0].0 == RecallChannel::Lexical)
                .count(),
            32
        );
    }

    #[test]
    fn cross_rerank_balance_deduplicates_and_backfills_channel_overlap() {
        let mut candidates = (0..8)
            .map(|rank| {
                let mut candidate =
                    channel_candidate(rank + 1, RecallChannel::Vector, rank as usize);
                candidate
                    .channels
                    .push((RecallChannel::Lexical, rank as usize, 1.0));
                candidate
            })
            .chain((0..16).map(|rank| {
                channel_candidate(rank + 101, RecallChannel::Lexical, rank as usize + 8)
            }))
            .collect::<Vec<_>>();

        promote_vector_lexical_balanced(&mut candidates, 16);

        assert_eq!(
            candidates[..16]
                .iter()
                .map(|candidate| candidate.unit.id)
                .collect::<std::collections::HashSet<_>>()
                .len(),
            16
        );
        assert_eq!(
            candidates[..16]
                .iter()
                .filter(|candidate| candidate.unit.id.as_uuid().as_u128() >= 101)
                .count(),
            8,
            "overlapping vector+lexical candidates do not consume lexical quota twice"
        );
    }

    #[test]
    fn cross_rerank_balance_backfills_from_the_available_channel() {
        let mut candidates =
            (0..16)
                .map(|rank| candidate(unit(rank + 1, "unchanneled", Vec::new()), 1.0))
                .chain((0..16).map(|rank| {
                    channel_candidate(rank + 101, RecallChannel::Lexical, rank as usize)
                }))
                .collect::<Vec<_>>();

        promote_vector_lexical_balanced(&mut candidates, 16);

        assert!(
            candidates[..16]
                .iter()
                .all(|candidate| candidate.channels[0].0 == RecallChannel::Lexical),
            "a missing vector half is backfilled from ranked lexical candidates before fused fallback"
        );
    }

    /// A minimal request with abstention/rerank/decomposition OFF so the packing
    /// loop is a straight budget-gated append in candidate order — isolating the
    /// cost-charging behaviour under test.
    fn request(budget_tokens: usize) -> RecallRequest {
        RecallRequest {
            compact_only: false,
            context: ResolvedMemoryContext {
                tenant_id: TenantId::from_u128(1),
                data_subject_id: SubjectId::from_u128(1),
                actor_id: ActorId::from_u128(1),
                actor_trust: TrustLevel::TrustedUser,
                scope_id: ScopeId::from_u128(1),
                agent_node_id: AgentNodeId::from_u128(1),
                agent_level: 0,
                subject_generation: 0,
                policy_revision: "test-policy".to_string(),
                sources_by_kind: MemoryKind::ALL
                    .into_iter()
                    .map(|kind| {
                        (
                            kind,
                            vec![ResolvedMemorySource {
                                scope_id: ScopeId::from_u128(1),
                                agent_node_id: AgentNodeId::from_u128(1),
                            }],
                        )
                    })
                    .collect(),
            },
            query: "quantum".to_string(),
            k: 10,
            budget_tokens,
            mode: RecallMode::Fast,
            include_beliefs: true,
            edge_expansion_enabled: false,
            context_packing_abstention_enabled: false,
            procedure_recall_enabled: true,
            decay_enabled: false,
            engine_version: "pack-cost-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        }
    }

    /// `n` one-byte whitespace-separated words → an `n`-token conservative
    /// estimate (the whitespace floor dominates the byte heuristic).
    fn body_of(n: usize) -> String {
        vec!["x"; n].join(" ")
    }

    #[test]
    fn submodular_ordering_selects_complementary_cross_source_evidence() {
        let query_tokens = tokenize("alpha beta gamma delta");
        let mut two_item_request = request(256);
        two_item_request.query = "alpha beta gamma delta".to_string();
        two_item_request.k = 2;
        two_item_request.context_packing_abstention_enabled = true;
        let make = || {
            let mut first = unit(1, "alpha beta shared detail", Vec::new());
            first.source_episode_id = Some(EpisodeId::from_u128(1));
            let mut redundant = unit(2, "alpha beta shared detail repeated", Vec::new());
            redundant.source_episode_id = Some(EpisodeId::from_u128(1));
            let mut complementary = unit(3, "gamma delta complementary answer", Vec::new());
            complementary.source_episode_id = Some(EpisodeId::from_u128(2));
            vec![
                candidate(first, 5.0),
                candidate(redundant, 4.9),
                candidate(complementary, 4.0),
            ]
        };

        let baseline = pack_recall_context(
            make(),
            &two_item_request,
            &[],
            &query_tokens,
            Vec::new(),
            3,
            PackLevers::default(),
            false,
        );
        assert_eq!(
            baseline
                .items
                .iter()
                .map(|item| item.unit_id)
                .collect::<Vec<_>>(),
            vec![UnitId::from_u128(1), UnitId::from_u128(2)]
        );

        let treatment = pack_recall_context(
            make(),
            &two_item_request,
            &[],
            &query_tokens,
            Vec::new(),
            3,
            PackLevers {
                submodular_ordering_enabled: true,
                ..PackLevers::default()
            },
            false,
        );
        assert_eq!(
            treatment
                .items
                .iter()
                .map(|item| item.unit_id)
                .collect::<Vec<_>>(),
            vec![UnitId::from_u128(1), UnitId::from_u128(3)]
        );
    }

    /// Rung-7 per-item render cap: without a cap, a big chunk-matched item's
    /// per-item render budget is its whole body, so sibling expansion refills it
    /// back to nearly the whole session — the pathology that budget-drops
    /// top-ranked gold on LME-S (2026-07-21-rung7-packing-diagnosis.md). With
    /// `pack_render_cap = Some(cap)`, the item renders compactly (matched chunk
    /// only) and the reclaimed budget admits a second item that the uncapped pack
    /// budget-drops. `None` is byte-identical to today (covered elsewhere).
    #[test]
    fn pack_render_cap_reclaims_budget_and_admits_second_item() {
        let query_tokens = tokenize("quantum");
        // Item A (top-ranked): a chunk-matched item. The matched chunk is small,
        // but its three siblings are large — uncapped expansion pulls them all in.
        let big_item = || {
            candidate(
                unit(
                    1,
                    &body_of(120),
                    vec![
                        chunk("1-4", "quantum"),     // small matched anchor
                        chunk("5-8", &body_of(40)),  // large neighbour
                        chunk("9-12", &body_of(40)), // large neighbour
                    ],
                ),
                5.0,
            )
        };
        // Item B (lower-ranked): a plain 20-token item.
        let plain = || candidate(unit(2, &body_of(20), Vec::new()), 4.0);

        // The pathology needs the request budget ≥ A's whole body so uncapped
        // sibling expansion refills A to (nearly) its whole self. Costs:
        let anchor_cost = chunk_block_token_cost(&chunk("1-4", "quantum"));
        let expanded_a: usize = [
            chunk("1-4", "quantum"),
            chunk("5-8", &body_of(40)),
            chunk("9-12", &body_of(40)),
        ]
        .iter()
        .map(chunk_block_token_cost)
        .sum();
        // Budget holds the fully-expanded A but leaves < 20 for B (so B drops
        // uncapped), yet holds the anchor-only A plus B (so B fits capped).
        let budget = expanded_a + 10; // A expands fully; B(20) then overflows
        let cap = anchor_cost; // per-item render cap = one matched block

        // Uncapped: A expands to its big siblings, exhausting the budget → B drops.
        // Merge pinned off so `expanded_a` (a per-chunk sum) is the real cost.
        let uncapped = pack_recall_context(
            vec![big_item(), plain()],
            &request(budget),
            &[],
            &query_tokens,
            Vec::new(),
            2,
            all_levers_off(),
            false,
        );
        assert_eq!(
            uncapped.items.len(),
            1,
            "uncapped: expanded A eats the budget, B is dropped"
        );

        // Capped: A renders compact (anchor only), reclaimed budget admits B.
        let capped = pack_recall_context(
            vec![big_item(), plain()],
            &request(budget),
            &[],
            &query_tokens,
            Vec::new(),
            2,
            PackLevers {
                pack_render_cap: Some(cap),
                merge_chunk_blocks: false,
                ..PackLevers::default()
            },
            false,
        );
        assert_eq!(
            capped.items.len(),
            2,
            "capped: compact A leaves room for B — both packed"
        );
        assert!(
            capped
                .items
                .iter()
                .any(|i| i.unit_id == UnitId::from_u128(2)),
            "the reclaimed budget admits the lower-ranked item B"
        );
    }

    /// Budget reclaim: charging a chunk-rendered item its RENDERED token count
    /// (not the whole-body count) frees budget so a second item that whole-body
    /// charging would drop now fits. Same bodies, same budget, only chunking
    /// differs — and the packed second item is the chunk render, not the raw body.
    #[test]
    fn rendered_cost_reclaim_admits_second_item() {
        let query_tokens = tokenize("quantum");
        // Item A: a plain 15-token item, packed first in both scenarios.
        let plain = || candidate(unit(1, &body_of(15), Vec::new()), 5.0);
        // Item B: a 40-token whole body. Its matched chunk render is cheaper
        // under the same conservative estimator.
        let chunks = || {
            vec![
                chunk("1-4", "quantum harmonica"), // matches the query
                chunk("5-8", "berlin note"),       // neighbour, pulled by expansion
            ]
        };
        let rendered_cost: usize = chunks().iter().map(chunk_block_token_cost).sum();
        let budget = 15 + rendered_cost;

        // Whole-body charging (B has no chunks): B costs 40, does not fit → drop.
        // Merge pinned off so `rendered_cost` (a per-chunk sum) is the real cost.
        let whole = pack_recall_context(
            vec![plain(), candidate(unit(2, &body_of(40), Vec::new()), 4.0)],
            &request(budget),
            &[],
            &query_tokens,
            Vec::new(),
            2,
            all_levers_off(),
            false,
        );
        assert_eq!(
            whole.items.len(),
            1,
            "whole-body charging packs only the plain item"
        );
        assert_eq!(whole.items[0].unit_id, UnitId::from_u128(1));
        assert_eq!(whole.token_estimate, 15);

        // Rendered charging reclaims enough budget to admit B.
        let rendered = pack_recall_context(
            vec![plain(), candidate(unit(2, &body_of(40), chunks()), 4.0)],
            &request(budget),
            &[],
            &query_tokens,
            Vec::new(),
            2,
            all_levers_off(),
            false,
        );
        assert_eq!(
            rendered.items.len(),
            2,
            "rendered charging reclaims budget for the second item"
        );
        assert_eq!(
            rendered.items[1].unit_id,
            UnitId::from_u128(2),
            "the reclaimed second item is B"
        );
        assert_eq!(rendered.token_estimate, budget);
        let b_body = &rendered.items[1].body;
        assert!(
            b_body.contains("[turns 1-4]") && b_body.contains("quantum harmonica"),
            "matched chunk rendered: {b_body}"
        );
        assert!(
            b_body.contains("[turns 5-8]") && b_body.contains("berlin note"),
            "neighbour chunk rendered: {b_body}"
        );
        assert!(
            !b_body.contains("filler"),
            "raw whole body not emitted: {b_body}"
        );
    }

    /// Property: a chunk-rendered item's charged cost conservatively covers the
    /// rendered text estimate and never exceeds the whole-body estimate.
    #[test]
    fn charged_cost_matches_rendered_tokens_and_never_exceeds_whole_body() {
        let query_tokens = tokenize("quantum tart");
        // (whole-body word count, chunks) across matched / unmatched / no-chunk /
        // nothing-fits layouts.
        let cases = vec![
            (
                40,
                vec![
                    chunk("1-4", "quantum harmonica"),
                    chunk("5-8", "berlin note"),
                ],
            ),
            (
                12,
                vec![
                    chunk("1-4", "quantum tart glaze here"),
                    chunk("5-8", "plum jam toast crust"),
                ],
            ),
            // Tiny body: matched blocks each cost more than the whole-body cap,
            // so nothing fits and render falls back (charged == whole body).
            (
                6,
                vec![
                    chunk("1-2", "quantum"),
                    chunk("3-4", "berlin"),
                    chunk("5-6", "tart"),
                ],
            ),
            // No chunks → whole-body path.
            (25, vec![]),
            // Chunked but no chunk matches the query → whole-body fallback.
            (
                30,
                vec![
                    chunk("1-4", "red apple pie"),
                    chunk("5-8", "green lime soda"),
                ],
            ),
        ];
        for (body_words, chunks) in cases {
            let u = unit(1, &body_of(body_words), chunks);
            let whole_body_tokens = conservative_token_estimate(&u.body);
            let (rendered_body, charged) = packed_body_and_cost(&u, &query_tokens);
            match &rendered_body {
                Some(text) => {
                    assert!(
                        charged >= conservative_token_estimate(text),
                        "charged cost covers the rendered model-token estimate"
                    );
                    assert!(
                        charged <= whole_body_tokens,
                        "rendered charge {charged} <= whole-body {whole_body_tokens}"
                    );
                }
                None => assert_eq!(
                    charged, whole_body_tokens,
                    "un-rendered item charged exactly its whole-body count"
                ),
            }
        }
    }

    /// No-chunks path: every item is charged its exact whole-body count and
    /// packing decisions / token_estimate are unchanged — the default caller
    /// (chunk-write OFF) is bit-identical to before this change.
    #[test]
    fn no_chunks_path_charges_whole_body_and_is_unchanged() {
        let query_tokens = tokenize("quantum");
        let a = candidate(unit(1, &body_of(10), Vec::new()), 5.0);
        let b = candidate(unit(2, &body_of(10), Vec::new()), 4.0);
        let c = candidate(unit(3, &body_of(10), Vec::new()), 3.0);
        // Budget 25 fits two 10-token bodies (20); the third overflows.
        let packed = pack_recall_context(
            vec![a, b, c],
            &request(25),
            &[],
            &query_tokens,
            Vec::new(),
            3,
            PackLevers::default(),
            false,
        );
        assert_eq!(
            packed.items.iter().map(|i| i.unit_id).collect::<Vec<_>>(),
            vec![UnitId::from_u128(1), UnitId::from_u128(2)],
            "first two fit in candidate order"
        );
        assert_eq!(
            packed.token_estimate, 20,
            "token_estimate == sum of whole-body counts"
        );
        assert_eq!(
            packed.items[0].body,
            body_of(10),
            "whole body emitted byte-identical"
        );
        assert_eq!(
            packed.items[1].body,
            body_of(10),
            "whole body emitted byte-identical"
        );
        assert!(
            packed
                .dropped_items
                .iter()
                .any(|d| d.unit_id == UnitId::from_u128(3) && d.reason == RecallDropReason::Budget),
            "third item dropped for budget: {:?}",
            packed.dropped_items
        );
    }

    /// A unit stamped with an explicit `source_episode_id` so the packing tests
    /// can exercise the per-session diversity quota.
    fn unit_ep(
        id: u128,
        episode: u128,
        body: &str,
        chunks: Vec<ContextualChunk>,
    ) -> StoredMemoryUnit {
        let mut unit = unit(id, body, chunks);
        unit.source_episode_id = Some(EpisodeId::from_u128(episode));
        unit
    }

    fn distinct_episodes(packed: &PackedRecallContext) -> std::collections::HashSet<EpisodeId> {
        packed
            .items
            .iter()
            .filter_map(|item| item.citation_episode_id)
            .collect()
    }

    #[test]
    fn quantity_rollup_consumes_one_slot_and_leaves_the_rest_in_rank_order() {
        let top = candidate(unit(1, "irrelevant", Vec::new()), 2.0);
        let next = candidate(unit(2, "quantum", Vec::new()), 1.0);
        let mut rollup = candidate(unit(3, "quantity rollup quantum", Vec::new()), 100.0);
        rollup.unit.source_kind = Some("quantity_rollup".to_string());
        let mut request = request(100);
        request.k = 2;

        let packed = pack_recall_context(
            vec![top, next, rollup],
            &request,
            &[],
            &tokenize("quantum"),
            Vec::new(),
            3,
            PackLevers::default(),
            false,
        );

        assert_eq!(
            packed
                .items
                .iter()
                .map(|item| item.unit_id)
                .collect::<Vec<_>>(),
            vec![UnitId::from_u128(3), UnitId::from_u128(1)],
            "the projection consumes one slot; the rest fill in the established \
             order, not by a packing-only re-score",
        );
    }

    /// Track R (2026-07-30) measured 27 of 56 displaced golds as `Rerank`
    /// evictions of an ALREADY-ADMITTED top-k item by a candidate from below the
    /// cut. `packing_relevance_score` is not the order the pack was handed, so
    /// once the output is full the established rank decides and later candidates
    /// are dropped, never swapped in.
    #[test]
    fn full_pack_keeps_rank_order_against_a_lexically_stronger_late_candidate() {
        let ranked_first = candidate(unit(1, "alpha", Vec::new()), 9.0);
        let ranked_second = candidate(unit(2, "beta", Vec::new()), 8.0);
        // Below the cut, but every packing-only term (exact/lexical/overlap)
        // favours it, so the old contest evicted `ranked_second` for it.
        let late = candidate(unit(3, "quantum quantum quantum", Vec::new()), 0.1);
        let mut request = request(100);
        request.k = 2;

        let packed = pack_recall_context(
            vec![ranked_first, ranked_second, late],
            &request,
            &[],
            &tokenize("quantum"),
            Vec::new(),
            3,
            PackLevers::default(),
            false,
        );

        assert_eq!(
            packed
                .items
                .iter()
                .map(|item| item.unit_id)
                .collect::<Vec<_>>(),
            vec![UnitId::from_u128(1), UnitId::from_u128(2)],
            "a below-the-cut candidate must not take a packed slot from a \
             higher-ranked admitted item",
        );
        assert!(
            packed
                .dropped_items
                .iter()
                .any(|dropped| dropped.unit_id == UnitId::from_u128(3)
                    && dropped.reason == RecallDropReason::OutputLimit),
            "the late candidate is the one dropped: {:?}",
            packed.dropped_items,
        );
    }

    /// A partially chunk-rendered item reaches FULL coverage on the pack's
    /// leftover budget without disturbing anything else. At admission the
    /// per-item whole-body cap admits the matched chunk plus one neighbour; the
    /// trailing sibling is left out until the completion pass recovers it. It
    /// must never evict the co-packed plain item nor push `token_estimate` past
    /// the budget. This is what the deleted sibling-gather lever was for; the
    /// completion pass covers it, and covers it with the window headers intact.
    #[test]
    fn partial_render_reaches_full_coverage_without_eviction_or_overbudget() {
        let query_tokens = tokenize("quantum");
        let chunks = || {
            vec![
                chunk("1-2", "quantum alpha"), // matches the query
                chunk("3-4", "beta gamma"),    // neighbour pulled at admission
                chunk("5-6", "delta epsilon"), // trailing sibling, recovered later
            ]
        };
        let chunk_costs = chunks()
            .iter()
            .map(chunk_block_token_cost)
            .collect::<Vec<_>>();
        let admission_cost = chunk_costs[0] + chunk_costs[1];
        let expanded_cost: usize = chunk_costs.iter().sum();
        let budget = 100;
        let packed = pack_recall_context(
            vec![
                candidate(unit(1, &body_of(admission_cost), chunks()), 5.0),
                candidate(unit(2, &body_of(5), Vec::new()), 4.0),
            ],
            &request(budget),
            &[],
            &query_tokens,
            Vec::new(),
            2,
            all_levers_off(),
            false,
        );

        assert_eq!(packed.items.len(), 2, "the completion pass never evicts");
        assert_eq!(
            packed.items[1].unit_id,
            UnitId::from_u128(2),
            "the co-packed plain item survives"
        );
        assert_eq!(
            packed.items[1].body,
            body_of(5),
            "plain item body unchanged"
        );
        assert!(
            packed.items[0].body.contains("[turns 5-6]")
                && packed.items[0].body.contains("delta epsilon"),
            "the trailing sibling is recovered, header intact: {}",
            packed.items[0].body
        );
        assert!(
            packed.items[0].body.contains("quantum alpha")
                && packed.items[0].body.contains("beta gamma"),
            "the admission chunks are preserved in document order: {}",
            packed.items[0].body
        );
        assert_eq!(packed.token_estimate, expanded_cost + 5);
        assert!(packed.token_estimate <= budget, "never exceeds budget");
    }

    /// W1 render loss. Every chunk block is charged its provenance header ON TOP
    /// of its body, while the per-item render budget is the whole body — so full
    /// chunk coverage always costs MORE than the whole body and an uncapped
    /// chunked item can never emit all of itself. It drops chunks while charging
    /// nearly the whole-body price, and the span the reader needs goes with them.
    ///
    /// The post-fill completion pass trades the partial render up — a superset,
    /// since chunk bodies are byte slices of the body — but ONLY when the pack's
    /// leftover budget covers the difference. It prefers FULL CHUNK COVERAGE,
    /// which keeps the per-window provenance headers, and falls back to the bare
    /// whole body only when those headers do not fit. On a budget-bound pack it
    /// is a no-op, which is the whole reason it is not the reverted
    /// admission-time fallback. A deliberate `pack_render_cap` also stays in
    /// force: bounding the item is that lever's entire purpose.
    #[test]
    fn partial_chunk_render_completes_itself_only_when_leftover_budget_allows() {
        let query_tokens = tokenize("quantum");
        let bodies = [
            "quantum alpha alpha alpha alpha",
            "beta gamma gamma gamma gammax",
            "delta epsilon epsilon epsilon",
            "omega goldspan goldspan omega",
        ];
        let chunks = || {
            bodies
                .iter()
                .enumerate()
                .map(|(index, body)| chunk(&format!("{}-{}", 2 * index + 1, 2 * index + 2), body))
                .collect::<Vec<_>>()
        };
        let whole = bodies.join(" ");
        let whole_cost = conservative_token_estimate(&whole);
        let full_coverage_cost: usize = chunks().iter().map(chunk_block_token_cost).sum();
        assert!(
            full_coverage_cost > whole_cost,
            "the defect's precondition: full chunk coverage ({full_coverage_cost}) costs more \
             than the whole body ({whole_cost}), which is the render budget"
        );

        // What admission renders on its own — the defect, in isolation.
        let (rendered, admission_cost, mask) =
            packed_render(&unit(1, &whole, chunks()), &query_tokens, 4096, None, false);
        let rendered = rendered.expect("the matched chunk renders");
        assert!(
            mask.expect("chunk-rendered").iter().any(|picked| !picked),
            "the selection is partial: {rendered}"
        );
        assert!(
            !rendered.contains("goldspan"),
            "the span is dropped by the render: {rendered}"
        );
        assert!(
            admission_cost < whole_cost,
            "…while {admission_cost} of the whole body's {whole_cost} tokens are charged"
        );

        let plain_cost = 5;
        let candidates = || {
            vec![
                candidate(unit(1, &whole, chunks()), 5.0),
                candidate(unit(2, &body_of(plain_cost), Vec::new()), 4.0),
            ]
        };
        let pack = |budget: usize, levers: PackLevers| {
            pack_recall_context(
                candidates(),
                &request(budget),
                &[],
                &query_tokens,
                Vec::new(),
                2,
                levers,
                false,
            )
        };

        // Slot-bound (budget to spare): the item emits all of itself AS CHUNK
        // BLOCKS, so every window keeps its provenance header — the whole body
        // carries none. The co-packed plain item is untouched.
        let roomy = pack(4096, all_levers_off());
        assert_eq!(roomy.items.len(), 2, "nothing is displaced");
        assert_eq!(
            roomy.items[0].body,
            emit_selected_chunks(&chunks(), &vec![true; bodies.len()], false),
            "full chunk coverage is emitted, headers and all"
        );
        assert!(
            roomy.items[0].body.contains("goldspan"),
            "the dropped span is recovered: {}",
            roomy.items[0].body
        );
        assert!(
            roomy.items[0].body.contains("[turns 7-8]"),
            "…carrying its window header: {}",
            roomy.items[0].body
        );
        assert_eq!(roomy.items[1].body, body_of(plain_cost));
        assert_eq!(roomy.token_estimate, full_coverage_cost + plain_cost);
        assert!(roomy.token_estimate <= 4096, "never exceeds budget");

        // Between the two: enough leftover for the whole body but not for the
        // per-window headers on top of it. Content still wins — the bare whole
        // body is the fallback, never a silent partial render.
        let header_bound = pack(whole_cost + plain_cost, all_levers_off());
        assert_eq!(
            header_bound.items[0].body, whole,
            "the whole body is the fallback when the headers do not fit"
        );
        assert_eq!(header_bound.token_estimate, whole_cost + plain_cost);

        // Budget-bound (leftover zero): byte-identical to the old behaviour.
        let tight_budget = admission_cost + plain_cost;
        let tight = pack(tight_budget, all_levers_off());
        assert_eq!(tight.items.len(), 2, "no item is displaced");
        assert_eq!(
            tight.items[0].body, rendered,
            "the partial chunk render is kept when the difference does not fit"
        );
        assert_eq!(tight.token_estimate, tight_budget);

        // A deliberate render cap is not overridden.
        let capped = pack(
            4096,
            PackLevers {
                merge_chunk_blocks: false,
                session_quota: None,
                pack_render_cap: Some(admission_cost),
                submodular_ordering_enabled: false,
            },
        );
        assert_eq!(
            capped.items[0].body, rendered,
            "pack_render_cap still bounds the item"
        );

        // P-2, paired at ONE budget: enough for full coverage under a single
        // run-spanning header, but NOT under one header per window.
        let merged_coverage_cost = selected_chunk_cost(&chunks(), &vec![true; bodies.len()], true);
        assert!(
            merged_coverage_cost < full_coverage_cost,
            "merging is strictly cheaper than the per-chunk price: \
             {merged_coverage_cost} < {full_coverage_cost}"
        );
        let paired_budget = merged_coverage_cost + plain_cost;

        // Lever off: the per-window headers do not fit, so provenance is lost
        // entirely — the reader gets the bare whole body.
        let off = pack(paired_budget, all_levers_off());
        assert_eq!(
            off.items[0].body, whole,
            "per-chunk headers do not fit at this budget, so none survive"
        );

        // Lever on: the same budget buys every span WITH its provenance.
        let merged = pack(
            paired_budget,
            PackLevers {
                merge_chunk_blocks: true,
                ..PackLevers::default()
            },
        );
        assert!(
            merged.items[0].body.contains("goldspan"),
            "every span is emitted: {}",
            merged.items[0].body
        );
        assert!(
            merged.items[0].body.contains("[turns 1-8]"),
            "…under ONE run-spanning header: {}",
            merged.items[0].body
        );
        assert_eq!(
            merged.items[0].body.matches("[episode ep]").count(),
            1,
            "exactly one header survives the merge: {}",
            merged.items[0].body
        );
        assert!(
            merged.token_estimate <= paired_budget,
            "never exceeds budget: {} vs {paired_budget}",
            merged.token_estimate
        );
    }

    /// §5 quota: an unquota'd greedy fill (candidate order) lets episode 1's eight
    /// leading candidates monopolise a `k = 8` pack (one distinct episode). The
    /// quota caps admissions per episode at 2 until every session has had a
    /// look-in, so ≥4 distinct episodes surface. Bodies are query-disjoint and
    /// equally scored, so no replacement fires — the quota alone drives the change.
    #[test]
    fn session_quota_admits_distinct_episodes_over_monopoly() {
        let query_tokens = tokenize("quantum");
        let candidates = || {
            let mut v = Vec::new();
            for i in 0..8u128 {
                v.push(candidate(
                    unit_ep(100 + i, 1, "alpha beta", Vec::new()),
                    1.0,
                ));
            }
            for episode in 2..=5u128 {
                for j in 0..2u128 {
                    v.push(candidate(
                        unit_ep(episode * 10 + j, episode, "alpha beta", Vec::new()),
                        1.0,
                    ));
                }
            }
            v
        };
        let mut req = request(10_000);
        req.k = 8;

        let off = pack_recall_context(
            candidates(),
            &req,
            &[],
            &query_tokens,
            Vec::new(),
            16,
            PackLevers::default(),
            false,
        );
        assert_eq!(
            distinct_episodes(&off).len(),
            1,
            "unquota'd greedy is monopolised by episode 1"
        );

        let on = pack_recall_context(
            candidates(),
            &req,
            &[],
            &query_tokens,
            Vec::new(),
            16,
            PackLevers {
                merge_chunk_blocks: false,
                session_quota: Some(DEFAULT_SESSION_DIVERSITY_QUOTA),
                pack_render_cap: None,
                submodular_ordering_enabled: false,
            },
            false,
        );
        assert!(
            distinct_episodes(&on).len() >= 4,
            "the quota surfaces >=4 distinct episodes, got {:?}",
            distinct_episodes(&on)
        );
        assert_eq!(on.items.len(), 8, "the pack is still filled to k");
    }

    /// §5 work-conserving: the quota must never leave admissible budget unused.
    /// With one dominant session (four candidates) plus one other, the capped
    /// pass admits only two of the big session; the second, unrestricted pass
    /// then fills the rest, so the quota packs exactly the same units as the
    /// unquota'd fill.
    #[test]
    fn session_quota_is_work_conserving() {
        let query_tokens = tokenize("quantum");
        let candidates = || {
            let mut v = Vec::new();
            for i in 0..4u128 {
                v.push(candidate(
                    unit_ep(100 + i, 1, "alpha beta", Vec::new()),
                    1.0,
                ));
            }
            v.push(candidate(unit_ep(200, 2, "alpha beta", Vec::new()), 1.0));
            v
        };
        let mut req = request(10_000);
        req.k = 10;

        let off = pack_recall_context(
            candidates(),
            &req,
            &[],
            &query_tokens,
            Vec::new(),
            5,
            PackLevers::default(),
            false,
        );
        let on = pack_recall_context(
            candidates(),
            &req,
            &[],
            &query_tokens,
            Vec::new(),
            5,
            PackLevers {
                merge_chunk_blocks: false,
                session_quota: Some(2),
                pack_render_cap: None,
                submodular_ordering_enabled: false,
            },
            false,
        );
        let off_ids: std::collections::HashSet<UnitId> =
            off.items.iter().map(|item| item.unit_id).collect();
        let on_ids: std::collections::HashSet<UnitId> =
            on.items.iter().map(|item| item.unit_id).collect();
        assert_eq!(
            on_ids, off_ids,
            "the quota leaves no admissible candidate unpacked"
        );
        assert_eq!(on.items.len(), 5, "all five candidates packed");
        assert_eq!(
            on.token_estimate, off.token_estimate,
            "no budget left unused vs the unrestricted fill"
        );
    }

    /// R1.5-T0 / D1 fix: `recall_pack_scan_limit` no longer clamps
    /// `scan_limit == output_limit == k` in Fast — that conflated the
    /// caller-presentation `k` with the engine's internal fan-out (D1: a
    /// larger `k` widened the scan window, which changed even the top-5
    /// ordering). Quota off must now match the pool floor
    /// (`recall_pool_depth.max(k)` — `k=8 < 64` here so the pool wins); quota
    /// on doubles the POOL FLOOR for headroom, so a `k` LARGER than the pool
    /// depth still gets scan room past its own `k` (second block below) —
    /// `recall_pool_depth*2` alone would sit at/below `scan_limit` there and
    /// silence the quota for exactly the large-k callers.
    ///
    /// wave-final-review finding (superseded by the above): quota off used to
    /// reproduce `output_limit.min(candidate_count).max(1)` and quota on
    /// widened to `2*k` — both were `k`-derived, which this test now pins
    /// against the decoupled formula instead.
    #[test]
    fn recall_pack_scan_limit_quota_off_is_unchanged_quota_on_widens() {
        let mut req = request(10_000);
        req.k = 8;
        let candidate_count = 100;

        req.mode = RecallMode::Fast;
        let off = recall_pack_scan_limit(
            &req,
            candidate_count,
            PackLevers::default(),
            DEFAULT_RECALL_POOL_DEPTH,
        );
        assert_eq!(
            off, DEFAULT_RECALL_POOL_DEPTH,
            "Fast: quota off must match recall_pool_depth (floored at k), not k"
        );

        let on = recall_pack_scan_limit(
            &req,
            candidate_count,
            PackLevers {
                merge_chunk_blocks: false,
                session_quota: Some(DEFAULT_SESSION_DIVERSITY_QUOTA),
                pack_render_cap: None,
                submodular_ordering_enabled: false,
            },
            DEFAULT_RECALL_POOL_DEPTH,
        );
        assert_eq!(
            on,
            DEFAULT_RECALL_POOL_DEPTH * 2,
            "Fast: quota on widens to 2*recall_pool_depth (not 2*k) so the quota has headroom"
        );

        // k LARGER than the pool depth: the quota widen must double the pool
        // FLOOR (max(pool, k) = 200 here), not the bare pool depth — bare
        // `64*2 = 128 < k` would leave the quota with no headroom past k.
        req.k = 200;
        let candidate_count = 1_000;
        let off = recall_pack_scan_limit(
            &req,
            candidate_count,
            PackLevers::default(),
            DEFAULT_RECALL_POOL_DEPTH,
        );
        assert_eq!(
            off, 200,
            "Fast: quota off, k>pool: the pool floor is k itself"
        );
        let on = recall_pack_scan_limit(
            &req,
            candidate_count,
            PackLevers {
                merge_chunk_blocks: false,
                session_quota: Some(DEFAULT_SESSION_DIVERSITY_QUOTA),
                pack_render_cap: None,
                submodular_ordering_enabled: false,
            },
            DEFAULT_RECALL_POOL_DEPTH,
        );
        assert_eq!(
            on, 400,
            "Fast: quota on, k>pool: widen doubles the pool floor (2*max(pool, k)), \
             keeping headroom past k"
        );
    }

    /// wave-final-review: the same monopoly corpus as
    /// `session_quota_admits_distinct_episodes_over_monopoly`, but run
    /// through the REAL `recall_pack_scan_limit` derivation (as
    /// `recall_with_pool` calls it) instead of a hand-passed `scan_limit`,
    /// and in Fast mode — the mode where the quota was inert before this fix.
    #[test]
    fn fast_mode_quota_surfaces_diversity_via_real_scan_limit_derivation() {
        let query_tokens = tokenize("quantum");
        let candidates = || {
            let mut v = Vec::new();
            for i in 0..8u128 {
                v.push(candidate(
                    unit_ep(100 + i, 1, "alpha beta", Vec::new()),
                    1.0,
                ));
            }
            for episode in 2..=5u128 {
                for j in 0..2u128 {
                    v.push(candidate(
                        unit_ep(episode * 10 + j, episode, "alpha beta", Vec::new()),
                        1.0,
                    ));
                }
            }
            v
        };
        let mut req = request(10_000);
        req.k = 8;
        req.mode = RecallMode::Fast;

        let off_levers = PackLevers::default();
        let off_scan_limit = recall_pack_scan_limit(
            &req,
            candidates().len(),
            off_levers,
            DEFAULT_RECALL_POOL_DEPTH,
        );
        let off = pack_recall_context(
            candidates(),
            &req,
            &[],
            &query_tokens,
            Vec::new(),
            off_scan_limit,
            off_levers,
            false,
        );
        assert_eq!(
            distinct_episodes(&off).len(),
            1,
            "quota off: Fast mode's real derivation still monopolises episode 1"
        );

        let on_levers = PackLevers {
            merge_chunk_blocks: false,
            session_quota: Some(DEFAULT_SESSION_DIVERSITY_QUOTA),
            pack_render_cap: None,
            submodular_ordering_enabled: false,
        };
        let on_scan_limit = recall_pack_scan_limit(
            &req,
            candidates().len(),
            on_levers,
            DEFAULT_RECALL_POOL_DEPTH,
        );
        assert!(
            on_scan_limit > req.k,
            "quota on: the real derivation must widen past k, got {on_scan_limit}"
        );
        let on = pack_recall_context(
            candidates(),
            &req,
            &[],
            &query_tokens,
            Vec::new(),
            on_scan_limit,
            on_levers,
            false,
        );
        assert!(
            distinct_episodes(&on).len() > 1,
            "quota on: the real derivation gives the quota headroom to surface >1 distinct episode, got {:?}",
            distinct_episodes(&on)
        );
        assert_eq!(on.items.len(), 8, "the pack is still filled to k");
    }

    /// Deterministic control arm: with EVERY lever off — including
    /// `merge_chunk_blocks`, which is default-ON since 2026-08-05 — the packer
    /// matches the pre-lever golden bit-for-bit (composition, bodies, cost,
    /// drops). The all-off config is constructed explicitly because
    /// `PackLevers::default()` now merges; this asserts the `…=0` control arm
    /// still reproduces the historical per-chunk render.
    #[test]
    fn control_arm_all_levers_off_matches_pre_lever_golden() {
        let query_tokens = tokenize("quantum");
        let off_levers = PackLevers {
            merge_chunk_blocks: false,
            ..PackLevers::default()
        };
        let chunk_render_cost: usize = [
            chunk("1-4", "quantum harmonica"),
            chunk("5-8", "berlin note"),
        ]
        .iter()
        .map(chunk_block_token_cost)
        .sum();
        let scenario = || {
            vec![
                candidate(unit_ep(1, 1, &body_of(10), Vec::new()), 5.0),
                candidate(
                    unit_ep(
                        2,
                        1,
                        &body_of(40),
                        vec![
                            chunk("1-4", "quantum harmonica"),
                            chunk("5-8", "berlin note"),
                        ],
                    ),
                    4.0,
                ),
                candidate(unit_ep(3, 2, &body_of(10), Vec::new()), 3.0),
            ]
        };
        let budget = 10 + chunk_render_cost;
        let reference = pack_recall_context(
            scenario(),
            &request(budget),
            &[],
            &query_tokens,
            Vec::new(),
            3,
            off_levers,
            false,
        );
        let again = pack_recall_context(
            scenario(),
            &request(budget),
            &[],
            &query_tokens,
            Vec::new(),
            3,
            off_levers,
            false,
        );
        assert_eq!(
            reference
                .items
                .iter()
                .map(|item| (item.unit_id, item.body.clone()))
                .collect::<Vec<_>>(),
            again
                .items
                .iter()
                .map(|item| (item.unit_id, item.body.clone()))
                .collect::<Vec<_>>(),
            "composition + bodies identical across default runs",
        );
        assert_eq!(reference.token_estimate, again.token_estimate);
        assert_eq!(
            reference
                .dropped_items
                .iter()
                .map(|drop| (drop.unit_id, format!("{:?}", drop.reason)))
                .collect::<Vec<_>>(),
            again
                .dropped_items
                .iter()
                .map(|drop| (drop.unit_id, format!("{:?}", drop.reason)))
                .collect::<Vec<_>>(),
            "drops identical across default runs",
        );
        // Golden of the conservative packer: A (whole body, 10) then B's exact
        // additive chunk charge fill the budget; C overflows.
        assert_eq!(
            reference
                .items
                .iter()
                .map(|item| item.unit_id)
                .collect::<Vec<_>>(),
            vec![UnitId::from_u128(1), UnitId::from_u128(2)],
        );
        assert_eq!(reference.token_estimate, budget);
        assert!(
            reference
                .dropped_items
                .iter()
                .any(|drop| drop.unit_id == UnitId::from_u128(3)
                    && drop.reason == RecallDropReason::Budget),
            "the third item drops for budget: {:?}",
            reference.dropped_items
        );
    }

    /// A unit with a grounded `valid_from`, for the dated-pack tests.
    fn unit_dated(id: u128, body: &str, valid_from: &str) -> StoredMemoryUnit {
        let mut unit = unit(id, body, Vec::new());
        unit.valid_from = Some(valid_from.to_string());
        unit
    }

    /// §6 dated packs: with the flag on, each item whose unit carries a grounded
    /// `valid_from` gets a leading `[date YYYY-MM-DD]` line resolved from it; a
    /// unit without a grounded date is left unprefixed (a date is never invented).
    #[test]
    fn temporal_grounding_prefixes_dated_items_only() {
        let query_tokens = tokenize("filler");
        let candidates = vec![
            candidate(unit_dated(1, &body_of(6), "2023-05-30T00:00:00Z"), 5.0),
            candidate(unit(2, &body_of(6), Vec::new()), 4.0),
        ];
        let packed = pack_recall_context(
            candidates,
            &request(10_000),
            &[],
            &query_tokens,
            Vec::new(),
            2,
            PackLevers::default(),
            true,
        );
        assert_eq!(packed.items.len(), 2);
        assert_eq!(
            packed.items[0].body,
            format!("[date 2023-05-30]\n{}", body_of(6)),
            "grounded item is date-prefixed"
        );
        assert_eq!(
            packed.items[1].body,
            body_of(6),
            "ungrounded item is not prefixed — a date is never invented"
        );
    }

    /// §6 flag-off byte-identity: the same pack with the flag OFF carries no
    /// prefix on any item — bit-identical to today regardless of grounded dates.
    #[test]
    fn temporal_grounding_off_is_byte_identical() {
        let query_tokens = tokenize("filler");
        let candidates = || {
            vec![
                candidate(unit_dated(1, &body_of(6), "2023-05-30T00:00:00Z"), 5.0),
                candidate(unit(2, &body_of(6), Vec::new()), 4.0),
            ]
        };
        let off = pack_recall_context(
            candidates(),
            &request(10_000),
            &[],
            &query_tokens,
            Vec::new(),
            2,
            PackLevers::default(),
            false,
        );
        assert!(
            off.items.iter().all(|item| item.body == body_of(6)),
            "flag off ⇒ no item is date-prefixed: {:?}",
            off.items.iter().map(|i| i.body.clone()).collect::<Vec<_>>()
        );
    }
}

#[cfg(test)]
mod deep_call_routing_tests {
    use super::*;
    use std::pin::Pin;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn compiled_unit(id: u128, body: &str) -> StoredMemoryUnit {
        StoredMemoryUnit {
            capture: None,
            invalidation: None,
            compact: None,
            id: UnitId::from_u128(id),
            tenant_id: TenantId::from_u128(1),
            data_subject_id: SubjectId::from_u128(2),
            scope_id: ScopeId::from_u128(3),
            agent_node_id: AgentNodeId::from_u128(4),
            subject_generation: 5,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: Some(format!("profile:{id}")),
            predicate: Some("states".to_string()),
            body: body.to_string(),
            confidence: Some(1.0),
            trust_level: TrustLevel::TrustedUser,
            churn_class: None,
            freshness_due_at: None,
            actor_id: Some(ActorId::from_u128(6)),
            source_kind: Some("user".to_string()),
            source_ref: format!("test:{id}"),
            observed_at: "2026-07-25T00:00:00Z".to_string(),
            source_episode_id: Some(EpisodeId::from_u128(10)),
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: Some("2026-07-25T00:00:00Z".to_string()),
            valid_to: None,
            transaction_from: Some("2026-07-25T00:00:00Z".to_string()),
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        }
    }

    #[test]
    fn deep_workspace_exposes_current_compiled_units_before_raw_sources() {
        let source_id = Uuid::from_u128(10);
        let workspace = build_deep_workspace(&[DeepSnapshotEntry {
            source_kind: DeepSnapshotSourceKind::Episode,
            source_id,
            path: format!("episodes/{source_id}.md"),
            body: "raw historical evidence".to_string(),
            body_sha256: sha256_bytes_hex(b"raw historical evidence"),
            bound_units: vec![
                compiled_unit(12, "current beta"),
                compiled_unit(11, "current alpha"),
            ],
        }]);

        assert_eq!(workspace.files[2].path, "state/compiled_units.jsonl");
        assert_eq!(workspace.files[3].path, format!("episodes/{source_id}.md"));
        assert_eq!(workspace.files[3].body, "raw historical evidence");
        let rows = workspace.files[2]
            .body
            .lines()
            .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["source_kind"], "episode");
        assert_eq!(rows[0]["source_id"], source_id.to_string());
        assert_eq!(rows[0]["unit_id"], Uuid::from_u128(11).to_string());
        assert_eq!(rows[0]["body"], "current alpha");
        assert_eq!(rows[1]["unit_id"], Uuid::from_u128(12).to_string());
        for private_field in [
            "tenant_id",
            "data_subject_id",
            "scope_id",
            "agent_node_id",
            "actor_id",
            "source_ref",
            "contextual_chunks",
        ] {
            assert!(
                rows[0].get(private_field).is_none(),
                "leaked {private_field}"
            );
        }
    }

    struct PanicProvider {
        identity: DeepProviderIdentity,
    }

    struct RecordingRemoteEmbedding {
        calls: AtomicUsize,
    }

    impl EmbeddingProvider for RecordingRemoteEmbedding {
        fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            Ok(vec![vec![1.0, 0.0, 0.0]; texts.len()])
        }

        fn dimensions(&self) -> usize {
            3
        }

        fn id(&self) -> &str {
            "recording-remote"
        }
    }

    impl DeepRecallProvider for PanicProvider {
        fn identity(&self) -> &DeepProviderIdentity {
            &self.identity
        }

        fn limits(&self) -> DeepRecallLimits {
            DeepRecallLimits {
                wall_time_ms: 1,
                max_tool_iterations: 1,
                max_context_tokens: 1,
                max_spend_micros: 1,
            }
        }

        fn gather<'a>(
            &'a self,
            _: DeepRecallProviderRequest,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<DeepRecallProviderResult, DeepRecallProviderError>>
                    + Send
                    + 'a,
            >,
        > {
            panic!("Fast must not invoke Deep provider")
        }
    }

    #[tokio::test]
    async fn fast_does_not_fetch_deep_snapshot_with_provider_installed() {
        let store = InMemoryStore::default();
        let context = memphant_store_testkit::resolved_context(
            TenantId::from_u128(91_000),
            ScopeId::from_u128(91_001),
            ActorId::from_u128(91_002),
        );
        store.seed_context_binding(&context);
        let provider = PanicProvider {
            identity: DeepProviderIdentity {
                provider: "test".to_string(),
                model: "test/deep".to_string(),
                prompt_hash: "prompt".to_string(),
                config_hash: "config".to_string(),
            },
        };
        for mode in [RecallMode::Fast] {
            recall_with_pool_and_selection_and_deep(
                &store,
                RecallRequest {
                    compact_only: false,
                    context: context.clone(),
                    query: "buried".to_string(),
                    k: 1,
                    budget_tokens: 32,
                    mode,
                    include_beliefs: false,
                    edge_expansion_enabled: false,
                    context_packing_abstention_enabled: true,
                    procedure_recall_enabled: true,
                    decay_enabled: false,
                    engine_version: "test".to_string(),
                    transaction_as_of: None,
                    valid_at: None,
                    aggregation_window: None,
                },
                None,
                &FixedClock("2026-07-20T00:00:00Z"),
                DEFAULT_RECALL_POOL_DEPTH,
                PackLevers::default(),
                LexicalScorer::default(),
                false,
                None,
                CrossRerankCandidateSelection::FusedHead,
                Some(&provider),
            )
            .await
            .unwrap();
        }
        assert_eq!(store.deep_snapshot_read_count(), 0);
    }

    #[tokio::test]
    async fn denied_requests_in_all_modes_perform_no_remote_or_snapshot_work() {
        let store = InMemoryStore::default();
        let mut context = memphant_store_testkit::resolved_context(
            TenantId::from_u128(92_000),
            ScopeId::from_u128(92_001),
            ActorId::from_u128(92_002),
        );
        store.seed_context_binding(&context);
        for sources in context.sources_by_kind.values_mut() {
            sources.clear();
        }
        let embedder = Arc::new(RecordingRemoteEmbedding {
            calls: AtomicUsize::new(0),
        });
        let provider = Arc::new(PanicProvider {
            identity: DeepProviderIdentity {
                provider: "test".to_string(),
                model: "test/deep".to_string(),
                prompt_hash: "prompt".to_string(),
                config_hash: "config".to_string(),
            },
        });
        let service = service::MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-20T00:00:00Z")),
            embedder.clone(),
        )
        .with_deep_recall_provider(provider);

        for mode in [RecallMode::Fast, RecallMode::Deep] {
            let error = service
                .recall_internal(RecallRequest {
                    compact_only: false,
                    context: context.clone(),
                    query: "denied secret query".to_string(),
                    k: 1,
                    budget_tokens: 32,
                    mode,
                    include_beliefs: false,
                    edge_expansion_enabled: false,
                    context_packing_abstention_enabled: true,
                    procedure_recall_enabled: true,
                    decay_enabled: false,
                    engine_version: "test".to_string(),
                    transaction_as_of: None,
                    valid_at: None,
                    aggregation_window: None,
                })
                .await
                .unwrap_err();

            assert!(matches!(
                error,
                service::ServiceError::Core(CoreError::PolicyDenied(_))
            ));
        }
        assert_eq!(embedder.calls.load(Ordering::SeqCst), 0);
        assert_eq!(store.deep_snapshot_read_count(), 0);
        let traces = store.retrieval_traces(TenantId::from_u128(92_000));
        assert_eq!(traces.len(), 2);
        assert!(
            traces
                .iter()
                .all(|trace| trace.channel_runs[0].detail == "denied_scope")
        );
    }
}

/// The two ranking components that were BROKEN until 2026-08-01 and that the
/// Track R coding bank structurally could not see: it is 100% episodic and runs
/// with `MEMPHANT_FACT_EXTRACTION=0`, so both channels were dropped whole by the
/// `score > 0.0` filter, while production runs with extraction default-ON
/// (`40ba26cf`) and a mixed-kind store.
///
/// These began as characterisation tests asserting the WRONG behaviour so they
/// would fail loudly when it was fixed. It is fixed, so they are inverted: they
/// now assert the corrected behaviour and pin the specific defect each one
/// found. See `docs/build-log/2026-08-01-rerank-channel.md` §7.
#[cfg(test)]
mod ranking_channels_fixed_tests {
    use super::*;

    const SCOPE: &str = "550e8400-e29b-41d4-a716-446655440000";

    fn semantic_unit(id: u128, body: &str, fact_key: Option<&str>) -> StoredMemoryUnit {
        StoredMemoryUnit {
            capture: None,
            invalidation: None,
            compact: None,
            id: UnitId::from_u128(id),
            tenant_id: TenantId::from_u128(1),
            data_subject_id: SubjectId::from_u128(1),
            scope_id: ScopeId::from_u128(1),
            agent_node_id: memphant_types::AgentNodeId::from_u128(
                ScopeId::from_u128(1).as_uuid().as_u128(),
            ),
            subject_generation: 0,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: fact_key.map(ToString::to_string),
            predicate: None,
            body: body.to_string(),
            confidence: None,
            trust_level: TrustLevel::TrustedUser,
            churn_class: None,
            freshness_due_at: None,
            actor_id: None,
            source_kind: None,
            source_ref: "test:ranking".to_string(),
            observed_at: "2026-07-15T00:00:00Z".to_string(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        }
    }

    fn now() -> jiff::Timestamp {
        "2026-08-01T00:00:00Z".parse().unwrap()
    }

    // ---- exact_score ----

    /// WAS 0.375, because `tokenize` split the scope UUID into five runs that
    /// landed in the denominator and no query could ever match. The function's
    /// doc calls it "a calibrated 0..1"; now it is one.
    #[test]
    fn full_coverage_of_the_curated_subject_now_scores_one() {
        let unit = semantic_unit(
            1,
            "deploy target is fly.io",
            Some(&format!("{SCOPE}:deploy_target:is")),
        );
        let score = exact_score(&unit, &tokenize("deploy target is"));
        assert!(
            (score - 1.0).abs() < 1e-6,
            "full coverage of `deploy_target:is` should score 1.0, got {score}"
        );
    }

    /// Partial coverage must still be partial — the fix must not simply
    /// saturate the channel. Two of three subject tokens matched.
    #[test]
    fn partial_coverage_is_still_partial() {
        let unit = semantic_unit(
            1,
            "deploy target is fly.io",
            Some(&format!("{SCOPE}:deploy_target:is")),
        );
        let score = exact_score(&unit, &tokenize("deploy target"));
        assert!(
            (score - 2.0 / 3.0).abs() < 1e-6,
            "two of three subject tokens should score 0.667, got {score}"
        );
    }

    /// The scope UUID must not be MATCHABLE either. A query that happens to
    /// contain a UUID run must not earn an Exact vote off the scaffolding.
    #[test]
    fn the_scope_uuid_is_not_matchable() {
        let unit = semantic_unit(
            1,
            "deploy target is fly.io",
            Some(&format!("{SCOPE}:auto:9f2b")),
        );
        assert_eq!(
            exact_score(&unit, &tokenize("550e8400 e29b 41d4")),
            0.0,
            "the scope UUID is scaffolding, not content"
        );
    }

    /// An auto-derived key carries no curated subject, so it legitimately earns
    /// no Exact vote — but for the RIGHT reason now. Its body is scored by the
    /// lexical family; synthesising an Exact vote from the same text would
    /// double-count one signal across two channels.
    #[test]
    fn auto_derived_keys_earn_no_exact_vote_because_they_carry_no_subject() {
        let key = derive_fact_key(
            SCOPE.parse().unwrap(),
            None,
            None,
            "the deploy target is fly.io",
        );
        assert!(
            key.contains(":auto:"),
            "expected the auto branch, got {key}"
        );
        let unit = semantic_unit(1, "the deploy target is fly.io", Some(&key));
        assert_eq!(
            exact_score(&unit, &tokenize("the deploy target is fly.io")),
            0.0
        );
    }

    /// Keys with no scope prefix must be untouched: the extractor emits its own
    /// `candidate.fact_key`, and `timezone:value` / `profile:{id}` are shipped
    /// shapes. Stripping is conditional on a segment actually parsing as a UUID.
    #[test]
    fn unscoped_fact_keys_are_unaffected() {
        let unit = semantic_unit(1, "timezone is CET", Some("timezone:value"));
        assert!((exact_score(&unit, &tokenize("timezone value")) - 1.0).abs() < 1e-6);
        assert!((exact_score(&unit, &tokenize("timezone")) - 0.5).abs() < 1e-6);
    }

    // ---- temporal_score ----

    /// WAS a flat 1.0 for every active semantic unit, with no clock read, so
    /// the channel's tie-break on `body` made its weight-2.5 vote ALPHABETICAL.
    /// Now the newer unit strictly outranks the older one.
    #[test]
    fn the_recency_channel_now_ranks_by_age_not_alphabetically() {
        // `zebra` is newer but sorts LAST alphabetically, so under the old
        // constant-scoring implementation it lost. It must now win.
        let mut newer = semantic_unit(1, "zebra: set recently", None);
        newer.valid_from = Some("2026-07-25T00:00:00Z".to_string());
        let mut older = semantic_unit(2, "apple: set long ago", None);
        older.valid_from = Some("2024-01-01T00:00:00Z".to_string());

        let query = "what is my current setting";
        let new_score = temporal_score(&newer, query, None, now());
        let old_score = temporal_score(&older, query, None, now());
        assert!(
            new_score > old_score,
            "newer {new_score} must outrank older {old_score}"
        );
        assert!(new_score <= 1.0 && old_score > 0.0, "scores stay in (0, 1]");
    }

    /// Strictly positive for any age: `channel_candidates` drops `0.0`, and an
    /// old unit should be uncompetitive on recency, not invisible.
    #[test]
    fn an_ancient_unit_is_uncompetitive_but_not_dropped() {
        let mut ancient = semantic_unit(1, "set in the distant past", None);
        ancient.valid_from = Some("1999-01-01T00:00:00Z".to_string());
        let score = temporal_score(&ancient, "what is current", None, now());
        assert!(
            score > 0.0,
            "must survive the score > 0.0 filter, got {score}"
        );
        assert!(score < 0.02, "but must be uncompetitive, got {score}");
    }

    /// A unit with no usable timestamp must not score as brand new.
    #[test]
    fn an_undated_unit_does_not_score_as_new() {
        let mut undated = semantic_unit(1, "no timestamps at all", None);
        undated.observed_at = "not-a-timestamp".to_string();
        let score = temporal_score(&undated, "what is current", None, now());
        assert_eq!(score, TEMPORAL_UNDATED_SCORE);
        let mut fresh = semantic_unit(2, "fresh", None);
        fresh.valid_from = Some("2026-08-01T00:00:00Z".to_string());
        assert!(temporal_score(&fresh, "what is current", None, now()) > score);
    }

    /// The channel is evaluated at the RECALL clock, so a `transaction_as_of`
    /// replay scores recency reproducibly instead of against wall-clock time.
    #[test]
    fn recency_is_measured_from_the_supplied_clock() {
        let mut unit = semantic_unit(1, "dated", None);
        unit.valid_from = Some("2026-07-01T00:00:00Z".to_string());
        let at_once = temporal_score(
            &unit,
            "current",
            None,
            "2026-07-01T00:00:00Z".parse().unwrap(),
        );
        let a_year_later = temporal_score(
            &unit,
            "current",
            None,
            "2027-07-01T00:00:00Z".parse().unwrap(),
        );
        assert!(
            (at_once - 1.0).abs() < 1e-6,
            "age zero scores 1.0, got {at_once}"
        );
        assert!(
            a_year_later < at_once,
            "the same unit is older at a later clock"
        );
    }

    /// The W5 explicit date-window boost still takes precedence over the
    /// recency curve: a queried period is a stronger statement of intent.
    #[test]
    fn the_explicit_date_window_boost_still_wins() {
        let mut unit = semantic_unit(1, "old but inside the queried window", None);
        unit.valid_from = Some("2024-03-05T00:00:00Z".to_string());
        // Bounds are RFC3339 instants, as `midnight_utc` produces them --
        // `valid_from_in_window` compares parsed timestamps, not date strings.
        let window = DateWindow {
            start: "2024-03-01T00:00:00Z".to_string(),
            end: "2024-04-01T00:00:00Z".to_string(),
        };
        assert_eq!(
            temporal_score(&unit, "what happened in march 2024", Some(&window), now()),
            1.0
        );
    }

    /// Unchanged and load-bearing: both channels stay inert on an episodic
    /// corpus, which is exactly why Track R could never see either defect.
    #[test]
    fn both_components_remain_inert_on_an_episodic_corpus() {
        let mut episodic = semantic_unit(1, "user: what is the current build target", None);
        episodic.kind = MemoryKind::Episodic;
        let query = tokenize("what is the current build target");
        assert_eq!(exact_score(&episodic, &query), 0.0, "no fact_key");
        assert_eq!(
            temporal_score(&episodic, "what is the current build target", None, now()),
            0.0,
            "the recency branch requires MemoryKind::Semantic"
        );
    }
}

/// Guards the `contextual_chunk_multi_window` golden against silent rot.
///
/// That fixture exists to give the deterministic lane its ONLY unit with more
/// than one contextual chunk. But a golden grades units, not rendered text, so
/// it passes whether or not chunk rendering actually fires — if its bodies or
/// budget ever drift into a whole-body fallback, the suite stays green while
/// covering nothing. That is exactly how the lane came to have zero multi-chunk
/// coverage in the first place. This test asserts the fixture's real shape:
/// its chunks are selected as ONE contiguous run at its declared budget, and
/// merging them measurably shrinks the render.
#[cfg(test)]
mod multi_window_fixture_guard {
    use super::*;

    /// Byte-identical to the chunks seeded by
    /// `examples/evals/golden/contextual_chunk_multi_window.yaml`.
    fn fixture_chunks() -> Vec<ContextualChunk> {
        [
            ("1-2", "Stage one moves read traffic to the new payments ledger behind a flag."),
            ("3-4", "The rollback trigger for the payments migration is a sustained authorization error rate above two percent."),
            ("5-6", "On trigger, flip the flag back and drain the write queue before re-pointing reads."),
            ("7-8", "Verification: authorization error rate under one percent for thirty minutes post-rollback."),
        ]
        .iter()
        .map(|(turns, body)| ContextualChunk {
            id: format!("chunk-payments-{turns}"),
            header: format!("[episode payments-mig] [kind user] [turns {turns}]"),
            body: (*body).to_string(),
            source_span: Some("0-70".to_string()),
        })
        .collect()
    }

    #[test]
    fn fixture_is_chunk_rendered_as_one_contiguous_run() {
        let chunks = fixture_chunks();
        let query = tokenize("What is the rollback trigger for the payments migration?");
        // 512 is the fixture's declared `budget_tokens`.
        let selected = select_chunk_mask(&chunks, &query, 512)
            .expect("fixture must chunk-render, not fall back to the whole body");
        assert!(
            selected.iter().all(|picked| *picked),
            "all four windows are selected at the fixture budget: {selected:?}"
        );
        assert_eq!(
            selected_runs(&chunks, &selected, true),
            vec![(0, 3)],
            "the four windows form ONE contiguous mergeable run"
        );

        let unmerged = emit_selected_chunks(&chunks, &selected, false);
        let merged = emit_selected_chunks(&chunks, &selected, true);
        assert!(
            merged.len() < unmerged.len(),
            "merging shrinks the render: {} vs {}",
            merged.len(),
            unmerged.len()
        );
        assert_eq!(
            merged.matches("[episode payments-mig]").count(),
            1,
            "one run-spanning header replaces the four per-window ones: {merged}"
        );
        assert!(
            merged.contains("[turns 1-8]"),
            "the run header spans the whole run: {merged}"
        );
        // The answer span survives the merge — the property the golden asserts
        // at unit granularity and cannot check at chunk granularity.
        assert!(
            merged.contains("sustained authorization error rate above two percent"),
            "the answer-bearing window is still emitted: {merged}"
        );
    }
}
