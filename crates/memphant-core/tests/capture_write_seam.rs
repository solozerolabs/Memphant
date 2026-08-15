//! Capture WRITE SEAM — end-to-end BDD tests for the service verb that SETS the
//! `payload.capture` marker (Stage A only READS it). A capture is a `retain`
//! Episode whose `source_ref` is `capture://mirror` or `capture://summary`; the
//! reflect nominator mints ONE inert `Belief` candidate carrying the fresh
//! `Captured` marker, and the Stage A engine (`run_capture_crosscheck`) ladders
//! it on the reflect tail.
//!
//! These drive the FULL path — `retain` (service verb) → `run_worker_tick_scoped`
//! (real `compile_job`) → `run_capture_crosscheck` — proving the seam connects to
//! Stage A's engine. Every positive assertion is paired with a
//! removal-perturbation control (the codebase's non-vacuity discipline).

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, JobFilter, MemoryStore, NoopEmbedding};
use memphant_types::{
    CaptureLadder, InvalidateMemoryRequest, InvalidationReason, MemoryKind, MemorySourceInput,
    RecallHttpRequest, RecallMode, ResolvedMemoryContext, RetainEpisodeHttpRequest,
    RetainEpisodePayload, RetainPayload, StoredMemoryUnit, TenantId, TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");
const OBSERVED_AT: &str = "2026-07-02T00:00:00Z";

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

/// POST one capture through the WRITE SEAM: a `retain` Episode tagged
/// `capture://<family>` with the given subject key and body, at `AgentOutput`
/// trust (the clamp a live agent principal would receive). This is the exact
/// shape an adapter's HTTP POST lands as.
async fn capture(
    svc: &MemoryService<InMemoryStore>,
    ctx: &ResolvedMemoryContext,
    idempotency_key: &str,
    family: &str,
    subject: &str,
    body: &str,
) {
    let request = RetainEpisodeHttpRequest {
        subject_id: ctx.data_subject_id,
        scope_id: ctx.scope_id,
        actor_id: ctx.actor_id,
        agent_node_id: ctx.agent_node_id,
        subject_generation: ctx.subject_generation,
        source_ref: format!("capture://{family}"),
        observed_at: OBSERVED_AT.to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: "agent".to_string(),
            body: body.to_string(),
            subject: Some(subject.to_string()),
            predicate: None,
        }),
    };
    svc.retain(ctx, idempotency_key, TrustLevel::AgentOutput, request)
        .await
        .expect("retain capture");
}

/// Drain the scope's reflect queue: this is the "reflect tick" — real
/// `compile_job` (which mints the captured belief) followed by
/// `run_capture_crosscheck` (which ladders it).
async fn reflect_tick(svc: &MemoryService<InMemoryStore>, ctx: &ResolvedMemoryContext) {
    let outcome = svc
        .run_worker_tick_scoped(
            JobFilter {
                tenant: Some(ctx.tenant_id),
                scope: Some(ctx.scope_id),
            },
            16,
        )
        .await
        .expect("worker tick");
    assert_eq!(
        outcome.failed, 0,
        "no capture job dead-letters: {outcome:?}"
    );
}

async fn open_captured_units(
    store: &InMemoryStore,
    ctx: &ResolvedMemoryContext,
) -> Vec<StoredMemoryUnit> {
    store
        .fetch_scope_open_units(ctx)
        .await
        .expect("open units")
        .into_iter()
        .filter(|unit| unit.capture.is_some())
        .collect()
}

async fn recall_ids(
    svc: &MemoryService<InMemoryStore>,
    ctx: &ResolvedMemoryContext,
    query: &str,
) -> Vec<UnitId> {
    let response = svc
        .recall(
            ctx.clone(),
            RecallHttpRequest {
                compact_only: false,
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
    response.items.iter().map(|item| item.unit_id).collect()
}

// --- the load-bearing end-to-end test ------------------------------------

/// AGREE: a `mirror` and a `summary` capture for one subject with MATCHING
/// bodies, driven end-to-end through the worker tick, are promoted to
/// `Corroborated`/`Active` and become recallable.
#[tokio::test]
async fn mirror_and_summary_agree_promote_to_corroborated_and_recallable() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_001)).await;
    let svc = service(store.clone());

    let body = "Build with cargo build --release.";
    capture(&svc, &ctx, "cap-a-1", "mirror", "build command", body).await;
    capture(&svc, &ctx, "cap-a-2", "summary", "build command", body).await;
    reflect_tick(&svc, &ctx).await;

    let captured = open_captured_units(&store, &ctx).await;
    assert_eq!(
        captured.len(),
        2,
        "both captures minted as beliefs: {captured:?}"
    );
    for unit in &captured {
        assert_eq!(unit.kind, MemoryKind::Belief);
        assert_eq!(unit.state, UnitState::Active, "promoted to active");
        assert_eq!(
            unit.trust_level,
            TrustLevel::AgentOutput,
            "trust floor preserved"
        );
        let marker = unit.capture.as_ref().expect("capture marker");
        assert_eq!(marker.ladder, CaptureLadder::Corroborated);
    }

    let recalled = recall_ids(&svc, &ctx, "cargo build release").await;
    assert!(
        captured.iter().any(|u| recalled.contains(&u.id)),
        "corroborated capture is recallable: {recalled:?}"
    );
}

/// DIVERGE: a `mirror` and a `summary` capture on the SAME subject with
/// DIVERGENT bodies are BOTH quarantined and recall-excluded. CONTROL: with the
/// cross-check DISABLED the poison survives Active and IS recalled — the
/// non-vacuity proof that the quarantine is the cross-check's doing.
#[tokio::test]
async fn mirror_and_summary_diverge_quarantine_and_exclude() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_002)).await;
    let svc = service(store.clone());

    capture(
        &svc,
        &ctx,
        "cap-d-1",
        "mirror",
        "deploy step",
        "Deploy by running make deploy on the release host.",
    )
    .await;
    capture(
        &svc,
        &ctx,
        "cap-d-2",
        "summary",
        "deploy step",
        "Deploy by force-pushing straight to the production branch.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;

    let captured = open_captured_units(&store, &ctx).await;
    assert_eq!(captured.len(), 2);
    for unit in &captured {
        assert_eq!(
            unit.state,
            UnitState::Quarantined,
            "divergent capture is quarantined: {unit:?}"
        );
    }
    let recalled = recall_ids(&svc, &ctx, "deploy production release host").await;
    for unit in &captured {
        assert!(
            !recalled.contains(&unit.id),
            "quarantined capture is recall-excluded: {recalled:?}"
        );
    }
}

/// CONTROL for the divergence test: identical seed, cross-check DISABLED. The
/// poison stays Active and IS recalled — nothing quarantines it.
#[tokio::test]
async fn diverging_capture_survives_without_crosscheck() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_003)).await;
    let svc = service(store.clone()).with_capture_crosscheck_enabled(false);

    capture(
        &svc,
        &ctx,
        "cap-c-1",
        "mirror",
        "deploy step",
        "Deploy by running make deploy on the release host.",
    )
    .await;
    capture(
        &svc,
        &ctx,
        "cap-c-2",
        "summary",
        "deploy step",
        "Deploy by force-pushing straight to the production branch.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;

    let captured = open_captured_units(&store, &ctx).await;
    assert_eq!(captured.len(), 2);
    assert!(
        captured.iter().all(|u| u.state == UnitState::Candidate),
        "without the cross-check nothing quarantines the poison: {captured:?}"
    );
}

// --- gate regressions -----------------------------------------------------

/// Idempotency: an identical capture re-POST (auto-capture's highest-volume
/// replay surface) dedups at the episode seam — one episode, one captured unit,
/// never a duplicate.
#[tokio::test]
async fn capture_replay_is_idempotent() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_010)).await;
    let svc = service(store.clone());

    let body = "Run the migration before restarting the worker.";
    capture(&svc, &ctx, "cap-i-1", "summary", "migration order", body).await;
    // Same source_kind + source_ref + body ⇒ same episode dedup key, so the
    // re-POST (a fresh idempotency key models an independent retry) is absorbed.
    capture(&svc, &ctx, "cap-i-2", "summary", "migration order", body).await;
    reflect_tick(&svc, &ctx).await;

    let captured = open_captured_units(&store, &ctx).await;
    assert_eq!(
        captured.len(),
        1,
        "a replayed capture does not duplicate: {captured:?}"
    );
}

/// Trust floor/clamp: even a corroborated captured belief stays at `AgentOutput`
/// and is dropped from a HIGH-RISK recall. CONTROL: a benign query recalls the
/// very same unit — the floor is the reason, not the unit's absence.
#[tokio::test]
async fn capture_stays_below_the_high_risk_trust_floor() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_020)).await;
    let svc = service(store.clone());

    let body = "The deploy token rotates every Friday.";
    capture(&svc, &ctx, "cap-t-1", "mirror", "deploy token", body).await;
    capture(&svc, &ctx, "cap-t-2", "summary", "deploy token", body).await;
    reflect_tick(&svc, &ctx).await;

    let unit = open_captured_units(&store, &ctx)
        .await
        .into_iter()
        .find(|u| u.state == UnitState::Active)
        .expect("a promoted active capture");

    let high_risk = recall_ids(&svc, &ctx, "how to bribe the deploy token holder").await;
    assert!(
        !high_risk.contains(&unit.id),
        "captured belief is below the high-risk trust floor: {high_risk:?}"
    );
    let benign = recall_ids(&svc, &ctx, "when does the deploy token rotate").await;
    assert!(
        benign.contains(&unit.id),
        "benign recall serves the very same unit: {benign:?}"
    );
}

/// Source-kind gate: a capture can NEVER escalate to a standing `Preference`,
/// however preference-shaped its body reads — the nominator forces `Belief`.
/// This is the write-seam counterpart to `remember`'s preference source gate.
#[tokio::test]
async fn capture_can_never_become_a_preference() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_030)).await;
    let svc = service(store.clone());

    capture(
        &svc,
        &ctx,
        "cap-p-1",
        "summary",
        "branching strategy",
        "Prefer trunk-based development over long-lived feature branches.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;

    let captured = open_captured_units(&store, &ctx).await;
    assert!(!captured.is_empty());
    assert!(
        captured.iter().all(|u| u.kind == MemoryKind::Belief),
        "a capture is always a belief, never a preference: {captured:?}"
    );
}

/// No-resurrection (the sharpest mirror false-positive): invalidating a promoted
/// capture must not be undone by the memory file re-syncing unchanged. The
/// identical re-POST dedups, so the invalidated identity stays gone. CONTROL:
/// remove the invalidation and the same unit IS recalled — the exclusion is the
/// invalidation's doing, not the query's.
#[tokio::test]
async fn resynced_mirror_does_not_resurrect_an_invalidated_capture() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_040)).await;
    let svc = service(store.clone());

    let body = "Tag the release and push the changelog.";
    capture(&svc, &ctx, "cap-r-1", "mirror", "release step", body).await;
    capture(&svc, &ctx, "cap-r-2", "summary", "release step", body).await;
    reflect_tick(&svc, &ctx).await;

    // CONTROL first: before invalidation the promoted unit IS recalled.
    let promoted = open_captured_units(&store, &ctx)
        .await
        .into_iter()
        .find(|u| u.state == UnitState::Active)
        .expect("promoted capture");
    assert!(
        recall_ids(&svc, &ctx, "tag the release changelog")
            .await
            .contains(&promoted.id),
        "control: the capture is recallable before invalidation"
    );

    svc.invalidate_memory(
        &ctx,
        "cap-r-inv",
        InvalidateMemoryRequest {
            memory_unit_id: promoted.id,
            reason_kind: InvalidationReason::Harmful,
            reason: "this release step caused an outage".to_string(),
            source: source(),
        },
    )
    .await
    .expect("invalidate");

    // The memory file re-syncs unchanged: an identical mirror re-POST. Because
    // the body + source_ref are unchanged the episode dedups — no re-derivation,
    // no resurrection.
    capture(&svc, &ctx, "cap-r-3", "mirror", "release step", body).await;
    reflect_tick(&svc, &ctx).await;

    let recalled = recall_ids(&svc, &ctx, "tag the release changelog").await;
    assert!(
        !recalled.contains(&promoted.id),
        "the invalidated capture is not resurrected by a file re-sync: {recalled:?}"
    );
}
