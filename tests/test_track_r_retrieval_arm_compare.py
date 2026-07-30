"""Unit tests for the Track R retrieval-arm comparator
(``scripts/track_r_retrieval_arm_compare.py``). Pure statistics and pairing —
no DB, no server, no run artifacts.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compare():
    spec = importlib.util.spec_from_file_location(
        "track_r_retrieval_arm_compare",
        ROOT / "scripts/track_r_retrieval_arm_compare.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_mcnemar_exact_reproduces_the_committed_phase1c_p_values(compare):
    """The Phase 1c build log records p=0.0436 at k=10 (14 vs 28 discordant)
    and p=0.00001 at k=5 (14 vs 50). Reproducing both pins the statistic."""
    assert compare.mcnemar_exact_p(14, 28) == pytest.approx(0.0436, abs=5e-5)
    assert compare.mcnemar_exact_p(14, 50) == pytest.approx(0.0000071, abs=5e-7)


def test_mcnemar_exact_is_symmetric_and_saturates_without_evidence(compare):
    assert compare.mcnemar_exact_p(0, 0) == 1.0
    assert compare.mcnemar_exact_p(1, 0) == 1.0
    assert compare.mcnemar_exact_p(3, 7) == compare.mcnemar_exact_p(7, 3)
    assert compare.mcnemar_exact_p(0, 20) < 1e-5


def test_paired_block_counts_the_four_cells(compare):
    block = compare.paired_block([True, True, False, False], [True, False, True, False])
    assert block["both"] == 1
    assert block["arm_only"] == 1
    assert block["control_only"] == 1
    assert block["neither"] == 1
    assert block["mcnemar_exact_p"] == 1.0


def test_fused_hits_reads_the_ranked_stage_and_honours_the_question_order(compare):
    report = {
        "per_question": [
            {"question_id": "b", "gold_fused_rank": 11},
            {"question_id": "a", "gold_fused_rank": 3},
            {"question_id": "c", "gold_fused_rank": None},
        ]
    }
    assert compare.fused_hits(report, ["a", "b", "c"], 10) == [True, False, False]
    assert compare.fused_hits(report, ["a", "b", "c"], 5) == [True, False, False]
    assert compare.fused_hits(report, ["b", "a", "c"], 11) == [True, True, False]
