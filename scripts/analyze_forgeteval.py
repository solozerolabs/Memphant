#!/usr/bin/env python3
"""Classify instrumented ForgetEval outcomes and compare one treatment arm."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


CATEGORIES = (
    "observed_exact_mutation_acknowledgement_failure",
    "adapter_semantic_selection_boundary",
    "intentionally_unsupported_operation",
    "ambiguous_destructive_request_should_fail_closed",
    "benchmark_limitation",
    "already_correct_behavior",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(case: dict) -> str:
    outcome = case["outcome"]
    operations = set(case["mutation_operations"])
    if outcome == "pass":
        return "already_correct_behavior"
    if outcome == "not_applicable":
        if case["error_kind"] != "not_supported":
            raise ValueError(f"{case['case_id']}: unexplained N/A outcome")
        return "intentionally_unsupported_operation"
    if outcome != "fail":
        raise ValueError(f"{case['case_id']}: unknown outcome {outcome!r}")
    if case.get("error_kind") == "benchmark_limitation":
        return "benchmark_limitation"
    decisions = case.get("adapter_decisions")
    if not isinstance(decisions, list):
        raise ValueError(f"{case['case_id']}: adapter evidence is missing")
    mutations = [
        row
        for row in decisions
        if row.get("operation") in {"release", "supersede"}
    ]
    if not mutations:
        if operations & {"release", "supersede"}:
            return "observed_exact_mutation_acknowledgement_failure"
        raise ValueError(f"{case['case_id']}: failed without a classified mutation")
    for mutation in mutations:
        if mutation["operation"] == "release":
            receipts = mutation.get("receipts")
            if not isinstance(receipts, list) or not receipts:
                return "observed_exact_mutation_acknowledgement_failure"
            for receipt in receipts:
                selected = receipt.get("selected_unit_id")
                if (
                    selected not in receipt.get("invalidated_units", [])
                    or receipt.get("verification")
                    != "authorized_transaction_committed"
                ):
                    return "observed_exact_mutation_acknowledgement_failure"
        elif (
            mutation.get("selected_unit_id") not in mutation.get("superseded", [])
            or not mutation.get("created")
        ):
            return "observed_exact_mutation_acknowledgement_failure"
    if "release" in operations:
        return "ambiguous_destructive_request_should_fail_closed"
    if "supersede" in operations:
        return "adapter_semantic_selection_boundary"
    raise ValueError(f"{case['case_id']}: failed without a classified mutation")


def build_report(baseline: dict, candidate: dict) -> dict:
    baseline_cases = baseline["results"]["cases"]
    candidate_cases = candidate["results"]["cases"]
    candidate_by_id = {case["case_id"]: case for case in candidate_cases}
    if len(candidate_by_id) != len(candidate_cases):
        raise ValueError("candidate contains duplicate case IDs")
    if {case["case_id"] for case in baseline_cases} != set(candidate_by_id):
        raise ValueError("baseline and candidate case IDs differ")

    rows = []
    categories: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    for case in baseline_cases:
        candidate_case = candidate_by_id[case["case_id"]]
        if candidate_case["family"] != case["family"]:
            raise ValueError(f"{case['case_id']}: family changed between arms")
        category = classify(case)
        assertion_indexes = {}
        for field in ("missing_must_contain_indexes", "present_must_not_contain_indexes"):
            value = case.get(field)
            if not isinstance(value, list) or any(
                not isinstance(index, int) or isinstance(index, bool) for index in value
            ):
                raise ValueError(f"{case['case_id']}: {field} is invalid")
            assertion_indexes[field] = value
        transition = f"{case['outcome']}->{candidate_case['outcome']}"
        categories[category] += 1
        transitions[transition] += 1
        rows.append(
            {
                "case_id": case["case_id"],
                "family": case["family"],
                "attack_category": case["attack_category"],
                "mutation_operations": case["mutation_operations"],
                **assertion_indexes,
                "baseline_outcome": case["outcome"],
                "triage_category": category,
                "candidate_outcome": candidate_case["outcome"],
                "transition": transition,
            }
        )

    return {
        "schema_version": 1,
        "status": "OPERATION_BOUNDARY_TRIAGE_ROOT_CAUSE_OPEN",
        "baseline_summary": {
            key: baseline["results"][key]
            for key in ("passed", "failed", "not_applicable", "total")
        },
        "candidate_summary": {
            key: candidate["results"][key]
            for key in ("passed", "failed", "not_applicable", "total")
        },
        "category_counts": {name: categories[name] for name in CATEGORIES},
        "transition_counts": dict(sorted(transitions.items())),
        "classification_basis": {
            "method": (
                "Per-case operation-boundary triage records the error kind, failed "
                "assertion indexes, adapter decisions, exact selected IDs, mutation "
                "receipts, and created/superseded IDs. These records do not establish "
                "target correctness, projection freshness, lineage correctness, or "
                "final recall; product root cause remains open."
            ),
            "exact_mutation_boundary": (
                "The adapter fails closed unless POST /v1/correct acknowledges the "
                "selected unit or POST /v1/forget acknowledges every selected unit."
            ),
            "supersession_failures": (
                "The public exact-ID correction primitive succeeded; natural-language "
                "target choice or compound-fact merge planning was the adapter gap."
            ),
            "release_failures": (
                "Natural-language multi-target deletion is destructive and ambiguous; "
                "it requires proposal plus explicit confirmation before exact-ID forget."
            ),
            "purge_na": (
                "MemPhant exposes subject erasure and exact-unit forget, not selective "
                "hard purge by natural-language query."
            ),
        },
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    report = build_report(
        json.loads(baseline_path.read_text(encoding="utf-8")),
        json.loads(candidate_path.read_text(encoding="utf-8")),
    )
    report["inputs"] = {
        "baseline": str(baseline_path),
        "baseline_sha256": sha256_file(baseline_path),
        "candidate": str(candidate_path),
        "candidate_sha256": sha256_file(candidate_path),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"categories": report["category_counts"], "transitions": report["transition_counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
