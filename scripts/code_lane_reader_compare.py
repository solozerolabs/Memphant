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
import math
import re
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


def leakage_five_tuple(path: Path, provenance_class: str) -> dict:
    """Leakage is a five-tuple, never a scalar (instrument register §1).

    Absolute coverage is not portable across unit definitions, and the
    exhaustive and sampled floors differ (2.018x vs 2.0518x on this very bank),
    so the floor KIND is named rather than left to be guessed. The exhaustive
    floor is the one carried: it is the mean over every non-target event of the
    same attempt, with no seed and no draw.
    """
    leak = json.loads(path.read_text(encoding="utf-8"))
    if leak.get("schema") != "memphant.eval.track-r-leakage.v2":
        raise ValueError(f"unexpected leakage schema: {leak.get('schema')!r}")
    return {
        "unit_definition": (
            "one unit = one content event of the attempt; "
            + leak["metric"]
        ),
        "absolute_target_coverage": leak["target"]["mean"],
        "floor": leak["non_target_exhaustive"]["mean"],
        "floor_kind": leak["non_target_exhaustive_definition"],
        "concentration": leak["concentration_vs_exhaustive"],
        "provenance_class": provenance_class,
    }


_WORD = re.compile(r"[a-z0-9_./-]+")


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(str(text).lower()))


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> dict | None:
    """Wilson score interval — the CI reported for the two conditional rates.

    Wald is not usable here: the conditional cells are small and the rates run
    near 0 and 1, where Wald produces intervals that leave [0, 1].
    """
    if n <= 0:
        return None
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return {
        "k": successes,
        "n": n,
        "point": p,
        "lo": max(0.0, centre - half),
        "hi": min(1.0, centre + half),
        "method": "Wilson score, 95%",
    }


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> dict | None:
    """Unpaired two-proportion test for P(correct|hit) vs P(correct|miss).

    These two rates are computed on DISJOINT subsets of questions, so McNemar
    does not apply; the contrast is between-group, not paired.
    """
    if n1 <= 0 or n2 <= 0:
        return None
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = (pooled * (1 - pooled) * (1 / n1 + 1 / n2)) ** 0.5
    if se == 0:
        return {"diff": p1 - p2, "z": None, "p_two_sided": 1.0}
    z = (p1 - p2) / se
    return {
        "diff": p1 - p2,
        "z": z,
        "p_two_sided": math.erfc(abs(z) / math.sqrt(2.0)),
        "test": "two-proportion z, pooled SE, two-sided",
    }


def gold_span_decomposition(
    reader_report: dict,
    retrieval_report: Path | None,
    equalized_evidence: Path,
    order: list[str],
    endpoint: str,
) -> dict:
    """Does answer correctness track gold-span retrieval@k at all?

    This is the question the coding lane never asked. Every banked coding-lane
    number is `hit_at_k` against ONE adjudicated provenance span, and the reader
    pilot answered correctly far more often than that metric allows -- so the
    span is not the only sufficient evidence, and retrieval@k may not track the
    outcome it is used as a proxy for.

    Three buckets, not two, because "correct without the gold span" has two very
    different causes and conflating them would hide the interesting one:

      * `correct_gold_present`     -- the adjudicated span was packed.
      * `correct_span_absent_text_present` -- the span was NOT packed, but the
        gold ANSWER STRING appears in the pack. The bank's single-span
        provenance is incomplete: other events in the same attempt carry the
        same fact, and retrieval@k scores those as misses. This inflates every
        measured deficit and understates every arm.
      * `correct_span_absent_text_absent` -- neither. The answer came from the
        reader's priors or from inference over the pack. This is the bucket the
        no-memory arm calibrates.

    The text check is deliberately crude (normalized substring, lowercase,
    punctuation-folded). It over-counts short gold answers that appear
    incidentally, so it is a LOWER bound on bucket 3 and an UPPER bound on
    bucket 2, and is reported as such rather than as a partition.
    """
    rows = {r["question_id"]: r for r in reader_report["per_question"]}
    evidence = {}
    for line in equalized_evidence.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            evidence[row["question_id"]] = row
    hits: dict[str, bool | None] = {}
    if retrieval_report is not None:
        report = json.loads(retrieval_report.read_text(encoding="utf-8"))
        for row in report.get("per_question", []):
            hits[row["question_id"]] = bool(row.get("hit_at_10"))

    tally = {
        "n": 0,
        "gold_span_in_pack": 0,
        "correct": 0,
        "correct_gold_present": 0,
        "correct_span_absent_text_present": 0,
        "correct_span_absent_text_absent": 0,
        "incorrect_gold_present": 0,
        "abstained_gold_present": 0,
    }
    for qid in order:
        row, ev = rows[qid], evidence.get(qid)
        if ev is None:
            continue
        tally["n"] += 1
        hit = hits.get(qid)
        correct = bool(row.get(endpoint))
        pack = _norm(" ".join(item["body"] for item in ev["evidence"]))
        text_present = _norm(ev["gold_answer"]) in pack if ev["gold_answer"] else False
        tally["gold_span_in_pack"] += int(bool(hit))
        tally["correct"] += int(correct)
        if correct and hit:
            tally["correct_gold_present"] += 1
        elif correct and text_present:
            tally["correct_span_absent_text_present"] += 1
        elif correct:
            tally["correct_span_absent_text_absent"] += 1
        if hit and not correct:
            tally["incorrect_gold_present"] += 1
        if hit and row.get("abstain"):
            tally["abstained_gold_present"] += 1

    n, hit_n = tally["n"], tally["gold_span_in_pack"]
    miss_n = n - hit_n
    # --- the primary deliverable: the joint distribution, written down whole.
    #
    # a = gold retrieved   AND answer correct
    # b = gold retrieved   AND answer wrong      -> bottleneck is DOWNSTREAM of retrieval
    # c = gold NOT retrieved AND answer correct  -> hit@k is not what carries the answer
    # d = gold NOT retrieved AND answer wrong
    a = tally["correct_gold_present"]
    b = hit_n - a
    c = tally["correct"] - a
    d = miss_n - c
    joint = {"a_hit_correct": a, "b_hit_wrong": b, "c_miss_correct": c, "d_miss_wrong": d}
    p_hit = wilson(a, hit_n)
    p_miss = wilson(c, miss_n)
    # phi (= Pearson r for two binary vectors) and raw agreement between the
    # hit@10 vector and the answer_correct vector.
    denom = ((a + b) * (c + d) * (a + c) * (b + d)) ** 0.5
    phi = ((a * d - b * c) / denom) if denom else None
    return tally | joint | {
        "retrieval_report_available": bool(hits),
        "joint_2x2": joint,
        "p_correct_given_gold_present_ci": p_hit,
        "p_correct_given_gold_absent_ci": p_miss,
        "conditional_rate_difference": two_proportion_z(a, hit_n, c, miss_n),
        "hit_vs_correct_phi": phi,
        "hit_vs_correct_agreement": ((a + d) / n) if n else None,
        "retrieval_hit_at_10": hit_n / n if n else None,
        "answer_accuracy": tally["correct"] / n if n else None,
        # The two conditional rates are the whole point: if they are close, the
        # gold span is not what carries the answer.
        "p_correct_given_gold_present": (
            tally["correct_gold_present"] / hit_n if hit_n else None
        ),
        "p_correct_given_gold_absent": (
            (tally["correct"] - tally["correct_gold_present"]) / miss_n if miss_n else None
        ),
        "correct_without_gold_span": tally["correct"] - tally["correct_gold_present"],
        "text_check": (
            "normalized substring of the gold answer in the packed bodies; crude, "
            "so text_present is an UPPER bound and text_absent a LOWER bound"
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
    refused = sum(1 for row in subset if row.get("reader_refusal"))
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
        # A provider refusal is scored as a non-answer, so this many rows are
        # guaranteed-incorrect for a reason that is not the pack. The arm's
        # accuracy is a LOWER bound by exactly this count.
        "reader_provider_refusals": refused,
        "reader_provider_refusal_rate": refused / len(subset) if subset else 0.0,
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
    parser.add_argument("--retrieval", action="append", default=[],
                        metavar="NAME=PROVENANCE.json",
                        help="that arm's retrieval provenance, for the gold-span decomposition")
    parser.add_argument("--control", required=True, help="arm name used as the reference")
    parser.add_argument(
        "--control-description",
        default="deterministic attempt-scoped BM25",
        help=(
            "what the control arm's retrieval actually is. Stamped verbatim into "
            "the evidence contract's harness.scorer field, which would otherwise "
            "name a control this run did not use."
        ),
    )
    parser.add_argument("--stage-manifest", required=True, type=Path,
                        help="stage-equalization.json from code_lane_reader_prepare.py")
    parser.add_argument("--bank", required=True, help="golden bank label, e.g. paraphrase")
    parser.add_argument("--endpoint", choices=ENDPOINTS, default="answer_correct",
                        help="graded field; see ENDPOINTS in this module")
    parser.add_argument("--claim", required=True,
                        help="the one sentence this artifact is cited for")
    parser.add_argument("--leakage", type=Path,
                        help="the bank's memphant.eval.track-r-leakage.v2 JSON")
    parser.add_argument("--provenance-class", required=True,
                        help="who authored the query, and could it be written FROM the target")
    parser.add_argument("--corpus-snapshot-id", required=True)
    parser.add_argument("--corpus-n-items", type=int, required=True)
    parser.add_argument("--license-id", required=True)
    parser.add_argument("--license-source", required=True)
    parser.add_argument("--license-evidence", required=True)
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

    retrievals: dict[str, Path] = {}
    for spec in args.retrieval:
        name, _, path = spec.partition("=")
        retrievals[name] = Path(path)

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
        "gold_span_decomposition": {
            name: gold_span_decomposition(
                report,
                retrievals.get(name),
                Path(manifest["arms"][name]["equalized_path"]),
                order,
                args.endpoint,
            )
            for name, report in reports.items()
        },
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
    # ---- the evidence contract, generated from this run's own cells.
    #
    # The headline contrast is the one the contract speaks for. `decisional` is
    # NOT a choice: it is false whenever the structural floor is unmet, whenever
    # any arm was a stub or a smoke, and whenever any arm recorded an error --
    # a reader or judge failure scores the row incorrect, so an errored arm
    # reports a deficit it did not measure.
    headline = contrasts[f"memphant_vs_{args.control}"]
    accounting = out["judge_accounting"]
    clean = all(
        not v.get("openrouter_stub_url") and not v.get("promotion_ineligible")
        for v in binding.values()
    ) and all(a["rows_with_error"] == 0 for a in accounting.values())
    leak = leakage_five_tuple(args.leakage, args.provenance_class) if args.leakage else None
    out["evidence_contract"] = {
        "schema_version": 1,
        "decisional": bool(
            headline["n_discordant"] >= N_D_FLOOR and clean
        ),
        "claim": args.claim,
        "power": {
            "test": "two-sided exact (conditional binomial) McNemar",
            "n": headline["n"],
            "b": headline["b_left_only"],
            "c": headline["c_right_only"],
            "n_d": headline["n_discordant"],
            "psi_observed": headline["realized_psi"],
            "mde_at_80": headline["mde_at_80_power"],
            "computed_by": "scripts/instrument_power.py:min_detectable_effect",
            "source": str(args.out),
        },
        "harness": {
            "embed_model": "fastembed:bge-small-en-v1.5 (shipped default)",
            "scorer": (
                "memphant recall mode=fast lexical=bm25-code (shipped default) "
                f"vs {args.control_description}; reader "
                f"{next(iter(readers.values()))}, judge "
                f"{next(iter(judges.values()))}, judge_profile rag-supported-v1"
            ),
            "k": manifest["k"],
            "budget": manifest["budget_tokens"],
            "flags": [
                f"endpoint={args.endpoint}",
                f"prompt_versions={sorted({str(b['prompt_version']) for b in binding.values()})}",
                f"reader_profiles={sorted({str(b['reader_profile']) for b in binding.values()})}",
                "provider_only=anthropic",
                "one packer, one k, one budget on every arm",
            ],
            "command": "scripts/code_lane_reader_run.sh",
        },
        "corpus": {
            "sha256": manifest["lineage"]["corpus_sha256"],
            "snapshot_id": args.corpus_snapshot_id,
            "n_items": args.corpus_n_items,
        },
        "instrument_verification": {
            "shipped_rows_verified": True,
            "rows_counted": len(order),
            "fields_counted": {
                "goldens_scored": len(order),
                "arms": len(arm_vectors),
                "reader_calls": sum(a["fresh_calls"] or 0 for a in accounting.values()),
            },
            "license_id": args.license_id,
            "license_source": args.license_source,
            "license_evidence": args.license_evidence,
        },
        "leakage": leak,
        "probe_kind": "lever",
        "mechanism_enabled": True,
        "mechanism_evidence": (
            "The lever is MemPhant recall itself. Liveness is the arms' own "
            "measured divergence rather than an assertion: the arms disagree on "
            f"{headline['n_discordant']} of {headline['n']} questions at the "
            "reader endpoint, their packs differ in mean tokens "
            f"({manifest['stage_parity']['tokens_mean_by_arm']}), and the judge "
            "fired on "
            + ", ".join(
                f"{name} {a['judge_fire_rate']:.2f}" for name, a in accounting.items()
            )
            + " of rows."
        ),
        "attribution": {"method": "paired arm contrast at one lineage"},
        "bar": None,
        "notes": (
            "Reader-QA, not retrieval@k. Every arm was packed by one packer at "
            "one k and one budget before reaching the reader; the comparison "
            "script refuses arms not bound by sha256 to the same equalization "
            "manifest. The no-memory arm uses a closed-book reader prompt and is "
            "a saturation probe, never the paired comparator for a memory claim."
        ),
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
