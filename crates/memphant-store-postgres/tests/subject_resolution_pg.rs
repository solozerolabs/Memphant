//! Semantic subject resolution vs `memphant_memory_unit_subject_valid_excl`,
//! on the REAL Postgres store.
//!
//! `memphant-core/tests/subject_resolution.rs` proves the resolution behaviour
//! against `InMemoryStore`, which has no exclusion constraint — and this
//! defect is invisible there. Verified: removing the guard under test leaves
//! all four in-memory tests green, because the in-memory compiler quietly
//! tolerates two candidates opening one subject where Postgres rejects them.
//! This is the Postgres twin, and it is the only layer that can pin it.
//!
//! The defect: two phrases mined from ONE episode can both sit above the
//! resolution threshold against the same open unit. If both adopt its
//! `fact_key`, the job opens one subject twice over overlapping validity and
//! the whole reflect job dies on the constraint. Observed draining the
//! ten-user HorizonBench sample at threshold 0.80
//! (`docs/build-log/2026-08-05-horizon-stage1-supersession-defect.md`).
//!
//! `#[ignore]`d like every live-PG contract; run under the AGENTS.md §37
//! scratch-DB leg.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{EmbedError, EmbeddingProvider, MemoryStore, SystemClock};
use memphant_store_postgres::PgStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    ResolvedMemoryContext, RetainEpisodeHttpRequest, TenantId,
};
use sqlx::Row;
use uuid::Uuid;

/// Episode 1 establishes one step-topical preference; episode 2 carries TWO,
/// so both of its mined phrases resolve toward episode 1's unit.
const EPISODES: [&str; 2] = [
    "[session s1]\nuser: I prefer getting this broken down step by step.\n",
    "[session s2]\nuser: I prefer having actual steps to follow.\n\
assistant: Noted.\n\
user: I also prefer sequential steps with numbers.\n",
];

/// Every step-topical phrase collapses to one axis, so each pair scores 1.0 and
/// the threshold cannot be what saves us — only the one-subject-per-job guard
/// can. Anything else gets its own axis.
struct TopicEmbedding;

impl EmbeddingProvider for TopicEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(texts
            .iter()
            .map(|text| {
                let lowered = text.to_ascii_lowercase();
                let stepish = ["step", "steps", "sequential"]
                    .iter()
                    .any(|marker| lowered.contains(marker));
                if stepish {
                    vec![1.0, 0.0]
                } else {
                    vec![0.0, 1.0]
                }
            })
            .collect())
    }

    fn dimensions(&self) -> usize {
        2
    }

    fn id(&self) -> &str {
        "topic-pg-test"
    }
}

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated scratch database")
}

fn body_hash(value: &str) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    std::hash::Hash::hash(value, &mut hasher);
    std::hash::Hasher::finish(&hasher)
}

async fn bind(label: &str) -> (ResolvedMemoryContext, PgStore) {
    let provisioner = PgStore::connect_provisioner(&db_url())
        .await
        .expect("connect provisioner store");
    let tenant_uuid = provisioner
        .create_tenant(&format!("{label}-{}", Uuid::new_v4().simple()))
        .await
        .expect("provision tenant");
    let tenant = TenantId::from_u128(tenant_uuid.as_u128());
    let store = PgStore::connect_app(&db_url(), &db_url())
        .await
        .expect("connect app store");
    let binding = store
        .resolve_context_binding(
            tenant,
            format!("{label}-client"),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: format!("{label}-subject"),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: format!("{label}-actor"),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: format!("{label}-scope"),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: format!("{label}-agent"),
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
    (context, store)
}

fn retain_request(context: &ResolvedMemoryContext, body: &str) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: format!("test:{:x}", body_hash(body)),
        observed_at: "2026-08-04T00:00:00Z".to_string(),
        payload: memphant_types::RetainPayload::Episode(memphant_types::RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
            subject: None,
            predicate: None,
        }),
    }
}

/// Ingests `EPISODES` with resolution at `threshold` and drains. Returns every
/// queue row that did not reach `done` — a tick swallows a per-job compile
/// failure, so the queue is the only honest verdict on whether the write path
/// completed.
async fn ingest_and_drain(label: &str, threshold: f32) -> Vec<(String, String)> {
    let (context, store) = bind(label).await;
    let app = MemoryService::new(
        Arc::new(store),
        Arc::new(SystemClock),
        Arc::new(TopicEmbedding),
    )
    .with_fact_extraction_enabled(true)
    .with_subject_resolution_threshold(Some(threshold));
    let worker = MemoryService::new(
        Arc::new(
            PgStore::connect_worker(&db_url())
                .await
                .expect("connect worker store"),
        ),
        Arc::new(SystemClock),
        Arc::new(TopicEmbedding),
    )
    .with_fact_extraction_enabled(true)
    .with_subject_resolution_threshold(Some(threshold));

    for body in EPISODES {
        app.retain(
            &context,
            &format!("test:{:x}", body_hash(body)),
            context.actor_trust,
            retain_request(&context, body),
        )
        .await
        .expect("retain");
    }
    for _ in 0..16 {
        let completed = worker.run_worker_tick(usize::MAX).await.expect("tick");
        if worker.pending_worker_job_count().await.expect("pending") == 0
            || completed.completed == 0
        {
            break;
        }
    }
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("queue pool");
    sqlx::query(
        "select state::text as state, coalesce(last_error, '') as last_error
           from memphant.job_state
          where tenant_id = $1 and (state <> 'done' or last_error is not null)",
    )
    .bind(context.tenant_id.as_uuid())
    .fetch_all(&pool)
    .await
    .expect("queue rows")
    .into_iter()
    .map(|row| {
        (
            row.get::<String, _>("state"),
            row.get::<String, _>("last_error"),
        )
    })
    .collect()
}

/// THE REGRESSION. Two phrases in one episode both resolve to the same open
/// subject. The drain must complete: before the guard, the second one dies on
/// `memphant_memory_unit_subject_valid_excl` and takes its whole job with it.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn two_resolved_phrases_in_one_episode_still_compile() {
    let stuck = ingest_and_drain("subj-collide", 0.9).await;
    assert!(
        stuck.is_empty(),
        "the drain must complete every reflect job; stuck={stuck:#?}"
    );
}

/// THE SECOND REGRESSION, and the deterministic one. Episode 2 restates
/// episode 1's phrase VERBATIM (so the compiler will derive episode 1's key for
/// it, after this stage runs) and also carries a near phrase. Resolving the
/// near phrase onto that same key puts two candidates on one subject; because
/// it is deterministic, every retry repeats it — 20 dead jobs and a blocked
/// scope lane on the Horizon sample at 0.80. Resolution must therefore respect
/// the keys the job will DERIVE, not only the ones it already carries.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn a_verbatim_restatement_beside_a_near_one_still_compiles() {
    let (context, store) = bind("subj-derived").await;
    let episodes = [
        "[session s1]\nuser: I prefer getting this broken down step by step.\n",
        // Verbatim repeat (destined for s1's derived key) + a near phrase.
        "[session s2]\nuser: I prefer getting this broken down step by step.\n\
assistant: Still noted.\n\
user: I also prefer sequential steps with numbers.\n",
    ];
    let app = MemoryService::new(
        Arc::new(store),
        Arc::new(SystemClock),
        Arc::new(TopicEmbedding),
    )
    .with_fact_extraction_enabled(true)
    .with_subject_resolution_threshold(Some(0.9));
    let worker = MemoryService::new(
        Arc::new(
            PgStore::connect_worker(&db_url())
                .await
                .expect("connect worker store"),
        ),
        Arc::new(SystemClock),
        Arc::new(TopicEmbedding),
    )
    .with_fact_extraction_enabled(true)
    .with_subject_resolution_threshold(Some(0.9));
    for body in episodes {
        app.retain(
            &context,
            &format!("test:{:x}", body_hash(body)),
            context.actor_trust,
            retain_request(&context, body),
        )
        .await
        .expect("retain");
    }
    for _ in 0..16 {
        let completed = worker.run_worker_tick(usize::MAX).await.expect("tick");
        if worker.pending_worker_job_count().await.expect("pending") == 0
            || completed.completed == 0
        {
            break;
        }
    }
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("queue pool");
    let bad: Vec<(String, String)> = sqlx::query(
        "select state::text as state, coalesce(last_error, '') as last_error
           from memphant.job_state
          where tenant_id = $1 and (state <> 'done' or last_error is not null)",
    )
    .bind(context.tenant_id.as_uuid())
    .fetch_all(&pool)
    .await
    .expect("queue rows")
    .into_iter()
    .map(|row| {
        (
            row.get::<String, _>("state"),
            row.get::<String, _>("last_error"),
        )
    })
    .collect();
    assert!(
        bad.is_empty(),
        "no job may die on the subject constraint; stuck={bad:#?}"
    );
}

/// Resolution off is the control: the same corpus must always have compiled,
/// so a failure here would mean the fixture, not the guard, is what is broken.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn the_same_corpus_compiles_with_resolution_off() {
    let (context, store) = bind("subj-off").await;
    let app = MemoryService::new(
        Arc::new(store),
        Arc::new(SystemClock),
        Arc::new(TopicEmbedding),
    )
    .with_fact_extraction_enabled(true);
    let worker = MemoryService::new(
        Arc::new(
            PgStore::connect_worker(&db_url())
                .await
                .expect("connect worker store"),
        ),
        Arc::new(SystemClock),
        Arc::new(TopicEmbedding),
    )
    .with_fact_extraction_enabled(true);
    for body in EPISODES {
        app.retain(
            &context,
            &format!("test:{:x}", body_hash(body)),
            context.actor_trust,
            retain_request(&context, body),
        )
        .await
        .expect("retain");
    }
    for _ in 0..16 {
        let completed = worker.run_worker_tick(usize::MAX).await.expect("tick");
        if worker.pending_worker_job_count().await.expect("pending") == 0
            || completed.completed == 0
        {
            break;
        }
    }
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("queue pool");
    let stuck: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state
          where tenant_id = $1 and (state <> 'done' or last_error is not null)",
    )
    .bind(context.tenant_id.as_uuid())
    .fetch_one(&pool)
    .await
    .expect("queue rows");
    assert_eq!(
        stuck, 0,
        "the control corpus must compile with resolution off"
    );
}
