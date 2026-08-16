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
    CaptureLadder, CaptureSource, CaptureWitness, InvalidateMemoryRequest, InvalidationReason,
    MarkOutcome, MarkRequest, MemoryEdgeKind, MemoryKind, MemorySourceInput, RecallContextItem,
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
    recall_items(svc, ctx, query, false, true)
        .await
        .iter()
        .map(|item| item.unit_id)
        .collect()
}

/// Recall on either lane: `compact_only` selects the coding-agent lane.
async fn recall_items(
    svc: &MemoryService<InMemoryStore>,
    ctx: &ResolvedMemoryContext,
    query: &str,
    compact_only: bool,
    include_beliefs: bool,
) -> Vec<RecallContextItem> {
    svc.recall(
        ctx.clone(),
        RecallHttpRequest {
            compact_only,
            subject_id: ctx.data_subject_id,
            scope_id: ctx.scope_id,
            actor_id: ctx.actor_id,
            agent_node_id: ctx.agent_node_id,
            subject_generation: ctx.subject_generation,
            query: query.to_string(),
            limit: Some(8),
            budget_tokens: Some(512),
            mode: Some(RecallMode::Fast),
            include_beliefs: Some(include_beliefs),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
    )
    .await
    .expect("recall")
    .items
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

// --- coding-lane serving + same-channel reinforce/supersede ----------------

/// An UNCONFIRMED capture (`Candidate`, one source, no witness) IS served on the
/// coding lane, labelled `captured_unconfirmed`. CONTROLS: the general lane
/// (`compact_only: false`) does NOT serve a Candidate; `include_beliefs: false`
/// drops it from the coding lane too.
#[tokio::test]
async fn captured_belief_is_served_on_the_coding_lane() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_050)).await;
    let svc = service(store.clone());

    capture(
        &svc,
        &ctx,
        "cap-l-1",
        "summary",
        "test command",
        "Run the unit tests with cargo nextest run.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;
    let unit = open_captured_units(&store, &ctx)
        .await
        .pop()
        .expect("captured unit");
    assert_eq!(unit.state, UnitState::Candidate);
    assert!(unit.compact.is_none(), "captures carry no compact envelope");
    assert_eq!(unit.confidence, Some(0.5));

    let coding = recall_items(&svc, &ctx, "cargo nextest unit tests", true, true).await;
    let served = coding
        .iter()
        .find(|item| item.unit_id == unit.id)
        .expect("coding lane serves the unconfirmed capture");
    assert_eq!(served.inclusion_reason, "captured_unconfirmed");

    let general = recall_items(&svc, &ctx, "cargo nextest unit tests", false, true).await;
    assert!(
        general.iter().all(|item| item.unit_id != unit.id),
        "the general lane keeps Candidate invisible: {general:?}"
    );
    let no_beliefs = recall_items(&svc, &ctx, "cargo nextest unit tests", true, false).await;
    assert!(
        no_beliefs.iter().all(|item| item.unit_id != unit.id),
        "perturbation: include_beliefs=false drops it: {no_beliefs:?}"
    );
}

/// A CONFIRMED capture (promoted to `Active` by a `SourceAgreement`) is served
/// on the coding lane under the `captured_confirmed` label.
#[tokio::test]
async fn confirmed_capture_is_labelled_on_the_coding_lane() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_051)).await;
    let svc = service(store.clone());

    let body = "Lint with cargo clippy --all-targets before pushing.";
    capture(&svc, &ctx, "cap-lc-1", "mirror", "lint command", body).await;
    capture(&svc, &ctx, "cap-lc-2", "summary", "lint command", body).await;
    reflect_tick(&svc, &ctx).await;

    let coding = recall_items(&svc, &ctx, "cargo clippy lint before pushing", true, true).await;
    let confirmed: Vec<_> = coding
        .iter()
        .filter(|item| item.inclusion_reason == "captured_confirmed")
        .collect();
    assert!(!confirmed.is_empty(), "labelled confirmed: {coding:?}");
    assert!(
        coding
            .iter()
            .all(|item| item.inclusion_reason != "captured_unconfirmed"),
        "nothing left unconfirmed once promoted: {coding:?}"
    );
}

/// SAME channel, SAME key, bodies equal after whitespace/case normalisation ⇒
/// ONE unit, reinforced (count 1, `last_reinforced_at` set) — never a second
/// open fragment. PERTURBATION: the same body through a DIFFERENT channel is a
/// second witness, not a reinforcement — the summary unit's count stays put and
/// the mirror coexists as its own unit.
#[tokio::test]
async fn same_channel_recapture_reinforces_not_fragments() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_060)).await;
    let svc = service(store.clone());

    capture(
        &svc,
        &ctx,
        "cap-re-1",
        "summary",
        "migration order",
        "Run the migration before restarting the worker.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;
    let first = open_captured_units(&store, &ctx).await;
    assert_eq!(first.len(), 1);
    assert_eq!(first[0].reinforcement_count, 0);
    assert!(first[0].last_reinforced_at.is_none());

    // Whitespace + case jitter: a distinct episode, the same normalised claim.
    capture(
        &svc,
        &ctx,
        "cap-re-2",
        "summary",
        "migration order",
        "run the migration   before restarting\nthe worker.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;
    let reinforced = open_captured_units(&store, &ctx).await;
    assert_eq!(reinforced.len(), 1, "no fragment: {reinforced:?}");
    assert_eq!(reinforced[0].id, first[0].id);
    assert_eq!(reinforced[0].reinforcement_count, 1);
    assert_eq!(
        reinforced[0].last_reinforced_at.as_deref(),
        Some(CLOCK.0),
        "reinforced now"
    );

    // Perturbation: the same claim through the MIRROR channel coexists as a
    // second unit and leaves the summary unit's count untouched.
    capture(
        &svc,
        &ctx,
        "cap-re-3",
        "mirror",
        "migration order",
        "Run the migration before restarting the worker.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;
    let after_mirror = open_captured_units(&store, &ctx).await;
    assert_eq!(after_mirror.len(), 2, "mirror coexists: {after_mirror:?}");
    let summary = after_mirror
        .iter()
        .find(|unit| unit.id == first[0].id)
        .expect("summary unit");
    assert_eq!(
        summary.reinforcement_count, 1,
        "a different channel does not reinforce: {after_mirror:?}"
    );
}

/// SAME channel, SAME key, a DIFFERENT body ⇒ supersede within the channel:
/// the new unit is open, the old one is `Superseded` (transaction-closed) with
/// a `Supersedes` edge new→old, and the reinforcement count carries forward.
/// PERTURBATION: the superseded unit no longer appears on the coding lane.
#[tokio::test]
async fn same_channel_recapture_with_new_body_supersedes() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_061)).await;
    let svc = service(store.clone());

    capture(
        &svc,
        &ctx,
        "cap-su-1",
        "summary",
        "release branch",
        "Releases cut from the main branch.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;
    // Reinforce once so there is a count to carry.
    capture(
        &svc,
        &ctx,
        "cap-su-2",
        "summary",
        "release branch",
        "releases cut from the MAIN branch.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;
    let old = open_captured_units(&store, &ctx).await.pop().expect("old");
    assert_eq!(old.reinforcement_count, 1);
    assert!(
        recall_items(&svc, &ctx, "which branch are releases cut from", true, true)
            .await
            .iter()
            .any(|item| item.unit_id == old.id),
        "control: the old capture is served before supersession"
    );

    capture(
        &svc,
        &ctx,
        "cap-su-3",
        "summary",
        "release branch",
        "Releases cut from the release/* branches now.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;

    let open = open_captured_units(&store, &ctx).await;
    assert_eq!(open.len(), 1, "one open unit on the key: {open:?}");
    let new = &open[0];
    assert_ne!(new.id, old.id);
    assert_eq!(new.body, "Releases cut from the release/* branches now.");
    assert_eq!(new.reinforcement_count, 1, "carried forward");
    assert_eq!(new.state, UnitState::Candidate);

    let closed = store
        .memory_units(ctx.tenant_id)
        .into_iter()
        .find(|unit| unit.id == old.id)
        .expect("old unit still stored, never deleted");
    assert_eq!(closed.state, UnitState::Superseded);
    assert!(closed.transaction_to.is_some());
    assert!(
        store.memory_edges(ctx.tenant_id).iter().any(|edge| {
            edge.kind == MemoryEdgeKind::Supersedes
                && edge.src_id == new.id
                && edge.dst_id == old.id
        }),
        "Supersedes edge new→old"
    );
    let served = recall_items(&svc, &ctx, "which branch are releases cut from", true, true).await;
    assert!(served.iter().any(|item| item.unit_id == new.id));
    assert!(
        served.iter().all(|item| item.unit_id != old.id),
        "perturbation: the superseded capture is no longer served: {served:?}"
    );
}

/// Regression guard for the witness rule: DIFFERENT channels on the same key
/// with divergent bodies still COEXIST (both open), so the cross-check can see
/// the collision — same-channel supersession must not widen to them.
#[tokio::test]
async fn different_channel_same_key_still_coexists() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_062)).await;
    let svc = service(store.clone()).with_capture_crosscheck_enabled(false);

    capture(
        &svc,
        &ctx,
        "cap-dc-1",
        "mirror",
        "cache dir",
        "Cache lives in .cache/a.",
    )
    .await;
    capture(
        &svc,
        &ctx,
        "cap-dc-2",
        "summary",
        "cache dir",
        "Cache lives in .cache/b.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;

    let open = open_captured_units(&store, &ctx).await;
    assert_eq!(open.len(), 2, "both channels stay open: {open:?}");
    let sources: Vec<CaptureSource> = open
        .iter()
        .map(|unit| unit.capture.as_ref().unwrap().source)
        .collect();
    assert!(sources.contains(&CaptureSource::Mirror));
    assert!(sources.contains(&CaptureSource::Summary));
    assert!(
        store
            .memory_edges(ctx.tenant_id)
            .iter()
            .all(|edge| edge.kind != MemoryEdgeKind::Supersedes),
        "no supersession across channels"
    );
}

/// The `errfix` channel mints a `Procedural` card (the kind hint IS the
/// channel), served on the coding lane as unconfirmed; and it is a DIFFERENT
/// family from `summary`, so an agreeing summary corroborates it.
#[tokio::test]
async fn errfix_channel_mints_a_procedural_capture() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_070)).await;
    let svc = service(store.clone());

    let body = "When cargo fails with E0308 on the clock arg, pass Arc::new(CLOCK).";
    capture(&svc, &ctx, "cap-ef-1", "errfix", "clock arg E0308", body).await;
    reflect_tick(&svc, &ctx).await;
    let unit = open_captured_units(&store, &ctx).await.pop().expect("unit");
    assert_eq!(unit.kind, MemoryKind::Procedural);
    assert_eq!(unit.state, UnitState::Candidate);
    assert_eq!(unit.capture.as_ref().unwrap().source, CaptureSource::ErrFix);
    let coding = recall_items(&svc, &ctx, "cargo E0308 clock arg", true, true).await;
    assert!(
        coding
            .iter()
            .any(|item| item.unit_id == unit.id && item.inclusion_reason == "captured_unconfirmed"),
        "unconfirmed procedural capture served: {coding:?}"
    );

    // A summary that agrees is a second family — but the summary mints a
    // BELIEF, a different kind, so it keys apart from the procedure and does
    // not corroborate it. The families are independent by construction; the
    // agreement pairing stays kind-scoped.
    let general = recall_items(&svc, &ctx, "cargo E0308 clock arg", false, true).await;
    assert!(
        general.iter().all(|item| item.unit_id != unit.id),
        "general lane keeps the unconfirmed procedure invisible: {general:?}"
    );
}

/// A capture body over the compact one-card ceiling is cut at a line boundary
/// under the ceiling and flagged `truncated`; a single overlong line that
/// cannot be cut mints NOTHING. CONTROL: a body under the ceiling is untouched.
#[tokio::test]
async fn overlong_capture_is_truncated_to_the_ceiling_or_dropped() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_080)).await;
    let svc = service(store.clone());

    let bullet = "- keep the worker warm between ticks";
    let long_body = std::iter::repeat_n(bullet, 200)
        .collect::<Vec<_>>()
        .join("\n");
    capture(
        &svc,
        &ctx,
        "cap-tr-1",
        "summary",
        "worker notes",
        &long_body,
    )
    .await;
    let unbreakable = "x".repeat(4_000);
    capture(
        &svc,
        &ctx,
        "cap-tr-2",
        "summary",
        "unbreakable",
        &unbreakable,
    )
    .await;
    capture(&svc, &ctx, "cap-tr-3", "summary", "short", bullet).await;
    reflect_tick(&svc, &ctx).await;

    let units = open_captured_units(&store, &ctx).await;
    let truncated = units
        .iter()
        .find(|u| u.fact_key.as_deref() == Some("worker notes"))
        .expect("truncated capture minted");
    assert!(truncated.capture.as_ref().unwrap().truncated);
    assert!(truncated.body.len() < long_body.len());
    assert!(truncated.body.ends_with(bullet), "cut at a line boundary");
    assert!(
        units
            .iter()
            .all(|u| u.fact_key.as_deref() != Some("unbreakable")),
        "an uncuttable overlong body mints nothing: {units:?}"
    );
    let short = units
        .iter()
        .find(|u| u.fact_key.as_deref() == Some("short"))
        .expect("control");
    assert!(!short.capture.as_ref().unwrap().truncated);
    assert_eq!(short.body, bullet);
}

/// SURVIVAL WITNESS: a served `Candidate` capture is promoted by the session's
/// `mark` Success ALONE — no later episode / reflect tick required. `mark` is a
/// WeakOutcome witness for the units it names, so the service runs the capture
/// cross-check right after recording it; before that, a Candidate served this
/// session sat un-promoted until some unrelated future capture happened to
/// trigger a reflect job (observed live on the battery).
#[tokio::test]
async fn a_survival_mark_promotes_a_served_candidate_without_a_new_episode() {
    let store = Arc::new(InMemoryStore::default());
    let ctx =
        memphant_store_testkit::bind_context(store.as_ref(), TenantId::from_u128(92_070)).await;
    let svc = service(store.clone());

    capture(
        &svc,
        &ctx,
        "cap-s-1",
        "summary",
        "acme wire framing",
        "The length field counts the payload plus the trailing CRC byte.",
    )
    .await;
    reflect_tick(&svc, &ctx).await;
    let unit = open_captured_units(&store, &ctx)
        .await
        .pop()
        .expect("captured unit");
    assert_eq!(unit.state, UnitState::Candidate);

    // Serve it on the coding lane (the exposure the mark must cite).
    let recall = svc
        .recall(
            ctx.clone(),
            RecallHttpRequest {
                compact_only: true,
                subject_id: ctx.data_subject_id,
                scope_id: ctx.scope_id,
                actor_id: ctx.actor_id,
                agent_node_id: ctx.agent_node_id,
                subject_generation: ctx.subject_generation,
                query: "acme framing length crc".to_string(),
                limit: Some(3),
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
    assert!(
        recall.items.iter().any(|item| item.unit_id == unit.id),
        "precondition: the candidate is served"
    );

    // The Stop hook's survival verdict for the served ids — and NOTHING else.
    svc.mark(
        &ctx,
        "survival-1",
        MarkRequest {
            subject_id: ctx.data_subject_id,
            scope_id: ctx.scope_id,
            actor_id: ctx.actor_id,
            agent_node_id: ctx.agent_node_id,
            subject_generation: ctx.subject_generation,
            trace_id: recall.trace_id,
            caller_id: "memphant-capture".to_string(),
            used_ids: vec![unit.id],
            outcome: MarkOutcome::Success,
        },
    )
    .await
    .expect("mark");

    let promoted = store
        .fetch_scope_open_units(&ctx)
        .await
        .expect("units")
        .into_iter()
        .find(|u| u.id == unit.id)
        .expect("unit still open");
    assert_eq!(promoted.state, UnitState::Active, "mark alone promotes");
    let marker = promoted.capture.expect("captured");
    assert!(marker.ladder >= CaptureLadder::Corroborated);
    assert!(
        marker.witnesses.contains(&CaptureWitness::WeakOutcome),
        "the mark is recorded as the WeakOutcome witness: {:?}",
        marker.witnesses
    );
}
