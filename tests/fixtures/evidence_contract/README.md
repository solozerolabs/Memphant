# Evidence-contract guard fixtures

Every file here reproduces a measurement failure this repo actually committed, in
the shape the evidence contract would have caught it. `tests/test_check_evidence_contract.py`
asserts that `scripts/check_evidence_contract.py` **rejects** each one. A guard
that has never rejected anything is not a guard.

These are fixtures, not evidence. Each carries `"__fixture__"` naming the real
case it reproduces and the doc it is taken from. The measured cells (`n`, `b`,
`c`, coverages, thresholds) are copied from the cited artifacts; the surrounding
harness/corpus strings are illustrative fixture scaffolding and are never cited
as MemPhant measurements.

| fixture | failure | guard |
|---|---|---|
| `nd2_abstention_screen.json` | 1 — underpowered run reported as a null | `_guard_power`, n_d < 6 |
| `asserted_power_phase2.json` | 2 — power asserted, not computed | `_guard_power`, MDE recomputation |
| `leakage_collapsed_to_one_number.json` | 3 — leakage as a scalar | schema required fields |
| `leakage_unit_mismatch.json` | 3 — coverage compared across unit definitions | `_guard_leakage` |
| `contaminated_track_r_bank.json` | 3 — contamination vs lexical tractability | `_guard_leakage` |
| `bar_below_floor_paraphrase.json` | 4 — bar preregistered below the achievable floor | `_guard_bar` |
| `bar_failed_by_human_corpus.json` | 4 — a published human corpus fails our gate | `_guard_bar` |
| `mechanism_off_packing_verdict.json` | 5 — probe ran with the mechanism switched off | `_guard_mechanism` |
| `harness_not_recorded.json` | 6 — harness settings not recorded beside scores | schema required fields |
| `corpus_live_path_track_u.json` | 7 — corpus that mutates under its own lock | `_guard_corpus` |
| `instrument_zero_shipped_rows.json` | 8 — instrument trusted from its card | `_guard_instrument` |
| `instrument_badge_licence.json` | 8 — a shields.io badge is not a licence | schema enum |
| `base_relative_attribution.json` | 9 — "pre-existing at my base" | unverified-on-decisional |
| `passing_forgeteval_lineage.json` | — | the shape that passes |
