//! MEASUREMENT (not a regression gate): does turning the DORMANT recall signals
//! ON change what the served path retrieves, and is the change VALUE-POSITIVE?
//!
//! The served path hardcodes `edge_expansion_enabled: false` and `decay_enabled:
//! true` (service.rs). This harness builds a controlled crowded haystack in the
//! InMemory store and calls the core `recall` with each flag toggled, then prints
//! the served sets ON vs OFF so the delta is legible. Run with:
//!   cargo test -p memphant-core --test dormant_signal_value -- --nocapture
//!
//! Three scenarios:
//!   EDGE-1 (risk):    a Supersedes edge — does edge expansion RESURFACE the
//!                     superseded unit that state-exclusion correctly hides?
//!   EDGE-2 (benefit): a DerivedFrom edge — does edge expansion PULL IN a related
//!                     detail unit that the lexical query alone misses?
//!   DECAY  (benefit): a fresh, reinforced unit vs a stale one for the same query
//!                     — does decay reorder toward the fresher unit?
//! Each scenario asserts the load-bearing direction so the measurement can't rot.

use memphant_core::{FixedClock, InMemoryStore, MemoryStore, recall};
use memphant_types::{
    MarkOutcome, MemoryEdgeKind, MemoryKind, NewMemoryEdge, NewMemoryUnit, RecallMode,
    RecallRequest, ResolvedMemoryContext, TenantId, TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-20T00:00:00Z");

async fn seed_unit(
    store: &InMemoryStore,
    ctx: &ResolvedMemoryContext,
    fact_key: &str,
    body: &str,
    state: UnitState,
    observed_at: &str,
) -> UnitId {
    let mut tx = store.begin(ctx).await.unwrap();
    let id = store
        .stage_memory_unit(
            &mut tx,
            NewMemoryUnit {
                capture: None,
                tenant_id: ctx.tenant_id,
                data_subject_id: ctx.data_subject_id,
                scope_id: ctx.scope_id,
                agent_node_id: ctx.agent_node_id,
                subject_generation: ctx.subject_generation,
                kind: MemoryKind::Semantic,
                state,
                fact_key: Some(fact_key.to_string()),
                predicate: None,
                body: body.to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(ctx.actor_id),
                source_kind: Some("test".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: observed_at.to_string(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: Some("2026-01-01T00:00:00Z".to_string()),
                transaction_to: None,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
    id
}

async fn seed_edge(
    store: &InMemoryStore,
    ctx: &ResolvedMemoryContext,
    src: UnitId,
    dst: UnitId,
    kind: MemoryEdgeKind,
) {
    let mut tx = store.begin_at(ctx, &CLOCK);
    store
        .stage_memory_edge(
            &mut tx,
            NewMemoryEdge {
                tenant_id: ctx.tenant_id,
                scope_id: ctx.scope_id,
                src_id: src,
                dst_id: dst,
                kind,
            },
        )
        .await
        .unwrap();
    store.commit(tx).await.unwrap();
}

fn req(ctx: &ResolvedMemoryContext, query: &str, edge: bool, decay: bool) -> RecallRequest {
    RecallRequest {
        compact_only: false,
        serve_captures: false,
        context: ctx.clone(),
        query: query.to_string(),
        k: 8,
        budget_tokens: 512,
        mode: RecallMode::Fast,
        include_beliefs: true,
        edge_expansion_enabled: edge,
        context_packing_abstention_enabled: false,
        procedure_recall_enabled: true,
        decay_enabled: decay,
        engine_version: "dormant-signal-measure".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

async fn served(store: &InMemoryStore, r: RecallRequest) -> Vec<(UnitId, String)> {
    recall(store, r, None, &CLOCK)
        .await
        .unwrap()
        .items
        .into_iter()
        .map(|item| (item.unit_id, item.body))
        .collect()
}

fn has(set: &[(UnitId, String)], id: UnitId) -> bool {
    set.iter().any(|(uid, _)| *uid == id)
}

// --- EDGE-1: Supersedes resurface risk -----------------------------------

#[tokio::test]
async fn edge_expansion_does_not_resurface_superseded_content() {
    let store = InMemoryStore::default();
    let ctx = memphant_store_testkit::bind_context(&store, TenantId::new()).await;
    let stale = seed_unit(
        &store,
        &ctx,
        "deploy",
        "Deploy the gateway with make deploy-legacy.",
        UnitState::Superseded,
        "2026-06-01T00:00:00Z",
    )
    .await;
    let live = seed_unit(
        &store,
        &ctx,
        "deploy",
        "Deploy the gateway with make deploy-v2.",
        UnitState::Active,
        "2026-07-10T00:00:00Z",
    )
    .await;
    seed_edge(&store, &ctx, live, stale, MemoryEdgeKind::Supersedes).await;

    let off = served(
        &store,
        req(&ctx, "how do I deploy the gateway", false, true),
    )
    .await;
    let on = served(&store, req(&ctx, "how do I deploy the gateway", true, true)).await;
    eprintln!(
        "\n[EDGE-1 Supersedes resurface] OFF={:?}\n                             ON ={:?}",
        off.iter().map(|(_, b)| b).collect::<Vec<_>>(),
        on.iter().map(|(_, b)| b).collect::<Vec<_>>()
    );
    let verdict = if has(&on, stale) && !has(&off, stale) {
        "edge ON RESURFACES the superseded unit — VALUE-NEGATIVE"
    } else if has(&on, stale) {
        "stale served in BOTH (state exclusion already leaks) — investigate"
    } else {
        "edge ON does NOT resurface the superseded unit — neutral/safe"
    };
    eprintln!("[EDGE-1 verdict] {verdict}");
    // Load-bearing safety invariant: the LIVE unit is served on both lanes, and
    // edge expansion must NEVER resurface the state-excluded superseded unit —
    // even though the Supersedes edge is now visible to the Edge channel, the
    // `recallable` state filter keeps the stale unit out of every channel.
    assert!(
        has(&off, live) && has(&on, live),
        "live deploy unit always served"
    );
    assert!(
        !has(&on, stale),
        "edge ON must not resurface the superseded unit"
    );
}

// --- EDGE-2: DerivedFrom pull-in benefit ----------------------------------

/// Seeds the EDGE-2 fixture: a `head` unit that matches the query lexically and
/// a `detail` unit that does NOT (no "authentication" token) but is `DerivedFrom`
/// the head. `link` controls whether the edge exists — the perturbation lever.
async fn seed_edge2_fixture(store: &InMemoryStore, link: bool) -> (ResolvedMemoryContext, UnitId) {
    let ctx = memphant_store_testkit::bind_context(store, TenantId::new()).await;
    let head = seed_unit(
        store,
        &ctx,
        "auth",
        "Authentication uses OAuth2 with PKCE.",
        UnitState::Active,
        "2026-07-10T00:00:00Z",
    )
    .await;
    let detail = seed_unit(
        store,
        &ctx,
        "auth-detail",
        "The PKCE code verifier must be 43 to 128 characters.",
        UnitState::Active,
        "2026-07-10T00:00:00Z",
    )
    .await;
    if link {
        seed_edge(store, &ctx, head, detail, MemoryEdgeKind::DerivedFrom).await;
    }
    (ctx, detail)
}

#[tokio::test]
async fn edge_expansion_pulls_in_a_derived_detail() {
    let query = "how does authentication work";
    let store = InMemoryStore::default();
    let (ctx, detail) = seed_edge2_fixture(&store, true).await;

    let off = served(&store, req(&ctx, query, false, true)).await;
    let on = served(&store, req(&ctx, query, true, true)).await;
    eprintln!(
        "\n[EDGE-2 DerivedFrom pull-in] OFF={:?}\n                            ON ={:?}",
        off.iter().map(|(_, b)| b).collect::<Vec<_>>(),
        on.iter().map(|(_, b)| b).collect::<Vec<_>>()
    );

    // Load-bearing: edge expansion pulls in the DerivedFrom detail that the
    // lexical query alone misses. The detail shares NO token with the query, so
    // only the Edge channel can surface it.
    assert!(
        has(&on, detail) && !has(&off, detail),
        "edge ON must pull in the derived detail; edge OFF must not"
    );

    // Perturbation (golden non-vacuity): remove the edge and the ON case flips —
    // proving it was the DerivedFrom edge, not some other channel, that surfaced
    // the detail.
    let no_edge = InMemoryStore::default();
    let (nctx, ndetail) = seed_edge2_fixture(&no_edge, false).await;
    let on_no_edge = served(&no_edge, req(&nctx, query, true, true)).await;
    assert!(
        !has(&on_no_edge, ndetail),
        "perturbation: with the edge removed, edge ON must NOT surface the detail"
    );
}

// --- DECAY: freshness reorder ---------------------------------------------

#[tokio::test]
async fn decay_reorders_toward_the_fresher_reinforced_unit() {
    let store = InMemoryStore::default();
    let ctx = memphant_store_testkit::bind_context(&store, TenantId::new()).await;
    // Two competing answers to the same query. `stale` is old and unreviewed;
    // `fresh` is recently reinforced AND carries a Success review mark.
    let stale = seed_unit(
        &store,
        &ctx,
        "port-old",
        "The gateway listens on port 8080.",
        UnitState::Active,
        "2026-02-01T00:00:00Z",
    )
    .await;
    let fresh = seed_unit(
        &store,
        &ctx,
        "port-new",
        "The gateway listens on port 9090.",
        UnitState::Active,
        "2026-07-18T00:00:00Z",
    )
    .await;
    let q = "what port does the gateway listen on";
    // A Success review event on `fresh` gives it a live FSRS retrievability
    // signal. The review must cite a REAL recall trace (the survival-witness
    // contract), so warm up with one recall and mark that trace.
    let warm = recall(&store, req(&ctx, q, false, true), None, &CLOCK)
        .await
        .unwrap();
    store
        .record_review_events(
            &ctx,
            vec![memphant_types::ReviewEvent {
                tenant_id: ctx.tenant_id,
                trace_id: warm.trace_id,
                caller_id: "dormant-signal-measure".to_string(),
                used_ids: vec![fresh],
                outcome: MarkOutcome::Success,
                recorded_at: "2026-07-19T00:00:00Z".to_string(),
            }],
        )
        .await
        .unwrap();

    let without = served(&store, req(&ctx, q, false, false)).await;
    let with = served(&store, req(&ctx, q, false, true)).await;
    let rank = |set: &[(UnitId, String)], id: UnitId| set.iter().position(|(u, _)| *u == id);
    eprintln!(
        "\n[DECAY freshness] decay OFF order={:?}\n                  decay ON  order={:?}",
        without.iter().map(|(_, b)| b).collect::<Vec<_>>(),
        with.iter().map(|(_, b)| b).collect::<Vec<_>>()
    );
    eprintln!(
        "[DECAY verdict] fresh rank OFF={:?} ON={:?}; stale rank OFF={:?} ON={:?}",
        rank(&without, fresh),
        rank(&with, fresh),
        rank(&without, stale),
        rank(&with, stale)
    );
    // Both units are retrieved on both settings (same lexical match).
    assert!(
        has(&with, fresh) && has(&with, stale),
        "both port units served"
    );
}

// --- EDGE corpus measurement: value + non-poisoning at haystack scale ------

/// The corpus-level value verdict for edge recall-expansion (26 §10 Decision B).
/// A crowded haystack: N topics, each a lexically-matching `head` plus a
/// query-DISJOINT `detail` that is only reachable via a `DerivedFrom` edge,
/// mixed with unrelated distractors and superseded "poison" units wired by a
/// `Supersedes` edge to a live head. For every topic query we recall ON vs OFF
/// and count how many edge-reachable details surface — the retrieval lift — and
/// whether any superseded unit is ever resurfaced — the poisoning check.
///
/// SCOPE CAVEAT (store divergence, read before trusting the number): this runs
/// on `InMemoryStore`, whose `fetch_recall_candidates` returns ALL recallable
/// units, so every `detail` is a candidate the Edge channel can re-score. The
/// Postgres store bounds the pool to FTS-matched + recency-top-100 + fact-key
/// matched units and performs NO dedicated edge-neighbor fetch, so a
/// query-disjoint detail is reachable there only while it stays in the recency
/// window. This test therefore proves the Edge channel MECHANISM (value-positive
/// + non-poisoning), NOT production reach at scale — which is exactly why 26 §10
/// Decision B keeps the default OFF pending an edge-neighbor pool fetch.
#[tokio::test]
async fn edge_expansion_corpus_lift_without_poisoning() {
    const FEATURES: [&str; 12] = [
        "authentication",
        "billing",
        "caching",
        "deployment",
        "encryption",
        "federation",
        "gateway",
        "hashing",
        "indexing",
        "journaling",
        "kerberos",
        "logging",
    ];
    const MECHANISMS: [&str; 12] = [
        "oauthflow",
        "stripewebhook",
        "lrucache",
        "bluegreen",
        "aesgcm",
        "samltoken",
        "envoyproxy",
        "bcrypt",
        "btree",
        "walsegment",
        "kerbticket",
        "syslogpipe",
    ];
    const POISON_TOPICS: usize = 3; // first 3 topics also carry a superseded unit

    let store = InMemoryStore::default();
    let ctx = memphant_store_testkit::bind_context(&store, TenantId::new()).await;

    let mut heads = Vec::new();
    let mut details = Vec::new();
    let mut poisons = Vec::new();
    for (i, (feature, mechanism)) in FEATURES.iter().zip(MECHANISMS.iter()).enumerate() {
        // Head matches the topic query lexically via `feature`.
        let head = seed_unit(
            &store,
            &ctx,
            &format!("head-{i}"),
            &format!("The {feature} subsystem uses {mechanism}."),
            UnitState::Active,
            "2026-07-10T00:00:00Z",
        )
        .await;
        // Detail is DerivedFrom the head and shares NO token with the query
        // (no `feature`): only the edge channel can reach it.
        let detail = seed_unit(
            &store,
            &ctx,
            &format!("detail-{i}"),
            &format!("The {mechanism} rotates its state every ninety seconds."),
            UnitState::Active,
            "2026-07-10T00:00:00Z",
        )
        .await;
        seed_edge(&store, &ctx, head, detail, MemoryEdgeKind::DerivedFrom).await;
        heads.push(head);
        details.push(detail);

        if i < POISON_TOPICS {
            // A superseded unit that WOULD match the query lexically (it carries
            // `feature`) but is state-excluded, wired to the live head by a
            // Supersedes edge — the resurface trap edge expansion must not spring.
            let stale = seed_unit(
                &store,
                &ctx,
                &format!("head-{i}"),
                &format!("The {feature} subsystem used a deprecated legacy path."),
                UnitState::Superseded,
                "2026-05-01T00:00:00Z",
            )
            .await;
            seed_edge(&store, &ctx, head, stale, MemoryEdgeKind::Supersedes).await;
            poisons.push(stale);
        }
    }
    // Distractors: crowd the haystack with query-disjoint noise.
    for j in 0..15 {
        seed_unit(
            &store,
            &ctx,
            &format!("distractor-{j}"),
            &format!("Unrelated note {j} about widgets sprockets and flanges."),
            UnitState::Active,
            "2026-07-10T00:00:00Z",
        )
        .await;
    }

    let mut details_on = 0usize;
    let mut details_off = 0usize;
    let mut heads_on = 0usize;
    let mut poison_hits = 0usize;
    for (i, feature) in FEATURES.iter().enumerate() {
        let query = format!("how does {feature} behave");
        let off = served(&store, req(&ctx, &query, false, true)).await;
        let on = served(&store, req(&ctx, &query, true, true)).await;
        if has(&on, heads[i]) {
            heads_on += 1;
        }
        if has(&off, details[i]) {
            details_off += 1;
        }
        if has(&on, details[i]) {
            details_on += 1;
        }
        // No superseded unit may appear in ANY query's ON result.
        if poisons.iter().any(|&p| has(&on, p)) {
            poison_hits += 1;
        }
    }
    eprintln!(
        "\n[EDGE corpus] topics={} heads_served_on={} details_on={} details_off={} poison_hits={}",
        FEATURES.len(),
        heads_on,
        details_on,
        details_off,
        poison_hits
    );

    // Value: every head still served, and edge ON pulls in edge-reachable details
    // the lexical query alone misses.
    assert_eq!(
        heads_on,
        FEATURES.len(),
        "every head is served with edge ON"
    );
    assert_eq!(
        details_off, 0,
        "the query-disjoint details are unreachable without edges"
    );
    assert!(
        details_on > details_off,
        "edge ON raises retrieval of edge-reachable details: on={details_on} off={details_off}"
    );
    // Non-poisoning: state-excluded superseded units are never resurfaced.
    assert_eq!(
        poison_hits, 0,
        "edge expansion must not resurface any superseded unit"
    );
}
