from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "s1_liveness_gate.py"
sys.path.insert(0, str(ROOT / "scripts"))

import preference_lane_analysis as analysis  # noqa: E402


def arm(path: Path, *, probe_id: str = "p1", include_neither: bool = True) -> None:
    row = {"probe_id": probe_id, "group_id": "g1"}
    if include_neither:
        row["neither_returned"] = False
    path.write_text(json.dumps({
        "lineage": {
            "git_head": "abc",
            "server_bin_sha256": "server",
            "worker_bin_sha256": "worker",
        },
        "source": {"sha256": "corpus"},
        "rows": [row],
        "paid_model_calls": 0,
        "diagnostics": {
            "supersedes_edges": 2,
            "superseded_units": 1,
            "superseded_with_open_transaction": 0,
            "open_subject_key_range_overlaps": 0,
            "remainders_recalled": 0,
            "compilation_verified": {"failed_jobs": 0, "pending_jobs": 0},
            "structured_extractor": {
                "ledger": [{"named": True}],
                "unit": "sentence",
                "threshold": 0.42,
                "targets_proposed": 1,
            },
        },
    }))


def run_gate(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dir", str(tmp_path), "--out",
         str(tmp_path / "gate.json"), "--arm", "u42-sentence", "--arm",
         "t53-sentence"],
        text=True, capture_output=True,
    )


def test_custom_live_pair_records_exact_probe_bank_and_neither_returned(tmp_path: Path) -> None:
    arm(tmp_path / "arm-u42-sentence.json")
    arm(tmp_path / "arm-t53-sentence.json")

    result = run_gate(tmp_path)

    assert result.returncode == 0, result.stderr
    gate = json.loads((tmp_path / "gate.json").read_text())
    assert len(gate["shared"]["probe_bank_sha256"]) == 64
    assert gate["arms"]["u42-sentence"]["neither_returned_rate"] == 0.0


def test_custom_live_pair_rejects_different_probe_banks(tmp_path: Path) -> None:
    arm(tmp_path / "arm-u42-sentence.json")
    arm(tmp_path / "arm-t53-sentence.json", probe_id="p2")

    result = run_gate(tmp_path)

    assert result.returncode == 1
    assert "ARMS DO NOT SHARE probe_bank_sha256" in result.stderr


def test_custom_live_pair_requires_neither_returned_on_every_probe(tmp_path: Path) -> None:
    arm(tmp_path / "arm-u42-sentence.json")
    arm(tmp_path / "arm-t53-sentence.json", include_neither=False)

    result = run_gate(tmp_path)

    assert result.returncode == 1
    assert "missing neither_returned" in result.stderr


def test_analysis_requires_a_passed_liveness_gate_for_the_exact_pair(tmp_path: Path) -> None:
    arm_a = tmp_path / "arm-u42-sentence.json"
    arm_b = tmp_path / "arm-t53-sentence.json"
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({
        "passed": True,
        "arms": {
            "u42-sentence": {"artifact": str(arm_a)},
            "t53-sentence": {"artifact": str(arm_b)},
        },
    }))

    checked = analysis.require_liveness_gate(gate, arm_a, arm_b)

    assert checked["passed"] is True


def test_analysis_rejects_a_liveness_gate_for_another_pair(tmp_path: Path) -> None:
    arm_a = tmp_path / "arm-u42-sentence.json"
    arm_b = tmp_path / "arm-t53-sentence.json"
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({
        "passed": True,
        "arms": {"old": {"artifact": str(tmp_path / "old.json")}},
    }))

    with pytest.raises(SystemExit, match="does not bind the requested arm pair"):
        analysis.require_liveness_gate(gate, arm_a, arm_b)
