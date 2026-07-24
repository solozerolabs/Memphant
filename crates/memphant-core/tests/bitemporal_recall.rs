use memphant_core::{CorrectionWrite, FixedClock, InMemoryStore, MemoryStore, recall};
use memphant_types::{
    CorrectSelector, CorrectionPayload, MemoryKind, NewMemoryUnit, RecallMode, RecallRequest,
    TenantId, TrustLevel, UnitState,
};

#[tokio::test]
async fn recall_resolves_both_time_axes_across_a_split_update_chain() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    // The store's strict context contract (canonical cutover) requires a
    // registered binding before any read/write; a hand-built context is
    // rejected with NotFound("memory context").
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let mut tx = store.begin(&context).await.unwrap();
    let old_id = store
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
                body: "lives in Oslo".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("test".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: Some("2025-01-01T00:00:00Z".to_string()),
                valid_to: Some("2026-01-01T00:00:00Z".to_string()),
                transaction_from: Some("2025-01-02T00:00:00Z".to_string()),
                transaction_to: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    store
        .apply_correction(
            &context,
            CorrectionWrite {
                selector: CorrectSelector {
                    memory_unit_id: old_id,
                },
                correction: CorrectionPayload {
                    value: "lives in Lima".to_string(),
                    reason: "moved".to_string(),
                    source_ref: "test:correction".to_string(),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
                    valid_from: Some("2025-04-01T00:00:00Z".to_string()),
                    valid_to: Some("2025-07-01T00:00:00Z".to_string()),
                },
                source_ref: "test:correction".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                now: "2025-08-01T00:00:00Z".to_string(),
                embedding: None,
                unit_ids: Default::default(),
            },
        )
        .await
        .unwrap();

    let run = |transaction_as_of: &str, valid_at: &str, query: &str| {
        let store = store.clone();
        let request = RecallRequest {
            context: context.clone(),
            query: query.to_string(),
            k: 4,
            budget_tokens: 128,
            mode: RecallMode::Fast,
            include_beliefs: true,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: false,
            procedure_recall_enabled: true,
            decay_enabled: false,
            engine_version: "bitemporal-test".to_string(),
            transaction_as_of: Some(transaction_as_of.to_string()),
            valid_at: Some(valid_at.to_string()),
            aggregation_window: None,
        };
        async move {
            recall(&store, request, None, &FixedClock("2026-01-01T00:00:00Z"))
                .await
                .unwrap()
        }
    };

    let before_update = run("2025-07-01T00:00:00Z", "2025-05-01T00:00:00Z", "Oslo").await;
    assert_eq!(before_update.items[0].body, "lives in Oslo");
    let corrected = run("2025-09-01T00:00:00Z", "2025-05-01T00:00:00Z", "Lima").await;
    assert_eq!(corrected.items[0].body, "lives in Lima");
    let remainder = run("2025-09-01T00:00:00Z", "2025-02-01T00:00:00Z", "Oslo").await;
    assert_eq!(remainder.items[0].body, "lives in Oslo");
    assert_eq!(corrected.recall_time.evaluated_at, "2026-01-01T00:00:00Z");
}
