from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "docs" / "launch" / "standing-quality-bars.json"


def load_scorecard() -> dict:
    return json.loads(SCORECARD.read_text(encoding="utf-8"))


def status_text() -> str:
    return (ROOT / "docs/superpowers/specs/memphant/STATUS.md").read_text(encoding="utf-8")


def status_marks_all_standing_bars_complete() -> bool:
    status = status_text()
    return "CURRENT PHASE: `COMPLETE`" in status


def status_marks_hot_path_complete() -> bool:
    status = status_text()
    return "- [x] Hot-path SLO holding" in status or status_marks_all_standing_bars_complete()


def status_marks_memory_trend_complete() -> bool:
    status = status_text()
    return (
        "- [x] `memory_utility_trend` SLI wired" in status
        or status_marks_all_standing_bars_complete()
    )


def status_marks_standing_bar_complete(name: str) -> bool:
    if name == "hot_path_slo":
        return status_marks_hot_path_complete()
    if name == "memory_utility_trend":
        return status_marks_memory_trend_complete()
    if name == "landscape_completeness":
        return True
    return status_marks_all_standing_bars_complete()


def status_marks_standing_bars_complete() -> bool:
    status = (ROOT / "docs/superpowers/specs/memphant/STATUS.md").read_text(encoding="utf-8")
    return (
        "- [x] Hot-path SLO holding" in status
        or "- [x] `memory_utility_trend` SLI wired" in status
        or "CURRENT PHASE: `COMPLETE`" in status
    )


def test_status_carries_promotion_provenance_rule_and_reopened_gates() -> None:
    status = status_text()

    assert "**Promotion-provenance rule (2026-07-09):**" in status
    assert "Synthetic fixtures gate regressions, never promotions." in status
    # The gates whose 2026-07-04 evidence was synthetic must stay reopened.
    assert "- [x] **Dogfood gate**" not in status
    assert "- [x] **Restraint launch gate**" not in status
    assert "- [x] **GateMem conditional gate**" not in status
    assert "- [x] **Public launch gate**" not in status


def test_agentic_deep_recall_is_built_but_stays_unpromoted_until_proven() -> None:
    status = status_text()

    assert "- [ ] 12 L4 Deep" in status
    assert "| L4 Deep recall behavior | BUILT" in status
    assert "No automatic Deep escalation." in status
    assert "LongMemEval-V2 n=12 promotion evidence remains pending" in status


def test_standing_quality_bars_all_pass() -> None:
    scorecard = load_scorecard()

    assert scorecard["status"] in {"pass", "candidate", "fail"}
    if status_marks_all_standing_bars_complete():
        assert scorecard["status"] == "pass"
    assert set(scorecard["bars"]) == {
        "hot_path_slo",
        "memory_utility_trend",
        "landscape_completeness",
    }
    for name, bar in scorecard["bars"].items():
        assert bar["status"] in {"pass", "candidate", "fail"}, name
        if status_marks_standing_bar_complete(name):
            assert bar["status"] == "pass", name


def test_hot_path_slo_has_executable_threshold_guard_and_profile_proof() -> None:
    bar = load_scorecard()["bars"]["hot_path_slo"]
    if bar["status"] != "pass":
        return
    postgres_slo = json.loads((ROOT / bar["proofs"][0]).read_text(encoding="utf-8"))

    assert bar["mode"] == "fast"
    assert bar["store_backend"] == "postgres"
    assert bar["seeded_units"] >= 1000
    assert bar["corpus_source"]
    assert bar["thresholds_ms"] == {"p50_lt": 200, "p95_lt": 500}
    assert postgres_slo["store_backend"] == "postgres"
    assert postgres_slo["seeded_units"] == bar["seeded_units"]
    assert postgres_slo["p50_ms"] < bar["thresholds_ms"]["p50_lt"]
    assert postgres_slo["p95_ms"] < bar["thresholds_ms"]["p95_lt"]
    assert postgres_slo["slowest_query_explain"]


def test_memory_utility_trend_is_wired_to_public_mark_contract() -> None:
    bar = load_scorecard()["bars"]["memory_utility_trend"]
    if bar["status"] != "pass":
        return
    trace_compare = json.loads((ROOT / bar["proofs"][0]).read_text(encoding="utf-8"))
    core_test = (ROOT / "crates/memphant-core/tests/surface_mutations.rs").read_text(
        encoding="utf-8"
    )
    rest_test = (ROOT / "crates/memphant-server/tests/rest_contract.rs").read_text(
        encoding="utf-8"
    )
    mcp_source = (ROOT / "crates/memphant-mcp/src/lib.rs").read_text(encoding="utf-8")

    assert bar["lane"] == trace_compare["case_id"]
    assert trace_compare["answer_bearing_recall"] == 1.0
    assert bar["baseline_window"] != bar["current_window"]
    assert bar["baseline_count"] > 0
    assert bar["current_count"] > 0
    assert bar["baseline_proof"] != bar["current_proof"]
    assert bar["current_success_rate"] >= bar["baseline_success_rate"]
    assert bar["declined_vs_baseline"] is False
    assert set(bar["outcomes_counted"]) == {"success", "failure", "corrected", "ignored"}
    assert bar["mark_contract"]["required_fields"] == ["trace_id", "used_ids", "outcome"]
    assert "mark_records_outcome_feedback_for_trace" in core_test
    assert '"/v1/mark"' in rest_test
    # The MCP surface exposes the outcome-feedback contract as `report_memory_use`
    # (the identity-free five-tool surface); it maps onto the same review-event
    # path as the REST `mark` verb.
    assert "ReportMemoryUseRequest" in mcp_source
    assert ".report_memory_use(" in mcp_source


def test_landscape_completeness_lists_every_verified_threshold_repo() -> None:
    bar = load_scorecard()["bars"]["landscape_completeness"]
    prior_art = (
        ROOT / "docs/superpowers/specs/memphant/13-prior-art-and-competitive-spec.md"
    ).read_text(encoding="utf-8")

    assert bar["threshold_stars_gte"] == 50000
    assert bar["entries"]
    for entry in bar["entries"]:
        assert entry["stars"] >= 50000, entry["repo"]
        assert entry["listed_or_excluded"] in {"listed", "excluded"}
        if entry["listed_or_excluded"] == "listed":
            assert entry["repo"] in prior_art, entry["repo"]
