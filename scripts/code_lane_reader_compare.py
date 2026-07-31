#!/usr/bin/env python3
"""Paired reader-QA comparison for the coding lane.

Consumes ``scripts/run_reader.py`` reports (one per arm), all produced from
stage-equalized evidence written by ``scripts/code_lane_reader_prepare.py``,
and emits the decision artifact: per-contrast 2x2 cells, two-sided exact
McNemar p, realized psi, MDE at 80% power, achieved power, the per-question
correctness vectors, judge fire-rate accounting, and the lineage block.

Design rules this script enforces rather than assumes:

* **Same stage, same haystack.** It refuses to run unless every arm's reader
  report is bound to an evidence file listed in the same stage-equalization
  manifest, and unless the manifest says every arm went through one packer at
  one k and one budget. The coding lane already has one permanently VOID
  number from comparing a packed arm to an unpacked one; this is the gate.
* **b and c are always written down.** Instrument-register item Z6: a bootstrap
  CI alone makes psi unrecoverable later. Every contrast commits its cells.
* **n_d >= 6 is a structural floor.** Two-sided exact McNemar has no rejection
  region below six discordant pairs, so a p of 1.0 there is arithmetic, not
  evidence. Contrasts below the floor are labelled NOT A MEASUREMENT.
* **Realized psi only.** Power and MDE come from this run's own cells.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from instrument_power import min_detectable_effect, power, required_n  # noqa: E402
from track_r_retrieval_arm_compare import mcnemar_exact_p  # noqa: E402

N_D_FLOOR = 6


def cells(left: list[bool], right: list[bool]) -> dict:
    """2x2 for the paired contrast. b favours `left`, c favours `right`."""
    both = sum(1 for a, b in zip(left, right) if a and b)
    b = sum(1 for a, b in zip(left, right) if a and not b)
    c = sum(1 for a, b in zip(left, right) if b and not a)
    n = len(left)
    n_d = b + c
    psi = n_d / n if n else 0.0
    delta = (b - c) / n if n else 0.0
    p = mcnemar_exact_p(b, c)
    mde = min_detectable_effect(n, psi) if psi > 0 else None
    achieved = power(n, psi, abs(delta)) if psi > 0 else 0.0
    need = required_n(psi, abs(delta)) if psi > 0 and abs(delta) > 0 else None
    return {
        "n": n,
        "both_correct": both,
        "b_left_only": b,
        "c_right_only": c,
        "neither": n - both - b - c,
        "n_discordant": n_d,
        "realized_psi": psi,
        "delta": delta,
        "left_accuracy": sum(left) / n if n else 0.0,
        "right_accuracy": sum(right) / n if n else 0.0,
        "mcnemar_exact_p": p,
        "mde_at_80_power": mde,
        "achieved_power_at_observed_delta": achieved,
        "required_n_for_observed_delta": need,
        "significant_at_0_05": p <= 0.05,
        "verdict": (
            "NOT A MEASUREMENT (n_d < 6: the two-sided exact test has no "
            "rejection region at any effect size)"
            if n_d < N_D_FLOOR
            else ("significant" if p <= 0.05 else "null (see achieved power)")
        ),
    }


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# Endpoint choice is load-bearing, so it is explicit rather than defaulted
# silently.
#
# `correct` under --judge-profile rag-supported-v1 is
# `answer_correct AND fully_supported`, and `fully_supported` cannot be true
# against an empty evidence pack — the strict parser rejects a true flag with no
# cited ranks. So `correct` scores the no-memory arm 0 BY CONSTRUCTION, which
# makes it an inert baseline rather than a saturation check, and inflates every
# comparison against it into a tautology.
#
# `answer_correct` is the judge's correctness boolean alone. It is the only
# endpoint that means the same thing for an arm with a pack and an arm without
# one, so it is the primary endpoint for anything the no-memory arm takes part
# in. `correct` remains the right, stricter endpoint for grounded correctness
# between two arms that both have packs.
ENDPOINTS = ("answer_correct", "correct")


def vectors(report: dict, order: list[str], endpoint: str) -> list[bool]:
    rows = {row["question_id"]: row for row in report["per_question"]}
    missing = [qid for qid in order if qid not in rows]
    if missing:
        raise ValueError(
            f"reader report {report.get('label')!r} is missing {len(missing)} "
            f"question ids, first: {missing[0]}"
        )
    if endpoint == "answer_correct" and report.get("judge_profile") != "rag-supported-v1":
        raise ValueError(
            "the answer_correct endpoint exists only under --judge-profile "
            f"rag-supported-v1; report {report.get('label')!r} used "
            f"{report.get('judge_profile')!r}"
        )
    # A row the judge never reached (reader abstained, parse failed, judge
    # failed) has answer_correct None. That is a scored miss, not a dropped row:
    # dropping it would silently change n per arm and unpair the comparison.
    return [bool(rows[qid].get(endpoint)) for qid in order]


def judge_accounting(report: dict, order: list[str]) -> dict:
    """The judge's own behaviour, measured rather than assumed."""
    rows = {row["question_id"]: row for row in report["per_question"]}
    subset = [rows[qid] for qid in order]
    methods: dict[str, int] = {}
    for row in subset:
        methods[str(row.get("judge_method"))] = methods.get(str(row.get("judge_method")), 0) + 1
    # Both judge profiles, named explicitly: `rag-supported-v1` records
    # "rag_supported_llm_judge" and the LongMemEval profile records "llm_judge".
    # Counting only one of them would report a 0% fire rate for a judge that in
    # fact fired on every row -- the exact "assumed, not measured" failure the
    # brief forbids.
    llm_judged = methods.get("llm_judge", 0) + methods.get("rag_supported_llm_judge", 0)
    parse_status: dict[str, int] = {}
    for row in subset:
        key = str(row.get("judge_parse_status"))
        parse_status[key] = parse_status.get(key, 0) + 1
    abstained = sum(1 for row in subset if row.get("abstain"))
    errored = sum(
        1 for row in subset
        if row.get("reader_error") or row.get("parse_error") or row.get("judge_error")
    )
    return {
        "n": len(subset),
        "judge_method_counts": methods,
        "judge_parse_status_counts": parse_status,
        "judge_fire_rate": llm_judged / len(subset) if subset else 0.0,
        "reader_abstentions": abstained,
        "reader_abstention_rate": abstained / len(subset) if subset else 0.0,
        "rows_with_error": errored,
        "reader_errors": sum(1 for row in subset if row.get("reader_error")),
        "parse_errors": sum(1 for row in subset if row.get("parse_error")),
        "judge_errors": sum(1 for row in subset if row.get("judge_error")),
        "errors_block": report.get("errors"),
        "fresh_calls": report.get("fresh_calls"),
        "cached_calls": report.get("cached_calls"),
        "reported_spend_usd": (report.get("spend_control") or {}).get("reported_spend_usd"),
        "unsettled_liability_usd": (report.get("spend_control") or {}).get(
            "unsettled_liability_usd"
        ),
        "complete": report.get("complete"),
        "aborted": report.get("aborted"),
        "smoke_only": report.get("smoke_only"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", action="append", required=True, metavar="NAME=REPORT.json")
    parser.add_argument("--control", required=True, help="arm name used as the reference")
    parser.add_argument("--stage-manifest", required=True, type=Path,
                        help="stage-equalization.json from code_lane_reader_prepare.py")
    parser.add_argument("--bank", required=True, help="golden bank label, e.g. paraphrase")
    parser.add_argument("--endpoint", choices=ENDPOINTS, default="answer_correct",
                        help="graded field; see ENDPOINTS in this module")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.stage_manifest.read_text(encoding="utf-8"))
    if manifest.get("schema") != "memphant.eval.code-lane-reader-stage-equalization.v1":
        raise ValueError("stage manifest schema mismatch")

    reports: dict[str, dict] = {}
    for spec in args.arm:
        name, _, path = spec.partition("=")
        if not name or not path:
            parser.error(f"--arm expects NAME=PATH, got {spec!r}")
        reports[name] = load_report(Path(path))
    if args.control not in reports:
        parser.error(f"--control {args.control!r} is not among the supplied arms")

    # --- STAGE GATE: every arm must be bound to this manifest's equalized file.
    binding = {}
    for name, report in reports.items():
        arm_entry = manifest["arms"].get(name)
        if arm_entry is None:
            raise ValueError(
                f"arm {name!r} is not in the stage-equalization manifest; refusing "
                "to compare arms that were not equalized together"
            )
        expected = arm_entry["equalized_sha256"]
        actual = report.get("source_evidence_sha256") or report.get("evidence_sha256")
        if actual != expected:
            raise ValueError(
                f"arm {name!r} reader report was NOT run on the equalized evidence: "
                f"report evidence sha256 {actual} != manifest {expected}. This is "
                "the exact stage mismatch that voided the 0.506-vs-0.806 comparison."
            )
        binding[name] = {
            "equalized_evidence_sha256": expected,
            "evaluated_evidence_sha256": report.get("evaluated_evidence_sha256"),
            "tokens_mean": arm_entry["stage_after"]["tokens_mean"],
            "items_mean": arm_entry["stage_after"]["items_mean"],
            "reader_model_id": report.get("reader_model_id"),
            "judge_model_id": report.get("judge_model_id"),
            "prompt_version": report.get("prompt_version"),
            "reader_profile": report.get("reader_profile"),
            "openrouter_stub_url": report.get("openrouter_stub_url"),
            "promotion_ineligible": report.get("promotion_ineligible"),
            "evaluator_sha256": (report.get("evaluator_fingerprint") or {}).get("sha256"),
        }

    # --- PAIRING GATE: one question order, common to every arm.
    id_sets = {name: {row["question_id"] for row in r["per_question"]} for name, r in reports.items()}
    common = set.intersection(*id_sets.values())
    order = [qid for qid in
             (row["question_id"] for row in reports[args.control]["per_question"])
             if qid in common]
    dropped = {name: sorted(ids - common) for name, ids in id_sets.items() if ids - common}

    # --- reader/judge identity must be constant across the SCORED arms, or the
    # contrast measures the reader, not the memory.
    readers = {name: b["reader_model_id"] for name, b in binding.items()}
    judges = {name: b["judge_model_id"] for name, b in binding.items()}
    if len(set(readers.values())) != 1:
        raise ValueError(f"arms do not share one reader model: {readers}")
    if len(set(judges.values())) != 1:
        raise ValueError(f"arms do not share one judge model: {judges}")

    arm_vectors = {name: vectors(report, order, args.endpoint)
                   for name, report in reports.items()}
    accuracies = {name: sum(v) / len(v) if v else 0.0 for name, v in arm_vectors.items()}

    contrasts = {}
    control_vector = arm_vectors[args.control]
    for name, vector in arm_vectors.items():
        if name == args.control:
            continue
        contrasts[f"{name}_vs_{args.control}"] = cells(vector, control_vector)
    names = [n for n in arm_vectors if n != args.control]
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            contrasts[f"{left}_vs_{right}"] = cells(arm_vectors[left], arm_vectors[right])

    out = {
        "schema": "memphant.eval.code-lane-reader-qa.v1",
        "bank": args.bank,
        "n_paired": len(order),
        "control_arm": args.control,
        "endpoint": args.endpoint,
        "endpoint_meaning": (
            "judge verdict that the reader's ANSWER is correct against the gold "
            "answer -- end behaviour, not retrieval@k"
            if args.endpoint == "answer_correct"
            else "answer correct AND every claim entailed by the pack (grounded "
                 "correctness); structurally unreachable for an empty pack"
        ),
        "test": "two-sided exact (conditional binomial) McNemar, alpha=0.05",
        "n_d_structural_floor": N_D_FLOOR,
        "arm_accuracy": accuracies,
        "arm_correct_counts": {name: sum(v) for name, v in arm_vectors.items()},
        "contrasts": contrasts,
        "judge_accounting": {name: judge_accounting(r, order) for name, r in reports.items()},
        "arm_binding": binding,
        "questions_dropped_from_pairing": dropped,
        "per_question_vectors": {
            "order": order,
            "arms": {name: [int(x) for x in v] for name, v in arm_vectors.items()},
        },
        "stage_equalization": {
            "manifest_path": str(args.stage_manifest),
            "manifest_sha256": hashlib.sha256(
                args.stage_manifest.read_bytes()
            ).hexdigest(),
            "k": manifest["k"],
            "budget_tokens": manifest["budget_tokens"],
            "tokens_mean_by_arm": manifest["stage_parity"]["tokens_mean_by_arm"],
            "tokens_mean_spread": manifest["stage_parity"]["tokens_mean_spread"],
        },
        "lineage": manifest["lineage"],
        "compare_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")

    print(json.dumps({
        "n_paired": len(order),
        "accuracy": {k: round(v, 4) for k, v in accuracies.items()},
        "contrasts": {
            k: {
                "b": v["b_left_only"], "c": v["c_right_only"], "n_d": v["n_discordant"],
                "delta": round(v["delta"], 4), "p": v["mcnemar_exact_p"],
                "psi": round(v["realized_psi"], 4),
                "mde": round(v["mde_at_80_power"], 4) if v["mde_at_80_power"] else None,
                "verdict": v["verdict"],
            } for k, v in contrasts.items()
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
