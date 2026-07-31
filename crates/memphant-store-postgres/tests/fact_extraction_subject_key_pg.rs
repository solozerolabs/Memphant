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
    ResolvedMemoryContext, RetainEpisodeHttpRequest, TenantId, TrustLevel,
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
        if worker.pending_worker_job_count().await.expect("pending") == 0
            || completed.completed == 0
        {
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
        superseded,
        1,
        "the later value supersedes the earlier one\n{}",
        open_rows(&fact_key).await
    );
}

// ---------------------------------------------------------------------------
// Sibling collision classes on the SAME key space, probed against the same
// constraint. Both mint through paths that never consult the semantic
// supersede scan.
// ---------------------------------------------------------------------------

/// CONDITION 1. Belief promotion (`lib.rs:11562`) mints a `Semantic` unit on
/// the mined key INSTEAD of running the semantic supersede branch. If an open
/// `Semantic` already holds that key, promotion adds a second one — and
/// `semantic` stays inside the exclusion constraint after the belief
/// narrowing, so this would be the same crash in a new place.
///
/// Mixed-trust lane over ONE context, which is exactly what `clamp_trust`
/// produces: the assigned trust of a retain is
/// `min(actor_trust, api_key.max_trust)`, so two API keys with different
/// `max_trust` writing through one context binding yield both trust levels on
/// one key space. (Two *actors* cannot share a lane — `context_binding` is
/// unique per (tenant, subject, agent_node) — so the API key is the seam.)
///   1. AgentOutput "…is chamomile"   ⇒ belief/candidate on K
///   2. TrustedUser "…is rooibos now" ⇒ semantic/active  on K (no incumbent
///                                       semantic, so it appends unbounded)
///   3. TrustedUser "…is chamomile"   ⇒ body-matches the belief ⇒ PROMOTE ⇒
///                                       a second unbounded semantic on K
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn belief_promotion_does_not_double_open_a_semantic_key() {
    let (context, store) = bind("fx-promote", "user").await;
    let tenant = context.tenant_id;
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

    let steps = [
        (
            "a1",
            TrustLevel::AgentOutput,
            "[session a1]\nuser: My favorite tea is chamomile.\n",
        ),
        (
            "u1",
            TrustLevel::TrustedUser,
            "[session u1]\nuser: My favorite tea is rooibos now.\n",
        ),
        (
            "u2",
            TrustLevel::TrustedUser,
            "[session u2]\nuser: My favorite tea is chamomile.\n",
        ),
    ];
    for (tag, trust, body) in steps {
        let mut request = retain_request(&context, body);
        request.source_ref = format!("test:{tag}");
        app.retain(&context, &format!("test:{tag}"), trust, request)
            .await
            .expect("retain");
        for _ in 0..8 {
            let completed = worker.run_worker_tick(usize::MAX).await.expect("tick");
            if worker.pending_worker_job_count().await.expect("pending") == 0
                || completed.completed == 0
            {
                break;
            }
        }
    }

    let fact_key = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("preference"),
        Some("favorite tea"),
        "",
    );
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("pool");
    let stuck: Vec<(String, String)> = sqlx::query(
        "select state::text as state, coalesce(last_error, '') as last_error
           from memphant.job_state
          where tenant_id = $1 and (state <> 'done' or last_error is not null)",
    )
    .bind(tenant.as_uuid())
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
        stuck.is_empty(),
        "promotion must not collide; stuck={stuck:#?}\nkey={fact_key}\n{}",
        open_rows(&fact_key).await
    );
    // The invariant is NOT "one open semantic row" — the bitemporal model
    // deliberately leaves the historical remainder open alongside the current
    // generation, and they tile the valid axis. The invariant is that no two
    // open rows on the key OVERLAP in valid time, which is exactly what the
    // exclusion constraint enforces and what promotion was violating.
    let overlaps: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_unit a
           join memphant.memory_unit b
             on b.fact_key = a.fact_key and b.kind = a.kind and b.id > a.id
            and b.scope_id = a.scope_id and b.agent_node_id = a.agent_node_id
            and b.transaction_to is null
            and tstzrange(a.valid_from, a.valid_to, '[)')
                && tstzrange(b.valid_from, b.valid_to, '[)')
          where a.fact_key = $1 and a.kind = 'semantic' and a.transaction_to is null",
    )
    .bind(&fact_key)
    .fetch_one(&pool)
    .await
    .expect("overlap count");
    assert_eq!(
        overlaps,
        0,
        "open semantic generations must tile the valid axis, not overlap\n{}",
        open_rows(&fact_key).await
    );
    // And the promotion must still have HAPPENED: the belief stays open and a
    // semantic carrying the promoted body is the current generation.
    let promoted: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_unit
          where fact_key = $1 and kind = 'semantic' and state = 'active'
            and transaction_to is null and valid_to is null
            and body = 'My favorite tea is chamomile'",
    )
    .bind(&fact_key)
    .fetch_one(&pool)
    .await
    .expect("promoted count");
    assert_eq!(
        promoted,
        1,
        "the belief is still promoted; it just closes what it replaced\n{}",
        open_rows(&fact_key).await
    );
}

/// CONDITION 3. `compose_inferred_beliefs` dedups composed units by BODY but
/// keys them by OBJECT (`{scope}:user_preference:{object}`). A third
/// observation whose descriptor sorts ahead of the existing pair changes the
/// composed body while the key stays put, so a second open belief lands on the
/// same key — the same class as the low-trust projection collision, reached
/// through a different door.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn recomposed_inferred_belief_does_not_double_open_its_object_key() {
    let (context, store) = bind("fx-compose", "user").await;
    let app = MemoryService::new(
        Arc::new(store),
        Arc::new(SystemClock),
        Arc::new(StubEmbedding::default()),
    )
    .with_fact_extraction_enabled(true);

    // Descriptors are ingested worst-sorting first, so the third one displaces
    // a member of the composed pair and rewrites the composed body.
    for (tag, descriptor) in [("d1", "dark"), ("d2", "quiet"), ("d3", "airy")] {
        let request = RetainEpisodeHttpRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_ref: format!("test:compose:{tag}"),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            payload: memphant_types::RetainPayload::Unit(memphant_types::RetainUnitPayload {
                kind: memphant_types::MemoryKind::Semantic,
                fact_key: format!("observation:{tag}"),
                predicate: "coffee shops".to_string(),
                body: format!("The user prefers {descriptor} coffee shops."),
                confidence: 0.9,
                valid_from: None,
                valid_to: None,
            }),
        };
        app.retain(
            &context,
            &format!("test:compose:{tag}"),
            context.actor_trust,
            request,
        )
        .await
        .unwrap_or_else(|error| {
            panic!("composed retain {tag} failed: {error}");
        });
    }

    let composed_key = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("user preference"),
        Some("coffee shops"),
        "",
    );
    let pool = sqlx::PgPool::connect(&db_url()).await.expect("pool");
    let open: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_unit
          where fact_key = $1 and transaction_to is null",
    )
    .bind(&composed_key)
    .fetch_one(&pool)
    .await
    .expect("count");
    assert!(
        open >= 1,
        "the composition path must actually have fired\n{}",
        open_rows(&composed_key).await
    );
    println!(
        "composed key={composed_key} open={open}\n{}",
        open_rows(&composed_key).await
    );
}

/// THE RULE, pinned rather than restated: the exclusion constraint's `kind`
/// predicate must be exactly the set of kinds whose write-router arm owns
/// close-generation supersession. Both sides are derived — the Rust set from
/// `supersedes_own_kind` over `MemoryKind::ALL`, the SQL set from the LIVE
/// constraint definition in the migrated database (not the shipped file, so a
/// migration that fails to apply is caught too).
///
/// This exists because the two halves of this invariant have drifted twice in
/// opposite directions: `belief` was asserted by the schema and never
/// maintained by the router, and `preference` was maintained by the router and
/// never asserted by the schema. A hand-maintained kind list is what let both
/// happen, so neither side is written down here.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn exclusion_predicate_matches_the_supersedes_own_kind_set() {
    let mut expected: Vec<String> = memphant_types::MemoryKind::ALL
        .into_iter()
        .filter(|kind| memphant_core::supersedes_own_kind(*kind) == Some(*kind))
        .map(|kind| {
            // The wire name is the storage name; deriving it keeps this test
            // from carrying its own copy of the kind spelling.
            serde_json::to_value(kind)
                .expect("kind serializes")
                .as_str()
                .expect("kind is a string")
                .to_string()
        })
        .collect();
    expected.sort();
    assert!(
        !expected.is_empty(),
        "at least one arm must own supersession, or the constraint is vacuous"
    );

    let pool = sqlx::PgPool::connect(&db_url()).await.expect("pool");
    let definition: String = sqlx::query_scalar(
        "select pg_get_constraintdef(oid) from pg_constraint
          where conname = 'memphant_memory_unit_subject_valid_excl'",
    )
    .fetch_one(&pool)
    .await
    .expect("the exclusion constraint must exist");

    let mut found: Vec<String> = memphant_types::MemoryKind::ALL
        .into_iter()
        .map(|kind| {
            serde_json::to_value(kind)
                .expect("kind serializes")
                .as_str()
                .expect("kind is a string")
                .to_string()
        })
        .filter(|name| definition.contains(&format!("'{name}'")))
        .collect();
    found.sort();

    assert_eq!(
        found, expected,
        "the exclusion constraint's kind predicate must equal the \
         supersedes_own_kind set; constraint was:\n{definition}"
    );
}
