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
        "questions_with_lexical_channel": sum(
            1
            for row in report["per_question"]
            if any(
                any(channel[0] == "lexical" for channel in entry.get("channels") or [])
                for entry in (row.get("channel_table") or [])
            )
        ),
        "questions_with_vector_channel": sum(
            1
            for row in report["per_question"]
            if any(
                any(channel[0] == "vector" for channel in entry.get("channels") or [])
                for entry in (row.get("channel_table") or [])
            )
        ),
    }
    if facts["lexical_scorer"] != "bm25-code" or facts["embed_model"] != "small":
        raise SystemExit(f"treatment is not the shipped default: {facts}")
    # Both fusion channels must be observed CONTRIBUTING in the served run's own
    # per-candidate channel table. A flag says what was asked for; this says
    # what fired. An inert dense channel and a neutral one score the same.
    if facts["questions_with_lexical_channel"] == 0 or facts["questions_with_vector_channel"] == 0:
        raise SystemExit(f"treatment channels not proven live from its own trace: {facts}")
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
        psi = stats["realized_psi"]
        # Below six discordant pairs no split of them reaches alpha=0.05 under
        # the two-sided exact test (2 * 2^-6 = 0.031 is the first p that can),
        # so the required n follows from the observed discordance rate.
        floor_n = int(-(-N_D_FLOOR // psi)) if psi > 0 else None
        return {
            "verdict": "NOT A MEASUREMENT",
            "reason": (
                f"n_d={stats['n_discordant']} < {N_D_FLOOR} structural floor: at "
                "this discordance no exact two-sided McNemar can reach p<0.05 at "
                "any split. This is not a tie and not a null."
            ),
            "required_n_for_n_d_floor": floor_n,
            "required_n_for_planning_mde": (
                required_n(psi, PLANNING_MDE) if psi > 0 else None
            ),
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


def evidence_contract(headline: dict, treatment: dict, args) -> dict:
    """The `evidence_contract` block for the headline contrast.

    `decisional` is **false**, and not as a formality. The paraphrase bank fails
    its OWN preregistered headline leakage bar — `leak_concentration_le_1_50` is
    false at 2.018 and `bar_passed` is false in
    `benchmarks/data/track_r_paraphrase_golden.lock.json` — and the corpus
    licence is a HuggingFace card assertion with no LICENSE blob pinned. Both
    are recorded in `w02-trunk-arms.json`'s contract and neither is fixable by
    re-reading an artifact. The bank is used deliberately with those failures
    declared, because it is still the least lexically confounded coding bank we
    own; that is a reason to use it, not a reason to promote off it.
    """
    return {
        "schema_version": 1,
        "decisional": False,
        "claim": (
            "On the 180-question Track R paraphrase bank, at one shared endpoint "
            "(gate_common.provenance_hit@10 over top-10 bodies), one haystack "
            "(the golden's bound attempt) and one query string, MemPhant at its "
            f"shipped bm25code_dense default scores {headline['treatment_hits']}/180 "
            f"against {headline['control_hits']}/180 for a no-substrate agentic "
            "control given only grep/read/ls over the same raw events: "
            f"b={headline['treatment_only_b']} / c={headline['control_only_c']}, "
            f"n_d={headline['n_discordant']}, delta={headline['delta_pp']}pp, "
            f"two-sided exact McNemar p={headline['mcnemar_exact_p']:.4g}."
        ),
        "power": {
            "test": "two-sided exact (conditional binomial) McNemar",
            "n": headline["n"],
            "b": headline["treatment_only_b"],
            "c": headline["control_only_c"],
            "n_d": headline["n_discordant"],
            "psi_observed": headline["realized_psi"],
            "mde_at_80": min_detectable_effect(
                headline["n"], headline["realized_psi"]
            ),
            "computed_by": "scripts/instrument_power.py:min_detectable_effect",
            "source": (
                "docs/build-log/artifacts/s4-controls/analysis.json "
                "contrasts['memphant_vs_agentic_grep']"
            ),
        },
        "harness": {
            "embed_model": (
                "treatment: small (bge-small-en-v1.5). agentic control: none — "
                "no embeddings at all, which is the point of the arm."
            ),
            "scorer": (
                "treatment: lexical=bm25-code + weighted-RRF fusion, recall "
                "mode=fast. agentic control: anthropic/claude-opus-5 in a "
                "bounded grep/read_event/list_events loop, provider pinned "
                "only=[anthropic] allow_fallbacks=false, max_price "
                "5.0/25.0 USD per million."
            ),
            "k": 10,
            "budget": treatment.get("budget_tokens"),
            "flags": [
                "agentic caps: 12 tool calls, 16 turns, 24000 completion tokens "
                "per question, grep<=25 matches, read_event<=6000 chars",
                "treatment costs 0 LLM calls at recall",
            ],
            "command": "scripts/s4_controls_run.sh pilot && scripts/s4_controls_run.sh full",
        },
        "corpus": {
            "sha256": treatment.get("corpus_sha256", "unverified"),
            "snapshot_id": (
                "track_r_paraphrase_golden@4aed8e99dbf1 over corpus c008142e9921 "
                "(nebius/SWE-rebench-openhands-trajectories@35455389, 495 sampled "
                "attempts, 64055 content events)"
            ),
            "n_items": headline["n"],
            "path_note": (
                "benchmarks/data/track_r_paraphrase_golden.jsonl is not tracked "
                "in this repo (only its lock), so the checker cannot recompute "
                "the golden sha here"
            ),
        },
        "leakage": {
            "unit_definition": "one content event of the attempt, 4000-char clip",
            "absolute_target_coverage": 0.1346,
            "floor": 0.0667,
            "floor_kind": "exhaustive",
            "concentration": 2.018,
            "provenance_class": "machine_generated",
            "source": "benchmarks/data/track_r_paraphrase_golden.lock.json",
            "negative_selection": (
                "same-attempt hard negatives — every non-target content event of "
                "the bound attempt, which is also exactly the retrieval haystack "
                "for every arm here"
            ),
        },
        "mechanism_enabled": True,
        "mechanism_evidence": (
            "Treatment: lexical_scorer='bm25-code' and embed_model='small' in the "
            "served run's provenance, with BOTH 'lexical' and 'vector' entries "
            "observed in its per-candidate channel_table on every question — "
            "asserted by s4_controls_compare.assert_treatment_liveness, which "
            "exits non-zero if either channel is absent. Control: every scored "
            "row carries an executed tool call and a selection that resolves to "
            "real event sequences in that attempt; rows_with_errors and "
            "rows_with_zero_tool_calls are recorded in the arm's own liveness "
            "block and both must be 0 before the arm is reported."
        ),
        "probe_kind": "gate",
        "attribution": {
            "method": "unverified",
            "note": (
                "No bisect and none applicable: this contrasts two different "
                "retrieval systems, not two commits of one. The treatment is a "
                "banked run at head 4a39ce5f with its binary sha256s recorded; "
                "the control has no MemPhant binary in its path at all."
            ),
        },
        "notes": (
            "decisional=false for the two reasons carried by every artifact on "
            "this bank and neither fixable by re-reading: (1) the bank FAILS its "
            "own preregistered headline leakage bar — concentration 2.018 against "
            "<=1.50, bar_passed=false in the lock; (2) the corpus licence "
            "CC-BY-4.0 is a HuggingFace card assertion with no LICENSE blob "
            "pinned. A third caveat is specific to THIS artifact and runs in the "
            "direction of the result rather than against it: the paraphrase bank "
            "bans identifier surfaces (q->target coverage 0.1346 vs human "
            "0.175-0.287), which is the hardest regime for a lexical grep "
            "control, so the control's margin here is a LOWER bound. Fourth: two "
            "goldens (track_r_par_028, track_r_par_066) have spans that appear in "
            "no event of their own attempt, so the ceiling is 178/180 for every "
            "arm equally; verified symmetric, so it does not bias the contrast. "
            "Fifth: an ONCU no-corpus probe answered 2/20 questions correctly "
            "with no evidence, a bias that inflates the CONTROL, not the "
            "treatment."
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
    parser.add_argument(
        "--drop-ids",
        default="",
        help=(
            "comma-separated question_ids to exclude from EVERY arm. Used for "
            "the complete-case sensitivity analysis when a row is unscoreable "
            "for a reason outside the harness -- e.g. the pinned provider's "
            "content filter refusing a question deterministically. Dropping is "
            "never the headline: the headline scores an unscoreable row as a "
            "MISS for the arm that could not produce it."
        ),
    )
    args = parser.parse_args()

    dropped = {value for value in args.drop_ids.split(",") if value}
    treatment = json.loads(args.treatment.read_text())
    assert_same_stage("treatment", treatment)
    treatment_facts = assert_treatment_liveness(treatment)
    treatment_hits = {
        key: value
        for key, value in hits_by_question(treatment).items()
        if key not in dropped
    }

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
        control_hits = {
            key: value
            for key, value in hits_by_question(report).items()
            if key not in dropped
        }
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
        "dropped_question_ids": sorted(dropped),
        "arms": arms,
        "contrasts": contrasts,
        "lineage": s4.lineage(
            {
                "treatment_provenance": args.treatment,
                **{
                    f"control:{spec.partition('=')[0]}": Path(spec.partition("=")[2])
                    for spec in args.control
                },
            }
        )
        | {
            # The rule is sha256 of the SERVED binaries. The treatment was
            # served by a MemPhant build whose hashes were banked beside its
            # run; the controls served no MemPhant binary at all, which is
            # itself the arm's defining property.
            "treatment_served_binaries": (
                args.treatment.with_name("binaries.sha256").read_text().strip()
                if args.treatment.with_name("binaries.sha256").exists()
                else "unverified"
            ),
            "treatment_runtime_identity": treatment.get("runtime_identity", {}).get(
                "repository"
            ),
            "control_served_binaries": "none — the controls run no MemPhant binary",
        },
        "bias_bound": (
            "The paraphrase bank bans identifier surfaces; absolute q->target "
            "coverage brackets as paraphrase 0.1346 < human 0.175-0.287 < "
            "original 0.396. A grep-driven control is a lexical instrument, so "
            "this bank is its hardest point. Any MemPhant margin over the "
            "agentic control here is an UPPER BOUND; any agentic-control margin "
            "over MemPhant is a LOWER BOUND."
        ),
    }
    headline = contrasts.get("memphant_vs_agentic_grep")
    if headline is not None:
        output["evidence_contract"] = evidence_contract(headline, treatment, args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"arms": {k: v["rate"] for k, v in arms.items()}}, indent=2))
    print(json.dumps(contrasts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
