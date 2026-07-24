use std::time::{Duration, Instant};

use memphant_core::{FixedClock, InMemoryStore, MemoryStore, recall};
use memphant_types::{
    ENGINE_VERSION, MemoryKind, NewEpisode, NewMemoryUnit, RecallMode, RecallRequest,
    ResolvedMemoryContext, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");

const FAST_P50_LIMIT: Duration = Duration::from_millis(200);
const FAST_P95_LIMIT: Duration = Duration::from_millis(500);

fn tenant(value: u128) -> TenantId {
    TenantId::from_u128(value)
}

#[tokio::test]
async fn fast_mode_recall_holds_release_hot_path_slo() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(86_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    seed_reference_corpus(&store, &context).await;

    let request = RecallRequest {
        context: context.clone(),
        query: "release owner for atlas rollback".to_string(),
        k: 5,
        budget_tokens: 512,
        mode: RecallMode::Fast,
        include_beliefs: false,
        edge_expansion_enabled: false,
        context_packing_abstention_enabled: false,
        procedure_recall_enabled: false,
        decay_enabled: false,
        engine_version: ENGINE_VERSION.to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    };

    for _ in 0..5 {
        recall(&store, request.clone(), None, &CLOCK)
            .await
            .expect("warm recall");
    }

    let mut samples = Vec::with_capacity(80);
    for _ in 0..80 {
        let started = Instant::now();
        let response = recall(&store, request.clone(), None, &CLOCK)
            .await
            .expect("fast recall");
        assert!(!response.items.is_empty());
        samples.push(started.elapsed());
    }

    samples.sort_unstable();
    let p50 = percentile(&samples, 0.50);
    let p95 = percentile(&samples, 0.95);

    assert!(
        p50 < FAST_P50_LIMIT,
        "fast recall p50 {:?} breached {:?}",
        p50,
        FAST_P50_LIMIT
    );
    assert!(
        p95 < FAST_P95_LIMIT,
        "fast recall p95 {:?} breached {:?}",
        p95,
        FAST_P95_LIMIT
    );
}

fn percentile(samples: &[Duration], quantile: f64) -> Duration {
    let index = ((samples.len() as f64 - 1.0) * quantile).ceil() as usize;
    samples[index]
}

async fn seed_reference_corpus(store: &InMemoryStore, context: &ResolvedMemoryContext) {
    let mut tx = store.begin(context).await.expect("begin transaction");
    for index in 0..240 {
        let body = if index == 121 {
            "Atlas rollback release owner is platform on-call; cite runbook RB-77."
        } else {
            "Routine release note for an unrelated service shard."
        };
        let fact_key = if index == 121 {
            "release_owner:atlas_rollback".to_string()
        } else {
            format!("release_note:shard_{index}")
        };
        let episode = store
            .stage_episode(
                &mut tx,
                NewEpisode {
                    tenant_id: context.tenant_id,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    actor_id: context.actor_id,
                    source_kind: "reference-corpus".to_string(),
                    source_ref: "test:fixture".to_string(),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
                    source_trust: TrustLevel::TrustedSystem,
                    dedup_key: format!("hot_path_slo:{index}"),
                    body: body.to_string(),
                },
            )
            .await
            .expect("episode seed");
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
                    fact_key: Some(fact_key),
                    predicate: None,
                    body: body.to_string(),
                    confidence: Some(1.0),
                    trust_level: TrustLevel::TrustedSystem,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(context.actor_id),
                    source_kind: Some("reference-corpus".to_string()),
                    source_ref: "test:fixture".to_string(),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
                    source_episode_id: Some(episode.episode_id),
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
            .expect("unit seed");
    }
    store.commit(tx).await.expect("seed commit");
}
