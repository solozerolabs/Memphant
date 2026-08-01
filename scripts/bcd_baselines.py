#!/usr/bin/env python3
"""The trivial-baseline ceiling for the BCD instrument -- run BEFORE any arm.

S6's lesson, paid in full: an as-of re-cut of MemoryCode successfully broke
recency identification (`max(observed_at)` fell to 0.0000) and was STILL
saturated by a ~20-line read rule (`max(observed_at <= t)` scored 0.9064).
Breaking the recency baseline is necessary and nowhere near sufficient. So the
first thing measured on BCD is not a substrate arm; it is the ceiling of the
cheapest rules that need no substrate at all.

Probe construction. For each strict removed-then-re-added arc
(feature, browser, live_from L, removed_at R, readded_at A) BCD asserts three
intervals, and we ask the same question in each:

    band B1   L <= V < R    feature IS supported     (gold = the "added at L" fact)
    band B2   R <= V < A    feature is NOT supported (gold = the "removed at R" fact)
    band B3   V >= A        feature IS supported     (gold = the "restored at A" fact)

B2 is the discriminating band and the reason BCD was chosen: the NEWEST
assertion about the feature (restored at A) is the WRONG answer there.

The gold is BCD's. The episode wording is ours. That asymmetry is stated in the
spec and it is the honest weakness of this instrument -- see `--report`, which
prints the ceiling for each rule so the reader can see exactly which trivial
rule, if any, already solves the task.

Baselines, none of which touch the substrate:

    constant_supported   always answer "supported"
    most_frequent        the modal answer over the bank
    latest_declared      trust the last episode authored in the stream
    max_version          the fact with the greatest version, query ignored
    max_version_le_V     greatest version <= V, ACROSS ALL browsers for the feature
    scoped_interval      greatest version <= V WITHIN the queried browser, honouring
                         removals -- the ~20-line rule that is the real bar

Usage:
    python3 scripts/bcd_baselines.py --bank BANK.json --report
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bcd_mine import parse_version  # noqa: E402


def bump(v, minor=1):
    """A version strictly inside the interval starting at `v`."""
    t = list(parse_version(v))
    t[-1] += minor
    return ".".join(str(x) for x in t)


def midpoint(lo, hi):
    """A version in [lo, hi). Returns None if the interval is empty of samples."""
    a, b = parse_version(lo), parse_version(hi)
    if a is None or b is None or a >= b:
        return None
    return lo  # lo itself is always in [lo, hi) and is a real shipped release


def build_probes(bank, seed=20260801):
    """One probe per (arc, band). Gold answer comes from BCD's intervals."""
    rng = random.Random(seed)
    probes = []
    for arc in bank["arc"]:
        L, R, A = arc["live_from"], arc["removed_at"], arc["readded_at"]
        if not all(isinstance(x, str) and parse_version(x) for x in (L, R, A)):
            continue
        if not (parse_version(L) < parse_version(R) < parse_version(A)):
            continue
        base = {
            "feature": arc["feature"],
            "browser": arc["browser"],
            "live_from": L,
            "removed_at": R,
            "readded_at": A,
            "cluster": arc["feature"],
        }
        for band, v, gold in (
            ("B1", midpoint(L, R), "supported"),
            ("B2", midpoint(R, A), "not_supported"),
            ("B3", bump(A), "supported"),
        ):
            if v is None:
                continue
            probes.append(dict(base, band=band, query_version=v, gold=gold))
    rng.shuffle(probes)
    return probes


# --- the facts a memory system would hold, one per BCD assertion -------------

def facts_for(probe):
    """The three scope-indexed facts BCD states about this (feature, browser).

    Authored order is chronological: added, removed, restored. `latest_declared`
    and `max_version` therefore both select the restoration.
    """
    return [
        {"kind": "added", "version": probe["live_from"], "browser": probe["browser"], "answer": "supported"},
        {"kind": "removed", "version": probe["removed_at"], "browser": probe["browser"], "answer": "not_supported"},
        {"kind": "restored", "version": probe["readded_at"], "browser": probe["browser"], "answer": "supported"},
    ]


# --- baselines ---------------------------------------------------------------

def b_constant(probe, facts, modal):
    return "supported"


def b_most_frequent(probe, facts, modal):
    return modal


def b_latest_declared(probe, facts, modal):
    return facts[-1]["answer"]


def b_max_version(probe, facts, modal):
    return max(facts, key=lambda f: parse_version(f["version"]))["answer"]


def b_max_version_le_v(probe, facts, modal):
    """Greatest version <= V, but blind to which browser the fact is scoped to.

    On a single-browser fact set this coincides with `scoped_interval`; it is
    reported separately because on the full multi-browser pool (where a
    feature's Chrome, Opera and Node facts are all in the store) the two come
    apart, and the gap is the entire value of scope-awareness.
    """
    v = parse_version(probe["query_version"])
    elig = [f for f in facts if parse_version(f["version"]) <= v]
    if not elig:
        return "not_supported"
    return max(elig, key=lambda f: parse_version(f["version"]))["answer"]


def b_scoped_interval(probe, facts, modal):
    """Filter to the queried browser, then greatest version <= V. ~20 lines."""
    v = parse_version(probe["query_version"])
    elig = [
        f
        for f in facts
        if f["browser"] == probe["browser"] and parse_version(f["version"]) <= v
    ]
    if not elig:
        return "not_supported"
    return max(elig, key=lambda f: parse_version(f["version"]))["answer"]


BASELINES = {
    "constant_supported": b_constant,
    "most_frequent": b_most_frequent,
    "latest_declared": b_latest_declared,
    "max_version": b_max_version,
    "max_version_le_V": b_max_version_le_v,
    "scoped_interval": b_scoped_interval,
}


def score(probes, cross_browser_pool=False, bank=None):
    modal = Counter(p["gold"] for p in probes).most_common(1)[0][0]
    by_browser = {}
    if cross_browser_pool and bank is not None:
        for arc in bank["arc"]:
            by_browser.setdefault(arc["feature"], []).append(arc)

    results = {name: {"hit": 0, "n": 0, "by_band": {}} for name in BASELINES}
    for p in probes:
        facts = facts_for(p)
        if cross_browser_pool and bank is not None:
            for other in by_browser.get(p["feature"], []):
                if other["browser"] == p["browser"]:
                    continue
                for k, ver in (
                    ("added", other["live_from"]),
                    ("removed", other["removed_at"]),
                    ("restored", other["readded_at"]),
                ):
                    if isinstance(ver, str) and parse_version(ver):
                        facts.append(
                            {
                                "kind": k,
                                "version": ver,
                                "browser": other["browser"],
                                "answer": "not_supported" if k == "removed" else "supported",
                            }
                        )
        for name, fn in BASELINES.items():
            ok = fn(p, facts, modal) == p["gold"]
            r = results[name]
            r["n"] += 1
            r["hit"] += ok
            band = r["by_band"].setdefault(p["band"], [0, 0])
            band[1] += 1
            band[0] += ok
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", type=Path, required=True)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    bank = json.loads(args.bank.read_text())
    probes = build_probes(bank)
    print(f"probes: {len(probes)} over {len({p['cluster'] for p in probes})} feature clusters")
    print(f"band sizes: {dict(Counter(p['band'] for p in probes))}")
    print(f"gold balance: {dict(Counter(p['gold'] for p in probes))}\n")

    for label, cross in (("single-browser fact set", False), ("cross-browser pool", True)):
        print(f"=== {label} ===")
        res = score(probes, cross_browser_pool=cross, bank=bank)
        print(f"{'baseline':<22} {'overall':>8}  {'B1':>7} {'B2':>7} {'B3':>7}")
        for name, r in res.items():
            bands = " ".join(
                f"{(r['by_band'].get(b, [0, 1])[0] / max(1, r['by_band'].get(b, [0, 1])[1])):>7.4f}"
                for b in ("B1", "B2", "B3")
            )
            print(f"{name:<22} {r['hit'] / r['n']:>8.4f}  {bands}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
