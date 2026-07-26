use std::collections::{BTreeMap, VecDeque};
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};

use memphant_core::service::MemoryService;
use memphant_core::{
    ActiveStructuredState, EvidenceSlice, FixedClock, InMemoryStore, MemoryStore, NoopEmbedding,
    StructuredObservation, StructuredObservationDisposition, StructuredSourceKind,
    StructuredStateOp, StructuredStateOperation, StructuredStateProvider,
    StructuredStateProviderError, StructuredStateProviderIdentity, StructuredStateRequest,
    derive_fact_key, evidence_slices_for_episode, evidence_slices_for_resource,
    fold_structured_observations, project_structured_state as project_source_state,
};
use memphant_types::{
    EpisodeId, MemoryKind, RecallHttpRequest, ResolvedMemoryContext, RetainEpisodeHttpRequest,
    TenantId, TrustLevel, UnitState,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

fn sha256(body: &str) -> String {
    Sha256::digest(body.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn neutral_slice_id(kind: &str, span: &str, body: &str) -> String {
    let body_sha = sha256(body);
    let mut hasher = Sha256::new();
    for component in [kind, span, body_sha.as_str()] {
        hasher.update((component.len() as u64).to_be_bytes());
        hasher.update(component.as_bytes());
    }
    format!("slice-{:x}", hasher.finalize())
}

fn observation(
    namespace: &str,
    item_key: &str,
    fields: BTreeMap<String, Value>,
    disposition: StructuredObservationDisposition,
    slice: &EvidenceSlice,
    quote: &str,
) -> StructuredObservation {
    StructuredObservation {
        namespace: namespace.to_string(),
        item_key: item_key.to_string(),
        fields,
        disposition,
        evidence_slice_id: slice.id.clone(),
        evidence_quote: quote.to_string(),
        valid_from: None,
        valid_to: None,
    }
}

#[test]
fn resource_slices_are_exact_source_neutral_and_fall_back_to_the_short_body() {
    let body = "deploy notes";
    let resource_id = memphant_types::ResourceId::from_u128(7);
    let request = StructuredStateRequest {
        source_kind: StructuredSourceKind::Resource,
        source_body_sha256: sha256(body),
        batch_index: 0,
        evidence_slices: evidence_slices_for_resource(body, &[]).unwrap(),
    };

    assert_eq!(request.evidence_slices[0].source_span, "0-12");
    assert_eq!(request.evidence_slices[0].body, body);
    assert!(
        !request.evidence_slices[0]
            .id
            .contains(resource_id.as_uuid().to_string().as_str())
    );

    let folded = fold_structured_observations(
        body,
        &request,
        &[observation(
            "deployment",
            "notes",
            fields(&[("value", json!("deploy notes"))]),
            StructuredObservationDisposition::State,
            &request.evidence_slices[0],
            body,
        )],
        &[],
    )
    .unwrap();
    assert_eq!(folded[0].source_span, "0-12");
    assert_eq!(folded[0].evidence_quote, body);
}

#[test]
fn episode_slices_admit_only_exact_user_and_user_agent_quotes() {
    let body = "system: hidden\nuser: keep me\nassistant: not me\nuser_agent: keep me too";
    let slices = evidence_slices_for_episode(body).unwrap();
    assert_eq!(slices.len(), 2);
    let request = StructuredStateRequest {
        source_kind: StructuredSourceKind::Episode,
        source_body_sha256: sha256(body),
        batch_index: 0,
        evidence_slices: slices,
    };
    let rejected = observation(
        "profile",
        "instruction",
        fields(&[("value", json!("not me"))]),
        StructuredObservationDisposition::State,
        &request.evidence_slices[0],
        "not me",
    );

    assert!(fold_structured_observations(body, &request, &[rejected], &[]).is_err());

    let assistant_quote = "not me";
    let assistant_start = body.find(assistant_quote).unwrap();
    let assistant_span = format!(
        "{assistant_start}-{}",
        assistant_start + assistant_quote.len()
    );
    let forged_slice = EvidenceSlice {
        id: neutral_slice_id("episode", &assistant_span, assistant_quote),
        body: assistant_quote.to_string(),
        source_span: assistant_span,
    };
    let forged_request = StructuredStateRequest {
        source_kind: StructuredSourceKind::Episode,
        source_body_sha256: sha256(body),
        batch_index: 0,
        evidence_slices: vec![forged_slice.clone()],
    };
    let forged = observation(
        "profile",
        "instruction",
        fields(&[("value", json!(assistant_quote))]),
        StructuredObservationDisposition::State,
        &forged_slice,
        assistant_quote,
    );
    assert!(fold_structured_observations(body, &forged_request, &[forged], &[]).is_err());
}

#[test]
fn neutral_slice_identity_is_stable_across_local_source_ids() {
    let body = "same resource";
    let first_local_id = memphant_types::ResourceId::from_u128(1);
    let second_local_id = memphant_types::ResourceId::from_u128(2);
    let first = evidence_slices_for_resource(body, &[]).unwrap();
    let second = evidence_slices_for_resource(body, &[]).unwrap();

    assert_ne!(first_local_id, second_local_id);
    assert_eq!(first, second);
    assert!(!first[0].id.contains(&first_local_id.as_uuid().to_string()));
    assert!(!first[0].id.contains(&second_local_id.as_uuid().to_string()));
}

#[test]
fn fold_rejects_slice_substitution_span_shift_quote_mismatch_and_utf8_breaks() {
    let body = "évidence exact";
    let slices = evidence_slices_for_resource(body, &[]).unwrap();
    let observation = observation(
        "proof",
        "text",
        fields(&[("value", json!("exact"))]),
        StructuredObservationDisposition::State,
        &slices[0],
        "exact",
    );
    let request = |evidence_slices| StructuredStateRequest {
        source_kind: StructuredSourceKind::Resource,
        source_body_sha256: sha256(body),
        batch_index: 0,
        evidence_slices,
    };

    let mut substituted = slices.clone();
    substituted[0].body = "different".to_string();
    assert!(
        fold_structured_observations(
            body,
            &request(substituted),
            std::slice::from_ref(&observation),
            &[],
        )
        .is_err()
    );

    let mut shifted = slices.clone();
    shifted[0].source_span = format!("2-{}", body.len());
    assert!(
        fold_structured_observations(
            body,
            &request(shifted),
            std::slice::from_ref(&observation),
            &[],
        )
        .is_err()
    );

    let mut mismatched = observation.clone();
    mismatched.evidence_quote = "absent".to_string();
    assert!(
        fold_structured_observations(body, &request(slices.clone()), &[mismatched], &[]).is_err()
    );

    let mut invalid_utf8 = slices;
    invalid_utf8[0].source_span = format!("1-{}", body.len());
    assert!(
        fold_structured_observations(body, &request(invalid_utf8), &[observation], &[]).is_err()
    );
}

#[test]
fn ordered_fold_keeps_only_the_last_state_and_resolves_fresh_targets_once() {
    let body = "city Oslo. city Bergen. walked 12 steps.";
    let request = StructuredStateRequest {
        source_kind: StructuredSourceKind::Resource,
        source_body_sha256: sha256(body),
        batch_index: 0,
        evidence_slices: evidence_slices_for_resource(body, &[]).unwrap(),
    };
    let slice = &request.evidence_slices[0];
    let active = ActiveStructuredState {
        unit_id: memphant_types::UnitId::from_u128(9),
        namespace: "profile".to_string(),
        item_key: "city".to_string(),
        fields: fields(&[("value", json!("Paris"))]),
        valid_from: None,
        valid_to: None,
    };
    let observations = vec![
        observation(
            "profile",
            "city",
            fields(&[("value", json!("Oslo"))]),
            StructuredObservationDisposition::State,
            slice,
            "city Oslo.",
        ),
        observation(
            "profile",
            "city",
            fields(&[("value", json!("Bergen"))]),
            StructuredObservationDisposition::State,
            slice,
            "city Bergen.",
        ),
        observation(
            "steps",
            "daily",
            fields(&[("count", json!(12))]),
            StructuredObservationDisposition::Event,
            slice,
            "walked 12 steps.",
        ),
    ];

    let folded = fold_structured_observations(body, &request, &observations, &[active]).unwrap();
    assert_eq!(folded.len(), 2);
    assert_eq!(folded[0].operation, StructuredStateOperation::Replace);
    assert_eq!(folded[0].fields["value"], "Bergen");
    assert_eq!(
        folded[0].target_unit_ids,
        [memphant_types::UnitId::from_u128(9)]
    );
    assert_eq!(folded[1].operation, StructuredStateOperation::Append);
    assert!(folded[1].target_unit_ids.is_empty());
}

#[test]
fn quantity_fold_requires_the_exact_numeric_lexeme_with_only_comma_normalization() {
    let body = "[date 2025-06-01]\nuser: I walked 7,640 steps.";
    let request = StructuredStateRequest {
        source_kind: StructuredSourceKind::Episode,
        source_body_sha256: sha256(body),
        batch_index: 0,
        evidence_slices: evidence_slices_for_episode(body).unwrap(),
    };
    let quantity_fields = |value: &str| {
        BTreeMap::from([
            ("dimensions".to_string(), json!({})),
            ("measure".to_string(), json!("daily_steps")),
            ("occurred_at".to_string(), json!("2025-06-01T08:00:00Z")),
            ("type".to_string(), json!("quantity_event.v1")),
            ("unit".to_string(), json!("steps")),
            ("value".to_string(), json!(value)),
        ])
    };
    let event = |value: &str| {
        observation(
            "activity",
            "daily_steps",
            quantity_fields(value),
            StructuredObservationDisposition::Event,
            &request.evidence_slices[0],
            "I walked 7,640 steps.",
        )
    };

    assert_eq!(
        fold_structured_observations(body, &request, &[event("7640")], &[])
            .unwrap()
            .len(),
        1
    );
    for absent in ["7641", "15280", "1e3"] {
        assert!(
            fold_structured_observations(body, &request, &[event(absent)], &[]).is_err(),
            "ungrounded quantity {absent} must fail closed"
        );
    }
}

#[test]
fn trusted_fold_rejects_reserved_and_noncanonical_observation_field_keys() {
    let body = "deploy notes";
    let request = StructuredStateRequest {
        source_kind: StructuredSourceKind::Resource,
        source_body_sha256: sha256(body),
        batch_index: 0,
        evidence_slices: evidence_slices_for_resource(body, &[]).unwrap(),
    };
    for key in [
        "operation",
        "target_unit_ids",
        "source_span",
        "namespace",
        "item_key",
        "valid_from",
        "valid_to",
        " ",
        "CamelCase",
        "two words",
    ] {
        let invalid = observation(
            "workflow",
            "deploy",
            BTreeMap::from([(key.to_string(), json!("untrusted"))]),
            StructuredObservationDisposition::State,
            &request.evidence_slices[0],
            body,
        );
        assert!(
            fold_structured_observations(body, &request, &[invalid], &[]).is_err(),
            "field key {key:?} must fail closed"
        );
    }
}

#[test]
fn append_predicate_binds_source_kind_local_id_and_canonical_span() {
    let body = "deploy notes";
    let operation = StructuredStateOp {
        operation: StructuredStateOperation::Append,
        namespace: "workflow".to_string(),
        item_key: "deploy".to_string(),
        target_unit_ids: vec![],
        fields: fields(&[("value", json!("done"))]),
        evidence_quote: body.to_string(),
        source_span: "0-12".to_string(),
        valid_from: None,
        valid_to: None,
    };
    let local_id = "00000000-0000-0000-0000-000000000007";
    let resource = project_source_state(
        StructuredSourceKind::Resource,
        local_id,
        body,
        std::slice::from_ref(&operation),
    )
    .unwrap();
    let episode = project_source_state(
        StructuredSourceKind::Episode,
        local_id,
        "user: deploy notes",
        &[StructuredStateOp {
            evidence_quote: body.to_string(),
            source_span: "6-18".to_string(),
            ..operation
        }],
    )
    .unwrap();

    assert_eq!(
        resource[0].predicate,
        "deploy@resource:00000000-0000-0000-0000-000000000007:0-12"
    );
    assert_eq!(
        episode[0].predicate,
        "deploy@episode:00000000-0000-0000-0000-000000000007:6-18"
    );
}

#[derive(Debug)]
struct FakeProvider {
    identity: StructuredStateProviderIdentity,
    responses: Mutex<VecDeque<Result<Vec<StructuredStateOp>, StructuredStateProviderError>>>,
}

fn project_structured_state(
    episode_id: EpisodeId,
    body: &str,
    operations: &[StructuredStateOp],
) -> Result<Vec<memphant_core::ProjectedStructuredState>, StructuredStateProviderError> {
    project_source_state(
        StructuredSourceKind::Episode,
        &episode_id.as_uuid().to_string(),
        body,
        operations,
    )
}

#[test]
fn quantity_event_requires_strict_fields_and_grounded_occurrence_date() {
    let body = "[date 2025-06-01]\nuser: I spent $8.48 on coffee.\n";
    let quote = "I spent $8.48 on coffee.";
    let start = body.find(quote).unwrap();
    let fields = BTreeMap::from([
        ("dimensions".to_string(), json!({"expense_type": "coffee"})),
        ("measure".to_string(), json!("food_spending")),
        ("occurred_at".to_string(), json!("2025-06-01T08:00:00Z")),
        ("type".to_string(), json!("quantity_event.v1")),
        ("unit".to_string(), json!("usd")),
        ("value".to_string(), json!("8.48")),
    ]);
    let operation = StructuredStateOp {
        operation: StructuredStateOperation::Append,
        namespace: "activity".to_string(),
        item_key: "expenses".to_string(),
        target_unit_ids: Vec::new(),
        fields: fields.clone(),
        evidence_quote: quote.to_string(),
        source_span: format!("{start}-{}", start + quote.len()),
        valid_from: None,
        valid_to: None,
    };
    assert_eq!(
        project_structured_state(EpisodeId::new(), body, std::slice::from_ref(&operation))
            .unwrap()
            .len(),
        1
    );

    let mut mismatched = operation.clone();
    mismatched
        .fields
        .insert("occurred_at".to_string(), json!("2025-06-02T08:00:00Z"));
    let error = project_structured_state(EpisodeId::new(), body, &[mismatched]).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("quantity occurrence date is not grounded"),
        "{error}"
    );

    let mut invalid_decimal = operation;
    invalid_decimal
        .fields
        .insert("value".to_string(), json!(8.48));
    let error = project_structured_state(EpisodeId::new(), body, &[invalid_decimal]).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("quantity fields violate the canonical contract"),
        "{error}"
    );
}

#[test]
fn invalid_evidence_span_is_a_provider_error_instead_of_silent_data_loss() {
    let body = "[date 2025-06-01]\nuser: I spent $3.41 on coffee.\n";
    let operation = StructuredStateOp {
        operation: StructuredStateOperation::Append,
        namespace: "quantity_event.v1".to_string(),
        item_key: "food_spending".to_string(),
        target_unit_ids: Vec::new(),
        fields: BTreeMap::from([
            ("dimensions".to_string(), json!({"expense_type": "coffee"})),
            ("measure".to_string(), json!("food_spending")),
            ("occurred_at".to_string(), json!("2025-06-01T00:00:00Z")),
            ("type".to_string(), json!("quantity_event.v1")),
            ("unit".to_string(), json!("usd")),
            ("value".to_string(), json!("3.41")),
        ]),
        evidence_quote: "I spent $3.41 on coffee.".to_string(),
        source_span: "0-10".to_string(),
        valid_from: None,
        valid_to: None,
    };

    let error = project_structured_state(EpisodeId::new(), body, &[operation]).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("evidence span is not an exact user quote"),
        "{error}"
    );
}

#[test]
fn duplicate_state_identity_fails_before_the_store_transaction() {
    let body = "user: agenda update\nuser: another agenda update\n";
    let operation = |quote: &str| {
        let start = body.find(quote).unwrap();
        StructuredStateOp {
            operation: StructuredStateOperation::Create,
            namespace: "meeting_notes_accessibility".to_string(),
            item_key: "agenda_items".to_string(),
            target_unit_ids: Vec::new(),
            fields: BTreeMap::from([("value".to_string(), json!(quote))]),
            evidence_quote: quote.to_string(),
            source_span: format!("{start}-{}", start + quote.len()),
            valid_from: None,
            valid_to: None,
        }
    };
    let error = project_structured_state(
        EpisodeId::new(),
        body,
        &[
            operation("agenda update"),
            operation("another agenda update"),
        ],
    )
    .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("duplicate structured-state identity")
    );
}

impl FakeProvider {
    fn new(responses: Vec<Vec<StructuredStateOp>>) -> Self {
        Self {
            identity: StructuredStateProviderIdentity {
                model: "fake-state-model-v1".to_string(),
                prompt_hash: "prompt-sha256".to_string(),
                schema_hash: "schema-sha256".to_string(),
            },
            responses: Mutex::new(responses.into_iter().map(Ok).collect()),
        }
    }

    fn failing() -> Self {
        Self {
            identity: StructuredStateProviderIdentity {
                model: "fake-state-model-v1".to_string(),
                prompt_hash: "prompt-sha256".to_string(),
                schema_hash: "schema-sha256".to_string(),
            },
            responses: Mutex::new(VecDeque::from([Err(
                StructuredStateProviderError::Unavailable("offline".to_string()),
            )])),
        }
    }
}

impl StructuredStateProvider for FakeProvider {
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
        let response = self
            .responses
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| Ok(Vec::new()))
            .map(|operations| {
                operations
                    .into_iter()
                    .map(|operation| {
                        let evidence_slice_id = request
                            .evidence_slices
                            .iter()
                            .find(|slice| slice.body.contains(&operation.evidence_quote))
                            .map_or_else(|| "unknown-slice".to_string(), |slice| slice.id.clone());
                        StructuredObservation {
                            namespace: operation.namespace,
                            item_key: operation.item_key,
                            fields: operation.fields,
                            disposition: if operation.operation == StructuredStateOperation::Append
                            {
                                StructuredObservationDisposition::Event
                            } else {
                                StructuredObservationDisposition::State
                            },
                            evidence_slice_id,
                            evidence_quote: operation.evidence_quote,
                            valid_from: operation.valid_from,
                            valid_to: operation.valid_to,
                        }
                    })
                    .collect()
            });
        Box::pin(async move { response })
    }
}

fn fields(entries: &[(&str, Value)]) -> BTreeMap<String, Value> {
    entries
        .iter()
        .map(|(key, value)| ((*key).to_string(), value.clone()))
        .collect()
}

fn op(
    operation: StructuredStateOperation,
    namespace: &str,
    item_key: &str,
    fields: BTreeMap<String, Value>,
    body: &str,
    quote: &str,
) -> StructuredStateOp {
    let start = body.find(quote).expect("quote exists");
    StructuredStateOp {
        operation,
        namespace: namespace.to_string(),
        item_key: item_key.to_string(),
        target_unit_ids: Vec::new(),
        fields,
        evidence_quote: quote.to_string(),
        source_span: format!("{start}-{}", start + quote.len()),
        valid_from: None,
        valid_to: None,
    }
}

fn service(
    store: InMemoryStore,
    clock: &'static str,
    responses: Vec<Vec<StructuredStateOp>>,
) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store),
        Arc::new(FixedClock(clock)),
        Arc::new(NoopEmbedding),
    )
    .with_structured_state_provider(Arc::new(FakeProvider::new(responses)))
}

fn retain_request(context: &ResolvedMemoryContext, body: &str) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "test:fixture".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        payload: memphant_types::RetainPayload::Episode(memphant_types::RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
        }),
    }
}

async fn retain_and_reflect(
    service: &MemoryService<InMemoryStore>,
    context: &ResolvedMemoryContext,
    body: &str,
) {
    // Each call is its own logical retain, so the idempotency key must vary
    // per call (not just per call SITE): this helper is shared across tests
    // that retain several distinct bodies against the same bound context, and
    // a fixed `concat!("test:", line!())` key would collide across those
    // calls, producing an `IdempotencyConflict` once the request hash (which
    // includes `body`) diverges from the first call's cached claim.
    service
        .retain(
            context,
            &format!("test:{body}"),
            TrustLevel::TrustedUser,
            retain_request(context, body),
        )
        .await
        .unwrap();
    service.run_worker_tick(usize::MAX).await.unwrap();
}

#[tokio::test]
async fn episode_job_keeps_raw_episode_episodic_and_projects_state_as_semantic() {
    let body = "user: Add book dentist to my todo list.";
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let service = service(
        store.clone(),
        "2026-07-13T10:00:00Z",
        vec![vec![op(
            StructuredStateOperation::Create,
            "todo",
            "dentist",
            fields(&[("title", json!("Book dentist"))]),
            body,
            "Add book dentist to my todo list.",
        )]],
    );

    retain_and_reflect(&service, &context, body).await;

    let units = store.memory_units(tenant);
    let raw = units.iter().find(|unit| unit.body == body).unwrap();
    assert_eq!(raw.kind, MemoryKind::Episodic);

    let key = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("todo"),
        Some("dentist"),
        "",
    );
    let projected = units
        .iter()
        .find(|unit| unit.fact_key.as_deref() == Some(&key))
        .unwrap();
    assert_eq!(projected.kind, MemoryKind::Semantic);
}

#[tokio::test]
async fn todo_upsert_update_and_delete_reuse_supersedence_and_invalidation() {
    let first = "user: Add book dentist to my todo list.";
    let second = "user: Change dentist todo priority to high.";
    let third = "user: Remove the dentist todo from my list.";
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let first_service = service(
        store.clone(),
        "2026-07-13T10:00:00Z",
        vec![vec![op(
            StructuredStateOperation::Create,
            "todo",
            "dentist",
            fields(&[
                ("title", json!("Book dentist")),
                ("priority", json!("normal")),
            ]),
            first,
            "Add book dentist to my todo list.",
        )]],
    );
    let second_service = service(
        store.clone(),
        "2026-07-13T11:00:00Z",
        vec![vec![op(
            StructuredStateOperation::Create,
            "todo",
            "dentist",
            fields(&[
                ("title", json!("Book dentist")),
                ("priority", json!("high")),
            ]),
            second,
            "Change dentist todo priority to high.",
        )]],
    );
    let third_service = service(
        store.clone(),
        "2026-07-13T12:00:00Z",
        vec![vec![op(
            StructuredStateOperation::Delete,
            "todo",
            "dentist",
            BTreeMap::new(),
            third,
            "Remove the dentist todo from my list.",
        )]],
    );

    retain_and_reflect(&first_service, &context, first).await;
    retain_and_reflect(&second_service, &context, second).await;

    let key = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("todo"),
        Some("dentist"),
        "",
    );
    let versions: Vec<_> = store
        .memory_units(tenant)
        .into_iter()
        .filter(|unit| unit.fact_key.as_deref() == Some(&key))
        .collect();
    assert!(versions.len() >= 2, "bitemporal generations: {versions:?}");
    assert!(
        versions
            .iter()
            .any(|unit| unit.state == UnitState::Superseded)
    );
    let current = versions
        .iter()
        .find(|unit| unit.state == UnitState::Active && unit.valid_to.is_none())
        .unwrap();
    assert_eq!(
        current.body,
        "todo item dentist: {\"priority\":\"high\",\"title\":\"Book dentist\"}"
    );
    assert_eq!(
        current.source_episode_id,
        store.episodes(tenant).last().map(|e| e.id)
    );
    assert_eq!(
        current.contextual_chunks[0].body,
        "Change dentist todo priority to high."
    );

    retain_and_reflect(&third_service, &context, third).await;
    assert!(
        store
            .memory_units(tenant)
            .iter()
            .filter(|unit| unit.fact_key.as_deref() == Some(&key))
            .all(|unit| unit.state != UnitState::Active || unit.valid_to.is_some()),
        "delete invalidates the open structured item"
    );
}

#[tokio::test]
async fn memora_removal_trajectory_preserves_siblings_and_recreates_once() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let steps = [
        (
            "user: Email recipients are Embedded Software Team and Head of Engineering.",
            vec![op(
                StructuredStateOperation::Create,
                "architecture_email",
                "recipients",
                fields(&[(
                    "value",
                    json!("Embedded Software Team; Head of Engineering"),
                )]),
                "user: Email recipients are Embedded Software Team and Head of Engineering.",
                "Email recipients are Embedded Software Team and Head of Engineering.",
            )],
        ),
        (
            "user: Remove Head of Engineering; keep Embedded Software Team as recipient.",
            vec![op(
                StructuredStateOperation::Create,
                "architecture_email",
                "recipients",
                fields(&[("value", json!("Embedded Software Team"))]),
                "user: Remove Head of Engineering; keep Embedded Software Team as recipient.",
                "Remove Head of Engineering; keep Embedded Software Team as recipient.",
            )],
        ),
        (
            "user: Proposal objectives are driver prediction and cost reduction; stakeholders are Product Management, QA, Data, and Leadership.",
            vec![
                op(
                    StructuredStateOperation::Create,
                    "project_proposal",
                    "objectives",
                    fields(&[("value", json!("driver prediction; cost reduction"))]),
                    "user: Proposal objectives are driver prediction and cost reduction; stakeholders are Product Management, QA, Data, and Leadership.",
                    "Proposal objectives are driver prediction and cost reduction; stakeholders are Product Management, QA, Data, and Leadership.",
                ),
                op(
                    StructuredStateOperation::Create,
                    "project_proposal",
                    "stakeholders",
                    fields(&[("value", json!("Product Management; QA; Data; Leadership"))]),
                    "user: Proposal objectives are driver prediction and cost reduction; stakeholders are Product Management, QA, Data, and Leadership.",
                    "Proposal objectives are driver prediction and cost reduction; stakeholders are Product Management, QA, Data, and Leadership.",
                ),
            ],
        ),
        (
            "user: Remove driver prediction and Product Management; keep cost reduction and QA, Data, and Leadership.",
            vec![
                op(
                    StructuredStateOperation::Create,
                    "project_proposal",
                    "objectives",
                    fields(&[("value", json!("cost reduction"))]),
                    "user: Remove driver prediction and Product Management; keep cost reduction and QA, Data, and Leadership.",
                    "Remove driver prediction and Product Management; keep cost reduction and QA, Data, and Leadership.",
                ),
                op(
                    StructuredStateOperation::Create,
                    "project_proposal",
                    "stakeholders",
                    fields(&[("value", json!("QA; Data; Leadership"))]),
                    "user: Remove driver prediction and Product Management; keep cost reduction and QA, Data, and Leadership.",
                    "Remove driver prediction and Product Management; keep cost reduction and QA, Data, and Leadership.",
                ),
            ],
        ),
        (
            "user: Meeting agenda is API latency; actions belong to Yuki, Lars, and Priya.",
            vec![
                op(
                    StructuredStateOperation::Create,
                    "accessibility_meeting",
                    "agenda",
                    fields(&[("value", json!("API latency"))]),
                    "user: Meeting agenda is API latency; actions belong to Yuki, Lars, and Priya.",
                    "Meeting agenda is API latency; actions belong to Yuki, Lars, and Priya.",
                ),
                op(
                    StructuredStateOperation::Create,
                    "accessibility_meeting",
                    "action_items",
                    fields(&[("value", json!("Yuki; Lars; Priya"))]),
                    "user: Meeting agenda is API latency; actions belong to Yuki, Lars, and Priya.",
                    "Meeting agenda is API latency; actions belong to Yuki, Lars, and Priya.",
                ),
            ],
        ),
        (
            "user: Remove the agenda and Yuki action; retain Lars and Priya.",
            vec![
                op(
                    StructuredStateOperation::Delete,
                    "accessibility_meeting",
                    "agenda",
                    BTreeMap::new(),
                    "user: Remove the agenda and Yuki action; retain Lars and Priya.",
                    "Remove the agenda and Yuki action; retain Lars and Priya.",
                ),
                op(
                    StructuredStateOperation::Create,
                    "accessibility_meeting",
                    "action_items",
                    fields(&[("value", json!("Lars; Priya"))]),
                    "user: Remove the agenda and Yuki action; retain Lars and Priya.",
                    "Remove the agenda and Yuki action; retain Lars and Priya.",
                ),
            ],
        ),
        (
            "user: Todos are buy groceries and refactor legacy code due today.",
            vec![
                op(
                    StructuredStateOperation::Create,
                    "todos",
                    "buy_groceries",
                    fields(&[("status", json!("pending"))]),
                    "user: Todos are buy groceries and refactor legacy code due today.",
                    "Todos are buy groceries and refactor legacy code due today.",
                ),
                op(
                    StructuredStateOperation::Create,
                    "todos",
                    "legacy_refactor_task",
                    fields(&[("title", json!("Refactor legacy code"))]),
                    "user: Todos are buy groceries and refactor legacy code due today.",
                    "Todos are buy groceries and refactor legacy code due today.",
                ),
                op(
                    StructuredStateOperation::Create,
                    "todos",
                    "legacy_refactor_due",
                    fields(&[("value", json!("today"))]),
                    "user: Todos are buy groceries and refactor legacy code due today.",
                    "Todos are buy groceries and refactor legacy code due today.",
                ),
            ],
        ),
        (
            "user: Delete buy groceries and the entire legacy refactor todo.",
            [
                "buy_groceries",
                "legacy_refactor_task",
                "legacy_refactor_due",
            ]
            .into_iter()
            .map(|key| {
                op(
                    StructuredStateOperation::Delete,
                    "todos",
                    key,
                    BTreeMap::new(),
                    "user: Delete buy groceries and the entire legacy refactor todo.",
                    "Delete buy groceries and the entire legacy refactor todo.",
                )
            })
            .collect(),
        ),
        (
            "user: Email latency is 340 milliseconds.",
            vec![op(
                StructuredStateOperation::Create,
                "architecture_email",
                "latency",
                fields(&[("value", json!("340 milliseconds"))]),
                "user: Email latency is 340 milliseconds.",
                "Email latency is 340 milliseconds.",
            )],
        ),
        (
            "user: Delete the email latency figure.",
            vec![op(
                StructuredStateOperation::Delete,
                "architecture_email",
                "latency",
                BTreeMap::new(),
                "user: Delete the email latency figure.",
                "Delete the email latency figure.",
            )],
        ),
        (
            "user: Email latency is now 120 milliseconds.",
            vec![op(
                StructuredStateOperation::Create,
                "architecture_email",
                "latency",
                fields(&[("value", json!("120 milliseconds"))]),
                "user: Email latency is now 120 milliseconds.",
                "Email latency is now 120 milliseconds.",
            )],
        ),
    ];
    let service = service(
        store.clone(),
        "2026-07-14T12:00:00Z",
        steps
            .iter()
            .map(|(_, operations)| operations.clone())
            .collect(),
    );
    for (body, _) in &steps {
        retain_and_reflect(&service, &context, body).await;
    }

    let current = store
        .memory_units(tenant)
        .iter()
        .filter_map(memphant_core::active_structured_state)
        .filter(|item| item.valid_to.is_none())
        .collect::<Vec<_>>();
    let rendered = serde_json::to_string(&current).unwrap();
    for present in [
        "Embedded Software Team",
        "cost reduction",
        "QA; Data; Leadership",
        "Lars; Priya",
        "120 milliseconds",
    ] {
        assert!(rendered.contains(present), "missing {present}: {rendered}");
    }
    for absent in [
        "Head of Engineering",
        "driver prediction",
        "Product Management",
        "Yuki",
        "API latency",
        "buy_groceries",
        "legacy_refactor_task",
        "legacy_refactor_due",
        "340 milliseconds",
    ] {
        assert!(!rendered.contains(absent), "stale {absent}: {rendered}");
    }
    assert_eq!(
        current
            .iter()
            .filter(|item| { item.namespace == "architecture_email" && item.item_key == "latency" })
            .count(),
        1,
        "delete-then-recreate must have one current generation"
    );
}

#[tokio::test]
async fn numeric_food_and_steps_append_as_distinct_cited_observations() {
    let body = "user: Lunch was 650 calories. I walked 8432 steps.";
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let service = service(
        store.clone(),
        "2026-07-13T11:00:00Z",
        vec![vec![
            op(
                StructuredStateOperation::Append,
                "food_log",
                "lunch",
                fields(&[("calories", json!(650))]),
                body,
                "Lunch was 650 calories.",
            ),
            op(
                StructuredStateOperation::Append,
                "steps",
                "daily",
                fields(&[("count", json!(8432))]),
                body,
                "I walked 8432 steps.",
            ),
        ]],
    );

    retain_and_reflect(&service, &context, body).await;
    let episode = store.episodes(tenant)[0].id;
    let projected: Vec<_> = store
        .memory_units(tenant)
        .into_iter()
        .filter(|unit| {
            unit.body.starts_with("food_log item") || unit.body.starts_with("steps item")
        })
        .collect();
    assert_eq!(projected.len(), 2);
    assert!(
        projected
            .iter()
            .all(|unit| unit.source_episode_id == Some(episode))
    );
    assert_ne!(projected[0].fact_key, projected[1].fact_key);
    assert!(projected.iter().any(|unit| unit.body.contains("650")));
    assert!(projected.iter().any(|unit| unit.body.contains("8432")));
}

#[tokio::test]
async fn rejects_wrong_spans_and_assistant_only_evidence() {
    let body = "assistant: The user has a secret yacht.\nuser: I own a bicycle.";
    let assistant_op = op(
        StructuredStateOperation::Create,
        "profile",
        "vehicle",
        fields(&[("value", json!("yacht"))]),
        body,
        "The user has a secret yacht.",
    );
    let mut wrong_span = op(
        StructuredStateOperation::Create,
        "profile",
        "vehicle",
        fields(&[("value", json!("bicycle"))]),
        body,
        "I own a bicycle.",
    );
    wrong_span.source_span = "0-4".to_string();
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let service = service(
        store.clone(),
        "2026-07-13T12:00:00Z",
        vec![vec![assistant_op, wrong_span]],
    );

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, body),
        )
        .await
        .unwrap();
    assert_eq!(service.run_worker_tick(usize::MAX).await.unwrap(), 0);
    assert_eq!(store.dead_letter_count().await.unwrap(), 1);
    assert!(
        store
            .memory_units(tenant)
            .iter()
            .all(|unit| !unit.body.starts_with("profile item")),
        "model output without exact USER evidence cannot become canonical state"
    );
}

#[test]
fn memora_roles_accept_user_agent_and_reject_ai_agent_evidence() {
    let body = "ai_agent: I walked 99999 steps.\nuser_agent: I walked 8432 steps.";
    let accepted = op(
        StructuredStateOperation::Append,
        "steps",
        "daily",
        fields(&[("count", json!(8432))]),
        body,
        "I walked 8432 steps.",
    );
    let rejected = op(
        StructuredStateOperation::Append,
        "steps",
        "daily",
        fields(&[("count", json!(99999))]),
        body,
        "I walked 99999 steps.",
    );
    let projected =
        project_structured_state(memphant_types::EpisodeId::from_u128(43), body, &[accepted])
            .unwrap();
    assert_eq!(projected.len(), 1);
    assert!(projected[0].body.contains("8432"));
    let error =
        project_structured_state(memphant_types::EpisodeId::from_u128(43), body, &[rejected])
            .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("evidence span is not an exact user quote")
    );
}

#[test]
fn user_evidence_accepts_utf8_multiline_continuations_until_the_next_role() {
    let body = "developer: Never invent preferences.\nuser: My favorite café is\nCafé 美味 in Zürich.\nNote: this colon prose is still part of the user turn.\nmodel: I will remember that.\ncustom_role: synthetic content\nuser_agent: My city is Zürich.";
    let accepted_quote = "My favorite café is\nCafé 美味 in Zürich.\nNote: this colon prose is still part of the user turn.";
    let accepted = op(
        StructuredStateOperation::Create,
        "profile",
        "favorite café",
        fields(&[("value", json!("Café 美味"))]),
        body,
        accepted_quote,
    );
    let note = op(
        StructuredStateOperation::Append,
        "notes",
        "colon prose",
        fields(&[("value", json!("kept"))]),
        body,
        "Note: this colon prose is still part of the user turn.",
    );
    let rejected = [
        "Never invent preferences.",
        "I will remember that.",
        "synthetic content",
    ]
    .map(|quote| {
        op(
            StructuredStateOperation::Create,
            "profile",
            "favorite café",
            fields(&[("value", json!(quote))]),
            body,
            quote,
        )
    });

    let projected = project_structured_state(
        memphant_types::EpisodeId::from_u128(45),
        body,
        &[accepted, note],
    )
    .unwrap();

    assert_eq!(projected.len(), 2);
    assert_eq!(projected[0].contextual_chunks[0].body, accepted_quote);
    for operation in rejected {
        assert!(
            project_structured_state(memphant_types::EpisodeId::from_u128(45), body, &[operation],)
                .unwrap_err()
                .to_string()
                .contains("evidence span is not an exact user quote")
        );
    }
}

#[test]
fn unlabelled_continuation_before_the_first_role_is_rejected() {
    let body = "Café 美味 is my favorite.\nuser_agent: My city is Zürich.";
    let unlabelled = op(
        StructuredStateOperation::Create,
        "profile",
        "favorite_cafe",
        fields(&[("value", json!("Café 美味"))]),
        body,
        "Café 美味 is my favorite.",
    );
    let labelled = op(
        StructuredStateOperation::Create,
        "profile",
        "city",
        fields(&[("value", json!("Zürich"))]),
        body,
        "My city is Zürich.",
    );

    let projected =
        project_structured_state(memphant_types::EpisodeId::from_u128(46), body, &[labelled])
            .unwrap();

    assert_eq!(projected.len(), 1);
    assert!(projected[0].body.contains("Zürich"));
    assert!(
        project_structured_state(
            memphant_types::EpisodeId::from_u128(46),
            body,
            &[unlabelled],
        )
        .unwrap_err()
        .to_string()
        .contains("evidence span is not an exact user quote")
    );
}

#[test]
fn unlabelled_prose_and_invalid_validity_never_become_canonical_state() {
    let body = "I own a yacht.\nuser: I live in Oslo.";
    let unlabelled = op(
        StructuredStateOperation::Create,
        "profile",
        "vehicle",
        fields(&[("value", json!("yacht"))]),
        body,
        "I own a yacht.",
    );
    let mut invalid_interval = op(
        StructuredStateOperation::Create,
        "profile",
        "city",
        fields(&[("value", json!("Oslo"))]),
        body,
        "I live in Oslo.",
    );
    invalid_interval.valid_from = Some("2026-02-01T00:00:00Z".to_string());
    invalid_interval.valid_to = Some("2026-01-01T00:00:00Z".to_string());

    assert!(
        project_structured_state(
            memphant_types::EpisodeId::from_u128(44),
            body,
            &[unlabelled],
        )
        .unwrap_err()
        .to_string()
        .contains("evidence span is not an exact user quote")
    );
    assert!(
        project_structured_state(
            memphant_types::EpisodeId::from_u128(44),
            body,
            &[invalid_interval],
        )
        .unwrap_err()
        .to_string()
        .contains("valid")
    );
}

#[test]
fn provider_operation_schema_rejects_unknown_fields_and_unknown_verbs() {
    let base = json!({
        "operation": "upsert",
        "namespace": "todo",
        "item_key": "dentist",
        "fields": {"title": "Book dentist"},
        "evidence_quote": "Book the dentist.",
        "source_span": "6-23",
        "valid_from": null,
        "valid_to": null
    });
    let mut unknown_field = base.clone();
    unknown_field["model_truth"] = json!(true);
    assert!(serde_json::from_value::<StructuredStateOp>(unknown_field).is_err());
    let mut unknown_verb = base;
    unknown_verb["operation"] = json!("merge");
    assert!(serde_json::from_value::<StructuredStateOp>(unknown_verb).is_err());
}

#[tokio::test]
async fn provider_failure_returns_unavailable_without_compiling_raw_only() {
    let body = "user: Remember this ordinary source episode even if extraction is offline.";
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(FixedClock("2026-07-13T13:00:00Z")),
        Arc::new(NoopEmbedding),
    )
    .with_structured_state_provider(Arc::new(FakeProvider::failing()));

    service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&context, body),
        )
        .await
        .unwrap();
    assert_eq!(service.run_worker_tick(usize::MAX).await.unwrap(), 0);
    assert_eq!(store.dead_letter_count().await.unwrap(), 1);
    assert!(store.memory_units(tenant).is_empty());
    assert!(store.reflect_traces(tenant).is_empty());
}

#[tokio::test]
async fn compiler_identity_and_append_mapping_are_retry_stable() {
    let body = "user: I walked 8432 steps today.";
    let operation = op(
        StructuredStateOperation::Append,
        "steps",
        "daily",
        fields(&[("count", json!(8432))]),
        body,
        "I walked 8432 steps today.",
    );
    let provider = FakeProvider::new(vec![]);
    let episode = memphant_types::EpisodeId::from_u128(42);
    let first = project_structured_state(episode, body, std::slice::from_ref(&operation)).unwrap();
    let retry = project_structured_state(episode, body, &[operation]).unwrap();
    assert_eq!(first, retry);
    assert_eq!(
        memphant_core::structured_compiler_identity("compiler-v1", provider.identity()),
        memphant_core::structured_compiler_identity("compiler-v1", provider.identity())
    );
    let mut changed_identity = provider.identity().clone();
    changed_identity.model = "fake-state-model-v2".to_string();
    assert_ne!(
        memphant_core::structured_compiler_identity("compiler-v1", provider.identity()),
        memphant_core::structured_compiler_identity("compiler-v1", &changed_identity)
    );
    assert!(
        first[0]
            .predicate
            .contains("00000000-0000-0000-0000-00000000002a")
    );
    assert!(first[0].body.starts_with("steps item daily:"));
}

#[tokio::test]
async fn bounded_delete_preserves_valid_time_outside_the_deleted_interval() {
    let initial_body = "user: I lived in Oslo throughout 2025.";
    let split_body = "user: I lived in Lima during spring 2025.";
    let delete_body = "user: Forget my home city from March through July 2025.";
    let mut initial = op(
        StructuredStateOperation::Create,
        "profile",
        "home_city",
        fields(&[("value", json!("Oslo"))]),
        initial_body,
        "I lived in Oslo throughout 2025.",
    );
    initial.valid_from = Some("2025-01-01T00:00:00Z".to_string());
    initial.valid_to = Some("2026-01-01T00:00:00Z".to_string());
    let mut split = op(
        StructuredStateOperation::Create,
        "profile",
        "home_city",
        fields(&[("value", json!("Lima"))]),
        split_body,
        "I lived in Lima during spring 2025.",
    );
    split.valid_from = Some("2025-04-01T00:00:00Z".to_string());
    split.valid_to = Some("2025-07-01T00:00:00Z".to_string());
    let mut delete = op(
        StructuredStateOperation::Delete,
        "profile",
        "home_city",
        BTreeMap::new(),
        delete_body,
        "Forget my home city from March through July 2025.",
    );
    delete.valid_from = Some("2025-03-01T00:00:00Z".to_string());
    delete.valid_to = Some("2025-08-01T00:00:00Z".to_string());
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    retain_and_reflect(
        &service(store.clone(), "2026-01-02T00:00:00Z", vec![vec![initial]]),
        &context,
        initial_body,
    )
    .await;
    retain_and_reflect(
        &service(store.clone(), "2026-02-02T00:00:00Z", vec![vec![split]]),
        &context,
        split_body,
    )
    .await;
    let delete_service = service(store.clone(), "2026-03-02T00:00:00Z", vec![vec![delete]]);
    retain_and_reflect(&delete_service, &context, delete_body).await;

    let key = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("profile"),
        Some("home_city"),
        "",
    );
    let versions: Vec<_> = store
        .memory_units(tenant)
        .into_iter()
        .filter(|unit| unit.fact_key.as_deref() == Some(&key))
        .collect();
    let active: Vec<_> = versions
        .iter()
        .filter(|unit| unit.state == UnitState::Active)
        .collect();
    assert_eq!(
        active.len(),
        2,
        "outside rectangles must survive: {versions:?}"
    );
    assert!(active.iter().any(|unit| {
        unit.valid_from.as_deref() == Some("2025-01-01T00:00:00Z")
            && unit.valid_to.as_deref() == Some("2025-03-01T00:00:00Z")
    }));
    assert!(active.iter().any(|unit| {
        unit.valid_from.as_deref() == Some("2025-08-01T00:00:00Z")
            && unit.valid_to.as_deref() == Some("2026-01-01T00:00:00Z")
    }));
    assert!(
        versions
            .iter()
            .any(|unit| unit.state == UnitState::Superseded)
    );
    assert!(!active.iter().any(|unit| {
        unit.valid_from.as_deref() == Some("2025-03-01T00:00:00Z")
            && unit.valid_to.as_deref() == Some("2025-08-01T00:00:00Z")
    }));

    let recall = |transaction_as_of: Option<&str>| RecallHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        query: "profile home city".to_string(),
        limit: Some(10),
        budget_tokens: Some(256),
        mode: None,
        include_beliefs: Some(true),
        transaction_as_of: transaction_as_of.map(str::to_string),
        valid_at: Some("2025-05-01T00:00:00Z".to_string()),
        aggregation_window: None,
    };
    let before_delete = delete_service
        .recall(context.clone(), recall(Some("2026-02-15T00:00:00Z")))
        .await
        .unwrap();
    assert!(before_delete.items.iter().any(|item| {
        item.body.starts_with("profile item home_city") && item.body.contains("Lima")
    }));
    let after_delete = delete_service
        .recall(context.clone(), recall(None))
        .await
        .unwrap();
    assert!(
        !after_delete
            .items
            .iter()
            .any(|item| { item.body.starts_with("profile item home_city") })
    );
    let delete_edges = store
        .memory_edges(tenant)
        .into_iter()
        .filter(|edge| edge.transaction_from.as_deref() == Some("2026-03-02T00:00:00Z"))
        .collect::<Vec<_>>();
    assert_eq!(delete_edges.len(), 2, "one edge per preserved remainder");
    assert!(delete_edges.iter().all(|edge| {
        edge.kind == memphant_types::MemoryEdgeKind::Supersedes
            && active.iter().any(|unit| unit.id == edge.src_id)
    }));
}

#[tokio::test]
async fn recurring_exact_body_still_replaces_every_overlapping_active_rectangle() {
    let bodies = [
        "user: Oslo was my home city throughout 2025.",
        "user: Lima was my home city in spring 2025.",
        "user: Oslo was my home city from March through July 2025.",
    ];
    let intervals = [
        ("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("2025-04-01T00:00:00Z", "2025-07-01T00:00:00Z"),
        ("2025-03-01T00:00:00Z", "2025-08-01T00:00:00Z"),
    ];
    let values = ["Oslo", "Lima", "Oslo"];
    let clocks = [
        "2026-01-02T00:00:00Z",
        "2026-02-02T00:00:00Z",
        "2026-03-02T00:00:00Z",
    ];
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let mut last_service = None;

    for index in 0..3 {
        let quote = bodies[index].strip_prefix("user: ").unwrap();
        let mut operation = op(
            StructuredStateOperation::Create,
            "profile",
            "home_city",
            fields(&[("value", json!(values[index]))]),
            bodies[index],
            quote,
        );
        operation.valid_from = Some(intervals[index].0.to_string());
        operation.valid_to = Some(intervals[index].1.to_string());
        let current = service(store.clone(), clocks[index], vec![vec![operation]]);
        retain_and_reflect(&current, &context, bodies[index]).await;
        last_service = Some(current);
    }
    let last_service = last_service.unwrap();
    let key = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("profile"),
        Some("home_city"),
        "",
    );
    let versions: Vec<_> = store
        .memory_units(tenant)
        .into_iter()
        .filter(|unit| unit.fact_key.as_deref() == Some(&key))
        .collect();
    let active: Vec<_> = versions
        .iter()
        .filter(|unit| unit.state == UnitState::Active)
        .collect();
    assert_eq!(
        active.len(),
        3,
        "one replacement plus two outside remainders"
    );
    assert!(active.iter().any(|unit| {
        unit.body.contains("Oslo")
            && unit.valid_from.as_deref() == Some("2025-01-01T00:00:00Z")
            && unit.valid_to.as_deref() == Some("2025-03-01T00:00:00Z")
    }));
    let replacement = active
        .iter()
        .find(|unit| {
            unit.body.contains("Oslo") && unit.valid_from.as_deref() == Some("2025-03-01T00:00:00Z")
        })
        .expect("spanning replacement");
    assert_eq!(
        replacement.valid_from.as_deref(),
        Some("2025-03-01T00:00:00Z")
    );
    assert_eq!(
        replacement.valid_to.as_deref(),
        Some("2025-08-01T00:00:00Z")
    );
    assert!(active.iter().any(|unit| {
        unit.body.contains("Oslo")
            && unit.valid_from.as_deref() == Some("2025-08-01T00:00:00Z")
            && unit.valid_to.as_deref() == Some("2026-01-01T00:00:00Z")
    }));
    assert!(active.iter().all(|unit| !unit.body.contains("Lima")));

    let replacement_edges: Vec<_> = store
        .memory_edges(tenant)
        .into_iter()
        .filter(|edge| {
            edge.transaction_from.as_deref() == Some("2026-03-02T00:00:00Z")
                && (edge.src_id == replacement.id || edge.dst_id == replacement.id)
        })
        .collect();
    assert_eq!(
        replacement_edges.len(),
        6,
        "two edges for each overwritten rectangle"
    );
    assert_eq!(
        replacement_edges
            .iter()
            .filter(|edge| edge.kind == memphant_types::MemoryEdgeKind::Contradicts)
            .count(),
        3
    );
    assert_eq!(
        replacement_edges
            .iter()
            .filter(|edge| edge.kind == memphant_types::MemoryEdgeKind::Supersedes)
            .count(),
        3
    );

    let request = |tx: Option<&str>| RecallHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        query: "profile home city".to_string(),
        limit: Some(10),
        budget_tokens: Some(256),
        mode: None,
        include_beliefs: Some(true),
        transaction_as_of: tx.map(str::to_string),
        valid_at: Some("2025-05-01T00:00:00Z".to_string()),
        aggregation_window: None,
    };
    let historical = last_service
        .recall(context.clone(), request(Some("2026-02-15T00:00:00Z")))
        .await
        .unwrap();
    assert!(historical.items.iter().any(|item| {
        item.body.starts_with("profile item home_city") && item.body.contains("Lima")
    }));
    let current = last_service
        .recall(context.clone(), request(None))
        .await
        .unwrap();
    assert!(current.items.iter().any(|item| {
        item.body.starts_with("profile item home_city") && item.body.contains("Oslo")
    }));
    assert!(current.items.iter().all(|item| {
        !item.body.starts_with("profile item home_city") || !item.body.contains("Lima")
    }));
}

#[tokio::test]
async fn projected_updates_support_current_and_transaction_as_of_recall() {
    let old_body = "user: Set my home city to Oslo.";
    let new_body = "user: Change my home city to Lima.";
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let old_service = service(
        store.clone(),
        "2026-01-02T00:00:00Z",
        vec![vec![op(
            StructuredStateOperation::Create,
            "profile",
            "home_city",
            fields(&[("value", json!("Oslo"))]),
            old_body,
            "Set my home city to Oslo.",
        )]],
    );
    retain_and_reflect(&old_service, &context, old_body).await;
    let new_service = service(
        store.clone(),
        "2026-02-02T00:00:00Z",
        vec![vec![op(
            StructuredStateOperation::Create,
            "profile",
            "home_city",
            fields(&[("value", json!("Lima"))]),
            new_body,
            "Change my home city to Lima.",
        )]],
    );
    retain_and_reflect(&new_service, &context, new_body).await;

    let request = |query: &str, tx: Option<&str>| RecallHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        query: query.to_string(),
        limit: Some(5),
        budget_tokens: Some(128),
        mode: None,
        include_beliefs: Some(true),
        transaction_as_of: tx.map(str::to_string),
        valid_at: Some("2026-03-01T00:00:00Z".to_string()),
        aggregation_window: None,
    };

    let current = new_service
        .recall(context.clone(), request("home city Lima", None))
        .await
        .unwrap();
    assert!(current.items.iter().any(|item| item.body.contains("Lima")));
    let projected_item = current
        .items
        .iter()
        .find(|item| item.body.starts_with("profile item") && item.body.contains("Lima"))
        .unwrap();
    let latest_episode = store.episodes(tenant).last().unwrap().id;
    assert!(current.citations.iter().any(|citation| {
        citation.unit_id == projected_item.unit_id && citation.episode_id == Some(latest_episode)
    }));
    assert!(
        current
            .items
            .iter()
            .filter(|item| item.body.starts_with("profile item"))
            .all(|item| !item.body.contains("Oslo"))
    );
    let historical = new_service
        .recall(
            context.clone(),
            request("home city Oslo", Some("2026-01-15T00:00:00Z")),
        )
        .await
        .unwrap();
    assert!(
        historical
            .items
            .iter()
            .any(|item| item.body.contains("Oslo"))
    );
    assert!(
        historical
            .items
            .iter()
            .filter(|item| item.body.starts_with("profile item"))
            .all(|item| !item.body.contains("Lima"))
    );
}
