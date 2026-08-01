#!/usr/bin/env python3
"""Shared contract for S8 — retrieve-then-rank (MemPhant narrows, the agent ranks).

Three things live here and nowhere else, because each is a rule a per-arm copy
would let drift:

1. ``ENDPOINT_CONTRACT`` — byte-identical to S4's. Every arm in this lane and
   every arm S4 measured stamps the same string, and the analysis refuses to
   pair two reports that do not both declare it. A headline in this program was
   voided for scoring one arm after packing against another's plain ranked
   top-10; this is the mechanical guard against repeating it.

2. ``lineage()`` — git HEAD, branch, dirty flag, input hashes. An artifact
   without lineage did not happen.

3. ``load_pool_dump()`` — the hybrid haystack. Every arm here ranks within
   ``code_lane_run_memphant --dump-pool``'s fused candidate pool for the same
   golden, truncated to that arm's ``N``. The pool is itself already scoped to
   the golden's attempt by ``bind_attempt_context``, so it is a SUBSET of the
   raw-event haystack S4's grep control searched.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gate_common as gc  # noqa: E402

ENDPOINT_CONTRACT = "gate_common.provenance_hit@10 over top-10 bodies"
PREREGISTRATION = "docs/build-log/2026-08-01-hybrid-retrieve-then-rank.md#part-a"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def lineage(inputs: dict[str, Path]) -> dict:
    status = _git("status", "--porcelain")
    return {
        "git_head": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(status),
        "git_dirty_paths": sorted(
            line[2:].strip() for line in status.splitlines() if line.strip()
        )[:50],
        "input_sha256": {
            name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in inputs.items()
        },
        "preregistration": PREREGISTRATION,
    }


def load_pool_dump(path: Path) -> dict[str, dict]:
    """``question_id -> {query, attempt_id, packed_bodies, pool}``.

    ``pool`` is the fused candidate list in served rank order, each row carrying
    its body. ``is_gold`` rides along for the post-hoc coverage decomposition
    and is stripped before anything is shown to a ranker.
    """
    rows: dict[str, dict] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pool = sorted(row["pool"], key=lambda item: item["fused_rank"] or 10**9)
        rows[row["question_id"]] = row | {"pool": pool}
    return rows


def coverage_at(pool_row: dict, n: int) -> bool:
    """Is a gold-bearing unit inside the first ``n`` of this pool?

    This is the arm's ceiling at ``N=n`` for this question, and it is computed
    from the retriever's ranking alone — no agent, no model, no spend.
    """
    return any(item["is_gold"] for item in pool_row["pool"][:n])


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
        "recall_at_5": sum(row["hit_at_5"] for row in per_question) / n if n else 0.0,
        "recall_at_10": sum(row["hit_at_10"] for row in per_question) / n if n else 0.0,
        "hits_at_10": sum(row["hit_at_10"] for row in per_question),
        "per_question": per_question,
        "_evidence_rows": evidence_rows,
    }


def write_report(report: dict, out_provenance: Path, out_evidence: Path) -> None:
    evidence_rows = report.pop("_evidence_rows")
    gc.write_jsonl(out_evidence, evidence_rows)
    Path(out_provenance).parent.mkdir(parents=True, exist_ok=True)
    Path(out_provenance).write_text(json.dumps(report, indent=2) + "\n")
