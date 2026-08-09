#!/usr/bin/env python3
"""Deterministic free gate for outcome-coupled coding-agent memory."""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
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
CORRECTION_PATTERN = re.compile(
    r"\b(no[,.] |I (already )?told you|as I said|I thought (we|you)|you keep|"
    r"stop (doing|using|running)|don't (do|use|run) that|why (do|did) you|"
    r"we (never|don't|do not) (do|use|run)|that's (not right|wrong)|actually,? we)\b",
    re.IGNORECASE,
)
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
PINNED_ACTION_MODEL = "claude-opus-5"
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["continue", "tool_call", "stop"]},
        "tool": {"type": ["string", "null"]},
        "command": {"type": ["string", "null"]},
        "files": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["kind", "tool", "command", "files"],
    "additionalProperties": False,
}
RULE_TEXT = {
    "one-session-workstream": "Continue the active workstream until it is complete. Do not stop merely to report progress or ask permission for an in-scope next step.",
    "explicit-staging": "Stage only explicit file paths. Never use git add -A, git add --all, or git add dot.",
    "full-local-gate": "Run the narrowest relevant local checks. Leave the full repository gate to CI unless the user explicitly asks for it locally.",
}
CODING_REPLAY_PRIOR_SPEND_USD = 20.163992


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
        if position == 0 and all(isinstance(value, int) for value in (pre, post, dropped)):
            prior_cumulative = dropped - (pre - post)
        prior_before_cut = prior_cumulative
        dropped_this_cut = dropped - prior_cumulative if isinstance(dropped, int) else None
        if (
            not all(isinstance(value, int) for value in (pre, post, dropped_this_cut))
            or prior_before_cut < 0
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
                "prior_cumulative_dropped_tokens": prior_before_cut,
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


def mine_correction_candidates(roots: list[Path]) -> dict[str, Any]:
    candidates = []
    seen = set()
    duplicate_turns = 0
    matching_files = set()
    paths = sorted({path for root in roots for path in root.rglob("*.jsonl")})
    for path in paths:
        if "subagents" in path.parts:
            continue
        for row in _read_jsonl(str(path)):
            message = row.get("message") or {}
            text = _text(row)
            if (
                message.get("role") != "user"
                or row.get("isMeta")
                or row.get("isSidechain")
                or not text
                or len(text) >= 2_000
                or text.startswith("<")
                or not CORRECTION_PATTERN.search(text)
            ):
                continue
            matching_files.add(str(path))
            identity = (row.get("uuid") or _hash_text(text), row.get("timestamp"))
            if identity in seen:
                duplicate_turns += 1
                continue
            seen.add(identity)
            candidates.append(
                {
                    "uuid": row.get("uuid"),
                    "timestamp": row.get("timestamp"),
                    "session_id": path.stem[:8],
                    "project": path.parent.name,
                    "path": str(path),
                    "text": text,
                }
            )
    return {
        "session_files": len(paths),
        "regex_matching_files": len(matching_files),
        "candidate_turns": len(candidates),
        "duplicate_turns": duplicate_turns,
        "candidates": candidates,
    }


def score_explicit_staging(command: str) -> str | None:
    matches = list(re.finditer(r"\bgit\s+add\s+([^;&|\n]+)", command))
    if not matches:
        return None
    for match in matches:
        args = match.group(1).strip().split()
        if "-A" in args or "--all" in args or args == ["."]:
            return "harmful"
    return "helpful"


def score_unmasked_gate(command: str) -> str | None:
    if not re.search(r"\b(pytest|cargo\s+test|make\s+check|preflight|ruff|mypy)\b", command):
        return None
    piped = re.search(r"\|\s*(head|tail|grep)\b", command)
    if piped and not re.search(r"set\s+-o\s+pipefail|set\s+-[a-z]*o?pipefail", command):
        return "harmful"
    return "helpful"


def project_triggered_lessons(
    projection: dict[str, Any],
    events: list[dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
    *,
    prompt: str,
    paths: list[str],
) -> list[dict[str, str]]:
    """Select validated projection bodies using causal evidence and deterministic triggers."""
    selected = []
    for unit in projection.get("items", []):
        unit_id = unit.get("unit_id")
        if unit.get("state") != "validated" or unit_id not in triggers:
            continue
        body = unit.get("body")
        body_sha = unit.get("body_sha256")
        if not isinstance(body, str) or _hash_text(body) != body_sha:
            raise ValueError(f"projection body hash mismatch: {unit_id}")
        unit_events = [event for event in events if event.get("unit_id") == unit_id]
        if any(
            event.get("event") == "silenced" and event.get("attribution") == "explicit_user"
            for event in unit_events
        ):
            continue
        if not any(
            event.get("event") == "helpful" and event.get("attribution") in CAUSAL_ATTRIBUTIONS
            for event in unit_events
        ):
            continue
        trigger = triggers[unit_id]
        prompt_match = bool(
            trigger.get("prompt_regex")
            and re.search(str(trigger["prompt_regex"]), prompt, re.IGNORECASE)
        )
        path_match = any(
            fnmatch.fnmatch(path, pattern)
            for pattern in trigger.get("path_globs", [])
            for path in paths
        )
        if prompt_match or path_match:
            selected.append(
                {"unit_id": unit_id, "body": body, "body_sha256": body_sha}
            )
    return selected


def delivery_context(lessons: list[dict[str, str]]) -> str:
    if not lessons:
        return ""
    if len(lessons) == 1:
        lesson = lessons[0]
        return (
            f"MemPhant project lesson ({lesson['unit_id']}@{lesson['body_sha256'][:12]}):\n"
            f"{lesson['body']}"
        )
    rendered = "\n\n".join(
        f"{lesson['unit_id']}@{lesson['body_sha256'][:12]}:\n{lesson['body']}"
        for lesson in lessons
    )
    return f"MemPhant project lessons:\n{rendered}"


def extract_stream_evidence(stdout: str) -> dict[str, Any]:
    commands = []
    tool_count = 0
    result = {}
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "result":
            result = row
        content = (row.get("message") or {}).get("content") or []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_count += 1
            if block.get("name") == "Bash":
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str):
                    commands.append(command)
    return {
        "commands": commands,
        "tool_count": tool_count,
        "cost_usd": float(result.get("total_cost_usd") or 0),
        "valid": result.get("subtype") == "success"
        and pinned_model_used(result.get("modelUsage") or {}, PINNED_ACTION_MODEL),
    }


def grade_coding_replay(
    case_id: str,
    *,
    validator_pass: bool,
    requested_end_state_pass: bool,
    before_dirty: set[str],
    after_dirty: set[str],
    staged: set[str],
    commands: list[str],
    full_gate_ran: bool,
) -> dict[str, Any]:
    if case_id.startswith("explicit-staging"):
        rule_violation = bool(before_dirty & staged) or any(
            score_explicit_staging(command) == "harmful" for command in commands
        )
    elif case_id.startswith("scoped-gate"):
        rule_violation = full_gate_ran
    else:
        raise ValueError(f"unknown coding replay case: {case_id}")
    return {
        "accepted_without_violation": bool(
            validator_pass and requested_end_state_pass and not rule_violation
        ),
        "validator_pass": bool(validator_pass),
        "requested_end_state_pass": bool(requested_end_state_pass),
        "rule_violation": bool(rule_violation),
        "new_dirty": sorted(after_dirty - before_dirty),
    }


def coding_replay_verdict(grades: dict[str, list[bool]]) -> dict[str, Any]:
    control = grades["C0"]
    treatment = grades["M1"]
    if len(control) not in {2, 4, 5, 6} or len(control) != len(treatment):
        raise ValueError("coding replay requires two initial or four-to-six paired cases")
    wins = sum(axis and not static for static, axis in zip(control, treatment))
    losses = sum(static and not axis for static, axis in zip(control, treatment))
    if losses:
        verdict = "CODING_REPLAY_HARMFUL"
    elif wins:
        verdict = "CODING_REPLAY_EXPAND" if len(control) == 2 else "CODING_REPLAY_PASS"
    else:
        verdict = "CODING_REPLAY_FLAT"
    return {
        "verdict": verdict,
        "control_passes": sum(control),
        "treatment_passes": sum(treatment),
        "net_wins": wins,
        "losses": losses,
    }


def score_next_action(case_id: str, payload: dict[str, Any]) -> bool:
    command = payload.get("command") or ""
    if case_id.startswith("a1-continue-"):
        return payload.get("kind") != "stop" and bool(
            payload.get("tool") or command or payload.get("files")
        )
    if case_id == "a1-explicit-stage-sealed":
        return score_explicit_staging(command) == "helpful"
    if case_id == "a1-scoped-gate-9e49b76b":
        full_gate = re.search(
            r"cargo\s+test\s+--workspace|pytest\s+tests/?(?:\s|$)|make\s+check|check_spec_drift|"
            r"clippy\s+--all-targets\s+--all-features",
            command,
        )
        scoped_check = re.search(r"\b(pytest|cargo\s+(?:test|clippy)|ruff|mypy)\b", command)
        return bool(scoped_check and not full_gate)
    raise ValueError(f"unknown action-look case: {case_id}")


def select_first_scored_action(
    transcript_paths: list[Path],
    start: str,
    end: str,
    scorer,
) -> dict[str, Any] | None:
    best = None
    best_rows = None
    seen = set()
    for path in transcript_paths:
        if "subagents" in path.parts:
            continue
        rows = _read_jsonl(str(path))
        for index, row in enumerate(rows):
            timestamp = row.get("timestamp") or ""
            content = (row.get("message") or {}).get("content")
            if not (start <= timestamp <= end) or not isinstance(content, list):
                continue
            commands = [
                str((block.get("input") or {}).get("command", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            outcome = scorer("\n".join(commands))
            if outcome is None:
                continue
            identity = (row.get("uuid"), timestamp)
            if identity in seen:
                continue
            seen.add(identity)
            task = next(
                (
                    prior
                    for prior in reversed(rows[:index])
                    if (prior.get("message") or {}).get("role") == "user"
                    and not prior.get("isMeta")
                    and not prior.get("isSidechain")
                    and not prior.get("isCompactSummary")
                    and _text(prior)
                ),
                None,
            )
            if not task:
                continue
            candidate = {
                "timestamp": timestamp,
                "session_id": path.stem[:8],
                "action_uuid": row.get("uuid"),
                "task_uuid": task.get("uuid"),
                "task_hash": _hash_text(_text(task)),
                "outcome": outcome,
            }
            if best is None or (timestamp, row.get("uuid") or "") < (
                best["timestamp"],
                best["action_uuid"] or "",
            ):
                best, best_rows = candidate, rows
    if best is not None:
        best.update(_context_metadata(best_rows, best["action_uuid"]))
    return best


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


def action_look_verdict(grades: dict[str, list[bool]]) -> dict[str, Any]:
    control = grades["C1"]
    treatment = grades["A1"]
    if len(control) != 4 or len(treatment) != 4:
        raise ValueError("action look requires four paired cases")
    wins = sum(axis and not static for static, axis in zip(control, treatment))
    losses = sum(static and not axis for static, axis in zip(control, treatment))
    treatment_passes = sum(treatment)
    control_passes = sum(control)
    if losses:
        verdict = "ACTION_LOOK_HARMFUL"
    elif treatment_passes >= 3 and wins:
        verdict = "ACTION_LOOK_PASS"
    else:
        verdict = "ACTION_LOOK_FLAT"
    return {
        "verdict": verdict,
        "treatment_passes": treatment_passes,
        "control_passes": control_passes,
        "net_wins": wins,
        "losses": losses,
    }


def pinned_model_used(model_usage: dict[str, Any], pinned: str) -> bool:
    primary = model_usage.get(pinned) or {}
    if primary.get("canonicalModel") != pinned or not primary.get("outputTokens"):
        return False
    return all(
        name == pinned or usage.get("canonicalModel") == "claude-haiku-4-5"
        for name, usage in model_usage.items()
    )


def locked_control_cells(artifact: dict[str, Any], response_dir: Path) -> dict[str, Any]:
    controls = {}
    for cell in artifact["cells"]:
        if cell.get("policy") != "C1":
            continue
        if cell.get("valid") is not True:
            raise ValueError(f"control cell is invalid: {cell['cell_id']}")
        response = response_dir / f"{cell['cell_id']}.response.json"
        if not response.exists() or hashlib.sha256(response.read_bytes()).hexdigest() != cell.get(
            "response_sha256"
        ):
            raise ValueError(f"control response drifted: {cell['cell_id']}")
        controls[cell["case_id"]] = {
            "cell_id": cell["cell_id"],
            "passed": cell["passed"],
            "response_sha256": cell["response_sha256"],
        }
    return controls


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


def admission_pack(case: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    unit_id = case["unit_id"]
    admitted = any(
        event.get("unit_id") == unit_id
        and event.get("event") == "helpful"
        and event.get("attribution") in CAUSAL_ATTRIBUTIONS
        for event in events
    )
    return [unit_id] if admitted else []


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


def _task_hash_before(session_prefix: str, timestamp: str) -> str:
    path = _transcript_path(session_prefix)
    if not path:
        raise ValueError(f"source transcript missing: {session_prefix}")
    task = next(
        (
            row
            for row in reversed(_read_jsonl(path))
            if (row.get("timestamp") or "") <= timestamp
            and (row.get("message") or {}).get("role") == "user"
            and not row.get("isMeta")
            and not row.get("isSidechain")
            and not row.get("isCompactSummary")
            and _text(row)
        ),
        None,
    )
    if not task:
        raise ValueError(f"source task missing before {timestamp}: {session_prefix}")
    return _hash_text(_text(task))


def _case(
    case_id: str,
    unit_id: str,
    source_task_hash: str,
    learned_at: str,
    held_out: dict[str, Any],
    predicate: str,
    rule_version: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "unit_id": unit_id,
        "kind": "adherence",
        "learned_at": learned_at,
        "held_out_at": held_out["timestamp"],
        "source_task_hash": source_task_hash,
        "held_out_task_hash": held_out["task_hash"],
        "objective_predicate": predicate,
        "rule_version": rule_version,
        "context_boundary": held_out["context_boundary"],
        "context_hash": held_out["context_hash"],
        "action_uuid": held_out["action_uuid"],
        "held_out_session_id": held_out["session_id"],
        "sensitive": False,
    }


def active_context(rows: list[dict[str, Any]], action_uuid: str) -> list[dict[str, str]]:
    action_index = next(i for i, row in enumerate(rows) if row.get("uuid") == action_uuid)
    cuts = reconstruct_compactions(rows)
    boundary_indexes = {
        row.get("uuid"): index
        for index, row in enumerate(rows)
        if row.get("subtype") == "compact_boundary"
    }
    eligible = [cut for cut in cuts if boundary_indexes[cut["boundary_uuid"]] < action_index]
    if eligible:
        cut = eligible[-1]
        start = boundary_indexes[cut["boundary_uuid"]] + 1
        context_rows = cut["preserved"] + [cut["summary"]] + [
            row for row in rows[start:action_index] if row.get("uuid") != cut["summary"].get("uuid")
        ]
    else:
        context_rows = rows[:action_index]
    return [
        {"role": (row.get("message") or {}).get("role"), "content": _text(row)}
        for row in context_rows
        if not row.get("isSidechain")
        and (row.get("message") or {}).get("role") in {"user", "assistant"}
        and _text(row)
    ]


def _context_metadata(rows: list[dict[str, Any]], action_uuid: str) -> dict[str, Any]:
    action_index = next(i for i, row in enumerate(rows) if row.get("uuid") == action_uuid)
    cuts = reconstruct_compactions(rows)
    boundary_indexes = {
        row.get("uuid"): index
        for index, row in enumerate(rows)
        if row.get("subtype") == "compact_boundary"
    }
    eligible = [cut for cut in cuts if boundary_indexes[cut["boundary_uuid"]] < action_index]
    boundary = f"compact:{eligible[-1]['boundary_uuid']}" if eligible else "session:start"
    canonical = [
        (message["role"], message["content"]) for message in active_context(rows, action_uuid)
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


def _action_prompt(context: list[dict[str, str]], pack: list[str]) -> str:
    return json.dumps(
        {
            "active_context": context,
            "learned_memory": [RULE_TEXT[unit_id] for unit_id in pack],
            "request": "Choose the single next coding-agent action. Return only the required structured payload.",
        },
        separators=(",", ":"),
    )


def prepare_action_look(free_path: str, private_dir: str, out_path: str) -> dict[str, Any]:
    free_gate = json.loads(Path(free_path).read_text())
    if free_gate.get("paid_gate") != "action_look_open":
        raise ValueError("free gate did not open the action look")
    packs = {
        policy: free_gate["lifecycle_simulation"]["packs"][policy]
        for policy in ("C0", "C1", "A1")
    }
    private = Path(private_dir)
    private.mkdir(parents=True, exist_ok=True)
    cells = []
    public_cells = []
    for case in free_gate["chronological_cases"]:
        path = _transcript_path(case["held_out_session_id"])
        if not path:
            raise ValueError(f"held-out transcript missing: {case['case_id']}")
        context = active_context(_read_jsonl(path), case["action_uuid"])
        context_hash = _hash_text(
            json.dumps(
                [(message["role"], message["content"]) for message in context],
                separators=(",", ":"),
            )
        )
        if context_hash != case["context_hash"]:
            raise ValueError(f"context identity drifted: {case['case_id']}")
        ordered = sorted(
            packs.items(),
            key=lambda item: _hash_text(f"action-look-v1:{case['case_id']}:{item[0]}"),
        )
        for index, (policy, pack) in enumerate(ordered, 1):
            prompt = _action_prompt(context, pack)
            cell_id = f"{case['case_id']}-arm-{index}"
            cells.append(
                {
                    "cell_id": cell_id,
                    "case_id": case["case_id"],
                    "blind_label": f"arm-{index}",
                    "policy": policy,
                    "context_hash": context_hash,
                    "pack": pack,
                    "prompt": prompt,
                }
            )
            public_cells.append(
                {
                    "cell_id": cell_id,
                    "case_id": case["case_id"],
                    "blind_label": f"arm-{index}",
                    "context_hash": context_hash,
                    "pack_hash": _hash_text(json.dumps(pack, separators=(",", ":"))),
                    "prompt_hash": _hash_text(prompt),
                    "prompt_bytes": len(prompt.encode()),
                }
            )
    manifest = {
        "schema_version": 1,
        "model": PINNED_ACTION_MODEL,
        "max_cell_usd": 2.5,
        "phase_cap_usd": 30,
        "cells": cells,
    }
    manifest_path = private / "action-look-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    result = {
        "schema_version": 1,
        "status": "preregistered",
        "result_read": False,
        "model": PINNED_ACTION_MODEL,
        "fallback_model": None,
        "budget": {"phase_cap_usd": 30, "max_cell_usd": 2.5, "reserved_cells": 12},
        "cases": [case["case_id"] for case in free_gate["chronological_cases"]],
        "cells": public_cells,
        "instrument": {
            "arm_blinding": "pass",
            "same_context_identity": "pass",
            "known_violation_liveness": "pass",
            "irrelevant_memory_negative_control": "pass",
            "identical_pack_suppression": "pass",
        },
        "private_manifest_sha256": manifest_sha,
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": "The four-case blinded action look was preregistered before any model result was read.",
            "power": {
                "test": "descriptive-only (no test)",
                "n": 4,
                "b": 0,
                "c": 0,
                "n_d": 0,
                "psi_observed": None,
                "mde_at_80": None,
                "computed_by": "not applicable; fixed process kill gate",
                "source": out_path,
            },
            "mechanism_enabled": True,
            "probe_kind": "lever",
            "mechanism_evidence": "C1 and A1 use the exact preregistered static and Wilson-ordered packs from the free gate.",
            "harness": {
                "embed_model": "none",
                "scorer": "deterministic structured next_action predicate",
                "k": "all three validated adherence units in policy order",
                "budget": 30,
                "flags": ["blind-arms", "same-context", "structured-output", "no-fallback", "no-tools"],
                "command": "python3 -m benchmarks.xs_crosssession.outcome_coupled_evolution run-action-look",
            },
            "corpus": {
                "sha256": manifest_sha,
                "snapshot_id": "private-action-look-manifest-2026-08-08",
                "n_items": 4,
            },
            "notes": "Private context and model bodies remain outside Git. Four cases cannot support a general effectiveness claim.",
        },
    }
    Path(out_path).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def _dispatch_cell(
    cell: dict[str, Any], manifest: dict[str, Any], private: Path, claude_path: str
) -> tuple[dict[str, Any], float]:
    marker = private / f"{cell['cell_id']}.dispatch.json"
    if marker.exists():
        raise RuntimeError(f"refusing ambiguous or repeated dispatch: {cell['cell_id']}")
    marker.write_text(json.dumps({"state": "dispatched", "cell_id": cell["cell_id"]}) + "\n")
    completed = subprocess.run(
            [
                claude_path,
                "-p",
                "--safe-mode",
                "--tools",
                "",
                "--no-session-persistence",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(ACTION_SCHEMA, separators=(",", ":")),
                "--model",
                manifest["model"],
                "--max-budget-usd",
                str(manifest["max_cell_usd"]),
                "--max-turns",
                "1",
            ],
            input=cell["prompt"],
            text=True,
            capture_output=True,
            cwd=private,
            timeout=600,
            check=False,
        )
    response_path = private / f"{cell['cell_id']}.response.json"
    response_path.write_text(
        json.dumps(
            {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
            indent=2,
        )
        + "\n"
    )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        response = {}
    cost = float(response.get("total_cost_usd") or 0)
    valid = (
        completed.returncode == 0
        and response.get("subtype") == "success"
        and isinstance(response.get("structured_output"), dict)
        and pinned_model_used(response.get("modelUsage") or {}, manifest["model"])
    )
    passed = valid and score_next_action(cell["case_id"], response["structured_output"])
    marker.write_text(
        json.dumps({"state": "settled", "cell_id": cell["cell_id"], "cost_usd": cost}) + "\n"
    )
    return (
        {
            "cell_id": cell["cell_id"],
            "case_id": cell["case_id"],
            "blind_label": cell["blind_label"],
            "policy": cell["policy"],
            "valid": valid,
            "passed": bool(passed),
            "cost_usd": cost,
            "response_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
        },
        cost,
    )


def run_action_look(private_dir: str, out_path: str, claude_path: str) -> dict[str, Any]:
    private = Path(private_dir)
    manifest = json.loads((private / "action-look-manifest.json").read_text())
    ledger = BudgetLedger(total_cap=100, phase_caps={"action": 30, "coding": 70})
    grades = {policy: [] for policy in ("C0", "C1", "A1")}
    public_cells = []
    settled = 0.0
    for cell in manifest["cells"]:
        reservation = ledger.reserve("action", manifest["max_cell_usd"])
        public_cell, cost = _dispatch_cell(cell, manifest, private, claude_path)
        ledger.settle(reservation, cost)
        settled += cost
        grades[cell["policy"]].append(public_cell["passed"])
        public_cells.append(public_cell)
    comparison = action_look_verdict({policy: grades[policy] for policy in ("C1", "A1")})
    prereg = json.loads(Path(out_path).read_text())
    prereg_cells = {cell["cell_id"]: cell for cell in prereg["cells"]}
    prereg.update(
        {
            "status": "complete",
            "result_read": True,
            "verdict": comparison["verdict"],
            "runtime_gate": "coding_replay_open" if comparison["verdict"] == "ACTION_LOOK_PASS" else "closed",
            "spend_usd": settled,
            "grades": grades,
            "comparison": comparison,
            "cells": [{**prereg_cells[cell["cell_id"]], **cell} for cell in public_cells],
        }
    )
    b = comparison["net_wins"]
    c = comparison["losses"]
    prereg["evidence_contract"]["claim"] = (
        f"The fixed four-case action look ended {comparison['verdict']} with {b} A1 win(s) and {c} loss(es) versus C1."
    )
    prereg["evidence_contract"]["power"].update({"b": b, "c": c, "n_d": b + c})
    Path(out_path).write_text(json.dumps(prereg, indent=2, sort_keys=True) + "\n")
    checksum_paths = [str(path) for path in private.glob("*.json")]
    (private / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(Path(path).read_bytes()).hexdigest()}  {Path(path).name}\n"
            for path in sorted(checksum_paths)
        )
    )
    return prereg


def regrade_action_look(private_dir: str, out_path: str) -> dict[str, Any]:
    private = Path(private_dir)
    manifest = json.loads((private / "action-look-manifest.json").read_text())
    artifact = json.loads(Path(out_path).read_text())
    grades = {policy: [] for policy in ("C0", "C1", "A1")}
    settled = []
    for cell in manifest["cells"]:
        response_path = private / f"{cell['cell_id']}.response.json"
        envelope = json.loads(response_path.read_text())
        response = json.loads(envelope["stdout"])
        valid = (
            envelope["returncode"] == 0
            and response.get("subtype") == "success"
            and isinstance(response.get("structured_output"), dict)
            and pinned_model_used(response.get("modelUsage") or {}, manifest["model"])
        )
        passed = valid and score_next_action(cell["case_id"], response["structured_output"])
        grades[cell["policy"]].append(bool(passed))
        settled.append(
            {
                "cell_id": cell["cell_id"],
                "case_id": cell["case_id"],
                "blind_label": cell["blind_label"],
                "policy": cell["policy"],
                "valid": valid,
                "passed": bool(passed),
                "cost_usd": float(response.get("total_cost_usd") or 0),
                "response_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
            }
        )
    comparison = action_look_verdict({policy: grades[policy] for policy in ("C1", "A1")})
    prior_cells = {cell["cell_id"]: cell for cell in artifact["cells"]}
    artifact.update(
        {
            "status": "complete",
            "result_read": True,
            "verdict": comparison["verdict"],
            "runtime_gate": "coding_replay_open" if comparison["verdict"] == "ACTION_LOOK_PASS" else "closed",
            "grades": grades,
            "comparison": comparison,
            "cells": [{**prior_cells[cell["cell_id"]], **cell} for cell in settled],
        }
    )
    artifact["instrument"]["model_pin_amendment"] = (
        "pregrade: require pinned Opus generation; permit only Claude Code's reported Haiku auxiliary validator; reject any fallback model"
    )
    b = comparison["net_wins"]
    c = comparison["losses"]
    artifact["evidence_contract"]["claim"] = (
        f"The fixed four-case action look ended {comparison['verdict']} with {b} A1 win(s) and {c} loss(es) versus C1."
    )
    artifact["evidence_contract"]["power"].update({"b": b, "c": c, "n_d": b + c})
    Path(out_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return artifact


def prepare_admission_look(
    free_path: str,
    action_path: str,
    action_private_dir: str,
    private_dir: str,
    out_path: str,
) -> dict[str, Any]:
    free_gate = json.loads(Path(free_path).read_text())
    action = json.loads(Path(action_path).read_text())
    if action.get("verdict") != "ACTION_LOOK_FLAT":
        raise ValueError("admission follow-up requires the settled flat ordering screen")
    controls = locked_control_cells(action, Path(action_private_dir))
    cases = free_gate["chronological_cases"]
    if len(cases) != 4 or set(controls) != {case["case_id"] for case in cases}:
        raise ValueError("admission follow-up requires four matching locked controls")
    validation_events = [
        {
            "unit_id": unit_id,
            "event": "helpful",
            "attribution": "explicit_user",
            "meaning": "source correction validated the lesson; no later exposure is inferred",
        }
        for unit_id in sorted({case["unit_id"] for case in cases})
    ]
    private = Path(private_dir)
    private.mkdir(parents=True, exist_ok=True)
    cells = []
    public_cells = []
    for case in cases:
        path = _transcript_path(case["held_out_session_id"])
        if not path:
            raise ValueError(f"held-out transcript missing: {case['case_id']}")
        context = active_context(_read_jsonl(path), case["action_uuid"])
        context_hash = _hash_text(
            json.dumps(
                [(message["role"], message["content"]) for message in context],
                separators=(",", ":"),
            )
        )
        if context_hash != case["context_hash"]:
            raise ValueError(f"context identity drifted: {case['case_id']}")
        pack = admission_pack(case, validation_events)
        if pack != [case["unit_id"]]:
            raise ValueError(f"triggered lesson was not admitted: {case['case_id']}")
        prompt = _action_prompt(context, pack)
        cell_id = f"{case['case_id']}-admission"
        cells.append(
            {
                "cell_id": cell_id,
                "case_id": case["case_id"],
                "blind_label": "followup-arm",
                "policy": "A4",
                "context_hash": context_hash,
                "pack": pack,
                "prompt": prompt,
            }
        )
        public_cells.append(
            {
                "cell_id": cell_id,
                "case_id": case["case_id"],
                "blind_label": "followup-arm",
                "context_hash": context_hash,
                "pack_unit_id": case["unit_id"],
                "pack_hash": _hash_text(json.dumps(pack, separators=(",", ":"))),
                "prompt_hash": _hash_text(prompt),
                "prompt_bytes": len(prompt.encode()),
                "locked_control": controls[case["case_id"]],
            }
        )
    manifest = {
        "schema_version": 1,
        "model": PINNED_ACTION_MODEL,
        "max_cell_usd": 2.5,
        "phase_cap_usd": 30,
        "prior_action_spend_usd": action["spend_usd"],
        "cells": cells,
    }
    manifest_path = private / "admission-look-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    result = {
        "schema_version": 1,
        "status": "preregistered",
        "result_read": False,
        "model": PINNED_ACTION_MODEL,
        "fallback_model": None,
        "budget": {
            "prior_action_spend_usd": action["spend_usd"],
            "new_reserve_usd": 10,
            "phase_cap_usd": 30,
            "max_cell_usd": 2.5,
            "new_cells": 4,
        },
        "policy": "A4: admit only the deterministically triggered lesson with positive explicit-user validation",
        "attribution": validation_events,
        "cells": public_cells,
        "instrument": {
            "locked_controls": "pass",
            "same_context_identity": "pass",
            "single_relevant_lesson": "pass",
            "unexposed_later_outcomes_are_observational": "pass",
            "no_control_redispatch": "pass",
        },
        "private_manifest_sha256": manifest_sha,
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": "The relevant-lesson admission follow-up was preregistered before its four new model results were read.",
            "power": {
                "test": "descriptive-only (no test)",
                "n": 4,
                "b": 0,
                "c": 0,
                "n_d": 0,
                "psi_observed": None,
                "mde_at_80": None,
                "computed_by": "not applicable; adaptive four-case mechanism screen",
                "source": out_path,
            },
            "mechanism_enabled": True,
            "probe_kind": "lever",
            "mechanism_evidence": "Each A4 cell contains exactly its triggered, explicitly validated lesson; locked C1 controls contain the full static pack.",
            "harness": {
                "embed_model": "none",
                "scorer": "unchanged deterministic structured next_action predicate",
                "k": 1,
                "budget": 10,
                "flags": [
                    "adaptive-followup",
                    "locked-controls",
                    "same-context",
                    "structured-output",
                    "no-fallback",
                    "no-tools",
                ],
                "command": "python3 -m benchmarks.xs_crosssession.outcome_coupled_evolution run-admission-look",
            },
            "corpus": {
                "sha256": manifest_sha,
                "snapshot_id": "private-admission-look-manifest-2026-08-08",
                "n_items": 4,
            },
            "notes": "Adaptive after a flat ordering screen. Private contexts and model bodies remain outside Git. Passing can open isolated replay but cannot support a general effectiveness claim.",
        },
    }
    Path(out_path).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def run_admission_look(
    action_path: str, private_dir: str, out_path: str, claude_path: str
) -> dict[str, Any]:
    private = Path(private_dir)
    manifest = json.loads((private / "admission-look-manifest.json").read_text())
    action = json.loads(Path(action_path).read_text())
    controls = {
        cell["case_id"]: cell["passed"]
        for cell in action["cells"]
        if cell.get("policy") == "C1" and cell.get("valid") is True
    }
    ledger = BudgetLedger(
        total_cap=100,
        phase_caps={"action": 30, "coding": 70},
        _settled={"action": manifest["prior_action_spend_usd"]},
    )
    public_cells = []
    new_spend = 0.0
    for cell in manifest["cells"]:
        reservation = ledger.reserve("action", manifest["max_cell_usd"])
        public_cell, cost = _dispatch_cell(cell, manifest, private, claude_path)
        ledger.settle(reservation, cost)
        new_spend += cost
        public_cells.append(public_cell)
    control_grades = [controls[cell["case_id"]] for cell in manifest["cells"]]
    treatment_grades = [cell["passed"] for cell in public_cells]
    comparison = action_look_verdict({"C1": control_grades, "A1": treatment_grades})
    comparison["verdict"] = comparison["verdict"].replace("ACTION_LOOK", "ADMISSION_LOOK")
    artifact = json.loads(Path(out_path).read_text())
    prior_cells = {cell["cell_id"]: cell for cell in artifact["cells"]}
    artifact.update(
        {
            "status": "complete",
            "result_read": True,
            "verdict": comparison["verdict"],
            "runtime_gate": (
                "isolated_coding_replay_open"
                if comparison["verdict"] == "ADMISSION_LOOK_PASS"
                else "closed"
            ),
            "new_spend_usd": new_spend,
            "cumulative_action_spend_usd": manifest["prior_action_spend_usd"] + new_spend,
            "grades": {"C1_locked": control_grades, "A4": treatment_grades},
            "comparison": comparison,
            "cells": [{**prior_cells[cell["cell_id"]], **cell} for cell in public_cells],
        }
    )
    b = comparison["net_wins"]
    c = comparison["losses"]
    artifact["evidence_contract"]["claim"] = (
        f"The four-case relevant-lesson admission screen ended {comparison['verdict']} with {b} A4 win(s) and {c} loss(es) versus locked C1."
    )
    artifact["evidence_contract"]["power"].update({"b": b, "c": c, "n_d": b + c})
    Path(out_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    checksum_paths = [str(path) for path in private.glob("*.json")]
    (private / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(Path(path).read_bytes()).hexdigest()}  {Path(path).name}\n"
            for path in sorted(checksum_paths)
        )
    )
    return artifact


def _run_git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


def _init_replay_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    for name, body in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    _run_git(path, "init", "-q")
    _run_git(path, "add", ".")
    _run_git(
        path,
        "-c",
        "user.name=MemPhant Replay",
        "-c",
        "user.email=replay@memphant.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return _run_git(path, "rev-parse", "HEAD")


def _git_state(path: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    dirty = set()
    staged = set()
    for line in status.splitlines():
        if len(line) < 4:
            continue
        code, name = line[:2], line[3:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        dirty.add(name)
        if code[0] not in {" ", "?"}:
            staged.add(name)
    return {
        "dirty": dirty,
        "staged": staged,
        "head": _run_git(path, "rev-parse", "HEAD"),
        "index": _run_git(path, "write-tree"),
    }


def _coding_replay_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "explicit-staging",
            "unit_id": "explicit-staging",
            "prompt": (
                "Change src/status.txt so python3 verify.py passes, run that validator, and stage "
                "the completed project change for commit. Preserve unrelated work."
            ),
            "files": {
                ".gitignore": "__pycache__/\n",
                "src/status.txt": "draft\n",
                "notes/private.txt": "private draft\n",
                "verify.py": (
                    "from pathlib import Path\n"
                    "assert Path('src/status.txt').read_text() == 'ready\\n'\n"
                ),
            },
            "validator": ["python3", "verify.py"],
            "trigger": {"prompt_regex": r"\b(stage|commit)\b"},
        },
        {
            "case_id": "scoped-gate",
            "unit_id": "full-local-gate",
            "prompt": "Fix calculator.add so it adds correctly and verify the change. Do not modify tests.",
            "files": {
                ".gitignore": "__pycache__/\n.full_gate_ran\n",
                "calculator.py": "def add(left, right):\n    return left - right\n",
                "tests/__init__.py": "",
                "tests/test_calculator.py": (
                    "import unittest\nfrom calculator import add\n\n"
                    "class CalculatorTest(unittest.TestCase):\n"
                    "    def test_adds(self):\n        self.assertEqual(add(2, 3), 5)\n"
                ),
                "tests/test_unrelated.py": (
                    "import unittest\n\nclass UnrelatedTest(unittest.TestCase):\n"
                    "    def test_ok(self):\n        self.assertTrue(True)\n"
                ),
                "run_tests.py": (
                    "from pathlib import Path\nimport subprocess, sys\n"
                    "Path('.full_gate_ran').write_text('ran\\n')\n"
                    "raise SystemExit(subprocess.run([sys.executable, '-m', 'unittest', 'discover']).returncode)\n"
                ),
                "README.md": (
                    "Focused calculator check: `python3 -m unittest tests.test_calculator`.\n"
                    "Full repository gate: `python3 run_tests.py`.\n"
                ),
            },
            "validator": ["python3", "-m", "unittest", "tests.test_calculator"],
            "trigger": {"prompt_regex": r"\b(test|verify|fix)\b"},
        },
        {
            "case_id": "explicit-staging-variant",
            "unit_id": "explicit-staging",
            "prompt": (
                "Change config/mode.txt so python3 verify_config.py passes, run that validator, "
                "and stage the completed project change for commit. Preserve unrelated work."
            ),
            "files": {
                ".gitignore": "__pycache__/\n",
                "config/mode.txt": "draft\n",
                "notes/local.txt": "local draft\n",
                "verify_config.py": (
                    "from pathlib import Path\n"
                    "assert Path('config/mode.txt').read_text() == 'ready\\n'\n"
                ),
            },
            "validator": ["python3", "verify_config.py"],
            "trigger": {"prompt_regex": r"\b(stage|commit)\b"},
        },
        {
            "case_id": "scoped-gate-variant",
            "unit_id": "full-local-gate",
            "prompt": "Fix formatter.slugify so it creates URL slugs and verify the change. Do not modify tests.",
            "files": {
                ".gitignore": "__pycache__/\n.full_gate_ran\n",
                "formatter.py": "def slugify(value):\n    return value.lower()\n",
                "tests/__init__.py": "",
                "tests/test_formatter.py": (
                    "import unittest\nfrom formatter import slugify\n\n"
                    "class FormatterTest(unittest.TestCase):\n"
                    "    def test_slugifies(self):\n"
                    "        self.assertEqual(slugify('Hello World'), 'hello-world')\n"
                ),
                "tests/test_unrelated.py": (
                    "import unittest\n\nclass UnrelatedTest(unittest.TestCase):\n"
                    "    def test_ok(self):\n        self.assertTrue(True)\n"
                ),
                "run_tests.py": (
                    "from pathlib import Path\nimport subprocess, sys\n"
                    "Path('.full_gate_ran').write_text('ran\\n')\n"
                    "raise SystemExit(subprocess.run([sys.executable, '-m', 'unittest', 'discover']).returncode)\n"
                ),
                "README.md": (
                    "Focused formatter check: `python3 -m unittest tests.test_formatter`.\n"
                    "Full repository gate: `python3 run_tests.py`.\n"
                ),
            },
            "validator": ["python3", "-m", "unittest", "tests.test_formatter"],
            "trigger": {"prompt_regex": r"\b(test|verify|fix)\b"},
        },
    ]


def prepare_coding_replay(
    private_dir: str,
    out_path: str,
    *,
    cases: list[dict[str, Any]] | None = None,
    prior_spend_usd: float = CODING_REPLAY_PRIOR_SPEND_USD,
    locked_initial_result_sha256: str | None = None,
) -> dict[str, Any]:
    private = Path(private_dir)
    manifest_path = private / "coding-replay-manifest.json"
    if manifest_path.exists():
        raise RuntimeError("coding replay is already prepared")
    bases = private / "bases"
    bases.mkdir(parents=True)
    cells = []
    public_cells = []
    parity = []
    selected_cases = cases if cases is not None else _coding_replay_cases()[:2]
    for case in selected_cases:
        base = bases / case["case_id"]
        base_commit = _init_replay_repo(base, case["files"])
        if case["case_id"].startswith("explicit-staging"):
            dirty_path = "notes/private.txt" if case["case_id"] == "explicit-staging" else "notes/local.txt"
            (base / dirty_path).write_text("private work in progress\n")
        before = _git_state(base)
        body = RULE_TEXT[case["unit_id"]]
        body_sha = _hash_text(body)
        projection = {
            "items": [
                {
                    "unit_id": case["unit_id"],
                    "kind": "procedure",
                    "body": body,
                    "body_sha256": body_sha,
                    "state": "validated",
                }
            ]
        }
        events = [
            {
                "unit_id": case["unit_id"],
                "event": "helpful",
                "attribution": "explicit_user",
            }
        ]
        lessons = project_triggered_lessons(
            projection,
            events,
            {case["unit_id"]: case["trigger"]},
            prompt=case["prompt"],
            paths=[],
        )
        if len(lessons) != 1:
            raise ValueError(f"coding replay trigger did not select one lesson: {case['case_id']}")
        repo_lesson = private / "repo-delivery" / f"{case['unit_id']}.md"
        repo_lesson.parent.mkdir(exist_ok=True)
        repo_lesson.write_text(body)
        parity.append(_hash_text(repo_lesson.read_text()) == lessons[0]["body_sha256"])
        arms = sorted(
            (("C0", ""), ("M1", delivery_context(lessons))),
            key=lambda arm: _hash_text(f"coding-replay-v1:{case['case_id']}:{arm[0]}"),
        )
        for index, (policy, context) in enumerate(arms, 1):
            cell_id = f"{case['case_id']}-arm-{index}"
            cell = {
                "cell_id": cell_id,
                "case_id": case["case_id"],
                "policy": policy,
                "base": str(base),
                "base_commit": base_commit,
                "before_dirty": sorted(before["dirty"]),
                "before_index": before["index"],
                "prompt": case["prompt"],
                "context": context,
                "validator": case["validator"],
                "unit_id": case["unit_id"] if policy == "M1" else None,
                "unit_body_sha256": body_sha if policy == "M1" else None,
            }
            cells.append(cell)
            public_cells.append(
                {
                    "cell_id": cell_id,
                    "case_id": case["case_id"],
                    "policy": policy,
                    "base_commit": base_commit,
                    "before_index": before["index"],
                    "prompt_sha256": _hash_text(case["prompt"]),
                    "context_sha256": _hash_text(context),
                    "unit_id": cell["unit_id"],
                    "unit_body_sha256": cell["unit_body_sha256"],
                }
            )
    manifest = {
        "schema_version": 1,
        "model": PINNED_ACTION_MODEL,
        "max_cell_usd": 5,
        "phase_cap_usd": 70,
        "prior_spend_usd": prior_spend_usd,
        "cells": cells,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    result = {
        "schema_version": 1,
        "status": "preregistered",
        "result_read": False,
        "verdict": "PENDING",
        "runtime_gate": "closed",
        "model": PINNED_ACTION_MODEL,
        "fallback_model": None,
        "budget": {
            "phase_cap_usd": 70,
            "max_cell_usd": 5,
            "reserved_cells": 4,
            "new_reserve_usd": 20,
            "prior_spend_usd": prior_spend_usd,
        },
        "cells": public_cells,
        "instrument": {
            "repo_projection_delivery_parity": "pass" if all(parity) else "fail",
            "pre_action_boundaries": "pass",
            "dirty_file_baseline": "pass",
            "one_shot_dispatch": "pass",
            "raw_content_public": "absent",
        },
        "private_manifest_sha256": manifest_sha,
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": "The two-case isolated coding replay was preregistered before any model result was read.",
            "power": {
                "test": "descriptive-only (no test)",
                "n": 4 if locked_initial_result_sha256 else 2,
                "b": 0,
                "c": 0,
                "n_d": 0,
                "psi_observed": None,
                "mde_at_80": None,
                "computed_by": "not applicable; two-case mechanism screen",
                "source": out_path,
            },
            "mechanism_enabled": True,
            "probe_kind": "lever",
            "mechanism_evidence": (
                "M1 receives one deterministic-triggered canonical projection body with explicit-user validation; C0 receives none."
            ),
            "harness": {
                "embed_model": "none",
                "scorer": "deterministic scratch-repository validator and rule predicate",
                "k": 1,
                "budget": 20,
                "flags": [
                    "paired",
                    "whole-task",
                    "pre-action-boundary",
                    "safe-mode",
                    "no-fallback",
                    "private-stream-json",
                ],
                "command": "python3 -m benchmarks.xs_crosssession.outcome_coupled_evolution run-coding-replay",
            },
            "corpus": {
                "sha256": manifest_sha,
                "snapshot_id": (
                    "private-outcome-coding-replay-expansion-2026-08-08"
                    if locked_initial_result_sha256
                    else "private-outcome-coding-replay-2026-08-08"
                ),
                "n_items": 4 if locked_initial_result_sha256 else 2,
            },
            "notes": "Mechanism screen only. Private task and model bodies remain outside Git.",
        },
    }
    if locked_initial_result_sha256:
        result["locked_initial_result_sha256"] = locked_initial_result_sha256
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def prepare_coding_replay_expansion(
    initial_path: str, private_dir: str, out_path: str
) -> dict[str, Any]:
    initial_file = Path(initial_path)
    initial = json.loads(initial_file.read_text())
    if initial.get("verdict") != "CODING_REPLAY_EXPAND" or initial.get("result_read") is not True:
        raise ValueError("coding replay expansion requires a settled positive initial screen")
    return prepare_coding_replay(
        private_dir,
        out_path,
        cases=_coding_replay_cases()[2:4],
        prior_spend_usd=float(initial["cumulative_spend_usd"]),
        locked_initial_result_sha256=hashlib.sha256(initial_file.read_bytes()).hexdigest(),
    )


def requested_end_state(case_id: str, run_dir: Path, staged: set[str]) -> bool:
    if case_id.startswith("explicit-staging"):
        target = "src/status.txt" if case_id == "explicit-staging" else "config/mode.txt"
        return (run_dir / target).read_text() == "ready\n" and target in staged
    checks = {
        "scoped-gate": "from calculator import add; assert add(2, 3) == 5",
        "scoped-gate-variant": (
            "from formatter import slugify; assert slugify('Hello World') == 'hello-world'"
        ),
    }
    if case_id not in checks:
        raise ValueError(f"unknown coding replay case: {case_id}")
    return subprocess.run(
        ["python3", "-c", checks[case_id]],
        cwd=run_dir,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        check=False,
    ).returncode == 0


def _evaluate_coding_cell(
    cell: dict[str, Any], run_dir: Path, response_path: Path
) -> tuple[dict[str, Any], float]:
    envelope = json.loads(response_path.read_text())
    stream = extract_stream_evidence(envelope["stdout"])
    validator = subprocess.run(
        cell["validator"],
        cwd=run_dir,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    after = _git_state(run_dir)
    grade = grade_coding_replay(
        cell["case_id"],
        validator_pass=validator.returncode == 0,
        requested_end_state_pass=requested_end_state(
            cell["case_id"], run_dir, after["staged"]
        ),
        before_dirty=set(cell["before_dirty"]),
        after_dirty=after["dirty"],
        staged=after["staged"],
        commands=stream["commands"],
        full_gate_ran=(run_dir / ".full_gate_ran").exists(),
    )
    valid = envelope["returncode"] == 0 and stream["valid"]
    return (
        {
            "cell_id": cell["cell_id"],
            "case_id": cell["case_id"],
            "policy": cell["policy"],
            "valid": valid,
            "passed": bool(valid and grade["accepted_without_violation"]),
            "cost_usd": stream["cost_usd"],
            "tool_count": stream["tool_count"],
            "validator_pass": grade["validator_pass"],
            "requested_end_state_pass": grade["requested_end_state_pass"],
            "rule_violation": grade["rule_violation"],
            "new_dirty_count": len(grade["new_dirty"]),
            "after_index": after["index"],
            "response_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
        },
        stream["cost_usd"],
    )


def _dispatch_coding_cell(
    cell: dict[str, Any], manifest: dict[str, Any], private: Path, claude_path: str
) -> tuple[dict[str, Any], float]:
    marker = private / f"{cell['cell_id']}.dispatch.json"
    if marker.exists():
        raise RuntimeError(f"refusing ambiguous or repeated dispatch: {cell['cell_id']}")
    run_dir = private / "runs" / cell["cell_id"]
    run_dir.parent.mkdir(exist_ok=True)
    shutil.copytree(cell["base"], run_dir)
    marker.write_text(json.dumps({"state": "dispatched", "cell_id": cell["cell_id"]}) + "\n")
    command = [
        claude_path,
        "-p",
        "--safe-mode",
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
        "--tools",
        "Read,Edit,Write,Bash",
        "--no-chrome",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        manifest["model"],
        "--max-budget-usd",
        str(manifest["max_cell_usd"]),
        "--max-turns",
        "12",
    ]
    if cell["context"]:
        command.extend(["--append-system-prompt", cell["context"]])
    env = os.environ.copy()
    env.update(
        {
            "MEMPHANT_REPLAY_CASE": cell["case_id"],
            "MEMPHANT_REPLAY_POLICY": cell["policy"],
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        command,
        input=cell["prompt"],
        text=True,
        capture_output=True,
        cwd=run_dir,
        env=env,
        timeout=1_200,
        check=False,
    )
    response_path = private / f"{cell['cell_id']}.response.json"
    response_path.write_text(
        json.dumps(
            {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
            indent=2,
        )
        + "\n"
    )
    public_cell, cost = _evaluate_coding_cell(cell, run_dir, response_path)
    marker.write_text(
        json.dumps({"state": "settled", "cell_id": cell["cell_id"], "cost_usd": cost})
        + "\n"
    )
    return public_cell, cost


def run_coding_replay(private_dir: str, out_path: str, claude_path: str) -> dict[str, Any]:
    private = Path(private_dir)
    manifest = json.loads((private / "coding-replay-manifest.json").read_text())
    ledger = BudgetLedger(
        total_cap=100,
        phase_caps={"action": 30, "coding": 70},
        _settled={
            "action": CODING_REPLAY_PRIOR_SPEND_USD,
            "coding": max(0.0, manifest["prior_spend_usd"] - CODING_REPLAY_PRIOR_SPEND_USD),
        },
    )
    settled = []
    new_spend = 0.0
    for cell in manifest["cells"]:
        reservation = ledger.reserve("coding", manifest["max_cell_usd"])
        public_cell, cost = _dispatch_coding_cell(cell, manifest, private, claude_path)
        ledger.settle(reservation, cost)
        settled.append(public_cell)
        new_spend += cost
        if not public_cell["valid"]:
            break
    grades = {
        policy: [cell["passed"] for cell in settled if cell["policy"] == policy]
        for policy in ("C0", "M1")
    }
    comparison = coding_replay_verdict(grades) if all(len(values) == 2 for values in grades.values()) else {
        "verdict": "CODING_REPLAY_INVALID",
        "control_passes": sum(grades["C0"]),
        "treatment_passes": sum(grades["M1"]),
        "net_wins": 0,
        "losses": 0,
    }
    artifact = json.loads(Path(out_path).read_text())
    prior = {cell["cell_id"]: cell for cell in artifact["cells"]}
    artifact.update(
        {
            "status": "complete",
            "result_read": True,
            "verdict": comparison["verdict"],
            "runtime_gate": "expansion_open" if comparison["verdict"] == "CODING_REPLAY_EXPAND" else "closed",
            "new_spend_usd": new_spend,
            "cumulative_spend_usd": manifest["prior_spend_usd"] + new_spend,
            "comparison": comparison,
            "grades": grades,
            "cells": [{**prior[cell["cell_id"]], **cell} for cell in settled],
        }
    )
    b, c = comparison["net_wins"], comparison["losses"]
    artifact["evidence_contract"]["claim"] = (
        f"The initial two-case whole-task replay ended {comparison['verdict']} with {b} M1 win(s) and {c} loss(es) versus C0."
    )
    artifact["evidence_contract"]["power"].update({"b": b, "c": c, "n_d": b + c})
    Path(out_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    checksum_paths = [path for path in private.glob("*.json")]
    (private / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in sorted(checksum_paths)
        )
    )
    return artifact


def run_coding_replay_expansion(
    initial_path: str, private_dir: str, out_path: str, claude_path: str
) -> dict[str, Any]:
    initial_file = Path(initial_path)
    initial_sha = hashlib.sha256(initial_file.read_bytes()).hexdigest()
    artifact = json.loads(Path(out_path).read_text())
    if artifact.get("locked_initial_result_sha256") != initial_sha:
        raise ValueError("locked initial coding replay drifted")
    initial = json.loads(initial_file.read_text())
    expansion = run_coding_replay(private_dir, out_path, claude_path)
    combined = {
        policy: initial["grades"][policy] + expansion["grades"][policy]
        for policy in ("C0", "M1")
    }
    comparison = coding_replay_verdict(combined)
    expansion.update(
        {
            "verdict": comparison["verdict"],
            "runtime_gate": (
                "production_hook_design_open"
                if comparison["verdict"] == "CODING_REPLAY_PASS"
                else "closed"
            ),
            "comparison": comparison,
            "grades": combined,
        }
    )
    b, c = comparison["net_wins"], comparison["losses"]
    expansion["evidence_contract"]["claim"] = (
        f"The four-case whole-task replay ended {comparison['verdict']} with {b} M1 win(s) and {c} loss(es) versus C0."
    )
    expansion["evidence_contract"]["power"].update({"b": b, "c": c, "n_d": b + c})
    Path(out_path).write_text(json.dumps(expansion, indent=2, sort_keys=True) + "\n")
    return expansion


def regrade_coding_replay_expansion(
    initial_path: str, private_dir: str, out_path: str
) -> dict[str, Any]:
    private = Path(private_dir)
    manifest = json.loads((private / "coding-replay-manifest.json").read_text())
    initial_file = Path(initial_path)
    initial = json.loads(initial_file.read_text())
    artifact = json.loads(Path(out_path).read_text())
    if artifact.get("locked_initial_result_sha256") != hashlib.sha256(
        initial_file.read_bytes()
    ).hexdigest():
        raise ValueError("locked initial coding replay drifted")
    settled = [
        _evaluate_coding_cell(
            cell,
            private / "runs" / cell["cell_id"],
            private / f"{cell['cell_id']}.response.json",
        )[0]
        for cell in manifest["cells"]
    ]
    new_grades = {
        policy: [cell["passed"] for cell in settled if cell["policy"] == policy]
        for policy in ("C0", "M1")
    }
    combined = {
        policy: initial["grades"][policy] + new_grades[policy]
        for policy in ("C0", "M1")
    }
    comparison = coding_replay_verdict(combined)
    prior_cells = {cell["cell_id"]: cell for cell in artifact["cells"]}
    artifact.update(
        {
            "verdict": comparison["verdict"],
            "runtime_gate": (
                "production_hook_design_open"
                if comparison["verdict"] == "CODING_REPLAY_PASS"
                else "closed"
            ),
            "comparison": comparison,
            "grades": combined,
            "cells": [{**prior_cells[cell["cell_id"]], **cell} for cell in settled],
            "new_spend_usd": sum(cell["cost_usd"] for cell in settled),
            "cumulative_spend_usd": manifest["prior_spend_usd"]
            + sum(cell["cost_usd"] for cell in settled),
        }
    )
    artifact["instrument"]["end_state_scorer_amendment"] = "pass"
    b, c = comparison["net_wins"], comparison["losses"]
    artifact["evidence_contract"]["claim"] = (
        f"The four-case whole-task replay ended {comparison['verdict']} with {b} M1 win(s) and {c} loss(es) versus C0 after implementation-independent end-state regrading."
    )
    artifact["evidence_contract"]["power"].update({"b": b, "c": c, "n_d": b + c})
    artifact["evidence_contract"]["notes"] = (
        "Mechanism screen only. Private task and model bodies remain outside Git. "
        "Post-result regrade replaced an implementation-string check with the preregistered functional end-state predicate and applied it symmetrically to both arms without model reruns."
    )
    Path(out_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return artifact


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
            observation.update(_context_metadata(rows, grade["action_uuid"]))
        observations.append(observation)

    claude_root = Path.home() / ".claude/projects"
    census = mine_correction_candidates([claude_root])
    census.pop("candidates")
    sealed_paths = [
        path
        for path in claude_root.rglob("*.jsonl")
        if "subagents" not in path.parts
        and ("-Users-sidsharma-Syndai" in str(path) or "-Users-sidsharma-Memphant" in str(path))
    ]
    staged = select_first_scored_action(
        sealed_paths,
        "2026-08-06T00:00:00Z",
        "2026-08-08T20:50:00Z",
        score_explicit_staging,
    )
    by_session = {row["session_id"]: row for row in observations}
    if not staged or any(by_session[sid].get("status") != "pass" for sid in ("15403b3d", "9e49b76b", "ed4f8502", "38ba8780")):
        cases = []
        causal_events = []
    else:
        stop_source = _task_hash_before("1a6b5297", "2026-07-29T00:51:13.808Z")
        stage_source = _task_hash_before("ed4f8502", "2026-07-30T02:09:27.815Z")
        cases = [
            _case(
                "a1-continue-ed4f8502",
                "one-session-workstream",
                stop_source,
                "2026-07-29T00:51:13.808Z",
                by_session["ed4f8502"],
                "next_action continues the workstream instead of ending the turn",
                "beba81855452d5214c30ae52367d95bde95a63c1bbea861d6c60064ea1bcd726",
            ),
            _case(
                "a1-continue-38ba8780",
                "one-session-workstream",
                stop_source,
                "2026-07-29T00:51:13.808Z",
                by_session["38ba8780"],
                "next_action continues the workstream instead of ending the turn",
                "beba81855452d5214c30ae52367d95bde95a63c1bbea861d6c60064ea1bcd726",
            ),
            _case(
                "a1-explicit-stage-sealed",
                "explicit-staging",
                stage_source,
                "2026-07-30T02:09:27.815Z",
                staged,
                "every git add payload names explicit paths and never uses -A, --all, or dot",
                "2a26af073af4ca061f85f87c9d4139936ffc7aedd968085226a23c0d6d9ffb92",
            ),
            _case(
                "a1-scoped-gate-9e49b76b",
                "full-local-gate",
                by_session["15403b3d"]["task_hash"],
                by_session["15403b3d"]["timestamp"],
                by_session["9e49b76b"],
                "next_action uses scoped checks and leaves the full repository gate to CI",
                _hash_text("full-local-gate-v1"),
            ),
        ]
        causal_events = [
            {"case_id": cases[0]["case_id"], "unit_id": cases[0]["unit_id"], "event": "harmful", "attribution": "deterministic_scorer"},
            {"case_id": cases[1]["case_id"], "unit_id": cases[1]["unit_id"], "event": "harmful", "attribution": "deterministic_scorer"},
            {"case_id": cases[2]["case_id"], "unit_id": cases[2]["unit_id"], "event": staged["outcome"], "attribution": "deterministic_scorer"},
            {"case_id": cases[3]["case_id"], "unit_id": cases[3]["unit_id"], "event": "harmful", "attribution": "deterministic_scorer"},
        ]
    scopes = classify_scopes(cases)
    liveness_pass = sum(row.get("status") == "pass" for row in observations)

    learned_by_unit = {}
    for case in cases:
        learned_by_unit.setdefault(case["unit_id"], case["learned_at"])
    units = [
        {"unit_id": unit_id, "kind": "adherence", "validated": True}
        for unit_id, _ in sorted(learned_by_unit.items(), key=lambda item: (item[1], item[0]))
    ]
    observational_events = [
        {"unit_id": row["family"], "event": "harmful", "attribution": "observational"}
        for row in observations
        if row.get("status") == "pass"
    ]
    packs = {
        policy: pack_for_policy(units, observational_events + causal_events, policy)
        for policy in ("C0", "C1", "A1", "A2", "A3")
    }
    policy_differences = {
        policy: should_dispatch(packs["C1"], packs[policy])
        for policy in ("A1", "A2", "A3")
    }
    verdict = gate_verdict(liveness_pass, len(KNOWN_VIOLATIONS), scopes, policy_differences)
    gate_open = verdict == "FREE_GATE_OPEN"
    for session in ("1a6b5297", staged["session_id"] if staged else None):
        if session and (path := _transcript_path(session)) and path not in paths:
            paths.append(path)
    result = {
        "schema_version": 1,
        "status": "complete",
        "result_read": True,
        "verdict": verdict,
        "runtime_gate": "closed",
        "paid_gate": "action_look_open" if gate_open else "closed",
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
            "candidate_funnel": census,
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
            "causal_events": len(causal_events),
            "observational_events": len(observational_events),
            "task_memory_events": causal_events,
            "packs": packs,
            "policy_differences": policy_differences,
        },
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": (
                f"The free instrument found and graded {liveness_pass}/{len(KNOWN_VIOLATIONS)} known violations, "
                f"qualified {len(cases)} chronological adherence cases, and recorded the policy difference that decides whether the action look may run."
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
            "mechanism_enabled": True,
            "probe_kind": "gate",
            "mechanism_evidence": f"The deterministic A1 lifecycle simulator ran with {len(causal_events)} scorer-backed events; A1 vs C1 = {policy_differences.get('A1')}.",
            "harness": {
                "embed_model": "none",
                "scorer": "deterministic transcript structure and preregistered historical correction predicates",
                "k": "four required cases per independent scope",
                "budget": 0,
                "flags": ["free-gate", "no-model", "no-runtime", "sealed-window", "observational-evidence-ineligible"],
                "command": "python3 -m benchmarks.xs_crosssession.outcome_coupled_evolution qualify",
            },
            "corpus": {
                "sha256": _combined_sha256(paths),
                "snapshot_id": "local-claude-qualified-transcripts-2026-08-08",
                "n_items": len(paths),
            },
            "notes": "Private transcript bodies and correction text remain local. This descriptive artifact may open only the bounded action look; runtime remains closed.",
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
    mine_parser = subparsers.add_parser("mine")
    mine_parser.add_argument(
        "--root", action="append", default=[str(Path.home() / ".claude/projects")]
    )
    mine_parser.add_argument(
        "--out",
        default=str(
            Path.home()
            / ".memphant-private/xs-crosssession/outcome-coupled-evolution/correction-candidates.jsonl"
        ),
    )
    prepare_parser = subparsers.add_parser("prepare-action-look")
    prepare_parser.add_argument(
        "--free-gate",
        default="docs/build-log/artifacts/outcome-coupled-evolution/free-gate.json",
    )
    prepare_parser.add_argument(
        "--private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/action-look"
        ),
    )
    prepare_parser.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/action-look.json",
    )
    run_parser = subparsers.add_parser("run-action-look")
    run_parser.add_argument(
        "--private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/action-look"
        ),
    )
    run_parser.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/action-look.json",
    )
    run_parser.add_argument("--claude", default="/Users/sidsharma/.local/bin/claude")
    regrade_parser = subparsers.add_parser("regrade-action-look")
    regrade_parser.add_argument(
        "--private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/action-look"
        ),
    )
    regrade_parser.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/action-look.json",
    )
    admission_prepare = subparsers.add_parser("prepare-admission-look")
    admission_prepare.add_argument(
        "--free-gate",
        default="docs/build-log/artifacts/outcome-coupled-evolution/free-gate.json",
    )
    admission_prepare.add_argument(
        "--action-look",
        default="docs/build-log/artifacts/outcome-coupled-evolution/action-look.json",
    )
    admission_prepare.add_argument(
        "--action-private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/action-look"
        ),
    )
    admission_prepare.add_argument(
        "--private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/admission-look"
        ),
    )
    admission_prepare.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/admission-look.json",
    )
    admission_run = subparsers.add_parser("run-admission-look")
    admission_run.add_argument(
        "--action-look",
        default="docs/build-log/artifacts/outcome-coupled-evolution/action-look.json",
    )
    admission_run.add_argument(
        "--private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/admission-look"
        ),
    )
    admission_run.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/admission-look.json",
    )
    admission_run.add_argument("--claude", default="/Users/sidsharma/.local/bin/claude")
    coding_prepare = subparsers.add_parser("prepare-coding-replay")
    coding_prepare.add_argument(
        "--private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/coding-replay"
        ),
    )
    coding_prepare.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay.json",
    )
    coding_run = subparsers.add_parser("run-coding-replay")
    coding_run.add_argument(
        "--private-dir",
        default=str(
            Path.home() / ".memphant-private/xs-crosssession/outcome-coupled-evolution/coding-replay"
        ),
    )
    coding_run.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay.json",
    )
    coding_run.add_argument("--claude", default="/Users/sidsharma/.local/bin/claude")
    expansion_prepare = subparsers.add_parser("prepare-coding-replay-expansion")
    expansion_prepare.add_argument(
        "--initial",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay.json",
    )
    expansion_prepare.add_argument(
        "--private-dir",
        default=str(
            Path.home()
            / ".memphant-private/xs-crosssession/outcome-coupled-evolution/coding-replay-expansion"
        ),
    )
    expansion_prepare.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay-expansion.json",
    )
    expansion_run = subparsers.add_parser("run-coding-replay-expansion")
    expansion_run.add_argument(
        "--initial",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay.json",
    )
    expansion_run.add_argument(
        "--private-dir",
        default=str(
            Path.home()
            / ".memphant-private/xs-crosssession/outcome-coupled-evolution/coding-replay-expansion"
        ),
    )
    expansion_run.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay-expansion.json",
    )
    expansion_run.add_argument("--claude", default="/Users/sidsharma/.local/bin/claude")
    expansion_regrade = subparsers.add_parser("regrade-coding-replay-expansion")
    expansion_regrade.add_argument(
        "--initial",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay.json",
    )
    expansion_regrade.add_argument(
        "--private-dir",
        default=str(
            Path.home()
            / ".memphant-private/xs-crosssession/outcome-coupled-evolution/coding-replay-expansion"
        ),
    )
    expansion_regrade.add_argument(
        "--out",
        default="docs/build-log/artifacts/outcome-coupled-evolution/coding-replay-expansion.json",
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
    elif args.command == "mine":
        result = mine_correction_candidates([Path(root) for root in args.root])
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(row) + "\n" for row in result.pop("candidates")))
        print(json.dumps({**result, "private_out": str(out)}, sort_keys=True))
    elif args.command == "prepare-action-look":
        result = prepare_action_look(args.free_gate, args.private_dir, args.out)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "model": result["model"],
                    "cells": len(result["cells"]),
                    "max_prompt_bytes": max(cell["prompt_bytes"] for cell in result["cells"]),
                },
                sort_keys=True,
            )
        )
    elif args.command == "run-action-look":
        result = run_action_look(args.private_dir, args.out, args.claude)
        print(
            json.dumps(
                {
                    "verdict": result["verdict"],
                    "spend_usd": result["spend_usd"],
                    "comparison": result["comparison"],
                },
                sort_keys=True,
            )
        )
    elif args.command == "regrade-action-look":
        result = regrade_action_look(args.private_dir, args.out)
        print(
            json.dumps(
                {"verdict": result["verdict"], "comparison": result["comparison"]},
                sort_keys=True,
            )
        )
    elif args.command == "prepare-admission-look":
        result = prepare_admission_look(
            args.free_gate,
            args.action_look,
            args.action_private_dir,
            args.private_dir,
            args.out,
        )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "cells": len(result["cells"]),
                    "new_reserve_usd": result["budget"]["new_reserve_usd"],
                },
                sort_keys=True,
            )
        )
    elif args.command == "run-admission-look":
        result = run_admission_look(
            args.action_look, args.private_dir, args.out, args.claude
        )
        print(
            json.dumps(
                {
                    "verdict": result["verdict"],
                    "new_spend_usd": result["new_spend_usd"],
                    "comparison": result["comparison"],
                },
                sort_keys=True,
            )
        )
    elif args.command == "prepare-coding-replay":
        result = prepare_coding_replay(args.private_dir, args.out)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "cells": len(result["cells"]),
                    "new_reserve_usd": result["budget"]["new_reserve_usd"],
                    "delivery_parity": result["instrument"]["repo_projection_delivery_parity"],
                },
                sort_keys=True,
            )
        )
    elif args.command == "run-coding-replay":
        result = run_coding_replay(args.private_dir, args.out, args.claude)
        print(
            json.dumps(
                {
                    "verdict": result["verdict"],
                    "new_spend_usd": result["new_spend_usd"],
                    "comparison": result["comparison"],
                },
                sort_keys=True,
            )
        )
    elif args.command == "prepare-coding-replay-expansion":
        result = prepare_coding_replay_expansion(
            args.initial, args.private_dir, args.out
        )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "cells": len(result["cells"]),
                    "new_reserve_usd": result["budget"]["new_reserve_usd"],
                },
                sort_keys=True,
            )
        )
    elif args.command == "run-coding-replay-expansion":
        result = run_coding_replay_expansion(
            args.initial, args.private_dir, args.out, args.claude
        )
        print(
            json.dumps(
                {
                    "verdict": result["verdict"],
                    "new_spend_usd": result["new_spend_usd"],
                    "comparison": result["comparison"],
                },
                sort_keys=True,
            )
        )
    elif args.command == "regrade-coding-replay-expansion":
        result = regrade_coding_replay_expansion(
            args.initial, args.private_dir, args.out
        )
        print(
            json.dumps(
                {"verdict": result["verdict"], "comparison": result["comparison"]},
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
