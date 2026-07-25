#!/usr/bin/env python3
"""Build the deterministic, bounded SWE-ContextBench evidence bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import zlib


EXECUTED_RUNNER_SHA256 = "f7a8e122310d0e7449fb2d2d4e9efdef4db7257b631b7e8149c0768607dd72e7"
EXECUTED_AUTHORIZATION_SHA256 = "38ae85494e7e11af529a39e39e31227314d810e733200cd948cf4ee6f480d7de"
RUNNER_BASE_COMMIT = "c70feead5b748383c587fc5f46b15161ff92af12"
RUNNER_PATH = "scripts/run_swe_contextbench_agent_gate.py"
AUTHORIZATION_PATH = (
    "docs/build-log/artifacts/next-evidence/coding/swe-contextbench-authorization.json"
)
MAX_SOURCE_BYTES = 16 * 1024 * 1024
TEST_PARTS = {"test", "tests", "testing", "spec", "specs", "__tests__"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def executed_runner(repo: Path) -> bytes:
    source = subprocess.run(
        ["git", "show", f"{RUNNER_BASE_COMMIT}:{RUNNER_PATH}"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    start = source.index(b"def baseline_ceiling_verdict")
    end = source.index(b"def run_checked", start)
    snapshot = source[:start] + source[end:]
    if sha256(snapshot) != EXECUTED_RUNNER_SHA256:
        raise RuntimeError("executed runner reconstruction drift")
    return snapshot


def git_snapshot(repo: Path, path: str, expected_sha256: str) -> bytes:
    snapshot = subprocess.run(
        ["git", "show", f"{RUNNER_BASE_COMMIT}:{path}"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    if sha256(snapshot) != expected_sha256:
        raise RuntimeError(f"executed snapshot drift: {path}")
    return snapshot


def read_bounded(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) > MAX_SOURCE_BYTES:
        raise RuntimeError(f"evidence source exceeds byte limit: {path}")
    return data


def entry(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "decoded_bytes": len(data),
        "decoded_sha256": sha256(data),
        "zlib_base64": base64.b64encode(zlib.compress(data, level=9)).decode("ascii"),
    }


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def changed_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        fields = shlex.split(line)
        if len(fields) != 4 or not fields[2].startswith("a/") or not fields[3].startswith("b/"):
            raise RuntimeError("invalid diff path header in preserved prediction")
        paths.extend((fields[2][2:], fields[3][2:]))
    return sorted(set(paths))


def is_test_path(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name.lower()
    test_configs = {"conftest.py", "pytest.ini", "tox.ini", ".rspec"}
    return (
        any(part.lower() in TEST_PARTS for part in parts)
        or name in test_configs
        or bool(re.match(r"^(jest|vitest|karma)\.(config|conf)\.", name))
        or bool(re.search(r"(^|[._-])(test|tests|spec|specs)([._-]|$)", name))
    )


def build(repo: Path, source_root: Path) -> dict[str, object]:
    entries = [
        entry(
            "executed/swe-contextbench-authorization.json",
            git_snapshot(repo, AUTHORIZATION_PATH, EXECUTED_AUTHORIZATION_SHA256),
        ),
        entry("executed/run_swe_contextbench_agent_gate.py", executed_runner(repo)),
    ]
    attempts = read_bounded(source_root / "attempts.jsonl")
    entries.append(entry("execution/attempts.jsonl", attempts))
    attempt_rows = [json.loads(line) for line in attempts.splitlines()]
    if len(attempt_rows) != 12:
        raise RuntimeError("expected 12 attempt rows")

    manifest = json.loads(
        (repo / "benchmarks/manifests/swe_contextbench.kill.n12.json").read_text(
            encoding="utf-8"
        )
    )
    reference_hashes = {
        case["target_id"]: case["target_patch_sha256"] for case in manifest["cases"]
    }
    prediction_rows = []
    for path in sorted((source_root / "predictions").glob("*/*_preds.json")):
        target_id = path.name.removesuffix("_preds.json")
        arm = path.parent.name
        prediction = json.loads(path.read_text(encoding="utf-8"))[target_id]
        patch = prediction["model_patch"]
        patch_sha256 = sha256(patch.encode())
        paths = changed_paths(patch)
        prediction_rows.append(
            {
                "target_id": target_id,
                "arm": arm,
                "model_patch_sha256": patch_sha256,
                "model_patch_bytes": len(patch.encode()),
                "changed_paths": paths,
                "test_path_violations": sum(is_test_path(value) for value in paths),
                "reference_patch_sha256_match": patch_sha256
                == reference_hashes[target_id],
            }
        )

    usage_rows = []
    for path in sorted((source_root / "raw").glob("*.jsonl")):
        raw = read_bounded(path)
        events = [json.loads(line) for line in raw.splitlines()]
        usages = [event["usage"] for event in events if event["type"] == "turn.completed"]
        if len(usages) != 1:
            raise RuntimeError(f"expected one terminal usage event: {path}")
        target_id, arm = path.name.removesuffix(".jsonl").rsplit(".", 1)
        usage_rows.append(
            {
                "target_id": target_id,
                "arm": arm,
                "raw_output_sha256": sha256(raw),
                "usage": usages[0],
            }
        )

    report_root = (
        source_root
        / "evaluation/no_memory/logs/run_evaluation/memphant_n12_no_memory/gpt-5.6-sol"
    )
    grade_rows = []
    for path in sorted(report_root.glob("*/report.json")):
        raw = read_bounded(path)
        report = json.loads(raw)
        grade_rows.append(
            {
                "instance_id": report["instance_id"],
                "report_sha256": sha256(raw),
                "resolved": report["resolved"],
                "patch_applied": report["patch_applied"],
                "fail_to_pass_passed": len(report["tests_status"]["FAIL_TO_PASS"]["success"]),
                "fail_to_pass_failed": len(report["tests_status"]["FAIL_TO_PASS"]["failure"]),
                "pass_to_pass_passed": len(report["tests_status"]["PASS_TO_PASS"]["success"]),
                "pass_to_pass_failed": len(report["tests_status"]["PASS_TO_PASS"]["failure"]),
            }
        )
    if not (len(prediction_rows) == len(usage_rows) == 12 and len(grade_rows) == 3):
        raise RuntimeError("incomplete coding evidence projections")
    entries.extend(
        (
            entry("execution/prediction-projections.json", json_bytes(prediction_rows)),
            entry("execution/usage-projections.json", json_bytes(usage_rows)),
            entry("grading/report-projections.json", json_bytes(grade_rows)),
        )
    )
    entries.sort(key=lambda value: str(value["path"]))
    return {
        "schema_version": 1,
        "compression": "zlib+base64-per-entry",
        "entry_count": len(entries),
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.repo.resolve(), args.source_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
