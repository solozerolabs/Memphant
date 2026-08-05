#!/usr/bin/env python3
"""C3 belief-revision: rates + paired bootstrap CIs, and the headroom verdict.

The decision-relevant quantity is NOT any single rule's rate. It is the PAIRED
headroom between the best trivial rule (frequency) and the statement-based
ceiling (union): no method that serves files the agent named can exceed the
ceiling, so headroom bounds what any memory system could ever win here.

SWE-ContextBench died with an oracle lift of 3.72pp against an MDE of
3.38-8.32pp. If the headroom CI sits inside that band, this direction dies the
same way — before any labeling spend.
"""

import collections
import glob
import json
import random
import sys

from c3_recency_check import (files_in, gold_files, hits, prefix_before_first_edit,
                              same_file)

RESAMPLES = 10000
SEED = 20260805


def per_trajectory(rows: list) -> list[dict]:
    out = []
    for row in rows:
        gold = gold_files(row)
        if not gold:
            continue
        traj = prefix_before_first_edit(row["trajectory"])
        seq = [f for m in traj if m.get("role") == "assistant" for f in files_in(m)]
        rec: dict = {"n_gold": len(gold), "prefix_msgs": len(traj), "named": len(seq)}
        if not seq:
            rec.update({r: 0 for r in ("recency", "frequency", "union")})
            out.append(rec)
            continue
        counts = collections.Counter(seq)
        top = max(counts.values())
        preds = {"recency": {seq[-1]},
                 "frequency": {f for f, c in counts.items() if c == top},
                 "union": set(seq)}
        for name, pred in preds.items():
            rec[name] = int(all(any(same_file(p, g) for p in pred) for g in gold))
        rec["any_hit_union"] = int(bool(hits(preds["union"], gold)))
        out.append(rec)
    return out


def boot_ci(vals: list[float], rng: random.Random) -> tuple[float, float]:
    n = len(vals)
    means = []
    for _ in range(RESAMPLES):
        means.append(sum(vals[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES)]


def main() -> int:
    rows = []
    for f in sorted(glob.glob(sys.argv[1] + "/c3_*.json")):
        rows += [r["row"] for r in json.load(open(f)).get("rows", [])]
    recs = per_trajectory(rows)
    n = len(recs)
    rng = random.Random(SEED)

    report: dict = {"n_trajectories_scored": n,
                    "median_prefix_msgs": sorted(r["prefix_msgs"] for r in recs)[n // 2],
                    "median_gold_files": sorted(r["n_gold"] for r in recs)[n // 2],
                    "rules": {}}
    for rule in ("recency", "frequency", "union"):
        vals = [r[rule] for r in recs]
        lo, hi = boot_ci(vals, rng)
        report["rules"][rule] = {"covers_gold": round(sum(vals) / n, 4),
                                 "ci95": [round(lo, 4), round(hi, 4)]}

    head = [r["union"] - r["frequency"] for r in recs]
    lo, hi = boot_ci(head, rng)
    point = sum(head) / n
    report["headroom_union_minus_frequency"] = {
        "point_pp": round(point * 100, 2),
        "ci95_pp": [round(lo * 100, 2), round(hi * 100, 2)],
        "swe_contextbench_danger_band_pp": [3.38, 8.32],
        "verdict": ("DIES — CI upper bound is inside/below the band that killed "
                    "SWE-ContextBench" if hi * 100 <= 8.32 else
                    "SURVIVES screening — CI upper bound clears the danger band"),
    }
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
