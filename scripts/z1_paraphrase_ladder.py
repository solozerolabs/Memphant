#!/usr/bin/env python3
"""Z1 -- recompute the Track R ladder on the decontaminated paraphrase bank.

Reads the raw per-arm provenance reports (memphant arms + the attempt-scoped
BM25 control), recomputes EVERY paired contrast at both stages and both k as a
full 2x2 (b and c reported, never a delta alone -- register action Z6), and
attaches exact-McNemar power/MDE computed from the REALIZED discordance of that
very contrast (never an assumed psi).

Two-sided exact McNemar has no rejection region below n_d = 6; contrasts under
that threshold are labelled NOT A MEASUREMENT rather than reported as nulls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from instrument_power import min_detectable_effect, power  # noqa: E402

# Below this many discordant pairs the two-sided exact test cannot reject at
# alpha=0.05 for ANY split, so "p > 0.05" carries no information.
MIN_ND_FOR_REJECTION = 6


def mcnemar_exact_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(b, c) + 1)) / 2.0**n
    return min(1.0, 2.0 * tail)


def cells(left: list[bool], right: list[bool]) -> dict:
    """Full 2x2 plus realized-discordance power. `b` favours left, `c` right."""
    both = sum(1 for x, y in zip(left, right) if x and y)
    b = sum(1 for x, y in zip(left, right) if x and not y)
    c = sum(1 for x, y in zip(left, right) if y and not x)
    n = len(left)
    n_d = b + c
    psi = n_d / n if n else 0.0
    delta = (b - c) / n if n else 0.0
    mde = min_detectable_effect(n, psi) if psi > 0 else None
    if n_d < MIN_ND_FOR_REJECTION:
        verdict = (
            f"NOT A MEASUREMENT (n_d={n_d} < {MIN_ND_FOR_REJECTION}; the "
            "two-sided exact test has no rejection region here)"
        )
    else:
        p = mcnemar_exact_p(b, c)
        direction = "left" if b > c else ("right" if c > b else "tie")
        verdict = (
            f"{'significant' if p < 0.05 else 'null'} at alpha=0.05, favours {direction}"
        )
    return {
        "both": both,
        "b_left_only": b,
        "c_right_only": c,
        "neither": n - both - b - c,
        "n": n,
        "n_discordant": n_d,
        "psi_realized": round(psi, 6),
        "delta": round(delta, 6),
        "mcnemar_exact_p": mcnemar_exact_p(b, c),
        "mde_at_80pct_power_realized_psi": (round(mde, 6) if mde is not None else None),
        "power_at_realized_delta": (
            round(power(n, psi, abs(delta)), 6) if psi > 0 and abs(delta) <= psi else None
        ),
        "verdict": verdict,
    }


def load_arm(path: Path) -> dict:
    report = json.loads(path.read_text())
    rows = {r["question_id"]: r for r in report["per_question"]}
    return {"report": report, "rows": rows}


def fused_hits(rows: dict, order: list[str], k: int) -> list[bool]:
    out = []
    for q in order:
        rank = rows[q].get("gold_fused_rank")
        out.append(rank is not None and rank <= k)
    return out


def packed_hits(rows: dict, order: list[str], k: int) -> list[bool]:
    return [bool(rows[q][f"hit_at_{k}"]) for q in order]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--control", required=True, type=Path)
    ap.add_argument("--arm", action="append", required=True, metavar="NAME=FILE")
    ap.add_argument("--contrast", action="append", default=[], metavar="LEFT:RIGHT")
    ap.add_argument(
        "--packed-status",
        default="LINEAGE-STALE (pre-f67f2b2a)",
        help=(
            "comparability stamp for every packed-stage figure; set to SOUND "
            "only when the arms were built with both render fixes present"
        ),
    )
    ap.add_argument(
        "--assert-ancestor",
        action="append",
        default=[],
        metavar="SHA",
        help=(
            "commit that MUST be an ancestor of the worktree HEAD the arms were "
            "built at; recorded in the lineage block as a checked assertion"
        ),
    )
    ap.add_argument(
        "--binary",
        action="append",
        default=[],
        metavar="PATH",
        help="binary whose sha256 is stamped into the lineage block",
    )
    ap.add_argument(
        "--harness-env",
        action="append",
        default=[],
        metavar="VAR=VALUE:REASON",
        help=(
            "environment variable that changes engine behaviour, stamped into "
            "the lineage block beside the git head; a write-path env var is as "
            "much a part of an artifact's lineage as the commit it was built at"
        ),
    )
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    control = json.loads(args.control.read_text())
    if control.get("scope") != "attempt":
        raise RuntimeError("control must be the attempt-scoped BM25 arm")
    order = [r["question_id"] for r in control["per_question"]]
    ctrl_rows = {r["question_id"]: r for r in control["per_question"]}

    arms = {}
    for spec in args.arm:
        name, _, fname = spec.partition("=")
        arms[name] = load_arm(args.run_dir / fname)

    # integrity: every arm on the same bank, corpus and question set
    shas = {a["report"]["golden_sha256"] for a in arms.values()}
    corpora = {a["report"]["corpus_sha256"] for a in arms.values()} | {
        control["corpus_sha256"]
    }
    if len(shas) != 1 or len(corpora) != 1:
        raise RuntimeError(f"arms disagree: golden={shas} corpus={corpora}")
    for name, a in arms.items():
        if set(a["rows"]) != set(order):
            raise RuntimeError(f"arm {name} has a different question set")

    # Lineage. Packed-stage figures are only comparable across artifacts built at
    # the same render lineage; fused-stage figures are not affected by it. Stamp
    # both the arms' git_head and the render-fix ancestry so a reader cannot pick
    # up a packed number without seeing what it is comparable to.
    arm_heads = sorted(
        {
            (a["report"].get("runtime_identity") or {})
            .get("repository", {})
            .get("git_head")
            for a in arms.values()
        }
        - {None}
    )
    worktree_head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Ancestry is CHECKED here, not asserted in prose. A packed figure whose
    # build lineage is missing a render fix is not comparable to one that has it.
    ancestry = {}
    for sha in args.assert_ancestor:
        rc = subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", sha, worktree_head]
        ).returncode
        ancestry[sha] = "ANCESTOR" if rc == 0 else "ABSENT"
    if args.assert_ancestor and any(v != "ANCESTOR" for v in ancestry.values()):
        raise RuntimeError(f"required render fixes absent from build lineage: {ancestry}")

    binaries = {}
    for p in args.binary:
        bp = Path(p)
        h = hashlib.sha256(bp.read_bytes()).hexdigest()
        binaries[bp.name] = {"path": str(bp), "sha256": h, "bytes": bp.stat().st_size}

    # Every arm must have been built at the head we just checked ancestry for,
    # otherwise the assertion above certifies a tree the arms did not run on.
    stray = [h for h in arm_heads if h != worktree_head]
    if stray:
        raise RuntimeError(
            f"arm git_head {stray} != worktree HEAD {worktree_head}; "
            "the ancestry assertion would certify the wrong tree"
        )

    lineage = {
        "arm_git_heads": arm_heads,
        "worktree_head": worktree_head,
        "render_fix_ancestry_checked": ancestry,
        "binaries": binaries,
        "harness_env": {
            spec.split("=", 1)[0]: {
                "value": spec.split("=", 1)[1].split(":", 1)[0],
                "reason": spec.split("=", 1)[1].split(":", 1)[1],
            }
            for spec in args.harness_env
        },
        "render_fixes_required_for_packed_comparability": {
            "f67f2b2a": "let a partially chunk-rendered item emit its whole body",
            "3fc4eede": "scale the Exact channel by its own subject-key coverage",
        },
        "packed_stage_status": args.packed_status,
        "fused_stage_status": (
            "SOUND -- render lineage is a packing-stage concern and does not "
            "affect retrieval or fusion"
        ),
        "note": (
            "Do NOT compare any packed figure here against a banked figure built "
            "at a different render lineage; that moves two variables at once."
        ),
    }

    out = {
        "schema": "memphant.eval.track-r-z1-paraphrase-ladder.v2",
        "paid_api_spend_usd": 0,
        "lineage": lineage,
        "golden_sha256": shas.pop(),
        "corpus_sha256": corpora.pop(),
        "n": len(order),
        "min_nd_for_rejection": MIN_ND_FOR_REJECTION,
        "power_model": (
            "two-sided exact McNemar, alpha=0.05; psi is the REALIZED "
            "discordance of each contrast, never assumed"
        ),
        "arms": {},
        "vs_control": {},
        "contrasts": {},
    }

    hits: dict[str, dict] = {}
    for name, a in arms.items():
        rep = a["report"]
        h = {}
        for k in (5, 10):
            h[("fused", k)] = fused_hits(a["rows"], order, k)
            h[("packed", k)] = packed_hits(a["rows"], order, k)
        hits[name] = h
        cc = rep.get("compiled_corpus", {})
        out["arms"][name] = {
            "lexical_scorer": rep.get("lexical_scorer"),
            "embed_model": rep.get("embed_model"),
            "recall_mode": rep.get("recall_mode"),
            "k": rep.get("k"),
            "budget_tokens": rep.get("budget_tokens"),
            "fused_recall_at_5": sum(h[("fused", 5)]) / len(order),
            "fused_recall_at_10": sum(h[("fused", 10)]) / len(order),
            "packed_recall_at_5": sum(h[("packed", 5)]) / len(order),
            "packed_recall_at_10": sum(h[("packed", 10)]) / len(order),
            "fused_hits_at_10": sum(h[("fused", 10)]),
            "packed_hits_at_10": sum(h[("packed", 10)]),
            "gold_in_pool": sum(
                1 for q in order if a["rows"][q].get("gold_in_pool")
            ),
            "drain": {
                "done_jobs": cc.get("done_jobs"),
                "pending_jobs": cc.get("pending_jobs"),
                "dead_jobs": cc.get("dead_jobs"),
                "episodic_units": cc.get("episodic_units"),
                "deduplicated_episodes": cc.get("deduplicated_episodes"),
            },
            "pack_drop_summary": rep.get("pack_drop_summary"),
            "timings_seconds": rep.get("timings"),
        }

    ctrl = {k: [bool(ctrl_rows[q][f"hit_at_{k}"]) for q in order] for k in (5, 10)}
    out["control"] = {
        "haystack": "events of the bound attempt only",
        "recall_at_5": control["recall_at_5"],
        "recall_at_10": control["recall_at_10"],
        "hits_at_5": sum(ctrl[5]),
        "hits_at_10": sum(ctrl[10]),
    }

    for name in arms:
        block = {}
        for stage in ("fused", "packed"):
            for k in (5, 10):
                cell = cells(hits[name][(stage, k)], ctrl[k])
                if stage == "packed":
                    cell["comparability"] = args.packed_status
                block[f"{stage}_at_{k}"] = cell
        out["vs_control"][name] = block

    for spec in args.contrast:
        left, _, right = spec.partition(":")
        block = {}
        for stage in ("fused", "packed"):
            for k in (5, 10):
                cell = cells(hits[left][(stage, k)], hits[right][(stage, k)])
                if stage == "packed":
                    cell["comparability"] = args.packed_status
                block[f"{stage}_at_{k}"] = cell
        out["contrasts"][spec] = block

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
