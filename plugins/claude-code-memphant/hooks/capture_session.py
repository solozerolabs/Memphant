#!/usr/bin/env python3
"""Claude Code session-capture hook (Stop / SessionEnd): summarize + capture.

Thin adapter over the shared capture core (`plugins/_shared/memphant_capture.py`).
This file owns ONLY Claude Code's Stop/SessionEnd parsing and its fail-safe
envelope; all capture logic lives in the shared core.

Registered for Stop and SessionEnd (see hooks/hooks.json). Claude Code passes a
`transcript_path` (a JSONL conversation log) plus `cwd` and `hook_event_name`;
the shared core reads the last turn, applies the exclusion filters, summarizes
via the cheap-model shell-out, and POSTs it tagged `source=summary`. Subagent
Stop events carry `agent_id`, which the core skips.

Async + fail-safe: any error is swallowed to a secret-free status code and the
hook ALWAYS exits 0 (an empty allow) so it can never break a Claude Code turn.

Design for testing: `run()` takes an injectable `build` so the unit test drives
it with a stubbed capture core and never summarizes or opens a socket.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")),
)

from memphant_capture import (  # noqa: E402  (path is set up above)
    build_capture,
    http_poster,
    load_transcript_messages,
    make_live_summarizer,
    resolve_capture_config,
)


def run(stdin, stdout, stderr, build) -> int:
    try:
        raw = stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        print("memphant-capture: skip code=bad_stdin", file=stderr)
        return 0

    transcript_path = event.get("transcript_path")
    messages = event.get("messages")
    if not isinstance(messages, list):
        messages = (
            load_transcript_messages(transcript_path)
            if isinstance(transcript_path, str) and transcript_path
            else []
        )
    if not messages:
        print("memphant-capture: skip code=no_transcript", file=stderr)
        return 0

    payload = {
        "source": "summary",
        "cwd": event.get("cwd", ""),
        "messages": messages,
        # Claude Code sets `agent_id` on subagent Stop events; the core skips it.
        "agent_id": event.get("agent_id"),
    }
    try:
        result = build(payload)
    except Exception:
        print("memphant-capture: no-capture code=internal", file=stderr)
        return 0
    print(f"memphant-capture: code={result.code}", file=stderr)
    return 0


def main() -> int:
    config = resolve_capture_config(require_summarizer=True)
    if config is None:
        print("memphant-capture: skip code=unconfigured", file=sys.stderr)
        return 0
    summarizer = make_live_summarizer(config)
    poster = http_poster(config["url"], config["bearer"], config["identity"])
    build = lambda payload: build_capture(payload, summarizer=summarizer, poster=poster)  # noqa: E731
    return run(sys.stdin, sys.stdout, sys.stderr, build)


if __name__ == "__main__":
    raise SystemExit(main())
