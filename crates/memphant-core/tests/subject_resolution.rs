//! Subject identity for restated preferences.
//!
//! W6 extraction mines an honest subject key only when the sentence carries an
//! explicit topic slot ("my favorite tea is chamomile" → `preference:favorite
//! tea`). An open-ended restatement has no such slot, so `clean_object` takes
//! the whole object phrase as the subject: "i prefer getting this broken down
//! step by step" and "i prefer having actual steps to follow" mint two
//! different keys, and AUTO-KEYS-aside, two different keys never supersede.
//! Both beliefs then reach the reader and the obsolete one competes with the
//! current one — the measured HorizonBench defect
//! (`docs/build-log/2026-08-05-horizon-stage1-supersession-defect.md`).
//!
//! These tests pin the resolution step: before admission, a candidate whose
//! subject phrase is semantically the same as an open unit's adopts that
//! unit's fact key, and the existing subject-key supersedence machinery does
//! the rest. Off by default, so the flag-off path is byte-identical to today.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{EmbedError, EmbeddingProvider, FixedClock, InMemoryStore};
use memphant_types::{
    ResolvedMemoryContext, RetainEpisodeHttpRequest, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-08-05T00:00:00Z");

/// Deterministic topical embedder: each dimension counts a topic marker, so
/// two differently-worded step-by-step preferences land on the same axis and
/// an unrelated preference lands on another. No model, no network.
struct TopicEmbedding;

const TOPICS: [&[&str]; 3] = [
    &["step", "steps", "sequential"],
    &["coffee", "espresso", "latte"],
    &["music", "jazz", "album"],
];

impl EmbeddingProvider for TopicEmbedding {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(texts
            .iter()
            .map(|text| {
                let lowered = text.to_ascii_lowercase();
                let mut vector: Vec<f32> = TOPICS
                    .iter()
                    .map(|markers| {
                        markers
                            .iter()
                            .filter(|marker| lowered.contains(*marker))
                            .count() as f32
                    })
                    .collect();
                // An unmarked phrase still needs a non-zero vector, or cosine
                // similarity is undefined and every pair would look identical.
                if vector.iter().all(|value| *value == 0.0) {
                    vector.push(1.0);
                } else {
                    vector.push(0.0);
                }
                vector
            })
            .collect())
    }

    fn dimensions(&self) -> usize {
        4
    }

    fn id(&self) -> &str {
        "topic-test"
    }
}

fn service(store: InMemoryStore, threshold: Option<f32>) -> MemoryService<InMemoryStore> {
    MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(TopicEmbedding))
        .with_fact_extraction_enabled(true)
        .with_subject_resolution_threshold(threshold)
}

async fn retain_and_reflect(
    svc: &MemoryService<InMemoryStore>,
    context: &ResolvedMemoryContext,
    body: &str,
) {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    std::hash::Hash::hash(body, &mut hasher);
    let idempotency_key = format!("test:{:x}", std::hash::Hasher::finish(&hasher));
    svc.retain(
        context,
        &idempotency_key,
        TrustLevel::TrustedUser,
        RetainEpisodeHttpRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_ref: format!("test:subject-resolution:{idempotency_key}"),
            observed_at: "2026-08-04T00:00:00Z".to_string(),
            payload: memphant_types::RetainPayload::Episode(memphant_types::RetainEpisodePayload {
                source_kind: "user".to_string(),
                body: body.to_string(),
                subject: None,
                predicate: None,
            }),
        },
    )
    .await
    .expect("retain");
    svc.run_worker_tick(usize::MAX).await.expect("reflect");
}

const FIRST: &str = "[session s1]\n\
user: I prefer getting this broken down step by step.\n\
assistant: Understood, I will break it down.\n";

const RESTATED: &str = "[session s2]\n\
user: I prefer having actual steps to follow now.\n\
assistant: Noted, steps it is.\n";

const UNRELATED: &str = "[session s3]\n\
user: I prefer drinking coffee in the morning.\n\
assistant: Coffee it is.\n";

/// Headline: the restatement adopts the earlier preference's subject key and
/// closes its generation, so only the current belief is served.
#[tokio::test]
async fn a_restated_preference_supersedes_the_earlier_wording() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), Some(0.9));
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    retain_and_reflect(&svc, &context, FIRST).await;
    retain_and_reflect(&svc, &context, RESTATED).await;

    let units = store.memory_units(tenant);
    let preference_keys: Vec<&str> = units
        .iter()
        .filter_map(|unit| unit.fact_key.as_deref())
        .filter(|key| key.contains(":preference:"))
        .collect();
    let distinct: std::collections::HashSet<&&str> = preference_keys.iter().collect();
    assert_eq!(
        distinct.len(),
        1,
        "both wordings must share one subject key, got {preference_keys:?}"
    );

    let superseded = units
        .iter()
        .filter(|unit| unit.state == UnitState::Superseded)
        .count();
    assert!(
        superseded >= 1,
        "the earlier wording must have a closed generation"
    );
    assert!(
        units.iter().any(|unit| unit.state == UnitState::Active
            && unit.valid_to.is_none()
            && unit.body.contains("actual steps")),
        "the restatement must be the open head"
    );
}

/// Guard: resolution must not collapse genuinely different preferences. A user
/// holds many at once; merging them would delete belief, not update it.
#[tokio::test]
async fn an_unrelated_preference_keeps_its_own_subject() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), Some(0.9));
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    retain_and_reflect(&svc, &context, FIRST).await;
    retain_and_reflect(&svc, &context, UNRELATED).await;

    let units = store.memory_units(tenant);
    let distinct: std::collections::HashSet<&str> = units
        .iter()
        .filter_map(|unit| unit.fact_key.as_deref())
        .filter(|key| key.contains(":preference:"))
        .collect();
    assert_eq!(
        distinct.len(),
        2,
        "unrelated preferences must not merge, got {distinct:?}"
    );
}

/// Two phrases mined from ONE episode can both sit near the same open unit.
/// Both adopting its key opens one subject twice over overlapping validity,
/// which Postgres rejects on `memphant_memory_unit_subject_valid_excl` and
/// which takes the whole reflect job down with it (observed draining the
/// Horizon sample at threshold 0.80). At most one candidate per subject per
/// job; the loser mints its own key as it did before resolution existed.
#[tokio::test]
async fn two_phrases_in_one_episode_never_claim_the_same_subject() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), Some(0.9));
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    retain_and_reflect(&svc, &context, FIRST).await;
    // Both sentences are step-topical, so both resolve toward the FIRST unit.
    retain_and_reflect(
        &svc,
        &context,
        "[session s2]\n\
user: I prefer having actual steps to follow.\n\
assistant: Noted.\n\
user: I also prefer sequential steps with numbers.\n",
    )
    .await;

    let units = store.memory_units(tenant);
    let mut open_subjects: Vec<&str> = units
        .iter()
        .filter(|unit| unit.state == UnitState::Active && unit.valid_to.is_none())
        .filter_map(|unit| unit.fact_key.as_deref())
        .filter(|key| key.contains(":preference:"))
        .collect();
    let before = open_subjects.len();
    open_subjects.sort_unstable();
    open_subjects.dedup();
    assert_eq!(
        open_subjects.len(),
        before,
        "one subject may hold only one open head, got {open_subjects:?}"
    );
}

/// The flag-off path is exactly today's behaviour: two wordings, two keys.
#[tokio::test]
async fn resolution_off_leaves_the_two_wordings_separate() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), None);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    retain_and_reflect(&svc, &context, FIRST).await;
    retain_and_reflect(&svc, &context, RESTATED).await;

    let units = store.memory_units(tenant);
    let distinct: std::collections::HashSet<&str> = units
        .iter()
        .filter_map(|unit| unit.fact_key.as_deref())
        .filter(|key| key.contains(":preference:"))
        .collect();
    assert_eq!(distinct.len(), 2, "off must not resolve, got {distinct:?}");
}
