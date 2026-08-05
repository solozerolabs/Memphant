# Flow: spec 31 evidence-integrity probes

## Spec

Build the deterministic, $0 golden eval suite from `docs/superpowers/specs/memphant/31-evidence-integrity-probes.md`: probes that assert MemPhant's existing trace-integrity machinery (temporal/valid-time filtering, `Contradicts`-edge + `unresolved_contradiction` → abstention, citation attribution, provenance) actually behaves under mutation and conflict. Reader-free, in-memory (`InMemoryStore`), no DB, no lattice, no exposure-guard interaction.

The four metrics, mapped to golden assertions (two ride existing fields, two need a small **harness** extension — never an engine feature):

| metric | assertion | field |
|---|---|---|
| conflict_evidence_recall | both conflicting units packed + forced abstention | `packed_context_contains` + `abstention_signal: true` (existing) |
| as_of_correctness | current-time recall returns the successor only; a valid-time-past query returns the superseded state | `forbidden_units`/`answer_bearing_ids` (existing) + **new `valid_at` on `GoldenCase`** |
| unsupported_answer_rate | the expected answer's key value string is present in the rendered packed evidence (catches a chunk-render drop or a mis-attribution) | **new `packed_body_contains` on `GoldenExpect`** |
| citation_justification | among a correct unit and a near-duplicate wrong-value distractor, the correct unit is the answer-bearer and is cited; the distractor is dropped | `citations_include` + `forbidden_units` + `answer_bearing_ids` (existing) |

**Non-goals** (from spec §5, held): no LLM judge, no new conflict-resolution engine features, no public-benchmark claim, no `REQUIRED_PROFILE_AXES` promotion (runs as a standalone sampled suite until it catches ≥1 real defect).

**Deliberate scoping deviations from the spec, recorded here:**
- **as_of via VALID-time, not transaction-time.** The spec §1 cites the `transaction_to.is_some() → None` transaction path, but the golden seed writer hardcodes `transaction_from/to = None` (lib.rs:2779) and can't write closed transaction intervals. Valid-time as-of (`valid_at` + `valid_from`/`valid_to`, both already seedable) demonstrates as-of correctness through the temporal stage with a one-field runner change. Transaction-time as-of is deferred (needs a seed-side transaction-interval extension) and noted in the suite.
- **Gate is a Rust contract test, not the Python `test_evidence_integrity_gate.py` of spec §3.3.** The golden runner is Rust; the pass/fail of each case's `expect` IS the metric gate. A Python test that subprocesses cargo only to assert Rust-run cases pass is indirection. A Rust contract test mirroring `eval_contract.rs::oracle_suite_runs` is the honest home.
- **Cases stay OUT of `golden.yaml` (pr-golden).** Per spec §3.4 they run as their own sampled suite, so the `total_cases == 11` guards in `eval_contract.rs` are untouched. They are still listed in `manifest.yaml` (the dir guard is total).

Priority when trading off: correctness of the machinery-exercise over case count. A probe that passes vacuously (evidence not actually exercised) is worse than a missing probe — every case must fail if the machinery it targets regresses.

**Non-vacuity requirement (review finding, load-bearing).** The failure mode that bit the earlier `contextual_chunk_multi_window` fixture: a probe that passes because the "wrong" unit was never a retrieval candidate, not because the machinery excluded it. Guard every probe:
- **Stale / as-of:** the old unit must be a genuine candidate that the temporal stage *drops*. Assert `dropped: [{unit: old, reason: stale}]` (proves it was in the pool AND dropped for the specific reason), not bare `forbidden_units` (which passes if it was never retrievable). The old unit must share query terms so it *would* surface but for the filter.
- **Conflict:** without the `contradicts` edge the pack must NOT abstain — the abstention must be attributable to the contradiction, not to some other trigger. Both units share query terms and carry deliberately different `subject_key`s so subject-dedup doesn't collapse them (the `packing_abstention_contradiction` idiom).
- **Adversarial:** the distractor must have high enough lexical overlap to genuinely compete for the answer slot (otherwise it never threatens and the citation assertion is vacuous). Assert both `citations_include: [correct]` and the distractor's exclusion, and confirm via a `top_k_contains`/`dropped` that the distractor was a live candidate.

## Plan

1. **Harness extension A — `valid_at` on `GoldenCase`.** Add `#[serde(default)] valid_at: Option<String>` to `GoldenCase` (lib.rs:~488); thread it into the `RecallRequest.valid_at` build (lib.rs:2065-2066), replacing the hardcoded `None`. Rust unit test: a case with `valid_at` inside a superseded unit's valid interval returns that unit; without it, returns the successor.
2. **Harness extension B — `packed_body_contains` on `GoldenExpect`.** Add `#[serde(default)] packed_body_contains: Vec<String>`; after packing, assert every string is a substring of some `response.items[].body`. Failure lists the missing string. Rust unit test: passes when the value is rendered, fails when it is absent.
3. **Golden cases** (~8, ≥3 entity families × the 3 regimes), `examples/evals/golden/evidence_integrity_*.yaml`, each `second_author_confirmed: true`, each constructed so latest-wins vs surface-both give different outputs (the corpus must not answer for free):
   - `evidence_integrity_stale_office.yaml` — valid-time supersession (office moved); current query → successor, old `forbidden_units`, value grounded.
   - `evidence_integrity_stale_flag_supersede.yaml` — explicit `state: superseded` + `supersedes` edge (feature-flag renamed); superseded unit must not surface.
   - `evidence_integrity_asof_value_correction.yaml` — a value corrected across episodes; `valid_at` past-time query returns the old value, current query returns the corrected one.
   - `evidence_integrity_conflict_owner.yaml` — two active units, different `subject_key`, `contradicts` edge (owner change); both packed + abstain.
   - `evidence_integrity_conflict_threshold.yaml` — second conflict family (config threshold), both packed + abstain.
   - `evidence_integrity_adversarial_flag.yaml` — correct unit + near-duplicate wrong-value distractor, high lexical overlap; correct is answer-bearer + cited, distractor dropped.
   - `evidence_integrity_adversarial_owner.yaml` — second adversarial family.
   - `evidence_integrity_ground_value.yaml` — a plain grounding probe: answer value must appear in the cited unit's rendered body (`packed_body_contains`).
4. **Register**: add all new stems to `examples/evals/manifest.yaml` (total dir guard). Create `benchmarks/evidence-integrity-sampled.yaml` (`id`, `manifest: ../examples/evals/manifest.yaml`, `cases: [...]`) matching the other `benchmarks/*-sampled.yaml` shape.
5. **Gate contract test** `crates/memphant-eval/tests/evidence_integrity_contract.rs`: `run_eval_file(evidence-integrity-sampled.yaml)`, assert `passed_cases == total_cases` and `total_cases == <N>`; assert each of the four metric-bearing case ids is present (guards against the suite being silently emptied); `verify_golden_file` load-bearing check.
6. **Confirm no collateral**: `eval_contract.rs` counts stay 11 (cases not in `golden.yaml`); if a real count moves, bump precisely. Update spec 31 header to `LANDED` with the case list and the two deviations.

## Harness

```sh
cargo build -p memphant-eval
cargo test -p memphant-eval --test evidence_integrity_contract
cargo test -p memphant-eval --test eval_contract
cargo test -p memphant-eval --lib
cargo run -q -p memphant-eval -- run benchmarks/evidence-integrity-sampled.yaml
cargo run -q -p memphant-eval -- verify-golden benchmarks/evidence-integrity-sampled.yaml
cargo fmt --all -- --check
cargo clippy -p memphant-eval --all-targets -- -D warnings
```

## Eng review (applied lens, autonomous — gstack-plan-eng-review)

Ran the eng-review lens directly (the interactive gstack flow assumes a human at the terminal). Scope: 2 struct fields + threading + 1 assertion loop of real logic; the rest is data fixtures — not overbuilt, no new services, no innovation token spent.

- **Extensions minimal?** Yes. `valid_at` is unfakeable for the as-of-past half. `packed_body_contains` catches a real P-2-era failure (unit cited but its value chunk dropped from the merged render) that unit-level `answer_bearing_ids`/`citations_include` cannot see. Neither is an engine feature.
- **Vacuous-pass risk (the one real finding):** folded into the plan's Non-vacuity requirement — use `dropped: [{unit, reason: stale}]` so the excluded unit is proven a live candidate; conflict probes must NOT abstain without the edge; adversarial distractors must genuinely compete. This is the single most important correctness property and the same trap that bit `contextual_chunk_multi_window`.
- **Scoping deviations sound?** Yes. Valid-time as-of exercises the temporal stage honestly and defers only the transaction-interval seed extension. A Rust contract test is the golden runner's native gate; a Python subprocess-cargo wrapper would be pure indirection.
- **DRY / tests:** the contract test asserts the specific metric-bearing case ids are present (anti-silent-empty), mirroring the `multi_window_fixture_guard` lesson.

VERDICT: proceed. One finding (non-vacuity) absorbed into the plan. NO UNRESOLVED DECISIONS.
