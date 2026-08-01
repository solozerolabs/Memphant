#!/usr/bin/env python3
"""S1 mechanism-liveness gate. Runs BEFORE any accuracy number is read.

An arm whose supersede-edge count is zero is INERT, and "inert" is then the
whole report -- there is no accuracy claim to make about a mechanism that never
fired. This script asserts that, plus the two invariants the B1 lane paid for:
`remainders_recalled == 0` (no valid-time-closed row ever reached a recall
result) and corpus compilation verified from the DATABASE on the bench
superuser credential, never a worker self-report.

It also stamps lineage for every arm and REFUSES to pass if the arms do not
share one tree, one set of served binary sha256s, one corpus sha256 and one
probe bank. Deltas must be within-run; a headline in this program was voided
once for comparing across pipeline stages.

Stdlib only. No model call, no network, $0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# arm -> (must the supersede-edge count be > 0?)
EXPECT_EDGES = {
    "n-noop": False,      # the no-op isolator; zero edges is its DEFINITION
    "s-body": True,
    "u-sentence": True,
    "r3-random": True,
}


def scalar(diagnostics: dict, key: str) -> int:
    value = diagnostics.get(key)
    if isinstance(value, list):
        if not value:
            raise SystemExit(f"diagnostic {key!r} is empty -- cannot gate on it")
        value = value[0]
    if isinstance(value, str) and not value.lstrip("-").isdigit():
        raise SystemExit(f"diagnostic {key!r} did not return a number: {value[:200]}")
    return int(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    arms, failures = {}, []
    for name, expect in EXPECT_EDGES.items():
        path = args.dir / f"arm-{name}.json"
        if not path.exists() or path.stat().st_size == 0:
            failures.append(f"{name}: artifact missing or empty ({path})")
            continue
        report = json.loads(path.read_text())
        diagnostics = report.get("diagnostics") or {}
        extractor = diagnostics.get("structured_extractor") or {}
        ledger = extractor.get("ledger") or []
        edges = scalar(diagnostics, "supersedes_edges")
        remainders = int(diagnostics.get("remainders_recalled", -1))
        compiled = diagnostics.get("compilation_verified") or {}
        arms[name] = {
            "artifact": str(path),
            "lineage": report.get("lineage"),
            "corpus_sha256": (report.get("source") or {}).get("sha256"),
            "probes": len(report.get("rows") or []),
            "instances": len({r["group_id"] for r in report.get("rows") or []}),
            "unit": extractor.get("unit"),
            "threshold": extractor.get("threshold"),
            "ablation": extractor.get("ablation"),
            "fire_rate": extractor.get("fire_rate"),
            "supersedes_edges": edges,
            "supersede_edges_expected_positive": expect,
            "superseded_units": scalar(diagnostics, "superseded_units"),
            "superseded_with_open_transaction": scalar(
                diagnostics, "superseded_with_open_transaction"
            ),
            "open_subject_key_range_overlaps": scalar(
                diagnostics, "open_subject_key_range_overlaps"
            ),
            "remainders_recalled": remainders,
            "compilation_verified": compiled,
            "candidate_pairs_seen": len(ledger),
            "targets_proposed": extractor.get("targets_proposed"),
            "realized_firing_rate": (
                round(sum(e["named"] for e in ledger) / len(ledger), 6)
                if ledger else None
            ),
            "paid_model_calls": report.get("paid_model_calls"),
        }
        row = arms[name]
        if expect and edges <= 0:
            failures.append(
                f"{name}: INERT -- supersedes_edges = {edges}. The arm never "
                "fired; 'inert' is the whole report and no accuracy number may "
                "be read from it."
            )
        if not expect and edges != 0:
            failures.append(
                f"{name}: no-op isolator minted {edges} supersede edges -- it "
                "is not a no-op"
            )
        if remainders != 0:
            failures.append(f"{name}: remainders_recalled = {remainders}, must be 0")
        if row["superseded_with_open_transaction"] != 0:
            failures.append(
                f"{name}: {row['superseded_with_open_transaction']} superseded "
                "units still carry an open transaction interval"
            )
        if row["open_subject_key_range_overlaps"] != 0:
            failures.append(
                f"{name}: {row['open_subject_key_range_overlaps']} overlapping "
                "open subject-key ranges -- the exclusion constraint is not holding"
            )
        if not compiled or compiled.get("failed_jobs", 1) != 0 or compiled.get(
            "pending_jobs", 1
        ) != 0:
            failures.append(f"{name}: corpus compilation not verified clean: {compiled}")
        if row["paid_model_calls"]:
            failures.append(f"{name}: paid_model_calls = {row['paid_model_calls']}")

    # SAME TREE, SAME BINARIES, SAME HAYSTACK, SAME BANK.
    def distinct(getter):
        return sorted({json.dumps(getter(a), sort_keys=True) for a in arms.values()})

    checks = {
        "git_head": distinct(lambda a: (a["lineage"] or {}).get("git_head")),
        "server_bin_sha256": distinct(lambda a: (a["lineage"] or {}).get("server_bin_sha256")),
        "worker_bin_sha256": distinct(lambda a: (a["lineage"] or {}).get("worker_bin_sha256")),
        "corpus_sha256": distinct(lambda a: a["corpus_sha256"]),
        "probes": distinct(lambda a: a["probes"]),
        "instances": distinct(lambda a: a["instances"]),
    }
    for key, values in checks.items():
        if len(values) != 1:
            failures.append(f"ARMS DO NOT SHARE {key}: {values}")

    gate = {
        "gate": "s1_mechanism_liveness",
        "passed": not failures,
        "failures": failures,
        "shared": {k: json.loads(v[0]) if len(v) == 1 else v for k, v in checks.items()},
        "arms": arms,
        "rule": "Checked before any accuracy number is read. A treatment arm "
                "with zero supersede edges is INERT and 'inert' is the whole "
                "report. remainders_recalled must be 0. Compilation is verified "
                "from the DB on the bench superuser credential.",
        "paid_model_calls": 0,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n")

    for name, row in sorted(arms.items()):
        print(
            f"{name:12s} edges {row['supersedes_edges']:>6}  superseded "
            f"{row['superseded_units']:>6}  remainders {row['remainders_recalled']}  "
            f"unit {str(row['unit']):8s} tau {row['threshold']}  "
            f"fired {row['targets_proposed']}/{row['candidate_pairs_seen']} "
            f"= {row['realized_firing_rate']}"
        )
    if failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nGATE PASSED -- accuracy numbers may now be read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
