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
    MemoryStore,
};
use memphant_types::{
    ActorId, DeepProviderIdentity, DeepRecallLimits, MemoryKind, NewMemoryUnit, RecallHttpRequest,
    RecallMode, ScopeId, TenantId, TrustLevel, UnitState,
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
async fn provider_free_recall_clone_never_invokes_ambient_providers() {
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
    let provider_free = service.provider_free_recall_clone();

    let response = provider_free
        .recall(context.clone(), request(&context, RecallMode::Fast))
        .await
        .expect("lexical-only recall works");
    assert_eq!(response.items.len(), 1);
    assert_eq!(
        response.items[0].body,
        "provider-free lexical recall remains available"
    );

    let _ = provider_free
        .recall(
            context,
            request(
                &memphant_store_testkit::resolved_context(tenant, scope, actor),
                RecallMode::Deep,
            ),
        )
        .await;
    assert_eq!(embedding_calls.load(Ordering::SeqCst), 0);
    assert_eq!(reranker_calls.load(Ordering::SeqCst), 0);
    assert_eq!(deep_calls.load(Ordering::SeqCst), 0);
}
