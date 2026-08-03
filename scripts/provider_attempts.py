#!/usr/bin/env python3
"""Durable, benchmark-neutral proof of paid provider attempts."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any


_LEDGER_LOCK = threading.RLock()
CAMPAIGN_HARD_CEILING_NANOS = 200_000_000_000
CAMPAIGN_UNALLOCATED_RESERVE_NANOS = 10_000_000_000
CAMPAIGN_AUTHORIZATION_STATUS = "AUTHORIZED_STATE_MEMORY_CAMPAIGN"


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


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_opening_reservations(
    value: Any, opening_liability_nanos: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("provider-attempt opening reservation inventory is malformed")
    reservations: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("provider-attempt opening reservation inventory is malformed")
        reservation_id = item.get("reservation_id")
        reserved_nanos = item.get("reserved_nanos")
        if (
            not isinstance(reservation_id, str)
            or not reservation_id
            or reservation_id in ids
            or type(reserved_nanos) is not int
            or reserved_nanos <= 0
            or not _valid_sha256(item.get("receipt_sha256"))
            or not _valid_sha256(item.get("proof_sha256"))
        ):
            raise ValueError("provider-attempt opening reservation inventory is malformed")
        ids.add(reservation_id)
        reservations.append({
            "reservation_id": reservation_id,
            "reserved_nanos": reserved_nanos,
            "receipt_sha256": item["receipt_sha256"],
            "proof_sha256": item["proof_sha256"],
        })
    if sum(item["reserved_nanos"] for item in reservations) != opening_liability_nanos:
        raise ValueError(
            "provider-attempt opening reservation inventory does not match opening liability"
        )
    return reservations


def _cost_nanos(response: Any) -> int | None:
    usage = response.get("usage") if isinstance(response, dict) else None
    value = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        decimal = Decimal(str(value))
    except InvalidOperation:
        return None
    if not decimal.is_finite() or decimal < 0:
        return None
    return int(
        (decimal * Decimal(1_000_000_000)).to_integral_value(
            rounding=ROUND_CEILING
        )
    )


def _replay_journal(
    path: Path,
    expected_authorization_sha256: str | None = None,
    expected_hard_ceiling_nanos: int | None = None,
    expected_opening_liability_nanos: int | None = None,
    expected_opening_reservations: list[dict[str, Any]] | None = None,
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
        or header.get("schema") != 2
        or not _valid_sha256(header.get("authorization_sha256"))
        or type(header.get("hard_ceiling_nanos")) is not int
        or header["hard_ceiling_nanos"] <= 0
        or type(header.get("opening_liability_nanos")) is not int
        or not 0 <= header["opening_liability_nanos"] <= header["hard_ceiling_nanos"]
        or header.get("unallocated_reserve_nanos")
        != CAMPAIGN_UNALLOCATED_RESERVE_NANOS
    ):
        raise ValueError("provider-attempt journal header is malformed")
    opening_reservations = _validate_opening_reservations(
        header.get("opening_reservations"), header["opening_liability_nanos"]
    )
    authorization_sha256 = header["authorization_sha256"]
    if (
        expected_authorization_sha256 is not None
        and authorization_sha256 != expected_authorization_sha256
    ):
        raise ValueError("provider-attempt ledger authorization mismatch")
    if (
        expected_hard_ceiling_nanos is not None
        and header["hard_ceiling_nanos"] != expected_hard_ceiling_nanos
    ):
        raise ValueError("provider-attempt ledger hard ceiling mismatch")
    if (
        expected_opening_liability_nanos is not None
        and header["opening_liability_nanos"] != expected_opening_liability_nanos
    ):
        raise ValueError("provider-attempt ledger opening liability mismatch")
    if (
        expected_opening_reservations is not None
        and opening_reservations != expected_opening_reservations
    ):
        raise ValueError("provider-attempt ledger opening reservation mismatch")

    attempts: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    reconciled_ids: set[str] = set()
    opening_by_id = {
        item["reservation_id"]: item for item in opening_reservations
    }
    closed = False
    for event in events[1:]:
        kind = event.get("event")
        if closed:
            raise ValueError("provider-attempt journal has an event after closed")
        if not isinstance(event.get("screen_id"), str) or not event["screen_id"]:
            raise ValueError("provider-attempt journal event screen is malformed")
        if kind == "reconcile":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("provider-attempt reconciliation is malformed")
            reservation_id = payload.get("reservation_id")
            reserved_nanos = payload.get("reserved_nanos")
            settled_nanos = payload.get("settled_nanos")
            opening_reservation = opening_by_id.get(reservation_id)
            if (
                opening_reservation is None
                or reservation_id in reconciled_ids
                or type(reserved_nanos) is not int
                or type(settled_nanos) is not int
                or reserved_nanos != opening_reservation["reserved_nanos"]
                or not 0 <= settled_nanos <= reserved_nanos
                or payload.get("receipt_sha256")
                != opening_reservation["receipt_sha256"]
                or payload.get("proof_sha256")
                != opening_reservation["proof_sha256"]
            ):
                raise ValueError("provider-attempt reconciliation is malformed")
            reconciled_ids.add(reservation_id)
            continue
        if kind == "closed":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("provider-attempt closure is malformed")
            closed = True
            continue
        attempt_id = event.get("attempt_id")
        request_key = event.get("request_key")
        payload = event.get("payload")
        if type(attempt_id) is not int or not isinstance(request_key, str) or not isinstance(payload, dict):
            raise ValueError("provider-attempt journal event is malformed")
        if kind == "start":
            if (
                attempt_id != len(attempts) + 1
                or attempt_id in by_id
                or type(payload.get("max_liability_nanos")) is not int
                or payload["max_liability_nanos"] <= 0
            ):
                raise ValueError("provider-attempt journal forked start transition")
            row = {
                "attempt_id": attempt_id,
                "request_key": request_key,
                "retry_index": payload.get("retry_index", 0),
                "start_sequence": event["sequence"],
                "result_sequence": None,
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
            row["result_sequence"] = event["sequence"]
            row[kind] = payload
        else:
            raise ValueError("provider-attempt journal event kind is malformed")
    if closed:
        state = _journal_state(events, attempts)
        closure = next(
            event for event in reversed(events) if event.get("event") == "closed"
        )
        if closure["payload"] != {
            "authorization_sha256": authorization_sha256,
            "settled_nanos": state["settled_nanos"],
            "unresolved_max_liability_nanos": state[
                "unresolved_max_liability_nanos"
            ],
            "total_liability_nanos": state["total_liability_nanos"],
        }:
            raise ValueError("provider-attempt closure is malformed")
    return authorization_sha256, events, attempts


def _journal_state(
    events: list[dict[str, Any]], attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    header = events[0]
    reconciliations = [
        event for event in events[1:] if event.get("event") == "reconcile"
    ]
    opening = header["opening_liability_nanos"] - sum(
        event["payload"]["reserved_nanos"] for event in reconciliations
    )
    settled = sum(event["payload"]["settled_nanos"] for event in reconciliations)
    unresolved = 0
    for attempt in attempts:
        response = (
            attempt["result"].get("response")
            if attempt.get("status") == "result" and isinstance(attempt.get("result"), dict)
            else None
        )
        cost_nanos = _cost_nanos(response)
        if cost_nanos is None:
            unresolved += attempt["start"]["max_liability_nanos"]
        else:
            settled += cost_nanos
    return {
        "authorization_sha256": header["authorization_sha256"],
        "hard_ceiling_nanos": header["hard_ceiling_nanos"],
        "opening_liability_nanos": opening,
        "settled_nanos": settled,
        "unresolved_max_liability_nanos": unresolved,
        "total_liability_nanos": opening + settled + unresolved,
        "closed": any(event.get("event") == "closed" for event in events),
        "journal_sha256": hashlib.sha256(b"\n".join(_event_bytes(event) for event in events) + b"\n").hexdigest(),
        "last_event_sha256": hashlib.sha256(_event_bytes(events[-1])).hexdigest(),
    }


def _validate_reconciliation_candidate(
    events: list[dict[str, Any]], event: dict[str, Any]
) -> None:
    payload = event["payload"]
    prior = [item["payload"] for item in events if item.get("event") == "reconcile"]
    reservation_id = payload.get("reservation_id")
    inventory = {
        item["reservation_id"]: item
        for item in events[0]["opening_reservations"]
    }
    opening = inventory.get(reservation_id)
    if opening is None or reservation_id in {
        item["reservation_id"] for item in prior
    }:
        raise ValueError("reconciliation requires a known opening reservation")
    if payload.get("receipt_sha256") != opening["receipt_sha256"]:
        raise ValueError("reconciliation opening reservation receipt mismatch")
    if payload.get("proof_sha256") != opening["proof_sha256"]:
        raise ValueError("reconciliation opening reservation proof mismatch")
    if (
        payload.get("reserved_nanos") != opening["reserved_nanos"]
        or type(payload.get("settled_nanos")) is not int
        or not 0 <= payload["settled_nanos"] <= opening["reserved_nanos"]
    ):
        raise ValueError("provider-attempt reconciliation is malformed")


def fresh_paid_usage(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return False
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    return (
        type(prompt) is int
        and prompt > 0
        and type(completion) is int
        and completion > 0
        and type(total) is int
        and total == prompt + completion
        and _cost_nanos(response) is not None
    )


class ProviderAttemptLedger:
    """Campaign-wide append-before-call, fsynced JSONL state machine.

    started -> result
            -> error
    """

    def __init__(
        self,
        path: Path,
        authorization_sha256: str,
        screen_id: str,
        hard_ceiling_nanos: int,
        opening_liability_nanos: int,
        *,
        opening_reservations: list[dict[str, Any]] | None = None,
    ) -> None:
        path = Path(path).resolve()
        if not _valid_sha256(authorization_sha256):
            raise ValueError("provider-attempt authorization must be a SHA-256")
        if not isinstance(screen_id, str) or not screen_id:
            raise ValueError("provider-attempt screen ID is required")
        if type(hard_ceiling_nanos) is not int or hard_ceiling_nanos <= 0:
            raise ValueError("provider-attempt hard ceiling must be positive")
        if (
            type(opening_liability_nanos) is not int
            or not 0 <= opening_liability_nanos <= hard_ceiling_nanos
        ):
            raise ValueError("provider-attempt opening liability is invalid")
        opening_reservations = _validate_opening_reservations(
            [] if opening_reservations is None else opening_reservations,
            opening_liability_nanos,
        )
        self.path = path
        self.authorization_sha256 = authorization_sha256
        self.screen_id = screen_id
        self.hard_ceiling_nanos = hard_ceiling_nanos
        self.initial_opening_liability_nanos = opening_liability_nanos
        self.opening_reservations = opening_reservations
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
                    _, self._events, self.attempts = self._replay()
                else:
                    self._events: list[dict[str, Any]] = []
                    self.attempts: list[dict[str, Any]] = []
                    self._append(
                        {
                            "schema": 2,
                            "event": "header",
                            "authorization_sha256": authorization_sha256,
                            "hard_ceiling_nanos": hard_ceiling_nanos,
                            "opening_liability_nanos": opening_liability_nanos,
                            "unallocated_reserve_nanos": (
                                CAMPAIGN_UNALLOCATED_RESERVE_NANOS
                            ),
                            "opening_reservations": opening_reservations,
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

    def _replay(
        self,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        return _replay_journal(
            self.path,
            self.authorization_sha256,
            self.hard_ceiling_nanos,
            self.initial_opening_liability_nanos,
            self.opening_reservations,
        )

    def assert_open(self) -> None:
        with _LEDGER_LOCK:
            _, self._events, self.attempts = self._replay()
            state = _journal_state(self._events, self.attempts)
            if state["closed"]:
                raise RuntimeError("provider-attempt campaign is closed")
            if state["total_liability_nanos"] > self.hard_ceiling_nanos:
                raise RuntimeError("provider-attempt campaign hard ceiling exceeded")

    def record(self, event: str, request_key: str, payload: dict | None) -> None:
        with _LEDGER_LOCK:
            _, self._events, self.attempts = self._replay()
            state = _journal_state(self._events, self.attempts)
            if state["closed"]:
                raise RuntimeError("provider-attempt campaign is closed")
            payload = payload or {}
            if event == "start":
                reservation = payload.get("max_liability_nanos")
                if type(reservation) is not int or reservation <= 0:
                    raise ValueError(
                        "provider-attempt start requires positive max_liability_nanos"
                    )
                if (
                    state["total_liability_nanos"] + reservation
                    > self.hard_ceiling_nanos
                    - CAMPAIGN_UNALLOCATED_RESERVE_NANOS
                ):
                    raise RuntimeError(
                        "provider-attempt campaign unallocated reserve would be invaded"
                    )
                attempt_id = len(self.attempts) + 1
                self._append(
                    {
                        "event": "start",
                        "screen_id": self.screen_id,
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
                        "screen_id": self.screen_id,
                        "attempt_id": attempt_id,
                        "request_key": request_key,
                        "payload": payload,
                    }
                )
            else:
                raise ValueError(f"unknown provider-attempt event: {event}")
            _, self._events, self.attempts = self._replay()

    def record_reconciliation(
        self,
        reservation_id: str,
        *,
        settled_nanos: int,
        receipt_sha256: str,
        proof_sha256: str,
    ) -> None:
        with _LEDGER_LOCK:
            _, self._events, self.attempts = self._replay()
            state = _journal_state(self._events, self.attempts)
            if state["closed"]:
                raise RuntimeError("provider-attempt campaign is closed")
            body = {
                "event": "reconcile",
                "screen_id": self.screen_id,
                "payload": {
                    "reservation_id": reservation_id,
                    "reserved_nanos": next(
                        (
                            item["reserved_nanos"]
                            for item in self.opening_reservations
                            if item["reservation_id"] == reservation_id
                        ),
                        None,
                    ),
                    "settled_nanos": settled_nanos,
                    "receipt_sha256": receipt_sha256,
                    "proof_sha256": proof_sha256,
                },
            }
            _validate_reconciliation_candidate(self._events, body)
            self._append(body)
            _, self._events, self.attempts = self._replay()


    def close_campaign(self, projection_path: Path) -> dict[str, Any]:
        with _LEDGER_LOCK:
            _, self._events, self.attempts = self._replay()
            state = _journal_state(self._events, self.attempts)
            if state["closed"]:
                raise RuntimeError("provider-attempt campaign is closed")
            projection_path = Path(projection_path).resolve()
            if projection_path == self.path:
                raise ValueError("campaign closure projection must not overwrite journal")
            closure = {
                "authorization_sha256": self.authorization_sha256,
                "settled_nanos": state["settled_nanos"],
                "unresolved_max_liability_nanos": state[
                    "unresolved_max_liability_nanos"
                ],
                "total_liability_nanos": state["total_liability_nanos"],
            }
            self._append(
                {
                    "event": "closed",
                    "screen_id": self.screen_id,
                    "payload": closure,
                }
            )
            _, self._events, self.attempts = self._replay()
            closed_state = _journal_state(self._events, self.attempts)
            projection = {**closure, "journal_sha256": closed_state["journal_sha256"]}
            _atomic_write_json(projection_path, projection)
            return projection

    def snapshot(self) -> dict[str, Any]:
        with _LEDGER_LOCK:
            _, self._events, self.attempts = self._replay()
            responses = [
                row["result"]["response"]
                for row in self.attempts
                if row.get("status") == "result"
                and isinstance(row.get("result"), dict)
                and isinstance(row["result"].get("response"), dict)
            ]
            priced = [response for response in responses if fresh_paid_usage(response)]
            return {
                **_journal_state(self._events, self.attempts),
                "provider_attempts": len(self.attempts),
                "priced_provider_attempts": len(priced),
                "unpriced_provider_attempts": len(self.attempts) - len(priced),
                "reported_cost_usd": sum(
                    float(row["usage"]["cost"]) for row in priced
                ),
                "attempts_sha256": _sha256_json(self.attempts),
                "attempts": self.attempts,
            }


def open_campaign_ledger(
    authorization_packet_path: Path,
    *,
    screen_id: str,
    expected_journal_path: Path | None = None,
) -> ProviderAttemptLedger:
    """Open the sole journal named by a frozen campaign authorization packet."""
    packet_path = Path(authorization_packet_path).resolve()
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("campaign authorization packet is unreadable") from error
    authorization = packet.get("authorization") if isinstance(packet, dict) else None
    campaign = packet.get("campaign") if isinstance(packet, dict) else None
    scope = (
        {
            key: value
            for key, value in packet.items()
            if key not in {"schema_version", "status", "authorization"}
        }
        if isinstance(packet, dict)
        else None
    )
    if (
        not isinstance(packet, dict)
        or packet.get("status") != CAMPAIGN_AUTHORIZATION_STATUS
        or not isinstance(authorization, dict)
        or not _valid_sha256(authorization.get("authorization_scope_sha256"))
        or authorization["authorization_scope_sha256"] != _sha256_json(scope)
        or not isinstance(campaign, dict)
    ):
        raise ValueError("campaign authorization packet is not active and canonical")
    journal_value = campaign.get("journal_path")
    if not isinstance(journal_value, str) or not journal_value:
        raise ValueError("campaign authorization packet journal path is malformed")
    journal_path = Path(journal_value)
    if not journal_path.is_absolute():
        journal_path = packet_path.parent / journal_path
    journal_path = journal_path.resolve()
    if (
        expected_journal_path is not None
        and Path(expected_journal_path).resolve() != journal_path
    ):
        raise ValueError("paid entrypoint differs from canonical campaign journal path")
    if campaign.get("hard_ceiling_nanos") != CAMPAIGN_HARD_CEILING_NANOS:
        raise ValueError("campaign authorization hard ceiling mismatch")
    if (
        campaign.get("unallocated_reserve_nanos")
        != CAMPAIGN_UNALLOCATED_RESERVE_NANOS
    ):
        raise ValueError("campaign authorization unallocated reserve mismatch")
    opening = campaign.get("opening_liability_nanos")
    reservations = _validate_opening_reservations(
        campaign.get("opening_reservations"), opening
    )
    return ProviderAttemptLedger(
        journal_path,
        authorization["authorization_scope_sha256"],
        screen_id,
        CAMPAIGN_HARD_CEILING_NANOS,
        opening,
        opening_reservations=reservations,
    )


def open_campaign_ledger_from_env(
    *, screen_id: str, expected_journal_path: Path | None = None
) -> ProviderAttemptLedger:
    packet = os.environ.get("MEMPHANT_CAMPAIGN_AUTHORIZATION")
    if not packet:
        raise RuntimeError(
            "paid execution requires MEMPHANT_CAMPAIGN_AUTHORIZATION"
        )
    return open_campaign_ledger(
        Path(packet),
        screen_id=screen_id,
        expected_journal_path=expected_journal_path,
    )


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
        _, events, attempts = _replay_journal(Path(path))
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
        **_journal_state(events, attempts),
        "provider_attempts": len(attempts),
        "priced_provider_attempts": len(priced),
        "unpriced_provider_attempts": len(attempts) - len(priced),
        "reported_cost_usd": sum(float(row["usage"]["cost"]) for row in priced),
        "attempts_sha256": actual_hash,
        "attempts": attempts,
    }


def _value(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


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
    return {
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
    ledger: ProviderAttemptLedger,
    *,
    max_liability_nanos: int,
    context: dict[str, Any] | None = None,
    generation_lookup=None,
    generation_lookup_factory=None,
    max_output_tokens: int | None = None,
) -> ProviderAttemptLedger:
    """Wrap available sync/async OpenAI clients with the same durable meter."""
    if not isinstance(ledger, ProviderAttemptLedger):
        raise TypeError("ledger must be an open ProviderAttemptLedger")
    if type(max_liability_nanos) is not int or max_liability_nanos <= 0:
        raise ValueError("max_liability_nanos must be positive")
    ledger.assert_open()
    if generation_lookup is not None and generation_lookup_factory is not None:
        raise ValueError(
            "generation_lookup and generation_lookup_factory are mutually exclusive"
        )
    context = dict(context or {})

    def cap_output(kwargs: dict[str, Any]) -> None:
        if max_output_tokens is None:
            return
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        for key in ("max_tokens", "max_completion_tokens"):
            if key in kwargs:
                if not isinstance(kwargs[key], int) or kwargs[key] > max_output_tokens:
                    raise ValueError(f"{key} exceeds the benchmark output ceiling")
                return
        kwargs["max_tokens"] = max_output_tokens

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
            client = None

            def original_create(*create_args, **create_kwargs):
                nonlocal client
                with _LEDGER_LOCK:
                    if client is None:
                        client = original(*args, **kwargs)
                return client.chat.completions.create(*create_args, **create_kwargs)

            if is_async:
                async def create(*create_args, **create_kwargs):
                    cap_output(create_kwargs)
                    return await _meter_async(
                        original_create, create_args, create_kwargs, ledger,
                        context, generation_lookup, generation_lookup_factory,
                        max_liability_nanos,
                    )
            else:
                def create(*create_args, **create_kwargs):
                    cap_output(create_kwargs)
                    return _meter_sync(
                        original_create, create_args, create_kwargs, ledger,
                        context, generation_lookup, generation_lookup_factory,
                        max_liability_nanos,
                    )

            class Completions:
                pass

            class Chat:
                completions = Completions()

            class MeteredClient:
                chat = Chat()

            MeteredClient.chat.completions.create = create
            return MeteredClient()

        setattr(openai_module, name, constructor)

    install("OpenAI", is_async=False)
    install("AsyncOpenAI", is_async=True)
    return ledger


def openrouter_generation_lookup(api_key: str, base_url: str | None = None):
    """Return a bounded lookup callable for OpenRouter's authoritative stats API.

    ``base_url`` exists so a $0 loopback dry run can exercise the settled-cost
    fallback instead of leaking it to the live host: without it, a stubbed
    chat-completions call whose reported cost needs reconciling would still
    reach openrouter.ai, and the "stub round trip" would not actually be one.
    """
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("OpenRouter generation lookup requires an API key")
    endpoint = (base_url or "https://openrouter.ai/api/v1") + "/generation"

    def lookup(response_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"id": response_id})
        request = urllib.request.Request(
            f"{endpoint}?{query}",
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


def openrouter_generation_lookup_from_env():
    """Resolve OpenRouter credentials only after an attempt is durable."""
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
        "OPEN_ROUTER_API_KEY"
    )
    if not api_key:
        return None
    return openrouter_generation_lookup(api_key)


def _attempt_input(
    kwargs: dict[str, Any], context: dict[str, Any], max_liability_nanos: int
) -> tuple[str, dict[str, Any]]:
    requested_model = kwargs.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        raise RuntimeError("completion request omitted model")
    request_sha256 = _sha256_json(kwargs)
    return request_sha256, {
        "context": context,
        "retry_index": 0,
        "requested_model": requested_model,
        "request_sha256": request_sha256,
        "max_liability_nanos": max_liability_nanos,
    }


def _meter_sync(
    create,
    args,
    kwargs,
    ledger,
    context,
    generation_lookup,
    generation_lookup_factory,
    max_liability_nanos,
):
    request_key, start = _attempt_input(kwargs, context, max_liability_nanos)
    with _LEDGER_LOCK:
        ledger.record("start", request_key, start)
    started = time.monotonic()
    try:
        active_lookup = (
            generation_lookup_factory()
            if generation_lookup_factory is not None
            else generation_lookup
        )
        response = create(*args, **kwargs)
        normalized = _normalize_response(
            response, start["requested_model"], time.monotonic() - started,
            start["request_sha256"], active_lookup,
        )
    except BaseException as error:
        with _LEDGER_LOCK:
            ledger.record(
                "error",
                request_key,
                {**_error_payload(error, time.monotonic() - started), "context": context},
            )
        raise
    with _LEDGER_LOCK:
        ledger.record(
            "result", request_key, {"context": context, "response": normalized}
        )
    return response


async def _meter_async(
    create,
    args,
    kwargs,
    ledger,
    context,
    generation_lookup,
    generation_lookup_factory,
    max_liability_nanos,
):
    request_key, start = _attempt_input(kwargs, context, max_liability_nanos)
    with _LEDGER_LOCK:
        ledger.record("start", request_key, start)
    started = time.monotonic()
    try:
        active_lookup = (
            generation_lookup_factory()
            if generation_lookup_factory is not None
            else generation_lookup
        )
        response = await create(*args, **kwargs)
        normalized = await asyncio.to_thread(
            _normalize_response,
            response,
            start["requested_model"],
            time.monotonic() - started,
            start["request_sha256"],
            active_lookup,
        )
    except BaseException as error:
        with _LEDGER_LOCK:
            ledger.record(
                "error",
                request_key,
                {**_error_payload(error, time.monotonic() - started), "context": context},
            )
        raise
    with _LEDGER_LOCK:
        ledger.record(
            "result", request_key, {"context": context, "response": normalized}
        )
    return response
