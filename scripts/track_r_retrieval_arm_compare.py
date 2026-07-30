#!/usr/bin/env python3
"""Compare Track R retrieval arms against the scoped-BM25 control, paired.

Every arm is a `code_lane_run_memphant.py` provenance report over the SAME
golden bank and the SAME attempt-scoped haystack; the control is a
`code_lane_run_deterministic.py --scope attempt` report. The comparison is
made at the SAME STAGE for both: MemPhant's ranked top-k is read off
``gold_fused_rank`` (Phase 1c established that ``gold_fused_rank <= k`` is
exactly ``gate_common.provenance_hit`` at k for this single-span bank, and the
precondition is re-asserted here rather than assumed).

Reported per arm and per k: recall, the 2x2 paired table vs the control, and
the EXACT McNemar p (two-sided binomial on the discordant pairs — not the
chi-square approximation, which is unreliable at these counts). No verdict, no
promotion, no reader, no paid call.
"""

from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import gate_common as gc  # noqa: E402


def mcnemar_exact_p(left_only: int, right_only: int) -> float:
    """Two-sided exact McNemar: binomial(n=discordant, p=0.5) tail test.

    With no discordant pairs the arms are identical and p is 1.0.
    """
    n = left_only + right_only
    if n == 0:
        return 1.0
    smaller = min(left_only, right_only)
    tail = sum(comb(n, i) for i in range(smaller + 1)) / 2.0**n
    return min(1.0, 2.0 * tail)


def paired_block(arm_hits: list[bool], control_hits: list[bool]) -> dict:
    both = sum(1 for a, c in zip(arm_hits, control_hits) if a and c)
    arm_only = sum(1 for a, c in zip(arm_hits, control_hits) if a and not c)
    control_only = sum(1 for a, c in zip(arm_hits, control_hits) if c and not a)
    return {
        "both": both,
        "arm_only": arm_only,
        "control_only": control_only,
        "neither": len(arm_hits) - both - arm_only - control_only,
        "mcnemar_exact_p": mcnemar_exact_p(arm_only, control_only),
    }


def fused_hits(report: dict, order: list[str], k: int) -> list[bool]:
    rows = {row["question_id"]: row for row in report["per_question"]}
    hits = []
    for question_id in order:
        rank = rows[question_id]["gold_fused_rank"]
        hits.append(rank is not None and rank <= k)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument(
        "--control", required=True, type=Path,
        help="code_lane_run_deterministic.py --scope attempt provenance report",
    )
    parser.add_argument(
        "--arm", action="append", required=True, metavar="NAME=PATH",
        help="a code_lane_run_memphant.py provenance report, repeatable",
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    lock = json.loads(memphant_runner.golden_lock_path(args.golden).read_text())
    _, goldens = memphant_runner.verify_input_contract(args.corpus, args.golden, lock)
    if any(len(gc.required_spans(golden)) != 1 for golden in goldens):
        raise RuntimeError(
            "fused-rank -> provenance_hit equivalence needs single-span goldens"
        )
    order = [golden["question_id"] for golden in goldens]
    shapes = {golden["question_id"]: golden["question_type"] for golden in goldens}

    control = json.loads(args.control.read_text())
    if control.get("scope") != "attempt":
        raise RuntimeError("the control must be the attempt-scoped BM25 arm")
    control_rows = {row["question_id"]: row for row in control["per_question"]}
    control_hits = {
        k: [bool(control_rows[q][f"hit_at_{k}"]) for q in order] for k in (5, 10)
    }

    arms = {}
    for entry in args.arm:
        name, _, path = entry.partition("=")
        report = json.loads(Path(path).read_text())
        if report["golden_sha256"] != lock["sha256"]:
            raise RuntimeError(f"arm {name} ran on a different golden bank")
        if report["corpus_sha256"] != control["corpus_sha256"]:
            raise RuntimeError(f"arm {name} ran on a different corpus")
        if report["golden_count"] != len(order):
            raise RuntimeError(f"arm {name} has a different question count")
        arms[name] = {"path": path, "report": report}

    out_arms = {}
    for name, entry in arms.items():
        report = entry["report"]
        hits = {k: fused_hits(report, order, k) for k in (5, 10)}
        by_shape = {}
        for k in (5, 10):
            for question_id, hit in zip(order, hits[k]):
                bucket = by_shape.setdefault(shapes[question_id], {5: [], 10: []})
                bucket[k].append(hit)
        out_arms[name] = {
            "provenance_path": entry["path"],
            "lexical_scorer": report.get("lexical_scorer"),
            "embed_model": report.get("embed_model"),
            "recall_at_5": sum(hits[5]) / len(order),
            "recall_at_10": sum(hits[10]) / len(order),
            "hits_at_5": sum(hits[5]),
            "hits_at_10": sum(hits[10]),
            "gold_in_pool": sum(
                1 for row in report["per_question"] if row["gold_in_pool"]
            ),
            "gold_at_rank_1": sum(
                1 for row in report["per_question"] if row["gold_fused_rank"] == 1
            ),
            "paired_vs_scoped_bm25": {
                f"at_{k}": paired_block(hits[k], control_hits[k]) for k in (5, 10)
            },
            "by_shape": {
                shape: {
                    f"recall_at_{k}": sum(bucket[k]) / len(bucket[k]) for k in (5, 10)
                }
                for shape, bucket in sorted(by_shape.items())
            },
            "per_question_hit_at_10": dict(zip(order, hits[10])),
            "per_question_hit_at_5": dict(zip(order, hits[5])),
        }

    report = {
        "schema": "memphant.eval.track-r-retrieval-arm-compare.v1",
        "golden_path": str(args.golden),
        "golden_sha256": lock["sha256"],
        "corpus_sha256": control["corpus_sha256"],
        "paid_api_spend_usd": 0,
        "stage": "fused ranked top-k for every MemPhant arm; ranked top-k for the control",
        "control": {
            "provenance_path": str(args.control),
            "haystack": "events of the bound attempt only",
            "recall_at_5": control["recall_at_5"],
            "recall_at_10": control["recall_at_10"],
            "hits_at_5": sum(control_hits[5]),
            "hits_at_10": sum(control_hits[10]),
        },
        "arms": out_arms,
        "note": "measurement only; no gate verdict and no default moves here",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for name, arm in out_arms.items():
        line = [f"{name:22s}"]
        for k in (5, 10):
            block = arm["paired_vs_scoped_bm25"][f"at_{k}"]
            line.append(
                f"r@{k}={arm[f'recall_at_{k}']:.4f} "
                f"(+{block['arm_only']}/-{block['control_only']} p={block['mcnemar_exact_p']:.5f})"
            )
        print("  ".join(line))
    print(
        f"{'scoped_bm25_control':22s}  "
        f"r@5={control['recall_at_5']:.4f}  r@10={control['recall_at_10']:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
