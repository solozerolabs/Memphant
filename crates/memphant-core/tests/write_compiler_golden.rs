use memphant_core::{
    CoreError, FixedClock, InMemoryStore, NoopEmbedding, correct_memory, reflect_recorded,
    retain_episode,
};
use memphant_types::{
    AdmissionAction, ContextualChunk, CorrectRequest, CorrectSelector, CorrectionPayload, JobId,
    MemoryEdgeKind, MemoryKind, ReflectCandidate, ReflectInput, ResolvedMemoryContext,
    RetainRequest, TenantId, TrustLevel, UnitId, UnitState,
};
use serde::Deserialize;

const CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");

fn tenant(value: u128) -> TenantId {
    TenantId::from_u128(value)
}

async fn retain_and_reflect(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    seed: ReflectSeed<'_>,
) {
    let retained = retain_episode(
        store,
        context,
        RetainRequest {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_kind: seed.source_kind.to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: seed.trust_level,
            subject_hint: Some(seed.subject.to_string()),
            subject: None,
            predicate: None,
            body: seed.body.to_string(),
            compiler_version: "compiler-rung15".to_string(),
        },
    )
    .await
    .expect("retain succeeds");
    let job = store
        .reflect_jobs(context.tenant_id)
        .last()
        .cloned()
        .expect("reflect job queued");
    reflect_recorded(
        store,
        ReflectInput {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_ref: "test:reflect".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_body: None,
            episode_id: Some(retained.episode_id),
            resource_id: None,
            job_id: job.id,
            compiler_version: "compiler-rung15".to_string(),
            candidates: vec![ReflectCandidate {
                compact: None,
                source_kind: seed.source_kind.to_string(),
                trust_level: seed.trust_level,
                actor_id: context.actor_id,
                subject: Some(seed.subject.to_string()),
                predicate: Some(seed.predicate.to_string()),
                fact_key: None,
                kind: None,
                body: seed.body.to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                target_unit_ids: None,
            }],
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("reflect succeeds");
}

struct ReflectSeed<'a> {
    source_kind: &'a str,
    trust_level: TrustLevel,
    subject: &'a str,
    predicate: &'a str,
    body: &'a str,
}

#[derive(Debug, Deserialize)]
struct GoldenCase {
    id: String,
    episodes: Vec<GoldenEpisode>,
    expected_actions: Vec<AdmissionAction>,
    expected_unit_count: usize,
    #[serde(default)]
    expected_semantic_bodies: Vec<String>,
    #[serde(default)]
    expected_belief_bodies: Vec<String>,
    #[serde(default)]
    expected_quarantined_bodies: Vec<String>,
    #[serde(default)]
    expected_freshness_due_bodies: Vec<String>,
    #[serde(default)]
    expected_edge_kinds: Vec<MemoryEdgeKind>,
}

#[derive(Debug, Deserialize)]
struct GoldenEpisode {
    source_kind: String,
    trust_level: TrustLevel,
    // Retained for fixture-schema fidelity; the compiled unit's `actor_id`
    // must equal the transaction's bound `context.actor_id` under the strict
    // write-time contract (see `owned_unit` in
    // memphant-core/src/lib.rs::persist_compiled_units), so this per-episode
    // fixture value is no longer read.
    #[serde(rename = "actor")]
    _actor: u128,
    subject: Option<String>,
    predicate: Option<String>,
    body: String,
    #[serde(rename = "confidence")]
    _confidence: Option<f32>,
    churn_class: Option<String>,
    admission_hint: Option<AdmissionAction>,
}

#[tokio::test]
async fn write_compiler_golden_fixtures_pass() {
    let cases: Vec<GoldenCase> = serde_json::from_str(include_str!(
        "../../../examples/evals/wsb-write-goldens.json"
    ))
    .expect("fixtures parse");

    for case in cases {
        let store = InMemoryStore::default();
        let tenant_id = tenant(10_000);
        let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
        let mut observed_actions = Vec::new();

        for (episode_index, episode) in case.episodes.iter().enumerate() {
            let retained = retain_episode(
                &store,
                &context,
                RetainRequest {
                    tenant_id: context.tenant_id,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    actor_id: context.actor_id,
                    source_kind: episode.source_kind.clone(),
                    // Each fixture episode is a DISTINCT observation event, so it
                    // gets a distinct `source_ref`. A shared `source_ref` would
                    // make two same-body episodes collide on the episode dedup
                    // key and coalesce to one idempotent reflect job — collapsing
                    // corroboration cases (`duplicate_collapse`,
                    // `corroboration_farming_resistance`) that must reflect twice
                    // and merge.
                    source_ref: format!("test:fixture:{episode_index}"),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
                    source_trust: episode.trust_level,
                    subject_hint: episode.subject.clone(),
                    subject: None,
                    predicate: None,
                    body: episode.body.clone(),
                    compiler_version: "compiler-wsb-golden".to_string(),
                },
            )
            .await
            .unwrap_or_else(|error| panic!("{} retain failed: {error}", case.id));
            let job = store
                .reflect_jobs(tenant_id)
                .last()
                .cloned()
                .unwrap_or_else(|| panic!("{} missing reflect job", case.id));

            let (trace, _) = reflect_recorded(
                &store,
                ReflectInput {
                    tenant_id: context.tenant_id,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    actor_id: context.actor_id,
                    source_ref: "test:reflect".to_string(),
                    observed_at: "2026-07-09T00:00:00Z".to_string(),
                    source_body: None,
                    episode_id: Some(retained.episode_id),
                    resource_id: None,
                    job_id: job.id,
                    compiler_version: "compiler-wsb-golden".to_string(),
                    candidates: vec![ReflectCandidate {
                        compact: None,
                        source_kind: episode.source_kind.clone(),
                        trust_level: episode.trust_level,
                        // Every compiled unit's `actor_id` must equal the
                        // transaction's own bound `context.actor_id` (see
                        // `owned_unit` in `persist_compiled_units`,
                        // memphant-core/src/lib.rs) — the strict write-time
                        // contract no longer permits attributing a unit to an
                        // arbitrary per-fixture actor. Corroboration cases in
                        // this golden set (`independent_corroboration_promotes_belief`,
                        // `invalidate_action`) still exercise cross-source
                        // independence via `episode.source_kind`, which
                        // `is_independent_source` also checks.
                        actor_id: context.actor_id,
                        subject: episode.subject.clone(),
                        predicate: episode.predicate.clone(),
                        fact_key: None,
                        kind: None,
                        body: episode.body.clone(),
                        confidence: None,
                        churn_class: episode.churn_class.clone(),
                        admission_hint: episode.admission_hint,
                        contextual_chunks: Vec::new(),
                        valid_from: None,
                        valid_to: None,
                        target_unit_ids: None,
                    }],
                },
                &NoopEmbedding,
                &CLOCK,
            )
            .await
            .unwrap_or_else(|error| panic!("{} reflect failed: {error}", case.id));

            assert_eq!(
                trace.stage_names(),
                [
                    "extract",
                    "detect",
                    "corroborate",
                    "promote",
                    "decay",
                    "trust"
                ],
                "{} trace stage contract",
                case.id
            );
            assert!(trace.cost_units > 0, "{} trace cost is recorded", case.id);
            observed_actions.extend(trace.actions);
        }

        assert_eq!(observed_actions, case.expected_actions, "{}", case.id);
        assert_eq!(
            store.memory_units(tenant_id).len(),
            case.expected_unit_count,
            "{} unit count",
            case.id
        );
        let semantic_bodies: Vec<_> = store
            .active_semantic_units(tenant_id)
            .into_iter()
            .map(|unit| unit.body)
            .collect();
        let belief_bodies: Vec<_> = store
            .belief_units(tenant_id)
            .into_iter()
            .map(|unit| unit.body)
            .collect();
        let edge_kinds: Vec<_> = store
            .memory_edges(tenant_id)
            .into_iter()
            .map(|edge| edge.kind)
            .collect();
        let quarantined_bodies: Vec<_> = store
            .quarantined_units(tenant_id)
            .into_iter()
            .map(|unit| unit.body)
            .collect();
        let freshness_due_bodies: Vec<_> = store
            .freshness_due_units(tenant_id)
            .into_iter()
            .map(|unit| unit.body)
            .collect();

        assert_eq!(
            semantic_bodies, case.expected_semantic_bodies,
            "{}",
            case.id
        );
        assert_eq!(belief_bodies, case.expected_belief_bodies, "{}", case.id);
        assert_eq!(
            quarantined_bodies, case.expected_quarantined_bodies,
            "{}",
            case.id
        );
        assert_eq!(
            freshness_due_bodies, case.expected_freshness_due_bodies,
            "{}",
            case.id
        );
        assert_eq!(edge_kinds, case.expected_edge_kinds, "{}", case.id);
        if case.id == "contradiction_detection" {
            let units = store.memory_units(tenant_id);
            let superseded = units
                .iter()
                .find(|unit| unit.state == UnitState::Superseded)
                .expect("the prior transaction rectangle is superseded");
            assert_eq!(superseded.body, "Callback token version is v1.");
            assert_eq!(superseded.valid_from, None);
            assert_eq!(superseded.valid_to, None);
            assert_eq!(superseded.transaction_to.as_deref(), Some(CLOCK.0));

            let historical = units
                .iter()
                .find(|unit| {
                    unit.state == UnitState::Active && unit.body == "Callback token version is v1."
                })
                .expect("the prior value remains valid before the correction");
            assert_eq!(historical.valid_from, None);
            assert_eq!(historical.valid_to.as_deref(), Some(CLOCK.0));
            assert_eq!(historical.transaction_to, None);

            let current = units
                .iter()
                .find(|unit| {
                    unit.state == UnitState::Active && unit.body == "Callback token version is v2."
                })
                .expect("the corrected value is current");
            assert_eq!(current.valid_from.as_deref(), Some(CLOCK.0));
            assert_eq!(current.valid_to, None);
            assert_eq!(current.transaction_to, None);
        }
        if case.id == "stale_fact_handling" {
            let units = store.memory_units(tenant_id);
            let superseded = units
                .iter()
                .find(|unit| unit.state == UnitState::Superseded)
                .expect("the prior transaction rectangle is superseded");
            assert_eq!(superseded.body, "Current project is Apollo.");
            assert_eq!(superseded.valid_from, None);
            assert_eq!(superseded.valid_to, None);
            assert_eq!(superseded.transaction_to.as_deref(), Some(CLOCK.0));

            let historical = units
                .iter()
                .find(|unit| {
                    unit.state == UnitState::Active && unit.body == "Current project is Apollo."
                })
                .expect("the prior value remains valid before the correction");
            assert_eq!(historical.valid_from, None);
            assert_eq!(historical.valid_to.as_deref(), Some(CLOCK.0));
            assert_eq!(historical.transaction_to, None);

            let current = units
                .iter()
                .find(|unit| {
                    unit.state == UnitState::Active && unit.body == "Current project is Borealis."
                })
                .expect("the corrected value is current");
            assert_eq!(current.valid_from.as_deref(), Some(CLOCK.0));
            assert_eq!(current.valid_to, None);
            assert_eq!(current.transaction_to, None);
            assert_eq!(current.churn_class.as_deref(), Some("volatile"));
            assert!(current.freshness_due_at.is_some());
        }
    }
}

#[tokio::test]
async fn reflect_recorded_is_idempotent_for_duplicate_job_delivery() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(30_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let retained = retain_episode(
        &store,
        &context,
        RetainRequest {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_kind: "user".to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: Some("deployment region".to_string()),
            subject: None,
            predicate: None,
            body: "Deployment region is Taipei.".to_string(),
            compiler_version: "compiler-wsb-golden".to_string(),
        },
    )
    .await
    .expect("retain succeeds");
    let job = store.reflect_jobs(tenant_id)[0].clone();
    let input = ReflectInput {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        source_ref: "test:reflect".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        source_body: None,
        episode_id: Some(retained.episode_id),
        resource_id: None,
        job_id: job.id,
        compiler_version: "compiler-wsb-golden".to_string(),
        candidates: vec![ReflectCandidate {
            compact: None,
            source_kind: "user".to_string(),
            trust_level: TrustLevel::TrustedUser,
            actor_id: context.actor_id,
            subject: Some("deployment region".to_string()),
            predicate: Some("value".to_string()),
            fact_key: None,
            kind: None,
            body: "Deployment region is Taipei.".to_string(),
            confidence: None,
            churn_class: None,
            admission_hint: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            target_unit_ids: None,
        }],
    };

    let (first, first_ids) = reflect_recorded(&store, input.clone(), &NoopEmbedding, &CLOCK)
        .await
        .expect("first reflect succeeds");
    let (second, second_ids) = reflect_recorded(&store, input, &NoopEmbedding, &CLOCK)
        .await
        .expect("redelivery reflect succeeds");

    assert_eq!(first, second);
    assert_eq!(first_ids.len(), 1, "first reflect creates the unit");
    assert!(second_ids.is_empty(), "redelivery creates nothing new");
    assert_eq!(store.memory_units(tenant_id).len(), 1);
    assert_eq!(store.reflect_traces(tenant_id).len(), 1);
}

#[tokio::test]
async fn invalidation_without_an_open_exact_key_fails_closed() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(30_001);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let retained = retain_episode(
        &store,
        &context,
        RetainRequest {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_kind: "system".to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedSystem,
            subject_hint: Some("missing todo".to_string()),
            subject: None,
            predicate: None,
            body: "Delete the missing todo now.".to_string(),
            compiler_version: "compiler-invalidation".to_string(),
        },
    )
    .await
    .expect("retain succeeds");
    let job = store.reflect_jobs(tenant_id)[0].clone();

    let error = reflect_recorded(
        &store,
        ReflectInput {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_ref: "test:reflect".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_body: None,
            episode_id: Some(retained.episode_id),
            resource_id: None,
            job_id: job.id,
            compiler_version: "compiler-invalidation".to_string(),
            candidates: vec![ReflectCandidate {
                compact: None,
                source_kind: "system".to_string(),
                trust_level: TrustLevel::TrustedSystem,
                actor_id: context.actor_id,
                subject: Some("todos".to_string()),
                predicate: Some("missing-todo".to_string()),
                fact_key: None,
                kind: None,
                body: "Deleted structured item todos/missing-todo from memory".to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: Some(AdmissionAction::Invalidate),
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                target_unit_ids: None,
            }],
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect_err("a delete that matched nothing must not record success");

    assert!(matches!(error, CoreError::ProviderInvalid(_)));
    assert!(store.reflect_traces(tenant_id).is_empty());
}

#[tokio::test]
async fn structured_replacement_requires_the_exact_active_target_id() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(30_002);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "user",
            trust_level: TrustLevel::TrustedUser,
            subject: "profile",
            predicate: "home_city",
            body: "profile item home_city: {\"value\":\"Oslo\"}",
        },
    )
    .await;
    let active_id = store
        .active_semantic_units(tenant_id)
        .into_iter()
        .find(|unit| unit.body.contains("Oslo"))
        .expect("seeded target is active")
        .id;
    let replacement = |target| ReflectCandidate {
        compact: None,
        source_kind: "user".to_string(),
        trust_level: TrustLevel::TrustedUser,
        actor_id: context.actor_id,
        subject: Some("profile".to_string()),
        predicate: Some("home_city".to_string()),
        fact_key: None,
        kind: None,
        body: "profile item home_city: {\"value\":\"Bergen\"}".to_string(),
        confidence: None,
        churn_class: None,
        admission_hint: None,
        contextual_chunks: Vec::new(),
        valid_from: None,
        valid_to: None,
        target_unit_ids: Some(vec![target]),
    };
    let input = |job_id, target| ReflectInput {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        source_ref: "test:reflect".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        source_body: None,
        episode_id: None,
        resource_id: None,
        job_id,
        compiler_version: "compiler-exact-target".to_string(),
        candidates: vec![replacement(target)],
    };

    let error = reflect_recorded(
        &store,
        input(JobId::new(), UnitId::new()),
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect_err("a guessed target id must fail closed");
    assert!(matches!(error, CoreError::ProviderInvalid(_)));

    reflect_recorded(
        &store,
        input(JobId::new(), active_id),
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("the exact active target is replaceable");
    let current = store
        .active_semantic_units(tenant_id)
        .into_iter()
        .find(|unit| unit.body.contains("Bergen"))
        .expect("replacement is active");
    assert_ne!(current.id, active_id);
}

#[tokio::test]
async fn reflect_candidate_contextual_chunks_are_stored_with_source_episode() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(31_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let retained = retain_episode(
        &store,
        &context,
        RetainRequest {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_kind: "system".to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedSystem,
            subject_hint: Some("deployment runbook".to_string()),
            subject: None,
            predicate: None,
            body: "The deployment runbook says the emergency breaker codeword is albatross."
                .to_string(),
            compiler_version: "compiler-ws4-chunks".to_string(),
        },
    )
    .await
    .expect("retain succeeds");
    let job = store.reflect_jobs(tenant_id)[0].clone();

    reflect_recorded(
        &store,
        ReflectInput {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_ref: "test:reflect".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_body: None,
            episode_id: Some(retained.episode_id),
            resource_id: None,
            job_id: job.id,
            compiler_version: "compiler-ws4-chunks".to_string(),
            candidates: vec![ReflectCandidate {
                compact: None,
                source_kind: "system".to_string(),
                trust_level: TrustLevel::TrustedSystem,
                actor_id: context.actor_id,
                subject: Some("deployment runbook".to_string()),
                predicate: Some("emergency breaker".to_string()),
                fact_key: None,
                kind: None,
                body: "Runbook contains a gated switch.".to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: None,
                contextual_chunks: vec![ContextualChunk {
                    id: "chunk-albatross-breaker".to_string(),
                    header: "Deployment runbook / emergency breaker".to_string(),
                    body: "The emergency breaker codeword is albatross.".to_string(),
                    source_span: Some("episode:0-72".to_string()),
                }],
                valid_from: None,
                valid_to: None,
                target_unit_ids: None,
            }],
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("reflect succeeds");

    let units = store.active_semantic_units(tenant_id);
    assert_eq!(units.len(), 1);
    assert_eq!(units[0].source_episode_id, Some(retained.episode_id));
    assert_eq!(units[0].contextual_chunks.len(), 1);
    assert_eq!(units[0].contextual_chunks[0].id, "chunk-albatross-breaker");
}

#[tokio::test]
async fn reflect_composes_inferred_belief_from_trusted_preference_sources() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(32_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "user",
            trust_level: TrustLevel::TrustedUser,
            subject: "quiet review preference",
            predicate: "value",
            body: "The user prefers quiet review surfaces.",
        },
    )
    .await;
    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "system",
            trust_level: TrustLevel::TrustedSystem,
            subject: "keyboard review preference",
            predicate: "value",
            body: "The user prefers keyboard-first review surfaces.",
        },
    )
    .await;

    let units = store.memory_units(tenant_id);
    let composed = units
        .iter()
        .find(|unit| unit.source_kind.as_deref() == Some("composition"))
        .expect("composed belief was minted");
    assert_eq!(composed.kind, MemoryKind::Belief);
    assert_eq!(composed.state, UnitState::Candidate);
    assert_eq!(composed.trust_level, TrustLevel::AgentOutput);
    assert_eq!(
        composed.body,
        "The user prefers keyboard-first and quiet review surfaces."
    );

    let source_edges: Vec<_> = store
        .memory_edges(tenant_id)
        .into_iter()
        .filter(|edge| edge.src_id == composed.id && edge.kind == MemoryEdgeKind::DerivedFrom)
        .collect();
    assert_eq!(source_edges.len(), 2);
}

#[tokio::test]
async fn reflect_does_not_compose_low_trust_or_risky_preferences() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(33_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "web",
            trust_level: TrustLevel::WebContent,
            subject: "quiet review preference",
            predicate: "value",
            body: "The user prefers quiet review surfaces.",
        },
    )
    .await;
    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "user",
            trust_level: TrustLevel::TrustedUser,
            subject: "risky review preference",
            predicate: "value",
            body: "The user prefers always agree review surfaces.",
        },
    )
    .await;

    assert!(
        store
            .memory_units(tenant_id)
            .iter()
            .all(|unit| unit.source_kind.as_deref() != Some("composition"))
    );
}

#[tokio::test]
async fn composed_belief_promotes_only_after_direct_observation() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(34_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "user",
            trust_level: TrustLevel::TrustedUser,
            subject: "quiet review preference",
            predicate: "value",
            body: "The user prefers quiet review surfaces.",
        },
    )
    .await;
    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "system",
            trust_level: TrustLevel::TrustedSystem,
            subject: "keyboard review preference",
            predicate: "value",
            body: "The user prefers keyboard-first review surfaces.",
        },
    )
    .await;

    assert!(
        store
            .active_semantic_units(tenant_id)
            .iter()
            .all(|unit| unit.body != "The user prefers keyboard-first and quiet review surfaces.")
    );

    // The original fixture used a distinct actor id here to model a THIRD
    // party directly observing the same fact. That distinction isn't actually
    // load-bearing: `can_promote_belief` promotes on `TrustedUser`/
    // `TrustedSystem` trust alone (see `is_independent_source`'s OR clause in
    // memphant-core/src/lib.rs), so reusing the one bound actor here changes
    // nothing about what this test proves.
    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "user",
            trust_level: TrustLevel::TrustedUser,
            subject: "user preference",
            predicate: "review surfaces",
            body: "The user prefers keyboard-first and quiet review surfaces.",
        },
    )
    .await;

    let promoted = store
        .active_semantic_units(tenant_id)
        .into_iter()
        .find(|unit| unit.body == "The user prefers keyboard-first and quiet review surfaces.")
        .expect("direct observation promoted the composed belief");
    let composed_id = store
        .belief_units(tenant_id)
        .into_iter()
        .find(|unit| unit.source_kind.as_deref() == Some("composition"))
        .expect("composed belief remains as provenance")
        .id;
    assert!(store.memory_edges(tenant_id).iter().any(|edge| {
        edge.src_id == promoted.id
            && edge.dst_id == composed_id
            && edge.kind == MemoryEdgeKind::DerivedFrom
    }));
}

#[tokio::test]
async fn correcting_source_expires_dependent_composed_belief() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(35_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "user",
            trust_level: TrustLevel::TrustedUser,
            subject: "quiet review preference",
            predicate: "value",
            body: "The user prefers quiet review surfaces.",
        },
    )
    .await;
    retain_and_reflect(
        &store,
        &context,
        ReflectSeed {
            source_kind: "system",
            trust_level: TrustLevel::TrustedSystem,
            subject: "keyboard review preference",
            predicate: "value",
            body: "The user prefers keyboard-first review surfaces.",
        },
    )
    .await;

    let units = store.memory_units(tenant_id);
    let source_id = units
        .iter()
        .find(|unit| unit.body == "The user prefers quiet review surfaces.")
        .expect("source unit exists")
        .id;
    let composed_id = units
        .iter()
        .find(|unit| unit.source_kind.as_deref() == Some("composition"))
        .expect("composed unit exists")
        .id;

    correct_memory(
        &store,
        &context,
        CorrectRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            selector: CorrectSelector {
                memory_unit_id: source_id,
            },
            correction: CorrectionPayload {
                value: "The user prefers detailed review surfaces.".to_string(),
                reason: "preference_changed".to_string(),
                source_ref: "test:correction".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                valid_from: None,
                valid_to: None,
            },
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("correction succeeds");

    let expired = store
        .memory_units(tenant_id)
        .into_iter()
        .find(|unit| unit.id == composed_id)
        .expect("composed unit still auditable");
    assert_eq!(expired.state, UnitState::Expired);
    assert_eq!(
        expired.transaction_to.as_deref(),
        Some("2026-07-03T00:00:00Z")
    );
}

#[tokio::test]
async fn two_trusted_retains_with_distinct_content_do_not_supersede() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(36_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    // Two subject-less retains with DISTINCT content: auto content-hash keys
    // never collide and NEVER supersede — both facts stay Active.
    for body in [
        "The staging cluster runs in Frankfurt.",
        "The billing ledger closes on Fridays.",
    ] {
        let retained = retain_episode(
            &store,
            &context,
            RetainRequest {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                actor_id: context.actor_id,
                source_kind: "user".to_string(),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_trust: TrustLevel::TrustedUser,
                subject_hint: None,
                subject: None,
                predicate: None,
                body: body.to_string(),
                compiler_version: "compiler-auto-subject".to_string(),
            },
        )
        .await
        .expect("retain succeeds");
        let job = store
            .reflect_jobs(tenant_id)
            .last()
            .cloned()
            .expect("job queued");
        reflect_recorded(
            &store,
            ReflectInput {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                actor_id: context.actor_id,
                source_ref: "test:reflect".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_body: None,
                episode_id: Some(retained.episode_id),
                resource_id: None,
                job_id: job.id,
                compiler_version: "compiler-auto-subject".to_string(),
                candidates: vec![ReflectCandidate {
                    compact: None,
                    source_kind: "user".to_string(),
                    trust_level: TrustLevel::TrustedUser,
                    actor_id: context.actor_id,
                    subject: job.subject.clone(),
                    predicate: job.predicate.clone(),
                    fact_key: None,
                    kind: None,
                    body: body.to_string(),
                    confidence: None,
                    churn_class: None,
                    admission_hint: None,
                    contextual_chunks: Vec::new(),
                    valid_from: None,
                    valid_to: None,
                    target_unit_ids: None,
                }],
            },
            &NoopEmbedding,
            &CLOCK,
        )
        .await
        .expect("reflect succeeds");
    }

    let active = store.active_semantic_units(tenant_id);
    assert_eq!(active.len(), 2, "distinct content must both stay Active");
    assert!(
        store
            .memory_units(tenant_id)
            .iter()
            .all(|unit| unit.state != UnitState::Superseded),
        "auto content-hash keys must never supersede"
    );
    let keys: Vec<_> = active
        .iter()
        .map(|unit| unit.fact_key.clone().expect("auto key derived"))
        .collect();
    assert!(keys.iter().all(|key| key.contains(":auto:")));
    assert_ne!(keys[0], keys[1], "distinct content yields distinct keys");
}

#[tokio::test]
async fn explicit_subject_updates_supersede_prior_generation() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(37_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    for body in ["Deploy region is Taipei.", "Deploy region is Singapore."] {
        retain_and_reflect(
            &store,
            &context,
            ReflectSeed {
                source_kind: "user",
                trust_level: TrustLevel::TrustedUser,
                subject: "deploy region",
                predicate: "value",
                body,
            },
        )
        .await;
    }

    let units = store.memory_units(tenant_id);
    assert_eq!(units.len(), 3, "the update splits both time axes");
    let superseded = units
        .iter()
        .find(|unit| unit.state == UnitState::Superseded)
        .expect("the prior transaction rectangle is superseded");
    assert_eq!(superseded.body, "Deploy region is Taipei.");
    assert_eq!(superseded.valid_from, None);
    assert_eq!(superseded.valid_to, None);
    assert_eq!(
        superseded.transaction_to.as_deref(),
        Some("2026-07-03T00:00:00Z"),
        "supersedence closes the transaction interval with the injected clock"
    );

    let historical = units
        .iter()
        .find(|unit| unit.state == UnitState::Active && unit.body == "Deploy region is Taipei.")
        .expect("the prior value remains valid before the correction");
    assert_eq!(historical.valid_from, None);
    assert_eq!(historical.valid_to.as_deref(), Some("2026-07-03T00:00:00Z"));
    assert_eq!(historical.transaction_to, None);

    let current = units
        .iter()
        .find(|unit| unit.state == UnitState::Active && unit.body == "Deploy region is Singapore.")
        .expect("the corrected value is current");
    assert_eq!(current.valid_from.as_deref(), Some("2026-07-03T00:00:00Z"));
    assert_eq!(current.valid_to, None);
    assert_eq!(current.transaction_to, None);
}

#[tokio::test]
async fn unit_transaction_from_uses_injected_clock() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(38_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let future_clock = memphant_core::FixedClock("2031-01-01T00:00:00Z");

    let retained = retain_episode(
        &store,
        &context,
        RetainRequest {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_kind: "user".to_string(),
            source_ref: "test:fixture".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: None,
            subject: Some("release train".to_string()),
            predicate: Some("cadence".to_string()),
            body: "Release train ships every second Tuesday.".to_string(),
            compiler_version: "compiler-clock-test".to_string(),
        },
    )
    .await
    .expect("retain succeeds");
    let job = store.reflect_jobs(tenant_id)[0].clone();
    reflect_recorded(
        &store,
        ReflectInput {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_ref: "test:reflect".to_string(),
            observed_at: "2026-07-09T00:00:00Z".to_string(),
            source_body: None,
            episode_id: Some(retained.episode_id),
            resource_id: None,
            job_id: job.id,
            compiler_version: "compiler-clock-test".to_string(),
            candidates: vec![ReflectCandidate {
                compact: None,
                source_kind: "user".to_string(),
                trust_level: TrustLevel::TrustedUser,
                actor_id: context.actor_id,
                subject: Some("release train".to_string()),
                predicate: Some("cadence".to_string()),
                fact_key: None,
                kind: None,
                body: "Release train ships every second Tuesday.".to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                target_unit_ids: None,
            }],
        },
        &NoopEmbedding,
        &future_clock,
    )
    .await
    .expect("reflect succeeds");

    let units = store.memory_units(tenant_id);
    assert_eq!(units.len(), 1);
    assert_eq!(
        units[0].transaction_from.as_deref(),
        Some("2031-01-01T00:00:00Z"),
        "transaction_from must come from the injected clock, not a build-time constant"
    );
}
