-- Forward-apply the bounded worker-claim query to databases that already
-- installed the bootstrap migration. Fresh databases receive the same function
-- from 20260703_001_wsa_bootstrap.sql.

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
  ), blocking_predecessors as materialized (
    -- Compute the first live/delayed blocker once per locked lane. A
    -- correlated `not exists (earlier.queue_order < job.queue_order)` probes
    -- every earlier queued row for every candidate when no blocker exists,
    -- making a 64k-event lane quadratic.
    select blocker.tenant_id, blocker.data_subject_id,
           blocker.subject_generation, blocker.scope_id,
           blocker.agent_node_id, min(blocker.queue_order) as first_queue_order
    from memphant.job_state blocker
    join locked_lanes lane
      on lane.tenant_id = blocker.tenant_id
     and lane.data_subject_id = blocker.data_subject_id
     and lane.subject_generation = blocker.subject_generation
     and lane.scope_id = blocker.scope_id
     and lane.agent_node_id = blocker.agent_node_id
    where blocker.state not in ('done', 'dead')
      and (
        blocker.run_after > now()
        or (blocker.state = 'running'
            and blocker.claimed_at >= now() - interval '15 minutes')
      )
    group by blocker.tenant_id, blocker.data_subject_id,
             blocker.subject_generation, blocker.scope_id,
             blocker.agent_node_id
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
    left join blocking_predecessors blocker
      on blocker.tenant_id = job.tenant_id
     and blocker.data_subject_id = job.data_subject_id
     and blocker.subject_generation = job.subject_generation
     and blocker.scope_id = job.scope_id
     and blocker.agent_node_id = job.agent_node_id
    where job.state in ('queued', 'running') and job.attempts < p_max_attempts
      and job.run_after <= now()
      and (job.claimed_at is null or job.claimed_at < now() - interval '15 minutes')
      and (blocker.first_queue_order is null
           or job.queue_order < blocker.first_queue_order)
    order by job.queue_order
  ), ranked as (
    select eligible.tenant_id, eligible.id, eligible.queue_order,
           row_number() over (
             partition by eligible.tenant_id order by eligible.queue_order
           ) as rn
    from eligible
  ), claim_candidates as (
    select ranked.tenant_id, ranked.id from ranked
    order by ranked.rn, ranked.queue_order
    limit greatest(0, least(p_limit, 1000))
  ), claimed as (
    -- Lock only the bounded candidate set. Locking inside `eligible` puts the
    -- LockRows node above its Sort and locks every eligible job before the
    -- downstream LIMIT (64k event lanes took minutes per four-job tick).
    -- The transaction-scoped lane advisory lock above already serializes
    -- claimers for a lane; this row lock only protects the chosen jobs.
    select job.tenant_id, job.id
    from memphant.job_state job
    join claim_candidates candidate
      on candidate.tenant_id = job.tenant_id and candidate.id = job.id
    for update of job skip locked
  )
  update memphant.job_state job
  set state = 'running', claimed_at = now(), attempts = job.attempts + 1
  from claimed
  where job.tenant_id = claimed.tenant_id and job.id = claimed.id
  returning job.*;
end;
$$;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260724_003_worker_claim_throughput',
  '20260723_002_file_sync_mutation_verb',
  'additive'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
