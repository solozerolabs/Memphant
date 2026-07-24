#!/usr/bin/env python3
"""Validate the frozen, rejected DeepSWE causal-pair audit against a checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/deep_swe.pairing.audit.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def git_value(checkout: Path, expression: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", expression], text=True
    ).strip()


def solution_paths(path: Path) -> set[str]:
    paths: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("+++ b/"):
            paths.add(line.removeprefix("+++ b/"))
    return paths


def public_target_view(task_dir: Path) -> dict[str, object]:
    metadata = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    safe_metadata = {
        key: metadata["metadata"][key]
        for key in (
            "task_id", "display_title", "display_description", "category",
            "language", "repository_url", "base_commit_hash",
        )
    }
    return {
        "metadata": safe_metadata,
        "instruction": (task_dir / "instruction.md").read_text(encoding="utf-8"),
    }


def task_digest_index(dataset_toml: Path) -> dict[str, str]:
    dataset = tomllib.loads(dataset_toml.read_text(encoding="utf-8"))
    return {
        item["name"].removeprefix("datacurve/"): item["digest"]
        for item in dataset["tasks"]
    }


def validate(manifest: dict[str, object], checkout: Path) -> dict[str, object]:
    release = manifest["release"]
    tasks_root = checkout / "tasks"
    require(git_value(checkout, "HEAD") == release["git_revision"], "revision drift")
    require(git_value(checkout, "HEAD^{tree}") == release["git_tree"], "tree drift")
    for relative, key in (
        ("LICENSE", "license_sha256"),
        ("tasks/manifest.json", "manifest_sha256"),
        ("tasks/dataset.toml", "dataset_toml_sha256"),
    ):
        require(sha256_file(checkout / relative) == release[key], f"{relative} drift")
    task_dirs = [path for path in tasks_root.iterdir() if path.is_dir()]
    require(len(task_dirs) == release["task_count"] == 113, "task count drift")
    digests = task_digest_index(tasks_root / "dataset.toml")

    targets: set[str] = set()
    ancestry = manifest.get("ancestry_evidence")
    require(isinstance(ancestry, dict), "ancestry evidence is missing")
    for pair in manifest["accepted_pairs"]:
        prior_id, target_id = pair["prior"], pair["target"]
        require(prior_id != target_id, "pair is not disjoint")
        require(target_id not in targets, "target is duplicated")
        targets.add(target_id)
        prior_dir, target_dir = tasks_root / prior_id, tasks_root / target_id
        prior_meta = tomllib.loads((prior_dir / "task.toml").read_text())["metadata"]
        target_meta = tomllib.loads((target_dir / "task.toml").read_text())["metadata"]
        require(prior_meta["repository_url"] == target_meta["repository_url"], "repo drift")
        require(prior_meta["base_commit_hash"] == pair["prior_base"], "prior base drift")
        require(target_meta["base_commit_hash"] == pair["target_base"], "target base drift")
        require(pair["prior_base"] != pair["target_base"], "pair has no earlier base")
        ancestry_key = f"{prior_meta['repository_url']}:{pair['prior_base']}...{pair['target_base']}"
        lineage = ancestry.get(ancestry_key)
        require(isinstance(lineage, dict), "pinned upstream compare evidence is missing")
        require(lineage.get("status") == "ahead", "target is not ahead of prior")
        require(lineage.get("behind_by") == 0, "prior is not an upstream ancestor")
        require(
            lineage.get("merge_base_commit") == pair["prior_base"],
            "upstream merge base does not equal prior base",
        )
        canonical_lineage = {
            key: lineage.get(key)
            for key in (
                "compare_url", "status", "ahead_by", "behind_by",
                "total_commits", "merge_base_commit",
            )
        }
        require(
            lineage.get("canonical_evidence_sha256") == canonical_sha256(canonical_lineage),
            "canonical upstream compare evidence hash drift",
        )
        require(
            pair["lineage"] == "prior_base_is_upstream_ancestor_of_target_base",
            "upstream lineage proof is missing",
        )
        require(pair["prior_commit_time"] < pair["target_commit_time"], "time order drift")
        for task_id, task_dir, lock in (
            (prior_id, prior_dir, pair["prior_lock"]),
            (target_id, target_dir, pair["target_lock"]),
        ):
            require(digests[task_id] == lock[0], f"task digest drift: {task_id}")
            require(sha256_file(task_dir / "instruction.md") == lock[1], f"instruction drift: {task_id}")
            require(sha256_file(task_dir / "solution/solution.patch") == lock[2], f"solution drift: {task_id}")
            require(sha256_file(task_dir / "tests/test.patch") == lock[3], f"test drift: {task_id}")
        exact_shared = solution_paths(prior_dir / "solution/solution.patch") & solution_paths(
            target_dir / "solution/solution.patch"
        )
        require(exact_shared == set(pair["shared_solution_files"]), f"shared-file drift: {target_id}")
        visible = json.dumps(public_target_view(target_dir), sort_keys=True)
        require("solution.patch" not in visible and "test.patch" not in visible, "target leak")

    admission = manifest["admission"]
    require(len(targets) == admission["accepted_unique_target_pairs"], "accepted count drift")
    require(len(targets) < admission["required_unique_target_pairs"], "rejection no longer justified")
    return {
        "classification": "deep_swe_unpaired_robustness_only",
        "accepted_pairs": len(targets),
        "required_pairs": admission["required_unique_target_pairs"],
        "model_calls": 0,
        "container_runs": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    result = validate(json.loads(args.manifest.read_text()), args.checkout.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
