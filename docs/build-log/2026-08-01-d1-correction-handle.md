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

### Postgres leg: 90 passed, one load-bound SLO threshold

`bash scripts/with_scratch_db.sh … cargo test -p memphant-store-postgres
--no-fail-fast -- --ignored --test-threads=1`.

**90 passed, 1 failed.** `--no-fail-fast` matters here: without it the run stops
at the first failing binary and the other legs are never reported at all, which
is how a single threshold breach can masquerade as a broken suite.
`pg_store_contract` reports **53 passed** — the 52 that passed before, plus the
new shared scenario. Nothing regressed.

The one failure is
`hot_path_slo_pg::fast_mode_recall_holds_release_hot_path_slo_on_postgres`, a
wall-clock 200 ms debug-build p50 threshold.

**Paired reading, because an absolute latency on this host is worthless.**
Baseline (`main@0e874da0`) and HEAD alternated back to back on the same box so
the shared load partly cancels. `hw.ncpu = 12`:

| arm | p50 | load (1 min) |
|---|---:|---:|
| baseline-1 | 291.2 ms | 45.3 |
| head-1 | 309.1 ms | 56.5 |
| baseline-2 | 334.3 ms | 63.8 |
| head-2 | 475.6 ms | 81.8 |
| baseline-3 | 601.7 ms | 85.4 |
| head-3 | 662.0 ms | 125.9 |

**The load-bound conclusion, stated with its own limitation.** The baseline
breaches the same 200 ms threshold in all three of its own runs — by 3× in
baseline-3 — so the gate is failing for reasons that exist without this change.
That is the fact the gate needed and it is solid.

What this reading does **not** establish is a null delta. Load climbed
monotonically from 45 to 126 across the sequence and HEAD always ran second in
each pair, so arm order is confounded with the load trend; the 6–42% head-over-
baseline gaps are not separable from it. `baseline-3` (601.7 ms) exceeding
`head-1` (309.1 ms) shows the between-run load term dominates any within-pair
difference. A real latency claim needs a quiet host and interleaved repeats,
which is the standing conclusion §4 of the plan already records for this
machine. **No latency claim is made here.**

Mechanically, the change adds one struct construction per recalled item from
columns already in memory, and clones four `Option<String>`s.

An earlier attempt aborted before any test ran with `database
"memphant_scratch_23140_…" does not exist`. See §7a — that was almost certainly
the unserialized-bootstrap defect, not a product failure.

### 7a. A cross-lane defect found while running this gate

Six lanes share this host. `scripts/with_scratch_db.sh` serializes scratch-DB
bootstrap behind a `mkdir` mutex because `create role` touches cluster-wide
`pg_authid`, and two concurrent bootstraps die inside migration 001 with `tuple
concurrently updated`.

Two defects, both fixed in `d91303c5`:

1. **The lock was held for the whole command,** not just bootstrap — its own
   comment says otherwise. Taken verbatim from `6fdcaf9d` on `main`.
2. **The lock is keyed by `sha256(url_prefix)`, so it did not cover this lane at
   all.** Five lanes spell the host `localhost`; this lane's gate command
   spells it `127.0.0.1`. Same cluster, same `pg_authid`, **two different lock
   files and no mutex between them.** Verified directly: the two spellings
   hashed to `5672171746…` and `165e553dfe…`. That is the exact collision the
   lock exists to prevent, and it is the most plausible cause of the
   unexplained `database … does not exist` abort above.

Collapsed with shell parameter expansion, not `sed`: BSD `sed` has no `\?`, so
the obvious optional-group pattern matches on Linux, silently fails on macOS,
and leaves the bug in place while looking fixed. Over-sharing a lock only
serializes more; under-sharing corrupts a bootstrap.

This lane binds no ports, starts no servers, and contains no `pkill`,
`killall`, or pattern-based `drop database` anywhere in `scripts/` — audited,
not assumed. The only `DROP` is the script's own `$NAME`.

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
