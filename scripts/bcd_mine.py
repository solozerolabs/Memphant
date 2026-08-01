#!/usr/bin/env python3
"""Census and arc-mining over MDN browser-compat-data (BCD).

Task S7. BCD is proposed in `docs/build-log/2026-08-01-discriminating-instrument-survey.md`
§4b as the coding instrument for the four regimes MemoryCode cannot express:
(a) re-assertion, (b) non-recency currency, (c) bounded validity, (d) expensive
wrong retirement.

Two things this file exists to do, in order:

1. **Reproduce the survey's census** from the pinned `data.json`, including the
   §4b.4 false-positive gate: the naive "removed then later added" query returns
   ~1,279 features, of which ~75% are `partial_implementation` upgrades and
   similar artifacts. Only after excluding `flags` / `prefix` /
   `alternative_name` / `partial_implementation` and requiring a STRICT version
   gap does the defensible arc set (~325 features / ~704 browser-pairs) fall
   out. Any run reporting a number strictly between the naive and filtered
   counts has not applied the filter.

2. **Emit the bank**: arc features (regime a) plus two control classes
   (never-removed, removed-and-stayed-removed), with the gold taken verbatim
   from BCD. We author episodes; we do NOT author gold.

No network. Reads the mirrored, sha-pinned `data.json` only.

Usage:
    python3 scripts/bcd_mine.py --census
    python3 scripts/bcd_mine.py --bank OUT.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_DATA = Path.home() / ".memphant-private/w7-instruments/browser-compat-data/pkg/package/data.json"

# BCD top-level namespaces that carry __compat feature nodes. `browsers` is the
# browser-release metadata tree and `__meta` the build stamp; both are excluded.
# `manifests` (38) and `mediatypes` (17) are small but real feature trees --
# omitting them reproduces 20,188 instead of the survey's 20,243, and that
# 55-feature gap is exactly these two.
FEATURE_ROOTS = (
    "api",
    "css",
    "html",
    "http",
    "javascript",
    "manifests",
    "mathml",
    "mediatypes",
    "svg",
    "webassembly",
    "webdriver",
    "webextensions",
)

# Modifier keys that make a support statement something other than a plain
# "this feature works here" assertion. The §4b.4 filter drops any statement
# carrying one of these on either side of an arc.
MODIFIER_KEYS = ("flags", "prefix", "alternative_name", "partial_implementation")

_VERSION_RE = re.compile(r"^[<>=≤≥]*\s*([0-9]+(?:\.[0-9]+)*)")


def parse_version(v):
    """Return a comparable tuple for a BCD version string, or None.

    BCD versions are release strings ("17.3.0", "1", "16.14"), sometimes
    range-prefixed ("≤37", "≤12.1"), sometimes non-numeric ("preview"), and
    sometimes booleans (True = "supported, version unknown"). Only strings with
    a numeric core are orderable; everything else returns None and is treated as
    un-orderable by the strict-gap test, which fails closed (drops the pair).
    """
    if not isinstance(v, str):
        return None
    m = _VERSION_RE.match(v.strip())
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def walk_features(node, path=()):
    """Yield (dotted_path, compat_dict) for every __compat node in the tree."""
    if not isinstance(node, dict):
        return
    if "__compat" in node and isinstance(node["__compat"], dict):
        yield ".".join(path), node["__compat"]
    for key, child in node.items():
        if key == "__compat":
            continue
        if isinstance(child, dict):
            yield from walk_features(child, path + (key,))


def statements(support_value):
    """Normalise a BCD support value to a list of statement dicts."""
    if isinstance(support_value, dict):
        return [support_value]
    if isinstance(support_value, list):
        return [s for s in support_value if isinstance(s, dict)]
    return []


def has_modifier(stmt):
    return any(stmt.get(k) for k in MODIFIER_KEYS)


def find_arc(stmts):
    """Detect a removed-then-re-added arc in one browser's statement list.

    Returns a dict describing the arc, or None. Two levels are reported:

      naive  -- some statement has a truthy `version_removed`, and some other
                statement has a `version_added` (the query §4b.4 says returns
                ~1,279 features and is ~75% false positive).
      strict -- additionally: neither the removed-side nor the re-added-side
                statement carries a modifier key, both versions parse, and the
                re-added version is STRICTLY GREATER than the removed version.

    BCD orders a support array newest-first, but this function does not rely on
    that ordering; it compares parsed versions directly.
    """
    removed_side = [s for s in stmts if s.get("version_removed")]
    if not removed_side:
        return None

    naive_any = False
    naive_ordered = False
    best = None
    for rem in removed_side:
        rv = parse_version(rem.get("version_removed"))
        for add in stmts:
            if add is rem:
                continue
            av_raw = add.get("version_added")
            if not av_raw:
                continue
            naive_any = True
            av = parse_version(av_raw)
            if rv is None or av is None:
                continue
            if av >= rv:
                # The survey's first pass: "version_removed followed by a later
                # version_added", with NO modifier filter and NO strict gap.
                # `av == rv` is the AbortController-in-Safari-12.1 trap -- a
                # partial_implementation upgraded to full support at the very
                # version recorded as the "removal".
                naive_ordered = True
            if has_modifier(rem) or has_modifier(add):
                continue
            if av > rv:  # strict gap: re-added at a LATER version than removal
                cand = {
                    "removed_at": rem.get("version_removed"),
                    "live_from": rem.get("version_added"),
                    "readded_at": av_raw,
                    "readded_removed": add.get("version_removed") or None,
                }
                if best is None or parse_version(cand["readded_at"]) < parse_version(best["readded_at"]):
                    best = cand
    if not naive_any:
        return None
    return {"naive_any": naive_any, "naive_ordered": naive_ordered, "strict": best}


def census(data):
    out = {
        "features": 0,
        "support_statements": 0,
        "bounded_validity_statements": 0,
        "deprecated_features": 0,
        "runtimes": set(),
        "naive_any_arc_features": set(),
        "naive_any_arc_pairs": 0,
        "naive_ordered_arc_features": set(),
        "naive_ordered_arc_pairs": 0,
        "strict_arc_features": set(),
        "strict_arc_pairs": 0,
        "strict_arc_pairs_by_browser": {},
        "strict_arc_features_by_root": {},
    }
    for root in FEATURE_ROOTS:
        if root not in data:
            continue
        for path, compat in walk_features(data[root], (root,)):
            out["features"] += 1
            status = compat.get("status") or {}
            if status.get("deprecated"):
                out["deprecated_features"] += 1
            support = compat.get("support") or {}
            for browser, value in support.items():
                stmts = statements(value)
                if stmts:
                    out["runtimes"].add(browser)
                out["support_statements"] += len(stmts)
                for s in stmts:
                    if s.get("version_added") and s.get("version_removed"):
                        out["bounded_validity_statements"] += 1
                arc = find_arc(stmts)
                if arc is None:
                    continue
                out["naive_any_arc_features"].add(path)
                out["naive_any_arc_pairs"] += 1
                if arc["naive_ordered"]:
                    out["naive_ordered_arc_features"].add(path)
                    out["naive_ordered_arc_pairs"] += 1
                if arc["strict"]:
                    out["strict_arc_features"].add(path)
                    out["strict_arc_pairs"] += 1
                    out["strict_arc_pairs_by_browser"][browser] = (
                        out["strict_arc_pairs_by_browser"].get(browser, 0) + 1
                    )
                    out["strict_arc_features_by_root"].setdefault(root, set()).add(path)
    return out


def build_bank(data):
    """Emit arc cases plus the two control classes.

    Gold is BCD's own annotation. For each case we record the (feature, browser)
    scope and the version-indexed support intervals; the probe asks whether the
    feature is supported at a named version, which BCD answers, not us.
    """
    arcs, never_removed, stayed_removed = [], [], []
    for root in FEATURE_ROOTS:
        if root not in data:
            continue
        for path, compat in walk_features(data[root], (root,)):
            status = compat.get("status") or {}
            support = compat.get("support") or {}
            for browser, value in support.items():
                stmts = statements(value)
                if not stmts:
                    continue
                clean = [s for s in stmts if not has_modifier(s)]
                arc = find_arc(stmts)
                if arc and arc["strict"]:
                    a = arc["strict"]
                    arcs.append(
                        {
                            "class": "arc",
                            "feature": path,
                            "root": root,
                            "browser": browser,
                            "live_from": a["live_from"],
                            "removed_at": a["removed_at"],
                            "readded_at": a["readded_at"],
                            "readded_removed": a["readded_removed"],
                            "deprecated": bool(status.get("deprecated")),
                            "mdn_url": compat.get("mdn_url"),
                        }
                    )
                    continue
                if len(clean) != 1:
                    continue
                s = clean[0]
                added, removed = s.get("version_added"), s.get("version_removed")
                if not isinstance(added, str) or parse_version(added) is None:
                    continue
                if not removed:
                    never_removed.append(
                        {
                            "class": "never_removed",
                            "feature": path,
                            "root": root,
                            "browser": browser,
                            "live_from": added,
                            "deprecated": bool(status.get("deprecated")),
                            "mdn_url": compat.get("mdn_url"),
                        }
                    )
                elif isinstance(removed, str) and parse_version(removed) is not None:
                    if parse_version(removed) > parse_version(added):
                        stayed_removed.append(
                            {
                                "class": "stayed_removed",
                                "feature": path,
                                "root": root,
                                "browser": browser,
                                "live_from": added,
                                "removed_at": removed,
                                "deprecated": bool(status.get("deprecated")),
                                "mdn_url": compat.get("mdn_url"),
                            }
                        )
    return {"arc": arcs, "never_removed": never_removed, "stayed_removed": stayed_removed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--census", action="store_true")
    ap.add_argument("--bank", type=Path)
    args = ap.parse_args()

    data = json.loads(args.data.read_text())

    if args.census:
        c = census(data)
        any_f = len(c["naive_any_arc_features"])
        naive_f = len(c["naive_ordered_arc_features"])
        strict_f = len(c["strict_arc_features"])
        print(f"compat features                       {c['features']:>10,}")
        print(f"support statements                    {c['support_statements']:>10,}")
        print(f"bounded-validity statements           {c['bounded_validity_statements']:>10,}")
        print(f"status.deprecated features            {c['deprecated_features']:>10,}")
        print(f"runtimes                              {len(c['runtimes']):>10,}")
        print(f"L0 any removed + any added (features) {any_f:>10,}")
        print(f"L0 pairs                              {c['naive_any_arc_pairs']:>10,}")
        print(f"L1 NAIVE removed-then-added (features){naive_f:>10,}")
        print(f"L1 pairs                              {c['naive_ordered_arc_pairs']:>10,}")
        print(f"L2 STRICT arc features (§4b.4 filter) {strict_f:>10,}")
        print(f"L2 STRICT arc browser-pairs           {c['strict_arc_pairs']:>10,}")
        if naive_f:
            print(f"false-positive rate of L1 naive query {100 * (1 - strict_f / naive_f):>9.1f}%")
        print("strict pairs by browser:")
        for b, n in sorted(c["strict_arc_pairs_by_browser"].items(), key=lambda kv: -kv[1]):
            print(f"    {b:<24} {n:>5}")
        print("strict features by namespace:")
        for r, s in sorted(c["strict_arc_features_by_root"].items(), key=lambda kv: -len(kv[1])):
            print(f"    {r:<24} {len(s):>5}")

    if args.bank:
        bank = build_bank(data)
        args.bank.write_text(json.dumps(bank, indent=1, sort_keys=True))
        for k, v in bank.items():
            print(f"{k:<20} {len(v):>8,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
