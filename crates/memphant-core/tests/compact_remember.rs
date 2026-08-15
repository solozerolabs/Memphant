//! Task 2 behavioral contract for `MemoryService::remember`: one self-contained
//! compact `Active` unit per call, across all six kinds, with the typed
//! `payload.compact` marker, scope containment, idempotent replay, belief kept
//! out of default recall, and the compact-ceiling / preference-source
//! validation gates.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, MemoryStore, NoopEmbedding};
use memphant_types::{
    CorrectResult, InvalidateMemoryRequest, InvalidationReason, MemoryKind, MemorySourceInput,
    RecallHttpRequest, RecallMode, RememberRequest, RetainEpisodeHttpResponse, TrustLevel,
    UnitState,
};

const CLOCK: FixedClock = FixedClock("2026-07-03T00:00:00Z");

fn source() -> MemorySourceInput {
    MemorySourceInput {
        kind: "user".to_string(),
        r#ref: "chat:compact-test".to_string(),
        observed_at: "2026-07-02T00:00:00Z".to_string(),
        episode_id: None,
        resource_id: None,
    }
}

fn remember_request(kind: MemoryKind, trigger: &str) -> RememberRequest {
    RememberRequest {
        kind,
        body: format!("Compact body for {trigger}."),
        trigger: trigger.to_string(),
        verification: "the check passes".to_string(),
        target_scope_id: None,
        valid_from: None,
        valid_to: None,
        source: source(),
    }
}

fn unit_ids(response: &memphant_core::MutationResponse) -> Vec<memphant_types::UnitId> {
    let result: RetainEpisodeHttpResponse = serde_json::from_slice(response.body()).unwrap();
    result.unit_ids
}

#[tokio::test]
async fn remember_mints_one_active_compact_unit_for_every_kind() {
    let store = InMemoryStore::default();
    let tenant_id = memphant_types::TenantId::from_u128(90_000);
    let context = memphant_store_testkit::bind_context(&store, tenant_id).await;
    let store = Arc::new(store);
    let service = MemoryService::new(store.clone(), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    for (i, kind) in [
        MemoryKind::Episodic,
        MemoryKind::Semantic,
        MemoryKind::Procedural,
        MemoryKind::Belief,
        MemoryKind::Resource,
        MemoryKind::Preference,
    ]
    .into_iter()
    .enumerate()
    {
        let trigger = format!("trigger-{i}");
        let response = service
            .remember(
                &context,
                &format!("idem-{i}"),
                TrustLevel::TrustedUser,
                remember_request(kind, &trigger),
            )
            .await
            .unwrap_or_else(|e| panic!("remember {kind:?} failed: {e}"));
        let ids = unit_ids(&response);
        assert_eq!(ids.len(), 1, "{kind:?} must mint exactly one unit");

        let unit = store
            .fetch_units_by_ids(&context, &ids)
            .await
            .unwrap()
            .pop()
            .expect("unit persisted");
        assert_eq!(unit.state, UnitState::Active, "{kind:?} must be Active");
        assert_eq!(unit.kind, kind, "kind preserved");
        // Scope containment: the omitted target resolves to the bound scope.
        assert_eq!(
            unit.scope_id, context.scope_id,
            "{kind:?} stays in bound scope"
        );
        // The typed compact marker is present and well formed.
        let compact = unit.compact.as_ref().expect("compact envelope present");
        assert_eq!(compact.schema_version, 1);
        assert_eq!(compact.write_channel, "agent_memory");
        assert_eq!(compact.verification, "the check passes");
        assert_eq!(compact.body_sha256.len(), 64, "body_sha256 is a hex digest");
        // Provenance trust is the caller's, not the routing trust.
        assert_eq!(unit.trust_level, TrustLevel::TrustedUser);
    }
}

#[tokio::test]
async fn remember_is_idempotent_on_replay() {
    let store = Arc::new(InMemoryStore::default());
    let tenant_id = memphant_types::TenantId::from_u128(90_100);
    let context = memphant_store_testkit::bind_context(store.as_ref(), tenant_id).await;
    let service = MemoryService::new(store.clone(), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    let first = service
        .remember(
            &context,
            "same-key",
            TrustLevel::TrustedUser,
            remember_request(MemoryKind::Procedural, "run fmt"),
        )
        .await
        .unwrap();
    let replay = service
        .remember(
            &context,
            "same-key",
            TrustLevel::TrustedUser,
            remember_request(MemoryKind::Procedural, "run fmt"),
        )
        .await
        .unwrap();
    assert_eq!(
        first.body(),
        replay.body(),
        "exact replay returns the original receipt"
    );
}

#[tokio::test]
async fn belief_persists_active_but_is_absent_from_default_recall() {
    let store = Arc::new(InMemoryStore::default());
    let tenant_id = memphant_types::TenantId::from_u128(90_200);
    let context = memphant_store_testkit::bind_context(store.as_ref(), tenant_id).await;
    let service = MemoryService::new(store.clone(), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    let response = service
        .remember(
            &context,
            "belief-1",
            TrustLevel::TrustedUser,
            remember_request(MemoryKind::Belief, "belief trigger"),
        )
        .await
        .unwrap();
    let ids = unit_ids(&response);
    let unit = store
        .fetch_units_by_ids(&context, &ids)
        .await
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(unit.state, UnitState::Active);
    assert_eq!(unit.kind, MemoryKind::Belief);

    // Default recall excludes beliefs.
    let recalled = service
        .recall(
            context.clone(),
            RecallHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                query: "belief trigger".to_string(),
                limit: Some(8),
                budget_tokens: Some(256),
                mode: Some(RecallMode::Fast),
                include_beliefs: Some(false),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .unwrap();
    assert!(
        !recalled
            .items
            .iter()
            .any(|item| ids.contains(&item.unit_id)),
        "belief must not appear in default recall"
    );
}

#[tokio::test]
async fn remember_rejects_blank_oversize_and_inferred_preference() {
    let store = Arc::new(InMemoryStore::default());
    let tenant_id = memphant_types::TenantId::from_u128(90_300);
    let context = memphant_store_testkit::bind_context(store.as_ref(), tenant_id).await;
    let service = MemoryService::new(store.clone(), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    // Blank body.
    let mut blank = remember_request(MemoryKind::Semantic, "t");
    blank.body = "   ".to_string();
    assert!(
        service
            .remember(&context, "b", TrustLevel::TrustedUser, blank)
            .await
            .is_err()
    );

    // Over the 512-token compact ceiling.
    let mut big = remember_request(MemoryKind::Semantic, "t2");
    big.body = "word ".repeat(600);
    assert!(
        service
            .remember(&context, "o", TrustLevel::TrustedUser, big)
            .await
            .is_err()
    );

    // Preference with an inferred (non-user/correction) source is refused.
    let mut inferred = remember_request(MemoryKind::Preference, "t3");
    inferred.source.kind = "agent".to_string();
    assert!(
        service
            .remember(&context, "p", TrustLevel::TrustedUser, inferred)
            .await
            .is_err()
    );

    // Preference with a user declaration is accepted.
    let ok = remember_request(MemoryKind::Preference, "t4");
    assert!(
        service
            .remember(&context, "pu", TrustLevel::TrustedUser, ok)
            .await
            .is_ok()
    );

    // A cross-scope target without a grant is refused, not silently redirected.
    let mut cross = remember_request(MemoryKind::Semantic, "t5");
    cross.target_scope_id = Some(memphant_types::ScopeId::from_u128(1));
    assert!(
        service
            .remember(&context, "x", TrustLevel::TrustedUser, cross)
            .await
            .is_err()
    );
}

#[tokio::test]
async fn invalidate_supersedes_predecessor_and_blocks_recall() {
    let store = Arc::new(InMemoryStore::default());
    let tenant_id = memphant_types::TenantId::from_u128(90_400);
    let context = memphant_store_testkit::bind_context(store.as_ref(), tenant_id).await;
    let service = MemoryService::new(store.clone(), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    // Remember a semantic compact unit (Active semantic is served by recall).
    let created = service
        .remember(
            &context,
            "inv-remember",
            TrustLevel::TrustedUser,
            remember_request(MemoryKind::Semantic, "the deploy command"),
        )
        .await
        .unwrap();
    let old_id = unit_ids(&created)[0];

    // It is recallable before invalidation.
    let before = recall_trigger(&service, &context, "the deploy command").await;
    assert!(before.contains(&old_id), "unit served before invalidation");

    // Invalidate it as stale.
    let response = service
        .invalidate_memory(
            &context,
            "inv-1",
            InvalidateMemoryRequest {
                memory_unit_id: old_id,
                reason_kind: InvalidationReason::Stale,
                reason: "superseded by newer runbook".to_string(),
                source: source(),
            },
        )
        .await
        .unwrap();
    let outcome: CorrectResult = serde_json::from_slice(response.body()).unwrap();
    assert_eq!(outcome.superseded, vec![old_id]);
    let tombstone_id = outcome.created[0];

    // Predecessor is closed (Superseded); tombstone is a bodyless open
    // Invalidated row carrying the reason marker.
    let predecessor = store
        .fetch_units_by_ids(&context, &[old_id])
        .await
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(predecessor.state, UnitState::Superseded);
    assert!(
        predecessor.transaction_to.is_some(),
        "predecessor is closed"
    );

    let tombstone = store
        .fetch_units_by_ids(&context, &[tombstone_id])
        .await
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(tombstone.state, UnitState::Invalidated);
    assert!(
        tombstone.transaction_to.is_none(),
        "tombstone is current/open"
    );
    assert!(tombstone.body.is_empty(), "tombstone is bodyless");
    assert!(
        tombstone.compact.is_none(),
        "tombstone carries no compact body"
    );
    let marker = tombstone
        .invalidation
        .as_ref()
        .expect("invalidation marker");
    assert_eq!(marker.kind, InvalidationReason::Stale);
    assert_eq!(marker.reason, "superseded by newer runbook");

    // Neither predecessor nor tombstone is served by normal recall.
    let after = recall_trigger(&service, &context, "the deploy command").await;
    assert!(
        !after.contains(&old_id) && !after.contains(&tombstone_id),
        "invalidated identity is absent from normal recall"
    );

    // Idempotent replay returns the original receipt.
    let replay = service
        .invalidate_memory(
            &context,
            "inv-1",
            InvalidateMemoryRequest {
                memory_unit_id: old_id,
                reason_kind: InvalidationReason::Stale,
                reason: "superseded by newer runbook".to_string(),
                source: source(),
            },
        )
        .await
        .unwrap();
    assert_eq!(response.body(), replay.body());
}

async fn recall_trigger(
    service: &MemoryService<InMemoryStore>,
    context: &memphant_types::ResolvedMemoryContext,
    query: &str,
) -> Vec<memphant_types::UnitId> {
    service
        .recall(
            context.clone(),
            RecallHttpRequest {
                subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                actor_id: context.actor_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                query: query.to_string(),
                limit: Some(8),
                budget_tokens: Some(256),
                mode: Some(RecallMode::Fast),
                include_beliefs: Some(true),
                transaction_as_of: None,
                valid_at: None,
                aggregation_window: None,
            },
        )
        .await
        .unwrap()
        .items
        .iter()
        .map(|item| item.unit_id)
        .collect()
}

#[tokio::test]
async fn an_open_tombstone_blocks_re_remembering_the_same_identity() {
    let store = Arc::new(InMemoryStore::default());
    let tenant_id = memphant_types::TenantId::from_u128(90_500);
    let context = memphant_store_testkit::bind_context(store.as_ref(), tenant_id).await;
    let service = MemoryService::new(store.clone(), Arc::new(CLOCK), Arc::new(NoopEmbedding));

    let created = service
        .remember(
            &context,
            "nr-1",
            TrustLevel::TrustedUser,
            remember_request(MemoryKind::Procedural, "restart the worker"),
        )
        .await
        .unwrap();
    let old_id = unit_ids(&created)[0];

    service
        .invalidate_memory(
            &context,
            "nr-inv",
            InvalidateMemoryRequest {
                memory_unit_id: old_id,
                reason_kind: InvalidationReason::Harmful,
                reason: "this advice caused an outage".to_string(),
                source: source(),
            },
        )
        .await
        .unwrap();

    // A bare re-remember of the same trigger+kind is refused; the agent must
    // correct the tombstone instead.
    let resurrect = service
        .remember(
            &context,
            "nr-2",
            TrustLevel::TrustedUser,
            remember_request(MemoryKind::Procedural, "restart the worker"),
        )
        .await;
    assert!(resurrect.is_err(), "open tombstone must block re-creation");

    // A DIFFERENT trigger is unaffected.
    let other = service
        .remember(
            &context,
            "nr-3",
            TrustLevel::TrustedUser,
            remember_request(MemoryKind::Procedural, "a different runbook step"),
        )
        .await;
    assert!(other.is_ok(), "a distinct identity is not blocked");
}
