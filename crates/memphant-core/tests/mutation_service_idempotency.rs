use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex};
use std::time::Duration;

use memphant_core::service::{MemoryService, ServiceError};
use memphant_core::{
    EmbedError, EmbeddingProvider, FixedClock, InMemoryStore, MemoryStore, NoopEmbedding,
    StoreError,
};
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    CorrectRequest, CorrectResult, CorrectSelector, CorrectionPayload, ForgetRequest, ForgetResult,
    ForgetSelector, MarkOutcome, MarkRequest, MarkResult, MemoryKind, NewMemoryUnit,
    RecallHttpRequest, RecallMode, ReflectAccepted, ReflectRequest, ResolvedMemoryContext,
    RetainEpisodeHttpRequest, RetainEpisodeHttpResponse, RetainEpisodePayload, RetainPayload,
    RetainResourcePayload, RetainUnitPayload, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-15T00:00:00Z");

#[derive(Default)]
struct OneShotEmbedding(AtomicUsize);

impl EmbeddingProvider for OneShotEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        if self.0.fetch_add(1, Ordering::SeqCst) == 0 {
            Ok(vec![vec![1.0]; texts.len()])
        } else {
            Err(EmbedError::Unavailable(
                "must not run on replay".to_string(),
            ))
        }
    }

    fn dimensions(&self) -> usize {
        1
    }

    fn id(&self) -> &str {
        "test-one-shot"
    }
}

#[derive(Default)]
struct FailOnceEmbedding(AtomicUsize);

impl EmbeddingProvider for FailOnceEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        if self.0.fetch_add(1, Ordering::SeqCst) == 0 {
            Err(EmbedError::Unavailable("first call fails".to_string()))
        } else {
            Ok(vec![vec![1.0]; texts.len()])
        }
    }

    fn dimensions(&self) -> usize {
        1
    }

    fn id(&self) -> &str {
        "test-fail-once"
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
        "test-barrier"
    }
}

async fn context(store: &InMemoryStore, tenant: TenantId) -> ResolvedMemoryContext {
    let binding = store
        .resolve_context_binding(
            tenant,
            "service-idempotency".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "subject:user-1".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "actor:user-1".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "scope:root".to_string(),
                    kind: "memory".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "agent:l0".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .unwrap();
    store
        .resolve_memory_context(
            tenant,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .unwrap()
}

async fn seed_unit(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    body: &str,
) -> memphant_types::UnitId {
    let mut tx = store.begin(context).await.unwrap();
    let id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("profile:timezone".to_string()),
                predicate: None,
                body: body.to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: None,
                source_ref: "test:seed".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    id
}

fn is_idempotency_conflict(error: &ServiceError) -> bool {
    matches!(
        error,
        ServiceError::Core(memphant_core::CoreError::Store(
            StoreError::IdempotencyConflict
        ))
    )
}

#[tokio::test]
async fn reflect_acceptance_is_atomic_replayable_and_worker_owned() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let request = ReflectRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
    };

    let first = service
        .reflect(&context, "reflect-1", request.clone())
        .await
        .unwrap();
    assert_eq!(first.status(), 202);
    let accepted: ReflectAccepted = serde_json::from_slice(first.body()).unwrap();
    assert!(store.memory_units(context.tenant_id).is_empty());
    assert_eq!(store.reflect_jobs(context.tenant_id).len(), 1);

    let replay = service
        .reflect(&context, "reflect-1", request)
        .await
        .unwrap();
    assert_eq!(replay, first);
    assert_eq!(store.reflect_jobs(context.tenant_id).len(), 1);

    assert_eq!(service.run_worker_tick(1).await.unwrap().completed, 1);
    assert!(
        store
            .fetch_reflect_trace(&context, accepted.job_id, memphant_types::COMPILER_VERSION)
            .await
            .unwrap()
            .is_some(),
        "a zero-mint scope consolidation still persists its trace"
    );
}

#[tokio::test]
async fn worker_runs_source_before_the_scope_barrier() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    service
        .retain(
            &context,
            "retain-before-reflect",
            TrustLevel::TrustedUser,
            episode_request(&context, "A durable source observation."),
        )
        .await
        .unwrap();
    let accepted = service
        .reflect(
            &context,
            "reflect-after-retain",
            ReflectRequest {
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
            },
        )
        .await
        .unwrap();
    let scope: ReflectAccepted = serde_json::from_slice(accepted.body()).unwrap();

    assert_eq!(service.run_worker_tick(2).await.unwrap().completed, 2);
    assert!(!store.memory_units(context.tenant_id).is_empty());
    assert!(
        store
            .fetch_reflect_trace(&context, scope.job_id, memphant_types::COMPILER_VERSION)
            .await
            .unwrap()
            .is_some()
    );
}

fn episode_request(context: &ResolvedMemoryContext, body: &str) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "test:episode".to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
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
        source_ref: "test:resource".to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: RetainPayload::Resource(RetainResourcePayload {
            uri: "file:///notes.txt".to_string(),
            mime_type: "text/plain".to_string(),
            content_hash: "sha256:notes".to_string(),
            kind: None,
            revision: None,
            body: Some("durable resource body".to_string()),
        }),
    }
}

fn direct_request(context: &ResolvedMemoryContext, body: &str) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "test:direct".to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: RetainPayload::Unit(RetainUnitPayload {
            kind: MemoryKind::Semantic,
            fact_key: Some("profile:timezone".to_string()),
            subject: None,
            predicate: "timezone".to_string(),
            body: body.to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
            target_unit_ids: None,
        }),
    }
}

#[tokio::test]
async fn retain_episode_replays_exact_response_and_conflict_does_not_mutate() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let request = episode_request(&context, "user remembers a durable fact");

    let first = service
        .retain(
            &context,
            "retain-episode-1",
            TrustLevel::TrustedUser,
            request.clone(),
        )
        .await
        .unwrap();
    let replay = service
        .retain(
            &context,
            "retain-episode-1",
            TrustLevel::TrustedUser,
            request,
        )
        .await
        .unwrap();
    assert_eq!(replay, first);
    assert_eq!(replay.body(), first.body());
    assert_eq!(store.episodes(context.tenant_id).len(), 1);
    assert_eq!(store.reflect_jobs(context.tenant_id).len(), 1);
    assert_eq!(store.episodes(context.tenant_id)[0].observation_count, 1);

    let error = service
        .retain(
            &context,
            "retain-episode-1",
            TrustLevel::TrustedUser,
            episode_request(&context, "user remembers a changed durable fact"),
        )
        .await
        .unwrap_err();
    assert!(is_idempotency_conflict(&error));
    assert_eq!(store.episodes(context.tenant_id).len(), 1);
    assert_eq!(store.reflect_jobs(context.tenant_id).len(), 1);
}

#[tokio::test]
async fn retain_resource_replays_without_duplicate_resource_or_job() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let request = resource_request(&context);
    let first = service
        .retain(
            &context,
            "retain-resource-1",
            TrustLevel::TrustedUser,
            request.clone(),
        )
        .await
        .unwrap();
    let replay = service
        .retain(
            &context,
            "retain-resource-1",
            TrustLevel::TrustedUser,
            request,
        )
        .await
        .unwrap();
    assert_eq!(replay, first);
    assert_eq!(store.resources(context.tenant_id).len(), 1);
    assert_eq!(store.reflect_jobs(context.tenant_id).len(), 1);
}

#[tokio::test]
async fn retain_direct_replay_skips_provider_and_keeps_one_unit_and_trace() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let embedder = Arc::new(OneShotEmbedding::default());
    let service = MemoryService::new(Arc::new(store.clone()), Arc::new(CLOCK), embedder.clone());
    let request = direct_request(&context, "timezone is pacific time");
    let first = service
        .retain(
            &context,
            "retain-direct-1",
            TrustLevel::TrustedUser,
            request.clone(),
        )
        .await
        .unwrap();
    let replay = service
        .retain(
            &context,
            "retain-direct-1",
            TrustLevel::TrustedUser,
            request,
        )
        .await
        .unwrap();
    assert_eq!(replay, first);
    assert_eq!(embedder.0.load(Ordering::SeqCst), 1);
    assert_eq!(store.memory_units(context.tenant_id).len(), 1);
    assert!(store.reflect_jobs(context.tenant_id).is_empty());
    let result: RetainEpisodeHttpResponse = serde_json::from_slice(first.body()).unwrap();
    let job: uuid::Uuid = result
        .trace_ref
        .unwrap()
        .strip_prefix("memphant://trace/")
        .unwrap()
        .parse()
        .unwrap();
    assert!(
        store
            .fetch_reflect_trace(
                &context,
                memphant_types::JobId::from_u128(job.as_u128()),
                memphant_types::COMPILER_VERSION,
            )
            .await
            .unwrap()
            .is_some()
    );

    let error = service
        .retain(
            &context,
            "retain-direct-1",
            TrustLevel::TrustedUser,
            direct_request(&context, "timezone changed to mountain time"),
        )
        .await
        .unwrap_err();
    assert!(is_idempotency_conflict(&error));
    assert_eq!(embedder.0.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn retain_direct_provider_failure_does_not_reserve_key() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let embedder = Arc::new(FailOnceEmbedding::default());
    let service = MemoryService::new(Arc::new(store.clone()), Arc::new(CLOCK), embedder.clone());
    let request = direct_request(&context, "timezone is pacific time");

    service
        .retain(
            &context,
            "retain-direct-retry",
            TrustLevel::TrustedUser,
            request.clone(),
        )
        .await
        .unwrap_err();
    assert!(store.memory_units(context.tenant_id).is_empty());
    assert!(store.reflect_traces(context.tenant_id).is_empty());
    let response = service
        .retain(
            &context,
            "retain-direct-retry",
            TrustLevel::TrustedUser,
            request,
        )
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(store.memory_units(context.tenant_id).len(), 1);
    assert_eq!(store.reflect_traces(context.tenant_id).len(), 1);
    let result: RetainEpisodeHttpResponse = serde_json::from_slice(response.body()).unwrap();
    let job: uuid::Uuid = result
        .trace_ref
        .unwrap()
        .strip_prefix("memphant://trace/")
        .unwrap()
        .parse()
        .unwrap();
    assert!(
        store
            .fetch_reflect_trace(
                &context,
                memphant_types::JobId::from_u128(job.as_u128()),
                memphant_types::COMPILER_VERSION,
            )
            .await
            .unwrap()
            .is_some()
    );
    assert_eq!(embedder.0.load(Ordering::SeqCst), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn concurrent_direct_retain_returns_one_winner_response() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let embedder = Arc::new(BarrierEmbedding {
        rendezvous: (Mutex::new(0), Condvar::new()),
        calls: AtomicUsize::new(0),
    });
    let service = Arc::new(MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        embedder.clone(),
    ));
    let request = direct_request(&context, "timezone is pacific time");
    let first = {
        let service = Arc::clone(&service);
        let context = context.clone();
        let request = request.clone();
        tokio::spawn(async move {
            service
                .retain(
                    &context,
                    "retain-direct-race",
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
                    "retain-direct-race",
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
    assert_eq!(store.memory_units(context.tenant_id).len(), 1);
    assert!(store.reflect_jobs(context.tenant_id).is_empty());
    assert_eq!(store.reflect_traces(context.tenant_id).len(), 1);
    let result: RetainEpisodeHttpResponse = serde_json::from_slice(first.body()).unwrap();
    let job: uuid::Uuid = result
        .trace_ref
        .unwrap()
        .strip_prefix("memphant://trace/")
        .unwrap()
        .parse()
        .unwrap();
    assert!(
        store
            .fetch_reflect_trace(
                &context,
                memphant_types::JobId::from_u128(job.as_u128()),
                memphant_types::COMPILER_VERSION,
            )
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test]
async fn correct_replays_exact_response_and_changed_request_does_not_mutate() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let unit_id = seed_unit(&store, &context, "Timezone is UTC.").await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let request = CorrectRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        selector: CorrectSelector {
            memory_unit_id: unit_id,
        },
        correction: CorrectionPayload {
            value: "Timezone is PST.".to_string(),
            reason: "user correction".to_string(),
            source_ref: "test:correction".to_string(),
            observed_at: CLOCK.0.to_string(),
            valid_from: None,
            valid_to: None,
        },
    };

    let first = service
        .correct(&context, "correct-1", request.clone())
        .await
        .unwrap();
    let count_after_first = store.memory_units(context.tenant_id).len();
    let replay = service
        .correct(&context, "correct-1", request.clone())
        .await
        .unwrap();
    assert_eq!(replay, first);
    assert_eq!(first.status(), 200);
    let result: CorrectResult = serde_json::from_slice(first.body()).unwrap();
    assert!(!result.created.is_empty());
    let count_after_replay = store.memory_units(context.tenant_id).len();
    assert_eq!(count_after_replay, count_after_first);

    let mut changed = request;
    changed.correction.reason = "different request".to_string();
    let error = service
        .correct(&context, "correct-1", changed)
        .await
        .unwrap_err();
    assert!(is_idempotency_conflict(&error));
    assert_eq!(
        store.memory_units(context.tenant_id).len(),
        count_after_replay
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn concurrent_correct_releases_the_claim_before_embedding_and_commits_once() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let unit_id = seed_unit(&store, &context, "Timezone is UTC.").await;
    let embedder = Arc::new(BarrierEmbedding {
        rendezvous: (Mutex::new(0), Condvar::new()),
        calls: AtomicUsize::new(0),
    });
    let service = Arc::new(MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        embedder.clone(),
    ));
    let request = CorrectRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        selector: CorrectSelector {
            memory_unit_id: unit_id,
        },
        correction: CorrectionPayload {
            value: "Timezone is PST.".to_string(),
            reason: "user correction".to_string(),
            source_ref: "test:correction-race".to_string(),
            observed_at: CLOCK.0.to_string(),
            valid_from: None,
            valid_to: None,
        },
    };
    let first = {
        let service = Arc::clone(&service);
        let context = context.clone();
        let request = request.clone();
        tokio::spawn(async move { service.correct(&context, "correct-race", request).await })
    };
    let second = {
        let service = Arc::clone(&service);
        let context = context.clone();
        tokio::spawn(async move { service.correct(&context, "correct-race", request).await })
    };

    let (first, second) = tokio::join!(first, second);
    let first = first.unwrap().unwrap();
    let second = second.unwrap().unwrap();
    assert_eq!(first, second);
    assert_eq!(embedder.calls.load(Ordering::SeqCst), 2);
    let result: CorrectResult = serde_json::from_slice(first.body()).unwrap();
    assert_eq!(result.superseded, vec![unit_id]);
    assert_eq!(result.created.len(), 2);
    let units = store.memory_units(context.tenant_id);
    assert_eq!(units.len(), 3);
    assert_eq!(
        units
            .iter()
            .filter(|unit| unit.body == "Timezone is PST.")
            .count(),
        1
    );
    assert_eq!(store.memory_edges(context.tenant_id).len(), 2);
}

#[tokio::test]
async fn correct_replay_does_not_call_the_embedder_again() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let unit_id = seed_unit(&store, &context, "Timezone is UTC.").await;
    let embedder = Arc::new(OneShotEmbedding::default());
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), embedder.clone());
    let request = CorrectRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        selector: CorrectSelector {
            memory_unit_id: unit_id,
        },
        correction: CorrectionPayload {
            value: "Timezone is PST.".to_string(),
            reason: "user correction".to_string(),
            source_ref: "test:correction-provider".to_string(),
            observed_at: CLOCK.0.to_string(),
            valid_from: None,
            valid_to: None,
        },
    };

    let first = service
        .correct(&context, "correct-provider-1", request.clone())
        .await
        .unwrap();
    let replay = service
        .correct(&context, "correct-provider-1", request)
        .await
        .unwrap();

    assert_eq!(replay, first);
    assert_eq!(embedder.0.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn forget_replays_exact_response_and_changed_request_does_not_delete() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let first_id = seed_unit(&store, &context, "Timezone is UTC.").await;
    let second_id = seed_unit(&store, &context, "Favorite editor is Helix.").await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let request = ForgetRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        selector: ForgetSelector {
            memory_unit_id: Some(first_id),
            episode_id: None,
            resource_id: None,
            scope_id: context.scope_id,
        },
        reason: "privacy request".to_string(),
    };

    let first = service
        .forget(&context, "forget-1", request.clone())
        .await
        .unwrap();
    let replay = service
        .forget(&context, "forget-1", request.clone())
        .await
        .unwrap();
    assert_eq!(replay, first);
    let result: ForgetResult = serde_json::from_slice(first.body()).unwrap();
    assert_eq!(result.invalidated_units, vec![first_id]);

    let mut changed = request;
    changed.selector.memory_unit_id = Some(second_id);
    let error = service
        .forget(&context, "forget-1", changed)
        .await
        .unwrap_err();
    assert!(is_idempotency_conflict(&error));
    assert!(
        store
            .memory_units(context.tenant_id)
            .iter()
            .any(|unit| unit.id == second_id)
    );
}

#[tokio::test]
async fn invalid_forget_does_not_reserve_the_idempotency_key() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let unit_id = seed_unit(&store, &context, "Timezone is UTC.").await;
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
    let mut request = ForgetRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        selector: ForgetSelector {
            memory_unit_id: None,
            episode_id: None,
            resource_id: None,
            scope_id: context.scope_id,
        },
        reason: "privacy request".to_string(),
    };

    service
        .forget(&context, "forget-invalid-first", request.clone())
        .await
        .unwrap_err();
    request.selector.memory_unit_id = Some(unit_id);
    let response = service
        .forget(&context, "forget-invalid-first", request)
        .await
        .unwrap();
    let result: ForgetResult = serde_json::from_slice(response.body()).unwrap();
    assert_eq!(result.invalidated_units, vec![unit_id]);
}

#[tokio::test]
async fn mark_replays_exact_response_without_duplicate_review_event() {
    let store = InMemoryStore::default();
    let context = context(&store, TenantId::new()).await;
    let unit_id = seed_unit(&store, &context, "Timezone is UTC.").await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let recall = service
        .recall(
            context.clone(),
            RecallHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                query: "timezone".to_string(),
                limit: Some(8),
                budget_tokens: Some(256),
                mode: Some(RecallMode::Fast),
                include_beliefs: Some(true),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .unwrap();
    let request = MarkRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        trace_id: recall.trace_id,
        caller_id: "test".to_string(),
        used_ids: vec![unit_id],
        outcome: MarkOutcome::Success,
    };

    let first = service
        .mark(&context, "mark-1", request.clone())
        .await
        .unwrap();
    let replay = service.mark(&context, "mark-1", request).await.unwrap();
    assert_eq!(replay, first);
    let result: MarkResult = serde_json::from_slice(first.body()).unwrap();
    assert!(result.accepted);
    assert_eq!(
        store
            .fetch_review_events(
                &context,
                &[unit_id],
                &memphant_types::RecallTime {
                    evaluated_at: CLOCK.0.to_string(),
                    transaction_as_of: CLOCK.0.to_string(),
                    valid_at: CLOCK.0.to_string(),
                },
            )
            .await
            .unwrap()
            .len(),
        1
    );
}
