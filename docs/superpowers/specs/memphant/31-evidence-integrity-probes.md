# MemPhant - Evidence-Integrity Probes (Unsupported-Answer Rate & Conflict Evidence Recall)

> Status: LANDED (2026-08-10). Suite `benchmarks/evidence-integrity-sampled.yaml` (10 cases), gate `crates/memphant-eval/tests/evidence_integrity_contract.rs`. Three harness-only extensions (`valid_at` on `GoldenCase`, `packed_body_contains` and `suppressed_read_no_refresh` on `GoldenExpect`) — no engine features, as promised. Motivated by Zero-Mem (arXiv 2607.29377): the defensible property of a trace-preserving memory is auditability under mutation/conflict — MemPhant had the *architecture* but zero *measurement*; this adds the measurement.
>
> **As-built vs spec — deviations (all recorded in `docs/flows/spec31-evidence-integrity.md`):**
> - **as_of via valid-time, not transaction-time.** The seed writer can't set closed transaction intervals; valid-time as-of (`valid_at` + `valid_from`/`valid_to`) exercises the temporal stage honestly. The `asof_concurrency` case is self-guarding: without `valid_at` threaded it returns the successor and fails. Transaction-time as-of deferred.
> - **Gate is a Rust contract test, not the Python `test_evidence_integrity_gate.py` of §3.3.** The golden runner is Rust; each case's `expect` IS the metric gate.
> - **Non-vacuity, verified by perturbation** (the load-bearing correctness property): stale/as-of rely on a same-subject sibling being retrieved + the temporal stage running; conflict cases stop abstaining when the `contradicts` edge is removed; adversarial cases stop citing the correct unit when the query is swapped to favor the distractor. Grounding uses `packed_body_contains` (rendered-text substring), which catches a value dropped from the render even when its unit is packed.
> - **Not promoted to `REQUIRED_PROFILE_AXES`** (§3.4) — runs as a standalone sampled suite, out of pr-golden, until it catches ≥1 real defect.

## 0. Rule

We already ship: no-LLM write path with verbatim extraction bodies (service.rs:6180), bitemporal supersession with remainder units + `Supersedes`/`Contradicts` edge pairs (lib.rs:12490), `unresolved_contradiction` suppression → forced abstention (lib.rs:10139, :9031), dedup exemption so both sides of a live contradiction render (:9938), and `CorrectionHandle.source_span` byte-provenance back to raw text. None of this is gated by an eval. This spec adds the eval; it adds **no engine features**. Any probe failure is a bug report against existing machinery, not a feature request.

## 1. Metrics

| metric | definition | grounded in |
|---|---|---|
| unsupported_answer_rate | fraction of answered probes where the answer's key value is not derivable from the *cited* trace bodies (leak-guard-style string/number containment against `citation_episode_id`/`source_span` content, not against the whole corpus) | reproducible, deterministic |
| conflict_evidence_recall | for probes with a live contradiction: both conflicting states present in packed output AND `unresolved_contradiction` label set AND abstention=true | reproducible |
| as_of_correctness | for superseded facts: current-time recall returns successor only; as-of-transaction-time recall returns the superseded unit (the `Superseded if transaction_to.is_some() → None` path, lib.rs:8388) | reproducible |
| citation_justification | the probe's expected answer maps to the specific unit whose `CorrectionHandle` is cited — not merely *a* relevant unit | reproducible |
| suppressed_read_no_refresh | retrieving a superseded or `unresolved_contradiction`-suppressed unit alters **no** ranking-relevant counter — access count, recency, and any future `mark`/usefulness signal (D-2026-08-09a). The suppression filter must run **before** any access/recency/outcome tracking. | reproducible |

All five are deterministic golden assertions — $0, no reader lattice, no exposure-guard interaction.

## 2. Probe design constraints

1. **The corpus must not answer the question for free.** Per the MemoryCode lesson, recency-compressible probes measure a policy, not the machinery. Every mutation probe must be constructed so the two policies ("latest wins" vs "surface both") give *different* observable outputs, and the golden asserts the conflict-surfacing output, not the lucky-recency one.
2. **Mutations are in-store, not in-repo.** Repo-recoverable facts are grep's turf; probes mutate entity attributes across episodes (office moved, flag renamed, owner changed, value corrected then re-corrected) so the ground truth exists only in the memory store's history.
3. **Three trace regimes per entity family:** stale (clean supersession — old unit must NOT leak), conflicting (two Active units, no supersession match — both must surface + abstain), adversarial (near-duplicate distractor with wrong value and high lexical overlap — citation_justification is the gate that catches packing the distractor).
4. Reuse existing golden idioms: `temporal_validity_current_office.yaml` is the stale-regime template; extend rather than invent a new YAML dialect.
5. **Suppressed-read-no-refresh probe (adopted D-2026-08-09b, from `marcusquinn/aidevops`'s filter-before-track ordering; concept reimplemented, no code copied).** One stale/superseded unit and one `unresolved_contradiction`-suppressed unit each get a probe that (a) records the unit's ranking-relevant counters, (b) issues a recall that would surface the unit were it not suppressed, (c) re-reads the counters and asserts **no change**. Verified by perturbation: remove the supersession/`contradicts` edge and the counter *must* move (proving the probe isn't vacuously passing on a unit that was never a candidate — the `memphant-golden-nonvacuity` rule). The counter set is "access + recency today, plus the `mark`/usefulness signal iff D-2026-08-09a activates"; the probe asserts the ordering guarantee, not new engine behaviour — any failure is a bug against existing suppression machinery (§0).

## 3. Wiring (follows the established add-a-probe-set path)

1. Golden cases: `examples/evals/golden/evidence_integrity_{stale,conflict,adversarial}_*.yaml` (~9–12 cases, 3 regimes × 3–4 entity families) + entries in `examples/evals/manifest.yaml` (manifest guard is total).
2. Suite: `benchmarks/evidence-integrity-sampled.yaml` listing the cases.
3. Gate contract test: `tests/test_evidence_integrity_gate.py` following `test_packing_sufficiency_screen.py` pattern — asserts the four metrics at 100% (deterministic goldens have no CI band; any miss is a defect).
4. **Axis promotion deferred.** Do NOT append to `REQUIRED_PROFILE_AXES` yet — that edit forces a block into every `*-profile.yaml` in the tree. Promote to a required `evidence_integrity` axis only after the probe set has survived ≥1 real defect-catch or a rung promotion wants to cite it. Until then it runs as a sampled suite in CI's ephemeral-DB lane.

## 4. Standing hazards

- Never touch `longmemeval_s.split.json` frozen cohort or `reader_lattices.v1.json` — this suite is deliberately reader-free so it cannot interact with the exposure guard.
- Probes exercise write→recall round-trips: use `with_scratch_db` like every other harness; no shared-DB debris.
- Known pre-existing gap this suite will likely surface: served path runs as superuser so RLS is bypassed (C1 standing note) — an RLS-dependent leak probe would pass locally and lie in prod. Keep state-filter leak checks at the recall-API layer, not the RLS layer.

## 5. Non-goals

- No LLM-judged "faithfulness" scoring — containment checks only; an LLM judge here re-imports the lattice/exposure machinery for a $0 suite.
- No new conflict-resolution features (merge policies, confidence weighting). The eval measures what exists.
- No public-benchmark claim. This is an internal integrity gate; the mutation/contradiction *benchmark* gap Zero-Mem's critics named is real but building a public instrument is a separate, later decision.
