#!/usr/bin/env python3
"""Shared MemPhant recall core for the coding-agent injection adapters.

ONE implementation, four thin harness adapters. Python hooks import this module;
TypeScript adapters (opencode, pi) shell out to its CLI. Stdlib only.

As a library:
    recall_card(prompt, cwd, caller) -> str
        Run the MCP handshake (initialize -> initialized -> tools/call recall)
        through an injectable `caller` and return the single already-packed
        compact card body on a hit, "" on an honest empty, or raise RecallError
        (a secret-free code) on any failure / pending state. Behavior is
        unchanged from the original Codex hook so its test still passes.

    advisory_block(card) -> str
        Wrap a non-empty card in the memori-style delimited advisory block. An
        empty/blank card yields "" (zero bytes — never an empty block).

    build_block(prompt, cwd, caller) -> str
        recall_card + advisory_block: the full injection payload for an adapter.

As a CLI:
    python3 memphant_recall.py --prompt "..." --cwd "..."
        Reads env MEMPHANT_MCP_URL + MEMPHANT_API_KEY, prints the injection
        block to stdout (empty string on honest-empty OR any failure), writes
        one secret-free diagnostic line to stderr, and ALWAYS exits 0 so it can
        never break a host turn.

Guarantees:
- Never prints bearer keys, prompts, recalled bodies, or raw responses to
  stderr; diagnostics are terse codes only.
- On auth/unavailable/timeout/malformed/oversized, inject NO memory bytes.
- A pending raw source (`unavailable` / `consolidation_pending`) injects zero
  bytes and preserves the code in diagnostics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Hard ceilings: a compact card is <=512 tokens; cap the raw response so a
# misbehaving endpoint cannot flood the agent's context.
MAX_RESPONSE_BYTES = 64 * 1024
MAX_QUERY_CHARS = 4096
DEFAULT_TIMEOUT_SECONDS = 2.0

PROTOCOL_VERSION = "2025-11-25"

# memori principle: a recalled card is injected as a delimited advisory block so
# the agent treats it as optional context, not an instruction.
ADVISORY_HEADER = (
    "Advisory context from prior sessions — use only if relevant to the "
    "current task."
)
ADVISORY_OPEN = "<memphant_memory>"
ADVISORY_CLOSE = "</memphant_memory>"


class RecallError(Exception):
    """A typed failure carrying a short, secret-free code."""

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
            "clientInfo": {"name": "memphant-recall", "version": "1"},
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


def advisory_block(card: str) -> str:
    """Wrap a non-empty card in the memori delimited advisory block.

    An empty/blank card yields "" (zero bytes) — never an empty block.
    """
    body = (card or "").strip()
    if not body:
        return ""
    return f"{ADVISORY_OPEN}\n{ADVISORY_HEADER}\n{body}\n{ADVISORY_CLOSE}"


def build_block(prompt: str, cwd: str, caller) -> str:
    """recall_card + advisory_block — the full injection payload for an adapter.

    Returns "" on an honest empty. Propagates RecallError so a caller can log a
    secret-free code (the CLI swallows it into zero bytes)."""
    return advisory_block(recall_card(prompt, cwd, caller))


def main(argv=None) -> int:
    """CLI entrypoint: print the injection block, exit 0 always."""
    parser = argparse.ArgumentParser(
        description="Print a MemPhant advisory memory block for a coding agent.",
        add_help=True,
    )
    parser.add_argument("--prompt", default="", help="The user prompt (may be empty).")
    parser.add_argument("--cwd", default="", help="The working directory / project path.")
    args = parser.parse_args(argv)

    base_url = os.environ.get("MEMPHANT_MCP_URL")
    bearer = os.environ.get("MEMPHANT_API_KEY")
    if not base_url or not bearer:
        # Not configured: inject nothing, do not fail the turn.
        print("memphant-recall: skip code=unconfigured", file=sys.stderr)
        return 0

    caller = http_caller(base_url, bearer, DEFAULT_TIMEOUT_SECONDS)
    try:
        block = build_block(args.prompt, args.cwd, caller)
    except RecallError as exc:
        print(f"memphant-recall: no-inject code={exc.code}", file=sys.stderr)
        return 0
    except Exception:  # never break a host turn on an unexpected error
        print("memphant-recall: no-inject code=internal", file=sys.stderr)
        return 0

    # Empty string on honest-empty (no trailing newline so callers see 0 bytes).
    sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
