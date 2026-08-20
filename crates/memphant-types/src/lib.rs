use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

macro_rules! id_type {
    ($name:ident) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
        pub struct $name(Uuid);

        impl $name {
            pub fn new() -> Self {
                Self(Uuid::now_v7())
            }

            pub fn from_u128(value: u128) -> Self {
                Self(Uuid::from_u128(value))
            }

            pub fn as_uuid(self) -> Uuid {
                self.0
            }
        }

        impl Default for $name {
            fn default() -> Self {
                Self::new()
            }
        }
    };
}

id_type!(ActorId);
id_type!(AgentNodeId);
id_type!(EdgeId);
id_type!(EpisodeId);
id_type!(JobId);
id_type!(ResourceId);
id_type!(ScopeId);
id_type!(SubjectId);
id_type!(TaskId);
id_type!(TenantId);
id_type!(TraceId);
id_type!(UnitId);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContextBindingEntityRef {
    pub external_ref: String,
    pub kind: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContextBindingScopeRef {
    pub external_ref: String,
    pub kind: String,
    #[serde(default)]
    pub parent_external_ref: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContextBindingAgentRef {
    pub external_ref: String,
    #[serde(default)]
    pub parent_external_ref: Option<String>,
}

#[cfg(test)]
mod context_binding_contract_tests {
    use super::*;

    #[test]
    fn access_policy_is_tagged_and_rejects_legacy_level_controls() {
        let policy: ContextBindingAccessPolicy = serde_json::from_value(serde_json::json!({
            "mode": "grant",
            "source_scope_external_ref": "scope:source",
            "source_agent_node_external_ref": "agent:source",
            "kind": "semantic"
        }))
        .expect("strict grant policy");
        assert!(matches!(policy, ContextBindingAccessPolicy::Grant { .. }));

        let legacy = serde_json::from_value::<ContextBindingRequest>(serde_json::json!({
            "subject": {"external_ref": "user:1", "kind": "user"},
            "actor": {"external_ref": "user:1", "kind": "user"},
            "scope": {"external_ref": "scope:root", "kind": "user"},
            "agent_node": {"external_ref": "agent:root"},
            "inherit_to_descendants": [{"kind": "semantic", "max_agent_level": 1}]
        }));
        assert!(legacy.is_err());
    }

    #[test]
    fn aggregation_window_rejects_unknown_fields() {
        let window = serde_json::from_value::<AggregationWindow>(serde_json::json!({
            "from": "2030-01-01T00:00:00Z",
            "to": "2030-02-01T00:00:00Z",
            "engine_override": true
        }));
        assert!(window.is_err());
    }

    #[test]
    fn retain_rejects_server_derived_metadata() {
        let request = serde_json::from_value::<RetainEpisodeHttpRequest>(serde_json::json!({
            "subject_id": SubjectId::new(),
            "scope_id": ScopeId::new(),
            "actor_id": ActorId::new(),
            "agent_node_id": AgentNodeId::new(),
            "subject_generation": 0,
            "source_kind": "user",
            "source_trust": "trusted_system",
            "body": "hello"
        }));
        assert!(request.is_err());
    }

    #[test]
    fn retain_payload_is_tagged_strict_and_requires_provenance() {
        let base = serde_json::json!({
            "subject_id": SubjectId::new(),
            "scope_id": ScopeId::new(),
            "actor_id": ActorId::new(),
            "agent_node_id": AgentNodeId::new(),
            "subject_generation": 0,
            "source_ref": "source:episode:1",
            "observed_at": "2030-01-01T00:00:00Z",
            "payload": {"episode": {"source_kind": "user", "body": "hello"}}
        });
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(base.clone()).is_ok());

        let mut legacy = base.clone();
        legacy.as_object_mut().unwrap().remove("payload");
        legacy["body"] = serde_json::json!("hello");
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(legacy).is_err());

        let mut unknown = base.clone();
        unknown["payload"]["episode"]["subject_hint"] = serde_json::json!("legacy");
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(unknown).is_err());

        for payload in [
            serde_json::json!({"resource": {
                "uri": "https://example.test/file", "mime_type": "text/plain",
                "content_hash": "sha256:abc", "body": "resource body"
            }}),
            serde_json::json!({"unit": {
                "kind": "semantic", "fact_key": "profile:city",
                "predicate": "lives_in", "body": "Lives in Lima", "confidence": 0.9
            }}),
        ] {
            let mut request = serde_json::json!({
                "subject_id": SubjectId::new(), "scope_id": ScopeId::new(),
                "actor_id": ActorId::new(), "agent_node_id": AgentNodeId::new(),
                "subject_generation": 0, "source_ref": "source:1",
                "observed_at": "2030-01-01T00:00:00Z"
            });
            request["payload"] = payload;
            assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(request).is_ok());
        }

        let mut multiple = base.clone();
        multiple["payload"]["resource"] = serde_json::json!({
            "uri": "https://example.test/file", "mime_type": "text/plain",
            "content_hash": "sha256:abc"
        });
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(multiple).is_err());

        let mut unknown_tag = base.clone();
        unknown_tag["payload"] = serde_json::json!({"legacy": {"body": "hello"}});
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(unknown_tag).is_err());

        let unit = serde_json::json!({
            "subject_id": SubjectId::new(),
            "scope_id": ScopeId::new(),
            "actor_id": ActorId::new(),
            "agent_node_id": AgentNodeId::new(),
            "subject_generation": 0,
            "source_ref": "source:fact:1",
            "observed_at": "2030-01-01T00:00:00Z",
            "payload": {"unit": {
                "kind": "semantic", "fact_key": "profile:city",
                "predicate": "lives_in", "body": "Lives in Lima"
            }}
        });
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(unit).is_err());

        // D1: a unit may name its subject instead of pre-composing a fact key,
        // so `fact_key` is no longer a wire-level requirement. Requiring ONE of
        // the two is a service-level rule (it needs the scope to compose the
        // key) and is asserted in `memphant-core/tests/retain_validation.rs`.
        // The wire contract here is only that both shapes decode and neither
        // field admits unknown neighbours.
        let unit_request = |payload: serde_json::Value| {
            serde_json::json!({
                "subject_id": SubjectId::new(), "scope_id": ScopeId::new(),
                "actor_id": ActorId::new(), "agent_node_id": AgentNodeId::new(),
                "subject_generation": 0, "source_ref": "source:1",
                "observed_at": "2030-01-01T00:00:00Z",
                "payload": {"unit": payload}
            })
        };
        for payload in [
            serde_json::json!({"kind": "semantic", "subject": "profile",
                "predicate": "lives_in", "body": "Lives in Lima", "confidence": 0.5}),
            serde_json::json!({"kind": "semantic", "predicate": "is",
                "body": "A complete unit body", "confidence": 0.5}),
        ] {
            assert!(
                serde_json::from_value::<RetainEpisodeHttpRequest>(unit_request(payload)).is_ok()
            );
        }
        let unknown_unit_field = unit_request(serde_json::json!({
            "kind": "semantic", "subject": "profile", "predicate": "is",
            "body": "A complete unit body", "confidence": 0.5, "subject_hint": "legacy"
        }));
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(unknown_unit_field).is_err());

        // The episode payload gained the same caller-authored key fields.
        let keyed_episode = serde_json::json!({
            "subject_id": SubjectId::new(), "scope_id": ScopeId::new(),
            "actor_id": ActorId::new(), "agent_node_id": AgentNodeId::new(),
            "subject_generation": 0, "source_ref": "source:1",
            "observed_at": "2030-01-01T00:00:00Z",
            "payload": {"episode": {"source_kind": "user", "body": "Use tabs.",
                "subject": "style", "predicate": "indentation"}}
        });
        assert!(serde_json::from_value::<RetainEpisodeHttpRequest>(keyed_episode).is_ok());
    }
}

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum ContextBindingPolicyMode {
    Inherit,
    Grant,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum ContextBindingAccessPolicy {
    Inherit {
        source_scope_external_ref: String,
        source_agent_node_external_ref: String,
        kind: MemoryKind,
    },
    Grant {
        source_scope_external_ref: String,
        source_agent_node_external_ref: String,
        kind: MemoryKind,
    },
}

impl ContextBindingAccessPolicy {
    pub fn mode(&self) -> ContextBindingPolicyMode {
        match self {
            Self::Inherit { .. } => ContextBindingPolicyMode::Inherit,
            Self::Grant { .. } => ContextBindingPolicyMode::Grant,
        }
    }

    pub fn source_scope_external_ref(&self) -> &str {
        match self {
            Self::Inherit {
                source_scope_external_ref,
                ..
            }
            | Self::Grant {
                source_scope_external_ref,
                ..
            } => source_scope_external_ref,
        }
    }

    pub fn source_agent_node_external_ref(&self) -> &str {
        match self {
            Self::Inherit {
                source_agent_node_external_ref,
                ..
            }
            | Self::Grant {
                source_agent_node_external_ref,
                ..
            } => source_agent_node_external_ref,
        }
    }

    pub fn kind(&self) -> MemoryKind {
        match self {
            Self::Inherit { kind, .. } | Self::Grant { kind, .. } => *kind,
        }
    }
}

/// Central own-memory and explicit-grant kind matrix for a resolved agent.
/// L0 is the user-memory boundary; L1+ is restricted to agent-local families.
pub fn agent_level_allows_memory_kind(agent_level: u8, kind: MemoryKind) -> bool {
    agent_level == 0
        || matches!(
            kind,
            MemoryKind::Episodic | MemoryKind::Procedural | MemoryKind::Resource
        )
}

pub fn actor_kind_trust(kind: &str) -> TrustLevel {
    match kind {
        "user" => TrustLevel::TrustedUser,
        "system" => TrustLevel::TrustedSystem,
        "tool" => TrustLevel::UnverifiedTool,
        "web" => TrustLevel::WebContent,
        _ => TrustLevel::AgentOutput,
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContextBindingRequest {
    pub subject: ContextBindingEntityRef,
    pub actor: ContextBindingEntityRef,
    pub scope: ContextBindingScopeRef,
    pub agent_node: ContextBindingAgentRef,
    #[serde(default)]
    pub access_policies: Vec<ContextBindingAccessPolicy>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContextBindingResponse {
    pub subject_id: SubjectId,
    pub actor_id: ActorId,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub agent_level: u8,
    pub policy_revision: String,
    pub subject_generation: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RetainRequest {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub source_kind: String,
    pub source_ref: String,
    pub observed_at: String,
    pub source_trust: TrustLevel,
    pub subject_hint: Option<String>,
    #[serde(default)]
    pub subject: Option<String>,
    #[serde(default)]
    pub predicate: Option<String>,
    pub body: String,
    pub compiler_version: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RetainResourceRequest {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub uri: String,
    pub source_ref: String,
    pub observed_at: String,
    #[serde(default)]
    pub kind: Option<ResourceKind>,
    pub content_hash: String,
    pub mime_type: String,
    #[serde(default)]
    pub revision: Option<String>,
    #[serde(default)]
    pub body: Option<String>,
    pub source_trust: TrustLevel,
    pub compiler_version: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RecallMode {
    Fast,
    Deep,
}

#[cfg(test)]
mod recall_mode_contract_tests {
    use super::RecallMode;

    #[test]
    fn deep_is_the_only_explicit_deliberate_recall_mode() {
        assert!(serde_json::from_str::<RecallMode>(r#""deep""#).is_ok());
        assert!(serde_json::from_str::<RecallMode>(r#""balanced""#).is_err());
        assert!(serde_json::from_str::<RecallMode>(r#""exhaustive""#).is_err());
    }
}

#[cfg(test)]
mod resource_acl_contract_tests {
    use super::{ResourceAcl, ResourceProtectedCategory, ScopeId, TrustLevel};

    #[test]
    fn resource_acl_is_strict_and_only_empty_is_deep_eligible() {
        let empty: ResourceAcl = serde_json::from_value(serde_json::json!({})).unwrap();
        assert!(empty.is_empty());
        assert!(empty.is_deep_eligible());
        assert_eq!(serde_json::to_value(&empty).unwrap(), serde_json::json!({}));

        let scope_id = ScopeId::new();
        let acl: ResourceAcl = serde_json::from_value(serde_json::json!({
            "scopes": [scope_id],
            "trust_floor": "verified_tool",
            "protected": "personal_identity"
        }))
        .unwrap();
        assert_eq!(acl.scopes, vec![scope_id]);
        assert_eq!(acl.trust_floor, Some(TrustLevel::VerifiedTool));
        assert_eq!(
            acl.protected,
            Some(ResourceProtectedCategory::PersonalIdentity)
        );
        assert!(!acl.is_empty());
        assert!(!acl.is_deep_eligible());

        for invalid in [
            serde_json::json!({"future_gate": true}),
            serde_json::json!({"scopes": scope_id}),
            serde_json::json!({"trust_floor": 3}),
            serde_json::json!({"protected": {"category": "personal_identity"}}),
            serde_json::json!({"protected": "future_category"}),
        ] {
            assert!(
                serde_json::from_value::<ResourceAcl>(invalid).is_err(),
                "unknown ACL fields, shapes, and tags must fail closed"
            );
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RecallChannel {
    Exact,
    Lexical,
    Vector,
    Temporal,
    Edge,
    Deep,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RecallDropReason {
    Tenant,
    Scope,
    Privacy,
    Trust,
    State,
    Stale,
    Budget,
    Duplicate,
    // The pack already held `output_limit` items when this candidate was
    // reached, so it was never considered. Named `Rerank` until 2026-08-01,
    // which was wrong in a way that cost a real conclusion: it has ONE emitter,
    // that emitter sits under `items.len() >= output_limit`, and it fires
    // whether or not a reranker is installed — on the coding lane no arm had
    // one. `dropped_items` is then pinned at `scan_depth - k` on EVERY query
    // (54 on all 180 Track R goldens), so the bucket is scan-window
    // arithmetic, not a scoring event. Read as a reranker verdict it made a
    // fusion-ranking deficit look like a rerank defect and set a lane's
    // priority on that basis. See docs/build-log/2026-08-01-rerank-channel.md.
    //
    // Deliberately a `//` comment, not a `///` doc comment: schemars renders a
    // documented variant as a `oneOf` branch, which would change the shape of
    // this enum in three public schema artifacts for a rationale that belongs
    // in the build log. The `rerank` alias keeps traces banked under the old
    // name deserializable.
    #[serde(alias = "rerank")]
    OutputLimit,
    Deleted,
    Invalidated,
    Unknown,
    ProtectedCategory,
    BelowTrustFloor,
    Irrelevant,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RecallRequest {
    pub context: ResolvedMemoryContext,
    pub query: String,
    pub k: usize,
    pub budget_tokens: usize,
    pub mode: RecallMode,
    pub include_beliefs: bool,
    #[serde(default = "default_true")]
    pub edge_expansion_enabled: bool,
    #[serde(default = "default_true")]
    pub context_packing_abstention_enabled: bool,
    #[serde(default = "default_true")]
    pub procedure_recall_enabled: bool,
    #[serde(default = "default_true")]
    pub decay_enabled: bool,
    /// The portable coding-agent recall lane. When true, only units carrying the
    /// typed `payload.compact` marker are eligible, and Active procedural compact
    /// units are served (not Validated-only). Default false keeps the general
    /// lane — existing non-compact corpora, the degraded read-your-own-writes
    /// fallback, and every eval — unchanged.
    #[serde(default)]
    pub compact_only: bool,
    /// The union (coding) lane's capture signal, orthogonal to `compact_only`.
    /// When true, captured `Candidate` units are served alongside the general
    /// lane's live facts (labelled `captured_unconfirmed`), WITHOUT the
    /// compact-only card restriction. `compact_only` implies this inside
    /// `recallable`, so the card lane (MCP/projection) is unchanged. Default
    /// false keeps the general lane's anti-poison guarantee: non-CLI consumers
    /// never see `Candidate` units.
    #[serde(default)]
    pub serve_captures: bool,
    pub engine_version: String,
    pub transaction_as_of: Option<String>,
    pub valid_at: Option<String>,
    #[serde(default)]
    pub aggregation_window: Option<AggregationWindow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ResolvedMemoryContext {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub actor_id: ActorId,
    pub actor_trust: TrustLevel,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub agent_level: u8,
    pub subject_generation: u64,
    pub policy_revision: String,
    pub sources_by_kind: BTreeMap<MemoryKind, Vec<ResolvedMemorySource>>,
}

impl ResolvedMemoryContext {
    pub fn allows(&self, kind: MemoryKind, scope_id: ScopeId, agent_node_id: AgentNodeId) -> bool {
        self.sources_by_kind.get(&kind).is_some_and(|sources| {
            sources.contains(&ResolvedMemorySource {
                scope_id,
                agent_node_id,
            })
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ResolvedMemorySource {
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AggregationWindow {
    /// Inclusive RFC 3339 lower bound supplied by the host.
    pub from: String,
    /// Exclusive RFC 3339 upper bound supplied by the host.
    pub to: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RecallCandidateTrace {
    pub unit_id: UnitId,
    pub channel: RecallChannel,
    pub channel_rank: usize,
    pub channel_score: f32,
    pub derived_by: String,
    pub fused_rank: Option<usize>,
    pub fused_score: Option<f32>,
    /// 1-based rank of this candidate AFTER the W8 cross-encoder rerank stage,
    /// or `None` when no reranker ran or the candidate sat outside the scored
    /// head. Recorded because `fused_rank` alone cannot separate the two
    /// post-rerank miss classes that need different fixes: "the reranker never
    /// saw the gold" (raise `candidate_limit`) and "the reranker saw it and
    /// still ranked it below the cut" (a model-quality problem).
    #[serde(default)]
    pub cross_rerank_rank: Option<usize>,
    #[serde(default)]
    pub decay_retrievability: f32,
    #[serde(default)]
    pub dsr_reinforcement_count: u32,
    pub trust_level: TrustLevel,
    pub state: UnitState,
    pub discard_reason: Option<RecallDropReason>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub transaction_from: Option<String>,
    pub transaction_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecallPolicyFilter {
    pub reason: RecallDropReason,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecallCitation {
    pub unit_id: UnitId,
    pub episode_id: Option<EpisodeId>,
    pub resource_id: Option<ResourceId>,
    #[serde(default)]
    pub derived_from_unit_ids: Vec<UnitId>,
    pub verification: CitationVerification,
}

pub const EVIDENCE_RECEIPT_CONTRACT_REVISION: &str = "memphant.evidence_receipt.v1";
pub const EVIDENCE_DISPOSITION_CONTRACT_REVISION: &str = "memphant.evidence_disposition.v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum CitationVerification {
    Verified { receipt: Box<EvidenceReceipt> },
    Unverified { reason: CitationUnverifiedReason },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CitationUnverifiedReason {
    DerivedReference,
    MissingCanonicalCitation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceSourceKind {
    Episode,
    Resource,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvidenceReceipt {
    pub contract_revision: String,
    pub citation_id: Uuid,
    pub trace_id: TraceId,
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub memory_unit_id: UnitId,
    pub source_kind: EvidenceSourceKind,
    pub source_id: Uuid,
    pub source_ref: String,
    pub source_revision: Option<String>,
    pub source_body_sha256: String,
    pub span: CitationSpan,
    pub quote_sha256: String,
    pub source_trust: TrustLevel,
    pub query_hash: String,
    pub policy_revision: String,
    pub engine_version: String,
    pub schema_compat_revision: String,
    pub recalled_at: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "kebab-case")]
pub enum EvidenceStatus {
    Supported,
    ContradictsPremise,
    NearMatch,
    Insufficient,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AnswerPolicy {
    AnswerNormally,
    StatePremiseFalse,
    SayExactTargetNotFound,
    AbstainUnknown,
}

impl EvidenceStatus {
    pub const fn answer_policy(self) -> AnswerPolicy {
        match self {
            Self::Supported => AnswerPolicy::AnswerNormally,
            Self::ContradictsPremise => AnswerPolicy::StatePremiseFalse,
            Self::NearMatch => AnswerPolicy::SayExactTargetNotFound,
            Self::Insufficient => AnswerPolicy::AbstainUnknown,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvidenceDisposition {
    pub contract_revision: String,
    pub status: EvidenceStatus,
    pub answer_policy: AnswerPolicy,
    pub reason: String,
}

#[cfg(test)]
mod evidence_contract_tests {
    use super::{
        AnswerPolicy, EVIDENCE_DISPOSITION_CONTRACT_REVISION, EvidenceDisposition, EvidenceStatus,
    };

    #[test]
    fn evidence_status_values_and_answer_policies_are_closed_and_deterministic() {
        let cases = [
            (
                EvidenceStatus::Supported,
                "supported",
                AnswerPolicy::AnswerNormally,
            ),
            (
                EvidenceStatus::ContradictsPremise,
                "contradicts-premise",
                AnswerPolicy::StatePremiseFalse,
            ),
            (
                EvidenceStatus::NearMatch,
                "near-match",
                AnswerPolicy::SayExactTargetNotFound,
            ),
            (
                EvidenceStatus::Insufficient,
                "insufficient",
                AnswerPolicy::AbstainUnknown,
            ),
        ];
        for (status, wire, policy) in cases {
            assert_eq!(
                serde_json::to_string(&status).unwrap(),
                format!("\"{wire}\"")
            );
            assert_eq!(status.answer_policy(), policy);
        }
        assert!(serde_json::from_str::<EvidenceStatus>("\"unknown\"").is_err());
    }

    #[test]
    fn evidence_disposition_rejects_unknown_fields() {
        let value = serde_json::json!({
            "contract_revision": EVIDENCE_DISPOSITION_CONTRACT_REVISION,
            "status": "insufficient",
            "answer_policy": "abstain_unknown",
            "reason": "no exact support",
            "confidence": 0.99
        });
        assert!(serde_json::from_value::<EvidenceDisposition>(value).is_err());
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ProcedureTraceFact {
    pub unit_id: UnitId,
    pub validation_state: String,
    pub signal_kind: String,
    pub safety_status: String,
}

/// Everything a caller needs to correct a recalled memory in one more round
/// trip, emitted at the exact point the memory was applied (spec plan D1).
///
/// It names the unit and the generation it was written under, the key a
/// correction must reuse to supersede it, the bitemporal rectangle it claims,
/// the byte range of the source body it was minted from, and the episode that
/// body belongs to. Nothing here is derived from the query: the handle is a
/// property of the unit, so the same unit yields the same handle on every
/// recall and in the file plane's unit footer.
///
/// `fact_key` may be an auto key (`{scope}:auto:{sha256[..16]}`), which never
/// supersedes — a corrector seeing one knows a keyed rewrite is required
/// rather than a supersession.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CorrectionHandle {
    pub unit_id: UnitId,
    pub subject_generation: u64,
    pub fact_key: Option<String>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    /// `start-end` UTF-8 byte offsets into the source episode or resource
    /// body, covering every contextual chunk this unit was minted from.
    /// `None` when the unit carries no chunk with a parseable span.
    pub source_span: Option<String>,
    pub episode_id: Option<EpisodeId>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecallContextItem {
    pub unit_id: UnitId,
    pub body: String,
    pub kind: MemoryKind,
    pub derived_by: String,
    pub inclusion_reason: String,
    pub citation_episode_id: Option<EpisodeId>,
    pub citation_resource_id: Option<ResourceId>,
    #[serde(default)]
    pub derived_from_unit_ids: Vec<UnitId>,
    pub suppression_labels: Vec<String>,
    /// The D1 correction handle. `None` only on the degraded path, where the
    /// item is a raw un-reflected episode and there is no stored unit to
    /// correct — emitting a handle there would name a synthetic id.
    #[serde(default)]
    pub correction: Option<CorrectionHandle>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecallDroppedItem {
    pub unit_id: UnitId,
    pub reason: RecallDropReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CrossRerankFailure {
    None,
    Error,
    Empty,
    InvalidScoreCount,
    NonFiniteScore,
}

/// Granularity of the docs fed to the W8 cross-encoder rerank stage: whole
/// unit bodies (the default), or each candidate's flattened
/// `contextual_chunks` bodies max-pooled back to one score per candidate
/// (fallback: the body when a candidate has no chunks).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CrossRerankGranularity {
    #[default]
    UnitBody,
    ContextualChunks,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CrossRerankTrace {
    pub provider: String,
    pub model: String,
    pub candidate_limit: usize,
    /// Candidates in the scored head — NOT docs: under `ContextualChunks`
    /// granularity one candidate can contribute several docs (`docs_scored`).
    pub candidate_count: usize,
    #[serde(default)]
    pub granularity: CrossRerankGranularity,
    /// Docs actually fed to `CrossReranker::rerank` — equals `candidate_count`
    /// under `UnitBody`, the flattened chunk/fallback-body count otherwise.
    #[serde(default)]
    pub docs_scored: usize,
    pub max_length: usize,
    pub batch_size: Option<usize>,
    pub input_chars_p50: usize,
    pub input_chars_p95: usize,
    pub input_chars_max: usize,
    pub failure: CrossRerankFailure,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RecallDegradationReason {
    PendingReflectionReadYourOwnWrites,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DegradedRecallTraceItem {
    pub body: String,
    pub kind: MemoryKind,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RecallDegradationDiagnostic {
    pub reason: RecallDegradationReason,
    pub consolidation_lag_ms: u64,
    pub items: Vec<DegradedRecallTraceItem>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DeepRecallLimits {
    pub wall_time_ms: u64,
    pub max_tool_iterations: u32,
    pub max_context_tokens: u64,
    pub max_spend_micros: u64,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DeepRecallUsage {
    pub wall_time_ms: u64,
    pub tool_iterations: u32,
    pub context_tokens: u64,
    pub spend_micros: u64,
    /// Conservative tokens that may have been accepted by a provider but
    /// could not be settled to provider-native usage before cancellation.
    #[serde(default)]
    pub unsettled_context_tokens_upper_bound: u64,
    /// Conservative micro-USD that may have been billed but could not be
    /// settled before cancellation. This is never included in trace cost.
    #[serde(default)]
    pub unsettled_spend_micros_upper_bound: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DeepRecallStatus {
    Completed,
    Capped,
    Partial,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DeepRecallStopReason {
    Completed,
    WallTime,
    ToolIterations,
    ContextTokens,
    Spend,
    ProviderError,
    InvalidOutput,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DeepProviderIdentity {
    pub provider: String,
    pub model: String,
    pub prompt_hash: String,
    pub config_hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DeepRecallSummary {
    pub status: DeepRecallStatus,
    pub stop_reason: DeepRecallStopReason,
    pub limits: DeepRecallLimits,
    pub usage: DeepRecallUsage,
    /// Ordered provider generation IDs for every accepted model turn.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub generation_ids: Vec<String>,
    pub evidence: EvidenceDisposition,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RetrievalTrace {
    pub id: TraceId,
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub policy_revision: String,
    pub query_hash: String,
    pub engine_version: String,
    pub feature_flags: Vec<String>,
    pub channel_runs: Vec<ReflectStageFact>,
    pub candidates: Vec<RecallCandidateTrace>,
    pub policy_filters: Vec<RecallPolicyFilter>,
    pub context_items: Vec<RecallContextItem>,
    pub dropped_items: Vec<RecallDroppedItem>,
    pub citations: Vec<RecallCitation>,
    pub filter_selectivity: Option<f32>,
    pub iterative_scan_depth: Option<u32>,
    /// R1.5-T0: the `recall_pool_depth` in effect for this recall — the ONE
    /// knob the vector/rerank/packing-scan internal fan-out derived from
    /// (never from `k`). Recorded per-trace so latency attribution stays
    /// observable across deployments that override
    /// `MEMPHANT_RECALL_POOL_DEPTH`. `#[serde(default)]` so traces recorded
    /// before this field existed still deserialize (as `0`, a visible "not
    /// recorded" sentinel — pool depth is never legitimately 0).
    #[serde(default)]
    pub recall_pool_depth: u32,
    /// R1.5-T1: per-recall wall-clock (ms) spent inside the W8 cross-encoder
    /// rerank stage ([`crate::CrossReranker`]). `0` when no cross-reranker is installed on the service
    /// (the default) or the candidate pool was empty — a legitimate "not
    /// run" value, not a sentinel. `#[serde(default)]` so traces recorded
    /// before this field existed still deserialize.
    #[serde(default)]
    pub cross_rerank_ms: u64,
    #[serde(default)]
    pub cross_rerank: Option<CrossRerankTrace>,
    pub consolidation_lag_ms: u64,
    #[serde(default)]
    pub degradation: Option<RecallDegradationDiagnostic>,
    pub mode_requested: RecallMode,
    pub mode_executed: RecallMode,
    pub escalation_reason: String,
    #[serde(default)]
    pub procedure_ids: Vec<UnitId>,
    #[serde(default)]
    pub procedure_validation_states: Vec<ProcedureTraceFact>,
    pub abstention_signal: bool,
    /// Monotonic top-level recall latency. Deep service calls start timing
    /// before query embedding; provider-loop time remains separately available
    /// as `deep.usage.wall_time_ms`.
    pub latency_ms: u64,
    pub token_estimate: usize,
    /// Metered recall cost. For Deep this currently includes provider-reported
    /// model spend; embedding-provider cost is not yet exposed by the embedder
    /// contract and therefore is not included.
    pub cost_micros: u64,
    #[serde(default)]
    pub decay_model_id: String,
    #[serde(default)]
    pub l4_sandbox_id: Option<String>,
    #[serde(default)]
    pub l4_gathered_evidence_ids: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deep: Option<DeepRecallSummary>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    /// Provider configured on the Deep provider instance.
    pub l4_provider: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    /// Model configured on the Deep provider instance.
    pub l4_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    /// Provider actually observed after routing or fallback.
    pub l4_observed_provider: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    /// Model actually observed after routing or fallback.
    pub l4_observed_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub l4_prompt_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub l4_config_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub l4_workspace_manifest_sha256: Option<String>,
    pub recall_time: RecallTime,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecallTime {
    pub evaluated_at: String,
    pub transaction_as_of: String,
    pub valid_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecallResponse {
    pub trace_id: TraceId,
    pub items: Vec<RecallContextItem>,
    pub candidate_whitelist: Vec<UnitId>,
    pub citations: Vec<RecallCitation>,
    pub abstention: bool,
    pub degraded: bool,
    /// Non-zero when `degraded: true`: recall drew on raw un-reflected
    /// episodes because consolidation had not caught up (spec 08 §4).
    #[serde(default)]
    pub consolidation_lag_ms: u64,
    pub suppression_labels: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deep: Option<DeepRecallSummary>,
    pub recall_time: RecallTime,
}

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum MemoryKind {
    Episodic,
    Semantic,
    Procedural,
    Belief,
    Resource,
    // Spec 04 §13.2a. A user-declared standing constraint: declared, never
    // promoted; superseded or revoked, never decayed. Minted by the
    // `20260731_006_preference_memory_kind` migration (00 §4 / 25 §11c). Its
    // router arm lives in `memphant_core::write_router_arm`, an exhaustive
    // match (RW-1) — a further kind must not compile until it has an arm
    // there. Kept as a `//` comment, not a doc comment: schemars turns a
    // documented variant into a `oneOf` branch and forks the flat enum in
    // `examples/evals/trace-schema.v1.json`.
    Preference,
}

impl MemoryKind {
    pub const ALL: [Self; 6] = [
        Self::Episodic,
        Self::Semantic,
        Self::Procedural,
        Self::Belief,
        Self::Resource,
        Self::Preference,
    ];
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum UnitState {
    // `Captured`, `Extracted` and `Retired` were removed 2026-07-31
    // (`20260801_009_drop_dead_schema.sql`): no write path in the tree ever
    // produced them. They existed only in test fixtures and in read-path match
    // arms that could not fire. The `memory_unit.state` CHECK constraint was
    // narrowed to exactly this set in the same commit; keep the two in step.
    Candidate,
    Active,
    Superseded,
    Invalidated,
    Deleted,
    Quarantined,
    Expired,
    Validated,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TrustLevel {
    TrustedUser,
    TrustedSystem,
    VerifiedTool,
    UnverifiedTool,
    WebContent,
    AgentOutput,
    ImportedExternal,
    Quarantined,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct NewEpisode {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub source_kind: String,
    pub source_ref: String,
    pub observed_at: String,
    pub source_trust: TrustLevel,
    pub dedup_key: String,
    pub body: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct StoredEpisode {
    pub id: EpisodeId,
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub source_kind: String,
    pub source_ref: String,
    pub source_trust: TrustLevel,
    pub dedup_key: String,
    pub body: String,
    pub observation_count: u32,
    pub first_observed_at: String,
    pub last_observed_at: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ResourceExtractorState {
    Registered,
    Fetching,
    Extracting,
    Chunked,
    Embedded,
    Failed,
    Stale,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum ResourceKind {
    Document,
    Code,
    Conversation,
    #[default]
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ResourceProtectedCategory {
    CredentialsSecrets,
    PaymentFinancial,
    MedicalLegal,
    PersonalIdentity,
    HighRiskToolArgs,
    ChildPrivateScope,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(default, deny_unknown_fields)]
pub struct ResourceAcl {
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub scopes: Vec<ScopeId>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trust_floor: Option<TrustLevel>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protected: Option<ResourceProtectedCategory>,
}

impl ResourceAcl {
    pub fn is_empty(&self) -> bool {
        self.scopes.is_empty() && self.trust_floor.is_none() && self.protected.is_none()
    }

    /// Deep may export a resource only when its dormant ACL cannot be bypassed.
    /// Ordinary recall enforcement is intentionally separate and remains pending.
    pub fn is_deep_eligible(&self) -> bool {
        self.is_empty()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct NewResource {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub uri: String,
    pub source_ref: String,
    pub observed_at: String,
    #[serde(default)]
    pub kind: ResourceKind,
    pub content_hash: String,
    pub mime_type: String,
    #[serde(default)]
    pub revision: Option<String>,
    #[serde(default)]
    pub body: Option<String>,
    pub source_trust: TrustLevel,
    #[serde(default)]
    pub acl: ResourceAcl,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct StoredResource {
    pub id: ResourceId,
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub uri: String,
    pub source_ref: String,
    pub observed_at: String,
    #[serde(default)]
    pub kind: ResourceKind,
    pub content_hash: String,
    pub mime_type: String,
    #[serde(default)]
    pub revision: Option<String>,
    #[serde(default)]
    pub body: Option<String>,
    pub source_trust: TrustLevel,
    #[serde(default)]
    pub acl: ResourceAcl,
    pub extractor_state: ResourceExtractorState,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ContextualChunk {
    pub id: String,
    pub header: String,
    pub body: String,
    pub source_span: Option<String>,
}

/// Parses a chunk's `start-end` byte-offset span. Returns `None` for any shape
/// the two chunkers do not mint (both emit `format!("{start}-{end}")`), so a
/// decorated or malformed span degrades to "no span" rather than to a wrong one.
fn parse_source_span(span: &str) -> Option<(usize, usize)> {
    let (start, end) = span.split_once('-')?;
    let (start, end) = (start.parse().ok()?, end.parse().ok()?);
    (start <= end).then_some((start, end))
}

/// The single byte range of the source body that a unit's contextual chunks
/// were minted from: `min(start)-max(end)` over every chunk carrying a
/// parseable span. `None` when there are no chunks or none parse.
///
/// This is the one definition of a unit-level `source_span`. Both the recall
/// correction handle and the file-plane unit footer read it here rather than
/// deriving their own — a lane may add fields to provenance, never reimplement
/// a primitive. It is deliberately independent of what recall chose to render:
/// a correction points at where the memory came from, not at this query's
/// packing decision.
pub fn covering_source_span(chunks: &[ContextualChunk]) -> Option<String> {
    let mut bounds: Option<(usize, usize)> = None;
    for span in chunks
        .iter()
        .filter_map(|chunk| chunk.source_span.as_deref())
    {
        let Some((start, end)) = parse_source_span(span) else {
            continue;
        };
        bounds = Some(match bounds {
            Some((lo, hi)) => (lo.min(start), hi.max(end)),
            None => (start, end),
        });
    }
    bounds.map(|(start, end)| format!("{start}-{end}"))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct NewMemoryUnit {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub kind: MemoryKind,
    pub state: UnitState,
    pub fact_key: Option<String>,
    pub predicate: Option<String>,
    pub body: String,
    pub confidence: Option<f32>,
    pub trust_level: TrustLevel,
    pub churn_class: Option<String>,
    #[serde(default)]
    pub freshness_due_at: Option<String>,
    pub actor_id: Option<ActorId>,
    pub source_kind: Option<String>,
    pub source_ref: String,
    pub observed_at: String,
    pub source_episode_id: Option<EpisodeId>,
    pub source_resource_id: Option<ResourceId>,
    pub deletion_generation: Option<u64>,
    #[serde(default)]
    pub contextual_chunks: Vec<ContextualChunk>,
    #[serde(default)]
    pub valid_from: Option<String>,
    #[serde(default)]
    pub valid_to: Option<String>,
    #[serde(default)]
    pub transaction_from: Option<String>,
    #[serde(default)]
    pub transaction_to: Option<String>,
    /// The capture marker to persist on `payload.capture` (see
    /// `StoredMemoryUnit::capture`). `None` for every non-captured write.
    #[serde(default)]
    pub capture: Option<CaptureMarker>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct StoredMemoryUnit {
    pub id: UnitId,
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub kind: MemoryKind,
    pub state: UnitState,
    pub fact_key: Option<String>,
    pub predicate: Option<String>,
    pub body: String,
    pub confidence: Option<f32>,
    pub trust_level: TrustLevel,
    pub churn_class: Option<String>,
    #[serde(default)]
    pub freshness_due_at: Option<String>,
    pub actor_id: Option<ActorId>,
    pub source_kind: Option<String>,
    pub source_ref: String,
    pub observed_at: String,
    pub source_episode_id: Option<EpisodeId>,
    pub source_resource_id: Option<ResourceId>,
    pub deletion_generation: Option<u64>,
    pub contextual_chunks: Vec<ContextualChunk>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub transaction_from: Option<String>,
    pub transaction_to: Option<String>,
    #[serde(default)]
    pub last_reinforced_at: Option<String>,
    #[serde(default)]
    pub reinforcement_count: u32,
    /// The typed compact-envelope marker, present iff the unit was written as a
    /// portable compact memory (`payload.compact`). Read-side only: eligibility
    /// for the coding recall lane keys on its presence. A raw episode/resource
    /// body copied into an Active unit never carries it.
    #[serde(default)]
    pub compact: Option<CompactEnvelope>,
    /// The invalidation marker (`payload.invalidation`), present iff the unit is
    /// a bodyless agent invalidation tombstone. Records why the identity was
    /// archived; the open (`transaction_to is null`) `Invalidated` tombstone is
    /// what blocks resurrection.
    #[serde(default)]
    pub invalidation: Option<InvalidationMarker>,
    /// The capture marker (`payload.capture`), present iff the unit was produced
    /// by a capture channel. Carries the source family + trust-ladder rung +
    /// witness set the anti-poisoning cross-check advances. A non-captured unit
    /// (a plain user/tool write) never carries it and is never touched by the
    /// cross-check.
    #[serde(default)]
    pub capture: Option<CaptureMarker>,
}

impl CorrectionHandle {
    /// Reads the handle straight off the stored unit. Every field is a
    /// persisted property of the unit, so this is total and never fails.
    pub fn for_unit(unit: &StoredMemoryUnit) -> Self {
        Self {
            unit_id: unit.id,
            subject_generation: unit.subject_generation,
            fact_key: unit.fact_key.clone(),
            valid_from: unit.valid_from.clone(),
            valid_to: unit.valid_to.clone(),
            source_span: covering_source_span(&unit.contextual_chunks),
            episode_id: unit.source_episode_id,
        }
    }
}

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum DeepSnapshotSourceKind {
    Episode,
    Resource,
}

/// One authorized canonical raw source and the exact unit records that made
/// it eligible for a Deep query snapshot. Keeping the records bound here lets
/// later query-policy gates run without a second, racy store read.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DeepSnapshotEntry {
    pub source_kind: DeepSnapshotSourceKind,
    pub source_id: Uuid,
    pub path: String,
    pub body: String,
    pub body_sha256: String,
    pub bound_units: Vec<StoredMemoryUnit>,
}

impl DeepSnapshotEntry {
    pub fn eligible_unit_ids(&self) -> Vec<UnitId> {
        let mut ids: Vec<_> = self.bound_units.iter().map(|unit| unit.id).collect();
        ids.sort_unstable_by_key(|id| id.as_uuid());
        ids
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DeepWorkspaceFile {
    pub path: String,
    pub body: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DeepWorkspace {
    pub files: Vec<DeepWorkspaceFile>,
    pub manifest_sha256: String,
    pub workspace_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredCitation {
    pub id: Uuid,
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub memory_unit_id: UnitId,
    pub episode_id: Option<EpisodeId>,
    pub resource_id: Option<ResourceId>,
    pub span: Option<CitationSpan>,
    pub quote_hash: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RecordMaterial {
    pub unit: StoredMemoryUnit,
    pub citations: Vec<StoredCitation>,
    pub lineage: Vec<StoredMemoryEdge>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CitationSpan {
    pub start: u64,
    pub end: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum CitationSource {
    Episode { id: EpisodeId },
    Resource { id: ResourceId },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MemoryCitation {
    pub source_ref: String,
    pub source: Option<CitationSource>,
    pub span: Option<CitationSpan>,
    pub quote_hash: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum LineageRelation {
    Supersedes,
    SupersededBy,
    Contradicts,
    DerivedFrom,
    DerivationSourceFor,
    Cites,
    CitedBy,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MemoryLineage {
    pub relation: LineageRelation,
    pub unit_id: UnitId,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MemoryRecord {
    pub id: UnitId,
    pub scope_id: ScopeId,
    pub kind: MemoryKind,
    pub state: UnitState,
    pub fact_key: Option<String>,
    pub predicate: Option<String>,
    pub body: String,
    pub confidence: Option<f32>,
    pub trust: TrustLevel,
    pub source_ref: String,
    pub observed_at: String,
    pub citations: Vec<MemoryCitation>,
    pub lineage: Vec<MemoryLineage>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub transaction_from: Option<String>,
    pub transaction_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RecallItem {
    pub record: MemoryRecord,
    pub inclusion_reason: String,
    pub derived_by: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum MemoryEdgeKind {
    Supersedes,
    Contradicts,
    DerivedFrom,
    Cites,
    SameSubject,
    DependsOn,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct StoredMemoryEdge {
    pub id: EdgeId,
    pub tenant_id: TenantId,
    pub scope_id: ScopeId,
    pub src_id: UnitId,
    pub dst_id: UnitId,
    pub kind: MemoryEdgeKind,
    pub transaction_from: Option<String>,
    pub transaction_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct NewMemoryEdge {
    pub tenant_id: TenantId,
    pub scope_id: ScopeId,
    pub src_id: UnitId,
    pub dst_id: UnitId,
    pub kind: MemoryEdgeKind,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AdmissionAction {
    Reject,
    Append,
    Merge,
    Supersede,
    Invalidate,
    Quarantine,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReflectJobKind {
    ReflectEpisode,
    ReflectResource,
    ReflectScope,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ReflectJob {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub episode_id: Option<EpisodeId>,
    pub resource_id: Option<ResourceId>,
    pub kind: ReflectJobKind,
    pub compiler_version: String,
    #[serde(default)]
    pub subject: Option<String>,
    #[serde(default)]
    pub predicate: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct QueuedReflectJob {
    pub id: JobId,
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub episode_id: Option<EpisodeId>,
    pub resource_id: Option<ResourceId>,
    pub kind: ReflectJobKind,
    pub compiler_version: String,
    #[serde(default)]
    pub subject: Option<String>,
    #[serde(default)]
    pub predicate: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ReflectCandidate {
    pub source_kind: String,
    pub trust_level: TrustLevel,
    pub actor_id: ActorId,
    pub subject: Option<String>,
    pub predicate: Option<String>,
    #[serde(default)]
    pub fact_key: Option<String>,
    /// Overrides the admission policy's minted kind (e.g. `resource` for
    /// resource-derived units, or a direct-unit retain's declared kind).
    #[serde(default)]
    pub kind: Option<MemoryKind>,
    pub body: String,
    #[serde(default)]
    pub confidence: Option<f32>,
    pub churn_class: Option<String>,
    pub admission_hint: Option<AdmissionAction>,
    #[serde(default)]
    pub contextual_chunks: Vec<ContextualChunk>,
    #[serde(default)]
    pub valid_from: Option<String>,
    #[serde(default)]
    pub valid_to: Option<String>,
    /// Structured-state mutation precondition. `Some([])` means create only
    /// when the subject is absent; non-empty lists bind replacement or
    /// invalidation to exact active units. Ordinary compiler candidates use
    /// `None`.
    #[serde(default)]
    pub target_unit_ids: Option<Vec<UnitId>>,
    /// The typed compact envelope for a portable agent-authored memory. When
    /// present, the minted unit carries `payload.compact` and becomes eligible
    /// for the coding recall lane. Ordinary compiler candidates leave it `None`.
    #[serde(default)]
    pub compact: Option<CompactEnvelope>,
    /// The typed capture marker for a cross-harness CAPTURED memory (a mirror
    /// file-write or a session summary). When present, the minted `Belief`
    /// candidate carries `payload.capture` and enters the Stage A trust ladder
    /// (`run_capture_crosscheck`). Ordinary compiler candidates leave it `None`.
    /// Mirrors `compact` exactly — a serde-default `payload` marker carried
    /// through the admission mint (`minted_unit`).
    #[serde(default)]
    pub capture: Option<CaptureMarker>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ReflectInput {
    pub tenant_id: TenantId,
    pub data_subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub actor_id: ActorId,
    pub source_ref: String,
    pub observed_at: String,
    pub source_body: Option<String>,
    /// The source episode, when this compilation derives from one.
    pub episode_id: Option<EpisodeId>,
    /// The source resource, when this compilation derives from one.
    #[serde(default)]
    pub resource_id: Option<ResourceId>,
    pub job_id: JobId,
    pub compiler_version: String,
    pub candidates: Vec<ReflectCandidate>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ReflectStageFact {
    pub stage: String,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ReflectTrace {
    pub tenant_id: TenantId,
    pub scope_id: ScopeId,
    pub job_id: JobId,
    pub episode_id: Option<EpisodeId>,
    #[serde(default)]
    pub resource_id: Option<ResourceId>,
    pub compiler_version: String,
    pub actions: Vec<AdmissionAction>,
    pub stages: Vec<ReflectStageFact>,
    pub cost_units: u32,
}

impl ReflectTrace {
    pub fn stage_names(&self) -> Vec<&str> {
        self.stages
            .iter()
            .map(|stage| stage.stage.as_str())
            .collect()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DedupOutcome {
    pub matched: bool,
    pub observation_count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RetainOutcome {
    pub episode_id: EpisodeId,
    pub dedup: DedupOutcome,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RetainResourceOutcome {
    pub resource_id: ResourceId,
}

pub const ENGINE_VERSION: &str = "0.1.0-ws0";
pub const COMPILER_VERSION: &str = "compiler-0.1.0-ws0";
pub const TRACE_SCHEMA_VERSION: &str = "trace-0.1.0-ws0";
/// The oldest schema this binary can safely read — i.e. the floor recorded by
/// the newest embedded migration, NOT the newest migration itself.
///
/// This must be bumped by any BREAKING migration. `20260731_006` added the
/// `preference` variant to a frozen closed Rust enum and moved the floor to
/// itself precisely so an older binary self-gates at boot rather than failing
/// serde decode on a `preference` row — but this constant was left at `002`.
/// Readiness (`PgStore::ping`) requires a `schema_migrations` row matching BOTH
/// `MIGRATION_HEAD` and this value, so a correctly-migrated database reported
/// as incompatible and the server never became ready. Pinned by
/// `migrations_manifest.rs::schema_compat_revision_matches_the_newest_migration`.
pub const SCHEMA_COMPAT_REVISION: &str = "20260817_013_drop_dead_fsrs_columns";
pub const METHODOLOGY_VERSION: &str = "memphant-methodology-2026-07-03";
pub const EXPORT_SCHEMA_VERSION: &str = "export-0.1.0-ws0";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct MemphantLock {
    pub engine_version: String,
    pub compiler_version: String,
    pub trace_schema_version: String,
    pub schema_compat_revision: String,
    pub methodology_version: String,
    pub export_schema_version: String,
}

impl MemphantLock {
    pub fn current() -> Self {
        Self {
            engine_version: ENGINE_VERSION.to_string(),
            compiler_version: COMPILER_VERSION.to_string(),
            trace_schema_version: TRACE_SCHEMA_VERSION.to_string(),
            schema_compat_revision: SCHEMA_COMPAT_REVISION.to_string(),
            methodology_version: METHODOLOGY_VERSION.to_string(),
            export_schema_version: EXPORT_SCHEMA_VERSION.to_string(),
        }
    }

    pub fn mismatches(&self, actual: &Self) -> Vec<VerifyMismatch> {
        let pairs = [
            (
                "engine_version",
                self.engine_version.as_str(),
                actual.engine_version.as_str(),
            ),
            (
                "compiler_version",
                self.compiler_version.as_str(),
                actual.compiler_version.as_str(),
            ),
            (
                "trace_schema_version",
                self.trace_schema_version.as_str(),
                actual.trace_schema_version.as_str(),
            ),
            (
                "schema_compat_revision",
                self.schema_compat_revision.as_str(),
                actual.schema_compat_revision.as_str(),
            ),
            (
                "methodology_version",
                self.methodology_version.as_str(),
                actual.methodology_version.as_str(),
            ),
            (
                "export_schema_version",
                self.export_schema_version.as_str(),
                actual.export_schema_version.as_str(),
            ),
        ];
        pairs
            .into_iter()
            .filter(|(_, expected, actual)| expected != actual)
            .map(|(key, expected, actual)| VerifyMismatch {
                key: key.to_string(),
                expected: expected.to_string(),
                actual: actual.to_string(),
            })
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct VerifyMismatch {
    pub key: String,
    pub expected: String,
    pub actual: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct VerifyReport {
    pub ok: bool,
    pub lock: MemphantLock,
    pub current: MemphantLock,
    pub mismatches: Vec<VerifyMismatch>,
}

impl VerifyReport {
    pub fn from_lock(lock: MemphantLock) -> Self {
        let current = MemphantLock::current();
        let mismatches = lock.mismatches(&current);
        Self {
            ok: mismatches.is_empty(),
            lock,
            current,
            mismatches,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct HealthResponse {
    pub status: String,
    /// The active store backend: `postgres` or `memory`.
    #[serde(default)]
    pub store: String,
    /// Dead-lettered reflect jobs (attempts exhausted); `null` when the
    /// backend cannot report it cheaply.
    #[serde(default)]
    pub dead_letter_jobs: Option<u64>,
    pub engine_version: String,
    pub trace_schema_version: String,
    pub schema_compat_revision: String,
}

/// Resource payload for the retain verb (spec 08 §209 `resource` shape).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RetainResourcePayload {
    pub uri: String,
    pub mime_type: String,
    pub content_hash: String,
    #[serde(default)]
    pub kind: Option<ResourceKind>,
    /// Revision identity (e.g. a code commit hash).
    #[serde(default)]
    pub revision: Option<String>,
    /// Durable resource content the worker compiles from.
    #[serde(default)]
    pub body: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RetainEpisodePayload {
    pub source_kind: String,
    pub body: String,
    /// What this episode is *about*, supplied by the caller that authored the
    /// write — typically the agent that just read the directive it is
    /// recording. With `predicate` it makes the compiled unit's fact key
    /// explicit, which is the only thing that lets it supersede a prior rule
    /// on the same subject; absent either, `derive_fact_key` falls back to the
    /// content-hash auto key exactly as before. Never an LLM call on this
    /// path — the caller already knows the subject or it does not.
    #[serde(default)]
    pub subject: Option<String>,
    #[serde(default)]
    pub predicate: Option<String>,
}

/// Direct pre-compiled unit payload for trusted callers (spec 08 §209 `unit`
/// shape). Requires a predicate, confidence, kind, and a subject key — as
/// either an explicit `subject` (the server composes the scope-qualified key)
/// or a pre-composed `fact_key`. The admission trust policy still applies.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RetainUnitPayload {
    pub kind: MemoryKind,
    /// A pre-composed `{scope_id}:{subject}:{predicate}` key. Optional since
    /// D1: prefer `subject`, which lets the server apply `derive_fact_key` and
    /// spares the caller from reimplementing that primitive. When both are
    /// present `fact_key` wins, so callers that supplied one before are
    /// byte-for-byte unaffected.
    #[serde(default)]
    pub fact_key: Option<String>,
    /// The subject this assertion is about. Composed server-side with
    /// `predicate` into the fact key when `fact_key` is absent.
    #[serde(default)]
    pub subject: Option<String>,
    pub predicate: String,
    pub body: String,
    pub confidence: f32,
    #[serde(default)]
    pub valid_from: Option<String>,
    #[serde(default)]
    pub valid_to: Option<String>,
    /// Structured-state supersession by exact prior unit id. `None` is an
    /// ordinary keyed write; `Some([])` asserts the subject key is absent;
    /// `Some([id, ..])` closes those exact active units' generations without
    /// requiring the caller to reproduce their subject key. Rank-0 trust only —
    /// the compiler fails the write closed for any lower tier.
    #[serde(default)]
    pub target_unit_ids: Option<Vec<UnitId>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RetainPayload {
    Episode(RetainEpisodePayload),
    Resource(RetainResourcePayload),
    Unit(RetainUnitPayload),
}

/// The retain verb request with exactly one tagged episode, resource, or unit payload.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RetainEpisodeHttpRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub source_ref: String,
    pub observed_at: String,
    pub payload: RetainPayload,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RetainEpisodeHttpResponse {
    #[serde(default)]
    pub episode_id: Option<EpisodeId>,
    #[serde(default)]
    pub resource_id: Option<ResourceId>,
    #[serde(default)]
    pub unit_ids: Vec<UnitId>,
    #[serde(default)]
    pub dedup: Option<DedupOutcome>,
    /// The trust tier actually assigned after clamping to the API key's
    /// `max_trust` ceiling.
    #[serde(default)]
    pub assigned_trust: Option<TrustLevel>,
    pub enqueued: Vec<String>,
    pub trace_ref: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ReflectRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ReflectAccepted {
    pub job_id: JobId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RecallHttpRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub query: String,
    pub limit: Option<usize>,
    pub budget_tokens: Option<usize>,
    pub mode: Option<RecallMode>,
    pub include_beliefs: Option<bool>,
    /// Select the portable coding-agent recall lane (compact-only eligibility).
    /// Defaults false — the general lane.
    #[serde(default)]
    pub compact_only: bool,
    /// The union (coding) lane's capture signal, orthogonal to `compact_only`.
    /// When true, captured `Candidate` units are served alongside general live
    /// facts without the compact-only card restriction — the default a bare
    /// `memphant recall` sends. `compact_only` implies it. Default false keeps
    /// the general lane's anti-poison guarantee.
    #[serde(default)]
    pub serve_captures: bool,
    pub transaction_as_of: Option<String>,
    pub valid_at: Option<String>,
    #[serde(default)]
    pub aggregation_window: Option<AggregationWindow>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CorrectSelector {
    pub memory_unit_id: UnitId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CorrectionPayload {
    pub value: String,
    pub reason: String,
    pub source_ref: String,
    pub observed_at: String,
    #[serde(default)]
    pub valid_from: Option<String>,
    #[serde(default)]
    pub valid_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CorrectRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub selector: CorrectSelector,
    pub correction: CorrectionPayload,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CorrectResult {
    pub correction_id: String,
    pub superseded: Vec<UnitId>,
    pub created: Vec<UnitId>,
    pub correction_kind: String,
    pub trace_ref: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ForgetSelector {
    #[serde(default)]
    pub memory_unit_id: Option<UnitId>,
    #[serde(default)]
    pub episode_id: Option<EpisodeId>,
    #[serde(default)]
    pub resource_id: Option<ResourceId>,
    pub scope_id: ScopeId,
}

/// The single forget target named by a selector; exactly one of the three ids
/// must be present.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ForgetTarget {
    MemoryUnit(UnitId),
    Episode(EpisodeId),
    Resource(ResourceId),
}

impl ForgetSelector {
    /// Validates the exactly-one-of contract and returns the named target.
    pub fn exactly_one_target(&self) -> Result<ForgetTarget, String> {
        let mut targets = Vec::new();
        if let Some(id) = self.memory_unit_id {
            targets.push(ForgetTarget::MemoryUnit(id));
        }
        if let Some(id) = self.episode_id {
            targets.push(ForgetTarget::Episode(id));
        }
        if let Some(id) = self.resource_id {
            targets.push(ForgetTarget::Resource(id));
        }
        match targets.as_slice() {
            [single] => Ok(*single),
            [] => Err(
                "forget selector must include exactly one of memory_unit_id, episode_id, resource_id"
                    .to_string(),
            ),
            _ => Err(
                "forget selector must include exactly one of memory_unit_id, episode_id, resource_id (got multiple)"
                    .to_string(),
            ),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ForgetRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub selector: ForgetSelector,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ForgetResult {
    pub deletion_generation: u64,
    pub policy: String,
    pub invalidated_units: Vec<UnitId>,
    pub verification: String,
    pub trace_ref: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum MarkOutcome {
    Success,
    Failure,
    Corrected,
    Ignored,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MarkRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub trace_id: TraceId,
    pub caller_id: String,
    pub used_ids: Vec<UnitId>,
    pub outcome: MarkOutcome,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TraceRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub trace_id: TraceId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct MarkResult {
    pub accepted: bool,
    pub trace_id: TraceId,
}

// ---------------------------------------------------------------------------
// Portable coding-agent memory: the five-tool intent surface.
//
// These are the IDENTITY-FREE wire DTOs. None carries tenant, subject, actor,
// scope, node, generation, trust, reporter, transaction time, or hashes: the
// server derives all of those from the live bound principal. Free-form source
// provenance never grants authority. The edge adapters resolve `LivePrincipal`
// and pass only the authorized context + request + idempotency key into the
// service.
// ---------------------------------------------------------------------------

/// The default `source.kind` for the string shorthand. A bare-string source is
/// attributed to the coding agent that authored the write: `"agent"` maps
/// through `actor_kind_trust` to `AgentOutput` (the non-elevated trust floor),
/// and being neither `user` nor `correction` it correctly bars a shorthand
/// `remember` from minting a standing `Preference`.
pub const MEMORY_SOURCE_DEFAULT_KIND: &str = "agent";

/// Evidence provenance for a compact write. Free-form `kind`/`ref` are
/// informational and never widen eligibility. At most one canonical id
/// (`episode_id` XOR `resource_id`) may be present; only a server-resolved
/// canonical resource may carry a source ACL, and that ACL may only narrow.
///
/// Accepts two wire shapes: the full object (see [`MemorySourceObject`]), or a
/// bare string shorthand for `ref` — `kind` defaults to
/// [`MEMORY_SOURCE_DEFAULT_KIND`] and `observed_at` to the empty sentinel the
/// service replaces with its clock's now.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct MemorySourceInput {
    pub kind: String,
    pub r#ref: String,
    pub observed_at: String,
    pub episode_id: Option<EpisodeId>,
    pub resource_id: Option<ResourceId>,
}

/// The object form of a compact-write source: `kind`, `ref`, and `observed_at`
/// (a UTC RFC3339 instant) required, with at most one canonical id
/// (`episode_id` XOR `resource_id`).
//
// Also the single source of truth for the strict object contract: it is reused
// for both deserialization (so unknown fields still error) and the object
// branch of `MemorySourceInput`'s schema.
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct MemorySourceObject {
    kind: String,
    r#ref: String,
    observed_at: String,
    #[serde(default)]
    episode_id: Option<EpisodeId>,
    #[serde(default)]
    resource_id: Option<ResourceId>,
}

impl From<MemorySourceObject> for MemorySourceInput {
    fn from(value: MemorySourceObject) -> Self {
        Self {
            kind: value.kind,
            r#ref: value.r#ref,
            observed_at: value.observed_at,
            episode_id: value.episode_id,
            resource_id: value.resource_id,
        }
    }
}

impl<'de> Deserialize<'de> for MemorySourceInput {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        use serde::de::Error as _;
        // Branch on the concrete value rather than `#[serde(untagged)]`: an
        // untagged enum silently drops `deny_unknown_fields` on its object
        // variant, whereas deserializing through `MemorySourceObject` keeps the
        // strict object contract.
        match serde_json::Value::deserialize(deserializer)? {
            serde_json::Value::String(r#ref) => Ok(Self {
                kind: MEMORY_SOURCE_DEFAULT_KIND.to_string(),
                r#ref,
                // Empty sentinel: the service stamps its clock's now.
                observed_at: String::new(),
                episode_id: None,
                resource_id: None,
            }),
            other => MemorySourceObject::deserialize(other)
                .map(Self::from)
                .map_err(D::Error::custom),
        }
    }
}

impl JsonSchema for MemorySourceInput {
    fn schema_name() -> std::borrow::Cow<'static, str> {
        "MemorySourceInput".into()
    }

    fn json_schema(generator: &mut schemars::SchemaGenerator) -> schemars::Schema {
        let object = MemorySourceObject::json_schema(generator).to_value();
        schemars::Schema::try_from(serde_json::json!({
            "description":
                "Evidence provenance for a compact write: either the object form, \
                 or a bare string shorthand for `ref` (kind defaults to \"agent\", \
                 observed_at to the server clock).",
            "oneOf": [
                {
                    "type": "string",
                    "description":
                        "String shorthand for `ref`; `kind` defaults to \"agent\" and \
                         `observed_at` to the server clock."
                },
                object
            ]
        }))
        .expect("MemorySourceInput schema is valid")
    }
}

/// Why an agent is invalidating a memory. Exactly `stale` or `harmful`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum InvalidationReason {
    Stale,
    Harmful,
}

impl InvalidationReason {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Stale => "stale",
            Self::Harmful => "harmful",
        }
    }
}

/// Create exactly one self-contained, compact, `Active` typed memory. `body`
/// is the compact primary rendering; `trigger` (stored in the unit predicate)
/// says when it applies; `verification` (stored in `payload.compact`) says how
/// to confirm it. `target_scope_id` is applicability, not caller identity:
/// omission means the live key's bound scope; a different scope is authorized
/// only by an owner-created `allow_write` grant plus a canonical source.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RememberRequest {
    pub kind: MemoryKind,
    pub body: String,
    pub trigger: String,
    pub verification: String,
    #[serde(default)]
    pub target_scope_id: Option<ScopeId>,
    #[serde(default)]
    pub valid_from: Option<String>,
    #[serde(default)]
    pub valid_to: Option<String>,
    pub source: MemorySourceInput,
}

/// Append a corrected bitemporal successor to one open unit selected by id.
/// Changed bytes carry only the correction's fresh provenance; valid-time
/// remainders that preserve old bytes keep their old evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CorrectMemoryRequest {
    pub memory_unit_id: UnitId,
    pub body: String,
    pub trigger: String,
    pub verification: String,
    pub reason: String,
    #[serde(default)]
    pub valid_from: Option<String>,
    #[serde(default)]
    pub valid_to: Option<String>,
    pub source: MemorySourceInput,
}

/// Archive one open unit as stale/harmful and append a current, bodyless
/// tombstone with the same stable identity. Only `correct_memory` may later
/// close that tombstone; ranking/retrieval can never reopen it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InvalidateMemoryRequest {
    pub memory_unit_id: UnitId,
    pub reason_kind: InvalidationReason,
    pub reason: String,
    pub source: MemorySourceInput,
}

/// Report how a recall pack was used. Ranking evidence only; the server derives
/// the reporter identity from the live key rather than accepting it on the wire.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ReportMemoryUseRequest {
    pub trace_id: TraceId,
    pub outcome: MarkOutcome,
    pub used_ids: Vec<UnitId>,
}

/// The typed compact-envelope marker persisted under `memory_unit.payload`'s
/// `compact` key. Its presence is what makes a unit eligible for the portable
/// coding recall lane (a raw episode body copied into an Active unit never
/// carries it). `body_sha256` is the SHA-256 of the compact `body` — the public
/// content hash — and backs the open-tombstone exact-body blockade;
/// `write_channel` is `agent_memory` for direct agent writes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CompactEnvelope {
    pub schema_version: u32,
    pub verification: String,
    pub body_sha256: String,
    pub write_channel: String,
}

/// The `payload.invalidation` marker on a bodyless `Invalidated` tombstone.
/// Present iff the unit is an agent invalidation tombstone; records why the
/// stable identity was archived. No table or lifecycle column — this rides in
/// `payload` alongside `compact`, consistent across both stores.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InvalidationMarker {
    pub kind: InvalidationReason,
    pub reason: String,
}

/// Which capture channel produced a captured memory unit. This is the
/// provenance FAMILY the cross-check independence rule keys on: an agent's
/// explicit file-write `Mirror` and its own LLM session `Summary` of that same
/// write are DIFFERENT sources but, when they agree, form a SINGLE witness
/// family (see `CaptureWitness::SourceAgreement`) — a confident-but-wrong agent
/// cannot manufacture two independent witnesses from one belief.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CaptureSource {
    /// Explicit in-repo file write mirrored into the store (e.g. `MEMORY.md`).
    Mirror,
    /// LLM session-end summary of the transcript.
    Summary,
    /// Deterministic error→fix pairing (a failing command followed by the fix
    /// that made it pass). Mints a `Procedural` card, not a `Belief`; a
    /// DIFFERENT family from `Summary`, so an errfix and a summary that agree
    /// form a `SourceAgreement` witness. Serialized as `errfix`, the same
    /// token as its `capture://errfix` source_ref family.
    #[serde(rename = "errfix")]
    ErrFix,
}

/// The trust-ladder rung of a captured memory unit. Rides `payload.capture`; it
/// is deliberately NOT the `TrustLevel` enum, which encodes SOURCE provenance
/// (the separate high-risk trust-floor layer). Recall-eligibility is driven by
/// `UnitState`: `Captured` units are `Candidate` (inert); `Corroborated`/
/// `Durable` are `Active` (recallable); a collision moves the unit to
/// `Quarantined`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum CaptureLadder {
    /// One source, provisional, recall-inert.
    Captured,
    /// One witness family present — promoted to an active, recallable rung.
    Corroborated,
    /// Two DISTINCT witness families present.
    Durable,
}

/// A witness FAMILY that can advance a captured unit along the ladder. Promotion
/// to `Durable` requires two DISTINCT families; the same family counts once no
/// matter how many raw witnesses of that kind exist, which is what blocks
/// witness-laundering (a single belief mirrored and summarized is one family).
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum CaptureWitness {
    /// Two DIFFERENT capture sources agree on the same subject + body.
    SourceAgreement,
    /// A positive weak-self-outcome (`review_event` `success`) on a served unit.
    WeakOutcome,
    /// The unit survived without contradiction across a horizon (reserved;
    /// emitted by the survival detector in a later stage).
    Survival,
}

/// The `payload.capture` marker on a captured memory unit. Present iff the unit
/// was produced by a capture channel; rides `payload` alongside `compact` and
/// `invalidation`, consistent across both stores. `witnesses` is a deduped,
/// sorted SET — the independence rule counts distinct families, not events.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CaptureMarker {
    pub source: CaptureSource,
    pub ladder: CaptureLadder,
    #[serde(default)]
    pub witnesses: Vec<CaptureWitness>,
    /// True when the captured body was cut at a paragraph/bullet boundary to fit
    /// the compact one-card ceiling at mint; the card is still injectable, but
    /// the reader should know it is not the whole capture.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub truncated: bool,
}

impl CaptureMarker {
    /// A freshly captured, unwitnessed unit from one source.
    pub fn captured(source: CaptureSource) -> Self {
        Self {
            source,
            ladder: CaptureLadder::Captured,
            witnesses: Vec::new(),
            truncated: false,
        }
    }

    /// Record a witness family, keeping `witnesses` a deduped, sorted set.
    /// Returns whether the family was newly added.
    pub fn record_witness(&mut self, witness: CaptureWitness) -> bool {
        if self.witnesses.contains(&witness) {
            return false;
        }
        self.witnesses.push(witness);
        self.witnesses.sort_unstable();
        true
    }

    /// The ladder rung implied by the current DISTINCT witness families: zero →
    /// `Captured`, one → `Corroborated`, two-or-more → `Durable`.
    pub fn rung_for_witnesses(&self) -> CaptureLadder {
        match self.distinct_witness_count() {
            0 => CaptureLadder::Captured,
            1 => CaptureLadder::Corroborated,
            _ => CaptureLadder::Durable,
        }
    }

    /// Number of DISTINCT witness families (the ladder's promotion counter).
    pub fn distinct_witness_count(&self) -> usize {
        let mut families: Vec<CaptureWitness> = self.witnesses.clone();
        families.sort_unstable();
        families.dedup();
        families.len()
    }
}

/// The write channel recorded for a direct agent-authored compact memory.
pub const COMPACT_WRITE_CHANNEL_AGENT: &str = "agent_memory";

/// Current `payload.compact` schema version.
pub const COMPACT_ENVELOPE_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ReviewEvent {
    pub tenant_id: TenantId,
    pub trace_id: TraceId,
    pub caller_id: String,
    pub used_ids: Vec<UnitId>,
    pub outcome: MarkOutcome,
    pub recorded_at: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskCompletionStatus {
    Completed,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskValidatorStatus {
    Passed,
    Failed,
    NotRun,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskMemoryEventKind {
    Shown,
    Activated,
    Helpful,
    Harmful,
    Silenced,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskMemoryAttribution {
    ExplicitUser,
    DeterministicScorer,
    RandomizedCounterfactual,
    Observational,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TaskMemoryEventInput {
    pub unit_id: UnitId,
    pub event: TaskMemoryEventKind,
    pub attribution: TaskMemoryAttribution,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TaskOutcomeRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub task_id: TaskId,
    pub harness_id: String,
    pub model_id: String,
    pub started_at: String,
    pub ended_at: String,
    pub completion_status: TaskCompletionStatus,
    pub validator_status: TaskValidatorStatus,
    pub tool_count: u32,
    pub failure_count: u32,
    pub retry_count: u32,
    pub planned_files: Option<Vec<String>>,
    pub actual_files: Vec<String>,
    pub transcript_sha256: String,
    #[serde(default)]
    pub shown_unit_ids: Vec<UnitId>,
    #[serde(default)]
    pub activated_unit_ids: Vec<UnitId>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct TaskOutcomeResult {
    pub accepted: bool,
    pub task_id: TaskId,
    pub scope_recall: Option<f64>,
    pub scope_precision: Option<f64>,
    pub scope_jaccard: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TaskMemoryEventsRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub task_id: TaskId,
    pub events: Vec<TaskMemoryEventInput>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct TaskMemoryEventsResult {
    pub accepted: bool,
    pub task_id: TaskId,
    pub recorded: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ScopeMemoryResponse {
    pub tenant_id: TenantId,
    pub scope_id: ScopeId,
    pub items: Vec<StoredMemoryUnit>,
    pub next_cursor: Option<String>,
    pub has_more: bool,
}

/// One unit current at the projection response's `evaluated_at` instant.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct CanonicalProjectionUnit {
    pub unit_id: UnitId,
    pub kind: MemoryKind,
    pub fact_key: Option<String>,
    pub predicate: Option<String>,
    pub body: String,
    pub confidence: Option<f32>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub body_sha256: String,
    /// D2. The unit's lifecycle state, carried so the file plane can SHOW it
    /// rather than merely filter on it. The projection query admits only
    /// `active` and `validated`, so this is not a filter the reader has to
    /// apply — it is the distinction between a rule that is merely current and
    /// one that has been validated.
    pub state: UnitState,
    /// D2. `start-end` byte offsets into the source body, from
    /// [`covering_source_span`]. Same value the recall correction handle
    /// carries, read through the same primitive.
    #[serde(default)]
    pub source_span: Option<String>,
}

/// The complete, unranked, tenant-bound file-projection snapshot evaluated at one RFC3339 server-clock instant.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct CanonicalProjectionResponse {
    pub tenant_id: TenantId,
    pub subject_id: SubjectId,
    pub actor_id: ActorId,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub evaluated_at: String,
    pub items: Vec<CanonicalProjectionUnit>,
    pub fingerprint: String,
}

/// The budgeted, deterministic core read for pre-injection: a RANKED,
/// budget-fitted compact envelope of the bitemporally-current memory, anchored
/// to the canonical projection's integrity semantics (`fingerprint` +
/// `subject_generation`). Ranking is the recall engine's Fast lane — no
/// sampling, total-order tie-breaks — so identical inputs against an unchanged
/// store produce an identical envelope, suitable for prompt-cache-friendly
/// injection. The budget is honored SERVER-side: packing stops before the item
/// that would exceed it and never truncates mid-item.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ScopeCoreResponse {
    pub tenant_id: TenantId,
    pub subject_id: SubjectId,
    pub actor_id: ActorId,
    pub scope_id: ScopeId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    /// RFC3339 server-clock instant the canonical-projection `fingerprint` was
    /// evaluated at; the ranked read runs within the same request.
    pub evaluated_at: String,
    /// The canonical projection's fingerprint at `evaluated_at` — the same
    /// value `GET /v1/scopes/{id}/projection` returns, so a consumer can prove
    /// which memory state the core was compiled from.
    pub fingerprint: String,
    /// Echo of the honored hard token budget (conservative server-side
    /// estimate, one token per three bytes with a whitespace-word floor).
    pub token_budget: u32,
    /// Ranked, budget-fitted compact items, best-first.
    pub items: Vec<RecallContextItem>,
    pub trace_id: TraceId,
    /// True when the envelope drew on raw un-reflected episodes because
    /// consolidation had not caught up (read-your-own-writes fallback).
    pub degraded: bool,
    pub abstention: bool,
}

/// Maximum encoded JSON body accepted by `POST /v1/file-sync`.
pub const MAX_FILE_SYNC_REQUEST_ENCODED_BYTES: usize = 2_097_152;

/// Immutable fields copied from one canonical projection unit. File sync uses
/// this shape to prove that a correction or forget still targets the exact
/// unit the local file was compiled from.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FileSyncUnitMetadata {
    pub unit_id: UnitId,
    pub kind: MemoryKind,
    pub fact_key: Option<String>,
    pub predicate: Option<String>,
    pub confidence: Option<f32>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub body_sha256: String,
}

/// One ordered operation in an atomic file-sync plan.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum FileSyncOperation {
    Correct {
        base: FileSyncUnitMetadata,
        body: String,
    },
    Retain {
        fact_key: String,
        predicate: String,
        body: String,
        confidence: f32,
        #[serde(default)]
        valid_from: Option<String>,
        #[serde(default)]
        valid_to: Option<String>,
    },
    Forget {
        base: FileSyncUnitMetadata,
    },
}

/// One fully context-bound atomic file-sync request.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FileSyncRequest {
    pub subject_id: SubjectId,
    pub scope_id: ScopeId,
    pub actor_id: ActorId,
    pub agent_node_id: AgentNodeId,
    pub subject_generation: u64,
    pub base_fingerprint: String,
    pub plan_sha256: String,
    pub observed_at: String,
    pub operations: Vec<FileSyncOperation>,
}

/// Per-operation receipt, ordered exactly like the submitted plan.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum FileSyncOperationResult {
    Correct {
        memory_unit_id: UnitId,
        created: Vec<UnitId>,
    },
    Retain {
        created: Vec<UnitId>,
    },
    Forget {
        memory_unit_id: UnitId,
        deletion_generation: u64,
        invalidated: Vec<UnitId>,
    },
}

/// Receipt committed with the batch's mutation-ledger claim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FileSyncResult {
    pub base_fingerprint: String,
    pub fingerprint: String,
    pub evaluated_at: String,
    pub plan_sha256: String,
    pub operations: Vec<FileSyncOperationResult>,
}

#[cfg(test)]
mod file_sync_contract_tests {
    use super::*;

    #[test]
    fn file_sync_request_and_tagged_operations_deny_unknown_fields() {
        let operation = FileSyncOperation::Retain {
            fact_key: "profile:test".to_string(),
            predicate: "states".to_string(),
            body: "A strict file sync body.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        };
        let request = FileSyncRequest {
            subject_id: SubjectId::new(),
            scope_id: ScopeId::new(),
            actor_id: ActorId::new(),
            agent_node_id: AgentNodeId::new(),
            subject_generation: 0,
            base_fingerprint: "0".repeat(64),
            plan_sha256: "1".repeat(64),
            observed_at: "2026-07-22T00:00:00Z".to_string(),
            operations: vec![operation],
        };

        let mut unknown_request = serde_json::to_value(&request).unwrap();
        unknown_request["unknown"] = serde_json::json!(true);
        assert!(serde_json::from_value::<FileSyncRequest>(unknown_request).is_err());

        let mut unknown_operation = serde_json::to_value(&request.operations[0]).unwrap();
        unknown_operation["unknown"] = serde_json::json!(true);
        assert!(serde_json::from_value::<FileSyncOperation>(unknown_operation).is_err());
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
    pub request_id: String,
    pub details: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ErrorEnvelope {
    pub error: ErrorBody,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct McpToolAnnotations {
    pub read_only_hint: bool,
    pub destructive_hint: bool,
    pub idempotent_hint: bool,
    pub open_world_hint: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct McpToolSpec {
    pub name: String,
    pub description: String,
    pub input_schema: serde_json::Value,
    pub output_schema: serde_json::Value,
    pub annotations: McpToolAnnotations,
}

#[cfg(test)]
mod correction_handle_tests {
    use super::*;

    fn chunk(source_span: Option<&str>) -> ContextualChunk {
        ContextualChunk {
            id: "chunk".to_string(),
            header: String::new(),
            body: "body".to_string(),
            source_span: source_span.map(str::to_string),
        }
    }

    /// The covering span is the union of every parseable chunk span, so a unit
    /// minted from several windows still points at one contiguous byte range in
    /// its source body. Chunk order must not matter.
    #[test]
    fn covering_span_unions_every_parseable_chunk_in_any_order() {
        let chunks = vec![chunk(Some("40-72")), chunk(Some("0-31")), chunk(None)];
        assert_eq!(covering_source_span(&chunks), Some("0-72".to_string()));
        let reversed: Vec<_> = chunks.into_iter().rev().collect();
        assert_eq!(covering_source_span(&reversed), Some("0-72".to_string()));
    }

    /// A span shape neither chunker mints degrades to "no span". Reporting a
    /// wrong byte range is worse than reporting none: a corrector would
    /// highlight text the unit was not minted from.
    #[test]
    fn unparseable_and_absent_spans_yield_no_span_rather_than_a_wrong_one() {
        for span in ["episode:0-72", "0-", "-72", "abc", "72-0", ""] {
            assert_eq!(
                covering_source_span(&[chunk(Some(span))]),
                None,
                "{span:?} must not produce a span"
            );
        }
        assert_eq!(covering_source_span(&[]), None);
        assert_eq!(covering_source_span(&[chunk(None)]), None);
    }

    /// Every handle field is read straight off the stored unit, so two recalls
    /// of the same row — however each one was packed — hand back the same
    /// handle. Guards against the handle becoming query-dependent.
    #[test]
    fn handle_reads_the_units_own_identity_key_interval_span_and_episode() {
        let unit = StoredMemoryUnit {
            capture: None,
            invalidation: None,
            compact: None,
            id: UnitId::from_u128(1),
            tenant_id: TenantId::from_u128(2),
            data_subject_id: SubjectId::from_u128(3),
            scope_id: ScopeId::from_u128(4),
            agent_node_id: AgentNodeId::from_u128(5),
            subject_generation: 7,
            kind: MemoryKind::Semantic,
            state: UnitState::Active,
            fact_key: Some("scope:profile:city".to_string()),
            predicate: Some("lives_in".to_string()),
            body: "Lives in Lima".to_string(),
            confidence: Some(1.0),
            trust_level: TrustLevel::TrustedUser,
            churn_class: None,
            freshness_due_at: None,
            actor_id: None,
            source_kind: None,
            source_ref: "test".to_string(),
            observed_at: "2026-08-01T00:00:00Z".to_string(),
            source_episode_id: Some(EpisodeId::from_u128(9)),
            source_resource_id: None,
            deletion_generation: None,
            contextual_chunks: vec![chunk(Some("4-13"))],
            valid_from: Some("2026-07-01T00:00:00Z".to_string()),
            valid_to: None,
            transaction_from: None,
            transaction_to: None,
            last_reinforced_at: None,
            reinforcement_count: 0,
        };
        assert_eq!(
            CorrectionHandle::for_unit(&unit),
            CorrectionHandle {
                unit_id: UnitId::from_u128(1),
                subject_generation: 7,
                fact_key: Some("scope:profile:city".to_string()),
                valid_from: Some("2026-07-01T00:00:00Z".to_string()),
                valid_to: None,
                source_span: Some("4-13".to_string()),
                episode_id: Some(EpisodeId::from_u128(9)),
            }
        );
    }

    /// The handle is `#[serde(default)]` on `RecallContextItem`, so every trace
    /// banked before D1 still decodes — as a handle-less item, not an error.
    #[test]
    fn pre_d1_context_items_still_deserialize_without_a_handle() {
        let item: RecallContextItem = serde_json::from_value(serde_json::json!({
            "unit_id": UnitId::from_u128(1),
            "body": "b",
            "kind": "semantic",
            "derived_by": "d",
            "inclusion_reason": "fused_top_k",
            "citation_episode_id": null,
            "citation_resource_id": null,
            "suppression_labels": []
        }))
        .expect("a pre-D1 context item decodes");
        assert_eq!(item.correction, None);
    }
}

#[cfg(test)]
mod recall_drop_reason_tests {
    use super::RecallDropReason;

    /// `Rerank` was renamed to `OutputLimit` on 2026-08-01. Traces banked under
    /// the old wire name must keep deserializing, or every artifact recorded
    /// before the rename becomes unreadable.
    #[test]
    fn the_retired_rerank_wire_name_still_deserializes() {
        assert_eq!(
            serde_json::from_str::<RecallDropReason>("\"rerank\"").unwrap(),
            RecallDropReason::OutputLimit
        );
    }

    /// The alias is read-only: new traces must be written under the new name,
    /// otherwise the rename buys nothing and the misleading label persists in
    /// every artifact produced from here on.
    #[test]
    fn output_limit_serializes_under_the_new_name_only() {
        assert_eq!(
            serde_json::to_string(&RecallDropReason::OutputLimit).unwrap(),
            "\"output_limit\""
        );
    }
}

#[cfg(test)]
mod compact_intent_types_tests {
    use super::{
        CorrectMemoryRequest, InvalidateMemoryRequest, InvalidationReason, RememberRequest,
        ReportMemoryUseRequest,
    };

    fn remember_json() -> serde_json::Value {
        serde_json::json!({
            "kind": "procedural",
            "body": "Run `cargo fmt` before committing.",
            "trigger": "before any commit touching Rust",
            "verification": "cargo fmt --check exits 0",
            "source": { "kind": "user", "ref": "chat:1", "observed_at": "2026-08-15T00:00:00Z" }
        })
    }

    /// The intent surface is identity-free: the server derives tenant/subject/
    /// actor/scope/node/generation from the live principal, so smuggling any of
    /// them in the body must be a hard deserialize error, not silently ignored.
    #[test]
    fn remember_rejects_identity_fields_in_the_body() {
        let mut with_identity = remember_json();
        with_identity["subject_id"] = serde_json::json!("00000000-0000-0000-0000-000000000001");
        let err = serde_json::from_value::<RememberRequest>(with_identity).unwrap_err();
        assert!(
            err.to_string().contains("subject_id") || err.to_string().contains("unknown field"),
            "identity field must be rejected: {err}"
        );
    }

    /// A well-formed compact remember round-trips, and `target_scope_id`/
    /// valid-time default to absent (applicability, not identity).
    #[test]
    fn remember_round_trips_and_defaults_scope_and_validtime() {
        let parsed: RememberRequest = serde_json::from_value(remember_json()).unwrap();
        assert!(parsed.target_scope_id.is_none());
        assert!(parsed.valid_from.is_none() && parsed.valid_to.is_none());
        assert!(parsed.source.episode_id.is_none() && parsed.source.resource_id.is_none());
    }

    /// `reason_kind` accepts exactly stale|harmful and nothing else.
    #[test]
    fn invalidate_reason_kind_is_closed() {
        let base = serde_json::json!({
            "memory_unit_id": "00000000-0000-0000-0000-000000000002",
            "reason": "superseded by newer guidance",
            "source": { "kind": "user", "ref": "chat:2", "observed_at": "2026-08-15T00:00:00Z" }
        });
        for (kind, ok) in [("stale", true), ("harmful", true), ("obsolete", false)] {
            let mut body = base.clone();
            body["reason_kind"] = serde_json::json!(kind);
            let parsed = serde_json::from_value::<InvalidateMemoryRequest>(body);
            assert_eq!(parsed.is_ok(), ok, "reason_kind={kind}");
            if let Ok(req) = parsed {
                assert!(matches!(
                    req.reason_kind,
                    InvalidationReason::Stale | InvalidationReason::Harmful
                ));
            }
        }
    }

    /// Correct selects by unit id and carries the correction reason; report use
    /// carries no caller-supplied reporter identity.
    #[test]
    fn correct_and_report_shapes_are_identity_free() {
        let correct = serde_json::json!({
            "memory_unit_id": "00000000-0000-0000-0000-000000000003",
            "body": "Updated guidance.",
            "trigger": "when configuring CI",
            "verification": "the pipeline is green",
            "reason": "the old flag was renamed",
            "source": { "kind": "correction", "ref": "chat:3", "observed_at": "2026-08-15T00:00:00Z" }
        });
        serde_json::from_value::<CorrectMemoryRequest>(correct).unwrap();

        let mut report = serde_json::json!({
            "trace_id": "00000000-0000-0000-0000-000000000004",
            "outcome": "success",
            "used_ids": []
        });
        serde_json::from_value::<ReportMemoryUseRequest>(report.clone()).unwrap();
        // A caller-supplied reporter identity must be rejected.
        report["caller_id"] = serde_json::json!("agent-x");
        assert!(serde_json::from_value::<ReportMemoryUseRequest>(report).is_err());
    }
}

#[cfg(test)]
mod memory_source_input_tests {
    use super::{MEMORY_SOURCE_DEFAULT_KIND, MemorySourceInput};

    /// A bare JSON string is shorthand for `ref`: `kind` defaults to the agent
    /// provenance and `observed_at` is the empty sentinel (the service stamps
    /// its clock's now — the deserializer has no clock).
    #[test]
    fn string_shorthand_maps_to_ref_with_defaults() {
        let parsed: MemorySourceInput =
            serde_json::from_value(serde_json::json!("chat:first-remember")).unwrap();
        assert_eq!(parsed.r#ref, "chat:first-remember");
        assert_eq!(parsed.kind, MEMORY_SOURCE_DEFAULT_KIND);
        assert_eq!(parsed.kind, "agent");
        assert_eq!(parsed.observed_at, "");
        assert!(parsed.episode_id.is_none() && parsed.resource_id.is_none());
    }

    /// The object form is the unchanged strict contract.
    #[test]
    fn object_form_still_parses_fully() {
        let parsed: MemorySourceInput = serde_json::from_value(serde_json::json!({
            "kind": "user",
            "ref": "chat:2",
            "observed_at": "2026-08-15T00:00:00Z"
        }))
        .unwrap();
        assert_eq!(parsed.kind, "user");
        assert_eq!(parsed.r#ref, "chat:2");
        assert_eq!(parsed.observed_at, "2026-08-15T00:00:00Z");
    }

    /// `deny_unknown_fields` is preserved on the object form (the value-based
    /// branch keeps the strictness an untagged enum would drop).
    #[test]
    fn object_form_rejects_unknown_fields() {
        let err = serde_json::from_value::<MemorySourceInput>(serde_json::json!({
            "kind": "user",
            "ref": "chat:3",
            "observed_at": "2026-08-15T00:00:00Z",
            "trust": "elevated"
        }))
        .unwrap_err();
        assert!(
            err.to_string().contains("unknown field") || err.to_string().contains("trust"),
            "unknown field must be rejected: {err}"
        );
    }

    /// The generated schema advertises both shapes: a `oneOf` whose first branch
    /// is the string shorthand and whose second is the strict object.
    #[test]
    fn schema_advertises_string_or_object() {
        let schema = schemars::schema_for!(MemorySourceInput);
        let value = serde_json::to_value(&schema).unwrap();
        let variants = value["oneOf"].as_array().expect("oneOf variants");
        assert_eq!(variants.len(), 2);
        assert_eq!(variants[0]["type"], "string");
        assert_eq!(variants[1]["type"], "object");
        assert_eq!(variants[1]["additionalProperties"], false);
        let required = variants[1]["required"]
            .as_array()
            .expect("object required")
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(
            required,
            ["kind", "ref", "observed_at"].into_iter().collect()
        );
    }
}
