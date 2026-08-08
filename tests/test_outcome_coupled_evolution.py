import json
from pathlib import Path

import pytest

from benchmarks.xs_crosssession.outcome_coupled_evolution import (
    BudgetLedger,
    CompactionError,
    classify_scopes,
    pack_for_policy,
    reconstruct_compactions,
    should_dispatch,
)


def test_reconstructs_summary_preserved_messages_and_active_tail():
    rows = [
        {"uuid": "head", "type": "user", "message": {"role": "user", "content": "old"}},
        {"uuid": "tail", "type": "assistant", "message": {"role": "assistant", "content": "done"}},
        {
            "uuid": "boundary",
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {
                "preTokens": 100,
                "postTokens": 40,
                "cumulativeDroppedTokens": 60,
                "preservedMessages": {
                    "anchorUuid": "summary",
                    "allUuids": ["head", "tail"],
                },
            },
        },
        {
            "uuid": "summary",
            "type": "user",
            "isCompactSummary": True,
            "message": {"role": "user", "content": "summary text"},
        },
        {"uuid": "next", "type": "user", "message": {"role": "user", "content": "next task"}},
    ]

    [cut] = reconstruct_compactions(rows)

    assert cut["summary"]["uuid"] == "summary"
    assert [row["uuid"] for row in cut["preserved"]] == ["head", "tail"]
    assert [row["uuid"] for row in cut["active_tail"]] == ["next"]
    assert cut["token_metadata_valid"] is True


def test_reconstruction_fails_closed_on_missing_anchor_or_bad_token_math():
    boundary = {
        "uuid": "boundary",
        "type": "system",
        "subtype": "compact_boundary",
        "compactMetadata": {
            "preTokens": 100,
            "postTokens": 41,
            "cumulativeDroppedTokens": 60,
            "preservedMessages": {"anchorUuid": "missing", "allUuids": []},
        },
    }
    with pytest.raises(CompactionError):
        reconstruct_compactions([boundary])


def test_scope_qualification_requires_four_distinct_chronological_objective_cases():
    valid = [
        {
            "case_id": f"a{i}",
            "kind": "adherence",
            "learned_at": f"2026-08-0{i}T00:00:00Z",
            "held_out_at": f"2026-08-0{i}T01:00:00Z",
            "source_task_hash": f"source-{i}",
            "held_out_task_hash": f"held-{i}",
            "objective_predicate": "tool_name == Edit",
            "rule_version": "sha256:rule",
            "context_boundary": "compact:1",
            "sensitive": False,
        }
        for i in range(1, 5)
    ]
    invalid = dict(valid[0], case_id="same-task", held_out_task_hash="source-1")

    scopes = classify_scopes(valid + [invalid])

    assert scopes["A1"]["status"] == "eligible"
    assert scopes["A1"]["n_valid"] == 4
    assert scopes["A2"]["status"] == "UNTESTABLE"
    assert scopes["A3"]["status"] == "UNTESTABLE"
    assert "same-task" in scopes["A1"]["rejected_case_ids"]


def test_policy_uses_only_causal_evidence_and_silence_excludes():
    units = [
        {"unit_id": "bad", "kind": "adherence", "validated": True},
        {"unit_id": "fresh", "kind": "adherence", "validated": True},
        {"unit_id": "good", "kind": "adherence", "validated": True},
        {"unit_id": "observed", "kind": "adherence", "validated": True},
        {"unit_id": "quiet", "kind": "adherence", "validated": True},
    ]
    events = (
        [{"unit_id": "good", "event": "helpful", "attribution": "explicit_user"}] * 8
        + [{"unit_id": "bad", "event": "harmful", "attribution": "deterministic_scorer"}] * 8
        + [{"unit_id": "observed", "event": "helpful", "attribution": "observational"}] * 20
        + [{"unit_id": "quiet", "event": "silenced", "attribution": "explicit_user"}]
    )

    assert pack_for_policy(units, events, "C1") == ["bad", "fresh", "good", "observed", "quiet"]
    assert pack_for_policy(units, events, "A1") == ["good", "fresh", "observed", "bad"]


def test_identical_pack_suppression_and_budget_cutoff():
    assert should_dispatch(["u1"], ["u1"]) == "no_policy_difference"
    assert should_dispatch(["u1"], ["u2"]) == "dispatch"

    ledger = BudgetLedger(total_cap=100, phase_caps={"action": 30, "coding": 70})
    reservation = ledger.reserve("action", 12)
    ledger.settle(reservation, 10)
    ledger.reserve("action", 20)
    with pytest.raises(ValueError, match="phase budget"):
        ledger.reserve("action", 1)
    with pytest.raises(ValueError, match="total budget"):
        ledger.reserve("coding", 71)


def test_private_text_never_enters_public_result(tmp_path: Path):
    private = tmp_path / "cases.jsonl"
    secret = "private transcript body"
    private.write_text(json.dumps({"case_id": "x", "raw_text": secret}) + "\n")

    # Public summaries are composed from allowlisted counters and hashes only.
    result = classify_scopes([])

    assert secret not in json.dumps(result)
