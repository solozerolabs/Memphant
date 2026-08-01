from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVOMEM_AUDIT = ROOT / "benchmarks/manifests/evomembench.release-audit.json"

# Extracted from `tests/test_public_benchmark_adapters.py` when the
# LongMemEval-V2 harness was deleted (2026-07-31, one-plan Phase C). This gate
# has nothing to do with LME-V2: it is the standing refusal to acquire or
# integrate EvoMemBench while the upstream repository ships no license.


def test_evomembench_is_fail_closed_until_repo_level_license_exists() -> None:
    audit = json.loads(EVOMEM_AUDIT.read_text(encoding="utf-8"))

    assert audit["code"]["commit"] == "aa4cea8fd936b76b2d3591d3ef897030617dc43a"
    assert audit["public_execution_ready"] is False
    assert audit["blockers"]["repository_license"] == "missing"
    assert audit["decision"] == "do_not_acquire_or_integrate"
