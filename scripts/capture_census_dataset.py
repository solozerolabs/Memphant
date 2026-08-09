#!/usr/bin/env python3
"""Capture-gate Stage 1a census dataset: read-only mine of Syndai's dev DB.

Assembles a per-attempt record from ``syndai.coding_execution_attempt_events``
joined to ``syndai.coding_runs`` so the census/validation kill gate (Task B1,
see ``.superpowers/sdd/2026-08-09-memphant-capture-gates/task-B1-brief.md``)
has something to ask "do correction -> later-same-scope chains exist at all?"
of.

Schema (verified 2026-08-09 against the live dev DB — do NOT assume any
earlier draft's shape):

* ``syndai.coding_execution_attempt_events``:
  ``{id, coding_run_id, attempt_id, sequence, event_type, subtype, payload
  (JSONB), occurred_at}``. Turn text lives in ``payload``; a *user turn* is
  ``event_type='user'``. ``assistant`` and structured
  ``message_*``/``turn_*``/``tool_execution_*`` events also exist.
* ``syndai.coding_runs``: ``{id, coding_repository_id, current_phase,
  pr_status, terminal_summary, validation_iteration}``. Outcome is derived
  from ``current_phase`` — there is no ``validator_status`` column.

Access posture (read-only, enforced not merely intended): the only SQL this
script ever issues is the single ``SELECT`` in ``fetch_raw_attempts`` below,
and every statement in the session runs under
``default_transaction_read_only = on`` via ``PGOPTIONS`` — a Postgres session
setting, so a stray write would be rejected by the server itself, not merely
omitted by this script. The connection string comes from ``CENSUS_DATABASE_URL``
(falling back to ``DATABASE_URL``); no secret is ever printed. The operator
supplies it via ``doppler run --config dev -- ...`` — this script never calls
doppler itself.

Output: ``benchmarks/data/capture_census.jsonl`` (gitignored — carries raw
turn text) and ``benchmarks/data/capture_census.stats.json`` (committed —
counts only, zero text).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402

EVENTS_TABLE = "syndai.coding_execution_attempt_events"
RUNS_TABLE = "syndai.coding_runs"

OUT_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "capture_census.jsonl"
STATS_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "capture_census.stats.json"

# One read-only SELECT: every event (any event_type) for every attempt that
# has at least one event, grouped by attempt, joined to its run for scope
# (coding_repository_id) and outcome (current_phase). started_at/ended_at are
# the attempt's own event timeline (min/max occurred_at across ALL of its
# events, not just user ones) so an attempt with zero user turns still gets a
# real span. normalize_attempt() below does the event_type=='user' filtering
# and payload-text extraction — kept out of SQL so it stays unit-testable
# without a DB.
SELECT_SQL = f"""
select coalesce(json_agg(t order by t.attempt_id), '[]'::json) from (
  select
    e.attempt_id,
    e.coding_run_id,
    r.coding_repository_id as repo_scope,
    r.current_phase,
    min(e.occurred_at) as started_at,
    max(e.occurred_at) as ended_at,
    json_agg(
      json_build_object('sequence', e.sequence, 'event_type', e.event_type, 'payload', e.payload)
      order by e.sequence
    ) as events
  from {EVENTS_TABLE} e
  join {RUNS_TABLE} r on r.id = e.coding_run_id
  group by e.attempt_id, e.coding_run_id, r.coding_repository_id, r.current_phase
) t
""".strip()


# --- pure functions (unit-tested in scripts/tests/test_capture_census_dataset.py) --


def phase_to_outcome(phase: str | None) -> str:
    """coding_runs.current_phase -> binary outcome. completed -> passed;
    failed/cancelled/anything else -> not_passed (there is no validator_status
    column to fall back on)."""
    return "passed" if phase == "completed" else "not_passed"


def _extract_text(payload) -> str | None:
    """payload may arrive as a dict OR a JSON string depending on driver.
    Returns None (skip) when there is no extractable non-empty text.

    Two payload shapes are handled: the flat ``{"text": "..."}`` shape, and
    the real live-DB shape confirmed 2026-08-09 against the Syndai dev DB —
    a Claude-Code-transcript-style envelope,
    ``{"message": {"content": [{"type": "text", "text": "..."}, ...]}}``.
    Only ``type == "text"`` content items count as extractable turn text;
    ``type == "tool_result"`` items (1,430 of the 1,434 real
    ``event_type='user'`` rows, confirmed by direct query) are the agent's own
    tool-result echo fed back as a 'user' role turn, not human-authored text,
    and are deliberately excluded here."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    flat_text = payload.get("text")
    if isinstance(flat_text, str) and flat_text.strip():
        return flat_text
    content = (payload.get("message") or {}).get("content")
    if isinstance(content, list):
        texts = [
            item["text"]
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ]
        if texts:
            return "\n\n".join(texts)
    return None


def normalize_attempt(raw: dict) -> dict:
    """One raw grouped-by-attempt row (as produced by ``fetch_raw_attempts``,
    or the equivalent fixture shape in the unit tests) -> the census record:
    ``{attempt_id, run_id, repo_scope, started_at, ended_at, outcome,
    user_turns:[{sequence, text}]}``. Keeps only ``event_type=='user'`` events
    and skips any with no extractable text, in ``sequence`` order (the SQL
    already orders by sequence; sorted again here so the contract holds even
    if a caller hands in unordered events)."""
    user_turns = []
    for event in raw.get("events") or []:
        if event.get("event_type") != "user":
            continue
        text = _extract_text(event.get("payload"))
        if text is None:
            continue
        user_turns.append({"sequence": event["sequence"], "text": text})
    user_turns.sort(key=lambda turn: turn["sequence"])
    return {
        "attempt_id": raw["attempt_id"],
        "run_id": raw["coding_run_id"],
        "repo_scope": raw.get("repo_scope"),
        "started_at": raw.get("started_at"),
        "ended_at": raw.get("ended_at"),
        "outcome": phase_to_outcome(raw.get("current_phase")),
        "user_turns": user_turns,
    }


# --- DB I/O (read-only) -------------------------------------------------------


def fetch_raw_attempts(database_url: str, psql_bin: str = "psql") -> list[dict]:
    """Runs the ONE select above and returns the parsed per-attempt rows.
    Never writes: the session is forced read-only server-side via PGOPTIONS,
    and this is the only statement ever issued."""
    env = dict(os.environ)
    env["PGOPTIONS"] = "-c default_transaction_read_only=on"
    result = subprocess.run(
        [psql_bin, database_url, "-v", "ON_ERROR_STOP=1", "-At", "-c", SELECT_SQL],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        # stderr from psql can echo the connection string; never print it.
        raise RuntimeError(f"psql failed with exit code {result.returncode}")
    return json.loads(result.stdout)


# --- main ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument("--out-stats", default=str(STATS_PATH))
    parser.add_argument("--psql-bin", default="psql")
    args = parser.parse_args()

    database_url = os.environ.get("CENSUS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "CENSUS_DATABASE_URL (or DATABASE_URL) is unset. Run under: "
            "doppler run --config dev -- python3 scripts/capture_census_dataset.py",
            file=sys.stderr,
        )
        return 2

    raw_attempts = fetch_raw_attempts(database_url, args.psql_bin)
    records = [normalize_attempt(raw) for raw in raw_attempts]

    with_scope = sum(1 for r in records if r["repo_scope"] is not None)
    with_outcome = sum(1 for raw in raw_attempts if raw.get("current_phase") is not None)
    user_turns_total = sum(len(r["user_turns"]) for r in records)

    out_path = Path(args.out)
    gc.write_jsonl(out_path, records)
    corpus_bytes = out_path.read_bytes()

    stats = {
        "source": f"{EVENTS_TABLE} join {RUNS_TABLE} (read-only SELECT)",
        "attempts": len(records),
        "with_scope": with_scope,
        "with_outcome": with_outcome,
        "user_turns": user_turns_total,
        "outcome_passed": sum(1 for r in records if r["outcome"] == "passed"),
        "outcome_not_passed": sum(1 for r in records if r["outcome"] == "not_passed"),
        "corpus_sha256": gc.sha256_hex(corpus_bytes),
        "corpus_bytes": len(corpus_bytes),
    }
    Path(args.out_stats).write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")

    print(
        f"attempts={len(records)} with_scope={with_scope} "
        f"with_outcome={with_outcome} user_turns={user_turns_total}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
