#!/usr/bin/env python3
"""S8 — the sweep analysis, and the preregistered decision rule applied to it.

Reads every Arm H run, the retriever comparators from this lane's own pool dump,
and S4's banked agentic `grep` arm; refuses to pair arms scored at different
stages; computes each contrast's exact paired McNemar and its OWN realized psi
and MDE; and reports every N against **its own** coverage ceiling, because a
sweep point cannot be called a failure for missing a ceiling it never had.

Nothing here chooses a threshold. The thresholds were committed before any cell
was seen (`docs/build-log/2026-08-01-hybrid-retrieve-then-rank.md` §A.7); this
script only evaluates them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gate_common as gc  # noqa: E402
import s8_hybrid_common as s8  # noqa: E402
from instrument_power import min_detectable_effect, required_n  # noqa: E402
from track_r_retrieval_arm_compare import mcnemar_exact_p  # noqa: E402

PLANNING_MDE = 0.0938  # psi=0.189 at n=180; S4 computed it for this bank
N_D_FLOOR = 6
# S4's measured agentic-grep cost, reused rather than re-run: $25.9318 over 180.
GREP_USD_PER_QUESTION = 0.144


def hits_by_question(report: dict) -> dict[str, bool]:
    return {row["question_id"]: bool(row["hit_at_10"]) for row in report["per_question"]}


def assert_same_stage(name: str, report: dict) -> None:
    declared = report.get("endpoint_contract")
    if declared == s8.ENDPOINT_CONTRACT:
        return
    # The banked MemPhant/agentic runners predate the contract string; their
    # stage is verified structurally instead — both grade with the identical
    # gate_common.provenance_hit(golden, bodies, 10).
    if report.get("engine") in {"memphant", "deterministic_file_search"} or str(
        report.get("engine", "")
    ).startswith("agentic_"):
        if int(report.get("k", 0)) != 10:
            raise SystemExit(f"{name}: k={report.get('k')} is not the pinned k=10")
        return
    raise SystemExit(
        f"{name}: endpoint contract {declared!r} != {s8.ENDPOINT_CONTRACT!r} — "
        "refusing to pair arms scored at different stages"
    )


def assert_pool_containment(name: str, report: dict) -> None:
    """An Arm H report that cannot prove its agent stayed inside the pool is S4
    re-run under a different label, and pairing it would be a lie about what was
    varied."""
    liveness = report.get("liveness") or {}
    if liveness.get("pool_containment_violations") != 0:
        raise SystemExit(
            f"{name}: pool_containment_violations="
            f"{liveness.get('pool_containment_violations')!r} — refusing to report"
        )
    if liveness.get("raw_event_access") is not False:
        raise SystemExit(f"{name}: raw_event_access is not proven false")
    if liveness.get("rows_with_errors"):
        raise SystemExit(
            f"{name}: {liveness['rows_with_errors']} errored rows — errors are not "
            "results; re-run them before scoring"
        )


def paired(treatment: dict[str, bool], control: dict[str, bool], order: list[str]) -> dict:
    b = sum(1 for q in order if treatment[q] and not control[q])
    c = sum(1 for q in order if control[q] and not treatment[q])
    n = len(order)
    n_d = b + c
    delta = (sum(treatment[q] for q in order) - sum(control[q] for q in order)) / n
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


def verdict(stats: dict, *, decisive: bool) -> dict:
    """§A.7, applied verbatim. `decisive=False` marks a stage-2 sweep contrast,
    which selects confirmation points and decides nothing."""
    if not decisive:
        base = {
            "verdict": "NOT A MEASUREMENT — stage-2 sweep, reduced n",
            "reason": (
                "the coarse sweep locates the knee; it does not test a hypothesis"
            ),
        }
    elif stats["n_discordant"] < N_D_FLOOR:
        # `required_n(psi, delta)` -- in that order. S4's compare script called
        # it with the arguments transposed, which both asks the wrong question
        # and walks the exact-power search to its 20,000 cap.
        needed = (
            required_n(stats["realized_psi"], PLANNING_MDE)
            if stats["realized_psi"] > 0
            else None
        )
        base = {
            "verdict": "NOT A MEASUREMENT",
            "reason": (
                f"n_discordant={stats['n_discordant']} < {N_D_FLOOR}; required n "
                + (
                    f"~= {needed} to detect the {round(PLANNING_MDE * 100, 2)}pp "
                    "planning effect at this discordance rate"
                    if needed
                    else (
                        f"is unreachable: the realized discordance "
                        f"{stats['realized_psi']} cannot express a "
                        f"{round(PLANNING_MDE * 100, 2)}pp effect at any n, so any "
                        "true effect is smaller than this contrast can resolve"
                    )
                )
            ),
        }
    else:
        mde = (stats["realized_mde_pp"] or PLANNING_MDE * 100) / 100
        p = stats["mcnemar_exact_p"]
        if stats["delta"] >= mde and p < 0.05:
            base = {"verdict": "H wins", "reason": "delta >= realized MDE and p < 0.05"}
        elif stats["delta"] <= -mde and p < 0.05:
            base = {
                "verdict": "the comparator wins",
                "reason": "delta <= -realized MDE and p < 0.05",
            }
        else:
            base = {
                "verdict": "no detectable difference at this power",
                "reason": (
                    f"the instrument cannot resolve an effect smaller than "
                    f"{stats['realized_mde_pp']}pp; effects inside that band are "
                    "unmeasured, not absent"
                ),
            }
    return stats | base


def retriever_arms(pool_dump: Path, golden: Path, order: list[str]) -> dict:
    """MemPhant's own two comparators, rebuilt from THIS lane's pool dump so
    they share the run, the head and the served binaries with Arm H.

    * `memphant_fused_at_10` — the first ten pool bodies, unpacked. Arm H's
      stage-matched comparator: both return plain ranked bodies.
    * `memphant_packed_at_10` — the shipped default's packed top ten. Reported
      beside it, never the paired headline against H.
    """
    goldens = {
        row["question_id"]: row
        for row in (json.loads(line) for line in Path(golden).read_text().splitlines() if line.strip())
    }
    pools = s8.load_pool_dump(pool_dump)
    fused: dict[str, bool] = {}
    packed: dict[str, bool] = {}
    coverage: dict[str, list[int]] = {}
    for question_id in order:
        golden_row = goldens[question_id]
        pool_row = pools[question_id]
        bodies = [item["body"] for item in pool_row["pool"][:10]]
        fused[question_id] = gc.provenance_hit(golden_row, bodies, 10)
        packed[question_id] = gc.provenance_hit(golden_row, pool_row["packed_bodies"], 10)
        coverage[question_id] = [
            index for index, item in enumerate(pool_row["pool"], start=1) if item["is_gold"]
        ]
    return {"fused": fused, "packed": packed, "gold_pool_ranks": coverage}


def coverage_at(gold_ranks: dict[str, list[int]], order: list[str], n: int) -> int:
    """How many questions have a gold-bearing unit inside the retriever's first
    ``n``. This is Arm H's ceiling at N=n, computed with no agent and no spend."""
    return sum(1 for q in order if any(rank <= n for rank in gold_ranks[q]))


def evidence_contract(headline_row: dict, pool_report: dict) -> dict:
    """The `evidence_contract` block for the headline contrast, generated here
    and never hand-edited.

    `decisional` is **false**, and not as a formality: the paraphrase bank fails
    its OWN preregistered headline leakage bar (`leak_concentration_le_1_50`
    false at 2.018, `bar_passed` false in
    `benchmarks/data/track_r_paraphrase_golden.lock.json`) and its corpus licence
    is a HuggingFace card assertion with no LICENSE blob pinned. The bank is used
    deliberately with those failures declared, because it is still the least
    lexically confounded coding bank we own — a reason to use it, not to promote
    off it.
    """
    headline = headline_row["contrasts"]["vs_memphant_fused_at_10"]
    return {
        "schema_version": 1,
        "decisional": False,
        "claim": (
            f"On the Track R paraphrase bank (n={headline['n']}), at one shared "
            "endpoint (gate_common.provenance_hit@10 over top-10 bodies) and one "
            "query string, handing anthropic/claude-opus-5 MemPhant's first "
            f"N={headline_row['N']} fused candidates AND NOTHING ELSE to re-rank "
            f"scores {headline['treatment_hits']}/{headline['n']} against "
            f"{headline['control_hits']}/{headline['n']} for MemPhant's own "
            f"fused top-10 on the same pool: b={headline['treatment_only_b']} / "
            f"c={headline['control_only_c']}, n_d={headline['n_discordant']}, "
            f"delta={headline['delta_pp']}pp, two-sided exact McNemar "
            f"p={headline['mcnemar_exact_p']:.4g}. The arm's ceiling at that N is "
            f"Coverage(N)={headline_row['coverage_ceiling_hits']}/{headline['n']}, "
            f"so its ranking accuracy is {headline_row['rank_accuracy']}, at "
            f"${headline_row['usd_per_question']}/question against the agentic "
            f"grep control's measured ${GREP_USD_PER_QUESTION}/question."
        ),
        "power": {
            "test": "two-sided exact (conditional binomial) McNemar",
            "n": headline["n"],
            "b": headline["treatment_only_b"],
            "c": headline["control_only_c"],
            "n_d": headline["n_discordant"],
            "psi_observed": headline["realized_psi"],
            "mde_at_80": min_detectable_effect(headline["n"], headline["realized_psi"]),
            "computed_by": "scripts/instrument_power.py:min_detectable_effect",
            "source": (
                "docs/build-log/artifacts/s8-hybrid/analysis-confirm.json "
                "sweep[].contrasts['vs_memphant_fused_at_10']"
            ),
        },
        "harness": {
            "embed_model": (
                "small (bge-small-en-v1.5) — the retriever half of the hybrid; the "
                "ranking half uses no embeddings at all"
            ),
            "scorer": (
                "retriever: lexical=bm25-code + weighted-RRF fusion, recall "
                "mode=fast, its pool banked with bodies via --dump-pool. ranker: "
                "anthropic/claude-opus-5 in a bounded list_pool/grep_pool/"
                "read_item loop over that pool ONLY, provider pinned "
                "only=[anthropic] allow_fallbacks=false, max_price 5.0/25.0 USD "
                "per million."
            ),
            "k": 10,
            "budget": pool_report.get("budget_tokens"),
            "flags": [
                f"pool depth N={headline_row['N']}",
                "ranker caps identical to S4's agentic control: 12 tool calls, 16 "
                "turns, 24000 completion tokens per question, grep<=25 matches, "
                "read_item<=6000 chars",
                "pool containment asserted per returned body; the runner refuses "
                "to score on any mismatch",
            ],
            "command": (
                "scripts/s8_hybrid_run.sh pool && scripts/s8_hybrid_run.sh stub && "
                "scripts/s8_hybrid_run.sh sweep N && scripts/s8_hybrid_run.sh confirm N"
            ),
        },
        "corpus": {
            "sha256": pool_report.get("corpus_sha256", "unverified"),
            "snapshot_id": (
                "track_r_paraphrase_golden@4aed8e99dbf1 over corpus c008142e9921 "
                "(nebius/SWE-rebench-openhands-trajectories@35455389, 495 sampled "
                "attempts, 64055 content events)"
            ),
            "n_items": headline["n"],
            "path_note": (
                "benchmarks/data/track_r_paraphrase_golden.jsonl is not tracked in "
                "this repo (only its lock), so the checker cannot recompute the "
                "golden sha here"
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
                "same-attempt hard negatives — for this arm, literally the other "
                "N-1 candidates MemPhant's own fusion ranked alongside the gold"
            ),
        },
        "mechanism_enabled": True,
        "mechanism_evidence": (
            "Retriever half: lexical_scorer='bm25-code' and embed_model='small' in "
            "the pool run's own provenance. Ranker half: every scored row carries "
            "an executed tool call and a selection that resolves inside the pool; "
            "pool_containment_violations=0 is asserted by matching EVERY returned "
            "body against the exact set that question's agent was handed, and the "
            "runner exits non-zero rather than writing a score on a mismatch. "
            "rows_with_errors must be 0 before the arm is reported."
        ),
        "probe_kind": "gate",
        "attribution": {
            "method": "unverified",
            "note": (
                "No bisect and none applicable: this contrasts two ways of cutting "
                "ten items out of ONE retrieval pool — the shipped fuser's rank "
                "order against an agent's judgement — not two commits of one "
                "system. Both arms read the same banked pool from the same run."
            ),
        },
        "notes": (
            "decisional=false for the two reasons carried by every artifact on "
            "this bank and neither fixable by re-reading: (1) the bank FAILS its "
            "own preregistered headline leakage bar — concentration 2.018 against "
            "<=1.50, bar_passed=false in the lock; (2) the corpus licence "
            "CC-BY-4.0 is a HuggingFace card assertion with no LICENSE blob "
            "pinned. Two further bounds are specific to THIS artifact and are "
            "stated ahead of its numbers rather than after them: the arm's ceiling "
            "at every N is Coverage(N), which tops out at 169/180=0.9389 — BELOW "
            "the agentic grep control's realized 174/180, so this arm cannot beat "
            "grep on this bank at any N and was never expected to; and the "
            "haystack is ONE coding attempt at a pool median of 122.5 units, so "
            "any operating point derived here is measured AT that haystack size "
            "and is not extrapolated to repo or cross-session scale, where "
            "enumeration has no analogue."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-dump", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="an Arm H provenance JSON; LABEL must end in the N it ran at",
    )
    parser.add_argument("--grep", type=Path, required=True, help="S4's banked agentic arm")
    parser.add_argument("--stage", choices=("sweep", "confirm"), required=True)
    parser.add_argument(
        "--headline-label",
        default=None,
        help="which arm carries the evidence contract at --stage confirm; "
        "defaults to the highest-scoring one",
    )
    parser.add_argument("--pool-provenance", type=Path, default=None)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    arms = {}
    for spec in args.arm:
        label, _, path = spec.partition("=")
        report = json.loads(Path(path).read_text())
        assert_same_stage(label, report)
        assert_pool_containment(label, report)
        arms[label] = {"path": Path(path), "report": report}
    if not arms:
        raise SystemExit("no arms given")

    grep_report = json.loads(args.grep.read_text())
    assert_same_stage("agentic_grep", grep_report)
    grep_hits = hits_by_question(grep_report)

    # The scored set is the intersection of every arm's questions: the sweep
    # subset at stage 2, the whole bank at stage 3. Pairing is only meaningful
    # over questions every arm actually ran.
    order = sorted(set.intersection(*(set(hits_by_question(a["report"])) for a in arms.values())))
    missing_grep = [q for q in order if q not in grep_hits]
    if missing_grep:
        raise SystemExit(f"grep arm is missing {len(missing_grep)} scored questions")

    retriever = retriever_arms(args.pool_dump, args.golden, order)
    gold_ranks = retriever["gold_pool_ranks"]
    pool_full = max(
        (max(ranks) for ranks in gold_ranks.values() if ranks), default=0
    )

    rows = []
    for label, entry in sorted(
        arms.items(), key=lambda item: int(item[1]["report"]["pool_depth_requested"] or 10**9)
    ):
        report = entry["report"]
        n_requested = int(report["pool_depth_requested"])
        hits = hits_by_question(report)
        ceiling = coverage_at(gold_ranks, order, n_requested if n_requested > 0 else 10**9)
        realized = report["hits_at_10"]
        decomposition = report["rank_decomposition"]
        usage = report["usage"]
        rows.append(
            {
                "label": label,
                "N": n_requested if n_requested > 0 else "all",
                "n": len(order),
                "hits_at_10": realized,
                "rate": round(realized / len(order), 6),
                "coverage_ceiling_hits": ceiling,
                "coverage_ceiling_rate": round(ceiling / len(order), 6),
                # THE curve nobody has: of the questions whose gold the retriever
                # actually handed over, what fraction did the agent rank into its
                # top ten?
                "rank_accuracy": round(realized / ceiling, 6) if ceiling else None,
                "misses_out_of_view": decomposition["out_of_view"],
                "misses_in_view_ranked_out": decomposition["in_view_but_ranked_out"],
                "mean_tool_calls": report["liveness"]["mean_tool_calls"],
                "prompt_tokens_per_question": usage["prompt_tokens_per_question"],
                "completion_tokens_per_question": usage["completion_tokens_per_question"],
                "usd_per_question": report["spend_usd_per_question"],
                "usd_per_question_vs_grep": round(
                    report["spend_usd_per_question"] / GREP_USD_PER_QUESTION, 4
                ),
                "hits_per_dollar": (
                    round(realized / (report["reported_spend_usd"] or 1e-9), 1)
                ),
                "contrasts": {
                    "vs_memphant_fused_at_10": verdict(
                        paired(hits, retriever["fused"], order),
                        decisive=args.stage == "confirm",
                    ),
                    "vs_memphant_packed_at_10": verdict(
                        paired(hits, retriever["packed"], order),
                        decisive=args.stage == "confirm",
                    ),
                    "vs_agentic_grep": verdict(
                        paired(hits, grep_hits, order), decisive=args.stage == "confirm"
                    ),
                },
            }
        )

    coverage_curve = {
        str(n): {
            "hits": coverage_at(gold_ranks, order, n),
            "rate": round(coverage_at(gold_ranks, order, n) / len(order), 6),
        }
        for n in (1, 2, 4, 5, 8, 10, 16, 24, 32, 48, 64, 96, 128, max(pool_full, 200))
    }

    analysis = {
        "instrument": f"track_r_paraphrase n={len(order)}",
        "stage": args.stage,
        "decisive": args.stage == "confirm",
        "endpoint": s8.ENDPOINT_CONTRACT,
        "preregistration": s8.PREREGISTRATION,
        "planning_mde_pp": round(PLANNING_MDE * 100, 2),
        "n_d_structural_floor": N_D_FLOOR,
        "not_a_measurement_note": (
            None
            if args.stage == "confirm"
            else "STAGE 2. Reduced n by preregistered subset; this stage locates "
            "the knee and decides nothing. Its p-values are reported for shape "
            "only and no verdict is drawn from them."
        ),
        "comparators": {
            "memphant_fused_at_10": {
                "hits_at_10": sum(retriever["fused"][q] for q in order),
                "rate": round(sum(retriever["fused"][q] for q in order) / len(order), 6),
                "source": str(args.pool_dump),
                "llm_calls_at_recall": 0,
                "usd_per_question": 0.0,
                "note": "Arm H's stage-matched retriever comparator: plain ranked bodies",
            },
            "memphant_packed_at_10": {
                "hits_at_10": sum(retriever["packed"][q] for q in order),
                "rate": round(sum(retriever["packed"][q] for q in order) / len(order), 6),
                "source": str(args.pool_dump),
                "llm_calls_at_recall": 0,
                "usd_per_question": 0.0,
                "note": "the shipped default; reported beside, never the paired headline vs H",
            },
            "agentic_grep": {
                "hits_at_10": sum(grep_hits[q] for q in order),
                "rate": round(sum(grep_hits[q] for q in order) / len(order), 6),
                "source": str(args.grep),
                "usd_per_question": GREP_USD_PER_QUESTION,
                "haystack": "the attempt's raw events — a SUPERSET of Arm H's pool",
            },
        },
        "coverage_curve": coverage_curve,
        "ceiling_note": (
            "Arm H cannot exceed Coverage(N) at any N, and Coverage tops out "
            "below agentic grep's realized rate. Each N is scored against its "
            "own ceiling; rank_accuracy is that ratio."
        ),
        "sweep": rows,
        "lineage": s8.lineage(
            {
                "pool_dump": args.pool_dump,
                "golden": args.golden,
                "grep_arm": args.grep,
                **{f"arm:{label}": entry["path"] for label, entry in arms.items()},
            }
        ),
    }
    if args.stage == "confirm":
        if args.pool_provenance is None:
            raise SystemExit("--pool-provenance is required at --stage confirm")
        headline_row = (
            next(row for row in rows if row["label"] == args.headline_label)
            if args.headline_label
            else max(rows, key=lambda row: row["hits_at_10"])
        )
        analysis["headline_arm"] = headline_row["label"]
        analysis["evidence_contract"] = evidence_contract(
            headline_row, json.loads(args.pool_provenance.read_text())
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(analysis, indent=2) + "\n")
    for row in rows:
        print(
            f"N={row['N']:>4}  hits={row['hits_at_10']:>3}/{row['n']}  "
            f"ceiling={row['coverage_ceiling_hits']:>3}  "
            f"rankacc={row['rank_accuracy']}  "
            f"out_of_view={row['misses_out_of_view']:>3} "
            f"ranked_out={row['misses_in_view_ranked_out']:>3}  "
            f"${row['usd_per_question']}/q ({row['usd_per_question_vs_grep']}x grep)"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
