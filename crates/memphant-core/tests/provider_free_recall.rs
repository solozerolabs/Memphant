use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use memphant_core::deep_recall::{
    DeepRecallProvider, DeepRecallProviderError, DeepRecallProviderRequest,
    DeepRecallProviderResult,
};
use memphant_core::service::MemoryService;
use memphant_core::{
    CrossReranker, CrossRerankerConfig, EmbedError, EmbeddingProvider, FixedClock, InMemoryStore,
    MemoryStore, NoopEmbedding,
};
use memphant_types::{
    ActorId, DeepProviderIdentity, DeepRecallLimits, MemoryKind, NewMemoryUnit, RecallChannel,
    RecallHttpRequest, RecallMode, ResolvedMemoryContext, RetainEpisodeHttpRequest,
    RetainEpisodePayload, RetainPayload, ScopeId, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-08-14T00:00:00Z");

struct CountingEmbedding(Arc<AtomicUsize>);

impl EmbeddingProvider for CountingEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(vec![vec![1.0]; texts.len()])
    }

    fn dimensions(&self) -> usize {
        1
    }

    fn id(&self) -> &str {
        "counting-embedding"
    }
}

struct CountingReranker(Arc<AtomicUsize>);

impl CrossReranker for CountingReranker {
    fn config(&self) -> CrossRerankerConfig {
        CrossRerankerConfig {
            provider: "test".to_string(),
            model: "counting".to_string(),
            candidate_limit: 64,
            max_length: 512,
            batch_size: None,
        }
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(vec![0.0; docs.len()])
    }
}

struct CountingDeep(Arc<AtomicUsize>, DeepProviderIdentity);

impl DeepRecallProvider for CountingDeep {
    fn identity(&self) -> &DeepProviderIdentity {
        &self.1
    }

    fn limits(&self) -> DeepRecallLimits {
        DeepRecallLimits {
            wall_time_ms: 1_000,
            max_tool_iterations: 1,
            max_context_tokens: 1_000,
            max_spend_micros: 1,
        }
    }

    fn gather<'a>(
        &'a self,
        _request: DeepRecallProviderRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<DeepRecallProviderResult, DeepRecallProviderError>>
                + Send
                + 'a,
        >,
    > {
        self.0.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { panic!("provider-free recall invoked the deep provider") })
    }
}

fn request(context: &memphant_types::ResolvedMemoryContext, mode: RecallMode) -> RecallHttpRequest {
    RecallHttpRequest {
        compact_only: false,
        serve_captures: false,
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        query: "provider-free lexical recall".to_string(),
        limit: Some(1),
        budget_tokens: Some(256),
        mode: Some(mode),
        include_beliefs: Some(false),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

#[tokio::test]
async fn ambient_free_recall_clone_keeps_embedder_but_strips_ambient_providers() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);

    let mut tx = store.begin(&context).await.expect("begin");
    store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Procedural,
                state: UnitState::Validated,
                fact_key: None,
                predicate: None,
                body: "provider-free lexical recall remains available".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("test".to_string()),
                source_ref: "test:provider-free".to_string(),
                observed_at: "2026-08-14T00:00:00Z".to_string(),
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
        .expect("stage");
    store.commit(tx).await.expect("commit");

    let embedding_calls = Arc::new(AtomicUsize::new(0));
    let reranker_calls = Arc::new(AtomicUsize::new(0));
    let deep_calls = Arc::new(AtomicUsize::new(0));
    let service = MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(CountingEmbedding(Arc::clone(&embedding_calls))),
    )
    .with_cross_reranker(Arc::new(CountingReranker(Arc::clone(&reranker_calls))))
    .with_deep_recall_provider(Arc::new(CountingDeep(
        Arc::clone(&deep_calls),
        DeepProviderIdentity {
            provider: "test".to_string(),
            model: "counting".to_string(),
            prompt_hash: "prompt".to_string(),
            config_hash: "config".to_string(),
        },
    )));
    let ambient_free = service.ambient_free_recall_clone();

    let response = ambient_free
        .recall(context.clone(), request(&context, RecallMode::Fast))
        .await
        .expect("recall works");
    assert_eq!(response.items.len(), 1);
    assert_eq!(
        response.items[0].body,
        "provider-free lexical recall remains available"
    );

    let _ = ambient_free
        .recall(
            context,
            request(
                &memphant_store_testkit::resolved_context(tenant, scope, actor),
                RecallMode::Deep,
            ),
        )
        .await;
    // The LOCAL embedder is kept — the clone runs the vector channel, so the
    // MCP `recall` tool is semantic, not lexical-only.
    assert!(
        embedding_calls.load(Ordering::SeqCst) > 0,
        "the recall clone keeps the local embedder and embeds the query"
    );
    // The AMBIENT providers stay stripped: no reranker, no deep-recall provider.
    assert_eq!(reranker_calls.load(Ordering::SeqCst), 0);
    assert_eq!(deep_calls.load(Ordering::SeqCst), 0);
}

/// A fixture embedder that maps a "topic" (`zephyr`/`alpha`) to one cluster and
/// everything else to an orthogonal one, so a query and a stored body sharing
/// NO tokens are still embedding-near. The ONLY channel that can surface such a
/// unit is the vector channel — lexical/trigram cannot.
#[derive(Clone, Copy, Default)]
struct TopicEmbedding;

impl EmbeddingProvider for TopicEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(texts
            .iter()
            .map(|text| {
                let lower = text.to_lowercase();
                if lower.contains("zephyr") || lower.contains("alpha") {
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
        "test-topic-embedding"
    }
}

fn topic_retain(context: &ResolvedMemoryContext, body: &str) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "test:topic".to_string(),
        observed_at: "2026-08-14T00:00:00Z".to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
            subject: None,
            predicate: None,
        }),
    }
}

fn semantic_request(context: &ResolvedMemoryContext, query: &str) -> RecallHttpRequest {
    RecallHttpRequest {
        compact_only: false,
        serve_captures: false,
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        query: query.to_string(),
        limit: Some(5),
        budget_tokens: Some(512),
        mode: Some(RecallMode::Fast),
        include_beliefs: Some(true),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// WORK ITEM A: the recall clone the MCP `recall` tool uses must serve the
/// dense vector channel, so the coding agent's own retrieval is semantic — not
/// lexical-only. Perturbation-checked per the golden-non-vacuity rule: remove
/// the embedder and the vector channel goes dark, so the surfacing is provably
/// the vector channel and not lexical overlap.
#[tokio::test]
async fn recall_clone_serves_the_vector_channel_for_lexically_disjoint_queries() {
    let store = InMemoryStore::default();
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(TopicEmbedding),
    );
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    // Body tokens are DISJOINT from the query below, but embedding-near.
    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            topic_retain(&context, "zephyr gamma delta protocol"),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    // The recall clone the MCP tool binds (`memphant-mcp/src/lib.rs`).
    let clone = service.ambient_free_recall_clone();
    let response = clone
        .recall(context.clone(), semantic_request(&context, "alpha beta"))
        .await
        .expect("recall");
    let trace = clone
        .trace(&context, response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    assert!(
        trace
            .candidates
            .iter()
            .any(|candidate| candidate.channel == RecallChannel::Vector),
        "the recall clone must run the vector channel (it is stripped to Noop today)"
    );
    assert!(
        response
            .items
            .iter()
            .any(|item| item.body.contains("zephyr")),
        "a lexically-disjoint, embedding-near query surfaces the unit through the clone"
    );

    // Perturbation: remove the embedder → the vector channel goes dark, proving
    // the surfacing above was the vector channel and not lexical overlap.
    let noop = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
    let noop_response = noop
        .recall(context.clone(), semantic_request(&context, "alpha beta"))
        .await
        .expect("recall");
    let noop_trace = noop
        .trace(&context, noop_response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    assert!(
        noop_trace
            .candidates
            .iter()
            .all(|candidate| candidate.channel != RecallChannel::Vector),
        "perturbation: with the embedder removed the vector channel produces nothing"
    );
}
