use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use memphant_core::deep_recall::{
    DeepRecallProvider, DeepRecallProviderError, DeepRecallProviderRequest,
    DeepRecallProviderResult,
};
use memphant_core::service::{MemoryService, ServiceError};
use memphant_core::{CoreError, FixedClock, InMemoryStore, MemoryStore, NoopEmbedding};
use memphant_types::{
    ActorId, DeepProviderIdentity, DeepRecallLimits, DeepRecallStatus, DeepRecallStopReason,
    DeepRecallUsage, EVIDENCE_DISPOSITION_CONTRACT_REVISION, EvidenceDisposition, EvidenceStatus,
    MemoryKind, NewEpisode, NewMemoryUnit, RecallChannel, RecallMode, RecallRequest, ReflectJob,
    ReflectJobKind, ScopeId, TenantId, TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-20T00:00:00Z");

fn evidence(_status: DeepRecallStatus) -> EvidenceDisposition {
    let status = EvidenceStatus::Insufficient;
    EvidenceDisposition {
        contract_revision: EVIDENCE_DISPOSITION_CONTRACT_REVISION.to_string(),
        status,
        answer_policy: status.answer_policy(),
        reason: "scripted test disposition".to_string(),
    }
}

struct RecordingProvider {
    identity: DeepProviderIdentity,
    limits: DeepRecallLimits,
    result: Mutex<DeepRecallProviderResult>,
    calls: AtomicUsize,
    workspaces: Mutex<Vec<String>>,
    delay_ms: u64,
}

impl RecordingProvider {
    fn completed(source_ids: Vec<uuid::Uuid>) -> Self {
        let evidence_status = EvidenceStatus::Insufficient;
        Self {
            identity: DeepProviderIdentity {
                provider: "test".to_string(),
                model: "test/deep".to_string(),
                prompt_hash: "prompt-v1".to_string(),
                config_hash: "config-v1".to_string(),
            },
            limits: limits(),
            result: Mutex::new(DeepRecallProviderResult {
                status: DeepRecallStatus::Completed,
                stop_reason: DeepRecallStopReason::Completed,
                source_ids,
                usage: DeepRecallUsage::default(),
                generation_ids: Vec::new(),
                observed_provider: Some("test".to_string()),
                observed_model: Some("test/deep".to_string()),
                evidence: EvidenceDisposition {
                    contract_revision: EVIDENCE_DISPOSITION_CONTRACT_REVISION.to_string(),
                    status: evidence_status,
                    answer_policy: evidence_status.answer_policy(),
                    reason: "scripted test disposition".to_string(),
                },
            }),
            calls: AtomicUsize::new(0),
            workspaces: Mutex::new(Vec::new()),
            delay_ms: 0,
        }
    }

    fn with_result(result: DeepRecallProviderResult) -> Self {
        Self {
            identity: DeepProviderIdentity {
                provider: "test".to_string(),
                model: "test/deep".to_string(),
                prompt_hash: "prompt-v1".to_string(),
                config_hash: "config-v1".to_string(),
            },
            limits: limits(),
            result: Mutex::new(result),
            calls: AtomicUsize::new(0),
            workspaces: Mutex::new(Vec::new()),
            delay_ms: 0,
        }
    }

    fn with_delay(mut self, delay_ms: u64) -> Self {
        self.delay_ms = delay_ms;
        self
    }
}

impl DeepRecallProvider for RecordingProvider {
    fn identity(&self) -> &DeepProviderIdentity {
        &self.identity
    }

    fn limits(&self) -> DeepRecallLimits {
        self.limits
    }

    fn gather<'a>(
        &'a self,
        request: DeepRecallProviderRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<DeepRecallProviderResult, DeepRecallProviderError>>
                + Send
                + 'a,
        >,
    > {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.workspaces.lock().unwrap().push(
            request
                .workspace
                .files
                .iter()
                .map(|file| file.body.as_str())
                .collect::<Vec<_>>()
                .join("\n"),
        );
        let result = self.result.lock().unwrap().clone();
        let delay_ms = self.delay_ms;
        Box::pin(async move {
            if delay_ms > 0 {
                tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
            }
            Ok(result)
        })
    }
}

fn limits() -> DeepRecallLimits {
    DeepRecallLimits {
        wall_time_ms: 120_000,
        max_tool_iterations: 24,
        max_context_tokens: 96_000,
        max_spend_micros: 300_000,
    }
}

fn request(context: memphant_types::ResolvedMemoryContext, mode: RecallMode) -> RecallRequest {
    RecallRequest {
        compact_only: false,
        context,
        query: "What approval is required before Stargate deploy?".to_string(),
        k: 1,
        budget_tokens: 64,
        mode,
        include_beliefs: false,
        edge_expansion_enabled: true,
        context_packing_abstention_enabled: true,
        procedure_recall_enabled: true,
        decay_enabled: true,
        engine_version: "deep-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

async fn seeded_service() -> (
    InMemoryStore,
    memphant_types::ResolvedMemoryContext,
    UnitId,
    uuid::Uuid,
) {
    let store = InMemoryStore::default();
    let tenant = TenantId::from_u128(81_000);
    let scope = ScopeId::from_u128(81_001);
    let actor = ActorId::from_u128(81_002);
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);
    let mut tx = store.begin(&context).await.unwrap();
    let episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: scope,
                agent_node_id: context.agent_node_id,
                subject_generation: 0,
                actor_id: actor,
                source_kind: "fixture".to_string(),
                source_ref: "test:deep".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_trust: TrustLevel::TrustedSystem,
                dedup_key: "deep-answer".to_string(),
                body: "Buried archive: Stargate requires heliotrope approval.".to_string(),
            },
        )
        .await
        .unwrap();
    let unit_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id: tenant,
                data_subject_id: context.data_subject_id,
                scope_id: scope,
                agent_node_id: context.agent_node_id,
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("buried codeword".to_string()),
                predicate: None,
                body: "Heliotrope.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:deep".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_episode_id: Some(episode.episode_id),
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
    (store, context, unit_id, episode.episode_id.as_uuid())
}

async fn add_bound_unit(
    store: &InMemoryStore,
    context: &memphant_types::ResolvedMemoryContext,
    source_id: uuid::Uuid,
    kind: MemoryKind,
    state: UnitState,
    trust_level: TrustLevel,
    body: &str,
) {
    let mut tx = store.begin(context).await.unwrap();
    store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind,
                state,
                fact_key: Some("policy projection test".to_string()),
                predicate: None,
                body: body.to_string(),
                confidence: Some(1.0),
                trust_level,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:deep".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_episode_id: Some(memphant_types::EpisodeId::from_u128(source_id.as_u128())),
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
}

#[tokio::test]
async fn explicit_deep_without_provider_is_unavailable() {
    let (store, context, _, _) = seeded_service().await;
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    let error = service
        .recall_internal(request(context, RecallMode::Deep))
        .await
        .unwrap_err();
    assert!(matches!(
        error,
        ServiceError::Core(CoreError::DeepUnavailable)
    ));
}

#[tokio::test]
async fn policy_denial_precedes_missing_provider_disclosure() {
    let (store, mut context, _, _) = seeded_service().await;
    for sources in context.sources_by_kind.values_mut() {
        sources.clear();
    }
    let tenant = context.tenant_id;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );

    let error = service
        .recall_internal(request(context, RecallMode::Deep))
        .await
        .unwrap_err();
    assert!(matches!(
        error,
        ServiceError::Core(CoreError::PolicyDenied(_))
    ));
    assert_eq!(store.retrieval_traces(tenant).len(), 1);
}

#[tokio::test]
async fn fast_never_calls_an_installed_deep_provider() {
    let (store, context, answer_id, source_id) = seeded_service().await;
    let provider = Arc::new(RecordingProvider::completed(vec![source_id]));
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(provider.clone());
    let baseline_service =
        MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    for mode in [RecallMode::Fast] {
        let response = service
            .recall_internal(request(context.clone(), mode))
            .await
            .unwrap();
        let baseline = baseline_service
            .recall_internal(request(context.clone(), mode))
            .await
            .unwrap();
        assert!(response.deep.is_none());
        assert!(!response.candidate_whitelist.contains(&answer_id));
        let mut response_json = serde_json::to_value(&response).unwrap();
        let mut baseline_json = serde_json::to_value(&baseline).unwrap();
        assert!(response_json.get("deep").is_none());
        response_json.as_object_mut().unwrap().remove("trace_id");
        baseline_json.as_object_mut().unwrap().remove("trace_id");
        assert_eq!(response_json, baseline_json);
        let trace = service
            .store()
            .trace_by_id_any_tenant(response.trace_id)
            .unwrap();
        let baseline_trace = baseline_service
            .store()
            .trace_by_id_any_tenant(baseline.trace_id)
            .unwrap();
        let mut trace_json = serde_json::to_value(trace).unwrap();
        let mut baseline_trace_json = serde_json::to_value(baseline_trace).unwrap();
        for field in [
            "deep",
            "l4_provider",
            "l4_model",
            "l4_observed_provider",
            "l4_observed_model",
            "l4_prompt_hash",
            "l4_config_hash",
            "l4_workspace_manifest_sha256",
        ] {
            assert!(trace_json.get(field).is_none(), "{field} must be omitted");
        }
        assert!(trace_json["l4_sandbox_id"].is_null());
        assert_eq!(
            trace_json["l4_gathered_evidence_ids"],
            serde_json::json!([])
        );
        trace_json.as_object_mut().unwrap().remove("id");
        baseline_trace_json.as_object_mut().unwrap().remove("id");
        assert_eq!(trace_json, baseline_trace_json);
    }
    assert_eq!(provider.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn deep_promotes_provider_nominated_bound_unit_with_citation() {
    let (store, context, answer_id, source_id) = seeded_service().await;
    let provider = Arc::new(RecordingProvider::completed(vec![source_id]));
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(provider.clone());

    let response = service
        .recall_internal(request(context, RecallMode::Deep))
        .await
        .unwrap();
    assert_eq!(response.candidate_whitelist, vec![answer_id]);
    assert_eq!(
        response.citations[0].episode_id.unwrap().as_uuid(),
        source_id
    );
    assert_eq!(
        response.deep.as_ref().unwrap().status,
        DeepRecallStatus::Completed
    );
    let trace = store.trace_by_id_any_tenant(response.trace_id).unwrap();
    assert!(trace.candidates.iter().any(|candidate| {
        candidate.unit_id == answer_id && candidate.channel == RecallChannel::Deep
    }));
    assert_eq!(trace.l4_provider.as_deref(), Some("test"));
    assert_eq!(trace.l4_model.as_deref(), Some("test/deep"));
    assert_eq!(trace.l4_observed_provider.as_deref(), Some("test"));
    assert_eq!(trace.l4_observed_model.as_deref(), Some("test/deep"));
    assert_eq!(provider.calls.load(Ordering::SeqCst), 1);
    let response_json = serde_json::to_value(&response).unwrap();
    assert!(response_json.get("deep").is_some());
    let trace_json = serde_json::to_value(trace).unwrap();
    for field in [
        "deep",
        "l4_sandbox_id",
        "l4_provider",
        "l4_model",
        "l4_observed_provider",
        "l4_observed_model",
        "l4_prompt_hash",
        "l4_config_hash",
        "l4_workspace_manifest_sha256",
    ] {
        assert!(trace_json.get(field).is_some(), "{field} must be present");
    }
}

#[tokio::test]
async fn observed_routing_is_distinct_from_configured_identity() {
    let (store, context, _, source_id) = seeded_service().await;
    let provider = RecordingProvider::with_result(DeepRecallProviderResult {
        status: DeepRecallStatus::Completed,
        stop_reason: DeepRecallStopReason::Completed,
        source_ids: vec![source_id],
        usage: DeepRecallUsage::default(),
        generation_ids: Vec::new(),
        observed_provider: Some("routed-provider".to_string()),
        observed_model: Some("routed/model-v2".to_string()),
        evidence: evidence(DeepRecallStatus::Completed),
    });
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(Arc::new(provider));

    let response = service
        .recall_internal(request(context, RecallMode::Deep))
        .await
        .unwrap();
    let trace = store.trace_by_id_any_tenant(response.trace_id).unwrap();
    assert_eq!(trace.l4_provider.as_deref(), Some("test"));
    assert_eq!(trace.l4_model.as_deref(), Some("test/deep"));
    assert_eq!(
        trace.l4_observed_provider.as_deref(),
        Some("routed-provider")
    );
    assert_eq!(trace.l4_observed_model.as_deref(), Some("routed/model-v2"));
}

#[tokio::test]
async fn semantic_support_without_a_verified_citation_receipt_fails_closed() {
    let (store, context, _, source_id) = seeded_service().await;
    let status = EvidenceStatus::Supported;
    let provider = RecordingProvider::with_result(DeepRecallProviderResult {
        status: DeepRecallStatus::Completed,
        stop_reason: DeepRecallStopReason::Completed,
        source_ids: vec![source_id],
        usage: DeepRecallUsage::default(),
        generation_ids: Vec::new(),
        observed_provider: Some("test".to_string()),
        observed_model: Some("test/deep".to_string()),
        evidence: EvidenceDisposition {
            contract_revision: EVIDENCE_DISPOSITION_CONTRACT_REVISION.to_string(),
            status,
            answer_policy: status.answer_policy(),
            reason: "source appears supportive but has no receipt".to_string(),
        },
    });
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(Arc::new(provider));

    assert!(matches!(
        service
            .recall_internal(request(context, RecallMode::Deep))
            .await,
        Err(ServiceError::Core(CoreError::DeepProviderInvalidOutput))
    ));
    assert!(
        store
            .retrieval_traces(TenantId::from_u128(81_000))
            .is_empty()
    );
}

#[tokio::test]
async fn top_level_deep_latency_is_measured_not_provider_reported() {
    let (store, context, _, _) = seeded_service().await;
    let provider = RecordingProvider::completed(Vec::new()).with_delay(5);
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(Arc::new(provider));
    let mut recall = request(context, RecallMode::Deep);
    recall.query = "quuxxyzy".to_string();

    let response = service.recall_internal(recall).await.unwrap();
    let trace = store.trace_by_id_any_tenant(response.trace_id).unwrap();
    assert_eq!(trace.deep.as_ref().unwrap().usage.wall_time_ms, 0);
    assert!(
        trace.latency_ms >= 5,
        "measured latency: {}",
        trace.latency_ms
    );
}

#[tokio::test]
async fn every_cap_returns_a_machine_readable_partial_result() {
    for (stop_reason, usage) in [
        (
            DeepRecallStopReason::WallTime,
            DeepRecallUsage {
                wall_time_ms: limits().wall_time_ms,
                ..DeepRecallUsage::default()
            },
        ),
        (
            DeepRecallStopReason::ToolIterations,
            DeepRecallUsage {
                tool_iterations: limits().max_tool_iterations,
                ..DeepRecallUsage::default()
            },
        ),
        (
            DeepRecallStopReason::ContextTokens,
            DeepRecallUsage {
                context_tokens: limits().max_context_tokens,
                ..DeepRecallUsage::default()
            },
        ),
        (
            DeepRecallStopReason::Spend,
            DeepRecallUsage {
                spend_micros: limits().max_spend_micros,
                ..DeepRecallUsage::default()
            },
        ),
    ] {
        let (store, context, answer_id, source_id) = seeded_service().await;
        let provider = Arc::new(RecordingProvider::with_result(DeepRecallProviderResult {
            status: DeepRecallStatus::Capped,
            stop_reason,
            source_ids: vec![source_id],
            usage,
            generation_ids: Vec::new(),
            observed_provider: Some("test-route".to_string()),
            observed_model: Some("test/deep".to_string()),
            evidence: evidence(DeepRecallStatus::Capped),
        }));
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        )
        .with_deep_recall_provider(provider);

        let response = service
            .recall_internal(request(context, RecallMode::Deep))
            .await
            .unwrap();
        assert_eq!(response.candidate_whitelist, vec![answer_id]);
        let summary = response.deep.unwrap();
        assert_eq!(summary.status, DeepRecallStatus::Capped);
        assert_eq!(summary.stop_reason, stop_reason);
        assert_eq!(summary.usage, usage);
        let trace = store.trace_by_id_any_tenant(response.trace_id).unwrap();
        assert_eq!(trace.deep.as_ref().unwrap(), &summary);
        assert!(
            trace
                .channel_runs
                .iter()
                .find(|stage| stage.stage == "l4_exhaustive")
                .unwrap()
                .detail
                .starts_with("capped_")
        );
        assert_eq!(trace.cost_micros, usage.spend_micros);
    }
}

#[tokio::test]
async fn paid_invalid_output_returns_checkpoint_and_truthful_outstanding_usage() {
    let (store, context, answer_id, source_id) = seeded_service().await;
    let usage = DeepRecallUsage {
        context_tokens: 100,
        spend_micros: 200,
        unsettled_context_tokens_upper_bound: 300,
        unsettled_spend_micros_upper_bound: 400,
        ..DeepRecallUsage::default()
    };
    let provider = Arc::new(RecordingProvider::with_result(DeepRecallProviderResult {
        status: DeepRecallStatus::Partial,
        stop_reason: DeepRecallStopReason::InvalidOutput,
        source_ids: vec![source_id],
        usage,
        generation_ids: vec!["gen-1".to_string(), "gen-2".to_string()],
        observed_provider: Some("Azure".to_string()),
        observed_model: Some("test/deep-routed".to_string()),
        evidence: evidence(DeepRecallStatus::Partial),
    }));
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(provider);

    let response = service
        .recall_internal(request(context, RecallMode::Deep))
        .await
        .unwrap();
    assert_eq!(response.candidate_whitelist, vec![answer_id]);
    let summary = response.deep.unwrap();
    assert_eq!(summary.status, DeepRecallStatus::Partial);
    assert_eq!(summary.stop_reason, DeepRecallStopReason::InvalidOutput);
    assert_eq!(summary.generation_ids, vec!["gen-1", "gen-2"]);
    assert_eq!(summary.usage, usage);
    let trace = store.trace_by_id_any_tenant(response.trace_id).unwrap();
    assert_eq!(trace.cost_micros, 200);
    assert_eq!(trace.deep.as_ref().unwrap(), &summary);
    assert_eq!(
        trace
            .channel_runs
            .iter()
            .find(|stage| stage.stage == "l4_exhaustive")
            .unwrap()
            .detail,
        "partial_invalid_output"
    );
}

#[tokio::test]
async fn provider_error_trace_stage_is_partial_not_capped() {
    let (store, context, _, _) = seeded_service().await;
    let provider = Arc::new(RecordingProvider::with_result(DeepRecallProviderResult {
        status: DeepRecallStatus::Partial,
        stop_reason: DeepRecallStopReason::ProviderError,
        source_ids: Vec::new(),
        usage: DeepRecallUsage::default(),
        generation_ids: vec!["gen-provider-error".to_string()],
        observed_provider: None,
        observed_model: None,
        evidence: evidence(DeepRecallStatus::Partial),
    }));
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(provider);

    let response = service
        .recall_internal(request(context, RecallMode::Deep))
        .await
        .unwrap();
    let trace = store.trace_by_id_any_tenant(response.trace_id).unwrap();
    assert_eq!(
        trace
            .channel_runs
            .iter()
            .find(|stage| stage.stage == "l4_exhaustive")
            .unwrap()
            .detail,
        "partial_provider_error"
    );
}

#[tokio::test]
async fn settled_plus_outstanding_caps_use_checked_arithmetic() {
    for usage in [
        DeepRecallUsage {
            context_tokens: limits().max_context_tokens,
            unsettled_context_tokens_upper_bound: 1,
            ..DeepRecallUsage::default()
        },
        DeepRecallUsage {
            spend_micros: limits().max_spend_micros,
            unsettled_spend_micros_upper_bound: 1,
            ..DeepRecallUsage::default()
        },
        DeepRecallUsage {
            context_tokens: u64::MAX,
            unsettled_context_tokens_upper_bound: 1,
            ..DeepRecallUsage::default()
        },
    ] {
        let (store, context, _, _) = seeded_service().await;
        let provider = Arc::new(RecordingProvider::with_result(DeepRecallProviderResult {
            status: DeepRecallStatus::Partial,
            stop_reason: DeepRecallStopReason::ProviderError,
            source_ids: Vec::new(),
            usage,
            generation_ids: vec!["gen-paid".to_string()],
            observed_provider: Some("Azure".to_string()),
            observed_model: Some("test/deep".to_string()),
            evidence: evidence(DeepRecallStatus::Partial),
        }));
        let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
            .with_deep_recall_provider(provider);
        assert!(matches!(
            service
                .recall_internal(request(context, RecallMode::Deep))
                .await,
            Err(ServiceError::Core(CoreError::DeepProviderInvalidOutput))
        ));
    }
}

#[tokio::test]
async fn completed_zero_evidence_returns_an_ordinary_abstention() {
    let (store, context, _, _) = seeded_service().await;
    let provider = Arc::new(RecordingProvider::completed(Vec::new()));
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_deep_recall_provider(provider);
    let mut request = request(context, RecallMode::Deep);
    request.query = "nothing in this corpus resembles quuxxyzy".to_string();

    let response = service.recall_internal(request).await.unwrap();
    assert!(response.abstention);
    assert!(response.items.is_empty());
    assert_eq!(response.deep.unwrap().status, DeepRecallStatus::Completed);
}

#[tokio::test]
async fn capped_zero_evidence_returns_an_ordinary_abstention() {
    let (store, context, _, _) = seeded_service().await;
    let provider = Arc::new(RecordingProvider::with_result(DeepRecallProviderResult {
        status: DeepRecallStatus::Capped,
        stop_reason: DeepRecallStopReason::ToolIterations,
        source_ids: Vec::new(),
        usage: DeepRecallUsage {
            tool_iterations: limits().max_tool_iterations,
            ..DeepRecallUsage::default()
        },
        generation_ids: Vec::new(),
        observed_provider: Some("test".to_string()),
        observed_model: Some("test/deep".to_string()),
        evidence: evidence(DeepRecallStatus::Capped),
    }));
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_deep_recall_provider(provider);
    let mut request = request(context, RecallMode::Deep);
    request.query = "nothing in this corpus resembles quuxxyzy".to_string();

    let response = service.recall_internal(request).await.unwrap();
    assert!(response.abstention);
    assert!(response.items.is_empty());
    assert_eq!(response.deep.unwrap().status, DeepRecallStatus::Capped);
}

#[tokio::test]
async fn deep_zero_evidence_does_not_bypass_provider_with_raw_pending_fallback() {
    let (store, context, _, source_id) = seeded_service().await;
    let mut tx = store.begin(&context).await.unwrap();
    store
        .enqueue_reflect(
            &mut tx,
            ReflectJob {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                episode_id: Some(memphant_types::EpisodeId::from_u128(source_id.as_u128())),
                resource_id: None,
                kind: ReflectJobKind::ReflectEpisode,
                compiler_version: memphant_types::COMPILER_VERSION.to_string(),
                subject: None,
                predicate: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_deep_recall_provider(Arc::new(RecordingProvider::completed(Vec::new())));
    let mut request = request(context, RecallMode::Deep);
    request.query = "Stargate deploy".to_string();

    let response = service.recall_internal(request).await.unwrap();
    assert!(response.abstention);
    assert!(response.items.is_empty());
    assert!(!response.degraded);
}

#[tokio::test]
async fn invalid_provider_results_fail_closed_without_a_success_trace() {
    let invalid_results = [
        DeepRecallProviderResult {
            status: DeepRecallStatus::Completed,
            stop_reason: DeepRecallStopReason::Completed,
            source_ids: vec![uuid::Uuid::from_u128(404)],
            usage: DeepRecallUsage::default(),
            generation_ids: Vec::new(),
            observed_provider: Some("test".to_string()),
            observed_model: Some("test/deep".to_string()),
            evidence: evidence(DeepRecallStatus::Completed),
        },
        DeepRecallProviderResult {
            status: DeepRecallStatus::Completed,
            stop_reason: DeepRecallStopReason::WallTime,
            source_ids: Vec::new(),
            usage: DeepRecallUsage::default(),
            generation_ids: Vec::new(),
            observed_provider: Some("test".to_string()),
            observed_model: Some("test/deep".to_string()),
            evidence: evidence(DeepRecallStatus::Completed),
        },
        DeepRecallProviderResult {
            status: DeepRecallStatus::Completed,
            stop_reason: DeepRecallStopReason::Completed,
            source_ids: Vec::new(),
            usage: DeepRecallUsage {
                spend_micros: limits().max_spend_micros + 1,
                ..DeepRecallUsage::default()
            },
            generation_ids: Vec::new(),
            observed_provider: Some("test".to_string()),
            observed_model: Some("test/deep".to_string()),
            evidence: evidence(DeepRecallStatus::Completed),
        },
        DeepRecallProviderResult {
            status: DeepRecallStatus::Completed,
            stop_reason: DeepRecallStopReason::Completed,
            source_ids: Vec::new(),
            usage: DeepRecallUsage::default(),
            generation_ids: Vec::new(),
            observed_provider: Some("test".to_string()),
            observed_model: Some(" ".to_string()),
            evidence: evidence(DeepRecallStatus::Completed),
        },
        DeepRecallProviderResult {
            status: DeepRecallStatus::Partial,
            stop_reason: DeepRecallStopReason::ProviderError,
            source_ids: Vec::new(),
            usage: DeepRecallUsage::default(),
            generation_ids: vec!["generation".to_string()],
            observed_provider: Some("test".to_string()),
            observed_model: None,
            evidence: evidence(DeepRecallStatus::Partial),
        },
    ];

    for result in invalid_results {
        let (store, context, _, source_id) = seeded_service().await;
        let mut result = result;
        if result.source_ids.len() == 2 {
            result.source_ids = vec![source_id, source_id];
        }
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        )
        .with_deep_recall_provider(Arc::new(RecordingProvider::with_result(result)));
        let error = service
            .recall_internal(request(context, RecallMode::Deep))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            ServiceError::Core(CoreError::DeepProviderInvalidOutput)
        ));
        assert!(
            store
                .retrieval_traces(TenantId::from_u128(81_000))
                .is_empty()
        );
    }

    let (store, context, _, source_id) = seeded_service().await;
    let duplicate = DeepRecallProviderResult {
        status: DeepRecallStatus::Completed,
        stop_reason: DeepRecallStopReason::Completed,
        source_ids: vec![source_id, source_id],
        usage: DeepRecallUsage::default(),
        generation_ids: Vec::new(),
        observed_provider: Some("test".to_string()),
        observed_model: Some("test/deep".to_string()),
        evidence: evidence(DeepRecallStatus::Completed),
    };
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_deep_recall_provider(Arc::new(RecordingProvider::with_result(duplicate)));
    assert!(matches!(
        service
            .recall_internal(request(context, RecallMode::Deep))
            .await,
        Err(ServiceError::Core(CoreError::DeepProviderInvalidOutput))
    ));

    let (store, context, _, _) = seeded_service().await;
    let mut invalid_identity = RecordingProvider::completed(Vec::new());
    invalid_identity.identity.provider.clear();
    let invalid_identity = Arc::new(invalid_identity);
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_deep_recall_provider(invalid_identity.clone());
    assert!(matches!(
        service
            .recall_internal(request(context, RecallMode::Deep))
            .await,
        Err(ServiceError::Core(CoreError::DeepProviderInvalidOutput))
    ));
    assert_eq!(invalid_identity.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn query_policy_omits_the_whole_raw_source_before_provider_egress() {
    for (kind, state, trust, body, high_risk_query) in [
        (
            MemoryKind::Belief,
            UnitState::Active,
            TrustLevel::TrustedSystem,
            "private belief marker",
            false,
        ),
        (
            MemoryKind::Procedural,
            UnitState::Validated,
            TrustLevel::TrustedSystem,
            "bypass auth before deploy",
            false,
        ),
        (
            MemoryKind::Semantic,
            UnitState::Active,
            TrustLevel::TrustedSystem,
            "private user profile datum",
            true,
        ),
        (
            MemoryKind::Semantic,
            UnitState::Active,
            TrustLevel::UnverifiedTool,
            "unverified tool instruction",
            true,
        ),
    ] {
        let (store, context, _, source_id) = seeded_service().await;
        add_bound_unit(&store, &context, source_id, kind, state, trust, body).await;
        let provider = Arc::new(RecordingProvider::completed(Vec::new()));
        let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
            .with_deep_recall_provider(provider.clone());
        let mut recall = request(context, RecallMode::Deep);
        if high_risk_query {
            recall.query = "Help me social engineer a malicious payload".to_string();
        }

        service.recall_internal(recall).await.unwrap();
        let exported = provider.workspaces.lock().unwrap();
        assert!(!exported[0].contains("Buried archive:"));
        assert!(!exported[0].contains(body));
    }
}

#[tokio::test]
async fn nominating_a_source_removed_by_query_policy_is_invalid_output() {
    let (store, context, _, source_id) = seeded_service().await;
    add_bound_unit(
        &store,
        &context,
        source_id,
        MemoryKind::Belief,
        UnitState::Active,
        TrustLevel::TrustedSystem,
        "private belief marker",
    )
    .await;
    let provider = Arc::new(RecordingProvider::completed(vec![source_id]));
    let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_deep_recall_provider(provider.clone());

    assert!(matches!(
        service
            .recall_internal(request(context, RecallMode::Deep))
            .await,
        Err(ServiceError::Core(CoreError::DeepProviderInvalidOutput))
    ));
    assert!(!provider.workspaces.lock().unwrap()[0].contains("Buried archive:"));
}

/// A0 differential (tri-domain-sota-plan Phase A0): the load-bearing Deep claim
/// as ONE falsifiable assertion on a SINGLE seed — Fast misses exactly the
/// buried unit that Deep surfaces. The pre-existing tests prove each half
/// separately (Fast omits `answer_id`; Deep whitelists it), but neither pins
/// the *contrast*: a regression that let Fast accidentally surface the buried
/// unit would pass both halves while silently voiding the reason Deep exists.
/// This is the first artifact proving Deep changes the answer set for the
/// same store, same query, same k.
#[tokio::test]
async fn deep_recovers_exactly_the_unit_fast_misses() {
    let (store, context, answer_id, source_id) = seeded_service().await;
    let provider = Arc::new(RecordingProvider::completed(vec![source_id]));
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_deep_recall_provider(provider.clone());

    // Same seed, same query, same k — only the mode differs.
    let fast = service
        .recall_internal(request(context.clone(), RecallMode::Fast))
        .await
        .unwrap();
    let deep = service
        .recall_internal(request(context, RecallMode::Deep))
        .await
        .unwrap();

    // The differential claim, stated as one contrast:
    assert!(
        !fast.candidate_whitelist.contains(&answer_id),
        "Fast must miss the buried unit; if it surfaces it, Deep has no reason to exist"
    );
    assert!(
        deep.candidate_whitelist.contains(&answer_id),
        "Deep must recover the buried unit Fast missed"
    );
    // The recovered unit is cited through the Deep channel with real provenance,
    // not smuggled in via the ordinary pool.
    assert!(
        deep.deep.is_some(),
        "Deep response must carry a deep envelope"
    );
    assert!(
        fast.deep.is_none(),
        "Fast response must not carry a deep envelope"
    );
    let deep_trace = store.trace_by_id_any_tenant(deep.trace_id).unwrap();
    assert!(
        deep_trace
            .candidates
            .iter()
            .any(|c| { c.unit_id == answer_id && c.channel == RecallChannel::Deep }),
        "the recovered unit must be attributed to the Deep channel, not the fast pool"
    );
    assert_eq!(
        provider.calls.load(Ordering::SeqCst),
        1,
        "Deep called the provider exactly once"
    );
}
