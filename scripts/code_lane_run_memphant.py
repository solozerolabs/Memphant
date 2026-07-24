#!/usr/bin/env python3
"""MemPhant engine runner for the R0 code-lane sub-bakeoff (R0-T6).

Ingests the pinned coding-events corpus into MemPhant as event-granular raw
EPISODES (real
runtime path: packaged ``memphant-server`` + ``memphant-worker`` +
``memphant-cli`` against Postgres), then calls ``/v1/recall`` (k=10,
mode=deep, budget_tokens=8192) per golden question and emits an
evidence JSONL in the ``run_reader.py``-consumable shape plus a provenance
report (span-level hit@5/hit@10 via ``gate_common.provenance_hit`` — the
SAME grading function the docs-lane runner uses).

Ingest mapping (episode, not resource — the brief's explicit choice for this
lane; documented here since the REST API has no literal "turns" field):
``POST /v1/episodes`` takes a single ``body: Option<String>`` (see
``RetainEpisodeHttpRequest`` in ``memphant-types``) — there is no turn-array
wire shape. One episode is retained per content event. Tool-result episodes
also include their nearest preceding assistant action, the smallest causal
window that lets an action-named continuity query retrieve its result without
turning a long trajectory into one oversized candidate. Other bodies contain
one ``role: text`` record. The format is the exact convention
``memphant-eval``'s ``bench_lme::session_body`` already uses for LongMemEval
turns, and the format ``memphant-core::service::segment_episode_body``
recognizes as "turn-structured" for its citation-window segmentation —
`parse_turn` there matches a `role: content` PREFIX per physical line, so a
multi-line event's continuation lines don't themselves parse as turns; this
is an accepted characteristic of the existing convention, not new here).
Event roles map explicitly to the public source taxonomy: user→user,
assistant→agent, and toolResult→tool. Trust is API-key-bound.

Isolation: each run re-execs itself through ``scripts/with_scratch_db.sh``
(``gate_runtime.reexec_through_scratch_db``) onto a fresh, migrated, per-run
scratch DB minted from ``--database-url`` (the campaign *base* server) and
dropped on exit — even if killed — with a freshly-minted tenant inside it.
No shared named DB, so the worker's global oldest-first job-claim can never
touch or be starved by foreign ``job_state`` debris. Same isolation contract
as ``gate_run_memphant.py``, the e2e probe, and the pg contract tests.

Smoke mode (``--limit-attempts``): caps the number of ingested attempts for
a tiny pass, but ALWAYS keeps every attempt referenced by the golden set's
provenance (never silently drops gold coverage) — same "coverage assertion,
never drop the gold" contract as the docs runner's ``--limit-haystack``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402
import gate_runtime as gr  # noqa: E402

# Base campaign *server* url to mint the per-run scratch DB from (see
# gate_runtime.reexec_through_scratch_db); the named DB in it is never touched.
DEFAULT_BASE_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"
CORPUS_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "coding_events_corpus.jsonl"
GOLDEN_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "coding_events_golden.jsonl"
repository_identity = gr.repository_identity
migration_identity = gr.migration_identity

def golden_lock_path(golden_path: Path) -> Path:
    return golden_path.with_name(golden_path.stem + ".lock.json")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- pure functions (unit-tested in tests/test_code_lane_run_memphant.py) ---


def build_episode_body(events: list[dict]) -> str:
    """One ``role: text`` line per content event, sequence order — the
    conversation-episode convention documented at module level."""
    return "\n".join(f"{event['role']}: {event['text']}" for event in events)


def select_ingest_attempts(
    corpus_rows: list[dict], goldens: list[dict], limit_attempts: int
) -> list[dict]:
    """Attempts to ingest for this run. ``limit_attempts <= 0`` means the
    full corpus. Otherwise: every attempt referenced by ANY golden's
    provenance is kept unconditionally (gold coverage is never dropped —
    same contract as the docs runner's ``--limit-haystack``), then filled up
    to ``limit_attempts`` with the remaining attempts in sorted attempt_id
    order for determinism."""
    if limit_attempts <= 0:
        return corpus_rows
    gold_attempt_ids = {
        entry["attempt_id"] for golden in goldens for entry in golden["provenance"]
    }
    by_id = {row["attempt_id"]: row for row in corpus_rows}
    kept = [by_id[aid] for aid in sorted(gold_attempt_ids) if aid in by_id]
    kept_ids = {row["attempt_id"] for row in kept}
    others = sorted(
        (row for row in corpus_rows if row["attempt_id"] not in kept_ids),
        key=lambda row: row["attempt_id"],
    )
    fill = max(0, limit_attempts - len(kept))
    return kept + others[:fill]


def assert_gold_coverage(ingested_rows: list[dict], goldens: list[dict]) -> None:
    ingested_ids = {row["attempt_id"] for row in ingested_rows}
    missing = sorted(
        {
            entry["attempt_id"]
            for golden in goldens
            for entry in golden["provenance"]
            if entry["attempt_id"] not in ingested_ids
        }
    )
    if missing:
        raise RuntimeError(f"gold attempt_id(s) not in ingest set: {missing}")


def verify_input_contract(
    corpus_path: Path, golden_path: Path, lock: dict
) -> tuple[list[dict], list[dict]]:
    """Verify both private inputs and every golden-to-event provenance edge.

    The old runner checked only the golden hash. That allowed a same-path
    corpus replacement to change the retrieval mechanism while preserving the
    claimed golden identity. The extraction block in the golden lock is the
    canonical corpus lock, so both sides are checked before any scratch DB or
    server process is created.
    """
    corpus_bytes = corpus_path.read_bytes()
    golden_bytes = golden_path.read_bytes()
    extraction = lock.get("extraction")
    if not isinstance(extraction, dict):
        raise RuntimeError("golden lock missing extraction corpus contract")
    corpus_sha = gc.sha256_hex(corpus_bytes)
    if corpus_sha != extraction.get("corpus_sha256"):
        raise RuntimeError("corpus sha256 mismatch")
    if len(corpus_bytes) != extraction.get("corpus_bytes"):
        raise RuntimeError("corpus byte count mismatch")
    golden_sha = gc.sha256_hex(golden_bytes)
    if golden_sha != lock.get("sha256"):
        raise RuntimeError("golden sha256 mismatch")
    if len(golden_bytes) != lock.get("bytes"):
        raise RuntimeError("golden byte count mismatch")

    corpus_rows = gc.load_goldens(corpus_path)
    goldens = gc.load_goldens(golden_path)
    if len(corpus_rows) != extraction.get("sampled_attempts"):
        raise RuntimeError("corpus attempt count mismatch")
    if len(goldens) != lock.get("count"):
        raise RuntimeError("golden count mismatch")
    attempt_ids = [row.get("attempt_id") for row in corpus_rows]
    if any(not isinstance(value, str) or not value for value in attempt_ids):
        raise RuntimeError("corpus attempt_id is missing")
    if len(attempt_ids) != len(set(attempt_ids)):
        raise RuntimeError("duplicate corpus attempt_id")

    events: dict[tuple[str, int], dict] = {}
    for row in corpus_rows:
        sequences: list[int] = []
        for event in row.get("events", []):
            sequence = event.get("sequence")
            if not isinstance(sequence, int):
                raise RuntimeError(f"invalid event sequence for {row['attempt_id']}")
            key = (row["attempt_id"], sequence)
            if key in events:
                raise RuntimeError(f"duplicate corpus event: {key}")
            events[key] = event
            sequences.append(sequence)
        if sequences != sorted(sequences):
            raise RuntimeError(f"corpus event order drift: {row['attempt_id']}")

    for golden in goldens:
        provenance = golden.get("provenance")
        if not isinstance(provenance, list) or not provenance:
            raise RuntimeError(f"golden provenance missing: {golden.get('question_id')}")
        for entry in provenance:
            key = (entry.get("attempt_id"), entry.get("event_sequence"))
            event = events.get(key)
            if event is None:
                raise RuntimeError(f"golden source event missing: {key}")
            if event.get("event_id") != entry.get("event_id"):
                raise RuntimeError(f"golden event_id pairing mismatch: {key}")
            start = entry.get("char_start")
            end = entry.get("char_end")
            span = entry.get("span")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(span, str):
                raise RuntimeError(f"golden span coordinates invalid: {key}")
            if event.get("text", "")[start:end] != span:
                raise RuntimeError(f"golden span pairing mismatch: {key}")
    return corpus_rows, goldens


def control_input_readiness(corpus_rows: list[dict], goldens: list[dict]) -> dict:
    """Report which Task-5 controls have the required immutable inputs.

    Outcome labels are intentionally never inferred from exit codes, run
    phases, or partial validator counts. A mark arm needs an explicit typed
    post-action label and its validator evidence on every training attempt.
    Likewise, retrieval QA is not a validator-backed held-out coding task.
    """
    required_attempt_fields = {
        "repository",
        "base_commit",
        "explicit_outcome",
        "outcome_evidence",
    }
    required_task_fields = {
        "held_out_task_id",
        "validator_command",
        "validator_expected",
    }
    missing = {
        field
        for field in required_attempt_fields
        if any(field not in row for row in corpus_rows)
    }
    missing.update(
        field for field in required_task_fields if any(field not in row for row in goldens)
    )
    typed = {"success", "failure", "corrected", "ignored"}
    labels_valid = all(row.get("explicit_outcome") in typed for row in corpus_rows)
    return {
        "deterministic_file_search_inputs": bool(corpus_rows and goldens),
        "verbatim_memphant_inputs": bool(corpus_rows and goldens),
        "outcome_mark_inputs": not missing.intersection(required_attempt_fields)
        and labels_valid,
        "validator_task_inputs": not missing.intersection(required_task_fields),
        "missing_fields": sorted(missing),
    }


def retrieval_query(golden: dict) -> str:
    """Return the source-derived query while keeping the grader prompt immutable."""
    query = golden.get("retrieval_query")
    if not isinstance(query, str) or not query.strip():
        raise RuntimeError(f"retrieval query missing: {golden.get('question_id')}")
    answer = golden.get("gold_answer")
    if isinstance(answer, str) and answer and answer in query:
        raise RuntimeError(f"retrieval query leaks gold answer: {golden.get('question_id')}")
    return query


def validate_recall_configuration(embed_model: str, mode: str) -> None:
    if mode == "deep" and embed_model == "off":
        raise RuntimeError("deep recall requires an embeddings model; use fast with off")


def compilation_summary(database_url: str, tenant_ids: list[str]) -> dict:
    ids = [str(uuid.UUID(value)) for value in tenant_ids]
    tenant_array = "array[" + ",".join(f"'{value}'::uuid" for value in ids) + "]"
    query = f"""
select json_build_object(
  'episodes', (select count(*) from memphant.episode where tenant_id = any({tenant_array})),
  'episodic_units', (select count(*) from memphant.memory_unit
    where tenant_id = any({tenant_array}) and kind = 'episodic'),
  'distinct_source_episodes', (select count(distinct source_episode_id)
    from memphant.memory_unit where tenant_id = any({tenant_array})
      and kind = 'episodic' and source_episode_id is not null),
  'missing_source_episodes', (select count(*) from memphant.episode episode
    where episode.tenant_id = any({tenant_array}) and not exists (
      select 1 from memphant.memory_unit unit
      where unit.tenant_id = episode.tenant_id
        and unit.source_episode_id = episode.id and unit.kind = 'episodic')),
  'done_jobs', (select count(*) from memphant.job_state
    where tenant_id = any({tenant_array}) and state = 'done'),
  'dead_jobs', (select count(*) from memphant.job_state
    where tenant_id = any({tenant_array}) and state = 'dead'),
  'pending_jobs', (select count(*) from memphant.job_state
    where tenant_id = any({tenant_array}) and state in ('queued', 'running'))
)::text
"""
    result = gr.sh([
        "psql", "--no-psqlrc", "--tuples-only", "--no-align",
        "--set", "ON_ERROR_STOP=1", database_url, "--command", query,
    ])
    if result.returncode != 0:
        raise RuntimeError(f"compilation summary failed: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout)


def validate_compilation_summary(
    summary: dict, expected_episodes: int, expected_projections: int
) -> None:
    expected = {
        "episodes": expected_episodes,
        "episodic_units": expected_projections,
        "distinct_source_episodes": expected_projections,
        "missing_source_episodes": expected_episodes - expected_projections,
        "done_jobs": expected_episodes,
        "dead_jobs": 0,
        "pending_jobs": 0,
    }
    mismatches = {
        key: {"expected": value, "actual": summary.get(key)}
        for key, value in expected.items()
        if summary.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"compiled corpus has silent drops: {mismatches}")


# --- ingest ------------------------------------------------------------------


EVENT_SOURCE_KINDS = {"user": "user", "assistant": "agent", "toolResult": "tool"}


def bind_attempt_context(client: gr.ApiClient, row: dict) -> dict:
    """Bind one independent coding attempt to one scope lane.

    All attempts remain under the same API-key tenant, while run/scope/agent
    identity stays faithful to the source trajectory instead of collapsing the
    entire public corpus into one serial scope.
    """
    return client.bind_context(
        f"code-lane:attempt:{row['attempt_id']}",
        subject_ref=f"code-lane:run:{row['run_id']}",
        actor_ref=f"code-lane:actor:{row['attempt_id']}",
        actor_kind="agent",
        scope_ref=f"code-lane:scope:{row['attempt_id']}",
        agent_node_ref=f"code-lane:agent:{row['attempt_id']}",
    )


def event_source_ref(row: dict, event: dict) -> str:
    return f"coding-event:{row['attempt_id']}:{event['sequence']}:{event['event_id']}"


def contextual_event_body(events: list[dict], event_index: int) -> str:
    event = events[event_index]
    current = f"{event['role']}: {event['text']}"
    if event["role"] != "toolResult":
        return current
    previous_action = next(
        (
            prior
            for prior in reversed(events[:event_index])
            if prior["role"] == "assistant"
        ),
        None,
    )
    if previous_action is None:
        return current
    return f"assistant: {previous_action['text']}\n{current}"


def ingest_attempt(client: gr.ApiClient, ctx: dict, row: dict) -> list[str]:
    """Retain every coding event as a minimally contextualized episode.

    Identity comes from the bound context; tenant remains API-key-bound."""
    episode_ids = []
    for event_index, event in enumerate(row["events"]):
        try:
            source_kind = EVENT_SOURCE_KINDS[event["role"]]
        except KeyError as error:
            raise RuntimeError(f"unmapped code event role: {event['role']!r}") from error
        response = client.post(
            "/v1/episodes",
            {
                **ctx,
                "source_ref": event_source_ref(row, event),
                "observed_at": row["started_at"],
                "payload": {
                    "episode": {
                        "source_kind": source_kind,
                        "body": contextual_event_body(row["events"], event_index),
                    }
                },
            },
        )
        episode_id = response.get("episode_id")
        if not episode_id:
            raise RuntimeError("event retain response omitted episode_id")
        episode_ids.append(episode_id)
    return episode_ids


ISOLATION_SENTINEL_TEXT = "tenant-b-only isolation sentinel cobalt-orchid-7319"


def ingest_isolation_sentinel(client: gr.ApiClient, ctx: dict, row: dict) -> tuple[str, str]:
    """Insert one tenant-B-only row using a source ref that also exists in A.

    This proves tenant identity, rather than external source identity, controls
    isolation. The body is intentionally distinct from the evaluation corpus.
    """
    event = row["events"][0]
    source_ref = event_source_ref(row, event)
    response = client.post(
        "/v1/episodes",
        {
            **ctx,
            "source_ref": source_ref,
            "observed_at": row["started_at"],
            "payload": {
                "episode": {
                    "source_kind": EVENT_SOURCE_KINDS[event["role"]],
                    "body": f"{event['role']}: {ISOLATION_SENTINEL_TEXT}",
                }
            },
        },
    )
    if not response.get("episode_id"):
        raise RuntimeError("isolation sentinel retain response omitted episode_id")
    return source_ref, f"{event['role']}: {ISOLATION_SENTINEL_TEXT}"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default=DEFAULT_BASE_DATABASE_URL,
        help="base campaign SERVER url to mint the per-run scratch DB from; the "
             "run uses a fresh ephemeral DB dropped on exit, never this one",
    )
    parser.add_argument("--corpus", default=str(CORPUS_PATH))
    parser.add_argument("--golden", default=str(GOLDEN_PATH))
    parser.add_argument("--out-evidence", required=True)
    parser.add_argument("--out-provenance", required=True)
    parser.add_argument(
        "--embed-model",
        default="off",
        help="MEMPHANT_EMBEDDINGS id passed into BOTH the server and worker subprocess env",
    )
    parser.add_argument("--label", default=None)
    parser.add_argument("--port", type=int, default=39413)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--budget-tokens", type=int, default=8192)
    parser.add_argument("--mode", default="fast", choices=("fast", "deep"))
    parser.add_argument(
        "--limit-attempts", type=int, default=0,
        help="0 = full corpus; otherwise a smoke cap that always keeps every gold-referenced attempt",
    )
    parser.add_argument("--server-bin", default=str(gc.MEMPHANT_ROOT / "target/release/memphant-server"))
    parser.add_argument("--worker-bin", default=str(gc.MEMPHANT_ROOT / "target/release/memphant-worker"))
    parser.add_argument("--cli-bin", default=str(gc.MEMPHANT_ROOT / "target/release/memphant-cli"))
    args = parser.parse_args()
    validate_recall_configuration(args.embed_model, args.mode)

    golden_path = Path(args.golden)
    lock_path = golden_lock_path(golden_path)
    lock = json.loads(lock_path.read_text())
    corpus_path = Path(args.corpus)
    corpus_rows, goldens = verify_input_contract(corpus_path, golden_path, lock)
    input_readiness = control_input_readiness(corpus_rows, goldens)

    # Re-exec only after immutable input + mechanism readiness checks. Invalid
    # inputs must not mint a scratch DB or start any packaged process.
    gr.reexec_through_scratch_db(args.database_url)
    args.database_url = os.environ["DATABASE_URL"]

    gr.check_embed_model_key(args.embed_model)
    label_prefix = f"[{args.label}] " if args.label else ""

    golden_sha = lock["sha256"]
    print(
        f"{label_prefix}goldens={len(goldens)} path={golden_path.name} "
        f"sha256={golden_sha[:12]} (lock verified)",
        file=sys.stderr,
    )

    ingest_rows = select_ingest_attempts(corpus_rows, goldens, args.limit_attempts)
    assert_gold_coverage(ingest_rows, goldens)
    print(
        f"{label_prefix}corpus attempts={len(corpus_rows)} ingesting={len(ingest_rows)} "
        f"(limit_attempts={args.limit_attempts or 'full'})",
        file=sys.stderr,
    )

    principals = [
        gr.provision_tenant(
            args.cli_bin, args.database_url, name_prefix=f"code-lane-gate-{name}"
        )
        for name in ("a", "b")
    ]
    print(
        f"{label_prefix}tenants={','.join(tenant for tenant, _key in principals)}",
        file=sys.stderr,
    )

    log_name = f"server-{args.label}.log" if args.label else "server.log"
    server_log_path = Path(args.out_provenance).resolve().parent / log_name
    server = gr.Server(
        args.server_bin, args.database_url, args.port, args.embed_model,
        log_path=server_log_path,
    )
    # Symmetric cleanup: start() and the ingest/recall body are both inside
    # this try so the server child is always killed on any exception path,
    # not just after a successful start (a failed start() already
    # self-terminates before raising; stop() here is then a safe no-op).
    try:
        server.start()
        clients = [gr.ApiClient(args.port, key, tenant) for tenant, key in principals]
        t0 = time.time()
        evaluation_events = 0
        evaluation_contexts = {}
        for i, row in enumerate(ingest_rows):
            context = bind_attempt_context(clients[0], row)
            evaluation_contexts[row["attempt_id"]] = context
            evaluation_events += len(ingest_attempt(clients[0], context, row))
            if (i + 1) % 25 == 0:
                print(f"{label_prefix}  ingested {i + 1}/{len(ingest_rows)}", file=sys.stderr)
        sentinel_attempt_id = goldens[0]["provenance"][0]["attempt_id"]
        sentinel_row = next(
            row for row in ingest_rows if row["attempt_id"] == sentinel_attempt_id
        )
        sentinel_context = bind_attempt_context(clients[1], sentinel_row)
        sentinel_source_ref, sentinel_body = ingest_isolation_sentinel(
            clients[1], sentinel_context, sentinel_row
        )
        isolation_sentinel_events = 1
        ingest_seconds = time.time() - t0
        print(
            f"{label_prefix}ingest done in {ingest_seconds:.1f}s "
            f"evaluation_events={evaluation_events} sentinel_events=1; draining worker...",
            file=sys.stderr,
        )
        compile_started = time.time()
        compiled = gr.drain_worker(args.worker_bin, args.database_url, args.embed_model)
        compile_seconds = time.time() - compile_started
        expected_jobs = evaluation_events + isolation_sentinel_events
        if compiled != expected_jobs:
            raise RuntimeError(
                f"compiled job count mismatch: {compiled} != {expected_jobs} events"
            )
        print(f"{label_prefix}worker drained: compiled={compiled} jobs", file=sys.stderr)
        compiled_corpus = compilation_summary(
            args.database_url, [tenant_id for tenant_id, _key in principals]
        )
        expected_projections = (
            sum(
                len(
                    {
                        contextual_event_body(row["events"], index)
                        for index in range(len(row["events"]))
                    }
                )
                for row in ingest_rows
            )
            + isolation_sentinel_events
        )
        validate_compilation_summary(
            compiled_corpus, expected_jobs, expected_projections
        )
        compiled_corpus["deduplicated_episodes"] = expected_jobs - expected_projections

        evidence_rows = []
        provenance_rows = []
        recall_started = time.time()
        for i, golden in enumerate(goldens):
            attempt_id = golden["provenance"][0]["attempt_id"]
            bodies, degraded = gr.recall_query(
                clients[0], evaluation_contexts[attempt_id], retrieval_query(golden), args.k,
                args.budget_tokens, args.mode
            )
            evidence_rows.append(gc.evidence_row(golden, bodies, args.k))
            provenance_rows.append(
                {
                    "question_id": golden["question_id"],
                    "question_type": golden["question_type"],
                    "returned_items": len(bodies),
                    "degraded": degraded,
                    "hit_at_5": gc.provenance_hit(golden, bodies, 5),
                    "hit_at_10": gc.provenance_hit(golden, bodies, min(10, args.k)),
                }
            )
            if (i + 1) % 10 == 0:
                print(f"{label_prefix}  recalled {i + 1}/{len(goldens)}", file=sys.stderr)
        recall_seconds = time.time() - recall_started

        isolation_golden = goldens[0]
        other_bodies, other_degraded = gr.recall_query(
            clients[1], sentinel_context, retrieval_query(isolation_golden),
            args.k, args.budget_tokens, args.mode,
        )
        if other_degraded or gc.provenance_hit(isolation_golden, other_bodies, args.k):
            raise RuntimeError("two-tenant negative recall leaked owner evidence")
        owner_sentinel_bodies, owner_sentinel_degraded = gr.recall_query(
            clients[0], evaluation_contexts[sentinel_attempt_id], ISOLATION_SENTINEL_TEXT,
            args.k, args.budget_tokens, args.mode,
        )
        if owner_sentinel_degraded or sentinel_body in owner_sentinel_bodies:
            raise RuntimeError("two-tenant negative recall leaked sentinel evidence")

        gc.write_jsonl(Path(args.out_evidence), evidence_rows)
        n = len(provenance_rows)
        r5 = sum(r["hit_at_5"] for r in provenance_rows) / n if n else 0.0
        r10 = sum(r["hit_at_10"] for r in provenance_rows) / n if n else 0.0
        portable_argv, portable_command = gr.portable_command(sys.argv, gc.MEMPHANT_ROOT)
        report = {
            "engine": "memphant",
            "lane": "code",
            "runtime": "memphant-server episode ingest (role-prefixed turn body) + /v1/recall",
            "embed_model": args.embed_model,
            "label": args.label,
            "golden_path": str(golden_path),
            "corpus_path": str(corpus_path),
            "database_url_db": args.database_url.rsplit("/", 1)[-1],
            "k": args.k,
            "recall_mode": args.mode,
            "budget_tokens": args.budget_tokens,
            "ingested_attempts": len(ingest_rows),
            "ingested_events": evaluation_events + isolation_sentinel_events,
            "evaluation_events": evaluation_events,
            "contextualized_tool_result_events": sum(
                1
                for row in ingest_rows
                for index, event in enumerate(row["events"])
                if event["role"] == "toolResult"
                and any(
                    prior["role"] == "assistant" for prior in row["events"][:index]
                )
            ),
            "context_window": "nearest_preceding_assistant_for_tool_results",
            "isolation_sentinel_events": isolation_sentinel_events,
            "compiled_jobs": compiled,
            "compiled_corpus": compiled_corpus,
            "corpus_attempts": len(corpus_rows),
            "limit_attempts": args.limit_attempts,
            "golden_sha256": golden_sha,
            "corpus_sha256": lock["extraction"]["corpus_sha256"],
            "golden_lock_sha256": sha256_file(lock_path),
            "golden_count": n,
            "runtime_identity": {
                "repository": gr.repository_identity(gc.MEMPHANT_ROOT),
                "migrations": gr.migration_identity(gc.MEMPHANT_ROOT),
                "runner_sha256": sha256_file(Path(__file__).resolve()),
                "gate_common_sha256": sha256_file(Path(gc.__file__).resolve()),
                "gate_runtime_sha256": sha256_file(Path(gr.__file__).resolve()),
                "server_sha256": sha256_file(Path(args.server_bin).resolve()),
                "worker_sha256": sha256_file(Path(args.worker_bin).resolve()),
                "cli_sha256": sha256_file(Path(args.cli_bin).resolve()),
                "argv": portable_argv,
                "command": portable_command,
            },
            "timings": {
                "ingest_seconds": ingest_seconds,
                "compile_seconds": compile_seconds,
                "recall_seconds": recall_seconds,
                "events_per_ingest_second": evaluation_events / ingest_seconds,
            },
            "tenancy": {
                "api_tenants": 2,
                "tenant_a_attempt_contexts": len(evaluation_contexts),
                "tenant_a_evaluation_events": evaluation_events,
                "tenant_b_isolation_sentinel_events": isolation_sentinel_events,
                "identical_binding_refs": True,
                "identical_source_ref_sentinel": True,
                "sentinel_source_ref": sentinel_source_ref,
                "cross_tenant_negative_question_id": isolation_golden["question_id"],
                "owner_to_sentinel_negative_passed": True,
                "sentinel_to_owner_negative_passed": True,
            },
            "control_input_readiness": input_readiness,
            "recall_at_5": r5,
            "recall_at_10": r10,
            "per_question": provenance_rows,
        }
        Path(args.out_provenance).write_text(json.dumps(report, indent=2) + "\n")
        print(
            f"{label_prefix}done: R@5={r5:.3f} R@10={r10:.3f} n={n} "
            f"evidence={args.out_evidence} provenance={args.out_provenance}",
            file=sys.stderr,
        )
    finally:
        server.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
