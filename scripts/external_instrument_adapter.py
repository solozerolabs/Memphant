#!/usr/bin/env python3
"""One retrieval-only adapter for externally-sourced memory instruments.

Deliberately ONE script for every adopted external instrument rather than a
harness per benchmark (ponytail). An instrument contributes exactly two
things -- a loader that turns its shipped rows into (a) ingest *units* and
(b) *probes* carrying deterministic gold unit ids -- and inherits the shared
runtime: scratch DB, packaged server/worker/cli, ``bind_context`` identity,
``/v1/episodes`` retain, worker drain, ``/v1/recall``, hit@k scoring.

$0 by construction. There is no reader and no judge here, and every gold
label is derived by a rule from a field the instrument itself ships -- never
by a model call -- so a score is reproducible offline and costs nothing.

Source attribution (reuse policy 26 D-2026-07-30) lives in the per-instrument
lock under ``benchmarks/manifests/``; this runner verifies the pinned sha256
of every source file, or of the whole source tree, before it touches a
database.

Scoring: a probe is scored by ``citation_episode_id`` on each recalled
item, matched against the episode id that ``POST /v1/episodes`` returned for
the gold unit. Not by substring. Recall returns *citation windows* over an
episode, so a window can begin past a body's first line -- any tag-in-body
scheme silently undercounts exactly the long units this lane cares about.
The id path has no such bias, and it lets the source text be ingested
verbatim with no marker injected into it at all.

Instruments
-----------
``ama_bench``   AMA-Bench open-ended QA, SOFTWARE (SWE-bench/OpenHands) slice.
                Unit = one trajectory turn. Probe = a QA pair whose question
                names an explicit ``step N``; gold = trajectory turn N. Only
                step-anchored questions are used, because only those carry a
                turn-level gold that is derivable without a judge.

``memorycode``  MemoryCode correction retention. Unit = one session's text.
                Probe = a coding convention that was later overwritten; gold
                = the session that most recently states it, distractor = the
                superseded earlier session. Supersession groups are formed by
                stripping quoted literals from ``topic`` (``always start
                function names with 'a_'`` and ``... with 'y_'`` collapse to
                one group), which uses only shipped fields.

``clawarena``   ClawArena, DERIVED gold only -- see load_clawarena. Ships no
                retrieval ground truth of its own; its native scoring needs a
                reader and a live agent writing files, so it is not reachable
                at $0. Included to prove the pin is runnable, not to score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_runtime as gr  # noqa: E402

MANIFESTS = Path(__file__).resolve().parent.parent / "benchmarks" / "manifests"
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
STEP = re.compile(r"\b[Ss]tep (\d+)")
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def observed_at(index: int) -> str:
    return (EPOCH + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# Loaders. Each returns list[group]; a group is one isolated memory subject:
#   {"group_id": str, "units": [...], "probes": [...]}
# unit  = {"unit_id", "source_kind", "body"}
# probe = {"probe_id", "query", "gold_unit_ids", "distractor_unit_ids"}
# --------------------------------------------------------------------------


def load_ama_bench(source: Path) -> list[dict]:
    groups = []
    with source.open() as handle:
        for line in handle:
            episode = json.loads(line)
            if episode["domain"] != "SOFTWARE":
                continue
            trajectory = episode["trajectory"]
            gid = f"ama-ep{episode['episode_id']}"
            units = []
            for turn in trajectory:
                uid = f"{gid}-t{turn['turn_idx']}"
                body = (
                    f"agent: {turn['action']}\n"
                    f"tool: {turn['observation']}"
                )
                units.append({"unit_id": uid, "source_kind": "agent", "body": body})
            probes = []
            for pair in episode["qa_pairs"]:
                steps = sorted({int(s) for s in STEP.findall(pair["question"])})
                steps = [s for s in steps if 0 <= s < len(trajectory)]
                if not steps:
                    continue
                probes.append(
                    {
                        "probe_id": pair["question_uuid"],
                        "query": pair["question"],
                        "kind": pair["type"],
                        "gold_unit_ids": [f"{gid}-t{s}" for s in steps],
                        "distractor_unit_ids": [],
                    }
                )
            if probes:
                groups.append({"group_id": gid, "units": units, "probes": probes})
    return groups


def load_memorycode(source: Path) -> list[dict]:
    import pyarrow.parquet as pq

    groups = []
    for row in pq.read_table(source).to_pylist():
        sessions = json.loads(row["sessions"])
        gid = f"mc-{row['id']}"
        units = [
            {
                "unit_id": f"{gid}-s{i}",
                "source_kind": "user",
                "body": session["text"],
                # ORACLE FIELD. Read by the `preference` arm ONLY, never by the
                # `memphant` or `lexical` arms. These are the supersession group
                # keys the GOLD RULE itself is built from, so an arm that
                # consumes them is not comparable to the lexical control -- see
                # `ingest_group_preference`.
                "declarations": sorted(
                    {
                        QUOTED.sub("<X>", topic).strip()
                        for kind, topic in zip(session["type"], session["topic"])
                        if kind in ("instruction-add", "instruction-update")
                    }
                ),
            }
            for i, session in enumerate(sessions)
        ]
        statements = defaultdict(list)
        for i, session in enumerate(sessions):
            for kind, topic in zip(session["type"], session["topic"]):
                if kind in ("instruction-add", "instruction-update"):
                    statements[QUOTED.sub("<X>", topic).strip()].append((i, topic))
        probes = []
        for key, occurrences in statements.items():
            if len(occurrences) < 2:
                continue
            current_index, current_topic = occurrences[-1]
            probes.append(
                {
                    "probe_id": f"{gid}-{hashlib.sha256(key.encode()).hexdigest()[:12]}",
                    "query": key.replace("<X>", "").strip(),
                    "kind": "correction_retention",
                    "current_topic": current_topic,
                    "gold_unit_ids": [f"{gid}-s{current_index}"],
                    "distractor_unit_ids": [
                        f"{gid}-s{i}" for i, _ in occurrences[:-1]
                    ],
                }
            )
        if probes:
            groups.append({"group_id": gid, "units": units, "probes": probes})
    return groups


FILENAME = re.compile(r"[A-Za-z0-9_./-]+\.(?:md|json|csv|py|sql|txt|yaml|db)")
CLAWARENA_AGENT = "claude-code"


def load_clawarena(source: Path) -> list[dict]:
    """ClawArena, retrieval-only.

    CAVEAT, and it is the whole story: ClawArena ships NO retrieval ground
    truth. Its own scoring is (a) a reader picking ``\\bbox{}`` options and
    (b) shell commands checking files a live agent wrote into a workspace --
    neither reachable at $0. The gold here is therefore DERIVED BY US: the
    workspace files a round's own feedback/eval prose names by filename. That
    covers 385 of 1801 rounds, so it is a coverage-biased proxy for a
    construct the benchmark does not itself measure. Read any number from
    this loader as a smoke signal, never as a ClawArena score.

    ``source`` is the scenario root, ``data/clawarena`` inside the mirror.
    """
    root = source
    groups = []
    for questions_path in sorted(root.glob("eval/*/questions.json")):
        scenario = questions_path.parent.name
        workspace = root / CLAWARENA_AGENT / "workspaces" / scenario
        if not workspace.is_dir():
            continue
        units = []
        by_basename = {}
        for path in sorted(p for p in workspace.rglob("*") if p.is_file()):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if not text.strip():
                continue
            uid = f"claw-{scenario}-{path.relative_to(workspace).as_posix()}"
            units.append({"unit_id": uid, "source_kind": "resource", "body": text})
            by_basename[path.name] = uid
        if not units:
            continue
        probes = []
        for round_ in json.loads(questions_path.read_text())["rounds"]:
            prose = (
                round_["question"]
                + json.dumps(round_.get("feedback") or {})
                + json.dumps(round_.get("eval") or {})
            )
            named = {Path(m).name for m in FILENAME.findall(prose)}
            gold = sorted({by_basename[n] for n in named if n in by_basename})
            if not gold:
                continue
            probes.append(
                {
                    "probe_id": f"{scenario}-{round_['id']}",
                    "query": round_["question"],
                    "kind": f"derived_evidence_{round_['type']}",
                    "gold_unit_ids": gold,
                    "distractor_unit_ids": [],
                }
            )
        if probes:
            groups.append({"group_id": f"claw-{scenario}", "units": units, "probes": probes})
    return groups


LOADERS = {
    "ama_bench": load_ama_bench,
    "clawarena": load_clawarena,
    "memorycode": load_memorycode,
}


# --------------------------------------------------------------------------


def sha256_tree(root: Path) -> str:
    """Aggregate hash of a source TREE: sha256 over `relpath\\0filehash\\n`
    lines in path order. A Track U bank broke this week because its source
    tree mutated mid-run; a directory-shaped instrument needs the same
    immutability check a single file gets."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0" + sha256_file(path).encode() + b"\n")
    return digest.hexdigest()


def verify_source(instrument: str, source: Path, lock_path: Path) -> dict:
    lock = json.loads(lock_path.read_text())
    if source.is_dir():
        expected = lock["dataset"]["aggregate_sha256"]
        actual = sha256_tree(source)
    else:
        expected = lock["dataset"]["files"][lock["dataset"]["primary_file"]]["sha256"]
        actual = sha256_file(source)
    if actual != expected:
        raise SystemExit(
            f"{instrument}: source sha256 {actual} != pinned {expected} "
            f"({lock_path.name}); the mirror mutated -- refusing to run"
        )
    return lock


def episode_hits(items: list[dict], unit_ids: list[str], episode_of: dict) -> list[int]:
    """Ranks (0-based) at which any of ``unit_ids`` is cited.

    Matched on the ids ``POST /v1/episodes`` returned for that unit -- exact,
    and unaffected by where a citation window happens to start inside a long
    body. An episode retain yields a ``citation_episode_id``; a direct-unit
    retain (the `preference` arm) yields ``unit_id``s and no episode at all, so
    both fields are checked. `episode_of` maps one instrument unit id to the
    SET of server ids that stand for it.
    """
    wanted: set = set()
    for uid in unit_ids:
        value = episode_of.get(uid)
        if value is None:
            continue
        wanted |= set(value) if isinstance(value, (set, list, tuple)) else {value}
    return [
        rank
        for rank, item in enumerate(items)
        if item.get("citation_episode_id") in wanted or item.get("unit_id") in wanted
    ]


def lexical_rank(group: dict, query: str, k: int) -> list[str]:
    """Unit ids ranked by the BM25 the repo already ships.

    Reuses ``code_lane_run_deterministic.bm25_search`` verbatim (Okapi, k1=1.2,
    b=0.75) rather than reimplementing the scorer, so the preference lane's
    lexical control and the code lane's are literally the same function. That
    helper ranks *documents* and returns their ``body``; here the body slot
    carries the unit id, which is what a retrieval score needs. Ties break on
    ascending session index -- a pure lexical baseline has no notion of time,
    and breaking ties toward the newer session would hand it a recency prior it
    has not earned.

    The haystack is the group's own units, mirroring the per-instance bound
    context MemPhant recalls through; the code lane scopes its BM25 to one
    attempt for the same reason.
    """
    import code_lane_run_deterministic as det

    documents = [
        {
            "attempt_id": "",
            "sequence": index,
            "body": unit["unit_id"],
            "tokens": det.tokens(unit["body"]),
        }
        for index, unit in enumerate(group["units"])
    ]
    return det.bm25_search(documents, query, k)


def score_group_lexical(group: dict, args) -> list[dict]:
    """Arm B. No database, no server, no network -- $0 by construction."""
    rows = []
    for probe in group["probes"]:
        ranked = lexical_rank(group, probe["query"], args.k)
        identity = {unit_id: unit_id for unit_id in ranked}
        items = [{"citation_episode_id": unit_id} for unit_id in ranked]
        rows.append(
            probe_row(group, probe, items, identity)
        )
    return rows


def probe_row(group: dict, probe: dict, items: list[dict], episode_of: dict,
              degraded: bool = False) -> dict:
    """One scored probe, arm-agnostic.

    The three outcome buckets are mutually exclusive and exhaustive by
    construction, which is the point: an arm that returns nothing scores 0 on
    BOTH ``appropriate_application`` and ``misapplication``, so a suppression
    win cannot be read as an application win. ``neither_returned`` carries that
    mass explicitly instead of letting it hide in one rate's complement.
    """
    gold = episode_hits(items, probe["gold_unit_ids"], episode_of)
    stale = episode_hits(items, probe["distractor_unit_ids"], episode_of)
    gold_rank = gold[0] if gold else None
    stale_rank = stale[0] if stale else None
    current_wins = gold_rank is not None and (
        stale_rank is None or gold_rank < stale_rank
    )
    stale_wins = stale_rank is not None and (
        gold_rank is None or stale_rank < gold_rank
    )
    return {
        "group_id": group["group_id"],
        "probe_id": probe["probe_id"],
        "kind": probe["kind"],
        "returned": len(items),
        # Kept so a scoring hazard can be CHECKED rather than assumed. A
        # close-generation mints a valid-time-closed "remainder" carrying the
        # PRIOR body but attributed to the superseding retain's response, so if
        # any remainder ever reached a recall result the preference arm's
        # identity map would be wrong. `remainders_recalled` in the report
        # intersects these ids with the database's valid-closed set.
        "returned_unit_ids": [item.get("unit_id") for item in items if item.get("unit_id")],
        "degraded": degraded,
        "gold_rank": gold_rank,
        "stale_rank": stale_rank,
        "hit_at_1": gold_rank == 0,
        "hit_at_k": gold_rank is not None,
        # Primary endpoint. Named for the direction it measures.
        "appropriate_application": current_wins,
        "misapplication": stale_wins,
        "neither_returned": gold_rank is None and stale_rank is None,
        # Retained under its adoption-pass name so the smoke is comparable.
        "stale_outranks_current": stale_wins,
    }


def ingest_group(client, group: dict) -> tuple[dict, dict, int]:
    """Retain every unit. Returns (context, unit_id -> episode_id, deduped).

    A retain whose ``dedup.matched`` is true folds into an existing episode
    and enqueues no compile job; counting those is what lets the caller
    assert the worker drained completely instead of guessing.
    """
    context = client.bind_context(
        f"external-{group['group_id']}",
        subject_ref=group["group_id"],
        actor_ref=f"{group['group_id']}-runner",
        scope_ref=group["group_id"],
        agent_node_ref="external-instrument-adapter",
    )
    episode_of = {}
    deduped = 0
    for index, unit in enumerate(group["units"]):
        response = client.post(
            "/v1/episodes",
            gr.episode_retain_payload(
                context,
                source_ref=unit["unit_id"],
                observed_at=observed_at(index),
                source_kind=unit["source_kind"],
                body=unit["body"],
            ),
        )
        episode_of[unit["unit_id"]] = response["episode_id"]
        deduped += bool((response.get("dedup") or {}).get("matched"))
    return context, episode_of, deduped


def ingest_group_preference(client, group: dict) -> tuple[dict, dict, int]:
    """Arm P. Every instruction-bearing session is retained as a **preference
    unit** with an explicit subject key; every other session stays an episode.

    READ THIS BEFORE READING ANY NUMBER THIS ARM PRODUCES. The subject key is
    the instrument's own supersession group key -- the SAME rule the gold labels
    are built from. This arm is therefore **oracle-keyed**, and its score is
    NOT comparable to the lexical control and is not evidence of retrieval
    quality. It exists to answer one mechanism question the 2026-08-01 run could
    not: with a correct chain, does the state machine actually fire end-to-end
    through Postgres, and does recall then use the state?

    It is oracle-keyed because the honest alternative was measured and failed.
    A deterministic body-derived key needs no gold and no model, and on this
    corpus it does not work: over the 1063 gold groups, a shared key derived
    from the session text is recovered by 0.008 of groups (quoted-literal-
    stripped content-word set) and at best 0.208 (the single content word
    preceding the quoted literal). Sessions restate a convention in paraphrase
    ("Remember when I mentioned that we use a specific naming convention...").
    Deriving the key is an extraction problem, and extraction is a `reflect`
    stage-1 LLM job, which this lane's $0 budget forbids.
    """
    context = client.bind_context(
        f"external-{group['group_id']}",
        subject_ref=group["group_id"],
        actor_ref=f"{group['group_id']}-runner",
        scope_ref=group["group_id"],
        agent_node_ref="external-instrument-adapter",
    )
    identity: dict[str, set] = {}
    episodes = 0
    deduped = 0
    for index, unit in enumerate(group["units"]):
        declarations = unit.get("declarations") or []
        if not declarations:
            response = client.post(
                "/v1/episodes",
                gr.episode_retain_payload(
                    context,
                    source_ref=unit["unit_id"],
                    observed_at=observed_at(index),
                    source_kind=unit["source_kind"],
                    body=unit["body"],
                ),
            )
            identity[unit["unit_id"]] = {response["episode_id"]}
            episodes += 1
            deduped += bool((response.get("dedup") or {}).get("matched"))
            continue
        ids: set = set()
        for declaration in sorted(declarations):
            key = hashlib.sha256(declaration.encode()).hexdigest()[:16]
            response = client.post(
                "/v1/episodes",
                {
                    **context,
                    "source_ref": f"{unit['unit_id']}#{key}",
                    "observed_at": observed_at(index),
                    "payload": {
                        "unit": {
                            "kind": "preference",
                            "fact_key": f"preference:{key}",
                            "predicate": "prefers",
                            "body": unit["body"],
                            "confidence": 1.0,
                        }
                    },
                },
            )
            minted = set(response.get("unit_ids") or [])
            if not minted:
                raise RuntimeError(
                    f"REFUSING TO SCORE: preference retain {unit['unit_id']}#{key} "
                    "minted no unit -- the declaration was silently dropped"
                )
            ids |= minted
        identity[unit["unit_id"]] = ids
    return context, identity, episodes, deduped


def score_group(client, context, episode_of: dict, group: dict, args) -> list[dict]:
    rows = []
    for probe in group["probes"]:
        response = client.post(
            "/v1/recall",
            {
                **context,
                "query": probe["query"],
                "limit": args.k,
                "budget_tokens": args.budget_tokens,
                "mode": args.mode,
            },
        )
        rows.append(
            probe_row(
                group,
                probe,
                response.get("items", []),
                episode_of,
                degraded=bool(response.get("degraded", False)),
            )
        )
    return rows


def summarise(rows: list[dict], k: int) -> dict:
    n = len(rows)
    if not n:
        return {"probes": 0}
    graded = [r for r in rows if r["stale_rank"] is not None or r["gold_rank"] is not None]
    return {
        "probes": n,
        "instances": len({r["group_id"] for r in rows}),
        "k": k,
        "hit_at_1": sum(r["hit_at_1"] for r in rows) / n,
        "hit_at_k": sum(r["hit_at_k"] for r in rows) / n,
        "degraded": sum(r["degraded"] for r in rows),
        "with_any_unit_returned": len(graded),
        # Both directions by name, plus the bucket that holds neither, so the
        # three sum to 1 and a suppression win is visible as a suppression win.
        "appropriate_application_rate": sum(r["appropriate_application"] for r in rows) / n,
        "misapplication_rate": sum(r["misapplication"] for r in rows) / n,
        "neither_returned_rate": sum(r["neither_returned"] for r in rows) / n,
        "latest_state_wins": sum(r["appropriate_application"] for r in rows) / n,
        "stale_outranks_current": sum(r["stale_outranks_current"] for r in rows),
    }


DIAGNOSTIC_QUERIES = {
    # Does supersession mint edges at all on this corpus?
    "memory_edge_by_kind":
        "select kind, count(*) from memphant.memory_edge group by kind order by kind",
    # Are retired units still live? A superseded unit with a NULL
    # transaction_to would still pass bitemporal recall.
    "memory_unit_by_state":
        "select state, count(*) from memphant.memory_unit group by state order by state",
    "memory_unit_by_kind":
        "select kind, count(*) from memphant.memory_unit group by kind order by kind",
    "superseded_with_open_transaction":
        "select count(*) from memphant.memory_unit "
        "where state = 'superseded' and transaction_to is null",
    # Supersedence requires an EXPLICIT subject key; auto content-hash keys are
    # documented never to supersede. This says which kind this corpus produced.
    "units_with_predicate":
        "select (predicate is not null) as has_predicate, count(*) "
        "from memphant.memory_unit group by 1 order by 1",
    "units_with_fact_key":
        "select (fact_key is not null) as has_fact_key, count(*) "
        "from memphant.memory_unit group by 1 order by 1",
    # The absent hot/cold plane: expected 'hot' for every episode, forever.
    "episode_by_retention_tier":
        "select retention_tier, count(*) from memphant.episode "
        "group by retention_tier order by retention_tier",
    "episodes": "select count(*) from memphant.episode",
    "jobs_remaining":
        "select count(*) from memphant.job_state where state in ('queued','running')",
}


def psql_scalar(database_url: str, sql: str) -> int:
    """One integer, read as the BENCH SUPERUSER credential on ``database_url``.

    Deliberately not the worker's own connection. `20260730_004` made the
    served pools assume `memphant_worker`, so FORCE RLS applies to them and a
    queue-wide count with no tenant bound matched zero rows -- the worker then
    reported a single batch as a completed drain (401 queued -> `completed=256`
    with 145 still queued). `20260730_005` restores the worker's own count via
    a security-definer function, but this bench never relies on that: it asks
    the database itself, on the unrestricted bench credential, and fails closed.
    """
    result = gr.sh([
        "psql", "--no-psqlrc", "--tuples-only", "--no-align",
        "--set", "ON_ERROR_STOP=1", database_url, "--command", sql,
    ])
    if result.returncode != 0:
        raise RuntimeError(f"bench verification query failed: {result.stderr.strip()[:300]}")
    return int(result.stdout.strip())


def verify_corpus_compiled(database_url: str, expected_episodes: int) -> dict:
    """Fail closed unless the DATABASE says the whole corpus is compiled.

    A partially compiled corpus is the one failure that would manufacture this
    lane's headline result -- a superseded session outranking the current one
    because the current one was never compiled. So this asserts, as the bench
    superuser and never from a self-report:

    1. no job is ``queued`` or ``running``;
    2. no job is ``failed`` or ``dead``;
    3. the episode count matches what retain accepted;
    4. EVERY episode produced at least one memory unit.

    (4) is the real corpus-compiled assertion. A job-count alone cannot see an
    episode whose job completed without minting anything.
    """
    counts = {
        "pending_jobs": psql_scalar(
            database_url,
            "select count(*) from memphant.job_state "
            "where state in ('queued','running')",
        ),
        "failed_jobs": psql_scalar(
            database_url,
            "select count(*) from memphant.job_state where state in ('failed','dead')",
        ),
        "episodes": psql_scalar(database_url, "select count(*) from memphant.episode"),
        "episodes_with_units": psql_scalar(
            database_url,
            "select count(distinct source_episode_id) from memphant.memory_unit "
            "where source_episode_id is not null",
        ),
        "memory_units": psql_scalar(database_url, "select count(*) from memphant.memory_unit"),
        "expected_episodes": expected_episodes,
    }
    if counts["pending_jobs"]:
        raise RuntimeError(
            f"REFUSING TO SCORE: {counts['pending_jobs']} jobs still queued/running "
            "after drain -- the corpus is only partially compiled"
        )
    if counts["failed_jobs"]:
        raise RuntimeError(
            f"REFUSING TO SCORE: {counts['failed_jobs']} failed/dead compile jobs"
        )
    if counts["episodes"] != expected_episodes:
        raise RuntimeError(
            f"REFUSING TO SCORE: {counts['episodes']} episodes in the database "
            f"!= {expected_episodes} distinct units retained"
        )
    if counts["episodes_with_units"] != counts["episodes"]:
        raise RuntimeError(
            f"REFUSING TO SCORE: {counts['episodes'] - counts['episodes_with_units']} "
            "episodes compiled to zero memory units -- partial corpus"
        )
    return counts


def count_recalled_remainders(database_url: str, rows: list[dict]) -> int:
    """How many recalled items were valid-time-closed rows.

    A close-generation re-INSERTs the prior body as a valid-closed rectangle
    (§7.3a), and that row is returned in the SUPERSEDING retain's `unit_ids`.
    If such a row ever reached a recall result, the preference arm's identity
    map would credit the wrong session. Expected 0 -- `bitemporally_recallable`
    closes a valid-closed row for a live `valid_at` -- but expected is not
    measured, so this counts it from the database.
    """
    result = gr.sh([
        "psql", "--no-psqlrc", "--tuples-only", "--no-align",
        "--set", "ON_ERROR_STOP=1", database_url, "--command",
        "select id from memphant.memory_unit where valid_to is not null",
    ])
    if result.returncode != 0:
        raise RuntimeError(f"remainder query failed: {result.stderr.strip()[:300]}")
    closed = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return sum(
        1
        for row in rows
        for unit_id in row.get("returned_unit_ids", [])
        if unit_id in closed
    )


def collect_diagnostics(database_url: str) -> dict:
    """Trace evidence, read from the scratch DB before it is dropped.

    Exploratory by prereg: mechanism, not a tested claim.
    """
    out = {}
    for name, sql in DIAGNOSTIC_QUERIES.items():
        result = gr.sh([
            "psql", "--no-psqlrc", "--tuples-only", "--no-align",
            "--set", "ON_ERROR_STOP=1", database_url, "--command", sql,
        ])
        out[name] = (
            result.stdout.strip().splitlines()
            if result.returncode == 0
            else f"query failed: {result.stderr.strip()[:300]}"
        )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", required=True, choices=sorted(LOADERS))
    parser.add_argument(
        "--arm",
        default="memphant",
        choices=("memphant", "lexical", "preference"),
        help="memphant = live recall; lexical = the repo's BM25 control over "
             "the same units and queries (no DB, no server, no network); "
             "preference = ORACLE-KEYED write-path mechanism arm, NOT "
             "comparable to either of the other two (see "
             "ingest_group_preference)",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="memphant arm only: dump supersession/state/tier counts from the "
             "scratch DB into the report BEFORE it is dropped",
    )
    parser.add_argument("--source", required=True, help="pinned mirror file")
    parser.add_argument("--lock", default=None, help="manifest lock (default: derived)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit-groups", type=int, default=0)
    parser.add_argument("--limit-probes-per-group", type=int, default=0)
    parser.add_argument("--limit-units-per-group", type=int, default=0)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--budget-tokens", type=int, default=8192)
    parser.add_argument("--mode", default="fast", choices=("fast", "deep"))
    parser.add_argument("--port", type=int, default=39471)
    parser.add_argument("--embed-model", default=None)
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "MEMPHANT_BASE_DATABASE_URL",
            "postgres://memphant:memphant@localhost:5432/memphant",
        ),
    )
    root = Path(__file__).resolve().parent.parent
    parser.add_argument("--server-bin", default=str(root / "target/release/memphant-server"))
    parser.add_argument("--worker-bin", default=str(root / "target/release/memphant-worker"))
    parser.add_argument("--cli-bin", default=str(root / "target/release/memphant-cli"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source = Path(args.source).expanduser().resolve()
    lock_path = Path(args.lock) if args.lock else MANIFESTS / f"{args.instrument}.lock.json"
    lock = verify_source(args.instrument, source, lock_path)

    groups = LOADERS[args.instrument](source)
    print(
        f"[{args.instrument}] loaded groups={len(groups)} "
        f"units={sum(len(g['units']) for g in groups)} "
        f"probes={sum(len(g['probes']) for g in groups)}",
        file=sys.stderr,
    )
    if args.limit_groups:
        groups = groups[: args.limit_groups]
    for group in groups:
        if args.limit_probes_per_group:
            group["probes"] = group["probes"][: args.limit_probes_per_group]
        if args.limit_units_per_group:
            keep = {u for p in group["probes"] for u in p["gold_unit_ids"]}
            keep |= {u for p in group["probes"] for u in p["distractor_unit_ids"]}
            head = group["units"][: args.limit_units_per_group]
            names = {u["unit_id"] for u in head}
            group["units"] = head + [
                u for u in group["units"] if u["unit_id"] in keep - names
            ]
        missing = ({u for p in group["probes"] for u in p["gold_unit_ids"]}
                   - {u["unit_id"] for u in group["units"]})
        if missing:
            raise SystemExit(f"gold unit dropped by limiting: {sorted(missing)[:3]}")

    started = time.time()
    diagnostics = None
    if args.arm == "lexical":
        # Arm B short-circuits every runtime: no scratch DB, no server, no
        # worker, no embedder, no network. Same probes, same queries, same k.
        rows = [row for group in groups for row in score_group_lexical(group, args)]
        ingested = sum(len(group["units"]) for group in groups)
        compiled = 0
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return write_report(
            args, lock, lock_path, source, rows, groups, ingested, compiled,
            started, out_path, diagnostics
        )

    # Inputs verified against the lock before any database is minted.
    gr.reexec_through_scratch_db(args.database_url)
    args.database_url = os.environ["DATABASE_URL"]
    gr.check_embed_model_key(args.embed_model)

    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, args.database_url, name_prefix=f"ext-{args.instrument}"
    )
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    server = gr.Server(
        args.server_bin,
        args.database_url,
        args.port,
        args.embed_model,
        log_path=out_path.parent / f"server-{args.instrument}.log",
    )
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        bound = {}
        ingested = 0
        deduped = 0
        episodes_retained = 0
        for group in groups:
            if args.arm == "preference":
                context, episode_of, group_episodes, group_deduped = (
                    ingest_group_preference(client, group)
                )
                episodes_retained += group_episodes
            else:
                context, episode_of, group_deduped = ingest_group(client, group)
                episodes_retained += len(group["units"])
            bound[group["group_id"]] = (context, episode_of)
            ingested += len(group["units"])
            deduped += group_deduped
        compiled = gr.drain_worker(args.worker_bin, args.database_url, args.embed_model)
        # Only EPISODE retains enqueue a reflect job. A direct-unit retain (the
        # preference arm) compiles inline inside the retain transaction, so it
        # is never part of the queue accounting.
        if compiled != episodes_retained - deduped:
            raise RuntimeError(
                f"compiled {compiled} != episodes {episodes_retained} - deduped {deduped}"
            )
        # And then do not take even that on trust: ask the database.
        compilation = verify_corpus_compiled(args.database_url, episodes_retained - deduped)
        print(f"[corpus verified] {json.dumps(compilation, sort_keys=True)}", file=sys.stderr)
        rows = []
        for group in groups:
            context, episode_of = bound[group["group_id"]]
            rows += score_group(client, context, episode_of, group, args)
        # Read the trace while the scratch DB still exists.
        if args.diagnostics:
            diagnostics = collect_diagnostics(args.database_url)
            diagnostics["compilation_verified"] = compilation
            diagnostics["remainders_recalled"] = count_recalled_remainders(
                args.database_url, rows
            )
    finally:
        server.stop()

    return write_report(
        args, lock, lock_path, source, rows, groups, ingested, compiled,
        started, out_path, diagnostics
    )


def write_report(args, lock, lock_path, source, rows, groups, ingested,
                 compiled, started, out_path, diagnostics) -> int:
    report = {
        "instrument": args.instrument,
        "arm": args.arm,
        "lock": {"path": lock_path.name, "sha256": sha256_file(lock_path)},
        "source": {
            "path": str(source),
            "sha256": lock["dataset"]["aggregate_sha256"]
            if source.is_dir()
            else lock["dataset"]["files"][lock["dataset"]["primary_file"]]["sha256"],
        },
        "license": lock["license"],
        "attribution": lock["attribution"],
        "recall": {"k": args.k, "mode": args.mode, "budget_tokens": args.budget_tokens,
                   "embed_model": args.embed_model}
        if args.arm in ("memphant", "preference")
        else {"k": args.k, "mechanism": "Okapi BM25 k1=1.2 b=0.75, instance-scoped, "
              "code_lane_run_deterministic.bm25_search"},
        "scale": {
            "groups": len(groups),
            "units_ingested": ingested,
            "compiled_jobs": compiled,
            "wall_seconds": round(time.time() - started, 1),
        },
        "paid_model_calls": 0,
        "summary": summarise(rows, args.k),
        "diagnostics": diagnostics,
        "rows": rows,
    }
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
