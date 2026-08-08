#!/usr/bin/env python3
"""Adherence death-from-below screening check (retrospective, $0).

QUESTION. The trivial fix for rule-violations is PROXIMITY: re-inject the top-N
rules right before actions. If that plausibly suffices, adherence is a prompt
change, not a product. We cannot A/B prompt variants retrospectively, but we can
test proximity's PREMISE: violations should occur DEEP in sessions, far from
where the rule entered context (rules enter at turn ~0 via AGENTS.md/session
policy auto-load).

DECISION RULE, fixed before any cell is seen:
- For each known correction event, depth = assistant-turn index of the violated
  action / total assistant turns in session (0..1).
- If MEDIAN depth >= 0.5 AND >= 70% of events have depth >= 0.3, proximity is
  PLAUSIBLE => verdict "PROMPT-CHANGE FIRST": the trivial rule must be tried in
  Syndai before any adherence product is built.
- If violations also occur shallow (>= 30% of events at depth < 0.3), proximity
  cannot explain them => verdict "ENFORCEMENT NICHE LIVE": re-injection alone is
  not a plausible fix for a material share.
- n < 5 usable events => "NOT A MEASUREMENT".

Events: the correction instances found by the 2026-08-05 demand analysis, located
by grepping user-turn text in the session JSONL. The violated action is the
correction turn itself minus one (the correction immediately follows the
violating behavior in all inspected cases).
"""

import glob
import json
import sys

EVENTS = [
    ("15403b3d", "full re-runs"),
    ("9e49b76b", "full gate locally"),
    ("ed4f8502", "always continue work until fully done"),
    ("38ba8780", "keep stopping"),
    ("c29d3978", "overly clever"),
    ("8cc9baa3", "contaminated"),
]
ROOTS = [
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Syndai/",
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Memphant/",
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Syndai--claude-worktrees-*/",
]


def turns(path):
    out = []
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("message") or {}
            role = m.get("role") or d.get("type")
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content
                                   if isinstance(c, dict))
            out.append((role, content or ""))
    return out


def main() -> int:
    depths = []
    for sid, needle in EVENTS:
        paths = []
        for r in ROOTS:
            paths += glob.glob(r + sid + "*.jsonl")
        if not paths:
            print(f"  {sid}: transcript not found — skipped")
            continue
        ts = turns(paths[0])
        a_idx = [i for i, (r, _) in enumerate(ts) if r == "assistant"]
        hit = next((i for i, (r, c) in enumerate(ts)
                    if r == "user" and needle.lower() in c.lower()), None)
        if hit is None or not a_idx:
            print(f"  {sid}: needle not found — skipped")
            continue
        prior_a = [j for j, i in enumerate(a_idx) if i < hit]
        depth = (len(prior_a) / len(a_idx)) if prior_a else 0.0
        depths.append(depth)
        print(f"  {sid}: violation at assistant-turn {len(prior_a)}/{len(a_idx)}"
              f" depth={depth:.2f}")

    n = len(depths)
    if n < 5:
        print(json.dumps({"verdict": "NOT A MEASUREMENT", "n": n}))
        return 1
    depths.sort()
    med = depths[n // 2]
    shallow = sum(d < 0.3 for d in depths) / n
    deep_share = sum(d >= 0.3 for d in depths) / n
    if med >= 0.5 and deep_share >= 0.7:
        verdict = "PROMPT-CHANGE FIRST — proximity plausible; trivial re-injection must be tried before any product"
    else:
        verdict = "ENFORCEMENT NICHE LIVE — material shallow-violation share; re-injection alone cannot explain"
    print(json.dumps({"n": n, "median_depth": round(med, 3),
                      "shallow_share": round(shallow, 3), "verdict": verdict}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
