-- Tenant-scoped API-key revocation for the served HTTP path.
-- migration_kind: additive
--
-- `DELETE /v1/api-keys/{id}` (per-run context-bound key teardown) needs a
-- revoke the request's authenticated tenant can call safely. The existing
-- `memphant.revoke_api_key(uuid)` is GLOBAL by id — correct for the operator
-- CLI (`memphant admin revoke-key`), wrong for a tenant-facing route: served
-- code holding only the provisioner capability could revoke another tenant's
-- key by guessing its id. The provisioner role deliberately has function
-- grants and no table access (`role_matrix.rs`), so tenant scoping must live
-- inside a SECURITY DEFINER function, not in a preceding SELECT.
--
-- Returns true only when a LIVE key belonging to p_tenant_id was revoked by
-- this call; already-revoked, missing, and other-tenant keys all return false
-- (indistinguishable on purpose — no cross-tenant existence oracle). The HTTP
-- route treats both outcomes as success (idempotent 204).

create function memphant.revoke_tenant_api_key(p_tenant_id uuid, p_id uuid)
returns boolean
language plpgsql
volatile
security definer
set search_path = memphant, pg_catalog
as $$
declare
  changed integer;
begin
  update memphant.api_key
  set revoked_at = now()
  where id = p_id and tenant_id = p_tenant_id and revoked_at is null;
  get diagnostics changed = row_count;
  return changed = 1;
end
$$;

alter function memphant.revoke_tenant_api_key(uuid, uuid) owner to memphant_owner;
grant execute on function memphant.revoke_tenant_api_key(uuid, uuid) to memphant_provisioner;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260820_015_revoke_tenant_api_key',
  -- Purely additive -- one new function; every existing row and function an
  -- older binary uses is untouched, so the floor stays where 013 left it.
  '20260817_013_drop_dead_fsrs_columns',
  'additive'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
