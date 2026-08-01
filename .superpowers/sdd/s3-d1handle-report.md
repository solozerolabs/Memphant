# S3 — D1 correction handle, caller-authored keys, D2 file-plane legibility

Branch `s3-d1handle`, worktree `/Users/sidsharma/Memphant-s3-d1handle`, off
`main@0e874da0`. Six commits — three feature (`41dc9fc4`, `4df4e23f`,
`33541d75`), one refactor (`f1c17f3f`), one shared-script fix (`d91303c5`), and
docs. **None merged, none pushed.** **`paid_model_calls: 0`. No measurement was run and none is
requested.**

---

## Brief

Plan of record `docs/superpowers/plans/2026-07-31-one-plan.md` §3 Phase D:

- **D1** — emit `{unit_id, subject_generation, fact_key, valid_from, valid_to,
  source_span, episode_id}` on every recall, and give `source_span` (shipped
  end to end with real byte offsets, **zero non-test consumers**, §2 item 10)
  its first consumer.
- **Enabler** — make caller-authored subject keys reachable from the served
  path. §1: `subject`/`predicate` exist internally and REST and MCP never set
  them, so every served write lands on `{scope}:auto:{sha256[..16]}` and
  **auto keys never supersede**.
- **D2** — put `valid_from`/`valid_to`/`source_span` in the unit footer and
  state/date/confidence columns in `MEMORY.md`.

Standing constraint carried into the design: **no LLM on the synchronous write
path.** Nothing here adds a model call anywhere.

---

## Design

### One handle, read off the row, never off the query

`CorrectionHandle` lives in `memphant-types` beside the unit it describes, and
`CorrectionHandle::for_unit(&StoredMemoryUnit)` is total — every field is a
persisted column. `RecallContextItem.correction` is populated in
`context_item_for` **before** `rendered_body` moves the body out of the
candidate.

The one real design question was `source_span`. Recall packs items by selecting
a chunk mask, so "the span of what we rendered" was available and tempting. It
was rejected for two reasons. First, selected chunks need not be contiguous, so
a covering `min-max` over a subset would name bytes we did not show — a
provenance value that is *wrong* is worse than one that is absent. Second, and
decisive: the file plane needs the same value, and it has no query. Making the
span a property of the unit gives **one definition with two consumers**
(`covering_source_span`, used by both the recall handle and `projection_items`)
instead of two definitions that drift. A correction points at where the memory
came from, not at this query's budget decision — and that is now a test:
`whole.correction == chunk_rendered.correction`.

A span shape neither chunker mints (`"episode:0-72"`, `"abc"`, reversed bounds)
parses to `None`, never to a wrong range.

`correction` is `None` on exactly one path: `degraded_episode_items`, where the
item is a raw un-reflected episode and `unit_id` is *synthesised from the
episode id*. There is no stored row, no key and no generation to correct there,
so a handle would name something that does not exist.

### Caller keys: an addition with a hard non-regression

`RetainUnitPayload` demanded a **pre-composed** `fact_key`. That is not a usable
key surface: it forces the client to reimplement `derive_fact_key` — scope UUID
prefix, whitespace collapse, lowercasing, space→underscore — which is the
"never reimplement a primitive" rule violated from the outside. Both public
payloads now accept `subject`/`predicate` and the **server** composes the key.

The precedence rule is deliberately conservative: `fact_key`, when present,
**wins**. A caller that composed its own key sees byte-identical behaviour. A
caller that sends neither gets the byte-identical content-hash auto key it got
before — pinned by
`a_caller_supplying_no_subject_still_gets_the_unchanged_auto_key`, which asserts
against `derive_fact_key(scope, None, None, body)` itself.

Blank is not a key. `"   "` for a subject would otherwise mint `{scope}::{pred}`
and collide an entire scope onto one generation; `non_blank` rejects it at
validation.

### D2: show, do not filter

The projection query already excludes superseded and out-of-interval rows, so
the file is correct by construction and a dead rule leaves it the moment
supersession fires. The new columns therefore *explain what survived*; they add
no filtering. `validate_footer` was changed from seven hand-listed field
comparisons to `*footer != expected`, so the next field added cannot be silently
left unvalidated.

`SCHEMA_VERSION` 1 → 2. Both `UnitFooter` and `ManifestEntry` are
`deny_unknown_fields` round-trips, so a v1 tree on disk is unparseable by this
binary; the manifest version check must fail loudly and name the version rather
than dying mid-parse with "invalid footer JSON".

---

## What shipped

| Commit | Change |
|---|---|
| `41dc9fc4` | `CorrectionHandle` + `covering_source_span` in memphant-types; `RecallContextItem.correction` populated in `context_item_for`, `None` on the degraded path; 4 type-level tests + a span-slices-the-episode-body test |
| `4df4e23f` | `subject`/`predicate` on `RetainEpisodePayload`; `subject` on `RetainUnitPayload` and `fact_key` made optional; validator accepts either; `non_blank` helper; shared testkit scenario on **both** stores + 4 core tests |
| `33541d75` | `state`/`source_span` on `CanonicalProjectionUnit`; footer gains state + both intervals + span; `MEMORY.md` becomes a table; `SCHEMA_VERSION` 2; 2 rendering tests |

`caller_subject_key_supersedes_without_client_derivation` is a **shared testkit
scenario**, registered in both `store_contract.rs` and `pg_store_contract.rs`,
so InMemory and Postgres cannot diverge on the new write path. It proves the
full chain: caller sends a plain subject → server keys it → the second write
**supersedes** rather than appends → recall returns the survivor with a handle
whose `fact_key` is that key.

Regenerated from their generators (never hand-edited): `openapi/memphant.v1.json`,
`mcp/memphant.tools.v1.json`, `examples/evals/trace-schema.v1.json`.

No migration. `source_span` already rides in `memory_unit.payload` jsonb; no
column, no `MIGRATIONS`/`MIGRATION_HEAD` change, no `SCHEMA_COMPAT_REVISION`
bump. No new `MemoryKind`, so the RW-1 hazard is untouched — and both
scope-policy lists already derive from `MemoryKind::ALL`, so the hazard the
brief warned about is already closed in this tree.

---

## What I refused to build, and why

**A model call at mutation time.** The 91.7–93.2% figure is real and the seam
(reflect stage 1) is sanctioned, but it is a *measurement* decision with a
price, not a code decision. This lane makes the deterministic caller-key path
reachable; whether a model should sit behind it is the next preregistration, not
this diff.

**A per-query `source_span`.** Rejected above: non-contiguous chunk selections
would name bytes we did not render, and the file plane needs a query-independent
value anyway.

**A handle on degraded items.** Their `unit_id` is synthetic. A handle there is
a correction target that cannot be corrected.

**Claiming the episode-payload subject makes episodes supersede.** It does not,
and the test says so in its name. The Episodic write-router arm maps to
`supersedes_own_kind == None` on purpose — that is the cross-kind bug
`ffa640b8` fixed. What the episode subject buys is a legible, groupable key
(and therefore a meaningful `fact_key` in the handle) instead of a sha256
prefix. The supersedable caller-key path is the unit payload. Overclaiming here
would have been the exact mismatched-stage error the plan warns about.

**Any paid arm.** None warranted from this lane. Nothing here produces a number;
the two things that could be measured next (does a caller-authored key move LSW
on a lane whose corpus carries real subjects; is a mutation-time model hook
worth its $0.17/385) are separate preregistrations with their own controls.

**A latency claim.** The Postgres SLO gate failed, and the paired reading shows
the baseline failing it too — but load climbed monotonically during the run and
HEAD always ran second, so the arms are confounded with the trend. That is
enough to say the gate is not measuring this change; it is not enough to claim a
null delta, and I did not.

---

## Verification

| Gate | Result |
|---|---|
| `cargo build --workspace` | clean |
| `cargo test --workspace` | **0 failed** |
| `cargo clippy --all-targets --all-features` | clean |
| `cargo fmt --check` | clean |
| Postgres `--ignored --test-threads=1 --no-fail-fast` under `with_scratch_db.sh` | **90 passed, 1 failed** — `pg_store_contract` 53/53 (52 before + the new shared scenario). The failure is `hot_path_slo_pg`'s wall-clock 200 ms p50, which **the baseline breaches too** (291/334/602 ms baseline vs 309/476/662 ms HEAD, load 45 → 126 on 12 cores). Pre-existing under this load; no latency claim made — see build log §7. |
| `python3 -m pytest -q tests/` | 736 passed, 12 skipped, **1 pre-existing failure** (`test_spec_drift_check_passes_against_linked_syndai_docs`, sibling-repo state, out of scope — identical before and after) |

Schema artifacts regenerated, never hand-edited; their three staleness tests are
the guard and all pass.

---

## Syndai-side integration (not built here — different repo)

Syndai's provenance chip already ships:
`mobile/lib/features/missions/widgets/memory_references_footer.dart`, chips under
each assistant message, tap → *Still true / Not anymore / Forget*, thirty
locales. Its model `MemoryRef`
(`mobile/lib/features/memory/models/memory_experience_models.dart:68`) reads
`{memory_type, memory_id, confidence, span_quote}` out of
`context_snapshot.memory_references`, populated from `syndai.memory_references`.
`grep -ric memphant mobile/lib` returns nothing.

The handle maps onto that model almost exactly — `memory_id ← unit_id`,
`span_quote ← the source bytes named by source_span`. What the Syndai side needs:

1. Carry `correction` from MemPhant's `/v1/recall` items into the
   `memory_references` entries it already writes (the chip needs no change to
   render; the extra fields ride as new keys).
2. Resolve `span_quote` by slicing the episode body at `source_span` — today it
   is whatever the agent quoted, which is not verified against the source.
3. Route *Not anymore* to a MemPhant write that reuses the handle's `fact_key`
   and `subject_generation`. That path is now reachable without the client
   deriving anything, which was the blocker.
4. `fact_key` containing `:auto:` means the unit cannot be superseded by key —
   the chip should fall back to the unit-id-targeted path rather than issuing a
   keyed write that would silently append.
