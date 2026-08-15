from __future__ import annotations

import json
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bindings" / "python"))

from memphant import BoundContext, MemPhant, MemPhantValidationError  # noqa: E402


def _bind(client: MemPhant) -> BoundContext:
    return client.bind_context(
        client_ref="syndai:user:demo",
        subject_ref="user:demo",
        subject_kind="user",
        actor_ref="agent:helper",
        actor_kind="agent",
        scope_ref="agent:helper",
        scope_kind="agent",
        agent_node_ref="agent:helper",
    )


def test_python_sdk_round_trips_all_public_verbs() -> None:
    server = FakeMemphantServer()
    client = MemPhant(base_url=server.base_url, api_key="test-key")
    try:
        ctx = _bind(client)
        assert isinstance(ctx, BoundContext)
        assert ctx.subject_generation == 0

        retained = client.retain_episode(
            ctx=ctx,
            source_ref="release-note:1",
            observed_at="2025-06-01T00:00:00Z",
            source_kind="system",
            body="Release region is Taipei.",
            idempotency_key="wsd-retain-episode",
        )
        assert retained["episode_id"] == "ep_test"

        client.retain_resource(
            ctx=ctx,
            source_ref="repo://demo/src/main.rs",
            observed_at="2025-06-01T00:00:00Z",
            uri="repo://demo/src/main.rs",
            mime_type="text/x-rust",
            content_hash="sha256:abc",
            kind="code",
            revision="abc123",
            body="fn main() {}",
            idempotency_key="wsd-retain-resource",
        )
        client.retain_unit(
            ctx=ctx,
            source_ref="fact:release-region",
            observed_at="2025-06-01T00:00:00Z",
            kind="semantic",
            fact_key="profile:release-region",
            predicate="release_region",
            body="Release region is Taipei.",
            confidence=1.0,
            idempotency_key="wsd-retain-unit",
        )

        reflected = client.reflect(ctx=ctx, idempotency_key="wsd-reflect")
        assert reflected["episodes_consumed"] == 1

        recalled = client.recall(
            ctx=ctx,
            query="Where is the release region?",
            aggregation_window={
                "from": "2025-06-01T00:00:00Z",
                "to": "2025-06-08T00:00:00Z",
            },
        )
        assert recalled["items"][0]["body"] == "Release region is Taipei."

        trace = client.trace(
            ctx=ctx, trace_id="00000000-0000-0000-0000-000000099001"
        )
        assert trace["id"] == "00000000-0000-0000-0000-000000099001"

        corrected = client.correct(
            ctx=ctx,
            memory_unit_id="00000000-0000-0000-0000-000000088001",
            value="Release region is Singapore.",
            reason="stale_fact",
            source_ref="agent:helper",
            observed_at="2025-06-02T00:00:00Z",
            idempotency_key="wsd-correct",
        )
        assert corrected["correction_kind"] == "current"

        forgotten = client.forget(
            ctx=ctx,
            memory_unit_id="00000000-0000-0000-0000-000000088001",
            reason="user_request",
            idempotency_key="wsd-forget",
        )
        assert forgotten["verification"] == "no_recall_path_returns_forgotten"

        marked = client.mark(
            ctx=ctx,
            trace_id="00000000-0000-0000-0000-000000099001",
            caller_id="pytest",
            used_ids=["00000000-0000-0000-0000-000000088001"],
            outcome="success",
            idempotency_key="wsd-mark",
        )
        assert marked["accepted"] is True

        paths = [request["path"] for request in server.requests]
        trace_path = paths[6]
        assert trace_path.startswith(
            "/v1/traces/00000000-0000-0000-0000-000000099001?"
        )
        assert paths[:6] + paths[7:] == [
            "/v1/context-bindings/syndai:user:demo",
            "/v1/episodes",
            "/v1/episodes",
            "/v1/episodes",
            "/v1/reflect",
            "/v1/recall",
            "/v1/correct",
            "/v1/forget",
            "/v1/mark",
        ]
        assert all(
            request["headers"].get("authorization") == "Bearer test-key"
            for request in server.requests
        )
        for request in server.requests:
            if request["method"] == "POST" and request["path"] in {
                "/v1/episodes",
                "/v1/reflect",
                "/v1/correct",
                "/v1/forget",
                "/v1/mark",
            }:
                assert request["headers"].get("idempotency-key", "").startswith("wsd-")

        # No verb body smuggles tenant_id / allowed_scope_ids (the banned shape).
        for request in server.requests:
            body = request["body"] or {}
            assert "tenant_id" not in body, f"{request['path']} smuggled tenant_id"
            assert "allowed_scope_ids" not in body

        # Every mutation/recall body carries the resolved identity, not tenant_id.
        recall_body = server.requests[5]["body"]
        assert recall_body["subject_id"] == ctx.subject_id
        assert recall_body["agent_node_id"] == ctx.agent_node_id
        assert recall_body["subject_generation"] == ctx.subject_generation

        resource_request = server.requests[2]["body"]
        assert resource_request["payload"]["resource"]["uri"] == "repo://demo/src/main.rs"
        assert resource_request["payload"]["resource"]["revision"] == "abc123"

        unit_request = server.requests[3]["body"]
        assert unit_request["payload"]["unit"]["kind"] == "semantic"
        assert server.requests[5]["body"]["aggregation_window"] == {
            "from": "2025-06-01T00:00:00Z",
            "to": "2025-06-08T00:00:00Z",
        }
    finally:
        server.close()


def test_python_sdk_maps_error_envelopes_to_typed_exceptions() -> None:
    server = FakeMemphantServer(error_on_recall=True)
    client = MemPhant(base_url=server.base_url, api_key="test-key")
    try:
        ctx = _bind(client)
        try:
            client.recall(ctx=ctx, query="bad")
        except MemPhantValidationError as exc:
            assert exc.code == "invalid_request"
            assert exc.fields == ["query"]
        else:
            raise AssertionError("expected MemPhantValidationError")
    finally:
        server.close()


def test_python_package_artifacts_exist() -> None:
    assert (ROOT / "bindings/python/pyproject.toml").is_file()
    assert (ROOT / "bindings/python/examples/roundtrip.py").is_file()
    openapi = json.loads((ROOT / "openapi/memphant.v1.json").read_text())
    mcp_tools = json.loads((ROOT / "mcp/memphant.tools.v1.json").read_text())
    assert openapi["openapi"] == "3.1.0"
    assert "/v1/recall" in openapi["paths"]
    assert {tool["name"] for tool in mcp_tools} == {
        "recall",
        "remember",
        "correct_memory",
        "invalidate_memory",
        "report_memory_use",
    }


def test_python_package_is_pure_http_sdk_until_native_api_exists() -> None:
    pyproject = tomllib.loads((ROOT / "bindings/python/pyproject.toml").read_text())

    assert pyproject["build-system"]["build-backend"] != "maturin"
    assert "maturin" not in pyproject
    assert "memphant._native" not in json.dumps(pyproject)


class FakeMemphantServer:
    def __init__(self, error_on_recall: bool = False) -> None:
        self.requests: list[dict[str, object]] = []
        self.error_on_recall = error_on_recall

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parent._record(self)
                if self.path.startswith("/v1/traces/"):
                    parent._write(
                        self,
                        200,
                        {"id": self.path.rsplit("/", 1)[-1].split("?", 1)[0]},
                    )
                else:
                    parent._write(self, 404, {"error": {"code": "not_found", "message": "missing", "request_id": "req_test", "details": {}}})

            def do_PUT(self) -> None:  # noqa: N802
                parent._record(self)
                if self.path.startswith("/v1/context-bindings/"):
                    parent._write(
                        self,
                        200,
                        {
                            "subject_id": "00000000-0000-0000-0000-0000000186a0",
                            "actor_id": "00000000-0000-0000-0000-0000000186a2",
                            "scope_id": "00000000-0000-0000-0000-0000000186a1",
                            "agent_node_id": "00000000-0000-0000-0000-0000000186a3",
                            "agent_level": 1,
                            "policy_revision": 1,
                            "subject_generation": 0,
                        },
                    )
                else:
                    parent._write(self, 404, {"error": {"code": "not_found", "message": "missing", "request_id": "req_test", "details": {}}})

            def do_POST(self) -> None:  # noqa: N802
                body = parent._record(self)
                if parent.error_on_recall and self.path == "/v1/recall":
                    parent._write(
                        self,
                        422,
                        {
                            "error": {
                                "code": "invalid_request",
                                "message": "query is invalid",
                                "request_id": "req_test",
                                "details": {"fields": ["query"]},
                            }
                        },
                    )
                    return
                responses = {
                    "/v1/episodes": {"episode_id": "ep_test"},
                    "/v1/reflect": {"episodes_consumed": 1},
                    "/v1/recall": {
                        "trace_id": "00000000-0000-0000-0000-000000099001",
                        "items": [{"unit_id": "00000000-0000-0000-0000-000000088001", "body": "Release region is Taipei."}],
                    },
                    "/v1/correct": {"correction_kind": "current"},
                    "/v1/forget": {"verification": "no_recall_path_returns_forgotten"},
                    "/v1/mark": {"accepted": True},
                }
                parent._write(self, 200, responses.get(self.path, {"echo": body}))

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()

    def _record(self, handler: BaseHTTPRequestHandler) -> object:
        length = int(handler.headers.get("content-length", "0"))
        raw = handler.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.requests.append(
            {
                "method": handler.command,
                "path": handler.path,
                "headers": {key.lower(): value for key, value in handler.headers.items()},
                "body": body,
            }
        )
        return body

    def _write(self, handler: BaseHTTPRequestHandler, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
