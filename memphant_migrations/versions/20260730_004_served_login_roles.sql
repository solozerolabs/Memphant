-- Served login roles.
--
-- 20260703_001 created six NOLOGIN capability roles and put ENABLE + FORCE RLS
-- with `_tenant_isolation` policies on 28 tables — but shipped no login role
-- that is a member of any of them, so every packaged deployment connected as a
-- superuser (or as `memphant_owner`, which holds a `using(true)` bypass policy)
-- and FORCE RLS never fired on the served path. Tenant isolation rested
-- entirely on application predicates.
--
-- This migration closes the gap on the SQL side: one login role per served
-- capability, each a member of exactly one capability role and nothing else.
--
-- NO PASSWORD IS SET HERE, deliberately. Migrations are committed to git, so a
-- password in this file would be a published credential. A passwordless LOGIN
-- role cannot authenticate under `scram-sha-256`/`md5` (the default for any
-- TCP-reachable Postgres), so these roles are inert until an operator runs the
-- provisioning step:
--
--   ALTER ROLE memphant_app_login    PASSWORD '<generated>';
--   ALTER ROLE memphant_authn_login  PASSWORD '<generated>';
--   ALTER ROLE memphant_worker_login PASSWORD '<generated>';
--
-- `scripts/provision_login_roles.sh` does exactly that. Managed providers that
-- authenticate by IAM instead of password (Neon, RDS IAM) grant the IAM role
-- membership instead; see `docs/deployment/self-host.md`.
--
-- NOINHERIT on the login roles is load-bearing: it forces the served pool to
-- issue an explicit `SET ROLE`, which makes `current_user` exactly the
-- capability role. RLS policies and the fail-closed startup assertion in
-- `PgStore::connect_pool` both key off `current_user`.
--
-- Roles are cluster-global, so this block is idempotent and never re-grants
-- blindly.

do $$
declare
  pair record;
begin
  for pair in
    select * from (values
      ('memphant_app_login',          'memphant_app'),
      ('memphant_authn_login',        'memphant_authn'),
      ('memphant_worker_login',       'memphant_worker'),
      ('memphant_provisioner_login',  'memphant_provisioner')
    ) as t(login_role, capability_role)
  loop
    if not exists (select 1 from pg_roles where rolname = pair.login_role) then
      execute format('create role %I login noinherit', pair.login_role);
    else
      execute format('alter role %I login noinherit', pair.login_role);
    end if;
    execute format('grant %I to %I', pair.capability_role, pair.login_role);
  end loop;
end;
$$;

-- Defence in depth: a login role must never be able to reach the schema
-- directly, only through the capability role it assumes.
revoke all on schema memphant from
  memphant_app_login, memphant_authn_login, memphant_worker_login,
  memphant_provisioner_login;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260730_004_served_login_roles',
  '20260723_002_file_sync_mutation_verb',
  'additive'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
