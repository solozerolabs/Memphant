#!/usr/bin/env python3
"""Query->target lexical-leakage metric for the GitHub-lane bank (FREE, no model call).

A direct adaptation of ``scripts/track_r_leakage.py``
(``/Users/sidsharma/Memphant-af-w0-instrument``, commit ``e5fda0de``). The
tokenizer and the coverage definition are copied unchanged, deliberately: the
whole point is that this bank's numbers and Track R's reference figures are
produced by the same arithmetic, so "2.05x" means the same thing in both places.

    coverage(query, doc) = |T(query) & T(doc)| / |T(query)|
    T(s) = set(re.findall(r"[a-z0-9_]{3,}", s.lower()))

Track R scoped non-targets to "other events of the same attempt". Here the
scoping unit is ``scope_key``: the source repository for the private strata, and
the source language for P1 (the public "same-domain negatives" comparison the
calibration figure was computed against). The retrieval haystack is scoped that
way, so the leakage floor must be too.

Two floors, as in the reference script:

* ``non_target_exhaustive`` — the mean coverage over EVERY non-target doc in the
  same scope. No seed, no draw, so a lucky sample cannot move it. This is the
  figure the gate is computed against.
* ``non_target_sampled`` — one seeded random non-target, the form the Track R
  program spec §1 reported.

Reference figures this gate is stated against
(``docs/build-log/2026-07-30-coding-lane-first-win.md`` §4):

    Track R original    0.396 / 0.094  = 4.19x   FAILED
    Track R paraphrase  0.135 / 0.067  = 2.05x   accepted  <- the bar

Human-authored coding queries sit at 0.175-0.287 absolute coverage (SWE-PRBench
review comments 0.197, 1.76x). A stratum far BELOW 0.175 is flagged
``below_human_band``: it is harder than reality, not better, and an
under-specified query measures a retrieval problem no user has.

    python3 scripts/github_lane_leakage.py \
      --golden benchmarks/data/github_lane_golden.jsonl \
      --corpus benchmarks/data/github_lane_corpus.jsonl \
      --out docs/build-log/artifacts/github-lane/leakage.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
from collections import defaultdict
from pathlib import Path

NON_TARGET_SEED = 7
TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")

CONCENTRATION_BAR = 2.05  # prereg §4.1
HUMAN_BAND = (0.175, 0.287)  # prereg §2b
GATED_STRATA = {"S1", "S2", "S3", "S5", "P1"}  # S4 is reported, never gated


def tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def coverage(query: str, doc_text: str) -> float:
    asked = tokens(query)
    if not asked:
        return 0.0
    return len(asked & tokens(doc_text)) / len(asked)


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


def measure(goldens: list[dict], corpus: list[dict], seed: int,
            scope_field: str = "scope_key") -> dict:
    by_scope: dict[str, list[dict]] = defaultdict(list)
    for doc in corpus:
        by_scope[doc.get(scope_field, doc["scope_key"])].append(doc)
    by_id = {doc["doc_id"]: doc for doc in corpus}
    rng = random.Random(seed)
    rows = []
    for golden in sorted(goldens, key=lambda g: g["golden_id"]):
        target = by_id.get(golden["target_doc_id"])
        if target is None:
            continue
        scope = golden.get(scope_field, golden["scope_key"])
        pool = [d for d in by_scope[scope] if d["doc_id"] != target["doc_id"]]
        if not pool:
            # No in-scope negative exists, so this golden has no floor and is
            # excluded from the distribution rather than scored against zero —
            # scoring it against zero would inflate every ratio it entered.
            continue
        query = golden["query"]
        exhaustive = (
            statistics.fmean(coverage(query, d["text"]) for d in pool) if pool else 0.0
        )
        sampled = rng.choice(pool) if pool else None
        rows.append(
            {
                "golden_id": golden["golden_id"],
                "stratum": golden["stratum"],
                "stratum_name": golden["stratum_name"],
                "query_author": golden["query_author"],
                "privacy_class": golden["privacy_class"],
                "scope_key": scope,
                "scope_pool_size": len(pool),
                "target_coverage": round(coverage(query, target["text"]), 4),
                "non_target_exhaustive": round(exhaustive, 4),
                "non_target_sampled": (
                    round(coverage(query, sampled["text"]), 4) if sampled else None
                ),
            }
        )

    def stratum_block(subset: list[dict]) -> dict:
        target = describe([r["target_coverage"] for r in subset])
        exhaustive = describe([r["non_target_exhaustive"] for r in subset])
        sampled_values = [
            r["non_target_sampled"] for r in subset if r["non_target_sampled"] is not None
        ]
        sampled = describe(sampled_values) if sampled_values else None
        ratio = (
            round(target["mean"] / exhaustive["mean"], 4) if exhaustive["mean"] else None
        )
        stratum = subset[0]["stratum"]
        gated = stratum in GATED_STRATA
        band = "within_human_band"
        if target["mean"] < HUMAN_BAND[0]:
            band = "below_human_band"
        elif target["mean"] > HUMAN_BAND[1]:
            band = "above_human_band"
        return {
            "n": len(subset),
            "query_author": subset[0]["query_author"],
            "privacy_class": subset[0]["privacy_class"],
            "target": target,
            "non_target_exhaustive": exhaustive,
            "non_target_sampled": sampled,
            "concentration_vs_exhaustive": ratio,
            "gated": gated,
            "bar": CONCENTRATION_BAR if gated else None,
            "verdict": (
                ("PASS" if ratio is not None and ratio <= CONCENTRATION_BAR else "FAIL")
                if gated
                else "REPORTED_NOT_GATED"
            ),
            "absolute_band": band,
            "absolute_band_reference": (
                f"human-authored coding queries measure {HUMAN_BAND[0]}-{HUMAN_BAND[1]}; "
                "below that range is harder than reality, not better"
            ),
        }

    strata = {}
    for stratum in sorted({r["stratum"] for r in rows}):
        strata[stratum] = stratum_block([r for r in rows if r["stratum"] == stratum])

    gated_rows = [r for r in rows if r["stratum"] in GATED_STRATA]
    private_rows = [
        r for r in gated_rows if r["privacy_class"] == "private_repo"
    ]
    return {
        "schema": "memphant.eval.github-lane-leakage.v1",
        "preregistration": "docs/build-log/2026-07-31-github-lane-bar-and-privacy.md",
        "metric": (
            "coverage(query, doc) = |T(query) & T(doc)| / |T(query)|, "
            "T(s) = set(re.findall(r'[a-z0-9_]{3,}', s.lower()))"
        ),
        "non_target_scoping": (
            "every other corpus doc sharing the golden's scope_key "
            "(source repository for private strata, source language for P1)"
        ),
        "non_target_sampled_definition": f"one uniformly random non-target in scope, seed {seed}",
        "reference_figures": {
            "track_r_original": {"target": 0.396, "non_target": 0.094, "ratio": 4.19,
                                 "verdict": "FAILED"},
            "track_r_paraphrase": {"target": 0.135, "non_target": 0.067, "ratio": 2.05,
                                   "verdict": "accepted — this is the bar"},
            "human_authored_band": list(HUMAN_BAND),
        },
        "n": len(rows),
        "by_stratum": strata,
        "gate": {
            "bar": CONCENTRATION_BAR,
            "gated_strata_present": sorted(
                s for s in strata if strata[s]["gated"]
            ),
            "failing_strata": sorted(
                s for s in strata if strata[s]["verdict"] == "FAIL"
            ),
            "all_gated_strata_pass": all(
                strata[s]["verdict"] == "PASS" for s in strata if strata[s]["gated"]
            ),
        },
        "aggregate_excluding_s4": (
            stratum_block(gated_rows) if gated_rows else None
        ),
        "aggregate_private_excluding_s4": (
            stratum_block(private_rows) if private_rows else None
        ),
        "per_question": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=NON_TARGET_SEED)
    args = parser.parse_args()
    goldens = [json.loads(line) for line in args.golden.read_text().splitlines() if line.strip()]
    corpus = [json.loads(line) for line in args.corpus.read_text().splitlines() if line.strip()]
    report = measure(goldens, corpus, args.seed)
    # The preregistered scope (§4.1) is "other target documents of the same
    # repository". A language-scoped variant was also computed while building
    # P1; it is published here as a SECONDARY figure so the choice of scope is
    # visible and cannot be mistaken for bar-shopping after the fact.
    secondary = measure(goldens, corpus, args.seed, scope_field="scope_key_alt")
    report["secondary_scoping_language"] = {
        "note": (
            "NOT the gate. Non-targets scoped by source LANGUAGE for P1 (private "
            "strata unchanged, still repo-scoped). Published for comparability "
            "with the 1.76x same-domain calibration figure."
        ),
        "by_stratum": {
            key: {
                "n": value["n"],
                "target_mean": value["target"]["mean"],
                "non_target_exhaustive_mean": value["non_target_exhaustive"]["mean"],
                "concentration_vs_exhaustive": value["concentration_vs_exhaustive"],
            }
            for key, value in secondary["by_stratum"].items()
        },
    }
    report["golden_sha256"] = hashlib.sha256(args.golden.read_bytes()).hexdigest()
    report["corpus_sha256"] = hashlib.sha256(args.corpus.read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    printable = {k: v for k, v in report.items() if k != "per_question"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0 if report["gate"]["all_gated_strata_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
