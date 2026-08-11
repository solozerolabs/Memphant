from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dockerfile_builds_all_rust_entrypoints_and_runs_non_root() -> None:
    dockerfile = read("Dockerfile")

    assert "rust:1.96.1-trixie" in dockerfile
    assert dockerfile.count("FROM debian:trixie-slim") == 2
    assert "-p memphant-server" in dockerfile
    assert "-p memphant-worker" in dockerfile
    assert "-p memphant-cli" in dockerfile
    assert "USER memphant" in dockerfile
    assert "MEMPHANT_BIND=0.0.0.0:3000" in dockerfile
    assert "/v1/health" in dockerfile


def test_compose_uses_pgvector_and_waits_for_postgres_health() -> None:
    compose = read("compose.yaml")

    assert "pgvector/pgvector:0.8.4-pg17" in compose
    assert "pg_isready -U memphant -d memphant" in compose
    assert "condition: service_healthy" in compose
    assert "127.0.0.1:${MEMPHANT_POSTGRES_PORT:-5432}:5432" in compose
    assert "127.0.0.1:${MEMPHANT_HTTP_PORT:-3000}:3000" in compose


def test_provider_profiles_pin_byoc_residency_and_retention_floor() -> None:
    for provider in ["plain-postgres", "supabase", "neon"]:
        profile = read(f"deploy/provider-profiles/{provider}.env.example")
        assert f"MEMPHANT_PROVIDER={provider}" in profile
        assert "MEMPHANT_SCHEMA=memphant" in profile
        assert "MEMPHANT_OBJECT_VERSIONING_REQUIRED=true" in profile
        pitr = int(re.search(r"MEMPHANT_PITR_WINDOW_DAYS=(\d+)", profile).group(1))
        retention = int(
            re.search(r"MEMPHANT_OBJECT_RETENTION_DAYS=(\d+)", profile).group(1)
        )
        assert retention >= pitr + 1


def test_supabase_profile_and_runbook_do_not_expose_memphant_schema() -> None:
    profile = read("deploy/provider-profiles/supabase.env.example")
    runbook = read("docs/deployment/byoc-supabase.md")

    assert "MEMPHANT_SUPABASE_EXPOSED_SCHEMAS=public" in profile
    assert "MEMPHANT_SUPABASE_ANON_HAS_MEMPHANT_ACCESS=false" in profile
    assert "MEMPHANT_SUPABASE_AUTHENTICATED_HAS_MEMPHANT_ACCESS=false" in profile
    assert "--schema memphant" in profile
    assert "--fail-on warning" in profile
    assert "never installs into `public`" in runbook
    assert "grants browser roles table access" in runbook


def test_hosted_control_plane_contract_keeps_memory_out_of_global_state() -> None:
    contract = json.loads(read("deploy/hosted/control-plane-hooks.json"))
    forbidden = set(contract["global_state_boundary"]["forbidden"])

    assert {"memory_body", "raw_episode", "resource_uri", "embedding_vector"} <= forbidden
    hook_names = {hook["name"] for hook in contract["hooks"]}
    assert {
        "tenant.provision.requested",
        "tenant.region.resolve",
        "billing.metered_event.recorded",
        "tenant.status.changed",
    } <= hook_names
    assert "same memphant-server" in contract["open_core_contract"]


def test_restore_runbook_blocks_unexpected_missing_blobs() -> None:
    runbook = read("docs/deployment/backup-restore-pitr.md")

    assert "Postgres PITR is authoritative" in runbook
    assert "Keep blob GC disabled" in runbook
    assert "restore_blob_missing{hash}" in runbook
    assert "block release" in runbook
    assert "at least the Postgres PITR window plus margin" in runbook
