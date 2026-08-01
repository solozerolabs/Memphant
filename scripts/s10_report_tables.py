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
import sys
from pathlib import Path


def pct(x: float | None, digits: int = 4) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def ci(block: dict | None) -> str:
    if not block:
        return "—"
    return f"{block['point']:.4f} [{block['lo']:.4f}, {block['hi']:.4f}] (k={block['k']}/n={block['n']})"


def main() -> int:
    art = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
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
