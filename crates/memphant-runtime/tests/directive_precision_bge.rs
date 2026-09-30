//! Real-model arm of the served-block precision fixture
//! (`docs/specs/graphify-understand-anything-napkin-digest.md`, Batch 0): the
//! same store and `/core` reads as `memphant-core/tests/directive_precision.rs`,
//! embedded by bge-small-en-v1.5 — the dense channel prod runs
//! (`fly.toml` `MEMPHANT_EMBEDDINGS='small'`). CPU, deterministic, no network
//! once the fastembed model cache is warm:
//!
//! ```sh
//! FASTEMBED_CACHE_DIR=<warm cache> cargo test -p memphant-runtime --features fastembed \
//!   --test directive_precision_bge -- --ignored --nocapture
//! ```
#![cfg(feature = "fastembed")]

use std::sync::Arc;

use memphant_core::InMemoryStore;
use memphant_core::service::MemoryService;
use memphant_runtime::embeddings::FastEmbedProvider;
use memphant_store_testkit::directive_precision::{
    self as fixture, CLOCK, SYNDAI_CORE_BUDGET, Summary,
};
use memphant_types::TenantId;

/// bge-small's served block at Syndai's `/core` shape before served-block
/// precision: the non-inferiority reference (spec Batch 0).
const BEFORE_BGE: Summary = Summary {
    gold_recalled: 29,
    gold_in_pack: 29,
    served: 837,
    token_estimate: 34590,
    duplicate_served: 95,
    vector_only_non_gold: 77,
    stale_served: 26,
    gap_cut: 0,
};

/// The same served block now, pinned exactly.
const AFTER_BGE: Summary = Summary {
    gold_recalled: 29,
    gold_in_pack: 29,
    served: 496,
    token_estimate: 19121,
    duplicate_served: 45,
    vector_only_non_gold: 362,
    stale_served: 0,
    gap_cut: 482,
};

#[tokio::test]
#[ignore = "loads bge-small-en-v1.5 from the fastembed cache (downloads on a cold cache)"]
async fn bge_small_served_block_precision() {
    let store = Arc::new(InMemoryStore::default());
    let context = memphant_store_testkit::bind_context(store.as_ref(), TenantId::new()).await;
    let embedder = FastEmbedProvider::new().expect("bge-small-en-v1.5");
    let service = MemoryService::new(store, Arc::new(CLOCK), Arc::new(embedder));
    let fixture = fixture::seed(&service, context).await;

    let results = fixture::run(&service, &fixture, SYNDAI_CORE_BUDGET).await;
    let summary = fixture::report("bge", &results);
    assert!(
        summary.gold_in_pack >= BEFORE_BGE.gold_in_pack,
        "gold non-inferior"
    );
    assert!(
        summary.token_estimate < BEFORE_BGE.token_estimate,
        "fewer tokens"
    );
    assert_eq!(summary, AFTER_BGE);
}
