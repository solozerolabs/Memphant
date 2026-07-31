//! W6 fact extraction vs `memphant_memory_unit_subject_valid_excl`, on the
//! REAL Postgres store.
//!
//! `memphant-core/tests/fact_extraction.rs` proves the same write path against
//! `InMemoryStore`, which has no exclusion constraint — so it structurally
//! cannot catch a divergence between what the supersede scan sees and what the
//! constraint enforces. This is the Postgres twin of that file.
//!
//! The divergence under test: when the retaining actor is NOT high trust
//! (`actor_kind = "agent"` ⇒ `TrustLevel::AgentOutput` — the binding every
//! agent-trajectory lane uses), the compiler's low-trust projection branch
//! mints a `belief`/`candidate` unit carrying the mined subject key and NEVER
//! supersedes. The exclusion constraint covers `kind in ('semantic','belief')`,
//! so the second distinct body under the same mined key collides.
//!
//! `#[ignore]`d like every live-PG contract; run under the AGENTS.md §37
//! scratch-DB leg.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{MemoryStore, StubEmbedding, SystemClock, derive_fact_key};
use memphant_store_postgres::PgStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    ResolvedMemoryContext, RetainEpisodeHttpRequest, TenantId,
};
use sqlx::Row;
use uuid::Uuid;

/// Two episodes whose USER turns mine the SAME `{scope}:preference:favorite tea`
/// key with DIFFERENT bodies — the minimal update chain W6 exists to serve.
const EPISODES: [&str; 2] = [
    "[session s1]\nuser: My favorite tea is chamomile.\n",
    "[session s2]\nuser: My favorite tea is rooibos now.\n",
];

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated scratch database")
}

async fn bind(label: &str, actor_kind: &str) -> (ResolvedMemoryContext, PgStore) {
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
                    kind: actor_kind.to_string(),
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
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        payload: memphant_types::RetainPayload::Episode(memphant_types::RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
        }),
    }
}

fn body_hash(value: &str) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    std::hash::Hash::hash(value, &mut hasher);
    std::hash::Hasher::finish(&hasher)
}

/// Rows the exclusion constraint's own predicate sees, for one mined key.
async fn open_rows(fact_key: &str) -> String {
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("dump pool");
    let rows = sqlx::query(
        "select kind::text as kind, state::text as state, subject_generation,
                to_char(valid_from at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as valid_from,
                to_char(valid_to at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as valid_to,
                to_char(transaction_to at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as transaction_to,
                body
           from memphant.memory_unit where fact_key = $1
          order by transaction_from, id",
    )
    .bind(fact_key)
    .fetch_all(&pool)
    .await
    .expect("dump rows");
    let mut out = String::new();
    for row in rows {
        out.push_str(&format!(
            "  kind={} state={} generation={} valid=[{:?},{:?}) transaction_to={:?} body={:?}\n",
            row.get::<String, _>("kind"),
            row.get::<String, _>("state"),
            row.get::<i64, _>("subject_generation"),
            row.get::<Option<String>, _>("valid_from"),
            row.get::<Option<String>, _>("valid_to"),
            row.get::<Option<String>, _>("transaction_to"),
            row.get::<String, _>("body"),
        ));
    }
    out
}

/// Ingests `EPISODES` under `actor_kind` and drains. Returns the mined key and
/// every queue row that did not reach `done` — the tick swallows a per-job
/// compile failure (release + eprintln), so the queue is the only honest
/// verdict on whether the write path completed.
async fn ingest_and_drain(label: &str, actor_kind: &str) -> (String, Vec<(String, String)>) {
    let (context, store) = bind(label, actor_kind).await;
    let fact_key = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("preference"),
        Some("favorite tea"),
        "",
    );
    let app = MemoryService::new(
        Arc::new(store),
        Arc::new(SystemClock),
        Arc::new(StubEmbedding::default()),
    )
    .with_fact_extraction_enabled(true);
    let worker = MemoryService::new(
        Arc::new(
            PgStore::connect_worker(&db_url())
                .await
                .expect("connect worker store"),
        ),
        Arc::new(SystemClock),
        Arc::new(StubEmbedding::default()),
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
        if worker.pending_worker_job_count().await.expect("pending") == 0 || completed == 0 {
            break;
        }
    }
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("queue pool");
    let stuck = sqlx::query(
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
    (fact_key, stuck)
}

/// THE REGRESSION. An agent-trust lane is the code lane's own binding
/// (`actor_kind = "agent"` ⇒ `AgentOutput`). Two episodes mining one key must
/// compile — before the fix the second one dies on the exclusion constraint.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn agent_trust_episodes_sharing_a_mined_key_compile() {
    let (fact_key, stuck) = ingest_and_drain("fx-agent", "agent").await;
    assert!(
        stuck.is_empty(),
        "the drain must complete every reflect job; stuck={stuck:#?}\nkey={fact_key}\n{}",
        open_rows(&fact_key).await
    );
}

/// The high-trust control: the same two episodes under `actor_kind = "user"`
/// take the supersession branch and tile the valid axis. This must keep
/// passing — it is what proves the fix did not simply disable the constraint's
/// job on the semantic path.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn user_trust_episodes_sharing_a_mined_key_supersede() {
    let (fact_key, stuck) = ingest_and_drain("fx-user", "user").await;
    assert!(
        stuck.is_empty(),
        "the drain must complete every reflect job; stuck={stuck:#?}\nkey={fact_key}\n{}",
        open_rows(&fact_key).await
    );
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("pool");
    let superseded: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_unit
          where fact_key = $1 and state = 'superseded' and transaction_to is not null",
    )
    .bind(&fact_key)
    .fetch_one(&pool)
    .await
    .expect("count");
    assert_eq!(
        superseded, 1,
        "the later value supersedes the earlier one\n{}",
        open_rows(&fact_key).await
    );
}

/// EVIDENCE, not an assertion: with the exclusion constraint dropped, both
/// rows commit, so the pair that Postgres refuses can be read directly. Run
/// with `--nocapture`.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "evidence capture; requires MEMPHANT_TEST_DATABASE_URL"]
async fn capture_the_colliding_pair_with_the_constraint_dropped() {
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("pool");
    sqlx::query(
        "alter table memphant.memory_unit
           drop constraint if exists memphant_memory_unit_subject_valid_excl",
    )
    .execute(&pool)
    .await
    .expect("drop constraint");
    let (fact_key, stuck) = ingest_and_drain("fx-evidence", "agent").await;
    println!("key={fact_key}\nstuck={stuck:#?}\n{}", open_rows(&fact_key).await);
}
