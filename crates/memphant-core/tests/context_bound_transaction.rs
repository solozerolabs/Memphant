use std::collections::BTreeMap;

use memphant_core::{InMemoryStore, MemoryStore, StoreError};
use memphant_types::{
    ActorId, AgentNodeId, MemoryEdgeKind, MemoryKind, NewEpisode, NewMemoryEdge, NewMemoryUnit,
    ReflectJob, ReflectJobKind, ResolvedMemoryContext, ResolvedMemorySource, ScopeId, SubjectId,
    TenantId, TrustLevel, UnitState,
};

fn context(seed: u128) -> ResolvedMemoryContext {
    let scope_id = ScopeId::from_u128(seed + 3);
    let agent_node_id = AgentNodeId::from_u128(seed + 4);
    ResolvedMemoryContext {
        tenant_id: TenantId::from_u128(seed),
        data_subject_id: SubjectId::from_u128(seed + 1),
        actor_id: ActorId::from_u128(seed + 2),
        actor_trust: memphant_types::TrustLevel::TrustedUser,
        scope_id,
        agent_node_id,
        agent_level: 0,
        subject_generation: 1,
        policy_revision: "test".to_string(),
        sources_by_kind: MemoryKind::ALL
            .into_iter()
            .map(|kind| {
                (
                    kind,
                    vec![ResolvedMemorySource {
                        scope_id,
                        agent_node_id,
                    }],
                )
            })
            .collect::<BTreeMap<_, _>>(),
    }
}

fn unit(context: &ResolvedMemoryContext, body: &str) -> NewMemoryUnit {
    NewMemoryUnit {
        capture: None,
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
        confidence: Some(1.0),
        trust_level: TrustLevel::TrustedUser,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(context.actor_id),
        source_kind: None,
        source_ref: "test:fixture".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        source_episode_id: None,
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: Vec::new(),
        valid_from: None,
        valid_to: None,
        transaction_from: None,
        transaction_to: None,
    }
}

#[tokio::test]
async fn transaction_rejects_cross_context_payloads_edges_and_jobs() {
    let store = InMemoryStore::default();
    let owner = context(10);
    let mut foreign = context(100);
    foreign.tenant_id = owner.tenant_id;
    store.seed_context_binding(&owner);
    store.seed_context_binding(&foreign);

    let mut foreign_tx = store.begin(&foreign).await.unwrap();
    let foreign_unit = store
        .stage_memory_unit(&mut foreign_tx, unit(&foreign, "foreign unit"))
        .await
        .unwrap();
    store.commit(foreign_tx).await.unwrap();

    let mut tx = store.begin(&owner).await.unwrap();
    let mut wrong_unit = unit(&owner, "wrong subject");
    wrong_unit.data_subject_id = foreign.data_subject_id;
    assert!(matches!(
        store.stage_memory_unit(&mut tx, wrong_unit).await,
        Err(StoreError::Conflict(_))
    ));

    let owner_unit = store
        .stage_memory_unit(&mut tx, unit(&owner, "owner unit"))
        .await
        .unwrap();
    assert!(matches!(
        store
            .stage_memory_edge(
                &mut tx,
                NewMemoryEdge {
                    tenant_id: owner.tenant_id,
                    scope_id: owner.scope_id,
                    src_id: owner_unit,
                    dst_id: foreign_unit,
                    kind: MemoryEdgeKind::DerivedFrom,
                },
            )
            .await,
        Err(StoreError::Conflict(_))
    ));

    let episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: owner.tenant_id,
                data_subject_id: owner.data_subject_id,
                scope_id: owner.scope_id,
                actor_id: owner.actor_id,
                agent_node_id: owner.agent_node_id,
                subject_generation: owner.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "owner".to_string(),
                body: "owner episode".to_string(),
            },
        )
        .await
        .unwrap();
    assert!(matches!(
        store
            .enqueue_reflect(
                &mut tx,
                ReflectJob {
                    tenant_id: owner.tenant_id,
                    data_subject_id: foreign.data_subject_id,
                    scope_id: owner.scope_id,
                    actor_id: owner.actor_id,
                    agent_node_id: owner.agent_node_id,
                    subject_generation: owner.subject_generation,
                    episode_id: Some(episode.episode_id),
                    resource_id: None,
                    kind: ReflectJobKind::ReflectEpisode,
                    compiler_version: "test".to_string(),
                    subject: None,
                    predicate: None,
                },
            )
            .await,
        Err(StoreError::Conflict(_))
    ));
}
