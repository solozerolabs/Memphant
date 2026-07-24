from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/validate_deep_swe_pairing.py"
MANIFEST = ROOT / "benchmarks/manifests/deep_swe.pairing.audit.json"


def _load():
    spec = importlib.util.spec_from_file_location("deep_swe_pairing", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pair_audit_rejects_n12_and_preserves_visibility_boundary():
    manifest = json.loads(MANIFEST.read_text())
    admission = manifest["admission"]
    visibility = manifest["visibility_contract"]

    assert manifest["status"].startswith("REJECTED_AS_N12_MEMORY_GATE")
    assert admission["accepted_unique_target_pairs"] == 3
    assert admission["required_unique_target_pairs"] == 12
    assert admission["accepted_unique_target_pairs"] < admission["required_unique_target_pairs"]
    assert visibility["model_calls_executed"] == 0
    assert visibility["container_runs_executed"] == 0
    assert "target solution/" in visibility["treatment_memory_forbidden"]
    assert "prior reference solution/" in visibility["treatment_memory_forbidden"]


def test_accepted_pairs_are_disjoint_unique_exact_base_candidates():
    runner = _load()
    manifest = json.loads(MANIFEST.read_text())
    pairs = manifest["accepted_pairs"]

    assert len(pairs) == 3
    assert len({pair["target"] for pair in pairs}) == 3
    ancestry = manifest["ancestry_evidence"]
    for pair in pairs:
        assert pair["prior"] != pair["target"]
        assert pair["prior_base"] != pair["target_base"]
        assert pair["lineage"] == "prior_base_is_upstream_ancestor_of_target_base"
        assert pair["prior_commit_time"] < pair["target_commit_time"]
        assert pair["shared_solution_files"]
        assert pair["reusable_lesson"]
        assert pair["non_leakage"]
        repository = (
            "https://github.com/pmndrs/koota"
            if pair["prior"].startswith("koota-")
            else "https://github.com/testem/testem"
        )
        lineage = ancestry[f"{repository}:{pair['prior_base']}...{pair['target_base']}"]
        assert lineage["status"] == "ahead"
        assert lineage["behind_by"] == 0
        assert lineage["merge_base_commit"] == pair["prior_base"]
        canonical = {
            key: lineage[key]
            for key in (
                "compare_url", "status", "ahead_by", "behind_by",
                "total_commits", "merge_base_commit",
            )
        }
        assert lineage["canonical_evidence_sha256"] == runner.canonical_sha256(canonical)
        for lock in (pair["prior_lock"], pair["target_lock"]):
            assert len(lock) == 4
            assert lock[0].startswith("sha256:") and len(lock[0]) == 71
            assert all(len(value) == 64 for value in lock[1:])
    assert runner.public_target_view
