#!/usr/bin/env python3
"""Build the pinned MemPhant arm for STATE-Bench's Agent Learning Track.

Only official training conversations and train/test ID manifests are read. Test
task definitions, environments, requirements, answers, and scorer fields never
enter an ingest, retrieval query, generated agent module, or runner command.
"""

from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_runtime as gate  # noqa: E402


STATE_LOCK = ROOT / "benchmarks" / "manifests" / "state_bench.lock.json"
ARM_MANIFEST = ROOT / "benchmarks" / "manifests" / "state_bench_memphant.v1.json"
AGENT_SOURCE = ROOT / "benchmarks" / "state_bench" / "memphant_memory_agent.py"
OPENAPI = ROOT / "openapi" / "memphant.v1.json"
DOMAINS = ("travel", "customer_support", "shopping_assistant")
TOP_K = 3
RECALL_MODE = "deep"
BUDGET_TOKENS = 4096
EMBED_MODEL = "small"
PROTOCOL_ID = "state_bench_v0.8.0_gpt54"
DEFAULT_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"


def map_attempt_source_kind(attempt_type: str) -> str:
    mapping = {
        "tool_attempt.success": "tool",
        "tool_attempt.failure": "tool",
    }
    try:
        return mapping[attempt_type]
    except KeyError as error:
        raise ValueError(f"unmapped STATE-Bench attempt_type: {attempt_type!r}") from error

EVIDENCE_SCHEMA = {
    "additionalProperties": False,
    "required": [
        "attempt_type",
        "domain",
        "source_task_id",
        "sequence",
        "user_context",
        "assistant_context",
        "tool_name",
        "arguments",
        "result",
        "outcome",
    ],
    "properties": {
        "attempt_type": {"enum": ["tool_attempt.success", "tool_attempt.failure"]},
        "domain": {"type": "string"},
        "source_task_id": {"type": "string"},
        "sequence": {"type": "integer"},
        "user_context": {"type": "string"},
        "assistant_context": {"type": "string"},
        "tool_name": {"type": "string"},
        "arguments": {"type": "object"},
        "result": {},
        "outcome": {"enum": ["success", "failure"]},
    },
    "type": "object",
}
RUNNER_INPUT_SCHEMA = {
    "agent_class": "MemphantMemoryAgent",
    "agent_reasoning_level": "high",
    "domains": list(DOMAINS),
    "num_runs": 5,
    "protocol_id": PROTOCOL_ID,
    "retrieval_top_k": TOP_K,
    "split": "test",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def atomic_write_json(path: Path, value: object, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    if private:
        temporary.chmod(0o600)
    os.replace(temporary, path)


def official_retrieval_instruction(repo: Path) -> str:
    source = repo / "state_bench" / "agents" / "state_bench.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id == "RETRIEVAL_SYSTEM_INSTRUCTION"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise ValueError("official STATE-Bench retrieval instruction was not found")


def validate_manifest(manifest: dict, official_repo: Path) -> None:
    expected = {
        "evidence_schema_sha256": sha256_json(EVIDENCE_SCHEMA),
        "official_retrieval_instruction_sha256": hashlib.sha256(
            official_retrieval_instruction(official_repo).encode()
        ).hexdigest(),
        "runner_input_schema_sha256": sha256_json(RUNNER_INPUT_SCHEMA),
    }
    if manifest.get("hashes") != expected:
        raise ValueError("STATE-Bench MemPhant arm schema or prompt hash mismatch")
    if manifest.get("state_bench_lock_sha256") != sha256_file(STATE_LOCK):
        raise ValueError("STATE-Bench acquisition lock hash mismatch")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"training conversation {field} must be a string")
    return value


def build_attempts(domain: str, task_id: str, trajectory: dict) -> list[dict]:
    conversation = trajectory.get("conversation")
    if not isinstance(conversation, list):
        raise ValueError(f"{domain}/{task_id} lacks a conversation list")
    last_user = ""
    attempts: list[dict] = []
    sequence = 0
    for message in conversation:
        if not isinstance(message, dict):
            raise ValueError(f"{domain}/{task_id} has a malformed message")
        role = message.get("role")
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"{domain}/{task_id} has an unsupported role")
        content = _string(message.get("content", ""), "content")
        if role == "user":
            last_user = content
        if role != "assistant":
            continue
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            raise ValueError(f"{domain}/{task_id} tool_calls must be a list")
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                raise ValueError(f"{domain}/{task_id} has a malformed tool call")
            name = _string(tool_call.get("name"), "tool name")
            arguments = tool_call.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError(f"{domain}/{task_id} tool arguments must be an object")
            result = tool_call.get("result")
            failed = isinstance(result, dict) and bool(result.get("error"))
            outcome = "failure" if failed else "success"
            evidence = {
                "attempt_type": f"tool_attempt.{outcome}",
                "domain": domain,
                "source_task_id": task_id,
                "sequence": sequence,
                "user_context": last_user,
                "assistant_context": content,
                "tool_name": name,
                "arguments": arguments,
                "result": result,
                "outcome": outcome,
            }
            recall_query = (
                f"Domain: {domain}\nUser context: {last_user}\n"
                f"Planned tool: {name}\nArguments: "
                + json.dumps(arguments, sort_keys=True, separators=(",", ":"))
            )
            attempt_id = sha256_json(evidence)
            attempts.append(
                evidence
                | {
                    "attempt_id": attempt_id,
                    "mark_outcome": outcome,
                    "recall_query": recall_query,
                    "body": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                }
            )
            sequence += 1
    return attempts


def load_official_domain(repo: Path, domain: str) -> tuple[list[dict], list[str]]:
    split_path = repo / "state_bench" / "domains" / domain / "splits" / "train_test.json"
    split = json.loads(split_path.read_text(encoding="utf-8"))["splits"]
    train_ids = [str(value) for value in split["train"]]
    test_ids = [str(value) for value in split["test"]]
    trajectory_dir = repo / "datasets" / "train_task_trajectories" / domain
    files = {path.stem: path for path in trajectory_dir.glob("*.json")}
    if set(files) != set(train_ids):
        raise ValueError(f"{domain} train trajectories do not match the official split")
    attempts = []
    for task_id in train_ids:
        source = json.loads(files[task_id].read_text(encoding="utf-8"))
        # Whitelist: only conversation reaches the mapping function.
        attempts.extend(build_attempts(domain, task_id, {"conversation": source.get("conversation")}))
    return attempts, test_ids


def build_fixture_plans(source: dict) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    plans: dict[str, list[dict]] = {}
    test_ids: dict[str, list[str]] = {}
    domains = source.get("domains")
    if not isinstance(domains, dict):
        raise ValueError("fixture lacks domains")
    for domain, value in domains.items():
        plans[domain] = []
        test_ids[domain] = [str(item) for item in value.get("test_ids", [])]
        for trajectory in value.get("train_trajectories", []):
            # Whitelist: task_id and conversation only.
            plans[domain].extend(
                build_attempts(
                    domain,
                    _string(trajectory.get("task_id"), "task_id"),
                    {"conversation": trajectory.get("conversation")},
                )
            )
    return plans, test_ids


def runner_contract(agent_model: str, output_root: str) -> dict:
    commands = []
    for domain in DOMAINS:
        commands.append(
            [
                "uv",
                "run",
                "python",
                "-m",
                "state_bench.scripts.run_batch",
                "--domain",
                domain,
                "--split",
                "test",
                "--agent-class",
                "MemphantMemoryAgent",
                "--agent-model-name",
                agent_model,
                "--agent-model-reasoning-level",
                "high",
                "--num-runs",
                "5",
                "--retrieve-learnings-top-k",
                "3",
                "--output-dir",
                str(Path(output_root) / domain),
            ]
        )
    return {
        "protocol_id": PROTOCOL_ID,
        "agent_source": str(AGENT_SOURCE),
        "agent_destination": "<STATE_BENCH_REPO>/agents/memphant_memory_agent.py",
        "setup": [
            "install",
            "-m",
            "0644",
            str(AGENT_SOURCE),
            "<STATE_BENCH_REPO>/agents/memphant_memory_agent.py",
        ],
        "environment": {
            "MEMPHANT_STATE_BENCH_CONFIG": "<private-runtime-config.json>",
            "MEMPHANT_STATE_BENCH_RETRIEVAL_PROOF": "<retrieval-proof.jsonl>",
        },
        "commands": commands,
    }


def validate_checkpoint(
    checkpoint: dict, fingerprint: str, expected_ids: dict[str, set[str]]
) -> None:
    if checkpoint.get("fingerprint") != fingerprint:
        raise ValueError("checkpoint fingerprint mismatch")
    domains = checkpoint.get("domains")
    if not isinstance(domains, dict) or set(domains) != set(expected_ids):
        raise ValueError("checkpoint domains mismatch")
    for domain, ids in expected_ids.items():
        attempts = domains[domain].get("attempts")
        if not isinstance(attempts, dict) or set(attempts) != ids:
            raise ValueError(f"checkpoint {domain} attempt IDs mismatch")
        for attempt_id, record in attempts.items():
            if not isinstance(record, dict) or "episode_id" not in record or "mark" not in record:
                raise ValueError(f"checkpoint {domain}/{attempt_id} record is incomplete")


def validate_complete_checkpoint(
    checkpoint: dict, plans: dict[str, list[dict]]
) -> None:
    by_id = {
        domain: {attempt["attempt_id"]: attempt for attempt in domain_plans}
        for domain, domain_plans in plans.items()
    }
    for domain, attempts in by_id.items():
        state = checkpoint["domains"][domain]
        if not isinstance(state.get("reflected"), dict):
            raise ValueError(f"checkpoint {domain} has no reflect proof")
        for attempt_id, attempt in attempts.items():
            record = state["attempts"][attempt_id]
            if not isinstance(record.get("episode_id"), str) or not record["episode_id"]:
                raise ValueError(f"checkpoint {domain}/{attempt_id} has no episode proof")
            mark = record.get("mark")
            if (
                not isinstance(mark, dict)
                or mark.get("accepted") is not True
                or mark.get("outcome") != attempt["mark_outcome"]
                or not isinstance(mark.get("trace_id"), str)
                or not mark["trace_id"]
                or not isinstance(mark.get("used_ids_sha256"), str)
                or len(mark["used_ids_sha256"]) != 64
            ):
                raise ValueError(f"checkpoint {domain}/{attempt_id} has invalid mark proof")


def install_agent(official_repo: Path) -> Path:
    destination = official_repo / "agents" / "memphant_memory_agent.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(AGENT_SOURCE.read_bytes())
    if sha256_file(destination) != sha256_file(AGENT_SOURCE):
        raise RuntimeError("installed STATE-Bench agent hash mismatch")
    return destination


def binary_hash(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"required packaged binary is missing: {path}")
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def runtime_contract(args: argparse.Namespace, official_repo: Path) -> dict:
    return {
        "binaries": {
            "server": binary_hash(args.server_bin),
            "cli": binary_hash(args.cli_bin),
        },
        "embed_model": EMBED_MODEL,
        "openapi_sha256": sha256_file(OPENAPI),
        "harness_sha256": {
            "builder": sha256_file(Path(__file__)),
            "agent": sha256_file(AGENT_SOURCE),
            "gate_runtime": sha256_file(ROOT / "scripts" / "gate_runtime.py"),
            "official_agent_base": sha256_file(official_repo / "state_bench" / "agents" / "base.py"),
            "official_agent_hook": sha256_file(
                official_repo / "state_bench" / "agents" / "state_bench.py"
            ),
        },
    }


def provision_domain(
    client_port: int,
    cli_bin: Path,
    database_url: str,
    domain: str,
) -> tuple[dict, gate.ApiClient]:
    tenant_id, api_key = gate.provision_tenant(
        str(cli_bin), database_url, name_prefix=f"state-bench-{domain}"
    )
    client = gate.ApiClient(client_port, api_key, tenant_id)
    context = client.bind_context(
        f"state-bench:{domain}",
        subject_ref=f"state-bench:{domain}:subject",
        actor_ref=f"state-bench:{domain}:actor",
        scope_ref=f"state-bench:{domain}:scope",
        agent_node_ref=f"state-bench:{domain}:agent",
        scope_kind="benchmark",
    )
    bound = {
        "tenant_id": tenant_id,
        "api_key": api_key,
        **context,
    }
    return bound, client


def execute_build(
    args: argparse.Namespace,
    plans: dict[str, list[dict]],
    fingerprint: str,
    runtime: dict,
    runner: dict,
) -> tuple[dict, dict]:
    expected_ids = {
        domain: {attempt["attempt_id"] for attempt in domain_plans}
        for domain, domain_plans in plans.items()
    }
    checkpoint_path = args.checkpoint or args.out.with_suffix(".checkpoint.json")
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        validate_checkpoint(checkpoint, fingerprint, expected_ids)
    else:
        checkpoint = {
            "fingerprint": fingerprint,
            "domains": {
                domain: {
                    "bound": None,
                    "reflected": None,
                    "attempts": {
                        attempt_id: {"episode_id": None, "mark": None}
                        for attempt_id in sorted(ids)
                    },
                }
                for domain, ids in expected_ids.items()
            },
        }
        atomic_write_json(checkpoint_path, checkpoint, private=True)

    server = gate.Server(
        str(args.server_bin),
        args.database_url,
        args.port,
        EMBED_MODEL,
        log_path=args.out.parent / "state-bench-memphant-server.log",
    )
    server.start()
    clients: dict[str, gate.ApiClient] = {}
    try:
        for domain, domain_plans in plans.items():
            domain_state = checkpoint["domains"][domain]
            bound = domain_state.get("bound")
            if bound is None:
                bound, client = provision_domain(
                    args.port, args.cli_bin, args.database_url, domain
                )
                domain_state["bound"] = bound
                atomic_write_json(checkpoint_path, checkpoint, private=True)
            else:
                client = gate.ApiClient(args.port, bound["api_key"], bound["tenant_id"])
            context = {
                key: bound[key]
                for key in (
                    "subject_id", "scope_id", "actor_id", "agent_node_id",
                    "subject_generation",
                )
            }
            clients[domain] = client
            for attempt in domain_plans:
                record = domain_state["attempts"][attempt["attempt_id"]]
                if record["episode_id"] is not None:
                    continue
                response = client.post(
                    "/v1/episodes",
                    gate.episode_retain_payload(
                        context,
                        source_ref=f"state-bench:{domain}:{attempt['attempt_id']}",
                        observed_at="2026-07-13T00:00:00Z",
                        source_kind=map_attempt_source_kind(attempt["attempt_type"]),
                        body=attempt["body"],
                    ),
                )
                episode_id = response.get("episode_id")
                if not isinstance(episode_id, str) or not episode_id:
                    raise RuntimeError("MemPhant retain returned no episode_id")
                record["episode_id"] = episode_id
                atomic_write_json(checkpoint_path, checkpoint, private=True)
            if domain_state.get("reflected") is None:
                reflected = client.post(
                    "/v1/reflect",
                    context,
                )
                domain_state["reflected"] = reflected
                atomic_write_json(checkpoint_path, checkpoint, private=True)
            for attempt in domain_plans:
                record = domain_state["attempts"][attempt["attempt_id"]]
                if record["mark"] is not None:
                    continue
                recalled = client.post(
                    "/v1/recall",
                    {
                        **context,
                        "query": attempt["recall_query"],
                        "limit": TOP_K,
                        "budget_tokens": BUDGET_TOKENS,
                        "mode": RECALL_MODE,
                    },
                )
                if recalled.get("degraded") is not False:
                    raise RuntimeError("training recall was degraded")
                items = recalled.get("items")
                if not isinstance(items, list) or not items or len(items) > TOP_K:
                    raise RuntimeError("training recall did not return valid top-3 evidence")
                used_ids = [item.get("unit_id") for item in items]
                if any(not isinstance(value, str) or not value for value in used_ids):
                    raise RuntimeError("training recall item lacks unit_id")
                marked = client.post(
                    "/v1/mark",
                    {
                        **context,
                        "trace_id": recalled["trace_id"],
                        "caller_id": f"state-bench-train:{attempt['attempt_id']}",
                        "used_ids": used_ids,
                        "outcome": attempt["mark_outcome"],
                    },
                )
                if marked.get("accepted") is not True:
                    raise RuntimeError("MemPhant rejected an outcome mark")
                record["mark"] = {
                    "accepted": True,
                    "trace_id": recalled["trace_id"],
                    "used_ids_sha256": sha256_json(used_ids),
                    "outcome": attempt["mark_outcome"],
                }
                atomic_write_json(checkpoint_path, checkpoint, private=True)
        validate_checkpoint(checkpoint, fingerprint, expected_ids)
        validate_complete_checkpoint(checkpoint, plans)
    finally:
        for client in clients.values():
            client.conn.close()
        server.stop()

    config = {
        "base_url": f"http://127.0.0.1:{args.port}",
        "domains": {
            domain: checkpoint["domains"][domain]["bound"] for domain in plans
        },
    }
    atomic_write_json(args.runtime_config, config, private=True)
    counts = {
        domain: collections.Counter(plan["mark_outcome"] for plan in domain_plans)
        for domain, domain_plans in plans.items()
    }
    proof = {
        "benchmark": "STATE-Bench",
        "protocol_id": PROTOCOL_ID,
        "fingerprint": fingerprint,
        "runtime": runtime,
        "runner": runner,
        "domains": {
            domain: {
                "attempt_count": len(domain_plans),
                "success_count": counts[domain]["success"],
                "failure_count": counts[domain]["failure"],
                "tenant_id": checkpoint["domains"][domain]["bound"]["tenant_id"],
                "scope_id": checkpoint["domains"][domain]["bound"]["scope_id"],
                "actor_id": checkpoint["domains"][domain]["bound"]["actor_id"],
                "attempt_records_sha256": sha256_json(
                    checkpoint["domains"][domain]["attempts"]
                ),
            }
            for domain, domain_plans in plans.items()
        },
        "complete": True,
    }
    output = {
        "status": "ready-for-official-runs",
        "protocol_id": PROTOCOL_ID,
        "runtime_config": str(args.runtime_config),
        "runner": runner,
        "proof_sha256": sha256_json(proof),
    }
    return output, proof


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--official-repo", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    result.add_argument("--proof", type=Path)
    result.add_argument("--checkpoint", type=Path)
    result.add_argument("--runtime-config", type=Path)
    result.add_argument("--fixture", type=Path)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    result.add_argument("--port", type=int, default=39433)
    result.add_argument("--server-bin", type=Path, default=ROOT / "target/debug/memphant-server")
    result.add_argument("--cli-bin", type=Path, default=ROOT / "target/debug/memphant-cli")
    result.add_argument(
        "--agent-model", default="openai/gpt-5.6-sol-pro-20260709"
    )
    result.add_argument("--output-root", default="outputs/memphant")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.fixture is not None and not args.dry_run:
        raise ValueError("--fixture is valid only with --dry-run")
    manifest = json.loads(ARM_MANIFEST.read_text(encoding="utf-8"))
    validate_manifest(manifest, args.official_repo)
    if args.fixture:
        plans, test_ids = build_fixture_plans(
            json.loads(args.fixture.read_text(encoding="utf-8"))
        )
    else:
        plans = {}
        test_ids = {}
        for domain in DOMAINS:
            plans[domain], test_ids[domain] = load_official_domain(args.official_repo, domain)
    runner = runner_contract(args.agent_model, args.output_root)
    corpus = {
        domain: {
            "attempt_count": len(domain_plans),
            "attempt_ids_sha256": sha256_json(
                sorted(plan["attempt_id"] for plan in domain_plans)
            ),
            "body_sha256": sha256_json([plan["body"] for plan in domain_plans]),
            "query_sha256": sha256_json(
                [plan["recall_query"] for plan in domain_plans]
            ),
            "success_count": sum(
                plan["mark_outcome"] == "success" for plan in domain_plans
            ),
            "failure_count": sum(
                plan["mark_outcome"] == "failure" for plan in domain_plans
            ),
            "test_id_count": len(test_ids[domain]),
            "test_ids_sha256": sha256_json(test_ids[domain]),
        }
        for domain, domain_plans in plans.items()
    }
    if args.dry_run:
        atomic_write_json(
            args.out,
            {
                "status": "dry-run-no-service-or-model-calls",
                "protocol_id": PROTOCOL_ID,
                "corpus": corpus,
                "runner": runner,
                "hashes": manifest["hashes"],
            },
        )
        return 0

    args.proof = args.proof or args.out.with_suffix(".proof.json")
    args.runtime_config = args.runtime_config or args.out.with_suffix(".runtime.json")
    runtime = runtime_contract(args, args.official_repo)
    installed_agent = install_agent(args.official_repo)
    runtime["installed_agent"] = {
        "path": str(installed_agent.resolve()),
        "sha256": sha256_file(installed_agent),
    }
    fingerprint = sha256_json(
        {
            "manifest_sha256": sha256_file(ARM_MANIFEST),
            "state_bench_lock_sha256": sha256_file(STATE_LOCK),
            "corpus": corpus,
            "runtime": runtime,
            "runner": runner,
        }
    )
    output, proof = execute_build(args, plans, fingerprint, runtime, runner)
    atomic_write_json(args.proof, proof)
    atomic_write_json(args.out, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
