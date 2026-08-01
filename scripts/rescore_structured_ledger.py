#!/usr/bin/env python3
"""Re-score B1's banked candidate ledger with a directive-sentence unit.

Offline, DB-free, $0. No ingest, no server, no model, no network. It reads two
things that already exist: the 7,890 live candidate scores Arm S banked in its
own artifact, and the pinned MemoryCode corpus.

The question
------------
B1's extractor decides whether to supersede by content-word Jaccard between the
candidate's body and the top-ranked prior unit's body. Its live distribution is
median 0.194, p95 0.283, max 0.521 — nothing ever approaches the thresholds an
offline sentence-level rule operates at. The obvious reading is "the threshold
is miscalibrated". This script tests a different one: **the similarity unit is
wrong.** A MemoryCode session is ~2,300 characters of mentor small talk wrapped
around one directive sentence, so a whole-body Jaccard is dominated by filler
that every session shares, and the discriminating tokens are a rounding error.

So: hold the candidate pairs fixed — exactly the pairs B1's own ranker chose —
and change only what is compared. Body vs. the best-matching directive sentence.

What this can and cannot say
----------------------------
CAN: whether a sentence-level unit separates co-declaring pairs from unrelated
ones better than a body-level unit, at a matched number of firings, on the pairs
B1 actually saw.

CANNOT: what latest-state-wins would be. These are pairs the live ranker already
returned as top-1; a sentence-level *scorer* would not change what the ranker
retrieves, and nothing here is a retrieval measurement. It is a precision
comparison at matched cost, and it is `decisional: false` for that reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure_key_recovery as kr  # noqa: E402
from external_instrument_adapter import load_memorycode, sha256_file  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "benchmarks" / "manifests" / "memorycode.lock.json"
FIRING_POINTS = (100, 250, 500, 1091, 2000)


def jaccard(left: frozenset, right: frozenset) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-s", required=True, type=Path, help="Arm S artifact")
    parser.add_argument("--source", required=True, type=Path, help="MemoryCode parquet")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    source = args.source.expanduser()
    digest = sha256_file(source)
    lock = json.loads(LOCK.read_text())
    expected = lock["dataset"]["files"][lock["dataset"]["primary_file"]]["sha256"]
    if digest != expected:
        print(f"FATAL: corpus sha256 {digest} != pinned {expected}", file=sys.stderr)
        return 2

    arm_s = json.loads(args.arm_s.read_text())
    ledger = arm_s["diagnostics"]["structured_extractor"]["ledger"]

    groups = load_memorycode(source)
    gold = kr.gold_structure(groups)
    bodies: dict[str, str] = {}
    for group in groups:
        for index, unit in enumerate(group["units"]):
            bodies[f"{group['group_id']}-s{index}"] = unit["body"]

    cache: dict[str, list[frozenset]] = {}

    def directive_sentences(uid: str) -> list[frozenset]:
        if uid not in cache:
            cache[uid] = [
                frozenset(kr.content_words(blanked))
                for _, blanked in kr.literal_sentences(bodies[uid])
            ]
        return cache[uid]

    rows = []
    for entry in ledger:
        left, right = entry["session"], entry["target_session"]
        if left not in bodies or right not in bodies:
            continue
        gid, li = left.rsplit("-s", 1)
        ri = right.rsplit("-s", 1)[1]
        pair = tuple(sorted((int(li), int(ri))))
        co_declaring = pair in gold[gid]["gold_pairs"]
        best = max(
            (
                jaccard(a, b)
                for a in directive_sentences(left)
                for b in directive_sentences(right)
            ),
            default=0.0,
        )
        rows.append(
            {
                "body": entry["jaccard"],
                "sentence": best,
                "co_declaring": co_declaring,
                "named_by_b1": entry["named"],
            }
        )

    def describe(key: str) -> dict:
        values = sorted(r[key] for r in rows)
        return {
            "median": round(statistics.median(values), 6),
            "p95": round(values[int(0.95 * len(values))], 6),
            "max": round(max(values), 6),
        }

    def precision_at(key: str, n: int) -> float:
        top = sorted(rows, key=lambda r: -r[key])[:n]
        return round(sum(r["co_declaring"] for r in top) / n, 6)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
    )

    artifact = {
        "measurement": "b1_ledger_rescored_with_a_directive_sentence_unit",
        "decisional": False,
        "decisional_reason": "A precision comparison at matched firing count on "
        "pairs B1's ranker already selected. It is not a retrieval measurement "
        "and it is not latest-state-wins; only a live arm can carry that.",
        "paid_model_calls": 0,
        "lineage": {
            "git_head": head,
            "git_branch": "w1-keyprod",
            "git_dirty": dirty,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "served_binaries": "NONE — no binary, server, worker or database runs here.",
            "upstream_artifact": str(args.arm_s),
            "upstream_lineage": arm_s.get("lineage"),
        },
        "corpus": {"sha256": digest, "revision": lock["attribution"]["revision"]},
        "pairs_scored": len(rows),
        "co_declaring_pairs": sum(r["co_declaring"] for r in rows),
        "b1_named": sum(r["named_by_b1"] for r in rows),
        "distribution": {"body": describe("body"), "sentence": describe("sentence")},
        "precision_at_matched_firing_count": {
            str(n): {"body": precision_at("body", n), "sentence": precision_at("sentence", n)}
            for n in FIRING_POINTS
            if n <= len(rows)
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

    print(f"pairs {len(rows)}  co-declaring {artifact['co_declaring_pairs']}")
    for unit in ("body", "sentence"):
        d = artifact["distribution"][unit]
        print(f"  {unit:9s} median {d['median']:.4f}  p95 {d['p95']:.4f}  max {d['max']:.4f}")
    print(f"\n{'n_fired':>8} {'BODY':>8} {'SENTENCE':>9}")
    for n, v in artifact["precision_at_matched_firing_count"].items():
        print(f"{int(n):8d} {v['body']:8.3f} {v['sentence']:9.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
