#!/usr/bin/env python3
"""Register the packing adapter, then enter the pinned official harness."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--official-dir", type=Path, required=True)
    bootstrap_args, harness_args = parser.parse_known_args()
    official_dir = bootstrap_args.official_dir.resolve()
    if not (official_dir / "evaluation/harness.py").is_file():
        raise RuntimeError(f"pinned upstream harness is missing: {official_dir}")
    sys.path.insert(0, str(official_dir))

    adapter_path = Path(__file__).with_name("memphant_packing_memory.py")
    spec = importlib.util.spec_from_file_location(
        "longmemeval_v2_memphant_packing_memory", adapter_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load MemPhant packing adapter: {adapter_path}")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    from evaluation.harness import main as harness_main

    sys.argv = ["evaluation.harness", *harness_args]
    harness_main()


if __name__ == "__main__":
    main()
