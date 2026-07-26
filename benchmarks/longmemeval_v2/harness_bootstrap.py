#!/usr/bin/env python3
"""Register MemPhant, then enter the pinned upstream evaluation harness."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def _canonical_sha256(value: object) -> str:
    body = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def deferred_score_prediction(
    row: dict[str, object], _eval_config: dict[str, object]
) -> tuple[bool, str, bool]:
    """Let upstream persist its reader row without invoking any evaluator."""
    return False, "memphant_deferred_native_scoring", bool(row.get("is_unknown"))


def _argument_value(arguments: list[str], name: str) -> str:
    if arguments.count(name) != 1:
        raise RuntimeError(f"deferred scoring requires exactly one {name}")
    try:
        index = arguments.index(name)
        value = arguments[index + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"deferred scoring requires {name}") from error
    if not value or value.startswith("--"):
        raise RuntimeError(f"deferred scoring requires {name}")
    return value


def write_deferred_scoring_proof(
    proof_path: Path,
    harness_args: list[str],
    *,
    official_dir: Path,
    adapter_path: Path,
    private_root: Path,
) -> dict[str, object]:
    output_dir = Path(_argument_value(harness_args, "--output-dir")).resolve()
    proof_path = proof_path.resolve()
    private_root = private_root.resolve()
    if (
        proof_path.parent != output_dir
        or not output_dir.is_relative_to(private_root)
    ):
        raise RuntimeError("deferred scoring proof must live in the private harness output")
    per_question = output_dir / "per_question.jsonl"
    prompt_rows = output_dir / "prompt_rows.jsonl"
    if not per_question.is_file() or not prompt_rows.is_file():
        raise RuntimeError("deferred scoring output is incomplete")
    records = [
        json.loads(line)
        for line in per_question.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(records) != 1 or records[0].get("score_bool") is not False:
        raise RuntimeError("deferred scoring requires exactly one provisional row")
    private_row = {
        key: value
        for key, value in records[0].items()
        if key not in {"score", "score_bool", "timestamp_utc"}
    }
    reader_output = output_dir / "READER-OUTPUT.private.json"
    with reader_output.open("x", encoding="utf-8") as handle:
        json.dump(private_row, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    reader_output.chmod(0o600)
    for provisional in (
        per_question,
        output_dir / "aggregated_metrics.json",
        output_dir / "per_question.jsonl",
    ):
        provisional.unlink(missing_ok=True)
    directory = os.open(output_dir, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    harness_path = official_dir / "evaluation/harness.py"
    qa_path = official_dir / "evaluation/qa_eval_metrics.py"
    if not harness_path.is_file() or not qa_path.is_file() or not adapter_path.is_file():
        raise RuntimeError("deferred scoring code authority is incomplete")
    core = {
        "schema_version": 1,
        "status": "READER_COMPLETE_SCORING_DEFERRED",
        "question_id": records[0].get("question_id"),
        "reader_output_sha256": hashlib.sha256(reader_output.read_bytes()).hexdigest(),
        "prompt_rows_sha256": hashlib.sha256(prompt_rows.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(harness_path.read_bytes()).hexdigest(),
        "qa_eval_metrics_sha256": hashlib.sha256(qa_path.read_bytes()).hexdigest(),
        "adapter_sha256": hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
        "harness_args_sha256": _canonical_sha256(harness_args),
        "official_metrics_eligible": False,
        "native_scoring_deferred": True,
    }
    proof = {**core, "proof_sha256": _canonical_sha256(core)}
    output_dir.chmod(0o700)
    with proof_path.open("x", encoding="utf-8") as handle:
        json.dump(proof, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    proof_path.chmod(0o600)
    return proof


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--official-dir", type=Path, required=True)
    parser.add_argument("--memphant-defer-scoring-proof", type=Path)
    bootstrap_args, harness_args = parser.parse_known_args()
    official_dir = bootstrap_args.official_dir.resolve()
    if not (official_dir / "evaluation/harness.py").is_file():
        raise RuntimeError(f"pinned upstream harness is missing: {official_dir}")
    sys.path.insert(0, str(official_dir))

    adapter_path = Path(__file__).with_name("memphant_memory.py")
    private_root: Path | None = None
    if bootstrap_args.memphant_defer_scoring_proof is not None:
        configured_root = os.environ.get("MEMPHANT_LME_PRIVATE_ROOT", "")
        if not configured_root:
            raise RuntimeError("deferred scoring requires MEMPHANT_LME_PRIVATE_ROOT")
        configured_private_root = Path(configured_root).absolute()
        configured_output_dir = Path(
            _argument_value(harness_args, "--output-dir")
        ).absolute()
        if configured_private_root.is_symlink() or configured_output_dir.is_symlink():
            raise RuntimeError("deferred scoring private paths cannot be symlinks")
        private_root = configured_private_root.resolve()
        output_dir = configured_output_dir.resolve()
        if not output_dir.is_relative_to(private_root):
            raise RuntimeError("deferred scoring output escapes the private root")
        private_root.mkdir(parents=True, exist_ok=True)
        private_root.chmod(0o700)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir.chmod(0o700)
    spec = importlib.util.spec_from_file_location("longmemeval_v2_memphant_memory", adapter_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load MemPhant adapter: {adapter_path}")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    import evaluation.harness as harness

    if bootstrap_args.memphant_defer_scoring_proof is not None:
        harness.score_prediction = deferred_score_prediction

    sys.argv = ["evaluation.harness", *harness_args]
    harness.main()
    if bootstrap_args.memphant_defer_scoring_proof is not None:
        write_deferred_scoring_proof(
            bootstrap_args.memphant_defer_scoring_proof,
            harness_args,
            official_dir=official_dir,
            adapter_path=adapter_path,
            private_root=private_root,
        )


if __name__ == "__main__":
    main()
