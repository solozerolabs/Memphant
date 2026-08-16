#!/usr/bin/env python3
"""Shared MemPhant recall core for the coding-agent injection adapters.

ONE implementation, four thin harness adapters. Python hooks import this module;
TypeScript adapters (opencode, pi) shell out to its CLI. Stdlib only.

As a library:
    recall_items(prompt, cwd, caller) -> list[dict]
        Run the MCP handshake (initialize -> initialized -> tools/call recall)
        through an injectable `caller` and return the served items (each a
        `RecallContextItem`: unit_id, body, kind, inclusion_reason, ...) on a
        hit, [] on an honest empty, or raise RecallError (a secret-free code)
        on any failure / pending state.

    render_cards(items) -> str
        Render up to MAX_CARD_ITEMS items as one card, one line per item, each
        prefixed `[unconfirmed]` when its `inclusion_reason` carries the
        `captured_unconfirmed` token (a Candidate capture that has not yet
        earned a survival witness) and unprefixed otherwise, bounded to
        MAX_CARD_CHARS. Precision over recall: the server already filters; we
        never pad.

    recall_card(prompt, cwd, caller) -> str
        recall_items + render_cards, plus the EXPOSURE RECEIPT (below) on a
        hit. "" on an honest empty.

Exposure receipt (the contract a Stop hook reads to post a survival
`/v1/mark` Success for every served unit — the WeakOutcome witness that
promotes Candidate captures):
    <cwd>/.memphant/.served.json   — JSON Lines, one record appended per hit:
    {"ts": <RFC 3339 UTC>, "query_sha256": <hex of the bounded query>,
     "trace_id": <retrieval trace id the mark must cite>,
     "unit_ids": [<served unit id in served order>, ...],
     "labels": {<unit id>: "confirmed" | "unconfirmed", ...}}
    The directory is created on demand and `.memphant/.served.json` is added
    to `.git/info/exclude` when cwd is inside a git repo (worktree-aware via
    `git rev-parse --git-path`). Any failure to write is silently ignored —
    the receipt never breaks a host turn.

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
import datetime
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

# Hard ceilings: the server packs at most a few compact items into a 512-token
# budget; cap the raw response so a misbehaving endpoint cannot flood the
# agent's context, and bound the rendered card independently of the server.
MAX_RESPONSE_BYTES = 64 * 1024
MAX_QUERY_CHARS = 4096
DEFAULT_TIMEOUT_SECONDS = 2.0
# Peer injection systems default to 5-10 items; the MCP lane serves <=3 under
# its 512-token budget. Never render more than this even if a server does.
MAX_CARD_ITEMS = 5
MAX_CARD_CHARS = 2000
UNCONFIRMED_TOKEN = "captured_unconfirmed"
UNCONFIRMED_LABEL = "[unconfirmed]"
RECEIPT_DIR = ".memphant"
RECEIPT_FILE = ".served.json"

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


def recall_items(prompt: str, cwd: str, caller) -> list:
    """Run initialize -> initialized -> tools/call recall through `caller`.

    Returns the served items (list of dicts) on a hit, or [] on an honest
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
        items = [item for item in items if isinstance(item, dict)]
        if not items:
            raise RecallError("malformed")
        # The retrieval trace id rides on each item so the exposure receipt can
        # record it: a survival `/v1/mark` must cite the trace that served the ids
        # (the server rejects a mark whose trace does not exist).
        trace_id = structured.get("trace_id") or structured.get("response", {}).get("trace_id")
        for item in items:
            item.setdefault("_trace_id", trace_id)
        return items[:MAX_CARD_ITEMS]
    if state == "empty":
        return []
    if state in ("unavailable", "consolidation_pending"):
        raise RecallError("consolidation_pending")
    if state == "error":
        raise RecallError("unavailable")
    raise RecallError("malformed")


def is_unconfirmed(item: dict) -> bool:
    """A served item is unconfirmed when the server says so: its
    `inclusion_reason` carries the `captured_unconfirmed` token, or it exposes a
    Candidate `state`. Anything else (Active procedures, promoted captures with
    `captured_confirmed`) is confirmed and renders unprefixed."""
    reason = str(item.get("inclusion_reason") or "")
    if UNCONFIRMED_TOKEN in reason:
        return True
    return str(item.get("state") or "").lower() == "candidate"


def render_cards(items: list) -> str:
    """Render up to MAX_CARD_ITEMS items as one card body, one line per item.

    A single item renders as its bare body (byte-identical to the historical
    single-card behaviour); N>1 render as `- ` bullets in served order. Each
    line is prefixed `[unconfirmed] ` when `is_unconfirmed`. The whole card is
    bounded to MAX_CARD_CHARS (the last line is truncated, later ones dropped).
    Empty / bodyless items yield "".
    """
    lines = []
    for item in items[:MAX_CARD_ITEMS]:
        body = " ".join(str(item.get("body", "")).split()) if len(items) > 1 else str(item.get("body", "")).strip()
        if not body:
            continue
        prefix = f"{UNCONFIRMED_LABEL} " if is_unconfirmed(item) else ""
        lines.append(f"{prefix}{body}")
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0][:MAX_CARD_CHARS]
    out = []
    used = 0
    for line in lines:
        line = f"- {line}"
        room = MAX_CARD_CHARS - used - (1 if out else 0)
        if room <= 0:
            break
        if len(line) > room:
            line = line[:room]
        out.append(line)
        used += len(line) + (1 if len(out) > 1 else 0)
    return "\n".join(out)


def _git_exclude_receipt(cwd: str) -> None:
    """Best-effort: keep the receipt out of `git status` (worktree-aware)."""
    try:
        exclude = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if exclude.returncode != 0:
            return
        path = exclude.stdout.strip()
        if not path:
            return
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        entry = f"{RECEIPT_DIR}/{RECEIPT_FILE}"
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                existing = handle.read()
        if entry in existing.splitlines():
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(entry + "\n")
    except Exception:
        return


def write_receipt(cwd: str, query: str, items: list) -> None:
    """Append one exposure-receipt record (see module docstring) for a hit.
    Silently ignores every failure — the receipt never breaks a host turn."""
    if not cwd or not items:
        return
    try:
        unit_ids = [str(item["unit_id"]) for item in items if item.get("unit_id")]
        if not unit_ids:
            return
        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "trace_id": next((str(i["_trace_id"]) for i in items if i.get("_trace_id")), None),
            "unit_ids": unit_ids,
            "labels": {
                str(item["unit_id"]): ("unconfirmed" if is_unconfirmed(item) else "confirmed")
                for item in items
                if item.get("unit_id")
            },
        }
        directory = os.path.join(cwd, RECEIPT_DIR)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, RECEIPT_FILE), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        _git_exclude_receipt(cwd)
    except Exception:
        return


def recall_card(prompt: str, cwd: str, caller) -> str:
    """recall_items + render_cards, writing the exposure receipt on a hit.

    Returns the rendered card on a hit, or "" on an honest empty. Raises
    RecallError (secret-free code) on any failure or pending state.
    """
    items = recall_items(prompt, cwd, caller)
    if not items:
        return ""
    write_receipt(cwd, _bounded_query(prompt, cwd), items)
    return render_cards(items)


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
