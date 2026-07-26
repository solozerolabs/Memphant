#!/usr/bin/env python3
"""Register MemPhant, meter calls, then enter the pinned MemSyco harness."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from provider_attempts import (  # noqa: E402
    install_openai_meter,
    open_campaign_ledger_from_env,
    openrouter_generation_lookup_from_env,
)


def install_usage_meter(openai_module, ledger_path: Path) -> None:
    """Install the benchmark-neutral durable meter on all SDK client variants."""
    context = {
        "arm": os.environ.get("MEMPHANT_MEMSYCO_ARM", ""),
        "task": os.environ.get("MEMPHANT_MEMSYCO_TASK", ""),
    }
    ledger = open_campaign_ledger_from_env(
        screen_id=f"memsyco-{context['arm']}-{context['task']}",
        expected_journal_path=ledger_path,
    )
    install_openai_meter(
        openai_module,
        ledger,
        max_liability_nanos=10_000_000_000,
        context=context,
        generation_lookup_factory=openrouter_generation_lookup_from_env,
    )


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load pinned module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def register_memphant(official_dir: Path) -> None:
    """Expose only the pinned baseline contract plus the local MemPhant builder."""
    baseline_dir = official_dir / "baselines"
    package_spec = importlib.util.spec_from_file_location(
        "baselines",
        baseline_dir / "__init__.py",
        submodule_search_locations=[str(baseline_dir)],
    )
    if package_spec is None:
        raise RuntimeError(f"cannot register pinned baseline package: {baseline_dir}")
    package = types.ModuleType("baselines")
    package.__file__ = str(baseline_dir / "__init__.py")
    package.__loader__ = package_spec.loader
    package.__package__ = "baselines"
    package.__spec__ = package_spec
    package.__path__ = [str(baseline_dir)]
    sys.modules["baselines"] = package
    base = _load("baselines.base", baseline_dir / "base.py")
    common = _load("baselines.common", baseline_dir / "common.py")
    config_loader = _load("baselines.config_loader", baseline_dir / "config_loader.py")
    adapter_path = Path(__file__).with_name("memphant_baseline.py")
    adapter = _load("baselines.memphant", adapter_path)

    registry = types.ModuleType("baselines.registry")
    registry.BASELINE_METHODS = ("MemPhant",)

    def build_baseline_context(prior_dialogue, user_question, eval_config, *, sample_key=None):
        if eval_config.method != "MemPhant":
            raise ValueError(f"Unsupported baseline method: {eval_config.method!r}")
        return adapter.build_context(
            prior_dialogue, user_question, eval_config, sample_key=sample_key
        )

    registry.build_baseline_context = build_baseline_context
    sys.modules["baselines.registry"] = registry
    for module in (base, config_loader, registry):
        for name in getattr(module, "__all__", ()):
            setattr(package, name, getattr(module, name))
    package.BaselineContext = base.BaselineContext
    package.BaselineEvalConfig = base.BaselineEvalConfig
    package.BASELINE_METHODS = registry.BASELINE_METHODS
    package.build_baseline_context = registry.build_baseline_context
    package.build_baseline_eval_config = config_loader.build_baseline_eval_config
    package.get_baseline_config_path = config_loader.get_baseline_config_path
    package.common = common


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--official-dir", type=Path, required=True)
    parser.add_argument("--usage-ledger", type=Path, required=True)
    known, remaining = parser.parse_known_args()
    official = known.official_dir.resolve()
    for path in (official, official / "evaluation"):
        sys.path.insert(0, str(path))
    register_memphant(official)
    import openai

    install_usage_meter(openai, known.usage_ledger.resolve())
    from evaluation import run_task

    sys.argv = ["evaluation.run_task", *remaining]
    run_task.main()


if __name__ == "__main__":
    main()
