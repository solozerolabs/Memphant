alter table memphant.mutation_ledger
  drop constraint mutation_ledger_verb_check,
  add constraint mutation_ledger_verb_check check (verb in (
    'retain','reflect','correct','forget','mark','task_outcome','task_memory_event',
    'file_sync','erase_subject'
  ));

create table if not exists memphant.task_outcome (
  tenant_id uuid not null,
  data_subject_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  scope_id uuid not null,
  agent_node_id uuid not null,
  actor_id uuid not null,
  task_id uuid not null,
  harness_id text not null check (length(btrim(harness_id)) > 0),
  model_id text not null check (length(btrim(model_id)) > 0),
  started_at timestamptz not null,
  ended_at timestamptz not null check (ended_at >= started_at),
  completion_status text not null check (completion_status in ('completed','failed','cancelled')),
  validator_status text not null check (validator_status in ('passed','failed','not_run')),
  tool_count bigint not null check (tool_count >= 0),
  failure_count bigint not null check (failure_count >= 0),
  retry_count bigint not null check (retry_count >= 0),
  planned_files text[],
  actual_files text[] not null,
  scope_recall double precision check (scope_recall between 0 and 1),
  scope_precision double precision check (scope_precision between 0 and 1),
  scope_jaccard double precision check (scope_jaccard between 0 and 1),
  transcript_sha256 text not null check (transcript_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at timestamptz not null,
  primary key (tenant_id, task_id),
  unique (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, actor_id, task_id),
  foreign key (tenant_id, data_subject_id) references memphant.subject (tenant_id, id) on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id) references memphant.scope (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, actor_id) references memphant.actor (tenant_id, data_subject_id, id),
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)
    references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)
);

create table if not exists memphant.task_memory_event (
  id uuid not null default gen_random_uuid(),
  tenant_id uuid not null,
  data_subject_id uuid not null,
  subject_generation bigint not null check (subject_generation >= 0),
  scope_id uuid not null,
  agent_node_id uuid not null,
  actor_id uuid not null,
  task_id uuid not null,
  memory_unit_id uuid not null,
  event text not null check (event in ('shown','activated','helpful','harmful','silenced')),
  attribution text not null check (attribution in ('explicit_user','deterministic_scorer','randomized_counterfactual','observational')),
  recorded_at timestamptz not null,
  primary key (tenant_id, id),
  unique (tenant_id, task_id, memory_unit_id, event, attribution),
  foreign key (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, actor_id, task_id)
    references memphant.task_outcome (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, actor_id, task_id)
    on delete cascade,
  foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, memory_unit_id)
    references memphant.memory_unit (tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, id)
);

create index if not exists memphant_task_outcome_tenant_scope_idx on memphant.task_outcome
  (tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, recorded_at);
create index if not exists memphant_task_memory_event_tenant_unit_idx on memphant.task_memory_event
  (tenant_id, memory_unit_id, event, recorded_at);

alter table memphant.task_outcome owner to memphant_owner;
alter table memphant.task_memory_event owner to memphant_owner;
alter table memphant.task_outcome enable row level security;
alter table memphant.task_outcome force row level security;
alter table memphant.task_memory_event enable row level security;
alter table memphant.task_memory_event force row level security;
create policy memphant_task_outcome_owner on memphant.task_outcome for all to memphant_owner using (true) with check (true);
create policy memphant_task_memory_event_owner on memphant.task_memory_event for all to memphant_owner using (true) with check (true);
create policy memphant_task_outcome_tenant_isolation on memphant.task_outcome for all to memphant_app, memphant_worker, memphant_readonly
  using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());
create policy memphant_task_memory_event_tenant_isolation on memphant.task_memory_event for all to memphant_app, memphant_worker, memphant_readonly
  using (tenant_id = memphant.current_tenant_id()) with check (tenant_id = memphant.current_tenant_id());

revoke all on memphant.task_outcome, memphant.task_memory_event from public;
grant select, insert on memphant.task_outcome, memphant.task_memory_event to memphant_app, memphant_worker;
grant select on memphant.task_outcome, memphant.task_memory_event to memphant_readonly;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values ('20260808_010_task_outcome_ledger', '20260808_010_task_outcome_ledger', 'breaking')
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
