#!/usr/bin/env python3
"""Loopback stub of the OpenRouter chat-completions endpoint, for $0 dry runs.

The instrument register's single highest-leverage governance recommendation is:
*no paid authorization for any lane whose adapter has not completed a $0 stub
round trip against the current contract since the last contract change.* Three
external instruments failed at first contact on our own side, two of them after
money was authorized; one stub round trip would have caught all three.

This is that stub for the reader lane. It answers `/api/v1/chat/completions`
with a response shaped exactly like OpenRouter's, honouring the `response_format`
json_schema the caller sends so that `run_reader.py`'s strict parsers are
genuinely exercised rather than bypassed — a stub that returns something the
parser accepts trivially proves nothing.

It also serves `/api/v1/generation` so the settled-cost fallback path is live.

Point the reader at it with MEMPHANT_OPENROUTER_STUB_URL; run_reader refuses any
non-loopback value and sends no real credential.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"calls": [], "generation_lookups": 0,
         "reader_answer": "stub answer", "reader_abstain": False}
LOCK = threading.Lock()


def payload_for(schema_name: str, schema: dict, evidence_ranks: list[int]) -> dict:
    """Build a schema-valid object for the kind the caller asked for."""
    if schema_name.startswith("reader"):
        return {
            "notes": "stub reasoning",
            "answer": None if STATE["reader_abstain"] else STATE["reader_answer"],
            "abstain": bool(STATE["reader_abstain"]),
        }
    if schema_name.startswith("rag_judge"):
        # fully_supported=True with an empty rank list is rejected by the strict
        # parser, so the stub must be honest about an empty pack. That asymmetry
        # is exactly what the no-memory arm will hit in production.
        return {
            "answer_correct": True,
            "fully_supported": bool(evidence_ranks),
            "supporting_evidence_ranks": evidence_ranks[:1],
        }
    if schema_name.startswith("pair_judge"):
        return {"verdict": "a"}
    if schema_name.startswith("judge"):
        return {"verdict": "yes"}
    props = list((schema.get("properties") or {}).keys())
    return {key: "stub" for key in props}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # silence per-request stderr noise
        return

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/v1/generation"):
            # Deliberately positive: the chat response reports cost 0, which the
            # settlement path treats as "unreported" and reconciles here. Serving
            # a positive figure exercises the fallback end to end instead of
            # tripping the fail-closed branch, so the dry run proves both legs.
            with LOCK:
                STATE["generation_lookups"] += 1
            self._send(200, {"data": {"total_cost": 0.000001, "provider_name": "stub",
                                      "model": "stub"}})
            return
        if self.path.startswith("/calls"):
            # Mechanism-liveness readout: what the reader actually sent. Served
            # over HTTP rather than written at shutdown so the evidence survives
            # however the stub is killed.
            with LOCK:
                self._send(200, {"calls": STATE["calls"],
                                 "generation_lookups": STATE["generation_lookups"]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        fmt = body.get("response_format") or {}
        schema_block = fmt.get("json_schema") or {}
        name = schema_block.get("name", "")
        schema = schema_block.get("schema") or {}

        # Recover the evidence ranks the judge prompt rendered, so the rag judge
        # can cite a rank that actually exists (the strict parser checks this).
        user = ""
        for message in body.get("messages", []):
            if message.get("role") == "user":
                user = message.get("content", "")
        ranks = []
        marker = "--- evidence item "
        index = user.find(marker)
        while index != -1 and len(ranks) < 32:
            tail = user[index + len(marker):index + len(marker) + 8]
            digits = "".join(ch for ch in tail.split(" ")[0] if ch.isdigit())
            if digits:
                ranks.append(int(digits))
            index = user.find(marker, index + 1)

        with LOCK:
            STATE["calls"].append({
                "schema_name": name,
                "model": body.get("model"),
                "has_response_format": bool(fmt),
                "strict": schema_block.get("strict"),
                "provider": body.get("provider"),
                "max_tokens": body.get("max_tokens"),
                "temperature": body.get("temperature"),
                "reasoning": body.get("reasoning"),
                "system_prompt_prefix": next(
                    (m.get("content", "")[:120] for m in body.get("messages", [])
                     if m.get("role") == "system"), ""
                ),
                "user_prompt_chars": len(user),
                "evidence_ranks_seen": sorted(set(ranks)),
                "authorization_header": self.headers.get("Authorization"),
            })

        content = json.dumps(payload_for(name, schema, sorted(set(ranks))))
        self._send(200, {
            "id": f"gen-stub-{len(STATE['calls'])}",
            "model": body.get("model"),
            "provider": "stub",
            "choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": max(1, len(user) // 4),
                      "completion_tokens": len(content) // 4 or 1,
                      "total_tokens": max(1, len(user) // 4),
                      "cost": 0.0},
        })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=39901)
    parser.add_argument("--calls-out", help="write the observed call log here on SIGTERM/EOF")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"stub listening on http://127.0.0.1:{args.port}/api/v1/chat/completions",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if args.calls_out:
            with open(args.calls_out, "w", encoding="utf-8") as handle:
                json.dump({"calls": STATE["calls"],
                           "generation_lookups": STATE["generation_lookups"]},
                          handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
