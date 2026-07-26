use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use memphant_core::service::MemoryService;
use memphant_core::{
    FixedClock, InMemoryStore, JobFilter, MemoryStore, NoopEmbedding, ReflectJobRow,
    StructuredObservation, StructuredStateProvider, StructuredStateProviderError,
    StructuredStateProviderIdentity, StructuredStateRequest, reflect_recorded, retain_episode,
    structured_compiler_identity,
};
use memphant_types::{
    COMPILER_VERSION, ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest,
    ContextBindingScopeRef, JobId, MemoryKind, ReflectCandidate, ReflectInput, RetainRequest,
    TenantId, TrustLevel,
};

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

async fn context(
    store: &InMemoryStore,
    tenant: TenantId,
    suffix: &str,
) -> memphant_types::ResolvedMemoryContext {
    let binding = store
        .resolve_context_binding(
            tenant,
            format!("binding:{suffix}"),
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
                    kind: "memory".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: format!("agent:{suffix}"),
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

fn input(
    context: &memphant_types::ResolvedMemoryContext,
    job_id: JobId,
    body: &str,
) -> ReflectInput {
    ReflectInput {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        source_ref: "test:reflect".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        source_body: None,
        episode_id: None,
        resource_id: None,
        job_id,
        compiler_version: "same-compiler".to_string(),
        candidates: vec![ReflectCandidate {
            source_kind: "direct".to_string(),
            trust_level: TrustLevel::TrustedUser,
            actor_id: context.actor_id,
            subject: Some("profile".to_string()),
            predicate: Some("home_city".to_string()),
            fact_key: None,
            kind: Some(MemoryKind::Semantic),
            body: body.to_string(),
            confidence: None,
            churn_class: None,
            admission_hint: None,
            target_unit_ids: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
        }],
    }
}

#[tokio::test]
async fn same_tenant_cross_subject_job_identity_conflicts() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let left = context(&store, tenant, "left").await;
    let right = context(&store, tenant, "right").await;
    let job_id = JobId::new();

    reflect_recorded(
        &store,
        input(&left, job_id, "Left subject lives in Oslo."),
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .unwrap();

    reflect_recorded(
        &store,
        input(&right, job_id, "Right subject lives in Lima."),
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect_err("another subject cannot replay the same job/compiler identity");
}

struct BlockingProvider {
    identity: StructuredStateProviderIdentity,
    entered: Arc<AtomicBool>,
    release: Arc<AtomicBool>,
}

impl StructuredStateProvider for BlockingProvider {
    fn identity(&self) -> &StructuredStateProviderIdentity {
        &self.identity
    }

    fn extract<'a>(
        &'a self,
        _request: &'a StructuredStateRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>>
                + Send
                + 'a,
        >,
    > {
        Box::pin(async move {
            self.entered.store(true, Ordering::SeqCst);
            while !self.release.load(Ordering::SeqCst) {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
            Ok(Vec::new())
        })
    }
}

#[tokio::test]
async fn worker_completed_count_excludes_a_stale_claim_noop() {
    let store = Arc::new(InMemoryStore::default());
    let tenant = TenantId::new();
    let context = context(&store, tenant, "worker").await;
    let provider_identity = StructuredStateProviderIdentity {
        model: "blocking-test".to_string(),
        prompt_hash: "prompt".to_string(),
        schema_hash: "schema".to_string(),
    };
    retain_episode(
        store.as_ref(),
        &context,
        RetainRequest {
            tenant_id: tenant,
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
            body: "user: I live in Oslo.".to_string(),
            compiler_version: structured_compiler_identity(
                &format!("{COMPILER_VERSION}+source-slice-greedy-batches-v2"),
                &provider_identity,
            ),
        },
    )
    .await
    .unwrap();

    let queued = store.reflect_jobs(tenant).into_iter().next().unwrap();
    let entered = Arc::new(AtomicBool::new(false));
    let release = Arc::new(AtomicBool::new(false));
    let service = MemoryService::new(Arc::clone(&store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_structured_state_provider(Arc::new(BlockingProvider {
            identity: provider_identity,
            entered: Arc::clone(&entered),
            release: Arc::clone(&release),
        }));

    let tick = tokio::spawn(async move { service.run_worker_tick(1).await });
    tokio::time::timeout(Duration::from_secs(1), async {
        while !entered.load(Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await
    .expect("worker must reach extraction before stale-claim simulation");
    let first_claim = ReflectJobRow {
        job: queued,
        attempts: 1,
        claim_generation: 0,
    };
    store
        .release_reflect_job(&first_claim, 0, "force reclaim".to_string())
        .await
        .unwrap();
    let reclaimed = store
        .claim_reflect_jobs(JobFilter::default(), 1)
        .await
        .unwrap();
    assert_eq!(reclaimed.len(), 1);
    assert_eq!(reclaimed[0].attempts, 2);

    release.store(true, Ordering::SeqCst);
    assert_eq!(tick.await.unwrap().unwrap(), 0);
}
