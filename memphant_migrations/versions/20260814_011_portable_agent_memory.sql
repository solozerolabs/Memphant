-- Portable coding-agent memory: operation capabilities, the `invalidate` verb,
-- an owner-managed write grant, and a compact-unit uniqueness/blockade pair.
--
-- Forward-only. The bootstrap (`20260703_001`) is NOT rewritten; precedent
-- b6417369 reverted a bootstrap hand-edit and pinned it in
-- `tests/test_wsa_migration_contract.py`. Everything here lands as a new file.
--
-- Classification: BREAKING. Two `drop`s (the verb CHECK and the two api_key
-- functions whose signatures change) mean the classifier marks this breaking;
-- older binaries also do not know the two new capability columns. The compat
-- floor stays where it is: the new columns default false, so an older binary
-- that ignores them still reads every existing row correctly.

-- 1. Operation capabilities on the API key. Default false everywhere: an
--    existing key, and every ordinary coding-agent key, receives neither
--    permanent-erasure nor historical-audit authority.
alter table memphant.api_key
  add column can_forget boolean not null default false,
  add column can_audit_history boolean not null default false;

-- 2. Admit the `invalidate` mutation verb. Preserves the 010 set verbatim.
alter table memphant.mutation_ledger
  drop constraint mutation_ledger_verb_check,
  add constraint mutation_ledger_verb_check check (verb in (
    'retain','reflect','correct','invalidate','forget','mark','task_outcome',
    'task_memory_event','file_sync','erase_subject'
  ));

-- 3. Owner-managed cross-scope WRITE grant. Read grants (`mode='grant'`) never
--    imply write authority; a scope may place a compact memory into another
--    scope only through a row whose `allow_write` is true.
alter table memphant.scope_policy
  add column allow_write boolean not null default false;

-- 4. Re-mint the two api_key functions to carry the capability columns. Return
--    type and argument list both change, so `create or replace` is illegal here
--    (Postgres forbids changing a function's OUT columns / signature in place);
--    drop then recreate. Owners and grants are restored to the bootstrap roles.
drop function if exists memphant.authenticate_api_key(text);
create function memphant.authenticate_api_key(p_key_hash text)
returns table (
  id uuid,
  tenant_id uuid,
  key_hash text,
  label text,
  max_trust text,
  data_subject_id uuid,
  subject_generation bigint,
  actor_id uuid,
  scope_id uuid,
  agent_node_id uuid,
  can_forget boolean,
  can_audit_history boolean,
  revoked boolean
)
language sql
stable
security definer
set search_path = memphant, pg_catalog
as $$
  select key.id, key.tenant_id, key.key_hash, key.label, key.max_trust,
         key.data_subject_id, key.subject_generation, key.actor_id, key.scope_id,
         key.agent_node_id,
         key.can_forget, key.can_audit_history,
         key.revoked_at is not null
  from memphant.api_key key
  where key.key_hash = p_key_hash
$$;

drop function if exists memphant.provision_api_key(uuid, text, text, text, uuid, bigint, uuid, uuid, uuid);
create function memphant.provision_api_key(
  p_tenant_id uuid,
  p_key_hash text,
  p_label text,
  p_max_trust text,
  p_data_subject_id uuid default null,
  p_subject_generation bigint default null,
  p_actor_id uuid default null,
  p_scope_id uuid default null,
  p_agent_node_id uuid default null,
  p_can_forget boolean default false,
  p_can_audit_history boolean default false
)
returns uuid
language plpgsql
volatile
security definer
set search_path = memphant, pg_catalog
as $$
declare
  created_id uuid := gen_random_uuid();
begin
  insert into memphant.api_key
    (id, tenant_id, key_hash, label, max_trust, data_subject_id, subject_generation,
     actor_id, scope_id, agent_node_id, can_forget, can_audit_history)
  values
    (created_id, p_tenant_id, p_key_hash, p_label, p_max_trust, p_data_subject_id,
     p_subject_generation, p_actor_id, p_scope_id, p_agent_node_id,
     p_can_forget, p_can_audit_history);
  return created_id;
end
$$;

alter function memphant.authenticate_api_key(text) owner to memphant_owner;
alter function memphant.provision_api_key(uuid, text, text, text, uuid, bigint, uuid, uuid, uuid, boolean, boolean) owner to memphant_owner;
grant execute on function memphant.authenticate_api_key(text) to memphant_authn;
grant execute on function memphant.provision_api_key(uuid, text, text, text, uuid, bigint, uuid, uuid, uuid, boolean, boolean) to memphant_provisioner;

-- 5. Compact-unit uniqueness. A SEPARATE exclusion, deliberately NOT folded into
--    `memphant_memory_unit_subject_valid_excl`: migration 007's pinned rule ties
--    that constraint's `kind` set to exactly the kinds where
--    `supersedes_own_kind(kind) == Some(kind)` (`semantic`,`preference`). Compact
--    identity is orthogonal to that set, so it gets its own constraint. It is
--    valid-time aware (`tstzrange && `) so a correction's replacement plus its
--    open valid-time remainders coexist; it forbids two open compact generations
--    for one stable key at the same instant.
alter table memphant.memory_unit add constraint memphant_memory_unit_compact_valid_excl
  exclude using gist (
    tenant_id with =,
    data_subject_id with =,
    scope_id with =,
    agent_node_id with =,
    subject_generation with =,
    fact_key with =,
    kind with =,
    tstzrange(valid_from, valid_to, '[)') with &&
  ) where (transaction_to is null and payload ? 'compact');

-- 6. Exact-body blockade lookup. A NON-unique partial expression index over the
--    open compact body digest, so an open tombstone can cheaply block recreation
--    of the exact same compact body under a drifted provenance.
create index if not exists memphant_memory_unit_compact_body_sha256_idx
  on memphant.memory_unit ((payload -> 'compact' ->> 'body_sha256'))
  where transaction_to is null and payload ? 'compact';

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values ('20260814_011_portable_agent_memory', '20260814_011_portable_agent_memory', 'breaking')
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
