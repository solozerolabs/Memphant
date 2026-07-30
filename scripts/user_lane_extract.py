#!/usr/bin/env python3
"""User-learning golden bank extractor (Track U, Phase 1a-U slice 1).

Built on the C1 read-only-extract pattern (``episodic_lane_corpus.py``): the
sources are opened read-only, never written, and the bank bodies stay
gitignored with a single committed lock file (the ``code_lane_mine.py`` lock
pattern). Privacy rules are preregistered in
``docs/build-log/2026-07-30-track-u-privacy-prereg.md`` — read that first.

What this produces: goldens for what a coding agent must learn about the USER,
on the three slice-1 axes of the accuracy-first program's "Eval axes for Track
U" table.

| axis                | probe shape                               | scored win                   |
|---------------------|-------------------------------------------|------------------------------|
| correction_retention| correction in session N, temptation in N+k| the mistake is not repeated  |
| staleness           | preference changed / rule retired         | the old memory is NOT applied|
| scope_contradiction | same user, opposite rules in two repos    | scope-correct rule applied   |

The four deferred axes (guardrail-exception pairs, sycophancy, lifecycle,
adherence) are NOT built here — the plan bars them until each has its own
preregistered end-behavior scorer.

Two layers, deliberately split so the private layer can be gitignored:

- **mechanism (this file, committed).** Parses the sources into memory BUNDLES
  (rule + incident/why + how-to-apply — the measured shape of the owner's write
  path; a bare rule triple is not a golden), resolves each authored probe to its
  source bundle, runs the accept checks, and emits bank + lock.
- **authored probe layer (``benchmarks/data/user_lane_probes.jsonl``,
  gitignored).** One record per candidate: the temptation prompt, the forbidden
  behavior, the OBSERVABLE CORRECT BEHAVIOR (end behavior, never retrieval@k —
  the plan's adherence rule), the superseded belief (staleness) or the
  counterpart scope (scope contradiction), and the agent adjudication verdict.
  It paraphrases private memory content, so it is treated exactly like a body.

Determinism: no network call, no provider call, no clock read. Same sources +
same probe file + same seed -> byte-identical bank. ``--check`` re-runs the
extraction and asserts the result still matches the committed lock, so a broken
parse or a source drift fails loudly instead of silently re-cutting the bank.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402

# --- sources (read-only; every path is opened, never written) ----------------

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
SYNDAI_REF = Path("/Users/sidsharma/Syndai-memphant-ref")
SYNDAI_LEARNINGS = SYNDAI_REF / "LEARNINGS.md"
SYNDAI_AGENTS = SYNDAI_REF / "AGENTS.md"
MEMPHANT_AGENTS = gc.MEMPHANT_ROOT / "AGENTS.md"

PROBES_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "user_lane_probes.jsonl"
GOLDEN_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "user_lane_golden.jsonl"

# Only these AGENTS.md sections are admitted (the prereg's "hard-rule sections").
AGENTS_SECTIONS = {
    "syndai": ("Session Execution", "Hard Rules"),
    "memphant": (
        "Repo Boundaries",
        "Sister Project and Secrets",
        "Database Rules",
        "Working Rules",
        "Benchmark Dataset Cache",
        "CI monitoring",
    ),
}

AXES = ("correction_retention", "staleness", "scope_contradiction")
CATEGORIES = ("procedural", "semantic", "guardrail_exception", "identity")
AUTHORITIES = ("user_correction", "agent_learned")

# Measured power-user distribution (n=1, adopted now per the plan) and the
# tolerance the strata gate allows on a ~50-golden bank.
TARGET_WEIGHTS = {
    "procedural": 0.65,
    "semantic": 0.20,
    "guardrail_exception": 0.10,
    "identity": 0.05,
}
WEIGHT_TOLERANCE = 0.08

TARGET_MIN = 40
TARGET_MAX = 60
SAMPLE_SEED = 20260730

# A temptation prompt must not hand the rule to the model. Jaccard over content
# words, plus a verbatim-run check: a genuine temptation describes the SITUATION.
MAX_TEMPTATION_OVERLAP = 0.34
MAX_VERBATIM_RUN_WORDS = 7
MIN_RULE_CHARS = 40

WORD_RE = re.compile(r"[a-z0-9]+")
WHY_RE = re.compile(r"\*\*Why:?\*\*:?")
HOW_RE = re.compile(r"\*\*How (?:to apply|to actually check):?\*\*:?", re.IGNORECASE)
LEARNING_RE = re.compile(r"^- (?P<key>[a-z0-9][a-z0-9-]*) \| (?P<rest>.+)$")


# --- pure parsing (unit-tested in tests/test_user_lane_extract.py) -----------


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """``(frontmatter, body)`` for a ``---``-delimited memory file.

    Only top-level ``key: value`` lines are read (nested ``metadata:`` blocks
    carry no field this extractor uses). No frontmatter -> ``({}, text)``.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            front: dict[str, str] = {}
            for line in lines[1:index]:
                if line.startswith((" ", "\t")) or ":" not in line:
                    continue
                key, _, value = line.partition(":")
                front[key.strip()] = value.strip().strip('"')
            return front, "\n".join(lines[index + 1 :]).strip()
    return {}, text


def parse_bundle(text: str) -> dict[str, str]:
    """Parse a feedback memory into the bundle the owner's write path produces.

    ``rule`` (what to do), ``why`` (the incident that caused it), and
    ``how_to_apply`` (how it lands on a future task). Missing sections come back
    as empty strings — the caller decides whether that disqualifies a candidate
    (it does for correction goldens: a bare rule is not a bundle).
    """
    front, body = split_frontmatter(text)
    rule, why, how = body, "", ""
    match = HOW_RE.search(rule)
    if match:
        rule, how = rule[: match.start()], rule[match.end() :]
    match = WHY_RE.search(rule)
    if match:
        rule, why = rule[: match.start()], rule[match.end() :]
    return {
        "name": front.get("name", ""),
        "description": front.get("description", ""),
        "rule": rule.strip(),
        "why": why.strip(),
        "how_to_apply": how.strip(),
    }


def parse_learnings(text: str) -> dict[str, dict[str, str]]:
    """Parse ``LEARNINGS.md``'s one-line entries into ``key -> entry``.

    Canonical line shape is ``- key | confidence | source | insight | refs``;
    ``refs:`` is optional and the insight itself may contain pipes, so only the
    first three fields are split off and a trailing ``refs:`` field is peeled
    from the end.
    """
    section = ""
    out: dict[str, dict[str, str]] = {}
    for line in text.split("\n"):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        match = LEARNING_RE.match(line)
        if not match:
            continue
        parts = match.group("rest").split(" | ")
        if len(parts) < 3:
            continue
        confidence, source = parts[0].strip(), parts[1].strip()
        tail = parts[2:]
        refs = ""
        if tail[-1].strip().startswith("refs:"):
            refs = tail[-1].strip()[len("refs:") :].strip()
            tail = tail[:-1]
        out[match.group("key")] = {
            "section": section,
            "confidence": confidence,
            "source": source,
            "insight": " | ".join(tail).strip(),
            "refs": refs,
        }
    return out


def parse_agents_sections(text: str, wanted: tuple[str, ...]) -> dict[str, list[str]]:
    """``section -> [rule text, ...]`` for the named ``## `` sections.

    A bulleted section yields one entry per ``- `` bullet; a prose section (the
    MemPhant ``## CI monitoring`` shape) yields its whole body as one entry, so
    every admitted section is addressable by the same locator mechanism.
    """
    out: dict[str, list[str]] = {}
    section, buffer = None, []

    def flush() -> None:
        if section is None or section not in wanted:
            return
        bullets = [
            line[2:].strip() for line in buffer if line.startswith("- ") and line[2:].strip()
        ]
        prose = "\n".join(line for line in buffer if not line.startswith("- ")).strip()
        out[section] = bullets if bullets else ([prose] if prose else [])

    for line in text.split("\n"):
        if line.startswith("## "):
            flush()
            section, buffer = line[3:].strip(), []
            continue
        if section is not None:
            buffer.append(line)
    flush()
    return out


def content_words(text: str) -> set[str]:
    return {word for word in WORD_RE.findall(text.lower()) if len(word) > 2}


def lexical_overlap(left: str, right: str) -> float:
    a, b = content_words(left), content_words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def longest_verbatim_run(probe: str, source: str) -> int:
    """Longest run of consecutive probe words appearing verbatim in ``source``.

    Catches a temptation prompt that quotes the rule instead of describing the
    situation that tempts the mistake.
    """
    probe_words = WORD_RE.findall(probe.lower())
    source_words = WORD_RE.findall(source.lower())
    if not probe_words or not source_words:
        return 0
    source_text = " ".join(source_words)
    best = 0
    for start in range(len(probe_words)):
        for end in range(start + best + 1, len(probe_words) + 1):
            if " ".join(probe_words[start:end]) in source_text:
                best = end - start
            else:
                break
    return best


# --- source index -----------------------------------------------------------


def feedback_files(projects_root: Path) -> dict[tuple[str, str], Path]:
    """``(project, stem) -> path`` for every ``feedback_*.md`` memory file."""
    out: dict[tuple[str, str], Path] = {}
    for path in sorted(projects_root.glob("*/memory/feedback_*.md")):
        out[(path.parent.parent.name, path.stem)] = path
    return out


def build_source_index(
    projects_root: Path,
    learnings_path: Path,
    agents_paths: dict[str, Path],
) -> tuple[dict, dict]:
    """Read every admitted source once. Returns ``(index, counts)``."""
    feedback = {
        key: parse_bundle(path.read_text()) for key, path in feedback_files(projects_root).items()
    }
    learnings = parse_learnings(learnings_path.read_text())
    agents = {
        repo: parse_agents_sections(path.read_text(), AGENTS_SECTIONS[repo])
        for repo, path in agents_paths.items()
    }

    per_project: dict[str, int] = {}
    for project, _stem in feedback:
        per_project[project] = per_project.get(project, 0) + 1
    counts = {
        "feedback_files_total": len(feedback),
        "feedback_files_by_project": dict(sorted(per_project.items())),
        "learnings_entries": len(learnings),
        "agents_rules_by_section": {
            f"{repo}:{section}": len(rules)
            for repo, sections in sorted(agents.items())
            for section, rules in sorted(sections.items())
        },
    }
    return {"feedback": feedback, "learnings": learnings, "agents": agents}, counts


def resolve_source(index: dict, spec: dict) -> tuple[dict, str] | None:
    """Resolve a probe's source spec to ``(bundle, source_key)``.

    ``None`` when the source cannot be resolved uniquely — a missing file, a
    renamed learnings key, or an AGENTS.md locator that now matches zero or
    several rules. Nothing is ever fabricated to fill a gap.
    """
    kind = spec.get("kind")
    if kind == "feedback":
        key = (spec["project"], spec["stem"])
        bundle = index["feedback"].get(key)
        if bundle is None:
            return None
        return dict(bundle), f"feedback:{spec['project']}/{spec['stem']}"
    if kind == "learnings":
        entry = index["learnings"].get(spec["key"])
        if entry is None:
            return None
        bundle = {
            "name": spec["key"],
            "description": entry["section"],
            "rule": entry["insight"],
            "why": "",
            "how_to_apply": "",
            "confidence": entry["confidence"],
            "recorded_by": entry["source"],
        }
        return bundle, f"learnings:{spec['key']}"
    if kind == "agents":
        rules = index["agents"].get(spec["repo"], {}).get(spec["section"], [])
        hits = [rule for rule in rules if spec["locator"] in rule]
        if len(hits) != 1:
            return None
        return (
            {
                "name": f"{spec['repo']} AGENTS.md :: {spec['section']}",
                "description": spec["locator"],
                "rule": hits[0],
                "why": "",
                "how_to_apply": "",
            },
            f"agents:{spec['repo']}#{spec['section']}::{spec['locator'][:48]}",
        )
    return None


# --- accept checks ----------------------------------------------------------


def check_probe(probe: dict, index: dict) -> tuple[dict | None, str | None]:
    """Run every accept check for one probe.

    Returns ``(golden, None)`` on accept or ``(None, reject_reason)``. The
    reasons are the lock's reject taxonomy; an agent-adjudication reject
    recorded in the probe file short-circuits first, so a human reading the lock
    sees why a candidate the adjudicator rejected never entered the bank.
    """
    verdict = probe.get("adjudication", {}).get("verdict")
    if verdict != "accept":
        return None, probe.get("adjudication", {}).get("reason", "adjudicator_reject")

    if probe["axis"] not in AXES:
        return None, "unknown_axis"
    if probe["category"] not in CATEGORIES:
        return None, "unknown_category"
    if probe["authority"] not in AUTHORITIES:
        return None, "unknown_authority"

    resolved = resolve_source(index, probe["source"])
    if resolved is None:
        return None, "source_unresolved"
    bundle, source_key = resolved
    if len(bundle["rule"]) < MIN_RULE_CHARS:
        return None, "rule_too_short"

    # Corrections are BUNDLES, never bare triples: rule + incident + how-to-apply.
    if probe["axis"] == "correction_retention" and not (bundle["why"] and bundle["how_to_apply"]):
        return None, "bundle_incomplete"

    temptation = probe["temptation"].strip()
    forbidden = probe["forbidden_behavior"].strip()
    correct = probe["observable_correct_behavior"].strip()
    if not (temptation and forbidden and correct) or forbidden == correct:
        return None, "behavior_unspecified"

    rule_text = " ".join([bundle["rule"], bundle["why"], bundle["how_to_apply"]])
    if lexical_overlap(temptation, bundle["rule"]) > MAX_TEMPTATION_OVERLAP:
        return None, "temptation_leaks_rule"
    if longest_verbatim_run(temptation, rule_text) > MAX_VERBATIM_RUN_WORDS:
        return None, "temptation_quotes_rule"

    counterpart_key = None
    if probe["axis"] == "staleness":
        if not probe.get("superseded_belief", "").strip():
            return None, "superseded_belief_missing"
        if "superseded_source" in probe:
            counter = resolve_source(index, probe["superseded_source"])
            if counter is None:
                return None, "counterpart_unresolved"
            counterpart_key = counter[1]
    if probe["axis"] == "scope_contradiction":
        counter = resolve_source(index, probe.get("counterpart_source", {}))
        if counter is None:
            return None, "counterpart_unresolved"
        counterpart_key = counter[1]
        if counterpart_key == source_key:
            return None, "counterpart_same_source"
        if probe.get("counterpart_scope", {}).get("key") == probe["scope"]["key"]:
            return None, "counterpart_same_scope"

    scored_win = {
        "correction_retention": "mistake_not_repeated",
        "staleness": "stale_memory_not_applied",
        "scope_contradiction": "scope_correct_rule_applied",
    }[probe["axis"]]

    golden = {
        "golden_id": probe["probe_id"],
        "axis": probe["axis"],
        "category": probe["category"],
        "authority": probe["authority"],
        "scope": probe["scope"],
        "memory_bundle": bundle,
        "source_key": source_key,
        "probe": {
            "temptation": temptation,
            "session_gap_turns": probe.get("session_gap_turns"),
        },
        "expected": {
            "scored_win": scored_win,
            "observable_correct_behavior": correct,
            "forbidden_behavior": forbidden,
            "must_not_apply": probe.get("superseded_belief", "").strip() or None,
            "must_not_apply_source_key": counterpart_key,
            "counterpart_scope": probe.get("counterpart_scope"),
        },
        "retrieval_target": source_key,
        "temptation_overlap": round(lexical_overlap(temptation, bundle["rule"]), 4),
        "guardrail_exception_pair": False,
        "adjudicator": probe["adjudication"].get("adjudicator", ""),
    }
    return golden, None


# --- build ------------------------------------------------------------------


def build(probes: list[dict], index: dict, seed: int) -> tuple[list[dict], dict[str, int]]:
    goldens: list[dict] = []
    rejects: dict[str, int] = {}
    for probe in sorted(probes, key=lambda row: row["probe_id"]):
        golden, reason = check_probe(probe, index)
        if golden is None:
            rejects[reason] = rejects.get(reason, 0) + 1
            continue
        goldens.append(golden)
    ids = [row["golden_id"] for row in goldens]
    assert len(set(ids)) == len(ids), "duplicate golden_id in probe file"
    random.Random(seed).shuffle(goldens)
    return goldens, dict(sorted(rejects.items()))


def strata(goldens: list[dict]) -> dict:
    def tally(key) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in goldens:
            label = key(row)
            out[label] = out.get(label, 0) + 1
        return dict(sorted(out.items()))

    return {
        "by_axis": tally(lambda row: row["axis"]),
        "by_category": tally(lambda row: row["category"]),
        "by_axis_category": tally(lambda row: f"{row['axis']}/{row['category']}"),
        "by_scope": tally(lambda row: f"{row['scope']['kind']}:{row['scope']['key']}"),
        "by_authority": tally(lambda row: row["authority"]),
        "by_source_kind": tally(lambda row: row["source_key"].split(":", 1)[0]),
    }


def weight_deviations(by_category: dict[str, int], total: int) -> dict[str, float]:
    return {
        category: round(by_category.get(category, 0) / total - target, 4)
        for category, target in sorted(TARGET_WEIGHTS.items())
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probes", default=str(PROBES_PATH))
    parser.add_argument("--out-golden", default=str(GOLDEN_PATH))
    parser.add_argument("--out-lock", default=None)
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--projects-root", default=str(CLAUDE_PROJECTS))
    parser.add_argument("--learnings", default=str(SYNDAI_LEARNINGS))
    parser.add_argument("--syndai-agents", default=str(SYNDAI_AGENTS))
    parser.add_argument("--memphant-agents", default=str(MEMPHANT_AGENTS))
    parser.add_argument(
        "--check",
        action="store_true",
        help="re-extract and assert the result still matches the committed lock",
    )
    args = parser.parse_args()

    out_golden = Path(args.out_golden)
    out_lock = (
        Path(args.out_lock)
        if args.out_lock
        else out_golden.with_name(out_golden.stem + ".lock.json")
    )

    probes_path = Path(args.probes)
    missing = [
        str(path)
        for path in (probes_path, Path(args.learnings), Path(args.syndai_agents))
        if not path.exists()
    ]
    if missing:
        print(f"source(s) not available: {', '.join(missing)}", file=sys.stderr)
        return 1

    index, source_counts = build_source_index(
        Path(args.projects_root),
        Path(args.learnings),
        {"syndai": Path(args.syndai_agents), "memphant": Path(args.memphant_agents)},
    )
    probes = gc.load_goldens(probes_path)
    goldens, rejects = build(probes, index, args.seed)

    if not goldens:
        print("no goldens accepted; aborting", file=sys.stderr)
        return 1

    bank_strata = strata(goldens)
    deviations = weight_deviations(bank_strata["by_category"], len(goldens))
    lock = {
        "golden_path": "benchmarks/data/user_lane_golden.jsonl",
        "sha256": "",
        "bytes": 0,
        "count": len(goldens),
        "target_range": [TARGET_MIN, TARGET_MAX],
        "strata": bank_strata,
        "category_weight_targets": TARGET_WEIGHTS,
        "category_weight_deviations": deviations,
        "params": {
            "seed": args.seed,
            "max_temptation_overlap": MAX_TEMPTATION_OVERLAP,
            "max_verbatim_run_words": MAX_VERBATIM_RUN_WORDS,
            "min_rule_chars": MIN_RULE_CHARS,
            "weight_tolerance": WEIGHT_TOLERANCE,
            "axes": list(AXES),
            "deferred_axes": [
                "guardrail_exceptions",
                "sycophancy",
                "lifecycle",
                "adherence",
            ],
        },
        "stats": {
            "candidates": len(probes),
            "accepted": len(goldens),
            "rejected": len(probes) - len(goldens),
            "rejects_by_reason": rejects,
        },
        "source_counts": source_counts,
        "probes_file": {
            "path": "benchmarks/data/user_lane_probes.jsonl",
            "sha256": gc.sha256_hex(probes_path.read_bytes()),
            "count": len(probes),
            "committed": False,
        },
        "privacy_prereg": "docs/build-log/2026-07-30-track-u-privacy-prereg.md",
    }

    # Serialize in memory first: --check must never rewrite the bank it verifies.
    # Same encoding as gc.write_jsonl, so the hash matches the file on disk.
    golden_bytes = (
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in goldens)
    ).encode()
    lock["sha256"] = gc.sha256_hex(golden_bytes)
    lock["bytes"] = len(golden_bytes)
    lock_text = json.dumps(lock, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not out_lock.exists():
            print(f"--check: no committed lock at {out_lock}", file=sys.stderr)
            return 1
        committed = json.loads(out_lock.read_text())
        drift = {
            field: (committed.get(field), lock[field])
            for field in ("sha256", "count", "strata", "source_counts", "params")
            if committed.get(field) != lock[field]
        }
        if drift:
            print(f"--check FAILED: {json.dumps(drift, indent=2)}", file=sys.stderr)
            return 1
        print(
            f"--check OK: {lock['count']} goldens, sha256={lock['sha256'][:12]}",
            file=sys.stderr,
        )
        return 0

    gc.write_jsonl(out_golden, goldens)
    assert out_golden.read_bytes() == golden_bytes, "serialization drift vs gc.write_jsonl"

    if not TARGET_MIN <= len(goldens) <= TARGET_MAX:
        print(
            f"FAIL: {len(goldens)} goldens outside target range {TARGET_MIN}-{TARGET_MAX}",
            file=sys.stderr,
        )
        return 1
    off = {k: v for k, v in deviations.items() if abs(v) > WEIGHT_TOLERANCE}
    if off:
        print(f"FAIL: category weights off target by >{WEIGHT_TOLERANCE}: {off}", file=sys.stderr)
        return 1
    if set(bank_strata["by_axis"]) != set(AXES):
        print(f"FAIL: not every slice-1 axis is populated: {bank_strata['by_axis']}", file=sys.stderr)
        return 1

    out_lock.write_text(lock_text)
    print(
        f"accepted={len(goldens)} rejected={lock['stats']['rejected']} "
        f"axes={bank_strata['by_axis']} categories={bank_strata['by_category']} "
        f"rejects={rejects} sha256={lock['sha256'][:12]} out={out_golden}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
