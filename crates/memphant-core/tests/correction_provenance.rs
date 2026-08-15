use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, MemoryStore, NoopEmbedding, correction_rectangles};
use memphant_types::{
    ActorId, AgentNodeId, CorrectRequest, CorrectSelector, CorrectionPayload, MemoryKind,
    NewMemoryUnit, ScopeId, StoredMemoryUnit, SubjectId, TenantId, TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

fn old_unit() -> StoredMemoryUnit {
    StoredMemoryUnit {
        invalidation: None,
        compact: None,
        id: UnitId::new(),
        tenant_id: TenantId::new(),
        data_subject_id: SubjectId::new(),
        scope_id: ScopeId::new(),
        agent_node_id: AgentNodeId::new(),
        subject_generation: 0,
        kind: MemoryKind::Semantic,
        state: UnitState::Active,
        fact_key: Some("profile:city".to_string()),
        predicate: Some("lives_in".to_string()),
        body: "Lives in Oslo".to_string(),
        confidence: Some(0.8),
        trust_level: TrustLevel::TrustedUser,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(ActorId::new()),
        source_kind: Some("episode".to_string()),
        source_ref: "syndai:episode:old".to_string(),
        observed_at: "2026-01-01T00:00:00Z".to_string(),
        source_episode_id: Some(memphant_types::EpisodeId::new()),
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: vec![],
        valid_from: Some("2026-01-01T00:00:00Z".to_string()),
        valid_to: Some("2027-01-01T00:00:00Z".to_string()),
        transaction_from: Some("2026-01-01T00:00:00Z".to_string()),
        transaction_to: None,
        difficulty: None,
        stability_days: None,
        last_reinforced_at: None,
        reinforcement_count: 0,
    }
}

#[tokio::test]
async fn correction_boundary_canonicalizes_utc_and_rejects_blank_source() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let mut tx = store.begin(&context).await.expect("begin");
    let unit_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("profile:city".to_string()),
                predicate: None,
                body: "Lives in Oslo".to_string(),
                confidence: Some(0.8),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("episode".to_string()),
                source_ref: "syndai:episode:old".to_string(),
                observed_at: "2029-01-01T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: vec![],
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .expect("stage unit");
    store.commit(tx).await.expect("commit");
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let request = CorrectRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        selector: CorrectSelector {
            memory_unit_id: unit_id,
        },
        correction: CorrectionPayload {
            value: "Lives in Lima".to_string(),
            reason: "user correction".to_string(),
            source_ref: "syndai:correction:1".to_string(),
            observed_at: "2030-01-01T00:00:00+00:00".to_string(),
            valid_from: None,
            valid_to: None,
        },
    };
    service
        .correct(&context, "correction-1", request.clone())
        .await
        .expect("correct");
    let replacement = store
        .memory_units(tenant)
        .into_iter()
        .find(|unit| unit.source_ref == "syndai:correction:1")
        .expect("replacement");
    assert_eq!(replacement.observed_at, "2030-01-01T00:00:00Z");

    let mut blank = request;
    blank.correction.source_ref = " ".to_string();
    assert!(
        service
            .correct(&context, "correction-2", blank)
            .await
            .is_err()
    );
}

#[test]
fn replacement_gets_correction_provenance_but_remainders_keep_source() {
    let old = old_unit();
    let payload = CorrectionPayload {
        value: "Lives in Lima".to_string(),
        reason: "user correction".to_string(),
        source_ref: "syndai:correction:1".to_string(),
        observed_at: "2026-06-01T00:00:00Z".to_string(),
        valid_from: Some("2026-05-01T00:00:00Z".to_string()),
        valid_to: Some("2026-08-01T00:00:00Z".to_string()),
    };

    let (replacement, remainders) = correction_rectangles(
        &old,
        &payload,
        &payload.source_ref,
        &payload.observed_at,
        ActorId::new(),
        "2026-06-01T00:00:00Z",
    )
    .expect("correction rectangles");

    assert_eq!(replacement.source_ref, payload.source_ref);
    assert_eq!(replacement.predicate, old.predicate);
    assert_eq!(replacement.observed_at, payload.observed_at);
    assert_eq!(replacement.source_kind.as_deref(), Some("correction"));
    assert!(replacement.source_episode_id.is_none());
    assert!(replacement.source_resource_id.is_none());
    assert_eq!(remainders.len(), 2);
    assert!(remainders.iter().all(|unit| {
        unit.source_ref == old.source_ref
            && unit.predicate == old.predicate
            && unit.observed_at == old.observed_at
            && unit.source_episode_id == old.source_episode_id
    }));
}
