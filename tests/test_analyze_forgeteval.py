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
    return {
        "case_id": case_id,
        "family": family,
        "attack_category": "fixture",
        "outcome": outcome,
        "error_kind": error_kind,
        "mutation_operations": operations,
    }


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
        "actual_memphant_defect": 0,
        "adapter_mismatch": 1,
        "intentionally_unsupported_operation": 1,
        "ambiguous_destructive_request_should_fail_closed": 1,
        "benchmark_limitation": 0,
        "already_correct_behavior": 1,
    }
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
