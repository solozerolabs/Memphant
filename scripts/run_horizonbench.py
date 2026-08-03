#!/usr/bin/env python3
"""Fail-closed HorizonBench acquisition, runtime, reader, and analysis runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch-sample")
    fetch.add_argument("--out", required=True, type=Path)
    fetch.add_argument("--lock-out", type=Path)
    args = parser.parse_args()
    if args.command == "fetch-sample":
        lock = fetch_sample(args.out)
        encoded = json.dumps(lock, indent=2, sort_keys=True).encode() + b"\n"
        if args.lock_out:
            atomic_write(args.lock_out, encoded)
        else:
            print(encoded.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
