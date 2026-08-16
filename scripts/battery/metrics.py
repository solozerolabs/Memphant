#!/usr/bin/env python3
"""Efficiency metrics for one codex run, read straight off its rollout .jsonl.

The strategic finding is that memphant's coding value is EFFICIENCY, not
correctness: a bare agent usually reaches the same correct fix, so what memory
buys is reaching it with fewer tokens / turns / tool-calls (less re-derivation).
Codex already logs all of that in the rollout, so the metric costs nothing extra.

Usage:
  metrics.py <rollout.jsonl>              # explicit file
  metrics.py --codex-home <dir>           # newest rollout under <dir>/sessions
Emits one JSON line: {total_tokens, output_tokens, reasoning_tokens,
tool_calls, turns, final_message}. Missing fields degrade to 0/"" — this is a
reporting aid, never a gate.
"""
import glob
import json
import os
import sys


def newest_rollout(codex_home: str) -> str:
    files = glob.glob(os.path.join(codex_home, "sessions", "**", "rollout-*.jsonl"), recursive=True)
    return max(files, key=os.path.getmtime) if files else ""


def metrics(path: str) -> dict:
    total = output = reasoning = tool_calls = turns = 0
    final = ""
    try:
        lines = open(path, encoding="utf-8", errors="replace").readlines()
    except OSError:
        return {"total_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                "tool_calls": 0, "turns": 0, "final_message": ""}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        p = rec.get("payload") or {}
        t = p.get("type")
        if t == "token_count":
            # total_token_usage is cumulative; the LAST one is the run total.
            u = (p.get("info") or {}).get("total_token_usage") or {}
            total = u.get("total_tokens", total)
            output = u.get("output_tokens", output)
            reasoning = u.get("reasoning_output_tokens", reasoning)
        elif t in ("custom_tool_call", "function_call", "local_shell_call"):
            tool_calls += 1
        elif t == "task_started":
            turns += 1
        elif t == "task_complete":
            final = p.get("last_agent_message") or final
    return {"total_tokens": total, "output_tokens": output, "reasoning_tokens": reasoning,
            "tool_calls": tool_calls, "turns": max(turns, 1), "final_message": final[:400]}


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return 2
    if args[0] == "--codex-home":
        path = newest_rollout(args[1])
    else:
        path = args[0]
    if not path:
        print("{}")
        return 0
    print(json.dumps(metrics(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
