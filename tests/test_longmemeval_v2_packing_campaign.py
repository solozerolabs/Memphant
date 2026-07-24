from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_longmemeval_v2_packing_campaign.py"


def _load():
    spec = importlib.util.spec_from_file_location("lme_packing_campaign", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_campaign_is_exactly_five_arms_by_two_domains():
    module = _load()
    cells = module.campaign_cells()
    assert len(cells) == 10
    assert {cell["arm"] for cell in cells} == set(module.ARM_CONFIGS)
    assert {cell["domain"] for cell in cells} == {"enterprise", "web"}
    assert sum(cell["arm"] == "no_retrieval" for cell in cells) == 2
