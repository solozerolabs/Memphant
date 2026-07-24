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


def test_campaign_commands_bind_both_openrouter_key_consumers(tmp_path):
    module = _load()
    official = tmp_path / "official"
    (official / "evaluation/memory_configs").mkdir(parents=True)
    (official / "evaluation/memory_configs/no_retrieval.json").write_text(
        '{"memory_type":"no_retrieval","memory_params":{}}'
    )
    args = type(
        "Args",
        (),
        {
            "official_dir": official,
            "slice_root": tmp_path / "slice",
            "artifact_root": tmp_path / "artifacts",
            "attempt_ledger": tmp_path / "attempts.jsonl",
            "authorization_manifest": tmp_path / "authorization.json",
        },
    )()
    command = module.command_for(args, arm="no_retrieval", domain="web")
    assert command[command.index("--api-key-env") + 1] == "OPENROUTER_API_KEY"
    assert (
        command[command.index("--evaluator-api-key-env") + 1]
        == "OPENROUTER_API_KEY"
    )
