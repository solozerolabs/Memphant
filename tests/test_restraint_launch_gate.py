from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "docs" / "launch" / "restraint-launch-scorecard.json"


def load_scorecard() -> dict:
    return json.loads(SCORECARD.read_text(encoding="utf-8"))


def status_marks_restraint_complete() -> bool:
    status = (ROOT / "docs/superpowers/specs/memphant/STATUS.md").read_text(encoding="utf-8")
    return "- [x] **Restraint launch gate**" in status


# Restraint-instrument substitution (2026-07-30, `26` §3 D-2026-07-30c, `27` §1):
# OP-Bench has no runnable public release and PS-Bench has no license, so the
# instrument that is pinned, adapted, and executed is MemSyco-Bench
# (`benchmarks/manifests/memsyco.lock.json`, `scripts/run_restraint_bench.py`).
# Before this set admitted "memsyco-bench", a PASSING MemSyco run would have
# failed the gate's own contract test. Only the instrument identity moved: the
# drop threshold, sample floor, and CI upper bound below are unchanged.
ADMISSIBLE_RESTRAINT_BENCHMARKS = {"memsyco-bench", "op-bench", "ps-bench"}


def test_restraint_scorecard_enforces_launch_threshold() -> None:
    scorecard = load_scorecard()

    # Evidence reset (2026-07-09): the 2026-07-04 run was answer-seeded
    # synthetic fixtures; the scorecard is retained only as an audit trail.
    assert scorecard["status"] == "invalid_synthetic_fixture"
    assert scorecard["source_status"] == "fabricated_fixture_20260703"
    assert not status_marks_restraint_complete()
    if scorecard["status"] == "pass":
        assert scorecard.get("runtime") == "postgres"
    assert scorecard["metric"] == "relative_drop_vs_memory_free"
    assert scorecard["threshold_max_drop"] == 0.15
    assert scorecard["relevance_gate_mandatory_if_drop_exceeds_threshold"] is True
    assert scorecard["pinned_block_in_scope"] is True
    if scorecard["status"] == "pass":
        assert scorecard["benchmark"] in ADMISSIBLE_RESTRAINT_BENCHMARKS
        assert scorecard["sample_count"] >= 50
        assert scorecard["ci"]["upper"] <= scorecard["threshold_max_drop"]
        assert scorecard["measured_drop"] <= scorecard["threshold_max_drop"]


def test_restraint_profile_axis_matches_scorecard_measurement() -> None:
    scorecard = load_scorecard()
    profile = json.loads((ROOT / scorecard["profile_path"]).read_text(encoding="utf-8"))
    restraint = profile["axes"]["restraint"]

    assert restraint["benchmark"] == scorecard["benchmark"]
    # The archived profile's per-axis gate is part of the invalidated audit
    # trail; it no longer has to mirror the (now invalid) scorecard status.
    if scorecard["status"] == "pass":
        assert restraint["gate"] == scorecard["status"]
        assert restraint["source_status"] == "sampled_public"
        assert restraint["sample_count"] == scorecard["sample_count"]
    assert restraint["score"] == scorecard["memphant_score"]
    assert restraint["baseline_score"] == scorecard["memory_free_baseline_score"]
    assert abs(restraint["delta_vs_baseline"]) == scorecard["measured_drop"]
    assert restraint["trace_ref"] in scorecard["trace_refs"]


def test_restraint_trace_has_no_mismatches() -> None:
    scorecard = load_scorecard()
    for trace_ref in scorecard["trace_refs"]:
        trace = json.loads((ROOT / trace_ref).read_text(encoding="utf-8"))
        if scorecard["status"] == "pass":
            assert trace["metrics"]["total_cases"] >= 50
            assert trace["source_status"] == "sampled_public"
            assert trace["metrics"]["passed_cases"] == trace["metrics"]["total_cases"]
            for case in trace["case_results"]:
                assert case["passed"] is True
                assert case["dropped_mismatches"] == []
        elif scorecard["status"] == "fail":
            assert trace["metrics"]["passed_cases"] < trace["metrics"]["total_cases"]


def test_pinned_block_content_is_in_scope_for_restraint_gate() -> None:
    scorecard = load_scorecard()
    owner_text = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in scorecard["owner_refs"])

    assert "pinned-block content" in owner_text or "pinned block" in owner_text
    # Renamed with the instrument substitution (`04` §12 "Restraint-gated");
    # the older spelling stays admissible so this asserts the rule, not a label.
    assert "Restraint-gated" in owner_text or "OP-Bench-gated" in owner_text
    assert "must not drop >15%" in owner_text
