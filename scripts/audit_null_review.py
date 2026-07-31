#!/usr/bin/env python3
"""Re-derive every recorded "null" in the 2026-07 window from per-question rows.

Companion to docs/build-log/2026-07-31-null-review.md. Nothing here is copied
from a build log's prose: each comparison names the artifact, loads the paired
per-question vectors out of it, and rebuilds the full 2x2. Where an artifact
carries only committed CELL COUNTS and not the rows, the entry is marked
`rows_recovered: false` and the review may not call it re-derived.

Power and MDE come from scripts/instrument_power.py -- the same exact
(conditional binomial) two-sided McNemar the lanes use -- evaluated at each
result's OWN realized discordance rate, never an assumed psi.

TWO-TREE READ POLICY. Evidence is split across two working copies and neither
is complete (see the build log's "unrecoverable" section):

  * `/Users/sidsharma/Memphant` (main) holds the gitignored campaign roots
    `unified-sota-2026071[34]/` -- 14,679 files, of which only 1 of the 88
    paths in `canonical-artifact-allowlist.txt` is actually git-tracked. All
    Memora / MemSyco / state-memory evidence lives there and NOWHERE else.
  * The `accuracy-first` worktree holds everything committed after main's
    HEAD -- track-r, track-r-paraphrase, lme-cleaned-split. Those are a branch
    gap, not a loss: they are tracked, and main gets them on merge.

So every read resolves against both roots and records which one answered and
whether git tracks the file. An artifact absent from ONE tree is not a missing
artifact; an artifact tracked by NEITHER is a durability finding.

Usage:
    python3 scripts/audit_null_review.py            # print the table
    python3 scripts/audit_null_review.py --write    # rewrite the ledger artifact
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instrument_power as ip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path("/Users/sidsharma/Memphant")
OUT = ROOT / "docs/build-log/artifacts/null-review-ledger.json"

# Preregistered effect both the chat and coding lanes are sized for
# (2026-07-27-accuracy-first-program.md:256 and :199).
D_MIN = 0.07
# Structural floor of the two-sided exact McNemar test: at n_d = 5 the most
# extreme split has p = 2 * 0.5**5 = 0.0625 > 0.05, so nothing rejects.
MIN_DECISIONAL_ND = 6

_PROVENANCE: dict[str, dict] = {}


def resolve(rel: str) -> Path:
    """Find `rel` in the worktree, else in main. Records where it came from."""
    for root, name in ((ROOT, "accuracy-first"), (MAIN, "main")):
        p = root / rel
        if p.exists():
            if rel not in _PROVENANCE:
                tracked = (
                    subprocess.run(
                        ["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
                        capture_output=True,
                    ).returncode
                    == 0
                )
                _PROVENANCE[rel] = {"tree": name, "git_tracked": tracked}
            return p
    _PROVENANCE[rel] = {"tree": None, "git_tracked": False}
    raise FileNotFoundError(rel)


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs."""
    n_d = b + c
    if n_d == 0:
        return 1.0
    lo = min(b, c)
    tail = sum(comb(n_d, k) for k in range(lo + 1)) / 2.0**n_d
    return min(2.0 * tail, 1.0)


def _cp_bounds(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Clopper-Pearson interval for k of n, by bisection on the binomial tail
    (stdlib only -- this repo's CI has no scipy)."""
    if n == 0:
        return 0.0, 1.0
    alpha = 1.0 - level

    def bisect(f, target: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2.0
            if f(mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    low = (
        0.0
        if k == 0
        else bisect(lambda p: sum(ip.binom_pmf(i, n, p) for i in range(k, n + 1)), alpha / 2.0)
    )
    high = (
        1.0
        if k == n
        else bisect(
            lambda p: 1.0 - sum(ip.binom_pmf(i, n, p) for i in range(0, k + 1)),
            1.0 - alpha / 2.0,
        )
    )
    return low, high


def delta_ci(b: int, c: int, n: int) -> tuple[float, float]:
    """95% interval on delta = (b - c)/n -- "how big a win could still hide here".

    Conditional on n_d it is Clopper-Pearson on pi = b/n_d mapped through
    delta = psi * (2*pi - 1). With n_d = 0 that is degenerate, so the rule of
    three applies instead: psi <= 3/n at 95%, hence |delta| <= 3/n.
    """
    n_d = b + c
    if n_d == 0:
        return -3.0 / n, 3.0 / n
    psi = n_d / n
    lo_pi, hi_pi = _cp_bounds(b, n_d)
    return psi * (2.0 * lo_pi - 1.0), psi * (2.0 * hi_pi - 1.0)


def classify(n, b, c, care: float, unrecoverable: str | None = None) -> dict:
    if unrecoverable or n is None or b is None or c is None:
        return {
            "n_d": None,
            "psi": None,
            "delta": None,
            "p_exact": None,
            "power_at_dmin": None,
            "mde_at_80": None,
            "delta_ci95": None,
            "abandoned_win_ceiling": None,
            "psi_ci95": None,
            "mde_at_psi_upper": None,
            "classification": "NON-MEASUREMENT",
            "why": unrecoverable
            or "no recoverable per-question rows and no committed 2x2 -- unfalsifiable as recorded",
        }
    n_d = b + c
    psi = n_d / n
    delta = (b - c) / n
    p = exact_p(b, c)
    mde = ip.min_detectable_effect(n, psi) if psi > 0 else None
    # Power at `care` is only meaningful when care <= psi: under the paired model
    # delta cannot exceed the discordance rate, so an effect of `care` implies at
    # least that much discordance. Where care > psi_observed the honest statement
    # is the MDE, not a power number of zero.
    pw = ip.power(n, psi, care) if 0 < care <= psi else None
    lo, hi = delta_ci(b, c, n)
    # psi is itself estimated, often from a handful of discordant pairs, and the
    # MDE is a function of it. An "adequately powered" verdict that survives only
    # at the point estimate of psi is not a verdict. Recompute the MDE at the
    # upper 95% bound of psi and require it to hold there too.
    psi_lo, psi_hi = _cp_bounds(n_d, n)
    mde_hi = ip.min_detectable_effect(n, psi_hi) if psi_hi > 0 else None
    if n_d < MIN_DECISIONAL_ND:
        cls = "NON-MEASUREMENT"
        why = (
            f"n_d = {n_d} < {MIN_DECISIONAL_ND}: the two-sided exact test has no rejection "
            f"region at this discordance, so it could not have rejected at ANY effect size"
        )
    elif p < 0.05:
        cls = "FALSE NULL"
        why = f"exact two-sided p = {p:.4g} < 0.05 on the re-derived cells -- recorded as a null, but it rejects"
    elif mde is not None and mde <= care:
        fragile = mde_hi is None or mde_hi > care
        cls = "VALID NULL (psi-fragile)" if fragile else "VALID NULL"
        why = (
            f"true MDE {mde * 100:.1f}pt at the realized psi = {psi:.4f}, at or below the "
            f"preregistered {care * 100:.0f}pt; it rules out |delta| >= {mde * 100:.1f}pt"
        )
        if fragile:
            why += (
                f". FRAGILE: psi is estimated from {n_d} discordant pairs (95% CI "
                f"{psi_lo:.4f}-{psi_hi:.4f}); at the upper bound the MDE is "
                + (f"{mde_hi * 100:.1f}pt" if mde_hi is not None else "unreachable")
                + f", above {care * 100:.0f}pt. The adequacy holds only at the point estimate."
            )
    elif mde is None:
        cls = "UNDERPOWERED"
        why = (
            f"n_d = {n_d} clears the floor, but at n = {n} and psi = {psi:.4f} even a "
            f"unanimous discordant split misses 80% power -- UNPOWERABLE at any effect size"
        )
    else:
        cls = "UNDERPOWERED"
        pw_txt = f"; power at {care * 100:.0f}pt is {pw:.3f}" if pw is not None else ""
        why = (
            f"n_d = {n_d} clears the floor, but the true MDE is {mde * 100:.1f}pt against a "
            f"preregistered {care * 100:.0f}pt{pw_txt}"
        )
    return {
        "n_d": n_d,
        "psi": round(psi, 6),
        "delta": round(delta, 6),
        "p_exact": round(p, 6),
        "power_at_dmin": round(pw, 4) if pw is not None else None,
        "mde_at_80": round(mde, 6) if mde is not None else None,
        "psi_ci95": [round(psi_lo, 6), round(psi_hi, 6)],
        "mde_at_psi_upper": round(mde_hi, 6) if mde_hi is not None else None,
        "delta_ci95": [round(lo, 4), round(hi, 4)],
        # the largest win still compatible with the data -- the review ranks on this
        "abandoned_win_ceiling": round(hi, 4),
        "classification": cls,
        "why": why,
    }


# ---------------------------------------------------------------------------
# row loaders
# ---------------------------------------------------------------------------


def _load(rel: str) -> dict:
    return json.loads(resolve(rel).read_text())


def reader_vec(rel: str) -> dict[str, bool]:
    """question_id -> correct, from a run_reader.py report."""
    return {
        r["question_id"]: bool(r["correct"])
        for r in _load(rel)["per_question"]
        if r.get("correct") is not None
    }


def lme_vec(rel: str, field: str = "hit_at_5") -> dict[str, bool]:
    """question_id -> hit, from a bench-lme retrieval report. Abstention rows
    carry no hit and are excluded exactly as bench_lme's own recall excludes
    them."""
    return {
        r["question_id"]: bool(r[field])
        for r in _load(rel)["per_question"]
        if not r.get("is_abstention") and r.get(field) is not None
    }


def lme_abs_vec(rel: str) -> dict[str, bool]:
    """The abstention sentinel: abstention_correct over the is_abstention rows."""
    return {
        r["question_id"]: bool(r["abstention_correct"])
        for r in _load(rel)["per_question"]
        if r.get("is_abstention")
    }


def max_rank(rel: str) -> int:
    ranks = [
        r["first_answer_rank"]
        for r in _load(rel)["per_question"]
        if r.get("first_answer_rank") is not None
    ]
    return max(ranks) if ranks else 0


def trackr_arm_vec(rel: str, arm: str, k: int) -> dict[str, bool]:
    return {q: bool(v) for q, v in _load(rel)["arms"][arm][f"per_question_hit_at_{k}"].items()}


def trackr_flat_vec(rel: str, field: str) -> dict[str, bool]:
    return {r["question_id"]: bool(r[field]) for r in _load(rel)["per_question"]}


def paraphrase_vec(field: str) -> dict[str, bool]:
    rows = _load("docs/build-log/artifacts/track-r-paraphrase/w0-2-five-arm.json")["per_question"]
    return {r.get("question_id", str(i)): bool(r[field]) for i, r in enumerate(rows)}


def forgeteval_vec(rel: str) -> dict[str, bool]:
    """case_id -> passed, over the scorable (non-N/A) cases."""
    out = {}
    for case in _load(rel)["results"]["cases"]:
        outcome = case["outcome"]
        if outcome not in ("pass", "fail"):
            continue  # not_applicable: 126 rows, identical in both arms
        out[case["case_id"]] = outcome == "pass"
    return out


def memora_vec(rel: str) -> dict[str, bool]:
    """evaluation_question_id -> is_correct, over the 71 subquestions."""
    out = {}
    for result in _load(rel)["results"]:
        for q in result.get("evaluation_questions", []):
            out[q["evaluation_question_id"]] = bool(q["evaluation_result"]["is_correct"])
    return out


def pooled(loader, *rels) -> dict[str, bool]:
    """Join several arms of the same lever across corpus versions, namespacing
    the question ids so v1 and v2 rows cannot collide."""
    out: dict[str, bool] = {}
    for i, rel in enumerate(rels):
        for q, v in loader(rel).items():
            out[f"v{i + 1}:{q}"] = v
    return out


def pair(after: dict[str, bool], before: dict[str, bool]):
    """(n, both, after_only, before_only, neither) over the joined keys."""
    keys = sorted(set(after) & set(before))
    both = sum(1 for q in keys if after[q] and before[q])
    a_only = sum(1 for q in keys if after[q] and not before[q])
    b_only = sum(1 for q in keys if before[q] and not after[q])
    return len(keys), both, a_only, b_only, len(keys) - both - a_only - b_only


# ---------------------------------------------------------------------------
# the audited conclusions
# ---------------------------------------------------------------------------

TR_ARMS = "docs/build-log/artifacts/track-r/track_r_phase1r_retrieval_arms.json"
TR_C = "docs/build-log/artifacts/track-r/track_r_phase1c_scoped_bm25_comparison.json"
PARA = "docs/build-log/artifacts/track-r-paraphrase/w0-2-five-arm.json"
LCS = "docs/build-log/artifacts/lme-cleaned-split"
WAVE = "docs/build-log/artifacts/wave-20260711"
R1 = "docs/build-log/artifacts/r1-docs"
R15 = "docs/build-log/artifacts/r15-docs"
R0 = "docs/build-log/artifacts/r0-embedder/chat"
SG = "docs/build-log/artifacts/syndai-gate"
FE = "docs/build-log/artifacts/next-evidence/forgeteval"
RG = "docs/build-log/artifacts/rung7-packing-reader-gate"
MEM13 = "docs/build-log/artifacts/unified-sota-20260713/task4-memora"
MEM14 = "docs/build-log/artifacts/unified-sota-20260714/task4-memora-luna"

ENTRIES: list[dict] = []


def add(
    ident,
    lane,
    doc,
    quote,
    artifact,
    *,
    cells=None,
    rows=None,
    unrecoverable=None,
    care=D_MIN,
    load_bearing="",
    register="not covered by the 2026-07-31 instrument register",
    note="",
    cost_if_wrong="",
    duplicate_of=None,
    recorded_null=True,
):
    recovered = rows is not None
    n = both = a_only = b_only = neither = None
    err = unrecoverable
    if recovered and not err:
        try:
            n, both, a_only, b_only, neither = rows()
        except (FileNotFoundError, KeyError, TypeError) as exc:
            recovered = False
            err = f"rows not loadable: {type(exc).__name__}: {exc}"
    elif cells is not None:
        n, both, a_only, b_only, neither = cells
    res = classify(n, a_only, b_only, care, err)
    if not recorded_null:
        # never recorded as a null -- a positive/negative control for the method
        res["classification"] = "CONTROL (" + res["classification"] + ")"
    ENTRIES.append(
        {
            "id": ident,
            "lane": lane,
            "source_doc": doc,
            "recorded_as": quote,
            "artifact": artifact,
            "artifact_provenance": _PROVENANCE.get(artifact.split(" ")[0].rstrip(","), {}),
            "rows_recovered": recovered,
            "n": n,
            "both": both,
            "after_only": a_only,
            "before_only": b_only,
            "neither": neither,
            **res,
            "recorded_as_null": recorded_null,
            "load_bearing": load_bearing,
            "register_status": register,
            "duplicate_of": duplicate_of,
            "note": note,
            "cost_if_wrong": cost_if_wrong,
        }
    )


# ===========================================================================
# A. Phase 1r, coding lane (2026-07-30)
# ===========================================================================

add(
    "phase1r-armB-at10",
    "repo/code",
    "2026-07-30-phase1r-retrieval-bm25.md:23",
    "5/3, p = 0.727 -- null",
    f"{TR_ARMS} (armB_bm25_control vs scoped BM25, k=10)",
    rows=lambda: pair(
        trackr_arm_vec(TR_ARMS, "armB_bm25_control", 10),
        trackr_flat_vec(TR_C, "bm25_scoped_hit_at_10"),
    ),
    load_bearing="YES -- this null is the entire justification for the A' code-aware "
    "tokenizer increment ('only the tokenizer change makes k=10 significant').",
    note="control vector taken from the committed Phase 1c comparison; the re-derived "
    "cells reproduce the committed 2x2 exactly, which independently confirms the log's "
    "claim that the re-run control is byte-identical.",
    cost_if_wrong="plain BM25 may already beat the lexical control at k=10, which would make "
    "the code-aware tokenizer a smaller increment than credited.",
)
add(
    "phase1r-armC-at5",
    "repo/code",
    "2026-07-30-phase1r-retrieval-bm25.md:25,116",
    "15/24, p = 0.200 -- null; 'only up to a null vs BM25'",
    f"{TR_ARMS} (armC_dense_overlap vs scoped BM25, k=5)",
    rows=lambda: pair(
        trackr_arm_vec(TR_ARMS, "armC_dense_overlap", 5),
        trackr_flat_vec(TR_C, "bm25_scoped_hit_at_5"),
    ),
    load_bearing="YES -- one of the two cells behind 'dense embeddings did not work on this "
    "lane' (2026-07-30-coding-lane-first-win.md:60).",
    note="point estimate is NEGATIVE (-5.0pt) and was reported as 'null'.",
    cost_if_wrong="dense retired on the coding lane on a contaminated bank.",
)
add(
    "phase1r-armC-at10",
    "repo/code",
    "2026-07-30-phase1r-retrieval-bm25.md:25,117",
    "16/15, p = 1.000 -- null",
    f"{TR_ARMS} (armC_dense_overlap vs scoped BM25, k=10)",
    rows=lambda: pair(
        trackr_arm_vec(TR_ARMS, "armC_dense_overlap", 10),
        trackr_flat_vec(TR_C, "bm25_scoped_hit_at_10"),
    ),
    load_bearing="YES -- the other cell behind 'dense did not work'.",
    note="MEMORA MODE, and the clearest instance in the window: marginals 0.9000 vs 0.8944, "
    "one question apart, over 31 discordant cells. Dense and BM25 are not one system "
    "agreeing with itself; they are two systems disagreeing on 17% of the bank and "
    "netting to nothing.",
    cost_if_wrong="the two channels are complementary on a sixth of the bank. A fusion keeping "
    "both could exceed either, and the recorded reading was 'dense adds nothing'.",
)
add(
    "phase1r-densebm25code-at5",
    "repo/code",
    "2026-07-30-phase1r-retrieval-bm25.md:122",
    "-10/+3 at k=5 (p = 0.092) ... neither loss is significant",
    f"{TR_ARMS} (armABC vs armAB, k=5 -- re-derived, never published as a 2x2)",
    rows=lambda: pair(
        trackr_arm_vec(TR_ARMS, "armABC_dense_bm25_code", 5),
        trackr_arm_vec(TR_ARMS, "armAB_bm25_code", 5),
    ),
    load_bearing="partly -- supports 'hybrid fusion is not recommended on this lane'.",
    cost_if_wrong="direction is negative, so the risk runs the other way: 'not significant' kept "
    "the hybrid alive as an option it may not deserve.",
)
add(
    "phase1r-densebm25code-at10",
    "repo/code",
    "2026-07-30-phase1r-retrieval-bm25.md:122",
    "-3/+2 at k=10 (p = 1.000)",
    f"{TR_ARMS} (armABC vs armAB, k=10 -- re-derived)",
    rows=lambda: pair(
        trackr_arm_vec(TR_ARMS, "armABC_dense_bm25_code", 10),
        trackr_arm_vec(TR_ARMS, "armAB_bm25_code", 10),
    ),
    load_bearing="partly -- as above.",
    cost_if_wrong="low: both arms sit at 0.95+, so the headroom is 4 questions.",
)
add(
    "phase1r-lme-n30",
    "episodic/chat",
    "2026-07-30-phase1r-retrieval-bm25.md:180",
    "+2/-1 ... exact p = 1.000",
    "docs/build-log/artifacts/track-r/track_r_phase1r_lme_s_nonregression.json "
    "(lme_s_n30_seed1, k=5)",
    cells=(28, 16, 2, 1, 11),
    load_bearing="NO -- superseded in the same table by the n=120 arm, which rejects "
    "(+10/-1, p = 0.0117).",
    register="register flags the p1r-small arm; this entry supplies its cells",
    note="cells committed; the per-arm reports under track-r/phase1r/ are gitignored, so "
    "the rows are NOT recoverable. Classified from committed cells, not re-derived.",
    cost_if_wrong="none.",
)

# --- exact-channel magnitude / packing non-regressions ---------------------

add(
    "packadj-exact-at5",
    "repo/code",
    "2026-07-30-exact-channel-magnitude.md:252",
    "exact p = 1.0 at both k ... Zero flips in either direction",
    "docs/build-log/artifacts/track-r/track_r_packadj_exact_magnitude.json (paired_at_5)",
    cells=(180, 166, 0, 0, 14),
    load_bearing="NO for the statistic; YES for the identity claim beside it.",
    register="register: 'valid as non-regression, invalid as evidence of equivalence' -- confirmed",
    note="n_d = 0. The document is RIGHT that the real evidence is byte-identity of the "
    "packed context ('packed_context_identical: true', and it says so explicitly). The "
    "p = 1.0 printed beside it is vacuous and must never be cited on its own.",
    cost_if_wrong="none -- identity is not a statistical claim.",
)
add(
    "packadj-exact-at10",
    "repo/code",
    "2026-07-30-exact-channel-magnitude.md:252",
    "exact p = 1.0 at both k",
    "docs/build-log/artifacts/track-r/track_r_packadj_exact_magnitude.json (paired_at_10)",
    cells=(180, 168, 0, 0, 12),
    load_bearing="NO for the statistic.",
    register="as above",
    note="as above.",
    cost_if_wrong="none.",
)

# ===========================================================================
# B. W0.3 cleaned split (2026-07-31)
# ===========================================================================

add(
    "w03-cleaned-split-at5",
    "episodic/chat",
    "2026-07-31-lme-cleaned-split.md:16",
    "exact two-sided McNemar p = 1.0 (0 arm-only wins, 1 baseline-only win)",
    f"{LCS}/{{cleaned,deprecated}}-n100-seed20260710.json (k=5)",
    rows=lambda: pair(
        lme_vec(f"{LCS}/cleaned-n100-seed20260710.json", "hit_at_5"),
        lme_vec(f"{LCS}/deprecated-n100-seed20260710.json", "hit_at_5"),
    ),
    load_bearing="YES -- cited in 2026-07-31-w2-reader-composition-prereg.md:166 to release "
    "a W2.1 blocker ('W0.3 reported p=1.0, no movement').",
    note="the CONCLUSION nevertheless stands on separate, non-statistical evidence: 23,854 of "
    "23,854 retained sessions are byte-identical and the cleaning removes 0.07% of turns. "
    "That corpus diff is the proof; the McNemar is not.",
    cost_if_wrong="low -- a corpus identical on 99.93% of its turns cannot host a 7pt effect. "
    "The defect is that the p-value was cited as the evidence.",
)
add(
    "w03-cleaned-split-at10",
    "episodic/chat",
    "2026-07-31-lme-cleaned-split.md:137",
    "exact two-sided McNemar p | 1.0 -- null",
    f"{LCS}/{{cleaned,deprecated}}-n100-seed20260710.json (k=10)",
    rows=lambda: pair(
        lme_vec(f"{LCS}/cleaned-n100-seed20260710.json", "hit_at_10"),
        lme_vec(f"{LCS}/deprecated-n100-seed20260710.json", "hit_at_10"),
    ),
    load_bearing="NO -- see duplicate_of.",
    register="register §4.1: hit@10 is hit@5 relabelled on this slice; this entry tests that",
    duplicate_of="w03-cleaned-split-at5",
    note="NOT an independent result. Verified at runtime: the k=5 and k=10 hit vectors are "
    "identical on every scored row in both arms. Reported as a second null in the same "
    "table; it is the same measurement printed twice.",
    cost_if_wrong="none -- it is the same cell.",
)

# ===========================================================================
# C. W0.2 paraphrase arms -- the known FALSE NULL, re-verified
# ===========================================================================

add(
    "w02-dense-paraphrase-at5",
    "repo/code",
    "2026-07-31-w0-2-paraphrase-arms.md:263",
    "Dense flips from null to strongly positive -- 'a false null of exactly the class "
    "the held null-review exists to catch'",
    f"{PARA} (paired_vs_baseline_arm.overlap_dense.fused_at_5 -- committed cells)",
    cells=(180, 14, 38, 1, 127),
    load_bearing="YES -- ANCHOR. This is the win the program nearly abandoned.",
    note="LINEAGE CAVEAT: the W0.2 arms were built from `af-w0-instrument`, which contains "
    "neither f67f2b2a nor 3fc4eede; the log's own 2026-07-31 correction requires a re-run "
    "on trunk before the numbers are cited again. That correction states the FUSED figures "
    "are unaffected (retrieval is upstream of both fixes), and every paraphrase entry in "
    "this ledger uses fused hits, not packed. "
    "Confirms the standing finding from rows: the original bank's lexical give-away "
    "masked the dense channel. Same lever, leak-free bank, opposite verdict.",
    cost_if_wrong="already realized.",
)
add(
    "w02-dense-paraphrase-at10",
    "repo/code",
    "2026-07-31-w0-2-paraphrase-arms.md:263",
    "Dense flips from null to strongly positive (k=10)",
    f"{PARA} (overlap_dense vs overlap_off, fused hit@10)",
    rows=lambda: pair(
        paraphrase_vec("overlap_dense_fused_hit_at_10"),
        paraphrase_vec("overlap_off_fused_hit_at_10"),
    ),
    load_bearing="YES -- ANCHOR.",
    cost_if_wrong="already realized.",
)
add(
    "w02-dense-vs-bm25code-para-at5",
    "repo/code",
    "2026-07-31-w0-2-paraphrase-arms.md",
    "(leak-free restatement of phase1r-armC: does dense still lose to BM25 once the "
    "give-away is removed)",
    f"{PARA} (overlap_dense vs bm25code_off at k=5)",
    unrecoverable="the paraphrase artifact's per_question rows carry ONLY *_hit_at_10; the "
    "k=5 cells are committed only against the BASELINE arm (overlap_off), not arm-vs-arm. "
    "This arm-vs-arm k=5 contrast is not recoverable without a re-run.",
    load_bearing="this is the decontaminated version of the load-bearing comparison.",
    cost_if_wrong="decides whether the coding lane's default should be lexical-only at all.",
)
add(
    "w02-dense-vs-bm25code-para-at10",
    "repo/code",
    "2026-07-31-w0-2-paraphrase-arms.md",
    "(as above, k=10)",
    f"{PARA} (overlap_dense vs bm25code_off, fused hit@10)",
    rows=lambda: pair(
        paraphrase_vec("overlap_dense_fused_hit_at_10"),
        paraphrase_vec("bm25code_off_fused_hit_at_10"),
    ),
    load_bearing="as above.",
    cost_if_wrong="as above.",
)
add(
    "w02-hybrid-vs-bm25code-para-at5",
    "repo/code",
    "2026-07-31-w0-2-paraphrase-arms.md",
    "(leak-free restatement of 'dense adds nothing on top of BM25')",
    f"{PARA} (bm25code_dense vs bm25code_off at k=5)",
    unrecoverable="as above -- per_question rows are k=10 only, and the committed k=5 cells "
    "are against the baseline arm, not this pair.",
    load_bearing="YES -- the hybrid was dropped on the contaminated bank's evidence.",
    cost_if_wrong="if the hybrid wins on the clean bank, 'hybrid fusion is not recommended' is "
    "backwards and the lane's default is wrong.",
)
add(
    "w02-hybrid-vs-bm25code-para-at10",
    "repo/code",
    "2026-07-31-w0-2-paraphrase-arms.md",
    "(as above, k=10)",
    f"{PARA} (bm25code_dense vs bm25code_off, fused hit@10)",
    rows=lambda: pair(
        paraphrase_vec("bm25code_dense_fused_hit_at_10"),
        paraphrase_vec("bm25code_off_fused_hit_at_10"),
    ),
    load_bearing="YES -- as above.",
    cost_if_wrong="as above.",
)

# ===========================================================================
# D. R1 docs gate (2026-07-12) -- reader QA, pooled over corpus versions
# ===========================================================================

add(
    "r1-a1-breadcrumb-null",
    "semantic/docs",
    "2026-07-12-r1-docs-gate.md:32",
    "Lever NULL (attr -0.017 [-0.067,+0.033])",
    f"{R1}/modernbert+bc vs {R1}/modernbert, reader QA, v1+v2 pooled",
    rows=lambda: pair(
        pooled(reader_vec, f"{R1}/modernbert+bc/v1/reader.json", f"{R1}/modernbert+bc/v2/reader.json"),
        pooled(reader_vec, f"{R1}/modernbert/v1/reader.json", f"{R1}/modernbert/v2/reader.json"),
    ),
    load_bearing="YES -- the breadcrumb lever was dropped on this, and it is cheap and would "
    "apply to every docs-lane chunk.",
    note="pooled over the two corpus versions, matching how the log reports the attribution "
    "CI. Adjudicated at the time on a bootstrap CI; the exact paired test was never run.",
    cost_if_wrong="a free structural lever abandoned.",
)
add(
    "r1-a4-chunks-null",
    "semantic/docs",
    "2026-07-12-r1-docs-gate.md:36",
    "Chunks null at shallow k (attr -0.017 ns)",
    f"{R1}/chunks-k10 vs {R1}/modernbert, reader QA, v1+v2 pooled",
    rows=lambda: pair(
        pooled(reader_vec, f"{R1}/chunks-k10/v1/reader.json", f"{R1}/chunks-k10/v2/reader.json"),
        pooled(reader_vec, f"{R1}/modernbert/v1/reader.json", f"{R1}/modernbert/v2/reader.json"),
    ),
    load_bearing="partly -- stopped the chunk-granularity investigation at shallow k.",
    cost_if_wrong="chunk granularity is structural; a wrong null here closed a whole axis.",
)
add(
    "r1-d2-parity",
    "semantic/docs",
    "2026-07-12-r1-docs-gate.md:34",
    "Parity at 7x volume",
    f"{R1}/diag-k25b16 vs Syndai, reader QA, v1+v2 pooled",
    rows=lambda: pair(
        pooled(reader_vec, f"{R1}/diag-k25b16/v1/reader.json", f"{R1}/diag-k25b16/v2/reader.json"),
        pooled(reader_vec, f"{SG}/reader-syndai.json", f"{R1}/syndai/v2/reader.json"),
    ),
    load_bearing="partly -- 'parity at 7x volume' was read as 'volume is free'.",
    note="the Syndai v1 arm is `syndai-gate/reader-syndai.json` (reused from W10 per "
    "r1-verdict-cis.json's `v1_reused_from_w10`; 60/60 question ids join the r1 v1 set and "
    "its QA 0.2167 is the .217 in the table). Not obvious from the r1-docs/ layout, which "
    "holds only the v2 arm.",
    cost_if_wrong="if volume is really a win or a loss, the budget policy is set on a coin flip.",
)

# ===========================================================================
# E. R1.5 rank compression (2026-07-12) -- the docs-lane unlock decision
# ===========================================================================

add(
    "r15-r6-parity",
    "semantic/docs",
    "2026-07-12-r15-rank-compression.md:16",
    "+0.083 [+0.000,+0.167] -- floor exactly 0.000 -- NOT unlocked. "
    "'Parity-to-better at comparable volume is real; proof is not'",
    f"{R15}/L1XC vs Syndai (v1 = {SG}/reader-syndai.json, v2 = {R1}/syndai/v2), pooled",
    rows=lambda: pair(
        pooled(reader_vec, f"{R15}/L1XC/v1/reader.json", f"{R15}/L1XC/v2/reader.json"),
        pooled(reader_vec, f"{SG}/reader-syndai.json", f"{R1}/syndai/v2/reader.json"),
    ),
    load_bearing="YES -- this is the docs-lane R6 unlock decision.",
    note="adjudicated on a bootstrap CI whose floor touched exactly zero. The exact paired "
    "test was never run on these rows.",
    cost_if_wrong="a docs-lane unlock deferred. The single most consequential 'not proven' in "
    "the window if the exact test rejects.",
)

# ===========================================================================
# F. Accuracy wave (2026-07-11)
# ===========================================================================

add(
    "wave-session-quota-qa-null",
    "episodic/chat",
    "2026-07-11-accuracy-wave.md:28",
    "mechanism proven in tests; QA null (-0.010 [-0.050,+0.020])",
    f"{WAVE}/reader-wave-quota.json vs {WAVE}/reader-wave-base-v1.json",
    rows=lambda: pair(
        reader_vec(f"{WAVE}/reader-wave-quota.json"),
        reader_vec(f"{WAVE}/reader-wave-base-v1.json"),
    ),
    load_bearing="YES -- session_quota is still default-off on the chat lane on this.",
    cost_if_wrong="a shipped-but-disabled mechanism.",
)
add(
    "wave-sibling-qa",
    "episodic/chat",
    "2026-07-11-accuracy-wave.md (sibling_gather arm)",
    "(later declared measured-dead; deletion proposed)",
    f"{WAVE}/reader-wave-sibling.json vs {WAVE}/reader-wave-base-v1.json",
    rows=lambda: pair(
        reader_vec(f"{WAVE}/reader-wave-sibling.json"),
        reader_vec(f"{WAVE}/reader-wave-base-v1.json"),
    ),
    load_bearing="YES -- a deletion is pending on the measured-dead verdict.",
    note="the deletion ALSO rests on a separate band-emptiness argument, which does hold "
    "independently of this statistic.",
    cost_if_wrong="deleting a mechanism on a non-measurement. Recoverable from git, so the cost "
    "is the re-derivation, not the code.",
)
add(
    "wave-cleanup-neutral",
    "episodic/chat",
    "2026-07-11-accuracy-wave.md:20",
    "+0.030 [-0.030, +0.090] ns -- honest label: cleanup-neutral-to-mildly-up",
    f"{WAVE}/reader-wave-base-v3.json vs {WAVE}/reader-wave-base-v1.json",
    rows=lambda: pair(
        reader_vec(f"{WAVE}/reader-wave-base-v3.json"),
        reader_vec(f"{WAVE}/reader-wave-base-v1.json"),
    ),
    load_bearing="partly -- licensed a prompt/harness cleanup as non-harmful.",
    cost_if_wrong="low; the direction claimed is already 'mildly up'.",
)

# ===========================================================================
# G. R0 embedder bakeoff (2026-07-11)
# ===========================================================================

add(
    "r0-small-vs-base-parity",
    "episodic/chat",
    "2026-07-11-r0-embedder-bakeoff.md:32",
    "text-embedding-3-small@1536 (Syndai-parity control) | .167 | .083",
    f"{R0}/reader-r0-chat-small-20260710.json vs {R0}/reader-r0-chat-base-20260710.json",
    rows=lambda: pair(
        reader_vec(f"{R0}/reader-r0-chat-small-20260710.json"),
        reader_vec(f"{R0}/reader-r0-chat-base-20260710.json"),
    ),
    load_bearing="YES -- the embedder default for the whole chat lane.",
    cost_if_wrong="the wrong embedder as a program-wide default.",
)

# ===========================================================================
# H. Register spot-checks -- verify, do not redo
# ===========================================================================

add(
    "memora-flat-4371",
    "temporal/state",
    "STATUS via 2026-07-31-instrument-register.md:465",
    "raw unweighted accuracy stayed flat at 43/71 vs the pilot's 44/71",
    f"{MEM13}/weekly-software-engineer.fama.json vs {MEM14}/full.fama.json",
    rows=lambda: pair(
        memora_vec(f"{MEM14}/full.fama.json"),
        memora_vec(f"{MEM13}/weekly-software-engineer.fama.json"),
    ),
    load_bearing="YES -- ANCHOR, and the register already reversed the reading.",
    register="register claims b=13, c=12, psi=0.352, 25 discordant of 71 -- SPOT-CHECKED HERE",
    note="CARRY THE NESTING: the 71 subquestions sit inside 15 parent questions (mean 4.7), "
    "so exact McNemar at n=71 is ANTICONSERVATIVE and the MDE printed here is a FLOOR on "
    "the true MDE, not the MDE. The effective n is nearer 15.",
    cost_if_wrong="the replay changed a third of the graded cells; treating it as a one-cell "
    "delta mis-sizes every downstream power estimate on this lane.",
)
add(
    "abs-sentinel-packrendercap",
    "episodic/chat",
    "register §4.1 / plan :96-97 (the screen that killed pack_render_cap)",
    "the 2-discordant-pair abstention screen, exact p = 0.50",
    f"{RG}/{{baseline,rendercap1200}}-retrieval.json (abstention_correct over is_abstention rows)",
    rows=lambda: pair(
        lme_abs_vec(f"{RG}/rendercap1200-retrieval.json"),
        lme_abs_vec(f"{RG}/baseline-retrieval.json"),
    ),
    load_bearing="YES -- it killed a lever worth +0.235 retrieval; already rescinded in Phase 0.",
    register="register: n=12, b=0, c=2, p=0.50 -- SPOT-CHECKED HERE",
    cost_if_wrong="already paid and already reversed.",
)
add(
    "forgeteval-lineage-complete",
    "forgetting/lifecycle",
    "register §4.4 / 2026-07-24-forgeteval-next-evidence.md",
    "(positive control for this audit: 111 paired gains, zero baseline regressions)",
    f"{FE}/adversarial-385-{{baseline-instrumented,lineage-complete}}.json",
    rows=lambda: pair(
        forgeteval_vec(f"{FE}/adversarial-385-lineage-complete.json"),
        forgeteval_vec(f"{FE}/adversarial-385-baseline-instrumented.json"),
    ),
    load_bearing="not a null -- included as the audit's positive control.",
    register="register claims b=111, c=0, n=259 -- SPOT-CHECKED HERE",
    note="a rejection this method must reproduce; if it does not, the method is wrong.",
    cost_if_wrong="n/a.",
    recorded_null=False,
)
add(
    "syndai-docs-qa",
    "semantic/docs",
    "2026-07-11-syndai-gate.md / register 2B row 11",
    "(the one negative result the register says clears its own MDE; psi recorded as a "
    "LOWER BOUND because b and c were never committed)",
    f"{SG}/reader-memphant.json vs {SG}/reader-syndai.json",
    rows=lambda: pair(
        reader_vec(f"{SG}/reader-memphant.json"),
        reader_vec(f"{SG}/reader-syndai.json"),
    ),
    load_bearing="YES -- the C2 docs-slice drop rests on it.",
    register="REGISTER CORRECTION: it records psi >= 0.1667 as unrecoverable from "
    "gate_compare.json's bootstrap CI. The per-question rows DO survive in "
    "reader-memphant.json / reader-syndai.json, so the true psi is computed here.",
    cost_if_wrong="n/a for the decision (it is a loss, not a null), but the register's MDE for "
    "this lane was computed from a lower-bound psi and needs the exact value.",
    recorded_null=False,
)
add(
    "syndai-docs-hit10",
    "semantic/docs",
    "2026-07-11-syndai-gate.md / register 2B row 11",
    "delta_hit_at_10 = -0.1333, CI [-0.25, -0.0333]",
    f"{SG}/gate_compare.json (bootstrap CI only)",
    unrecoverable="gate_compare.json commits only the bootstrap CI on the retrieval delta; "
    "b and c were never written down and no per-question RETRIEVAL vector is banked "
    "(only the reader vectors are). psi is a lower bound of 0.1333 forever. This is "
    "governance item Z6, and it is unrecoverable without a re-run.",
    load_bearing="YES -- half the C2 drop rationale.",
    register="register agrees psi is a lower bound; this entry classifies it as "
    "NON-MEASUREMENT-by-unrecoverability rather than leaving it as a bounded number",
    cost_if_wrong="the decision direction is negative and the QA leg is recoverable, so the "
    "drop survives; what is lost is the ability to state the resolution.",
)
add(
    "coding-events-golden-bm25",
    "repo/code",
    "benchmarks/manifests/code_lane_controls.2026-07-13.json",
    "paired_delta_recall_at_10 = -0.05",
    "benchmarks/manifests/code_lane_controls.2026-07-13.json (delta only)",
    unrecoverable="only the paired delta was published; b and c were never committed and no "
    "per-question vector is banked. psi >= 0.05 is a bound, not a value. Also n=40 in "
    "ONE repo with a 4-question held-out slice.",
    load_bearing="partly -- cited as early evidence the coding lane was not working.",
    register="register: 'UNPOWERABLE at n=40; no conclusion on this bank at any k is "
    "defensible' -- CONFIRMED, and sharpened to unrecoverable",
    cost_if_wrong="low on its own, but it contributed to the mood that the coding lane was dead "
    "before the paraphrase bank existed.",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    # The @10 relabelling trap, checked rather than assumed.
    rank_probe = {
        rel: max_rank(rel)
        for rel in (
            f"{LCS}/cleaned-n100-seed20260710.json",
            f"{LCS}/deprecated-n100-seed20260710.json",
            f"{RG}/baseline-retrieval.json",
            f"{RG}/rendercap1200-retrieval.json",
        )
    }
    print("max first_answer_rank per LME artifact (hit@10 == hit@5 iff <= 5):")
    for rel, mx in rank_probe.items():
        print(f"  {mx:>3}  {rel}")
    print()

    counts: dict[str, int] = {}
    for e in ENTRIES:
        if e["duplicate_of"] is None:
            counts[e["classification"]] = counts.get(e["classification"], 0) + 1
        flag = " [DUP]" if e["duplicate_of"] else ""
        print(
            f"{e['id']:<32} n={str(e['n']):>4} "
            f"[{e['both']},{e['after_only']},{e['before_only']},{e['neither']}] "
            f"n_d={str(e['n_d']):>3} p={str(e['p_exact']):>8} "
            f"pw={str(e['power_at_dmin']):>6} mde={str(e['mde_at_80']):>8} "
            f"-> {e['classification']}{flag}"
        )
    print()
    for k, v in sorted(counts.items()):
        print(f"{k}: {v}")
    print(f"(excluded as duplicate measurements: {sum(1 for e in ENTRIES if e['duplicate_of'])})")

    if args.write:
        ledger = {
            "schema": "memphant.audit.null-review.v1",
            "generated_by": "scripts/audit_null_review.py --write",
            "build_log": "docs/build-log/2026-07-31-null-review.md",
            "test": "two-sided exact (conditional binomial) McNemar, alpha=0.05",
            "min_decisional_n_d": MIN_DECISIONAL_ND,
            "d_min": D_MIN,
            "power_engine": "scripts/instrument_power.py (unconditional; psi realized per result)",
            # This ledger is an INDEX over 30 separate 2x2s, not one result, so the
            # evidence contract's single power block cannot describe it. Encoded the
            # way the schema intends for exactly that case: every unrepresentable
            # field is the literal "unverified" and `decisional` is false. This
            # artifact must never carry a promotion or kill decision -- it audits
            # the artifacts that do.
            "evidence_contract": {
                "schema_version": 1,
                "decisional": False,
                "claim": (
                    "Re-derivation of 27 conclusions recorded as null/flat/parity in the "
                    "2026-07 window; per-result cells live in `entries`, not in this block."
                ),
                "power": {
                    "test": "two-sided exact (conditional binomial) McNemar",
                    "n": "unverified",
                    "b": "unverified",
                    "c": "unverified",
                    "n_d": "unverified",
                    "computed_by": "scripts/instrument_power.py",
                    "source": "per-entry; see entries[].artifact",
                },
                "harness": {
                    "embed_model": "unverified",
                    "scorer": "unverified",
                    "k": "unverified",
                    "budget": "unverified",
                    "flags": "unverified",
                    "command": "python3 scripts/audit_null_review.py --write",
                },
                "corpus": {
                    "sha256": "unverified",
                    "snapshot_id": "unverified",
                    "n_items": "unverified",
                },
            },
            "paid_api_spend_usd": 0,
            "read_roots": {"worktree": str(ROOT), "canonical_main": str(MAIN)},
            "max_first_answer_rank_probe": rank_probe,
            "counts_excluding_duplicates": counts,
            "artifact_provenance": _PROVENANCE,
            "entries": ENTRIES,
        }
        OUT.write_text(json.dumps(ledger, indent=2) + "\n")
        print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
