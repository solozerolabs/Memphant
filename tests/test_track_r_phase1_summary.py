"""Unit tests for the Phase 1b/1c three-arm summary aggregator
(``scripts/track_r_phase1_summary.py``). No DB, no server, no run artifacts —
pure re-aggregation of per-question rows.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def summary_module():
    spec = importlib.util.spec_from_file_location(
        "track_r_phase1_summary", ROOT / "scripts/track_r_phase1_summary.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _pack_summary(items: int, chars: int) -> dict:
    return {
        "packed_items_total": items,
        "packed_items_mean": items / 2,
        "packed_item_chars_total": chars,
        "packed_item_chars_mean": chars / max(items, 1),
        "packed_item_chars_max": chars,
        "buckets": {"hit": 1, "absent_from_pool": 1},
        "in_pool_unpacked": 0,
        "in_pool_unpacked_gold_drop_reasons": {},
        "budget_share_of_in_pool_unpacked": None,
    }


def _report(hits: dict[str, bool], cap, pack_items=4, pack_chars=400) -> dict:
    return {
        "golden_sha256": "g" * 64,
        "corpus_sha256": "c" * 64,
        "pack_render_cap": cap,
        "k": 10,
        "pack_drop_summary": _pack_summary(pack_items, pack_chars),
        "runtime_identity": {"command": f"cmd cap={cap}"},
        "per_question": [
            {
                "question_id": qid,
                "hit_at_5": hit,
                "hit_at_10": hit,
                "bucket": "hit" if hit else "in_pool_unpacked",
                "gold_in_pool": True,
                "gold_fused_rank": 2 if hit else 40,
                "packed_size": 10,
            }
            for qid, hit in hits.items()
        ],
    }


def test_stage_decomposition_localizes_the_miss(summary_module):
    decomposition = summary_module.stage_decomposition(
        _report({"q1": True, "q2": False}, None)
    )

    assert decomposition["gold_in_pool_rate"] == 1.0
    assert decomposition["recall_at_10"] == 0.5
    assert decomposition["in_pool_unpacked"] == 1
    assert decomposition["in_pool_unpacked_gold_rank_within_k"] == 0
    assert decomposition["in_pool_unpacked_gold_rank_beyond_k"] == 1
    assert decomposition["packed_size_histogram"] == {"10": 2}


def test_recall_splits_by_adjudicated_distractor(summary_module):
    flags = {"q1": True, "q2": False}
    arm = summary_module.arm_recall(_report({"q1": True, "q2": False}, None), flags)

    assert arm["n"] == 2
    assert arm["recall_at_10"] == 0.5
    assert arm["with_adjudicated_distractor"] == {
        "n": 1, "recall_at_5": 1.0, "recall_at_10": 1.0
    }
    assert arm["without_adjudicated_distractor"] == {
        "n": 1, "recall_at_5": 0.0, "recall_at_10": 0.0
    }


def test_paired_flips_counts_both_directions(summary_module):
    left = _report({"q1": True, "q2": True, "q3": False}, None)
    right = _report({"q1": True, "q2": False, "q3": True}, None)

    assert summary_module.paired_flips(left, right, "hit_at_10") == {
        "both_hit": 1, "left_only": 1, "right_only": 1, "neither": 0
    }


def test_paired_flips_rejects_mismatched_question_sets(summary_module):
    with pytest.raises(RuntimeError, match="same question set"):
        summary_module.paired_flips(
            _report({"q1": True}, None), _report({"q2": True}, None), "hit_at_10"
        )


def test_identical_pack_witness_reads_as_cap_did_not_run(summary_module):
    flags = {"q1": True, "q2": False}
    hits = {"q1": True, "q2": False}
    built = summary_module.build_summary(
        Path("g.jsonl"),
        _report(hits, None),
        _report(hits, None, pack_items=4, pack_chars=400),
        _report(hits, 1200, pack_items=4, pack_chars=400),
        flags,
    )

    witness = built["hypothesis_b_render_witness"]
    assert witness["cap_changed_packing"] is False
    assert "not evidence that the cap fails" in witness["reading"]


def test_changed_pack_witness_reads_as_interpretable(summary_module):
    flags = {"q1": True, "q2": False}
    hits = {"q1": True, "q2": False}
    built = summary_module.build_summary(
        Path("g.jsonl"),
        _report(hits, None),
        _report(hits, None, pack_items=4, pack_chars=4000),
        _report(hits, 1200, pack_items=6, pack_chars=2400),
        flags,
    )

    witness = built["hypothesis_b_render_witness"]
    assert witness["cap_changed_packing"] is True
    assert witness["cap_1200"]["packed_items_total"] == 6
    assert json.loads(json.dumps(built))["arms"]["bm25_control"]["n"] == 2


# --- Phase 1c scoped comparison ------------------------------------------


@pytest.fixture(scope="module")
def scoped_module():
    spec = importlib.util.spec_from_file_location(
        "track_r_scoped_bm25_compare", ROOT / "scripts/track_r_scoped_bm25_compare.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_scoped_comparison_paired_counts_are_per_question_flips(scoped_module):
    rows = [
        {"left": True, "right": True},
        {"left": True, "right": False},
        {"left": False, "right": True},
        {"left": False, "right": True},
        {"left": False, "right": False},
    ]

    flips = scoped_module.paired(
        rows, lambda row: row["left"], lambda row: row["right"]
    )

    assert flips == {
        "both_hit": 1,
        "left_only": 1,
        "right_only": 2,
        "neither": 1,
        "discordant": 3,
    }


def test_scoped_comparison_scoping_witness_flags_a_haystack_mismatch(scoped_module):
    corpus_rows = [
        {
            "attempt_id": "a1",
            "events": [
                {"sequence": 0, "role": "user", "text": "one"},
                {"sequence": 1, "role": "assistant", "text": "two"},
            ],
        }
    ]
    goldens = [{"question_id": "q1", "provenance": [{"attempt_id": "a1"}]}]
    matched = scoped_module.scoping_witness(
        goldens,
        corpus_rows,
        {"per_question": [{"question_id": "q1", "documents_searched": 2}]},
        {"per_question": [{"question_id": "q1", "pool_size": 2}]},
    )
    mismatched = scoped_module.scoping_witness(
        goldens,
        corpus_rows,
        {"per_question": [{"question_id": "q1", "documents_searched": 7}]},
        {"per_question": [{"question_id": "q1", "pool_size": 2}]},
    )

    assert matched["bm25_documents_searched_equals_attempt_events"] == 1
    assert matched["memphant_pool_size_le_bm25_documents_searched"] == 1
    assert matched["violations"] == []
    assert mismatched["bm25_documents_searched_equals_attempt_events"] == 0
    assert mismatched["violations"] == [
        {"question_id": "q1", "documents_searched": 7, "attempt_events": 2}
    ]
