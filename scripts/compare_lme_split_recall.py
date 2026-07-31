#!/usr/bin/env python3
"""Paired retrieval-only comparison of two `memphant-eval bench-lme` reports.

Written for the cleaned-vs-deprecated LongMemEval-S split comparison
(`docs/build-log/2026-07-31-lme-cleaned-split.md`), but it is split-agnostic: it
joins two reports on `question_id` and reports the paired deltas.

Both splits carry the same 500 question IDs, so the seeded stratified sample
selects the same questions in each arm and the join is total. The script does
not assume that — it reports the join explicitly (paired / arm-only /
baseline-only) so a future split that drops or renames questions shows up as a
shrunken pair set rather than a silently different denominator.

Scoring mirrors the harness: abstention questions are excluded from recall
(`hit_at_*` is None for them) and reported separately.

Significance is the EXACT two-sided McNemar over discordant pairs (a binomial
sign test), not the chi-square approximation — the repo's convention, and the
right test when the discordant count is small.

Usage:
  python3 scripts/compare_lme_split_recall.py \
    --baseline <deprecated-report.json> --arm <cleaned-report.json> \
    [--baseline-label deprecated] [--arm-label cleaned] [--out <comparison.json>]
"""

from __future__ import annotations

import argparse
import json
import random
from math import comb
from pathlib import Path

# Seeded so a rerun reproduces the interval exactly.
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20260731

# Harness settings that must agree before a delta is attributable to the split.
# Anything that changes what retrieval does belongs here; a default is not
# evidence merely because it was the default, so every one of these is asserted,
# not assumed.
PINNED_SETTINGS = [
    "sample_seed",
    "sample_n",
    "k",
    "granularity",
    "turns_window",
    "budget_tokens",
    "recall_pool_depth",
    "embeddings",
    "embed_model",
    "mode",
    "lexical_scorer",
    "cross_rerank",
    "sibling_gather",
    "session_quota",
    "pack_render_cap",
    "temporal_grounding",
    "fact_extraction",
    "runtime_chunks",
    "retrieval_only",
]


def exact_mcnemar_p(wins: int, losses: int) -> float:
    """Two-sided exact McNemar: binomial sign test on the discordant pairs."""
    n = wins + losses
    if n == 0:
        return 1.0
    observed = min(wins, losses)
    tail = sum(comb(n, i) for i in range(observed + 1))
    return min(1.0, 2.0 * tail / (2.0**n))


def rate(hits: int, scored: int) -> float | None:
    return hits / scored if scored else None


def bootstrap_delta_ci(deltas: list[int]) -> dict:
    """Seeded percentile bootstrap 95% CI over per-question paired deltas.

    Reported because a null McNemar on one discordant pair is not evidence of
    equivalence on its own — the interval is what says how large a real
    difference this sample could still have hidden.
    """
    if not deltas:
        return {"mean": None, "ci95_low": None, "ci95_high": None}
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(deltas)
    means = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        means.append(sum(rng.choice(deltas) for _ in range(n)) / n)
    means.sort()
    low = means[int(0.025 * BOOTSTRAP_RESAMPLES)]
    high = means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]
    return {
        "mean": sum(deltas) / n,
        "ci95_low": low,
        "ci95_high": high,
        "ci_excludes_zero": low > 0 or high < 0,
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
    }


def compare_at(k: str, base: dict, arm: dict, ids: list[str]) -> dict:
    """Paired comparison over questions scored (non-abstention) in BOTH arms."""
    scored = [q for q in ids if base[q][k] is not None and arm[q][k] is not None]
    both = sum(1 for q in scored if base[q][k] and arm[q][k])
    arm_only = sorted(q for q in scored if arm[q][k] and not base[q][k])
    base_only = sorted(q for q in scored if base[q][k] and not arm[q][k])
    neither = len(scored) - both - len(arm_only) - len(base_only)
    base_hits = both + len(base_only)
    arm_hits = both + len(arm_only)
    base_rate = rate(base_hits, len(scored))
    arm_rate = rate(arm_hits, len(scored))
    return {
        "n_scored_paired": len(scored),
        "baseline_hits": base_hits,
        "arm_hits": arm_hits,
        "baseline_recall": base_rate,
        "arm_recall": arm_rate,
        "delta": None if base_rate is None else arm_rate - base_rate,
        "concordant_both_hit": both,
        "concordant_both_miss": neither,
        "arm_only_wins": len(arm_only),
        "baseline_only_wins": len(base_only),
        "exact_mcnemar_two_sided_p": exact_mcnemar_p(len(arm_only), len(base_only)),
        "bootstrap_delta_ci95": bootstrap_delta_ci(
            [int(bool(arm[q][k])) - int(bool(base[q][k])) for q in scored]
        ),
        "arm_only_question_ids": arm_only,
        "baseline_only_question_ids": base_only,
    }


def abstention_block(report: dict) -> dict:
    rows = [row for row in report["per_question"] if row["is_abstention"]]
    return {
        "n": len(rows),
        "correct": sum(1 for row in rows if row["abstention_correct"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--arm", required=True, type=Path)
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--arm-label", default="arm")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    base_report = json.loads(args.baseline.read_text(encoding="utf-8"))
    arm_report = json.loads(args.arm.read_text(encoding="utf-8"))

    settings = {}
    mismatched = []
    for key in PINNED_SETTINGS:
        left, right = base_report.get(key), arm_report.get(key)
        settings[key] = left if left == right else {"baseline": left, "arm": right}
        if left != right:
            mismatched.append(key)

    if base_report["dataset_sha256"] == arm_report["dataset_sha256"]:
        raise SystemExit("both reports name the same dataset sha256; nothing to compare")

    base = {row["question_id"]: row for row in base_report["per_question"]}
    arm = {row["question_id"]: row for row in arm_report["per_question"]}
    paired = sorted(set(base) & set(arm))

    result = {
        "comparison": "longmemeval_s retrieval-only, paired by question_id",
        "baseline": {
            "label": args.baseline_label,
            "report": str(args.baseline),
            "dataset_path": base_report["dataset_path"],
            "dataset_sha256": base_report["dataset_sha256"],
            "dataset_questions": base_report["dataset_questions"],
            "abstention": abstention_block(base_report),
        },
        "arm": {
            "label": args.arm_label,
            "report": str(args.arm),
            "dataset_path": arm_report["dataset_path"],
            "dataset_sha256": arm_report["dataset_sha256"],
            "dataset_questions": arm_report["dataset_questions"],
            "abstention": abstention_block(arm_report),
        },
        "harness_settings": settings,
        "harness_settings_mismatched": mismatched,
        "join": {
            "paired": len(paired),
            "baseline_only": sorted(set(base) - set(arm)),
            "arm_only": sorted(set(arm) - set(base)),
            "total_join": len(paired) == len(base) == len(arm),
        },
        "recall_at_5": compare_at("hit_at_5", base, arm, paired),
        "recall_at_10": compare_at("hit_at_10", base, arm, paired),
        "degraded_questions": {
            "baseline": sum(1 for row in base.values() if row["degraded"]),
            "arm": sum(1 for row in arm.values() if row["degraded"]),
        },
        "ingested_sessions": {
            "baseline": sum(row["ingested_sessions"] for row in base.values()),
            "arm": sum(row["ingested_sessions"] for row in arm.values()),
        },
    }

    if mismatched:
        print(f"WARNING harness settings differ between arms: {mismatched}")
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"written: {args.out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
