#!/usr/bin/env python3
"""Generate non-executing ForgetEval mutation proposals under paid hard caps."""

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
    "You propose memory mutations but cannot execute them. Select only from the "
    "numbered evidence candidates. For supersession, start replacement_text with "
    "NEW_FACT exactly as written. Append only clauses whose subject-attribute is "
    "independent of SUPERSEDE_QUERY. Omit every old negation, value, role, reason, "
    "or time qualifier that bears on the targeted attribute. If no independent "
    "clause remains, replacement_text must equal NEW_FACT byte-for-byte. Return "
    "strict JSON without prose or reasoning."
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def build_prompt(value: dict) -> str:
    candidates = "\n".join(
        f"[{row['index']}] {row['body']}" for row in value["candidates"]
    )
    if value["operation"] == "supersede":
        instruction = (
            "Select exactly one existing memory whose subject and attribute are "
            "targeted by SUPERSEDE_QUERY. replacement_text must apply NEW_FACT and "
            "may preserve only a distinct, unaffected subject-attribute clause from "
            "that selected memory. Do not preserve stale target history, copy facts "
            "from other candidates, or invent facts. Return "
            '{"selected_indices":[<one integer>],"replacement_text":"<text>"}.'
        )
        details = (
            f"SUPERSEDE_QUERY: {value['query']}\n"
            f"NEW_FACT: {value['new_text']}\n"
        )
    elif value["operation"] == "release":
        instruction = (
            "Select every memory about the entity or topic explicitly targeted by "
            "RELEASE_REQUEST. Do not select a sibling merely because it shares an "
            "attribute. An empty selection is allowed when evidence is insufficient. "
            'Return {"selected_indices":[<zero or more unique integers>]}.'
        )
        details = f"RELEASE_REQUEST: {value['query']}\n"
    else:
        raise ValueError(f"unsupported proposal operation: {value['operation']}")
    return f"{instruction}\n\n{details}\nCANDIDATES:\n{candidates}"


def parse_proposal(reply: str, value: dict) -> dict:
    try:
        parsed = json.loads(reply)
    except json.JSONDecodeError as error:
        raise ValueError("proposal response is not strict JSON") from error
    expected = (
        {"selected_indices", "replacement_text"}
        if value["operation"] == "supersede"
        else {"selected_indices"}
    )
    if not isinstance(parsed, dict) or set(parsed) != expected:
        raise ValueError("proposal response fields do not match the operation schema")
    selected = parsed["selected_indices"]
    if (
        not isinstance(selected, list)
        or any(type(index) is not int for index in selected)
        or len(selected) != len(set(selected))
    ):
        raise ValueError("selected_indices must be unique integers")
    available = {row["index"] for row in value["candidates"]}
    if any(index not in available for index in selected):
        raise ValueError("proposal selected an index outside the candidate set")
    if value["operation"] == "supersede":
        if len(selected) != 1:
            raise ValueError("supersession proposal must select exactly one candidate")
        replacement = parsed["replacement_text"]
        if not isinstance(replacement, str) or not replacement.strip():
            raise ValueError("supersession replacement_text must be nonempty")
    selected_hashes = [value["candidates"][index]["body_sha256"] for index in selected]
    result = {
        "input_sha256": value["input_sha256"],
        "case_id": value["case_id"],
        "mutation_index": value["mutation_index"],
        "operation": value["operation"],
        "selected_indices": selected,
        "selected_body_sha256": selected_hashes,
        "replacement_text": parsed.get("replacement_text"),
        "confirmed": False,
        "confirmed_by": None,
    }
    result["proposal_sha256"] = sha256_json(result)
    return result


def validate_authorization(
    manifest: dict,
    *,
    input_path: Path,
    output_path: Path,
    attempt_ledger: Path,
    cache_dir: Path,
    model: str,
    reasoning_effort: str,
    max_calls: int,
    max_provider_attempts: int,
    max_output_tokens: int,
    max_spend_usd: Decimal,
    prompt_price: Decimal,
    completion_price: Decimal,
) -> None:
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
        raise ValueError("proposal campaign is not explicitly authorized")
    code = manifest.get("code", {})
    code_files = {
        "proposal_generator_sha256": Path(__file__).resolve(),
        "reader_client_sha256": ROOT / "scripts/run_reader.py",
        "provider_attempt_journal_sha256": ROOT / "scripts/provider_attempts.py",
    }
    for field, path in code_files.items():
        if code.get(field) != sha256_file(path):
            raise ValueError(f"authorization code hash mismatch for {field}")
    if code.get("proposal_generator") != "scripts/generate_forgeteval_proposals.py":
        raise ValueError("authorization proposal generator path mismatch")
    if code.get("proposal_system_prompt_sha256") != sha256_bytes(
        SYSTEM_PROMPT.encode()
    ):
        raise ValueError("authorization proposal prompt hash mismatch")
    expected = {
        "input_sha256": sha256_file(input_path),
        "output": str(output_path),
        "attempt_ledger": str(attempt_ledger),
        "cache_dir": str(cache_dir),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "max_calls": max_calls,
        "max_provider_attempts": max_provider_attempts,
        "max_output_tokens": max_output_tokens,
        "max_spend_usd": str(max_spend_usd),
        "max_price_prompt_per_million": str(prompt_price),
        "max_price_completion_per_million": str(completion_price),
        "system_prompt_sha256": sha256_bytes(SYSTEM_PROMPT.encode()),
    }
    for key, value in expected.items():
        if manifest.get("execution", {}).get(key) != value:
            raise ValueError(f"authorization mismatch for {key}")


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
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

    input_path = Path(args.inputs)
    output_path = Path(args.out)
    attempt_path = Path(args.attempt_ledger)
    cache_dir = Path(args.cache_dir)
    manifest = json.loads(Path(args.authorization_manifest).read_text(encoding="utf-8"))
    validate_authorization(
        manifest,
        input_path=input_path,
        output_path=output_path,
        attempt_ledger=attempt_path,
        cache_dir=cache_dir,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        max_calls=args.max_calls,
        max_provider_attempts=args.max_provider_attempts,
        max_output_tokens=args.max_output_tokens,
        max_spend_usd=args.max_spend_usd,
        prompt_price=args.max_price_prompt_per_million,
        completion_price=args.max_price_completion_per_million,
    )
    document = json.loads(input_path.read_text(encoding="utf-8"))
    inputs = document["inputs"]
    if len(inputs) != args.max_calls:
        raise ValueError("max_calls must equal the frozen proposal input count")

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
            prior = json.loads(output_path.read_text(encoding="utf-8"))
            if prior.get("input_sha256") != sha256_file(input_path):
                raise ValueError("proposal resume input hash mismatch")
            completed = {row["input_sha256"]: row for row in prior["proposals"]}
        proposals = []
        for value in inputs:
            proposal = completed.get(value["input_sha256"])
            if proposal is None:
                prompt = build_prompt(value)
                reply = cli.call(
                    f"forget_{value['operation']}", SYSTEM_PROMPT, prompt
                )
                proposal = parse_proposal(reply, value)
                proposal["prompt_sha256"] = sha256_bytes(prompt.encode())
                proposal["response_sha256"] = sha256_bytes(reply.encode())
            proposals.append(proposal)
            atomic_write(
                output_path,
                {
                    "schema_version": 1,
                    "status": "AWAITING_EXPLICIT_CONFIRMATION",
                    "input_sha256": sha256_file(input_path),
                    "model": args.model,
                    "reasoning_effort": args.reasoning_effort,
                    "proposals": proposals,
                    "paid": {
                        "logical_calls": cli.fresh_calls,
                        "provider_attempts": cli.provider_attempts,
                        "reported_spend_usd": str(cli.reported_spend_usd),
                        "unsettled_liability_usd": str(cli.unsettled_liability_usd),
                    },
                },
            )
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
