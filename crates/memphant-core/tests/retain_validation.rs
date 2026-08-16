use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, NoopEmbedding};
use memphant_core::{JobFilter, MemoryStore};
use memphant_types::{
    MemoryKind, ResolvedMemoryContext, RetainEpisodeHttpRequest, RetainEpisodeHttpResponse,
    RetainEpisodePayload, RetainPayload, RetainUnitPayload, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2030-01-01T00:00:00Z");

fn unit_request(context: &ResolvedMemoryContext) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: " Syndai:Fact:1 ".to_string(),
        observed_at: "2030-01-01T00:00:00+00:00".to_string(),
        payload: RetainPayload::Unit(RetainUnitPayload {
            kind: MemoryKind::Semantic,
            fact_key: Some("profile:city".to_string()),
            subject: None,
            predicate: "lives_in".to_string(),
            body: "The user lives in Lima".to_string(),
            confidence: 0.9,
            valid_from: Some("2029-01-01T00:00:00Z".to_string()),
            valid_to: Some("2031-01-01T00:00:00Z".to_string()),
            target_unit_ids: None,
        }),
    }
}

#[tokio::test]
async fn retain_rejects_invalid_provenance_confidence_and_valid_time() {
    let store = InMemoryStore::default();
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let base = unit_request(&context);
    service
        .retain(
            &context,
            "valid-control",
            TrustLevel::TrustedUser,
            base.clone(),
        )
        .await
        .expect("valid control retain");
    let stored = store
        .memory_units(tenant)
        .into_iter()
        .find(|unit| unit.source_ref == base.source_ref)
        .expect("valid control stored");
    assert_eq!(stored.source_ref, " Syndai:Fact:1 ");
    assert_eq!(stored.observed_at, "2030-01-01T00:00:00Z");
    assert_eq!(stored.confidence, Some(0.9));
    assert_eq!(stored.predicate.as_deref(), Some("lives_in"));

    let mut short = base.clone();
    short.source_ref = "short-direct-unit".to_string();
    let RetainPayload::Unit(unit) = &mut short.payload else {
        unreachable!()
    };
    unit.fact_key = Some("profile:greeting".to_string());
    unit.predicate = "states".to_string();
    unit.body = "Hi.".to_string();
    let short_response = service
        .retain(
            &context,
            "valid-short-direct-unit",
            TrustLevel::TrustedUser,
            short,
        )
        .await
        .expect("short explicit direct unit must be admitted");
    let short_result: RetainEpisodeHttpResponse =
        serde_json::from_slice(short_response.body()).unwrap();
    assert_eq!(short_result.unit_ids.len(), 1);

    let mut cases = Vec::new();

    let mut request = base.clone();
    request.source_ref = "  ".to_string();
    cases.push((request, "invalid request: source_ref must not be blank"));
    let mut request = base.clone();
    request.observed_at = "2030-01-01T01:00:00+01:00".to_string();
    cases.push((
        request,
        "invalid request: observed_at must use a UTC offset",
    ));
    let mut request = base.clone();
    request.observed_at = "not-a-time+00:00".to_string();
    cases.push((request, "invalid request: observed_at must be RFC3339"));
    // A blank observed_at (what a capture adapter posts when it forgets to
    // stamp the episode) is rejected, never defaulted server-side.
    let mut request = base.clone();
    request.observed_at = String::new();
    cases.push((
        request,
        "invalid request: observed_at must use a UTC offset",
    ));
    for confidence in [f32::NAN, -0.1, 1.1] {
        let mut request = base.clone();
        let RetainPayload::Unit(unit) = &mut request.payload else {
            unreachable!()
        };
        unit.confidence = confidence;
        cases.push((
            request,
            "invalid request: unit confidence must be finite and between 0 and 1",
        ));
    }
    let mut request = base.clone();
    let RetainPayload::Unit(unit) = &mut request.payload else {
        unreachable!()
    };
    unit.fact_key = Some(" ".to_string());
    cases.push((
        request,
        "invalid request: unit retain requires a predicate and either a subject or a fact_key",
    ));
    // D1: a blank subject is no more a key than a blank fact_key.
    let mut request = base.clone();
    let RetainPayload::Unit(unit) = &mut request.payload else {
        unreachable!()
    };
    unit.fact_key = None;
    unit.subject = Some("   ".to_string());
    cases.push((
        request,
        "invalid request: unit retain requires a predicate and either a subject or a fact_key",
    ));
    let mut request = base.clone();
    let RetainPayload::Unit(unit) = &mut request.payload else {
        unreachable!()
    };
    unit.body = "  ".to_string();
    cases.push((request, "retain body cannot be empty"));
    let mut request = base.clone();
    let RetainPayload::Unit(unit) = &mut request.payload else {
        unreachable!()
    };
    unit.valid_from = Some("2032-01-01T00:00:00Z".to_string());
    cases.push((
        request,
        "invalid request: valid_from must be before valid_to",
    ));
    let mut request = base;
    let RetainPayload::Unit(unit) = &mut request.payload else {
        unreachable!()
    };
    unit.valid_from = Some("not-a-time".to_string());
    cases.push((request, "invalid request: valid_from must be RFC3339"));

    for (request, expected) in cases {
        let error = service
            .retain(
                &context,
                "invalid-control",
                TrustLevel::TrustedUser,
                request,
            )
            .await
            .expect_err("invalid retain must fail at validation");
        assert_eq!(
            error.to_string(),
            expected,
            "validation must fail before context/store lookup"
        );
    }
}

/// Trust FLOOR by source kind: an `agent`-authored episode retained under a
/// TRUSTED-SYSTEM key still lands at `AgentOutput` → `Candidate`. CONTROL: the
/// same episode with `source_kind = "system"` keeps the key's trust and mints
/// `Active` — the clamp is the source kind's doing, not the key's.
#[tokio::test]
async fn retain_clamps_episode_trust_to_its_source_kind() {
    let store = InMemoryStore::default();
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let episode = |source_kind: &str, source_ref: &str| RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: source_ref.to_string(),
        observed_at: "2030-01-01T00:00:00Z".to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: source_kind.to_string(),
            body: "The worker restarts every night at two.".to_string(),
            subject: Some("worker restart".to_string()),
            predicate: Some("schedule".to_string()),
        }),
    };
    for (key, source_kind, source_ref) in [
        ("agent-under-system-key", "agent", "capture://summary"),
        ("system-under-system-key", "system", "ops:cron"),
    ] {
        let response = service
            .retain(
                &context,
                key,
                TrustLevel::TrustedSystem,
                episode(source_kind, source_ref),
            )
            .await
            .expect("retain episode");
        let result: RetainEpisodeHttpResponse = serde_json::from_slice(response.body()).unwrap();
        let expected = if source_kind == "agent" {
            TrustLevel::AgentOutput
        } else {
            TrustLevel::TrustedSystem
        };
        assert_eq!(result.assigned_trust, Some(expected), "{source_kind}");
    }
    let outcome = service
        .run_worker_tick_scoped(
            JobFilter {
                tenant: Some(tenant),
                scope: Some(context.scope_id),
            },
            8,
        )
        .await
        .expect("worker tick");
    assert_eq!(outcome.failed, 0);
    let units = store
        .fetch_scope_open_units(&context)
        .await
        .expect("open units");
    let captured = units
        .iter()
        .find(|unit| unit.capture.is_some())
        .expect("captured unit");
    assert_eq!(captured.trust_level, TrustLevel::AgentOutput);
    assert_eq!(
        captured.state,
        UnitState::Candidate,
        "agent-authored capture is clamped below the key: {captured:?}"
    );
    let system = units
        .iter()
        .find(|unit| unit.source_ref == "ops:cron")
        .expect("system unit");
    assert_eq!(system.trust_level, TrustLevel::TrustedSystem);
    assert_eq!(system.state, UnitState::Active, "control: {system:?}");
}
