#!/usr/bin/env python3
"""Durable, benchmark-neutral proof of paid provider attempts."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


_LEDGER_LOCK = threading.RLock()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _response_sha256(response: Any) -> str:
    if isinstance(response, dict):
        value = response
    elif callable(getattr(response, "model_dump", None)):
        value = response.model_dump(mode="json")
    elif hasattr(response, "__dict__"):
        value = {
            key: (
                nested.__dict__ if hasattr(nested, "__dict__") else nested
            )
            for key, nested in vars(response).items()
        }
    else:
        raise RuntimeError("provider response cannot be hashed deterministically")
    return _sha256_json(value)


def _event_bytes(event: dict[str, Any]) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode()


def _append_event(path: Path, event: dict[str, Any]) -> None:
    """Append and fsync exactly one journal event; prior bytes are untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(_event_bytes(event) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _replay_journal(
    path: Path, expected_fingerprint: str | None = None
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise ValueError("provider-attempt journal is truncated")
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for sequence, line in enumerate(raw.splitlines()):
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("provider-attempt journal is malformed") from error
        if not isinstance(event, dict) or event.get("sequence") != sequence:
            raise ValueError("provider-attempt journal sequence mismatch")
        claimed_event_hash = event.get("event_sha256")
        unhashed_event = {key: value for key, value in event.items() if key != "event_sha256"}
        if claimed_event_hash != _sha256_json(unhashed_event):
            raise ValueError("provider-attempt journal event hash mismatch")
        if event.get("previous_event_sha256") != previous_hash:
            raise ValueError("provider-attempt journal hash-chain mismatch")
        events.append(event)
        previous_hash = hashlib.sha256(_event_bytes(event)).hexdigest()

    header = events[0]
    if (
        header.get("event") != "header"
        or header.get("schema") != 1
        or not isinstance(header.get("generation_fingerprint"), str)
    ):
        raise ValueError("provider-attempt journal header is malformed")
    fingerprint = header["generation_fingerprint"]
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise ValueError("provider-attempt ledger fingerprint mismatch")

    attempts: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    for event in events[1:]:
        kind = event.get("event")
        attempt_id = event.get("attempt_id")
        request_key = event.get("request_key")
        payload = event.get("payload")
        if type(attempt_id) is not int or not isinstance(request_key, str) or not isinstance(payload, dict):
            raise ValueError("provider-attempt journal event is malformed")
        if kind == "start":
            if attempt_id != len(attempts) + 1 or attempt_id in by_id:
                raise ValueError("provider-attempt journal forked start transition")
            row = {
                "attempt_id": attempt_id,
                "request_key": request_key,
                "retry_index": payload.get("retry_index", 0),
                "start": payload,
                "status": "started",
                "result": None,
                "error": None,
            }
            attempts.append(row)
            by_id[attempt_id] = row
        elif kind in {"result", "error"}:
            row = by_id.get(attempt_id)
            if row is None or row["request_key"] != request_key or row["status"] != "started":
                raise ValueError("provider-attempt journal forked terminal transition")
            row["status"] = kind
            row[kind] = payload
        else:
            raise ValueError("provider-attempt journal event kind is malformed")
    return fingerprint, events, attempts


def fresh_paid_usage(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return False
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    cost = usage.get("cost")
    return (
        type(prompt) is int
        and prompt > 0
        and type(completion) is int
        and completion > 0
        and type(total) is int
        and total == prompt + completion
        and not isinstance(cost, bool)
        and isinstance(cost, (int, float))
        and cost > 0
    )


class ProviderAttemptLedger:
    """Append-before-call, fsynced JSONL state machine.

    started -> result
            -> error
    """

    def __init__(self, path: Path, generation_fingerprint: str) -> None:
        self.path = path
        self.generation_fingerprint = generation_fingerprint
        self._lock_path = path.with_name(path.name + ".lock")
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_handle = self._lock_path.open("a+b")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lock_handle.close()
            raise RuntimeError(
                f"provider-attempt authorization is already active: {self._lock_path}"
            ) from error
        with _LEDGER_LOCK:
            try:
                if path.exists():
                    _, self._events, self.attempts = _replay_journal(
                        path, generation_fingerprint
                    )
                else:
                    self._events: list[dict[str, Any]] = []
                    self.attempts: list[dict[str, Any]] = []
                    self._append(
                        {
                            "schema": 1,
                            "event": "header",
                            "generation_fingerprint": generation_fingerprint,
                        }
                    )
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        handle = getattr(self, "_lock_handle", None)
        if handle is not None and not handle.closed:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def __del__(self) -> None:
        self.close()

    def _append(self, body: dict[str, Any]) -> None:
        event = {
            **body,
            "sequence": len(self._events),
            "previous_event_sha256": (
                hashlib.sha256(_event_bytes(self._events[-1])).hexdigest()
                if self._events
                else None
            ),
        }
        event["event_sha256"] = _sha256_json(event)
        _append_event(self.path, event)
        self._events.append(event)

    def record(self, event: str, request_key: str, payload: dict | None) -> None:
        with _LEDGER_LOCK:
            _, self._events, self.attempts = _replay_journal(
                self.path, self.generation_fingerprint
            )
            payload = payload or {}
            if event == "start":
                attempt_id = len(self.attempts) + 1
                self._append(
                    {
                        "event": "start",
                        "attempt_id": attempt_id,
                        "request_key": request_key,
                        "payload": payload,
                    }
                )
            elif event in {"result", "error"}:
                for attempt in reversed(self.attempts):
                    if (
                        attempt["request_key"] == request_key
                        and attempt["status"] == "started"
                    ):
                        attempt_id = attempt["attempt_id"]
                        break
                else:
                    raise RuntimeError(
                        f"provider-attempt {event} has no durable start"
                    )
                self._append(
                    {
                        "event": event,
                        "attempt_id": attempt_id,
                        "request_key": request_key,
                        "payload": payload,
                    }
                )
            else:
                raise ValueError(f"unknown provider-attempt event: {event}")
            _, self._events, self.attempts = _replay_journal(
                self.path, self.generation_fingerprint
            )

    def snapshot(self) -> dict[str, Any]:
        with _LEDGER_LOCK:
            _, self._events, self.attempts = _replay_journal(
                self.path, self.generation_fingerprint
            )
            responses = [
                row["result"]["response"]
                for row in self.attempts
                if row.get("status") == "result"
                and isinstance(row.get("result"), dict)
                and isinstance(row["result"].get("response"), dict)
            ]
            priced = [response for response in responses if fresh_paid_usage(response)]
            return {
                "provider_attempts": len(self.attempts),
                "priced_provider_attempts": len(priced),
                "unpriced_provider_attempts": len(self.attempts) - len(priced),
                "reported_cost_usd": sum(
                    float(row["usage"]["cost"]) for row in priced
                ),
                "attempts_sha256": _sha256_json(self.attempts),
                "attempts": self.attempts,
            }


def provider_attempt_ledger_is_complete(snapshot: dict[str, Any]) -> bool:
    attempts = snapshot.get("attempts")
    if not isinstance(attempts, list):
        return False
    return (
        all(
            isinstance(row, dict)
            and row.get("status") == "result"
            and isinstance(row.get("result"), dict)
            and fresh_paid_usage(row["result"].get("response"))
            and row["result"]["response"].get("parse_status")
            == "provider_response_validated"
            and _valid_attempt_metadata(row)
            for row in attempts
        )
        and snapshot.get("attempts_sha256") == _sha256_json(attempts)
        and snapshot.get("provider_attempts") == len(attempts)
        and snapshot.get("priced_provider_attempts") == len(attempts)
        and snapshot.get("unpriced_provider_attempts") == 0
    )


def _valid_attempt_metadata(row: dict[str, Any]) -> bool:
    response = row["result"]["response"]
    start = row.get("start")
    hashes = (response.get("request_sha256"), response.get("result_sha256"))
    return (
        isinstance(start, dict)
        and all(
            isinstance(response.get(field), str) and response[field]
            for field in ("requested_model", "served_model", "provider")
        )
        and not isinstance(response.get("elapsed_seconds"), bool)
        and isinstance(response.get("elapsed_seconds"), (int, float))
        and response["elapsed_seconds"] >= 0
        and type(response.get("retry_index")) is int
        and response["retry_index"] >= 0
        and row.get("retry_index") == response["retry_index"]
        and start.get("retry_index") == response["retry_index"]
        and start.get("requested_model") == response["requested_model"]
        and start.get("request_sha256") == response["request_sha256"]
        and all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in hashes
        )
    )


def validate_provider_attempt_ledger(snapshot: dict[str, Any]) -> None:
    if not provider_attempt_ledger_is_complete(snapshot):
        raise RuntimeError("provider-attempt ledger contains an interrupted or unpriced attempt")
    response_ids = [row["result"]["response"].get("response_id") for row in snapshot["attempts"]]
    if any(not isinstance(value, str) or not value for value in response_ids):
        raise RuntimeError("provider-attempt ledger has a missing response ID")
    if len(response_ids) != len(set(response_ids)):
        raise RuntimeError("provider-attempt ledger has a duplicate response ID")


def load_provider_attempt_ledger_snapshot(path: Path) -> dict[str, Any]:
    """Load a persisted ledger into the same validated summary used at runtime."""
    try:
        _, _events, attempts = _replay_journal(Path(path))
    except ValueError as error:
        raise RuntimeError(f"malformed provider-attempt ledger: {path}") from error
    actual_hash = _sha256_json(attempts)
    responses = [
        row["result"]["response"]
        for row in attempts
        if row.get("status") == "result"
        and isinstance(row.get("result"), dict)
        and isinstance(row["result"].get("response"), dict)
    ]
    priced = [response for response in responses if fresh_paid_usage(response)]
    return {
        "provider_attempts": len(attempts),
        "priced_provider_attempts": len(priced),
        "unpriced_provider_attempts": len(attempts) - len(priced),
        "reported_cost_usd": sum(float(row["usage"]["cost"]) for row in priced),
        "attempts_sha256": actual_hash,
        "attempts": attempts,
    }


def _value(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _response_text_sha256(response: Any) -> str | None:
    choices = _value(response, "choices")
    if not isinstance(choices, (list, tuple)) or not choices:
        return None
    message = _value(choices[0], "message")
    content = _value(message, "content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, (list, tuple)):
        parts = []
        for part in content:
            part_text = _value(part, "text")
            if isinstance(part_text, str):
                parts.append(part_text)
        text = "".join(parts)
    else:
        return None
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class GenerationStatsLookupError(RuntimeError):
    def __init__(self, response: dict[str, Any], cause: BaseException) -> None:
        super().__init__("OpenRouter generation statistics lookup failed")
        self.response = response
        self.cause = cause


class ProviderResponseValidationError(RuntimeError):
    def __init__(self, message: str, response: dict[str, Any]) -> None:
        super().__init__(message)
        self.response = response


def provider_response_evidence(
    response: Any,
    requested_model: str,
    elapsed_seconds: float,
    request_sha256: str,
    *,
    retry_index: int = 0,
    provider: Any = None,
    parse_status: str = "provider_response_validated",
) -> dict[str, Any]:
    usage = _value(response, "usage")
    evidence = {
        "response_id": _value(response, "id"),
        "requested_model": requested_model,
        "served_model": _value(response, "model"),
        "provider": provider or _value(response, "provider"),
        "usage": {
            "prompt_tokens": _value(usage, "prompt_tokens"),
            "completion_tokens": _value(usage, "completion_tokens"),
            "total_tokens": _value(usage, "total_tokens"),
            "cost": _value(usage, "cost"),
        },
        "elapsed_seconds": elapsed_seconds,
        "retry_index": retry_index,
        "request_sha256": request_sha256,
        "result_sha256": _response_sha256(response),
        "parse_status": parse_status,
    }
    response_text_sha256 = _response_text_sha256(response)
    if response_text_sha256 is not None:
        evidence["response_text_sha256"] = response_text_sha256
    return evidence


def _normalize_response(
    response: Any,
    requested_model: str,
    elapsed_seconds: float,
    request_sha256: str,
    generation_lookup,
) -> dict[str, Any]:
    normalized = provider_response_evidence(
        response, requested_model, elapsed_seconds, request_sha256
    )
    response_id = normalized["response_id"]
    stats = {}
    if (
        isinstance(response_id, str)
        and response_id
        and generation_lookup is not None
    ):
        try:
            stats = generation_lookup(response_id) or {}
        except BaseException as error:
            normalized["parse_status"] = "generation_stats_lookup_failed"
            raise GenerationStatsLookupError(normalized, error) from error
    normalized["served_model"] = stats.get("model") or normalized["served_model"]
    usage = normalized["usage"]
    prompt = stats.get("tokens_prompt")
    completion = stats.get("tokens_completion")
    if type(usage["prompt_tokens"]) is not int or usage["prompt_tokens"] <= 0:
        usage["prompt_tokens"] = prompt
    if type(usage["completion_tokens"]) is not int or usage["completion_tokens"] <= 0:
        usage["completion_tokens"] = completion
    if type(usage["total_tokens"]) is not int or usage["total_tokens"] <= 0:
        usage["total_tokens"] = stats.get("tokens")
        if type(usage["total_tokens"]) is not int and type(prompt) is int and type(completion) is int:
            usage["total_tokens"] = prompt + completion
    cost = usage["cost"]
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost <= 0:
        normalized["usage"]["cost"] = stats.get("total_cost", stats.get("cost"))
    normalized["provider"] = (
        stats.get("provider_name")
        or stats.get("provider")
        or normalized["provider"]
    )
    if not isinstance(response_id, str) or not response_id:
        normalized["parse_status"] = "provenance_validation_failed"
        raise ProviderResponseValidationError("provider response omitted response id", normalized)
    if not isinstance(normalized["served_model"], str) or not normalized["served_model"]:
        normalized["parse_status"] = "provenance_validation_failed"
        raise ProviderResponseValidationError("provider response omitted served model", normalized)
    if not fresh_paid_usage(normalized):
        normalized["parse_status"] = "provenance_validation_failed"
        raise ProviderResponseValidationError(
            "provider response omitted complete paid usage", normalized
        )
    if not isinstance(normalized["provider"], str) or not normalized["provider"]:
        normalized["parse_status"] = "provenance_validation_failed"
        raise ProviderResponseValidationError("provider response omitted provider", normalized)
    return normalized


def _error_payload(error: BaseException, elapsed_seconds: float) -> dict[str, Any]:
    cause = error.cause if isinstance(error, GenerationStatsLookupError) else error
    payload = {
        "type": type(cause).__name__,
        "message": str(cause),
        "elapsed_seconds": elapsed_seconds,
        "retry_index": 0,
    }
    if isinstance(error, (GenerationStatsLookupError, ProviderResponseValidationError)):
        payload["response"] = error.response
    return payload


def install_openai_meter(
    openai_module: Any,
    ledger_path: Path,
    *,
    context: dict[str, Any] | None = None,
    ledger_context: dict[str, Any] | None = None,
    request_metadata=None,
    generation_lookup=None,
) -> ProviderAttemptLedger:
    """Wrap available sync/async OpenAI clients with the same durable meter."""
    context = dict(context or {})
    # An evaluation campaign may invoke the official harness in separate
    # processes for several arm/domain cells while sharing one cumulative
    # attempt ceiling. Attempt ``context`` remains recorded on every row;
    # ``ledger_context`` gives those cells one explicitly scoped journal.
    ledger_context = dict(context if ledger_context is None else ledger_context)
    fingerprint = _sha256_json({"schema_version": 2, "context": ledger_context})
    ledger = ProviderAttemptLedger(Path(ledger_path), fingerprint)

    def install(name: str, *, is_async: bool) -> None:
        original = getattr(openai_module, name, None)
        if original is None:
            return

        def constructor(*args, **kwargs):
            kwargs["max_retries"] = 0
            default_headers = dict(kwargs.get("default_headers") or {})
            # Benchmark attempts must reach the provider and carry positive
            # authoritative cost. This overrides any account/preset response
            # cache without changing the scorer's prompt or model parameters.
            default_headers["X-OpenRouter-Cache"] = "false"
            kwargs["default_headers"] = default_headers
            client = original(*args, **kwargs)
            completions = client.chat.completions
            original_create = completions.create

            if is_async:
                async def create(*create_args, **create_kwargs):
                    return await _meter_async(
                        original_create, create_args, create_kwargs, ledger,
                        context, generation_lookup, request_metadata,
                    )
            else:
                def create(*create_args, **create_kwargs):
                    return _meter_sync(
                        original_create, create_args, create_kwargs, ledger,
                        context, generation_lookup, request_metadata,
                    )
            completions.create = create
            return client

        setattr(openai_module, name, constructor)

    install("OpenAI", is_async=False)
    install("AsyncOpenAI", is_async=True)
    return ledger


def openrouter_generation_lookup(api_key: str):
    """Return a bounded lookup callable for OpenRouter's authoritative stats API."""
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("OpenRouter generation lookup requires an API key")

    def lookup(response_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"id": response_id})
        request = urllib.request.Request(
            f"https://openrouter.ai/api/v1/generation?{query}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        for delay in (1, 2, 4, 8, 16, None):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                retryable = (
                    error.code in {404, 408, 429}
                    or 500 <= error.code <= 599
                )
                if not retryable or delay is None:
                    raise
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    retry_after_seconds = float(retry_after)
                except (TypeError, ValueError):
                    retry_after_seconds = 0
                time.sleep(retry_after_seconds if retry_after_seconds > 0 else delay)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if delay is None:
                    raise
                time.sleep(delay)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("OpenRouter generation lookup returned malformed data")
        return data

    return lookup


def _attempt_input(
    kwargs: dict[str, Any],
    context: dict[str, Any],
    request_metadata=None,
) -> tuple[str, dict[str, Any]]:
    requested_model = kwargs.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        raise RuntimeError("completion request omitted model")
    request_sha256 = _sha256_json(kwargs)
    metadata = request_metadata(kwargs) if request_metadata is not None else {}
    if not isinstance(metadata, dict) or set(metadata) & set(context):
        raise RuntimeError("provider attempt metadata is invalid")
    return request_sha256, {
        "retry_index": 0,
        "requested_model": requested_model,
        "request_sha256": request_sha256,
        **metadata,
        **context,
    }


def _meter_sync(
    create, args, kwargs, ledger, context, generation_lookup, request_metadata=None
):
    request_key, start = _attempt_input(kwargs, context, request_metadata)
    with _LEDGER_LOCK:
        ledger.record("start", request_key, start)
    started = time.monotonic()
    try:
        response = create(*args, **kwargs)
        normalized = _normalize_response(
            response, start["requested_model"], time.monotonic() - started,
            start["request_sha256"], generation_lookup,
        )
        normalized.update(context)
    except BaseException as error:
        with _LEDGER_LOCK:
            ledger.record(
                "error",
                request_key,
                _error_payload(error, time.monotonic() - started),
            )
        raise
    with _LEDGER_LOCK:
        ledger.record("result", request_key, {"response": normalized, **context})
    return response


async def _meter_async(
    create, args, kwargs, ledger, context, generation_lookup, request_metadata=None
):
    request_key, start = _attempt_input(kwargs, context, request_metadata)
    with _LEDGER_LOCK:
        ledger.record("start", request_key, start)
    started = time.monotonic()
    try:
        response = await create(*args, **kwargs)
        normalized = await asyncio.to_thread(
            _normalize_response,
            response,
            start["requested_model"],
            time.monotonic() - started,
            start["request_sha256"],
            generation_lookup,
        )
        normalized.update(context)
    except BaseException as error:
        with _LEDGER_LOCK:
            ledger.record(
                "error",
                request_key,
                _error_payload(error, time.monotonic() - started),
            )
        raise
    with _LEDGER_LOCK:
        ledger.record("result", request_key, {"response": normalized, **context})
    return response
