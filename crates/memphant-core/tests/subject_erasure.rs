use memphant_core::{
    ApiKeyRow, EmbeddingProfileRow, EmbeddingRow, FixedClock, InMemoryStore, MemoryStore,
    MutationClaim, MutationClaimOutcome, MutationLedgerStore, MutationResponse, MutationVerb,
    StoreError,
};
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    MemoryKind, NewEpisode, NewMemoryEdge, NewMemoryUnit, NewResource, RecallMode, RecallTime,
    ReflectJob, ReflectJobKind, ResolvedMemoryContext, ResourceKind, RetrievalTrace, TenantId,
    TraceId, TrustLevel, UnitState,
};
use uuid::Uuid;

const NOW: &str = "2026-07-15T00:00:00Z";

fn binding_request(name: &str) -> ContextBindingRequest {
    ContextBindingRequest {
        subject: ContextBindingEntityRef {
            external_ref: format!("subject:{name}"),
            kind: "user".to_string(),
        },
        actor: ContextBindingEntityRef {
            external_ref: format!("actor:{name}"),
            kind: "user".to_string(),
        },
        scope: ContextBindingScopeRef {
            external_ref: format!("scope:{name}"),
            kind: "memory".to_string(),
            parent_external_ref: None,
        },
        agent_node: ContextBindingAgentRef {
            external_ref: format!("agent:{name}"),
            parent_external_ref: None,
        },
        access_policies: Vec::new(),
    }
}

async fn bind(
    store: &InMemoryStore,
    tenant: TenantId,
    client_ref: &str,
    name: &str,
) -> ResolvedMemoryContext {
    let response = store
        .resolve_context_binding(tenant, client_ref.to_string(), binding_request(name))
        .await
        .unwrap();
    store
        .resolve_memory_context(
            tenant,
            response.subject_id,
            response.actor_id,
            response.scope_id,
            response.agent_node_id,
        )
        .await
        .unwrap()
}

fn claim(context: &ResolvedMemoryContext, verb: MutationVerb, key: &str) -> MutationClaim {
    MutationClaim::new(context, verb, key, [verb as u8; 32]).unwrap()
}

fn episode(context: &ResolvedMemoryContext, label: &str) -> NewEpisode {
    NewEpisode {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_kind: "user".to_string(),
        source_ref: format!("test:{label}"),
        observed_at: NOW.to_string(),
        source_trust: TrustLevel::TrustedUser,
        dedup_key: label.to_string(),
        body: label.to_string(),
    }
}

fn unit(context: &ResolvedMemoryContext, label: &str) -> NewMemoryUnit {
    NewMemoryUnit {
        capture: None,
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        kind: MemoryKind::Semantic,
        state: UnitState::Active,
        fact_key: Some(label.to_string()),
        predicate: None,
        body: label.to_string(),
        confidence: Some(1.0),
        trust_level: TrustLevel::TrustedUser,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(context.actor_id),
        source_kind: None,
        source_ref: format!("test:{label}"),
        observed_at: NOW.to_string(),
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

fn trace(context: &ResolvedMemoryContext) -> RetrievalTrace {
    RetrievalTrace {
        id: TraceId::new(),
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        policy_revision: context.policy_revision.clone(),
        query_hash: "redacted-query-hash".to_string(),
        engine_version: "test".to_string(),
        feature_flags: Vec::new(),
        channel_runs: Vec::new(),
        candidates: Vec::new(),
        policy_filters: Vec::new(),
        context_items: Vec::new(),
        dropped_items: Vec::new(),
        citations: Vec::new(),
        filter_selectivity: None,
        iterative_scan_depth: None,
        recall_pool_depth: 1,
        cross_rerank_ms: 0,
        cross_rerank: None,
        consolidation_lag_ms: 0,
        degradation: None,
        mode_requested: RecallMode::Fast,
        mode_executed: RecallMode::Fast,
        escalation_reason: "none".to_string(),
        procedure_ids: Vec::new(),
        procedure_validation_states: Vec::new(),
        abstention_signal: true,
        latency_ms: 0,
        token_estimate: 0,
        cost_micros: 0,
        decay_model_id: "none".to_string(),
        l4_sandbox_id: None,
        l4_gathered_evidence_ids: Vec::new(),
        deep: None,
        l4_provider: None,
        l4_model: None,
        l4_observed_provider: None,
        l4_observed_model: None,
        l4_prompt_hash: None,
        l4_config_hash: None,
        l4_workspace_manifest_sha256: None,
        recall_time: RecallTime {
            evaluated_at: NOW.to_string(),
            transaction_as_of: NOW.to_string(),
            valid_at: NOW.to_string(),
        },
    }
}

async fn seed_subject(store: &InMemoryStore, context: &ResolvedMemoryContext, label: &str) {
    let mut tx = store.begin_at(context, &FixedClock(NOW));
    let episode_id = store
        .stage_episode(&mut tx, episode(context, label))
        .await
        .unwrap()
        .episode_id;
    store
        .stage_resource(
            &mut tx,
            NewResource {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                uri: format!("memory://{label}"),
                source_ref: format!("test:{label}"),
                observed_at: NOW.to_string(),
                kind: ResourceKind::Other,
                content_hash: label.to_string(),
                mime_type: "text/plain".to_string(),
                revision: None,
                body: Some(label.to_string()),
                source_trust: TrustLevel::TrustedUser,
                acl: memphant_types::ResourceAcl::default(),
            },
        )
        .await
        .unwrap();
    let left = store
        .stage_memory_unit(&mut tx, unit(context, label))
        .await
        .unwrap();
    let right = store
        .stage_memory_unit(&mut tx, unit(context, &format!("{label}-right")))
        .await
        .unwrap();
    store
        .stage_memory_edge(
            &mut tx,
            NewMemoryEdge {
                tenant_id: context.tenant_id,
                scope_id: context.scope_id,
                src_id: left,
                dst_id: right,
                kind: memphant_types::MemoryEdgeKind::DerivedFrom,
            },
        )
        .await
        .unwrap();
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                episode_id: Some(episode_id),
                resource_id: None,
                kind: ReflectJobKind::ReflectEpisode,
                compiler_version: "test".to_string(),
                subject: None,
                predicate: None,
            },
        )
        .await
        .unwrap();
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                episode_id: None,
                resource_id: None,
                kind: ReflectJobKind::ReflectScope,
                compiler_version: "test".to_string(),
                subject: None,
                predicate: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    store.store_trace(context, trace(context)).await.unwrap();

    let profile = EmbeddingProfileRow {
        id: Uuid::new_v4(),
        provider: "test".to_string(),
        model: "test".to_string(),
        dimensions: 1,
        distance: "cosine".to_string(),
        version: "1".to_string(),
        index_strategy: "exact".to_string(),
    };
    store
        .upsert_embedding_profile(context.tenant_id, profile.clone())
        .await
        .unwrap();
    store
        .upsert_embeddings(
            context,
            vec![EmbeddingRow {
                memory_unit_id: left,
                embedding_profile_id: profile.id,
                vec: vec![1.0],
            }],
        )
        .await
        .unwrap();
}

#[tokio::test]
async fn erasure_atomically_removes_only_the_subject_and_all_subject_owned_state() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let erased = bind(&store, tenant, "erased-client", "shared-name").await;
    let survivor = bind(&store, tenant, "survivor-client", "survivor").await;
    seed_subject(&store, &erased, "erased").await;
    seed_subject(&store, &survivor, "survivor").await;

    let old_claim = claim(&erased, MutationVerb::Retain, "old-ledger-entry");
    let mut old_mutation = store.begin_at(&erased, &FixedClock(NOW));
    assert_eq!(
        store
            .stage_mutation_claim(&mut old_mutation, old_claim.clone())
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    store
        .stage_mutation_response(
            &mut old_mutation,
            MutationResponse::success(201, b"old".to_vec()).unwrap(),
        )
        .await
        .unwrap();
    store.commit(old_mutation).await.unwrap();

    store.insert_api_key(ApiKeyRow {
        id: Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: "tenant-wide".to_string(),
        label: "tenant-wide".to_string(),
        max_trust: TrustLevel::TrustedUser,
        data_subject_id: None,
        subject_generation: None,
        actor_id: None,
        scope_id: None,
        agent_node_id: None,
        can_forget: false,
        can_audit_history: false,
        revoked: false,
    });
    store.insert_api_key(ApiKeyRow {
        id: Uuid::new_v4(),
        tenant_id: tenant,
        key_hash: "subject-scoped".to_string(),
        label: "subject-scoped".to_string(),
        max_trust: TrustLevel::TrustedUser,
        data_subject_id: Some(erased.data_subject_id),
        subject_generation: Some(erased.subject_generation),
        actor_id: Some(erased.actor_id),
        scope_id: Some(erased.scope_id),
        agent_node_id: Some(erased.agent_node_id),
        can_forget: false,
        can_audit_history: false,
        revoked: false,
    });

    let mut tx = store.begin_at(&erased, &FixedClock(NOW));
    assert_eq!(
        store
            .stage_mutation_claim(
                &mut tx,
                claim(&erased, MutationVerb::EraseSubject, "erase-1"),
            )
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    let receipt = store.stage_subject_erasure(&mut tx).await.unwrap();
    assert_eq!(receipt.generation, erased.subject_generation + 1);
    assert_eq!(receipt.erased_at, NOW);
    assert_eq!(
        serde_json::to_value(&receipt).unwrap(),
        serde_json::json!({"generation": 1, "erased_at": NOW})
    );
    store.commit(tx).await.unwrap();

    assert_eq!(store.episodes(tenant).len(), 1);
    assert_eq!(store.resources(tenant).len(), 1);
    assert_eq!(store.memory_units(tenant).len(), 2);
    assert_eq!(store.memory_edges(tenant).len(), 1);
    assert_eq!(store.reflect_jobs(tenant).len(), 2);
    assert_eq!(store.retrieval_traces(tenant).len(), 1);
    assert!(
        store
            .lookup_api_key("subject-scoped")
            .await
            .unwrap()
            .is_none()
    );
    assert!(store.lookup_api_key("tenant-wide").await.unwrap().is_some());
    assert!(matches!(
        store
            .resolve_memory_context(
                tenant,
                erased.data_subject_id,
                erased.actor_id,
                erased.scope_id,
                erased.agent_node_id,
            )
            .await,
        Err(StoreError::SubjectErased)
    ));
    let mut deleted_ledger_replay = store.begin_at(&erased, &FixedClock(NOW));
    assert!(matches!(
        store
            .stage_mutation_claim(&mut deleted_ledger_replay, old_claim)
            .await,
        Err(StoreError::SubjectErased)
    ));

    let rebound = bind(&store, tenant, "erased-client", "shared-name").await;
    assert_ne!(rebound.data_subject_id, erased.data_subject_id);
    assert_eq!(rebound.subject_generation, 0);
    let survivor_ids = store
        .memory_units(tenant)
        .into_iter()
        .filter(|unit| unit.data_subject_id == survivor.data_subject_id)
        .map(|unit| unit.id)
        .collect::<Vec<_>>();
    assert_eq!(
        store
            .fetch_embeddings(&survivor, &survivor_ids)
            .await
            .unwrap()
            .len(),
        1
    );
}

#[tokio::test]
async fn erasure_replays_exact_receipt_and_rejects_stale_or_non_erasure_mutations() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = bind(&store, tenant, "client", "subject").await;
    let erasure_claim = claim(&context, MutationVerb::EraseSubject, "erase-1");

    let mut tx = store.begin_at(&context, &FixedClock(NOW));
    assert_eq!(
        store
            .stage_mutation_claim(&mut tx, erasure_claim.clone())
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    let receipt = store.stage_subject_erasure(&mut tx).await.unwrap();
    store.commit(tx).await.unwrap();

    let mut replay = store.begin_at(&context, &FixedClock(NOW));
    assert_eq!(
        store
            .stage_mutation_claim(&mut replay, erasure_claim)
            .await
            .unwrap(),
        MutationClaimOutcome::Replay(
            MutationResponse::success(200, serde_json::to_vec(&receipt).unwrap()).unwrap()
        )
    );
    store.commit(replay).await.unwrap();

    let mut stale = store.begin_at(&context, &FixedClock(NOW));
    assert!(matches!(
        store
            .stage_mutation_claim(
                &mut stale,
                claim(&context, MutationVerb::Retain, "stale-retain"),
            )
            .await,
        Err(StoreError::SubjectErased)
    ));
    let mut second_erasure = store.begin_at(&context, &FixedClock(NOW));
    assert!(matches!(
        store
            .stage_mutation_claim(
                &mut second_erasure,
                claim(&context, MutationVerb::EraseSubject, "erase-2"),
            )
            .await,
        Err(StoreError::SubjectErased)
    ));
}

#[tokio::test]
async fn erasure_rejects_a_transaction_that_already_staged_subject_writes() {
    let store = InMemoryStore::default();
    let context = bind(&store, TenantId::new(), "client", "subject").await;
    let mut tx = store.begin_at(&context, &FixedClock(NOW));
    assert_eq!(
        store
            .stage_mutation_claim(
                &mut tx,
                claim(&context, MutationVerb::EraseSubject, "erase-1"),
            )
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    store
        .stage_episode(&mut tx, episode(&context, "must-not-be-discarded"))
        .await
        .unwrap();

    assert!(matches!(
        store.stage_subject_erasure(&mut tx).await,
        Err(StoreError::Conflict(message)) if message.contains("empty transaction")
    ));
}

#[tokio::test]
async fn transaction_staged_before_erasure_cannot_resurrect_subject_state() {
    let store = InMemoryStore::default();
    let context = bind(&store, TenantId::new(), "client", "subject").await;

    let mut stale_write = store.begin_at(&context, &FixedClock(NOW));
    store
        .stage_mutation_claim(
            &mut stale_write,
            claim(&context, MutationVerb::Retain, "retain-before-erasure"),
        )
        .await
        .unwrap();
    store
        .stage_episode(&mut stale_write, episode(&context, "must-not-resurrect"))
        .await
        .unwrap();
    store
        .stage_mutation_response(
            &mut stale_write,
            MutationResponse::success(201, b"stale".to_vec()).unwrap(),
        )
        .await
        .unwrap();

    let mut erasure = store.begin_at(&context, &FixedClock(NOW));
    store
        .stage_mutation_claim(
            &mut erasure,
            claim(&context, MutationVerb::EraseSubject, "erase-wins"),
        )
        .await
        .unwrap();
    store.stage_subject_erasure(&mut erasure).await.unwrap();
    store.commit(erasure).await.unwrap();

    assert!(matches!(
        store.commit(stale_write).await,
        Err(StoreError::SubjectErased)
    ));
    assert!(store.episodes(context.tenant_id).is_empty());
}

#[tokio::test]
async fn only_one_pre_staged_erasure_can_commit() {
    let store = InMemoryStore::default();
    let context = bind(&store, TenantId::new(), "client", "subject").await;
    let mut first = store.begin_at(&context, &FixedClock(NOW));
    let mut second = store.begin_at(&context, &FixedClock(NOW));

    for (tx, key) in [(&mut first, "erase-first"), (&mut second, "erase-second")] {
        store
            .stage_mutation_claim(tx, claim(&context, MutationVerb::EraseSubject, key))
            .await
            .unwrap();
        store.stage_subject_erasure(tx).await.unwrap();
    }

    store.commit(first).await.unwrap();
    assert!(matches!(
        store.commit(second).await,
        Err(StoreError::SubjectErased)
    ));
}

#[tokio::test]
async fn replay_with_staged_writes_conflicts_instead_of_discarding_them() {
    let store = InMemoryStore::default();
    let context = bind(&store, TenantId::new(), "client", "subject").await;
    let retain_claim = claim(&context, MutationVerb::Retain, "retain-replay");

    let mut initial = store.begin_at(&context, &FixedClock(NOW));
    store
        .stage_mutation_claim(&mut initial, retain_claim.clone())
        .await
        .unwrap();
    store
        .stage_mutation_response(
            &mut initial,
            MutationResponse::success(201, b"canonical".to_vec()).unwrap(),
        )
        .await
        .unwrap();
    store.commit(initial).await.unwrap();

    let mut replay = store.begin_at(&context, &FixedClock(NOW));
    assert!(matches!(
        store
            .stage_mutation_claim(&mut replay, retain_claim)
            .await
            .unwrap(),
        MutationClaimOutcome::Replay(_)
    ));
    store
        .stage_episode(&mut replay, episode(&context, "must-not-be-discarded"))
        .await
        .unwrap();

    assert!(matches!(
        store.commit(replay).await,
        Err(StoreError::Conflict(message)) if message.contains("replayed mutation")
    ));
    assert!(store.episodes(context.tenant_id).is_empty());
}

#[tokio::test]
async fn stale_context_reads_and_direct_writes_fail_after_erasure() {
    let store = InMemoryStore::default();
    let context = bind(&store, TenantId::new(), "client", "subject").await;
    let mut erasure = store.begin_at(&context, &FixedClock(NOW));
    store
        .stage_mutation_claim(
            &mut erasure,
            claim(&context, MutationVerb::EraseSubject, "erase"),
        )
        .await
        .unwrap();
    store.stage_subject_erasure(&mut erasure).await.unwrap();
    store.commit(erasure).await.unwrap();

    assert!(matches!(
        store
            .fetch_recall_candidates(
                &context,
                &[],
                &[],
                &RecallTime {
                    evaluated_at: NOW.to_string(),
                    transaction_as_of: NOW.to_string(),
                    valid_at: NOW.to_string(),
                },
                1,
            )
            .await,
        Err(StoreError::SubjectErased)
    ));
    assert!(matches!(
        store.store_trace(&context, trace(&context)).await,
        Err(StoreError::SubjectErased)
    ));
}
