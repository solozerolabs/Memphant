from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "analyze_forgeteval", ROOT / "scripts/analyze_forgeteval.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def case(case_id, family, outcome, operations, error_kind=None):
    value = {
        "case_id": case_id,
        "family": family,
        "attack_category": "fixture",
        "outcome": outcome,
        "error_kind": error_kind,
        "mutation_operations": operations,
    }
    decisions = []
    if "release" in operations:
        decisions.append(
            {
                "operation": "release",
                "receipts": [
                    {
                        "selected_unit_id": "unit",
                        "invalidated_units": ["unit"],
                        "verification": "authorized_transaction_committed",
                    }
                ],
            }
        )
    if "supersede" in operations:
        decisions.append(
            {
                "operation": "supersede",
                "selected_unit_id": "unit",
                "superseded": ["unit"],
                "created": ["replacement"],
            }
        )
    value["adapter_decisions"] = decisions
    value["missing_must_contain_indexes"] = []
    value["present_must_not_contain_indexes"] = []
    return value


def report(cases):
    counts = {
        "passed": sum(row["outcome"] == "pass" for row in cases),
        "failed": sum(row["outcome"] == "fail" for row in cases),
        "not_applicable": sum(row["outcome"] == "not_applicable" for row in cases),
        "total": len(cases),
        "cases": cases,
    }
    return {"results": counts}


def test_build_report_classifies_control_plane_boundaries_and_transitions() -> None:
    baseline = report(
        [
            case("pass", "drift", "pass", ["supersede"]),
            case("update", "supersession", "fail", ["supersede"]),
            case("delete", "amnesia", "fail", ["release"]),
            case("purge", "purge", "not_applicable", ["purge"], "not_supported"),
        ]
    )
    candidate = report(
        [
            case("pass", "drift", "fail", ["supersede"]),
            case("update", "supersession", "pass", ["supersede"]),
            case("delete", "amnesia", "pass", ["release"]),
            case("purge", "purge", "not_applicable", ["purge"], "not_supported"),
        ]
    )

    result = module.build_report(baseline, candidate)

    assert result["category_counts"] == {
        "observed_exact_mutation_acknowledgement_failure": 0,
        "adapter_semantic_selection_boundary": 1,
        "intentionally_unsupported_operation": 1,
        "ambiguous_destructive_request_should_fail_closed": 1,
        "benchmark_limitation": 0,
        "already_correct_behavior": 1,
    }
    assert result["status"] == "OPERATION_BOUNDARY_TRIAGE_ROOT_CAUSE_OPEN"
    assert result["cases"][1]["missing_must_contain_indexes"] == []
    assert result["transition_counts"] == {
        "fail->pass": 2,
        "not_applicable->not_applicable": 1,
        "pass->fail": 1,
    }


def test_classification_fails_closed_on_unexplained_outcomes() -> None:
    with pytest.raises(ValueError, match="unexplained N/A"):
        module.classify(case("x", "purge", "not_applicable", ["purge"], "timeout"))
    with pytest.raises(ValueError, match="without a classified mutation"):
        module.classify(case("x", "unknown", "fail", ["recall"]))


def test_failed_exact_mutation_receipt_is_an_observed_acknowledgement_failure() -> None:
    value = case("x", "amnesia", "fail", ["release"])
    value["adapter_decisions"][0]["receipts"][0]["verification"] = "unknown"
    assert module.classify(value) == "observed_exact_mutation_acknowledgement_failure"


def test_assertion_indexes_are_required_for_triage() -> None:
    baseline_case = case("x", "drift", "fail", ["supersede"])
    del baseline_case["missing_must_contain_indexes"]
    with pytest.raises(ValueError, match="missing_must_contain_indexes"):
        module.build_report(report([baseline_case]), report([case("x", "drift", "pass", ["supersede"])]))


def test_explicit_benchmark_limitation_is_reachable() -> None:
    value = case("x", "drift", "fail", ["supersede"], "benchmark_limitation")
    assert module.classify(value) == "benchmark_limitation"


def test_frozen_n12_manifest_is_unique_and_model_free_so_far() -> None:
    import json

    manifest = json.loads(
        (ROOT / "benchmarks/manifests/forgeteval.next-evidence.n12.json").read_text()
    )
    assert len(manifest["case_ids"]) == 12
    assert len(set(manifest["case_ids"])) == 12
    assert manifest["mutation_call_count"] == 16
    assert manifest["frozen_before_model_calls"] is True
    assert manifest["free_screen"]["promotion_decision"] == "rejected"
