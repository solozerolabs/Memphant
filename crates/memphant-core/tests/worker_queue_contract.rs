use memphant_core::{InMemoryStore, JobFilter, MemoryStore, ReflectJobResult};
use memphant_types::{
    ActorId, AgentNodeId, ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest,
    ContextBindingScopeRef, NewEpisode, ReflectJob, ReflectJobKind, ScopeId, SubjectId, TenantId,
    TrustLevel,
};

const INPUT_MANIFEST_SHA256: &str =
    "1111111111111111111111111111111111111111111111111111111111111111";
const EXTRACTION_RECEIPT_SHA256: &str =
    "2222222222222222222222222222222222222222222222222222222222222222";

fn scope_job(context: &memphant_types::ResolvedMemoryContext) -> ReflectJob {
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
        compiler_version: memphant_types::COMPILER_VERSION.to_string(),
        subject: None,
        predicate: None,
    }
}

#[tokio::test]
async fn scope_jobs_are_fresh_strict_ordered_barriers() {
    let store = InMemoryStore::default();
    let context = bound_context(&store, TenantId::new()).await;
    let mut tx = store.begin(&context).await.unwrap();
    let episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "test:barrier".to_string(),
                observed_at: "2026-07-15T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "scope-barrier".to_string(),
                body: "source before barrier".to_string(),
            },
        )
        .await
        .unwrap();
    let source_id = store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                episode_id: Some(episode.episode_id),
                kind: ReflectJobKind::ReflectEpisode,
                ..scope_job(&context)
            },
        )
        .await
        .unwrap();
    let first_scope = store
        .enqueue_reflect(&mut tx, scope_job(&context))
        .await
        .unwrap();
    let second_scope = store
        .enqueue_reflect(&mut tx, scope_job(&context))
        .await
        .unwrap();
    assert_ne!(first_scope, second_scope);

    let mut malformed = scope_job(&context);
    malformed.episode_id = Some(episode.episode_id);
    assert!(store.enqueue_reflect(&mut tx, malformed).await.is_err());
    let mut malformed = scope_job(&context);
    malformed.kind = ReflectJobKind::ReflectEpisode;
    assert!(store.enqueue_reflect(&mut tx, malformed).await.is_err());
    let mut malformed = scope_job(&context);
    malformed.kind = ReflectJobKind::ReflectResource;
    malformed.episode_id = Some(episode.episode_id);
    malformed.resource_id = Some(memphant_types::ResourceId::new());
    assert!(store.enqueue_reflect(&mut tx, malformed).await.is_err());
    store.commit(tx).await.unwrap();

    let source = store
        .claim_reflect_jobs(JobFilter::default(), 1)
        .await
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(source.job.id, source_id);
    assert!(
        store
            .claim_reflect_jobs(JobFilter::default(), 10)
            .await
            .unwrap()
            .is_empty(),
        "an active source must block later jobs in its lane"
    );
    store.complete_reflect_job(&source).await.unwrap();
    let next = store
        .claim_reflect_jobs(JobFilter::default(), 1)
        .await
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(next.job.id, first_scope);
}

async fn bound_context(
    store: &InMemoryStore,
    tenant: TenantId,
) -> memphant_types::ResolvedMemoryContext {
    let binding = store
        .resolve_context_binding(
            tenant,
            "queue-contract".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "subject:queue".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "actor:queue".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "scope:queue".to_string(),
                    kind: "memory".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "agent:queue".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .unwrap();
    store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn episode_dedup_replays_but_rejects_cross_context_identity() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = bound_context(&store, tenant).await;
    let episode = NewEpisode {
        tenant_id: tenant,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_kind: "user".to_string(),
        source_ref: "test:same-content".to_string(),
        observed_at: "2026-07-15T00:00:00Z".to_string(),
        source_trust: TrustLevel::TrustedUser,
        dedup_key: "same-content".to_string(),
        body: "same content".to_string(),
    };
    let mut tx = store.begin(&context).await.unwrap();
    let first = store.stage_episode(&mut tx, episode.clone()).await.unwrap();
    let replay = store.stage_episode(&mut tx, episode.clone()).await.unwrap();
    let mut other_actor = episode.clone();
    other_actor.actor_id = ActorId::new();
    assert!(store.stage_episode(&mut tx, other_actor).await.is_err());
    let mut other_agent = episode.clone();
    other_agent.agent_node_id = memphant_types::AgentNodeId::new();
    assert!(store.stage_episode(&mut tx, other_agent).await.is_err());
    let mut other_generation = episode;
    other_generation.subject_generation += 1;
    assert!(
        store
            .stage_episode(&mut tx, other_generation)
            .await
            .is_err()
    );

    assert_eq!(first.episode_id, replay.episode_id);
    assert!(replay.dedup.matched);
}

#[tokio::test]
async fn enqueue_replay_returns_persisted_id_without_reviving_completed_job() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = bound_context(&store, tenant).await;
    let mut tx = store.begin(&context).await.unwrap();
    let episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "test:queue-once".to_string(),
                observed_at: "2026-07-15T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "queue-once".to_string(),
                body: "queue once".to_string(),
            },
        )
        .await
        .unwrap();
    let job = ReflectJob {
        tenant_id: tenant,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        episode_id: Some(episode.episode_id),
        resource_id: None,
        kind: ReflectJobKind::ReflectEpisode,
        compiler_version: "queue-contract".to_string(),
        subject: None,
        predicate: None,
    };
    let first = store.enqueue_reflect(&mut tx, job.clone()).await.unwrap();
    let replay = store.enqueue_reflect(&mut tx, job.clone()).await.unwrap();
    assert_eq!(first, replay);
    store.commit(tx).await.unwrap();

    let claimed = store
        .claim_reflect_jobs(JobFilter::default(), 1)
        .await
        .unwrap()
        .pop()
        .unwrap();
    store.complete_reflect_job(&claimed).await.unwrap();

    let mut replay_tx = store.begin(&context).await.unwrap();
    assert_eq!(
        store.enqueue_reflect(&mut replay_tx, job).await.unwrap(),
        first
    );
    store.commit(replay_tx).await.unwrap();
    assert!(
        store
            .claim_reflect_jobs(JobFilter::default(), 1)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn post_claim_operations_require_the_exact_claim_token() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = bound_context(&store, tenant).await;
    let mut tx = store.begin(&context).await.unwrap();
    let episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "test:exact-claim".to_string(),
                observed_at: "2026-07-15T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "exact-claim".to_string(),
                body: "exact claim token".to_string(),
            },
        )
        .await
        .unwrap();
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                episode_id: Some(episode.episode_id),
                resource_id: None,
                kind: ReflectJobKind::ReflectEpisode,
                compiler_version: "exact-claim".to_string(),
                subject: None,
                predicate: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();

    let claim = store
        .claim_reflect_jobs(JobFilter::default(), 1)
        .await
        .unwrap()
        .pop()
        .unwrap();
    let mut forged = Vec::new();
    for mutation in 0..6 {
        let mut token = claim.clone();
        match mutation {
            0 => token.job.tenant_id = TenantId::new(),
            1 => token.job.data_subject_id = SubjectId::new(),
            2 => token.job.subject_generation += 1,
            3 => token.job.scope_id = ScopeId::new(),
            4 => token.job.agent_node_id = AgentNodeId::new(),
            _ => token.job.actor_id = ActorId::new(),
        }
        forged.push(token);
    }

    for token in &forged {
        store
            .store_prepared_structured_state(
                token,
                INPUT_MANIFEST_SHA256.to_string(),
                vec![EXTRACTION_RECEIPT_SHA256.to_string()],
                Vec::new(),
            )
            .await
            .unwrap();
        store
            .release_reflect_job(token, 0, "forged release".to_string())
            .await
            .unwrap();
    }
    store
        .fail_reflect_job(&forged[0], "forged failure".to_string())
        .await
        .unwrap();
    store.complete_reflect_job(&forged[1]).await.unwrap();

    assert_eq!(
        store
            .fetch_prepared_structured_state(&claim, INPUT_MANIFEST_SHA256)
            .await
            .unwrap(),
        None
    );
    assert!(
        store
            .claim_reflect_jobs(JobFilter::default(), 1)
            .await
            .unwrap()
            .is_empty(),
        "forged operations must leave the real claim running"
    );

    store
        .store_prepared_structured_state(
            &claim,
            INPUT_MANIFEST_SHA256.to_string(),
            vec![EXTRACTION_RECEIPT_SHA256.to_string()],
            Vec::new(),
        )
        .await
        .unwrap();
    assert_eq!(
        store
            .fetch_prepared_structured_state(&claim, INPUT_MANIFEST_SHA256)
            .await
            .unwrap(),
        Some(Vec::new())
    );
    assert!(
        store
            .fetch_prepared_structured_state(
                &claim,
                "3333333333333333333333333333333333333333333333333333333333333333",
            )
            .await
            .is_err(),
        "prepared state must reject an input-manifest hash mismatch"
    );
    store
        .release_reflect_job(&claim, 0, "retry".to_string())
        .await
        .unwrap();
    let reclaimed = store
        .claim_reflect_jobs(JobFilter::default(), 1)
        .await
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(reclaimed.attempts, claim.attempts + 1);
    assert_eq!(
        store
            .fetch_prepared_structured_state(&reclaimed, INPUT_MANIFEST_SHA256)
            .await
            .unwrap(),
        Some(Vec::new()),
        "prepared state survives a valid release and reclaim"
    );
    store.complete_reflect_job(&claim).await.unwrap();
    assert!(
        store
            .claim_reflect_jobs(JobFilter::default(), 1)
            .await
            .unwrap()
            .is_empty(),
        "a stale completion must not release or complete the current attempt"
    );
    store.complete_reflect_job(&reclaimed).await.unwrap();
}

#[test]
fn prepared_result_serialization_binds_input_and_extraction_receipts() {
    let result = ReflectJobResult::Prepared {
        input_manifest_sha256: INPUT_MANIFEST_SHA256.to_string(),
        extraction_receipt_sha256s: vec![EXTRACTION_RECEIPT_SHA256.to_string()],
        projections: Vec::new(),
    };
    let value = serde_json::to_value(result).unwrap();
    assert_eq!(value["input_manifest_sha256"], INPUT_MANIFEST_SHA256);
    assert_eq!(
        value["extraction_receipt_sha256s"],
        serde_json::json!([EXTRACTION_RECEIPT_SHA256])
    );
}
