use memphant_core::{
    EmbeddingProvider, EmbeddingRow, FixedClock, InMemoryStore, MemoryStore, MutationClaim,
    MutationClaimOutcome, MutationLedgerStore, MutationVerb, StubEmbedding, embedding_profile_for,
    recall,
};
use memphant_types::{
    ActorId, ContextBindingAccessPolicy, ContextBindingAgentRef, ContextBindingEntityRef,
    ContextBindingRequest, ContextBindingScopeRef, MemoryKind, NewEpisode, NewMemoryUnit,
    NewResource, RecallMode, RecallRequest, RecallTime, ReflectJob, ReflectJobKind, ResourceKind,
    TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

fn binding(
    subject: &str,
    scope: &str,
    scope_parent: Option<&str>,
    agent: &str,
    agent_parent: Option<&str>,
    access_policies: Vec<ContextBindingAccessPolicy>,
) -> ContextBindingRequest {
    ContextBindingRequest {
        subject: ContextBindingEntityRef {
            external_ref: subject.to_string(),
            kind: "user".to_string(),
        },
        actor: ContextBindingEntityRef {
            external_ref: subject.to_string(),
            kind: "user".to_string(),
        },
        scope: ContextBindingScopeRef {
            external_ref: scope.to_string(),
            kind: "memory".to_string(),
            parent_external_ref: scope_parent.map(str::to_string),
        },
        agent_node: ContextBindingAgentRef {
            external_ref: agent.to_string(),
            parent_external_ref: agent_parent.map(str::to_string),
        },
        access_policies,
    }
}

fn grant(source_scope: &str, source_agent: &str, kind: MemoryKind) -> ContextBindingAccessPolicy {
    ContextBindingAccessPolicy::Grant {
        source_scope_external_ref: source_scope.to_string(),
        source_agent_node_external_ref: source_agent.to_string(),
        kind,
    }
}

fn inherit(source_scope: &str, source_agent: &str, kind: MemoryKind) -> ContextBindingAccessPolicy {
    ContextBindingAccessPolicy::Inherit {
        source_scope_external_ref: source_scope.to_string(),
        source_agent_node_external_ref: source_agent.to_string(),
        kind,
    }
}

#[tokio::test]
async fn exact_agent_pairs_deny_siblings_until_explicit_grant_across_all_channels() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let root = store
        .resolve_context_binding(
            tenant,
            "root".to_string(),
            binding("user:1", "scope:root", None, "agent:root", None, vec![]),
        )
        .await
        .unwrap();
    let source = store
        .resolve_context_binding(
            tenant,
            "source".to_string(),
            binding(
                "user:1",
                "scope:root",
                None,
                "agent:source",
                Some("agent:root"),
                vec![],
            ),
        )
        .await
        .unwrap();
    let target_request = binding(
        "user:1",
        "scope:root",
        None,
        "agent:target",
        Some("agent:root"),
        vec![],
    );
    let target = store
        .resolve_context_binding(tenant, "target".to_string(), target_request.clone())
        .await
        .unwrap();
    assert_eq!(
        source.scope_id, target.scope_id,
        "shared scopes remain valid"
    );

    let source_context = store
        .resolve_memory_context(
            tenant,
            source.subject_id,
            source.actor_id,
            source.scope_id,
            source.agent_node_id,
        )
        .await
        .unwrap();
    let mut tx = store.begin(&source_context).await.unwrap();
    let resource_id = store
        .stage_resource(
            &mut tx,
            NewResource {
                tenant_id: tenant,
                data_subject_id: root.subject_id,
                scope_id: source.scope_id,
                actor_id: root.actor_id,
                agent_node_id: source.agent_node_id,
                subject_generation: source.subject_generation,
                uri: "memphant://private-resource".to_string(),
                kind: ResourceKind::Document,
                content_hash: "sha256:private-resource".to_string(),
                mime_type: "text/plain".to_string(),
                revision: None,
                body: Some("source sibling private resource".to_string()),
                source_ref: "test:private-resource".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                acl: memphant_types::ResourceAcl::default(),
            },
        )
        .await
        .unwrap();
    let unit_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: tenant,
                data_subject_id: root.subject_id,
                scope_id: source.scope_id,
                agent_node_id: source.agent_node_id,
                subject_generation: 0,
                kind: MemoryKind::Resource,
                state: UnitState::Active,
                fact_key: Some("private_resource".to_string()),
                predicate: None,
                body: "source sibling private resource".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(root.actor_id),
                source_kind: Some("user".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .unwrap();
    store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: tenant,
                data_subject_id: root.subject_id,
                scope_id: source.scope_id,
                agent_node_id: source.agent_node_id,
                subject_generation: 0,
                actor_id: root.actor_id,
                source_kind: "user".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "source-private-episode".to_string(),
                body: "source sibling private episode".to_string(),
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();

    let target_context = store
        .resolve_memory_context(
            tenant,
            target.subject_id,
            target.actor_id,
            target.scope_id,
            target.agent_node_id,
        )
        .await
        .unwrap();
    let time = RecallTime {
        evaluated_at: CLOCK.0.to_string(),
        transaction_as_of: CLOCK.0.to_string(),
        valid_at: CLOCK.0.to_string(),
    };
    assert!(
        store
            .fetch_recall_candidates(&target_context, &[], &[], &time, 10)
            .await
            .unwrap()
            .is_empty()
    );
    assert!(
        store
            .fetch_episodes_for_scope(&target_context, 10)
            .await
            .unwrap()
            .is_empty()
    );
    assert!(
        store
            .fetch_resource(&target_context, resource_id)
            .await
            .unwrap()
            .is_none()
    );
    let source_context = store
        .resolve_memory_context(
            tenant,
            source.subject_id,
            source.actor_id,
            source.scope_id,
            source.agent_node_id,
        )
        .await
        .unwrap();
    assert!(
        store
            .fetch_resource(&source_context, resource_id)
            .await
            .unwrap()
            .is_some()
    );

    let embedder = StubEmbedding::default();
    let profile = embedding_profile_for(&embedder);
    let vector = embedder
        .embed(&["source sibling private resource".to_string()])
        .unwrap()
        .remove(0);
    store
        .upsert_embedding_profile(tenant, profile.clone())
        .await
        .unwrap();
    store
        .upsert_embeddings(
            &source_context,
            vec![EmbeddingRow {
                memory_unit_id: unit_id,
                embedding_profile_id: profile.id,
                vec: vector.clone(),
            }],
        )
        .await
        .unwrap();
    assert!(
        store
            .fetch_vector_candidates(&target_context, &vector, profile.id, &time, 10)
            .await
            .unwrap()
            .is_empty()
    );

    let mut granted_request = target_request;
    granted_request.access_policies = vec![
        grant("scope:root", "agent:source", MemoryKind::Resource),
        grant("scope:root", "agent:source", MemoryKind::Episodic),
    ];
    let granted = store
        .resolve_context_binding(tenant, "target".to_string(), granted_request)
        .await
        .unwrap();
    let granted_context = store
        .resolve_memory_context(
            tenant,
            granted.subject_id,
            granted.actor_id,
            granted.scope_id,
            granted.agent_node_id,
        )
        .await
        .unwrap();
    assert_eq!(
        store
            .fetch_recall_candidates(&granted_context, &[], &[], &time, 10)
            .await
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        store
            .fetch_vector_candidates(&granted_context, &vector, profile.id, &time, 10)
            .await
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        store
            .fetch_episodes_for_scope(&granted_context, 10)
            .await
            .unwrap()
            .len(),
        1
    );
}

#[tokio::test]
async fn stale_generation_reflect_jobs_are_never_claimed() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let binding = store
        .resolve_context_binding(
            tenant,
            "current".to_string(),
            binding("user:stale", "scope:root", None, "agent:root", None, vec![]),
        )
        .await
        .unwrap();
    let context = store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .unwrap();
    // Enqueue a reflect job legitimately at the subject's current generation N.
    let mut tx = store.begin(&context).await.unwrap();
    let episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: tenant,
                data_subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                actor_id: binding.actor_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "stale-job".to_string(),
                body: "must not compile stale work".to_string(),
            },
        )
        .await
        .unwrap();
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: tenant,
                data_subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                actor_id: binding.actor_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                episode_id: Some(episode.episode_id),
                resource_id: None,
                kind: ReflectJobKind::ReflectEpisode,
                compiler_version: "stale-test".to_string(),
                subject: None,
                predicate: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();

    // The job was legitimately enqueued at the subject's current generation N.
    assert_eq!(store.reflect_jobs(tenant).len(), 1);

    // Advance the subject to generation N+1 through the real erasure API.
    let mut erasure = store.begin(&context).await.unwrap();
    assert_eq!(
        store
            .stage_mutation_claim(
                &mut erasure,
                MutationClaim::new(
                    &context,
                    MutationVerb::EraseSubject,
                    "erase-stale",
                    [MutationVerb::EraseSubject as u8; 32],
                )
                .unwrap(),
            )
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    let receipt = store.stage_subject_erasure(&mut erasure).await.unwrap();
    assert_eq!(receipt.generation, binding.subject_generation + 1);
    store.commit(erasure).await.unwrap();

    // The generation-N job must never be claimed now that the subject is at N+1.
    assert!(
        store
            .claim_reflect_jobs(memphant_core::JobFilter::default(), 10)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn inherit_is_l0_scope_ancestor_only_and_cross_subject_sources_fail_closed() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    store
        .resolve_context_binding(
            tenant,
            "root".to_string(),
            binding("user:1", "scope:root", None, "agent:root", None, vec![]),
        )
        .await
        .unwrap();
    let l0 = store
        .resolve_context_binding(
            tenant,
            "l0-child".to_string(),
            binding(
                "user:1",
                "scope:child",
                Some("scope:root"),
                "agent:l0-child",
                None,
                vec![inherit("scope:root", "agent:root", MemoryKind::Semantic)],
            ),
        )
        .await
        .unwrap();
    assert_eq!(l0.agent_level, 0);

    let l1 = store
        .resolve_context_binding(
            tenant,
            "l1-child".to_string(),
            binding(
                "user:1",
                "scope:child-2",
                Some("scope:root"),
                "agent:l1-child",
                Some("agent:root"),
                vec![inherit("scope:root", "agent:root", MemoryKind::Episodic)],
            ),
        )
        .await;
    assert!(matches!(l1, Err(memphant_core::StoreError::Conflict(_))));

    let cross_subject = store
        .resolve_context_binding(
            tenant,
            "other-user".to_string(),
            binding(
                "user:2",
                "scope:other",
                None,
                "agent:other",
                None,
                vec![grant("scope:root", "agent:root", MemoryKind::Semantic)],
            ),
        )
        .await;
    assert!(matches!(
        cross_subject,
        Err(memphant_core::StoreError::NotFound(
            "access policy source context"
        ))
    ));
}

#[tokio::test]
async fn traces_record_policy_revision_bind_actor_and_survive_policy_updates() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let response = store
        .resolve_context_binding(
            tenant,
            "root".to_string(),
            binding(
                "user:trace",
                "scope:trace",
                None,
                "agent:trace",
                None,
                vec![],
            ),
        )
        .await
        .unwrap();
    let context = store
        .resolve_memory_context(
            tenant,
            response.subject_id,
            response.actor_id,
            response.scope_id,
            response.agent_node_id,
        )
        .await
        .unwrap();
    let recalled = recall(
        &store,
        RecallRequest {
            context: context.clone(),
            query: "nothing".to_string(),
            k: 3,
            budget_tokens: 128,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: false,
            context_packing_abstention_enabled: false,
            procedure_recall_enabled: false,
            decay_enabled: false,
            engine_version: "trace-policy-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    let stored = store
        .trace_by_id(&context, recalled.trace_id)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(stored.policy_revision, context.policy_revision);

    let mut changed_policy = context.clone();
    changed_policy.policy_revision = "new-current-policy".to_string();
    assert!(
        store
            .trace_by_id(&changed_policy, recalled.trace_id)
            .await
            .unwrap()
            .is_some(),
        "historical traces remain readable after policy replacement"
    );
    let mut wrong_actor = context;
    wrong_actor.actor_id = ActorId::new();
    // The strict context contract validates the binding before the actor-scoped
    // trace lookup, so a hand-mutated foreign actor must itself be a registered
    // context; ownership still fails closed, yielding None rather than the trace.
    store.seed_context_binding(&wrong_actor);
    assert!(
        store
            .trace_by_id(&wrong_actor, recalled.trace_id)
            .await
            .unwrap()
            .is_none()
    );
}
