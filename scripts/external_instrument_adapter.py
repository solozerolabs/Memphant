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
from collections import Counter, defaultdict
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


def observed_at(index: float) -> str:
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


def load_memorycode_asof(source: Path) -> list[dict]:
    """MemoryCode, RE-CUT so the gold is not the latest declaration (S2 §4).

    THE CORPUS IS UNCHANGED AND SO IS INGEST. Only the question moves: for a
    key declared at sessions j_1 < ... < j_m, today's cut emits ONE probe with
    gold = j_m. This cut emits m-1 probes, one per non-final declaration r,
    asking the question **as of a time while j_r's declaration was in force**:

        valid_at       = midpoint(observed_at(j_r), observed_at(j_{r+1}))
        gold           = j_r
        distractors    = every OTHER declaring session, including j_{r+1}..j_m

    So `max(observed_at)` returns j_m -- a distractor -- on every probe, by
    construction, and every earlier declaration becomes the gold of its own
    probe (which is what makes wrong retirement expensive; see §4.3).

    WHY THE MIDPOINT AND NOT `observed_at(j_r) + eps`, WHICH IS WHAT S2 §4.1
    SPECIFIES. Measured on the pinned parquet before this loader was written:
    under the eps policy the LATEST SESSION OF ANY KIND at or before valid_at
    is the gold on **3599 of 3599** probes -- the trivial rule
    `max(observed_at <= t)` over the raw session stream is then the gold rule
    exactly, and the re-cut reproduces the very identification it exists to
    break, one axis over. Under the midpoint policy that collapses to
    **667 of 3599 (18.5%)**, because 2932 probes have at least one
    non-declaring session between j_r and t. The midpoint is also the
    semantically honest choice: "as of a time while rule r was in force"
    rather than "one epsilon after it was stated".

    WHAT THE MIDPOINT STILL DOES NOT FIX, stated here rather than discovered
    downstream: among the DECLARING sessions of the key, `max(observed_at <=
    t)` is still exactly j_r for every probe, because MemoryCode has zero
    re-assertions (measured) so a key's declarations form a monotone chain.
    Any rule that truncates at t and then takes the newest therefore ranks the
    gold above every distractor it returns. The gold-vs-stale endpoint
    (`appropriate_application` / latest-state-wins) is consequently
    **saturated by the trivial as-of rule up to pool coverage**, and that is a
    property of the corpus, not of this loader. `hit_at_1` is the endpoint
    where the baselines genuinely separate. Both are reported.
    """
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
                # Bounded ingest (S2 §4.4): valid time is put on the SESSION
                # axis, not on wall-clock ingest order, so a probe's valid_at
                # is computable from the loader alone.
                "valid_from": observed_at(i),
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
            indices = [i for i, _ in occurrences]
            digest = hashlib.sha256(key.encode()).hexdigest()[:12]
            for rank in range(len(indices) - 1):
                here, nxt = indices[rank], indices[rank + 1]
                if nxt <= here:
                    # Two declarations of one key inside ONE session: there is
                    # no interval during which the earlier one was uniquely in
                    # force, so the probe is degenerate and is dropped.
                    continue
                probes.append(
                    {
                        "probe_id": f"{gid}-{digest}-r{rank}",
                        "query": key.replace("<X>", "").strip(),
                        "kind": "asof_retention",
                        "valid_at": observed_at((here + nxt) / 2.0),
                        "asof_rank": rank,
                        "declarations": len(indices),
                        "intervening_sessions": nxt - here - 1,
                        "gold_unit_ids": [f"{gid}-s{here}"],
                        "distractor_unit_ids": [
                            f"{gid}-s{i}" for i in indices if i != here
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
    "memorycode_asof": load_memorycode_asof,
}

# Instruments whose probes carry a `valid_at` and whose units carry a
# `valid_from`. Threaded explicitly rather than sniffed per row, so an
# instrument that forgets one of the two fails loudly instead of silently
# degrading to an unbounded run.
ASOF_INSTRUMENTS = frozenset({"memorycode_asof"})


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


def trivial_rank(group: dict, probe: dict, rule: str, k: int) -> list[str]:
    """THE TRIVIAL-BASELINE SET, preregistered in full before any cell existed.

    S2 §3.3's finding, measured on TempLAMA's own shipped files: a corpus that
    defeats `max(timestamp)` (correct on 55.5% of rows) can still be defeated
    by `most_frequent_answer` (70.7%). Beating the recency control is therefore
    not evidence of anything until the OTHER cheap rules have been run too. All
    six below rank the same haystack the substrate arms recall over -- the
    instance's own sessions, the scope `bind_context` binds -- so they are
    comparable at the same stage, and all are $0: no database, no server, no
    network, no model call.

    ``t`` is the probe's ``valid_at``; unit index i has ``observed_at(i)``, so
    ``index <= t_minutes`` is exactly ``observed_at <= valid_at``.
    """
    units = group["units"]
    n = len(units)
    order = list(range(n))
    cutoff = None
    if probe.get("valid_at"):
        # valid_at is EPOCH + minutes; recover the minute offset.
        cutoff = (
            datetime.fromisoformat(probe["valid_at"].replace("Z", "+00:00")) - EPOCH
        ).total_seconds() / 60.0

    if rule == "constant":
        # CONSTANT / MAJORITY CONTROL. Session order, first session first.
        # Carries no time signal, no query signal and no key signal; it is the
        # floor every other rule must clear to have shown anything at all.
        ranked = order
    elif rule == "first":
        ranked = order
    elif rule == "recency":
        # `max(observed_at)`. The A-recency rule with no truncation. WRONG ON
        # EVERY PROBE BY CONSTRUCTION on this cut -- it returns j_m.
        ranked = sorted(order, reverse=True)
    elif rule == "asof_truncation":
        # `max(observed_at <= t)`. THE HONEST BASELINE (S2 §4.2): a ~20-line
        # read rule, no substrate. Truncate, then newest-first.
        ranked = sorted((i for i in order if i <= cutoff), reverse=True)
    elif rule == "bm25":
        # The repo's own BM25, no time signal at all. Same function the
        # `lexical` arm uses.
        by_id = {unit["unit_id"]: i for i, unit in enumerate(units)}
        ranked = [by_id[u] for u in lexical_rank(group, probe["query"], n)]
    elif rule == "bm25_asof":
        # RELEVANCE THEN TRUNCATION -- the strongest non-oracle trivial rule
        # available, and the one an engineer would actually ship: retrieve by
        # relevance, drop anything asserted after t.
        by_id = {unit["unit_id"]: i for i, unit in enumerate(units)}
        ranked = [
            by_id[u]
            for u in lexical_rank(group, probe["query"], n)
            if by_id[u] <= cutoff
        ]
    elif rule == "mode_oracle":
        # `most_frequent_answer` -- S2 §3.3's named trap. ORACLE-KEYED and
        # therefore NOT comparable to any other arm here: it has to know which
        # sessions declare the probe's key, which is the gold structure
        # itself. It is run because the prereg says run every cheap rule that
        # could explain a win, and reported with this label attached.
        declaring = [
            i
            for i, unit in enumerate(units)
            if any(
                d.replace("<X>", "").strip() == probe["query"]
                for d in (unit.get("declarations") or [])
            )
        ]
        counts = Counter(units[i]["body"] for i in declaring)
        ranked = sorted(
            declaring, key=lambda i: (-counts[units[i]["body"]], i)
        ) + [i for i in order if i not in set(declaring)]
    else:
        raise SystemExit(f"unknown trivial rule {rule!r}")
    return [units[i]["unit_id"] for i in ranked[:k]]


def score_group_trivial(group: dict, args) -> list[dict]:
    """A trivial-rule arm. No database, no server, no network -- $0."""
    rows = []
    for probe in group["probes"]:
        ranked = trivial_rank(group, probe, args.trivial_rule, args.k)
        rows.append(
            probe_row(
                group,
                probe,
                [{"citation_episode_id": unit_id} for unit_id in ranked],
                {unit_id: unit_id for unit_id in ranked},
            )
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
        # As-of stratifiers, carried so the analysis can condition on them
        # without re-deriving them from the corpus. Absent on the original cut.
        "valid_at": probe.get("valid_at"),
        "asof_rank": probe.get("asof_rank"),
        "declarations": probe.get("declarations"),
        "intervening_sessions": probe.get("intervening_sessions"),
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


def retain_keyed_unit(client, context, unit, index, key, body, identity,
                      key_last_session, bounded):
    """One keyed preference retain, with BOUNDED valid time and correct
    remainder attribution. Shared by Arm P and Arm K so the two cannot drift.

    THE ATTRIBUTION IS THE WHOLE POINT AND IT IS NEW HERE. On the original cut
    a supersede's remainder was never recallable (`remainders_recalled: 0` in
    every banked run), so last-writer-wins on the identity map was safe. On the
    as-of cut it is exactly WRONG. Bounded supersession closes
    `[observed_at(j_r), inf)` against a candidate `[observed_at(j_next), inf)`,
    `interval_intersection` yields `[observed_at(j_next), inf)`, and
    `correction_rectangles` mints the historical remainder
    `[observed_at(j_r), observed_at(j_next))` **carrying j_r's body** -- which
    is the GOLD of the as-of probe at any t inside that interval. That
    remainder is returned in the SUPERSEDING retain's `unit_ids`. Crediting it
    to j_next, as the old map did, would attribute every correct as-of answer
    to a distractor and score the bitemporal arm near zero for a bookkeeping
    reason. So the leading ids of a supersede response are credited to the
    session that last asserted this key, and only the trailing id -- the
    compiler appends the caller's own unit last -- to this session.
    """
    payload = {
        "kind": "preference",
        "fact_key": f"preference:{key}",
        "predicate": "prefers",
        "body": body,
        "confidence": 1.0,
    }
    if bounded:
        payload["valid_from"] = unit["valid_from"]
    response = client.post(
        "/v1/episodes",
        {
            **context,
            "source_ref": f"{unit['unit_id']}#{key}",
            "observed_at": observed_at(index),
            "payload": {"unit": payload},
        },
    )
    minted = list(response.get("unit_ids") or [])
    if not minted:
        raise RuntimeError(
            f"REFUSING TO SCORE: keyed retain {unit['unit_id']}#{key} minted no "
            "unit -- the declaration was silently dropped"
        )
    prior = key_last_session.get(key)
    if len(minted) > 1:
        if prior is None:
            raise RuntimeError(
                f"REFUSING TO SCORE: retain {unit['unit_id']}#{key} minted "
                f"{len(minted)} units with no prior assertion on the key -- the "
                "remainder cannot be attributed and the identity map would lie"
            )
        identity.setdefault(prior, set()).update(minted[:-1])
    identity.setdefault(unit["unit_id"], set()).add(minted[-1])
    key_last_session[key] = unit["unit_id"]
    return len(minted) - 1


def ingest_group_preference(client, group: dict, bounded: bool = False) -> tuple:
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
    key_last_session: dict[str, str] = {}
    episodes = 0
    deduped = 0
    remainders = 0
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
            identity.setdefault(unit["unit_id"], set()).add(response["episode_id"])
            episodes += 1
            deduped += bool((response.get("dedup") or {}).get("matched"))
            continue
        for declaration in sorted(declarations):
            remainders += retain_keyed_unit(
                client, context, unit, index,
                hashlib.sha256(declaration.encode()).hexdigest()[:16],
                unit["body"], identity, key_last_session, bounded,
            )
    return context, identity, episodes, deduped, remainders


def ingest_group_derived(
    client, group: dict, args
) -> tuple[dict, dict, int, int, int]:
    """Arm K. IDENTICAL to Arm P in every respect except where the key comes
    from — which is the whole point of running it.

    Arm P keys on ``unit["declarations"]``: the instrument's own supersession
    group key, the same field the gold labels are built from, which is why P is
    ``decisional: false`` and a ceiling rather than a result. Arm K keys on a
    rule in ``scripts/measure_key_recovery.py`` that reads the session BODY and
    nothing else. Same kind, same predicate, same retain shape, same recall,
    same scorer — so K is comparable to A' and B at the same pipeline stage on
    the same haystack, and the A'→K→P triple prices exactly one variable: key
    quality.

    The rule is chosen on the offline frontier
    (``docs/build-log/artifacts/2026-08-01-key-production/recovery.json``), not
    here, and it is passed by name so the artifact records which one ran.
    """
    import measure_key_recovery as kr

    rule = kr.BODY_RULES[args.derived_rule]
    context = client.bind_context(
        f"external-{group['group_id']}",
        subject_ref=group["group_id"],
        actor_ref=f"{group['group_id']}-runner",
        scope_ref=group["group_id"],
        agent_node_ref="external-instrument-adapter",
    )
    identity: dict[str, set] = {}
    key_last_session: dict[str, str] = {}
    episodes = 0
    deduped = 0
    remainders = 0
    for index, unit in enumerate(group["units"]):
        # GOLD-INDEPENDENCE, enforced rather than promised: the oracle field is
        # deleted from the unit before the rule can see it.
        unit.pop("declarations", None)
        derived = sorted(rule(unit["body"]))
        if not derived:
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
            identity.setdefault(unit["unit_id"], set()).add(response["episode_id"])
            episodes += 1
            deduped += bool((response.get("dedup") or {}).get("matched"))
            continue
        for derived_key in derived:
            remainders += retain_keyed_unit(
                client, context, unit, index,
                hashlib.sha256(derived_key.encode()).hexdigest()[:16],
                unit["body"], identity, key_last_session, args.bounded,
            )
    return context, identity, episodes, deduped, remainders


WORD = re.compile(r"[a-z0-9_]+")
# Deliberately tiny: the discriminating vocabulary here is code-convention
# nouns, and an aggressive stoplist would delete the very tokens ("names",
# "variables") that separate one convention from another.
STOPWORDS = frozenset(
    "the a an and or of to in is are be for that this it you your we our with "
    "on as at by from not but if then so do does did have has had will would "
    "can could should i me my he she they them his her their there here what "
    "when where which who how all any some no nor own same than too very just "
    "was were been being into out up down over under again more most other "
    "such only own s t don now".split()
)


def content_words(text: str) -> set:
    return {w for w in WORD.findall(text.lower()) if len(w) > 2 and w not in STOPWORDS}


def jaccard(left: set, right: set) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


# --------------------------------------------------------------------------
# THE DIRECTIVE-SENTENCE SIMILARITY UNIT (S1).
#
# These four constants and two functions were written for
# `measure_key_recovery.py` and were moved here, not copied, when the live B1
# extractor needed the same unit. `measure_key_recovery` imports them back
# under their original names. One definition, so an offline census and a live
# arm can never be measuring two different objects.
#
# GOLD-INDEPENDENT BY CONSTRUCTION: every input is `body` text. No `topic`, no
# `declarations`, no probe, no gold field of any kind is reachable from here.
# --------------------------------------------------------------------------

# A quoted literal in *prose*. The gold rule's `QUOTED` runs over the clean
# `topic` field; a body is full of apostrophes ("I'm", "team's"), so the quote
# delimiters must be bounded by non-alphanumerics or every contraction pair
# becomes a "literal". Verified against the corpus: this finds 'q_' and not
# "'s a pleasure to finally meet you. I'".
DIRECTIVE_LITERAL = re.compile(
    r"(?<![A-Za-z0-9])['\"`]([^'\"`\n]{1,40})['\"`](?![A-Za-z0-9])"
)
DIRECTIVE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
DIRECTIVE_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

# Ordinary English function words plus the conversational filler this corpus is
# padded with. Chosen once, before any rule was scored, and shared by every
# rule so no rule is advantaged by its own stoplist.
DIRECTIVE_STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those there here it its it's
    is are was were be been being am do does did doing done have has had having
    i you he she we they me him her us them my your his our their mine yours
    to of in on at by for with from into onto about as up out over under again
    further once all any both each few more most other some such no nor not only
    own same so too very can will just should now s t don don't shall may might
    must would could ll re ve d m o y ain aren couldn didn doesn hadn hasn haven
    isn ma mightn mustn needn shan shouldn wasn weren won wouldn
    please make sure let lets going go want need needs like also going forward
    ok okay yes yeah sounds good great thanks thank hi hello sure well right
    when where what which who whom while because since during before after
    """.split()
)


def directive_content_words(text: str) -> list[str]:
    return [
        w
        for w in (m.group(0).lower() for m in DIRECTIVE_WORD.finditer(text))
        if w not in DIRECTIVE_STOPWORDS and len(w) > 1
    ]


def literal_sentences(body: str) -> list[tuple[str, str]]:
    """(sentence, sentence-with-quoted-literals-blanked) for each sentence
    carrying a quoted literal. That is this corpus's directive shape; a rule
    family anchored on it is the honest deterministic ceiling."""
    out = []
    for raw in DIRECTIVE_SENTENCE_SPLIT.split(body):
        s = raw.strip()
        if not s or not DIRECTIVE_LITERAL.search(s):
            continue
        out.append((s, DIRECTIVE_LITERAL.sub(" <X> ", s)))
    return out


_DIRECTIVE_SETS: dict[str, tuple[frozenset, ...]] = {}


def directive_sets(body: str) -> tuple[frozenset, ...]:
    """Content-word sets of a body's directive sentences, literals blanked.

    Blanking the quoted literal is deliberate and is the same choice every
    key-production rule makes: two sessions that state the SAME convention
    about DIFFERENT literals ("prefix with 'q_'" then "prefix with 'x_'") are
    the co-declaring pair supersession exists to catch, and leaving the literal
    in would push them apart rather than together.

    Cached on the body itself: recall hands the same prior body back many
    times, and re-tokenising ~2,300 characters per candidate is the only part
    of this unit that is not free.
    """
    hit = _DIRECTIVE_SETS.get(body)
    if hit is None:
        hit = tuple(
            frozenset(directive_content_words(blanked))
            for _, blanked in literal_sentences(body)
        )
        _DIRECTIVE_SETS[body] = hit
    return hit


def structured_similarity(left_body: str, right_body: str, unit: str) -> float:
    """The B1 extractor's similarity, under either unit.

    ``body``     -- whole-body content-word Jaccard. B1's original unit. A
                    MemoryCode session is ~2,300 characters of mentor small
                    talk wrapped around one directive, so this is dominated by
                    filler every session shares: live median 0.194, max 0.521.
    ``sentence`` -- max content-word Jaccard over all directive-sentence pairs
                    of the two bodies. Same information, filler removed.

    Deterministic, offline, no model call. The max is over every pair, which a
    live extractor can compute at write time without knowing which pair is
    right.
    """
    if unit == "body":
        return jaccard(content_words(left_body), content_words(right_body))
    if unit != "sentence":
        raise ValueError(f"unknown structured similarity unit: {unit!r}")
    return max(
        (
            jaccard(left, right)
            for left in directive_sets(left_body)
            for right in directive_sets(right_body)
        ),
        default=0.0,
    )


def _fires(session_id: str, rate: float) -> bool:
    """Deterministic per-session draw. Reproducible from the id alone, so a
    rate-matched ablation is a re-run rather than a re-roll."""
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    draw = int(hashlib.sha256(f"fire:{session_id}".encode()).hexdigest(), 16) % 10**9
    return draw < rate * 10**9


def ingest_group_structured(client, group: dict, args) -> tuple:
    """Arm S. THE B1 STRUCTURED-STATE EXTRACTOR.

    Every session is retained as a **preference unit whose subject key is a
    content hash of its own body** -- a self-identity, not a matching key. It
    is derived with zero knowledge of any prior session and can therefore never
    coincide with one, which is exactly the point: this arm does not produce a
    key that has to match anything. Supersession is expressed by naming the
    exact prior unit id instead.

    The extractor is handed the candidate's RETRIEVAL CONTEXT and nothing else:
    one `/v1/recall` against the scope the session is about to be written into,
    using the session body as the query. It considers the single top-ranked
    live preference unit, and names it as a supersession target iff the
    content-word Jaccard between the two bodies clears ``--structured-threshold``.

    WHY A THRESHOLD AND NOT PURE RANK. Rank alone always returns something, so
    an unconditional top-1 rule closes one generation per session and collapses
    a group to a single live unit -- every mid-group gold would then be retired
    and unrecallable. The cutoff is the only free parameter, and it is chosen
    on a DEV SLICE that the confirmatory analysis excludes; a threshold above
    1.0 disables supersession entirely and is the arm's own no-op control.

    GOLD-INDEPENDENT. Unlike ``ingest_group_preference`` this reads no
    ``declarations`` field, no ``topic``, no probe. Its only inputs are session
    bodies and MemPhant's own recall. It is therefore comparable to the lexical
    control.

    CEILING, stated rather than hidden: one unit per SESSION. Arm P mints one
    per declaration, and a session that states several conventions cannot be
    split by anything this arm can see. Upgrade path is a mutation-time hook
    that segments a session before keying it (spec 04 §13, and the 92-93% band
    in 2026-07-31-preference-writepath.md §7).
    """
    context = client.bind_context(
        f"external-{group['group_id']}",
        subject_ref=group["group_id"],
        actor_ref=f"{group['group_id']}-runner",
        scope_ref=group["group_id"],
        agent_node_ref="external-instrument-adapter",
    )
    identity: dict[str, set] = {}
    session_of: dict[str, str] = {}
    ledger: list[dict] = []
    # The caller's own live units, in mint order. Only supersession removes a
    # row from it, and the only supersessions in this scope are this arm's own,
    # so it is an exact mirror of the live set without a database round trip.
    live: list[str] = []
    proposed = 0
    considered = 0
    for index, unit in enumerate(group["units"]):
        target = None
        if args.structured_ablation != "none":
            # ABLATION. Same edge RATE, deliberately uninformative target: the
            # semantic test is not consulted at all. `recency` names the most
            # recent live prior unit, `random` a uniformly drawn one. Both are
            # gated by a deterministic per-session draw so the firing rate can
            # be matched to the measured rate of the arm under test.
            if live and _fires(unit["unit_id"], args.structured_fire_rate):
                target = (
                    live[-1]
                    if args.structured_ablation == "recency"
                    else live[
                        int(
                            hashlib.sha256(
                                f"pick:{unit['unit_id']}".encode()
                            ).hexdigest(),
                            16,
                        )
                        % len(live)
                    ]
                )
        elif index:
            recalled = client.post(
                "/v1/recall",
                {
                    **context,
                    "query": unit["body"][: args.structured_query_chars],
                    "limit": args.structured_pool,
                    "budget_tokens": args.budget_tokens,
                    "mode": args.mode,
                },
            )
            for item in recalled.get("items", []):
                if item.get("kind") != "preference":
                    continue
                considered += 1
                # THE SIMILARITY UNIT (S1). `body` is B1's original whole-body
                # Jaccard; `sentence` compares the best-matching directive
                # sentences instead. Both scores are banked on every arm so a
                # later calibration never has to re-run an arm to see the other
                # distribution.
                body_similarity = structured_similarity(
                    unit["body"], item.get("body", ""), "body"
                )
                sentence_similarity = structured_similarity(
                    unit["body"], item.get("body", ""), "sentence"
                )
                similarity = (
                    body_similarity
                    if args.structured_unit == "body"
                    else sentence_similarity
                )
                if similarity >= args.structured_threshold:
                    target = item["unit_id"]
                # CALIBRATION LEDGER. Recorded on every arm including the
                # threshold>1.0 no-op control, so the cutoff can be chosen on a
                # dev slice from the extractor's OWN candidate distribution
                # rather than from all-pairs statistics that the recall ranker
                # never sees.
                ledger.append(
                    {
                        "session": unit["unit_id"],
                        "target_session": session_of.get(item["unit_id"]),
                        # `jaccard` is the DECIDING score under this arm's unit
                        # -- the field name is kept so every downstream reader
                        # of B1's ledger keeps working -- and both units are
                        # recorded alongside it.
                        "jaccard": round(similarity, 6),
                        "body_jaccard": round(body_similarity, 6),
                        "sentence_jaccard": round(sentence_similarity, 6),
                        "named": target is not None,
                    }
                )
                # Top-ranked live preference unit ONLY. Scanning further down
                # the pool would let a weak match anywhere in the top-k retire
                # a rule, which is a different (and much looser) rule than the
                # one preregistered.
                break
        payload = {
            "kind": "preference",
            "fact_key": f"b1:{hashlib.sha256(unit['body'].encode()).hexdigest()[:32]}",
            "predicate": "prefers",
            "body": unit["body"],
            "confidence": 1.0,
        }
        if target is not None:
            payload["target_unit_ids"] = [target]
            proposed += 1
            live.remove(target)
        response = client.post(
            "/v1/episodes",
            {
                **context,
                "source_ref": unit["unit_id"],
                "observed_at": observed_at(index),
                "payload": {"unit": payload},
            },
        )
        minted_ids = list(response.get("unit_ids") or [])
        # A supersede also mints the historical remainder, which carries the
        # PRIOR body and the PRIOR key. The compiler appends the caller's own
        # unit last, so the tail is ours; the shape is asserted rather than
        # assumed, because naming a remainder later would fail the write (its
        # valid interval has already closed) and the failure would be remote
        # from its cause.
        expected = 2 if target is not None else 1
        if len(minted_ids) > expected:
            raise RuntimeError(
                f"REFUSING TO SCORE: retain {unit['unit_id']} minted "
                f"{len(minted_ids)} units, expected at most {expected}"
            )
        if minted_ids:
            live.append(minted_ids[-1])
        minted = set(minted_ids)
        if not minted:
            raise RuntimeError(
                f"REFUSING TO SCORE: structured retain {unit['unit_id']} minted no "
                "unit -- the session was silently dropped"
            )
        identity[unit["unit_id"]] = minted
        for unit_id in minted:
            # The remainder a supersede also mints carries the PRIOR body, and
            # attributing it to this session would corrupt the ledger's join.
            # It is never recallable (`remainders_recalled` proves that), so
            # last-writer-wins on this map is safe; it is asserted, not assumed.
            session_of.setdefault(unit_id, unit["unit_id"])
    return (
        context,
        identity,
        0,
        0,
        {"proposed": proposed, "considered": considered, "ledger": ledger},
    )


def score_group(client, context, episode_of: dict, group: dict, args) -> list[dict]:
    rows = []
    for probe in group["probes"]:
        request = {
            **context,
            "query": probe["query"],
            "limit": args.k,
            "budget_tokens": args.budget_tokens,
            "mode": args.mode,
        }
        # THE AS-OF QUESTION. `valid_at` is a live recall filter, not a stub:
        # RecallRequest.valid_at -> valid_for_query, `valid_from <= valid_at`
        # and `valid_at < valid_to`. Sending it is what makes the bitemporal
        # arm answerable at all on this cut.
        if probe.get("valid_at"):
            request["valid_at"] = probe["valid_at"]
        response = client.post("/v1/recall", request)
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
    # --- A-recency mechanism liveness (both directions) -------------------
    # An inert arm and a neutral arm produce the same score and mean opposite
    # things, so both arms must PROVE which one they are.
    #
    # `supersedes_edges` / `superseded_units` / `valid_closed_units` must be
    # NON-ZERO on the bitemporal arm (the edifice fired) and ZERO on the
    # A-recency arm (the edifice was bypassed, not merely unexercised).
    "supersedes_edges":
        "select count(*) from memphant.memory_edge where kind = 'supersedes'",
    "superseded_units":
        "select count(*) from memphant.memory_unit where state = 'superseded'",
    "valid_closed_units":
        "select count(*) from memphant.memory_unit where valid_to is not null",
    # THE assertion, and it is deliberately NOT an open-row count. The
    # bitemporal model correctly leaves a historical remainder open beside the
    # current generation -- remainders TILE, they do not overlap -- so
    # `count(open rows) <= 1` is the wrong test and would pass for the wrong
    # reason. This is the right one: how many pairs of OPEN rows share a
    # subject key AND have OVERLAPPING valid ranges. Exactly the predicate of
    # `memphant_memory_unit_subject_valid_excl`, computed rather than trusted.
    #   bitemporal arm -> 0 (the machinery maintains the invariant)
    #   A-recency arm   -> >0 (competing live assertions coexist; the read side
    #                     must choose between them, which is the whole control)
    "open_subject_key_range_overlaps":
        "select count(*) from memphant.memory_unit a "
        "join memphant.memory_unit b on a.tenant_id = b.tenant_id "
        "and a.data_subject_id = b.data_subject_id and a.scope_id = b.scope_id "
        "and a.agent_node_id = b.agent_node_id "
        "and a.subject_generation = b.subject_generation "
        "and a.fact_key = b.fact_key and a.kind = b.kind and a.id < b.id "
        "and a.transaction_to is null and b.transaction_to is null "
        "and tstzrange(a.valid_from, a.valid_to, '[)') "
        "&& tstzrange(b.valid_from, b.valid_to, '[)') "
        "where a.kind in ('semantic','preference')",
    # How much work the recency collapse actually had to do: subject keys
    # carrying more than one live row. Zero here would mean the A-recency read
    # rule never fired on anything.
    "subject_keys_with_competing_live_rows":
        "select count(*) from (select fact_key from memphant.memory_unit "
        "where transaction_to is null and kind in ('semantic','preference') "
        "group by tenant_id, data_subject_id, scope_id, agent_node_id, "
        "subject_generation, fact_key, kind having count(*) > 1) multi",
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
        choices=("memphant", "lexical", "preference", "structured", "derived",
                 "trivial"),
        help="memphant = live recall; lexical = the repo's BM25 control over "
             "the same units and queries (no DB, no server, no network); "
             "preference = ORACLE-KEYED write-path mechanism arm, NOT "
             "comparable to either of the other two (see "
             "ingest_group_preference); structured = B1, gold-independent "
             "id-named supersession (see ingest_group_structured)",
    )
    parser.add_argument(
        "--structured-unit", default="body", choices=("body", "sentence"),
        help="structured arm: the SIMILARITY UNIT. body = B1's original "
             "whole-body content-word Jaccard; sentence = max Jaccard over the "
             "two bodies' directive sentences, literals blanked. Both are "
             "deterministic, gold-independent and free. The threshold is NOT "
             "unit-free: the two units live on different scales, so a "
             "--structured-threshold chosen for one is meaningless on the "
             "other.",
    )
    parser.add_argument(
        "--structured-threshold", type=float, default=0.35,
        help="structured arm: content-word Jaccard a top-ranked prior unit must "
             "clear before it is named as a supersession target. >1.0 disables "
             "supersession entirely, which is this arm's own no-op control.",
    )
    parser.add_argument(
        "--structured-ablation", default="none",
        choices=("none", "recency", "random"),
        help="structured arm: replace the semantic target choice with an "
             "uninformative one at a matched firing rate. This instrument's "
             "distractors are ALWAYS earlier declarations, so any policy that "
             "retires older rows scores well on it; the ablation is what "
             "separates B1's semantics from plain distractor retirement.",
    )
    parser.add_argument(
        "--structured-fire-rate", type=float, default=0.0,
        help="structured arm: per-session firing probability for the ablation, "
             "drawn deterministically from the session id. Set to the measured "
             "firing rate of the arm being ablated.",
    )
    parser.add_argument(
        "--structured-pool", type=int, default=5,
        help="structured arm: recall limit for the extractor's retrieval "
             "context. Only the top-ranked preference unit is ever considered.",
    )
    parser.add_argument(
        "--structured-query-chars", type=int, default=8000,
        help="structured arm: cap on the session body used as the extractor's "
             "recall query.",
    )
    parser.add_argument(
        "--group-mod", default=None,
        help="RESIDUE/MODULUS over sha256(group_id) -- e.g. 0/2 keeps the dev "
             "half, 1/2 the confirmatory half. Applied after loading, before "
             "any database exists.",
    )
    parser.add_argument(
        "--a-recency",
        action="store_true",
        help="A-RECENCY CONTROL. Runs the server and worker with "
             "MEMPHANT_A_RECENCY_CONTROL=1: the write path never supersedes "
             "and the read path keeps the greatest-observed_at row per subject "
             "key. Also DROPS memphant_memory_unit_subject_valid_excl on the "
             "scratch DB, because that constraint is part of the machinery "
             "under test and an append-only store has no such invariant. "
             "Pair with --arm preference; measurement only, never a default",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="memphant arm only: dump supersession/state/tier counts from the "
             "scratch DB into the report BEFORE it is dropped",
    )
    parser.add_argument(
        "--trivial-rule", default="asof_truncation",
        choices=("constant", "first", "recency", "asof_truncation", "bm25",
                 "bm25_asof", "mode_oracle"),
        help="trivial arm: which cheap read rule to run. The FULL set is "
             "preregistered and every member is reported, because a corpus "
             "that defeats recency can still be defeated by a different "
             "trivial rule (S2 3.3, measured on TempLAMA).",
    )
    parser.add_argument(
        "--bounded", action="store_true",
        help="keyed arms: send `valid_from` on every unit payload, putting "
             "valid time on the SESSION axis instead of wall-clock ingest "
             "order. Required for any as-of question; changes the write path "
             "(close_open_generation takes the interval_intersection branch "
             "and mints the historical remainder).",
    )
    parser.add_argument("--source", required=True, help="pinned mirror file")
    parser.add_argument("--lock", default=None, help="manifest lock (default: derived)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit-groups", type=int, default=0)
    parser.add_argument("--limit-probes-per-group", type=int, default=0)
    parser.add_argument("--limit-units-per-group", type=int, default=0)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--budget-tokens", type=int, default=8192)
    parser.add_argument(
        "--derived-rule",
        default="pre3_content_words",
        help="derived arm: which gold-independent body rule from "
             "scripts/measure_key_recovery.py mints the subject key. Chosen on "
             "the offline frontier, never here.",
    )
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
    if args.group_mod:
        residue, modulus = (int(part) for part in args.group_mod.split("/"))
        groups = [
            group
            for group in groups
            if int(hashlib.sha256(group["group_id"].encode()).hexdigest(), 16) % modulus
            == residue
        ]
        print(
            f"[{args.instrument}] group-mod {args.group_mod} keeps groups={len(groups)} "
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

    # Set BEFORE the scratch-db re-exec so the flag survives it and is inherited
    # by every child (server, worker) rather than being re-derived per process.
    if args.a_recency:
        os.environ["MEMPHANT_A_RECENCY_CONTROL"] = "1"

    if args.instrument in ASOF_INSTRUMENTS and args.arm == "structured":
        # Arm S names supersession targets by exact unit id and keys on a
        # content hash of the body, so its remainders cannot be attributed by
        # the `key_last_session` rule the keyed arms use. Supporting it is a
        # separate build; refusing is honest, silently mis-attributing is not.
        raise SystemExit(
            "memorycode_asof: arm `structured` has no remainder-attribution "
            "rule on this cut and would credit every as-of gold to a "
            "distractor. Not built; refusing rather than mis-scoring."
        )
    if args.instrument in ASOF_INSTRUMENTS and args.arm in (
        "preference", "derived"
    ) and not args.bounded:
        raise SystemExit(
            f"{args.instrument}: arm {args.arm} without --bounded puts valid "
            "time on wall-clock ingest order, so every as-of probe would be "
            "answered against an axis the loader does not control. Refusing."
        )

    started = time.time()
    diagnostics = None
    if args.arm in ("lexical", "trivial"):
        # Arms B and T short-circuit every runtime: no scratch DB, no server,
        # no worker, no embedder, no network. Same probes, same queries, same k.
        scorer = score_group_lexical if args.arm == "lexical" else score_group_trivial
        rows = [row for group in groups for row in scorer(group, args)]
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
    if args.a_recency:
        # The GiST exclusion constraint asserts at-most-one-open-row per
        # (subject key, kind) with overlapping valid time. That invariant is
        # maintained BY supersession, so with supersession off the second
        # assertion on a key would be rejected at insert. Dropping it is not a
        # loosening of the control -- the constraint is part of the edifice the
        # control replaces. Scratch DB only; it is destroyed on exit.
        drop = gr.sh([
            "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1", args.database_url,
            "--command",
            "alter table memphant.memory_unit drop constraint "
            "memphant_memory_unit_subject_valid_excl",
        ])
        if drop.returncode != 0:
            raise SystemExit(
                "A-recency control could not drop the subject exclusion "
                f"constraint: {drop.stderr.strip()[:300]}"
            )
        print("[a-recency] dropped memphant_memory_unit_subject_valid_excl", file=sys.stderr)

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    server = gr.Server(
        args.server_bin,
        args.database_url,
        args.port,
        args.embed_model,
        # Per-ARM log name. Four arms sharing one artifact directory otherwise
        # interleave into one `server-memorycode.log`, and the only copy of a
        # crash reason belongs to whichever arm truncated it last.
        log_path=out_path.parent / f"server-{args.instrument}-{out_path.stem}.log",
    )
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        bound = {}
        ingested = 0
        deduped = 0
        episodes_retained = 0
        remainders_minted = 0
        structured_counts = {"proposed": 0, "considered": 0}
        for group in groups:
            if args.arm == "structured":
                context, episode_of, group_episodes, group_deduped, counts = (
                    ingest_group_structured(client, group, args)
                )
                episodes_retained += group_episodes
                structured_counts["proposed"] += counts["proposed"]
                structured_counts["considered"] += counts["considered"]
                structured_counts.setdefault("ledger", []).extend(counts["ledger"])
            elif args.arm == "derived":
                context, episode_of, group_episodes, group_deduped, group_rem = (
                    ingest_group_derived(client, group, args)
                )
                episodes_retained += group_episodes
                remainders_minted += group_rem
            elif args.arm == "preference":
                context, episode_of, group_episodes, group_deduped, group_rem = (
                    ingest_group_preference(client, group, args.bounded)
                )
                episodes_retained += group_episodes
                remainders_minted += group_rem
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
            # MECHANISM LIVENESS FOR THE AS-OF CONSTRUCT, and the direction is
            # the OPPOSITE of every prior lane's. On the original cut a
            # recalled remainder was a scoring hazard and 0 was the pass. Here
            # the historical rectangle IS the gold answer, so `> 0` is the
            # pass and 0 means the valid-time machinery never fired -- "inert"
            # is then the whole report, exactly as Arm F was voided.
            diagnostics["asof_liveness"] = {
                "bounded_ingest": bool(args.bounded),
                "remainders_minted": remainders_minted,
                "remainders_recalled": diagnostics["remainders_recalled"],
                "probes_with_valid_at": sum(1 for r in rows if r.get("valid_at")),
                "expectation": (
                    "remainders_recalled > 0"
                    if args.instrument in ASOF_INSTRUMENTS and args.bounded
                    else "remainders_recalled == 0"
                ),
            }
            if args.arm == "structured":
                # MECHANISM LIVENESS. An inert arm and a neutral arm produce the
                # same score; only these counts tell them apart.
                diagnostics["structured_extractor"] = {
                    "unit": args.structured_unit,
                    "threshold": args.structured_threshold,
                    "ablation": args.structured_ablation,
                    "fire_rate": args.structured_fire_rate,
                    "pool": args.structured_pool,
                    "targets_proposed": structured_counts["proposed"],
                    "top_ranked_prior_units_seen": structured_counts["considered"],
                    "ledger": structured_counts.get("ledger", []),
                }
    finally:
        server.stop()

    return write_report(
        args, lock, lock_path, source, rows, groups, ingested, compiled,
        started, out_path, diagnostics
    )


def lineage_stamp(args) -> dict:
    """Git head, working-tree cleanliness, and the sha256 of the served binary."""
    root = Path(__file__).resolve().parent.parent
    def git(*argv: str) -> str:
        result = gr.sh(["git", "-C", str(root), *argv])
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    stamp = {
        "worktree": str(root),
        "git_head": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
    }
    for name in ("server_bin", "worker_bin"):
        path = Path(getattr(args, name))
        stamp[f"{name}_sha256"] = sha256_file(path) if path.exists() else "absent"
    return stamp


def write_report(args, lock, lock_path, source, rows, groups, ingested,
                 compiled, started, out_path, diagnostics) -> int:
    report = {
        "instrument": args.instrument,
        "arm": args.arm,
        "trivial_rule": args.trivial_rule if args.arm == "trivial" else None,
        "bounded_valid_time": bool(args.bounded),
        "a_recency_control": bool(args.a_recency),
        # Lineage drift across ~19 worktrees is this program's dominant failure
        # mode: the same false claim was produced twice in two days by two
        # independent careful sessions reading a binary that was not the tree.
        # So the tree AND the binary that actually served the recall are both
        # stamped into every report.
        "lineage": lineage_stamp(args),
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
        if args.arm in ("memphant", "preference", "structured", "derived")
        else {"k": args.k, "mechanism": f"trivial read rule `{args.trivial_rule}` "
              "over the instance's own sessions; no DB, no server, no network"}
        if args.arm == "trivial"
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
