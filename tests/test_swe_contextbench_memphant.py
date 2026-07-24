from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_swe_contextbench_memphant.py"
MANIFEST = ROOT / "benchmarks/manifests/swe_contextbench.kill.n12.json"
OFFICIAL = (
    ROOT / "docs/build-log/artifacts/tri-sota-completion/swe-contextbench/official"
)
REHEARSAL = (
    ROOT
    / "docs/build-log/artifacts/next-evidence/coding/swe-contextbench-n12-rehearsal.json"
)


def _load():
    spec = importlib.util.spec_from_file_location("swe_context_runner", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**overrides):
    row = {
        "instance_id": "repo__repo-1",
        "repo": "repo/repo",
        "base_commit": "a" * 40,
        "problem_statement": "Fix the prior failure",
        "hints_text": "Inspect the parser",
        "patch": "diff --git a/a.py b/a.py\n+fixed = True",
        "test_patch": "TARGET TEST MUST STAY HIDDEN",
        "FAIL_TO_PASS": '["test_prior"]',
        "PASS_TO_PASS": '["test_existing"]',
        "created_at": "2026-01-01T00:00:00Z",
        "version": "1",
    }
    row.update(overrides)
    return row


def test_target_agent_input_is_an_exact_solution_blind_whitelist():
    runner = _load()
    row = _row(patch="SECRET TARGET PATCH", test_patch="SECRET TARGET TEST")

    visible = runner.target_agent_input(row)

    assert set(visible) == runner.TARGET_AGENT_FIELDS
    assert "SECRET TARGET PATCH" not in json.dumps(visible)
    assert "SECRET TARGET TEST" not in json.dumps(visible)


def test_experience_body_includes_prior_outcome_but_not_hidden_test_patch():
    runner = _load()
    body = runner.experience_body(_row())

    assert "Observed successful patch from the prior task" in body
    assert "+fixed = True" in body
    assert "test_prior" in body
    assert "TARGET TEST MUST STAY HIDDEN" not in body
    assert "test_existing" not in body


def test_outcome_mark_is_bound_to_trace_units_target_and_validator_result():
    runner = _load()
    context = {
        "subject_id": "subject",
        "scope_id": "scope",
        "actor_id": "actor",
        "agent_node_id": "agent",
        "subject_generation": 1,
    }
    success = runner.build_mark_payload(
        context,
        trace_id="trace",
        used_ids=["unit"],
        target_id="target",
        resolved=True,
    )
    failure = runner.build_mark_payload(
        context,
        trace_id="trace",
        used_ids=["unit"],
        target_id="target",
        resolved=False,
    )

    assert success["outcome"] == "success"
    assert failure["outcome"] == "failure"
    assert success["caller_id"] == "swe-contextbench:target"
    with pytest.raises(RuntimeError, match="identity is incomplete"):
        runner.build_mark_payload(
            context,
            trace_id="",
            used_ids=["unit"],
            target_id="target",
            resolved=True,
        )


def test_frozen_n12_is_paired_prior_and_answer_blind():
    runner = _load()
    manifest = json.loads(MANIFEST.read_text())
    cases = manifest["cases"]

    assert manifest["model_calls_executed"] == 0
    assert manifest["official_dataset"]["license"] == "MIT"
    assert manifest["official_code"]["license"] == "NO_LICENSE_FILE_OBSERVED"
    assert manifest["selection"]["target_agent_fields"] == sorted(
        runner.TARGET_AGENT_FIELDS
    )
    assert len(cases) == 12
    assert len({case["target_id"] for case in cases}) == 12
    assert len({case["repo"] for case in cases}) == 9
    assert [arm["name"] for arm in manifest["arms"]] == [
        "no_memory",
        "unrelated_memory",
        "related_memphant_memory",
    ]
    for case in cases:
        assert case["experience_created_at"] < case["target_created_at"]
        assert case["unrelated_created_at"] < case["target_created_at"]
        assert case["experience_id"] != case["unrelated_experience_id"]
        assert case["unrelated_shared_patch_files"] == []
        assert case["unrelated_added_line_overlap"] == 0
        assert case["target_patch_sha256"] not in {
            case["experience_patch_sha256"],
            case["unrelated_patch_sha256"],
        }
        assert runner.DOCKER_DIGEST_RE.fullmatch(case["docker_image_digest"])
        official_case = (
            OFFICIAL / "cases/SWEContextBench Lite" / f"{case['target_id']}.json"
        )
        assert runner.sha256_file(official_case) == case["official_case_json_sha256"]


def test_manifest_locks_only_small_lite_parquet_objects():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["dataset_files"] == {
        "SWEContextBench_Lite_Experience.parquet": {
            "bytes": 1119540,
            "sha256": "7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4",
        },
        "SWEContextBench_Related_Lite.parquet": {
            "bytes": 538469,
            "sha256": "1930b392f7beb17a0d87c2e79d1eb889af2c5996b23a003386651ba64a68b8f3",
        },
        "SWEContextBench_Relationship.parquet": {
            "bytes": 24385,
            "sha256": "4bcbe81657a58ad3349ae97c8ff836ed154d3e30d98de2207b1bc5309843ce93",
        },
    }


def test_committed_rehearsal_has_complete_receipts_and_runtime_identity():
    runner = _load()
    manifest = json.loads(MANIFEST.read_text())
    rehearsal = json.loads(REHEARSAL.read_text())
    records = rehearsal["records"]

    assert rehearsal["classification"] == (
        "no_model_adapter_and_retrieval_rehearsal_not_task_success"
    )
    assert rehearsal["manifest_sha256"] == runner.sha256_json(manifest)
    assert rehearsal["model_calls"] == 0
    assert rehearsal["cost_usd"] == 0
    assert rehearsal["database_persisted"] is False
    assert rehearsal["worker_completed"] == 24
    assert len(records) == 24
    assert {(record["target_id"], record["arm"]) for record in records} == {
        (case["target_id"], arm)
        for case in manifest["cases"]
        for arm in ("related", "unrelated")
    }
    for record in records:
        assert record["returned_unit_ids"]
        assert record["rendered_tokens"] > 0
        assert record["latency_ms"] >= 0
        for field in (
            "resource_body_sha256",
            "trace_sha256",
            "context_sha256",
            "receipt_sha256",
            "future_mark_payload_sha256",
        ):
            assert len(record[field]) == 64

    identity = rehearsal["runtime_identity"]
    assert identity["runner_sha256"] == runner.sha256_file(RUNNER)
    assert identity["gate_runtime_sha256"] == runner.sha256_file(
        ROOT / "scripts/gate_runtime.py"
    )
    assert len(identity["prompt_contract_sha256"]) == 64
    assert set(identity["binaries"]) == {"server", "worker", "cli"}
    assert all(len(spec["sha256"]) == 64 for spec in identity["binaries"].values())
