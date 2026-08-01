//! Live capability-role and transaction-local tenant-binding contract.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, JobFilter, MemoryStore, NoopEmbedding};
use memphant_store_postgres::PgStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    RetainEpisodeHttpRequest, TenantId, TrustLevel,
};
use sqlx::postgres::PgPoolOptions;
use sqlx::{AssertSqlSafe, Executor, Row};
use uuid::Uuid;

const CLOCK: FixedClock = FixedClock("2026-07-13T00:00:00Z");

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
    format!("{scheme}{role}:role_matrix_password@{host}")
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn capability_roles_are_isolated_and_tenant_binding_is_transaction_local() {
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&db_url())
        .await
        .expect("connect");

    let mut provision = pool.begin().await.expect("begin provision");
    provision
        .execute("set local role memphant_provisioner")
        .await
        .expect("assume provisioner");
    let tenant_a: Uuid =
        sqlx::query_scalar("select memphant.provision_tenant('role-matrix-a', 'test', 'local')")
            .fetch_one(&mut *provision)
            .await
            .expect("provision tenant A");
    let tenant_b: Uuid =
        sqlx::query_scalar("select memphant.provision_tenant('role-matrix-b', 'test', 'local')")
            .fetch_one(&mut *provision)
            .await
            .expect("provision tenant B");
    sqlx::query("select memphant.provision_api_key($1, 'hash-a', 'test', 'trusted_user')")
        .bind(tenant_a)
        .execute(&mut *provision)
        .await
        .expect("provision key");
    provision.commit().await.expect("commit provision");

    let mut provision_denied = pool.begin().await.expect("begin provision denial");
    provision_denied
        .execute("set local role memphant_provisioner")
        .await
        .expect("assume provisioner");
    assert!(
        sqlx::query("select count(*) from memphant.tenant")
            .fetch_one(&mut *provision_denied)
            .await
            .is_err(),
        "provisioner has functions, not table access"
    );
    provision_denied.rollback().await.expect("rollback denial");

    let mut authn = pool.begin().await.expect("begin authn");
    authn
        .execute("set local role memphant_authn")
        .await
        .expect("assume authn");
    let authenticated: Uuid =
        sqlx::query("select tenant_id from memphant.authenticate_api_key($1)")
            .bind("hash-a")
            .fetch_one(&mut *authn)
            .await
            .expect("authenticate through function")
            .get("tenant_id");
    assert_eq!(authenticated, tenant_a);
    assert!(
        sqlx::query("select count(*) from memphant.api_key")
            .fetch_one(&mut *authn)
            .await
            .is_err(),
        "authn has no direct key-table access"
    );
    authn.rollback().await.expect("rollback authn");

    let actor = Uuid::now_v7();
    let subject = Uuid::now_v7();
    let scope = Uuid::now_v7();
    let agent_node = Uuid::now_v7();
    let job = Uuid::now_v7();
    let mut app = pool.begin().await.expect("begin app");
    app.execute("set local role memphant_app")
        .await
        .expect("assume app");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant_a)
        .execute(&mut *app)
        .await
        .expect("bind tenant A");
    sqlx::query(
        "insert into memphant.subject (id, tenant_id, external_ref, kind) \
         values ($1, $2, 'role-matrix-subject', 'user')",
    )
    .bind(subject)
    .bind(tenant_a)
    .execute(&mut *app)
    .await
    .expect("tenant-bound subject write");
    sqlx::query(
        "insert into memphant.scope
           (id, tenant_id, data_subject_id, kind, external_ref, materialized_path, scope_depth)
         values ($1, $2, $3, 'role_matrix', 'role-matrix-root', $4::memphant.ltree, 0)",
    )
    .bind(scope)
    .bind(tenant_a)
    .bind(subject)
    .bind(scope.to_string().replace('-', "_"))
    .execute(&mut *app)
    .await
    .expect("tenant-bound scope write");
    sqlx::query(
        "insert into memphant.actor
           (id, tenant_id, data_subject_id, kind, external_ref, trust_level)
         values ($1, $2, $3, 'agent', 'role-matrix', 'trusted_system')",
    )
    .bind(actor)
    .bind(tenant_a)
    .bind(subject)
    .execute(&mut *app)
    .await
    .expect("tenant-bound app write");
    sqlx::query(
        "insert into memphant.agent_node
           (id, tenant_id, data_subject_id, scope_id, level, external_ref)
         values ($1, $2, $3, $4, 0, 'role-matrix-agent')",
    )
    .bind(agent_node)
    .bind(tenant_a)
    .bind(subject)
    .bind(scope)
    .execute(&mut *app)
    .await
    .expect("tenant-bound agent-node write");
    sqlx::query(
        "insert into memphant.job_state \
         (id, tenant_id, data_subject_id, actor_id, agent_node_id, subject_generation, \
          job_type, target_id, compiler_version, state, scope_id) \
         values ($1, $2, $3, $4, $5, 0, 'reflect_episode', $6, 'role-matrix', 'queued', $7)",
    )
    .bind(job)
    .bind(tenant_a)
    .bind(subject)
    .bind(actor)
    .bind(agent_node)
    .bind(Uuid::now_v7())
    .bind(scope)
    .execute(&mut *app)
    .await
    .expect("tenant-bound job write");
    assert_eq!(
        sqlx::query_scalar::<_, i64>("select count(*) from memphant.actor")
            .fetch_one(&mut *app)
            .await
            .expect("tenant A read"),
        1
    );
    app.commit().await.expect("commit app");

    let mut unbound = pool.begin().await.expect("begin unbound");
    unbound
        .execute("set local role memphant_app")
        .await
        .expect("assume app");
    assert_eq!(
        sqlx::query_scalar::<_, i64>("select count(*) from memphant.actor")
            .fetch_one(&mut *unbound)
            .await
            .expect("unbound read"),
        0,
        "tenant binding must not survive transaction commit"
    );
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant_b)
        .execute(&mut *unbound)
        .await
        .expect("bind tenant B");
    assert_eq!(
        sqlx::query_scalar::<_, i64>("select count(*) from memphant.actor")
            .fetch_one(&mut *unbound)
            .await
            .expect("tenant B read"),
        0,
        "tenant B cannot see tenant A"
    );
    unbound.rollback().await.expect("rollback unbound");

    let mut worker = pool.begin().await.expect("begin worker");
    worker
        .execute("set local role memphant_worker")
        .await
        .expect("assume worker");
    assert_eq!(
        sqlx::query_scalar::<_, i64>("select count(*) from memphant.job_state")
            .fetch_one(&mut *worker)
            .await
            .expect("unbound worker read"),
        0,
        "worker cannot scan tenant jobs directly"
    );
    let claimed_tenant: Uuid =
        sqlx::query("select tenant_id from memphant.claim_reflect_jobs(1, $1, null, 5)")
            .bind(tenant_a)
            .fetch_one(&mut *worker)
            .await
            .expect("claim through narrow worker function")
            .get("tenant_id");
    assert_eq!(claimed_tenant, tenant_a);
    worker.rollback().await.expect("rollback worker");

    let mut worker_policy_denied = pool.begin().await.expect("begin worker policy denial");
    worker_policy_denied
        .execute("set local role memphant_worker")
        .await
        .expect("assume worker");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant_a)
        .execute(&mut *worker_policy_denied)
        .await
        .expect("bind worker tenant");
    assert!(
        sqlx::query("delete from memphant.scope_policy")
            .execute(&mut *worker_policy_denied)
            .await
            .is_err(),
        "worker cannot mutate access policy"
    );
    worker_policy_denied
        .rollback()
        .await
        .expect("rollback worker policy denial");

    let mut readonly = pool.begin().await.expect("begin readonly");
    readonly
        .execute("set local role memphant_readonly")
        .await
        .expect("assume readonly");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant_a)
        .execute(&mut *readonly)
        .await
        .expect("bind readonly");
    assert_eq!(
        sqlx::query_scalar::<_, i64>("select count(*) from memphant.actor")
            .fetch_one(&mut *readonly)
            .await
            .expect("readonly select"),
        1
    );
    assert!(
        sqlx::query("delete from memphant.actor")
            .execute(&mut *readonly)
            .await
            .is_err(),
        "readonly cannot write"
    );
    readonly.rollback().await.expect("rollback readonly");

    sqlx::query("delete from memphant.job_state where tenant_id = $1 and id = $2")
        .bind(tenant_a)
        .bind(job)
        .execute(&pool)
        .await
        .expect("clean raw role-matrix job");

    pool.close().await;
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn pg_store_runs_with_separate_least_privilege_credentials() {
    let root = PgPoolOptions::new()
        .max_connections(1)
        .connect(&db_url())
        .await
        .expect("connect root");
    let suffix = Uuid::new_v4().simple().to_string();
    let app_login = format!("mp_app_{suffix}");
    let auth_login = format!("mp_auth_{suffix}");
    let provision_login = format!("mp_provision_{suffix}");
    let worker_login = format!("mp_worker_{suffix}");
    for (login, capability) in [
        (&app_login, "memphant_app"),
        (&auth_login, "memphant_authn"),
        (&provision_login, "memphant_provisioner"),
        (&worker_login, "memphant_worker"),
    ] {
        let create = format!("create role {login} login password 'role_matrix_password'");
        sqlx::query(AssertSqlSafe(create.as_str()))
            .execute(&root)
            .await
            .expect("create login");
        let grant = format!("grant {capability} to {login}");
        sqlx::query(AssertSqlSafe(grant.as_str()))
            .execute(&root)
            .await
            .expect("grant capability");
    }

    let provisioner = PgStore::connect_provisioner(&login_url(&db_url(), &provision_login))
        .await
        .expect("connect provisioner store");
    let store = PgStore::connect_app(
        &login_url(&db_url(), &app_login),
        &login_url(&db_url(), &auth_login),
    )
    .await
    .expect("connect split-capability store");
    assert!(store.create_tenant("forbidden").await.is_err());
    let tenant_uuid = provisioner
        .create_tenant(&format!("split-role-{suffix}"))
        .await
        .expect("provision tenant");
    let tenant = TenantId::from_u128(tenant_uuid.as_u128());
    provisioner
        .create_api_key(
            tenant_uuid,
            "split-role-hash",
            "test",
            TrustLevel::TrustedUser,
            None,
        )
        .await
        .expect("provision key");
    assert_eq!(
        store
            .lookup_api_key("split-role-hash")
            .await
            .expect("authenticate key")
            .expect("key exists")
            .tenant_id,
        tenant
    );

    let binding = store
        .resolve_context_binding(
            tenant,
            "role-matrix-runtime".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "role-matrix-user".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "role-matrix-user".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "role-matrix-scope".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "role-matrix-agent".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("resolve runtime memory context");
    let context = store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("load runtime memory context");
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let retained = service
        .retain(
            &context,
            "role-matrix-retain",
            TrustLevel::TrustedUser,
            RetainEpisodeHttpRequest {
                subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                actor_id: binding.actor_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                source_ref: "role-matrix:episode".to_string(),
                observed_at: CLOCK.0.to_string(),
                payload: memphant_types::RetainPayload::Episode(
                    memphant_types::RetainEpisodePayload {
                        source_kind: "user".to_string(),
                        body: "least privilege survives the real store".to_string(),
                        subject: None,
                        predicate: None,
                    },
                ),
            },
        )
        .await
        .expect("tenant-bound app retain");
    let retained_result: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).unwrap();
    let episode_id = retained_result
        .episode_id
        .expect("episode retain returns id");
    assert!(
        store
            .fetch_episode(&context, episode_id)
            .await
            .expect("tenant-bound app read")
            .is_some()
    );

    let worker = PgStore::connect_worker(&login_url(&db_url(), &worker_login))
        .await
        .expect("connect worker store");
    let claimed = worker
        .claim_reflect_jobs(
            JobFilter {
                tenant: Some(tenant),
                scope: None,
            },
            1,
        )
        .await
        .expect("claim through worker function");
    assert_eq!(claimed.len(), 1);
    assert_eq!(claimed[0].job.tenant_id, tenant);
    assert!(
        worker
            .fetch_episode(&context, episode_id)
            .await
            .expect("tenant-bound worker read")
            .is_some()
    );
    worker
        .complete_reflect_job(&claimed[0])
        .await
        .expect("tenant-bound worker completion");
    assert!(
        worker
            .claim_reflect_jobs(
                JobFilter {
                    tenant: Some(tenant),
                    scope: None,
                },
                1,
            )
            .await
            .expect("completed job is not reclaimed")
            .is_empty()
    );
    let persisted_state: String =
        sqlx::query_scalar("select state from memphant.job_state where tenant_id = $1 and id = $2")
            .bind(tenant.as_uuid())
            .bind(claimed[0].job.id.as_uuid())
            .fetch_one(&root)
            .await
            .expect("read completed job as test owner");
    assert_eq!(persisted_state, "done");

    drop(service);
    drop(store);
    drop(worker);
    drop(provisioner);
    for login in [&app_login, &auth_login, &provision_login, &worker_login] {
        let drop_role = format!("drop role {login}");
        sqlx::query(AssertSqlSafe(drop_role.as_str()))
            .execute(&root)
            .await
            .expect("drop login");
    }
    root.close().await;
}
