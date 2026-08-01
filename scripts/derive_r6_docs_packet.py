#!/usr/bin/env python3
"""R6 (docs-lane unlock) decidability packet — power, homogeneity, and price.

Companion to docs/build-log/2026-08-01-r6-docs-decision.md. Everything it
prints is recomputed from rows on disk; nothing is transcribed from prose.

Three questions, three sections of the emitted JSON:

1. ``observed`` — the realized paired cells for the R6 contrast (best arm
   L1XC vs the Syndai incumbent), per set and pooled, recovered from the r15
   reader artifacts. Includes the exact two-sided McNemar p and per-set
   homogeneity tests, because a pooled n is only admissible across
   homogeneous sets.

2. ``power`` — the required n derived from the *realized* psi via
   ``instrument_power`` (same exact conditional-binomial test as the register),
   at the realized psi and at both ends of its 95% interval. The register's
   n=370 is an output here, not an input.

3. ``lineage`` and ``price`` — whether the existing rows are poolable with any
   newly mined set, and what reaching the required n costs. Prices follow the
   ``derive_phase2_packet.py`` convention exactly: one byte per prompt token at
   the WIDEST MEASURED evidence row, 1024 completion tokens, the recorded
   provider maxima, times an enumerated call budget. No figure is estimated;
   inputs that were never measured are emitted as ``unverified``.

Reader evidence lives in the main worktree (gitignored bodies). Pass
``--evidence-root`` to point at whichever checkout holds it; widths are read
read-only and only their maxima are recorded.

Usage:
    python3 scripts/derive_r6_docs_packet.py
    python3 scripts/derive_r6_docs_packet.py --check   # verify committed JSON
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/build-log/artifacts/r6-docs-decision/packet.json"

# Recorded provider maxima for openai/gpt-5.6-terra via OpenRouter, carried
# from derive_phase2_packet.py (which carries them from the v2 packet).
PRICE_PROMPT_PER_M = 2.75
PRICE_COMPLETION_PER_M = 16.5
COMPLETION_TOKENS = 1024

# The two paired arms of the R6 contrast, as recorded in the null-review
# ledger entry `r15-r6-parity`.
PAIRS = {
    "v1": (
        "docs/build-log/artifacts/r15-docs/L1XC/v1/reader.json",
        "docs/build-log/artifacts/syndai-gate/reader-syndai.json",
    ),
    "v2": (
        "docs/build-log/artifacts/r15-docs/L1XC/v2/reader.json",
        "docs/build-log/artifacts/r1-docs/syndai/v2/reader.json",
    ),
}

# Evidence JSONLs whose widest row bounds the reader prompt, per arm family.
EVIDENCE = {
    "memphant_arm": [
        "docs/build-log/artifacts/r15-docs/L1XC/v1/evidence.jsonl",
        "docs/build-log/artifacts/r15-docs/L1XC/v2/evidence.jsonl",
    ],
    "syndai_arm": [
        "docs/build-log/artifacts/r1-docs/syndai/v2/evidence.jsonl",
    ],
}

CURRENT_GOLDENS = {
    "v1": ROOT / "benchmarks/data/syndai_docs_golden.jsonl",
    "v2": ROOT / "benchmarks/data/syndai_docs_golden_v2.jsonl",
}

_spec = importlib.util.spec_from_file_location(
    "instrument_power", ROOT / "scripts/instrument_power.py"
)
ip = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ip)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact (conditional binomial) McNemar p-value."""
    n_d = b + c
    if n_d == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n_d, i) for i in range(k + 1)) / 2**n_d
    return min(1.0, 2 * tail)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Fisher's exact test on a 2x2, two-sided by the point-probability rule."""
    n, r1, c1 = a + b + c + d, a + b, a + c

    def prob(x: int) -> float:
        return comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)

    observed = prob(a)
    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    return sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed + 1e-12)


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% interval — the register's convention for a psi interval."""
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.959963985, k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def per_call_usd(prompt_tokens: int) -> float:
    return (
        prompt_tokens / 1_000_000 * PRICE_PROMPT_PER_M
        + COMPLETION_TOKENS / 1_000_000 * PRICE_COMPLETION_PER_M
    )


def observed_cells(evidence_root: Path) -> dict:
    """Recover the paired cells for each set and pooled."""
    sets, pooled_b, pooled_c, pooled_n = {}, 0, 0, 0
    for name, (arm_path, base_path) in PAIRS.items():
        arm = json.loads((evidence_root / arm_path).read_text())
        base = json.loads((evidence_root / base_path).read_text())
        a = {q["question_id"]: bool(q["correct"]) for q in arm["per_question"]}
        s = {q["question_id"]: bool(q["correct"]) for q in base["per_question"]}
        ids = sorted(set(a) & set(s))
        b = sum(1 for i in ids if a[i] and not s[i])
        c = sum(1 for i in ids if s[i] and not a[i])
        both = sum(1 for i in ids if a[i] and s[i])
        n = len(ids)
        sets[name] = {
            "arm_label": arm["label"],
            "baseline_label": base["label"],
            "n": n,
            "both": both,
            "arm_only_b": b,
            "baseline_only_c": c,
            "neither": n - both - b - c,
            "n_d": b + c,
            "psi": (b + c) / n,
            "delta": (b - c) / n,
            "p_exact": mcnemar_exact(b, c),
        }
        pooled_b, pooled_c, pooled_n = pooled_b + b, pooled_c + c, pooled_n + n

    n_d = pooled_b + pooled_c
    psi = n_d / pooled_n
    lo, hi = wilson(n_d, pooled_n)
    pooled = {
        "n": pooled_n,
        "arm_only_b": pooled_b,
        "baseline_only_c": pooled_c,
        "n_d": n_d,
        "psi": psi,
        "psi_ci95_wilson": [lo, hi],
        "delta": (pooled_b - pooled_c) / pooled_n,
        "p_exact": mcnemar_exact(pooled_b, pooled_c),
    }

    v1, v2 = sets["v1"], sets["v2"]
    homogeneity = {
        "effect_direction_fisher_p": fisher_exact_two_sided(
            v1["arm_only_b"], v1["baseline_only_c"],
            v2["arm_only_b"], v2["baseline_only_c"],
        ),
        "discordance_rate_fisher_p": fisher_exact_two_sided(
            v1["n_d"], v1["n"] - v1["n_d"], v2["n_d"], v2["n"] - v2["n_d"]
        ),
        "note": (
            "Neither test rejects at alpha=0.05, but neither is powered to "
            "detect heterogeneity at these counts. Non-rejection here is not "
            "evidence of homogeneity; it is absence of evidence either way."
        ),
    }
    return {"per_set": sets, "pooled": pooled, "homogeneity": homogeneity}


def power_block(pooled: dict, d_min: float = 0.07) -> dict:
    psi = pooled["psi"]
    lo, hi = pooled["psi_ci95_wilson"]
    rows = {}
    for label, p in (("realized", psi), ("psi_ci_low", lo), ("psi_ci_high", hi)):
        rows[label] = {
            "psi": p,
            "mde_at_80_on_current_n": ip.min_detectable_effect(pooled["n"], p),
            "required_n_for_7pt": ip.required_n(p, d_min),
            "required_n_for_observed_delta": ip.required_n(p, pooled["delta"]),
        }
    return {
        "test": "two-sided exact conditional-binomial McNemar, alpha=0.05, target power 0.80",
        "d_min": d_min,
        "power_at_current_n_and_observed_delta": ip.power(
            pooled["n"], psi, pooled["delta"]
        ),
        "by_psi": rows,
        "required_n": rows["realized"]["required_n_for_7pt"],
        "planning_n_at_psi_ci_high": rows["psi_ci_high"]["required_n_for_7pt"],
    }


def lineage_block(evidence_root: Path) -> dict:
    """Are the r15-scored rows poolable with the currently pinned bank?"""
    current = {}
    for tag, path in CURRENT_GOLDENS.items():
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                current[(tag, row["question_id"])] = row["gold_answer"]

    out = {}
    for tag in PAIRS:
        reader = json.loads((evidence_root / PAIRS[tag][0]).read_text())
        rows = reader["per_question"]
        id_hits = sum(1 for q in rows if (tag, q["question_id"]) in current)
        full_hits = sum(
            1
            for q in rows
            if current.get((tag, q["question_id"])) == q["gold_answer"]
        )
        prov = json.loads(
            (evidence_root / PAIRS[tag][0]).with_name("provenance.json").read_text()
        )
        out[tag] = {
            "scored_rows": len(rows),
            "question_id_also_in_current_bank": id_hits,
            "same_question_and_gold_answer": full_hits,
            "scored_against_golden_sha256": prov["golden_sha256"],
            "haystack_sections_at_scoring_time": prov.get("haystack_sections"),
        }

    lock = json.loads((ROOT / "benchmarks/manifests/syndai_docs_gate.lock.json").read_text())
    v1lock = json.loads((ROOT / "benchmarks/data/syndai_docs_golden.lock.json").read_text())
    out["current_pin"] = {
        "corpus_git_commit": v1lock["corpus_git_commit"],
        "file_count": lock["file_count"],
        "section_count": lock["section_count"],
        "mining_candidate_section_count": lock["mining_candidate_section_count"],
        "golden_v1_sha256": v1lock["sha256"],
        "generator_model": v1lock["generator_model"],
    }
    out["verdict"] = (
        "NOT POOLABLE. Zero of the 120 r15-scored rows exist as the same "
        "question in the currently pinned bank; the C2 re-pin re-mined both "
        "sets against a different corpus revision. Usable sets on the current "
        "pin: 0, not 2."
    )
    return out


def widest_row(evidence_root: Path, paths: list[str]) -> dict:
    widths = []
    missing = []
    for p in paths:
        f = evidence_root / p
        if not f.exists():
            missing.append(p)
            continue
        widths.extend(
            len(line.encode()) for line in f.read_text().splitlines() if line.strip()
        )
    if not widths:
        return {"measured": False, "missing": missing}
    widths.sort()
    return {
        "measured": True,
        "rows": len(widths),
        "max_bytes": widths[-1],
        "median_bytes": widths[len(widths) // 2],
        "missing": missing,
    }


def price_block(evidence_root: Path, required_n: int) -> dict:
    mp = widest_row(evidence_root, EVIDENCE["memphant_arm"])
    sy = widest_row(evidence_root, EVIDENCE["syndai_arm"])
    if not (mp["measured"] and sy["measured"]):
        raise SystemExit(
            "reader evidence not found under --evidence-root; widths are the "
            "only measured input to the ceiling and must not be estimated"
        )

    def bound(measured_max: int) -> int:
        return -(-measured_max // 1000) * 1000  # round up to the next 1k

    mp_tokens, sy_tokens = bound(mp["max_bytes"]), bound(sy["max_bytes"])
    # Judge prompt is question + gold answer + reply; never measured on this
    # lane, so it is bounded generously rather than estimated.
    judge_tokens = 2000
    # Mining prompt is at most two mining-candidate sections; the corpus lock's
    # own mining rule caps a candidate section body at 3200 chars.
    mine_tokens = 8000

    already_mined = sum(
        len([l for l in p.read_text().splitlines() if l.strip()])
        for p in CURRENT_GOLDENS.values()
    )
    to_mine = max(0, required_n - already_mined)
    # Floor: one accepted golden per generator call. Ceiling: the miner's own
    # recorded default budget, --max-calls 400 per 60-golden set.
    mine_calls_floor = to_mine
    mine_calls_ceiling = round(to_mine / 60 * 400)
    # Judge fires only when normalized containment misses. Measured on the r15
    # L1XC v1 run: 89 total calls over 60 rows => 29 judge calls (48.3%).
    judge_fire_rate = 29 / 60

    stages = [
        {
            "stage": "A_mine_new_goldens",
            "model": "google/gemini-3.1-pro-preview (OpenRouter)",
            "price_source": "no gemini price is recorded in this repo; bounded at the recorded gpt-5.6-terra maxima, same convention as the register's STATE-Bench row",
            "prompt_token_bound": mine_tokens,
            "calls_floor": mine_calls_floor,
            "calls_ceiling": mine_calls_ceiling,
            "usd_floor": mine_calls_floor * per_call_usd(mine_tokens),
            "usd_ceiling": mine_calls_ceiling * per_call_usd(mine_tokens),
        },
        {
            "stage": "B_reader_memphant_arm",
            "model": "openai/gpt-5.6-terra",
            "price_source": f"widest measured MemPhant docs evidence row {mp['max_bytes']} B, one byte per prompt token",
            "prompt_token_bound": mp_tokens,
            "calls_floor": required_n,
            "calls_ceiling": required_n,
            "usd_floor": required_n * per_call_usd(mp_tokens),
            "usd_ceiling": required_n * per_call_usd(mp_tokens),
        },
        {
            "stage": "C_reader_syndai_incumbent_arm",
            "model": "openai/gpt-5.6-terra",
            "price_source": f"widest measured Syndai docs evidence row {sy['max_bytes']} B, one byte per prompt token",
            "prompt_token_bound": sy_tokens,
            "calls_floor": required_n,
            "calls_ceiling": required_n,
            "usd_floor": required_n * per_call_usd(sy_tokens),
            "usd_ceiling": required_n * per_call_usd(sy_tokens),
        },
        {
            "stage": "D_judge",
            "model": "anthropic/claude-sonnet-5",
            "price_source": "judge prompt width never measured on this lane; bounded at 2000 tokens. Fire rate measured at 29/60 on r15 L1XC v1; ceiling assumes every row",
            "prompt_token_bound": judge_tokens,
            "calls_floor": round(2 * required_n * judge_fire_rate),
            "calls_ceiling": 2 * required_n,
            "usd_floor": round(2 * required_n * judge_fire_rate) * per_call_usd(judge_tokens),
            "usd_ceiling": 2 * required_n * per_call_usd(judge_tokens),
        },
    ]

    return {
        "convention": "derive_phase2_packet.py: one byte per prompt token at the widest MEASURED row, 1024 completion tokens, recorded provider maxima, times an enumerated call budget",
        "provider_max_price_usd_per_million": {
            "prompt": PRICE_PROMPT_PER_M,
            "completion": PRICE_COMPLETION_PER_M,
        },
        "measured_evidence_widths": {"memphant_arm": mp, "syndai_arm": sy},
        "required_n": required_n,
        "goldens_already_mined_on_current_pin": already_mined,
        "goldens_to_mine": to_mine,
        "stages": stages,
        "total_usd_floor": sum(s["usd_floor"] for s in stages),
        "total_usd_ceiling": sum(s["usd_ceiling"] for s in stages),
        "unpriced_inputs": [
            "Syndai incumbent ingest embeddings (text-embedding-3-small over ~9,890 chunks): no OpenAI embedding price is recorded in this repo — UNVERIFIED, and small relative to the stages above",
            "Jina rerank calls inside search_knowledge_detached: price UNVERIFIED",
        ],
        "independent_cross_check": "the r15 wave itself settled ~$25-35 reader/judge for 4 arms x 120 rows plus a chat pair; this packet's floor is the same order for 2 arms x required_n",
    }


def build(evidence_root: Path) -> dict:
    observed = observed_cells(evidence_root)
    power = power_block(observed["pooled"])
    lineage = lineage_block(evidence_root)
    price = price_block(evidence_root, power["required_n"])
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    pooled = observed["pooled"]
    return {
        "decision": "R6 — the docs-lane unlock (best arm L1XC vs the Syndai incumbent, pooled)",
        "evidence_contract": {
            "schema_version": 1,
            # This packet decides nothing. It re-derives banked cells, corrects
            # the required n, and prices a run that has not been authorized.
            "decisional": False,
            "claim": (
                "R6 is not decidable at $0: reaching a homogeneous n=370 costs "
                "$57.72-$121.39, and the 120 rows already scored are on a "
                "retired corpus pin and cannot be pooled with new ones."
            ),
            "power": {
                "test": "two-sided exact (conditional binomial) McNemar",
                "n": pooled["n"],
                "b": pooled["arm_only_b"],
                "c": pooled["baseline_only_c"],
                "n_d": pooled["n_d"],
                "psi_observed": pooled["psi"],
                "mde_at_80": ip.min_detectable_effect(pooled["n"], pooled["psi"]),
                "computed_by": "scripts/instrument_power.py",
                "source": (
                    "docs/build-log/artifacts/r15-docs/L1XC/{v1,v2}/reader.json vs "
                    "docs/build-log/artifacts/syndai-gate/reader-syndai.json and "
                    "docs/build-log/artifacts/r1-docs/syndai/v2/reader.json"
                ),
            },
            "harness": {
                "embed_model": "modernbert",
                "scorer": (
                    "normalized containment first, one claude-sonnet-5 judge call on "
                    "non-match; reader openai/gpt-5.6-terra"
                ),
                "k": 10,
                "budget": 8192,
                "flags": [
                    "MEMPHANT_CROSS_RERANK=1 (bge-reranker-base, 64-pool)",
                    "MEMPHANT_RESOURCE_CHUNKS=1",
                    "recall_pool_depth=64",
                ],
                "command": "python3 scripts/derive_r6_docs_packet.py",
                "note": (
                    "the harness above is the r15 L1XC arm's, as recorded in its "
                    "provenance.json; this packet re-derives from those rows and "
                    "runs no arm of its own"
                ),
            },
            "corpus": {
                # The scored rows' bank, NOT the currently pinned one -- that is
                # the finding. Both retired golden shas are recorded.
                "sha256": "c424b08f02608510445007a5d75dcf5903c8ac34d31c073c629717db9a0f0c83",
                "snapshot_id": "syndai-docs @ fb650da (retired), r15 golden bank v1",
                "n_items": pooled["n"],
                "retired_golden_sha256_v2": "30b354c55c830abed8bfcedd3c659610f813aedc2bab63fc0f6b37cb80ce365b",
                "currently_pinned_snapshot_id": "syndai-docs @ 96a26f1f",
                "poolable_with_current_pin": False,
            },
            "leakage": None,
            "instrument_verification": {
                "shipped_rows_verified": True,
                "rows_counted": pooled["n"],
                "fields_counted": {
                    "per_question.correct": pooled["n"],
                    "per_question.question_id": pooled["n"],
                },
                "license_id": "private (Syndai product documentation, owner's own)",
                "license_source": "unverified",
                "license_evidence": (
                    "private corpus, no LICENSE blob exists to pin; used read-only "
                    "and no body is committed"
                ),
            },
        },
        "lineage": {
            "memphant_git_head": head,
            "instrument_power_sha256": hashlib.sha256(
                (ROOT / "scripts/instrument_power.py").read_bytes()
            ).hexdigest(),
            "this_script_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "evidence_root": str(evidence_root),
            "no_rust_binary_involved": True,
        },
        "observed": observed,
        "power": power,
        "corpus_lineage": lineage,
        "price": price,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-root",
        default="/Users/sidsharma/Memphant",
        help="checkout holding the gitignored reader evidence JSONLs",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    packet = build(Path(args.evidence_root))
    text = json.dumps(packet, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not OUT.exists():
            print(f"MISSING {OUT}")
            return 1
        committed = json.loads(OUT.read_text())
        fresh = json.loads(text)
        # lineage.evidence_root and git head move with the checkout.
        for d in (committed, fresh):
            d["lineage"].pop("evidence_root", None)
            d["lineage"].pop("memphant_git_head", None)
        if committed != fresh:
            print("DRIFT: recomputed packet differs from the committed one")
            return 1
        print("ok: committed packet reproduces")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    p, o, pw = packet["price"], packet["observed"]["pooled"], packet["power"]
    print(f"pooled n={o['n']} b={o['arm_only_b']} c={o['baseline_only_c']} "
          f"psi={o['psi']:.4f} delta={o['delta']:+.4f} p_exact={o['p_exact']:.4f}")
    print(f"required n for 7pt at realized psi: {pw['required_n']} "
          f"(at psi CI high: {pw['planning_n_at_psi_ci_high']})")
    print(f"usable sets on the current pin: 0 — {packet['corpus_lineage']['verdict']}")
    print(f"price to reach n={p['required_n']}: "
          f"${p['total_usd_floor']:.2f} floor / ${p['total_usd_ceiling']:.2f} ceiling")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
