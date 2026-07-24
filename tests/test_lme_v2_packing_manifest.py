from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_lme_v2_packing_manifest.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_packing_manifest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_validator_proves_case_and_shared_construction_identity(tmp_path):
    module = _load()
    data = tmp_path / "data"
    (data / "haystacks").mkdir(parents=True)
    cases = []
    questions = []
    small = {}
    for index in range(12):
        domain = "enterprise" if index < 6 else "web"
        question = {
            "id": f"q{index}",
            "domain": domain,
            "question_type": "procedure",
            "question": f"question {index}",
            "image": None,
            "answer": "secret",
        }
        questions.append(question)
        haystack = [f"{domain}-trajectory-{item}" for item in range(100)]
        small[question["id"]] = haystack
        cases.append(
            {
                "id": question["id"],
                "domain": domain,
                "question_type": "procedure",
                "question_record_sha256": module._canonical_sha256(question),
                "haystack_ids_sha256": module._canonical_sha256(haystack),
                "image": None,
            }
        )
    (data / "questions.jsonl").write_text(
        "\n".join(json.dumps(row) for row in questions) + "\n"
    )
    (data / "haystacks/lme_v2_small.json").write_text(json.dumps(small))
    (data / "haystacks/lme_v2_medium.json").write_text("{}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_files": {
                    "questions.jsonl": _sha(data / "questions.jsonl"),
                    "haystacks/lme_v2_small.json": _sha(
                        data / "haystacks/lme_v2_small.json"
                    ),
                    "haystacks/lme_v2_medium.json": _sha(
                        data / "haystacks/lme_v2_medium.json"
                    ),
                },
                "cases": cases,
            }
        )
    )

    proof = module.validate(manifest, data)

    assert proof["cases"] == 12
    assert proof["unique_constructions"] == 2
    assert proof["answers_exported"] is False


def test_validator_fails_closed_on_question_drift(tmp_path):
    module = _load()
    data = tmp_path / "data"
    (data / "haystacks").mkdir(parents=True)
    (data / "questions.jsonl").write_text("{}\n")
    (data / "haystacks/lme_v2_small.json").write_text("{}")
    (data / "haystacks/lme_v2_medium.json").write_text("{}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_files": {
                    "questions.jsonl": "0" * 64,
                    "haystacks/lme_v2_small.json": "0" * 64,
                    "haystacks/lme_v2_medium.json": "0" * 64,
                },
                "cases": [],
            }
        )
    )
    with pytest.raises(RuntimeError, match="metadata drift"):
        module.validate(manifest, data)
