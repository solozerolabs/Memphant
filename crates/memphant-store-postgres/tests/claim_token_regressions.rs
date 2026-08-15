use std::sync::{Arc, Barrier};

use memphant_core::{
    CompiledWrite, EmbedError, EmbeddingProvider, FixedClock, JobFilter, MemoryStore,
    reflect_recorded, retain_episode,
};
use memphant_store_postgres::PgStore;
use memphant_types::{
    JobId, MemoryKind, ReflectCandidate, ReflectInput, ReflectTrace, RetainRequest, TenantId,
    TrustLevel,
};
use uuid::Uuid;

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

fn empty_write(
    job_id: JobId,
    compiler_version: &str,
    tenant: TenantId,
    scope: memphant_types::ScopeId,
) -> CompiledWrite {
    CompiledWrite {
        job_id,
        compiler_version: compiler_version.to_string(),
        new_units: Vec::new(),
        new_edges: Vec::new(),
        citations: Vec::new(),
        unit_updates: Vec::new(),
        trace: ReflectTrace {
            tenant_id: tenant,
            scope_id: scope,
            job_id,
            episode_id: None,
            resource_id: None,
            compiler_version: compiler_version.to_string(),
            actions: Vec::new(),
            stages: Vec::new(),
            cost_units: 0,
        },
        embedding_profile: None,
        embeddings: Vec::new(),
    }
}

async fn store() -> PgStore {
    let url = std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated Postgres");
    PgStore::connect(&url).await.expect("connect PgStore")
}

async fn tenant(store: &PgStore) -> TenantId {
    let id = store
        .create_tenant(&format!("claim-regression-{}", Uuid::now_v7()))
        .await
        .unwrap();
    TenantId::from_u128(id.as_u128())
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn partial_lane_reclaim_invalidates_every_old_lane_token() {
    let store = store().await;
    let tenant = tenant(&store).await;
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    for index in 0..3 {
        retain_episode(
            &store,
            &context,
            RetainRequest {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_kind: "user".to_string(),
                source_ref: "test:claim-token".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_trust: TrustLevel::TrustedUser,
                subject_hint: None,
                subject: None,
                predicate: None,
                body: format!("Ordered lane item {index}."),
                compiler_version: "partial-reclaim".to_string(),
            },
        )
        .await
        .unwrap();
    }
    let filter = JobFilter {
        tenant: Some(tenant),
        scope: Some(context.scope_id),
    };
    let original = store.claim_reflect_jobs(filter, 3).await.unwrap();
    assert_eq!(original.len(), 3);
    sqlx::query(
        "update memphant.job_state set claimed_at = now() - interval '16 minutes'
         where tenant_id = $1 and scope_id = $2 and state = 'running'",
    )
    .bind(tenant.as_uuid())
    .bind(context.scope_id.as_uuid())
    .execute(store.pool())
    .await
    .unwrap();

    let reclaimed = store.claim_reflect_jobs(filter, 1).await.unwrap();
    assert_eq!(reclaimed.len(), 1);
    assert_eq!(reclaimed[0].attempts, original[0].attempts + 1);

    store.complete_reflect_job(&original[1]).await.unwrap();
    let state: String =
        sqlx::query_scalar("select state from memphant.job_state where tenant_id = $1 and id = $2")
            .bind(tenant.as_uuid())
            .bind(original[1].job.id.as_uuid())
            .fetch_one(store.pool())
            .await
            .unwrap();
    assert_ne!(
        state, "done",
        "reclaiming a lane must fence every old token"
    );
}

struct BarrierEmbedding(Barrier);

impl EmbeddingProvider for BarrierEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        tokio::task::block_in_place(|| self.0.wait());
        Ok(vec![vec![1.0, 0.0]; texts.len()])
    }

    fn dimensions(&self) -> usize {
        2
    }

    fn id(&self) -> &str {
        "barrier-test"
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn concurrent_direct_same_job_compile_persists_once() {
    let store = Arc::new(store().await);
    let tenant = tenant(&store).await;
    let context = memphant_store_testkit::bind_context(store.as_ref(), tenant).await;
    let job_id = JobId::new();
    let body = "Direct runbook memory.";
    let input = ReflectInput {
        tenant_id: tenant,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        source_ref: "test:reflect".to_string(),
        observed_at: CLOCK.0.to_string(),
        source_body: None,
        episode_id: None,
        resource_id: None,
        job_id,
        compiler_version: "direct-race".to_string(),
        candidates: vec![ReflectCandidate {
            compact: None,
            capture: None,
            source_kind: "direct".to_string(),
            trust_level: TrustLevel::TrustedUser,
            actor_id: context.actor_id,
            subject: None,
            predicate: None,
            fact_key: None,
            kind: Some(MemoryKind::Resource),
            body: body.to_string(),
            confidence: None,
            churn_class: None,
            admission_hint: None,
            target_unit_ids: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
        }],
    };
    let embedder = Arc::new(BarrierEmbedding(Barrier::new(2)));
    let left = {
        let store = Arc::clone(&store);
        let embedder = Arc::clone(&embedder);
        let input = input.clone();
        tokio::spawn(async move {
            reflect_recorded(store.as_ref(), input, embedder.as_ref(), &CLOCK).await
        })
    };
    let right = {
        let store = Arc::clone(&store);
        let embedder = Arc::clone(&embedder);
        tokio::spawn(async move {
            reflect_recorded(store.as_ref(), input, embedder.as_ref(), &CLOCK).await
        })
    };
    left.await.unwrap().unwrap();
    right.await.unwrap().unwrap();

    let count: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_unit
         where tenant_id = $1 and data_subject_id = $2 and scope_id = $3
           and agent_node_id = $4 and actor_id = $5 and kind = 'resource' and body = $6",
    )
    .bind(tenant.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.scope_id.as_uuid())
    .bind(context.agent_node_id.as_uuid())
    .bind(context.actor_id.as_uuid())
    .bind(body)
    .fetch_one(store.pool())
    .await
    .unwrap();
    assert_eq!(count, 1);
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn dropped_staged_direct_compilation_rolls_back_job_row() {
    let store = store().await;
    let tenant = tenant(&store).await;
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let job_id = JobId::new();
    let mut tx = store.begin(&context).await.unwrap();
    assert_eq!(
        store
            .stage_compiled_units(
                &mut tx,
                None,
                empty_write(job_id, "drop-direct", tenant, context.scope_id),
            )
            .await
            .unwrap(),
        memphant_core::ClaimMutationOutcome::Applied
    );
    drop(tx);
    let count: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state where tenant_id = $1 and id = $2",
    )
    .bind(tenant.as_uuid())
    .bind(job_id.as_uuid())
    .fetch_one(store.pool())
    .await
    .unwrap();
    assert_eq!(count, 0);
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn direct_compilation_cannot_hijack_a_worker_job_row() {
    let store = store().await;
    let tenant = tenant(&store).await;
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    retain_episode(
        &store,
        &context,
        RetainRequest {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_kind: "user".to_string(),
            source_ref: "test:claim-token".to_string(),
            observed_at: CLOCK.0.to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: None,
            subject: None,
            predicate: None,
            body: "Worker-owned episode.".to_string(),
            compiler_version: "worker-owned".to_string(),
        },
    )
    .await
    .unwrap();
    let job_id = JobId::from_u128(
        sqlx::query_scalar::<_, uuid::Uuid>(
            "select id from memphant.job_state where tenant_id = $1 and data_subject_id = $2 and job_type = 'reflect_episode' order by queue_order desc limit 1",
        )
        .bind(tenant.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .fetch_one(store.pool())
        .await
        .unwrap()
        .as_u128(),
    );
    let mut tx = store.begin(&context).await.unwrap();
    assert!(matches!(
        store.stage_compiled_units(
            &mut tx,
            None,
            empty_write(job_id, "worker-owned", tenant, context.scope_id),
        ).await,
        Err(memphant_core::StoreError::Conflict(message)) if message.contains("worker job")
    ));
}
