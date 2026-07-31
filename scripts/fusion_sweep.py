#!/usr/bin/env python3
"""Sweep the fuser's unfitted constants offline, from ONE instrumented run.

Weighted RRF is a pure function of the per-candidate `(channel, rank, score)`
votes that `code_lane_run_memphant.py` now records:

    contribution = magnitude * weight[channel] / (K + channel_rank)
    magnitude    = score for the Exact channel, 1.0 otherwise
    fused_score  = sum(contributions) * decay_retrievability

so every candidate weighting and every `K` can be evaluated against the same
180 questions without re-ingesting a corpus per arm (~2h each).

THE GATE: a simulator is worthless unless it reproduces the shipped ranking it
claims to improve on. `--verify` recomputes the SHIPPED constants from the
recorded votes and refuses to report a single sweep row unless the gold's
recomputed rank matches `gold_fused_rank` on every question. A sweep that
cannot reproduce the baseline is measuring its own reimplementation.

This ranks the POOL; it does not run packing. On this lane those coincide (the
50 `OutputLimit` misses are exactly the golds fusion put below rank 10, and
packing is faithful to the order it is handed -- see the build log), but any
winner here is a CANDIDATE that must be confirmed by a live arm before it moves
a default.
"""

from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from instrument_power import min_detectable_effect, power  # noqa: E402

# The shipped constants (`memphant-core/src/lib.rs`, `channel_weight`).
SHIPPED_WEIGHTS = {
    "exact": 1.0,
    "lexical": 1.0,
    "semantic": 2.0,
    # BM25 stands in for BOTH overlap passes, so it carries their combined
    # weight.
    "bm25": 1.0 + 2.0,
    "temporal": 0.5,
    "edge": 0.5,
    "vector": 2.0,
    "deep": 1.0,
}
SHIPPED_K = 60.0


def fused_score(row: dict, weights: dict[str, float], k: float, use_decay: bool) -> float:
    total = 0.0
    for channel, rank, score in row["channels"]:
        magnitude = score if channel == "exact" else 1.0
        total += magnitude * weights.get(channel, 0.0) / (k + rank)
    if use_decay:
        total *= row.get("decay_retrievability") or 1.0
    return total


def gold_rank(rows: list[dict], weights: dict[str, float], k: float, use_decay: bool):
    """Rank of the best-placed gold candidate under these constants.

    The tie-break mirrors the engine's own: `fused_score` descending, then unit
    id ascending as a deterministic stand-in for the engine's body ordering
    (only reached on exact score ties)."""
    scored = sorted(
        ((fused_score(row, weights, k, use_decay), row["unit_id"], row["is_gold"]) for row in rows),
        key=lambda triple: (-triple[0], triple[1]),
    )
    for index, (_score, _unit, is_gold) in enumerate(scored, start=1):
        if is_gold:
            return index
    return None


def mcnemar_exact_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2**n)


def cells(left: list[bool], right: list[bool], n: int) -> dict:
    b = sum(1 for x, y in zip(left, right) if x and not y)
    c = sum(1 for x, y in zip(left, right) if y and not x)
    n_d = b + c
    psi = n_d / n
    return {
        "b": b,
        "c": c,
        "n_d": n_d,
        "psi": psi,
        "delta": (b - c) / n,
        "exact_p": mcnemar_exact_p(b, c),
        "mde_at_80": min_detectable_effect(n, psi),
        "power": power(n, psi, abs(b - c) / n),
        # Two-sided exact McNemar has no rejection region below six discordant
        # pairs, so p=1.0 under that is arithmetic and not evidence.
        "is_a_measurement": n_d >= 6,
    }


def load(path: Path) -> list[dict]:
    report = json.loads(path.read_text())
    missing = [r["question_id"] for r in report["per_question"] if "channel_table" not in r]
    if missing:
        raise SystemExit(
            f"{path} predates the channel-table capture ({len(missing)} rows lack it); "
            "re-run the arm with the current harness"
        )
    return report["per_question"]


def verify(questions: list[dict]) -> None:
    """Refuse to sweep unless the shipped constants are reproduced exactly."""
    mismatches = []
    for row in questions:
        recomputed = gold_rank(row["channel_table"], SHIPPED_WEIGHTS, SHIPPED_K, use_decay=True)
        if recomputed != row["gold_fused_rank"]:
            mismatches.append((row["question_id"], row["gold_fused_rank"], recomputed))
    if mismatches:
        head = ", ".join(f"{q}: shipped={s} sim={r}" for q, s, r in mismatches[:8])
        raise SystemExit(
            f"SIMULATOR DOES NOT REPRODUCE THE SHIPPED FUSION on "
            f"{len(mismatches)}/{len(questions)} questions -- refusing to sweep. {head}"
        )
    print(f"simulator gate PASSED: shipped fusion reproduced on {len(questions)}/{len(questions)} "
          "questions (gold rank identical)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--k", type=int, default=10, help="cut depth scored (gold in top-k)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    questions = load(Path(args.provenance))
    n = len(questions)
    verify(questions)

    baseline = [
        (row["gold_fused_rank"] is not None and row["gold_fused_rank"] <= args.k)
        for row in questions
    ]
    print(f"baseline gold@{args.k}: {sum(baseline)}/{n}\n")

    arms: list[tuple[str, dict, float, bool]] = []
    for k_rrf in (1, 5, 10, 20, 30, 60):
        arms.append((f"K={k_rrf}", SHIPPED_WEIGHTS, float(k_rrf), True))
    for vec in (0.5, 1.0, 2.0, 3.0, 4.0, 6.0):
        weights = dict(SHIPPED_WEIGHTS, vector=vec)
        arms.append((f"vector_w={vec} (bm25=3.0)", weights, SHIPPED_K, True))
    for k_rrf in (5, 10, 20):
        for vec in (1.0, 2.0, 3.0, 4.0):
            weights = dict(SHIPPED_WEIGHTS, vector=vec)
            arms.append((f"K={k_rrf} vector_w={vec}", weights, float(k_rrf), True))
    arms.append(("decay OFF", SHIPPED_WEIGHTS, SHIPPED_K, False))

    results = []
    for label, weights, k_rrf, use_decay in arms:
        hits = [
            (lambda r: r is not None and r <= args.k)(
                gold_rank(row["channel_table"], weights, k_rrf, use_decay)
            )
            for row in questions
        ]
        cell = cells(hits, baseline, n)
        results.append({"arm": label, f"gold_at_{args.k}": sum(hits), **cell})

    results.sort(key=lambda r: -r[f"gold_at_{args.k}"])
    width = max(len(r["arm"]) for r in results)
    print(f"{'arm'.ljust(width)}  gold@{args.k}   b    c   n_d      p      MDE   power")
    for r in results:
        flag = "" if r["is_a_measurement"] else "  NOT A MEASUREMENT (n_d<6)"
        print(
            f"{r['arm'].ljust(width)}  {r[f'gold_at_{args.k}']:>6}  "
            f"{r['b']:>3}  {r['c']:>3}  {r['n_d']:>3}  {r['exact_p']:.4f}  "
            f"{r['mde_at_80']:.4f}  {r['power']:.3f}{flag}"
        )

    if args.out:
        payload = {
            "schema": "memphant.eval.fusion-sweep.v1",
            "n": n,
            "cut_depth": args.k,
            "paid_api_spend_usd": 0,
            "min_nd_for_rejection": 6,
            "simulator_gate": "shipped fusion reproduced on all questions",
            "shipped": {"weights": SHIPPED_WEIGHTS, "rrf_k": SHIPPED_K},
            f"baseline_gold_at_{args.k}": sum(baseline),
            "arms": results,
            "caveat": (
                "Ranks the POOL, does not run packing. Any winner is a CANDIDATE "
                "and must be confirmed by a live arm before it moves a default."
            ),
        }
        Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
