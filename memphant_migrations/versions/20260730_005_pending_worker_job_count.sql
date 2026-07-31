-- The queue-wide pending-job count must survive FORCE RLS.
--
-- 20260730_004 made the served pools assume a capability role, so the worker
-- pool now runs as `memphant_worker` and the `memphant_job_state_tenant_isolation`
-- policy applies to it. The worker's drain-exit check is a QUEUE-WIDE count
-- with no tenant bound: under that policy it matched zero rows and returned 0,
-- so `MEMPHANT_WORKER_DRAIN=1` exited after a single tick and reported that
-- partial number as a completed drain. Measured on 401 queued reflect_episode
-- jobs: `drain completed=256` with 145 still `queued` (batch size, not queue
-- depth, bounded the run). Claiming was unaffected because
-- `memphant.claim_reflect_jobs` is already `security definer`.
--
-- Same shape as the sibling `memphant.dead_letter_count()`: a `security
-- definer` function owned by `memphant_owner`, which holds the `using(true)`
-- owner policy. Execute is granted to `memphant_worker` only — this count is
-- fleet-drain orchestration and must never be reachable from a tenant request
-- surface.
create or replace function memphant.pending_worker_job_count()
returns bigint
language sql
stable
security definer
set search_path = memphant, pg_catalog
as $$
  select count(*) from memphant.job_state where state in ('queued', 'running')
$$;

alter function memphant.pending_worker_job_count() owner to memphant_owner;
revoke all on function memphant.pending_worker_job_count() from public;
grant execute on function memphant.pending_worker_job_count() to memphant_worker;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260730_005_pending_worker_job_count',
  '20260730_004_served_login_roles',
  'additive'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
