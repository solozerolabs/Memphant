#!/usr/bin/env python3
"""Fail-closed HorizonBench acquisition, runtime, reader, and analysis runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import gate_runtime as gr  # noqa: E402
from provider_attempts import (  # noqa: E402
    CAMPAIGN_HARD_CEILING_NANOS,
    CAMPAIGN_UNALLOCATED_RESERVE_NANOS,
    open_campaign_ledger,
    provider_attempt_ledger_is_complete,
)
from run_reader import (  # noqa: E402
    CallBudgetExceeded,
    ProviderRefusal,
    ReaderCli,
    parse_reader_output,
    restore_spend_from_attempts,
)


DATASET_ID = "stellalisy/HorizonBench"
DATASET_REVISION = "50941f00f90c03a5a60219d76393869b757b835a"
DATASET_SERVER = "https://datasets-server.huggingface.co"
HUB_API = "https://huggingface.co/api/datasets"
SCORING_ONLY_FIELDS = (
    "correct_letter",
    "distractor_letter",
    "has_evolved",
    "preference_domain",
    "preference_evolution",
)
PROMPT_FIELDS = ("id", "user_id", "generator", "conversation", "options")
MAX_EPISODE_BYTES = 120_000
RECALL_QUERY_CHARS = 8_000
ESCAPED_CODEPOINT = re.compile(r"\\u([0-9a-fA-F]{4})")
PAID_ARMS = ("full_context", "fast", "selective_deep")
READER_MODEL = "anthropic/claude-opus-4.5"
READER_PROVIDER = "anthropic"
READER_MAX_SPEND_USD = Decimal("22")
DEEP_MODEL = "openai/gpt-5.6-luna-20260709"
DEEP_PROVIDER = "azure"
DEEP_MAX_SPEND_USD = Decimal("3")
COMBINED_MAX_SPEND_USD = Decimal("25")
READER_SYSTEM_PROMPT = (
    "Choose the response A-E that best matches the user's current preference using "
    "only the supplied evidence. If the evidence is insufficient, set abstain=true "
    "and answer=null. Otherwise set abstain=false and answer to exactly one uppercase "
    "letter A-E. Return the required JSON object; notes must be brief."
)


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def authorization_packet(
    frozen_inputs: dict, *, authorized_by: str, authorized_at: str
) -> dict:
    packet = {
        "schema_version": 1,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        "frozen_inputs": frozen_inputs,
        "models": {
            "reader": READER_MODEL,
            "reader_provider": READER_PROVIDER,
            "reader_prompt_sha256": hashlib.sha256(
                READER_SYSTEM_PROMPT.encode()
            ).hexdigest(),
            "temperature": 0,
            "max_output_tokens": 256,
            "reader_price_usd_per_million": {
                "prompt": "5",
                "completion": "25",
            },
            "deep": DEEP_MODEL,
            "deep_provider": DEEP_PROVIDER,
            "deep_price_usd_per_million": {
                "prompt": "1.1",
                "completion": "6.6",
            },
        },
        "hard_limits": {
            "reader": {
                "max_logical_calls": 30,
                "max_provider_attempts": 60,
                "max_spend_usd": str(READER_MAX_SPEND_USD),
            },
            "deep": {
                "max_calls": 10,
                "max_spend_per_call_usd": "0.30",
                "max_spend_usd": str(DEEP_MAX_SPEND_USD),
            },
            "combined_max_spend_usd": str(COMBINED_MAX_SPEND_USD),
        },
        "execution": {
            "journal_path": "reader-attempts.jsonl",
            "cache_dir": "reader-cache",
            "raw_rows": "paid-rows.jsonl",
            "deep_cache": "deep-evidence.jsonl",
            "closure": "reader-closure.json",
            "census": "paid-census.json",
        },
        "campaign": {
            "journal_path": "reader-attempts.jsonl",
            "hard_ceiling_nanos": CAMPAIGN_HARD_CEILING_NANOS,
            "unallocated_reserve_nanos": CAMPAIGN_UNALLOCATED_RESERVE_NANOS,
            "opening_liability_nanos": 0,
            "opening_reservations": [],
        },
        "claim_boundary": "Ten-row diagnostic only; no SOTA or default promotion.",
    }
    scope = {
        key: value
        for key, value in packet.items()
        if key not in {"schema_version", "status", "authorization"}
    }
    packet["authorization"] = {
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "authorization_scope_sha256": sha256_json(scope),
    }
    return packet


def validate_pilot_authorization(packet: dict, expected_frozen: dict) -> None:
    authorization = packet.get("authorization") if isinstance(packet, dict) else None
    if (
        not isinstance(authorization, dict)
        or not isinstance(authorization.get("authorized_by"), str)
        or not authorization["authorized_by"].strip()
        or not isinstance(authorization.get("authorized_at"), str)
        or not authorization["authorized_at"].strip()
    ):
        raise ValueError("paid authorization is missing owner approval")
    expected = authorization_packet(
        expected_frozen,
        authorized_by=authorization["authorized_by"],
        authorized_at=authorization["authorized_at"],
    )
    if packet != expected:
        raise ValueError("paid authorization scope or frozen input drift")


def selective_route(fast_row: dict) -> str:
    if fast_row.get("status") != "completed":
        raise ValueError("selective routing requires a completed Fast reader row")
    if fast_row.get("abstain") is True and fast_row.get("answer") is None:
        return "deep"
    answer = fast_row.get("answer")
    if fast_row.get("abstain") is False and answer in list("ABCDE"):
        return "fast"
    raise ValueError("selective routing received an invalid Fast reader row")


def validate_deep_completion(row: dict) -> None:
    deep = row.get("deep")
    if (
        row.get("degraded") is not False
        or not isinstance(row.get("evidence"), list)
        or not row["evidence"]
        or not isinstance(deep, dict)
        or deep.get("status") != "completed"
        or type(deep.get("settled_micros")) is not int
        or not 0 <= deep["settled_micros"] <= 300_000
        or deep.get("unsettled_micros_upper_bound") != 0
    ):
        raise ValueError("Deep result is not completed, settled, non-degraded evidence")


def validate_terminal_rows(rows: list[dict], expected_ids: list[str]) -> None:
    keys = [(row.get("id"), row.get("arm")) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("paid terminal rows contain a duplicate id/arm")
    expected = {(item_id, arm) for item_id in expected_ids for arm in PAID_ARMS}
    if set(keys) != expected or len(keys) != len(expected):
        raise ValueError("paid terminal rows do not match expected IDs and arms")
    if any(row.get("status") not in {"completed", "error"} for row in rows):
        raise ValueError("paid terminal rows contain a non-terminal status")


def normalize_source_text(value: str) -> str:
    """Losslessly escape Postgres-forbidden controls and literal backslashes."""
    return "".join(
        f"\\u{ord(character):04x}"
        if character == "\\" or (ord(character) < 32 and character not in "\n\r\t")
        else character
        for character in value
    )


def restore_source_text(value: str) -> str:
    return ESCAPED_CODEPOINT.sub(lambda match: chr(int(match.group(1), 16)), value)


def parse_options(value: str | list[dict]) -> list[dict]:
    options = json.loads(value) if isinstance(value, str) else value
    if not isinstance(options, list) or len(options) != 5:
        raise ValueError("HorizonBench options must contain exactly five rows")
    letters = []
    normalized = []
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("HorizonBench option must be an object")
        if set(option) < {"letter", "value", "option"}:
            raise ValueError("HorizonBench option is missing a required field")
        letter = option["letter"]
        if letter not in "ABCDE" or not isinstance(option["option"], str):
            raise ValueError("HorizonBench option letter or body is invalid")
        letters.append(letter)
        normalized.append(
            {
                "letter": letter,
                "value": option["value"],
                "option": option["option"],
            }
        )
    if letters != list("ABCDE"):
        raise ValueError("HorizonBench option letters must be ordered A-E")
    return normalized


def parse_sessions(conversation_text: str) -> list[dict]:
    """Mirror the official parser without importing HorizonBench dependencies."""
    sessions: list[dict] = []
    current: dict | None = None
    for line in conversation_text.split("\n"):
        if line.startswith("Conversation History:"):
            continue
        if line.startswith("Date: "):
            if current is not None:
                sessions.append(current)
            current = {"date": line[6:], "scenario": "", "turns": []}
        elif line.startswith("Scenario: ") and current is not None:
            current["scenario"] = line[10:]
        elif line.startswith("User: ") and current is not None:
            current["turns"].append({"role": "user", "content": line[6:]})
        elif line.startswith("Assistant: ") and current is not None:
            current["turns"].append({"role": "assistant", "content": line[11:]})
        elif current is not None and current["turns"]:
            current["turns"][-1]["content"] += "\n" + line
    if current is not None:
        sessions.append(current)
    return sessions


def prompt_item(row: dict) -> dict:
    """Return the complete and exclusive pre-score view of one benchmark row."""
    missing = [field for field in PROMPT_FIELDS if field not in row]
    if missing:
        raise ValueError(f"HorizonBench prompt row is missing {missing}")
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "generator": row["generator"],
        "conversation": row["conversation"],
        "options": parse_options(row["options"]),
    }


def build_question(options: list[dict]) -> str:
    lines = ["Which of the following responses would align with the user's preferences the most?"]
    lines.extend(
        f"{option['letter']}: {normalize_source_text(option['option'])}"
        for option in options
    )
    lines.append(
        "Please respond with the letter of the option that aligns with the user's preferences the most and nothing else."
    )
    return "\n".join(lines)


def _safe_ref(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")[:72]
    if safe == value and safe:
        return safe
    suffix = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{safe or 'item'}-{suffix}"


def _observed_times(sessions: list[dict]) -> list[str]:
    values = []
    previous: datetime | None = None
    for session in sessions:
        try:
            parsed = datetime.fromisoformat(session["date"].replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid HorizonBench session date: {session['date']!r}") from error
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if previous is not None and parsed <= previous:
            parsed = previous + timedelta(microseconds=1)
        values.append(parsed.isoformat().replace("+00:00", "Z"))
        previous = parsed
    return values


def _session_body(session: dict) -> str:
    lines = [f"Date: {session['date']}"]
    if session["scenario"]:
        lines.append(f"Scenario: {session['scenario']}")
    for turn in session["turns"]:
        label = "User" if turn["role"] == "user" else "Assistant"
        lines.append(f"{label}: {turn['content']}")
    body = "\n".join(lines)
    if not body.strip() or len(body.encode()) > MAX_EPISODE_BYTES:
        raise ValueError("HorizonBench session body is empty or exceeds retain boundary")
    return body


def runtime_item(row: dict) -> dict:
    view = prompt_item(row)
    sessions = parse_sessions(normalize_source_text(view["conversation"]))
    if not sessions:
        raise ValueError(f"HorizonBench row {view['id']} has no conversation sessions")
    ref = _safe_ref(view["id"])
    times = _observed_times(sessions)
    episodes = [
        {
            "source_ref": f"horizon:{ref}:session:{index}",
            "observed_at": times[index],
            "body": _session_body(session),
        }
        for index, session in enumerate(sessions)
    ]
    question = build_question(view["options"])
    return {
        "id": view["id"],
        "user_id": view["user_id"],
        "generator": view["generator"],
        "context_ref": f"horizon-{ref}",
        "episodes": episodes,
        "question": question,
        "recall_query": question[:RECALL_QUERY_CHARS],
        "options": view["options"],
    }


def validate_evidence_rows(rows: list[dict], expected_ids: list[str], arm: str) -> None:
    ids = [row.get("id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"HorizonBench {arm} evidence has a duplicate id")
    if set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
        raise ValueError(f"HorizonBench {arm} evidence does not match expected IDs")
    for row in rows:
        if row.get("arm") != arm:
            raise ValueError(f"HorizonBench evidence row has wrong arm: {row.get('arm')!r}")
        if row.get("degraded") is not False:
            raise ValueError(f"HorizonBench {arm} evidence is degraded for {row.get('id')}")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"HorizonBench {arm} has empty evidence for {row.get('id')}")


def retain_runtime_items(client, items: list[dict]) -> tuple[dict[str, dict], int]:
    bound = {}
    retained = 0
    for item in items:
        context = client.bind_context(
            item["context_ref"],
            subject_ref=item["context_ref"],
            actor_ref="horizonbench-runner",
            scope_ref=item["context_ref"],
            agent_node_ref="horizonbench-runner",
        )
        bound[item["id"]] = context
        for episode in item["episodes"]:
            payload = gr.episode_retain_payload(
                context,
                source_ref=episode["source_ref"],
                observed_at=episode["observed_at"],
                source_kind="user",
                body=episode["body"],
            )
            client.post("/v1/episodes", payload)
            retained += 1
    return bound, retained


def recall_runtime_items(
    client,
    items: list[dict],
    bound: dict[str, dict],
    arm: str,
    k: int,
    budget_tokens: int,
) -> list[dict]:
    mode = "deep" if arm == "deep" else "fast"
    rows = []
    for item in items:
        started = time.monotonic()
        response = client.post(
            "/v1/recall",
            {
                **bound[item["id"]],
                "query": item["recall_query"],
                "limit": k,
                "budget_tokens": budget_tokens,
                "mode": mode,
            },
        )
        rows.append(
            {
                "id": item["id"],
                "user_id": item["user_id"],
                "generator": item["generator"],
                "arm": arm,
                "question": item["question"],
                "options": item["options"],
                "evidence": response.get("items", []),
                "degraded": bool(response.get("degraded", False)),
                "trace_id": response.get("trace_id"),
                "deep": response.get("deep"),
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
            }
        )
    return rows


def validate_sample_rows(rows: list[dict]) -> dict:
    if len(rows) != 10:
        raise ValueError(f"HorizonBench sample must contain exactly 10 rows, got {len(rows)}")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(item_id, str) or not item_id for item_id in ids):
        raise ValueError("HorizonBench sample has a missing id")
    if len(set(ids)) != len(ids):
        raise ValueError("HorizonBench sample has a duplicate id")
    users = [row.get("user_id") for row in rows]
    if any(not isinstance(user_id, str) or not user_id for user_id in users):
        raise ValueError("HorizonBench sample has a missing user_id")
    if len(set(users)) != 10:
        raise ValueError("HorizonBench sample must contain exactly 10 unique users")
    for row in rows:
        view = prompt_item(row)
        sessions = parse_sessions(view["conversation"])
        if not sessions or any(not session["date"] or not session["turns"] for session in sessions):
            raise ValueError(f"HorizonBench row {row['id']} has invalid conversation sessions")
    return {
        "row_count": len(rows),
        "user_count": len(set(users)),
        "expected_ids": ids,
        "expected_user_ids": users,
    }


def canonical_jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in rows
    )


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    atomic_write(path, canonical_jsonl_bytes(rows))


def evidence_prompt(evidence: list[dict], question: str) -> str:
    bodies = []
    for rank, item in enumerate(evidence, start=1):
        body = item.get("body") if isinstance(item, dict) else None
        if not isinstance(body, str) or not body.strip():
            raise ValueError("reader evidence contains an empty body")
        bodies.append(f"[{rank}] {body}")
    if not bodies:
        raise ValueError("reader evidence is empty")
    return "Evidence:\n" + "\n\n".join(bodies) + "\n\nQuestion:\n" + question


def full_context_prompt(row: dict) -> str:
    view = prompt_item(row)
    return (
        "Evidence:\n"
        + normalize_source_text(view["conversation"])
        + "\n\nQuestion:\n"
        + build_question(view["options"])
    )


def _validate_reader_metadata(metadata: dict | None) -> None:
    if (
        not isinstance(metadata, dict)
        or metadata.get("parse_status") != "provider_response_validated"
        or metadata.get("requested_model") != READER_MODEL
        or str(metadata.get("provider", "")).lower() != READER_PROVIDER
        or not isinstance(metadata.get("usage"), dict)
        or not isinstance(metadata["usage"].get("cost"), (int, float))
        or metadata["usage"]["cost"] <= 0
    ):
        raise RuntimeError("reader provider/model/price provenance mismatch")


def reader_terminal(cli: ReaderCli, item_id: str, arm: str, prompt: str) -> dict:
    reply = cli.call("reader", READER_SYSTEM_PROMPT, prompt)
    metadata = cli.last_call_metadata
    _validate_reader_metadata(metadata)
    try:
        parsed = parse_reader_output(reply)
        answer = parsed["answer"]
        if answer is not None:
            answer = answer.strip().upper()
        if not parsed["abstain"] and answer not in list("ABCDE"):
            raise ValueError("reader answer must be exactly one letter A-E")
        return {
            "id": item_id,
            "arm": arm,
            "status": "completed",
            "answer": answer,
            "abstain": parsed["abstain"],
            "notes": parsed["notes"],
            "provider": metadata,
        }
    except (json.JSONDecodeError, ValueError, TypeError) as error:
        return {
            "id": item_id,
            "arm": arm,
            "status": "error",
            "answer": None,
            "abstain": False,
            "error": f"reader_parse: {error}",
            "provider": metadata,
        }


def _append_terminal(
    rows: list[dict], output: Path, authorization_sha256: str, row: dict
) -> None:
    row["authorization_sha256"] = authorization_sha256
    rows.append(row)
    write_jsonl(output, rows)


def _expected_paid_frozen(args) -> dict:
    return {
        "source_jsonl_sha256": gr.sha256_file(args.source.resolve()),
        "lock_sha256": gr.sha256_file(args.lock.resolve()),
        "fast_evidence_sha256": gr.sha256_file(args.fast_evidence.resolve()),
        "fast_gate_sha256": gr.sha256_file(args.fast_gate.resolve()),
        "runner_sha256": gr.sha256_file(Path(__file__)),
        "provider_attempts_sha256": gr.sha256_file(
            SCRIPTS_DIR / "provider_attempts.py"
        ),
    }


def _configure_deep_runtime() -> None:
    os.environ.update(
        {
            "MEMPHANT_FACT_EXTRACTION": "0",
            "MEMPHANT_DEEP": "on",
            "MEMPHANT_DEEP_MODEL": DEEP_MODEL,
            "MEMPHANT_DEEP_RESPONSE_MODEL": DEEP_MODEL,
            "MEMPHANT_DEEP_PROVIDERS": DEEP_PROVIDER,
            "MEMPHANT_DEEP_INPUT_PRICE_MICROS_PER_MILLION": "1100000",
            "MEMPHANT_DEEP_OUTPUT_PRICE_MICROS_PER_MILLION": "6600000",
            "MEMPHANT_DEEP_PROMPT_PATH": str(
                REPO_ROOT / "config" / "deep-recall-v1.txt"
            ),
        }
    )


def run_paid_pilot(args) -> dict:
    source_rows, lock = load_locked_sample(args.source.resolve(), args.lock.resolve())
    expected_ids = lock["expected_ids"]
    fast_rows = load_jsonl(args.fast_evidence.resolve())
    validate_evidence_rows(fast_rows, expected_ids, "fast")
    fast_gate = json.loads(args.fast_gate.read_text(encoding="utf-8"))
    if (
        fast_gate.get("status") != "passed"
        or fast_gate.get("evidence_jsonl_sha256")
        != gr.sha256_file(args.fast_evidence.resolve())
    ):
        raise ValueError("paid pilot requires the matching passed Fast gate")

    packet = json.loads(args.authorization.read_text(encoding="utf-8"))
    frozen = _expected_paid_frozen(args)
    validate_pilot_authorization(packet, frozen)
    authorization_sha = packet["authorization"]["authorization_scope_sha256"]
    execution = packet["execution"]
    artifact_dir = args.authorization.resolve().parent
    journal = artifact_dir / execution["journal_path"]
    cache_dir = artifact_dir / execution["cache_dir"]
    raw_output = artifact_dir / execution["raw_rows"]
    deep_cache_path = artifact_dir / execution["deep_cache"]
    closure_path = artifact_dir / execution["closure"]
    census_path = artifact_dir / execution["census"]
    if args.output.resolve() != raw_output.resolve():
        raise ValueError("paid output differs from the authorized path")

    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    _configure_deep_runtime()
    items = [runtime_item(row) for row in source_rows]
    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, database_url, name_prefix="horizon-paid"
    )
    server = gr.Server(
        args.server_bin,
        database_url,
        args.port,
        log_path=artifact_dir / "server-paid.log",
    )
    ledger = None
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        bound, retained = retain_runtime_items(client, items)
        compiled = gr.drain_worker(args.worker_bin, database_url)
        if retained != 943 or compiled != 943:
            raise RuntimeError(
                f"paid runtime lineage mismatch: retained={retained} compiled={compiled}"
            )

        ledger = open_campaign_ledger(
            args.authorization,
            screen_id="horizonbench-pilot",
            expected_journal_path=journal,
        )
        snapshot = ledger.snapshot()
        reported, unsettled = restore_spend_from_attempts(snapshot["attempts"])
        cli = ReaderCli(
            "openrouter",
            READER_MODEL,
            READER_MODEL,
            cache_dir,
            max_calls=30,
            max_spend_usd=READER_MAX_SPEND_USD,
            max_price_per_million={
                "prompt": Decimal("5"),
                "completion": Decimal("25"),
            },
            max_output_tokens=256,
        )
        cli.provider_only = [READER_PROVIDER]
        cli.set_provider_attempt_ledger(ledger)
        cli.provider_attempts = len(snapshot["attempts"])
        cli.set_provider_attempt_limit(60)
        cli.restore_spend_state(
            reported_spend_usd=reported, unsettled_liability_usd=unsettled
        )

        terminal_rows = load_jsonl(raw_output)
        terminal_keys = [(row.get("id"), row.get("arm")) for row in terminal_rows]
        if len(terminal_keys) != len(set(terminal_keys)):
            raise ValueError("paid resume contains duplicate terminal rows")
        if any(
            row.get("authorization_sha256") != authorization_sha
            or row.get("id") not in expected_ids
            or row.get("arm") not in PAID_ARMS
            for row in terminal_rows
        ):
            raise ValueError("paid resume row is outside the authorized scope")
        by_key = {(row["id"], row["arm"]): row for row in terminal_rows}
        source_by_id = {row["id"]: row for row in source_rows}
        fast_by_id = {row["id"]: row for row in fast_rows}

        for item_id in expected_ids:
            if (item_id, "full_context") not in by_key:
                try:
                    row = reader_terminal(
                        cli,
                        item_id,
                        "full_context",
                        full_context_prompt(source_by_id[item_id]),
                    )
                except (CallBudgetExceeded, ProviderRefusal, RuntimeError) as error:
                    _append_terminal(
                        terminal_rows,
                        raw_output,
                        authorization_sha,
                        {
                            "id": item_id,
                            "arm": "full_context",
                            "status": "error",
                            "answer": None,
                            "abstain": False,
                            "error": f"{type(error).__name__}: {error}",
                        },
                    )
                    raise
                _append_terminal(terminal_rows, raw_output, authorization_sha, row)
                by_key[(item_id, "full_context")] = row

        for item_id in expected_ids:
            if (item_id, "fast") not in by_key:
                try:
                    row = reader_terminal(
                        cli,
                        item_id,
                        "fast",
                        evidence_prompt(
                            fast_by_id[item_id]["evidence"],
                            fast_by_id[item_id]["question"],
                        ),
                    )
                except (CallBudgetExceeded, ProviderRefusal, RuntimeError) as error:
                    _append_terminal(
                        terminal_rows,
                        raw_output,
                        authorization_sha,
                        {
                            "id": item_id,
                            "arm": "fast",
                            "status": "error",
                            "answer": None,
                            "abstain": False,
                            "error": f"{type(error).__name__}: {error}",
                        },
                    )
                    raise
                _append_terminal(terminal_rows, raw_output, authorization_sha, row)
                by_key[(item_id, "fast")] = row

        deep_rows = load_jsonl(deep_cache_path)
        if len({row.get("id") for row in deep_rows}) != len(deep_rows):
            raise ValueError("Deep resume cache contains duplicate IDs")
        deep_by_id = {row["id"]: row for row in deep_rows}
        for row in deep_rows:
            if (
                row.get("authorization_sha256") != authorization_sha
                or row.get("id") not in expected_ids
            ):
                raise ValueError("Deep resume cache authorization or ID mismatch")
            validate_deep_completion(row)

        for item in items:
            item_id = item["id"]
            if (item_id, "selective_deep") in by_key:
                continue
            fast_answer = by_key[(item_id, "fast")]
            if fast_answer.get("status") != "completed":
                row = {
                    "id": item_id,
                    "arm": "selective_deep",
                    "status": "error",
                    "answer": None,
                    "abstain": False,
                    "route": "fast_error",
                    "error": "Fast reader row unavailable for selective routing",
                }
            elif selective_route(fast_answer) == "fast":
                row = {
                    "id": item_id,
                    "arm": "selective_deep",
                    "status": "completed",
                    "answer": fast_answer["answer"],
                    "abstain": False,
                    "route": "fast",
                    "provider": fast_answer["provider"],
                }
            else:
                deep_row = deep_by_id.get(item_id)
                if deep_row is None:
                    deep_row = recall_runtime_items(
                        client, [item], bound, "deep", args.k, args.budget_tokens
                    )[0]
                    deep_row["authorization_sha256"] = authorization_sha
                    validate_deep_completion(deep_row)
                    deep_rows.append(deep_row)
                    write_jsonl(deep_cache_path, deep_rows)
                    deep_by_id[item_id] = deep_row
                if len(deep_rows) > 10:
                    raise RuntimeError("Deep call ceiling exceeded")
                deep_micros = sum(row["deep"]["settled_micros"] for row in deep_rows)
                if deep_micros > 3_000_000:
                    raise RuntimeError("Deep spend ceiling exceeded")
                row = reader_terminal(
                    cli,
                    item_id,
                    "selective_deep",
                    evidence_prompt(deep_row["evidence"], deep_row["question"]),
                )
                row["route"] = "deep"
                row["deep"] = deep_row["deep"]
            _append_terminal(terminal_rows, raw_output, authorization_sha, row)
            by_key[(item_id, "selective_deep")] = row

        validate_terminal_rows(terminal_rows, expected_ids)
        snapshot = ledger.snapshot()
        if not provider_attempt_ledger_is_complete(snapshot):
            raise RuntimeError("paid reader ledger is incomplete or unpriced")
        reader_cost = Decimal(str(snapshot["reported_cost_usd"]))
        deep_cost = Decimal(
            sum(row["deep"]["settled_micros"] for row in deep_rows)
        ) / Decimal(1_000_000)
        if reader_cost + deep_cost > COMBINED_MAX_SPEND_USD:
            raise RuntimeError("combined paid pilot spend ceiling exceeded")
        closure = ledger.close_campaign(closure_path)
        census = {
            "schema_version": 1,
            "status": "complete",
            "authorization_scope_sha256": authorization_sha,
            "terminal_rows": len(terminal_rows),
            "completed_rows": sum(row["status"] == "completed" for row in terminal_rows),
            "error_rows": sum(row["status"] == "error" for row in terminal_rows),
            "reader": {
                "model": READER_MODEL,
                "provider": READER_PROVIDER,
                "provider_attempts": snapshot["provider_attempts"],
                "priced_provider_attempts": snapshot["priced_provider_attempts"],
                "reported_cost_usd": str(reader_cost),
                "attempts_sha256": snapshot["attempts_sha256"],
            },
            "deep": {
                "model": DEEP_MODEL,
                "provider": DEEP_PROVIDER,
                "calls": len(deep_rows),
                "settled_cost_usd": str(deep_cost),
                "unsettled_cost_usd": "0",
            },
            "combined_cost_usd": str(reader_cost + deep_cost),
            "raw_rows_sha256": gr.sha256_file(raw_output),
            "deep_cache_sha256": (
                gr.sha256_file(deep_cache_path) if deep_cache_path.exists() else None
            ),
            "journal_closure": closure,
            "lineage": {
                "repository": gr.repository_identity(REPO_ROOT),
                **frozen,
                "server_sha256": gr.sha256_file(Path(args.server_bin)),
                "worker_sha256": gr.sha256_file(Path(args.worker_bin)),
                "cli_sha256": gr.sha256_file(Path(args.cli_bin)),
            },
        }
        atomic_write_json(census_path, census)
        return census
    finally:
        if ledger is not None:
            ledger.close()
        server.stop()


def load_locked_sample(source: Path, lock_path: Path) -> tuple[list[dict], dict]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != lock.get("jsonl_sha256"):
        raise ValueError("HorizonBench sample bytes do not match the lock")
    validate_source_revision(lock.get("dataset_revision"))
    rows = [json.loads(line) for line in raw.split(b"\n") if line.strip()]
    census = validate_sample_rows(rows)
    if census["expected_ids"] != lock.get("expected_ids"):
        raise ValueError("HorizonBench sample IDs do not match the lock")
    if census["expected_user_ids"] != lock.get("expected_user_ids"):
        raise ValueError("HorizonBench sample users do not match the lock")
    return rows, lock


def validate_source_revision(actual: str) -> None:
    if actual != DATASET_REVISION:
        raise ValueError(
            f"HorizonBench dataset revision drift: {actual!r} != {DATASET_REVISION!r}"
        )


def fetch_source_revision() -> str:
    request = urllib.request.Request(
        f"{HUB_API}/{DATASET_ID}",
        headers={"User-Agent": "MemPhant-HorizonBench/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    revision = payload.get("sha")
    if not isinstance(revision, str):
        raise ValueError("HorizonBench Hub metadata omitted its source revision")
    validate_source_revision(revision)
    return revision


def fetch_sample(output: Path) -> dict:
    revision = fetch_source_revision()
    params = urllib.parse.urlencode(
        {
            "dataset": DATASET_ID,
            "config": "sample",
            "split": "test",
            "offset": 0,
            "length": 10,
        }
    )
    request = urllib.request.Request(
        f"{DATASET_SERVER}/rows?{params}",
        headers={"User-Agent": "MemPhant-HorizonBench/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    rows = [entry["row"] for entry in payload.get("rows", [])]
    census = validate_sample_rows(rows)
    raw = canonical_jsonl_bytes(rows)
    atomic_write(output, raw)
    return {
        "dataset": DATASET_ID,
        "dataset_revision": revision,
        "config": "sample",
        "split": "test",
        "source_url": request.full_url,
        "jsonl_sha256": hashlib.sha256(raw).hexdigest(),
        **census,
    }


def fast_gate_evidence_contract(source_sha: str, k: int, budget_tokens: int) -> dict:
    return {
        "schema_version": 1,
        "decisional": False,
        "claim": "The pinned ten-row HorizonBench sample completed the gold-blind Fast construction gate.",
        "power": {
            "test": "descriptive-only (no test)",
            "n": 10,
            "b": 0,
            "c": 0,
            "n_d": 0,
        },
        "mechanism_enabled": True,
        "probe_kind": "gate",
        "mechanism_evidence": "Fast recall ran with MEMPHANT_DEEP=off and produced ten non-degraded evidence rows.",
        "harness": {
            "embed_model": "local sentence-unit embedder",
            "scorer": "construction completeness only; benchmark gold remained quarantined",
            "k": k,
            "budget": budget_tokens,
            "flags": ["fast", "fact_extraction=off", "deep=off"],
        },
        "corpus": {
            "sha256": source_sha,
            "snapshot_id": f"{DATASET_ID}@{DATASET_REVISION}:sample/test",
            "n_items": 10,
        },
        "instrument_verification": {
            "shipped_rows_verified": True,
            "rows_counted": 10,
            "fields_counted": {
                "conversation": 10,
                "options": 10,
                "correct_letter": 10,
            },
            "license_id": "CC-BY-4.0",
            "license_source": "RECORD_METADATA",
            "license_evidence": "Pinned Hugging Face dataset metadata.",
        },
        "notes": "Non-decisional construction proof; no answer scoring or SOTA claim.",
    }


def build_fast_evidence(args) -> dict:
    source = args.source.expanduser().resolve()
    rows, lock = load_locked_sample(source, args.lock.resolve())
    items = [runtime_item(row) for row in rows]
    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    os.environ["MEMPHANT_FACT_EXTRACTION"] = "0"
    os.environ["MEMPHANT_DEEP"] = "off"
    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, database_url, name_prefix="horizon-sample"
    )
    server = gr.Server(
        args.server_bin,
        database_url,
        args.port,
        log_path=args.out.parent / "server-fast.log",
    )
    started = time.monotonic()
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        bound, retained = retain_runtime_items(client, items)
        compiled = gr.drain_worker(args.worker_bin, database_url)
        evidence = recall_runtime_items(
            client, items, bound, "fast", args.k, args.budget_tokens
        )
    finally:
        server.stop()
    validate_evidence_rows(evidence, lock["expected_ids"], "fast")
    evidence_raw = canonical_jsonl_bytes(evidence)
    atomic_write(args.out, evidence_raw)
    report = {
        "schema_version": 1,
        "status": "passed",
        "decisional": False,
        "claim": "The pinned ten-row HorizonBench sample completed the gold-blind Fast construction gate.",
        "source": {
            "dataset": DATASET_ID,
            "dataset_revision": DATASET_REVISION,
            "jsonl_sha256": lock["jsonl_sha256"],
            "expected_ids_sha256": hashlib.sha256(
                json.dumps(lock["expected_ids"], separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "runtime": {
            "arm": "fast",
            "items": len(items),
            "sessions_retained": retained,
            "jobs_compiled": compiled,
            "nonempty_evidence_rows": sum(bool(row["evidence"]) for row in evidence),
            "degraded_rows": sum(bool(row["degraded"]) for row in evidence),
            "k": args.k,
            "budget_tokens": args.budget_tokens,
            "latency_ms": [row["latency_ms"] for row in evidence],
        },
        "gold_quarantine": {
            "runtime_fields": list(PROMPT_FIELDS),
            "scoring_only_fields": list(SCORING_ONLY_FIELDS),
            "mental_state_graph_acquired": False,
        },
        "evidence_jsonl_sha256": hashlib.sha256(evidence_raw).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "evidence_contract": fast_gate_evidence_contract(
            lock["jsonl_sha256"], args.k, args.budget_tokens
        ),
        "lineage": {
            "repository": gr.repository_identity(REPO_ROOT),
            "migrations": gr.migration_identity(REPO_ROOT),
            "runner_sha256": gr.sha256_file(Path(__file__)),
            "test_sha256": gr.sha256_file(
                REPO_ROOT / "tests" / "test_horizonbench_contract.py"
            ),
            "server_sha256": gr.sha256_file(Path(args.server_bin)),
            "worker_sha256": gr.sha256_file(Path(args.worker_bin)),
            "cli_sha256": gr.sha256_file(Path(args.cli_bin)),
        },
    }
    atomic_write_json(args.report_out, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch-sample")
    fetch.add_argument("--out", required=True, type=Path)
    fetch.add_argument("--lock-out", type=Path)
    evidence = subparsers.add_parser("build-fast-evidence")
    evidence.add_argument("--source", required=True, type=Path)
    evidence.add_argument(
        "--lock",
        default=REPO_ROOT / "benchmarks/manifests/horizonbench.sample.v1.json",
        type=Path,
    )
    evidence.add_argument("--out", required=True, type=Path)
    evidence.add_argument("--report-out", required=True, type=Path)
    evidence.add_argument("--k", type=int, default=20)
    evidence.add_argument("--budget-tokens", type=int, default=16384)
    evidence.add_argument("--port", type=int, default=39483)
    evidence.add_argument(
        "--database-url",
        default="postgres://memphant:memphant@localhost:5432/memphant",
    )
    evidence.add_argument(
        "--server-bin", default=str(REPO_ROOT / "target/release/memphant-server")
    )
    evidence.add_argument(
        "--worker-bin", default=str(REPO_ROOT / "target/release/memphant-worker")
    )
    evidence.add_argument(
        "--cli-bin", default=str(REPO_ROOT / "target/release/memphant-cli")
    )
    paid = subparsers.add_parser("run-paid-pilot")
    paid.add_argument("--source", required=True, type=Path)
    paid.add_argument(
        "--lock",
        default=REPO_ROOT / "benchmarks/manifests/horizonbench.sample.v1.json",
        type=Path,
    )
    paid.add_argument(
        "--fast-evidence",
        default=REPO_ROOT
        / "docs/build-log/artifacts/horizonbench-pilot/fast-evidence.jsonl",
        type=Path,
    )
    paid.add_argument(
        "--fast-gate",
        default=REPO_ROOT
        / "docs/build-log/artifacts/horizonbench-pilot/fast-gate.json",
        type=Path,
    )
    paid.add_argument("--authorization", required=True, type=Path)
    paid.add_argument("--output", required=True, type=Path)
    paid.add_argument("--k", type=int, default=20)
    paid.add_argument("--budget-tokens", type=int, default=16384)
    paid.add_argument("--port", type=int, default=39484)
    paid.add_argument(
        "--database-url",
        default="postgres://memphant:memphant@localhost:5432/memphant",
    )
    paid.add_argument(
        "--server-bin", default=str(REPO_ROOT / "target/release/memphant-server")
    )
    paid.add_argument(
        "--worker-bin", default=str(REPO_ROOT / "target/release/memphant-worker")
    )
    paid.add_argument(
        "--cli-bin", default=str(REPO_ROOT / "target/release/memphant-cli")
    )
    args = parser.parse_args()
    if args.command == "fetch-sample":
        lock = fetch_sample(args.out)
        encoded = json.dumps(lock, indent=2, sort_keys=True).encode() + b"\n"
        if args.lock_out:
            atomic_write(args.lock_out, encoded)
        else:
            print(encoded.decode(), end="")
    elif args.command == "build-fast-evidence":
        report = build_fast_evidence(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "run-paid-pilot":
        report = run_paid_pilot(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
