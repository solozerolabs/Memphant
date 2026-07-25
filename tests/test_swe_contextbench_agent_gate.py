from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_swe_contextbench_agent_gate.py"
AUTHORIZATION = (
    ROOT
    / "docs/build-log/artifacts/next-evidence/coding/swe-contextbench-authorization.json"
)
RESULT = (
    ROOT
    / "docs/build-log/artifacts/next-evidence/coding/swe-contextbench-first-tranche-result.json"
)


def _load():
    spec = importlib.util.spec_from_file_location("swe_context_agent_gate", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _authorization(**overrides):
    packet = {
        "schema_version": 1,
        "campaign": "swe_contextbench_codex_n12",
        "status": "AUTHORIZED_FIRST_TRANCHE",
        "authorization": {
            "authorized_by": "repository_owner",
            "scope": "local benchmark execution only",
        },
        "inputs": {
            "manifest_sha256": "a" * 64,
            "rehearsal_sha256": "b" * 64,
            "official_code_commit": "c" * 40,
        },
        "agent": {
            "cli": "codex",
            "cli_version": "codex-cli 0.145.0-alpha.24",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "sandbox": "workspace-write",
            "timeout_seconds": 900,
            "retries": 0,
        },
        "tranche": {
            "target_ids": ["t1", "t2", "t3", "t4"],
            "arms": [
                "no_memory",
                "unrelated_memory",
                "related_memphant_memory",
            ],
            "max_task_runs": 12,
        },
        "continuation": {
            "minimum_related_gain_over_no_memory": 2,
            "require_zero_unrelated_gain": True,
            "require_zero_unsafe_reuse": True,
            "require_zero_invalid_receipts": True,
        },
    }
    packet.update(overrides)
    return packet


def test_authorization_requires_exact_small_tranche_and_no_retries():
    runner = _load()
    packet = _authorization()

    runner.validate_authorization(
        packet,
        manifest_sha256="a" * 64,
        rehearsal_sha256="b" * 64,
        codex_version="codex-cli 0.145.0-alpha.24",
    )

    packet["agent"]["retries"] = 1
    with pytest.raises(RuntimeError, match="retries"):
        runner.validate_authorization(
            packet,
            manifest_sha256="a" * 64,
            rehearsal_sha256="b" * 64,
            codex_version="codex-cli 0.145.0-alpha.24",
        )


def test_prompt_is_solution_blind_and_only_treatment_receives_memory():
    runner = _load()
    target = {
        "instance_id": "repo__repo-2",
        "repo": "repo/repo",
        "base_commit": "d" * 40,
        "problem_statement": "Fix the current bug",
    }
    proof = {
        "trace_id": "trace-1",
        "receipt_sha256": "e" * 64,
        "returned_unit_ids": ["unit-1"],
    }

    baseline = runner.build_agent_prompt(target, arm="no_memory")
    treatment = runner.build_agent_prompt(
        target,
        arm="related_memphant_memory",
        memory_body="Prior root cause: stale alias resolution.",
        memory_proof=proof,
    )

    assert "Prior root cause" not in baseline
    assert "Prior root cause: stale alias resolution." in treatment
    assert "trace-1" in treatment
    assert "Use this prior experience critically" in treatment
    assert "target patch" not in treatment.lower()
    assert "hidden test" not in treatment.lower()


def test_codex_command_is_ephemeral_sandboxed_and_frozen():
    runner = _load()
    command = runner.codex_command(
        codex_bin="/usr/local/bin/codex",
        worktree=Path("/tmp/task"),
        output_message=Path("/tmp/last.txt"),
        model="gpt-5.6-sol",
        reasoning_effort="medium",
    )

    assert command[:2] == ["/usr/local/bin/codex", "exec"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert 'model_reasoning_effort="medium"' in command


def test_patch_policy_rejects_tests_and_target_solution_identity():
    runner = _load()
    safe = "diff --git a/pkg/core.py b/pkg/core.py\n--- a/pkg/core.py\n+++ b/pkg/core.py\n@@ -1 +1 @@\n-a=1\n+a=2\n"
    runner.validate_model_patch(safe, target_patch_sha256="f" * 64)

    test_patch = "diff --git a/tests/test_core.py b/tests/test_core.py\n--- a/tests/test_core.py\n+++ b/tests/test_core.py\n"
    with pytest.raises(RuntimeError, match="test path"):
        runner.validate_model_patch(test_patch, target_patch_sha256="f" * 64)

    with pytest.raises(RuntimeError, match="reference solution"):
        runner.validate_model_patch(
            safe,
            target_patch_sha256=runner.sha256_text(safe),
        )


def test_continuation_gate_is_paired_and_binding():
    runner = _load()
    passing = {
        "t1": {"no_memory": False, "unrelated_memory": False, "related_memphant_memory": True},
        "t2": {"no_memory": False, "unrelated_memory": False, "related_memphant_memory": True},
        "t3": {"no_memory": True, "unrelated_memory": True, "related_memphant_memory": True},
        "t4": {"no_memory": False, "unrelated_memory": False, "related_memphant_memory": False},
    }
    verdict = runner.continuation_verdict(
        passing,
        unsafe_reuse=0,
        invalid_receipts=0,
    )
    assert verdict["continue"] is True
    assert verdict["related_gain_over_no_memory"] == 2
    assert verdict["unrelated_gain_over_no_memory"] == 0

    passing["t4"]["unrelated_memory"] = True
    assert runner.continuation_verdict(
        passing,
        unsafe_reuse=0,
        invalid_receipts=0,
    )["continue"] is False


def test_early_stop_when_baseline_ceiling_makes_gain_impossible():
    runner = _load()

    verdict = runner.baseline_ceiling_verdict(
        {
            "t1": True,
            "t2": True,
            "t3": True,
        },
        total_targets=4,
        required_related_gain=2,
    )

    assert verdict == {
        "stop": True,
        "graded_baselines": 3,
        "resolved_baselines": 3,
        "ungraded_baselines": 1,
        "maximum_possible_related_gain": 1,
        "required_related_gain": 2,
    }


def test_authorization_artifact_is_canonical_json():
    runner = _load()
    packet = _authorization()
    assert json.loads(runner.canonical_json(packet)) == packet


def test_committed_first_tranche_result_binds_rejection_and_inputs():
    runner = _load()
    result = json.loads(RESULT.read_text(encoding="utf-8"))

    assert result["authorization_sha256"] == runner.sha256_file(AUTHORIZATION)
    assert result["codex"]["task_calls"] == 12
    assert result["execution"]["completed_calls"] == 12
    assert result["execution"]["failed_calls"] == 0
    assert result["decision"] == {
        "graded_baselines": 3,
        "maximum_possible_related_gain": 1,
        "remaining_task_calls_executed": 0,
        "required_related_gain": 2,
        "resolved_baselines": 3,
        "result": "REJECTED_STOP_NO_BROADENING",
        "ungraded_baselines": 1,
        "ungraded_generated_patches": 9,
    }
    assert all(item["resolved"] for item in result["official_partial_evaluation"])
    assert sum(
        item["fail_to_pass_failed"] + item["pass_to_pass_failed"]
        for item in result["official_partial_evaluation"]
    ) == 0
