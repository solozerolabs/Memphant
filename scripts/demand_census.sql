-- Phase 2 Stage 0 — feasibility census (is the demand read measurable at this scale?)
--
-- Answers the ONLY question Stage 0 gates on: are there enough INDEPENDENT coding
-- subjects with real sessions + terminal outcomes to power a per-subject holdout?
-- Effective N is independent SUBJECTS, not sessions (within-subject correlation),
-- so this counts subjects and per-subject distribution, then hands the numbers to
-- scripts/instrument_power.py for the max-attainable-power ceiling.
--
-- $0, read-only, no capture data required. Run BEFORE any build/calendar spend.
--
--   psql "$DATABASE_URL" -v since="'2026-01-01 00:00:00+00'" -f scripts/demand_census.sql
--
-- A row of zeros is the decisive answer, not a failure: it means no real coding
-- traffic is instrumented through memphant yet, so the experiment is not
-- measurable and Stage 0 STOPs (wire capture+injection into the live lane first).

\set QUIET on
\pset footer off
-- default the window to "all time" if -v since was not passed
\if :{?since} \else \set since '''1970-01-01 00:00:00+00''' \endif

-- 1. Feasibility headline: independent subjects + the two signals the primary needs.
--    recalls  = the exposure substrate (nothing to recall-of-capture without it).
--    outcomes = the terminal fully-observed signal (task_outcome), NOT "no revert seen".
select
  (select count(distinct data_subject_id) from memphant.retrieval_trace where created_at >= :since) as subjects_with_recall,
  (select count(distinct data_subject_id) from memphant.task_outcome    where recorded_at >= :since) as subjects_with_outcome,
  (select count(*) from memphant.retrieval_trace where created_at   >= :since)                       as recalls,
  (select count(*) from memphant.task_outcome    where recorded_at  >= :since)                       as terminal_outcomes,
  (select count(*) from memphant.episode         where created_at   >= :since)                       as episodes,
  (select count(distinct task_id) from memphant.task_outcome where recorded_at >= :since)            as task_episodes;

-- 2. Terminal-outcome density + mix (the Stage-2 primary's observability).
--    Sparse or all-one-value here => the primary can't move => STOP or fall back.
select
  completion_status,
  validator_status,
  count(*) as n
from memphant.task_outcome
where recorded_at >= :since
group by completion_status, validator_status
order by n desc;

-- 3. Per-subject distribution = the ICC input for instrument_power.py.
--    Concentration in one subject means effective N ~ 1 regardless of row count.
select
  data_subject_id,
  count(*)                                              as terminal_outcomes,
  count(*) filter (where completion_status = 'success') as successes,
  (select count(*) from memphant.retrieval_trace r
     where r.data_subject_id = t.data_subject_id and r.created_at >= :since) as recalls
from memphant.task_outcome t
where recorded_at >= :since
group by data_subject_id
order by terminal_outcomes desc
limit 50;
