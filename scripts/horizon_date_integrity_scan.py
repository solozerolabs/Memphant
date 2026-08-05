#!/usr/bin/env python3
"""Derive the HorizonBench date-integrity exclusion set.

Six eligible synthetic users carry session dates the runtime cannot ground:
relative placeholders (`[Today]`, `[six months from today]`), unfilled template
stubs (`YYYY-MM-DD`, `[auto-fill]`), and a control-char corruption. The runtime
assigns `observed_at` from session dates, so these timelines cannot be temporally
ordered — the same class of benchmark-integrity defect the census already
excludes 2 drift users for.

This scan makes the exclusion reproducible and hash-bound rather than a magic
list: run it, commit the artifact, feed it to `select-fresh-tranche
--extra-exclusions`. Requires pyarrow (run under `uv run --with pyarrow`).

Usage:
  uv run --with pyarrow --python python3 python \
    scripts/horizon_date_integrity_scan.py --out <artifact.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import run_horizonbench as H

REPO_ROOT = Path(__file__).resolve().parents[1]


def parses_as_date(value: object) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full-lock",
        default=REPO_ROOT / "benchmarks/manifests/horizonbench.benchmark.v1.json",
        type=Path,
    )
    parser.add_argument(
        "--cache-root",
        default=Path("~/.cache/memphant-bench/horizonbench"),
        type=Path,
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    full = json.loads(args.full_lock.read_text(encoding="utf-8"))
    H.validate_source_revision(full.get("dataset_revision"))
    index_rows = H.load_jsonl(Path(full["index_path"]))
    by_user: dict[str, list[dict]] = {}
    for row in index_rows:
        by_user.setdefault(row["user_id"], []).append(row)

    cache_dir = args.cache_root.expanduser().resolve() / H.DATASET_REVISION
    all_rows = {
        row["id"]: row
        for row in H._parquet_rows(
            [cache_dir / source[0] for source in H.BENCHMARK_SOURCE_FILES]
        )
    }

    affected: dict[str, list[str]] = {}
    for user_id, rows in by_user.items():
        if {row["has_evolved"] for row in rows} != {False, True}:
            continue  # eligibility mirrors select_confirmation_rows
        bad: set[str] = set()
        for row in rows:
            full_row = all_rows.get(row["id"])
            if not full_row:
                continue
            for session in H.parse_sessions(
                H.normalize_source_text(full_row["conversation"])
            ):
                date = session.get("date")
                if not parses_as_date(date):
                    bad.add(str(date))
        if bad:
            affected[user_id] = sorted(bad)

    excluded = sorted(affected)
    artifact = {
        "schema_version": 1,
        "purpose": (
            "HorizonBench eligible users whose session dates cannot be grounded; "
            "excluded from fresh-tranche selection like census drift users."
        ),
        "dataset_revision": H.DATASET_REVISION,
        "method": "parse every session date via datetime.fromisoformat over the eligible pool",
        "excluded_user_ids": excluded,
        "excluded_user_ids_sha256": hashlib.sha256(
            json.dumps(excluded, separators=(",", ":")).encode()
        ).hexdigest(),
        "bad_date_samples": {uid: affected[uid][:5] for uid in excluded},
        "full_lock_sha256": H.gr.sha256_file(args.full_lock.resolve()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"excluded": len(excluded), "users": excluded}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
