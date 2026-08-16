#!/usr/bin/env python3
"""Codex session-capture hook (Stop): summarize the last turn and capture it.

Thin adapter over the shared capture core (`plugins/_shared/memphant_capture.py`).
This file owns ONLY Codex's Stop-event parsing and its fail-safe envelope; all
capture logic (filters, secret redaction, summarize, POST) lives in the core so
every harness shares one implementation.

Codex is SUMMARIZE-ONLY: its file-write edits go through `apply_patch`, which the
hook layer cannot observe, so there is no Codex file-mirror (a documented harness
limitation, not a scope cut). This hook fires on Stop / session end, reads the
transcript, and hands the last turn to the shared core tagged `source=summary`.

EXPERIMENTAL-API NOTE: Codex's Stop-event payload shape is not covered by a
stable public contract at the time of writing. This adapter reads the transcript
defensively from whichever of `messages` / `transcript` / `transcript_path` the
event provides; if none is present it is a silent no-op. If Codex changes the
Stop payload, update `_messages_from_event` here — the single seam.

Async + fail-safe: any error is swallowed, nothing is printed but a secret-free
status code, and it ALWAYS exits 0 so it can never break a Codex session.

Design for testing: `run()` takes an injectable `build` so the unit test drives
it with a stubbed capture core and never summarizes or opens a socket.
"""

from __future__ import annotations

import glob
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


def _newest_rollout(event: dict) -> str:
    """The Stop event's payload shape is not a stable contract, so as a durable
    fallback we read the session's own rollout `.jsonl` straight off disk.
    Codex writes it to `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<id>.jsonl`.
    Prefer the file matching the event's session id; otherwise the newest one
    (a Stop hook runs right after its own session, which is the newest rollout).
    Returns "" when CODEX_HOME is unset or has no rollouts."""
    home = os.environ.get("CODEX_HOME")
    if not home:
        return ""
    files = glob.glob(os.path.join(home, "sessions", "**", "rollout-*.jsonl"), recursive=True)
    if not files:
        return ""
    sid = event.get("session_id") or event.get("thread_id") or event.get("id")
    if isinstance(sid, str) and sid:
        matched = [f for f in files if sid in os.path.basename(f)]
        if matched:
            files = matched
    return max(files, key=os.path.getmtime)


def _messages_from_event(event: dict) -> list:
    """Normalize whatever transcript Codex handed us into a messages list."""
    messages = event.get("messages") or event.get("transcript")
    if isinstance(messages, list):
        return messages
    path = event.get("transcript_path") or event.get("rollout_path") or _newest_rollout(event)
    if isinstance(path, str) and path:
        return load_transcript_messages(path)
    return []


def run(stdin, stdout, stderr, build) -> int:
    try:
        raw = stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        print("memphant-capture: skip code=bad_stdin", file=stderr)
        return 0

    messages = _messages_from_event(event)
    if not messages:
        print("memphant-capture: skip code=no_transcript", file=stderr)
        return 0

    payload = {
        "source": "summary",
        "cwd": event.get("cwd") or event.get("workspace") or "",
        "messages": messages,
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
