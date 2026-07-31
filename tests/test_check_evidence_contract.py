"""Every guard, proved against the real historical case it was built for.

A guard that has never rejected anything is not a guard. Each test below loads a
fixture from ``tests/fixtures/evidence_contract/`` that reproduces a measurement
failure this repo actually committed, and asserts the checker rejects it with the
reason it was built to give.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/evidence_contract"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cec = _load("check_evidence_contract", "scripts/check_evidence_contract.py")
ip = _load("instrument_power", "scripts/instrument_power.py")


def violations(fixture: str) -> list[str]:
    return [str(v) for v in cec.check_artifact(FIXTURES / fixture)]


def rejects(fixture: str, needle: str) -> None:
    found = violations(fixture)
    assert found, f"{fixture}: guard accepted a case it was built to reject"
    assert any(needle in v for v in found), f"{fixture}: {needle!r} not in {found}"


# ---------------------------------------------------------------------------
# the shape that passes -- without this the suite only proves the checker says no
# ---------------------------------------------------------------------------


def test_a_complete_contract_passes() -> None:
    assert violations("passing_forgeteval_lineage.json") == []


def test_a_missing_contract_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"verdict": "PASS", "delta": 0.12}), encoding="utf-8")
    found = [str(v) for v in cec.check_artifact(path)]
    assert any("no `evidence_contract` block" in v for v in found)


def test_a_registered_artifact_that_does_not_exist_is_a_failure(tmp_path: Path) -> None:
    found = [str(v) for v in cec.check_artifact(tmp_path / "absent.json")]
    assert any("does not exist" in v for v in found)


# ---------------------------------------------------------------------------
# failure 1 -- underpowered runs reported as nulls
# ---------------------------------------------------------------------------


def test_min_decisional_nd_is_derived_not_chosen() -> None:
    # Two-sided exact binomial at alpha=0.05: no rejection region exists below
    # n_d=6, and one exists at 6. The constant is a consequence of the test.
    for n_d in range(6):
        assert ip.exact_binom_reject(n_d) == (), n_d
    assert ip.exact_binom_reject(6) == (0, 6)
    assert cec.MIN_DECISIONAL_ND == 6


def test_rejects_the_nd2_abstention_screen() -> None:
    # The real n_d=2 screen that rejected pack_render_cap.
    rejects("nd2_abstention_screen.json", "power.n_d=2 < 6 but decisional=true")


def test_nd_below_six_is_allowed_only_as_non_decisional() -> None:
    doc = json.loads((FIXTURES / "nd2_abstention_screen.json").read_text())
    contract = doc["evidence_contract"]
    contract["decisional"] = False
    assert cec.check_contract(contract) == []


def test_rejects_b_equals_c_equals_zero_cited_as_a_decision() -> None:
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    contract["power"].update({"b": 0, "c": 0, "n_d": 0, "psi_observed": 0.0, "mde_at_80": None})
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("power.n_d=0 < 6 but decisional=true" in v for v in found)


def test_rejects_an_nd_that_disagrees_with_its_own_cells() -> None:
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    contract["power"]["n_d"] = 99
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("does not equal b+c" in v for v in found)


# ---------------------------------------------------------------------------
# failure 2 -- power asserted, not computed
# ---------------------------------------------------------------------------


def test_rejects_the_phase2_asserted_power_claim() -> None:
    # "~80% power at psi~=0.15" recomputes to 0.728 there and 0.541 at the psi
    # the lane exhibits. The asserted MDE does not survive recomputation.
    rejects("asserted_power_phase2.json", "does not match the recomputed")


def test_a_decisional_artifact_must_carry_a_computed_mde() -> None:
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    del contract["power"]["mde_at_80"]
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("must carry a COMPUTED MDE" in v for v in found)


def test_rejects_an_mde_on_an_unpowerable_lane() -> None:
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    contract["power"].update({"n": 12, "b": 2, "c": 0, "n_d": 2, "psi_observed": 2 / 12, "mde_at_80": 0.07})
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("UNPOWERABLE" in v for v in found)


# ---------------------------------------------------------------------------
# failure 3 -- leakage collapsed to one number
# ---------------------------------------------------------------------------


def test_rejects_leakage_reported_as_a_scalar() -> None:
    found = violations("leakage_collapsed_to_one_number.json")
    for field in ("unit_definition", "absolute_target_coverage", "floor", "floor_kind", "provenance_class"):
        assert any(f"leakage.{field}" in v for v in found), field


def test_rejects_absolute_coverage_compared_across_unit_definitions() -> None:
    # Same bank: 'user turn + agent reply' 0.3367 vs 'user turn only' 0.1871.
    rejects("leakage_unit_mismatch.json", "DIFFERENT unit definitions")


def test_rejects_a_decision_taken_on_a_contaminated_bank() -> None:
    rejects("contaminated_track_r_bank.json", "provenance_class=authored_from_target")


def test_lexical_tractability_alone_does_not_disqualify() -> None:
    # Only contamination disqualifies. A lexically pointed but independently
    # authored bank stays decisional -- it just measures the lexical regime.
    doc = json.loads((FIXTURES / "contaminated_track_r_bank.json").read_text())
    contract = doc["evidence_contract"]
    contract["leakage"]["provenance_class"] = "authored_independently"
    assert cec.check_contract(contract) == []


# ---------------------------------------------------------------------------
# failure 4 -- bars preregistered below the achievable floor
# ---------------------------------------------------------------------------


def test_rejects_the_paraphrase_bar_set_below_the_measured_floor() -> None:
    # Preregistered <= 1.50 against a measured achievable floor of 1.79.
    rejects("bar_below_floor_paraphrase.json", "sits BELOW the recorded achievable floor 1.79")


def test_rejects_a_bar_a_published_human_corpus_cannot_pass() -> None:
    # 2.05 clears the 1.76 floor, so the floor check alone passes it; swe-prbench
    # measures 2.42 through our own pipeline and fails it.
    rejects("bar_failed_by_human_corpus.json", "is failed by a recorded human corpus")


def test_rejects_a_bar_with_no_recorded_floor_behind_it() -> None:
    doc = json.loads((FIXTURES / "bar_below_floor_paraphrase.json").read_text())
    contract = doc["evidence_contract"]
    contract["bar"]["floor_reference"] = "vibes"
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("is not a recorded floor" in v for v in found)


def test_rejects_a_bar_calibrated_against_a_different_negative_selection() -> None:
    # Concentration moves ~1.8x with negative selection (1.76-2.03 same-domain
    # vs ~3.70 random-corpus), so the comparison must match.
    doc = json.loads((FIXTURES / "bar_failed_by_human_corpus.json").read_text())
    contract = doc["evidence_contract"]
    contract["bar"]["threshold"] = 3.0
    contract["leakage"]["negative_selection"] = "random-corpus negatives"
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("negative_selection" in v for v in found)


def test_the_committed_floor_reference_matches_the_recorded_measurements() -> None:
    floors = {f["id"]: f for f in cec.load_json(cec.FLOORS_PATH)["floors"]}
    assert floors["w0-probe-2026-07-31"]["concentration"] == 1.79
    assert floors["w0-probe-2026-07-31"]["n"] == 27
    human = floors["human-coding-queries-2026-07-31"]
    assert human["concentration_band"] == [1.76, 2.03]
    assert human["absolute_target_coverage_band"] == [0.175, 0.287]


# ---------------------------------------------------------------------------
# failure 5 -- a probe that ran with the mechanism switched off
# ---------------------------------------------------------------------------


def test_rejects_the_packing_verdict_taken_with_the_mechanism_off() -> None:
    # 276/276 "measured-permanent", taken on the cross-rerank arm where
    # rank_based_ordering_active disables the contest under test.
    rejects("mechanism_off_packing_verdict.json", "mechanism_enabled=false on a suppression probe")


def test_a_probe_must_state_whether_the_mechanism_was_on() -> None:
    doc = json.loads((FIXTURES / "mechanism_off_packing_verdict.json").read_text())
    contract = doc["evidence_contract"]
    del contract["mechanism_enabled"]
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("mechanism_enabled: REQUIRED when probe_kind" in v for v in found)


def test_mechanism_on_must_name_its_evidence() -> None:
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    del contract["mechanism_evidence"]
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("mechanism_evidence: REQUIRED" in v for v in found)


# ---------------------------------------------------------------------------
# failure 6 -- harness settings not recorded beside scores
# ---------------------------------------------------------------------------


def test_rejects_a_score_with_no_harness_recorded() -> None:
    rejects("harness_not_recorded.json", "evidence_contract.harness: REQUIRED field missing")


@pytest.mark.parametrize("field", ["embed_model", "scorer", "k", "budget", "flags"])
def test_every_harness_setting_is_individually_required(field: str) -> None:
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    del contract["harness"][field]
    found = [str(v) for v in cec.check_contract(contract)]
    assert any(f"harness.{field}" in v for v in found), field


# ---------------------------------------------------------------------------
# failure 7 -- corpora that mutate under their own lock
# ---------------------------------------------------------------------------


def test_rejects_a_live_path_used_as_a_corpus_identity() -> None:
    # Track U: a concurrent session wrote a feedback_* file mid-run, 90 -> 91.
    rejects("corpus_live_path_track_u.json", "is a live filesystem path, not a snapshot identity")


def test_rejects_a_corpus_whose_content_no_longer_matches_its_pin(tmp_path: Path) -> None:
    corpus = ROOT / "benchmarks/manifests/leakage_floor_reference.json"
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    contract["corpus"]["path"] = str(corpus.relative_to(ROOT))
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("does not match the current content" in v for v in found)


# ---------------------------------------------------------------------------
# failure 8 -- instruments trusted from cards and READMEs
# ---------------------------------------------------------------------------


def test_rejects_an_instrument_that_ships_zero_rows_for_the_field_under_test() -> None:
    # SWE-Explore: 848 rows, problem_statement on 0, base_commit on 0.
    rejects("instrument_zero_shipped_rows.json", "ships zero populated rows for this field")


def test_rejects_a_shields_io_badge_as_a_licence() -> None:
    rejects("instrument_badge_licence.json", "is not one of ['LICENSE_FILE', 'RECORD_METADATA', 'unverified']")


def test_rejects_uncounted_shipped_rows_on_a_decisional_artifact() -> None:
    doc = json.loads((FIXTURES / "passing_forgeteval_lineage.json").read_text())
    contract = doc["evidence_contract"]
    contract["instrument_verification"] = {
        "shipped_rows_verified": False,
        "rows_counted": 848,
        "fields_counted": {"instance_id": 848},
        "license_id": "cc-by-4.0",
        "license_source": "RECORD_METADATA",
    }
    found = [str(v) for v in cec.check_contract(contract)]
    assert any("shipped_rows_verified is not true" in v for v in found)


# ---------------------------------------------------------------------------
# failure 9 -- --lib instead of --workspace, and base-relative attribution
# ---------------------------------------------------------------------------


def test_rejects_base_relative_attribution_on_a_decision() -> None:
    rejects("base_relative_attribution.json", "unverified field")


def test_ci_has_a_workspace_wide_rust_floor() -> None:
    assert cec.check_ci_workflow() == []
    assert "cargo test --workspace" in cec.CI_WORKFLOW.read_text(encoding="utf-8")


def test_rejects_a_ci_workflow_that_narrows_to_lib(tmp_path: Path) -> None:
    # `-p memphant-core --lib` runs 137 tests and excludes all 30 files in
    # memphant-core/tests/. That is how a packing regression shipped.
    path = tmp_path / "ci.yml"
    path.write_text("      - run: cargo test --workspace\n      - run: cargo test -p memphant-core --lib\n")
    found = [str(v) for v in cec.check_ci_workflow(path)]
    assert any("`--lib` narrows a CI test invocation" in v for v in found)


def test_rejects_a_ci_workflow_with_no_workspace_floor(tmp_path: Path) -> None:
    path = tmp_path / "ci.yml"
    path.write_text("      - run: cargo test -p memphant-core\n")
    found = [str(v) for v in cec.check_ci_workflow(path)]
    assert any("no `cargo test --workspace` floor" in v for v in found)


def test_a_missing_ci_workflow_fails_closed(tmp_path: Path) -> None:
    found = [str(v) for v in cec.check_ci_workflow(tmp_path / "nope.yml")]
    assert any("CI workflow missing" in v for v in found)


# ---------------------------------------------------------------------------
# the ratchet and the retrofit ledger
# ---------------------------------------------------------------------------


def test_every_decisional_artifact_is_contracted_or_declared_debt() -> None:
    assert [str(v) for v in cec.scan_unregistered()] == []


def test_the_ratchet_rejects_a_new_unregistered_decisional_artifact(monkeypatch) -> None:
    monkeypatch.setattr(
        cec,
        "decisional_candidates",
        lambda root=cec.ROOT: [("docs/build-log/artifacts/brand-new-result.json", ["verdict"])],
    )
    found = [str(v) for v in cec.scan_unregistered()]
    assert any("neither in `contracted` nor recorded as retrofit debt" in v for v in found)


def test_the_retrofit_report_is_current() -> None:
    expected = json.dumps(cec.build_report(), indent=2) + "\n"
    assert cec.REPORT_PATH.read_text(encoding="utf-8") == expected, (
        "rerun `python3 scripts/check_evidence_contract.py --report`"
    )


def test_the_retrofit_report_backfills_nothing() -> None:
    report = json.loads(cec.REPORT_PATH.read_text(encoding="utf-8"))
    assert report["candidates"] == report["pending_retrofit"] + report["contracted"]
    # Not one historical artifact carries a contract yet, and none was invented:
    # every candidate is reported as failing with its fields absent.
    assert report["failing"] == report["candidates"] - report["contracted"]


def test_registry_entries_are_unique_and_disjoint() -> None:
    registry = cec.load_json(cec.REGISTRY_PATH)
    contracted = registry["contracted"]
    pending = [entry["path"] for entry in registry["pending"]]
    assert len(contracted) == len(set(contracted))
    assert len(pending) == len(set(pending))
    assert not set(contracted) & set(pending)
    for entry in registry["pending"]:
        assert entry["reason"].strip(), entry["path"]


def test_the_schema_is_the_single_source_of_required_fields() -> None:
    schema = cec.load_json(cec.SCHEMA_PATH)
    assert set(schema["required"]) == {
        "schema_version",
        "decisional",
        "claim",
        "power",
        "harness",
        "corpus",
    }
    assert schema["properties"]["leakage"]["required"] == [
        "unit_definition",
        "absolute_target_coverage",
        "floor",
        "floor_kind",
        "concentration",
        "provenance_class",
    ]
