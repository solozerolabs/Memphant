use std::collections::BTreeMap;

use memphant_core::{FixedClock, InMemoryStore, MemoryStore, recall, record_mark};
use memphant_types::{
    AggregationWindow, MarkOutcome, MarkRequest, MemoryKind, NewMemoryUnit, RecallMode,
    RecallRequest, ResolvedMemoryContext, TenantId, TrustLevel, UnitState,
};
use serde_json::{Value, json};

const CLOCK: FixedClock = FixedClock("2025-06-08T00:00:00Z");

fn quantity_body(
    item_key: &str,
    measure: &str,
    value: &str,
    unit: &str,
    occurred_at: &str,
    dimensions: Value,
) -> String {
    let fields = BTreeMap::from([
        ("dimensions", dimensions),
        ("measure", json!(measure)),
        ("occurred_at", json!(occurred_at)),
        ("type", json!("quantity_event.v1")),
        ("unit", json!(unit)),
        ("value", json!(value)),
    ]);
    format!(
        "activity item {item_key}: {}",
        serde_json::to_string(&fields).unwrap()
    )
}

async fn stage_quantity(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    body: String,
    state: UnitState,
    transaction_from: &str,
    transaction_to: Option<&str>,
) {
    let mut tx = store.begin(context).await.unwrap();
    store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                kind: MemoryKind::Semantic,
                state,
                fact_key: None,
                predicate: None,
                body,
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("user".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: Some(transaction_from.to_string()),
                transaction_to: transaction_to.map(str::to_string),
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
}

fn request(context: &ResolvedMemoryContext, query: &str) -> RecallRequest {
    RecallRequest {
        context: context.clone(),
        query: query.to_string(),
        k: 10,
        budget_tokens: 1024,
        mode: RecallMode::Fast,
        include_beliefs: false,
        edge_expansion_enabled: true,
        context_packing_abstention_enabled: false,
        procedure_recall_enabled: true,
        decay_enabled: false,
        engine_version: "quantity-rollup-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: Some(AggregationWindow {
            from: "2025-06-01T00:00:00Z".to_string(),
            to: "2025-06-08T00:00:00Z".to_string(),
        }),
    }
}

#[tokio::test]
async fn more_than_ten_events_roll_up_exactly_with_complete_provenance() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let values = [
        "3.41", "9.15", "7.35", "28.50", "3.91", "3.25", "9.84", "13.83", "3.45", "19.77", "7.33",
        "6.80", "8.48", "9.75", "6.94", "4.78", "11.79", "15.72", "23.03", "8.13", "7.94", "30.01",
        "4.77", "10.30",
    ];
    for (index, value) in values.iter().enumerate() {
        stage_quantity(
            &store,
            &context,
            quantity_body(
                "expenses",
                "food_spending",
                value,
                "usd",
                &format!("2025-06-{:02}T12:00:00Z", index % 7 + 1),
                json!({"expense_type": if index % 3 == 0 { "coffee" } else { "meal" }}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    for index in 0..100 {
        stage_quantity(
            &store,
            &context,
            format!("total food spending weekly planning distractor {index}: no observed expense"),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }

    let mut baseline_request = request(&context, "What is my total food spending?");
    baseline_request.aggregation_window = None;
    let baseline = recall(&store, baseline_request, None, &CLOCK)
        .await
        .unwrap();

    let response = recall(
        &store,
        request(&context, "What is my total food spending?"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    let rollup = response
        .items
        .iter()
        .find(|item| item.derived_by == "quantity_rollup")
        .expect("rollup is packed despite the ten-item output cap");
    assert!(rollup.body.contains("total=258.23"), "{}", rollup.body);
    assert!(
        rollup.body.contains("average=10.759583 ("),
        "{}",
        rollup.body
    );
    assert_eq!(rollup.derived_from_unit_ids.len(), 24);
    let citation = response
        .citations
        .iter()
        .find(|citation| citation.unit_id == rollup.unit_id)
        .expect("rollup citation");
    assert_eq!(citation.derived_from_unit_ids, rollup.derived_from_unit_ids);
    let ordinary_ids = response
        .items
        .iter()
        .filter(|item| item.derived_by != "quantity_rollup")
        .map(|item| item.unit_id)
        .collect::<Vec<_>>();
    assert_eq!(
        ordinary_ids,
        baseline
            .items
            .iter()
            .take(ordinary_ids.len())
            .map(|item| item.unit_id)
            .collect::<Vec<_>>(),
        "a synthetic rollup must not reorder ordinary retrieval",
    );
    record_mark(
        &store,
        &context,
        MarkRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            trace_id: response.trace_id,
            caller_id: "quantity-rollup-test".to_string(),
            used_ids: vec![rollup.unit_id],
            outcome: MarkOutcome::Success,
        },
        &CLOCK,
    )
    .await
    .unwrap();
    assert_eq!(
        store.review_events(tenant)[0].used_ids,
        rollup.derived_from_unit_ids
    );
}

#[tokio::test]
async fn goal_query_packs_the_matching_compact_goal_beside_the_rollup() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    stage_quantity(
        &store,
        &context,
        quantity_body(
            "food_spending",
            "food_spending",
            "79.49",
            "usd",
            "2025-06-04T12:00:00Z",
            json!({"expense_type": "coffee"}),
        ),
        UnitState::Active,
        "2025-06-01T00:00:00Z",
        None,
    )
    .await;
    stage_quantity(
        &store,
        &context,
        "coffee_spending_goal item spending_limit: {\"expense_type\":\"coffee\",\"frequency\":\"weekly\",\"target_amount\":\"30\"}".to_string(),
        UnitState::Active,
        "2025-06-01T00:00:00Z",
        None,
    )
    .await;
    for index in 0..100 {
        stage_quantity(
            &store,
            &context,
            format!("project_goal item target: {{\"name\":\"generic {index}\",\"value\":\"40\"}}"),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }

    let response = recall(
        &store,
        request(&context, "Am I meeting my coffee budget goal?"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();

    assert!(response.items[0].body.starts_with("quantity rollup "));
    assert_eq!(
        response.items[1].body,
        "coffee_spending_goal item spending_limit: {\"expense_type\":\"coffee\",\"frequency\":\"weekly\",\"target_amount\":\"30\"}"
    );
}

#[tokio::test]
async fn dimension_filter_and_window_are_applied_before_aggregation() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    for (value, kind, occurred_at) in [
        ("8.48", "coffee", "2025-06-04T12:00:00Z"),
        ("9.75", "coffee", "2025-06-05T12:00:00Z"),
        ("12.00", "meal", "2025-06-05T12:00:00Z"),
        ("99.00", "coffee", "2025-05-31T12:00:00Z"),
    ] {
        stage_quantity(
            &store,
            &context,
            quantity_body(
                "expenses",
                "food_spending",
                value,
                "usd",
                occurred_at,
                json!({"expense_type": kind}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    let response = recall(
        &store,
        request(&context, "How much coffee spending?"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    let body = &response
        .items
        .iter()
        .find(|item| item.derived_by == "quantity_rollup")
        .unwrap()
        .body;
    assert!(body.contains("filter=expense_type=coffee"), "{body}");
    assert!(body.contains("total=18.23"), "{body}");
    assert!(body.contains("count=2"), "{body}");
}

#[tokio::test]
async fn distinct_series_and_units_never_combine() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    for (item_key, measure, value, unit) in [
        ("daily_steps", "steps", "14096", "steps"),
        ("daily_steps", "steps", "7935", "steps"),
        ("distance", "steps", "3.5", "km"),
        ("other_steps", "steps", "100", "steps"),
    ] {
        stage_quantity(
            &store,
            &context,
            quantity_body(
                item_key,
                measure,
                value,
                unit,
                "2025-06-04T12:00:00Z",
                json!({"activity_type": "work"}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    let response = recall(
        &store,
        request(&context, "total daily steps for work"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    let bodies = response
        .items
        .iter()
        .filter(|item| item.derived_by == "quantity_rollup")
        .map(|item| item.body.as_str())
        .collect::<Vec<_>>();
    assert!(bodies.iter().any(|body| body.contains("total=22031")));
    assert!(!bodies.iter().any(|body| body.contains("total=22131")));
    assert!(!bodies.iter().any(|body| body.contains("total=22034.5")));
}

#[tokio::test]
async fn matched_dimensions_are_and_across_keys_and_or_within_each_key() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    for (value, expense_type, context_kind) in [
        ("5.00", "coffee", "work"),
        ("7.00", "coffee", "home"),
        ("11.00", "meal", "work"),
    ] {
        stage_quantity(
            &store,
            &context,
            quantity_body(
                "expenses",
                "food_spending",
                value,
                "usd",
                "2025-06-04T12:00:00Z",
                json!({"expense_type": expense_type, "context": context_kind}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    let response = recall(&store, request(&context, "work coffee total"), None, &CLOCK)
        .await
        .unwrap();
    let body = &response
        .items
        .iter()
        .find(|item| item.derived_by == "quantity_rollup")
        .unwrap()
        .body;
    assert!(body.contains("total=5"), "{body}");
    assert!(body.contains("count=1"), "{body}");
}

#[tokio::test]
async fn daily_steps_rollup_exposes_exact_total_and_average_for_goal_queries() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    for (day, value) in [
        (1, "14096"),
        (2, "7935"),
        (3, "7640"),
        (4, "4502"),
        (5, "6870"),
        (6, "5269"),
        (7, "4956"),
    ] {
        stage_quantity(
            &store,
            &context,
            quantity_body(
                "daily_steps",
                "steps",
                value,
                "steps",
                &format!("2025-06-{day:02}T12:00:00Z"),
                json!({"activity_type": "daily_steps"}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    let response = recall(
        &store,
        request(&context, "Am I meeting my daily steps goal?"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    let body = &response
        .items
        .iter()
        .find(|item| item.derived_by == "quantity_rollup")
        .unwrap()
        .body;
    assert!(body.contains("total=51268"), "{body}");
    assert!(body.contains("average=7324"), "{body}");
    assert!(body.contains("count=7"), "{body}");
}

#[tokio::test]
async fn rollup_never_crosses_tenant_or_scope_boundaries() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let other_tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let other_scope_context = memphant_store_testkit::bind_context(&store, tenant).await;
    let other_tenant_context = memphant_store_testkit::bind_context(&store, other_tenant).await;
    for (event_context, value) in [
        (&context, "5.00"),
        (&other_scope_context, "50.00"),
        (&other_tenant_context, "500.00"),
    ] {
        stage_quantity(
            &store,
            event_context,
            quantity_body(
                "expenses",
                "food_spending",
                value,
                "usd",
                "2025-06-04T12:00:00Z",
                json!({"expense_type": "coffee"}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    let response = recall(&store, request(&context, "coffee total"), None, &CLOCK)
        .await
        .unwrap();
    let rollup = response
        .items
        .iter()
        .find(|item| item.derived_by == "quantity_rollup")
        .unwrap();
    assert!(rollup.body.contains("total=5"), "{}", rollup.body);
    assert_eq!(rollup.derived_from_unit_ids.len(), 1);
}

#[tokio::test]
async fn unrelated_query_does_not_synthesize_or_displace_generic_memory() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    stage_quantity(
        &store,
        &context,
        quantity_body(
            "expenses",
            "food_spending",
            "8.48",
            "usd",
            "2025-06-04T12:00:00Z",
            json!({"expense_type": "coffee"}),
        ),
        UnitState::Active,
        "2025-06-01T00:00:00Z",
        None,
    )
    .await;
    let mut tx = store.begin(&context).await.unwrap();
    store
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
                fact_key: None,
                predicate: None,
                body: "Favorite movie is Arrival".to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(context.actor_id),
                source_kind: Some("user".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-09T00:00:00Z".to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: Some("2025-06-01T00:00:00Z".to_string()),
                transaction_to: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    let response = recall(
        &store,
        request(&context, "What movie do I like?"),
        None,
        &CLOCK,
    )
    .await
    .unwrap();
    assert!(
        response
            .items
            .iter()
            .all(|item| item.derived_by != "quantity_rollup")
    );
    assert!(
        response
            .items
            .iter()
            .any(|item| item.body == "Favorite movie is Arrival")
    );
}

#[tokio::test]
async fn aggregation_does_not_merge_other_allowed_scopes() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let other_context = memphant_store_testkit::bind_context(&store, tenant).await;
    for (event_context, value) in [(&context, "10.00"), (&other_context, "100.00")] {
        stage_quantity(
            &store,
            event_context,
            quantity_body(
                "expenses",
                "food_spending",
                value,
                "usd",
                "2025-06-04T12:00:00Z",
                json!({"expense_type": "coffee"}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    let mut recall_request = request(&context, "coffee total");
    for sources in recall_request.context.sources_by_kind.values_mut() {
        sources.push(memphant_types::ResolvedMemorySource {
            scope_id: other_context.scope_id,
            agent_node_id: other_context.agent_node_id,
        });
    }

    let response = recall(&store, recall_request, None, &CLOCK).await.unwrap();
    let rollup = response
        .items
        .iter()
        .find(|item| item.derived_by == "quantity_rollup")
        .unwrap();

    assert!(rollup.body.contains("total=10"), "{}", rollup.body);
    assert!(!rollup.body.contains("total=110"), "{}", rollup.body);
    assert_eq!(rollup.derived_from_unit_ids.len(), 1);
}

#[tokio::test]
async fn append_corrections_use_explicit_reversal_and_replacement_events() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    for value in ["10.00", "-10.00", "20.00"] {
        stage_quantity(
            &store,
            &context,
            quantity_body(
                "expenses",
                "food_spending",
                value,
                "usd",
                "2025-06-04T12:00:00Z",
                json!({"expense_type": "coffee"}),
            ),
            UnitState::Active,
            "2025-06-01T00:00:00Z",
            None,
        )
        .await;
    }
    let response = recall(&store, request(&context, "coffee total"), None, &CLOCK)
        .await
        .unwrap();
    let rollup = response
        .items
        .iter()
        .find(|item| item.derived_by == "quantity_rollup")
        .unwrap();
    assert!(rollup.body.contains("total=20"), "{}", rollup.body);
    assert_eq!(rollup.derived_from_unit_ids.len(), 3);
}

#[tokio::test]
async fn invalid_half_open_window_is_rejected() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let mut invalid = request(&context, "food total");
    invalid.aggregation_window = Some(AggregationWindow {
        from: "2025-06-08T00:00:00Z".to_string(),
        to: "2025-06-08T00:00:00Z".to_string(),
    });
    let error = recall(&store, invalid, None, &CLOCK).await.unwrap_err();
    assert_eq!(
        error.to_string(),
        "invalid request: aggregation_window.from must be before aggregation_window.to"
    );
}

#[tokio::test]
async fn transaction_as_of_selects_one_event_version_and_invalidated_events_stay_out() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let old = quantity_body(
        "expenses",
        "food_spending",
        "10.00",
        "usd",
        "2025-06-04T12:00:00Z",
        json!({"expense_type": "coffee"}),
    );
    stage_quantity(
        &store,
        &context,
        old,
        UnitState::Superseded,
        "2025-06-01T00:00:00Z",
        Some("2025-06-05T00:00:00Z"),
    )
    .await;
    stage_quantity(
        &store,
        &context,
        quantity_body(
            "expenses",
            "food_spending",
            "20.00",
            "usd",
            "2025-06-04T12:00:00Z",
            json!({"expense_type": "coffee"}),
        ),
        UnitState::Active,
        "2025-06-05T00:00:00Z",
        None,
    )
    .await;
    stage_quantity(
        &store,
        &context,
        quantity_body(
            "expenses",
            "food_spending",
            "100.00",
            "usd",
            "2025-06-04T12:00:00Z",
            json!({"expense_type": "coffee"}),
        ),
        UnitState::Invalidated,
        "2025-06-01T00:00:00Z",
        None,
    )
    .await;

    let mut before = request(&context, "coffee total");
    before.transaction_as_of = Some("2025-06-04T00:00:00Z".to_string());
    let before = recall(&store, before, None, &CLOCK).await.unwrap();
    assert!(
        before
            .items
            .iter()
            .any(|item| { item.derived_by == "quantity_rollup" && item.body.contains("total=10") }),
        "{:?}",
        before.items
    );
    let mut after = request(&context, "coffee total");
    after.transaction_as_of = Some("2025-06-06T00:00:00Z".to_string());
    let after = recall(&store, after, None, &CLOCK).await.unwrap();
    assert!(
        after
            .items
            .iter()
            .any(|item| { item.derived_by == "quantity_rollup" && item.body.contains("total=20") })
    );
    assert!(
        !after
            .items
            .iter()
            .any(|item| item.body.contains("total=120"))
    );
}
