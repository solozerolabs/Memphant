#!/usr/bin/env python3
"""Question->target lexical-leakage metric for Track R banks (FREE, no model call).

This is the headline acceptance metric of the W0.1 paraphrase variant
(``docs/build-log/2026-07-31-track-r-paraphrase-bar.md``). It is defined here,
once, so the original bank's reference figures and the paraphrase variant's
achieved distribution are computed by the *same* code on the *same* corpus.

**Coverage** of a question against an event is

    |tokens(question) & tokens(event)| / |tokens(question)|

with ``tokens(s) = set(re.findall(r"[a-z0-9_]{3,}", s.lower()))``. It answers
"what fraction of the question's vocabulary is literally present in this event"
— i.e. how much of the question is a copy of the thing it asks about. This
tokenizer is the one that reproduces the program spec's §1 reference figures on
the original 180-golden bank (mean 0.3960 / median 0.3880 against the reported
0.396 / 0.388, and 105/180 questions narrowing to exactly one event); it is
pinned here so the bar is stated against a reproduced number rather than a
quoted one.

Three figures per golden:

* ``target`` — coverage against the golden's provenance event.
* ``non_target_exhaustive`` — the mean coverage against **every** non-target
  event of the same attempt. This is the primary floor: it needs no seed, so it
  cannot be moved by a lucky draw.
* ``non_target_sampled`` — coverage against ONE seeded random non-target event
  of the same attempt, the form §1 reported.

The same attempt is the right comparison set because the retrieval haystack is
attempt-scoped (``code_lane_run_deterministic.scoped_documents``).

    python3 scripts/track_r_leakage.py \
      --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
      --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
      --out docs/build-log/artifacts/track-r/leakage-original.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402

NON_TARGET_SEED = 7
TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


def tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def coverage(question: str, event_text: str) -> float:
    asked = tokens(question)
    if not asked:
        return 0.0
    return len(asked & tokens(event_text)) / len(asked)


def describe(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": round(statistics.fmean(ordered), 4),
        "median": round(statistics.median(ordered), 4),
        "p10": round(ordered[int(0.10 * (len(ordered) - 1))], 4),
        "p90": round(ordered[int(0.90 * (len(ordered) - 1))], 4),
        "min": round(ordered[0], 4),
        "max": round(ordered[-1], 4),
    }


def measure(goldens: list[dict], corpus_rows: list[dict], seed: int) -> dict:
    by_attempt = {row["attempt_id"]: row["events"] for row in corpus_rows}
    rng = random.Random(seed)
    per_question = []
    for golden in goldens:
        provenance = golden["provenance"][0]
        attempt_id = provenance["attempt_id"]
        events = by_attempt[attempt_id]
        target = next(e for e in events if e["sequence"] == provenance["event_sequence"])
        others = [e for e in events if e["sequence"] != provenance["event_sequence"]]
        question = golden["question"]
        sampled = rng.choice(others) if others else None
        exhaustive = (
            statistics.fmean(coverage(question, e["text"]) for e in others) if others else 0.0
        )
        per_question.append(
            {
                "question_id": golden["question_id"],
                "question_type": golden["question_type"],
                "attempt_id": attempt_id,
                "attempt_event_count": len(events),
                "target_coverage": round(coverage(question, target["text"]), 4),
                "non_target_exhaustive": round(exhaustive, 4),
                "non_target_sampled": (
                    round(coverage(question, sampled["text"]), 4) if sampled else None
                ),
                "non_target_sampled_sequence": sampled["sequence"] if sampled else None,
                "narrowed_event_count": (golden.get("identification") or {}).get(
                    "narrowed_event_count"
                ),
            }
        )
    target_stats = describe([row["target_coverage"] for row in per_question])
    exhaustive_stats = describe([row["non_target_exhaustive"] for row in per_question])
    sampled_stats = describe(
        [
            row["non_target_sampled"]
            for row in per_question
            if row["non_target_sampled"] is not None
        ]
    )
    narrowed = [
        row["narrowed_event_count"]
        for row in per_question
        if isinstance(row["narrowed_event_count"], int)
    ]
    shapes = sorted({row["question_type"] for row in per_question})
    return {
        "schema": "memphant.eval.track-r-leakage.v2",
        "metric": (
            "coverage(question, event) = |T(question) & T(event)| / |T(question)|, "
            "T(s) = set(re.findall(r'[a-z0-9_]{3,}', s.lower()))"
        ),
        "non_target_exhaustive_definition": (
            "per golden, the mean coverage over EVERY non-target event of the same "
            "attempt; no seed, no draw"
        ),
        "non_target_sampled_definition": (
            f"one uniformly random non-target event of the same attempt, seed {seed}"
        ),
        "n": len(per_question),
        "target": target_stats,
        "non_target_exhaustive": exhaustive_stats,
        "non_target_sampled": sampled_stats,
        "concentration_vs_exhaustive": round(target_stats["mean"] / exhaustive_stats["mean"], 4)
        if exhaustive_stats["mean"]
        else None,
        "concentration_vs_sampled": round(target_stats["mean"] / sampled_stats["mean"], 4)
        if sampled_stats["mean"]
        else None,
        "by_shape": {
            shape: {
                "n": len(rows),
                "target_mean": round(
                    statistics.fmean([r["target_coverage"] for r in rows]), 4
                ),
                "non_target_exhaustive_mean": round(
                    statistics.fmean([r["non_target_exhaustive"] for r in rows]), 4
                ),
            }
            for shape, rows in (
                (s, [r for r in per_question if r["question_type"] == s]) for s in shapes
            )
        },
        "narrowing": {
            "recorded": len(narrowed),
            "narrowed_to_exactly_one": sum(1 for value in narrowed if value == 1),
            "mean": round(statistics.fmean(narrowed), 4) if narrowed else None,
        },
        "per_question": per_question,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=NON_TARGET_SEED)
    args = parser.parse_args()
    goldens = gc.load_goldens(args.golden)
    corpus_rows = gc.load_goldens(args.corpus)
    report = measure(goldens, corpus_rows, args.seed)
    report["golden_path"] = args.golden.as_posix()
    report["golden_sha256"] = gc.sha256_hex(args.golden.read_bytes())
    report["corpus_sha256"] = gc.sha256_hex(args.corpus.read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    printable = {key: value for key, value in report.items() if key != "per_question"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
