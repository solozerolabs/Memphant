#!/usr/bin/env python3
"""Exact paired-McNemar power for the instrument register.

Companion to docs/build-log/2026-07-31-instrument-register.md. This script
exists so every MDE ("minimum detectable effect at 80% power") in the register
is *computed from an observed discordance rate banked in this repo*, not
asserted. The prior convention -- `derive_phase2_packet.py`'s
`"power_note": "~80% at psi~=0.15"` -- was an assumed psi with no run behind it.
Every psi used here carries a `source` field naming the artifact it was
measured from, or is explicitly marked as unverified.

Test modelled: two-sided exact (conditional binomial) McNemar at alpha=0.05,
which is the test the lanes actually use -- not the chi-square approximation,
which overstates power at the discordant-pair counts we live at (n_d < 30).

Parameterisation. For a paired run of n rows:
  psi   = P(pair is discordant)                      [observed, per lane]
  delta = p_treat - p_control                        [the effect, in points]
  b/n   = (psi + delta) / 2,  c/n = (psi - delta) / 2

Power is computed UNCONDITIONALLY: the discordant-pair count is itself random,
so we integrate the conditional exact test over N_d ~ Binomial(n, psi). Fixing
n_d at its expectation -- the common shortcut -- is optimistic at our n.

Usage:
    python3 scripts/instrument_power.py            # print the register table
    python3 scripts/instrument_power.py --check    # verify the committed JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks/manifests/instrument_power.json"

ALPHA = 0.05
TARGET_POWER = 0.80


def binom_pmf(k: int, n: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return comb(n, k) * p**k * (1.0 - p) ** (n - k)


@lru_cache(maxsize=None)
def exact_binom_reject(n_d: int) -> tuple[int, ...]:
    """Rejection region of the two-sided exact binomial test of pi=0.5.

    Returns the b in 0..n_d whose two-sided p-value is <= ALPHA. Under H0 the
    distribution is symmetric, so the two-sided p-value of b is
    2 * P(B <= min(b, n_d - b)), capped at 1. The region is a pair of tails, so
    it is enough to find the largest rejecting tail index and mirror it.
    """
    cum = 0.0
    crit = -1
    for k in range(n_d + 1):
        cum += binom_pmf(k, n_d, 0.5)
        if min(2.0 * cum, 1.0) <= ALPHA:
            crit = k
        else:
            break
    if crit < 0:
        return ()
    # sorted(set(...)) guards the degenerate small-n_d case where the two tails
    # would otherwise overlap and double-count.
    return tuple(sorted(set(range(crit + 1)) | set(range(n_d - crit, n_d + 1))))


def power(n: int, psi: float, delta: float) -> float:
    """Unconditional power of the two-sided exact McNemar test.

    n     -- paired rows scored
    psi   -- discordance rate (b + c) / n
    delta -- marginal accuracy difference (b - c) / n
    """
    if delta < 0:
        delta = -delta
    if psi <= 0.0 or delta > psi:
        return 0.0
    # P(b-cell | discordant). delta == psi means every discordant pair favours
    # the treatment; the conditional binomial degenerates to pi = 1.
    pi = (psi + delta) / (2.0 * psi)
    total = 0.0
    for n_d in range(n + 1):
        p_nd = binom_pmf(n_d, n, psi)
        if p_nd < 1e-15:
            continue
        reject = exact_binom_reject(n_d)
        if not reject:
            continue  # n_d too small for ANY split to reach alpha=0.05
        total += p_nd * sum(binom_pmf(b, n_d, pi) for b in reject)
    return total


def min_detectable_effect(n: int, psi: float, target: float = TARGET_POWER) -> float | None:
    """Smallest |delta| reaching `target` power. None if unreachable at any delta.

    Power is monotone in delta for fixed n and psi, so bisect. delta cannot
    exceed psi (that is the all-discordant-pairs-agree ceiling), which is why a
    low-discordance lane can be unpowerable outright: if even delta == psi
    misses 80%, no effect of any size is detectable at this n.
    """
    if psi <= 0.0:
        return None
    if power(n, psi, psi) < target:
        return None
    lo, hi = 0.0, psi
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if power(n, psi, mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def required_n(psi: float, delta: float, target: float = TARGET_POWER, cap: int = 20000) -> int | None:
    """Smallest n reaching `target` power for the given psi and delta.

    Power is NOT strictly monotone in n for an exact discrete test (it saws),
    so this walks upward and returns the first n from which the target holds
    and keeps holding -- checked over a trailing window, not a single point.
    """
    if psi <= 0.0 or delta <= 0.0 or delta > psi:
        return None
    n = 4
    while n <= cap:
        if power(n, psi, delta) >= target:
            window = [power(m, psi, delta) >= target for m in range(n, min(n + 12, cap) + 1)]
            if all(window):
                return n
        n += 1 if n < 200 else 5
    return None


# --------------------------------------------------------------------------
# Lane inputs. Every psi is either measured from a banked artifact in this
# repo -- `source` names it -- or flagged unverified. NEVER add a lane here
# with an invented psi; a lane with no paired run has no psi, and the register
# must say so rather than borrow a neighbour's.
# --------------------------------------------------------------------------
LANES: list[dict] = []

# The preregistered effect the chat and coding lanes are sized for
# (2026-07-27-accuracy-first-program.md:256 and :199).
D_MIN = 0.07


def add_lane(
    lane: str,
    instrument: str,
    n: int | None,
    b: int | None,
    c: int | None,
    source: str,
    care_about: float | None,
    note: str = "",
) -> None:
    LANES.append(
        {
            "lane": lane,
            "instrument": instrument,
            "n": n,
            "b": b,
            "c": c,
            "psi_source": source,
            "effect_we_care_about": care_about,
            "note": note,
        }
    )


A = "docs/build-log/artifacts"

# --- episodic / chat -------------------------------------------------------
add_lane(
    "episodic/chat", "LME-S rung7 retrieval hit@5", 166, 38, 0,
    f"{A}/rung7-packing-reader-gate/{{baseline,rendercap1200}}-retrieval.json", D_MIN,
    "baseline vs cap-1200, recomputed from per_question rows: b=38 favours cap-1200, "
    "c=0, unanimous. 12 _abs rows excluded. RETRIEVAL endpoint, NOT the reader endpoint "
    "the paid run scores. Also verified: max first_answer_rank across both arms is 5, so "
    "hit@5 and hit@10 are identical on all 166 rows -- @10 is a dead metric on this slice.",
)
add_lane(
    "episodic/chat", "LME-S rung7 non-regression", 166, 0, 0,
    f"{A}/rung7-packing-reader-gate/phase1{{d,w}}/chat-*-retrieval.json", None,
    "phase1d and phase1w both. b=c=0 by construction; cite only as non-regression, never as effect evidence.",
)
add_lane(
    "episodic/chat", "LME-S _abs sentinel (the killed gate)", 12, 0, 2,
    f"{A}/rung7-packing-reader-gate/{{baseline,rendercap1200}}-retrieval.json"
    " (abstention_correct over the 12 is_abstention rows)", None,
    "Independently recomputed: this IS the 2-discordant-pair screen that rejected "
    "pack_render_cap. Exact two-sided p = 0.50. n_d = 2 < the structural floor of 6, so "
    "it could not have rejected at ANY effect size. The Phase 0 rescission is confirmed "
    "structurally, not merely on the trap-session argument.",
)
add_lane(
    "episodic/chat", "LME-S 80q pool, median arm", 72, 4, 8,
    f"{A}/p1-retrieval-bench/scores/score-rr-{{none,minilm-chunk}}.json", D_MIN,
    "Median of 21 banked arms (b+c range 0-14). Best empirical psi we own for this lane. "
    "Saturated above k=16 (base recall@16 0.986, recall@48 1.000).",
)
add_lane(
    "episodic/chat", "LME-S paid reader QA (Phase 2)", None, None, None,
    "NO RUN: authorization-request.v3.json paid_calls_executed=0, authorization=null", D_MIN,
    "The endpoint the $30-60 spend buys has never been observed. Its preregistered "
    "psi~=0.15 is an assumption; see the register's proxy analysis.",
)
add_lane(
    "episodic/chat", "LongMemEval-V2 state-aware", None, None, None,
    "NO RUN: v1/v3/v4 ABANDONED_NEVER_RESUME; 11 p1-t6 runs all carry INVALIDATION-PROOF.json", None,
    "9,405 lines of harness, official_output_files=0, settled_micros=0.",
)

# --- repo / code -----------------------------------------------------------
add_lane(
    "repo/code", "Track R fused vs scoped-BM25 @10", 180, 15, 3,
    f"{A}/track-r/track_r_phase1e_combined_fixes.json", D_MIN,
    "The decision-relevant contrast (does MemPhant beat a lexical control). Measured on "
    "the CONTAMINATED bank: target coverage 0.3960 vs floor 0.0945 = 4.19x. Effect unfalsifiable as a memory gain.",
)
add_lane(
    "repo/code", "Track R rank-order fix @10", 180, 22, 0,
    f"{A}/track-r/track_r_phase1d_packing_rank_order.json", D_MIN, "Contaminated bank.",
)
add_lane(
    "repo/code", "Track R render-loss fix @5", 180, 35, 0,
    f"{A}/track-r/track_r_phase1w_render_loss.json", D_MIN, "Contaminated bank.",
)
add_lane(
    "repo/code", "Track R PARAPHRASE bank", None, None, None,
    "NO RUN: zero artifacts reference the paraphrase bank as a scored arm", D_MIN,
    "The only bank that fixes the contamination (2.02x) has never been run. Its own lock "
    "records bar_passed=false against a 1.50x bar, while downstream prereg cites it as the 2.05x standard.",
)
add_lane(
    "repo/code", "coding_events_golden BM25 vs MemPhant @10", 40, 0, 2,
    "benchmarks/manifests/code_lane_controls.2026-07-13.json (delta only; psi is a LOWER BOUND)", D_MIN,
    "Only the delta (-0.05) was published, so psi >= |delta|. n=40 over 8 attempts in ONE repo; "
    "held-out slice is 4 questions. Unpowerable at any psi below ~0.15.",
)
add_lane(
    "repo/code", "GitHub lane golden bank", None, None, None,
    "NO RUN: scripts/github_lane_* are fetch/extract/leakage/secrets only -- no runner exists", None,
    "416 goldens mined, 3 of 5 preregistered bars FAIL, no bank certified.",
)
add_lane(
    "repo/code", "SWE-ContextBench tranche 1", None, None, None,
    f"RUN 2026-07-24 but UNPAIRABLE: {A}/next-evidence/coding/swe-contextbench-first-tranche-result.json", None,
    "Baseline-saturated: 3/4 no-memory baselines resolve, max possible gain 1 < required 2.",
)
add_lane(
    "repo/code", "SWE-Explore", None, None, None,
    "NO RUN: benchmarks/manifests/swe_explore.lock.json", None,
    "848 shipped rows carry problem_statement on 0 and base_commit on 0. Nothing to explore from.",
)

# --- semantic / docs -------------------------------------------------------
add_lane(
    "semantic/docs", "Syndai docs gate, hit@10", 60, 0, 8,
    f"{A}/syndai-gate/gate_compare.json (bootstrap CI only; psi is a LOWER BOUND)", D_MIN,
    "Delta -0.133 => |b-c| = 8. Direction: MemPhant LOSES to Syndai's own stack. "
    "Only b-c is recoverable from the artifact; b and c were never committed separately.",
)
add_lane(
    "semantic/docs", "Syndai docs gate, QA", 60, 0, 10,
    f"{A}/syndai-gate/gate_compare.json (bootstrap CI only; psi is a LOWER BOUND)", D_MIN,
    "Delta -0.167 => |b-c| = 10. MemPhant loses. This negative result CLEARS its own MDE.",
)


# --- forgetting / lifecycle ------------------------------------------------
add_lane(
    "forgetting/lifecycle", "ForgetEval adversarial-385", 259, 111, 0,
    f"{A}/next-evidence/forgeteval/adversarial-385-{{baseline-instrumented,lineage-complete}}.json",
    D_MIN,
    "Recomputed from the 385 shipped case rows: baseline 133 pass / 126 fail / 126 N/A, "
    "lineage 244 / 15 / 126, the SAME 126 N/A rows in both arms (na agreement 385/385). "
    "b=111, c=0 -- the STATUS claim of '111 paired gains and zero baseline regressions' "
    "checks out exactly against the artifact. Deterministic mechanism comparison, no model "
    "call in the contrast. Highest discordance of any lane we own.",
)


def compute(lane: dict) -> dict:
    out = dict(lane)
    n, b, c = lane["n"], lane["b"], lane["c"]
    if n is None or b is None or c is None:
        out.update(
            {
                "psi_observed": None,
                "delta_observed": None,
                "mde_at_80": None,
                "required_n": None,
                "verdict": "NO PAIRED RUN -- psi unmeasured, MDE uncomputable",
            }
        )
        return out
    psi = (b + c) / n
    delta = (b - c) / n
    mde = min_detectable_effect(n, psi)
    care = lane["effect_we_care_about"]
    req = required_n(psi, care) if care is not None else None
    if mde is None:
        verdict = f"UNPOWERABLE at n={n}: even a unanimous discordant split misses 80% power"
    elif care is None:
        verdict = f"MDE {mde * 100:.1f}pt at n={n}; no preregistered effect size to judge against"
    elif mde <= care:
        verdict = f"ADEQUATE: MDE {mde * 100:.1f}pt <= target {care * 100:.1f}pt"
    elif req is None:
        verdict = (
            f"INADEQUATE and UNREACHABLE: MDE {mde * 100:.1f}pt vs target {care * 100:.1f}pt; "
            f"target exceeds observed discordance psi={psi * 100:.1f}% -- no n suffices"
        )
    else:
        verdict = f"INADEQUATE: MDE {mde * 100:.1f}pt vs target {care * 100:.1f}pt; need n>={req}"
    out.update(
        {
            "psi_observed": round(psi, 6),
            "delta_observed": round(delta, 6),
            "mde_at_80": round(mde, 6) if mde is not None else None,
            "required_n": req,
            "verdict": verdict,
        }
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rows = [compute(lane) for lane in LANES]
    doc = {
        "schema_version": 1,
        "generated_by": "scripts/instrument_power.py",
        "test": "two-sided exact (conditional binomial) McNemar",
        "alpha": ALPHA,
        "target_power": TARGET_POWER,
        "power_model": "unconditional: integrates the conditional exact test over N_d ~ Binomial(n, psi)",
        "psi_policy": "every psi is measured from a banked artifact named in psi_source, or the lane is recorded as having no paired run",
        "lanes": rows,
    }
    text = json.dumps(doc, indent=2) + "\n"

    if args.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current != text:
            print(f"power_drift={OUT}", file=sys.stderr)
            return 1
        print(f"power_ok={OUT}")
        return 0

    OUT.write_text(text)
    for r in rows:
        print(f"{r['lane']:<28} {r['instrument']:<28} {r['verdict']}")
    print(f"wrote={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
