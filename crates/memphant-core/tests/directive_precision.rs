//! Served-block precision at the turn-1 `/core` shape Syndai reads
//! (`docs/specs/graphify-understand-anything-napkin-digest.md`, Batch 0).
//! The fixture lives in `memphant_store_testkit::directive_precision` so the
//! real-model arm in `memphant-runtime` measures the identical store.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{EmbedError, EmbeddingProvider, InMemoryStore, NoopEmbedding, StubEmbedding};
use memphant_store_testkit::directive_precision::{
    self as fixture, CLOCK, NEGATED_PAIR_QUERY, SYNDAI_CORE_BUDGET, Summary, UNBOUNDED_BUDGET,
};
use memphant_types::TenantId;

/// The bge shape without the model: every unit sits at cosine in [0.6, 1]
/// against every query (bge similarities cluster there), so the dense channel
/// votes for the whole store, as it does in prod. A constant component
/// carrying 0.6 of the mass plus a non-negative hashed bag of words for the rest.
#[derive(Clone, Copy, Default)]
struct CompressedCosineEmbedding {
    inner: StubEmbedding,
}

impl EmbeddingProvider for CompressedCosineEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(self
            .inner
            .embed(texts)?
            .into_iter()
            .map(|bag| {
                std::iter::once(0.6_f32.sqrt())
                    .chain(bag.into_iter().map(|value| value * 0.4_f32.sqrt()))
                    .collect()
            })
            .collect())
    }

    fn dimensions(&self) -> usize {
        self.inner.dimensions() + 1
    }

    fn id(&self) -> &str {
        "test-compressed-cosine"
    }
}

async fn seeded(
    embedding: Arc<dyn EmbeddingProvider>,
) -> (MemoryService<InMemoryStore>, fixture::Fixture) {
    let store = Arc::new(InMemoryStore::default());
    let context = memphant_store_testkit::bind_context(store.as_ref(), TenantId::new()).await;
    let service = MemoryService::new(store, Arc::new(CLOCK), embedding);
    let seeded = fixture::seed(&service, context).await;
    (service, seeded)
}

async fn dense() -> (MemoryService<InMemoryStore>, fixture::Fixture) {
    seeded(Arc::new(CompressedCosineEmbedding::default())).await
}

async fn lexical_only() -> (MemoryService<InMemoryStore>, fixture::Fixture) {
    seeded(Arc::new(NoopEmbedding)).await
}

/// Sanity gate: with no budget pressure every case's gold is recalled on the
/// dense arm — some channel votes for it (the dense channel votes for the
/// whole store, so only a seeding failure can miss). If admission ever stops
/// landing the lane-3 learnings as recallable units, every other assertion
/// here measures nothing, so this one fails first. (Recalled, not served: the gap cutoff may rightly drop a
/// correctly seeded gold that only a weak dense vote reached.)
#[tokio::test]
async fn fixture_gold_is_recalled_at_unbounded_budget() {
    let (service, fixture) = dense().await;
    let results = fixture::run(&service, &fixture, UNBOUNDED_BUDGET).await;
    let missing: Vec<_> = results
        .iter()
        .filter(|result| !result.gold_recalled)
        .map(|result| result.id)
        .collect();
    assert!(missing.is_empty(), "gold never recalled: {missing:?}");
}

/// The served block at Syndai's `/core` shape before served-block precision
/// (content-term filter, dense gap cutoff, near-duplicate collapse): the
/// non-inferiority reference (spec Batch 0).
const BEFORE_DENSE: Summary = Summary {
    gold_recalled: 29,
    gold_in_pack: 28,
    served: 828,
    token_estimate: 34571,
    duplicate_served: 90,
    vector_only_non_gold: 69,
    stale_served: 28,
    gap_cut: 0,
};
const BEFORE_LEXICAL: Summary = Summary {
    gold_recalled: 28,
    gold_in_pack: 28,
    served: 759,
    token_estimate: 32222,
    duplicate_served: 83,
    vector_only_non_gold: 0,
    stale_served: 26,
    gap_cut: 0,
};

/// The same served block now, pinned exactly so any ranking change shows up.
const AFTER_DENSE: Summary = Summary {
    gold_recalled: 29,
    gold_in_pack: 28,
    served: 675,
    token_estimate: 29041,
    duplicate_served: 21,
    vector_only_non_gold: 542,
    stale_served: 0,
    gap_cut: 288,
};
const AFTER_LEXICAL: Summary = Summary {
    gold_recalled: 28,
    gold_in_pack: 28,
    served: 137,
    token_estimate: 5906,
    duplicate_served: 5,
    vector_only_non_gold: 0,
    stale_served: 2,
    gap_cut: 0,
};

/// The ship bar (spec Batch 5, "offline fixture"): every gold served before
/// is still served, with fewer tokens.
#[tokio::test]
async fn served_block_keeps_gold_with_fewer_tokens() {
    for (arm, (service, fixture), before, after) in [
        ("dense", dense().await, BEFORE_DENSE, AFTER_DENSE),
        (
            "lexical",
            lexical_only().await,
            BEFORE_LEXICAL,
            AFTER_LEXICAL,
        ),
    ] {
        let results = fixture::run(&service, &fixture, SYNDAI_CORE_BUDGET).await;
        let summary = fixture::report(arm, &results);
        assert!(
            summary.gold_in_pack >= before.gold_in_pack,
            "{arm}: gold non-inferior"
        );
        assert!(
            summary.token_estimate < before.token_estimate,
            "{arm}: fewer tokens"
        );
        assert_eq!(summary, after, "{arm} arm");
    }
}

/// Opposite rules share every content term ("Use the Stripe SDK…" / "Do not
/// use the Stripe SDK…"); the near-duplicate collapse must serve both.
#[tokio::test]
async fn negated_pair_is_served_together() {
    let (service, fixture) = dense().await;
    let read = fixture::core_read(&service, &fixture, NEGATED_PAIR_QUERY, SYNDAI_CORE_BUDGET).await;
    for id in fixture.negated_pair() {
        assert!(
            read.served.contains(&id),
            "negated half {id:?} served: {:?}",
            read.served
        );
    }
}
