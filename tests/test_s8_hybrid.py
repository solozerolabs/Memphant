"""S8 — the guards that make the retrieve-then-rank comparison mean anything.

Each test here pins a rule the preregistration commits to and that a later
convenience edit would otherwise be free to erode: the shared endpoint contract,
pool containment (without which S8 is S4 re-run), the budget caps, the ceiling
arithmetic, and the decision rule's structural floor.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import code_lane_run_hybrid_rank as hybrid  # noqa: E402
import s8_hybrid_analyze as analyze  # noqa: E402
import s8_hybrid_common as s8  # noqa: E402


def test_endpoint_contract_is_byte_identical_to_s4s():
    # S4's arms are paired against this lane's. If the string drifts, the
    # analysis refuses to pair them -- which is the point -- but the drift must
    # be a deliberate edit here, not an accident in either lane.
    assert s8.ENDPOINT_CONTRACT == "gate_common.provenance_hit@10 over top-10 bodies"


def test_budget_caps_match_the_agentic_control():
    # Budget symmetry is what stops "the hybrid won" from meaning "the hybrid
    # was given more compute". These are S4's caps, verbatim.
    assert hybrid.MAX_TOOL_CALLS == 12
    assert hybrid.MAX_TURNS == 16
    assert hybrid.MAX_COMPLETION_TOKENS_PER_QUESTION == 24_000
    assert hybrid.GREP_MAX_MATCHES == 25
    assert hybrid.READ_ITEM_CHARS == 6_000
    assert hybrid.SELECT_MAX == 10


def test_no_offered_tool_can_reach_outside_the_pool():
    names = {tool["function"]["name"] for tool in hybrid.TOOLS}
    assert names == {"list_pool", "grep_pool", "read_item", "select"}
    # The raw-event surface S4's control had must not exist here under any name.
    assert not names & {"list_events", "read_event", "grep", "read_file", "bash"}


def test_pool_tools_refuse_an_out_of_range_item_and_leak_nothing():
    tools = hybrid.PoolTools([{"body": "alpha"}, {"body": "beta"}])
    result = tools.dispatch("read_item", {"item": 99})
    assert result.startswith("ERROR")
    assert "alpha" not in result and "beta" not in result
    assert tools.out_of_range_requests == 1
    assert tools.dispatch("read_item", {"item": 2}) == "item 2\nbeta"


def test_grep_pool_searches_only_the_bodies_it_holds():
    tools = hybrid.PoolTools([{"body": "the answer is 42"}, {"body": "unrelated"}])
    assert "item 1" in tools.grep_pool("answer")
    assert tools.grep_pool("nothing here at all") == "no matches"
    assert tools.grep_pool("[") .startswith("ERROR: bad regex")


def test_selection_resolves_only_within_the_view():
    pool = [{"body": f"body-{i}", "is_gold": i == 3} for i in range(1, 9)]
    row = hybrid.run_question(
        hybrid.StubEngine(),
        {"question_id": "q1", "question_type": "t"},
        {"pool": pool, "query": "find the third body", "attempt_id": "a1"},
        depth=4,
    )
    assert row["pool_depth"] == 4
    assert row["error"] is None
    assert all(1 <= item <= 4 for item in row["resolved_items"])
    assert row["unresolved_items"] == []
    # Every returned body came from the four the agent was shown.
    assert set(row["bodies"]) <= {item["body"] for item in pool[:4]}
    # is_gold is post-hoc only: the gold sits at pool rank 3, inside the view.
    assert row["gold_in_view"] is True
    assert row["gold_best_pool_rank"] == 3


def test_gold_outside_the_view_is_recorded_as_the_retrievers_failure():
    pool = [{"body": f"body-{i}", "is_gold": i == 7} for i in range(1, 9)]
    row = hybrid.run_question(
        hybrid.StubEngine(),
        {"question_id": "q1", "question_type": "t"},
        {"pool": pool, "query": "find something", "attempt_id": "a1"},
        depth=4,
    )
    assert row["gold_in_view"] is False
    assert row["gold_best_pool_rank"] == 7  # known, and unreachable at N=4


def test_coverage_ceiling_is_monotone_and_bounded_by_the_pool():
    gold_ranks = {"q1": [3], "q2": [40], "q3": [], "q4": [1, 9]}
    order = ["q1", "q2", "q3", "q4"]
    curve = [analyze.coverage_at(gold_ranks, order, n) for n in (1, 4, 10, 64)]
    assert curve == [1, 2, 2, 3]
    assert curve == sorted(curve)  # coverage can never fall as N grows
    # q3's gold is in no pool position at all: no N can reach it.
    assert analyze.coverage_at(gold_ranks, order, 10**9) == 3


def test_sweep_stage_never_returns_a_verdict():
    stats = analyze.paired(
        {"a": True, "b": True}, {"a": False, "b": False}, ["a", "b"]
    )
    assert analyze.verdict(stats, decisive=False)["verdict"].startswith("NOT A MEASUREMENT")


def test_below_the_discordance_floor_is_not_a_measurement():
    treatment = {f"q{i}": i < 50 for i in range(100)}
    control = dict(treatment)
    control["q0"] = not control["q0"]
    stats = analyze.paired(treatment, control, sorted(treatment))
    assert stats["n_discordant"] == 1
    result = analyze.verdict(stats, decisive=True)
    assert result["verdict"] == "NOT A MEASUREMENT"
    assert "required n" in result["reason"]


def test_this_lanes_verdict_calls_required_n_with_psi_first():
    # The signature itself is guarded on trunk by
    # tests/test_instrument_power_contract.py, which is the survivor; this test
    # guards only THIS lane's call site. A transposed call here would be
    # type-correct, would return a plausible integer, and would stall ~23s
    # walking the exact-power search to its cap -- so it is pinned by the
    # observable difference between the two orders, not by reading the comment.
    stats = analyze.paired({"a": True, "b": True}, {"a": True, "b": False}, ["a", "b"])
    assert stats["n_discordant"] == 1  # under the floor: the branch runs
    reason = analyze.verdict(stats, decisive=True)["reason"]
    # psi=0.5 > delta=0.0938, so psi-first reaches an n; the transposed order
    # (psi=0.0938, delta=0.5) would return None and say "unreachable".
    assert "unreachable" not in reason and "~=" in reason


def test_the_two_reasons_required_n_returns_none_are_reported_differently():
    # psi == 0 (no discordant pairs) and delta > psi (no n attains the planning
    # effect) are materially different facts and must not collapse into one line.
    concordant = analyze.paired({"a": True}, {"a": True}, ["a"])
    assert "undefined" in analyze.verdict(concordant, decisive=True)["reason"]

    thin = {f"q{i}": True for i in range(200)}
    control = dict(thin)
    control["q0"] = False
    stats = analyze.paired(thin, control, sorted(thin))
    assert stats["realized_psi"] < 0.0938
    assert "unreachable at any n" in analyze.verdict(stats, decisive=True)["reason"]


def test_analysis_refuses_an_arm_that_escaped_the_pool():
    report = {
        "liveness": {
            "pool_containment_violations": 2,
            "raw_event_access": False,
            "rows_with_errors": 0,
        }
    }
    with pytest.raises(SystemExit, match="pool_containment_violations"):
        analyze.assert_pool_containment("h64", report)


def test_analysis_refuses_an_arm_with_errored_rows():
    report = {
        "liveness": {
            "pool_containment_violations": 0,
            "raw_event_access": False,
            "rows_with_errors": 3,
        }
    }
    with pytest.raises(SystemExit, match="errors are not"):
        analyze.assert_pool_containment("h64", report)


def test_analysis_refuses_to_pair_a_different_stage():
    with pytest.raises(SystemExit, match="different stages"):
        analyze.assert_same_stage("x", {"endpoint_contract": "something else"})


def test_runner_refuses_a_pool_dump_missing_goldens(tmp_path: Path):
    dump = tmp_path / "pool.jsonl"
    dump.write_text("")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/code_lane_run_hybrid_rank.py"),
            "--corpus", str(tmp_path / "missing-corpus.jsonl"),
            "--golden", str(tmp_path / "missing-golden.jsonl"),
            "--pool-dump", str(dump),
            "--pool-depth", "8",
            "--label", "t",
            "--out-evidence", str(tmp_path / "e.jsonl"),
            "--out-provenance", str(tmp_path / "p.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0  # it never reaches a score with no inputs


def test_provider_refusals_are_admitted_only_under_an_explicit_flag():
    report = {
        "liveness": {
            "pool_containment_violations": 0,
            "raw_event_access": False,
            "rows_with_errors": 3,
            "error_kinds": ["no tool call after 2 nudges (finish_reason=content_filter)"],
        }
    }
    with pytest.raises(SystemExit, match="errors are not"):
        analyze.assert_pool_containment("h64", report)
    analyze.assert_pool_containment("h64", report, allow_refusals=True)  # explicit


def test_the_refusal_flag_does_not_launder_any_other_error():
    report = {
        "liveness": {
            "pool_containment_violations": 0,
            "raw_event_access": False,
            "rows_with_errors": 1,
            "error_kinds": ["turn ceiling reached before `select`"],
        }
    }
    with pytest.raises(SystemExit, match="would not apply either"):
        analyze.assert_pool_containment("h64", report, allow_refusals=True)


def test_random_ranker_floor_is_exact_and_bounded():
    gold_ranks = {"q1": [1], "q2": [50], "q3": []}
    pool_sizes = {"q1": 100, "q2": 100, "q3": 100}
    order = ["q1", "q2", "q3"]
    # At N<=10 the whole view is returned, so it is a hit iff gold is in view.
    assert analyze.random_ranker_baseline(gold_ranks, pool_sizes, order, 10) == 1.0
    # At N=20 with one gold in view, exactly 10/20.
    assert round(analyze.random_ranker_baseline(gold_ranks, pool_sizes, order, 20), 6) == 0.5
    # It can never exceed coverage: a random ranker cannot find what it was not shown.
    for n in (4, 16, 64, 128):
        assert analyze.random_ranker_baseline(
            gold_ranks, pool_sizes, order, n
        ) <= analyze.coverage_at(gold_ranks, order, n) + 1e-9
