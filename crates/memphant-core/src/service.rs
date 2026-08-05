//! `MemoryService`: the one application layer shared by REST, MCP, CLI and
//! the background worker. All orchestration (retain dispatch, reflect job
//! claiming/compilation, degraded read-your-own-writes recall) lives here —
//! transport handlers stay thin.

use std::collections::HashSet;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use futures::{StreamExt, stream};
#[cfg(test)]
use memphant_types::TenantId;
use memphant_types::{
    COMPILER_VERSION, CanonicalProjectionResponse, CanonicalProjectionUnit, ContextualChunk,
    CorrectRequest, CorrectionPayload, DegradedRecallTraceItem, ENGINE_VERSION, EpisodeId,
    FileSyncOperation, FileSyncOperationResult, FileSyncRequest, FileSyncResult,
    FileSyncUnitMetadata, ForgetRequest, ForgetResult, ForgetTarget, MarkRequest, MarkResult,
    MemoryKind, NewEpisode, NewResource, RecallContextItem, RecallDegradationDiagnostic,
    RecallDegradationReason, RecallHttpRequest, RecallMode, RecallRequest, RecallResponse,
    ReflectAccepted, ReflectCandidate, ReflectInput, ReflectJob, ReflectJobKind, ReflectRequest,
    ResolvedMemoryContext, ResourceId, ResourceKind, RetainEpisodeHttpRequest,
    RetainEpisodeHttpResponse, RetrievalTrace, ReviewEvent, StoredEpisode, StoredMemoryUnit,
    TraceId, TrustLevel, UnitId,
};
use sha2::{Digest, Sha256};

use crate::deep_recall::DeepRecallProvider;
use crate::{
    ClaimMutationOutcome, Clock, CompiledWrite, CoreError, CorrectionUnitIds, CorrectionWrite,
    CrossRerankCandidateSelection, CrossRerankGranularity, CrossReranker,
    DEFAULT_RECALL_POOL_DEPTH, EmbeddingProvider, EmbeddingTaskKind, FileSyncTransitionSnapshot,
    ForgetWrite, JobFilter, LexicalScorer, MemoryStore, MutationClaim, MutationClaimOutcome,
    MutationLedgerStore, MutationResponse, MutationVerb, PackLevers, PreparedCompiledWrite,
    ReflectJobRow, ScopePage, StoreError, StructuredExtractionPacket, StructuredSourceKind,
    StructuredStateProvider, StructuredStateRequest, VectorQuery, apply_correction_transition,
    apply_unit_forget_transition, canonical_mutation_request_hash, correction_rectangles_with_ids,
    derive_episode_dedup_key, embedding_profile_for, evidence_slices_for_episode,
    fold_structured_observations, non_blank, normalize_component, parse_content_date,
    prepare_compiled_write_from_snapshot_owned, prepare_compiled_write_owned,
    project_structured_state, recall_scope_admitted,
    recall_with_pool_and_selection_and_deep_started, reflect_recorded_claimed_owned,
    run_embedding_task, structured_compiler_identity, structured_extraction_receipt_sha256,
    tokenize, validate_structured_observations_for_request, validate_valid_interval,
};

pub const DEFAULT_STRUCTURED_STATE_PREFETCH_CONCURRENCY: usize = 4;
pub const MAX_STRUCTURED_STATE_PREFETCH_CONCURRENCY: usize = 16;
pub const DEFAULT_WORKER_COMPILE_CONCURRENCY: usize = 16;
pub const MAX_WORKER_COMPILE_CONCURRENCY: usize = 128;
/// The maximum encoded JSON payload returned by the canonical projection read.
pub const MAX_CANONICAL_PROJECTION_ENCODED_BYTES: usize = 1_048_576;
/// Maximum-length future timestamp emitted by the canonical Jiff formatter.
const MAX_CANONICAL_PROJECTION_TIMESTAMP: &str = "9999-12-30T22:00:00.999999999Z";

#[cfg(test)]
mod canonical_projection_store_tests {
    use super::*;
    use crate::{FixedClock, InMemoryStore, MemoryStore, NewMemoryUnit, NoopEmbedding, UnitState};
    use memphant_types::{
        ActorId, AgentNodeId, CanonicalProjectionUnit, ContextBindingAgentRef,
        ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef, MemoryKind,
        ResolvedMemoryContext, TenantId, UnitId,
    };

    async fn context(store: &InMemoryStore) -> ResolvedMemoryContext {
        let tenant = TenantId::from_u128(96_100);
        let binding = store
            .resolve_context_binding(
                tenant,
                "canonical-projection-store".to_string(),
                ContextBindingRequest {
                    subject: ContextBindingEntityRef {
                        external_ref: "subject:canonical-projection-store".to_string(),
                        kind: "user".to_string(),
                    },
                    actor: ContextBindingEntityRef {
                        external_ref: "actor:canonical-projection-store".to_string(),
                        kind: "user".to_string(),
                    },
                    scope: ContextBindingScopeRef {
                        external_ref: "scope:canonical-projection-store".to_string(),
                        kind: "memory".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: ContextBindingAgentRef {
                        external_ref: "agent:canonical-projection-store".to_string(),
                        parent_external_ref: None,
                    },
                    access_policies: Vec::new(),
                },
            )
            .await
            .expect("bind context");
        store
            .resolve_memory_context(
                tenant,
                binding.subject_id,
                binding.actor_id,
                binding.scope_id,
                binding.agent_node_id,
            )
            .await
            .expect("resolve context")
    }

    fn unit(
        context: &ResolvedMemoryContext,
        kind: MemoryKind,
        state: UnitState,
        body: &str,
    ) -> NewMemoryUnit {
        NewMemoryUnit {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            kind,
            state,
            fact_key: Some(format!("projection:{body}")),
            predicate: Some("states".to_string()),
            body: body.to_string(),
            confidence: Some(1.0),
            trust_level: TrustLevel::TrustedSystem,
            churn_class: None,
            freshness_due_at: None,
            actor_id: Some(context.actor_id),
            source_kind: Some("test".to_string()),
            source_ref: format!("test:{body}"),
            observed_at: "2026-07-22T00:00:00Z".to_string(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
        }
    }

    #[tokio::test]
    async fn canonical_projection_store_excludes_historical_and_disallowed_units() {
        let store = InMemoryStore::default();
        let context = context(&store).await;
        let mut tx = store.begin(&context).await.expect("begin");
        for (kind, state, body) in [
            (MemoryKind::Semantic, UnitState::Active, "semantic-active"),
            (
                MemoryKind::Semantic,
                UnitState::Validated,
                "semantic-validated",
            ),
            (
                MemoryKind::Procedural,
                UnitState::Validated,
                "procedure-validated",
            ),
            (
                MemoryKind::Procedural,
                UnitState::Active,
                "procedure-active",
            ),
            (MemoryKind::Episodic, UnitState::Active, "episodic-active"),
            (MemoryKind::Belief, UnitState::Active, "belief-active"),
            (MemoryKind::Resource, UnitState::Active, "resource-active"),
            (MemoryKind::Semantic, UnitState::Candidate, "candidate"),
            (MemoryKind::Semantic, UnitState::Superseded, "superseded"),
            (MemoryKind::Semantic, UnitState::Invalidated, "invalidated"),
            (MemoryKind::Semantic, UnitState::Deleted, "deleted"),
            (MemoryKind::Semantic, UnitState::Quarantined, "quarantined"),
            (MemoryKind::Semantic, UnitState::Expired, "expired"),
        ] {
            store
                .stage_memory_unit(&mut tx, unit(&context, kind, state, body))
                .await
                .expect("stage unit");
        }
        let mut closed = unit(
            &context,
            MemoryKind::Semantic,
            UnitState::Active,
            "closed-transaction",
        );
        closed.transaction_to = Some("2026-07-21T00:00:00Z".to_string());
        store
            .stage_memory_unit(&mut tx, closed)
            .await
            .expect("stage closed unit");
        let mut deleted = unit(
            &context,
            MemoryKind::Semantic,
            UnitState::Active,
            "deleted-generation",
        );
        deleted.deletion_generation = Some(1);
        store
            .stage_memory_unit(&mut tx, deleted)
            .await
            .expect("stage deleted unit");
        let mut quarantined_trust = unit(
            &context,
            MemoryKind::Semantic,
            UnitState::Active,
            "quarantined-trust",
        );
        quarantined_trust.trust_level = TrustLevel::Quarantined;
        store
            .stage_memory_unit(&mut tx, quarantined_trust)
            .await
            .expect("stage quarantined-trust unit");
        let mut future_valid = unit(
            &context,
            MemoryKind::Semantic,
            UnitState::Active,
            "future-valid",
        );
        future_valid.valid_from = Some("2026-07-23T00:00:00Z".to_string());
        store
            .stage_memory_unit(&mut tx, future_valid)
            .await
            .expect("stage future-valid unit");
        let mut expired = unit(&context, MemoryKind::Semantic, UnitState::Active, "expired");
        expired.valid_to = Some("2026-07-21T00:00:00Z".to_string());
        store
            .stage_memory_unit(&mut tx, expired)
            .await
            .expect("stage expired unit");
        let mut future_transaction = unit(
            &context,
            MemoryKind::Semantic,
            UnitState::Active,
            "future-transaction",
        );
        future_transaction.transaction_from = Some("2026-07-23T00:00:00Z".to_string());
        store
            .stage_memory_unit(&mut tx, future_transaction)
            .await
            .expect("stage future-transaction unit");
        store.commit(tx).await.expect("commit");

        let template = store
            .memory_units(context.tenant_id)
            .into_iter()
            .next()
            .expect("seed unit");
        let mut wrong_generation = template.clone();
        wrong_generation.id = UnitId::from_u128(96_101);
        wrong_generation.subject_generation += 1;
        let mut wrong_agent = template.clone();
        wrong_agent.id = UnitId::from_u128(96_102);
        wrong_agent.agent_node_id = AgentNodeId::from_u128(96_102);
        let mut wrong_actor = template.clone();
        wrong_actor.id = UnitId::from_u128(96_103);
        wrong_actor.actor_id = Some(ActorId::from_u128(96_103));
        let mut wrong_tenant = template;
        let foreign_tenant = TenantId::from_u128(96_104);
        wrong_tenant.id = UnitId::from_u128(96_104);
        wrong_tenant.tenant_id = foreign_tenant;
        {
            let mut state = store.inner.lock().expect("in-memory state");
            state
                .memory_units
                .entry(context.tenant_id)
                .or_default()
                .extend([wrong_generation, wrong_agent, wrong_actor]);
            state
                .memory_units
                .entry(foreign_tenant)
                .or_default()
                .push(wrong_tenant);
        }

        let projected = store
            .canonical_projection_units(&context, "2026-07-22T00:00:00Z")
            .await
            .expect("one visible projection snapshot");
        assert_eq!(
            projected
                .iter()
                .map(|unit| (unit.kind, unit.state))
                .collect::<Vec<_>>(),
            vec![
                (MemoryKind::Semantic, UnitState::Active),
                (MemoryKind::Semantic, UnitState::Validated),
                (MemoryKind::Procedural, UnitState::Validated),
            ]
        );
    }

    #[tokio::test]
    async fn canonical_projection_service_uses_its_returned_clock_instant_for_visibility() {
        let store = InMemoryStore::default();
        let context = context(&store).await;
        let mut tx = store.begin(&context).await.expect("begin");
        let visible_id = store
            .stage_memory_unit(
                &mut tx,
                unit(
                    &context,
                    MemoryKind::Semantic,
                    UnitState::Active,
                    "visible-at-clock",
                ),
            )
            .await
            .expect("stage visible unit");
        let mut expires_at_clock = unit(
            &context,
            MemoryKind::Semantic,
            UnitState::Active,
            "expires-at-clock",
        );
        expires_at_clock.valid_to = Some("2026-07-22T00:00:00Z".to_string());
        store
            .stage_memory_unit(&mut tx, expires_at_clock)
            .await
            .expect("stage expired unit");
        store.commit(tx).await.expect("commit");

        let service = MemoryService::new(
            Arc::new(store),
            Arc::new(FixedClock("2026-07-22T00:00:00Z")),
            Arc::new(NoopEmbedding),
        );
        let projection = service
            .canonical_projection(&context)
            .await
            .expect("canonical projection");

        assert_eq!(projection.evaluated_at, "2026-07-22T00:00:00Z");
        assert_eq!(
            projection
                .items
                .iter()
                .map(|item| item.unit_id)
                .collect::<Vec<_>>(),
            vec![visible_id]
        );
    }

    #[tokio::test]
    async fn canonical_projection_canonicalizes_equivalent_utc_metadata() {
        let store = InMemoryStore::default();
        let context = context(&store).await;
        let mut tx = store.begin(&context).await.expect("begin");
        let mut semantic = unit(
            &context,
            MemoryKind::Semantic,
            UnitState::Active,
            "policy-hidden-semantic",
        );
        semantic.valid_from = Some("2026-07-01T00:00:00+00:00".to_string());
        semantic.valid_to = Some("2026-08-01T00:00:00+00:00".to_string());
        store
            .stage_memory_unit(&mut tx, semantic)
            .await
            .expect("stage semantic");
        store.commit(tx).await.expect("commit");

        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-22T00:00:00Z")),
            Arc::new(NoopEmbedding),
        );
        let unrestricted = service
            .canonical_projection(&context)
            .await
            .expect("unrestricted projection");
        let semantic = &unrestricted.items[0];
        assert_eq!(semantic.valid_from.as_deref(), Some("2026-07-01T00:00:00Z"));
        assert_eq!(semantic.valid_to.as_deref(), Some("2026-08-01T00:00:00Z"));
    }

    #[tokio::test]
    async fn canonical_projection_respects_resolved_kind_policy() {
        let store = InMemoryStore::default();
        let context = context(&store).await;
        let mut tx = store.begin(&context).await.expect("begin");
        store
            .stage_memory_unit(
                &mut tx,
                unit(
                    &context,
                    MemoryKind::Semantic,
                    UnitState::Active,
                    "policy-hidden-semantic",
                ),
            )
            .await
            .expect("stage semantic");
        store
            .stage_memory_unit(
                &mut tx,
                unit(
                    &context,
                    MemoryKind::Procedural,
                    UnitState::Validated,
                    "policy-visible-procedure",
                ),
            )
            .await
            .expect("stage procedure");
        store.commit(tx).await.expect("commit");

        let mut restricted = context;
        restricted
            .sources_by_kind
            .get_mut(&MemoryKind::Semantic)
            .expect("semantic policy entry")
            .clear();
        let projected = store
            .canonical_projection_units(&restricted, "2026-07-22T00:00:00Z")
            .await
            .expect("policy-bound projection");
        assert_eq!(
            projected.iter().map(|unit| unit.kind).collect::<Vec<_>>(),
            vec![MemoryKind::Procedural]
        );
    }

    #[test]
    fn canonical_projection_fingerprint_is_fixed_for_differently_ordered_records() {
        let items = vec![
            CanonicalProjectionUnit {
                unit_id: UnitId::from_u128(2),
                kind: MemoryKind::Procedural,
                fact_key: Some("b".to_string()),
                predicate: Some("does".to_string()),
                body: "B".to_string(),
                confidence: Some(0.5),
                valid_from: None,
                valid_to: None,
                state: UnitState::Validated,
                source_span: None,
                body_sha256: "bb".to_string(),
            },
            CanonicalProjectionUnit {
                unit_id: UnitId::from_u128(1),
                kind: MemoryKind::Semantic,
                fact_key: Some("a".to_string()),
                predicate: Some("states".to_string()),
                body: "A".to_string(),
                confidence: Some(1.0),
                valid_from: Some("2026-07-01T00:00:00Z".to_string()),
                valid_to: Some("2026-08-01T00:00:00Z".to_string()),
                state: UnitState::Active,
                source_span: Some("0-1".to_string()),
                body_sha256: "aa".to_string(),
            },
        ];
        assert_eq!(
            canonical_projection_fingerprint(&items).expect("fingerprint"),
            // D2 re-pinned: `state` and `source_span` joined the projection unit, so
            // the fingerprint over the serialized items necessarily moved.
            "82c9591abf851236986e56ef44a18a16147b1add4d0e817988ff21c70c68e22b"
        );
        assert_ne!(
            canonical_projection_fingerprint(&items).expect("fingerprint"),
            canonical_projection_fingerprint(&items.into_iter().rev().collect::<Vec<_>>())
                .expect("reordered fingerprint")
        );
    }
}

#[cfg(test)]
mod file_sync_tests {
    use super::*;
    use crate::{
        EmbedError, EmbeddingProvider, FixedClock, InMemoryStore, MemoryStore, NewMemoryUnit,
        NoopEmbedding, UnitState, prepare_compiled_write_from_snapshot,
    };
    use memphant_types::{
        ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest,
        ContextBindingScopeRef, EdgeId, FileSyncOperation, FileSyncOperationResult,
        FileSyncRequest, FileSyncResult, FileSyncUnitMetadata, MemoryEdgeKind, MemoryKind,
        StoredMemoryEdge, StoredMemoryUnit, TenantId, TrustLevel,
    };
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicUsize, Ordering};

    const CLOCK: FixedClock = FixedClock("2026-07-22T00:00:00Z");

    #[derive(Default)]
    struct OneShotEmbedding(AtomicUsize);

    impl EmbeddingProvider for OneShotEmbedding {
        fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
            if self.0.fetch_add(1, Ordering::SeqCst) == 0 {
                Ok(vec![vec![1.0]; texts.len()])
            } else {
                Err(EmbedError::Unavailable(
                    "file-sync replay must bypass preparation".to_string(),
                ))
            }
        }

        fn dimensions(&self) -> usize {
            1
        }

        fn id(&self) -> &str {
            "file-sync-one-shot"
        }
    }

    struct AdmissionDriftEmbedding {
        store: InMemoryStore,
        unit: Mutex<Option<StoredMemoryUnit>>,
        calls: AtomicUsize,
    }

    struct EdgeDriftEmbedding {
        store: InMemoryStore,
        edge: Mutex<Option<StoredMemoryEdge>>,
    }

    impl EmbeddingProvider for EdgeDriftEmbedding {
        fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
            if self
                .store
                .mutation_locks
                .lock()
                .unwrap()
                .values()
                .any(|lock| lock.strong_count() > 0)
            {
                return Err(EmbedError::Unavailable(
                    "embedding ran while a mutation claim was active".to_string(),
                ));
            }
            if let Some(edge) = self.edge.lock().unwrap().take() {
                self.store
                    .inner
                    .lock()
                    .unwrap()
                    .memory_edges
                    .entry(edge.tenant_id)
                    .or_default()
                    .push(edge);
            }
            Ok(vec![vec![1.0]; texts.len()])
        }

        fn dimensions(&self) -> usize {
            1
        }

        fn id(&self) -> &str {
            "file-sync-edge-drift"
        }
    }

    impl EmbeddingProvider for AdmissionDriftEmbedding {
        fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
            if self
                .store
                .mutation_locks
                .lock()
                .unwrap()
                .values()
                .any(|lock| lock.strong_count() > 0)
            {
                return Err(EmbedError::Unavailable(
                    "embedding ran while a mutation claim was active".to_string(),
                ));
            }
            self.calls.fetch_add(1, Ordering::SeqCst);
            if let Some(unit) = self.unit.lock().unwrap().take() {
                self.store
                    .inner
                    .lock()
                    .unwrap()
                    .memory_units
                    .entry(unit.tenant_id)
                    .or_default()
                    .push(unit);
            }
            Ok(vec![vec![1.0]; texts.len()])
        }

        fn dimensions(&self) -> usize {
            1
        }

        fn id(&self) -> &str {
            "file-sync-admission-drift"
        }
    }

    async fn context(store: &InMemoryStore, suffix: &str) -> ResolvedMemoryContext {
        let tenant = TenantId::new();
        let binding = store
            .resolve_context_binding(
                tenant,
                format!("file-sync:{suffix}"),
                ContextBindingRequest {
                    subject: ContextBindingEntityRef {
                        external_ref: format!("subject:{suffix}"),
                        kind: "user".to_string(),
                    },
                    actor: ContextBindingEntityRef {
                        external_ref: format!("actor:{suffix}"),
                        kind: "user".to_string(),
                    },
                    scope: ContextBindingScopeRef {
                        external_ref: format!("scope:{suffix}"),
                        kind: "user_root".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: ContextBindingAgentRef {
                        external_ref: format!("agent:{suffix}"),
                        parent_external_ref: None,
                    },
                    access_policies: Vec::new(),
                },
            )
            .await
            .unwrap();
        store
            .resolve_memory_context(
                tenant,
                binding.subject_id,
                binding.actor_id,
                binding.scope_id,
                binding.agent_node_id,
            )
            .await
            .unwrap()
    }

    async fn seed_unit(
        store: &InMemoryStore,
        context: &ResolvedMemoryContext,
        fact_key: &str,
        body: &str,
        valid_from: Option<&str>,
        valid_to: Option<&str>,
    ) -> UnitId {
        seed_unit_with_kind(
            store,
            context,
            MemoryKind::Semantic,
            fact_key,
            body,
            valid_from,
            valid_to,
        )
        .await
    }

    async fn seed_unit_with_kind(
        store: &InMemoryStore,
        context: &ResolvedMemoryContext,
        kind: MemoryKind,
        fact_key: &str,
        body: &str,
        valid_from: Option<&str>,
        valid_to: Option<&str>,
    ) -> UnitId {
        let mut tx = store.begin(context).await.unwrap();
        let id = store
            .stage_memory_unit(
                &mut tx,
                NewMemoryUnit {
                    tenant_id: context.tenant_id,
                    data_subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    kind,
                    state: UnitState::Active,
                    fact_key: Some(fact_key.to_string()),
                    predicate: Some("states".to_string()),
                    body: body.to_string(),
                    confidence: Some(1.0),
                    trust_level: TrustLevel::TrustedUser,
                    churn_class: None,
                    freshness_due_at: None,
                    actor_id: Some(context.actor_id),
                    source_kind: Some("test".to_string()),
                    source_ref: format!("test:{fact_key}"),
                    observed_at: CLOCK.0.to_string(),
                    source_episode_id: None,
                    source_resource_id: None,
                    deletion_generation: None,
                    contextual_chunks: Vec::new(),
                    valid_from: valid_from.map(str::to_string),
                    valid_to: valid_to.map(str::to_string),
                    transaction_from: None,
                    transaction_to: None,
                },
            )
            .await
            .unwrap();
        store.commit(tx).await.unwrap();
        id
    }

    fn metadata(item: &CanonicalProjectionUnit) -> FileSyncUnitMetadata {
        FileSyncUnitMetadata {
            unit_id: item.unit_id,
            kind: item.kind,
            fact_key: item.fact_key.clone(),
            predicate: item.predicate.clone(),
            confidence: item.confidence,
            valid_from: item.valid_from.clone(),
            valid_to: item.valid_to.clone(),
            body_sha256: item.body_sha256.clone(),
        }
    }

    fn request(
        context: &ResolvedMemoryContext,
        base_fingerprint: String,
        operations: Vec<FileSyncOperation>,
    ) -> FileSyncRequest {
        let plan_sha256 = file_sync_plan_sha256(&operations).unwrap();
        FileSyncRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            base_fingerprint,
            plan_sha256,
            observed_at: CLOCK.0.to_string(),
            operations,
        }
    }

    fn result(response: &MutationResponse) -> FileSyncResult {
        serde_json::from_slice(response.body()).unwrap()
    }

    fn stored_unit(
        context: &ResolvedMemoryContext,
        kind: MemoryKind,
        fact_key: &str,
        body: &str,
    ) -> StoredMemoryUnit {
        StoredMemoryUnit {
            id: UnitId::new(),
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            kind,
            state: UnitState::Active,
            fact_key: Some(fact_key.to_string()),
            predicate: Some("states".to_string()),
            body: body.to_string(),
            confidence: Some(1.0),
            trust_level: TrustLevel::TrustedUser,
            churn_class: None,
            freshness_due_at: None,
            actor_id: Some(context.actor_id),
            source_kind: Some("test".to_string()),
            source_ref: format!("test:{fact_key}"),
            observed_at: CLOCK.0.to_string(),
            source_episode_id: None,
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
            transaction_from: Some(CLOCK.0.to_string()),
            transaction_to: None,
            difficulty: None,
            stability_days: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        }
    }

    fn projection_unit(fact_key: &str, body: &str, unit_id: u128) -> CanonicalProjectionUnit {
        CanonicalProjectionUnit {
            unit_id: UnitId::from_u128(unit_id),
            kind: MemoryKind::Semantic,
            fact_key: Some(fact_key.to_string()),
            predicate: Some("states".to_string()),
            body: body.to_string(),
            confidence: Some(1.0),
            valid_from: None,
            valid_to: None,
            state: UnitState::Active,
            source_span: None,
            body_sha256: format!("{:x}", Sha256::digest(body.as_bytes())),
        }
    }

    fn body_for_projection_size(
        context: &ResolvedMemoryContext,
        mut items: Vec<CanonicalProjectionUnit>,
        adjustable_item: usize,
        target_bytes: usize,
    ) -> String {
        let response = CanonicalProjectionResponse {
            tenant_id: context.tenant_id,
            subject_id: context.data_subject_id,
            actor_id: context.actor_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            evaluated_at: MAX_CANONICAL_PROJECTION_TIMESTAMP.to_string(),
            fingerprint: "0".repeat(64),
            items: items.clone(),
        };
        let fixed_bytes = serde_json::to_vec(&response).unwrap().len();
        let body = "x".repeat(target_bytes - fixed_bytes);
        items[adjustable_item].body_sha256 = format!("{:x}", Sha256::digest(body.as_bytes()));
        items[adjustable_item].body = body.clone();
        let response = CanonicalProjectionResponse { items, ..response };
        assert_eq!(serde_json::to_vec(&response).unwrap().len(), target_bytes);
        body
    }

    #[test]
    fn file_sync_plan_digest_is_a_fixed_typed_contract() {
        let operations = vec![FileSyncOperation::Retain {
            fact_key: "profile:short".to_string(),
            predicate: "states".to_string(),
            body: "Hi.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        }];
        assert_eq!(
            file_sync_plan_sha256(&operations).unwrap(),
            "7c3fc04bc305ea5a0a54deb5c4f96fbd305d6001cb902c82dbff4a80ffda80d9"
        );
    }

    #[tokio::test]
    async fn file_sync_admission_snapshot_digest_is_ordered_and_covers_full_units_and_edges() {
        let store = InMemoryStore::default();
        let context = context(&store, "admission-digest").await;
        let first = stored_unit(&context, MemoryKind::Belief, "profile:first", "First.");
        let second = stored_unit(&context, MemoryKind::Semantic, "profile:second", "Second.");
        let snapshot =
            FileSyncTransitionSnapshot::new(vec![first.clone(), second.clone()], Vec::new());
        let digest = file_sync_admission_snapshot_sha256(&snapshot).unwrap();
        assert_eq!(
            digest,
            file_sync_admission_snapshot_sha256(&FileSyncTransitionSnapshot::new(
                vec![second.clone(), first.clone()],
                Vec::new(),
            ))
            .unwrap()
        );
        assert_ne!(
            digest,
            file_sync_admission_snapshot_sha256(&FileSyncTransitionSnapshot::new(
                vec![
                    StoredMemoryUnit {
                        trust_level: TrustLevel::Quarantined,
                        ..first.clone()
                    },
                    second.clone()
                ],
                Vec::new(),
            ))
            .unwrap()
        );
        let edge = StoredMemoryEdge {
            id: EdgeId::new(),
            tenant_id: context.tenant_id,
            scope_id: context.scope_id,
            src_id: first.id,
            dst_id: second.id,
            kind: MemoryEdgeKind::DerivedFrom,
            transaction_from: Some(CLOCK.0.to_string()),
            transaction_to: None,
        };
        let reverse_edge = StoredMemoryEdge {
            id: EdgeId::new(),
            tenant_id: context.tenant_id,
            scope_id: context.scope_id,
            src_id: second.id,
            dst_id: first.id,
            kind: MemoryEdgeKind::Supersedes,
            transaction_from: Some(CLOCK.0.to_string()),
            transaction_to: None,
        };
        let edge_digest = file_sync_admission_snapshot_sha256(&FileSyncTransitionSnapshot::new(
            vec![first.clone(), second.clone()],
            vec![edge.clone(), reverse_edge.clone()],
        ))
        .unwrap();
        assert_ne!(digest, edge_digest);
        assert_eq!(
            edge_digest,
            file_sync_admission_snapshot_sha256(&FileSyncTransitionSnapshot::new(
                vec![second.clone(), first.clone(), first],
                vec![reverse_edge, edge.clone(), edge],
            ))
            .unwrap(),
            "unit and edge duplicates/order do not change the transition digest"
        );
    }

    #[tokio::test]
    async fn file_sync_transition_snapshot_is_actor_bound() {
        let store = InMemoryStore::default();
        let context = context(&store, "actor-bound-snapshot").await;
        let own = stored_unit(&context, MemoryKind::Semantic, "profile:own", "Own fact.");
        let mut foreign = stored_unit(
            &context,
            MemoryKind::Semantic,
            "profile:foreign",
            "Foreign fact.",
        );
        foreign.actor_id = Some(memphant_types::ActorId::new());
        let cross_actor_edge = StoredMemoryEdge {
            id: EdgeId::new(),
            tenant_id: context.tenant_id,
            scope_id: context.scope_id,
            src_id: own.id,
            dst_id: foreign.id,
            kind: MemoryEdgeKind::SameSubject,
            transaction_from: Some(CLOCK.0.to_string()),
            transaction_to: None,
        };
        {
            let mut state = store.inner.lock().unwrap();
            state
                .memory_units
                .entry(context.tenant_id)
                .or_default()
                .extend([own.clone(), foreign]);
            state
                .memory_edges
                .entry(context.tenant_id)
                .or_default()
                .push(cross_actor_edge);
        }

        let snapshot = store
            .fetch_file_sync_transition_snapshot(&context)
            .await
            .expect("actor-bound snapshot");
        assert_eq!(
            snapshot
                .units
                .iter()
                .map(|unit| unit.id)
                .collect::<Vec<_>>(),
            vec![own.id]
        );
        assert!(snapshot.edges.is_empty());
    }

    #[tokio::test]
    async fn file_sync_retain_requires_semantic_policy_and_trusted_direct_provenance() {
        for (suffix, restrict_policy, actor_trust) in [
            ("policy-denied", true, TrustLevel::TrustedUser),
            ("trust-denied", false, TrustLevel::AgentOutput),
        ] {
            let store = InMemoryStore::default();
            let mut context = context(&store, suffix).await;
            if restrict_policy {
                context
                    .sources_by_kind
                    .get_mut(&MemoryKind::Semantic)
                    .expect("semantic policy entry")
                    .clear();
            }
            context.actor_trust = actor_trust;
            let service = MemoryService::new(
                Arc::new(store.clone()),
                Arc::new(CLOCK),
                Arc::new(NoopEmbedding),
            );
            let base = service.canonical_projection(&context).await.unwrap();
            let sync_request = request(
                &context,
                base.fingerprint,
                vec![FileSyncOperation::Retain {
                    fact_key: format!("profile:{suffix}"),
                    predicate: "states".to_string(),
                    body: "This semantic retain must fail closed.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                }],
            );

            assert!(matches!(
                service
                    .file_sync(&context, &format!("file-sync-{suffix}"), sync_request)
                    .await,
                Err(ServiceError::SyncInvalid(_))
            ));
            assert!(store.memory_units(context.tenant_id).is_empty());
        }
    }

    #[tokio::test]
    async fn file_sync_replay_and_stale_base_bypass_preparation() {
        let store = InMemoryStore::default();
        let context = context(&store, "replay-before-prepare").await;
        let embedder = Arc::new(OneShotEmbedding::default());
        let service =
            MemoryService::new(Arc::new(store.clone()), Arc::new(CLOCK), embedder.clone());
        let base = service.canonical_projection(&context).await.unwrap();
        let sync_request = request(
            &context,
            base.fingerprint,
            vec![FileSyncOperation::Retain {
                fact_key: "profile:replay".to_string(),
                predicate: "states".to_string(),
                body: "The replay body is valid.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        );

        let first = service
            .file_sync(
                &context,
                "file-sync-replay-before-prepare",
                sync_request.clone(),
            )
            .await
            .unwrap();
        let claim = MutationClaim::new(
            &context,
            MutationVerb::FileSync,
            "file-sync-replay-before-prepare",
            canonical_mutation_request_hash(MutationVerb::FileSync, &sync_request).unwrap(),
        )
        .unwrap();
        assert_eq!(
            store
                .lookup_mutation_replay(&context, &claim)
                .await
                .unwrap()
                .unwrap()
                .body(),
            first.body()
        );
        let replay = service
            .file_sync(
                &context,
                "file-sync-replay-before-prepare",
                sync_request.clone(),
            )
            .await
            .unwrap();

        let mut conflicting = sync_request;
        conflicting.operations = vec![FileSyncOperation::Retain {
            fact_key: "profile:replay-conflict".to_string(),
            predicate: "states".to_string(),
            body: "A different request must conflict before embedding.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        }];
        conflicting.plan_sha256 = file_sync_plan_sha256(&conflicting.operations).unwrap();
        assert!(matches!(
            service
                .file_sync(&context, "file-sync-replay-before-prepare", conflicting,)
                .await,
            Err(ServiceError::Core(CoreError::Store(
                StoreError::IdempotencyConflict
            )))
        ));

        let stale = request(
            &context,
            "0".repeat(64),
            vec![FileSyncOperation::Retain {
                fact_key: "profile:stale-short".to_string(),
                predicate: "states".to_string(),
                body: "The stale body must never be embedded.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        );
        assert!(matches!(
            service
                .file_sync(&context, "file-sync-stale-before-prepare", stale)
                .await,
            Err(ServiceError::SyncConflict(_))
        ));

        assert_eq!(first.body(), replay.body());
        assert_eq!(embedder.0.load(Ordering::SeqCst), 1);
        assert_eq!(result(&first).operations.len(), 1);
    }

    #[tokio::test]
    async fn file_sync_accepts_a_short_explicit_direct_unit() {
        let store = InMemoryStore::default();
        let context = context(&store, "short-direct-unit").await;
        let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
        let base = service.canonical_projection(&context).await.unwrap();
        let sync_request = request(
            &context,
            base.fingerprint,
            vec![FileSyncOperation::Retain {
                fact_key: "profile:short".to_string(),
                predicate: "states".to_string(),
                body: "Hi.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        );

        let response = service
            .file_sync(&context, "file-sync-short-direct-unit", sync_request)
            .await
            .unwrap();
        assert_eq!(result(&response).operations.len(), 1);
        assert_eq!(
            service.canonical_projection(&context).await.unwrap().items[0].body,
            "Hi."
        );
    }

    #[tokio::test]
    async fn unkeyed_short_candidate_remains_rejected() {
        let store = InMemoryStore::default();
        let context = context(&store, "unkeyed-short").await;
        let prepared = prepare_compiled_write_from_snapshot(
            ReflectInput {
                tenant_id: context.tenant_id,
                data_subject_id: context.data_subject_id,
                scope_id: context.scope_id,
                agent_node_id: context.agent_node_id,
                subject_generation: context.subject_generation,
                actor_id: context.actor_id,
                source_ref: "test:unkeyed-short".to_string(),
                observed_at: CLOCK.0.to_string(),
                source_body: Some("ok".to_string()),
                episode_id: None,
                resource_id: None,
                job_id: memphant_types::JobId::new(),
                compiler_version: COMPILER_VERSION.to_string(),
                candidates: vec![ReflectCandidate {
                    source_kind: "agent".to_string(),
                    trust_level: TrustLevel::AgentOutput,
                    actor_id: context.actor_id,
                    subject: None,
                    predicate: None,
                    fact_key: None,
                    kind: None,
                    body: "ok".to_string(),
                    confidence: None,
                    churn_class: None,
                    admission_hint: None,
                    target_unit_ids: None,
                    contextual_chunks: Vec::new(),
                    valid_from: None,
                    valid_to: None,
                }],
            },
            &NoopEmbedding,
            &CLOCK,
            &context,
            Vec::new(),
        )
        .await
        .unwrap();
        let PreparedCompiledWrite::Write {
            trace,
            created_unit_ids,
            ..
        } = prepared
        else {
            panic!("snapshot compile must produce a write receipt");
        };
        assert_eq!(trace.actions, [memphant_types::AdmissionAction::Reject]);
        assert!(created_unit_ids.is_empty());
    }

    #[tokio::test]
    async fn admission_drift_after_precompute_conflicts_without_open_claim_provider_work() {
        let store = InMemoryStore::default();
        let context = context(&store, "admission-drift").await;
        let embedder = Arc::new(AdmissionDriftEmbedding {
            store: store.clone(),
            unit: Mutex::new(Some(stored_unit(
                &context,
                MemoryKind::Belief,
                "profile:drift",
                "Concurrent belief.",
            ))),
            calls: AtomicUsize::new(0),
        });
        let service =
            MemoryService::new(Arc::new(store.clone()), Arc::new(CLOCK), embedder.clone());
        let base = service.canonical_projection(&context).await.unwrap();
        let sync_request = request(
            &context,
            base.fingerprint,
            vec![FileSyncOperation::Retain {
                fact_key: "profile:requested".to_string(),
                predicate: "states".to_string(),
                body: "Requested semantic unit.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        );
        let claim = MutationClaim::new(
            &context,
            MutationVerb::FileSync,
            "file-sync-admission-drift",
            canonical_mutation_request_hash(MutationVerb::FileSync, &sync_request).unwrap(),
        )
        .unwrap();

        assert!(matches!(
            service
                .file_sync(&context, "file-sync-admission-drift", sync_request,)
                .await,
            Err(ServiceError::SyncConflict(_))
        ));
        assert_eq!(embedder.calls.load(Ordering::SeqCst), 1);
        assert!(
            service
                .canonical_projection(&context)
                .await
                .unwrap()
                .items
                .is_empty()
        );
        let open = store.fetch_scope_open_units(&context).await.unwrap();
        assert_eq!(open.len(), 1);
        assert_eq!(open[0].fact_key.as_deref(), Some("profile:drift"));
        assert!(
            store
                .lookup_mutation_replay(&context, &claim)
                .await
                .unwrap()
                .is_none()
        );
    }

    #[tokio::test]
    async fn edge_only_drift_after_precompute_conflicts_without_staging_writes() {
        let store = InMemoryStore::default();
        let context = context(&store, "edge-drift").await;
        let source = seed_unit(
            &store,
            &context,
            "profile:edge-source",
            "The edge source remains stable.",
            None,
            None,
        )
        .await;
        let dependent = seed_unit(
            &store,
            &context,
            "profile:edge-dependent",
            "The edge dependent remains stable.",
            None,
            None,
        )
        .await;
        let edge = StoredMemoryEdge {
            id: EdgeId::new(),
            tenant_id: context.tenant_id,
            scope_id: context.scope_id,
            src_id: dependent,
            dst_id: source,
            kind: MemoryEdgeKind::DerivedFrom,
            transaction_from: Some(CLOCK.0.to_string()),
            transaction_to: None,
        };
        let embedder = Arc::new(EdgeDriftEmbedding {
            store: store.clone(),
            edge: Mutex::new(Some(edge.clone())),
        });
        let service = MemoryService::new(Arc::new(store.clone()), Arc::new(CLOCK), embedder);
        let base = service.canonical_projection(&context).await.unwrap();
        let before_count = store.memory_units(context.tenant_id).len();
        let sync_request = request(
            &context,
            base.fingerprint,
            vec![FileSyncOperation::Retain {
                fact_key: "profile:edge-requested".to_string(),
                predicate: "states".to_string(),
                body: "The requested unit must not land after edge drift.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        );

        assert!(matches!(
            service
                .file_sync(&context, "file-sync-edge-drift", sync_request)
                .await,
            Err(ServiceError::SyncConflict(_))
        ));
        assert_eq!(store.memory_units(context.tenant_id).len(), before_count);
        assert_eq!(store.memory_edges(context.tenant_id), vec![edge]);
    }

    #[tokio::test]
    async fn file_sync_admission_uses_the_serializable_full_open_scope_snapshot() {
        let store = InMemoryStore::default();
        let context = context(&store, "open-snapshot").await;
        let mut tx = store.begin_serializable(&context).await.unwrap();
        assert!(
            store
                .canonical_projection_units_in_tx(&mut tx, CLOCK.0)
                .await
                .unwrap()
                .is_empty()
        );

        let belief_id = seed_unit_with_kind(
            &store,
            &context,
            MemoryKind::Belief,
            "profile:greeting",
            "Hi.",
            None,
            None,
        )
        .await;
        assert!(
            store
                .canonical_projection_units(&context, CLOCK.0)
                .await
                .unwrap()
                .is_empty(),
            "the concurrent belief is intentionally outside the file projection"
        );

        let protected = store.fetch_scope_open_units_in_tx(&mut tx).await.unwrap();
        let mutable = store.fetch_scope_open_units(&context).await.unwrap();
        assert!(protected.is_empty());
        assert_eq!(
            mutable.iter().map(|unit| unit.id).collect::<Vec<_>>(),
            [belief_id]
        );

        let input = ReflectInput {
            tenant_id: context.tenant_id,
            data_subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            actor_id: context.actor_id,
            source_ref: "file-sync:snapshot:0".to_string(),
            observed_at: CLOCK.0.to_string(),
            source_body: Some("Hi.".to_string()),
            episode_id: None,
            resource_id: None,
            job_id: memphant_types::JobId::new(),
            compiler_version: COMPILER_VERSION.to_string(),
            candidates: vec![ReflectCandidate {
                source_kind: "direct".to_string(),
                trust_level: TrustLevel::TrustedUser,
                actor_id: context.actor_id,
                subject: None,
                predicate: Some("states".to_string()),
                fact_key: Some("profile:greeting".to_string()),
                kind: Some(MemoryKind::Semantic),
                body: "Hi.".to_string(),
                confidence: Some(1.0),
                churn_class: None,
                admission_hint: None,
                target_unit_ids: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
            }],
        };
        let protected_write = prepare_compiled_write_from_snapshot(
            input.clone(),
            &NoopEmbedding,
            &CLOCK,
            &context,
            protected,
        )
        .await
        .unwrap();
        let mutable_write =
            prepare_compiled_write_from_snapshot(input, &NoopEmbedding, &CLOCK, &context, mutable)
                .await
                .unwrap();
        let PreparedCompiledWrite::Write {
            write: protected_write,
            ..
        } = protected_write
        else {
            panic!("protected snapshot must compile");
        };
        let PreparedCompiledWrite::Write {
            write: mutable_write,
            ..
        } = mutable_write
        else {
            panic!("mutable snapshot must compile");
        };
        assert!(protected_write.new_edges.is_empty());
        assert_eq!(mutable_write.new_edges.len(), 1);
        assert!(matches!(
            store.commit(tx).await,
            Err(StoreError::Conflict(_))
        ));
    }

    #[tokio::test]
    async fn file_sync_commits_correct_retain_forget_once_and_replays_exact_receipt() {
        let store = InMemoryStore::default();
        let context = context(&store, "happy").await;
        let city = seed_unit(
            &store,
            &context,
            "profile:city",
            "I live in Oslo.",
            None,
            None,
        )
        .await;
        let pet = seed_unit(&store, &context, "profile:pet", "I have a cat.", None, None).await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&context).await.unwrap();
        let city_meta = metadata(base.items.iter().find(|item| item.unit_id == city).unwrap());
        let pet_meta = metadata(base.items.iter().find(|item| item.unit_id == pet).unwrap());
        let request = request(
            &context,
            base.fingerprint,
            vec![
                FileSyncOperation::Correct {
                    base: city_meta,
                    body: "I live in Lima.".to_string(),
                },
                FileSyncOperation::Retain {
                    fact_key: "profile:language".to_string(),
                    predicate: "states".to_string(),
                    body: "I speak English fluently.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                },
                FileSyncOperation::Forget { base: pet_meta },
            ],
        );

        let first = service
            .file_sync(&context, "file-sync-happy", request.clone())
            .await
            .unwrap();
        let replay = service
            .file_sync(&context, "file-sync-happy", request)
            .await
            .unwrap();
        assert_eq!(first.body(), replay.body());

        let result = result(&first);
        assert!(matches!(
            &result.operations[..],
            [
                FileSyncOperationResult::Correct { memory_unit_id, .. },
                FileSyncOperationResult::Retain { .. },
                FileSyncOperationResult::Forget { memory_unit_id: forgotten, .. }
            ] if *memory_unit_id == city && *forgotten == pet
        ));
        let after = service.canonical_projection(&context).await.unwrap();
        assert_eq!(after.fingerprint, result.fingerprint);
        let mut bodies = after
            .items
            .iter()
            .map(|item| item.body.as_str())
            .collect::<Vec<_>>();
        bodies.sort_unstable();
        assert_eq!(bodies, vec!["I live in Lima.", "I speak English fluently."]);
    }

    #[tokio::test]
    async fn later_retain_does_not_compile_against_a_corrected_units_composition_dependent() {
        let store = InMemoryStore::default();
        let context = context(&store, "correct-dependent-plan").await;
        let source = seed_unit(
            &store,
            &context,
            "profile:source",
            "The source fact is current.",
            None,
            None,
        )
        .await;
        let dependent = seed_unit(
            &store,
            &context,
            "profile:derived",
            "The composed dependent is stale.",
            None,
            None,
        )
        .await;
        {
            let mut state = store.inner.lock().unwrap();
            state
                .memory_units
                .get_mut(&context.tenant_id)
                .unwrap()
                .iter_mut()
                .find(|unit| unit.id == dependent)
                .unwrap()
                .source_kind = Some("composition".to_string());
            state
                .memory_edges
                .entry(context.tenant_id)
                .or_default()
                .push(StoredMemoryEdge {
                    id: EdgeId::new(),
                    tenant_id: context.tenant_id,
                    scope_id: context.scope_id,
                    src_id: dependent,
                    dst_id: source,
                    kind: MemoryEdgeKind::DerivedFrom,
                    transaction_from: Some(CLOCK.0.to_string()),
                    transaction_to: None,
                });
        }
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&context).await.unwrap();
        let source_meta = metadata(
            base.items
                .iter()
                .find(|item| item.unit_id == source)
                .unwrap(),
        );
        let sync_request = request(
            &context,
            base.fingerprint,
            vec![
                FileSyncOperation::Correct {
                    base: source_meta,
                    body: "The source fact is corrected.".to_string(),
                },
                FileSyncOperation::Retain {
                    fact_key: "profile:derived".to_string(),
                    predicate: "states".to_string(),
                    body: "The replacement derived fact is current.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                },
            ],
        );

        service
            .file_sync(&context, "file-sync-correct-dependent-plan", sync_request)
            .await
            .unwrap();

        let units = store.memory_units(context.tenant_id);
        let dependent_after = units.iter().find(|unit| unit.id == dependent).unwrap();
        assert_eq!(dependent_after.state, UnitState::Expired);
        assert!(dependent_after.transaction_to.is_some());
        assert!(store.memory_edges(context.tenant_id).iter().all(|edge| {
            edge.kind != MemoryEdgeKind::Contradicts
                || (edge.src_id != dependent && edge.dst_id != dependent)
        }));
    }

    #[tokio::test]
    async fn later_retain_does_not_compile_against_a_forgotten_supersedes_lineage() {
        let store = InMemoryStore::default();
        let context = context(&store, "forget-lineage-plan").await;
        let target = seed_unit(
            &store,
            &context,
            "profile:forget-target",
            "The selected lineage branch is current.",
            None,
            None,
        )
        .await;
        let ancestor = seed_unit(
            &store,
            &context,
            "profile:ancestor",
            "The shared ancestor is historical.",
            None,
            None,
        )
        .await;
        let sibling = seed_unit(
            &store,
            &context,
            "profile:lineage",
            "The sibling lineage branch is stale.",
            None,
            None,
        )
        .await;
        let dependent = seed_unit(
            &store,
            &context,
            "profile:lineage-dependent",
            "The lineage derivative is stale.",
            None,
            None,
        )
        .await;
        {
            let mut state = store.inner.lock().unwrap();
            let units = state.memory_units.get_mut(&context.tenant_id).unwrap();
            let ancestor_unit = units.iter_mut().find(|unit| unit.id == ancestor).unwrap();
            ancestor_unit.state = UnitState::Superseded;
            ancestor_unit.transaction_to = Some(CLOCK.0.to_string());
            units
                .iter_mut()
                .find(|unit| unit.id == dependent)
                .unwrap()
                .source_kind = Some("composition".to_string());
            state
                .memory_edges
                .entry(context.tenant_id)
                .or_default()
                .extend([
                    StoredMemoryEdge {
                        id: EdgeId::new(),
                        tenant_id: context.tenant_id,
                        scope_id: context.scope_id,
                        src_id: target,
                        dst_id: ancestor,
                        kind: MemoryEdgeKind::Supersedes,
                        transaction_from: Some(CLOCK.0.to_string()),
                        transaction_to: None,
                    },
                    StoredMemoryEdge {
                        id: EdgeId::new(),
                        tenant_id: context.tenant_id,
                        scope_id: context.scope_id,
                        src_id: sibling,
                        dst_id: ancestor,
                        kind: MemoryEdgeKind::Supersedes,
                        transaction_from: Some(CLOCK.0.to_string()),
                        transaction_to: None,
                    },
                    StoredMemoryEdge {
                        id: EdgeId::new(),
                        tenant_id: context.tenant_id,
                        scope_id: context.scope_id,
                        src_id: dependent,
                        dst_id: sibling,
                        kind: MemoryEdgeKind::DerivedFrom,
                        transaction_from: Some(CLOCK.0.to_string()),
                        transaction_to: None,
                    },
                ]);
        }
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&context).await.unwrap();
        let target_meta = metadata(
            base.items
                .iter()
                .find(|item| item.unit_id == target)
                .unwrap(),
        );
        let sync_request = request(
            &context,
            base.fingerprint,
            vec![
                FileSyncOperation::Forget { base: target_meta },
                FileSyncOperation::Retain {
                    fact_key: "profile:lineage".to_string(),
                    predicate: "states".to_string(),
                    body: "The replacement lineage fact is current.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                },
                FileSyncOperation::Retain {
                    fact_key: "profile:lineage-dependent".to_string(),
                    predicate: "states".to_string(),
                    body: "The replacement lineage derivative is current.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                },
            ],
        );

        service
            .file_sync(&context, "file-sync-forget-lineage-plan", sync_request)
            .await
            .unwrap();

        let units = store.memory_units(context.tenant_id);
        for removed in [target, ancestor, sibling, dependent] {
            let removed = units.iter().find(|unit| unit.id == removed).unwrap();
            assert_eq!(removed.state, UnitState::Deleted);
            assert!(removed.transaction_to.is_some());
        }
        assert!(store.memory_edges(context.tenant_id).iter().all(|edge| {
            edge.kind != MemoryEdgeKind::Contradicts
                || (![sibling, dependent].contains(&edge.src_id)
                    && ![sibling, dependent].contains(&edge.dst_id))
        }));
    }

    #[tokio::test]
    async fn file_sync_retain_preserves_native_contradiction_edges() {
        let store = InMemoryStore::default();
        let context = context(&store, "contradiction").await;
        let old_id = seed_unit(
            &store,
            &context,
            "profile:city",
            "I live in Oslo.",
            None,
            None,
        )
        .await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&context).await.unwrap();
        let request = request(
            &context,
            base.fingerprint,
            vec![FileSyncOperation::Retain {
                fact_key: "profile:city".to_string(),
                predicate: "states".to_string(),
                body: "I now live in Lima permanently.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        );
        service
            .file_sync(&context, "file-sync-contradiction", request)
            .await
            .unwrap();
        assert!(store.memory_edges(context.tenant_id).iter().any(|edge| {
            edge.kind == memphant_types::MemoryEdgeKind::Contradicts && edge.src_id == old_id
        }));
    }

    #[tokio::test]
    async fn file_sync_keeps_multiple_distinct_retains_in_plan_order() {
        let store = InMemoryStore::default();
        let context = context(&store, "multiple-retains").await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&context).await.unwrap();
        let request = request(
            &context,
            base.fingerprint,
            vec![
                FileSyncOperation::Retain {
                    fact_key: "profile:first".to_string(),
                    predicate: "states".to_string(),
                    body: "The first retained fact is valid.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                },
                FileSyncOperation::Retain {
                    fact_key: "profile:second".to_string(),
                    predicate: "states".to_string(),
                    body: "The second retained fact is valid.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                },
            ],
        );
        let response = service
            .file_sync(&context, "file-sync-multiple-retains", request)
            .await
            .unwrap();
        let result = result(&response);
        assert!(matches!(
            &result.operations[..],
            [
                FileSyncOperationResult::Retain { created: first },
                FileSyncOperationResult::Retain { created: second }
            ] if first.len() == 1 && second.len() == 1 && first != second
        ));
        assert_eq!(
            service
                .canonical_projection(&context)
                .await
                .unwrap()
                .items
                .len(),
            2
        );
    }

    #[tokio::test]
    async fn file_sync_rejects_stale_base_and_rolls_back_a_late_batch_failure() {
        let store = InMemoryStore::default();
        let context = context(&store, "rollback").await;
        let original = seed_unit(
            &store,
            &context,
            "profile:rollback",
            "The original value stays intact.",
            None,
            None,
        )
        .await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&context).await.unwrap();
        let before_count = store.memory_units(context.tenant_id).len();

        let stale = request(
            &context,
            "0".repeat(64),
            vec![FileSyncOperation::Retain {
                fact_key: "profile:stale".to_string(),
                predicate: "states".to_string(),
                body: "This stale write must not land.".to_string(),
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        );
        assert!(matches!(
            service.file_sync(&context, "file-sync-stale", stale).await,
            Err(ServiceError::SyncConflict(_))
        ));
        assert_eq!(store.memory_units(context.tenant_id).len(), before_count);

        let original_meta = metadata(
            base.items
                .iter()
                .find(|item| item.unit_id == original)
                .unwrap(),
        );
        store.fail_next_mutation_response();
        let late_failure = request(
            &context,
            base.fingerprint,
            vec![
                FileSyncOperation::Correct {
                    base: original_meta,
                    body: "The staged correction must roll back.".to_string(),
                },
                FileSyncOperation::Retain {
                    fact_key: "profile:must-roll-back".to_string(),
                    predicate: "states".to_string(),
                    body: "The staged retain must roll back too.".to_string(),
                    confidence: 1.0,
                    valid_from: None,
                    valid_to: None,
                },
            ],
        );
        assert!(
            service
                .file_sync(&context, "file-sync-late-failure", late_failure)
                .await
                .is_err()
        );
        assert_eq!(store.memory_units(context.tenant_id).len(), before_count);
    }

    #[tokio::test]
    async fn file_sync_enforces_the_exact_final_projection_ceiling_atomically() {
        assert_eq!(
            jiff::Timestamp::MAX.to_string(),
            MAX_CANONICAL_PROJECTION_TIMESTAMP
        );
        let store = InMemoryStore::default();
        let under_context = context(&store, "projection-size-under").await;
        let service = MemoryService::new(Arc::new(store), Arc::new(CLOCK), Arc::new(NoopEmbedding));
        let base = service.canonical_projection(&under_context).await.unwrap();
        let first_fact_key = "profile:projection-size-first";
        let second_fact_key = "profile:projection-size-second";
        let first_body = "The first staged retain must roll back.";
        let second_body = body_for_projection_size(
            &under_context,
            vec![
                projection_unit(first_fact_key, first_body, 1),
                projection_unit(second_fact_key, "", 2),
            ],
            1,
            MAX_CANONICAL_PROJECTION_ENCODED_BYTES - 1,
        );
        service
            .file_sync(
                &under_context,
                "file-sync-projection-size-under",
                request(
                    &under_context,
                    base.fingerprint,
                    vec![
                        FileSyncOperation::Retain {
                            fact_key: first_fact_key.to_string(),
                            predicate: "states".to_string(),
                            body: first_body.to_string(),
                            confidence: 1.0,
                            valid_from: None,
                            valid_to: None,
                        },
                        FileSyncOperation::Retain {
                            fact_key: second_fact_key.to_string(),
                            predicate: "states".to_string(),
                            body: second_body,
                            confidence: 1.0,
                            valid_from: None,
                            valid_to: None,
                        },
                    ],
                ),
            )
            .await
            .unwrap();
        let mut projection = service.canonical_projection(&under_context).await.unwrap();
        projection.evaluated_at = MAX_CANONICAL_PROJECTION_TIMESTAMP.to_string();
        assert_eq!(projection.items.len(), 2);
        assert_eq!(
            serde_json::to_vec(&projection).unwrap().len(),
            MAX_CANONICAL_PROJECTION_ENCODED_BYTES - 1
        );

        let store = InMemoryStore::default();
        let over_context = context(&store, "projection-size-over").await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&over_context).await.unwrap();
        let before_units = store.memory_units(over_context.tenant_id);
        let second_body = body_for_projection_size(
            &over_context,
            vec![
                projection_unit(first_fact_key, first_body, 1),
                projection_unit(second_fact_key, "", 2),
            ],
            1,
            MAX_CANONICAL_PROJECTION_ENCODED_BYTES + 1,
        );
        let error = service
            .file_sync(
                &over_context,
                "file-sync-projection-size-over",
                request(
                    &over_context,
                    base.fingerprint.clone(),
                    vec![
                        FileSyncOperation::Retain {
                            fact_key: first_fact_key.to_string(),
                            predicate: "states".to_string(),
                            body: first_body.to_string(),
                            confidence: 1.0,
                            valid_from: None,
                            valid_to: None,
                        },
                        FileSyncOperation::Retain {
                            fact_key: second_fact_key.to_string(),
                            predicate: "states".to_string(),
                            body: second_body,
                            confidence: 1.0,
                            valid_from: None,
                            valid_to: None,
                        },
                    ],
                ),
            )
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            ServiceError::ProjectionTooLarge {
                max_bytes: MAX_CANONICAL_PROJECTION_ENCODED_BYTES
            }
        ));
        assert_eq!(store.memory_units(over_context.tenant_id), before_units);
        assert_eq!(
            service.canonical_projection(&over_context).await.unwrap(),
            base
        );
    }

    #[tokio::test]
    async fn file_sync_rejects_duplicate_new_fact_and_duplicate_unit_touches_prewrite() {
        let store = InMemoryStore::default();
        let context = context(&store, "duplicates").await;
        let id = seed_unit(
            &store,
            &context,
            "profile:name",
            "My name is Sid.",
            None,
            None,
        )
        .await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        );
        let base = service.canonical_projection(&context).await.unwrap();
        let meta = metadata(base.items.iter().find(|item| item.unit_id == id).unwrap());

        for (key, operations) in [
            (
                "duplicate-fact",
                vec![
                    FileSyncOperation::Retain {
                        fact_key: "profile:duplicate".to_string(),
                        predicate: "states".to_string(),
                        body: "The first duplicate body is valid.".to_string(),
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                    },
                    FileSyncOperation::Retain {
                        fact_key: "profile:duplicate".to_string(),
                        predicate: "states".to_string(),
                        body: "The second duplicate body is valid.".to_string(),
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                    },
                ],
            ),
            (
                "duplicate-touch",
                vec![
                    FileSyncOperation::Correct {
                        base: meta.clone(),
                        body: "My name is Sidney.".to_string(),
                    },
                    FileSyncOperation::Forget { base: meta.clone() },
                ],
            ),
            (
                "duplicate-fact-across-kinds",
                vec![
                    FileSyncOperation::Correct {
                        base: meta.clone(),
                        body: "My name is Sidney.".to_string(),
                    },
                    FileSyncOperation::Retain {
                        fact_key: "profile:name".to_string(),
                        predicate: "states".to_string(),
                        body: "My name remains Sidney in this duplicate.".to_string(),
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                    },
                ],
            ),
        ] {
            let request = request(&context, base.fingerprint.clone(), operations);
            assert!(matches!(
                service.file_sync(&context, key, request).await,
                Err(ServiceError::SyncInvalid(_))
            ));
        }
        assert_eq!(store.memory_units(context.tenant_id).len(), 1);
    }
}

/// Errors surfaced by the application layer. Transport layers map these onto
/// their envelope (REST status codes / MCP tool errors).
#[derive(Debug, thiserror::Error)]
pub enum ServiceError {
    #[error(transparent)]
    Core(#[from] CoreError),
    #[error("invalid request: {0}")]
    Invalid(String),
    #[error("invalid file sync: {0}")]
    SyncInvalid(String),
    #[error("file sync conflict: {0}")]
    SyncConflict(String),
    #[error("canonical projection exceeds {max_bytes} encoded bytes")]
    ProjectionTooLarge { max_bytes: usize },
}

enum PreparedFileSyncOperation {
    Correct {
        memory_unit_id: UnitId,
        write: CorrectionWrite,
    },
    Retain {
        created: Vec<UnitId>,
        write: CompiledWrite,
    },
    Forget {
        memory_unit_id: UnitId,
        write: ForgetWrite,
    },
}

fn apply_compiled_write_to_snapshot(
    working: &mut FileSyncTransitionSnapshot,
    write: &CompiledWrite,
) {
    for update in &write.unit_updates {
        if let Some(unit) = working.units.iter_mut().find(|unit| unit.id == update.id) {
            unit.state = update.state;
            unit.transaction_to = update.transaction_to.clone();
        }
    }
    working.units.extend(write.new_units.iter().cloned());
    working.edges.extend(write.new_edges.iter().cloned());
    working.units.sort_unstable_by_key(|unit| unit.id.as_uuid());
    working.units.dedup_by_key(|unit| unit.id);
    working.edges.sort_unstable_by_key(|edge| edge.id.as_uuid());
    working.edges.dedup_by_key(|edge| edge.id);
}

fn projection_items(
    units: Vec<StoredMemoryUnit>,
) -> Result<Vec<CanonicalProjectionUnit>, ServiceError> {
    units
        .into_iter()
        .map(|mut unit| {
            canonicalize_optional_timestamp(&mut unit.valid_from, "projection.valid_from")?;
            canonicalize_optional_timestamp(&mut unit.valid_to, "projection.valid_to")?;
            validate_valid_interval(unit.valid_from.as_deref(), unit.valid_to.as_deref())?;
            Ok(CanonicalProjectionUnit {
                unit_id: unit.id,
                kind: unit.kind,
                fact_key: unit.fact_key,
                predicate: unit.predicate,
                body_sha256: format!("{:x}", Sha256::digest(unit.body.as_bytes())),
                body: unit.body,
                confidence: unit.confidence,
                valid_from: unit.valid_from,
                valid_to: unit.valid_to,
                state: unit.state,
                // D2. The same primitive the recall correction handle uses, so
                // the file plane and the API can never disagree about where a
                // unit came from.
                source_span: memphant_types::covering_source_span(&unit.contextual_chunks),
            })
        })
        .collect()
}

fn canonical_projection_response(
    context: &ResolvedMemoryContext,
    evaluated_at: String,
    items: Vec<CanonicalProjectionUnit>,
) -> Result<CanonicalProjectionResponse, ServiceError> {
    let response = CanonicalProjectionResponse {
        tenant_id: context.tenant_id,
        subject_id: context.data_subject_id,
        actor_id: context.actor_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        evaluated_at,
        fingerprint: canonical_projection_fingerprint(&items)?,
        items,
    };
    if serde_json::to_vec(&response)
        .map_err(|error| {
            ServiceError::Invalid(format!("canonical projection cannot be encoded: {error}"))
        })?
        .len()
        > MAX_CANONICAL_PROJECTION_ENCODED_BYTES
    {
        return Err(ServiceError::ProjectionTooLarge {
            max_bytes: MAX_CANONICAL_PROJECTION_ENCODED_BYTES,
        });
    }
    Ok(response)
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn canonicalize_optional_timestamp(
    value: &mut Option<String>,
    field: &str,
) -> Result<(), ServiceError> {
    if let Some(timestamp) = value {
        *timestamp = canonical_utc_timestamp(timestamp, field)?;
    }
    Ok(())
}

fn metadata_matches(base: &FileSyncUnitMetadata, current: &CanonicalProjectionUnit) -> bool {
    base.unit_id == current.unit_id
        && base.kind == current.kind
        && base.fact_key == current.fact_key
        && base.predicate == current.predicate
        && base.confidence == current.confidence
        && base.valid_from == current.valid_from
        && base.valid_to == current.valid_to
        && base.body_sha256 == current.body_sha256
}

/// Stable SHA-256 of typed, ordered file-sync operations. The operation DTOs
/// contain no unordered maps, so their serde field order is the canonical plan
/// encoding shared by the server and CLI.
pub fn file_sync_plan_sha256(
    operations: &[FileSyncOperation],
) -> Result<String, serde_json::Error> {
    serde_json::to_vec(operations).map(|encoded| format!("{:x}", Sha256::digest(encoded)))
}

/// Stable SHA-256 of every unit and edge that can affect ordered file-sync
/// admission or native correction/forget cascades. Historical/deleted units
/// remain in the digest because supersedes closure can traverse them.
pub fn file_sync_admission_snapshot_sha256(
    snapshot: &FileSyncTransitionSnapshot,
) -> Result<String, serde_json::Error> {
    let ordered = FileSyncTransitionSnapshot::new(snapshot.units.clone(), snapshot.edges.clone());
    serde_json::to_vec(&(ordered.units, ordered.edges))
        .map(|encoded| format!("{:x}", Sha256::digest(encoded)))
}

fn validate_file_sync_metadata(
    base: &mut FileSyncUnitMetadata,
    field: &str,
) -> Result<(), ServiceError> {
    if !is_sha256(&base.body_sha256) {
        return Err(ServiceError::SyncInvalid(format!(
            "{field}.body_sha256 must be a lowercase SHA-256 digest"
        )));
    }
    if base
        .fact_key
        .as_deref()
        .is_some_and(|value| value.trim().is_empty())
        || base
            .predicate
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
    {
        return Err(ServiceError::SyncInvalid(format!(
            "{field} optional fact_key and predicate must not be blank"
        )));
    }
    if base
        .confidence
        .is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
    {
        return Err(ServiceError::SyncInvalid(format!(
            "{field}.confidence must be finite and between 0 and 1"
        )));
    }
    canonicalize_optional_timestamp(&mut base.valid_from, &format!("{field}.valid_from"))
        .map_err(|error| ServiceError::SyncInvalid(error.to_string()))?;
    canonicalize_optional_timestamp(&mut base.valid_to, &format!("{field}.valid_to"))
        .map_err(|error| ServiceError::SyncInvalid(error.to_string()))?;
    validate_valid_interval(base.valid_from.as_deref(), base.valid_to.as_deref())
        .map_err(|error| ServiceError::SyncInvalid(error.to_string()))
}

fn sync_store_error(error: StoreError) -> ServiceError {
    match error {
        StoreError::SerializationConflict => {
            ServiceError::SyncConflict("serializable transaction conflicted".to_string())
        }
        other => ServiceError::Core(CoreError::Store(other)),
    }
}

fn sync_operation_error(error: StoreError) -> ServiceError {
    match error {
        StoreError::SerializationConflict => {
            ServiceError::SyncConflict("serializable transaction conflicted".to_string())
        }
        StoreError::Conflict(message) => ServiceError::SyncInvalid(message),
        StoreError::NotFound(entity) => {
            ServiceError::SyncInvalid(format!("file sync target not found: {entity}"))
        }
        other => ServiceError::Core(CoreError::Store(other)),
    }
}

fn sync_commit_error(error: StoreError) -> ServiceError {
    match error {
        StoreError::SerializationConflict | StoreError::Conflict(_) => {
            ServiceError::SyncConflict("serializable transaction conflicted".to_string())
        }
        other => ServiceError::Core(CoreError::Store(other)),
    }
}

/// SHA-256 of the canonical JSON encoding of ordered projection unit records.
pub fn canonical_projection_fingerprint(
    items: &[CanonicalProjectionUnit],
) -> Result<String, ServiceError> {
    let encoded = serde_json::to_vec(items).map_err(|error| {
        ServiceError::Invalid(format!("canonical projection cannot be encoded: {error}"))
    })?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

fn canonical_utc_timestamp(value: &str, field: &str) -> Result<String, ServiceError> {
    if !(value.ends_with('Z') || value.ends_with("+00:00")) {
        return Err(ServiceError::Invalid(format!(
            "{field} must use a UTC offset"
        )));
    }
    value
        .parse::<jiff::Timestamp>()
        .map(crate::fmt_rfc3339)
        .map_err(|_| ServiceError::Invalid(format!("{field} must be RFC3339")))
}

#[cfg(test)]
mod structured_provider_retry_tests {
    use std::collections::{BTreeMap, VecDeque};
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::Duration;

    use super::*;
    use crate::{
        FixedClock, InMemoryStore, NoopEmbedding, StructuredObservation,
        StructuredObservationDisposition, StructuredStateOp, StructuredStateOperation,
        StructuredStateProviderError, StructuredStateProviderIdentity,
    };
    use serde_json::json;

    async fn bind_test_context(
        store: &InMemoryStore,
        tenant: TenantId,
        suffix: &str,
    ) -> ResolvedMemoryContext {
        use memphant_types::{
            ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest,
            ContextBindingScopeRef,
        };
        let binding = store
            .resolve_context_binding(
                tenant,
                format!("test:{suffix}"),
                ContextBindingRequest {
                    subject: ContextBindingEntityRef {
                        external_ref: format!("subject:{suffix}"),
                        kind: "user".to_string(),
                    },
                    actor: ContextBindingEntityRef {
                        external_ref: format!("actor:{suffix}"),
                        kind: "user".to_string(),
                    },
                    scope: ContextBindingScopeRef {
                        external_ref: format!("scope:{suffix}"),
                        kind: "memory".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: ContextBindingAgentRef {
                        external_ref: format!("agent:{suffix}"),
                        parent_external_ref: None,
                    },
                    access_policies: Vec::new(),
                },
            )
            .await
            .unwrap();
        store
            .resolve_memory_context(
                tenant,
                binding.subject_id,
                binding.actor_id,
                binding.scope_id,
                binding.agent_node_id,
            )
            .await
            .unwrap()
    }

    fn episode_request(
        context: &ResolvedMemoryContext,
        source_ref: impl Into<String>,
        body: impl Into<String>,
    ) -> RetainEpisodeHttpRequest {
        RetainEpisodeHttpRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_ref: source_ref.into(),
            observed_at: "2026-07-13T00:00:00Z".to_string(),
            payload: memphant_types::RetainPayload::Episode(memphant_types::RetainEpisodePayload {
                source_kind: "user".to_string(),
                body: body.into(),
                subject: None,
                predicate: None,
            }),
        }
    }

    struct RetryProvider {
        identity: StructuredStateProviderIdentity,
        responses: Mutex<VecDeque<Result<Vec<StructuredStateOp>, StructuredStateProviderError>>>,
    }

    struct DelayedProvider {
        identity: StructuredStateProviderIdentity,
        calls: Arc<AtomicUsize>,
        in_flight: Arc<AtomicUsize>,
        max_in_flight: Arc<AtomicUsize>,
        active_item_counts: Arc<Mutex<Vec<(String, usize)>>>,
    }

    impl StructuredStateProvider for DelayedProvider {
        fn identity(&self) -> &StructuredStateProviderIdentity {
            &self.identity
        }

        fn extract<'a>(
            &'a self,
            request: &'a StructuredStateRequest,
        ) -> Pin<
            Box<
                dyn Future<
                        Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>,
                    > + Send
                    + 'a,
            >,
        > {
            self.calls.fetch_add(1, Ordering::SeqCst);
            let current = self.in_flight.fetch_add(1, Ordering::SeqCst) + 1;
            self.max_in_flight.fetch_max(current, Ordering::SeqCst);
            let in_flight = Arc::clone(&self.in_flight);
            let slice = request.evidence_slices[0].clone();
            let body = format!("user: {}", slice.body);
            self.active_item_counts
                .lock()
                .unwrap()
                .push((body.clone(), 0));
            Box::pin(async move {
                let delay = if body.contains("Oslo") {
                    40
                } else if body.contains("Lima") {
                    20
                } else {
                    1
                };
                tokio::time::sleep(Duration::from_millis(delay)).await;
                in_flight.fetch_sub(1, Ordering::SeqCst);
                let quote = body.strip_prefix("user: ").unwrap();
                Ok(vec![StructuredObservation {
                    namespace: "profile".to_string(),
                    item_key: "home_city".to_string(),
                    fields: BTreeMap::from([(
                        "value".to_string(),
                        json!(quote.trim_end_matches('.').rsplit(' ').next().unwrap()),
                    )]),
                    disposition: StructuredObservationDisposition::State,
                    evidence_slice_id: slice.id,
                    evidence_quote: quote.to_string(),
                    valid_from: None,
                    valid_to: None,
                }])
            })
        }
    }

    impl StructuredStateProvider for RetryProvider {
        fn identity(&self) -> &StructuredStateProviderIdentity {
            &self.identity
        }

        fn extract<'a>(
            &'a self,
            request: &'a StructuredStateRequest,
        ) -> Pin<
            Box<
                dyn Future<
                        Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>,
                    > + Send
                    + 'a,
            >,
        > {
            let response = self
                .responses
                .lock()
                .unwrap()
                .pop_front()
                .unwrap()
                .map(|operations| {
                    operations
                        .into_iter()
                        .map(|operation| StructuredObservation {
                            namespace: operation.namespace,
                            item_key: operation.item_key,
                            fields: operation.fields,
                            disposition: if operation.operation == StructuredStateOperation::Append
                            {
                                StructuredObservationDisposition::Event
                            } else {
                                StructuredObservationDisposition::State
                            },
                            evidence_slice_id: request.evidence_slices[0].id.clone(),
                            evidence_quote: operation.evidence_quote,
                            valid_from: operation.valid_from,
                            valid_to: operation.valid_to,
                        })
                        .collect()
                });
            Box::pin(async move { response })
        }
    }

    #[tokio::test]
    async fn provider_failure_writes_nothing_and_same_job_redelivery_succeeds() {
        let body = "user: My home city is Oslo.";
        let quote = "My home city is Oslo.";
        let start = body.find(quote).unwrap();
        let operation = StructuredStateOp {
            operation: StructuredStateOperation::Create,
            namespace: "profile".to_string(),
            item_key: "home_city".to_string(),
            target_unit_ids: Vec::new(),
            fields: BTreeMap::from([("value".to_string(), json!("Oslo"))]),
            evidence_quote: quote.to_string(),
            source_span: format!("{start}-{}", start + quote.len()),
            valid_from: None,
            valid_to: None,
        };
        let provider = RetryProvider {
            identity: StructuredStateProviderIdentity {
                model: "test/model".to_string(),
                prompt_hash: "prompt".to_string(),
                schema_hash: "schema".to_string(),
            },
            responses: Mutex::new(VecDeque::from([
                Err(StructuredStateProviderError::Unavailable(
                    "temporary outage".to_string(),
                )),
                Ok(vec![operation]),
            ])),
        };
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "redelivery").await;
        let scope = context.scope_id;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(Arc::new(provider));
        service
            .retain(
                &context,
                "structured:redelivery",
                TrustLevel::TrustedUser,
                episode_request(&context, "structured:redelivery", body),
            )
            .await
            .unwrap();
        let queued_compiler = store.reflect_jobs(tenant)[0].compiler_version.clone();
        assert!(queued_compiler.contains("+structured-"));
        let job = store
            .claim_reflect_jobs(
                JobFilter {
                    tenant: Some(tenant),
                    scope: Some(scope),
                },
                1,
            )
            .await
            .unwrap()
            .pop()
            .unwrap();
        let context = service.resolve_job_context(&job).await.unwrap();

        assert!(matches!(
            service
                .compile_job(&job, &context, Some("different-compiler".to_string()), None)
                .await,
            Err(ServiceError::Invalid(_))
        ));
        assert!(store.memory_units(tenant).is_empty());
        assert!(store.reflect_traces(tenant).is_empty());

        assert!(matches!(
            service.compile_job(&job, &context, None, None).await,
            Err(ServiceError::Core(CoreError::ProviderUnavailable(_)))
        ));
        assert!(store.memory_units(tenant).is_empty());
        assert!(store.reflect_traces(tenant).is_empty());

        service
            .compile_job(&job, &context, None, None)
            .await
            .unwrap();
        store.complete_reflect_job(&job).await.unwrap();
        assert_eq!(store.memory_units(tenant).len(), 2);
        let traces = store.reflect_traces(tenant);
        assert_eq!(traces.len(), 1);
        assert_eq!(traces[0].compiler_version, queued_compiler);
    }

    /// A tick that failed every job must not look like a tick with nothing to
    /// do. Both complete zero jobs, so the old bare-count return made them
    /// indistinguishable and `while tick() > 0 {}` exited on total failure
    /// exactly as it did on an empty queue — a harness could report a fully
    /// drained corpus while nothing had compiled, silently understating every
    /// recall number measured on it.
    #[tokio::test]
    async fn a_tick_that_failed_every_job_is_not_reported_as_an_idle_tick() {
        let provider = Arc::new(RetryProvider {
            identity: StructuredStateProviderIdentity {
                model: "test/model".to_string(),
                prompt_hash: "prompt".to_string(),
                schema_hash: "schema".to_string(),
            },
            responses: Mutex::new(VecDeque::from([Err(
                StructuredStateProviderError::InvalidOutput("schema mismatch".to_string()),
            )])),
        });
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "tick-honesty").await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(provider.clone());

        // Baseline: an empty queue. This is what a failing tick must NOT match.
        let idle = service.run_worker_tick(usize::MAX).await.unwrap();
        assert!(idle.is_idle());
        assert!(idle.is_clean());

        service
            .retain(
                &context,
                "structured:tick-honesty",
                TrustLevel::TrustedUser,
                episode_request(
                    &context,
                    "structured:tick-honesty",
                    "user: My home city is Oslo.",
                ),
            )
            .await
            .unwrap();

        let tick = service.run_worker_tick(usize::MAX).await.unwrap();

        // The property under test, stated three ways.
        assert_eq!(tick.completed, 0, "nothing compiled: {tick:?}");
        assert!(
            !tick.is_idle(),
            "a tick that failed a job claimed work and must not read as idle: {tick:?}"
        );
        assert!(
            !tick.is_clean(),
            "a tick that failed a job must not read as clean: {tick:?}"
        );
        assert_ne!(
            tick, idle,
            "total failure must be distinguishable from an empty queue"
        );
        assert_eq!(tick.failed + tick.retried, 1, "one job errored: {tick:?}");
    }

    #[tokio::test]
    async fn worker_terminally_fails_provider_errors_after_provider_owned_attempts() {
        for failure in [
            StructuredStateProviderError::Unavailable("attempts exhausted".to_string()),
            StructuredStateProviderError::InvalidOutput("schema mismatch".to_string()),
        ] {
            let provider = Arc::new(RetryProvider {
                identity: StructuredStateProviderIdentity {
                    model: "test/model".to_string(),
                    prompt_hash: "prompt".to_string(),
                    schema_hash: "schema".to_string(),
                },
                responses: Mutex::new(VecDeque::from([Err(failure), Ok(Vec::new())])),
            });
            let store = InMemoryStore::default();
            let tenant = TenantId::new();
            let context = bind_test_context(&store, tenant, "terminal-provider").await;
            let service = MemoryService::new(
                Arc::new(store.clone()),
                Arc::new(FixedClock("2026-07-13T00:00:00Z")),
                Arc::new(NoopEmbedding),
            )
            .with_structured_state_provider(provider.clone());
            service
                .retain(
                    &context,
                    "structured:terminal-provider",
                    TrustLevel::TrustedUser,
                    episode_request(
                        &context,
                        "structured:terminal-provider",
                        "user: My home city is Oslo.",
                    ),
                )
                .await
                .unwrap();

            let tick = service.run_worker_tick(1).await.unwrap();
            assert_eq!(tick.completed, 0);
            assert_eq!(tick.failed, 1, "expected a terminal failure: {tick:?}");
            assert_eq!(store.dead_letter_count().await.unwrap(), 1);
            // Dead-lettered, so the next tick must claim nothing at all.
            assert!(service.run_worker_tick(1).await.unwrap().is_idle());
            assert_eq!(
                provider.responses.lock().unwrap().len(),
                1,
                "the job layer must not multiply provider-owned attempts"
            );
        }
    }

    #[tokio::test(start_paused = true)]
    async fn worker_prepares_and_compiles_same_lane_sequentially() {
        let calls = Arc::new(AtomicUsize::new(0));
        let in_flight = Arc::new(AtomicUsize::new(0));
        let max_in_flight = Arc::new(AtomicUsize::new(0));
        let active_item_counts = Arc::new(Mutex::new(Vec::new()));
        let provider = DelayedProvider {
            identity: StructuredStateProviderIdentity {
                model: "test/model".to_string(),
                prompt_hash: "prompt".to_string(),
                schema_hash: "schema".to_string(),
            },
            calls: Arc::clone(&calls),
            in_flight,
            max_in_flight: Arc::clone(&max_in_flight),
            active_item_counts: Arc::clone(&active_item_counts),
        };
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "same-lane").await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(Arc::new(provider))
        .with_structured_state_prefetch_concurrency(2);
        for city in ["Oslo", "Lima"] {
            let key = format!("structured:same-lane:{city}");
            service
                .retain(
                    &context,
                    &key,
                    TrustLevel::TrustedUser,
                    episode_request(
                        &context,
                        format!("structured:same-lane:{city}"),
                        format!("user: My home city is {city}."),
                    ),
                )
                .await
                .unwrap();
        }

        assert_eq!(service.run_worker_tick(16).await.unwrap().completed, 2);
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        assert_eq!(max_in_flight.load(Ordering::SeqCst), 1);
        assert_eq!(
            *active_item_counts.lock().unwrap(),
            vec![
                ("user: My home city is Oslo.".to_string(), 0),
                ("user: My home city is Lima.".to_string(), 0),
            ]
        );
        let active = store
            .memory_units(tenant)
            .into_iter()
            .filter(|unit| {
                unit.state == memphant_types::UnitState::Active
                    && unit
                        .fact_key
                        .as_deref()
                        .is_some_and(|key| key.ends_with(":profile:home_city"))
            })
            .map(|unit| unit.body)
            .collect::<Vec<_>>();
        assert!(
            active.iter().any(|body| body.contains("Lima")),
            "{active:?}"
        );
    }

    #[tokio::test]
    async fn worker_batch_is_not_capped_by_provider_prefetch_concurrency() {
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "batch-not-prefetch").await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_prefetch_concurrency(2);
        for index in 0..6 {
            let key = format!("batch-not-prefetch:{index}");
            service
                .retain(
                    &context,
                    &key,
                    TrustLevel::TrustedUser,
                    episode_request(&context, key.clone(), format!("user: event {index}")),
                )
                .await
                .unwrap();
        }

        assert_eq!(service.run_worker_tick(16).await.unwrap().completed, 6);
        assert_eq!(service.pending_worker_job_count().await.unwrap(), 0);
    }

    #[tokio::test(start_paused = true)]
    async fn worker_prepares_distinct_lanes_concurrently() {
        let calls = Arc::new(AtomicUsize::new(0));
        let in_flight = Arc::new(AtomicUsize::new(0));
        let max_in_flight = Arc::new(AtomicUsize::new(0));
        let provider = DelayedProvider {
            identity: StructuredStateProviderIdentity {
                model: "test/model".to_string(),
                prompt_hash: "prompt".to_string(),
                schema_hash: "schema".to_string(),
            },
            calls: Arc::clone(&calls),
            in_flight,
            max_in_flight: Arc::clone(&max_in_flight),
            active_item_counts: Arc::new(Mutex::new(Vec::new())),
        };
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(Arc::new(provider))
        .with_structured_state_prefetch_concurrency(2)
        .with_worker_compile_concurrency(128);
        for (suffix, city) in [
            ("oslo-lane", "Oslo"),
            ("lima-lane", "Lima"),
            ("rome-lane", "Rome"),
            ("paris-lane", "Paris"),
            ("tokyo-lane", "Tokyo"),
            ("cairo-lane", "Cairo"),
        ] {
            let context = bind_test_context(&store, tenant, suffix).await;
            let key = format!("structured:parallel-lane:{suffix}");
            service
                .retain(
                    &context,
                    &key,
                    TrustLevel::TrustedUser,
                    episode_request(
                        &context,
                        format!("structured:parallel-lane:{suffix}"),
                        format!("user: My home city is {city}."),
                    ),
                )
                .await
                .unwrap();
        }

        assert_eq!(service.run_worker_tick(16).await.unwrap().completed, 6);
        assert_eq!(calls.load(Ordering::SeqCst), 6);
        assert_eq!(max_in_flight.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn terminal_predecessor_releases_unprepared_tail_for_redelivery() {
        let first = "user: My home city is Oslo.";
        let quote = first.strip_prefix("user: ").unwrap();
        let operation = StructuredStateOp {
            operation: StructuredStateOperation::Create,
            namespace: "profile".to_string(),
            item_key: "home_city".to_string(),
            target_unit_ids: Vec::new(),
            fields: BTreeMap::from([("value".to_string(), json!("Oslo"))]),
            evidence_quote: quote.to_string(),
            source_span: format!("6-{}", first.len()),
            valid_from: None,
            valid_to: None,
        };
        let provider = Arc::new(RetryProvider {
            identity: StructuredStateProviderIdentity {
                model: "test/model".to_string(),
                prompt_hash: "prompt".to_string(),
                schema_hash: "schema".to_string(),
            },
            responses: Mutex::new(VecDeque::from([
                Err(StructuredStateProviderError::Unavailable(
                    "temporary outage".to_string(),
                )),
                Ok(Vec::new()),
                Ok(Vec::new()),
                Ok(vec![operation]),
            ])),
        });
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "tail-redelivery").await;
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(provider.clone());
        for (index, body) in [first, "user: Tail two.", "user: Tail three."]
            .into_iter()
            .enumerate()
        {
            let key = format!("structured:tail:{index}");
            service
                .retain(
                    &context,
                    &key,
                    TrustLevel::TrustedUser,
                    episode_request(&context, format!("structured:tail:{index}"), body),
                )
                .await
                .unwrap();
        }
        assert_eq!(service.run_worker_tick(16).await.unwrap().completed, 0);
        assert_eq!(provider.responses.lock().unwrap().len(), 3);
        assert_eq!(service.run_worker_tick(16).await.unwrap().completed, 2);
        assert_eq!(provider.responses.lock().unwrap().len(), 1);
        assert_eq!(store.dead_letter_count().await.unwrap(), 1);
    }

    #[tokio::test]
    async fn persisted_completion_closes_crash_window_without_another_provider_call() {
        let body = "user: My home city is Oslo.";
        let quote = body.strip_prefix("user: ").unwrap();
        let operation = StructuredStateOp {
            operation: StructuredStateOperation::Create,
            namespace: "profile".to_string(),
            item_key: "home_city".to_string(),
            target_unit_ids: Vec::new(),
            fields: BTreeMap::from([("value".to_string(), json!("Oslo"))]),
            evidence_quote: quote.to_string(),
            source_span: format!("6-{}", body.len()),
            valid_from: None,
            valid_to: None,
        };
        let identity = || StructuredStateProviderIdentity {
            model: "test/model".to_string(),
            prompt_hash: "prompt".to_string(),
            schema_hash: "schema".to_string(),
        };
        let first_provider = Arc::new(RetryProvider {
            identity: identity(),
            responses: Mutex::new(VecDeque::from([Ok(vec![operation])])),
        });
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "crash-window").await;
        let scope = context.scope_id;
        let first = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(first_provider);
        first
            .retain(
                &context,
                "structured:crash-window",
                TrustLevel::TrustedUser,
                episode_request(&context, "structured:crash-window", body),
            )
            .await
            .unwrap();
        let job = store
            .claim_reflect_jobs(
                JobFilter {
                    tenant: Some(tenant),
                    scope: Some(scope),
                },
                1,
            )
            .await
            .unwrap()
            .pop()
            .unwrap();
        let context = first.resolve_job_context(&job).await.unwrap();
        let prepared = first
            .prepare_structured_state(&job, &context)
            .await
            .unwrap();
        first
            .compile_job(&job, &context, None, Some(&prepared))
            .await
            .unwrap();
        // Simulate a process crash before the legacy separate complete call.
        let second_provider = Arc::new(RetryProvider {
            identity: identity(),
            responses: Mutex::new(VecDeque::from([Err(
                StructuredStateProviderError::Unavailable("must not run".to_string()),
            )])),
        });
        let second = MemoryService::new(
            Arc::new(store),
            Arc::new(FixedClock("2026-07-13T00:01:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(second_provider.clone());
        assert_eq!(second.run_worker_tick(16).await.unwrap().completed, 0);
        assert_eq!(
            second_provider.responses.lock().unwrap().len(),
            1,
            "completed persistence must make the job unclaimable"
        );
    }

    #[tokio::test]
    async fn prepared_observations_refold_against_intervening_active_state_without_provider_call() {
        let oslo_body = "user: My home city is Oslo.";
        let lima_body = "user: My home city is Lima.";
        let operation = |body: &str, value: &str| StructuredStateOp {
            operation: StructuredStateOperation::Create,
            namespace: "profile".to_string(),
            item_key: "home_city".to_string(),
            target_unit_ids: Vec::new(),
            fields: BTreeMap::from([("value".to_string(), json!(value))]),
            evidence_quote: body.strip_prefix("user: ").unwrap().to_string(),
            source_span: format!("6-{}", body.len()),
            valid_from: None,
            valid_to: None,
        };
        let identity = || StructuredStateProviderIdentity {
            model: "test/recovery".to_string(),
            prompt_hash: "prompt".to_string(),
            schema_hash: "schema".to_string(),
        };
        let provider = Arc::new(RetryProvider {
            identity: identity(),
            responses: Mutex::new(VecDeque::from([
                Ok(vec![operation(oslo_body, "Oslo")]),
                Ok(vec![operation(lima_body, "Lima")]),
            ])),
        });
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "prepared-refold").await;
        let first = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(provider);
        first
            .retain(
                &context,
                "prepared-refold-oslo",
                TrustLevel::TrustedUser,
                episode_request(&context, "prepared-refold-oslo", oslo_body),
            )
            .await
            .unwrap();
        assert_eq!(first.run_worker_tick(1).await.unwrap().completed, 1);
        first
            .retain(
                &context,
                "prepared-refold-lima",
                TrustLevel::TrustedUser,
                episode_request(&context, "prepared-refold-lima", lima_body),
            )
            .await
            .unwrap();
        let claim = store
            .claim_reflect_jobs(
                JobFilter {
                    tenant: Some(tenant),
                    scope: Some(context.scope_id),
                },
                1,
            )
            .await
            .unwrap()
            .pop()
            .unwrap();
        let resolved = first.resolve_job_context(&claim).await.unwrap();
        first
            .prepare_structured_state(&claim, &resolved)
            .await
            .unwrap();
        store
            .release_reflect_job(&claim, 0, "simulated crash".to_string())
            .await
            .unwrap();

        let oslo = store
            .memory_units(tenant)
            .into_iter()
            .find(|unit| {
                unit.state == memphant_types::UnitState::Active
                    && unit.valid_to.is_none()
                    && unit.body == "profile item home_city: {\"value\":\"Oslo\"}"
            })
            .unwrap();
        first
            .correct(
                &context,
                "prepared-refold-intervening-correction",
                CorrectRequest {
                    subject_id: context.data_subject_id,
                    scope_id: context.scope_id,
                    actor_id: context.actor_id,
                    agent_node_id: context.agent_node_id,
                    subject_generation: context.subject_generation,
                    selector: memphant_types::CorrectSelector {
                        memory_unit_id: oslo.id,
                    },
                    correction: CorrectionPayload {
                        value: "profile item home_city: {\"value\":\"Rome\"}".to_string(),
                        reason: "intervening state".to_string(),
                        source_ref: "test:prepared-refold-intervening".to_string(),
                        observed_at: "2026-07-13T00:01:00Z".to_string(),
                        valid_from: None,
                        valid_to: None,
                    },
                },
            )
            .await
            .unwrap();

        let replay_provider = Arc::new(RetryProvider {
            identity: identity(),
            responses: Mutex::new(VecDeque::from([Err(
                StructuredStateProviderError::Unavailable("must not extract again".to_string()),
            )])),
        });
        let replay = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:02:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(replay_provider.clone());
        assert_eq!(replay.run_worker_tick(1).await.unwrap().completed, 1);
        assert_eq!(replay_provider.responses.lock().unwrap().len(), 1);
        let current = store
            .memory_units(tenant)
            .into_iter()
            .filter(|unit| {
                unit.state == memphant_types::UnitState::Active
                    && unit.valid_to.is_none()
                    && unit
                        .fact_key
                        .as_deref()
                        .is_some_and(|key| key.ends_with(":profile:home_city"))
            })
            .collect::<Vec<_>>();
        assert_eq!(current.len(), 1);
        assert_eq!(
            current[0].body,
            "profile item home_city: {\"value\":\"Lima\"}"
        );
    }

    #[tokio::test]
    async fn worker_requeues_stale_compiler_identity_before_extraction() {
        let body = "user: My home city is Lima.";
        let operation = StructuredStateOp {
            operation: StructuredStateOperation::Create,
            namespace: "profile".to_string(),
            item_key: "home_city".to_string(),
            target_unit_ids: Vec::new(),
            fields: BTreeMap::from([("value".to_string(), json!("Lima"))]),
            evidence_quote: "My home city is Lima.".to_string(),
            source_span: format!("6-{}", body.len()),
            valid_from: None,
            valid_to: None,
        };
        let provider_a = Arc::new(RetryProvider {
            identity: StructuredStateProviderIdentity {
                model: "test/provider-a".to_string(),
                prompt_hash: "prompt-a".to_string(),
                schema_hash: "schema".to_string(),
            },
            responses: Mutex::new(VecDeque::from([Err(
                StructuredStateProviderError::Unavailable("A must not run".to_string()),
            )])),
        });
        let provider_b = Arc::new(RetryProvider {
            identity: StructuredStateProviderIdentity {
                model: "test/provider-b".to_string(),
                prompt_hash: "prompt-b".to_string(),
                schema_hash: "schema".to_string(),
            },
            responses: Mutex::new(VecDeque::from([Ok(vec![operation])])),
        });
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let context = bind_test_context(&store, tenant, "identity-drift").await;
        let enqueue = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:00:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(provider_a.clone());
        enqueue
            .retain(
                &context,
                "identity-drift",
                TrustLevel::TrustedUser,
                episode_request(&context, "identity-drift", body),
            )
            .await
            .unwrap();
        let compiler_a = store.reflect_jobs(tenant)[0].compiler_version.clone();

        let worker = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-13T00:01:00Z")),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(provider_b.clone());
        assert_eq!(worker.run_worker_tick(1).await.unwrap().completed, 0);
        assert_eq!(provider_a.responses.lock().unwrap().len(), 1);
        assert_eq!(provider_b.responses.lock().unwrap().len(), 1);
        assert!(
            store
                .reflect_traces(tenant)
                .iter()
                .all(|trace| trace.compiler_version != compiler_a)
        );

        assert_eq!(worker.run_worker_tick(1).await.unwrap().completed, 1);
        assert!(provider_b.responses.lock().unwrap().is_empty());
        let compiler_b =
            service_structured_compiler_identity(COMPILER_VERSION, provider_b.as_ref());
        let traces = store.reflect_traces(tenant);
        assert_eq!(traces.len(), 1);
        assert_eq!(traces[0].compiler_version, compiler_b);
    }
}

impl From<StoreError> for ServiceError {
    fn from(error: StoreError) -> Self {
        Self::Core(CoreError::Store(error))
    }
}

fn structured_projection_kind(namespace: &str) -> Option<MemoryKind> {
    matches!(namespace, "workflow" | "procedure" | "gotcha").then_some(MemoryKind::Procedural)
}

fn service_structured_compiler_identity(
    compiler: &str,
    provider: &dyn StructuredStateProvider,
) -> String {
    structured_compiler_identity(
        &format!("{compiler}+source-slice-greedy-batches-v2"),
        provider.identity(),
    )
}

fn serialized_mutation_response(
    status: u16,
    value: &impl serde::Serialize,
) -> Result<MutationResponse, ServiceError> {
    let body = serde_json::to_vec(value).map_err(|error| {
        CoreError::Store(StoreError::Backend(format!(
            "mutation response serialization failed: {error}"
        )))
    })?;
    Ok(MutationResponse::success(status, body)?)
}

#[cfg(test)]
mod retain_atomicity_tests {
    use super::*;
    use crate::{FixedClock, InMemoryStore, NoopEmbedding};
    use memphant_types::{
        ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest,
        ContextBindingScopeRef, RetainEpisodePayload, RetainPayload,
    };

    #[tokio::test]
    async fn retain_rolls_back_source_and_job_when_receipt_staging_fails() {
        let store = InMemoryStore::default();
        let tenant = TenantId::new();
        let binding = store
            .resolve_context_binding(
                tenant,
                "retain-atomicity".to_string(),
                ContextBindingRequest {
                    subject: ContextBindingEntityRef {
                        external_ref: "subject:atomicity".to_string(),
                        kind: "user".to_string(),
                    },
                    actor: ContextBindingEntityRef {
                        external_ref: "actor:atomicity".to_string(),
                        kind: "user".to_string(),
                    },
                    scope: ContextBindingScopeRef {
                        external_ref: "scope:atomicity".to_string(),
                        kind: "memory".to_string(),
                        parent_external_ref: None,
                    },
                    agent_node: ContextBindingAgentRef {
                        external_ref: "agent:atomicity".to_string(),
                        parent_external_ref: None,
                    },
                    access_policies: Vec::new(),
                },
            )
            .await
            .unwrap();
        let context = store
            .resolve_memory_context(
                tenant,
                binding.subject_id,
                binding.actor_id,
                binding.scope_id,
                binding.agent_node_id,
            )
            .await
            .unwrap();
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(FixedClock("2026-07-15T00:00:00Z")),
            Arc::new(NoopEmbedding),
        );
        let request = RetainEpisodeHttpRequest {
            subject_id: context.data_subject_id,
            scope_id: context.scope_id,
            actor_id: context.actor_id,
            agent_node_id: context.agent_node_id,
            subject_generation: context.subject_generation,
            source_ref: "test:atomicity".to_string(),
            observed_at: "2026-07-15T00:00:00Z".to_string(),
            payload: RetainPayload::Episode(RetainEpisodePayload {
                source_kind: "user".to_string(),
                body: "an atomic retained episode".to_string(),
                subject: None,
                predicate: None,
            }),
        };

        store.fail_next_mutation_response();
        service
            .retain(
                &context,
                "retain-atomicity",
                TrustLevel::TrustedUser,
                request.clone(),
            )
            .await
            .unwrap_err();
        assert!(store.episodes(tenant).is_empty());
        assert!(store.reflect_jobs(tenant).is_empty());

        service
            .retain(
                &context,
                "retain-atomicity",
                TrustLevel::TrustedUser,
                request,
            )
            .await
            .unwrap();
        assert_eq!(store.episodes(tenant).len(), 1);
        assert_eq!(store.reflect_jobs(tenant).len(), 1);
    }
}

/// Comparable trust ranking (higher = more trusted). Used to clamp
/// caller-declared trust at the API key's `max_trust` ceiling — trust is
/// provenance-derived, never forgeable.
pub fn trust_rank(level: TrustLevel) -> u8 {
    match level {
        TrustLevel::TrustedSystem => 7,
        TrustLevel::TrustedUser => 6,
        TrustLevel::VerifiedTool => 5,
        TrustLevel::UnverifiedTool => 4,
        TrustLevel::WebContent => 3,
        TrustLevel::ImportedExternal => 2,
        TrustLevel::AgentOutput => 1,
        TrustLevel::Quarantined => 0,
    }
}

/// `min(declared, ceiling)` on the trust lattice.
pub fn clamp_trust(declared: TrustLevel, ceiling: TrustLevel) -> TrustLevel {
    if trust_rank(declared) > trust_rank(ceiling) {
        ceiling
    } else {
        declared
    }
}

pub struct MemoryService<S: MemoryStore> {
    store: Arc<S>,
    clock: Arc<dyn Clock>,
    embedder: Arc<dyn EmbeddingProvider>,
    /// Rung 4 write-time toggle: when set, the reflect-stage compile mints
    /// per-episode contextual chunks (§`compile_job`). DEFAULT TRUE (promoted
    /// 2026-07-10): the paired ablation through THIS runtime path cleared —
    /// LME-S n=100 seed 20260710 session+runtime-chunks (shipped code incl.
    /// reclaim) vs session baseline: ΔQA +0.110 [+0.020, +0.190], ΔR@5 +0.117
    /// [+0.053, +0.191], ΔR@10 +0.117 [+0.053, +0.191] (all 95% CIs exclude
    /// zero; reader gpt-5.6-terra@medium, judge claude-sonnet-5, 1000-resample
    /// paired bootstrap). Proof:
    /// `docs/build-log/artifacts/real-retrieval-20260710/scaled-reader-or-session-chunkpack-rerank-off.json`
    /// and `scaled-lme-s-session-chunkpack-rerank-off.json`. The
    /// `with_contextual_chunks_write_enabled(false)` builder stays so ablations
    /// can force the chunks-off control arm.
    contextual_chunks_write_enabled: bool,
    /// R1 docs-domain twin of `contextual_chunks_write_enabled`: when set, the
    /// reflect-stage compile of a `kind=document` resource mints per-resource
    /// contextual chunks (§`compile_job` `ReflectResource` arm) via
    /// `resource_contextual_chunks` — the SAME rung-4 machinery episodes use,
    /// extended to the docs domain. DEFAULT FALSE (flag-gated until an R1-T4
    /// promotion): shipped behavior is byte-identical to today (whole-section
    /// units, no chunks). Non-document resource kinds are never chunked. Set via
    /// `with_resource_chunks_write_enabled` (the gate's `--resource-chunks` /
    /// `MEMPHANT_RESOURCE_CHUNKS` thread it through the runtime).
    resource_chunks_write_enabled: bool,
    /// R1.5-T0 recall-pool-depth knob: the ONE knob every internal
    /// channel/fusion limit in the recall path derives from — vector-channel
    /// KNN fan-out, packing scan window, rerank rescoring cap. DEFAULT
    /// `DEFAULT_RECALL_POOL_DEPTH` (64). Raised via `with_recall_pool_depth`
    /// (also lets the W8 cross-encoder rerank arm rerank a widened pool — no
    /// wire change). See the pool-mapping note on `DEFAULT_RECALL_POOL_DEPTH`.
    recall_pool_depth: usize,
    /// Packing levers (session-diversity quota, render cap, submodular
    /// ordering), threaded construction-time like `recall_pool_depth`. ALL
    /// DEFAULT OFF: they ship default-on only after the accuracy-wave
    /// measurement campaign, so the bench needs the flags. Set via
    /// `with_session_quota` and friends.
    pack_levers: PackLevers,
    /// Which lexical scorer the fusion's lexical family uses, threaded
    /// construction-time like `pack_levers`. `LexicalScorer::Overlap` is the
    /// DEFAULT and leaves fusion byte-identical; the BM25 variants are selected
    /// by `with_lexical_scorer` (runtime: `MEMPHANT_LEXICAL_SCORER`).
    lexical_scorer: LexicalScorer,
    /// W5 temporal-grounding toggle (DEFAULT OFF). Gates all three temporal
    /// behaviours together: reflect-stage content-date grounding of `valid_from`
    /// and dated contextual-chunk headers (`compile_job`), query-date windowing
    /// at recall, and date-prefixed packed items (recall). Off means every one of
    /// those paths is byte-identical to today. Promotion is measurement-only, so
    /// it ships off and the bench threads it via `with_temporal_grounding_enabled`.
    temporal_grounding_enabled: bool,
    /// W6 deterministic fact-extraction toggle (DEFAULT OFF). When on, the
    /// reflect-stage compile of an EPISODE mines its user turns for first-person
    /// preference/attribute statements and emits extra short, honest-subject-key
    /// ReflectCandidates alongside the raw episode unit (`compile_job`). Off means
    /// the compile is byte-identical to today (only the raw episode candidate).
    /// Independent of `temporal_grounding_enabled`: the two only interact so that
    /// a mined fact body is `[date ...]`-prefixed when BOTH are on and the body
    /// carries a parseable content date. Measurement-only promotion, so it ships
    /// off and the bench threads it via `with_fact_extraction_enabled`.
    fact_extraction_enabled: bool,
    /// W8 cross-encoder rerank seam (DEFAULT `None`). When set, recall reorders
    /// the top `recall_pool_depth` fused candidates by a real `(query, body)`
    /// cross-encoder AFTER fusion and BEFORE packing — the widened-pool rerank
    /// arm. `None` leaves recall byte-identical to today. Independent of the
    /// retired heuristic rerank stage. Set via `with_cross_reranker`; the bench
    /// lane's `--cross-rerank` threads the real fastembed reranker here.
    cross_reranker: Option<Arc<dyn CrossReranker>>,
    cross_rerank_candidate_selection: CrossRerankCandidateSelection,
    cross_rerank_granularity: CrossRerankGranularity,
    structured_state_provider: Option<Arc<dyn StructuredStateProvider>>,
    structured_state_prefetch_concurrency: usize,
    worker_compile_concurrency: usize,
    deep_recall_provider: Option<Arc<dyn DeepRecallProvider>>,
}

impl<S: MemoryStore> Clone for MemoryService<S> {
    fn clone(&self) -> Self {
        Self {
            store: Arc::clone(&self.store),
            clock: Arc::clone(&self.clock),
            embedder: Arc::clone(&self.embedder),
            contextual_chunks_write_enabled: self.contextual_chunks_write_enabled,
            resource_chunks_write_enabled: self.resource_chunks_write_enabled,
            recall_pool_depth: self.recall_pool_depth,
            pack_levers: self.pack_levers,
            lexical_scorer: self.lexical_scorer,
            temporal_grounding_enabled: self.temporal_grounding_enabled,
            fact_extraction_enabled: self.fact_extraction_enabled,
            cross_reranker: self.cross_reranker.clone(),
            cross_rerank_candidate_selection: self.cross_rerank_candidate_selection,
            cross_rerank_granularity: self.cross_rerank_granularity,
            structured_state_provider: self.structured_state_provider.clone(),
            structured_state_prefetch_concurrency: self.structured_state_prefetch_concurrency,
            worker_compile_concurrency: self.worker_compile_concurrency,
            deep_recall_provider: self.deep_recall_provider.clone(),
        }
    }
}

impl<S: MemoryStore> MemoryService<S> {
    pub fn new(store: Arc<S>, clock: Arc<dyn Clock>, embedder: Arc<dyn EmbeddingProvider>) -> Self {
        Self {
            store,
            clock,
            embedder,
            contextual_chunks_write_enabled: true,
            resource_chunks_write_enabled: false,
            recall_pool_depth: DEFAULT_RECALL_POOL_DEPTH,
            pack_levers: PackLevers::default(),
            lexical_scorer: LexicalScorer::default(),
            temporal_grounding_enabled: false,
            fact_extraction_enabled: false,
            cross_reranker: None,
            cross_rerank_candidate_selection: CrossRerankCandidateSelection::FusedHead,
            cross_rerank_granularity: CrossRerankGranularity::UnitBody,
            structured_state_provider: None,
            structured_state_prefetch_concurrency: DEFAULT_STRUCTURED_STATE_PREFETCH_CONCURRENCY,
            worker_compile_concurrency: DEFAULT_WORKER_COMPILE_CONCURRENCY,
            deep_recall_provider: None,
        }
    }

    /// Overrides the rung 4 contextual-chunk write path (default on since the
    /// 2026-07-10 promotion). A builder override so ablations can force the
    /// control arm: the bench lane's `--disable runtime_chunks` passes `false`
    /// here to run the chunk-free baseline (old behavior).
    pub fn with_contextual_chunks_write_enabled(mut self, enabled: bool) -> Self {
        self.contextual_chunks_write_enabled = enabled;
        self
    }

    /// Overrides the R1 docs-domain resource-chunk write path (default OFF).
    /// When enabled, the reflect-stage compile of a `kind=document` resource
    /// mints per-resource contextual chunks (the docs twin of the episode
    /// chunk path). Construction-time only, mirroring
    /// `with_contextual_chunks_write_enabled`: no recall-request/wire change.
    /// The runtime threads `MEMPHANT_RESOURCE_CHUNKS` here so the gate's
    /// `--resource-chunks` reaches both the server and worker.
    pub fn with_resource_chunks_write_enabled(mut self, enabled: bool) -> Self {
        self.resource_chunks_write_enabled = enabled;
        self
    }

    /// Overrides the recall pool depth (default `DEFAULT_RECALL_POOL_DEPTH`,
    /// 64) — R1.5-T0's ONE knob every internal channel/fusion limit in the
    /// recall path derives from (vector-channel KNN fan-out, packing scan
    /// window, rerank rescoring cap), NEVER `k`; the bench lane's `--pool <n>`
    /// and the runtime's `MEMPHANT_RECALL_POOL_DEPTH` env override both thread
    /// their value here. Construction-time only, mirroring
    /// `with_contextual_chunks_write_enabled`: no recall-request/wire field
    /// changes.
    pub fn with_recall_pool_depth(mut self, depth: usize) -> Self {
        self.recall_pool_depth = depth;
        self
    }

    /// Selects the fusion's lexical scorer (default [`LexicalScorer::Overlap`],
    /// byte-identical to today). Threaded construction-time exactly like
    /// `with_recall_pool_depth`: no recall-request or wire change.
    pub fn with_lexical_scorer(mut self, scorer: LexicalScorer) -> Self {
        self.lexical_scorer = scorer;
        self
    }

    /// Sets the W4 per-`source_episode_id` diversity quota (default OFF = `None`).
    /// `Some(cap)` caps admissions per session during the greedy fill until every
    /// distinct episode has had a look-in, then fills remaining budget
    /// unrestricted (work-conserving). `DEFAULT_SESSION_DIVERSITY_QUOTA` (2) is
    /// the recommended value. Construction-time only; the bench lane's
    /// `--session-quota <n>` threads its value here.
    pub fn with_session_quota(mut self, quota: Option<usize>) -> Self {
        self.pack_levers.session_quota = quota;
        self
    }

    /// Sets the rung-7 per-item render cap (default OFF = `None`). `Some(cap)`
    /// bounds each packed item's chunk-render budget at `cap` tokens so a large
    /// chunk-matched body cannot refill to nearly its whole self and hog the pack
    /// budget (2026-07-21-rung7-packing-diagnosis.md). Construction-time only;
    /// the bench lane's `--pack-render-cap <n>` threads its value here.
    pub fn with_pack_render_cap(mut self, cap: Option<usize>) -> Self {
        self.pack_levers.pack_render_cap = cap;
        self
    }

    /// Enables P-2 merged chunk-block rendering (default OFF): a contiguous run
    /// of selected chunks renders as one block under a single run-spanning
    /// provenance header instead of one header per chunk. Construction-time
    /// only; the bench lane's `--merge-chunk-blocks` threads its value here.
    pub fn with_merge_chunk_blocks(mut self, enabled: bool) -> Self {
        self.pack_levers.merge_chunk_blocks = enabled;
        self
    }

    /// Enables budgeted submodular evidence ordering (default OFF).
    pub fn with_pack_submodular_ordering_enabled(mut self, enabled: bool) -> Self {
        self.pack_levers.submodular_ordering_enabled = enabled;
        self
    }

    /// Enables W5 temporal grounding (default OFF): reflect-stage content-date
    /// grounding of `valid_from` + dated chunk headers, query-date windowing at
    /// recall, and `[date ...]`-prefixed packed items. Construction-time only,
    /// mirroring the W3/W4 knobs; the bench lane's `--temporal-grounding` threads
    /// its value here. Off ⇒ all three paths behave exactly as today.
    pub fn with_temporal_grounding_enabled(mut self, enabled: bool) -> Self {
        self.temporal_grounding_enabled = enabled;
        self
    }

    /// Enables W6 deterministic fact extraction (default OFF): the reflect-stage
    /// episode compile mines user turns for preference/attribute statements and
    /// emits extra short, honest-subject-key ReflectCandidates. Construction-time
    /// only, mirroring the W3/W4/W5 knobs; the bench lane's `--fact-extraction`
    /// threads its value here. Off ⇒ the compile is byte-identical to today.
    pub fn with_fact_extraction_enabled(mut self, enabled: bool) -> Self {
        self.fact_extraction_enabled = enabled;
        self
    }

    /// Installs the W8 cross-encoder rerank seam (default `None`). When set,
    /// recall reorders the top `recall_pool_depth` fused candidates by this
    /// reranker's `(query, body)` scores AFTER fusion and BEFORE packing.
    /// Construction-time only, mirroring the W3/W4/W5 knobs; the bench lane's
    /// `--cross-rerank` threads the real fastembed reranker here. Unset ⇒ recall
    /// is byte-identical to today. Independent of the retired heuristic rerank.
    pub fn with_cross_reranker(mut self, reranker: Arc<dyn CrossReranker>) -> Self {
        self.cross_reranker = Some(reranker);
        self
    }

    pub fn with_cross_rerank_candidate_selection(
        mut self,
        selection: CrossRerankCandidateSelection,
    ) -> Self {
        self.cross_rerank_candidate_selection = selection;
        self
    }

    /// Sets the W8 cross-rerank doc granularity (default `UnitBody` =
    /// today's behavior). `ContextualChunks` feeds each head candidate's
    /// flattened `contextual_chunks` bodies to the reranker (fallback: the
    /// unit body when a candidate has no chunks) and max-pools the scores
    /// back to one score per candidate. Construction-time only, mirroring
    /// `with_cross_rerank_candidate_selection`; the runtime threads
    /// `MEMPHANT_RERANK_GRANULARITY` here and the bench lane
    /// `--rerank-granularity`. Inert unless a cross-reranker is installed.
    pub fn with_cross_rerank_granularity(mut self, granularity: CrossRerankGranularity) -> Self {
        self.cross_rerank_granularity = granularity;
        self
    }

    /// Installs structured-state extraction for episode and resource reflection.
    /// Provider output is validated against exact source evidence before it
    /// reaches the existing admission and bitemporal storage path.
    pub fn with_structured_state_provider(
        mut self,
        provider: Arc<dyn StructuredStateProvider>,
    ) -> Self {
        self.structured_state_provider = Some(provider);
        self
    }

    pub fn with_structured_state_prefetch_concurrency(mut self, concurrency: usize) -> Self {
        self.structured_state_prefetch_concurrency =
            concurrency.clamp(1, MAX_STRUCTURED_STATE_PREFETCH_CONCURRENCY);
        self
    }

    /// Sets the maximum number of independent scope lanes compiled in parallel
    /// when no structured-state provider is installed. Provider-bearing work
    /// always uses the separately bounded prefetch concurrency so increasing
    /// local throughput cannot increase remote calls or spend.
    pub fn with_worker_compile_concurrency(mut self, concurrency: usize) -> Self {
        self.worker_compile_concurrency = concurrency.clamp(1, MAX_WORKER_COMPILE_CONCURRENCY);
        self
    }

    pub fn with_deep_recall_provider(mut self, provider: Arc<dyn DeepRecallProvider>) -> Self {
        self.deep_recall_provider = Some(provider);
        self
    }

    pub fn store(&self) -> &S {
        &self.store
    }

    pub fn embedder(&self) -> &dyn EmbeddingProvider {
        self.embedder.as_ref()
    }

    /// The retain verb. Identity comes from the resolved context and source
    /// trust is assigned by the authenticated edge.
    pub async fn retain(
        &self,
        context: &ResolvedMemoryContext,
        idempotency_key: &str,
        assigned_trust: TrustLevel,
        mut request: RetainEpisodeHttpRequest,
    ) -> Result<MutationResponse, ServiceError>
    where
        S: MutationLedgerStore,
    {
        if (
            request.subject_id,
            request.scope_id,
            request.actor_id,
            request.agent_node_id,
            request.subject_generation,
        ) != (
            context.data_subject_id,
            context.scope_id,
            context.actor_id,
            context.agent_node_id,
            context.subject_generation,
        ) {
            return Err(ServiceError::Invalid(
                "retain request identity must match the resolved context".to_string(),
            ));
        }
        if request.source_ref.trim().is_empty() {
            return Err(ServiceError::Invalid(
                "source_ref must not be blank".to_string(),
            ));
        }
        request.observed_at = canonical_utc_timestamp(&request.observed_at, "observed_at")?;
        let compiler_version = COMPILER_VERSION.to_string();
        match &request.payload {
            memphant_types::RetainPayload::Episode(episode) => {
                if !matches!(
                    episode.source_kind.as_str(),
                    "user" | "agent" | "tool" | "web" | "resource" | "system"
                ) {
                    return Err(ServiceError::Invalid(
                        "episode source_kind must be one of user, agent, tool, web, resource, system"
                            .to_string(),
                    ));
                }
                if episode.body.trim().is_empty() {
                    return Err(CoreError::EmptyBody.into());
                }
            }
            memphant_types::RetainPayload::Unit(unit) => {
                if unit.body.trim().is_empty() {
                    return Err(CoreError::EmptyBody.into());
                }
                // A direct unit write must still name its subject — that is what
                // makes it supersedable. It may do so as a pre-composed
                // `fact_key` (the pre-D1 shape) or as a plain `subject` the
                // server keys with `derive_fact_key`. Neither is accepted blank.
                let keyed = non_blank(unit.fact_key.as_deref()).is_some()
                    || non_blank(unit.subject.as_deref()).is_some();
                if !keyed || unit.predicate.trim().is_empty() {
                    return Err(ServiceError::Invalid(
                        "unit retain requires a predicate and either a subject or a fact_key"
                            .to_string(),
                    ));
                }
                if !unit.confidence.is_finite() || !(0.0..=1.0).contains(&unit.confidence) {
                    return Err(ServiceError::Invalid(
                        "unit confidence must be finite and between 0 and 1".to_string(),
                    ));
                }
                validate_valid_interval(unit.valid_from.as_deref(), unit.valid_to.as_deref())?;
            }
            memphant_types::RetainPayload::Resource(_) => {}
        }
        let claim = MutationClaim::new(
            context,
            MutationVerb::Retain,
            idempotency_key,
            canonical_mutation_request_hash(MutationVerb::Retain, &request)?,
        )?;
        let source_ref = request.source_ref;
        let observed_at = request.observed_at;
        match request.payload {
            memphant_types::RetainPayload::Resource(resource) => {
                let compiler_version = self
                    .structured_state_provider
                    .as_ref()
                    .map(|provider| {
                        service_structured_compiler_identity(&compiler_version, provider.as_ref())
                    })
                    .unwrap_or(compiler_version);
                let mut tx = self.store.begin(context).await?;
                match self.store.stage_mutation_claim(&mut tx, claim).await? {
                    MutationClaimOutcome::Replay(response) => {
                        self.store.commit(tx).await?;
                        return Ok(response);
                    }
                    MutationClaimOutcome::Execute => {}
                }
                let resource_id = self
                    .store
                    .stage_resource(
                        &mut tx,
                        NewResource {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            uri: resource.uri,
                            source_ref: source_ref.clone(),
                            observed_at,
                            kind: resource.kind.unwrap_or_default(),
                            content_hash: resource.content_hash,
                            mime_type: resource.mime_type,
                            revision: resource.revision,
                            body: resource.body,
                            source_trust: assigned_trust,
                            acl: memphant_types::ResourceAcl::default(),
                        },
                    )
                    .await?;
                self.store
                    .enqueue_reflect(
                        &mut tx,
                        ReflectJob {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            episode_id: None,
                            resource_id: Some(resource_id),
                            kind: ReflectJobKind::ReflectResource,
                            compiler_version,
                            subject: None,
                            predicate: None,
                        },
                    )
                    .await?;
                let result = RetainEpisodeHttpResponse {
                    episode_id: None,
                    resource_id: Some(resource_id),
                    unit_ids: Vec::new(),
                    dedup: None,
                    assigned_trust: Some(assigned_trust),
                    enqueued: vec!["reflect_resource".to_string()],
                    trace_ref: None,
                };
                let response = serialized_mutation_response(200, &result)?;
                self.store
                    .stage_mutation_response(&mut tx, response.clone())
                    .await?;
                self.store.commit(tx).await?;
                Ok(response)
            }
            memphant_types::RetainPayload::Unit(unit) => {
                let mut probe = self.store.begin(context).await?;
                match self
                    .store
                    .stage_mutation_claim(&mut probe, claim.clone())
                    .await?
                {
                    MutationClaimOutcome::Replay(response) => {
                        self.store.commit(probe).await?;
                        return Ok(response);
                    }
                    MutationClaimOutcome::Execute => self.store.rollback(probe).await?,
                }
                let job_id = memphant_types::JobId::new();
                let prepared = prepare_compiled_write_owned(
                    self.store.as_ref(),
                    ReflectInput {
                        tenant_id: context.tenant_id,
                        data_subject_id: context.data_subject_id,
                        scope_id: context.scope_id,
                        agent_node_id: context.agent_node_id,
                        subject_generation: context.subject_generation,
                        actor_id: context.actor_id,
                        source_ref: source_ref.clone(),
                        observed_at,
                        source_body: Some(unit.body.clone()),
                        episode_id: None,
                        resource_id: None,
                        job_id,
                        compiler_version,
                        candidates: vec![ReflectCandidate {
                            source_kind: "direct".to_string(),
                            trust_level: assigned_trust,
                            actor_id: context.actor_id,
                            // D1: pass the caller's subject through instead of
                            // dropping it. `fact_key`, when the caller composed
                            // one itself, still wins — `prepare_compiled_write`
                            // only reaches `derive_fact_key` when it is absent,
                            // so pre-D1 callers are unaffected.
                            subject: non_blank(unit.subject.as_deref()),
                            predicate: Some(unit.predicate.clone()),
                            fact_key: non_blank(unit.fact_key.as_deref()),
                            kind: Some(unit.kind),
                            body: unit.body,
                            confidence: Some(unit.confidence),
                            churn_class: None,
                            admission_hint: None,
                            target_unit_ids: unit.target_unit_ids.clone(),
                            contextual_chunks: Vec::new(),
                            valid_from: unit.valid_from.clone(),
                            valid_to: unit.valid_to.clone(),
                        }],
                    },
                    Arc::clone(&self.embedder),
                    self.clock.as_ref(),
                    Some(context),
                )
                .await?;
                let PreparedCompiledWrite::Write {
                    trace,
                    created_unit_ids: unit_ids,
                    write,
                    ..
                } = prepared
                else {
                    return Err(CoreError::Store(StoreError::Conflict(
                        "new direct retain unexpectedly matched an existing reflect trace"
                            .to_string(),
                    ))
                    .into());
                };
                let mut tx = self.store.begin(context).await?;
                match self.store.stage_mutation_claim(&mut tx, claim).await? {
                    MutationClaimOutcome::Replay(response) => {
                        self.store.commit(tx).await?;
                        return Ok(response);
                    }
                    MutationClaimOutcome::Execute => {}
                }
                self.store
                    .stage_compiled_units(&mut tx, None, write)
                    .await?;
                let result = RetainEpisodeHttpResponse {
                    episode_id: None,
                    resource_id: None,
                    unit_ids,
                    dedup: None,
                    assigned_trust: Some(assigned_trust),
                    enqueued: Vec::new(),
                    trace_ref: Some(format!("memphant://trace/{}", trace.job_id.as_uuid())),
                };
                let response = serialized_mutation_response(200, &result)?;
                self.store
                    .stage_mutation_response(&mut tx, response.clone())
                    .await?;
                self.store.commit(tx).await?;
                Ok(response)
            }
            memphant_types::RetainPayload::Episode(episode) => {
                let compiler_version = self
                    .structured_state_provider
                    .as_ref()
                    .map(|provider| {
                        service_structured_compiler_identity(&compiler_version, provider.as_ref())
                    })
                    .unwrap_or(compiler_version);
                let mut tx = self.store.begin(context).await?;
                match self.store.stage_mutation_claim(&mut tx, claim).await? {
                    MutationClaimOutcome::Replay(response) => {
                        self.store.commit(tx).await?;
                        return Ok(response);
                    }
                    MutationClaimOutcome::Execute => {}
                }
                let dedup_key =
                    derive_episode_dedup_key(&episode.source_kind, &source_ref, &episode.body);
                let outcome = self
                    .store
                    .stage_episode(
                        &mut tx,
                        NewEpisode {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            source_kind: episode.source_kind,
                            source_ref,
                            observed_at,
                            source_trust: assigned_trust,
                            dedup_key,
                            body: episode.body,
                        },
                    )
                    .await?;
                self.store
                    .enqueue_reflect(
                        &mut tx,
                        ReflectJob {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            episode_id: Some(outcome.episode_id),
                            resource_id: None,
                            kind: ReflectJobKind::ReflectEpisode,
                            compiler_version,
                            // D1: the caller's own subject/predicate, carried
                            // to reflect stage 1 where the candidate is minted.
                            // `ReflectJob` has always had these fields and the
                            // episodic candidate has always read them; only the
                            // public payload was missing a way to fill them.
                            // Both `None` reproduces the pre-D1 auto key.
                            subject: non_blank(episode.subject.as_deref()),
                            predicate: non_blank(episode.predicate.as_deref()),
                        },
                    )
                    .await?;
                let result = RetainEpisodeHttpResponse {
                    episode_id: Some(outcome.episode_id),
                    resource_id: None,
                    unit_ids: Vec::new(),
                    dedup: Some(outcome.dedup),
                    assigned_trust: Some(assigned_trust),
                    enqueued: vec!["reflect_episode".to_string()],
                    trace_ref: None,
                };
                let response = serialized_mutation_response(200, &result)?;
                self.store
                    .stage_mutation_response(&mut tx, response.clone())
                    .await?;
                self.store.commit(tx).await?;
                Ok(response)
            }
        }
    }

    /// The recall verb with the read-your-own-writes degraded fallback: when
    /// no units match AND the scope has pending reflect jobs, raw episode
    /// bodies are matched and returned with `degraded: true` (spec 08 §4).
    pub async fn recall(
        &self,
        context: ResolvedMemoryContext,
        request: RecallHttpRequest,
    ) -> Result<RecallResponse, ServiceError> {
        // Defensive ceilings on caller-supplied sizing, for symmetry with the
        // scope endpoint's clamp. No allocation is driven by these (output is
        // bounded by the candidate pool), so they only reject absurd values.
        const MAX_RECALL_LIMIT: usize = 1_000;
        const MAX_RECALL_BUDGET_TOKENS: usize = 1_000_000;
        let k = request.limit.unwrap_or(8).clamp(1, MAX_RECALL_LIMIT);
        self.recall_internal(RecallRequest {
            context,
            query: request.query,
            k,
            budget_tokens: request
                .budget_tokens
                .unwrap_or(512)
                .clamp(1, MAX_RECALL_BUDGET_TOKENS),
            mode: request.mode.unwrap_or(RecallMode::Fast),
            include_beliefs: request.include_beliefs.unwrap_or(false),
            edge_expansion_enabled: false,
            context_packing_abstention_enabled: true,
            // Real-evidence default (rung 8 disable-when, real-retrieval-20260710):
            // the deterministic reranker cost -0.143 Recall@5 on LongMemEval-S
            // (CI excludes zero), so it is opt-in until a variant earns its keep.
            procedure_recall_enabled: true,
            decay_enabled: true,
            engine_version: ENGINE_VERSION.to_string(),
            transaction_as_of: request.transaction_as_of,
            valid_at: request.valid_at,
            aggregation_window: request.aggregation_window,
        })
        .await
    }

    /// Internal recall contract for benchmarks and controlled evaluations.
    /// Public callers always enter through [`Self::recall`].
    pub async fn recall_internal(
        &self,
        request: RecallRequest,
    ) -> Result<RecallResponse, ServiceError> {
        let deep_started_at = (request.mode == RecallMode::Deep).then(std::time::Instant::now);
        let context = request.context.clone();
        let query = request.query.clone();
        let k = request.k;
        if !recall_scope_admitted(&request.context)
            || (request.mode == RecallMode::Deep && self.deep_recall_provider.is_none())
        {
            return recall_with_pool_and_selection_and_deep_started(
                self.store.as_ref(),
                request,
                None,
                self.clock.as_ref(),
                self.recall_pool_depth,
                self.pack_levers,
                self.lexical_scorer,
                self.temporal_grounding_enabled,
                self.cross_reranker.as_deref(),
                self.cross_rerank_candidate_selection,
                self.cross_rerank_granularity,
                self.deep_recall_provider.as_deref(),
                deep_started_at,
            )
            .await
            .map_err(ServiceError::from);
        }
        // Real embedding provider → embed the query and run the vector
        // channel; the Noop provider keeps the channel honestly disabled.
        let query_vec = if self.embedder.dimensions() > 0 {
            run_embedding_task(
                Arc::clone(&self.embedder),
                vec![query.clone()],
                EmbeddingTaskKind::Query,
            )
            .await
            .map_err(|error| {
                ServiceError::Core(CoreError::Store(StoreError::Backend(format!(
                    "query embedding failed: {error}"
                ))))
            })?
            .into_iter()
            .next()
            .filter(|vec| !vec.is_empty())
        } else {
            None
        };
        // The stored counterparts of `query_vec` live under the active
        // embedder's profile; the store filters `<=>` to that id (spec 03).
        let vector_query = query_vec.as_deref().map(|vec| VectorQuery {
            vec,
            profile_id: embedding_profile_for(self.embedder()).id,
        });
        let response = recall_with_pool_and_selection_and_deep_started(
            self.store.as_ref(),
            request.clone(),
            vector_query,
            self.clock.as_ref(),
            self.recall_pool_depth,
            self.pack_levers,
            self.lexical_scorer,
            self.temporal_grounding_enabled,
            self.cross_reranker.as_deref(),
            self.cross_rerank_candidate_selection,
            self.cross_rerank_granularity,
            self.deep_recall_provider.as_deref(),
            deep_started_at,
        )
        .await?;

        if !response.items.is_empty()
            || request.mode == RecallMode::Deep
            || request.transaction_as_of.is_some()
            || request.valid_at.is_some()
        {
            return Ok(response);
        }
        let pending = self.store.pending_job_count(&context).await?;
        if pending == 0 {
            return Ok(response);
        }
        let episodes = self.store.fetch_episodes_for_scope(&context, 256).await?;
        let items = degraded_episode_items(&episodes, &query, k.max(1));
        if items.is_empty() {
            return Ok(response);
        }
        let consolidation_lag_ms = 1;
        let mut trace = self
            .store
            .trace_by_id(&context, response.trace_id)
            .await?
            .ok_or(StoreError::NotFound("retrieval trace"))?;
        trace.consolidation_lag_ms = consolidation_lag_ms;
        trace.degradation = Some(RecallDegradationDiagnostic {
            reason: RecallDegradationReason::PendingReflectionReadYourOwnWrites,
            consolidation_lag_ms,
            items: items
                .iter()
                .map(|item| DegradedRecallTraceItem {
                    body: item.body.clone(),
                    kind: item.kind,
                })
                .collect(),
        });
        self.store.store_trace(&context, trace).await?;
        Ok(RecallResponse {
            degraded: true,
            consolidation_lag_ms,
            abstention: false,
            candidate_whitelist: Vec::new(),
            citations: Vec::new(),
            items,
            ..response
        })
    }

    /// Accepts asynchronous reflection for an already-bound context. Retain
    /// owns durable job enqueueing; only the worker may claim or compile jobs.
    pub async fn reflect(
        &self,
        context: &ResolvedMemoryContext,
        idempotency_key: &str,
        request: ReflectRequest,
    ) -> Result<MutationResponse, ServiceError>
    where
        S: MutationLedgerStore,
    {
        if (
            request.subject_id,
            request.scope_id,
            request.actor_id,
            request.agent_node_id,
            request.subject_generation,
        ) != (
            context.data_subject_id,
            context.scope_id,
            context.actor_id,
            context.agent_node_id,
            context.subject_generation,
        ) {
            return Err(ServiceError::Invalid(
                "reflect request identity must match the resolved context".to_string(),
            ));
        }
        let claim = MutationClaim::new(
            context,
            MutationVerb::Reflect,
            idempotency_key,
            canonical_mutation_request_hash(MutationVerb::Reflect, &request)?,
        )?;
        let mut tx = self.store.begin(context).await?;
        match self.store.stage_mutation_claim(&mut tx, claim).await? {
            MutationClaimOutcome::Replay(response) => {
                self.store.commit(tx).await?;
                Ok(response)
            }
            MutationClaimOutcome::Execute => {
                let job_id = self
                    .store
                    .enqueue_reflect(
                        &mut tx,
                        ReflectJob {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            actor_id: context.actor_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            episode_id: None,
                            resource_id: None,
                            kind: ReflectJobKind::ReflectScope,
                            compiler_version: COMPILER_VERSION.to_string(),
                            subject: None,
                            predicate: None,
                        },
                    )
                    .await?;
                let response = serialized_mutation_response(202, &ReflectAccepted { job_id })?;
                self.store
                    .stage_mutation_response(&mut tx, response.clone())
                    .await?;
                self.store.commit(tx).await?;
                Ok(response)
            }
        }
    }

    pub async fn correct(
        &self,
        context: &ResolvedMemoryContext,
        idempotency_key: &str,
        mut request: CorrectRequest,
    ) -> Result<MutationResponse, ServiceError>
    where
        S: MutationLedgerStore,
    {
        if request.correction.value.trim().is_empty() {
            return Err(CoreError::Invalid("correction value cannot be empty".to_string()).into());
        }
        if request.correction.source_ref.trim().is_empty() {
            return Err(ServiceError::Invalid(
                "correction source_ref must not be blank".to_string(),
            ));
        }
        request.correction.observed_at =
            canonical_utc_timestamp(&request.correction.observed_at, "correction observed_at")?;
        validate_valid_interval(
            request.correction.valid_from.as_deref(),
            request.correction.valid_to.as_deref(),
        )?;
        let claim = MutationClaim::new(
            context,
            MutationVerb::Correct,
            idempotency_key,
            canonical_mutation_request_hash(MutationVerb::Correct, &request)?,
        )?;
        let mut probe = self.store.begin(context).await?;
        match self
            .store
            .stage_mutation_claim(&mut probe, claim.clone())
            .await?
        {
            MutationClaimOutcome::Replay(response) => {
                self.store.commit(probe).await?;
                return Ok(response);
            }
            MutationClaimOutcome::Execute => self.store.rollback(probe).await?,
        }

        let embedding = if self.embedder.dimensions() > 0 {
            run_embedding_task(
                Arc::clone(&self.embedder),
                vec![request.correction.value.clone()],
                EmbeddingTaskKind::Document,
            )
            .await
            .map_err(|error| {
                CoreError::Store(StoreError::Backend(format!("embedding failed: {error}")))
            })?
            .into_iter()
            .next()
            .filter(|vector| !vector.is_empty())
            .map(|vector| (embedding_profile_for(self.embedder.as_ref()), vector))
        } else {
            None
        };

        let mut tx = self.store.begin(context).await?;
        match self.store.stage_mutation_claim(&mut tx, claim).await? {
            MutationClaimOutcome::Replay(response) => {
                self.store.commit(tx).await?;
                return Ok(response);
            }
            MutationClaimOutcome::Execute => {}
        }
        let result = self
            .store
            .stage_correction(
                &mut tx,
                CorrectionWrite {
                    selector: request.selector,
                    source_ref: request.correction.source_ref.clone(),
                    observed_at: request.correction.observed_at.clone(),
                    correction: request.correction,
                    now: self.clock.now_rfc3339(),
                    embedding,
                    unit_ids: Default::default(),
                },
            )
            .await
            .map_err(|error| match error {
                StoreError::NotFound(entity) => CoreError::NotFound(entity.to_string()),
                other => CoreError::Store(other),
            })?;
        let response = serialized_mutation_response(200, &result)?;
        self.store
            .stage_mutation_response(&mut tx, response.clone())
            .await?;
        self.store.commit(tx).await?;
        Ok(response)
    }

    pub async fn forget(
        &self,
        context: &ResolvedMemoryContext,
        idempotency_key: &str,
        request: ForgetRequest,
    ) -> Result<MutationResponse, ServiceError>
    where
        S: MutationLedgerStore,
    {
        let target = request
            .selector
            .exactly_one_target()
            .map_err(CoreError::Invalid)?;
        let claim = MutationClaim::new(
            context,
            MutationVerb::Forget,
            idempotency_key,
            canonical_mutation_request_hash(MutationVerb::Forget, &request)?,
        )?;
        let mut tx = self.store.begin(context).await?;
        match self.store.stage_mutation_claim(&mut tx, claim).await? {
            MutationClaimOutcome::Replay(response) => {
                self.store.commit(tx).await?;
                Ok(response)
            }
            MutationClaimOutcome::Execute => {
                let outcome = self
                    .store
                    .stage_forget(
                        &mut tx,
                        ForgetWrite {
                            target,
                            now: self.clock.now_rfc3339(),
                        },
                    )
                    .await?;
                let result = ForgetResult {
                    deletion_generation: outcome.deletion_generation,
                    policy: "hard_delete".to_string(),
                    invalidated_units: outcome.invalidated_units,
                    verification: "authorized_transaction_committed".to_string(),
                    trace_ref: None,
                };
                let response = serialized_mutation_response(200, &result)?;
                self.store
                    .stage_mutation_response(&mut tx, response.clone())
                    .await?;
                self.store.commit(tx).await?;
                Ok(response)
            }
        }
    }

    /// Applies one compile-time file plan as a single serializable mutation.
    pub async fn file_sync(
        &self,
        context: &ResolvedMemoryContext,
        idempotency_key: &str,
        mut request: FileSyncRequest,
    ) -> Result<MutationResponse, ServiceError>
    where
        S: MutationLedgerStore,
    {
        if (
            request.subject_id,
            request.scope_id,
            request.actor_id,
            request.agent_node_id,
            request.subject_generation,
        ) != (
            context.data_subject_id,
            context.scope_id,
            context.actor_id,
            context.agent_node_id,
            context.subject_generation,
        ) {
            return Err(ServiceError::SyncInvalid(
                "file sync request identity must match the resolved context".to_string(),
            ));
        }
        if request.operations.is_empty() {
            return Err(ServiceError::SyncInvalid(
                "file sync operations must not be empty".to_string(),
            ));
        }
        if !is_sha256(&request.base_fingerprint) {
            return Err(ServiceError::SyncInvalid(
                "base_fingerprint must be a lowercase SHA-256 digest".to_string(),
            ));
        }
        if !is_sha256(&request.plan_sha256) {
            return Err(ServiceError::SyncInvalid(
                "plan_sha256 must be a lowercase SHA-256 digest".to_string(),
            ));
        }
        request.observed_at = canonical_utc_timestamp(&request.observed_at, "observed_at")
            .map_err(|error| ServiceError::SyncInvalid(error.to_string()))?;

        let mut touched_units = HashSet::new();
        let mut plan_fact_keys = HashSet::new();
        for (index, operation) in request.operations.iter_mut().enumerate() {
            match operation {
                FileSyncOperation::Correct { base, body } => {
                    validate_file_sync_metadata(base, &format!("operations[{index}].base"))?;
                    if !touched_units.insert(base.unit_id) {
                        return Err(ServiceError::SyncInvalid(
                            "one file sync plan cannot touch a unit more than once".to_string(),
                        ));
                    }
                    if let Some(fact_key) = &base.fact_key
                        && !plan_fact_keys.insert(fact_key.clone())
                    {
                        return Err(ServiceError::SyncInvalid(
                            "one file sync plan cannot use the same fact_key more than once"
                                .to_string(),
                        ));
                    }
                    if body.trim().is_empty() {
                        return Err(ServiceError::SyncInvalid(
                            "correct body must not be blank".to_string(),
                        ));
                    }
                    if format!("{:x}", Sha256::digest(body.as_bytes())) == base.body_sha256 {
                        return Err(ServiceError::SyncInvalid(
                            "correct body must differ from the base unit".to_string(),
                        ));
                    }
                }
                FileSyncOperation::Retain {
                    fact_key,
                    predicate,
                    body,
                    confidence,
                    valid_from,
                    valid_to,
                } => {
                    if !context.allows(
                        MemoryKind::Semantic,
                        context.scope_id,
                        context.agent_node_id,
                    ) {
                        return Err(ServiceError::SyncInvalid(
                            "resolved context does not allow semantic memory".to_string(),
                        ));
                    }
                    if !matches!(
                        context.actor_trust,
                        TrustLevel::TrustedUser | TrustLevel::TrustedSystem
                    ) {
                        return Err(ServiceError::SyncInvalid(
                            "retain requires trusted direct provenance".to_string(),
                        ));
                    }
                    if fact_key.trim().is_empty() || predicate.trim().is_empty() {
                        return Err(ServiceError::SyncInvalid(
                            "retain fact_key and predicate must not be blank".to_string(),
                        ));
                    }
                    if fact_key.trim() != fact_key || predicate.trim() != predicate {
                        return Err(ServiceError::SyncInvalid(
                            "retain fact_key and predicate must not contain surrounding whitespace"
                                .to_string(),
                        ));
                    }
                    if !plan_fact_keys.insert(fact_key.clone()) {
                        return Err(ServiceError::SyncInvalid(
                            "one file sync plan cannot use the same fact_key more than once"
                                .to_string(),
                        ));
                    }
                    if body.trim().is_empty() {
                        return Err(ServiceError::SyncInvalid(
                            "retain body must not be blank".to_string(),
                        ));
                    }
                    if !confidence.is_finite() || !(0.0..=1.0).contains(confidence) {
                        return Err(ServiceError::SyncInvalid(
                            "retain confidence must be finite and between 0 and 1".to_string(),
                        ));
                    }
                    canonicalize_optional_timestamp(
                        valid_from,
                        &format!("operations[{index}].valid_from"),
                    )
                    .map_err(|error| ServiceError::SyncInvalid(error.to_string()))?;
                    canonicalize_optional_timestamp(
                        valid_to,
                        &format!("operations[{index}].valid_to"),
                    )
                    .map_err(|error| ServiceError::SyncInvalid(error.to_string()))?;
                    validate_valid_interval(valid_from.as_deref(), valid_to.as_deref())
                        .map_err(|error| ServiceError::SyncInvalid(error.to_string()))?;
                }
                FileSyncOperation::Forget { base } => {
                    validate_file_sync_metadata(base, &format!("operations[{index}].base"))?;
                    if !touched_units.insert(base.unit_id) {
                        return Err(ServiceError::SyncInvalid(
                            "one file sync plan cannot touch a unit more than once".to_string(),
                        ));
                    }
                    if let Some(fact_key) = &base.fact_key
                        && !plan_fact_keys.insert(fact_key.clone())
                    {
                        return Err(ServiceError::SyncInvalid(
                            "one file sync plan cannot use the same fact_key more than once"
                                .to_string(),
                        ));
                    }
                }
            }
        }
        let actual_plan_sha256 = file_sync_plan_sha256(&request.operations).map_err(|error| {
            ServiceError::SyncInvalid(format!("file sync plan cannot be encoded: {error}"))
        })?;
        if request.plan_sha256 != actual_plan_sha256 {
            return Err(ServiceError::SyncInvalid(
                "plan_sha256 does not match the canonical ordered operations".to_string(),
            ));
        }

        let claim = MutationClaim::new(
            context,
            MutationVerb::FileSync,
            idempotency_key,
            canonical_mutation_request_hash(MutationVerb::FileSync, &request)?,
        )?;
        if let Some(response) = self
            .store
            .lookup_mutation_replay(context, &claim)
            .await
            .map_err(sync_store_error)?
        {
            return Ok(response);
        }

        let evaluated_at = self.clock.now_rfc3339();
        let initial_admission = self
            .store
            .fetch_file_sync_transition_snapshot(context)
            .await
            .map_err(sync_store_error)?;
        let initial_admission_fingerprint = file_sync_admission_snapshot_sha256(&initial_admission)
            .map_err(|error| {
                ServiceError::SyncInvalid(format!(
                    "file sync admission snapshot cannot be encoded: {error}"
                ))
            })?;
        let current = projection_items(
            self.store
                .canonical_projection_units(context, &evaluated_at)
                .await
                .map_err(sync_store_error)?,
        )?;
        let current_fingerprint = canonical_projection_fingerprint(&current)?;
        if current_fingerprint != request.base_fingerprint {
            return Err(ServiceError::SyncConflict(format!(
                "base_fingerprint {} no longer matches canonical projection {current_fingerprint}",
                request.base_fingerprint
            )));
        }
        for operation in &request.operations {
            let base = match operation {
                FileSyncOperation::Correct { base, .. } | FileSyncOperation::Forget { base } => {
                    Some(base)
                }
                FileSyncOperation::Retain { .. } => None,
            };
            if let Some(base) = base
                && current
                    .iter()
                    .find(|item| item.unit_id == base.unit_id)
                    .is_none_or(|item| !metadata_matches(base, item))
            {
                return Err(ServiceError::SyncInvalid(
                    "file sync immutable unit metadata does not match the canonical projection"
                        .to_string(),
                ));
            }
        }

        let mut working = initial_admission.clone();
        let mut prepared = Vec::with_capacity(request.operations.len());
        for (index, operation) in request.operations.iter().enumerate() {
            let source_ref = format!("file-sync:{}:{index}", request.plan_sha256);
            match operation {
                FileSyncOperation::Correct { base, body } => {
                    let embedding = if self.embedder.dimensions() > 0 {
                        run_embedding_task(
                            Arc::clone(&self.embedder),
                            vec![body.clone()],
                            EmbeddingTaskKind::Document,
                        )
                        .await
                        .map_err(|error| {
                            CoreError::Store(StoreError::Backend(format!(
                                "embedding failed: {error}"
                            )))
                        })?
                        .into_iter()
                        .next()
                        .filter(|vector| !vector.is_empty())
                        .map(|vector| (embedding_profile_for(self.embedder.as_ref()), vector))
                    } else {
                        None
                    };
                    let unit_ids = CorrectionUnitIds::new();
                    let correction = CorrectionPayload {
                        value: body.clone(),
                        reason: "file_sync".to_string(),
                        source_ref: source_ref.clone(),
                        observed_at: request.observed_at.clone(),
                        valid_from: base.valid_from.clone(),
                        valid_to: base.valid_to.clone(),
                    };
                    let old = working
                        .open_units(context)
                        .into_iter()
                        .find(|unit| unit.id == base.unit_id)
                        .ok_or_else(|| {
                            ServiceError::SyncInvalid(
                                "file sync correction target is absent from admission snapshot"
                                    .to_string(),
                            )
                        })?;
                    let (replacement, remainders) = correction_rectangles_with_ids(
                        &old,
                        &correction,
                        &source_ref,
                        &request.observed_at,
                        context.actor_id,
                        &evaluated_at,
                        &unit_ids,
                    )
                    .map_err(sync_operation_error)?;
                    apply_correction_transition(
                        &mut working.units,
                        &mut working.edges,
                        context,
                        base.unit_id,
                        replacement,
                        remainders,
                        &evaluated_at,
                    )
                    .map_err(sync_operation_error)?;
                    prepared.push(PreparedFileSyncOperation::Correct {
                        memory_unit_id: base.unit_id,
                        write: CorrectionWrite {
                            selector: memphant_types::CorrectSelector {
                                memory_unit_id: base.unit_id,
                            },
                            correction,
                            source_ref,
                            observed_at: request.observed_at.clone(),
                            now: evaluated_at.clone(),
                            embedding,
                            unit_ids,
                        },
                    });
                }
                FileSyncOperation::Retain {
                    fact_key,
                    predicate,
                    body,
                    confidence,
                    valid_from,
                    valid_to,
                } => {
                    let compiled = prepare_compiled_write_from_snapshot_owned(
                        ReflectInput {
                            tenant_id: context.tenant_id,
                            data_subject_id: context.data_subject_id,
                            scope_id: context.scope_id,
                            agent_node_id: context.agent_node_id,
                            subject_generation: context.subject_generation,
                            actor_id: context.actor_id,
                            source_ref,
                            observed_at: request.observed_at.clone(),
                            source_body: Some(body.clone()),
                            episode_id: None,
                            resource_id: None,
                            job_id: memphant_types::JobId::new(),
                            compiler_version: COMPILER_VERSION.to_string(),
                            candidates: vec![ReflectCandidate {
                                source_kind: "direct".to_string(),
                                trust_level: context.actor_trust,
                                actor_id: context.actor_id,
                                subject: None,
                                predicate: Some(predicate.clone()),
                                fact_key: Some(fact_key.clone()),
                                kind: Some(MemoryKind::Semantic),
                                body: body.clone(),
                                confidence: Some(*confidence),
                                churn_class: None,
                                admission_hint: None,
                                target_unit_ids: None,
                                contextual_chunks: Vec::new(),
                                valid_from: valid_from.clone(),
                                valid_to: valid_to.clone(),
                            }],
                        },
                        Arc::clone(&self.embedder),
                        self.clock.as_ref(),
                        context,
                        working.open_units(context),
                    )
                    .await?;
                    let PreparedCompiledWrite::Write {
                        created_unit_ids,
                        write,
                        ..
                    } = compiled
                    else {
                        return Err(ServiceError::SyncInvalid(
                            "retain operation unexpectedly matched an existing compile".to_string(),
                        ));
                    };
                    if created_unit_ids.is_empty() {
                        return Err(ServiceError::SyncInvalid(
                            "retain operation did not create a semantic unit".to_string(),
                        ));
                    }
                    apply_compiled_write_to_snapshot(&mut working, &write);
                    prepared.push(PreparedFileSyncOperation::Retain {
                        created: created_unit_ids,
                        write,
                    });
                }
                FileSyncOperation::Forget { base } => {
                    apply_unit_forget_transition(
                        &mut working.units,
                        &working.edges,
                        context,
                        base.unit_id,
                        &evaluated_at,
                        None,
                    )
                    .map_err(sync_operation_error)?;
                    prepared.push(PreparedFileSyncOperation::Forget {
                        memory_unit_id: base.unit_id,
                        write: ForgetWrite {
                            target: ForgetTarget::MemoryUnit(base.unit_id),
                            now: evaluated_at.clone(),
                        },
                    });
                }
            }
        }

        let mut tx = self
            .store
            .begin_serializable(context)
            .await
            .map_err(sync_store_error)?;
        match self.store.stage_mutation_claim(&mut tx, claim).await {
            Ok(MutationClaimOutcome::Replay(response)) => {
                self.store.commit(tx).await.map_err(sync_commit_error)?;
                return Ok(response);
            }
            Ok(MutationClaimOutcome::Execute) => {}
            Err(error) => {
                let _ = self.store.rollback(tx).await;
                return Err(sync_store_error(error));
            }
        }

        let current = match self
            .store
            .canonical_projection_units_in_tx(&mut tx, &evaluated_at)
            .await
        {
            Ok(units) => match projection_items(units) {
                Ok(items) => items,
                Err(error) => {
                    let _ = self.store.rollback(tx).await;
                    return Err(error);
                }
            },
            Err(error) => {
                let _ = self.store.rollback(tx).await;
                return Err(sync_store_error(error));
            }
        };
        let current_fingerprint = match canonical_projection_fingerprint(&current) {
            Ok(fingerprint) => fingerprint,
            Err(error) => {
                let _ = self.store.rollback(tx).await;
                return Err(error);
            }
        };
        if current_fingerprint != request.base_fingerprint {
            let _ = self.store.rollback(tx).await;
            return Err(ServiceError::SyncConflict(format!(
                "base_fingerprint {} no longer matches canonical projection {current_fingerprint}",
                request.base_fingerprint
            )));
        }
        let transaction_admission = match self
            .store
            .fetch_file_sync_transition_snapshot_in_tx(&mut tx)
            .await
        {
            Ok(snapshot) => snapshot,
            Err(error) => {
                let _ = self.store.rollback(tx).await;
                return Err(sync_store_error(error));
            }
        };
        let transaction_admission_fingerprint =
            match file_sync_admission_snapshot_sha256(&transaction_admission).map_err(|error| {
                ServiceError::SyncInvalid(format!(
                    "file sync admission snapshot cannot be encoded: {error}"
                ))
            }) {
                Ok(fingerprint) => fingerprint,
                Err(error) => {
                    let _ = self.store.rollback(tx).await;
                    return Err(error);
                }
            };
        if transaction_admission_fingerprint != initial_admission_fingerprint {
            let _ = self.store.rollback(tx).await;
            return Err(ServiceError::SyncConflict(
                "full unit/edge transition snapshot changed during file sync preparation"
                    .to_string(),
            ));
        }

        let mut results = Vec::with_capacity(prepared.len());
        for operation in prepared {
            let staged = match operation {
                PreparedFileSyncOperation::Correct {
                    memory_unit_id,
                    write,
                } => self
                    .store
                    .stage_correction(&mut tx, write)
                    .await
                    .map(|outcome| FileSyncOperationResult::Correct {
                        memory_unit_id,
                        created: outcome.created,
                    })
                    .map_err(sync_operation_error),
                PreparedFileSyncOperation::Retain { created, write } => self
                    .store
                    .stage_compiled_units(&mut tx, None, write)
                    .await
                    .map_err(sync_operation_error)
                    .and_then(|outcome| match outcome {
                        ClaimMutationOutcome::Applied => {
                            Ok(FileSyncOperationResult::Retain { created })
                        }
                        ClaimMutationOutcome::Stale => Err(ServiceError::SyncConflict(
                            "serializable transaction conflicted".to_string(),
                        )),
                    }),
                PreparedFileSyncOperation::Forget {
                    memory_unit_id,
                    write,
                } => self
                    .store
                    .stage_forget(&mut tx, write)
                    .await
                    .map(|outcome| FileSyncOperationResult::Forget {
                        memory_unit_id,
                        deletion_generation: outcome.deletion_generation,
                        invalidated: outcome.invalidated_units,
                    })
                    .map_err(sync_operation_error),
            };
            match staged {
                Ok(result) => results.push(result),
                Err(error) => {
                    let _ = self.store.rollback(tx).await;
                    return Err(error);
                }
            }
        }

        let final_evaluated_at = self.clock.now_rfc3339();
        let final_items = match self
            .store
            .canonical_projection_units_in_tx(&mut tx, &final_evaluated_at)
            .await
        {
            Ok(units) => match projection_items(units) {
                Ok(items) => items,
                Err(error) => {
                    let _ = self.store.rollback(tx).await;
                    return Err(error);
                }
            },
            Err(error) => {
                let _ = self.store.rollback(tx).await;
                return Err(sync_store_error(error));
            }
        };
        // Use the longest future canonical timestamp so the committed state
        // cannot make the immediate projection GET cross the encoded ceiling.
        let final_projection = match canonical_projection_response(
            context,
            MAX_CANONICAL_PROJECTION_TIMESTAMP.to_string(),
            final_items,
        ) {
            Ok(projection) => projection,
            Err(error) => {
                let _ = self.store.rollback(tx).await;
                return Err(error);
            }
        };
        let response = match serialized_mutation_response(
            200,
            &FileSyncResult {
                base_fingerprint: request.base_fingerprint,
                fingerprint: final_projection.fingerprint,
                evaluated_at: final_evaluated_at,
                plan_sha256: request.plan_sha256,
                operations: results,
            },
        ) {
            Ok(response) => response,
            Err(error) => {
                let _ = self.store.rollback(tx).await;
                return Err(error);
            }
        };
        if let Err(error) = self
            .store
            .stage_mutation_response(&mut tx, response.clone())
            .await
        {
            let _ = self.store.rollback(tx).await;
            return Err(sync_operation_error(error));
        }
        self.store.commit(tx).await.map_err(sync_commit_error)?;
        Ok(response)
    }

    pub async fn mark(
        &self,
        context: &ResolvedMemoryContext,
        idempotency_key: &str,
        mut request: MarkRequest,
    ) -> Result<MutationResponse, ServiceError>
    where
        S: MutationLedgerStore,
    {
        if request.caller_id.trim().is_empty() {
            return Err(CoreError::Invalid("caller_id cannot be empty".to_string()).into());
        }
        let claim = MutationClaim::new(
            context,
            MutationVerb::Mark,
            idempotency_key,
            canonical_mutation_request_hash(MutationVerb::Mark, &request)?,
        )?;
        request.used_ids.sort_unstable_by_key(|id| id.as_uuid());
        request.used_ids.dedup();
        let mut tx = self.store.begin(context).await?;
        match self.store.stage_mutation_claim(&mut tx, claim).await? {
            MutationClaimOutcome::Replay(response) => {
                self.store.commit(tx).await?;
                Ok(response)
            }
            MutationClaimOutcome::Execute => {
                let trace = self
                    .store
                    .trace_by_id(context, request.trace_id)
                    .await?
                    .ok_or_else(|| CoreError::NotFound("retrieval trace".to_string()))?;
                let canonical_ids: HashSet<UnitId> = trace
                    .context_items
                    .iter()
                    .map(|item| item.unit_id)
                    .collect();
                if request
                    .used_ids
                    .iter()
                    .any(|unit_id| !canonical_ids.contains(unit_id))
                {
                    return Err(CoreError::Invalid(
                        "marked units must belong to the trace canonical inclusion whitelist"
                            .to_string(),
                    )
                    .into());
                }
                let result = MarkResult {
                    accepted: true,
                    trace_id: request.trace_id,
                };
                self.store
                    .stage_review_events(
                        &mut tx,
                        vec![ReviewEvent {
                            tenant_id: context.tenant_id,
                            trace_id: request.trace_id,
                            caller_id: request.caller_id,
                            used_ids: request.used_ids,
                            outcome: request.outcome,
                            recorded_at: self.clock.now_rfc3339(),
                        }],
                    )
                    .await?;
                let response = serialized_mutation_response(200, &result)?;
                self.store
                    .stage_mutation_response(&mut tx, response.clone())
                    .await?;
                self.store.commit(tx).await?;
                Ok(response)
            }
        }
    }

    /// Tenant-bound trace fetch: a trace owned by another tenant is `None`.
    pub async fn trace(
        &self,
        context: &ResolvedMemoryContext,
        id: TraceId,
    ) -> Result<Option<RetrievalTrace>, ServiceError> {
        Ok(self.store.trace_by_id(context, id).await?)
    }

    pub async fn scope_memory_page(
        &self,
        context: &ResolvedMemoryContext,
        cursor: Option<UnitId>,
        limit: usize,
    ) -> Result<ScopePage, ServiceError> {
        Ok(self.store.scope_memory_page(context, cursor, limit).await?)
    }

    /// Returns the complete bitemporally-current file projection at one server clock instant.
    /// The historical cursor export above intentionally has different semantics.
    pub async fn canonical_projection(
        &self,
        context: &ResolvedMemoryContext,
    ) -> Result<CanonicalProjectionResponse, ServiceError> {
        let evaluated_at = self.clock.now_rfc3339();
        let items = projection_items(
            self.store
                .canonical_projection_units(context, &evaluated_at)
                .await?,
        )?;
        canonical_projection_response(context, evaluated_at, items)
    }

    /// One worker tick: claims up to `batch` reflect jobs (unfiltered across
    /// tenants) and compiles them. Infrastructure failures are requeued with bounded backoff;
    /// provider-invalid output and provider-unavailable-after-retries are terminally failed;
    /// later jobs in a failed scope lane are released for ordered redelivery.
    /// Returns a [`WorkerTickOutcome`] — completed, failed, retried and
    /// deferred counts, not a bare completed-count. See that type for why.
    pub async fn run_worker_tick(
        &self,
        batch: usize,
    ) -> Result<crate::WorkerTickOutcome, ServiceError> {
        self.run_worker_tick_scoped(JobFilter::default(), batch)
            .await
    }

    /// Queue-wide pending count for fleet-worker drain orchestration. Unlike
    /// `pending_job_count`, this is intentionally unscoped and must never be
    /// exposed through a tenant request surface.
    pub async fn pending_worker_job_count(&self) -> Result<usize, ServiceError> {
        Ok(self.store.pending_worker_job_count().await?)
    }

    /// Queue-wide dead-letter count for worker health/drain exit checks.
    pub async fn worker_dead_letter_count(&self) -> Result<u64, ServiceError> {
        Ok(self.store.dead_letter_count().await?)
    }

    /// A worker tick restricted to `filter`'s tenant/scope lanes. The unscoped
    /// tick claims across every tenant, which is right for a fleet worker but
    /// wrong for callers that must not drain lanes they do not own (per-tenant
    /// workers, shared-database test harnesses).
    pub async fn run_worker_tick_scoped(
        &self,
        filter: JobFilter,
        batch: usize,
    ) -> Result<crate::WorkerTickOutcome, ServiceError> {
        let jobs = self.store.claim_reflect_jobs(filter, batch).await?;
        let mut lanes: Vec<Vec<ReflectJobRow>> = Vec::new();
        for job in jobs {
            let lane = (
                job.job.tenant_id,
                job.job.data_subject_id,
                job.job.subject_generation,
                job.job.scope_id,
                job.job.agent_node_id,
            );
            match lanes.iter_mut().find(|jobs| {
                jobs.first().is_some_and(|first| {
                    (
                        first.job.tenant_id,
                        first.job.data_subject_id,
                        first.job.subject_generation,
                        first.job.scope_id,
                        first.job.agent_node_id,
                    ) == lane
                })
            }) {
                Some(jobs) => jobs.push(job),
                None => lanes.push(vec![job]),
            }
        }
        let lane_concurrency = if self.structured_state_provider.is_some() {
            self.structured_state_prefetch_concurrency
        } else {
            self.worker_compile_concurrency
        };
        let outcomes = stream::iter(lanes.into_iter().map(|jobs| {
            let service = self.clone();
            async move {
                let mut outcome = crate::WorkerTickOutcome::default();
                let mut blocked = false;
                for job in jobs {
                    if blocked {
                        service
                            .store
                            .release_reflect_job(
                                &job,
                                0,
                                "blocked by an earlier scope-lane job".to_string(),
                            )
                            .await?;
                        outcome.deferred += 1;
                        continue;
                    }
                    if let Some(live_compiler_version) =
                        service.live_structured_compiler_identity(job.job.kind)
                        && job.job.compiler_version != live_compiler_version
                    {
                        service
                            .store
                            .requeue_reflect_job_with_compiler(&job, &live_compiler_version)
                            .await?;
                        outcome.deferred += 1;
                        blocked = true;
                        continue;
                    }
                    let prepared = async {
                        let context = service.resolve_job_context(&job).await?;
                        let projections = service.prepare_structured_state(&job, &context).await?;
                        Ok::<_, ServiceError>((context, projections))
                    };
                    let (context, projections) = match CatchUnwind::new(prepared).await {
                        Ok(Ok(prepared)) => prepared,
                        Ok(Err(error)) => {
                            blocked = true;
                            if is_terminal_provider_error(&error) {
                                service
                                    .store
                                    .fail_reflect_job(&job, error.to_string())
                                    .await?;
                                outcome.failed += 1;
                            } else {
                                service
                                    .store
                                    .release_reflect_job(
                                        &job,
                                        retry_backoff_seconds(job.attempts),
                                        error.to_string(),
                                    )
                                    .await?;
                                outcome.retried += 1;
                            }
                            eprintln!(
                                "memphant-worker: job {} preparation failed (attempt {}): {error}",
                                job.job.id.as_uuid(),
                                job.attempts
                            );
                            continue;
                        }
                        Err(()) => {
                            blocked = true;
                            service
                                .store
                                .release_reflect_job(
                                    &job,
                                    retry_backoff_seconds(job.attempts),
                                    "structured-state preparation panicked".to_string(),
                                )
                                .await?;
                            outcome.retried += 1;
                            eprintln!(
                                "memphant-worker: job {} preparation panicked (attempt {})",
                                job.job.id.as_uuid(),
                                job.attempts
                            );
                            continue;
                        }
                    };
                    match CatchUnwind::new(service.compile_job(
                        &job,
                        &context,
                        None,
                        Some(&projections),
                    ))
                    .await
                    {
                        Ok(Ok(_)) => {
                            if service.store.complete_reflect_job(&job).await?
                                == ClaimMutationOutcome::Applied
                            {
                                outcome.completed += 1;
                            }
                        }
                        Ok(Err(error)) => {
                            blocked = true;
                            if is_terminal_provider_error(&error) {
                                service
                                    .store
                                    .fail_reflect_job(&job, error.to_string())
                                    .await?;
                                outcome.failed += 1;
                            } else {
                                service
                                    .store
                                    .release_reflect_job(
                                        &job,
                                        retry_backoff_seconds(job.attempts),
                                        error.to_string(),
                                    )
                                    .await?;
                                outcome.retried += 1;
                            }
                            eprintln!(
                                "memphant-worker: job {} failed (attempt {}): {error}",
                                job.job.id.as_uuid(),
                                job.attempts
                            );
                        }
                        Err(()) => {
                            blocked = true;
                            service
                                .store
                                .release_reflect_job(
                                    &job,
                                    retry_backoff_seconds(job.attempts),
                                    "reflect compilation panicked".to_string(),
                                )
                                .await?;
                            outcome.retried += 1;
                            eprintln!(
                                "memphant-worker: job {} panicked (attempt {})",
                                job.job.id.as_uuid(),
                                job.attempts
                            );
                        }
                    }
                }
                Ok::<crate::WorkerTickOutcome, ServiceError>(outcome)
            }
        }))
        .buffer_unordered(lane_concurrency)
        .collect::<Vec<_>>()
        .await;
        outcomes
            .into_iter()
            .try_fold(crate::WorkerTickOutcome::default(), |mut total, outcome| {
                let lane = outcome?;
                total.completed += lane.completed;
                total.failed += lane.failed;
                total.retried += lane.retried;
                total.deferred += lane.deferred;
                Ok(total)
            })
    }

    /// Compiles one claimed reflect job through `reflect_recorded` — the ONE
    /// compilation path shared by the public reflect verb and the worker.
    async fn compile_job(
        &self,
        job: &ReflectJobRow,
        context: &ResolvedMemoryContext,
        compiler_override: Option<String>,
        prepared_structured_state: Option<&[crate::ProjectedStructuredState]>,
    ) -> Result<(), ServiceError> {
        if compiler_override
            .as_ref()
            .is_some_and(|version| version != &job.job.compiler_version)
        {
            return Err(ServiceError::Invalid(
                "reflect compiler override must match the queued compiler version".to_string(),
            ));
        }
        let compiler_version = job.job.compiler_version.clone();
        let (episode_id, resource_id, source_ref, observed_at, source_body, candidates): (
            _,
            _,
            String,
            String,
            Option<String>,
            Vec<ReflectCandidate>,
        ) = match job.job.kind {
            ReflectJobKind::ReflectEpisode => {
                let Some(episode_id) = job.job.episode_id else {
                    return Ok(());
                };
                let Some(episode) = self.store.fetch_episode(context, episode_id).await? else {
                    // Episode gone (e.g. forgotten before compile): nothing to do.
                    return Ok(());
                };
                // W5 temporal grounding: extract the episode's primary content
                // date (deterministic, clock-free) once. Valid time falls back
                // to the episode's first observation when the body has no date.
                // Gated: off ⇒ no date at all.
                let content_date = if self.temporal_grounding_enabled {
                    parse_content_date(&episode.body)
                } else {
                    None
                };
                // `YYYY-MM-DD` for the chunk header slot; midnight-UTC RFC 3339
                // for the grounded `valid_from`. Both derive from the SAME parsed
                // date so the header and the window agree.
                let content_date_header = content_date.map(|date| date.to_string());
                let valid_from = self.temporal_grounding_enabled.then(|| {
                    content_date.map_or_else(
                        || episode.first_observed_at.clone(),
                        |date| format!("{date}T00:00:00Z"),
                    )
                });
                // Rung 4: mint contextual chunks tied to this raw episode when
                // the write path is enabled (default on since 2026-07-10).
                // Every other candidate construction (resource jobs,
                // direct-unit retains) stays chunk-free — episodes only.
                let contextual_chunks = if self.contextual_chunks_write_enabled {
                    episode_contextual_chunks(
                        episode.id,
                        &episode.source_kind,
                        &episode.body,
                        content_date_header.as_deref(),
                    )
                } else {
                    Vec::new()
                };
                // The raw episode candidate is explicitly episodic; derived
                // candidates keep the compiler's semantic default. Then — only
                // when W6 fact extraction is on — add mined facts. The
                // `[date ...]` body prefix couples to temporal grounding only:
                // `content_date_header` is already `None` unless that flag is on.
                let mut candidates = vec![ReflectCandidate {
                    source_kind: episode.source_kind.clone(),
                    trust_level: episode.source_trust,
                    actor_id: episode.actor_id,
                    subject: job.job.subject.clone(),
                    predicate: job.job.predicate.clone(),
                    fact_key: None,
                    kind: Some(MemoryKind::Episodic),
                    body: episode.body.clone(),
                    confidence: None,
                    churn_class: None,
                    admission_hint: None,
                    target_unit_ids: None,
                    contextual_chunks,
                    valid_from,
                    valid_to: None,
                }];
                if self.fact_extraction_enabled {
                    candidates.extend(extract_fact_candidates(
                        &episode,
                        content_date_header.as_deref(),
                    ));
                }
                if self.structured_state_provider.is_some() {
                    let projections = match prepared_structured_state {
                        Some(projections) => projections.to_vec(),
                        None => self.prepare_structured_state(job, context).await?,
                    };
                    candidates.extend(projections.into_iter().map(|projection| ReflectCandidate {
                        kind: structured_projection_kind(&projection.subject),
                        source_kind: episode.source_kind.clone(),
                        trust_level: episode.source_trust,
                        actor_id: episode.actor_id,
                        subject: Some(projection.subject),
                        predicate: Some(projection.predicate),
                        fact_key: None,
                        body: projection.body,
                        confidence: None,
                        churn_class: None,
                        admission_hint: projection.admission_hint,
                        target_unit_ids: projection.target_unit_ids,
                        contextual_chunks: projection.contextual_chunks,
                        valid_from: projection.valid_from,
                        valid_to: projection.valid_to,
                    }));
                }
                (
                    Some(episode.id),
                    None,
                    episode.source_ref.clone(),
                    episode.last_observed_at.clone(),
                    Some(episode.body.clone()),
                    candidates,
                )
            }
            ReflectJobKind::ReflectResource => {
                let Some(resource_id) = job.job.resource_id else {
                    return Ok(());
                };
                let Some(resource) = self.store.fetch_resource(context, resource_id).await? else {
                    return Ok(());
                };
                let Some(body) = resource.body.clone().filter(|body| !body.trim().is_empty())
                else {
                    // Pointer-only resource: nothing durable to compile yet.
                    return Ok(());
                };
                // R1: rung-4 machinery extended to the docs domain. Mint
                // per-resource contextual chunks for DOCUMENT resources when
                // the (default-off) resource-chunk write path is enabled;
                // non-document kinds and the disabled path stay chunk-free —
                // byte-identical to today. Episodes get theirs in the
                // ReflectEpisode arm above; this is the resource twin.
                let contextual_chunks = if self.resource_chunks_write_enabled
                    && resource.kind == ResourceKind::Document
                {
                    resource_contextual_chunks(resource.id, &resource.uri, &body)
                } else {
                    Vec::new()
                };
                let mut candidates = vec![ReflectCandidate {
                    source_kind: "resource".to_string(),
                    trust_level: resource.source_trust,
                    actor_id: resource.actor_id,
                    subject: None,
                    predicate: None,
                    fact_key: None,
                    kind: Some(MemoryKind::Resource),
                    body: body.clone(),
                    confidence: None,
                    churn_class: None,
                    admission_hint: None,
                    target_unit_ids: None,
                    contextual_chunks,
                    valid_from: None,
                    valid_to: None,
                }];
                if self.structured_state_provider.is_some() {
                    let projections = match prepared_structured_state {
                        Some(projections) => projections.to_vec(),
                        None => self.prepare_structured_state(job, context).await?,
                    };
                    candidates.extend(projections.into_iter().map(|projection| ReflectCandidate {
                        kind: structured_projection_kind(&projection.subject),
                        source_kind: "resource".to_string(),
                        trust_level: resource.source_trust,
                        actor_id: resource.actor_id,
                        subject: Some(projection.subject),
                        predicate: Some(projection.predicate),
                        fact_key: None,
                        body: projection.body,
                        confidence: None,
                        churn_class: None,
                        admission_hint: projection.admission_hint,
                        target_unit_ids: projection.target_unit_ids,
                        contextual_chunks: projection.contextual_chunks,
                        valid_from: projection.valid_from,
                        valid_to: projection.valid_to,
                    }));
                }
                (
                    None,
                    Some(resource.id),
                    resource.source_ref,
                    resource.observed_at,
                    Some(body.clone()),
                    candidates,
                )
            }
            ReflectJobKind::ReflectScope => (
                None,
                None,
                format!("memphant:reflect_scope:{}", job.job.id.as_uuid()),
                self.clock.now_rfc3339(),
                None,
                Vec::new(),
            ),
        };

        // Every candidate in a job shares the episode/resource actor; the raw
        // candidate is always first, so its actor drives the ReflectInput.
        let actor_id = match job.job.kind {
            ReflectJobKind::ReflectScope => job.job.actor_id,
            _ => match candidates.first() {
                Some(candidate) => candidate.actor_id,
                None => return Ok(()),
            },
        };

        reflect_recorded_claimed_owned(
            self.store.as_ref(),
            ReflectInput {
                tenant_id: job.job.tenant_id,
                data_subject_id: job.job.data_subject_id,
                scope_id: job.job.scope_id,
                agent_node_id: job.job.agent_node_id,
                subject_generation: job.job.subject_generation,
                actor_id,
                source_ref,
                observed_at,
                source_body,
                episode_id,
                resource_id,
                job_id: job.job.id,
                compiler_version,
                candidates,
            },
            Arc::clone(&self.embedder),
            self.clock.as_ref(),
            context,
            job,
        )
        .await?;
        Ok(())
    }

    async fn prepare_structured_state(
        &self,
        job: &ReflectJobRow,
        context: &ResolvedMemoryContext,
    ) -> Result<Vec<crate::ProjectedStructuredState>, ServiceError> {
        let Some(provider) = &self.structured_state_provider else {
            return Ok(Vec::new());
        };
        let (source_kind, local_source_id, source_body, evidence_slices) = match job.job.kind {
            ReflectJobKind::ReflectEpisode => {
                let Some(episode_id) = job.job.episode_id else {
                    return Ok(Vec::new());
                };
                let Some(episode) = self.store.fetch_episode(context, episode_id).await? else {
                    return Ok(Vec::new());
                };
                let slices = evidence_slices_for_episode(&episode.body).map_err(|error| {
                    ServiceError::Core(CoreError::ProviderInvalid(error.to_string()))
                })?;
                (
                    StructuredSourceKind::Episode,
                    episode_id.as_uuid().to_string(),
                    episode.body,
                    slices,
                )
            }
            ReflectJobKind::ReflectResource => {
                let Some(resource_id) = job.job.resource_id else {
                    return Ok(Vec::new());
                };
                let Some(resource) = self.store.fetch_resource(context, resource_id).await? else {
                    return Ok(Vec::new());
                };
                let Some(body) = resource.body.filter(|body| !body.trim().is_empty()) else {
                    return Ok(Vec::new());
                };
                let slices = structured_state_slices_for_resource(&body).map_err(|error| {
                    ServiceError::Core(CoreError::ProviderInvalid(error.to_string()))
                })?;
                (
                    StructuredSourceKind::Resource,
                    resource_id.as_uuid().to_string(),
                    body,
                    slices,
                )
            }
            ReflectJobKind::ReflectScope => return Ok(Vec::new()),
        };
        let source_body_sha256 = format!("{:x}", Sha256::digest(source_body.as_bytes()));
        let request = StructuredStateRequest {
            source_kind,
            source_body_sha256: source_body_sha256.clone(),
            batch_index: 0,
            evidence_slices: evidence_slices.clone(),
        };
        let batches = provider
            .plan_batches(source_kind, &source_body_sha256, evidence_slices)
            .map_err(|error| match error {
                crate::StructuredStateProviderError::Unavailable(message) => {
                    ServiceError::Core(CoreError::ProviderUnavailable(message))
                }
                crate::StructuredStateProviderError::InvalidOutput(message) => {
                    ServiceError::Core(CoreError::ProviderInvalid(message))
                }
            })?;
        let provider_identity = provider.identity();
        let extraction_packets = if let Some(prepared) = self
            .store
            .fetch_prepared_structured_state(job, &batches, provider_identity)
            .await?
        {
            prepared
        } else {
            let mut packets = Vec::with_capacity(batches.len());
            for batch in &batches {
                let batch_observations =
                    provider.extract(batch).await.map_err(|error| match error {
                        crate::StructuredStateProviderError::Unavailable(message) => {
                            ServiceError::Core(CoreError::ProviderUnavailable(message))
                        }
                        crate::StructuredStateProviderError::InvalidOutput(message) => {
                            ServiceError::Core(CoreError::ProviderInvalid(message))
                        }
                    })?;
                validate_structured_observations_for_request(
                    &source_body,
                    batch,
                    &batch_observations,
                )
                .map_err(|error| {
                    ServiceError::Core(CoreError::ProviderInvalid(error.to_string()))
                })?;
                packets.push(StructuredExtractionPacket {
                    batch_index: batch.batch_index,
                    receipt_sha256: structured_extraction_receipt_sha256(
                        provider_identity,
                        batch,
                        &batch_observations,
                    )?,
                    observations: batch_observations,
                });
            }
            self.store
                .store_prepared_structured_state(job, &batches, provider_identity, packets.clone())
                .await?;
            packets
        };
        for (batch, packet) in batches.iter().zip(&extraction_packets) {
            validate_structured_observations_for_request(&source_body, batch, &packet.observations)
                .map_err(|error| {
                    ServiceError::Core(CoreError::ProviderInvalid(error.to_string()))
                })?;
        }
        let observations = extraction_packets
            .iter()
            .flat_map(|packet| packet.observations.iter().cloned())
            .collect::<Vec<_>>();
        let active_items = self
            .store
            .fetch_scope_open_units(context)
            .await?
            .iter()
            .filter_map(crate::active_structured_state)
            .collect::<Vec<_>>();
        let operations =
            fold_structured_observations(&source_body, &request, &observations, &active_items)
                .map_err(|error| {
                    ServiceError::Core(CoreError::ProviderInvalid(error.to_string()))
                })?;
        let projections =
            project_structured_state(source_kind, &local_source_id, &source_body, &operations)
                .map_err(|error| {
                    ServiceError::Core(CoreError::ProviderInvalid(error.to_string()))
                })?;
        Ok(projections)
    }

    fn live_structured_compiler_identity(&self, kind: ReflectJobKind) -> Option<String> {
        match (kind, self.structured_state_provider.as_ref()) {
            (ReflectJobKind::ReflectEpisode | ReflectJobKind::ReflectResource, Some(provider)) => {
                Some(service_structured_compiler_identity(
                    COMPILER_VERSION,
                    provider.as_ref(),
                ))
            }
            _ => None,
        }
    }

    async fn resolve_job_context(
        &self,
        job: &ReflectJobRow,
    ) -> Result<ResolvedMemoryContext, ServiceError> {
        let context = self
            .store
            .resolve_memory_context(
                job.job.tenant_id,
                job.job.data_subject_id,
                job.job.actor_id,
                job.job.scope_id,
                job.job.agent_node_id,
            )
            .await?;
        if context.subject_generation != job.job.subject_generation {
            return Err(StoreError::Conflict("subject generation is stale".to_string()).into());
        }
        Ok(context)
    }
}

fn is_terminal_provider_error(error: &ServiceError) -> bool {
    matches!(
        error,
        ServiceError::Core(CoreError::ProviderUnavailable(_) | CoreError::ProviderInvalid(_))
    )
}

fn retry_backoff_seconds(attempts: u32) -> u64 {
    1_u64 << attempts.saturating_sub(1).min(6)
}

/// The ONE drain-exit decision, shared by every caller that drains the reflect
/// queue (`memphant-worker`'s drain mode, `bench_lme`'s in-process drain).
///
/// A zero-completion tick is NOT proof of an empty queue. `run_worker_tick_scoped`
/// releases a failed job with `retry_backoff_seconds` (`run_after` in the future)
/// and blocks the rest of its scope lane; `claim_reflect_jobs` excludes
/// `run_after > now()`, so the next tick claims nothing while the work is still
/// `queued`. Only the DATABASE can say the queue is empty, and a job the queue
/// dead-lettered is silently-dropped work, not a drained queue.
pub fn drain_finished(
    pending: usize,
    dead_letters_before: u64,
    dead_letters_after: u64,
) -> Result<bool, &'static str> {
    if dead_letters_after > dead_letters_before {
        return Err("drain produced dead-lettered jobs");
    }
    Ok(pending == 0)
}

/// Turns (or fallback segments) per contextual-chunk window. This is the
/// turn-window granularity promoted on real evidence (LME-S n=100, 2026-07-10
/// scaled-reader campaign: ≤4-turn episodes lifted ΔR@5/ΔR@10/ΔQA with CIs
/// excluding zero). The runtime write path is the same granularity as an
/// extraction-side embodiment rather than client-side windowing.
const CONTEXTUAL_CHUNK_WINDOW: usize = 4;

/// Per-episode chunk cap — the rung 4 bloat guard (disable-when: chunk fan-out
/// hurts recall latency/cost once it stops adding coverage). W9: the window
/// (see `adaptive_chunk_window`) grows past `CONTEXTUAL_CHUNK_WINDOW` for
/// bodies long enough to otherwise mint more than this many windows, so the
/// cap bounds fan-out WITHOUT truncating the body — bodies of ≤128 segments
/// (`MAX_CONTEXTUAL_CHUNKS * CONTEXTUAL_CHUNK_WINDOW`) never trigger growth
/// and chunk exactly as before this change.
const MAX_CONTEXTUAL_CHUNKS: usize = 32;

/// W9: the window size that keeps `MAX_CONTEXTUAL_CHUNKS` windows ALWAYS
/// covering the full body, instead of the fixed `CONTEXTUAL_CHUNK_WINDOW`
/// silently truncating the tail once a body outgrows
/// `MAX_CONTEXTUAL_CHUNKS * CONTEXTUAL_CHUNK_WINDOW` segments (128 today).
/// Grows only when needed: `ceil(segment_count / MAX_CONTEXTUAL_CHUNKS)` is
/// the smallest window that fits the whole body in the cap, and taking the
/// max with `CONTEXTUAL_CHUNK_WINDOW` means short bodies are completely
/// unaffected (byte-identical to pre-W9 behavior for ≤128 segments).
fn adaptive_chunk_window(segment_count: usize) -> usize {
    CONTEXTUAL_CHUNK_WINDOW.max(segment_count.div_ceil(MAX_CONTEXTUAL_CHUNKS))
}

/// Parses a body line's `role: content` turn shape: a short leading role token,
/// then `": "`, then non-empty content. Returns `(role, content)` or `None` for
/// non-turn lines. The bench lane's per-session episodes ingest in exactly this
/// form; a bracketed provenance line like `[session s1] [date ...]` has no `": "`
/// and parses as `None`. Shared by the chunk segmenter and the W6 fact miner so
/// there is ONE turn parser, not two.
fn parse_turn(line: &str) -> Option<(&str, &str)> {
    let (role, content) = line.trim().split_once(": ")?;
    let ok = !role.is_empty()
        && role.len() <= 32
        && !content.trim().is_empty()
        && role
            .chars()
            .all(|c| c.is_alphanumeric() || matches!(c, ' ' | '_' | '-'));
    ok.then_some((role, content))
}

/// A body line reads as a conversational turn when it parses as `role: content`.
fn line_is_turn(line: &str) -> bool {
    parse_turn(line).is_some()
}

/// Byte spans of the segments to window over, plus whether the body parsed as
/// turns. Turn-structured bodies window over their `role: content` lines;
/// everything else falls back to non-empty line segments.
fn segment_episode_body(body: &str) -> (Vec<(usize, usize)>, bool) {
    let mut lines: Vec<(usize, usize, bool)> = Vec::new();
    let mut offset = 0usize;
    for raw in body.split_inclusive('\n') {
        let start = offset;
        offset += raw.len();
        let content = raw.trim_end_matches(['\n', '\r']);
        if content.trim().is_empty() {
            continue;
        }
        lines.push((start, start + content.len(), line_is_turn(content)));
    }
    let turn_count = lines.iter().filter(|(_, _, is_turn)| *is_turn).count();
    // Turn-structured when turns are present and dominate (a stray `": "` in a
    // prose body never flips it).
    let turn_structured = turn_count >= 2 && turn_count * 2 >= lines.len();
    let spans = lines
        .into_iter()
        .filter(|(_, _, is_turn)| !turn_structured || *is_turn)
        .map(|(start, end, _)| (start, end))
        .collect();
    (spans, turn_structured)
}

/// Splits `body` into up to `MAX_CONTEXTUAL_CHUNKS` windows and mints one
/// `ContextualChunk` per window, each tied back to its parent episode. The
/// window is `CONTEXTUAL_CHUNK_WINDOW` turns/segments for bodies that fit
/// within the cap at that size, and grows (`adaptive_chunk_window`)
/// otherwise so the windows ALWAYS cover the full body — no silently dropped
/// tail (W9). Emits nothing when the body fits a single window (a lone chunk
/// would just duplicate the unit body) and never emits empty-body chunks —
/// the rung 4 bloat guards.
fn episode_contextual_chunks(
    episode_id: EpisodeId,
    source_kind: &str,
    body: &str,
    content_date: Option<&str>,
) -> Vec<ContextualChunk> {
    let (spans, turn_structured) = segment_episode_body(body);
    if spans.len() <= CONTEXTUAL_CHUNK_WINDOW {
        return Vec::new();
    }
    let window_size = adaptive_chunk_window(spans.len());
    let span_label = if turn_structured { "turns" } else { "segments" };
    // W5: reinstates the header date slot with the TRUE parsed content date
    // (never the compile clock). `None` ⇒ the header stays dateless, exactly as
    // before this change.
    let date_slot = content_date
        .map(|date| format!(" [date {date}]"))
        .unwrap_or_default();
    spans
        .chunks(window_size)
        // W9: `window_size` already guarantees ≤`MAX_CONTEXTUAL_CHUNKS`
        // windows for the whole body — this `take` is a defensive backstop,
        // not the active truncation it used to be.
        .take(MAX_CONTEXTUAL_CHUNKS)
        .enumerate()
        .filter_map(|(window_index, window)| {
            let start = window.first()?.0;
            let end = window.last()?.1;
            let text = body.get(start..end)?;
            if text.trim().is_empty() {
                return None;
            }
            let first = window_index * window_size + 1;
            let last = window_index * window_size + window.len();
            Some(ContextualChunk {
                id: format!("chunk-{}-{window_index}", episode_id.as_uuid()),
                header: format!(
                    "[episode {}] [kind {source_kind}]{date_slot} [{span_label} {first}-{last}]",
                    episode_id.as_uuid()
                ),
                body: text.to_string(),
                // Byte offsets (matching the `body.get(start..end)` slice
                // above) — NOT char counts, so `body[start..end]` reproduces
                // the chunk body directly even over multi-byte text.
                source_span: Some(format!("{start}-{end}")),
            })
        })
        .collect()
}

// ===========================================================================
// R1 docs-domain contextual chunks: the resource twin of the episode chunker
// above. `episode_contextual_chunks` windows a conversation over its TURNS; the
// gate ingests docs as `kind=document` resources (one markdown section each,
// 240–3200 chars, first line usually a `#`-heading, fenced code blocks common),
// so this variant windows a resource body over its PARAGRAPHS under a char
// budget. Everything that recall packing + citation touch — the `ContextualChunk`
// fields, the `chunk-{parent}-{index}` id shape, byte-offset `source_span`, and
// the "emit nothing for a single window" bloat guard — is copied verbatim from
// the episode chunker so resource and episode chunks flow through the read path
// identically. The parent whole-section unit stays stored verbatim; chunks are
// additive retrieval keys + pack content.
//
// DEVIATION FROM BRIEF (recorded in the task report): the brief asked for a
// 1-paragraph window overlap, but the PROMOTED episode twin partitions
// NON-overlapping (`spans.chunks(window_size)`). Per the mirror-the-twin rule
// (consistency with the promoted machinery wins) these windows are
// non-overlapping too: disjoint `source_span`s and the same ±1 sibling-adjacency
// semantics the read path's chunk-render expansion assumes.
// ===========================================================================

/// Resource-chunk char budget. Windows aim for `TARGET_MIN..=TARGET_MAX` chars of
/// markdown, may stretch to `HARD_MAX` to avoid splitting a paragraph, and never
/// split a paragraph/fenced block (an oversized single paragraph becomes its own
/// window). Char budgets (not the episode chunker's fixed turn count) because doc
/// paragraphs vary far more in length than conversational turns.
const RESOURCE_CHUNK_TARGET_MIN_CHARS: usize = 700;
const RESOURCE_CHUNK_TARGET_MAX_CHARS: usize = 1100;
const RESOURCE_CHUNK_HARD_MAX_CHARS: usize = 1600;

/// Byte spans of `body`'s paragraphs, split on blank-line boundaries but NEVER
/// inside a fenced code block (```` ``` ````/`~~~`): a blank line inside an open
/// fence stays part of the current paragraph. Mirrors `segment_episode_body`'s
/// offset bookkeeping (byte offsets via `split_inclusive('\n')`; a span's content
/// bounds exclude the trailing newline) so the downstream span math is identical.
fn segment_resource_paragraphs(body: &str) -> Vec<(usize, usize)> {
    let mut paras: Vec<(usize, usize)> = Vec::new();
    let mut current: Option<(usize, usize)> = None;
    let mut in_fence = false;
    let mut offset = 0usize;
    for raw in body.split_inclusive('\n') {
        let line_start = offset;
        offset += raw.len();
        let content = raw.trim_end_matches(['\n', '\r']);
        let is_fence = {
            let trimmed = content.trim_start();
            trimmed.starts_with("```") || trimmed.starts_with("~~~")
        };
        // A blank line OUTSIDE a fence closes the current paragraph. Inside an
        // open fence a blank line is ordinary fence content (never a boundary).
        if content.trim().is_empty() && !in_fence {
            if let Some(span) = current.take() {
                paras.push(span);
            }
            continue;
        }
        let content_end = line_start + content.len();
        match current.as_mut() {
            Some(span) => span.1 = content_end,
            None => current = Some((line_start, content_end)),
        }
        if is_fence {
            in_fence = !in_fence;
        }
    }
    if let Some(span) = current.take() {
        paras.push(span);
    }
    paras
        .into_iter()
        .flat_map(|(start, end)| split_oversized_resource_span(body, start, end))
        .collect()
}

/// Preserves a paragraph as one semantic segment when it fits the hard cap.
/// Pathological DOM/accessibility-tree lines and oversized fenced blocks are
/// split losslessly: prefer a whitespace boundary near the target size, then
/// fall back to an exact UTF-8 character boundary at the hard cap. Source byte
/// spans remain gapless inside the original paragraph.
fn split_oversized_resource_span(body: &str, start: usize, end: usize) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    let mut cursor = start;
    while cursor < end {
        let remaining = &body[cursor..end];
        let Some((hard_offset, _)) = remaining.char_indices().nth(RESOURCE_CHUNK_HARD_MAX_CHARS)
        else {
            spans.push((cursor, end));
            break;
        };
        let hard_end = cursor + hard_offset;
        let target_end = remaining
            .char_indices()
            .nth(RESOURCE_CHUNK_TARGET_MAX_CHARS)
            .map_or(hard_end, |(offset, _)| cursor + offset);
        let preferred = body[cursor..target_end]
            .char_indices()
            .enumerate()
            .filter_map(|(character_index, (offset, character))| {
                (character_index >= RESOURCE_CHUNK_TARGET_MIN_CHARS && character.is_whitespace())
                    .then_some(cursor + offset + character.len_utf8())
            })
            .last();
        let split = preferred.unwrap_or(hard_end);
        debug_assert!(split > cursor && split <= end && body.is_char_boundary(split));
        spans.push((cursor, split));
        cursor = split;
    }
    spans
}

/// Groups paragraph spans into non-overlapping char-budget windows (inclusive
/// `(first_para, last_para)` index pairs). Greedily grows a window until adding
/// the next paragraph would exceed `TARGET_MAX` (stretching only while the window
/// is still under `TARGET_MIN`, and never past `HARD_MAX`), then a tiny trailing
/// window is merged back into its predecessor when the merge fits `HARD_MAX`.
fn window_resource_paragraphs(body: &str, paras: &[(usize, usize)]) -> Vec<(usize, usize)> {
    let char_len = |start: usize, end: usize| {
        body.get(start..end)
            .map_or(0, |slice| slice.chars().count())
    };
    let mut windows: Vec<(usize, usize)> = Vec::new();
    let mut i = 0usize;
    while i < paras.len() {
        let start_byte = paras[i].0;
        let mut end_idx = i;
        while end_idx + 1 < paras.len() {
            let current = char_len(start_byte, paras[end_idx].1);
            if current >= RESOURCE_CHUNK_TARGET_MAX_CHARS {
                break;
            }
            let with_next = char_len(start_byte, paras[end_idx + 1].1);
            if with_next > RESOURCE_CHUNK_HARD_MAX_CHARS {
                break;
            }
            // Add the next paragraph when it keeps us within target, or when the
            // window is still below the minimum (merge-small, bounded by HARD_MAX
            // above).
            if with_next <= RESOURCE_CHUNK_TARGET_MAX_CHARS
                || current < RESOURCE_CHUNK_TARGET_MIN_CHARS
            {
                end_idx += 1;
            } else {
                break;
            }
        }
        windows.push((i, end_idx));
        i = end_idx + 1;
    }
    // Merge a tiny trailing window into its predecessor (brief: "merge tiny
    // trailing paragraphs") when the merged span stays within the hard cap.
    if windows.len() >= 2 {
        let last = windows[windows.len() - 1];
        let prev = windows[windows.len() - 2];
        let last_chars = char_len(paras[last.0].0, paras[last.1].1);
        let merged_chars = char_len(paras[prev.0].0, paras[last.1].1);
        if last_chars < RESOURCE_CHUNK_TARGET_MIN_CHARS
            && merged_chars <= RESOURCE_CHUNK_HARD_MAX_CHARS
        {
            let n = windows.len();
            windows[n - 2] = (prev.0, last.1);
            windows.pop();
        }
    }
    windows
}

/// The chunk provenance header for a document resource: the section's own first
/// markdown heading line (the gate ingests each section starting with its `###`
/// heading), falling back to the uri stem when there is no heading. The
/// docs-domain analog of the episode chunk's `[session ...]` context header — it
/// gives every retrieval-key chunk its section identity.
fn resource_chunk_header(body: &str, uri: &str) -> String {
    if let Some(heading) = body
        .lines()
        .map(str::trim)
        .find(|line| line.starts_with('#'))
    {
        return heading.to_string();
    }
    let stem = uri.rsplit(['/', '\\']).next().unwrap_or(uri);
    let stem = stem.split(['?', '#']).next().unwrap_or(stem);
    let stem = stem.rsplit_once('.').map_or(stem, |(base, _)| base);
    if stem.is_empty() {
        "resource".to_string()
    } else {
        stem.to_string()
    }
}

/// Splits a `kind=document` resource `body` into non-overlapping char-budget
/// windows and mints one `ContextualChunk` per window, each tied back to its
/// parent resource — the docs-domain twin of `episode_contextual_chunks`. Emits
/// nothing when the body fits a single window (a lone chunk would just duplicate
/// the unit body) and never emits empty-body chunks. Unlike bounded
/// conversational episodes, document resources are not capped at 32 windows:
/// silently dropping a large document's tail is a correctness and citation
/// failure. Ingestion adapters should still split large documents into
/// resource-sized units so retrieval fan-out stays bounded.
/// Test-only seam: mint resource chunks for an arbitrary body so the span/body
/// invariant the compiler enforces can be property-tested directly.
#[doc(hidden)]
pub fn resource_chunks_for_test(body: &str) -> Vec<ContextualChunk> {
    resource_contextual_chunks(ResourceId::new(), "test://doc.md", body)
}

fn resource_contextual_chunks(
    resource_id: ResourceId,
    uri: &str,
    body: &str,
) -> Vec<ContextualChunk> {
    let paras = segment_resource_paragraphs(body);
    let windows = window_resource_paragraphs(body, &paras);
    if windows.len() <= 1 {
        return Vec::new();
    }
    let header = resource_chunk_header(body, uri);
    windows
        .into_iter()
        .enumerate()
        .filter_map(|(window_index, (first_para, last_para))| {
            let start = paras[first_para].0;
            let end = paras[last_para].1;
            let text = body.get(start..end)?;
            if text.trim().is_empty() {
                return None;
            }
            Some(ContextualChunk {
                id: format!("chunk-{}-{window_index}", resource_id.as_uuid()),
                header: header.clone(),
                body: text.to_string(),
                // Byte offsets (matching the `body.get(start..end)` slice) — NOT
                // char counts — so `body[start..end]` reproduces the chunk body
                // verbatim, the provenance span-grading invariant (identical to
                // the episode chunk spans).
                source_span: Some(format!("{start}-{end}")),
            })
        })
        .collect()
}

/// Build the exact resource evidence slices used by structured reflection.
/// Census callers use this pure seam so request planning cannot drift from the
/// production compiler's paragraph/span contract.
pub fn structured_state_slices_for_resource(
    body: &str,
) -> Result<Vec<crate::EvidenceSlice>, crate::StructuredStateProviderError> {
    crate::evidence_slices_for_resource(
        body,
        &resource_contextual_chunks(ResourceId::new(), "census://resource", body),
    )
}

// ===========================================================================
// W6 deterministic fact extraction (preference/attribute mining at reflect).
//
// v1 is a hand-rolled, clock-free, LLM-free pattern miner (canonical plan: NO
// LLM in the write path). It scans an episode's USER turns for first-person
// preference/attribute statements and emits SHORT, embeddable ReflectCandidates
// with HONEST subject keys — the lever the single-session-preference stratum
// needs, and the fix for the opaque-content-hash keys that starve supersedence.
//
// PRECISION over recall (§6): a noisy fact index poisons packs, so every rule is
// conservative — demonstrative/pronoun objects and conversational meta nouns
// ("my point is", "I like that idea") are dropped, and the ambiguous "I'm a
// <desc>" family is keyed by its own description (each a standalone fact that
// only an exact repeat or an explicit negation supersedes) rather than guessing
// a shared occupation/identity slot. The cost is missed updates on that family;
// the win is never wrongly superseding "I'm a teacher" with "I'm a vegetarian".
//
// Patterns are hand-rolled rather than regex: core carries no regex dependency
// and the v1 shapes (fixed trigger phrases + token scans) stay readable without
// one (KISS). An LLM extractor is a later experiment behind this same seam.
// ===========================================================================

/// Per-episode hard cap on extracted facts (§3 bloat guard). An episode dense
/// with first-person statements keeps only the most recent `MAX_EXTRACTED_FACTS`
/// — a noisy fact index costs more recall than it earns.
const MAX_EXTRACTED_FACTS: usize = 8;

/// Minimum word count for a mineable sentence (§3). Sub-4-word fragments
/// ("I love it", "my bad") are almost always conversational noise, not durable
/// facts; counted on the raw sentence, before any date prefix.
const MIN_FACT_SENTENCE_WORDS: usize = 4;

/// A deterministic preference/attribute fact mined from one episode body. The
/// `family`/`subject_phrase` pair becomes the honest subject key
/// (`{scope}:{family}:{subject_phrase}`, via `derive_fact_key`) so the SAME
/// subject in a later episode supersedes; `body` is the verbatim source sentence
/// (optionally `[date ...]`-prefixed).
#[derive(Debug, Clone, PartialEq, Eq)]
struct ExtractedFact {
    family: &'static str,
    subject_phrase: String,
    body: String,
}

/// Pronoun / demonstrative objects that read as conversational filler, never a
/// durable subject ("I love it", "my favorite is that", "I like these").
const PRONOUN_STOPS: &[&str] = &[
    "it",
    "that",
    "this",
    "these",
    "those",
    "them",
    "they",
    "he",
    "she",
    "him",
    "her",
    "you",
    "us",
    "me",
    "myself",
    "one",
    "ones",
    "everything",
    "anything",
    "something",
    "nothing",
    "everyone",
    "anyone",
    "someone",
    "itself",
    "here",
    "there",
    "who",
    "what",
    "which",
];

/// Meta / conversational nouns for the "my <noun> is <value>" rule — these are
/// discourse moves, not attributes ("my point is", "my question is").
const NOUN_STOPS: &[&str] = &[
    "point",
    "question",
    "guess",
    "concern",
    "answer",
    "goal",
    "issue",
    "problem",
    "idea",
    "opinion",
    "view",
    "take",
    "understanding",
    "assumption",
    "mistake",
    "bad",
    "apologies",
    "sense",
    "hope",
    "plan",
    "thought",
    "thoughts",
    "feeling",
    "feelings",
    "response",
    "reply",
    "suggestion",
    "recommendation",
    "advice",
    "impression",
    "worry",
    "fear",
];

/// Description heads that follow "I'm a/an" but signal filler, not identity
/// ("I'm a bit tired", "I'm a huge fan", "I'm a little confused").
const IDENTITY_STOPS: &[&str] = &[
    "bit", "little", "tad", "lot", "fan", "huge", "big", "couple", "few", "sort", "kind", "loyal",
];

/// Tokens skipped between the first-person "I" and a preference verb (negation
/// and hedges) so "I don't really like X" and "I like X" find the same verb.
const PREF_FILLERS: &[&str] = &[
    "do",
    "don't",
    "dont",
    "did",
    "didn't",
    "not",
    "never",
    "really",
    "no",
    "longer",
    "just",
    "also",
    "still",
    "actually",
    "totally",
    "absolutely",
    "genuinely",
    "truly",
    "simply",
    "so",
    "much",
    "always",
    "generally",
    "usually",
    "kinda",
    "sort",
    "of",
];

/// Single-word preference verbs. Polarity (love vs hate) lives in the body, not
/// the key, so a reversal supersedes/contradicts the prior fact.
const PREF_VERBS: &[&str] = &[
    "love",
    "loved",
    "like",
    "liked",
    "prefer",
    "preferred",
    "enjoy",
    "enjoyed",
    "hate",
    "hated",
    "dislike",
    "disliked",
    "adore",
    "adored",
    "fancy",
];

/// Trailing temporal/update words stripped from an object phrase so the key is
/// stable across "I like coffee", "I like coffee now", "...coffee anymore".
const OBJECT_TRAILERS: &[&str] = &[
    "anymore",
    "now",
    "today",
    "currently",
    "lately",
    "recently",
    "nowadays",
    "days",
    "these",
    "any",
    "more",
    "longer",
    "too",
    "though",
    "either",
    "here",
];

/// Clause-boundary words: an object/desc/noun phrase ends before the first of
/// these so "I love hiking but hate crowds" keys on "hiking", not the whole tail.
const CLAUSE_STOPS: &[&str] = &[
    "and", "but", "because", "so", "although", "though", "however", "yet", "while", "whereas",
];

/// Strips leading/trailing punctuation from a token, keeping inner apostrophes
/// (so "don't" / "can't" survive intact for verb matching).
fn strip_punct(token: &str) -> &str {
    token.trim_matches(|c: char| !c.is_ascii_alphanumeric() && c != '\'')
}

/// Splits a turn's content into sentences on terminal `.`/`!`/`?`. A deterministic
/// extension of the line/turn splitting — NOT a second word tokenizer. All three
/// delimiters are single-byte ASCII, so the byte slices land on char boundaries
/// even over multi-byte content.
fn split_sentences(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0usize;
    for (i, byte) in text.bytes().enumerate() {
        if matches!(byte, b'.' | b'!' | b'?') {
            let sentence = text[start..i].trim();
            if !sentence.is_empty() {
                out.push(sentence);
            }
            start = i + 1;
        }
    }
    let tail = text[start..].trim();
    if !tail.is_empty() {
        out.push(tail);
    }
    out
}

/// Trims a token slice at the first clause boundary, returning the leading
/// tokens (may be empty).
fn clause_trim<'a>(tokens: &[&'a str]) -> Vec<&'a str> {
    let mut out = Vec::new();
    for token in tokens {
        let word = strip_punct(token);
        if CLAUSE_STOPS.contains(&word) {
            break;
        }
        out.push(*token);
        // A trailing comma/semicolon ends the clause after including this word.
        if token.ends_with([',', ';', ':']) {
            break;
        }
    }
    out
}

/// Cleans a preference object phrase into a stable normalized key half, or
/// `None` when it is empty or a bare pronoun/demonstrative (§6).
fn clean_object(tokens: &[&str]) -> Option<String> {
    let mut words: Vec<&str> = clause_trim(tokens)
        .iter()
        .map(|token| strip_punct(token))
        .filter(|word| !word.is_empty())
        .collect();
    // Drop a single leading article so "I love the ocean" / "I hate the ocean"
    // share `preference:ocean`.
    if matches!(words.first().copied(), Some("a" | "an" | "the")) {
        words.remove(0);
    }
    // Drop trailing temporal/update words.
    while matches!(words.last().copied(), Some(word) if OBJECT_TRAILERS.contains(&word)) {
        words.pop();
    }
    let first = *words.first()?;
    if PRONOUN_STOPS.contains(&first) {
        return None;
    }
    let phrase = normalize_component(&words.join(" "));
    (!phrase.is_empty()).then_some(phrase)
}

/// Normalizes a noun/desc phrase (subject-side), rejecting empties and bare
/// pronouns; unlike `clean_object` it keeps leading articles out by caller
/// choice and never strips trailers (subjects are not temporal).
fn clean_subject_phrase(tokens: &[&str]) -> Option<String> {
    let words: Vec<&str> = clause_trim(tokens)
        .iter()
        .map(|token| strip_punct(token))
        .filter(|word| !word.is_empty())
        .collect();
    let first = *words.first()?;
    if PRONOUN_STOPS.contains(&first) {
        return None;
    }
    let phrase = normalize_component(&words.join(" "));
    (!phrase.is_empty()).then_some(phrase)
}

/// Matches ONE fact in a single sentence, first rule wins (superlative before
/// the generic "my <noun> is" so "favorite" lands in the preference namespace).
/// `None` when the sentence is too short or matches no v1 pattern.
fn match_fact(sentence: &str) -> Option<ExtractedFact> {
    if sentence.split_whitespace().count() < MIN_FACT_SENTENCE_WORDS {
        return None;
    }
    let lower = sentence.to_ascii_lowercase();
    let tokens: Vec<&str> = lower.split_whitespace().collect();
    let (family, subject_phrase) = match_superlative(&tokens)
        .or_else(|| match_my_new_noun(&tokens))
        .or_else(|| match_my_noun_is(&tokens))
        .or_else(|| match_identity(&tokens))
        .or_else(|| match_preference_verb(&tokens))?;
    Some(ExtractedFact {
        family,
        subject_phrase,
        body: sentence.to_string(),
    })
}

/// "my [all-time] favorite <noun> is <value>" → `preference:favorite <noun>`
/// (the value is deliberately OUT of the key so a later value supersedes).
fn match_superlative(tokens: &[&str]) -> Option<(&'static str, String)> {
    let fav = tokens
        .iter()
        .position(|token| strip_punct(token) == "favorite")?;
    // The word(s) before "favorite" must root it in "my [all-time] favorite".
    let before =
        |offset: usize| -> Option<&str> { fav.checked_sub(offset).map(|i| strip_punct(tokens[i])) };
    let rooted = before(1) == Some("my")
        || (before(1) == Some("all-time") && before(2) == Some("my"))
        || (before(1) == Some("time") && before(2) == Some("all") && before(3) == Some("my"));
    if !rooted {
        return None;
    }
    let is_at = tokens
        .iter()
        .enumerate()
        .skip(fav + 1)
        .find(|(_, token)| strip_punct(token) == "is")
        .map(|(index, _)| index)?;
    let noun = clean_subject_phrase(&tokens[fav + 1..is_at])?;
    // A value must follow, and it must not be a bare pronoun.
    let _value = clean_subject_phrase(&tokens[is_at + 1..])?;
    Some(("preference", format!("favorite {noun}")))
}

/// "my new <noun> is <value>" → `attribute:<noun>` (shares the key of the plain
/// "my <noun> is <value>" so an explicit update supersedes).
fn match_my_new_noun(tokens: &[&str]) -> Option<(&'static str, String)> {
    let my = tokens.iter().position(|token| strip_punct(token) == "my")?;
    if strip_punct(tokens.get(my + 1)?) != "new" {
        return None;
    }
    let is_at = tokens
        .iter()
        .enumerate()
        .skip(my + 2)
        .find(|(_, token)| strip_punct(token) == "is")
        .map(|(index, _)| index)?;
    let noun = clean_subject_phrase(&tokens[my + 2..is_at])?;
    let _value = clean_subject_phrase(&tokens[is_at + 1..])?;
    (!NOUN_STOPS.contains(&noun.as_str())).then_some(("attribute", noun))
}

/// "my <noun> is <value>" → `attribute:<noun>`, dropping meta/discourse nouns.
fn match_my_noun_is(tokens: &[&str]) -> Option<(&'static str, String)> {
    let my = tokens.iter().position(|token| strip_punct(token) == "my")?;
    let is_at = tokens
        .iter()
        .enumerate()
        .skip(my + 1)
        .find(|(_, token)| strip_punct(token) == "is")
        .map(|(index, _)| index)?;
    let noun_tokens = &tokens[my + 1..is_at];
    // Keep the noun tight (1..=3 words) so "my <clause> is" prose doesn't key.
    if noun_tokens.is_empty() || noun_tokens.len() > 3 {
        return None;
    }
    let noun = clean_subject_phrase(noun_tokens)?;
    let _value = clean_subject_phrase(&tokens[is_at + 1..])?;
    // Reject any meta noun (checked per word so "only point" is caught too).
    if noun.split(' ').any(|word| NOUN_STOPS.contains(&word)) {
        return None;
    }
    Some(("attribute", noun))
}

/// "I am a/an <desc>" / "I'm a/an <desc>" (incl. negated "not a/an") →
/// `attribute:<desc>`. Keyed by the description itself: two different identities
/// coexist, and only an exact repeat or an explicit negation supersedes.
fn match_identity(tokens: &[&str]) -> Option<(&'static str, String)> {
    // Locate the "I'm" / "I am" anchor and the token index just after it.
    let mut after_pronoun = None;
    for (i, token) in tokens.iter().enumerate() {
        match strip_punct(token) {
            "i'm" => {
                after_pronoun = Some(i + 1);
                break;
            }
            "i" if strip_punct(tokens.get(i + 1).copied().unwrap_or("")) == "am" => {
                after_pronoun = Some(i + 2);
                break;
            }
            _ => {}
        }
    }
    let mut idx = after_pronoun?;
    // Skip a negation/hedge ("not", "no longer", "really").
    while matches!(
        strip_punct(tokens.get(idx).copied().unwrap_or("")),
        "not" | "no" | "longer" | "really" | "actually" | "also" | "still"
    ) {
        idx += 1;
    }
    // Require an article: "a"/"an" — this filters bare "I am happy" adjectives.
    if !matches!(
        strip_punct(tokens.get(idx).copied().unwrap_or("")),
        "a" | "an"
    ) {
        return None;
    }
    let desc_tokens = &tokens[idx + 1..];
    if desc_tokens.is_empty() {
        return None;
    }
    let desc = clean_subject_phrase(desc_tokens)?;
    let head = desc.split(' ').next().unwrap_or("");
    if IDENTITY_STOPS.contains(&head) {
        return None;
    }
    // Keep the description tight (1..=4 words).
    if desc.split(' ').count() > 4 {
        return None;
    }
    Some(("attribute", desc))
}

/// Preference verbs (incl. multi-word "can't stand", "switched to") → the
/// `preference:<object>` key, with negation/hedges skipped between "I" and the
/// verb so update phrasings share the positive assertion's key.
fn match_preference_verb(tokens: &[&str]) -> Option<(&'static str, String)> {
    for (i, token) in tokens.iter().enumerate() {
        if strip_punct(token) != "i" {
            continue;
        }
        let mut j = i + 1;
        while j < tokens.len() && PREF_FILLERS.contains(&strip_punct(tokens[j])) {
            j += 1;
        }
        if j >= tokens.len() {
            continue;
        }
        let verb = strip_punct(tokens[j]);
        let next = tokens.get(j + 1).map(|token| strip_punct(token));
        // Multi-word verbs ("can't stand", "switched to") consume two tokens.
        let verb_end = if (matches!(verb, "can't" | "cannot" | "can") && next == Some("stand"))
            || (verb == "switched" && next == Some("to"))
        {
            j + 2
        } else if PREF_VERBS.contains(&verb) {
            j + 1
        } else {
            // This "I" did not front a preference verb (e.g. "When I travel I
            // love X"); try the next "I" rather than giving up on the sentence.
            continue;
        };
        // A verb was found: its object decides the fact. A bad (pronoun) object
        // rejects the sentence outright (precision) — we do not hunt further.
        return clean_object(&tokens[verb_end..]).map(|object| ("preference", object));
    }
    None
}

/// Mines an episode body for W6 facts: scans USER turns (and non-role prose)
/// sentence-by-sentence, dedups by subject keeping the LAST occurrence (later
/// turns win), caps at `MAX_EXTRACTED_FACTS` keeping the most recent, and bakes
/// the `[date ...]` prefix into each body when `content_date` is supplied.
fn extract_facts(body: &str, content_date: Option<&str>) -> Vec<ExtractedFact> {
    let mut found: Vec<ExtractedFact> = Vec::new();
    for line in body.split_inclusive('\n') {
        let content = line.trim();
        if content.is_empty() {
            continue;
        }
        // §3: only user turns are mined. A role-prefixed line with any role
        // other than "user" (assistant/system/tool) is skipped; a non-role prose
        // line is treated as user-authored text.
        let text = match parse_turn(content) {
            Some((role, turn)) => {
                if normalize_component(role) != "user" {
                    continue;
                }
                turn
            }
            None => content,
        };
        for sentence in split_sentences(text) {
            if let Some(fact) = match_fact(sentence) {
                found.push(fact);
            }
        }
    }

    // Within-episode dedup by subject, keeping the LAST occurrence and its
    // position (later turns win), then cap to the most recent.
    let mut deduped: Vec<ExtractedFact> = Vec::new();
    for fact in found {
        if let Some(pos) = deduped.iter().position(|kept| {
            kept.family == fact.family && kept.subject_phrase == fact.subject_phrase
        }) {
            deduped.remove(pos);
        }
        deduped.push(fact);
    }
    if deduped.len() > MAX_EXTRACTED_FACTS {
        deduped.drain(0..deduped.len() - MAX_EXTRACTED_FACTS);
    }

    if let Some(date) = content_date {
        for fact in &mut deduped {
            fact.body = format!("[date {date}]\n{}", fact.body);
        }
    }
    deduped
}

/// Turns mined facts into extra ReflectCandidates for one episode: honest
/// `subject`/`predicate` (→ the `{scope}:{family}:{phrase}` key), the parent
/// episode's trust/actor so citations and admission are unchanged, NO contextual
/// chunks (§3), and NO `valid_from` (the date is baked into the body prefix
/// instead, so recall's dated-pack pass never double-prefixes).
fn extract_fact_candidates(
    episode: &StoredEpisode,
    content_date: Option<&str>,
) -> Vec<ReflectCandidate> {
    extract_facts(&episode.body, content_date)
        .into_iter()
        .map(|fact| ReflectCandidate {
            source_kind: episode.source_kind.clone(),
            trust_level: episode.source_trust,
            actor_id: episode.actor_id,
            subject: Some(fact.family.to_string()),
            predicate: Some(fact.subject_phrase),
            fact_key: None,
            kind: None,
            body: fact.body,
            confidence: None,
            churn_class: None,
            admission_hint: None,
            target_unit_ids: None,
            contextual_chunks: Vec::new(),
            valid_from: None,
            valid_to: None,
        })
        .collect()
}

/// Degraded read-your-own-writes items: raw episode bodies lexically matched
/// against the query. They are deliberately uncited and excluded from the
/// candidate whitelist because the canonical trace did not admit them.
fn degraded_episode_items(
    episodes: &[StoredEpisode],
    query: &str,
    k: usize,
) -> Vec<RecallContextItem> {
    let query_tokens = tokenize(query);
    let mut scored: Vec<(&StoredEpisode, f32)> = episodes
        .iter()
        .filter_map(|episode| {
            let score = crate::token_set_overlap_text_score(&episode.body, &query_tokens);
            (score > 0.0).then_some((episode, score))
        })
        .collect();
    scored.sort_by(|left, right| {
        right
            .1
            .partial_cmp(&left.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.0.body.cmp(&right.0.body))
            // Total-order tie-break: `dedup_key` is unique per (tenant, scope),
            // so same-body/same-score episodes still cite deterministically.
            .then_with(|| left.0.dedup_key.cmp(&right.0.dedup_key))
    });
    scored
        .into_iter()
        .take(k)
        .map(|(episode, _)| RecallContextItem {
            unit_id: unit_id_for_episode(episode.id),
            body: episode.body.clone(),
            kind: MemoryKind::Episodic,
            derived_by: "raw_episode".to_string(),
            inclusion_reason: "degraded_read_your_own_writes".to_string(),
            citation_episode_id: None,
            citation_resource_id: None,
            derived_from_unit_ids: Vec::new(),
            suppression_labels: Vec::new(),
            // D1: no handle. `unit_id` here is synthesised from the episode id
            // because consolidation has not run — there is no stored unit, no
            // fact key and no generation to correct. A handle would name a row
            // that does not exist.
            correction: None,
        })
        .collect()
}

/// Deterministic synthetic unit id for a degraded raw-episode item (there is
/// no compiled unit yet; the id mirrors the episode identity).
fn unit_id_for_episode(episode_id: EpisodeId) -> UnitId {
    UnitId::from_u128(episode_id.as_uuid().as_u128())
}

/// A minimal `catch_unwind` future adapter (std-only; core has no futures
/// dependency). Job compilation panics must not take down the worker loop.
struct CatchUnwind<F> {
    inner: F,
}

impl<F> CatchUnwind<F> {
    fn new(inner: F) -> Self {
        Self { inner }
    }
}

impl<F: Future> Future for CatchUnwind<F> {
    type Output = Result<F::Output, ()>;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        // SAFETY: structural pin projection to the only field; `inner` is
        // never moved out of the pinned wrapper.
        let inner = unsafe { self.map_unchecked_mut(|this| &mut this.inner) };
        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| inner.poll(cx))) {
            Ok(Poll::Ready(output)) => Poll::Ready(Ok(output)),
            Ok(Poll::Pending) => Poll::Pending,
            Err(_) => Poll::Ready(Err(())),
        }
    }
}

#[cfg(test)]
mod chunk_tests {
    use super::*;

    /// Builds a turn-structured body of `turn_count` `user: ...` turns, for
    /// the W9 window-growth tests below (a leading provenance line, exactly
    /// like every other body in this module, so it exercises the same
    /// turn-structured segmentation path).
    fn turn_body(turn_count: usize) -> String {
        let mut body = String::from("[session s1] [date 2023/05/30]\n");
        for turn in 0..turn_count {
            body.push_str(&format!("user: turn number {turn} here.\n"));
        }
        body
    }

    /// Extracts the `a-b` range out of a chunk header's trailing
    /// `[label a-b]` slot (`label` is `"turns"` or `"segments"`) — no regex
    /// dependency, matching the hand-rolled-parsing rationale used elsewhere
    /// in this module.
    fn parse_span_range(header: &str, label: &str) -> (usize, usize) {
        let marker = format!("[{label} ");
        let start = header.rfind(&marker).expect("span slot present") + marker.len();
        let rest = &header[start..];
        let end = rest.find(']').expect("span slot closes");
        let (first, last) = rest[..end].split_once('-').expect("span is a-b");
        (
            first.parse().expect("first is numeric"),
            last.parse().expect("last is numeric"),
        )
    }

    /// `role: content` bodies window over turns with a `[turns a-b]` header
    /// and exact byte-offset spans over the parent body.
    #[test]
    fn turn_structured_body_windows_over_turns() {
        let episode_id = EpisodeId::new();
        let body = "[session s1] [date 2023/05/30]\n\
user: a b c.\n\
assistant: d e f.\n\
user: g h i.\n\
assistant: j k l.\n\
user: m n o.\n";
        let chunks = episode_contextual_chunks(episode_id, "user", body, None);
        assert_eq!(chunks.len(), 2, "five turns / window 4 → two windows");

        let uuid = episode_id.as_uuid();
        assert_eq!(
            chunks[0].header,
            format!("[episode {uuid}] [kind user] [turns 1-4]")
        );
        assert_eq!(
            chunks[1].header,
            format!("[episode {uuid}] [kind user] [turns 5-5]")
        );

        // Spans are byte offsets of the window within the body; the body
        // slice at that span equals the chunk body (ASCII → byte == char).
        let first_start = body.find("user: a b c.").unwrap();
        let fourth = "assistant: j k l.";
        let first_end = body.find(fourth).unwrap() + fourth.len();
        assert_eq!(
            chunks[0].source_span,
            Some(format!("{first_start}-{first_end}"))
        );
        assert_eq!(chunks[0].body, &body[first_start..first_end]);
        assert!(chunks[0].body.starts_with("user: a b c."));
        assert!(chunks[0].body.ends_with("assistant: j k l."));
    }

    /// W5 §1: a parsed content date is stamped into the chunk header `[date ...]`
    /// slot (between kind and the span label), using the TRUE date, never the
    /// clock. `None` (the default and the flag-off path) keeps the header
    /// dateless — every existing header assertion above exercises that case.
    #[test]
    fn dated_header_stamps_content_date_between_kind_and_span() {
        let episode_id = EpisodeId::new();
        let body = "[session s1] [date 2023/05/30]\n\
user: a b c.\n\
assistant: d e f.\n\
user: g h i.\n\
assistant: j k l.\n\
user: m n o.\n";
        let uuid = episode_id.as_uuid();
        let chunks = episode_contextual_chunks(episode_id, "user", body, Some("2023-05-30"));
        assert_eq!(chunks.len(), 2);
        assert_eq!(
            chunks[0].header,
            format!("[episode {uuid}] [kind user] [date 2023-05-30] [turns 1-4]")
        );
        assert_eq!(
            chunks[1].header,
            format!("[episode {uuid}] [kind user] [date 2023-05-30] [turns 5-5]")
        );
    }

    /// Non-turn prose falls back to line segments with a `[segments a-b]` label.
    #[test]
    fn non_turn_body_falls_back_to_line_segments() {
        let episode_id = EpisodeId::new();
        let body = "Line one about apples.\n\
Line two about oranges.\n\
Line three about pears.\n\
Line four about grapes.\n\
Line five about kiwis.\n";
        let chunks = episode_contextual_chunks(episode_id, "doc", body, None);
        assert_eq!(chunks.len(), 2, "five lines / window 4 → two windows");
        assert!(
            chunks[0].header.contains("[segments 1-4]"),
            "fallback labels windows as segments: {}",
            chunks[0].header
        );
        assert!(chunks[1].header.contains("[segments 5-5]"));
        assert!(chunks[0].body.starts_with("Line one about apples."));
    }

    #[test]
    fn episode_chunk_span_preserves_edge_whitespace_verbatim() {
        let episode_id = EpisodeId::new();
        let body = "  Line one keeps leading spaces.\n\
Line two.\n\
Line three.\n\
Line four keeps trailing spaces.   \n\
Line five.\n";
        let chunks = episode_contextual_chunks(episode_id, "doc", body, None);
        assert_eq!(chunks.len(), 2);
        for chunk in &chunks {
            let (start, end) = chunk
                .source_span
                .as_deref()
                .and_then(|span| span.split_once('-'))
                .map(|(start, end)| {
                    (
                        start.parse::<usize>().unwrap(),
                        end.parse::<usize>().unwrap(),
                    )
                })
                .unwrap();
            assert_eq!(&body[start..end], chunk.body);
        }
        assert!(chunks[0].body.starts_with("  Line one"));
        assert!(chunks[0].body.ends_with("spaces.   "));
    }

    /// A body that fits a single window would only duplicate the unit body:
    /// emit nothing (bloat guard).
    #[test]
    fn single_window_body_emits_no_chunks() {
        let episode_id = EpisodeId::new();
        let four_turns = "[session s1] [date 2023/05/30]\n\
user: a b c.\n\
assistant: d e f.\n\
user: g h i.\n\
assistant: j k l.\n";
        assert!(episode_contextual_chunks(episode_id, "user", four_turns, None).is_empty());
        // A lone prose line is also a single window.
        assert!(episode_contextual_chunks(episode_id, "doc", "one solitary line", None).is_empty());
        // And an empty body yields nothing.
        assert!(episode_contextual_chunks(episode_id, "doc", "", None).is_empty());
    }

    /// Never mint more than `MAX_CONTEXTUAL_CHUNKS` per episode. W9: this cap
    /// now holds by construction (the window grows to fit), so the same body
    /// that used to have its tail silently dropped once the fixed 4-turn
    /// window hit the 32-chunk cap is now fully covered too.
    #[test]
    fn per_episode_chunk_cap_is_enforced() {
        let episode_id = EpisodeId::new();
        let turns = (MAX_CONTEXTUAL_CHUNKS + 2) * CONTEXTUAL_CHUNK_WINDOW;
        let body = turn_body(turns);
        let chunks = episode_contextual_chunks(episode_id, "user", &body, None);
        assert!(
            chunks.len() <= MAX_CONTEXTUAL_CHUNKS,
            "cap holds: {} chunks for {turns} turns",
            chunks.len()
        );
        assert!(chunks.iter().all(|chunk| !chunk.body.trim().is_empty()));

        let (_, last) = parse_span_range(&chunks.last().unwrap().header, "turns");
        assert_eq!(
            last, turns,
            "last chunk must reach the final turn, not truncate the tail"
        );
    }

    /// W9 property: for any body length, either it fits a single window (no
    /// chunks minted — the pre-existing bloat guard, unaffected by W9) or the
    /// windows are contiguous, start at turn 1, and the last window's `last`
    /// equals the turn count exactly — the whole body is covered, remainder
    /// included, no matter how far past the cap-at-fixed-window threshold
    /// (128 segments) the body runs.
    #[test]
    fn full_body_coverage_property_for_growing_bodies() {
        for &turn_count in &[1usize, 129, 500, 10_000] {
            let episode_id = EpisodeId::new();
            let body = turn_body(turn_count);
            let chunks = episode_contextual_chunks(episode_id, "user", &body, None);

            if turn_count <= CONTEXTUAL_CHUNK_WINDOW {
                assert!(
                    chunks.is_empty(),
                    "n={turn_count}: single-window body mints no chunks"
                );
                continue;
            }

            assert!(
                !chunks.is_empty(),
                "n={turn_count}: multi-window body must mint chunks"
            );
            assert!(
                chunks.len() <= MAX_CONTEXTUAL_CHUNKS,
                "n={turn_count}: cap must hold, got {} chunks",
                chunks.len()
            );

            let mut expected_start = 1usize;
            for (idx, chunk) in chunks.iter().enumerate() {
                let (first, last) = parse_span_range(&chunk.header, "turns");
                assert_eq!(
                    first, expected_start,
                    "n={turn_count}: window {idx} must start where the previous one left off"
                );
                assert!(
                    last >= first,
                    "n={turn_count}: window {idx} range must be non-empty"
                );
                expected_start = last + 1;
            }

            let (_, last) = parse_span_range(&chunks.last().unwrap().header, "turns");
            assert_eq!(
                last, turn_count,
                "n={turn_count}: last window must reach the final turn — no dropped tail"
            );
        }
    }

    /// §2: bodies of ≤128 segments (`MAX_CONTEXTUAL_CHUNKS * CONTEXTUAL_CHUNK_WINDOW`)
    /// must be byte-identical to pre-W9 behavior — the window stays fixed at
    /// `CONTEXTUAL_CHUNK_WINDOW` and every window is exactly that size except
    /// a possible final remainder.
    #[test]
    fn bodies_at_or_under_128_segments_use_fixed_window() {
        for &turn_count in &[5usize, 32, 100, 128] {
            let episode_id = EpisodeId::new();
            let body = turn_body(turn_count);
            let chunks = episode_contextual_chunks(episode_id, "user", &body, None);

            let expected_windows = turn_count.div_ceil(CONTEXTUAL_CHUNK_WINDOW);
            assert_eq!(
                chunks.len(),
                expected_windows,
                "n={turn_count}: window must stay fixed at {CONTEXTUAL_CHUNK_WINDOW} up to 128 segments"
            );
            for (idx, chunk) in chunks.iter().enumerate() {
                let (first, last) = parse_span_range(&chunk.header, "turns");
                let expected_first = idx * CONTEXTUAL_CHUNK_WINDOW + 1;
                let expected_last = (expected_first + CONTEXTUAL_CHUNK_WINDOW - 1).min(turn_count);
                assert_eq!(first, expected_first, "n={turn_count}: window {idx} start");
                assert_eq!(last, expected_last, "n={turn_count}: window {idx} end");
            }
        }
    }

    /// §1: header ranges stay truthful under window growth — a small,
    /// fully-worked example (rather than the property sweep above) so a
    /// failure here points straight at the arithmetic.
    #[test]
    fn header_ranges_truthful_under_growth() {
        let episode_id = EpisodeId::new();
        let turn_count = 129;
        let body = turn_body(turn_count);
        let chunks = episode_contextual_chunks(episode_id, "user", &body, None);

        // window_size = max(4, ceil(129 / 32)) = 5; ceil(129 / 5) = 26 windows.
        assert_eq!(chunks.len(), 26, "129 turns / grown window 5 → 26 windows");
        assert!(chunks.len() <= MAX_CONTEXTUAL_CHUNKS);

        let uuid = episode_id.as_uuid();
        assert_eq!(
            chunks[0].header,
            format!("[episode {uuid}] [kind user] [turns 1-5]")
        );
        assert_eq!(
            chunks[1].header,
            format!("[episode {uuid}] [kind user] [turns 6-10]")
        );
        // Last window carries the 4-turn remainder (129 == 25 * 5 + 4)
        // instead of being dropped.
        assert_eq!(
            chunks[25].header,
            format!("[episode {uuid}] [kind user] [turns 126-129]")
        );
    }

    /// Ids are deterministic in episode id + window index across calls.
    #[test]
    fn chunk_ids_are_deterministic() {
        let episode_id = EpisodeId::new();
        let body = "[session s1] [date 2023/05/30]\n\
user: a b c.\n\
assistant: d e f.\n\
user: g h i.\n\
assistant: j k l.\n\
user: m n o.\n";
        let uuid = episode_id.as_uuid();
        let first = episode_contextual_chunks(episode_id, "user", body, None);
        let second = episode_contextual_chunks(episode_id, "user", body, None);
        let ids: Vec<_> = first.iter().map(|chunk| chunk.id.clone()).collect();
        assert_eq!(
            ids,
            vec![format!("chunk-{uuid}-0"), format!("chunk-{uuid}-1")]
        );
        assert_eq!(
            ids,
            second
                .iter()
                .map(|chunk| chunk.id.clone())
                .collect::<Vec<_>>()
        );
    }

    /// Non-ASCII bodies expose the byte-vs-char bug directly: the reported
    /// `source_span` must be byte offsets so slicing the original body at
    /// that span reproduces the chunk body exactly, even when multi-byte
    /// characters precede the window.
    #[test]
    fn source_span_is_byte_offsets_for_multibyte_body() {
        let episode_id = EpisodeId::new();
        // Turns 1-4 are packed with multi-byte characters (é, ö, 世界, 🎉),
        // so byte offsets and char offsets diverge well before the second
        // window (turn 5) starts; five turns / window 4 → two chunks.
        let body = "user: héllo wörld.\n\
assistant: 世界 reply here.\n\
user: third turn 🎉 emoji.\n\
assistant: fourth turn plain.\n\
user: fifth turn plain.\n";
        let chunks = episode_contextual_chunks(episode_id, "conversation", body, None);
        assert_eq!(chunks.len(), 2, "five turns / window 4 → two windows");

        let chunk = &chunks[1];
        let span = chunk.source_span.as_deref().expect("span present");
        let (start_str, end_str) = span.split_once('-').expect("span is start-end");
        let start: usize = start_str.parse().expect("start is a byte offset");
        let end: usize = end_str.parse().expect("end is a byte offset");

        // Byte offsets differ from char offsets here because turns 1-4 (which
        // precede this window) contain multi-byte characters — this would
        // mis-slice (or, at a non-boundary byte, panic) if the span were
        // still reported in chars.
        assert_ne!(
            start,
            body[..start].chars().count(),
            "test body must actually contain multi-byte offsets before the span"
        );
        assert_eq!(
            &body[start..end],
            chunk.body,
            "slicing the episode body at the reported byte span reproduces the chunk body exactly"
        );
    }
}

#[cfg(test)]
mod resource_chunk_tests {
    use super::*;

    const RESOURCE_ID: u128 = 0x0000_0000_0000_4d3d_0000_0000_0000_0007;

    /// A single-line paragraph of at least `chars` ASCII characters, no trailing
    /// whitespace, tagged so windows/chunks are identifiable.
    fn para(tag: &str, chars: usize) -> String {
        let mut body = format!("{tag}:");
        while body.chars().count() < chars {
            body.push_str(" lorem ipsum dolor sit amet consectetur");
        }
        body
    }

    /// Parses a chunk's `start-end` byte span.
    fn span_of(chunk: &ContextualChunk) -> (usize, usize) {
        let raw = chunk.source_span.as_deref().expect("chunk carries a span");
        let (start, end) = raw.split_once('-').expect("span is start-end");
        (
            start.parse().expect("start is a byte offset"),
            end.parse().expect("end is a byte offset"),
        )
    }

    #[test]
    fn blank_lines_split_paragraphs_outside_fences() {
        let body = "# Title\n\nFirst paragraph here.\n\nSecond paragraph here.\n";
        let paras = segment_resource_paragraphs(body);
        assert_eq!(paras.len(), 3, "heading + two paragraphs: {paras:?}");
        assert_eq!(&body[paras[0].0..paras[0].1], "# Title");
        assert_eq!(&body[paras[1].0..paras[1].1], "First paragraph here.");
        assert_eq!(&body[paras[2].0..paras[2].1], "Second paragraph here.");
    }

    #[test]
    fn fenced_block_is_never_split_on_internal_blank_lines() {
        let body = "# Heading\n\n\
Some intro prose before the code block goes here.\n\n\
```rust\n\
fn foo() {\n\
\n\
    let x = 1;\n\
\n\
    x + 1\n\
}\n\
```\n\n\
Trailing prose after the fence.\n";
        let paras = segment_resource_paragraphs(body);
        // heading, intro, WHOLE fence (one paragraph despite two internal blank
        // lines), trailing prose = 4 paragraphs.
        assert_eq!(paras.len(), 4, "fence stays a single paragraph: {paras:?}");
        let fence = &body[paras[2].0..paras[2].1];
        assert!(
            fence.starts_with("```rust"),
            "fence span starts at the opening fence: {fence:?}"
        );
        assert!(
            fence.contains("let x = 1;") && fence.contains("x + 1"),
            "fence interior (across its blank lines) is intact: {fence:?}"
        );
        assert!(
            fence.ends_with("```"),
            "fence span includes the closing fence: {fence:?}"
        );
    }

    #[test]
    fn windows_are_a_gapless_non_overlapping_partition_within_hard_cap() {
        let body = [
            para("a", 500),
            para("b", 500),
            para("c", 500),
            para("d", 500),
        ]
        .join("\n\n");
        let paras = segment_resource_paragraphs(&body);
        assert_eq!(paras.len(), 4);
        let windows = window_resource_paragraphs(&body, &paras);
        assert!(
            windows.len() >= 2,
            "a ~2k-char body yields multiple windows: {windows:?}"
        );
        // Non-overlapping + gapless: window[k+1] starts exactly one paragraph
        // past window[k]'s inclusive end (mirrors the episode chunker's
        // `spans.chunks(window_size)` partition — the mirror-the-twin deviation
        // from the brief's requested overlap).
        for pair in windows.windows(2) {
            assert_eq!(
                pair[1].0,
                pair[0].1 + 1,
                "windows partition paragraphs without overlap or gaps"
            );
        }
        assert_eq!(windows.first().unwrap().0, 0, "coverage starts at para 0");
        assert_eq!(
            windows.last().unwrap().1,
            paras.len() - 1,
            "coverage reaches the last paragraph (no dropped tail)"
        );
        for &(first, last) in &windows {
            let chars = body[paras[first].0..paras[last].1].chars().count();
            assert!(
                chars <= RESOURCE_CHUNK_HARD_MAX_CHARS,
                "each window stays within the hard cap: {chars} chars"
            );
        }
    }

    #[test]
    fn tiny_trailing_paragraph_merges_into_predecessor() {
        // Two ~1000-char paragraphs (each fills its own window) then a tiny tail.
        // Greedy leaves the tail as a lone sub-min window; the post-pass merges it
        // into its predecessor (the merged span stays within HARD_MAX).
        let body = [
            para("a", 1000),
            para("b", 1000),
            "tiny final note.".to_string(),
        ]
        .join("\n\n");
        let paras = segment_resource_paragraphs(&body);
        assert_eq!(paras.len(), 3);
        let windows = window_resource_paragraphs(&body, &paras);
        assert_eq!(
            windows,
            vec![(0, 0), (1, 2)],
            "the tiny trailing paragraph is folded into its predecessor window"
        );
    }

    #[test]
    fn source_span_reproduces_chunk_body_verbatim_over_multibyte_text() {
        // Leading multi-byte content so later windows sit at byte offsets that
        // differ from char offsets — proving the span is BYTE-based.
        let body = [
            para("café•α", 500),
            para("β• second", 500),
            para("γ•δ third", 500),
            para("δ• fourth", 500),
        ]
        .join("\n\n");
        let chunks =
            resource_contextual_chunks(ResourceId::from_u128(RESOURCE_ID), "doc.md", &body);
        assert!(chunks.len() >= 2, "multi-window body: {}", chunks.len());
        for chunk in &chunks {
            let (start, end) = span_of(chunk);
            assert_eq!(
                &body[start..end],
                chunk.body,
                "slicing the resource body at the reported byte span reproduces the chunk body"
            );
            assert!(
                body.contains(chunk.body.as_str()),
                "chunk body is a verbatim substring of the parent resource body"
            );
        }
        let last = chunks.last().unwrap();
        let (start, _) = span_of(last);
        assert_ne!(
            start,
            body[..start].chars().count(),
            "a later window's byte offset must diverge from its char offset (multi-byte proof)"
        );
    }

    #[test]
    fn oversized_single_paragraph_is_split_without_utf8_or_span_loss() {
        let body = "Outlook🙂calendar,".repeat(400);
        let chunks =
            resource_contextual_chunks(ResourceId::from_u128(RESOURCE_ID), "portal.txt", &body);
        assert!(
            chunks.len() > 1,
            "one oversized paragraph must become bounded evidence"
        );
        let mut previous_end = 0;
        for chunk in &chunks {
            let (start, end) = span_of(chunk);
            assert_eq!(start, previous_end, "hard splits are gapless");
            assert_eq!(&body[start..end], chunk.body, "span stays byte exact");
            assert!(
                chunk.body.chars().count() <= RESOURCE_CHUNK_HARD_MAX_CHARS,
                "every oversized-paragraph slice respects the hard cap"
            );
            previous_end = end;
        }
        assert_eq!(
            previous_end,
            body.len(),
            "the final chunk reaches the source tail"
        );
    }

    #[test]
    fn resource_chunking_never_drops_tail_after_thirty_two_windows() {
        let body = (0..40)
            .map(|index| format!("paragraph-{index} {}", "x".repeat(1580)))
            .collect::<Vec<_>>()
            .join("\n\n");
        let chunks =
            resource_contextual_chunks(ResourceId::from_u128(RESOURCE_ID), "large.md", &body);
        assert!(
            chunks.len() > MAX_CONTEXTUAL_CHUNKS,
            "resource coverage is not episode-capped"
        );
        let (_, end) = span_of(chunks.last().expect("tail chunk"));
        assert_eq!(end, body.len(), "the source tail is represented");
    }

    #[test]
    fn resource_chunk_span_preserves_edge_whitespace_verbatim() {
        let body = [
            format!("  {}", para("first", 800)),
            format!("{}   ", para("second", 800)),
            para("third", 800),
        ]
        .join("\n\n");
        let chunks =
            resource_contextual_chunks(ResourceId::from_u128(RESOURCE_ID), "doc.md", &body);
        assert!(chunks.len() >= 2);
        for chunk in &chunks {
            let (start, end) = span_of(chunk);
            assert_eq!(&body[start..end], chunk.body);
        }
        assert!(chunks[0].body.starts_with("  first:"));
        assert!(chunks.iter().any(|chunk| chunk.body.ends_with("   ")));
    }

    #[test]
    fn single_window_or_short_body_emits_no_chunks() {
        let id = ResourceId::from_u128(RESOURCE_ID);
        // One short section = a single window = no chunks (the whole-section unit
        // is the memory; a lone chunk would just duplicate it).
        assert!(
            resource_contextual_chunks(id, "doc.md", "# Only\n\nOne short paragraph.").is_empty()
        );
        // Empty / whitespace-only body yields nothing.
        assert!(resource_contextual_chunks(id, "doc.md", "").is_empty());
        assert!(resource_contextual_chunks(id, "doc.md", "\n\n   \n").is_empty());
    }

    #[test]
    fn chunks_are_deterministic() {
        let id = ResourceId::from_u128(RESOURCE_ID);
        let body = [
            para("a", 500),
            para("b", 500),
            para("c", 500),
            para("d", 500),
        ]
        .join("\n\n");
        let first = resource_contextual_chunks(id, "doc.md", &body);
        let second = resource_contextual_chunks(id, "doc.md", &body);
        assert_eq!(first, second, "same input yields identical chunks");
        assert!(first.len() >= 2);
    }

    #[test]
    fn header_is_first_heading_then_uri_stem() {
        // First markdown heading wins, verbatim (the gate ingests sections
        // starting with their `###` heading).
        assert_eq!(
            resource_chunk_header("### Config Reference\n\nBody.", "x/config.md"),
            "### Config Reference"
        );
        // No heading → uri stem (path tail without extension/query/fragment).
        assert_eq!(
            resource_chunk_header(
                "Just prose, no heading.",
                "https://d.io/guides/setup.md?v=2"
            ),
            "setup"
        );
        assert_eq!(resource_chunk_header("prose", ""), "resource");
    }

    #[test]
    fn chunk_id_and_header_link_to_parent_resource() {
        let id = ResourceId::from_u128(RESOURCE_ID);
        let body = format!(
            "### Deploy Guide\n\n{}\n\n{}\n\n{}\n\n{}",
            para("a", 500),
            para("b", 500),
            para("c", 500),
            para("d", 500)
        );
        let chunks = resource_contextual_chunks(id, "deploy.md", &body);
        assert!(chunks.len() >= 2);
        let uuid = id.as_uuid();
        for (index, chunk) in chunks.iter().enumerate() {
            assert_eq!(
                chunk.id,
                format!("chunk-{uuid}-{index}"),
                "chunk id derives from the parent resource + window index"
            );
            assert_eq!(
                chunk.header, "### Deploy Guide",
                "every chunk carries the section heading as its context header"
            );
            assert!(!chunk.body.trim().is_empty(), "no empty-body chunks");
        }
    }
}

#[cfg(test)]
mod fact_tests {
    use super::*;

    /// Reduce a matched fact to `(family, subject_phrase)` for table assertions;
    /// `None` when the sentence is a near-miss the extractor rejects.
    fn matched(sentence: &str) -> Option<(&'static str, String)> {
        match_fact(sentence).map(|fact| (fact.family, fact.subject_phrase))
    }

    /// §5 pattern table: deterministic hits across all four v1 families, plus the
    /// §6 near-miss rejections (conversational false positives). Precision matters
    /// more than recall — a demonstrative/pronoun object or a meta noun is dropped.
    #[test]
    fn pattern_table_hits_and_near_miss_rejections() {
        // Hits: (sentence, family, subject_phrase).
        let hits: &[(&str, &str, &str)] = &[
            // superlative → preference, keyed on the SUBJECT ("favorite tea"),
            // never the value, so a later value supersedes.
            ("My favorite tea is chamomile", "preference", "favorite tea"),
            (
                "My all-time favorite band is Queen",
                "preference",
                "favorite band",
            ),
            // preference verbs → preference, keyed on the object.
            (
                "I really love hiking outdoors",
                "preference",
                "hiking outdoors",
            ),
            ("I switched to oat milk lately", "preference", "oat milk"),
            // explicit update: negation + trailing "anymore" normalize to the
            // same object key as the positive assertion (→ supersede).
            ("I don't like coffee anymore", "preference", "coffee"),
            (
                "I can't stand loud crowded bars",
                "preference",
                "loud crowded bars",
            ),
            // a non-verb-fronting leading "I" does not blind the scan to the
            // verb-fronting "I" later in the sentence.
            (
                "When I travel I love quiet trails",
                "preference",
                "quiet trails",
            ),
            // identity / attribute.
            ("My name is Sidney Carter", "attribute", "name"),
            ("My birthday is in early May", "attribute", "birthday"),
            ("I am a software engineer", "attribute", "software engineer"),
            // "my new <noun> is" shares the attribute:<noun> key (→ supersede).
            ("My new phone is a pixel", "attribute", "phone"),
        ];
        for (sentence, family, phrase) in hits {
            assert_eq!(
                matched(sentence),
                Some((*family, phrase.to_string())),
                "hit: {sentence:?}"
            );
        }

        // Near-miss rejections → None.
        let rejects: &[&str] = &[
            "I like that idea",                // demonstrative object
            "I love it",                       // pronoun object (and < 4 words)
            "My point is that we ship",        // meta noun
            "My question is whether it works", // meta noun
            "I'm a big fan of yours",          // filler identity ("big fan")
            "The weather is nice today",       // no first-person marker
            "She loves the ocean",             // not first person
            "I think we should go soon",       // no preference verb
        ];
        for sentence in rejects {
            assert_eq!(matched(sentence), None, "reject: {sentence:?}");
        }
    }

    /// §3 word-count guard: sentences under `MIN_FACT_SENTENCE_WORDS` are never
    /// mined even when they match a pattern.
    #[test]
    fn short_sentences_are_skipped() {
        assert_eq!(matched("I love it"), None);
        assert_eq!(
            matched("My car is red"),
            Some(("attribute", "car".to_string()))
        );
    }

    /// §3 assistant-turn exclusion at the pure level: a first-person statement in
    /// an assistant turn is never mined; user turns and non-role prose are.
    #[test]
    fn extract_skips_assistant_turns() {
        let body = "[session s1]\n\
assistant: I love that plan and my favorite bit is the finish.\n\
user: My favorite fruit is a ripe mango.\n";
        let facts = extract_facts(body, None);
        assert_eq!(facts.len(), 1, "only the user turn is mined: {facts:?}");
        assert_eq!(facts[0].family, "preference");
        assert_eq!(facts[0].subject_phrase, "favorite fruit");
        assert_eq!(facts[0].body, "My favorite fruit is a ripe mango");
    }

    /// §3 within-episode dedup keeps the LAST occurrence of a subject (later
    /// turns win) and the §2 date prefix is baked into the body only when a date
    /// is supplied.
    #[test]
    fn dedup_keeps_last_and_dates_prefix_when_supplied() {
        let body = "user: My favorite tea is plain green tea.\n\
user: My favorite tea is smoky oolong tea.\n";
        let facts = extract_facts(body, Some("2023-05-30"));
        assert_eq!(facts.len(), 1, "deduped to one favorite-tea fact");
        assert!(
            facts[0].body.contains("oolong") && !facts[0].body.contains("green"),
            "later assertion wins: {}",
            facts[0].body
        );
        assert!(
            facts[0].body.starts_with("[date 2023-05-30]"),
            "date prefix baked in: {}",
            facts[0].body
        );
    }

    /// §3 cap: never more than `MAX_EXTRACTED_FACTS`, keeping the most recent.
    #[test]
    fn cap_holds_and_keeps_most_recent() {
        let mut body = String::new();
        for i in 0..(MAX_EXTRACTED_FACTS + 4) {
            body.push_str(&format!("user: I really love hobby number {i} here.\n"));
        }
        let facts = extract_facts(&body, None);
        assert_eq!(facts.len(), MAX_EXTRACTED_FACTS, "cap holds");
        assert!(
            facts
                .last()
                .unwrap()
                .body
                .contains(&format!("number {} here", MAX_EXTRACTED_FACTS + 3)),
            "the most recent facts are kept: {:?}",
            facts.last().unwrap().body
        );
    }
}
