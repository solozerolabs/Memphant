-- Narrow `memphant_memory_unit_subject_valid_excl` to the kind the write
-- router actually keeps unique, and add the generation it was missing.
--
-- Why (measured 2026-07-31, `crates/memphant-store-postgres/tests/
-- fact_extraction_subject_key_pg.rs`): with `MEMPHANT_FACT_EXTRACTION=1` every
-- full-corpus code-lane ingest died during worker drain on this constraint.
-- The code lane binds `actor_kind="agent"`, so `actor_kind_trust` assigns
-- `AgentOutput`, `high_trust` is false, and the compiler never reaches the
-- supersedence branch at all. It takes the low-trust projection branch
-- (`memphant-core/src/lib.rs:11826`), which mints `belief`/`candidate` carrying
-- the mined `{scope}:{family}:{phrase}` key with an unbounded valid interval
-- and NO supersession — deliberately, because `supersedes_own_kind(Belief)` is
-- `None` (spec 04 §13.1: unverified claims coexist until promoted). Two
-- episodes mining one key with different bodies therefore produced two open
-- beliefs, which is correct, and the constraint rejected the second.
--
-- So this is not a weakened invariant: the schema asserted at-most-one open
-- belief per subject key while the router promised as-many-as-you-like, and the
-- schema had no way to make its version true. It surfaced as a crash on ingest
-- rather than as an enforced property. Two independent producers hit it —
-- the low-trust projection above and `compose_inferred_beliefs`, which dedups
-- composed units by BODY but keys them by OBJECT, so a third observation that
-- displaces a member of the composed pair rewrites the body under the same key.
-- Both are reproduced in the test file named above.
--
-- THE RULE, so the next person adding a kind knows which way the implication
-- runs: the `kind` predicate of this constraint must be EXACTLY the set of
-- kinds for which `supersedes_own_kind(kind) == Some(kind)` — the kinds whose
-- write-router arm owns close-generation supersession and therefore actually
-- maintains at-most-one-open-per-subject-key. Today that set is
-- `('semantic', 'preference')`. It is pinned by
-- `exclusion_predicate_matches_the_supersedes_own_kind_set` in
-- `crates/memphant-store-postgres/tests/fact_extraction_subject_key_pg.rs`,
-- which derives the Rust side rather than restating it, so adding a kind
-- fails loudly instead of landing where nothing checks it.
--
-- `semantic` KEEPS the constraint: the knowledge arm owns supersession, tiles
-- the valid axis through `correction_rectangles`, and is proven to do so by
-- `user_trust_episodes_sharing_a_mined_key_supersede`.
--
-- `preference` JOINS it, and that is the same defect mirrored. Belief was an
-- invariant the schema asserted and the router never kept; preference is one
-- the router keeps (`supersedes_own_kind(Preference) = Some(Preference)`, minted
-- by 20260731_006) and the schema never checked. Both are the schema and the
-- router disagreeing; both get the same answer.
--
-- Also adds `subject_generation` to the key. This was the sole uniqueness
-- constraint in the bootstrap that omitted it (cf. the eight siblings at lines
-- 288/290/323/372/496/554/609/695) while `fetch_scope_open_units_tx` filters on
-- it — a real scan/constraint divergence, but a latent one: the only generation
-- bump is `erase_subject`, which purges the subject's rows in the same
-- transaction, so no cross-generation open rows survive today. Closed here
-- because it is free to close alongside, not because it is urgent.
--
-- Classification: BREAKING, computed by the classifier (this file drops a
-- constraint). The compat floor does NOT move: narrowing a constraint cannot
-- make an existing row unreadable by an older binary, and no column, type or
-- enum changes. Rolling the SCHEMA back is what is unsafe, and that is what the
-- floor already records.
--
-- No data changes: the predicate loses `belief`, gains `preference` (a kind
-- minted one migration ago, so no pre-existing row can violate it), and gains
-- `subject_generation`, a dimension in which no live duplicate can exist. The
-- re-add therefore validates against a table where the predicate already holds.

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
  ) where (transaction_to is null and kind in ('semantic', 'preference'));

insert into memphant.schema_migrations (version, schema_compat_revision, migration_kind)
values (
  '20260731_007_semantic_only_subject_exclusion',
  '20260731_006_preference_memory_kind',
  'breaking'
)
on conflict (version) do update
set schema_compat_revision = excluded.schema_compat_revision,
    migration_kind = excluded.migration_kind;
