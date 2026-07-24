#!/usr/bin/env python3
"""Validate and rehearse the frozen SWE-ContextBench MemPhant n=12 gate.

The official target solution and tests are validation-only inputs. Agent-visible
target data is restricted to identity, repository, base commit, and problem
statement. Each treatment context contains exactly one disjoint earlier
experience record, either the official linked experience or a same-repository
unrelated negative control. Model and Docker execution are deliberately out of
scope for this zero-call runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_runtime as gr  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/swe_contextbench.kill.n12.json"
DEFAULT_BASE_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"
TARGET_AGENT_FIELDS = {
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
}
EXPERIENCE_BODY_FIELDS = {
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "hints_text",
    "patch",
    "FAIL_TO_PASS",
    "created_at",
    "version",
}
HIDDEN_TARGET_FIELDS = {"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"}
DOCKER_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_row(row: dict[str, object]) -> dict[str, object]:
    """Canonicalize Arrow null scalars without weakening source identity."""
    return {key: (None if value is None else value) for key, value in row.items()}


def load_parquet_rows(path: Path) -> list[dict[str, object]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError(
            "pyarrow is required only for pinned SWE-ContextBench Parquet validation"
        ) from error
    return [normalize_row(row) for row in pq.read_table(path).to_pylist()]


def index_rows(rows: list[dict[str, object]], *, label: str) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for row in rows:
        instance_id = row.get("instance_id")
        require(isinstance(instance_id, str) and instance_id, f"{label} id is missing")
        require(instance_id not in indexed, f"duplicate {label} id: {instance_id}")
        indexed[instance_id] = row
    return indexed


def target_agent_input(row: dict[str, object]) -> dict[str, str]:
    require(not (HIDDEN_TARGET_FIELDS - set(row)), "target validation fields are missing")
    visible = {key: row.get(key) for key in TARGET_AGENT_FIELDS}
    require(
        all(isinstance(value, str) and value for value in visible.values()),
        "target agent input contains an invalid field",
    )
    require(set(visible) == TARGET_AGENT_FIELDS, "target agent whitelist drift")
    return visible  # type: ignore[return-value]


def experience_body(row: dict[str, object]) -> str:
    selected = {key: row.get(key) for key in EXPERIENCE_BODY_FIELDS}
    require(
        all(key in row for key in EXPERIENCE_BODY_FIELDS),
        "experience source field is missing",
    )
    require(
        all(isinstance(selected[key], str) and selected[key] for key in (
            "instance_id", "repo", "base_commit", "problem_statement", "patch",
            "FAIL_TO_PASS", "created_at", "version",
        )),
        "experience source field is invalid",
    )
    hints = selected["hints_text"]
    require(hints is None or isinstance(hints, str), "experience hints are invalid")
    return (
        f"SWE-ContextBench prior experience: {selected['instance_id']}\n"
        f"Repository: {selected['repo']}\n"
        f"Base commit: {selected['base_commit']}\n"
        f"Observed at: {selected['created_at']}\n\n"
        f"Prior problem:\n{selected['problem_statement']}\n\n"
        f"Prior hints:\n{hints or '(none)'}\n\n"
        f"Observed successful patch from the prior task:\n{selected['patch']}\n\n"
        f"Prior fail-to-pass tests:\n{selected['FAIL_TO_PASS']}"
    )


def build_mark_payload(
    context: dict[str, object],
    *,
    trace_id: str,
    used_ids: list[str],
    target_id: str,
    resolved: bool,
) -> dict[str, object]:
    require(trace_id and target_id and used_ids, "outcome mark identity is incomplete")
    require(all(isinstance(value, str) and value for value in used_ids), "used id is invalid")
    return {
        **context,
        "trace_id": trace_id,
        "caller_id": f"swe-contextbench:{target_id}",
        "used_ids": used_ids,
        "outcome": "success" if resolved else "failure",
    }


def verify_sources(
    manifest: dict[str, object],
    *,
    experience_path: Path,
    related_path: Path,
    relationship_path: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    files = manifest.get("dataset_files")
    require(isinstance(files, dict), "dataset file lock is missing")
    paths = {
        "SWEContextBench_Lite_Experience.parquet": experience_path,
        "SWEContextBench_Related_Lite.parquet": related_path,
        "SWEContextBench_Relationship.parquet": relationship_path,
    }
    for name, path in paths.items():
        spec = files.get(name)
        require(isinstance(spec, dict), f"dataset lock is missing {name}")
        require(path.is_file(), f"dataset file is missing: {path}")
        require(path.stat().st_size == spec.get("bytes"), f"dataset byte drift: {name}")
        require(sha256_file(path) == spec.get("sha256"), f"dataset hash drift: {name}")

    experiences = index_rows(load_parquet_rows(experience_path), label="experience")
    targets = index_rows(load_parquet_rows(related_path), label="target")
    relationships = load_parquet_rows(relationship_path)
    relation_pairs = {
        (row.get("related_instance_id"), row.get("experience_instance_id"))
        for row in relationships
    }
    cases = manifest.get("cases")
    require(isinstance(cases, list) and len(cases) == 12, "gate must contain exactly 12 cases")
    seen_targets: set[str] = set()
    for case in cases:
        require(isinstance(case, dict), "case must be an object")
        target_id = case.get("target_id")
        positive_id = case.get("experience_id")
        negative_id = case.get("unrelated_experience_id")
        require(
            all(isinstance(value, str) and value for value in (target_id, positive_id, negative_id)),
            "case identity is invalid",
        )
        require(target_id not in seen_targets, f"duplicate target: {target_id}")
        seen_targets.add(target_id)
        require(target_id in targets, f"target source is missing: {target_id}")
        require(positive_id in experiences, f"experience source is missing: {positive_id}")
        require(negative_id in experiences, f"negative source is missing: {negative_id}")
        target = targets[target_id]
        positive = experiences[positive_id]
        negative = experiences[negative_id]
        require((target_id, positive_id) in relation_pairs, "official relation edge is missing")
        require((target_id, negative_id) not in relation_pairs, "negative is officially related")
        require(
            target.get("repo") == positive.get("repo") == negative.get("repo") == case.get("repo"),
            "case repository pairing drift",
        )
        require(
            str(positive.get("created_at")) < str(target.get("created_at"))
            and str(negative.get("created_at")) < str(target.get("created_at")),
            "memory source must predate target",
        )
        require(positive_id != negative_id, "positive and negative memory must be disjoint")
        target_visible = target_agent_input(target)
        require(sha256_json(target_visible) == case.get("target_agent_input_sha256"), "target input drift")
        for prefix, row in (("target", target), ("experience", positive), ("unrelated", negative)):
            require(sha256_json(row) == case.get(f"{prefix}_row_sha256"), f"{prefix} row drift")
        require(
            hashlib.sha256(str(target["patch"]).encode()).hexdigest()
            == case.get("target_patch_sha256"),
            "target patch drift",
        )
        require(
            hashlib.sha256(str(target["test_patch"]).encode()).hexdigest()
            == case.get("target_test_patch_sha256"),
            "target test patch drift",
        )
        require(
            hashlib.sha256(experience_body(positive).encode()).hexdigest()
            == case.get("experience_body_sha256"),
            "experience body drift",
        )
        require(
            hashlib.sha256(experience_body(negative).encode()).hexdigest()
            == case.get("unrelated_body_sha256"),
            "unrelated body drift",
        )
        require(
            case.get("target_patch_sha256")
            not in {case.get("experience_patch_sha256"), case.get("unrelated_patch_sha256")},
            "target solution equals a memory solution",
        )
        require(
            isinstance(case.get("docker_image_tag"), str)
            and case["docker_image_tag"].startswith("jiayuanz3/swecontextbench:"),
            "official Docker image tag is invalid",
        )
        require(
            isinstance(case.get("docker_image_digest"), str)
            and DOCKER_DIGEST_RE.fullmatch(case["docker_image_digest"]) is not None,
            "official Docker image digest is not frozen",
        )
    return experiences, targets


def runtime_identity(
    *, server_bin: str, worker_bin: str, cli_bin: str
) -> dict[str, object]:
    binaries = {
        "server": Path(server_bin).resolve(),
        "worker": Path(worker_bin).resolve(),
        "cli": Path(cli_bin).resolve(),
    }
    for name, path in binaries.items():
        require(path.is_file(), f"{name} binary is missing: {path}")
    prompt_contract = {
        "target_agent_fields": sorted(TARGET_AGENT_FIELDS),
        "hidden_target_fields": sorted(HIDDEN_TARGET_FIELDS),
        "experience_body_fields": sorted(EXPERIENCE_BODY_FIELDS),
        "recall": {"limit": 5, "budget_tokens": 8192, "mode": "fast"},
    }
    return {
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "gate_runtime_sha256": sha256_file(Path(gr.__file__).resolve()),
        "prompt_contract_sha256": sha256_json(prompt_contract),
        "binaries": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in binaries.items()
        },
    }


def retain_resource(
    client: gr.ApiClient,
    context: dict[str, object],
    *,
    source_id: str,
    row: dict[str, object],
) -> tuple[str, str]:
    body = experience_body(row)
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    response = client.post(
        "/v1/episodes",
        {
            **context,
            "source_ref": f"swe-contextbench:experience:{source_id}",
            "observed_at": row["created_at"],
            "payload": {
                "resource": {
                    "uri": f"swe-contextbench://experience/{source_id}",
                    "mime_type": "text/markdown",
                    "content_hash": f"sha256:{body_sha}",
                    "kind": "document",
                    "revision": str(row["base_commit"]),
                    "body": body,
                }
            },
        },
    )
    resource_id = response.get("resource_id")
    require(isinstance(resource_id, str) and resource_id, "retain omitted resource id")
    return resource_id, body_sha


def rehearse(
    manifest: dict[str, object],
    experiences: dict[str, dict[str, object]],
    targets: dict[str, dict[str, object]],
    *,
    database_url: str,
    port: int,
    server_bin: str,
    worker_bin: str,
    cli_bin: str,
    embed_model: str,
    output: Path,
) -> dict[str, object]:
    tenant_id, api_key = gr.provision_tenant(cli_bin, database_url, "swe-context-n12")
    server = gr.Server(
        server_bin,
        database_url,
        port,
        embed_model=embed_model,
        log_path=output.with_suffix(".server.log"),
    )
    records: list[dict[str, object]] = []
    try:
        server.start()
        client = gr.ApiClient(port, api_key, tenant_id)
        contexts: dict[tuple[str, str], dict[str, object]] = {}
        resources: dict[tuple[str, str], tuple[str, str]] = {}
        for case in manifest["cases"]:
            target_id = case["target_id"]
            for arm, field in (("related", "experience_id"), ("unrelated", "unrelated_experience_id")):
                source_id = case[field]
                context = client.bind_context(
                    f"swe-context-{target_id}-{arm}",
                    subject_ref=f"swe-context:{target_id}:{arm}",
                    actor_ref="swe-contextbench-runner",
                    scope_ref=f"swe-context:{target_id}:{arm}",
                    agent_node_ref="swe-contextbench-agent",
                )
                contexts[(target_id, arm)] = context
                resources[(target_id, arm)] = retain_resource(
                    client, context, source_id=source_id, row=experiences[source_id]
                )
        completed = gr.drain_worker(worker_bin, database_url, embed_model)
        require(completed == 24, f"worker completed {completed} jobs, expected 24")

        for case in manifest["cases"]:
            target_id = case["target_id"]
            query = target_agent_input(targets[target_id])["problem_statement"]
            for arm in ("related", "unrelated"):
                context = contexts[(target_id, arm)]
                resource_id, body_sha = resources[(target_id, arm)]
                started = time.perf_counter()
                recalled = client.post(
                    "/v1/recall",
                    {**context, "query": query, "limit": 5, "budget_tokens": 8192, "mode": "fast"},
                )
                latency_ms = int(round((time.perf_counter() - started) * 1000))
                require(recalled.get("degraded") is False, "recall was degraded")
                items = recalled.get("items")
                citations = recalled.get("citations")
                trace_id = recalled.get("trace_id")
                require(isinstance(items, list) and items, "recall returned no context")
                require(isinstance(citations, list) and isinstance(trace_id, str), "recall proof is incomplete")
                matches = [c for c in citations if c.get("resource_id") == resource_id]
                require(len(matches) == 1, "expected resource citation is missing or duplicated")
                verification = matches[0].get("verification")
                require(isinstance(verification, dict) and verification.get("status") == "verified", "receipt is not verified")
                trace_query = urllib.parse.urlencode(context)
                trace = client.get(f"/v1/traces/{trace_id}?{trace_query}")
                require(trace.get("context_items") == items, "trace context pairing drift")
                used_ids = [item.get("unit_id") for item in items]
                require(all(isinstance(value, str) and value for value in used_ids), "recall unit id is invalid")
                records.append(
                    {
                        "target_id": target_id,
                        "arm": arm,
                        "source_id": case["experience_id" if arm == "related" else "unrelated_experience_id"],
                        "resource_id": resource_id,
                        "resource_body_sha256": body_sha,
                        "trace_id": trace_id,
                        "trace_sha256": sha256_json(trace),
                        "context_sha256": sha256_json(items),
                        "receipt_sha256": sha256_json(verification),
                        "returned_unit_ids": used_ids,
                        "rendered_tokens": trace.get("token_estimate"),
                        "latency_ms": latency_ms,
                        "future_mark_payload_sha256": sha256_json(
                            build_mark_payload(
                                context,
                                trace_id=trace_id,
                                used_ids=used_ids,
                                target_id=target_id,
                                resolved=True,
                            )
                        ),
                    }
                )
    finally:
        server.stop()
    return {
        "schema_version": 1,
        "classification": "no_model_adapter_and_retrieval_rehearsal_not_task_success",
        "manifest_sha256": sha256_json(manifest),
        "database": database_url.rsplit("/", 1)[-1],
        "database_persisted": False,
        "model_calls": 0,
        "cost_usd": 0,
        "runtime_identity": runtime_identity(
            server_bin=server_bin, worker_bin=worker_bin, cli_bin=cli_bin
        ),
        "worker_completed": 24,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "rehearse"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--experience-parquet", type=Path, required=True)
    parser.add_argument("--related-parquet", type=Path, required=True)
    parser.add_argument("--relationship-parquet", type=Path, required=True)
    parser.add_argument("--database-url", default=DEFAULT_BASE_DATABASE_URL)
    parser.add_argument("--port", type=int, default=39441)
    parser.add_argument("--embed-model", default="small")
    parser.add_argument("--server-bin", default=str(ROOT / "target/debug/memphant-server"))
    parser.add_argument("--worker-bin", default=str(ROOT / "target/debug/memphant-worker"))
    parser.add_argument("--cli-bin", default=str(ROOT / "target/debug/memphant-cli"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    experiences, targets = verify_sources(
        manifest,
        experience_path=args.experience_parquet,
        related_path=args.related_parquet,
        relationship_path=args.relationship_parquet,
    )
    if args.command == "validate":
        print(json.dumps({"cases": 12, "manifest_sha256": sha256_json(manifest)}))
        return 0
    require(args.output is not None, "--output is required for rehearse")
    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    result = rehearse(
        manifest,
        experiences,
        targets,
        database_url=database_url,
        port=args.port,
        server_bin=args.server_bin,
        worker_bin=args.worker_bin,
        cli_bin=args.cli_bin,
        embed_model=args.embed_model,
        output=args.output,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"records": len(result["records"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
