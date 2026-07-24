from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/materialize_lme_s_packing_pilot.py"


def _load():
    spec = importlib.util.spec_from_file_location("lme_s_packing_pilot", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_materializer_preserves_frozen_order_and_proves_bytes(tmp_path):
    module = _load()
    source = tmp_path / "source.json"
    rows = [
        {"question_id": "q2", "question": "two"},
        {"question_id": "q1", "question": "one"},
    ]
    source.write_text(json.dumps(rows))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "question_ids": ["q1", "q2"],
            }
        )
    )
    output = tmp_path / "pilot.json"

    proof = module.materialize(source, manifest, output)

    assert [row["question_id"] for row in json.loads(output.read_text())] == [
        "q1",
        "q2",
    ]
    assert proof["question_count"] == 2
    assert proof["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_materializer_fails_closed_on_source_drift(tmp_path):
    module = _load()
    source = tmp_path / "source.json"
    source.write_text("[]")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"source_dataset_sha256": "0" * 64, "question_ids": []})
    )

    with pytest.raises(RuntimeError, match="source drift"):
        module.materialize(source, manifest, tmp_path / "pilot.json")
