#!/usr/bin/env python3
"""Deterministic free gate for outcome-coupled coding-agent memory."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


CAUSAL_ATTRIBUTIONS = {
    "explicit_user",
    "deterministic_scorer",
    "randomized_counterfactual",
}
PROCEDURAL_KINDS = {"adherence", "procedural"}


class CompactionError(ValueError):
    pass


def reconstruct_compactions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve Claude compact boundaries without copying transcript bodies."""
    by_uuid = {row.get("uuid"): row for row in rows if row.get("uuid")}
    cuts = []
    boundaries = [i for i, row in enumerate(rows) if row.get("subtype") == "compact_boundary"]
    for position, index in enumerate(boundaries):
        metadata = rows[index].get("compactMetadata") or {}
        preserved = metadata.get("preservedMessages") or {}
        anchor = preserved.get("anchorUuid")
        summary = by_uuid.get(anchor)
        if not anchor or not summary or not summary.get("isCompactSummary"):
            raise CompactionError("compact summary anchor is missing or invalid")
        preserved_rows = []
        for uuid in preserved.get("allUuids") or []:
            if uuid not in by_uuid:
                raise CompactionError(f"preserved message is missing: {uuid}")
            preserved_rows.append(by_uuid[uuid])
        pre = metadata.get("preTokens")
        post = metadata.get("postTokens")
        dropped = metadata.get("cumulativeDroppedTokens")
        if not all(isinstance(value, int) for value in (pre, post, dropped)) or pre - dropped != post:
            raise CompactionError("compact token metadata does not reconcile")
        next_index = boundaries[position + 1] if position + 1 < len(boundaries) else len(rows)
        active_tail = [
            row
            for row in rows[index + 1 : next_index]
            if row.get("uuid") != anchor and row.get("type") in {"user", "assistant"}
        ]
        cuts.append(
            {
                "boundary_uuid": rows[index].get("uuid"),
                "summary": summary,
                "preserved": preserved_rows,
                "active_tail": active_tail,
                "token_metadata_valid": True,
                "pre_tokens": pre,
                "post_tokens": post,
                "dropped_tokens": dropped,
            }
        )
    return cuts


def _valid_case(case: dict[str, Any]) -> bool:
    try:
        learned = datetime.fromisoformat(case["learned_at"].replace("Z", "+00:00"))
        held_out = datetime.fromisoformat(case["held_out_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    return all(
        (
            learned < held_out,
            case.get("source_task_hash") != case.get("held_out_task_hash"),
            bool(case.get("source_task_hash")),
            bool(case.get("held_out_task_hash")),
            bool(case.get("objective_predicate")),
            bool(case.get("rule_version")),
            bool(case.get("context_boundary")),
            not case.get("sensitive", False),
        )
    )


def classify_scopes(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selectors = {
        "A1": lambda case: case.get("kind") == "adherence",
        "A2": lambda case: case.get("kind") == "procedural",
        "A3": lambda case: case.get("kind") not in PROCEDURAL_KINDS
        and bool(case.get("objective_predicate")),
    }
    result = {}
    for arm, selected in selectors.items():
        relevant = [case for case in cases if selected(case)]
        valid = [case for case in relevant if _valid_case(case)]
        result[arm] = {
            "status": "eligible" if len(valid) >= 4 else "UNTESTABLE",
            "n_valid": len(valid),
            "valid_case_ids": [case["case_id"] for case in valid[:4]],
            "rejected_case_ids": [case.get("case_id", "missing") for case in relevant if not _valid_case(case)],
        }
    return result


def _wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return ((centre - margin) / denominator, (centre + margin) / denominator)


def _policy_applies(policy: str, unit: dict[str, Any]) -> bool:
    kind = unit.get("kind")
    if policy == "A1":
        return kind == "adherence"
    if policy == "A2":
        return kind in PROCEDURAL_KINDS
    if policy == "A3":
        return kind in PROCEDURAL_KINDS or unit.get("objectively_scoreable") is True
    return False


def pack_for_policy(
    units: list[dict[str, Any]], events: list[dict[str, Any]], policy: str
) -> list[str]:
    if policy == "C0":
        return []
    if policy == "C1":
        return [unit["unit_id"] for unit in units if unit.get("validated")]
    if policy not in {"A1", "A2", "A3"}:
        raise ValueError(f"unknown policy: {policy}")

    indexed = []
    for ordinal, unit in enumerate(units):
        if not unit.get("validated"):
            continue
        unit_events = [event for event in events if event.get("unit_id") == unit["unit_id"]]
        if not _policy_applies(policy, unit):
            indexed.append((1, 0.0, ordinal, unit["unit_id"]))
            continue
        if any(
            event.get("event") == "silenced" and event.get("attribution") == "explicit_user"
            for event in unit_events
        ):
            continue
        causal = [
            event
            for event in unit_events
            if event.get("event") in {"helpful", "harmful"}
            and event.get("attribution") in CAUSAL_ATTRIBUTIONS
        ]
        helpful = sum(event["event"] == "helpful" for event in causal)
        lower, upper = _wilson(helpful, len(causal))
        if helpful:
            indexed.append((0, -lower, ordinal, unit["unit_id"]))
        elif causal and upper < 0.5:
            indexed.append((2, upper, ordinal, unit["unit_id"]))
        else:
            indexed.append((1, 0.0, ordinal, unit["unit_id"]))
    indexed.sort()
    return [unit_id for _, _, _, unit_id in indexed]


def should_dispatch(control_pack: list[str], treatment_pack: list[str]) -> str:
    return "no_policy_difference" if control_pack == treatment_pack else "dispatch"


@dataclass
class BudgetLedger:
    total_cap: float
    phase_caps: dict[str, float]
    _next_id: int = 0
    _reservations: dict[int, tuple[str, float]] = field(default_factory=dict)
    _settled: dict[str, float] = field(default_factory=dict)

    def _committed(self, phase: str | None = None) -> float:
        settled = sum(self._settled.values()) if phase is None else self._settled.get(phase, 0.0)
        reserved = sum(
            amount for reserved_phase, amount in self._reservations.values()
            if phase is None or reserved_phase == phase
        )
        return settled + reserved

    def reserve(self, phase: str, amount: float) -> int:
        if amount <= 0:
            raise ValueError("reservation must be positive")
        if self._committed() + amount > self.total_cap:
            raise ValueError("total budget exceeded")
        if phase not in self.phase_caps or self._committed(phase) + amount > self.phase_caps[phase]:
            raise ValueError("phase budget exceeded")
        self._next_id += 1
        self._reservations[self._next_id] = (phase, amount)
        return self._next_id

    def settle(self, reservation: int, amount: float) -> None:
        phase, reserved = self._reservations.pop(reservation)
        if amount < 0 or amount > reserved:
            raise ValueError("settled amount exceeds reservation")
        self._settled[phase] = self._settled.get(phase, 0.0) + amount
