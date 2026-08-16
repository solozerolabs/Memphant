#!/usr/bin/env python3
"""Claude Code memory-injection hook: inject the MemPhant advisory block (<=5 labelled cards).

Thin adapter over the shared recall core (`plugins/_shared/memphant_recall.py`).
This file owns ONLY Claude Code's input-parsing, its 10k injection cap, and its
hook envelope; all recall logic lives in the shared core.

Registered for two events (see hooks/hooks.json):
- UserPromptSubmit — stdin carries `prompt` + `cwd`; query = prompt + cwd.
- SessionStart      — no prompt; query = cwd (the project) alone.

Claude Code tells us which event fired via the stdin field `hook_event_name`;
the same script serves both. Output is a Claude Code hook envelope whose
`additionalContext` is either zero bytes or the memori advisory block, truncated
safely to the 10,000-char injection cap. Any recall failure injects zero bytes
and never breaks the turn.

Design for testing: `run()` takes an injectable `caller` so the unit test drives
it with a fake in-process transport and never opens a socket.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")),
)

from memphant_recall import (  # noqa: E402  (path is set up above)
    ADVISORY_CLOSE,
    ADVISORY_HEADER,
    ADVISORY_OPEN,
    DEFAULT_TIMEOUT_SECONDS,
    RecallError,
    http_caller,
    recall_card,
)

# Claude Code caps injected `additionalContext` at 10,000 characters.
INJECTION_CAP = 10000
_DEFAULT_EVENT = "UserPromptSubmit"
_VALID_EVENTS = ("UserPromptSubmit", "SessionStart")


def _capped_block(card: str) -> str:
    """Wrap the card in the memori block, truncating the card body so the whole
    block never exceeds INJECTION_CAP. Empty card -> zero bytes."""
    body = (card or "").strip()
    if not body:
        return ""
    # Fixed structural overhead: open tag + header + close tag + 3 newlines.
    overhead = len(ADVISORY_OPEN) + len(ADVISORY_HEADER) + len(ADVISORY_CLOSE) + 3
    budget = INJECTION_CAP - overhead
    if budget <= 0:
        return ""  # pathological cap; inject nothing rather than a broken block
    if len(body) > budget:
        body = body[:budget]
    return f"{ADVISORY_OPEN}\n{ADVISORY_HEADER}\n{body}\n{ADVISORY_CLOSE}"


def _envelope(event_name: str, additional_context: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": additional_context,
        }
    }


def run(stdin, stdout, stderr, caller) -> int:
    try:
        raw = stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        print("memphant-hook: skip code=bad_stdin", file=stderr)
        json.dump(_envelope(_DEFAULT_EVENT, ""), stdout)
        return 0

    event_name = event.get("hook_event_name")
    if event_name not in _VALID_EVENTS:
        event_name = _DEFAULT_EVENT

    # SessionStart carries no prompt — the query is the project cwd alone.
    prompt = event.get("prompt")
    if prompt is None:
        prompt = ""
    cwd = event.get("cwd")
    if not isinstance(prompt, str) or not isinstance(cwd, str):
        print("memphant-hook: skip code=missing_fields", file=stderr)
        json.dump(_envelope(event_name, ""), stdout)
        return 0

    try:
        card = recall_card(prompt, cwd, caller)
    except RecallError as exc:
        print(f"memphant-hook: no-inject code={exc.code}", file=stderr)
        json.dump(_envelope(event_name, ""), stdout)
        return 0

    json.dump(_envelope(event_name, _capped_block(card)), stdout)
    return 0


def main() -> int:
    base_url = os.environ.get("MEMPHANT_MCP_URL")
    bearer = os.environ.get("MEMPHANT_API_KEY")
    if not base_url or not bearer:
        print("memphant-hook: skip code=unconfigured", file=sys.stderr)
        json.dump(_envelope(_DEFAULT_EVENT, ""), sys.stdout)
        return 0
    caller = http_caller(base_url, bearer, DEFAULT_TIMEOUT_SECONDS)
    return run(sys.stdin, sys.stdout, sys.stderr, caller)


if __name__ == "__main__":
    raise SystemExit(main())
