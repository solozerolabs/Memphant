#!/usr/bin/env python3
"""Stage-equalize coding-lane retrieval arms before a paid reader-QA run.

Why this script exists
----------------------
The coding lane has one permanently VOID number ("MemPhant 0.506 vs BM25
0.806") created by scoring one arm *after* packing against another arm's plain
top-k. The two runners emit their evidence at different stages by construction:

* ``code_lane_run_memphant.py`` writes the bodies ``/v1/recall`` returned, i.e.
  already budget-packed server-side at ``--budget-tokens``;
* ``code_lane_run_deterministic.py`` writes the raw BM25 top-k with no budget
  applied at all.

Handing those two files to a reader unchanged would give the arms different
amounts of context and reproduce the void comparison one layer downstream. This
script removes the asymmetry the only way that is defensible: it applies the
**same** ``gate_common.pack_evidence`` at the **same** k and the **same**
budget to **every** arm, including the ones already packed (for which the pass
is a no-op and is asserted to be one), and records the before/after size of
every arm so the equality is auditable rather than asserted.

It also mints the no-memory (closed-book) arm — the same questions with an
empty evidence list — because without it a reader-QA gap cannot distinguish
"memory helped" from "the reader already knew". SWE-ContextBench's first
tranche died of exactly that pathology.

Every output carries a lineage block: git head, binary sha256s, corpus and
golden sha256, harness env, and the sha256 of each arm's evidence before and
after equalization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import gate_common as gc  # noqa: E402

# Fields that must be byte-identical across arms for the comparison to be
# paired at all. The evidence list is the only thing an arm is allowed to vary.
PAIRED_FIELDS = ("question", "question_type", "gold_answer", "is_abstention")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_rows(rows: list[dict]) -> str:
    payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def count_tokens(body: str) -> int:
    """The packer's own tokenizer: unicode non-whitespace runs."""
    return len(re.findall(r"\S+", body))


def load_arm(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"{path} is empty")
    return rows


def arm_stats(rows: list[dict]) -> dict:
    items = [len(row["evidence"]) for row in rows]
    chars = [sum(len(item["body"]) for item in row["evidence"]) for row in rows]
    tokens = [sum(count_tokens(item["body"]) for item in row["evidence"]) for row in rows]
    return {
        "rows": len(rows),
        "items_total": sum(items),
        "items_mean": sum(items) / len(rows),
        "items_min": min(items),
        "items_max": max(items),
        "chars_total": sum(chars),
        "chars_mean": sum(chars) / len(rows),
        "chars_max": max(chars),
        "tokens_total": sum(tokens),
        "tokens_mean": sum(tokens) / len(rows),
        "tokens_max": max(tokens),
        "rows_with_zero_evidence": sum(1 for value in items if value == 0),
    }


def equalize(rows: list[dict], *, k: int, budget_tokens: int) -> tuple[list[dict], dict]:
    out: list[dict] = []
    changed_rows = 0
    truncated_rows = 0
    dropped_items = 0
    for row in rows:
        bodies = [item["body"] for item in row["evidence"]]
        packed, info = gc.pack_evidence(bodies, k=k, budget_tokens=budget_tokens)
        if packed != bodies:
            changed_rows += 1
        truncated_rows += int(bool(info["evidence_truncated_items"]))
        dropped_items += int(info["evidence_dropped_items"])
        new = dict(row)
        new["evidence"] = [
            {"rank": rank + 1, "session_id": None, "body": body}
            for rank, body in enumerate(packed)
        ]
        new["k"] = k
        new["abstained"] = len(packed) == 0
        out.append(new)
    return out, {
        "evidence_packer_sha256": gc.EVIDENCE_PACKER_CONFIG["sha256"],
        "k": k,
        "budget_tokens": budget_tokens,
        "rows_changed_by_equalization": changed_rows,
        "rows_truncated": truncated_rows,
        "items_dropped": dropped_items,
    }


def git_head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def git_dirty(root: Path) -> bool:
    out = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return bool(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", action="append", required=True, metavar="NAME=PATH",
                        help="retrieval-arm evidence JSONL, repeatable")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--budget-tokens", type=int, default=8192)
    parser.add_argument("--no-memory-arm", default="nomemory",
                        help="name of the minted closed-book arm ('' to skip)")
    parser.add_argument("--corpus-sha256", required=True)
    parser.add_argument("--golden-sha256", required=True)
    parser.add_argument("--binary", action="append", default=[], metavar="NAME=PATH",
                        help="binary whose sha256 is stamped into the lineage block")
    parser.add_argument("--harness-env", action="append", default=[], metavar="K=V")
    parser.add_argument("--limit", type=int, help="keep only the first N question ids (pilot slice)")
    args = parser.parse_args()

    root = SCRIPTS_DIR.parent
    arms: dict[str, Path] = {}
    for spec in args.arm:
        name, _, path = spec.partition("=")
        if not name or not path:
            parser.error(f"--arm expects NAME=PATH, got {spec!r}")
        if name in arms:
            parser.error(f"duplicate arm name {name!r}")
        arms[name] = Path(path)
    if args.no_memory_arm and args.no_memory_arm in arms:
        parser.error("--no-memory-arm collides with a supplied arm name")

    loaded = {name: load_arm(path) for name, path in arms.items()}

    # --- pairing contract: identical question sets and identical question text.
    reference_name = next(iter(loaded))
    reference = {row["question_id"]: row for row in loaded[reference_name]}
    order = [row["question_id"] for row in loaded[reference_name]]
    if len(reference) != len(order):
        raise ValueError(f"{reference_name} has duplicate question ids")
    for name, rows in loaded.items():
        indexed = {row["question_id"]: row for row in rows}
        if len(indexed) != len(rows):
            raise ValueError(f"{name} has duplicate question ids")
        if set(indexed) != set(reference):
            raise ValueError(
                f"arm {name!r} question set differs from {reference_name!r}: "
                f"{len(set(indexed) ^ set(reference))} ids disagree"
            )
        for qid, row in indexed.items():
            for field in PAIRED_FIELDS:
                if row.get(field) != reference[qid].get(field):
                    raise ValueError(
                        f"arm {name!r} disagrees with {reference_name!r} on "
                        f"{field!r} for {qid}: the arms are not the same bank"
                    )
        # re-materialize in the reference order so per-question vectors align
        loaded[name] = [indexed[qid] for qid in order]

    if args.limit is not None:
        if args.limit < 1 or args.limit > len(order):
            parser.error(f"--limit must be in 1..{len(order)}")
        order = order[: args.limit]
        keep = set(order)
        loaded = {
            name: [row for row in rows if row["question_id"] in keep]
            for name, rows in loaded.items()
        }

    if args.no_memory_arm:
        loaded[args.no_memory_arm] = [
            {**dict(reference[qid]), "evidence": [], "abstained": True, "k": args.k}
            for qid in order
        ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_arms: dict[str, dict] = {}
    for name, rows in loaded.items():
        before = arm_stats(rows)
        equalized, info = equalize(rows, k=args.k, budget_tokens=args.budget_tokens)
        after = arm_stats(equalized)
        out_path = args.out_dir / f"{name}-equalized-evidence.jsonl"
        gc.write_jsonl(out_path, equalized)
        manifest_arms[name] = {
            "source_path": str(arms[name]) if name in arms else "minted:no-memory",
            "source_sha256": sha256_file(arms[name]) if name in arms else None,
            "equalized_path": str(out_path),
            "equalized_sha256": sha256_file(out_path),
            "stage_before": before,
            "stage_after": after,
            "equalization": info,
        }

    # --- the assertion this script exists to make, stated numerically.
    scored = {n: v for n, v in manifest_arms.items() if n != args.no_memory_arm}
    token_means = {n: v["stage_after"]["tokens_mean"] for n, v in scored.items()}
    ceiling = max(token_means.values()) if token_means else 0.0
    floor = min(token_means.values()) if token_means else 0.0
    over_budget = {
        n: v["stage_after"]["tokens_max"]
        for n, v in scored.items()
        if v["stage_after"]["tokens_max"] > args.budget_tokens
    }
    if over_budget:
        raise RuntimeError(
            f"equalization failed: arms still exceed the budget: {over_budget}"
        )

    manifest = {
        "schema": "memphant.eval.code-lane-reader-stage-equalization.v1",
        "purpose": (
            "every arm packed by one packer at one k and one budget, so a reader "
            "gap cannot be an artifact of unequal context"
        ),
        "k": args.k,
        "budget_tokens": args.budget_tokens,
        "question_order_sha256": hashlib.sha256(
            json.dumps(order, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "n_questions": len(order),
        "no_memory_arm": args.no_memory_arm or None,
        "arms": manifest_arms,
        "stage_parity": {
            "tokens_mean_by_arm": token_means,
            "tokens_mean_spread": ceiling - floor,
            "tokens_mean_spread_ratio": (ceiling / floor) if floor else None,
            "all_arms_within_budget": True,
        },
        "lineage": {
            "git_head": git_head(root),
            "git_dirty": git_dirty(root),
            "worktree": str(root),
            "corpus_sha256": args.corpus_sha256,
            "golden_sha256": args.golden_sha256,
            "binaries": {
                spec.split("=", 1)[0]: sha256_file(Path(spec.split("=", 1)[1]))
                for spec in args.binary
            },
            "harness_env": dict(
                spec.split("=", 1) for spec in args.harness_env
            ),
            "prepare_script_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "pack_evidence_source_sha256": gc.EVIDENCE_PACKER_CONFIG["sha256"],
        },
    }
    manifest_path = args.out_dir / "stage-equalization.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({
        "n_questions": len(order),
        "arms": {n: round(v, 1) for n, v in token_means.items()},
        "tokens_mean_spread": round(ceiling - floor, 1),
        "manifest": str(manifest_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
