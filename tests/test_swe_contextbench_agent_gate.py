from __future__ import annotations

import base64
import importlib.util
import hashlib
import json
from pathlib import Path, PurePosixPath
import zlib

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
EVIDENCE_BUNDLE = (
    ROOT
    / "docs/build-log/artifacts/next-evidence/coding/swe-contextbench-first-tranche-evidence.bundle.json"
)
COMBINED_PACKET = ROOT / "docs/build-log/artifacts/next-evidence/authorization-request.json"


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


def test_committed_closed_authorization_cannot_execute_again():
    runner = _load()
    packet = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    assert packet["authorization"] is None
    assert packet["status"].startswith("COMPLETED_REJECTED_")
    with pytest.raises(RuntimeError, match="not authorized"):
        runner.validate_authorization(
            packet,
            manifest_sha256=packet["inputs"]["manifest_sha256"],
            rehearsal_sha256=packet["inputs"]["rehearsal_sha256"],
            codex_version=packet["agent"]["cli_version"],
        )
    assert "--authorization" not in runner.build_parser().format_help()


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


@pytest.mark.parametrize(
    "path",
    [
        "spec/core_spec.rb",
        "specs/core.rb",
        "pkg/__tests__/core.js",
        "pkg/core.test.js",
        "pkg/core.spec.ts",
        "pkg/core_test.go",
        "pkg/core_spec.rb",
        "pkg/conftest.py",
        "pytest.ini",
        "tox.ini",
        "jest.config.js",
        "vitest.config.ts",
    ],
)
def test_patch_policy_rejects_cross_language_test_conventions(path):
    runner = _load()
    patch = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    with pytest.raises(RuntimeError, match="test path"):
        runner.validate_model_patch(patch, target_patch_sha256="f" * 64)


def test_patch_policy_parses_quoted_diff_paths_directly():
    runner = _load()
    patch = (
        'diff --git "a/pkg/check spec.test.js" "b/pkg/check spec.test.js"\n'
        '--- "a/pkg/check spec.test.js"\n'
        '+++ "b/pkg/check spec.test.js"\n'
    )
    with pytest.raises(RuntimeError, match="test path"):
        runner.validate_model_patch(patch, target_patch_sha256="f" * 64)


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

    assert result["execution"]["evidence_bundle_sha256"] == runner.sha256_file(
        EVIDENCE_BUNDLE
    )
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


def _decode_evidence_bundle() -> dict[str, bytes]:
    bundle = json.loads(EVIDENCE_BUNDLE.read_text(encoding="utf-8"))
    assert bundle["schema_version"] == 1
    assert bundle["compression"] == "zlib+base64-per-entry"
    assert bundle["entry_count"] == 6 == len(bundle["entries"])
    assert sum(entry["decoded_bytes"] for entry in bundle["entries"]) < 256 * 1024
    decoded: dict[str, bytes] = {}
    total_decoded = 0
    for entry in bundle["entries"]:
        path = entry["path"]
        pure = PurePosixPath(path)
        assert path == pure.as_posix()
        assert not pure.is_absolute()
        assert ".." not in pure.parts
        assert path not in decoded
        expected_size = entry["decoded_bytes"]
        assert 0 <= expected_size <= 16 * 1024 * 1024
        compressed = base64.b64decode(entry["zlib_base64"], validate=True)
        inflater = zlib.decompressobj()
        data = inflater.decompress(compressed, expected_size + 1)
        assert inflater.eof
        assert not inflater.unconsumed_tail
        assert not inflater.unused_data
        assert len(data) == expected_size
        assert hashlib.sha256(data).hexdigest() == entry["decoded_sha256"]
        decoded[path] = data
        total_decoded += len(data)
    assert total_decoded < 256 * 1024
    return decoded


def test_evidence_bundle_preserves_every_hash_preimage_and_usage_total():
    runner = _load()
    evidence = _decode_evidence_bundle()
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    manifest = json.loads(
        (ROOT / "benchmarks/manifests/swe_contextbench.kill.n12.json").read_text(
            encoding="utf-8"
        )
    )
    reference_hashes = {
        row["target_id"]: row["target_patch_sha256"] for row in manifest["cases"]
    }
    executed_authorization = evidence[result["executed_authorization_bundle_entry"]]
    assert hashlib.sha256(executed_authorization).hexdigest() == result[
        "executed_authorization_sha256"
    ]
    assert hashlib.sha256(
        evidence["executed/run_swe_contextbench_agent_gate.py"]
    ).hexdigest() == result["execution"]["executed_runner_sha256"]

    ledger = evidence["execution/attempts.jsonl"]
    assert hashlib.sha256(ledger).hexdigest() == result["execution"]["attempt_ledger_sha256"]
    attempts = [json.loads(line) for line in ledger.splitlines()]
    assert len(attempts) == 12
    assert len({(row["target_id"], row["arm"]) for row in attempts}) == 12
    assert sum(row["status"] == "COMPLETED" for row in attempts) == result["execution"][
        "completed_calls"
    ]
    assert sum(row["status"] != "COMPLETED" for row in attempts) == result["execution"][
        "failed_calls"
    ]
    assert round(sum(row["duration_seconds"] for row in attempts), 3) == result[
        "execution"
    ]["total_duration_seconds"]
    assert sum(row["memory_receipt_sha256"] is not None for row in attempts) == 8
    assert sum(row["memory_trace_id"] is not None for row in attempts) == 8

    prediction_rows = {
        (row["target_id"], row["arm"]): row
        for row in json.loads(evidence["execution/prediction-projections.json"])
    }
    usage_rows = {
        (row["target_id"], row["arm"]): row
        for row in json.loads(evidence["execution/usage-projections.json"])
    }
    assert len(prediction_rows) == len(usage_rows) == 12
    usage = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    for row in attempts:
        key = (row["target_id"], row["arm"])
        usage_projection = usage_rows[key]
        assert usage_projection["raw_output_sha256"] == row["raw_output_sha256"]
        for metric_key in usage:
            usage[metric_key] += usage_projection["usage"][metric_key]
        prediction = prediction_rows[(row["target_id"], row["arm"])]
        assert prediction["model_patch_sha256"] == row["model_patch_sha256"]
        assert prediction["model_patch_bytes"] == row["model_patch_bytes"]
        changed_paths = prediction["changed_paths"]
        assert changed_paths
        assert changed_paths == sorted(set(changed_paths))
        expected_test_violations = sum(runner.is_test_path(path) for path in changed_paths)
        assert prediction["test_path_violations"] == expected_test_violations == 0
        expected_reference_match = (
            prediction["model_patch_sha256"] == reference_hashes[row["target_id"]]
        )
        assert prediction["reference_patch_sha256_match"] is expected_reference_match
        assert expected_reference_match is False
    assert usage == {key: result["codex"][key] for key in usage}

    grade_rows = {
        row["instance_id"]: row
        for row in json.loads(evidence["grading/report-projections.json"])
    }
    for grade in result["official_partial_evaluation"]:
        projection = grade_rows[grade["instance_id"]]
        assert projection == {
            "instance_id": grade["instance_id"],
            "report_sha256": grade["report_sha256"],
            "resolved": grade["resolved"],
            "patch_applied": grade["patch_applied"],
            "fail_to_pass_passed": grade["fail_to_pass_passed"],
            "fail_to_pass_failed": grade["fail_to_pass_failed"],
            "pass_to_pass_passed": grade["pass_to_pass_passed"],
            "pass_to_pass_failed": grade["pass_to_pass_failed"],
        }


def test_parent_packet_binds_closed_child_result_and_evidence_bundle():
    runner = _load()
    parent = json.loads(COMBINED_PACKET.read_text(encoding="utf-8"))
    campaign = parent["campaigns"]["swe_contextbench"]
    assert campaign["authorization"] is None
    assert campaign["authoritative_child_packet_sha256"] == runner.sha256_file(
        AUTHORIZATION
    )
    assert campaign["result_sha256"] == runner.sha256_file(RESULT)
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    assert result["execution"]["evidence_bundle_sha256"] == runner.sha256_file(
        EVIDENCE_BUNDLE
    )
