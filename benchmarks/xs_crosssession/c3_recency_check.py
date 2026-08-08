#!/usr/bin/env python3
"""C3 belief-revision direction: death-from-below recency check.

If "serve the most recently named source file" already predicts the patched
file set, the direction is saturated by a ~10-line rule and dies before any
labeling spend (the MemoryCode recency failure in new clothing).

Three defects in the first version of this script, all fixed here and recorded
because each produced a clean-looking but meaningless number:
  1. paths were compared by exact string equality — gold is `src/apiron/client.py`
     while the agent says `client.py`, so nothing ever matched;
  2. the edit detector matched the substring "create" anywhere in the tool-call
     JSON, truncating the pre-edit prefix to ~4 messages of 109;
  3. files were read only from message content (3 per trajectory) and not from
     tool_calls (38 per trajectory), which is where localization actually shows.

Leakage guard: the agent's later messages contain the edit itself, so a
whole-trajectory score is trivially inflated. The honest ask is the PREFIX
before the first real edit — predict where the defect is before fixing it.
"""

import collections
import glob
import json
import re
import sys

FILE_RE = re.compile(r"([\w][\w/.-]*\.(?:py|js|jsx|ts|tsx|rs|go|java|rb|c|h|cpp))")
EDIT_CMDS = {"create", "str_replace", "insert"}


def norm(path: str) -> str:
    """Drop sandbox/workspace prefixes; keep the repo-relative tail."""
    p = path.lstrip("/")
    p = re.sub(r"^workspace/[^/]+/", "", p)
    return p


def same_file(a: str, b: str) -> bool:
    """Suffix match — `client.py` matches `src/apiron/client.py`."""
    a, b = norm(a), norm(b)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def hits(pred: set[str], gold: set[str]) -> int:
    return sum(1 for p in pred if any(same_file(p, g) for g in gold))


def files_in(msg) -> list[str]:
    found = FILE_RE.findall(str(msg.get("content") or ""))
    for tc in (msg.get("tool_calls") or []):
        found += FILE_RE.findall(json.dumps(tc))
    return found


def is_edit(msg) -> bool:
    for tc in (msg.get("tool_calls") or []):
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        if fn.get("name") != "str_replace_editor":
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            continue
        if str(args.get("command")) in EDIT_CMDS:
            return True
    return False


def prefix_before_first_edit(traj: list) -> list:
    for i, m in enumerate(traj):
        if is_edit(m):
            return traj[:i]
    return traj


def gold_files(row) -> set[str]:
    return set(re.findall(r"^\+\+\+ b/(.+)$", row.get("model_patch") or "", re.M))


def main() -> int:
    rows = []
    for f in glob.glob(sys.argv[1] + "/c3_*.json"):
        rows += [r["row"] for r in json.load(open(f)).get("rows", [])]

    report: dict = {}
    for scope, cut in [("prefix_before_first_edit", prefix_before_first_edit),
                       ("full_trajectory", lambda t: t)]:
        tally: collections.Counter = collections.Counter()
        prefix_lens = []
        scored = 0
        for row in rows:
            gold = gold_files(row)
            if not gold:
                continue
            traj = cut(row["trajectory"])
            prefix_lens.append(len(traj))
            seq = [f for m in traj if m.get("role") == "assistant" for f in files_in(m)]
            if not seq:
                tally["no_named_file"] += 1
                scored += 1
                continue
            scored += 1
            counts = collections.Counter(seq)
            top = max(counts.values())
            preds = {"recency": {seq[-1]},
                     "frequency": {f for f, c in counts.items() if c == top},
                     "union": set(seq)}
            for name, pred in preds.items():
                if hits(pred, gold):
                    tally[f"{name}_any_hit"] += 1
                # gold fully covered by the prediction
                if all(any(same_file(p, g) for p in pred) for g in gold):
                    tally[f"{name}_covers_gold"] += 1
        report[scope] = {"scored": scored,
                         "median_msgs": sorted(prefix_lens)[len(prefix_lens) // 2],
                         "tally": dict(tally)}
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
