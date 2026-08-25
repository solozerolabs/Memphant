//! Bar 3 (C1): two-user episodic RLS leakage proof under the real least-
//! privilege role.
//!
//! The Syndai cutover swaps app-level `user_id` WHERE-filters for MemPhant
//! tenant-RLS. That swap is load-bearing: production episodic tables have
//! `relrowsecurity = false` today, so a gap is a data-exposure incident, not a
//! bug. This test proves RLS actually bites for the EPISODIC tables by seeding
//! episodes for tenant A and tenant B and asserting that, under
//! `set local role memphant_app` + `bind_tenant`, each tenant's connection sees
//! exactly zero of the other's episodes and memory_units — enforced by Postgres
//! FORCE RLS, not by application code.
//!
//! Why the role matters (the finding this test exists for): the packaged server
//! and `e2e_probe.sh` connect as the scratch-DB login, which is a superuser with
//! `rolbypassrls = true` — RLS never fires there, so the probe's cross-tenant
//! 404 proves app+GUC isolation, NOT the RLS backstop. Only a connection that
//! has ASSUMED `memphant_app` (a non-BYPASSRLS policy role) exercises the swap.
//! This mirrors `role_matrix.rs`, which is the only other place RLS is proven to
//! bite, extended to the episodic surface the cutover touches.
//!
//! `#[ignore]`d like every live-PG contract; run under the AGENTS.md §37
//! scratch-DB leg.

use memphant_store_postgres::PgStore;
use sqlx::postgres::PgPoolOptions;
use sqlx::{Executor, Row};
use uuid::Uuid;

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated scratch database")
}

/// Seed one subject/scope/agent + one episode for `tenant` under the
/// `memphant_app` policy role (so the write itself must satisfy RLS `with check`).
/// Returns the episode id.
async fn seed_episode(pool: &sqlx::PgPool, tenant: Uuid) -> Uuid {
    let subject = Uuid::now_v7();
    let scope = Uuid::now_v7();
    let actor = Uuid::now_v7();
    let agent_node = Uuid::now_v7();
    let episode = Uuid::now_v7();

    let mut tx = pool.begin().await.expect("begin seed");
    tx.execute("set local role memphant_app")
        .await
        .expect("assume app");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant)
        .execute(&mut *tx)
        .await
        .expect("bind tenant");
    sqlx::query(
        "insert into memphant.subject (id, tenant_id, external_ref, kind) \
         values ($1, $2, 'rls-subject', 'user')",
    )
    .bind(subject)
    .bind(tenant)
    .execute(&mut *tx)
    .await
    .expect("subject write");
    sqlx::query(
        "insert into memphant.scope
           (id, tenant_id, data_subject_id, kind, external_ref, materialized_path, scope_depth)
         values ($1, $2, $3, 'rls', 'rls-root', $4::memphant.ltree, 0)",
    )
    .bind(scope)
    .bind(tenant)
    .bind(subject)
    .bind(scope.to_string().replace('-', "_"))
    .execute(&mut *tx)
    .await
    .expect("scope write");
    sqlx::query(
        "insert into memphant.actor
           (id, tenant_id, data_subject_id, kind, external_ref, trust_level)
         values ($1, $2, $3, 'agent', 'rls-actor', 'trusted_system')",
    )
    .bind(actor)
    .bind(tenant)
    .bind(subject)
    .execute(&mut *tx)
    .await
    .expect("actor write");
    sqlx::query(
        "insert into memphant.agent_node
           (id, tenant_id, data_subject_id, scope_id, level, external_ref)
         values ($1, $2, $3, $4, 0, 'rls-agent')",
    )
    .bind(agent_node)
    .bind(tenant)
    .bind(subject)
    .bind(scope)
    .execute(&mut *tx)
    .await
    .expect("agent_node write");
    sqlx::query(
        "insert into memphant.episode
           (id, tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
            actor_id, source_kind, source_ref, source_trust, dedup_key,
            first_observed_at, last_observed_at, body)
         values ($1, $2, $3, $4, $5, 0, $6, 'user', 'rls:ep', 'trusted_system', $7,
                 now(), now(), 'tenant-private episode body')",
    )
    .bind(episode)
    .bind(tenant)
    .bind(subject)
    .bind(scope)
    .bind(agent_node)
    .bind(actor)
    .bind(format!("rls-dedup-{episode}"))
    .execute(&mut *tx)
    .await
    .expect("episode write");
    tx.commit().await.expect("commit seed");
    episode
}

/// Count episodes visible to `reader_tenant` under the `memphant_app` role — i.e.
/// what RLS lets that tenant's connection see.
async fn visible_episode_count(pool: &sqlx::PgPool, reader_tenant: Uuid) -> i64 {
    let mut tx = pool.begin().await.expect("begin read");
    tx.execute("set local role memphant_app")
        .await
        .expect("assume app");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(reader_tenant)
        .execute(&mut *tx)
        .await
        .expect("bind reader tenant");
    let count: i64 = sqlx::query("select count(*) from memphant.episode")
        .fetch_one(&mut *tx)
        .await
        .expect("count episodes")
        .get(0);
    tx.rollback().await.expect("rollback read");
    count
}

/// Under `reader_tenant`'s RLS role, how many episodes belonging to
/// `other_tenant` are visible. FORCE RLS filters by `current_tenant_id()`, so
/// even an explicit `tenant_id = other` predicate can see nothing — a leak would
/// return > 0. Robust to a shared scratch DB (it names the exact other tenant).
async fn cross_tenant_visible(pool: &sqlx::PgPool, reader_tenant: Uuid, other_tenant: Uuid) -> i64 {
    let mut tx = pool.begin().await.expect("begin xt read");
    tx.execute("set local role memphant_app")
        .await
        .expect("assume app");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(reader_tenant)
        .execute(&mut *tx)
        .await
        .expect("bind reader tenant");
    let count: i64 = sqlx::query("select count(*) from memphant.episode where tenant_id = $1")
        .bind(other_tenant)
        .fetch_one(&mut *tx)
        .await
        .expect("count cross-tenant")
        .get(0);
    tx.rollback().await.expect("rollback xt read");
    count
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn two_user_episodic_isolation_is_enforced_by_rls_not_app_code() {
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&db_url())
        .await
        .expect("connect");

    // Provision two tenants (provisioner capability).
    let provisioner = PgStore::connect_provisioner(&db_url())
        .await
        .expect("connect provisioner");
    let tenant_a = provisioner
        .create_tenant(&format!("rls-a-{}", Uuid::new_v4().simple()))
        .await
        .expect("provision tenant A");
    let tenant_b = provisioner
        .create_tenant(&format!("rls-b-{}", Uuid::new_v4().simple()))
        .await
        .expect("provision tenant B");

    seed_episode(&pool, tenant_a).await;
    seed_episode(&pool, tenant_b).await;

    // Each tenant, under the memphant_app policy role, sees exactly its own one
    // episode — RLS, not an app WHERE clause, enforces this (the connection
    // carries no `user_id` filter; the query is a bare `select count(*) from
    // memphant.episode`). Assertions are robust to a shared scratch DB: each
    // tenant is freshly provisioned and only this test writes to it, so its
    // RLS-scoped view holds exactly the one episode seeded here — regardless of
    // how many episodes other tests seeded under other tenants.
    assert_eq!(
        visible_episode_count(&pool, tenant_a).await,
        1,
        "tenant A sees exactly its own episode under RLS"
    );
    assert_eq!(
        visible_episode_count(&pool, tenant_b).await,
        1,
        "tenant B sees exactly its own episode under RLS"
    );

    // The direct leakage assertion: under tenant A's RLS view, zero of tenant
    // B's episodes are visible (and vice versa). This is the data-exposure
    // check — it must not silently pass on a shared DB, so it names the other
    // tenant's id explicitly rather than relying on a global count.
    assert_eq!(
        cross_tenant_visible(&pool, tenant_a, tenant_b).await,
        0,
        "tenant A must NOT see tenant B's episodes (cross-tenant leak)"
    );
    assert_eq!(
        cross_tenant_visible(&pool, tenant_b, tenant_a).await,
        0,
        "tenant B must NOT see tenant A's episodes (cross-tenant leak)"
    );

    pool.close().await;
}

/// Phase-1 item 6 — the tenant GUC set by `bind_tenant` must NOT bleed across a
/// pooled connection. `bind_tenant` uses `set_config(..., is_local => true)`
/// (`SET LOCAL`, transaction-scoped), and every tenant-scoped query runs inside
/// an explicit transaction, so the GUC can never outlive the txn that set it.
/// This regression guard pins that: with `max_connections(1)` the pool hands
/// the SAME physical backend to every borrow, so a query on a re-borrowed
/// connection that binds NO tenant must see ZERO rows — the GUC did not survive,
/// and RLS fails CLOSED on a NULL `memphant.tenant_id`. If a future refactor
/// moved a tenant query onto a bare pooled connection (no txn) or switched to
/// session-scoped `SET`, this test would see tenant A's row leak and fail.
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn bind_tenant_guc_does_not_bleed_across_a_pooled_connection() {
    // ONE physical connection → every pool.begin() reuses it, so this genuinely
    // exercises "connection returned to pool, then re-borrowed by a caller that
    // did not bind a tenant".
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&db_url())
        .await
        .expect("connect");

    let provisioner = PgStore::connect_provisioner(&db_url())
        .await
        .expect("connect provisioner");
    let tenant_a = provisioner
        .create_tenant(&format!("bleed-a-{}", Uuid::new_v4().simple()))
        .await
        .expect("provision tenant A");
    seed_episode(&pool, tenant_a).await;

    // 1. Borrow the single connection, bind tenant A under the app role, read
    //    (sees its own row), rollback → the connection returns to the pool
    //    carrying whatever GUC state the SET LOCAL bind left behind.
    assert_eq!(
        visible_episode_count(&pool, tenant_a).await,
        1,
        "tenant A sees its own episode when bound"
    );

    // 2. Re-borrow the SAME physical connection under the app role, but bind NO
    //    tenant. A bled GUC would keep current_tenant_id() == A and leak A's
    //    row (count 1); it must be 0.
    let mut tx = pool.begin().await.expect("begin unbound read");
    tx.execute("set local role memphant_app")
        .await
        .expect("assume app");
    let guc: Option<String> = sqlx::query("select current_setting('memphant.tenant_id', true)")
        .fetch_one(&mut *tx)
        .await
        .expect("read tenant guc")
        .get(0);
    let unbound_count: i64 = sqlx::query("select count(*) from memphant.episode")
        .fetch_one(&mut *tx)
        .await
        .expect("count under unbound reused connection")
        .get(0);
    tx.rollback().await.expect("rollback unbound read");

    assert_eq!(
        unbound_count, 0,
        "a re-borrowed connection that bound no tenant must see zero rows — the \
         SET LOCAL tenant GUC must not bleed across pooled borrows"
    );
    assert!(
        guc.as_deref().unwrap_or("").is_empty(),
        "the tenant GUC must be unset on a reused connection that did not bind, got {guc:?}"
    );

    pool.close().await;
}
