//! Rung 4 runtime contextual-chunk write path: the reflect-stage compile
//! (`MemoryService::compile_job`, shared by the public reflect verb and the
//! worker tick) mints contextual chunks tied to their parent episode when the
//! `contextual_chunks_write_enabled` service option is on — and stays chunk-free
//! (today's behavior) when it is off. Recall still cites chunk-matched items
//! back to the PARENT episode (chunk id ↔ parent linkage).

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, MemoryStore, StubEmbedding};
use memphant_types::{
    ActorId, RecallHttpRequest, RetainEpisodeHttpRequest, ScopeId, TenantId, TrustLevel,
};

const CLOCK: FixedClock = FixedClock("2026-07-09T00:00:00Z");

/// Six turns behind a `[session]` provenance line: turn windows of 4 yield
/// two chunks (turns 1-4, 5-6).
const EPISODE_BODY: &str = "[session s1] [date 2023/05/30]\n\
user: I moved to Berlin in March.\n\
assistant: Got it, you moved to Berlin in March.\n\
user: My favorite tea is oolong.\n\
assistant: Noted, oolong tea it is.\n\
user: I drive a blue Tesla.\n\
assistant: A blue Tesla, understood.\n";

fn service(store: InMemoryStore, chunks_write: bool) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    )
    .with_contextual_chunks_write_enabled(chunks_write)
}

fn retain_request(context: &memphant_types::ResolvedMemoryContext) -> RetainEpisodeHttpRequest {
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
            body: EPISODE_BODY.to_string(),
        }),
    }
}

fn recall_request(
    tenant_id: TenantId,
    scope_id: ScopeId,
    actor_id: ActorId,
    query: &str,
) -> RecallHttpRequest {
    RecallHttpRequest {
        subject_id: memphant_types::SubjectId::from_u128(tenant_id.as_uuid().as_u128()),
        scope_id,
        agent_node_id: memphant_types::AgentNodeId::from_u128(scope_id.as_uuid().as_u128()),
        subject_generation: 0,
        actor_id,
        query: query.to_string(),
        limit: None,
        budget_tokens: None,
        mode: None,
        include_beliefs: None,
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

#[tokio::test]
async fn reflect_mints_contextual_chunks_when_write_enabled() {
    let store = InMemoryStore::default();
    let service = service(store.clone(), true);
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));

    let retained = service
        .retain(
            &memphant_store_testkit::resolved_context(tenant, scope, actor),
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&memphant_store_testkit::resolved_context(
                tenant, scope, actor,
            )),
        )
        .await
        .expect("retain");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let episode_id = retained.episode_id.expect("episode retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let unit = page
        .items
        .iter()
        .find(|unit| unit.source_episode_id == Some(episode_id))
        .expect("episode-derived unit");

    // Six turns / window 4 → two chunks (turns 1-4, 5-6).
    assert_eq!(unit.contextual_chunks.len(), 2, "one chunk per turn window");
    let episode_uuid = episode_id.as_uuid();
    for chunk in &unit.contextual_chunks {
        assert!(
            chunk.id.starts_with(&format!("chunk-{episode_uuid}-")),
            "chunk id derives from parent episode: {}",
            chunk.id
        );
        assert!(
            chunk.header.contains(&format!("[episode {episode_uuid}]")),
            "header carries parent episode provenance: {}",
            chunk.header
        );
        assert!(
            chunk.header.contains("[kind user]"),
            "header carries source_kind"
        );
        assert!(!chunk.body.trim().is_empty(), "no empty-body chunks");
        assert!(
            chunk
                .source_span
                .as_deref()
                .is_some_and(|span| span.contains('-')),
            "chunk carries a source span"
        );
    }
    assert!(
        unit.contextual_chunks[0].header.contains("[turns 1-4]"),
        "first window covers turns 1-4: {}",
        unit.contextual_chunks[0].header
    );
    assert!(
        unit.contextual_chunks[1].header.contains("[turns 5-6]"),
        "second window covers turns 5-6: {}",
        unit.contextual_chunks[1].header
    );
    assert_ne!(
        unit.contextual_chunks[0].id, unit.contextual_chunks[1].id,
        "window ids are distinct"
    );
}

#[tokio::test]
async fn reflect_mints_contextual_chunks_by_default() {
    let store = InMemoryStore::default();
    // Default construction (no builder call) now mints chunks: the rung 4
    // runtime path was promoted to default-on on 2026-07-10 once the paired
    // ablation THROUGH this path cleared (see the field doc on
    // `contextual_chunks_write_enabled`). This is the product path.
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    );
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));

    let mut request = retain_request(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));
    let memphant_types::RetainPayload::Episode(episode) = &mut request.payload else {
        unreachable!()
    };
    episode.body = SESSION_BODY.to_string();
    let retained = service
        .retain(
            &memphant_store_testkit::resolved_context(tenant, scope, actor),
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&memphant_store_testkit::resolved_context(
                tenant, scope, actor,
            )),
        )
        .await
        .expect("retain");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let episode_id = retained.episode_id.expect("episode retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let unit = page
        .items
        .iter()
        .find(|unit| unit.source_episode_id == Some(episode_id))
        .expect("episode-derived unit");
    // Six turns / window 4 → two chunks (turns 1-4, 5-6): the product path
    // mints them with no builder opt-in.
    assert_eq!(
        unit.contextual_chunks.len(),
        2,
        "default construction mints per-episode chunks (promoted 2026-07-10)"
    );
}

/// Explicit control arm: `with_contextual_chunks_write_enabled(false)` forces
/// the pre-promotion chunk-free behavior — the baseline the bench lane's
/// `--disable runtime_chunks` runs. This is the surviving explicit-off test.
#[tokio::test]
async fn reflect_stays_chunk_free_when_write_disabled() {
    let store = InMemoryStore::default();
    let service = service(store.clone(), false);
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));

    let retained = service
        .retain(
            &memphant_store_testkit::resolved_context(tenant, scope, actor),
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&memphant_store_testkit::resolved_context(
                tenant, scope, actor,
            )),
        )
        .await
        .expect("retain");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let episode_id = retained.episode_id.expect("episode retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let context = memphant_store_testkit::resolved_context(tenant, scope, actor);
    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let unit = page
        .items
        .iter()
        .find(|unit| unit.source_episode_id == Some(episode_id))
        .expect("episode-derived unit");
    assert!(
        unit.contextual_chunks.is_empty(),
        "explicit builder-off keeps the chunk-free control arm (old behavior)"
    );
}

/// Twelve turns behind a `[session]` line: turn windows of 4 yield three chunks
/// (1-4 Berlin/balcony, 5-8 quantum harmonica, 9-12 pomegranate). A query for
/// the middle window's content chunk-renders that window plus one neighbour
/// within the item's whole-body budget, dropping the far window.
const SESSION_BODY: &str = "[session s7] [date 2023/06/01]\n\
user: I moved to Berlin in March for a new job.\n\
assistant: Congrats on the move to Berlin and the new job.\n\
user: The apartment there has a lovely balcony garden.\n\
assistant: A balcony garden sounds wonderful in Berlin.\n\
user: My prized possession is a vintage quantum harmonica.\n\
assistant: A vintage quantum harmonica is a rare collector item.\n\
user: I keep the quantum harmonica in a velvet case.\n\
assistant: Storing the quantum harmonica in velvet protects it well.\n\
user: On weekends I bake sourdough with pomegranate molasses.\n\
assistant: Sourdough with pomegranate molasses sounds delicious.\n\
user: The pomegranate molasses comes from a shop downtown.\n\
assistant: A downtown shop for pomegranate molasses is handy.\n";

/// End-to-end: retain → reflect (chunks on) → recall. The packed context text of
/// the chunk-matched item is rendered from its chunks — the matched window's
/// header + body and a neighbour window — NOT the full session body (the far,
/// unmatched window is dropped).
///
/// The dropped far window is a BUDGET-BOUND outcome, not a redaction contract:
/// since f67f2b2a the post-fill whole-body completion pass trades a partial
/// chunk render for the unit's whole body — a superset — whenever the pack's
/// leftover budget covers the difference. So the chunk render is only observable
/// when the budget cannot afford the whole body, and this test pins an explicit
/// `budget_tokens` to sit in that band (the whole body costs 248; 240 admits the
/// item and gathers the neighbour with no room left to complete). The tail of the
/// test recalls again on the default roomy budget and asserts the superseding
/// behavior.
#[tokio::test]
async fn recall_chunk_renders_matched_window_plus_neighbour() {
    let store = InMemoryStore::default();
    let service = service(store.clone(), true);
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));

    let mut request = retain_request(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));
    let memphant_types::RetainPayload::Episode(episode) = &mut request.payload else {
        unreachable!()
    };
    episode.body = SESSION_BODY.to_string();
    let retained = service
        .retain(
            &memphant_store_testkit::resolved_context(tenant, scope, actor),
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            request,
        )
        .await
        .expect("retain");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let episode_id = retained.episode_id.expect("episode retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let mut tight = recall_request(tenant, scope, actor, "quantum harmonica");
    tight.budget_tokens = Some(240);
    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            tight,
        )
        .await
        .expect("recall");
    let item = response
        .items
        .iter()
        .find(|item| item.citation_episode_id == Some(episode_id))
        .expect("episode-derived item recalled");

    // Matched middle window (turns 5-8) is present with its provenance header.
    assert!(
        item.body.contains("[turns 5-8]"),
        "matched window header present: {}",
        item.body
    );
    assert!(
        item.body.contains("quantum harmonica"),
        "matched window body present: {}",
        item.body
    );
    // A neighbour window (turns 1-4) is gathered in.
    assert!(
        item.body.contains("[turns 1-4]") && item.body.contains("Berlin"),
        "neighbour window gathered: {}",
        item.body
    );
    // The far, unmatched window (turns 9-12) is dropped — this is NOT the full
    // session body.
    assert!(
        !item.body.contains("pomegranate") && !item.body.contains("[turns 9-12]"),
        "far unmatched window dropped: {}",
        item.body
    );
    assert_ne!(
        item.body, SESSION_BODY,
        "packed text is chunk-rendered, not the raw session body"
    );

    // Roomy budget: the completion pass supersedes the partial render and the
    // item emits all of itself, far window included. Dropping content the budget
    // can afford was the render loss f67f2b2a fixed, never a contract.
    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "quantum harmonica"),
        )
        .await
        .expect("recall");
    let item = response
        .items
        .iter()
        .find(|item| item.citation_episode_id == Some(episode_id))
        .expect("episode-derived item recalled");
    assert_eq!(
        item.body, SESSION_BODY,
        "leftover budget covers the whole body, so the partial render completes"
    );
}

#[tokio::test]
async fn recall_cites_chunk_matched_item_to_parent_episode() {
    let store = InMemoryStore::default();
    let service = service(store.clone(), true);
    let tenant = TenantId::new();
    let scope = ScopeId::new();
    let actor = ActorId::new();
    store.seed_context_binding(&memphant_store_testkit::resolved_context(
        tenant, scope, actor,
    ));

    let retained = service
        .retain(
            &memphant_store_testkit::resolved_context(tenant, scope, actor),
            concat!("test:", line!()),
            TrustLevel::TrustedUser,
            retain_request(&memphant_store_testkit::resolved_context(
                tenant, scope, actor,
            )),
        )
        .await
        .expect("retain");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let episode_id = retained.episode_id.expect("episode retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let response = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "oolong tea"),
        )
        .await
        .expect("recall");
    let item = response
        .items
        .iter()
        .find(|item| item.inclusion_reason == "contextual_chunk")
        .expect("an item was included via its contextual chunk");
    assert_eq!(
        item.citation_episode_id,
        Some(episode_id),
        "chunk-matched recall cites the PARENT episode, not the chunk"
    );
}
