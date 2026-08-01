-- Drop `episode.retention_tier` and its permanently-empty partial index.
-- migration_kind: rewrite
--
-- Why: the column had ZERO readers and ZERO writers. Verified 2026-07-31 on
-- this tree, not inherited from the plan:
--
--   grep -rn 'retention_tier' crates --include='*.rs'   -> 0 hits
--   grep -rn 'retention_tier' bindings openapi mcp      -> 0 hits
--   grep -rn 'retention_tier' scripts tests             -> 0 hits
--
-- The only hits anywhere in the tree were this migration, spec prose, and
-- build-log measurements — and those measurements are the point:
-- `docs/build-log/2026-08-01-preference-lane-first-measurement.md` and
-- `2026-07-31-preference-writepath.md` both report the live distribution as
-- `hot` x 8147, i.e. every episode ever written sat in the default and nothing
-- ever moved one. The `tier_episode` job named in `04` §2.4, `14` §3 and `02`
-- §6 does not exist in code and never did. The partial index
-- `... where retention_tier <> 'hot'` was therefore empty for its entire life:
-- it indexed zero rows, on every deployment, always.
--
-- This is a rescue, not a gap (one-plan §3/§4). Three independent lines say do
-- not build scored tiering: eviction-under-budget is what makes the best
-- week-3 architecture lose by week 9 (arXiv:2607.21962); no OSS peer ships
-- scored tiering (Hindsight ships binary live/archive and deliberately REMOVED
-- the archive's embedding column after a dimension mismatch broke a revert);
-- and `04` §13.3/§13.4 are withdrawn by the plan of record. If demotion ever
-- returns it is a recall-time predicate and needs no schema.
--
-- Classification: the header declares `rewrite` because the boundary checker
-- (`scripts/check_memphant_migration_boundary.py`) requires that header for any
-- `drop index`. The ledger row declares `breaking`, which is what
-- `scripts/check_memphant_migration_class.py` computes for `drop column` +
-- `drop index` where no table is dropped. Both are correct, both are checked,
-- and note that the classifier is a plain substring scan: writing the two words
-- "table" and "drop" adjacently anywhere in a comment reclassifies the file.
--
-- Compat floor: this DOES move it. An older binary cannot be rolled back onto
-- this schema and have `insert into memphant.episode` succeed, because the
-- bootstrap's `retention_tier` is `not null` with a default that no longer
-- exists. No binary in the tree writes the column, so no code change pairs with
-- this migration; the floor moves because SCHEMA rollback is what is unsafe.
--
-- No data loss of consequence: every row in the column holds the literal 'hot'.

drop index if exists memphant.memphant_episode_tenant_retention_idx;

alter table memphant.episode
  drop column if exists retention_tier;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260801_008_drop_retention_tier',
  '20260801_008_drop_retention_tier',
  'breaking'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
