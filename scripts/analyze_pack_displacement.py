#!/usr/bin/env python3
"""Paired before/after analysis for the fused->packed displacement fix.

Reads two ``code_lane_run_memphant.py`` provenance files (same golden bank,
same haystack) and reports r@5/r@10, the exact-McNemar paired test, and the
recovery rate on the preregistered target: the questions whose gold sat at
fused rank <= 10 in the BASELINE arm yet never reached the packed context.

Deterministic, no model call. Every figure is computed from the two artifacts.
"""

from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path


def mcnemar_exact_p(only_a: int, only_b: int) -> float:
    """Two-sided exact binomial p on the discordant pairs."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    smaller = min(only_a, only_b)
    tail = sum(comb(n, i) for i in range(smaller + 1)) / (2**n)
    return min(1.0, 2 * tail)


def rows_by_question(path: Path) -> tuple[dict, dict[str, dict]]:
    report = json.loads(path.read_text())
    return report, {row["question_id"]: row for row in report["per_question"]}


def paired(before: dict[str, dict], after: dict[str, dict], key: str) -> dict:
    ids = sorted(set(before) & set(after))
    both = sum(1 for q in ids if before[q][key] and after[q][key])
    only_before = sum(1 for q in ids if before[q][key] and not after[q][key])
    only_after = sum(1 for q in ids if after[q][key] and not before[q][key])
    neither = len(ids) - both - only_before - only_after
    return {
        "n": len(ids),
        "both": both,
        "before_only": only_before,
        "after_only": only_after,
        "neither": neither,
        "mcnemar_exact_p": mcnemar_exact_p(only_before, only_after),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()

    before_report, before = rows_by_question(Path(args.before))
    after_report, after = rows_by_question(Path(args.after))

    if before_report["golden_sha256"] != after_report["golden_sha256"]:
        raise SystemExit("arms scored different golden banks")
    if before_report["corpus_sha256"] != after_report["corpus_sha256"]:
        raise SystemExit("arms scored different corpora")
    if set(before) != set(after):
        raise SystemExit("arms scored different question sets")

    # Preregistered target: gold in the fused top-10 but displaced by packing.
    target = sorted(
        q
        for q, row in before.items()
        if row["bucket"] == "in_pool_unpacked"
        and row["gold_fused_rank"] is not None
        and row["gold_fused_rank"] <= 10
    )
    recovered = sorted(q for q in target if after[q]["hit_at_10"])

    summary = {
        "golden_sha256": before_report["golden_sha256"],
        "corpus_sha256": before_report["corpus_sha256"],
        "before": {
            "label": before_report["label"],
            "recall_at_5": before_report["recall_at_5"],
            "recall_at_10": before_report["recall_at_10"],
            "pack_drop_summary": before_report["pack_drop_summary"],
        },
        "after": {
            "label": after_report["label"],
            "recall_at_5": after_report["recall_at_5"],
            "recall_at_10": after_report["recall_at_10"],
            "pack_drop_summary": after_report["pack_drop_summary"],
        },
        "paired_at_5": paired(before, after, "hit_at_5"),
        "paired_at_10": paired(before, after, "hit_at_10"),
        "displacement_target": {
            "n": len(target),
            "recovered": len(recovered),
            "recovered_question_ids": recovered,
            "still_missing_question_ids": [q for q in target if q not in set(recovered)],
        },
        "fused_top10_ceiling_before": sum(
            1
            for row in before.values()
            if row["gold_fused_rank"] is not None and row["gold_fused_rank"] <= 10
        ),
        "fused_top10_ceiling_after": sum(
            1
            for row in after.values()
            if row["gold_fused_rank"] is not None and row["gold_fused_rank"] <= 10
        ),
    }
    text = json.dumps(summary, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
