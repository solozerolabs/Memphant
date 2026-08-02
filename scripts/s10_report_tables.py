#!/usr/bin/env python3
"""Render the S10 comparison artifact as the markdown tables the report cites.

Nothing is computed here that the artifact does not already carry: this is a
formatter, so the report and the artifact cannot disagree. The one derived
figure is the bounded reading of cell `c` (correct with the gold span absent),
which is split by the crude normalized-substring text check and therefore
printed as an interval rather than a partition.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

BOOTSTRAP_DRAWS = 20000
BOOTSTRAP_SEED = 20260801


def conversion(art: dict, hits: dict[str, dict[str, bool]],
               treatment: str, control: str) -> dict | None:
    """How much of a retrieval gap arrives at the answer?

    conversion = (accuracy gap) / (hit@10 gap), on the SAME paired questions.

    Reported as its own number because the two arm rates do not carry it: a
    +0.378 retrieval gap that yields +0.167 on answers means retrieval points
    are discounted by more than half at the outcome, which changes what a
    retrieval point is worth. The CI is a paired bootstrap over question ids —
    resampling questions, not arms, because both arms answer every question and
    the numerator and denominator move together.

    Draws where the resampled hit gap is <= 0 are DROPPED and counted, not
    silently kept: the ratio is undefined there, and keeping them would let a
    near-zero denominator manufacture an arbitrarily wide interval.
    """
    order = art["per_question_vectors"]["order"]
    arms = art["per_question_vectors"]["arms"]
    if treatment not in arms or control not in arms:
        return None
    if treatment not in hits or control not in hits:
        return None
    t_acc = [bool(x) for x in arms[treatment]]
    c_acc = [bool(x) for x in arms[control]]
    t_hit = [bool(hits[treatment].get(q)) for q in order]
    c_hit = [bool(hits[control].get(q)) for q in order]
    n = len(order)

    def point(idx: list[int]) -> tuple[float, float]:
        gap_hit = sum(c_hit[i] for i in idx) / len(idx) - sum(t_hit[i] for i in idx) / len(idx)
        gap_acc = sum(c_acc[i] for i in idx) / len(idx) - sum(t_acc[i] for i in idx) / len(idx)
        return gap_hit, gap_acc

    base_hit, base_acc = point(list(range(n)))
    if base_hit <= 0:
        return {"defined": False, "hit_gap": base_hit, "accuracy_gap": base_acc,
                "note": "hit gap is not positive; a conversion ratio is undefined"}
    rng = random.Random(BOOTSTRAP_SEED)
    ratios: list[float] = []
    dropped = 0
    for _ in range(BOOTSTRAP_DRAWS):
        idx = [rng.randrange(n) for _ in range(n)]
        gap_hit, gap_acc = point(idx)
        if gap_hit <= 0:
            dropped += 1
            continue
        ratios.append(gap_acc / gap_hit)
    ratios.sort()
    lo = ratios[int(0.025 * len(ratios))] if ratios else None
    hi = ratios[int(0.975 * len(ratios)) - 1] if ratios else None
    return {
        "defined": True,
        "treatment": treatment,
        "control": control,
        "hit_gap": base_hit,
        "accuracy_gap": base_acc,
        "conversion_ratio": base_acc / base_hit,
        "ci_lo": lo,
        "ci_hi": hi,
        "draws": len(ratios),
        "draws_dropped_undefined": dropped,
        "method": f"paired bootstrap over question ids, {BOOTSTRAP_DRAWS} draws, "
                  f"seed {BOOTSTRAP_SEED}, percentile CI",
    }


def pct(x: float | None, digits: int = 4) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def ci(block: dict | None) -> str:
    if not block:
        return "—"
    return f"{block['point']:.4f} [{block['lo']:.4f}, {block['hi']:.4f}] (k={block['k']}/n={block['n']})"


def main() -> int:
    art = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    hits: dict[str, dict[str, bool]] = {}
    for spec in sys.argv[2:]:
        name, _, path = spec.partition("=")
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        hits[name] = {r["question_id"]: bool(r.get("hit_at_10"))
                      for r in report.get("per_question", [])}
    print(f"## Endpoint `{art['endpoint']}` · n = {art['n_paired']} · "
          f"control = `{art['control_arm']}`\n")

    print("### Arm accuracy\n")
    print("| arm | answer accuracy | banked hit@10 | reader abstentions | provider refusals | errors |")
    print("|---|---:|---:|---:|---:|---:|")
    for name, acc in art["arm_accuracy"].items():
        g = art["gold_span_decomposition"][name]
        j = art["judge_accounting"][name]
        print(f"| `{name}` | **{acc:.4f}** ({art['arm_correct_counts'][name]}/{art['n_paired']}) | "
              f"{pct(g['retrieval_hit_at_10'])} | {j['reader_abstentions']} | "
              f"{j['reader_provider_refusals']} | {j['rows_with_error']} |")

    bounded = [f"`{n}` +{j['reader_provider_refusals']}"
               for n, j in art["judge_accounting"].items()
               if j["reader_provider_refusals"]]
    if bounded:
        print(f"\n**These accuracies are LOWER BOUNDS.** A provider refusal is scored "
              f"as a non-answer, so those rows are guaranteed-incorrect for a reason "
              f"that is not the pack: {', '.join(bounded)} row(s). The true ceiling "
              f"for each arm is its figure plus its refusal count.")

    conv = conversion(art, hits, "memphant", art["control_arm"])
    if conv and conv.get("defined"):
        print(f"\n### Does the retrieval gap convert?\n")
        print(f"| quantity | value |")
        print(f"|---|---:|")
        print(f"| hit@10 gap (`{conv['control']}` − `{conv['treatment']}`) | "
              f"**{conv['hit_gap']:+.4f}** |")
        print(f"| answer-accuracy gap, same questions | **{conv['accuracy_gap']:+.4f}** |")
        print(f"| **conversion ratio** | **{conv['conversion_ratio']:.3f}** "
              f"[{conv['ci_lo']:.3f}, {conv['ci_hi']:.3f}] |")
        print(f"\n{conv['method']}; {conv['draws_dropped_undefined']} of "
              f"{BOOTSTRAP_DRAWS} draws dropped as undefined (resampled hit gap "
              f"<= 0). A ratio below 1 means retrieval points are DISCOUNTED at "
              f"the outcome: {(1 - conv['conversion_ratio']) * 100:.1f}% of this "
              f"retrieval gap does not arrive as answer correctness.")

    print("\n### The joint distribution — the primary deliverable\n")
    for name in art["arm_accuracy"]:
        g = art["gold_span_decomposition"][name]
        jt = g["joint_2x2"]
        print(f"**`{name}`**"
              + ("  (no retrieval report — every row counts as gold-not-retrieved by construction)"
                 if not g["retrieval_report_available"] else "") + "\n")
        print("|  | answer_correct | answer_wrong |")
        print("|---|---:|---:|")
        print(f"| **gold retrieved** | a = {jt['a_hit_correct']} | b = {jt['b_hit_wrong']} |")
        print(f"| **gold NOT retrieved** | **c = {jt['c_miss_correct']}** | d = {jt['d_miss_wrong']} |")
        print()
        print(f"* P(correct \\| gold retrieved) = {ci(g['p_correct_given_gold_present_ci'])}")
        print(f"* P(correct \\| gold NOT retrieved) = {ci(g['p_correct_given_gold_absent_ci'])}")
        diff = g["conditional_rate_difference"]
        if diff:
            z = "—" if diff["z"] is None else f"{diff['z']:.3f}"
            print(f"* difference = {diff['diff']:+.4f}, z = {z}, "
                  f"two-sided p = {diff['p_two_sided']:.3e}")
        print(f"* phi(hit@10, answer_correct) = {pct(g['hit_vs_correct_phi'])} · "
              f"raw agreement = {pct(g['hit_vs_correct_agreement'])}")
        c = jt["c_miss_correct"]
        tp = g["correct_span_absent_text_present"]
        ta = g["correct_span_absent_text_absent"]
        print(f"* cell c = {c}, bounded: the gold answer STRING was in the pack on "
              f"at most {tp} of them and absent on at least {ta}. The check is a "
              f"normalized substring match, so it over-counts short gold answers; "
              f"read these as bounds, not a partition.")
        print()

    print("### Paired contrasts (two-sided exact McNemar, alpha=0.05)\n")
    print("| contrast | b | c | n_d | delta | p | realized psi | MDE@80% | verdict |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for key, v in art["contrasts"].items():
        mde = "—" if v["mde_at_80_power"] is None else f"{v['mde_at_80_power']:.4f}"
        print(f"| `{key}` | {v['b_left_only']} | {v['c_right_only']} | {v['n_discordant']} | "
              f"{v['delta']:+.4f} | {v['mcnemar_exact_p']:.4g} | {v['realized_psi']:.4f} | "
              f"{mde} | {v['verdict']} |")

    print("\n### Accounting\n")
    print("| arm | judge fire rate | fresh calls | cached | reported spend USD | complete | aborted |")
    print("|---|---:|---:|---:|---:|---|---|")
    total = 0.0
    for name, j in art["judge_accounting"].items():
        spend = j["reported_spend_usd"] or 0.0
        total += float(spend)
        print(f"| `{name}` | {j['judge_fire_rate']:.3f} | {j['fresh_calls']} | "
              f"{j['cached_calls']} | {float(spend):.4f} | {j['complete']} | {j['aborted']} |")
    print(f"\n**Total reported spend on this endpoint's arms: ${total:.2f}.**")
    print(f"\nLineage: git_head `{art['lineage']['git_head']}`, dirty "
          f"`{art['lineage']['git_dirty']}`, corpus "
          f"`{art['lineage']['corpus_sha256'][:16]}…`, golden "
          f"`{art['lineage']['golden_sha256'][:16]}…`, k={art['stage_equalization']['k']}, "
          f"budget={art['stage_equalization']['budget_tokens']}, "
          f"tokens_mean_by_arm={art['stage_equalization']['tokens_mean_by_arm']}. "
          f"decisional={art['evidence_contract']['decisional']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
