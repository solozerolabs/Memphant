//! D1: caller-authored subject keys reachable from the served path.
//!
//! `derive_fact_key` has always been able to make a supersedable key out of a
//! subject and a predicate, and `has_explicit_subject` has always been the gate
//! that decides whether a write may close a prior generation. What was missing
//! was a *caller* — the public `retain` payloads had no field for a subject, so
//! the only reachable key was the `{scope}:auto:{sha256}` fallback, which never
//! supersedes. These tests pin the two new seams and, just as importantly, pin
//! that supplying nothing still lands on exactly the old auto key.
//!
//! No LLM anywhere on this path: the caller either knows its subject or it does
//! not.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, MemoryStore, NoopEmbedding, derive_fact_key};
use memphant_types::{
    ActorId, MemoryKind, ResolvedMemoryContext, RetainEpisodeHttpRequest, RetainPayload,
    RetainUnitPayload, ScopeId, StoredMemoryUnit, TenantId, TrustLevel,
};

const CLOCK: FixedClock = FixedClock("2026-08-01T00:00:00Z");

fn setup() -> (
    InMemoryStore,
    MemoryService<InMemoryStore>,
    ResolvedMemoryContext,
) {
    let store = InMemoryStore::default();
    let context =
        memphant_store_testkit::resolved_context(TenantId::new(), ScopeId::new(), ActorId::new());
    store.seed_context_binding(&context);
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    );
    (store, service, context)
}

fn request(
    context: &ResolvedMemoryContext,
    source_ref: &str,
    payload: RetainPayload,
) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: source_ref.to_string(),
        observed_at: CLOCK.0.to_string(),
        payload,
    }
}

fn unit_payload(fact_key: Option<&str>, subject: Option<&str>, body: &str) -> RetainPayload {
    RetainPayload::Unit(RetainUnitPayload {
        kind: MemoryKind::Semantic,
        fact_key: fact_key.map(str::to_string),
        subject: subject.map(str::to_string),
        predicate: "is".to_string(),
        body: body.to_string(),
        confidence: 1.0,
        valid_from: None,
        valid_to: None,
        target_unit_ids: None,
    })
}

async fn units(store: &InMemoryStore, context: &ResolvedMemoryContext) -> Vec<StoredMemoryUnit> {
    store
        .fetch_scope_open_units(context)
        .await
        .expect("fetch open units")
}

/// The regression this whole task exists to prevent: a caller that supplies
/// neither `subject` nor `fact_key` must land on the byte-identical auto key it
/// landed on before D1. `derive_fact_key`'s fallback is not being replaced.
#[tokio::test]
async fn a_caller_supplying_no_subject_still_gets_the_unchanged_auto_key() {
    let (store, service, context) = setup();
    let body = "An unkeyed observation.";
    service
        .retain(
            &context,
            "test:unkeyed",
            TrustLevel::TrustedUser,
            request(
                &context,
                "test:unkeyed",
                RetainPayload::Episode(memphant_types::RetainEpisodePayload {
                    source_kind: "user".to_string(),
                    body: body.to_string(),
                    subject: None,
                    predicate: None,
                }),
            ),
        )
        .await
        .expect("retain");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let unit = units(&store, &context)
        .await
        .into_iter()
        .find(|unit| unit.body == body)
        .expect("the episode compiled to a unit");
    assert_eq!(
        unit.fact_key,
        Some(derive_fact_key(
            context.scope_id.as_uuid(),
            None,
            None,
            body
        )),
        "an absent subject must still produce the content-hash auto key"
    );
    assert!(
        unit.fact_key.as_deref().unwrap().contains(":auto:"),
        "and it must still be an auto key"
    );
}

/// A pre-composed `fact_key` keeps winning. A caller that did the client-side
/// key ceremony before D1 must see no change at all, even if it now also sends
/// a subject.
#[tokio::test]
async fn an_explicit_fact_key_beats_a_subject_rather_than_being_recomposed() {
    let (store, service, context) = setup();
    let explicit = format!("{}:composed_by_caller:is", context.scope_id.as_uuid());
    service
        .retain(
            &context,
            "test:explicit",
            TrustLevel::TrustedUser,
            request(
                &context,
                "test:explicit",
                unit_payload(Some(&explicit), Some("something else entirely"), "Body."),
            ),
        )
        .await
        .expect("retain");

    let unit = units(&store, &context)
        .await
        .into_iter()
        .find(|unit| unit.body == "Body.")
        .expect("the unit was written");
    assert_eq!(unit.fact_key.as_deref(), Some(explicit.as_str()));
}

/// A blank subject is not a subject. Whitespace must not mint a degenerate key
/// like `{scope}::is`, which would collide every blank-subject write in a scope
/// onto one generation.
#[tokio::test]
async fn blank_subjects_and_fact_keys_are_rejected_not_silently_keyed() {
    let (_store, service, context) = setup();
    for (fact_key, subject) in [(None, Some("   ")), (Some("  "), None), (None, None)] {
        let error = service
            .retain(
                &context,
                "test:blank",
                TrustLevel::TrustedUser,
                request(
                    &context,
                    "test:blank",
                    unit_payload(fact_key, subject, "B."),
                ),
            )
            .await
            .expect_err("a blank subject key must be refused");
        assert!(
            error
                .to_string()
                .contains("requires a predicate and either a subject or a fact_key"),
            "unexpected error: {error}"
        );
    }
}

/// The episode payload's subject/predicate reach reflect stage 1 and key the
/// compiled unit.
///
/// **This keys the episodic unit; it does not make it supersede.** The write
/// router maps the Episodic arm to `supersedes_own_kind == None` on purpose —
/// an episode is an event, not an assertion, and letting one close a
/// generation is the exact cross-kind bug `ffa640b8` fixed. The supersedable
/// caller-key path is the *unit* payload. What this buys is a legible,
/// groupable key on the episodic unit — and therefore a meaningful `fact_key`
/// in the correction handle instead of a sha256 prefix.
#[tokio::test]
async fn an_episode_payload_subject_keys_the_compiled_unit_without_superseding() {
    let (store, service, context) = setup();
    for (source_ref, body) in [
        ("test:ep-1", "Deploy to staging first."),
        ("test:ep-2", "Deploy straight to production."),
    ] {
        service
            .retain(
                &context,
                source_ref,
                TrustLevel::TrustedUser,
                request(
                    &context,
                    source_ref,
                    RetainPayload::Episode(memphant_types::RetainEpisodePayload {
                        source_kind: "user".to_string(),
                        body: body.to_string(),
                        subject: Some("deploy target".to_string()),
                        predicate: Some("is".to_string()),
                    }),
                ),
            )
            .await
            .expect("retain");
    }
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let expected = derive_fact_key(
        context.scope_id.as_uuid(),
        Some("deploy target"),
        Some("is"),
        "",
    );
    let keyed: Vec<_> = units(&store, &context)
        .await
        .into_iter()
        .filter(|unit| unit.body.starts_with("Deploy"))
        .collect();
    assert_eq!(keyed.len(), 2, "episodes append; they do not supersede");
    for unit in &keyed {
        assert_eq!(unit.kind, MemoryKind::Episodic);
        assert_eq!(
            unit.fact_key.as_deref(),
            Some(expected.as_str()),
            "the caller's subject, not an auto key"
        );
        // The correction handle a recall of this unit would carry names the
        // caller's own key.
        assert_eq!(
            memphant_types::CorrectionHandle::for_unit(unit).fact_key,
            Some(expected.clone())
        );
    }
}
