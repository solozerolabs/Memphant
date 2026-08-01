# 2026-08-01 — D1: the correction handle, and a caller that can name its own key

Branch `s3-d1handle` off `main@0e874da0`. Commits `41dc9fc4`, `4df4e23f`,
`33541d75`. **Not merged, not pushed. `paid_model_calls: 0`. No measurement was
run; no paid arm is requested.**

Plan of record: `docs/superpowers/plans/2026-07-31-one-plan.md` §3 Phase D.

---

## 1. What was actually missing

Phase D reads as a UX task, and it is not. The mechanisms it needs were all
already compiled in.

`RetainUnitPayload.fact_key` has been **mandatory** since it was written. Arm P
minted 7,198 supersession edges through the caller-key path. `derive_fact_key`,
`has_explicit_subject` and the supersede branch all work — Arm K
(`+0.058325` LSW, McNemar `1.65e-08`, 4,162 edges from a key that never sees a
gold field) is the proof.

The gap is narrower and more embarrassing than "we have not built keying":

- `RetainUnitPayload` demanded a **pre-composed** `fact_key`, i.e. a client-side
  reimplementation of `derive_fact_key` including the scope UUID prefix,
  whitespace collapse, lowercasing and space→underscore. Nothing on the served
  path did that, so nothing supplied a key, so every unit got
  `{scope}:auto:{sha256[..16]}`, and **auto keys never supersede**.
- `RetainEpisodePayload` had no subject field at all — while `ReflectJob` has
  carried `subject`/`predicate` the whole time, and the episodic candidate has
  read them the whole time (`service.rs:5384`). Two hardcoded `None`s at the
  enqueue site were the entire break.
- `source_span` shipped end to end with real byte offsets and **zero non-test
  readers**. `context_item_for` dropped the chunks entirely.

So this lane is plumbing, not invention. That is a good sign about the
architecture and a bad sign about how the surfaces were reviewed.

## 2. The one real design decision: whose property is `source_span`?

Recall computes a chunk mask before packing, so "the span of the text we
actually rendered" was available. It was rejected.

**Correctness.** Selected chunks need not be contiguous. A covering `min-max`
over a non-contiguous subset names bytes that were *not* shown. A provenance
value that is wrong is strictly worse than one that is absent — the whole point
of shipping the span is that a correction surface can quote the source
verbatim.

**Non-duplication.** D2 needs the same value in the unit footer, and the file
plane has no query. Defining the span per-query would have forced a second,
different definition for the file — two provenance definitions that drift, which
is precisely what the "a lane may add fields to provenance; it may never
reimplement a primitive" rule exists to prevent.

So `covering_source_span(&[ContextualChunk]) -> Option<String>` lives once, in
`memphant-types`, and has exactly two callers: `CorrectionHandle::for_unit` and
`projection_items`. The handle is a property of the *row*. Asserted directly:
the same unit recalled twice, once chunk-rendered and once not, yields
byte-identical handles.

A span shape neither chunker mints — `"episode:0-72"`, `"abc"`, reversed bounds
— yields `None`, never a wrong range.

## 3. Where the handle is deliberately absent

`degraded_episode_items` synthesises `unit_id` from the episode id because
consolidation has not run. There is no stored row, no fact key, no generation.
A handle there would name a correction target that cannot be corrected, so it is
`None` and the field carries a doc comment saying why.

## 4. Non-regression, stated as a test rather than an intention

The brief's hard constraint was that `derive_fact_key`'s fallback stays and that
a caller supplying nothing sees no change. Three tests, not one comment:

- `a_caller_supplying_no_subject_still_gets_the_unchanged_auto_key` asserts
  equality against `derive_fact_key(scope, None, None, body)` itself, so the
  test cannot pass by reimplementing the expected value.
- `an_explicit_fact_key_beats_a_subject_rather_than_being_recomposed` pins the
  precedence rule: a pre-D1 caller is unaffected even if it now also sends a
  subject.
- `blank_subjects_and_fact_keys_are_rejected_not_silently_keyed` — `"   "`
  would otherwise mint `{scope}::{predicate}` and collide a whole scope onto one
  generation.

And the positive case is a **shared testkit scenario**,
`caller_subject_key_supersedes_without_client_derivation`, registered in both
`store_contract.rs` and `pg_store_contract.rs`. It runs byte-identically against
InMemory and Postgres, so the new write path cannot diverge between stores — the
failure mode this repo has been burned by twice.

## 5. What is NOT claimed

**The episode-payload subject keys the compiled unit; it does not make episodes
supersede.** `supersedes_own_kind` maps the Episodic arm to `None` deliberately
— an episode is an event, not an assertion, and letting one close a generation
is exactly the cross-kind bug `ffa640b8` fixed (bisect-confirmed: the regression
test fails at `ffa640b8^`). The test that covers it says so in its own name.

What the episode subject buys is a legible, groupable key, and therefore a
`fact_key` in the correction handle that a human can read instead of a sha256
prefix. The supersedable caller-key path is the **unit** payload.

Collapsing those two into one headline would be the mismatched-stage error the
plan already warns about twice.

## 6. D2, and why `state` is shown despite being low-cardinality

The projection query admits only `active` and `validated`, so a state column is
near-constant. It is shipped anyway, because the distinction between a rule that
is merely current and one that has been *validated* is exactly what a reviewer
wants, and because the alternative reading — that the column is a filter — is
wrong and worth foreclosing in a comment. **Nothing in `MEMORY.md` filters.**
The projection excludes superseded and out-of-interval rows by construction, so
a dead rule leaves the file the moment supersession fires. The columns explain
what survived.

`validate_footer` was rewritten from seven hand-listed field comparisons to a
whole-struct `!=`. The old shape is how a field gets added and silently left
unvalidated.

`SCHEMA_VERSION` 1 → 2: both the footer and the manifest entry are
`deny_unknown_fields` round-trips, so a v1 tree on disk cannot be parsed by this
binary. The version check must fail loudly and name the version rather than
dying mid-parse with "invalid footer JSON".

## 7. Verification

| Gate | Result |
|---|---|
| `cargo build --workspace` | clean |
| `cargo test --workspace` | **0 failed** |
| `cargo clippy --all-targets --all-features` | clean |
| `cargo fmt --check` | clean |
| `python3 -m pytest -q tests/` | 736 passed, 12 skipped, 1 **pre-existing** failure (`test_spec_drift_check_passes_against_linked_syndai_docs` — sibling-repo state, identical before and after) |

Schema artifacts (`openapi/memphant.v1.json`, `mcp/memphant.tools.v1.json`,
`examples/evals/trace-schema.v1.json`) regenerated from their generators, never
hand-edited. Their three staleness tests pass. No migration: `source_span`
already rides in `memory_unit.payload` jsonb, so `MIGRATIONS`, `MIGRATION_HEAD`
and `SCHEMA_COMPAT_REVISION` are untouched.

### Postgres leg, and a machine-load caveat that matters

`bash scripts/with_scratch_db.sh … cargo test -p memphant-store-postgres --
--ignored --test-threads=1`.

**Record the load with the number, per the standing rule.** `hw.ncpu = 12`,
`load average 30.9–39.5` throughout — this host was running several sibling
worktrees concurrently, ~3× oversubscribed. On the first attempt
`hot_path_slo_pg::fast_mode_recall_holds_release_hot_path_slo_on_postgres`
breached its 200 ms debug-build p50 under that load. It is a wall-clock
threshold test on a 3×-oversubscribed 12-core box; the change it is measuring
adds one struct construction per recalled item, reading columns already in
memory. Treat any latency figure taken on this host today as uninterpretable —
that is the same standing conclusion §4 of the plan already records for this
machine.

An earlier attempt aborted before any test ran with `database
"memphant_scratch_23140_…" does not exist` — a scratch-DB provisioning failure
under load, not a product failure; it reproduced neither on rerun.

## 8. Syndai integration — what the other repo needs

The consuming UI already ships:
`mobile/lib/features/missions/widgets/memory_references_footer.dart` — chips
under each assistant message, tap → *Still true / Not anymore / Forget*, thirty
locales, live in mission chat. Its model reads
`{memory_type, memory_id, confidence, span_quote}` from
`context_snapshot.memory_references`, written from `syndai.memory_references`.
`grep -ric memphant mobile/lib` returns nothing.

The handle maps onto that model nearly one-to-one. Four steps:

1. Carry `item.correction` from `/v1/recall` into the `memory_references`
   entries Syndai already writes. The chip renders unchanged; the handle rides
   as additional keys.
2. Resolve `span_quote` by slicing the episode body at `source_span` instead of
   trusting whatever the agent quoted. Today nothing verifies that quote against
   its source; the span makes it checkable.
3. Route *Not anymore* to a MemPhant write reusing the handle's `fact_key` and
   `subject_generation`. Reachable without any client-side key derivation — that
   was the blocker this lane removed.
4. A `fact_key` containing `:auto:` means the unit cannot be superseded **by
   key**. The chip must fall back to the unit-id-targeted path
   (`target_unit_ids`) rather than issue a keyed write that would silently
   append. This is the one place where a naive integration would look like it
   worked and quietly do nothing.
