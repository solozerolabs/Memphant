#!/usr/bin/env python3
"""Validate the frozen LongMemEval-V2 packing kill manifest against pinned metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/longmemeval_v2.packing-kill.n12.json"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(manifest_path: Path, data_root: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_files = manifest["source_files"]
    for relative, expected in expected_files.items():
        actual = _file_sha256(data_root / relative)
        if actual != expected:
            raise RuntimeError(f"LongMemEval-V2 metadata drift for {relative}")
    questions: dict[str, dict[str, object]] = {}
    for line in (data_root / "questions.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        questions[row["id"]] = row
    small = json.loads(
        (data_root / "haystacks/lme_v2_small.json").read_text(encoding="utf-8")
    )
    cases = manifest["cases"]
    if len(cases) != 12 or len({case["id"] for case in cases}) != 12:
        raise RuntimeError("packing kill manifest must contain 12 unique cases")
    for case in cases:
        question = questions.get(case["id"])
        if question is None:
            raise RuntimeError(f"unknown packing case {case['id']}")
        if (
            question.get("domain") != case["domain"]
            or question.get("question_type") != case["question_type"]
            or question.get("image") != case["image"]
            or _canonical_sha256(question) != case["question_record_sha256"]
        ):
            raise RuntimeError(f"packing question drift for {case['id']}")
        haystack = small.get(case["id"])
        if not isinstance(haystack, list) or len(haystack) != 100:
            raise RuntimeError(f"packing small haystack drift for {case['id']}")
        if _canonical_sha256(haystack) != case["haystack_ids_sha256"]:
            raise RuntimeError(f"packing haystack identity drift for {case['id']}")
    by_domain = {
        domain: {case["haystack_ids_sha256"] for case in cases if case["domain"] == domain}
        for domain in ("enterprise", "web")
    }
    if any(len(hashes) != 1 for hashes in by_domain.values()):
        raise RuntimeError("selected cases do not share one construction per domain")
    return {
        "cases": len(cases),
        "domains": len(by_domain),
        "unique_constructions": sum(len(hashes) for hashes in by_domain.values()),
        "manifest_sha256": _file_sha256(manifest_path),
        "answers_read_by_validator": True,
        "answers_exported": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            validate(args.manifest.resolve(), args.data_root.resolve()),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
