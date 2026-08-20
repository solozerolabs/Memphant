-- Exempt CAPTURED units from `memphant_memory_unit_subject_valid_excl`: a
-- captured unit's per-key uniqueness is CHANNEL-scoped, not key-scoped, so two
-- different capture channels on one subject key must coexist.
-- migration_kind: breaking
--
-- Why (measured 2026-08-19, `crates/memphant-store-testkit/src/lib.rs`
-- `same_channel_capture_reinforces_supersedes_and_serves_on_the_coding_lane`,
-- the ignored live-PG contract suite): commit `f9e45639`
-- ("fix(capture): mint captures as Semantic-at-Candidate, not Belief") moved a
-- capture's trust out of the KIND (was `Belief`, which this constraint has
-- never covered — see migration 007) and into the unit STATE (`Candidate`) plus
-- the `payload.capture` ladder. But `Mirror`/`Summary` captures now mint
-- `Semantic`, which IS covered, and this constraint keys on
-- `(tenant, subject, scope, agent, generation, fact_key, kind, valid-range)` —
-- the capture CHANNEL is not one of its dimensions. So a `Mirror` capture and a
-- `Summary` capture on one mined key (same fact_key, both open `Semantic`,
-- overlapping valid interval) collided on this exclusion at worker drain, even
-- though they are DIFFERENT witness sources that must coexist. `f9e45639` ran
-- only the in-memory `capture_write_seam` (`InMemoryStore` has no exclusion
-- constraint), so the PG-only regression shipped green and was inherited red.
--
-- This is not a weakened invariant; it is the belief carve-out restored at its
-- new locus. Migration 007 exempted `belief` because unverified claims coexist
-- until promoted (`supersedes_own_kind(Belief) = None`). A captured `Candidate`
-- is exactly that same provisional, pre-witness state — the anti-poisoning
-- cross-check (spec: capture trust ladder) REQUIRES multiple captured units on
-- one key to be representable so it can detect corroboration/collision across
-- sources. `f9e45639` relabelled that provisional-ness from `kind=Belief` to
-- `payload.capture`, but this constraint kept keying on `kind`, so it wrongly
-- pulled captured `Semantic` units in. The fix keys the exemption on the same
-- signal the write path now uses: `payload ? 'capture'` (present iff the unit
-- was produced by a capture channel; a non-captured write never carries it,
-- mirroring the `payload ? 'compact'` idiom of migration 011's sibling
-- constraint). Same-CHANNEL uniqueness is unaffected: the compiler still closes
-- the prior same-source generation (transaction-time) before inserting the next
-- — proved by the SUPERSEDE arm of the same contract test — so at most one open
-- unit per (key, source) survives without this constraint policing it.
--
-- The `kind` predicate is UNCHANGED (`semantic`, `preference`), so migration
-- 007's pinned rule still holds: those are exactly the kinds where
-- `supersedes_own_kind(kind) == Some(kind)`, and
-- `exclusion_predicate_matches_the_supersedes_own_kind_set` derives that set
-- from the live constraint rather than restating it. Non-captured `Semantic`
-- and `Preference` facts are policed exactly as before
-- (`user_trust_episodes_sharing_a_mined_key_supersede` is untouched).
--
-- Classification: `breaking`, computed by the classifier (this file drops a
-- constraint). The compat floor does NOT move: narrowing a constraint's
-- predicate cannot make an existing row unreadable by an older binary, and no
-- column, type or enum changes — identical reasoning to migration 007. Rolling
-- the SCHEMA back is what would be unsafe, and the floor already records that.
--
-- No data changes: the predicate only loses rows carrying `payload.capture`.
-- The re-add validates against the live table because any pre-existing pair of
-- open captured units on one key is precisely the row set this exemption now
-- permits; no NON-captured pair can newly violate.

alter table memphant.memory_unit
  drop constraint if exists memphant_memory_unit_subject_valid_excl;
alter table memphant.memory_unit add constraint memphant_memory_unit_subject_valid_excl
  exclude using gist (
    tenant_id with =,
    data_subject_id with =,
    scope_id with =,
    agent_node_id with =,
    subject_generation with =,
    fact_key with =,
    kind with =,
    tstzrange(valid_from, valid_to, '[)') with &&
  ) where (
    transaction_to is null
    and kind in ('semantic', 'preference')
    and not (payload ? 'capture')
  );

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260819_014_capture_units_exempt_from_subject_exclusion',
  -- Narrowing a predicate keeps every existing row readable by an older binary,
  -- so the compatibility floor stays where migration 013 left it.
  '20260817_013_drop_dead_fsrs_columns',
  'breaking'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
