from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_packing_sufficiency_screen",
    ROOT / "scripts/run_packing_sufficiency_screen.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def evidence_row(*, abstention: bool = False) -> dict:
    return {
        "question_id": "q1",
        "question_type": "multi-session",
        "is_abstention": abstention,
        "question": "What is the exact total?",
        "question_date": None,
        "gold_answer": "secret gold must not enter the prompt",
        "abstained": False,
        "granularity": "session",
        "k": 10,
        "evidence": [
            {"rank": 1, "session_id": "answer", "body": "The first cost is $2."},
            {"rank": 2, "session_id": "other", "body": "The second cost is unknown."},
        ],
    }


def test_prompt_excludes_gold_and_requires_complete_evidence() -> None:
    prompt = module.build_prompt(evidence_row())

    assert "secret gold" not in prompt
    assert "fully and unambiguously" in prompt
    assert "[1] The first cost is $2." in prompt


def test_decision_parser_fails_closed_on_invalid_or_incomplete_output() -> None:
    row = evidence_row()
    valid = module.parse_decision(
        json.dumps(
            {
                "selected_ranks": [1],
                "sufficient": False,
                "negative_transfer_ranks": [2],
                "missing_evidence": ["second exact cost"],
                "reason": "One required value is absent.",
            }
        ),
        row,
    )
    assert valid["selected_ranks"] == [1]
    assert valid["sufficient"] is False

    with pytest.raises(ValueError, match="missing_evidence"):
        module.parse_decision(
            json.dumps(
                {
                    "selected_ranks": [1],
                    "sufficient": False,
                    "negative_transfer_ranks": [],
                    "missing_evidence": [],
                    "reason": "Not enough.",
                }
            ),
            row,
        )
    with pytest.raises(ValueError, match="outside"):
        module.parse_decision(
            json.dumps(
                {
                    "selected_ranks": [3],
                    "sufficient": True,
                    "negative_transfer_ranks": [],
                    "missing_evidence": [],
                    "reason": "Complete.",
                }
            ),
            row,
        )


def test_apply_decisions_scores_support_and_abstention_without_gold_leakage() -> None:
    answerable = evidence_row()
    abstention = evidence_row(abstention=True) | {"question_id": "q2"}
    decisions = [
        {
            "question_id": "q1",
            "selected_ranks": [1],
            "sufficient": True,
            "negative_transfer_ranks": [],
            "missing_evidence": [],
            "reason": "Complete.",
        },
        {
            "question_id": "q2",
            "selected_ranks": [1],
            "sufficient": False,
            "negative_transfer_ranks": [],
            "missing_evidence": ["exact total"],
            "reason": "Partial only.",
        },
    ]
    dataset = {
        "q1": {"answer_session_ids": ["answer"]},
        "q2": {"answer_session_ids": ["answer"]},
    }

    summary, compiled = module.apply_decisions(
        [answerable, abstention], decisions, dataset
    )

    assert summary == {
        "total": 2,
        "scored": 1,
        "scored_hit_at_10": 1,
        "abstention": 1,
        "abstention_correct": 1,
    }
    assert [item["rank"] for item in compiled[0]["evidence"]] == [1]
    assert compiled[1]["abstained"] is True
    assert compiled[1]["evidence"] == []


def test_failure_record_keeps_only_response_hash() -> None:
    raw = '{"selected_ranks":[1],"negative_transfer_ranks":[1]}'
    record = module.failure_record(evidence_row(), ValueError("overlap"), raw)

    assert record["question_id"] == "q1"
    assert record["error_kind"] == "ValueError"
    assert record["error"] == "overlap"
    assert record["response_sha256"] == module.sha256_bytes(raw.encode())
    assert raw not in json.dumps(record)
