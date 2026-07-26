#!/usr/bin/env python3
"""Meter the unchanged pinned STALE scorer, then execute it in-process."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import runpy
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from provider_attempts import (  # noqa: E402
    ProviderAttemptLedger,
    install_openai_meter,
    openrouter_generation_lookup,
)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--official-repo", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    known, remaining = parser.parse_known_args()
    entrypoint = (
        known.official_repo.resolve()
        / "STALE"
        / "Evaluation"
        / "full_eval_performance.py"
    )
    for path in (entrypoint.parent, known.official_repo.resolve() / "STALE"):
        sys.path.insert(0, str(path))
    import openai

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    lookup = openrouter_generation_lookup(api_key) if api_key else None
    ledger = ProviderAttemptLedger(
        known.attempt_ledger.resolve(),
        hashlib.sha256(
            (ROOT / "benchmarks/manifests/stale.lock.json").read_bytes()
        ).hexdigest(),
        "stale-native-judge",
        200_000_000_000,
        4_258_002_400,
    )
    install_openai_meter(
        openai,
        ledger,
        max_liability_nanos=10_000_000_000,
        context={"benchmark": "STALE", "arm": "judge"},
        generation_lookup=lookup,
        max_output_tokens=known.max_output_tokens,
    )
    sys.argv = [str(entrypoint), *remaining]
    runpy.run_path(str(entrypoint), run_name="__main__")


if __name__ == "__main__":
    main()
