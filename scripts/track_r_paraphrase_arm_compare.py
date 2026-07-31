#!/usr/bin/env python3
"""W0.2: the three-arm, two-stage decisive comparison on the paraphrase variant.

Same machinery, same haystack, same query string, same graders as the original
bank's Phase 1c/1e comparison — only the instrument changes. Three arms:

* ``bm25_control`` — ``code_lane_run_deterministic.py --scope attempt``.
* ``overlap_off`` / ``bm25code_off`` — ``code_lane_run_memphant.py
  --lexical-scorer {overlap,bm25-code} --embed-model off``.
* ``overlap_dense`` / ``bm25code_dense`` — the same two lexical scorers with
  ``--embed-model small``. Dense measured null-to-negative on the ORIGINAL bank,
  but that bank's questions carried their targets' identifiers; this one withholds
  them, which is the regime where semantic matching should be able to show a gain
  if it has one. Running it here is what makes that falsifiable.

Two stages are reported for every MemPhant arm, because the original bank's
result was two-stage and reporting only one understates or overstates it:

* **fused** — the ranked candidate list, read off ``gold_fused_rank``. Phase 1c
  established that ``gold_fused_rank <= k`` is exactly
  ``gate_common.provenance_hit`` at k for a single-span bank; the precondition
  is re-asserted here rather than assumed.
* **packed** — what actually reaches a reader, read off the runner's own
  ``hit_at_k`` over the packed bodies.

Every comparison is a paired exact McNemar (two-sided binomial on the discordant
pairs, not the chi-square approximation). The comparison that answers the
ownership question is ``memphant_after`` vs ``bm25_control``; the comparison that
sizes the fix is ``memphant_after`` vs ``memphant_before``.

``--original`` optionally takes the original bank's committed
``track_r_phase1e_combined_fixes.json`` so the same contrasts are printed side by
side and the **survival ratio** — how much of the measured win survives the
removal of the lexical give-away — is computed rather than eyeballed.

No verdict, no promotion, no default move, no reader, no paid call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import gate_common as gc  # noqa: E402
from track_r_retrieval_arm_compare import mcnemar_exact_p  # noqa: E402


def paired(left: list[bool], right: list[bool]) -> dict:
    both = sum(1 for a, b in zip(left, right) if a and b)
    left_only = sum(1 for a, b in zip(left, right) if a and not b)
    right_only = sum(1 for a, b in zip(left, right) if b and not a)
    return {
        "n": len(left),
        "both": both,
        "left_only": left_only,
        "right_only": right_only,
        "neither": len(left) - both - left_only - right_only,
        "mcnemar_exact_p": mcnemar_exact_p(left_only, right_only),
    }


def arm_hits(report: dict, order: list[str]) -> dict:
    rows = {row["question_id"]: row for row in report["per_question"]}
    fused = {
        k: [
            rows[q]["gold_fused_rank"] is not None and rows[q]["gold_fused_rank"] <= k
            for q in order
        ]
        for k in (5, 10)
    }
    packed = {k: [bool(rows[q][f"hit_at_{k}"]) for q in order] for k in (5, 10)}
    return {"fused": fused, "packed": packed}


def rates(hits: dict, order: list[str]) -> dict:
    return {
        f"{stage}_recall_at_{k}": sum(hits[stage][k]) / len(order)
        for stage in ("fused", "packed")
        for k in (5, 10)
    } | {
        f"{stage}_hits_at_{k}": sum(hits[stage][k])
        for stage in ("fused", "packed")
        for k in (5, 10)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument(
        "--arm", action="append", required=True, metavar="NAME=PATH",
        help="a code_lane_run_memphant.py provenance report, repeatable",
    )
    parser.add_argument(
        "--baseline-arm", default="overlap_off",
        help="the arm every other arm is additionally paired against",
    )
    parser.add_argument(
        "--survival-arm", default="bm25code_off",
        help="the arm whose margin over the control is compared to the original bank",
    )
    parser.add_argument("--leakage", type=Path, default=None)
    parser.add_argument("--original", type=Path, default=None)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    lock = json.loads(memphant_runner.golden_lock_path(args.golden).read_text())
    _, goldens = memphant_runner.verify_input_contract(args.corpus, args.golden, lock)
    if any(len(gc.required_spans(golden)) != 1 for golden in goldens):
        raise RuntimeError("fused-rank -> provenance_hit equivalence needs single-span goldens")
    order = [golden["question_id"] for golden in goldens]
    shapes = {golden["question_id"]: golden["question_type"] for golden in goldens}

    control = json.loads(args.control.read_text())
    if control.get("scope") != "attempt":
        raise RuntimeError("the control must be the attempt-scoped BM25 arm")
    control_rows = {row["question_id"]: row for row in control["per_question"]}
    control_hits = {k: [bool(control_rows[q][f"hit_at_{k}"]) for q in order] for k in (5, 10)}

    arm_paths = {}
    for entry in args.arm:
        name, _, path = entry.partition("=")
        arm_paths[name] = Path(path)
    reports = {name: json.loads(path.read_text()) for name, path in arm_paths.items()}
    for name, report in reports.items():
        if report["golden_sha256"] != lock["sha256"]:
            raise RuntimeError(f"arm {name} ran on a different golden bank")
        if report["corpus_sha256"] != control["corpus_sha256"]:
            raise RuntimeError(f"arm {name} ran on a different corpus")
        if report["golden_count"] != len(order):
            raise RuntimeError(f"arm {name} has a different question count")
    if control["golden_sha256"] != lock["sha256"]:
        raise RuntimeError("the control ran on a different golden bank")

    hits = {name: arm_hits(report, order) for name, report in reports.items()}

    arms = {}
    for name, report in reports.items():
        by_shape: dict[str, dict] = {}
        for question_id, fused10, packed10 in zip(
            order, hits[name]["fused"][10], hits[name]["packed"][10]
        ):
            bucket = by_shape.setdefault(shapes[question_id], {"fused": [], "packed": []})
            bucket["fused"].append(fused10)
            bucket["packed"].append(packed10)
        arms[name] = {
            "provenance_path": str(arm_paths[name]),
            "harness": {
                "lexical_scorer": report.get("lexical_scorer"),
                "embed_model": report.get("embed_model"),
                "recall_mode": report.get("recall_mode") or report.get("mode"),
                "k": report.get("k"),
                "budget_tokens": report.get("budget_tokens"),
                "git_head": report.get("git_head"),
                "label": report.get("label"),
            },
            **rates(hits[name], order),
            "gold_in_pool": sum(1 for row in report["per_question"] if row["gold_in_pool"]),
            "by_shape_at_10": {
                shape: {
                    "n": len(bucket["fused"]),
                    "fused_recall_at_10": sum(bucket["fused"]) / len(bucket["fused"]),
                    "packed_recall_at_10": sum(bucket["packed"]) / len(bucket["packed"]),
                }
                for shape, bucket in sorted(by_shape.items())
            },
            "paired_vs_control": {
                f"{stage}_at_{k}": paired(hits[name][stage][k], control_hits[k])
                for stage in ("fused", "packed")
                for k in (5, 10)
            },
        }

    baseline = args.baseline_arm
    paired_vs_baseline = {
        name: {
            f"{stage}_at_{k}": paired(hits[name][stage][k], hits[baseline][stage][k])
            for stage in ("fused", "packed")
            for k in (5, 10)
        }
        for name in reports
        if name != baseline
    }

    survival = None
    if args.original is not None:
        original = json.loads(args.original.read_text())
        original_control = original["scoped_bm25_control"]
        original_arms = original["factorial_2x2_hits_of_180"]["arms"]
        n_original = original["n"]

        def original_rate(arm: str, stage: str, k: int) -> float:
            return original_arms[arm][f"{stage}_hits_at_{k}"] / n_original

        margins = {}
        for stage in ("fused", "packed"):
            for k in (5, 10):
                original_margin = original_rate("both_fixes", stage, k) - original_control[
                    f"recall_at_{k}"
                ]
                new_margin = (
                    arms[args.survival_arm][f"{stage}_recall_at_{k}"]
                    - control[f"recall_at_{k}"]
                )
                margins[f"{stage}_at_{k}"] = {
                    "original_bank_margin_over_control": round(original_margin, 4),
                    "paraphrase_bank_margin_over_control": round(new_margin, 4),
                    "survival_ratio": round(new_margin / original_margin, 4)
                    if original_margin
                    else None,
                }
        survival = {
            "arm": args.survival_arm,
            "definition": (
                "margin = MemPhant(after) recall - scoped BM25 control recall, on that "
                "bank's own questions; survival_ratio = paraphrase margin / original "
                "margin. A ratio near 1 means the win was not an artifact of the "
                "lexical give-away; near 0 means it was; negative means the ordering "
                "reverses once the give-away is removed."
            ),
            "original_artifact": str(args.original),
            "original_n": n_original,
            "original_bank_reference": {
                "scoped_bm25_control": {
                    "recall_at_5": original_control["recall_at_5"],
                    "recall_at_10": original_control["recall_at_10"],
                },
                "memphant_before_overlap": {
                    f"{stage}_recall_at_{k}": round(original_rate("packing_fix_only", stage, k), 4)
                    for stage in ("fused", "packed")
                    for k in (5, 10)
                },
                "memphant_after_bm25_code": {
                    f"{stage}_recall_at_{k}": round(original_rate("both_fixes", stage, k), 4)
                    for stage in ("fused", "packed")
                    for k in (5, 10)
                },
            },
            "margins": margins,
        }

    out = {
        "schema": "memphant.eval.track-r-paraphrase-arm-compare.v1",
        "question": (
            "how much of the measured coding-lane win survives when the lexical "
            "give-away is removed from the instrument"
        ),
        "paid_api_spend_usd": 0,
        "golden_path": str(args.golden),
        "golden_sha256": lock["sha256"],
        "corpus_sha256": control["corpus_sha256"],
        "n": len(order),
        "stages": {
            "fused": "ranked candidate list, via gold_fused_rank (single-span bank)",
            "packed": "what reaches a reader, via the runner's hit_at_k over packed bodies",
        },
        "control": {
            "provenance_path": str(args.control),
            "haystack": "events of the bound attempt only",
            "documents_searched_median": sorted(
                row["documents_searched"] for row in control["per_question"]
            )[len(control["per_question"]) // 2],
            "recall_at_5": control["recall_at_5"],
            "recall_at_10": control["recall_at_10"],
            "hits_at_5": sum(control_hits[5]),
            "hits_at_10": sum(control_hits[10]),
        },
        "arms": arms,
        "baseline_arm": baseline,
        "paired_vs_baseline_arm": paired_vs_baseline,
        "survival_vs_original_bank": survival,
        "per_question": [
            {
                "question_id": question_id,
                "shape": shapes[question_id],
                "control_hit_at_10": control_hits[10][index],
                **{
                    f"{name}_{stage}_hit_at_10": hits[name][stage][10][index]
                    for name in reports
                    for stage in ("fused", "packed")
                },
            }
            for index, question_id in enumerate(order)
        ],
        "grade": "DIAGNOSTIC -- NOT PROMOTION-GRADE",
        "why_diagnostic": (
            "the bank this runs on FAILED its own preregistered headline leakage "
            "criterion (concentration 2.0180 against a bar of <= 1.50) and is used "
            "here deliberately and with that failure declared, because it is still a "
            "far less lexically confounded instrument than the original bank at "
            "3.9286. No number here may be promoted, published or cited as a "
            "standing measurement."
        ),
        "bank_leakage": (
            {
                key: value
                for key, value in json.loads(args.leakage.read_text()).items()
                if key != "per_question"
            }
            if args.leakage
            else None
        ),
        "note": (
            "measurement only; ownership question (d) is the owner's to decide and no "
            "default, checkbox or promotion moves here"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(
        f"{'scoped_bm25_control':18s}  r@5={control['recall_at_5']:.4f}  "
        f"r@10={control['recall_at_10']:.4f}"
    )
    for name, arm in arms.items():
        for stage in ("fused", "packed"):
            cells = []
            for k in (5, 10):
                block = arm["paired_vs_control"][f"{stage}_at_{k}"]
                cells.append(
                    f"r@{k}={arm[f'{stage}_recall_at_{k}']:.4f} "
                    f"(+{block['left_only']}/-{block['right_only']} "
                    f"p={block['mcnemar_exact_p']:.5g})"
                )
            print(f"{name:18s} {stage:7s} " + "  ".join(cells))
    print(f"  [bank concentration 2.0180 vs bar 1.50 -- DIAGNOSTIC, NOT PROMOTION-GRADE]")
    if survival:
        for key, block in survival["margins"].items():
            print(
                f"survival {key:12s} original={block['original_bank_margin_over_control']:+.4f} "
                f"paraphrase={block['paraphrase_bank_margin_over_control']:+.4f} "
                f"ratio={block['survival_ratio']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
