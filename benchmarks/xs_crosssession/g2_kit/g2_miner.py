#!/usr/bin/env python3
"""G2 external-validation kit — runs entirely on the participant's machine.

PRIVACY CONTRACT (read this first):
- This script reads your local Claude Code transcripts
  (~/.claude/projects/*/*.jsonl) and NEVER transmits anything.
- Step 1 writes candidate correction turns to a LOCAL file for you to label.
- Step 2 aggregates your labels into COUNTS ONLY (g2_result.json). That file
  contains zero transcript content — open it and check before sending it back.

WHAT THIS MEASURES: when your coding agent got something wrong and you
corrected it, was the missing knowledge (a) already in your repo's docs/config,
(b) already in an agent memory/rules file (CLAUDE.md, AGENTS.md, .cursorrules,
notes), or (c) written down nowhere? You label; the script counts.

USAGE:
  python3 g2_miner.py extract   # writes g2_candidates.jsonl (LOCAL ONLY)
  # edit g2_candidates.jsonl: set "label" on each row to one of
  #   a  = knowledge was in the repo (docs, config, code comments)
  #   b  = knowledge was in an agent memory/rules file
  #   c  = knowledge was written down nowhere
  #   x  = not actually a correction (skip it)
  python3 g2_miner.py report    # writes g2_result.json (counts only) — send this
"""

import glob
import json
import os
import re
import sys
from pathlib import Path

PATTERNS = re.compile(
    r"\b(no[,.] |I (already )?told you|as I said|I thought (we|you)|you keep|"
    r"stop (doing|using|running)|don't (do|use|run) that|why (do|did) you|"
    r"we (never|don't|do not) (do|use|run)|that's (not right|wrong)|"
    r"actually,? we)\b", re.I)
HOME = str(Path.home())


def turns(path):
    out = []
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("message") or {}
            role, c = m.get("role"), m.get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c
                             if isinstance(x, dict) and x.get("type") == "text")
            if role in ("user", "assistant") and c:
                out.append((role, c))
    return out


def extract() -> int:
    cands, sessions = [], 0
    for f in glob.glob(HOME + "/.claude/projects/*/*.jsonl"):
        ts = turns(f)
        if len(ts) < 10:
            continue
        sessions += 1
        a_total = sum(1 for r, _ in ts if r == "assistant")
        a_seen = 0
        for role, c in ts:
            if role == "assistant":
                a_seen += 1
            elif PATTERNS.search(c) and len(c) < 2000 and not c.startswith("<"):
                cands.append({"session": Path(f).stem[:8],
                              "depth": round(a_seen / max(a_total, 1), 2),
                              "text": c[:500], "label": ""})
    with open("g2_candidates.jsonl", "w") as fh:
        for c in cands:
            fh.write(json.dumps(c) + "\n")
    print(f"{sessions} sessions scanned, {len(cands)} correction candidates "
          f"-> g2_candidates.jsonl\nNow label each row (a/b/c/x) and run: "
          f"python3 g2_miner.py report")
    return 0


def report() -> int:
    rows = [json.loads(l) for l in open("g2_candidates.jsonl")]
    labeled = [r for r in rows if r.get("label") in ("a", "b", "c")]
    if len(labeled) < 10:
        print(json.dumps({"error": "fewer than 10 labeled corrections — "
                          "not a measurement", "labeled": len(labeled)}))
        return 1
    n = len(labeled)
    out = {
        "sessions_with_candidates": len({r["session"] for r in rows}),
        "candidates_total": len(rows),
        "labeled": n,
        "a_in_repo": sum(r["label"] == "a" for r in labeled),
        "b_in_memory_file": sum(r["label"] == "b" for r in labeled),
        "c_nowhere": sum(r["label"] == "c" for r in labeled),
        "already_written_share": round(
            sum(r["label"] in ("a", "b") for r in labeled) / n, 3),
        "median_depth": sorted(r["depth"] for r in labeled)[n // 2],
        "shallow_share_lt_0.3": round(
            sum(r["depth"] < 0.3 for r in labeled) / n, 3),
    }
    with open("g2_result.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))
    print("\nWrote g2_result.json — counts only; inspect it, then send it back.")
    return 0


if __name__ == "__main__":
    sys.exit(extract() if sys.argv[1] == "extract" else report())
