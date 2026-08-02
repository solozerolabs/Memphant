#!/usr/bin/env python3
"""S1b: offline tau re-calibration against the SENTENCE similarity distribution.

Preregistered in docs/build-log/2026-08-01-similarity-unit-swap.md section 10.
Reads the banked Arm U ledger (7,890 candidate pairs, dual jaccard) and the
pinned MemoryCode corpus, then reports -- for every REACHABLE tau on the
sentence staircase -- the realized firing count, precision and recall against
the co-declaring gold pairs.

Not decisional. This is a precision/coverage census on banked pairs; only a
live arm can carry latest-state-wins. Zero paid model calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import measure_key_recovery as kr  # noqa: E402
from external_instrument_adapter import (  # noqa: E402
    load_memorycode,
    sha256_file,
    structured_similarity,
)

LOCK = Path(__file__).resolve().parent.parent / "benchmarks" / "manifests" / "memorycode.lock.json"

U_TAU = 0.42


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm-u", required=True, type=Path)
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    digest = sha256_file(args.source.expanduser())
    lock = json.loads(LOCK.read_text())
    expected = lock["dataset"]["files"][lock["dataset"]["primary_file"]]["sha256"]
    if digest != expected:
        print(f"FATAL: corpus sha256 {digest} != pinned {expected}", file=sys.stderr)
        return 2

    arm_u = json.loads(args.arm_u.read_text())
    ledger = arm_u["diagnostics"]["structured_extractor"]["ledger"]

    groups = load_memorycode(args.source.expanduser())
    gold = kr.gold_structure(groups)
    bodies = {
        f"{g['group_id']}-s{i}": u["body"]
        for g in groups
        for i, u in enumerate(g["units"])
    }

    rows = []
    for e in ledger:
        left, right = e["session"], e["target_session"]
        if left not in bodies or right not in bodies:
            continue
        gid, li = left.rsplit("-s", 1)
        ri = right.rsplit("-s", 1)[1]
        pair = tuple(sorted((int(li), int(ri))))
        rows.append(
            {
                "sentence": structured_similarity(bodies[left], bodies[right], "sentence"),
                "co_declaring": pair in gold[gid]["gold_pairs"],
                "named_by_u": e["named"],
            }
        )

    positives = sum(r["co_declaring"] for r in rows)
    # ponytail: the sentence unit is quantized (small-denominator fractions), so
    # only the distinct observed values are reachable operating points. Sweeping
    # a fine tau grid would report the same handful of plateaus many times over.
    reachable = sorted({r["sentence"] for r in rows if r["sentence"] > 0})

    sweep = []
    for tau in reachable:
        fired = [r for r in rows if r["sentence"] >= tau]
        tp = sum(r["co_declaring"] for r in fired)
        sweep.append(
            {
                "tau": round(tau, 6),
                "firings": len(fired),
                "true_positives": tp,
                "precision": round(tp / len(fired), 6) if fired else None,
                "recall": round(tp / positives, 6) if positives else None,
            }
        )

    named = [r for r in rows if r["named_by_u"]]
    named_tp = sum(r["co_declaring"] for r in named)
    artifact = {
        "measurement": "s1b_tau_recalibration_sentence_unit",
        "decisional": False,
        "paid_model_calls": 0,
        "corpus_sha256": digest,
        "pairs_scored": len(rows),
        "co_declaring_pairs": positives,
        "arm_u_realized": {
            "tau": U_TAU,
            "firings": len(named),
            "true_positives": named_tp,
            "precision": round(named_tp / len(named), 6) if named else None,
            "recall": round(named_tp / positives, 6) if positives else None,
        },
        "reachable_operating_points": sweep,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

    print(f"pairs {len(rows)}  co-declaring {positives}  reachable taus {len(sweep)}")
    print(f"arm U @tau={U_TAU}: {len(named)} firings  "
          f"P={artifact['arm_u_realized']['precision']}  "
          f"R={artifact['arm_u_realized']['recall']}")
    print(f"{'tau':>9} {'firings':>8} {'precision':>10} {'recall':>8}")
    for s in sweep:
        print(f"{s['tau']:>9.6f} {s['firings']:>8d} {s['precision']:>10.4f} {s['recall']:>8.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
