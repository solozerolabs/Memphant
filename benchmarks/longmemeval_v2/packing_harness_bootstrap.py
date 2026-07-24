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
import subprocess
import sys


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _harness_options(argv: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    index = 0
    while index < len(argv):
        flag = argv[index]
        if not flag.startswith("--") or index + 1 >= len(argv):
            raise RuntimeError("official harness invocation is malformed")
        if flag in options:
            raise RuntimeError(f"official harness option is duplicated: {flag}")
        options[flag] = argv[index + 1]
        index += 2
    return options


def _validate_file_locks(repo_root: Path, campaign: dict[str, object]) -> dict[str, object]:
    frozen = campaign.get("frozen_inputs", {})
    if not isinstance(frozen, dict):
        raise RuntimeError("paid authorization frozen inputs are malformed")
    manifest_path = (repo_root / str(frozen.get("case_manifest", ""))).resolve()
    lock_path = (repo_root / str(frozen.get("adapter_lock", ""))).resolve()
    if (
        _sha256_file(manifest_path) != frozen.get("case_manifest_sha256")
        or _sha256_file(lock_path) != frozen.get("adapter_lock_sha256")
    ):
        raise RuntimeError("paid authorization frozen input hash drift")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    files = lock.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("packing adapter file lock is malformed")
    for relative, expected in files.items():
        path = (repo_root / relative).resolve()
        if repo_root.resolve() not in path.parents or _sha256_file(path) != expected:
            raise RuntimeError(f"packing adapter file hash drift: {relative}")
    release_path = repo_root / "benchmarks/manifests/longmemeval_v2.lock.json"
    if _sha256_file(release_path) != lock.get("upstream_release_lock_sha256"):
        raise RuntimeError("LongMemEval-V2 upstream release lock drift")
    return json.loads(release_path.read_text(encoding="utf-8"))


def _validate_official_checkout(official_dir: Path, release: dict[str, object]) -> None:
    code = release.get("code", {})
    if not isinstance(code, dict):
        raise RuntimeError("LongMemEval-V2 code lock is malformed")
    head = subprocess.run(
        ["git", "-C", str(official_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(official_dir), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != code.get("commit") or status:
        raise RuntimeError("pinned upstream checkout identity drift")
    files = code.get("files", {})
    if not isinstance(files, dict):
        raise RuntimeError("LongMemEval-V2 code file lock is malformed")
    for relative, expected in files.items():
        if _sha256_file(official_dir / relative) != expected:
            raise RuntimeError(f"pinned upstream file hash drift: {relative}")


def _validate_execution_paths(
    campaign: dict[str, object],
    *,
    repo_root: Path,
    official_dir: Path,
    context: dict[str, object],
    harness_args: list[str],
) -> None:
    options = _harness_options(harness_args)
    arm = str(context["arm"])
    domain = str(context["domain"])
    execution = campaign["execution"]
    artifact_root = (repo_root / execution["artifact_root"]).resolve()
    slice_root = artifact_root / "slice"
    expected_config = (
        official_dir / "evaluation/memory_configs/no_retrieval.json"
        if arm == "no_retrieval"
        else repo_root
        / "benchmarks/longmemeval_v2"
        / {
            "memphant_current": "memphant.packing-current.memory.json",
            "memphant_cap1200": "memphant.packing-cap1200.memory.json",
            "memphant_cap1200_submodular": "memphant.packing-cap1200-submodular.memory.json",
            "memphant_order_swapped": "memphant.packing-order-swapped.memory.json",
        }[arm]
    )
    expected = {
        "--domain": domain,
        "--questions-path": str(slice_root / domain / "questions.n6.jsonl"),
        "--haystack-path": str(slice_root / domain / "haystack.n6.json"),
        "--trajectories-path": str(slice_root / "trajectories.n12.jsonl"),
        "--memory-config-path": str(expected_config.resolve()),
        "--output-dir": str(artifact_root / "runs" / arm / domain),
        "--model": "qwen/qwen3.5-9b",
        "--base-url": "https://openrouter.ai/api/v1",
        "--evaluator-model": "openai/gpt-5.2",
        "--evaluator-base-url": "https://openrouter.ai/api/v1",
        "--api-key-env": "OPENROUTER_API_KEY",
        "--evaluator-api-key-env": "OPENROUTER_API_KEY",
        "--max-completion-tokens": "1024",
        "--evaluator-max-completion-tokens": "1024",
        "--reader-max-concurrent-requests": "4",
        "--memory-context-max-tokens": "8192",
    }
    for flag, value in expected.items():
        if options.get(flag) != value:
            raise RuntimeError(f"paid authorization harness path drift: {flag}")
    proof_path = slice_root / "slice-proof.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    manifest_path = repo_root / campaign["frozen_inputs"]["case_manifest"]
    if (
        proof.get("manifest_sha256") != _sha256_file(manifest_path)
        or proof.get("question_count") != 12
    ):
        raise RuntimeError("packing slice proof identity drift")
    outputs = proof.get("outputs", {})
    if not isinstance(outputs, dict):
        raise RuntimeError("packing slice output lock is malformed")
    for relative, expected_hash in outputs.items():
        if _sha256_file(slice_root / relative) != expected_hash:
            raise RuntimeError(f"packing slice output hash drift: {relative}")


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
    official_dir: Path | None = None,
    harness_args: list[str] | None = None,
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
    if official_dir is None or harness_args is None:
        raise RuntimeError("paid authorization execution identity is missing")
    release = _validate_file_locks(repo_root, campaign)
    _validate_official_checkout(official_dir, release)
    _validate_execution_paths(
        campaign,
        repo_root=repo_root,
        official_dir=official_dir,
        context=context,
        harness_args=harness_args,
    )


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
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "scripts"))

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
        official_dir=official_dir,
        harness_args=harness_args,
    )

    sys.path.insert(0, str(official_dir))
    adapter_path = Path(__file__).with_name("memphant_packing_memory.py")
    spec = importlib.util.spec_from_file_location(
        "longmemeval_v2_memphant_packing_memory", adapter_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load MemPhant packing adapter: {adapter_path}")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

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
