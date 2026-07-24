//! R1.5-T0: `recall_pool_depth` decouples the recall engine's internal
//! fan-out from the caller's requested `k`. D1 proved the OLD coupling was a
//! correctness bug in ranking terms: Fast-mode packing clamped its
//! candidate-consideration scan window to exactly `output_limit == k`, so a
//! caller requesting a bigger `k` handed the greedy fill a wider window to
//! skip past subject-dedup drops — which changed even the top-5 composition
//! (R@5 0.067→0.167 for k=10 vs k=50 on the same corpus/query,
//! `docs/build-log/2026-07-12-r1-docs-gate.md`).
//!
//! `k5_and_k50_over_the_same_corpus_produce_identical_top5` is the bug's
//! tombstone: it reproduces the SAME shape (a top-ranked subject-key
//! monopoly whose duplicates get dropped, forcing the fill to backfill from
//! lower-ranked distinct candidates) and asserts the top-5 no longer moves
//! when `k` changes. Before the fix this fixture is RED — with 5 duplicate-
//! subject candidates ranked 1..5 and `scan_limit == k`, a k=5 request never
//! scans far enough to see any backfill candidate (`items == [dup_a]`, len
//! 1), while a k=50 request scans deep enough to backfill 49 slots from the
//! distinct-subject filler pool — different top-5. After the fix both k=5 and
//! k=50 scan the SAME `recall_pool_depth`-wide window (64, ≫ either k here),
//! so the top-5 composition and order are byte-identical regardless of `k`.

use memphant_core::{FixedClock, InMemoryStore, MemoryStore, recall};
use memphant_types::{
    MemoryKind, NewMemoryUnit, RecallMode, RecallRequest, ResolvedMemoryContext, TenantId,
    TrustLevel, UnitId, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-12T00:00:00Z");

fn tenant(value: u128) -> TenantId {
    TenantId::from_u128(value)
}

fn new_unit(context: &ResolvedMemoryContext, fact_key: &str, body: String) -> NewMemoryUnit {
    NewMemoryUnit {
        tenant_id: context.tenant_id,
        data_subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        kind: MemoryKind::Semantic,
        state: UnitState::Active,
        fact_key: Some(fact_key.to_string()),
        predicate: None,
        body,
        confidence: Some(1.0),
        trust_level: TrustLevel::TrustedSystem,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(context.actor_id),
        source_kind: Some("fixture".to_string()),
        source_ref: "test:fixture".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
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

fn request(context: &ResolvedMemoryContext, query: &str, k: usize) -> RecallRequest {
    RecallRequest {
        context: context.clone(),
        query: query.to_string(),
        k,
        budget_tokens: 20_000,
        mode: RecallMode::Fast,
        include_beliefs: false,
        edge_expansion_enabled: false,
        context_packing_abstention_enabled: true,
        procedure_recall_enabled: true,
        decay_enabled: true,
        engine_version: "engine-r15t0-test".to_string(),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// Seeds a corpus shaped to exercise the D1 mechanism: 5 units that all share
/// ONE `fact_key` (so subject-dedup admits only the best-ranked one and
/// drops the other 4) ranked strictly ABOVE 70 distinct-subject "filler"
/// units the fill must backfill from. Returns `(dup_survivor_id, filler_ids
/// in rank order)`.
async fn seed_dedup_monopoly_corpus(
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
) -> (UnitId, Vec<UnitId>) {
    let mut tx = store.begin(context).await.expect("begin transaction");

    // The 5 candidates ranked 1..5 by lexical overlap ratio: pure signal
    // tokens ("gamma trench outpost") plus one distinguishing suffix token
    // that does NOT change the ratio (identical token count -> identical
    // score for all 5), so admission order is the body-text tiebreak alone
    // (`a` < `b` < `c` < `d` < `e`) — deterministic, and "a" is always the
    // one dedup keeps.
    let mut dup_ids = Vec::new();
    for suffix in ["a", "b", "c", "d", "e"] {
        let id = store
            .stage_memory_unit(
                &mut tx,
                new_unit(
                    context,
                    "dup-subject-key",
                    format!("gamma trench outpost note {suffix}"),
                ),
            )
            .await
            .expect("dup unit seeded");
        dup_ids.push(id);
    }
    let dup_survivor_id = dup_ids[0];

    // 70 distinct-subject fillers, diluted lexical ratio (well below the dup
    // units' 3/5 = 0.6) so they rank strictly below all 5 dup candidates, but
    // still clearly above zero (every filler shares the same 3 signal tokens
    // plus a fixed amount of non-matching padding, so every filler ties on
    // score too — the body-embedded index is ONE token regardless of digit
    // width, so it never perturbs the ratio). Fill order ties break on body
    // text, and `{index:03}` zero-pads so that tiebreak is also rank order.
    let mut filler_ids = Vec::new();
    for index in 0..70u32 {
        let id = store
            .stage_memory_unit(
                &mut tx,
                new_unit(
                    context,
                    &format!("filler-subject-{index:03}"),
                    format!(
                        "gamma trench outpost filler entry {index:03} with additional \
                         padding words that dilute this candidate below the dup cluster"
                    ),
                ),
            )
            .await
            .expect("filler unit seeded");
        filler_ids.push(id);
    }
    store.commit(tx).await.expect("seed committed");
    (dup_survivor_id, filler_ids)
}

/// The D1 tombstone: k=5 and k=50 over the identical corpus/query must
/// produce byte-identical top-5 ordering. See the module doc for why this
/// fixture is RED on the pre-R1.5-T0 `scan_limit == k` coupling.
#[tokio::test]
async fn k5_and_k50_over_the_same_corpus_produce_identical_top5() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(90_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let (dup_survivor_id, _filler_ids) = seed_dedup_monopoly_corpus(&store, &context).await;

    let query = "gamma trench outpost";

    let k5 = recall(&store, request(&context, query, 5), None, &CLOCK)
        .await
        .expect("k=5 recall succeeds");
    let k50 = recall(&store, request(&context, query, 50), None, &CLOCK)
        .await
        .expect("k=50 recall succeeds");

    // Sanity: the dedup monopoly actually fired (only ONE of the 5
    // same-subject duplicates survives packing) and both responses saw it —
    // otherwise this fixture isn't exercising the mechanism it claims to.
    assert!(
        k5.candidate_whitelist.contains(&dup_survivor_id),
        "k=5 must surface the dedup survivor: {:?}",
        k5.candidate_whitelist
    );
    assert!(
        k50.candidate_whitelist.contains(&dup_survivor_id),
        "k=50 must surface the dedup survivor: {:?}",
        k50.candidate_whitelist
    );
    assert_eq!(k5.items.len(), 5, "k=5 pack fills to exactly k");
    assert!(
        k50.items.len() > 5,
        "k=50 must actually admit more than 5 items for the top-5 comparison to be meaningful, got {}",
        k50.items.len()
    );

    // THE tombstone assertion: identical unit ids, in identical order, for
    // the first 5 items regardless of whether the caller asked for k=5 or
    // k=50 — internal fan-out no longer scales with the caller's `k`.
    let k5_top5: Vec<UnitId> = k5.items.iter().map(|item| item.unit_id).collect();
    let k50_top5: Vec<UnitId> = k50.items[..5].iter().map(|item| item.unit_id).collect();
    assert_eq!(
        k5_top5, k50_top5,
        "k=5 and k=50 must produce an identical top-5 over the same corpus/query \
         (D1 tombstone) — k5={k5_top5:?} k50_top5={k50_top5:?}"
    );

    // Also pin the exact bodies (not just ids) so a future change that
    // preserves id-order but alters WHICH text got packed is still caught.
    let k5_bodies: Vec<&str> = k5.items.iter().map(|item| item.body.as_str()).collect();
    let k50_bodies: Vec<&str> = k50.items[..5]
        .iter()
        .map(|item| item.body.as_str())
        .collect();
    assert_eq!(k5_bodies, k50_bodies);
}

/// Companion positive control: with the dedup monopoly REMOVED (every
/// candidate has a distinct subject key), there is nothing to backfill and
/// k=5 vs k=50 already agreed even under the pre-fix `scan_limit == k`
/// coupling — top-5 identical is the trivial case. Kept as a guard against a
/// vacuously-true tombstone (e.g. a regression that makes `recall_pool_depth`
/// inert would still pass a corpus with no dedup pressure).
#[tokio::test]
async fn k5_and_k50_agree_trivially_without_dedup_pressure() {
    let store = InMemoryStore::default();
    let tenant_id = tenant(90_100);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;

    let mut tx = store.begin(&context).await.expect("begin transaction");
    for index in 0..75u32 {
        store
            .stage_memory_unit(
                &mut tx,
                new_unit(
                    &context,
                    &format!("distinct-subject-{index:03}"),
                    format!("gamma trench outpost distinct entry {index:03}"),
                ),
            )
            .await
            .expect("distinct unit seeded");
    }
    store.commit(tx).await.expect("seed committed");

    let query = "gamma trench outpost";
    let k5 = recall(&store, request(&context, query, 5), None, &CLOCK)
        .await
        .expect("k=5 recall succeeds");
    let k50 = recall(&store, request(&context, query, 50), None, &CLOCK)
        .await
        .expect("k=50 recall succeeds");

    let k5_top5: Vec<UnitId> = k5.items.iter().map(|item| item.unit_id).collect();
    let k50_top5: Vec<UnitId> = k50.items[..5].iter().map(|item| item.unit_id).collect();
    assert_eq!(k5_top5, k50_top5);
}
