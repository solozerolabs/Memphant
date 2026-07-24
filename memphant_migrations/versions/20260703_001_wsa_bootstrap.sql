create schema if not exists memphant;

create extension if not exists vector;
create extension if not exists pg_trgm;
create extension if not exists ltree;
create extension if not exists btree_gist;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'memphant_owner') then
    create role memphant_owner nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'memphant_app') then
    create role memphant_app nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'memphant_worker') then
    create role memphant_worker nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'memphant_authn') then
    create role memphant_authn nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'memphant_readonly') then
    create role memphant_readonly nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'memphant_provisioner') then
    create role memphant_provisioner nologin noinherit;
  end if;
end;
$$;

alter schema memphant owner to memphant_owner;

alter role memphant_app set statement_timeout = '30s';
alter role memphant_app set lock_timeout = '5s';
alter role memphant_app set idle_in_transaction_session_timeout = '30s';
alter role memphant_worker set statement_timeout = '5min';
alter role memphant_worker set lock_timeout = '5s';
alter role memphant_worker set idle_in_transaction_session_timeout = '30s';
alter role memphant_readonly set statement_timeout = '30s';
alter role memphant_readonly set lock_timeout = '5s';
alter role memphant_readonly set idle_in_transaction_session_timeout = '30s';

revoke all on schema memphant from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on schema memphant from anon';
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    execute 'revoke all on schema memphant from authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticator') then
    execute 'revoke all on schema memphant from authenticator';
  end if;
end
$$;
grant usage on schema memphant to memphant_app, memphant_worker, memphant_authn, memphant_readonly, memphant_provisioner;

do $$
declare
  extension_schema record;
  extension_function record;
begin
  for extension_schema in
    select distinct namespace.nspname
    from pg_extension extension
    join pg_namespace namespace on namespace.oid = extension.extnamespace
    where extension.extname in ('vector','pg_trgm','ltree','btree_gist')
  loop
    execute format(
      'grant usage on schema %I to memphant_app, memphant_worker, memphant_readonly',
      extension_schema.nspname
    );
  end loop;
  for extension_function in
    select procedure.oid::regprocedure as signature
    from pg_proc procedure
    join pg_depend dependency
      on dependency.classid = 'pg_proc'::regclass and dependency.objid = procedure.oid
    join pg_extension extension
      on dependency.refclassid = 'pg_extension'::regclass
     and dependency.refobjid = extension.oid
    where extension.extname in ('vector','pg_trgm','ltree','btree_gist')
  loop
    execute format(
      'grant execute on function %s to memphant_app, memphant_worker, memphant_readonly',
      extension_function.signature
    );
  end loop;
end
$$;

create or replace function memphant.current_tenant_id()
returns uuid
language sql
stable
set search_path = memphant, pg_catalog
as $$
  select nullif(current_setting('memphant.tenant_id', true), '')::uuid
$$;

create or replace function memphant.bind_tenant(tenant_id uuid)
returns void
language plpgsql
volatile
set search_path = memphant, pg_catalog
as $$
begin
  perform pg_catalog.set_config('memphant.tenant_id', tenant_id::text, true);
end
$$;

create or replace function memphant.set_updated_at()
returns trigger
language plpgsql
set search_path = memphant, pg_catalog
as $$
begin
  new.updated_at = now();
  return new;
end
$$;

create table if not exists memphant.schema_migrations (
  version text primary key,
  schema_compat_revision text not null,
  migration_kind text not null default 'additive'
    check (migration_kind in ('additive', 'breaking', 'rewrite')),
  applied_at timestamptz not null default now()
);

create table if not exists memphant.tenant (
  id uuid primary key,
  slug text not null unique,
  plan text not null,
  region text not null,
  schema_compat_revision text not null default '20260703_001_wsa_bootstrap',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists memphant.subject (
  id uuid not null,
  tenant_id uuid not null,
  external_ref text not null,
  kind text not null,
  generation bigint not null default 0 check (generation >= 0),
  privacy_policy jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, external_ref)
);

create table if not exists memphant.subject_tombstone (
  tenant_id uuid not null references memphant.tenant(id) on delete cascade,
  erased_subject_id uuid not null,
  generation bigint not null check (generation > 0),
  erased_at timestamptz not null default now(),
  primary key (tenant_id, erased_subject_id)
);

create table if not exists memphant.actor (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  kind text not null check (kind in ('user','agent','tool','web','system')),
  external_ref text not null,
  trust_level text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, id),
  unique (tenant_id, data_subject_id, external_ref),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade
);

create table if not exists memphant.scope (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  parent_scope_id uuid,
  kind text not null,
  external_ref text,
  materialized_path ltree not null,
  scope_depth smallint not null check (scope_depth <= 32),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, id),
  unique (tenant_id, data_subject_id, external_ref),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, parent_scope_id)
    references memphant.scope (tenant_id, data_subject_id, id) on delete cascade
);

create table if not exists memphant.agent_node (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  parent_agent_node_id uuid,
  level smallint not null check (level between 0 and 64),
  external_ref text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, id),
  unique (tenant_id, data_subject_id, scope_id, id),
  unique (tenant_id, data_subject_id, external_ref),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id)
    references memphant.scope (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, parent_agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, id) on delete cascade
);

create table if not exists memphant.scope_policy (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  source_scope_id uuid not null,
  source_agent_node_id uuid not null,
  grantee_scope_id uuid not null,
  grantee_agent_node_id uuid not null,
  kind text not null check (kind in ('episodic','semantic','procedural','belief','resource')),
  mode text not null check (mode in ('inherit','grant')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, source_scope_id, source_agent_node_id,
          grantee_scope_id, grantee_agent_node_id, kind),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, source_scope_id, source_agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id),
  foreign key (tenant_id, data_subject_id, grantee_scope_id, grantee_agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id),
  check ((source_scope_id, source_agent_node_id)
         is distinct from (grantee_scope_id, grantee_agent_node_id))
);

create table if not exists memphant.context_binding (
  tenant_id uuid not null,
  data_subject_id uuid not null,
  client_ref text not null,
  identity_fingerprint text not null,
  request_fingerprint text not null,
  actor_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  policy_revision text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, client_ref),
  unique (tenant_id, data_subject_id, agent_node_id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, actor_id)
    references memphant.actor (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, scope_id)
    references memphant.scope (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)
);

create table if not exists memphant.episode (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  actor_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  source_kind text not null check (source_kind in ('user','agent','tool','web','resource','system')),
  source_ref text not null,
  source_trust text not null,
  dedup_key text not null,
  observation_count integer not null default 1 check (observation_count >= 1),
  retention_tier text not null default 'hot' check (retention_tier in ('hot','warm','cold')),
  blob_hash text,
  body text,
  first_observed_at timestamptz not null,
  last_observed_at timestamptz not null,
  transaction_from timestamptz not null default now(),
  deletion_generation bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, subject_generation, scope_id,
          agent_node_id, actor_id, dedup_key),
  unique (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id)
    references memphant.scope (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, actor_id)
    references memphant.actor (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)
);

create table if not exists memphant.resource (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  kind text not null,
  uri text not null,
  source_ref text not null,
  observed_at timestamptz not null,
  content_hash text not null,
  actor_id uuid not null,
  mime_type text,
  revision text,
  body text,
  source_trust text not null default 'untrusted',
  acl jsonb not null default '{}'::jsonb,
  extractor_state text not null default 'registered'
    check (extractor_state in ('registered','fetching','extracting','chunked','embedded','failed','stale')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id),
  foreign key (tenant_id, data_subject_id, actor_id)
    references memphant.actor (tenant_id, data_subject_id, id)
);

create table if not exists memphant.memory_unit (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  kind text not null check (kind in ('episodic','semantic','procedural','belief','resource')),
  state text not null check (state in (
    'captured','extracted','candidate','active','superseded',
    'invalidated','deleted','quarantined','expired','validated','retired'
  )),
  fact_key text,
  predicate text,
  body text not null,
  payload jsonb not null default '{}'::jsonb,
  confidence real check (confidence between 0 and 1),
  trust_level text not null,
  actor_id uuid,
  source_kind text,
  source_ref text not null,
  source_episode_id uuid,
  source_resource_id uuid,
  churn_class text,
  valid_from timestamptz,
  valid_to timestamptz,
  observed_at timestamptz not null,
  transaction_from timestamptz not null default now(),
  transaction_to timestamptz,
  difficulty real check (difficulty between 0 and 10),
  stability_days real,
  last_reinforced_at timestamptz,
  reinforcement_count integer not null default 0 check (reinforcement_count >= 0),
  desired_retention real not null default 0.9 check (desired_retention between 0 and 1),
  last_confirmed_at timestamptz,
  freshness_due_at timestamptz,
  body_tsv tsvector generated always as (to_tsvector('english', coalesce(body,''))) stored,
  deletion_generation bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id)
    references memphant.scope (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id,
               subject_generation, source_episode_id)
    references memphant.episode (tenant_id, data_subject_id, scope_id, agent_node_id,
                                 subject_generation, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id,
               subject_generation, source_resource_id)
    references memphant.resource (tenant_id, data_subject_id, scope_id, agent_node_id,
                                  subject_generation, id)
);

create table if not exists memphant.memory_edge (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  src_id uuid not null,
  dst_id uuid not null,
  kind text not null check (kind in (
    'supersedes','contradicts','derived_from','cites','same_subject','depends_on'
  )),
  observed boolean not null default false,
  transaction_from timestamptz not null default now(),
  transaction_to timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, src_id)
    references memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id,
                                     subject_generation, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, dst_id)
    references memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id,
                                     subject_generation, id) on delete cascade
);

create table if not exists memphant.embedding_profile (
  id uuid not null,
  tenant_id uuid not null,
  provider text not null,
  model text not null,
  dimensions integer not null check (dimensions > 0 and dimensions <= 16000),
  distance text not null check (distance in ('cosine','l2','inner_product')),
  version text not null,
  index_strategy text not null check (index_strategy in ('hnsw_full','hnsw_subvector','hnsw_binary','exact')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, provider, model, dimensions, version, index_strategy)
);

create table if not exists memphant.embedding (
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  memory_unit_id uuid not null,
  embedding_profile_id uuid not null,
  vec halfvec,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, memory_unit_id, embedding_profile_id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, memory_unit_id)
    references memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id,
                                     subject_generation, id) on delete cascade,
  foreign key (tenant_id, embedding_profile_id) references memphant.embedding_profile (tenant_id, id)
);

create table if not exists memphant.citation (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  memory_unit_id uuid not null,
  episode_id uuid,
  resource_id uuid,
  span jsonb,
  quote_hash text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  check (num_nonnulls(episode_id, resource_id) <= 1),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, memory_unit_id)
    references memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id,
                                     subject_generation, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, episode_id)
    references memphant.episode (tenant_id, data_subject_id, scope_id, agent_node_id,
                                 subject_generation, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, resource_id)
    references memphant.resource (tenant_id, data_subject_id, scope_id, agent_node_id,
                                  subject_generation, id) on delete cascade
);

create table if not exists memphant.trust_event (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  target_kind text not null check (target_kind in ('episode','memory_unit','resource','actor','source')),
  target_id uuid not null,
  level text not null,
  decision text not null,
  reason_code text not null,
  corroborating_sources jsonb,
  policy_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id) on delete cascade
);

create table if not exists memphant.event_outbox (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  event_type text not null check (event_type in (
    'memory.promoted','memory.superseded','memory.contradiction_detected',
    'memory.quarantined','reflect.completed','mark.recorded'
  )),
  event_schema_version integer not null default 1 check (event_schema_version > 0),
  visibility text not null check (visibility in ('public','internal','billing')),
  memory_unit_ids uuid[] not null default '{}'::uuid[],
  trust_event_id uuid,
  generation_ref text,
  payload jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null,
  delivered_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, trust_event_id)
    references memphant.trust_event (tenant_id, data_subject_id, scope_id, agent_node_id,
                                     subject_generation, id) on delete cascade
);

create table if not exists memphant.retrieval_trace (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  actor_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  policy_revision text not null,
  query_hash text not null,
  mode text not null,
  channels jsonb not null default '[]'::jsonb,
  candidates jsonb not null default '[]'::jsonb,
  dropped jsonb not null default '[]'::jsonb,
  citations jsonb not null default '[]'::jsonb,
  filter_selectivity real,
  consolidation_lag_ms bigint,
  config_hash text not null,
  trace jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, subject_generation, scope_id,
          actor_id, agent_node_id, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id)
    references memphant.scope (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, actor_id)
    references memphant.actor (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)
);

create table if not exists memphant.deletion_generation (
  id bigint generated always as identity,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  requested_by uuid not null,
  state text not null check (state in ('requested','tombstoned','compacting','completed','failed')),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, requested_by)
    references memphant.actor (tenant_id, data_subject_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id) on delete cascade
);

create table if not exists memphant.job_state (
  id uuid not null,
  queue_order bigint generated always as identity,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  actor_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  job_type text not null,
  target_id uuid not null,
  compiler_version text not null,
  state text not null check (state in ('queued','running','done','failed','dead')),
  attempts integer not null default 0 check (attempts >= 0),
  claim_generation bigint not null default 0 check (claim_generation >= 0),
  run_after timestamptz not null default now(),
  claimed_at timestamptz,
  scope_id uuid not null,
  subject text,
  predicate text,
  result jsonb,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id,
          actor_id, job_type, target_id, compiler_version),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, actor_id)
    references memphant.actor (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)
);

-- Mutation receipts deliberately have no subject foreign key: complete subject
-- erasure removes the subject row while retaining the current erasure receipt
-- for at most this ledger's 24-hour replay window.
create table if not exists memphant.mutation_ledger (
  tenant_id uuid not null references memphant.tenant(id) on delete cascade,
  verb text not null check (verb in ('retain','reflect','correct','forget','mark','erase_subject')),
  idempotency_key text not null check (octet_length(idempotency_key) between 1 and 255),
  data_subject_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  request_hash bytea not null check (octet_length(request_hash) = 32),
  state text not null check (state in ('pending','completed')),
  response_status smallint,
  response_body bytea,
  created_at timestamptz not null default statement_timestamp(),
  expires_at timestamptz not null default statement_timestamp() + interval '24 hours',
  primary key (tenant_id, verb, idempotency_key),
  check (expires_at > created_at),
  check (
    (state = 'pending' and response_status is null and response_body is null)
    or
    (state = 'completed' and response_status between 200 and 299 and response_body is not null)
  )
);

create table if not exists memphant.blob_ledger (
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  content_hash text not null,
  state text not null check (state in ('present','collected')),
  byte_len bigint not null check (byte_len >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, data_subject_id, subject_generation, content_hash),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id) on delete cascade
);

create table if not exists memphant.belief_observation (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  memory_unit_id uuid not null,
  source_event_id text not null,
  evidence text not null,
  direction text not null check (direction in ('confirm','disconfirm')),
  observed_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, memory_unit_id, source_event_id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, memory_unit_id)
    references memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id,
                                     subject_generation, id) on delete cascade
);

create table if not exists memphant.review_event (
  id uuid not null default gen_random_uuid(),
  tenant_id uuid not null references memphant.tenant(id),
  data_subject_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  scope_id uuid not null,
  actor_id uuid not null,
  agent_node_id uuid not null,
  trace_id uuid not null,
  caller_id text not null,
  outcome text not null check (outcome in ('success','failure','corrected','ignored')),
  created_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, trace_id, caller_id),
  unique (tenant_id, data_subject_id, subject_generation, scope_id,
          actor_id, agent_node_id, id),
  foreign key (tenant_id, data_subject_id)
    references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id),
  foreign key (tenant_id, data_subject_id, actor_id)
    references memphant.actor (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, subject_generation, scope_id,
               actor_id, agent_node_id, trace_id)
    references memphant.retrieval_trace(
      tenant_id, data_subject_id, subject_generation, scope_id,
      actor_id, agent_node_id, id
    ) on delete cascade
);

create table if not exists memphant.review_event_unit (
  review_event_id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  scope_id uuid not null,
  actor_id uuid not null,
  agent_node_id uuid not null,
  memory_unit_id uuid not null,
  primary key (tenant_id, review_event_id, memory_unit_id),
  foreign key (tenant_id, data_subject_id, subject_generation, scope_id,
               actor_id, agent_node_id, review_event_id)
    references memphant.review_event(
      tenant_id, data_subject_id, subject_generation, scope_id,
      actor_id, agent_node_id, id
    ) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id,
               subject_generation, memory_unit_id)
    references memphant.memory_unit(tenant_id, data_subject_id, scope_id, agent_node_id,
                                    subject_generation, id) on delete cascade
);

create table if not exists memphant.api_key (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references memphant.tenant(id),
  key_hash text not null unique,
  label text not null default '',
  max_trust text not null default 'trusted_user',
  data_subject_id uuid,
  subject_generation bigint check (subject_generation >= 0),
  actor_id uuid,
  scope_id uuid,
  agent_node_id uuid,
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  foreign key (tenant_id, data_subject_id) references memphant.subject(tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, actor_id)
    references memphant.actor(tenant_id, data_subject_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node(tenant_id, data_subject_id, scope_id, id) on delete cascade,
  check (
    (data_subject_id is null and subject_generation is null and actor_id is null
      and scope_id is null and agent_node_id is null)
    or
    (data_subject_id is not null and subject_generation is not null and actor_id is not null
      and scope_id is not null and agent_node_id is not null)
  )
);

create table if not exists memphant.forgotten_source (
  tenant_id uuid not null references memphant.tenant(id),
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  source_kind text not null check (source_kind in ('episode','resource','memory_unit')),
  source_id uuid not null,
  forgotten_at timestamptz not null default now(),
  primary key (tenant_id, data_subject_id, subject_generation, source_kind, source_id),
  foreign key (tenant_id, data_subject_id) references memphant.subject(tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node(tenant_id, data_subject_id, scope_id, id) on delete cascade
);

create table if not exists memphant.scope_block (
  id uuid not null,
  tenant_id uuid not null,
  data_subject_id uuid not null,
  scope_id uuid not null,
  agent_node_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  content text not null,
  token_limit integer not null default 300 check (token_limit > 0),
  version integer not null check (version > 0),
  updated_by_actor_id uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, id),
  unique (tenant_id, scope_id, version),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, updated_by_actor_id)
    references memphant.actor (tenant_id, data_subject_id, id) on delete cascade
);

-- DORMANT tables: the schema (frozen contract) exists but no code path reads or
-- writes them yet. Registered explicitly here so the catalog says so and nobody
-- mistakes a schema-only table for a live one. See docs/superpowers/specs/
-- memphant/STATUS.md "Dormant machinery" and 04-codebase-memphant.md §f.7.
--   trust_event   — no producer (no trust-decision writer wired)
--   event_outbox  — no consumer (no outbox drain/relay job)
--   scope_block   — no surface (observation-block verb is B1, unbuilt)
-- (retention_tier is a live COLUMN on episode with a real index, not a table —
--  what is dormant there is the warm/cold tiering job, not the schema.)
comment on table memphant.trust_event is
  'DORMANT (2026-07-22): schema-only, no producer. Frozen trust-event contract; no writer wired.';
comment on table memphant.event_outbox is
  'DORMANT (2026-07-22): schema-only, no consumer. Frozen outbox contract; no drain/relay job.';
comment on table memphant.scope_block is
  'DORMANT (2026-07-22): schema-only, no surface. Observation-block storage; the verb is plan item B1 (unbuilt).';

create index if not exists memphant_subject_tenant_external_idx on memphant.subject (tenant_id, external_ref);
create index if not exists memphant_subject_tombstone_tenant_erased_idx on memphant.subject_tombstone (tenant_id, erased_subject_id);
create index if not exists memphant_actor_tenant_external_idx on memphant.actor (tenant_id, kind, external_ref);
create index if not exists memphant_scope_tenant_parent_idx on memphant.scope (tenant_id, parent_scope_id);
create index if not exists memphant_scope_tenant_path_idx on memphant.scope using gist (tenant_id, materialized_path gist_ltree_ops(siglen=100));
create index if not exists memphant_scope_policy_tenant_grantee_idx on memphant.scope_policy (tenant_id, data_subject_id, grantee_scope_id, grantee_agent_node_id, kind);
create index if not exists memphant_scope_policy_tenant_source_idx on memphant.scope_policy (tenant_id, data_subject_id, source_scope_id, source_agent_node_id, kind);
create index if not exists memphant_agent_node_tenant_scope_idx on memphant.agent_node (tenant_id, data_subject_id, scope_id);
create index if not exists memphant_agent_node_tenant_parent_idx on memphant.agent_node (tenant_id, data_subject_id, parent_agent_node_id);
create index if not exists memphant_context_binding_tenant_created_idx on memphant.context_binding (tenant_id, data_subject_id, created_at);
create index if not exists memphant_episode_tenant_scope_source_idx on memphant.episode (tenant_id, data_subject_id, scope_id, agent_node_id, source_kind, last_observed_at);
create index if not exists memphant_episode_tenant_source_ref_idx on memphant.episode (tenant_id, data_subject_id, subject_generation, source_ref);
create index if not exists memphant_episode_tenant_actor_idx on memphant.episode (tenant_id, data_subject_id, actor_id, last_observed_at);
create index if not exists memphant_episode_tenant_retention_idx on memphant.episode (tenant_id, data_subject_id, scope_id, agent_node_id, retention_tier) where retention_tier <> 'hot';
create index if not exists memphant_resource_tenant_scope_idx on memphant.resource (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation);
create index if not exists memphant_resource_tenant_source_ref_idx on memphant.resource (tenant_id, data_subject_id, subject_generation, source_ref);
create index if not exists memphant_resource_tenant_actor_idx on memphant.resource (tenant_id, data_subject_id, actor_id);
create index if not exists memphant_memory_unit_tenant_live_idx on memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id, kind, valid_to) where state = 'active' and transaction_to is null;
create index if not exists memphant_memory_unit_tenant_source_ref_idx on memphant.memory_unit (tenant_id, data_subject_id, subject_generation, source_ref);
create index if not exists memphant_memory_unit_tenant_subject_idx on memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id, fact_key) where state = 'active' and transaction_to is null;
alter table memphant.memory_unit add constraint memphant_memory_unit_subject_valid_excl
  exclude using gist (
    tenant_id with =,
    data_subject_id with =,
    scope_id with =,
    agent_node_id with =,
    fact_key with =,
    kind with =,
    tstzrange(valid_from, valid_to, '[)') with &&
  ) where (transaction_to is null and kind in ('semantic', 'belief'));
create index if not exists memphant_memory_unit_history_idx on memphant.memory_unit
  (tenant_id, data_subject_id, scope_id, agent_node_id, transaction_from);
create index if not exists memphant_memory_unit_tenant_source_episode_idx on memphant.memory_unit (tenant_id, source_episode_id);
create index if not exists memphant_memory_unit_tenant_source_resource_idx on memphant.memory_unit (tenant_id, source_resource_id);
create index if not exists memphant_memory_unit_body_tsv_idx on memphant.memory_unit using gin (body_tsv);
create index if not exists memphant_memory_edge_tenant_src_idx on memphant.memory_edge
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, src_id, kind);
create index if not exists memphant_memory_edge_tenant_dst_idx on memphant.memory_edge
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, dst_id, kind);
alter table memphant.memory_edge add constraint memphant_memory_edge_transaction_excl
  exclude using gist (
    tenant_id with =,
    data_subject_id with =,
    subject_generation with =,
    scope_id with =,
    agent_node_id with =,
    src_id with =,
    dst_id with =,
    kind with =,
    tstzrange(transaction_from, transaction_to, '[)') with &&
  );
create index if not exists memphant_embedding_profile_tenant_model_idx on memphant.embedding_profile (tenant_id, provider, model, version);
create index if not exists memphant_embedding_tenant_profile_idx on memphant.embedding
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, embedding_profile_id, memory_unit_id);
create index if not exists memphant_citation_tenant_unit_idx on memphant.citation
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, memory_unit_id);
create index if not exists memphant_citation_tenant_episode_idx on memphant.citation
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, episode_id);
create index if not exists memphant_citation_tenant_resource_idx on memphant.citation
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, resource_id);
create index if not exists memphant_trust_event_tenant_target_idx on memphant.trust_event
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, target_kind, target_id, created_at);
create index if not exists memphant_event_outbox_tenant_scope_idx on memphant.event_outbox
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, occurred_at, id);
create index if not exists memphant_event_outbox_tenant_delivery_idx on memphant.event_outbox
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, delivered_at, occurred_at, id);
create index if not exists memphant_event_outbox_tenant_trust_event_idx on memphant.event_outbox
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, trust_event_id);
create index if not exists memphant_retrieval_trace_tenant_scope_idx on memphant.retrieval_trace (tenant_id, scope_id, created_at);
create index if not exists memphant_deletion_generation_tenant_scope_idx on memphant.deletion_generation
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, state);
create index if not exists memphant_deletion_generation_tenant_actor_idx on memphant.deletion_generation
  (tenant_id, data_subject_id, subject_generation, requested_by);
create index if not exists memphant_job_state_tenant_run_idx on memphant.job_state (tenant_id, data_subject_id, scope_id, agent_node_id, state, run_after);
create index if not exists memphant_mutation_ledger_tenant_subject_idx on memphant.mutation_ledger
  (tenant_id, data_subject_id, subject_generation);
create index if not exists memphant_mutation_ledger_expiry_idx on memphant.mutation_ledger (expires_at);
create index if not exists memphant_blob_ledger_tenant_state_idx on memphant.blob_ledger
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, state, created_at);
create index if not exists memphant_belief_observation_tenant_unit_idx on memphant.belief_observation
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, memory_unit_id, observed_at);
create index if not exists memphant_review_event_tenant_trace_idx on memphant.review_event (tenant_id, data_subject_id, scope_id, actor_id, agent_node_id, trace_id);
create index if not exists memphant_review_event_unit_tenant_unit_idx on memphant.review_event_unit (tenant_id, data_subject_id, scope_id, agent_node_id, memory_unit_id);
create index if not exists memphant_review_event_unit_tenant_event_idx on memphant.review_event_unit (tenant_id, data_subject_id, review_event_id);
create index if not exists memphant_api_key_tenant_idx on memphant.api_key (tenant_id, created_at);
create index if not exists memphant_api_key_tenant_subject_idx on memphant.api_key
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, created_at)
  where data_subject_id is not null;
create index if not exists memphant_forgotten_source_tenant_kind_idx on memphant.forgotten_source
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, source_kind, forgotten_at);
create index if not exists memphant_job_state_tenant_scope_idx on memphant.job_state (tenant_id, data_subject_id, scope_id, agent_node_id, state);
create index if not exists memphant_scope_block_tenant_scope_idx on memphant.scope_block
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, version);
create index if not exists memphant_scope_block_tenant_actor_idx on memphant.scope_block
  (tenant_id, data_subject_id, subject_generation, updated_by_actor_id);

alter table memphant.tenant enable row level security;
alter table memphant.tenant force row level security;
alter table memphant.subject enable row level security;
alter table memphant.subject force row level security;
alter table memphant.subject_tombstone enable row level security;
alter table memphant.subject_tombstone force row level security;
alter table memphant.actor enable row level security;
alter table memphant.actor force row level security;
alter table memphant.scope enable row level security;
alter table memphant.scope force row level security;
alter table memphant.scope_policy enable row level security;
alter table memphant.scope_policy force row level security;
alter table memphant.agent_node enable row level security;
alter table memphant.agent_node force row level security;
alter table memphant.context_binding enable row level security;
alter table memphant.context_binding force row level security;
alter table memphant.episode enable row level security;
alter table memphant.episode force row level security;
alter table memphant.resource enable row level security;
alter table memphant.resource force row level security;
alter table memphant.memory_unit enable row level security;
alter table memphant.memory_unit force row level security;
alter table memphant.memory_edge enable row level security;
alter table memphant.memory_edge force row level security;
alter table memphant.embedding_profile enable row level security;
alter table memphant.embedding_profile force row level security;
alter table memphant.embedding enable row level security;
alter table memphant.embedding force row level security;
alter table memphant.citation enable row level security;
alter table memphant.citation force row level security;
alter table memphant.trust_event enable row level security;
alter table memphant.trust_event force row level security;
alter table memphant.event_outbox enable row level security;
alter table memphant.event_outbox force row level security;
alter table memphant.retrieval_trace enable row level security;
alter table memphant.retrieval_trace force row level security;
alter table memphant.deletion_generation enable row level security;
alter table memphant.deletion_generation force row level security;
alter table memphant.job_state enable row level security;
alter table memphant.job_state force row level security;
alter table memphant.mutation_ledger enable row level security;
alter table memphant.mutation_ledger force row level security;
alter table memphant.blob_ledger enable row level security;
alter table memphant.blob_ledger force row level security;
alter table memphant.belief_observation enable row level security;
alter table memphant.belief_observation force row level security;
alter table memphant.review_event enable row level security;
alter table memphant.review_event force row level security;
alter table memphant.review_event_unit enable row level security;
alter table memphant.review_event_unit force row level security;
alter table memphant.api_key enable row level security;
alter table memphant.api_key force row level security;
alter table memphant.forgotten_source enable row level security;
alter table memphant.forgotten_source force row level security;
alter table memphant.scope_block enable row level security;
alter table memphant.scope_block force row level security;

do $$
declare
  rel record;
begin
  for rel in
    select tablename from pg_tables where schemaname = 'memphant'
  loop
    execute format(
      'drop policy if exists memphant_%s_tenant_isolation on memphant.%I',
      rel.tablename,
      rel.tablename
    );
  end loop;
  execute 'drop policy if exists memphant_tenant_isolation on memphant.tenant';
end
$$;

do $$
declare
  rel record;
begin
  for rel in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'memphant' and c.relkind = 'r'
  loop
    execute format(
      'alter table memphant.%I owner to memphant_owner',
      rel.relname
    );
  end loop;
end
$$;

alter function memphant.current_tenant_id() owner to memphant_owner;
alter function memphant.bind_tenant(uuid) owner to memphant_owner;
alter function memphant.set_updated_at() owner to memphant_owner;

do $$
declare
  rel record;
begin
  for rel in
    select tablename from pg_tables
    where schemaname = 'memphant' and tablename <> 'schema_migrations'
  loop
    execute format(
      'create policy memphant_%s_owner on memphant.%I for all to memphant_owner using (true) with check (true)',
      rel.tablename,
      rel.tablename
    );
  end loop;
end
$$;

create policy memphant_tenant_isolation on memphant.tenant
  for all to memphant_app, memphant_worker, memphant_readonly
  using (id = memphant.current_tenant_id())
  with check (id = memphant.current_tenant_id());

create policy memphant_subject_tenant_isolation on memphant.subject for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_subject_tombstone_tenant_isolation on memphant.subject_tombstone for all to memphant_app, memphant_worker using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_actor_tenant_isolation on memphant.actor for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_scope_tenant_isolation on memphant.scope for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_scope_policy_tenant_isolation on memphant.scope_policy for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_agent_node_tenant_isolation on memphant.agent_node for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_context_binding_tenant_isolation on memphant.context_binding for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_episode_tenant_isolation on memphant.episode for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_resource_tenant_isolation on memphant.resource for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_memory_unit_tenant_isolation on memphant.memory_unit for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_memory_edge_tenant_isolation on memphant.memory_edge for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_embedding_profile_tenant_isolation on memphant.embedding_profile for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_embedding_tenant_isolation on memphant.embedding for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_citation_tenant_isolation on memphant.citation for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_trust_event_tenant_isolation on memphant.trust_event for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_event_outbox_tenant_isolation on memphant.event_outbox for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_retrieval_trace_tenant_isolation on memphant.retrieval_trace for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_deletion_generation_tenant_isolation on memphant.deletion_generation for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_job_state_tenant_isolation on memphant.job_state for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_mutation_ledger_tenant_isolation on memphant.mutation_ledger for all to memphant_app, memphant_worker using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_blob_ledger_tenant_isolation on memphant.blob_ledger for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_belief_observation_tenant_isolation on memphant.belief_observation for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_review_event_tenant_isolation on memphant.review_event for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_review_event_unit_tenant_isolation on memphant.review_event_unit for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_api_key_tenant_isolation on memphant.api_key for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_forgotten_source_tenant_isolation on memphant.forgotten_source for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_scope_block_tenant_isolation on memphant.scope_block for all to memphant_app, memphant_worker, memphant_readonly using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());

create or replace function memphant.authenticate_api_key(p_key_hash text)
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
         key.revoked_at is not null
  from memphant.api_key key
  where key.key_hash = p_key_hash
$$;

-- Composite key for a reflect scope-lane, used by claim_reflect_jobs to carry
-- advisory-locked lanes from the lock loop into the claim query.
do $$
begin
  if not exists (
    select 1 from pg_type t
    join pg_namespace n on n.oid = t.typnamespace
    where n.nspname = 'memphant' and t.typname = 'reflect_lane_key'
  ) then
    create type memphant.reflect_lane_key as (
      tenant_id uuid,
      data_subject_id uuid,
      subject_generation bigint,
      scope_id uuid,
      agent_node_id uuid
    );
  end if;
end
$$;

create or replace function memphant.claim_reflect_jobs(
  p_limit integer,
  p_tenant_id uuid default null,
  p_scope_id uuid default null,
  p_max_attempts integer default 5
)
returns setof memphant.job_state
language plpgsql
volatile
security definer
set search_path = memphant, pg_catalog
as $$
declare
  stale_lane record;
  cand_lane record;
  lane_limit integer := greatest(0, least(p_limit, 1000));
  locked_lane_keys memphant.reflect_lane_key[] := array[]::memphant.reflect_lane_key[];
begin
  update memphant.job_state job
  set state = 'dead'
  where job.state not in ('done', 'dead') and job.attempts >= p_max_attempts
    and (p_tenant_id is null or job.tenant_id = p_tenant_id)
    and (p_scope_id is null or job.scope_id = p_scope_id)
    and exists (
      select 1 from memphant.subject subject
      where subject.tenant_id = job.tenant_id
        and subject.id = job.data_subject_id
        and subject.generation = job.subject_generation
    );

  for stale_lane in
    select agent.tenant_id, agent.data_subject_id,
           subject.generation as subject_generation,
           agent.scope_id, agent.id as agent_node_id
    from memphant.agent_node agent
    join memphant.subject subject
      on subject.tenant_id = agent.tenant_id and subject.id = agent.data_subject_id
    where (p_tenant_id is null or agent.tenant_id = p_tenant_id)
      and (p_scope_id is null or agent.scope_id = p_scope_id)
      and exists (
        select 1 from memphant.job_state stale
        where stale.tenant_id = agent.tenant_id
          and stale.data_subject_id = agent.data_subject_id
          and stale.subject_generation = subject.generation
          and stale.scope_id = agent.scope_id and stale.agent_node_id = agent.id
          and stale.state = 'running'
          and stale.claimed_at < now() - interval '15 minutes'
      )
      and not exists (
        select 1 from memphant.job_state active
        where active.tenant_id = agent.tenant_id
          and active.data_subject_id = agent.data_subject_id
          and active.subject_generation = subject.generation
          and active.scope_id = agent.scope_id and active.agent_node_id = agent.id
          and active.state = 'running'
          and active.claimed_at >= now() - interval '15 minutes'
      )
    order by agent.tenant_id, agent.id
    limit greatest(0, least(p_limit, 1000))
    for update of agent skip locked
  loop
    update memphant.job_state job
    set claim_generation = job.claim_generation + 1,
        state = 'queued', claimed_at = null, updated_at = now()
    where job.tenant_id = stale_lane.tenant_id
      and job.data_subject_id = stale_lane.data_subject_id
      and job.subject_generation = stale_lane.subject_generation
      and job.scope_id = stale_lane.scope_id
      and job.agent_node_id = stale_lane.agent_node_id
      and job.state not in ('done', 'dead');
  end loop;

  -- Serialize lane ownership with a BLOCKING per-lane transaction advisory
  -- lock, taken here as its own statement per candidate lane — not inside the
  -- claim query.
  --
  -- Why a blocking lock in a separate loop, and not the obvious in-query gates:
  --   * `for update of agent skip locked` on the lane's agent_node row is not a
  --     reliable gate. Its LockRows node sits above the Sort in the plan, so
  --     under load two concurrent claimers can both pass it in the race window,
  --     then split the lane at the job-level `for update of job skip locked`
  --     scan (owner A takes the first N jobs in queue_order, B skip-locks those
  --     and takes the disjoint tail).
  --   * `pg_try_advisory_xact_lock` inside the claim query (WHERE clause or a
  --     CTE filter) does not close the window either: lane ADMISSION and the
  --     lock are evaluated against the same MVCC snapshot, but the lock loop
  --     and the claim run as separate plpgsql statements with separate
  --     snapshots. A claimer can admit a lane on a snapshot taken just before a
  --     peer commits the head jobs as running, TRY-lock succeeds because the
  --     peer has already released on commit, and it then claims the tail. Every
  --     `try`-based placement leaves this residual split (~0.3% under a tight
  --     concurrent hammer).
  -- A blocking `pg_advisory_xact_lock` removes the window: the loser WAITS for
  -- the winner to commit and release, and only then runs its claim query, whose
  -- fresh snapshot sees the winner's head jobs `running` — so the tail is
  -- excluded (see the earlier-running guard in `eligible`) and the loser claims
  -- nothing. Lanes are locked in a deterministic order (tenant_id, agent_id),
  -- so multiple claimers acquire in the same order and cannot deadlock. Held to
  -- transaction end, covering the job claim below. Lanes are processed serially
  -- anyway, so the brief wait costs no real throughput.
  for cand_lane in
    select agent.tenant_id, agent.data_subject_id,
           subject.generation as subject_generation,
           agent.scope_id, agent.id as agent_node_id
    from memphant.agent_node agent
    join memphant.subject subject
      on subject.tenant_id = agent.tenant_id and subject.id = agent.data_subject_id
    where (p_tenant_id is null or agent.tenant_id = p_tenant_id)
      and (p_scope_id is null or agent.scope_id = p_scope_id)
      and exists (
        select 1 from memphant.job_state candidate
        where candidate.tenant_id = agent.tenant_id
          and candidate.data_subject_id = agent.data_subject_id
          and candidate.subject_generation = subject.generation
          and candidate.scope_id = agent.scope_id and candidate.agent_node_id = agent.id
          and candidate.state in ('queued', 'running')
          and candidate.attempts < p_max_attempts and candidate.run_after <= now()
          and (candidate.claimed_at is null or candidate.claimed_at < now() - interval '15 minutes')
      )
    order by agent.tenant_id, agent.id
    limit lane_limit
  loop
    exit when cardinality(locked_lane_keys) >= lane_limit;
    perform pg_advisory_xact_lock(
      hashtextextended(
        cand_lane.tenant_id::text || ':' || cand_lane.data_subject_id::text || ':'
          || cand_lane.subject_generation::text || ':' || cand_lane.scope_id::text || ':'
          || cand_lane.agent_node_id::text,
        0));
    locked_lane_keys := locked_lane_keys || array[
      row(cand_lane.tenant_id, cand_lane.data_subject_id, cand_lane.subject_generation,
          cand_lane.scope_id, cand_lane.agent_node_id)::memphant.reflect_lane_key];
  end loop;

  return query
  with locked_lanes as (
    select (key).tenant_id, (key).data_subject_id, (key).subject_generation,
           (key).scope_id, (key).agent_node_id
    from unnest(locked_lane_keys) as key
  ), eligible as (
    select job.tenant_id, job.id, job.queue_order
    from memphant.job_state job
    join memphant.subject subject
      on subject.tenant_id = job.tenant_id and subject.id = job.data_subject_id
     and subject.generation = job.subject_generation
    join locked_lanes lane
      on lane.tenant_id = job.tenant_id and lane.data_subject_id = job.data_subject_id
     and lane.subject_generation = job.subject_generation and lane.scope_id = job.scope_id
     and lane.agent_node_id = job.agent_node_id
    where job.state in ('queued', 'running') and job.attempts < p_max_attempts
      and job.run_after <= now()
      and (job.claimed_at is null or job.claimed_at < now() - interval '15 minutes')
      and not exists (
        -- Do not claim a job while an earlier job in the same lane is not yet
        -- claimable-past: either it is scheduled for the future (run_after >
        -- now, the original delayed-predecessor guard) OR it is already
        -- `running` under a live claim held by another worker. The second case
        -- is what keeps two concurrent claims from splitting one lane: the
        -- lane-admission snapshot can be stale (a claimer may admit the lane
        -- just before a peer commits the head jobs as running), but this guard
        -- is evaluated in the claim query's own, later snapshot, where the
        -- peer's running head IS visible — so the tail jobs are excluded and
        -- the loser claims nothing. The serial-per-lane invariant (a later job
        -- never runs before an earlier one completes) is thus enforced at claim
        -- time, not left to the lane lock alone.
        select 1 from memphant.job_state earlier
        where earlier.tenant_id = job.tenant_id
          and earlier.data_subject_id = job.data_subject_id
          and earlier.subject_generation = job.subject_generation
          and earlier.scope_id = job.scope_id and earlier.agent_node_id = job.agent_node_id
          and earlier.state not in ('done', 'dead')
          and earlier.queue_order < job.queue_order
          and (
            earlier.run_after > now()
            or (earlier.state = 'running'
                and earlier.claimed_at >= now() - interval '15 minutes')
          )
      )
    order by job.queue_order
    for update of job skip locked
  ), ranked as (
    select eligible.tenant_id, eligible.id, eligible.queue_order,
           row_number() over (
             partition by eligible.tenant_id order by eligible.queue_order
           ) as rn
    from eligible
  ), claimed as (
    select ranked.tenant_id, ranked.id from ranked
    order by ranked.rn, ranked.queue_order
    limit greatest(0, least(p_limit, 1000))
  )
  update memphant.job_state job
  set state = 'running', claimed_at = now(), attempts = job.attempts + 1
  from claimed
  where job.tenant_id = claimed.tenant_id and job.id = claimed.id
  returning job.*;
end;
$$;

create or replace function memphant.dead_letter_count()
returns bigint
language sql
stable
security definer
set search_path = memphant, pg_catalog
as $$
  select count(*) from memphant.job_state where state = 'dead'
$$;

create or replace function memphant.provision_tenant(
  p_slug text,
  p_plan text default 'dev',
  p_region text default 'local'
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
  insert into memphant.tenant (id, slug, plan, region)
  values (created_id, p_slug, p_plan, p_region);
  return created_id;
end
$$;

create or replace function memphant.provision_api_key(
  p_tenant_id uuid,
  p_key_hash text,
  p_label text,
  p_max_trust text,
  p_data_subject_id uuid default null,
  p_subject_generation bigint default null,
  p_actor_id uuid default null,
  p_scope_id uuid default null,
  p_agent_node_id uuid default null
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
     actor_id, scope_id, agent_node_id)
  values
    (created_id, p_tenant_id, p_key_hash, p_label, p_max_trust, p_data_subject_id,
     p_subject_generation, p_actor_id, p_scope_id, p_agent_node_id);
  return created_id;
end
$$;

create or replace function memphant.revoke_api_key(p_id uuid)
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
  where id = p_id and revoked_at is null;
  get diagnostics changed = row_count;
  return changed = 1;
end
$$;

alter function memphant.authenticate_api_key(text) owner to memphant_owner;
alter function memphant.claim_reflect_jobs(integer, uuid, uuid, integer) owner to memphant_owner;
alter function memphant.dead_letter_count() owner to memphant_owner;
alter function memphant.provision_tenant(text, text, text) owner to memphant_owner;
alter function memphant.provision_api_key(uuid, text, text, text, uuid, bigint, uuid, uuid, uuid) owner to memphant_owner;
alter function memphant.revoke_api_key(uuid) owner to memphant_owner;

revoke all on all tables in schema memphant from public;
revoke all on all sequences in schema memphant from public;
revoke all on all functions in schema memphant from public;

grant select on memphant.tenant to memphant_app, memphant_worker, memphant_readonly;
grant select, insert, update, delete on
  memphant.subject, memphant.subject_tombstone, memphant.actor, memphant.agent_node, memphant.context_binding, memphant.scope,
  memphant.scope_policy, memphant.episode, memphant.resource, memphant.memory_unit,
  memphant.memory_edge, memphant.embedding_profile, memphant.embedding,
  memphant.citation, memphant.trust_event, memphant.event_outbox,
  memphant.retrieval_trace, memphant.deletion_generation, memphant.job_state,
  memphant.mutation_ledger, memphant.blob_ledger, memphant.belief_observation, memphant.review_event,
  memphant.review_event_unit, memphant.scope_block, memphant.forgotten_source
  to memphant_app;
grant select on
  memphant.subject, memphant.actor, memphant.agent_node, memphant.context_binding,
  memphant.scope, memphant.scope_policy
  to memphant_worker;
grant select, insert, update, delete on
  memphant.subject_tombstone, memphant.episode, memphant.resource, memphant.memory_unit, memphant.memory_edge,
  memphant.embedding_profile, memphant.embedding, memphant.citation,
  memphant.trust_event, memphant.event_outbox, memphant.retrieval_trace,
  memphant.deletion_generation, memphant.job_state, memphant.mutation_ledger, memphant.blob_ledger,
  memphant.belief_observation, memphant.review_event, memphant.review_event_unit,
  memphant.scope_block, memphant.forgotten_source
  to memphant_worker;
grant select on
  memphant.subject, memphant.actor, memphant.agent_node, memphant.context_binding, memphant.scope,
  memphant.scope_policy, memphant.episode, memphant.resource, memphant.memory_unit,
  memphant.memory_edge, memphant.embedding_profile, memphant.embedding,
  memphant.citation, memphant.trust_event, memphant.event_outbox,
  memphant.retrieval_trace, memphant.deletion_generation, memphant.job_state,
  memphant.blob_ledger, memphant.belief_observation, memphant.review_event,
  memphant.review_event_unit, memphant.scope_block, memphant.forgotten_source
  to memphant_readonly;
grant usage, select on all sequences in schema memphant to memphant_app, memphant_worker;

grant execute on function memphant.current_tenant_id() to memphant_app, memphant_worker, memphant_readonly;
grant execute on function memphant.bind_tenant(uuid) to memphant_app, memphant_worker, memphant_readonly;
grant execute on function memphant.authenticate_api_key(text) to memphant_authn;
grant execute on function memphant.claim_reflect_jobs(integer, uuid, uuid, integer) to memphant_worker;
grant execute on function memphant.dead_letter_count() to memphant_app, memphant_worker;
grant execute on function memphant.provision_tenant(text, text, text) to memphant_provisioner;
grant execute on function memphant.provision_api_key(uuid, text, text, text, uuid, bigint, uuid, uuid, uuid) to memphant_provisioner;
grant execute on function memphant.revoke_api_key(uuid) to memphant_provisioner;

alter default privileges for role memphant_owner in schema memphant revoke all on tables from public;
alter default privileges for role memphant_owner in schema memphant revoke all on sequences from public;
alter default privileges for role memphant_owner in schema memphant revoke all on functions from public;

do $$
declare
  rel record;
begin
  for rel in
    select c.table_name as tablename
    from information_schema.columns c
    where c.table_schema = 'memphant'
      and c.column_name = 'updated_at'
  loop
    execute format('drop trigger if exists set_updated_at on memphant.%I', rel.tablename);
    execute format(
      'create trigger set_updated_at before update on memphant.%I for each row execute function memphant.set_updated_at()',
      rel.tablename
    );
  end loop;
end
$$;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values ('20260703_001_wsa_bootstrap', '20260703_001_wsa_bootstrap', 'additive')
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
