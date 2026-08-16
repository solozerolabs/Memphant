-- Remove the four tables that have no reader, no writer, and no surface.
-- migration_kind: rewrite
--
-- Verified against THIS tree on 2026-08-15 by grep across crates/, scripts/,
-- tests/, plugins/, openapi/ and mcp/: outside this migration directory the
-- only references to these four tables were catalogue lists (the Rust
-- `REQUIRED_TABLES` in memphant-store-postgres, `check_memphant_live_catalog.py`,
-- `check_memphant_migration_contract.py`, and the pytest migration contract).
-- All four lists are updated in the same commit. No query in the tree selects
-- from, inserts into, or joins any of them.
--
-- 1. `memphant.trust_event` — bootstrap comment: "no producer (no
--    trust-decision writer wired)". Trust decisions are expressed today on the
--    unit itself (`memory_unit.trust_level`, `reinforcement_count`, `state`)
--    and in `mutation_ledger`; a second, unwired ledger reads as a surface
--    that exists.
--
-- 2. `memphant.event_outbox` — bootstrap comment: "no consumer (no outbox
--    drain/relay job)". Its three indexes were already dropped in
--    `20260801_009`. Also carries the only FK onto `trust_event`, so it must
--    go first.
--
-- 3. `memphant.blob_ledger` — no writer. (`live_blob_hashes` /
--    `ledger_blob_hashes` in memphant-eval name an OPS-lane check that never
--    read this table.)
--
-- 4. `memphant.belief_observation` — no writer. Confirmation/disconfirmation
--    is expressed by `reinforcement_count` and the bitemporal interval on
--    `memory_unit`, which is what the trust ladder actually reads.
--
-- Policies, indexes, and the `set_updated_at` trigger are owned by each table
-- and fall with `drop table`. Classification: `rewrite` (tables are removed),
-- declared in both the header and the ledger row.

drop table if exists memphant.event_outbox;
drop table if exists memphant.trust_event;
drop table if exists memphant.blob_ledger;
drop table if exists memphant.belief_observation;

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260815_012_drop_dead_tables',
  -- Compatibility floor stays at 011: dropping tables that no binary references
  -- breaks nothing older, so an 011-era binary remains valid against this head.
  '20260814_011_portable_agent_memory',
  'rewrite'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
