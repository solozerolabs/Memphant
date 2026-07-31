#!/usr/bin/env python3
"""Fetch the LongMemEval dataset from Hugging Face and pin it by sha256.

Downloads `longmemeval_s` (preferred; ~40-50 haystack sessions per question)
and `longmemeval_oracle` (answer sessions only; reduced distractor pressure)
from the `xiaowu0162/longmemeval-cleaned` dataset repo into `benchmarks/data/`
(gitignored), verified against the committed lock manifest at
`benchmarks/manifests/longmemeval_s.lock.json`.

The fetcher never changes pins. Existing files are verified and re-downloaded
only on hash mismatch or absence; downloaded bytes are verified before replace.

`--deprecated` additionally fetches the ORIGINAL (upstream-deprecated)
`xiaowu0162/longmemeval` split, pinned by commit sha rather than the `main`
branch the first pin used. It is not part of the standing lane; it exists so the
cleaned-vs-deprecated retrieval comparison in
`docs/build-log/2026-07-31-lme-cleaned-split.md` reproduces.

`--verify-lock` runs no network I/O. It re-derives every figure the lock
asserts — file sha256s and byte counts, row/session/turn counts, this
materializer's own sha256, and the private mirror's copies — and exits non-zero
on the first disagreement. Bodies stay gitignored, so the mirror check is what
makes a single local copy not the only copy.

Usage:
  python3 scripts/fetch_longmemeval.py [--oracle-only] [--deprecated]
  python3 scripts/fetch_longmemeval.py --verify-lock
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

# The upstream-deprecated original split. The first pin of this repo resolved
# `main`, which is not a pin at all; this names the commit that `main` pointed
# at, and its sha256 is byte-identical to the one every standing chat-lane
# number was measured on.
DEPRECATED_REPO = "xiaowu0162/longmemeval"
DEPRECATED_REVISION = "2ec2a557f339b6c0369619b1ed5793734cc87533"
DEPRECATED_REMOTE = "longmemeval_s"
DEPRECATED_LOCAL = "longmemeval_s_original_deprecated"

# Bodies are gitignored, so a single local copy is the whole corpus. The repo
# has already lost one that way; every fetched body is mirrored here too.
MIRROR_DIR = Path.home() / ".memphant-private" / "longmemeval-cleaned"


def resolve_url(name: str) -> str:
    return (
        f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/"
        f"{REMOTE_FILES[name]}"
    )


def deprecated_url() -> str:
    return (
        f"https://huggingface.co/datasets/{DEPRECATED_REPO}/resolve/"
        f"{DEPRECATED_REVISION}/{DEPRECATED_REMOTE}"
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


def corpus_shape(path: Path) -> dict:
    """Row/session/turn counts the lock asserts, re-derived from the bytes.

    A sha256 pins the file; these pin what is *in* it, which is what a reader of
    the comparison actually needs — the cleaning's whole effect is on session
    counts, and 1230 of the sessions it dropped were empty.
    """
    rows = json.loads(path.read_text(encoding="utf-8"))
    sessions = [session for row in rows for session in row["haystack_sessions"]]
    return {
        "questions": len(rows),
        "unique_question_ids": len({row["question_id"] for row in rows}),
        "haystack_sessions": len(sessions),
        "empty_haystack_sessions": sum(1 for session in sessions if not session),
        "haystack_turns": sum(len(session) for session in sessions),
        "answer_sessions": sum(len(row["answer_session_ids"]) for row in rows),
    }


def _check(failures: list[str], label: str, expected: object, actual: object) -> None:
    if expected != actual:
        failures.append(f"{label}: lock says {expected!r}, measured {actual!r}")


def verify_lock() -> int:
    """Re-derive every figure in the lock. No network I/O."""
    if not MANIFEST.exists():
        print(f"FAIL missing lock manifest: {MANIFEST}", file=sys.stderr)
        return 1
    lock = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures: list[str] = []

    _check(failures, "repo", lock.get("repo"), REPO)
    _check(failures, "revision", lock.get("revision"), REVISION)
    _check(
        failures,
        "materializer.sha256",
        (lock.get("materializer") or {}).get("sha256"),
        sha256_of(Path(__file__).resolve()),
    )

    deprecated = lock.get("deprecated_split") or {}
    _check(failures, "deprecated_split.repo", deprecated.get("repo"), DEPRECATED_REPO)
    _check(
        failures,
        "deprecated_split.revision",
        deprecated.get("revision"),
        DEPRECATED_REVISION,
    )

    entries = dict(lock.get("files") or {})
    if deprecated.get("file"):
        entries[DEPRECATED_LOCAL] = deprecated["file"]

    for name, pinned in sorted(entries.items()):
        path = DATA_DIR / f"{name}.json"
        if not path.exists():
            failures.append(f"{name}: body absent at {path} (re-fetch to verify)")
            continue
        _check(failures, f"{name}.sha256", pinned.get("sha256"), sha256_of(path))
        _check(failures, f"{name}.bytes", pinned.get("bytes"), path.stat().st_size)
        if pinned.get("shape") is not None:
            _check(failures, f"{name}.shape", pinned["shape"], corpus_shape(path))
        mirrored = MIRROR_DIR / f"{name}.json"
        if not mirrored.exists():
            failures.append(f"{name}: mirror absent at {mirrored}")
        else:
            _check(
                failures,
                f"{name}.mirror.sha256",
                pinned.get("sha256"),
                sha256_of(mirrored),
            )

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"OK lock verified: {MANIFEST}")
    print(f"OK mirror verified: {MIRROR_DIR}")
    return 0


def download(name: str, expected_sha256: str, url: str | None = None) -> None:
    url = url or resolve_url(name)
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
    mirror(dest, expected_sha256)


def mirror(source: Path, expected_sha256: str) -> None:
    """Copy a verified body into the private mirror, verifying the copy."""
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    destination = MIRROR_DIR / source.name
    if destination.exists() and sha256_of(destination) == expected_sha256:
        return
    temp_path = destination.with_suffix(destination.suffix + ".partial")
    temp_path.write_bytes(source.read_bytes())
    if sha256_of(temp_path) != expected_sha256:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"mirror copy of {source.name} failed its sha256 check")
    os.replace(temp_path, destination)
    print(f"  mirrored -> {destination}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle-only", action="store_true")
    parser.add_argument(
        "--deprecated",
        action="store_true",
        help="also fetch the upstream-deprecated original split (comparison only)",
    )
    parser.add_argument(
        "--verify-lock",
        action="store_true",
        help="re-derive every figure in the lock, including the mirror; no network",
    )
    args = parser.parse_args()
    if args.verify_lock:
        return verify_lock()
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
            mirror(dest, pinned["sha256"])
            continue
        download(name, pinned["sha256"])

    if args.deprecated:
        pinned = (manifest.get("deprecated_split") or {}).get("file")
        if not isinstance(pinned, dict) or not pinned.get("sha256"):
            raise ValueError("dataset lock manifest has no deprecated_split pin")
        if pinned.get("url") != deprecated_url():
            raise ValueError("dataset lock manifest URL does not match deprecated split")
        dest = DATA_DIR / f"{DEPRECATED_LOCAL}.json"
        if dest.exists() and sha256_of(dest) == pinned["sha256"]:
            print(f"{dest} verified against pinned sha256, skipping download")
            mirror(dest, pinned["sha256"])
        else:
            download(DEPRECATED_LOCAL, pinned["sha256"], url=deprecated_url())

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
