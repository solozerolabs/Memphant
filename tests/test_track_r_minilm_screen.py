import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("track_r_minilm_screen", ROOT / "scripts/track_r_minilm_screen.py")
screen = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(screen)


def pool_row(qid="q1", count=70):
    return {
        "question_id": qid,
        "query": "question",
        "pool": [
            {"unit_id": f"u{i}", "body": f"body {i}", "fused_rank": i + 1, "is_gold": i == 20}
            for i in range(count)
        ],
    }


def test_candidate_projection_is_top64_and_blind():
    rows = screen.candidate_rows([pool_row(), pool_row("empty", 0)])
    assert [row["qid"] for row in rows] == ["q1"]
    assert len(rows[0]["docs"]) == 64
    assert rows[0]["docs"][0] == {"doc_id": "u0", "text": "body 0", "chunks": []}
    assert "is_gold" not in str(rows)


def test_scores_must_be_exactly_contained_in_handed_pool():
    row = pool_row(count=2)
    with pytest.raises(RuntimeError, match="score ids"):
        screen.selections_from_scores(
            [row],
            [{"qid": "q1", "scores": {"u0": 1.0, "outside": 2.0}, "docs_scored": 2}],
        )


def test_ranking_uses_score_then_original_order_and_records_liveness():
    row = pool_row(count=3)
    selected, live = screen.selections_from_scores(
        [row],
        [{"qid": "q1", "scores": {"u0": 0.1, "u1": 0.9, "u2": 0.9}, "docs_scored": 3}],
    )
    assert selected["q1"] == ["body 1", "body 2", "body 0"]
    assert live == {
        "pool_containment_violations": 0,
        "raw_event_access": False,
        "gold_labels_in_model_input": False,
        "score_rows": 1,
        "empty_pool_rows": 0,
        "docs_scored": 3,
    }


def test_paired_cells_are_directional_and_exact():
    stats = screen.paired({"a": True, "b": False, "c": True}, {"a": False, "b": True, "c": True}, ["a", "b", "c"])
    assert (stats["b"], stats["c"], stats["n_d"], stats["delta"]) == (1, 1, 2, 0.0)
    assert stats["mcnemar_exact_p"] == 1.0
