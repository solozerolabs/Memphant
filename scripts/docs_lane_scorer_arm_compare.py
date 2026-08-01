#!/usr/bin/env python3
"""Paired exact-McNemar comparison of two docs-lane lexical-scorer arms.

The docs-plane follow-up to the 2026-08-01 dense-default-on flip: does making
`bm25-code` the shipped default cost anything on the docs plane, where the
code-lane arms cannot speak? Both arms are the SAME ingest configuration on the
SAME pinned corpus with dense ON; only `--lexical-scorer` differs.

Pools the two disjoint golden sets (v1 + v2, 60 each, zero section overlap)
into one n=120 comparison, because each alone sits near the n_d >= 6 floor
below which a two-sided exact binomial has no rejection region at all.

Reports, per k: the 2x2 discordant cells, the exact two-sided McNemar p, the
realized psi (discordant rate) and the MDE this run's own cells support --
never an assumed psi. No reader, no judge, no paid call.

Usage:
  docs_lane_scorer_arm_compare.py --arm NAME:prov_v1.json,prov_v2.json \\
                                  --control NAME:prov_v1.json,prov_v2.json \\
                                  --out comparison.json
"""

from __future__ import annotations

import argparse
import json
from math import comb, sqrt
from pathlib import Path


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact binomial on the discordant pairs (never the chi-square
    approximation -- these cell counts are far too small for it)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def min_detectable_effect(n: int, psi: float, alpha: float = 0.05, power: float = 0.80) -> float | None:
    """MDE in proportion points for a paired McNemar at this run's OWN realized
    discordant rate psi. Returns None when psi is 0 (nothing is detectable)."""
    if psi <= 0 or n <= 0:
        return None
    z_alpha, z_beta = 1.959963985, 0.841621234
    return (z_alpha + z_beta) * sqrt(psi / n)


def load_arm(spec: str) -> tuple[str, dict[str, dict]]:
    name, _, paths = spec.partition(":")
    if not name or not paths:
        raise SystemExit(f"--arm/--control expects NAME:path[,path]; got {spec!r}")
    rows: dict[str, dict] = {}
    meta: dict[str, object] = {}
    for path in paths.split(","):
        report = json.loads(Path(path).read_text())
        for field in ("lexical_scorer", "embed_model", "recall_mode", "k",
                      "resource_chunks", "corpus_revision", "runtime_config_fingerprint"):
            value = report.get(field)
            if field in meta and meta[field] != value:
                raise SystemExit(
                    f"{name}: arm reports disagree on {field}: {meta[field]!r} vs {value!r}"
                )
            meta[field] = value
        for row in report["per_question"]:
            qid = row["question_id"]
            if qid in rows:
                raise SystemExit(f"{name}: duplicate question_id across golden sets: {qid}")
            rows[qid] = row
    return name, {"rows": rows, "meta": meta}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    arm_name, arm = load_arm(args.arm)
    ctl_name, ctl = load_arm(args.control)

    if set(arm["rows"]) != set(ctl["rows"]):
        raise SystemExit("arms did not score the same question set -- unpairable")
    # The whole point of the comparison is that ONLY the scorer differs.
    for field in ("embed_model", "recall_mode", "k", "resource_chunks", "corpus_revision"):
        if arm["meta"][field] != ctl["meta"][field]:
            raise SystemExit(
                f"arms differ on {field} ({arm['meta'][field]!r} vs {ctl['meta'][field]!r}) -- "
                "this is not a clean scorer contrast"
            )
    if arm["meta"]["lexical_scorer"] == ctl["meta"]["lexical_scorer"]:
        raise SystemExit("both arms recorded the same lexical_scorer -- the lever is INERT")
    if arm["meta"]["runtime_config_fingerprint"] == ctl["meta"]["runtime_config_fingerprint"]:
        raise SystemExit("both arms share a runtime_config_fingerprint -- the lever is INERT")

    qids = sorted(arm["rows"])
    out: dict[str, object] = {
        "comparison": f"{arm_name} vs {ctl_name}",
        "arm_lexical_scorer": arm["meta"]["lexical_scorer"],
        "control_lexical_scorer": ctl["meta"]["lexical_scorer"],
        "shared": {f: arm["meta"][f] for f in
                   ("embed_model", "recall_mode", "k", "resource_chunks", "corpus_revision")},
        "fingerprints": {arm_name: arm["meta"]["runtime_config_fingerprint"],
                         ctl_name: ctl["meta"]["runtime_config_fingerprint"]},
        "n": len(qids),
        "paid_api_spend_usd": 0,
    }
    for field in ("hit_at_5", "hit_at_10"):
        a = [bool(arm["rows"][q][field]) for q in qids]
        c = [bool(ctl["rows"][q][field]) for q in qids]
        both = sum(1 for x, y in zip(a, c) if x and y)
        arm_only = sum(1 for x, y in zip(a, c) if x and not y)
        ctl_only = sum(1 for x, y in zip(a, c) if y and not x)
        neither = len(qids) - both - arm_only - ctl_only
        n_d = arm_only + ctl_only
        psi = n_d / len(qids)
        out[field] = {
            "n": len(qids),
            "arm_hits": both + arm_only,
            "control_hits": both + ctl_only,
            "arm_recall": (both + arm_only) / len(qids),
            "control_recall": (both + ctl_only) / len(qids),
            "both": both, "arm_only": arm_only, "control_only": ctl_only, "neither": neither,
            "discordant_n_d": n_d,
            "meets_n_d_floor_of_6": n_d >= 6,
            "realized_psi": psi,
            "mde_pp_at_realized_psi": (
                None if (m := min_detectable_effect(len(qids), psi)) is None else round(m * 100, 2)
            ),
            "mcnemar_exact_p": mcnemar_exact_p(arm_only, ctl_only),
        }
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
