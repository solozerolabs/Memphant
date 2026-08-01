//! Bar 1 (C1) service-layer SLO guard: fast recall p50 < 200 ms / p95 < 500 ms
//! on the REAL Postgres store, not `InMemoryStore`.
//!
//! `memphant-core/tests/hot_path_slo.rs` proves the same budget but against
//! `InMemoryStore` in-process — which STATUS §6 correctly flags as NOT the
//! packaged runtime. This test measures `MemoryService::recall` against
//! `PgStore` over a 252-row episodic corpus, closing the store-layer half of
//! the gap. The HTTP-boundary half (the acceptance number, incl. the axum hop +
//! `resolve_memory_context`) is measured by
//! `scripts/episodic_lane_run_memphant.py --slo-samples`; this Rust test is the
//! cheap CI component guard that catches a pipeline/PG regression early.
//!
//! `#[ignore]`d like every other live-PG contract; run under the AGENTS.md §37
//! scratch-DB leg (`with_scratch_db.sh … MEMPHANT_TEST_DATABASE_URL …`).

use std::sync::Arc;
use std::time::{Duration, Instant};

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, MemoryStore, StubEmbedding};
use memphant_store_postgres::PgStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    RecallHttpRequest, RecallMode, ResolvedMemoryContext, RetainEpisodeHttpRequest, TenantId,
    TrustLevel,
};
use uuid::Uuid;

// A future fixed clock: the worker stamps `transaction_from` with real
// wall-clock `now()` at compile, so recall_time must be >= now or the
// bitemporal window excludes every freshly-compiled unit. (Same reason
// pg_store_contract.rs pins CLOCK to 2030.)
const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");
const FAST_P50_LIMIT: Duration = Duration::from_millis(200);
const FAST_P95_LIMIT: Duration = Duration::from_millis(500);
const CORPUS_ROWS: usize = 252;

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated scratch database")
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn fast_mode_recall_holds_release_hot_path_slo_on_postgres() {
    let provisioner = PgStore::connect_provisioner(&db_url())
        .await
        .expect("connect provisioner store");
    let tenant_uuid = provisioner
        .create_tenant(&format!("hot-path-slo-{}", Uuid::new_v4().simple()))
        .await
        .expect("provision tenant");
    let tenant = TenantId::from_u128(tenant_uuid.as_u128());

    let store = PgStore::connect_app(&db_url(), &db_url())
        .await
        .expect("connect app store");

    let binding = store
        .resolve_context_binding(
            tenant,
            "hot-path-slo".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "hot-path-slo-user".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "hot-path-slo-user".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "hot-path-slo-scope".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "hot-path-slo-agent".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("resolve context binding");
    let context = store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve memory context");

    // StubEmbedding gives a deterministic 32-dim vector channel — the same
    // real-embedder presence the packaged runtime has (fastembed bge-small),
    // without loading model weights. NoopEmbedding would leave the vector
    // channel off, which is not the packaged runtime this SLO must reflect.
    let service = MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    );
    // The drain runs on a WORKER store, as production does: `claim_reflect_jobs`
    // is granted to `memphant_worker` only, and since W3.3 the app pool really
    // is `memphant_app`, so a tick through the app credential is (correctly)
    // `permission denied for function claim_reflect_jobs`.
    let worker = MemoryService::new(
        Arc::new(
            PgStore::connect_worker(&db_url())
                .await
                .expect("connect worker store"),
        ),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    );
    seed_reference_corpus(&service, &worker, &context).await;
    let request = |()| RecallHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        query: "Atlas rollback release owner is platform on-call; cite runbook RB-77.".to_string(),
        limit: Some(10),
        budget_tokens: Some(1200),
        mode: Some(RecallMode::Fast),
        include_beliefs: None,
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    };

    // Warm the pool + query plan cache.
    for _ in 0..5 {
        service
            .recall(context.clone(), request(()))
            .await
            .expect("warm recall");
    }

    let mut samples = Vec::with_capacity(80);
    for _ in 0..80 {
        let started = Instant::now();
        let response = service
            .recall(context.clone(), request(()))
            .await
            .expect("fast recall");
        assert!(
            !response.items.is_empty(),
            "recall must surface the seeded unit"
        );
        samples.push(started.elapsed());
    }

    samples.sort_unstable();
    let p50 = percentile(&samples, 0.50);
    let p95 = percentile(&samples, 0.95);
    assert!(
        p50 < FAST_P50_LIMIT,
        "fast recall p50 {p50:?} breached {FAST_P50_LIMIT:?}"
    );
    assert!(
        p95 < FAST_P95_LIMIT,
        "fast recall p95 {p95:?} breached {FAST_P95_LIMIT:?}"
    );
}

fn percentile(samples: &[Duration], quantile: f64) -> Duration {
    let index = ((samples.len() as f64 - 1.0) * quantile).ceil() as usize;
    samples[index]
}

/// Seed `CORPUS_ROWS` short episodic units through the REAL retain + worker
/// compile path (not hand-staged units) so recall measures the genuine packaged
/// read path over genuinely-compiled units — the same path the Python runner and
/// role_matrix.rs exercise. One answer episode buried among filler.
async fn seed_reference_corpus(
    service: &MemoryService<PgStore>,
    worker: &MemoryService<PgStore>,
    context: &ResolvedMemoryContext,
) {
    for index in 0..CORPUS_ROWS {
        let body = if index == 121 {
            "Atlas rollback release owner is platform on-call; cite runbook RB-77.".to_string()
        } else {
            format!("Routine release note for unrelated service shard {index}.")
        };
        service
            .retain(
                context,
                &format!("hot-path-slo-pg:{index}"),
                TrustLevel::TrustedSystem,
                RetainEpisodeHttpRequest {
                    subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    actor_id: context.actor_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    source_ref: format!("test:fixture:{index}"),
                    observed_at: CLOCK.0.to_string(),
                    payload: memphant_types::RetainPayload::Episode(
                        memphant_types::RetainEpisodePayload {
                            source_kind: "system".to_string(),
                            body: body.to_string(),
                            subject: None,
                            predicate: None,
                        },
                    ),
                },
            )
            .await
            .expect("retain episode seed");
    }
    // Drain the reflect queue so recall reads compiled units, not degraded
    // read-your-own-writes episodes.
    loop {
        let tick = worker.run_worker_tick(256).await.expect("worker tick");
        // Fail closed: if the queue did not compile, recall below would read
        // degraded episodes and the SLO would be measured against the wrong
        // path. `completed == 0` alone could not tell that from an empty queue.
        assert!(tick.is_clean(), "reflect drain hit errors: {tick:?}");
        if tick.is_idle() {
            break;
        }
    }
}
