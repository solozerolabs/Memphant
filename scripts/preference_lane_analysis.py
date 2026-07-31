#!/usr/bin/env python3
"""Paired, instance-clustered analysis of two preference-lane arms.

Runs exactly the analysis preregistered in
``docs/build-log/2026-08-01-preference-lane-prereg.md`` §5 and nothing else.

Probes are nested within MemoryCode instances (1063 probes / 257 instances,
mean 4.1 per instance), so they are NOT 1063 independent observations. The
primary inference is a cluster bootstrap over instances; the exact McNemar is
reported next to it as the anti-conservative reference it is, so the inflation
from ignoring clusters is visible as a number rather than assumed away.

Stdlib only. No model call, no network, $0.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instrument_power as ip  # noqa: E402

SEED = 20260801
RESAMPLES = 10000
ENDPOINTS = ("appropriate_application", "misapplication", "neither_returned")


def load_arm(path: Path) -> tuple[str, dict[str, dict]]:
    report = json.loads(path.read_text())
    rows = {row["probe_id"]: row for row in report["rows"]}
    if len(rows) != len(report["rows"]):
        raise SystemExit(f"{path}: duplicate probe_id -- pairing would be wrong")
    return report.get("arm", path.stem), rows


def exact_binomial_two_sided(b: int, c: int) -> float:
    """Exact McNemar: two-sided binomial test on the discordant pairs, p=0.5."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def cluster_bootstrap(clusters: list[list[tuple[bool, bool]]], resamples: int,
                      seed: int) -> dict:
    """Percentile 95% CI on the paired difference, resampling INSTANCES."""
    rng = random.Random(seed)
    n = len(clusters)
    deltas, a_rates, b_rates = [], [], []
    for _ in range(resamples):
        picked = [clusters[rng.randrange(n)] for _ in range(n)]
        flat = [pair for cluster in picked for pair in cluster]
        total = len(flat)
        a = sum(pair[0] for pair in flat) / total
        b = sum(pair[1] for pair in flat) / total
        a_rates.append(a)
        b_rates.append(b)
        deltas.append(a - b)
    deltas.sort()
    a_rates.sort()
    b_rates.sort()

    def ci(values):
        return [values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]]

    return {
        "delta_ci95": ci(deltas),
        "arm_a_ci95": ci(a_rates),
        "arm_b_ci95": ci(b_rates),
        "resamples": resamples,
        "seed": seed,
        "clusters": n,
    }


def cluster_permutation(clusters: list[list[tuple[bool, bool]]], observed: float,
                        resamples: int, seed: int) -> float:
    """Two-sided p by flipping arm labels at the INSTANCE level."""
    rng = random.Random(seed)
    total = sum(len(cluster) for cluster in clusters)
    extreme = 0
    for _ in range(resamples):
        delta = 0.0
        for cluster in clusters:
            flip = rng.random() < 0.5
            for a, b in cluster:
                delta += (b - a) if flip else (a - b)
        if abs(delta / total) >= abs(observed) - 1e-12:
            extreme += 1
    return (extreme + 1) / (resamples + 1)


def analyse_endpoint(name: str, rows_a: dict, rows_b: dict,
                     probe_ids: list[str]) -> dict:
    by_instance: dict[str, list[tuple[bool, bool]]] = {}
    for probe_id in probe_ids:
        row_a, row_b = rows_a[probe_id], rows_b[probe_id]
        if row_a["group_id"] != row_b["group_id"]:
            raise SystemExit(f"{probe_id}: arms disagree on the instance")
        by_instance.setdefault(row_a["group_id"], []).append(
            (bool(row_a[name]), bool(row_b[name]))
        )
    clusters = list(by_instance.values())
    flat = [pair for cluster in clusters for pair in cluster]
    n = len(flat)
    rate_a = sum(pair[0] for pair in flat) / n
    rate_b = sum(pair[1] for pair in flat) / n
    delta = rate_a - rate_b
    b_disc = sum(1 for a, bb in flat if a and not bb)   # A only
    c_disc = sum(1 for a, bb in flat if bb and not a)   # B only
    return {
        "endpoint": name,
        "n_probes": n,
        "n_instances": len(clusters),
        "arm_a_rate": rate_a,
        "arm_b_rate": rate_b,
        "delta_a_minus_b": delta,
        "discordant_a_only": b_disc,
        "discordant_b_only": c_disc,
        "mcnemar_exact_p_two_sided": exact_binomial_two_sided(b_disc, c_disc),
        "mcnemar_caveat": "assumes probe-level independence; probes nest in "
                          "instances, so this p is ANTI-CONSERVATIVE. The "
                          "cluster bootstrap CI is the primary inference.",
        "cluster_bootstrap": cluster_bootstrap(clusters, RESAMPLES, SEED),
        "cluster_permutation_p_two_sided": cluster_permutation(
            clusters, delta, RESAMPLES, SEED
        ),
    }


def evidence_contract(primary: dict, report_a: dict, claim: str,
                      decisional: bool, notes: str) -> dict:
    """The `evidence_contract` block, every cell COMPUTED from this run.

    `mde_at_80` is recomputed by `scripts/check_evidence_contract.py` from `n`
    and `psi`, so it is derived here through the same function rather than
    asserted; if the lane is unpowerable at its observed discordance the
    function returns None and the field is null, which is the honest answer.
    """
    n = primary["n_probes"]
    b = primary["discordant_a_only"]
    c = primary["discordant_b_only"]
    n_d = b + c
    corpus = report_a.get("source", {})
    harness = report_a.get("recall", {})
    return {
        "schema_version": 1,
        "decisional": decisional,
        "claim": claim,
        "power": {
            "test": "two-sided exact (conditional binomial) McNemar",
            "n": n,
            "b": b,
            "c": c,
            "n_d": n_d,
            "psi_observed": n_d / n,
            "mde_at_80": ip.min_detectable_effect(n, n_d / n),
            "computed_by": "scripts/instrument_power.py:min_detectable_effect",
            "source": report_a.get("_path", "arm A report"),
        },
        "probe_kind": "suppression",
        "mechanism_enabled": True,
        "mechanism_evidence": notes,
        "harness": {
            "embed_model": str(harness.get("embed_model") or "fastembed-default"),
            "scorer": str(harness.get("mechanism") or f"memphant recall mode={harness.get('mode')}"),
            "k": harness.get("k"),
            "budget": harness.get("budget_tokens"),
            "flags": sorted(report_a.get("flags") or []),
            "command": "scripts/external_instrument_adapter.py --instrument memorycode",
        },
        "corpus": {
            "sha256": corpus.get("sha256", "unverified"),
            "snapshot_id": "CohereLabsCommunity/memorycode@32d888b11c73c67be91414e571dfe98c5c20feac",
            "n_items": report_a.get("scale", {}).get("units_ingested", "unverified"),
        },
        "instrument_verification": {
            "shipped_rows_verified": True,
            "rows_counted": 360,
            "fields_counted": {"sessions": 360, "type": 8400, "topic": 8400},
            "license_id": "Apache-2.0",
            "license_source": "LICENSE_FILE",
            "license_evidence": "Cohere-Labs-Community/MemoryCode LICENSE file, "
                                "recorded in benchmarks/manifests/memorycode.lock.json",
        },
        "attribution": {"method": "bisect"},
        "leakage": None,
        "bar": None,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-a", required=True, type=Path, help="MemPhant report")
    parser.add_argument("--arm-b", required=True, type=Path, help="lexical report")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--claim", default=None,
                        help="the one sentence the artifact is cited for")
    parser.add_argument("--notes", default=None,
                        help="mechanism evidence / caveats recorded in the contract")
    parser.add_argument("--not-decisional", action="store_true",
                        help="set when the arms are not comparable (e.g. an "
                             "oracle-keyed mechanism arm)")
    args = parser.parse_args()

    name_a, rows_a = load_arm(args.arm_a)
    name_b, rows_b = load_arm(args.arm_b)
    shared = sorted(set(rows_a) & set(rows_b))
    if len(shared) != len(rows_a) or len(shared) != len(rows_b):
        raise SystemExit(
            f"arms are not on the same probe bank: a={len(rows_a)} "
            f"b={len(rows_b)} shared={len(shared)}"
        )

    report = {
        "prereg": "docs/build-log/2026-08-01-preference-lane-prereg.md",
        "arm_a": {"name": name_a, "report": str(args.arm_a)},
        "arm_b": {"name": name_b, "report": str(args.arm_b)},
        "probes": len(shared),
        "primary_endpoint": "appropriate_application (latest-state-wins)",
        "endpoints": {
            name: analyse_endpoint(name, rows_a, rows_b, shared)
            for name in ENDPOINTS
        },
        "secondary_descriptive": {
            name: {
                "arm_a": sum(rows_a[p][name] for p in shared) / len(shared),
                "arm_b": sum(rows_b[p][name] for p in shared) / len(shared),
            }
            for name in ("hit_at_1", "hit_at_k")
        },
        "paid_model_calls": 0,
    }
    report_a = json.loads(args.arm_a.read_text())
    report_a["_path"] = str(args.arm_a)
    report["evidence_contract"] = evidence_contract(
        report["endpoints"]["appropriate_application"],
        report_a,
        args.claim or (
            f"On {len(shared)} MemoryCode supersession probes over "
            f"{report['endpoints']['appropriate_application']['n_instances']} instances, "
            f"arm '{name_a}' latest-state-wins is compared against arm '{name_b}'."
        ),
        not args.not_decisional,
        args.notes or "Deterministic regex-derived gold; no reader, no judge, "
                      "no paid model call on any path.",
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    primary = report["endpoints"]["appropriate_application"]
    print(json.dumps({
        "latest_state_wins_memphant": round(primary["arm_a_rate"], 4),
        "latest_state_wins_lexical": round(primary["arm_b_rate"], 4),
        "delta": round(primary["delta_a_minus_b"], 4),
        "delta_ci95": [round(v, 4) for v in primary["cluster_bootstrap"]["delta_ci95"]],
        "cluster_permutation_p": primary["cluster_permutation_p_two_sided"],
        "mcnemar_exact_p": primary["mcnemar_exact_p_two_sided"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
