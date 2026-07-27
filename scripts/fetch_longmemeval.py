#!/usr/bin/env python3
"""Fetch the LongMemEval dataset from Hugging Face and pin it by sha256.

Downloads `longmemeval_s` (preferred; ~40-50 haystack sessions per question)
and `longmemeval_oracle` (answer sessions only; reduced distractor pressure)
from the `xiaowu0162/longmemeval-cleaned` dataset repo into `benchmarks/data/`
(gitignored), verified against the committed lock manifest at
`benchmarks/manifests/longmemeval_s.lock.json`.

The fetcher never changes pins. Existing files are verified and re-downloaded
only on hash mismatch or absence; downloaded bytes are verified before replace.

Usage: python3 scripts/fetch_longmemeval.py [--oracle-only]
"""

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

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "benchmarks" / "data"
MANIFEST = ROOT / "benchmarks" / "manifests" / "longmemeval_s.lock.json"
SPLIT_MANIFEST = ROOT / "benchmarks" / "manifests" / "longmemeval_s.split.json"
REPO = "xiaowu0162/longmemeval-cleaned"
REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
FILES = ["longmemeval_s", "longmemeval_oracle"]
REMOTE_FILES = {
    "longmemeval_s": "longmemeval_s_cleaned.json",
    "longmemeval_oracle": "longmemeval_oracle.json",
}


def resolve_url(name: str) -> str:
    return (
        f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/"
        f"{REMOTE_FILES[name]}"
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def id_list_sha256(ids: set[str] | list[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode()).hexdigest()


def _question_ids(value: object) -> set[str]:
    if isinstance(value, dict):
        found = {
            item
            for key, item in value.items()
            if key == "question_id" and isinstance(item, str)
        }
        for item in value.values():
            found.update(_question_ids(item))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_question_ids(item))
        return found
    return set()


def _load_json_or_jsonl(raw: bytes, path: str) -> object:
    """Parse a committed artifact that may be a JSON document or a JSONL ledger.

    Some hash-chained attempt ledgers are committed as ``*.attempts.json`` even
    though they are line-delimited. This scan is a benchmark leak guard, so it
    must read their contents rather than crash on them — a guard that aborts
    covers nothing. Anything that parses as neither still raises.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    records = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(
                f"{path} parses as neither JSON nor JSONL (line {number})"
            ) from error
    return records


def exposed_question_ids(dataset_ids: set[str]) -> tuple[set[str], str, int, int]:
    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    paths = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
            "--",
            "docs/build-log/artifacts",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    json_paths = [path for path in paths if path.endswith(".json")]
    exposed: set[str] = set()
    matching_files = 0
    for path in json_paths:
        raw = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{path}"],
            check=True,
            capture_output=True,
        ).stdout
        found = _question_ids(_load_json_or_jsonl(raw, path)) & dataset_ids
        if found:
            matching_files += 1
            exposed.update(found)
    return exposed, commit, len(json_paths), matching_files


def build_split_manifest(dataset_path: Path) -> dict:
    raw = dataset_path.read_bytes()
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != 500:
        raise ValueError("cleaned LongMemEval-S must contain exactly 500 rows")
    by_id = {row["question_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("cleaned LongMemEval-S question IDs must be unique")
    for row in rows:
        if not set(row["answer_session_ids"]).issubset(row["haystack_session_ids"]):
            raise ValueError("answer sessions must be present in the question haystack")

    dataset_ids = set(by_id)
    exposed, commit, artifact_count, matching_count = exposed_question_ids(dataset_ids)
    # The development cohort is FROZEN, not a live recomputation: its ID-set
    # sha256 is a hash-bound term of benchmarks/manifests/reader_lattices.v1.json
    # and the recorded validity transform, so recomputing it would silently
    # redefine the cohort and break same-lattice comparability. Exposure keeps
    # growing as evidence lands; that drift belongs in current_exposure, and it
    # is what seals the confirmation set below.
    frozen_development = set(exposed)
    if SPLIT_MANIFEST.exists():
        previous = json.loads(SPLIT_MANIFEST.read_text(encoding="utf-8"))
        frozen_development = set(previous["exposed_development"]["question_ids"])
        if not frozen_development <= exposed:
            raise ValueError(
                "frozen development cohort is no longer fully exposed; committed "
                "evidence was deleted: "
                f"{sorted(frozen_development - exposed)}"
            )
    exposed_answer_sessions = {
        session_id
        for question_id in exposed
        for session_id in by_id[question_id]["answer_session_ids"]
    }
    exposed_haystack_sessions = {
        session_id
        for question_id in exposed
        for session_id in by_id[question_id]["haystack_session_ids"]
    }
    unexposed = dataset_ids - exposed
    answer_disjoint = {
        question_id
        for question_id in unexposed
        if exposed_answer_sessions.isdisjoint(by_id[question_id]["answer_session_ids"])
    }
    strict_disjoint = {
        question_id
        for question_id in unexposed
        if exposed_haystack_sessions.isdisjoint(by_id[question_id]["haystack_session_ids"])
    }
    excluded = unexposed - answer_disjoint

    def split(ids: set[str]) -> dict:
        return {
            "count": len(ids),
            "question_ids_sorted_sha256": id_list_sha256(ids),
            "question_ids": sorted(ids),
        }

    return {
        "dataset": {
            "repo": REPO,
            "revision": REVISION,
            "filename": REMOTE_FILES["longmemeval_s"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "question_count": len(rows),
            "question_ids_sorted_sha256": id_list_sha256(dataset_ids),
        },
        "exposure_snapshot": {
            "git_commit": commit,
            "tracked_artifact_json_count": artifact_count,
            "artifact_json_with_dataset_question_ids_count": matching_count,
            "algorithm": (
                "Parse every git-tracked docs/build-log/artifacts/**/*.json at "
                "HEAD; recursively collect exact question_id string values; "
                "intersect with cleaned dataset IDs."
            ),
        },
        "exposed_development": split(frozen_development),
        "current_exposure": {
            **split(exposed),
            "exposure_class": "every-question-id-present-in-committed-evidence",
            "beyond_frozen_development_count": len(exposed - frozen_development),
        },
        "answer_bearing_session_disjoint_confirmation": {
            **split(answer_disjoint),
            "exposure_class": "question-unseen-and-answer-bearing-session-disjoint",
            "excluded_linked_question_ids": sorted(excluded),
            "excluded_linked_question_ids_sorted_sha256": id_list_sha256(excluded),
        },
        "strict_all_haystack_session_disjoint_confirmation": {
            **split(strict_disjoint),
            "exposure_class": "all-haystack-session-disjoint",
        },
    }


def download(name: str, expected_sha256: str) -> None:
    url = resolve_url(name)
    dest = DATA_DIR / f"{name}.json"
    print(f"downloading {url} -> {dest}")
    request = urllib.request.Request(url, headers={"User-Agent": "memphant-fetch/1.0"})
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=DATA_DIR, delete=False) as out:
            temp_path = Path(out.name)
            with urllib.request.urlopen(request) as response:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
        actual_sha256 = sha256_of(temp_path)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"downloaded {name} sha256 {actual_sha256} does not match "
                f"pinned sha256 {expected_sha256}"
            )
        os.replace(temp_path, dest)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    print(f"  sha256={expected_sha256} bytes={dest.stat().st_size}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle-only", action="store_true")
    args = parser.parse_args()
    names = ["longmemeval_oracle"] if args.oracle_only else FILES

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)

    if not MANIFEST.exists():
        raise ValueError(f"missing committed dataset lock manifest: {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("repo") != REPO or manifest.get("revision") != REVISION:
        raise ValueError("dataset lock manifest repo or revision does not match fetcher")

    for name in names:
        dest = DATA_DIR / f"{name}.json"
        pinned = manifest["files"].get(name)
        if not isinstance(pinned, dict) or not pinned.get("sha256"):
            raise ValueError(f"dataset lock manifest has no pin for {name}")
        if pinned.get("url") != resolve_url(name):
            raise ValueError(f"dataset lock manifest URL does not match {name}")
        if (
            dest.exists()
            and sha256_of(dest) == pinned["sha256"]
        ):
            print(f"{dest} verified against pinned sha256, skipping download")
            continue
        download(name, pinned["sha256"])

    print(f"verified against lock manifest: {MANIFEST}")
    if not args.oracle_only:
        split_manifest = build_split_manifest(DATA_DIR / "longmemeval_s.json")
        SPLIT_MANIFEST.write_text(
            json.dumps(split_manifest, indent=2) + "\n", encoding="utf-8"
        )
        print(f"split manifest written: {SPLIT_MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
