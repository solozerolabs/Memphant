//! Contract for the MCP edge hardening: tool errors must not leak raw backend
//! detail (mirroring the REST edge), and the streamable-HTTP transport resolves
//! the presented Bearer to a tenant per request (rejecting missing / unknown /
//! revoked / non-`mk_` bearers), mirroring REST's `AuthedTenant`.

use memphant_core::service::ServiceError;
use memphant_core::{ApiKeyRow, CoreError, InMemoryStore, StoreError};
use memphant_mcp::{McpAuthReject, api_key_hash, mcp_error, resolve_http_principal};
use memphant_runtime::AnyStore;
use memphant_types::{ActorId, AgentNodeId, ScopeId, SubjectId, TenantId, TrustLevel};

#[test]
fn mcp_error_hides_backend_detail_but_surfaces_caller_errors() {
    // Backend/store errors collapse to a generic message — no raw SQL leaks.
    let leaked = mcp_error(ServiceError::Core(CoreError::Store(StoreError::Backend(
        "relation memphant.SECRET does not exist".to_string(),
    ))));
    assert_eq!(leaked, "backend unavailable");
    assert!(!leaked.contains("SECRET"));

    // Caller-relevant errors keep their (safe) messages.
    assert!(
        mcp_error(ServiceError::Invalid("missing field".to_string())).contains("missing field")
    );
    assert!(
        mcp_error(ServiceError::Core(CoreError::Invalid(
            "bad shape".to_string()
        )))
        .contains("bad shape")
    );
    assert!(
        mcp_error(ServiceError::Core(CoreError::NotFound(
            "memory_unit".to_string()
        )))
        .contains("memory_unit")
    );
}

#[test]
fn deep_provider_errors_have_stable_safe_mcp_codes() {
    assert_eq!(
        mcp_error(ServiceError::Core(CoreError::DeepUnavailable)),
        "deep_unavailable: deep recall is unavailable"
    );
    assert_eq!(
        mcp_error(ServiceError::Core(CoreError::DeepProviderInvalidOutput)),
        "deep_provider_invalid_output: deep recall provider returned invalid output"
    );
}

/// One in-memory store seeded with a fully context-bound key at `token`, plus
/// the key's tenant so the caller can assert the resolved binding.
fn store_with_key(token: &str, revoked: bool) -> (AnyStore, TenantId, TrustLevel) {
    let store = InMemoryStore::default();
    let tenant = TenantId::new();
    store.insert_api_key(ApiKeyRow {
        id: uuid::Uuid::new_v4(),
        tenant_id: tenant,
        // The row is stored under the HASH of the token, exactly as the edge
        // hashes the presented Bearer before lookup.
        key_hash: api_key_hash(token),
        label: "edge-auth".to_string(),
        max_trust: TrustLevel::AgentOutput,
        data_subject_id: Some(SubjectId::new()),
        subject_generation: Some(0),
        actor_id: Some(ActorId::new()),
        scope_id: Some(ScopeId::new()),
        agent_node_id: Some(AgentNodeId::new()),
        can_forget: false,
        can_audit_history: false,
        revoked,
    });
    (AnyStore::Mem(store), tenant, TrustLevel::AgentOutput)
}

fn bearer(token: &str) -> String {
    format!("Bearer {token}")
}

#[tokio::test]
async fn http_principal_resolves_a_valid_bearer_to_its_tenant() {
    let (store, tenant, trust) = store_with_key("mk_valid_edge_key", false);
    let bound = resolve_http_principal(&store, Some(&bearer("mk_valid_edge_key")))
        .await
        .expect("valid bearer resolves");
    assert_eq!(bound.tenant, tenant, "resolves the key's tenant");
    assert_eq!(bound.max_trust, trust, "carries the key's trust ceiling");
    assert!(!bound.dev_mode, "a real key is never dev mode");
    // A context-bound key resolves its full binding (so recall/resources work).
    assert!(bound.subject_id.is_some() && bound.scope_id.is_some());
    assert!(
        bound.api_key_hash.is_some(),
        "carries the hash for live recheck"
    );
}

#[tokio::test]
async fn http_principal_rejects_missing_unknown_revoked_and_non_mk_bearers() {
    let (store, _, _) = store_with_key("mk_present_key", false);

    // Missing Authorization header.
    assert!(matches!(
        resolve_http_principal(&store, None).await,
        Err(McpAuthReject::Unauthorized)
    ));
    // Present, valid scheme, but not a known key.
    assert!(matches!(
        resolve_http_principal(&store, Some(&bearer("mk_unknown_key"))).await,
        Err(McpAuthReject::Unauthorized)
    ));
    // A non-`mk_` bearer never reaches the store (mirrors REST's prefix filter).
    assert!(matches!(
        resolve_http_principal(&store, Some("Bearer sk_wrong_prefix")).await,
        Err(McpAuthReject::Unauthorized)
    ));
    // Wrong scheme.
    assert!(matches!(
        resolve_http_principal(&store, Some("Basic mk_present_key")).await,
        Err(McpAuthReject::Unauthorized)
    ));
    // The scheme is case-sensitive (mirrors REST's `Bearer ` strip).
    assert!(matches!(
        resolve_http_principal(&store, Some("bearer mk_present_key")).await,
        Err(McpAuthReject::Unauthorized)
    ));
}

#[tokio::test]
async fn http_principal_rejects_a_revoked_key() {
    let (store, _, _) = store_with_key("mk_revoked_key", true);
    assert!(matches!(
        resolve_http_principal(&store, Some(&bearer("mk_revoked_key"))).await,
        Err(McpAuthReject::Unauthorized)
    ));
}
