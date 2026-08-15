use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{
    EmbeddingProvider, EmbeddingRow, FixedClock, InMemoryStore, MemoryStore, NoopEmbedding,
    StubEmbedding, correct_memory, embedding_profile_for, forget_memory, recall, record_mark,
    retain_episode,
};
use memphant_types::{
    ContextBindingAccessPolicy, ContextBindingAgentRef, ContextBindingEntityRef,
    ContextBindingRequest, ContextBindingScopeRef, CorrectRequest, CorrectSelector,
    CorrectionPayload, ForgetRequest, ForgetSelector, MarkOutcome, MarkRequest, MemoryKind,
    NewEpisode, NewMemoryUnit, RecallHttpRequest, RecallMode, RecallRequest, RecallTime,
    RetainRequest, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");

fn tenant(value: u128) -> TenantId {
    TenantId::from_u128(value)
}

#[tokio::test]
async fn recall_does_not_admit_another_subjects_unit_from_a_caller_supplied_scope() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_000);
    let context_a = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let context_b = memphant_store_testkit::bind_context(&store, tenant_id).await;
    seed_active_unit(
        &store,
        &context_b,
        "private_city:value",
        "Subject B lives in Kyoto.",
    )
    .await;

    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
    let mut context = context_a.clone();
    context.scope_id = context_b.scope_id;
    let error = service
        .recall(
            context,
            RecallHttpRequest {
                compact_only: false,
                subject_id: context_a.data_subject_id,
                scope_id: context_b.scope_id,
                actor_id: context_a.actor_id,
                agent_node_id: context_a.agent_node_id,
                subject_generation: context_a.subject_generation,
                query: "Where does subject B live?".to_string(),
                limit: Some(8),
                budget_tokens: Some(256),
                mode: Some(RecallMode::Fast),
                include_beliefs: Some(true),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .expect_err("caller-supplied cross-subject context must fail closed");

    assert!(matches!(
        error,
        memphant_core::service::ServiceError::Core(memphant_core::CoreError::Store(
            memphant_core::StoreError::NotFound("memory context")
        ))
    ));
}

#[tokio::test]
async fn vector_candidates_reject_another_subject_in_the_same_tenant() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_100);
    let context_a = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let context_b = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let unit_id = seed_active_unit(
        &store,
        &context_b,
        "private_city:value",
        "Subject B lives in Kyoto.",
    )
    .await;
    let embedder = StubEmbedding::default();
    let profile = embedding_profile_for(&embedder);
    store
        .upsert_embedding_profile(tenant_id, profile.clone())
        .await
        .unwrap();
    let query_vec = embedder
        .embed(&["Subject B lives in Kyoto.".to_string()])
        .unwrap()
        .remove(0);
    store
        .upsert_embeddings(
            &context_b,
            vec![EmbeddingRow {
                memory_unit_id: unit_id,
                embedding_profile_id: profile.id,
                vec: query_vec.clone(),
            }],
        )
        .await
        .unwrap();
    assert!(
        store
            .fetch_embeddings(&context_a, &[unit_id])
            .await
            .unwrap()
            .is_empty(),
        "embedding reads are subject-bound even under one tenant"
    );
    assert!(
        store
            .upsert_embeddings(
                &context_a,
                vec![EmbeddingRow {
                    memory_unit_id: unit_id,
                    embedding_profile_id: profile.id,
                    vec: query_vec.clone(),
                }],
            )
            .await
            .is_err(),
        "embedding writes cannot attach another subject's unit"
    );

    let candidates = store
        .fetch_vector_candidates(
            &context_a,
            &query_vec,
            profile.id,
            &RecallTime {
                evaluated_at: CLOCK.0.to_string(),
                transaction_as_of: CLOCK.0.to_string(),
                valid_at: CLOCK.0.to_string(),
            },
            8,
        )
        .await
        .unwrap();
    assert!(candidates.is_empty());
}

#[tokio::test]
async fn recall_candidates_reject_a_stale_subject_generation() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_200);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    seed_active_unit(
        &store,
        &context,
        "private_city:value",
        "The current city is Kyoto.",
    )
    .await;
    let mut stale = context;
    stale.subject_generation = 1;
    let error = store
        .fetch_recall_candidates(
            &stale,
            &[],
            &["kyoto".to_string()],
            &RecallTime {
                evaluated_at: CLOCK.0.to_string(),
                transaction_as_of: CLOCK.0.to_string(),
                valid_at: CLOCK.0.to_string(),
            },
            8,
        )
        .await
        .expect_err("stale context must be rejected");
    assert!(matches!(
        error,
        memphant_core::StoreError::StaleSubjectGeneration
    ));
}

#[tokio::test]
async fn recall_admission_uses_the_scope_grant_for_each_memory_kind() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_250);
    let source = memphant_store_testkit::bind_context_request(
        &store,
        tenant_id,
        "source",
        ContextBindingRequest {
            subject: ContextBindingEntityRef {
                external_ref: "subject:79-250".to_string(),
                kind: "user".to_string(),
            },
            actor: ContextBindingEntityRef {
                external_ref: "actor:79-250".to_string(),
                kind: "user".to_string(),
            },
            scope: ContextBindingScopeRef {
                external_ref: "scope:shared".to_string(),
                kind: "memory".to_string(),
                parent_external_ref: None,
            },
            agent_node: ContextBindingAgentRef {
                external_ref: "agent:source".to_string(),
                parent_external_ref: None,
            },
            access_policies: vec![],
        },
    )
    .await;
    let caller = memphant_store_testkit::bind_context_request(
        &store,
        tenant_id,
        "caller",
        ContextBindingRequest {
            subject: ContextBindingEntityRef {
                external_ref: "subject:79-250".to_string(),
                kind: "user".to_string(),
            },
            actor: ContextBindingEntityRef {
                external_ref: "actor:79-250".to_string(),
                kind: "user".to_string(),
            },
            scope: ContextBindingScopeRef {
                external_ref: "scope:shared".to_string(),
                kind: "memory".to_string(),
                parent_external_ref: None,
            },
            agent_node: ContextBindingAgentRef {
                external_ref: "agent:caller".to_string(),
                parent_external_ref: None,
            },
            access_policies: vec![ContextBindingAccessPolicy::Grant {
                source_scope_external_ref: "scope:shared".to_string(),
                source_agent_node_external_ref: "agent:source".to_string(),
                kind: MemoryKind::Semantic,
            }],
        },
    )
    .await;
    let mut tx = store.begin(&source).await.unwrap();
    store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id,
                data_subject_id: source.data_subject_id,
                scope_id: source.scope_id,
                agent_node_id: source.agent_node_id,
                subject_generation: source.subject_generation,
                kind: MemoryKind::Resource,
                state: UnitState::Active,
                fact_key: None,
                predicate: None,
                body: "Private resource document".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(source.actor_id),
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
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    let candidates = store
        .fetch_recall_candidates(
            &caller,
            &[],
            &["resource".to_string()],
            &RecallTime {
                evaluated_at: CLOCK.0.to_string(),
                transaction_as_of: CLOCK.0.to_string(),
                valid_at: CLOCK.0.to_string(),
            },
            8,
        )
        .await
        .unwrap();
    assert!(candidates.is_empty());
}

#[tokio::test]
async fn degraded_fallback_rejects_another_subjects_pending_episode() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_300);
    let context_a = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let context_b = memphant_store_testkit::bind_context(&store, tenant_id).await;
    retain_episode(
        &store,
        &context_b,
        RetainRequest {
            tenant_id,
            data_subject_id: context_b.data_subject_id,
            scope_id: context_b.scope_id,
            actor_id: context_b.actor_id,
            agent_node_id: context_b.agent_node_id,
            subject_generation: context_b.subject_generation,
            source_kind: "user".to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: None,
            subject: None,
            predicate: None,
            body: "Subject B lives in Kyoto.".to_string(),
            compiler_version: "subject-isolation-test".to_string(),
        },
    )
    .await
    .unwrap();
    let request = RecallHttpRequest {
        compact_only: false,
        subject_id: context_a.data_subject_id,
        scope_id: context_a.scope_id,
        actor_id: context_a.actor_id,
        agent_node_id: context_a.agent_node_id,
        subject_generation: context_a.subject_generation,
        query: "Where does subject B live?".to_string(),
        limit: Some(8),
        budget_tokens: Some(256),
        mode: Some(RecallMode::Fast),
        include_beliefs: Some(true),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    };
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
    let response = service.recall(context_a, request).await.unwrap();
    assert!(response.items.is_empty());
    assert!(!response.degraded);
}

#[tokio::test]
async fn degraded_fallback_keeps_trace_membership_empty() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_350);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    retain_episode(
        &store,
        &context,
        RetainRequest {
            tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_kind: "user".to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: None,
            subject: None,
            predicate: None,
            body: "Fallback rollout window is Thursday night.".to_string(),
            compiler_version: "degraded-trace-integrity-test".to_string(),
        },
    )
    .await
    .unwrap();
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
    let response = service
        .recall(
            context.clone(),
            RecallHttpRequest {
                compact_only: false,
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                query: "When is the fallback rollout window?".to_string(),
                limit: Some(8),
                budget_tokens: Some(256),
                mode: Some(RecallMode::Fast),
                include_beliefs: Some(true),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .unwrap();

    assert!(response.degraded);
    assert_eq!(
        response.items[0].body,
        "Fallback rollout window is Thursday night."
    );
    assert!(response.items[0].citation_episode_id.is_none());
    assert!(response.items[0].citation_resource_id.is_none());
    assert!(response.citations.is_empty());
    assert!(response.candidate_whitelist.is_empty());
    assert_eq!(response.consolidation_lag_ms, 1);
    let degraded_id = response.items[0].unit_id;
    let trace = service
        .trace(&context, response.trace_id)
        .await
        .unwrap()
        .expect("canonical trace");
    assert!(trace.citations.is_empty());
    assert!(trace.context_items.is_empty());
    assert_eq!(trace.consolidation_lag_ms, 1);
    let trace_json = serde_json::to_value(&trace).expect("serialize degraded trace");
    assert_eq!(
        trace_json["degradation"]["reason"],
        "pending_reflection_read_your_own_writes"
    );
    assert_eq!(trace_json["degradation"]["consolidation_lag_ms"], 1);
    assert_eq!(
        trace_json["degradation"]["items"][0]["body"],
        "Fallback rollout window is Thursday night."
    );

    let error = service
        .mark(
            &context,
            "degraded-trace-integrity-test",
            MarkRequest {
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                trace_id: response.trace_id,
                caller_id: "degraded-trace-integrity-test".to_string(),
                used_ids: vec![degraded_id],
                outcome: MarkOutcome::Success,
            },
        )
        .await
        .expect_err("degraded synthetic ids are not canonical trace members");
    assert!(error.to_string().contains("canonical inclusion whitelist"));
}

/// Anti-pattern guard: the compact coding lane (`compact_only`) must never serve
/// the raw-episode read-your-own-writes fallback. A raw episode has no compact
/// envelope, so leaking it would violate the compact-only shape contract. The
/// same fixture that yields a degraded raw item at `compact_only: false` (above)
/// must return an honest empty at `compact_only: true`.
#[tokio::test]
async fn compact_lane_never_serves_the_raw_episode_degraded_fallback() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_355);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    retain_episode(
        &store,
        &context,
        RetainRequest {
            tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_kind: "user".to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: None,
            subject: None,
            predicate: None,
            body: "Fallback rollout window is Thursday night.".to_string(),
            compiler_version: "compact-degraded-guard-test".to_string(),
        },
    )
    .await
    .unwrap();
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
    let response = service
        .recall(
            context.clone(),
            RecallHttpRequest {
                compact_only: true,
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                query: "When is the fallback rollout window?".to_string(),
                limit: Some(8),
                budget_tokens: Some(256),
                mode: Some(RecallMode::Fast),
                include_beliefs: Some(false),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .unwrap();

    assert!(
        !response.degraded,
        "compact lane must not enter degraded mode"
    );
    assert!(
        response.items.is_empty(),
        "compact lane must not leak a non-compact raw episode: {:?}",
        response.items
    );
}

#[tokio::test]
async fn another_subjects_full_context_cannot_fetch_a_trace() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(79_400);
    let context_a = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let context_b = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let request = RecallHttpRequest {
        compact_only: false,
        subject_id: context_a.data_subject_id,
        scope_id: context_a.scope_id,
        actor_id: context_a.actor_id,
        agent_node_id: context_a.agent_node_id,
        subject_generation: 0,
        query: "anything".to_string(),
        limit: Some(8),
        budget_tokens: Some(256),
        mode: Some(RecallMode::Fast),
        include_beliefs: Some(true),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    };
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
    let response = service.recall(context_a.clone(), request).await.unwrap();
    assert!(
        service
            .trace(&context_a, response.trace_id)
            .await
            .unwrap()
            .is_some()
    );
    assert!(
        service
            .trace(&context_b, response.trace_id)
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn correct_supersedes_old_generation_and_recall_returns_new_value() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(80_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let old_id = seed_active_unit(
        &store,
        &context,
        "callback_token:value",
        "Callback token is v1.",
    )
    .await;

    let corrected = correct_memory(
        &store,
        &context,
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
                value: "Callback token is v2.".to_string(),
                reason: "stale_fact".to_string(),
                source_ref: "test:correction".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                valid_from: Some("2026-07-01T00:00:00Z".to_string()),
                valid_to: None,
            },
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("correction succeeds");

    assert_eq!(corrected.superseded, vec![old_id]);
    assert_eq!(corrected.created.len(), 2);
    assert_eq!(corrected.correction_kind, "retroactive");
    let units = store.memory_units(tenant_id);
    let old_unit = units.iter().find(|unit| unit.id == old_id).unwrap();
    assert_eq!(old_unit.state, UnitState::Superseded);
    assert_eq!(
        old_unit.transaction_to.as_deref(),
        Some("2026-07-03T00:00:00Z")
    );

    let replacement = units
        .iter()
        .find(|unit| unit.id == corrected.created[0])
        .unwrap();
    assert_eq!(replacement.body, "Callback token is v2.");
    assert_eq!(
        replacement.valid_from.as_deref(),
        Some("2026-07-01T00:00:00Z")
    );
    let remainder = units
        .iter()
        .find(|unit| unit.id == corrected.created[1])
        .unwrap();
    assert_eq!(remainder.body, "Callback token is v1.");
    assert_eq!(remainder.valid_to.as_deref(), Some("2026-07-01T00:00:00Z"));
    assert_eq!(replacement.valid_to, None);
    assert_eq!(
        replacement.transaction_from.as_deref(),
        Some("2026-07-03T00:00:00Z")
    );
    assert_eq!(replacement.transaction_to, None);

    let recalled = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: context.clone(),
            query: "Which callback token is current?".to_string(),
            k: 3,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-wsd-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    assert_eq!(recalled.items.len(), 1);
    assert_eq!(recalled.items[0].body, "Callback token is v2.");
}

#[tokio::test]
async fn forget_marks_memory_deleted_and_recall_hides_it() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(81_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let unit_id = seed_active_unit(
        &store,
        &context,
        "refund_window:value",
        "Refund window is 30 days.",
    )
    .await;

    let forgotten = forget_memory(
        &store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            selector: ForgetSelector {
                memory_unit_id: Some(unit_id),
                episode_id: None,
                resource_id: None,
                scope_id: context.scope_id,
            },
            reason: "user_request".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget succeeds");

    assert_eq!(forgotten.invalidated_units, vec![unit_id]);
    assert!(forgotten.deletion_generation > 0);
    let unit = store
        .memory_units(tenant_id)
        .into_iter()
        .find(|unit| unit.id == unit_id)
        .expect("unit remains as tombstone");
    assert_eq!(unit.state, UnitState::Deleted);
    assert_eq!(
        unit.deletion_generation,
        Some(forgotten.deletion_generation)
    );

    let recalled = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: context.clone(),
            query: "What is the refund window?".to_string(),
            k: 3,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-wsd-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    assert!(recalled.items.is_empty());
}

#[tokio::test]
async fn mark_records_outcome_feedback_for_trace() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(82_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let unit_id = seed_active_unit(
        &store,
        &context,
        "deploy_region:value",
        "Deploy region is Taipei.",
    )
    .await;
    let recalled = recall_seeded_unit(&store, &context, "deploy region").await;
    assert!(recalled.candidate_whitelist.contains(&unit_id));
    let trace_id = recalled.trace_id;

    let marked = record_mark(
        &store,
        &context,
        MarkRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: 0,
            trace_id,
            caller_id: "surface-contract-test".to_string(),
            used_ids: vec![unit_id],
            outcome: MarkOutcome::Success,
        },
        &CLOCK,
    )
    .await
    .expect("mark succeeds");

    assert!(marked.accepted);
    let events = store.review_events(tenant_id);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].trace_id, trace_id);
    assert_eq!(events[0].used_ids, vec![unit_id]);
    assert_eq!(events[0].outcome, MarkOutcome::Success);
}

#[tokio::test]
async fn mark_is_idempotent_per_trace_and_caller() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(82_100);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let unit_id = seed_active_unit(
        &store,
        &context,
        "deploy_region:value",
        "Deploy region is Taipei.",
    )
    .await;
    let recalled = recall_seeded_unit(&store, &context, "deploy region").await;
    assert!(recalled.candidate_whitelist.contains(&unit_id));
    let trace_id = recalled.trace_id;
    let request = MarkRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: 0,
        trace_id,
        caller_id: "surface-contract-test".to_string(),
        used_ids: vec![unit_id],
        outcome: MarkOutcome::Success,
    };

    record_mark(&store, &context, request.clone(), &CLOCK)
        .await
        .expect("first mark succeeds");
    record_mark(&store, &context, request, &CLOCK)
        .await
        .expect("duplicate mark succeeds");

    let events = store.review_events(tenant_id);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].trace_id, trace_id);
}

#[tokio::test]
async fn mark_keeps_the_canonical_unit_instead_of_expanding_hidden_lineage() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(82_200);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let unit_id = seed_active_unit(
        &store,
        &context,
        "deploy_region:value",
        "Deploy region is Taipei.",
    )
    .await;
    let recalled = recall_seeded_unit(&store, &context, "deploy region").await;
    let mut trace = store
        .trace_by_id(&context, recalled.trace_id)
        .await
        .unwrap()
        .expect("stored trace");
    let hidden_source = memphant_types::UnitId::new();
    let citation = trace
        .citations
        .iter_mut()
        .find(|citation| citation.unit_id == unit_id)
        .expect("visible unit citation");
    citation.derived_from_unit_ids = vec![hidden_source];
    store.store_trace(&context, trace).await.unwrap();

    record_mark(
        &store,
        &context,
        MarkRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            trace_id: recalled.trace_id,
            caller_id: "lineage-membership-test".to_string(),
            used_ids: vec![unit_id],
            outcome: MarkOutcome::Success,
        },
        &CLOCK,
    )
    .await
    .expect("canonical mark succeeds");

    assert_eq!(store.review_events(tenant_id)[0].used_ids, vec![unit_id]);
}

async fn recall_seeded_unit(
    store: &InMemoryStore,
    context: &memphant_core::ResolvedMemoryContext,
    query: &str,
) -> memphant_types::RecallResponse {
    recall(
        store,
        RecallRequest {
            compact_only: false,
            context: context.clone(),
            query: query.to_string(),
            k: 4,
            budget_tokens: 128,
            mode: RecallMode::Fast,
            include_beliefs: true,
            edge_expansion_enabled: false,
            context_packing_abstention_enabled: false,
            procedure_recall_enabled: true,
            decay_enabled: false,
            engine_version: "mark-whitelist-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall seeded unit")
}

async fn seed_active_unit(
    store: &InMemoryStore,
    context: &memphant_core::ResolvedMemoryContext,
    fact_key: &str,
    body: &str,
) -> memphant_types::UnitId {
    let mut tx = store.begin(context).await.expect("begin transaction");
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
                source_kind: "system".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedSystem,
                dedup_key: format!("{fact_key}:{body}"),
                body: body.to_string(),
            },
        )
        .await
        .expect("episode seed");
    let unit_id = store
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
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("system".to_string()),
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
    store.commit(tx).await.expect("seed commit");
    unit_id
}

/// Correcting an already-superseded generation must be rejected, not mint a
/// second live replacement. `apply_correction` locks + supersedes the target,
/// but its guard only excluded `deleted` units — so re-correcting the SAME id
/// (double-submit / retry) re-superseded an already-closed unit and created
/// another active generation. In Postgres the partial unique scope-subject
/// index masks this for `semantic` units (it errors instead), but non-semantic
/// kinds silently double-insert; in-memory has no index, so any kind
/// duplicates. The guard must require the OPEN generation (`transaction_to is
/// null`) in both stores.
#[tokio::test]
async fn correct_rejects_an_already_superseded_generation() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(80_100);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let old_id = seed_active_unit(
        &store,
        &context,
        "webhook_secret:value",
        "Webhook secret is s1.",
    )
    .await;

    let request = |value: &str| CorrectRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        selector: CorrectSelector {
            memory_unit_id: old_id,
        },
        correction: CorrectionPayload {
            value: value.to_string(),
            reason: "stale_fact".to_string(),
            source_ref: "test:correction".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            valid_from: None,
            valid_to: None,
        },
    };

    correct_memory(
        &store,
        &context,
        request("Webhook secret is s2."),
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("first correction supersedes the open generation");

    // `old_id` is now superseded; correcting it again targets a CLOSED
    // generation and must fail rather than mint a second live unit.
    let second = correct_memory(
        &store,
        &context,
        request("Webhook secret is s3."),
        &NoopEmbedding,
        &CLOCK,
    )
    .await;
    assert!(
        second.is_err(),
        "re-correcting an already-superseded generation must be rejected, got {second:?}"
    );

    let open: Vec<_> = store
        .memory_units(tenant_id)
        .into_iter()
        .filter(|unit| {
            unit.fact_key.as_deref() == Some("webhook_secret:value")
                && unit.state == UnitState::Active
                && unit.transaction_to.is_none()
        })
        .collect();
    assert_eq!(
        open.len(),
        2,
        "the accepted correction must leave one historical remainder and one current rectangle"
    );
    let historical = open
        .iter()
        .find(|unit| unit.body == "Webhook secret is s1.")
        .expect("the original value remains valid before the correction");
    assert_eq!(historical.valid_from, None);
    assert_eq!(historical.valid_to.as_deref(), Some(CLOCK.0));

    let current = open
        .iter()
        .find(|unit| unit.body == "Webhook secret is s2.")
        .expect("the first correction remains the current value");
    assert_eq!(current.valid_from.as_deref(), Some(CLOCK.0));
    assert_eq!(current.valid_to, None);
    assert!(open.iter().all(|unit| unit.body != "Webhook secret is s3."));
}

#[tokio::test]
async fn correction_rejects_a_sibling_agent_unit_in_the_same_scope() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(80_200);
    let owner = memphant_store_testkit::bind_context_request(
        &store,
        tenant_id,
        "owner",
        ContextBindingRequest {
            subject: ContextBindingEntityRef {
                external_ref: "subject:80-200".to_string(),
                kind: "user".to_string(),
            },
            actor: ContextBindingEntityRef {
                external_ref: "actor:80-200".to_string(),
                kind: "user".to_string(),
            },
            scope: ContextBindingScopeRef {
                external_ref: "scope:shared".to_string(),
                kind: "memory".to_string(),
                parent_external_ref: None,
            },
            agent_node: ContextBindingAgentRef {
                external_ref: "agent:owner".to_string(),
                parent_external_ref: None,
            },
            access_policies: vec![],
        },
    )
    .await;
    let sibling = memphant_store_testkit::bind_context_request(
        &store,
        tenant_id,
        "sibling",
        ContextBindingRequest {
            subject: ContextBindingEntityRef {
                external_ref: "subject:80-200".to_string(),
                kind: "user".to_string(),
            },
            actor: ContextBindingEntityRef {
                external_ref: "actor:80-200".to_string(),
                kind: "user".to_string(),
            },
            scope: ContextBindingScopeRef {
                external_ref: "scope:shared".to_string(),
                kind: "memory".to_string(),
                parent_external_ref: None,
            },
            agent_node: ContextBindingAgentRef {
                external_ref: "agent:sibling".to_string(),
                parent_external_ref: None,
            },
            access_policies: vec![],
        },
    )
    .await;
    let unit_id = seed_active_unit(&store, &owner, "private:value", "owner-only value").await;

    let result = correct_memory(
        &store,
        &sibling,
        CorrectRequest {
            subject_id: sibling.data_subject_id,
            scope_id: sibling.scope_id,
            actor_id: sibling.actor_id,
            agent_node_id: sibling.agent_node_id,
            subject_generation: sibling.subject_generation,
            selector: CorrectSelector {
                memory_unit_id: unit_id,
            },
            correction: CorrectionPayload {
                value: "stolen value".to_string(),
                reason: "test".to_string(),
                source_ref: "test:correction".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                valid_from: None,
                valid_to: None,
            },
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await;

    assert!(matches!(result, Err(memphant_core::CoreError::NotFound(_))));
}

#[tokio::test]
async fn forget_rejects_a_stale_subject_generation() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(81_100);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let unit_id = seed_active_unit(&store, &context, "private:value", "generation two").await;
    let mut stale = context.clone();
    stale.subject_generation = 1;

    let result = forget_memory(
        &store,
        &stale,
        ForgetRequest {
            subject_id: stale.data_subject_id,
            scope_id: stale.scope_id,
            actor_id: stale.actor_id,
            agent_node_id: stale.agent_node_id,
            subject_generation: 1,
            selector: ForgetSelector {
                memory_unit_id: Some(unit_id),
                episode_id: None,
                resource_id: None,
                scope_id: stale.scope_id,
            },
            reason: "test".to_string(),
        },
        &CLOCK,
    )
    .await;

    assert!(matches!(
        result,
        Err(memphant_core::CoreError::Store(
            memphant_core::StoreError::StaleSubjectGeneration
        ))
    ));
    assert_eq!(
        store
            .memory_units(tenant_id)
            .into_iter()
            .find(|unit| unit.id == unit_id)
            .unwrap()
            .state,
        UnitState::Active
    );
}

#[tokio::test]
async fn forget_rejects_another_subjects_unit() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(81_200);
    let owner = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let caller = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let unit_id = seed_active_unit(&store, &owner, "private:value", "owner-only value").await;
    let result = forget_memory(
        &store,
        &caller,
        ForgetRequest {
            subject_id: caller.data_subject_id,
            scope_id: caller.scope_id,
            actor_id: caller.actor_id,
            agent_node_id: caller.agent_node_id,
            subject_generation: caller.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: Some(unit_id),
                episode_id: None,
                resource_id: None,
                scope_id: caller.scope_id,
            },
            reason: "test".to_string(),
        },
        &CLOCK,
    )
    .await;

    assert!(matches!(
        result,
        Err(memphant_core::CoreError::Store(
            memphant_core::StoreError::NotFound(_)
        ))
    ));
}
