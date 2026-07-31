#!/usr/bin/env python3
"""One retrieval-only adapter for externally-sourced memory instruments.

Deliberately ONE script for every adopted external instrument rather than a
harness per benchmark (ponytail). An instrument contributes exactly two
things -- a loader that turns its shipped rows into (a) ingest *units* and
(b) *probes* carrying deterministic gold unit ids -- and inherits the shared
runtime: scratch DB, packaged server/worker/cli, ``bind_context`` identity,
``/v1/episodes`` retain, worker drain, ``/v1/recall``, hit@k scoring.

$0 by construction. There is no reader and no judge here: a probe is scored
by whether the gold unit's tag line appears in the recalled bodies. Every
gold label is derived by a rule from a field the instrument itself ships --
never by a model call -- so the score is reproducible offline.

Attribution (reuse policy 26 D-2026-07-30) lives in the per-instrument lock
under ``benchmarks/manifests/``; this runner verifies the pinned sha256 of
every source file it reads before it touches a database.

Unit tagging: each ingested body is prefixed with a single
``unit: <unit_id>`` line. Recall returns bodies, not ids, so this tag is the
attribution channel -- the same trick the docs lane's span matching relies
on, made exact. It is a documented transformation of the source text, not a
hidden one.

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


def tagged(unit_id: str, body: str) -> str:
    return f"unit: {unit_id}\n{body}"


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
                units.append(
                    {"unit_id": uid, "source_kind": "agent", "body": tagged(uid, body)}
                )
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
                "body": tagged(f"{gid}-s{i}", session["text"]),
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


LOADERS = {"ama_bench": load_ama_bench, "memorycode": load_memorycode}


# --------------------------------------------------------------------------


def verify_source(instrument: str, source: Path, lock_path: Path) -> dict:
    lock = json.loads(lock_path.read_text())
    expected = lock["dataset"]["files"][lock["dataset"]["primary_file"]]["sha256"]
    actual = sha256_file(source)
    if actual != expected:
        raise SystemExit(
            f"{instrument}: source sha256 {actual} != pinned {expected} "
            f"({lock_path.name}); the mirror mutated -- refusing to run"
        )
    return lock


def unit_hits(bodies: list[str], unit_ids: list[str]) -> list[int]:
    """Ranks (0-based) at which any of ``unit_ids`` appears in ``bodies``."""
    wanted = {f"unit: {uid}\n" for uid in unit_ids}
    return [
        rank
        for rank, body in enumerate(bodies)
        if any(tag in body for tag in wanted)
    ]


def run_group(client, group: dict, args) -> list[dict]:
    context = client.bind_context(
        f"external-{group['group_id']}",
        subject_ref=group["group_id"],
        actor_ref=f"{group['group_id']}-runner",
        scope_ref=group["group_id"],
        agent_node_ref="external-instrument-adapter",
    )
    for index, unit in enumerate(group["units"]):
        client.post(
            "/v1/episodes",
            gr.episode_retain_payload(
                context,
                source_ref=unit["unit_id"],
                observed_at=observed_at(index),
                source_kind=unit["source_kind"],
                body=unit["body"],
            ),
        )
    return context


def score_group(client, context, group: dict, args) -> list[dict]:
    rows = []
    for probe in group["probes"]:
        bodies, degraded = gr.recall_query(
            client, context, probe["query"], args.k, args.budget_tokens, args.mode
        )
        gold = unit_hits(bodies, probe["gold_unit_ids"])
        stale = unit_hits(bodies, probe["distractor_unit_ids"])
        rows.append(
            {
                "group_id": group["group_id"],
                "probe_id": probe["probe_id"],
                "kind": probe["kind"],
                "returned": len(bodies),
                "degraded": degraded,
                "gold_rank": gold[0] if gold else None,
                "stale_rank": stale[0] if stale else None,
                "hit_at_1": bool(gold) and gold[0] == 0,
                "hit_at_k": bool(gold),
                "stale_outranks_current": bool(stale)
                and (not gold or stale[0] < gold[0]),
            }
        )
    return rows


def summarise(rows: list[dict], k: int) -> dict:
    n = len(rows)
    if not n:
        return {"probes": 0}
    graded = [r for r in rows if r["stale_rank"] is not None or r["gold_rank"] is not None]
    return {
        "probes": n,
        "k": k,
        "hit_at_1": sum(r["hit_at_1"] for r in rows) / n,
        "hit_at_k": sum(r["hit_at_k"] for r in rows) / n,
        "degraded": sum(r["degraded"] for r in rows),
        "with_any_unit_returned": len(graded),
        "stale_outranks_current": sum(r["stale_outranks_current"] for r in rows),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", required=True, choices=sorted(LOADERS))
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
    started = time.time()
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        contexts = {}
        ingested = 0
        for group in groups:
            contexts[group["group_id"]] = run_group(client, group, args)
            ingested += len(group["units"])
        compiled = gr.drain_worker(args.worker_bin, args.database_url, args.embed_model)
        if compiled != ingested:
            raise RuntimeError(f"compiled {compiled} != ingested {ingested}")
        rows = []
        for group in groups:
            rows += score_group(client, contexts[group["group_id"]], group, args)
    finally:
        server.stop()

    report = {
        "instrument": args.instrument,
        "lock": {"path": lock_path.name, "sha256": sha256_file(lock_path)},
        "source": {"path": str(source), "sha256": lock["dataset"]["files"][
            lock["dataset"]["primary_file"]]["sha256"]},
        "license": lock["license"],
        "attribution": lock["attribution"],
        "recall": {"k": args.k, "mode": args.mode, "budget_tokens": args.budget_tokens,
                   "embed_model": args.embed_model},
        "scale": {
            "groups": len(groups),
            "units_ingested": ingested,
            "compiled_jobs": compiled,
            "wall_seconds": round(time.time() - started, 1),
        },
        "paid_model_calls": 0,
        "summary": summarise(rows, args.k),
        "rows": rows,
    }
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
