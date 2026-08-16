//! Embedding seam contract (Task 6): with a real (stub) provider, compiled
//! units get persisted embeddings and recall runs a genuinely scored `vector`
//! channel; with the Noop provider the channel stays honestly disabled.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{
    EmbedError, EmbeddingProvider, FixedClock, InMemoryStore, MemoryStore, NoopEmbedding,
    StubEmbedding, VECTOR_CANDIDATE_LIMIT, cosine_similarity, embedding_profile_for,
};
use memphant_types::{
    CorrectRequest, CorrectSelector, CorrectionPayload, RecallChannel, RecallHttpRequest,
    RecallTime, ResolvedMemoryContext, RetainEpisodeHttpRequest, TenantId, TrustLevel,
};

const CLOCK: FixedClock = FixedClock("2026-07-09T00:00:00Z");

fn test_recall_time() -> RecallTime {
    RecallTime {
        evaluated_at: CLOCK.0.to_string(),
        transaction_as_of: CLOCK.0.to_string(),
        valid_at: CLOCK.0.to_string(),
    }
}

/// A provider whose `embed_query` deliberately diverges from `embed` (it
/// nudges every component by a small constant, then renormalizes), so the
/// R0-T1 query/document seam is observable: a regression that calls `embed`
/// for the recall-time query (or `embed_query` for index-time documents)
/// produces a measurably different, independently-computable vector-channel
/// score. The nudge keeps the result positively correlated with `embed`'s
/// output (unlike e.g. reversing components, which tends to land near-
/// orthogonal for these sparse hash vectors and gets dropped by the vector
/// channel's `score > 0.0` gate).
#[derive(Clone, Copy, Default)]
struct AsymmetricEmbedding {
    inner: StubEmbedding,
}

impl EmbeddingProvider for AsymmetricEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        self.inner.embed(texts)
    }

    fn embed_query(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        let mut vectors = self.inner.embed(texts)?;
        for vec in &mut vectors {
            for value in vec.iter_mut() {
                *value += 0.05;
            }
            let norm = vec.iter().map(|value| value * value).sum::<f32>().sqrt();
            if norm > 0.0 {
                for value in vec.iter_mut() {
                    *value /= norm;
                }
            }
        }
        Ok(vectors)
    }

    fn dimensions(&self) -> usize {
        self.inner.dimensions()
    }

    fn id(&self) -> &str {
        "test-asymmetric"
    }
}

fn asymmetric_service(store: InMemoryStore) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(AsymmetricEmbedding::default()),
    )
}

fn stub_service(store: InMemoryStore) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    )
}

fn noop_service(store: InMemoryStore) -> MemoryService<InMemoryStore> {
    MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
}

fn retain_request(context: &ResolvedMemoryContext, body: &str) -> RetainEpisodeHttpRequest {
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

fn recall_request(context: &ResolvedMemoryContext, query: &str) -> RecallHttpRequest {
    RecallHttpRequest {
        compact_only: false,
        serve_captures: false,
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        query: query.to_string(),
        limit: None,
        budget_tokens: None,
        mode: None,
        include_beliefs: None,
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

#[tokio::test]
async fn compile_persists_embeddings_under_seeded_profile() {
    let store = InMemoryStore::default();
    let service = stub_service(store.clone());
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    assert!(!page.items.is_empty(), "reflect compiled at least one unit");
    let unit_ids: Vec<_> = page.items.iter().map(|unit| unit.id).collect();
    let rows = store
        .fetch_embeddings(&context, &unit_ids)
        .await
        .expect("embeddings");
    assert!(
        !rows.is_empty(),
        "compiled units have persisted embeddings when a real provider is configured"
    );
    let stub = StubEmbedding::default();
    let profile = embedding_profile_for(&stub);
    assert!(
        rows.iter().all(|row| {
            row.embedding_profile_id == profile.id && row.vec.len() == stub.dimensions()
        }),
        "embeddings are keyed to the provider's deterministic profile"
    );
    // The stored vector matches a fresh embedding of the same body (cosine 1).
    let expected = stub
        .embed(&[page.items[0].body.clone()])
        .expect("stub embed")
        .remove(0);
    let stored = rows
        .iter()
        .find(|row| row.memory_unit_id == page.items[0].id)
        .expect("row for first unit");
    assert!(cosine_similarity(&expected, &stored.vec) > 0.999);
}

#[tokio::test]
async fn vector_channel_scores_candidates_with_real_provider() {
    let store = InMemoryStore::default();
    let service = stub_service(store.clone());
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let response = service
        .recall(
            context.clone(),
            recall_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("recall");
    assert!(!response.items.is_empty(), "recall returns the unit");

    let trace = service
        .trace(&context, response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    let vector_candidates: Vec<_> = trace
        .candidates
        .iter()
        .filter(|candidate| candidate.channel == RecallChannel::Vector)
        .collect();
    assert!(
        !vector_candidates.is_empty(),
        "the vector channel produced scored candidates"
    );
    assert!(
        vector_candidates
            .iter()
            .all(|candidate| candidate.channel_score > 0.0),
        "vector candidates carry real cosine scores"
    );
    let vector_stage = trace
        .channel_runs
        .iter()
        .find(|stage| stage.stage == "vector")
        .expect("vector stage traced");
    assert_eq!(vector_stage.detail, "completed");
    assert!(
        trace.feature_flags.contains(&"vector_enabled".to_string()),
        "feature flags report the vector channel as enabled"
    );
}

#[tokio::test]
async fn in_memory_vector_candidates_return_cosine_distance() {
    // The extended store contract: `fetch_vector_candidates` returns
    // (unit, cosine DISTANCE) under the active profile — the in-memory analogue
    // of pgvector `<=>`, NOT a raw-vector fetch the caller must re-cosine.
    let store = InMemoryStore::default();
    let service = stub_service(store.clone());
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let stub = StubEmbedding::default();
    let profile = embedding_profile_for(&stub);
    let query_vec = stub
        .embed(&["Release region Osaka.".to_string()])
        .expect("embed query")
        .remove(0);

    let pairs = store
        .fetch_vector_candidates(
            &context,
            &query_vec,
            profile.id,
            &test_recall_time(),
            VECTOR_CANDIDATE_LIMIT,
        )
        .await
        .expect("vector candidates");
    assert_eq!(pairs.len(), 1, "the single embedded unit is returned once");
    let (unit, distance) = &pairs[0];

    let rows = store
        .fetch_embeddings(&context, &[unit.id])
        .await
        .expect("embeddings");
    let stored = rows
        .iter()
        .find(|row| row.embedding_profile_id == profile.id)
        .expect("row under the active profile");
    let expected_distance = 1.0 - cosine_similarity(&query_vec, &stored.vec);
    assert!(
        (distance - expected_distance).abs() < 1e-6,
        "store returns cosine distance (1 - similarity); got {distance}, expected {expected_distance}"
    );
    // A vector NEARER the query has a SMALLER distance — direction sanity.
    assert!(*distance >= 0.0 && *distance <= 2.0);
}

#[tokio::test]
async fn vector_channel_score_is_one_minus_store_distance() {
    // The wiring contract: the recall vector channel's traced score is exactly
    // `1 - distance` where `distance` is what the store returned — not an
    // app-side recompute from raw vectors.
    let store = InMemoryStore::default();
    let service = stub_service(store.clone());
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let query = "Release region Osaka.";

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let stub = StubEmbedding::default();
    let profile = embedding_profile_for(&stub);
    let query_vec = stub
        .embed(&[query.to_string()])
        .expect("embed query")
        .remove(0);
    let pairs = store
        .fetch_vector_candidates(
            &context,
            &query_vec,
            profile.id,
            &test_recall_time(),
            VECTOR_CANDIDATE_LIMIT,
        )
        .await
        .expect("vector candidates");
    let unit_id = pairs[0].0.id;
    let distance = pairs[0].1;

    let response = service
        .recall(context.clone(), recall_request(&context, query))
        .await
        .expect("recall");
    let trace = service
        .trace(&context, response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    let vector_candidate = trace
        .candidates
        .iter()
        .find(|candidate| {
            candidate.channel == RecallChannel::Vector && candidate.unit_id == unit_id
        })
        .expect("vector candidate for the embedded unit");
    assert!(
        (vector_candidate.channel_score - (1.0 - distance)).abs() < 1e-6,
        "vector channel score {} must equal 1 - store distance {}",
        vector_candidate.channel_score,
        1.0 - distance
    );
}

#[tokio::test]
async fn noop_provider_keeps_vector_channel_disabled() {
    let store = InMemoryStore::default();
    let service = noop_service(store.clone());
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let unit_ids: Vec<_> = page.items.iter().map(|unit| unit.id).collect();
    let rows = store
        .fetch_embeddings(&context, &unit_ids)
        .await
        .expect("embeddings");
    assert!(rows.is_empty(), "Noop provider persists no embeddings");

    let response = service
        .recall(
            context.clone(),
            recall_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("recall");
    let trace = service
        .trace(&context, response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    assert!(
        trace
            .candidates
            .iter()
            .all(|candidate| candidate.channel != RecallChannel::Vector),
        "no fake vector candidates without a real provider"
    );
    let vector_stage = trace
        .channel_runs
        .iter()
        .find(|stage| stage.stage == "vector")
        .expect("vector stage traced");
    assert_eq!(vector_stage.detail, "disabled");
    assert!(trace.feature_flags.contains(&"vector_disabled".to_string()));
}

/// R0-T1 seam: `service.recall` embeds the query via `embed_query`, while
/// `service.reflect` (index-time) embeds unit bodies via plain `embed`. With
/// `AsymmetricEmbedding`, whose `embed_query` deliberately diverges from
/// `embed`, this is directly observable — a regression that swapped the two
/// calls would flip both assertions below.
#[tokio::test]
async fn recall_embeds_query_via_embed_query_index_time_via_embed() {
    let store = InMemoryStore::default();
    let service = asymmetric_service(store.clone());
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let body = "Release region is Taipei.";
    let provider = AsymmetricEmbedding::default();

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, body),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    // Index-time (reflect): the persisted vector matches `embed(body)`, NOT
    // `embed_query(body)` — documents are never query-prefixed/transformed.
    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let unit_id = page.items[0].id;
    let rows = store
        .fetch_embeddings(&context, &[unit_id])
        .await
        .expect("embeddings");
    let stored = &rows
        .iter()
        .find(|r| r.memory_unit_id == unit_id)
        .expect("row")
        .vec;
    let doc_vec = provider
        .embed(&[body.to_string()])
        .expect("embed")
        .remove(0);
    assert!(
        cosine_similarity(&doc_vec, stored) > 0.999,
        "index-time embedding must use embed(), not embed_query()"
    );

    // Recall-time: the traced vector-channel score for this unit must equal
    // `1 - cosine_distance(embed_query(query), stored)`, computed
    // independently here — proving recall used `embed_query`, not `embed`.
    let response = service
        .recall(context.clone(), recall_request(&context, body))
        .await
        .expect("recall");
    let trace = service
        .trace(&context, response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    let vector_candidate = trace
        .candidates
        .iter()
        .find(|candidate| {
            candidate.channel == RecallChannel::Vector && candidate.unit_id == unit_id
        })
        .expect("vector candidate for the embedded unit");

    let query_vec_via_embed_query = provider
        .embed_query(&[body.to_string()])
        .expect("embed_query")
        .remove(0);
    let expected_score = cosine_similarity(&query_vec_via_embed_query, stored);
    assert!(
        (vector_candidate.channel_score - expected_score).abs() < 1e-6,
        "recall vector channel score {} must match embed_query()-derived score {}",
        vector_candidate.channel_score,
        expected_score
    );

    // Sanity: embed_query really does diverge from embed for this body, or
    // the test would not discriminate a regression that swapped the calls.
    let query_vec_via_embed = provider
        .embed(&[body.to_string()])
        .expect("embed")
        .remove(0);
    assert!(
        cosine_similarity(&query_vec_via_embed_query, &query_vec_via_embed) < 0.999,
        "fixture sanity: embed_query must diverge from embed for the test body"
    );
}

/// A provider with real dimensions whose `embed` always fails, to exercise the
/// embed-before-persist ordering.
#[derive(Clone, Copy, Default)]
struct FailingEmbedding;

impl EmbeddingProvider for FailingEmbedding {
    fn embed(&self, _texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Err(EmbedError::Unavailable("embedder down".to_string()))
    }

    fn dimensions(&self) -> usize {
        32
    }

    fn id(&self) -> &str {
        "test-failing"
    }
}

/// #1 regression: the embedding write-through runs BEFORE the persist
/// transaction, so a provider failure commits NOTHING — no compiled units, no
/// embeddings, and no job-result idempotency marker. Before the fix, persist
/// committed the units + marker and the later embed failed, so a retry
/// short-circuited on the marker and the units stayed permanently unembedded.
#[tokio::test]
async fn failed_embedding_commits_nothing_from_reflect() {
    let store = InMemoryStore::default();
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(FailingEmbedding),
    );
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("retain");
    let tick = service.run_worker_tick(usize::MAX).await.unwrap();
    assert_eq!(tick.completed, 0);
    // The job was released for retry, not completed and not dead-lettered.
    // Asserting `retried` states that directly; the old `== 0` would also have
    // held if the tick had claimed nothing at all.
    assert_eq!(tick.retried, 1, "expected one retry release: {tick:?}");
    assert_eq!(store.pending_job_count(&context).await.unwrap(), 1);

    // Nothing from the compile committed: no units, hence no embeddings and no
    // reflect trace for a retry to short-circuit on.
    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    assert!(
        page.items.is_empty(),
        "a failed embed leaves no compiled units behind (embed runs before persist)"
    );
}

/// #2 regression: a correction embeds its replacement unit in the SAME
/// transaction as the supersedes edge, so corrected truth is immediately
/// vector-visible. Before the fix, `correct` wrote no embedding and enqueued no
/// job, so the corrected unit was absent from the (inner-joined) vector channel.
#[tokio::test]
async fn correction_embeds_the_replacement_unit() {
    let store = InMemoryStore::default();
    let service = stub_service(store.clone());
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, "Release region is Taipei."),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");
    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let old_id = page.items[0].id;

    let corrected = service
        .correct(
            &context,
            "embedding-correction-test",
            CorrectRequest {
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                actor_id: context.actor_id,
                selector: CorrectSelector {
                    memory_unit_id: old_id,
                },
                correction: CorrectionPayload {
                    value: "Release region is Osaka.".to_string(),
                    reason: "corrected_fact".to_string(),
                    source_ref: "test:correction".to_string(),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
                    valid_from: None,
                    valid_to: None,
                },
            },
        )
        .await
        .expect("correct");
    let corrected: memphant_types::CorrectResult =
        serde_json::from_slice(corrected.body()).expect("correct response");
    let new_id = corrected.created[0];

    // The replacement carries a persisted embedding under the active profile.
    let rows = store
        .fetch_embeddings(&context, &[new_id])
        .await
        .expect("embeddings");
    assert!(
        rows.iter().any(|row| row.memory_unit_id == new_id),
        "the corrected unit is embedded, not left invisible to the vector channel"
    );

    // And it is genuinely vector-visible: the corrected body's query returns it.
    let stub = StubEmbedding::default();
    let profile = embedding_profile_for(&stub);
    let query_vec = stub
        .embed(&["Release region is Osaka.".to_string()])
        .expect("embed query")
        .remove(0);
    let pairs = store
        .fetch_vector_candidates(
            &context,
            &query_vec,
            profile.id,
            &test_recall_time(),
            VECTOR_CANDIDATE_LIMIT,
        )
        .await
        .expect("vector candidates");
    assert!(
        pairs.iter().any(|(unit, _)| unit.id == new_id),
        "the corrected unit appears in the vector channel"
    );
}
