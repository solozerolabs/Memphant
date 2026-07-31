//! Shared `MemoryStore` contract suite.
//!
//! One set of scenarios, generic over any `MemoryStore`, so the SAME assertions
//! run against `InMemoryStore` (default, in `memphant-core`) and `PgStore`
//! (DB-gated `#[ignore]`, in `memphant-store-postgres`). Before this crate the
//! two stores had hand-mirrored, separate test files — so a divergence in one
//! trait method (e.g. `fetch_recall_candidates` capping rows on PgStore only)
//! passed every in-memory test while the real backend silently misbehaved.
//! A scenario here fails on at least one store the moment the two diverge.
//!
//! Only the two things that genuinely differ between backends are abstracted
//! behind [`StoreHarness`]: obtaining a live store, and minting a tenant that
//! satisfies the backend's FK constraints. Everything else goes through the
//! `MemoryStore` trait and the store-generic core functions.

use std::future::Future;
use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{
    EmbeddingProfileRow, EmbeddingRow, FixedClock, JobFilter, MemoryStore, MutationClaim,
    MutationClaimOutcome, MutationLedgerStore, MutationVerb, NoopEmbedding, StoreError,
    build_deep_workspace, correct_memory, derive_fact_key, forget_memory, recall, record_mark,
    reflect_recorded, retain_episode, retain_resource,
};
use memphant_types::{
    ActorId, AgentNodeId, ContextBindingAccessPolicy, ContextBindingAgentRef,
    ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef, CorrectRequest,
    CorrectSelector, CorrectionPayload, DeepSnapshotSourceKind, EpisodeId, ForgetRequest,
    ForgetSelector, JobId, MarkOutcome, MarkRequest, MemoryKind, NewEpisode, NewMemoryUnit,
    NewResource, RecallContextItem, RecallMode, RecallRequest, RecallTime, ReflectCandidate,
    ReflectInput, ResolvedMemoryContext, ResolvedMemorySource, ResourceAcl, ResourceExtractorState,
    ResourceId, ResourceKind, ResourceProtectedCategory, RetainEpisodeHttpRequest, RetainPayload,
    RetainRequest, RetainResourceRequest, RetainUnitPayload, RetrievalTrace, ScopeId, SubjectId,
    TenantId, TraceId, TrustLevel, UnitId, UnitState,
};
use uuid::Uuid;

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

/// The backend-specific seam. A scenario only needs a live store and a way to
/// mint a fresh, backend-valid tenant; both are trivial for `InMemoryStore` and
/// require `create_tenant` for `PgStore`.
pub trait StoreHarness {
    type Store: MutationLedgerStore + Clone + 'static;
    fn store(&self) -> &Self::Store;
    fn fresh_tenant(&self) -> impl Future<Output = TenantId> + Send;
}

fn service<S: MutationLedgerStore + Clone + 'static>(store: &S) -> MemoryService<S> {
    MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
}

fn subject_for(tenant: TenantId) -> SubjectId {
    SubjectId::from_u128(tenant.as_uuid().as_u128())
}

fn agent_for(scope: ScopeId) -> AgentNodeId {
    AgentNodeId::from_u128(scope.as_uuid().as_u128())
}

pub fn resolved_context(tenant: TenantId, scope: ScopeId, actor: ActorId) -> ResolvedMemoryContext {
    ResolvedMemoryContext {
        tenant_id: tenant,
        data_subject_id: subject_for(tenant),
        actor_id: actor,
        actor_trust: memphant_types::TrustLevel::TrustedUser,
        scope_id: scope,
        agent_node_id: agent_for(scope),
        agent_level: 0,
        subject_generation: 0,
        policy_revision: "test-policy".to_string(),
        sources_by_kind: MemoryKind::ALL
            .into_iter()
            .map(|kind| {
                (
                    kind,
                    vec![ResolvedMemorySource {
                        scope_id: scope,
                        agent_node_id: agent_for(scope),
                    }],
                )
            })
            .collect(),
    }
}

/// Creates and resolves an authoritative context binding for store-backed tests.
pub async fn bind_context_request<S: MemoryStore>(
    store: &S,
    tenant: TenantId,
    client_ref: impl Into<String>,
    request: ContextBindingRequest,
) -> ResolvedMemoryContext {
    let binding = store
        .resolve_context_binding(tenant, client_ref.into(), request)
        .await
        .expect("create explicit memory context binding");
    store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve explicit memory context binding")
}

/// Worker-tick filter pinned to the context's tenant, for harnesses whose
/// store is shared with concurrently running scenarios.
pub fn tenant_filter(context: &ResolvedMemoryContext) -> JobFilter {
    JobFilter {
        tenant: Some(context.tenant_id),
        scope: None,
    }
}

pub async fn bind_context<S: MemoryStore>(store: &S, tenant: TenantId) -> ResolvedMemoryContext {
    let suffix = Uuid::now_v7().to_string();
    bind_context_request(
        store,
        tenant,
        format!("contract:{suffix}"),
        ContextBindingRequest {
            subject: ContextBindingEntityRef {
                external_ref: format!("subject:{suffix}"),
                kind: "user".to_string(),
            },
            actor: ContextBindingEntityRef {
                external_ref: format!("actor:{suffix}"),
                kind: "user".to_string(),
            },
            scope: ContextBindingScopeRef {
                external_ref: format!("scope:{suffix}"),
                kind: "user_root".to_string(),
                parent_external_ref: None,
            },
            agent_node: ContextBindingAgentRef {
                external_ref: format!("agent:{suffix}"),
                parent_external_ref: None,
            },
            access_policies: vec![],
        },
    )
    .await
}

fn retain_request(
    context: &ResolvedMemoryContext,
    body: &str,
    subject: Option<&str>,
) -> RetainRequest {
    RetainRequest {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_kind: "user".to_string(),
        source_ref: "testkit:episode".to_string(),
        observed_at: CLOCK.0.to_string(),
        source_trust: TrustLevel::TrustedUser,
        subject_hint: subject.map(str::to_string),
        subject: subject.map(str::to_string),
        predicate: subject.map(|_| "value".to_string()),
        body: body.to_string(),
        compiler_version: "compiler-contract".to_string(),
    }
}

fn recall_request(context: &ResolvedMemoryContext, query: &str) -> RecallRequest {
    RecallRequest {
        context: context.clone(),
        query: query.to_string(),
        k: 4,
        budget_tokens: 256,
        mode: RecallMode::Fast,
        include_beliefs: true,
        edge_expansion_enabled: true,
        context_packing_abstention_enabled: true,
        procedure_recall_enabled: true,
        decay_enabled: true,
        engine_version: "contract-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// A second `retain` of an identical episode collapses onto the first by dedup
/// key (bumping `observation_count`) while still leaving a reflect job pending,
/// so the new observation gets recompiled.
///
/// The exact pending-job COUNT is deliberately not asserted: it is store
/// implementation freedom (InMemoryStore pushes a fresh job per retain; PgStore
/// dedups the reflect job for the same episode). Both guarantee `>= 1`, which is
/// the actual contract — the new observation is recompiled either way. Pinning
/// it to a specific number would over-specify one backend, not test a contract.
pub async fn retain_episode_dedups_and_enqueues<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    let mut later = retain_request(&context, "Staging pins Node 24.15.0.", None);
    later.source_ref = "syndai:Episode:ABC".to_string();
    later.observed_at = "2030-06-01T00:00:00Z".to_string();
    let first = retain_episode(store, &context, later.clone())
        .await
        .expect("first retain");
    let mut earlier = later;
    earlier.observed_at = "2030-01-01T00:00:00Z".to_string();
    let second = retain_episode(store, &context, earlier)
        .await
        .expect("second retain");

    let mut distinct_case = retain_request(&context, "Staging pins Node 24.15.0.", None);
    distinct_case.source_ref = "syndai:episode:abc".to_string();
    retain_episode(store, &context, distinct_case)
        .await
        .expect("case-distinct opaque source retain");

    assert!(!first.dedup.matched);
    assert!(second.dedup.matched);
    assert_eq!(second.episode_id, first.episode_id);
    assert_eq!(second.dedup.observation_count, 2);

    let episodes = store
        .fetch_episodes_for_scope(&context, 10)
        .await
        .expect("episodes");
    assert_eq!(episodes.len(), 2, "case-distinct source refs never merge");
    let deduped = episodes
        .iter()
        .find(|episode| episode.id == first.episode_id)
        .expect("deduped episode");
    assert_eq!(deduped.observation_count, 2);
    assert_eq!(deduped.first_observed_at, "2030-01-01T00:00:00Z");
    assert_eq!(deduped.last_observed_at, "2030-06-01T00:00:00Z");
    assert!(
        store.pending_job_count(&context).await.expect("count") >= 1,
        "a deduped retain still leaves a reflect job pending to recompile the \
         new observation"
    );
}

/// A retained resource is registered as a pointer (pre-extraction) and enqueues
/// a reflect job.
pub async fn retain_resource_registers_and_enqueues<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    let retained = retain_resource(
        store,
        &context,
        RetainResourceRequest {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            uri: "https://example.test/runbooks/deploy.md".to_string(),
            source_ref: "testkit:resource".to_string(),
            observed_at: CLOCK.0.to_string(),
            kind: None,
            content_hash: "sha256:deploy-runbook".to_string(),
            mime_type: "text/markdown".to_string(),
            revision: None,
            body: Some("Deploy runbook body: roll forward, never force-push.".to_string()),
            source_trust: TrustLevel::WebContent,
            compiler_version: "compiler-contract".to_string(),
        },
    )
    .await
    .expect("retain resource");

    let resource = store
        .fetch_resource(&context, retained.resource_id)
        .await
        .expect("fetch")
        .expect("resource exists");
    assert_eq!(resource.id, retained.resource_id);
    assert_eq!(resource.acl, ResourceAcl::default());
    assert!(resource.acl.is_deep_eligible());
    assert_eq!(resource.extractor_state, ResourceExtractorState::Registered);
    assert_eq!(store.pending_job_count(&context).await.expect("count"), 1);
    assert!(
        store
            .pending_worker_job_count()
            .await
            .expect("worker queue count")
            >= 1,
        "the fleet-worker queue count includes the tenant-scoped resource job"
    );
}

/// Resource ACLs are persisted exactly by both stores. Public retain above
/// remains default-empty; this direct store contract covers the dormant typed
/// ACL read path without exposing ACL authoring on the public API.
pub async fn resource_acl_round_trips_empty_and_non_empty<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    for acl in [
        ResourceAcl::default(),
        ResourceAcl {
            scopes: vec![context.scope_id],
            trust_floor: Some(TrustLevel::VerifiedTool),
            protected: Some(ResourceProtectedCategory::PersonalIdentity),
        },
    ] {
        let mut tx = store.begin(&context).await.expect("begin");
        let resource_id = store
            .stage_resource(
                &mut tx,
                NewResource {
                    tenant_id: tenant,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    actor_id: context.actor_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    uri: format!(
                        "memphant://resource/{resource_id}",
                        resource_id = Uuid::now_v7()
                    ),
                    source_ref: "testkit:resource-acl".to_string(),
                    observed_at: CLOCK.0.to_string(),
                    kind: ResourceKind::Document,
                    content_hash: format!("sha256:{}", Uuid::now_v7()),
                    mime_type: "text/plain".to_string(),
                    revision: None,
                    body: Some("ACL round-trip body".to_string()),
                    source_trust: TrustLevel::TrustedUser,
                    acl: acl.clone(),
                },
            )
            .await
            .expect("stage resource");
        store.commit(tx).await.expect("commit");

        let stored = store
            .fetch_resource(&context, resource_id)
            .await
            .expect("fetch resource")
            .expect("resource exists");
        assert_eq!(stored.acl, acl);
        assert_eq!(stored.acl.is_deep_eligible(), stored.acl.is_empty());
    }
}

fn deep_time(transaction_as_of: &str) -> RecallTime {
    RecallTime {
        evaluated_at: transaction_as_of.to_string(),
        transaction_as_of: transaction_as_of.to_string(),
        valid_at: transaction_as_of.to_string(),
    }
}

async fn stage_deep_episode<S: MemoryStore>(
    store: &S,
    context: &ResolvedMemoryContext,
    source_body: &str,
    source_trust: TrustLevel,
    unit_state: UnitState,
    unit_trust: TrustLevel,
    transaction_rectangle: Option<(&str, &str)>,
) -> (EpisodeId, UnitId) {
    let mut tx = store.begin(context).await.expect("begin deep episode");
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
                source_ref: format!("testkit:deep:{}", Uuid::now_v7()),
                observed_at: CLOCK.0.to_string(),
                source_trust,
                dedup_key: Uuid::now_v7().to_string(),
                body: source_body.to_string(),
            },
        )
        .await
        .expect("stage deep episode");
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
                state: unit_state,
                fact_key: None,
                predicate: None,
                body: format!("Derived from {source_body}"),
                confidence: None,
                trust_level: unit_trust,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: "testkit:deep-unit".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_episode_id: Some(episode.episode_id),
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: transaction_rectangle.map(|(from, _)| from.to_string()),
                transaction_to: transaction_rectangle.map(|(_, to)| to.to_string()),
            },
        )
        .await
        .expect("stage deep episode unit");
    store.commit(tx).await.expect("commit deep episode");
    (episode.episode_id, unit_id)
}

async fn stage_deep_resource<S: MemoryStore>(
    store: &S,
    context: &ResolvedMemoryContext,
    source_body: &str,
    acl: ResourceAcl,
) -> (ResourceId, UnitId) {
    let mut tx = store.begin(context).await.expect("begin deep resource");
    let resource_id = store
        .stage_resource(
            &mut tx,
            NewResource {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                uri: format!("memphant://resource/{}", Uuid::now_v7()),
                source_ref: format!("testkit:deep:{}", Uuid::now_v7()),
                observed_at: CLOCK.0.to_string(),
                kind: ResourceKind::Document,
                content_hash: "sha256:untrusted-stored-value".to_string(),
                mime_type: "text/plain".to_string(),
                revision: None,
                body: Some(source_body.to_string()),
                source_trust: TrustLevel::TrustedUser,
                acl,
            },
        )
        .await
        .expect("stage deep resource");
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
                fact_key: None,
                predicate: None,
                body: format!("Derived from {source_body}"),
                confidence: None,
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: "testkit:deep-resource-unit".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_episode_id: None,
                source_resource_id: Some(resource_id),
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .expect("stage deep resource unit");
    store.commit(tx).await.expect("commit deep resource");
    (resource_id, unit_id)
}

/// The Deep read seam exports only raw sources with at least one independently
/// authorized, recallable direct-link unit. Every negative source body is
/// asserted against the final snapshot so a future query rewrite cannot turn
/// this into a metadata-only authorization test.
pub async fn deep_snapshot_is_authorized_stable_and_read_only<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    let (authorized_episode, authorized_episode_unit) = stage_deep_episode(
        store,
        &context,
        "Authorized episode body.",
        TrustLevel::TrustedUser,
        UnitState::Active,
        TrustLevel::TrustedUser,
        None,
    )
    .await;
    let (authorized_resource, authorized_resource_unit) = stage_deep_resource(
        store,
        &context,
        "Authorized resource body.",
        ResourceAcl::default(),
    )
    .await;
    let mut mixed_tx = store
        .begin(&context)
        .await
        .expect("begin mixed linked units");
    let linked_unit = |state: UnitState, label: &str| NewMemoryUnit {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        kind: MemoryKind::Semantic,
        state,
        fact_key: None,
        predicate: None,
        body: label.to_string(),
        confidence: None,
        trust_level: if state == UnitState::Quarantined {
            TrustLevel::Quarantined
        } else {
            TrustLevel::TrustedUser
        },
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(context.actor_id),
        source_kind: None,
        source_ref: "testkit:deep:mixed".to_string(),
        observed_at: CLOCK.0.to_string(),
        source_episode_id: Some(authorized_episode),
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: Vec::new(),
        valid_from: None,
        valid_to: None,
        transaction_from: None,
        transaction_to: None,
    };
    let second_authorized_unit = store
        .stage_memory_unit(
            &mut mixed_tx,
            linked_unit(UnitState::Validated, "Second authorized linked unit."),
        )
        .await
        .expect("stage second authorized linked unit");
    let stale_linked_unit = store
        .stage_memory_unit(
            &mut mixed_tx,
            linked_unit(UnitState::Expired, "Negative stale linked unit."),
        )
        .await
        .expect("stage stale linked unit");
    store
        .commit(mixed_tx)
        .await
        .expect("commit mixed linked units");

    stage_deep_episode(
        store,
        &context,
        "Negative quarantined-unit body.",
        TrustLevel::TrustedUser,
        UnitState::Quarantined,
        TrustLevel::Quarantined,
        None,
    )
    .await;
    stage_deep_episode(
        store,
        &context,
        "Negative quarantined-source body.",
        TrustLevel::Quarantined,
        UnitState::Active,
        TrustLevel::TrustedUser,
        None,
    )
    .await;
    stage_deep_episode(
        store,
        &context,
        "Negative non-live-unit body.",
        TrustLevel::TrustedUser,
        UnitState::Candidate,
        TrustLevel::TrustedUser,
        None,
    )
    .await;
    stage_deep_resource(
        store,
        &context,
        "Negative resource ACL body.",
        ResourceAcl {
            scopes: vec![context.scope_id],
            trust_floor: None,
            protected: None,
        },
    )
    .await;

    let (unit_forgotten_source, unit_to_forget) = stage_deep_episode(
        store,
        &context,
        "Negative forgotten memory-unit body.",
        TrustLevel::TrustedUser,
        UnitState::Active,
        TrustLevel::TrustedUser,
        None,
    )
    .await;
    forget_memory(
        store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: Some(unit_to_forget),
                episode_id: None,
                resource_id: None,
                scope_id: context.scope_id,
            },
            reason: "deep snapshot unit tombstone contract".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget memory unit");

    let mut bad_link_tx = store.begin(&context).await.expect("begin bad links");
    let dual_episode = store
        .stage_episode(
            &mut bad_link_tx,
            NewEpisode {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "testkit:deep:dual-episode".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: Uuid::now_v7().to_string(),
                body: "Negative dual-link episode body.".to_string(),
            },
        )
        .await
        .expect("stage dual-link episode");
    let dual_resource = store
        .stage_resource(
            &mut bad_link_tx,
            NewResource {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                uri: format!("memphant://resource/{}", Uuid::now_v7()),
                source_ref: "testkit:deep:dual-resource".to_string(),
                observed_at: CLOCK.0.to_string(),
                kind: ResourceKind::Document,
                content_hash: "sha256:dual".to_string(),
                mime_type: "text/plain".to_string(),
                revision: None,
                body: Some("Negative dual-link resource body.".to_string()),
                source_trust: TrustLevel::TrustedUser,
                acl: ResourceAcl::default(),
            },
        )
        .await
        .expect("stage dual-link resource");
    let dual_link_unit = store
        .stage_memory_unit(
            &mut bad_link_tx,
            NewMemoryUnit {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: None,
                predicate: None,
                body: "Negative dual-link unit.".to_string(),
                confidence: None,
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: "testkit:deep:dual".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_episode_id: Some(dual_episode.episode_id),
                source_resource_id: Some(dual_resource),
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .expect("stage dual-link unit");
    let no_link_unit = store
        .stage_memory_unit(
            &mut bad_link_tx,
            NewMemoryUnit {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: None,
                predicate: None,
                body: "Negative no-link unit.".to_string(),
                confidence: None,
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: "testkit:deep:no-link".to_string(),
                observed_at: CLOCK.0.to_string(),
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
        .expect("stage no-link unit");
    store.commit(bad_link_tx).await.expect("commit bad links");

    let mut no_unit_tx = store.begin(&context).await.expect("begin no-unit source");
    store
        .stage_episode(
            &mut no_unit_tx,
            NewEpisode {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "testkit:deep:no-unit".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: Uuid::now_v7().to_string(),
                body: "Negative no-eligible-unit body.".to_string(),
            },
        )
        .await
        .expect("stage no-unit source");
    store
        .commit(no_unit_tx)
        .await
        .expect("commit no-unit source");

    let (forgotten_episode, _) = stage_deep_episode(
        store,
        &context,
        "Negative forgotten episode body.",
        TrustLevel::TrustedUser,
        UnitState::Active,
        TrustLevel::TrustedUser,
        None,
    )
    .await;
    forget_memory(
        store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: None,
                episode_id: Some(forgotten_episode),
                resource_id: None,
                scope_id: context.scope_id,
            },
            reason: "deep snapshot contract".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget episode source");

    let (forgotten_resource, _) = stage_deep_resource(
        store,
        &context,
        "Negative forgotten resource body.",
        ResourceAcl::default(),
    )
    .await;
    forget_memory(
        store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: None,
                episode_id: None,
                resource_id: Some(forgotten_resource),
                scope_id: context.scope_id,
            },
            reason: "deep snapshot contract".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget resource source");

    let other_context = bind_context(store, tenant).await;
    stage_deep_episode(
        store,
        &other_context,
        "Negative sibling-agent body.",
        TrustLevel::TrustedUser,
        UnitState::Active,
        TrustLevel::TrustedUser,
        None,
    )
    .await;

    let pending_before = store
        .pending_job_count(&context)
        .await
        .expect("pending jobs before snapshot");
    let snapshot = store
        .fetch_deep_snapshot(&context, &deep_time(CLOCK.0))
        .await
        .expect("deep snapshot");
    let repeated = store
        .fetch_deep_snapshot(&context, &deep_time(CLOCK.0))
        .await
        .expect("repeat deep snapshot");
    assert_eq!(snapshot, repeated, "snapshot is stable and read-only");
    assert_eq!(
        pending_before,
        store
            .pending_job_count(&context)
            .await
            .expect("pending jobs after snapshot"),
        "snapshot performs no writes"
    );

    assert_eq!(snapshot.len(), 2);
    assert_eq!(snapshot[0].source_kind, DeepSnapshotSourceKind::Episode);
    assert_eq!(snapshot[0].source_id, authorized_episode.as_uuid());
    assert_eq!(
        snapshot[0].path,
        format!("episodes/{}.md", authorized_episode.as_uuid())
    );
    assert_eq!(
        snapshot[0].body_sha256,
        "fcf2595e49f470cf4e0b77c4f2332036b9da192aa8eae537a26f2a3d1ea0ea29"
    );
    let mut expected_episode_units = vec![authorized_episode_unit, second_authorized_unit];
    expected_episode_units.sort_unstable_by_key(|id| id.as_uuid());
    assert_eq!(snapshot[0].eligible_unit_ids(), expected_episode_units);
    assert!(
        snapshot[0]
            .bound_units
            .windows(2)
            .all(|units| units[0].id.as_uuid() < units[1].id.as_uuid())
    );
    assert!(!snapshot[0].eligible_unit_ids().contains(&stale_linked_unit));
    assert_eq!(snapshot[1].source_kind, DeepSnapshotSourceKind::Resource);
    assert_eq!(snapshot[1].source_id, authorized_resource.as_uuid());
    assert_eq!(
        snapshot[1].path,
        format!("resources/{}.md", authorized_resource.as_uuid())
    );
    assert_eq!(
        snapshot[1].body_sha256,
        "91c82d760ae9ce7627f3ca9202e1c0a6d2940540584cfd2fac364977b25e0a7b"
    );
    assert_eq!(
        snapshot[1].eligible_unit_ids(),
        vec![authorized_resource_unit]
    );
    assert_eq!(snapshot[1].bound_units[0].id, authorized_resource_unit);

    let exported_bodies: Vec<_> = snapshot.iter().map(|entry| entry.body.as_str()).collect();
    for forbidden in [
        "Negative quarantined-unit body.",
        "Negative quarantined-source body.",
        "Negative non-live-unit body.",
        "Negative resource ACL body.",
        "Negative forgotten memory-unit body.",
        "Negative dual-link episode body.",
        "Negative dual-link resource body.",
        "Negative no-eligible-unit body.",
        "Negative forgotten episode body.",
        "Negative forgotten resource body.",
        "Negative sibling-agent body.",
    ] {
        assert!(!exported_bodies.contains(&forbidden), "leaked {forbidden}");
    }
    let bound_ids: Vec<_> = snapshot
        .iter()
        .flat_map(|entry| entry.eligible_unit_ids())
        .collect();
    assert!(!bound_ids.contains(&dual_link_unit));
    assert!(!bound_ids.contains(&no_link_unit));
    assert_eq!(
        store
            .fetch_episode(&context, unit_forgotten_source)
            .await
            .expect("forgotten-unit source still readable")
            .expect("forgetting a unit does not delete its source")
            .body,
        "Negative forgotten memory-unit body."
    );

    let workspace = build_deep_workspace(&snapshot);
    assert_eq!(
        workspace
            .files
            .iter()
            .map(|file| file.path.as_str())
            .collect::<Vec<_>>(),
        vec![
            "WORKFLOW.md",
            "manifest.jsonl",
            "state/compiled_units.jsonl",
            snapshot[0].path.as_str(),
            snapshot[1].path.as_str(),
        ]
    );
    assert!(workspace.files[0].body.contains("read-only"));
    assert!(
        workspace.files[1]
            .body
            .contains(&authorized_episode.as_uuid().to_string())
    );
    assert!(
        workspace.files[1]
            .body
            .contains(&authorized_resource.as_uuid().to_string())
    );
    assert!(!workspace.manifest_sha256.is_empty());
    assert!(!workspace.workspace_sha256.is_empty());

    let mut semantic_only = context.clone();
    semantic_only.sources_by_kind.remove(&MemoryKind::Episodic);
    let semantic_snapshot = store
        .fetch_deep_snapshot(&semantic_only, &deep_time(CLOCK.0))
        .await
        .expect("semantic-only deep snapshot");
    assert!(
        semantic_snapshot
            .iter()
            .all(|entry| entry.source_kind != DeepSnapshotSourceKind::Episode),
        "a semantic unit grant cannot reveal its raw episode"
    );
    let mut no_resource_grant = context.clone();
    no_resource_grant
        .sources_by_kind
        .remove(&MemoryKind::Resource);
    let no_resource_snapshot = store
        .fetch_deep_snapshot(&no_resource_grant, &deep_time(CLOCK.0))
        .await
        .expect("no-resource-grant deep snapshot");
    assert!(
        no_resource_snapshot
            .iter()
            .all(|entry| entry.source_kind != DeepSnapshotSourceKind::Resource),
        "a semantic unit grant cannot reveal its raw resource"
    );

    let mut erasure = store
        .begin(&context)
        .await
        .expect("begin generation advance");
    assert_eq!(
        store
            .stage_mutation_claim(
                &mut erasure,
                MutationClaim::new(
                    &context,
                    MutationVerb::EraseSubject,
                    "deep-snapshot-prior-generation",
                    [MutationVerb::EraseSubject as u8; 32],
                )
                .expect("valid erasure claim"),
            )
            .await
            .expect("stage erasure claim"),
        MutationClaimOutcome::Execute
    );
    store
        .stage_subject_erasure(&mut erasure)
        .await
        .expect("advance subject generation");
    store
        .commit(erasure)
        .await
        .expect("commit generation advance");
    match store
        .fetch_deep_snapshot(&context, &deep_time(CLOCK.0))
        .await
    {
        Ok(snapshot) => assert!(
            snapshot.is_empty(),
            "prior-generation source bytes must not survive into Deep"
        ),
        Err(StoreError::StaleSubjectGeneration | StoreError::SubjectErased) => {}
        Err(error) => panic!("unexpected prior-generation snapshot error: {error}"),
    }
}

/// Closed correction rectangles remain reproducible: only the unit visible at
/// the requested transaction snapshot is bound to the raw source.
pub async fn deep_snapshot_binds_historical_rectangle_only<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;
    let (episode_id, historical_unit) = stage_deep_episode(
        store,
        &context,
        "Historical source body.",
        TrustLevel::TrustedUser,
        UnitState::Superseded,
        TrustLevel::TrustedUser,
        Some(("2029-01-01T00:00:00Z", "2031-01-01T00:00:00Z")),
    )
    .await;

    let mut tx = store
        .begin(&context)
        .await
        .expect("begin current correction");
    let current_unit = store
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
                fact_key: None,
                predicate: None,
                body: "Current derived correction.".to_string(),
                confidence: None,
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: "testkit:deep-current".to_string(),
                observed_at: "2031-01-01T00:00:00Z".to_string(),
                source_episode_id: Some(episode_id),
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: Some("2031-01-01T00:00:00Z".to_string()),
                transaction_to: None,
            },
        )
        .await
        .expect("stage current correction");
    store.commit(tx).await.expect("commit current correction");

    let historical = store
        .fetch_deep_snapshot(&context, &deep_time("2030-01-01T00:00:00Z"))
        .await
        .expect("historical snapshot");
    assert_eq!(historical.len(), 1);
    assert_eq!(historical[0].eligible_unit_ids(), vec![historical_unit]);

    let current = store
        .fetch_deep_snapshot(&context, &deep_time("2032-01-01T00:00:00Z"))
        .await
        .expect("current snapshot");
    assert_eq!(current.len(), 1);
    assert_eq!(current[0].eligible_unit_ids(), vec![current_unit]);
}

/// Actor identity is source provenance, not a read partition. A second caller
/// bound to the same subject/scope/agent source tuple sees authorized memory
/// even though it did not author the source.
pub async fn deep_snapshot_does_not_treat_actor_as_read_partition<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let suffix = Uuid::now_v7();
    let author_binding = ContextBindingRequest {
        subject: ContextBindingEntityRef {
            external_ref: format!("deep-subject:{suffix}"),
            kind: "user".to_string(),
        },
        actor: ContextBindingEntityRef {
            external_ref: format!("deep-actor:{suffix}:author"),
            kind: "user".to_string(),
        },
        scope: ContextBindingScopeRef {
            external_ref: format!("deep-scope:{suffix}:source"),
            kind: "user_root".to_string(),
            parent_external_ref: None,
        },
        agent_node: ContextBindingAgentRef {
            external_ref: format!("deep-agent:{suffix}:source"),
            parent_external_ref: None,
        },
        access_policies: vec![],
    };
    let author = bind_context_request(
        store,
        tenant,
        format!("deep-author:{suffix}"),
        author_binding,
    )
    .await;
    let caller_binding = ContextBindingRequest {
        subject: ContextBindingEntityRef {
            external_ref: format!("deep-subject:{suffix}"),
            kind: "user".to_string(),
        },
        actor: ContextBindingEntityRef {
            external_ref: format!("deep-actor:{suffix}:caller"),
            kind: "user".to_string(),
        },
        scope: ContextBindingScopeRef {
            external_ref: format!("deep-scope:{suffix}:caller"),
            kind: "user_root".to_string(),
            parent_external_ref: None,
        },
        agent_node: ContextBindingAgentRef {
            external_ref: format!("deep-agent:{suffix}:caller"),
            parent_external_ref: None,
        },
        access_policies: vec![
            ContextBindingAccessPolicy::Grant {
                source_scope_external_ref: format!("deep-scope:{suffix}:source"),
                source_agent_node_external_ref: format!("deep-agent:{suffix}:source"),
                kind: MemoryKind::Episodic,
            },
            ContextBindingAccessPolicy::Grant {
                source_scope_external_ref: format!("deep-scope:{suffix}:source"),
                source_agent_node_external_ref: format!("deep-agent:{suffix}:source"),
                kind: MemoryKind::Semantic,
            },
        ],
    };
    let caller = bind_context_request(
        store,
        tenant,
        format!("deep-caller:{suffix}"),
        caller_binding,
    )
    .await;
    assert_ne!(author.actor_id, caller.actor_id);
    assert_eq!(author.data_subject_id, caller.data_subject_id);
    assert!(caller.allows(MemoryKind::Episodic, author.scope_id, author.agent_node_id));
    assert!(caller.allows(MemoryKind::Semantic, author.scope_id, author.agent_node_id));

    let (_, unit_id) = stage_deep_episode(
        store,
        &author,
        "Authorized cross-actor provenance body.",
        TrustLevel::TrustedUser,
        UnitState::Active,
        TrustLevel::TrustedUser,
        None,
    )
    .await;
    let snapshot = store
        .fetch_deep_snapshot(&caller, &deep_time(CLOCK.0))
        .await
        .expect("cross-actor deep snapshot");
    assert_eq!(snapshot.len(), 1);
    assert_eq!(snapshot[0].body, "Authorized cross-actor provenance body.");
    assert_eq!(snapshot[0].eligible_unit_ids(), vec![unit_id]);
    assert_eq!(snapshot[0].bound_units[0].actor_id, Some(author.actor_id));
}

/// A committed staged transaction publishes both the episode and the unit; a
/// fresh read sees nothing until the commit lands.
pub async fn commit_publishes_staged_episode_and_unit<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    let mut tx = store.begin(&context).await.expect("begin");
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
                source_ref: "testkit:episode".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_trust: TrustLevel::TrustedUser,
                dedup_key: "scope:user:hello".to_string(),
                body: "Remember the deploy channel is #launch.".to_string(),
            },
        )
        .await
        .expect("stage episode");
    let unit = store
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
                fact_key: Some("deploy_channel:value".to_string()),
                predicate: Some("value".to_string()),
                body: "Deploy channel is #launch.".to_string(),
                confidence: None,
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: "testkit:episode".to_string(),
                observed_at: CLOCK.0.to_string(),
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
        .expect("stage unit");

    assert!(
        store
            .fetch_episode(&context, episode.episode_id)
            .await
            .expect("fetch")
            .is_none(),
        "staged rows are invisible before commit"
    );

    store.commit(tx).await.expect("commit");

    assert!(
        store
            .fetch_episode(&context, episode.episode_id)
            .await
            .expect("fetch")
            .is_some()
    );
    let units = store
        .fetch_units_by_ids(&context, &[unit])
        .await
        .expect("fetch units");
    assert_eq!(units.len(), 1);
    assert_eq!(units[0].id, unit);
}

/// A staged transaction dropped without commit leaves no rows behind.
pub async fn drop_rolls_back_staged_rows<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    {
        let mut tx = store.begin(&context).await.expect("begin");
        store
            .stage_episode(
                &mut tx,
                NewEpisode {
                    tenant_id: tenant,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    actor_id: context.actor_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    source_kind: "agent".to_string(),
                    source_ref: "testkit:discarded".to_string(),
                    observed_at: CLOCK.0.to_string(),
                    source_trust: TrustLevel::AgentOutput,
                    dedup_key: "agent:discarded".to_string(),
                    body: "This row is staged only.".to_string(),
                },
            )
            .await
            .expect("stage");
    }

    assert!(
        store
            .fetch_episodes_for_scope(&context, 10)
            .await
            .expect("fetch")
            .is_empty(),
        "a dropped transaction rolls back"
    );
}

/// `fetch_recall_candidates` never leaks across a tenant or scope boundary.
pub async fn recall_candidates_are_tenant_and_scope_scoped<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant_a = h.fresh_tenant().await;
    let tenant_b = h.fresh_tenant().await;
    let context_a = bind_context(store, tenant_a).await;
    let context_a_other = bind_context(store, tenant_a).await;
    let context_b = bind_context(store, tenant_b).await;

    for (context, body) in [
        (&context_a, "Tenant A scope A fact."),
        (&context_a_other, "Tenant A scope B fact."),
        (&context_b, "Tenant B scope A fact."),
    ] {
        let mut tx = store.begin(context).await.expect("begin");
        store
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
                    fact_key: None,
                    predicate: None,
                    body: body.to_string(),
                    confidence: None,
                    trust_level: TrustLevel::TrustedSystem,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(context.actor_id),
                    source_kind: None,
                    source_ref: "testkit:unit".to_string(),
                    observed_at: CLOCK.0.to_string(),
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
            .expect("stage");
        store.commit(tx).await.expect("commit");
    }

    let candidates = store
        .fetch_recall_candidates(
            &context_a,
            &[],
            &[],
            &memphant_types::RecallTime {
                evaluated_at: "9999-01-01T00:00:00Z".to_string(),
                transaction_as_of: "9999-01-01T00:00:00Z".to_string(),
                valid_at: "9999-01-01T00:00:00Z".to_string(),
            },
            usize::MAX,
        )
        .await
        .expect("fetch");
    assert_eq!(candidates.len(), 1);
    assert_eq!(candidates[0].body, "Tenant A scope A fact.");
}

/// A stored trace is tenant-bound: its owner resolves it, any other tenant gets
/// `None` — never another tenant's trace.
pub async fn trace_is_tenant_bound<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant_a = h.fresh_tenant().await;
    let tenant_b = h.fresh_tenant().await;
    let context_a = bind_context(store, tenant_a).await;
    let context_b = bind_context(store, tenant_b).await;

    let response = recall(store, recall_request(&context_a, "anything"), None, &CLOCK)
        .await
        .expect("recall");

    let own = store
        .trace_by_id(&context_a, response.trace_id)
        .await
        .expect("lookup");
    assert!(own.is_some(), "owner tenant sees its trace");

    let cross = store
        .trace_by_id(&context_b, response.trace_id)
        .await
        .expect("lookup");
    assert!(cross.is_none(), "wrong tenant must get None, never a trace");
}

/// Usage credit for a synthetic context item (e.g. a quantity rollup) flows to
/// its real source units: `record_mark` expands the synthetic id into
/// `derived_from_unit_ids` before staging, and each store's review whitelist
/// must accept those expanded source ids while still rejecting ids outside the
/// trace. One shared case so the core expansion and the two store whitelists
/// (InMemory + Pg SQL) cannot drift apart.
pub async fn review_marks_credit_synthetic_sources_and_stay_trace_bound<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    // The rollup's source units are real persisted units (Pg enforces this via
    // the review_event_unit -> memory_unit FK), so stage them for real.
    let mut tx = store.begin(&context).await.expect("begin");
    let mut source_ids = Vec::new();
    for amount in ["12.50", "9.75"] {
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
                    fact_key: None,
                    predicate: None,
                    body: format!("food_spending item amount: {amount}"),
                    confidence: None,
                    trust_level: TrustLevel::TrustedUser,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(context.actor_id),
                    source_kind: None,
                    source_ref: format!("testkit:mark:{amount}"),
                    observed_at: CLOCK.0.to_string(),
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
            .expect("stage source unit");
        source_ids.push(unit_id);
    }
    store.commit(tx).await.expect("commit source units");
    let (source_a, source_b) = (source_ids[0], source_ids[1]);

    let synthetic = UnitId::new();
    let trace = RetrievalTrace {
        id: TraceId::new(),
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        policy_revision: context.policy_revision.clone(),
        query_hash: "testkit-query-hash".to_string(),
        engine_version: "testkit".to_string(),
        feature_flags: Vec::new(),
        channel_runs: Vec::new(),
        candidates: Vec::new(),
        policy_filters: Vec::new(),
        context_items: vec![RecallContextItem {
            unit_id: synthetic,
            body: "food_spending total=2".to_string(),
            kind: MemoryKind::Semantic,
            derived_by: "quantity_rollup".to_string(),
            inclusion_reason: "aggregation".to_string(),
            citation_episode_id: None,
            citation_resource_id: None,
            derived_from_unit_ids: vec![source_a, source_b],
            suppression_labels: Vec::new(),
        }],
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
        abstention_signal: false,
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
            evaluated_at: CLOCK.0.to_string(),
            transaction_as_of: CLOCK.0.to_string(),
            valid_at: CLOCK.0.to_string(),
        },
    };
    store
        .store_trace(&context, trace.clone())
        .await
        .expect("store trace");

    let mark = |used_ids: Vec<UnitId>, caller: &str| MarkRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        trace_id: trace.id,
        caller_id: caller.to_string(),
        used_ids,
        outcome: MarkOutcome::Success,
    };

    record_mark(store, &context, mark(vec![synthetic], "synthetic"), &CLOCK)
        .await
        .expect("marking the synthetic item is accepted");
    let mut fetched = store
        .fetch_review_events(&context, &[source_a, source_b], &trace.recall_time)
        .await
        .expect("fetch review events");
    assert_eq!(fetched.len(), 1, "one review event recorded");
    fetched[0].used_ids.sort_unstable_by_key(|id| id.as_uuid());
    let mut expected = vec![source_a, source_b];
    expected.sort_unstable_by_key(|id| id.as_uuid());
    assert_eq!(
        fetched[0].used_ids, expected,
        "credit lands on the real source units, not the per-recall synthetic id"
    );

    // Callers may only mark ids they were actually shown: raw source ids and
    // foreign ids both stay outside the canonical inclusion whitelist.
    record_mark(store, &context, mark(vec![source_a], "raw-source"), &CLOCK)
        .await
        .expect_err("raw source id is not directly markable");
    record_mark(
        store,
        &context,
        mark(vec![UnitId::new()], "foreign"),
        &CLOCK,
    )
    .await
    .expect_err("foreign id is rejected");
}

/// Forgetting an episode invalidates its derived units, and the forgotten-source
/// tombstone blocks re-derivation when the SAME episode is recompiled under a
/// bumped compiler version. Driven through `reflect_recorded` on the forgotten
/// episode id — the precise `persist_compiled_units` tombstone contract, without
/// coupling to whether dedup re-matches a forgotten episode.
pub async fn forget_by_episode_blocks_recompilation<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;

    let retained = retain_episode(
        store,
        &context,
        retain_request(
            &context,
            "Payment processor is AcmePay.",
            Some("payment processor"),
        ),
    )
    .await
    .expect("retain");

    let reflect_input = |compiler_version: &str| ReflectInput {
        tenant_id: tenant,
        data_subject_id: context.data_subject_id,
        scope_id: scope,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: actor,
        source_ref: "testkit:payment".to_string(),
        observed_at: CLOCK.0.to_string(),
        source_body: None,
        episode_id: Some(retained.episode_id),
        resource_id: None,
        job_id: JobId::new(),
        compiler_version: compiler_version.to_string(),
        candidates: vec![ReflectCandidate {
            source_kind: "user".to_string(),
            trust_level: TrustLevel::TrustedUser,
            actor_id: actor,
            subject: Some("payment processor".to_string()),
            predicate: Some("value".to_string()),
            fact_key: None,
            kind: None,
            body: "Payment processor is AcmePay.".to_string(),
            confidence: None,
            churn_class: None,
            admission_hint: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            target_unit_ids: None,
        }],
    };

    reflect_recorded(
        store,
        reflect_input("compiler-forget"),
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("reflect");
    let present = recall(
        store,
        recall_request(&context, "Which payment processor do we use?"),
        None,
        &CLOCK,
    )
    .await
    .expect("recall");
    assert_eq!(present.items[0].body, "Payment processor is AcmePay.");

    let forgotten = forget_memory(
        store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: scope,
            actor_id: actor,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: None,
                episode_id: Some(retained.episode_id),
                resource_id: None,
                scope_id: scope,
            },
            reason: "user_request".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget");
    assert_eq!(forgotten.invalidated_units.len(), 1);
    assert_eq!(
        forgotten.verification, "authorized_transaction_committed",
        "forget verifies the authorized hard-delete transaction committed"
    );

    // A second reflect of the SAME episode with a bumped compiler version must
    // NOT resurrect the forgotten fact: the forgotten-source tombstone blocks
    // re-derivation.
    reflect_recorded(
        store,
        reflect_input("compiler-forget-v2"),
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("recompilation runs");
    let recalled_again = recall(
        store,
        recall_request(&context, "Which payment processor do we use?"),
        None,
        &CLOCK,
    )
    .await
    .expect("recall after recompile");
    assert!(
        recalled_again.items.is_empty(),
        "tombstoned episode must not re-derive units"
    );
}

/// Deletion-completeness across correction lineage (spec 04 §10). A /v1/correct
/// replacement deliberately carries correction provenance — `source_episode_id`
/// is None (pinned by `correction_provenance.rs`) — so forget-by-episode cannot
/// find it by source column alone: it must cascade from the directly-affected
/// units through `supersedes` edges to their descendants, or the corrected
/// content survives the episode's erasure and resurfaces in recall.
pub async fn forget_by_episode_cascades_through_correction_lineage<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;

    let retained = retain_episode(
        store,
        &context,
        retain_request(
            &context,
            "Release region is Taipei.",
            Some("release region"),
        ),
    )
    .await
    .expect("retain");

    reflect_recorded(
        store,
        ReflectInput {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: scope,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: actor,
            source_ref: "testkit:region".to_string(),
            observed_at: CLOCK.0.to_string(),
            source_body: None,
            episode_id: Some(retained.episode_id),
            resource_id: None,
            job_id: JobId::new(),
            compiler_version: "compiler-lineage".to_string(),
            candidates: vec![ReflectCandidate {
                source_kind: "user".to_string(),
                trust_level: TrustLevel::TrustedUser,
                actor_id: actor,
                subject: Some("release region".to_string()),
                predicate: Some("value".to_string()),
                fact_key: None,
                kind: None,
                body: "Release region is Taipei.".to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                target_unit_ids: None,
            }],
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("reflect");

    let source_unit_id = store
        .fetch_scope_open_units(&context)
        .await
        .expect("open units")
        .into_iter()
        .find(|unit| unit.source_episode_id == Some(retained.episode_id))
        .expect("unit derived from episode")
        .id;

    let corrected = correct_memory(
        store,
        &context,
        CorrectRequest {
            subject_id: context.data_subject_id,
            scope_id: scope,
            actor_id: actor,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: CorrectSelector {
                memory_unit_id: source_unit_id,
            },
            correction: CorrectionPayload {
                value: "Release region is Osaka.".to_string(),
                reason: "user correction".to_string(),
                source_ref: "testkit:correction:region".to_string(),
                observed_at: CLOCK.0.to_string(),
                valid_from: None,
                valid_to: None,
            },
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("correct");
    let replacement_id = corrected.created[0];

    let present = recall(
        store,
        recall_request(&context, "Which release region do we use?"),
        None,
        &CLOCK,
    )
    .await
    .expect("recall before forget");
    assert!(
        present.items.iter().any(|item| item.body.contains("Osaka")),
        "corrected truth must be live before the forget"
    );

    let forgotten = forget_memory(
        store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: scope,
            actor_id: actor,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: None,
                episode_id: Some(retained.episode_id),
                resource_id: None,
                scope_id: scope,
            },
            reason: "user_request".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget");
    assert!(
        forgotten.invalidated_units.contains(&replacement_id),
        "forget-by-episode must cascade through the supersedes edge to the \
         correction replacement; invalidated: {:?}",
        forgotten.invalidated_units
    );

    let after = recall(
        store,
        recall_request(&context, "Which release region do we use?"),
        None,
        &CLOCK,
    )
    .await
    .expect("recall after forget");
    assert!(
        after
            .items
            .iter()
            .all(|item| !item.body.contains("egion is")),
        "forgotten content escaped episode forget: {:?}",
        after.items
    );
}

/// Deletion-completeness across COMPOSITION lineage. A composed belief is
/// derived (via `derived_from` edges) from its source preference units; when a
/// source is forgotten the composition contains forgotten-derived content and
/// must die too (spec 04 §10). `delete_composed_dependents` is hand-mirrored —
/// Rust in InMemoryStore, SQL in PgStore — so a shared scenario is the only
/// thing that stops the two from silently diverging on this deletion path.
pub async fn forget_source_cascades_to_composed_dependent<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;

    // Two trusted preference observations in the same scope; the second reflect
    // composes them into an inferred belief with two `derived_from` edges.
    let reflect_preference = |body: &str, subject: &str, source: &str, trust| {
        let retained = retain_episode(
            store,
            &context,
            retain_request(&context, body, Some(subject)),
        );
        let body = body.to_string();
        let subject = subject.to_string();
        let source = source.to_string();
        async move {
            let retained = retained.await.expect("retain");
            reflect_recorded(
                store,
                ReflectInput {
                    tenant_id: tenant,
                    data_subject_id: context.data_subject_id,
                    scope_id: scope,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    actor_id: actor,
                    source_ref: format!("testkit:pref:{subject}"),
                    observed_at: CLOCK.0.to_string(),
                    source_body: None,
                    episode_id: Some(retained.episode_id),
                    resource_id: None,
                    job_id: JobId::new(),
                    compiler_version: "compiler-composition".to_string(),
                    candidates: vec![ReflectCandidate {
                        source_kind: source,
                        trust_level: trust,
                        actor_id: actor,
                        subject: Some(subject),
                        predicate: Some("value".to_string()),
                        fact_key: None,
                        kind: None,
                        body,
                        confidence: None,
                        churn_class: None,
                        admission_hint: None,
                        contextual_chunks: Vec::new(),
                        valid_from: None,
                        valid_to: None,
                        target_unit_ids: None,
                    }],
                },
                &NoopEmbedding,
                &CLOCK,
            )
            .await
            .expect("reflect");
        }
    };
    reflect_preference(
        "The user prefers quiet review surfaces.",
        "quiet review preference",
        "user",
        TrustLevel::TrustedUser,
    )
    .await;
    reflect_preference(
        "The user prefers keyboard-first review surfaces.",
        "keyboard review preference",
        "system",
        TrustLevel::TrustedSystem,
    )
    .await;

    let open = store
        .fetch_scope_open_units(&context)
        .await
        .expect("open units");
    let composed = open
        .iter()
        .find(|unit| unit.source_kind.as_deref() == Some("composition"))
        .expect("composition belief was minted");
    let source_unit = open
        .iter()
        .find(|unit| unit.body == "The user prefers quiet review surfaces.")
        .expect("source preference unit");

    forget_memory(
        store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: scope,
            actor_id: actor,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: Some(source_unit.id),
                episode_id: None,
                resource_id: None,
                scope_id: scope,
            },
            reason: "user_request".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget source unit");

    let open_after = store
        .fetch_scope_open_units(&context)
        .await
        .expect("open units after forget");
    assert!(
        !open_after.iter().any(|unit| unit.id == composed.id),
        "composition derived from a forgotten source must be deleted too"
    );
}

/// Forgetting a unit CLOSES its transaction interval (so it leaves the open
/// write scope) and PURGES its embedding — on every store. Guards two ways the
/// in-memory store used to under-mirror Postgres's `apply_forget`: it marked the
/// unit `Deleted` without setting `transaction_to` (so a forgotten unit leaked
/// back through `fetch_scope_open_units`, the reflect write seam), and it never
/// deleted the embedding (so a forgotten unit stayed vector-visible). Both are
/// the exact InMemory/PgStore divergence class the shared suite exists to catch.
pub async fn forget_by_unit_closes_and_purges<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;

    // One open semantic unit, staged directly so we hold its id.
    let mut tx = store.begin(&context).await.expect("begin");
    let unit_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: scope,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("preference:value".to_string()),
                predicate: Some("value".to_string()),
                confidence: None,
                body: "The user prefers dark mode.".to_string(),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor),
                source_kind: None,
                source_ref: "testkit:preference".to_string(),
                observed_at: CLOCK.0.to_string(),
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
        .expect("stage unit");
    store.commit(tx).await.expect("commit");

    // Give it an embedding so we can prove forget hard-deletes it.
    let profile = EmbeddingProfileRow {
        id: Uuid::now_v7(),
        provider: "contract-stub".to_string(),
        model: "contract-stub".to_string(),
        dimensions: 3,
        distance: "cosine".to_string(),
        version: "1".to_string(),
        index_strategy: "exact".to_string(),
    };
    store
        .upsert_embedding_profile(tenant, profile.clone())
        .await
        .expect("seed profile");
    store
        .upsert_embeddings(
            &context,
            vec![EmbeddingRow {
                memory_unit_id: unit_id,
                embedding_profile_id: profile.id,
                vec: vec![0.1, 0.2, 0.3],
            }],
        )
        .await
        .expect("seed embedding");

    let open_before = store
        .fetch_scope_open_units(&context)
        .await
        .expect("open before");
    assert!(open_before.iter().any(|unit| unit.id == unit_id));
    assert_eq!(
        store
            .fetch_embeddings(&context, &[unit_id])
            .await
            .expect("embeddings before")
            .len(),
        1
    );

    let forgotten = forget_memory(
        store,
        &context,
        ForgetRequest {
            subject_id: context.data_subject_id,
            scope_id: scope,
            actor_id: actor,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            selector: ForgetSelector {
                memory_unit_id: Some(unit_id),
                episode_id: None,
                resource_id: None,
                scope_id: scope,
            },
            reason: "user_request".to_string(),
        },
        &CLOCK,
    )
    .await
    .expect("forget");
    assert_eq!(forgotten.invalidated_units, vec![unit_id]);

    assert!(
        store
            .fetch_scope_open_units(&context)
            .await
            .expect("open after")
            .iter()
            .all(|unit| unit.id != unit_id),
        "a forgotten unit must leave the open write scope (transaction_to set)"
    );
    assert!(
        store
            .fetch_embeddings(&context, &[unit_id])
            .await
            .expect("embeddings after")
            .is_empty(),
        "a forgotten unit's embedding must be hard-deleted"
    );
}

/// The divergence guard. `reflect_recorded` supersedes against the WHOLE scope
/// via `fetch_scope_open_units`. If the write path ever regressed to a bounded
/// recall pool (`fetch_recall_candidates`, which PgStore caps at the 100
/// most-recent units), a prior unit aged past the window would be invisible and
/// a high-trust update would try to insert a duplicate open unit on the same
/// subject — hard-failing on PgStore (unique index) while passing on
/// InMemoryStore (whole scope). Running this against both stores makes such a
/// divergence fail here.
pub async fn semantic_update_supersedes_unit_aged_past_recall_window<H: StoreHarness>(h: &H) {
    const OLD_CLOCK: FixedClock = FixedClock("2026-07-01T00:00:00Z");
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;

    // The target unit, written oldest so recency-ordering buries it.
    reflect_recorded(
        store,
        ReflectInput {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: scope,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: actor,
            source_ref: "testkit:role-old".to_string(),
            observed_at: OLD_CLOCK.0.to_string(),
            source_body: None,
            episode_id: None,
            resource_id: None,
            job_id: JobId::new(),
            compiler_version: "compiler-supersede".to_string(),
            candidates: vec![ReflectCandidate {
                source_kind: "user".to_string(),
                trust_level: TrustLevel::TrustedUser,
                actor_id: actor,
                subject: Some("role".to_string()),
                predicate: Some("is".to_string()),
                fact_key: None,
                kind: Some(MemoryKind::Semantic),
                body: "the user is an admin".to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                target_unit_ids: None,
            }],
        },
        &NoopEmbedding,
        &OLD_CLOCK,
    )
    .await
    .expect("seed target unit");

    // 105 newer unrelated units push the target out of the most-recent-100.
    let fillers: Vec<ReflectCandidate> = (0..105)
        .map(|i| ReflectCandidate {
            source_kind: "user".to_string(),
            trust_level: TrustLevel::TrustedUser,
            actor_id: actor,
            subject: Some(format!("filler-{i}")),
            predicate: Some("is".to_string()),
            fact_key: None,
            kind: Some(MemoryKind::Semantic),
            body: format!("filler fact number {i} about widgets"),
            confidence: None,
            churn_class: None,
            admission_hint: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            target_unit_ids: None,
        })
        .collect();
    reflect_recorded(
        store,
        ReflectInput {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: scope,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: actor,
            source_ref: "testkit:fillers".to_string(),
            observed_at: CLOCK.0.to_string(),
            source_body: None,
            episode_id: None,
            resource_id: None,
            job_id: JobId::new(),
            compiler_version: "compiler-supersede".to_string(),
            candidates: fillers,
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("seed fillers");

    // Update the same subject/predicate: must supersede the aged unit, not
    // collide with it on the scope-subject unique index.
    service(store)
        .retain(
            &context,
            "testkit:aged-supersession",
            TrustLevel::TrustedUser,
            RetainEpisodeHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: scope,
                actor_id: actor,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_ref: "testkit:role-new".to_string(),
                observed_at: CLOCK.0.to_string(),
                payload: RetainPayload::Unit(RetainUnitPayload {
                    kind: MemoryKind::Semantic,
                    fact_key: derive_fact_key(scope.as_uuid(), Some("role"), Some("is"), ""),
                    predicate: "is".to_string(),
                    body: "the user is a developer".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                    target_unit_ids: None,
                }),
            },
        )
        .await
        .expect("update must supersede the aged unit, not fail on a duplicate subject");

    // The open belief partitions preserve the old value before the change and
    // expose exactly one value at the current valid-time boundary.
    let current: Vec<_> = store
        .fetch_scope_open_units(&context)
        .await
        .expect("fetch open units")
        .into_iter()
        .filter(|unit| unit.body.contains("the user is"))
        .filter(|unit| {
            unit.valid_from
                .as_deref()
                .is_none_or(|from| from <= CLOCK.0)
                && unit.valid_to.as_deref().is_none_or(|to| CLOCK.0 < to)
        })
        .collect();
    assert_eq!(
        current.len(),
        1,
        "the aged value must end where the current value begins"
    );
    assert_eq!(current[0].body, "the user is a developer");
}

/// `fetch_episodes_for_scope` honors the caller's `limit` — no silent store-side
/// cap. Guards the Deep-recall divergence: PgStore used to clamp this read
/// at 1000 rows while InMemoryStore returned everything, so `RecallMode::
/// Deep` (which passes `usize::MAX` and re-ranks the FULL episode set)
/// silently dropped relevant-but-old episodes on Postgres only. Seeds past the
/// old cap and asserts both stores return the whole scope.
pub async fn fetch_episodes_honors_large_limit<H: StoreHarness>(h: &H) {
    // One past the old 1000-row silent cap.
    const N: usize = 1001;
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;

    for i in 0..N {
        retain_episode(
            store,
            &context,
            retain_request(&context, &format!("Episode fact number {i}."), None),
        )
        .await
        .expect("retain");
    }

    let episodes = store
        .fetch_episodes_for_scope(&context, usize::MAX)
        .await
        .expect("fetch episodes");
    assert_eq!(
        episodes.len(),
        N,
        "fetch_episodes_for_scope must return the whole scope for an unbounded \
         limit, not a silently-capped subset"
    );
}

/// `scope_memory_page` cursors through a scope's units without overlap or loss.
pub async fn scope_memory_page_paginates_without_overlap<H: StoreHarness>(h: &H) {
    let store = h.store();
    let tenant = h.fresh_tenant().await;
    let context = bind_context(store, tenant).await;
    let svc = service(store);

    for index in 0..5 {
        retain_episode(
            svc.store(),
            &context,
            retain_request(
                &context,
                &format!("Paginated fact number {index}."),
                Some(&format!("paginated fact {index}")),
            ),
        )
        .await
        .expect("retain");
    }
    // Tenant-scoped so a concurrently running harness sharing the database
    // cannot claim (or be robbed of) this tenant's reflect jobs.
    // Drain to idle, not to `completed == 0`: a tick that failed every job
    // also completes zero, so the old condition exited on total failure exactly
    // as it did on an empty queue. Assert the drain was clean afterwards —
    // otherwise this fixture would hand the assertions below a corpus that
    // never compiled.
    loop {
        let tick = svc
            .run_worker_tick_scoped(tenant_filter(&context), usize::MAX)
            .await
            .expect("reflect");
        assert!(
            tick.is_clean(),
            "reflect drain hit errors: {tick:?} — the fixture corpus did not compile"
        );
        if tick.is_idle() {
            break;
        }
    }

    let page_one = store
        .scope_memory_page(&context, None, 3)
        .await
        .expect("page one");
    assert_eq!(page_one.items.len(), 3);
    assert!(page_one.has_more);
    let cursor = page_one.next_cursor.expect("cursor");

    let page_two = store
        .scope_memory_page(&context, Some(cursor), 3)
        .await
        .expect("page two");
    assert!(!page_two.items.is_empty());
    assert!(!page_two.has_more);

    let ids_one: std::collections::HashSet<_> = page_one
        .items
        .iter()
        .map(|unit| unit.id.as_uuid())
        .collect();
    let ids_two: std::collections::HashSet<_> = page_two
        .items
        .iter()
        .map(|unit| unit.id.as_uuid())
        .collect();
    assert!(ids_one.is_disjoint(&ids_two));
    assert_eq!(ids_one.len() + ids_two.len(), 5);
}
