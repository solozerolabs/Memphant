-- Drop the two dead FSRS columns on memphant.memory_unit: `difficulty` and
-- `stability_days`.
-- migration_kind: breaking
--
-- 26 §10 Decision C. Verified against THIS tree on 2026-08-17 by grep across
-- crates/, scripts/, tests/: these columns were WRITE-ONLY dead state. They were
-- staged NULL on every insert (`bind(unit.difficulty)`/`bind(unit.stability_days)`)
-- and never written back — no `update ... set difficulty`/`set stability_days`
-- exists anywhere. At recall the FSRS decay seed read them as
-- `unit.stability_days.unwrap_or(DEFAULT_STABILITY_DAYS)` /
-- `unit.difficulty.unwrap_or(DEFAULT_DIFFICULTY)`, so a perpetually-NULL column
-- always resolved to the default (7.0 / 5.0). Retrievability reorders MARKED
-- units via `review_event` + `reinforcement_count`, which are untouched here.
--
-- The same commit removes the columns from `StoredMemoryUnit`, the PG
-- select/insert, the `DecayScore` struct, and the `dsr_stability_days` /
-- `dsr_difficulty` fields on `RecallCandidateTrace`; the decay seed now reads the
-- DEFAULT constants directly. The inline `check (difficulty between 0 and 10)`
-- constraint falls with the column.
--
-- Classification: `breaking` (a `drop column`) — a binary built before this
-- migration selects the now-absent columns, so the compat floor rises to this
-- version. `memory_unit` remains a required table; only two columns are removed.

alter table memphant.memory_unit drop column if exists difficulty;
alter table memphant.memory_unit drop column if exists stability_days;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260817_013_drop_dead_fsrs_columns',
  -- Breaking: a pre-013 binary recall SELECT names these columns, so it is not
  -- valid against this head. The floor is this version itself.
  '20260817_013_drop_dead_fsrs_columns',
  'breaking'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
