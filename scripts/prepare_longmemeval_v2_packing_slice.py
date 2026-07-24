#!/usr/bin/env python3
"""Materialize only the frozen LongMemEval-V2 n=12 inputs after acquisition."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/longmemeval_v2.packing-kill.n12.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new(path: Path, body: str) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite frozen slice: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def prepare(manifest_path: Path, data_root: Path, output_root: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text())
    selected = {case["id"]: case for case in manifest["cases"]}
    if len(selected) != 12:
        raise RuntimeError("packing slice must contain exactly 12 unique cases")
    questions: dict[str, dict[str, object]] = {}
    for line in (data_root / "questions.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row.get("id") in selected:
            questions[row["id"]] = row
    if set(questions) != set(selected):
        raise RuntimeError("selected question is missing")
    runtime_questions: dict[str, dict[str, object]] = {}
    for case_id, row in questions.items():
        item = dict(row)
        image = item.pop("image", None)
        if image is not None:
            image_path = (data_root / str(image)).resolve()
            if not image_path.is_file():
                raise RuntimeError(f"selected question image is missing: {case_id}")
            item["question"] = {"text": item["question"], "image": str(image_path)}
        runtime_questions[case_id] = item
    haystacks = json.loads((data_root / "haystacks/lme_v2_small.json").read_text())
    selected_haystacks = {case_id: haystacks[case_id] for case_id in selected}
    needed_trajectories = {
        trajectory_id
        for trajectory_ids in selected_haystacks.values()
        for trajectory_id in trajectory_ids
    }
    trajectories: list[dict[str, object]] = []
    for line in (data_root / "trajectories.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row.get("id") in needed_trajectories:
            trajectories.append(row)
    if {row["id"] for row in trajectories} != needed_trajectories:
        raise RuntimeError("selected trajectory is missing")

    outputs: dict[str, str] = {}
    trajectory_path = output_root / "trajectories.n12.jsonl"
    write_new(trajectory_path, "".join(json.dumps(row) + "\n" for row in trajectories))
    outputs[str(trajectory_path.relative_to(output_root))] = sha256_file(trajectory_path)
    for domain in ("enterprise", "web"):
        ids = [case["id"] for case in manifest["cases"] if case["domain"] == domain]
        question_path = output_root / domain / "questions.n6.jsonl"
        haystack_path = output_root / domain / "haystack.n6.json"
        write_new(question_path, "".join(json.dumps(runtime_questions[item]) + "\n" for item in ids))
        write_new(haystack_path, json.dumps({item: selected_haystacks[item] for item in ids}, indent=2) + "\n")
        outputs[str(question_path.relative_to(output_root))] = sha256_file(question_path)
        outputs[str(haystack_path.relative_to(output_root))] = sha256_file(haystack_path)
    proof = {
        "schema_version": 1,
        "manifest_sha256": sha256_file(manifest_path),
        "question_count": len(selected),
        "trajectory_count": len(trajectories),
        "outputs": outputs,
        "answers_preserved_for_official_grader": True,
        "model_calls": 0,
    }
    proof_path = output_root / "slice-proof.json"
    write_new(proof_path, json.dumps(proof, indent=2, sort_keys=True) + "\n")
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.manifest, args.data_root, args.output_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
