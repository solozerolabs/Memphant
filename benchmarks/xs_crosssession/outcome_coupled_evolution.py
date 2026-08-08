#!/usr/bin/env python3
"""Deterministic free gate for outcome-coupled coding-agent memory."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


CAUSAL_ATTRIBUTIONS = {
    "explicit_user",
    "deterministic_scorer",
    "randomized_counterfactual",
}
PROCEDURAL_KINDS = {"adherence", "procedural"}
KNOWN_VIOLATIONS = (
    ("15403b3d", "full re-runs", "full-local-gate", True),
    ("9e49b76b", "full gate locally", "full-local-gate", True),
    ("ed4f8502", "always continue work until fully done", "stop-mid-workstream", True),
    ("38ba8780", "keep stopping", "stop-mid-workstream", True),
    ("c29d3978", "overly clever", "overly-clever", False),
    ("8cc9baa3", "contaminated", "contamination", False),
)
TRANSCRIPT_ROOTS = (
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Syndai",
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Memphant",
)


class CompactionError(ValueError):
    pass


def reconstruct_compactions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve Claude compact boundaries without copying transcript bodies."""
    by_uuid = {row.get("uuid"): row for row in rows if row.get("uuid")}
    cuts = []
    boundaries = [i for i, row in enumerate(rows) if row.get("subtype") == "compact_boundary"]
    prior_cumulative = 0
    for position, index in enumerate(boundaries):
        metadata = rows[index].get("compactMetadata") or {}
        preserved = metadata.get("preservedMessages") or {}
        anchor = preserved.get("anchorUuid")
        summary = by_uuid.get(anchor)
        if not anchor or not summary or not summary.get("isCompactSummary"):
            raise CompactionError("compact summary anchor is missing or invalid")
        preserved_rows = []
        # Claude records the exposed preserved messages in `uuids`; `allUuids`
        # may also name internal chain nodes that are not serialized as rows.
        for uuid in preserved.get("uuids", preserved.get("allUuids") or []):
            if uuid not in by_uuid:
                raise CompactionError(f"preserved message is missing: {uuid}")
            preserved_rows.append(by_uuid[uuid])
        pre = metadata.get("preTokens")
        post = metadata.get("postTokens")
        dropped = metadata.get("cumulativeDroppedTokens")
        dropped_this_cut = dropped - prior_cumulative if isinstance(dropped, int) else None
        if (
            not all(isinstance(value, int) for value in (pre, post, dropped_this_cut))
            or dropped_this_cut < 0
            or pre - dropped_this_cut != post
        ):
            raise CompactionError("compact token metadata does not reconcile")
        prior_cumulative = dropped
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
                "dropped_tokens": dropped_this_cut,
                "cumulative_dropped_tokens": dropped,
            }
        )
    return cuts


def _text(row: dict[str, Any]) -> str:
    content = (row.get("message") or {}).get("content", row.get("content", ""))
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            parts.append(f"tool:{block.get('name', '')}")
    return " ".join(parts)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def grade_liveness(rows: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    correction_index = next(
        (
            index
            for index, row in enumerate(rows)
            if (row.get("message") or {}).get("role") == "user"
            and needle.lower() in _text(row).lower()
        ),
        None,
    )
    if correction_index is None:
        return {"status": "fail", "reason": "correction_not_found"}
    action = next(
        (
            row
            for row in reversed(rows[:correction_index])
            if (row.get("message") or {}).get("role") == "assistant" and _text(row)
        ),
        None,
    )
    task = next(
        (
            row
            for row in reversed(rows[:correction_index])
            if (row.get("message") or {}).get("role") == "user"
            and not row.get("isCompactSummary")
            and _text(row)
        ),
        None,
    )
    correction = rows[correction_index]
    if not action or not task:
        return {"status": "fail", "reason": "preceding_task_or_action_not_found"}
    return {
        "status": "pass",
        "historical_grade": "violation",
        "timestamp": correction.get("timestamp"),
        "correction_uuid": correction.get("uuid"),
        "action_uuid": action.get("uuid"),
        "task_hash": _hash_text(_text(task)),
        "correction_hash": _hash_text(_text(correction)),
    }


def blind_arms(
    case_id: str, context_hash: str, packs: dict[str, list[str]], *, seed: str
) -> list[dict[str, Any]]:
    ordered = sorted(
        packs.items(),
        key=lambda item: _hash_text(f"{seed}:{case_id}:{item[0]}"),
    )
    return [
        {
            "blind_label": f"arm-{index}",
            "context_hash": context_hash,
            "pack": pack,
            "pack_hash": _hash_text(json.dumps(pack, separators=(",", ":"))),
        }
        for index, (_, pack) in enumerate(ordered, 1)
    ]


def build_chronological_cases(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    families = sorted({row["family"] for row in observations if row.get("objective")})
    for family in families:
        rows = sorted(
            (row for row in observations if row.get("objective") and row["family"] == family),
            key=lambda row: row["timestamp"],
        )
        for source, held_out in zip(rows, rows[1:]):
            cases.append(
                {
                    "case_id": _hash_text(f"{family}:{source['session_id']}:{held_out['session_id']}")[:16],
                    "kind": "adherence",
                    "learned_at": source["timestamp"],
                    "held_out_at": held_out["timestamp"],
                    "source_task_hash": source["task_hash"],
                    "held_out_task_hash": held_out["task_hash"],
                    "objective_predicate": f"historical_action_violation:{family}",
                    "rule_version": _hash_text(f"outcome-coupled-rule-v1:{family}"),
                    "context_boundary": held_out["context_boundary"],
                    "sensitive": False,
                }
            )
    return cases


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


def gate_verdict(
    liveness_pass: int,
    liveness_expected: int,
    scopes: dict[str, dict[str, Any]],
    policy_differences: dict[str, str],
) -> str:
    if liveness_pass != liveness_expected:
        return "FREE_GATE_CLOSED_INSTRUMENT_FAILED"
    eligible = [arm for arm, row in scopes.items() if row["status"] == "eligible"]
    if not eligible:
        return "FREE_GATE_CLOSED_UNTESTABLE"
    if not any(policy_differences[arm] == "dispatch" for arm in eligible):
        return "FREE_GATE_CLOSED_NO_POLICY_DIFFERENCE"
    return "FREE_GATE_OPEN"


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


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _transcript_path(session_prefix: str) -> str | None:
    matches = []
    for root in TRANSCRIPT_ROOTS:
        matches.extend(glob.glob(f"{root}/**/{session_prefix}*.jsonl", recursive=True))
    return sorted(matches)[0] if matches else None


def _context_metadata(rows: list[dict[str, Any]], correction_uuid: str) -> dict[str, Any]:
    correction_index = next(i for i, row in enumerate(rows) if row.get("uuid") == correction_uuid)
    cuts = reconstruct_compactions(rows)
    boundary_indexes = {
        row.get("uuid"): index
        for index, row in enumerate(rows)
        if row.get("subtype") == "compact_boundary"
    }
    eligible = [cut for cut in cuts if boundary_indexes[cut["boundary_uuid"]] < correction_index]
    if eligible:
        cut = eligible[-1]
        start = boundary_indexes[cut["boundary_uuid"]] + 1
        context_rows = cut["preserved"] + [cut["summary"]] + rows[start:correction_index]
        boundary = f"compact:{cut['boundary_uuid']}"
    else:
        context_rows = rows[:correction_index]
        boundary = "session:start"
    canonical = [
        ((row.get("message") or {}).get("role"), _text(row))
        for row in context_rows
        if not row.get("isSidechain") and _text(row)
    ]
    return {
        "context_boundary": boundary,
        "context_hash": _hash_text(json.dumps(canonical, separators=(",", ":"))),
        "compaction_count": len(cuts),
        "compaction_metadata_valid": True,
    }


def _combined_sha256(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        file_digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(chunk)
        digest.update(file_digest.digest())
    return digest.hexdigest()


def qualify(out_path: str) -> dict[str, Any]:
    observations = []
    paths = []
    for session_prefix, needle, family, objective in KNOWN_VIOLATIONS:
        path = _transcript_path(session_prefix)
        if not path:
            observations.append(
                {"session_id": session_prefix, "family": family, "objective": objective, "status": "fail"}
            )
            continue
        paths.append(path)
        rows = _read_jsonl(path)
        grade = grade_liveness(rows, needle)
        observation = {
            "session_id": session_prefix,
            "family": family,
            "objective": objective,
            **grade,
        }
        if grade["status"] == "pass":
            observation.update(_context_metadata(rows, grade["correction_uuid"]))
        observations.append(observation)

    cases = build_chronological_cases(observations)
    scopes = classify_scopes(cases)
    liveness_pass = sum(row.get("status") == "pass" for row in observations)

    units = [
        {"unit_id": family, "kind": "adherence", "validated": True}
        for family in sorted({row["family"] for row in observations})
    ]
    observational_events = [
        {"unit_id": row["family"], "event": "harmful", "attribution": "observational"}
        for row in observations
        if row.get("status") == "pass"
    ]
    packs = {
        policy: pack_for_policy(units, observational_events, policy)
        for policy in ("C0", "C1", "A1", "A2", "A3")
    }
    policy_differences = {
        policy: should_dispatch(packs["C1"], packs[policy])
        for policy in ("A1", "A2", "A3")
    }
    verdict = gate_verdict(liveness_pass, len(KNOWN_VIOLATIONS), scopes, policy_differences)
    gate_open = verdict == "FREE_GATE_OPEN"
    result = {
        "schema_version": 1,
        "status": "complete",
        "result_read": True,
        "verdict": verdict,
        "runtime_gate": "open" if gate_open else "closed",
        "paid_gate": "open" if gate_open else "closed",
        "spend_usd": 0,
        "privacy": {
            "raw_content_committed": False,
            "public_fields": "hashes, counters, IDs, timestamps, predicates, and aggregate decisions only",
        },
        "instrument": {
            "known_violations_expected": len(KNOWN_VIOLATIONS),
            "known_violations_found_and_graded": liveness_pass,
            "all_compaction_metadata_valid": all(
                row.get("compaction_metadata_valid") is True
                for row in observations
                if row.get("status") == "pass"
            ),
        },
        "observations": [
            {
                key: row.get(key)
                for key in (
                    "session_id",
                    "family",
                    "objective",
                    "status",
                    "historical_grade",
                    "timestamp",
                    "action_uuid",
                    "task_hash",
                    "context_boundary",
                    "context_hash",
                    "compaction_count",
                    "compaction_metadata_valid",
                )
            }
            for row in observations
        ],
        "chronological_cases": cases,
        "scopes": scopes,
        "lifecycle_simulation": {
            "causal_events": 0,
            "observational_events": len(observational_events),
            "packs": packs,
            "policy_differences": policy_differences,
        },
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": (
                f"The free instrument found and graded {liveness_pass}/{len(KNOWN_VIOLATIONS)} known violations, "
                f"but only {len(cases)} valid chronological adherence cases and no causal pack difference, so no paid or runtime gate opened."
            ),
            "power": {
                "test": "descriptive-only (no test)",
                "n": len(cases),
                "b": 0,
                "c": 0,
                "n_d": 0,
                "psi_observed": None,
                "mde_at_80": None,
                "computed_by": "not applicable; qualification stopped before paired model cells",
                "source": out_path,
            },
            "mechanism_enabled": False,
            "probe_kind": None,
            "mechanism_evidence": "No model or runtime mechanism ran. Historical helpful/harmful signals were observational and therefore could not alter lifecycle ordering.",
            "harness": {
                "embed_model": "none",
                "scorer": "deterministic transcript structure and preregistered historical correction predicates",
                "k": "four required cases per independent scope",
                "budget": 0,
                "flags": ["free-gate", "no-model", "no-runtime", "observational-evidence-ineligible"],
                "command": "python3 -m benchmarks.xs_crosssession.outcome_coupled_evolution qualify",
            },
            "corpus": {
                "sha256": _combined_sha256(paths),
                "snapshot_id": "local-claude-known-violation-transcripts-2026-08-08",
                "n_items": len(paths),
            },
            "notes": "Private transcript bodies and correction text remain local. n_d=0 and n<4 per scope force decisional=false.",
        },
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/free-gate.json",
    )
    args = parser.parse_args()
    if args.command == "qualify":
        result = qualify(args.out)
        print(
            json.dumps(
                {
                    "verdict": result["verdict"],
                    "liveness": result["instrument"]["known_violations_found_and_graded"],
                    "scopes": result["scopes"],
                    "spend_usd": result["spend_usd"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
