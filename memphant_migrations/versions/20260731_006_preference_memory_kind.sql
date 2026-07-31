-- Mint `preference` as a MemoryKind (spec 04 §13.2a, 00 §4, 25 §11c).
--
-- Why: the preference lane's first measurement (2026-08-01) found supersession
-- UNREACHABLE, not broken. A retained episode carries no explicit subject, so
-- `has_explicit_subject` is false and the whole supersedence branch is skipped;
-- past that gate the branch additionally required `kind == semantic`. Nothing
-- ever reached `UnitState::Superseded` — 8147 units, all `active`, all
-- `episodic`, zero edges. A user-declared standing constraint had no kind to be
-- stored as, so it could not participate in close-generation supersession at
-- all. §13.2a's policy row is distinct from `semantic` on promotion, retrieval
-- and decay, and from `belief` on trust and promotion, so it passes the §0.1
-- admission test.
--
-- Classification: BREAKING, deliberately, and the classifier computes it (this
-- file drops a constraint). Widening a TEXT+CHECK enum column is only
-- "additive" under §11c's fallback read rule, and `MemoryKind` is precisely the
-- frozen closed Rust enum that rule exempts — a binary embedding the old
-- five-variant enum fails serde decode on a 'preference' row. So the
-- `schema_compat_revision` floor moves to this migration: an older binary
-- self-gates at boot rather than mis-reading a row. Pre-launch (25 §11c
-- "freeze hard, no window"), so there is no expand-migrate-contract window.
--
-- No data changes: no existing row can hold the new value, so both re-adds are
-- validated against a table where the predicate already holds.

alter table memphant.scope_policy
  drop constraint if exists scope_policy_kind_check;
alter table memphant.scope_policy
  add constraint scope_policy_kind_check
  check (kind in ('episodic','semantic','procedural','belief','resource','preference'));

alter table memphant.memory_unit
  drop constraint if exists memory_unit_kind_check;
alter table memphant.memory_unit
  add constraint memory_unit_kind_check
  check (kind in ('episodic','semantic','procedural','belief','resource','preference'));

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260731_006_preference_memory_kind',
  '20260731_006_preference_memory_kind',
  'breaking'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
