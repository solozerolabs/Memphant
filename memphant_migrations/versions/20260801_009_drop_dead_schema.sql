-- Remove schema that has no reader, no writer, and no surface.
-- migration_kind: rewrite
--
-- Each item below was verified against THIS tree on 2026-07-31, by grep across
-- crates/, scripts/, tests/, bindings/, openapi/, mcp/, examples/ and
-- benchmarks/ — not taken on trust from the plan.
--
-- 1. `subject.privacy_policy jsonb` — 0 hits outside this migration directory.
--    A jsonb column named `privacy_policy` that nothing reads is worse than
--    absent: it reads as a policy surface that exists.
--
-- 2. `memory_unit.last_confirmed_at timestamptz` — 0 hits outside this
--    directory. Confirmation is expressed through `belief_observation` and the
--    bitemporal interval; this column was a third, unwired way to say it.
--
-- 3. `episode.blob_hash text` — 0 hits outside this directory. (The
--    `live_blob_hashes` / `tombstoned_blob_hashes` / `ledger_blob_hashes`
--    fields in memphant-eval are the OPS-lane blob ledger check and are
--    unrelated; `blob_ledger` is untouched here.)
--
-- 4. `memphant.scope_block` — the table already carried
--    `comment on table ... 'DORMANT (2026-07-22): schema-only, no surface.'`
--    Its verb was plan item B1 and B1 has since been re-scoped to structured
--    state, which supersedes by naming prior unit ids and needs no block table.
--    Its only references were four catalogue lists (the Rust
--    `REQUIRED_TABLES`, two check scripts, and the migration contract test),
--    all updated in the same commit. No query in the tree selects from it.
--
-- 5. The three `event_outbox` indexes. The table stays — it is a frozen
--    contract with a `DORMANT` comment — but it has no consumer, so all three
--    indexes have indexed zero rows since the day they were created. An index
--    on a table with no producer is pure write-amplification insurance against
--    a query nobody has ever written. The table and its RLS policy are kept.
--
-- 6. `embedding_profile.index_strategy`'s `hnsw_*` CHECK values. Every profile
--    ever constructed in the tree passes `"exact"` (memphant-core/src/lib.rs:695,
--    memphant-store-testkit, three test fixtures); `hnsw_full`,
--    `hnsw_subvector` and `hnsw_binary` have 0 hits anywhere. Beyond dead, they
--    are currently IMPOSSIBLE: `vec halfvec` has no dimension typmod so pgvector
--    cannot build the index, the query has no matching cast, and the
--    `, unit.body` determinism tie-break forces a full sort that disqualifies
--    index ordering (one-plan §4). Leaving the vocabulary in the CHECK invites
--    exactly the one-line fix that cannot work.
--
-- 7. `memory_unit.state`'s `captured`, `extracted`, `retired`. No write path in
--    the tree ever produces them: the only `UnitState::{Captured,Extracted,Retired}`
--    occurrences were test fixtures, one recall drop-reason arm, and one
--    name-mapping arm. The Rust enum loses the three variants in the same
--    commit, so the type and the constraint stay in agreement.
--
-- Classification: `rewrite` (a table is removed), declared in both the header
-- and the ledger row. Compat floor moves to this migration: an older binary
-- rolled back onto this schema cannot insert an episode (`blob_hash` is gone),
-- and a binary carrying the pre-existing 11-variant `UnitState` could stage a
-- unit this CHECK now rejects.

alter table memphant.subject
  drop column if exists privacy_policy;

alter table memphant.memory_unit
  drop column if exists last_confirmed_at;

alter table memphant.episode
  drop column if exists blob_hash;

drop index if exists memphant.memphant_event_outbox_tenant_scope_idx;
drop index if exists memphant.memphant_event_outbox_tenant_delivery_idx;
drop index if exists memphant.memphant_event_outbox_tenant_trust_event_idx;

drop index if exists memphant.memphant_scope_block_tenant_scope_idx;
drop index if exists memphant.memphant_scope_block_tenant_actor_idx;
drop table if exists memphant.scope_block;

alter table memphant.embedding_profile
  drop constraint if exists embedding_profile_index_strategy_check;
alter table memphant.embedding_profile
  add constraint embedding_profile_index_strategy_check
  check (index_strategy in ('exact'));

alter table memphant.memory_unit
  drop constraint if exists memory_unit_state_check;
alter table memphant.memory_unit
  add constraint memory_unit_state_check
  check (state in (
    'candidate','active','superseded',
    'invalidated','deleted','quarantined','expired','validated'
  ));

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260801_009_drop_dead_schema',
  '20260801_009_drop_dead_schema',
  'rewrite'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
