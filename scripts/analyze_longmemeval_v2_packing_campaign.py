#!/usr/bin/env python3
"""Fail-closed adjudication for the frozen LongMemEval-V2 packing campaign."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from provider_attempts import (  # noqa: E402
    load_provider_attempt_ledger_snapshot,
    validate_provider_attempt_ledger,
)


ARMS = (
    "no_retrieval",
    "memphant_current",
    "memphant_cap1200",
    "memphant_cap1200_submodular",
    "memphant_order_swapped",
)
PACKING_ARM = {
    "memphant_current": "current",
    "memphant_cap1200": "cap1200",
    "memphant_cap1200_submodular": "cap1200_submodular",
    "memphant_order_swapped": "order_swapped",
}
DOMAINS = ("enterprise", "web")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _cell_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    abstention = [row for row in rows if row["is_abstention_problem"]]
    answerable = [row for row in rows if not row["is_abstention_problem"]]
    durations = sorted(float(row["memory_query_duration_seconds"]) for row in rows)
    return {
        "question_count": len(rows),
        "answer_correct": sum(bool(row["score_bool"]) for row in rows),
        "answerable_correct": sum(bool(row["score_bool"]) for row in answerable),
        "answerable_count": len(answerable),
        "llm_judged_abstention_or_premise_resistance_correct": sum(
            bool(row["score_bool"]) for row in abstention
        ),
        "abstention_count": len(abstention),
        "unknown_count": sum(bool(row["is_unknown"]) for row in rows),
        "reader_prompt_tokens": sum(int(row["usage"]["prompt_tokens"]) for row in rows),
        "reader_completion_tokens": sum(
            int(row["usage"]["completion_tokens"]) for row in rows
        ),
        "query_latency_seconds": {
            "mean": statistics.fmean(durations),
            "p95_observed": durations[-1],
            "max": durations[-1],
        },
    }


def _validate_packing_rows(
    rows: list[dict[str, Any]], proof_dir: Path, expected_arm: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    paths = sorted(proof_dir.glob("*.packing.json"))
    if len(paths) != len(rows):
        raise RuntimeError(
            f"packing proof count drift for {expected_arm}: {len(paths)} != {len(rows)}"
        )
    dispositions: dict[str, int] = {}
    item_statuses: dict[str, int] = {}
    verified_support_items = 0
    proofs: dict[str, dict[str, Any]] = {}
    expected_root = proof_dir.resolve()
    for row in rows:
        if row.get("memory_context_was_truncated") is not False:
            raise RuntimeError("packing reader context was truncated")
        metadata = row.get("memory_post_query_metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("packing row metadata is missing")
        path = Path(str(metadata.get("packing_proof_path", ""))).resolve()
        if expected_root not in path.parents or path not in {item.resolve() for item in paths}:
            raise RuntimeError("packing row proof path escaped its frozen cell")
        proof = json.loads(path.read_text())
        claimed = proof.pop("packing_proof_sha256", None)
        if claimed != sha256_json(proof) or metadata.get("packing_proof_sha256") != claimed:
            raise RuntimeError(f"packing proof hash mismatch: {path}")
        contract = proof.get("contract", {})
        packing = proof.get("packing", {})
        if (
            contract.get("packing_arm") != expected_arm
            or metadata.get("packing_arm") != expected_arm
        ):
            raise RuntimeError(f"packing proof arm mismatch: {path}")
        context_hash = sha256_json(row.get("memory_context"))
        if (
            packing.get("context_sha256") != context_hash
            or metadata.get("context_sha256") != context_hash
        ):
            raise RuntimeError(f"packing context is not bound to its proof: {path}")
        disposition = packing.get("disposition")
        if disposition not in {
            "supported",
            "contradicts_premise",
            "near_match",
            "insufficient",
        }:
            raise RuntimeError(f"packing proof disposition is invalid: {path}")
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        for item in packing.get("items", []):
            status = item.get("status")
            if status not in {"supported", "contradicts_premise", "near_match"}:
                raise RuntimeError(f"packing item status is invalid: {path}")
            item_statuses[status] = item_statuses.get(status, 0) + 1
            receipt = item.get("verification_sha256")
            if status == "supported":
                if not (
                    isinstance(receipt, str)
                    and len(receipt) == 64
                    and all(character in "0123456789abcdef" for character in receipt)
                ):
                    raise RuntimeError(f"supported item lacks verified receipt: {path}")
                verified_support_items += 1
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or question_id in proofs:
            raise RuntimeError("packing proof question identity drift")
        proofs[question_id] = {**proof, "packing_proof_sha256": claimed}
    return {
        "proof_count": len(paths),
        "proof_hash_failures": 0,
        "receipt_failures": 0,
        "verified_support_items": verified_support_items,
        "dispositions": dispositions,
        "item_statuses": item_statuses,
    }, proofs


def _free_exact_abstention_gate() -> dict[str, Any]:
    paths = {
        "current": ROOT / "docs/build-log/artifacts/next-evidence/packing/lme-s-pilot-current.json",
        "cap1200": ROOT / "docs/build-log/artifacts/next-evidence/packing/lme-s-pilot-cap1200.json",
    }
    expected = {
        "current": "a925bef25d82000ecb55e6f58ff5d73850e1491a7e3afbcae0b16768fa4b65be",
        "cap1200": "da56ae3a5a3d5f0fa895728e0ebb1f2cf50082d688cbe2dece86a6f66f1f1442",
    }
    rows = {}
    for arm, path in paths.items():
        if sha256_file(path) != expected[arm]:
            raise RuntimeError(f"frozen LME-S exact sentinel drift: {arm}")
        rows[arm] = json.loads(path.read_text())["overall"]
    current = int(rows["current"]["abstention_correct"])
    cap = int(rows["cap1200"]["abstention_correct"])
    return {
        "current": f"{current}/{rows['current']['abstention_n']}",
        "cap1200": f"{cap}/{rows['cap1200']['abstention_n']}",
        "nonregression": cap >= current,
        "artifact_sha256": expected,
    }


def analyze(
    artifact_root: Path,
    manifest_path: Path,
    attempt_ledger: Path,
    *,
    snapshot_loader: Callable[[Path], dict[str, Any]] = load_provider_attempt_ledger_snapshot,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    expected_ids = {
        domain: {case["id"] for case in manifest["cases"] if case["domain"] == domain}
        for domain in DOMAINS
    }
    cells: dict[str, dict[str, Any]] = {}
    proof_summary: dict[str, dict[str, Any]] = {}
    proof_rows: dict[str, dict[str, dict[str, Any]]] = {}
    artifact_hashes: dict[str, str] = {}
    all_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    reader_row_bindings: Counter[tuple[str, str, str, str]] = Counter()
    for arm in ARMS:
        arm_rows: list[dict[str, Any]] = []
        for domain in DOMAINS:
            run_dir = artifact_root / "runs" / arm / domain
            result_path = run_dir / "per_question.jsonl"
            aggregate_path = run_dir / "aggregated_metrics.json"
            prompt_path = run_dir / "prompt_rows.jsonl"
            rows = load_jsonl(result_path)
            prompt_rows = load_jsonl(prompt_path)
            if len(rows) != 6 or {row.get("question_id") for row in rows} != expected_ids[domain]:
                raise RuntimeError(f"official result identity drift: {arm}/{domain}")
            if {row.get("question_id") for row in prompt_rows} != expected_ids[domain]:
                raise RuntimeError(f"official prompt identity drift: {arm}/{domain}")
            prompts = {row["question_id"]: row for row in prompt_rows}
            for row in rows:
                if not isinstance(row.get("score_bool"), bool):
                    raise RuntimeError(f"official result score missing: {arm}/{domain}")
                usage = row.get("usage", {})
                if any(type(usage.get(field)) is not int or usage[field] <= 0 for field in ("prompt_tokens", "completion_tokens", "total_tokens")):
                    raise RuntimeError(f"reader token proof missing: {arm}/{domain}")
                if row.get("memory_context_was_truncated") is not False:
                    raise RuntimeError(f"reader context truncation invalidates cell: {arm}/{domain}")
                if arm == "no_retrieval" and row.get("memory_post_query_metadata") is not None:
                    raise RuntimeError("no-retrieval control unexpectedly emitted packing metadata")
                question_id = row["question_id"]
                all_rows[(arm, domain, question_id)] = row
                request = {
                    "model": "qwen/qwen3.5-9b",
                    "messages": prompts[question_id]["messages"],
                    "timeout": 43200.0,
                    "max_tokens": 1024,
                }
                request_hash = sha256_json(request)
                response_hash = hashlib.sha256(row["response_raw"].encode()).hexdigest()
                reader_row_bindings[(arm, domain, request_hash, response_hash)] += 1
            arm_rows.extend(rows)
            artifact_hashes[str(result_path.relative_to(artifact_root))] = sha256_file(result_path)
            artifact_hashes[str(aggregate_path.relative_to(artifact_root))] = sha256_file(aggregate_path)
            artifact_hashes[str(prompt_path.relative_to(artifact_root))] = sha256_file(prompt_path)
            if arm != "no_retrieval":
                summary, proofs = _validate_packing_rows(
                    rows,
                    artifact_root / "proof" / arm / domain,
                    PACKING_ARM[arm],
                )
                proof_summary[f"{arm}/{domain}"] = summary
                proof_rows[f"{arm}/{domain}"] = proofs
        cells[arm] = _cell_metrics(arm_rows)

    snapshot = snapshot_loader(attempt_ledger)
    validate_provider_attempt_ledger(snapshot)
    attempts = snapshot["attempts"]
    if len(attempts) != 90:
        raise RuntimeError(f"provider attempt count drift: {len(attempts)} != 90")
    context_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    for attempt in attempts:
        start = attempt["start"]
        key = f"{start.get('arm')}/{start.get('domain')}"
        context_counts[key] = context_counts.get(key, 0) + 1
        model = start.get("requested_model")
        model_counts[model] = model_counts.get(model, 0) + 1
    expected_contexts = {f"{arm}/{domain}": 9 for arm in ARMS for domain in DOMAINS}
    if context_counts != expected_contexts:
        raise RuntimeError("provider attempt arm/domain matrix drift")
    if model_counts != {"qwen/qwen3.5-9b": 60, "openai/gpt-5.2": 30}:
        raise RuntimeError("provider attempt model matrix drift")
    reader_attempts = [
        row for row in attempts if row["start"].get("requested_model") == "qwen/qwen3.5-9b"
    ]
    ledger_bindings = Counter(
        (
            row["start"].get("arm"),
            row["start"].get("domain"),
            row["start"].get("request_sha256"),
            row["result"]["response"].get("response_text_sha256"),
        )
        for row in reader_attempts
    )
    if sum(ledger_bindings.values()) != 60 or ledger_bindings != reader_row_bindings:
        raise RuntimeError("reader rows are not exactly bound to provider attempts")

    for domain in DOMAINS:
        ordered_proofs = proof_rows[f"memphant_cap1200_submodular/{domain}"]
        swapped_proofs = proof_rows[f"memphant_order_swapped/{domain}"]
        for question_id in expected_ids[domain]:
            ordered_row = all_rows[("memphant_cap1200_submodular", domain, question_id)]
            swapped_row = all_rows[("memphant_order_swapped", domain, question_id)]
            if ordered_row["memory_context"] != list(reversed(swapped_row["memory_context"])):
                raise RuntimeError("order-swapped control is not an exact context reversal")
            if ordered_proofs[question_id]["packing"]["items"] != list(
                reversed(swapped_proofs[question_id]["packing"]["items"])
            ):
                raise RuntimeError("order-swapped control is not an exact proof reversal")

    current = cells["memphant_current"]
    cap = cells["memphant_cap1200"]
    ordered = cells["memphant_cap1200_submodular"]
    swapped = cells["memphant_order_swapped"]
    primary_pass = (
        cap["answer_correct"] > current["answer_correct"]
        and cap["llm_judged_abstention_or_premise_resistance_correct"]
        >= current["llm_judged_abstention_or_premise_resistance_correct"]
    )
    ordering_pass = (
        ordered["answer_correct"] > cap["answer_correct"]
        or ordered["llm_judged_abstention_or_premise_resistance_correct"]
        > cap["llm_judged_abstention_or_premise_resistance_correct"]
    ) and (
        ordered["answer_correct"] >= cap["answer_correct"]
        and ordered["llm_judged_abstention_or_premise_resistance_correct"]
        >= cap["llm_judged_abstention_or_premise_resistance_correct"]
    )
    negative_control_pass = (
        swapped["answer_correct"] <= ordered["answer_correct"]
        and swapped["llm_judged_abstention_or_premise_resistance_correct"]
        <= ordered["llm_judged_abstention_or_premise_resistance_correct"]
    )
    free_exact = _free_exact_abstention_gate()
    integrity_pass = (
        len(proof_summary) == 8
        and all(value["receipt_failures"] == 0 for value in proof_summary.values())
        and len(artifact_hashes) == 30
        and len(reader_attempts) == 60
    )
    eligible = (
        integrity_pass
        and free_exact["nonregression"]
        and primary_pass
        and ordering_pass
        and negative_control_pass
    )
    return {
        "schema_version": 1,
        "status": "COMPLETE_KILL_GATE_ADJUDICATED",
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "cells": cells,
        "artifact_sha256": artifact_hashes,
        "verified_support_and_receipts": proof_summary,
        "free_exact_abstention_sentinel": free_exact,
        "retrieval_and_packed_gold": {
            "longmemeval_v2": "NOT_AVAILABLE: official n=12 questions contain answer gold but no answer-bearing source-span labels",
            "frozen_longmemeval_s": "Use the existing no-model n=12 packed-gold/hit@10 artifacts; do not infer source recall from answer strings.",
        },
        "stale_or_contradictory_evidence_use": {
            "presented_item_counts": {
                key: value["item_statuses"].get("contradicts_premise", 0)
                for key, value in proof_summary.items()
            },
            "reader_attribution": "NOT_MEASURABLE: official answers do not cite context items; no use claim is made.",
        },
        "provider": {
            "attempt_count": 90,
            "context_counts": context_counts,
            "model_counts": model_counts,
            "reported_cost_usd": snapshot["reported_cost_usd"],
            "attempts_sha256": snapshot["attempts_sha256"],
        },
        "kill_predicates": {
            "primary_cap1200": primary_pass,
            "ordering_increment": ordering_pass,
            "order_swapped_negative_control": negative_control_pass,
            "integrity": integrity_pass,
            "eligible_for_larger_confirmation": eligible,
        },
        "claim_boundary": "n=12 paired answer-quality kill gate only; no default, SOTA, production, or confirmation-scale claim",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks/manifests/longmemeval_v2.packing-kill.n12.json")
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise RuntimeError(f"refusing to overwrite campaign adjudication: {args.out}")
    result = analyze(args.artifact_root, args.manifest, args.attempt_ledger)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["kill_predicates"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
