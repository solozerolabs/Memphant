//! Concurrency guard for `persist_compiled_units`: a reflect job stalled past
//! the 15-minute reclaim window (see `claim_reflect_jobs`) can be compiled by two
//! workers at once. Semantic units are protected from a duplicate open
//! generation by the partial unique index `memphant_memory_unit_scope_subject_idx`
//! (`kind = 'semantic'`); `resource`-kind units have NO such index, so the
//! idempotency guard has to live in `persist_compiled_units` itself — a row lock
//! on the job_state result record. Without it, two READ COMMITTED compiles both
//! read `result is null` and each inserts a resource unit with a distinct UUID.
//!
//! Gated like the rest of the pg suite (`#[ignore]`, reads
//! MEMPHANT_TEST_DATABASE_URL). Kept in its own file so it does not collide with
//! concurrent edits to pg_store_contract.rs.

use std::sync::Arc;

use memphant_core::{
    FixedClock, JobFilter, MemoryStore, NoopEmbedding, reflect_recorded_claimed, retain_resource,
};
use memphant_store_postgres::PgStore;
use memphant_types::{
    MemoryKind, ReflectCandidate, ReflectInput, ResourceKind, RetainResourceRequest, TenantId,
    TrustLevel,
};
use uuid::Uuid;

const CLOCK: FixedClock = FixedClock("2026-07-09T00:00:00Z");

async fn connect() -> PgStore {
    let url = std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated Postgres");
    PgStore::connect(&url).await.expect("connect PgStore")
}

async fn fresh_tenant(store: &PgStore) -> TenantId {
    let id = store
        .create_tenant(&format!("reclaim-{}", Uuid::now_v7()))
        .await
        .expect("create tenant");
    TenantId::from_u128(id.as_u128())
}

// Two truly-parallel compiles (separate tokio tasks + threads) race between the
// idempotency check and commit. Local Postgres is fast enough that one overlap
// can serialize by luck, so we hammer fresh jobs: the guard must hold on EVERY
// iteration, while the unguarded path double-inserts on at least one.
const RECLAIM_RACE_ITERATIONS: usize = 64;

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn reclaimed_resource_job_recompile_does_not_double_insert_units() {
    let store = Arc::new(connect().await);
    let tenant = fresh_tenant(&store).await;

    for iteration in 0..RECLAIM_RACE_ITERATIONS {
        let context = memphant_store_testkit::bind_context(&*store, tenant).await;
        let scope = context.scope_id;
        let actor = context.actor_id;
        let body = format!("Deploy runbook {iteration}: canary first, then roll forward regions.");
        let retained = retain_resource(
            &*store,
            &context,
            RetainResourceRequest {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: scope,
                actor_id: actor,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                uri: format!("https://example.test/runbooks/deploy-{iteration}.md"),
                source_ref: "test:reclaim".to_string(),
                observed_at: CLOCK.0.to_string(),
                kind: Some(ResourceKind::Document),
                content_hash: format!("sha256:reclaim-runbook-{iteration}"),
                mime_type: "text/markdown".to_string(),
                revision: Some("rev-1".to_string()),
                body: Some(body.clone()),
                source_trust: TrustLevel::TrustedUser,
                compiler_version: "compiler-reclaim".to_string(),
            },
        )
        .await
        .expect("retain resource");

        // Claim once so the job_state row exists (result is null) — the shared
        // idempotency record both concurrent compiles race on.
        let jobs = store
            .claim_reflect_jobs(
                JobFilter {
                    tenant: Some(tenant),
                    scope: Some(scope),
                },
                10,
            )
            .await
            .expect("claim reflect job");
        let job = jobs
            .iter()
            .find(|row| row.job.resource_id == Some(retained.resource_id))
            .expect("resource reflect job was enqueued")
            .clone();

        // Both workers rebuild the SAME ReflectInput (compile_job's resource
        // branch). Each compile mints fresh random unit ids, so a double insert
        // is two rows with distinct UUIDs — what the guard must collapse to one.
        let input = ReflectInput {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: scope,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: actor,
            source_ref: "test:reflect".to_string(),
            observed_at: CLOCK.0.to_string(),
            source_body: None,
            episode_id: None,
            resource_id: Some(retained.resource_id),
            job_id: job.job.id,
            compiler_version: job.job.compiler_version.clone(),
            candidates: vec![ReflectCandidate {
                compact: None,
                source_kind: "resource".to_string(),
                trust_level: TrustLevel::TrustedUser,
                actor_id: actor,
                subject: None,
                predicate: None,
                fact_key: None,
                kind: Some(MemoryKind::Resource),
                body,
                confidence: None,
                churn_class: None,
                admission_hint: None,
                target_unit_ids: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
            }],
        };

        let (store_a, store_b) = (store.clone(), store.clone());
        let (input_a, input_b) = (input.clone(), input);
        let (context_a, context_b) = (context.clone(), context.clone());
        let (job_a, job_b) = (job.clone(), job);
        let worker_a = tokio::spawn(async move {
            reflect_recorded_claimed(
                &*store_a,
                input_a,
                &NoopEmbedding,
                &CLOCK,
                &context_a,
                &job_a,
            )
            .await
        });
        let worker_b = tokio::spawn(async move {
            reflect_recorded_claimed(
                &*store_b,
                input_b,
                &NoopEmbedding,
                &CLOCK,
                &context_b,
                &job_b,
            )
            .await
        });
        worker_a.await.expect("join A").expect("compile A");
        worker_b.await.expect("join B").expect("compile B");

        let resource_units: i64 = sqlx::query_scalar(
            "select count(*) from memphant.memory_unit
             where tenant_id = $1 and kind = 'resource' and source_resource_id = $2",
        )
        .bind(tenant.as_uuid())
        .bind(retained.resource_id.as_uuid())
        .fetch_one(store.pool())
        .await
        .expect("count resource units");

        assert_eq!(
            resource_units, 1,
            "iteration {iteration}: a reclaimed resource-job re-compile double-inserted \
             resource units ({resource_units})"
        );
    }
}
