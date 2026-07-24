#!/usr/bin/env python3
"""Materialize a volume-matched public coding-continuity corpus and goldens.

The source is the pinned, CC-BY-4.0 Nebius OpenHands trajectory dataset. The
Hugging Face rows service cannot address a commit directly, so this program
checks the repository's exact revision before and after every materialization
and aborts on drift. Outputs are content-addressed and remain gitignored; the
committed manifest/proof records hashes, counts, license, and transformation.

The rows are public synthetic agent rollouts over real issues. They are not
Syndai traffic and must never be described as organic production evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATASET = "nebius/SWE-rebench-openhands-trajectories"
REVISION = "35455389ab51bf5e2306bfd436ef72d0f98bf882"
LICENSE = "CC-BY-4.0"
ROWS_URL = "https://datasets-server.huggingface.co/rows"
API_URL = f"https://huggingface.co/api/datasets/{DATASET}"
TARGET_EVENTS = 64_000
PAGE_SIZE = 100
TRUNCATE_CHARS = 4_000
GOLDEN_COUNT = 40
TRANSFORM_VERSION = "openhands_trajectory_to_syndai_content_events_v2"
GOLDEN_GENERATOR = "deterministic_issue_to_late_diagnostic_v3"
QUERY_MAX_CHARS = 1_000
MAX_QUERY_TARGET_OVERLAP = 0.06
MIN_DISTRACTOR_EVENTS = 20
WORD_RE = re.compile(r"[a-z0-9]+")
DIAGNOSTIC_PATTERNS = (
    re.compile(r"^E\s+\S"),
    re.compile(r"^(?:ERROR|FAILED)(?:\s|:|$)", re.IGNORECASE),
    re.compile(r"^[A-Za-z_][\w.]*?(?:Error|Exception):\s+\S"),
    re.compile(r"^=+\s+.*(?:failed|error).*\s+=+$", re.IGNORECASE),
    re.compile(r"^\d+\s+(?:failed|errors?)(?:,|\s|$)", re.IGNORECASE),
)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def role_for(source_role: object) -> str | None:
    return {
        "user": "user",
        "assistant": "assistant",
        "tool": "toolResult",
    }.get(source_role)


def event_text(message: dict) -> str | None:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    calls = message.get("tool_calls")
    if message.get("role") == "assistant" and isinstance(calls, list) and calls:
        return "tool_calls: " + canonical_json(calls)
    return None


def adapt_row(source: dict, row_index: int, max_chars: int) -> tuple[dict, dict]:
    trajectory_id = source.get("trajectory_id")
    instance_id = source.get("instance_id")
    repository = source.get("repo")
    trajectory = source.get("trajectory")
    if not all(isinstance(value, str) and value for value in (trajectory_id, instance_id, repository)):
        raise ValueError(f"source row {row_index} is missing identity")
    if not isinstance(trajectory, list):
        raise ValueError(f"source row {row_index} trajectory is not a list")

    events = []
    skipped_system = skipped_empty = 0
    truncated = truncated_char_count = truncated_bytes = 0
    for source_index, message in enumerate(trajectory):
        if not isinstance(message, dict):
            raise ValueError(f"trajectory {trajectory_id} has a non-object message")
        role = role_for(message.get("role"))
        if role is None:
            if message.get("role") == "system":
                skipped_system += 1
                continue
            raise ValueError(f"trajectory {trajectory_id} has unmapped role {message.get('role')!r}")
        text = event_text(message)
        if text is None:
            skipped_empty += 1
            continue
        clipped = text[:max_chars]
        was_truncated = len(text) > max_chars
        truncated += int(was_truncated)
        if was_truncated:
            truncated_char_count += len(text) - len(clipped)
            truncated_bytes += len(text.encode()) - len(clipped.encode())
        digest = sha256_bytes(text.encode())[:16]
        events.append(
            {
                "sequence": source_index,
                "role": role,
                "text": clipped,
                "event_id": f"hf:{trajectory_id}:{source_index}:{digest}",
                "truncated": was_truncated,
            }
        )
    started_at = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=row_index)
    row = {
        "attempt_id": trajectory_id,
        "run_id": instance_id,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "repository": repository,
        "events": events,
        "public_source": {
            "dataset": DATASET,
            "revision": REVISION,
            "row_index": row_index,
            "classification": "public_synthetic_agent_rollout",
        },
    }
    return row, {
        "source_messages": len(trajectory),
        "emitted_events": len(events),
        "skipped_system": skipped_system,
        "skipped_empty": skipped_empty,
        "truncated_events": truncated,
        "truncated_chars": truncated_char_count,
        "truncated_bytes": truncated_bytes,
    }


def diagnostic_span(text: str) -> tuple[int, int, str] | None:
    offset = 0
    for line in text.splitlines(keepends=True):
        clean = line.strip()
        if 12 <= len(clean) <= 240 and any(
            pattern.search(clean) for pattern in DIAGNOSTIC_PATTERNS
        ):
            start = offset + line.find(clean)
            return start, start + len(clean), clean
        offset += len(line)
    return None


def issue_query(events: list[dict]) -> str:
    source = next((event["text"] for event in events if event["role"] == "user"), "")
    marker = "<issue_description>"
    start = source.find(marker)
    if start >= 0:
        source = source[start + len(marker) :]
        end = source.find("</issue_description>")
        if end >= 0:
            source = source[:end]
    return " ".join(source.split())[:QUERY_MAX_CHARS].strip()


def lexical_overlap(left: str, right: str) -> float:
    left_words = {word for word in WORD_RE.findall(left.lower()) if len(word) > 2}
    right_words = {word for word in WORD_RE.findall(right.lower()) if len(word) > 2}
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def build_goldens(corpus: list[dict], count: int) -> list[dict]:
    candidates = []
    for row in corpus:
        events = row["events"]
        query = issue_query(events)
        query_source = next((event for event in events if event["role"] == "user"), None)
        if not query or query_source is None:
            continue
        for event_index, event in enumerate(row["events"]):
            minimum_index = max(20, int(len(events) * 0.60))
            if event_index < minimum_index or event["role"] != "toolResult":
                continue
            span = diagnostic_span(event["text"])
            if span is None:
                continue
            previous_event = next(
                (
                    prior
                    for prior in reversed(events[:event_index])
                    if prior["role"] == "assistant"
                ),
                None,
            )
            if previous_event is None:
                continue
            start, end, answer = span
            contextual_target = previous_event["text"] + "\n" + event["text"]
            query_target_overlap = lexical_overlap(query, contextual_target)
            query_answer_overlap = lexical_overlap(query, answer)
            if (
                answer in query
                or query_target_overlap > MAX_QUERY_TARGET_OVERLAP
                or query_answer_overlap > MAX_QUERY_TARGET_OVERLAP
                or len(events) - 1 < MIN_DISTRACTOR_EVENTS
            ):
                continue
            candidates.append(
                (
                    event_index / len(events),
                    row["repository"],
                    row["attempt_id"],
                {
                    "question_type": "coding-continuity",
                    "is_abstention": False,
                    "question": (
                        f"While addressing the source issue in {row['repository']}, what exact "
                        "late diagnostic was observed?"
                    ),
                    "retrieval_query": query,
                    "question_date": row["started_at"],
                    "gold_answer": answer,
                    "provenance": [
                        {
                            "role": "answer",
                            "attempt_id": row["attempt_id"],
                            "event_sequence": event["sequence"],
                            "event_id": event["event_id"],
                            "char_start": start,
                            "char_end": end,
                            "span": answer,
                            "event_index": event_index,
                            "attempt_event_count": len(events),
                            "distractor_events": len(events) - 1,
                            "query_source_event_id": query_source["event_id"],
                            "query_target_lexical_overlap": round(query_target_overlap, 6),
                            "query_answer_lexical_overlap": round(query_answer_overlap, 6),
                        }
                    ],
                },
                )
            )
            break
    # Deepest/best-buried first, then stable source identity. Enforce repository
    # and answer diversity so repeated generic diagnostics cannot dominate.
    answer_occurrences = Counter(
        candidate["gold_answer"] for _depth, _repository, _attempt_id, candidate in candidates
    )
    goldens = []
    repositories = set()
    answers = set()
    for _depth, repository, _attempt_id, golden in sorted(
        candidates, key=lambda item: (-item[0], item[1], item[2])
    ):
        answer = golden["gold_answer"]
        if repository in repositories or answer in answers or answer_occurrences[answer] != 1:
            continue
        golden["question_id"] = f"public_code_{len(goldens) + 1:03d}"
        goldens.append(golden)
        repositories.add(repository)
        answers.add(answer)
        if len(goldens) == count:
            break
    if len(goldens) != count:
        raise ValueError(f"only {len(goldens)} adversarial diagnostic goldens available")
    if len(repositories) < min(2, count):
        raise ValueError("goldens must span at least two repositories")
    return goldens


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=180) as response:
        return json.loads(response.read())


def require_revision() -> dict:
    metadata = fetch_json(API_URL)
    if metadata.get("sha") != REVISION:
        raise RuntimeError(f"dataset revision drift: {metadata.get('sha')} != {REVISION}")
    if f"license:{LICENSE.lower()}" not in metadata.get("tags", []):
        raise RuntimeError("dataset license drift")
    return metadata


def fetch_rows(target_events: int, page_size: int, truncate_chars: int) -> tuple[list[dict], dict]:
    corpus = []
    totals = {
        "source_messages": 0,
        "emitted_events": 0,
        "skipped_system": 0,
        "skipped_empty": 0,
        "truncated_events": 0,
        "truncated_chars": 0,
        "truncated_bytes": 0,
    }
    offset = 0
    while totals["emitted_events"] < target_events:
        query = urllib.parse.urlencode(
            {"dataset": DATASET, "config": "default", "split": "train", "offset": offset, "length": page_size}
        )
        page = fetch_json(f"{ROWS_URL}?{query}")
        rows = page.get("rows")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("dataset rows exhausted before target event volume")
        for wrapped in rows:
            row_index = wrapped.get("row_idx")
            source = wrapped.get("row")
            if not isinstance(row_index, int) or not isinstance(source, dict):
                raise RuntimeError("dataset rows response is malformed")
            adapted, counts = adapt_row(source, row_index, truncate_chars)
            corpus.append(adapted)
            for key, value in counts.items():
                totals[key] += value
            if totals["emitted_events"] >= target_events:
                break
        offset += len(rows)
    return corpus, totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-events", type=int, default=TARGET_EVENTS)
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument("--truncate-chars", type=int, default=TRUNCATE_CHARS)
    parser.add_argument("--golden-count", type=int, default=GOLDEN_COUNT)
    parser.add_argument("--out-corpus", required=True)
    parser.add_argument("--out-golden", required=True)
    parser.add_argument("--out-lock", required=True)
    args = parser.parse_args()
    if min(args.target_events, args.page_size, args.truncate_chars, args.golden_count) < 1:
        parser.error("all numeric bounds must be positive")

    metadata = require_revision()
    corpus, totals = fetch_rows(args.target_events, args.page_size, args.truncate_chars)
    require_revision()
    goldens = build_goldens(corpus, args.golden_count)
    corpus_bytes = b"".join((canonical_json(row) + "\n").encode() for row in corpus)
    golden_bytes = b"".join((canonical_json(row) + "\n").encode() for row in goldens)
    corpus_path = Path(args.out_corpus)
    golden_path = Path(args.out_golden)
    atomic_write(corpus_path, corpus_bytes)
    atomic_write(golden_path, golden_bytes)
    lock = {
        "schema_version": 1,
        "golden_path": str(golden_path),
        "sha256": sha256_bytes(golden_bytes),
        "bytes": len(golden_bytes),
        "count": len(goldens),
        "strata": {"coding-continuity": len(goldens)},
        "generator": GOLDEN_GENERATOR,
        "extraction": {
            "source_dataset": DATASET,
            "source_revision": REVISION,
            "source_last_modified": metadata.get("lastModified"),
            "source_license": LICENSE,
            "classification": "public_synthetic_agent_rollout_not_production_traffic",
            "source_url": f"https://huggingface.co/datasets/{DATASET}/tree/{REVISION}",
            "source_creator": "Nebius AI",
            "source_citation": "SWE-rebench OpenHands trajectories dataset card",
            "source_license_url": "https://creativecommons.org/licenses/by/4.0/",
            "modification_notice": (
                "Roles normalized, system messages omitted with accounting, and event "
                "texts clipped at the recorded character cap."
            ),
            "transform": TRANSFORM_VERSION,
            "materializer_sha256": sha256_bytes(Path(__file__).read_bytes()),
            "target_events": args.target_events,
            "truncate_chars": args.truncate_chars,
            "sampled_attempts": len(corpus),
            "corpus_path": str(corpus_path),
            "corpus_sha256": sha256_bytes(corpus_bytes),
            "corpus_bytes": len(corpus_bytes),
            **totals,
        },
    }
    atomic_write(Path(args.out_lock), (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode())
    print(canonical_json(lock["extraction"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
