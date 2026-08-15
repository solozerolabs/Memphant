#!/usr/bin/env python3
"""Claude Code file-mirror hook (PreToolUse: Write|Edit|MultiEdit).

Thin adapter over the shared capture core (`plugins/_shared/memphant_capture.py`).
When the agent writes a MEMORY file (MEMORY.md, AGENTS.md, or a configured set),
this hook COPIES the written content into the store tagged `source=mirror` — the
high-precision augment to the session summary. It is ALLOW-AND-COPY: it NEVER
blocks the write (best UX; mem0 blocks, we deliberately do not). The host write
proceeds; the store gets a copy.

Detection: `tool_name in {Write, Edit, MultiEdit}` AND the target `file_path`'s
basename is in the mirror set (default `MEMORY.md`, `AGENTS.md`; override via
`MEMPHANT_CAPTURE_MIRROR_FILES`, a comma-separated basename list). The captured
content is the NEW text being written (Write: `content`; Edit: `new_string`;
MultiEdit: the concatenated `new_string`s) — the memory the agent is recording.

Async + fail-safe: any error is swallowed to a secret-free status code and the
hook ALWAYS exits 0 with NO decision — an implicit allow — so it can never block
a Claude Code file write.

Design for testing: `run()` takes an injectable `build` so the unit test drives
it with a stubbed capture core and never opens a socket.
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
    resolve_capture_config,
)

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}
_DEFAULT_MIRROR_FILES = ("MEMORY.md", "AGENTS.md")


def mirror_files() -> set:
    """The set of memory-file basenames to mirror (configurable)."""
    override = os.environ.get("MEMPHANT_CAPTURE_MIRROR_FILES", "")
    names = [part.strip() for part in override.split(",") if part.strip()]
    return set(names) if names else set(_DEFAULT_MIRROR_FILES)


def _written_content(tool_name: str, tool_input: dict) -> str:
    """The NEW content the tool is about to write, across Write/Edit/MultiEdit."""
    if tool_name == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else ""
    if tool_name == "Edit":
        new = tool_input.get("new_string")
        return new if isinstance(new, str) else ""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            return "\n".join(
                e.get("new_string", "")
                for e in edits
                if isinstance(e, dict) and isinstance(e.get("new_string"), str)
            )
    return ""


def run(stdin, stdout, stderr, build) -> int:
    try:
        raw = stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        print("memphant-capture: skip code=bad_stdin", file=stderr)
        return 0  # never block the write

    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    if tool_name not in _WRITE_TOOLS or not isinstance(tool_input, dict):
        return 0  # not a file write — allow, nothing to mirror

    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or os.path.basename(file_path) not in mirror_files():
        return 0  # not a memory file — allow, do not mirror

    content = _written_content(tool_name, tool_input)
    if not content.strip():
        return 0  # nothing to copy — allow

    payload = {
        "source": "mirror",
        "cwd": event.get("cwd", ""),
        "content": content,
        "agent_id": event.get("agent_id"),
    }
    try:
        result = build(payload)
    except Exception:
        print("memphant-capture: no-capture code=internal", file=stderr)
        return 0
    print(f"memphant-capture: code={result.code}", file=stderr)
    return 0  # ALLOW-AND-COPY: never block the host write


def main() -> int:
    # Mirror needs no summarizer (no LLM), so `require_summarizer=False`.
    config = resolve_capture_config(require_summarizer=False)
    if config is None:
        print("memphant-capture: skip code=unconfigured", file=sys.stderr)
        return 0
    poster = http_poster(config["url"], config["bearer"], config["identity"])
    build = lambda payload: build_capture(  # noqa: E731
        payload, summarizer=(lambda _text: ""), poster=poster
    )
    return run(sys.stdin, sys.stdout, sys.stderr, build)


if __name__ == "__main__":
    raise SystemExit(main())
