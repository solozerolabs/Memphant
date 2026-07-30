#!/usr/bin/env python3
"""Phase 1b/1c summary over the three executed Track R arms.

Reads only committed-shape provenance reports written by
``code_lane_run_deterministic.py`` (BM25 control) and
``code_lane_run_memphant.py`` (cap-OFF, cap-1200) and emits ONE committable
summary JSON. It computes nothing the arms did not measure: every rate here is
a re-aggregation of the arms' own per-question ``hit_at_5``/``hit_at_10`` and
pack-drop rows.

Two preregistered questions it answers:

* **Bank saturation (hypothesis A).** BM25 r@10 overall, and split by whether
  the golden carries an adjudicated distractor (the one preregistered check the
  bank missed, 75/180 < 50%). A saturated control cannot express a substrate
  win; a control that wins mainly on the no-distractor subset makes the
  coverage miss a real defect rather than a threshold artifact.
* **Render-cap inertness (hypothesis B).** The packed-items / per-item render
  size deltas between cap-OFF and cap-1200. Identical values mean the cap did
  not run on this corpus — a null about the run, not about code bodies.

No kill-gate verdict and no bar amendment is emitted: those are owner decisions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def golden_distractor_flags(golden_path: Path) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for line in golden_path.read_text().splitlines():
        if not line.strip():
            continue
        golden = json.loads(line)
        adjudication = golden.get("adjudication") or {}
        flags[golden["question_id"]] = bool(adjudication.get("distractors"))
    return flags


def rate(rows: list[dict], key: str) -> float | None:
    return (sum(bool(row[key]) for row in rows) / len(rows)) if rows else None


def arm_recall(report: dict, flags: dict[str, bool]) -> dict:
    rows = report["per_question"]
    with_distractor = [row for row in rows if flags[row["question_id"]]]
    without = [row for row in rows if not flags[row["question_id"]]]
    return {
        "n": len(rows),
        "recall_at_5": rate(rows, "hit_at_5"),
        "recall_at_10": rate(rows, "hit_at_10"),
        "with_adjudicated_distractor": {
            "n": len(with_distractor),
            "recall_at_5": rate(with_distractor, "hit_at_5"),
            "recall_at_10": rate(with_distractor, "hit_at_10"),
        },
        "without_adjudicated_distractor": {
            "n": len(without),
            "recall_at_5": rate(without, "hit_at_5"),
            "recall_at_10": rate(without, "hit_at_10"),
        },
    }


def paired_flips(left: dict, right: dict, key: str) -> dict:
    """Per-question flip counts between two arms on the same golden set."""
    left_rows = {row["question_id"]: bool(row[key]) for row in left["per_question"]}
    right_rows = {row["question_id"]: bool(row[key]) for row in right["per_question"]}
    if set(left_rows) != set(right_rows):
        raise RuntimeError("arms did not run on the same question set")
    both = sum(left_rows[q] and right_rows[q] for q in left_rows)
    return {
        "both_hit": both,
        "left_only": sum(left_rows[q] and not right_rows[q] for q in left_rows),
        "right_only": sum(right_rows[q] and not left_rows[q] for q in left_rows),
        "neither": sum(not left_rows[q] and not right_rows[q] for q in left_rows),
    }


def render_witness(report: dict) -> dict:
    summary = report["pack_drop_summary"]
    return {
        "pack_render_cap": report["pack_render_cap"],
        "packed_items_total": summary["packed_items_total"],
        "packed_items_mean": summary["packed_items_mean"],
        "packed_item_chars_total": summary["packed_item_chars_total"],
        "packed_item_chars_mean": summary["packed_item_chars_mean"],
        "packed_item_chars_max": summary["packed_item_chars_max"],
        "buckets": summary["buckets"],
        "in_pool_unpacked": summary["in_pool_unpacked"],
        "in_pool_unpacked_gold_drop_reasons": summary[
            "in_pool_unpacked_gold_drop_reasons"
        ],
        "budget_share_of_in_pool_unpacked": summary["budget_share_of_in_pool_unpacked"],
    }


def build_summary(
    golden_path: Path, bm25: dict, cap_off: dict, cap_1200: dict, flags: dict[str, bool]
) -> dict:
    off_witness = render_witness(cap_off)
    on_witness = render_witness(cap_1200)
    cap_changed_packing = (
        off_witness["packed_items_total"] != on_witness["packed_items_total"]
        or off_witness["packed_item_chars_total"] != on_witness["packed_item_chars_total"]
    )
    return {
        "schema": "memphant.eval.track-r-phase1-summary.v1",
        "golden_path": str(golden_path),
        "golden_sha256": bm25["golden_sha256"],
        "corpus_sha256": bm25["corpus_sha256"],
        "paid_api_spend_usd": 0,
        "arms": {
            "bm25_control": arm_recall(bm25, flags),
            "memphant_cap_off": arm_recall(cap_off, flags),
            "memphant_cap_1200": arm_recall(cap_1200, flags),
        },
        "paired_hit_at_10": {
            "memphant_cap_off_vs_bm25": paired_flips(cap_off, bm25, "hit_at_10"),
            "cap_1200_vs_cap_off": paired_flips(cap_1200, cap_off, "hit_at_10"),
        },
        "hypothesis_b_render_witness": {
            "cap_off": off_witness,
            "cap_1200": on_witness,
            "cap_changed_packing": cap_changed_packing,
            "reading": (
                "cap altered the pack on this corpus; the retrieval delta is "
                "interpretable"
                if cap_changed_packing
                else "cap did not alter the pack on this corpus: the run is a null "
                "about the cap NOT RUNNING here, not evidence that the cap fails "
                "on code bodies"
            ),
        },
        "arm_provenance": {
            "bm25_control": bm25["runtime_identity"]["command"]
            if "runtime_identity" in bm25
            else None,
            "memphant_cap_off": cap_off["runtime_identity"]["command"],
            "memphant_cap_1200": cap_1200["runtime_identity"]["command"],
        },
        "note": (
            "Kill-gate and bar decisions are the owner's; with_distractors_ge_50pct "
            "stays false in the golden lock."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--bm25", required=True, type=Path)
    parser.add_argument("--cap-off", required=True, type=Path)
    parser.add_argument("--cap-1200", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    flags = golden_distractor_flags(args.golden)
    summary = build_summary(
        args.golden,
        json.loads(args.bm25.read_text()),
        json.loads(args.cap_off.read_text()),
        json.loads(args.cap_1200.read_text()),
        flags,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
