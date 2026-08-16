//! `MemoryStore` contract, run against `InMemoryStore`.
//!
//! The scenarios live in `memphant-store-testkit` and run identically against
//! `PgStore` (DB-gated) in `memphant-store-postgres/tests/pg_store_contract.rs`.
//! Keeping one shared suite is what stops a per-store trait divergence from
//! passing here while the real backend misbehaves.

use memphant_core::InMemoryStore;
use memphant_store_testkit::StoreHarness;
use memphant_types::{
    ActorId, MemoryKind, NewEpisode, NewMemoryUnit, ScopeId, TenantId, TrustLevel, UnitState,
};

struct InMemHarness(InMemoryStore);

impl StoreHarness for InMemHarness {
    type Store = InMemoryStore;

    fn store(&self) -> &InMemoryStore {
        &self.0
    }

    async fn fresh_tenant(&self) -> TenantId {
        TenantId::new()
    }
}

fn harness() -> InMemHarness {
    InMemHarness(InMemoryStore::default())
}

macro_rules! contract_test {
    ($name:ident) => {
        #[tokio::test]
        async fn $name() {
            memphant_store_testkit::$name(&harness()).await;
        }
    };
}

contract_test!(retain_episode_dedups_and_enqueues);
contract_test!(retain_resource_registers_and_enqueues);
contract_test!(resource_acl_round_trips_empty_and_non_empty);
contract_test!(deep_snapshot_is_authorized_stable_and_read_only);
contract_test!(deep_snapshot_binds_historical_rectangle_only);
contract_test!(deep_snapshot_does_not_treat_actor_as_read_partition);
contract_test!(commit_publishes_staged_episode_and_unit);
contract_test!(drop_rolls_back_staged_rows);
contract_test!(recall_candidates_are_tenant_and_scope_scoped);
contract_test!(trace_is_tenant_bound);
contract_test!(review_marks_credit_synthetic_sources_and_stay_trace_bound);
contract_test!(forget_by_episode_blocks_recompilation);
contract_test!(forget_by_episode_cascades_through_correction_lineage);
contract_test!(forget_source_cascades_to_composed_dependent);
contract_test!(forget_by_unit_closes_and_purges);
contract_test!(fetch_episodes_honors_large_limit);
contract_test!(semantic_update_supersedes_unit_aged_past_recall_window);
contract_test!(caller_subject_key_supersedes_without_client_derivation);
contract_test!(scope_memory_page_paginates_without_overlap);
contract_test!(capture_crosscheck_promotes_and_quarantines_across_the_write_seam);
contract_test!(same_subject_captured_beliefs_coexist_for_collision);
contract_test!(same_channel_capture_reinforces_supersedes_and_serves_on_the_coding_lane);

/// Pure type-shape check (no store): the staged-write structs carry the tenant
/// and scope ids through unchanged. Not part of the store contract, so it stays
/// here rather than in the shared suite.
#[test]
fn new_episode_and_unit_shapes_require_tenant_and_scope_ids() {
    let episode = NewEpisode {
        tenant_id: TenantId::from_u128(100),
        data_subject_id: memphant_types::SubjectId::from_u128(
            TenantId::from_u128(100).as_uuid().as_u128(),
        ),
        scope_id: ScopeId::from_u128(200),
        agent_node_id: memphant_types::AgentNodeId::from_u128(
            ScopeId::from_u128(200).as_uuid().as_u128(),
        ),
        subject_generation: 0,
        actor_id: ActorId::from_u128(300),
        source_kind: "tool".to_string(),
        source_ref: "test:tool:result".to_string(),
        observed_at: "2030-01-01T00:00:00Z".to_string(),
        source_trust: TrustLevel::VerifiedTool,
        dedup_key: "tool:result".to_string(),
        body: "Tool result stored as raw episode.".to_string(),
    };
    let unit = NewMemoryUnit {
        capture: None,
        tenant_id: episode.tenant_id,
        data_subject_id: memphant_types::SubjectId::from_u128(
            episode.tenant_id.as_uuid().as_u128(),
        ),
        scope_id: episode.scope_id,
        agent_node_id: memphant_types::AgentNodeId::from_u128(episode.scope_id.as_uuid().as_u128()),
        subject_generation: 0,
        kind: MemoryKind::Episodic,
        state: UnitState::Candidate,
        fact_key: None,
        predicate: None,
        body: episode.body.clone(),
        confidence: None,
        trust_level: episode.source_trust,
        churn_class: None,
        freshness_due_at: None,
        actor_id: Some(episode.actor_id),
        source_kind: Some(episode.source_kind.clone()),
        source_ref: episode.source_ref.clone(),
        observed_at: episode.observed_at.clone(),
        source_episode_id: None,
        source_resource_id: None,
        deletion_generation: None,
        contextual_chunks: Vec::new(),
        valid_from: None,
        valid_to: None,
        transaction_from: None,
        transaction_to: None,
    };

    assert_eq!(episode.tenant_id, unit.tenant_id);
    assert_eq!(episode.scope_id, unit.scope_id);
}
