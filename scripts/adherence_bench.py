#!/usr/bin/env python3
"""Coding-memory ADHERENCE bench — the behavior-level A/B the ledger never ran.

Every prior coding-lane test scored RETRIEVAL (hit@k). STATUS conditions
reopening the code lane on "a causal behavior-level code bank". This is that
bank: for each recorded DECISION (whose rejected alternative is NOT
repo-derivable, so grep cannot supply it), measure whether a fresh agent

  - baseline arm: fails to adhere when NOT told the decision   -> headroom
  - memory arm:   adheres when the decision is injected turn-1  -> lift

Injection uses the real `repo_profile` block shape. Adherence is semantic, so
grading is by TWO cross-family LLM judges (a rescue counts only when both agree),
which also gives an inter-judge agreement number. Controls prove the bench is
not rubber-stamping: best_practice_control cases (decision == default) should
show ~no lift; repo_derivable_control flags where grep competes.

Run:  doppler run --project syndai --config dev -- python3 scripts/adherence_bench.py
Cases: benchmarks/data/adherence_cases.jsonl

ponytail: stdlib only, temp 0 (reproducible), one sample/arm. Scale samples only
if a per-case rate (not the cross-case rate) is ever needed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SUT = os.environ.get("ADHERENCE_MODEL", "anthropic/claude-sonnet-5")
JUDGES = os.environ.get(
    "ADHERENCE_JUDGES", "openai/gpt-5.6-terra,google/gemini-3.1-pro-preview"
).split(",")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PROFILE_HEADER = "## Repo Runtime Profile (learned from prior runs)"  # real repo_profile shape
CASES_PATH = os.environ.get("ADHERENCE_CASES", "benchmarks/data/adherence_cases.jsonl")

SYSTEM = "You are a senior engineer working in this codebase. Be concise and concrete."
JUDGE_SYS = (
    "You grade whether an engineer's answer honored a recorded team decision. "
    "The engineer may NOT have been told the decision — judge only the answer's "
    "content against the decision. Reply with exactly one token first — ADHERE, "
    "VIOLATE, or PARTIAL — then ' | ' and a <=8-word reason. "
    "PARTIAL = follows the decision but also keeps the rejected alternative (a hedge)."
)


def call(messages: list[dict], model: str, max_tokens: int) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set (use doppler run --project syndai --config dev)")
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": max_tokens,
                       "messages": messages}).encode()
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    return data["choices"][0]["message"].get("content") or ""


def answer_arm(case: dict, with_memory: bool) -> str:
    user = case["task"]
    if with_memory:
        user = f"{PROFILE_HEADER}\n- {case['decision']}\n\n{user}"
    return call([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                model=SUT, max_tokens=700)


def judge_one(decision: str, task: str, answer: str, model: str) -> tuple[str, str]:
    prompt = (f"Recorded decision: {decision}\n\nQuestion asked: {task}\n\n"
              f"Engineer's answer:\n{answer}\n\nDid the answer ADHERE, VIOLATE, or PARTIAL?")
    raw = call([{"role": "system", "content": JUDGE_SYS}, {"role": "user", "content": prompt}],
               model=model, max_tokens=2000).strip()
    tok = raw.split()[0].strip(":|").upper() if raw else ""
    return {"ADHERE": "adhered", "VIOLATE": "violated", "PARTIAL": "partial"}.get(tok, "ambiguous"), raw


def judged(case: dict, answer: str) -> dict:
    out = {}
    for m in JUDGES:
        label, raw = judge_one(case["decision"], case["task"], answer, m)
        out[m] = {"label": label, "raw": raw}
    labels = [v["label"] for v in out.values()]
    out["consensus"] = labels[0] if len(set(labels)) == 1 else "split"
    return out


def main() -> int:
    cases = [json.loads(l) for l in Path(CASES_PATH).read_text().splitlines() if l.strip()]
    rows = []
    for c in cases:
        base_a = answer_arm(c, with_memory=False)
        mem_a = answer_arm(c, with_memory=True)
        base_j = judged(c, base_a)
        mem_j = judged(c, mem_a)
        # conservative: both judges agree baseline did NOT fully adhere, and both agree memory adhered
        headroom = base_j["consensus"] in ("violated", "partial")
        rescued = headroom and mem_j["consensus"] == "adhered"
        rows.append({"id": c["id"], "class": c["class"],
                     "baseline": base_j["consensus"], "memory": mem_j["consensus"],
                     "headroom": headroom, "rescued": rescued,
                     "baseline_judges": base_j, "memory_judges": mem_j,
                     "baseline_answer": base_a, "memory_answer": mem_a})
        print(f"{c['id']:26s} {c['class']:22s} base={base_j['consensus']:9s} "
              f"mem={mem_j['consensus']:9s} {'RESCUED' if rescued else ('headroom' if headroom else '')}")

    def agree_rate(arm: str) -> float:
        ok = sum(1 for r in rows if r[f"{arm}_judges"]["consensus"] != "split")
        return round(ok / len(rows), 3)

    by_class = defaultdict(lambda: {"n": 0, "headroom": 0, "rescued": 0, "mem_adhere": 0})
    for r in rows:
        b = by_class[r["class"]]
        b["n"] += 1
        b["headroom"] += r["headroom"]
        b["rescued"] += r["rescued"]
        b["mem_adhere"] += r["memory"] == "adhered"

    n = len(rows)
    headroom = sum(r["headroom"] for r in rows)
    rescued = sum(r["rescued"] for r in rows)
    summary = {
        "sut": SUT, "judges": JUDGES, "n": n,
        "baseline_nonadherence_rate": round(headroom / n, 3),
        "memory_adherence_rate": round(sum(1 for r in rows if r["memory"] == "adhered") / n, 3),
        "rescued_count": rescued,
        "rescue_rate_of_headroom": round(rescued / headroom, 3) if headroom else None,
        "judge_agreement_baseline": agree_rate("baseline"),
        "judge_agreement_memory": agree_rate("memory"),
        "by_class": {k: dict(v) for k, v in by_class.items()},
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    print("\n== SUMMARY ==")
    print(json.dumps(summary, indent=2))
    print("\nValidity check — best_practice_control rescues should be ~0; "
          "counter_default should carry the lift.")
    out = {"summary": summary, "rows": rows}
    path = os.environ.get("ADHERENCE_OUT", "benchmarks/data/adherence_bench_result.json")
    Path(path).write_text(json.dumps(out, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
