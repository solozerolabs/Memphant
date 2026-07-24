#!/usr/bin/env python3
"""Materialize the frozen diagnostic LongMemEval-S packing pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/lme_s.packing-pilot.n12.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize(source: Path, manifest_path: Path, output: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_source_sha256 = _sha256(source)
    if actual_source_sha256 != manifest["source_dataset_sha256"]:
        raise RuntimeError(
            "LongMemEval-S source drift: "
            f"expected {manifest['source_dataset_sha256']}, got {actual_source_sha256}"
        )
    rows = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("LongMemEval-S source must be a JSON array")
    indexed = {row.get("question_id"): row for row in rows if isinstance(row, dict)}
    question_ids = manifest["question_ids"]
    if len(indexed) != len(rows):
        raise RuntimeError("LongMemEval-S question IDs are missing or duplicated")
    missing = [question_id for question_id in question_ids if question_id not in indexed]
    if missing:
        raise RuntimeError(f"frozen packing pilot IDs are missing: {missing}")
    selected = [indexed[question_id] for question_id in question_ids]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(selected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "source_dataset_sha256": actual_source_sha256,
        "manifest_sha256": _sha256(manifest_path),
        "output_sha256": _sha256(output),
        "question_count": len(selected),
        "question_ids": question_ids,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    proof = materialize(args.source.resolve(), args.manifest.resolve(), args.out.resolve())
    print(json.dumps(proof, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
