#!/usr/bin/env python3
"""Register the packing adapter, then enter the pinned official harness."""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_authorization(
    path: Path,
    campaign_name: str,
    *,
    repo_root: Path,
    context: dict[str, object],
    attempt_ledger: Path,
    max_attempts: int,
    max_spend_usd: str,
    prices: dict[str, object],
    output_caps: dict[str, object],
) -> None:
    packet = json.loads(path.read_text(encoding="utf-8"))
    campaign = packet.get("campaigns", {}).get(campaign_name)
    if not isinstance(campaign, dict):
        raise RuntimeError("paid authorization campaign is missing")
    authorization = campaign.get("authorization")
    scope = {
        key: value
        for key, value in campaign.items()
        if key not in {"status", "authorization"}
    }
    if (
        campaign.get("status") != "AUTHORIZED_FOR_PAID_EXECUTION"
        or not isinstance(authorization, dict)
        or not str(authorization.get("authorized_by", "")).strip()
        or not str(authorization.get("authorized_at", "")).strip()
        or authorization.get("authorization_scope_sha256") != _sha256_json(scope)
    ):
        raise RuntimeError("paid execution has not been explicitly authorized")
    hard = campaign.get("hard_limits", {})
    execution = campaign.get("execution", {})
    models = campaign.get("models", {})
    expected_ledger = (repo_root / execution.get("attempt_ledger", "")).resolve()
    if (
        context not in campaign.get("allowed_contexts", [])
        or attempt_ledger.resolve() != expected_ledger
        or hard.get("max_provider_attempts") != max_attempts
        or str(hard.get("max_spend_usd")) != str(max_spend_usd)
        or models.get("prices_usd_per_million") != prices
        or models.get("max_completion_tokens") != output_caps
    ):
        raise RuntimeError("paid authorization invocation drift")


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--official-dir", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument("--attempt-context-json", required=True)
    parser.add_argument("--max-provider-attempts", type=int, required=True)
    parser.add_argument("--max-spend-usd", required=True)
    parser.add_argument("--model-prices-json", required=True)
    parser.add_argument("--model-output-caps-json", required=True)
    parser.add_argument("--generation-api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--authorization-manifest", type=Path, required=True)
    parser.add_argument("--authorization-campaign", required=True)
    bootstrap_args, harness_args = parser.parse_known_args()
    official_dir = bootstrap_args.official_dir.resolve()
    if not (official_dir / "evaluation/harness.py").is_file():
        raise RuntimeError(f"pinned upstream harness is missing: {official_dir}")
    sys.path.insert(0, str(official_dir))
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "scripts"))

    adapter_path = Path(__file__).with_name("memphant_packing_memory.py")
    spec = importlib.util.spec_from_file_location(
        "longmemeval_v2_memphant_packing_memory", adapter_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load MemPhant packing adapter: {adapter_path}")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    context = json.loads(bootstrap_args.attempt_context_json)
    prices_raw = json.loads(bootstrap_args.model_prices_json)
    output_caps_raw = json.loads(bootstrap_args.model_output_caps_json)
    _validate_authorization(
        bootstrap_args.authorization_manifest,
        bootstrap_args.authorization_campaign,
        repo_root=repo_root,
        context=context,
        attempt_ledger=bootstrap_args.attempt_ledger,
        max_attempts=bootstrap_args.max_provider_attempts,
        max_spend_usd=bootstrap_args.max_spend_usd,
        prices=prices_raw,
        output_caps=output_caps_raw,
    )

    import openai
    from paid_meter import install_bounded_openai_meter
    from provider_attempts import openrouter_generation_lookup

    api_key = os.environ.get(bootstrap_args.generation_api_key_env)
    if not api_key:
        raise RuntimeError(
            f"missing provider key: {bootstrap_args.generation_api_key_env}"
        )
    prices = {
        model: {field: Decimal(str(value)) for field, value in values.items()}
        for model, values in prices_raw.items()
    }
    output_caps = {
        model: int(value)
        for model, value in output_caps_raw.items()
    }
    install_bounded_openai_meter(
        openai,
        bootstrap_args.attempt_ledger,
        context=context,
        ledger_context={"campaign": bootstrap_args.authorization_campaign},
        generation_lookup=openrouter_generation_lookup(api_key),
        max_attempts=bootstrap_args.max_provider_attempts,
        max_spend_usd=Decimal(bootstrap_args.max_spend_usd),
        model_prices=prices,
        model_output_caps=output_caps,
    )

    from evaluation import harness, qa_eval_metrics

    harness.OPENAI_MAX_RETRIES = 0
    qa_eval_metrics.OPENAI_MAX_RETRIES = 0

    sys.argv = ["evaluation.harness", *harness_args]
    harness.main()


if __name__ == "__main__":
    main()
