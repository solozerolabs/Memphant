"""Pure-function tests for the capture-census dataset assembler (Task B1).

No DB needed: ``phase_to_outcome`` and ``normalize_attempt`` are pure. See
``scripts/capture_census_dataset.py`` for the read-only ``main()`` that mines
Syndai's dev DB (``syndai.coding_execution_attempt_events`` joined to
``syndai.coding_runs``) and is exercised separately against real data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_census_dataset import normalize_attempt, phase_to_outcome  # noqa: E402


def test_phase_maps_to_binary_outcome():
    assert phase_to_outcome("completed") == "passed"
    assert phase_to_outcome("failed") == "not_passed"
    assert phase_to_outcome("cancelled") == "not_passed"


def test_normalize_attempt_extracts_user_turns_and_scope():
    raw = {
        "attempt_id": "a1",
        "coding_run_id": "r1",
        "repo_scope": "repo1",
        "current_phase": "completed",
        "started_at": "2026-07-06T15:44:21+00:00",
        "ended_at": "2026-07-06T15:50:00+00:00",
        "events": [
            {"sequence": 4, "event_type": "user", "payload": {"text": "use pnpm not npm"}},
            {"sequence": 5, "event_type": "assistant", "payload": {"text": "ok"}},
        ],
    }
    rec = normalize_attempt(raw)
    assert rec["repo_scope"] == "repo1" and rec["outcome"] == "passed"
    assert rec["user_turns"] == [{"sequence": 4, "text": "use pnpm not npm"}]


def test_normalize_attempt_handles_string_payload():
    """payload may arrive as a JSON string (not a dict) depending on driver."""
    raw = {
        "attempt_id": "a2",
        "coding_run_id": "r2",
        "repo_scope": "repo2",
        "current_phase": "failed",
        "started_at": None,
        "ended_at": None,
        "events": [
            {
                "sequence": 1,
                "event_type": "user",
                "payload": json.dumps({"text": "flaky test again"}),
            }
        ],
    }
    rec = normalize_attempt(raw)
    assert rec["outcome"] == "not_passed"
    assert rec["user_turns"] == [{"sequence": 1, "text": "flaky test again"}]


def test_normalize_attempt_skips_user_events_without_text():
    raw = {
        "attempt_id": "a3",
        "coding_run_id": "r3",
        "repo_scope": "repo3",
        "current_phase": "cancelled",
        "started_at": None,
        "ended_at": None,
        "events": [
            {"sequence": 1, "event_type": "user", "payload": {}},
            {"sequence": 2, "event_type": "user", "payload": {"text": "   "}},
            {"sequence": 3, "event_type": "user", "payload": None},
            {"sequence": 4, "event_type": "user", "payload": {"text": "real text"}},
        ],
    }
    rec = normalize_attempt(raw)
    assert rec["user_turns"] == [{"sequence": 4, "text": "real text"}]


def test_normalize_attempt_extracts_real_transcript_shape_text():
    """Real live-DB payload shape (confirmed 2026-08-09): a Claude-Code
    transcript envelope, not the flat {"text": ...} fixture shape."""
    raw = {
        "attempt_id": "a5",
        "coding_run_id": "r5",
        "repo_scope": "repo5",
        "current_phase": "completed",
        "started_at": None,
        "ended_at": None,
        "events": [
            {
                "sequence": 1,
                "event_type": "user",
                "payload": {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "use pnpm not npm"}],
                    }
                },
            }
        ],
    }
    rec = normalize_attempt(raw)
    assert rec["user_turns"] == [{"sequence": 1, "text": "use pnpm not npm"}]


def test_normalize_attempt_skips_tool_result_content_items():
    """1,430 of 1,434 real event_type='user' rows are the agent's own
    tool_result echo replayed as a 'user' role turn, not human text — these
    must not be counted as user_turns."""
    raw = {
        "attempt_id": "a6",
        "coding_run_id": "r6",
        "repo_scope": "repo6",
        "current_phase": "failed",
        "started_at": None,
        "ended_at": None,
        "events": [
            {
                "sequence": 1,
                "event_type": "user",
                "payload": {
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "content": "file updated successfully",
                                "tool_use_id": "toolu_1",
                            }
                        ],
                    }
                },
            }
        ],
    }
    rec = normalize_attempt(raw)
    assert rec["user_turns"] == []


def test_normalize_attempt_preserves_ids_and_timestamps():
    raw = {
        "attempt_id": "a4",
        "coding_run_id": "r4",
        "repo_scope": "repo4",
        "current_phase": "completed",
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:05:00+00:00",
        "events": [],
    }
    rec = normalize_attempt(raw)
    assert rec["attempt_id"] == "a4"
    assert rec["run_id"] == "r4"
    assert rec["started_at"] == "2026-01-01T00:00:00+00:00"
    assert rec["ended_at"] == "2026-01-01T00:05:00+00:00"
    assert rec["user_turns"] == []
