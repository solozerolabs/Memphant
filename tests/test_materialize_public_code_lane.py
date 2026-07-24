from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "materialize_public_code_lane", ROOT / "scripts/materialize_public_code_lane.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def source_row() -> dict:
    return {
        "trajectory_id": "trajectory-1",
        "instance_id": "owner__repo-1",
        "repo": "owner/repo",
        "trajectory": [
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": "fix it"},
            {"role": "assistant", "content": "running tests"},
            {"role": "tool", "content": "error E0425: missing value"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        ],
    }


def test_adapter_maps_roles_ids_and_reports_every_drop() -> None:
    lane = load_module()
    row, counts = lane.adapt_row(source_row(), 7, 12)
    assert [event["role"] for event in row["events"]] == [
        "user", "assistant", "toolResult", "assistant"
    ]
    assert counts == {
        "source_messages": 5,
        "emitted_events": 4,
        "skipped_system": 1,
        "skipped_empty": 0,
        "truncated_events": 3,
        "truncated_chars": 27,
        "truncated_bytes": 27,
    }
    assert row["public_source"]["row_index"] == 7
    assert all(event["event_id"].startswith("hf:trajectory-1:") for event in row["events"])
    assert [event["text"] for event in row["events"]] == [
        "fix it",
        "running test",
        "error E0425:",
        'tool_calls: ',
    ]


def test_adapter_rejects_unmapped_roles() -> None:
    lane = load_module()
    source = source_row()
    source["trajectory"].append({"role": "developer", "content": "hidden"})
    with pytest.raises(ValueError, match="unmapped role"):
        lane.adapt_row(source, 0, 4000)


def test_diagnostic_golden_is_exactly_bound_to_source_span() -> None:
    lane = load_module()
    source = source_row()
    source["trajectory"] = (
        source["trajectory"][:2]
        + [{"role": "assistant", "content": f"step {i}"} for i in range(20)]
        + [{"role": "assistant", "content": "run the late test"},
           {"role": "tool", "content": "ERROR: late deterministic failure"}]
    )
    row, _ = lane.adapt_row(source, 0, 4000)
    # Forty independent source rows are enough for the production count;
    # this focused test needs one and proves exact span binding.
    golden = lane.build_goldens([row], 1)[0]
    event = next(item for item in row["events"] if item["event_id"] == golden["provenance"][0]["event_id"])
    proof = golden["provenance"][0]
    assert event["text"][proof["char_start"] : proof["char_end"]] == proof["span"]
    assert golden["gold_answer"] == proof["span"]
    assert proof["event_index"] / proof["attempt_event_count"] >= 0.60


def test_build_goldens_fails_closed_without_diagnostics() -> None:
    lane = load_module()
    row, _ = lane.adapt_row(source_row(), 0, 4000)
    for event in row["events"]:
        event["text"] = "ordinary output"
    with pytest.raises(ValueError, match="only 0"):
        lane.build_goldens([row], 1)


def test_diagnostic_parser_rejects_keyword_only_source_and_docs_lines() -> None:
    lane = load_module()
    assert lane.diagnostic_span("raise RuntimeError('failure')") is None
    assert lane.diagnostic_span("single point of failure") is None
    assert lane.diagnostic_span("ERROR: actual test collection failed") is not None


def test_transform_and_golden_generator_versions_are_not_conflated() -> None:
    lane = load_module()
    assert lane.TRANSFORM_VERSION == "openhands_trajectory_to_syndai_content_events_v2"
    assert lane.GOLDEN_GENERATOR == "deterministic_late_diagnostic_span_v2"
