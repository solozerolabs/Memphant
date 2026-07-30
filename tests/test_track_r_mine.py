"""Unit tests for the Track R repo-memory golden miner's pure gates.

The three failure modes recorded in the v3 rejection receipt each have a
mechanical gate in ``scripts/track_r_mine.py``; these tests pin the gates so a
future edit cannot quietly re-open one. The end-to-end determinism check lives
in ``scripts/track_r_mine.py --verify-lock`` (it needs the warm agent cache).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import track_r_mine as tr  # noqa: E402


def test_skeleton_collapses_the_rejected_generic_template():
    """The v3 bank's 19 path-varied file-not-found questions collapse to ONE
    skeleton, so the per-skeleton cap rejects all but the first two."""
    questions = [
        "In the astropy/astropy trajectory, what exact diagnostic followed reading src/io/fits.py?",
        "In the pandas-dev/pandas trajectory, what exact diagnostic followed reading pandas/core/frame.py?",
        "In the tobymao/sqlglot trajectory, what exact diagnostic followed reading sqlglot/parser.py?",
    ]
    skeletons = {tr.skeleton(q) for q in questions}
    assert len(skeletons) == 1


def test_skeleton_keeps_genuinely_different_questions_apart():
    a = tr.skeleton("Which assertion failed after the retry decorator was widened?")
    b = tr.skeleton("What return type did the config loader end up declaring?")
    assert a != b and a and b


def test_tokens_include_identifiers_and_dotted_names():
    found = tr.tokens("Fix parse_config in sqlglot/dialects/hive.py (see module.attr)")
    assert "parse_config" in found
    assert "module.attr" in found
    assert "hive.py" in found


def test_file_paths_finds_source_paths_only():
    text = "edited src/pkg/module_one.py and tests/test_module_one.py; skipped a.py"
    assert tr.file_paths(text) == ["src/pkg/module_one.py", "tests/test_module_one.py"]


def _attempt(events):
    return {
        "attempt_id": "att-1",
        "run_id": "run-1",
        "repository": "acme/widget",
        "started_at": "2025-01-01T00:00:00Z",
        "events": [
            {"sequence": i, "role": role, "text": text, "event_id": f"e{i}"}
            for i, (role, text) in enumerate(events)
        ],
    }


def test_state_churn_requires_a_separated_earlier_touch():
    body = "x" * 200
    row = _attempt(
        [
            ("assistant", f"open src/pkg/widget_core.py {body}"),
            ("toolResult", f"unrelated {body}"),
            ("toolResult", f"patched src/pkg/widget_core.py again {body}"),
        ]
    )
    shapes = {
        (c["shape"], c["sequence"]) for c in tr.shape_candidates_for_attempt(row, 200)
    }
    assert ("state-churn", 2) in shapes
    assert ("state-churn", 0) not in shapes


def test_file_symbol_grounding_needs_both_a_path_and_a_symbol():
    body = "y" * 200
    with_symbol = _attempt([("toolResult", f"src/pkg/widget_core.py\ndef normalize(x):\n {body}")])
    without_symbol = _attempt([("toolResult", f"src/pkg/widget_core.py listing {body}")])
    assert any(
        c["shape"] == "file-symbol-grounding"
        for c in tr.shape_candidates_for_attempt(with_symbol, 200)
    )
    assert not any(
        c["shape"] == "file-symbol-grounding"
        for c in tr.shape_candidates_for_attempt(without_symbol, 200)
    )


def test_task_resumption_is_late_and_unresolved_only():
    body = "z" * 200
    row = _attempt(
        [
            ("toolResult", f"AssertionError early {body}"),
            ("toolResult", f"quiet middle {body}"),
            ("toolResult", f"AssertionError late {body}"),
        ]
    )
    sequences = {
        c["sequence"] for c in tr.shape_candidates_for_attempt(row, 200)
        if c["shape"] == "task-resumption"
    }
    assert sequences == {2}


def test_draw_candidates_is_deterministic_and_round_robins_shapes():
    pool = [
        {
            "shape": shape,
            "attempt_id": f"att-{i}",
            "sequence": i,
            "text": "t",
        }
        for shape in tr.SHAPES
        for i in range(10)
    ]
    first = tr.draw_candidates(pool, 7, 4)
    second = tr.draw_candidates(pool, 7, 4)
    assert [tr.candidate_key(c) for c in first] == [tr.candidate_key(c) for c in second]
    assert [c["shape"] for c in first[:3]] == list(tr.SHAPES)
    assert len(first) == 12


def test_draw_candidates_takes_at_most_one_candidate_per_attempt_per_shape():
    pool = [
        {"shape": "state-churn", "attempt_id": "att-1", "sequence": i, "text": "t"}
        for i in range(5)
    ]
    drawn = tr.draw_candidates(pool, 7, 5)
    assert len(drawn) == 1


def test_cache_key_is_content_addressed():
    a = tr.cache_key("generate-state-churn", "sys", "prompt")
    assert a == tr.cache_key("generate-state-churn", "sys", "prompt")
    assert a != tr.cache_key("generate-state-churn", "sys", "prompt2")
    assert a != tr.cache_key("adjudicate", "sys", "prompt")


def test_parse_adjudication_requires_one_verdict_per_distractor():
    good = '{"target_identified": true, "reason": "r", "distractors": [{"index": 1, "also_answers": false, "why": "w"}]}'
    assert tr.parse_adjudication(good, 1)["target_identified"] is True
    assert tr.parse_adjudication(good, 2) is None
    assert tr.parse_adjudication('{"target_identified": true}', 0) is None
    assert tr.parse_adjudication("not json", 0) is None


def test_parse_adjudication_accepts_a_fenced_reply_with_no_distractors():
    reply = '```json\n{"target_identified": false, "reason": "vague", "distractors": []}\n```'
    parsed = tr.parse_adjudication(reply, 0)
    assert parsed == {"target_identified": False, "reason": "vague", "distractors": []}
