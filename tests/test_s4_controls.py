#!/usr/bin/env python3
"""Tests for the S4 control arms.

Three invariants are worth a test, and they are the three that decide whether
the measurement means anything:

1. **Stage identity.** Every arm must grade through the same
   `gate_common.provenance_hit` at the same k, and the comparison must REFUSE
   two arms that do not. A headline in this program was voided for scoring one
   arm after packing against another's plain ranked top-10.
2. **The decision rule.** §A.4 was committed before any cell; the code must
   apply it verbatim, including the n_d >= 6 structural floor that forbids the
   words "a tie".
3. **Budget symmetry.** The agentic control's caps are what make its number
   believable; a cap that silently does not bind is worse than no cap.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_common as gc  # noqa: E402
import s4_controls_common as s4  # noqa: E402
import s4_controls_compare as compare  # noqa: E402
import code_lane_run_agentic_control as agentic  # noqa: E402


def golden(question_id: str, span: str) -> dict:
    return {
        "question_id": question_id,
        "question_type": "state-churn",
        "is_abstention": False,
        "question": f"what happened in {question_id}",
        "question_date": None,
        "gold_answer": span,
        "provenance": [{"attempt_id": "a1", "span": span, "event_sequence": 3}],
    }


def test_score_arm_grades_through_gate_common_at_the_pinned_stage():
    g = golden("q1", "pip install microdf")
    hit = s4.score_arm([g], {"q1": ["noise"] * 9 + ["run: pip install microdf ok"]})
    miss = s4.score_arm([g], {"q1": ["noise"] * 10})
    assert hit["hits_at_10"] == 1 and miss["hits_at_10"] == 0
    assert hit["endpoint_contract"] == s4.ENDPOINT_CONTRACT
    # identical to grading the same bodies with gate_common directly
    assert gc.provenance_hit(g, ["noise"] * 9 + ["run: pip install microdf ok"], 10)


def test_score_arm_truncates_to_k_so_an_arm_cannot_buy_recall_with_width():
    g = golden("q1", "needle")
    over = s4.score_arm([g], {"q1": ["noise"] * 10 + ["needle here"]})
    assert over["hits_at_10"] == 0
    assert over["per_question"][0]["returned_items"] == 10


def test_compare_refuses_arms_scored_at_a_different_stage():
    with pytest.raises(SystemExit, match="refusing to pair"):
        compare.assert_same_stage("bogus", {"engine": "something_else", "k": 10})
    with pytest.raises(SystemExit, match="not the pinned k"):
        compare.assert_same_stage("bm25", {"engine": "deterministic_file_search", "k": 5})
    compare.assert_same_stage("ok", {"endpoint_contract": s4.ENDPOINT_CONTRACT})


def test_treatment_liveness_requires_both_channels_from_the_runs_own_trace():
    live = {
        "lexical_scorer": "bm25-code",
        "embed_model": "small",
        "per_question": [
            {"channel_table": [{"channels": [["lexical", 1, 5.0], ["vector", 2, 0.6]]}]}
        ],
    }
    assert compare.assert_treatment_liveness(live)["questions_with_vector_channel"] == 1
    inert = {
        **live,
        "per_question": [{"channel_table": [{"channels": [["lexical", 1, 5.0]]}]}],
    }
    with pytest.raises(SystemExit, match="channels not proven live"):
        compare.assert_treatment_liveness(inert)
    with pytest.raises(SystemExit, match="not the shipped default"):
        compare.assert_treatment_liveness({**live, "embed_model": "off"})


def paired_from(b: int, c: int, both: int, neither: int) -> dict:
    order = []
    treatment, control = {}, {}
    for index in range(b + c + both + neither):
        key = f"q{index}"
        order.append(key)
        if index < b:
            treatment[key], control[key] = True, False
        elif index < b + c:
            treatment[key], control[key] = False, True
        elif index < b + c + both:
            treatment[key], control[key] = True, True
        else:
            treatment[key], control[key] = False, False
    return compare.paired(treatment, control, order)


def test_below_the_structural_floor_is_not_a_tie():
    stats = paired_from(b=3, c=2, both=50, neither=125)
    assert stats["n_discordant"] == 5
    result = compare.verdict(stats)
    assert result["verdict"] == "NOT A MEASUREMENT"
    assert result["required_n_for_n_d_floor"] == 216
    assert "tie" in result["reason"]


def test_decision_rule_boundaries_are_the_preregistered_ones():
    # delta well above MDE with a decisive p -> Verdict A
    assert compare.verdict(paired_from(b=30, c=2, both=40, neither=108))["verdict"].startswith("A")
    # the same shape reversed -> Verdict B
    assert compare.verdict(paired_from(b=2, c=30, both=40, neither=108))["verdict"].startswith("B")
    # discordant but inside the MDE band -> D, never "no effect"
    inside = compare.verdict(paired_from(b=12, c=8, both=40, neither=120))
    assert inside["verdict"].startswith("D")
    assert "unmeasured, not absent" in inside["reason"]


def test_agentic_tool_caps_actually_bind():
    events = [
        {"sequence": index, "role": "toolResult", "text": f"line {index} needle " + "x" * 20_000}
        for index in range(300)
    ]
    tools = agentic.AttemptTools(events)
    assert len(tools.list_events().splitlines()) == agentic.LIST_EVENTS_MAX + 1
    assert len(tools.grep("needle").splitlines()) == agentic.GREP_MAX_MATCHES
    assert len(tools.read_event(3)) <= agentic.READ_EVENT_CHARS + 100
    assert "ERROR" in tools.read_event(99999)
    assert "bad regex" in tools.grep("(")


def test_agentic_stub_completes_the_whole_adapter_contract():
    events = [
        {"sequence": index, "role": "assistant", "text": f"event {index} content"}
        for index in range(1, 12)
    ]
    row = agentic.run_question(agentic.StubEngine(), golden("q1", "content"), events)
    assert row["error"] is None
    assert row["tool_calls"] >= 1
    assert row["selection"] and not row["unresolved_sequences"]
    assert len(row["bodies"]) == len(row["resolved_sequences"])
    assert {call["tool"] for call in row["tool_call_log"]} >= {
        "list_events",
        "grep",
        "read_event",
        "select",
    }
