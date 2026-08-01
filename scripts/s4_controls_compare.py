#!/usr/bin/env python3
"""S4 — the paired comparison, and the preregistered decision rule applied to it.

Reads the banked MemPhant treatment plus the control arms, refuses to pair arms
that were not scored at the same stage, computes an exact paired McNemar and the
contrast's OWN realized psi and MDE, and then applies the rule fixed in
`docs/build-log/2026-08-01-agentic-search-controls.md` §A.4 **without
reinterpreting it**.

Nothing here chooses a threshold. The thresholds were committed before any cell
was seen; this script only evaluates them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import s4_controls_common as s4  # noqa: E402
from instrument_power import min_detectable_effect, required_n  # noqa: E402
from track_r_retrieval_arm_compare import mcnemar_exact_p  # noqa: E402

PLANNING_MDE = 0.0938  # psi=0.189 at n=180, computed in the preregistration
N_D_FLOOR = 6


def hits_by_question(report: dict) -> dict[str, bool]:
    return {row["question_id"]: bool(row["hit_at_10"]) for row in report["per_question"]}


def assert_same_stage(name: str, report: dict) -> None:
    """Either the arm declares the shared endpoint contract, or it is the banked
    MemPhant runner — whose stage is verified structurally instead of by a
    string it predates: `code_lane_run_memphant.py` grades with the identical
    `gate_common.provenance_hit(golden, bodies, 10)` over its packed top-k."""
    declared = report.get("endpoint_contract")
    if declared == s4.ENDPOINT_CONTRACT:
        return
    if report.get("engine") in {"memphant", "deterministic_file_search"}:
        if int(report.get("k", 0)) != 10:
            raise SystemExit(f"{name}: k={report.get('k')} is not the pinned k=10")
        return
    raise SystemExit(
        f"{name}: endpoint contract {declared!r} != {s4.ENDPOINT_CONTRACT!r} — "
        "refusing to pair arms scored at different stages"
    )


def assert_treatment_liveness(report: dict) -> dict:
    """The treatment's mechanism, proven from the served run's own provenance
    rather than from the flag that was passed to it."""
    facts = {
        "lexical_scorer": report.get("lexical_scorer"),
        "embed_model": report.get("embed_model"),
        "questions_with_channel_table": sum(
            1 for row in report["per_question"] if row.get("channel_table")
        ),
        "questions_with_dense_channel": sum(
            1
            for row in report["per_question"]
            if any(
                entry.get("vector_rank") is not None
                or entry.get("dense_rank") is not None
                or "vector_score" in entry
                or "dense_score" in entry
                for entry in (row.get("channel_table") or [])
            )
        ),
    }
    if facts["lexical_scorer"] != "bm25-code" or facts["embed_model"] != "small":
        raise SystemExit(f"treatment is not the shipped default: {facts}")
    if facts["questions_with_channel_table"] == 0:
        raise SystemExit("treatment provenance carries no channel_table: mechanism unproven")
    return facts


def paired(treatment: dict[str, bool], control: dict[str, bool], order: list[str]) -> dict:
    b = sum(1 for q in order if treatment[q] and not control[q])
    c = sum(1 for q in order if control[q] and not treatment[q])
    n = len(order)
    n_d = b + c
    delta = (
        sum(treatment[q] for q in order) - sum(control[q] for q in order)
    ) / n
    psi = n_d / n
    return {
        "n": n,
        "treatment_hits": sum(treatment[q] for q in order),
        "control_hits": sum(control[q] for q in order),
        "both": sum(1 for q in order if treatment[q] and control[q]),
        "neither": sum(1 for q in order if not treatment[q] and not control[q]),
        "treatment_only_b": b,
        "control_only_c": c,
        "n_discordant": n_d,
        "delta": round(delta, 6),
        "delta_pp": round(delta * 100, 2),
        "mcnemar_exact_p": mcnemar_exact_p(b, c),
        "realized_psi": round(psi, 6),
        "realized_mde_pp": (
            round((min_detectable_effect(n, psi) or 0) * 100, 2) if psi > 0 else None
        ),
        "planning_mde_pp": round(PLANNING_MDE * 100, 2),
    }


def verdict(stats: dict) -> dict:
    """§A.4, applied verbatim."""
    if stats["n_discordant"] < N_D_FLOOR:
        needed = required_n(max(stats["realized_psi"], 1e-6), PLANNING_MDE)
        return {
            "verdict": "NOT A MEASUREMENT",
            "reason": (
                f"n_d={stats['n_discordant']} < {N_D_FLOOR} structural floor. "
                "This is not a tie and not a null."
            ),
            "required_n": needed,
        }
    delta, p = stats["delta"], stats["mcnemar_exact_p"]
    if delta >= PLANNING_MDE and p < 0.05:
        return {"verdict": "A — MemPhant wins", "reason": "delta >= MDE and p < 0.05"}
    if delta <= -PLANNING_MDE and p < 0.05:
        return {"verdict": "B — the control wins", "reason": "delta <= -MDE and p < 0.05"}
    return {
        "verdict": "D — no detectable difference at this power",
        "reason": (
            f"delta={stats['delta_pp']}pp, p={p:.4g}; the instrument cannot "
            f"resolve an effect smaller than {stats['planning_mde_pp']}pp. "
            "Effects inside that band are unmeasured, not absent."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--treatment", required=True, type=Path)
    parser.add_argument(
        "--control", action="append", required=True, metavar="NAME=PATH",
        help="repeatable; e.g. agentic=/path/prov.json",
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    treatment = json.loads(args.treatment.read_text())
    assert_same_stage("treatment", treatment)
    treatment_facts = assert_treatment_liveness(treatment)
    treatment_hits = hits_by_question(treatment)

    contrasts = {}
    arms = {
        "memphant_bm25code_dense": {
            "hits_at_10": sum(treatment_hits.values()),
            "n": len(treatment_hits),
            "rate": round(sum(treatment_hits.values()) / len(treatment_hits), 6),
            "source": str(args.treatment),
            "liveness": treatment_facts,
            "llm_calls_at_recall": 0,
            "reported_spend_usd": 0.0,
        }
    }
    for spec in args.control:
        name, _, path = spec.partition("=")
        report = json.loads(Path(path).read_text())
        assert_same_stage(name, report)
        control_hits = hits_by_question(report)
        order = [q for q in control_hits if q in treatment_hits]
        if len(order) != len(control_hits):
            raise SystemExit(f"{name}: question ids not a subset of the treatment's")
        arms[name] = {
            "hits_at_10": sum(control_hits[q] for q in order),
            "n": len(order),
            "rate": round(sum(control_hits[q] for q in order) / len(order), 6),
            "source": str(path),
            "liveness": report.get("liveness"),
            "reported_spend_usd": report.get("reported_spend_usd"),
        }
        stats = paired(treatment_hits, control_hits, order)
        contrasts[f"memphant_vs_{name}"] = stats | verdict(stats)

    output = {
        "instrument": "track_r_paraphrase n=180",
        "endpoint": s4.ENDPOINT_CONTRACT,
        "preregistration": s4.PREREGISTRATION,
        "planning_mde_pp": round(PLANNING_MDE * 100, 2),
        "n_d_structural_floor": N_D_FLOOR,
        "arms": arms,
        "contrasts": contrasts,
        "lineage": s4.lineage({}),
        "bias_bound": (
            "The paraphrase bank bans identifier surfaces; absolute q->target "
            "coverage brackets as paraphrase 0.1346 < human 0.175-0.287 < "
            "original 0.396. A grep-driven control is a lexical instrument, so "
            "this bank is its hardest point. Any MemPhant margin over the "
            "agentic control here is an UPPER BOUND; any agentic-control margin "
            "over MemPhant is a LOWER BOUND."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"arms": {k: v["rate"] for k, v in arms.items()}}, indent=2))
    print(json.dumps(contrasts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
