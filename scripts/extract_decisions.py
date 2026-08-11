#!/usr/bin/env python3
"""Decision-capture extractor — turn a coding-run transcript into injectable
DECISION facts (the write side the adherence bench validated as the right fuel).

For each run we extract durable engineering DECISIONS: an approach chosen over a
named rejected alternative, for a reason that is NOT recoverable by reading the
repo (rationale/tradeoff, not code). These are exactly the non-repo-derivable,
counter-default facts the adherence bench showed carry the lift; infra facts
(sandbox_backend/resource_tier) are explicitly out.

This is the STANDALONE validation of capture quality BEFORE wiring a production
finalize job: run it on real transcripts, read the decisions, judge whether the
fuel is good. Input: benchmarks/data/coding_events_corpus.jsonl (mined real
attempts). Output: extracted decisions per attempt for inspection.

Run: doppler run --project syndai --config dev -- python3 scripts/extract_decisions.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

MODEL = os.environ.get("EXTRACT_MODEL", "anthropic/claude-sonnet-5")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CORPUS = os.environ.get("EXTRACT_CORPUS", "benchmarks/data/coding_events_corpus.jsonl")
MAX_CHARS = 24000  # transcript budget per attempt

SYS = (
    "You extract DURABLE ENGINEERING DECISIONS from a coding-agent run transcript, "
    "for a per-repo memory that will be shown to future agents. A decision qualifies "
    "ONLY if: (1) an approach was chosen over a NAMED rejected alternative, (2) the "
    "reason is a rationale/tradeoff NOT recoverable by reading the repo's code (so "
    "not 'the test command is X' — that's greppable), and (3) it would plausibly "
    "recur on a later task in this repo. Exclude one-off task steps, infra/runtime "
    "facts (sandbox, resource tier), and anything a fresh agent could grep. "
    "Return STRICT JSON: {\"decisions\":[{\"subject\":\"<=6 words\",\"chosen\":\"...\","
    "\"rejected\":\"...\",\"rationale\":\"...\"}]}. Empty list if none qualify. No prose."
)


def call(messages: list[dict], max_tokens: int = 1500) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set (doppler run --project syndai --config dev)")
    body = json.dumps({"model": MODEL, "temperature": 0, "max_tokens": max_tokens,
                       "messages": messages}).encode()
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"].get("content") or ""


def transcript(events: list[dict]) -> str:
    parts = []
    for e in events:
        role, text = e.get("role", "?"), (e.get("text") or "")
        if text.strip():
            parts.append(f"[{role}] {text}")
    return "\n".join(parts)[:MAX_CHARS]


def extract(events: list[dict]) -> list[dict]:
    raw = call([{"role": "system", "content": SYS},
                {"role": "user", "content": transcript(events)}]).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(raw).get("decisions", [])
    except Exception:
        return [{"_parse_error": raw[:200]}]


def main() -> int:
    recs = [json.loads(l) for l in Path(CORPUS).read_text().splitlines() if l.strip()]
    out = []
    total = 0
    for r in recs:
        decisions = extract(r.get("events", []))
        n = len([d for d in decisions if "_parse_error" not in d])
        total += n
        out.append({"attempt_id": r.get("attempt_id"), "n": n, "decisions": decisions})
        print(f"\n=== attempt {str(r.get('attempt_id'))[:8]} — {n} decision(s) ===")
        for d in decisions:
            if "_parse_error" in d:
                print(f"  [parse error] {d['_parse_error']}")
            else:
                print(f"  • {d.get('subject')}: chose {d.get('chosen')!r} over "
                      f"{d.get('rejected')!r}\n      because {d.get('rationale')}")
    print(f"\n== TOTAL decisions across {len(recs)} attempts: {total} "
          f"(avg {total/len(recs):.1f}/run) ==")
    Path("benchmarks/data/extracted_decisions.json").write_text(json.dumps(out, indent=2))
    print("wrote benchmarks/data/extracted_decisions.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
