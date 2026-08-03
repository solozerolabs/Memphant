from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_horizonbench.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_horizonbench", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_row(index: int) -> dict:
    options = [
        {"letter": letter, "value": f"value-{letter}", "option": f"response {letter}"}
        for letter in "ABCDE"
    ]
    return {
        "id": f"item-{index}",
        "generator": "fixture-generator",
        "user_id": f"user-{index}",
        "conversation": (
            "Conversation History:\n"
            "Date: 2026-01-01T01:02:03\n"
            "Scenario: initial preference\n"
            "User: I prefer careful answers.\n"
            "continued thought\n"
            "Assistant: Understood.\n"
            "Date: 2026-02-01T01:02:03\n"
            "Scenario: later event\n"
            "User: My situation changed.\n"
            "Assistant: Tell me more."
        ),
        "options": json.dumps(options),
        "correct_letter": "gold-correct-sentinel",
        "distractor_letter": "gold-distractor-sentinel",
        "has_evolved": "gold-evolved-sentinel",
        "preference_domain": "gold-domain-sentinel",
        "preference_evolution": "gold-evolution-sentinel",
    }


def test_options_and_conversations_follow_official_shape() -> None:
    module = load_module()
    row = sample_row(0)

    options = module.parse_options(row["options"])
    sessions = module.parse_sessions(row["conversation"])

    assert [option["letter"] for option in options] == list("ABCDE")
    assert len(sessions) == 2
    assert sessions[0]["date"] == "2026-01-01T01:02:03"
    assert sessions[0]["scenario"] == "initial preference"
    assert sessions[0]["turns"][0] == {
        "role": "user",
        "content": "I prefer careful answers.\ncontinued thought",
    }


def test_prompt_item_quarantines_every_scoring_field_and_value() -> None:
    module = load_module()
    row = sample_row(0)

    prompt = module.prompt_item(row)
    encoded = json.dumps(prompt, sort_keys=True)

    assert set(prompt) == {"id", "user_id", "generator", "conversation", "options"}
    for field in module.SCORING_ONLY_FIELDS:
        assert field not in encoded
        assert str(row[field]) not in encoded


def test_sample_census_requires_ten_unique_rows_and_users() -> None:
    module = load_module()
    rows = [sample_row(index) for index in range(10)]

    census = module.validate_sample_rows(rows)

    assert census["row_count"] == 10
    assert census["user_count"] == 10
    assert census["expected_ids"] == [f"item-{index}" for index in range(10)]

    with pytest.raises(ValueError, match="exactly 10"):
        module.validate_sample_rows(rows[:-1])
    duplicate = [*rows[:-1], dict(rows[0])]
    with pytest.raises(ValueError, match="duplicate id"):
        module.validate_sample_rows(duplicate)


def test_canonical_jsonl_is_stable_and_ends_with_newline() -> None:
    module = load_module()
    rows = [sample_row(index) for index in range(10)]

    first = module.canonical_jsonl_bytes(rows)
    second = module.canonical_jsonl_bytes(list(rows))

    assert first == second
    assert first.endswith(b"\n")
    assert len([line for line in first.split(b"\n") if line]) == 10


def test_source_revision_must_match_pin() -> None:
    module = load_module()

    module.validate_source_revision(module.DATASET_REVISION)
    with pytest.raises(ValueError, match="revision drift"):
        module.validate_source_revision("0" * 40)


def test_runtime_inputs_are_item_isolated_chronological_and_gold_blind() -> None:
    module = load_module()
    row = sample_row(3)

    runtime = module.runtime_item(row)

    assert runtime["context_ref"] == "horizon-item-3"
    assert len(runtime["episodes"]) == 2
    assert [episode["source_ref"] for episode in runtime["episodes"]] == [
        "horizon:item-3:session:0",
        "horizon:item-3:session:1",
    ]
    observed = [datetime.fromisoformat(episode["observed_at"].replace("Z", "+00:00")) for episode in runtime["episodes"]]
    assert observed == sorted(observed)
    assert all(len(episode["body"].encode()) <= module.MAX_EPISODE_BYTES for episode in runtime["episodes"])
    encoded = json.dumps(runtime, sort_keys=True)
    for field in module.SCORING_ONLY_FIELDS:
        assert field not in encoded
        assert str(row[field]) not in encoded
    for letter in "ABCDE":
        assert f"response {letter}" in runtime["question"]


def test_evidence_completion_rejects_missing_duplicate_degraded_and_empty() -> None:
    module = load_module()
    expected = ["one", "two"]
    rows = [
        {"id": "one", "arm": "fast", "degraded": False, "evidence": [{"body": "a"}]},
        {"id": "two", "arm": "fast", "degraded": False, "evidence": [{"body": "b"}]},
    ]

    module.validate_evidence_rows(rows, expected, "fast")
    with pytest.raises(ValueError, match="expected IDs"):
        module.validate_evidence_rows(rows[:-1], expected, "fast")
    with pytest.raises(ValueError, match="duplicate"):
        module.validate_evidence_rows([rows[0], rows[0]], expected, "fast")
    with pytest.raises(ValueError, match="degraded"):
        module.validate_evidence_rows([{**rows[0], "degraded": True}, rows[1]], expected, "fast")
    with pytest.raises(ValueError, match="empty evidence"):
        module.validate_evidence_rows([{**rows[0], "evidence": []}, rows[1]], expected, "fast")


def test_runtime_calls_public_retain_then_gold_blind_recall() -> None:
    module = load_module()
    row = sample_row(0)

    class Client:
        def __init__(self):
            self.posts = []

        def bind_context(self, client_ref, **kwargs):
            assert client_ref == "horizon-item-0"
            assert kwargs["subject_ref"] == "horizon-item-0"
            return {
                "subject_id": "subject",
                "scope_id": "scope",
                "actor_id": "actor",
                "agent_node_id": "agent",
                "subject_generation": 1,
            }

        def post(self, path, payload):
            self.posts.append((path, payload))
            if path == "/v1/episodes":
                return {"episode_id": f"episode-{len(self.posts)}", "unit_ids": []}
            assert path == "/v1/recall"
            return {
                "items": [{"unit_id": "unit", "body": "relevant memory", "kind": "episodic"}],
                "degraded": False,
                "trace_id": "trace",
            }

    client = Client()
    items = [module.runtime_item(row)]
    bound, retained = module.retain_runtime_items(client, items)
    evidence = module.recall_runtime_items(client, items, bound, "fast", 20, 16384)

    assert retained == 2
    assert [path for path, _ in client.posts] == ["/v1/episodes", "/v1/episodes", "/v1/recall"]
    assert evidence[0]["trace_id"] == "trace"
    assert evidence[0]["evidence"][0]["body"] == "relevant memory"
    serialized_calls = json.dumps(client.posts, sort_keys=True)
    for field in module.SCORING_ONLY_FIELDS:
        assert field not in serialized_calls
        assert str(row[field]) not in serialized_calls
