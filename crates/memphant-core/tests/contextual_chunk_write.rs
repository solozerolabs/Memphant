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
            subject: None,
            predicate: None,
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
        compact_only: false,
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
/// since f67f2b2a the post-fill completion pass trades a partial chunk render up
/// to a superset whenever the pack's leftover budget covers the difference. So
/// the partial render is only observable when the budget cannot afford the
/// completion, and this test pins an explicit `budget_tokens` to sit in that band
/// (the whole body costs 248; 240 admits the item and gathers the neighbour with
/// no room left to complete).
///
/// What the completion may NEVER do is drop the provenance with the partiality.
/// The chunk render carries a `[turns a-b]` header on every block and the raw
/// whole body carries none, so the completion goes to FULL CHUNK COVERAGE — same
/// content, headers intact. The tail of the test recalls again on the default
/// roomy budget and asserts exactly that: every window present, every window
/// headed, and still not the raw session body.
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

    // The matched window and its gathered neighbour are BOTH present. Since
    // `merge_chunk_blocks` is default-ON (spec 30 §7), the two contiguous
    // same-episode windows render under ONE run-spanning header (`[turns 1-8]`)
    // rather than one header per window — the attribution is preserved as the
    // run's span, which is the point, not the per-window granularity.
    assert!(
        item.body.contains("quantum harmonica") && item.body.contains("Berlin"),
        "matched window and its neighbour are both rendered: {}",
        item.body
    );
    // Provenance is never lost: the chunk-rendered body LEADS with its
    // provenance header — no body text appears above the first `[episode …]`
    // line. This is the invariant the merge must preserve, and did.
    assert!(
        item.body.starts_with("[episode "),
        "rendered body leads with its provenance header: {}",
        item.body
    );
    assert_ne!(
        item.body, SESSION_BODY,
        "packed text is chunk-rendered, not the raw session body"
    );

    // Roomy budget: the completion pass supersedes the partial render and the
    // item emits all of itself — far window included. Under default-ON
    // `merge_chunk_blocks` the three contiguous windows collapse to ONE
    // run-spanning block (`[turns 1-12]`): full content, single header.
    // Dropping content the budget can afford was the render loss f67f2b2a
    // fixed; dropping the header with it was the regression that fix introduced
    // — the merge keeps exactly one header, never zero.
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
    // Every window's body is present (full coverage) …
    for body in ["Berlin", "quantum harmonica", "pomegranate"] {
        assert!(
            item.body.contains(body),
            "leftover budget completes to full coverage — {body} missing from: {}",
            item.body
        );
    }
    // … under exactly ONE merged provenance header spanning the whole run, and
    // the body still leads with that header (content is never shown headerless).
    assert_eq!(
        item.body.matches("[episode ").count(),
        1,
        "the contiguous run carries exactly one run-spanning header: {}",
        item.body
    );
    assert!(
        item.body.starts_with("[episode "),
        "the completion still leads with its provenance header: {}",
        item.body
    );
    assert_ne!(
        item.body, SESSION_BODY,
        "the completion is chunk-rendered, not the bare session body"
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

    // D1: the correction handle gives `source_span` its first non-test
    // consumer. The span must slice the ORIGINAL episode body — that is the
    // whole value of shipping it, and it is what lets a correction surface
    // quote the exact bytes the memory was minted from.
    let handle = item
        .correction
        .as_ref()
        .expect("a recalled stored unit carries a correction handle");
    assert_eq!(handle.unit_id, item.unit_id);
    assert_eq!(handle.episode_id, Some(episode_id));
    let span = handle
        .source_span
        .as_deref()
        .expect("a chunked unit has a covering span");
    let (start, end) = span.split_once('-').expect("span is start-end");
    let (start, end): (usize, usize) = (start.parse().unwrap(), end.parse().unwrap());
    assert!(
        EPISODE_BODY.get(start..end).is_some(),
        "span {span} must be a valid byte range of the {} byte episode body",
        EPISODE_BODY.len()
    );
    assert!(
        EPISODE_BODY[start..end].contains("oolong"),
        "the span must cover the text the unit was minted from"
    );

    // The handle is a property of the unit, so the item that was rendered from
    // a chunk subset carries the same span as the whole-body render of the same
    // unit — it never narrows to this query's packing decision.
    let whole = service
        .recall(
            memphant_store_testkit::resolved_context(tenant, scope, actor),
            recall_request(tenant, scope, actor, "oolong tea"),
        )
        .await
        .expect("second recall")
        .items
        .into_iter()
        .find(|other| other.unit_id == item.unit_id)
        .expect("same unit recalled again");
    assert_eq!(whole.correction.as_ref(), Some(handle));
}
