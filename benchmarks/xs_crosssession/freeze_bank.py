#!/usr/bin/env python3
"""Assemble mined slices into the frozen XS dev bank.

Dedups by answer-set overlap, assigns stable ids xs_NNN in input order,
stamps corpus/units hashes, writes bank + sha256. Run once; the output is
frozen (exposure-guard rule: the split is never re-cut).
"""

import hashlib
import json
import sys
from pathlib import Path

PRIV = Path.home() / ".memphant-private/xs-crosssession"
SNAP = PRIV / "corpus-snapshot-2026-08-05"


def main(out_name: str, *slice_paths: str) -> int:
    out = PRIV / out_name
    if out.exists():
        print(f"REFUSING: {out} exists — the bank is frozen", file=sys.stderr)
        return 1
    goldens, seen_answer_sets = [], []
    for p in slice_paths:
        for g in json.loads(Path(p).read_text()):
            aset = frozenset(g["answer_bearing_ids"])
            if any(aset & s for s in seen_answer_sets):
                continue  # same fact already covered by an earlier golden
            seen_answer_sets.append(aset)
            goldens.append(g)
    for i, g in enumerate(goldens, 1):
        g["id"] = f"xs_{i:03d}"
    bank = {
        "lane": "xs-crosssession",
        "split": "dev",
        "miner": "claude-fable-5 session subagents, 2026-08-05",
        "corpus_manifest_sha256": hashlib.sha256(
            (SNAP / "MANIFEST.sha256").read_bytes()).hexdigest(),
        "units_sha256": hashlib.sha256((PRIV / "units.jsonl").read_bytes()).hexdigest(),
        "endpoint_contract": "hits@10: any answer_bearing_id in arm top-10 unit ids",
        "goldens": goldens,
    }
    out.write_text(json.dumps(bank, indent=1))
    print(json.dumps({"n": len(goldens),
                      "kinds": {k: sum(g["kind_expected"] == k for g in goldens)
                                for k in {g["kind_expected"] for g in goldens}},
                      "bank_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                      "path": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
