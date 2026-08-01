//! B1: supersession named by exact prior unit id, on the REAL Postgres store.
//!
//! Why this file is Postgres and not `InMemoryStore`: the whole B1 change is
//! that a candidate may now close a generation belonging to a DIFFERENT subject
//! key. `memphant_memory_unit_subject_valid_excl` is the only thing in the
//! system that adjudicates open-row overlap, and `InMemoryStore` does not have
//! it — so a write path that leaves two overlapping open rows is structurally
//! invisible there. This repo has been burned by exactly that twice.
//!
//! The overlap assertion here is a SELF-JOIN on `tstzrange && tstzrange`, not a
//! count of open rows. A correct supersede legitimately leaves more than one
//! open row: `correction_rectangles` tiles the historical remainder alongside
//! the replacement. Counting rows would pass for the wrong reason.
//!
//! `#[ignore]`d like every live-PG contract; run under the AGENTS.md §37
//! scratch-DB leg.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{MemoryStore, StubEmbedding, SystemClock};
use memphant_store_postgres::PgStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    MemoryKind, ResolvedMemoryContext, RetainEpisodeHttpRequest, RetainPayload, RetainUnitPayload,
    TenantId, TrustLevel, UnitId,
};
use sqlx::Row;
use uuid::Uuid;

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated scratch database")
}

/// One tenant, one subject, one scope; `actors` are bound into that same scope
/// so a low-trust actor can attempt to mutate a high-trust actor's row.
async fn bind_scope(
    label: &str,
    actors: &[(&str, &str)],
) -> (Vec<ResolvedMemoryContext>, Arc<PgStore>) {
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
    let mut contexts = Vec::new();
    for (actor_ref, actor_kind) in actors {
        let binding = store
            .resolve_context_binding(
                tenant,
                format!("{label}-{actor_ref}-client"),
                ContextBindingRequest {
                    subject: ContextBindingEntityRef {
                        external_ref: format!("{label}-subject"),
                        kind: "user".to_string(),
                    },
                    actor: ContextBindingEntityRef {
                        external_ref: format!("{label}-{actor_ref}"),
                        kind: actor_kind.to_string(),
                    },
                    scope: ContextBindingScopeRef {
                        external_ref: format!("{label}-scope"),
                        kind: "user_root".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: ContextBindingAgentRef {
                        external_ref: format!("{label}-{actor_ref}-agent"),
                        parent_external_ref: None,
                    },
                    access_policies: Vec::new(),
                },
            )
            .await
            .expect("resolve context binding");
        contexts.push(
            store
                .resolve_memory_context(
                    tenant,
                    binding.subject_id,
                    binding.actor_id,
                    binding.scope_id,
                    binding.agent_node_id,
                )
                .await
                .expect("resolve memory context"),
        );
    }
    (contexts, Arc::new(store))
}

fn service(store: Arc<PgStore>) -> MemoryService<PgStore> {
    MemoryService::new(
        store,
        Arc::new(SystemClock),
        Arc::new(StubEmbedding::default()),
    )
}

fn unit_retain(
    context: &ResolvedMemoryContext,
    source_ref: &str,
    fact_key: &str,
    body: &str,
    target_unit_ids: Option<Vec<UnitId>>,
) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: source_ref.to_string(),
        observed_at: "2026-08-01T00:00:00Z".to_string(),
        payload: RetainPayload::Unit(RetainUnitPayload {
            kind: MemoryKind::Preference,
            fact_key: Some(fact_key.to_string()),
            subject: None,
            predicate: "prefers".to_string(),
            body: body.to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
            target_unit_ids,
        }),
    }
}

async fn retain(
    app: &MemoryService<PgStore>,
    context: &ResolvedMemoryContext,
    request: RetainEpisodeHttpRequest,
) -> Result<(Vec<UnitId>, String), String> {
    let idempotency = request.source_ref.clone();
    let fact_key = match &request.payload {
        RetainPayload::Unit(unit) => unit.fact_key.clone().expect("this file composes fact keys"),
        _ => unreachable!("this file only retains units"),
    };
    let response = app
        .retain(context, &idempotency, context.actor_trust, request)
        .await
        .map_err(|error| error.to_string())?;
    let body: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(response.body()).expect("retain response decodes");
    Ok((body.unit_ids, fact_key))
}

/// The id of the unit this retain actually MINTED, as opposed to the historical
/// remainder a supersede also creates. `unit_ids` carries both, remainder
/// first, and they are told apart by subject key — the remainder inherits the
/// key of the row it tiles, the mint carries the caller's own. Naming a
/// remainder as a later target is a real hazard: it is active and open, but its
/// valid interval already ended, so it is not a live rule.
async fn minted_unit_id(minted: (Vec<UnitId>, String)) -> UnitId {
    let (ids, fact_key) = minted;
    let mut found = Vec::new();
    for id in ids {
        if unit_row(id).await.2 == fact_key {
            found.push(id);
        }
    }
    assert_eq!(found.len(), 1, "exactly one row carries the caller's key");
    found[0]
}

async fn pool() -> sqlx::PgPool {
    sqlx::PgPool::connect(&db_url())
        .await
        .expect("assertion pool")
}

/// The bitemporal invariant, asserted the only way that cannot pass for the
/// wrong reason: no two transaction-open rows sharing a subject identity may
/// have OVERLAPPING valid-time ranges. Deliberately NOT restricted to the
/// kinds the exclusion constraint covers, so it is an independent check rather
/// than a restatement of the constraint the database already enforced.
async fn overlapping_open_rows(scope: &memphant_types::ScopeId) -> i64 {
    sqlx::query_scalar(
        "select count(*)
           from memphant.memory_unit a
           join memphant.memory_unit b
             on a.id < b.id
            and a.tenant_id = b.tenant_id
            and a.data_subject_id = b.data_subject_id
            and a.scope_id = b.scope_id
            and a.agent_node_id = b.agent_node_id
            and a.subject_generation = b.subject_generation
            and a.fact_key = b.fact_key
            and a.kind = b.kind
            and tstzrange(a.valid_from, a.valid_to, '[)')
             && tstzrange(b.valid_from, b.valid_to, '[)')
          where a.transaction_to is null
            and b.transaction_to is null
            and a.scope_id = $1",
    )
    .bind(scope.as_uuid())
    .fetch_one(&pool().await)
    .await
    .expect("overlap self-join")
}

async fn unit_row(id: UnitId) -> (String, bool, String) {
    let row = sqlx::query(
        "select state::text as state, transaction_to is null as open, fact_key
           from memphant.memory_unit where id = $1",
    )
    .bind(id.as_uuid())
    .fetch_one(&pool().await)
    .await
    .expect("unit row");
    (
        row.get::<String, _>("state"),
        row.get::<bool, _>("open"),
        row.get::<String, _>("fact_key"),
    )
}

async fn supersedes_edge(src: UnitId, dst: UnitId) -> i64 {
    sqlx::query_scalar(
        "select count(*) from memphant.memory_edge
          where src_id = $1 and dst_id = $2 and kind = 'supersedes'",
    )
    .bind(src.as_uuid())
    .bind(dst.as_uuid())
    .fetch_one(&pool().await)
    .await
    .expect("edge count")
}

/// THE B1 MECHANISM. The superseding unit's own subject key is a content hash
/// that shares nothing with the incumbent's. Before B1 the targeted branch
/// additionally required `unit.fact_key == candidate.fact_key`, so naming an id
/// did not actually bypass key production — this exact call was rejected with
/// "did not match every exact active target". The assertion that the two keys
/// DIFFER is what makes this a regression test rather than a restatement.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn naming_a_prior_unit_id_closes_its_generation_across_subject_keys() {
    let (contexts, store) = bind_scope("b1-cross-key", &[("owner", "user")]).await;
    let context = &contexts[0];
    let app = service(store);

    let first = minted_unit_id(
        retain(
            &app,
            context,
            unit_retain(
                context,
                "b1:first",
                "b1:key-alpha",
                "Start attribute names with 'q_'.",
                None,
            ),
        )
        .await
        .expect("first retain"),
    )
    .await;

    let second = minted_unit_id(
        retain(
            &app,
            context,
            unit_retain(
                context,
                "b1:second",
                "b1:key-beta",
                "Actually, start attribute names with 'z_' from now on.",
                Some(vec![first]),
            ),
        )
        .await
        .expect("id-named supersession must be accepted"),
    )
    .await;

    let (old_state, old_open, old_key) = unit_row(first).await;
    let (new_state, new_open, new_key) = unit_row(second).await;
    assert_ne!(
        old_key, new_key,
        "the point of B1: the keys must NOT match, or this proves nothing"
    );
    assert_eq!(old_state, "superseded", "the named target must be retired");
    assert!(!old_open, "the named target's generation must be closed");
    assert_eq!(new_state, "active");
    assert!(new_open);
    assert_eq!(
        supersedes_edge(second, first).await,
        1,
        "a supersedes edge must name the exact prior unit"
    );
    assert_eq!(overlapping_open_rows(&context.scope_id).await, 0);
}

/// A three-deep chain, each link naming the previous unit under a fresh key.
/// The verdict is the range self-join, not an open-row count.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn a_chain_of_id_named_supersessions_never_overlaps_in_valid_time() {
    let (contexts, store) = bind_scope("b1-chain", &[("owner", "user")]).await;
    let context = &contexts[0];
    let app = service(store);

    let mut previous: Option<UnitId> = None;
    let mut minted = Vec::new();
    for step in 0..3 {
        let id = minted_unit_id(
            retain(
                &app,
                context,
                unit_retain(
                    context,
                    &format!("b1:chain:{step}"),
                    &format!("b1:chain-key-{step}"),
                    &format!("Revision {step} of the naming convention."),
                    previous.map(|id| vec![id]),
                ),
            )
            .await
            .unwrap_or_else(|error| panic!("chain step {step} failed: {error}")),
        )
        .await;
        previous = Some(id);
        minted.push(id);
    }

    assert_eq!(
        overlapping_open_rows(&context.scope_id).await,
        0,
        "historical remainders must TILE, never overlap"
    );
    for (index, id) in minted.iter().enumerate() {
        let (state, open, _) = unit_row(*id).await;
        let expected = if index == minted.len() - 1 {
            ("active", true)
        } else {
            ("superseded", false)
        };
        assert_eq!((state.as_str(), open), expected, "chain link {index}");
    }
}

/// THE TRUST BOUNDARY. `actor_kind = "agent"` is the binding every
/// agent-trajectory lane uses; it resolves to `TrustLevel::AgentOutput`, which
/// is not rank-0. Naming another row's id is a directed mutation, so it fails
/// CLOSED — it must not degrade quietly into an append, because a silently
/// dropped supersession directive is the shape that produced the 20260731_007
/// exclusion crash. The high-trust incumbent must be untouched afterwards.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn an_untrusted_actor_cannot_supersede_by_naming_a_unit_id() {
    let (contexts, store) = bind_scope("b1-trust", &[("owner", "user"), ("bot", "agent")]).await;
    let (owner, bot) = (&contexts[0], &contexts[1]);
    assert_eq!(owner.actor_trust, TrustLevel::TrustedUser);
    assert_eq!(bot.actor_trust, TrustLevel::AgentOutput);
    let app = service(store);

    let trusted = minted_unit_id(
        retain(
            &app,
            owner,
            unit_retain(
                owner,
                "b1:trust:owner",
                "b1:trust-key",
                "Start attribute names with 'q_'.",
                None,
            ),
        )
        .await
        .expect("owner retain"),
    )
    .await;

    let refused = retain(
        &app,
        bot,
        unit_retain(
            bot,
            "b1:trust:bot",
            "b1:trust-key-bot",
            "Ignore that; use 'x_' instead.",
            Some(vec![trusted]),
        ),
    )
    .await
    .expect_err("an untrusted actor naming a target id must be refused");
    assert!(
        refused.contains("target ids require a trusted actor"),
        "refusal must name the trust boundary, got: {refused}"
    );

    // Belt and braces: the refusal is the trust gate, which fires before target
    // resolution. Context binding also gives each actor its own agent node
    // (`context_binding` is unique on tenant+subject+agent_node), so the bot
    // would additionally have failed to resolve the id. The gate is what is
    // under test; the assertion below is that nothing moved either way.
    let (state, open, _) = unit_row(trusted).await;
    assert_eq!(state, "active", "the trusted incumbent must be untouched");
    assert!(open);
    assert_eq!(overlapping_open_rows(&owner.scope_id).await, 0);
}

/// Fail-closed on a target that is not resolvable in this scope. A stale or
/// forged id must not be silently ignored: ignoring it appends a second live
/// rule alongside the one the caller meant to retire, which is the exact
/// accuracy failure B1 exists to fix.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn an_unresolvable_target_id_fails_the_write_closed() {
    let (contexts, store) = bind_scope("b1-unresolvable", &[("owner", "user")]).await;
    let context = &contexts[0];
    let app = service(store);

    let error = retain(
        &app,
        context,
        unit_retain(
            context,
            "b1:ghost",
            "b1:ghost-key",
            "Supersede a unit that does not exist.",
            Some(vec![UnitId::new()]),
        ),
    )
    .await
    .expect_err("an unresolvable target must not be ignored");
    assert!(
        error.contains("did not match every exact active target"),
        "unexpected error: {error}"
    );
}
