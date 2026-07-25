#!/usr/bin/env python3
"""Run a bounded, read-only sufficiency-card screen over packed LME evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_attempts import ProviderAttemptLedger
from run_reader import ReaderCli, restore_spend_from_attempts


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT = (
    "You are an evidence-sufficiency controller, not an answer generator. "
    "Select only ranked evidence that is necessary to answer the question fully "
    "and unambiguously. Mark sufficient=false when any entity, state, time "
    "anchor, quantity, comparison operand, or disambiguating fact is missing, "
    "conflicting, or only a range where an exact answer is requested. Related "
    "evidence is not sufficient evidence. Use no outside knowledge. Return only "
    "the required strict JSON object."
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def decision_input(row: dict) -> dict:
    return {
        "question_id": row["question_id"],
        "question": row["question"],
        "question_date": row.get("question_date"),
        "evidence": [
            {
                "rank": item["rank"],
                "session_id": item.get("session_id"),
                "body": item["body"],
            }
            for item in row["evidence"]
        ],
    }


def build_prompt(row: dict) -> str:
    source = decision_input(row)
    candidates = "\n\n".join(
        f"[{item['rank']}] {item['body']}" for item in source["evidence"]
    )
    return f"""Decide whether the ranked evidence can answer the question fully and unambiguously.

Rules:
- Select the smallest sufficient set of ranks; never select a rank merely for topical similarity.
- sufficient=true requires a complete, unambiguous answer derivable from selected evidence alone.
- sufficient=false requires concise missing_evidence entries naming every unresolved need.
- negative_transfer_ranks identifies stale, conflicting, wrong-entity, or misleading partial evidence.
- Do not answer the question and do not infer facts absent from evidence.

QUESTION_DATE: {source['question_date'] or 'unknown'}
QUESTION: {source['question']}

RANKED EVIDENCE:
{candidates}

Return exactly:
{{"selected_ranks":[1],"sufficient":true,"negative_transfer_ranks":[],"missing_evidence":[],"reason":"concise reason"}}"""


def parse_decision(reply: str, row: dict) -> dict:
    try:
        value = json.loads(reply)
    except json.JSONDecodeError as error:
        raise ValueError("sufficiency response is not strict JSON") from error
    expected = {
        "selected_ranks",
        "sufficient",
        "negative_transfer_ranks",
        "missing_evidence",
        "reason",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("sufficiency response fields do not match the schema")
    available = {item["rank"] for item in row["evidence"]}
    for field in ("selected_ranks", "negative_transfer_ranks"):
        ranks = value[field]
        if (
            not isinstance(ranks, list)
            or any(type(rank) is not int for rank in ranks)
            or len(ranks) != len(set(ranks))
        ):
            raise ValueError(f"{field} must contain unique integer ranks")
        if any(rank not in available for rank in ranks):
            raise ValueError(f"{field} contains a rank outside the evidence set")
    if set(value["selected_ranks"]) & set(value["negative_transfer_ranks"]):
        raise ValueError("selected and negative-transfer ranks must be disjoint")
    if type(value["sufficient"]) is not bool:
        raise ValueError("sufficient must be a boolean")
    missing = value["missing_evidence"]
    if not isinstance(missing, list) or any(
        not isinstance(item, str) or not item.strip() for item in missing
    ):
        raise ValueError("missing_evidence must contain nonempty strings")
    if value["sufficient"]:
        if not value["selected_ranks"] or missing:
            raise ValueError("sufficient decisions need selected ranks and no missing_evidence")
    elif not missing:
        raise ValueError("insufficient decisions require missing_evidence")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("reason must be a nonempty string")
    result = dict(value)
    result["question_id"] = row["question_id"]
    result["input_sha256"] = sha256_json(decision_input(row))
    result["decision_sha256"] = sha256_json(result)
    return result


def failure_record(row: dict, error: Exception, reply: str | None) -> dict:
    record = {
        "question_id": row["question_id"],
        "input_sha256": sha256_json(decision_input(row)),
        "error_kind": type(error).__name__,
        "error": str(error),
    }
    if reply is not None:
        record["response_sha256"] = sha256_bytes(reply.encode())
    return record


def apply_decisions(
    rows: list[dict], decisions: list[dict], dataset: dict[str, dict]
) -> tuple[dict, list[dict]]:
    by_question = {row["question_id"]: row for row in decisions}
    if len(by_question) != len(decisions):
        raise ValueError("duplicate sufficiency decisions")
    if set(by_question) != {row["question_id"] for row in rows}:
        raise ValueError("sufficiency decisions do not cover the evidence rows exactly")
    summary = {
        "total": len(rows),
        "scored": 0,
        "scored_hit_at_10": 0,
        "abstention": 0,
        "abstention_correct": 0,
    }
    compiled = []
    for row in rows:
        decision = by_question[row["question_id"]]
        selected = [
            item for item in row["evidence"] if item["rank"] in decision["selected_ranks"]
        ]
        official = dataset[row["question_id"]]
        answer_sessions = set(official["answer_session_ids"])
        if row["is_abstention"]:
            summary["abstention"] += 1
            if not decision["sufficient"]:
                summary["abstention_correct"] += 1
        else:
            summary["scored"] += 1
            if decision["sufficient"] and any(
                item.get("session_id") in answer_sessions for item in selected
            ):
                summary["scored_hit_at_10"] += 1
        output = dict(row)
        output["abstained"] = not decision["sufficient"]
        output["evidence"] = selected if decision["sufficient"] else []
        output["sufficiency_decision_sha256"] = decision.get("decision_sha256")
        compiled.append(output)
    return summary, compiled


def load_jsonl(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows or len({row["question_id"] for row in rows}) != len(rows):
        raise ValueError("evidence JSONL must contain unique question rows")
    return rows


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("".join(json.dumps(row) + "\n" for row in rows))
    os.replace(temporary, path)


def validate_authorization(manifest: dict, args: argparse.Namespace) -> None:
    authorization = manifest.get("authorization")
    scope = {
        key: value
        for key, value in manifest.items()
        if key not in {"status", "authorization"}
    }
    if (
        manifest.get("status") != "AUTHORIZED_FOR_PAID_EXECUTION"
        or not isinstance(authorization, dict)
        or not str(authorization.get("authorized_by", "")).strip()
        or not str(authorization.get("authorized_at", "")).strip()
        or authorization.get("authorization_scope_sha256") != sha256_json(scope)
    ):
        raise ValueError("packing sufficiency screen is not explicitly authorized")
    code = manifest["code"]
    expected_code = {
        "screen_runner_sha256": Path(__file__).resolve(),
        "reader_client_sha256": ROOT / "scripts/run_reader.py",
        "provider_attempt_journal_sha256": ROOT / "scripts/provider_attempts.py",
    }
    for field, path in expected_code.items():
        if code.get(field) != sha256_file(path):
            raise ValueError(f"authorization code hash mismatch for {field}")
    expected = {
        "evidence_sha256": sha256_file(Path(args.evidence)),
        "dataset_sha256": sha256_file(Path(args.dataset)),
        "output": args.out,
        "compiled_output": args.compiled_out,
        "attempt_ledger": args.attempt_ledger,
        "cache_dir": args.cache_dir,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_calls": args.max_calls,
        "max_provider_attempts": args.max_provider_attempts,
        "max_output_tokens": args.max_output_tokens,
        "max_spend_usd": str(args.max_spend_usd),
        "max_price_prompt_per_million": str(args.max_price_prompt_per_million),
        "max_price_completion_per_million": str(args.max_price_completion_per_million),
        "system_prompt_sha256": sha256_bytes(SYSTEM_PROMPT.encode()),
    }
    for field, value in expected.items():
        if manifest["execution"].get(field) != value:
            raise ValueError(f"authorization mismatch for {field}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--compiled-out", required=True)
    parser.add_argument("--authorization-manifest", required=True)
    parser.add_argument("--attempt-ledger", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--max-provider-attempts", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument("--max-spend-usd", type=Decimal, required=True)
    parser.add_argument("--max-price-prompt-per-million", type=Decimal, required=True)
    parser.add_argument("--max-price-completion-per-million", type=Decimal, required=True)
    args = parser.parse_args()

    evidence_path = Path(args.evidence)
    dataset_path = Path(args.dataset)
    output_path = Path(args.out)
    compiled_path = Path(args.compiled_out)
    attempt_path = Path(args.attempt_ledger)
    cache_dir = Path(args.cache_dir)
    manifest = json.loads(Path(args.authorization_manifest).read_text())
    validate_authorization(manifest, args)
    rows = load_jsonl(evidence_path)
    if len(rows) != args.max_calls:
        raise ValueError("max_calls must equal the frozen evidence row count")
    official_rows = json.loads(dataset_path.read_text())
    dataset = {row["question_id"]: row for row in official_rows}

    fingerprint = sha256_json(manifest["execution"])
    ledger = ProviderAttemptLedger(attempt_path, fingerprint)
    try:
        cli = ReaderCli(
            "openrouter",
            args.model,
            args.model,
            cache_dir,
            args.max_calls,
            reasoning_effort=args.reasoning_effort,
            max_spend_usd=args.max_spend_usd,
            max_price_per_million={
                "prompt": args.max_price_prompt_per_million,
                "completion": args.max_price_completion_per_million,
            },
            max_output_tokens=args.max_output_tokens,
        )
        reported, unsettled = restore_spend_from_attempts(ledger.attempts)
        cli.restore_spend_state(
            reported_spend_usd=reported, unsettled_liability_usd=unsettled
        )
        cli.provider_attempts = len(ledger.attempts)
        cli.set_provider_attempt_hook(ledger.record)
        cli.set_provider_attempt_limit(args.max_provider_attempts)

        completed = {}
        if output_path.exists():
            prior = json.loads(output_path.read_text())
            if prior.get("evidence_sha256") != sha256_file(evidence_path):
                raise ValueError("sufficiency resume evidence hash mismatch")
            completed = {row["input_sha256"]: row for row in prior["decisions"]}
        decisions = []
        for row in rows:
            input_sha = sha256_json(decision_input(row))
            decision = completed.get(input_sha)
            if decision is None:
                prompt = build_prompt(row)
                reply = None
                try:
                    reply = cli.call("packing_sufficiency", SYSTEM_PROMPT, prompt)
                    decision = parse_decision(reply, row)
                except Exception as error:
                    summary, compiled = apply_decisions(
                        rows[: len(decisions)], decisions, dataset
                    )
                    atomic_write_json(
                        output_path,
                        {
                            "schema_version": 1,
                            "status": "REJECTED_INVALID_DECISION",
                            "evidence_sha256": sha256_file(evidence_path),
                            "dataset_sha256": sha256_file(dataset_path),
                            "model": args.model,
                            "reasoning_effort": args.reasoning_effort,
                            "decisions": decisions,
                            "summary": summary,
                            "failure": failure_record(row, error, reply),
                            "paid": {
                                "logical_calls": cli.fresh_calls,
                                "provider_attempts": cli.provider_attempts,
                                "reported_spend_usd": str(cli.reported_spend_usd),
                                "unsettled_liability_usd": str(
                                    cli.unsettled_liability_usd
                                ),
                            },
                        },
                    )
                    atomic_write_jsonl(compiled_path, compiled)
                    raise
                decision["prompt_sha256"] = sha256_bytes(prompt.encode())
                decision["response_sha256"] = sha256_bytes(reply.encode())
            decisions.append(decision)
            summary, compiled = apply_decisions(rows[: len(decisions)], decisions, dataset)
            document = {
                "schema_version": 1,
                "status": "COMPLETE" if len(decisions) == len(rows) else "PARTIAL",
                "evidence_sha256": sha256_file(evidence_path),
                "dataset_sha256": sha256_file(dataset_path),
                "model": args.model,
                "reasoning_effort": args.reasoning_effort,
                "decisions": decisions,
                "summary": summary,
                "paid": {
                    "logical_calls": cli.fresh_calls,
                    "provider_attempts": cli.provider_attempts,
                    "reported_spend_usd": str(cli.reported_spend_usd),
                    "unsettled_liability_usd": str(cli.unsettled_liability_usd),
                },
            }
            atomic_write_json(output_path, document)
            atomic_write_jsonl(compiled_path, compiled)
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
