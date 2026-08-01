use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, NoopEmbedding};
use memphant_types::{
    MemoryKind, ResolvedMemoryContext, RetainEpisodeHttpRequest, RetainEpisodeHttpResponse,
    RetainPayload, RetainUnitPayload, TenantId, TrustLevel,
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
