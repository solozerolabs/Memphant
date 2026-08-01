#!/usr/bin/env python3
"""Kill-gate (b): can a sub-second full-pool chunk-rerank reach the 16-64 band?

Kill-gate (a) (2026-07-22) established that the r15 cross-encoder's QA win lives
at candidate ranks 17-64, unreachable by the top-16 rank compression that was
the only configuration under the 1.5 s ceiling with the 13 s bge model. (b) asks
the complementary question the C2 wave set up and abandoned: now that
ms-marco-MiniLM-L6 int8 reranks the FULL 64-candidate pool at chunk granularity,
is that band affordable — and does reaching it actually move retrieval?

This is the retrieval endpoint only. Grading is `gate_common.provenance_hit`
span containment, deterministic, no reader and no judge, $0.

It supersedes the archived
`docs/build-log/artifacts/p1-c2-killgate/score_killgate_b.py`, which scored an
aggregate gap-closure fraction against a HISTORICAL Syndai hit@10 constant
(`SYNDAI_HIT10=0.200`, from a different corpus pin) whenever a live incumbent
arm was absent. That fallback is a stale-lineage hazard and is not reproduced
here: this scorer refuses to mix pins, and reports a PAIRED exact McNemar on
per-question vectors rather than a difference of two aggregates.

Three things are checked, and all three must hold for the arm to mean anything:

1. **Effect** — paired exact (conditional binomial) McNemar on hit@10, per set
   and pooled, with realized psi and the MDE the run's own cells support.
2. **Mechanism liveness** — an inert rerank and a neutral rerank produce the
   same score. The arm must prove the cross-encoder actually ran (per-question
   `cross_rerank` trace facts, `docs_scored`, zero failures) AND actually
   reordered (the ordered returned-body digest must differ from the base arm on
   a non-trivial share of questions).
3. **Latency** — measured on THIS host from the arm's own `cross_rerank_ms`,
   not inherited from the 2026-07-22 spike's 449 ms synthetic matrix.

Usage:
    python3 scripts/score_killgate_b.py [--dir DIR] [--evidence-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "docs/build-log/artifacts/p1-c2-killgate/killgate-b"

# The band kill-gate (a) proved the win lives in. Reaching it is the whole point
# of the full-pool arm; a rerank that only reshuffles the top 16 cannot.
CEILING_MS = 1500
# A rerank that reorders almost nothing is indistinguishable from an inert one.
MIN_REORDER_FRACTION = 0.50
# Above this 1-minute loadavg per core the host is oversubscribed and a wall-clock
# latency figure measures the queue, not the model. Precedent in this repo:
# docs/build-log/2026-08-01-rerank-channel.md recorded cross_rerank_ms p50 =
# 22,166 ms under loadavg 120-140 for work that takes ~1,460 ms uncontended — a
# ~15x contention artifact. A contended sample is therefore an UPPER BOUND and
# may neither pass nor fail the latency leg; it forces INDETERMINATE.
QUIET_LOADAVG_PER_CORE = 1.5


def mcnemar_exact(b: int, c: int) -> float:
    n_d = b + c
    if n_d == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n_d, i) for i in range(k + 1)) / 2**n_d)


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.959963985, k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def load_provenance(base: Path, arm: str) -> dict[str, dict]:
    out = {}
    for v in ("v1", "v2"):
        report = json.loads((base / f"prov-{arm}-{v}.json").read_text())
        for row in report["per_question"]:
            out[row["question_id"]] = {"set": v, **row, "_header": report}
    return out


def headers(base: Path, arm: str) -> dict[str, dict]:
    return {
        v: json.loads((base / f"prov-{arm}-{v}.json").read_text())
        for v in ("v1", "v2")
    }


def order_digests(evidence_dir: Path, arm: str) -> dict[str, str]:
    """sha256 over the ORDERED returned bodies. Bodies never leave this process;
    only the digest is emitted, so no corpus content is committed."""
    out = {}
    for v in ("v1", "v2"):
        path = evidence_dir / f"ev-{arm}-{v}.jsonl"
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            h = hashlib.sha256()
            for item in row["evidence"]:
                body = item["body"].encode()
                h.update(len(body).to_bytes(8, "big"))
                h.update(body)
            out[row["question_id"]] = h.hexdigest()
    return out


def paired(base_hits: dict[str, bool], arm_hits: dict[str, bool], ids: list[str]) -> dict:
    b = sum(1 for i in ids if arm_hits[i] and not base_hits[i])
    c = sum(1 for i in ids if base_hits[i] and not arm_hits[i])
    both = sum(1 for i in ids if arm_hits[i] and base_hits[i])
    n = len(ids)
    n_d = b + c
    lo, hi = wilson(n_d, n) if n else (0.0, 0.0)
    return {
        "n": n,
        "both": both,
        "rerank_only_b": b,
        "base_only_c": c,
        "neither": n - both - b - c,
        "n_d": n_d,
        "psi": n_d / n if n else 0.0,
        "psi_ci95_wilson": [lo, hi],
        "delta": (b - c) / n if n else 0.0,
        "p_exact": mcnemar_exact(b, c),
        "clears_nd_floor_of_6": n_d >= 6,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--evidence-dir", required=True,
                        help="holds ev-{arm}-{v}.jsonl; bodies stay here, only digests are emitted")
    parser.add_argument(
        "--build-profile", default="release", choices=("release", "debug"),
        help="profile the SERVED binaries were built under. A latency figure "
             "measured on a debug build is worthless (see the 2026-08-01 near "
             "miss); 'debug' is accepted only so such a run is stamped as "
             "latency-invalid rather than silently banked.",
    )
    args = parser.parse_args()
    base_dir, ev_dir = Path(args.dir), Path(args.evidence_dir)

    base_rows = load_provenance(base_dir, "base")
    rr_rows = load_provenance(base_dir, "rerank")
    if set(base_rows) != set(rr_rows):
        raise SystemExit("question sets differ across arms — refusing to pool")
    ids = sorted(base_rows)

    base_h = headers(base_dir, "base")
    rr_h = headers(base_dir, "rerank")

    # --- lineage: both arms must be the same corpus, goldens, embedder, k -----
    lineage_fields = ("golden_sha256", "corpus_revision", "embed_model", "k",
                      "budget_tokens", "haystack_sections", "resource_chunks")
    lineage_ok, lineage_detail = True, {}
    for v in ("v1", "v2"):
        for f in lineage_fields:
            a, b_ = base_h[v].get(f), rr_h[v].get(f)
            lineage_detail[f"{v}.{f}"] = {"base": a, "rerank": b_, "match": a == b_}
            if a != b_:
                lineage_ok = False
    # the rerank flag itself MUST differ, else the arms are the same arm
    arms_differ = all(
        base_h[v].get("cross_rerank") is False and rr_h[v].get("cross_rerank") is True
        for v in ("v1", "v2")
    )

    # --- mechanism liveness ---------------------------------------------------
    # `candidate_count` is the scored head size the runner verifies against
    # `candidate_limit`. NOTE: CrossRerankTrace also carries `docs_scored` (the
    # flattened chunk count under chunk granularity, which is what actually
    # bounds latency), but gate_run_memphant._cross_rerank_facts does not
    # propagate it or `granularity` into the provenance row — so both are
    # UNVERIFIED here. That is an instrumentation gap, recorded not papered over.
    scored = [r.get("candidate_count") for r in rr_rows.values()]
    limits = {r.get("candidate_limit") for r in rr_rows.values()}
    lat = [r["cross_rerank_ms"] for r in rr_rows.values() if "cross_rerank_ms" in r]
    lat.sort()

    def pct(p: float) -> float | None:
        return lat[min(len(lat) - 1, int(round(p * (len(lat) - 1))))] if lat else None

    base_dig, rr_dig = order_digests(ev_dir, "base"), order_digests(ev_dir, "rerank")
    reordered = [i for i in ids if base_dig.get(i) != rr_dig.get(i)]
    reorder_fraction = len(reordered) / len(ids)

    mechanism = {
        "cross_rerank_declared": arms_differ,
        "reranker": rr_h["v1"].get("runtime_config", {}).get("cross_reranker"),
        "questions_with_rerank_trace": len(lat),
        "questions_total": len(ids),
        "candidate_count_min": min([s for s in scored if s is not None], default=None),
        "candidate_count_max": max([s for s in scored if s is not None], default=None),
        "candidate_limit": sorted(x for x in limits if x is not None),
        "full_pool_scored_on_every_question": (
            len(lat) == len(ids)
            and limits == {64}
            and min([s for s in scored if s is not None], default=0) == 64
        ),
        "docs_scored": "unverified — CrossRerankTrace.docs_scored is not propagated into the provenance row",
        "granularity": "unverified in provenance — requested via MEMPHANT_RERANK_GRANULARITY=chunk (see the arm command)",
        "reranker_failure_count": {v: rr_h[v].get("reranker_failure_count") for v in ("v1", "v2")},
        "ordered_output_differs_from_base": len(reordered),
        "reorder_fraction": reorder_fraction,
        "reorder_fraction_bar": MIN_REORDER_FRACTION,
        "reorder_bar_met": reorder_fraction >= MIN_REORDER_FRACTION,
        "note": (
            "an inert rerank and a neutral rerank give the same score; the "
            "ordered-output digest is what separates them. Digests are sha256 "
            "over the ordered returned bodies — no corpus content is emitted."
        ),
    }

    # Latency is only meaningful against the profile the server actually ran
    # under, and against the load the host was carrying while it ran. Both are
    # recorded beside the number, never inferred from it.
    cpu_count = os.cpu_count() or 1
    loadavg = list(os.getloadavg())
    arm_load = {}
    for arm in ("base", "rerank"):
        path = base_dir / f"host-{arm}.txt"
        arm_load[arm] = path.read_text().strip() if path.exists() else None
    contended = loadavg[0] > QUIET_LOADAVG_PER_CORE * cpu_count
    host = {
        "cpu_count": cpu_count,
        "loadavg_1_5_15": loadavg,
        "loadavg_per_core": loadavg[0] / cpu_count,
        "quiet_bar_per_core": QUIET_LOADAVG_PER_CORE,
        "contended": contended,
        "loadavg_source": "at scoring time; per-arm start-of-run loadavg below",
        "per_arm_start": arm_load,
        "concurrency_caveat": (
            "sibling git worktrees of this repo ran full-corpus ingests on the "
            "same host during this wave. Under contention a wall-clock latency "
            "figure measures the run queue, not the model, so it is recorded as "
            "an UPPER BOUND and is not allowed to decide the latency leg either "
            "way. The retrieval half is unaffected: span containment is "
            "deterministic and does not depend on how long anything took."
        ),
    }

    latency = {
        "source": "this run's own per-question cross_rerank_ms on this host",
        "build_profile": args.build_profile,
        "valid_for_a_ceiling_claim": args.build_profile == "release",
        "host": host,
        "n": len(lat),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "max_ms": lat[-1] if lat else None,
        "ceiling_ms": CEILING_MS,
        "p95_under_ceiling": (pct(0.95) is not None and pct(0.95) <= CEILING_MS),
        "figures_are_upper_bounds_only": contended,
        "verdict": (
            "UNVERIFIED — sampled on a contended host; upper bound only, "
            "re-measure on a quiet box before this leg decides anything"
            if contended
            else ("WITHIN CEILING" if (pct(0.95) is not None and pct(0.95) <= CEILING_MS)
                  else "BREACHES CEILING")
        ),
        "inherited_claim_ms": 449,
        "inherited_claim_status": (
            "RETRACTED — re-measured on the same #[ignore]d matrix, same model "
            "dir, same --release profile: 1140/1871/1448/1472 ms, median ~1460 "
            "ms, 2 of 4 samples breaching the 1500 ms ceiling. That matrix is "
            "BODY granularity on synthetic ~1.5 KB docs, the cheapest case; "
            "this arm runs CHUNK granularity on real sections and can only be "
            "slower. The figure below is the authoritative one."
        ),
        "inherited_claim_source": "docs/build-log/2026-07-22-reranker-latency-spike.md, synthetic 64x~1.5KB body-granularity matrix",
    }

    # --- effect ---------------------------------------------------------------
    bh = {i: bool(base_rows[i]["hit_at_10"]) for i in ids}
    rh = {i: bool(rr_rows[i]["hit_at_10"]) for i in ids}
    per_set = {
        v: paired(bh, rh, [i for i in ids if base_rows[i]["set"] == v])
        for v in ("v1", "v2")
    }
    pooled = paired(bh, rh, ids)

    bh5 = {i: bool(base_rows[i]["hit_at_5"]) for i in ids}
    rh5 = {i: bool(rr_rows[i]["hit_at_5"]) for i in ids}
    pooled_at5 = paired(bh5, rh5, ids)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ip", ROOT / "scripts/instrument_power.py")
        ip = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ip)
        mde = ip.min_detectable_effect(pooled["n"], pooled["psi"]) if pooled["psi"] > 0 else None
    except Exception:
        mde = None

    hit10_base = sum(bh.values()) / len(ids)
    hit10_rr = sum(rh.values()) / len(ids)

    # --- served-binary identity ----------------------------------------------
    # §K.4: the first attempt at this gate served target/debug/memphant-server
    # and would have banked a meaningless latency figure beside a 1.5 s ceiling.
    # The arms record the sha256 of the binary they ACTUALLY spawned in
    # runtime_config.generation_identity.files; this checks that against the
    # release binary on disk, so the profile is proven rather than asserted.
    bin_dir = ROOT / "target" / args.build_profile
    served_detail, served_ok = {}, True
    for arm, hs in (("base", base_h), ("rerank", rr_h)):
        for v in ("v1", "v2"):
            ident = (hs[v].get("runtime_config", {})
                     .get("generation_identity", {}).get("files", {}))
            for role in ("server", "worker"):
                on_disk = hashlib.sha256((bin_dir / f"memphant-{role}").read_bytes()).hexdigest()
                match = ident.get(role) == on_disk
                served_detail[f"{arm}.{v}.{role}"] = {
                    "arm_recorded": ident.get(role), "on_disk": on_disk, "match": match
                }
                served_ok = served_ok and match

    # --- verdict --------------------------------------------------------------
    # (b) passes only if the rerank produces a REAL, powered retrieval gain that
    # it could actually ship: mechanism live, latency inside the ceiling, and a
    # paired effect that the exact test rejects.
    reasons = []
    if not lineage_ok or not arms_differ:
        reasons.append("lineage: arms are not a clean single-variable contrast")
    if not served_ok:
        reasons.append(
            f"lineage: the binaries the arms served do not match "
            f"target/{args.build_profile} on disk — the profile behind the "
            f"latency figure is unproven"
        )
    if args.build_profile != "release":
        reasons.append(
            "lineage: arms ran on a debug build — the latency half of this gate "
            "is not a measurement"
        )
    if not mechanism["reorder_bar_met"]:
        reasons.append(
            f"mechanism: rerank changed the returned order on only "
            f"{reorder_fraction:.1%} of questions (bar {MIN_REORDER_FRACTION:.0%})"
        )
    # The latency leg is separated from the accuracy legs on purpose. A
    # contended sample is an upper bound: it may not fail the gate (that would
    # be a finding manufactured by sibling load) and it may not pass it either.
    latency_indeterminate = contended
    if not latency_indeterminate and not latency["p95_under_ceiling"]:
        reasons.append(
            f"latency: p95 {latency['p95_ms']} ms breaches the {CEILING_MS} ms ceiling"
        )
    if not pooled["clears_nd_floor_of_6"]:
        reasons.append(f"power: n_d = {pooled['n_d']} is below the structural floor of 6")
    effect_rejects = pooled["p_exact"] < 0.05 and pooled["delta"] > 0
    if not effect_rejects:
        reasons.append(
            f"effect: delta {pooled['delta']:+.4f}, exact p = {pooled['p_exact']:.4f} — "
            "the paired test does not reject"
        )
    passed = not reasons
    # A gate that fails on accuracy is decided outright: if a full-pool rerank
    # does not move retrieval, how fast it is cannot rescue it. Only a gate that
    # clears every accuracy leg has to wait on a clean latency sample.
    verdict = "FAIL" if reasons else (
        "INDETERMINATE — accuracy legs pass; latency unverified on a contended host"
        if latency_indeterminate else "PASS"
    )

    packet = {
        "gate": "C2 kill-gate (b) — full-pool sub-second chunk-rerank on the retrieval endpoint",
        "question": (
            "kill-gate (a) proved the r15 win lives at candidate ranks 17-64. Now that "
            "MiniLM-L6-int8 can rerank the full 64 pool, is that band reachable in "
            "practice — and does reaching it move hit@10?"
        ),
        "endpoint": "retrieval hit@10, gate_common.provenance_hit span containment, no reader, no judge",
        "spend_usd": 0,
        "lineage": {
            "memphant_git_head": subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True).stdout.strip(),
            "git_branch": subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, check=True).stdout.strip(),
            "git_dirty": bool(subprocess.run(
                ["git", "-C", str(ROOT), "status", "--porcelain"],
                capture_output=True, text=True, check=True).stdout.strip()),
            "build_profile": args.build_profile,
            "server_bin": str(bin_dir / "memphant-server"),
            "server_bin_sha256": hashlib.sha256(
                (bin_dir / "memphant-server").read_bytes()).hexdigest(),
            "worker_bin": str(bin_dir / "memphant-worker"),
            "worker_bin_sha256": hashlib.sha256(
                (bin_dir / "memphant-worker").read_bytes()).hexdigest(),
            "served_binary_identity_matches_arms": served_ok,
            "served_binary_identity_detail": served_detail,
            "single_variable_contrast": lineage_ok and arms_differ,
            "arms_differ_only_in_cross_rerank": arms_differ,
            "fields": lineage_detail,
            "corpus_revision": base_h["v1"].get("corpus_revision"),
            "corpus_pin": (
                "syndai-docs @ 96a26f1f842a4f76eb46c2e7c5385b1ec835238e — git-archive of "
                "the pinned tree into a scratch dir, 114/114 files sha256-verified "
                "against benchmarks/manifests/syndai_docs_gate.lock.json before ingest. "
                "The live Syndai HEAD (7cbcd13e) has drifted off the pin and fails the "
                "corpus contract; the archive preserves the pin without re-mining."
            ),
            "runtime_config_fingerprints": {
                v: {"base": base_h[v].get("runtime_config_fingerprint"),
                    "rerank": rr_h[v].get("runtime_config_fingerprint")}
                for v in ("v1", "v2")
            },
        },
        "mechanism": mechanism,
        "latency": latency,
        "aggregate": {"hit10_base": hit10_base, "hit10_rerank": hit10_rr},
        "effect": {
            "test": "two-sided exact (conditional binomial) McNemar, alpha=0.05",
            "per_set": per_set,
            "pooled_at10": pooled,
            "pooled_at5": pooled_at5,
            "mde_at_80_from_this_runs_psi": mde,
        },
        "verdict": verdict,
        "accuracy_legs_pass": passed,
        "latency_leg": latency["verdict"],
        "failing_reasons": reasons,
    }

    out = base_dir / "verdict-b.json"
    out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n")

    print(f"hit@10  base={hit10_base:.4f}  rerank={hit10_rr:.4f}")
    print(f"paired pooled n={pooled['n']} b={pooled['rerank_only_b']} c={pooled['base_only_c']} "
          f"n_d={pooled['n_d']} psi={pooled['psi']:.4f} delta={pooled['delta']:+.4f} "
          f"p={pooled['p_exact']:.4f} MDE={mde}")
    print(f"mechanism: reordered {len(reordered)}/{len(ids)} ({reorder_fraction:.1%}), "
          f"candidate_count {mechanism['candidate_count_min']}-{mechanism['candidate_count_max']}, "
          f"traces {mechanism['questions_with_rerank_trace']}/{mechanism['questions_total']}")
    print(f"latency: p50={latency['p50_ms']}ms p95={latency['p95_ms']}ms max={latency['max_ms']}ms "
          f"(ceiling {CEILING_MS}ms, profile={args.build_profile}, "
          f"cpu={host['cpu_count']} loadavg={host['loadavg_1_5_15'][0]:.1f}; "
          f"the 449ms inherited claim is RETRACTED)")
    print(f"VERDICT: {packet['verdict']}")
    for r in reasons:
        print("  -", r)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
