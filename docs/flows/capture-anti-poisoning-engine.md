# Capture anti-poisoning engine (Rust core)

Flow lane: `flow`. Feature: capture anti-poisoning engine. Base commit `665f57d0`
(fast-forwarded to `main`, includes `a61ba7d4`). Plan of record:
`docs/superpowers/plans/2026-08-15-cross-harness-capture-plan.md`.

Write-side counterpart to the shipped cross-harness INJECTION adapters. This stage
adds ONLY the Rust trust-ladder engine that processes already-CAPTURED memory units:
the cross-source cross-check + the weak-self-outcome witness. The per-harness capture
ADAPTERS (transcript parsing, file-mirror, session summarize) are a separate stage and
are explicitly out of scope here.

## Spec

### What already exists (reuse, do not rebuild)
- Low-trust writes are forced to inert `state=candidate` by `low_trust_projection_state`
  (`crates/memphant-core/src/lib.rs`); `AgentOutput` is the floor (`actor_kind_trust`,
  `crates/memphant-types/src/lib.rs`). Captured agent output is already recall-inert.
- `high_risk_memory_below_trust_floor` drops below-floor/Belief units from high-risk recall.
- Recall already excludes `Quarantined` (`RecallDropReason::Trust`,
  `recall drop-reason path`) and `Candidate` (the `unit_is_recallable_at`-class gate only
  admits `Active`/`Validated`/open-`Superseded`).
- `memory_unit.state` permits `candidate`/`quarantined`/`active`/`validated`/`superseded`;
  `confidence`/`reinforcement_count`/`last_reinforced_at` exist; `review_event` (outcome ∈
  `success`/`failure`/`corrected`/`ignored`, `MarkOutcome`) exists and is written by the
  `mark`/`record_mark` verb. The WRITE SEAM is `fetch_scope_open_units`.
- Typed markers ride `payload` jsonb via a struct field pattern: `StoredMemoryUnit.compact`
  (`payload.compact`) and `.invalidation` (`payload.invalidation`), serialized in the pg
  store and carried in-struct by InMemory. This is the exact template for capture tagging.
- The subject-uniqueness exclusion constraint `memphant_memory_unit_subject_valid_excl`
  covers ONLY `kind in ('semantic','preference')` and ignores `state`. Two same-key
  `belief` units may coexist; two same-key `semantic` candidates may NOT.

### The delta this flow builds

1. **Capture-source tagging (zero schema).** A typed `CaptureMarker` on `payload.capture`,
   mirroring `compact`/`invalidation`:
   - `CaptureSource { Mirror, Summary }` — the explicit-file-write mirror vs the LLM session
     summary. This is the provenance FAMILY that the independence rule keys on.
   - `CaptureLadder { Captured, Corroborated, Durable }` — the trust-ladder rung.
   - `CaptureWitness { SourceAgreement, WeakOutcome, Survival }` — witness FAMILIES.
   - `CaptureMarker { source, ladder, witnesses: Vec<CaptureWitness> (deduped set) }`.
   - New serde-default `capture: Option<CaptureMarker>` on `StoredMemoryUnit` and
     `NewMemoryUnit`. Tiny typed helpers (`CaptureMarker::captured`, `.record_witness`,
     `.rung_for_witnesses`).

   **Design choice — captured units are `Belief`-kind candidates.** `remember` force-mints
   `Active`; captured provisional facts must be inert and must be able to COEXIST on one
   subject key so a collision is representable. `Belief` satisfies both: it is below the
   high-risk trust floor already, is not under the `semantic/preference` exclusion
   constraint (so two same-subject captured units coexist in BOTH stores), and its
   candidate state is recall-inert. The ladder rung lives in `payload.capture`, NOT in the
   `TrustLevel` enum: `TrustLevel` is closed and encodes SOURCE provenance (the separate
   trust-floor layer); overloading it with ladder rungs would merge two anti-poisoning
   layers the plan keeps distinct. `trust_level` therefore stays `AgentOutput` for captured
   units; recall-eligibility is driven by `state` (candidate→active on promotion,
   quarantined on collision/negative outcome).

2. **Cross-check promotion in the reflect job**, reading the WRITE SEAM
   `fetch_scope_open_units` (never a bounded recall pool — store-divergence rule). Pure core
   fn `compute_capture_crosscheck(units, review_events, now) -> Vec<CaptureTransition>`:
   - Operates ONLY on units carrying a `capture` marker that are open and live (candidate or
     active). Non-captured units (a plain USER unit) are never touched — the false-positive
     guard is structural.
   - Group captured units by subject key `(fact_key, kind)`.
   - **Source agreement:** two captured units in a group whose bodies match (dedup identity =
     normalized-body equality; embedding-sim ≥ τ is a documented deferral, unreachable under
     the NoopEmbedding test provider and less precise than exact identity) AND come from ≥2
     DIFFERENT `CaptureSource` families → each records the `SourceAgreement` witness.
   - **Cross-source subject collision:** a group with captured units from ≥2 different
     `CaptureSource` families whose bodies DIVERGE → set ALL captured units in the group to
     `state=Quarantined` (recall-excluded), pending tiebreak. Overrides promotion.
   - **Ladder / independence rule:** rung is derived from the witness SET. 0 families →
     `Captured` (candidate, inert). ≥1 distinct family → `Corroborated` (active, recallable).
     ≥2 DISTINCT families → `Durable` (active). `SourceAgreement` is one family however many
     mirror+summary pairs agree, so an agent's mirror-write + its own summary-of-that-write
     count once and cannot self-promote poison past `Corroborated`; reaching `Durable`
     requires a second, different family (weak-outcome or survival).
   - Emits a `CaptureTransition { id, state, confidence, capture }` only for units whose
     state/confidence/marker actually changed.

3. **Weak-self-outcome witness (Phase 1).** From `review_event`s whose `used_ids` include a
   captured unit:
   - `Corrected` → `state=Quarantined` (recall-excluded), confidence cut to 0.
   - `Failure` → confidence cut (demote), unit stays candidate/its state; no promotion.
   - `Success` → records the `WeakOutcome` witness (positive, toward corroborated/durable).
   - `Ignored` → no-op.
   Confidence keys off this CONFIRMATION signal only, never retrieval frequency.

4. **Recall excludes `quarantined`** everywhere — verified existing behavior; a regression
   test pins it. No recall code change expected.

5. **New store seam `apply_capture_transitions`** (both stores) applies the pure engine's
   transitions to `state` + `confidence` + `payload.capture`, context-scoped exactly like
   the compiled `unit_updates` path. Wired into the reflect job via a new service method
   `run_capture_crosscheck(context)` called at the end of `compile_job`, gated by a
   `capture_crosscheck_enabled` service flag (default ON; the flag OFF is the non-vacuity
   control arm).

### Non-goals
No new migration/column, no new MCP/service verb (rides `remember`/`retain`/`reflect` +
`mark`), no capture adapters/transcript parsing, no hard deletes, no blocking of agent
writes, no embedding-sim paid path.

## Plan

1. **Types** (`crates/memphant-types/src/lib.rs`): `CaptureSource`, `CaptureLadder`,
   `CaptureWitness`, `CaptureMarker` (+ JsonSchema derives + helpers); add serde-default
   `capture: Option<CaptureMarker>` to `StoredMemoryUnit` and `NewMemoryUnit`.
2. **Engine + store seam** (`crates/memphant-core/src/lib.rs`): `CaptureTransition`,
   `CaptureCrossCheckReport`, pure `compute_capture_crosscheck`, and
   `MemoryStore::apply_capture_transitions`; InMemory impl. Thread `capture` through
   `stage_memory_unit` (NewMemoryUnit→StoredMemoryUnit) in InMemory.
3. **Postgres store** (`crates/memphant-store-postgres/src/store.rs`): serialize/read
   `payload.capture` (mirror compact/invalidation); implement `apply_capture_transitions`
   (UPDATE state/confidence/payload); carry `capture` on the unit loader.
4. **Service wiring** (`crates/memphant-core/src/service.rs`): `capture_crosscheck_enabled`
   flag + `with_capture_crosscheck_enabled` builder (default true); `run_capture_crosscheck`
   method; call it at the end of `compile_job`.
5. **Tests** (`crates/memphant-core/tests/capture_crosscheck.rs`): BDD, every positive paired
   with a removal-perturbation control (see Harness list below).
6. **Store contract** (`crates/memphant-store-testkit/src/lib.rs` +
   `crates/memphant-core/tests/store_contract.rs` + `pg_store_contract.rs`): a shared
   scope-read/promotion scenario so both stores exercise the write-seam→transition path,
   plus the same-subject captured-belief COEXISTENCE that makes collision representable.
7. **Eval** (`examples/evals/security-smoke.yaml` + `crates/memphant-eval/src/lib.rs`): a
   `poisoning` cross-check quarantine lane and a paired `no-crosscheck` control lane, with a
   `run_security_file` guard that fails (`missing_no_crosscheck_control`) if the control is
   absent — the rung6 substring-guard idiom ported to the security path.
8. Regenerate `openapi/memphant.v1.json` + `mcp/memphant.tools.v1.json` if the added
   serde-default fields change them, via the server/mcp binaries (never hand-edited).

### capture_crosscheck.rs test list (each with its non-vacuity control)
- `cross_source_collision_quarantines_and_excludes_poison` — divergent bodies, two sources →
  both quarantined + recall-excluded. CONTROL `..._survives_without_crosscheck`: same seed,
  `with_capture_crosscheck_enabled(false)` → active poison stays active AND is recalled.
- `two_source_agreement_promotes_to_corroborated_and_recallable` — matching bodies, two
  sources → active/`Corroborated`, recalled. FALSE-POSITIVE GUARD in-test: a single
  high-trust USER unit with no capture marker is kept active/recallable (no blanket delete);
  CONTROL: diverge the two bodies → collision, not promoted.
- `weak_outcome_corrected_quarantines_served_captured_unit` — `corrected` review on a served
  captured active unit → quarantined. CONTROL `weak_outcome_success_does_not_demote`:
  `success` → not demoted (stays active, gains WeakOutcome witness).
- `independence_same_family_double_witness_stays_corroborated` — mirror+summary agreement (one
  `SourceAgreement` family) → `Corroborated`, NOT `Durable`. CONTROL
  `independence_two_families_reach_durable`: SourceAgreement + WeakOutcome(success) → `Durable`.
- `recaptured_forgotten_identity_does_not_resurrect` — an open invalidation tombstone blocks a
  re-captured identity (no-resurrection regression). CONTROL: a distinct identity is not blocked.
- `capture_respects_trust_floor_and_preference_source_gate` — captured belief never passes the
  high-risk trust floor; an agent-sourced preference is refused (source-kind gate regression).
- `quarantined_capture_unit_is_never_recalled` — recall-exclusion regression for quarantined.
Store-contract (both InMemory + Postgres):
`capture_crosscheck_promotes_and_quarantines_across_the_write_seam` and
`same_subject_captured_beliefs_coexist_for_collision`.

## Harness

```sh
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a5b348293273eb41d && cargo fmt --check
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a5b348293273eb41d && cargo clippy --all-targets --all-features -- -D warnings
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a5b348293273eb41d && cargo test -p memphant-types -p memphant-core --all-features
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a5b348293273eb41d && cargo test --workspace --all-targets --all-features
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a5b348293273eb41d && cargo test -p memphant-core --test capture_crosscheck --all-features
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a5b348293273eb41d && cargo run -p memphant-eval -- security examples/evals/security-smoke.yaml
```
