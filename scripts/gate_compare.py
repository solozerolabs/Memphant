#!/usr/bin/env python3
"""Engine-vs-engine comparison for the Syndai replacement gate (W10).

Combines the two runners' provenance reports (R@5/R@10 retrieval) and the two
``run_reader.py`` QA reports (reader-scored answer accuracy) into a single
verdict, applying the binding gate rule from the plan addendum:

    MemPhant must BEAT Syndai's stack on the golden set — the paired QA-accuracy
    bootstrap CI (MemPhant - Syndai) must EXCLUDE ZERO and be positive — before
    any replacement work. If it does not beat it, that is a finding, not a
    failure.

Both a retrieval delta (provenance hit@10) and the QA delta are reported with a
source-document cluster bootstrap over the explicit pinned golden file. Pairing
is by question_id.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402

BOOTSTRAP_RESAMPLES = gc._RUN_READER.BOOTSTRAP_RESAMPLES


def _load(path: str) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value, raw


def _clustered_ci(
    pairs: list[tuple[str, dict, dict]], clusters: dict[str, str], field: str, seed: int
) -> dict:
    values: dict[str, list[float]] = defaultdict(list)
    for question_id, left, right in pairs:
        values[clusters[question_id]].append(float(left[field]) - float(right[field]))
    return gc.cluster_bootstrap_ci(values, resamples=BOOTSTRAP_RESAMPLES, seed=seed)


def _bind_reader(
    reader: dict, provenance: dict, provenance_bytes: bytes, *, name: str
) -> None:
    if reader.get("source_evidence_sha256") != provenance.get("evidence_sha256"):
        raise ValueError(f"{name} reader evidence is not bound to provenance")
    provenance_sha = gc.sha256_hex(provenance_bytes)
    if reader.get("retrieval_report_sha256") != provenance_sha:
        raise ValueError(f"{name} reader retrieval report hash differs")
    evaluator = reader.get("evaluator_fingerprint")
    if not isinstance(evaluator, dict) or evaluator.get("retrieval_report_sha256") != provenance_sha:
        raise ValueError(f"{name} evaluator is not bound to retrieval report")


def _bind_negative_reader(
    reader: dict, provenance: dict, provenance_bytes: bytes, *, name: str
) -> None:
    negative = provenance.get("negative")
    if not isinstance(negative, dict):
        raise ValueError(f"{name} negative provenance is missing")
    if reader.get("source_evidence_sha256") != negative.get("negative_evidence_sha256"):
        raise ValueError(f"{name} negative reader evidence is not bound to provenance")
    provenance_sha = gc.sha256_hex(provenance_bytes)
    if reader.get("retrieval_report_sha256") != provenance_sha:
        raise ValueError(f"{name} negative reader retrieval report hash differs")
    evaluator = reader.get("evaluator_fingerprint")
    if not isinstance(evaluator, dict) or evaluator.get("retrieval_report_sha256") != provenance_sha:
        raise ValueError(f"{name} negative evaluator is not bound to retrieval report")


def _negative_rows(provenance: dict, *, name: str) -> dict[str, dict]:
    negative = provenance.get("negative")
    rows = negative.get("negative_per_case") if isinstance(negative, dict) else None
    if not isinstance(rows, list) or len(rows) != 10:
        raise ValueError(f"{name} negative provenance must contain exactly 10 cases")
    if not re.fullmatch(
        r"[0-9a-f]{64}", str(negative.get("negative_evidence_sha256", ""))
    ):
        raise ValueError(f"{name} negative evidence hash is invalid")
    by_id = {}
    for row in rows:
        case_id = row.get("case_id") if isinstance(row, dict) else None
        if not isinstance(case_id, str) or not case_id or case_id in by_id:
            raise ValueError(f"{name} negative provenance has invalid case IDs")
        if (
            not isinstance(row.get("case_kind"), str)
            or row.get("supported") is not True
            or row.get("unsupported_reason") is not None
            or type(row.get("forbidden_hits")) is not int
            or row["forbidden_hits"] < 0
            or type(row.get("passed")) is not bool
            or row["passed"] != (row["forbidden_hits"] == 0)
        ):
            raise ValueError(f"{name} negative case {case_id} is unsupported or malformed")
        by_id[case_id] = row
    forbidden_hits = sum(row["forbidden_hits"] for row in rows)
    all_passed = all(row["passed"] for row in rows)
    if (
        negative.get("negative_case_count") != len(rows)
        or negative.get("negative_forbidden_hit_count") != forbidden_hits
        or negative.get("negative_forbidden_hit_rate") != forbidden_hits / len(rows)
        or negative.get("negative_unsupported_count") != 0
        or negative.get("negative_promotion_eligible") is not all_passed
    ):
        raise ValueError(f"{name} negative provenance aggregates are invalid")
    return by_id


def _validate_negative_admission(
    mem_prov: dict,
    syn_prov: dict,
    mem_prov_bytes: bytes,
    syn_prov_bytes: bytes,
    mem_reader: dict,
    syn_reader: dict,
) -> dict:
    mem_rows = _negative_rows(mem_prov, name="memphant")
    syn_rows = _negative_rows(syn_prov, name="syndai")
    if set(mem_rows) != set(syn_rows) or any(
        mem_rows[case_id]["case_kind"] != syn_rows[case_id]["case_kind"]
        for case_id in mem_rows
    ):
        raise ValueError("negative provenance case IDs or kinds differ")
    pairs = gc._RUN_READER.validate_and_pair_reports(mem_reader, syn_reader, "reader")
    if {case_id for case_id, _, _ in pairs} != set(mem_rows):
        raise ValueError("negative reader and provenance case IDs differ")
    _bind_negative_reader(mem_reader, mem_prov, mem_prov_bytes, name="memphant")
    _bind_negative_reader(syn_reader, syn_prov, syn_prov_bytes, name="syndai")
    paired_reader_rows = {}
    for case_id, mem_row, syn_row in pairs:
        if any(
            row.get("question_type") != mem_rows[case_id]["case_kind"]
            or row.get("is_abstention") is not True
            # A structured null still scores `abstention_exact` (free); a prose
            # refusal is now delegated to the judge as `abstention_llm_judge`
            # (spec 30 §7b). Both are valid abstention evaluations of a negative
            # case — accept either, reject anything else.
            or row.get("judge_method") not in ("abstention_exact", "abstention_llm_judge")
            or row.get("gold_answer") != "ABSTAIN"
            for row in (mem_row, syn_row)
        ):
            raise ValueError(f"negative case {case_id} is not an abstention evaluation")
        paired_reader_rows[case_id] = (mem_row, syn_row)

    def arm_summary(index: int, provenance_rows: dict[str, dict]) -> dict:
        retrieval_pass = {
            case_id for case_id, row in provenance_rows.items() if row["passed"]
        }
        reader_pass = {
            case_id
            for case_id, rows in paired_reader_rows.items()
            if rows[index]["correct"] is True
        }
        joint = retrieval_pass & reader_pass
        return {
            "retrieval_pass_count": len(retrieval_pass),
            "retrieval_pass_rate": len(retrieval_pass) / 10,
            "exact_abstention_count": len(reader_pass),
            "exact_abstention_rate": len(reader_pass) / 10,
            "joint_pass_count": len(joint),
            "joint_pass_rate": len(joint) / 10,
        }

    memphant = arm_summary(0, mem_rows)
    syndai = arm_summary(1, syn_rows)
    return {
        "n_paired": 10,
        "inference": "descriptive exact counts only; no bootstrap claim",
        "memphant": memphant,
        "syndai": syndai,
        "candidate_minus_incumbent": {
            field: memphant[field] - syndai[field]
            for field in (
                "retrieval_pass_count",
                "retrieval_pass_rate",
                "exact_abstention_count",
                "exact_abstention_rate",
                "joint_pass_count",
                "joint_pass_rate",
            )
        },
        "memphant_restraint_pass": memphant["joint_pass_count"] == 10,
    }


def _validate_shared_evidence_contract(memphant: dict, syndai: dict) -> None:
    for field in ("k", "budget_tokens", "evidence_packer"):
        if memphant["runtime_config"].get(field) != syndai["runtime_config"].get(field):
            raise ValueError(f"provenance shared evidence {field} differs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memphant-provenance", required=True)
    parser.add_argument("--syndai-provenance", required=True)
    parser.add_argument("--memphant-reader", help="run_reader QA report for MemPhant")
    parser.add_argument("--syndai-reader", help="run_reader QA report for Syndai")
    parser.add_argument("--memphant-negative-reader", help="run_reader abstention report for MemPhant")
    parser.add_argument("--syndai-negative-reader", help="run_reader abstention report for Syndai")
    parser.add_argument("--golden", required=True, help="pinned golden JSONL used for source-document clusters")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=20260711)
    args = parser.parse_args()

    mem_prov, mem_prov_bytes = _load(args.memphant_provenance)
    syn_prov, syn_prov_bytes = _load(args.syndai_provenance)

    result: dict = {
        "gate": "syndai_docs_engine_vs_engine",
        "seed": args.seed,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "retrieval": {},
        "qa": None,
        "negative": None,
        "verdict": None,
    }

    try:
        mem_rows = gc.validate_provenance_report(mem_prov)
        syn_rows = gc.validate_provenance_report(syn_prov)
        golden_revision, clusters = gc.golden_source_clusters(Path(args.golden))
        if mem_prov["golden_revision"] != golden_revision or syn_prov["golden_revision"] != golden_revision:
            raise ValueError("provenance does not match pinned golden revision")
        if set(mem_rows) != set(syn_rows) or set(mem_rows) != set(clusters):
            raise ValueError("provenance and pinned golden question IDs differ")
        if mem_prov["corpus_revision"] != syn_prov["corpus_revision"]:
            raise ValueError("provenance corpus revisions differ")
        _validate_shared_evidence_contract(mem_prov, syn_prov)
        prov_pairs = [(qid, mem_rows[qid], syn_rows[qid]) for qid in sorted(mem_rows)]
        if not all(
            isinstance(provenance.get("negative"), dict)
            for provenance in (mem_prov, syn_prov)
        ):
            raise ValueError("both negative provenance reports are required")
        if not args.memphant_negative_reader or not args.syndai_negative_reader:
            raise ValueError("both negative reader reports are required")
        mem_negative_reader, mem_negative_bytes = _load(args.memphant_negative_reader)
        syn_negative_reader, syn_negative_bytes = _load(args.syndai_negative_reader)
        negative_reader_bytes = [mem_negative_bytes, syn_negative_bytes]
        negative_summary = _validate_negative_admission(
            mem_prov,
            syn_prov,
            mem_prov_bytes,
            syn_prov_bytes,
            mem_negative_reader,
            syn_negative_reader,
        )
    except ValueError as error:
        result["verdict"] = {
            "rule": "Only complete, exactly paired reports may produce a verdict",
            "memphant_beats_syndai": None,
            "decision": f"HOLD/INVALID: {error}",
        }
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
        return 1

    # Retrieval (provenance) comparison.
    prov_deltas = [0] * len(prov_pairs)
    result["retrieval"] = {
        "memphant": {
            "recall_at_5": mem_prov["recall_at_5"],
            "recall_at_10": mem_prov["recall_at_10"],
            "haystack_sections": mem_prov.get("haystack_sections"),
        },
        "syndai": {
            "recall_at_5": syn_prov["recall_at_5"],
            "recall_at_10": syn_prov["recall_at_10"],
            "haystack_sections": syn_prov.get("haystack_sections"),
        },
        "n_paired": len(prov_deltas),
        "n_source_document_clusters": len(set(clusters.values())),
        "cluster_source": "pinned_golden.provenance[].file",
        "delta_hit_at_10": _clustered_ci(prov_pairs, clusters, "hit_at_10", args.seed),
    }
    result["negative"] = negative_summary

    # QA comparison (the binding verdict).
    if args.memphant_reader and args.syndai_reader:
        mem_reader, mem_reader_bytes = _load(args.memphant_reader)
        syn_reader, syn_reader_bytes = _load(args.syndai_reader)
        try:
            qa_pairs = gc._RUN_READER.validate_and_pair_reports(
                mem_reader, syn_reader, "reader"
            )
            if {qid for qid, _, _ in qa_pairs} != {qid for qid, _, _ in prov_pairs}:
                raise ValueError("reader and provenance question IDs differ")
            _bind_reader(mem_reader, mem_prov, mem_prov_bytes, name="memphant")
            _bind_reader(syn_reader, syn_prov, syn_prov_bytes, name="syndai")
        except ValueError as error:
            result["verdict"] = {
                "rule": "Only complete, exactly paired reports may produce a verdict",
                "memphant_beats_syndai": None,
                "decision": f"HOLD/INVALID: {error}",
            }
            Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
            return 1
        qa_deltas = [0] * len(qa_pairs)
        qa_ci = _clustered_ci(qa_pairs, clusters, "correct", args.seed + 1)
        qa_beats = bool(qa_ci["ci_excludes_zero"] and qa_ci["mean"] > 0)
        beats = qa_beats and negative_summary["memphant_restraint_pass"]
        mem_accuracy = sum(row["correct"] for _, row, _ in qa_pairs) / len(qa_pairs)
        syn_accuracy = sum(row["correct"] for _, _, row in qa_pairs) / len(qa_pairs)
        result["qa"] = {
            "memphant_accuracy": mem_accuracy,
            "syndai_accuracy": syn_accuracy,
            "memphant_n_scored": len(qa_pairs),
            "syndai_n_scored": len(qa_pairs),
            "n_paired": len(qa_deltas),
            "n_source_document_clusters": len(set(clusters.values())),
            "delta_qa_accuracy": qa_ci,
            "reader_model": mem_reader.get("reader_model_id"),
            "judge_model": mem_reader.get("judge_model_id"),
        }
        result["artifact_bindings"] = {
            "memphant_provenance_sha256": gc.sha256_hex(mem_prov_bytes),
            "syndai_provenance_sha256": gc.sha256_hex(syn_prov_bytes),
            "memphant_reader_sha256": gc.sha256_hex(mem_reader_bytes),
            "syndai_reader_sha256": gc.sha256_hex(syn_reader_bytes),
            "golden_sha256": golden_revision.removeprefix("sha256:"),
        }
        result["artifact_bindings"].update(
            memphant_negative_reader_sha256=gc.sha256_hex(negative_reader_bytes[0]),
            syndai_negative_reader_sha256=gc.sha256_hex(negative_reader_bytes[1]),
        )
        result["verdict"] = {
            "rule": (
                "MemPhant proceeds iff the paired QA-accuracy bootstrap CI "
                "(MemPhant - Syndai) excludes zero and is positive, and MemPhant "
                "passes retrieval restraint plus exact abstention on all 10 negative cases"
            ),
            "memphant_beats_syndai": beats,
            "decision": (
                "PROCEED: MemPhant beats Syndai's stack on the golden set"
                if beats
                else "HOLD: positive QA superiority or candidate restraint is not proven"
            ),
        }
    else:
        result["verdict"] = {
            "rule": "QA reader reports required for the binding verdict",
            "memphant_beats_syndai": None,
            "decision": "HOLD/INVALID: both QA reader reports are required",
        }

    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")

    ret = result["retrieval"]
    print(
        f"RETRIEVAL  memphant R@5={ret['memphant']['recall_at_5']:.3f} "
        f"R@10={ret['memphant']['recall_at_10']:.3f} | "
        f"syndai R@5={ret['syndai']['recall_at_5']:.3f} "
        f"R@10={ret['syndai']['recall_at_10']:.3f} | "
        f"Δhit@10 mean={ret['delta_hit_at_10']['mean']:+.3f} "
        f"CI=[{ret['delta_hit_at_10']['ci95_low']:+.3f},"
        f"{ret['delta_hit_at_10']['ci95_high']:+.3f}]",
        file=sys.stderr,
    )
    if result["qa"]:
        qa = result["qa"]
        print(
            f"QA         memphant={qa['memphant_accuracy']} "
            f"syndai={qa['syndai_accuracy']} | "
            f"Δacc mean={qa['delta_qa_accuracy']['mean']:+.3f} "
            f"CI=[{qa['delta_qa_accuracy']['ci95_low']:+.3f},"
            f"{qa['delta_qa_accuracy']['ci95_high']:+.3f}] "
            f"excl_zero={qa['delta_qa_accuracy']['ci_excludes_zero']}",
            file=sys.stderr,
        )
        print(f"VERDICT    {result['verdict']['decision']}", file=sys.stderr)
    return 0 if result["qa"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
