//! W5 temporal grounding, end-to-end through the service (retain → reflect →
//! recall): reflect-stage content-date grounding of `valid_from` and dated
//! chunk headers, soft query-date windowing at recall, and `[date ...]`-prefixed
//! packed items — all behind `with_temporal_grounding_enabled`, default off.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, NoopEmbedding};
use memphant_types::{
    RecallHttpRequest, ResolvedMemoryContext, RetainEpisodeHttpRequest, TenantId, TrustLevel,
};

// A clock pinned well after every content date under test, so the grounded
// `valid_from` cannot be confused with the compile clock.
const CLOCK: FixedClock = FixedClock("2026-07-10T00:00:00Z");

fn service(store: InMemoryStore, temporal: bool) -> MemoryService<InMemoryStore> {
    MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding))
        .with_temporal_grounding_enabled(temporal)
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

fn recall_request(context: &ResolvedMemoryContext, query: &str) -> RecallHttpRequest {
    RecallHttpRequest {
        compact_only: false,
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
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

// A five-turn bench-style session body carrying the `[date YYYY/MM/DD]` prefix,
// long enough (5 turns) to mint contextual chunks.
fn dated_session_body(session: &str, date: &str) -> String {
    format!(
        "[session {session}] [date {date}]\n\
user: alpha bravo charlie.\n\
assistant: delta echo foxtrot.\n\
user: golf hotel india.\n\
assistant: juliet kilo lima.\n\
user: mike november oscar.\n"
    )
}

/// §1: with the flag on, the reflect stage grounds the minted unit's
/// `valid_from` to the body's parsed content date and stamps that date into the
/// contextual-chunk headers — the TRUE date, never the `2026-07-10` clock.
#[tokio::test]
async fn reflect_grounds_valid_from_and_chunk_headers() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    svc.retain(
        &context,
        concat!("test:", line!()),
        TrustLevel::TrustedUser,
        retain_request(&context, &dated_session_body("s1", "2023/05/30")),
    )
    .await
    .expect("retain");
    svc.run_worker_tick(usize::MAX).await.expect("reflect");

    let units = store.memory_units(tenant);
    assert_eq!(units.len(), 1, "one unit compiled");
    let unit = &units[0];
    assert_eq!(
        unit.valid_from.as_deref(),
        Some("2023-05-30T00:00:00Z"),
        "valid_from grounded to the parsed content date at midnight UTC"
    );
    assert!(
        !unit.contextual_chunks.is_empty(),
        "5 turns → chunks minted"
    );
    assert!(
        unit.contextual_chunks
            .iter()
            .all(|chunk| chunk.header.contains("[date 2023-05-30]")),
        "every chunk header carries the parsed date: {:?}",
        unit.contextual_chunks
            .iter()
            .map(|chunk| chunk.header.clone())
            .collect::<Vec<_>>()
    );
}

/// §1 flag-off: the default service grounds nothing — `valid_from` stays `None`
/// and chunk headers stay dateless, exactly as before W5.
#[tokio::test]
async fn reflect_flag_off_leaves_valid_from_none_and_dateless_headers() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), false);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    svc.retain(
        &context,
        concat!("test:", line!()),
        TrustLevel::TrustedUser,
        retain_request(&context, &dated_session_body("s1", "2023/05/30")),
    )
    .await
    .expect("retain");
    svc.run_worker_tick(usize::MAX).await.expect("reflect");

    let units = store.memory_units(tenant);
    assert_eq!(units.len(), 1);
    assert!(
        units[0].valid_from.is_none(),
        "flag off ⇒ valid_from ungrounded"
    );
    assert!(
        units[0]
            .contextual_chunks
            .iter()
            .all(|chunk| !chunk.header.contains("[date ")),
        "flag off ⇒ dateless chunk headers"
    );
}

/// With the flag on and no parseable body date, valid time falls back to the
/// episode's first observation.
#[tokio::test]
async fn reflect_grounds_first_observation_when_body_has_no_date() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    svc.retain(
        &context,
        concat!("test:", line!()),
        TrustLevel::TrustedUser,
        retain_request(
            &context,
            "user: a plain undated note about the quarterly plan and its scope.",
        ),
    )
    .await
    .expect("retain");
    svc.run_worker_tick(usize::MAX).await.expect("reflect");

    let units = store.memory_units(tenant);
    assert_eq!(units.len(), 1);
    assert_eq!(
        units[0].valid_from.as_deref(),
        Some("2026-07-09T00:00:00Z"),
        "no parseable body date ⇒ valid_from uses first observation"
    );
}

// Two structurally-symmetric single-turn episodes differing only in session id
// and date. B ("s1") sorts lexicographically BEFORE A ("s2"), so on a pure body
// tiebreak (no temporal boost) B ranks first — the windowing boost must overcome
// that ordering for A to lead.
fn windowing_corpus() -> (String, String) {
    let in_window =
        "[session s2] [date 2023-05-15]\nuser: project deadline alpha bravo".to_string();
    let out_window =
        "[session s1] [date 2023-08-20]\nuser: project deadline alpha bravo".to_string();
    (in_window, out_window)
}

/// §2 + §3: a dated query softly prefers the in-window unit (it overcomes the
/// body-order tiebreak that would otherwise lead with the out-window unit), the
/// out-window unit is still retrievable (soft preference, not a hard filter),
/// and packed item bodies carry the `[date ...]` prefix from their `valid_from`.
#[tokio::test]
async fn windowing_prefers_in_window_and_keeps_out_window() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let (in_window, out_window) = windowing_corpus();

    svc.retain(
        &context,
        concat!("test:", line!()),
        TrustLevel::TrustedUser,
        retain_request(&context, &in_window),
    )
    .await
    .expect("retain in-window");
    svc.retain(
        &context,
        concat!("test:", line!()),
        TrustLevel::TrustedUser,
        retain_request(&context, &out_window),
    )
    .await
    .expect("retain out-window");
    svc.run_worker_tick(usize::MAX).await.expect("reflect");

    let response = svc
        .recall(
            context.clone(),
            recall_request(&context, "project deadline in may 2023"),
        )
        .await
        .expect("recall");

    assert!(response.items.len() >= 2, "both units retrievable");
    assert!(
        response.items[0].body.starts_with("[date 2023-05-15]"),
        "in-window unit leads AND carries its dated-pack prefix: {}",
        response.items[0].body
    );
    assert!(
        response
            .items
            .iter()
            .any(|item| item.body.contains("2023-08-20")),
        "out-window unit is still present (soft preference, not a filter)"
    );
}

/// §7 flag-off recall byte-identity: with the flag off the same corpus/query
/// ranks by today's body tiebreak (out-window "s1" leads) and no item carries a
/// `[date ...]` prefix — the recall path is unchanged from before W5.
#[tokio::test]
async fn recall_flag_off_keeps_today_ordering_and_no_prefix() {
    let store = InMemoryStore::default();
    let svc = service(store.clone(), false);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;
    let (in_window, out_window) = windowing_corpus();

    svc.retain(
        &context,
        concat!("test:", line!()),
        TrustLevel::TrustedUser,
        retain_request(&context, &in_window),
    )
    .await
    .expect("retain in-window");
    svc.retain(
        &context,
        concat!("test:", line!()),
        TrustLevel::TrustedUser,
        retain_request(&context, &out_window),
    )
    .await
    .expect("retain out-window");
    svc.run_worker_tick(usize::MAX).await.expect("reflect");

    let response = svc
        .recall(
            context.clone(),
            recall_request(&context, "project deadline in may 2023"),
        )
        .await
        .expect("recall");

    assert!(response.items.len() >= 2);
    assert!(
        response
            .items
            .iter()
            .all(|item| !item.body.starts_with("[date ")),
        "flag off ⇒ no dated-pack prefix on any item"
    );
    assert!(
        response.items[0].body.contains("[session s1]"),
        "flag off ⇒ today's body-order tiebreak leads with the smaller body (s1): {}",
        response.items[0].body
    );
}
