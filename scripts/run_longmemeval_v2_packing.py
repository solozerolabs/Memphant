#!/usr/bin/env python3
"""Build a pinned official LongMemEval-V2 command for a packing arm.

The command is printed only. Model execution remains behind the separately
sealed authorization packet and is never implied by this helper.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_RUNNER_PATH = ROOT / "scripts/run_longmemeval_v2.py"
BOOTSTRAP = ROOT / "benchmarks/longmemeval_v2/packing_harness_bootstrap.py"


def _load_base_runner():
    spec = importlib.util.spec_from_file_location("longmemeval_v2_base_runner", BASE_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load base runner: {BASE_RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def packing_harness_command(**kwargs: object) -> list[str]:
    config_path = Path(kwargs["memory_config_path"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    params = config.get("memory_params")
    if config.get("memory_type") != "memphant_packing" or not isinstance(params, dict):
        raise RuntimeError("packing memory config contract drift")
    budget = params.get("reader_context_max_tokens")
    if not isinstance(budget, int) or budget <= 0 or params.get("budget_tokens") != budget:
        raise RuntimeError("packing recall and reader budgets must be equal and positive")
    base = _load_base_runner()
    command = base.native_harness_command(**kwargs)
    command[1] = str(BOOTSTRAP)
    command[2:2] = ["--official-dir", str(Path(kwargs["official_dir"]))]
    budget_index = command.index("--memory-context-max-tokens") + 1
    command[budget_index] = str(budget)
    return command


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-dir", type=Path, required=True)
    parser.add_argument("--domain", choices=["web", "enterprise"], required=True)
    parser.add_argument("--questions-path", type=Path, required=True)
    parser.add_argument("--haystack-path", type=Path, required=True)
    parser.add_argument("--trajectories-path", type=Path, required=True)
    parser.add_argument("--memory-config-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reader-model", required=True)
    parser.add_argument("--reader-base-url", required=True)
    parser.add_argument("--evaluator-model", required=True)
    parser.add_argument("--evaluator-base-url", required=True)
    args = parser.parse_args()
    command = packing_harness_command(
        official_dir=args.official_dir.resolve(),
        domain=args.domain,
        questions_path=args.questions_path.resolve(),
        haystack_path=args.haystack_path.resolve(),
        trajectories_path=args.trajectories_path.resolve(),
        memory_config_path=args.memory_config_path.resolve(),
        output_dir=args.output_dir.resolve(),
        reader_model=args.reader_model,
        reader_base_url=args.reader_base_url,
        evaluator_model=args.evaluator_model,
        evaluator_base_url=args.evaluator_base_url,
        python=sys.executable,
    )
    print(shlex.join(command))


if __name__ == "__main__":
    main()
