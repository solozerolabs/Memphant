#!/usr/bin/env python3
"""Build a scratch-only ForgetEval confirmation ledger after deterministic review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def load_cases(official_dir: Path) -> list[Any]:
    sys.path.insert(0, str(official_dir.resolve()))
    from bench.forgeteval.adversarial import ADVERSARIAL_TESTS

    return list(ADVERSARIAL_TESTS)


def extend_lineage_confirmations(
    base_document: dict,
    inputs_document: dict,
    cases: list[Any],
    *,
    reviewed_by: str,
    reviewed_at: str,
    inputs_path: str,
    inputs_sha256: str,
) -> dict:
    if base_document.get("schema_version") != 1:
        raise ValueError("base confirmation ledger must use schema_version 1")
    case_map = {case.id: case for case in cases}
    confirmations = [dict(row) for row in base_document.get("confirmations", [])]
    by_transition = {
        (row["case_id"], row["mutation_index"]): row for row in confirmations
    }
    if len(by_transition) != len(confirmations):
        raise ValueError("base confirmation ledger contains duplicate transitions")

    added = 0
    for source in sorted(
        inputs_document.get("inputs", []),
        key=lambda row: (row["case_id"], row["mutation_index"]),
    ):
        key = (source["case_id"], source["mutation_index"])
        if key in by_transition:
            continue
        case = case_map.get(source["case_id"])
        if case is None or not 1 <= source["mutation_index"] <= len(case.mutations):
            raise ValueError(f"unknown lineage transition: {key}")
        operation, query, *values = case.mutations[source["mutation_index"] - 1]
        expected_new_text = values[0] if values else None
        if (
            source["operation"] != operation
            or source["query"] != query
            or source.get("new_text") != expected_new_text
        ):
            raise ValueError(f"lineage input does not match official transition: {key}")
        if operation != "supersede":
            raise ValueError(f"lineage completion supports supersession only: {key}")
        previous = by_transition.get((source["case_id"], source["mutation_index"] - 1))
        previous_text = previous.get("replacement_text") if previous else None
        if not isinstance(previous_text, str) or not previous_text:
            raise ValueError(f"lineage transition has no confirmed predecessor: {key}")
        previous_hash = sha256_bytes(previous_text.encode())
        matches = [
            row
            for row in source["candidates"]
            if row["body_sha256"] == previous_hash
            and sha256_bytes(row["body"].encode()) == previous_hash
        ]
        if len(matches) != 1:
            raise ValueError(
                f"confirmed predecessor matched {len(matches)} candidates: {key}"
            )
        provenance = {
            "selection_source": "deterministic_previous_transition",
            "input_sha256": source["input_sha256"],
            "predecessor_body_sha256": previous_hash,
        }
        row = {
            "input_sha256": source["input_sha256"],
            "case_id": source["case_id"],
            "mutation_index": source["mutation_index"],
            "operation": operation,
            "confirmed": True,
            "confirmed_by": reviewed_by,
            "selected_body_sha256": [previous_hash],
            "replacement_text": expected_new_text,
            "proposal_sha256": sha256_json(provenance),
            "selection_source": provenance["selection_source"],
        }
        confirmations.append(row)
        by_transition[key] = row
        added += 1

    output = dict(base_document)
    output["reviewed_at"] = reviewed_at
    output["lineage_completion"] = {
        "source_inputs": inputs_path,
        "source_inputs_sha256": inputs_sha256,
        "selection_source": "deterministic_previous_transition",
        "added_confirmations": added,
    }
    output["confirmations"] = confirmations
    return output


def build_confirmation(
    proposals_document: dict,
    inputs_document: dict,
    cases: list[Any],
    overrides_document: dict,
    *,
    reviewed_by: str,
    reviewed_at: str,
    proposals_path: str,
    proposals_sha256: str,
    inputs_path: str,
    inputs_sha256: str,
    replacement_policy: str = "proposal",
) -> dict:
    inputs = {row["input_sha256"]: row for row in inputs_document["inputs"]}
    proposals = proposals_document["proposals"]
    overrides = overrides_document.get("overrides", {})
    proposal_hashes = {row["proposal_sha256"] for row in proposals}
    unknown_overrides = sorted(set(overrides) - proposal_hashes)
    if unknown_overrides:
        raise ValueError(f"override references unknown proposals: {unknown_overrides}")

    reviewed = []
    by_case: dict[str, list[dict]] = defaultdict(list)
    for proposal in proposals:
        source = inputs.get(proposal["input_sha256"])
        if source is None:
            raise ValueError(f"proposal input is missing: {proposal['input_sha256']}")
        if proposal["operation"] != source["operation"]:
            raise ValueError(f"proposal operation mismatch: {proposal['proposal_sha256']}")
        replacement = proposal.get("replacement_text")
        selected_body_sha256 = proposal["selected_body_sha256"]
        if proposal["operation"] == "supersede" and replacement_policy == "exact_new_fact":
            replacement = source["new_text"]
        override = overrides.get(proposal["proposal_sha256"])
        if override is not None:
            if replacement_policy == "exact_new_fact":
                raise ValueError("override conflicts with deterministic replacement")
            if set(override) != {"replacement_text", "reason"}:
                raise ValueError("override must contain replacement_text and reason")
            replacement = override["replacement_text"]
        if proposal["operation"] == "supersede":
            if len(selected_body_sha256) != 1:
                raise ValueError("supersession must select exactly one body")
            if not isinstance(replacement, str) or not replacement.startswith(
                source["new_text"]
            ):
                raise ValueError(
                    f"replacement does not start with exact NEW_FACT: "
                    f"{proposal['proposal_sha256']}"
                )
        elif replacement is not None:
            raise ValueError("release replacement_text must be null")
        row = {
            "input_sha256": proposal["input_sha256"],
            "case_id": proposal["case_id"],
            "mutation_index": proposal["mutation_index"],
            "operation": proposal["operation"],
            "confirmed": True,
            "confirmed_by": reviewed_by,
            "selected_body_sha256": selected_body_sha256,
            "replacement_text": replacement,
            "proposal_sha256": proposal["proposal_sha256"],
        }
        reviewed.append(row)
        by_case[row["case_id"]].append(row)

    case_map = {case.id: case for case in cases}
    for case_id, rows in by_case.items():
        case = case_map.get(case_id)
        if case is None:
            raise ValueError(f"proposal references unknown official case: {case_id}")
        expected = [
            (index, mutation[0])
            for index, mutation in enumerate(case.mutations, start=1)
            if mutation[0] != "purge"
        ]
        actual = sorted((row["mutation_index"], row["operation"]) for row in rows)
        if actual != expected:
            raise ValueError(
                f"incomplete transition chain for {case_id}: "
                f"expected {expected}, got {actual}"
            )

    transition_failures = []
    oracle_failures = []
    oracle_passes = 0
    for case_id, rows in by_case.items():
        case = case_map[case_id]
        state = list(case.setup_facts)
        for row in sorted(rows, key=lambda value: value["mutation_index"]):
            selected = set(row["selected_body_sha256"])
            matches = [
                index
                for index, body in enumerate(state)
                if sha256_bytes(body.encode()) in selected
            ]
            if len(matches) != len(selected):
                transition_failures.append(
                    {
                        "case_id": case_id,
                        "mutation_index": row["mutation_index"],
                        "expected_matches": len(selected),
                        "actual_matches": len(matches),
                    }
                )
                break
            if row["operation"] == "release":
                for index in sorted(matches, reverse=True):
                    state.pop(index)
            else:
                state[matches[0]] = row["replacement_text"]
        else:
            blob = " ".join(state).lower()
            missing = [
                value for value in case.must_contain if value.lower() not in blob
            ]
            present = [
                value for value in case.must_not_contain if value.lower() in blob
            ]
            if missing or present:
                oracle_failures.append(
                    {
                        "case_id": case_id,
                        "missing_must_contain": missing,
                        "present_must_not_contain": present,
                    }
                )
            else:
                oracle_passes += 1
    if transition_failures:
        raise ValueError(f"transition chain review failed: {transition_failures[:3]}")

    return {
        "schema_version": 1,
        "scope": "scratch_benchmark_evaluation_only",
        "source_proposals": proposals_path,
        "source_proposals_sha256": proposals_sha256,
        "source_inputs": inputs_path,
        "source_inputs_sha256": inputs_sha256,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "replacement_policy": replacement_policy,
        "override_file_sha256": sha256_json(overrides_document),
        "review_summary": {
            "proposal_count": len(reviewed),
            "override_count": len(overrides),
            "transition_chain_failures": 0,
            "state_oracle_passed": oracle_passes,
            "state_oracle_failed": len(oracle_failures),
            "state_oracle_failures": oracle_failures,
        },
        "confirmations": reviewed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--official-dir", required=True)
    parser.add_argument("--overrides", required=True)
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument(
        "--replacement-policy",
        choices=["proposal", "exact_new_fact"],
        default="proposal",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    proposal_path = Path(args.proposals)
    input_path = Path(args.inputs)
    override_path = Path(args.overrides)
    output = build_confirmation(
        json.loads(proposal_path.read_text()),
        json.loads(input_path.read_text()),
        load_cases(Path(args.official_dir)),
        json.loads(override_path.read_text()),
        reviewed_by=args.reviewed_by,
        reviewed_at=args.reviewed_at,
        proposals_path=args.proposals,
        proposals_sha256=sha256_file(proposal_path),
        inputs_path=args.inputs,
        inputs_sha256=sha256_file(input_path),
        replacement_policy=args.replacement_policy,
    )
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
