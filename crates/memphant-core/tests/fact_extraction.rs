//! W6 deterministic fact extraction, end-to-end through the service
//! (retain → reflect): when `with_fact_extraction_enabled` is on, the reflect
//! stage mines first-person preference/attribute statements from an episode's
//! USER turns and emits EXTRA short, honest-subject-key ReflectCandidates that
//! flow through the existing admission machinery — so the "my favorite X is now
//! Z" update chain finally supersedes. All behind the flag, default off, so the
//! flag-off path is byte-identical to today.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, NoopEmbedding, derive_fact_key};
use memphant_types::{
    ResolvedMemoryContext, RetainEpisodeHttpRequest, ScopeId, TenantId, TrustLevel, UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-10T00:00:00Z");

fn service(store: InMemoryStore, fact_extraction: bool) -> MemoryService<InMemoryStore> {
    MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_fact_extraction_enabled(fact_extraction)
}

fn retain_request(context: &ResolvedMemoryContext, body: &str) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "test:fixture".to_string(),
        observed_at: "2026-07-09T00:00:00Z".to_string(),
        payload: memphant_types::RetainPayload::Episode(memphant_types::RetainEpisodePayload {
            source_kind: "user".to_string(),
            body: body.to_string(),
            subject: None,
            predicate: None,
        }),
    }
}

async fn retain_and_reflect(
    svc: &MemoryService<InMemoryStore>,
    context: &ResolvedMemoryContext,
    body: &str,
) {
    // The idempotency key must be unique per distinct request within a
    // tenant (that is what "idempotency key" means): a fixed compile-time
    // constant here would collide across the multiple retains a single test
    // issues under the same bound context. Hashing the body keeps the key
    // deterministic (same content ⇒ same key) while staying well under the
    // store's 255-byte limit, unlike using the (often long) body verbatim.
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    std::hash::Hash::hash(body, &mut hasher);
    let idempotency_key = format!("test:{:x}", std::hash::Hasher::finish(&hasher));
    svc.retain(
        context,
        &idempotency_key,
        TrustLevel::TrustedUser,
        retain_request(context, body),
    )
    .await
    .expect("retain");
    svc.run_worker_tick(usize::MAX).await.expect("reflect");
}

/// §5 update-chain integration (the headline test): episode A asserts
/// "my favorite tea is chamomile"; a LATER episode B asserts "my favorite tea is
/// rooibos now". Both mine the SAME honest subject key
/// (`{scope}:preference:favorite tea`), so B's fact unit SUPERSEDES A's through
/// the existing subject-key machinery — the update chain fires without any new
/// admission code.
#[tokio::test]
async fn favorite_update_across_episodes_supersedes_prior_fact() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let scope = context.scope_id;

    retain_and_reflect(
        &svc,
        &context,
        "[session s1]\n\
user: My favorite tea is chamomile.\n\
assistant: Chamomile is very calming.\n\
user: I drink it every single evening.\n",
    )
    .await;
    retain_and_reflect(
        &svc,
        &context,
        "[session s2]\n\
user: My favorite tea is rooibos now.\n\
assistant: Rooibos is a lovely switch.\n\
user: I changed it just last week.\n",
    )
    .await;

    let fact_key = derive_fact_key(
        scope.as_uuid(),
        Some("preference"),
        Some("favorite tea"),
        "",
    );
    let units = store.memory_units(tenant);
    let fact_units: Vec<_> = units
        .iter()
        .filter(|unit| unit.fact_key.as_deref() == Some(fact_key.as_str()))
        .collect();
    assert_eq!(fact_units.len(), 3, "the update splits both time axes");
    let superseded = fact_units
        .iter()
        .find(|unit| unit.state == UnitState::Superseded)
        .expect("the prior transaction rectangle is superseded");
    assert!(superseded.body.contains("chamomile"));
    assert_eq!(superseded.valid_from, None);
    assert_eq!(superseded.valid_to, None);
    assert_eq!(superseded.transaction_to.as_deref(), Some(CLOCK.0));

    let historical = fact_units
        .iter()
        .find(|unit| unit.state == UnitState::Active && unit.body.contains("chamomile"))
        .expect("the prior value remains valid before the correction");
    assert_eq!(historical.valid_from, None);
    assert_eq!(historical.valid_to.as_deref(), Some(CLOCK.0));
    assert_eq!(historical.transaction_to, None);

    let current = fact_units
        .iter()
        .find(|unit| unit.state == UnitState::Active && unit.body.contains("rooibos"))
        .expect("the corrected value is current");
    assert_eq!(current.valid_from.as_deref(), Some(CLOCK.0));
    assert_eq!(current.valid_to, None);
    assert_eq!(current.transaction_to, None);

    // The supersedence machinery also wrote the Supersedes/Contradicts edges.
    let edges = store.memory_edges(tenant);
    assert!(
        edges.iter().any(
            |edge| edge.kind == memphant_types::MemoryEdgeKind::Supersedes
                && edge.src_id == current.id
                && edge.dst_id == superseded.id
        ),
        "a Supersedes edge points from the winner to the retired generation"
    );
}

/// §2 citation + shape: the extracted fact unit is SHORT (the verbatim
/// sentence, not the whole session), is cited back to the parent episode, and
/// carries NO contextual chunks (§3).
#[tokio::test]
async fn extracted_fact_unit_is_short_cited_and_chunkless() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let scope = context.scope_id;

    let episode = store_episode_id(
        &svc,
        &store,
        &context,
        "[session s1]\n\
user: My name is Sidney Carter.\n\
assistant: Nice to meet you Sidney.\n\
user: I work in downtown Boston.\n",
    )
    .await;

    let name_key = derive_fact_key(scope.as_uuid(), Some("attribute"), Some("name"), "");
    let units = store.memory_units(tenant);
    let fact = units
        .iter()
        .find(|unit| unit.fact_key.as_deref() == Some(name_key.as_str()))
        .expect("the name fact was mined");
    assert_eq!(
        fact.body, "My name is Sidney Carter",
        "body is the verbatim sentence, not the whole session"
    );
    assert_eq!(
        fact.source_episode_id,
        Some(episode),
        "the fact cites its parent episode"
    );
    assert!(
        fact.contextual_chunks.is_empty(),
        "extracted facts never carry contextual chunks"
    );
}

async fn store_episode_id(
    svc: &MemoryService<InMemoryStore>,
    store: &InMemoryStore,
    context: &ResolvedMemoryContext,
    body: &str,
) -> memphant_types::EpisodeId {
    retain_and_reflect(svc, context, body).await;
    store
        .episodes(context.tenant_id)
        .last()
        .expect("episode stored")
        .id
}

/// §3 assistant-turn exclusion: a first-person statement inside an ASSISTANT
/// turn is never mined; only the user turn's fact is extracted.
#[tokio::test]
async fn assistant_turns_are_never_mined() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    retain_and_reflect(
        &svc,
        &context,
        "[session s1]\n\
assistant: I love that idea and my favorite part is the ending.\n\
user: My favorite color is deep blue.\n\
assistant: I really like blue too, it is calming.\n",
    )
    .await;

    let units = store.memory_units(tenant);
    let fact_units: Vec<_> = units
        .iter()
        .filter(|unit| {
            unit.fact_key
                .as_deref()
                .is_some_and(|key| key.contains(":preference:") || key.contains(":attribute:"))
        })
        .collect();
    assert_eq!(
        fact_units.len(),
        1,
        "only the user turn is mined: {:?}",
        fact_units
            .iter()
            .map(|unit| unit.body.clone())
            .collect::<Vec<_>>()
    );
    assert!(fact_units[0].body.contains("deep blue"));
}

/// §5 flag-off byte-identical reflect: with fact extraction OFF, only the raw
/// episode unit is compiled — no extra fact units, exactly as before W6.
#[tokio::test]
async fn flag_off_emits_no_fact_units() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), false);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    retain_and_reflect(
        &svc,
        &context,
        "[session s1]\n\
user: My favorite tea is chamomile.\n\
assistant: Chamomile is very calming.\n\
user: I really love hiking in the hills.\n",
    )
    .await;

    let units = store.memory_units(tenant);
    assert_eq!(
        units.len(),
        1,
        "flag off ⇒ only the raw episode unit, no fact units: {:?}",
        units
            .iter()
            .map(|unit| unit.body.clone())
            .collect::<Vec<_>>()
    );
    assert!(
        units[0].body.contains("[session s1]"),
        "the single unit is the raw episode body"
    );
}

/// §3 caps + within-episode dedup: an episode packed with distinct facts is
/// capped at 8, and a subject asserted twice in the same episode keeps only the
/// LAST value (later turns win).
#[tokio::test]
async fn caps_and_within_episode_dedup() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let scope = context.scope_id;

    // 10 distinct preference facts (cap is 8) plus a favorite-tea asserted twice
    // (green tea, then oolong): the second assertion must win.
    retain_and_reflect(
        &svc,
        &context,
        "[session s1]\n\
user: I really love mountain hiking trips.\n\
user: I really enjoy long distance cycling.\n\
user: I really like ocean kayaking a lot.\n\
user: I really adore quiet forest camping.\n\
user: I really prefer strong black coffee.\n\
user: I really love spicy thai cooking.\n\
user: I really enjoy classic jazz records.\n\
user: I really like vintage film cameras.\n\
user: I really love slow sunday mornings.\n\
user: I really enjoy fresh mountain air.\n\
user: My favorite tea is plain green tea.\n\
user: My favorite tea is smoky oolong tea.\n",
    )
    .await;

    let units = store.memory_units(tenant);
    let fact_units: Vec<_> = units
        .iter()
        .filter(|unit| {
            unit.fact_key
                .as_deref()
                .is_some_and(|key| key.contains(":preference:") || key.contains(":attribute:"))
        })
        .collect();
    assert!(
        fact_units.len() <= 8,
        "the per-episode cap holds: {} facts",
        fact_units.len()
    );

    // The favorite-tea subject deduped to its LAST value within the episode.
    let tea_key = derive_fact_key(
        scope.as_uuid(),
        Some("preference"),
        Some("favorite tea"),
        "",
    );
    let tea_units: Vec<_> = units
        .iter()
        .filter(|unit| unit.fact_key.as_deref() == Some(tea_key.as_str()))
        .collect();
    assert_eq!(
        tea_units.len(),
        1,
        "favorite tea deduped within the episode"
    );
    assert!(
        tea_units[0].body.contains("oolong"),
        "the later assertion (oolong) wins: {}",
        tea_units[0].body
    );
}

/// §2 subject-key stability: the SAME subject asserted with DIFFERENT values in
/// DIFFERENT episodes derives the SAME subject key — the precondition for the
/// supersedence chain to fire.
#[tokio::test]
async fn same_subject_different_episodes_share_fact_key() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let scope = context.scope_id;

    retain_and_reflect(
        &svc,
        &context,
        "[session s1]\nuser: My name is Alexander Hamilton today.\n",
    )
    .await;
    retain_and_reflect(
        &svc,
        &context,
        "[session s2]\nuser: My name is Alexander the Great now.\n",
    )
    .await;

    let name_key = derive_fact_key(scope.as_uuid(), Some("attribute"), Some("name"), "");
    let units = store.memory_units(tenant);
    let name_units: Vec<_> = units
        .iter()
        .filter(|unit| unit.fact_key.as_deref() == Some(name_key.as_str()))
        .collect();
    assert_eq!(name_units.len(), 3, "the update splits both time axes");
    let superseded = name_units
        .iter()
        .find(|unit| unit.state == UnitState::Superseded)
        .expect("the prior transaction rectangle is superseded");
    assert!(superseded.body.contains("Alexander Hamilton"));
    assert_eq!(superseded.valid_from, None);
    assert_eq!(superseded.valid_to, None);
    assert_eq!(superseded.transaction_to.as_deref(), Some(CLOCK.0));

    let historical = name_units
        .iter()
        .find(|unit| unit.state == UnitState::Active && unit.body.contains("Alexander Hamilton"))
        .expect("the prior name remains valid before the correction");
    assert_eq!(historical.valid_from, None);
    assert_eq!(historical.valid_to.as_deref(), Some(CLOCK.0));
    assert_eq!(historical.transaction_to, None);

    let current = name_units
        .iter()
        .find(|unit| unit.state == UnitState::Active && unit.body.contains("Alexander the Great"))
        .expect("the corrected name is current");
    assert_eq!(current.valid_from.as_deref(), Some(CLOCK.0));
    assert_eq!(current.valid_to, None);
    assert_eq!(current.transaction_to, None);
}

/// §2 date coupling: with BOTH fact extraction and temporal grounding on, the
/// fact body carries the `[date ...]` prefix from the episode's parsed content
/// date; with temporal grounding OFF (fact extraction still on) the body is the
/// bare sentence — the two flags are not coupled.
#[tokio::test]
async fn date_prefix_only_when_temporal_grounding_also_on() {
    let dated_body = "[session s1] [date 2023/05/30]\nuser: My favorite tea is chamomile today.\n";
    let tea_key_scope = |scope: ScopeId| {
        derive_fact_key(
            scope.as_uuid(),
            Some("preference"),
            Some("favorite tea"),
            "",
        )
    };

    // Temporal ON.
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let svc = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_fact_extraction_enabled(true)
    .with_temporal_grounding_enabled(true);
    retain_and_reflect(&svc, &context, dated_body).await;
    let key = tea_key_scope(context.scope_id);
    let units = store.memory_units(tenant);
    let fact = units
        .iter()
        .find(|unit| unit.fact_key.as_deref() == Some(key.as_str()))
        .expect("fact mined");
    assert!(
        fact.body.contains("[date 2023-05-30]"),
        "temporal on ⇒ fact body is date-prefixed: {}",
        fact.body
    );
    assert!(fact.body.contains("chamomile"));

    // Temporal OFF, fact extraction still ON.
    let store2 = InMemoryStore::default();
    let tenant2 = TenantId::new();
    let context2 = memphant_store_testkit::bind_context(&store2, tenant2).await;
    let svc2 = MemoryService::new(
        Arc::new(store2.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_fact_extraction_enabled(true);
    retain_and_reflect(&svc2, &context2, dated_body).await;
    let key2 = tea_key_scope(context2.scope_id);
    let units2 = store2.memory_units(tenant2);
    let fact2 = units2
        .iter()
        .find(|unit| unit.fact_key.as_deref() == Some(key2.as_str()))
        .expect("fact mined");
    assert!(
        !fact2.body.contains("[date "),
        "temporal off ⇒ bare sentence, no date prefix: {}",
        fact2.body
    );
}
