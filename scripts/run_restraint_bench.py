#!/usr/bin/env python3
"""Run the pinned official MemSyco-Bench suite through packaged MemPhant."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_runtime  # noqa: E402
from provider_attempts import (  # noqa: E402
    load_provider_attempt_ledger_snapshot,
    validate_provider_attempt_ledger,
)


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "benchmarks" / "memsyco" / "harness_bootstrap.py"
BASELINE_CONFIG = ROOT / "benchmarks" / "memsyco" / "memphant.baseline.json"
CURRENT_DATE = "2025-06-01"
TASKS = (
    "objective_fact_judgment",
    "contextual_scope_control",
    "memory_evidence_conflict",
    "valid_memory_selection",
    "personalized_memory_use",
)
REPORT_TASKS = {task: task for task in TASKS}
METRIC_KEYS = {
    "objective_fact_judgment": {
        "n_judged", "objective_correctness_avg", "preference_contamination_avg",
        "preference_answer_selected_avg", "suppress_pass_avg",
        "objective_correctness_sum", "preference_contamination_sum",
        "preference_answer_selected_sum", "suppress_pass_sum",
        "judge_parse_failed", "judge_error_count",
    },
    "contextual_scope_control": {
        "n_scored", "accuracy_avg", "incorrectly_used_preference_avg",
        "scope_pass_avg", "accuracy_sum", "incorrectly_used_preference_sum",
        "scope_pass_sum", "parse_failed", "judge_error_count",
    },
    "memory_evidence_conflict": {
        "n_scored", "accuracy_avg", "misled_by_conflicting_memory_avg",
        "evidence_pass_avg", "accuracy_sum", "misled_by_conflicting_memory_sum",
        "evidence_pass_sum", "parse_failed", "judge_error_count",
    },
    "valid_memory_selection": {
        "n_judged", "uses_latest_preference_avg",
        "outdated_preference_contamination_avg", "valid_selection_pass_avg",
        "uses_latest_preference_sum", "outdated_preference_contamination_sum",
        "valid_selection_pass_sum", "judge_parse_failed", "judge_error_count",
    },
    "personalized_memory_use": {
        "n_judged", "answer_accuracy_avg", "preference_used_avg",
        "memory_use_pass_avg", "answer_accuracy_sum", "preference_used_sum",
        "memory_use_pass_sum", "judge_parse_failed", "judge_error_count",
    },
}


def cargo_binary_path(name: str) -> Path:
    target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    if not target.is_absolute():
        target = ROOT / target
    return target / "debug" / name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize_sample_slice(
    source: Path,
    destination: Path,
    *,
    offset: int,
    sample_count: int,
) -> dict:
    """Freeze one exact JSONL slice before any database or provider activity."""
    if destination.exists() or (destination.parent.exists() and any(destination.parent.iterdir())):
        raise RuntimeError("MemSyco requires a fresh artifact directory")
    if offset < 0 or sample_count < 1:
        raise ValueError("offset must be non-negative and sample_count must be positive")
    if not source.is_file():
        raise FileNotFoundError(source)
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = rows[offset : offset + sample_count]
    if len(selected) != sample_count:
        raise RuntimeError(
            f"MemSyco slice count mismatch: requested {sample_count} at offset {offset}, "
            f"found {len(selected)}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in selected
        ),
        encoding="utf-8",
    )
    return {
        "source_sha256": sha256_file(source),
        "slice_sha256": sha256_file(destination),
        "source_rows": len(rows),
        "offset": offset,
        "sample_count": sample_count,
    }


def release_url(lock: dict) -> str:
    return f"{lock['code']['repository']}/archive/{lock['code']['revision']}.tar.gz"


def verify_official(official_dir: Path, lock: dict) -> dict[str, int]:
    expected_files = dict(lock["code"]["files"]) | dict(lock["native_scorer"]["files"])
    expected_files |= {
        "data/manifest.json": lock["dataset"]["manifest_sha256"],
        "data/schema.json": lock["dataset"]["schema_sha256"],
        **{
            f"data/{spec['file']}": spec["sha256"]
            for spec in lock["dataset"]["tasks"].values()
        },
    }
    for relative, expected in expected_files.items():
        path = official_dir / relative
        actual = sha256_file(path) if path.is_file() else "missing"
        if actual != expected:
            raise RuntimeError(
                f"official file drift: {relative}; expected {expected}, got {actual}"
            )

    upstream = json.loads((official_dir / "data/manifest.json").read_text(encoding="utf-8"))
    expected_dataset = lock["dataset"]
    expected_upstream_tasks = {
        task: {
            key: value
            for key, value in spec.items()
            if key != "manifest_sha256_claim"
        }
        | {
            "sha256": spec.get("manifest_sha256_claim", spec["sha256"]),
        }
        for task, spec in expected_dataset["tasks"].items()
    }
    if (
        upstream.get("schema_version") != expected_dataset["schema_version"]
        or upstream.get("total_samples") != expected_dataset["total_samples"]
        or upstream.get("tasks") != expected_upstream_tasks
    ):
        raise RuntimeError("official dataset manifest drift")
    sample_count = 0
    for spec in expected_dataset["tasks"].values():
        path = official_dir / "data" / spec["file"]
        rows = sum(1 for line in path.read_bytes().splitlines() if line.strip())
        if rows != spec["samples"]:
            raise RuntimeError(f"official dataset row-count drift: {spec['file']}")
        sample_count += rows
    if sample_count != expected_dataset["total_samples"]:
        raise RuntimeError("official dataset total-count drift")
    return {
        "files": len(expected_files),
        "samples": sample_count,
        "tasks": len(expected_dataset["tasks"]),
    }


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "MemPhant-MemSyco"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def acquire(directory: Path, lock: dict, *, downloader=_download) -> dict[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    official = directory / "official"
    if official.exists():
        return verify_official(official, lock)
    with tempfile.TemporaryDirectory(dir=directory) as temp_name:
        temp = Path(temp_name)
        archive = temp / "official.tar.gz"
        extracted = temp / "extracted"
        extracted.mkdir()
        downloader(release_url(lock), archive)
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(extracted, filter="data")
        roots = list(extracted.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise RuntimeError("unexpected official archive layout")
        audit = verify_official(roots[0], lock)
        roots[0].replace(official)
        return audit


def official_command(
    *,
    official_dir: Path,
    output: Path,
    task: str,
    arm: str,
    model: str,
    base_url: str,
    judge_model: str,
    judge_base_url: str,
    limit: int,
    usage_ledger: Path | None = None,
    test_jsonl: Path | None = None,
) -> list[str]:
    if task not in TASKS:
        raise ValueError(f"unsupported task: {task}")
    if arm not in {"memphant", "episode_only", "raw_dialogue", "no_memory"}:
        raise ValueError(f"unsupported arm: {arm}")
    if arm == "no_memory" and task != "objective_fact_judgment":
        raise ValueError("NoMemory is only an official objective-task control")
    command = [
        sys.executable,
        str(BOOTSTRAP),
        "--official-dir",
        str(official_dir.resolve()),
        "--usage-ledger",
        str((usage_ledger or output.parent / "usage.jsonl").resolve()),
        task,
        "--optimized",
        "--test-jsonl",
        str((test_jsonl or official_dir / "data" / f"{task}.jsonl").resolve()),
        "--output",
        str(output.resolve()),
        "--model",
        model,
        "--base-url",
        base_url,
        "--judge-model",
        judge_model,
        "--judge-base-url",
        judge_base_url,
        "--current-date",
        CURRENT_DATE,
        "--workers",
        "1",
        "--limit",
        str(limit),
        "--no-completion-cache",
        "--api-max-retries",
        "1",
    ]
    if arm in {"memphant", "episode_only"}:
        if task == "objective_fact_judgment":
            command.append("--with-memory-only")
        command.extend(
            [
                "--memory-method",
                "MemPhant",
                "--memory-top-k",
                "10",
                "--memory-baseline-config",
                str(BASELINE_CONFIG.resolve()),
                "--memory-save-root",
                str((output.parent / "memory").resolve()),
            ]
        )
    elif arm == "raw_dialogue":
        if task == "objective_fact_judgment":
            command.append("--with-memory-only")
    else:
        command.append("--no-memory-only")
    return command


def paid_provider_env() -> dict[str, str]:
    """Build the official harness environment without putting keys in argv/proofs."""
    env = dict(os.environ)
    openrouter_key = env.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        env.setdefault("GENERATION_API_KEY", openrouter_key)
        env.setdefault("JUDGE_API_KEY", openrouter_key)
    return env


def implementation_hashes() -> dict[str, str]:
    return {
        "adapter": sha256_file(ROOT / "benchmarks/memsyco/memphant_baseline.py"),
        "baseline_config": sha256_file(BASELINE_CONFIG),
        "harness_bootstrap": sha256_file(BOOTSTRAP),
        "provider_attempts": sha256_file(ROOT / "scripts/provider_attempts.py"),
        "structured_state_openrouter": sha256_file(
            ROOT / "crates/memphant-runtime/src/structured_state_openrouter.rs"
        ),
        "structured_state_prompt": sha256_file(ROOT / "config/structured-state-v2.txt"),
    }


def requested_models_match(
    requested: list[str], answer_model: object, judge_model: object, sample_count: int
) -> bool:
    if not isinstance(answer_model, str) or not isinstance(judge_model, str):
        return False
    if answer_model == judge_model:
        return requested.count(answer_model) == 2 * sample_count
    return (
        requested.count(answer_model) == sample_count
        and requested.count(judge_model) == sample_count
    )


def structured_extractor_is_complete(extractor: dict, expected: int) -> bool:
    """Accept fully recovered HTTP retries without weakening terminal checks."""
    transient_http = extractor.get("transient_http_attempts")
    if type(transient_http) is not int or transient_http < 0:
        return False
    provider_attempts = expected + transient_http
    return (
        extractor.get("provider_attempts") == provider_attempts
        and extractor.get("completed_attempts") == provider_attempts
        and extractor.get("interrupted_attempts") == 0
        and extractor.get("successful_responses") == expected
        and extractor.get("successful_decodes") == expected
        and extractor.get("successful_episodes") == expected
        and extractor.get("episodes") == expected
        and extractor.get("priced_responses") == expected
        and extractor.get("unpriced_attempts") == transient_http
        and extractor.get("transient_attempts") == transient_http
        and extractor.get("transient_no_content_attempts") == 0
        and extractor.get("transient_transport_attempts") == 0
        and extractor.get("terminal_provenance_errors") == 0
        and extractor.get("terminal_decode_errors") == 0
        and extractor.get("terminal_rejected_operations") == 0
    )


def _verify_legacy_results(run_dir: Path) -> dict:
    contract = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if contract.get("tasks") != list(TASKS) or contract.get("sample_limit") != 1:
        raise RuntimeError("MemSyco smoke run contract is not the five-task one-sample ladder")
    answer_model = contract.get("answer_model")
    judge_model = contract.get("judge_model")
    extractor_model = contract.get("extractor_model")
    if not isinstance(extractor_model, str) or not extractor_model:
        raise RuntimeError("MemSyco smoke run contract lacks extractor model")
    extractor = gate_runtime.structured_extractor_attempt_summary(
        run_dir / "extractor-attempts.jsonl",
        extractor_model,
        require_episode_coverage=True,
    )
    if not structured_extractor_is_complete(extractor, len(TASKS)):
        raise RuntimeError("MemSyco smoke extractor coverage/cost mismatch")
    metrics_by_task = {}
    response_ids: list[str] = []
    for task in TASKS:
        task_dir = run_dir / task
        report = json.loads((task_dir / "report.json").read_text(encoding="utf-8"))
        if report.get("task") != REPORT_TASKS[task]:
            raise RuntimeError(f"MemSyco {task} official task mismatch")
        results = report.get("results")
        sample_count = report.get("n_samples", len(results) if isinstance(results, list) else None)
        if not isinstance(results, list) or len(results) != 1 or sample_count != 1:
            raise RuntimeError(f"MemSyco {task} result count mismatch")
        result = results[0]
        if report.get("n_api_failed_samples") != 0 or result.get("api_error"):
            raise RuntimeError(f"MemSyco {task} contains an API failure")
        with_memory = result.get("with_memory")
        judge = with_memory.get("judge") if isinstance(with_memory, dict) else None
        if (
            not isinstance(with_memory, dict)
            or not isinstance(with_memory.get("answer_text"), str)
            or not with_memory["answer_text"].strip()
            or not isinstance(judge, dict)
            or judge.get("judge_parse_ok") is not True
            or judge.get("judge_error") is not None
        ):
            raise RuntimeError(f"MemSyco {task} judge result is incomplete")
        metrics = report.get("metrics")
        count_key = (
            "n_scored"
            if task in {"contextual_scope_control", "memory_evidence_conflict"}
            else "n_judged"
        )
        with_memory_metrics = metrics.get("with_memory") if isinstance(metrics, dict) else None
        if (
            not isinstance(metrics, dict)
            or not isinstance(with_memory_metrics, dict)
            or set(with_memory_metrics) != METRIC_KEYS[task]
            or with_memory_metrics.get(count_key) != 1
            or with_memory_metrics.get("judge_error_count") != 0
            or with_memory_metrics.get(
                "parse_failed" if count_key == "n_scored" else "judge_parse_failed"
            ) != 0
        ):
            raise RuntimeError(f"MemSyco {task} lacks official metrics")
        proofs = list((task_dir / "memory").glob("*.json"))
        if len(proofs) != 1:
            raise RuntimeError(f"MemSyco {task} adapter proof count mismatch")
        proof = json.loads(proofs[0].read_text(encoding="utf-8"))
        if proof.get("gold_fields_consumed") != [] or not proof.get("trace_id"):
            raise RuntimeError(f"MemSyco {task} adapter proof is malformed")
        sample_digest = proof.get("sample_key_sha256")
        source = proof.get("sample_key_source")
        if source == "official_argument":
            sample_key = result.get("id") or result.get("query_id")
            expected_digest = (
                hashlib.sha256(str(sample_key).encode()).hexdigest()
                if isinstance(sample_key, (str, int)) and str(sample_key)
                else None
            )
        elif source == "label_free_content_hash":
            identity_material = hashlib.sha256(
                json.dumps(
                    {
                        "dialogue_sha256": proof.get("dialogue_sha256"),
                        "question_sha256": proof.get("question_sha256"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            expected_digest = hashlib.sha256(
                f"content-{identity_material}".encode()
            ).hexdigest()
            if proof.get("sample_identity_material_sha256") != identity_material:
                expected_digest = None
        else:
            expected_digest = None
        lightmem = result.get("lightmem")
        if (
            not isinstance(sample_digest, str)
            or len(sample_digest) != 64
            or sample_digest != expected_digest
            or not isinstance(lightmem, dict)
            or lightmem.get("user_id") != f"memsyco_{sample_digest[:24]}"
            or Path(str(lightmem.get("save_dir"))).resolve() != proofs[0].resolve()
            or proofs[0].stem != sample_digest
        ):
            raise RuntimeError(f"MemSyco {task} sample identity mismatch")
        snapshot = load_provider_attempt_ledger_snapshot(task_dir / "attempts.json")
        validate_provider_attempt_ledger(snapshot)
        if snapshot["provider_attempts"] != 2:
            raise RuntimeError(f"MemSyco {task} answer/judge attempt count mismatch")
        requested = []
        for attempt in snapshot["attempts"]:
            result = attempt["result"]
            response = result["response"]
            if response.get("retry_index") != 0:
                raise RuntimeError(f"MemSyco {task} contains a retry")
            if result.get("context") != {"arm": "memphant", "task": task}:
                raise RuntimeError(f"MemSyco {task} attempt context mismatch")
            response_ids.append(response["response_id"])
            requested.append(response.get("requested_model"))
        if sorted(requested) != sorted([answer_model, judge_model]):
            raise RuntimeError(f"MemSyco {task} answer/judge model mismatch")
        metrics_by_task[task] = metrics
    if len(response_ids) != len(set(response_ids)):
        raise RuntimeError("MemSyco smoke contains duplicate response IDs")
    return {
        "benchmark": "MemSyco-Bench",
        "extractor_attempt_ledger_sha256": extractor["ledger_sha256"],
        "metrics_by_task": metrics_by_task,
    }


def _verify_manifest_results(run_dir: Path, contract: dict) -> dict:
    task = contract.get("task")
    arm = contract.get("arm")
    expected = contract.get("sample_count")
    if task not in TASKS or arm not in {
        "memphant",
        "episode_only",
        "raw_dialogue",
        "no_memory",
    }:
        raise RuntimeError("MemSyco run manifest task/arm is invalid")
    if not isinstance(expected, int) or expected < 1:
        raise RuntimeError("MemSyco run manifest sample count is invalid")
    input_path = run_dir / "input.jsonl"
    if (
        not input_path.is_file()
        or sha256_file(input_path) != contract.get("slice_sha256")
        or sum(1 for line in input_path.read_bytes().splitlines() if line.strip()) != expected
    ):
        raise RuntimeError("MemSyco run input slice drifted")

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    results = report.get("results")
    sample_count = report.get("n_samples", len(results) if isinstance(results, list) else None)
    if (
        report.get("task") != REPORT_TASKS[task]
        or not isinstance(results, list)
        or len(results) != expected
        or sample_count != expected
        or report.get("n_api_failed_samples") != 0
    ):
        raise RuntimeError("MemSyco manifest result count/task mismatch")
    result_key = "no_memory" if arm == "no_memory" else "with_memory"
    for result in results:
        block = result.get(result_key)
        judge = block.get("judge") if isinstance(block, dict) else None
        if (
            result.get("api_error")
            or not isinstance(block, dict)
            or not isinstance(block.get("answer_text"), str)
            or not block["answer_text"].strip()
            or not isinstance(judge, dict)
            or judge.get("judge_parse_ok") is not True
            or judge.get("judge_error") is not None
        ):
            raise RuntimeError("MemSyco manifest contains an incomplete result")
    metrics = report.get("metrics")
    selected_metrics = metrics.get(result_key) if isinstance(metrics, dict) else None
    count_key = (
        "n_scored"
        if task in {"contextual_scope_control", "memory_evidence_conflict"}
        else "n_judged"
    )
    if (
        not isinstance(selected_metrics, dict)
        or set(selected_metrics) != METRIC_KEYS[task]
        or selected_metrics.get(count_key) != expected
        or selected_metrics.get("judge_error_count") != 0
        or selected_metrics.get(
            "parse_failed" if count_key == "n_scored" else "judge_parse_failed"
        )
        != 0
    ):
        raise RuntimeError("MemSyco manifest lacks complete official metrics")

    response_ids: list[str] = []
    snapshot = load_provider_attempt_ledger_snapshot(run_dir / "attempts.json")
    validate_provider_attempt_ledger(snapshot)
    if snapshot["provider_attempts"] != 2 * expected:
        raise RuntimeError("MemSyco manifest answer/judge attempt count mismatch")
    requested: list[str] = []
    for attempt in snapshot["attempts"]:
        result = attempt["result"]
        response = result["response"]
        if (
            response.get("retry_index") != 0
            or result.get("context") != {"arm": arm, "task": task}
        ):
            raise RuntimeError("MemSyco manifest contains retry/context drift")
        response_ids.append(response["response_id"])
        requested.append(response["requested_model"])
    if not requested_models_match(
        requested, contract.get("answer_model"), contract.get("judge_model"), expected
    ):
        raise RuntimeError("MemSyco manifest answer/judge model mismatch")
    if len(response_ids) != len(set(response_ids)):
        raise RuntimeError("MemSyco manifest contains duplicate response IDs")

    proof_hashes: list[str] = []
    extractor = None
    if arm in {"memphant", "episode_only"}:
        proof_paths = sorted((run_dir / "memory").glob("*.json"))
        if len(proof_paths) != expected:
            raise RuntimeError("MemSyco manifest adapter proof count mismatch")
        implementation = contract.get("implementation_sha256")
        for proof_path in proof_paths:
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            if (
                proof.get("gold_fields_consumed") != []
                or not proof.get("trace_id")
                or proof.get("implementation_sha256") != implementation
                or not isinstance(proof.get("typed_context_sha256"), str)
                or len(proof["typed_context_sha256"]) != 64
                or hashlib.sha256(proof.get("typed_context", "").encode()).hexdigest()
                != proof.get("typed_context_sha256")
                or not isinstance(proof.get("typed_memories"), list)
            ):
                raise RuntimeError("MemSyco manifest adapter proof is malformed")
            proof_hashes.append(sha256_file(proof_path))
        if arm == "memphant":
            extractor_model = contract.get("extractor_model")
            if not isinstance(extractor_model, str) or not extractor_model:
                raise RuntimeError("MemSyco structured run lacks extractor model")
            extractor = gate_runtime.structured_extractor_attempt_summary(
                run_dir / "extractor-attempts.jsonl",
                extractor_model,
                require_episode_coverage=True,
            )
            if not structured_extractor_is_complete(extractor, expected):
                raise RuntimeError("MemSyco structured extractor coverage/cost mismatch")
        elif (run_dir / "extractor-attempts.jsonl").exists():
            raise RuntimeError("episode-only MemSyco run unexpectedly used the extractor")
    elif (run_dir / "memory").exists():
        raise RuntimeError("MemSyco control unexpectedly emitted adapter proofs")

    return {
        "benchmark": "MemSyco-Bench",
        "task": task,
        "arm": arm,
        "sample_count": expected,
        "metrics": selected_metrics,
        "attempt_ledger_sha256": snapshot["attempts_sha256"],
        "extractor_attempt_ledger_sha256": (
            extractor["ledger_sha256"] if extractor is not None else None
        ),
        "proof_sha256": proof_hashes,
    }


def verify_results(run_dir: Path) -> dict:
    contract = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if contract.get("schema_version") == 2:
        return _verify_manifest_results(run_dir, contract)
    return _verify_legacy_results(run_dir)


OFFICIAL_VMS_ROWS = 350
OFFICIAL_BOOTSTRAP_RESAMPLES = 10_000
OFFICIAL_BOOTSTRAP_SEED = 20260716
OFFICIAL_CLEAN_EXCLUSION = "v11_000321"
OFFICIAL_TASK_SPECS = {
    "valid_memory_selection": {
        "task": "valid_memory_selection",
        "source_file": "valid_memory_selection.jsonl",
        "rows": OFFICIAL_VMS_ROWS,
        "clean_exclusion": OFFICIAL_CLEAN_EXCLUSION,
        "base_runs": [
            {"offset": offset, "sample_count": 25}
            for offset in range(0, OFFICIAL_VMS_ROWS, 25)
        ],
        "metrics": {
            "accuracy": "uses_latest_preference",
            "contamination": "outdated_preference_contamination",
        },
        "primary_name": "full-350",
        "sensitivity_name": "clean-349",
    },
    "personalized_memory_use": {
        "task": "personalized_memory_use",
        "source_file": "personalized_memory_use.jsonl",
        "rows": 300,
        "clean_exclusion": "v11_001093",
        "base_runs": [{"offset": 0, "sample_count": 300}],
        "metrics": {
            "accuracy": "answer_accuracy",
            "preference_use": "preference_used",
        },
        "primary_name": "full-300",
        "sensitivity_name": "clean-299",
    },
}
OFFICIAL_CONFIG_KEYS = (
    "answer_model",
    "judge_model",
    "embed_model",
    "top_k",
    "source_sha256",
    "answer_base_url",
    "judge_base_url",
    "provider_policy_sha256",
)


def official_task_spec(task: str) -> dict:
    try:
        spec = OFFICIAL_TASK_SPECS[task]
    except KeyError as exc:
        raise RuntimeError(f"task is not supported by the official scorer: {task}") from exc
    return json.loads(json.dumps(spec))


def validate_official_base_runs(base_runs: list[dict], task_spec: dict) -> list[dict]:
    observed = [
        {"offset": run.get("offset"), "sample_count": run.get("sample_count")}
        for run in base_runs
    ]
    if observed != task_spec["base_runs"]:
        raise RuntimeError("official arm does not contain the predeclared base runs")
    return base_runs


def validate_official_uid_partition(rows: list[dict], expected_uids: list[str]) -> list[dict]:
    uids = [row.get("uid") for row in rows]
    if len(uids) != len(set(uids)):
        raise RuntimeError("official result partition contains a duplicate UID")
    if uids != expected_uids:
        raise RuntimeError("official UID partition does not match the predeclared source order")
    if [row.get("offset") for row in rows] != list(range(len(expected_uids))):
        raise RuntimeError("official offset partition is not unique and gap-free")
    return rows


def validate_official_configuration(
    memphant: list[dict], raw_dialogue: list[dict]
) -> dict:
    configurations = memphant + raw_dialogue
    if not configurations:
        raise RuntimeError("official configuration is absent")
    canonical = {key: configurations[0].get(key) for key in OFFICIAL_CONFIG_KEYS}
    if any(value is None for value in canonical.values()):
        raise RuntimeError("official configuration is incomplete")
    for configuration in configurations[1:]:
        normalized = {key: configuration.get(key) for key in OFFICIAL_CONFIG_KEYS}
        if configuration in raw_dialogue:
            normalized["embed_model"] = normalized["embed_model"] or canonical["embed_model"]
            normalized["top_k"] = normalized["top_k"] or canonical["top_k"]
        if normalized != canonical:
            raise RuntimeError("official model/provider/judge/embed/top-k configuration drift")
    return canonical


def validate_unique_response_ids(groups: list[list[str]]) -> int:
    response_ids = [response_id for group in groups for response_id in group]
    if not response_ids or any(not value for value in response_ids):
        raise RuntimeError("official response IDs are incomplete")
    if len(response_ids) != len(set(response_ids)):
        raise RuntimeError("official run contains duplicate response IDs")
    return len(response_ids)


def validate_official_recovery(recovery: dict) -> dict:
    if recovery.get("failure_class") != "infrastructure":
        raise RuntimeError("official product failure is not recoverable")
    first_incomplete = recovery.get("first_incomplete_offset")
    recovery_offset = recovery.get("recovery_offset")
    completed = recovery.get("completed_offsets")
    base_offset = recovery.get("base_offset", 0)
    if (
        not isinstance(recovery.get("failed_base"), str)
        or not isinstance(first_incomplete, int)
        or not isinstance(recovery_offset, int)
        or not isinstance(completed, list)
        or not isinstance(base_offset, int)
        or completed != list(range(base_offset, first_incomplete))
    ):
        raise RuntimeError("official infrastructure recovery artifact is malformed")
    if recovery_offset < first_incomplete:
        raise RuntimeError("official recovery attempted a completed-row recall")
    if recovery_offset != first_incomplete:
        raise RuntimeError("official recovery does not begin at the first incomplete row")
    failure = recovery.get("failure")
    if not isinstance(failure, dict):
        raise RuntimeError("official recovery lacks a machine-identifiable infrastructure failure")
    status = failure.get("http_status")
    failure_type = failure.get("type")
    allowed_status = status in {408, 429} or isinstance(status, int) and status >= 500
    allowed_type = failure_type in {
        "transport",
        "local_process",
        "database",
    }
    if not (allowed_status or allowed_type):
        raise RuntimeError("official recovery lacks a machine-identifiable infrastructure failure")
    return recovery


def _percentile_interval(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    lower = ordered[math.floor(0.025 * (len(ordered) - 1))]
    upper = ordered[math.ceil(0.975 * (len(ordered) - 1))]
    return lower, upper


def _metric_summary(point: float, samples: list[float]) -> dict[str, float]:
    lower, upper = _percentile_interval(samples)
    return {"point": point, "lower": lower, "upper": upper}


def _score_paired_binary_metrics(
    memphant: list[dict],
    raw_dialogue: list[dict],
    *,
    metrics: tuple[str, ...],
    resamples: int,
    seed: int,
) -> dict:
    if resamples < 1 or not memphant or len(memphant) != len(raw_dialogue):
        raise ValueError("paired bootstrap requires equal non-empty arms and positive resamples")
    raw_by_uid = {row["uid"]: row for row in raw_dialogue}
    if len(raw_by_uid) != len(raw_dialogue) or set(raw_by_uid) != {
        row["uid"] for row in memphant
    }:
        raise RuntimeError("official arms are not paired by unique UID")
    paired = [(row, raw_by_uid[row["uid"]]) for row in memphant]
    for left, right in paired:
        for row in (left, right):
            if any(row.get(metric) not in {0, 1} for metric in metrics):
                raise RuntimeError("official judge metric is not binary")

    size = len(paired)
    points = {
        (arm, metric): sum(pair[arm][metric] for pair in paired) / size
        for arm in (0, 1)
        for metric in metrics
    }
    rng = random.Random(seed)
    samples = {
        (arm, metric): []
        for arm in (0, 1)
        for metric in metrics
    }
    deltas = {metric: [] for metric in metrics}
    for _ in range(resamples):
        indices = [rng.randrange(size) for _ in range(size)]
        for metric in metrics:
            left = sum(paired[index][0][metric] for index in indices) / size
            right = sum(paired[index][1][metric] for index in indices) / size
            samples[(0, metric)].append(left)
            samples[(1, metric)].append(right)
            deltas[metric].append(left - right)
    result = {
        "memphant": {
            metric: _metric_summary(points[(0, metric)], samples[(0, metric)])
            for metric in metrics
        },
        "raw_dialogue": {
            metric: _metric_summary(points[(1, metric)], samples[(1, metric)])
            for metric in metrics
        },
        "bootstrap": {
            "method": "paired_percentile",
            "resamples": resamples,
            "seed": seed,
            "percentiles": [2.5, 97.5],
        },
    }
    result.update({
        f"paired_{metric}_delta": _metric_summary(
            points[(0, metric)] - points[(1, metric)], deltas[metric]
        )
        for metric in metrics
    })
    return result


def score_paired_rows(
    memphant: list[dict], raw_dialogue: list[dict], *, resamples: int, seed: int
) -> dict:
    return _score_paired_binary_metrics(
        memphant,
        raw_dialogue,
        metrics=("accuracy", "contamination"),
        resamples=resamples,
        seed=seed,
    )


def score_paired_pmu_rows(
    memphant: list[dict], raw_dialogue: list[dict], *, resamples: int, seed: int
) -> dict:
    return _score_paired_binary_metrics(
        memphant,
        raw_dialogue,
        metrics=("accuracy", "preference_use"),
        resamples=resamples,
        seed=seed,
    )


def clean_official_rows(rows: list[dict], exclusion: str) -> list[dict]:
    matches = [row for row in rows if row.get("uid") == exclusion]
    if len(matches) != 1:
        raise RuntimeError("official clean exclusion must match exactly one row")
    return [row for row in rows if row.get("uid") != exclusion]


def evaluate_official_gate(score: dict, clean: dict, thresholds: dict) -> dict:
    checks = {
        "accuracy_point": score["memphant"]["accuracy"]["point"]
        >= thresholds["accuracy_point_min"],
        "accuracy_lower_bound": score["memphant"]["accuracy"]["lower"]
        > thresholds["accuracy_lower_bound_gt"],
        "contamination_point": score["memphant"]["contamination"]["point"]
        <= thresholds["contamination_point_max"],
        "contamination_upper_bound": score["memphant"]["contamination"]["upper"]
        < thresholds["contamination_upper_bound_lt"],
        "paired_accuracy_lower_bound": score["paired_accuracy_delta"]["lower"]
        > thresholds["paired_accuracy_lower_bound_gt"],
        "paired_contamination_upper_bound": score["paired_contamination_delta"]["upper"]
        < thresholds["paired_contamination_upper_bound_lt"],
        "clean_accuracy_point": clean["accuracy"]
        > thresholds["clean_accuracy_point_gt"],
        "clean_contamination_point": clean["contamination"]
        < thresholds["clean_contamination_point_lt"],
    }
    return {"checks": checks, "pass": all(checks.values())}


def evaluate_pmu_official_gate(score: dict, clean: dict, thresholds: dict) -> dict:
    checks = {
        "accuracy_point": score["memphant"]["accuracy"]["point"]
        >= thresholds["accuracy_point_min"],
        "accuracy_lower_bound": score["memphant"]["accuracy"]["lower"]
        > thresholds["accuracy_lower_bound_gt"],
        "preference_use_point": score["memphant"]["preference_use"]["point"]
        >= thresholds["preference_use_point_min"],
        "preference_use_lower_bound": score["memphant"]["preference_use"]["lower"]
        > thresholds["preference_use_lower_bound_gt"],
        "paired_accuracy_lower_bound": score["paired_accuracy_delta"]["lower"]
        > thresholds["paired_accuracy_lower_bound_gt"],
        "paired_preference_use_lower_bound": score["paired_preference_use_delta"]["lower"]
        > thresholds["paired_preference_use_lower_bound_gt"],
        "clean_accuracy_point": clean["accuracy"]
        > thresholds["clean_accuracy_point_gt"],
        "clean_preference_use_point": clean["preference_use"]
        > thresholds["clean_preference_use_point_gt"],
    }
    return {"checks": checks, "pass": all(checks.values())}


def _json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _artifact_manifest(root: Path) -> dict:
    entries = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return {
        "file_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
        "manifest_sha256": _json_sha256(entries),
    }


def _attempt_stage_summary(attempts: list[dict]) -> tuple[list[str], dict]:
    response_ids = []
    stages = {
        "answer": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0, "latency_seconds": 0.0},
        "judge": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0, "latency_seconds": 0.0},
    }
    for index, attempt in enumerate(attempts):
        if attempt.get("status") != "result" or attempt.get("error") is not None:
            raise RuntimeError("official complete shard contains a hidden provider error")
        response = attempt["result"]["response"]
        response_ids.append(response.get("response_id"))
        stage = stages["answer" if index % 2 == 0 else "judge"]
        usage = response.get("usage")
        if not isinstance(usage, dict) or not isinstance(response.get("elapsed_seconds"), (int, float)):
            raise RuntimeError("official complete shard lacks usage or latency")
        stage["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if not isinstance(usage.get(key), int):
                raise RuntimeError("official complete shard contains incomplete token usage")
            stage[key] += usage[key]
        if not isinstance(usage.get("cost"), (int, float)):
            raise RuntimeError("official complete shard contains incomplete cost")
        stage["cost"] += usage["cost"]
        stage["latency_seconds"] += response["elapsed_seconds"]
    return response_ids, stages


def _extractor_stage_summary(path: Path) -> tuple[list[str], dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = [row for row in rows if row.get("event") == "result"]
    response_ids = []
    summary = {
        "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "total_tokens": 0, "cost": 0.0, "latency_seconds": 0.0,
    }
    for row in results:
        usage = row.get("usage")
        if not isinstance(usage, dict) or not isinstance(row.get("elapsed_seconds"), (int, float)):
            raise RuntimeError("official extractor result lacks usage or latency")
        response_ids.append(row.get("response_id"))
        summary["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if not isinstance(usage.get(key), int):
                raise RuntimeError("official extractor result contains incomplete token usage")
            summary[key] += usage[key]
        if not isinstance(usage.get("cost"), (int, float)):
            raise RuntimeError("official extractor result contains incomplete cost")
        summary["cost"] += usage["cost"]
        summary["latency_seconds"] += row["elapsed_seconds"]
    return response_ids, summary


def _merge_stage_totals(totals: dict, incoming: dict) -> None:
    for stage, values in incoming.items():
        target = totals.setdefault(stage, {key: 0 for key in values})
        for key, value in values.items():
            target[key] += value


def official_metric_row(result: dict, *, offset: int, task_spec: dict) -> dict:
    uid = result.get("query_id") or result.get("id")
    if not isinstance(uid, str) or not uid:
        raise RuntimeError("official result lacks its public UID")
    judge = result.get("with_memory", {}).get("judge")
    if not isinstance(judge, dict):
        raise RuntimeError("official result lacks its judge verdict")
    row = {"uid": uid, "offset": offset}
    for metric, judge_key in task_spec["metrics"].items():
        value = judge.get(judge_key)
        if value not in {0, 1}:
            raise RuntimeError("official judge metric is not binary")
        row[metric] = value
    return row


def _load_complete_shard(
    run_dir: Path, *, arm: str, provider_policy_sha256: str, task_spec: dict
) -> dict:
    if any(run_dir.rglob("ABORTED.json")):
        raise RuntimeError("official complete shard contains an abort artifact")
    verified = verify_results(run_dir)
    contract = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if contract.get("task") != task_spec["task"] or contract.get("arm") != arm:
        raise RuntimeError("official shard task/arm drift")
    if contract.get("promotion_ineligible") is not False:
        raise RuntimeError("official shard is marked promotion-ineligible")
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    rows = [
        official_metric_row(
            result, offset=contract["offset"] + index, task_spec=task_spec
        )
        for index, result in enumerate(report["results"])
    ]
    snapshot = load_provider_attempt_ledger_snapshot(run_dir / "attempts.json")
    response_ids, stages = _attempt_stage_summary(snapshot["attempts"])
    if arm == "memphant":
        extractor_ids, extractor_stage = _extractor_stage_summary(
            run_dir / "extractor-attempts.jsonl"
        )
        response_ids.extend(extractor_ids)
        stages["extractor"] = extractor_stage
    configuration = {
        "answer_model": contract.get("answer_model"),
        "judge_model": contract.get("judge_model"),
        "embed_model": contract.get("embed_model"),
        "top_k": contract.get("top_k"),
        "source_sha256": contract.get("source_sha256"),
        "answer_base_url": report.get("base_url"),
        "judge_base_url": report.get("judge_base_url"),
        "provider_policy_sha256": provider_policy_sha256,
    }
    bindings = {
        name: sha256_file(run_dir / name)
        for name in ("run.json", "input.jsonl", "report.json", "attempts.json")
    }
    if arm == "memphant":
        bindings["extractor-attempts.jsonl"] = sha256_file(
            run_dir / "extractor-attempts.jsonl"
        )
    return {
        "rows": rows,
        "response_ids": response_ids,
        "stage_totals": stages,
        "configuration": configuration,
        "binding": {
            "directory": run_dir.name,
            "offset": contract["offset"],
            "sample_count": contract["sample_count"],
            "files_sha256": bindings,
            "binary_sha256": contract.get("binary_sha256"),
            "implementation_sha256": contract.get("implementation_sha256"),
            "response_ids_sha256": _json_sha256(response_ids),
            "verification_sha256": _json_sha256(verified),
        },
    }


def _collect_official_arm(
    root: Path, *, arm: str, provider_policy_sha256: str, task_spec: dict
) -> dict:
    run_dirs = sorted(path.parent for path in root.rglob("run.json"))
    if not run_dirs:
        raise RuntimeError(f"official {arm} arm contains no shards")
    complete = []
    partial = []
    recoveries = []
    for recovery_path in sorted(root.rglob("RECOVERY.json")):
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        validate_official_recovery(recovery)
        recoveries.append({"path": recovery_path.relative_to(root).as_posix(), "sha256": sha256_file(recovery_path), **recovery})
    base_contracts = []
    for run_dir in run_dirs:
        contract = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        if not (run_dir / "RECOVERY.json").is_file():
            base_contracts.append(
                {"directory": run_dir.relative_to(root).as_posix(), "offset": contract.get("offset"), "sample_count": contract.get("sample_count")}
            )
        if (run_dir / "report.json").is_file():
            complete.append(
                _load_complete_shard(
                    run_dir,
                    arm=arm,
                    provider_policy_sha256=provider_policy_sha256,
                    task_spec=task_spec,
                )
            )
        else:
            if any(run_dir.rglob("ABORTED.json")):
                raise RuntimeError("official arm contains an unrecoverable product failure")
            if not any(item["failed_base"] in {run_dir.name, run_dir.relative_to(root).as_posix()} for item in recoveries):
                raise RuntimeError("official partial shard is not bound to an infrastructure recovery")
            partial.append(
                {
                    "directory": run_dir.relative_to(root).as_posix(),
                    "artifact_manifest": _artifact_manifest(run_dir),
                }
            )
    base_contracts.sort(key=lambda item: item["offset"])
    validate_official_base_runs(base_contracts, task_spec)
    rows = sorted((row for shard in complete for row in shard["rows"]), key=lambda row: row["offset"])
    response_groups = [shard["response_ids"] for shard in complete]
    stage_totals = {}
    for shard in complete:
        _merge_stage_totals(stage_totals, shard["stage_totals"])
    return {
        "rows": rows,
        "response_ids": response_groups,
        "configurations": [shard["configuration"] for shard in complete],
        "stage_totals": stage_totals,
        "complete_shards": [shard["binding"] for shard in complete],
        "partial_infrastructure_shards": partial,
        "recoveries": recoveries,
        "base_slices": base_contracts,
        "artifact_manifest": _artifact_manifest(root),
    }


def _validate_candidate_bindings(freeze: dict, memphant: dict, raw: dict, configuration: dict) -> None:
    expected_implementation = dict(freeze.get("implementation_sha256", {}))
    expected_implementation.pop("runner", None)
    expected_binaries = freeze.get("binary_sha256")
    if not expected_implementation or not isinstance(expected_binaries, dict):
        raise RuntimeError("candidate freeze lacks implementation or binary bindings")
    for shard in memphant["complete_shards"] + raw["complete_shards"]:
        if shard.get("implementation_sha256") != expected_implementation:
            raise RuntimeError("official shard implementation drifted from the candidate freeze")
    for shard in memphant["complete_shards"]:
        if shard.get("binary_sha256") != expected_binaries:
            raise RuntimeError("official MemPhant binary drifted from the candidate freeze")
    expected_config = {
        "answer_model": freeze.get("models", {}).get("answer"),
        "judge_model": freeze.get("models", {}).get("judge"),
        "embed_model": freeze.get("embedding", {}).get("identity"),
        "top_k": freeze.get("embedding", {}).get("top_k"),
        "provider_policy_sha256": freeze.get("provider_policy_sha256"),
    }
    if any(configuration.get(key) != value for key, value in expected_config.items()):
        raise RuntimeError("official configuration drifted from the candidate freeze")


def _official_source_uids(source: Path) -> list[str]:
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    uids = [row.get("id") or row.get("query_id") for row in rows]
    if any(not isinstance(uid, str) or not uid for uid in uids):
        raise RuntimeError("official source contains a row without a public UID")
    return uids


def score_official(
    *, official_dir: Path, gate_path: Path, freeze_path: Path,
    memphant_dir: Path, raw_dialogue_dir: Path, out: Path, lock: dict,
) -> dict:
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    task_spec = official_task_spec(gate.get("task", "valid_memory_selection"))
    if gate.get("candidate_freeze_sha256") != sha256_file(freeze_path):
        raise RuntimeError("official gate does not hash-bind the candidate freeze")
    if freeze.get("implementation_sha256", {}).get("runner") != sha256_file(Path(__file__)):
        raise RuntimeError("candidate freeze does not bind the official runner")
    provider_policy_sha256 = freeze.get("provider_policy_sha256")
    if (
        not isinstance(provider_policy_sha256, str)
        or provider_policy_sha256 != _json_sha256(freeze.get("provider_policy"))
    ):
        raise RuntimeError("candidate freeze lacks the provider policy binding")
    official_verification = verify_official(official_dir, lock)
    source = official_dir / "data" / task_spec["source_file"]
    expected_uids = _official_source_uids(source)
    if (
        len(expected_uids) != task_spec["rows"]
        or gate.get("expected_uids") != expected_uids
        or gate.get("expected_uids_sha256") != _json_sha256(expected_uids)
        or gate.get("official_source_sha256") != sha256_file(source)
    ):
        raise RuntimeError("official source/UID gate drift")
    if task_spec["task"] == "personalized_memory_use" and (
        gate.get("expected_rows") != task_spec["rows"]
        or gate.get("prior_public_smoke_uid") != task_spec["clean_exclusion"]
    ):
        raise RuntimeError("official PMU row-count or prior-smoke disclosure drift")
    if (
        gate.get("bootstrap") != {
            "method": "paired_percentile",
            "resamples": OFFICIAL_BOOTSTRAP_RESAMPLES,
            "seed": OFFICIAL_BOOTSTRAP_SEED,
        }
        or gate.get("clean_exclusion") != task_spec["clean_exclusion"]
    ):
        raise RuntimeError("official bootstrap or clean-exclusion gate drift")

    memphant = _collect_official_arm(
        memphant_dir,
        arm="memphant",
        provider_policy_sha256=provider_policy_sha256,
        task_spec=task_spec,
    )
    raw = _collect_official_arm(
        raw_dialogue_dir,
        arm="raw_dialogue",
        provider_policy_sha256=provider_policy_sha256,
        task_spec=task_spec,
    )
    validate_official_uid_partition(memphant["rows"], expected_uids)
    validate_official_uid_partition(raw["rows"], expected_uids)
    configuration = validate_official_configuration(
        memphant["configurations"], raw["configurations"]
    )
    if configuration["source_sha256"] != sha256_file(source):
        raise RuntimeError("official run source drifted from the gated source")
    _validate_candidate_bindings(freeze, memphant, raw, configuration)
    response_count = validate_unique_response_ids(
        memphant["response_ids"] + raw["response_ids"]
    )
    score_rows = (
        score_paired_rows
        if task_spec["task"] == "valid_memory_selection"
        else score_paired_pmu_rows
    )
    full = score_rows(
        memphant["rows"], raw["rows"],
        resamples=OFFICIAL_BOOTSTRAP_RESAMPLES, seed=OFFICIAL_BOOTSTRAP_SEED,
    )
    clean_memphant = clean_official_rows(
        memphant["rows"], task_spec["clean_exclusion"]
    )
    clean_raw = clean_official_rows(raw["rows"], task_spec["clean_exclusion"])
    clean_score = score_rows(
        clean_memphant, clean_raw,
        resamples=OFFICIAL_BOOTSTRAP_RESAMPLES, seed=OFFICIAL_BOOTSTRAP_SEED,
    )
    clean = {
        "excluded_uid": task_spec["clean_exclusion"],
        "n": len(clean_memphant),
        **{
            metric: clean_score["memphant"][metric]["point"]
            for metric in task_spec["metrics"]
        },
        "diagnostic_score": clean_score,
    }
    gate_result = (
        evaluate_official_gate(full, clean, gate["thresholds"])
        if task_spec["task"] == "valid_memory_selection"
        else evaluate_pmu_official_gate(full, clean, gate["thresholds"])
    )
    result = {
        "schema_version": 1,
        "benchmark": "MemSyco-Bench",
        "task": task_spec["task"],
        "primary": {
            "name": task_spec["primary_name"],
            "n": task_spec["rows"],
            "score": full,
        },
        "sensitivity": {"name": task_spec["sensitivity_name"], **clean},
        "gate": gate_result,
        "configuration": configuration,
        "provenance": {
            "official_source_sha256": sha256_file(source),
            "official_verification_sha256": _json_sha256(official_verification),
            "gate_sha256": sha256_file(gate_path),
            "candidate_freeze_sha256": sha256_file(freeze_path),
            "response_id_count": response_count,
            "memphant": {key: value for key, value in memphant.items() if key not in {"rows", "response_ids", "configurations"}},
            "raw_dialogue": {key: value for key, value in raw.items() if key not in {"rows", "response_ids", "configurations"}},
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks/manifests/memsyco.lock.json")
    sub = parser.add_subparsers(dest="command", required=True)
    acquire_parser = sub.add_parser("acquire")
    acquire_parser.add_argument("--directory", type=Path, required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--official-dir", type=Path, required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--official-dir", type=Path, required=True)
    run_parser.add_argument("--out-dir", type=Path, required=True)
    run_parser.add_argument("--task", choices=TASKS, default=None)
    run_parser.add_argument(
        "--arm",
        choices=("memphant", "episode_only", "raw_dialogue", "no_memory"),
        default="memphant",
    )
    run_parser.add_argument("--limit", type=int, default=1)
    run_parser.add_argument("--offset", type=int, default=0)
    run_parser.add_argument("--test-jsonl", type=Path)
    run_parser.add_argument("--embed-model", default="small")
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument("--base-url", required=True)
    run_parser.add_argument("--judge-model", required=True)
    run_parser.add_argument("--judge-base-url", required=True)
    run_parser.add_argument("--database-url", default="postgres://memphant:memphant@localhost:5432/memphant")
    run_parser.add_argument("--port", type=int, default=39433)
    results_parser = sub.add_parser("verify-results")
    results_parser.add_argument("--run-dir", type=Path, required=True)
    score_parser = sub.add_parser("score-official")
    score_parser.add_argument("--official-dir", type=Path, required=True)
    score_parser.add_argument("--gate", type=Path, required=True)
    score_parser.add_argument("--candidate-freeze", type=Path, required=True)
    score_parser.add_argument("--memphant-dir", type=Path, required=True)
    score_parser.add_argument("--raw-dialogue-dir", type=Path, required=True)
    score_parser.add_argument("--out", type=Path, required=True)
    return parser


def run_smoke(args: argparse.Namespace, lock: dict) -> dict:
    if args.task is not None:
        return run_manifest(args, lock)
    verify_official(args.official_dir, lock)
    gate_runtime.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    subprocess.run(
        ["cargo", "build", "-p", "memphant-server", "-p", "memphant-worker", "-p", "memphant-cli"],
        cwd=ROOT,
        check=True,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    extractor_model = os.environ.get("MEMPHANT_STRUCTURED_STATE_MODEL", "").strip()
    if os.environ.get("MEMPHANT_STRUCTURED_STATE") != "on" or not extractor_model:
        raise RuntimeError(
            "MemSyco smoke requires MEMPHANT_STRUCTURED_STATE=on and an explicit model"
        )
    extractor_ledger = args.out_dir / "extractor-attempts.jsonl"
    os.environ["MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER"] = str(extractor_ledger)
    run_contract = {
        "tasks": list(TASKS), "sample_limit": 1,
        "answer_model": args.model, "judge_model": args.judge_model,
        "extractor_model": extractor_model,
        "promotion_ineligible": True,
    }
    (args.out_dir / "run.json").write_text(
        json.dumps(run_contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    server = gate_runtime.Server(
        str(cargo_binary_path("memphant-server")), database_url, args.port, "small",
        log_path=args.out_dir / "memphant-server.log",
    )
    try:
        server.start()
        for task in TASKS:
            task_dir = args.out_dir / task
            task_dir.mkdir(parents=True, exist_ok=True)
            tenant_id, api_key = gate_runtime.provision_tenant(
                str(cargo_binary_path("memphant-cli")), database_url, f"memsyco-{task}"
            )
            env = paid_provider_env()
            env.update({
                "MEMPHANT_MEMSYCO_ARM": "memphant",
                "MEMPHANT_MEMSYCO_TASK": task,
                "MEMPHANT_MEMSYCO_TENANT_ID": tenant_id,
                "MEMPHANT_MEMSYCO_API_KEY": api_key,
                "MEMPHANT_MEMSYCO_PORT": str(args.port),
                "MEMPHANT_MEMSYCO_RUN_ID": args.out_dir.name,
                "MEMPHANT_MEMSYCO_PROOF_DIR": str(task_dir / "memory"),
            })
            subprocess.run(
                official_command(
                    official_dir=args.official_dir, output=task_dir / "report.json",
                    task=task, arm="memphant", model=args.model, base_url=args.base_url,
                    judge_model=args.judge_model, judge_base_url=args.judge_base_url,
                    limit=1, usage_ledger=task_dir / "attempts.json",
                ),
                cwd=args.official_dir,
                env=env,
                check=True,
            )
    finally:
        server.stop()
    return verify_results(args.out_dir)


def run_manifest(args: argparse.Namespace, lock: dict) -> dict:
    """Run one immutable task/arm shard described entirely by run.json."""
    # The official harness changes cwd to its pinned checkout. Keep every
    # artifact and adapter proof rooted in the caller-selected run directory,
    # independent of that cwd transition.
    args.out_dir = args.out_dir.resolve()
    args.official_dir = args.official_dir.resolve()
    if args.test_jsonl is not None:
        args.test_jsonl = args.test_jsonl.resolve()
    verify_official(args.official_dir, lock)
    if args.arm == "no_memory" and args.task != "objective_fact_judgment":
        raise RuntimeError("NoMemory is only valid for objective_fact_judgment")
    memory_arm = args.arm in {"memphant", "episode_only"}
    if memory_arm:
        gate_runtime.reexec_through_scratch_db(args.database_url)
        database_url = os.environ["DATABASE_URL"]
        subprocess.run(
            [
                "cargo",
                "build",
                "-p",
                "memphant-server",
                "-p",
                "memphant-worker",
                "-p",
                "memphant-cli",
            ],
            cwd=ROOT,
            check=True,
        )
    else:
        database_url = ""

    source = args.test_jsonl or args.official_dir / "data" / f"{args.task}.jsonl"
    slice_manifest = materialize_sample_slice(
        source.resolve(),
        args.out_dir / "input.jsonl",
        offset=args.offset,
        sample_count=args.limit,
    )
    extractor_model = None
    if args.arm == "memphant":
        extractor_model = os.environ.get("MEMPHANT_STRUCTURED_STATE_MODEL", "").strip()
        if os.environ.get("MEMPHANT_STRUCTURED_STATE") != "on" or not extractor_model:
            raise RuntimeError(
                "structured MemSyco requires MEMPHANT_STRUCTURED_STATE=on and an explicit model"
            )
    contract = {
        "schema_version": 2,
        "task": args.task,
        "arm": args.arm,
        "sample_count": args.limit,
        "offset": args.offset,
        "answer_model": args.model,
        "judge_model": args.judge_model,
        "extractor_model": extractor_model,
        "embed_model": args.embed_model if memory_arm else None,
        "top_k": 10 if memory_arm else None,
        "promotion_ineligible": args.test_jsonl is not None,
        "implementation_sha256": implementation_hashes(),
        **slice_manifest,
    }
    if memory_arm:
        contract["binary_sha256"] = {
            name: sha256_file(cargo_binary_path(name))
            for name in ("memphant-server", "memphant-worker", "memphant-cli")
        }
    (args.out_dir / "run.json").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    env = paid_provider_env()
    env.update(
        {
            "MEMPHANT_MEMSYCO_ARM": args.arm,
            "MEMPHANT_MEMSYCO_TASK": args.task,
            "MEMPHANT_MEMSYCO_RUN_ID": args.out_dir.name,
        }
    )
    command = official_command(
        official_dir=args.official_dir,
        output=args.out_dir / "report.json",
        task=args.task,
        arm=args.arm,
        model=args.model,
        base_url=args.base_url,
        judge_model=args.judge_model,
        judge_base_url=args.judge_base_url,
        limit=args.limit,
        usage_ledger=args.out_dir / "attempts.json",
        test_jsonl=args.out_dir / "input.jsonl",
    )
    if not memory_arm:
        subprocess.run(command, cwd=args.official_dir, env=env, check=True)
        return verify_results(args.out_dir)

    tenant_id, api_key = gate_runtime.provision_tenant(
        str(cargo_binary_path("memphant-cli")),
        database_url,
        f"memsyco-{args.task}-{args.arm}-{args.out_dir.name}",
    )
    env.update(
        {
            "MEMPHANT_MEMSYCO_TENANT_ID": tenant_id,
            "MEMPHANT_MEMSYCO_API_KEY": api_key,
            "MEMPHANT_MEMSYCO_PORT": str(args.port),
            "MEMPHANT_MEMSYCO_PROOF_DIR": str(args.out_dir / "memory"),
            "MEMPHANT_MEMSYCO_DATABASE_URL": database_url,
            "MEMPHANT_MEMSYCO_WORKER_BIN": str(
                cargo_binary_path("memphant-worker")
            ),
            "MEMPHANT_MEMSYCO_EMBED_MODEL": args.embed_model,
            "MEMPHANT_MEMSYCO_STRUCTURED_STATE": (
                "on" if args.arm == "memphant" else "off"
            ),
            "MEMPHANT_STRUCTURED_STATE": (
                "on" if args.arm == "memphant" else "off"
            ),
        }
    )
    if args.arm == "memphant":
        env["MEMPHANT_MEMSYCO_EXTRACTOR_LEDGER"] = str(
            args.out_dir / "extractor-attempts.jsonl"
        )
        env["MEMPHANT_MEMSYCO_EXTRACTOR_MODEL"] = str(extractor_model)
        env["MEMPHANT_STRUCTURED_STATE_MODEL"] = str(extractor_model)
        env["MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER"] = str(
            args.out_dir / "extractor-attempts.jsonl"
        )
    previous_state = os.environ.get("MEMPHANT_STRUCTURED_STATE")
    previous_ledger = os.environ.get("MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER")
    if args.arm == "episode_only":
        os.environ["MEMPHANT_STRUCTURED_STATE"] = "off"
        os.environ.pop("MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER", None)
    else:
        os.environ["MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER"] = str(
            args.out_dir / "extractor-attempts.jsonl"
        )
    server = gate_runtime.Server(
        str(cargo_binary_path("memphant-server")),
        database_url,
        args.port,
        args.embed_model,
        log_path=args.out_dir / "memphant-server.log",
    )
    try:
        server.start()
        subprocess.run(command, cwd=args.official_dir, env=env, check=True)
    finally:
        server.stop()
        if previous_state is None:
            os.environ.pop("MEMPHANT_STRUCTURED_STATE", None)
        else:
            os.environ["MEMPHANT_STRUCTURED_STATE"] = previous_state
        if previous_ledger is None:
            os.environ.pop("MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER", None)
        else:
            os.environ["MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER"] = previous_ledger
    return verify_results(args.out_dir)


def main() -> int:
    args = build_parser().parse_args()
    lock = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.command == "acquire":
        result = acquire(args.directory, lock)
    elif args.command == "verify":
        result = verify_official(args.official_dir, lock)
    elif args.command == "run":
        result = run_smoke(args, lock)
    elif args.command == "verify-results":
        result = verify_results(args.run_dir)
    else:
        result = score_official(
            official_dir=args.official_dir,
            gate_path=args.gate,
            freeze_path=args.candidate_freeze,
            memphant_dir=args.memphant_dir,
            raw_dialogue_dir=args.raw_dialogue_dir,
            out=args.out,
            lock=lock,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
