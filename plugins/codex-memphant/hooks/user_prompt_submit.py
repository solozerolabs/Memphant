#!/usr/bin/env python3
"""Codex UserPromptSubmit hook: inject the MemPhant advisory block (<=5 labelled cards).

Thin adapter over the shared recall core (`plugins/_shared/memphant_recall.py`).
This file owns ONLY Codex's input-parsing and its hook envelope; all recall
logic (the MCP handshake, response parsing, error typing) lives in the shared
core so every harness shares one implementation.

Codex passes one JSON object on stdin (`prompt`, `cwd`, `session_id`,
`turn_id`, ...). We build a bounded query from `prompt`+`cwd`, call the recall
core, and return a Codex hook envelope whose `additionalContext` is either zero
bytes or the honest recalled cards (each labelled `[unconfirmed]` when it is a
not-yet-witnessed capture); the shared core also writes the exposure receipt.

Behavior is preserved exactly (raw card in `additionalContext`); the memori
advisory-block wrapper is a shared-core capability the newer adapters adopt.

Design for testing: `run()` takes an injectable `caller` so the unit test drives
it with a fake in-process transport and never opens a socket.
"""

from __future__ import annotations

import json
import os
import sys

# The shared core lives at plugins/_shared/memphant_recall.py, two levels up
# from this hook (plugins/codex-memphant/hooks/). Resolve from __file__ so the
# import works regardless of the process cwd.
sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")),
)

from memphant_recall import (  # noqa: E402  (path is set up above)
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    RecallError,
    build_block,
    http_caller,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES",
    "RecallError",
    "build_block",
    "http_caller",
    "run",
    "main",
]


def _envelope(additional_context: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }


def run(stdin, stdout, stderr, caller) -> int:
    try:
        raw = stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        print("memphant-hook: skip code=bad_stdin", file=stderr)
        json.dump(_envelope(""), stdout)
        return 0

    prompt = event.get("prompt")
    cwd = event.get("cwd")
    if not isinstance(prompt, str) or not isinstance(cwd, str):
        print("memphant-hook: skip code=missing_fields", file=stderr)
        json.dump(_envelope(""), stdout)
        return 0

    try:
        block = build_block(prompt, cwd, caller)
    except RecallError as exc:
        # No memory bytes on failure; the code is not "empty search".
        print(f"memphant-hook: no-inject code={exc.code}", file=stderr)
        json.dump(_envelope(""), stdout)
        return 0

    json.dump(_envelope(block), stdout)
    return 0


def main() -> int:
    base_url = os.environ.get("MEMPHANT_MCP_URL")
    bearer = os.environ.get("MEMPHANT_API_KEY")
    if not base_url or not bearer:
        # Not configured: inject nothing, do not fail the prompt.
        print("memphant-hook: skip code=unconfigured", file=sys.stderr)
        json.dump(_envelope(""), sys.stdout)
        return 0
    caller = http_caller(base_url, bearer, DEFAULT_TIMEOUT_SECONDS)
    return run(sys.stdin, sys.stdout, sys.stderr, caller)


if __name__ == "__main__":
    raise SystemExit(main())
