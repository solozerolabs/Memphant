#!/usr/bin/env python3
"""Generate ForgetEval proposals through the canonical campaign authority."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from generate_forgeteval_proposals import (
    SYSTEM_PROMPT,
    atomic_write,
    build_prompt,
    parse_proposal,
    sha256_bytes,
    sha256_file,
    sha256_json,
)
from provider_attempts import open_campaign_ledger
from run_reader import ReaderCli, restore_spend_from_attempts


def validate_authorization(manifest: dict, args: argparse.Namespace) -> None:
    scope = {
        key: value
        for key, value in manifest.items()
        if key not in {"schema_version", "status", "authorization"}
    }
    authorization = manifest.get("authorization")
    if (
        manifest.get("status") != "AUTHORIZED_STATE_MEMORY_CAMPAIGN"
        or not isinstance(authorization, dict)
        or authorization.get("authorization_scope_sha256") != sha256_json(scope)
    ):
        raise ValueError("ForgetEval campaign is not canonically authorized")
    execution = manifest.get("execution", {}).get("forgeteval_proposals")
    expected = {
        "input_sha256": sha256_file(args.inputs),
        "output": str(args.out),
        "attempt_ledger": str(args.attempt_ledger),
        "cache_dir": str(args.cache_dir),
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_calls": args.max_calls,
        "max_provider_attempts": args.max_provider_attempts,
        "max_output_tokens": args.max_output_tokens,
        "max_spend_usd": str(args.max_spend_usd),
        "max_price_prompt_per_million": str(args.max_price_prompt_per_million),
        "max_price_completion_per_million": str(
            args.max_price_completion_per_million
        ),
        "system_prompt_sha256": sha256_bytes(SYSTEM_PROMPT.encode()),
    }
    if not isinstance(execution, dict):
        raise ValueError("ForgetEval campaign execution is missing")
    for field, value in expected.items():
        if execution.get(field) != value:
            raise ValueError(f"ForgetEval campaign execution field {field} drift")
    code = manifest.get("code", {})
    if (
        code.get("proposal_generator") != "scripts/run_forgeteval_proposals.py"
        or code.get("proposal_generator_sha256")
        != sha256_file(Path(__file__).resolve())
    ):
        raise ValueError("ForgetEval maintained entrypoint code drift")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--authorization-manifest", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--max-provider-attempts", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument("--max-spend-usd", type=Decimal, required=True)
    parser.add_argument("--max-price-prompt-per-million", type=Decimal, required=True)
    parser.add_argument("--max-price-completion-per-million", type=Decimal, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.authorization_manifest.read_text(encoding="utf-8"))
    validate_authorization(manifest, args)
    document = json.loads(args.inputs.read_text(encoding="utf-8"))
    inputs = document["inputs"]
    if len(inputs) != args.max_calls:
        raise ValueError("max_calls must equal the frozen proposal input count")
    ledger = open_campaign_ledger(
        args.authorization_manifest,
        screen_id="forgeteval-proposals",
        expected_journal_path=args.attempt_ledger,
    )
    try:
        cli = ReaderCli(
            "openrouter",
            args.model,
            args.model,
            args.cache_dir,
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
        cli.set_provider_attempt_ledger(ledger)
        cli.set_provider_attempt_limit(args.max_provider_attempts)
        completed = {}
        if args.out.exists():
            prior = json.loads(args.out.read_text(encoding="utf-8"))
            if prior.get("input_sha256") != sha256_file(args.inputs):
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
            atomic_write(args.out, {
                "schema_version": 1,
                "status": "AWAITING_EXPLICIT_CONFIRMATION",
                "input_sha256": sha256_file(args.inputs),
                "model": args.model,
                "reasoning_effort": args.reasoning_effort,
                "proposals": proposals,
                "paid": {
                    "logical_calls": cli.fresh_calls,
                    "provider_attempts": cli.provider_attempts,
                    "reported_spend_usd": str(cli.reported_spend_usd),
                    "unsettled_liability_usd": str(cli.unsettled_liability_usd),
                },
            })
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
