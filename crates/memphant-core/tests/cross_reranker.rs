//! W8 cross-encoder rerank seam: `with_cross_reranker` reorders the top
//! `recall_pool_depth` fused candidates by `(query, body)` scores AFTER fusion
//! and BEFORE packing. Stub rerankers prove the three contract properties:
//! reordering, prior-rank tie stability, and inert-when-absent/declined.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{
    CrossRerankCandidateSelection, CrossRerankGranularity, CrossReranker, CrossRerankerConfig,
    EmbedError, EmbeddingProvider, FixedClock, InMemoryStore, MemoryStore, StubEmbedding,
};
use memphant_types::{
    ActorId, ContextualChunk, CrossRerankFailure, MemoryKind, NewMemoryUnit, RecallHttpRequest,
    RetainEpisodeHttpRequest, ScopeId, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-09T00:00:00Z");

/// Boosts docs containing `needle` (score 1.0) above the rest (0.0). One score
/// per doc, in input order — the seam contract.
struct BoostReranker {
    needle: String,
}
impl CrossReranker for BoostReranker {
    fn config(&self) -> CrossRerankerConfig {
        test_config(64)
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        Ok(docs
            .iter()
            .map(|doc| if doc.contains(&self.needle) { 1.0 } else { 0.0 })
            .collect())
    }
}

/// Scores every doc identically: a stable sort must leave the order untouched.
struct ConstantReranker;
impl CrossReranker for ConstantReranker {
    fn config(&self) -> CrossRerankerConfig {
        test_config(64)
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        Ok(vec![0.5; docs.len()])
    }
}

/// Declines by returning a wrong-length (empty) vector — the seam's documented
/// no-op signal; the fused order must survive unchanged.
struct DecliningReranker;
impl CrossReranker for DecliningReranker {
    fn config(&self) -> CrossRerankerConfig {
        test_config(64)
    }

    fn rerank(&self, _query: &str, _docs: &[&str]) -> Result<Vec<f32>, String> {
        Ok(Vec::new())
    }
}

/// Sleeps a fixed, small-but-measurable amount before scoring — so the
/// R1.5-T1 `cross_rerank_ms` trace field is provably nonzero without relying
/// on a real model's (variable, possibly sub-millisecond-rounding) timing.
struct SlowReranker;
impl CrossReranker for SlowReranker {
    fn config(&self) -> CrossRerankerConfig {
        test_config(64)
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        std::thread::sleep(std::time::Duration::from_millis(5));
        Ok(vec![0.5; docs.len()])
    }
}

struct ReversePrefixReranker;
impl CrossReranker for ReversePrefixReranker {
    fn config(&self) -> CrossRerankerConfig {
        test_config(2)
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        assert_eq!(docs.len(), 2, "core applies the static candidate limit");
        Ok(vec![0.0, 1.0])
    }
}

struct ErrorReranker;
impl CrossReranker for ErrorReranker {
    fn config(&self) -> CrossRerankerConfig {
        test_config(2)
    }

    fn rerank(&self, _query: &str, _docs: &[&str]) -> Result<Vec<f32>, String> {
        Err("backend unavailable".to_string())
    }
}

/// Scores each doc by the exact `SCORE_*` marker it contains (else 0.0), so a
/// mixed chunk/body pool proves each candidate max-pools ITS OWN docs — any
/// off-by-one in the flattened scatter mapping shifts a marker onto the wrong
/// candidate and flips the resulting order.
struct MarkerScoreReranker;
impl CrossReranker for MarkerScoreReranker {
    fn config(&self) -> CrossRerankerConfig {
        test_config(64)
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        const MARKERS: [(&str, f32); 6] = [
            ("SCORE_ONE", 1.0),
            ("SCORE_TWO", 2.0),
            ("SCORE_THREE", 3.0),
            ("SCORE_FOUR", 4.0),
            ("SCORE_SIX", 6.0),
            ("SCORE_SEVEN", 7.0),
        ];
        Ok(docs
            .iter()
            .map(|doc| {
                MARKERS
                    .iter()
                    .find(|(marker, _)| doc.contains(marker))
                    .map_or(0.0, |(_, score)| *score)
            })
            .collect())
    }
}

/// Makes one otherwise lexical-free document the nearest vector neighbor while
/// every `widget` distractor shares a different vector. This lets the service
/// test prove candidate admission, not merely score ordering.
struct QuotaTestEmbedding;
impl EmbeddingProvider for QuotaTestEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(texts
            .iter()
            .map(|text| {
                if text.contains("semantic candidate") || text == "widget lookup" {
                    vec![1.0, 0.0]
                } else {
                    vec![0.0, 1.0]
                }
            })
            .collect())
    }

    fn dimensions(&self) -> usize {
        2
    }

    fn id(&self) -> &str {
        "quota-test"
    }
}

fn test_config(candidate_limit: usize) -> CrossRerankerConfig {
    CrossRerankerConfig {
        provider: "test".to_string(),
        model: "deterministic".to_string(),
        candidate_limit,
        max_length: 512,
        batch_size: None,
    }
}

fn stub_service(store: InMemoryStore) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    )
}

fn retain_request(
    context: &memphant_types::ResolvedMemoryContext,
    body: &str,
) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "test:fixture".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        payload: memphant_types::RetainPayload::Episode(memphant_types::RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
            subject: None,
            predicate: None,
        }),
    }
}

fn recall_request(
    tenant_id: TenantId,
    scope_id: ScopeId,
    actor_id: ActorId,
    query: &str,
) -> RecallHttpRequest {
    RecallHttpRequest {
        subject_id: memphant_types::SubjectId::from_u128(tenant_id.as_uuid().as_u128()),
        scope_id,
        agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
        subject_generation: 0,
        actor_id,
        query: query.to_string(),
        limit: Some(10),
        budget_tokens: Some(8192),
        mode: None,
        include_beliefs: None,
        // Keep the retired heuristic rerank and decomposition OFF so the test
        // isolates the cross-encoder seam from those (independent) stages.
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// Five units share the query token `widget` (all retrieved via lexical) and
/// each carries a unique marker, so a reranker can target exactly one.
const BODIES: [&str; 5] = [
    "The widget alpha rotates smoothly every morning.",
    "The widget bravo hums a quiet tune at noon.",
    "The widget charlie glows amber under load.",
    "The widget delta clicks twice before resetting.",
    "The widget echo pulses when the queue drains.",
];

async fn seed(store: &InMemoryStore) -> (TenantId, ScopeId, ActorId) {
    let ingest = stub_service(store.clone());
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));
    for (retain_index, body) in BODIES.into_iter().enumerate() {
        ingest
            .retain(
                &memphant_store_testkit::resolved_context(tenant, scope, actor),
                &format!("test:{retain_index}"),
                TrustLevel::TrustedUser,
                retain_request(
                    &memphant_store_testkit::resolved_context(tenant, scope, actor),
                    body,
                ),
            )
            .await
            .expect("retain");
    }
    // Drain to idle, asserting cleanliness: a tick that failed every job also
    // completes zero, so `> 0` exited the same way on total failure as on an
    // empty queue.
    loop {
        let tick = ingest.run_worker_tick(usize::MAX).await.expect("reflect");
        assert!(tick.is_clean(), "reflect drain hit errors: {tick:?}");
        if tick.is_idle() {
            break;
        }
    }
    (tenant, scope, actor)
}

#[tokio::test]
async fn vector_lexical_balance_preserves_a_vector_only_candidate_for_reranking() {
    let store = InMemoryStore::default();
    let ingest = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(QuotaTestEmbedding),
    );
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));
    for index in 0..70 {
        let body = format!("widget distractor number {index} with repeated widget terms");
        ingest
            .retain(
                &memphant_store_testkit::resolved_context(tenant, scope, actor),
                &format!("distractor:{index}"),
                TrustLevel::TrustedUser,
                retain_request(
                    &memphant_store_testkit::resolved_context(tenant, scope, actor),
                    &body,
                ),
            )
            .await
            .expect("retain distractor");
    }
    for index in 0..32 {
        let body = format!("semantic candidate {index} contains hidden material");
        ingest
            .retain(
                &memphant_store_testkit::resolved_context(tenant, scope, actor),
                &format!("semantic:{index}"),
                TrustLevel::TrustedUser,
                retain_request(
                    &memphant_store_testkit::resolved_context(tenant, scope, actor),
                    &body,
                ),
            )
            .await
            .expect("retain semantic candidate");
    }
    let target = "semantic candidate 9 contains hidden material";
    // Drain to idle, asserting cleanliness: a tick that failed every job also
    // completes zero, so `> 0` exited the same way on total failure as on an
    // empty queue.
    loop {
        let tick = ingest.run_worker_tick(usize::MAX).await.expect("reflect");
        assert!(tick.is_clean(), "reflect drain hit errors: {tick:?}");
        if tick.is_idle() {
            break;
        }
    }

    let baseline = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(QuotaTestEmbedding),
    )
    .with_cross_reranker(Arc::new(BoostReranker {
        needle: target.to_string(),
    }))
    .recall(
        memphant_store_testkit::resolved_context(tenant, scope, actor),
        recall_request(tenant, scope, actor, "widget lookup"),
    )
    .await
    .expect("baseline recall");
    assert!(
        baseline.items.iter().all(|item| item.body != target),
        "the fused-head reranker never receives the vector-only target"
    );

    let service = MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(QuotaTestEmbedding),
    )
    .with_cross_rerank_candidate_selection(CrossRerankCandidateSelection::VectorLexicalBalanced)
    .with_cross_reranker(Arc::new(BoostReranker {
        needle: target.to_string(),
    }));
    let bodies = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget lookup"),
        )
        .await
        .expect("recall")
        .items
        .into_iter()
        .map(|item| item.body)
        .collect::<Vec<_>>();

    assert_eq!(bodies.first().map(String::as_str), Some(target));
}

async fn recalled_bodies(
    service: &MemoryService<InMemoryStore>,
    tenant: TenantId,
    scope: ScopeId,
    actor: ActorId,
) -> Vec<String> {
    service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall")
        .items
        .into_iter()
        .map(|item| item.body)
        .collect()
}

/// The cross-encoder reorders: boosting the candidate that fusion ranked LAST
/// lifts it to rank 1, and it was not there without the reranker.
#[tokio::test]
async fn cross_reranker_reorders_candidates() {
    let store = InMemoryStore::default();
    let (tenant, scope, actor) = seed(&store).await;

    let baseline = recalled_bodies(&stub_service(store.clone()), tenant, scope, actor).await;
    assert!(baseline.len() >= 2, "fusion returns several candidates");
    let last = baseline.last().expect("non-empty").clone();
    assert_ne!(baseline[0], last, "the target starts BELOW rank 1");

    // The unique marker word of the fusion-last body (e.g. "echo").
    let needle = last
        .split_whitespace()
        .nth(2)
        .expect("marker token")
        .to_string();

    let reranked = recalled_bodies(
        &stub_service(store.clone()).with_cross_reranker(Arc::new(BoostReranker { needle })),
        tenant,
        scope,
        actor,
    )
    .await;
    assert_eq!(
        reranked[0], last,
        "the boosted candidate is lifted to rank 1 by the cross-encoder"
    );
}

/// No reranker, a declining reranker (wrong-length no-op), and a constant-score
/// reranker (equal scores, stable sort) all produce byte-identical output — the
/// seam is inert unless the cross-encoder expresses a strict preference, and
/// ties fall back to prior fused rank.
#[tokio::test]
async fn absent_or_neutral_reranker_is_byte_identical() {
    let store = InMemoryStore::default();
    let (tenant, scope, actor) = seed(&store).await;

    let baseline = recalled_bodies(&stub_service(store.clone()), tenant, scope, actor).await;

    let declined = recalled_bodies(
        &stub_service(store.clone()).with_cross_reranker(Arc::new(DecliningReranker)),
        tenant,
        scope,
        actor,
    )
    .await;
    assert_eq!(
        declined, baseline,
        "a declining (wrong-length) reranker leaves the fused order unchanged"
    );

    let constant = recalled_bodies(
        &stub_service(store.clone()).with_cross_reranker(Arc::new(ConstantReranker)),
        tenant,
        scope,
        actor,
    )
    .await;
    assert_eq!(
        constant, baseline,
        "equal cross-encoder scores break ties by prior fused rank (stable), \
         so the order is identical to no rerank"
    );
}

/// Determinism: the same reranker over the same corpus yields byte-identical
/// order across repeated recalls.
#[tokio::test]
async fn cross_rerank_is_deterministic() {
    let store = InMemoryStore::default();
    let (tenant, scope, actor) = seed(&store).await;
    let service = stub_service(store.clone()).with_cross_reranker(Arc::new(BoostReranker {
        needle: "charlie".to_string(),
    }));
    let first = recalled_bodies(&service, tenant, scope, actor).await;
    let second = recalled_bodies(&service, tenant, scope, actor).await;
    assert_eq!(first, second, "cross-rerank is order-deterministic");
    assert!(
        first[0].contains("charlie"),
        "the boosted candidate leads: {first:?}"
    );
}

/// R1.5-T1: `RetrievalTrace::cross_rerank_ms` is `0` (and `feature_flags`
/// carries no `cross_rerank_enabled` entry) when no reranker is installed —
/// the flag-off byte-identity case — and is a real, nonzero measurement (with
/// the flag present) when one runs.
#[tokio::test]
async fn cross_rerank_ms_is_recorded_on_the_trace_only_when_a_reranker_runs() {
    let store = InMemoryStore::default();
    let (tenant, scope, actor) = seed(&store).await;

    let absent_service = stub_service(store.clone());
    let absent = absent_service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    let absent_trace = absent_service
        .store()
        .trace_by_id_any_tenant(absent.trace_id)
        .expect("trace exists");
    assert_eq!(
        absent_trace.cross_rerank_ms, 0,
        "no reranker installed ⇒ cross_rerank_ms stays the legitimate 0 (not run)"
    );
    assert!(
        !absent_trace
            .feature_flags
            .iter()
            .any(|flag| flag == "cross_rerank_enabled"),
        "no reranker installed ⇒ no cross_rerank_enabled feature flag: {:?}",
        absent_trace.feature_flags
    );

    let slow_service = stub_service(store.clone()).with_cross_reranker(Arc::new(SlowReranker));
    let present = slow_service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    let present_trace = slow_service
        .store()
        .trace_by_id_any_tenant(present.trace_id)
        .expect("trace exists");
    assert!(
        present_trace.cross_rerank_ms >= 5,
        "the reranker's 5ms sleep must be reflected in the trace: {}",
        present_trace.cross_rerank_ms
    );
    assert!(
        present_trace
            .feature_flags
            .iter()
            .any(|flag| flag == "cross_rerank_enabled"),
        "a reranker installed ⇒ cross_rerank_enabled feature flag present: {:?}",
        present_trace.feature_flags
    );
}

#[tokio::test]
async fn candidate_limit_reorders_only_the_scored_prefix_and_traces_exact_inputs() {
    let store = InMemoryStore::default();
    let (tenant, scope, actor) = seed(&store).await;
    let baseline = recalled_bodies(&stub_service(store.clone()), tenant, scope, actor).await;
    let service = stub_service(store.clone()).with_cross_reranker(Arc::new(ReversePrefixReranker));

    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    let bodies: Vec<_> = response
        .items
        .iter()
        .map(|item| item.body.clone())
        .collect();
    assert_eq!(bodies[0], baseline[1]);
    assert_eq!(bodies[1], baseline[0]);
    assert_eq!(
        &bodies[2..],
        &baseline[2..],
        "unscored fused tail is stable"
    );

    let trace = service
        .store()
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace exists");
    let facts = trace.cross_rerank.expect("rerank facts");
    assert_eq!(facts.provider, "test");
    assert_eq!(facts.model, "deterministic");
    assert_eq!(facts.candidate_limit, 2);
    assert_eq!(facts.candidate_count, 2);
    assert_eq!(facts.max_length, 512);
    assert_eq!(facts.batch_size, None);
    let mut lengths = baseline[..2]
        .iter()
        .map(|body| body.chars().count())
        .collect::<Vec<_>>();
    lengths.sort_unstable();
    assert_eq!(facts.input_chars_p50, lengths[0]);
    assert_eq!(facts.input_chars_p95, lengths[1]);
    assert_eq!(facts.input_chars_max, lengths[1]);
    assert_eq!(facts.failure, CrossRerankFailure::None);
}

#[tokio::test]
async fn reranker_error_fails_open_and_records_the_failure() {
    let store = InMemoryStore::default();
    let (tenant, scope, actor) = seed(&store).await;
    let baseline = recalled_bodies(&stub_service(store.clone()), tenant, scope, actor).await;
    let service = stub_service(store.clone()).with_cross_reranker(Arc::new(ErrorReranker));

    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall fails open");
    let bodies: Vec<_> = response
        .items
        .iter()
        .map(|item| item.body.clone())
        .collect();
    assert_eq!(
        bodies, baseline,
        "rerank failure preserves fused recall order"
    );
    let trace = service
        .store()
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace exists");
    assert_eq!(
        trace.cross_rerank.expect("rerank facts").failure,
        CrossRerankFailure::Error
    );
}

/// Stages one active semantic unit with EXPLICIT contextual chunks (the
/// retain→reflect path windows chunks out of the body, so it cannot mint a
/// chunk carrying text the body lacks — the buried-chunk case under test).
async fn stage_unit_with_chunks(
    store: &InMemoryStore,
    context: &memphant_types::ResolvedMemoryContext,
    body: &str,
    chunks: &[&str],
) {
    let mut tx = store.begin(context).await.expect("begin");
    store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: None,
                predicate: None,
                body: body.to_string(),
                confidence: None,
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: format!("test:{body}"),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: chunks
                    .iter()
                    .enumerate()
                    .map(|(chunk_index, chunk_body)| ContextualChunk {
                        id: format!("{body}:{chunk_index}"),
                        header: "ctx".to_string(),
                        body: (*chunk_body).to_string(),
                        source_span: None,
                    })
                    .collect(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .expect("stage unit");
    store.commit(tx).await.expect("commit");
}

/// Chunk granularity reranks the flattened `contextual_chunks` bodies and
/// max-pools each candidate's own chunk scores: a needle buried in a chunk
/// (absent from the body) lifts its unit, while the same reranker under the
/// default `UnitBody` granularity still ranks by body text alone.
#[tokio::test]
async fn chunk_granularity_max_pools_contextual_chunks() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);
    // A: body WITHOUT the needle, one chunk WITH it. B: body WITH the needle.
    stage_unit_with_chunks(
        &store,
        &context,
        "The widget alpha stays steady",
        &["alpha calm morning report", "alpha carries NEEDLE marker"],
    )
    .await;
    stage_unit_with_chunks(
        &store,
        &context,
        "The widget bravo NEEDLE beacon",
        &["bravo calm evening report"],
    )
    .await;

    let body_service = stub_service(store.clone()).with_cross_reranker(Arc::new(BoostReranker {
        needle: "NEEDLE".to_string(),
    }));
    let body_response = body_service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    assert!(
        body_response.items[0].body.contains("bravo"),
        "UnitBody granularity scores bodies: B's body carries the needle: {:?}",
        body_response.items[0].body
    );
    let body_facts = body_service
        .store()
        .trace_by_id_any_tenant(body_response.trace_id)
        .expect("trace exists")
        .cross_rerank
        .expect("rerank facts");
    assert_eq!(body_facts.granularity, CrossRerankGranularity::UnitBody);
    assert_eq!(
        body_facts.docs_scored, 2,
        "UnitBody feeds one body per candidate"
    );

    let chunk_service = stub_service(store.clone())
        .with_cross_rerank_granularity(CrossRerankGranularity::ContextualChunks)
        .with_cross_reranker(Arc::new(BoostReranker {
            needle: "NEEDLE".to_string(),
        }));
    let chunk_response = chunk_service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    assert!(
        chunk_response.items[0].body.contains("alpha"),
        "ContextualChunks granularity max-pools chunk scores: A's buried-chunk \
         needle outranks B: {:?}",
        chunk_response.items[0].body
    );
    assert!(
        chunk_response.items[1].body.contains("bravo"),
        "B follows A: {:?}",
        chunk_response.items[1].body
    );
    let chunk_facts = chunk_service
        .store()
        .trace_by_id_any_tenant(chunk_response.trace_id)
        .expect("trace exists")
        .cross_rerank
        .expect("rerank facts");
    assert_eq!(
        chunk_facts.granularity,
        CrossRerankGranularity::ContextualChunks
    );
    assert_eq!(
        chunk_facts.docs_scored, 3,
        "docs fed = total chunks across the head (2 for A + 1 for B)"
    );
    assert_eq!(chunk_facts.failure, CrossRerankFailure::None);
}

/// A chunk-less candidate still participates under chunk granularity: its body
/// is fed as the fallback doc, so a body-carried needle lifts it past a
/// lexically stronger chunked candidate.
#[tokio::test]
async fn chunk_granularity_falls_back_to_body_when_no_chunks() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);
    stage_unit_with_chunks(&store, &context, "widget charlie NEEDLE beacon", &[]).await;
    stage_unit_with_chunks(
        &store,
        &context,
        "widget widget delta plain",
        &["delta calm plain report"],
    )
    .await;

    let baseline = recalled_bodies(&stub_service(store.clone()), tenant, scope, actor).await;
    assert!(
        baseline[0].contains("delta"),
        "fixture guard: the fallback target starts BELOW rank 1: {baseline:?}"
    );

    let service = stub_service(store.clone())
        .with_cross_rerank_granularity(CrossRerankGranularity::ContextualChunks)
        .with_cross_reranker(Arc::new(BoostReranker {
            needle: "NEEDLE".to_string(),
        }));
    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    assert!(
        response.items[0].body.contains("charlie"),
        "the chunk-less candidate is scored via its body fallback: {:?}",
        response.items[0].body
    );
    assert!(
        response.items[1].body.contains("delta"),
        "the chunked candidate follows: {:?}",
        response.items[1].body
    );
    let facts = service
        .store()
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace exists")
        .cross_rerank
        .expect("rerank facts");
    assert_eq!(
        facts.docs_scored, 2,
        "one fallback body + one chunk were fed"
    );
    assert_eq!(facts.failure, CrossRerankFailure::None);
}

/// Mixed pool (3-chunk, 0-chunk fallback, 2-chunk): each candidate's score is
/// the max over ITS OWN docs. Correct mapping yields Y(7) > Z(6) > X(4); any
/// off-by-one bleed across the flattened list reassigns a marker and produces
/// a different order (or an out-of-bounds panic).
#[tokio::test]
async fn chunk_granularity_mixed_pool_scatter_mapping() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);
    stage_unit_with_chunks(
        &store,
        &context,
        "widget xray unit",
        &["xray SCORE_ONE", "xray SCORE_FOUR", "xray SCORE_TWO"],
    )
    .await;
    stage_unit_with_chunks(&store, &context, "widget yankee unit SCORE_SEVEN", &[]).await;
    stage_unit_with_chunks(
        &store,
        &context,
        "widget zulu unit",
        &["zulu SCORE_THREE", "zulu SCORE_SIX"],
    )
    .await;

    let service = stub_service(store.clone())
        .with_cross_rerank_granularity(CrossRerankGranularity::ContextualChunks)
        .with_cross_reranker(Arc::new(MarkerScoreReranker));
    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    let bodies: Vec<_> = response
        .items
        .iter()
        .map(|item| item.body.clone())
        .collect();
    assert!(
        bodies[0].contains("yankee"),
        "Y max-pools its body fallback (7): {bodies:?}"
    );
    assert!(
        bodies[1].contains("zulu"),
        "Z max-pools its own chunks (max 3,6 = 6): {bodies:?}"
    );
    assert!(
        bodies[2].contains("xray"),
        "X max-pools its own chunks (max 1,4,2 = 4): {bodies:?}"
    );
    let facts = service
        .store()
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace exists")
        .cross_rerank
        .expect("rerank facts");
    assert_eq!(
        facts.docs_scored, 6,
        "3 chunks + 1 fallback body + 2 chunks"
    );
    assert_eq!(facts.failure, CrossRerankFailure::None);
}

/// Mimics a BERT cross-encoder's hard position wall: only the first
/// `visible_chars` of each doc are inspected for the needle (everything past
/// the wall is truncated away, exactly as `ms-marco-MiniLM-L6`/`bge` do at 512
/// tokens on an over-long chunk). Used to prove the rerank-time sub-chunk cap
/// makes a tail-buried needle reachable.
struct TruncatingBoostReranker {
    needle: String,
    visible_chars: usize,
    max_length: usize,
}
impl CrossReranker for TruncatingBoostReranker {
    fn config(&self) -> CrossRerankerConfig {
        CrossRerankerConfig {
            provider: "test".to_string(),
            model: "truncating".to_string(),
            candidate_limit: 64,
            max_length: self.max_length,
            batch_size: None,
        }
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        Ok(docs
            .iter()
            .map(|doc| {
                let visible: String = doc.chars().take(self.visible_chars).collect();
                if visible.contains(&self.needle) {
                    1.0
                } else {
                    0.0
                }
            })
            .collect())
    }
}

/// The rerank-time sub-chunk cap: a contextual chunk whose body exceeds the
/// model's `max_length` window is split into window-sized sub-chunks before
/// reranking, and the candidate max-pools over ALL its sub-chunks. So a needle
/// buried in the TAIL of an over-long chunk (past a truncating model's wall) is
/// still reached — the fix that makes chunk-granularity actually recover
/// accuracy on prod-sized `contextual_chunks` (measured ~80% > 512 tokens).
#[tokio::test]
async fn oversized_chunks_are_sub_split_so_a_tail_needle_survives_truncation() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);

    // The char budget per fed doc is `max_length * RERANK_CHARS_PER_TOKEN`.
    // With max_length 10 → 35-char windows. A's needle sits in the far tail
    // (past the first window), invisible to a whole-chunk rerank but reachable
    // once the chunk is sub-split. B is a short chunk with no needle.
    let budget = 10 * memphant_core::RERANK_CHARS_PER_TOKEN;
    let long_tail = format!("{}widget NEEDLE tail", "widget filler prose ".repeat(20));
    assert!(long_tail.len() > budget, "test needs an over-budget chunk");
    stage_unit_with_chunks(&store, &context, "unit alpha", &[long_tail.as_str()]).await;
    stage_unit_with_chunks(&store, &context, "unit bravo", &["widget short chunk"]).await;

    // Both bodies here are "unit alpha"/"unit bravo" — the query term `widget`
    // lives ONLY in the contextual chunks, so candidacy comes from the
    // chunk-aware `Semantic` overlap pass. The shipped default scorer
    // (bm25-code since 2026-08-01) scores BODIES only and would produce an
    // empty pool; that gap is pinned in `recall_trace_golden.rs`
    // (`contextual_chunk_recall_finds_source_unit_and_traces_flag`). This test
    // is about the rerank sub-split, so it selects the overlap arm explicitly
    // rather than testing two things at once.
    let service = stub_service(store.clone())
        .with_lexical_scorer(memphant_core::LexicalScorer::Overlap)
        .with_cross_rerank_granularity(CrossRerankGranularity::ContextualChunks)
        .with_cross_reranker(Arc::new(TruncatingBoostReranker {
            needle: "NEEDLE".to_string(),
            visible_chars: budget,
            max_length: 10,
        }));
    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall");
    assert!(
        response.items[0].body.contains("alpha"),
        "A's tail needle survives via sub-split + max-pool: {:?}",
        response.items[0].body
    );
    let facts = service
        .store()
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace exists")
        .cross_rerank
        .expect("rerank facts");
    assert!(
        facts.docs_scored > 2,
        "the long chunk was sub-split into multiple window-sized docs (not 1 per candidate): {}",
        facts.docs_scored
    );
    assert!(
        facts.input_chars_max <= budget,
        "no fed doc exceeds the max_length char budget ({budget}): {}",
        facts.input_chars_max
    );
    assert_eq!(facts.failure, CrossRerankFailure::None);
}

#[tokio::test]
async fn empty_reranker_output_fails_open_and_records_empty() {
    let store = InMemoryStore::default();
    let (tenant, scope, actor) = seed(&store).await;
    let service = stub_service(store).with_cross_reranker(Arc::new(DecliningReranker));
    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "widget"),
        )
        .await
        .expect("recall fails open");
    let trace = service
        .store()
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace exists");
    assert_eq!(
        trace.cross_rerank.expect("rerank facts").failure,
        CrossRerankFailure::Empty
    );
}
