#!/usr/bin/env python3
"""Generate gold-sealed Memora/FAMA answers through packaged MemPhant."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_runtime  # noqa: E402
import gate_common  # noqa: E402
import run_reader  # noqa: E402
import run_memora_fama  # noqa: E402
from provider_attempts import (  # noqa: E402
    ProviderAttemptLedger,
    fresh_paid_usage,
    provider_attempt_ledger_is_complete,
    validate_provider_attempt_ledger,
)


MEMORA_LOCK = ROOT / "benchmarks" / "manifests" / "memora.lock.json"
GENERATION_LOCK = ROOT / "benchmarks" / "manifests" / "memora_generation.v1.json"
READER_LATTICE = ROOT / "benchmarks" / "manifests" / "reader_lattices.v1.json"
OPENAPI = ROOT / "openapi" / "memphant.v1.json"
DEFAULT_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"
PERIODS = ("weekly", "monthly", "quarterly")
TASKS = ("remembering", "reasoning", "recommending")
ROLLUP_PREFIX_BY_QUESTION = {
    "activity_food_total_163":
        "quantity rollup quantity_event_v1/food_spending/food_spending (usd); "
        "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); filter=all; ",
    "activity_steps_total_163":
        "quantity rollup quantity_event_v1/daily_steps/daily_steps (steps); "
        "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); filter=all; ",
    "goal_food_expenses_coffee_163_0":
        "quantity rollup quantity_event_v1/food_spending/food_spending (usd); "
        "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); "
        "filter=expense_type=coffee; ",
    "goal_step_tracker_daily_steps_163_0":
        "quantity rollup quantity_event_v1/daily_steps/daily_steps (steps); "
        "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); filter=all; ",
    "activity_food_coffee_163":
        "quantity rollup quantity_event_v1/food_spending/food_spending (usd); "
        "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); "
        "filter=expense_type=coffee; ",
}
GOAL_COMPANION_BODY_BY_QUESTION = {
    "goal_food_expenses_coffee_163_0":
        "coffee_spending_goal item spending_limit: "
        "{\"expense_type\":\"coffee\",\"frequency\":\"weekly\","
        "\"target_amount\":\"30\"}",
    "goal_step_tracker_daily_steps_163_0":
        "fitness_goals item daily_step_goal: "
        "{\"frequency\":\"daily\",\"unit\":\"steps\",\"value\":\"8000\"}",
}


def is_goal_companion(question_id: str, body: str) -> bool:
    """Validate goal meaning without binding proof to one extractor field layout."""
    match = re.fullmatch(r"[^:\n]+ item [^:\n]+: (\{.*\})", body)
    if match is None:
        return False
    try:
        fields = json.loads(match.group(1))
    except json.JSONDecodeError:
        return False
    if not isinstance(fields, dict) or any(not isinstance(value, str) for value in fields.values()):
        return False
    if question_id == "goal_food_expenses_coffee_163_0":
        amount = next(
            (fields[key] for key in ("target_amount", "limit", "value") if key in fields),
            None,
        )
        cadence = fields.get("frequency") or fields.get("period")
        return (
            fields.get("expense_type") == "coffee"
            and amount == "30"
            and cadence in {"weekly", "week"}
        )
    if question_id == "goal_step_tracker_daily_steps_163_0":
        amount = next(
            (fields[key] for key in ("target", "value") if key in fields),
            None,
        )
        cadence = fields.get("frequency") or fields.get("period")
        return amount == "8000" and fields.get("unit") == "steps" and cadence == "daily"
    return False


ALLOWED_SPEAKERS = {"user", "user_agent", "ai_agent"}

MODEL_CANDIDATES = (
    "openai/gpt-5.6-luna-pro",
    "google/gemini-3.5-flash",
)
DEFAULT_MODEL = MODEL_CANDIDATES[0]
REQUESTED_MODEL = DEFAULT_MODEL
LUNA_CANONICAL_MODEL = "openai/gpt-5.6-luna-pro-20260709"
MODEL = DEFAULT_MODEL
STRUCTURED_STATE_MODEL = DEFAULT_MODEL
REASONING_EFFORT = "high"
RECALL_LIMIT = 10
EVIDENCE_BUDGET_TOKENS = 8192
RECALL_MODE = "deep"
EMBED_MODEL = "small"
BEHAVIOR_ENV = {
    "MEMPHANT_CROSS_RERANK": "0",
    "MEMPHANT_CROSS_RERANK_CANDIDATES": "fused-head",
    "MEMPHANT_EMBEDDINGS": EMBED_MODEL,
    "MEMPHANT_RECALL_POOL_DEPTH": "64",
    "MEMPHANT_RERANKER": "fastembed",
    "MEMPHANT_RERANK_BATCH_SIZE": "256",
    "MEMPHANT_RERANK_CANDIDATE_LIMIT": "64",
    "MEMPHANT_RERANK_MAX_LENGTH": "512",
    "MEMPHANT_RESOURCE_CHUNKS": "0",
    "MEMPHANT_STRUCTURED_STATE": "on",
    "MEMPHANT_STRUCTURED_STATE_MODEL": STRUCTURED_STATE_MODEL,
    "MEMPHANT_STRUCTURED_STATE_CONCURRENCY": "4",
    "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH": str(
        ROOT / "config" / "structured-state-v1.txt"
    ),
}


def configure_model(model: str, extractor_model: str | None = None) -> None:
    """Bind one paid arm before fingerprints, processes, or ledgers exist."""
    if model not in MODEL_CANDIDATES:
        raise ValueError(f"unsupported Memora model arm: {model}")
    extractor_model = extractor_model or model
    if extractor_model not in MODEL_CANDIDATES:
        raise ValueError(f"unsupported Memora extractor arm: {extractor_model}")
    global REQUESTED_MODEL, MODEL, STRUCTURED_STATE_MODEL
    REQUESTED_MODEL = model
    MODEL = model
    STRUCTURED_STATE_MODEL = extractor_model
    BEHAVIOR_ENV["MEMPHANT_STRUCTURED_STATE_MODEL"] = extractor_model
    # Luna Pro already fixes reasoning.mode=pro at the model route. Flash
    # defaults to medium, so its accuracy-first arm must request high explicitly.
    if extractor_model == "google/gemini-3.5-flash":
        BEHAVIOR_ENV["MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT"] = "high"
    else:
        BEHAVIOR_ENV.pop("MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT", None)
TEMPORAL_CONTRACT = {
    "evaluation_snapshot": "final state after full period/persona ingest and reflection drain",
    "official_question_date_semantics": "context only, not retrieval filtering",
    "question_date_use": "reader prompt context only",
    "session_date_use": "chronological ordering, retained dialogue text, and the explicit half-open group aggregation window",
}

SYSTEM_PROMPT = (
    "Answer the user's question accurately using ONLY the retrieved MemPhant "
    "evidence. Reconcile updates and deletions chronologically: use the latest "
    "supported user state and do not repeat superseded facts. Be specific and "
    "concise; never mention benchmark rubrics or hidden evidence. "
    + run_reader.READER_OUTPUT_CONTRACT
)
PROMPT_TEMPLATE = (
    "Question date: {question_date}\nQuestion: {question}\n\n"
    "Retrieved memory evidence (most relevant first):\n{evidence}"
)
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "period", "persona", "question_id", "question", "question_date",
        "task_type", "answer", "evidence", "trace",
    ],
    "properties": {
        "period": {"enum": list(PERIODS)},
        "persona": {"type": "string", "minLength": 1},
        "question_id": {"type": "string", "minLength": 1},
        "question": {"type": "string", "minLength": 1},
        "question_date": {"type": "string", "minLength": 1},
        "task_type": {"enum": [task.title() for task in TASKS]},
        "answer": {"type": "string", "minLength": 1},
        "evidence": {"type": "array"},
        "trace": {"type": "object"},
    },
}

ANSWER_KEYS = set(OUTPUT_SCHEMA["required"])


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


BANK_FORMAT_VERSION = 2
BANK_GROUP_KEYS = {
    "tenant_id", "subject_id", "scope_id", "actor_id", "agent_node_id",
    "subject_generation", "agent_level", "policy_revision",
}
BANK_EXCLUDED_TABLES = (
    "memphant.schema_migrations",
    "memphant.api_key",
    "memphant.event_outbox",
    "memphant.job_state",
    "memphant.retrieval_trace",
    "memphant.review_event",
    "memphant.review_event_unit",
)


def run_postgres_command(command: list[str], label: str) -> None:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Memora bank {label} failed: {result.stderr.strip()}")


def postgres_tool_identity(binary: str, database_url: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [binary, "--version"], cwd=ROOT, text=True, capture_output=True,
            check=False, timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"PostgreSQL tool did not start: {binary}") from error
    match = re.search(r"PostgreSQL\) (\d+)(?:\.|$)", result.stdout)
    if result.returncode != 0 or match is None:
        raise RuntimeError(f"cannot identify PostgreSQL tool: {binary}")
    server_major = psql_json(
        database_url,
        "select (current_setting('server_version_num')::int / 10000)::int as major",
    )[0]["major"]
    identity = {
        "binary": shutil.which(binary) or str(Path(binary).resolve()),
        "version": result.stdout.strip(),
        "major": int(match.group(1)),
        "server_major": server_major,
    }
    if identity["major"] != server_major:
        raise ValueError(
            f"PostgreSQL archive tool major {identity['major']} does not match server major {server_major}"
        )
    return identity


def database_bank_identity(database_url: str) -> dict[str, Any]:
    excluded = {table.rsplit(".", 1)[1] for table in BANK_EXCLUDED_TABLES}
    tables = [
        row["tablename"]
        for row in psql_json(
            database_url,
            "select tablename from pg_tables where schemaname = 'memphant' "
            "order by tablename",
        )
        if row["tablename"] not in excluded
    ]
    identity: dict[str, Any] = {"tables": {}}
    for table in tables:
        if not re.fullmatch(r"[a-z_]+", table):
            raise RuntimeError("Memora bank table identity is unsafe")
        result = subprocess.run(
            [
                "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--quiet",
                "--tuples-only", "--no-align", "--dbname", database_url,
                "--command",
                f'copy (select row_to_json(t)::text from memphant."{table}" t '
                "order by row_to_json(t)::text) to stdout",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Memora bank logical identity failed for {table}: "
                + result.stderr.decode(errors="replace").strip()
            )
        identity["tables"][table] = {
            "rows": len(result.stdout.splitlines()),
            "sha256": hashlib.sha256(result.stdout).hexdigest(),
        }
    identity["sha256"] = sha256_json(identity["tables"])
    return identity


def dump_extraction_bank(
    database_url: str, bank_dir: Path, pg_dump_bin: str = "pg_dump"
) -> tuple[Path, str, dict[str, Any]]:
    tool = postgres_tool_identity(pg_dump_bin, database_url)
    bank_dir.mkdir(parents=True, exist_ok=True)
    manifest = bank_dir / "manifest.json"
    temporary = bank_dir / ".bank.dump.tmp"
    if manifest.exists() or any(bank_dir.glob("*.dump")):
        raise ValueError("Memora bank directory is not empty")
    command = [
        pg_dump_bin, "--format=custom", "--data-only", "--schema=memphant",
        "--no-owner", "--no-acl", f"--file={temporary}",
    ]
    command.extend(
        f"--exclude-table-data={table}" for table in BANK_EXCLUDED_TABLES
    )
    command.append(database_url)
    try:
        run_postgres_command(command, "dump")
        digest = sha256_file(temporary)
        archive = bank_dir / f"{digest}.dump"
        temporary.replace(archive)
        return archive, digest, tool
    finally:
        temporary.unlink(missing_ok=True)


def _copy_bank_artifact(
    source: Path, bank_dir: Path, role: str, *, expected_sha256: str | None = None,
) -> dict[str, str]:
    if not source.is_file():
        raise ValueError(f"Memora bank construction artifact is missing: {source}")
    digest = sha256_file(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"Memora bank construction artifact hash mismatch: {source}")
    safe_role = re.sub(r"[^a-z0-9_-]+", "-", role.lower()).strip("-")
    suffix = "".join(source.suffixes) or ".artifact"
    destination = bank_dir / f"{digest}.{safe_role}{suffix}"
    if destination.exists():
        if sha256_file(destination) != digest:
            raise ValueError("Memora bank construction artifact destination collided")
    else:
        shutil.copyfile(source, destination)
    return {"role": role, "file": destination.name, "sha256": digest}


def validate_bank_construction(manifest: dict[str, Any], bank_dir: Path) -> None:
    construction = manifest.get("construction")
    if (
        not isinstance(construction, dict)
        or construction.get("kind") not in {"direct_extraction", "causal_composition"}
        or manifest.get("construction_sha256") != sha256_json(construction)
    ):
        raise ValueError("Memora bank construction identity is malformed")
    artifacts = construction.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Memora bank construction artifacts are missing")
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"role", "file", "sha256"}
            or not isinstance(artifact["role"], str)
            or not artifact["role"]
            or not isinstance(artifact["file"], str)
            or Path(artifact["file"]).name != artifact["file"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"]))
        ):
            raise ValueError("Memora bank construction artifact identity is malformed")
        path = bank_dir / artifact["file"]
        if not path.is_file() or sha256_file(path) != artifact["sha256"]:
            raise ValueError("Memora bank construction artifact hash mismatch")


def seal_causal_extraction_bank(
    database_url: str,
    bank_dir: Path,
    source_bank_dir: Path,
    composition_proof_path: Path,
    composition_ledger_dir: Path,
    *,
    extraction_plan_sha256: str,
    pg_dump_bin: str = "pg_dump",
) -> dict[str, Any]:
    """Seal an exact already-composed causal database without replaying providers."""
    source_manifest_path = source_bank_dir / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_archive_sha256 = source_manifest.get("archive_sha256")
    source_archive = source_bank_dir / str(source_manifest.get("archive", ""))
    source_ledger_sha256 = source_manifest.get("extractor_ledger_sha256")
    source_ledger = source_bank_dir / str(source_manifest.get("extractor_ledger", ""))
    if (
        not re.fullmatch(r"[0-9a-f]{64}", str(source_archive_sha256))
        or not source_archive.is_file()
        or sha256_file(source_archive) != source_archive_sha256
        or not re.fullmatch(r"[0-9a-f]{64}", str(source_ledger_sha256))
        or not source_ledger.is_file()
        or sha256_file(source_ledger) != source_ledger_sha256
    ):
        raise ValueError("Memora causal source bank proof is invalid")
    composition_proof = json.loads(composition_proof_path.read_text(encoding="utf-8"))
    ledger_hashes = composition_proof.get("ledgers")
    if (
        composition_proof.get("archive_sha256") != source_archive_sha256
        or not isinstance(ledger_hashes, dict)
        or not ledger_hashes
    ):
        raise ValueError("Memora causal composition proof does not bind the source bank")
    ledgers: list[tuple[Path, str]] = []
    for name, digest in sorted(ledger_hashes.items()):
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not re.fullmatch(r"[0-9a-f]{64}", str(digest))
        ):
            raise ValueError("Memora causal composition ledger identity is malformed")
        ledger = composition_ledger_dir / name
        if not ledger.is_file() or sha256_file(ledger) != digest:
            raise ValueError(f"Memora causal composition ledger hash mismatch: {name}")
        ledgers.append((ledger, digest))

    archive, archive_sha256, dump_tool = dump_extraction_bank(
        database_url, bank_dir, pg_dump_bin
    )
    if not archive.is_file():
        raise RuntimeError("Memora causal bank dump was not created")
    artifacts = [
        _copy_bank_artifact(source_manifest_path, bank_dir, "source_manifest"),
        _copy_bank_artifact(
            source_archive, bank_dir, "source_archive",
            expected_sha256=source_archive_sha256,
        ),
        _copy_bank_artifact(
            source_ledger, bank_dir, "source_extractor_ledger",
            expected_sha256=source_ledger_sha256,
        ),
        _copy_bank_artifact(composition_proof_path, bank_dir, "composition_proof"),
    ]
    artifacts.extend(
        _copy_bank_artifact(path, bank_dir, "composition_ledger", expected_sha256=digest)
        for path, digest in ledgers
    )
    copied_source_ledger = next(
        artifact for artifact in artifacts if artifact["role"] == "source_extractor_ledger"
    )
    database_identity = gate_common.database_schema_identity(
        database_url,
        "select 'migration:' || version from memphant.schema_migrations",
    )
    logical_identity = database_bank_identity(database_url)
    source_compiler_versions = source_manifest.get("compiler_versions")
    if (
        not isinstance(source_compiler_versions, list)
        or not source_compiler_versions
        or any(not isinstance(value, str) or not value for value in source_compiler_versions)
    ):
        raise ValueError("Memora causal source bank compiler identity is missing")
    composed_compiler_versions = [
        row["compiler_version"]
        for row in psql_json(
            database_url,
            "select distinct compiler_version from memphant.job_state "
            "where state = 'done' order by compiler_version",
        )
    ]
    if any(not isinstance(value, str) or not value for value in composed_compiler_versions):
        raise RuntimeError("Memora causal bank compiler identity is malformed")
    # Extraction-bank archives deliberately exclude transient job_state rows.
    # A data-only restore therefore inherits the compiler proof from the hashed
    # source manifest, while a genuinely composed live database may contribute
    # additional completed compiler identities.
    compiler_versions = sorted(set(source_compiler_versions + composed_compiler_versions))
    construction = {
        "kind": "causal_composition",
        "source_archive_sha256": source_archive_sha256,
        "source_logical_identity_sha256": source_manifest.get("logical_identity", {}).get("sha256"),
        "artifacts": artifacts,
    }
    manifest = {
        "format_version": BANK_FORMAT_VERSION,
        "archive": archive.name,
        "archive_sha256": archive_sha256,
        "database_identity": database_identity,
        "logical_identity": logical_identity,
        "postgres_major": dump_tool["major"],
        "pg_dump_version": dump_tool["version"],
        "extractor_model": source_manifest["extractor_model"],
        "extractor_ledger": copied_source_ledger["file"],
        "extractor_ledger_sha256": copied_source_ledger["sha256"],
        "extractor_summary": {
            "source": source_manifest.get("extractor_summary"),
            "causal_composition": composition_proof.get("final_run"),
        },
        "compiler_versions": compiler_versions,
        "groups": source_manifest["groups"],
        "extraction_plan_sha256": extraction_plan_sha256,
        "runtime_sha256": source_manifest["runtime_sha256"],
        "construction": construction,
        "construction_sha256": sha256_json(construction),
    }
    validate_bank_construction(manifest, bank_dir)
    run_reader.atomic_write_json(bank_dir / "manifest.json", manifest)
    return manifest


def restore_extraction_bank(
    database_url: str, bank_dir: Path, pg_restore_bin: str = "pg_restore"
) -> dict[str, Any]:
    manifest_path = bank_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != BANK_FORMAT_VERSION:
        raise ValueError("Memora bank format is unsupported")
    validate_bank_construction(manifest, bank_dir)
    digest = manifest.get("archive_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Memora bank archive hash is malformed")
    archive = bank_dir / f"{digest}.dump"
    if manifest.get("archive") != archive.name:
        raise ValueError("Memora bank archive name mismatch")
    if not archive.is_file() or sha256_file(archive) != digest:
        raise ValueError("Memora bank archive hash mismatch")
    ledger_name = manifest.get("extractor_ledger")
    ledger_digest = manifest.get("extractor_ledger_sha256")
    if (
        not isinstance(ledger_name, str)
        or not isinstance(ledger_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", ledger_digest)
    ):
        raise ValueError("Memora bank extractor ledger identity is malformed")
    ledger = bank_dir / ledger_name
    if not ledger.is_file() or sha256_file(ledger) != ledger_digest:
        raise ValueError("Memora bank extractor ledger hash mismatch")
    compiler_versions = manifest.get("compiler_versions")
    if (
        not isinstance(compiler_versions, list)
        or not compiler_versions
        or any(not isinstance(value, str) or not value for value in compiler_versions)
    ):
        raise ValueError("Memora bank compiler identity is missing")
    groups = manifest.get("groups")
    if (
        not isinstance(groups, dict)
        or not groups
        or any(
            not isinstance(label, str)
            or not isinstance(identity, dict)
            or set(identity) != BANK_GROUP_KEYS
            or any(
                not isinstance(identity[key], str) or not identity[key]
                for key in BANK_GROUP_KEYS - {"subject_generation", "agent_level"}
            )
            or type(identity["subject_generation"]) is not int
            or identity["subject_generation"] < 0
            or type(identity["agent_level"]) is not int
            or identity["agent_level"] < 0
            for label, identity in groups.items()
        )
    ):
        raise ValueError("Memora bank group identity is malformed")
    logical_identity = manifest.get("logical_identity")
    if (
        not isinstance(logical_identity, dict)
        or not isinstance(logical_identity.get("tables"), dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(logical_identity.get("sha256", "")))
        or sha256_json(logical_identity["tables"]) != logical_identity["sha256"]
    ):
        raise ValueError("Memora bank logical identity is malformed")
    current_database = gate_common.database_schema_identity(
        database_url,
        "select 'migration:' || version from memphant.schema_migrations",
    )
    if current_database != manifest.get("database_identity"):
        raise ValueError("Memora bank database schema identity mismatch")
    restore_tool = postgres_tool_identity(pg_restore_bin, database_url)
    if restore_tool["major"] != manifest.get("postgres_major"):
        raise ValueError("Memora bank PostgreSQL archive major mismatch")
    run_postgres_command(
        [
            pg_restore_bin, "--data-only", "--single-transaction", "--exit-on-error",
            "--no-owner", "--no-acl", f"--dbname={database_url}", str(archive),
        ],
        "restore",
    )
    if database_bank_identity(database_url) != manifest.get("logical_identity"):
        raise ValueError("Memora bank restored logical identity mismatch")
    return manifest


def bank_runtime_sha256(runtime_proof: dict[str, Any]) -> str:
    return sha256_json({
        "source": runtime_proof["source"],
        "dataset": runtime_proof["dataset"],
        "binaries": runtime_proof["binaries"],
        "openapi_sha256": runtime_proof["openapi_sha256"],
        "behavior_environment": runtime_proof["behavior_environment"],
    })


def extraction_plan_sha256(plans: list[dict]) -> str:
    return sha256_json([
        {
            "scope": plan["scope"],
            "date_range": plan["date_range"],
            "sessions": plan["sessions"],
        }
        for plan in plans
    ])


def psql_json(database_url: str, sql: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--quiet",
            "--tuples-only", "--no-align", "--dbname", database_url,
            "--command",
            f"select coalesce(json_agg(row_to_json(q)), '[]'::json) from ({sql}) q;",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Memora provenance export failed: " + result.stderr.strip())
    value = json.loads(result.stdout.strip())
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise RuntimeError("Memora provenance export returned malformed JSON")
    return value


def export_derived_sources(
    database_url: str, tenant_id: str, unit_ids: list[str]
) -> list[dict[str, Any]]:
    tenant = str(uuid.UUID(tenant_id))
    unique_ids = sorted({str(uuid.UUID(unit_id)) for unit_id in unit_ids})
    if not unique_ids:
        return []
    quoted_ids = ",".join(f"'{unit_id}'::uuid" for unit_id in unique_ids)
    rows = psql_json(
        database_url,
        "select unit.tenant_id::text as tenant_id, unit.id::text as unit_id, "
        "unit.body as memory_unit_body, unit.source_episode_id::text as source_episode_id, "
        "episode.body as source_episode_body "
        "from memphant.memory_unit unit "
        "left join memphant.episode episode "
        "on episode.tenant_id = unit.tenant_id and episode.id = unit.source_episode_id "
        f"where unit.tenant_id = '{tenant}'::uuid and unit.id in ({quoted_ids}) "
        "order by unit.id",
    )
    by_id = {row.get("unit_id"): row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != set(unique_ids):
        raise RuntimeError("Memora provenance export is missing a derived source unit")
    for unit_id in unique_ids:
        row = by_id[unit_id]
        if (
            row.get("tenant_id") != tenant
            or not isinstance(row.get("memory_unit_body"), str)
            or not row["memory_unit_body"].strip()
            or not isinstance(row.get("source_episode_id"), str)
            or not isinstance(row.get("source_episode_body"), str)
            or not row["source_episode_body"].strip()
        ):
            raise RuntimeError(f"Memora provenance export is incomplete for {unit_id}")
    return [by_id[unit_id] for unit_id in unique_ids]


def retrieval_proof(
    trace: dict[str, Any], evidence: list[dict[str, Any]], *, tenant_id: str,
    scope_id: str, actor_id: str, database_url: str,
) -> dict[str, Any]:
    if (
        trace.get("tenant_id") != tenant_id
        or trace.get("scope_id") != scope_id
        or trace.get("actor_id") != actor_id
        or not isinstance(trace.get("context_items"), list)
        or not isinstance(trace.get("citations"), list)
    ):
        raise RuntimeError("Memora trace tenant/scope/actor pairing failed")
    context_ids = [item.get("unit_id") for item in trace["context_items"]]
    if context_ids != [item.get("unit_id") for item in evidence]:
        raise RuntimeError("Memora trace context does not pair with returned evidence")
    derived_ids: list[str] = []
    direct_ids: list[str] = []
    for item in [*trace["context_items"], *trace["citations"]]:
        if not isinstance(item, dict) or not isinstance(item.get("unit_id"), str):
            raise RuntimeError("Memora trace contains a malformed source item")
        values = item.get("derived_from_unit_ids", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise RuntimeError("Memora trace contains malformed derived source IDs")
        if values:
            derived_ids.extend(values)
        else:
            direct_ids.append(item["unit_id"])
    sources = export_derived_sources(database_url, tenant_id, derived_ids)
    direct_sources = export_derived_sources(database_url, tenant_id, direct_ids)
    return {
        "trace": trace,
        "trace_sha256": sha256_json(trace),
        "derived_sources": sources,
        "derived_sources_sha256": sha256_json(sources),
        "direct_sources": direct_sources,
        "direct_sources_sha256": sha256_json(direct_sources),
    }


def format_session(session: dict[str, Any], period: str, persona: str) -> dict[str, Any]:
    session_id = session.get("session_id")
    date = session.get("date")
    if not isinstance(session_id, int) or not isinstance(date, str) or not date:
        raise ValueError("Memora session requires integer session_id and date")
    if session.get("persona") != persona:
        raise ValueError(f"Memora session {session_id} persona mismatch")
    turns = session.get("conversation")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"Memora session {session_id} has no dialogue")
    rendered = [f"[period {period}] [persona {persona}] [session {session_id:04d}] [date {date}]"]
    clean_turns = []
    for turn in turns:
        speaker = turn.get("speaker")
        message = turn.get("message")
        turn_number = turn.get("turn")
        if speaker not in ALLOWED_SPEAKERS:
            raise ValueError(f"Memora session {session_id} has invalid speaker {speaker!r}")
        if not isinstance(turn_number, int) or not isinstance(message, str) or not message.strip():
            raise ValueError(f"Memora session {session_id} has malformed dialogue")
        clean_turns.append({"turn": turn_number, "speaker": speaker, "message": message})
        rendered.append(f"{speaker}: {message}")
    return {
        "session_id": session_id,
        "date": date,
        "persona": persona,
        "turns": clean_turns,
        "body": "\n".join(rendered) + "\n",
    }


def build_group_plan(
    period: str,
    persona: str,
    sessions: list[dict[str, Any]],
    question_document: dict[str, Any],
) -> dict[str, Any]:
    if period not in PERIODS or question_document.get("persona") != persona:
        raise ValueError("Memora period/persona group mismatch")
    clean_sessions = [format_session(session, period, persona) for session in sessions]
    clean_sessions.sort(key=lambda row: (row["date"], row["session_id"]))
    first_date = date.fromisoformat(clean_sessions[0]["date"])
    last_session_date = date.fromisoformat(clean_sessions[-1]["date"])
    official_date_range = question_document.get("date_range")
    if not isinstance(official_date_range, dict) or official_date_range != {
        "start_date": first_date.isoformat(),
        "end_date": last_session_date.isoformat(),
    }:
        raise ValueError(f"Memora {period}/{persona} date_range does not match its sessions")
    last_date = last_session_date + timedelta(days=1)
    date_range = {
        "from": f"{first_date.isoformat()}T00:00:00Z",
        "to": f"{last_date.isoformat()}T00:00:00Z",
    }
    session_ids = [row["session_id"] for row in clean_sessions]
    if len(session_ids) != len(set(session_ids)):
        raise ValueError(f"duplicate session identity in {period}/{persona}")

    queries = []
    identities = set()
    for task in TASKS:
        values = question_document.get("questions", {}).get(task)
        if not isinstance(values, list):
            raise ValueError(f"Memora {period}/{persona} lacks {task} questions")
        for question in values:
            question_id = question.get("question_id")
            text = question.get("question")
            question_date = question.get("question_date")
            identity = (period, persona, question_id)
            if identity in identities:
                raise ValueError(f"duplicate question identity: {identity}")
            if not isinstance(question_id, str) or not question_id or not isinstance(text, str) or not text.strip() or not isinstance(question_date, str) or not question_date:
                raise ValueError(f"malformed Memora question in {period}/{persona}")
            identities.add(identity)
            queries.append({
                "period": period,
                "persona": persona,
                "question_id": question_id,
                "question": text,
                "question_date": question_date,
                "task_type": task.title(),
            })
    return {
        "scope": {"period": period, "persona": persona},
        "date_range": date_range,
        "sessions": clean_sessions,
        "queries": queries,
    }


def load_official_plans(repo: Path) -> list[dict[str, Any]]:
    plans = []
    for period in PERIODS:
        for persona_dir in sorted(path for path in (repo / "data" / period).iterdir() if path.is_dir()):
            sessions = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((persona_dir / "conversations").glob("session_*.json"))
            ]
            files = list(persona_dir.glob("evaluation_questions_*.json"))
            if len(files) != 1:
                raise ValueError(f"expected one question file under {persona_dir}")
            questions = json.loads(files[0].read_text(encoding="utf-8"))
            plans.append(build_group_plan(period, persona_dir.name, sessions, questions))
    return plans


def select_task(plans: list[dict[str, Any]], task: str | None) -> list[dict[str, Any]]:
    if task is None:
        return plans
    if task not in TASKS:
        raise ValueError(f"unknown Memora task: {task}")
    selected = []
    expected_type = task.title()
    for plan in plans:
        copied = dict(plan)
        copied["queries"] = [
            query for query in plan["queries"] if query["task_type"] == expected_type
        ]
        if copied["queries"]:
            selected.append(copied)
    if not selected:
        raise ValueError(f"Memora task has no questions: {task}")
    return selected


def verify_generation_lock(path: Path) -> dict[str, Any]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "dataset_lock_sha256": sha256_file(MEMORA_LOCK),
        "output_schema_sha256": sha256_json(OUTPUT_SCHEMA),
        "prompt_sha256": hashlib.sha256(f"{SYSTEM_PROMPT}\x1e{PROMPT_TEMPLATE}".encode()).hexdigest(),
        "reader_lattice_sha256": sha256_file(READER_LATTICE),
    }
    for key, value in expected.items():
        if lock.get(key) != value:
            raise ValueError(f"Memora generation lock drifted: {key}")
    lattice = json.loads(READER_LATTICE.read_text(encoding="utf-8"))
    screen = lattice.get("final_user_requested_screen", {})
    expected_reader = {
        "candidates": [
            {
                "requested_model": "openai/gpt-5.6-luna-pro",
                "canonical_model_snapshot": LUNA_CANONICAL_MODEL,
                "reasoning_effort": "high",
                "prior_screen": "normal-development-reader",
            },
            {
                "requested_model": "google/gemini-3.5-flash",
                "reasoning_effort": "high",
                "prior_screen": "rejected-contract-invalid",
            },
        ],
        "selection": "paired-memora-capability-cost-screen",
    }
    selected = next(
        candidate
        for candidate in expected_reader["candidates"]
        if candidate["requested_model"] == REQUESTED_MODEL
    )
    lattice_entry = (
        screen.get("development_reader", {})
        if REQUESTED_MODEL == "openai/gpt-5.6-luna-pro"
        else screen.get("rejected_reader", {})
    )
    if (
        lock.get("reader") != expected_reader
        or lattice_entry.get("requested_reader_model") != REQUESTED_MODEL
        or lattice_entry.get("reasoning_effort") != selected["reasoning_effort"]
        or lattice_entry.get("decision") != selected["prior_screen"]
    ):
        raise ValueError("Memora generation reader selection drifted")
    if lock.get("retrieval") != {
        "aggregation_window": "official_group_date_range",
        "budget_tokens": EVIDENCE_BUDGET_TOKENS,
        "cross_rerank": False,
        "embed_model": EMBED_MODEL,
        "limit": RECALL_LIMIT,
        "mode": RECALL_MODE,
    }:
        raise ValueError("Memora generation retrieval contract drifted")
    if lock.get("temporal_contract") != TEMPORAL_CONTRACT:
        raise ValueError("Memora generation temporal contract drifted")
    return lock


def freeze_behavior_environment() -> dict[str, str]:
    for key in [key for key in os.environ if key.startswith("MEMPHANT_")]:
        del os.environ[key]
    os.environ.update(BEHAVIOR_ENV)
    return dict(BEHAVIOR_ENV)


def validate_reader_cache_contract(
    cache_dir: Path, ledger: ProviderAttemptLedger, *, ledger_existed: bool
) -> None:
    cache_has_files = cache_dir.exists() and any(path.is_file() for path in cache_dir.rglob("*"))
    if cache_has_files and not ledger.attempts:
        raise ValueError(
            "Memora fresh attempt ledger requires an empty reader cache directory"
        )
    if ledger_existed and ledger.attempts:
        validate_provider_attempt_ledger(ledger.snapshot())


def question_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return row["period"], row["persona"], row["question_id"]


def evidence_hash(evidence: list[dict[str, Any]]) -> str:
    return sha256_json(evidence)


def validate_retrieval_record(record: dict[str, Any], row: dict[str, Any]) -> None:
    retrieval = record.get("retrieval")
    if not isinstance(retrieval, dict) or set(retrieval) != {
        "trace", "trace_sha256", "derived_sources", "derived_sources_sha256",
        "direct_sources", "direct_sources_sha256",
    }:
        raise ValueError("Memora retrieval proof is incomplete")
    trace = retrieval["trace"]
    sources = retrieval["derived_sources"]
    direct_sources = retrieval["direct_sources"]
    if (
        not isinstance(trace, dict)
        or trace.get("id") != row["trace"]["trace_id"]
        or retrieval["trace_sha256"] != sha256_json(trace)
        or not isinstance(trace.get("tenant_id"), str)
        or not isinstance(trace.get("scope_id"), str)
        or not isinstance(trace.get("actor_id"), str)
        or not isinstance(trace.get("context_items"), list)
        or not isinstance(trace.get("citations"), list)
        or [item.get("unit_id") for item in trace["context_items"]]
        != [item.get("unit_id") for item in row["evidence"]]
        or not isinstance(sources, list)
        or retrieval["derived_sources_sha256"] != sha256_json(sources)
        or not isinstance(direct_sources, list)
        or retrieval["direct_sources_sha256"] != sha256_json(direct_sources)
    ):
        raise ValueError("Memora retrieval proof hash or pairing mismatch")
    derived_ids = {
        unit_id
        for item in [*trace["context_items"], *trace["citations"]]
        for unit_id in item.get("derived_from_unit_ids", [])
    }
    source_ids = {source.get("unit_id") for source in sources if isinstance(source, dict)}
    if len(source_ids) != len(sources) or source_ids != derived_ids:
        raise ValueError("Memora derived source proof does not pair exactly")
    direct_ids = {
        item.get("unit_id")
        for item in [*trace["context_items"], *trace["citations"]]
        if isinstance(item, dict) and item.get("derived_from_unit_ids", []) == []
    }
    direct_source_ids = {
        source.get("unit_id") for source in direct_sources if isinstance(source, dict)
    }
    if len(direct_source_ids) != len(direct_sources) or direct_source_ids != direct_ids:
        raise ValueError("Memora direct source proof does not pair exactly")
    for source in [*sources, *direct_sources]:
        if (
            source.get("tenant_id") != trace["tenant_id"]
            or not isinstance(source.get("memory_unit_body"), str)
            or not source["memory_unit_body"].strip()
            or not isinstance(source.get("source_episode_id"), str)
            or not isinstance(source.get("source_episode_body"), str)
            or not source["source_episode_body"].strip()
        ):
            raise ValueError("Memora derived source proof is incomplete")


def validate_answer(row: dict[str, Any]) -> None:
    if set(row) != ANSWER_KEYS:
        raise ValueError(f"Memora answer keys drifted: {question_identity(row)}")
    if not isinstance(row["answer"], str) or not row["answer"].strip():
        raise ValueError(f"Memora answer is empty: {question_identity(row)}")
    if not isinstance(row["evidence"], list):
        raise ValueError("Memora evidence must be a list")
    for rank, item in enumerate(row["evidence"], 1):
        if set(item) - {"rank", "body", "unit_id"} or item.get("rank") != rank or not isinstance(item.get("body"), str) or not item["body"].strip():
            raise ValueError("Memora evidence violates the native scorer input shape")
    trace = row["trace"]
    if (
        not isinstance(trace, dict)
        or set(trace) != {"trace_id", "degraded", "evidence_sha256"}
        or not isinstance(trace.get("trace_id"), str)
        or not trace["trace_id"]
        or trace.get("degraded") is not False
        or trace.get("evidence_sha256") != evidence_hash(row["evidence"])
    ):
        raise ValueError("Memora answer trace is incomplete")


def load_checkpoint(
    path: Path, fingerprint: str
) -> tuple[list[dict], list[dict], list[dict], list[dict] | None]:
    if not path.exists():
        return [], [], [], None
    value = json.loads(path.read_text(encoding="utf-8"))
    answers = value.get("answers", {})
    proof = value.get("proof", {})
    if answers.get("summary", {}).get("generation_fingerprint") != fingerprint or proof.get("generation_fingerprint") != fingerprint:
        raise ValueError("Memora resume fingerprint mismatch")
    rows = answers.get("data")
    records = proof.get("records")
    errors = proof.get("errors", [])
    if not isinstance(rows, list) or not isinstance(records, list) or not isinstance(errors, list) or len(rows) != len(records):
        raise ValueError("Memora resume checkpoint is malformed")
    by_identity = {question_identity(row): row for row in rows}
    proof_by_identity = {tuple(record.get("identity", [])): record for record in records}
    if len(by_identity) != len(rows) or set(by_identity) != set(proof_by_identity):
        raise ValueError("Memora resume identities do not pair exactly")
    for identity, row in by_identity.items():
        validate_answer(row)
        record = proof_by_identity[identity]
        if (
            record.get("answer_sha256") != sha256_json(row)
            or record.get("trace_id") != row["trace"]["trace_id"]
            or record.get("evidence_sha256") != row["trace"]["evidence_sha256"]
            or record.get("returned_items") != len(row["evidence"])
            or record.get("reader_metadata_sha256")
            != sha256_json(record.get("reader"))
        ):
            raise ValueError(f"Memora resume answer hash mismatch: {identity}")
        validate_retrieval_record(record, row)
    ledger = proof.get("provider_attempt_ledger")
    if ledger is not None:
        validate_provider_attempt_ledger(ledger)
    checkpoint_attempts = None if ledger is None else ledger.get("attempts")
    if checkpoint_attempts is not None and not isinstance(checkpoint_attempts, list):
        raise ValueError("Memora checkpoint provider-attempt ledger is malformed")
    return rows, records, errors, checkpoint_attempts


def output_objects(
    plans: list[dict], answers: list[dict], records: list[dict], errors: list[dict],
    fingerprint: str, runtime_proof: dict,
    attempt_ledger_snapshot: dict[str, Any] | None = None,
) -> tuple[dict, dict]:
    expected = sum(len(plan["queries"]) for plan in plans)
    answers_complete = len(answers) == expected
    ledger_complete = (
        attempt_ledger_snapshot is None
        or provider_attempt_ledger_is_complete(attempt_ledger_snapshot)
    )
    output = {
        "summary": {
            "benchmark": "Memora/FAMA",
            "complete": answers_complete and ledger_complete,
            "expected_questions": expected,
            "generation_fingerprint": fingerprint,
            "model": MODEL,
            "question_count": len(answers),
        },
        "data": answers,
    }
    provider_attempts = sum(
        int(item.get("provider_attempts", 0))
        for item in [*(record.get("reader", {}) for record in records), *errors]
    )
    reported_cost = sum(
        float(item.get("cost_usd", 0) or 0)
        for item in [*(record.get("reader", {}) for record in records), *errors]
    )
    priced_attempts = sum(
        int((item or {}).get("priced_response_count", 0))
        for item in [
            *(record.get("reader", {}) for record in records),
            *(error.get("reader_metadata") for error in errors),
        ]
    )
    if attempt_ledger_snapshot is not None:
        provider_attempts = attempt_ledger_snapshot["provider_attempts"]
        reported_cost = attempt_ledger_snapshot["reported_cost_usd"]
        priced_attempts = attempt_ledger_snapshot["priced_provider_attempts"]
    proof = {
        "benchmark": "Memora/FAMA",
        "generation_fingerprint": fingerprint,
        "runtime": runtime_proof,
        "records": records,
        "errors": errors,
        "fallback_count": 0,
        "provider_attempt_ledger": attempt_ledger_snapshot,
        "reader": {
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "fresh_calls": sum(bool(record["reader"].get("fresh_call")) for record in records),
            "cache_hits": sum(bool(record["reader"].get("cache_hit")) for record in records),
            "provider_attempts": provider_attempts,
            "reported_cost_usd": reported_cost,
            "unpriced_provider_attempts": provider_attempts - priced_attempts,
            "cost_status": (
                "all_provider_attempts_priced"
                if provider_attempts == priced_attempts
                else "response_reported_cost_only"
            ),
        },
    }
    return output, proof


def write_checkpoint(checkpoint: Path, out: Path, proof_path: Path, output: dict, proof: dict) -> None:
    run_reader.atomic_write_json(proof_path, proof)
    run_reader.atomic_write_json(checkpoint, {"answers": output, "proof": proof})
    run_reader.atomic_write_json(out, output)


def execute_groups(
    plans: list[dict], runtime: Any, reader: Any, out: Path, proof_path: Path,
    checkpoint: Path, *, generation_fingerprint: str, runtime_proof: dict,
    max_provider_attempts: int | None = None,
    attempt_ledger: ProviderAttemptLedger | None = None,
) -> None:
    answers, records, errors, checkpoint_attempts = load_checkpoint(
        checkpoint, generation_fingerprint
    )
    if attempt_ledger is not None and checkpoint_attempts is not None:
        current_attempts = attempt_ledger.snapshot()["attempts"]
        if len(current_attempts) < len(checkpoint_attempts):
            raise ValueError("Memora provider-attempt ledger is truncated")
        if current_attempts[: len(checkpoint_attempts)] != checkpoint_attempts:
            raise ValueError(
                "Memora provider-attempt ledger diverged from checkpoint prefix"
            )
    elif attempt_ledger is not None and checkpoint.exists():
        raise ValueError("Memora checkpoint is missing provider-attempt ledger proof")
    attempts_used = (
        attempt_ledger.snapshot()["provider_attempts"]
        if attempt_ledger is not None
        else sum(
            int(item.get("provider_attempts", 0))
            for item in [*(record.get("reader", {}) for record in records), *errors]
        )
    )
    if max_provider_attempts is not None:
        if attempts_used > max_provider_attempts:
            raise ValueError("Memora checkpoint exceeds provider attempt budget")
        if hasattr(reader, "set_attempt_budget"):
            reader.set_attempt_budget(max_provider_attempts - attempts_used)
    completed = {question_identity(row) for row in answers}
    expected_identities = {
        question_identity(query) for plan in plans for query in plan["queries"]
    }
    if not completed <= expected_identities:
        raise ValueError("Memora resume contains answers outside the selected input")
    for plan in plans:
        pending = [query for query in plan["queries"] if question_identity(query) not in completed]
        if not pending:
            continue
        runtime.open_group(plan)
        if not getattr(runtime, "bank_replay", False):
            for session in plan["sessions"]:
                runtime.retain(session)
            drained = runtime.drain()
            if hasattr(runtime, "extractor_attempt_summary"):
                runtime_proof["structured_extractor"] = runtime.extractor_attempt_summary
            if drained < len(plan["sessions"]):
                raise RuntimeError(f"Memora worker drained {drained} jobs for {len(plan['sessions'])} sessions")
        for query in pending:
            started = time.perf_counter()
            reader_attempts_before = getattr(reader, "provider_attempts", 0)
            retrieval_checkpoint = None
            if hasattr(reader, "last_metadata"):
                reader.last_metadata = None
            try:
                evidence, trace_id, retrieval = runtime.recall(query)
                retrieval_checkpoint = {
                    "identity": list(question_identity(query)),
                    "status": "recall_complete",
                    "trace_id": trace_id,
                    "retrieval": retrieval,
                }
                errors.append(retrieval_checkpoint)
                output, proof = output_objects(
                    plans, answers, records, errors, generation_fingerprint,
                    runtime_proof,
                    attempt_ledger.snapshot() if attempt_ledger else None,
                )
                write_checkpoint(checkpoint, out, proof_path, output, proof)
                answer, reader_meta = reader.answer(query, evidence)
                row = dict(query) | {
                    "answer": answer,
                    "evidence": evidence,
                    "trace": {
                        "trace_id": trace_id,
                        "degraded": False,
                        "evidence_sha256": evidence_hash(evidence),
                    },
                }
                validate_answer(row)
                record = {
                    "identity": list(question_identity(row)),
                    "answer_sha256": sha256_json(row),
                    "trace_id": trace_id,
                    "evidence_sha256": evidence_hash(evidence),
                    "returned_items": len(evidence),
                    "retrieval": retrieval,
                    "elapsed_seconds": time.perf_counter() - started,
                    "reader": reader_meta,
                }
                record["reader_metadata_sha256"] = sha256_json(reader_meta)
                errors.remove(retrieval_checkpoint)
                answers.append(row)
                records.append(record)
                completed.add(question_identity(row))
            except Exception as error:
                provider_attempts = (
                    getattr(reader, "provider_attempts", reader_attempts_before)
                    - reader_attempts_before
                )
                failure = {
                    "identity": list(question_identity(query)),
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "provider_attempts": provider_attempts,
                    "reader_metadata": getattr(reader, "last_metadata", None),
                    "cost_usd": (
                        getattr(reader, "last_metadata", None) or {}
                    ).get("cost_usd"),
                }
                if retrieval_checkpoint is None:
                    errors.append(failure)
                else:
                    retrieval_checkpoint.update(failure)
                output, proof = output_objects(
                    plans, answers, records, errors, generation_fingerprint,
                    runtime_proof,
                    attempt_ledger.snapshot() if attempt_ledger else None,
                )
                write_checkpoint(checkpoint, out, proof_path, output, proof)
                raise
            output, proof = output_objects(
                plans, answers, records, errors, generation_fingerprint,
                runtime_proof,
                attempt_ledger.snapshot() if attempt_ledger else None,
            )
            write_checkpoint(checkpoint, out, proof_path, output, proof)
    output, proof = output_objects(
        plans, answers, records, errors, generation_fingerprint, runtime_proof,
        attempt_ledger.snapshot() if attempt_ledger else None,
    )
    if attempt_ledger is not None:
        validate_provider_attempt_ledger(attempt_ledger.snapshot())
    if completed != expected_identities or not output["summary"]["complete"]:
        raise RuntimeError("Memora generation ended incomplete")
    write_checkpoint(checkpoint, out, proof_path, output, proof)


class PackagedRuntime:
    def __init__(self, args: argparse.Namespace, runtime_proof: dict | None = None) -> None:
        self.args = args
        self.runtime_proof = runtime_proof
        self.extractor_attempt_ledger = args.extractor_attempt_ledger
        self.extractor_attempt_summary = None
        if self.extractor_attempt_ledger.exists():
            self._refresh_extractor_proof()
        self.server = gate_runtime.Server(str(args.server_bin), args.database_url, args.port, EMBED_MODEL, args.proof.parent / "memora-memphant-server.log")
        self.client = None
        self.scope_id = ""
        self.actor_id = ""
        self.context: dict[str, Any] = {}
        self.aggregation_window = None
        self.bank_groups = getattr(args, "bank_groups", None)
        self.bank_replay = self.bank_groups is not None

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        try:
            if self.client is not None:
                self.client.conn.close()
        finally:
            self.server.stop()

    def open_group(self, group: dict) -> None:
        if self.client is not None:
            self.client.conn.close()
        scope = group["scope"]
        label = f"{scope['period']}-{scope['persona']}"
        if self.bank_replay:
            identity = self.bank_groups.get(label)
            if not isinstance(identity, dict) or set(identity) != BANK_GROUP_KEYS:
                raise ValueError(f"Memora bank omitted group: {label}")
            tenant_id = identity["tenant_id"]
            self.context = {key: value for key, value in identity.items() if key != "tenant_id"}
            api_key = gate_runtime.provision_api_key(
                str(self.args.cli_bin), self.args.database_url, tenant_id
            )
        else:
            tenant_id, api_key = gate_runtime.provision_tenant(
                str(self.args.cli_bin), self.args.database_url, f"memora-{label}"
            )
        self.aggregation_window = group["date_range"]
        self.client = gate_runtime.ApiClient(self.args.port, api_key, tenant_id)
        if not self.bank_replay:
            self.context = self.client.put(
                f"/v1/context-bindings/memora-{label}",
                {
                    "subject": {
                        "external_ref": f"memora:subject:{label}",
                        "kind": "user",
                    },
                    "actor": {
                        "external_ref": f"memora:actor:{label}",
                        "kind": "system",
                    },
                    "scope": {
                        "external_ref": f"memora:scope:{label}",
                        "kind": "user_root",
                        "parent_external_ref": None,
                    },
                    "agent_node": {
                        "external_ref": f"memora:agent:{label}",
                        "parent_external_ref": None,
                    },
                    "access_policies": [],
                },
            )
        if set(self.context) != BANK_GROUP_KEYS - {"tenant_id"}:
            raise RuntimeError("Memora context binding response is malformed")
        self.scope_id = self.context["scope_id"]
        self.actor_id = self.context["actor_id"]

    def group_identity(self) -> dict[str, str]:
        if self.client is None:
            raise RuntimeError("Memora group is not open")
        return {"tenant_id": self.client.tenant_id, **self.context}

    def context_payload(self) -> dict[str, Any]:
        return {
            key: self.context[key]
            for key in (
                "subject_id", "scope_id", "actor_id", "agent_node_id",
                "subject_generation",
            )
        }

    def retain(self, session: dict) -> None:
        response = self.client.post(
            "/v1/episodes",
            gate_runtime.episode_retain_payload(
                self.context_payload(),
                source_ref=f"memora:session:{session['session_id']:04d}",
                observed_at=f"{session['date']}T00:00:00Z",
                source_kind="user",
                body=session["body"],
            ),
        )
        if not response.get("episode_id"):
            raise RuntimeError(f"Memora retain returned no episode_id for session {session['session_id']}")

    def drain(self) -> int:
        completed = gate_runtime.drain_worker(
            str(self.args.worker_bin),
            self.args.database_url,
            EMBED_MODEL,
            structured_attempt_ledger=self.extractor_attempt_ledger,
            structured_requested_model=STRUCTURED_STATE_MODEL,
        )
        self._refresh_extractor_proof()
        return completed

    def _refresh_extractor_proof(self) -> None:
        self.extractor_attempt_summary = gate_runtime.structured_extractor_attempt_summary(
            self.extractor_attempt_ledger, STRUCTURED_STATE_MODEL,
            require_episode_coverage=True,
        )
        if self.runtime_proof is not None:
            self.runtime_proof["structured_extractor"] = self.extractor_attempt_summary

    def recall(
        self, query: dict, *, aggregate: bool = True, limit: int = RECALL_LIMIT
    ) -> tuple[list[dict], str, dict[str, Any]]:
        payload = {
            **self.context_payload(),
            "query": query["question"],
            "limit": limit,
            "budget_tokens": EVIDENCE_BUDGET_TOKENS,
            "mode": RECALL_MODE,
        }
        if aggregate:
            payload["aggregation_window"] = self.aggregation_window
        response = self.client.post("/v1/recall", payload)
        if response.get("degraded") is not False:
            raise RuntimeError("Memora recall degraded or omitted degraded=false")
        trace_id = response.get("trace_id")
        items = response.get("items")
        if not isinstance(trace_id, str) or not trace_id or not isinstance(items, list):
            raise RuntimeError("Memora recall response is malformed")
        evidence = []
        for rank, item in enumerate(items, 1):
            if not isinstance(item, dict) or not isinstance(item.get("body"), str) or not item["body"].strip():
                raise RuntimeError("Memora recall item is malformed")
            evidence.append({"rank": rank, "unit_id": item.get("unit_id", ""), "body": item["body"]})
        trace_query = urllib.parse.urlencode(self.context_payload())
        trace = self.client.get(f"/v1/traces/{trace_id}?{trace_query}")
        if not isinstance(trace, dict) or trace.get("id") != trace_id:
            raise RuntimeError(f"Memora trace coverage missing for {trace_id}")
        proof = retrieval_proof(
            trace, evidence,
            tenant_id=self.client.tenant_id,
            scope_id=self.scope_id,
            actor_id=self.actor_id,
            database_url=self.args.database_url,
        )
        return evidence, trace_id, proof


def scratch_database_identity(database_url: str) -> str:
    rows = psql_json(database_url, "select current_database() as name")
    if len(rows) != 1 or not isinstance(rows[0].get("name"), str):
        raise RuntimeError("Memora scratch database identity is malformed")
    name = rows[0]["name"]
    if not name.startswith("memphant_scratch_"):
        raise RuntimeError("Memora retrieval arm is not running in a scratch database")
    return sha256_json({"database": name})


def execute_retrieval_arm(
    plans: list[dict], runtime: Any, arm: str, database_identity: str,
    extraction_bank: dict[str, Any],
) -> dict[str, Any]:
    if arm not in {"baseline", "candidate"}:
        raise ValueError("Memora retrieval arm is unsupported")
    aggregate = arm == "candidate"
    results = []
    for plan in plans:
        runtime.open_group(plan)
        for query in plan["queries"]:
            evidence, trace_id, retrieval = runtime.recall(
                query, aggregate=aggregate
            )
            result = {
                "identity": list(question_identity(query)),
                "evidence": evidence,
                "trace_id": trace_id,
                "retrieval": retrieval,
            }
            if arm == "baseline" and query["question_id"] in ROLLUP_PREFIX_BY_QUESTION:
                comparison, comparison_trace_id, comparison_retrieval = runtime.recall(
                    query, aggregate=False, limit=RECALL_LIMIT - 1,
                )
                result.update({
                    "comparison_evidence": comparison,
                    "comparison_trace_id": comparison_trace_id,
                    "comparison_retrieval": comparison_retrieval,
                })
            results.append(result)
    return {
        "arm": arm,
        "database_identity": database_identity,
        "extraction_bank": extraction_bank,
        "paid_calls": 0,
        "results": results,
    }


def compare_retrieval_arms(
    baseline: dict[str, Any], candidate: dict[str, Any], out: Path,
    proof_path: Path, runtime_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if baseline.get("arm") != "baseline" or candidate.get("arm") != "candidate":
        raise RuntimeError("Memora retrieval screen arm labels are malformed")
    baseline_database = baseline.get("database_identity")
    candidate_database = candidate.get("database_identity")
    if (
        not isinstance(baseline_database, str)
        or not baseline_database
        or not isinstance(candidate_database, str)
        or not candidate_database
        or baseline_database == candidate_database
    ):
        raise RuntimeError("Memora retrieval arms require distinct fresh scratch databases")
    bank = baseline.get("extraction_bank")
    required_bank_keys = {
        "archive_sha256", "extractor_ledger_sha256", "compiler_versions",
        "construction_runtime_sha256", "manifest_sha256",
    }
    if (
        not isinstance(bank, dict)
        or not required_bank_keys <= set(bank)
        or candidate.get("extraction_bank") != bank
    ):
        raise RuntimeError("Memora retrieval arms did not restore the identical frozen bank")
    if baseline.get("paid_calls") != 0 or candidate.get("paid_calls") != 0:
        raise RuntimeError("Memora retrieval screen must make zero paid calls")
    baseline_results = baseline.get("results")
    candidate_results = candidate.get("results")
    if not isinstance(baseline_results, list) or not isinstance(candidate_results, list):
        raise RuntimeError("Memora retrieval screen results are malformed")
    if [row.get("identity") for row in baseline_results] != [
        row.get("identity") for row in candidate_results
    ]:
        raise RuntimeError("Memora retrieval arms do not pair the exact same questions")

    results = []
    for baseline_row, candidate_row in zip(baseline_results, candidate_results):
        identity = baseline_row["identity"]
        question_id = identity[2]
        baseline_evidence = baseline_row["evidence"]
        candidate_evidence = candidate_row["evidence"]
        rollups = [
            item for item in candidate_evidence
            if item["body"].startswith("quantity rollup ")
        ]
        rollup_prefix = ROLLUP_PREFIX_BY_QUESTION.get(question_id)
        number = r"-?\d+(?:\.\d+)?"
        correct_rollups = [
            item for item in rollups
            if rollup_prefix
            and re.fullmatch(
                re.escape(rollup_prefix)
                + rf"total={number}; average={number} "
                rf"\(rounded to 6 decimal places when needed\); count=\d+; "
                rf"min={number}; max={number}",
                item["body"],
            )
        ]
        expected_goal = question_id in GOAL_COMPANION_BODY_BY_QUESTION
        goal_companions = [
            item for item in candidate_evidence
            if expected_goal and is_goal_companion(question_id, item["body"])
        ]
        ordinary = [
            item for item in candidate_evidence
            if item not in rollups and item not in goal_companions
        ]
        expected = rollup_prefix is not None
        comparison_evidence = baseline_row.get("comparison_evidence")
        if expected and not isinstance(comparison_evidence, list):
            raise RuntimeError(
                f"Memora baseline capacity control missing: {question_id}"
            )
        baseline_identity = [
            (item["unit_id"], item["body"])
            for item in (comparison_evidence if expected else baseline_evidence)
            if not is_goal_companion(question_id, item["body"])
        ]
        ordinary_identity = [
            (item["unit_id"], item["body"]) for item in ordinary
        ]
        special_items = [*rollups, *goal_companions]
        expected_ordinary = baseline_identity
        bounded_displacement = (
            len(candidate_evidence) <= RECALL_LIMIT
            and candidate_evidence[: len(special_items)] == special_items
            and ordinary_identity == expected_ordinary
            if expected else candidate_evidence == baseline_evidence
        )
        if (
            len(rollups) != int(expected)
            or len(correct_rollups) != int(expected)
            or len(goal_companions) != int(expected_goal)
            or not bounded_displacement
        ):
            raise RuntimeError(
                f"Memora rollup retrieval screen failed: {question_id}"
            )
        results.append({
            "identity": identity,
            "expected_rollup": expected,
            "rollup_count": len(rollups),
            "goal_companion_count": len(goal_companions),
            "baseline_evidence": baseline_evidence,
            "candidate_evidence": candidate_evidence,
            "baseline_trace_id": baseline_row["trace_id"],
            "candidate_trace_id": candidate_row["trace_id"],
            "baseline_retrieval": baseline_row["retrieval"],
            "candidate_retrieval": candidate_row["retrieval"],
        })
    report = {
        "eligible": True,
        "questions": len(results),
        "expected_rollup_questions": sum(row["expected_rollup"] for row in results),
        "results": results,
    }
    proof = {
        **({"runtime": runtime_proof} if runtime_proof is not None else {}),
        "report_sha256": sha256_json(report),
        "paid_calls": 0,
        "fresh_restore_per_arm": True,
        "scratch_database_identities": {
            "baseline": baseline_database,
            "candidate": candidate_database,
        },
        "extraction_bank": bank,
        "arm_report_sha256": {
            "baseline": sha256_json(baseline),
            "candidate": sha256_json(candidate),
        },
    }
    run_reader.atomic_write_json(out, report)
    run_reader.atomic_write_json(proof_path, proof)
    return report


def orchestrate_retrieval_screen(args: argparse.Namespace) -> None:
    env = dict(os.environ)
    env.pop("MEMPHANT_SCRATCH_ACTIVE", None)
    env.pop("DATABASE_URL", None)
    with tempfile.TemporaryDirectory(prefix="memphant-retrieval-screen-") as temporary:
        temporary_path = Path(temporary)
        arms: dict[str, dict[str, Any]] = {}
        arm_proofs: dict[str, dict[str, Any]] = {}
        for arm in ("baseline", "candidate"):
            arm_out = temporary_path / f"{arm}.json"
            arm_proof = temporary_path / f"{arm}.proof.json"
            command = [
                sys.executable, str(Path(__file__).resolve()), *sys.argv[1:],
                "--retrieval-arm", arm, "--out", str(arm_out),
                "--proof", str(arm_proof),
            ]
            result = subprocess.run(
                command, cwd=ROOT, env=env, text=True, capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Memora {arm} retrieval arm failed: {result.stderr.strip()}"
                )
            arms[arm] = json.loads(arm_out.read_text(encoding="utf-8"))
            arm_proofs[arm] = json.loads(arm_proof.read_text(encoding="utf-8"))
            if (
                arm_proofs[arm].get("paid_calls") != 0
                or arm_proofs[arm].get("report_sha256") != sha256_json(arms[arm])
            ):
                raise RuntimeError(f"Memora {arm} retrieval arm proof is malformed")
        if arm_proofs["baseline"].get("runtime") != arm_proofs["candidate"].get("runtime"):
            raise RuntimeError("Memora retrieval arms used different runtime contracts")
        compare_retrieval_arms(
            arms["baseline"], arms["candidate"], args.out, args.proof,
            arm_proofs["baseline"]["runtime"],
        )


def validate_reader_metadata(value: Any) -> dict[str, Any]:
    required = {
        "response_id",
        "requested_model",
        "served_model",
        "provider",
        "usage",
        "elapsed_seconds",
        "retry_index",
        "parse_status",
        "request_sha256",
        "result_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("OpenRouter reader response omitted provenance metadata")
    expected_served_model = LUNA_CANONICAL_MODEL if MODEL == REQUESTED_MODEL else MODEL
    if value["requested_model"] != MODEL or value["served_model"] != expected_served_model:
        raise RuntimeError(
            f"OpenRouter served model {value['served_model']!r}, "
            f"expected pinned {expected_served_model!r}"
        )
    if not isinstance(value["response_id"], str) or not value["response_id"].strip():
        raise RuntimeError("OpenRouter reader response omitted response ID")
    if not isinstance(value["provider"], str) or not value["provider"].strip():
        raise RuntimeError("OpenRouter reader response omitted provider")
    usage = value["usage"]
    if not fresh_paid_usage(value):
        raise RuntimeError("OpenRouter reader response usage/cost is malformed")
    if (
        isinstance(value["elapsed_seconds"], bool)
        or not isinstance(value["elapsed_seconds"], (int, float))
        or value["elapsed_seconds"] < 0
        or type(value["retry_index"]) is not int
        or value["retry_index"] < 0
        or value["parse_status"] != "provider_response_validated"
        or not isinstance(value["request_sha256"], str)
        or len(value["request_sha256"]) != 64
        or not isinstance(value["result_sha256"], str)
        or len(value["result_sha256"]) != 64
    ):
        raise RuntimeError("OpenRouter reader response timing/retry metadata is malformed")
    return dict(value)


def reader_attempt_proof(
    attempts: list[dict], successful: dict | None, *, cache_hit: bool
) -> dict[str, Any] | None:
    responses = [
        attempt["response"]
        for attempt in attempts
        if "response" in attempt
    ]
    errors = [attempt["error"] for attempt in attempts if "error" in attempt]
    if successful is None and not responses and not errors:
        return None
    costs = [
        response.get("usage", {}).get("cost")
        for response in responses
        if isinstance(response, dict) and isinstance(response.get("usage"), dict)
    ]
    numeric_costs = [
        float(cost)
        for cost in costs
        if not isinstance(cost, bool) and isinstance(cost, (int, float)) and cost >= 0
    ]
    charged_cost = (
        0.0
        if cache_hit
        else sum(numeric_costs) if len(numeric_costs) == len(responses) else None
    )
    return {
        "model": successful.get("model") if successful else None,
        "provider": successful.get("provider") if successful else None,
        "usage": successful.get("usage") if successful else None,
        "attempt_responses": responses,
        "attempt_errors": errors,
        "priced_response_count": len(numeric_costs),
        "cost_usd": charged_cost,
    }


EPISODE_HEADER_RE = re.compile(
    r"\[episode ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]"
)


def render_reader_evidence(evidence: list[dict]) -> str:
    """Render a replay-stable reader pack without runtime-generated UUIDs."""
    episode_labels: dict[str, str] = {}

    def canonicalize(match: re.Match[str]) -> str:
        episode_id = match.group(1)
        label = episode_labels.setdefault(
            episode_id, f"episode-{len(episode_labels) + 1}"
        )
        return f"[episode {label}]"

    rendered = []
    for item in evidence:
        body = EPISODE_HEADER_RE.sub(canonicalize, item["body"].strip())
        rendered.append(f"--- evidence item {item['rank']} ---\n{body}")
    return "\n\n".join(rendered) or "(no evidence was retrieved)"


class OpenRouterReader:
    def __init__(self, cache_dir: Path, max_calls: int) -> None:
        self.model = MODEL
        self.cli = run_reader.ReaderCli("openrouter", MODEL, MODEL, cache_dir, max_calls, REASONING_EFFORT)
        self.last_metadata = None

    @property
    def provider_attempts(self) -> int:
        return self.cli.provider_attempts

    def set_attempt_budget(self, remaining: int) -> None:
        self.cli.set_provider_attempt_limit(self.cli.provider_attempts + remaining)

    def set_attempt_ledger(self, ledger: ProviderAttemptLedger) -> None:
        self.cli.set_provider_attempt_hook(ledger.record)

    def answer(self, query: dict, evidence: list[dict]) -> tuple[str, dict]:
        rendered = render_reader_evidence(evidence)
        prompt = PROMPT_TEMPLATE.format(question_date=query["question_date"], question=query["question"], evidence=rendered)
        fresh = self.cli.fresh_calls
        cached = self.cli.cached_calls
        provider_attempts_before = self.cli.provider_attempts
        attempt_log_before = len(self.cli.provider_attempt_log)
        started = time.perf_counter()
        try:
            reply = self.cli.call("reader", SYSTEM_PROMPT, prompt)
        except Exception:
            self.last_metadata = reader_attempt_proof(
                self.cli.provider_attempt_log[attempt_log_before:],
                None,
                cache_hit=False,
            )
            raise
        cache_hit = self.cli.cached_calls > cached
        self.last_metadata = reader_attempt_proof(
            self.cli.provider_attempt_log[attempt_log_before:],
            self.cli.last_call_metadata,
            cache_hit=cache_hit,
        )
        assert self.last_metadata is not None
        for response in self.last_metadata["attempt_responses"]:
            validate_reader_metadata(response)
        validate_reader_metadata(self.cli.last_call_metadata)
        output = run_reader.parse_reader_output(reply)
        abstained = output["abstain"] or not output["answer"]
        answer = (
            "I cannot answer from the retrieved memory."
            if abstained
            else output["answer"]
        )
        return answer, self.last_metadata | {
            "abstained": abstained,
            "cache_hit": cache_hit,
            "fresh_call": self.cli.fresh_calls > fresh,
            "elapsed_seconds": time.perf_counter() - started,
            "provider_attempts": self.cli.provider_attempts - provider_attempts_before,
        }


def create_extraction_bank(
    plans: list[dict], runtime: PackagedRuntime, args: argparse.Namespace,
    runtime_proof: dict[str, Any], bank_dir: Path,
) -> dict[str, Any]:
    groups: dict[str, dict[str, str]] = {}
    for plan in plans:
        runtime.open_group(plan)
        for session in plan["sessions"]:
            runtime.retain(session)
        drained = runtime.drain()
        if drained < len(plan["sessions"]):
            raise RuntimeError(
                f"Memora worker drained {drained} jobs for {len(plan['sessions'])} sessions"
            )
        scope = plan["scope"]
        groups[f"{scope['period']}-{scope['persona']}"] = runtime.group_identity()
    database_identity = gate_common.database_schema_identity(
        args.database_url,
        "select 'migration:' || version from memphant.schema_migrations",
    )
    logical_identity = database_bank_identity(args.database_url)
    compiler_versions = [
        row["compiler_version"]
        for row in psql_json(
            args.database_url,
            "select distinct compiler_version from memphant.job_state "
            "where state = 'done' order by compiler_version",
        )
    ]
    if not compiler_versions or any(not isinstance(value, str) for value in compiler_versions):
        raise RuntimeError("Memora bank has no completed compiler identity")
    archive, archive_sha256, dump_tool = dump_extraction_bank(
        args.database_url, bank_dir, str(args.pg_dump_bin)
    )
    extractor_sha256 = sha256_file(args.extractor_attempt_ledger)
    extractor_copy = bank_dir / f"{extractor_sha256}.extractor.jsonl"
    shutil.copyfile(args.extractor_attempt_ledger, extractor_copy)
    construction = {
        "kind": "direct_extraction",
        "runtime_sha256": bank_runtime_sha256(runtime_proof),
        "artifacts": [
            {
                "role": "extractor_ledger",
                "file": extractor_copy.name,
                "sha256": extractor_sha256,
            }
        ],
    }
    manifest = {
        "format_version": BANK_FORMAT_VERSION,
        "archive": archive.name,
        "archive_sha256": archive_sha256,
        "database_identity": database_identity,
        "logical_identity": logical_identity,
        "postgres_major": dump_tool["major"],
        "pg_dump_version": dump_tool["version"],
        "extractor_model": STRUCTURED_STATE_MODEL,
        "extractor_ledger": extractor_copy.name,
        "extractor_ledger_sha256": extractor_sha256,
        "extractor_summary": runtime.extractor_attempt_summary,
        "compiler_versions": compiler_versions,
        "groups": groups,
        "extraction_plan_sha256": extraction_plan_sha256(plans),
        "runtime_sha256": construction["runtime_sha256"],
        "construction": construction,
        "construction_sha256": sha256_json(construction),
    }
    validate_bank_construction(manifest, bank_dir)
    run_reader.atomic_write_json(bank_dir / "manifest.json", manifest)
    args.extractor_attempt_ledger.unlink(missing_ok=True)
    return manifest


def binary_fingerprint(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"required packaged binary is missing: {path}")
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def runtime_contract(args: argparse.Namespace, lock: dict, generation: dict) -> dict:
    config = {
        "reader": generation["reader"],
        "retrieval": generation["retrieval"],
        "max_provider_attempts": args.max_provider_attempts,
        "temporal_contract": generation["temporal_contract"],
    }
    return {
        "source": lock["code"],
        "dataset": lock["dataset"],
        "manifest_sha256": sha256_file(MEMORA_LOCK),
        "generation_manifest_sha256": sha256_file(args.generation_lock),
        "reader_lattice_sha256": sha256_file(READER_LATTICE),
        "binaries": {
            name: binary_fingerprint(getattr(args, f"{name}_bin"))
            for name in ("server", "worker", "cli")
        },
        "openapi_sha256": sha256_file(OPENAPI),
        "config_sha256": sha256_json(config),
        "prompt_sha256": generation["prompt_sha256"],
        "model_sha256": sha256_json(generation["reader"]),
        "behavior_environment": args.behavior_environment,
        "harness_sha256": {
            "generator": sha256_file(Path(__file__)),
            "gate_runtime": sha256_file(ROOT / "scripts" / "gate_runtime.py"),
            "reader": sha256_file(ROOT / "scripts" / "run_reader.py"),
        },
    }


def run_fingerprint(
    runtime_proof: dict, selection: str | None, out: Path, attempt_ledger: Path,
    cache_dir: Path,
) -> str:
    return sha256_json(
        {
            "runtime": runtime_proof,
            "selection": selection,
            "out": str(out.resolve()),
            "attempt_ledger": str(attempt_ledger.resolve()),
            "cache_dir": str(cache_dir.resolve()),
        }
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-repo", type=Path, required=True)
    parser.add_argument("--generation-lock", type=Path, default=GENERATION_LOCK)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--attempt-ledger", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--group", help="optional PERIOD/PERSONA pilot selection")
    parser.add_argument(
        "--task", choices=TASKS,
        help="optional task-only diagnostic; all group sessions are still ingested",
    )
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--port", type=int, default=39433)
    parser.add_argument("--server-bin", type=Path, default=ROOT / "target/debug/memphant-server")
    parser.add_argument("--worker-bin", type=Path, default=ROOT / "target/debug/memphant-worker")
    parser.add_argument("--cli-bin", type=Path, default=ROOT / "target/debug/memphant-cli")
    parser.add_argument("--pg-dump-bin", type=Path, default=Path("pg_dump"))
    parser.add_argument("--pg-restore-bin", type=Path, default=Path("pg_restore"))
    parser.add_argument("--max-provider-attempts", type=int, default=2400)
    parser.add_argument("--model", choices=MODEL_CANDIDATES, default=DEFAULT_MODEL)
    parser.add_argument("--extractor-model", choices=MODEL_CANDIDATES)
    bank = parser.add_mutually_exclusive_group()
    bank.add_argument("--create-bank", type=Path)
    bank.add_argument("--bank", type=Path)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument(
        "--retrieval-arm", choices=("baseline", "candidate"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.retrieval_only and not args.bank:
        raise ValueError("--retrieval-only requires --bank")
    if args.retrieval_arm and not args.retrieval_only:
        raise ValueError("--retrieval-arm requires --retrieval-only")
    configure_model(args.model, args.extractor_model)
    generation = verify_generation_lock(args.generation_lock)
    lock = json.loads(MEMORA_LOCK.read_text(encoding="utf-8"))
    run_memora_fama.verify_official_repo(args.official_repo, lock)
    run_memora_fama.verify_dataset(args.official_repo, lock)
    plans = load_official_plans(args.official_repo)
    sessions = sum(len(plan["sessions"]) for plan in plans)
    queries = sum(len(plan["queries"]) for plan in plans)
    if sessions != 27_614 or queries != 600 or len(plans) != 30:
        raise ValueError("Memora sanitized plan counts mismatch")
    if args.group:
        period, separator, persona = args.group.partition("/")
        if not separator:
            raise ValueError("--group must be PERIOD/PERSONA")
        plans = [plan for plan in plans if plan["scope"] == {"period": period, "persona": persona}]
        if len(plans) != 1:
            raise ValueError(f"Memora group not found: {args.group}")
    plans = select_task(plans, args.task)
    if args.dry_run:
        run_reader.atomic_write_json(args.out, {
            "source_status": "dry_run_no_answers",
            "groups": len(plans),
            "queries": sum(len(plan["queries"]) for plan in plans),
            "sessions": sum(len(plan["sessions"]) for plan in plans),
            "plan_sha256": sha256_json(plans),
            "prompt_sha256": generation["prompt_sha256"],
            "model": MODEL,
        })
        return
    if args.retrieval_only and args.retrieval_arm is None:
        args.proof = args.proof or args.out.with_suffix(args.out.suffix + ".proof.json")
        orchestrate_retrieval_screen(args)
        return
    gate_runtime.reexec_through_scratch_db(args.database_url)
    args.database_url = os.environ["DATABASE_URL"]
    args.behavior_environment = freeze_behavior_environment()
    args.proof = args.proof or args.out.with_suffix(args.out.suffix + ".proof.json")
    checkpoint = args.checkpoint or args.out.with_suffix(args.out.suffix + ".checkpoint.json")
    runtime_proof = runtime_contract(args, lock, generation)
    attempt_ledger_path = args.attempt_ledger or args.out.with_suffix(
        args.out.suffix + ".attempts.json"
    )
    args.extractor_attempt_ledger = (
        args.create_bank / ".extractor-attempts.jsonl"
        if args.create_bank
        else attempt_ledger_path.with_suffix(attempt_ledger_path.suffix + ".extractor.jsonl")
    )
    bank_manifest = None
    if args.bank:
        bank_manifest = restore_extraction_bank(
            args.database_url, args.bank, str(args.pg_restore_bin)
        )
        if bank_manifest.get("extractor_model") != STRUCTURED_STATE_MODEL:
            raise ValueError("Memora bank extractor model mismatch")
        if bank_manifest.get("extraction_plan_sha256") != extraction_plan_sha256(plans):
            raise ValueError("Memora bank ingestion plan mismatch")
        args.extractor_attempt_ledger = args.bank / bank_manifest["extractor_ledger"]
        args.bank_groups = bank_manifest.get("groups")
        runtime_proof["extraction_bank"] = {
            "archive_sha256": bank_manifest["archive_sha256"],
            "extractor_ledger_sha256": bank_manifest["extractor_ledger_sha256"],
            "compiler_versions": bank_manifest["compiler_versions"],
            "construction_sha256": bank_manifest["construction_sha256"],
            "construction_runtime_sha256": bank_manifest["runtime_sha256"],
            "manifest_sha256": sha256_file(args.bank / "manifest.json"),
        }
    runtime = PackagedRuntime(args, runtime_proof)
    if args.create_bank:
        try:
            runtime.start()
            create_extraction_bank(plans, runtime, args, runtime_proof, args.create_bank)
        finally:
            runtime.stop()
        return
    if args.retrieval_only:
        try:
            runtime.start()
            report = execute_retrieval_arm(
                plans, runtime, args.retrieval_arm,
                scratch_database_identity(args.database_url),
                runtime_proof["extraction_bank"],
            )
            run_reader.atomic_write_json(args.out, report)
            run_reader.atomic_write_json(args.proof, {
                "runtime": runtime_proof,
                "report_sha256": sha256_json(report),
                "paid_calls": 0,
            })
        finally:
            runtime.stop()
        return
    selection = f"group={args.group or 'all'};task={args.task or 'all'}"
    fingerprint = run_fingerprint(
        runtime_proof, selection, args.out, attempt_ledger_path, args.cache_dir
    )
    ledger_existed = attempt_ledger_path.exists()
    attempt_ledger = ProviderAttemptLedger(attempt_ledger_path, fingerprint)
    validate_reader_cache_contract(
        args.cache_dir, attempt_ledger, ledger_existed=ledger_existed
    )
    reader = OpenRouterReader(args.cache_dir, args.max_provider_attempts)
    reader.set_attempt_ledger(attempt_ledger)
    try:
        runtime.start()
        execute_groups(
            plans,
            runtime,
            reader,
            args.out,
            args.proof,
            checkpoint,
            generation_fingerprint=fingerprint,
            runtime_proof=runtime_proof,
            max_provider_attempts=args.max_provider_attempts,
            attempt_ledger=attempt_ledger,
        )
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
