#!/usr/bin/env python3
"""Capture summarizer: coding-turn text on stdin -> terse non-repo-gotcha bullets on
stdout (empty on nothing-durable or any failure — capture is fail-safe).

Model pick is a measured bake-off result (5 varied inputs + a NONE-robustness probe,
judged by hand): `gemini-3.1-flash-lite` won — crispest extraction AND robust on the
no-capture path (NONE 3/3 on vague routine turns where 2.5-flash-lite hallucinated
1/3, the false-positive/poison risk). haiku-4.5 was DROPPED — it fabricated false
conventions (memory-poison risk) at the highest price ($1/$5). gpt-5-nano DROPPED —
hallucinated + 1000+ token reasoning bloat. Deterministic client-side fallback (only
on transport error, never off a valid answer incl. a correct NONE): 3.1-flash-lite ->
2.5-flash-lite (same provider) -> gpt-5-mini (cross-provider, survives a Google outage).
Needs OPENROUTER_API_KEY. Per-capture cost is fractions of a cent (~30-50 output tok).
"""
import json, os, re, sys, urllib.request

MODELS = [
    "google/gemini-3.1-flash-lite",   # primary — crispest + NONE-robust (measured)
    "google/gemini-2.5-flash-lite",   # backup 1 — same provider, cheaper
    "openai/gpt-5-mini",              # backup 2 — cross-provider resilience
]
# OUTPUT CONTRACT (parsed by plugins/_shared/memphant_capture.py `parse_topic`):
#   line 1:  TOPIC: <2-5 word lowercase noun phrase>   -- the stable subject key
#   lines 2+: 1-4 terse bullets                         -- the stored body
#   or exactly NONE (no TOPIC line) when nothing durable was learned.
PROMPT = (
    "From this coding session turn, extract ONLY non-obvious fixes, gotchas, "
    "error->resolution facts, or conventions the agent DISCOVERED that are NOT "
    "recoverable from the repo itself (environmental, external, or experiential). "
    "Output format: the FIRST line must be exactly `TOPIC: <2-5 word lowercase "
    "noun phrase>` naming the external system, component, or gotcha (generic, "
    "no repo-specific file names, so the same knowledge always gets the same "
    "topic regardless of wording); then 1-4 terse token-efficient bullets. Never "
    "reproduce secret values. Do NOT restate the task or capture routine actions. "
    "If nothing durable and non-repo was learned, output exactly: NONE (no TOPIC "
    "line)."
)


def _is_none(out: str) -> bool:
    # Robust no-capture detection: models wrap it ("* None", "NONE.", "- none",
    # or a stray "TOPIC: none" line) so compare on alphanumerics only.
    body = re.sub(r"(?im)^\s*topic\s*:.*$", "", out)
    return re.sub(r"[^a-z0-9]", "", body.lower()) in ("", "none")


def _call(model: str, turn: str, key: str) -> str:
    """One model call. Returns the content, or raises on any transport/parse error
    so the caller can fall back. A correct NONE is a SUCCESS, not a failure — we do
    not fall back off it (that was the bug: OpenRouter's `models` array re-routed a
    valid NONE to a worse model that hallucinated)."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": turn}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    r = json.load(urllib.request.urlopen(req, timeout=30))
    return (r["choices"][0]["message"]["content"] or "").strip()


def main() -> int:
    key = os.environ.get("OPENROUTER_API_KEY")
    turn = sys.stdin.read()
    if not key or not turn.strip():
        return 0  # unconfigured / empty -> capture no-ops
    for model in MODELS:  # deterministic client-side fallback: only on ERROR, never off a valid answer
        try:
            out = _call(model, turn, key)
        except Exception:
            continue  # this model failed -> try the next
        if not _is_none(out):
            sys.stdout.write(out)
        return 0  # got a valid answer (bullets or NONE) -> done, no fallback
    return 0  # every model failed -> fail-safe empty (capture no-ops)


if __name__ == "__main__":
    raise SystemExit(main())
