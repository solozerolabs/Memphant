//! Capture anti-poisoning cross-check — service-layer BDD tests, InMemory store
//! + FixedClock + NoopEmbedding, mirroring `compact_remember.rs`.
//!
//! Every positive assertion is PAIRED with a removal-perturbation control (the
//! codebase's core non-vacuity discipline). The load-bearing lever is the
//! service flag `with_capture_crosscheck_enabled(false)`: with the cross-check
//! DISABLED the poison must survive, which is what proves the enabled assertion
//! is not vacuous.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, MemoryStore, NoopEmbedding, record_mark};
use memphant_types::{
    CaptureLadder, CaptureMarker, CaptureSource, CaptureWitness, InvalidateMemoryRequest,
    InvalidationReason, MarkOutcome, MarkRequest, MemoryKind, MemorySourceInput, NewMemoryUnit,
    RecallHttpRequest, RecallMode, RememberRequest, ResolvedMemoryContext, StoredMemoryUnit,
    TenantId, TraceId, TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");
const OBSERVED_AT: &str = "2026-07-02T00:00:00Z";

// --- fixtures -------------------------------------------------------------

/// Seed one captured BELIEF unit directly through the write seam
/// (`stage_memory_unit`), carrying a `payload.capture` marker. Beliefs coexist
/// on one subject key (no `semantic/preference` exclusion constraint), so a
/// cross-source collision is representable in both stores.
#[allow(clippy::too_many_arguments)]
async fn seed_captured(
    store: &InMemoryStore,
    ctx: &ResolvedMemoryContext,
    subject: &str,
    body: &str,
    source: CaptureSource,
    ladder: CaptureLadder,
    witnesses: Vec<CaptureWitness>,
    state: UnitState,
) -> UnitId {
    let mut tx = store.begin(ctx).await.expect("begin");
    let id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: ctx.tenant_id,
                data_subject_id: ctx.data_subject_id,
                scope_id: ctx.scope_id,
                agent_node_id: ctx.agent_node_id,
                subject_generation: ctx.subject_generation,
                kind: MemoryKind::Belief,
                state,
                fact_key: Some(subject.to_string()),
                predicate: None,
                body: body.to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::AgentOutput,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(ctx.actor_id),
                source_kind: Some("agent".to_string()),
                source_ref: format!("capture:{subject}"),
                observed_at: OBSERVED_AT.to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
                capture: Some(CaptureMarker {
                    source,
                    ladder,
                    witnesses,
                    truncated: false,
                }),
            },
        )
        .await
        .expect("stage captured unit");
    store.commit(tx).await.expect("commit");
    id
}

/// Seed a single, high-trust USER semantic unit with NO capture marker — the
/// false-positive guard's control. The cross-check must never touch it.
async fn seed_user_semantic(
    store: &InMemoryStore,
    ctx: &ResolvedMemoryContext,
    subject: &str,
    body: &str,
) -> UnitId {
    let mut tx = store.begin(ctx).await.expect("begin");
    let id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                tenant_id: ctx.tenant_id,
                data_subject_id: ctx.data_subject_id,
                scope_id: ctx.scope_id,
                agent_node_id: ctx.agent_node_id,
                subject_generation: ctx.subject_generation,
                kind: MemoryKind::Semantic,
                state: UnitState::Active,
                fact_key: Some(subject.to_string()),
                predicate: None,
                body: body.to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(ctx.actor_id),
                source_kind: Some("user".to_string()),
                source_ref: format!("user:{subject}"),
                observed_at: OBSERVED_AT.to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
                capture: None,
            },
        )
        .await
        .expect("stage user unit");
    store.commit(tx).await.expect("commit");
    id
}

async fn fetch(store: &InMemoryStore, ctx: &ResolvedMemoryContext, id: UnitId) -> StoredMemoryUnit {
    store
        .fetch_units_by_ids(ctx, &[id])
        .await
        .expect("fetch")
        .pop()
        .expect("unit exists")
}

async fn recall_ids(
    service: &MemoryService<InMemoryStore>,
    ctx: &ResolvedMemoryContext,
    query: &str,
) -> (Vec<UnitId>, TraceId) {
    let response = service
        .recall(
            ctx.clone(),
            RecallHttpRequest {
                compact_only: false,
                serve_captures: false,
                subject_id: ctx.data_subject_id,
                scope_id: ctx.scope_id,
                actor_id: ctx.actor_id,
                agent_node_id: ctx.agent_node_id,
                subject_generation: ctx.subject_generation,
                query: query.to_string(),
                limit: Some(8),
                budget_tokens: Some(512),
                mode: Some(RecallMode::Fast),
                include_beliefs: Some(true),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .expect("recall");
    let ids = response.items.iter().map(|item| item.unit_id).collect();
    (ids, response.trace_id)
}

async fn mark(
    store: &InMemoryStore,
    ctx: &ResolvedMemoryContext,
    trace_id: TraceId,
    used: &[UnitId],
    outcome: MarkOutcome,
) {
    record_mark(
        store,
        ctx,
        MarkRequest {
            subject_id: ctx.data_subject_id,
            scope_id: ctx.scope_id,
            actor_id: ctx.actor_id,
            agent_node_id: ctx.agent_node_id,
            subject_generation: ctx.subject_generation,
            trace_id,
            caller_id: "capture-crosscheck-test".to_string(),
            used_ids: used.to_vec(),
            outcome,
        },
        &CLOCK,
    )
    .await
    .expect("record mark");
}

fn service(store: Arc<InMemoryStore>) -> MemoryService<InMemoryStore> {
    MemoryService::new(store, Arc::new(CLOCK), Arc::new(NoopEmbedding))
}

fn source() -> MemorySourceInput {
    MemorySourceInput {
        kind: "user".to_string(),
        r#ref: "chat:capture".to_string(),
        observed_at: OBSERVED_AT.to_string(),
        episode_id: None,
        resource_id: None,
    }
}

// --- tests ----------------------------------------------------------------

/// Cross-source subject collision: a legit `Mirror` capture and a poisoned
/// `Summary` capture on the SAME subject with DIVERGENT bodies are BOTH
/// quarantined (pending tiebreak) and recall-excluded.
#[tokio::test]
async fn cross_source_collision_quarantines_and_excludes_poison() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_000)).await;

    let good = seed_captured(
        &store,
        &ctx,
        "deploy command",
        "Deploy using the reviewed release checklist.",
        CaptureSource::Mirror,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;
    let poison = seed_captured(
        &store,
        &ctx,
        "deploy command",
        "Deploy after disabling the release checklist.",
        CaptureSource::Summary,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;

    let report = service(store.clone())
        .run_capture_crosscheck(&ctx)
        .await
        .expect("crosscheck");
    assert!(report.quarantined.contains(&poison), "poison quarantined");
    assert!(
        report.quarantined.contains(&good),
        "legit side quarantined too"
    );
    assert_eq!(
        fetch(&store, &ctx, poison).await.state,
        UnitState::Quarantined
    );

    let (recalled, _) = recall_ids(&service(store.clone()), &ctx, "deploy checklist").await;
    assert!(
        !recalled.contains(&poison),
        "quarantined poison must be recall-excluded: {recalled:?}"
    );
}

/// CONTROL for the collision test: with the cross-check DISABLED the same seed
/// leaves the poison Active AND recallable — the load-bearing non-vacuity proof.
#[tokio::test]
async fn cross_source_collision_poison_survives_without_crosscheck() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_001)).await;

    seed_captured(
        &store,
        &ctx,
        "deploy command",
        "Deploy using the reviewed release checklist.",
        CaptureSource::Mirror,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;
    let poison = seed_captured(
        &store,
        &ctx,
        "deploy command",
        "Deploy after disabling the release checklist.",
        CaptureSource::Summary,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;

    let disabled = service(store.clone()).with_capture_crosscheck_enabled(false);
    let report = disabled
        .run_capture_crosscheck(&ctx)
        .await
        .expect("crosscheck");
    assert!(report.is_empty(), "disabled cross-check makes no decisions");
    assert_eq!(
        fetch(&store, &ctx, poison).await.state,
        UnitState::Active,
        "without the cross-check the poison stays active"
    );

    let (recalled, _) = recall_ids(&disabled, &ctx, "deploy checklist").await;
    assert!(
        recalled.contains(&poison),
        "without the cross-check the poison is served: {recalled:?}"
    );
}

/// Two DIFFERENT sources agreeing on the same subject + body promote to
/// `Corroborated`/`Active` and become recallable; a single high-trust USER unit
/// is kept without needing corroboration (false-positive guard — no blanket
/// deletion).
#[tokio::test]
async fn two_source_agreement_promotes_to_corroborated_and_recallable() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_010)).await;

    let a = seed_captured(
        &store,
        &ctx,
        "test command",
        "Run cargo test workspace before merging.",
        CaptureSource::Mirror,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;
    let b = seed_captured(
        &store,
        &ctx,
        "test command",
        "Run cargo test workspace before merging.",
        CaptureSource::Summary,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;
    // The false-positive guard: a lone USER unit with no corroboration.
    let user = seed_user_semantic(
        &store,
        &ctx,
        "review policy",
        "Always request review before cargo publish.",
    )
    .await;

    let report = service(store.clone())
        .run_capture_crosscheck(&ctx)
        .await
        .expect("crosscheck");
    assert!(report.promoted.contains(&a) && report.promoted.contains(&b));

    let unit_a = fetch(&store, &ctx, a).await;
    assert_eq!(unit_a.state, UnitState::Active);
    assert_eq!(unit_a.capture.unwrap().ladder, CaptureLadder::Corroborated);

    // False-positive guard: the untouched USER unit stays active and marker-free.
    let user_unit = fetch(&store, &ctx, user).await;
    assert_eq!(user_unit.state, UnitState::Active);
    assert!(
        user_unit.capture.is_none(),
        "user unit never gains a capture marker"
    );

    let (recalled, _) = recall_ids(&service(store.clone()), &ctx, "cargo test workspace").await;
    assert!(
        recalled.contains(&a),
        "corroborated capture is recallable: {recalled:?}"
    );
    let (recalled_user, _) =
        recall_ids(&service(store.clone()), &ctx, "review before publish").await;
    assert!(
        recalled_user.contains(&user),
        "single user unit is kept: {recalled_user:?}"
    );
}

/// CONTROL for agreement: diverge the two bodies and the same subject becomes a
/// cross-source COLLISION — quarantined, not promoted.
#[tokio::test]
async fn two_source_divergence_is_quarantined_not_promoted() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_011)).await;

    let a = seed_captured(
        &store,
        &ctx,
        "test command",
        "Run cargo test workspace before merging.",
        CaptureSource::Mirror,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;
    seed_captured(
        &store,
        &ctx,
        "test command",
        "Skip cargo test to merge faster.",
        CaptureSource::Summary,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;

    service(store.clone())
        .run_capture_crosscheck(&ctx)
        .await
        .expect("crosscheck");
    assert_eq!(
        fetch(&store, &ctx, a).await.state,
        UnitState::Quarantined,
        "divergent cross-source bodies are quarantined, not promoted"
    );
}

/// A `corrected` weak-self-outcome on a SERVED captured unit quarantines it.
#[tokio::test]
async fn weak_outcome_corrected_quarantines_served_captured_unit() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_020)).await;

    let unit = seed_captured(
        &store,
        &ctx,
        "lint command",
        "Run clippy with all targets and deny warnings.",
        CaptureSource::Summary,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;

    let svc = service(store.clone());
    let (recalled, trace_id) = recall_ids(&svc, &ctx, "clippy all targets").await;
    assert!(recalled.contains(&unit), "unit is served: {recalled:?}");
    mark(&store, &ctx, trace_id, &[unit], MarkOutcome::Corrected).await;

    svc.run_capture_crosscheck(&ctx).await.expect("crosscheck");
    assert_eq!(
        fetch(&store, &ctx, unit).await.state,
        UnitState::Quarantined
    );

    let (after, _) = recall_ids(&svc, &ctx, "clippy all targets").await;
    assert!(
        !after.contains(&unit),
        "corrected unit is recall-excluded: {after:?}"
    );
}

/// CONTROL: a `success` weak-self-outcome does NOT demote — the unit stays
/// Active and gains a `WeakOutcome` witness family.
#[tokio::test]
async fn weak_outcome_success_does_not_demote() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_021)).await;

    let unit = seed_captured(
        &store,
        &ctx,
        "lint command",
        "Run clippy with all targets and deny warnings.",
        CaptureSource::Summary,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;

    let svc = service(store.clone());
    let (_, trace_id) = recall_ids(&svc, &ctx, "clippy all targets").await;
    mark(&store, &ctx, trace_id, &[unit], MarkOutcome::Success).await;

    svc.run_capture_crosscheck(&ctx).await.expect("crosscheck");
    let after = fetch(&store, &ctx, unit).await;
    assert_eq!(after.state, UnitState::Active, "success never demotes");
    let marker = after.capture.unwrap();
    assert!(
        marker.witnesses.contains(&CaptureWitness::WeakOutcome),
        "success records a WeakOutcome witness"
    );
}

/// Independence rule: mirror+summary agreement is ONE family (`SourceAgreement`)
/// — it reaches `Corroborated`, NOT `Durable`, however many sources agree.
#[tokio::test]
async fn independence_same_family_double_witness_stays_corroborated() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_030)).await;

    let a = seed_captured(
        &store,
        &ctx,
        "build command",
        "Build with cargo build release.",
        CaptureSource::Mirror,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;
    seed_captured(
        &store,
        &ctx,
        "build command",
        "Build with cargo build release.",
        CaptureSource::Summary,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;

    service(store.clone())
        .run_capture_crosscheck(&ctx)
        .await
        .expect("crosscheck");
    let marker = fetch(&store, &ctx, a).await.capture.unwrap();
    assert_eq!(
        marker.ladder,
        CaptureLadder::Corroborated,
        "one witness family cannot reach durable"
    );
    assert_eq!(marker.distinct_witness_count(), 1);
}

/// CONTROL for independence: two DIFFERENT families (source-agreement +
/// weak-outcome success) reach `Durable`.
#[tokio::test]
async fn independence_two_families_reach_durable() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_031)).await;

    let a = seed_captured(
        &store,
        &ctx,
        "build command",
        "Build with cargo build release.",
        CaptureSource::Mirror,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;
    seed_captured(
        &store,
        &ctx,
        "build command",
        "Build with cargo build release.",
        CaptureSource::Summary,
        CaptureLadder::Captured,
        vec![],
        UnitState::Candidate,
    )
    .await;

    let svc = service(store.clone());
    // First cross-check: source agreement promotes to corroborated/active.
    svc.run_capture_crosscheck(&ctx).await.expect("crosscheck");
    // A second, DIFFERENT family: a positive weak-outcome on the served unit.
    let (recalled, trace_id) = recall_ids(&svc, &ctx, "cargo build release").await;
    assert!(recalled.contains(&a));
    mark(&store, &ctx, trace_id, &[a], MarkOutcome::Success).await;
    svc.run_capture_crosscheck(&ctx).await.expect("crosscheck");

    let marker = fetch(&store, &ctx, a).await.capture.unwrap();
    assert_eq!(
        marker.ladder,
        CaptureLadder::Durable,
        "two distinct witness families reach durable"
    );
    assert_eq!(marker.distinct_witness_count(), 2);
}

/// Regression: the capture write INGRESS (`remember`) still refuses to resurrect
/// a forgotten identity through an open invalidation tombstone.
#[tokio::test]
async fn recaptured_forgotten_identity_does_not_resurrect() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_040)).await;
    let svc = service(store.clone());

    let remember = |trigger: &str| RememberRequest {
        kind: MemoryKind::Procedural,
        body: format!("Compact body for {trigger}."),
        trigger: trigger.to_string(),
        verification: "the check passes".to_string(),
        target_scope_id: None,
        valid_from: None,
        valid_to: None,
        source: source(),
    };

    let created = svc
        .remember(
            &ctx,
            "cap-nr-1",
            TrustLevel::TrustedUser,
            remember("restart the worker"),
        )
        .await
        .expect("remember");
    let old_id: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(created.body()).unwrap();
    let old_id = old_id.unit_ids[0];

    svc.invalidate_memory(
        &ctx,
        "cap-nr-inv",
        InvalidateMemoryRequest {
            memory_unit_id: old_id,
            reason_kind: InvalidationReason::Harmful,
            reason: "this advice caused an outage".to_string(),
            source: source(),
        },
    )
    .await
    .expect("invalidate");

    let resurrect = svc
        .remember(
            &ctx,
            "cap-nr-2",
            TrustLevel::TrustedUser,
            remember("restart the worker"),
        )
        .await;
    assert!(
        resurrect.is_err(),
        "open tombstone blocks re-capture of the same identity"
    );

    // CONTROL: a distinct identity is not blocked.
    let other = svc
        .remember(
            &ctx,
            "cap-nr-3",
            TrustLevel::TrustedUser,
            remember("rotate the token"),
        )
        .await;
    assert!(other.is_ok(), "a different identity is not blocked");
}

/// Regression: a corroborated captured belief never passes the high-risk trust
/// floor, and an agent-sourced preference is refused (source-kind gate).
#[tokio::test]
async fn capture_respects_trust_floor_and_preference_source_gate() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_050)).await;
    let svc = service(store.clone());

    let unit = seed_captured(
        &store,
        &ctx,
        "deploy token",
        "The deploy token rotates every Friday.",
        CaptureSource::Mirror,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;

    // High-risk action query: even a corroborated captured belief is below the
    // trust floor and is dropped.
    let (high_risk, _) = recall_ids(&svc, &ctx, "how to bribe the deploy token holder").await;
    assert!(
        !high_risk.contains(&unit),
        "captured belief is below the high-risk trust floor: {high_risk:?}"
    );
    // CONTROL: a benign query recalls the very same unit — the floor is the
    // reason, not the unit's absence.
    let (benign, _) = recall_ids(&svc, &ctx, "when does the deploy token rotate").await;
    assert!(
        benign.contains(&unit),
        "benign recall serves the unit: {benign:?}"
    );

    // Preference source-kind gate: an agent-inferred preference is refused.
    let mut inferred = RememberRequest {
        kind: MemoryKind::Preference,
        body: "Prefer trunk-based development.".to_string(),
        trigger: "branching strategy".to_string(),
        verification: "the team agreed".to_string(),
        target_scope_id: None,
        valid_from: None,
        valid_to: None,
        source: source(),
    };
    inferred.source.kind = "agent".to_string();
    assert!(
        svc.remember(&ctx, "cap-pref", TrustLevel::TrustedUser, inferred)
            .await
            .is_err(),
        "an agent-sourced preference is refused"
    );
}

/// Regression: a quarantined captured unit is never recalled; a corroborated
/// one on the same subject/body IS — the recall-exclusion control.
#[tokio::test]
async fn quarantined_capture_unit_is_never_recalled() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(91_060)).await;
    let svc = service(store.clone());

    let quarantined = seed_captured(
        &store,
        &ctx,
        "release step",
        "Tag the release and push the changelog.",
        CaptureSource::Summary,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Quarantined,
    )
    .await;
    let (recalled, _) = recall_ids(&svc, &ctx, "tag the release changelog").await;
    assert!(
        !recalled.contains(&quarantined),
        "quarantined unit is recall-excluded: {recalled:?}"
    );

    // CONTROL: the same content at Active/Corroborated IS recalled.
    let active = seed_captured(
        &store,
        &ctx,
        "release step",
        "Tag the release and push the changelog.",
        CaptureSource::Mirror,
        CaptureLadder::Corroborated,
        vec![CaptureWitness::SourceAgreement],
        UnitState::Active,
    )
    .await;
    let (recalled2, _) = recall_ids(&svc, &ctx, "tag the release changelog").await;
    assert!(
        recalled2.contains(&active),
        "active corroborated unit is recallable: {recalled2:?}"
    );
}
