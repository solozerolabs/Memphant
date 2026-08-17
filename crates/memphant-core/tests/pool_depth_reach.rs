//! # Spec: `recall_pool_depth` and the semantic reach of recall
//!
//! ## Hypothesis
//! The dense vector channel is the ONLY pool-expander for *semantic reach*: a
//! unit that shares NO tokens with the query (no exact / lexical / bm25 hit) can
//! enter the served set ONLY by landing in the vector KNN top-`recall_pool_depth`
//! (`MemoryService::with_recall_pool_depth`, default
//! `DEFAULT_RECALL_POOL_DEPTH = 64`). Every other channel merely re-scores units
//! already pooled. Therefore, for a query-disjoint but embedding-near GOLD unit
//! whose cosine rank among the corpus is a known `R`, GOLD is served iff
//! `recall_pool_depth >= R`. `recall_pool_depth` is a hard semantic-reach knob.
//!
//! ## Corpus design (fully deterministic, $0)
//! Units are seeded DIRECTLY (`stage_memory_unit` + `upsert_embeddings`) so both
//! the body tokens and the exact embedding vectors are under test control — no
//! reflect rewording can perturb the engineered cosine ranks. All bodies are
//! lexically DISJOINT from the query, so the lexical/exact/bm25/edge channels
//! produce ZERO candidates and the vector channel is the sole path to service.
//!
//! Embeddings live on the unit circle (2-D). The query beacon embeds to angle 0
//! (`[1, 0]`); a unit at angle `a` has cosine similarity `cos(a)`, so smaller `a`
//! ⇒ nearer. Distractor `i` (i = 1..=`N_DISTRACT`) sits at `a = i * DELTA`; GOLD
//! sits at `a = (R - 0.5) * DELTA`, so exactly `R - 1` distractors are nearer and
//! GOLD's cosine rank is exactly `R`. With `R = 40` GOLD crosses the default-64
//! fan-out but sits above a 32 fan-out — the flip lands inside the swept grid.
//!
//! ## What the curve means
//! Sweeping `recall_pool_depth ∈ {8,16,32,64,128,256}` and recording whether
//! GOLD is served traces the semantic-reach curve. The step is at `R`: below it
//! GOLD is unreachable (not in the KNN fan-out ⇒ no vector score ⇒ no candidate),
//! above it GOLD is served. The `vector`-labelled candidate count grows with
//! depth (= `min(depth, corpus)`), the cost proxy: a deeper fan-out fuses/packs
//! more candidates for its reach.
//!
//! ## Non-vacuity (golden rule)
//! The case FLIPS across the threshold: GOLD absent at depth 32 (< R), present at
//! depth 64 (> R). Both sides are asserted, and a store-level KNN probe confirms
//! the pure fan-out membership independent of the full recall pipeline.
//!
//! CAVEAT (PG generalization) is documented at the end of this file.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{
    EmbedError, EmbeddingProvider, EmbeddingRow, FixedClock, InMemoryStore, MemoryStore,
    embedding_profile_for,
};
use memphant_types::{
    ActorId, MemoryKind, NewMemoryUnit, RecallChannel, RecallHttpRequest, RecallMode, RecallTime,
    ResolvedMemoryContext, ScopeId, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-08-17T00:00:00Z");
const OBSERVED_AT: &str = "2026-08-17T00:00:00Z";

/// Corpus size and the engineered cosine rank of GOLD.
const N_DISTRACT: usize = 200;
const R: usize = 40;
/// Angular spacing between adjacent distractors (radians). Kept small so every
/// unit stays on the positive-cosine hemisphere (`cos(N_DISTRACT * DELTA) > 0`),
/// clearing the vector channel's `score > 0` gate.
const DELTA: f32 = 0.001;

/// Query beacon: every text embeds to angle 0 (`[1, 0]`). Unit vectors are
/// seeded directly, so this provider is exercised ONLY for the recall-time query
/// embedding (`embed_query` defaults to `embed`). Its `id`/`dimensions` fix the
/// embedding profile the seeded rows must match.
#[derive(Clone, Copy, Default)]
struct QueryBeacon;

impl EmbeddingProvider for QueryBeacon {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(vec![vec![1.0, 0.0]; texts.len()])
    }

    fn dimensions(&self) -> usize {
        2
    }

    fn id(&self) -> &str {
        "pool-depth-reach-beacon"
    }
}

fn recall_time() -> RecallTime {
    RecallTime {
        evaluated_at: CLOCK.0.to_string(),
        transaction_as_of: CLOCK.0.to_string(),
        valid_at: CLOCK.0.to_string(),
    }
}

fn new_unit(context: &ResolvedMemoryContext, body: &str) -> NewMemoryUnit {
    NewMemoryUnit {
        capture: None,
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        kind: MemoryKind::Procedural,
        state: UnitState::Validated,
        fact_key: None,
        predicate: None,
        body: body.to_string(),
        confidence: Some(1.0),
        trust_level: TrustLevel::TrustedSystem,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(context.actor_id),
        source_kind: Some("test".to_string()),
        source_ref: "test:pool-depth-reach".to_string(),
        observed_at: OBSERVED_AT.to_string(),
        source_episode_id: None,
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: Vec::new(),
        valid_from: None,
        valid_to: None,
        transaction_from: None,
        transaction_to: None,
    }
}

/// A unit-circle vector at angle `a`: cosine similarity to the query beacon
/// `[1, 0]` is `cos(a)`, so rank-by-nearness is rank-by-`a`.
fn circle_vec(a: f32) -> Vec<f32> {
    vec![a.cos(), a.sin()]
}

fn recall_request(context: &ResolvedMemoryContext, query: &str) -> RecallHttpRequest {
    RecallHttpRequest {
        compact_only: false,
        serve_captures: false,
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        query: query.to_string(),
        // Large `k` + budget so the served set faithfully reflects pool
        // membership: the flip is about REACH (does GOLD enter the pool), not
        // about rank-truncation or packing drops.
        limit: Some(300),
        budget_tokens: Some(2_000_000),
        mode: Some(RecallMode::Fast),
        include_beliefs: Some(true),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// Seed GOLD + `N_DISTRACT` distractors, all lexically disjoint from the query,
/// with directly-written circle embeddings placing GOLD at cosine rank `R`.
/// Returns the GOLD unit id.
async fn seed_corpus(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
) -> memphant_types::UnitId {
    let profile = embedding_profile_for(&QueryBeacon);
    store
        .upsert_embedding_profile(context.tenant_id, profile.clone())
        .await
        .expect("seed profile");

    let mut tx = store.begin(context).await.expect("begin");
    let mut embeddings: Vec<EmbeddingRow> = Vec::with_capacity(N_DISTRACT + 1);

    // GOLD: angle (R - 0.5) * DELTA ⇒ exactly R-1 distractors nearer ⇒ rank R.
    let gold_angle = (R as f32 - 0.5) * DELTA;
    let gold_id = store
        .stage_memory_unit(&mut tx, new_unit(context, "goldshibboleth omegatoken"))
        .await
        .expect("stage gold");
    embeddings.push(EmbeddingRow {
        memory_unit_id: gold_id,
        embedding_profile_id: profile.id,
        vec: circle_vec(gold_angle),
    });

    for i in 1..=N_DISTRACT {
        let body = format!("distractorunit{i} fillerbody");
        let id = store
            .stage_memory_unit(&mut tx, new_unit(context, &body))
            .await
            .expect("stage distractor");
        embeddings.push(EmbeddingRow {
            memory_unit_id: id,
            embedding_profile_id: profile.id,
            vec: circle_vec(i as f32 * DELTA),
        });
    }
    store.commit(tx).await.expect("commit units");
    store
        .upsert_embeddings(context, embeddings)
        .await
        .expect("seed embeddings");
    gold_id
}

fn service_at_depth(store: &InMemoryStore, depth: usize) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(QueryBeacon),
    )
    .with_recall_pool_depth(depth)
}

/// Whether GOLD is in the served items, plus the number of `vector`-labelled
/// fusion candidates (the depth-driven cost proxy).
async fn recall_gold(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    gold_id: memphant_types::UnitId,
    depth: usize,
) -> (bool, usize) {
    let service = service_at_depth(store, depth);
    let response = service
        .recall(
            context.clone(),
            recall_request(context, "beaconprobe queryonly"),
        )
        .await
        .expect("recall");
    let served = response
        .items
        .iter()
        .any(|item| item.body.contains("goldshibboleth"));
    let trace = service
        .trace(context, response.trace_id)
        .await
        .expect("trace fetch")
        .expect("trace stored");
    // Sanity: the ONLY channel that ever fired is `vector` — the corpus is
    // lexically disjoint from the query, so no lexical/exact/bm25/edge reach.
    assert!(
        trace
            .candidates
            .iter()
            .all(|candidate| candidate.channel == RecallChannel::Vector),
        "corpus is lexically disjoint: only the vector channel may produce candidates"
    );
    let vector_candidates = trace
        .candidates
        .iter()
        .filter(|candidate| candidate.channel == RecallChannel::Vector)
        .count();
    // If GOLD is served it must be by the vector channel carrying its id.
    if served {
        assert!(
            trace
                .candidates
                .iter()
                .any(|c| c.channel == RecallChannel::Vector && c.unit_id == gold_id),
            "served GOLD must appear as a vector candidate"
        );
    }
    (served, vector_candidates)
}

/// Store-level ground truth: GOLD's cosine rank among the seeded corpus, and
/// whether GOLD is inside the top-`depth` KNN fan-out — the pure vector reach,
/// independent of the full recall pipeline.
async fn knn_probe(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    gold_id: memphant_types::UnitId,
    depth: usize,
) -> (usize, bool) {
    let profile = embedding_profile_for(&QueryBeacon);
    let query_vec = QueryBeacon
        .embed_query(&["beaconprobe queryonly".to_string()])
        .expect("embed query")
        .remove(0);
    let pairs = store
        .fetch_vector_candidates(context, &query_vec, profile.id, &recall_time(), depth)
        .await
        .expect("knn");
    let in_pool = pairs.iter().any(|(unit, _)| unit.id == gold_id);
    // Rank among the FULL corpus (limit = everything).
    let full = store
        .fetch_vector_candidates(context, &query_vec, profile.id, &recall_time(), usize::MAX)
        .await
        .expect("knn full");
    let rank = full
        .iter()
        .position(|(unit, _)| unit.id == gold_id)
        .map(|idx| idx + 1)
        .expect("gold present in full KNN");
    (rank, in_pool)
}

#[tokio::test]
async fn pool_depth_gates_semantic_reach_of_a_lexically_disjoint_gold() {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    store.seed_context_binding(&context);

    let gold_id = seed_corpus(&store, &context).await;

    // The engineered ground-truth rank: GOLD sits at cosine rank R among the
    // corpus. Confirm it before trusting the sweep (design self-check).
    let (rank, _) = knn_probe(&store, &context, gold_id, usize::MAX).await;
    assert_eq!(
        rank, R,
        "engineered GOLD cosine rank must be exactly R = {R}; got {rank}"
    );
    println!(
        "[POOL DEPTH corpus] units={} engineered_gold_cosine_rank_R={R}",
        N_DISTRACT + 1
    );

    let depths = [8usize, 16, 32, 64, 128, 256];
    let mut curve: Vec<(usize, bool, bool, usize)> = Vec::new();
    for depth in depths {
        let (served, vector_candidates) = recall_gold(&store, &context, gold_id, depth).await;
        let (probe_rank, in_pool) = knn_probe(&store, &context, gold_id, depth).await;
        assert_eq!(probe_rank, R, "full-corpus rank is stable across the sweep");
        // The served flip must track the pure KNN fan-out membership exactly.
        assert_eq!(
            served, in_pool,
            "served GOLD ({served}) must equal KNN pool membership ({in_pool}) at depth {depth}"
        );
        // And that membership is exactly the depth >= R rule.
        assert_eq!(
            in_pool,
            depth >= R,
            "GOLD in top-{depth} KNN iff depth >= R ({R})"
        );
        println!(
            "[POOL DEPTH curve] depth={depth:<3} gold={} vector_candidates={vector_candidates}",
            if served { "present" } else { "absent " }
        );
        curve.push((depth, served, in_pool, vector_candidates));
    }

    // --- Non-vacuity: the case FLIPS across the engineered threshold R=40. ---
    // BELOW R (depth 32): GOLD absent. ABOVE R (depth 64): GOLD present.
    let below = curve
        .iter()
        .find(|(d, ..)| *d == 32)
        .expect("depth 32 swept");
    let above = curve
        .iter()
        .find(|(d, ..)| *d == 64)
        .expect("depth 64 swept");
    assert!(
        !below.1,
        "below threshold (depth 32 < R={R}) GOLD must be ABSENT — the fan-out cannot reach it"
    );
    assert!(
        above.1,
        "above threshold (depth 64 > R={R}) GOLD must be PRESENT — the fan-out reaches it"
    );

    // The default (64) DOES reach this GOLD (R=40 < 64): adequate for a corpus
    // whose relevant unit sits within the top-64 semantic neighbourhood.
    let default_present = curve
        .iter()
        .find(|(d, ..)| *d == memphant_core::DEFAULT_RECALL_POOL_DEPTH)
        .map(|(_, served, ..)| *served)
        .expect("default depth 64 swept");
    assert!(
        default_present,
        "the default recall_pool_depth reaches a GOLD at cosine rank {R}"
    );

    // Cost proxy: the vector-labelled candidate count grows monotonically with
    // depth up to the corpus cap (deeper fan-out ⇒ more fused candidates).
    let cost: Vec<usize> = curve.iter().map(|(_, _, _, c)| *c).collect();
    assert!(
        cost.windows(2).all(|w| w[1] >= w[0]),
        "vector candidate count (fusion cost) is non-decreasing in depth: {cost:?}"
    );
    println!(
        "[POOL DEPTH cost] vector_candidates_by_depth {:?} (corpus={} units)",
        depths.iter().zip(cost.iter()).collect::<Vec<_>>(),
        N_DISTRACT + 1
    );
}

// ---------------------------------------------------------------------------
// CAVEAT — how this InMemory finding does and does NOT generalize to Postgres
// ---------------------------------------------------------------------------
// This test ISOLATES the vector KNN fan-out as the sole reach mechanism:
// `InMemoryStore::fetch_recall_candidates` returns ALL of the tenant's units
// (it ignores query terms), so the ONLY thing `recall_pool_depth` gates here is
// which units earn a nonzero VECTOR score — and, for a lexically-disjoint GOLD,
// that is the only score it can earn. Hence the clean `served == (depth >= R)`
// step measured above.
//
// On Postgres the LEXICAL pool is ALSO bounded: `fetch_recall_candidates` unions
// an FTS top-N (~200), a recency top-N (~100), and fact-key lookups, rather than
// scanning the whole tenant. So real semantic reach is
//     reach = (vector top-`recall_pool_depth`)  ∪  (bounded lexical pool),
// and a query-disjoint unit that also falls outside the bounded lexical pool is
// reachable ONLY via the vector top-`recall_pool_depth`. The DIRECTION of the
// finding therefore holds on PG — `recall_pool_depth` is a hard cap on the
// semantic reach of otherwise-unreachable units, and raising it is the only
// lever that lets a deeper-ranked embedding-near unit surface. The exact step
// location `R` is corpus-specific (it depends on how many nearer neighbours a
// real embedding model places between the query and the target); this test
// proves the MECHANISM and its monotone cost, not a universal `R`.
