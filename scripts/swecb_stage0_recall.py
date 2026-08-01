#!/usr/bin/env python3
"""SWE-ContextBench stage 0: can MemPhant retrieve the official Relationship parent?

Zero model calls, zero dollars, local embeddings, ephemeral scratch Postgres.

The whole experience pool is ONE haystack: every experience row is bound to a
single subject/scope and every target query is issued against that same context.
This is the structural difference from the retained n=12 rehearsal
(``run_swe_contextbench_memphant.py``), which bound a fresh scope per
(target, arm) and therefore ranked over a pool of one -- a citation assertion
that could not fail is not a retrieval measurement.

Decision bands are preregistered in
``docs/build-log/artifacts/s5-swecb/stage0-prereg.json`` and were committed
before the first ingest. This runner does not know about them and does not
emit a verdict; it emits cells.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_runtime as gr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "benchmarks/manifests/swe_contextbench.lock.json"
DEFAULT_BASE_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"

# Fields the agent would never see for a TARGET. Kept here so the query
# construction cannot silently widen.
HIDDEN_TARGET_FIELDS = ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS")
K_VALUES = (1, 3, 5, 10, 25)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_fingerprint(row: dict) -> str:
    return sha256_json({k: (None if v is None else str(v)) for k, v in row.items()})


def load_parquet(path: Path) -> list[dict]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - environment guard
        raise RuntimeError("pyarrow is required to read the pinned parquets") from error
    return pq.read_table(path).to_pylist()


# ---------------------------------------------------------------- sources


def verify_and_load(lock: dict, mirror: Path) -> dict:
    """Re-verify every pinned parquet on disk, then load and de-duplicate.

    Duplicate handling is asserted, not assumed: the Experience duplicates must
    be byte-identical (they are, and de-duplicating them is safe), and the
    Related duplicates must NOT be (they are not, and collapsing them would
    silently discard a distinct patch/test pair). If either ever stops holding
    upstream, this aborts rather than quietly changing what was measured.
    """
    files = lock["dataset"]["files"]
    verified = {}
    for name, spec in files.items():
        path = mirror / name
        require(path.is_file(), f"pinned file missing from the mirror: {path}")
        require(path.stat().st_size == spec["bytes"], f"byte drift: {name}")
        digest = sha256_file(path)
        require(digest == spec["sha256"], f"sha256 drift: {name}")
        verified[name] = {"bytes": spec["bytes"], "sha256": digest}

    experience_rows = load_parquet(mirror / "data/SWEContextBench_Experience.parquet")
    target_rows = load_parquet(mirror / "data/SWEContextBench_Related.parquet")
    edge_rows = load_parquet(mirror / "data/SWEContextBench_Relationship.parquet")

    exp_groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in experience_rows:
        exp_groups[row["instance_id"]].append(row)
    exp_dupe_ids = [k for k, v in exp_groups.items() if len(v) > 1]
    for key in exp_dupe_ids:
        prints = {row_fingerprint(r) for r in exp_groups[key]}
        require(
            len(prints) == 1,
            f"experience duplicate {key} is NOT byte-identical; de-duplication is "
            "no longer safe and the pool definition must be re-preregistered",
        )
    pool = {k: v[0] for k, v in exp_groups.items()}

    tgt_groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in target_rows:
        tgt_groups[row["instance_id"]].append(row)
    tgt_dupe_ids = [k for k, v in tgt_groups.items() if len(v) > 1]
    for key in tgt_dupe_ids:
        prints = {row_fingerprint(r) for r in tgt_groups[key]}
        require(
            len(prints) > 1,
            f"related duplicate {key} became byte-identical upstream; the "
            "row-vs-task distinction this run reports would be vacuous",
        )

    parents: dict[str, list[str]] = collections.defaultdict(list)
    for edge in edge_rows:
        parents[edge["related_instance_id"]].append(edge["experience_instance_id"])
    for key in parents:
        parents[key] = sorted(set(parents[key]))

    require(set(parents) <= set(tgt_groups), "an edge names a target absent from Related")
    require(set(tgt_groups) <= set(parents), "a Related target has no relationship edge")
    missing = {p for ps in parents.values() for p in ps} - set(pool)
    require(not missing, f"gold parents absent from the pool: {sorted(missing)[:5]}")

    overlap = sorted(set(tgt_groups) & set(pool))
    return {
        "verified_files": verified,
        "pool": pool,
        "target_groups": dict(tgt_groups),
        "parents": dict(parents),
        "counts": {
            "experience_rows": len(experience_rows),
            "experience_distinct": len(pool),
            "experience_duplicate_ids": len(exp_dupe_ids),
            "experience_duplicates_byte_identical": True,
            "related_rows": len(target_rows),
            "related_distinct": len(tgt_groups),
            "related_duplicate_ids": len(tgt_dupe_ids),
            "related_duplicates_byte_identical": False,
            "edges": len(edge_rows),
            "distinct_parents": len({p for ps in parents.values() for p in ps}),
            "two_parent_targets": sum(1 for v in parents.values() if len(v) > 1),
            "related_intersect_experience": len(overlap),
        },
        "self_retrieval_ids": overlap,
    }


def experience_body(row: dict, variant: str) -> str:
    """The experience text placed in memory.

    ``patchfree`` (PRIMARY) is identity + problem statement + hints. Measured
    leakage into the target's gold patch is 9.0% of edges carrying at least one
    exact added line, against a 6.9% floor produced by the target's OWN problem
    statement quoting its own patch. That is the instrument's irreducible noise,
    so this body is clean.

    ``withpatch`` (SECONDARY, DIAGNOSTIC ONLY, NEVER A CLAIM) appends the gold
    merged diff. It is inadmissible for any published number: 75.5% of gold
    parents touch a target patch file, 32.4% have an identical touched-file set,
    and 37.2% contain an exact target added line -- against a same-repo random
    control of 9.1% / 0.13%. It exists here solely to price how much putting raw
    code in memory buys on retrieval, which is what the ~$737 trajectory-pool
    rebuild would be paying for.
    """
    head = (
        f"SWE-ContextBench prior experience: {row['instance_id']}\n"
        f"Repository: {row['repo']}\n"
        f"Base commit: {row['base_commit']}\n"
        f"Observed at: {row['created_at']}\n\n"
        f"Prior problem:\n{row['problem_statement']}\n\n"
        f"Prior hints:\n{row['hints_text'] or '(none)'}\n"
    )
    if variant == "patchfree":
        return head
    if variant == "withpatch":
        return head + f"\nMerged fix:\n{row['patch']}\n"
    raise ValueError(f"unknown body variant: {variant}")


# ---------------------------------------------------------------- lineage


def command_output(argv: list[str]) -> str:
    return subprocess.run(argv, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def lineage(*, server_bin: str, worker_bin: str, cli_bin: str) -> dict:
    binaries = {"server": Path(server_bin), "worker": Path(worker_bin), "cli": Path(cli_bin)}
    for name, path in binaries.items():
        require(path.is_file(), f"{name} binary is missing: {path}")
    migrations = sorted((ROOT / "memphant_migrations/versions").glob("*.sql"))
    status = command_output(["git", "status", "--porcelain"])
    return {
        "git_head": command_output(["git", "rev-parse", "HEAD"]),
        "git_branch": command_output(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": bool(status.strip()),
        "git_status_porcelain": status,
        "cargo_lock_sha256": sha256_file(ROOT / "Cargo.lock"),
        "migration_head": migrations[-1].name,
        "rustc_version": command_output(["rustc", "--version"]),
        "cargo_version": command_output(["cargo", "--version"]),
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "gate_runtime_sha256": sha256_file(Path(gr.__file__).resolve()),
        "binaries": {n: {"path": str(p.resolve()), "sha256": sha256_file(p)} for n, p in binaries.items()},
    }


def psql_rows(database_url: str, sql: str) -> list[list[str]]:
    result = gr.sh([
        "psql", "--no-psqlrc", "--tuples-only", "--no-align", "--field-separator", "\x1f",
        "--set", "ON_ERROR_STOP=1", database_url, "--command", sql,
    ])
    require(result.returncode == 0, f"psql failed: {result.stderr.strip()[:400]}")
    return [line.split("\x1f") for line in result.stdout.splitlines() if line]


# ---------------------------------------------------------------- the run


def run(
    sources: dict,
    *,
    database_url: str,
    port: int,
    server_bin: str,
    worker_bin: str,
    cli_bin: str,
    embed_model: str,
    lexical_scorer: str | None,
    limit: int,
    budget_tokens: int,
    body_variant: str,
    output: Path,
) -> dict:
    identity = lineage(server_bin=server_bin, worker_bin=worker_bin, cli_bin=cli_bin)
    pool = sources["pool"]
    parents = sources["parents"]
    target_groups = sources["target_groups"]

    tenant_id, api_key = gr.provision_tenant(cli_bin, database_url, "swecb-stage0")
    server = gr.Server(
        server_bin, database_url, port,
        embed_model=embed_model,
        lexical_scorer=lexical_scorer,
        log_path=output.with_suffix(".server.log"),
    )
    started_wall = time.time()
    try:
        server.start()
        client = gr.ApiClient(port, api_key, tenant_id)

        # ONE haystack. Every experience and every query share this context.
        context = client.bind_context(
            "swecb-stage0",
            subject_ref="swecb:stage0:pool",
            actor_ref="swecb-stage0-runner",
            scope_ref="swecb:stage0:pool",
            agent_node_ref="swecb-stage0-agent",
        )

        ingest_started = time.perf_counter()
        episode_ids: dict[str, str] = {}
        body_chars = 0
        for index, (instance_id, row) in enumerate(sorted(pool.items())):
            body = experience_body(row, body_variant)
            body_chars += len(body)
            payload = gr.episode_retain_payload(
                context,
                source_ref=f"swecb:exp:{instance_id}",
                observed_at=row["created_at"],
                source_kind="resource",
                body=body,
            )
            response = client.post("/v1/episodes", payload)
            episode_id = response.get("episode_id")
            require(
                isinstance(episode_id, str) and episode_id,
                f"retain returned no episode id for {instance_id}",
            )
            episode_ids[instance_id] = episode_id
            if (index + 1) % 200 == 0:
                print(f"  retained {index + 1}/{len(pool)}", flush=True)
        require(len(episode_ids) == len(pool), "retain count does not match the pool")
        require(
            len(set(episode_ids.values())) == len(pool),
            "two experiences collapsed onto one episode -- the pool deduped under us",
        )
        ingest_seconds = round(time.perf_counter() - ingest_started, 1)

        drain_started = time.perf_counter()
        completed = gr.drain_worker(worker_bin, database_url, embed_model)
        gr.assert_worker_queue_empty(database_url)
        drain_seconds = round(time.perf_counter() - drain_started, 1)

        # unit_id -> instance_id, read from the DATABASE, so candidate-trace
        # units that never reached a citation still resolve.
        rows = psql_rows(
            database_url,
            "select u.id::text, coalesce(e.source_ref, u.source_ref) "
            "from memphant.memory_unit u "
            "left join memphant.episode e on e.id = u.source_episode_id",
        )
        unit_to_instance: dict[str, str] = {}
        units_per_instance: collections.Counter = collections.Counter()
        for unit_id, source_ref in rows:
            if not source_ref.startswith("swecb:exp:"):
                continue
            instance_id = source_ref[len("swecb:exp:"):]
            unit_to_instance[unit_id] = instance_id
            units_per_instance[instance_id] += 1
        compiled = len(units_per_instance)
        require(
            compiled == len(pool),
            f"only {compiled} of {len(pool)} experiences compiled into a memory unit; "
            "refusing to score a partially compiled corpus",
        )

        embed_count = int(psql_rows(database_url, "select count(*) from memphant.embedding")[0][0])
        require(embed_count > 0, "no embeddings exist; the vector channel cannot have fired")

        # ------------------------------------------------------ queries
        records: list[dict] = []
        query_started = time.perf_counter()
        ordered_targets = sorted(target_groups)
        for index, instance_id in enumerate(ordered_targets):
            gold = parents[instance_id]
            for variant, row in enumerate(target_groups[instance_id]):
                for hidden in HIDDEN_TARGET_FIELDS:
                    require(hidden in row, f"target {instance_id} lost a validation field")
                query = row["problem_statement"]
                require(isinstance(query, str) and query, f"empty query for {instance_id}")

                started = time.perf_counter()
                response = client.post(
                    "/v1/recall",
                    {**context, "query": query, "limit": limit,
                     "budget_tokens": budget_tokens, "mode": "fast"},
                )
                latency_ms = int(round((time.perf_counter() - started) * 1000))
                require(response.get("degraded") is False, f"recall degraded for {instance_id}")
                trace_id = response.get("trace_id")
                require(isinstance(trace_id, str) and trace_id, "recall returned no trace id")
                trace = client.get(
                    f"/v1/traces/{trace_id}?{urllib.parse.urlencode(context)}"
                )

                packed = []
                for item in response.get("items", []):
                    mapped = unit_to_instance.get(item.get("unit_id"))
                    packed.append(mapped)
                # self-retrieval guard: a target's own row may sit in the pool
                self_retrieved = instance_id in packed
                packed_ranked = [p for p in packed if p != instance_id]

                candidates = trace.get("candidates") or []
                cand_rank: dict[str, int] = {}
                cand_drop: dict[str, str | None] = {}
                for candidate in candidates:
                    mapped = unit_to_instance.get(candidate.get("unit_id"))
                    if mapped is None or mapped == instance_id:
                        continue
                    rank = candidate.get("fused_rank")
                    if rank is None:
                        continue
                    if mapped not in cand_rank or rank < cand_rank[mapped]:
                        cand_rank[mapped] = rank
                        cand_drop[mapped] = candidate.get("discard_reason")

                packed_rank = {}
                for rank, mapped in enumerate(packed_ranked, start=1):
                    if mapped is not None and mapped not in packed_rank:
                        packed_rank[mapped] = rank

                gold_detail = []
                for parent in gold:
                    gold_detail.append({
                        "parent_id": parent,
                        "packed_rank": packed_rank.get(parent),
                        "retrieval_rank": cand_rank.get(parent),
                        "discard_reason": cand_drop.get(parent),
                        "in_candidates": parent in cand_rank,
                    })
                records.append({
                    "target_id": instance_id,
                    "variant": variant,
                    "variant_count": len(target_groups[instance_id]),
                    "repo": row["repo"],
                    "gold_parents": gold,
                    "gold": gold_detail,
                    "self_retrieved": self_retrieved,
                    "returned_items": len(packed),
                    "candidate_count": len(candidates),
                    "trace_id": trace_id,
                    "token_estimate": trace.get("token_estimate"),
                    "latency_ms": latency_ms,
                })
            if (index + 1) % 50 == 0:
                print(f"  queried {index + 1}/{len(ordered_targets)}", flush=True)
        query_seconds = round(time.perf_counter() - query_started, 1)
    finally:
        server.stop()

    return {
        "schema_version": 1,
        "lane": "s5-swecb",
        "stage": 0,
        "classification": "retrieval_measurement_not_task_success",
        "model_calls": 0,
        "cost_usd": 0.0,
        "prereg": "docs/build-log/artifacts/s5-swecb/stage0-prereg.json",
        "lineage": identity,
        "dataset": {
            "revision": "5bec275a2095768a53ac804ae4fdf90b1723b8af",
            "verified_files": sources["verified_files"],
            "counts": sources["counts"],
            "self_retrieval_ids": sources["self_retrieval_ids"],
        },
        "configuration": {
            "embed_model": embed_model,
            "lexical_scorer": lexical_scorer,
            "limit": limit,
            "budget_tokens": budget_tokens,
            "mode": "fast",
            "haystack": "single subject/scope containing every distinct experience",
            "body_variant": body_variant,
            "body": {
                "patchfree": "identity + problem_statement + hints_text (PRIMARY, admissible)",
                "withpatch": "patchfree + gold merged patch (SECONDARY, DIAGNOSTIC ONLY, inadmissible for any claim)",
            }[body_variant],
        },
        "mechanism_liveness": {
            "retained": len(episode_ids),
            "distinct_episode_ids": len(set(episode_ids.values())),
            "worker_completed": completed,
            "queue_drained_verified_by_bench_credential": True,
            "instances_with_a_compiled_unit": compiled,
            "memory_units_total": sum(units_per_instance.values()),
            "embeddings_total": embed_count,
            "recall_degraded_count": 0,
            "ingest_seconds": ingest_seconds,
            "drain_seconds": drain_seconds,
            "query_seconds": query_seconds,
            "wall_seconds": round(time.time() - started_wall, 1),
            "ingested_body_chars": body_chars,
        },
        "records": records,
    }


# ---------------------------------------------------------------- scoring


def score(result: dict) -> dict:
    records = result["records"]
    by_target: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        by_target[record["target_id"]].append(record)

    def hits(record: dict, k: int, field: str, mode: str) -> bool:
        ranks = [g[field] for g in record["gold"] if g[field] is not None and g[field] <= k]
        if mode == "any":
            return bool(ranks)
        return len(ranks) == len(record["gold"])

    def rate(subset: list[dict], k: int, field: str, mode: str) -> float:
        return round(sum(hits(r, k, field, mode) for r in subset) / len(subset), 4)

    # PRIMARY view: one cell per distinct task. A duplicated instance is not an
    # independent pair; a task counts as a hit if ANY of its row variants hits.
    distinct: list[dict] = []
    for target_id, group in by_target.items():
        merged_gold: dict[str, dict] = {}
        for record in group:
            for gold in record["gold"]:
                current = merged_gold.get(gold["parent_id"])
                def better(a, b):
                    if a is None:
                        return b
                    if b is None:
                        return a
                    return min(a, b)
                if current is None:
                    merged_gold[gold["parent_id"]] = dict(gold)
                else:
                    current["packed_rank"] = better(current["packed_rank"], gold["packed_rank"])
                    current["retrieval_rank"] = better(current["retrieval_rank"], gold["retrieval_rank"])
                    current["in_candidates"] = current["in_candidates"] or gold["in_candidates"]
        distinct.append({
            "target_id": target_id,
            "gold": list(merged_gold.values()),
            "repo": group[0]["repo"],
        })

    multi = [r for r in distinct if len(r["gold"]) > 1]
    summary = {
        "n_distinct_tasks": len(distinct),
        "n_rows": len(records),
        "n_two_parent_tasks": len(multi),
        "primary": {
            "view": "distinct tasks, ANY-PARENT",
            "packed_recall_at_k": {str(k): rate(distinct, k, "packed_rank", "any") for k in K_VALUES},
            "retrieval_recall_at_k": {str(k): rate(distinct, k, "retrieval_rank", "any") for k in K_VALUES},
        },
        "secondary_row_census": {
            "view": "all Related rows, ANY-PARENT",
            "packed_recall_at_k": {str(k): rate(records, k, "packed_rank", "any") for k in K_VALUES},
            "retrieval_recall_at_k": {str(k): rate(records, k, "retrieval_rank", "any") for k in K_VALUES},
        },
        "all_parent_two_parent_tasks": (
            {
                "n": len(multi),
                "packed_recall_at_k": {str(k): rate(multi, k, "packed_rank", "all") for k in K_VALUES},
            }
            if multi else None
        ),
    }

    # Miss taxonomy at the decision k.
    k = 5
    taxonomy = collections.Counter()
    drop_reasons = collections.Counter()
    for record in distinct:
        best_packed = [g["packed_rank"] for g in record["gold"] if g["packed_rank"] is not None]
        if best_packed and min(best_packed) <= k:
            taxonomy["hit"] += 1
            continue
        best_retrieval = [g["retrieval_rank"] for g in record["gold"] if g["retrieval_rank"] is not None]
        if not any(g["in_candidates"] for g in record["gold"]):
            taxonomy["absent_from_candidates"] += 1
        elif best_retrieval and min(best_retrieval) <= k:
            taxonomy["ranked_within_k_but_not_packed"] += 1
        else:
            taxonomy["ranked_below_cut"] += 1
        for gold in record["gold"]:
            if gold.get("discard_reason"):
                drop_reasons[gold["discard_reason"]] += 1
    summary["miss_taxonomy_at_k5"] = dict(taxonomy)
    summary["discard_reasons"] = dict(drop_reasons)

    latencies = sorted(r["latency_ms"] for r in records)
    summary["recall_latency_ms"] = {
        "p50": latencies[len(latencies) // 2],
        "p95": latencies[int(0.95 * len(latencies))],
        "max": latencies[-1],
    }
    summary["self_retrieval_events"] = sum(1 for r in records if r["self_retrieved"])
    summary["mean_returned_items"] = round(
        sum(r["returned_items"] for r in records) / len(records), 2
    )

    # Per-repo view: same-repo haystack size varies by an order of magnitude.
    per_repo: dict[str, list[dict]] = collections.defaultdict(list)
    for record in distinct:
        per_repo[record["repo"]].append(record)
    summary["packed_recall_at_5_by_repo"] = {
        repo: {"n": len(group), "recall": rate(group, 5, "packed_rank", "any")}
        for repo, group in sorted(per_repo.items(), key=lambda kv: -len(kv[1]))
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--database-url", default=DEFAULT_BASE_DATABASE_URL)
    parser.add_argument("--port", type=int, default=39471)
    parser.add_argument("--embed-model", default="small")
    parser.add_argument("--lexical-scorer", default=None)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--budget-tokens", type=int, default=32768)
    parser.add_argument("--server-bin", default=str(ROOT / "target/release/memphant-server"))
    parser.add_argument("--worker-bin", default=str(ROOT / "target/release/memphant-worker"))
    parser.add_argument("--cli-bin", default=str(ROOT / "target/release/memphant-cli"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--body", choices=("patchfree", "withpatch"), default="patchfree")
    parser.add_argument("--sources-only", action="store_true")
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    sources = verify_and_load(lock, args.mirror.expanduser())
    if args.sources_only:
        print(json.dumps(sources["counts"], indent=2, sort_keys=True))
        return 0

    gr.check_embed_model_key(args.embed_model)
    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]

    result = run(
        sources,
        database_url=database_url,
        port=args.port,
        server_bin=args.server_bin,
        worker_bin=args.worker_bin,
        cli_bin=args.cli_bin,
        embed_model=args.embed_model,
        lexical_scorer=args.lexical_scorer,
        limit=args.limit,
        budget_tokens=args.budget_tokens,
        body_variant=args.body,
        output=args.output,
    )
    result["summary"] = score(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
