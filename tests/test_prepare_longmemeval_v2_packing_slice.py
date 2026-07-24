from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare_longmemeval_v2_packing_slice.py"


def _load():
    spec = importlib.util.spec_from_file_location("lme_slice", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_writes_only_selected_questions_and_trajectories(tmp_path):
    module = _load()
    data = tmp_path / "data"
    (data / "haystacks").mkdir(parents=True)
    rows = [
        {"id": f"e{i}", "domain": "enterprise", "answer": f"gold-e{i}"}
        for i in range(6)
    ] + [
        {"id": f"w{i}", "domain": "web", "answer": f"gold-w{i}"}
        for i in range(6)
    ] + [{"id": "unused", "domain": "web", "answer": "unused"}]
    (data / "questions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    haystacks = {row["id"]: [f"t-{row['id']}"] for row in rows}
    (data / "haystacks/lme_v2_small.json").write_text(json.dumps(haystacks))
    (data / "trajectories.jsonl").write_text("".join(json.dumps({"id": f"t-{row['id']}"}) + "\n" for row in rows))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [{"id": row["id"], "domain": row["domain"]} for row in rows if row["id"] != "unused"]}))

    proof = module.prepare(manifest, data, tmp_path / "out")

    assert proof["question_count"] == 12
    assert proof["trajectory_count"] == 12
    assert "unused" not in (tmp_path / "out/trajectories.n12.jsonl").read_text()
    assert "gold-e0" in (tmp_path / "out/enterprise/questions.n6.jsonl").read_text()
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        module.prepare(manifest, data, tmp_path / "out")
