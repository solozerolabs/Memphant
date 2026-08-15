//! Durable `MemoryStore` implementation over Postgres (sqlx 0.9, runtime
//! queries) against the real 001+002 schema: composite `(tenant_id, id)`
//! primary keys, the reused `job_state` queue, `body_tsv` FTS,
//! `forgotten_source` tombstones and `api_key.max_trust` ceilings.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::str::FromStr;

use memphant_core::{
    ApiKeyRow, ClaimMutationOutcome, CompiledWrite, CorrectOutcome, CorrectionWrite,
    EmbeddingProfileRow, EmbeddingRow, FileSyncTransitionSnapshot, ForgetOutcome, ForgetWrite,
    JOB_DEAD_LETTER_ATTEMPTS, JobFilter, MemoryStore, MutationClaim, MutationClaimOutcome,
    MutationLedgerStore, MutationResponse, ReflectJobRow, ResolvedMemoryContext, ReviewEventRow,
    ScopePage, StoreError, SubjectErasureReceipt, TaskMemoryEventRow, TaskOutcomeRow,
    correction_rectangles_with_ids, deep_unit_is_snapshot_eligible,
};
use memphant_types::{
    ActorId, AgentNodeId, CitationSpan, ContextBindingAccessPolicy, ContextBindingPolicyMode,
    ContextBindingRequest, ContextBindingResponse, ContextualChunk, CorrectResult,
    DeepSnapshotEntry, DeepSnapshotSourceKind, EdgeId, EpisodeId, ForgetTarget, JobId, MemoryKind,
    NewEpisode, NewMemoryEdge, NewMemoryUnit, NewResource, QueuedReflectJob, RecallTime,
    RecordMaterial, ReflectJob, ReflectJobKind, ReflectTrace, ResolvedMemorySource, ResourceAcl,
    ResourceId, RetainOutcome, RetrievalTrace, SCHEMA_COMPAT_REVISION, ScopeId, StoredCitation,
    StoredEpisode, StoredMemoryEdge, StoredMemoryUnit, StoredResource, SubjectId,
    TaskMemoryAttribution, TaskMemoryEventKind, TenantId, TraceId, TrustLevel, UnitId,
    agent_level_allows_memory_kind,
};
use serde::Serialize;
use serde::de::DeserializeOwned;
use sha2::{Digest, Sha256};
use sqlx::postgres::{PgConnectOptions, PgPoolOptions, PgRow};
use sqlx::{AssertSqlSafe, PgPool, Postgres, Row, Transaction};
use uuid::Uuid;

use crate::MIGRATION_HEAD;

/// RFC 3339 UTC projection used for every timestamptz read; writes bind RFC
/// 3339 strings and cast `::timestamptz`.
const TS_FMT: &str = r#"'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'"#;

fn task_event_name(value: TaskMemoryEventKind) -> &'static str {
    match value {
        TaskMemoryEventKind::Shown => "shown",
        TaskMemoryEventKind::Activated => "activated",
        TaskMemoryEventKind::Helpful => "helpful",
        TaskMemoryEventKind::Harmful => "harmful",
        TaskMemoryEventKind::Silenced => "silenced",
    }
}

fn task_attribution_name(value: TaskMemoryAttribution) -> &'static str {
    match value {
        TaskMemoryAttribution::ExplicitUser => "explicit_user",
        TaskMemoryAttribution::DeterministicScorer => "deterministic_scorer",
        TaskMemoryAttribution::RandomizedCounterfactual => "randomized_counterfactual",
        TaskMemoryAttribution::Observational => "observational",
    }
}
const TRANSACTION_POOLER_ERROR: &str = "persistent Postgres connections cannot use transaction pooler port 6543; use direct or session port 5432";
pub const DEFAULT_DATABASE_MAX_CONNECTIONS: u32 = 8;
pub const MAX_WORKER_DATABASE_MAX_CONNECTIONS: u32 = 64;

/// Capability roles a served pool assumes on every physical connection. They
/// are NOLOGIN + NOINHERIT, so the credential in the URL must be a *member*
/// and the connection must issue an explicit `SET ROLE`.
pub const APP_ROLE: &str = "memphant_app";
pub const AUTHN_ROLE: &str = "memphant_authn";
pub const WORKER_ROLE: &str = "memphant_worker";
pub const PROVISIONER_ROLE: &str = "memphant_provisioner";

/// Marker the fail-closed RLS refusal carries, so wrapping never buries it.
const RLS_REFUSAL: &str = "refusing to serve";

/// A credential plus the capability role its connections assume.
#[derive(Clone, Copy)]
struct PoolSpec<'a> {
    url: &'a str,
    role: Option<&'static str>,
}

impl<'a> PoolSpec<'a> {
    fn capability(url: &'a str, role: &'static str) -> Self {
        Self {
            url,
            role: Some(role),
        }
    }

    fn unrestricted(url: &'a str) -> Self {
        Self { url, role: None }
    }

    /// Wrap a connect failure with the role the credential failed to assume —
    /// the common cause is a login role that was never granted membership.
    fn context(&self, error: StoreError) -> StoreError {
        if error.to_string().contains(RLS_REFUSAL) {
            return error;
        }
        match self.role {
            Some(role) => StoreError::Backend(format!("could not open a `{role}` pool: {error}.")),
            None => error,
        }
    }
}

fn ts(column: &str) -> String {
    format!("to_char({column} at time zone 'utc', {TS_FMT})")
}

fn canonical_timestamp(value: Option<String>) -> Option<String> {
    value.map(|mut value| {
        if value.ends_with('Z')
            && let Some(dot) = value.rfind('.')
        {
            let fraction_end = value.len() - 1;
            let trimmed_end = value[dot + 1..fraction_end].trim_end_matches('0').len() + dot + 1;
            if trimmed_end == dot + 1 {
                value.truncate(dot);
            } else {
                value.truncate(trimmed_end);
            }
            value.push('Z');
        }
        value
    })
}

fn backend(error: sqlx::Error) -> StoreError {
    if error
        .as_database_error()
        .and_then(|database| database.code())
        .as_deref()
        == Some("40001")
    {
        StoreError::SerializationConflict
    } else {
        StoreError::Backend(error.to_string())
    }
}

fn enum_str<T: Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .expect("unit enums serialize to strings")
}

fn enum_from_str<T: DeserializeOwned>(value: &str) -> Result<T, StoreError> {
    serde_json::from_value(serde_json::Value::String(value.to_string()))
        .map_err(|error| StoreError::Backend(format!("bad enum value {value}: {error}")))
}

fn parse_vec_literal(text: &str) -> Result<Vec<f32>, StoreError> {
    let inner = text.trim().trim_start_matches('[').trim_end_matches(']');
    if inner.trim().is_empty() {
        return Ok(Vec::new());
    }
    inner
        .split(',')
        .map(|value| {
            value
                .trim()
                .parse::<f32>()
                .map_err(|error| StoreError::Backend(format!("bad halfvec literal: {error}")))
        })
        .collect()
}

fn vec_literal(vec: &[f32]) -> String {
    let mut out = String::from("[");
    for (index, value) in vec.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        out.push_str(&value.to_string());
    }
    out.push(']');
    out
}

fn source_kind_triples(
    context: &ResolvedMemoryContext,
    kinds: &[MemoryKind],
) -> (Vec<Uuid>, Vec<Uuid>, Vec<String>) {
    let mut scopes = Vec::new();
    let mut agents = Vec::new();
    let mut kind_names = Vec::new();
    for (kind, sources) in &context.sources_by_kind {
        if !kinds.is_empty() && !kinds.contains(kind) {
            continue;
        }
        for source in sources {
            scopes.push(source.scope_id.as_uuid());
            agents.push(source.agent_node_id.as_uuid());
            kind_names.push(enum_str(kind));
        }
    }
    (scopes, agents, kind_names)
}

/// Owned bind values for dynamically assembled queries.
enum Bind {
    Uuid(Uuid),
    UuidOpt(Option<Uuid>),
    UuidVec(Vec<Uuid>),
    Text(String),
    TextVec(Vec<String>),
    I64(i64),
}

#[derive(Clone)]
pub struct PgStore {
    pool: PgPool,
    auth_pool: Option<PgPool>,
    provision_pool: Option<PgPool>,
}

pub struct PgTxn {
    tx: Transaction<'static, Postgres>,
    context: ResolvedMemoryContext,
    mutation: Option<PgMutationState>,
    has_subject_writes: bool,
}

enum PgMutationState {
    Execute {
        claim: MutationClaim,
        response_staged: bool,
    },
    Replay {
        claim: MutationClaim,
        response: MutationResponse,
    },
}

impl PgStore {
    /// Connects and pings; refuses to construct against an unreachable
    /// database.
    pub async fn connect(database_url: &str) -> Result<Self, StoreError> {
        Self::connect_with_capabilities(database_url, database_url, database_url).await
    }

    pub async fn connect_app(
        database_url: &str,
        auth_database_url: &str,
    ) -> Result<Self, StoreError> {
        Self::connect_pools(
            PoolSpec::capability(database_url, APP_ROLE),
            Some(PoolSpec::capability(auth_database_url, AUTHN_ROLE)),
            None,
        )
        .await
    }

    pub async fn connect_worker(database_url: &str) -> Result<Self, StoreError> {
        Self::connect_pools(PoolSpec::capability(database_url, WORKER_ROLE), None, None).await
    }

    pub async fn connect_worker_with_max_connections(
        database_url: &str,
        max_connections: u32,
    ) -> Result<Self, StoreError> {
        if !(1..=MAX_WORKER_DATABASE_MAX_CONNECTIONS).contains(&max_connections) {
            return Err(StoreError::Backend(format!(
                "worker database max connections must be from 1 through {MAX_WORKER_DATABASE_MAX_CONNECTIONS}"
            )));
        }
        Self::connect_pools_with_database_capacity(
            PoolSpec::capability(database_url, WORKER_ROLE),
            None,
            None,
            max_connections,
        )
        .await
    }

    /// Operator/admin credential (`memphant admin …`, test provisioning). NOT
    /// a served path, and deliberately does NOT assume `memphant_provisioner`:
    /// `memphant admin create-key --subject-id …` calls
    /// `resolve_memory_context`, which reads `context_binding`/`subject`, and
    /// the provisioner capability is granted execute on three SECURITY DEFINER
    /// functions and no table access at all (`permission denied for table
    /// context_binding`, asserted deliberately in `role_matrix.rs`). Making the
    /// admin CLI least-privilege needs either a second app-capability
    /// connection for that read or a narrower provisioning function — a real
    /// follow-on, not something to buy by widening the grant.
    pub async fn connect_provisioner(database_url: &str) -> Result<Self, StoreError> {
        Self::connect_pools(
            PoolSpec::unrestricted(database_url),
            None,
            Some(PoolSpec::unrestricted(database_url)),
        )
        .await
    }

    /// Single-credential convenience used by contract tests, benches and the
    /// eval harnesses: one URL plays all three capabilities at once, so no
    /// capability role can be assumed (they are disjoint by construction) and
    /// the served-path RLS assertion does not apply. Never a serving path —
    /// the binaries go through `connect_app`/`connect_worker`.
    pub async fn connect_with_capabilities(
        database_url: &str,
        auth_database_url: &str,
        provision_database_url: &str,
    ) -> Result<Self, StoreError> {
        Self::connect_pools(
            PoolSpec::unrestricted(database_url),
            Some(PoolSpec::unrestricted(auth_database_url)),
            Some(PoolSpec::unrestricted(provision_database_url)),
        )
        .await
    }

    async fn connect_pools(
        database: PoolSpec<'_>,
        auth: Option<PoolSpec<'_>>,
        provision: Option<PoolSpec<'_>>,
    ) -> Result<Self, StoreError> {
        Self::connect_pools_with_database_capacity(
            database,
            auth,
            provision,
            DEFAULT_DATABASE_MAX_CONNECTIONS,
        )
        .await
    }

    async fn connect_pools_with_database_capacity(
        database: PoolSpec<'_>,
        auth: Option<PoolSpec<'_>>,
        provision: Option<PoolSpec<'_>>,
        database_max_connections: u32,
    ) -> Result<Self, StoreError> {
        // Every URL is parsed and shape-checked BEFORE any network work, so a
        // bad auth/provision URL is reported even when the database URL is
        // unreachable (`persistent_connection.rs` pins this).
        let database_options = Self::persistent_connect_options(database.url)?;
        for spec in [auth, provision].into_iter().flatten() {
            Self::persistent_connect_options(spec.url)?;
        }
        let pool = Self::connect_pool(database_options, database_max_connections, database.role)
            .await
            .map_err(|error| database.context(error))?;
        // A pool is reused only when BOTH the credential and the assumed
        // capability role match; `connect_app` shares one URL across the app
        // and authn capabilities in every shipped profile, and those two roles
        // are deliberately disjoint.
        let auth_pool = Self::sibling_pool(&pool, database, auth).await?;
        let provision_pool = Self::sibling_pool(&pool, database, provision).await?;
        Ok(Self {
            pool,
            auth_pool,
            provision_pool,
        })
    }

    async fn sibling_pool(
        primary: &PgPool,
        primary_spec: PoolSpec<'_>,
        spec: Option<PoolSpec<'_>>,
    ) -> Result<Option<PgPool>, StoreError> {
        let Some(spec) = spec else { return Ok(None) };
        if spec.url == primary_spec.url && spec.role == primary_spec.role {
            return Ok(Some(primary.clone()));
        }
        let options = Self::persistent_connect_options(spec.url)?;
        Ok(Some(
            Self::connect_pool(options, DEFAULT_DATABASE_MAX_CONNECTIONS, spec.role)
                .await
                .map_err(|error| spec.context(error))?,
        ))
    }

    fn persistent_connect_options(database_url: &str) -> Result<PgConnectOptions, StoreError> {
        let options = PgConnectOptions::from_str(database_url).map_err(backend)?;
        if options.get_port() == 6543 {
            return Err(StoreError::Backend(TRANSACTION_POOLER_ERROR.to_string()));
        }
        Ok(options)
    }

    async fn connect_pool(
        options: PgConnectOptions,
        max_connections: u32,
        role: Option<&'static str>,
    ) -> Result<PgPool, StoreError> {
        // A `SET ROLE` that fails inside `after_connect` is retried by the pool
        // and surfaces 30 s later as an opaque acquire timeout. Probe it once on
        // a throwaway connection first so a credential that was never granted
        // membership fails immediately, saying so.
        if let Some(role) = role {
            Self::probe_role_membership(&options, role).await?;
        }
        let pool = PgPoolOptions::new()
            .max_connections(max_connections)
            .after_connect(move |connection, _| {
                Box::pin(async move {
                    sqlx::query(
                        "select pg_catalog.set_config(
                           'search_path',
                           (select pg_catalog.string_agg(pg_catalog.format('%I', schema_name), ',')
                            from (
                              select 'memphant'::text as schema_name
                              union
                              select namespace.nspname
                              from pg_catalog.pg_extension extension
                              join pg_catalog.pg_namespace namespace
                                on namespace.oid = extension.extnamespace
                              where extension.extname in ('vector','pg_trgm','ltree','btree_gist')
                            ) schemas) || ',pg_catalog',
                           false)",
                    )
                    .execute(&mut *connection)
                    .await?;
                    // The capability roles are NOINHERIT, so membership alone
                    // grants nothing: the served connection must ASSUME the
                    // role. Assuming it also drops superuser/BYPASSRLS, which
                    // is what makes FORCE RLS bite on the served path.
                    if let Some(role) = role {
                        sqlx::query("select pg_catalog.set_config('role', $1, false)")
                            .bind(role)
                            .execute(&mut *connection)
                            .await?;
                    }
                    Ok(())
                })
            })
            .connect_with(options)
            .await
            .map_err(backend)?;
        sqlx::query("select 1")
            .execute(&pool)
            .await
            .map_err(backend)?;
        if let Some(role) = role {
            Self::assert_row_security_enforced(&pool, role).await?;
        }
        Ok(pool)
    }

    async fn probe_role_membership(
        options: &PgConnectOptions,
        role: &str,
    ) -> Result<(), StoreError> {
        use sqlx::Connection;
        let mut probe = sqlx::PgConnection::connect_with(options)
            .await
            .map_err(backend)?;
        let assumed = sqlx::query("select pg_catalog.set_config('role', $1, false)")
            .bind(role)
            .execute(&mut probe)
            .await;
        let _ = probe.close().await;
        assumed.map_err(|error| {
            StoreError::Backend(format!(
                "refusing to serve: the credential cannot assume the capability role `{role}`: \
                 {error}. Grant it (`grant {role} to <login>`), or use the `{role}_login` role \
                 that migration 20260730_004_served_login_roles ships."
            ))
        })?;
        Ok(())
    }

    /// Fail-closed startup guard: a served pool must run as its non-superuser
    /// capability role. A superuser or `BYPASSRLS` effective role silently
    /// disables every `_tenant_isolation` policy, leaving tenant isolation to
    /// application predicates alone — so refuse to serve instead.
    async fn assert_row_security_enforced(pool: &PgPool, role: &str) -> Result<(), StoreError> {
        let row = sqlx::query(
            "select current_user::text as effective_role,
                    coalesce(role.rolsuper, false) as is_superuser,
                    coalesce(role.rolbypassrls, false) as bypasses_rls,
                    session_user::text as login_role
             from (select 1) probe
             left join pg_catalog.pg_roles role on role.rolname = current_user",
        )
        .fetch_one(pool)
        .await
        .map_err(backend)?;
        let effective: String = row.try_get("effective_role").map_err(backend)?;
        let login: String = row.try_get("login_role").map_err(backend)?;
        let is_superuser: bool = row.try_get("is_superuser").map_err(backend)?;
        let bypasses_rls: bool = row.try_get("bypasses_rls").map_err(backend)?;
        if effective != role || is_superuser || bypasses_rls {
            return Err(StoreError::Backend(format!(
                "refusing to serve: row-level security would be bypassed. \
                 The connection must run as the non-superuser capability role `{role}`, but the \
                 effective role is `{effective}` (login `{login}`, rolsuper={is_superuser}, \
                 rolbypassrls={bypasses_rls}). Apply migration \
                 20260730_004_served_login_roles and point the credential at a login role that is \
                 a member of `{role}` and is neither SUPERUSER nor BYPASSRLS \
                 (`grant {role} to <login>`)."
            )));
        }
        Ok(())
    }

    pub fn pool(&self) -> &PgPool {
        &self.pool
    }

    /// Close every pool this store owns. Tests that mint throwaway login roles
    /// need it: `drop role` fails while any session is still connected.
    pub async fn close(&self) {
        self.pool.close().await;
        for pool in [self.auth_pool.as_ref(), self.provision_pool.as_ref()]
            .into_iter()
            .flatten()
        {
            pool.close().await;
        }
    }

    async fn tenant_tx(
        &self,
        tenant: TenantId,
    ) -> Result<Transaction<'static, Postgres>, StoreError> {
        let mut tx = self.pool.begin().await.map_err(backend)?;
        sqlx::query("select memphant.bind_tenant($1)")
            .bind(tenant.as_uuid())
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        Ok(tx)
    }

    /// Physically removes at most `limit` expired idempotency receipts for one
    /// tenant. Callers can repeat this bounded maintenance operation until it
    /// returns zero; `skip locked` keeps it from blocking active mutations.
    pub async fn purge_expired_mutation_receipts(
        &self,
        tenant: TenantId,
        limit: u32,
    ) -> Result<u64, StoreError> {
        if limit == 0 {
            return Ok(0);
        }
        let mut tx = self.tenant_tx(tenant).await?;
        let result = sqlx::query(
            "with expired as (
               select ctid
               from memphant.mutation_ledger
               where tenant_id = $1 and expires_at <= statement_timestamp()
               order by expires_at
               limit $2
               for update skip locked
             )
             delete from memphant.mutation_ledger ledger
             using expired
             where ledger.ctid = expired.ctid and ledger.tenant_id = $1",
        )
        .bind(tenant.as_uuid())
        .bind(i64::from(limit))
        .execute(&mut *tx)
        .await
        .map_err(backend)?;
        tx.commit().await.map_err(backend)?;
        Ok(result.rows_affected())
    }

    // ---- Admin surface (tenants + API keys; used by `memphant admin`). ----

    pub async fn create_tenant(&self, name: &str) -> Result<Uuid, StoreError> {
        let pool = self.provision_pool.as_ref().ok_or_else(|| {
            StoreError::Backend("store has no provisioner capability".to_string())
        })?;
        let id: Uuid = sqlx::query_scalar("select memphant.provision_tenant($1, 'dev', 'local')")
            .bind(name)
            .fetch_one(pool)
            .await
            .map_err(backend)?;
        Ok(id)
    }

    pub async fn create_api_key(
        &self,
        tenant: Uuid,
        key_hash: &str,
        label: &str,
        max_trust: TrustLevel,
        scoped_context: Option<&ResolvedMemoryContext>,
    ) -> Result<Uuid, StoreError> {
        let pool = self.provision_pool.as_ref().ok_or_else(|| {
            StoreError::Backend("store has no provisioner capability".to_string())
        })?;
        if scoped_context.is_some_and(|context| context.tenant_id.as_uuid() != tenant) {
            return Err(StoreError::PolicyDenied(
                "API key context does not match tenant".to_string(),
            ));
        }
        let id: Uuid = sqlx::query_scalar(
            "select memphant.provision_api_key($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        )
        .bind(tenant)
        .bind(key_hash)
        .bind(label)
        .bind(enum_str(&max_trust))
        .bind(scoped_context.map(|context| context.data_subject_id.as_uuid()))
        .bind(scoped_context.map(|context| context.subject_generation as i64))
        .bind(scoped_context.map(|context| context.actor_id.as_uuid()))
        .bind(scoped_context.map(|context| context.scope_id.as_uuid()))
        .bind(scoped_context.map(|context| context.agent_node_id.as_uuid()))
        .fetch_one(pool)
        .await
        .map_err(backend)?;
        Ok(id)
    }

    pub async fn revoke_api_key(&self, id: Uuid) -> Result<bool, StoreError> {
        let pool = self.provision_pool.as_ref().ok_or_else(|| {
            StoreError::Backend("store has no provisioner capability".to_string())
        })?;
        let revoked: Option<bool> = sqlx::query_scalar("select memphant.revoke_api_key($1)")
            .bind(id)
            .fetch_optional(pool)
            .await
            .map_err(backend)?;
        Ok(revoked.unwrap_or(false))
    }

    // Context-owned rows are provisioned only by resolve_context_binding.

    async fn validate_episode_context(
        tx: &mut Transaction<'static, Postgres>,
        episode: &NewEpisode,
    ) -> Result<(), StoreError> {
        let valid = sqlx::query_scalar::<_, bool>(
            "select exists (
               select 1 from memphant.context_binding binding
               join memphant.subject subject
                 on subject.tenant_id = binding.tenant_id
                and subject.id = binding.data_subject_id
              where binding.tenant_id = $1 and binding.data_subject_id = $2
                and binding.actor_id = $3 and binding.scope_id = $4
                and binding.agent_node_id = $5 and subject.generation = $6
             )",
        )
        .bind(episode.tenant_id.as_uuid())
        .bind(episode.data_subject_id.as_uuid())
        .bind(episode.actor_id.as_uuid())
        .bind(episode.scope_id.as_uuid())
        .bind(episode.agent_node_id.as_uuid())
        .bind(episode.subject_generation as i64)
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        if valid {
            Ok(())
        } else {
            Err(StoreError::NotFound("memory context"))
        }
    }

    async fn validate_resource_context(
        tx: &mut Transaction<'static, Postgres>,
        resource: &NewResource,
    ) -> Result<(), StoreError> {
        let valid = sqlx::query_scalar::<_, bool>(
            "select exists (
               select 1 from memphant.context_binding binding
               join memphant.subject subject
                 on subject.tenant_id = binding.tenant_id
                and subject.id = binding.data_subject_id
              where binding.tenant_id = $1 and binding.data_subject_id = $2
                and binding.actor_id = $3 and binding.scope_id = $4
                and binding.agent_node_id = $5 and subject.generation = $6
             )",
        )
        .bind(resource.tenant_id.as_uuid())
        .bind(resource.data_subject_id.as_uuid())
        .bind(resource.actor_id.as_uuid())
        .bind(resource.scope_id.as_uuid())
        .bind(resource.agent_node_id.as_uuid())
        .bind(resource.subject_generation as i64)
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        valid
            .then_some(())
            .ok_or(StoreError::NotFound("memory context"))
    }

    fn unit_select(where_clause: &str, tail: &str) -> String {
        format!(
            "select id, data_subject_id, scope_id, agent_node_id, subject_generation,
                    kind, state, fact_key, predicate, body, confidence, trust_level, churn_class,
                    {freshness} as freshness_due_at, actor_id, source_kind, source_ref,
                    {observed_at} as observed_at, source_episode_id,
                    source_resource_id, deletion_generation, payload,
                    {valid_from} as valid_from, {valid_to} as valid_to,
                    {tx_from} as transaction_from, {tx_to} as transaction_to,
                    difficulty, stability_days, {reinforced} as last_reinforced_at,
                    reinforcement_count, tenant_id
             from memphant.memory_unit where {where_clause} {tail}",
            freshness = ts("freshness_due_at"),
            valid_from = ts("valid_from"),
            valid_to = ts("valid_to"),
            tx_from = ts("transaction_from"),
            tx_to = ts("transaction_to"),
            reinforced = ts("last_reinforced_at"),
            observed_at = ts("observed_at"),
        )
    }

    fn unit_from_row(row: &PgRow) -> Result<StoredMemoryUnit, StoreError> {
        let payload: serde_json::Value = row.try_get("payload").map_err(backend)?;
        let contextual_chunks: Vec<ContextualChunk> = payload
            .get("contextual_chunks")
            .cloned()
            .map(serde_json::from_value)
            .transpose()
            .map_err(|error| StoreError::Backend(error.to_string()))?
            .unwrap_or_default();
        let compact: Option<memphant_types::CompactEnvelope> = payload
            .get("compact")
            .cloned()
            .map(serde_json::from_value)
            .transpose()
            .map_err(|error| StoreError::Backend(error.to_string()))?;
        let invalidation: Option<memphant_types::InvalidationMarker> = payload
            .get("invalidation")
            .cloned()
            .map(serde_json::from_value)
            .transpose()
            .map_err(|error| StoreError::Backend(error.to_string()))?;
        Ok(StoredMemoryUnit {
            invalidation,
            id: UnitId::from_u128(row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128()),
            tenant_id: TenantId::from_u128(
                row.try_get::<Uuid, _>("tenant_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            data_subject_id: SubjectId::from_u128(
                row.try_get::<Uuid, _>("data_subject_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            scope_id: ScopeId::from_u128(
                row.try_get::<Uuid, _>("scope_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            agent_node_id: AgentNodeId::from_u128(
                row.try_get::<Uuid, _>("agent_node_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            subject_generation: row
                .try_get::<i64, _>("subject_generation")
                .map_err(backend)? as u64,
            kind: enum_from_str(row.try_get::<String, _>("kind").map_err(backend)?.as_str())?,
            state: enum_from_str(row.try_get::<String, _>("state").map_err(backend)?.as_str())?,
            fact_key: row.try_get("fact_key").map_err(backend)?,
            predicate: row.try_get("predicate").map_err(backend)?,
            body: row.try_get("body").map_err(backend)?,
            confidence: row.try_get("confidence").map_err(backend)?,
            trust_level: enum_from_str(
                row.try_get::<String, _>("trust_level")
                    .map_err(backend)?
                    .as_str(),
            )?,
            churn_class: row.try_get("churn_class").map_err(backend)?,
            freshness_due_at: canonical_timestamp(
                row.try_get("freshness_due_at").map_err(backend)?,
            ),
            actor_id: row
                .try_get::<Option<Uuid>, _>("actor_id")
                .map_err(backend)?
                .map(|id| ActorId::from_u128(id.as_u128())),
            source_kind: row.try_get("source_kind").map_err(backend)?,
            source_ref: row.try_get("source_ref").map_err(backend)?,
            observed_at: canonical_timestamp(row.try_get("observed_at").map_err(backend)?)
                .ok_or_else(|| {
                    StoreError::Backend("memory unit observed_at is null".to_string())
                })?,
            source_episode_id: row
                .try_get::<Option<Uuid>, _>("source_episode_id")
                .map_err(backend)?
                .map(|id| EpisodeId::from_u128(id.as_u128())),
            source_resource_id: row
                .try_get::<Option<Uuid>, _>("source_resource_id")
                .map_err(backend)?
                .map(|id| ResourceId::from_u128(id.as_u128())),
            deletion_generation: row
                .try_get::<Option<i64>, _>("deletion_generation")
                .map_err(backend)?
                .map(|generation| generation as u64),
            contextual_chunks,
            valid_from: canonical_timestamp(row.try_get("valid_from").map_err(backend)?),
            valid_to: canonical_timestamp(row.try_get("valid_to").map_err(backend)?),
            transaction_from: canonical_timestamp(
                row.try_get("transaction_from").map_err(backend)?,
            ),
            transaction_to: canonical_timestamp(row.try_get("transaction_to").map_err(backend)?),
            difficulty: row.try_get("difficulty").map_err(backend)?,
            stability_days: row.try_get("stability_days").map_err(backend)?,
            last_reinforced_at: canonical_timestamp(
                row.try_get("last_reinforced_at").map_err(backend)?,
            ),
            reinforcement_count: row
                .try_get::<i32, _>("reinforcement_count")
                .map_err(backend)? as u32,
            compact,
        })
    }

    fn edge_from_row(row: &PgRow) -> Result<StoredMemoryEdge, StoreError> {
        Ok(StoredMemoryEdge {
            id: EdgeId::from_u128(row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128()),
            tenant_id: TenantId::from_u128(
                row.try_get::<Uuid, _>("tenant_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            scope_id: ScopeId::from_u128(
                row.try_get::<Uuid, _>("scope_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            src_id: UnitId::from_u128(row.try_get::<Uuid, _>("src_id").map_err(backend)?.as_u128()),
            dst_id: UnitId::from_u128(row.try_get::<Uuid, _>("dst_id").map_err(backend)?.as_u128()),
            kind: enum_from_str(row.try_get::<String, _>("kind").map_err(backend)?.as_str())?,
            transaction_from: row.try_get("transaction_from").map_err(backend)?,
            transaction_to: row.try_get("transaction_to").map_err(backend)?,
        })
    }

    fn episode_from_row(row: &PgRow) -> Result<StoredEpisode, StoreError> {
        Ok(StoredEpisode {
            id: EpisodeId::from_u128(row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128()),
            tenant_id: TenantId::from_u128(
                row.try_get::<Uuid, _>("tenant_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            data_subject_id: SubjectId::from_u128(
                row.try_get::<Uuid, _>("data_subject_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            scope_id: ScopeId::from_u128(
                row.try_get::<Uuid, _>("scope_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            actor_id: ActorId::from_u128(
                row.try_get::<Uuid, _>("actor_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            agent_node_id: AgentNodeId::from_u128(
                row.try_get::<Uuid, _>("agent_node_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            subject_generation: row
                .try_get::<i64, _>("subject_generation")
                .map_err(backend)? as u64,
            source_kind: row.try_get("source_kind").map_err(backend)?,
            source_ref: row.try_get("source_ref").map_err(backend)?,
            source_trust: enum_from_str(
                row.try_get::<String, _>("source_trust")
                    .map_err(backend)?
                    .as_str(),
            )?,
            dedup_key: row.try_get("dedup_key").map_err(backend)?,
            body: row
                .try_get::<Option<String>, _>("body")
                .map_err(backend)?
                .unwrap_or_default(),
            observation_count: row
                .try_get::<i32, _>("observation_count")
                .map_err(backend)? as u32,
            first_observed_at: canonical_timestamp(
                row.try_get("first_observed_at").map_err(backend)?,
            )
            .ok_or_else(|| StoreError::Backend("episode first_observed_at is null".to_string()))?,
            last_observed_at: canonical_timestamp(
                row.try_get("last_observed_at").map_err(backend)?,
            )
            .ok_or_else(|| StoreError::Backend("episode last_observed_at is null".to_string()))?,
        })
    }

    fn resource_from_row(row: &PgRow) -> Result<StoredResource, StoreError> {
        let acl = serde_json::from_value::<ResourceAcl>(
            row.try_get::<serde_json::Value, _>("acl")
                .map_err(backend)?,
        )
        .map_err(|error| StoreError::Backend(format!("invalid resource ACL: {error}")))?;
        Ok(StoredResource {
            id: ResourceId::from_u128(row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128()),
            tenant_id: TenantId::from_u128(
                row.try_get::<Uuid, _>("tenant_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            data_subject_id: SubjectId::from_u128(
                row.try_get::<Uuid, _>("data_subject_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            scope_id: ScopeId::from_u128(
                row.try_get::<Uuid, _>("scope_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            actor_id: ActorId::from_u128(
                row.try_get::<Uuid, _>("actor_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            agent_node_id: AgentNodeId::from_u128(
                row.try_get::<Uuid, _>("agent_node_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            subject_generation: row
                .try_get::<i64, _>("subject_generation")
                .map_err(backend)? as u64,
            uri: row.try_get("uri").map_err(backend)?,
            source_ref: row.try_get("source_ref").map_err(backend)?,
            observed_at: canonical_timestamp(row.try_get("observed_at").map_err(backend)?)
                .ok_or_else(|| StoreError::Backend("resource observed_at is null".to_string()))?,
            kind: enum_from_str(row.try_get::<String, _>("kind").map_err(backend)?.as_str())
                .unwrap_or_default(),
            content_hash: row.try_get("content_hash").map_err(backend)?,
            mime_type: row
                .try_get::<Option<String>, _>("mime_type")
                .map_err(backend)?
                .unwrap_or_default(),
            revision: row.try_get("revision").map_err(backend)?,
            body: row.try_get("body").map_err(backend)?,
            source_trust: enum_from_str(
                row.try_get::<String, _>("source_trust")
                    .map_err(backend)?
                    .as_str(),
            )
            .unwrap_or(TrustLevel::Quarantined),
            acl,
            extractor_state: enum_from_str(
                row.try_get::<String, _>("extractor_state")
                    .map_err(backend)?
                    .as_str(),
            )?,
        })
    }

    async fn insert_unit(
        tx: &mut Transaction<'static, Postgres>,
        unit: &StoredMemoryUnit,
    ) -> Result<(), StoreError> {
        let Some(actor_id) = unit.actor_id else {
            return Err(StoreError::NotFound("memory context"));
        };
        let valid: bool = sqlx::query_scalar(
            "select exists (
               select 1
               from memphant.context_binding binding
               join memphant.subject subject
                 on subject.tenant_id = binding.tenant_id
                and subject.id = binding.data_subject_id
              where binding.tenant_id = $1 and binding.data_subject_id = $2
                and binding.actor_id = $3 and binding.scope_id = $4
                and binding.agent_node_id = $5 and subject.generation = $6
             )",
        )
        .bind(unit.tenant_id.as_uuid())
        .bind(unit.data_subject_id.as_uuid())
        .bind(actor_id.as_uuid())
        .bind(unit.scope_id.as_uuid())
        .bind(unit.agent_node_id.as_uuid())
        .bind(unit.subject_generation as i64)
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        if !valid {
            return Err(StoreError::NotFound("memory context"));
        }
        let mut payload = serde_json::json!({ "contextual_chunks": unit.contextual_chunks });
        // The typed compact marker rides in payload.compact. Its presence is
        // what the coding recall lane and the compact exclusion/body-hash index
        // key on; a raw-body unit never carries it.
        if let Some(compact) = &unit.compact {
            payload["compact"] = serde_json::to_value(compact).map_err(|error| {
                StoreError::Backend(format!("compact envelope serialize: {error}"))
            })?;
        }
        if let Some(invalidation) = &unit.invalidation {
            payload["invalidation"] = serde_json::to_value(invalidation).map_err(|error| {
                StoreError::Backend(format!("invalidation marker serialize: {error}"))
            })?;
        }
        sqlx::query(
            "insert into memphant.memory_unit
               (id, tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                kind, state, fact_key, predicate, body, payload, confidence, trust_level,
                valid_from, valid_to, transaction_from, transaction_to, difficulty,
                stability_days, last_reinforced_at, reinforcement_count, freshness_due_at,
                deletion_generation, actor_id, source_kind, source_ref, observed_at,
                source_episode_id,
                source_resource_id, churn_class)
             values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                     $15::timestamptz, $16::timestamptz, coalesce($17::timestamptz, now()),
                     $18::timestamptz, $19, $20, $21::timestamptz, $22, $23::timestamptz,
                     $24, $25, $26, $27, $28::timestamptz, $29, $30, $31)",
        )
        .bind(unit.id.as_uuid())
        .bind(unit.tenant_id.as_uuid())
        .bind(unit.data_subject_id.as_uuid())
        .bind(unit.scope_id.as_uuid())
        .bind(unit.agent_node_id.as_uuid())
        .bind(unit.subject_generation as i64)
        .bind(enum_str(&unit.kind))
        .bind(enum_str(&unit.state))
        .bind(&unit.fact_key)
        .bind(&unit.predicate)
        .bind(&unit.body)
        .bind(payload)
        .bind(unit.confidence)
        .bind(enum_str(&unit.trust_level))
        .bind(&unit.valid_from)
        .bind(&unit.valid_to)
        .bind(&unit.transaction_from)
        .bind(&unit.transaction_to)
        .bind(unit.difficulty)
        .bind(unit.stability_days)
        .bind(&unit.last_reinforced_at)
        .bind(unit.reinforcement_count as i32)
        .bind(&unit.freshness_due_at)
        .bind(unit.deletion_generation.map(|generation| generation as i64))
        .bind(unit.actor_id.map(|id| id.as_uuid()))
        .bind(&unit.source_kind)
        .bind(&unit.source_ref)
        .bind(&unit.observed_at)
        .bind(unit.source_episode_id.map(|id| id.as_uuid()))
        .bind(unit.source_resource_id.map(|id| id.as_uuid()))
        .bind(&unit.churn_class)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;
        Ok(())
    }

    async fn insert_edge(
        tx: &mut Transaction<'static, Postgres>,
        context: &ResolvedMemoryContext,
        edge: &StoredMemoryEdge,
    ) -> Result<(), StoreError> {
        sqlx::query(
            "insert into memphant.memory_edge
               (id, tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                src_id, dst_id, kind, transaction_from, transaction_to)
             values ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                     coalesce($10::timestamptz, now()), $11::timestamptz)
             on conflict do nothing",
        )
        .bind(edge.id.as_uuid())
        .bind(edge.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(edge.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(edge.src_id.as_uuid())
        .bind(edge.dst_id.as_uuid())
        .bind(enum_str(&edge.kind))
        .bind(&edge.transaction_from)
        .bind(&edge.transaction_to)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;
        Ok(())
    }

    /// Idempotently seed an embedding profile inside a caller-owned tx.
    async fn upsert_embedding_profile_tx(
        tx: &mut Transaction<'static, Postgres>,
        tenant: TenantId,
        profile: &EmbeddingProfileRow,
    ) -> Result<(), StoreError> {
        sqlx::query(
            "insert into memphant.embedding_profile
               (id, tenant_id, provider, model, dimensions, distance, version, index_strategy)
             values ($1, $2, $3, $4, $5, $6, $7, $8)
             on conflict (tenant_id, id) do nothing",
        )
        .bind(profile.id)
        .bind(tenant.as_uuid())
        .bind(&profile.provider)
        .bind(&profile.model)
        .bind(profile.dimensions as i32)
        .bind(&profile.distance)
        .bind(&profile.version)
        .bind(&profile.index_strategy)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;
        Ok(())
    }

    /// Upsert one embedding row inside a caller-owned tx. Empty vectors are a
    /// no-op so callers can pass unfiltered rows.
    async fn insert_embedding_tx(
        tx: &mut Transaction<'static, Postgres>,
        context: &ResolvedMemoryContext,
        row: &EmbeddingRow,
    ) -> Result<(), StoreError> {
        if row.vec.is_empty() {
            return Ok(());
        }
        sqlx::query(
            "insert into memphant.embedding
               (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                memory_unit_id, embedding_profile_id, vec)
             values ($1, $2, $3, $4, $5, $6, $7, $8::halfvec)
             on conflict (tenant_id, memory_unit_id, embedding_profile_id)
               do update set vec = excluded.vec",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(row.memory_unit_id.as_uuid())
        .bind(row.embedding_profile_id)
        .bind(vec_literal(&row.vec))
        .execute(&mut **tx)
        .await
        .map_err(backend)?;
        Ok(())
    }

    async fn fetch_units_where(
        tx: &mut Transaction<'static, Postgres>,
        where_clause: &str,
        tail: &str,
        binds: Vec<Bind>,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let sql = Self::unit_select(where_clause, tail);
        let mut query = sqlx::query(AssertSqlSafe(sql.as_str()));
        for bind in binds {
            query = match bind {
                Bind::Uuid(value) => query.bind(value),
                Bind::UuidOpt(value) => query.bind(value),
                Bind::UuidVec(value) => query.bind(value),
                Bind::Text(value) => query.bind(value),
                Bind::TextVec(value) => query.bind(value),
                Bind::I64(value) => query.bind(value),
            };
        }
        let rows = query.fetch_all(&mut **tx).await.map_err(backend)?;
        rows.iter().map(Self::unit_from_row).collect()
    }

    async fn fetch_scope_open_units_tx(
        tx: &mut Transaction<'static, Postgres>,
        context: &ResolvedMemoryContext,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let kinds: Vec<String> = MemoryKind::ALL
            .into_iter()
            .filter(|kind| context.allows(*kind, context.scope_id, context.agent_node_id))
            .map(|kind| enum_str(&kind).to_string())
            .collect();
        Self::fetch_units_where(
            tx,
            "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
             and scope_id = $4 and agent_node_id = $5 and kind = any($6)
             and transaction_to is null",
            "order by id",
            vec![
                Bind::Uuid(context.tenant_id.as_uuid()),
                Bind::Uuid(context.data_subject_id.as_uuid()),
                Bind::I64(context.subject_generation as i64),
                Bind::Uuid(context.scope_id.as_uuid()),
                Bind::Uuid(context.agent_node_id.as_uuid()),
                Bind::TextVec(kinds),
            ],
        )
        .await
    }

    async fn fetch_file_sync_transition_snapshot_tx(
        tx: &mut Transaction<'static, Postgres>,
        context: &ResolvedMemoryContext,
    ) -> Result<FileSyncTransitionSnapshot, StoreError> {
        let units = Self::fetch_units_where(
            tx,
            "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
             and scope_id = $4 and agent_node_id = $5 and actor_id = $6",
            "order by id",
            vec![
                Bind::Uuid(context.tenant_id.as_uuid()),
                Bind::Uuid(context.data_subject_id.as_uuid()),
                Bind::I64(context.subject_generation as i64),
                Bind::Uuid(context.scope_id.as_uuid()),
                Bind::Uuid(context.agent_node_id.as_uuid()),
                Bind::Uuid(context.actor_id.as_uuid()),
            ],
        )
        .await?;
        let unit_ids = units
            .iter()
            .map(|unit| unit.id.as_uuid())
            .collect::<Vec<_>>();
        let rows = sqlx::query(
            r#"select id, tenant_id, scope_id, src_id, dst_id, kind,
                      to_char(transaction_from at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as transaction_from,
                      to_char(transaction_to at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as transaction_to
               from memphant.memory_edge
               where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                 and scope_id = $4 and agent_node_id = $5
                 and src_id = any($6) and dst_id = any($6)
               order by id"#,
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(unit_ids)
        .fetch_all(&mut **tx)
        .await
        .map_err(backend)?;
        let edges = rows
            .iter()
            .map(Self::edge_from_row)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(FileSyncTransitionSnapshot::new(units, edges))
    }

    /// Deletes composition-derived dependents of the given source units;
    /// returns the ids transitioned.
    async fn delete_composed_dependents(
        tx: &mut Transaction<'static, Postgres>,
        context: &ResolvedMemoryContext,
        source_ids: &[Uuid],
        generation: i64,
        now: &str,
    ) -> Result<Vec<UnitId>, StoreError> {
        let rows = sqlx::query(
            "update memphant.memory_unit set state = 'deleted', deletion_generation = $8,
                    transaction_to = $9::timestamptz
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and state <> 'deleted' and source_kind = 'composition'
               and id in (select src_id from memphant.memory_edge
                          where tenant_id = $1 and data_subject_id = $2
                            and subject_generation = $3 and scope_id = $4
                            and agent_node_id = $5 and kind = 'derived_from'
                            and dst_id = any($7))
             returning id",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(source_ids)
        .bind(generation)
        .bind(now)
        .fetch_all(&mut **tx)
        .await
        .map_err(backend)?;
        rows.iter()
            .map(|row| {
                Ok(UnitId::from_u128(
                    row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128(),
                ))
            })
            .collect()
    }

    async fn replace_context_policies(
        tx: &mut Transaction<'_, Postgres>,
        tenant: TenantId,
        subject_id: Uuid,
        grantee_scope_id: Uuid,
        grantee_agent_node_id: Uuid,
        agent_level: u8,
        policies: &[ContextBindingAccessPolicy],
    ) -> Result<(), StoreError> {
        let mut seen = HashSet::new();
        for policy in policies {
            let key = (
                policy.source_scope_external_ref().to_string(),
                policy.source_agent_node_external_ref().to_string(),
                policy.kind(),
            );
            if !seen.insert(key) {
                return Err(StoreError::Conflict("duplicate access policy".to_string()));
            }
        }
        sqlx::query(
            "delete from memphant.scope_policy
              where tenant_id = $1 and data_subject_id = $2
                and grantee_scope_id = $3 and grantee_agent_node_id = $4",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id)
        .bind(grantee_scope_id)
        .bind(grantee_agent_node_id)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;

        for policy in policies {
            let kind = policy.kind();
            if !agent_level_allows_memory_kind(agent_level, kind) {
                return Err(StoreError::Conflict(
                    "memory kind is not allowed for the grantee agent level".to_string(),
                ));
            }
            let source = sqlx::query(
                "select source_scope.id as source_scope_id,
                          source_agent.id as source_agent_node_id,
                          (source_scope.materialized_path @> grantee_scope.materialized_path
                            and source_scope.id <> grantee_scope.id) as scope_ancestor
                     from memphant.scope source_scope
                     join memphant.agent_node source_agent
                       on source_agent.tenant_id = source_scope.tenant_id
                      and source_agent.data_subject_id = source_scope.data_subject_id
                      and source_agent.scope_id = source_scope.id
                     join memphant.scope grantee_scope
                       on grantee_scope.tenant_id = source_scope.tenant_id
                      and grantee_scope.data_subject_id = source_scope.data_subject_id
                      and grantee_scope.id = $4
                    where source_scope.tenant_id = $1
                      and source_scope.data_subject_id = $2
                      and source_scope.external_ref = $6
                      and source_agent.external_ref = $7",
            )
            .bind(tenant.as_uuid())
            .bind(subject_id)
            .bind(grantee_scope_id)
            .bind(grantee_scope_id)
            .bind(grantee_agent_node_id)
            .bind(policy.source_scope_external_ref())
            .bind(policy.source_agent_node_external_ref())
            .fetch_optional(&mut **tx)
            .await
            .map_err(backend)?
            .ok_or(StoreError::NotFound("access policy source context"))?;
            let source_scope_id: Uuid = source.try_get("source_scope_id").map_err(backend)?;
            let source_agent_node_id: Uuid =
                source.try_get("source_agent_node_id").map_err(backend)?;
            match policy {
                ContextBindingAccessPolicy::Inherit { .. } => {
                    let scope_ancestor: bool = source.try_get("scope_ancestor").map_err(backend)?;
                    if agent_level != 0
                        || !matches!(
                            kind,
                            MemoryKind::Episodic | MemoryKind::Semantic | MemoryKind::Belief
                        )
                        || !scope_ancestor
                    {
                        return Err(StoreError::Conflict(
                            "inherit source must be a strict same-subject ancestor root-memory context"
                                .to_string(),
                        ));
                    }
                }
                ContextBindingAccessPolicy::Grant { .. }
                    if source_scope_id == grantee_scope_id
                        && source_agent_node_id == grantee_agent_node_id =>
                {
                    return Err(StoreError::Conflict(
                        "grant source must differ from the grantee context".to_string(),
                    ));
                }
                ContextBindingAccessPolicy::Grant { .. } => {}
            }
            sqlx::query(
                "insert into memphant.scope_policy
                   (id, tenant_id, data_subject_id, source_scope_id,
                    source_agent_node_id, grantee_scope_id, grantee_agent_node_id,
                    kind, mode)
                 values ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            )
            .bind(Uuid::now_v7())
            .bind(tenant.as_uuid())
            .bind(subject_id)
            .bind(source_scope_id)
            .bind(source_agent_node_id)
            .bind(grantee_scope_id)
            .bind(grantee_agent_node_id)
            .bind(enum_str(&kind))
            .bind(match policy.mode() {
                ContextBindingPolicyMode::Inherit => "inherit",
                ContextBindingPolicyMode::Grant => "grant",
            })
            .execute(&mut **tx)
            .await
            .map_err(backend)?;
        }
        Ok(())
    }
}

impl PgTxn {
    fn validate_identity(
        &self,
        tenant_id: TenantId,
        data_subject_id: SubjectId,
        subject_generation: u64,
        scope_id: ScopeId,
        agent_node_id: AgentNodeId,
        actor_id: Option<ActorId>,
    ) -> Result<(), StoreError> {
        if tenant_id != self.context.tenant_id
            || data_subject_id != self.context.data_subject_id
            || subject_generation != self.context.subject_generation
            || scope_id != self.context.scope_id
            || agent_node_id != self.context.agent_node_id
            || actor_id != Some(self.context.actor_id)
        {
            return Err(StoreError::Conflict(
                "write does not match transaction context".to_string(),
            ));
        }
        Ok(())
    }
}

impl MutationLedgerStore for PgStore {
    async fn lookup_mutation_replay(
        &self,
        context: &ResolvedMemoryContext,
        claim: &MutationClaim,
    ) -> Result<Option<MutationResponse>, StoreError> {
        if claim.tenant_id() != context.tenant_id
            || claim.data_subject_id() != context.data_subject_id
            || claim.subject_generation() != context.subject_generation
        {
            return Err(StoreError::IdempotencyConflict);
        }
        let generation = i64::try_from(claim.subject_generation())
            .map_err(|_| StoreError::StaleSubjectGeneration)?;
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let row = sqlx::query(
            "select data_subject_id, subject_generation, request_hash, state,
                    response_status, response_body
             from memphant.mutation_ledger
             where tenant_id = $1 and verb = $2 and idempotency_key = $3
               and expires_at > statement_timestamp()",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(claim.verb().as_str())
        .bind(claim.idempotency_key())
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        let Some(row) = row else {
            tx.commit().await.map_err(backend)?;
            return Ok(None);
        };
        let stored_subject: Uuid = row.try_get("data_subject_id").map_err(backend)?;
        let stored_generation: i64 = row.try_get("subject_generation").map_err(backend)?;
        let stored_hash: Vec<u8> = row.try_get("request_hash").map_err(backend)?;
        if stored_subject != claim.data_subject_id().as_uuid()
            || stored_generation != generation
            || stored_hash.as_slice() != claim.request_hash()
        {
            return Err(StoreError::IdempotencyConflict);
        }
        let state: String = row.try_get("state").map_err(backend)?;
        if state != "completed" {
            tx.commit().await.map_err(backend)?;
            return Ok(None);
        }
        let status: i16 = row.try_get("response_status").map_err(backend)?;
        let body: Vec<u8> = row.try_get("response_body").map_err(backend)?;
        let response = MutationResponse::success(
            u16::try_from(status).map_err(|_| {
                StoreError::Backend("stored mutation response status is invalid".to_string())
            })?,
            body,
        )?;
        tx.commit().await.map_err(backend)?;
        Ok(Some(response))
    }

    async fn stage_mutation_claim(
        &self,
        tx: &mut Self::Txn,
        claim: MutationClaim,
    ) -> Result<MutationClaimOutcome, StoreError> {
        if claim.subject_generation() != tx.context.subject_generation {
            return Err(StoreError::StaleSubjectGeneration);
        }
        if claim.tenant_id() != tx.context.tenant_id
            || claim.data_subject_id() != tx.context.data_subject_id
        {
            return Err(StoreError::IdempotencyConflict);
        }
        if let Some(staged) = &tx.mutation {
            return match staged {
                PgMutationState::Execute {
                    claim: staged_claim,
                    ..
                } if staged_claim == &claim => Ok(MutationClaimOutcome::Execute),
                PgMutationState::Replay {
                    claim: staged_claim,
                    response,
                } if staged_claim == &claim => Ok(MutationClaimOutcome::Replay(response.clone())),
                PgMutationState::Execute { .. } | PgMutationState::Replay { .. } => {
                    Err(StoreError::IdempotencyConflict)
                }
            };
        }

        let generation = i64::try_from(claim.subject_generation())
            .map_err(|_| StoreError::StaleSubjectGeneration)?;
        let subject_lock_sql = if claim.verb() == memphant_core::MutationVerb::EraseSubject {
            "select generation from memphant.subject
             where tenant_id = $1 and id = $2 for update"
        } else {
            "select generation from memphant.subject
             where tenant_id = $1 and id = $2 for share"
        };
        let current_generation: Option<i64> = sqlx::query_scalar(AssertSqlSafe(subject_lock_sql))
            .bind(claim.tenant_id().as_uuid())
            .bind(claim.data_subject_id().as_uuid())
            .fetch_optional(&mut *tx.tx)
            .await
            .map_err(backend)?;
        match current_generation {
            None if claim.verb() == memphant_core::MutationVerb::EraseSubject => {
                let receipt = sqlx::query(
                    "select data_subject_id, subject_generation, request_hash, state,
                            response_status, response_body
                     from memphant.mutation_ledger
                     where tenant_id = $1 and verb = 'erase_subject'
                       and idempotency_key = $2
                       and expires_at > statement_timestamp()
                     for update",
                )
                .bind(claim.tenant_id().as_uuid())
                .bind(claim.idempotency_key())
                .fetch_optional(&mut *tx.tx)
                .await
                .map_err(backend)?;
                let Some(receipt) = receipt else {
                    let tombstoned: bool = sqlx::query_scalar(
                        "select exists(
                           select 1 from memphant.subject_tombstone
                           where tenant_id = $1 and erased_subject_id = $2
                         )",
                    )
                    .bind(claim.tenant_id().as_uuid())
                    .bind(claim.data_subject_id().as_uuid())
                    .fetch_one(&mut *tx.tx)
                    .await
                    .map_err(backend)?;
                    return Err(if tombstoned {
                        StoreError::SubjectErased
                    } else {
                        StoreError::NotFound("memory context")
                    });
                };
                let stored_subject: Uuid = receipt.try_get("data_subject_id").map_err(backend)?;
                let stored_generation: i64 =
                    receipt.try_get("subject_generation").map_err(backend)?;
                let stored_hash: Vec<u8> = receipt.try_get("request_hash").map_err(backend)?;
                if stored_subject != claim.data_subject_id().as_uuid()
                    || stored_generation != generation
                    || stored_hash.as_slice() != claim.request_hash()
                {
                    return Err(StoreError::IdempotencyConflict);
                }
                let state: String = receipt.try_get("state").map_err(backend)?;
                if state != "completed" {
                    return Err(StoreError::Backend(
                        "committed erasure receipt has no response".to_string(),
                    ));
                }
                let status: i16 = receipt.try_get("response_status").map_err(backend)?;
                let body: Vec<u8> = receipt.try_get("response_body").map_err(backend)?;
                let response = MutationResponse::success(
                    u16::try_from(status).map_err(|_| {
                        StoreError::Backend("stored erasure response status is invalid".to_string())
                    })?,
                    body,
                )?;
                tx.mutation = Some(PgMutationState::Replay {
                    claim,
                    response: response.clone(),
                });
                return Ok(MutationClaimOutcome::Replay(response));
            }
            None => {
                let tombstoned: bool = sqlx::query_scalar(
                    "select exists(
                       select 1 from memphant.subject_tombstone
                       where tenant_id = $1 and erased_subject_id = $2
                     )",
                )
                .bind(claim.tenant_id().as_uuid())
                .bind(claim.data_subject_id().as_uuid())
                .fetch_one(&mut *tx.tx)
                .await
                .map_err(backend)?;
                return Err(if tombstoned {
                    StoreError::SubjectErased
                } else {
                    StoreError::NotFound("memory context")
                });
            }
            Some(current) if current != generation => {
                return Err(StoreError::StaleSubjectGeneration);
            }
            Some(_) => {}
        }

        let inserted = sqlx::query_scalar::<_, bool>(
            "insert into memphant.mutation_ledger
               (tenant_id, verb, idempotency_key, data_subject_id,
                subject_generation, request_hash, state)
             values ($1, $2, $3, $4, $5, $6, 'pending')
             on conflict (tenant_id, verb, idempotency_key) do nothing
             returning true",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.verb().as_str())
        .bind(claim.idempotency_key())
        .bind(claim.data_subject_id().as_uuid())
        .bind(generation)
        .bind(claim.request_hash().as_slice())
        .fetch_optional(&mut *tx.tx)
        .await
        .map_err(backend)?
        .unwrap_or(false);

        let row = sqlx::query(
            "select data_subject_id, subject_generation, request_hash, state,
                    response_status, response_body,
                    expires_at <= statement_timestamp() as expired
             from memphant.mutation_ledger
             where tenant_id = $1 and verb = $2 and idempotency_key = $3
             for update",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.verb().as_str())
        .bind(claim.idempotency_key())
        .fetch_one(&mut *tx.tx)
        .await
        .map_err(backend)?;

        if inserted {
            tx.mutation = Some(PgMutationState::Execute {
                claim,
                response_staged: false,
            });
            return Ok(MutationClaimOutcome::Execute);
        }

        let expired: bool = row.try_get("expired").map_err(backend)?;
        if expired {
            sqlx::query(
                "update memphant.mutation_ledger
                 set data_subject_id = $4, subject_generation = $5, request_hash = $6,
                     state = 'pending', response_status = null, response_body = null,
                     created_at = statement_timestamp(),
                     expires_at = statement_timestamp() + interval '24 hours'
                 where tenant_id = $1 and verb = $2 and idempotency_key = $3",
            )
            .bind(claim.tenant_id().as_uuid())
            .bind(claim.verb().as_str())
            .bind(claim.idempotency_key())
            .bind(claim.data_subject_id().as_uuid())
            .bind(generation)
            .bind(claim.request_hash().as_slice())
            .execute(&mut *tx.tx)
            .await
            .map_err(backend)?;
            tx.mutation = Some(PgMutationState::Execute {
                claim,
                response_staged: false,
            });
            return Ok(MutationClaimOutcome::Execute);
        }

        let stored_subject: Uuid = row.try_get("data_subject_id").map_err(backend)?;
        let stored_generation: i64 = row.try_get("subject_generation").map_err(backend)?;
        let stored_hash: Vec<u8> = row.try_get("request_hash").map_err(backend)?;
        if stored_subject != claim.data_subject_id().as_uuid()
            || stored_generation != generation
            || stored_hash.as_slice() != claim.request_hash()
        {
            return Err(StoreError::IdempotencyConflict);
        }

        let state: String = row.try_get("state").map_err(backend)?;
        if state != "completed" {
            return Err(StoreError::Backend(
                "committed mutation ledger row has no response".to_string(),
            ));
        }
        let status: i16 = row.try_get("response_status").map_err(backend)?;
        let body: Vec<u8> = row.try_get("response_body").map_err(backend)?;
        let status = u16::try_from(status).map_err(|_| {
            StoreError::Backend("stored mutation response status is invalid".to_string())
        })?;
        let response = MutationResponse::success(status, body)?;
        tx.mutation = Some(PgMutationState::Replay {
            claim,
            response: response.clone(),
        });
        Ok(MutationClaimOutcome::Replay(response))
    }

    async fn stage_mutation_response(
        &self,
        tx: &mut Self::Txn,
        response: MutationResponse,
    ) -> Result<(), StoreError> {
        let claim = match tx.mutation.as_ref() {
            Some(PgMutationState::Execute { claim, .. }) => claim,
            Some(PgMutationState::Replay { .. }) => {
                return Err(StoreError::Conflict(
                    "replayed mutation cannot stage a new response".to_string(),
                ));
            }
            None => {
                return Err(StoreError::Conflict(
                    "mutation claim must be staged before its response".to_string(),
                ));
            }
        };
        if claim.verb() == memphant_core::MutationVerb::EraseSubject {
            return Err(StoreError::Conflict(
                "subject erasure response is generated by the store".to_string(),
            ));
        }
        let generation = i64::try_from(claim.subject_generation())
            .map_err(|_| StoreError::StaleSubjectGeneration)?;
        let result = sqlx::query(
            "update memphant.mutation_ledger
             set state = 'completed', response_status = $7, response_body = $8
             where tenant_id = $1 and verb = $2 and idempotency_key = $3
               and data_subject_id = $4 and subject_generation = $5
               and request_hash = $6 and state = 'pending'",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.verb().as_str())
        .bind(claim.idempotency_key())
        .bind(claim.data_subject_id().as_uuid())
        .bind(generation)
        .bind(claim.request_hash().as_slice())
        .bind(i16::try_from(response.status()).map_err(|_| {
            StoreError::Backend("mutation response status exceeds smallint".to_string())
        })?)
        .bind(response.body())
        .execute(&mut *tx.tx)
        .await
        .map_err(backend)?;
        if result.rows_affected() != 1 {
            return Err(StoreError::Backend(
                "mutation ledger response did not match its pending claim".to_string(),
            ));
        }
        if let Some(PgMutationState::Execute {
            response_staged, ..
        }) = tx.mutation.as_mut()
        {
            *response_staged = true;
        }
        Ok(())
    }

    async fn stage_subject_erasure(
        &self,
        tx: &mut Self::Txn,
    ) -> Result<SubjectErasureReceipt, StoreError> {
        if tx.has_subject_writes {
            return Err(StoreError::Conflict(
                "subject erasure requires an otherwise empty transaction".to_string(),
            ));
        }
        let claim = match tx.mutation.as_ref() {
            Some(PgMutationState::Execute {
                claim,
                response_staged: false,
            }) if claim.verb() == memphant_core::MutationVerb::EraseSubject => claim.clone(),
            Some(PgMutationState::Execute { .. }) | Some(PgMutationState::Replay { .. }) => {
                return Err(StoreError::Conflict(
                    "subject erasure requires an executable erase_subject claim".to_string(),
                ));
            }
            None => {
                return Err(StoreError::Conflict(
                    "erasure claim must be staged first".to_string(),
                ));
            }
        };
        let old_generation = i64::try_from(claim.subject_generation())
            .map_err(|_| StoreError::StaleSubjectGeneration)?;
        let new_generation = old_generation
            .checked_add(1)
            .ok_or_else(|| StoreError::Conflict("subject generation overflow".to_string()))?;

        let locked_generation: Option<i64> = sqlx::query_scalar(
            "select generation from memphant.subject
             where tenant_id = $1 and id = $2 for update",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.data_subject_id().as_uuid())
        .fetch_optional(&mut *tx.tx)
        .await
        .map_err(backend)?;
        match locked_generation {
            None => return Err(StoreError::SubjectErased),
            Some(generation) if generation != old_generation => {
                return Err(StoreError::StaleSubjectGeneration);
            }
            Some(_) => {}
        }

        let updated = sqlx::query(
            "update memphant.subject set generation = $3, updated_at = statement_timestamp()
             where tenant_id = $1 and id = $2 and generation = $4",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.data_subject_id().as_uuid())
        .bind(new_generation)
        .bind(old_generation)
        .execute(&mut *tx.tx)
        .await
        .map_err(backend)?;
        if updated.rows_affected() != 1 {
            return Err(StoreError::StaleSubjectGeneration);
        }

        sqlx::query(
            "delete from memphant.mutation_ledger
             where tenant_id = $1 and data_subject_id = $2
               and not (verb = 'erase_subject' and idempotency_key = $3)",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.data_subject_id().as_uuid())
        .bind(claim.idempotency_key())
        .execute(&mut *tx.tx)
        .await
        .map_err(backend)?;

        let erased_at: String = sqlx::query_scalar(
            "insert into memphant.subject_tombstone
               (tenant_id, erased_subject_id, generation, erased_at)
             values ($1, $2, $3, statement_timestamp())
             returning to_char(
               erased_at at time zone 'utc',
               'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
             )",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.data_subject_id().as_uuid())
        .bind(new_generation)
        .fetch_one(&mut *tx.tx)
        .await
        .map_err(backend)?;
        let erased_at = canonical_timestamp(Some(erased_at)).ok_or_else(|| {
            StoreError::Backend("subject tombstone omitted erasure timestamp".to_string())
        })?;

        let deleted = sqlx::query(
            "delete from memphant.subject
             where tenant_id = $1 and id = $2 and generation = $3",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.data_subject_id().as_uuid())
        .bind(new_generation)
        .execute(&mut *tx.tx)
        .await
        .map_err(backend)?;
        if deleted.rows_affected() != 1 {
            return Err(StoreError::Backend(
                "subject erasure did not delete its locked subject".to_string(),
            ));
        }

        let receipt = SubjectErasureReceipt {
            generation: u64::try_from(new_generation).map_err(|_| {
                StoreError::Backend("subject erasure generation is invalid".to_string())
            })?,
            erased_at,
        };
        let body =
            serde_json::to_vec(&receipt).map_err(|error| StoreError::Backend(error.to_string()))?;
        let response = MutationResponse::success(200, body)?;
        let stored = sqlx::query(
            "update memphant.mutation_ledger
             set state = 'completed', response_status = $7, response_body = $8
             where tenant_id = $1 and verb = $2 and idempotency_key = $3
               and data_subject_id = $4 and subject_generation = $5
               and request_hash = $6 and state = 'pending'",
        )
        .bind(claim.tenant_id().as_uuid())
        .bind(claim.verb().as_str())
        .bind(claim.idempotency_key())
        .bind(claim.data_subject_id().as_uuid())
        .bind(old_generation)
        .bind(claim.request_hash().as_slice())
        .bind(i16::try_from(response.status()).map_err(|_| {
            StoreError::Backend("erasure response status exceeds smallint".to_string())
        })?)
        .bind(response.body())
        .execute(&mut *tx.tx)
        .await
        .map_err(backend)?;
        if stored.rows_affected() != 1 {
            return Err(StoreError::Backend(
                "subject erasure receipt did not match its pending claim".to_string(),
            ));
        }
        if let Some(PgMutationState::Execute {
            response_staged, ..
        }) = tx.mutation.as_mut()
        {
            *response_staged = true;
        }
        Ok(receipt)
    }

    async fn stage_task_outcome(
        &self,
        tx: &mut Self::Txn,
        outcome: TaskOutcomeRow,
        events: Vec<TaskMemoryEventRow>,
    ) -> Result<(), StoreError> {
        tx.validate_identity(
            outcome.tenant_id,
            outcome.data_subject_id,
            outcome.subject_generation,
            outcome.scope_id,
            outcome.agent_node_id,
            Some(outcome.actor_id),
        )?;
        let completion = match outcome.completion_status {
            memphant_types::TaskCompletionStatus::Completed => "completed",
            memphant_types::TaskCompletionStatus::Failed => "failed",
            memphant_types::TaskCompletionStatus::Cancelled => "cancelled",
        };
        let validator = match outcome.validator_status {
            memphant_types::TaskValidatorStatus::Passed => "passed",
            memphant_types::TaskValidatorStatus::Failed => "failed",
            memphant_types::TaskValidatorStatus::NotRun => "not_run",
        };
        sqlx::query(
            "insert into memphant.task_outcome
            (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, actor_id,
             task_id, harness_id, model_id, started_at, ended_at, completion_status,
             validator_status, tool_count, failure_count, retry_count, planned_files, actual_files,
             scope_recall, scope_precision, scope_jaccard, transcript_sha256, recorded_at)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::timestamptz,$11::timestamptz,$12,$13,
                    $14,$15,$16,$17,$18,$19,$20,$21,$22,$23::timestamptz)",
        )
        .bind(outcome.tenant_id.as_uuid())
        .bind(outcome.data_subject_id.as_uuid())
        .bind(
            i64::try_from(outcome.subject_generation)
                .map_err(|_| StoreError::StaleSubjectGeneration)?,
        )
        .bind(outcome.scope_id.as_uuid())
        .bind(outcome.agent_node_id.as_uuid())
        .bind(outcome.actor_id.as_uuid())
        .bind(outcome.task_id.as_uuid())
        .bind(outcome.harness_id)
        .bind(outcome.model_id)
        .bind(outcome.started_at)
        .bind(outcome.ended_at)
        .bind(completion)
        .bind(validator)
        .bind(i64::from(outcome.tool_count))
        .bind(i64::from(outcome.failure_count))
        .bind(i64::from(outcome.retry_count))
        .bind(outcome.planned_files)
        .bind(outcome.actual_files)
        .bind(outcome.scope_recall)
        .bind(outcome.scope_precision)
        .bind(outcome.scope_jaccard)
        .bind(outcome.transcript_sha256)
        .bind(outcome.recorded_at)
        .execute(&mut *tx.tx)
        .await
        .map_err(backend)?;
        for event in events {
            sqlx::query(
                "insert into memphant.task_memory_event
                (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, actor_id,
                 task_id, memory_unit_id, event, attribution, recorded_at)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::timestamptz)",
            )
            .bind(event.tenant_id.as_uuid())
            .bind(event.data_subject_id.as_uuid())
            .bind(
                i64::try_from(event.subject_generation)
                    .map_err(|_| StoreError::StaleSubjectGeneration)?,
            )
            .bind(event.scope_id.as_uuid())
            .bind(event.agent_node_id.as_uuid())
            .bind(event.actor_id.as_uuid())
            .bind(event.task_id.as_uuid())
            .bind(event.unit_id.as_uuid())
            .bind(task_event_name(event.event))
            .bind(task_attribution_name(event.attribution))
            .bind(event.recorded_at)
            .execute(&mut *tx.tx)
            .await
            .map_err(backend)?;
        }
        Ok(())
    }

    async fn stage_task_memory_events(
        &self,
        tx: &mut Self::Txn,
        events: Vec<TaskMemoryEventRow>,
    ) -> Result<(), StoreError> {
        for event in events {
            tx.validate_identity(
                event.tenant_id,
                event.data_subject_id,
                event.subject_generation,
                event.scope_id,
                event.agent_node_id,
                Some(event.actor_id),
            )?;
            if matches!(
                event.event,
                TaskMemoryEventKind::Helpful | TaskMemoryEventKind::Harmful
            ) {
                let exposed: bool = sqlx::query_scalar(
                    "select exists(
                    select 1 from memphant.task_memory_event where tenant_id=$1 and task_id=$2
                      and memory_unit_id=$3 and event in ('shown','activated'))",
                )
                .bind(event.tenant_id.as_uuid())
                .bind(event.task_id.as_uuid())
                .bind(event.unit_id.as_uuid())
                .fetch_one(&mut *tx.tx)
                .await
                .map_err(backend)?;
                if !exposed {
                    return Err(StoreError::Conflict(
                        "task evidence requires prior exposure".to_string(),
                    ));
                }
            }
            sqlx::query(
                "insert into memphant.task_memory_event
                (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, actor_id,
                 task_id, memory_unit_id, event, attribution, recorded_at)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::timestamptz)",
            )
            .bind(event.tenant_id.as_uuid())
            .bind(event.data_subject_id.as_uuid())
            .bind(
                i64::try_from(event.subject_generation)
                    .map_err(|_| StoreError::StaleSubjectGeneration)?,
            )
            .bind(event.scope_id.as_uuid())
            .bind(event.agent_node_id.as_uuid())
            .bind(event.actor_id.as_uuid())
            .bind(event.task_id.as_uuid())
            .bind(event.unit_id.as_uuid())
            .bind(task_event_name(event.event))
            .bind(task_attribution_name(event.attribution))
            .bind(event.recorded_at)
            .execute(&mut *tx.tx)
            .await
            .map_err(backend)?;
        }
        Ok(())
    }
}

impl MemoryStore for PgStore {
    type Txn = PgTxn;

    async fn begin(&self, context: &ResolvedMemoryContext) -> Result<Self::Txn, StoreError> {
        let mut tx = self.pool.begin().await.map_err(backend)?;
        sqlx::query("select memphant.bind_tenant($1)")
            .bind(context.tenant_id.as_uuid())
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        Ok(PgTxn {
            tx,
            context: context.clone(),
            mutation: None,
            has_subject_writes: false,
        })
    }

    async fn begin_serializable(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<Self::Txn, StoreError> {
        let mut tx = self.pool.begin().await.map_err(backend)?;
        sqlx::query("set transaction isolation level serializable")
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        sqlx::query("select memphant.bind_tenant($1)")
            .bind(context.tenant_id.as_uuid())
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        Ok(PgTxn {
            tx,
            context: context.clone(),
            mutation: None,
            has_subject_writes: false,
        })
    }

    async fn commit(&self, tx: Self::Txn) -> Result<(), StoreError> {
        match tx.mutation.as_ref() {
            Some(PgMutationState::Execute {
                response_staged: false,
                ..
            }) => {
                tx.tx.rollback().await.map_err(backend)?;
                Err(StoreError::Conflict(
                    "successful mutation response must be staged before commit".to_string(),
                ))
            }
            Some(PgMutationState::Replay { .. }) => tx.tx.rollback().await.map_err(backend),
            Some(PgMutationState::Execute {
                response_staged: true,
                ..
            })
            | None => tx.tx.commit().await.map_err(backend),
        }
    }

    async fn rollback(&self, tx: Self::Txn) -> Result<(), StoreError> {
        tx.tx.rollback().await.map_err(backend)
    }

    async fn stage_episode(
        &self,
        tx: &mut Self::Txn,
        episode: NewEpisode,
    ) -> Result<RetainOutcome, StoreError> {
        tx.validate_identity(
            episode.tenant_id,
            episode.data_subject_id,
            episode.subject_generation,
            episode.scope_id,
            episode.agent_node_id,
            Some(episode.actor_id),
        )?;
        Self::validate_episode_context(&mut tx.tx, &episode).await?;
        let row = sqlx::query(
            "insert into memphant.episode
               (id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                subject_generation, source_kind, source_ref, source_trust, dedup_key, body,
                first_observed_at, last_observed_at)
             values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                     $13::timestamptz, $13::timestamptz)
             on conflict (tenant_id, data_subject_id, subject_generation, scope_id,
                          agent_node_id, actor_id, dedup_key) do update
               set observation_count = memphant.episode.observation_count + 1,
                   first_observed_at = least(memphant.episode.first_observed_at, excluded.first_observed_at),
                   last_observed_at = greatest(memphant.episode.last_observed_at, excluded.last_observed_at)
             returning id, observation_count, (xmax = 0) as inserted",
        )
        .bind(Uuid::now_v7())
        .bind(episode.tenant_id.as_uuid())
        .bind(episode.data_subject_id.as_uuid())
        .bind(episode.scope_id.as_uuid())
        .bind(episode.actor_id.as_uuid())
        .bind(episode.agent_node_id.as_uuid())
        .bind(episode.subject_generation as i64)
        .bind(&episode.source_kind)
        .bind(&episode.source_ref)
        .bind(enum_str(&episode.source_trust))
        .bind(&episode.dedup_key)
        .bind(&episode.body)
        .bind(&episode.observed_at)
        .fetch_one(&mut *tx.tx)
        .await
        .map_err(backend)?;
        let inserted: bool = row.try_get("inserted").map_err(backend)?;
        tx.has_subject_writes = true;
        Ok(RetainOutcome {
            episode_id: EpisodeId::from_u128(
                row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128(),
            ),
            dedup: memphant_types::DedupOutcome {
                matched: !inserted,
                observation_count: row
                    .try_get::<i32, _>("observation_count")
                    .map_err(backend)? as u32,
            },
        })
    }

    async fn stage_memory_unit(
        &self,
        tx: &mut Self::Txn,
        unit: NewMemoryUnit,
    ) -> Result<UnitId, StoreError> {
        tx.validate_identity(
            unit.tenant_id,
            unit.data_subject_id,
            unit.subject_generation,
            unit.scope_id,
            unit.agent_node_id,
            unit.actor_id,
        )?;
        let id = UnitId::new();
        let stored = StoredMemoryUnit {
            invalidation: None,
            compact: None,
            id,
            tenant_id: unit.tenant_id,
            data_subject_id: unit.data_subject_id,
            scope_id: unit.scope_id,
            agent_node_id: unit.agent_node_id,
            subject_generation: unit.subject_generation,
            kind: unit.kind,
            state: unit.state,
            fact_key: unit.fact_key,
            predicate: unit.predicate,
            body: unit.body,
            confidence: unit.confidence,
            trust_level: unit.trust_level,
            churn_class: unit.churn_class,
            freshness_due_at: unit.freshness_due_at,
            actor_id: unit.actor_id,
            source_kind: unit.source_kind,
            source_ref: unit.source_ref,
            observed_at: unit.observed_at,
            source_episode_id: unit.source_episode_id,
            source_resource_id: unit.source_resource_id,
            deletion_generation: unit.deletion_generation,
            contextual_chunks: unit.contextual_chunks,
            valid_from: unit.valid_from,
            valid_to: unit.valid_to,
            transaction_from: unit.transaction_from,
            transaction_to: unit.transaction_to,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        };
        Self::insert_unit(&mut tx.tx, &stored).await?;
        tx.has_subject_writes = true;
        Ok(id)
    }

    async fn stage_resource(
        &self,
        tx: &mut Self::Txn,
        resource: NewResource,
    ) -> Result<ResourceId, StoreError> {
        tx.validate_identity(
            resource.tenant_id,
            resource.data_subject_id,
            resource.subject_generation,
            resource.scope_id,
            resource.agent_node_id,
            Some(resource.actor_id),
        )?;
        Self::validate_resource_context(&mut tx.tx, &resource).await?;
        let id = ResourceId::new();
        let acl = serde_json::to_value(&resource.acl)
            .map_err(|error| StoreError::Backend(format!("serialize resource ACL: {error}")))?;
        sqlx::query(
            "insert into memphant.resource
               (id, tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                kind, uri, source_ref, observed_at, content_hash, actor_id, mime_type, revision,
                body, source_trust, acl)
             values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::timestamptz, $11,
                     $12, $13, $14, $15, $16, $17)",
        )
        .bind(id.as_uuid())
        .bind(resource.tenant_id.as_uuid())
        .bind(resource.data_subject_id.as_uuid())
        .bind(resource.scope_id.as_uuid())
        .bind(resource.agent_node_id.as_uuid())
        .bind(resource.subject_generation as i64)
        .bind(enum_str(&resource.kind))
        .bind(&resource.uri)
        .bind(&resource.source_ref)
        .bind(&resource.observed_at)
        .bind(&resource.content_hash)
        .bind(resource.actor_id.as_uuid())
        .bind(&resource.mime_type)
        .bind(&resource.revision)
        .bind(&resource.body)
        .bind(enum_str(&resource.source_trust))
        .bind(acl)
        .execute(&mut *tx.tx)
        .await
        .map_err(backend)?;
        tx.has_subject_writes = true;
        Ok(id)
    }

    async fn stage_memory_edge(
        &self,
        tx: &mut Self::Txn,
        edge: NewMemoryEdge,
    ) -> Result<EdgeId, StoreError> {
        if edge.tenant_id != tx.context.tenant_id || edge.scope_id != tx.context.scope_id {
            return Err(StoreError::Conflict(
                "memory edge does not match transaction context".to_string(),
            ));
        }
        let endpoint_count: i64 = sqlx::query_scalar(
            "select count(*) from memphant.memory_unit
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = any($7)",
        )
        .bind(tx.context.tenant_id.as_uuid())
        .bind(tx.context.data_subject_id.as_uuid())
        .bind(tx.context.subject_generation as i64)
        .bind(tx.context.scope_id.as_uuid())
        .bind(tx.context.agent_node_id.as_uuid())
        .bind(tx.context.actor_id.as_uuid())
        .bind(vec![edge.src_id.as_uuid(), edge.dst_id.as_uuid()])
        .fetch_one(&mut *tx.tx)
        .await
        .map_err(backend)?;
        let expected = if edge.src_id == edge.dst_id { 1 } else { 2 };
        if endpoint_count != expected {
            return Err(StoreError::Conflict(
                "memory edge endpoints must belong to the transaction context".to_string(),
            ));
        }
        let id = EdgeId::new();
        Self::insert_edge(
            &mut tx.tx,
            &tx.context,
            &StoredMemoryEdge {
                id,
                tenant_id: edge.tenant_id,
                scope_id: edge.scope_id,
                src_id: edge.src_id,
                dst_id: edge.dst_id,
                kind: edge.kind,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await?;
        tx.has_subject_writes = true;
        Ok(id)
    }

    async fn enqueue_reflect(
        &self,
        tx: &mut Self::Txn,
        job: ReflectJob,
    ) -> Result<JobId, StoreError> {
        tx.validate_identity(
            job.tenant_id,
            job.data_subject_id,
            job.subject_generation,
            job.scope_id,
            job.agent_node_id,
            Some(job.actor_id),
        )?;
        let id = JobId::new();
        let (job_type, target) = match job.kind {
            ReflectJobKind::ReflectEpisode if job.resource_id.is_none() => (
                "reflect_episode",
                job.episode_id.map(|episode| episode.as_uuid()),
            ),
            ReflectJobKind::ReflectResource if job.episode_id.is_none() => (
                "reflect_resource",
                job.resource_id.map(|resource| resource.as_uuid()),
            ),
            ReflectJobKind::ReflectScope
                if job.episode_id.is_none() && job.resource_id.is_none() =>
            {
                ("reflect_scope", Some(id.as_uuid()))
            }
            _ => {
                return Err(StoreError::Conflict(
                    "reflect job source identifiers must exactly match its kind".to_string(),
                ));
            }
        };
        let target = target.ok_or_else(|| {
            StoreError::Conflict(
                "reflect job source identifiers must exactly match its kind".to_string(),
            )
        })?;
        let target_query = match job.kind {
            ReflectJobKind::ReflectEpisode => Some(
                "select exists(select 1 from memphant.episode
                 where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                   and scope_id = $4 and agent_node_id = $5 and actor_id = $6 and id = $7)",
            ),
            ReflectJobKind::ReflectResource => Some(
                "select exists(select 1 from memphant.resource
                 where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                   and scope_id = $4 and agent_node_id = $5 and actor_id = $6 and id = $7)",
            ),
            ReflectJobKind::ReflectScope => None,
        };
        if let Some(query) = target_query {
            let target_matches: bool = sqlx::query_scalar(query)
                .bind(tx.context.tenant_id.as_uuid())
                .bind(tx.context.data_subject_id.as_uuid())
                .bind(tx.context.subject_generation as i64)
                .bind(tx.context.scope_id.as_uuid())
                .bind(tx.context.agent_node_id.as_uuid())
                .bind(tx.context.actor_id.as_uuid())
                .bind(target)
                .fetch_one(&mut *tx.tx)
                .await
                .map_err(backend)?;
            if !target_matches {
                return Err(StoreError::Conflict(
                    "reflect job target must belong to the transaction context".to_string(),
                ));
            }
        }
        let persisted_id: Option<Uuid> = sqlx::query_scalar(
            "insert into memphant.job_state
               (id, tenant_id, data_subject_id, actor_id, agent_node_id, subject_generation,
                job_type, target_id, compiler_version, state, scope_id, subject, predicate)
             values ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'queued', $10, $11, $12)
             on conflict (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id,
                          actor_id, job_type, target_id, compiler_version) do nothing
             returning id",
        )
        .bind(id.as_uuid())
        .bind(job.tenant_id.as_uuid())
        .bind(job.data_subject_id.as_uuid())
        .bind(job.actor_id.as_uuid())
        .bind(job.agent_node_id.as_uuid())
        .bind(job.subject_generation as i64)
        .bind(job_type)
        .bind(target)
        .bind(&job.compiler_version)
        .bind(job.scope_id.as_uuid())
        .bind(&job.subject)
        .bind(&job.predicate)
        .fetch_optional(&mut *tx.tx)
        .await
        .map_err(backend)?;
        let persisted_id = match persisted_id {
            Some(id) => id,
            None => sqlx::query_scalar(
                "select id from memphant.job_state
                 where tenant_id = $1 and data_subject_id = $2 and actor_id = $3
                   and agent_node_id = $4 and subject_generation = $5
                   and job_type = $6 and target_id = $7 and compiler_version = $8
                   and scope_id = $9",
            )
            .bind(job.tenant_id.as_uuid())
            .bind(job.data_subject_id.as_uuid())
            .bind(job.actor_id.as_uuid())
            .bind(job.agent_node_id.as_uuid())
            .bind(job.subject_generation as i64)
            .bind(job_type)
            .bind(target)
            .bind(&job.compiler_version)
            .bind(job.scope_id.as_uuid())
            .fetch_one(&mut *tx.tx)
            .await
            .map_err(backend)?,
        };
        tx.has_subject_writes = true;
        Ok(JobId::from_u128(persisted_id.as_u128()))
    }

    async fn fetch_recall_candidates(
        &self,
        context: &ResolvedMemoryContext,
        kinds: &[MemoryKind],
        query_terms: &[String],
        time: &RecallTime,
        limit: usize,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let (scope_uuids, agent_uuids, allowed_kind_strs) = source_kind_triples(context, kinds);
        let kind_strs: Vec<String> = kinds.iter().map(enum_str).collect();
        let base = "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                    and exists (
                      select 1 from unnest($4::uuid[], $5::uuid[], $6::text[])
                        allowed(scope_id, agent_node_id, kind)
                      where allowed.scope_id = memphant.memory_unit.scope_id
                        and allowed.agent_node_id = memphant.memory_unit.agent_node_id
                        and allowed.kind = memphant.memory_unit.kind
                    )
                    and (cardinality($7::text[]) = 0 or kind = any($7))
                    and deletion_generation is null and state <> 'deleted'
                    and coalesce(transaction_from, '-infinity'::timestamptz) <= $8::timestamptz
                    and $8::timestamptz < coalesce(transaction_to, 'infinity'::timestamptz)
                    and coalesce(valid_from, '-infinity'::timestamptz) <= $9::timestamptz
                    and $9::timestamptz < coalesce(valid_to, 'infinity'::timestamptz)";
        let mut seen: HashSet<Uuid> = HashSet::new();
        let mut units = Vec::new();
        let mut extend = |fetched: Vec<StoredMemoryUnit>| {
            for unit in fetched {
                if seen.insert(unit.id.as_uuid()) {
                    units.push(unit);
                }
            }
        };

        // Family 1: FTS top-N over body_tsv (websearch, OR-joined terms).
        if !query_terms.is_empty() {
            let websearch = query_terms.join(" or ");
            let fetched = Self::fetch_units_where(
                    &mut tx,
                    &format!(
                        "{base} and body_tsv @@ websearch_to_tsquery('english', $10)"
                    ),
                    // `, body` is a content-derived tie-break: keeps the top-200
                    // cut deterministic when ts_rank ties, so a re-ingest with
                    // fresh UUIDs ranks the same corpus identically.
                    "order by ts_rank_cd(body_tsv, websearch_to_tsquery('english', $10)) desc, body limit 200",
                    vec![
                        Bind::Uuid(context.tenant_id.as_uuid()),
                        Bind::Uuid(context.data_subject_id.as_uuid()),
                        Bind::I64(context.subject_generation as i64),
                        Bind::UuidVec(scope_uuids.clone()),
                        Bind::UuidVec(agent_uuids.clone()),
                        Bind::TextVec(allowed_kind_strs.clone()),
                        Bind::TextVec(kind_strs.clone()),
                        Bind::Text(time.transaction_as_of.clone()),
                        Bind::Text(time.valid_at.clone()),
                        Bind::Text(websearch.clone()),
                    ],
                )
                .await?;
            extend(fetched);
        }

        // Family 2: most-recent-M per scope.
        let mut unique_scopes = scope_uuids.clone();
        unique_scopes.sort_unstable();
        unique_scopes.dedup();
        for scope in &unique_scopes {
            let fetched = Self::fetch_units_where(
                &mut tx,
                &format!("{base} and scope_id = $10"),
                "order by transaction_from desc, body limit 100",
                vec![
                    Bind::Uuid(context.tenant_id.as_uuid()),
                    Bind::Uuid(context.data_subject_id.as_uuid()),
                    Bind::I64(context.subject_generation as i64),
                    Bind::UuidVec(scope_uuids.clone()),
                    Bind::UuidVec(agent_uuids.clone()),
                    Bind::TextVec(allowed_kind_strs.clone()),
                    Bind::TextVec(kind_strs.clone()),
                    Bind::Text(time.transaction_as_of.clone()),
                    Bind::Text(time.valid_at.clone()),
                    Bind::Uuid(*scope),
                ],
            )
            .await?;
            extend(fetched);
        }

        // Family 3: exact-subject matches.
        if !query_terms.is_empty() {
            let fetched = Self::fetch_units_where(
                &mut tx,
                &format!(
                    "{base} and fact_key is not null
                         and exists (select 1 from unnest($10::text[]) term
                                     where memphant.memory_unit.fact_key ilike '%' || term || '%')"
                ),
                "order by body limit 200",
                vec![
                    Bind::Uuid(context.tenant_id.as_uuid()),
                    Bind::Uuid(context.data_subject_id.as_uuid()),
                    Bind::I64(context.subject_generation as i64),
                    Bind::UuidVec(scope_uuids.clone()),
                    Bind::UuidVec(agent_uuids.clone()),
                    Bind::TextVec(allowed_kind_strs.clone()),
                    Bind::TextVec(kind_strs.clone()),
                    Bind::Text(time.transaction_as_of.clone()),
                    Bind::Text(time.valid_at.clone()),
                    Bind::TextVec(query_terms.to_vec()),
                ],
            )
            .await?;
            extend(fetched);
        }

        // The vector family lives in `fetch_vector_candidates` — it carries the
        // `<=>` distance back to core fusion and applies the mandatory
        // embedding_profile_id predicate.

        units.truncate(limit.min(1_000));
        Ok(units)
    }

    async fn fetch_deep_snapshot(
        &self,
        context: &ResolvedMemoryContext,
        time: &RecallTime,
    ) -> Result<Vec<DeepSnapshotEntry>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let (unit_scopes, unit_agents, unit_kinds) = source_kind_triples(context, &[]);
        let source_pairs = |kind: MemoryKind| {
            context
                .sources_by_kind
                .get(&kind)
                .into_iter()
                .flatten()
                .map(|source| (source.scope_id.as_uuid(), source.agent_node_id.as_uuid()))
                .unzip::<_, _, Vec<_>, Vec<_>>()
        };
        let (episode_scopes, episode_agents) = source_pairs(MemoryKind::Episodic);
        let (resource_scopes, resource_agents) = source_pairs(MemoryKind::Resource);

        // One joined snapshot query: raw bodies and their authorizing unit
        // records are read together, with source/unit tombstones and all owner
        // predicates applied before a body can enter the returned row set.
        let rows = sqlx::query(
            r#"with eligible_unit as (
                 select unit.*
                 from memphant.memory_unit unit
                 where unit.tenant_id = $1
                   and unit.data_subject_id = $2
                   and unit.subject_generation = $3
                   and exists (
                     select 1 from unnest($4::uuid[], $5::uuid[], $6::text[])
                       allowed(scope_id, agent_node_id, kind)
                     where allowed.scope_id = unit.scope_id
                       and allowed.agent_node_id = unit.agent_node_id
                       and allowed.kind = unit.kind
                   )
                   and ((unit.source_episode_id is not null)::int
                      + (unit.source_resource_id is not null)::int) = 1
                   and unit.deletion_generation is null
                   and unit.state <> 'deleted'
                   and unit.trust_level <> 'quarantined'
                   and (unit.state in ('active', 'validated')
                        or (unit.state = 'superseded' and unit.transaction_to is not null))
                   and coalesce(unit.transaction_from, '-infinity'::timestamptz) <= $11::timestamptz
                   and $11::timestamptz < coalesce(unit.transaction_to, 'infinity'::timestamptz)
                   and coalesce(unit.valid_from, '-infinity'::timestamptz) <= $12::timestamptz
                   and $12::timestamptz < coalesce(unit.valid_to, 'infinity'::timestamptz)
                   and not exists (
                     select 1 from memphant.forgotten_source forgotten
                     where forgotten.tenant_id = unit.tenant_id
                       and forgotten.data_subject_id = unit.data_subject_id
                       and forgotten.subject_generation = unit.subject_generation
                       and forgotten.scope_id = unit.scope_id
                       and forgotten.agent_node_id = unit.agent_node_id
                       and forgotten.source_kind = 'memory_unit'
                       and forgotten.source_id = unit.id
                   )
               ), source_row as (
                 select unit.*, 'episode'::text as deep_source_kind,
                        episode.id as deep_source_id, episode.body as deep_source_body,
                        '{}'::jsonb as deep_source_acl
                 from eligible_unit unit
                 join memphant.episode episode
                   on episode.tenant_id = unit.tenant_id
                  and episode.data_subject_id = unit.data_subject_id
                  and episode.subject_generation = unit.subject_generation
                  and episode.scope_id = unit.scope_id
                  and episode.agent_node_id = unit.agent_node_id
                  and episode.id = unit.source_episode_id
                 where exists (
                     select 1 from unnest($7::uuid[], $8::uuid[]) allowed(scope_id, agent_node_id)
                     where allowed.scope_id = episode.scope_id
                       and allowed.agent_node_id = episode.agent_node_id
                   )
                   and episode.deletion_generation is null
                   and episode.source_trust <> 'quarantined'
                   and not exists (
                     select 1 from memphant.forgotten_source forgotten
                     where forgotten.tenant_id = episode.tenant_id
                       and forgotten.data_subject_id = episode.data_subject_id
                       and forgotten.subject_generation = episode.subject_generation
                       and forgotten.scope_id = episode.scope_id
                       and forgotten.agent_node_id = episode.agent_node_id
                       and forgotten.source_kind = 'episode'
                       and forgotten.source_id = episode.id
                   )
                 union all
                 select unit.*, 'resource'::text as deep_source_kind,
                        resource.id as deep_source_id, resource.body as deep_source_body,
                        resource.acl as deep_source_acl
                 from eligible_unit unit
                 join memphant.resource resource
                   on resource.tenant_id = unit.tenant_id
                  and resource.data_subject_id = unit.data_subject_id
                  and resource.subject_generation = unit.subject_generation
                  and resource.scope_id = unit.scope_id
                  and resource.agent_node_id = unit.agent_node_id
                  and resource.id = unit.source_resource_id
                 where exists (
                     select 1 from unnest($9::uuid[], $10::uuid[]) allowed(scope_id, agent_node_id)
                     where allowed.scope_id = resource.scope_id
                       and allowed.agent_node_id = resource.agent_node_id
                   )
                   and resource.source_trust <> 'quarantined'
                   and resource.body is not null
                   and resource.acl = '{}'::jsonb
                   and not exists (
                     select 1 from memphant.forgotten_source forgotten
                     where forgotten.tenant_id = resource.tenant_id
                       and forgotten.data_subject_id = resource.data_subject_id
                       and forgotten.subject_generation = resource.subject_generation
                       and forgotten.scope_id = resource.scope_id
                       and forgotten.agent_node_id = resource.agent_node_id
                       and forgotten.source_kind = 'resource'
                       and forgotten.source_id = resource.id
                   )
               )
               select id, data_subject_id, scope_id, agent_node_id, subject_generation,
                      kind, state, fact_key, predicate, body, confidence, trust_level,
                      churn_class,
                      to_char(freshness_due_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as freshness_due_at,
                      actor_id, source_kind, source_ref,
                      to_char(observed_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as observed_at,
                      source_episode_id, source_resource_id, deletion_generation, payload,
                      to_char(valid_from at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as valid_from,
                      to_char(valid_to at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as valid_to,
                      to_char(transaction_from at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as transaction_from,
                      to_char(transaction_to at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as transaction_to,
                      difficulty, stability_days,
                      to_char(last_reinforced_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as last_reinforced_at,
                      reinforcement_count, tenant_id, deep_source_kind, deep_source_id,
                      deep_source_body, deep_source_acl
               from source_row
               order by deep_source_kind, deep_source_id, id"#,
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(unit_scopes)
        .bind(unit_agents)
        .bind(unit_kinds)
        .bind(episode_scopes)
        .bind(episode_agents)
        .bind(resource_scopes)
        .bind(resource_agents)
        .bind(&time.transaction_as_of)
        .bind(&time.valid_at)
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;

        let mut grouped: BTreeMap<(DeepSnapshotSourceKind, Uuid), (String, Vec<StoredMemoryUnit>)> =
            BTreeMap::new();
        for row in rows {
            let source_kind = match row
                .try_get::<String, _>("deep_source_kind")
                .map_err(backend)?
                .as_str()
            {
                "episode" => DeepSnapshotSourceKind::Episode,
                "resource" => DeepSnapshotSourceKind::Resource,
                other => {
                    return Err(StoreError::Backend(format!(
                        "unknown Deep source kind: {other}"
                    )));
                }
            };
            let source_id: Uuid = row.try_get("deep_source_id").map_err(backend)?;
            let source_body: String = row.try_get("deep_source_body").map_err(backend)?;
            let source_acl = serde_json::from_value::<ResourceAcl>(
                row.try_get::<serde_json::Value, _>("deep_source_acl")
                    .map_err(backend)?,
            )
            .map_err(|error| StoreError::Backend(format!("invalid resource ACL: {error}")))?;
            if source_kind == DeepSnapshotSourceKind::Resource && !source_acl.is_deep_eligible() {
                return Err(StoreError::Backend(
                    "Deep snapshot SQL returned an ACL-restricted resource".to_string(),
                ));
            }
            let unit = Self::unit_from_row(&row)?;
            if !deep_unit_is_snapshot_eligible(&unit, time) {
                return Err(StoreError::Backend(
                    "Deep snapshot SQL returned an ineligible unit".to_string(),
                ));
            }
            grouped
                .entry((source_kind, source_id))
                .or_insert_with(|| (source_body, Vec::new()))
                .1
                .push(unit);
        }

        Ok(grouped
            .into_iter()
            .map(|((source_kind, source_id), (body, mut bound_units))| {
                bound_units.sort_unstable_by_key(|unit| unit.id.as_uuid());
                let directory = match source_kind {
                    DeepSnapshotSourceKind::Episode => "episodes",
                    DeepSnapshotSourceKind::Resource => "resources",
                };
                DeepSnapshotEntry {
                    source_kind,
                    source_id,
                    path: format!("{directory}/{source_id}.md"),
                    body_sha256: format!("{:x}", Sha256::digest(body.as_bytes())),
                    body,
                    bound_units,
                }
            })
            .collect())
    }

    async fn fetch_scope_open_units(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        // ponytail: full-scope scan — the write compiler needs completeness, and
        // it's index-backed (tenant_id, scope_id) filtered to open rows. If a
        // scope ever grows large enough that per-write scans hurt, narrow to the
        // incoming candidates' fact_keys (dedup/supersede only touch those).
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        Self::fetch_scope_open_units_tx(&mut tx, context).await
    }

    async fn fetch_scope_open_units_in_tx(
        &self,
        txn: &mut Self::Txn,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        Self::fetch_scope_open_units_tx(&mut txn.tx, &txn.context).await
    }

    async fn fetch_file_sync_transition_snapshot(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<FileSyncTransitionSnapshot, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        Self::fetch_file_sync_transition_snapshot_tx(&mut tx, context).await
    }

    async fn fetch_file_sync_transition_snapshot_in_tx(
        &self,
        txn: &mut Self::Txn,
    ) -> Result<FileSyncTransitionSnapshot, StoreError> {
        Self::fetch_file_sync_transition_snapshot_tx(&mut txn.tx, &txn.context).await
    }

    async fn fetch_vector_candidates(
        &self,
        context: &ResolvedMemoryContext,
        query_vec: &[f32],
        profile_id: Uuid,
        time: &RecallTime,
        limit: usize,
    ) -> Result<Vec<(StoredMemoryUnit, f32)>, StoreError> {
        if query_vec.is_empty() {
            return Ok(Vec::new());
        }
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let (scope_uuids, agent_uuids, allowed_kind_strs) = source_kind_triples(context, &[]);
        let base = "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                    and exists (
                      select 1 from unnest($4::uuid[], $5::uuid[], $6::text[])
                        allowed(scope_id, agent_node_id, kind)
                      where allowed.scope_id = memphant.memory_unit.scope_id
                        and allowed.agent_node_id = memphant.memory_unit.agent_node_id
                        and allowed.kind = memphant.memory_unit.kind
                    )
                    and deletion_generation is null and state <> 'deleted'
                    and coalesce(transaction_from, '-infinity'::timestamptz) <= $7::timestamptz
                    and $7::timestamptz < coalesce(transaction_to, 'infinity'::timestamptz)
                    and coalesce(valid_from, '-infinity'::timestamptz) <= $8::timestamptz
                    and $8::timestamptz < coalesce(valid_to, 'infinity'::timestamptz)";
        // The embedding_profile_id predicate ($10) is mandatory (spec 03): it
        // keeps `<=>` from comparing vectors of different dimensions/models
        // across profiles, and makes the scan exact-per-profile. Query vector is
        // $9; distance rides back as `<=>` (cosine).
        //
        // ponytail: exact brute-force `<=>` scan, no ANN index. This is
        // pgvector's default (perfect recall) and the correct design here — the
        // scan runs only over ONE (tenant, subject, generation)'s units in the
        // allowed scopes. The scope/agent/kind allowlist is a non-sargable
        // EXISTS-over-unnest POST-filter, so the cost driver is a single
        // subject's footprint, NOT total table rows; `limit` caps output after
        // the sort, not the scan. Measured (384-dim, warm): p50 ~= 6ms +
        // 226ns/row; the 34ms packaged-recall SLO breaks near 50k rows local,
        // ~15-25k prod-derated. Trigger: watch max rows per (tenant, subject,
        // generation, profile) partition; warn at 10k, ship ANN at 50k (lower
        // for 1024-dim). Upgrade path is NOT a global HNSW but a per-profile
        // PARTIAL EXPRESSION index
        //   CREATE INDEX CONCURRENTLY ... USING hnsw
        //     ((vec::halfvec(N)) halfvec_cosine_ops) WHERE embedding_profile_id = $10
        // plus casting this ORDER BY to match. A global HNSW is wrong: the
        // selective pre-filter over-filters it (pgvector #721; iterative_scan
        // only softens it). `vec` is intentionally typmod-less so multi-dim profiles
        // coexist, so the cast-expression form is the only indexable path — no
        // ALTER TYPE rewrite. 1024-dim profiles TOAST (>2KB/row) and reach the
        // cliff earlier than the 384-dim default (772B/row).
        let sql = format!(
            "select unit.*, (embedding.vec <=> $9::halfvec) as vector_distance
             from ({inner}) unit
             join memphant.embedding embedding
               on embedding.tenant_id = $1
              and embedding.data_subject_id = $2
              and embedding.subject_generation = $3
              and embedding.scope_id = unit.scope_id
              and embedding.agent_node_id = unit.agent_node_id
              and embedding.memory_unit_id = unit.id
              and embedding.embedding_profile_id = $10
             order by embedding.vec <=> $9::halfvec, unit.body limit {limit}",
            inner = Self::unit_select(base, ""),
            limit = limit.min(1_000),
        );
        let rows = sqlx::query(AssertSqlSafe(sql.as_str()))
            .bind(context.tenant_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(scope_uuids)
            .bind(agent_uuids)
            .bind(allowed_kind_strs)
            .bind(&time.transaction_as_of)
            .bind(&time.valid_at)
            .bind(vec_literal(query_vec))
            .bind(profile_id)
            .fetch_all(&mut *tx)
            .await
            .map_err(backend)?;
        rows.iter()
            .map(|row| {
                let unit = Self::unit_from_row(row)?;
                let distance = row.try_get::<f64, _>("vector_distance").map_err(backend)? as f32;
                Ok((unit, distance))
            })
            .collect()
    }

    async fn fetch_units_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let uuids: Vec<Uuid> = ids.iter().map(|id| id.as_uuid()).collect();
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let (scope_uuids, agent_uuids, allowed_kind_strs) = source_kind_triples(context, &[]);
        Self::fetch_units_where(
            &mut tx,
            "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
             and id = any($4)
             and exists (
               select 1 from unnest($5::uuid[], $6::uuid[], $7::text[])
                 allowed(scope_id, agent_node_id, kind)
               where allowed.scope_id = memphant.memory_unit.scope_id
                 and allowed.agent_node_id = memphant.memory_unit.agent_node_id
                 and allowed.kind = memphant.memory_unit.kind
             )",
            "",
            vec![
                Bind::Uuid(context.tenant_id.as_uuid()),
                Bind::Uuid(context.data_subject_id.as_uuid()),
                Bind::I64(context.subject_generation as i64),
                Bind::UuidVec(uuids),
                Bind::UuidVec(scope_uuids),
                Bind::UuidVec(agent_uuids),
                Bind::TextVec(allowed_kind_strs),
            ],
        )
        .await
    }

    async fn fetch_edges(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<StoredMemoryEdge>, StoreError> {
        let allowed = self.fetch_units_by_ids(context, unit_ids).await?;
        let uuids: Vec<Uuid> = allowed.iter().map(|unit| unit.id.as_uuid()).collect();
        let scope_uuids: Vec<Uuid> = allowed.iter().map(|unit| unit.scope_id.as_uuid()).collect();
        let agent_uuids: Vec<Uuid> = allowed
            .iter()
            .map(|unit| unit.agent_node_id.as_uuid())
            .collect();
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let rows = sqlx::query(
                r#"select id, tenant_id, scope_id, src_id, dst_id, kind,
                        to_char(transaction_from at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as transaction_from,
                        to_char(transaction_to at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as transaction_to
                 from memphant.memory_edge
                 where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                   and exists (
                     select 1 from unnest($4::uuid[], $5::uuid[]) allowed(scope_id, agent_node_id)
                     where allowed.scope_id = memphant.memory_edge.scope_id
                       and allowed.agent_node_id = memphant.memory_edge.agent_node_id
                   )
                   and (src_id = any($6) or dst_id = any($6))
                   and transaction_from <= $7::timestamptz
                   and $7::timestamptz < coalesce(transaction_to, 'infinity'::timestamptz)"#,
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(scope_uuids)
        .bind(agent_uuids)
        .bind(uuids)
        .bind(&time.transaction_as_of)
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        let edges: Vec<StoredMemoryEdge> = rows
            .iter()
            .map(Self::edge_from_row)
            .collect::<Result<_, StoreError>>()?;
        let endpoint_ids: Vec<UnitId> = edges
            .iter()
            .flat_map(|edge| [edge.src_id, edge.dst_id])
            .collect();
        let authorized_ids: HashSet<UnitId> = self
            .fetch_units_by_ids(context, &endpoint_ids)
            .await?
            .into_iter()
            .map(|unit| unit.id)
            .collect();
        Ok(edges
            .into_iter()
            .filter(|edge| {
                authorized_ids.contains(&edge.src_id) && authorized_ids.contains(&edge.dst_id)
            })
            .collect())
    }

    async fn fetch_record_material(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<RecordMaterial>, StoreError> {
        let units = self.fetch_units_by_ids(context, ids).await?;
        let by_id: HashMap<UnitId, StoredMemoryUnit> = units
            .into_iter()
            .filter(|unit| memphant_core::unit_is_recallable_at(unit, time))
            .map(|unit| (unit.id, unit))
            .collect();
        let allowed_ids: Vec<UnitId> = ids
            .iter()
            .copied()
            .filter(|id| by_id.contains_key(id))
            .collect();
        let edges = self.fetch_edges(context, &allowed_ids, time).await?;
        let uuids: Vec<Uuid> = allowed_ids.iter().map(|id| id.as_uuid()).collect();
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let rows = sqlx::query(
            "select id, memory_unit_id, episode_id, resource_id, span, quote_hash
             from memphant.citation
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and memory_unit_id = any($4)",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(uuids)
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        let citations: Vec<StoredCitation> = rows
            .iter()
            .map(|row| {
                let span = row
                    .try_get::<Option<serde_json::Value>, _>("span")
                    .map_err(backend)?
                    .map(serde_json::from_value::<CitationSpan>)
                    .transpose()
                    .map_err(|error| StoreError::Backend(error.to_string()))?;
                Ok(StoredCitation {
                    id: row.try_get("id").map_err(backend)?,
                    tenant_id: context.tenant_id,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    memory_unit_id: UnitId::from_u128(
                        row.try_get::<Uuid, _>("memory_unit_id")
                            .map_err(backend)?
                            .as_u128(),
                    ),
                    episode_id: row
                        .try_get::<Option<Uuid>, _>("episode_id")
                        .map_err(backend)?
                        .map(|id| EpisodeId::from_u128(id.as_u128())),
                    resource_id: row
                        .try_get::<Option<Uuid>, _>("resource_id")
                        .map_err(backend)?
                        .map(|id| ResourceId::from_u128(id.as_u128())),
                    span,
                    quote_hash: row.try_get("quote_hash").map_err(backend)?,
                })
            })
            .collect::<Result<_, StoreError>>()?;
        Ok(allowed_ids
            .into_iter()
            .filter_map(|id| by_id.get(&id))
            .map(|unit| RecordMaterial {
                unit: unit.clone(),
                citations: citations
                    .iter()
                    .filter(|citation| citation.memory_unit_id == unit.id)
                    .cloned()
                    .collect(),
                lineage: edges
                    .iter()
                    .filter(|edge| edge.src_id == unit.id || edge.dst_id == unit.id)
                    .filter(|edge| {
                        matches!(
                            edge.kind,
                            memphant_types::MemoryEdgeKind::Supersedes
                                | memphant_types::MemoryEdgeKind::Contradicts
                                | memphant_types::MemoryEdgeKind::DerivedFrom
                                | memphant_types::MemoryEdgeKind::Cites
                        )
                    })
                    .cloned()
                    .collect(),
            })
            .collect())
    }

    async fn fetch_review_events(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
        time: &RecallTime,
    ) -> Result<Vec<ReviewEventRow>, StoreError> {
        let allowed = self.fetch_units_by_ids(context, unit_ids).await?;
        let uuids: Vec<Uuid> = allowed.iter().map(|unit| unit.id.as_uuid()).collect();
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let rows = sqlx::query(
            "select event.id, event.trace_id, event.caller_id, event.outcome,
                    to_char(event.created_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as recorded_at,
                    coalesce(array_agg(unit.memory_unit_id)
                             filter (where unit.memory_unit_id is not null
                                       and unit.memory_unit_id = any($7)), '{}') as used_ids
             from memphant.review_event event
             join memphant.retrieval_trace trace
               on trace.tenant_id = event.tenant_id and trace.id = event.trace_id
             left join memphant.review_event_unit unit
               on unit.tenant_id = event.tenant_id and unit.review_event_id = event.id
             where event.tenant_id = $1 and trace.data_subject_id = $2
               and trace.subject_generation = $3
               and trace.scope_id = $4 and trace.actor_id = $5
               and trace.agent_node_id = $6
               and event.created_at <= $8::timestamptz
             group by event.id, event.trace_id, event.caller_id, event.outcome, event.created_at
             having count(unit.memory_unit_id) = 0
                 or bool_or(unit.memory_unit_id = any($7))",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(uuids)
        .bind(&time.transaction_as_of)
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        rows.iter()
            .map(|row| {
                Ok(ReviewEventRow {
                    tenant_id: context.tenant_id,
                    trace_id: TraceId::from_u128(
                        row.try_get::<Uuid, _>("trace_id")
                            .map_err(backend)?
                            .as_u128(),
                    ),
                    caller_id: row.try_get("caller_id").map_err(backend)?,
                    used_ids: row
                        .try_get::<Vec<Uuid>, _>("used_ids")
                        .map_err(backend)?
                        .into_iter()
                        .map(|id| UnitId::from_u128(id.as_u128()))
                        .collect(),
                    outcome: enum_from_str(
                        row.try_get::<String, _>("outcome")
                            .map_err(backend)?
                            .as_str(),
                    )?,
                    recorded_at: row.try_get("recorded_at").map_err(backend)?,
                })
            })
            .collect()
    }

    async fn fetch_episodes_for_scope(
        &self,
        context: &ResolvedMemoryContext,
        limit: usize,
    ) -> Result<Vec<StoredEpisode>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let episodic_sources = context
            .sources_by_kind
            .get(&MemoryKind::Episodic)
            .cloned()
            .unwrap_or_default();
        let scope_ids: Vec<Uuid> = episodic_sources
            .iter()
            .map(|source| source.scope_id.as_uuid())
            .collect();
        let agent_ids: Vec<Uuid> = episodic_sources
            .iter()
            .map(|source| source.agent_node_id.as_uuid())
            .collect();
        let rows = sqlx::query(
            "select id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                    subject_generation, source_kind, source_ref, source_trust, dedup_key, body,
                    observation_count,
                    to_char(first_observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as first_observed_at,
                    to_char(last_observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as last_observed_at
             from memphant.episode
             where tenant_id = $1 and data_subject_id = $2
               and exists (
                 select 1 from unnest($3::uuid[], $4::uuid[])
                   allowed(scope_id, agent_node_id)
                 where allowed.scope_id = memphant.episode.scope_id
                   and allowed.agent_node_id = memphant.episode.agent_node_id
               )
               and subject_generation = $5 and deletion_generation is null
             -- `dedup_key` breaks `last_observed_at` ties (identical for every
             -- episode staged in one transaction, since it is now()) with a
             -- total, content-derived key — unique per (tenant, scope) — so the
             -- LIMIT cut selects the same episodes across a fresh-UUID re-ingest.
             order by last_observed_at desc, dedup_key limit $6",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(scope_ids)
        .bind(agent_ids)
        .bind(context.subject_generation as i64)
        // The caller's `limit` is authoritative — no silent store-side cap. The
        // `usize::MAX` sentinel (Deep recall wants the whole scope) can't
        // become a bound i64 (it wraps to -1, an invalid LIMIT), so it saturates
        // to i64::MAX = effectively LIMIT ALL. Both stores now honor the limit,
        // so Deep recall is not silently truncated on Postgres.
        .bind(i64::try_from(limit).unwrap_or(i64::MAX))
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        rows.iter().map(Self::episode_from_row).collect()
    }

    async fn pending_job_count(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<usize, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let count: i64 = sqlx::query_scalar(
            "select count(*) from memphant.job_state
             where tenant_id = $1 and data_subject_id = $2 and scope_id = $3
               and agent_node_id = $4 and subject_generation = $5
               and state in ('queued', 'running')",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.subject_generation as i64)
        .fetch_one(&mut *tx)
        .await
        .map_err(backend)?;
        Ok(count as usize)
    }

    async fn pending_worker_job_count(&self) -> Result<usize, StoreError> {
        // `security definer`, like `dead_letter_count()`. A bare count here is
        // filtered to nothing by the job_state tenant-isolation policy now that
        // the worker pool assumes `memphant_worker`, and a queue-wide count
        // that silently answers 0 turns the drain-exit check into a rubber
        // stamp (see 20260730_005_pending_worker_job_count.sql).
        let count: i64 = sqlx::query_scalar("select memphant.pending_worker_job_count()")
            .fetch_one(&self.pool)
            .await
            .map_err(backend)?;
        Ok(count as usize)
    }

    async fn fetch_episode(
        &self,
        context: &ResolvedMemoryContext,
        id: EpisodeId,
    ) -> Result<Option<StoredEpisode>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let row = sqlx::query(
            "select id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                    subject_generation, source_kind, source_ref, source_trust, dedup_key, body,
                    observation_count,
                    to_char(first_observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as first_observed_at,
                    to_char(last_observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as last_observed_at
             from memphant.episode
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and deletion_generation is null",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(id.as_uuid())
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        row.as_ref().map(Self::episode_from_row).transpose()
    }

    async fn fetch_episodes_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[EpisodeId],
    ) -> Result<Vec<StoredEpisode>, StoreError> {
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let ids = ids.iter().map(|id| id.as_uuid()).collect::<Vec<_>>();
        let rows = sqlx::query(
            "select id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                    subject_generation, source_kind, source_ref, source_trust, dedup_key, body,
                    observation_count,
                    to_char(first_observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as first_observed_at,
                    to_char(last_observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as last_observed_at
             from memphant.episode
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = any($7) and deletion_generation is null",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(ids)
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        rows.iter().map(Self::episode_from_row).collect()
    }

    async fn fetch_resource(
        &self,
        context: &ResolvedMemoryContext,
        id: ResourceId,
    ) -> Result<Option<StoredResource>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let row = sqlx::query(
            "select id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                    subject_generation, kind, uri, source_ref,
                    to_char(observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as observed_at,
                    content_hash,
                    mime_type, revision, body, source_trust, acl, extractor_state
             from memphant.resource
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6 and id = $7",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(id.as_uuid())
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        row.as_ref().map(Self::resource_from_row).transpose()
    }

    async fn fetch_resources_by_ids(
        &self,
        context: &ResolvedMemoryContext,
        ids: &[ResourceId],
    ) -> Result<Vec<StoredResource>, StoreError> {
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let ids = ids.iter().map(|id| id.as_uuid()).collect::<Vec<_>>();
        let rows = sqlx::query(
            "select id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                    subject_generation, kind, uri, source_ref,
                    to_char(observed_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') as observed_at,
                    content_hash, mime_type, revision, body, source_trust, acl, extractor_state
             from memphant.resource
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = any($7)",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(ids)
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        rows.iter().map(Self::resource_from_row).collect()
    }

    async fn stage_correction(
        &self,
        txn: &mut Self::Txn,
        correction: CorrectionWrite,
    ) -> Result<CorrectOutcome, StoreError> {
        let context = txn.context.clone();
        txn.has_subject_writes = true;
        let tx = &mut txn.tx;
        let transaction_time: String = sqlx::query_scalar(
            "select to_char(transaction_timestamp() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')",
        )
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        let old_id = correction.selector.memory_unit_id;
        // Only the OPEN generation is correctable (`transaction_to is null`).
        // With the `for update` lock this makes a repeated (or concurrent)
        // correction of the same id re-read the now-superseded row, fail the
        // predicate, and return NotFound instead of minting a second live unit
        // — the partial unique scope-subject index only masked this for
        // `semantic` kinds; resource/belief/procedural had no guard.
        let sql = Self::unit_select(
            "tenant_id = $1 and id = $2 and data_subject_id = $3
             and subject_generation = $4 and scope_id = $5 and agent_node_id = $6
             and actor_id = $7 and state <> 'deleted'
             and transaction_to is null",
            "for update",
        );
        let row = sqlx::query(AssertSqlSafe(sql.as_str()))
            .bind(context.tenant_id.as_uuid())
            .bind(old_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .bind(context.actor_id.as_uuid())
            .fetch_optional(&mut **tx)
            .await
            .map_err(backend)?
            .ok_or(StoreError::NotFound("memory_unit"))?;
        let old_unit = Self::unit_from_row(&row)?;
        let is_retroactive =
            correction.correction.valid_from.is_some() || correction.correction.valid_to.is_some();

        sqlx::query(
            "update memphant.memory_unit set state = 'superseded', transaction_to = $8::timestamptz
             where tenant_id = $1 and id = $2 and data_subject_id = $3
               and subject_generation = $4 and scope_id = $5 and agent_node_id = $6
               and actor_id = $7",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(old_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(&transaction_time)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;

        let (replacement, remainders) = correction_rectangles_with_ids(
            &old_unit,
            &correction.correction,
            &correction.source_ref,
            &correction.observed_at,
            context.actor_id,
            &transaction_time,
            &correction.unit_ids,
        )?;
        let new_id = replacement.id;
        let remainder_ids: Vec<UnitId> = remainders.iter().map(|unit| unit.id).collect();
        Self::insert_unit(tx, &replacement).await?;
        for remainder in &remainders {
            Self::insert_unit(tx, remainder).await?;
        }
        for created_id in std::iter::once(new_id).chain(remainder_ids.iter().copied()) {
            Self::insert_edge(
                tx,
                &context,
                &StoredMemoryEdge {
                    id: EdgeId::new(),
                    tenant_id: context.tenant_id,
                    scope_id: context.scope_id,
                    src_id: created_id,
                    dst_id: old_id,
                    kind: memphant_types::MemoryEdgeKind::Supersedes,
                    transaction_from: Some(transaction_time.clone()),
                    transaction_to: None,
                },
            )
            .await?;
        }

        for remainder_id in &remainder_ids {
            sqlx::query(
                "insert into memphant.embedding
                   (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                    memory_unit_id, embedding_profile_id, vec)
                 select tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                        $3, embedding_profile_id, vec
                 from memphant.embedding
                 where tenant_id = $1 and data_subject_id = $4 and subject_generation = $5
                   and scope_id = $6 and agent_node_id = $7 and memory_unit_id = $2",
            )
            .bind(context.tenant_id.as_uuid())
            .bind(old_id.as_uuid())
            .bind(remainder_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .execute(&mut **tx)
            .await
            .map_err(backend)?;
        }
        for created_id in std::iter::once(new_id).chain(remainder_ids.iter().copied()) {
            sqlx::query(
                "insert into memphant.citation
                   (id, tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                    memory_unit_id, episode_id, resource_id, span, quote_hash)
                 select gen_random_uuid(), tenant_id, data_subject_id, scope_id, agent_node_id,
                        subject_generation, $3, episode_id, resource_id, span, quote_hash
                 from memphant.citation
                 where tenant_id = $1 and data_subject_id = $4 and subject_generation = $5
                   and scope_id = $6 and agent_node_id = $7 and memory_unit_id = $2",
            )
            .bind(context.tenant_id.as_uuid())
            .bind(old_id.as_uuid())
            .bind(created_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .execute(&mut **tx)
            .await
            .map_err(backend)?;
        }

        // Expire composition-derived dependents of the superseded unit.
        sqlx::query(
            "update memphant.memory_unit set state = 'expired', transaction_to = $8::timestamptz
             where tenant_id = $1 and data_subject_id = $3 and subject_generation = $4
               and scope_id = $5 and agent_node_id = $6 and actor_id = $7
               and state <> 'deleted' and transaction_to is null
               and source_kind = 'composition'
               and id in (select src_id from memphant.memory_edge
                          where tenant_id = $1 and data_subject_id = $3
                            and subject_generation = $4 and scope_id = $5
                            and agent_node_id = $6 and kind = 'derived_from' and dst_id = $2)",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(old_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(&transaction_time)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;

        // Embed the replacement unit in the SAME transaction as its supersedes
        // edge so corrected truth is vector-visible at once (read-your-writes).
        if let Some((profile, vec)) = &correction.embedding {
            Self::upsert_embedding_profile_tx(tx, context.tenant_id, profile).await?;
            Self::insert_embedding_tx(
                tx,
                &context,
                &EmbeddingRow {
                    memory_unit_id: new_id,
                    embedding_profile_id: profile.id,
                    vec: vec.clone(),
                },
            )
            .await?;
        }

        let mut created = vec![new_id];
        created.extend(remainder_ids);
        let result = CorrectResult {
            correction_id: format!("cor_{}", new_id.as_uuid()),
            superseded: vec![old_id],
            created,
            correction_kind: if is_retroactive {
                "retroactive".to_string()
            } else {
                "current".to_string()
            },
            trace_ref: None,
        };
        Ok(result)
    }

    async fn stage_invalidation(
        &self,
        txn: &mut Self::Txn,
        invalidation: memphant_core::InvalidationWrite,
    ) -> Result<CorrectOutcome, StoreError> {
        let context = txn.context.clone();
        txn.has_subject_writes = true;
        let tx = &mut txn.tx;
        let transaction_time: String = sqlx::query_scalar(
            "select to_char(transaction_timestamp() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')",
        )
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        let old_id = invalidation.target;
        // Lock the single OPEN generation; a concurrent/repeat invalidation
        // re-reads the now-superseded row and returns NotFound.
        let sql = Self::unit_select(
            "tenant_id = $1 and id = $2 and data_subject_id = $3
             and subject_generation = $4 and scope_id = $5 and agent_node_id = $6
             and actor_id = $7 and state <> 'deleted'
             and transaction_to is null",
            "for update",
        );
        let row = sqlx::query(AssertSqlSafe(sql.as_str()))
            .bind(context.tenant_id.as_uuid())
            .bind(old_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .bind(context.actor_id.as_uuid())
            .fetch_optional(&mut **tx)
            .await
            .map_err(backend)?
            .ok_or(StoreError::NotFound("memory_unit"))?;
        let old_unit = Self::unit_from_row(&row)?;

        sqlx::query(
            "update memphant.memory_unit set state = 'superseded', transaction_to = $8::timestamptz
             where tenant_id = $1 and id = $2 and data_subject_id = $3
               and subject_generation = $4 and scope_id = $5 and agent_node_id = $6
               and actor_id = $7",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(old_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(&transaction_time)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;

        // The bodyless tombstone carries the same stable identity (kind,
        // fact_key, valid interval) but no body, chunks, compact, or embedding.
        let mut tombstone = old_unit.clone();
        tombstone.id = invalidation.tombstone_id;
        tombstone.state = memphant_types::UnitState::Invalidated;
        tombstone.body = String::new();
        tombstone.compact = None;
        tombstone.contextual_chunks = Vec::new();
        tombstone.invalidation = Some(memphant_types::InvalidationMarker {
            kind: invalidation.reason_kind,
            reason: invalidation.reason.clone(),
        });
        tombstone.source_ref = invalidation.source_ref.clone();
        tombstone.observed_at = invalidation.observed_at.clone();
        tombstone.transaction_from = Some(transaction_time.clone());
        tombstone.transaction_to = None;
        Self::insert_unit(tx, &tombstone).await?;

        Self::insert_edge(
            tx,
            &context,
            &StoredMemoryEdge {
                id: EdgeId::new(),
                tenant_id: context.tenant_id,
                scope_id: context.scope_id,
                src_id: invalidation.tombstone_id,
                dst_id: old_id,
                kind: memphant_types::MemoryEdgeKind::Supersedes,
                transaction_from: Some(transaction_time.clone()),
                transaction_to: None,
            },
        )
        .await?;

        Ok(CorrectResult {
            correction_id: format!("inv_{}", invalidation.tombstone_id.as_uuid()),
            superseded: vec![old_id],
            created: vec![invalidation.tombstone_id],
            correction_kind: "invalidation".to_string(),
            trace_ref: None,
        })
    }

    async fn stage_forget(
        &self,
        txn: &mut Self::Txn,
        forget: ForgetWrite,
    ) -> Result<ForgetOutcome, StoreError> {
        let context = txn.context.clone();
        txn.has_subject_writes = true;
        let tx = &mut txn.tx;
        let transaction_time: String = sqlx::query_scalar(
            "select to_char(transaction_timestamp() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')",
        )
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        let (source_kind, source_id) = match forget.target {
            ForgetTarget::MemoryUnit(id) => ("memory_unit", id.as_uuid()),
            ForgetTarget::Episode(id) => ("episode", id.as_uuid()),
            ForgetTarget::Resource(id) => ("resource", id.as_uuid()),
        };

        let table = match forget.target {
            ForgetTarget::MemoryUnit(_) => "memory_unit",
            ForgetTarget::Episode(_) => "episode",
            ForgetTarget::Resource(_) => "resource",
        };
        let authorized: bool = sqlx::query_scalar(AssertSqlSafe(
            format!(
                "select exists (select 1 from memphant.{table}
                 where tenant_id = $1 and id = $2 and data_subject_id = $3
                   and subject_generation = $4 and scope_id = $5 and agent_node_id = $6
                   and actor_id = $7)"
            )
            .as_str(),
        ))
        .bind(context.tenant_id.as_uuid())
        .bind(source_id)
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        if !authorized {
            return Err(StoreError::NotFound("forget target"));
        }

        // Durable tombstone: blocks re-derivation in persist_compiled_units.
        sqlx::query(
            "insert into memphant.forgotten_source
               (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                source_kind, source_id)
             values ($1, $2, $3, $4, $5, $6, $7) on conflict do nothing",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(source_kind)
        .bind(source_id)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;

        let generation: i64 = sqlx::query_scalar(
            "insert into memphant.deletion_generation
               (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation,
                requested_by, state, completed_at)
             values ($1, $2, $3, $4, $5, $6, 'completed', now()) returning id",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.actor_id.as_uuid())
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;

        if let ForgetTarget::Episode(episode_id) = forget.target {
            sqlx::query(
                "update memphant.episode set deletion_generation = $8
                 where tenant_id = $1 and id = $2 and data_subject_id = $3
                   and subject_generation = $4 and scope_id = $5 and agent_node_id = $6
                   and actor_id = $7",
            )
            .bind(context.tenant_id.as_uuid())
            .bind(episode_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .bind(context.actor_id.as_uuid())
            .bind(generation)
            .execute(&mut **tx)
            .await
            .map_err(backend)?;
        }

        let rows = match forget.target {
            ForgetTarget::MemoryUnit(_) => {
                let lineage: Vec<Uuid> = sqlx::query_scalar(
                    "with recursive lineage(id) as (
                       values ($2::uuid)
                       union
                       select neighbor.id
                       from memphant.memory_edge edge join lineage
                         on edge.src_id = lineage.id or edge.dst_id = lineage.id
                       join memphant.memory_unit neighbor
                         on neighbor.tenant_id = $1
                        and neighbor.id = case
                          when edge.src_id = lineage.id then edge.dst_id else edge.src_id end
                        and neighbor.data_subject_id = $3
                        and neighbor.subject_generation = $4
                        and neighbor.scope_id = $5 and neighbor.agent_node_id = $6
                        and neighbor.actor_id = $7
                       where edge.tenant_id = $1 and edge.data_subject_id = $3
                         and edge.subject_generation = $4 and edge.scope_id = $5
                         and edge.agent_node_id = $6 and edge.kind = 'supersedes'
                     ) select id from lineage",
                )
                .bind(context.tenant_id.as_uuid())
                .bind(source_id)
                .bind(context.data_subject_id.as_uuid())
                .bind(context.subject_generation as i64)
                .bind(context.scope_id.as_uuid())
                .bind(context.agent_node_id.as_uuid())
                .bind(context.actor_id.as_uuid())
                .fetch_all(&mut **tx)
                .await
                .map_err(backend)?;
                sqlx::query(
                    "update memphant.memory_unit
                     set state = 'deleted', deletion_generation = $8,
                         transaction_to = $9::timestamptz
                     where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                       and scope_id = $4 and agent_node_id = $5 and actor_id = $6
                       and state <> 'deleted' and id = any($7)
                     returning id",
                )
                .bind(context.tenant_id.as_uuid())
                .bind(context.data_subject_id.as_uuid())
                .bind(context.subject_generation as i64)
                .bind(context.scope_id.as_uuid())
                .bind(context.agent_node_id.as_uuid())
                .bind(context.actor_id.as_uuid())
                .bind(lineage)
                .bind(generation)
                .bind(&transaction_time)
                .fetch_all(&mut **tx)
                .await
                .map_err(backend)?
            }
            target => {
                let column = match target {
                    ForgetTarget::Episode(_) => "source_episode_id",
                    ForgetTarget::Resource(_) => "source_resource_id",
                    ForgetTarget::MemoryUnit(_) => unreachable!(),
                };
                // Cascade from the source-column matches to supersedes
                // DESCENDANTS: a correction replacement carries correction
                // provenance (source_episode_id = null, pinned by
                // correction_provenance.rs), so without the lineage walk the
                // corrected content survives the episode's erasure. Descendants
                // only (dst -> src) — ancestors came from other sources.
                sqlx::query(AssertSqlSafe(
                    format!(
                        "with recursive lineage(id) as (
                           select id from memphant.memory_unit
                           where tenant_id = $1 and data_subject_id = $2
                             and subject_generation = $3 and scope_id = $4
                             and agent_node_id = $5 and actor_id = $6
                             and {column} = $7
                           union
                           select child.id
                           from memphant.memory_edge edge join lineage
                             on edge.dst_id = lineage.id
                           join memphant.memory_unit child
                             on child.tenant_id = $1 and child.id = edge.src_id
                            and child.data_subject_id = $2
                            and child.subject_generation = $3
                            and child.scope_id = $4 and child.agent_node_id = $5
                            and child.actor_id = $6
                           where edge.tenant_id = $1 and edge.data_subject_id = $2
                             and edge.subject_generation = $3 and edge.scope_id = $4
                             and edge.agent_node_id = $5 and edge.kind = 'supersedes'
                         )
                         update memphant.memory_unit
                         set state = 'deleted', deletion_generation = $8,
                             transaction_to = $9::timestamptz
                         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                           and scope_id = $4 and agent_node_id = $5 and actor_id = $6
                           and state <> 'deleted' and id in (select id from lineage)
                         returning id"
                    )
                    .as_str(),
                ))
                .bind(context.tenant_id.as_uuid())
                .bind(context.data_subject_id.as_uuid())
                .bind(context.subject_generation as i64)
                .bind(context.scope_id.as_uuid())
                .bind(context.agent_node_id.as_uuid())
                .bind(context.actor_id.as_uuid())
                .bind(source_id)
                .bind(generation)
                .bind(&transaction_time)
                .fetch_all(&mut **tx)
                .await
                .map_err(backend)?
            }
        };
        let mut invalidated: Vec<UnitId> = rows
            .iter()
            .map(|row| {
                Ok::<_, StoreError>(UnitId::from_u128(
                    row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128(),
                ))
            })
            .collect::<Result<_, _>>()?;

        let invalidated_uuids: Vec<Uuid> = invalidated.iter().map(|id| id.as_uuid()).collect();
        invalidated.extend(
            Self::delete_composed_dependents(
                tx,
                &context,
                &invalidated_uuids,
                generation,
                &transaction_time,
            )
            .await?,
        );

        // Forgotten embeddings are hard-deleted with their units.
        let all_uuids: Vec<Uuid> = invalidated.iter().map(|id| id.as_uuid()).collect();
        sqlx::query(
            "delete from memphant.embedding
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and memory_unit_id = any($6)",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(all_uuids)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;

        let outcome = ForgetOutcome {
            deletion_generation: generation as u64,
            invalidated_units: invalidated,
        };
        Ok(outcome)
    }

    async fn stage_review_events(
        &self,
        txn: &mut Self::Txn,
        events: Vec<ReviewEventRow>,
    ) -> Result<(), StoreError> {
        let context = txn.context.clone();
        txn.has_subject_writes = true;
        let tx = &mut txn.tx;
        for event in events {
            if event.tenant_id != context.tenant_id {
                return Err(StoreError::NotFound("retrieval trace review context"));
            }
            let authorized = sqlx::query_scalar::<_, bool>(
                "select exists (
                   select 1 from memphant.retrieval_trace trace
                   where trace.tenant_id = $1 and trace.data_subject_id = $2
                     and trace.subject_generation = $3 and trace.scope_id = $4
                     and trace.actor_id = $5 and trace.agent_node_id = $6
                     and trace.id = $7
                     and not exists (
                       select 1 from unnest($8::uuid[]) requested(id)
                       where not exists (
                         select 1 from jsonb_array_elements(coalesce(trace.trace->'context_items', '[]'::jsonb)) item
                         where item->>'unit_id' = requested.id::text
                            or coalesce(item->'derived_from_unit_ids', '[]'::jsonb) ? requested.id::text
                       )
                     )
                 )",
            )
            .bind(context.tenant_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.actor_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .bind(event.trace_id.as_uuid())
            .bind(event.used_ids.iter().map(|id| id.as_uuid()).collect::<Vec<_>>())
            .fetch_one(&mut **tx)
            .await
            .map_err(backend)?;
            if !authorized {
                return Err(StoreError::NotFound("retrieval trace review whitelist"));
            }
            let inserted: Option<Uuid> = sqlx::query_scalar(
                "insert into memphant.review_event
                   (tenant_id, data_subject_id, subject_generation, scope_id,
                    actor_id, agent_node_id, trace_id, caller_id, outcome, created_at)
                 values ($1, $2, $3, $4, $5, $6, $7, $8, $9, transaction_timestamp())
                 on conflict (tenant_id, trace_id, caller_id) do nothing
                 returning id",
            )
            .bind(context.tenant_id.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.actor_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .bind(event.trace_id.as_uuid())
            .bind(&event.caller_id)
            .bind(enum_str(&event.outcome))
            .fetch_optional(&mut **tx)
            .await
            .map_err(backend)?;
            if let Some(event_id) = inserted {
                for unit_id in &event.used_ids {
                    sqlx::query(
                        "insert into memphant.review_event_unit
                           (review_event_id, tenant_id, data_subject_id, subject_generation,
                            scope_id, actor_id, agent_node_id, memory_unit_id)
                         values ($1, $2, $3, $4, $5, $6, $7, $8) on conflict do nothing",
                    )
                    .bind(event_id)
                    .bind(context.tenant_id.as_uuid())
                    .bind(context.data_subject_id.as_uuid())
                    .bind(context.subject_generation as i64)
                    .bind(context.scope_id.as_uuid())
                    .bind(context.actor_id.as_uuid())
                    .bind(context.agent_node_id.as_uuid())
                    .bind(unit_id.as_uuid())
                    .execute(&mut **tx)
                    .await
                    .map_err(backend)?;
                }
            }
        }
        Ok(())
    }

    async fn store_trace(
        &self,
        context: &ResolvedMemoryContext,
        trace: RetrievalTrace,
    ) -> Result<(), StoreError> {
        if trace.tenant_id != context.tenant_id
            || trace.data_subject_id != context.data_subject_id
            || trace.subject_generation != context.subject_generation
            || trace.scope_id != context.scope_id
            || trace.actor_id != context.actor_id
            || trace.agent_node_id != context.agent_node_id
            || trace.policy_revision != context.policy_revision
        {
            return Err(StoreError::Conflict("trace context mismatch".to_string()));
        }
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let document =
            serde_json::to_value(&trace).map_err(|error| StoreError::Backend(error.to_string()))?;
        let result = sqlx::query(
            "insert into memphant.retrieval_trace
               (id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                subject_generation, policy_revision,
                query_hash, mode, channels, candidates, dropped,
                citations, filter_selectivity, consolidation_lag_ms, config_hash, trace)
             values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                     $14, $15, $16, $17, $18)
             on conflict (tenant_id, id) do update set
               query_hash = excluded.query_hash,
               mode = excluded.mode,
               channels = excluded.channels,
               candidates = excluded.candidates,
               dropped = excluded.dropped,
               citations = excluded.citations,
               filter_selectivity = excluded.filter_selectivity,
               consolidation_lag_ms = excluded.consolidation_lag_ms,
               config_hash = excluded.config_hash,
               trace = excluded.trace,
               updated_at = now()
             where retrieval_trace.data_subject_id = excluded.data_subject_id
               and retrieval_trace.scope_id = excluded.scope_id
               and retrieval_trace.actor_id = excluded.actor_id
               and retrieval_trace.agent_node_id = excluded.agent_node_id
               and retrieval_trace.subject_generation = excluded.subject_generation
               and retrieval_trace.policy_revision = excluded.policy_revision",
        )
        .bind(trace.id.as_uuid())
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(trace.scope_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(&context.policy_revision)
        .bind(&trace.query_hash)
        .bind(enum_str(&trace.mode_executed))
        .bind(serde_json::to_value(&trace.channel_runs).unwrap_or_default())
        .bind(serde_json::to_value(&trace.candidates).unwrap_or_default())
        .bind(serde_json::to_value(&trace.dropped_items).unwrap_or_default())
        .bind(serde_json::to_value(&trace.citations).unwrap_or_default())
        .bind(trace.filter_selectivity)
        .bind(trace.consolidation_lag_ms as i64)
        .bind(&trace.engine_version)
        .bind(document)
        .execute(&mut *tx)
        .await
        .map_err(backend)?;
        if result.rows_affected() != 1 {
            return Err(StoreError::Conflict("trace context mismatch".to_string()));
        }
        tx.commit().await.map_err(backend)
    }

    async fn trace_by_id(
        &self,
        context: &ResolvedMemoryContext,
        id: TraceId,
    ) -> Result<Option<RetrievalTrace>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let document: Option<serde_json::Value> = sqlx::query_scalar(
            "select trace from memphant.retrieval_trace
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and actor_id = $5 and agent_node_id = $6 and id = $7",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(id.as_uuid())
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        document
            .map(serde_json::from_value)
            .transpose()
            .map_err(|error| StoreError::Backend(error.to_string()))
    }

    async fn scope_memory_page(
        &self,
        context: &ResolvedMemoryContext,
        cursor: Option<UnitId>,
        limit: usize,
    ) -> Result<ScopePage, StoreError> {
        let limit = limit.clamp(1, 1_000);
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let (scope_uuids, agent_uuids, allowed_kind_strs) = source_kind_triples(context, &[]);
        let fetched = Self::fetch_units_where(
            &mut tx,
            "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
             and exists (
               select 1 from unnest($4::uuid[], $5::uuid[], $6::text[])
                 allowed(scope_id, agent_node_id, kind)
               where allowed.scope_id = memphant.memory_unit.scope_id
                 and allowed.agent_node_id = memphant.memory_unit.agent_node_id
                 and allowed.kind = memphant.memory_unit.kind
             )
             and scope_id = $7 and agent_node_id = $8
             and ($9::uuid is null or id > $9)",
            "order by id limit $10",
            vec![
                Bind::Uuid(context.tenant_id.as_uuid()),
                Bind::Uuid(context.data_subject_id.as_uuid()),
                Bind::I64(context.subject_generation as i64),
                Bind::UuidVec(scope_uuids),
                Bind::UuidVec(agent_uuids),
                Bind::TextVec(allowed_kind_strs),
                Bind::Uuid(context.scope_id.as_uuid()),
                Bind::Uuid(context.agent_node_id.as_uuid()),
                Bind::UuidOpt(cursor.map(|cursor| cursor.as_uuid())),
                Bind::I64((limit + 1) as i64),
            ],
        )
        .await?;
        let has_more = fetched.len() > limit;
        let mut items = fetched;
        items.truncate(limit);
        let next_cursor = has_more.then(|| items.last().map(|unit| unit.id)).flatten();
        Ok(ScopePage {
            items,
            next_cursor,
            has_more,
        })
    }

    async fn canonical_projection_units(
        &self,
        context: &ResolvedMemoryContext,
        evaluated_at: &str,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let kinds = [MemoryKind::Semantic, MemoryKind::Procedural]
            .into_iter()
            .filter(|kind| context.allows(*kind, context.scope_id, context.agent_node_id))
            .map(|kind| enum_str(&kind).to_string())
            .collect::<Vec<_>>();
        Self::fetch_units_where(
            &mut tx,
            "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
             and scope_id = $4 and agent_node_id = $5 and actor_id = $6
             and deletion_generation is null and trust_level <> 'quarantined'
             and (transaction_from is null or transaction_from <= $7::timestamptz)
             and (transaction_to is null or $7::timestamptz < transaction_to)
             and (valid_from is null or valid_from <= $7::timestamptz)
             and (valid_to is null or $7::timestamptz < valid_to)
             and kind = any($9)
             and (
               (kind = 'semantic' and state = any($8))
               or (kind = 'procedural' and state = 'validated')
             )",
            "order by id",
            vec![
                Bind::Uuid(context.tenant_id.as_uuid()),
                Bind::Uuid(context.data_subject_id.as_uuid()),
                Bind::I64(context.subject_generation as i64),
                Bind::Uuid(context.scope_id.as_uuid()),
                Bind::Uuid(context.agent_node_id.as_uuid()),
                Bind::Uuid(context.actor_id.as_uuid()),
                Bind::Text(evaluated_at.to_string()),
                Bind::TextVec(vec!["active".to_string(), "validated".to_string()]),
                Bind::TextVec(kinds),
            ],
        )
        .await
    }

    async fn canonical_projection_units_in_tx(
        &self,
        txn: &mut Self::Txn,
        evaluated_at: &str,
    ) -> Result<Vec<StoredMemoryUnit>, StoreError> {
        let context = &txn.context;
        let kinds = [MemoryKind::Semantic, MemoryKind::Procedural]
            .into_iter()
            .filter(|kind| context.allows(*kind, context.scope_id, context.agent_node_id))
            .map(|kind| enum_str(&kind).to_string())
            .collect::<Vec<_>>();
        Self::fetch_units_where(
            &mut txn.tx,
            "tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
             and scope_id = $4 and agent_node_id = $5 and actor_id = $6
             and deletion_generation is null and trust_level <> 'quarantined'
             and (transaction_from is null or transaction_from <= $7::timestamptz)
             and (transaction_to is null or $7::timestamptz < transaction_to)
             and (valid_from is null or valid_from <= $7::timestamptz)
             and (valid_to is null or $7::timestamptz < valid_to)
             and kind = any($9)
             and (
               (kind = 'semantic' and state = any($8))
               or (kind = 'procedural' and state = 'validated')
             )",
            "order by id",
            vec![
                Bind::Uuid(context.tenant_id.as_uuid()),
                Bind::Uuid(context.data_subject_id.as_uuid()),
                Bind::I64(context.subject_generation as i64),
                Bind::Uuid(context.scope_id.as_uuid()),
                Bind::Uuid(context.agent_node_id.as_uuid()),
                Bind::Uuid(context.actor_id.as_uuid()),
                Bind::Text(evaluated_at.to_string()),
                Bind::TextVec(vec!["active".to_string(), "validated".to_string()]),
                Bind::TextVec(kinds),
            ],
        )
        .await
    }

    async fn claim_reflect_jobs(
        &self,
        filter: JobFilter,
        limit: usize,
    ) -> Result<Vec<ReflectJobRow>, StoreError> {
        let rows = sqlx::query(
            "select id, tenant_id, data_subject_id, scope_id, actor_id, agent_node_id,
                    subject_generation, job_type, target_id, compiler_version,
                    subject, predicate, attempts, claim_generation, queue_order
             from memphant.claim_reflect_jobs($1, $2, $3, $4)
             order by queue_order",
        )
        .bind(limit.min(1_000) as i32)
        .bind(filter.tenant.map(|tenant| tenant.as_uuid()))
        .bind(filter.scope.map(|scope| scope.as_uuid()))
        .bind(JOB_DEAD_LETTER_ATTEMPTS as i32)
        .fetch_all(&self.pool)
        .await
        .map_err(backend)?;

        rows.iter()
            .map(|row| {
                let job_type: String = row.try_get("job_type").map_err(backend)?;
                let target: Uuid = row.try_get("target_id").map_err(backend)?;
                let (kind, episode_id, resource_id) = match job_type.as_str() {
                    "reflect_episode" => (
                        ReflectJobKind::ReflectEpisode,
                        Some(EpisodeId::from_u128(target.as_u128())),
                        None,
                    ),
                    "reflect_resource" => (
                        ReflectJobKind::ReflectResource,
                        None,
                        Some(ResourceId::from_u128(target.as_u128())),
                    ),
                    "reflect_scope"
                        if target == row.try_get::<Uuid, _>("id").map_err(backend)? =>
                    {
                        (ReflectJobKind::ReflectScope, None, None)
                    }
                    other => {
                        return Err(StoreError::Backend(format!(
                            "invalid persisted reflect job type or target: {other}"
                        )));
                    }
                };
                Ok(ReflectJobRow {
                    job: QueuedReflectJob {
                        id: JobId::from_u128(
                            row.try_get::<Uuid, _>("id").map_err(backend)?.as_u128(),
                        ),
                        tenant_id: TenantId::from_u128(
                            row.try_get::<Uuid, _>("tenant_id")
                                .map_err(backend)?
                                .as_u128(),
                        ),
                        data_subject_id: SubjectId::from_u128(
                            row.try_get::<Uuid, _>("data_subject_id")
                                .map_err(backend)?
                                .as_u128(),
                        ),
                        scope_id: ScopeId::from_u128(
                            row.try_get::<Uuid, _>("scope_id")
                                .map_err(backend)?
                                .as_u128(),
                        ),
                        actor_id: ActorId::from_u128(
                            row.try_get::<Uuid, _>("actor_id")
                                .map_err(backend)?
                                .as_u128(),
                        ),
                        agent_node_id: AgentNodeId::from_u128(
                            row.try_get::<Uuid, _>("agent_node_id")
                                .map_err(backend)?
                                .as_u128(),
                        ),
                        subject_generation: row
                            .try_get::<i64, _>("subject_generation")
                            .map_err(backend)? as u64,
                        episode_id,
                        resource_id,
                        kind,
                        compiler_version: row.try_get("compiler_version").map_err(backend)?,
                        subject: row.try_get("subject").map_err(backend)?,
                        predicate: row.try_get("predicate").map_err(backend)?,
                    },
                    attempts: row.try_get::<i32, _>("attempts").map_err(backend)? as u32,
                    claim_generation: row.try_get::<i64, _>("claim_generation").map_err(backend)?
                        as u64,
                })
            })
            .collect()
    }

    async fn complete_reflect_job(
        &self,
        claim: &ReflectJobRow,
    ) -> Result<ClaimMutationOutcome, StoreError> {
        let tenant = claim.job.tenant_id;
        let mut tx = self.tenant_tx(tenant).await?;
        let updated = sqlx::query(
            "update memphant.job_state set state = 'done'
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8 and attempts = $9
               and claim_generation = $10 and state = 'running'",
        )
        .bind(tenant.as_uuid())
        .bind(claim.job.data_subject_id.as_uuid())
        .bind(claim.job.subject_generation as i64)
        .bind(claim.job.scope_id.as_uuid())
        .bind(claim.job.agent_node_id.as_uuid())
        .bind(claim.job.actor_id.as_uuid())
        .bind(claim.job.id.as_uuid())
        .bind(&claim.job.compiler_version)
        .bind(claim.attempts as i32)
        .bind(claim.claim_generation as i64)
        .execute(&mut *tx)
        .await
        .map_err(backend)?;
        let applied = updated.rows_affected() == 1
            || sqlx::query_scalar::<_, bool>(
                "select exists (
                   select 1 from memphant.job_state
                   where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                     and scope_id = $4 and agent_node_id = $5 and actor_id = $6
                     and id = $7 and compiler_version = $8 and attempts = $9
                     and claim_generation = $10 and state = 'done' and result is not null
                 )",
            )
            .bind(tenant.as_uuid())
            .bind(claim.job.data_subject_id.as_uuid())
            .bind(claim.job.subject_generation as i64)
            .bind(claim.job.scope_id.as_uuid())
            .bind(claim.job.agent_node_id.as_uuid())
            .bind(claim.job.actor_id.as_uuid())
            .bind(claim.job.id.as_uuid())
            .bind(&claim.job.compiler_version)
            .bind(claim.attempts as i32)
            .bind(claim.claim_generation as i64)
            .fetch_one(&mut *tx)
            .await
            .map_err(backend)?;
        tx.commit().await.map_err(backend)?;
        Ok(if applied {
            ClaimMutationOutcome::Applied
        } else {
            ClaimMutationOutcome::Stale
        })
    }

    async fn requeue_reflect_job_with_compiler(
        &self,
        claim: &ReflectJobRow,
        compiler_version: &str,
    ) -> Result<ClaimMutationOutcome, StoreError> {
        if compiler_version.trim().is_empty() {
            return Err(StoreError::Conflict(
                "reflect compiler identity must not be empty".to_string(),
            ));
        }
        let tenant = claim.job.tenant_id;
        let mut tx = self.tenant_tx(tenant).await?;
        let source: Option<(String, Uuid, i64)> = sqlx::query_as(
            "select job_type, target_id, queue_order from memphant.job_state
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8 and attempts = $9
               and claim_generation = $10 and state = 'running'
             for update",
        )
        .bind(tenant.as_uuid())
        .bind(claim.job.data_subject_id.as_uuid())
        .bind(claim.job.subject_generation as i64)
        .bind(claim.job.scope_id.as_uuid())
        .bind(claim.job.agent_node_id.as_uuid())
        .bind(claim.job.actor_id.as_uuid())
        .bind(claim.job.id.as_uuid())
        .bind(&claim.job.compiler_version)
        .bind(claim.attempts as i32)
        .bind(claim.claim_generation as i64)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        let Some((job_type, target_id, queue_order)) = source else {
            tx.commit().await.map_err(backend)?;
            return Ok(ClaimMutationOutcome::Stale);
        };
        let authoritative_id: Option<Uuid> = sqlx::query_scalar(
            "select id from memphant.job_state
               where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
                 and scope_id = $4 and agent_node_id = $5 and actor_id = $6
                 and job_type = $7 and target_id = $8 and compiler_version = $9 and id <> $10
               order by queue_order limit 1",
        )
        .bind(tenant.as_uuid())
        .bind(claim.job.data_subject_id.as_uuid())
        .bind(claim.job.subject_generation as i64)
        .bind(claim.job.scope_id.as_uuid())
        .bind(claim.job.agent_node_id.as_uuid())
        .bind(claim.job.actor_id.as_uuid())
        .bind(&job_type)
        .bind(target_id)
        .bind(compiler_version)
        .bind(claim.job.id.as_uuid())
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        if let Some(authoritative_id) = authoritative_id {
            sqlx::query(
                "update memphant.job_state set queue_order = least(queue_order, $3)
                 where tenant_id = $1 and id = $2",
            )
            .bind(tenant.as_uuid())
            .bind(authoritative_id)
            .bind(queue_order)
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
            sqlx::query(
                "update memphant.job_state
                 set state = 'dead', claimed_at = null, result = null,
                     claim_generation = claim_generation + 1,
                     last_error = 'stale compiler identity', updated_at = now()
                 where tenant_id = $1 and id = $2",
            )
            .bind(tenant.as_uuid())
            .bind(claim.job.id.as_uuid())
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        } else {
            sqlx::query(
                "update memphant.job_state
                 set compiler_version = $3, state = 'queued', claimed_at = null,
                     run_after = now(), attempts = 0,
                     claim_generation = claim_generation + 1, result = null,
                     last_error = 'stale compiler identity', updated_at = now()
                 where tenant_id = $1 and id = $2",
            )
            .bind(tenant.as_uuid())
            .bind(claim.job.id.as_uuid())
            .bind(compiler_version)
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        }
        tx.commit().await.map_err(backend)?;
        Ok(ClaimMutationOutcome::Applied)
    }

    async fn fetch_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
        expected_batches: &[memphant_core::StructuredStateRequest],
        provider_identity: &memphant_core::StructuredStateProviderIdentity,
    ) -> Result<Option<Vec<memphant_core::StructuredExtractionPacket>>, StoreError> {
        let tenant = claim.job.tenant_id;
        let mut tx = self.tenant_tx(tenant).await?;
        // `queued` is included so a valid release does not lose the paid
        // preparation: the releasing owner's token still matches on attempts +
        // claim_generation (both bump on reclaim, so stale/forged tokens miss).
        let value: Option<serde_json::Value> = sqlx::query_scalar(
            "select result from memphant.job_state
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8 and attempts = $9
               and claim_generation = $10 and state in ('running', 'queued')",
        )
        .bind(tenant.as_uuid())
        .bind(claim.job.data_subject_id.as_uuid())
        .bind(claim.job.subject_generation as i64)
        .bind(claim.job.scope_id.as_uuid())
        .bind(claim.job.agent_node_id.as_uuid())
        .bind(claim.job.actor_id.as_uuid())
        .bind(claim.job.id.as_uuid())
        .bind(&claim.job.compiler_version)
        .bind(claim.attempts as i32)
        .bind(claim.claim_generation as i64)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?
        .flatten();
        tx.commit().await.map_err(backend)?;
        match value
            .map(serde_json::from_value)
            .transpose()
            .map_err(|error| StoreError::Backend(format!("invalid reflect job result: {error}")))?
        {
            Some(memphant_core::ReflectJobResult::Prepared {
                input_manifest_sha256,
                extraction_packets,
            }) => {
                memphant_core::validate_prepared_structured_state_binding(
                    expected_batches,
                    provider_identity,
                    &input_manifest_sha256,
                    &extraction_packets,
                )?;
                Ok(Some(extraction_packets))
            }
            Some(memphant_core::ReflectJobResult::Completed { .. }) | None => Ok(None),
        }
    }

    async fn store_prepared_structured_state(
        &self,
        claim: &ReflectJobRow,
        expected_batches: &[memphant_core::StructuredStateRequest],
        provider_identity: &memphant_core::StructuredStateProviderIdentity,
        extraction_packets: Vec<memphant_core::StructuredExtractionPacket>,
    ) -> Result<(), StoreError> {
        let input_manifest_sha256 =
            memphant_core::structured_input_manifest_sha256(expected_batches)?;
        memphant_core::validate_prepared_structured_state_binding(
            expected_batches,
            provider_identity,
            &input_manifest_sha256,
            &extraction_packets,
        )?;
        let value = serde_json::to_value(memphant_core::ReflectJobResult::Prepared {
            input_manifest_sha256,
            extraction_packets,
        })
        .map_err(|error| StoreError::Backend(error.to_string()))?;
        let tenant = claim.job.tenant_id;
        let mut tx = self.tenant_tx(tenant).await?;
        sqlx::query(
            "update memphant.job_state set result = $11, updated_at = now()
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8 and attempts = $9
               and claim_generation = $10
               and state = 'running' and result is null",
        )
        .bind(tenant.as_uuid())
        .bind(claim.job.data_subject_id.as_uuid())
        .bind(claim.job.subject_generation as i64)
        .bind(claim.job.scope_id.as_uuid())
        .bind(claim.job.agent_node_id.as_uuid())
        .bind(claim.job.actor_id.as_uuid())
        .bind(claim.job.id.as_uuid())
        .bind(&claim.job.compiler_version)
        .bind(claim.attempts as i32)
        .bind(claim.claim_generation as i64)
        .bind(value)
        .execute(&mut *tx)
        .await
        .map_err(backend)?;
        tx.commit().await.map_err(backend)
    }

    async fn release_reflect_job(
        &self,
        claim: &ReflectJobRow,
        retry_after_seconds: u64,
        error: String,
    ) -> Result<(), StoreError> {
        let tenant = claim.job.tenant_id;
        let mut tx = self.tenant_tx(tenant).await?;
        sqlx::query(
            "update memphant.job_state
             set state = 'queued', claimed_at = null,
                 run_after = now() + make_interval(secs => $11::double precision),
                 last_error = $12, updated_at = now()
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8 and attempts = $9
               and claim_generation = $10 and state = 'running'",
        )
        .bind(tenant.as_uuid())
        .bind(claim.job.data_subject_id.as_uuid())
        .bind(claim.job.subject_generation as i64)
        .bind(claim.job.scope_id.as_uuid())
        .bind(claim.job.agent_node_id.as_uuid())
        .bind(claim.job.actor_id.as_uuid())
        .bind(claim.job.id.as_uuid())
        .bind(&claim.job.compiler_version)
        .bind(claim.attempts as i32)
        .bind(claim.claim_generation as i64)
        .bind(retry_after_seconds as f64)
        .bind(error)
        .execute(&mut *tx)
        .await
        .map_err(backend)?;
        tx.commit().await.map_err(backend)
    }

    async fn fail_reflect_job(
        &self,
        claim: &ReflectJobRow,
        error: String,
    ) -> Result<(), StoreError> {
        let tenant = claim.job.tenant_id;
        let mut tx = self.tenant_tx(tenant).await?;
        sqlx::query(
            "update memphant.job_state
             set state = 'dead', claimed_at = null, last_error = $11, updated_at = now()
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8 and attempts = $9
               and claim_generation = $10 and state = 'running'",
        )
        .bind(tenant.as_uuid())
        .bind(claim.job.data_subject_id.as_uuid())
        .bind(claim.job.subject_generation as i64)
        .bind(claim.job.scope_id.as_uuid())
        .bind(claim.job.agent_node_id.as_uuid())
        .bind(claim.job.actor_id.as_uuid())
        .bind(claim.job.id.as_uuid())
        .bind(&claim.job.compiler_version)
        .bind(claim.attempts as i32)
        .bind(claim.claim_generation as i64)
        .bind(error)
        .execute(&mut *tx)
        .await
        .map_err(backend)?;
        tx.commit().await.map_err(backend)
    }

    async fn stage_compiled_units(
        &self,
        txn: &mut Self::Txn,
        claim: Option<&ReflectJobRow>,
        write: CompiledWrite,
    ) -> Result<ClaimMutationOutcome, StoreError> {
        let context = txn.context.clone();
        txn.has_subject_writes = true;
        if let Some(claim) = claim
            && (claim.job.tenant_id != context.tenant_id
                || claim.job.data_subject_id != context.data_subject_id
                || claim.job.subject_generation != context.subject_generation
                || claim.job.scope_id != context.scope_id
                || claim.job.agent_node_id != context.agent_node_id
                || claim.job.actor_id != context.actor_id
                || claim.job.id != write.job_id
                || claim.job.compiler_version != write.compiler_version)
        {
            return Err(StoreError::Conflict(
                "reflect claim does not match memory context".to_string(),
            ));
        }
        if write.new_units.iter().any(|unit| {
            unit.tenant_id != context.tenant_id
                || unit.data_subject_id != context.data_subject_id
                || unit.subject_generation != context.subject_generation
                || unit.scope_id != context.scope_id
                || unit.agent_node_id != context.agent_node_id
                || unit.actor_id != Some(context.actor_id)
        }) || write
            .new_edges
            .iter()
            .any(|edge| edge.tenant_id != context.tenant_id || edge.scope_id != context.scope_id)
        {
            return Err(StoreError::Conflict(
                "compiled output does not match memory context".to_string(),
            ));
        }
        let tenant = context.tenant_id;
        let tx = &mut txn.tx;
        if claim.is_none() {
            sqlx::query(
                "insert into memphant.job_state
                   (id, tenant_id, data_subject_id, actor_id, agent_node_id, subject_generation,
                    job_type, target_id, compiler_version, state, scope_id)
                 values ($1, $2, $3, $4, $5, $6, 'direct', $1, $7, 'running', $8)
                 on conflict do nothing",
            )
            .bind(write.job_id.as_uuid())
            .bind(tenant.as_uuid())
            .bind(context.data_subject_id.as_uuid())
            .bind(context.actor_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(&write.compiler_version)
            .bind(context.scope_id.as_uuid())
            .execute(&mut **tx)
            .await
            .map_err(backend)?;
        }
        let transaction_time: String = sqlx::query_scalar(
            "select to_char(transaction_timestamp() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')",
        )
        .fetch_one(&mut **tx)
        .await
        .map_err(backend)?;
        // Idempotency: one compilation per (job_id, compiler_version). Lock the
        // job_state row (`for update`) so a reclaimed re-compile serializes HERE
        // rather than racing: a second writer blocks until the first commits,
        // then sees `result` set and no-ops. Without the lock both writers pass
        // this check at READ COMMITTED and each inserts — semantic units roll
        // back on the partial unique scope-subject index, but resource-kind
        // units have no such guard and would double-insert. The row may not
        // exist yet (direct writes mint it below), so the lock is best-effort;
        // that path is single-writer per job_id.
        let existing = sqlx::query_as::<_, (Option<serde_json::Value>, i32, String, i64, String)>(
            "select result, attempts, state, claim_generation, job_type from memphant.job_state
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8
             for update",
        )
        .bind(tenant.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(write.job_id.as_uuid())
        .bind(&write.compiler_version)
        .fetch_optional(&mut **tx)
        .await
        .map_err(backend)?;
        if existing.is_none() {
            return Err(StoreError::Conflict(
                "reflect job identity belongs to another memory context".to_string(),
            ));
        }
        if claim.is_none() && existing.as_ref().is_some_and(|row| row.4 != "direct") {
            return Err(StoreError::Conflict(
                "direct compilation id belongs to a worker job".to_string(),
            ));
        }
        if let Some(expected_claim) = claim
            && existing
                .as_ref()
                .is_none_or(|(_, attempts, state, generation, _)| {
                    *attempts != expected_claim.attempts as i32
                        || *generation != expected_claim.claim_generation as i64
                        || state != "running"
                })
        {
            return Ok(ClaimMutationOutcome::Stale);
        }
        if let Some(value) = existing.and_then(|(result, _, _, _, _)| result) {
            let result: memphant_core::ReflectJobResult =
                serde_json::from_value(value).map_err(|error| {
                    StoreError::Backend(format!("invalid reflect job result: {error}"))
                })?;
            if matches!(result, memphant_core::ReflectJobResult::Completed { .. }) {
                return Ok(ClaimMutationOutcome::Applied);
            }
        }

        // Apply state transitions BEFORE inserts so the partial unique
        // scope-subject index never sees two open semantic generations.
        for update in &write.unit_updates {
            let updated = sqlx::query(
                "update memphant.memory_unit set state = $3, transaction_to = $4::timestamptz
                 where tenant_id = $1 and id = $2 and data_subject_id = $5
                   and subject_generation = $6 and scope_id = $7 and agent_node_id = $8
                   and actor_id = $9",
            )
            .bind(tenant.as_uuid())
            .bind(update.id.as_uuid())
            .bind(enum_str(&update.state))
            .bind(&transaction_time)
            .bind(context.data_subject_id.as_uuid())
            .bind(context.subject_generation as i64)
            .bind(context.scope_id.as_uuid())
            .bind(context.agent_node_id.as_uuid())
            .bind(context.actor_id.as_uuid())
            .execute(&mut **tx)
            .await
            .map_err(backend)?;
            if updated.rows_affected() != 1 {
                return Err(StoreError::Conflict(
                    "compiled update does not match memory context".to_string(),
                ));
            }
        }

        // Forgotten-source tombstones durably block re-derivation.
        let tombstones: Vec<(String, Uuid)> = sqlx::query(
            "select source_kind, source_id from memphant.forgotten_source
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5",
        )
        .bind(tenant.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .fetch_all(&mut **tx)
        .await
        .map_err(backend)?
        .iter()
        .map(|row| {
            Ok::<_, StoreError>((
                row.try_get::<String, _>("source_kind").map_err(backend)?,
                row.try_get::<Uuid, _>("source_id").map_err(backend)?,
            ))
        })
        .collect::<Result<_, _>>()?;
        let forbidden: HashSet<(&str, Uuid)> = tombstones
            .iter()
            .map(|(kind, id)| (kind.as_str(), *id))
            .collect();
        let is_forgotten = |unit: &StoredMemoryUnit| {
            unit.source_episode_id
                .is_some_and(|id| forbidden.contains(&("episode", id.as_uuid())))
                || unit
                    .source_resource_id
                    .is_some_and(|id| forbidden.contains(&("resource", id.as_uuid())))
                || forbidden.contains(&("memory_unit", unit.id.as_uuid()))
        };

        let mut admitted_ids: HashSet<UnitId> = HashSet::new();
        for unit in &write.new_units {
            if is_forgotten(unit) {
                continue;
            }
            let mut unit = unit.clone();
            unit.transaction_from = Some(transaction_time.clone());
            unit.transaction_to = None;
            Self::insert_unit(tx, &unit).await?;
            admitted_ids.insert(unit.id);
        }
        for citation in &write.citations {
            if !admitted_ids.contains(&citation.memory_unit_id) {
                continue;
            }
            if citation.tenant_id != context.tenant_id
                || citation.data_subject_id != context.data_subject_id
                || citation.subject_generation != context.subject_generation
                || citation.scope_id != context.scope_id
                || citation.agent_node_id != context.agent_node_id
                || (citation.episode_id.is_some() && citation.resource_id.is_some())
            {
                return Err(StoreError::Conflict(
                    "compiled citation does not match memory context".to_string(),
                ));
            }
            sqlx::query(
                "insert into memphant.citation
                   (id, tenant_id, data_subject_id, scope_id, agent_node_id,
                    subject_generation, memory_unit_id, episode_id, resource_id, span, quote_hash)
                 values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            )
            .bind(citation.id)
            .bind(citation.tenant_id.as_uuid())
            .bind(citation.data_subject_id.as_uuid())
            .bind(citation.scope_id.as_uuid())
            .bind(citation.agent_node_id.as_uuid())
            .bind(citation.subject_generation as i64)
            .bind(citation.memory_unit_id.as_uuid())
            .bind(citation.episode_id.map(|id| id.as_uuid()))
            .bind(citation.resource_id.map(|id| id.as_uuid()))
            .bind(
                citation
                    .span
                    .as_ref()
                    .map(serde_json::to_value)
                    .transpose()
                    .map_err(|error| StoreError::Backend(error.to_string()))?,
            )
            .bind(&citation.quote_hash)
            .execute(&mut **tx)
            .await
            .map_err(backend)?;
        }

        // Edges may only reference admitted or pre-existing units.
        let mut existing: HashMap<Uuid, bool> = HashMap::new();
        for edge in &write.new_edges {
            let mut endpoints_ok = true;
            for endpoint in [edge.src_id, edge.dst_id] {
                if admitted_ids.contains(&endpoint) {
                    continue;
                }
                let known = match existing.get(&endpoint.as_uuid()) {
                    Some(known) => *known,
                    None => {
                        let found: Option<Uuid> = sqlx::query_scalar(
                            "select id from memphant.memory_unit
                             where tenant_id = $1 and data_subject_id = $2
                               and subject_generation = $3 and scope_id = $4
                               and agent_node_id = $5 and actor_id = $6 and id = $7",
                        )
                        .bind(tenant.as_uuid())
                        .bind(context.data_subject_id.as_uuid())
                        .bind(context.subject_generation as i64)
                        .bind(context.scope_id.as_uuid())
                        .bind(context.agent_node_id.as_uuid())
                        .bind(context.actor_id.as_uuid())
                        .bind(endpoint.as_uuid())
                        .fetch_optional(&mut **tx)
                        .await
                        .map_err(backend)?;
                        let known = found.is_some();
                        existing.insert(endpoint.as_uuid(), known);
                        known
                    }
                };
                if !known {
                    endpoints_ok = false;
                    break;
                }
            }
            if !endpoints_ok {
                return Err(StoreError::Conflict(
                    "compiled edge does not match memory context".to_string(),
                ));
            }
            let mut edge = edge.clone();
            edge.transaction_from = Some(transaction_time.clone());
            edge.transaction_to = None;
            Self::insert_edge(tx, &context, &edge).await?;
        }

        // Store the compiled trace as the idempotency record on the row locked
        // before any unit mutation.
        let trace_json = serde_json::to_value(memphant_core::ReflectJobResult::Completed {
            trace: write.trace.clone(),
        })
        .map_err(|error| StoreError::Backend(error.to_string()))?;
        let updated = sqlx::query(
            "update memphant.job_state
             set result = $11, state = 'done', claimed_at = null, updated_at = now()
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8
               and ($9::integer is null or attempts = $9)
               and claim_generation = $10 and state = 'running'",
        )
        .bind(tenant.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(write.job_id.as_uuid())
        .bind(&write.compiler_version)
        .bind(claim.map(|claim| claim.attempts as i32))
        .bind(claim.map_or(0, |claim| claim.claim_generation) as i64)
        .bind(&trace_json)
        .execute(&mut **tx)
        .await
        .map_err(backend)?;
        if updated.rows_affected() == 0 {
            return Ok(ClaimMutationOutcome::Stale);
        }

        // Embedding write-through in the SAME transaction as the units they
        // describe, for admitted units only (forgotten-source units were
        // skipped above), so the vector channel can never reference a unit this
        // write tombstoned out. Empty for noop providers.
        if let Some(profile) = &write.embedding_profile {
            let rows: Vec<&EmbeddingRow> = write
                .embeddings
                .iter()
                .filter(|row| admitted_ids.contains(&row.memory_unit_id))
                .collect();
            if !rows.is_empty() {
                Self::upsert_embedding_profile_tx(tx, tenant, profile).await?;
                for row in rows {
                    Self::insert_embedding_tx(tx, &context, row).await?;
                }
            }
        }

        Ok(ClaimMutationOutcome::Applied)
    }

    async fn fetch_reflect_trace(
        &self,
        context: &ResolvedMemoryContext,
        job_id: JobId,
        compiler_version: &str,
    ) -> Result<Option<ReflectTrace>, StoreError> {
        let tenant = context.tenant_id;
        let mut tx = self.tenant_tx(tenant).await?;
        let document: Option<serde_json::Value> = sqlx::query_scalar(
            "select result from memphant.job_state
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and actor_id = $6
               and id = $7 and compiler_version = $8 and result is not null",
        )
        .bind(tenant.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(context.actor_id.as_uuid())
        .bind(job_id.as_uuid())
        .bind(compiler_version)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        match document
            .map(serde_json::from_value)
            .transpose()
            .map_err(|error| StoreError::Backend(format!("invalid reflect job result: {error}")))?
        {
            Some(memphant_core::ReflectJobResult::Completed { trace }) => Ok(Some(trace)),
            Some(memphant_core::ReflectJobResult::Prepared { .. }) | None => Ok(None),
        }
    }

    async fn upsert_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        rows: Vec<EmbeddingRow>,
    ) -> Result<(), StoreError> {
        if rows.is_empty() {
            return Ok(());
        }
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        for row in &rows {
            Self::insert_embedding_tx(&mut tx, context, row).await?;
        }
        tx.commit().await.map_err(backend)
    }

    async fn upsert_embedding_profile(
        &self,
        tenant: TenantId,
        profile: EmbeddingProfileRow,
    ) -> Result<(), StoreError> {
        let mut tx = self.tenant_tx(tenant).await?;
        Self::upsert_embedding_profile_tx(&mut tx, tenant, &profile).await?;
        tx.commit().await.map_err(backend)
    }

    async fn fetch_embeddings(
        &self,
        context: &ResolvedMemoryContext,
        unit_ids: &[UnitId],
    ) -> Result<Vec<EmbeddingRow>, StoreError> {
        let mut tx = self.tenant_tx(context.tenant_id).await?;
        let uuids: Vec<Uuid> = unit_ids.iter().map(|id| id.as_uuid()).collect();
        let rows = sqlx::query(
            "select memory_unit_id, embedding_profile_id, vec::text as vec
             from memphant.embedding
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and memory_unit_id = any($6)",
        )
        .bind(context.tenant_id.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(uuids)
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        rows.iter()
            .map(|row| {
                Ok(EmbeddingRow {
                    memory_unit_id: UnitId::from_u128(
                        row.try_get::<Uuid, _>("memory_unit_id")
                            .map_err(backend)?
                            .as_u128(),
                    ),
                    embedding_profile_id: row.try_get("embedding_profile_id").map_err(backend)?,
                    vec: parse_vec_literal(
                        row.try_get::<Option<String>, _>("vec")
                            .map_err(backend)?
                            .as_deref()
                            .unwrap_or(""),
                    )?,
                })
            })
            .collect()
    }

    async fn lookup_api_key(&self, key_hash: &str) -> Result<Option<ApiKeyRow>, StoreError> {
        let pool = self
            .auth_pool
            .as_ref()
            .ok_or_else(|| StoreError::Backend("store has no authn capability".to_string()))?;
        let row = sqlx::query(
            "select id, tenant_id, key_hash, label, max_trust, data_subject_id,
                    subject_generation, actor_id, scope_id, agent_node_id,
                    can_forget, can_audit_history, revoked
             from memphant.authenticate_api_key($1)",
        )
        .bind(key_hash)
        .fetch_optional(pool)
        .await
        .map_err(backend)?;
        let Some(row) = row else { return Ok(None) };
        Ok(Some(ApiKeyRow {
            id: row.try_get("id").map_err(backend)?,
            tenant_id: TenantId::from_u128(
                row.try_get::<Uuid, _>("tenant_id")
                    .map_err(backend)?
                    .as_u128(),
            ),
            key_hash: row.try_get("key_hash").map_err(backend)?,
            label: row.try_get("label").map_err(backend)?,
            max_trust: enum_from_str(
                row.try_get::<String, _>("max_trust")
                    .map_err(backend)?
                    .as_str(),
            )?,
            data_subject_id: row
                .try_get::<Option<Uuid>, _>("data_subject_id")
                .map_err(backend)?
                .map(|id| SubjectId::from_u128(id.as_u128())),
            subject_generation: row
                .try_get::<Option<i64>, _>("subject_generation")
                .map_err(backend)?
                .map(|generation| generation as u64),
            actor_id: row
                .try_get::<Option<Uuid>, _>("actor_id")
                .map_err(backend)?
                .map(|id| ActorId::from_u128(id.as_u128())),
            scope_id: row
                .try_get::<Option<Uuid>, _>("scope_id")
                .map_err(backend)?
                .map(|id| ScopeId::from_u128(id.as_u128())),
            agent_node_id: row
                .try_get::<Option<Uuid>, _>("agent_node_id")
                .map_err(backend)?
                .map(|id| AgentNodeId::from_u128(id.as_u128())),
            can_forget: row.try_get("can_forget").map_err(backend)?,
            can_audit_history: row.try_get("can_audit_history").map_err(backend)?,
            revoked: row.try_get("revoked").map_err(backend)?,
        }))
    }

    async fn resolve_context_binding(
        &self,
        tenant: TenantId,
        client_ref: String,
        request: ContextBindingRequest,
    ) -> Result<ContextBindingResponse, StoreError> {
        let mut canonical_policies = request.access_policies.clone();
        canonical_policies.sort_by_key(|policy| serde_json::to_string(policy).unwrap_or_default());
        let policy_bytes = serde_json::to_vec(&canonical_policies)
            .map_err(|error| StoreError::Backend(error.to_string()))?;
        let fingerprint: String = Sha256::digest(policy_bytes)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let mut identity = request.clone();
        identity.access_policies.clear();
        let identity_bytes = serde_json::to_vec(&identity)
            .map_err(|error| StoreError::Backend(error.to_string()))?;
        let identity_fingerprint: String = Sha256::digest(identity_bytes)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let mut tx = self.pool.begin().await.map_err(backend)?;
        sqlx::query("select memphant.bind_tenant($1)")
            .bind(tenant.as_uuid())
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        sqlx::query("select pg_advisory_xact_lock(hashtextextended($1, 0))")
            // Context provisioning is low-frequency. A tenant-wide lock keeps
            // different client refs that share subject/scope/actor refs from
            // racing through SELECT -> INSERT and surfacing a unique violation
            // as a spurious backend outage.
            .bind(tenant.as_uuid().to_string())
            .execute(&mut *tx)
            .await
            .map_err(backend)?;

        if let Some(row) = sqlx::query(
            "select binding.identity_fingerprint, binding.policy_revision,
                    binding.data_subject_id as subject_id, binding.actor_id,
                    binding.scope_id, binding.agent_node_id,
                    subject.generation, agent_node.level
               from memphant.context_binding binding
               join memphant.subject subject
                 on subject.tenant_id = binding.tenant_id
                and subject.id = binding.data_subject_id
               join memphant.agent_node agent_node
                 on agent_node.tenant_id = binding.tenant_id and agent_node.id = binding.agent_node_id
                and agent_node.data_subject_id = binding.data_subject_id
               join memphant.scope scope
                 on scope.tenant_id = binding.tenant_id and scope.id = binding.scope_id
                and scope.data_subject_id = binding.data_subject_id
              where binding.tenant_id = $1 and binding.client_ref = $2",
        )
        .bind(tenant.as_uuid())
        .bind(&client_ref)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?
        {
            let existing: String = row.try_get("policy_revision").map_err(backend)?;
            let existing_identity: String =
                row.try_get("identity_fingerprint").map_err(backend)?;
            if existing_identity != identity_fingerprint {
                return Err(StoreError::Conflict(
                    "context binding identity, kind, and parent are immutable".to_string(),
                ));
            }
            let mut response = ContextBindingResponse {
                subject_id: SubjectId::from_u128(
                    row.try_get::<Uuid, _>("subject_id").map_err(backend)?.as_u128(),
                ),
                actor_id: ActorId::from_u128(
                    row.try_get::<Uuid, _>("actor_id").map_err(backend)?.as_u128(),
                ),
                scope_id: ScopeId::from_u128(
                    row.try_get::<Uuid, _>("scope_id").map_err(backend)?.as_u128(),
                ),
                agent_node_id: AgentNodeId::from_u128(
                    row.try_get::<Uuid, _>("agent_node_id")
                        .map_err(backend)?
                        .as_u128(),
                ),
                agent_level: row.try_get::<i16, _>("level").map_err(backend)? as u8,
                policy_revision: row.try_get("policy_revision").map_err(backend)?,
                subject_generation: row.try_get::<i64, _>("generation").map_err(backend)? as u64,
            };
            if existing != fingerprint {
                Self::replace_context_policies(
                    &mut tx,
                    tenant,
                    response.subject_id.as_uuid(),
                    response.scope_id.as_uuid(),
                    response.agent_node_id.as_uuid(),
                    response.agent_level,
                    &canonical_policies,
                )
                .await?;
                sqlx::query(
                    "update memphant.context_binding
                        set request_fingerprint = $6, policy_revision = $6
                      where tenant_id = $1 and data_subject_id = $2 and scope_id = $3
                        and agent_node_id = $4 and client_ref = $5",
                )
                .bind(tenant.as_uuid())
                .bind(response.subject_id.as_uuid())
                .bind(response.scope_id.as_uuid())
                .bind(response.agent_node_id.as_uuid())
                .bind(&client_ref)
                .bind(&fingerprint)
                .execute(&mut *tx)
                .await
                .map_err(backend)?;
                response.policy_revision = fingerprint.clone();
            }
            tx.commit().await.map_err(backend)?;
            return Ok(response);
        }

        let (subject_id, subject_generation) = if let Some(row) = sqlx::query(
            "select id, kind, generation from memphant.subject
              where tenant_id = $1 and external_ref = $2",
        )
        .bind(tenant.as_uuid())
        .bind(&request.subject.external_ref)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?
        {
            let kind: String = row.try_get("kind").map_err(backend)?;
            if kind != request.subject.kind {
                return Err(StoreError::Conflict(
                    "subject kind is immutable".to_string(),
                ));
            }
            (
                row.try_get::<Uuid, _>("id").map_err(backend)?,
                row.try_get::<i64, _>("generation").map_err(backend)? as u64,
            )
        } else {
            let id = Uuid::now_v7();
            sqlx::query(
                "insert into memphant.subject (id, tenant_id, external_ref, kind)
                 values ($1, $2, $3, $4)",
            )
            .bind(id)
            .bind(tenant.as_uuid())
            .bind(&request.subject.external_ref)
            .bind(&request.subject.kind)
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
            (id, 0)
        };

        let actor_id = if let Some(row) = sqlx::query(
            "select id, kind from memphant.actor
              where tenant_id = $1 and data_subject_id = $2 and external_ref = $3",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id)
        .bind(&request.actor.external_ref)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?
        {
            let kind: String = row.try_get("kind").map_err(backend)?;
            if kind != request.actor.kind {
                return Err(StoreError::Conflict("actor kind is immutable".to_string()));
            }
            row.try_get::<Uuid, _>("id").map_err(backend)?
        } else {
            let id = Uuid::now_v7();
            let trust = memphant_types::actor_kind_trust(&request.actor.kind);
            sqlx::query(
                "insert into memphant.actor
                   (id, tenant_id, data_subject_id, kind, external_ref, trust_level)
                 values ($1, $2, $3, $4, $5, $6)",
            )
            .bind(id)
            .bind(tenant.as_uuid())
            .bind(subject_id)
            .bind(&request.actor.kind)
            .bind(&request.actor.external_ref)
            .bind(enum_str(&trust))
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
            id
        };

        let (parent_scope_id, parent_path, scope_depth) =
            if let Some(parent_ref) = request.scope.parent_external_ref.as_deref() {
                let row = sqlx::query(
                    "select id, materialized_path::text as materialized_path, scope_depth
                       from memphant.scope
                      where tenant_id = $1 and data_subject_id = $2 and external_ref = $3",
                )
                .bind(tenant.as_uuid())
                .bind(subject_id)
                .bind(parent_ref)
                .fetch_optional(&mut *tx)
                .await
                .map_err(backend)?
                .ok_or(StoreError::NotFound("parent scope"))?;
                (
                    Some(row.try_get::<Uuid, _>("id").map_err(backend)?),
                    Some(
                        row.try_get::<String, _>("materialized_path")
                            .map_err(backend)?,
                    ),
                    row.try_get::<i16, _>("scope_depth").map_err(backend)? + 1,
                )
            } else {
                (None, None, 0)
            };
        let scope_id = if let Some(row) = sqlx::query(
            "select id, kind, parent_scope_id from memphant.scope
              where tenant_id = $1 and data_subject_id = $2 and external_ref = $3",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id)
        .bind(&request.scope.external_ref)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?
        {
            let kind: String = row.try_get("kind").map_err(backend)?;
            let existing_parent: Option<Uuid> = row.try_get("parent_scope_id").map_err(backend)?;
            if kind != request.scope.kind || existing_parent != parent_scope_id {
                return Err(StoreError::Conflict(
                    "scope kind or parent is immutable".to_string(),
                ));
            }
            row.try_get::<Uuid, _>("id").map_err(backend)?
        } else {
            let id = Uuid::now_v7();
            let label = id.to_string().replace('-', "_");
            let path = parent_path
                .map(|parent| format!("{parent}.{label}"))
                .unwrap_or(label);
            sqlx::query(
                "insert into memphant.scope
                   (id, tenant_id, data_subject_id, parent_scope_id, kind, external_ref,
                    materialized_path, scope_depth)
                 values ($1, $2, $3, $4, $5, $6, $7::ltree, $8)",
            )
            .bind(id)
            .bind(tenant.as_uuid())
            .bind(subject_id)
            .bind(parent_scope_id)
            .bind(&request.scope.kind)
            .bind(&request.scope.external_ref)
            .bind(path)
            .bind(scope_depth)
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
            id
        };

        let (parent_agent_node_id, agent_level) =
            if let Some(parent_ref) = request.agent_node.parent_external_ref.as_deref() {
                let row = sqlx::query(
                    "select id, level from memphant.agent_node
                      where tenant_id = $1 and data_subject_id = $2 and external_ref = $3",
                )
                .bind(tenant.as_uuid())
                .bind(subject_id)
                .bind(parent_ref)
                .fetch_optional(&mut *tx)
                .await
                .map_err(backend)?
                .ok_or(StoreError::NotFound("parent agent node"))?;
                (
                    Some(row.try_get::<Uuid, _>("id").map_err(backend)?),
                    row.try_get::<i16, _>("level").map_err(backend)? + 1,
                )
            } else {
                (None, 0)
            };
        let agent_node_id = if let Some(row) = sqlx::query(
            "select id, scope_id, parent_agent_node_id, level from memphant.agent_node
              where tenant_id = $1 and data_subject_id = $2 and external_ref = $3",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id)
        .bind(&request.agent_node.external_ref)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?
        {
            let existing_scope: Uuid = row.try_get("scope_id").map_err(backend)?;
            let existing_parent: Option<Uuid> =
                row.try_get("parent_agent_node_id").map_err(backend)?;
            let existing_level: i16 = row.try_get("level").map_err(backend)?;
            if existing_scope != scope_id
                || existing_parent != parent_agent_node_id
                || existing_level != agent_level
            {
                return Err(StoreError::Conflict(
                    "agent node parent or scope is immutable".to_string(),
                ));
            }
            row.try_get::<Uuid, _>("id").map_err(backend)?
        } else {
            let id = Uuid::now_v7();
            sqlx::query(
                "insert into memphant.agent_node
                   (id, tenant_id, data_subject_id, scope_id, parent_agent_node_id, level,
                    external_ref)
                 values ($1, $2, $3, $4, $5, $6, $7)",
            )
            .bind(id)
            .bind(tenant.as_uuid())
            .bind(subject_id)
            .bind(scope_id)
            .bind(parent_agent_node_id)
            .bind(agent_level)
            .bind(&request.agent_node.external_ref)
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
            id
        };

        if let Some(existing_client_ref) = sqlx::query_scalar::<_, String>(
            "select client_ref from memphant.context_binding
              where tenant_id = $1 and data_subject_id = $2 and agent_node_id = $3",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id)
        .bind(agent_node_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?
        {
            return Err(StoreError::Conflict(format!(
                "context identity is already registered as {existing_client_ref}"
            )));
        }

        Self::replace_context_policies(
            &mut tx,
            tenant,
            subject_id,
            scope_id,
            agent_node_id,
            agent_level as u8,
            &canonical_policies,
        )
        .await?;

        sqlx::query(
            "insert into memphant.context_binding
               (tenant_id, data_subject_id, client_ref, identity_fingerprint,
                request_fingerprint, actor_id, scope_id, agent_node_id, policy_revision)
             values ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id)
        .bind(&client_ref)
        .bind(&identity_fingerprint)
        .bind(&fingerprint)
        .bind(actor_id)
        .bind(scope_id)
        .bind(agent_node_id)
        .bind(&fingerprint)
        .execute(&mut *tx)
        .await
        .map_err(backend)?;
        tx.commit().await.map_err(backend)?;

        Ok(ContextBindingResponse {
            subject_id: SubjectId::from_u128(subject_id.as_u128()),
            actor_id: ActorId::from_u128(actor_id.as_u128()),
            scope_id: ScopeId::from_u128(scope_id.as_u128()),
            agent_node_id: AgentNodeId::from_u128(agent_node_id.as_u128()),
            agent_level: agent_level as u8,
            policy_revision: fingerprint,
            subject_generation,
        })
    }

    async fn resolve_memory_context(
        &self,
        tenant: TenantId,
        subject_id: SubjectId,
        actor_id: ActorId,
        scope_id: ScopeId,
        agent_node_id: AgentNodeId,
    ) -> Result<ResolvedMemoryContext, StoreError> {
        let mut tx = self.pool.begin().await.map_err(backend)?;
        sqlx::query("select memphant.bind_tenant($1)")
            .bind(tenant.as_uuid())
            .execute(&mut *tx)
            .await
            .map_err(backend)?;
        let row = sqlx::query(
            "select subject.generation, agent_node.level, binding.policy_revision,
                    actor.trust_level
               from memphant.context_binding binding
               join memphant.subject subject
                 on subject.tenant_id = binding.tenant_id
                and subject.id = binding.data_subject_id
               join memphant.agent_node agent_node
                 on agent_node.tenant_id = binding.tenant_id
                and agent_node.id = binding.agent_node_id
                and agent_node.data_subject_id = binding.data_subject_id
               join memphant.actor actor
                 on actor.tenant_id = binding.tenant_id
                and actor.id = binding.actor_id
                and actor.data_subject_id = binding.data_subject_id
              where binding.tenant_id = $1 and binding.data_subject_id = $2
                and binding.actor_id = $3 and binding.scope_id = $4
                and binding.agent_node_id = $5",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id.as_uuid())
        .bind(actor_id.as_uuid())
        .bind(scope_id.as_uuid())
        .bind(agent_node_id.as_uuid())
        .fetch_optional(&mut *tx)
        .await
        .map_err(backend)?;
        let Some(row) = row else {
            let tombstoned: bool = sqlx::query_scalar(
                "select exists(
                   select 1 from memphant.subject_tombstone
                   where tenant_id = $1 and erased_subject_id = $2
                 )",
            )
            .bind(tenant.as_uuid())
            .bind(subject_id.as_uuid())
            .fetch_one(&mut *tx)
            .await
            .map_err(backend)?;
            return Err(if tombstoned {
                StoreError::SubjectErased
            } else {
                StoreError::NotFound("memory context")
            });
        };
        let agent_level = row.try_get::<i16, _>("level").map_err(backend)? as u8;
        let subject_generation = row.try_get::<i64, _>("generation").map_err(backend)? as u64;
        let policy_revision: String = row.try_get("policy_revision").map_err(backend)?;
        let actor_trust =
            enum_from_str(&row.try_get::<String, _>("trust_level").map_err(backend)?)?;

        let mut sources_by_kind = std::collections::BTreeMap::new();
        // `MemoryKind::ALL`, never a hand-written list: a kind omitted here
        // resolves to no source, so `context.allows` denies it and the unit is
        // invisible to the compiler snapshot AND to recall. That is how minting
        // `preference` first failed — the write landed and nothing could see it.
        for kind in MemoryKind::ALL {
            let exact_allowed = agent_level_allows_memory_kind(agent_level, kind);
            sources_by_kind.insert(
                kind,
                if exact_allowed {
                    vec![ResolvedMemorySource {
                        scope_id,
                        agent_node_id,
                    }]
                } else {
                    Vec::new()
                },
            );
        }
        let admitted = sqlx::query(
            "select distinct policy.kind, policy.source_scope_id,
                    policy.source_agent_node_id
               from memphant.scope_policy policy
              where policy.tenant_id = $1 and policy.data_subject_id = $2
                and policy.grantee_scope_id = $3
                and policy.grantee_agent_node_id = $4",
        )
        .bind(tenant.as_uuid())
        .bind(subject_id.as_uuid())
        .bind(scope_id.as_uuid())
        .bind(agent_node_id.as_uuid())
        .fetch_all(&mut *tx)
        .await
        .map_err(backend)?;
        for admitted_row in admitted {
            let kind: MemoryKind = enum_from_str(
                admitted_row
                    .try_get::<String, _>("kind")
                    .map_err(backend)?
                    .as_str(),
            )?;
            let source_scope = ScopeId::from_u128(
                admitted_row
                    .try_get::<Uuid, _>("source_scope_id")
                    .map_err(backend)?
                    .as_u128(),
            );
            let source_agent = AgentNodeId::from_u128(
                admitted_row
                    .try_get::<Uuid, _>("source_agent_node_id")
                    .map_err(backend)?
                    .as_u128(),
            );
            sources_by_kind
                .entry(kind)
                .or_default()
                .push(ResolvedMemorySource {
                    scope_id: source_scope,
                    agent_node_id: source_agent,
                });
        }
        for sources in sources_by_kind.values_mut() {
            sources
                .sort_by_key(|source| (source.scope_id.as_uuid(), source.agent_node_id.as_uuid()));
            sources.dedup();
        }
        tx.commit().await.map_err(backend)?;
        Ok(ResolvedMemoryContext {
            tenant_id: tenant,
            data_subject_id: subject_id,
            actor_id,
            actor_trust,
            scope_id,
            agent_node_id,
            agent_level,
            subject_generation,
            policy_revision,
            sources_by_kind,
        })
    }

    async fn ping(&self) -> Result<(), StoreError> {
        let (database_head, compatibility_floor, embedded_head_present): (String, String, bool) =
            sqlx::query_as(
                "select coalesce(max(version), ''),
                        coalesce(max(schema_compat_revision), ''),
                        coalesce(bool_or(version = $1 and schema_compat_revision = $2), false)
                 from memphant.schema_migrations",
            )
            .bind(MIGRATION_HEAD)
            .bind(SCHEMA_COMPAT_REVISION)
            .fetch_one(&self.pool)
            .await
            .map_err(backend)?;
        let ready = embedded_head_present
            && database_head.as_str() >= MIGRATION_HEAD
            && compatibility_floor.as_str() <= MIGRATION_HEAD;
        if ready {
            Ok(())
        } else {
            Err(StoreError::Backend(format!(
                "database schema is incompatible with embedded migration head {MIGRATION_HEAD} \
                 (database head {database_head}, compatibility floor {compatibility_floor})"
            )))
        }
    }

    async fn dead_letter_count(&self) -> Result<u64, StoreError> {
        let count: i64 = sqlx::query_scalar("select memphant.dead_letter_count()")
            .fetch_one(&self.pool)
            .await
            .map_err(backend)?;
        Ok(count as u64)
    }
}

#[cfg(test)]
mod tests {
    use super::{PgStore, StoreError, TRANSACTION_POOLER_ERROR, canonical_timestamp};

    #[test]
    fn persistent_connect_options_accepts_5432_forms_and_rejects_6543() {
        for (label, url) in [
            (
                "implicit default",
                "postgresql://memphant:secret@localhost/memphant",
            ),
            (
                "explicit direct",
                "postgresql://memphant:secret@localhost:5432/memphant",
            ),
            (
                "supabase direct",
                "postgresql://postgres:secret@db.example.supabase.co:5432/postgres",
            ),
            (
                "supabase session pooler",
                "postgresql://postgres.example:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
            ),
            (
                "ipv6 direct",
                "postgresql://memphant:secret@[::1]:5432/memphant",
            ),
        ] {
            let options = PgStore::persistent_connect_options(url)
                .unwrap_or_else(|error| panic!("{label} URL was rejected: {error}"));
            assert_eq!(options.get_port(), 5432, "{label}");
        }

        match PgStore::persistent_connect_options(
            "postgresql://postgres.example:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
        )
        .expect_err("transaction pooler must be rejected")
        {
            StoreError::Backend(message) => assert_eq!(message, TRANSACTION_POOLER_ERROR),
            other => panic!("unexpected error: {other}"),
        }
    }

    #[test]
    fn timestamp_projection_uses_minimal_rfc3339_fraction() {
        assert_eq!(
            canonical_timestamp(Some("2025-03-01T00:00:00.000000Z".to_string())).as_deref(),
            Some("2025-03-01T00:00:00Z")
        );
        assert_eq!(
            canonical_timestamp(Some("2025-03-01T00:00:00.123000Z".to_string())).as_deref(),
            Some("2025-03-01T00:00:00.123Z")
        );
    }
}
