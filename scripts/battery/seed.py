#!/usr/bin/env python3
"""Seed one non-repo convention as a coding-lane-servable PROCEDURAL compact unit
via the MCP `remember` tool. Warm-start / smoke only — the real battery's memory
comes from capture, not this.

The coding-lane injection hook serves Active compact PROCEDURAL (and Validated)
units; a Belief is excluded unless the recall sets include_beliefs=true (the hook
does not). So a hand-seed for the coding lane must be procedural, and captured
BELIEFS will NOT be injected on this lane until promoted/typed accordingly — the
integration gap the battery exists to catch.

Env: MEMPHANT_MCP_URL, MEMPHANT_API_KEY + the bound identity is implicit in the
key (remember writes to the key's bound scope). Usage: seed.py "<convention>".
"""
import os, sys, uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "_shared"))
from memphant_recall import http_caller, DEFAULT_TIMEOUT_SECONDS, PROTOCOL_VERSION  # noqa: E402


def main() -> int:
    fact = sys.argv[1] if len(sys.argv) > 1 else sys.exit("seed.py needs a convention string")
    trigger = os.environ.get("SEED_TRIGGER", "working in this repo")
    caller = http_caller(os.environ["MEMPHANT_MCP_URL"], os.environ["MEMPHANT_API_KEY"], DEFAULT_TIMEOUT_SECONDS)
    _, sid = caller({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                "clientInfo": {"name": "battery-seed", "version": "1"}}}, None)
    caller({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, sid)
    req = {"kind": "procedural", "body": fact, "trigger": trigger,
           "verification": "run the repo's antipattern/type checks; they must pass",
           "source": {"kind": "user", "ref": "battery://seed", "observed_at": "2026-08-15T18:00:00+00:00"}}
    body, _ = caller({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "remember",
                                 "arguments": {"idempotency_key": "seed-" + uuid.uuid4().hex, "request": req}}}, sid)
    text = body.decode("utf-8", "replace")
    print("seeded" if "unit_ids" in text else "seed FAILED: " + text[:200])
    return 0 if "unit_ids" in text else 1


if __name__ == "__main__":
    raise SystemExit(main())
