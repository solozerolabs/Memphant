//! Runtime wiring shared by the server, worker, MCP and CLI binaries:
//! `AnyStore` (env-selected store backend behind the non-dyn-safe AFIT
//! `MemoryStore` trait), `MemoryService` construction, and the embedding
//! provider seam. Binaries built with the `fastembed` feature (the shipped
//! server/worker default) embed with local bge-small-en-v1.5 unless
//! `MEMPHANT_EMBEDDINGS=off`; feature-less binaries fall back to Noop.

use std::sync::Arc;

#[cfg(any(feature = "fastembed", test))]
use memphant_core::CrossRerankerConfig;
use memphant_core::service::{
    DEFAULT_STRUCTURED_STATE_PREFETCH_CONCURRENCY, DEFAULT_WORKER_COMPILE_CONCURRENCY,
    MAX_STRUCTURED_STATE_PREFETCH_CONCURRENCY, MAX_WORKER_COMPILE_CONCURRENCY, MemoryService,
};
use memphant_core::{
    ApiKeyRow, CompiledWrite, CorrectOutcome, CorrectionWrite, CrossRerankCandidateSelection,
    CrossRerankGranularity, CrossReranker, DEFAULT_RECALL_POOL_DEPTH, EmbedError,
    EmbeddingProfileRow, EmbeddingProvider, EmbeddingRow, FileSyncTransitionSnapshot,
    ForgetOutcome, ForgetWrite, InMemoryStore, InMemoryTxn, JobFilter, MemoryStore, MutationClaim,
    MutationClaimOutcome, MutationLedgerStore, MutationResponse, NoopEmbedding, ReflectJobRow,
    ResolvedMemoryContext, ReviewEventRow, ScopePage, StoreError, SubjectErasureReceipt,
    SystemClock,
};
use memphant_store_postgres::{
    DEFAULT_DATABASE_MAX_CONNECTIONS, MAX_WORKER_DATABASE_MAX_CONNECTIONS, PgStore, PgTxn,
};
use memphant_types::{
    ActorId, AgentNodeId, ContextBindingRequest, ContextBindingResponse, DeepSnapshotEntry,
    EpisodeId, JobId, MemoryKind, NewEpisode, NewMemoryEdge, NewMemoryUnit, NewResource,
    RecallTime, RecordMaterial, ReflectJob, ReflectTrace, ResourceId, RetainOutcome,
    RetrievalTrace, ScopeId, StoredEpisode, StoredMemoryEdge, StoredMemoryUnit, StoredResource,
    SubjectId, TenantId, TraceId, UnitId,
};
use uuid::Uuid;

pub use memphant_store_postgres::PgStore as Postgres;

/// The env-selected store: `MemoryStore` is AFIT (not object-safe), so the
/// binaries dispatch statically through this enum.
#[derive(Clone)]
pub enum AnyStore {
    Mem(InMemoryStore),
    Pg(PgStore),
}

// ponytail: transient per-transaction dispatch wrapper — one live at a time,
// created by begin() and consumed by commit()/rollback(), never collected.
// Boxing the large variant would add a heap alloc per transaction and deref
// churn across every match arm for no benefit. Box it if it's ever stored in bulk.
#[allow(clippy::large_enum_variant)]
pub enum AnyTxn {
    Mem(InMemoryTxn),
    Pg(PgTxn),
}

impl AnyStore {
    pub fn name(&self) -> &'static str {
        match self {
            Self::Mem(_) => "memory",
            Self::Pg(_) => "postgres",
        }
    }

    pub fn as_pg(&self) -> Option<&PgStore> {
        match self {
            Self::Pg(store) => Some(store),
            Self::Mem(_) => None,
        }
    }
}

/// Builds the server/MCP store from separate tenant-data and authentication
/// credentials. Both must be present together; neither process provisions.
pub async fn build_app_store() -> Result<AnyStore, StoreError> {
    match (
        env_url("MEMPHANT_APP_DATABASE_URL"),
        env_url("MEMPHANT_AUTHN_DATABASE_URL"),
        env_url("DATABASE_URL"),
    ) {
        (Some(url), Some(auth_url), None) => {
            let store = PgStore::connect_app(&url, &auth_url).await?;
            Ok(AnyStore::Pg(store))
        }
        (None, None, None) => {
            eprintln!(
                "memphant: EPHEMERAL in-memory store — set MEMPHANT_APP_DATABASE_URL and MEMPHANT_AUTHN_DATABASE_URL for durability"
            );
            Ok(AnyStore::Mem(InMemoryStore::default()))
        }
        _ => Err(StoreError::Backend(
            "server/MCP database config requires MEMPHANT_APP_DATABASE_URL and MEMPHANT_AUTHN_DATABASE_URL together; DATABASE_URL is not accepted"
                .to_string(),
        )),
    }
}

/// Builds the worker store from its dedicated queue/data credential.
pub async fn build_worker_store() -> Result<AnyStore, StoreError> {
    match (
        env_url("MEMPHANT_WORKER_DATABASE_URL"),
        env_url("DATABASE_URL"),
    ) {
        (Some(url), None) => {
            let max_connections = worker_database_max_connections_from_value(
                std::env::var("MEMPHANT_WORKER_DATABASE_MAX_CONNECTIONS")
                    .ok()
                    .as_deref(),
            )
            .map_err(StoreError::Backend)?;
            let store = PgStore::connect_worker_with_max_connections(
                &url,
                max_connections,
            )
            .await?;
            Ok(AnyStore::Pg(store))
        }
        (None, None) => {
            eprintln!(
                "memphant: EPHEMERAL in-memory worker store — set MEMPHANT_WORKER_DATABASE_URL for durability"
            );
            Ok(AnyStore::Mem(InMemoryStore::default()))
        }
        _ => Err(StoreError::Backend(
            "worker database config requires MEMPHANT_WORKER_DATABASE_URL; DATABASE_URL is not accepted"
                .to_string(),
        )),
    }
}

fn env_url(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
}

fn worker_database_max_connections_from_value(value: Option<&str>) -> Result<u32, String> {
    let Some(value) = value else {
        return Ok(DEFAULT_DATABASE_MAX_CONNECTIONS);
    };
    value
        .trim()
        .parse::<u32>()
        .ok()
        .filter(|value| (1..=MAX_WORKER_DATABASE_MAX_CONNECTIONS).contains(value))
        .ok_or_else(|| {
            format!(
                "must be an integer from 1 through {MAX_WORKER_DATABASE_MAX_CONNECTIONS}, got {value:?}"
            )
        })
}

#[cfg(feature = "fastembed")]
pub mod embeddings;

pub mod api_embeddings;
mod api_reranking;
pub mod deep_recall_openrouter;
mod structured_state_openrouter;

/// Single source of truth mapping an embedder selector id to a provider, shared
/// by the runtime `MEMPHANT_EMBEDDINGS` env var (via [`build_embedder`]) AND
/// the eval bench `--embed-model` flag — so the docs-gate harness (T3) can swap
/// any arm purely by that id, no rebuild. Local (fastembed/qwen3) arms are
/// cargo-feature gated and yield a build-instruction error when absent; the six
/// API arms are always compiled and yield a missing-key error when their env
/// var is unset. Construction is cheap for the API arms (reads the key, builds a
/// pooled agent — no network round-trip).
///
/// Accepted ids:
/// - `off` | `noop` → [`NoopEmbedding`] (vector channel honestly disabled)
/// - `fastembed` → the legacy default local arm (bge-small-en-v1.5)
/// - `small` | `base` | `bge-m3` | `modernbert` | `gemma` → the T1 fastembed arms
/// - `qwen3` → the T1b Qwen3-Embedding-0.6B arm
/// - `voyage-4` | `voyage-4-lite` | `voyage-4-large` | `voyage-code-3`
///   | `voyage-context-4` | `gemini-embedding-001`
///   | `openai-text-embedding-3-small` → the T2 API arms
pub fn embedder_from_id(id: &str) -> Result<Arc<dyn EmbeddingProvider>, String> {
    use api_embeddings::{
        GeminiEmbedding, JinaEmbedding, OpenAiEmbedding, VoyageContextualizedEmbedding,
        VoyageEmbedding, VoyageModel,
    };
    match id {
        "off" | "noop" => Ok(Arc::new(NoopEmbedding)),
        "fastembed" | "small" | "base" | "bge-m3" | "fastembed:bge-m3" | "modernbert" | "gemma" => {
            fastembed_arm(id)
        }
        "qwen3" => qwen3_arm(),
        "voyage-4" => api(VoyageEmbedding::new(VoyageModel::Voyage4)),
        "voyage-4-lite" => api(VoyageEmbedding::new(VoyageModel::Voyage4Lite)),
        "voyage-4-large" => api(VoyageEmbedding::new(VoyageModel::Voyage4Large)),
        "voyage-code-3" => api(VoyageEmbedding::new(VoyageModel::VoyageCode3)),
        "voyage-context-4" => api(VoyageContextualizedEmbedding::new()),
        "gemini-embedding-001" => api(GeminiEmbedding::new()),
        "gemini-embedding-2" => api(GeminiEmbedding::new_v2()),
        "openai-text-embedding-3-small" => api(OpenAiEmbedding::new()),
        "jina-v5-small" => api(JinaEmbedding::new()),
        other => Err(format!(
            "unknown embedder id: {other} (accepted: off, noop, fastembed, small, base, \
             bge-m3, fastembed:bge-m3, modernbert, gemma, qwen3, voyage-4, voyage-4-lite, voyage-4-large, voyage-code-3, \
             voyage-context-4, gemini-embedding-001, gemini-embedding-2, \
             openai-text-embedding-3-small, jina-v5-small)"
        )),
    }
}

/// Wraps an API provider construction (`Result<P, EmbedError>`) into the shared
/// `Result<Arc<dyn EmbeddingProvider>, String>` grammar return.
fn api<P>(provider: Result<P, EmbedError>) -> Result<Arc<dyn EmbeddingProvider>, String>
where
    P: EmbeddingProvider + 'static,
{
    provider
        .map(|provider| Arc::new(provider) as Arc<dyn EmbeddingProvider>)
        .map_err(|error| error.to_string())
}

/// The fastembed local arms (`fastembed`/`small`/`base`/`bge-m3`/`modernbert`/`gemma`),
/// when the feature is compiled in.
#[cfg(feature = "fastembed")]
fn fastembed_arm(id: &str) -> Result<Arc<dyn EmbeddingProvider>, String> {
    let model = match id {
        "fastembed" | "small" => embeddings::FastEmbedModel::BgeSmallEnV15,
        "base" => embeddings::FastEmbedModel::BgeBaseEnV15,
        "bge-m3" | "fastembed:bge-m3" => embeddings::FastEmbedModel::BgeM3,
        "modernbert" => embeddings::FastEmbedModel::ModernBertEmbedLarge,
        "gemma" => embeddings::FastEmbedModel::EmbeddingGemma300M,
        other => unreachable!("fastembed_arm dispatched a non-fastembed id: {other}"),
    };
    embeddings::FastEmbedProvider::with_model(model)
        .map(|provider| Arc::new(provider) as Arc<dyn EmbeddingProvider>)
        .map_err(|error| format!("fastembed initialization failed ({id}): {error}"))
}

#[cfg(not(feature = "fastembed"))]
fn fastembed_arm(id: &str) -> Result<Arc<dyn EmbeddingProvider>, String> {
    Err(format!(
        "embedder '{id}' requires a binary built with --features fastembed"
    ))
}

/// The T1b Qwen3-Embedding-0.6B arm, when the `qwen3` feature is compiled in.
#[cfg(feature = "qwen3")]
fn qwen3_arm() -> Result<Arc<dyn EmbeddingProvider>, String> {
    embeddings::Qwen3Provider::new()
        .map(|provider| Arc::new(provider) as Arc<dyn EmbeddingProvider>)
        .map_err(|error| format!("qwen3 initialization failed: {error}"))
}

#[cfg(not(feature = "qwen3"))]
fn qwen3_arm() -> Result<Arc<dyn EmbeddingProvider>, String> {
    Err(
        "embedder 'qwen3' requires a binary built with --features qwen3 \
         (Qwen3-Embedding-0.6B via fastembed's candle backend)"
            .to_string(),
    )
}

/// Builds the real W8 cross-encoder reranker (`BAAI/bge-reranker-base`, ~1.1
/// GB ONNX download on first use). R1.5-T1's shared runtime factory: BOTH
/// `build_service`'s `MEMPHANT_CROSS_RERANK` env wiring and the eval bench's
/// `--cross-rerank` arm (`memphant-eval::bench_lme`) call this SAME function,
/// so a served recall and a bench recall install byte-identical reranker
/// construction — no separate bench-side factory to drift from the server's.
/// A clear build-instruction error when the `fastembed` feature is absent
/// (the cross-encoder is a fastembed model), mirroring `fastembed_arm`/
/// `qwen3_arm`.
pub fn build_cross_reranker() -> Result<Arc<dyn CrossReranker>, String> {
    let candidate_limit = reranker_candidate_limit_from_value(
        std::env::var("MEMPHANT_RERANK_CANDIDATE_LIMIT")
            .ok()
            .as_deref(),
    )?;
    match std::env::var("MEMPHANT_RERANKER")
        .ok()
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None | Some("fastembed") => build_fastembed_cross_reranker(),
        Some("byo") => build_byo_cross_reranker(),
        Some("voyage-rerank-2.5") => api_reranking::VoyageReranker::new(candidate_limit)
            .map(|reranker| Arc::new(reranker) as Arc<dyn CrossReranker>),
        Some("cohere-rerank-3.5") => api_reranking::CohereReranker::new(candidate_limit)
            .map(|reranker| Arc::new(reranker) as Arc<dyn CrossReranker>),
        Some(value) => Err(format!(
            "MEMPHANT_RERANKER expected fastembed, byo, voyage-rerank-2.5, or cohere-rerank-3.5, got {value:?}"
        )),
    }
}

/// Bring-your-own reranker arm (`MEMPHANT_RERANKER=byo`): loads a local ONNX +
/// tokenizer from `MEMPHANT_RERANK_BYO_DIR` (ONNX file `MEMPHANT_RERANK_BYO_ONNX`,
/// default `model_quantized.onnx`) through fastembed's user-defined path. The
/// seam for a smaller/faster reranker than bge-reranker-base (e.g. ms-marco-
/// MiniLM-L6 int8 — ~13x faster at comparable BEIR; see the reranker latency
/// spike). Same `MEMPHANT_RERANK_CANDIDATE_LIMIT`/`_MAX_LENGTH` env knobs.
#[cfg(feature = "fastembed")]
fn build_byo_cross_reranker() -> Result<Arc<dyn CrossReranker>, String> {
    let dir = std::env::var("MEMPHANT_RERANK_BYO_DIR").map_err(|_| {
        "MEMPHANT_RERANKER=byo requires MEMPHANT_RERANK_BYO_DIR (dir with the ONNX + tokenizer)"
            .to_string()
    })?;
    let onnx_name = std::env::var("MEMPHANT_RERANK_BYO_ONNX")
        .unwrap_or_else(|_| "model_quantized.onnx".to_string());
    let mut config = reranker_config_from_env()?;
    config.provider = "byo".to_string();
    config.model = format!("byo:{onnx_name}");
    embeddings::FastEmbedCrossReranker::from_user_defined(
        std::path::Path::new(&dir),
        &onnx_name,
        config,
    )
    .map(|reranker| Arc::new(reranker) as Arc<dyn CrossReranker>)
    .map_err(|error| format!("byo cross-reranker initialization failed: {error}"))
}

#[cfg(not(feature = "fastembed"))]
fn build_byo_cross_reranker() -> Result<Arc<dyn CrossReranker>, String> {
    Err(
        "MEMPHANT_RERANKER=byo requires a binary built with --features fastembed \
         (the cross-encoder loader is fastembed's user-defined path)"
            .to_string(),
    )
}

#[cfg(feature = "fastembed")]
fn build_fastembed_cross_reranker() -> Result<Arc<dyn CrossReranker>, String> {
    embeddings::FastEmbedCrossReranker::with_config(reranker_config_from_env()?)
        .map(|reranker| Arc::new(reranker) as Arc<dyn CrossReranker>)
        .map_err(|error| format!("cross-reranker initialization failed: {error}"))
}

#[cfg(not(feature = "fastembed"))]
fn build_fastembed_cross_reranker() -> Result<Arc<dyn CrossReranker>, String> {
    Err(
        "cross-reranker requires a binary built with --features fastembed \
         (the cross-encoder is a fastembed model)"
            .to_string(),
    )
}

/// The embedding provider seam, selected by `MEMPHANT_EMBEDDINGS`:
/// - unset/empty (DEFAULT) → local bge-small-en-v1.5 when built with the
///   `fastembed` feature (the shipped server/worker default); a binary built
///   without the feature falls back to `NoopEmbedding` with a loud warning.
/// - any explicit id → routed through [`embedder_from_id`]; a construction
///   failure (feature not compiled, API key unset, unknown id) is a loud panic
///   carrying the grammar's own message. This preserves the documented legacy
///   semantics: `off`/`noop` → Noop, `fastembed` → panic-if-feature-missing,
///   unknown value → panic listing the accepted values.
pub fn build_embedder() -> Arc<dyn EmbeddingProvider> {
    match std::env::var("MEMPHANT_EMBEDDINGS").ok().as_deref() {
        None | Some("") => default_embedder(),
        Some(id) => {
            embedder_from_id(id).unwrap_or_else(|error| panic!("MEMPHANT_EMBEDDINGS={id}: {error}"))
        }
    }
}

/// The DEFAULT (unset) path: local bge-small when the fastembed feature is
/// present, else a graceful `NoopEmbedding` with a loud warning (NOT a panic).
fn default_embedder() -> Arc<dyn EmbeddingProvider> {
    fastembed_or(|| {
        eprintln!(
            "memphant: fastembed feature not compiled in — vector channel DISABLED \
             (build with --features fastembed, or set MEMPHANT_EMBEDDINGS=off to silence)"
        );
        Arc::new(NoopEmbedding)
    })
}

/// Constructs the default fastembed provider (bge-small) when the feature is
/// present; otherwise runs `fallback`. The `fallback` closure is unused in the
/// fastembed build.
#[cfg(feature = "fastembed")]
fn fastembed_or(
    _fallback: impl FnOnce() -> Arc<dyn EmbeddingProvider>,
) -> Arc<dyn EmbeddingProvider> {
    Arc::new(
        embeddings::FastEmbedProvider::new()
            .expect("fastembed model initialization failed (bge-small-en-v1.5)"),
    )
}

#[cfg(not(feature = "fastembed"))]
fn fastembed_or(
    fallback: impl FnOnce() -> Arc<dyn EmbeddingProvider>,
) -> Arc<dyn EmbeddingProvider> {
    fallback()
}

/// Standard `MemoryService` wiring: injected system clock + embedder seam. The
/// R1 docs-domain resource-chunk write path is threaded from
/// `MEMPHANT_RESOURCE_CHUNKS` (default OFF) so BOTH the server and worker
/// binaries honor the gate's `--resource-chunks` lever, mirroring how
/// `MEMPHANT_EMBEDDINGS` reaches both via [`build_embedder`]. R1.5-T0's
/// `MEMPHANT_RECALL_POOL_DEPTH` (default `DEFAULT_RECALL_POOL_DEPTH`, 64) is
/// threaded the same way, so the recall-pool-depth knob reaches both binaries
/// from ONE env var. R1.5-T1's `MEMPHANT_CROSS_RERANK` (default OFF) is the
/// same pattern again for recall-serving processes: only when truthy does this construct the W8
/// cross-encoder reranker (via [`build_cross_reranker`], a real ~1.1 GB model
/// load) and install it with `with_cross_reranker` — unset/off costs nothing,
/// so server/MCP never pay the load unless the flag is on. The worker uses
/// [`build_worker_service`] because it never recalls.
pub fn build_service(store: AnyStore) -> MemoryService<AnyStore> {
    let service = build_base_service(store)
        .with_cross_rerank_candidate_selection(
            cross_rerank_candidate_selection_from_env()
                .unwrap_or_else(|error| panic!("MEMPHANT_CROSS_RERANK_CANDIDATES: {error}")),
        )
        .with_cross_rerank_granularity(
            cross_rerank_granularity_from_env()
                .unwrap_or_else(|error| panic!("MEMPHANT_RERANK_GRANULARITY: {error}")),
        );
    let service = if cross_rerank_enabled_from_env() {
        let reranker = build_cross_reranker().unwrap_or_else(|error| {
            panic!("MEMPHANT_CROSS_RERANK=1: {error}");
        });
        service.with_cross_reranker(reranker)
    } else {
        service
    };
    match deep_recall_openrouter::build_deep_recall_provider()
        .unwrap_or_else(|error| panic!("MEMPHANT_DEEP: {error}"))
    {
        Some(provider) => service.with_deep_recall_provider(provider),
        None => service,
    }
}

/// Workers only compile queued writes; they never recall, so loading a
/// cross-encoder in the worker process wastes memory and startup time.
pub fn build_worker_service(store: AnyStore) -> MemoryService<AnyStore> {
    build_base_service(store)
}

fn build_base_service(store: AnyStore) -> MemoryService<AnyStore> {
    let service = MemoryService::new(Arc::new(store), Arc::new(SystemClock), build_embedder())
        .with_resource_chunks_write_enabled(resource_chunks_write_from_env())
        .with_recall_pool_depth(recall_pool_depth_from_env())
        .with_structured_state_prefetch_concurrency(
            structured_state_prefetch_concurrency_from_value(
                std::env::var("MEMPHANT_STRUCTURED_STATE_CONCURRENCY")
                    .ok()
                    .as_deref(),
            )
            .unwrap_or_else(|error| panic!("MEMPHANT_STRUCTURED_STATE_CONCURRENCY: {error}")),
        )
        .with_worker_compile_concurrency(
            worker_compile_concurrency_from_value(
                std::env::var("MEMPHANT_WORKER_COMPILE_CONCURRENCY")
                    .ok()
                    .as_deref(),
            )
            .unwrap_or_else(|error| panic!("MEMPHANT_WORKER_COMPILE_CONCURRENCY: {error}")),
        );
    match structured_state_openrouter::provider_from_env()
        .unwrap_or_else(|error| panic!("MEMPHANT_STRUCTURED_STATE=on: {error}"))
    {
        Some(provider) => service.with_structured_state_provider(provider),
        None => service,
    }
}

fn structured_state_prefetch_concurrency_from_value(value: Option<&str>) -> Result<usize, String> {
    let Some(value) = value else {
        return Ok(DEFAULT_STRUCTURED_STATE_PREFETCH_CONCURRENCY);
    };
    value
        .trim()
        .parse::<usize>()
        .ok()
        .filter(|value| (1..=MAX_STRUCTURED_STATE_PREFETCH_CONCURRENCY).contains(value))
        .ok_or_else(|| {
            format!(
                "must be an integer from 1 through {MAX_STRUCTURED_STATE_PREFETCH_CONCURRENCY}, got {value:?}"
            )
        })
}

fn worker_compile_concurrency_from_value(value: Option<&str>) -> Result<usize, String> {
    let Some(value) = value else {
        return Ok(DEFAULT_WORKER_COMPILE_CONCURRENCY);
    };
    value
        .trim()
        .parse::<usize>()
        .ok()
        .filter(|value| (1..=MAX_WORKER_COMPILE_CONCURRENCY).contains(value))
        .ok_or_else(|| {
            format!(
                "must be an integer from 1 through {MAX_WORKER_COMPILE_CONCURRENCY}, got {value:?}"
            )
        })
}

/// `MEMPHANT_RESOURCE_CHUNKS` → bool. Truthy (`1`/`true`/`on`, case-insensitive)
/// enables the resource-chunk write path; unset/empty/anything else keeps it OFF
/// (the shipped default), so no env means byte-identical-to-today behavior.
fn resource_chunks_write_from_env() -> bool {
    matches!(
        std::env::var("MEMPHANT_RESOURCE_CHUNKS")
            .ok()
            .as_deref()
            .map(str::trim)
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some("1") | Some("true") | Some("on")
    )
}

/// `MEMPHANT_CROSS_RERANK` → bool. Truthy (`1`/`true`/`on`, case-insensitive)
/// enables the R1.5-T1 W8 cross-encoder rerank seam (the flag [`build_service`]
/// gates [`build_cross_reranker`] construction behind); unset/empty/anything
/// else keeps it OFF (the shipped default — recall stays byte-identical to
/// today, no reranker constructed, no model-load cost). Mirrors
/// `resource_chunks_write_from_env`/`recall_pool_depth_from_env`. Named
/// distinctly from the retired heuristic rerank's request-level
fn cross_rerank_enabled_from_env() -> bool {
    matches!(
        std::env::var("MEMPHANT_CROSS_RERANK")
            .ok()
            .as_deref()
            .map(str::trim)
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some("1") | Some("true") | Some("on")
    )
}

fn cross_rerank_candidate_selection_from_env() -> Result<CrossRerankCandidateSelection, String> {
    cross_rerank_candidate_selection_from_value(
        std::env::var("MEMPHANT_CROSS_RERANK_CANDIDATES")
            .ok()
            .as_deref(),
    )
}

fn cross_rerank_candidate_selection_from_value(
    value: Option<&str>,
) -> Result<CrossRerankCandidateSelection, String> {
    match value.map(str::trim).filter(|value| !value.is_empty()) {
        None | Some("fused-head") => Ok(CrossRerankCandidateSelection::FusedHead),
        Some("vector-lexical-balanced") => Ok(CrossRerankCandidateSelection::VectorLexicalBalanced),
        Some(value) => Err(format!(
            "expected fused-head or vector-lexical-balanced, got {value:?}"
        )),
    }
}

fn cross_rerank_granularity_from_env() -> Result<CrossRerankGranularity, String> {
    cross_rerank_granularity_from_value(
        std::env::var("MEMPHANT_RERANK_GRANULARITY").ok().as_deref(),
    )
}

/// `MEMPHANT_RERANK_GRANULARITY` → W8 cross-rerank doc granularity. `body`
/// (or unset/empty, the shipped default) scores whole unit bodies; `chunk`
/// scores each candidate's flattened `contextual_chunks` bodies and
/// max-pools back per candidate. Mirrors
/// `cross_rerank_candidate_selection_from_value`: explicit and fail-closed
/// on anything else.
fn cross_rerank_granularity_from_value(
    value: Option<&str>,
) -> Result<CrossRerankGranularity, String> {
    match value.map(str::trim).filter(|value| !value.is_empty()) {
        None | Some("body") => Ok(CrossRerankGranularity::UnitBody),
        Some("chunk") => Ok(CrossRerankGranularity::ContextualChunks),
        Some(value) => Err(format!("expected body or chunk, got {value:?}")),
    }
}

#[cfg(feature = "fastembed")]
fn reranker_config_from_env() -> Result<CrossRerankerConfig, String> {
    reranker_config_from_values(
        std::env::var("MEMPHANT_RERANK_CANDIDATE_LIMIT")
            .ok()
            .as_deref(),
        std::env::var("MEMPHANT_RERANK_MAX_LENGTH").ok().as_deref(),
        std::env::var("MEMPHANT_RERANK_BATCH_SIZE").ok().as_deref(),
    )
}

fn reranker_candidate_limit_from_value(value: Option<&str>) -> Result<usize, String> {
    let Some(value) = value else {
        return Ok(DEFAULT_RECALL_POOL_DEPTH);
    };
    value
        .trim()
        .parse::<usize>()
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| {
            format!("MEMPHANT_RERANK_CANDIDATE_LIMIT must be a positive integer, got {value:?}")
        })
}

#[cfg(any(feature = "fastembed", test))]
fn reranker_config_from_values(
    candidate_limit: Option<&str>,
    max_length: Option<&str>,
    batch_size: Option<&str>,
) -> Result<CrossRerankerConfig, String> {
    fn positive(name: &str, value: Option<&str>, default: usize) -> Result<usize, String> {
        let Some(value) = value else {
            return Ok(default);
        };
        value
            .trim()
            .parse::<usize>()
            .ok()
            .filter(|value| *value > 0)
            .ok_or_else(|| format!("{name} must be a positive integer, got {value:?}"))
    }

    Ok(CrossRerankerConfig {
        provider: "fastembed".to_string(),
        model: "fastembed:bge-reranker-base".to_string(),
        candidate_limit: positive(
            "MEMPHANT_RERANK_CANDIDATE_LIMIT",
            candidate_limit,
            DEFAULT_RECALL_POOL_DEPTH,
        )?,
        max_length: positive("MEMPHANT_RERANK_MAX_LENGTH", max_length, 512)?,
        batch_size: Some(positive("MEMPHANT_RERANK_BATCH_SIZE", batch_size, 256)?),
    })
}

/// `MEMPHANT_RECALL_POOL_DEPTH` → `usize`. Unset, empty, or unparseable-as a
/// positive integer falls back to [`DEFAULT_RECALL_POOL_DEPTH`] (64) — the
/// shipped default, so no env means byte-identical-to-the-new-default
/// behavior. A parsed `0` also falls back to the default rather than
/// disabling recall entirely (pool depth is never legitimately 0).
fn recall_pool_depth_from_env() -> usize {
    std::env::var("MEMPHANT_RECALL_POOL_DEPTH")
        .ok()
        .as_deref()
        .map(str::trim)
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|depth| *depth > 0)
        .unwrap_or(DEFAULT_RECALL_POOL_DEPTH)
}

fn txn_mismatch<T>() -> Result<T, StoreError> {
    Err(StoreError::Backend(
        "transaction/store backend mismatch".to_string(),
    ))
}

macro_rules! delegate {
    ($self:ident, $store:ident => $body:expr) => {
        match $self {
            AnyStore::Mem($store) => $body,
            AnyStore::Pg($store) => $body,
        }
    };
}

impl MemoryStore for AnyStore {
    type Txn = AnyTxn;

    async fn begin(&self, context: &ResolvedMemoryContext) -> Result<Self::Txn, StoreError> {
        Ok(match self {
            Self::Mem(store) => AnyTxn::Mem(store.begin(context).await?),
            Self::Pg(store) => AnyTxn::Pg(store.begin(context).await?),
        })
    }

    async fn begin_serializable(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<Self::Txn, StoreError> {
        Ok(match self {
            Self::Mem(store) => AnyTxn::Mem(store.begin_serializable(context).await?),
            Self::Pg(store) => AnyTxn::Pg(store.begin_serializable(context).await?),
        })
    }

    async fn commit(&self, tx: Self::Txn) -> Result<(), StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.commit(tx).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.commit(tx).await,
            _ => txn_mismatch(),
        }
    }

    async fn rollback(&self, tx: Self::Txn) -> Result<(), StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.rollback(tx).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.rollback(tx).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_episode(
        &self,
        tx: &mut Self::Txn,
        episode: NewEpisode,
    ) -> Result<RetainOutcome, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_episode(tx, episode).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_episode(tx, episode).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_memory_unit(
        &self,
        tx: &mut Self::Txn,
        unit: NewMemoryUnit,
    ) -> Result<UnitId, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_memory_unit(tx, unit).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_memory_unit(tx, unit).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_resource(
        &self,
        tx: &mut Self::Txn,
        resource: NewResource,
    ) -> Result<ResourceId, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_resource(tx, resource).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_resource(tx, resource).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_memory_edge(
        &self,
        tx: &mut Self::Txn,
        edge: NewMemoryEdge,
    ) -> Result<memphant_types::EdgeId, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_memory_edge(tx, edge).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_memory_edge(tx, edge).await,
            _ => txn_mismatch(),
        }
    }

    async fn enqueue_reflect(
        &self,
        tx: &mut Self::Txn,
        job: ReflectJob,
    ) -> Result<JobId, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.enqueue_reflect(tx, job).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.enqueue_reflect(tx, job).await,
            _ => txn_mismatch(),
        }
    }

    async fn fetch_recall_candidates(
        &self,
        context: &ResolvedMemoryContext,
        kinds: &[MemoryKind],
        query_terms: &[String],
        time: &RecallTime,
        limit: usize,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        delegate!(self, store => store
            .fetch_recall_candidates(context, kinds, query_terms, time, limit)
            .await)
    }

    async fn fetch_deep_snapshot(
        &self,
        context: &ResolvedMemoryContext,
        time: &RecallTime,
    ) -> Result<Vec<DeepSnapshotEntry>, StoreError> {
        delegate!(self, store => store.fetch_deep_snapshot(context, time).await)
    }

    async fn fetch_scope_open_units(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        delegate!(self, store => store.fetch_scope_open_units(context).await)
    }

    async fn fetch_scope_open_units_in_tx(
        &self,
        tx: &mut Self::Txn,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.fetch_scope_open_units_in_tx(tx).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.fetch_scope_open_units_in_tx(tx).await,
            _ => txn_mismatch(),
        }
    }

    async fn fetch_file_sync_transition_snapshot(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<FileSyncTransitionSnapshot, StoreError> {
        delegate!(self, store => store.fetch_file_sync_transition_snapshot(context).await)
    }

    async fn fetch_file_sync_transition_snapshot_in_tx(
        &self,
        tx: &mut Self::Txn,
    ) -> Result<FileSyncTransitionSnapshot, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => {
                store.fetch_file_sync_transition_snapshot_in_tx(tx).await
            }
            (Self::Pg(store), AnyTxn::Pg(tx)) => {
                store.fetch_file_sync_transition_snapshot_in_tx(tx).await
            }
            _ => txn_mismatch(),
        }
    }

    async fn fetch_vector_candidates(
        &self,
        context: &ResolvedMemoryContext,
        query_vec: &[f32],
        profile_id: Uuid,
        time: &RecallTime,
        limit: usize,
    ) -> Result<Vec<(StoredMemoryUnit, f32)>, StoreError> {
        delegate!(self, store => store
            .fetch_vector_candidates(context, query_vec, profile_id, time, limit)
            .await)
    }

    async fn fetch_units_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        delegate!(self, store => store.fetch_units_by_ids(context, ids).await)
    }

    async fn fetch_edges(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<StoredMemoryEdge>, StoreError> {
        delegate!(self, store => store.fetch_edges(context, unit_ids, time).await)
    }

    async fn fetch_record_material(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<RecordMaterial>, StoreError> {
        delegate!(self, store => store.fetch_record_material(context, ids, time).await)
    }

    async fn fetch_review_events(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<ReviewEventRow>, StoreError> {
        delegate!(self, store => store.fetch_review_events(context, unit_ids, time).await)
    }

    async fn fetch_episodes_for_scope(
        &self,
        context: &ResolvedMemoryContext,
        limit: usize,
    ) -> Result<Vec<StoredEpisode>, StoreError> {
        delegate!(self, store => store.fetch_episodes_for_scope(context, limit).await)
    }

    async fn pending_job_count(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<usize, StoreError> {
        delegate!(self, store => store.pending_job_count(context).await)
    }

    async fn pending_worker_job_count(&self) -> Result<usize, StoreError> {
        delegate!(self, store => store.pending_worker_job_count().await)
    }

    async fn fetch_episode(
        &self,
        context: &ResolvedMemoryContext,
        id: EpisodeId,
    ) -> Result<Option<StoredEpisode>, StoreError> {
        delegate!(self, store => store.fetch_episode(context, id).await)
    }

    async fn fetch_episodes_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[EpisodeId],
    ) -> Result<Vec<StoredEpisode>, StoreError> {
        delegate!(self, store => store.fetch_episodes_by_ids(context, ids).await)
    }

    async fn fetch_resource(
        &self,
        context: &ResolvedMemoryContext,
        id: ResourceId,
    ) -> Result<Option<StoredResource>, StoreError> {
        delegate!(self, store => store.fetch_resource(context, id).await)
    }

    async fn fetch_resources_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[ResourceId],
    ) -> Result<Vec<StoredResource>, StoreError> {
        delegate!(self, store => store.fetch_resources_by_ids(context, ids).await)
    }

    async fn stage_correction(
        &self,
        tx: &mut Self::Txn,
        correction: CorrectionWrite,
    ) -> Result<CorrectOutcome, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_correction(tx, correction).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_correction(tx, correction).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_forget(
        &self,
        tx: &mut Self::Txn,
        forget: ForgetWrite,
    ) -> Result<ForgetOutcome, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_forget(tx, forget).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_forget(tx, forget).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_review_events(
        &self,
        tx: &mut Self::Txn,
        events: Vec<ReviewEventRow>,
    ) -> Result<(), StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_review_events(tx, events).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_review_events(tx, events).await,
            _ => txn_mismatch(),
        }
    }

    async fn store_trace(
        &self,
        context: &ResolvedMemoryContext,
        trace: RetrievalTrace,
    ) -> Result<(), StoreError> {
        delegate!(self, store => store.store_trace(context, trace).await)
    }

    async fn trace_by_id(
        &self,
        context: &ResolvedMemoryContext,
        id: TraceId,
    ) -> Result<Option<RetrievalTrace>, StoreError> {
        delegate!(self, store => store.trace_by_id(context, id).await)
    }

    async fn scope_memory_page(
        &self,
        context: &ResolvedMemoryContext,
        cursor: Option<UnitId>,
        limit: usize,
    ) -> Result<ScopePage, StoreError> {
        delegate!(self, store => store.scope_memory_page(context, cursor, limit).await)
    }

    async fn canonical_projection_units(
        &self,
        context: &ResolvedMemoryContext,
        evaluated_at: &str,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        delegate!(self, store => store.canonical_projection_units(context, evaluated_at).await)
    }

    async fn canonical_projection_units_in_tx(
        &self,
        tx: &mut Self::Txn,
        evaluated_at: &str,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => {
                store
                    .canonical_projection_units_in_tx(tx, evaluated_at)
                    .await
            }
            (Self::Pg(store), AnyTxn::Pg(tx)) => {
                store
                    .canonical_projection_units_in_tx(tx, evaluated_at)
                    .await
            }
            _ => txn_mismatch(),
        }
    }

    async fn claim_reflect_jobs(
        &self,
        filter: JobFilter,
        limit: usize,
    ) -> Result<Vec<ReflectJobRow>, StoreError> {
        delegate!(self, store => store.claim_reflect_jobs(filter, limit).await)
    }

    async fn complete_reflect_job(
        &self,
        claim: &ReflectJobRow,
    ) -> Result<memphant_core::ClaimMutationOutcome, StoreError> {
        delegate!(self, store => store.complete_reflect_job(claim).await)
    }

    async fn fetch_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
    ) -> Result<Option<Vec<memphant_core::ProjectedStructuredState>>, StoreError> {
        delegate!(self, store => store.fetch_prepared_structured_state(claim).await)
    }

    async fn store_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
        projections: Vec<memphant_core::ProjectedStructuredState>,
    ) -> Result<(), StoreError> {
        delegate!(self, store => store.store_prepared_structured_state(claim, projections).await)
    }

    async fn release_reflect_job(
        &self,
        claim: &ReflectJobRow,
        retry_after_seconds: u64,
        error: String,
    ) -> Result<(), StoreError> {
        delegate!(self, store => store.release_reflect_job(claim, retry_after_seconds, error).await)
    }

    async fn fail_reflect_job(
        &self,
        claim: &ReflectJobRow,
        error: String,
    ) -> Result<(), StoreError> {
        delegate!(self, store => store.fail_reflect_job(claim, error).await)
    }

    async fn stage_compiled_units(
        &self,
        tx: &mut Self::Txn,
        claim: Option<&ReflectJobRow>,
        write: CompiledWrite,
    ) -> Result<memphant_core::ClaimMutationOutcome, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => {
                store.stage_compiled_units(tx, claim, write).await
            }
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_compiled_units(tx, claim, write).await,
            _ => txn_mismatch(),
        }
    }

    async fn fetch_reflect_trace(
        &self,
        context: &ResolvedMemoryContext,
        job_id: JobId,
        compiler_version: &str,
    ) -> Result<Option<ReflectTrace>, StoreError> {
        delegate!(self, store => store.fetch_reflect_trace(context, job_id, compiler_version).await)
    }

    async fn upsert_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        rows: Vec<EmbeddingRow>,
    ) -> Result<(), StoreError> {
        delegate!(self, store => store.upsert_embeddings(context, rows).await)
    }

    async fn upsert_embedding_profile(
        &self,
        tenant: TenantId,
        profile: EmbeddingProfileRow,
    ) -> Result<(), StoreError> {
        delegate!(self, store => store.upsert_embedding_profile(tenant, profile).await)
    }

    async fn fetch_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
    ) -> Result<Vec<EmbeddingRow>, StoreError> {
        delegate!(self, store => store.fetch_embeddings(context, unit_ids).await)
    }

    async fn lookup_api_key(&self, key_hash: &str) -> Result<Option<ApiKeyRow>, StoreError> {
        delegate!(self, store => store.lookup_api_key(key_hash).await)
    }

    async fn resolve_context_binding(
        &self,
        tenant: TenantId,
        client_ref: String,
        request: ContextBindingRequest,
    ) -> Result<ContextBindingResponse, StoreError> {
        delegate!(self, store => store.resolve_context_binding(tenant, client_ref, request).await)
    }

    async fn resolve_memory_context(
        &self,
        tenant: TenantId,
        subject_id: SubjectId,
        actor_id: ActorId,
        scope_id: ScopeId,
        agent_node_id: AgentNodeId,
    ) -> Result<ResolvedMemoryContext, StoreError> {
        delegate!(self, store => store.resolve_memory_context(
            tenant,
            subject_id,
            actor_id,
            scope_id,
            agent_node_id
        ).await)
    }

    async fn ping(&self) -> Result<(), StoreError> {
        delegate!(self, store => store.ping().await)
    }

    async fn dead_letter_count(&self) -> Result<u64, StoreError> {
        delegate!(self, store => store.dead_letter_count().await)
    }
}

impl MutationLedgerStore for AnyStore {
    async fn lookup_mutation_replay(
        &self,
        context: &ResolvedMemoryContext,
        claim: &MutationClaim,
    ) -> Result<Option<MutationResponse>, StoreError> {
        delegate!(self, store => store.lookup_mutation_replay(context, claim).await)
    }

    async fn stage_mutation_claim(
        &self,
        tx: &mut Self::Txn,
        claim: MutationClaim,
    ) -> Result<MutationClaimOutcome, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_mutation_claim(tx, claim).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_mutation_claim(tx, claim).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_mutation_response(
        &self,
        tx: &mut Self::Txn,
        response: MutationResponse,
    ) -> Result<(), StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => {
                store.stage_mutation_response(tx, response).await
            }
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_mutation_response(tx, response).await,
            _ => txn_mismatch(),
        }
    }

    async fn stage_subject_erasure(
        &self,
        tx: &mut Self::Txn,
    ) -> Result<SubjectErasureReceipt, StoreError> {
        match (self, tx) {
            (Self::Mem(store), AnyTxn::Mem(tx)) => store.stage_subject_erasure(tx).await,
            (Self::Pg(store), AnyTxn::Pg(tx)) => store.stage_subject_erasure(tx).await,
            _ => txn_mismatch(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        embedder_from_id, structured_state_prefetch_concurrency_from_value,
        worker_compile_concurrency_from_value, worker_database_max_connections_from_value,
    };
    use memphant_core::{EmbedError, EmbeddingProvider, embedding_profile_for};

    #[test]
    fn structured_state_concurrency_is_bounded() {
        assert_eq!(
            structured_state_prefetch_concurrency_from_value(None),
            Ok(4)
        );
        assert_eq!(
            structured_state_prefetch_concurrency_from_value(Some("1")),
            Ok(1)
        );
        assert_eq!(
            structured_state_prefetch_concurrency_from_value(Some("16")),
            Ok(16)
        );
        assert!(structured_state_prefetch_concurrency_from_value(Some("0")).is_err());
        assert!(structured_state_prefetch_concurrency_from_value(Some("17")).is_err());
        assert!(structured_state_prefetch_concurrency_from_value(Some("fast")).is_err());
    }

    #[test]
    fn worker_compile_concurrency_is_separate_and_bounded() {
        assert_eq!(worker_compile_concurrency_from_value(None), Ok(16));
        assert_eq!(worker_compile_concurrency_from_value(Some("1")), Ok(1));
        assert_eq!(worker_compile_concurrency_from_value(Some("128")), Ok(128));
        assert!(worker_compile_concurrency_from_value(Some("0")).is_err());
        assert!(worker_compile_concurrency_from_value(Some("129")).is_err());
        assert!(worker_compile_concurrency_from_value(Some("fast")).is_err());
    }

    #[test]
    fn worker_database_capacity_is_explicit_and_bounded() {
        assert_eq!(worker_database_max_connections_from_value(None), Ok(8));
        assert_eq!(worker_database_max_connections_from_value(Some("1")), Ok(1));
        assert_eq!(
            worker_database_max_connections_from_value(Some("64")),
            Ok(64)
        );
        assert!(worker_database_max_connections_from_value(Some("0")).is_err());
        assert!(worker_database_max_connections_from_value(Some("65")).is_err());
        assert!(worker_database_max_connections_from_value(Some("wide")).is_err());
    }

    #[test]
    fn off_and_noop_construct_the_disabled_noop_provider() {
        // `off` (and the legacy `noop` alias) disable the vector channel for
        // tests/CI without a model load — dims 0 traces the channel disabled.
        for id in ["off", "noop"] {
            let provider = embedder_from_id(id).expect("noop construction");
            assert_eq!(provider.dimensions(), 0, "{id} must be the disabled Noop");
        }
    }

    #[test]
    fn grammar_recognizes_the_network_free_ids() {
        // Recognition = maps to a real branch, never the unknown-id error. Only
        // ids whose construction is network-free are exercised here: `off`/`noop`
        // (Noop) and the seven API arms (which only read a key + build a pooled
        // agent — no round-trip). The local fastembed/qwen3 arms are DELIBERATELY
        // excluded: constructing them downloads model weights, so their
        // recognition is asserted in `local_arm_ids_recognized_without_the_feature`
        // under a feature-off build instead.
        const NETWORK_FREE_IDS: [&str; 9] = [
            "off",
            "noop",
            "voyage-4",
            "voyage-4-lite",
            "voyage-4-large",
            "voyage-code-3",
            "voyage-context-4",
            "gemini-embedding-001",
            "openai-text-embedding-3-small",
        ];
        for id in NETWORK_FREE_IDS {
            if let Err(error) = embedder_from_id(id) {
                // API arms with an unset key error for a RECOGNIZED reason.
                assert!(
                    !error.contains("unknown embedder id"),
                    "id {id} must be recognized by the grammar: {error}"
                );
            }
        }
    }

    /// Without the fastembed feature the local arms construct nothing — they
    /// return a cheap build-instruction error — so recognition is provable here
    /// with zero model downloads. Cfg'd out under `--all-features` (where the
    /// feature is on and constructing them WOULD download weights); the arms are
    /// still structurally explicit match arms in [`embedder_from_id`].
    /// `Arc<dyn EmbeddingProvider>` isn't `Debug`, so `expect_err` (which needs
    /// `T: Debug` to format the Ok case) can't be used — match instead.
    fn expect_grammar_err(id: &str) -> String {
        match embedder_from_id(id) {
            Err(error) => error,
            Ok(_) => panic!("expected an error for id {id}"),
        }
    }

    #[cfg(not(feature = "fastembed"))]
    #[test]
    fn local_arm_ids_recognized_without_the_feature() {
        for id in [
            "fastembed",
            "small",
            "base",
            "bge-m3",
            "fastembed:bge-m3",
            "modernbert",
            "gemma",
            "qwen3",
        ] {
            let error = expect_grammar_err(id);
            assert!(
                !error.contains("unknown embedder id"),
                "id {id} must be recognized by the grammar: {error}"
            );
            assert!(
                error.contains("--features"),
                "recognized-but-uncompiled arm must name the missing feature: {error}"
            );
        }
    }

    #[test]
    fn unknown_id_error_lists_the_accepted_values() {
        let error = expect_grammar_err("word2vec");
        assert!(error.contains("unknown embedder id"), "{error}");
        // A representative from each family must appear in the accepted list.
        for expected in [
            "off",
            "fastembed",
            "qwen3",
            "voyage-context-4",
            "gemini-embedding-001",
            "openai-text-embedding-3-small",
        ] {
            assert!(
                error.contains(expected),
                "accepted list must name {expected}: {error}"
            );
        }
    }

    /// A pure identity stub reporting only `id()`+`dimensions()`, so
    /// `embedding_profile_for` can be exercised without constructing a real
    /// (feature- or key-gated) provider.
    struct IdDims(&'static str, usize);
    impl EmbeddingProvider for IdDims {
        fn embed(&self, _texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
            Ok(Vec::new())
        }
        fn dimensions(&self) -> usize {
            self.1
        }
        fn id(&self) -> &str {
            self.0
        }
    }

    #[test]
    fn every_arm_derives_a_distinct_embedding_profile() {
        // The whole "coexist cleanly" claim, extended over the seven T2 API arms:
        // every arm keys a different profile id (hash of id+dims), so their
        // stored vectors never mix under `<=>` — even where dims coincide
        // (voyage arms + modernbert + qwen3 all 1024), the id disambiguates.
        use crate::api_embeddings::{
            GEMINI_DIMS, GEMINI_ID, OPENAI_DIMS, OPENAI_ID, VOYAGE_CONTEXT_ID, VOYAGE_DIMS,
            VoyageModel,
        };
        let arms = [
            // Seven API arms (id + live-pinned dims).
            IdDims(VoyageModel::Voyage4.id(), VOYAGE_DIMS),
            IdDims(VoyageModel::Voyage4Lite.id(), VOYAGE_DIMS),
            IdDims(VoyageModel::Voyage4Large.id(), VOYAGE_DIMS),
            IdDims(VoyageModel::VoyageCode3.id(), VOYAGE_DIMS),
            IdDims(VOYAGE_CONTEXT_ID, VOYAGE_DIMS),
            IdDims(GEMINI_ID, GEMINI_DIMS),
            IdDims(OPENAI_ID, OPENAI_DIMS),
            // Local arm identities (stable ids from T1/T1b), to prove the API
            // arms never collide with the fastembed/qwen3 arms or Noop.
            IdDims("fastembed:bge-small-en-v1.5", 384),
            IdDims("fastembed:bge-base-en-v1.5", 768),
            IdDims("fastembed:bge-m3", 1024),
            IdDims("fastembed:modernbert-embed-large", 1024),
            IdDims("fastembed:embeddinggemma-300m", 768),
            IdDims("fastembed:qwen3-embedding-0.6b", 1024),
            IdDims("noop", 0),
        ];
        let profiles: Vec<_> = arms
            .iter()
            .map(|arm| embedding_profile_for(arm as &dyn EmbeddingProvider))
            .collect();
        for (left_index, left) in profiles.iter().enumerate() {
            for (right_index, right) in profiles.iter().enumerate() {
                if left_index != right_index {
                    assert_ne!(
                        left.id, right.id,
                        "arms {} and {} must derive distinct profiles",
                        arms[left_index].0, arms[right_index].0
                    );
                }
            }
        }
    }

    /// R1.5-T0: `MEMPHANT_RECALL_POOL_DEPTH` is the runtime-level override for
    /// the ONE recall-pool-depth knob (mirrors `MEMPHANT_RESOURCE_CHUNKS`'s
    /// plumbing pattern). No other test in this binary reads this env var, so
    /// mutating it here is safe against parallel test execution; it is still
    /// restored to its original value before returning.
    #[test]
    fn recall_pool_depth_env_override_parses_and_falls_back_to_default() {
        use super::recall_pool_depth_from_env;

        const VAR: &str = "MEMPHANT_RECALL_POOL_DEPTH";
        let saved = std::env::var(VAR).ok();

        // SAFETY: test-only mutation of a var no other test in this binary
        // reads; restored below before returning.
        unsafe {
            std::env::remove_var(VAR);
        }
        assert_eq!(
            recall_pool_depth_from_env(),
            memphant_core::DEFAULT_RECALL_POOL_DEPTH,
            "unset falls back to the default"
        );

        unsafe {
            std::env::set_var(VAR, "128");
        }
        assert_eq!(
            recall_pool_depth_from_env(),
            128,
            "a valid positive integer is honored"
        );

        unsafe {
            std::env::set_var(VAR, "  96  ");
        }
        assert_eq!(
            recall_pool_depth_from_env(),
            96,
            "surrounding whitespace is trimmed"
        );

        unsafe {
            std::env::set_var(VAR, "0");
        }
        assert_eq!(
            recall_pool_depth_from_env(),
            memphant_core::DEFAULT_RECALL_POOL_DEPTH,
            "0 falls back to the default — pool depth is never legitimately zero"
        );

        unsafe {
            std::env::set_var(VAR, "not-a-number");
        }
        assert_eq!(
            recall_pool_depth_from_env(),
            memphant_core::DEFAULT_RECALL_POOL_DEPTH,
            "unparseable falls back to the default"
        );

        unsafe {
            match &saved {
                Some(value) => std::env::set_var(VAR, value),
                None => std::env::remove_var(VAR),
            }
        }
    }

    /// R1.5-T1: `MEMPHANT_CROSS_RERANK` env plumbing — mirrors
    /// `recall_pool_depth_env_override_parses_and_falls_back_to_default`'s
    /// structure. No other test in this binary reads this var, so mutating it
    /// here is safe against parallel test execution; restored before
    /// returning.
    #[test]
    fn cross_rerank_enabled_from_env_parses_truthy_values_and_defaults_false() {
        use super::cross_rerank_enabled_from_env;

        const VAR: &str = "MEMPHANT_CROSS_RERANK";
        let saved = std::env::var(VAR).ok();

        // SAFETY: test-only mutation of a var no other test in this binary
        // reads; restored below before returning.
        unsafe {
            std::env::remove_var(VAR);
        }
        assert!(
            !cross_rerank_enabled_from_env(),
            "unset defaults to OFF (byte-identical-to-today shipped default)"
        );

        for off_value in ["", "0", "false", "off", "no", "garbage"] {
            unsafe {
                std::env::set_var(VAR, off_value);
            }
            assert!(
                !cross_rerank_enabled_from_env(),
                "{off_value:?} must not enable cross-rerank"
            );
        }

        for truthy in ["1", "true", "on", "TRUE", "On", "  1  "] {
            unsafe {
                std::env::set_var(VAR, truthy);
            }
            assert!(
                cross_rerank_enabled_from_env(),
                "{truthy:?} must enable cross-rerank (truthy, case/whitespace-insensitive)"
            );
        }

        unsafe {
            match &saved {
                Some(value) => std::env::set_var(VAR, value),
                None => std::env::remove_var(VAR),
            }
        }
    }

    #[test]
    fn cross_rerank_candidate_selection_is_explicit_and_fail_closed() {
        use super::cross_rerank_candidate_selection_from_value;
        use memphant_core::CrossRerankCandidateSelection::{FusedHead, VectorLexicalBalanced};

        assert_eq!(
            cross_rerank_candidate_selection_from_value(None),
            Ok(FusedHead)
        );
        assert_eq!(
            cross_rerank_candidate_selection_from_value(Some("fused-head")),
            Ok(FusedHead)
        );
        assert_eq!(
            cross_rerank_candidate_selection_from_value(Some("vector-lexical-balanced")),
            Ok(VectorLexicalBalanced)
        );
        assert!(
            cross_rerank_candidate_selection_from_value(Some("vector-lexical-quota32")).is_err()
        );
        assert!(cross_rerank_candidate_selection_from_value(Some("quota")).is_err());
    }

    #[test]
    fn cross_rerank_granularity_is_explicit_and_fail_closed() {
        use super::cross_rerank_granularity_from_value;
        use memphant_core::CrossRerankGranularity::{ContextualChunks, UnitBody};

        assert_eq!(cross_rerank_granularity_from_value(None), Ok(UnitBody));
        assert_eq!(cross_rerank_granularity_from_value(Some("")), Ok(UnitBody));
        assert_eq!(
            cross_rerank_granularity_from_value(Some("body")),
            Ok(UnitBody)
        );
        assert_eq!(
            cross_rerank_granularity_from_value(Some(" chunk ")),
            Ok(ContextualChunks)
        );
        assert!(cross_rerank_granularity_from_value(Some("chunks")).is_err());
        assert!(cross_rerank_granularity_from_value(Some("unit-body")).is_err());
    }

    #[test]
    fn reranker_runtime_config_uses_safe_defaults_and_positive_integer_overrides() {
        use super::reranker_config_from_values;

        let defaults = reranker_config_from_values(None, None, None).expect("defaults");
        assert_eq!(defaults.candidate_limit, 64);
        assert_eq!(defaults.max_length, 512);
        assert_eq!(defaults.batch_size, Some(256));

        let configured = reranker_config_from_values(Some("32"), Some("1024"), Some("8"))
            .expect("valid overrides");
        assert_eq!(configured.candidate_limit, 32);
        assert_eq!(configured.max_length, 1024);
        assert_eq!(configured.batch_size, Some(8));

        for (candidate, max_length, batch, expected_name) in [
            (Some("0"), None, None, "MEMPHANT_RERANK_CANDIDATE_LIMIT"),
            (None, Some("nope"), None, "MEMPHANT_RERANK_MAX_LENGTH"),
            (None, None, Some("0"), "MEMPHANT_RERANK_BATCH_SIZE"),
        ] {
            let error = reranker_config_from_values(candidate, max_length, batch)
                .expect_err("explicit invalid override must fail");
            assert!(error.contains(expected_name), "{error}");
        }
    }

    #[cfg(feature = "fastembed")]
    #[test]
    fn build_cross_reranker_rejects_invalid_env_before_model_load() {
        const VAR: &str = "MEMPHANT_RERANK_BATCH_SIZE";
        let saved = std::env::var(VAR).ok();
        unsafe {
            std::env::set_var(VAR, "0");
        }
        let error = match super::build_cross_reranker() {
            Err(error) => error,
            Ok(_) => panic!("invalid config must fail before model construction"),
        };
        unsafe {
            match saved {
                Some(value) => std::env::set_var(VAR, value),
                None => std::env::remove_var(VAR),
            }
        }
        assert!(error.contains(VAR), "{error}");
    }

    /// R1.5-T1 feature-off error path: without the `fastembed` feature,
    /// `build_cross_reranker` must fail with a clear, build-instruction error
    /// rather than a confusing panic — mirrors
    /// `local_arm_ids_recognized_without_the_feature` for the embedder arms.
    /// Cfg'd out under `--all-features` (where the feature is on and the real
    /// constructor WOULD attempt a model download).
    #[cfg(not(feature = "fastembed"))]
    #[test]
    fn build_cross_reranker_feature_off_error_path() {
        use super::build_cross_reranker;

        let error = match build_cross_reranker() {
            Err(error) => error,
            Ok(_) => panic!("expected an error without the fastembed feature"),
        };
        assert!(
            error.contains("--features fastembed"),
            "recognized-but-uncompiled reranker must name the missing feature: {error}"
        );
    }
}
