use memphant_core::{FixedClock, InMemoryStore, MemoryStore, recall};
use memphant_types::{
    ContextualChunk, EpisodeId, MemoryKind, NewMemoryUnit, RecallMode, RecallRequest,
    ResolvedMemoryContext, TenantId, TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-14T00:00:00Z");

fn unit(
    context: &ResolvedMemoryContext,
    episode_id: EpisodeId,
    kind: MemoryKind,
    state: UnitState,
    fact_key: &str,
    body: &str,
) -> NewMemoryUnit {
    NewMemoryUnit {
        capture: None,
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        kind,
        state,
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
        source_episode_id: Some(episode_id),
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: (kind == MemoryKind::Semantic)
            .then(|| ContextualChunk {
                id: format!("evidence-{fact_key}"),
                header: "[structured-state evidence]".to_string(),
                body: body.to_string(),
                source_span: Some("0-1".to_string()),
            })
            .into_iter()
            .collect(),
        valid_from: None,
        valid_to: None,
        transaction_from: None,
        transaction_to: None,
    }
}

async fn stage(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    units: Vec<NewMemoryUnit>,
) -> Vec<UnitId> {
    let mut transaction = store.begin(context).await.unwrap();
    let mut ids = Vec::with_capacity(units.len());
    for unit in units {
        ids.push(
            store
                .stage_memory_unit(&mut transaction, unit)
                .await
                .unwrap(),
        );
    }
    store.commit(transaction).await.unwrap();
    ids
}

fn request(context: &ResolvedMemoryContext) -> RecallRequest {
    RecallRequest {
        compact_only: false,
        context: context.clone(),
        query: "Write a proposal for 'Aurora Routing Plan'".to_string(),
        k: 10,
        budget_tokens: 1024,
        mode: RecallMode::Fast,
        include_beliefs: false,
        edge_expansion_enabled: false,
        context_packing_abstention_enabled: false,
        procedure_recall_enabled: true,
        decay_enabled: false,
        engine_version: "artifact-reconstruction-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

#[tokio::test]
async fn explicit_title_reconstructs_one_current_grounded_artifact_deterministically() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let seed_episode = EpisodeId::new();
    let sibling_episode = EpisodeId::new();
    let distractor_episode = EpisodeId::new();

    let mut expired_rectangle = unit(
        &context,
        seed_episode,
        MemoryKind::Semantic,
        UnitState::Active,
        "artifact_aurora:old_owner",
        "artifact_aurora item old_owner: {\"value\":\"Removed Person\"}",
    );
    expired_rectangle.valid_to = Some("2026-07-13T00:00:00Z".to_string());
    let mut superseded_rectangle = unit(
        &context,
        seed_episode,
        MemoryKind::Semantic,
        UnitState::Superseded,
        "artifact_aurora:old_architect",
        "artifact_aurora item old_architect: {\"value\":\"Prior Architect\"}",
    );
    superseded_rectangle.transaction_to = Some("2026-07-13T00:00:00Z".to_string());
    let mut units = vec![
        unit(
            &context,
            seed_episode,
            MemoryKind::Episodic,
            UnitState::Active,
            "raw:aurora-routing-update",
            "Please update the proposal titled 'Aurora Routing Plan'.",
        ),
        unit(
            &context,
            seed_episode,
            MemoryKind::Semantic,
            UnitState::Active,
            "artifact_aurora:title",
            "artifact_aurora item title: {\"value\":\"Aurora Routing Plan\"}",
        ),
        unit(
            &context,
            sibling_episode,
            MemoryKind::Semantic,
            UnitState::Active,
            "artifact_aurora:budget",
            "artifact_aurora item budget: {\"currency\":\"usd\",\"value\":\"575000\"}",
        ),
        unit(
            &context,
            seed_episode,
            MemoryKind::Semantic,
            UnitState::Active,
            "artifact_aurora_metrics:latency",
            "artifact_aurora_metrics item latency: {\"unit\":\"ms\",\"value\":\"140\"}",
        ),
        expired_rectangle,
        superseded_rectangle,
        unit(
            &context,
            distractor_episode,
            MemoryKind::Episodic,
            UnitState::Active,
            "raw:aurora-spatial",
            "The unrelated proposal is titled 'Aurora Spatial Plan'.",
        ),
        unit(
            &context,
            distractor_episode,
            MemoryKind::Semantic,
            UnitState::Active,
            "artifact_spatial:budget",
            "artifact_spatial item budget: {\"currency\":\"usd\",\"value\":\"999999\"}",
        ),
    ];
    let sibling_start = units.len();
    for index in 0..10 {
        units.push(unit(
            &context,
            sibling_episode,
            MemoryKind::Semantic,
            UnitState::Active,
            &format!("artifact_aurora:detail_{index}"),
            &format!(
                "artifact_aurora item detail_{index}: {{\"value\":\"Grounded project detail {index} with enough descriptive context to model a realistic multi-field proposal\"}}"
            ),
        ));
    }
    let sibling_end = units.len();
    for index in 0..12 {
        units.push(unit(
            &context,
            distractor_episode,
            MemoryKind::Episodic,
            UnitState::Active,
            &format!("raw:distractor-{index}"),
            &format!("Aurora routing proposal plan status summary distractor {index}"),
        ));
    }
    let ids = stage(&store, &context, units).await;
    let mut expected_sources = vec![ids[1], ids[2], ids[3]];
    expected_sources.extend(ids[sibling_start..sibling_end].iter().copied());
    expected_sources.sort_unstable_by_key(|id| id.as_uuid());

    let first = recall(&store, request(&context), None, &CLOCK)
        .await
        .unwrap();
    let second = recall(&store, request(&context), None, &CLOCK)
        .await
        .unwrap();
    let first_bundle = first
        .items
        .iter()
        .find(|item| !item.derived_from_unit_ids.is_empty())
        .expect("one reconstructed artifact");
    let second_bundle = second
        .items
        .iter()
        .find(|item| !item.derived_from_unit_ids.is_empty())
        .expect("same reconstructed artifact");

    assert_eq!(
        first
            .items
            .iter()
            .filter(|item| !item.derived_from_unit_ids.is_empty())
            .count(),
        1,
        "one bundle replaces its field-level members"
    );
    assert_eq!(first_bundle.unit_id, second_bundle.unit_id);
    assert_eq!(first.items[0].unit_id, first_bundle.unit_id);
    assert_eq!(first_bundle.body, second_bundle.body);
    assert_eq!(first_bundle.derived_from_unit_ids, expected_sources);
    assert!(first_bundle.body.contains("Aurora Routing Plan"));
    assert!(first_bundle.body.contains("575000"));
    assert!(first_bundle.body.contains("140"));
    assert!(first_bundle.body.contains("detail_9"));
    assert!(!first_bundle.body.contains("Removed Person"));
    assert!(!first_bundle.body.contains("Prior Architect"));
    assert!(!first_bundle.body.contains("999999"));

    let mut historical_request = request(&context);
    historical_request.valid_at = Some("2026-07-12T00:00:00Z".to_string());
    let historical = recall(&store, historical_request, None, &CLOCK)
        .await
        .unwrap();
    let historical_bundle = historical
        .items
        .iter()
        .find(|item| !item.derived_from_unit_ids.is_empty())
        .expect("artifact reconstructed from the selected historical rectangles");
    assert!(historical_bundle.body.contains("Removed Person"));

    let mut transaction_request = request(&context);
    transaction_request.transaction_as_of = Some("2026-07-12T00:00:00Z".to_string());
    let transaction_history = recall(&store, transaction_request, None, &CLOCK)
        .await
        .unwrap();
    let transaction_bundle = transaction_history
        .items
        .iter()
        .find(|item| !item.derived_from_unit_ids.is_empty())
        .expect("artifact reconstructed from the selected transaction history");
    assert!(transaction_bundle.body.contains("Prior Architect"));
}
