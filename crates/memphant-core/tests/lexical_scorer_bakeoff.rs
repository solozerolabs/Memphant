//! LEXICAL SCORER BAKE-OFF (measurement, not a regression gate).
//!
//! The fusion's lexical family runs one of three scorers, selected construction-
//! time by `LexicalScorer` (threaded here directly through `recall_with_pool`):
//!
//! - `Overlap` — the PRE-2026-08-01 default control. TWO token-overlap passes:
//!   body-overlap DENSITY (`matched / body_len`) and token-set JACCARD
//!   (`intersection / union`). No IDF; both passes divide by a length/union term,
//!   so a long body is penalized hard and every matched token counts the same.
//! - `Bm25Control` — Okapi BM25 (k1=1.2, b=0.75) over `bm25_control_tokens`
//!   (whole identifiers: `src/foo/bar.py`, `snake_case_name` stay intact). IDF +
//!   `b`-normalization.
//! - `Bm25Code` — **the SHIPPED DEFAULT (flipped 2026-08-01).** BM25 over
//!   `bm25_code_tokens`: every control token PLUS its alphanumeric sub-tokens, so
//!   a path/identifier matches both whole and by part.
//!
//! HYPOTHESES under test (each stated as a claim the numbers must earn):
//! 1. IDF discrimination — a query pairing a RARE identifier with a COMMON word.
//!    A distractor keyword-stuffs the common word. Without IDF, Overlap lets the
//!    stuffed distractor out-rank the gold that carries the rare term; BM25's IDF
//!    weights the rare term up and floats gold to the top.
//! 2. Length normalization — the rare-identifier gold is buried in a LONG body (a
//!    simulated long tool result); a SHORT distractor shares only the common
//!    word. Overlap's length division sinks the long gold below the short
//!    distractor; BM25's b=0.75 keeps gold on top.
//! 3. Sub-token matching (Bm25Code vs Bm25Control) — a query naming only a PART
//!    of a whole identifier (`bar` inside `src/foo/bar.py`). `Bm25Control` keeps
//!    the path whole and never matches, so gold is not a candidate at all;
//!    `Bm25Code` adds the sub-token and surfaces gold. (Overlap's own tokenizer
//!    already sub-splits on non-alphanumerics, so it ALSO surfaces gold — the
//!    Code advantage here is specifically over the plain control.)
//!
//! ISOLATION. The corpus is built so ONLY the lexical family can discriminate,
//! which makes the served `items` order equal to the lexical channel's order:
//!
//! - `NoopEmbedding`/`vector_query = None` → VECTOR channel off.
//! - `fact_key = None` → EXACT channel scores 0 (silent).
//! - non-recency query, no temporal window → TEMPORAL channel scores 0.
//! - `edge_expansion_enabled = false` → EDGE channel off.
//! - `decay_enabled = false` → no retrievability reordering.
//!
//! Each scenario ASSERTS this isolation from the trace (every scored candidate
//! is on `RecallChannel::Lexical`), so a regression that woke another channel
//! would fail the test rather than silently confound the measurement.
//!
//! NON-VACUITY (golden rule). Every "scorer X ranks gold higher" claim is proven
//! by PERTURBATION: swapping to the losing scorer must FLIP the outcome, and both
//! directions are asserted. A scenario that stopped discriminating (e.g. the gold
//! stops being the rare-term carrier) would break the flip and fail.
//!
//! GENERALIZATION. The lexical scorer is a pure scoring function over the recall
//! candidate pool (`bm25_unit_scores` / the overlap passes read only unit bodies
//! and the query). It is store-agnostic — identical on `InMemoryStore` and
//! Postgres — so this InMemory measurement generalizes to the served PG path.
//!
//! Run with:
//!   cargo test -p memphant-core --test lexical_scorer_bakeoff -- --nocapture

use memphant_core::{
    DEFAULT_RECALL_POOL_DEPTH, FixedClock, InMemoryStore, LexicalScorer, MemoryStore, PackLevers,
    recall_with_pool,
};
use memphant_types::{
    MemoryKind, NewMemoryUnit, RecallChannel, RecallMode, RecallRequest, ResolvedMemoryContext,
    TenantId, TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-20T00:00:00Z");

/// Stage a body directly into the store as an Active semantic unit with NO
/// `fact_key` (so the Exact channel stays silent) and no chunks/valid-time.
async fn seed(store: &InMemoryStore, ctx: &ResolvedMemoryContext, body: &str) -> UnitId {
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
                state: UnitState::Active,
                fact_key: None,
                predicate: None,
                body: body.to_string(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedUser,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(ctx.actor_id),
                source_kind: Some("test".to_string()),
                source_ref: "test:fixture".to_string(),
                observed_at: "2026-07-10T00:00:00Z".to_string(),
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

/// A recall request wired for lexical isolation (see the module doc): non-recency
/// query, no edge expansion, abstention off, decay off, generous budget/k so the
/// whole (small) scoring pool is served in fused order.
fn req(ctx: &ResolvedMemoryContext, query: &str) -> RecallRequest {
    RecallRequest {
        compact_only: false,
        serve_captures: false,
        context: ctx.clone(),
        query: query.to_string(),
        k: 8,
        budget_tokens: 8192,
        mode: RecallMode::Fast,
        include_beliefs: true,
        edge_expansion_enabled: false,
        context_packing_abstention_enabled: false,
        procedure_recall_enabled: true,
        decay_enabled: false,
        engine_version: "lexical-bakeoff".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// Runs one recall under `scorer` and returns the served unit ids in ranked
/// order. Also asserts the isolation invariant on the trace it just wrote: every
/// scored candidate is on the LEXICAL channel, so the served order it returns is
/// the lexical family's order and nothing else.
async fn served(
    store: &InMemoryStore,
    ctx: &ResolvedMemoryContext,
    query: &str,
    scorer: LexicalScorer,
) -> Vec<UnitId> {
    let response = recall_with_pool(
        store,
        req(ctx, query),
        None, // vector_query: VECTOR channel off
        &CLOCK,
        DEFAULT_RECALL_POOL_DEPTH,
        PackLevers::default(),
        scorer,
        false,
        None,
    )
    .await
    .expect("recall succeeds");

    // Isolation proof: the most recent trace has candidates on ONLY the lexical
    // channel, so no Exact/Temporal/Vector/Edge vote confounds the ranking.
    let traces = store.retrieval_traces(ctx.tenant_id);
    let last = traces.last().expect("a trace was written");
    assert!(
        last.candidates
            .iter()
            .all(|candidate| candidate.channel == RecallChannel::Lexical),
        "{scorer:?}: corpus must isolate the lexical channel, but a candidate landed on {:?}",
        last.candidates
            .iter()
            .map(|candidate| candidate.channel)
            .filter(|channel| *channel != RecallChannel::Lexical)
            .collect::<Vec<_>>()
    );

    response
        .items
        .into_iter()
        .map(|item| item.unit_id)
        .collect()
}

/// 1-based rank of `id` in a served list; `None` when it was not served at all
/// (no lexical candidate scored it).
fn rank(items: &[UnitId], id: UnitId) -> Option<usize> {
    items.iter().position(|uid| *uid == id).map(|idx| idx + 1)
}

/// Pretty-prints a rank as `1`, `2`, ... or `absent`.
fn show(r: Option<usize>) -> String {
    r.map_or_else(|| "absent".to_string(), |n| n.to_string())
}

// --- Scenario 1: IDF discrimination (rare vs common) ----------------------

/// GOLD carries the RARE identifier `xylophonium` (df = 1 in the pool) plus one
/// occurrence of the COMMON word `config`. The DISTRACTOR keyword-stuffs
/// `config` and never mentions the rare term. Filler units also carry `config`,
/// so its document frequency is high and its IDF low.
///
/// Overlap (no IDF, density/Jaccard): the stuffed distractor's `config` density
/// dominates, so it out-ranks gold. BM25 (IDF): the rare `xylophonium` carries a
/// far larger IDF than the common `config`, so gold wins. The perturbation is the
/// scorer swap — it FLIPS which of gold/distractor is on top.
#[tokio::test]
async fn idf_rare_term_beats_common_term_stuffing() {
    let store = InMemoryStore::default();
    let ctx = memphant_store_testkit::bind_context(&store, TenantId::new()).await;

    let gold = seed(
        &store,
        &ctx,
        "The xylophonium tuning parameter lives in the config file.",
    )
    .await;
    let distractor = seed(
        &store,
        &ctx,
        "config config config config config config config config",
    )
    .await;
    // Filler carrying `config` so its document frequency is high (IDF low).
    seed(&store, &ctx, "database config for the staging environment").await;
    seed(&store, &ctx, "the deployment config controls rollout").await;
    seed(&store, &ctx, "restart after editing the config values").await;

    let query = "xylophonium config";
    let overlap = served(&store, &ctx, query, LexicalScorer::Overlap).await;
    let bm25c = served(&store, &ctx, query, LexicalScorer::Bm25Control).await;
    let bm25code = served(&store, &ctx, query, LexicalScorer::Bm25Code).await;

    let (og, od) = (rank(&overlap, gold), rank(&overlap, distractor));
    let (cg, cd) = (rank(&bm25c, gold), rank(&bm25c, distractor));
    let (kg, kd) = (rank(&bm25code, gold), rank(&bm25code, distractor));

    eprintln!(
        "[LEXICAL bakeoff] scenario=idf   gold_rank overlap={} bm25control={} bm25code={} | distractor_rank overlap={} bm25control={} bm25code={}",
        show(og),
        show(cg),
        show(kg),
        show(od),
        show(cd),
        show(kd)
    );

    // Overlap ranks the keyword-stuffed distractor at/above the rare-term gold.
    assert!(
        od <= og,
        "Overlap should NOT float the rare-term gold above the stuffed distractor (gold={}, distractor={})",
        show(og),
        show(od)
    );
    // Both BM25 variants put the rare-term gold strictly above the distractor.
    assert!(
        kg < kd,
        "Bm25Code: rare-term gold must out-rank the stuffed distractor (gold={}, distractor={})",
        show(kg),
        show(kd)
    );
    assert!(
        cg < cd,
        "Bm25Control: rare-term gold must out-rank the stuffed distractor (gold={}, distractor={})",
        show(cg),
        show(cd)
    );
    // Non-vacuity, both directions: BM25 puts gold first, Overlap does not.
    assert_eq!(kg, Some(1), "Bm25Code ranks gold #1");
    assert_ne!(
        og,
        Some(1),
        "Overlap does NOT rank gold #1 (the flip is real)"
    );
}

// --- Scenario 2: length normalization -------------------------------------

/// GOLD is the rare-identifier match `zephyrine` buried in a LONG body (a
/// simulated long tool result). The DISTRACTOR is a SHORT body sharing only the
/// COMMON word `status`. Query = `zephyrine status`.
///
/// Overlap divides by body length, so the long gold's matched-token DENSITY is
/// tiny and the short distractor wins. BM25's b=0.75 only partially penalizes
/// length, and the rare `zephyrine` IDF dominates, so gold wins. The scorer swap
/// FLIPS the order.
#[tokio::test]
async fn length_normalization_rescues_buried_rare_match() {
    let store = InMemoryStore::default();
    let ctx = memphant_store_testkit::bind_context(&store, TenantId::new()).await;

    // ~60-token long body; the rare `zephyrine` appears once, near the end.
    let filler = "line of routine tool output about files and paths and modules and imports and tests and builds and logs and traces and metrics and threads and queues and caches and buffers and sockets and headers";
    let long_gold_body = format!("{filler} and finally the zephyrine handler returned");
    let gold = seed(&store, &ctx, &long_gold_body).await;
    let distractor = seed(&store, &ctx, "status ok").await;
    // A couple of short filler units so `status` is a common term.
    seed(&store, &ctx, "status pending").await;
    seed(&store, &ctx, "status failed").await;

    let query = "zephyrine status";
    let overlap = served(&store, &ctx, query, LexicalScorer::Overlap).await;
    let bm25c = served(&store, &ctx, query, LexicalScorer::Bm25Control).await;
    let bm25code = served(&store, &ctx, query, LexicalScorer::Bm25Code).await;

    let (og, od) = (rank(&overlap, gold), rank(&overlap, distractor));
    let (cg, cd) = (rank(&bm25c, gold), rank(&bm25c, distractor));
    let (kg, kd) = (rank(&bm25code, gold), rank(&bm25code, distractor));

    eprintln!(
        "[LEXICAL bakeoff] scenario=length gold_rank overlap={} bm25control={} bm25code={} | distractor_rank overlap={} bm25control={} bm25code={}",
        show(og),
        show(cg),
        show(kg),
        show(od),
        show(cd),
        show(kd)
    );

    // Overlap's length division sinks the buried gold below the short distractor.
    assert!(
        od < og,
        "Overlap should sink the long buried gold below the short distractor (gold={}, distractor={})",
        show(og),
        show(od)
    );
    // BM25 length-normalization + rare-term IDF floats gold back to the top.
    assert!(
        kg < kd,
        "Bm25Code: length norm must rescue the buried rare match (gold={}, distractor={})",
        show(kg),
        show(kd)
    );
    assert!(
        cg < cd,
        "Bm25Control: length norm must rescue the buried rare match (gold={}, distractor={})",
        show(cg),
        show(cd)
    );
    // Non-vacuity, both directions.
    assert_eq!(kg, Some(1), "Bm25Code ranks the buried gold #1");
    assert_ne!(
        og,
        Some(1),
        "Overlap does NOT rank the buried gold #1 (flip is real)"
    );
}

// --- Scenario 3: sub-token matching (Bm25Code vs Bm25Control) --------------

/// The query names only a PART of a whole identifier: `bar`, which lives inside
/// the path `src/foo/bar.py` in the GOLD body. It also names `parser`, a whole
/// token present in the DISTRACTOR body.
///
/// `Bm25Control` keeps `src/foo/bar.py` whole (its token chars include `/` and
/// `.`), so the query term `bar` never matches — gold is not a lexical candidate
/// at all (ABSENT), while the distractor's whole `parser` does match. `Bm25Code`
/// additionally emits the sub-token `bar`, surfacing gold. This isolates the
/// Code-vs-Control difference and validates the shipped default over the plain
/// control. (Overlap's tokenizer also sub-splits on `/`/`.`, so it likewise
/// surfaces gold — recorded here as an honest non-win for Code over Overlap on
/// this axis.)
#[tokio::test]
async fn sub_token_matching_is_unique_to_bm25_code_over_control() {
    let store = InMemoryStore::default();
    let ctx = memphant_store_testkit::bind_context(&store, TenantId::new()).await;

    let gold = seed(
        &store,
        &ctx,
        "TypeError raised in src/foo/bar.py during the run",
    )
    .await;
    let distractor = seed(&store, &ctx, "the parser module handles input").await;

    let query = "bar parser";
    let overlap = served(&store, &ctx, query, LexicalScorer::Overlap).await;
    let bm25c = served(&store, &ctx, query, LexicalScorer::Bm25Control).await;
    let bm25code = served(&store, &ctx, query, LexicalScorer::Bm25Code).await;

    let og = rank(&overlap, gold);
    let cg = rank(&bm25c, gold);
    let kg = rank(&bm25code, gold);
    let cd = rank(&bm25c, distractor);

    eprintln!(
        "[LEXICAL bakeoff] scenario=subtoken gold_rank overlap={} bm25control={} bm25code={} | control_distractor_rank={}",
        show(og),
        show(cg),
        show(kg),
        show(cd)
    );

    // Bm25Control keeps the path whole → the sub-token query never matches gold.
    assert_eq!(
        cg, None,
        "Bm25Control keeps `src/foo/bar.py` whole: the sub-token `bar` must NOT match gold"
    );
    // ...yet Control is alive: it still surfaces the whole-token `parser` distractor.
    assert!(
        cd.is_some(),
        "Bm25Control still matches the whole-token distractor — it is not globally empty"
    );
    // Bm25Code adds the sub-token → gold IS surfaced. This is the flip.
    assert!(
        kg.is_some(),
        "Bm25Code adds the sub-token `bar` and surfaces gold (gold={})",
        show(kg)
    );
    // Honest non-win: Overlap's tokenizer sub-splits too, so it also surfaces gold.
    assert!(
        og.is_some(),
        "Overlap's tokenizer sub-splits paths, so it also surfaces gold (gold={})",
        show(og)
    );
}
