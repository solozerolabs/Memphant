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
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import gate_runtime as gr  # noqa: E402


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
    lines.extend(f"{option['letter']}: {option['option']}" for option in options)
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
    sessions = parse_sessions(view["conversation"])
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
        "lineage": {
            "repository": gr.repository_identity(REPO_ROOT),
            "migrations": gr.migration_identity(REPO_ROOT),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
