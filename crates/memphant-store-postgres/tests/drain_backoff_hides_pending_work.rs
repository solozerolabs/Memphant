//! `while tick() > 0 {}` is NOT a drain: a zero-completion tick is not proof of
//! an empty queue.
//!
//! `run_worker_tick_scoped` releases a failed job with `retry_backoff_seconds`
//! and blocks the rest of its scope lane; `claim_reflect_jobs` excludes rows
//! whose `run_after > now()`. So one released job makes the NEXT tick claim
//! nothing and complete zero while the work is still `queued` — and any caller
//! that treats that zero as "queue empty" proceeds to score a partially
//! compiled corpus. This test pins the mechanism and the fix
//! (`memphant_core::service::drain_finished`, which asks the DATABASE).
//!
//! This MUST be a Postgres test: `InMemoryStore::release_reflect_job` ignores
//! its `retry_after_seconds` argument entirely (`_retry_after_seconds`,
//! memphant-core/src/lib.rs), so the in-memory store cannot express the bug at
//! all. Gated like the rest of the pg suite (`#[ignore]`, reads
//! MEMPHANT_TEST_DATABASE_URL).

use memphant_core::service::drain_finished;
use memphant_core::{FixedClock, JobFilter, MemoryStore, retain_episode};
use memphant_store_postgres::PgStore;
use memphant_types::{RetainRequest, TenantId, TrustLevel};
use uuid::Uuid;

const CLOCK: FixedClock = FixedClock("2026-07-30T00:00:00Z");

async fn connect() -> PgStore {
    let url = std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated Postgres");
    PgStore::connect(&url).await.expect("connect PgStore")
}

async fn fresh_tenant(store: &PgStore) -> TenantId {
    let id = store
        .create_tenant(&format!("drain-backoff-{}", Uuid::now_v7()))
        .await
        .expect("create tenant");
    TenantId::from_u128(id.as_u128())
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn a_released_job_makes_the_next_claim_empty_while_work_is_still_pending() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
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
            source_ref: format!("drain-backoff:{}", Uuid::now_v7()),
            observed_at: CLOCK.0.to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: None,
            subject: None,
            predicate: None,
            body: "One episode whose compile will be released with a retry backoff.".to_string(),
            compiler_version: "compiler-drain-backoff".to_string(),
        },
    )
    .await
    .expect("retain episode");

    let filter = JobFilter {
        tenant: Some(tenant),
        scope: None,
    };
    let claimed = store
        .claim_reflect_jobs(filter, 64)
        .await
        .expect("claim the queued job");
    assert_eq!(claimed.len(), 1, "the retained episode enqueues one job");

    // Exactly what a failed compile does (service.rs `run_worker_tick_scoped`).
    store
        .release_reflect_job(&claimed[0], 3_600, "simulated provider failure".to_string())
        .await
        .expect("release with backoff");

    // The bug: the next tick claims nothing, so it completes ZERO...
    let after_release = store
        .claim_reflect_jobs(filter, 64)
        .await
        .expect("claim after release");
    assert!(
        after_release.is_empty(),
        "a job delayed by retry backoff is excluded by run_after <= now(), so the \
         next tick completes zero"
    );

    // ...while the DATABASE still has the work queued.
    let pending = store
        .pending_worker_job_count()
        .await
        .expect("pending count");
    assert!(
        pending >= 1,
        "the released job is still queued, so a zero-completion tick is NOT an empty queue"
    );

    // The fix: the shared drain gate refuses to declare the drain finished.
    assert!(
        !drain_finished(pending, 0, 0).expect("no new dead letters"),
        "drain_finished must keep the drain open while the database reports pending work"
    );
}
