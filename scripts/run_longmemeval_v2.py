#!/usr/bin/env python3
"""Acquire and run the pinned official LongMemEval-V2 benchmark.

This adapter owns only release integrity and process orchestration. Generation,
answer parsing, LLM judging, exact metrics, and aggregation remain entirely in
the pinned upstream ``evaluation/harness.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/longmemeval_v2.lock.json"
MEMPHANT_BOOTSTRAP = ROOT / "benchmarks/longmemeval_v2/harness_bootstrap.py"
MEMORY_CONTEXT_MAX_TOKENS = 200000
READER_TEMPERATURE = 0.6
READER_TOP_P = 0.95
READER_TOP_K = 20
EVALUATOR_REASONING_EFFORT = "medium"
MATRIX_DOMAINS = {"web", "enterprise"}
MATRIX_TIERS = {"small", "medium"}
MATRIX_ARMS = {"memphant_fast", "memphant_deep", "no_retrieval"}


def release_urls(lock: dict) -> dict[str, str]:
    commit = lock["code"]["commit"]
    dataset = lock["dataset"]
    return {
        "code_archive": (
            f"https://github.com/xiaowu0162/LongMemEval-V2/archive/{commit}.tar.gz"
        ),
        "dataset_revision": (
            "https://huggingface.co/datasets/"
            f"{dataset['repository']}/tree/{dataset['revision']}"
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_code(path: Path, expected_files: dict[str, str]) -> None:
    for relative, expected_sha in expected_files.items():
        file_path = path / relative
        if not file_path.is_file():
            raise RuntimeError(f"official code file missing: {relative}")
        actual_sha = _sha256(file_path)
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"official code drift: {relative}; expected {expected_sha}, got {actual_sha}"
            )


def _parse_checksum_file(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise RuntimeError(f"invalid upstream checksum entry at line {line_number}")
        relative = parts[1].lstrip("* ")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or not relative:
            raise RuntimeError(f"unsafe upstream checksum path at line {line_number}")
        if relative in entries:
            raise RuntimeError(f"duplicate upstream checksum path: {relative}")
        entries[relative] = parts[0]
    return entries


def _verify_file(path: Path, *, relative: str, sha256: str, bytes_: int | None) -> None:
    if not path.is_file():
        raise RuntimeError(f"dataset file missing: {relative}")
    if bytes_ is not None and path.stat().st_size != bytes_:
        raise RuntimeError(f"dataset byte count mismatch: {relative}")
    actual_sha = _sha256(path)
    if actual_sha != sha256:
        raise RuntimeError(
            f"dataset sha256 mismatch: {relative}; expected {sha256}, got {actual_sha}"
        )


def verify_dataset(data_root: Path, expected: dict) -> dict[str, int]:
    checksum_spec = expected["checksums_file"]
    checksum_path = data_root / checksum_spec["path"]
    if not checksum_path.is_file():
        raise RuntimeError(f"dataset file missing: {checksum_spec['path']}")
    actual_checksum_sha = _sha256(checksum_path)
    if actual_checksum_sha != checksum_spec["sha256"]:
        raise RuntimeError(
            "checksums file sha256 mismatch: "
            f"expected {checksum_spec['sha256']}, got {actual_checksum_sha}"
        )

    upstream_entries = _parse_checksum_file(checksum_path)
    if len(upstream_entries) != checksum_spec["entries"]:
        raise RuntimeError(
            "upstream checksum entry count mismatch: "
            f"expected {checksum_spec['entries']}, got {len(upstream_entries)}"
        )
    exceptions = expected.get("checksum_exceptions", {})
    if set(exceptions) - set(upstream_entries):
        raise RuntimeError("dataset checksum exception is not an upstream entry")
    for relative, expected_sha in upstream_entries.items():
        exception = exceptions.get(relative)
        if exception is not None:
            if exception["upstream_sha256"] != expected_sha:
                raise RuntimeError(f"upstream checksum exception drift: {relative}")
            expected_sha = exception["snapshot_sha256"]
        _verify_file(
            data_root / relative,
            relative=relative,
            sha256=expected_sha,
            bytes_=None,
        )

    for relative, file_spec in expected["files"].items():
        _verify_file(
            data_root / relative,
            relative=relative,
            sha256=file_spec["sha256"],
            bytes_=file_spec["bytes"],
        )
    return {
        "upstream_checksum_entries": len(upstream_entries),
        "upstream_checksum_exceptions": len(exceptions),
        "separately_locked_files": len(expected["files"]),
    }


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "MemPhant-LME-V2"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _extract_archive(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise RuntimeError("unexpected official code archive layout")
    return roots[0]


def acquire(directory: Path, lock: dict, *, python: str = sys.executable) -> dict[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    official_dir = directory / "official"
    data_root = directory / "data"
    urls = release_urls(lock)

    with tempfile.TemporaryDirectory(dir=directory) as temp_name:
        temp = Path(temp_name)
        if official_dir.exists():
            verify_code(official_dir, lock["code"]["files"])
        else:
            archive = temp / "official.tar.gz"
            extracted = temp / "extracted"
            extracted.mkdir()
            _download(urls["code_archive"], archive)
            root = _extract_archive(archive, extracted)
            verify_code(root, lock["code"]["files"])
            root.replace(official_dir)

        if data_root.exists() and any(data_root.iterdir()):
            return verify_dataset(data_root, lock["dataset"])

        subprocess.run(
            [
                python,
                str(official_dir / "data/download_data.py"),
                "--repo-id",
                lock["dataset"]["repository"],
                "--revision",
                lock["dataset"]["revision"],
                "--data-root",
                str(data_root),
            ],
            cwd=official_dir,
            check=True,
        )
    return verify_dataset(data_root, lock["dataset"])


def native_harness_command(
    *,
    official_dir: Path,
    domain: str,
    questions_path: Path,
    haystack_path: Path,
    trajectories_path: Path,
    memory_config_path: Path,
    output_dir: Path,
    reader_model: str,
    reader_base_url: str,
    evaluator_model: str,
    evaluator_base_url: str,
    python: str = sys.executable,
) -> list[str]:
    if domain not in {"web", "enterprise"}:
        raise ValueError("domain must be web or enterprise")
    return [
        python,
        str(official_dir / "evaluation/harness.py"),
        "--domain",
        domain,
        "--questions-path",
        str(questions_path),
        "--haystack-path",
        str(haystack_path),
        "--trajectories-path",
        str(trajectories_path),
        "--memory-config-path",
        str(memory_config_path),
        "--output-dir",
        str(output_dir),
        "--model",
        reader_model,
        "--base-url",
        reader_base_url,
        "--temperature",
        str(READER_TEMPERATURE),
        "--top-p",
        str(READER_TOP_P),
        "--top-k",
        str(READER_TOP_K),
        "--memory-context-max-tokens",
        str(MEMORY_CONTEXT_MAX_TOKENS),
        "--evaluator-model",
        evaluator_model,
        "--evaluator-base-url",
        evaluator_base_url,
        "--evaluator-reasoning-effort",
        EVALUATOR_REASONING_EFFORT,
    ]


def memphant_harness_command(**kwargs: object) -> list[str]:
    official_dir = Path(kwargs["official_dir"])
    command = native_harness_command(**kwargs)
    return [
        str(kwargs.get("python", sys.executable)),
        str(MEMPHANT_BOOTSTRAP),
        "--official-dir",
        str(official_dir),
        *command[2:],
    ]


def verify_execution_matrix(matrix: dict) -> dict[str, int]:
    """Validate complete, same-reader Fast/Deep/control leaderboard evidence."""
    if matrix.get("schema_version") != 1 or matrix.get("benchmark") != "LongMemEval-V2":
        raise RuntimeError("LongMemEval-V2 execution matrix contract drift")
    if matrix.get("upstream_release_lock_sha256") != _sha256(DEFAULT_MANIFEST):
        raise RuntimeError("LongMemEval-V2 execution matrix release lock drift")
    runs = matrix.get("runs")
    if not isinstance(runs, list):
        raise RuntimeError("LongMemEval-V2 execution matrix runs must be a list")
    expected = {
        (domain, tier, arm)
        for domain in MATRIX_DOMAINS
        for tier in MATRIX_TIERS
        for arm in MATRIX_ARMS
    }
    indexed: dict[tuple[str, str, str], dict] = {}
    for run in runs:
        if not isinstance(run, dict):
            raise RuntimeError("LongMemEval-V2 execution matrix run must be an object")
        key = (run.get("domain"), run.get("tier"), run.get("arm"))
        if key not in expected or key in indexed:
            raise RuntimeError(f"unexpected or duplicate execution matrix run: {key}")
        if run.get("memory_context_max_tokens") != MEMORY_CONTEXT_MAX_TOKENS:
            raise RuntimeError(f"execution matrix context budget drift: {key}")
        count = run.get("question_count")
        if not isinstance(count, int) or count <= 0 or run.get("completed_questions") != count:
            raise RuntimeError(f"execution matrix run is incomplete: {key}")
        if run.get("error_count") != 0:
            raise RuntimeError(f"execution matrix run recorded errors: {key}")
        for field in ("question_ids_sha256", "reader_contract_sha256", "judge_contract_sha256", "output_sha256"):
            value = run.get(field)
            if not isinstance(value, str) or len(value) != 64:
                raise RuntimeError(f"execution matrix run lacks {field}: {key}")
        if key[2].startswith("memphant_"):
            binaries = run.get("binaries")
            if not isinstance(binaries, dict) or set(binaries) != {"server", "cli"}:
                raise RuntimeError(f"MemPhant execution lacks binary fingerprints: {key}")
            for name, fingerprint in binaries.items():
                if not isinstance(fingerprint, dict):
                    raise RuntimeError(f"invalid {name} binary fingerprint: {key}")
                digest = fingerprint.get("sha256")
                if (
                    not isinstance(fingerprint.get("path"), str)
                    or not fingerprint["path"]
                    or not isinstance(fingerprint.get("bytes"), int)
                    or fingerprint["bytes"] <= 0
                    or not isinstance(digest, str)
                    or len(digest) != 64
                ):
                    raise RuntimeError(f"invalid {name} binary fingerprint: {key}")
        indexed[key] = run
    if set(indexed) != expected:
        raise RuntimeError(f"execution matrix is incomplete: missing={sorted(expected - set(indexed))}")
    for domain in MATRIX_DOMAINS:
        for tier in MATRIX_TIERS:
            control = indexed[(domain, tier, "no_retrieval")]
            paired_fields = (
                "question_count",
                "question_ids_sha256",
                "reader_contract_sha256",
                "judge_contract_sha256",
                "memory_context_max_tokens",
            )
            for arm in ("memphant_fast", "memphant_deep"):
                candidate = indexed[(domain, tier, arm)]
                if any(candidate[field] != control[field] for field in paired_fields):
                    raise RuntimeError(f"execution matrix arms are not paired: {(domain, tier)}")
    if len({run["reader_contract_sha256"] for run in runs}) != 1:
        raise RuntimeError("execution matrix reader contract drift")
    if len({run["judge_contract_sha256"] for run in runs}) != 1:
        raise RuntimeError("execution matrix judge contract drift")
    candidate_binaries = [
        run["binaries"] for run in runs if run["arm"].startswith("memphant_")
    ]
    if any(binaries != candidate_binaries[0] for binaries in candidate_binaries[1:]):
        raise RuntimeError("execution matrix MemPhant binary drift")
    return {"runs": len(runs), "paired_cells": len(MATRIX_DOMAINS) * len(MATRIX_TIERS)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("acquire", "verify", "run-native", "run-memphant", "verify-matrix")
    )
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--domain", choices=("web", "enterprise"))
    parser.add_argument("--questions-path", type=Path)
    parser.add_argument("--haystack-path", type=Path)
    parser.add_argument("--memory-config-path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--reader-model")
    parser.add_argument("--reader-base-url")
    parser.add_argument("--evaluator-model")
    parser.add_argument("--evaluator-base-url")
    args = parser.parse_args()

    lock = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.command == "verify-matrix":
        if args.matrix is None:
            parser.error("verify-matrix requires --matrix")
        audit = verify_execution_matrix(json.loads(args.matrix.read_text(encoding="utf-8")))
        print(json.dumps({"verified": True, **audit}))
        return 0
    if args.directory is None:
        parser.error(f"{args.command} requires --directory")
    if args.command == "acquire":
        audit = acquire(args.directory, lock)
    else:
        official_dir = args.directory / "official"
        data_root = args.directory / "data"
        verify_code(official_dir, lock["code"]["files"])
        audit = verify_dataset(data_root, lock["dataset"])
        if args.command in {"run-native", "run-memphant"}:
            required = {
                "domain": args.domain,
                "questions_path": args.questions_path,
                "haystack_path": args.haystack_path,
                "memory_config_path": args.memory_config_path,
                "output_dir": args.output_dir,
                "reader_model": args.reader_model,
                "reader_base_url": args.reader_base_url,
                "evaluator_model": args.evaluator_model,
                "evaluator_base_url": args.evaluator_base_url,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                parser.error("run-native requires: " + ", ".join(missing))
            command_builder = (
                memphant_harness_command
                if args.command == "run-memphant"
                else native_harness_command
            )
            command = command_builder(
                official_dir=official_dir,
                domain=args.domain,
                questions_path=args.questions_path,
                haystack_path=args.haystack_path,
                trajectories_path=data_root / "trajectories.jsonl",
                memory_config_path=args.memory_config_path,
                output_dir=args.output_dir,
                reader_model=args.reader_model,
                reader_base_url=args.reader_base_url,
                evaluator_model=args.evaluator_model,
                evaluator_base_url=args.evaluator_base_url,
            )
            subprocess.run(command, cwd=official_dir, check=True)
    print(json.dumps({"verified": True, "native_metrics": True, **audit}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
