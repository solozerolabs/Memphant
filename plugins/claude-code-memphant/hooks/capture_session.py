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

After the summary capture, two best-effort steps: the survival witness
(`session_outcome` -> ONE `/v1/mark` over the injection hook's exposure receipt
`<cwd>/.memphant/.served.json`, then truncate it) and the AGENTS.md projection.

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
    http_marker,
    http_poster,
    load_transcript_messages,
    make_live_summarizer,
    mark_url_from_capture_url,
    post_survival_mark,
    resolve_capture_config,
    session_outcome,
)
from memphant_projection import project  # noqa: E402


def _session_start(transcript_path) -> str:
    """First `timestamp` in the JSONL transcript (RFC3339) — bounds the exposure
    receipt to this session; "" when unknown."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                ts = record.get("timestamp") if isinstance(record, dict) else None
                if isinstance(ts, str) and ts:
                    return ts
    except OSError:
        pass
    return ""


def run(stdin, stdout, stderr, build, marker=None, projector=None) -> int:
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
        print(f"memphant-capture: code={result.code}", file=stderr)
    except Exception:
        print("memphant-capture: no-capture code=internal", file=stderr)

    # Best-effort tail: survival witness + projection. Never raises. Claude Code
    # transcripts do not expose tool exit codes, so the outcome rests on the
    # last turn's text (correction / unresolved-failure markers).
    cwd = payload["cwd"]
    if marker is not None and cwd:
        outcome = session_outcome(messages)
        code = post_survival_mark(cwd, outcome, marker, since=_session_start(transcript_path) or None)
        print(f"memphant-capture: survival={code}", file=stderr)
    if projector is not None and cwd:
        try:
            print(f"memphant-capture: projection={projector(cwd)}", file=stderr)
        except Exception:
            print("memphant-capture: projection=projection_error", file=stderr)
    return 0


def main() -> int:
    config = resolve_capture_config(require_summarizer=True)
    if config is None:
        print("memphant-capture: skip code=unconfigured", file=sys.stderr)
        return 0
    summarizer = make_live_summarizer(config)
    poster = http_poster(config["url"], config["bearer"], config["identity"])
    marker = http_marker(mark_url_from_capture_url(config["url"]), config["bearer"], config["identity"])
    build = lambda payload: build_capture(payload, summarizer=summarizer, poster=poster)  # noqa: E731
    return run(sys.stdin, sys.stdout, sys.stderr, build, marker=marker, projector=project)


if __name__ == "__main__":
    raise SystemExit(main())
