//! Live-PG contract for HTTP API-key provisioning (`POST /v1/api-keys` /
//! `DELETE /v1/api-keys/{id}`): the shared `MemoryStore::create_api_key`
//! path and the tenant-scoped `revoke_tenant_api_key` SQL function
//! (`20260820_015`), exercised through the SERVED pool combination
//! (`connect_app_with_provisioner`, provisioner capability assumed as
//! `memphant_provisioner`) — so a missing grant on the new SECURITY DEFINER
//! function fails here, not in production.
//!
//! The full HTTP mint→recall→revoke→recall roundtrip runs against the router
//! in `memphant-server/tests/auth_contract.rs`; this twin pins the
//! Postgres-only facts: role grants, tenant scoping inside the function, and
//! revocation visibility through `authenticate_api_key` (the exact lookup
//! REST auth and MCP `live_principal()` re-run per request).
//!
//! `#[ignore]`d like every live-PG contract; run under the AGENTS.md
//! scratch-DB leg.

use memphant_core::{MemoryStore, StoreError};
use memphant_store_postgres::PgStore;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    TenantId, TrustLevel,
};
use uuid::Uuid;

fn db_url() -> String {
    std::env::var("MEMPHANT_TEST_DATABASE_URL")
        .expect("MEMPHANT_TEST_DATABASE_URL must point at a migrated scratch database")
}

fn unique_hash() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

fn binding_request(label: &str) -> ContextBindingRequest {
    ContextBindingRequest {
        subject: ContextBindingEntityRef {
            external_ref: format!("{label}-subject"),
            kind: "user".to_string(),
        },
        actor: ContextBindingEntityRef {
            external_ref: format!("{label}-actor"),
            kind: "system".to_string(),
        },
        scope: ContextBindingScopeRef {
            external_ref: format!("{label}-scope"),
            kind: "user_root".to_string(),
            parent_external_ref: None,
        },
        agent_node: ContextBindingAgentRef {
            external_ref: format!("{label}-agent"),
            parent_external_ref: None,
        },
        access_policies: Vec::new(),
    }
}

#[tokio::test]
#[ignore = "requires MEMPHANT_TEST_DATABASE_URL"]
async fn http_key_provisioning_is_tenant_scoped_grant_complete_and_immediate() {
    let url = db_url();
    let provisioner = PgStore::connect_provisioner(&url)
        .await
        .expect("connect provisioner store");
    let label = format!("api-key-prov-{}", Uuid::new_v4().simple());
    let tenant_a = TenantId::from_u128(
        provisioner
            .create_tenant(&format!("{label}-a"))
            .await
            .expect("provision tenant A")
            .as_u128(),
    );
    let tenant_b = TenantId::from_u128(
        provisioner
            .create_tenant(&format!("{label}-b"))
            .await
            .expect("provision tenant B")
            .as_u128(),
    );

    // The served pool combination: app + authn + provisioner capability roles.
    let store = PgStore::connect_app_with_provisioner(&url, &url, &url)
        .await
        .expect("connect app store with provisioner");
    let binding = store
        .resolve_context_binding(tenant_a, format!("{label}-client"), binding_request(&label))
        .await
        .expect("resolve context binding");
    let context = store
        .resolve_memory_context(
            tenant_a,
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .expect("resolve memory context");

    // Mint through the one shared creation path, under memphant_provisioner.
    let key_hash = unique_hash();
    let key_id = store
        .create_api_key(
            tenant_a,
            &key_hash,
            "syndai-run:pg-contract",
            TrustLevel::AgentOutput,
            Some(&context),
        )
        .await
        .expect("mint context-bound key");

    let row = store
        .lookup_api_key(&key_hash)
        .await
        .expect("lookup minted key")
        .expect("minted key row");
    assert_eq!(row.id, key_id);
    assert_eq!(row.tenant_id, tenant_a);
    assert_eq!(row.max_trust, TrustLevel::AgentOutput);
    assert_eq!(row.data_subject_id, Some(binding.subject_id));
    assert_eq!(row.subject_generation, Some(binding.subject_generation));
    assert_eq!(row.actor_id, Some(binding.actor_id));
    assert_eq!(row.scope_id, Some(binding.scope_id));
    assert_eq!(row.agent_node_id, Some(binding.agent_node_id));
    assert!(!row.can_forget, "mint path must never grant can_forget");
    assert!(
        !row.can_audit_history,
        "mint path must never grant can_audit_history"
    );
    assert!(!row.revoked);

    // Cross-tenant revoke: refused inside the SECURITY DEFINER function.
    assert!(
        !store
            .revoke_tenant_api_key(tenant_b, key_id)
            .await
            .expect("cross-tenant revoke call"),
        "another tenant must not be able to revoke the key"
    );
    assert!(
        !store
            .lookup_api_key(&key_hash)
            .await
            .expect("lookup after cross-tenant revoke")
            .expect("key row")
            .revoked
    );

    // Owner revoke: visible to authenticate_api_key on the very next lookup.
    assert!(
        store
            .revoke_tenant_api_key(tenant_a, key_id)
            .await
            .expect("owner revoke")
    );
    assert!(
        store
            .lookup_api_key(&key_hash)
            .await
            .expect("lookup after revoke")
            .expect("key row")
            .revoked,
        "revocation must be immediately visible to the auth lookup"
    );

    // Idempotent: a second revoke reports nothing-to-do, never an error.
    assert!(
        !store
            .revoke_tenant_api_key(tenant_a, key_id)
            .await
            .expect("second revoke")
    );

    // A binding tuple from another tenant is refused at creation.
    let crossed = store
        .create_api_key(
            tenant_b,
            &unique_hash(),
            "syndai-run:crossed",
            TrustLevel::AgentOutput,
            Some(&context),
        )
        .await;
    assert!(
        matches!(crossed, Err(StoreError::PolicyDenied(_))),
        "cross-tenant context mint must be policy-denied, got {crossed:?}"
    );
}
