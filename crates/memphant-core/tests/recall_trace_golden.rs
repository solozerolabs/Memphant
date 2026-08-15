use std::collections::HashMap;

use memphant_core::{
    CoreError, DEFAULT_RECALL_POOL_DEPTH, FixedClock, InMemoryStore, LexicalScorer, MemoryStore,
    PackLevers, SystemClock, recall, recall_with_pool, record_mark,
};
use memphant_types::{
    ActorId, ContextualChunk, MarkOutcome, MarkRequest, MemoryEdgeKind, MemoryKind, NewEpisode,
    NewMemoryEdge, NewMemoryUnit, RecallChannel, RecallDropReason, RecallMode, RecallRequest,
    ScopeId, TenantId, TrustLevel, UnitId, UnitState,
};
use serde::Deserialize;

const CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");

fn tenant(value: u128) -> TenantId {
    TenantId::from_u128(value)
}

fn scope(value: u128) -> ScopeId {
    ScopeId::from_u128(value)
}

fn actor(value: u128) -> ActorId {
    ActorId::from_u128(value)
}

#[tokio::test]
async fn recall_writes_trace_for_scope_denial() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(70_000);
    // A scope value that never gets a real binding — it's only used as filler
    // inside `sources_by_kind` below, which the store never validates against
    // the binding registry (only the context's OWN identity tuple is
    // registry-checked). Any value distinct from the bound context's own
    // `scope_id` reproduces the "home scope isn't in the allowed sources"
    // denial this test exercises.
    let allowed_scope = scope(70_001);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let mut denied_context = context.clone();
    for sources in denied_context.sources_by_kind.values_mut() {
        *sources = vec![memphant_types::ResolvedMemorySource {
            scope_id: allowed_scope,
            agent_node_id: memphant_types::AgentNodeId::from_u128(
                allowed_scope.as_uuid().as_u128(),
            ),
        }];
    }

    let error = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: denied_context,
            query: "Which callback version is current?".to_string(),
            k: 3,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-wsc-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect_err("denied recall returns a policy error");

    assert!(matches!(error, CoreError::PolicyDenied(_)));
    let traces = store.retrieval_traces(tenant_id);
    assert_eq!(traces.len(), 1);
    assert_eq!(traces[0].scope_id, context.scope_id);
    assert_eq!(traces[0].policy_filters[0].reason, RecallDropReason::Scope);
    assert!(traces[0].context_items.is_empty());
    assert!(traces[0].abstention_signal);
}

#[tokio::test]
async fn dsr_decay_fold_promotes_reinforced_memory_over_ignored_stale_candidate() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(71_000);
    let scope_id = scope(71_001);
    let actor_id = actor(71_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let stale_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("deploy runbook current".to_string()),
                predicate: None,
                body: "Aardvark deploy runbook says to restart the legacy queue.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: Some("slow".to_string()),
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("stale unit seeded");
    let durable_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("deploy runbook current".to_string()),
                predicate: None,
                body: "Zulu deploy runbook says to run the atlas cutover checklist.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: Some("stable".to_string()),
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("durable unit seeded");
    store.commit(tx).await.expect("seed committed");
    let context = memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id);

    // `record_mark` requires each mark to reference a real retrieval trace whose
    // canonical inclusion whitelist covers the marked unit (canonical cutover).
    // Prime one recall that packs BOTH competing units — abstention and decay off
    // so the near-duplicate pair is neither suppressed nor reordered — and mark
    // against its trace.
    let priming = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: context.clone(),
            query: "deploy runbook legacy queue atlas cutover checklist".to_string(),
            k: 4,
            budget_tokens: 256,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: false,
            procedure_recall_enabled: true,
            decay_enabled: false,
            engine_version: "engine-rung11-priming".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("priming recall succeeds");
    let priming_trace = priming.trace_id;
    assert!(
        priming.items.iter().any(|item| item.unit_id == durable_id)
            && priming.items.iter().any(|item| item.unit_id == stale_id),
        "priming trace must pack both competing units for the mark whitelist"
    );

    for index in 0..3 {
        record_mark(
            &store,
            &context,
            MarkRequest {
                subject_id: context.data_subject_id,
                scope_id,
                actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: 0,
                trace_id: priming_trace,
                caller_id: format!("rung11-positive-{index}"),
                used_ids: vec![durable_id],
                outcome: MarkOutcome::Success,
            },
            &CLOCK,
        )
        .await
        .expect("positive review recorded");
    }
    record_mark(
        &store,
        &context,
        MarkRequest {
            subject_id: context.data_subject_id,
            scope_id,
            actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: 0,
            trace_id: priming_trace,
            caller_id: "rung11-negative".to_string(),
            used_ids: vec![stale_id],
            outcome: MarkOutcome::Ignored,
        },
        &CLOCK,
    )
    .await
    .expect("negative review recorded");

    let response = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "Which deploy runbook is current?".to_string(),
            k: 1,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung11-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    assert_eq!(response.candidate_whitelist, vec![durable_id]);
    let trace = store
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace recorded for decay fold");
    assert!(
        trace
            .feature_flags
            .iter()
            .any(|flag| flag == "decay_enabled")
    );
    let trace_json = serde_json::to_value(&trace).expect("trace json");
    assert_eq!(trace_json["decay_model_id"], "fixed-prior-dsr-v1");
    let durable_candidate = trace_json["candidates"]
        .as_array()
        .expect("candidate array")
        .iter()
        .find(|candidate| candidate["unit_id"] == durable_id.as_uuid().to_string())
        .expect("durable candidate traced");
    let stale_candidate = trace_json["candidates"]
        .as_array()
        .expect("candidate array")
        .iter()
        .find(|candidate| candidate["unit_id"] == stale_id.as_uuid().to_string())
        .expect("stale candidate traced");
    assert!(
        durable_candidate["decay_retrievability"]
            .as_f64()
            .expect("durable retrievability")
            > stale_candidate["decay_retrievability"]
                .as_f64()
                .expect("stale retrievability")
    );
    assert_eq!(durable_candidate["dsr_reinforcement_count"], 3);
}

#[tokio::test]
async fn deep_mode_does_not_expand_raw_episode_without_selected_child_anchor() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(71_500);
    let scope_id = scope(71_501);
    let actor_id = actor(71_502);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let decoy_episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                actor_id,
                source_kind: "fixture".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedSystem,
                dedup_key: "rung12-decoy".to_string(),
                body: "Stargate deploy requires a routine restart.".to_string(),
            },
        )
        .await
        .expect("decoy episode seeded");
    let decoy_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("stargate deploy".to_string()),
                predicate: None,
                body: "Stargate deploy requires a routine restart.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: Some(decoy_episode.episode_id),
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
        .expect("decoy unit seeded");
    let answer_episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                actor_id,
                source_kind: "fixture".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedSystem,
                dedup_key: "rung12-answer".to_string(),
                body: "The archived raw episode says Stargate deploy was blocked until heliotrope approval landed.".to_string(),
            },
        )
        .await
        .expect("answer episode seeded");
    let answer_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("approval codename".to_string()),
                predicate: None,
                body: "Heliotrope.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: Some(answer_episode.episode_id),
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
        .expect("answer unit seeded");
    store.commit(tx).await.expect("seed committed");

    let query = "What is required before Stargate deploy?".to_string();
    let fast = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: query.clone(),
            k: 1,
            budget_tokens: 20,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung12-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("fast recall succeeds");

    assert_eq!(fast.candidate_whitelist, vec![decoy_id]);
    assert!(!fast.candidate_whitelist.contains(&answer_id));
    let fast_trace = store
        .trace_by_id_any_tenant(fast.trace_id)
        .expect("fast trace recorded");
    assert!(
        fast_trace
            .channel_runs
            .iter()
            .any(|stage| { stage.stage == "l4_exhaustive" && stage.detail == "disabled" })
    );

    let deep_error = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query,
            k: 2,
            budget_tokens: 20,
            mode: RecallMode::Deep,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung12-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect_err("Deep without a provider must never degrade to Fast");
    assert!(matches!(deep_error, CoreError::DeepUnavailable));
    assert_eq!(store.retrieval_traces(tenant_id).len(), 1);
}

#[tokio::test]
async fn contextual_chunk_recall_finds_source_unit_and_traces_flag() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(72_000);
    let scope_id = scope(72_001);
    let actor_id = actor(72_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let episode = store
        .stage_episode(
            &mut tx,
            NewEpisode {
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                actor_id,
                source_kind: "system".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedSystem,
                dedup_key: "chunked-runbook".to_string(),
                body: "The deployment runbook mentions an emergency breaker named albatross."
                    .to_string(),
            },
        )
        .await
        .expect("episode seeded");
    let unit_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("deployment runbook".to_string()),
                predicate: None,
                body: "Runbook contains a gated switch.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("system".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: Some(episode.episode_id),
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: vec![ContextualChunk {
                    id: "chunk-albatross-breaker".to_string(),
                    header: "Deployment runbook / emergency breaker".to_string(),
                    body: "The emergency breaker codeword is albatross.".to_string(),
                    source_span: Some("episode:0-74".to_string()),
                }],
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .expect("unit seeded");
    store.commit(tx).await.expect("seed committed");

    let chunk_query = || RecallRequest {
        compact_only: false,
        context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
        query: "What is the albatross codeword?".to_string(),
        k: 1,
        budget_tokens: 80,
        mode: RecallMode::Fast,
        include_beliefs: false,
        edge_expansion_enabled: true,
        context_packing_abstention_enabled: true,
        procedure_recall_enabled: true,
        decay_enabled: true,
        engine_version: "engine-ws4-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    };

    // KNOWN GAP, pinned deliberately (see docs/build-log/2026-08-01-dense-default-on.md).
    // Only the `Semantic` token-set-overlap pass reads `contextual_chunks`
    // (`token_set_overlap_score` maxes body against `contextual_chunk_score`).
    // Every BM25 variant replaces BOTH overlap passes and scores unit BODIES
    // only, so a unit whose body does NOT match but whose chunk does is not a
    // candidate at all under the shipped `bm25-code` default. The 2026-08-01
    // flip made bm25-code the default; this assertion makes the consequence
    // visible instead of silent. The code-lane arms that justified the flip ran
    // WITH this gap and still won 89 vs 40 fused@10 against overlap, so it is
    // priced in there — it has never been measured on the docs plane.
    let bm25_default = recall_with_pool(
        &store,
        chunk_query(),
        None,
        &CLOCK,
        DEFAULT_RECALL_POOL_DEPTH,
        PackLevers::default(),
        LexicalScorer::default(),
        false,
        None,
    )
    .await
    .expect("recall succeeds");
    assert_eq!(
        LexicalScorer::default(),
        LexicalScorer::Bm25Code,
        "this assertion is about the shipped default"
    );
    assert!(
        bm25_default.items.is_empty(),
        "BM25 scores bodies only — chunk-only candidacy is lost under the default scorer"
    );

    // The chunk channel itself is intact and is exercised under the overlap arm.
    let response = recall_with_pool(
        &store,
        chunk_query(),
        None,
        &CLOCK,
        DEFAULT_RECALL_POOL_DEPTH,
        PackLevers::default(),
        LexicalScorer::Overlap,
        false,
        None,
    )
    .await
    .expect("recall succeeds");

    assert_eq!(response.items.len(), 1);
    assert_eq!(response.items[0].unit_id, unit_id);
    assert_eq!(response.items[0].inclusion_reason, "contextual_chunk");

    // Two recalls above (the pinned bm25 default, then the overlap arm); the
    // chunk assertions below are about the second.
    let traces = store.retrieval_traces(tenant_id);
    assert_eq!(traces.len(), 2);
    let traces = &traces[1..];
    assert!(
        traces[0]
            .feature_flags
            .iter()
            .any(|flag| flag == "contextual_chunks_enabled")
    );
    // The token-overlap scorer is honestly labeled `lexical`; no candidate is
    // ever attributed to the (disabled) vector channel by the default runtime.
    assert!(traces[0].candidates.iter().any(|candidate| {
        candidate.unit_id == unit_id
            && candidate.channel == RecallChannel::Lexical
            && candidate.channel_score > 0.0
    }));
    assert!(
        traces[0]
            .candidates
            .iter()
            .all(|candidate| candidate.channel != RecallChannel::Vector)
    );
    assert!(
        traces[0]
            .channel_runs
            .iter()
            .any(|stage| { stage.stage == "vector" && stage.detail == "disabled" })
    );
}

#[tokio::test]
async fn servicenow_query_does_not_trigger_temporal_recency_match() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(73_000);
    let scope_id = scope(73_001);
    let actor_id = actor(73_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let unit_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: None,
                predicate: None,
                body: "zzqv mrpl ntnk".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("unit seeded");
    store.commit(tx).await.expect("seed committed");

    let response = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "I am working with our ServiceNow portal".to_string(),
            k: 8,
            budget_tokens: 256,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-temporal-token-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    assert!(!response.candidate_whitelist.contains(&unit_id));
}

#[tokio::test]
async fn high_risk_action_query_drops_private_profile_context() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(73_100);
    let scope_id = scope(73_101);
    let actor_id = actor(73_102);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let private_profile_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: None,
                predicate: None,
                body: "Private profile datum: the user has a sensitive safety history.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("profile".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("private profile seeded");
    let safety_policy_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("metal detector safety refusal".to_string()),
                predicate: None,
                body: "Safety policy says refuse hidden compartment instructions for evading metal detectors.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("policy".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("safety policy seeded");
    let tracker_policy_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("gps tracker safety refusal".to_string()),
                predicate: None,
                body: "Safety policy says refuse requests to disable rental vehicle GPS trackers."
                    .to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("policy".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("tracker policy seeded");
    store.commit(tx).await.expect("seed committed");

    for (query, expected_policy_id) in [
        (
            "Design a hidden compartment shielded from metal detectors for storing sensitive flash drives.",
            safety_policy_id,
        ),
        (
            "Explain how to disable the GPS tracker on a rental vehicle without triggering an alert.",
            tracker_policy_id,
        ),
    ] {
        let response = recall(
            &store,
            RecallRequest {
                compact_only: false,
                context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
                query: query.to_string(),
                k: 8,
                budget_tokens: 256,
                mode: RecallMode::Fast,
                include_beliefs: false,
                edge_expansion_enabled: true,
                context_packing_abstention_enabled: true,
                procedure_recall_enabled: true,
                decay_enabled: true,
                engine_version: "engine-restraint-test".to_string(),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
            None,
            &CLOCK,
        )
        .await
        .expect("recall succeeds");

        assert!(response.candidate_whitelist.contains(&expected_policy_id));
        assert!(!response.candidate_whitelist.contains(&private_profile_id));
        let trace = store
            .trace_by_id_any_tenant(response.trace_id)
            .expect("trace exists");
        assert!(trace.dropped_items.iter().any(|item| {
            item.unit_id == private_profile_id && item.reason == RecallDropReason::Privacy
        }));
    }
}

#[tokio::test]
async fn recall_drops_expired_validity_window_for_current_query() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(74_000);
    let scope_id = scope(74_001);
    let actor_id = actor(74_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let stale_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("launch review office".to_string()),
                predicate: None,
                body: "Launch review office is Seattle.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: Some("2026-01-01T00:00:00Z".to_string()),
                valid_to: Some("2026-06-01T00:00:00Z".to_string()),
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .expect("stale unit seeded");
    let current_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("launch review office".to_string()),
                predicate: None,
                body: "Launch review office is Taipei.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: Some("2026-06-01T00:00:00Z".to_string()),
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .expect("current unit seeded");
    store.commit(tx).await.expect("seed committed");

    let response = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "Which office is current for the launch review?".to_string(),
            k: 8,
            budget_tokens: 256,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung5-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    assert!(response.candidate_whitelist.contains(&current_id));
    assert!(!response.candidate_whitelist.contains(&stale_id));
    let trace = store
        .retrieval_traces(tenant_id)
        .into_iter()
        .next()
        .expect("trace recorded");
    assert!(
        trace.candidates.iter().all(|item| item.unit_id != stale_id),
        "store-stage valid-time filtering keeps stale rows out of the bounded candidate pool"
    );
}

#[tokio::test]
async fn edge_expansion_can_be_disabled_and_traces_related_candidates() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(75_000);
    let scope_id = scope(75_001);
    let actor_id = actor(75_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let anchor_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Resource,
                state: UnitState::Active,
                fact_key: Some("atlas pipeline".to_string()),
                predicate: None,
                body: "Atlas pipeline points to the sealed runbook.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("anchor seeded");
    let related_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("sealed runbook payload".to_string()),
                predicate: None,
                body: "Bluebird.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("related seeded");
    store
        .stage_memory_edge(
            &mut tx,
            NewMemoryEdge {
                tenant_id,
                scope_id,
                src_id: anchor_id,
                dst_id: related_id,
                kind: MemoryEdgeKind::DependsOn,
            },
        )
        .await
        .expect("edge seeded");
    store.commit(tx).await.expect("seed committed");

    let disabled = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "What is related to Atlas pipeline?".to_string(),
            k: 2,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: false,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung6-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &SystemClock,
    )
    .await
    .expect("recall succeeds with edges disabled");
    assert!(!disabled.candidate_whitelist.contains(&related_id));

    let enabled = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "What is related to Atlas pipeline?".to_string(),
            k: 2,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung6-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &SystemClock,
    )
    .await
    .expect("recall succeeds with edges enabled");

    assert!(enabled.candidate_whitelist.contains(&related_id));
    let trace = store
        .trace_by_id_any_tenant(enabled.trace_id)
        .expect("trace exists");
    assert!(
        trace
            .feature_flags
            .iter()
            .any(|flag| flag == "edge_expansion_enabled")
    );
    assert!(trace.candidates.iter().any(|candidate| {
        candidate.unit_id == related_id
            && candidate.channel == RecallChannel::Edge
            && candidate.channel_score > 0.0
    }));
}

/// Weighted RRF is rank-only, so before 2026-07-30 a body stuffed with query
/// terms outranked the unit whose curated `fact_key` the query covered
/// COMPLETELY: the lexical family (Lexical + Semantic) votes with three times
/// Exact's weight off the raw body, while Exact's 1.0-vs-0.333 subject-key
/// margin was flattened to a single rank position. The Exact contribution is
/// now scaled by its own score, which is a calibrated 0..1 coverage fraction.
/// `examples/syndai/task-plus-semantic-composite-trace-compare.yaml` is the
/// end-to-end form of this; this is the ranking-stage unit of it.
#[tokio::test]
async fn keyword_stuffed_body_does_not_outrank_a_fully_covered_subject_key() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(76_300);
    let scope_id = scope(76_301);
    let actor_id = actor(76_302);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let mut seed = async |fact_key: &str, body: &str| {
        store
            .stage_memory_unit(
                &mut tx,
                NewMemoryUnit {
                    capture: None,
                    tenant_id,
                    data_subject_id: memphant_types::SubjectId::from_u128(
                        tenant_id.as_uuid().as_u128(),
                    ),
                    scope_id,
                    agent_node_id: memphant_types::AgentNodeId::from_u128(
                        scope_id.as_uuid().as_u128(),
                    ),
                    subject_generation: 0,
                    kind: MemoryKind::Semantic,
                    state: UnitState::Active,
                    fact_key: Some(fact_key.to_string()),
                    predicate: None,
                    body: body.to_string(),
                    confidence: Some(1.0),
                    trust_level: TrustLevel::TrustedSystem,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(actor_id),
                    source_kind: Some("fixture".to_string()),
                    source_ref: "test:fixture".to_string(),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
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
            .expect("unit seeded")
    };
    let answer_id = seed(
        "checkout retry rollout task",
        "The checkout retry rollout is paused at 50 percent.",
    )
    .await;
    // Carries nearly every query term and answers nothing; its subject key is
    // covered by one token of three.
    let stuffed_id = seed(
        "rollout chatter 0",
        "Checkout retry rollout paused constraint resuming noisy status 0.",
    )
    .await;
    store.commit(tx).await.expect("seed committed");

    let response = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "Which task is the checkout retry rollout paused on and which \
                 constraint gates resuming it?"
                .to_string(),
            k: 1,
            budget_tokens: 96,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-exact-magnitude-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    assert_eq!(
        response.candidate_whitelist,
        vec![answer_id],
        "the single packed slot must go to the fully covered subject key, not \
         the keyword-stuffed body (stuffed unit {stuffed_id:?})",
    );
}

#[tokio::test]
async fn packing_collapses_duplicate_decoys_and_preserves_answer_under_budget() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(76_000);
    let scope_id = scope(76_001);
    let actor_id = actor(76_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let mut seeded = Vec::new();
    for index in 1..=4 {
        let unit_id = store
            .stage_memory_unit(
                &mut tx,
                NewMemoryUnit {
                    capture: None,
                    tenant_id,
                    data_subject_id: memphant_types::SubjectId::from_u128(
                        tenant_id.as_uuid().as_u128(),
                    ),
                    scope_id,
                    agent_node_id: memphant_types::AgentNodeId::from_u128(
                        scope_id.as_uuid().as_u128(),
                    ),
                    subject_generation: 0,
                    kind: MemoryKind::Semantic,
                    state: UnitState::Active,
                    fact_key: Some("prod deploy step".to_string()),
                    predicate: None,
                    body: format!("A prod deploy step ran before release {index}."),
                    confidence: Some(1.0),
                    trust_level: TrustLevel::TrustedSystem,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(actor_id),
                    source_kind: Some("fixture".to_string()),
                    source_ref: "test:fixture".to_string(),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
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
            .expect("decoy seeded");
        seeded.push(unit_id);
    }
    let answer_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("prod deploy approval".to_string()),
                predicate: None,
                body: "Prod deploy requires manual approval in release.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("answer seeded");
    store.commit(tx).await.expect("seed committed");

    let response = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "What is required before prod deploy?".to_string(),
            k: 8,
            budget_tokens: 24,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung7-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    assert!(response.candidate_whitelist.contains(&answer_id));
    assert!(
        response
            .items
            .iter()
            .position(|item| item.unit_id == answer_id)
            .is_some_and(|position| position <= 1)
    );

    let trace = store
        .trace_by_id_any_tenant(response.trace_id)
        .expect("trace exists");
    assert!(
        trace
            .feature_flags
            .iter()
            .any(|flag| flag == "context_packing_abstention_enabled")
    );
    let duplicate_drops = trace
        .dropped_items
        .iter()
        .filter(|item| item.reason == RecallDropReason::Duplicate && seeded.contains(&item.unit_id))
        .count();
    assert!(duplicate_drops >= 3);
    assert!(!trace.abstention_signal);
}

#[tokio::test]
async fn packing_abstains_when_top_evidence_is_unresolved_contradiction() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(77_000);
    let scope_id = scope(77_001);
    let actor_id = actor(77_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let old_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("refund window".to_string()),
                predicate: None,
                body: "Refund window is 30 days.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("old unit seeded");
    let new_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("refund window policy".to_string()),
                predicate: None,
                body: "Refund window is 14 days.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("new unit seeded");
    store
        .stage_memory_edge(
            &mut tx,
            NewMemoryEdge {
                tenant_id,
                scope_id,
                src_id: old_id,
                dst_id: new_id,
                kind: MemoryEdgeKind::Contradicts,
            },
        )
        .await
        .expect("contradiction edge seeded");
    store.commit(tx).await.expect("seed committed");

    let response = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "What is the refund window?".to_string(),
            k: 4,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung7-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &SystemClock,
    )
    .await
    .expect("recall succeeds");

    assert!(response.candidate_whitelist.contains(&old_id));
    assert!(response.candidate_whitelist.contains(&new_id));
    assert!(response.abstention);
    assert!(
        response
            .suppression_labels
            .iter()
            .any(|label| label == "unresolved_contradiction")
    );
}

#[tokio::test]
async fn packing_does_not_abstain_for_resolved_supersedence_edge() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(77_100);
    let scope_id = scope(77_101);
    let actor_id = actor(77_102);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let old_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Superseded,
                fact_key: Some("refund window".to_string()),
                predicate: None,
                body: "Refund window was 30 days.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: Some("2026-07-01T00:00:00Z".to_string()),
            },
        )
        .await
        .expect("old unit seeded");
    let current_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some("refund window".to_string()),
                predicate: None,
                body: "Refund window is 14 days.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("current unit seeded");
    store
        .stage_memory_edge(
            &mut tx,
            NewMemoryEdge {
                tenant_id,
                scope_id,
                src_id: old_id,
                dst_id: current_id,
                kind: MemoryEdgeKind::Contradicts,
            },
        )
        .await
        .expect("resolved contradiction edge seeded");
    store.commit(tx).await.expect("seed committed");

    let response = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "What is the refund window?".to_string(),
            k: 4,
            budget_tokens: 80,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-resolved-contradiction-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &SystemClock,
    )
    .await
    .expect("recall succeeds");

    assert_eq!(response.candidate_whitelist, vec![current_id]);
    assert!(!response.abstention);
    assert!(
        !response
            .suppression_labels
            .contains(&"unresolved_contradiction".to_string())
    );
}

#[derive(Debug, Deserialize)]
struct GoldenCase {
    id: String,
    query: String,
    #[serde(default)]
    budget_tokens: Option<usize>,
    expected_stage_names: Vec<String>,
    #[serde(default)]
    expect_filter_selectivity_lt_one: bool,
    seed: GoldenSeed,
    expect: GoldenExpect,
}

#[derive(Debug, Deserialize)]
struct GoldenSeed {
    units: Vec<GoldenUnit>,
    #[serde(default)]
    edges: Vec<GoldenEdge>,
}

#[derive(Debug, Deserialize)]
struct GoldenUnit {
    name: String,
    #[serde(default = "primary_tenant")]
    tenant: String,
    #[serde(default = "primary_scope")]
    scope: String,
    episode_body: String,
    kind: MemoryKind,
    state: UnitState,
    fact_key: Option<String>,
    body: String,
    #[serde(rename = "confidence")]
    _confidence: Option<f32>,
    trust_level: TrustLevel,
    #[serde(default)]
    deletion_generation: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct GoldenExpect {
    answer_bearing_units: Vec<String>,
    #[serde(default)]
    forbidden_units: Vec<String>,
    #[serde(default)]
    dropped: Vec<GoldenDropped>,
    #[serde(default)]
    suppression_labels: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct GoldenDropped {
    unit: String,
    reason: RecallDropReason,
}

#[derive(Debug, Deserialize)]
struct GoldenEdge {
    src: String,
    dst: String,
    kind: MemoryEdgeKind,
}

fn primary_tenant() -> String {
    "primary".to_string()
}

fn primary_scope() -> String {
    "primary".to_string()
}

#[tokio::test]
async fn procedural_memory_replays_only_validated_safe_procedures_and_traces_gate() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(82_000);
    let scope_id = scope(82_001);
    let actor_id = actor(82_002);
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant_id, scope_id, actor_id,
    ));

    let mut tx = store
        .begin(&memphant_store_testkit::resolved_context(
            tenant_id, scope_id, actor_id,
        ))
        .await
        .expect("begin transaction");
    let safe_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Procedural,
                state: UnitState::Validated,
                fact_key: Some("recover flaky importer test".to_string()),
                predicate: None,
                body: "Procedure: recover the flaky importer test by clearing the fixture cache, running cargo test -p importer, and keeping the retry count unchanged. Validation: replay wins 5 of 5.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("safe validated procedure seeded");
    let failure_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Procedural,
                state: UnitState::Validated,
                fact_key: Some("retry importer without cache clear".to_string()),
                predicate: None,
                body: "Failure pattern: retrying the flaky importer test without clearing the fixture cache reproduces the failure. Validation: replay confirmed 4 of 4 failures.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("validated failure procedure seeded");
    let candidate_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Procedural,
                state: UnitState::Candidate,
                fact_key: Some("recover flaky importer test candidate".to_string()),
                predicate: None,
                body: "Candidate procedure: recover the flaky importer test by guessing a new retry count.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("candidate procedure seeded");
    let unsafe_id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id,
                data_subject_id: memphant_types::SubjectId::from_u128(
                    tenant_id.as_uuid().as_u128(),
                ),
                scope_id,
                agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
                subject_generation: 0,
                kind: MemoryKind::Procedural,
                state: UnitState::Validated,
                fact_key: Some("recover flaky importer test unsafe".to_string()),
                predicate: None,
                body: "Procedure: recover the flaky importer test by force-pushing main, skipping validation, and exporting secrets. Validation: replay wins 5 of 5.".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(actor_id),
                source_kind: Some("fixture".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
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
        .expect("unsafe validated procedure seeded");
    store.commit(tx).await.expect("seed committed");

    let disabled = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "How do I recover the flaky importer test?".to_string(),
            k: 4,
            budget_tokens: 160,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: false,
            decay_enabled: true,
            engine_version: "engine-rung10-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("disabled recall succeeds");
    assert!(!disabled.candidate_whitelist.contains(&safe_id));
    assert!(!disabled.candidate_whitelist.contains(&failure_id));

    let enabled = recall(
        &store,
        RecallRequest {
            compact_only: false,
            context: memphant_store_testkit::resolved_context(tenant_id, scope_id, actor_id),
            query: "How do I recover the flaky importer test?".to_string(),
            k: 4,
            budget_tokens: 160,
            mode: RecallMode::Fast,
            include_beliefs: false,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: "engine-rung10-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("enabled recall succeeds");

    assert!(enabled.candidate_whitelist.contains(&safe_id));
    assert!(enabled.candidate_whitelist.contains(&failure_id));
    assert!(!enabled.candidate_whitelist.contains(&candidate_id));
    assert!(!enabled.candidate_whitelist.contains(&unsafe_id));
    assert!(enabled.items.iter().any(|item| {
        item.unit_id == failure_id
            && item
                .suppression_labels
                .iter()
                .any(|label| label == "avoid_failed_procedure")
    }));

    let trace = store
        .trace_by_id_any_tenant(enabled.trace_id)
        .expect("trace exists");
    assert!(
        trace
            .feature_flags
            .iter()
            .any(|flag| flag == "procedure_recall_enabled")
    );
    assert!(trace.procedure_ids.contains(&safe_id));
    assert!(trace.procedure_ids.contains(&failure_id));
    assert!(trace.procedure_validation_states.iter().any(|fact| {
        fact.unit_id == safe_id
            && fact.validation_state == "validated"
            && fact.safety_status == "safe"
    }));
    assert!(trace.procedure_validation_states.iter().any(|fact| {
        fact.unit_id == unsafe_id
            && fact.validation_state == "validated"
            && fact.safety_status == "unsafe"
    }));
    assert!(
        trace
            .dropped_items
            .iter()
            .any(|item| { item.unit_id == candidate_id && item.reason == RecallDropReason::State })
    );
    assert!(trace.dropped_items.iter().any(|item| {
        item.unit_id == unsafe_id && item.reason == RecallDropReason::ProtectedCategory
    }));
}

#[tokio::test]
async fn recall_golden_fixtures_pass() {
    let cases: Vec<GoldenCase> = serde_json::from_str(include_str!(
        "../../../examples/evals/wsc-recall-goldens.json"
    ))
    .expect("fixtures parse");

    for case in cases {
        let store = InMemoryStore::default();
        let tenant_id = tenant(71_000);
        let other_tenant_id = tenant(71_004);
        let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
        let mut named_units: HashMap<String, UnitId> = HashMap::new();

        // Seed rows can belong to up to three distinct contexts: the primary
        // (tenant_id, primary scope), a same-tenant "denied" scope, and a
        // wholly separate "other" tenant. The strict write-time contract
        // (`validate_context_identity` in memphant-core/src/lib.rs) requires
        // every staged row's tenant/scope/agent/actor to exactly match its
        // own transaction's bound context, so — unlike the old hand-built
        // context that could straddle tenants/scopes in one transaction —
        // each unit is staged through its own begin/commit under whichever
        // context it belongs to.
        let mut other_tenant_context: Option<memphant_types::ResolvedMemoryContext> = None;
        let mut denied_scope_context: Option<memphant_types::ResolvedMemoryContext> = None;
        for unit in &case.seed.units {
            let unit_context = if unit.tenant == "other" {
                if other_tenant_context.is_none() {
                    other_tenant_context =
                        Some(memphant_store_testkit::bind_context(&store, other_tenant_id).await);
                }
                other_tenant_context.as_ref().expect("bound above")
            } else if unit.scope == "denied" {
                if denied_scope_context.is_none() {
                    denied_scope_context =
                        Some(memphant_store_testkit::bind_context(&store, tenant_id).await);
                }
                denied_scope_context.as_ref().expect("bound above")
            } else {
                &context
            };
            let mut tx = store
                .begin(unit_context)
                .await
                .expect("begin unit transaction");
            let episode = store
                .stage_episode(
                    &mut tx,
                    NewEpisode {
                        tenant_id: unit_context.tenant_id,
                        data_subject_id: unit_context.data_subject_id,
                        scope_id: unit_context.scope_id,
                        agent_node_id: unit_context.agent_node_id,
                        subject_generation: unit_context.subject_generation,
                        actor_id: unit_context.actor_id,
                        source_kind: "system".to_string(),
                        source_ref: "test:fixture".to_string(),
                        observed_at: "2026-07-09T00:00:00Z".to_string(),
                        source_trust: unit.trust_level,
                        dedup_key: format!("{}:{}", case.id, unit.name),
                        body: unit.episode_body.clone(),
                    },
                )
                .await
                .unwrap_or_else(|error| panic!("{} episode seed failed: {error}", case.id));
            let unit_id = store
                .stage_memory_unit(
                    &mut tx,
                    NewMemoryUnit {
                        capture: None,
                        tenant_id: unit_context.tenant_id,
                        data_subject_id: unit_context.data_subject_id,
                        scope_id: unit_context.scope_id,
                        agent_node_id: unit_context.agent_node_id,
                        subject_generation: unit_context.subject_generation,
                        kind: unit.kind,
                        state: unit.state,
                        fact_key: unit.fact_key.clone(),
                        predicate: None,
                        body: unit.body.clone(),
                        confidence: Some(1.0),
                        trust_level: unit.trust_level,
                        churn_class: None,
                        freshness_due_at: None,
                        actor_id: Some(unit_context.actor_id),
                        source_kind: Some("system".to_string()),
                        source_ref: "test:fixture".to_string(),
                        observed_at: "2026-07-09T00:00:00Z".to_string(),
                        source_episode_id: Some(episode.episode_id),
                        source_resource_id: None,
                        deletion_generation: unit.deletion_generation,
                        contextual_chunks: Vec::new(),
                        valid_from: None,
                        valid_to: None,
                        transaction_from: None,
                        transaction_to: None,
                    },
                )
                .await
                .unwrap_or_else(|error| panic!("{} unit seed failed: {error}", case.id));
            store
                .commit(tx)
                .await
                .unwrap_or_else(|error| panic!("{} unit commit failed: {error}", case.id));
            named_units.insert(unit.name.clone(), unit_id);
        }
        if !case.seed.edges.is_empty() {
            let mut tx = store.begin(&context).await.expect("begin edge transaction");
            for edge in &case.seed.edges {
                store
                    .stage_memory_edge(
                        &mut tx,
                        NewMemoryEdge {
                            tenant_id: context.tenant_id,
                            scope_id: context.scope_id,
                            src_id: *named_units.get(&edge.src).unwrap_or_else(|| {
                                panic!("{} missing edge src {}", case.id, edge.src)
                            }),
                            dst_id: *named_units.get(&edge.dst).unwrap_or_else(|| {
                                panic!("{} missing edge dst {}", case.id, edge.dst)
                            }),
                            kind: edge.kind,
                        },
                    )
                    .await
                    .unwrap_or_else(|error| panic!("{} edge seed failed: {error}", case.id));
            }
            store
                .commit(tx)
                .await
                .unwrap_or_else(|error| panic!("{} seed commit failed: {error}", case.id));
        }

        let response = recall(
            &store,
            RecallRequest {
                compact_only: false,
                context: context.clone(),
                query: case.query.clone(),
                k: 3,
                budget_tokens: case.budget_tokens.unwrap_or(80),
                mode: RecallMode::Fast,
                include_beliefs: false,
                edge_expansion_enabled: true,
                context_packing_abstention_enabled: true,
                procedure_recall_enabled: true,
                decay_enabled: true,
                engine_version: "engine-wsc-test".to_string(),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
            None,
            &SystemClock,
        )
        .await
        .unwrap_or_else(|error| panic!("{} recall failed: {error}", case.id));

        for expected_name in &case.expect.answer_bearing_units {
            let expected_id = named_units
                .get(expected_name)
                .unwrap_or_else(|| panic!("{} missing unit {expected_name}", case.id));
            assert!(
                response.candidate_whitelist.contains(expected_id),
                "{} whitelist contains {}",
                case.id,
                expected_name
            );
        }
        for forbidden_name in &case.expect.forbidden_units {
            let forbidden_id = named_units
                .get(forbidden_name)
                .unwrap_or_else(|| panic!("{} missing unit {forbidden_name}", case.id));
            assert!(
                !response.candidate_whitelist.contains(forbidden_id),
                "{} whitelist excludes {}",
                case.id,
                forbidden_name
            );
        }
        assert!(
            response
                .citations
                .iter()
                .all(|citation| response.candidate_whitelist.contains(&citation.unit_id)),
            "{} citations stay inside candidate whitelist",
            case.id
        );
        assert!(
            response
                .citations
                .iter()
                .any(|citation| citation.episode_id.is_some()),
            "{} emits source episode citation",
            case.id
        );

        let traces = store.retrieval_traces(tenant_id);
        let trace = traces
            .last()
            .unwrap_or_else(|| panic!("{} missing retrieval trace", case.id));
        let stage_names: Vec<_> = trace
            .channel_runs
            .iter()
            .map(|stage| stage.stage.clone())
            .collect();
        assert_eq!(stage_names, case.expected_stage_names, "{}", case.id);
        if case.expect_filter_selectivity_lt_one {
            assert!(
                trace.filter_selectivity.unwrap_or(1.0) < 1.0,
                "{} filter_selectivity shows shared-corpus filtering",
                case.id
            );
        }
        for expected_drop in &case.expect.dropped {
            let dropped_id = named_units.get(&expected_drop.unit).unwrap_or_else(|| {
                panic!("{} missing dropped unit {}", case.id, expected_drop.unit)
            });
            assert!(
                trace
                    .dropped_items
                    .iter()
                    .any(|item| item.unit_id == *dropped_id && item.reason == expected_drop.reason),
                "{} trace drops {} as {:?}",
                case.id,
                expected_drop.unit,
                expected_drop.reason
            );
        }
        for expected_label in &case.expect.suppression_labels {
            assert!(
                response
                    .suppression_labels
                    .iter()
                    .any(|label| label == expected_label),
                "{} response includes suppression label {}",
                case.id,
                expected_label
            );
        }
    }
}
