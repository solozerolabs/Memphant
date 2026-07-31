#!/usr/bin/env python3
"""Chat-lane (LME-S) non-regression check for a packing change.

Packing is shared between the coding and chat lanes, so a code-lane packing win
has to be paired against the chat lane's own retrieval evidence before it
counts. Reads two ``bench-lme`` retrieval reports over the same dataset/seed and
reports r@5/r@10 with exact two-sided McNemar on the paired per-question
vectors. Deterministic, no reader, no model spend.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from analyze_pack_displacement import mcnemar_exact_p, percentiles

# Episode ids are UUIDv7s minted per ingest, so they differ between two runs of
# the same arm. Everything else in a packed body is content.
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def packed_bodies(path: Path) -> dict[str, list[str]]:
    # `splitlines()` would also split on U+2028/U+2029, which appear inside these
    # bodies; JSONL rows are newline-delimited only.
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return {
        row["question_id"]: [UUID.sub("<uuid>", item["body"]) for item in row["evidence"]]
        for row in rows
    }


def load(path: Path) -> tuple[dict, dict[str, dict]]:
    report = json.loads(path.read_text())
    return report, {row["question_id"]: row for row in report["per_question"]}


def paired(before: dict[str, dict], after: dict[str, dict], key: str) -> dict:
    ids = sorted(set(before) & set(after))
    scored = [q for q in ids if before[q][key] is not None and after[q][key] is not None]
    before_only = sum(1 for q in scored if before[q][key] and not after[q][key])
    after_only = sum(1 for q in scored if after[q][key] and not before[q][key])
    return {
        "n": len(scored),
        "before_hits": sum(1 for q in scored if before[q][key]),
        "after_hits": sum(1 for q in scored if after[q][key]),
        "before_only": before_only,
        "after_only": after_only,
        "mcnemar_exact_p": mcnemar_exact_p(before_only, after_only),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--before-evidence")
    parser.add_argument("--after-evidence")
    parser.add_argument("--out")
    args = parser.parse_args()

    before_report, before = load(Path(args.before))
    after_report, after = load(Path(args.after))
    for field in ("dataset_sha256", "sample_n", "sample_seed", "k", "budget_tokens"):
        if before_report.get(field) != after_report.get(field):
            raise SystemExit(f"arms differ on {field}")

    summary = {
        "dataset_sha256": before_report["dataset_sha256"],
        "sample_n": before_report["sample_n"],
        "sample_seed": before_report["sample_seed"],
        "before_overall": before_report["overall"],
        "after_overall": after_report["overall"],
        "paired_at_5": paired(before, after, "hit_at_5"),
        "paired_at_10": paired(before, after, "hit_at_10"),
        # Stronger than score equality: the packed CONTEXT itself, question by
        # question, modulo per-run episode ids.
        "packed_context_identical": (
            packed_bodies(Path(args.before_evidence))
            == packed_bodies(Path(args.after_evidence))
            if args.before_evidence and args.after_evidence
            else None
        ),
        # A render change that claims to be inert here must show it in the
        # packed-item count and per-item rendered sizes, not only in the score.
        "render_size_distribution": (
            {
                arm: {
                    "packed_items": percentiles([len(bodies) for bodies in packed.values()]),
                    "item_chars": percentiles(
                        [len(body) for bodies in packed.values() for body in bodies]
                    ),
                }
                for arm, packed in (
                    ("before", packed_bodies(Path(args.before_evidence))),
                    ("after", packed_bodies(Path(args.after_evidence))),
                )
            }
            if args.before_evidence and args.after_evidence
            else None
        ),
        "per_question_vector_identical": all(
            before[q]["hit_at_5"] == after[q]["hit_at_5"]
            and before[q]["hit_at_10"] == after[q]["hit_at_10"]
            for q in sorted(set(before) & set(after))
        ),
    }
    text = json.dumps(summary, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
