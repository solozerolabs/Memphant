use memphant_core::{FixedClock, InMemoryStore, MemoryStore, recall};
use memphant_types::{
    ActorId, AgentNodeId, MarkOutcome, MemoryKind, NewMemoryUnit, RecallMode, RecallRequest,
    ResolvedMemoryContext, ResolvedMemorySource, ReviewEvent, ScopeId, SubjectId, TenantId,
    TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

fn context(
    tenant_id: TenantId,
    subject_id: SubjectId,
    scope_id: ScopeId,
    actor_id: ActorId,
    agent_node_id: AgentNodeId,
) -> ResolvedMemoryContext {
    ResolvedMemoryContext {
        tenant_id,
        data_subject_id: subject_id,
        actor_id,
        actor_trust: memphant_types::TrustLevel::TrustedUser,
        scope_id,
        agent_node_id,
        agent_level: 0,
        subject_generation: 0,
        policy_revision: "test-policy".to_string(),
        sources_by_kind: [(
            MemoryKind::Semantic,
            vec![ResolvedMemorySource {
                scope_id,
                agent_node_id,
            }],
        )]
        .into_iter()
        .collect(),
    }
}

async fn stage_unit(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    fact_key: &str,
    body: &str,
) -> UnitId {
    store.seed_context_binding(context);
    let mut tx = store.begin(context).await.unwrap();
    let id = store
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
                fact_key: Some(fact_key.to_string()),
                predicate: None,
                body: body.to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("user".to_string()),
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
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    id
}

fn recall_request(context: ResolvedMemoryContext, query: &str) -> RecallRequest {
    RecallRequest {
        context,
        query: query.to_string(),
        k: 8,
        budget_tokens: 256,
        mode: RecallMode::Fast,
        include_beliefs: false,
        edge_expansion_enabled: false,
        context_packing_abstention_enabled: false,
        procedure_recall_enabled: false,
        decay_enabled: false,
        engine_version: "context-auth-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

#[tokio::test]
async fn same_scope_sibling_agents_cannot_list_or_compile_against_each_others_units() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let subject = SubjectId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let left = context(tenant, subject, scope, actor, AgentNodeId::new());
    let right = context(tenant, subject, scope, actor, AgentNodeId::new());
    let left_id = stage_unit(&store, &left, "same-fact", "left sibling value").await;
    let right_id = stage_unit(&store, &right, "same-fact", "right sibling value").await;

    let mut left_with_grant = left.clone();
    left_with_grant
        .sources_by_kind
        .get_mut(&MemoryKind::Semantic)
        .unwrap()
        .push(ResolvedMemorySource {
            scope_id: scope,
            agent_node_id: right.agent_node_id,
        });
    let listed = store
        .scope_memory_page(&left_with_grant, None, 100)
        .await
        .unwrap();
    assert_eq!(
        listed.items.iter().map(|unit| unit.id).collect::<Vec<_>>(),
        vec![left_id]
    );

    let open = store.fetch_scope_open_units(&left).await.unwrap();
    assert_eq!(
        open.iter().map(|unit| unit.id).collect::<Vec<_>>(),
        vec![left_id]
    );
    assert!(!open.iter().any(|unit| unit.id == right_id));
}

#[tokio::test]
async fn review_reads_keep_authorized_overlap_without_returning_other_unit_ids() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let owner = context(
        tenant,
        SubjectId::new(),
        ScopeId::new(),
        ActorId::new(),
        AgentNodeId::new(),
    );
    let first = stage_unit(&store, &owner, "first", "first reviewed memory").await;
    let second = stage_unit(&store, &owner, "second", "second reviewed memory").await;
    let recalled = recall(
        &store,
        recall_request(owner.clone(), "reviewed memory"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    store
        .record_review_events(
            &owner,
            vec![ReviewEvent {
                tenant_id: tenant,
                trace_id: recalled.trace_id,
                caller_id: "overlap-caller".to_string(),
                used_ids: vec![first, second],
                outcome: MarkOutcome::Success,
                recorded_at: CLOCK.0.to_string(),
            }],
        )
        .await
        .unwrap();

    let events = store
        .fetch_review_events(
            &owner,
            &[first],
            &memphant_types::RecallTime {
                evaluated_at: CLOCK.0.to_string(),
                transaction_as_of: CLOCK.0.to_string(),
                valid_at: CLOCK.0.to_string(),
            },
        )
        .await
        .unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].used_ids, vec![first]);
}

#[tokio::test]
async fn review_writes_reject_mixed_whitelists_and_foreign_contexts_atomically() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let subject = SubjectId::new();
    let scope = ScopeId::new();
    let owner = context(tenant, subject, scope, ActorId::new(), AgentNodeId::new());
    let unit_id = stage_unit(&store, &owner, "reviewed", "reviewed memory body").await;
    let recalled = recall(
        &store,
        recall_request(owner.clone(), "reviewed memory"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    assert!(recalled.items.iter().any(|item| item.unit_id == unit_id));

    let event = ReviewEvent {
        tenant_id: tenant,
        trace_id: recalled.trace_id,
        caller_id: "caller".to_string(),
        used_ids: vec![unit_id, UnitId::new()],
        outcome: MarkOutcome::Success,
        recorded_at: CLOCK.0.to_string(),
    };
    assert!(
        store
            .record_review_events(&owner, vec![event.clone()])
            .await
            .is_err()
    );
    assert!(store.review_events(tenant).is_empty());

    let wrong_actor = context(tenant, subject, scope, ActorId::new(), owner.agent_node_id);
    assert!(
        store
            .record_review_events(&wrong_actor, vec![event.clone()])
            .await
            .is_err()
    );
    let wrong_subject = context(
        tenant,
        SubjectId::new(),
        scope,
        owner.actor_id,
        owner.agent_node_id,
    );
    assert!(
        store
            .record_review_events(&wrong_subject, vec![event])
            .await
            .is_err()
    );
    assert!(store.review_events(tenant).is_empty());
}
