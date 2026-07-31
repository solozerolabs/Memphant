//! W3.3: the served path itself must run under `memphant_app`.
//!
//! `role_matrix.rs` and `episodic_rls_leakage.rs` prove the POLICIES bite once
//! a transaction has issued `set local role memphant_app`. Neither proves the
//! SERVER does that — and until this landed it did not: `connect_pool` set only
//! `search_path`, no migration shipped a login role that was a member of
//! `memphant_app`, and every packaging path (compose, the Neon and Supabase
//! profiles) shipped a credential with `rolbypassrls = true`. FORCE RLS on 28
//! tables was decorative on the served path.
//!
//! These tests close that hole from the outside: they hand `PgStore::connect_app`
//! an ordinary login role that is a member of `memphant_app` and NOTHING else
//! (NOINHERIT, no direct schema grants), then run a bare cross-tenant query
//! through the store's OWN pool. The harness never issues `set local role`; if
//! `connect_pool` stops assuming the capability role the connection loses every
//! privilege and these tests fail loudly.
//!
//! `#[ignore]`d like every live-PG contract; run under the AGENTS.md §37
//! scratch-DB leg.

use memphant_core::MemoryStore;
use memphant_store_postgres::PgStore;
use sqlx::postgres::PgPoolOptions;
use sqlx::{AssertSqlSafe, Row};
use uuid::Uuid;

const LOGIN_PASSWORD: &str = "served_path_rls_password";

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated scratch database")
}

fn login_url(base: &str, role: &str) -> String {
    let (_, host) = base.split_once('@').expect("database URL has credentials");
    let scheme = if base.starts_with("postgresql://") {
        "postgresql://"
    } else {
        "postgres://"
    };
    format!("{scheme}{role}:{LOGIN_PASSWORD}@{host}")
}

/// Mint a throwaway login role that is a member of `capability` and holds no
/// other privilege — the shape `memphant_app_login` has in production, but
/// unique per run so concurrent scratch databases on the same cluster (roles
/// are cluster-global) never collide.
async fn mint_login(root: &sqlx::PgPool, capability: &str, suffix: &str) -> String {
    let login = format!("mp_served_{capability}_{suffix}");
    for statement in [
        format!("create role {login} login noinherit password '{LOGIN_PASSWORD}'"),
        format!("grant {capability} to {login}"),
        format!("revoke all on schema memphant from {login}"),
    ] {
        sqlx::query(AssertSqlSafe(statement.as_str()))
            .execute(root)
            .await
            .unwrap_or_else(|error| panic!("{statement}: {error}"));
    }
    login
}

/// Seed one tenant-owned episode with the owner credential (which holds the
/// `using(true)` owner policy), so the seeding path is independent of the
/// served path under test.
struct Seed {
    subject: Uuid,
    scope: Uuid,
    actor: Uuid,
    agent_node: Uuid,
    episode: Uuid,
}

async fn seed_episode(root: &sqlx::PgPool, tenant: Uuid) -> Seed {
    let subject = Uuid::now_v7();
    let scope = Uuid::now_v7();
    let actor = Uuid::now_v7();
    let agent_node = Uuid::now_v7();
    let episode = Uuid::now_v7();
    sqlx::query(
        "insert into memphant.subject (id, tenant_id, external_ref, kind) \
         values ($1, $2, 'served-rls-subject', 'user')",
    )
    .bind(subject)
    .bind(tenant)
    .execute(root)
    .await
    .expect("subject write");
    sqlx::query(
        "insert into memphant.scope
           (id, tenant_id, data_subject_id, kind, external_ref, materialized_path, scope_depth)
         values ($1, $2, $3, 'served_rls', 'served-rls-root', $4::memphant.ltree, 0)",
    )
    .bind(scope)
    .bind(tenant)
    .bind(subject)
    .bind(scope.to_string().replace('-', "_"))
    .execute(root)
    .await
    .expect("scope write");
    sqlx::query(
        "insert into memphant.actor
           (id, tenant_id, data_subject_id, kind, external_ref, trust_level)
         values ($1, $2, $3, 'agent', 'served-rls-actor', 'trusted_system')",
    )
    .bind(actor)
    .bind(tenant)
    .bind(subject)
    .execute(root)
    .await
    .expect("actor write");
    sqlx::query(
        "insert into memphant.agent_node
           (id, tenant_id, data_subject_id, scope_id, level, external_ref)
         values ($1, $2, $3, $4, 0, 'served-rls-agent')",
    )
    .bind(agent_node)
    .bind(tenant)
    .bind(subject)
    .bind(scope)
    .execute(root)
    .await
    .expect("agent_node write");
    sqlx::query(
        "insert into memphant.episode
           (id, tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
            actor_id, source_kind, source_ref, source_trust, dedup_key,
            first_observed_at, last_observed_at, body)
         values ($1, $2, $3, $4, $5, 0, $6, 'user', 'served-rls:ep', 'trusted_system', $7,
                 now(), now(), 'tenant-private served episode body')",
    )
    .bind(episode)
    .bind(tenant)
    .bind(subject)
    .bind(scope)
    .bind(agent_node)
    .bind(actor)
    .bind(format!("served-rls-dedup-{episode}"))
    .execute(root)
    .await
    .expect("episode write");
    Seed {
        subject,
        scope,
        actor,
        agent_node,
        episode,
    }
}

/// Bind `tenant` on a pool the STORE built and run a bare, unfiltered count of
/// another tenant's episodes. No `set local role` here on purpose: whatever
/// role the pool assumed at connect time is what answers.
async fn cross_tenant_visible(pool: &sqlx::PgPool, reader: Uuid, other: Uuid) -> i64 {
    let mut tx = pool.begin().await.expect("begin");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(reader)
        .execute(&mut *tx)
        .await
        .expect("bind tenant");
    let count: i64 = sqlx::query("select count(*) from memphant.episode where tenant_id = $1")
        .bind(other)
        .fetch_one(&mut *tx)
        .await
        .expect("count cross-tenant")
        .get(0);
    tx.rollback().await.expect("rollback");
    count
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn the_served_app_pool_assumes_memphant_app_and_rls_blocks_cross_tenant_reads() {
    let root = PgPoolOptions::new()
        .max_connections(1)
        .connect(&db_url())
        .await
        .expect("connect root");
    let suffix = Uuid::new_v4().simple().to_string();
    let app_login = mint_login(&root, "memphant_app", &suffix).await;
    let authn_login = mint_login(&root, "memphant_authn", &suffix).await;

    let provisioner = PgStore::connect_provisioner(&db_url())
        .await
        .expect("connect provisioner");
    let tenant_a = provisioner
        .create_tenant(&format!("served-rls-a-{suffix}"))
        .await
        .expect("provision tenant A");
    let tenant_b = provisioner
        .create_tenant(&format!("served-rls-b-{suffix}"))
        .await
        .expect("provision tenant B");
    seed_episode(&root, tenant_a).await;
    seed_episode(&root, tenant_b).await;

    // The served constructor. If `connect_pool` stops issuing `SET ROLE`, this
    // credential (NOINHERIT, member-only, no direct schema grants) can read
    // nothing at all and every assertion below fails.
    let store = PgStore::connect_app(
        &login_url(&db_url(), &app_login),
        &login_url(&db_url(), &authn_login),
    )
    .await
    .expect("connect served app store");

    let identity = sqlx::query(
        "select current_user::text as effective, session_user::text as login,
                (select rolsuper from pg_catalog.pg_roles where rolname = current_user) as super,
                (select rolbypassrls from pg_catalog.pg_roles where rolname = current_user) as bypass,
                row_security_active('memphant.episode') as rls_active",
    )
    .fetch_one(store.pool())
    .await
    .expect("read served identity");
    assert_eq!(
        identity.get::<String, _>("effective"),
        "memphant_app",
        "the served pool must run as memphant_app, not as its login role"
    );
    assert_eq!(identity.get::<String, _>("login"), app_login);
    assert!(
        !identity.get::<bool, _>("super"),
        "served role is superuser"
    );
    assert!(
        !identity.get::<bool, _>("bypass"),
        "served role has rolbypassrls"
    );
    assert!(
        identity.get::<bool, _>("rls_active"),
        "row security must be ACTIVE on the served connection — this is the whole point"
    );

    // The data-exposure assertion, through the store's own pool.
    assert_eq!(
        cross_tenant_visible(store.pool(), tenant_a, tenant_b).await,
        0,
        "served path leaked tenant B's episodes to tenant A"
    );
    assert_eq!(
        cross_tenant_visible(store.pool(), tenant_b, tenant_a).await,
        0,
        "served path leaked tenant A's episodes to tenant B"
    );
    // ...and the reader can still see its own row, so the zero above is
    // isolation, not a blanket denial.
    assert_eq!(
        cross_tenant_visible(store.pool(), tenant_a, tenant_a).await,
        1,
        "tenant A must still see its own episode"
    );

    // Same query, same database, a credential that does NOT assume the
    // capability role: it leaks. This is what the served path did before W3.3,
    // and it is why the assertions above are not vacuous.
    let unrestricted = PgStore::connect_with_capabilities(&db_url(), &db_url(), &db_url())
        .await
        .expect("connect unrestricted store");
    assert_eq!(
        cross_tenant_visible(unrestricted.pool(), tenant_a, tenant_b).await,
        1,
        "control: a bypassing credential is expected to see across tenants — if this ever \
         returns 0 the test above proves nothing and must be re-examined"
    );

    // Roles are CLUSTER-global, so dropping the scratch database does not
    // reclaim them. Close every pool first: `drop role` fails while any session
    // is still connected.
    store.close().await;
    unrestricted.close().await;
    for login in [&app_login, &authn_login] {
        let drop_role = format!("drop role if exists {login}");
        sqlx::query(AssertSqlSafe(drop_role.as_str()))
            .execute(&root)
            .await
            .unwrap_or_else(|error| panic!("drop {login}: {error}"));
    }
    root.close().await;
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn the_shipped_login_roles_are_members_and_carry_no_bypass() {
    let root = PgPoolOptions::new()
        .max_connections(1)
        .connect(&db_url())
        .await
        .expect("connect root");
    for (login, capability) in [
        ("memphant_app_login", "memphant_app"),
        ("memphant_authn_login", "memphant_authn"),
        ("memphant_worker_login", "memphant_worker"),
        ("memphant_provisioner_login", "memphant_provisioner"),
    ] {
        let row = sqlx::query(
            "select role.rolcanlogin, role.rolsuper, role.rolbypassrls, role.rolinherit,
                    pg_catalog.pg_has_role($1, $2, 'MEMBER') as is_member
             from pg_catalog.pg_roles role where role.rolname = $1",
        )
        .bind(login)
        .bind(capability)
        .fetch_optional(&root)
        .await
        .expect("query login role")
        .unwrap_or_else(|| panic!("migration must create the {login} login role"));
        assert!(row.get::<bool, _>("rolcanlogin"), "{login} must be LOGIN");
        assert!(
            row.get::<bool, _>("is_member"),
            "{login} must be a member of {capability}"
        );
        assert!(
            !row.get::<bool, _>("rolsuper"),
            "{login} must not be SUPERUSER"
        );
        assert!(
            !row.get::<bool, _>("rolbypassrls"),
            "{login} must not have BYPASSRLS"
        );
        assert!(
            !row.get::<bool, _>("rolinherit"),
            "{login} must be NOINHERIT so the served pool has to SET ROLE explicitly"
        );
    }
    root.close().await;
}

/// The worker pool assumes `memphant_worker`, so FORCE RLS applies to it —
/// including to the QUEUE-WIDE pending count the drain-exit check reads. That
/// count has no tenant bound, so under the tenant-isolation policy a bare
/// `select count(*) from memphant.job_state` answers 0 no matter how deep the
/// queue is, and `MEMPHANT_WORKER_DRAIN=1` stops after one tick and calls the
/// partial number a completed drain (measured: 401 queued jobs -> `drain
/// completed=256`, 145 left `queued`). Seed a job the worker cannot see under
/// the policy and require the store to count it anyway.
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn the_worker_pool_counts_queued_jobs_across_every_tenant() {
    let root = PgPoolOptions::new()
        .max_connections(1)
        .connect(&db_url())
        .await
        .expect("connect root");
    let suffix = Uuid::new_v4().simple().to_string();
    let worker_login = mint_login(&root, "memphant_worker", &suffix).await;

    let provisioner = PgStore::connect_provisioner(&db_url())
        .await
        .expect("connect provisioner");
    let tenant = provisioner
        .create_tenant(&format!("served-rls-worker-{suffix}"))
        .await
        .expect("provision tenant");
    let seed = seed_episode(&root, tenant).await;
    sqlx::query(
        "insert into memphant.job_state
           (id, tenant_id, data_subject_id, actor_id, agent_node_id, subject_generation,
            job_type, target_id, compiler_version, state, scope_id)
         values ($1, $2, $3, $4, $5, 0, 'reflect_episode', $6, 'test', 'queued', $7)",
    )
    .bind(Uuid::now_v7())
    .bind(tenant)
    .bind(seed.subject)
    .bind(seed.actor)
    .bind(seed.agent_node)
    .bind(seed.episode)
    .bind(seed.scope)
    .execute(&root)
    .await
    .expect("job_state write");

    let worker = PgStore::connect_worker(&login_url(&db_url(), &worker_login))
        .await
        .expect("connect served worker store");
    let pending = worker
        .pending_worker_job_count()
        .await
        .expect("pending worker job count");
    assert!(
        pending >= 1,
        "the worker pool must see queued jobs it does not have a tenant bound for; \
         a 0 here means the drain-exit check is a rubber stamp"
    );
    root.close().await;
}
