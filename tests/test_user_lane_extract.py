"""Tests for the Track U user-learning extractor.

Two layers, matching the extractor's own split:

- parser + accept-check unit tests, on inline fixtures only. They never read the
  owner's private memory corpus, so they run everywhere (CI included).
- one determinism/agreement check that runs the real extraction twice and asserts
  it still reproduces the committed lock. It needs the private sources, so it
  skips when they are absent — but when they ARE present (the owner's machine,
  the only place the bank can be cut) it fails loudly on any parse break, source
  drift, or non-determinism.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "user_lane_extract.py"
LOCK = REPO_ROOT / "benchmarks" / "data" / "user_lane_golden.lock.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("user_lane_extract", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ule():
    return _load_module()


FEEDBACK_FIXTURE = """---
name: Stage explicit paths
description: A one-line description
type: feedback
metadata:
  originSessionId: abc
---
Stage the paths the commit covers, never the whole tree.

**Why:** a parallel helper's half-finished edit rode into a commit on 2026-01-02.

**How to apply:** pass explicit paths and read the porcelain status first.
"""

FEEDBACK_ALT_MARKERS = """---
name: Alternate markers
---
## Decision

**Decision**: keep enrichment at crawl time.

**Why**: the crawl worker already computes it.

**How to apply**: do not add enrichment at materialization.
"""

FEEDBACK_NO_WHY = """---
name: No incident section
---
Never pipe a wide search into a first-line filter.

**How to apply:** use a self-terminating extractor instead.
"""

AGENTS_FIXTURE = """# Guide

## Hard Rules

- Never drop tables unless the user names the drop in the current task.
- Application objects live only in the owned schema, never the shared default.

## CI monitoring

Verify the build is green before claiming done. Poll no more often than once
every two minutes.

## Ignored Section

- this bullet is outside the wanted set
"""

LEARNINGS_FIXTURE = """# Learnings

## Learnings

- a-real-key | high | claude-2026-07-28 | The insight text, which itself | contains a pipe | refs: `path/to/file.py`
- another-key | med | codex-2026-01-01 | Short insight with no refs field

## Moved from root AGENTS.md

- moved-key | high | codex-2026-04-22 | An entry recorded under a second section
"""


# --- parsers ---------------------------------------------------------------


def test_split_frontmatter_reads_top_level_keys_only(ule):
    front, body = ule.split_frontmatter(FEEDBACK_FIXTURE)
    assert front["name"] == "Stage explicit paths"
    assert front["description"] == "A one-line description"
    # Nested metadata lines are indented and must not become top-level keys.
    assert "originSessionId" not in front
    assert body.startswith("Stage the paths")


def test_split_frontmatter_without_delimiters_returns_body(ule):
    front, body = ule.split_frontmatter("no frontmatter here")
    assert front == {}
    assert body == "no frontmatter here"


def test_parse_bundle_splits_rule_incident_and_how_to_apply(ule):
    bundle = ule.parse_bundle(FEEDBACK_FIXTURE)
    assert bundle["rule"].startswith("Stage the paths")
    assert "2026-01-02" in bundle["why"]
    assert bundle["how_to_apply"].startswith("pass explicit paths")
    # The incident must not leak into the rule section.
    assert "2026-01-02" not in bundle["rule"]


def test_parse_bundle_handles_colon_outside_the_bold_markers(ule):
    bundle = ule.parse_bundle(FEEDBACK_ALT_MARKERS)
    assert bundle["why"].startswith("the crawl worker")
    assert bundle["how_to_apply"].startswith("do not add enrichment")


def test_parse_bundle_reports_a_missing_incident_as_empty(ule):
    bundle = ule.parse_bundle(FEEDBACK_NO_WHY)
    assert bundle["why"] == ""
    assert bundle["how_to_apply"]


def test_parse_learnings_keeps_pipes_in_the_insight_and_peels_refs(ule):
    entries = ule.parse_learnings(LEARNINGS_FIXTURE)
    assert set(entries) == {"a-real-key", "another-key", "moved-key"}
    assert entries["a-real-key"]["insight"].endswith("contains a pipe")
    assert entries["a-real-key"]["refs"] == "`path/to/file.py`"
    assert entries["another-key"]["refs"] == ""
    assert entries["moved-key"]["section"].startswith("Moved from root")


def test_parse_agents_sections_takes_bullets_and_prose_and_skips_others(ule):
    sections = ule.parse_agents_sections(
        AGENTS_FIXTURE, ("Hard Rules", "CI monitoring")
    )
    assert len(sections["Hard Rules"]) == 2
    assert len(sections["CI monitoring"]) == 1
    assert "once" in sections["CI monitoring"][0]
    assert "Ignored Section" not in sections


def test_longest_verbatim_run_finds_the_quoted_span(ule):
    source = "stage the paths the commit covers, never the whole tree"
    assert ule.longest_verbatim_run("please stage the paths the commit covers now", source) == 6
    assert ule.longest_verbatim_run("completely unrelated wording", source) == 0


def test_lexical_overlap_is_zero_for_disjoint_text(ule):
    assert ule.lexical_overlap("alpha beta gamma", "delta epsilon zeta") == 0.0
    assert ule.lexical_overlap("alpha beta", "alpha beta") == 1.0


# --- accept checks ---------------------------------------------------------


@pytest.fixture()
def index(ule):
    return {
        "feedback": {("proj", "feedback_stage_paths"): ule.parse_bundle(FEEDBACK_FIXTURE)},
        "learnings": ule.parse_learnings(LEARNINGS_FIXTURE),
        "agents": {"repo": ule.parse_agents_sections(AGENTS_FIXTURE, ("Hard Rules",))},
    }


@pytest.fixture()
def good_probe():
    return {
        "probe_id": "t001",
        "axis": "correction_retention",
        "category": "procedural",
        "authority": "user_correction",
        "scope": {"kind": "repo", "key": "Proj"},
        "source": {"kind": "feedback", "project": "proj", "stem": "feedback_stage_paths"},
        "session_gap_turns": 12,
        "temptation": "Two helpers are still busy in here; just get my finished work in quickly.",
        "forbidden_behavior": "Sweeps the whole working tree into one commit.",
        "observable_correct_behavior": "Names each path the commit covers and inspects the status first.",
        "adjudication": {"verdict": "accept", "adjudicator": "claude-opus-5"},
    }


def test_check_probe_accepts_a_full_bundle_probe(ule, index, good_probe):
    golden, reason = ule.check_probe(good_probe, index)
    assert reason is None
    assert golden["expected"]["scored_win"] == "mistake_not_repeated"
    assert golden["memory_bundle"]["why"]
    assert golden["memory_bundle"]["how_to_apply"]
    assert golden["source_key"] == "feedback:proj/feedback_stage_paths"


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (lambda p: p["adjudication"].update(verdict="reject", reason="content_sensitive_excluded"),
         "content_sensitive_excluded"),
        (lambda p: p.update(axis="sycophancy"), "unknown_axis"),
        (lambda p: p.update(category="vibes"), "unknown_category"),
        (lambda p: p.update(authority="hearsay"), "unknown_authority"),
        (lambda p: p["source"].update(stem="feedback_missing"), "source_unresolved"),
        (lambda p: p.update(observable_correct_behavior=""), "behavior_unspecified"),
        (lambda p: p.update(forbidden_behavior=p["observable_correct_behavior"]),
         "behavior_unspecified"),
        (lambda p: p.update(
            temptation="Stage the paths the commit covers, never the whole tree please"),
         "temptation_leaks_rule"),
        (lambda p: p.update(axis="staleness", superseded_belief=""),
         "superseded_belief_missing"),
        (lambda p: p.update(axis="scope_contradiction"), "counterpart_unresolved"),
    ],
)
def test_check_probe_rejects_each_defect_class(ule, index, good_probe, mutate, expected_reason):
    probe = copy.deepcopy(good_probe)
    mutate(probe)
    golden, reason = ule.check_probe(probe, index)
    assert golden is None
    assert reason == expected_reason


def test_check_probe_rejects_a_correction_probe_whose_bundle_has_no_incident(
    ule, index, good_probe
):
    """A correction golden must be a BUNDLE (rule + incident + how-to-apply),
    never a bare rule — the measured property of this corpus."""
    index["feedback"][("proj", "feedback_no_why")] = ule.parse_bundle(FEEDBACK_NO_WHY)
    probe = copy.deepcopy(good_probe)
    probe["source"]["stem"] = "feedback_no_why"
    golden, reason = ule.check_probe(probe, index)
    assert golden is None
    assert reason == "bundle_incomplete"


def test_check_probe_rejects_a_long_quote_even_when_diluted_below_the_overlap_bar(
    ule, index, good_probe
):
    """A quoted rule padded with unrelated words slips under the Jaccard bar, so
    the verbatim-run check has to catch it independently."""
    padding = " ".join(f"filler{n}word" for n in range(24))
    probe = copy.deepcopy(good_probe)
    probe["temptation"] = f"Stage the paths the commit covers, never the whole tree. {padding}"
    assert ule.lexical_overlap(
        probe["temptation"], index["feedback"][("proj", "feedback_stage_paths")]["rule"]
    ) <= ule.MAX_TEMPTATION_OVERLAP
    golden, reason = ule.check_probe(probe, index)
    assert golden is None
    assert reason == "temptation_quotes_rule"


def test_scope_probe_needs_a_counterpart_in_a_different_scope(ule, index, good_probe):
    probe = copy.deepcopy(good_probe)
    probe["axis"] = "scope_contradiction"
    probe["counterpart_source"] = probe["source"]
    probe["counterpart_scope"] = {"kind": "repo", "key": "Other"}
    golden, reason = ule.check_probe(probe, index)
    assert golden is None
    assert reason == "counterpart_same_source"

    probe["counterpart_source"] = {"kind": "learnings", "key": "a-real-key"}
    probe["counterpart_scope"] = {"kind": "repo", "key": "Proj"}
    golden, reason = ule.check_probe(probe, index)
    assert golden is None
    assert reason == "counterpart_same_scope"

    probe["counterpart_scope"] = {"kind": "repo", "key": "Other"}
    golden, reason = ule.check_probe(probe, index)
    assert reason is None
    assert golden["expected"]["scored_win"] == "scope_correct_rule_applied"
    assert golden["expected"]["must_not_apply_source_key"] == "learnings:a-real-key"


def test_build_shuffles_deterministically_and_rejects_duplicate_ids(ule, index, good_probe):
    probes = []
    for n in range(6):
        probe = copy.deepcopy(good_probe)
        probe["probe_id"] = f"t{n:03d}"
        probes.append(probe)
    first, rejects = ule.build(probes, index, seed=7)
    second, _ = ule.build(list(reversed(probes)), index, seed=7)
    assert rejects == {}
    assert [row["golden_id"] for row in first] == [row["golden_id"] for row in second]
    third, _ = ule.build(probes, index, seed=8)
    assert {row["golden_id"] for row in third} == {row["golden_id"] for row in first}

    probes.append(copy.deepcopy(probes[0]))
    with pytest.raises(AssertionError):
        ule.build(probes, index, seed=7)


def test_weight_deviation_math(ule):
    deviations = ule.weight_deviations(
        {"procedural": 34, "semantic": 10, "guardrail_exception": 5, "identity": 2}, 51
    )
    assert all(abs(value) <= ule.WEIGHT_TOLERANCE for value in deviations.values())


# --- the runnable determinism / lock-agreement gate ------------------------


def _sources_available(ule) -> bool:
    return (
        ule.PROBES_PATH.exists()
        and ule.SYNDAI_LEARNINGS.exists()
        and ule.SYNDAI_AGENTS.exists()
        and any(ule.CLAUDE_PROJECTS.glob("*/memory/feedback_*.md"))
    )


def test_extraction_still_reproduces_the_committed_lock(ule):
    """Fails if extraction breaks, a source drifts, or the run is not deterministic.

    Runs the extractor's own ``--check`` mode twice: it re-extracts from the live
    sources and compares sha256, count, strata, source counts and params against
    the committed lock without rewriting anything.
    """
    if not _sources_available(ule):
        pytest.skip("private Track U sources not present on this machine")
    assert LOCK.exists(), "committed lock is missing"
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    lock = json.loads(LOCK.read_text())
    assert lock["params"]["axes"] == list(ule.AXES)
    assert ule.TARGET_MIN <= lock["count"] <= ule.TARGET_MAX
    assert set(lock["strata"]["by_axis"]) == set(ule.AXES)
    assert lock["probes_file"]["committed"] is False
    assert all(
        abs(value) <= ule.WEIGHT_TOLERANCE
        for value in lock["category_weight_deviations"].values()
    )
