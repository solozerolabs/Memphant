"""Unit tests for the R0-T6 coding-lane golden miner's pure functions
(``scripts/code_lane_mine.py``): verbatim span location within one event's
text, the too-generic-span rejection check, candidate-pool construction, and
the stratified deterministic candidate sample. No network, no DB, no
OpenRouter calls — these run under plain ``pytest tests/``.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def clm():
    return _load("code_lane_mine", "scripts/code_lane_mine.py")


# --- locate_span_in_event -----------------------------------------------


def test_locate_span_in_event_exact_match(clm):
    text = "npm test failed: 2 tests did not pass"
    located = clm.locate_span_in_event(text, "2 tests did not pass")
    assert located is not None
    start, end, exact = located
    assert text[start:end] == exact == "2 tests did not pass"


def test_locate_span_in_event_not_found(clm):
    assert clm.locate_span_in_event("some text here", "not present anywhere") is None


def test_locate_span_in_event_whitespace_normalized_fallback(clm):
    text = "the build\nfailed with exit code 1"
    # Model reflowed the whitespace across the original newline.
    located = clm.locate_span_in_event(text, "the build failed with exit code 1")
    assert located is not None
    start, end, exact = located
    assert text[start:end] == exact
    assert "the build" in exact and "exit code 1" in exact


def test_locate_span_in_event_empty_span_is_none(clm):
    assert clm.locate_span_in_event("some text", "   ") is None


# --- too_generic -----------------------------------------------------------


def test_too_generic_false_when_span_rare(clm):
    corpus_index = {
        "a1": "unique error message here",
        "a2": "totally different content",
    }
    assert clm.too_generic("unique error message", corpus_index, threshold=3) is False


def test_too_generic_true_when_span_common(clm):
    corpus_index = {
        "a1": "npm install completed successfully",
        "a2": "the npm install completed successfully too",
        "a3": "again: npm install completed successfully",
        "a4": "and once more npm install completed successfully",
    }
    assert clm.too_generic("npm install completed successfully", corpus_index, threshold=3) is True


def test_too_generic_boundary_at_threshold(clm):
    corpus_index = {
        "a1": "the marker phrase appears",
        "a2": "the marker phrase appears",
        "a3": "the marker phrase appears",
    }
    # Exactly 3 distinct attempts contain it; threshold=3 means >3 rejects,
    # so exactly-3 must NOT be flagged as too generic.
    assert clm.too_generic("the marker phrase appears", corpus_index, threshold=3) is False


def test_too_generic_counts_distinct_attempts_not_occurrences(clm):
    corpus_index = {
        "a1": "repeat repeat repeat the phrase the phrase the phrase",
    }
    # Multiple occurrences within ONE attempt still count as one attempt: a
    # naive total-occurrence count (3) would wrongly exceed threshold=1, but
    # the correct distinct-attempt count (1) does not.
    assert clm.too_generic("the phrase", corpus_index, threshold=1) is False


# --- build_candidate_pool ----------------------------------------------------


def _corpus_row(attempt_id: str, events: list[dict]) -> dict:
    return {"attempt_id": attempt_id, "run_id": "r1", "started_at": "2026-01-01T00:00:00Z", "events": events}


def test_build_candidate_pool_filters_by_min_chars(clm):
    corpus = [
        _corpus_row(
            "a1",
            [
                {"sequence": 0, "role": "assistant", "text": "x" * 250, "event_id": "e0", "truncated": False},
                {"sequence": 1, "role": "assistant", "text": "short", "event_id": "e1", "truncated": False},
            ],
        )
    ]
    pool = clm.build_candidate_pool(corpus, min_chars=200)
    assert len(pool) == 1
    assert pool[0]["sequence"] == 0
    assert pool[0]["attempt_id"] == "a1"


def test_build_candidate_pool_key_is_attempt_and_sequence(clm):
    corpus = [
        _corpus_row(
            "a1",
            [{"sequence": 3, "role": "toolResult", "text": "y" * 300, "event_id": "e3", "truncated": False}],
        )
    ]
    pool = clm.build_candidate_pool(corpus, min_chars=200)
    assert clm.candidate_key(pool[0]) == "a1::3"


# --- stratified_candidates ---------------------------------------------------


def _candidate(attempt_id: str, sequence: int, role: str, text: str) -> dict:
    return {"attempt_id": attempt_id, "sequence": sequence, "role": role, "text": text, "event_id": f"e{sequence}"}


def test_stratified_candidates_is_deterministic(clm):
    pool = [_candidate(f"a{i}", i, "assistant" if i % 2 == 0 else "toolResult", "x" * 250) for i in range(20)]
    out1 = clm.stratified_candidates(pool, seed=20260713, want=10)
    out2 = clm.stratified_candidates(pool, seed=20260713, want=10)
    assert [clm.candidate_key(c) for c in out1] == [clm.candidate_key(c) for c in out2]


def test_stratified_candidates_respects_want_cap(clm):
    pool = [_candidate(f"a{i}", i, "assistant", "x" * 250) for i in range(20)]
    out = clm.stratified_candidates(pool, seed=20260713, want=5)
    assert len(out) == 5


def test_stratified_candidates_draws_from_multiple_roles(clm):
    pool = (
        [_candidate(f"a{i}", i, "assistant", "x" * 250) for i in range(10)]
        + [_candidate(f"b{i}", i, "toolResult", "y" * 250) for i in range(10)]
    )
    out = clm.stratified_candidates(pool, seed=20260713, want=10)
    roles = {c["role"] for c in out}
    assert roles == {"assistant", "toolResult"}


# --- build_context_preview ---------------------------------------------------


def test_build_context_preview_includes_preceding_events_only(clm):
    events = [
        {"sequence": 0, "role": "user", "text": "please fix the bug"},
        {"sequence": 1, "role": "assistant", "text": "looking into it"},
        {"sequence": 2, "role": "toolResult", "text": "TARGET TEXT HERE"},
    ]
    preview = clm.build_context_preview(events, target_index=2, max_events=3, max_chars_each=300)
    assert "please fix the bug" in preview
    assert "looking into it" in preview
    assert "TARGET TEXT HERE" not in preview


def test_build_context_preview_truncates_each_event(clm):
    events = [
        {"sequence": 0, "role": "user", "text": "a" * 500},
        {"sequence": 1, "role": "assistant", "text": "TARGET"},
    ]
    preview = clm.build_context_preview(events, target_index=1, max_events=3, max_chars_each=50)
    # The 500-char event is clipped in the preview.
    assert "a" * 500 not in preview
    assert "a" * 50 in preview


def test_build_context_preview_empty_when_target_is_first(clm):
    events = [{"sequence": 0, "role": "user", "text": "TARGET"}]
    preview = clm.build_context_preview(events, target_index=0, max_events=3, max_chars_each=300)
    assert preview == ""


# --- no-provider-path guard -------------------------------------------------


def test_module_has_no_provider_calling_path(clm):
    """The miner CLI is deleted, so this module must stay purely local.

    It previously called ``gate_mine_goldens.MinerCli`` — a name that exists
    nowhere — so it raised AttributeError before ever reaching a provider. This
    guard fails if anyone reintroduces a paid path here instead of in
    ``scripts/track_r_mine.py``: no reader/miner machinery imported, and no CLI
    entry point to invoke one. Checked over the AST, not the raw text, so the
    module docstring may name the deleted machinery without tripping it.
    """
    tree = ast.parse((ROOT / "scripts" / "code_lane_mine.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {"__future__", "random", "re"}, f"unexpected imports: {imported}"
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {"MinerCli", "ReaderCli"} & attrs

    assert not hasattr(clm, "main"), "code_lane_mine must expose no CLI entry point"
    assert not hasattr(clm, "mine"), "mining belongs in track_r_mine.py"
    # Every surviving symbol is a pure function or a compiled regex.
    assert sorted(n for n in vars(clm) if not n.startswith("_") and callable(getattr(clm, n))) == [
        "build_candidate_pool",
        "build_context_preview",
        "candidate_key",
        "content_words",
        "lexical_overlap",
        "locate_span_in_event",
        "stratified_candidates",
        "too_generic",
    ]


def test_live_helpers_are_the_ones_track_r_mine_imports():
    """track_r_mine.py is the reason this module still exists; keep that true."""
    source = (ROOT / "scripts" / "track_r_mine.py").read_text()
    assert "from code_lane_mine import lexical_overlap, locate_span_in_event, too_generic" in source
