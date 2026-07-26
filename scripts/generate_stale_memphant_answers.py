#!/usr/bin/env python3
"""Generate official-format STALE answers through packaged MemPhant.

The dataset adapter is deliberately whitelist-only: only ``haystack_session``,
``timestamps``, and ``probing_queries`` can reach MemPhant or the reader. STALE
ground-truth fields are never included in an ingest or reader payload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_run_memphant as gate  # noqa: E402
import run_reader  # noqa: E402
from provider_attempts import (  # noqa: E402
    ProviderAttemptLedger,
    fresh_paid_usage,
    validate_provider_attempt_ledger,
)
from gate_runtime import episode_retain_payload, reexec_through_scratch_db  # noqa: E402
from run_stale import load_records, sha256_file, verify_dataset  # noqa: E402


GENERATION_MANIFEST = ROOT / "benchmarks" / "manifests" / "stale_generation.v1.json"
STALE_MANIFEST = ROOT / "benchmarks" / "manifests" / "stale.lock.json"
READER_LATTICE = ROOT / "benchmarks" / "manifests" / "reader_lattices.v1.json"
OPENAPI = ROOT / "openapi" / "memphant.v1.json"
DEFAULT_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"

REQUESTED_MODEL = "openai/gpt-5.6-luna-pro"
CANONICAL_MODEL = "openai/gpt-5.6-luna-pro-20260709"
MODEL = CANONICAL_MODEL
REASONING_EFFORT = "high"
EMBED_MODEL = "small"
RECALL_LIMIT = 10
EVIDENCE_BUDGET_TOKENS = 8192
RECALL_MODE = "deep"
NO_ABSTAIN_INSTRUCTION = (
    " Never set abstain=true. If evidence is insufficient, state that explicitly "
    "in the answer field."
)

SYSTEM_PROMPTS = {
    "dim1": (
        "You are a helpful assistant. Accurately answer the question using ONLY "
        "the retrieved memory evidence. Resolve temporal conflicts in favor of "
        "the latest supported user state. "
        + run_reader.READER_OUTPUT_CONTRACT
        + NO_ABSTAIN_INSTRUCTION
    ),
    "dim2": (
        "You are a helpful assistant. Accurately answer the question using ONLY "
        "the retrieved memory evidence. If the question presupposes a user state "
        "that conflicts with later evidence, explicitly correct the premise before "
        "helping. " + run_reader.READER_OUTPUT_CONTRACT + NO_ABSTAIN_INSTRUCTION
    ),
    "dim3": (
        "You are a helpful assistant. Respond to the latest query directly using "
        "ONLY the retrieved memory evidence, applying the latest supported user "
        "state without requiring the user to restate it. "
        + run_reader.READER_OUTPUT_CONTRACT
        + NO_ABSTAIN_INSTRUCTION
    ),
}
PROMPT_TEMPLATE = (
    "Evidence (retrieved memory items, most relevant first):\n\n"
    "{evidence}\n\n{query_label}: {query}"
)
UPSTREAM_ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "uid",
        "target_model",
        "target_model_responses",
        "target_model_meta",
    ],
    "properties": {
        "uid": {"type": "string", "minLength": 1},
        "target_model": {"const": MODEL},
        "target_model_responses": {
            "type": "object",
            "additionalProperties": False,
            "required": ["dim1_response", "dim2_response", "dim3_response"],
            "properties": {
                "dim1_response": {"type": "string", "minLength": 1},
                "dim2_response": {"type": "string", "minLength": 1},
                "dim3_response": {"type": "string", "minLength": 1},
            },
        },
        "target_model_meta": {
            "type": "object",
            "additionalProperties": False,
            "required": ["dim1_meta", "dim2_meta", "dim3_meta"],
            "properties": {
                dimension: {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "elapsed_seconds",
                        "usage",
                        "cache_hit",
                        "fresh_call",
                        "trace_id",
                        "returned_items",
                        "evidence_sha256",
                        "degraded",
                    ],
                    "properties": {
                        "elapsed_seconds": {"type": "number", "minimum": 0},
                        "usage": {
                            "type": "object",
                            "required": ["cost"],
                            "properties": {"cost": {"type": "number", "minimum": 0}},
                        },
                        "cache_hit": {"type": "boolean"},
                        "fresh_call": {"type": "boolean"},
                        "trace_id": {"type": "string", "minLength": 1},
                        "returned_items": {"type": "integer", "minimum": 0},
                        "evidence_sha256": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                        "degraded": {"const": False},
                    },
                }
                for dimension in ("dim1_meta", "dim2_meta", "dim3_meta")
            },
        },
    },
}
UPSTREAM_META_KEYS = frozenset(
    UPSTREAM_ROW_SCHEMA["properties"]["target_model_meta"]["properties"][
        "dim1_meta"
    ]["required"]
)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def prompt_hashes() -> dict[str, str]:
    disposition = {
        "contract_revision": "memphant.evidence_disposition.v1",
        "status": "supported",
        "answer_policy": "answer_normally",
        "reason": "PROMPT_CONTRACT_REASON",
    }
    return {
        dimension: hashlib.sha256(
            (
                f"{SYSTEM_PROMPTS[dimension]}\x1e"
                + build_reader_prompt(
                    ["PROMPT_CONTRACT_EVIDENCE"],
                    "PROMPT_CONTRACT_QUERY",
                    dimension,
                    disposition,
                )
            ).encode()
        ).hexdigest()
        for dimension in ("dim1", "dim2", "dim3")
    }


def format_session_body(index: int, timestamp: str, turns: list[dict]) -> str:
    body = f"[session {index + 1:04d}] [date {timestamp}]\n"
    for turn in turns:
        role = turn.get("role")
        content = turn.get("content")
        if role not in {"user", "assistant"}:
            raise ValueError(f"session {index} has invalid role {role!r}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"session {index} has empty content")
        body += f"{role}: {content}\n"
    return body


def build_record_plan(record: dict) -> dict:
    uid = record.get("uid")
    sessions = record.get("haystack_session")
    timestamps = record.get("timestamps")
    queries = record.get("probing_queries")
    if not isinstance(uid, str) or not uid:
        raise ValueError("STALE record is missing uid")
    if not isinstance(sessions, list) or not isinstance(timestamps, list):
        raise ValueError(f"STALE record {uid} lacks sessions or timestamps")
    if len(sessions) != len(timestamps) or not sessions:
        raise ValueError(f"STALE record {uid} session/timestamp counts differ")
    if not isinstance(queries, dict):
        raise ValueError(f"STALE record {uid} lacks probing_queries")

    ordered = []
    for source_index, (timestamp, turns) in enumerate(zip(timestamps, sessions)):
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise ValueError(f"STALE record {uid} has invalid timestamp")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"STALE record {uid} has empty session")
        ordered.append((timestamp, source_index, turns))
    ordered.sort(key=lambda item: (item[0], item[1]))

    clean_queries = {}
    for dimension in ("dim1", "dim2", "dim3"):
        value = queries.get(f"{dimension}_query")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"STALE record {uid} lacks {dimension}_query")
        clean_queries[dimension] = value

    clean_sessions = [
        {
            "timestamp": timestamp,
            "body": format_session_body(index, timestamp, turns),
        }
        for index, (timestamp, _source_index, turns) in enumerate(ordered)
    ]
    return {
        "uid": uid,
        "conflict_type": record.get("type"),
        "record_sha256": sha256_json(record),
        "sessions": clean_sessions,
        "queries": clean_queries,
    }


def build_reader_prompt(
    evidence: list[str], query: str, dimension: str, disposition: dict
) -> str:
    if dimension not in SYSTEM_PROMPTS:
        raise ValueError(f"unknown STALE dimension: {dimension}")
    rendered = []
    for rank, body in enumerate(evidence, 1):
        rendered.append(f"--- evidence item {rank} ---\n{body.strip()}")
    if not rendered:
        rendered.append("(no evidence was retrieved)")
    prompt = PROMPT_TEMPLATE.format(
        evidence="\n\n".join(rendered),
        query_label="Latest Query" if dimension == "dim3" else "Question",
        query=query,
    )
    return f"MemPhant evidence disposition: {canonical_json(disposition)}\n\n{prompt}"


def select_plans(
    plans: list[dict],
    limit: int | None,
    pilot_manifest: dict | None = None,
    dataset_sha256: str | None = None,
) -> tuple[list[dict], dict]:
    if limit is not None and pilot_manifest is not None:
        raise ValueError("--limit and --selection-manifest are mutually exclusive")
    if pilot_manifest is not None:
        records = pilot_manifest.get("records")
        if (
            pilot_manifest.get("protocol") != "stale-paired-pilot-v1"
            or pilot_manifest.get("dataset_sha256") != dataset_sha256
            or not isinstance(records, list)
            or len(records) != 4
            or sorted(record.get("conflict_type") for record in records)
            != ["T1", "T1", "T2", "T2"]
        ):
            raise ValueError("STALE pilot requires exactly two T1 and two T2 records")
        by_uid = {plan["uid"]: plan for plan in plans}
        selected = []
        seen = set()
        for record in records:
            uid = record.get("uid")
            plan = by_uid.get(uid)
            if (
                not isinstance(uid, str)
                or uid in seen
                or plan is None
                or record
                != {
                    key: plan.get(key)
                    for key in ("uid", "conflict_type", "record_sha256")
                }
            ):
                raise ValueError("STALE pilot selection record mismatch")
            seen.add(uid)
            selected.append(plan)
        return selected, {
            "method": "answer_blind_paired_manifest",
            "manifest_sha256": sha256_json(pilot_manifest),
            "upstream_revision": pilot_manifest.get("upstream_revision"),
            "source_record_count": len(plans),
            "record_count": 4,
            "query_count": 12,
            "smoke_only": False,
            "promotion_ineligible": True,
        }
    smoke = limit is not None
    if smoke and (limit < 1 or limit >= len(plans)):
        raise ValueError("STALE --limit must select a non-empty strict subset")
    selected = plans[:limit] if smoke else plans
    return selected, {
        "method": "pinned_dataset_prefix" if smoke else "full_pinned_dataset",
        "limit": limit,
        "source_record_count": len(plans),
        "smoke_only": smoke,
        "promotion_ineligible": smoke,
    }


def structured_state_contract(*, smoke: bool) -> dict:
    mode = os.environ.get("MEMPHANT_STRUCTURED_STATE")
    if smoke:
        if mode != "off":
            raise ValueError(
                "STALE smoke requires structured state off "
                "(MEMPHANT_STRUCTURED_STATE=off)"
            )
        return {"enabled": False, "model": None, "prompt_sha256": None}
    if mode != "on":
        return {"enabled": False, "model": None, "prompt_sha256": None}
    model = os.environ.get("MEMPHANT_STRUCTURED_STATE_MODEL", "").strip()
    prompt_path = os.environ.get("MEMPHANT_STRUCTURED_STATE_PROMPT_PATH", "").strip()
    if not model or not prompt_path:
        raise ValueError(
            "enabled structured state requires explicit model and prompt path"
        )
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    prompt = prompt.removesuffix("\r\n").removesuffix("\n")
    if not prompt.strip():
        raise ValueError("structured state prompt must not be empty")
    return {
        "enabled": True,
        "model": model,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }


def build_answer_row(uid: str, responses: dict[str, dict]) -> dict:
    return {
        "uid": uid,
        "target_model": MODEL,
        "target_model_responses": {
            f"{dimension}_response": responses[dimension]["answer"]
            for dimension in ("dim1", "dim2", "dim3")
        },
        "target_model_meta": {
            f"{dimension}_meta": {
                key: value
                for key, value in responses[dimension].items()
                if key in UPSTREAM_META_KEYS
            }
            for dimension in ("dim1", "dim2", "dim3")
        },
    }


def validate_answer_row(row: dict) -> None:
    if set(row) != {
        "uid",
        "target_model",
        "target_model_responses",
        "target_model_meta",
    }:
        raise ValueError("answer row keys do not match the pinned schema")
    if (
        not isinstance(row["uid"], str)
        or not row["uid"]
        or row["target_model"] != MODEL
    ):
        raise ValueError("answer row uid or target_model is invalid")
    responses = row["target_model_responses"]
    response_keys = {f"dim{index}_response" for index in (1, 2, 3)}
    if (
        not isinstance(responses, dict)
        or set(responses) != response_keys
        or any(
            not isinstance(value, str) or not value.strip()
            for value in responses.values()
        )
    ):
        raise ValueError(
            "target_model_responses must contain exactly three non-empty strings"
        )
    meta = row["target_model_meta"]
    meta_keys = {f"dim{index}_meta" for index in (1, 2, 3)}
    required_meta = UPSTREAM_META_KEYS
    if not isinstance(meta, dict) or set(meta) != meta_keys:
        raise ValueError("target_model_meta must contain exactly three dimensions")
    for facts in meta.values():
        if not isinstance(facts, dict) or set(facts) != required_meta:
            raise ValueError(
                "target_model_meta dimension provenance does not match the pinned schema"
            )
        if (
            facts["cache_hit"] is not False
            or facts["fresh_call"] is not True
        ):
            raise ValueError("target_model_meta provenance is invalid")
        if not fresh_paid_usage(facts):
            raise ValueError("target_model_meta usage is invalid")
        if (
            not isinstance(facts["trace_id"], str)
            or not facts["trace_id"]
            or not isinstance(facts["returned_items"], int)
            or facts["returned_items"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", facts["evidence_sha256"])
            or facts["degraded"] is not False
        ):
            raise ValueError("target_model_meta trace facts are invalid")


def answer_row_sha256(row: dict) -> str:
    return sha256_json(row)


def validate_resume(answers: dict, proof: dict, fingerprint: str) -> set[str]:
    if answers.get("summary", {}).get("generation_fingerprint") != fingerprint:
        raise ValueError("resume answer fingerprint mismatch")
    if proof.get("generation_fingerprint") != fingerprint:
        raise ValueError("resume proof fingerprint mismatch")
    answer_rows = answers.get("data")
    proof_rows = proof.get("records")
    if not isinstance(answer_rows, list) or not isinstance(proof_rows, list):
        raise ValueError("resume files are malformed")
    ledger = proof.get("provider_attempt_ledger")
    if ledger is not None:
        validate_provider_attempt_ledger(ledger)
        if proof.get("provider_attempt_ledger_sha256") != ledger.get("attempts_sha256"):
            raise ValueError("resume provider-attempt ledger hash mismatch")
        if ledger.get("provider_attempts") != len(proof_rows) * 3:
            raise ValueError("resume provider-attempt ledger count mismatch")
    answer_by_uid = {row.get("uid"): row for row in answer_rows}
    proof_by_uid = {row.get("uid"): row for row in proof_rows}
    if (
        len(answer_by_uid) != len(answer_rows)
        or len(proof_by_uid) != len(proof_rows)
        or set(answer_by_uid) != set(proof_by_uid)
    ):
        raise ValueError("resume UID sets must exactly match")
    for uid, row in answer_by_uid.items():
        if proof_by_uid[uid].get("answer_row_sha256") != answer_row_sha256(row):
            raise ValueError(f"resume answer hash mismatch for {uid}")
        dimensions = proof_by_uid[uid].get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != {
            "dim1",
            "dim2",
            "dim3",
        }:
            raise ValueError(f"resume trace coverage is incomplete for {uid}")
        for facts in dimensions.values():
            if (
                not isinstance(facts, dict)
                or not isinstance(facts.get("trace_id"), str)
                or not facts["trace_id"]
                or facts.get("degraded") is not False
            ):
                raise ValueError(f"resume trace coverage is invalid for {uid}")
            attempts = facts.get("provider_attempts")
            if ledger is not None and (
                not isinstance(attempts, list)
                or len(attempts) != 1
                or "response" not in attempts[0]
                or facts.get("parse_status") != "parsed"
            ):
                raise ValueError(f"resume provider-attempt coverage is invalid for {uid}")
    return set(answer_by_uid)


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def binary_fingerprint(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"required packaged binary is missing: {path}")
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def require_fresh_smoke_artifacts(args: argparse.Namespace, selection: dict) -> None:
    if not selection["smoke_only"]:
        return
    proof_path = args.proof or args.out.with_suffix(args.out.suffix + ".proof.json")
    checkpoint_path = args.checkpoint or args.out.with_suffix(
        args.out.suffix + ".checkpoint.json"
    )
    paths = (
        args.out,
        proof_path,
        checkpoint_path,
        proof_path.with_suffix(proof_path.suffix + ".attempts.json"),
        proof_path.parent / "stale-memphant-server.log",
    )
    existing = [str(path) for path in paths if path.exists()]
    if args.cache_dir.exists() and (
        not args.cache_dir.is_dir() or any(args.cache_dir.iterdir())
    ):
        existing.append(str(args.cache_dir))
    if existing:
        raise ValueError(
            "STALE fresh smoke requires a new output/cache directory; found: "
            + ", ".join(existing)
        )


def validate_generation_manifest(manifest: dict) -> None:
    if manifest.get("prompt_sha256") != prompt_hashes():
        raise ValueError("STALE generation prompt hash mismatch")
    if manifest.get("output_schema_sha256") != sha256_json(UPSTREAM_ROW_SCHEMA):
        raise ValueError("STALE generation output schema hash mismatch")
    if manifest.get("reader_response_contract_sha256") != sha256_json(
        run_reader.response_contract("openrouter", "reader")
    ):
        raise ValueError("STALE reader response contract hash mismatch")
    if manifest.get("reader") != {
        "requested_model": REQUESTED_MODEL,
        "canonical_model_snapshot": CANONICAL_MODEL,
        "reasoning_effort": REASONING_EFFORT,
    }:
        raise ValueError("STALE generation reader contract mismatch")
    if manifest.get("smoke") != {"structured_state": "off"}:
        raise ValueError("STALE smoke structured-state contract mismatch")
    if manifest.get("dataset_lock_sha256") != sha256_file(STALE_MANIFEST):
        raise ValueError("STALE dataset lock hash mismatch")
    if manifest.get("reader_lattice_sha256") != sha256_file(READER_LATTICE):
        raise ValueError("STALE reader lattice hash mismatch")
    if manifest.get("retrieval") != {
        "budget_tokens": EVIDENCE_BUDGET_TOKENS,
        "cross_rerank": False,
        "embed_model": EMBED_MODEL,
        "limit": RECALL_LIMIT,
        "mode": RECALL_MODE,
    }:
        raise ValueError("STALE retrieval contract mismatch")


def runtime_contract(
    args: argparse.Namespace, dataset_sha256: str, structured_state: dict
) -> dict:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "binaries": {
            "server": binary_fingerprint(args.server_bin),
            "worker": binary_fingerprint(args.worker_bin),
            "cli": binary_fingerprint(args.cli_bin),
        },
        "dataset_sha256": dataset_sha256,
        "embed_model": EMBED_MODEL,
        "evidence_budget_tokens": EVIDENCE_BUDGET_TOKENS,
        "git_commit": commit,
        "limit": RECALL_LIMIT,
        "mode": RECALL_MODE,
        "openapi_sha256": sha256_file(OPENAPI),
        "reader_lattice_sha256": sha256_file(READER_LATTICE),
        "harness_sha256": {
            "generator": sha256_file(Path(__file__)),
            "gate_run_memphant": sha256_file(ROOT / "scripts" / "gate_run_memphant.py"),
            "gate_runtime": sha256_file(ROOT / "scripts" / "gate_runtime.py"),
            "provider_attempts": sha256_file(ROOT / "scripts" / "provider_attempts.py"),
            "reader": sha256_file(ROOT / "scripts" / "run_reader.py"),
            "stale_bootstrap": sha256_file(
                ROOT / "benchmarks" / "stale" / "harness_bootstrap.py"
            ),
        },
        "cross_rerank": False,
        "structured_state": structured_state,
    }


def provision_tenant(cli_bin: Path, database_url: str, uid: str) -> tuple[str, str]:
    name = f"stale-{uid}"
    result = gate.sh(
        [
            str(cli_bin),
            "admin",
            "create-tenant",
            "--name",
            name,
            "--database-url",
            database_url,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"create-tenant failed for {uid}: {result.stderr.strip()}")
    match = re.search(r"tenant_created id=(\S+)", result.stdout)
    if match is None:
        raise RuntimeError(f"could not parse tenant id for {uid}")
    tenant_id = match.group(1)
    result = gate.sh(
        [
            str(cli_bin),
            "admin",
            "create-key",
            "--tenant",
            tenant_id,
            "--max-trust",
            "trusted_system",
            "--database-url",
            database_url,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"create-key failed for {uid}: {result.stderr.strip()}")
    api_key = result.stdout.strip().splitlines()[-1].strip()
    if not api_key.startswith("mk_"):
        raise RuntimeError(f"could not parse API key for {uid}")
    return tenant_id, api_key


def bind_record_context(
    client: gate.ApiClient, uid: str
) -> tuple[str, str, str, str, int]:
    external_ref = f"stale:{uid}"
    response = client.put(
        f"/v1/context-bindings/{urllib.parse.quote(external_ref, safe='')}",
        {
            "subject": {"external_ref": external_ref, "kind": "user"},
            "actor": {"external_ref": external_ref, "kind": "user"},
            "scope": {"external_ref": f"{external_ref}:root", "kind": "stale_record"},
            "agent_node": {"external_ref": f"{external_ref}:agent"},
        },
    )
    keys = (
        "subject_id",
        "scope_id",
        "actor_id",
        "agent_node_id",
        "subject_generation",
    )
    if any(key not in response for key in keys):
        raise RuntimeError(f"context binding returned incomplete identity for {uid}")
    return tuple(response[key] for key in keys)


def ingest_plan(
    client: gate.ApiClient,
    plan: dict,
    subject_id: str,
    scope_id: str,
    actor_id: str,
    agent_node_id: str,
    subject_generation: int,
) -> int:
    context = {
        "subject_id": subject_id,
        "scope_id": scope_id,
        "actor_id": actor_id,
        "agent_node_id": agent_node_id,
        "subject_generation": subject_generation,
    }
    for index, session in enumerate(plan["sessions"]):
        response = client.post(
            "/v1/episodes",
            episode_retain_payload(
                context,
                source_ref=f"stale:{plan['uid']}:session:{index + 1:04d}",
                observed_at=session["timestamp"],
                source_kind="user",
                body=session["body"],
            ),
        )
        if not response.get("episode_id"):
            raise RuntimeError(
                f"retain returned no episode_id for {plan['uid']} session {index}"
            )
    return len(plan["sessions"])


def recall_plan(
    client: gate.ApiClient,
    subject_id: str,
    scope_id: str,
    actor_id: str,
    agent_node_id: str,
    subject_generation: int,
    query: str,
) -> tuple[list[str], str, dict, list[dict]]:
    response = client.post(
        "/v1/recall",
        {
            "subject_id": subject_id,
            "scope_id": scope_id,
            "actor_id": actor_id,
            "agent_node_id": agent_node_id,
            "subject_generation": subject_generation,
            "query": query,
            "limit": RECALL_LIMIT,
            "budget_tokens": EVIDENCE_BUDGET_TOKENS,
            "mode": RECALL_MODE,
        },
    )
    if response.get("degraded") is not False:
        raise RuntimeError("STALE recall degraded or omitted degraded=false")
    trace_id = response.get("trace_id")
    items = response.get("items")
    if not isinstance(trace_id, str) or not trace_id:
        raise RuntimeError("STALE recall omitted trace_id")
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("body"), str)
        for item in items
    ):
        raise RuntimeError("STALE recall returned malformed items")
    deep = response.get("deep")
    disposition = deep.get("evidence") if isinstance(deep, dict) else None
    if (
        not isinstance(disposition, dict)
        or set(disposition)
        != {"contract_revision", "status", "answer_policy", "reason"}
        or disposition.get("contract_revision")
        != "memphant.evidence_disposition.v1"
        or not isinstance(disposition.get("reason"), str)
        or not disposition["reason"].strip()
    ):
        raise RuntimeError("STALE recall omitted valid Deep evidence disposition")
    citations = response.get("citations")
    by_unit = {
        citation.get("unit_id"): citation
        for citation in citations
        if isinstance(citations, list) and isinstance(citation, dict)
    } if isinstance(citations, list) else {}
    receipts = []
    for item in items:
        verification = by_unit.get(item.get("unit_id"), {}).get("verification")
        receipt = verification.get("receipt") if isinstance(verification, dict) else None
        if (
            not isinstance(verification, dict)
            or verification.get("status") != "verified"
            or not isinstance(receipt, dict)
        ):
            raise RuntimeError("STALE recall item lacks a verified evidence receipt")
        receipts.append(receipt)
    trace_query = urllib.parse.urlencode(
        {
            "subject_id": subject_id,
            "scope_id": scope_id,
            "actor_id": actor_id,
            "agent_node_id": agent_node_id,
            "subject_generation": subject_generation,
        }
    )
    trace = client.get(f"/v1/traces/{trace_id}?{trace_query}")
    if not isinstance(trace, dict) or trace.get("id") != trace_id:
        raise RuntimeError(f"STALE trace coverage missing for {trace_id}")
    return [item["body"] for item in items], trace_id, disposition, receipts


def run_reader_dimension(
    reader: run_reader.ReaderCli,
    dimension: str,
    query: str,
    evidence: list[str],
    disposition: dict,
) -> dict:
    prompt = build_reader_prompt(evidence, query, dimension, disposition)
    fresh_before = reader.fresh_calls
    cached_before = reader.cached_calls
    reader.set_provider_attempt_limit(reader.provider_attempts + 1)
    reply = reader.call("reader", SYSTEM_PROMPTS[dimension], prompt)
    output = run_reader.parse_reader_output(reply)
    if output["abstain"] or not output["answer"]:
        raise RuntimeError(f"STALE reader abstained for {dimension}")
    metadata = reader.last_call_metadata
    if not fresh_paid_usage(metadata):
        raise RuntimeError("STALE reader response omitted positive OpenRouter usage cost")
    required = {
        "response_id", "requested_model", "served_model", "provider", "usage",
        "elapsed_seconds", "retry_index", "parse_status", "request_sha256",
        "result_sha256",
    }
    if set(metadata) != required:
        raise RuntimeError("STALE reader response omitted provenance metadata")
    if (
        metadata["requested_model"] != REQUESTED_MODEL
        or metadata["served_model"] != CANONICAL_MODEL
    ):
        raise RuntimeError("STALE reader requested/served model does not match its pin")
    return {
        "answer": output["answer"],
        **metadata,
        "cache_hit": reader.cached_calls > cached_before,
        "fresh_call": reader.fresh_calls > fresh_before,
    }


def dimension_artifacts(
    result: dict,
    trace_id: str,
    evidence: list[str],
    disposition: dict,
    receipts: list[dict],
    attempt_slice: list[dict],
) -> tuple[dict, dict]:
    trace_facts = {
        "trace_id": trace_id,
        "returned_items": len(evidence),
        "evidence_sha256": sha256_json(evidence),
        "degraded": False,
        "evidence_disposition": disposition,
        "verified_receipt_count": len(receipts),
        "verified_receipts_sha256": sha256_json(receipts),
    }
    answer_facts = {
        key: value for key, value in (result | trace_facts).items()
        if key in UPSTREAM_META_KEYS or key == "answer"
    }
    return answer_facts, trace_facts | {
        "response_id": result["response_id"],
        "provider_attempts": attempt_slice,
        "parse_status": "parsed",
    }


def output_objects(
    fingerprint: str,
    expected_items: int,
    answers: list[dict],
    records: list[dict],
    runtime: dict,
    drain_completed: int,
    selection: dict,
    provider_attempt_ledger: dict | None = None,
) -> tuple[dict, dict]:
    output = {
        "summary": {
            "target_model": MODEL,
            "canonical_model_snapshot": CANONICAL_MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "num_items": len(answers),
            "expected_items": expected_items,
            "complete": len(answers) == expected_items,
            "generation_fingerprint": fingerprint,
            "source_record_count": selection["source_record_count"],
            "smoke_only": selection["smoke_only"],
            "promotion_ineligible": selection["promotion_ineligible"],
        },
        "data": answers,
    }
    proof = {
        "benchmark": "STALE",
        "generation_fingerprint": fingerprint,
        "runtime": runtime,
        "drain_completed": drain_completed,
        "trace_count": sum(len(row.get("dimensions", {})) for row in records),
        "expected_trace_count": len(records) * 3,
        "records": records,
        "selection": selection,
        "smoke_only": selection["smoke_only"],
        "promotion_ineligible": selection["promotion_ineligible"],
    }
    if provider_attempt_ledger is not None:
        validate_provider_attempt_ledger(provider_attempt_ledger)
        proof["provider_attempt_ledger"] = provider_attempt_ledger
        proof["provider_attempt_ledger_sha256"] = provider_attempt_ledger[
            "attempts_sha256"
        ]
    validate_resume(output, proof, fingerprint)
    return output, proof


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--port", type=int, default=39432)
    parser.add_argument(
        "--server-bin", type=Path, default=ROOT / "target/debug/memphant-server"
    )
    parser.add_argument(
        "--worker-bin", type=Path, default=ROOT / "target/debug/memphant-worker"
    )
    parser.add_argument(
        "--cli-bin", type=Path, default=ROOT / "target/debug/memphant-cli"
    )
    parser.add_argument("--max-calls", type=int, default=1200)
    parser.add_argument(
        "--limit", type=int, help="deterministic prefix size for a smoke-only run"
    )
    parser.add_argument(
        "--selection-manifest",
        type=Path,
        help="answer-blind four-record paired pilot manifest",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="allow an unpinned synthetic dataset; valid only with --dry-run",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.fixture and not args.dry_run:
        raise ValueError("--fixture is valid only with --dry-run")
    manifest = json.loads(GENERATION_MANIFEST.read_text(encoding="utf-8"))
    validate_generation_manifest(manifest)
    dataset_bytes = args.dataset.read_bytes()
    dataset_sha = hashlib.sha256(dataset_bytes).hexdigest()
    if args.fixture:
        rows = load_records(args.dataset)
    else:
        stale_lock = json.loads(STALE_MANIFEST.read_text(encoding="utf-8"))
        rows = verify_dataset(args.dataset, stale_lock)
    all_plans = [build_record_plan(row) for row in rows]
    if len({plan["uid"] for plan in all_plans}) != len(all_plans):
        raise ValueError("STALE dataset contains duplicate uids")
    pilot_manifest = (
        json.loads(args.selection_manifest.read_text(encoding="utf-8"))
        if args.selection_manifest
        else None
    )
    if pilot_manifest is not None:
        stale_lock = json.loads(STALE_MANIFEST.read_text(encoding="utf-8"))
        if pilot_manifest.get("upstream_revision") != stale_lock["dataset"]["revision"]:
            raise ValueError("STALE pilot upstream revision mismatch")
    plans, selection = select_plans(
        all_plans, args.limit, pilot_manifest, dataset_sha
    )
    structured_state = structured_state_contract(smoke=selection["smoke_only"])

    if args.dry_run:
        atomic_write_json(
            args.out,
            {
                "source_status": "dry_run_no_answers",
                "dataset_sha256": dataset_sha,
                "record_count": len(plans),
                "session_count": sum(len(plan["sessions"]) for plan in plans),
                "uids_sha256": sha256_json(sorted(plan["uid"] for plan in plans)),
                "body_sha256": sha256_json(
                    [
                        sha256_json(session)
                        for plan in plans
                        for session in plan["sessions"]
                    ]
                ),
                "query_sha256": sha256_json([plan["queries"] for plan in plans]),
                "prompt_sha256": prompt_hashes(),
                "output_schema_sha256": sha256_json(UPSTREAM_ROW_SCHEMA),
                "selection": selection,
                "smoke_only": selection["smoke_only"],
                "promotion_ineligible": selection["promotion_ineligible"],
            },
        )
        return 0

    require_fresh_smoke_artifacts(args, selection)
    reexec_through_scratch_db(args.database_url)
    args.database_url = os.environ["DATABASE_URL"]
    runtime = runtime_contract(args, dataset_sha, structured_state)
    fingerprint = sha256_json(
        {
            "manifest_sha256": sha256_file(GENERATION_MANIFEST),
            "reader": manifest["reader"],
            "retrieval": manifest["retrieval"],
            "prompt_sha256": manifest["prompt_sha256"],
            "output_schema_sha256": manifest["output_schema_sha256"],
            "runtime": runtime,
            "selection": selection,
        }
    )
    proof_path = args.proof or args.out.with_suffix(args.out.suffix + ".proof.json")
    attempt_ledger_path = proof_path.with_suffix(proof_path.suffix + ".attempts.json")
    checkpoint_path = args.checkpoint or args.out.with_suffix(
        args.out.suffix + ".checkpoint.json"
    )
    answers: list[dict] = []
    proof_records: list[dict] = []
    previous_drain_completed = 0
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        answer_obj = checkpoint.get("answers", {})
        proof_obj = checkpoint.get("proof", {})
        validate_resume(answer_obj, proof_obj, fingerprint)
        atomic_write_json(args.out, answer_obj)
        atomic_write_json(proof_path, proof_obj)
        answers = answer_obj["data"]
        proof_records = proof_obj["records"]
        previous_drain_completed = int(proof_obj.get("drain_completed", 0))
    elif args.out.exists() or proof_path.exists():
        if not args.out.exists() or not proof_path.exists():
            raise ValueError("resume requires both answer and proof files")
        answer_obj = json.loads(args.out.read_text(encoding="utf-8"))
        proof_obj = json.loads(proof_path.read_text(encoding="utf-8"))
        validate_resume(answer_obj, proof_obj, fingerprint)
        answers = answer_obj["data"]
        proof_records = proof_obj["records"]
        previous_drain_completed = int(proof_obj.get("drain_completed", 0))
        atomic_write_json(checkpoint_path, {"answers": answer_obj, "proof": proof_obj})
    completed = {row["uid"] for row in answers}
    pending = [plan for plan in plans if plan["uid"] not in completed]
    if not pending:
        return 0

    reader = run_reader.ReaderCli(
        "openrouter",
        REQUESTED_MODEL,
        REQUESTED_MODEL,
        args.cache_dir,
        args.max_calls,
        REASONING_EFFORT,
    )
    attempt_ledger = ProviderAttemptLedger(attempt_ledger_path, fingerprint)
    if completed:
        embedded = json.loads(proof_path.read_text(encoding="utf-8")).get(
            "provider_attempt_ledger"
        )
        sidecar = attempt_ledger.snapshot()
        if (
            not isinstance(embedded, dict)
            or embedded.get("attempts_sha256") != sidecar.get("attempts_sha256")
            or embedded.get("provider_attempts") != sidecar.get("provider_attempts")
        ):
            raise ValueError("resume provider-attempt sidecar mismatch")
    reader.set_provider_attempt_hook(attempt_ledger.record)
    server_log = proof_path.parent / "stale-memphant-server.log"
    server = gate.Server(
        str(args.server_bin),
        args.database_url,
        args.port,
        EMBED_MODEL,
        log_path=server_log,
    )
    credentials = {}
    try:
        server.start()
        for index, plan in enumerate(pending, 1):
            tenant_id, api_key = provision_tenant(
                args.cli_bin, args.database_url, plan["uid"]
            )
            client = gate.ApiClient(args.port, api_key, tenant_id)
            (
                subject_id,
                scope_id,
                actor_id,
                agent_node_id,
                subject_generation,
            ) = bind_record_context(client, plan["uid"])
            ingested = ingest_plan(
                client,
                plan,
                subject_id,
                scope_id,
                actor_id,
                agent_node_id,
                subject_generation,
            )
            client.conn.close()
            credentials[plan["uid"]] = (
                tenant_id,
                api_key,
                subject_id,
                scope_id,
                actor_id,
                agent_node_id,
                subject_generation,
                ingested,
            )
            print(
                f"STALE ingest [{index}/{len(pending)}] {plan['uid']}", file=sys.stderr
            )

        current_drain_completed = gate.drain_worker(
            str(args.worker_bin), args.database_url, EMBED_MODEL
        )
        if current_drain_completed < sum(value[7] for value in credentials.values()):
            raise RuntimeError(
                "worker drain completed fewer jobs than ingested sessions"
            )
        drain_completed = previous_drain_completed + current_drain_completed

        for index, plan in enumerate(pending, 1):
            (
                tenant_id,
                api_key,
                subject_id,
                scope_id,
                actor_id,
                agent_node_id,
                subject_generation,
                ingested,
            ) = credentials[plan["uid"]]
            client = gate.ApiClient(args.port, api_key, tenant_id)
            dimension_results = {}
            dimension_proof = {}
            for dimension in ("dim1", "dim2", "dim3"):
                evidence, trace_id, disposition, receipts = recall_plan(
                    client,
                    subject_id,
                    scope_id,
                    actor_id,
                    agent_node_id,
                    subject_generation,
                    plan["queries"][dimension],
                )
                attempt_start = len(reader.provider_attempt_log)
                result = run_reader_dimension(
                    reader,
                    dimension,
                    plan["queries"][dimension],
                    evidence,
                    disposition,
                )
                attempt_slice = reader.provider_attempt_log[attempt_start:]
                dimension_results[dimension], dimension_proof[dimension] = (
                    dimension_artifacts(
                        result,
                        trace_id,
                        evidence,
                        disposition,
                        receipts,
                        attempt_slice,
                    )
                )
            client.conn.close()
            answer_row = build_answer_row(plan["uid"], dimension_results)
            validate_answer_row(answer_row)
            proof_row = {
                "uid": plan["uid"],
                "tenant_id": tenant_id,
                "subject_id": subject_id,
                "scope_id": scope_id,
                "actor_id": actor_id,
                "agent_node_id": agent_node_id,
                "subject_generation": subject_generation,
                "sessions_ingested": ingested,
                "answer_row_sha256": answer_row_sha256(answer_row),
                "dimensions": dimension_proof,
            }
            answers.append(answer_row)
            proof_records.append(proof_row)
            output, proof = output_objects(
                fingerprint,
                len(plans),
                answers,
                proof_records,
                runtime,
                drain_completed,
                selection,
                attempt_ledger.snapshot(),
            )
            atomic_write_json(checkpoint_path, {"answers": output, "proof": proof})
            atomic_write_json(args.out, output)
            atomic_write_json(proof_path, proof)
            print(
                f"STALE answer [{index}/{len(pending)}] {plan['uid']}", file=sys.stderr
            )
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
