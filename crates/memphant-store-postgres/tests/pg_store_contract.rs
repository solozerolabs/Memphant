//! Live-Postgres contract tests for `PgStore`.
//!
//! The shared `MemoryStore` contract runs here via `memphant-store-testkit`
//! (the `pg_contract_test!` wrappers) — the SAME scenarios `memphant-core` runs
//! against `InMemoryStore`, so a per-store trait divergence fails on at least
//! one backend. The rest are pg-specific tests exercising behaviour the trait
//! can't express: fresh-pool durability, the reflect job queue, pgvector, and
//! SQL-level invariants.
//!
//! Gated: every test is `#[ignore]` and reads `MEMPHANT_TEST_DATABASE_URL`.
//! Run with:
//!   MEMPHANT_TEST_DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant \
//!     cargo test -p memphant-store-postgres -- --ignored --test-threads=1

use std::collections::BTreeMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};

use memphant_core::service::{MemoryService, file_sync_plan_sha256};
use memphant_core::{
    EmbedError, EmbeddingProvider, FixedClock, JobFilter, MemoryStore, MutationClaim,
    MutationLedgerStore, MutationVerb, NoopEmbedding, StructuredObservation,
    StructuredObservationDisposition, StructuredStateOp, StructuredStateOperation,
    StructuredStateProvider, StructuredStateProviderError, StructuredStateProviderIdentity,
    StructuredStateRequest, canonical_mutation_request_hash, derive_fact_key, recall,
    reflect_recorded, retain_episode, retain_resource,
};
use memphant_store_postgres::{MIGRATIONS, PgStore};
use memphant_store_testkit::StoreHarness;
use memphant_types::{
    CanonicalProjectionUnit, ContextBindingAccessPolicy, ContextBindingAgentRef,
    ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef, FileSyncOperation,
    FileSyncOperationResult, FileSyncRequest, FileSyncResult, FileSyncUnitMetadata, JobId,
    MarkOutcome, MemoryEdgeKind, MemoryKind, NewMemoryEdge, NewMemoryUnit, RecallHttpRequest,
    RecallMode, RecallRequest, RecallTime, ReflectCandidate, ReflectInput, ReflectJob,
    ReflectJobKind, ResolvedMemoryContext, RetainEpisodeHttpRequest, RetainEpisodeHttpResponse,
    RetainEpisodePayload, RetainPayload, RetainRequest, RetainResourceRequest, RetainUnitPayload,
    ReviewEvent, SCHEMA_COMPAT_REVISION, TenantId, TrustLevel, UnitState,
};
use uuid::Uuid;

#[derive(Debug)]
struct FixedStructuredProvider {
    identity: StructuredStateProviderIdentity,
    operations: Vec<StructuredStateOp>,
}

impl FixedStructuredProvider {
    fn new(operations: Vec<StructuredStateOp>) -> Self {
        Self {
            identity: StructuredStateProviderIdentity {
                model: "fixed-structured-state".to_string(),
                prompt_hash: "pg-contract-prompt-sha256".to_string(),
                schema_hash: "pg-contract-schema-sha256".to_string(),
            },
            operations,
        }
    }
}

impl StructuredStateProvider for FixedStructuredProvider {
    fn identity(&self) -> &StructuredStateProviderIdentity {
        &self.identity
    }

    fn extract<'a>(
        &'a self,
        request: &'a StructuredStateRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>>
                + Send
                + 'a,
        >,
    > {
        let evidence_slice_id = request.evidence_slices[0].id.clone();
        let observations = self
            .operations
            .clone()
            .into_iter()
            .map(|operation| StructuredObservation {
                namespace: operation.namespace,
                item_key: operation.item_key,
                fields: operation.fields,
                disposition: if operation.operation == StructuredStateOperation::Append {
                    StructuredObservationDisposition::Event
                } else {
                    StructuredObservationDisposition::State
                },
                evidence_slice_id: evidence_slice_id.clone(),
                evidence_quote: operation.evidence_quote,
                valid_from: operation.valid_from,
                valid_to: operation.valid_to,
            })
            .collect();
        Box::pin(async move { Ok(observations) })
    }
}

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

fn test_recall_time() -> RecallTime {
    RecallTime {
        evaluated_at: CLOCK.0.to_string(),
        transaction_as_of: CLOCK.0.to_string(),
        valid_at: CLOCK.0.to_string(),
    }
}

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated Postgres")
}

async fn connect() -> PgStore {
    PgStore::connect(&db_url()).await.expect("connect PgStore")
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn ping_rejects_bootstrap_only_schema_until_required_revision_is_applied() {
    let latest = MIGRATIONS.last().expect("at least one embedded migration");
    let database_url = db_url();
    let pool = sqlx::PgPool::connect(&database_url)
        .await
        .expect("connect migration test pool");
    sqlx::raw_sql("drop schema memphant cascade")
        .execute(&pool)
        .await
        .expect("reset scratch schema");
    sqlx::raw_sql(MIGRATIONS[0].1)
        .execute(&pool)
        .await
        .expect("apply bootstrap only");

    let store = PgStore::connect(&database_url)
        .await
        .expect("connect bootstrap-only store");
    let error = store
        .ping()
        .await
        .expect_err("bootstrap-only schema must be unready");
    assert!(
        error.to_string().contains(latest.0),
        "readiness error must name the required migration head: {error}"
    );

    let app_login = format!("mp_readiness_{}", Uuid::new_v4().simple());
    let create_login = format!("create role {app_login} login password 'pg_contract_password'");
    sqlx::query(sqlx::AssertSqlSafe(create_login.as_str()))
        .execute(&pool)
        .await
        .expect("create app readiness login");
    let grant_app = format!("grant memphant_app to {app_login}");
    sqlx::query(sqlx::AssertSqlSafe(grant_app.as_str()))
        .execute(&pool)
        .await
        .expect("grant app capability");
    let (_, host) = database_url
        .split_once('@')
        .expect("database URL has credentials");
    let scheme = if database_url.starts_with("postgresql://") {
        "postgresql://"
    } else {
        "postgres://"
    };
    let app_database_url = format!("{scheme}{app_login}:pg_contract_password@{host}");
    let app_store = PgStore::connect_worker(&app_database_url)
        .await
        .expect("connect least-privilege app store");
    app_store
        .ping()
        .await
        .expect_err("bootstrap-only schema must be unready under memphant_app");

    for (_, migration) in MIGRATIONS.iter().skip(1) {
        sqlx::raw_sql(*migration)
            .execute(&pool)
            .await
            .expect("apply required forward migration");
    }
    store
        .ping()
        .await
        .expect("current schema revision is ready");
    app_store
        .ping()
        .await
        .expect("current schema revision is ready under memphant_app");

    async fn readiness_as_app(pool: &sqlx::PgPool) -> (String, String, bool) {
        let mut app_tx = pool.begin().await.expect("begin app-role readiness");
        sqlx::query("set local role memphant_app")
            .execute(&mut *app_tx)
            .await
            .expect("assume app role");
        sqlx::query_as(
            "select coalesce(max(version), ''),
                    coalesce(max(schema_compat_revision), ''),
                    coalesce(bool_or(version = $1 and schema_compat_revision = $2), false)
             from memphant.schema_migrations",
        )
        .bind(MIGRATIONS.last().expect("embedded migration head").0)
        .bind(SCHEMA_COMPAT_REVISION)
        .fetch_one(&mut *app_tx)
        .await
        .expect("app role can evaluate the compatibility handshake")
    }

    assert_eq!(
        readiness_as_app(&pool).await,
        (
            latest.0.to_string(),
            SCHEMA_COMPAT_REVISION.to_string(),
            true,
        ),
        "the least-privilege app role sees the current compatible head and floor"
    );

    sqlx::query(
        "insert into memphant.schema_migrations
           (version, schema_compat_revision, migration_kind)
         values ('20990101_001_future_additive', $1, 'additive')",
    )
    .bind(SCHEMA_COMPAT_REVISION)
    .execute(&pool)
    .await
    .expect("record future additive migration");
    assert_eq!(
        readiness_as_app(&pool).await,
        (
            "20990101_001_future_additive".to_string(),
            SCHEMA_COMPAT_REVISION.to_string(),
            true,
        ),
        "an additive database head must not raise the effective compatibility floor"
    );
    app_store
        .ping()
        .await
        .expect("future additive database head remains ready under memphant_app");

    sqlx::query(
        "insert into memphant.schema_migrations
           (version, schema_compat_revision, migration_kind)
         values (
           '20990101_002_mislabeled_incompatible',
           '20990101_002_mislabeled_incompatible',
           'additive'
         )",
    )
    .execute(&pool)
    .await
    .expect("record migration whose declared kind understates its compatibility floor");
    assert_eq!(
        readiness_as_app(&pool).await,
        (
            "20990101_002_mislabeled_incompatible".to_string(),
            "20990101_002_mislabeled_incompatible".to_string(),
            true,
        ),
        "schema_compat_revision is authoritative even when migration_kind is wrong"
    );
    app_store
        .ping()
        .await
        .expect_err("an advanced compatibility floor must fail closed regardless of kind");
    sqlx::query(
        "delete from memphant.schema_migrations
         where version = '20990101_002_mislabeled_incompatible'",
    )
    .execute(&pool)
    .await
    .expect("remove mislabeled migration fixture");

    sqlx::query(
        "insert into memphant.schema_migrations
           (version, schema_compat_revision, migration_kind)
         values ('20990101_003_future_breaking', '20990101_003_future_breaking', 'breaking')",
    )
    .execute(&pool)
    .await
    .expect("record future breaking migration");
    assert_eq!(
        readiness_as_app(&pool).await,
        (
            "20990101_003_future_breaking".to_string(),
            "20990101_003_future_breaking".to_string(),
            true,
        ),
        "the least-privilege app role sees the raised compatibility floor"
    );
    let error = app_store
        .ping()
        .await
        .expect_err("future breaking database head must be unready under memphant_app");
    assert!(
        error.to_string().contains("20990101_003_future_breaking"),
        "readiness error must name the incompatible database floor: {error}"
    );

    sqlx::query(
        "delete from memphant.schema_migrations
         where version in ('20990101_001_future_additive', '20990101_003_future_breaking')",
    )
    .execute(&pool)
    .await
    .expect("remove synthetic future migrations");

    let mut app_tx = pool.begin().await.expect("begin app-role readiness");
    sqlx::query("set local role memphant_app")
        .execute(&mut *app_tx)
        .await
        .expect("assume app role");
    let app_ready: bool = sqlx::query_scalar(
        "select exists (
           select 1 from memphant.schema_migrations
           where version = $1 and schema_compat_revision = $1
         )",
    )
    .bind(SCHEMA_COMPAT_REVISION)
    .fetch_one(&mut *app_tx)
    .await
    .expect("app role can read the compatibility floor");
    assert!(app_ready);

    app_store.pool().close().await;
    let drop_login = format!("drop role {app_login}");
    sqlx::query(sqlx::AssertSqlSafe(drop_login.as_str()))
        .execute(&pool)
        .await
        .expect("drop app readiness login");
}

async fn fresh_tenant(store: &PgStore) -> TenantId {
    let id = store
        .create_tenant(&format!("pg-contract-{}", Uuid::now_v7()))
        .await
        .expect("create tenant");
    TenantId::from_u128(id.as_u128())
}

fn service(store: PgStore) -> MemoryService<PgStore> {
    MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
}

fn context_binding_request(suffix: &str) -> ContextBindingRequest {
    ContextBindingRequest {
        subject: ContextBindingEntityRef {
            external_ref: format!("syndai:user:{suffix}"),
            kind: "user".to_string(),
        },
        actor: ContextBindingEntityRef {
            external_ref: format!("syndai:user:{suffix}"),
            kind: "user".to_string(),
        },
        scope: ContextBindingScopeRef {
            external_ref: format!("syndai:user:{suffix}:root"),
            kind: "user_root".to_string(),
            parent_external_ref: None,
        },
        agent_node: ContextBindingAgentRef {
            external_ref: format!("syndai:user:{suffix}:l0"),
            parent_external_ref: None,
        },
        access_policies: vec![],
    }
}

async fn fresh_memory_context(store: &PgStore, tenant: TenantId) -> ResolvedMemoryContext {
    memphant_store_testkit::bind_context(store, tenant).await
}

fn file_sync_request(
    context: &ResolvedMemoryContext,
    base_fingerprint: String,
    operations: Vec<FileSyncOperation>,
) -> FileSyncRequest {
    FileSyncRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        base_fingerprint,
        plan_sha256: file_sync_plan_sha256(&operations).unwrap(),
        observed_at: CLOCK.0.to_string(),
        operations,
    }
}

fn file_sync_metadata(unit: &CanonicalProjectionUnit) -> FileSyncUnitMetadata {
    FileSyncUnitMetadata {
        unit_id: unit.unit_id,
        kind: unit.kind,
        fact_key: unit.fact_key.clone(),
        predicate: unit.predicate.clone(),
        confidence: unit.confidence,
        valid_from: unit.valid_from.clone(),
        valid_to: unit.valid_to.clone(),
        body_sha256: unit.body_sha256.clone(),
    }
}

struct PgAdmissionDriftEmbedding {
    trigger: tokio::sync::mpsc::UnboundedSender<()>,
    completed: Mutex<std::sync::mpsc::Receiver<bool>>,
}

impl EmbeddingProvider for PgAdmissionDriftEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        self.trigger
            .send(())
            .map_err(|_| EmbedError::Unavailable("admission drift trigger failed".to_string()))?;
        let inserted = self
            .completed
            .lock()
            .expect("drift completion lock")
            .recv_timeout(std::time::Duration::from_secs(5))
            .map_err(|_| EmbedError::Unavailable("admission drift writer timed out".to_string()))?;
        if !inserted {
            return Err(EmbedError::Unavailable(
                "admission drift writer failed".to_string(),
            ));
        }
        Ok(vec![vec![1.0]; texts.len()])
    }

    fn dimensions(&self) -> usize {
        1
    }

    fn id(&self) -> &str {
        "pg-file-sync-admission-drift"
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn file_sync_rejects_nonprojected_admission_drift_before_claiming() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let mut drift = active_projection_unit(&context, "Concurrent belief.");
    drift.kind = MemoryKind::Belief;
    drift.fact_key = Some("profile:concurrent-belief".to_string());
    let (trigger_tx, mut trigger_rx) = tokio::sync::mpsc::unbounded_channel();
    let (completed_tx, completed_rx) = std::sync::mpsc::channel();
    let writer_store = store.clone();
    let writer_context = context.clone();
    let writer = tokio::spawn(async move {
        trigger_rx.recv().await.expect("drift trigger");
        let inserted = async {
            let mut tx = writer_store.begin(&writer_context).await?;
            writer_store.stage_memory_unit(&mut tx, drift).await?;
            writer_store.commit(tx).await
        }
        .await;
        completed_tx
            .send(inserted.is_ok())
            .expect("drift completion");
        inserted
    });
    let embedder = Arc::new(PgAdmissionDriftEmbedding {
        trigger: trigger_tx,
        completed: Mutex::new(completed_rx),
    });
    let service = MemoryService::new(Arc::new(store.clone()), Arc::new(CLOCK), embedder);
    let base = service.canonical_projection(&context).await.unwrap();
    let request = file_sync_request(
        &context,
        base.fingerprint,
        vec![FileSyncOperation::Retain {
            fact_key: "profile:requested".to_string(),
            predicate: "states".to_string(),
            body: "Requested semantic unit.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        }],
    );
    let claim = MutationClaim::new(
        &context,
        MutationVerb::FileSync,
        "pg-file-sync-admission-drift",
        canonical_mutation_request_hash(MutationVerb::FileSync, &request).unwrap(),
    )
    .unwrap();

    let result = service
        .file_sync(&context, "pg-file-sync-admission-drift", request)
        .await;
    assert!(
        matches!(
            result,
            Err(memphant_core::service::ServiceError::SyncConflict(_))
        ),
        "unexpected drift result: {result:?}"
    );
    let open = store.fetch_scope_open_units(&context).await.unwrap();
    assert_eq!(open.len(), 1);
    assert_eq!(open[0].kind, MemoryKind::Belief);
    assert_eq!(
        open[0].fact_key.as_deref(),
        Some("profile:concurrent-belief")
    );
    assert!(
        store
            .lookup_mutation_replay(&context, &claim)
            .await
            .unwrap()
            .is_none()
    );
    writer.await.unwrap().unwrap();
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn file_sync_ordered_preview_matches_native_correction_and_forget_cascades() {
    let store = connect().await;

    let correction_tenant = fresh_tenant(&store).await;
    let correction_context = fresh_memory_context(&store, correction_tenant).await;
    let mut seed = store.begin(&correction_context).await.unwrap();
    let mut source = active_projection_unit(&correction_context, "The source fact is current.");
    source.fact_key = Some("profile:source".to_string());
    let source_id = store.stage_memory_unit(&mut seed, source).await.unwrap();
    let mut dependent =
        active_projection_unit(&correction_context, "The composed dependent is stale.");
    dependent.fact_key = Some("profile:derived".to_string());
    dependent.source_kind = Some("composition".to_string());
    let dependent_id = store.stage_memory_unit(&mut seed, dependent).await.unwrap();
    store
        .stage_memory_edge(
            &mut seed,
            NewMemoryEdge {
                tenant_id: correction_context.tenant_id,
                scope_id: correction_context.scope_id,
                src_id: dependent_id,
                dst_id: source_id,
                kind: MemoryEdgeKind::DerivedFrom,
            },
        )
        .await
        .unwrap();
    store.commit(seed).await.unwrap();

    let correction_service = service(store.clone());
    let base = correction_service
        .canonical_projection(&correction_context)
        .await
        .unwrap();
    let source_metadata = file_sync_metadata(
        base.items
            .iter()
            .find(|unit| unit.unit_id == source_id)
            .unwrap(),
    );
    correction_service
        .file_sync(
            &correction_context,
            "pg-file-sync-correction-dependent-preview",
            file_sync_request(
                &correction_context,
                base.fingerprint,
                vec![
                    FileSyncOperation::Correct {
                        base: source_metadata,
                        body: "The source fact is corrected.".to_string(),
                    },
                    FileSyncOperation::Retain {
                        fact_key: "profile:derived".to_string(),
                        predicate: "states".to_string(),
                        body: "The replacement derived fact is current.".to_string(),
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                    },
                ],
            ),
        )
        .await
        .unwrap();
    let correction_snapshot = store
        .fetch_file_sync_transition_snapshot(&correction_context)
        .await
        .unwrap();
    let dependent_after = correction_snapshot
        .units
        .iter()
        .find(|unit| unit.id == dependent_id)
        .unwrap();
    assert_eq!(dependent_after.state, UnitState::Expired);
    assert!(dependent_after.transaction_to.is_some());
    assert!(correction_snapshot.edges.iter().all(|edge| {
        edge.kind != MemoryEdgeKind::Contradicts
            || (edge.src_id != dependent_id && edge.dst_id != dependent_id)
    }));

    let forget_tenant = fresh_tenant(&store).await;
    let forget_context = fresh_memory_context(&store, forget_tenant).await;
    let mut seed = store.begin(&forget_context).await.unwrap();
    let mut target =
        active_projection_unit(&forget_context, "The selected lineage branch is current.");
    target.fact_key = Some("profile:forget-target".to_string());
    let target_id = store.stage_memory_unit(&mut seed, target).await.unwrap();
    let mut ancestor = active_projection_unit(&forget_context, "The ancestor is historical.");
    ancestor.fact_key = Some("profile:ancestor".to_string());
    let ancestor_id = store.stage_memory_unit(&mut seed, ancestor).await.unwrap();
    let mut sibling = active_projection_unit(&forget_context, "The sibling lineage is stale.");
    sibling.fact_key = Some("profile:lineage".to_string());
    let sibling_id = store.stage_memory_unit(&mut seed, sibling).await.unwrap();
    let mut dependent = active_projection_unit(&forget_context, "The lineage derivative is stale.");
    dependent.fact_key = Some("profile:lineage-dependent".to_string());
    dependent.source_kind = Some("composition".to_string());
    let lineage_dependent_id = store.stage_memory_unit(&mut seed, dependent).await.unwrap();
    for (src_id, dst_id, kind) in [
        (target_id, ancestor_id, MemoryEdgeKind::Supersedes),
        (sibling_id, ancestor_id, MemoryEdgeKind::Supersedes),
        (
            lineage_dependent_id,
            sibling_id,
            MemoryEdgeKind::DerivedFrom,
        ),
    ] {
        store
            .stage_memory_edge(
                &mut seed,
                NewMemoryEdge {
                    tenant_id: forget_context.tenant_id,
                    scope_id: forget_context.scope_id,
                    src_id,
                    dst_id,
                    kind,
                },
            )
            .await
            .unwrap();
    }
    store.commit(seed).await.unwrap();
    sqlx::query(
        "update memphant.memory_unit set state = 'superseded', transaction_to = $7::timestamptz
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and scope_id = $4 and agent_node_id = $5 and id = $6",
    )
    .bind(forget_context.tenant_id.as_uuid())
    .bind(forget_context.data_subject_id.as_uuid())
    .bind(forget_context.subject_generation as i64)
    .bind(forget_context.scope_id.as_uuid())
    .bind(forget_context.agent_node_id.as_uuid())
    .bind(ancestor_id.as_uuid())
    .bind(CLOCK.0)
    .execute(store.pool())
    .await
    .unwrap();

    let forget_service = service(store.clone());
    let base = forget_service
        .canonical_projection(&forget_context)
        .await
        .unwrap();
    let target_metadata = file_sync_metadata(
        base.items
            .iter()
            .find(|unit| unit.unit_id == target_id)
            .unwrap(),
    );
    forget_service
        .file_sync(
            &forget_context,
            "pg-file-sync-forget-lineage-preview",
            file_sync_request(
                &forget_context,
                base.fingerprint,
                vec![
                    FileSyncOperation::Forget {
                        base: target_metadata,
                    },
                    FileSyncOperation::Retain {
                        fact_key: "profile:lineage".to_string(),
                        predicate: "states".to_string(),
                        body: "The replacement lineage fact is current.".to_string(),
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                    },
                    FileSyncOperation::Retain {
                        fact_key: "profile:lineage-dependent".to_string(),
                        predicate: "states".to_string(),
                        body: "The replacement lineage derivative is current.".to_string(),
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                    },
                ],
            ),
        )
        .await
        .unwrap();
    let forget_snapshot = store
        .fetch_file_sync_transition_snapshot(&forget_context)
        .await
        .unwrap();
    for removed_id in [target_id, ancestor_id, sibling_id, lineage_dependent_id] {
        let removed = forget_snapshot
            .units
            .iter()
            .find(|unit| unit.id == removed_id)
            .unwrap();
        assert_eq!(removed.state, UnitState::Deleted);
        assert!(removed.transaction_to.is_some());
    }
    assert!(forget_snapshot.edges.iter().all(|edge| {
        edge.kind != MemoryEdgeKind::Contradicts
            || (![sibling_id, lineage_dependent_id].contains(&edge.src_id)
                && ![sibling_id, lineage_dependent_id].contains(&edge.dst_id))
    }));
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn file_sync_is_atomic_rejects_stale_base_and_serializes_concurrent_batches() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let mut seed = store.begin(&context).await.unwrap();
    let mut city = active_projection_unit(&context, "I live in Oslo.");
    city.fact_key = Some("profile:city".to_string());
    let city_id = store.stage_memory_unit(&mut seed, city).await.unwrap();
    let mut status = active_projection_unit(&context, "Free.");
    status.fact_key = Some("profile:status".to_string());
    let status_id = store.stage_memory_unit(&mut seed, status).await.unwrap();
    let mut pet = active_projection_unit(&context, "I have a cat.");
    pet.fact_key = Some("profile:pet".to_string());
    let pet_id = store.stage_memory_unit(&mut seed, pet).await.unwrap();
    store.commit(seed).await.unwrap();
    let service = service(store.clone());
    let base = service.canonical_projection(&context).await.unwrap();
    let city_metadata = file_sync_metadata(
        base.items
            .iter()
            .find(|unit| unit.unit_id == city_id)
            .unwrap(),
    );
    let pet_metadata = file_sync_metadata(
        base.items
            .iter()
            .find(|unit| unit.unit_id == pet_id)
            .unwrap(),
    );
    let mixed = file_sync_request(
        &context,
        base.fingerprint,
        vec![
            FileSyncOperation::Correct {
                base: city_metadata,
                body: "I live in Lima.".to_string(),
            },
            FileSyncOperation::Retain {
                fact_key: "profile:status".to_string(),
                predicate: "states".to_string(),
                body: "Busy.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            },
            FileSyncOperation::Forget { base: pet_metadata },
        ],
    );
    let first = service
        .file_sync(&context, "pg-file-sync-mixed", mixed.clone())
        .await
        .expect("mixed file-sync batch");
    let replay = service
        .file_sync(&context, "pg-file-sync-mixed", mixed)
        .await
        .expect("exact file-sync replay");
    assert_eq!(first.body(), replay.body());
    let receipt: FileSyncResult = serde_json::from_slice(first.body()).unwrap();
    let [
        FileSyncOperationResult::Correct {
            memory_unit_id: corrected,
            created: corrected_ids,
        },
        FileSyncOperationResult::Retain {
            created: retained_ids,
        },
        FileSyncOperationResult::Forget {
            memory_unit_id: forgotten,
            ..
        },
    ] = &receipt.operations[..]
    else {
        panic!("mixed receipt must preserve operation order and kinds");
    };
    assert_eq!(*corrected, city_id);
    assert_eq!(*forgotten, pet_id);
    assert!(!corrected_ids.is_empty());
    assert!(!retained_ids.is_empty());
    let after_mixed = service.canonical_projection(&context).await.unwrap();
    assert_eq!(after_mixed.fingerprint, receipt.fingerprint);
    let bodies = after_mixed
        .items
        .iter()
        .map(|unit| unit.body.as_str())
        .collect::<Vec<_>>();
    assert!(bodies.contains(&"Busy."));
    assert!(bodies.contains(&"I live in Lima."));
    assert!(!bodies.contains(&"I have a cat."));
    let status_replacement_id = after_mixed
        .items
        .iter()
        .find(|unit| unit.fact_key.as_deref() == Some("profile:status"))
        .expect("current retained status")
        .unit_id;
    assert!(retained_ids.contains(&status_replacement_id));
    let edge_time = RecallTime {
        evaluated_at: CLOCK.0.to_string(),
        transaction_as_of: CLOCK.0.to_string(),
        valid_at: CLOCK.0.to_string(),
    };
    let edges = store
        .fetch_edges(&context, &[status_replacement_id], &edge_time)
        .await
        .expect("native contradiction edges");
    assert!(edges.iter().any(|edge| {
        edge.kind == MemoryEdgeKind::Contradicts
            && edge.src_id == status_id
            && edge.dst_id == status_replacement_id
    }));
    assert!(edges.iter().any(|edge| {
        edge.kind == MemoryEdgeKind::Supersedes
            && edge.src_id == status_replacement_id
            && edge.dst_id == status_id
    }));

    let base = after_mixed;
    sqlx::query(
        "create function memphant.test_file_sync_fail_op_n() returns trigger language plpgsql as $$
         begin
           if new.fact_key = 'profile:fail-op-n' then
             raise exception 'injected file-sync operation failure';
           end if;
           return new;
         end
         $$",
    )
    .execute(store.pool())
    .await
    .unwrap();
    sqlx::query(
        "create trigger test_file_sync_fail_op_n before insert on memphant.memory_unit
         for each row execute function memphant.test_file_sync_fail_op_n()",
    )
    .execute(store.pool())
    .await
    .unwrap();

    let before = store.fetch_scope_open_units(&context).await.unwrap();
    let rollback = file_sync_request(
        &context,
        base.fingerprint.clone(),
        vec![
            FileSyncOperation::Retain {
                fact_key: "profile:rollback-first".to_string(),
                predicate: "states".to_string(),
                body: "This first operation must roll back.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            },
            FileSyncOperation::Retain {
                fact_key: "profile:fail-op-n".to_string(),
                predicate: "states".to_string(),
                body: "This second operation fails inside Postgres.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            },
        ],
    );
    assert!(
        service
            .file_sync(&context, "pg-file-sync-rollback", rollback)
            .await
            .is_err()
    );
    assert_eq!(
        store.fetch_scope_open_units(&context).await.unwrap(),
        before
    );

    let stale = file_sync_request(
        &context,
        "0".repeat(64),
        vec![FileSyncOperation::Retain {
            fact_key: "profile:stale-pg".to_string(),
            predicate: "states".to_string(),
            body: "This stale Postgres write must not land.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        }],
    );
    let stale_result = service
        .file_sync(&context, "pg-file-sync-stale", stale)
        .await;
    assert!(
        matches!(
            stale_result,
            Err(memphant_core::service::ServiceError::SyncConflict(_))
        ),
        "unexpected stale result: {stale_result:?}"
    );
    assert_eq!(
        store.fetch_scope_open_units(&context).await.unwrap(),
        before
    );

    let fresh = service.canonical_projection(&context).await.unwrap();
    let left = file_sync_request(
        &context,
        fresh.fingerprint.clone(),
        vec![FileSyncOperation::Retain {
            fact_key: "profile:concurrent-left".to_string(),
            predicate: "states".to_string(),
            body: "The left concurrent Postgres batch is valid.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        }],
    );
    let right = file_sync_request(
        &context,
        fresh.fingerprint,
        vec![FileSyncOperation::Retain {
            fact_key: "profile:concurrent-right".to_string(),
            predicate: "states".to_string(),
            body: "The right concurrent Postgres batch is valid.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        }],
    );
    let (left, right) = tokio::join!(
        service.file_sync(&context, "pg-file-sync-left", left),
        service.file_sync(&context, "pg-file-sync-right", right),
    );
    assert_eq!(left.is_ok() as u8 + right.is_ok() as u8, 1);
    assert!(
        [left, right]
            .into_iter()
            .filter_map(Result::err)
            .all(|error| matches!(error, memphant_core::service::ServiceError::SyncConflict(_)))
    );
}

fn active_projection_unit(context: &ResolvedMemoryContext, body: &str) -> NewMemoryUnit {
    NewMemoryUnit {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        kind: MemoryKind::Semantic,
        state: UnitState::Active,
        fact_key: Some(format!("projection:{body}")),
        predicate: Some("states".to_string()),
        body: body.to_string(),
        confidence: Some(1.0),
        trust_level: TrustLevel::TrustedSystem,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(context.actor_id),
        source_kind: Some("pg-contract".to_string()),
        source_ref: format!("pg-contract:projection:{body}"),
        observed_at: CLOCK.0.to_string(),
        source_episode_id: None,
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: Vec::new(),
        valid_from: None,
        valid_to: None,
        transaction_from: None,
        transaction_to: None,
    }
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn canonical_projection_respects_resolved_kind_policy() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let mut tx = store.begin(&context).await.expect("begin projection seed");
    store
        .stage_memory_unit(
            &mut tx,
            active_projection_unit(&context, "policy-hidden-semantic"),
        )
        .await
        .expect("stage semantic");
    let mut procedure = active_projection_unit(&context, "policy-visible-procedure");
    procedure.kind = MemoryKind::Procedural;
    procedure.state = UnitState::Validated;
    store
        .stage_memory_unit(&mut tx, procedure)
        .await
        .expect("stage procedure");
    store.commit(tx).await.expect("commit projection seed");

    let mut restricted = context;
    restricted
        .sources_by_kind
        .get_mut(&MemoryKind::Semantic)
        .expect("semantic policy entry")
        .clear();
    let projected = store
        .canonical_projection_units(&restricted, CLOCK.0)
        .await
        .expect("policy-bound projection");
    assert_eq!(
        projected.iter().map(|unit| unit.kind).collect::<Vec<_>>(),
        vec![MemoryKind::Procedural]
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn file_sync_transition_snapshot_and_forget_are_actor_bound() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let mut tx = store.begin(&context).await.expect("begin snapshot seed");
    let own_id = store
        .stage_memory_unit(
            &mut tx,
            active_projection_unit(&context, "actor-owned-unit"),
        )
        .await
        .expect("stage owned unit");
    let foreign_id = store
        .stage_memory_unit(
            &mut tx,
            active_projection_unit(&context, "foreign-actor-unit"),
        )
        .await
        .expect("stage future foreign unit");
    let peer_id = store
        .stage_memory_unit(
            &mut tx,
            active_projection_unit(&context, "actor-owned-peer"),
        )
        .await
        .expect("stage owned peer");
    for (src_id, dst_id) in [(own_id, foreign_id), (foreign_id, peer_id)] {
        store
            .stage_memory_edge(
                &mut tx,
                NewMemoryEdge {
                    tenant_id: tenant,
                    scope_id: context.scope_id,
                    src_id,
                    dst_id,
                    kind: MemoryEdgeKind::Supersedes,
                },
            )
            .await
            .expect("stage supersedes bridge");
    }
    store.commit(tx).await.expect("commit snapshot seed");

    let foreign_actor_id = Uuid::now_v7();
    let mut raw_tx = store
        .pool()
        .begin()
        .await
        .expect("begin actor reassignment");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant.as_uuid())
        .execute(&mut *raw_tx)
        .await
        .expect("bind tenant");
    sqlx::query(
        "insert into memphant.actor
           (id, tenant_id, data_subject_id, kind, external_ref, trust_level)
         values ($1, $2, $3, 'user', $4, 'trusted_user')",
    )
    .bind(foreign_actor_id)
    .bind(tenant.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(format!("pg-contract:foreign-actor:{foreign_actor_id}"))
    .execute(&mut *raw_tx)
    .await
    .expect("insert foreign actor");
    sqlx::query(
        "update memphant.memory_unit set actor_id = $7
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and scope_id = $4 and agent_node_id = $5 and id = $6",
    )
    .bind(tenant.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .bind(context.scope_id.as_uuid())
    .bind(context.agent_node_id.as_uuid())
    .bind(foreign_id.as_uuid())
    .bind(foreign_actor_id)
    .execute(&mut *raw_tx)
    .await
    .expect("reassign unit actor");
    raw_tx.commit().await.expect("commit actor reassignment");

    let snapshot = store
        .fetch_file_sync_transition_snapshot(&context)
        .await
        .expect("actor-bound snapshot");
    assert_eq!(
        snapshot
            .units
            .iter()
            .map(|unit| unit.id)
            .collect::<Vec<_>>(),
        vec![own_id, peer_id]
    );
    assert!(snapshot.edges.is_empty());

    let service = service(store.clone());
    let projection = service.canonical_projection(&context).await.unwrap();
    let own = file_sync_metadata(
        projection
            .items
            .iter()
            .find(|unit| unit.unit_id == own_id)
            .expect("owned unit in projection"),
    );
    service
        .file_sync(
            &context,
            "pg-file-sync-actor-bridge-forget",
            file_sync_request(
                &context,
                projection.fingerprint,
                vec![FileSyncOperation::Forget { base: own }],
            ),
        )
        .await
        .expect("actor-bound forget");
    let after = store
        .fetch_file_sync_transition_snapshot(&context)
        .await
        .expect("post-forget actor-bound snapshot");
    assert_eq!(
        after
            .units
            .iter()
            .find(|unit| unit.id == own_id)
            .unwrap()
            .state,
        UnitState::Deleted
    );
    assert_eq!(
        after
            .units
            .iter()
            .find(|unit| unit.id == peer_id)
            .unwrap()
            .state,
        UnitState::Active,
        "forget must not traverse through a foreign actor's unit"
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn source_forget_does_not_cross_a_foreign_actor_bridge() {
    use memphant_core::ForgetWrite;
    use memphant_types::ForgetTarget;

    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;

    let episode = retain_episode(
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
            source_ref: format!("pg-contract:forget-episode:{}", Uuid::now_v7()),
            observed_at: CLOCK.0.to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: None,
            subject: None,
            predicate: None,
            body: "Episode source for an actor-bound forget lineage.".to_string(),
            compiler_version: "compiler-pg-contract".to_string(),
        },
    )
    .await
    .expect("retain episode source");
    let resource = retain_resource(
        &store,
        &context,
        RetainResourceRequest {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            uri: format!("memphant://forget-resource/{}", Uuid::now_v7()),
            source_ref: format!("pg-contract:forget-resource:{}", Uuid::now_v7()),
            observed_at: CLOCK.0.to_string(),
            kind: None,
            content_hash: format!("sha256:{}", Uuid::now_v7()),
            mime_type: "text/plain".to_string(),
            revision: None,
            body: Some("Resource source for an actor-bound forget lineage.".to_string()),
            source_trust: TrustLevel::TrustedUser,
            compiler_version: "compiler-pg-contract".to_string(),
        },
    )
    .await
    .expect("retain resource source");

    for (label, target) in [
        ("episode", ForgetTarget::Episode(episode.episode_id)),
        ("resource", ForgetTarget::Resource(resource.resource_id)),
    ] {
        let mut tx = store.begin(&context).await.expect("begin lineage seed");
        let mut source_unit = active_projection_unit(&context, &format!("{label}-source-unit"));
        match target {
            ForgetTarget::Episode(id) => source_unit.source_episode_id = Some(id),
            ForgetTarget::Resource(id) => source_unit.source_resource_id = Some(id),
            ForgetTarget::MemoryUnit(_) => unreachable!(),
        }
        let source_unit_id = store
            .stage_memory_unit(&mut tx, source_unit)
            .await
            .expect("stage source-linked unit");
        let foreign_bridge_id = store
            .stage_memory_unit(
                &mut tx,
                active_projection_unit(&context, &format!("{label}-foreign-bridge")),
            )
            .await
            .expect("stage future foreign bridge");
        let own_grandchild_id = store
            .stage_memory_unit(
                &mut tx,
                active_projection_unit(&context, &format!("{label}-owned-grandchild")),
            )
            .await
            .expect("stage owned grandchild");
        for (src_id, dst_id) in [
            (foreign_bridge_id, source_unit_id),
            (own_grandchild_id, foreign_bridge_id),
        ] {
            store
                .stage_memory_edge(
                    &mut tx,
                    NewMemoryEdge {
                        tenant_id: tenant,
                        scope_id: context.scope_id,
                        src_id,
                        dst_id,
                        kind: MemoryEdgeKind::Supersedes,
                    },
                )
                .await
                .expect("stage descendant edge");
        }
        store.commit(tx).await.expect("commit lineage seed");

        let foreign_actor_id = Uuid::now_v7();
        let mut raw_tx = store
            .pool()
            .begin()
            .await
            .expect("begin actor reassignment");
        sqlx::query("select memphant.bind_tenant($1)")
            .bind(tenant.as_uuid())
            .execute(&mut *raw_tx)
            .await
            .expect("bind tenant");
        sqlx::query(
            "insert into memphant.actor
               (id, tenant_id, data_subject_id, kind, external_ref, trust_level)
             values ($1, $2, $3, 'user', $4, 'trusted_user')",
        )
        .bind(foreign_actor_id)
        .bind(tenant.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(format!(
            "pg-contract:foreign-{label}-actor:{foreign_actor_id}"
        ))
        .execute(&mut *raw_tx)
        .await
        .expect("insert foreign actor");
        sqlx::query(
            "update memphant.memory_unit set actor_id = $7
             where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
               and scope_id = $4 and agent_node_id = $5 and id = $6",
        )
        .bind(tenant.as_uuid())
        .bind(context.data_subject_id.as_uuid())
        .bind(context.subject_generation as i64)
        .bind(context.scope_id.as_uuid())
        .bind(context.agent_node_id.as_uuid())
        .bind(foreign_bridge_id.as_uuid())
        .bind(foreign_actor_id)
        .execute(&mut *raw_tx)
        .await
        .expect("reassign bridge actor");
        raw_tx.commit().await.expect("commit actor reassignment");

        let forgotten = store
            .apply_forget(
                &context,
                ForgetWrite {
                    target,
                    now: CLOCK.0.to_string(),
                },
            )
            .await
            .expect("forget actor-owned source");
        assert!(forgotten.invalidated_units.contains(&source_unit_id));
        assert!(
            !forgotten.invalidated_units.contains(&own_grandchild_id),
            "{label} forget must stop before a foreign actor bridge"
        );

        let remaining = store
            .fetch_units_by_ids(&context, &[source_unit_id, own_grandchild_id])
            .await
            .expect("fetch actor-owned lineage after forget");
        assert_eq!(
            remaining
                .iter()
                .find(|unit| unit.id == source_unit_id)
                .expect("source unit remains addressable")
                .state,
            UnitState::Deleted
        );
        assert_eq!(
            remaining
                .iter()
                .find(|unit| unit.id == own_grandchild_id)
                .expect("owned grandchild remains addressable")
                .state,
            UnitState::Active,
            "{label} forget must preserve an owned descendant behind a foreign actor"
        );
    }
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn canonical_projection_filters_bitemporal_trust_bounds_and_orders_by_uuid() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;

    let mut first_tx = store
        .begin(&context)
        .await
        .expect("begin first projection tx");
    let mut visible_at_lower_bound = active_projection_unit(&context, "visible-lower-bound");
    visible_at_lower_bound.valid_from = Some(CLOCK.0.to_string());
    let lower_bound_id = store
        .stage_memory_unit(&mut first_tx, visible_at_lower_bound)
        .await
        .expect("stage lower-bound visible unit");

    let mut second_tx = store
        .begin(&context)
        .await
        .expect("begin second projection tx");
    let open_id = store
        .stage_memory_unit(
            &mut second_tx,
            active_projection_unit(&context, "visible-open-interval"),
        )
        .await
        .expect("stage open visible unit");
    assert!(
        lower_bound_id.as_uuid() < open_id.as_uuid(),
        "the UUID-order assertion needs two independently staged ids"
    );

    let mut quarantined = active_projection_unit(&context, "quarantined-trust");
    quarantined.trust_level = TrustLevel::Quarantined;
    store
        .stage_memory_unit(&mut second_tx, quarantined)
        .await
        .expect("stage quarantined unit");

    let mut valid_to_at_boundary = active_projection_unit(&context, "valid-to-boundary");
    valid_to_at_boundary.valid_to = Some(CLOCK.0.to_string());
    store
        .stage_memory_unit(&mut second_tx, valid_to_at_boundary)
        .await
        .expect("stage exclusive-valid-to unit");

    let mut future_valid = active_projection_unit(&context, "future-valid-from");
    future_valid.valid_from = Some("2030-01-01T00:00:01Z".to_string());
    store
        .stage_memory_unit(&mut second_tx, future_valid)
        .await
        .expect("stage future-valid unit");

    let mut expired_valid = active_projection_unit(&context, "expired-valid-to");
    expired_valid.valid_to = Some("2029-12-31T23:59:59Z".to_string());
    store
        .stage_memory_unit(&mut second_tx, expired_valid)
        .await
        .expect("stage expired-valid unit");

    let mut future_transaction = active_projection_unit(&context, "future-transaction-from");
    future_transaction.transaction_from = Some("2030-01-01T00:00:01Z".to_string());
    store
        .stage_memory_unit(&mut second_tx, future_transaction)
        .await
        .expect("stage future-transaction unit");

    let mut closed_transaction = active_projection_unit(&context, "closed-transaction-to");
    closed_transaction.transaction_to = Some(CLOCK.0.to_string());
    store
        .stage_memory_unit(&mut second_tx, closed_transaction)
        .await
        .expect("stage closed-transaction unit");

    // Reverse physical insertion order. The read contract must still use UUID
    // order, independent of commit order.
    store
        .commit(second_tx)
        .await
        .expect("commit second projection tx");
    store
        .commit(first_tx)
        .await
        .expect("commit first projection tx");

    let projected = store
        .canonical_projection_units(&context, CLOCK.0)
        .await
        .expect("read canonical projection");
    assert_eq!(
        projected.iter().map(|unit| unit.id).collect::<Vec<_>>(),
        vec![lower_bound_id, open_id]
    );
    assert_eq!(
        projected
            .iter()
            .map(|unit| unit.body.as_str())
            .collect::<Vec<_>>(),
        vec!["visible-lower-bound", "visible-open-interval"]
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn context_binding_is_atomic_replay_safe_and_tenant_isolated() {
    let store = connect().await;
    let tenant_a = fresh_tenant(&store).await;
    let tenant_b = fresh_tenant(&store).await;
    let suffix = Uuid::now_v7().to_string();
    let request = context_binding_request(&suffix);
    let client_ref = format!("syndai:binding:{suffix}");

    let created = store
        .resolve_context_binding(tenant_a, client_ref.clone(), request.clone())
        .await
        .expect("create binding");
    let replayed = store
        .resolve_context_binding(tenant_a, client_ref.clone(), request.clone())
        .await
        .expect("replay binding");
    assert_eq!(replayed, created);

    let other_tenant = store
        .resolve_context_binding(tenant_b, client_ref.clone(), request.clone())
        .await
        .expect("same external refs are isolated by tenant");
    assert_ne!(other_tenant.subject_id, created.subject_id);
    assert_ne!(other_tenant.scope_id, created.scope_id);

    let pool = sqlx::PgPool::connect(&db_url())
        .await
        .expect("connect raw pool");
    let mut generation_tx = pool.begin().await.expect("begin generation update");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant_a.as_uuid())
        .execute(&mut *generation_tx)
        .await
        .expect("bind generation tenant");
    sqlx::query("update memphant.subject set generation = 7 where tenant_id = $1 and id = $2")
        .bind(tenant_a.as_uuid())
        .bind(created.subject_id.as_uuid())
        .execute(&mut *generation_tx)
        .await
        .expect("advance subject generation");
    generation_tx.commit().await.expect("commit generation");

    let mut child_request = request.clone();
    child_request.scope = ContextBindingScopeRef {
        external_ref: format!("syndai:user:{suffix}:workspace"),
        kind: "agent_workspace".to_string(),
        parent_external_ref: Some(format!("syndai:user:{suffix}:root")),
    };
    child_request.agent_node = ContextBindingAgentRef {
        external_ref: format!("syndai:user:{suffix}:l1-a"),
        parent_external_ref: Some(format!("syndai:user:{suffix}:l0")),
    };
    child_request.access_policies.clear();
    let child = store
        .resolve_context_binding(
            tenant_a,
            format!("syndai:binding:{suffix}:l1-a"),
            child_request.clone(),
        )
        .await
        .expect("create child binding");
    assert_eq!(child.subject_id, created.subject_id);
    assert_eq!(child.actor_id, created.actor_id);
    assert_eq!(child.subject_generation, 7);
    assert_eq!(child.agent_level, 1);

    child_request.agent_node.external_ref = format!("syndai:user:{suffix}:l1-b");
    let sibling = store
        .resolve_context_binding(
            tenant_a,
            format!("syndai:binding:{suffix}:l1-b"),
            child_request,
        )
        .await
        .expect("create a second agent in the shared scope");
    assert_eq!(sibling.scope_id, child.scope_id);
    assert_ne!(sibling.agent_node_id, child.agent_node_id);

    let mut policy_update = request.clone();
    policy_update.access_policies = vec![ContextBindingAccessPolicy::Grant {
        source_scope_external_ref: format!("syndai:user:{suffix}:workspace"),
        source_agent_node_external_ref: format!("syndai:user:{suffix}:l1-a"),
        kind: MemoryKind::Resource,
    }];
    let updated = store
        .resolve_context_binding(tenant_a, client_ref.clone(), policy_update)
        .await
        .expect("policy-only update");
    assert_eq!(updated.subject_id, created.subject_id);
    assert_eq!(updated.scope_id, created.scope_id);
    assert_ne!(updated.policy_revision, created.policy_revision);

    let alias_error = store
        .resolve_context_binding(tenant_a, format!("{client_ref}:alias"), request.clone())
        .await
        .expect_err("one identity cannot hide behind two client refs");
    assert!(matches!(
        alias_error,
        memphant_core::StoreError::Conflict(_)
    ));

    let mut conflicting = request;
    conflicting.scope.kind = "agent_workspace".to_string();
    let error = store
        .resolve_context_binding(tenant_a, client_ref, conflicting)
        .await
        .expect_err("immutable replay must conflict");
    assert!(matches!(error, memphant_core::StoreError::Conflict(_)));
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn context_binding_accepts_stale_uuid_refs() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let uid = "7c0ae4e7-6b5a-42a2-891b-0ccf553bfe7f";
    let external_ref = format!("stale:{uid}");
    let request = ContextBindingRequest {
        subject: ContextBindingEntityRef {
            external_ref: external_ref.clone(),
            kind: "user".to_string(),
        },
        actor: ContextBindingEntityRef {
            external_ref: external_ref.clone(),
            kind: "user".to_string(),
        },
        scope: ContextBindingScopeRef {
            external_ref: format!("{external_ref}:root"),
            kind: "stale_record".to_string(),
            parent_external_ref: None,
        },
        agent_node: ContextBindingAgentRef {
            external_ref: format!("{external_ref}:agent"),
            parent_external_ref: None,
        },
        access_policies: vec![],
    };

    store
        .resolve_context_binding(tenant, external_ref, request)
        .await
        .expect("bind STALE UUID context");
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn context_binding_external_refs_are_isolated_by_subject() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let suffix = Uuid::now_v7().to_string();
    let first_request = context_binding_request(&format!("subject-a:{suffix}"));
    let mut second_request = first_request.clone();
    second_request.subject.external_ref = format!("syndai:user:subject-b:{suffix}");

    let first = store
        .resolve_context_binding(tenant, format!("subject-a:{suffix}"), first_request)
        .await
        .expect("bind first subject");
    let second = store
        .resolve_context_binding(tenant, format!("subject-b:{suffix}"), second_request)
        .await
        .expect("the same external actor, scope, and agent refs are subject-local");

    assert_ne!(second.subject_id, first.subject_id);
    assert_ne!(second.actor_id, first.actor_id);
    assert_ne!(second.scope_id, first.scope_id);
    assert_ne!(second.agent_node_id, first.agent_node_id);
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn retain_persists_subject_context_and_rejects_mixed_bindings() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let suffix = Uuid::now_v7().to_string();
    let first = store
        .resolve_context_binding(
            tenant,
            format!("retain-subject-a:{suffix}"),
            context_binding_request(&format!("retain-a:{suffix}")),
        )
        .await
        .expect("bind first subject");
    let second = store
        .resolve_context_binding(
            tenant,
            format!("retain-subject-b:{suffix}"),
            context_binding_request(&format!("retain-b:{suffix}")),
        )
        .await
        .expect("bind second subject");
    let first_context = store
        .resolve_memory_context(
            tenant,
            first.subject_id,
            first.actor_id,
            first.scope_id,
            first.agent_node_id,
        )
        .await
        .expect("resolve first context");
    let service = service(store.clone());

    let retained = service
        .retain(
            &first_context,
            "pg-contract-subject-bound",
            TrustLevel::TrustedUser,
            RetainEpisodeHttpRequest {
                subject_id: first.subject_id,
                scope_id: first.scope_id,
                actor_id: first.actor_id,
                agent_node_id: first.agent_node_id,
                subject_generation: first.subject_generation,
                source_ref: "pg-contract:episode".to_string(),
                observed_at: CLOCK.0.to_string(),
                payload: RetainPayload::Episode(RetainEpisodePayload {
                    source_kind: "user".to_string(),
                    body: "subject-bound Postgres memory".to_string(),
                }),
            },
        )
        .await
        .expect("retain with one resolved context");
    let retained_response: RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).unwrap();
    let episode = store
        .fetch_episode(
            &first_context,
            retained_response.episode_id.expect("episode id"),
        )
        .await
        .expect("fetch episode")
        .expect("stored episode");
    assert_eq!(episode.data_subject_id, first.subject_id);
    assert_eq!(episode.agent_node_id, first.agent_node_id);
    assert_eq!(episode.subject_generation, first.subject_generation);

    let reflected = service
        .run_worker_tick_scoped(
            memphant_store_testkit::tenant_filter(&first_context),
            usize::MAX,
        )
        .await
        .expect("reflect subject-bound episode");
    assert_eq!(reflected, 1);
    let page = store
        .scope_memory_page(&first_context, None, 10)
        .await
        .expect("list reflected units");
    assert!(!page.items.is_empty());
    assert!(page.items.iter().all(|unit| {
        unit.data_subject_id == first.subject_id
            && unit.agent_node_id == first.agent_node_id
            && unit.subject_generation == first.subject_generation
    }));

    let mixed = service
        .retain(
            &first_context,
            "pg-contract-mixed",
            TrustLevel::TrustedUser,
            RetainEpisodeHttpRequest {
                subject_id: first.subject_id,
                scope_id: second.scope_id,
                actor_id: second.actor_id,
                agent_node_id: second.agent_node_id,
                subject_generation: first.subject_generation,
                source_ref: "pg-contract:episode-mixed".to_string(),
                observed_at: CLOCK.0.to_string(),
                payload: RetainPayload::Episode(RetainEpisodePayload {
                    source_kind: "user".to_string(),
                    body: "must not cross subjects".to_string(),
                }),
            },
        )
        .await;
    assert!(
        mixed.is_err(),
        "mixed subject/scope context must fail closed"
    );
}

fn structured_service(
    store: PgStore,
    operations: Vec<StructuredStateOp>,
) -> MemoryService<PgStore> {
    service(store)
        .with_structured_state_provider(Arc::new(FixedStructuredProvider::new(operations)))
}

fn structured_upsert(
    body: &str,
    quote: &str,
    value: &str,
    valid_from: &str,
    valid_to: &str,
) -> StructuredStateOp {
    let start = body.find(quote).expect("structured evidence quote exists");
    StructuredStateOp {
        operation: StructuredStateOperation::Create,
        namespace: "profile".to_string(),
        item_key: "home_city".to_string(),
        target_unit_ids: vec![],
        fields: BTreeMap::from([("value".to_string(), serde_json::json!(value))]),
        evidence_quote: quote.to_string(),
        source_span: format!("{start}-{}", start + quote.len()),
        valid_from: Some(valid_from.to_string()),
        valid_to: Some(valid_to.to_string()),
    }
}

fn retain_request(
    context: &ResolvedMemoryContext,
    body: &str,
    subject: Option<&str>,
) -> RetainRequest {
    RetainRequest {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_kind: "user".to_string(),
        source_ref: "pg-contract:retain-request".to_string(),
        observed_at: CLOCK.0.to_string(),
        source_trust: TrustLevel::TrustedUser,
        subject_hint: subject.map(str::to_string),
        subject: subject.map(str::to_string),
        predicate: subject.map(|_| "value".to_string()),
        body: body.to_string(),
        compiler_version: "compiler-pg-contract".to_string(),
    }
}

fn recall_request(context: &ResolvedMemoryContext, query: &str) -> RecallRequest {
    RecallRequest {
        context: context.clone(),
        query: query.to_string(),
        k: 4,
        budget_tokens: 256,
        mode: RecallMode::Fast,
        include_beliefs: true,
        edge_expansion_enabled: true,
        context_packing_abstention_enabled: true,
        procedure_recall_enabled: true,
        decay_enabled: true,
        engine_version: "pg-contract-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// The shared `MemoryStore` contract, run against `PgStore`. These are the
/// SAME scenarios `memphant-core` runs against `InMemoryStore` — one suite, two
/// backends, so a per-store trait divergence fails on at least one of them. The
/// pg-specific tests below exercise behaviour the trait alone can't express
/// (fresh-pool durability, the job queue, pgvector, SQL-level invariants).
struct PgHarness(PgStore);

impl StoreHarness for PgHarness {
    type Store = PgStore;

    fn store(&self) -> &PgStore {
        &self.0
    }

    async fn fresh_tenant(&self) -> TenantId {
        fresh_tenant(&self.0).await
    }
}

async fn contract_harness() -> PgHarness {
    PgHarness(connect().await)
}

macro_rules! pg_contract_test {
    ($name:ident) => {
        #[tokio::test]
        #[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
        async fn $name() {
            memphant_store_testkit::$name(&contract_harness().await).await;
        }
    };
}

pg_contract_test!(retain_episode_dedups_and_enqueues);
pg_contract_test!(retain_resource_registers_and_enqueues);
pg_contract_test!(resource_acl_round_trips_empty_and_non_empty);
pg_contract_test!(deep_snapshot_is_authorized_stable_and_read_only);
pg_contract_test!(deep_snapshot_binds_historical_rectangle_only);
pg_contract_test!(deep_snapshot_does_not_treat_actor_as_read_partition);
pg_contract_test!(commit_publishes_staged_episode_and_unit);
pg_contract_test!(drop_rolls_back_staged_rows);
pg_contract_test!(recall_candidates_are_tenant_and_scope_scoped);
pg_contract_test!(trace_is_tenant_bound);
pg_contract_test!(review_marks_credit_synthetic_sources_and_stay_trace_bound);
pg_contract_test!(forget_by_episode_blocks_recompilation);
pg_contract_test!(forget_by_episode_cascades_through_correction_lineage);
pg_contract_test!(forget_source_cascades_to_composed_dependent);
pg_contract_test!(forget_by_unit_closes_and_purges);
pg_contract_test!(fetch_episodes_honors_large_limit);
pg_contract_test!(semantic_update_supersedes_unit_aged_past_recall_window);
pg_contract_test!(scope_memory_page_paginates_without_overlap);

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn resource_acl_unknown_shape_fails_closed() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let retained = retain_resource(
        &store,
        &context,
        RetainResourceRequest {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            uri: "memphant://invalid-acl".to_string(),
            source_ref: "pg-contract:invalid-acl".to_string(),
            observed_at: CLOCK.0.to_string(),
            kind: None,
            content_hash: "sha256:invalid-acl".to_string(),
            mime_type: "text/plain".to_string(),
            revision: None,
            body: Some("invalid ACL fixture".to_string()),
            source_trust: TrustLevel::TrustedUser,
            compiler_version: "compiler-pg-contract".to_string(),
        },
    )
    .await
    .expect("retain resource");

    let pool = sqlx::PgPool::connect(&db_url())
        .await
        .expect("connect raw pool");
    let mut tx = pool.begin().await.expect("begin raw transaction");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant.as_uuid())
        .execute(&mut *tx)
        .await
        .expect("bind tenant");
    sqlx::query("update memphant.resource set acl = $1 where tenant_id = $2 and id = $3")
        .bind(serde_json::json!({"future_gate": true}))
        .bind(tenant.as_uuid())
        .bind(retained.resource_id.as_uuid())
        .execute(&mut *tx)
        .await
        .expect("corrupt ACL fixture");
    tx.commit().await.expect("commit malformed ACL");

    let error = store
        .fetch_resource(&context, retained.resource_id)
        .await
        .expect_err("unknown ACL data must fail closed");
    assert!(matches!(error, memphant_core::StoreError::Backend(_)));

    // This suite shares one ephemeral database so process-level worker smoke
    // tests can exercise the rows produced by the store contracts. Restore the
    // adversarial fixture after proving the fail-closed read; otherwise this
    // intentionally malformed resource poisons the later fleet drain.
    let mut tx = pool.begin().await.expect("begin cleanup transaction");
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(tenant.as_uuid())
        .execute(&mut *tx)
        .await
        .expect("bind cleanup tenant");
    sqlx::query("update memphant.resource set acl = '{}'::jsonb where tenant_id = $1 and id = $2")
        .bind(tenant.as_uuid())
        .bind(retained.resource_id.as_uuid())
        .execute(&mut *tx)
        .await
        .expect("restore valid ACL fixture");
    tx.commit().await.expect("commit ACL cleanup");
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn durability_write_with_pool_a_read_with_fresh_pool_b() {
    let store_a = connect().await;
    let tenant = fresh_tenant(&store_a).await;
    let context = fresh_memory_context(&store_a, tenant).await;
    let svc_a = service(store_a);
    retain_episode(
        svc_a.store(),
        &context,
        retain_request(
            &context,
            "Durable release region is Taipei.",
            Some("release region"),
        ),
    )
    .await
    .expect("retain");
    svc_a
        .run_worker_tick_scoped(memphant_store_testkit::tenant_filter(&context), usize::MAX)
        .await
        .expect("reflect");

    // A COMPLETELY fresh pool must see the compiled unit.
    let store_b = connect().await;
    let recalled = recall(
        &store_b,
        recall_request(&context, "Where is the durable release region?"),
        None,
        &CLOCK,
    )
    .await
    .expect("recall via fresh pool");
    assert_eq!(recalled.items[0].body, "Durable release region is Taipei.");

    // The recall's trace is durable and tenant-bound through yet another pool.
    let store_c = connect().await;
    let trace = store_c
        .trace_by_id(&context, recalled.trace_id)
        .await
        .expect("trace lookup");
    assert!(trace.is_some(), "trace persists across pools");
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn claim_reflect_jobs_is_disjoint_and_does_not_reclaim_fresh_claims() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;
    retain_episode(
        &store,
        &context,
        retain_request(&context, "Claim scenario fact one.", None),
    )
    .await
    .expect("retain one");
    retain_episode(
        &store,
        &context,
        retain_request(&context, "Claim scenario fact two.", None),
    )
    .await
    .expect("retain two");

    let filter = JobFilter {
        tenant: Some(tenant),
        scope: Some(scope),
    };
    let first = store.claim_reflect_jobs(filter, 10).await.expect("claim 1");
    let second = store.claim_reflect_jobs(filter, 1).await.expect("claim 2");
    assert_eq!(first.len(), 2, "one owner claims the contiguous scope lane");
    assert!(
        second.is_empty(),
        "a fresh scope-lane lease blocks a second owner"
    );

    // Both jobs are claimed; nothing is left to claim inside the window.
    let third = store.claim_reflect_jobs(filter, 10).await.expect("claim 3");
    assert!(third.is_empty());
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn concurrent_workers_cannot_split_a_scope_lane_and_reclaim_reuses_preparation() {
    let store_a = connect().await;
    let store_b = connect().await;
    let tenant = fresh_tenant(&store_a).await;
    let context = fresh_memory_context(&store_a, tenant).await;
    let scope = context.scope_id;
    for index in 0..6 {
        retain_episode(
            &store_a,
            &context,
            retain_request(&context, &format!("Ordered lane fact {index}."), None),
        )
        .await
        .expect("retain lane item");
    }
    let expected: Vec<Uuid> = sqlx::query_scalar(
        "select id from memphant.job_state
         where tenant_id = $1 and scope_id = $2
         order by queue_order limit 4",
    )
    .bind(tenant.as_uuid())
    .bind(scope.as_uuid())
    .fetch_all(store_a.pool())
    .await
    .expect("canonical jobs");
    let filter = JobFilter {
        tenant: Some(tenant),
        scope: Some(scope),
    };
    let (left, right) = tokio::join!(
        store_a.claim_reflect_jobs(filter, 4),
        store_b.claim_reflect_jobs(filter, 4)
    );
    let left = left.expect("left claim");
    let right = right.expect("right claim");
    assert!(left.is_empty() ^ right.is_empty(), "only one lane owner");
    let claimed = if left.is_empty() { right } else { left };
    assert_eq!(claimed.len(), 4);
    assert_eq!(
        claimed
            .iter()
            .map(|row| row.job.id.as_uuid())
            .collect::<Vec<_>>(),
        expected,
        "the owner receives a canonical contiguous prefix"
    );

    let prepared_job = claimed[1].job.id;
    store_a
        .store_prepared_structured_state(&claimed[1], Vec::new())
        .await
        .expect("persist preparation");
    assert_eq!(
        store_b
            .fetch_prepared_structured_state(&claimed[1])
            .await
            .expect("fresh-pool preparation read"),
        Some(Vec::new())
    );

    sqlx::query(
        "update memphant.job_state
         set claimed_at = now() - interval '16 minutes'
         where tenant_id = $1 and scope_id = $2 and state = 'running'",
    )
    .bind(tenant.as_uuid())
    .bind(scope.as_uuid())
    .execute(store_a.pool())
    .await
    .expect("expire lane lease");
    let reclaimed = store_b
        .claim_reflect_jobs(filter, 4)
        .await
        .expect("reclaim stale lane");
    assert_eq!(
        reclaimed
            .iter()
            .map(|row| row.job.id.as_uuid())
            .collect::<Vec<_>>(),
        expected
    );
    store_a
        .complete_reflect_job(&claimed[0])
        .await
        .expect("stale completion is a no-op");
    let state: String =
        sqlx::query_scalar("select state from memphant.job_state where tenant_id = $1 and id = $2")
            .bind(tenant.as_uuid())
            .bind(reclaimed[0].job.id.as_uuid())
            .fetch_one(store_a.pool())
            .await
            .expect("reclaimed state");
    assert_eq!(state, "running", "attempt 1 cannot complete attempt 2");
    store_a
        .release_reflect_job(&claimed[1], 0, "stale release".to_string())
        .await
        .expect("stale release is a no-op");
    store_b
        .release_reflect_job(&reclaimed[1], 0, "retry preparation".to_string())
        .await
        .expect("current release");
    let released_state: String =
        sqlx::query_scalar("select state from memphant.job_state where tenant_id = $1 and id = $2")
            .bind(tenant.as_uuid())
            .bind(prepared_job.as_uuid())
            .fetch_one(store_a.pool())
            .await
            .expect("released state");
    assert_eq!(released_state, "queued");
    assert_eq!(
        store_b
            .fetch_prepared_structured_state(&reclaimed[1])
            .await
            .expect("reclaimed preparation"),
        Some(Vec::new()),
        "reclaim must not lose the paid preparation"
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn source_job_precedes_its_scope_barrier_by_queue_order() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    retain_episode(
        &store,
        &context,
        retain_request(&context, "Source before scope barrier.", None),
    )
    .await
    .expect("retain source");
    let mut tx = store.begin(&context).await.expect("begin scope barrier");
    let scope_job = store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                episode_id: None,
                resource_id: None,
                kind: ReflectJobKind::ReflectScope,
                compiler_version: memphant_types::COMPILER_VERSION.to_string(),
                subject: None,
                predicate: None,
            },
        )
        .await
        .expect("enqueue scope barrier");
    store.commit(tx).await.expect("commit scope barrier");

    let ordered: Vec<(Uuid, i64)> = sqlx::query_as(
        "select id, queue_order from memphant.job_state
         where tenant_id = $1 and data_subject_id = $2 and scope_id = $3
         order by queue_order",
    )
    .bind(tenant.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.scope_id.as_uuid())
    .fetch_all(store.pool())
    .await
    .expect("ordered lane");
    assert_eq!(ordered.len(), 2);
    assert!(ordered[0].1 < ordered[1].1);
    assert_eq!(ordered[1].0, scope_job.as_uuid());

    let claimed = store
        .claim_reflect_jobs(
            JobFilter {
                tenant: Some(tenant),
                scope: Some(context.scope_id),
            },
            2,
        )
        .await
        .expect("claim ordered lane");
    assert_eq!(claimed[0].job.kind, ReflectJobKind::ReflectEpisode);
    assert_eq!(claimed[1].job.kind, ReflectJobKind::ReflectScope);
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn terminal_failure_is_dead_lettered_once_with_diagnostic() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;

    retain_episode(
        &store,
        &context,
        retain_request(&context, "Terminal provider output.", None),
    )
    .await
    .expect("retain");
    let job = store
        .claim_reflect_jobs(
            JobFilter {
                tenant: Some(tenant),
                scope: Some(scope),
            },
            1,
        )
        .await
        .expect("claim")
        .pop()
        .expect("job");

    store
        .fail_reflect_job(&job, "terminal invalid structured output".to_string())
        .await
        .expect("terminal fail");

    let row: (String, Option<String>) = sqlx::query_as(
        "select state, last_error from memphant.job_state where tenant_id = $1 and id = $2",
    )
    .bind(tenant.as_uuid())
    .bind(job.job.id.as_uuid())
    .fetch_one(store.pool())
    .await
    .expect("terminal state");
    assert_eq!(row.0, "dead");
    assert_eq!(row.1.as_deref(), Some("terminal invalid structured output"));
    assert!(
        store
            .claim_reflect_jobs(
                JobFilter {
                    tenant: Some(tenant),
                    scope: Some(scope),
                },
                1,
            )
            .await
            .expect("reclaim")
            .is_empty()
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn exhausted_jobs_dead_letter_and_surface_in_count() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;

    retain_episode(
        &store,
        &context,
        retain_request(&context, "Dead letter scenario fact.", None),
    )
    .await
    .expect("retain");

    sqlx::query("update memphant.job_state set attempts = 5 where tenant_id = $1")
        .bind(tenant.as_uuid())
        .execute(store.pool())
        .await
        .expect("exhaust attempts");

    let claimed = store
        .claim_reflect_jobs(
            JobFilter {
                tenant: Some(tenant),
                scope: Some(scope),
            },
            10,
        )
        .await
        .expect("claim");
    assert!(claimed.is_empty(), "exhausted jobs are never re-claimed");

    let dead: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state where tenant_id = $1 and state = 'dead'",
    )
    .bind(tenant.as_uuid())
    .fetch_one(store.pool())
    .await
    .expect("dead count");
    assert!(dead >= 1);
    assert!(store.dead_letter_count().await.expect("dead letters") >= 1);
}

/// A tenant-scoped claim must dead-letter only its own exhausted jobs, never
/// another tenant's. Regression guard for the sweep that used to run globally
/// on every claim — it crossed the tenant boundary (tenant A's foreground
/// reflect wrote tenant B's rows) and seq-scanned job_state on the hot path.
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn dead_letter_sweep_stays_within_the_claim_filter() {
    let store = connect().await;
    let tenant_a = fresh_tenant(&store).await;
    let tenant_b = fresh_tenant(&store).await;
    let context_a = fresh_memory_context(&store, tenant_a).await;
    let context_b = fresh_memory_context(&store, tenant_b).await;
    let scope_a = context_a.scope_id;

    retain_episode(
        &store,
        &context_a,
        retain_request(&context_a, "A fact.", None),
    )
    .await
    .expect("retain A");
    retain_episode(
        &store,
        &context_b,
        retain_request(&context_b, "B fact.", None),
    )
    .await
    .expect("retain B");

    // Exhaust BOTH tenants' jobs, then run a claim scoped to A only.
    sqlx::query("update memphant.job_state set attempts = 5 where tenant_id = any($1)")
        .bind(vec![tenant_a.as_uuid(), tenant_b.as_uuid()])
        .execute(store.pool())
        .await
        .expect("exhaust attempts");

    store
        .claim_reflect_jobs(
            JobFilter {
                tenant: Some(tenant_a),
                scope: Some(scope_a),
            },
            10,
        )
        .await
        .expect("claim A");

    let a_dead: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state where tenant_id = $1 and state = 'dead'",
    )
    .bind(tenant_a.as_uuid())
    .fetch_one(store.pool())
    .await
    .expect("A dead count");
    let b_dead: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state where tenant_id = $1 and state = 'dead'",
    )
    .bind(tenant_b.as_uuid())
    .fetch_one(store.pool())
    .await
    .expect("B dead count");

    assert!(
        a_dead >= 1,
        "A's exhausted job dead-letters on A's own claim"
    );
    assert_eq!(
        b_dead, 0,
        "B's job must NOT dead-letter from A's tenant-scoped claim"
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn resource_retain_reflect_recall_round_trips_via_service() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;
    let svc = service(store.clone());

    let retained = retain_resource(
        svc.store(),
        &context,
        RetainResourceRequest {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: scope,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            uri: "https://example.test/runbooks/deploy.md".to_string(),
            source_ref: "pg-contract:resource".to_string(),
            observed_at: CLOCK.0.to_string(),
            kind: Some(memphant_types::ResourceKind::Document),
            content_hash: "sha256:15cae6da6e921e32a0dd42efdb3fcec54e8de2b4b45824a46f2b7768dd78448b"
                .to_string(),
            mime_type: "text/markdown".to_string(),
            revision: Some("rev-42".to_string()),
            body: Some("Deploy runbook: canary first, then roll forward regions.".to_string()),
            source_trust: TrustLevel::TrustedUser,
            compiler_version: "compiler-pg-contract".to_string(),
        },
    )
    .await
    .expect("retain resource");
    svc.run_worker_tick_scoped(memphant_store_testkit::tenant_filter(&context), usize::MAX)
        .await
        .expect("reflect");

    let recalled = recall(
        &store,
        recall_request(&context, "How does the deploy runbook roll forward?"),
        None,
        &CLOCK,
    )
    .await
    .expect("recall");
    let item = recalled
        .items
        .iter()
        .find(|item| item.kind == MemoryKind::Resource)
        .expect("resource-derived item");
    assert_eq!(item.citation_resource_id, Some(retained.resource_id));
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn api_key_lookup_and_revocation_round_trip() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let key_hash = format!("hash-{}", Uuid::now_v7());

    let key_id = store
        .create_api_key(
            tenant.as_uuid(),
            &key_hash,
            "contract",
            TrustLevel::TrustedUser,
            None,
        )
        .await
        .expect("create key");

    let row = store
        .lookup_api_key(&key_hash)
        .await
        .expect("lookup")
        .expect("key exists");
    assert_eq!(row.tenant_id, tenant);
    assert_eq!(row.max_trust, TrustLevel::TrustedUser);
    assert_eq!(row.data_subject_id, None);
    assert_eq!(row.subject_generation, None);
    assert_eq!(row.agent_node_id, None);
    assert!(!row.revoked);

    assert!(store.revoke_api_key(key_id).await.expect("revoke"));
    let row = store
        .lookup_api_key(&key_hash)
        .await
        .expect("lookup")
        .expect("key still resolvable");
    assert!(row.revoked, "revoked keys resolve as revoked, never valid");
    assert!(
        !store.revoke_api_key(key_id).await.expect("second revoke"),
        "double revoke is a no-op"
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn scoped_api_key_round_trips_the_full_memory_context() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let key_hash = format!("scoped-hash-{}", Uuid::now_v7());

    store
        .create_api_key(
            tenant.as_uuid(),
            &key_hash,
            "scoped-contract",
            TrustLevel::TrustedUser,
            Some(&context),
        )
        .await
        .expect("create scoped key");

    let row = store
        .lookup_api_key(&key_hash)
        .await
        .expect("lookup")
        .expect("scoped key exists");
    assert_eq!(row.data_subject_id, Some(context.data_subject_id));
    assert_eq!(row.subject_generation, Some(context.subject_generation));
    assert_eq!(row.actor_id, Some(context.actor_id));
    assert_eq!(row.scope_id, Some(context.scope_id));
    assert_eq!(row.agent_node_id, Some(context.agent_node_id));
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn degraded_read_your_own_writes_serves_unreflected_episodes() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;
    let svc = service(store.clone());

    retain_episode(
        svc.store(),
        &context,
        retain_request(&context, "Fallback rollout window is Thursday night.", None),
    )
    .await
    .expect("retain");

    // No reflect: the service-level recall must fall back to raw episodes.
    let response = svc
        .recall(
            context.clone(),
            RecallHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: scope,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                actor_id: context.actor_id,
                query: "When is the fallback rollout window?".to_string(),
                limit: Some(4),
                budget_tokens: Some(256),
                mode: None,
                include_beliefs: None,
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .expect("service recall");
    assert!(response.degraded);
    assert_eq!(
        response.items[0].body,
        "Fallback rollout window is Thursday night."
    );
}

/// Six turns behind a `[session]` provenance line: turn windows of 4 yield
/// two chunks (turns 1-4, 5-6). Mirrors
/// `memphant-core/tests/contextual_chunk_write.rs::EPISODE_BODY`.
const CHUNK_EPISODE_BODY: &str = "[session s1] [date 2023/05/30]\n\
user: I moved to Berlin in March.\n\
assistant: Got it, you moved to Berlin in March.\n\
user: My favorite tea is oolong.\n\
assistant: Noted, oolong tea it is.\n\
user: I drive a blue Tesla.\n\
assistant: A blue Tesla, understood.\n";

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn contextual_chunks_round_trip_through_postgres_via_fresh_pool() {
    let store_a = connect().await;
    let tenant = fresh_tenant(&store_a).await;
    let context = fresh_memory_context(&store_a, tenant).await;

    // Default construction mints contextual chunks (promoted to default-on
    // 2026-07-10) — the product path, same as `service()` elsewhere in this
    // file.
    let svc_a = service(store_a);
    let retained = retain_episode(
        svc_a.store(),
        &context,
        retain_request(&context, CHUNK_EPISODE_BODY, None),
    )
    .await
    .expect("retain");
    svc_a
        .run_worker_tick_scoped(memphant_store_testkit::tenant_filter(&context), usize::MAX)
        .await
        .expect("reflect");

    // A COMPLETELY fresh pool must see the compiled unit's chunks: this is
    // the payload jsonb round trip through `memphant.memory_unit`, never
    // exercised by an automated test before this one (rung 4 was "by
    // construction" only, per InMemoryStore assertions).
    let store_b = connect().await;
    let page = store_b
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page via fresh pool");
    let unit = page
        .items
        .iter()
        .find(|unit| unit.source_episode_id == Some(retained.episode_id))
        .expect("episode-derived unit");

    assert_eq!(
        unit.contextual_chunks.len(),
        2,
        "six turns / window 4 yields two chunks, surviving the payload jsonb round trip"
    );
    let episode_uuid = retained.episode_id.as_uuid();
    for chunk in &unit.contextual_chunks {
        assert!(
            chunk.id.starts_with(&format!("chunk-{episode_uuid}-")),
            "chunk id derives from parent episode: {}",
            chunk.id
        );
        assert!(
            chunk.header.contains(&format!("[episode {episode_uuid}]")),
            "header carries parent episode provenance: {}",
            chunk.header
        );
        assert!(!chunk.body.trim().is_empty(), "no empty-body chunks");
        assert!(
            chunk
                .source_span
                .as_deref()
                .is_some_and(|span| span.contains('-')),
            "chunk carries a source span"
        );
    }
    assert!(
        unit.contextual_chunks[0].header.contains("[turns 1-4]"),
        "first window covers turns 1-4: {}",
        unit.contextual_chunks[0].header
    );
    assert!(
        unit.contextual_chunks[1].header.contains("[turns 5-6]"),
        "second window covers turns 5-6: {}",
        unit.contextual_chunks[1].header
    );
    assert_ne!(
        unit.contextual_chunks[0].id, unit.contextual_chunks[1].id,
        "window ids are distinct"
    );
}

/// Multi-tenant job-claim fairness (plan addendum W1-b): `claim_reflect_jobs`
/// claims per-tenant round-robin — each tenant's eligible jobs are ranked by
/// enqueue order (`row_number() over (partition by tenant_id order by queue_order)`) and
/// the batch draws every tenant's oldest before any tenant's second
/// (`crates/memphant-store-postgres/src/store.rs::claim_reflect_jobs`). A tenant
/// with a large backlog can therefore no longer starve a low-volume tenant's
/// single, more urgent job — the exact gap that produced the orphaned-backlog
/// e2e failure named in the research audit (scratchpad/research/tests-audit.md,
/// gap 3).
///
/// This is the GREEN assertion that flipped the former `_red_baseline`: tenant A
/// floods a 50-job backlog created first, tenant B queues one job strictly
/// after, and a single worker batch of 16 MUST include tenant B's job (it lands
/// at round-robin position 2, right behind tenant A's oldest).
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn multi_tenant_claim_batch_does_not_starve_the_low_volume_tenant() {
    let store = connect().await;
    let tenant_a = fresh_tenant(&store).await;
    let tenant_b = fresh_tenant(&store).await;
    let context_a = fresh_memory_context(&store, tenant_a).await;
    let context_b = fresh_memory_context(&store, tenant_b).await;

    // Tenant A floods the global queue with a 50-job backlog...
    for index in 0..50 {
        retain_episode(
            &store,
            &context_a,
            retain_request(
                &context_a,
                &format!("Tenant A backlog fact number {index}."),
                None,
            ),
        )
        .await
        .expect("retain tenant A backlog item");
    }

    // ...then tenant B queues a single job strictly AFTER tenant A's backlog.
    retain_episode(
        &store,
        &context_b,
        retain_request(&context_b, "Tenant B single urgent fact.", None),
    )
    .await
    .expect("retain tenant B job");

    // Same batch size the real memphant-worker binary claims per tick
    // (crates/memphant-worker/src/main.rs::BATCH).
    const WORKER_BATCH: usize = 16;
    let claimed = store
        .claim_reflect_jobs(JobFilter::default(), WORKER_BATCH)
        .await
        .expect("claim");

    assert_eq!(claimed.len(), WORKER_BATCH, "a full batch is claimed");
    assert!(
        claimed.iter().any(|row| row.job.tenant_id == tenant_b),
        "tenant B's single job must be claimed in the first batch despite tenant \
         A's 50-job head-start backlog — per-tenant fair claiming interleaves by \
         age rank, so B lands right behind A's oldest job"
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn stub_embeddings_persist_and_power_the_vector_channel() {
    use memphant_core::StubEmbedding;

    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;
    let svc = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    );

    retain_episode(
        svc.store(),
        &context,
        retain_request(&context, "Release region is Taipei.", None),
    )
    .await
    .expect("retain");
    svc.run_worker_tick_scoped(memphant_store_testkit::tenant_filter(&context), usize::MAX)
        .await
        .expect("reflect");

    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    assert!(!page.items.is_empty());
    let unit_ids: Vec<_> = page.items.iter().map(|unit| unit.id).collect();
    let rows = store
        .fetch_embeddings(&context, &unit_ids)
        .await
        .expect("fetch embeddings");
    assert!(
        !rows.is_empty(),
        "compiled unit embeddings are durably persisted in Postgres"
    );
    assert!(rows.iter().all(|row| row.vec.len() == 32));

    let response = svc
        .recall(
            context.clone(),
            RecallHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: scope,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                actor_id: context.actor_id,
                query: "Release region is Taipei.".to_string(),
                limit: Some(4),
                budget_tokens: Some(256),
                mode: None,
                include_beliefs: None,
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .expect("recall");
    assert!(!response.items.is_empty());
    let trace = svc
        .trace(&context, response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    assert!(
        trace.candidates.iter().any(|candidate| candidate.channel
            == memphant_types::RecallChannel::Vector
            && candidate.channel_score > 0.0),
        "pgvector-backed vector channel produced scored candidates"
    );
}

/// The spec-mandated `embedding_profile_id` predicate (spec 03): the vector
/// query MUST filter by the active profile. This is not cosmetic — a unit that
/// also carries an embedding under a DIFFERENT-dimension profile would make the
/// `<=>` join compare mismatched dimensions and raise a pgvector error if the
/// predicate were dropped. The query succeeding (and returning the unit via its
/// active-profile row) is the regression guard.
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn vector_candidates_filter_by_embedding_profile() {
    use memphant_core::{
        EmbeddingProfileRow, EmbeddingProvider, EmbeddingRow, StubEmbedding,
        VECTOR_CANDIDATE_LIMIT, embedding_profile_for,
    };

    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let svc = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    );

    retain_episode(
        svc.store(),
        &context,
        retain_request(&context, "Release region is Taipei.", None),
    )
    .await
    .expect("retain");
    svc.run_worker_tick_scoped(memphant_store_testkit::tenant_filter(&context), usize::MAX)
        .await
        .expect("reflect");

    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    assert!(!page.items.is_empty());
    let unit = page.items[0].id;

    let stub = StubEmbedding::default();
    let active_profile = embedding_profile_for(&stub); // 32-dim

    // A SECOND profile with a DIFFERENT dimension, plus an embedding for the
    // SAME unit under it. Without the `embedding_profile_id = $pid` predicate,
    // the vector join would also select this 4-dim row and pgvector would raise
    // a dimension-mismatch error against the 32-dim query vector.
    let other_profile = EmbeddingProfileRow {
        id: Uuid::now_v7(),
        provider: "stub-alt".to_string(),
        model: "stub-alt".to_string(),
        dimensions: 4,
        distance: "cosine".to_string(),
        version: "1".to_string(),
        index_strategy: "exact".to_string(),
    };
    store
        .upsert_embedding_profile(tenant, other_profile.clone())
        .await
        .expect("seed cross profile");
    store
        .upsert_embeddings(
            &context,
            vec![EmbeddingRow {
                memory_unit_id: unit,
                embedding_profile_id: other_profile.id,
                vec: vec![0.1, 0.2, 0.3, 0.4],
            }],
        )
        .await
        .expect("insert cross-profile embedding");

    let query_vec = stub
        .embed(&["Release region Taipei.".to_string()])
        .expect("embed query")
        .remove(0);
    assert_eq!(query_vec.len(), 32);

    // WITH the predicate: only the 32-dim active-profile row is compared, so the
    // query succeeds and returns the unit. WITHOUT it, this call errors.
    let pairs = store
        .fetch_vector_candidates(
            &context,
            &query_vec,
            active_profile.id,
            &test_recall_time(),
            VECTOR_CANDIDATE_LIMIT,
        )
        .await
        .expect("vector query must filter cross-profile rows, not error on them");
    assert!(
        pairs.iter().any(|(candidate, _)| candidate.id == unit),
        "unit is returned via its active-profile (32-dim) embedding"
    );

    // Querying under the OTHER profile with a matching 4-dim vector reaches the
    // same unit via THAT profile's row — proving selection is by profile id.
    let other_pairs = store
        .fetch_vector_candidates(
            &context,
            &[0.1_f32, 0.2, 0.3, 0.4],
            other_profile.id,
            &test_recall_time(),
            VECTOR_CANDIDATE_LIMIT,
        )
        .await
        .expect("other-profile query");
    assert!(
        other_pairs
            .iter()
            .any(|(candidate, _)| candidate.id == unit),
        "unit is reachable under the cross profile via its 4-dim embedding"
    );
}

/// R0 determinism guard: re-ingesting the same corpus into a fresh tenant mints
/// new UUIDs, so any recall ordering point that breaks ties by insertion/heap
/// order reshuffles run-to-run (the measured ±1-question docs-gate variance).
/// The corpus is three token-PERMUTATIONS of the same words: distinct bodies
/// (distinct dedup keys — `normalize_component` preserves word order) but an
/// identical `StubEmbedding` vector, so they TIE exactly on vector distance.
/// Ingested in NON-body order, a tie-cut that fell back to insertion/uuid order
/// would diverge from the content order. The `<=>, unit.body` tie-break instead
/// pins the vector candidates to body order, and the whole recall-candidate
/// ordering is then identical across two independently-UUID'd tenants.
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn recall_ordering_is_content_stable_across_reingest() {
    use memphant_core::{
        EmbeddingProvider, StubEmbedding, VECTOR_CANDIDATE_LIMIT, embedding_profile_for,
    };

    // Inserted top-to-bottom; sorts bottom-to-top by body — insertion order is
    // the reverse of the content order the tie-break must produce.
    const CORPUS: [&str; 3] = [
        "region alpha bravo",
        "bravo region alpha",
        "alpha bravo region",
    ];
    let mut sorted_bodies: Vec<String> = CORPUS.iter().map(|s| s.to_string()).collect();
    sorted_bodies.sort();

    let store = connect().await;
    let stub = StubEmbedding::default();
    let profile = embedding_profile_for(&stub);
    let query_vec = stub
        .embed(&["region".to_string()])
        .expect("embed query")
        .remove(0);

    let only_corpus = |bodies: Vec<String>| -> Vec<String> {
        bodies
            .into_iter()
            .filter(|b| CORPUS.contains(&b.as_str()))
            .collect()
    };

    let mut per_tenant = Vec::new();
    for _ in 0..2 {
        let tenant = fresh_tenant(&store).await;
        let context = fresh_memory_context(&store, tenant).await;
        let svc = MemoryService::new(Arc::new(store.clone()), Arc::new(CLOCK), Arc::new(stub));
        for body in CORPUS {
            retain_episode(svc.store(), &context, retain_request(&context, body, None))
                .await
                .expect("retain");
        }
        svc.run_worker_tick_scoped(memphant_store_testkit::tenant_filter(&context), usize::MAX)
            .await
            .expect("reflect");

        let vector_bodies = only_corpus(
            store
                .fetch_vector_candidates(
                    &context,
                    &query_vec,
                    profile.id,
                    &test_recall_time(),
                    VECTOR_CANDIDATE_LIMIT,
                )
                .await
                .expect("vector candidates")
                .into_iter()
                .map(|(unit, _)| unit.body)
                .collect(),
        );
        assert_eq!(
            vector_bodies, sorted_bodies,
            "tied vector candidates must order by body, not insertion/uuid order"
        );

        let recall_bodies = only_corpus(
            store
                .fetch_recall_candidates(
                    &context,
                    &[],
                    &["region".to_string()],
                    &test_recall_time(),
                    100,
                )
                .await
                .expect("recall candidates")
                .into_iter()
                .map(|unit| unit.body)
                .collect(),
        );
        per_tenant.push((vector_bodies, recall_bodies));
    }

    assert_eq!(
        per_tenant[0], per_tenant[1],
        "identical corpus recalls in identical order across a fresh-UUID re-ingest"
    );
}

/// Sibling of the recall-ordering guard, for the degraded read-your-own-writes
/// path: `fetch_episodes_for_scope` orders by `last_observed_at desc` and cuts
/// at LIMIT. Every episode staged in one transaction shares `last_observed_at`
/// (it is `now()`), so the recency key ties and the LIMIT boundary is decided
/// by the `dedup_key` tie-break — a total, content-derived order. Without it the
/// cut follows physical/insertion order and a fresh-UUID re-ingest surfaces a
/// different subset as degraded citations.
///
/// The two tenants ingest the SAME corpus in OPPOSITE orders and then have every
/// episode's `last_observed_at` collapsed to one instant: pre-fix the tied cut
/// tracks insertion order and the two disagree; the content tie-break makes them
/// agree. This is what makes the assertion a real guard rather than a
/// coincidental pass.
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn episode_scope_cut_is_content_stable_across_reingest() {
    const CORPUS: [&str; 5] = [
        "Episode fact alpha.",
        "Episode fact bravo.",
        "Episode fact charlie.",
        "Episode fact delta.",
        "Episode fact echo.",
    ];
    let store = connect().await;

    let mut per_tenant = Vec::new();
    for order in [[0, 1, 2, 3, 4], [4, 3, 2, 1, 0]] {
        let tenant = fresh_tenant(&store).await;
        let context = fresh_memory_context(&store, tenant).await;
        for index in order {
            retain_episode(
                &store,
                &context,
                retain_request(&context, CORPUS[index], None),
            )
            .await
            .expect("retain episode");
        }
        // Collapse every episode onto one `last_observed_at` in a single
        // statement (all rows get the same `now()`), forcing the recency tie the
        // content key must resolve.
        sqlx::query("update memphant.episode set last_observed_at = now() where tenant_id = $1")
            .bind(tenant.as_uuid())
            .execute(store.pool())
            .await
            .expect("collapse last_observed_at");

        let bodies: Vec<String> = store
            .fetch_episodes_for_scope(&context, 3)
            .await
            .expect("fetch episodes")
            .into_iter()
            .map(|episode| episode.body)
            .collect();
        per_tenant.push(bodies);
    }

    assert_eq!(per_tenant[0].len(), 3, "the cut returns the limit");
    assert_eq!(
        per_tenant[0], per_tenant[1],
        "the recency LIMIT cut must be content-stable regardless of ingest order / fresh UUIDs"
    );
}

/// Regression: a direct-unit retain into a scope with >1000 pre-existing units
/// must return the just-created unit's id. The response id used to be recovered
/// via `scope_memory_page`, which clamps to 1000 rows ordered by id, so a fresh
/// (larger) unit id fell off the page and `unit_ids` came back empty. The id now
/// comes straight from `reflect_recorded`, independent of scope size.
#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn direct_unit_retain_returns_unit_id_past_scope_page_clamp() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;

    // Seed 1001 units in one reflect so the scope exceeds the 1000-row clamp.
    let candidates: Vec<ReflectCandidate> = (0..1001)
        .map(|i| ReflectCandidate {
            source_kind: "user".to_string(),
            trust_level: TrustLevel::TrustedUser,
            actor_id: actor,
            subject: Some(format!("seed-subject-{i}")),
            predicate: Some("is".to_string()),
            fact_key: None,
            kind: Some(MemoryKind::Semantic),
            body: format!("seed fact number {i} about widgets"),
            confidence: None,
            churn_class: None,
            admission_hint: None,
            target_unit_ids: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
        })
        .collect();
    reflect_recorded(
        &store,
        ReflectInput {
            tenant_id: tenant,
            data_subject_id: context.data_subject_id,
            scope_id: scope,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: actor,
            source_ref: "test:reflect".to_string(),
            observed_at: CLOCK.0.to_string(),
            source_body: None,
            episode_id: None,
            resource_id: None,
            job_id: JobId::new(),
            compiler_version: "compiler-pg-seed".to_string(),
            candidates,
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("seed reflect succeeds");

    // `has_more` past the 1000-row page clamp proves the scope is oversized.
    let seeded = store
        .scope_memory_page(&context, None, usize::MAX)
        .await
        .expect("count seeded units");
    assert!(
        seeded.has_more,
        "scope must exceed the 1000-row page clamp, got {}",
        seeded.items.len()
    );

    let direct_body = "direct unit past the clamp".to_string();
    let response = service(store)
        .retain(
            &context,
            "pg-contract-direct-clamp",
            TrustLevel::TrustedUser,
            RetainEpisodeHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: scope,
                actor_id: actor,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_ref: "pg-contract:direct".to_string(),
                observed_at: CLOCK.0.to_string(),
                payload: RetainPayload::Unit(RetainUnitPayload {
                    kind: MemoryKind::Semantic,
                    fact_key: "direct-subject".to_string(),
                    predicate: "records".to_string(),
                    body: direct_body.clone(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                }),
            },
        )
        .await
        .expect("direct unit retain succeeds");

    let response: RetainEpisodeHttpResponse = serde_json::from_slice(response.body()).unwrap();
    assert!(
        !response.unit_ids.is_empty(),
        "response must surface the created unit id regardless of scope size"
    );
    let store = connect().await;
    let resolved = store
        .fetch_units_by_ids(&context, &response.unit_ids)
        .await
        .expect("resolve returned unit ids");
    assert!(
        resolved.iter().any(|unit| unit.body == direct_body),
        "a returned id must resolve to the just-created direct unit"
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn bitemporal_correction_round_trips_through_postgres_and_forget_erases_history() {
    use memphant_core::{
        CorrectionWrite, EmbeddingProfileRow, EmbeddingRow, ForgetWrite, embedding_profile_for,
    };
    use memphant_types::{
        CorrectSelector, CorrectionPayload, ForgetTarget, NewMemoryUnit, UnitState,
    };

    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;
    let mut tx = store.begin(&context).await.expect("begin");
    let old_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: scope,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("profile:city".to_string()),
                predicate: None,
                body: "lives in Oslo".to_string(),
                confidence: None,
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor),
                source_kind: Some("pg-bitemporal-test".to_string()),
                source_ref: "pg-contract:bitemporal".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: Some("2025-01-01T00:00:00Z".to_string()),
                valid_to: Some("2026-01-01T00:00:00Z".to_string()),
                transaction_from: Some("2025-01-02T00:00:00Z".to_string()),
                transaction_to: None,
            },
        )
        .await
        .expect("stage old unit");
    store.commit(tx).await.expect("commit old unit");

    let profile: EmbeddingProfileRow =
        embedding_profile_for(&memphant_core::StubEmbedding::default());
    store
        .upsert_embedding_profile(tenant, profile.clone())
        .await
        .expect("profile");
    store
        .upsert_embeddings(
            &context,
            vec![EmbeddingRow {
                memory_unit_id: old_id,
                embedding_profile_id: profile.id,
                vec: vec![0.5; profile.dimensions],
            }],
        )
        .await
        .expect("old embedding");

    let correction_write = CorrectionWrite {
        selector: CorrectSelector {
            memory_unit_id: old_id,
        },
        correction: CorrectionPayload {
            value: "lives in Lima".to_string(),
            reason: "moved".to_string(),
            source_ref: "pg-contract:correction".to_string(),
            observed_at: CLOCK.0.to_string(),
            valid_from: Some("2025-04-01T00:00:00Z".to_string()),
            valid_to: Some("2025-07-01T00:00:00Z".to_string()),
        },
        source_ref: "pg-contract:correction".to_string(),
        observed_at: CLOCK.0.to_string(),
        now: "1900-01-01T00:00:00Z".to_string(),
        embedding: Some((profile.clone(), vec![0.75; profile.dimensions])),
        unit_ids: Default::default(),
    };
    let mut dropped_correction = store.begin(&context).await.unwrap();
    let dropped_created = store
        .stage_correction(&mut dropped_correction, correction_write.clone())
        .await
        .unwrap()
        .created;
    drop(dropped_correction);
    assert!(
        store
            .fetch_units_by_ids(&context, &dropped_created)
            .await
            .unwrap()
            .is_empty()
    );
    let corrected = store
        .apply_correction(&context, correction_write)
        .await
        .expect("correct");
    assert_eq!(
        corrected.created.len(),
        3,
        "replacement plus two remainders"
    );
    let created_rows = store
        .fetch_units_by_ids(&context, &corrected.created)
        .await
        .expect("created rows");
    let correction_transaction_time = created_rows
        .iter()
        .find(|unit| unit.id == corrected.created[0])
        .and_then(|unit| unit.transaction_from.clone())
        .expect("replacement has database-assigned transaction time");
    assert!(
        created_rows.iter().all(|unit| {
            unit.transaction_from
                .as_deref()
                .is_some_and(|stamp| stamp > "2020-01-01T00:00:00Z")
        }),
        "Postgres assigns transaction time; the skewed caller clock is ignored"
    );

    async fn snapshot(
        store: &PgStore,
        context: &ResolvedMemoryContext,
        transaction_as_of: &str,
        valid_at: &str,
        query: &str,
    ) -> memphant_types::RecallResponse {
        recall(
            store,
            RecallRequest {
                context: context.clone(),
                query: query.to_string(),
                k: 4,
                budget_tokens: 128,
                mode: RecallMode::Fast,
                include_beliefs: true,
                edge_expansion_enabled: true,
                context_packing_abstention_enabled: false,
                procedure_recall_enabled: true,
                decay_enabled: false,
                engine_version: "pg-bitemporal-test".to_string(),
                transaction_as_of: Some(transaction_as_of.to_string()),
                valid_at: Some(valid_at.to_string()),
                aggregation_window: None,
            },
            None,
            &FixedClock("2030-01-01T00:00:00Z"),
        )
        .await
        .expect("snapshot recall")
    }

    let before = snapshot(
        &store,
        &context,
        "2025-07-01T00:00:00Z",
        "2025-04-01T00:00:00Z",
        "Oslo",
    )
    .await;
    assert_eq!(before.items[0].body, "lives in Oslo");
    let boundary = snapshot(
        &store,
        &context,
        "2029-09-01T00:00:00Z",
        "2025-04-01T00:00:00Z",
        "Lima",
    )
    .await;
    assert_eq!(boundary.items[0].body, "lives in Lima");
    let review = ReviewEvent {
        tenant_id: tenant,
        trace_id: boundary.trace_id,
        caller_id: "bitemporal-review".to_string(),
        used_ids: vec![corrected.created[0]],
        outcome: MarkOutcome::Success,
        recorded_at: "1900-01-01T00:00:00Z".to_string(),
    };
    let mut dropped_review = store.begin(&context).await.unwrap();
    store
        .stage_review_events(&mut dropped_review, vec![review.clone()])
        .await
        .unwrap();
    drop(dropped_review);
    let review_count: i64 = sqlx::query_scalar(
        "select count(*) from memphant.review_event where tenant_id = $1 and caller_id = $2",
    )
    .bind(tenant.as_uuid())
    .bind(&review.caller_id)
    .fetch_one(store.pool())
    .await
    .unwrap();
    assert_eq!(review_count, 0);
    store
        .record_review_events(&context, vec![review])
        .await
        .expect("review event");
    let before_review = RecallTime {
        evaluated_at: "2030-01-01T00:00:00Z".to_string(),
        transaction_as_of: "2000-01-01T00:00:00Z".to_string(),
        valid_at: "2000-01-01T00:00:00Z".to_string(),
    };
    assert!(
        store
            .fetch_review_events(&context, &[corrected.created[0]], &before_review)
            .await
            .expect("historical reviews")
            .is_empty(),
        "database-assigned review time excludes feedback written after the snapshot"
    );
    let left = snapshot(
        &store,
        &context,
        "2029-09-01T00:00:00Z",
        "2025-03-31T23:59:59Z",
        "Oslo",
    )
    .await;
    assert_eq!(left.items[0].body, "lives in Oslo");
    let right_boundary = snapshot(
        &store,
        &context,
        "2029-09-01T00:00:00Z",
        "2025-07-01T00:00:00Z",
        "Oslo",
    )
    .await;
    assert_eq!(right_boundary.items[0].body, "lives in Oslo");

    let embeddings = store
        .fetch_embeddings(&context, &corrected.created)
        .await
        .expect("created embeddings");
    assert_eq!(
        embeddings.len(),
        3,
        "fresh replacement plus copied remainders"
    );
    let after_time = RecallTime {
        evaluated_at: "2030-01-01T00:00:00Z".to_string(),
        transaction_as_of: correction_transaction_time,
        valid_at: "2025-05-01T00:00:00Z".to_string(),
    };
    assert_eq!(
        store
            .fetch_edges(&context, &corrected.created, &after_time)
            .await
            .expect("current edges")
            .len(),
        3
    );
    let before_time = RecallTime {
        transaction_as_of: "2025-07-01T00:00:00Z".to_string(),
        ..after_time.clone()
    };
    let before_vector = store
        .fetch_vector_candidates(
            &context,
            &vec![0.5; profile.dimensions],
            profile.id,
            &before_time,
            10,
        )
        .await
        .expect("historical vector");
    assert_eq!(before_vector[0].0.id, old_id);
    let after_vector = store
        .fetch_vector_candidates(
            &context,
            &vec![0.75; profile.dimensions],
            profile.id,
            &after_time,
            10,
        )
        .await
        .expect("current vector");
    assert_eq!(after_vector[0].0.id, corrected.created[0]);
    assert!(
        store
            .fetch_edges(&context, &corrected.created, &before_time)
            .await
            .expect("historical edges")
            .is_empty()
    );

    let forget_write = ForgetWrite {
        target: ForgetTarget::MemoryUnit(corrected.created[0]),
        now: "2025-10-01T00:00:00Z".to_string(),
    };
    let mut dropped_forget = store.begin(&context).await.unwrap();
    store
        .stage_forget(&mut dropped_forget, forget_write.clone())
        .await
        .unwrap();
    drop(dropped_forget);
    assert!(
        store
            .fetch_units_by_ids(&context, &[corrected.created[0]])
            .await
            .unwrap()
            .iter()
            .any(|unit| unit.state != UnitState::Deleted)
    );
    store
        .apply_forget(&context, forget_write)
        .await
        .expect("forget old generation");
    assert!(
        snapshot(
            &store,
            &context,
            "2025-07-01T00:00:00Z",
            "2025-05-01T00:00:00Z",
            "Oslo",
        )
        .await
        .items
        .is_empty(),
        "forgetting the current generation erases its supersedes lineage from every snapshot"
    );
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn structured_exact_recurrence_replaces_every_overlapping_postgres_rectangle() {
    let store = connect().await;
    let tenant = fresh_tenant(&store).await;
    let context = fresh_memory_context(&store, tenant).await;
    let scope = context.scope_id;
    let actor = context.actor_id;
    let cases = [
        (
            "user: Oslo was my home city throughout 2025.",
            "Oslo was my home city throughout 2025.",
            "Oslo",
            "2025-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
        (
            "user: Lima was my home city in spring 2025.",
            "Lima was my home city in spring 2025.",
            "Lima",
            "2025-04-01T00:00:00Z",
            "2025-07-01T00:00:00Z",
        ),
        (
            "user: Oslo was my home city from March through July 2025.",
            "Oslo was my home city from March through July 2025.",
            "Oslo",
            "2025-03-01T00:00:00Z",
            "2025-08-01T00:00:00Z",
        ),
    ];

    let mut second_generation_time = None;
    let fact_key = derive_fact_key(scope.as_uuid(), Some("profile"), Some("home_city"), "");
    for (index, (body, quote, value, valid_from, valid_to)) in cases.iter().enumerate() {
        let svc = structured_service(
            store.clone(),
            vec![structured_upsert(body, quote, value, valid_from, valid_to)],
        );
        svc.retain(
            &context,
            &format!("pg-contract-structured-{index}"),
            TrustLevel::TrustedUser,
            RetainEpisodeHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: scope,
                actor_id: actor,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                source_ref: "pg-contract:structured".to_string(),
                observed_at: CLOCK.0.to_string(),
                payload: RetainPayload::Episode(RetainEpisodePayload {
                    source_kind: "user".to_string(),
                    body: (*body).to_string(),
                }),
            },
        )
        .await
        .expect("retain structured episode");
        svc.run_worker_tick_scoped(memphant_store_testkit::tenant_filter(&context), usize::MAX)
            .await
            .expect("reflect structured episode");

        if index == 1 {
            second_generation_time = store
                .fetch_scope_open_units(&context)
                .await
                .expect("fetch second generation")
                .into_iter()
                .find(|unit| {
                    unit.fact_key.as_deref() == Some(&fact_key) && unit.body.contains("Lima")
                })
                .and_then(|unit| unit.transaction_from);
        }
    }

    let current = store
        .fetch_scope_open_units(&context)
        .await
        .expect("fetch current structured rectangles")
        .into_iter()
        .filter(|unit| unit.fact_key.as_deref() == Some(&fact_key))
        .collect::<Vec<_>>();
    assert_eq!(
        current.len(),
        3,
        "Postgres persists one replacement plus both surviving remainders"
    );
    assert!(current.iter().all(|unit| unit.state == UnitState::Active));
    assert!(current.iter().all(|unit| !unit.body.contains("Lima")));
    assert!(
        current.iter().any(|unit| {
            unit.body.contains("Oslo")
                && unit.valid_from.as_deref() == Some("2025-01-01T00:00:00Z")
                && unit.valid_to.as_deref() == Some("2025-03-01T00:00:00Z")
        }),
        "missing left remainder: {current:#?}"
    );
    assert!(
        current.iter().any(|unit| {
            unit.body.contains("Oslo")
                && unit.valid_from.as_deref() == Some("2025-03-01T00:00:00Z")
                && unit.valid_to.as_deref() == Some("2025-08-01T00:00:00Z")
        }),
        "missing replacement: {current:#?}"
    );
    assert!(
        current.iter().any(|unit| {
            unit.body.contains("Oslo")
                && unit.valid_from.as_deref() == Some("2025-08-01T00:00:00Z")
                && unit.valid_to.as_deref() == Some("2026-01-01T00:00:00Z")
        }),
        "missing right remainder: {current:#?}"
    );

    let mut historical_request = recall_request(&context, "profile home city");
    historical_request.transaction_as_of =
        Some(second_generation_time.expect("second generation has database transaction time"));
    historical_request.valid_at = Some("2025-05-01T00:00:00Z".to_string());
    historical_request.context_packing_abstention_enabled = false;
    let historical = recall(&store, historical_request, None, &CLOCK)
        .await
        .expect("historical structured recall");
    assert!(historical.items.iter().any(|item| {
        item.body.starts_with("profile item home_city") && item.body.contains("Lima")
    }));
}
