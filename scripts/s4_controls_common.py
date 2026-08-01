#!/usr/bin/env python3
"""Shared contract for the S4 control arms (agentic `grep` and dense RAG).

Two things live here and nowhere else, because both are load-bearing rules that
a per-arm copy would let drift:

1. ``ENDPOINT_CONTRACT`` — the single string every arm stamps into its report.
   The comparison script refuses to pair two arms that do not both declare it.
   A headline in this program was voided for scoring one arm after packing
   against another's plain ranked top-10; this is the mechanical guard.

2. ``lineage()`` — git HEAD, branch, dirty flag and input hashes. An artifact
   without lineage did not happen.

The haystack rule is also here: MemPhant's recall is bound to the golden's
attempt by ``code_lane_run_memphant.bind_attempt_context``, so every control
ranks exactly that attempt's raw events and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import gate_common as gc  # noqa: E402

ENDPOINT_CONTRACT = "gate_common.provenance_hit@10 over top-10 bodies"
PREREGISTRATION = "docs/build-log/2026-08-01-agentic-search-controls.md#part-a"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def lineage(inputs: dict[str, Path]) -> dict:
    """Git identity plus a sha256 for every input file this arm read."""
    status = _git("status", "--porcelain")
    return {
        "git_head": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(status),
        # `git status --porcelain` is `XY<space>path`, but a staged-only entry
        # is `M <space>path` and a naive line[3:] eats the path's first
        # character. Split on the first run of whitespace after the 2-char code.
        "git_dirty_paths": sorted(
            line[2:].strip() for line in status.splitlines() if line.strip()
        )[:50],
        "input_sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in inputs.items()
        },
        "preregistration": PREREGISTRATION,
    }


def load_contract(corpus: Path, golden: Path) -> tuple[list[dict], list[dict], dict]:
    """Verify both private inputs, then return (corpus_rows, goldens, lock)."""
    lock = json.loads(memphant_runner.golden_lock_path(golden).read_text())
    corpus_rows, goldens = memphant_runner.verify_input_contract(corpus, golden, lock)
    return corpus_rows, goldens, lock


def attempt_events(corpus_rows: list[dict]) -> dict[str, list[dict]]:
    return {row["attempt_id"]: row["events"] for row in corpus_rows}


def golden_attempt_id(golden: dict) -> str:
    return golden["provenance"][0]["attempt_id"]


def haystack_for(corpus_rows_by_attempt: dict[str, list[dict]], golden: dict) -> list[dict]:
    """The events one control arm may rank for this golden.

    Identical scoping rule to ``code_lane_run_deterministic.scoped_documents``
    with ``--scope attempt``: the single coding attempt MemPhant's recall is
    bound to. The attempt's full event set is a superset of MemPhant's pool.
    """
    return corpus_rows_by_attempt[golden_attempt_id(golden)]


def score_arm(goldens: list[dict], selections: dict[str, list[str]], k: int = 10) -> dict:
    """Grade an arm at the one shared stage. ``selections`` maps question_id to
    that arm's ranked bodies."""
    per_question = []
    evidence_rows = []
    for golden in goldens:
        bodies = selections[golden["question_id"]][:k]
        evidence_rows.append(gc.evidence_row(golden, bodies, k))
        per_question.append(
            {
                "question_id": golden["question_id"],
                "question_type": golden["question_type"],
                "returned_items": len(bodies),
                "hit_at_5": gc.provenance_hit(golden, bodies, min(5, k)),
                "hit_at_10": gc.provenance_hit(golden, bodies, min(10, k)),
                "gold_rank": next(
                    (
                        rank
                        for rank in range(1, len(bodies) + 1)
                        if gc.provenance_hit(golden, bodies, rank)
                    ),
                    None,
                ),
            }
        )
    n = len(per_question)
    return {
        "endpoint_contract": ENDPOINT_CONTRACT,
        "k": k,
        "scope": "attempt",
        "golden_count": n,
        "recall_at_5": sum(row["hit_at_5"] for row in per_question) / n,
        "recall_at_10": sum(row["hit_at_10"] for row in per_question) / n,
        "hits_at_10": sum(row["hit_at_10"] for row in per_question),
        "per_question": per_question,
        "_evidence_rows": evidence_rows,
    }


def write_report(report: dict, out_provenance: Path, out_evidence: Path) -> None:
    evidence_rows = report.pop("_evidence_rows")
    gc.write_jsonl(out_evidence, evidence_rows)
    out_provenance.parent.mkdir(parents=True, exist_ok=True)
    out_provenance.write_text(json.dumps(report, indent=2) + "\n")
