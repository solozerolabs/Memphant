use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex};
use std::time::Duration;

use memphant_core::service::MemoryService;
use memphant_core::{
    EmbedError, EmbeddingProvider, FixedClock, MemoryStore, MutationClaim, MutationClaimOutcome,
    MutationLedgerStore, MutationResponse, MutationVerb, NoopEmbedding, StoreError,
};
use memphant_store_postgres::PgStore;
use memphant_types::{
    COMPILER_VERSION, CorrectRequest, CorrectResult, CorrectSelector, CorrectionPayload,
    MemoryKind, NewEpisode, ResolvedMemoryContext, RetainEpisodeHttpRequest,
    RetainEpisodeHttpResponse, RetainEpisodePayload, RetainPayload, RetainResourcePayload,
    RetainUnitPayload, TenantId, TrustLevel, UnitId,
};
use uuid::Uuid;

async fn store() -> PgStore {
    let url = std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated Postgres");
    PgStore::connect(&url).await.expect("connect PgStore")
}

async fn tenant(store: &PgStore) -> TenantId {
    TenantId::from_u128(
        store
            .create_tenant(&format!("ledger-{}", Uuid::now_v7()))
            .await
            .unwrap()
            .as_u128(),
    )
}

async fn context_for_tenant(store: &PgStore, tenant: TenantId) -> ResolvedMemoryContext {
    memphant_store_testkit::bind_context(store, tenant).await
}

async fn context(store: &PgStore) -> ResolvedMemoryContext {
    context_for_tenant(store, tenant(store).await).await
}

fn claim(context: &ResolvedMemoryContext, key: &str, hash: u8) -> MutationClaim {
    MutationClaim::new(context, MutationVerb::Retain, key, [hash; 32]).unwrap()
}

fn response(body: &[u8]) -> MutationResponse {
    MutationResponse::success(201, body.to_vec()).unwrap()
}

const CLOCK: FixedClock = FixedClock("2026-07-15T00:00:00Z");

fn episode_request(context: &ResolvedMemoryContext) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "pg-service:episode".to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: "postgres atomic episode body".to_string(),
            subject: None,
            predicate: None,
        }),
    }
}

fn resource_request(context: &ResolvedMemoryContext) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "pg-service:resource".to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: RetainPayload::Resource(RetainResourcePayload {
            uri: "file:///pg-service.txt".to_string(),
            mime_type: "text/plain".to_string(),
            content_hash: "sha256:pg-service".to_string(),
            kind: None,
            revision: None,
            body: Some("postgres atomic resource body".to_string()),
        }),
    }
}

fn direct_request(context: &ResolvedMemoryContext) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "pg-service:direct".to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: RetainPayload::Unit(RetainUnitPayload {
            kind: MemoryKind::Semantic,
            fact_key: Some("profile:timezone".to_string()),
            subject: None,
            predicate: "timezone".to_string(),
            body: "timezone is pacific time".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
            target_unit_ids: None,
        }),
    }
}

struct BarrierEmbedding {
    rendezvous: (Mutex<usize>, Condvar),
    calls: AtomicUsize,
}

impl EmbeddingProvider for BarrierEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let (arrivals, ready) = &self.rendezvous;
        let mut arrivals = arrivals.lock().unwrap();
        *arrivals += 1;
        if *arrivals == 2 {
            ready.notify_all();
        } else {
            let (observed, timeout) = ready
                .wait_timeout_while(arrivals, Duration::from_secs(2), |arrivals| *arrivals < 2)
                .unwrap();
            if timeout.timed_out() && *observed < 2 {
                return Err(EmbedError::Unavailable(
                    "second concurrent embedding never reached the rendezvous".to_string(),
                ));
            }
        }
        Ok(vec![vec![1.0]; texts.len()])
    }

    fn dimensions(&self) -> usize {
        1
    }

    fn id(&self) -> &str {
        "pg-service-barrier"
    }
}

fn correct_request(context: &ResolvedMemoryContext, unit_id: UnitId) -> CorrectRequest {
    CorrectRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        selector: CorrectSelector {
            memory_unit_id: unit_id,
        },
        correction: CorrectionPayload {
            value: "timezone is mountain time".to_string(),
            reason: "user correction".to_string(),
            source_ref: "pg-service:correction-race".to_string(),
            observed_at: CLOCK.0.to_string(),
            valid_from: None,
            valid_to: None,
        },
    }
}

fn episode(context: &ResolvedMemoryContext, key: &str) -> NewEpisode {
    NewEpisode {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_kind: "user".to_string(),
        source_ref: format!("test:{key}"),
        observed_at: "2026-07-15T00:00:00Z".to_string(),
        source_trust: TrustLevel::TrustedUser,
        dedup_key: key.to_string(),
        body: "atomic ledger write".to_string(),
    }
}

async fn count_episodes(store: &PgStore, context: &ResolvedMemoryContext) -> i64 {
    let mut tx = store.pool().begin().await.unwrap();
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(context.tenant_id.as_uuid())
        .execute(&mut *tx)
        .await
        .unwrap();
    let count = sqlx::query_scalar(
        "select count(*) from memphant.episode
         where tenant_id = $1 and data_subject_id = $2",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    tx.rollback().await.unwrap();
    count
}

async fn commit_receipt(store: &PgStore, context: &ResolvedMemoryContext, key: &str, hash: u8) {
    let mut tx = store.begin(context).await.unwrap();
    assert_eq!(
        store
            .stage_mutation_claim(&mut tx, claim(context, key, hash))
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    store
        .stage_mutation_response(&mut tx, response(key.as_bytes()))
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
}

async fn expire_receipt(store: &PgStore, context: &ResolvedMemoryContext, key: &str) {
    let mut tx = store.pool().begin().await.unwrap();
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(context.tenant_id.as_uuid())
        .execute(&mut *tx)
        .await
        .unwrap();
    sqlx::query(
        "update memphant.mutation_ledger
         set created_at = statement_timestamp() - interval '25 hours',
             expires_at = statement_timestamp() - interval '1 hour'
         where tenant_id = $1 and verb = 'retain' and idempotency_key = $2",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(key)
    .execute(&mut *tx)
    .await
    .unwrap();
    tx.commit().await.unwrap();
}

async fn count_receipts(store: &PgStore, context: &ResolvedMemoryContext) -> i64 {
    let mut tx = store.pool().begin().await.unwrap();
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(context.tenant_id.as_uuid())
        .execute(&mut *tx)
        .await
        .unwrap();
    let count =
        sqlx::query_scalar("select count(*) from memphant.mutation_ledger where tenant_id = $1")
            .bind(context.tenant_id.as_uuid())
            .fetch_one(&mut *tx)
            .await
            .unwrap();
    tx.rollback().await.unwrap();
    count
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn committed_response_replays_exact_bytes_and_conflicting_hash_is_rejected() {
    let store = store().await;
    let context = context(&store).await;
    let mut first = store.begin(&context).await.unwrap();
    assert_eq!(
        store
            .stage_mutation_claim(&mut first, claim(&context, "replay", 1))
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    store
        .stage_episode(&mut first, episode(&context, "replay-episode"))
        .await
        .unwrap();
    store
        .stage_mutation_response(&mut first, response(&[0, 255, 1, 2]))
        .await
        .unwrap();
    store.commit(first).await.unwrap();

    let mut replay = store.begin(&context).await.unwrap();
    let replayed = store
        .stage_mutation_claim(&mut replay, claim(&context, "replay", 1))
        .await
        .unwrap();
    assert_eq!(
        replayed,
        MutationClaimOutcome::Replay(response(&[0, 255, 1, 2]))
    );
    assert!(matches!(
        store
            .stage_mutation_claim(&mut replay, claim(&context, "replay", 2))
            .await,
        Err(StoreError::IdempotencyConflict)
    ));
    store
        .stage_episode(&mut replay, episode(&context, "replay-must-rollback"))
        .await
        .unwrap();
    store.commit(replay).await.unwrap();
    assert_eq!(count_episodes(&store, &context).await, 1);

    let mut conflict = store.begin(&context).await.unwrap();
    assert!(matches!(
        store
            .stage_mutation_claim(&mut conflict, claim(&context, "replay", 2))
            .await,
        Err(StoreError::IdempotencyConflict)
    ));

    let other_context = memphant_store_testkit::bind_context(&store, context.tenant_id).await;
    let mut wrong_context = store.begin(&context).await.unwrap();
    assert!(matches!(
        store
            .stage_mutation_claim(
                &mut wrong_context,
                claim(&other_context, "wrong-context", 1),
            )
            .await,
        Err(StoreError::IdempotencyConflict)
    ));
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn expired_key_executes_again_with_the_new_hash() {
    let store = store().await;
    let context = context(&store).await;
    let mut first = store.begin(&context).await.unwrap();
    store
        .stage_mutation_claim(&mut first, claim(&context, "expired", 1))
        .await
        .unwrap();
    store
        .stage_mutation_response(&mut first, response(b"old"))
        .await
        .unwrap();
    store.commit(first).await.unwrap();

    let mut admin = store.pool().begin().await.unwrap();
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(context.tenant_id.as_uuid())
        .execute(&mut *admin)
        .await
        .unwrap();
    sqlx::query(
        "update memphant.mutation_ledger
         set expires_at = statement_timestamp() + interval '500 milliseconds'
         where tenant_id = $1 and verb = 'retain' and idempotency_key = 'expired'",
    )
    .bind(context.tenant_id.as_uuid())
    .execute(&mut *admin)
    .await
    .unwrap();
    admin.commit().await.unwrap();

    let mut second = store.begin(&context).await.unwrap();
    tokio::time::sleep(Duration::from_millis(650)).await;
    assert_eq!(
        store
            .stage_mutation_claim(&mut second, claim(&context, "expired", 2))
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    store
        .stage_mutation_response(&mut second, response(b"new"))
        .await
        .unwrap();
    store.commit(second).await.unwrap();
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn same_tenant_different_subject_cannot_reuse_an_unexpired_key() {
    let store = store().await;
    let first = context(&store).await;
    let second = context_for_tenant(&store, first.tenant_id).await;
    commit_receipt(&store, &first, "subject-key", 1).await;

    let mut tx = store.begin(&second).await.unwrap();
    assert!(matches!(
        store
            .stage_mutation_claim(&mut tx, claim(&second, "subject-key", 1))
            .await,
        Err(StoreError::IdempotencyConflict)
    ));
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn different_tenants_can_use_the_same_key_independently() {
    let store = store().await;
    let first = context(&store).await;
    let second = context(&store).await;

    commit_receipt(&store, &first, "tenant-key", 1).await;
    commit_receipt(&store, &second, "tenant-key", 1).await;
    assert_eq!(count_receipts(&store, &first).await, 1);
    assert_eq!(count_receipts(&store, &second).await, 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn concurrent_same_claim_waits_then_replays_the_committed_response() {
    let store = Arc::new(store().await);
    let context = context(&store).await;
    let mut first = store.begin(&context).await.unwrap();
    store
        .stage_mutation_claim(&mut first, claim(&context, "concurrent", 3))
        .await
        .unwrap();

    let second_store = Arc::clone(&store);
    let second_context = context.clone();
    let second = tokio::spawn(async move {
        let mut tx = second_store.begin(&second_context).await.unwrap();
        let outcome = second_store
            .stage_mutation_claim(&mut tx, claim(&second_context, "concurrent", 3))
            .await
            .unwrap();
        second_store.commit(tx).await.unwrap();
        outcome
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(
        !second.is_finished(),
        "concurrent claimant must wait for owner"
    );

    store
        .stage_mutation_response(&mut first, response(b"winner"))
        .await
        .unwrap();
    store.commit(first).await.unwrap();
    assert_eq!(
        second.await.unwrap(),
        MutationClaimOutcome::Replay(response(b"winner"))
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn concurrent_different_hash_waits_then_conflicts() {
    let store = Arc::new(store().await);
    let context = context(&store).await;
    let mut owner = store.begin(&context).await.unwrap();
    store
        .stage_mutation_claim(&mut owner, claim(&context, "hash-race", 1))
        .await
        .unwrap();

    let waiter_store = Arc::clone(&store);
    let waiter_context = context.clone();
    let waiter = tokio::spawn(async move {
        let mut tx = waiter_store.begin(&waiter_context).await.unwrap();
        waiter_store
            .stage_mutation_claim(&mut tx, claim(&waiter_context, "hash-race", 2))
            .await
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(
        !waiter.is_finished(),
        "different hash must wait for key owner"
    );
    store
        .stage_mutation_response(&mut owner, response(b"owner"))
        .await
        .unwrap();
    store.commit(owner).await.unwrap();
    assert!(matches!(
        waiter.await.unwrap(),
        Err(StoreError::IdempotencyConflict)
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn owner_rollback_allows_waiter_to_execute() {
    let store = Arc::new(store().await);
    let context = context(&store).await;
    let mut owner = store.begin(&context).await.unwrap();
    store
        .stage_mutation_claim(&mut owner, claim(&context, "rollback-race", 1))
        .await
        .unwrap();
    store
        .stage_episode(&mut owner, episode(&context, "rolled-back-owner"))
        .await
        .unwrap();

    let waiter_store = Arc::clone(&store);
    let waiter_context = context.clone();
    let waiter = tokio::spawn(async move {
        let mut tx = waiter_store.begin(&waiter_context).await.unwrap();
        let outcome = waiter_store
            .stage_mutation_claim(&mut tx, claim(&waiter_context, "rollback-race", 1))
            .await
            .unwrap();
        waiter_store
            .stage_mutation_response(&mut tx, response(b"waiter"))
            .await
            .unwrap();
        waiter_store.commit(tx).await.unwrap();
        outcome
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(
        !waiter.is_finished(),
        "waiter must block on the uncommitted owner"
    );
    drop(owner);
    assert_eq!(waiter.await.unwrap(), MutationClaimOutcome::Execute);
    assert_eq!(count_episodes(&store, &context).await, 0);
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn dropped_transaction_and_missing_response_commit_both_roll_back() {
    let store = store().await;
    let context = context(&store).await;
    {
        let mut dropped = store.begin(&context).await.unwrap();
        store
            .stage_mutation_claim(&mut dropped, claim(&context, "dropped", 4))
            .await
            .unwrap();
        store
            .stage_episode(&mut dropped, episode(&context, "dropped-episode"))
            .await
            .unwrap();
    }
    assert_eq!(count_episodes(&store, &context).await, 0);
    let mut retry = store.begin(&context).await.unwrap();
    assert_eq!(
        store
            .stage_mutation_claim(&mut retry, claim(&context, "dropped", 4))
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    store
        .stage_mutation_response(&mut retry, response(b"retry"))
        .await
        .unwrap();
    store.commit(retry).await.unwrap();

    let mut missing = store.begin(&context).await.unwrap();
    store
        .stage_mutation_claim(&mut missing, claim(&context, "missing", 5))
        .await
        .unwrap();
    store
        .stage_episode(&mut missing, episode(&context, "missing-episode"))
        .await
        .unwrap();
    assert!(matches!(
        store.commit(missing).await,
        Err(StoreError::Conflict(_))
    ));
    assert_eq!(count_episodes(&store, &context).await, 0);
    let mut missing_retry = store.begin(&context).await.unwrap();
    assert_eq!(
        store
            .stage_mutation_claim(&mut missing_retry, claim(&context, "missing", 5))
            .await
            .unwrap(),
        MutationClaimOutcome::Execute
    );
    store
        .stage_mutation_response(&mut missing_retry, response(b"missing-retry"))
        .await
        .unwrap();
    store.commit(missing_retry).await.unwrap();
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn retain_service_episode_and_resource_commit_with_receipt_once() {
    let store = store().await;
    let context = context(&store).await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );

    let episode = episode_request(&context);
    let episode_first = service
        .retain(
            &context,
            "pg-retain-episode",
            TrustLevel::TrustedUser,
            episode.clone(),
        )
        .await
        .unwrap();
    let episode_replay = service
        .retain(
            &context,
            "pg-retain-episode",
            TrustLevel::TrustedUser,
            episode,
        )
        .await
        .unwrap();
    assert_eq!(episode_replay, episode_first);
    assert_eq!(episode_replay.body(), episode_first.body());
    let episode_result: RetainEpisodeHttpResponse =
        serde_json::from_slice(episode_first.body()).unwrap();
    let episode_id = episode_result.episode_id.unwrap();

    let resource = resource_request(&context);
    let resource_first = service
        .retain(
            &context,
            "pg-retain-resource",
            TrustLevel::TrustedUser,
            resource.clone(),
        )
        .await
        .unwrap();
    let resource_replay = service
        .retain(
            &context,
            "pg-retain-resource",
            TrustLevel::TrustedUser,
            resource,
        )
        .await
        .unwrap();
    assert_eq!(resource_replay, resource_first);
    assert_eq!(resource_replay.body(), resource_first.body());
    let resource_result: RetainEpisodeHttpResponse =
        serde_json::from_slice(resource_first.body()).unwrap();
    let resource_id = resource_result.resource_id.unwrap();

    let mut tx = store.pool().begin().await.unwrap();
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(context.tenant_id.as_uuid())
        .execute(&mut *tx)
        .await
        .unwrap();
    let episode_count: i64 = sqlx::query_scalar(
        "select count(*) from memphant.episode
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and source_ref = 'pg-service:episode' and id = $4",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .bind(episode_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let resource_count: i64 = sqlx::query_scalar(
        "select count(*) from memphant.resource
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and source_ref = 'pg-service:resource' and id = $4",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .bind(resource_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let episode_jobs: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and job_type = 'reflect_episode' and target_id = $4 and state = 'queued'",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .bind(episode_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let resource_jobs: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and job_type = 'reflect_resource' and target_id = $4 and state = 'queued'",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .bind(resource_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let receipts: i64 = sqlx::query_scalar(
        "select count(*) from memphant.mutation_ledger
         where tenant_id = $1 and verb = 'retain'
           and idempotency_key in ('pg-retain-episode', 'pg-retain-resource')
           and response_body is not null",
    )
    .bind(context.tenant_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    tx.rollback().await.unwrap();

    assert_eq!((episode_count, resource_count), (1, 1));
    assert_eq!((episode_jobs, resource_jobs), (1, 1));
    assert_eq!(receipts, 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn retain_service_direct_concurrent_winner_commits_once() {
    let store = store().await;
    let context = context(&store).await;
    let embedder = Arc::new(BarrierEmbedding {
        rendezvous: (Mutex::new(0), Condvar::new()),
        calls: AtomicUsize::new(0),
    });
    let service = Arc::new(MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        embedder.clone(),
    ));
    let request = direct_request(&context);
    let first = {
        let service = Arc::clone(&service);
        let context = context.clone();
        let request = request.clone();
        tokio::spawn(async move {
            service
                .retain(
                    &context,
                    "pg-retain-direct-race",
                    TrustLevel::TrustedUser,
                    request,
                )
                .await
        })
    };
    let second = {
        let service = Arc::clone(&service);
        let context = context.clone();
        tokio::spawn(async move {
            service
                .retain(
                    &context,
                    "pg-retain-direct-race",
                    TrustLevel::TrustedUser,
                    request,
                )
                .await
        })
    };
    let (first, second) = tokio::join!(first, second);
    let first = first.unwrap().unwrap();
    let second = second.unwrap().unwrap();
    assert_eq!(first, second);
    assert_eq!(embedder.calls.load(Ordering::SeqCst), 2);
    let result: RetainEpisodeHttpResponse = serde_json::from_slice(first.body()).unwrap();
    let trace_uuid: Uuid = result
        .trace_ref
        .unwrap()
        .strip_prefix("memphant://trace/")
        .unwrap()
        .parse()
        .unwrap();
    let job_id = memphant_types::JobId::from_u128(trace_uuid.as_u128());

    let mut tx = store.pool().begin().await.unwrap();
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(context.tenant_id.as_uuid())
        .execute(&mut *tx)
        .await
        .unwrap();
    let units: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_unit
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and source_ref = 'pg-service:direct'",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let jobs: i64 = sqlx::query_scalar(
        "select count(*) from memphant.job_state
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3
           and id = $4 and job_type = 'direct' and state = 'done' and result is not null",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .bind(trace_uuid)
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let receipts: i64 = sqlx::query_scalar(
        "select count(*) from memphant.mutation_ledger
         where tenant_id = $1 and verb = 'retain'
           and idempotency_key = 'pg-retain-direct-race' and response_body is not null",
    )
    .bind(context.tenant_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    tx.rollback().await.unwrap();

    assert_eq!((units, jobs, receipts), (1, 1, 1));
    assert!(
        store
            .fetch_reflect_trace(&context, job_id, COMPILER_VERSION)
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn correct_service_releases_claim_before_embedding_and_commits_once() {
    let store = store().await;
    let context = context(&store).await;
    let seed_service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let seed = seed_service
        .retain(
            &context,
            "pg-correct-race-seed",
            TrustLevel::TrustedUser,
            direct_request(&context),
        )
        .await
        .unwrap();
    let seed: RetainEpisodeHttpResponse = serde_json::from_slice(seed.body()).unwrap();
    let unit_id = seed.unit_ids[0];

    let embedder = Arc::new(BarrierEmbedding {
        rendezvous: (Mutex::new(0), Condvar::new()),
        calls: AtomicUsize::new(0),
    });
    let service = Arc::new(MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        embedder.clone(),
    ));
    let request = correct_request(&context, unit_id);
    let first = {
        let service = Arc::clone(&service);
        let context = context.clone();
        let request = request.clone();
        tokio::spawn(async move { service.correct(&context, "pg-correct-race", request).await })
    };
    let second = {
        let service = Arc::clone(&service);
        let context = context.clone();
        tokio::spawn(async move { service.correct(&context, "pg-correct-race", request).await })
    };

    let (first, second) = tokio::join!(first, second);
    let first = first.unwrap().unwrap();
    let second = second.unwrap().unwrap();
    assert_eq!(first, second);
    assert_eq!(embedder.calls.load(Ordering::SeqCst), 2);
    let result: CorrectResult = serde_json::from_slice(first.body()).unwrap();
    assert_eq!(result.superseded, vec![unit_id]);
    assert_eq!(result.created.len(), 2);

    let mut tx = store.pool().begin().await.unwrap();
    sqlx::query("select memphant.bind_tenant($1)")
        .bind(context.tenant_id.as_uuid())
        .execute(&mut *tx)
        .await
        .unwrap();
    let units: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_unit
         where tenant_id = $1 and data_subject_id = $2 and subject_generation = $3",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.data_subject_id.as_uuid())
    .bind(context.subject_generation as i64)
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let edges: i64 = sqlx::query_scalar(
        "select count(*) from memphant.memory_edge
         where tenant_id = $1 and scope_id = $2",
    )
    .bind(context.tenant_id.as_uuid())
    .bind(context.scope_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    let receipts: i64 = sqlx::query_scalar(
        "select count(*) from memphant.mutation_ledger
         where tenant_id = $1 and verb = 'correct'
           and idempotency_key = 'pg-correct-race' and response_body is not null",
    )
    .bind(context.tenant_id.as_uuid())
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    tx.rollback().await.unwrap();

    assert_eq!((units, edges, receipts), (3, 2, 1));
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn bounded_purge_physically_deletes_only_expired_receipts_for_its_tenant() {
    let store = store().await;
    let first = context(&store).await;
    let second = context(&store).await;
    for key in ["expired-a", "expired-b", "current"] {
        commit_receipt(&store, &first, key, 1).await;
    }
    commit_receipt(&store, &second, "other-expired", 1).await;
    expire_receipt(&store, &first, "expired-a").await;
    expire_receipt(&store, &first, "expired-b").await;
    expire_receipt(&store, &second, "other-expired").await;

    assert_eq!(
        store
            .purge_expired_mutation_receipts(first.tenant_id, 1)
            .await
            .unwrap(),
        1
    );
    assert_eq!(count_receipts(&store, &first).await, 2);
    assert_eq!(count_receipts(&store, &second).await, 1);
    assert_eq!(
        store
            .purge_expired_mutation_receipts(first.tenant_id, 10)
            .await
            .unwrap(),
        1
    );
    assert_eq!(count_receipts(&store, &first).await, 1);
    assert_eq!(count_receipts(&store, &second).await, 1);
    assert_eq!(
        store
            .purge_expired_mutation_receipts(first.tenant_id, 10)
            .await
            .unwrap(),
        0
    );
}
