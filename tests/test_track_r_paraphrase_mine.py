"""Unit tests for the W0.1 paraphrase miner's pure gates.

The paraphrase variant's whole claim is that identification survives while the
identifier *tokens* do not. Three mechanisms carry that claim — the leakage
metric, the identifier-withholding gate, and the BM25-nearest distractor
selector — and each is pinned here so a future edit cannot quietly re-open the
lexical give-away the bank exists to remove. The end-to-end determinism check
lives in ``scripts/track_r_paraphrase_mine.py --verify-lock`` (it needs the warm
agent cache).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import track_r_leakage as leak  # noqa: E402
import track_r_paraphrase_mine as pm  # noqa: E402


# --- the leakage metric ----------------------------------------------------


def test_coverage_is_question_side_normalised():
    """Coverage is |Q & E| / |Q|: it asks what share of the QUESTION is copied
    out of the event, so a long event cannot dilute it."""
    assert leak.coverage("retry backoff", "retry backoff widened to five") == 1.0
    assert leak.coverage("the retry backoff assertion", "retry backoff") == 0.5
    assert leak.coverage("alpha beta", "alpha " + "filler " * 500) == 0.5
    assert leak.coverage("alpha beta", "gamma delta") == 0.0


def test_coverage_tokenizer_drops_sub_three_char_noise():
    """T(s) is [a-z0-9_]{3,} lowercased — the tokenizer that reproduces the
    program spec's 0.396/0.388 reference on the original bank."""
    assert leak.tokens("A of the retry_count v2") == {"the", "retry_count"}


def test_coverage_is_case_insensitive():
    assert leak.coverage("FeatureStore lookup", "featurestore lookup") == 1.0


# --- identifier withholding ------------------------------------------------


def test_identifier_forms_catch_every_banned_surface():
    forms = pm.identifier_forms(
        "def parse_config(self):\n    return FeatureStore(path='src/io/fits.py', "
        "opt=module.attribute)"
    )
    assert "src/io/fits.py" in forms       # file path
    assert "parse_config" in forms         # snake_case
    assert "featurestore" in forms         # CamelCase, lowercased
    assert "module.attribute" in forms     # dotted


def test_leaked_identifiers_flags_a_copied_symbol_in_any_casing():
    target = "def parse_config(self): pass"
    assert pm.leaked_identifiers("What did PARSE_CONFIG return?", target) == ["parse_config"]
    assert pm.leaked_identifiers("What did the config parser return?", target) == []


def test_leaked_identifiers_flags_a_copied_path():
    target = "Editing /workspace/proj/src/io/fits.py now"
    assert "/workspace/proj/src/io/fits.py" in pm.leaked_identifiers(
        "What changed in /workspace/proj/src/io/fits.py?", target
    )
    assert pm.leaked_identifiers("What changed in the FITS reader?", target) == []


def test_answer_run_leaked_catches_a_quoted_fragment_of_the_answer():
    span = "AssertionError: expected 3 retries but observed 1"
    assert pm.answer_run_leaked("Why did it say expected 3 retries but observed something else?", span)
    assert not pm.answer_run_leaked("How many retries did the run actually observe?", span)


def test_answer_run_leaked_ignores_spans_shorter_than_the_run():
    assert not pm.answer_run_leaked("what was the exit code two", "code two", run=4)


# --- distractor selection --------------------------------------------------


def _events(*texts: str) -> list[dict]:
    return [
        {"sequence": index, "text": text, "role": "toolResult", "event_id": f"e{index}"}
        for index, text in enumerate(texts)
    ]


def test_bm25_rank_events_excludes_the_target_and_ranks_the_nearest_first():
    events = _events(
        "the retry backoff decorator was widened to five attempts",
        "unrelated readme rendering of installation instructions",
        "retry backoff decorator raised on the fifth attempt",
    )
    ranked = pm.bm25_rank_events(events, "retry backoff decorator attempt", 0, 5)
    assert 0 not in ranked
    assert ranked[0] == 2


def test_bm25_rank_events_returns_the_adversarial_set_not_an_empty_one():
    """The 100% distractor-coverage bar (bar doc SS4.3, W0.4) rests on this:
    in an attempt-scoped haystack the non-target set is never empty."""
    events = _events("alpha beta gamma", "alpha delta epsilon")
    assert pm.bm25_rank_events(events, "alpha", 0, 5) == [1]


def test_bm25_rank_events_is_capped_by_k():
    events = _events(*[f"shared token number {n}" for n in range(10)])
    assert len(pm.bm25_rank_events(events, "shared token", 0, 3)) == 3


# --- adjudication parsing --------------------------------------------------


def test_parse_adjudication_requires_the_uniqueness_verdict():
    """The original miner's schema had no uniqueness field. Accepting a reply
    without one would silently drop the gate that replaces the withheld
    identifier tokens."""
    without = '{"target_identified": true, "reason": "x", "distractors": []}'
    assert pm.parse_adjudication(without, 0) is None
    with_it = (
        '{"target_identified": true, "uniquely_identified_within_attempt": true, '
        '"reason": "x", "distractors": []}'
    )
    assert pm.parse_adjudication(with_it, 0)["uniquely_identified_within_attempt"] is True


def test_parse_adjudication_rejects_a_wrong_length_distractor_list():
    reply = (
        '{"target_identified": true, "uniquely_identified_within_attempt": true, '
        '"reason": "x", "distractors": [{"index": 1, "also_answers": false}]}'
    )
    assert pm.parse_adjudication(reply, 3) is None
    assert pm.parse_adjudication(reply, 1) is not None
