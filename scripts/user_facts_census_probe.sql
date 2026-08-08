-- SC lane §A.2 census probe for syndai.user_facts.
-- AGGREGATES ONLY. Selects no label, value, user_id, or any row identifier.
-- Governed by docs/build-log/2026-08-05-user-facts-census-privacy-prereg.md.
-- Run read-only: PGOPTIONS="-c default_transaction_read_only=on"

select
  count(*)                                                as rows_total,
  count(distinct user_id)                                 as distinct_users,
  count(*) filter (where supersedes_fact_id is not null)  as supersede_arcs,
  count(*) filter (where valid_to is not null)            as closed_generations,
  count(*) filter (where review_status = 'active')        as active_rows,
  count(*) filter (where review_status = 'proposed')      as proposed_rows,
  count(distinct (user_id, lower(label)))                 as distinct_subjects,
  min(created_at)::date                                   as first_day,
  max(created_at)::date                                   as last_day
from syndai.user_facts;

-- Subject-level restatement shape: how many (user, label) subjects carry more
-- than one generation. This is the supersede-arc ceiling, counted without
-- reading any label.
select
  generations,
  count(*) as subjects
from (
  select user_id, lower(label) as l, count(*) as generations
  from syndai.user_facts
  group by 1, 2
) s
group by 1
order by 1;

-- Category mix (counts only) — bears on whether coexist pairs are constructible.
select category, count(*) as n
from syndai.user_facts
group by 1
order by 2 desc;
