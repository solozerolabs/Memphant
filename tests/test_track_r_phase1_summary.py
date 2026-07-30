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
        "pack_drop_summary": _pack_summary(pack_items, pack_chars),
        "runtime_identity": {"command": f"cmd cap={cap}"},
        "per_question": [
            {"question_id": qid, "hit_at_5": hit, "hit_at_10": hit}
            for qid, hit in hits.items()
        ],
    }


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
