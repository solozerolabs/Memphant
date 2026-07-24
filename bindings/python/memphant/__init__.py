"""MemPhant public REST SDK, pinned to the strict landed contract.

The v1 request schemas are `additionalProperties: false`: an extra key (e.g. a
legacy `tenant_id`) or a missing required field (`subject_id`, `agent_node_id`,
`subject_generation`) is a hard 422. Tenant is bound server-side by the API key
principal and is NEVER sent in a body.

Identity is a two-step handshake that mirrors the server's
`resolve_memory_context`: `bind_context()` (PUT /v1/context-bindings) resolves
your external refs into a `BoundContext` (the five ids + subject generation),
and every verb takes that context. There is no `tenant_id`-shaped legacy path to
silently fall back to — a contract violation raises `MemPhantValidationError`.
`tests/test_contract_drift.py` pins every payload to `openapi/memphant.v1.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

__version__ = "0.3.0"


class MemPhantError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.details = details or {}


class MemPhantAuthError(MemPhantError):
    pass


class MemPhantNotFound(MemPhantError):
    pass


class MemPhantConflict(MemPhantError):
    pass


class MemPhantValidationError(MemPhantError):
    @property
    def fields(self) -> list[str]:
        fields = self.details.get("fields", [])
        return fields if isinstance(fields, list) else []


class MemPhantRateLimited(MemPhantError):
    def __init__(self, *args: Any, retry_after: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class MemPhantUnavailable(MemPhantError):
    pass


ERROR_MAP = {
    "auth_required": MemPhantAuthError,
    "tenant_denied": MemPhantAuthError,
    "scope_denied": MemPhantAuthError,
    "policy_denied": MemPhantAuthError,
    "not_found": MemPhantNotFound,
    "conflict": MemPhantConflict,
    "idempotency_conflict": MemPhantConflict,
    "invalid_request": MemPhantValidationError,
    "context_binding_conflict": MemPhantConflict,
    "rate_limited": MemPhantRateLimited,
    "backend_unavailable": MemPhantUnavailable,
}


@dataclass(frozen=True)
class BoundContext:
    """The resolved identity the server needs on every verb. Returned by
    `bind_context`; opaque to callers beyond carrying it back in."""

    subject_id: str
    scope_id: str
    actor_id: str
    agent_node_id: str
    subject_generation: int

    def _identity(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "scope_id": self.scope_id,
            "actor_id": self.actor_id,
            "agent_node_id": self.agent_node_id,
            "subject_generation": self.subject_generation,
        }


@dataclass(frozen=True)
class MemPhant:
    base_url: str
    api_key: str | None = None
    timeout: float = 30.0
    # Injectable transport (method, path, body) -> response dict; defaults to
    # the real urllib wire. Tests swap this to capture payloads without a socket.
    _transport: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None = None

    def bind_context(
        self,
        *,
        client_ref: str,
        subject_ref: str,
        subject_kind: str,
        actor_ref: str,
        actor_kind: str,
        scope_ref: str,
        scope_kind: str,
        agent_node_ref: str,
        agent_node_parent_ref: str | None = None,
        scope_parent_ref: str | None = None,
    ) -> BoundContext:
        """Resolve external refs into a `BoundContext` (PUT /v1/context-bindings).

        Requires a tenant service key. The returned ids + `subject_generation`
        are what the server validates on every subsequent verb."""
        response = self._request(
            "PUT",
            f"/v1/context-bindings/{client_ref}",
            {
                "subject": {"external_ref": subject_ref, "kind": subject_kind},
                "actor": {"external_ref": actor_ref, "kind": actor_kind},
                "scope": {
                    "external_ref": scope_ref,
                    "kind": scope_kind,
                    "parent_external_ref": scope_parent_ref,
                },
                "agent_node": {
                    "external_ref": agent_node_ref,
                    "parent_external_ref": agent_node_parent_ref,
                },
            },
        )
        return BoundContext(
            subject_id=str(response["subject_id"]),
            scope_id=str(response["scope_id"]),
            actor_id=str(response["actor_id"]),
            agent_node_id=str(response["agent_node_id"]),
            subject_generation=int(response["subject_generation"]),
        )

    def retain_episode(
        self,
        *,
        ctx: BoundContext,
        source_ref: str,
        observed_at: str,
        source_kind: str,
        body: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Retain a raw episode (RetainEpisodePayload: source_kind + body)."""
        return self._post(
            "/v1/episodes",
            {
                **ctx._identity(),
                "source_ref": source_ref,
                "observed_at": observed_at,
                "payload": {"episode": {"source_kind": source_kind, "body": body}},
            },
            idempotency_key=idempotency_key,
        )

    def retain_resource(
        self,
        *,
        ctx: BoundContext,
        source_ref: str,
        observed_at: str,
        uri: str,
        mime_type: str,
        content_hash: str,
        kind: str | None = None,
        revision: str | None = None,
        body: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Retain a resource (RetainResourcePayload: uri + mime_type +
        content_hash; `revision` is the commit identity for code)."""
        return self._post(
            "/v1/episodes",
            {
                **ctx._identity(),
                "source_ref": source_ref,
                "observed_at": observed_at,
                "payload": {
                    "resource": {
                        "uri": uri,
                        "mime_type": mime_type,
                        "content_hash": content_hash,
                        "kind": kind,
                        "revision": revision,
                        "body": body,
                    }
                },
            },
            idempotency_key=idempotency_key,
        )

    def retain_unit(
        self,
        *,
        ctx: BoundContext,
        source_ref: str,
        observed_at: str,
        kind: str,
        fact_key: str,
        predicate: str,
        body: str,
        confidence: float,
        idempotency_key: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> dict[str, Any]:
        """Retain a pre-compiled unit (RetainUnitPayload). The admission trust
        policy still applies (untrusted keys mint candidate tier)."""
        return self._post(
            "/v1/episodes",
            {
                **ctx._identity(),
                "source_ref": source_ref,
                "observed_at": observed_at,
                "payload": {
                    "unit": {
                        "kind": kind,
                        "fact_key": fact_key,
                        "predicate": predicate,
                        "body": body,
                        "confidence": confidence,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                    }
                },
            },
            idempotency_key=idempotency_key,
        )

    def recall(
        self,
        *,
        ctx: BoundContext,
        query: str,
        limit: int | None = None,
        budget_tokens: int | None = None,
        mode: str | None = None,
        include_beliefs: bool | None = None,
        transaction_as_of: str | None = None,
        valid_at: str | None = None,
        aggregation_window: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/recall",
            {
                **ctx._identity(),
                "query": query,
                "limit": limit,
                "budget_tokens": budget_tokens,
                "mode": mode,
                "include_beliefs": include_beliefs,
                "transaction_as_of": transaction_as_of,
                "valid_at": valid_at,
                "aggregation_window": aggregation_window,
            },
        )

    def reflect(self, *, ctx: BoundContext, idempotency_key: str) -> dict[str, Any]:
        return self._post(
            "/v1/reflect",
            {**ctx._identity()},
            idempotency_key=idempotency_key,
        )

    def correct(
        self,
        *,
        ctx: BoundContext,
        memory_unit_id: str,
        value: str,
        reason: str,
        source_ref: str,
        observed_at: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/correct",
            {
                **ctx._identity(),
                "selector": {"memory_unit_id": memory_unit_id},
                "correction": {
                    "value": value,
                    "reason": reason,
                    "source_ref": source_ref,
                    "observed_at": observed_at,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                },
            },
            idempotency_key=idempotency_key,
        )

    def forget(
        self,
        *,
        ctx: BoundContext,
        reason: str,
        memory_unit_id: str | None = None,
        episode_id: str | None = None,
        resource_id: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Forget exactly one of memory_unit_id / episode_id / resource_id. The
        selector carries the scope from the bound context."""
        selectors = [
            ("memory_unit_id", memory_unit_id),
            ("episode_id", episode_id),
            ("resource_id", resource_id),
        ]
        chosen = [(name, value) for name, value in selectors if value is not None]
        if len(chosen) != 1:
            raise ValueError(
                "forget requires exactly one of memory_unit_id, episode_id, resource_id"
            )
        selector_name, selector_value = chosen[0]
        return self._post(
            "/v1/forget",
            {
                **ctx._identity(),
                "selector": {"scope_id": ctx.scope_id, selector_name: selector_value},
                "reason": reason,
            },
            idempotency_key=idempotency_key,
        )

    def mark(
        self,
        *,
        ctx: BoundContext,
        trace_id: str,
        caller_id: str,
        used_ids: list[str],
        outcome: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/mark",
            {
                **ctx._identity(),
                "trace_id": trace_id,
                "caller_id": caller_id,
                "used_ids": used_ids,
                "outcome": outcome,
            },
            idempotency_key=idempotency_key,
        )

    def trace(self, *, ctx: BoundContext, trace_id: str) -> dict[str, Any]:
        query = urlencode(ctx._identity())
        return self._get(f"/v1/traces/{trace_id}?{query}")

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path, None)

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST", path, body, idempotency_key=idempotency_key
        )

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self._headers(method, path, body, idempotency_key)
        if self._transport is not None:
            return self._transport(method, path, body)
        wire_body = None if body is None else _strip_none(body)
        payload = None if wire_body is None else json.dumps(wire_body).encode()
        request = Request(
            _join_url(self.base_url, path),
            data=payload,
            method=method,
            headers=self._headers(method, path, wire_body, idempotency_key),
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as error:
            raw = error.read()
            retry_after = error.headers.get("retry-after")
            _raise_error(raw, retry_after=retry_after)
        return json.loads(raw.decode()) if raw else {}

    def _headers(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if body is not None:
            headers["content-type"] = "application/json"
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        mutation = method == "POST" and path in {
            "/v1/episodes",
            "/v1/reflect",
            "/v1/correct",
            "/v1/forget",
            "/v1/mark",
        }
        if mutation:
            if not isinstance(idempotency_key, str) or not 1 <= len(
                idempotency_key.encode()
            ) <= 255:
                raise MemPhantValidationError(
                    "invalid_request",
                    "mutation idempotency_key must contain 1 through 255 UTF-8 bytes"
                )
            headers["idempotency-key"] = idempotency_key
        elif idempotency_key is not None:
            raise MemPhantValidationError(
                "invalid_request",
                "idempotency_key is accepted only by mutation requests"
            )
        return headers


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


def _raise_error(raw: bytes, *, retry_after: str | None = None) -> None:
    try:
        payload = json.loads(raw.decode())
    except json.JSONDecodeError:
        raise MemPhantUnavailable(
            "backend_unavailable", "MemPhant returned a non-JSON error"
        ) from None
    body = payload.get("error", {})
    code = body.get("code", "backend_unavailable")
    message = body.get("message", code)
    request_id = body.get("request_id")
    details = body.get("details") if isinstance(body.get("details"), dict) else {}
    error_type = ERROR_MAP.get(code, MemPhantError)
    if error_type is MemPhantRateLimited:
        raise error_type(
            code, message, request_id=request_id, details=details, retry_after=retry_after
        )
    raise error_type(code, message, request_id=request_id, details=details)


__all__ = [
    "BoundContext",
    "MemPhant",
    "MemPhantAuthError",
    "MemPhantConflict",
    "MemPhantError",
    "MemPhantNotFound",
    "MemPhantRateLimited",
    "MemPhantUnavailable",
    "MemPhantValidationError",
]
