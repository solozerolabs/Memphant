#!/usr/bin/env python3
"""Restrict a preference-lane arm report to one `--group-mod` residue class.

Exists so a paired comparison can be run against a slice WITHOUT re-ingesting a
corpus for it. The residue rule is the same `sha256(group_id) % modulus` the
adapter applies at load time, so a subset taken here is bit-identical to the
slice the adapter would have produced -- which is what lets a banked full-bank
arm serve as the reference for a slice-scale treatment arm.

Rewrites `summary` from the surviving rows via the adapter's own `summarise`,
so no figure is carried over from the wider bank.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import external_instrument_adapter as adapter  # noqa: E402


def keep(group_id: str, residue: int, modulus: int) -> bool:
    return int(hashlib.sha256(group_id.encode()).hexdigest(), 16) % modulus == residue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, type=Path)
    parser.add_argument("--group-mod", required=True, help="RESIDUE/MODULUS")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    residue, modulus = (int(part) for part in args.group_mod.split("/"))
    report = json.loads(args.arm.read_text())
    rows = [row for row in report["rows"] if keep(row["group_id"], residue, modulus)]
    if not rows:
        raise SystemExit(f"{args.arm}: no rows survive group-mod {args.group_mod}")
    report["rows"] = rows
    report["summary"] = adapter.summarise(rows, report["recall"]["k"])
    report["scale"] = dict(report.get("scale") or {}, group_mod=args.group_mod,
                           groups=len({row["group_id"] for row in rows}))
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
