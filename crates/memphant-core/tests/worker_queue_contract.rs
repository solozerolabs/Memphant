use memphant_core::{
    EvidenceSlice, InMemoryStore, JobFilter, MemoryStore, ReflectJobResult,
    StructuredExtractionPacket, StructuredSourceKind, StructuredStateProviderIdentity,
    StructuredStateRequest, structured_extraction_receipt_sha256, structured_input_manifest_sha256,
};
use memphant_types::{
    ActorId, AgentNodeId, ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest,
    ContextBindingScopeRef, NewEpisode, ReflectJob, ReflectJobKind, ScopeId, SubjectId, TenantId,
    TrustLevel,
};

fn prepared_fixture() -> (
    Vec<StructuredStateRequest>,
    StructuredStateProviderIdentity,
    Vec<StructuredExtractionPacket>,
) {
    let identity = StructuredStateProviderIdentity {
        model: "test/prepared".to_string(),
        prompt_hash: "prompt".to_string(),
        schema_hash: "schema".to_string(),
    };
    let batches = ["first", "second"]
        .into_iter()
        .enumerate()
        .map(|(batch_index, body)| StructuredStateRequest {
            source_kind: StructuredSourceKind::Episode,
            source_body_sha256: "1".repeat(64),
            batch_index,
            evidence_slices: vec![EvidenceSlice {
                id: format!("slice-{batch_index}"),
                body: body.to_string(),
                source_span: format!("{batch_index}-{}", batch_index + body.len()),
            }],
        })
        .collect::<Vec<_>>();
    let packets = batches
        .iter()
        .map(|batch| StructuredExtractionPacket {
            batch_index: batch.batch_index,
            receipt_sha256: structured_extraction_receipt_sha256(&identity, batch, &[]).unwrap(),
            observations: Vec::new(),
        })
        .collect();
    (batches, identity, packets)
}

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
    let (batches, identity, packets) = prepared_fixture();
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
            .store_prepared_structured_state(token, &batches, &identity, packets.clone())
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
            .fetch_prepared_structured_state(&claim, &batches, &identity)
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
        .store_prepared_structured_state(&claim, &batches, &identity, packets.clone())
        .await
        .unwrap();
    assert_eq!(
        store
            .fetch_prepared_structured_state(&claim, &batches, &identity)
            .await
            .unwrap(),
        Some(packets.clone())
    );
    let mut reordered_batches = batches.clone();
    reordered_batches.swap(0, 1);
    assert!(
        store
            .fetch_prepared_structured_state(&claim, &reordered_batches, &identity)
            .await
            .is_err(),
        "prepared state must reject a reordered input manifest"
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
            .fetch_prepared_structured_state(&reclaimed, &batches, &identity)
            .await
            .unwrap(),
        Some(packets),
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
fn prepared_result_serialization_contains_only_source_neutral_packets() {
    let (batches, identity, packets) = prepared_fixture();
    let result = ReflectJobResult::Prepared {
        input_manifest_sha256: structured_input_manifest_sha256(&batches).unwrap(),
        extraction_packets: packets.clone(),
    };
    let value = serde_json::to_value(result).unwrap();
    assert_eq!(
        value["input_manifest_sha256"],
        structured_input_manifest_sha256(&batches).unwrap()
    );
    assert_eq!(value["extraction_packets"][0]["batch_index"], 0);
    assert_eq!(
        value["extraction_packets"][0]["receipt_sha256"],
        structured_extraction_receipt_sha256(&identity, &batches[0], &[]).unwrap()
    );
    assert!(value.get("projections").is_none());
}

#[tokio::test]
async fn in_memory_prepared_packets_reject_deleted_inserted_or_swapped_receipts() {
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
                source_ref: "test:packet-tamper".to_string(),
                observed_at: "2026-07-15T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "packet-tamper".to_string(),
                body: "first second".to_string(),
            },
        )
        .await
        .unwrap();
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                episode_id: Some(episode.episode_id),
                kind: ReflectJobKind::ReflectEpisode,
                compiler_version: "packet-tamper".to_string(),
                ..scope_job(&context)
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
    let (batches, identity, packets) = prepared_fixture();

    let mut mutations = vec![packets[..1].to_vec(), {
        let mut inserted = packets.clone();
        inserted.push(packets[1].clone());
        inserted
    }];
    let mut swapped = packets.clone();
    swapped.swap(0, 1);
    mutations.push(swapped);
    for tampered in mutations {
        assert!(
            store
                .store_prepared_structured_state(&claim, &batches, &identity, tampered)
                .await
                .is_err()
        );
    }
}
