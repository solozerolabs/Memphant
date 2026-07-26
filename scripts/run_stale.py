#!/usr/bin/env python3
"""Run the pinned official STALE scorer over externally produced answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_attempts import (
    load_provider_attempt_ledger_snapshot,
    validate_provider_attempt_ledger,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks" / "manifests" / "stale.lock.json"
GENERATION_MANIFEST = ROOT / "benchmarks" / "manifests" / "stale_generation.v1.json"
HARNESS_BOOTSTRAP = ROOT / "benchmarks" / "stale" / "harness_bootstrap.py"
PROOF_HARNESS_FILES = {
    "runner": Path(__file__),
    "stale_bootstrap": HARNESS_BOOTSTRAP,
    "provider_attempts": ROOT / "scripts" / "provider_attempts.py",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_records(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("data")
    if not isinstance(value, list):
        raise ValueError(
            f"{path} must contain a JSON list or an object with a data list"
        )
    return value


def verify_official_repo(
    repo: Path, manifest: dict, *, verify_revision: bool = True
) -> None:
    if verify_revision:
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        expected = manifest["code"]["revision"]
        if revision != expected:
            raise ValueError(
                f"official STALE revision mismatch: {revision} != {expected}"
            )

    for relative, expected in manifest["native_scorer"]["files"].items():
        path = repo / relative
        actual = sha256_file(path) if path.is_file() else "missing"
        if actual != expected:
            raise ValueError(f"official STALE source hash mismatch for {relative}")

    requirements = repo / "STALE" / "requirements.txt"
    expected = manifest["native_scorer"].get("requirements_sha256")
    if expected and sha256_file(requirements) != expected:
        raise ValueError("official STALE requirements hash mismatch")


def verify_dataset(path: Path, manifest: dict) -> list[dict]:
    locked = manifest["dataset"]
    if path.stat().st_size != locked["size_bytes"]:
        raise ValueError("official STALE dataset size mismatch")
    if sha256_file(path) != locked["sha256"]:
        raise ValueError("official STALE dataset hash mismatch")
    rows = load_records(path)
    if len(rows) != locked["record_count"]:
        raise ValueError("official STALE dataset record count mismatch")
    return rows


def verify_answers(
    dataset: list[dict], answers: list[dict], *, smoke: bool = False
) -> None:
    dataset_ids = [row.get("uid") for row in dataset]
    answer_ids = [row.get("uid") for row in answers]
    if len(answer_ids) != len(set(answer_ids)):
        raise ValueError("STALE answers contain a duplicate uid")
    if smoke:
        if not answer_ids or len(answer_ids) >= len(dataset_ids):
            raise ValueError("STALE smoke answers must be a non-empty strict subset")
        if answer_ids != dataset_ids[: len(answer_ids)]:
            raise ValueError("STALE smoke answers must match the pinned dataset prefix")
    elif set(answer_ids) != set(dataset_ids) or len(answer_ids) != len(dataset_ids):
        raise ValueError("STALE answer IDs must exactly match dataset IDs")

    for row in answers:
        responses = row.get("target_model_responses")
        if not isinstance(responses, dict):
            raise ValueError(f"answer {row.get('uid')} lacks target_model_responses")
        for dimension in ("dim1_response", "dim2_response", "dim3_response"):
            value = responses.get(dimension)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"answer {row.get('uid')} {dimension} must be a non-empty string"
                )


def verify_smoke_contract(
    summary: object, answers: list[dict], dataset_count: int
) -> None:
    expected = {
        "num_items": len(answers),
        "expected_items": len(answers),
        "source_record_count": dataset_count,
        "smoke_only": True,
        "promotion_ineligible": True,
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != value for key, value in expected.items()
    ):
        raise ValueError(
            "STALE smoke answers lack the explicit promotion-ineligible contract"
        )


def require_fresh_smoke_result(args: argparse.Namespace) -> None:
    if not args.smoke:
        return
    paths = (
        args.out,
        args.out.with_suffix(args.out.suffix + ".attempts.json"),
        args.out.with_suffix(args.out.suffix + ".proof.json"),
    )
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise ValueError(
            "STALE fresh smoke requires new judge result paths; found: "
            + ", ".join(existing)
        )


def verify_native_result(result: dict, expected_ids: set[str]) -> None:
    details = result.get("details")
    if not isinstance(details, list):
        raise ValueError("official STALE result lacks details")
    result_ids = [row.get("uid") for row in details]
    if (
        len(result_ids) != len(set(result_ids))
        or set(result_ids) != expected_ids
        or result.get("config", {}).get("num_samples") != len(expected_ids)
    ):
        raise ValueError("official STALE result IDs must exactly match answer IDs")
    errors = [row["uid"] for row in details if row.get("judge_meta", {}).get("error")]
    if errors:
        raise RuntimeError(
            f"official STALE scorer recorded a judge error for {errors[0]}"
        )
    for row in details:
        metadata = row.get("judge_meta")
        usage = metadata.get("usage") if isinstance(metadata, dict) else None
        if (
            not isinstance(metadata, dict)
            or isinstance(metadata.get("elapsed_seconds"), bool)
            or not isinstance(metadata.get("elapsed_seconds"), (int, float))
            or metadata["elapsed_seconds"] < 0
            or not isinstance(usage, dict)
            or type(usage.get("prompt_tokens")) is not int
            or usage["prompt_tokens"] <= 0
            or type(usage.get("completion_tokens")) is not int
            or usage["completion_tokens"] <= 0
            or usage.get("total_tokens")
            != usage["prompt_tokens"] + usage["completion_tokens"]
        ):
            raise ValueError("official STALE result lacks complete judge metadata")


def verify_reader_provenance(
    generation_proof: dict,
    sample_count: int,
    answers: list[dict],
    reader_contract: dict,
) -> list[str]:
    reader_ledger = generation_proof.get("provider_attempt_ledger")
    if not isinstance(reader_ledger, dict):
        raise ValueError("STALE smoke lacks reader provider-attempt ledger")
    validate_provider_attempt_ledger(reader_ledger)
    if (
        generation_proof.get("provider_attempt_ledger_sha256")
        != reader_ledger.get("attempts_sha256")
        or reader_ledger.get("provider_attempts") != sample_count * 3
    ):
        raise ValueError("STALE smoke provider-attempt count or hash mismatch")
    records = generation_proof.get("records")
    if not isinstance(records, list) or len(records) != sample_count:
        raise ValueError("STALE smoke generation/result count mismatch")
    answer_by_uid = {row.get("uid"): row for row in answers}
    if (
        len(answer_by_uid) != sample_count
        or any(
            row.get("target_model") != reader_contract.get("canonical_model_snapshot")
            for row in answers
        )
    ):
        raise ValueError("STALE smoke answer UID count mismatch")
    reader_responses = {
        row["result"]["response"]["response_id"]: row["result"]["response"]
        for row in reader_ledger["attempts"]
    }
    if any(
        response.get("requested_model") != reader_contract.get("requested_model")
        or response.get("served_model")
        != reader_contract.get("canonical_model_snapshot")
        for response in reader_responses.values()
    ):
        raise ValueError("STALE smoke reader model mismatch")
    if any(response.get("retry_index") != 0 for response in reader_responses.values()):
        raise ValueError("STALE smoke contains a hidden or explicit retry")
    archived_response_ids = []
    for record in records:
        uid = record.get("uid")
        answer = answer_by_uid.get(uid)
        if answer is None or record.get("answer_row_sha256") != sha256_json(answer):
            raise ValueError("STALE smoke answer/proof hash mismatch")
        dimensions = record.get("dimensions")
        if not isinstance(dimensions, dict) or len(dimensions) != 3:
            raise ValueError("STALE smoke trace count mismatch")
        for facts in dimensions.values():
            attempts = facts.get("provider_attempts")
            archived = (
                attempts[0].get("response")
                if isinstance(attempts, list)
                and len(attempts) == 1
                and isinstance(attempts[0], dict)
                else None
            )
            response_id = facts.get("response_id")
            if (
                facts.get("degraded") is not False
                or facts.get("parse_status") != "parsed"
                or not isinstance(archived, dict)
                or archived.get("response_id") != response_id
                or reader_responses.get(response_id) != archived
            ):
                raise ValueError("STALE smoke has degraded, parse, or attempt rows")
            archived_response_ids.append(response_id)
    if set(archived_response_ids) != set(reader_responses):
        raise ValueError("STALE smoke attempt archive does not cover its reader ledger")
    return list(reader_responses)


def verify_judge_provenance(
    judge_ledger: dict,
    sample_count: int,
    judge_model: str,
    reader_response_ids: list[str],
) -> None:
    validate_provider_attempt_ledger(judge_ledger)
    if judge_ledger.get("provider_attempts") != sample_count:
        raise ValueError("STALE smoke judge attempt count mismatch")
    judge_rows = [row["result"] for row in judge_ledger["attempts"]]
    judge_responses = [row["response"] for row in judge_rows]
    if any(
        row["response"].get("requested_model") != judge_model
        or row.get("context") != {"benchmark": "STALE", "arm": "judge"}
        for row in judge_rows
    ):
        raise ValueError("STALE smoke judge model or context mismatch")
    if any(response.get("retry_index") != 0 for response in judge_responses):
        raise ValueError("STALE smoke contains a hidden or explicit retry")
    response_ids = reader_response_ids + [
        response["response_id"] for response in judge_responses
    ]
    if len(response_ids) != len(set(response_ids)):
        raise ValueError("STALE smoke contains duplicate response IDs")


def verify_smoke_provenance(
    generation_proof: dict,
    judge_ledger: dict,
    sample_count: int,
    answers: list[dict],
    reader_contract: dict,
    judge_model: str,
) -> None:
    reader_response_ids = verify_reader_provenance(
        generation_proof, sample_count, answers, reader_contract
    )
    verify_judge_provenance(
        judge_ledger, sample_count, judge_model, reader_response_ids
    )


def download_dataset(cache_dir: Path, manifest: dict) -> Path:
    locked = manifest["dataset"]
    destination = cache_dir / locked["revision"] / locked["file"]
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = (
        f"https://huggingface.co/datasets/{locked['repo']}/resolve/"
        f"{locked['revision']}/{locked['file']}"
    )
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                while chunk := response.read(1024 * 1024):
                    temp.write(chunk)
            os.replace(temp_path, destination)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate inputs and invoke STALE's pinned native scorer."
    )
    parser.add_argument("--official-repo", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "memphant-bench" / "stale",
    )
    parser.add_argument("--model-method", default="memphant")
    parser.add_argument("--conflict-type", default="T1_T2")
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-provider")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--judge-max-output-tokens", type=int, default=4096)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="accept only a deterministic promotion-ineligible prefix smoke",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    verify_official_repo(args.official_repo, manifest)
    dataset_path = args.dataset or download_dataset(args.cache_dir, manifest)
    dataset = verify_dataset(dataset_path, manifest)
    answer_payload = json.loads(args.answers.read_text(encoding="utf-8"))
    answers = load_records(args.answers)
    native = manifest["native_scorer"]
    generation_proof = None
    reader_response_ids: list[str] = []
    if args.smoke:
        verify_smoke_contract(
            answer_payload.get("summary") if isinstance(answer_payload, dict) else None,
            answers,
            len(dataset),
        )
    verify_answers(dataset, answers, smoke=args.smoke)
    if args.smoke:
        generation_proof_path = args.answers.with_suffix(
            args.answers.suffix + ".proof.json"
        )
        if not generation_proof_path.is_file():
            raise ValueError("STALE smoke lacks generation attempt proof")
        generation_proof = json.loads(
            generation_proof_path.read_text(encoding="utf-8")
        )
        reader_contract = json.loads(
            GENERATION_MANIFEST.read_text(encoding="utf-8")
        )["reader"]
        reader_response_ids = verify_reader_provenance(
            generation_proof, len(answers), answers, reader_contract
        )
        require_fresh_smoke_result(args)
    if args.verify_only:
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    judge_ledger_path = args.out.with_suffix(args.out.suffix + ".attempts.json")
    command = [
        sys.executable,
        str(HARNESS_BOOTSTRAP),
        "--official-repo",
        str(args.official_repo.resolve()),
        "--attempt-ledger",
        str(judge_ledger_path.resolve()),
        "--max-output-tokens",
        str(args.judge_max_output_tokens),
        "--answers-path",
        str(args.answers.resolve()),
        "--dataset-path",
        str(dataset_path.resolve()),
        "--output-path",
        str(args.out.resolve()),
        "--model-method",
        args.model_method,
        "--conflict-type",
        args.conflict_type,
        "--judge-model",
        args.judge_model or native["default_judge_model"],
        "--judge-provider",
        args.judge_provider or native["default_judge_provider"],
        "--concurrency",
        str(args.concurrency),
    ]
    child_env = dict(os.environ)
    if (args.judge_provider or native["default_judge_provider"]) == "OPENAI":
        openrouter_key = child_env.get("OPENROUTER_API_KEY")
        if openrouter_key:
            child_env["OPENAI_API_KEY"] = openrouter_key
            child_env["OPENAI_BASE_URL"] = "https://openrouter.ai/api/v1"
    subprocess.run(
        command, cwd=args.official_repo / "STALE", env=child_env, check=True
    )
    result = json.loads(args.out.read_text(encoding="utf-8"))
    expected_ids = {row["uid"] for row in answers}
    verify_native_result(result, expected_ids)
    if args.smoke:
        if not judge_ledger_path.is_file():
            raise ValueError("STALE smoke lacks judge attempt proof")
        judge_snapshot = load_provider_attempt_ledger_snapshot(judge_ledger_path)
        verify_judge_provenance(
            judge_snapshot,
            len(expected_ids),
            args.judge_model or native["default_judge_model"],
            reader_response_ids,
        )
        result["smoke_only"] = True
        result["promotion_ineligible"] = True
        args.out.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    proof = {
        "answers_sha256": sha256_file(args.answers),
        "benchmark": "STALE",
        "command": command,
        "dataset_sha256": sha256_file(dataset_path),
        "judge_model": args.judge_model or native["default_judge_model"],
        "judge_provider": args.judge_provider or native["default_judge_provider"],
        "harness_sha256": {
            name: sha256_file(path) for name, path in PROOF_HARNESS_FILES.items()
        },
        "manifest_sha256": sha256_file(args.manifest),
        "native_result_sha256": sha256_file(args.out),
        "official_revision": manifest["code"]["revision"],
        "sample_count": len(expected_ids),
        "smoke_only": args.smoke,
        "promotion_ineligible": args.smoke,
        "judge_attempt_ledger_sha256": (
            judge_snapshot["attempts_sha256"] if args.smoke else None
        ),
    }
    args.out.with_suffix(args.out.suffix + ".proof.json").write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
