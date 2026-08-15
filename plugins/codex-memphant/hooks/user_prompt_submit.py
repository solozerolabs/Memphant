#!/usr/bin/env python3
"""Codex UserPromptSubmit hook: inject at most one MemPhant compact card.

Stdlib only. Reads one JSON object from stdin (Codex passes `prompt`, `cwd`,
`session_id`, `turn_id`, ...), builds a bounded query from `prompt`+`cwd`, and
calls the ALREADY-RUNNING MemPhant Streamable-HTTP MCP endpoint's `recall` tool
(the portable coding lane). It returns a Codex hook envelope whose
`additionalContext` is either zero bytes or the single honest recalled card.

Design for testing: `recall_card()` takes an injectable `caller` so the unit
test drives it with a fake in-process transport and never opens a socket. The
same importable helper is reused by the e2e probe (Task 8).

Guarantees:
- Never prints bearer keys, prompts, recalled bodies, or raw responses to
  stderr; diagnostics are terse codes only.
- On auth/unavailable/timeout/malformed/oversized, inject NO memory bytes and
  emit one concise stderr line — never mislabel a backend failure as an empty
  memory search.
- A pending raw source (`unavailable` / `consolidation_pending`) injects zero
  bytes and preserves the code in diagnostics.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

# Hard ceilings: a compact card is <=512 tokens; cap the raw response so a
# misbehaving endpoint cannot flood the agent's context.
MAX_RESPONSE_BYTES = 64 * 1024
MAX_QUERY_CHARS = 4096
DEFAULT_TIMEOUT_SECONDS = 2.0

PROTOCOL_VERSION = "2025-11-25"


class RecallError(Exception):
    """A typed hook failure carrying a short, secret-free code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _bounded_query(prompt: str, cwd: str) -> str:
    query = f"{prompt}\n{cwd}".strip()
    return query[:MAX_QUERY_CHARS]


def _parse_body(body: bytes) -> dict:
    """Parse a Streamable-HTTP response body (JSON or a single SSE `data:`)."""
    if len(body) > MAX_RESPONSE_BYTES:
        raise RecallError("oversized")
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise RecallError("malformed")
    if text.startswith("{"):
        candidate = text
    else:
        # Server-Sent Events: take the last `data:` payload.
        data_lines = [
            line[len("data:"):].strip()
            for line in text.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            raise RecallError("malformed")
        candidate = data_lines[-1]
    try:
        return json.loads(candidate)
    except (ValueError, TypeError) as exc:
        raise RecallError("malformed") from exc


def http_caller(base_url: str, bearer: str, timeout: float):
    """A `caller(payload_dict, session_id) -> (body_bytes, session_id)` bound to
    a real Streamable-HTTP endpoint. Kept separate so tests inject a fake."""

    def call(payload: dict, session_id: str | None):
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {bearer}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = urllib.request.Request(base_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                new_session = response.headers.get("Mcp-Session-Id") or session_id
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RecallError("auth") from exc
            raise RecallError("unavailable") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RecallError("timeout") from exc
        if status in (401, 403):
            raise RecallError("auth")
        if status >= 400:
            raise RecallError("unavailable")
        return body, new_session

    return call


def recall_card(prompt: str, cwd: str, caller) -> str:
    """Run initialize -> initialized -> tools/call recall through `caller`.

    Returns the single already-packed card string on a hit, or "" on an honest
    empty. Raises RecallError (secret-free code) on any failure or a typed
    unavailable/consolidation_pending result.
    """
    query = _bounded_query(prompt, cwd)

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "codex-memphant-hook", "version": "1"},
        },
    }
    init_body, session_id = caller(init_payload, None)
    _parse_body(init_body)  # validate handshake shape / surface transport errors

    caller(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        session_id,
    )

    call_body, _ = caller(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "recall", "arguments": {"query": query}},
        },
        session_id,
    )
    parsed = _parse_body(call_body)
    if "error" in parsed:
        raise RecallError("unavailable")
    structured = (parsed.get("result") or {}).get("structuredContent") or {}
    state = structured.get("state")
    if state == "hit":
        items = structured.get("response", {}).get("items") or structured.get("items") or []
        if not items:
            raise RecallError("malformed")
        # The card is the already-packed compact body; the server enforces the
        # 512-token ceiling, so we render it verbatim.
        return str(items[0].get("body", "")).strip()
    if state == "empty":
        return ""
    if state in ("unavailable", "consolidation_pending"):
        raise RecallError("consolidation_pending")
    if state == "error":
        raise RecallError("unavailable")
    raise RecallError("malformed")


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
        card = recall_card(prompt, cwd, caller)
    except RecallError as exc:
        # No memory bytes on failure; the code is not "empty search".
        print(f"memphant-hook: no-inject code={exc.code}", file=stderr)
        json.dump(_envelope(""), stdout)
        return 0

    json.dump(_envelope(card), stdout)
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
