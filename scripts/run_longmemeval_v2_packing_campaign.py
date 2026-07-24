#!/usr/bin/env python3
"""Plan or execute the frozen 10-cell LongMemEval-V2 packing kill gate."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_runtime as gr  # noqa: E402
from analyze_longmemeval_v2_packing_campaign import analyze  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PACKING_RUNNER = ROOT / "scripts/run_longmemeval_v2_packing.py"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
READER_MODEL = "qwen/qwen3.5-9b"
JUDGE_MODEL = "openai/gpt-5.2"
PRICES = {
    READER_MODEL: {"prompt": "0.17", "completion": "0.25"},
    JUDGE_MODEL: {"prompt": "1.75", "completion": "14.00"},
}
OUTPUT_CAPS = {READER_MODEL: 1024, JUDGE_MODEL: 1024}
ARM_CONFIGS = {
    "no_retrieval": None,
    "memphant_current": "memphant.packing-current.memory.json",
    "memphant_cap1200": "memphant.packing-cap1200.memory.json",
    "memphant_cap1200_submodular": "memphant.packing-cap1200-submodular.memory.json",
    "memphant_order_swapped": "memphant.packing-order-swapped.memory.json",
}
ARM_SERVER = {
    "memphant_current": {},
    "memphant_cap1200": {"pack_render_cap": 1200},
    "memphant_cap1200_submodular": {
        "pack_render_cap": 1200,
        "pack_submodular_ordering": True,
    },
    "memphant_order_swapped": {
        "pack_render_cap": 1200,
        "pack_submodular_ordering": True,
    },
}


def _load_runner():
    spec = importlib.util.spec_from_file_location("lme_packing_runner", PACKING_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("packing runner is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def campaign_cells() -> list[dict[str, str]]:
    return [
        {"arm": arm, "domain": domain}
        for arm in ARM_CONFIGS
        for domain in ("enterprise", "web")
    ]


def command_for(
    args: argparse.Namespace,
    *,
    arm: str,
    domain: str,
) -> list[str]:
    runner = _load_runner()
    config_name = ARM_CONFIGS[arm]
    config_path = (
        args.official_dir / "evaluation/memory_configs/no_retrieval.json"
        if config_name is None
        else ROOT / "benchmarks/longmemeval_v2" / config_name
    )
    context = json.dumps({"arm": arm, "domain": domain}, sort_keys=True, separators=(",", ":"))
    return runner.packing_harness_command(
        official_dir=args.official_dir,
        domain=domain,
        questions_path=args.slice_root / domain / "questions.n6.jsonl",
        haystack_path=args.slice_root / domain / "haystack.n6.json",
        trajectories_path=args.slice_root / "trajectories.n12.jsonl",
        memory_config_path=config_path,
        output_dir=args.artifact_root / "runs" / arm / domain,
        reader_model=READER_MODEL,
        reader_base_url=OPENROUTER_URL,
        evaluator_model=JUDGE_MODEL,
        evaluator_base_url=OPENROUTER_URL,
        attempt_ledger=args.attempt_ledger,
        attempt_context_json=context,
        max_provider_attempts=90,
        max_spend_usd="8.00",
        model_prices_json=json.dumps(PRICES, sort_keys=True, separators=(",", ":")),
        model_output_caps_json=json.dumps(OUTPUT_CAPS, sort_keys=True, separators=(",", ":")),
        authorization_manifest=args.authorization_manifest,
        authorization_campaign="packing",
        python=sys.executable,
    )


def validate_inputs(args: argparse.Namespace) -> None:
    required = [
        args.official_dir / "evaluation/harness.py",
        args.slice_root / "slice-proof.json",
        args.slice_root / "trajectories.n12.jsonl",
        args.authorization_manifest,
    ]
    for domain in ("enterprise", "web"):
        required.extend(
            [
                args.slice_root / domain / "questions.n6.jsonl",
                args.slice_root / domain / "haystack.n6.json",
            ]
        )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"packing campaign input is missing: {missing}")
    if len(campaign_cells()) != 10:
        raise RuntimeError("packing campaign matrix drift")


def execute(args: argparse.Namespace) -> None:
    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    base_env = dict(os.environ)
    base_env.update(
        {
            "MEMPHANT_SCRATCH_ACTIVE": "1",
            "MEMPHANT_TEST_DATABASE_URL": database_url,
            "MEMPHANT_CLI_BIN": str(args.cli_bin),
            "MEMPHANT_LME_SERVER_BIN": str(args.server_bin),
            "MEMPHANT_LME_WORKER_BIN": str(args.worker_bin),
            "MEMPHANT_LME_SERVER_URL": f"http://127.0.0.1:{args.port}",
        }
    )
    construction: dict[str, Path] = {}
    for cell in campaign_cells():
        arm, domain = cell["arm"], cell["domain"]
        output_dir = args.artifact_root / "runs" / arm / domain
        if output_dir.exists():
            raise RuntimeError(f"refusing to overwrite paid output: {output_dir}")
        proof_dir = args.artifact_root / "proof" / arm / domain
        env = dict(base_env)
        env["MEMPHANT_LME_PROOF_DIR"] = str(proof_dir)
        env["MEMPHANT_LME_RUN_ID"] = f"packing-{arm}-{domain}"
        if arm != "no_retrieval" and arm != "memphant_current":
            env["MEMPHANT_LME_PREBUILT_PROOF"] = str(construction[domain])
        else:
            env.pop("MEMPHANT_LME_PREBUILT_PROOF", None)
        command = command_for(args, arm=arm, domain=domain)
        if arm == "no_retrieval":
            subprocess.run(command, cwd=ROOT, env=env, check=True)
            continue
        server = gr.Server(
            str(args.server_bin),
            database_url,
            args.port,
            embed_model="small",
            log_path=args.artifact_root / "logs" / f"{arm}-{domain}.server.log",
            **ARM_SERVER[arm],
        )
        try:
            server.start()
            subprocess.run(command, cwd=ROOT, env=env, check=True)
        finally:
            server.stop()
        if arm == "memphant_current":
            proofs = list(proof_dir.glob("construction.*.json"))
            if len(proofs) != 1:
                raise RuntimeError(f"construction proof count drift: {domain}")
            construction[domain] = proofs[0]
    summary = analyze(args.artifact_root, args.manifest, args.attempt_ledger)
    summary_path = args.artifact_root / "campaign-summary.json"
    if summary_path.exists():
        raise RuntimeError(f"refusing to overwrite paid summary: {summary_path}")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "execute"))
    parser.add_argument("--official-dir", type=Path, required=True)
    parser.add_argument("--slice-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--authorization-manifest", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks/manifests/longmemeval_v2.packing-kill.n12.json")
    parser.add_argument("--database-url", default="postgres://memphant:memphant@localhost:5432/memphant")
    parser.add_argument("--port", type=int, default=39551)
    parser.add_argument("--server-bin", type=Path, default=ROOT / "target/debug/memphant-server")
    parser.add_argument("--worker-bin", type=Path, default=ROOT / "target/debug/memphant-worker")
    parser.add_argument("--cli-bin", type=Path, default=ROOT / "target/debug/memphant-cli")
    args = parser.parse_args()
    for field in ("official_dir", "slice_root", "artifact_root", "authorization_manifest", "attempt_ledger", "manifest", "server_bin", "worker_bin", "cli_bin"):
        setattr(args, field, getattr(args, field).resolve())
    validate_inputs(args)
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "cells": campaign_cells(),
                    "commands": [
                        shlex.join(command_for(args, arm=cell["arm"], domain=cell["domain"]))
                        for cell in campaign_cells()
                    ],
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        return 0
    execute(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
