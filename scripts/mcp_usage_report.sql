-- MCP coding-lane adherence report: did the agent actually use memory, and how?
--
-- Reads only the existing Postgres audit trail (no added logging). Scope the
-- dogfood to ONE coding subject so this filter isolates the agent's usage:
--
--   psql "$DATABASE_URL" \
--     -v subject="'00000000-0000-0000-0000-000000000001'" \
--     -v since="'2026-08-15 00:00:00+00'" \
--     -f scripts/mcp_usage_report.sql
--
-- `subject` is the coding key's data_subject_id; `since` bounds the window.
-- An empty recall is honest, not a failure: it means the scope had nothing to
-- serve. The signal is whether the agent CALLS recall at all and whether it
-- writes back (remember/correct/invalidate) and reports outcomes.

\set QUIET on
\pset footer off

-- recall: how often called, how often it served something vs honest-empty.
select
  'recall' as surface,
  count(*)                                                    as calls,
  count(*) filter (where jsonb_array_length(candidates) > 0)  as served_ge1_candidate,
  count(*) filter (where jsonb_array_length(candidates) = 0)  as honest_empty,
  count(*) filter (where jsonb_array_length(citations)  > 0)  as served_citation
from memphant.retrieval_trace
where data_subject_id = :subject
  and created_at >= :since;

-- writes: the agent contributing memory back (remember=retain, correct, invalidate).
select
  verb,
  count(*) as completed_writes
from memphant.mutation_ledger
where data_subject_id = :subject
  and state = 'completed'
  and verb in ('retain', 'correct', 'invalidate')
  and created_at >= :since
group by verb
order by verb;

-- outcomes: report_memory_use feedback the agent sent on recalled packs.
select
  outcome,
  count(*) as reports
from memphant.review_event
where data_subject_id = :subject
  and created_at >= :since
group by outcome
order by count(*) desc;
