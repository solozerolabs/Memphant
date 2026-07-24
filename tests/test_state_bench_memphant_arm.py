from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_state_bench_memphant_arm.py"
AGENT = ROOT / "benchmarks" / "state_bench" / "memphant_memory_agent.py"
MANIFEST = ROOT / "benchmarks" / "manifests" / "state_bench_memphant.v1.json"
FIXTURE = ROOT / "tests" / "fixtures" / "state_bench_learning_small.json"


def load_script():
    spec = importlib.util.spec_from_file_location("build_state_bench_arm", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memphant_arm_lock_freezes_official_protocol_and_runtime() -> None:
    lock = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert lock["protocol"] == "state-bench-memphant-arm-v1"
    assert lock["state_bench_lock_sha256"] == (
        "ee32c340f3e0dd5bcdee7f725fe0e542622f2c1a9a61626ee9c06feb49e075ce"
    )
    assert lock["official"] == {
        "agent_class": "MemphantMemoryAgent",
        "domains": ["travel", "customer_support", "shopping_assistant"],
        "num_runs": 5,
        "split": "test",
        "top_k": 3,
    }
    assert lock["retrieval"] == {
        "budget_tokens": 4096,
        "limit": 3,
        "mode": "deep",
    }
    assert lock["evidence"]["source"] == "official-train-trajectories-only"
    assert set(lock["hashes"]) == {
        "evidence_schema_sha256",
        "official_retrieval_instruction_sha256",
        "runner_input_schema_sha256",
    }


def test_train_mapping_preserves_success_and_failure_without_gold() -> None:
    arm = load_script()
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plans, test_ids = arm.build_fixture_plans(source)

    assert test_ids == {"travel": ["2-test"]}
    assert [plan["attempt_type"] for plan in plans["travel"]] == [
        "tool_attempt.success",
        "tool_attempt.failure",
    ]
    assert [plan["mark_outcome"] for plan in plans["travel"]] == [
        "success",
        "failure",
    ]
    assert plans["travel"][0]["result"]["status"] == "rejected"
    assert plans["travel"][1]["result"]["error"] == "Flight BAD not found."
    assert "Flight BAD not found" not in plans["travel"][1]["recall_query"]
    serialized = json.dumps(plans)
    for forbidden in (
        "DO_NOT_EXPOSE_TRAIN_GOLD",
        "DO_NOT_EXPOSE_TRAIN_STATE",
        "DO_NOT_EXPOSE_TRAIN_SCORE",
        "DO_NOT_EXPOSE_TEST_ANSWERS",
        "DO_NOT_EXPOSE_SCORER_FIELDS",
    ):
        assert forbidden not in serialized


def test_external_attempt_kinds_map_explicitly_and_unknown_values_fail() -> None:
    arm = load_script()
    assert arm.map_attempt_source_kind("tool_attempt.success") == "tool"
    assert arm.map_attempt_source_kind("tool_attempt.failure") == "tool"
    with pytest.raises(ValueError, match="unmapped STATE-Bench attempt_type"):
        arm.map_attempt_source_kind("tool_attempt.mystery")


def test_official_loader_reads_only_train_conversation_and_split_ids(
    tmp_path: Path,
) -> None:
    arm = load_script()
    domain = tmp_path / "state_bench" / "domains" / "travel"
    trajectories = tmp_path / "datasets" / "train_task_trajectories" / "travel"
    trajectories.mkdir(parents=True)
    (domain / "splits").mkdir(parents=True)
    (domain / "tasks").mkdir()
    (domain / "task_envs").mkdir()
    (domain / "splits" / "train_test.json").write_text(
        json.dumps({"splits": {"train": ["1-train"], "test": ["2-test"]}}),
        encoding="utf-8",
    )
    (trajectories / "1-train.json").write_text(
        json.dumps(
            {
                "conversation": [
                    {"role": "user", "content": "Need help"},
                    {
                        "role": "assistant",
                        "content": "Checking",
                        "tool_calls": [
                            {"name": "lookup", "arguments": {}, "result": {"value": 1}}
                        ],
                    },
                ],
                "task_requirements": "DO_NOT_EXPOSE_GOLD",
            }
        ),
        encoding="utf-8",
    )
    (domain / "tasks" / "2-test.json").write_text(
        json.dumps({"answer": "DO_NOT_READ_TEST_TASK"}), encoding="utf-8"
    )
    (domain / "task_envs" / "2-test.json").write_text(
        json.dumps({"database": "DO_NOT_READ_TEST_ENV"}), encoding="utf-8"
    )

    plans, test_ids = arm.load_official_domain(tmp_path, "travel")

    assert test_ids == ["2-test"]
    serialized = json.dumps(plans)
    assert "DO_NOT_EXPOSE_GOLD" not in serialized
    assert "DO_NOT_READ_TEST" not in serialized


def test_runner_inputs_are_exact_official_learning_track_contract() -> None:
    arm = load_script()
    runner = arm.runner_contract("gpt-5.6-sol-pro", "/tmp/outputs")

    assert runner["protocol_id"] == "state_bench_v0.8.0_gpt54"
    assert len(runner["commands"]) == 3
    for command in runner["commands"]:
        assert command.count("--num-runs") == 1
        assert command[command.index("--num-runs") + 1] == "5"
        assert command[command.index("--split") + 1] == "test"
        assert command[command.index("--retrieve-learnings-top-k") + 1] == "3"
        assert command[command.index("--agent-class") + 1] == "MemphantMemoryAgent"
        assert command[command.index("--agent-model-reasoning-level") + 1] == "high"
        assert "--tasks" not in command


def test_agent_injects_only_official_read_only_retrieval() -> None:
    source = AGENT.read_text(encoding="utf-8")

    assert "class MemphantMemoryAgent(StateBenchAgent)" in source
    assert "def retrieve_learnings(self, query: str, top_k: int = 3) -> list[str]" in source
    assert 'if top_k != 3:' in source
    assert '"limit": 3' in source
    assert '"mode": "deep"' in source
    assert "task_summary" not in source
    assert "task_requirements" not in source
    assert "state_requirements" not in source
    assert "/v1/retain" not in source
    assert "/v1/mark" not in source


def test_checkpoint_requires_exact_fingerprint_and_complete_attempt_records() -> None:
    arm = load_script()
    checkpoint = {
        "fingerprint": "frozen",
        "domains": {
            "travel": {
                "attempts": {
                    "a": {"episode_id": "ep", "mark": {"accepted": True}}
                }
            }
        },
    }
    arm.validate_checkpoint(checkpoint, "frozen", {"travel": {"a"}})
    with pytest.raises(ValueError, match="fingerprint"):
        arm.validate_checkpoint(checkpoint, "changed", {"travel": {"a"}})
    with pytest.raises(ValueError, match="attempt IDs"):
        arm.validate_checkpoint(checkpoint, "frozen", {"travel": {"a", "b"}})


def test_complete_checkpoint_requires_episode_reflect_and_matching_mark() -> None:
    arm = load_script()
    plan = {"attempt_id": "a", "mark_outcome": "failure"}
    checkpoint = {
        "domains": {
            "travel": {
                "reflected": {"episodes_consumed": 1},
                "attempts": {
                    "a": {
                        "episode_id": "episode-a",
                        "mark": {
                            "accepted": True,
                            "outcome": "failure",
                            "trace_id": "trace-a",
                            "used_ids_sha256": "0" * 64,
                        },
                    }
                },
            }
        }
    }
    arm.validate_complete_checkpoint(checkpoint, {"travel": [plan]})
    checkpoint["domains"]["travel"]["attempts"]["a"]["mark"]["outcome"] = "success"
    with pytest.raises(ValueError, match="invalid mark proof"):
        arm.validate_complete_checkpoint(checkpoint, {"travel": [plan]})
