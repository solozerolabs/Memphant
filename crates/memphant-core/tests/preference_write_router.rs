//! Spec 04 §13.1/§13.2a — the typed write-router's preference arm.
//!
//! These are regressions for a measured defect, not speculative coverage. The
//! preference lane's first measurement (2026-08-01, MemoryCode, 1063 probes)
//! found supersession **unreachable**: 8147 units, all `active`, all
//! `episodic`, zero edges of any kind. Two locks caused it — the supersedence
//! branch is gated on an explicit subject, and past that gate it required
//! `kind == Semantic`. A user-declared standing constraint had no kind to be
//! stored as, so it could never close a generation.
//!
//! Each test below fails on the pre-router tree.

use memphant_core::{
    FixedClock, InMemoryStore, NoopEmbedding, recall, reflect_recorded, retain_episode,
};
use memphant_types::{
    AdmissionAction, MemoryEdgeKind, MemoryKind, RecallMode, RecallRequest, ReflectCandidate,
    ReflectInput, ResolvedMemoryContext, RetainRequest, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-31T00:00:00Z");
const COMPILER: &str = "compiler-preference-router";

/// Retains one episode and compiles exactly one candidate of `kind` carrying
/// an EXPLICIT subject/predicate. Returns the admission action the router
/// chose, which is the whole observable of this stage.
async fn declare(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    index: usize,
    kind: MemoryKind,
    subject: &str,
    body: &str,
) -> AdmissionAction {
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
            source_kind: "user".to_string(),
            source_ref: format!("test:preference:{index}"),
            observed_at: "2026-07-31T00:00:00Z".to_string(),
            source_trust: TrustLevel::TrustedUser,
            subject_hint: Some(subject.to_string()),
            subject: None,
            predicate: None,
            body: body.to_string(),
            compiler_version: COMPILER.to_string(),
        },
    )
    .await
    .expect("retain succeeds");
    let job = store
        .reflect_jobs(context.tenant_id)
        .last()
        .cloned()
        .expect("reflect job queued");
    let (trace, _) = reflect_recorded(
        store,
        ReflectInput {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_ref: format!("test:reflect:{index}"),
            observed_at: "2026-07-31T00:00:00Z".to_string(),
            source_body: None,
            episode_id: Some(retained.episode_id),
            resource_id: None,
            job_id: job.id,
            compiler_version: COMPILER.to_string(),
            candidates: vec![ReflectCandidate {
                source_kind: "user".to_string(),
                trust_level: TrustLevel::TrustedUser,
                actor_id: context.actor_id,
                subject: Some(subject.to_string()),
                predicate: Some("prefers".to_string()),
                fact_key: None,
                kind: Some(kind),
                body: body.to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: None,
                target_unit_ids: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
            }],
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("reflect succeeds");
    trace
        .actions
        .last()
        .copied()
        .expect("one admission action per candidate")
}

/// The crux. A restated preference on the same subject must close the prior
/// chain head's generation (§13.1 `preference_arm`, §7.3a), leaving exactly one
/// open head and a citable superseded generation behind it.
#[tokio::test]
async fn a_restated_preference_supersedes_the_prior_chain_head() {
    let store = InMemoryStore::default();
    let tenant = TenantId::from_u128(90_001);
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    let first = declare(
        &store,
        &context,
        0,
        MemoryKind::Preference,
        "function argument name prefix",
        "always start function argument names with 'e_'",
    )
    .await;
    let second = declare(
        &store,
        &context,
        1,
        MemoryKind::Preference,
        "function argument name prefix",
        "always start function argument names with 'y_'",
    )
    .await;
    assert_eq!(first, AdmissionAction::Append);
    assert_eq!(
        second,
        AdmissionAction::Supersede,
        "the second declaration must close the first generation, not append beside it"
    );

    let units = store.memory_units(tenant);
    let preferences: Vec<_> = units
        .iter()
        .filter(|unit| unit.kind == MemoryKind::Preference)
        .collect();
    // The §7.3a close-generation shape, identical to the semantic path: the
    // prior head's transaction generation is closed, its still-true history is
    // re-INSERTed as a valid-time-closed rectangle, and the new head opens. So
    // three rows, and the retired content stays citable rather than being
    // overwritten in place.
    assert_eq!(preferences.len(), 3, "the retired generation stays citable");

    let retired: Vec<_> = preferences
        .iter()
        .filter(|unit| unit.state == UnitState::Superseded)
        .collect();
    assert_eq!(retired.len(), 1);
    assert!(
        retired[0].body.contains("'e_'"),
        "the OLDER declaration is the one retired"
    );
    assert!(
        retired[0].transaction_to.is_some(),
        "RW-4: the closed generation carries a transaction_to, or bitemporal \
         recall would still serve it"
    );

    // §13.1: `chain_head(k)` is derived, not a column — the unique open,
    // active generation on the key. There must be exactly one.
    // §13.1 defines the chain head on transaction time; the valid-time bound is
    // what separates the open head from the historical rectangle the close
    // minted beside it, so both axes are required to name exactly one.
    let heads: Vec<_> = preferences
        .iter()
        .filter(|unit| {
            unit.state == UnitState::Active
                && unit.transaction_to.is_none()
                && unit.valid_to.is_none()
        })
        .collect();
    assert_eq!(heads.len(), 1, "exactly one chain head per preference key");
    assert!(heads[0].body.contains("'y_'"));

    let edges = store.memory_edges(tenant);
    assert!(
        edges
            .iter()
            .any(|edge| edge.kind == MemoryEdgeKind::Supersedes
                && edge.src_id == heads[0].id
                && edge.dst_id == retired[0].id),
        "supersession must mint an edge: the measured run had zero edges of any kind"
    );
}

/// The read-side consequence. A retired preference must not come back.
#[tokio::test]
async fn recall_serves_the_live_preference_and_not_the_retired_one() {
    let store = InMemoryStore::default();
    let tenant = TenantId::from_u128(90_002);
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    declare(
        &store,
        &context,
        0,
        MemoryKind::Preference,
        "function argument name prefix",
        "always start function argument names with 'e_'",
    )
    .await;
    declare(
        &store,
        &context,
        1,
        MemoryKind::Preference,
        "function argument name prefix",
        "always start function argument names with 'y_'",
    )
    .await;

    let response = recall(
        &store,
        RecallRequest {
            context: context.clone(),
            query: "always start function argument names with".to_string(),
            k: 10,
            budget_tokens: 4096,
            mode: RecallMode::Fast,
            include_beliefs: true,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: false,
            procedure_recall_enabled: true,
            decay_enabled: false,
            engine_version: "preference-router-test".to_string(),
            transaction_as_of: None,
            valid_at: None,
            aggregation_window: None,
        },
        None,
        &CLOCK,
    )
    .await
    .expect("recall succeeds");

    let bodies: Vec<&str> = response
        .items
        .iter()
        .map(|item| item.body.as_str())
        .collect();
    assert!(
        bodies.iter().any(|body| body.contains("'y_'")),
        "the live rule must be served; got {bodies:?}"
    );
    assert!(
        !bodies.iter().any(|body| body.contains("'e_'")),
        "the retired rule must NOT be served -- this is the misapplication the \
         preference lane measured at 0.6717; got {bodies:?}"
    );
}

/// RW-3, the half of the lift that removes reach rather than adding it. An
/// episode is ground truth: `episodic_arm` owns no supersession, so an episodic
/// candidate with an explicit subject must append even when an open unit shares
/// its key. Before the lift the target filter was the literal
/// `MemoryKind::Semantic`, so this candidate could close a *semantic* unit's
/// generation — a cross-kind write.
#[tokio::test]
async fn an_episodic_candidate_never_supersedes_even_with_an_explicit_subject() {
    let store = InMemoryStore::default();
    let tenant = TenantId::from_u128(90_003);
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    let seeded = declare(
        &store,
        &context,
        0,
        MemoryKind::Semantic,
        "deployment window",
        "deploys go out on Tuesday",
    )
    .await;
    let followed = declare(
        &store,
        &context,
        1,
        MemoryKind::Episodic,
        "deployment window",
        "deploys go out on Thursday",
    )
    .await;
    assert_eq!(seeded, AdmissionAction::Append);
    assert_eq!(
        followed,
        AdmissionAction::Append,
        "an episodic candidate must not close a semantic generation"
    );

    let units = store.memory_units(tenant);
    assert!(
        units.iter().all(|unit| unit.state != UnitState::Superseded),
        "nothing may be superseded by the episodic arm"
    );
}

/// RW-7 / §13.2a: a preference is actor-gated at write. An untrusted caller's
/// preference hint degrades to a belief rather than minting a standing
/// constraint, exactly as a semantic hint already does.
#[tokio::test]
async fn an_untrusted_caller_cannot_mint_a_preference() {
    let store = InMemoryStore::default();
    let tenant = TenantId::from_u128(90_004);
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

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
            source_kind: "web".to_string(),
            source_ref: "test:preference:untrusted".to_string(),
            observed_at: "2026-07-31T00:00:00Z".to_string(),
            source_trust: TrustLevel::WebContent,
            subject_hint: Some("function argument name prefix".to_string()),
            subject: None,
            predicate: None,
            body: "always start function argument names with 'z_'".to_string(),
            compiler_version: COMPILER.to_string(),
        },
    )
    .await
    .expect("retain succeeds");
    let job = store
        .reflect_jobs(tenant)
        .last()
        .cloned()
        .expect("reflect job queued");
    reflect_recorded(
        &store,
        ReflectInput {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_ref: "test:reflect:untrusted".to_string(),
            observed_at: "2026-07-31T00:00:00Z".to_string(),
            source_body: None,
            episode_id: Some(retained.episode_id),
            resource_id: None,
            job_id: job.id,
            compiler_version: COMPILER.to_string(),
            candidates: vec![ReflectCandidate {
                source_kind: "web".to_string(),
                trust_level: TrustLevel::WebContent,
                actor_id: context.actor_id,
                subject: Some("function argument name prefix".to_string()),
                predicate: Some("prefers".to_string()),
                fact_key: None,
                kind: Some(MemoryKind::Preference),
                body: "always start function argument names with 'z_'".to_string(),
                confidence: None,
                churn_class: None,
                admission_hint: None,
                target_unit_ids: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
            }],
        },
        &NoopEmbedding,
        &CLOCK,
    )
    .await
    .expect("reflect succeeds");

    let units = store.memory_units(tenant);
    assert!(
        units.iter().all(|unit| unit.kind != MemoryKind::Preference),
        "web-trust content must not mint a standing constraint"
    );
    assert!(units.iter().any(|unit| unit.kind == MemoryKind::Belief));
}
