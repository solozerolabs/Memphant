"""Contract-drift guard for the MemPhant Python SDK.

Pins every request the SDK builds to `openapi/memphant.v1.json`: the payload
key-set must be a subset of the schema's properties (schemas are
`additionalProperties: false`, so an extra key is a hard 422) and must contain
every `required` field. This is the test that makes the silent-fallback failure
mode impossible: a client that omits `subject_id` or smuggles `tenant_id` fails
here, loudly, instead of 422-ing at runtime and being swallowed by a caller.

The SDK is pure request-building; these tests never open a socket. A tiny
capture transport records the (method, path, body) the SDK would send.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from memphant import BoundContext, MemPhant

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "openapi" / "memphant.v1.json"


def _spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


def _match_spec_path(spec: dict, path: str) -> str:
    """Match a concrete request path to its templated spec key
    (`/v1/context-bindings/syndai:user:alice` -> `/v1/context-bindings/{client_ref}`)."""
    if path in spec["paths"]:
        return path
    req = path.strip("/").split("/")
    for key in spec["paths"]:
        tmpl = key.strip("/").split("/")
        if len(tmpl) != len(req):
            continue
        if all(t.startswith("{") or t == r for t, r in zip(tmpl, req)):
            return key
    raise KeyError(f"no spec path matches {path}")


def _body_schema_for(spec: dict, method: str, path: str) -> dict:
    """Resolve the request-body schema object for one endpoint."""
    op = spec["paths"][_match_spec_path(spec, path)][method.lower()]
    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    name = ref.split("/")[-1]
    return name, spec["components"]["schemas"][name]


def _resolve(spec: dict, schema: dict) -> dict:
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return spec["components"]["schemas"][name]
    return schema


def _assert_conforms(spec: dict, method: str, path: str, body: dict) -> None:
    """The body's key-set must obey the endpoint's request schema, recursively
    for the nested object properties the SDK builds (selector, correction,
    payload, resource/unit, entity refs)."""
    name, schema = _body_schema_for(spec, method, path)
    _assert_object_conforms(spec, name, schema, body)


def _assert_object_conforms(spec: dict, name: str, schema: dict, body: dict) -> None:
    schema = _resolve(spec, schema)

    # Tagged union (e.g. RetainPayload episode|resource|unit): conform to ANY variant.
    if "oneOf" in schema:
        errors = []
        for i, variant in enumerate(schema["oneOf"]):
            try:
                _assert_object_conforms(spec, f"{name}#{i}", variant, body)
                return
            except AssertionError as exc:
                errors.append(str(exc))
        raise AssertionError(f"{name}: matched no oneOf variant:\n" + "\n".join(errors))

    props = schema.get("properties", {})
    allowed = set(props)
    sent = set(body)

    extra = sent - allowed
    assert not extra, f"{name}: sent keys not in contract (would 422): {sorted(extra)}"

    required = set(schema.get("required", []))
    missing = required - sent
    assert not missing, f"{name}: missing contract-required keys: {sorted(missing)}"

    # Recurse into nested object properties the SDK constructs itself.
    for key, value in body.items():
        if isinstance(value, dict):
            _assert_object_conforms(spec, f"{name}.{key}", props[key], value)


class _Capture:
    """Records the last request the SDK built instead of sending it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method: str, path: str, body: dict | None) -> dict:
        self.calls.append((method, path, body or {}))
        # Minimal plausible responses so mapping code does not choke.
        if path.startswith("/v1/context-bindings/"):
            return {
                "subject_id": str(uuid4()),
                "actor_id": str(uuid4()),
                "scope_id": str(uuid4()),
                "agent_node_id": str(uuid4()),
                "agent_level": 1,
                "policy_revision": 1,
                "subject_generation": 0,
            }
        return {"trace_id": str(uuid4()), "items": []}

    @property
    def last(self) -> tuple[str, str, dict]:
        return self.calls[-1]


@pytest.fixture
def spec() -> dict:
    return _spec()


@pytest.fixture
def capture() -> _Capture:
    return _Capture()


@pytest.fixture
def client(capture: _Capture) -> MemPhant:
    c = MemPhant(base_url="http://memphant.test", api_key="mk_test")
    # Swap the wire for the capture transport (no socket).
    object.__setattr__(c, "_transport", capture)
    return c


@pytest.fixture
def ctx() -> BoundContext:
    return BoundContext(
        subject_id=str(uuid4()),
        scope_id=str(uuid4()),
        actor_id=str(uuid4()),
        agent_node_id=str(uuid4()),
        subject_generation=0,
    )


def test_bind_context_payload_conforms(spec, client, capture):
    client.bind_context(
        client_ref="syndai:user:alice",
        subject_ref="user:alice",
        subject_kind="user",
        actor_ref="agent:helper",
        actor_kind="agent",
        scope_ref="agent:helper",
        scope_kind="agent",
        agent_node_ref="agent:helper",
    )
    method, path, body = capture.last
    assert (method, path) == ("PUT", "/v1/context-bindings/syndai:user:alice")
    _assert_conforms(spec, method, path, body)


def test_bind_context_returns_bound_context(spec, client, capture):
    ctx = client.bind_context(
        client_ref="syndai:user:alice",
        subject_ref="user:alice",
        subject_kind="user",
        actor_ref="agent:helper",
        actor_kind="agent",
        scope_ref="agent:helper",
        scope_kind="agent",
        agent_node_ref="agent:helper",
    )
    assert isinstance(ctx, BoundContext)
    assert ctx.subject_generation == 0
    assert ctx.subject_id and ctx.scope_id and ctx.actor_id and ctx.agent_node_id


def test_recall_payload_conforms(spec, client, capture, ctx):
    client.recall(ctx=ctx, query="where did I leave the keys", limit=10, budget_tokens=2048, mode="fast")
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/recall")
    _assert_conforms(spec, method, path, body)


def test_retain_episode_payload_conforms(spec, client, capture, ctx):
    client.retain_episode(
        ctx=ctx,
        source_ref="coding-attempt:123",
        observed_at="2026-07-21T00:00:00Z",
        source_kind="agent",
        body="assistant: hello",
    )
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/episodes")
    _assert_conforms(spec, method, path, body)


def test_retain_resource_payload_conforms(spec, client, capture, ctx):
    client.retain_resource(
        ctx=ctx,
        source_ref="file:src/main.rs",
        observed_at="2026-07-21T00:00:00Z",
        uri="file:///src/main.rs",
        mime_type="text/x-rust",
        content_hash="sha256:abc",
        revision="deadbeef",
        body="fn main() {}",
    )
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/episodes")
    _assert_conforms(spec, method, path, body)


def test_retain_unit_payload_conforms(spec, client, capture, ctx):
    client.retain_unit(
        ctx=ctx,
        source_ref="fact:release-region",
        observed_at="2026-07-21T00:00:00Z",
        kind="semantic",
        fact_key="profile:release-region",
        predicate="release_region",
        body="Release region is Taipei.",
        confidence=1.0,
    )
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/episodes")
    _assert_conforms(spec, method, path, body)


def test_correct_payload_conforms(spec, client, capture, ctx):
    client.correct(
        ctx=ctx,
        memory_unit_id=str(uuid4()),
        value="new value",
        reason="file_memory_update",
        source_ref="agent:helper",
        observed_at="2026-07-21T00:00:00Z",
    )
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/correct")
    _assert_conforms(spec, method, path, body)


def test_forget_payload_conforms(spec, client, capture, ctx):
    client.forget(
        ctx=ctx,
        reason="user_erasure",
        memory_unit_id=str(uuid4()),
    )
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/forget")
    _assert_conforms(spec, method, path, body)


def test_reflect_payload_conforms(spec, client, capture, ctx):
    client.reflect(ctx=ctx)
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/reflect")
    _assert_conforms(spec, method, path, body)


def test_mark_payload_conforms(spec, client, capture, ctx):
    client.mark(
        ctx=ctx,
        trace_id=str(uuid4()),
        caller_id="agent:helper",
        used_ids=[str(uuid4())],
        outcome="used",
    )
    method, path, body = capture.last
    assert (method, path) == ("POST", "/v1/mark")
    _assert_conforms(spec, method, path, body)


def test_wire_headers_satisfy_mutation_idempotency_contract(client, ctx):
    body = {**ctx._identity(), "query": "q"}
    first = client._headers("POST", "/v1/reflect", body)
    second = client._headers("POST", "/v1/reflect", dict(reversed(body.items())))
    assert first["idempotency-key"] == second["idempotency-key"]
    assert first["idempotency-key"].startswith("memphant-sdk-")
    assert "idempotency-key" not in client._headers("POST", "/v1/recall", body)
    assert "idempotency-key" not in client._headers("GET", "/v1/health", None)


def test_trace_carries_complete_bound_context_query(client, capture, ctx):
    trace_id = str(uuid4())
    client.trace(ctx=ctx, trace_id=trace_id)
    method, path, body = capture.last
    assert method == "GET"
    assert body == {}
    assert path.startswith(f"/v1/traces/{trace_id}?")
    for name, value in ctx._identity().items():
        assert f"{name}={value}" in path


def test_no_verb_smuggles_tenant_id(spec, client, capture, ctx):
    """The banned failure mode: tenant is bound by the API key, never sent in a
    body. Every verb must be clean."""
    client.recall(ctx=ctx, query="q")
    client.reflect(ctx=ctx)
    client.mark(ctx=ctx, trace_id=str(uuid4()), caller_id="a", used_ids=[], outcome="used")
    for method, path, body in capture.calls:
        assert "tenant_id" not in body, f"{path} smuggles tenant_id"
        assert "allowed_scope_ids" not in body, f"{path} smuggles allowed_scope_ids"
