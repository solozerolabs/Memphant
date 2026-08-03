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


def test_control_character_normalization_is_lossless_and_postgres_safe() -> None:
    module = load_module()
    source = "Clock\x0027s\x07 ticking\nnext\tline"

    normalized = module.normalize_source_text(source)

    assert normalized == "Clock\\u000027s\\u0007 ticking\nnext\tline"
    assert "\x00" not in normalized
    assert "\x07" not in normalized
    assert module.restore_source_text(normalized) == source


def test_fast_gate_carries_nondecisional_evidence_contract() -> None:
    module = load_module()

    contract = module.fast_gate_evidence_contract(
        source_sha="a" * 64,
        k=20,
        budget_tokens=16384,
    )

    assert contract["schema_version"] == 1
    assert contract["decisional"] is False
    assert contract["power"]["test"] == "descriptive-only (no test)"
    assert contract["power"]["n"] == 10
    assert contract["harness"]["k"] == 20
    assert contract["corpus"]["sha256"] == "a" * 64
    assert contract["instrument_verification"]["rows_counted"] == 10


def test_paid_authorization_is_hash_bound_and_capped_at_25_dollars() -> None:
    module = load_module()
    frozen = {
        "source_jsonl_sha256": "a" * 64,
        "lock_sha256": "b" * 64,
        "fast_evidence_sha256": "c" * 64,
        "fast_gate_sha256": "d" * 64,
        "runner_sha256": "e" * 64,
        "provider_attempts_sha256": "f" * 64,
    }
    packet = module.authorization_packet(
        frozen, authorized_by="owner", authorized_at="2026-08-03T00:00:00Z"
    )

    module.validate_pilot_authorization(packet, frozen)
    assert packet["hard_limits"]["reader"]["max_spend_usd"] == "22"
    assert packet["hard_limits"]["deep"]["max_spend_usd"] == "3"
    assert packet["hard_limits"]["combined_max_spend_usd"] == "25"

    packet["frozen_inputs"]["source_jsonl_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="authorization scope"):
        module.validate_pilot_authorization(packet, frozen)


def test_selective_routing_uses_fast_answer_or_requires_completed_deep() -> None:
    module = load_module()

    assert module.selective_route({"status": "completed", "answer": "B", "abstain": False}) == "fast"
    assert module.selective_route({"status": "completed", "answer": None, "abstain": True}) == "deep"
    module.validate_deep_completion(
        {
            "degraded": False,
            "evidence": [{"body": "current preference"}],
            "deep": {
                "status": "completed",
                "settled_micros": 10,
                "unsettled_micros_upper_bound": 0,
            },
        }
    )
    with pytest.raises(ValueError, match="completed"):
        module.validate_deep_completion(
            {
                "degraded": False,
                "evidence": [{"body": "partial"}],
                "deep": {
                    "status": "partial",
                    "settled_micros": 10,
                    "unsettled_micros_upper_bound": 0,
                },
            }
        )


def test_terminal_rows_reject_duplicates_and_missing_arms() -> None:
    module = load_module()
    rows = [
        {"id": item_id, "arm": arm, "status": "completed"}
        for item_id in ("one", "two")
        for arm in module.PAID_ARMS
    ]

    module.validate_terminal_rows(rows, ["one", "two"])
    with pytest.raises(ValueError, match="duplicate"):
        module.validate_terminal_rows([*rows, rows[0]], ["one", "two"])
    with pytest.raises(ValueError, match="terminal rows"):
        module.validate_terminal_rows(rows[:-1], ["one", "two"])


def test_analysis_joins_gold_last_and_applies_preregistered_verdict() -> None:
    module = load_module()
    source = [sample_row(0), sample_row(1)]
    source[0].update(correct_letter="A", distractor_letter="B", has_evolved=True)
    source[1].update(correct_letter="C", distractor_letter="", has_evolved=False)
    predictions = []
    for item, full, fast in zip(source, ("B", "C"), ("A", "D"), strict=True):
        predictions.extend(
            [
                {"id": item["id"], "arm": "full_context", "status": "completed", "answer": full, "abstain": False},
                {"id": item["id"], "arm": "fast", "status": "completed", "answer": fast, "abstain": False},
                {"id": item["id"], "arm": "selective_deep", "status": "completed", "answer": fast, "abstain": False, "route": "fast"},
            ]
        )

    first = module.analyze_paid_rows(source, predictions, bootstrap_seed=7, bootstrap_samples=1000)
    second = module.analyze_paid_rows(source, predictions, bootstrap_seed=7, bootstrap_samples=1000)

    assert first == second
    assert first["arms"]["full_context"]["correct"] == 1
    assert first["arms"]["selective_deep"]["correct"] == 1
    assert first["arms"]["full_context"]["evolved_distractor_selections"] == 1
    assert first["arms"]["selective_deep"]["evolved_distractor_selections"] == 0
    assert first["paired_selective_vs_full"] == {
        **first["paired_selective_vs_full"],
        "gains": 1,
        "losses": 1,
        "discordant": 2,
    }
    assert first["verdict"]["outcome"] == "advance_to_powered_plan"

    with pytest.raises(ValueError, match="terminal rows"):
        module.analyze_paid_rows(source, predictions[:-1])


def test_pilot_contract_stays_nondecisional_below_mcnemar_floor() -> None:
    module = load_module()
    analysis = {"paired_selective_vs_full": {"gains": 1, "losses": 1, "discordant": 2}}

    contract = module.pilot_evidence_contract("a" * 64, analysis)

    assert contract["decisional"] is False
    assert contract["power"]["n_d"] == 2
    assert contract["power"]["test"] == "two-sided exact (conditional binomial) McNemar"
