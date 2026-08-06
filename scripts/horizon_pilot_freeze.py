#!/usr/bin/env python3
"""Freeze the group-sequential PILOT: the first 4/3/3 fresh users per generator
(gemini-3-flash/o3/sonnet-4.5) by the same seeded ranking as the interim, so the
10-user / 20-item pilot is a provable prefix of the 60-user interim, which in
turn nests in the 102-user n_max. The pilot is the kill gate (packet §7).

Reads the interim source (body-bearing, local, gitignored) and emits a frozen
pilot selection + source in the exact shape `load_locked_confirmation` accepts.
Bodies (pilot-source) stay out of git; the selection (ids + hashes) is committed.

Usage:
  python3 scripts/horizon_pilot_freeze.py \
    --interim-source docs/build-log/artifacts/horizonbench-fresh-v1/fresh-source-20.jsonl \
    --interim-selection docs/build-log/artifacts/horizonbench-fresh-v1/fresh-selection-20.json \
    --out-source <pilot-source.jsonl> --out-selection <pilot-selection.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import run_horizonbench as H

# Balanced 4/3/3 = 10 users, preserving generator representation. Alphabetical
# generator order matches the interim's own per-generator ranking.
PILOT_PER_GENERATOR = {"gemini-3-flash": 4, "o3": 3, "sonnet-4.5": 3}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interim-source", required=True, type=Path)
    ap.add_argument("--interim-selection", required=True, type=Path)
    ap.add_argument("--seed", default="horizonbench-fresh-v1")
    ap.add_argument("--out-source", required=True, type=Path)
    ap.add_argument("--out-selection", required=True, type=Path)
    args = ap.parse_args()

    selection = json.loads(args.interim_selection.read_text(encoding="utf-8"))
    if selection.get("status") != "frozen" or selection.get("seed") != args.seed:
        raise ValueError("interim selection is not the expected frozen seed")
    raw = args.interim_source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != selection["source_jsonl_sha256"]:
        raise ValueError("interim source hash drift")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]

    by_gen: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        by_gen.setdefault(row["generator"], {}).setdefault(row["user_id"], []).append(row)

    pilot_users: set[str] = set()
    for generator, want in PILOT_PER_GENERATOR.items():
        ranked = sorted(
            by_gen.get(generator, {}),
            key=lambda uid: H._seeded_key(args.seed, generator, uid),
        )
        if len(ranked) < want:
            raise ValueError(f"generator {generator} has {len(ranked)} < {want} interim users")
        pilot_users.update(ranked[:want])

    # Prefix guarantee: every pilot user is an interim user.
    interim_users = set(selection["expected_user_ids"])
    if not pilot_users <= interim_users:
        raise ValueError("pilot users are not a subset of the interim")

    ordered = [row for row in rows if row["user_id"] in pilot_users]
    ordered.sort(key=lambda row: (row["user_id"], row["id"]))
    H.validate_benchmark_rows(
        ordered,
        expected_rows=len(pilot_users) * 2,
        expected_users=len(pilot_users),
        expected_generator_counts={
            g: PILOT_PER_GENERATOR[g] * 2 for g in PILOT_PER_GENERATOR
        },
    )
    source_raw = H.canonical_jsonl_bytes(ordered)
    args.out_source.parent.mkdir(parents=True, exist_ok=True)
    args.out_source.write_bytes(source_raw)

    pilot_user_ids = sorted(pilot_users)
    pilot_ids = [row["id"] for row in ordered]
    # Prefix guarantee at item level too.
    if not set(pilot_ids) <= set(selection["expected_ids"]):
        raise ValueError("pilot items are not a subset of the interim")

    report = {
        "schema_version": 1,
        "status": "frozen",
        "role": "group-sequential pilot kill gate (packet §7)",
        "dataset": selection["dataset"],
        "dataset_revision": selection["dataset_revision"],
        "seed": args.seed,
        "source_jsonl_sha256": hashlib.sha256(source_raw).hexdigest(),
        "expected_ids": pilot_ids,
        "expected_user_ids": pilot_user_ids,
        "rows": len(pilot_ids),
        "users": len(pilot_user_ids),
        "users_per_generator": PILOT_PER_GENERATOR,
        "nests_in_interim": True,
        "interim_selection_sha256": H.gr.sha256_file(args.interim_selection.resolve()),
        "gold_quarantine": {
            "selection_fields": ["id", "user_id", "generator", "has_evolved"],
            "selection_uses_correct_letter": False,
            "selection_uses_distractor_letter": False,
            "mental_state_graphs_acquired": False,
        },
        "notes": "First 4/3/3 users per generator by the interim seeded ranking; prefix of the interim.",
    }
    args.out_selection.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "pilot_users": len(pilot_user_ids),
        "pilot_items": len(pilot_ids),
        "user_ids": pilot_user_ids,
        "fresh_pilot_idset_sha256": hashlib.sha256(
            json.dumps(pilot_user_ids, separators=(",", ":")).encode()
        ).hexdigest(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
