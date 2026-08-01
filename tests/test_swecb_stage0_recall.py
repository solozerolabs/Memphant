from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/swecb_stage0_recall.py"
PREREG = ROOT / "docs/build-log/artifacts/s5-swecb/stage0-prereg.json"


def _load():
    spec = importlib.util.spec_from_file_location("swecb_stage0", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


# --------------------------------------------------------------- timestamps


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2022-03-03T15:14:54Z", "2022-03-03T15:14:54Z"),
        ("2023-01-23 04:10:47", "2023-01-23T04:10:47Z"),
        ("2022-03-03T15:14:54+00:00", "2022-03-03T15:14:54Z"),
    ],
)
def test_both_upstream_created_at_formats_normalize_to_rfc3339_utc(raw, expected):
    """The pinned parquet ships two formats; /v1/episodes accepts only one.

    707 experience rows use `...Z`, 300 use `YYYY-MM-DD HH:MM:SS`. The naive
    adapter died at HTTP 422 on the 300.
    """
    assert MODULE.canonical_observed_at(raw) == expected


def test_a_non_utc_offset_is_refused_rather_than_silently_shifted():
    """Naive-UTC is an assumption about *absent* offsets only. A stated non-UTC
    offset would mean the upstream semantics changed, and silently converting it
    would move timestamps under us."""
    with pytest.raises(RuntimeError):
        MODULE.canonical_observed_at("2023-01-23T04:10:47+05:30")


# --------------------------------------------------------------- body arms


def _row(**overrides):
    row = {
        "instance_id": "repo__repo-1",
        "repo": "repo/repo",
        "base_commit": "a" * 40,
        "problem_statement": "The parser drops trailing commas",
        "hints_text": "See the tokenizer",
        "patch": "diff --git a/p.py b/p.py\n+SENTINEL_PATCH_LINE = True",
        "created_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def test_the_primary_body_carries_no_patch():
    """The admissible pool is patch-free. 37.2% of gold parents contain an exact
    added line of their target's patch, so a with-patch pool leaks the answer."""
    body = MODULE.experience_body(_row(), "patchfree")
    assert "SENTINEL_PATCH_LINE" not in body
    assert "diff --git" not in body
    assert "The parser drops trailing commas" in body
    assert "See the tokenizer" in body


def test_the_diagnostic_body_carries_the_patch_and_is_a_superset():
    body = MODULE.experience_body(_row(), "withpatch")
    assert "SENTINEL_PATCH_LINE" in body
    assert body.startswith(MODULE.experience_body(_row(), "patchfree"))


def test_an_unknown_body_variant_is_refused_not_defaulted():
    with pytest.raises(ValueError):
        MODULE.experience_body(_row(), "trajectory")


def test_a_missing_hints_field_does_not_produce_a_none_body():
    body = MODULE.experience_body(_row(hints_text=None), "patchfree")
    assert "None" not in body
    assert "(none)" in body


# --------------------------------------------------------------- scoring


def _record(target, gold, packed=None, retrieval=None, in_candidates=None, variant=0):
    packed = packed or {}
    retrieval = retrieval or {}
    in_candidates = in_candidates or {}
    return {
        "target_id": target,
        "variant": variant,
        "variant_count": 1,
        "repo": "repo/repo",
        "gold_parents": gold,
        "gold": [
            {
                "parent_id": parent,
                "packed_rank": packed.get(parent),
                "retrieval_rank": retrieval.get(parent),
                "discard_reason": None,
                "in_candidates": in_candidates.get(parent, parent in retrieval),
            }
            for parent in gold
        ],
        "self_retrieved": False,
        "returned_items": 5,
        "candidate_count": 40,
        "trace_id": f"trace-{target}-{variant}",
        "token_estimate": 100,
        "latency_ms": 10,
    }


def test_recall_at_k_is_a_rank_threshold_not_a_membership_test():
    result = {
        "records": [
            _record("t1", ["p1"], packed={"p1": 3}),
            _record("t2", ["p2"], packed={"p2": 9}),
        ]
    }
    summary = MODULE.score(result)
    packed = summary["primary"]["packed_recall_at_k"]
    assert packed["1"] == 0.0
    assert packed["3"] == 0.5
    assert packed["10"] == 1.0


def test_any_parent_counts_a_task_once_however_many_parents_it_has():
    """Only three targets have two distinct parents, but the rule still must not
    double-count them."""
    result = {"records": [_record("t1", ["p1", "p2"], packed={"p2": 2})]}
    summary = MODULE.score(result)
    assert summary["n_distinct_tasks"] == 1
    assert summary["primary"]["packed_recall_at_k"]["3"] == 1.0
    assert summary["all_parent_two_parent_tasks"]["packed_recall_at_k"]["3"] == 0.0


def test_duplicate_related_rows_collapse_to_one_task_taking_the_best_rank():
    """Related ships 376 rows over 357 distinct ids. A duplicated instance is not
    an independent pair, so the distinct-task view must not count it twice."""
    result = {
        "records": [
            _record("t1", ["p1"], packed={"p1": 20}, variant=0),
            _record("t1", ["p1"], packed={"p1": 2}, variant=1),
        ]
    }
    summary = MODULE.score(result)
    assert summary["n_distinct_tasks"] == 1
    assert summary["n_rows"] == 2
    assert summary["primary"]["packed_recall_at_k"]["3"] == 1.0
    # the row census keeps both, so it must NOT report 100% at k=3
    assert summary["secondary_row_census"]["packed_recall_at_k"]["3"] == 0.5


def test_miss_taxonomy_separates_never_retrieved_from_retrieved_but_unpacked():
    """These two miss classes need different fixes and must never be merged: one
    is a retriever problem, the other is a packing-budget problem we own a lever
    for."""
    result = {
        "records": [
            _record("hit", ["p"], packed={"p": 1}, retrieval={"p": 1}),
            _record("absent", ["p"], in_candidates={"p": False}),
            _record("unpacked", ["p"], retrieval={"p": 2}, in_candidates={"p": True}),
            _record("below", ["p"], retrieval={"p": 40}, in_candidates={"p": True}),
        ]
    }
    taxonomy = MODULE.score(result)["miss_taxonomy_at_k5"]
    assert taxonomy["hit"] == 1
    assert taxonomy["absent_from_candidates"] == 1
    assert taxonomy["ranked_within_k_but_not_packed"] == 1
    assert taxonomy["ranked_below_cut"] == 1


# --------------------------------------------------------------- prereg


def test_the_decision_bands_are_committed_and_partition_the_unit_interval():
    """A band set with a gap or an overlap is a band set that can be argued
    after the fact."""
    prereg = json.loads(PREREG.read_text())
    rule = prereg["decision_rule"]
    assert rule["committed_before_any_cell_exists"] is True
    assert "0.50" in rule["GREEN"]
    assert "0.20 <=" in rule["AMBER"] and "< 0.50" in rule["AMBER"]
    assert "< 0.20" in rule["RED"]


def test_the_amendment_records_why_the_pool_changed():
    """The pool definition moved after the first prereg. That is only legitimate
    because it happened before any cell existed and the reason is measured."""
    prereg = json.loads(PREREG.read_text())
    amendment = prereg["amendment_1"]
    assert amendment["amended_before_any_cell_existed"] is True
    overlap = amendment["measured_evidence"]["patch_overlap"]
    assert overlap["gold_parent_contains_an_exact_target_added_line_over_20_chars"]["pct"] > 30
    assert overlap["same_repo_random_non_parent_control"]["mean_added_line_overlap"] < 0.01
