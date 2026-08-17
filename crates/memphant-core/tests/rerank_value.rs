//! # Does the cross-encoder rerank stage improve recall ORDERING? (measurement)
//!
//! ## Hypothesis
//! The recall pipeline's optional cross-encoder rerank stage (installed via
//! `MemoryService::with_cross_reranker`, default OFF) reorders the top
//! `recall_pool_depth` FUSED candidates by a real `(query, body)` relevance
//! score BEFORE packing. The claim under test: when weighted-RRF fusion
//! mis-orders a keyword-stuffed DISTRACTOR above the true GOLD answer, a
//! reranker that scores by true relevance PROMOTES gold above the distractor —
//! and, symmetrically, does NOT demote a gold that fusion already ranked first.
//!
//! ## Corpus design (adversarial-for-fusion, by construction)
//! Vector channel is OFF (`NoopEmbedding`), so fusion order is driven by the
//! default `Bm25Code` lexical channel. We seed:
//!
//! - a DISTRACTOR that keyword-stuffs every query term (multiple query-token
//!   matches ⇒ top BM25 ⇒ fusion rank 1), but is content-free boilerplate;
//! - a GOLD unit that is the genuine answer yet shares only ONE query token
//!   (low BM25 ⇒ fusion ranks it BELOW the distractor, but still in the pool);
//! - unrelated filler units (never enter the lexical pool).
//!
//! A unit needs a positive channel score to enter the fused set, so gold shares
//! exactly one query token — enough to be a candidate, few enough to lose fusion.
//!
//! ## Reranker: oracle (mechanism/ceiling), not the shipped model
//! The real `FastEmbedCrossReranker` (`memphant-runtime`, `fastembed` feature,
//! `BAAI/bge-reranker-base`) is NOT reachable from a `memphant-core` test — it
//! would require adding a `memphant-runtime` dev-dependency and building the
//! `fastembed`/onnxruntime stack. So we install a deterministic ORACLE reranker
//! that scores the known-gold body 1.0 and everything else 0.0. This measures
//! (a) the PLUMBING — that a reranker's scores actually reorder recall output —
//! and (b) the CEILING a perfect reranker reaches. It does NOT measure the real
//! model's relevance quality; that needs the bench lane's `--cross-rerank`
//! against a paired corpus.
//!
//! ## Non-vacuity (golden rule) — the promotion must FLIP under perturbation
//! Three arms over the SAME corpus/query:
//!
//! - OFF (no reranker) ⇒ gold ranks BELOW the distractor.
//! - ORACLE ON (relevance score) ⇒ gold ranks ABOVE the distractor (rank 0).
//! - NEUTRAL ON (all-equal score) ⇒ gold UNCHANGED (still below distractor).
//!
//! The NEUTRAL arm is the perturbation: it isolates the reranker's SCORES as the
//! cause. If merely installing a rerank stage (regardless of its scores) moved
//! gold, the neutral arm would move it too — it must not.
//!
//! ## No-op / no-harm
//! A second corpus where fusion ALREADY ranks gold first: both the oracle and
//! the neutral reranker must keep gold at rank 0 (rerank must not scramble a
//! correct order).
//!
//! ## Verdict semantics
//! POSITIVE mechanism: `rank(gold) OFF > rank(gold) ORACLE-ON`, the neutral arm
//! leaves gold where fusion put it, and the no-op arm keeps a correct gold at 0.
//! That proves the layer CAN fix fusion mis-orderings and is wired correctly.
//! It does NOT prove the SHIPPED model helps on real queries — that is the
//! separate, paid, paired-corpus question this test brackets.
//!
//! ## Store-divergence caveat
//! `InMemoryStore::fetch_recall_candidates` returns ALL units, so gold enters
//! the pool on one shared query token. Postgres BOUNDS the candidate pool, so a
//! gold with near-zero lexical overlap could be pruned before rerank ever sees
//! it. Rerank operates on the post-fusion set (identical on both stores), so
//! this ORDERING result generalizes; pool ADMISSION does not.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{CrossReranker, CrossRerankerConfig, FixedClock, InMemoryStore, NoopEmbedding};
use memphant_types::{
    RecallHttpRequest, RecallMode, ResolvedMemoryContext, RetainEpisodeHttpRequest,
    RetainEpisodePayload, RetainPayload, TenantId, TrustLevel,
};

const CLOCK: FixedClock = FixedClock("2026-08-17T00:00:00Z");

// A unique phrase living ONLY in the gold body, used both as the oracle's
// relevance key and to locate gold in the recall output.
const GOLD_MARKER: &str = "full jitter decorrelates retries across clients";
const DISTRACTOR_MARKER: &str = "retry backoff jitter configure retry backoff jitter index";

/// Oracle reranker: scores the known-gold doc 1.0, everything else 0.0. It is a
/// perfect relevance signal for THIS corpus — the ceiling a reranker can reach,
/// not a model.
struct OracleReranker {
    gold_marker: String,
}

impl CrossReranker for OracleReranker {
    fn config(&self) -> CrossRerankerConfig {
        CrossRerankerConfig {
            provider: "test".to_string(),
            model: "oracle".to_string(),
            candidate_limit: 64,
            max_length: 0, // unbounded: UnitBody granularity feeds whole bodies
            batch_size: None,
        }
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        Ok(docs
            .iter()
            .map(|doc| {
                if doc.contains(&self.gold_marker) {
                    1.0
                } else {
                    0.0
                }
            })
            .collect())
    }
}

/// Neutral reranker: one finite, EQUAL score per doc. The rerank stage runs and
/// its sort is stable, so it must leave the fused order untouched. This is the
/// perturbation control that isolates the oracle's scores as the cause of any
/// promotion.
struct NeutralReranker;

impl CrossReranker for NeutralReranker {
    fn config(&self) -> CrossRerankerConfig {
        CrossRerankerConfig {
            provider: "test".to_string(),
            model: "neutral".to_string(),
            candidate_limit: 64,
            max_length: 0,
            batch_size: None,
        }
    }

    fn rerank(&self, _query: &str, docs: &[&str]) -> Result<Vec<f32>, String> {
        Ok(vec![0.0; docs.len()])
    }
}

fn episode(context: &ResolvedMemoryContext, tag: &str, body: &str) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: format!("test:rerank:{tag}"),
        observed_at: "2026-08-17T00:00:00Z".to_string(),
        payload: RetainPayload::Episode(RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
            subject: None,
            predicate: None,
        }),
    }
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
        // Wide enough that both gold and distractor are returned, never trimmed
        // by k or budget — we are measuring ORDER, not the packing cut.
        limit: Some(20),
        budget_tokens: Some(100_000),
        mode: Some(RecallMode::Fast),
        include_beliefs: Some(true),
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// Seeds a fresh store+context with the given bodies (lexical-only fusion) and
/// returns a lexical-only service reader plus the context. The vector channel
/// is off, so BM25 alone drives fusion order.
async fn seed(bodies: &[(&str, &str)]) -> (InMemoryStore, ResolvedMemoryContext) {
    let store = InMemoryStore::default();
    let seeder = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    for (tag, body) in bodies {
        seeder
            .retain(
                &context,
                &format!("test:rerank:{tag}"),
                TrustLevel::TrustedUser,
                episode(&context, tag, body),
            )
            .await
            .expect("retain");
    }
    seeder.run_worker_tick(usize::MAX).await.expect("reflect");
    (store, context)
}

fn lexical_service(store: &InMemoryStore) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
}

/// Index of the first returned item whose body contains `marker`, or `None` if
/// the unit was not returned at all.
async fn rank_of(
    service: &MemoryService<InMemoryStore>,
    context: &ResolvedMemoryContext,
    query: &str,
    marker: &str,
) -> Option<usize> {
    let response = service
        .recall(context.clone(), recall_request(context, query))
        .await
        .expect("recall");
    response
        .items
        .iter()
        .position(|item| item.body.contains(marker))
}

/// MAIN measurement: fusion mis-orders (distractor above gold); the oracle
/// reranker promotes gold above the distractor; the neutral reranker does not.
#[tokio::test]
async fn cross_rerank_promotes_gold_over_a_fusion_distractor() {
    // Query terms: configure / retry / backoff / jitter.
    // DISTRACTOR stuffs all four (top BM25); GOLD shares only "jitter".
    let gold_body =
        format!("Prefer {GOLD_MARKER}: sample each sleep uniformly from zero to the current cap.");
    let distractor_body = DISTRACTOR_MARKER.to_string();
    let (store, context) = seed(&[
        ("distractor", distractor_body.as_str()),
        ("gold", gold_body.as_str()),
        (
            "filler1",
            "The kubernetes ingress terminates TLS at the edge proxy.",
        ),
        (
            "filler2",
            "Postgres autovacuum thresholds scale with table bloat.",
        ),
    ])
    .await;
    let query = "configure retry backoff jitter";

    // --- OFF: fusion order (no reranker). ---
    let off = lexical_service(&store);
    let gold_off = rank_of(&off, &context, query, GOLD_MARKER)
        .await
        .expect("gold is a candidate under fusion");
    let distractor_off = rank_of(&off, &context, query, DISTRACTOR_MARKER)
        .await
        .expect("distractor is a candidate under fusion");

    // --- ORACLE ON: relevance scores reorder the fused head. ---
    let oracle_on = lexical_service(&store).with_cross_reranker(Arc::new(OracleReranker {
        gold_marker: GOLD_MARKER.to_string(),
    }));
    let gold_oracle = rank_of(&oracle_on, &context, query, GOLD_MARKER)
        .await
        .expect("gold still returned with oracle rerank");
    let distractor_oracle = rank_of(&oracle_on, &context, query, DISTRACTOR_MARKER)
        .await
        .expect("distractor still returned with oracle rerank");

    // --- NEUTRAL ON (perturbation control): equal scores ⇒ no reorder. ---
    let neutral_on = lexical_service(&store).with_cross_reranker(Arc::new(NeutralReranker));
    let gold_neutral = rank_of(&neutral_on, &context, query, GOLD_MARKER)
        .await
        .expect("gold still returned with neutral rerank");

    eprintln!(
        "[RERANK verdict] MECHANISM/CEILING (oracle) — real-model value pending model. \
         MAIN: rank(gold) OFF={gold_off} distractor_OFF={distractor_off} | \
         ORACLE-ON gold={gold_oracle} distractor={distractor_oracle} | \
         NEUTRAL-ON gold={gold_neutral} (== OFF ⇒ scores, not the stage, moved gold)"
    );

    // Precondition: fusion really mis-orders (else the test is vacuous).
    assert!(
        distractor_off < gold_off,
        "corpus precondition: fusion must rank the distractor ABOVE gold \
         (distractor_off={distractor_off}, gold_off={gold_off})"
    );
    // The load-bearing direction: the oracle promotes gold above the distractor.
    assert!(
        gold_oracle < distractor_oracle,
        "oracle rerank must promote gold above the distractor \
         (gold={gold_oracle}, distractor={distractor_oracle})"
    );
    assert_eq!(
        gold_oracle, 0,
        "a perfect relevance oracle lifts gold to the head of the pack"
    );
    // Non-vacuity: the promotion FLIPS under perturbation. The neutral reranker
    // (equal scores) leaves gold exactly where fusion put it — proving the
    // oracle's SCORES, not the mere presence of a rerank stage, moved gold.
    assert_eq!(
        gold_neutral, gold_off,
        "perturbation: a neutral (equal-score) reranker must NOT promote gold \
         (neutral={gold_neutral}, off={gold_off})"
    );
    assert!(
        gold_neutral > distractor_off || gold_neutral == gold_off,
        "perturbation: gold stays below the distractor under the neutral reranker"
    );
}

/// NO-OP / NO-HARM: when fusion already ranks gold first, neither the oracle nor
/// a neutral reranker may demote it.
#[tokio::test]
async fn cross_rerank_does_not_demote_an_already_correct_gold() {
    // Here GOLD keyword-matches the query best, so fusion already ranks it #1.
    let gold_body = format!("configure retry backoff jitter settings — {GOLD_MARKER}.");
    let (store, context) = seed(&[
        ("gold", gold_body.as_str()),
        (
            "weak",
            "A stray note mentioning jitter once and nothing else relevant.",
        ),
        (
            "filler",
            "Redis eviction uses an approximated LRU sampling policy.",
        ),
    ])
    .await;
    let query = "configure retry backoff jitter settings";

    let off = lexical_service(&store);
    let gold_off = rank_of(&off, &context, query, GOLD_MARKER)
        .await
        .expect("gold candidate");

    let oracle_on = lexical_service(&store).with_cross_reranker(Arc::new(OracleReranker {
        gold_marker: GOLD_MARKER.to_string(),
    }));
    let gold_oracle = rank_of(&oracle_on, &context, query, GOLD_MARKER)
        .await
        .expect("gold candidate with oracle");

    let neutral_on = lexical_service(&store).with_cross_reranker(Arc::new(NeutralReranker));
    let gold_neutral = rank_of(&neutral_on, &context, query, GOLD_MARKER)
        .await
        .expect("gold candidate with neutral");

    eprintln!(
        "[RERANK verdict] NO-OP: fusion already correct — rank(gold) OFF={gold_off} \
         ORACLE-ON={gold_oracle} NEUTRAL-ON={gold_neutral} (rerank must not demote #1)"
    );

    assert_eq!(
        gold_off, 0,
        "corpus precondition: fusion already ranks gold #1"
    );
    assert_eq!(
        gold_oracle, 0,
        "an agreeing reranker must not demote an already-correct #1 gold"
    );
    assert_eq!(
        gold_neutral, 0,
        "a neutral reranker must not scramble an already-correct #1 gold"
    );
}
