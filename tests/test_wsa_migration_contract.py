from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "memphant_migrations" / "versions"
BOOTSTRAP = MIGRATIONS / "20260703_001_wsa_bootstrap.sql"
FILE_SYNC_FORWARD = MIGRATIONS / "20260723_002_file_sync_mutation_verb.sql"
WORKER_CLAIM_FORWARD = MIGRATIONS / "20260724_003_worker_claim_throughput.sql"


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

REQUIRED_TABLES = {
    "tenant",
    "subject",
    "subject_tombstone",
    "actor",
    "context_binding",
    "agent_node",
    "scope",
    "scope_policy",
    "episode",
    "resource",
    "memory_unit",
    "memory_edge",
    "embedding_profile",
    "embedding",
    "citation",
    "trust_event",
    "event_outbox",
    "retrieval_trace",
    "deletion_generation",
    "job_state",
    "blob_ledger",
    "belief_observation",
    "review_event",
    "scope_block",
    "mutation_ledger",
    "schema_migrations",
}

TENANT_SCOPED_TABLES = REQUIRED_TABLES - {"schema_migrations"}

# Roots deleted by the single subject cascade. Join rows are covered by their
# owning root foreign keys; mutation_ledger is deliberately receipt-safe.
SUBJECT_OWNED_ROOTS = {
    "actor",
    "scope",
    "agent_node",
    "scope_policy",
    "context_binding",
    "episode",
    "resource",
    "memory_unit",
    "memory_edge",
    "embedding",
    "citation",
    "trust_event",
    "event_outbox",
    "retrieval_trace",
    "deletion_generation",
    "job_state",
    "blob_ledger",
    "belief_observation",
    "review_event",
    "api_key",
    "forgotten_source",
    "scope_block",
}


def sql_text() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


def test_wsa_bootstrap_migration_declares_required_tables() -> None:
    sql = sql_text()

    missing = [
        table
        for table in sorted(REQUIRED_TABLES)
        if f"create table if not exists memphant.{table}" not in sql.lower()
    ]

    assert missing == []


def test_memory_sources_are_non_null_indexed_and_fact_key_is_canonical() -> None:
    sql = sql_text().lower()
    for table in ("episode", "resource", "memory_unit"):
        block = _table_block(sql, table)
        assert "source_ref text not null" in block
        assert (
            f"memphant_{table}_tenant_source_ref_idx on memphant.{table} "
            "(tenant_id, data_subject_id, subject_generation, source_ref)"
        ) in sql
    assert "observed_at timestamptz not null" in _table_block(sql, "resource")
    assert "observed_at timestamptz not null" in _table_block(sql, "memory_unit")
    assert "fact_key text" in _table_block(sql, "memory_unit")
    assert "predicate text" in _table_block(sql, "memory_unit")
    assert "subject_key" not in sql


def test_mutation_ledger_is_receipt_safe_and_exactly_24_hour_scoped() -> None:
    block = _load_script("check_memphant_migration_contract.py").table_block(
        sql_text().lower(), "mutation_ledger"
    )

    assert "primary key (tenant_id, verb, idempotency_key)" in block
    assert "request_hash bytea" in block
    assert "response_body bytea" in block
    assert "interval '24 hours'" in block
    assert "statement_timestamp()" in block
    assert "transaction_timestamp()" not in block
    assert "references memphant.subject" not in block
    assert "foreign key (tenant_id, data_subject_id)" not in block


def test_file_sync_verb_is_forward_migrated_without_rewriting_bootstrap() -> None:
    bootstrap_block = _load_script("check_memphant_migration_contract.py").table_block(
        sql_text().lower(), "mutation_ledger"
    )

    assert "'file_sync'" not in bootstrap_block
    assert FILE_SYNC_FORWARD.exists()
    forward = FILE_SYNC_FORWARD.read_text(encoding="utf-8").lower()
    assert "drop constraint mutation_ledger_verb_check" in forward
    assert "add constraint mutation_ledger_verb_check" in forward
    assert "'file_sync'" in forward
    assert "grant select on memphant.schema_migrations" in forward


def test_subject_owned_roots_are_deleted_by_one_composite_subject_cascade() -> None:
    sql = sql_text().lower()
    expected_fk = (
        "foreign key (tenant_id, data_subject_id) "
        "references memphant.subject (tenant_id, id) on delete cascade"
    )
    compact_expected_fk = expected_fk.replace("subject (", "subject(")
    missing = []

    for table in sorted(SUBJECT_OWNED_ROOTS):
        block = " ".join(_table_block(sql, table).split())
        if expected_fk not in block and compact_expected_fk not in block:
            missing.append(table)

    assert missing == []


def test_subject_tombstone_is_non_pii_minimal_control_state() -> None:
    sql = sql_text().lower()
    marker = "create table if not exists memphant.subject_tombstone ("
    assert marker in sql
    block = _table_block(sql, "subject_tombstone")
    columns = set(
        re.findall(
            r"^\s+([a-z][a-z0-9_]*)\s+(?:uuid|bigint|timestamptz)\b",
            block,
            flags=re.MULTILINE,
        )
    )

    assert columns == {"tenant_id", "erased_subject_id", "generation", "erased_at"}
    for forbidden in (
        "external_ref",
        "client_ref",
        "namespace",
        "source_ref",
        "request_hash",
        "response_body",
    ):
        assert forbidden not in block


def test_subject_tombstone_is_force_rls_tenant_isolated() -> None:
    sql = sql_text().lower()

    assert "alter table memphant.subject_tombstone enable row level security" in sql
    assert "alter table memphant.subject_tombstone force row level security" in sql
    assert "create policy memphant_subject_tombstone_tenant_isolation" in sql


def test_bootstrap_does_not_reuse_index_names_for_different_definitions() -> None:
    names = re.findall(
        r"create\s+index\s+if\s+not\s+exists\s+([a-z0-9_]+)",
        sql_text().lower(),
    )

    assert len(names) == len(set(names))


def test_scope_policy_is_exact_agent_qualified_and_mode_independent_unique() -> None:
    sql = " ".join(sql_text().lower().split())
    assert (
        "unique (tenant_id, data_subject_id, source_scope_id, source_agent_node_id, "
        "grantee_scope_id, grantee_agent_node_id, kind)"
    ) in sql
    assert "grantee_scope_id, grantee_agent_node_id, kind, mode" not in sql
    assert (
        "foreign key (tenant_id, data_subject_id, source_scope_id, source_agent_node_id) "
        "references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)"
    ) in sql


def test_migration_boundary_script_rejects_public_syndai_and_drop_table() -> None:
    result = subprocess.run(
        ["python3", "scripts/check_memphant_migration_boundary.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "migration_boundary=clean" in result.stdout


def test_migration_contract_script_accepts_wsa_bootstrap() -> None:
    result = subprocess.run(
        ["python3", "scripts/check_memphant_migration_contract.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "migration_contract=clean" in result.stdout


def test_migration_class_script_accepts_additive_bootstrap() -> None:
    result = subprocess.run(
        ["python3", "scripts/check_memphant_migration_class.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "migration_class=clean" in result.stdout


def test_apply_runner_dry_run_reports_ordered_migrations() -> None:
    result = subprocess.run(
        [
            "python3",
            "scripts/apply_memphant_migrations.py",
            "--database-url",
            "postgres://memphant.invalid/memphant",
            "--dry-run",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "migration_plan=3" in result.stdout
    assert result.stdout.index("20260703_001_wsa_bootstrap.sql") < result.stdout.index(
        "20260723_002_file_sync_mutation_verb.sql"
    )
    assert result.stdout.index("20260723_002_file_sync_mutation_verb.sql") < result.stdout.index(
        "20260724_003_worker_claim_throughput.sql"
    )


def test_apply_runner_executes_migration_and_ledger_in_one_transaction(
    tmp_path: Path,
) -> None:
    fake_psql = tmp_path / "psql"
    log = tmp_path / "psql.jsonl"
    fake_psql.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['PSQL_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "PSQL_LOG": str(log),
    }

    result = subprocess.run(
        [
            "python3",
            "scripts/apply_memphant_migrations.py",
            "--database-url",
            "postgres://memphant.example/memphant",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    mutation_calls = [call for call in calls if "--file" in call]
    assert len(mutation_calls) == 3
    for call in mutation_calls:
        assert "ON_ERROR_STOP=1" in call
        assert "--single-transaction" in call
        assert "--file" in call
        assert "--command" in call
        assert "insert into memphant.schema_migrations" in call[call.index("--command") + 1]
    assert len(calls) == 4, "ledger must not execute in a second psql process"


def test_apply_runner_failure_keeps_migration_and_ledger_in_same_failed_transaction(
    tmp_path: Path,
) -> None:
    fake_psql = tmp_path / "psql"
    log = tmp_path / "psql.jsonl"
    fake_psql.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['PSQL_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(37 if '--file' in sys.argv else 0)\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "PSQL_LOG": str(log),
    }

    result = subprocess.run(
        [
            "python3",
            "scripts/apply_memphant_migrations.py",
            "--database-url",
            "postgres://memphant.example/memphant",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 37
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    failed = [call for call in calls if "--file" in call]
    assert len(failed) == 1
    assert "--single-transaction" in failed[0]
    assert "--command" in failed[0]
    assert len(calls) == 2, "failure must not be followed by a ledger write"


def test_apply_runner_rejects_transaction_pooler_url() -> None:
    result = subprocess.run(
        [
            "python3",
            "scripts/apply_memphant_migrations.py",
            "--database-url",
            "postgresql://postgres.example:secret@aws-0.pooler.supabase.com:6543/postgres",
            "--dry-run",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "transaction pooler port 6543" in result.stderr


def test_exact_vector_mode_accepts_compatible_08_capabilities() -> None:
    catalog = _load_script("check_memphant_live_catalog.py")

    assert catalog.vector_findings(
        mode="exact",
        version="0.8.0",
        capabilities={"vector", "halfvec", "vector_cosine", "halfvec_cosine"},
        index_names=set(),
    ) == []


def test_hnsw_vector_mode_requires_safe_version_and_expected_index() -> None:
    catalog = _load_script("check_memphant_live_catalog.py")
    capabilities = {"vector", "halfvec", "vector_cosine", "halfvec_cosine"}

    assert catalog.vector_findings(
        mode="hnsw",
        version="0.8.3",
        capabilities=capabilities,
        index_names=set(),
    ) == [
        "vector:hnsw_below_floor:0.8.3",
        "embedding:missing_hnsw_index:memphant_embedding_hnsw_idx",
    ]
    assert catalog.vector_findings(
        mode="hnsw",
        version="0.8.4",
        capabilities=capabilities,
        index_names={"memphant_embedding_hnsw_idx"},
    ) == []


def test_live_catalog_check_requires_database_url() -> None:
    result = subprocess.run(
        ["python3", "scripts/check_memphant_live_catalog.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--database-url" in result.stderr


def test_live_catalog_manifest_includes_subject_bound_context_bindings() -> None:
    catalog = _load_script("check_memphant_live_catalog.py")

    assert "context_binding" in catalog.REQUIRED_TABLES
    assert "context_binding" in catalog.TENANT_RLS_TABLES


@pytest.mark.skipif(
    not os.environ.get("MEMPHANT_TEST_DATABASE_URL"),
    reason="requires scratch Postgres",
)
def test_failed_migration_rolls_back_object_and_ledger(tmp_path: Path) -> None:
    database_url = os.environ["MEMPHANT_TEST_DATABASE_URL"]
    runner = _load_script("apply_memphant_migrations.py")
    migration = tmp_path / "20990101_999_failure_probe.sql"
    migration.write_text(
        "create table memphant.migration_failure_probe (id integer);\n"
        "select 1 / 0;\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.CalledProcessError):
        runner.apply_migration(database_url, migration)

    readback = subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--dbname",
            database_url,
            "--command",
            "select to_regclass('memphant.migration_failure_probe') is null, count(*) "
            "from memphant.schema_migrations "
            "where version = '20990101_999_failure_probe'",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert readback.stdout.strip() == "t|0"


@pytest.mark.skipif(
    not os.environ.get("MEMPHANT_TEST_DATABASE_URL"),
    reason="requires scratch Postgres",
)
def test_live_forward_migration_upgrades_applied_bootstrap_atomically() -> None:
    database_url = os.environ["MEMPHANT_TEST_DATABASE_URL"]
    runner = _load_script("apply_memphant_migrations.py")

    database_name = runner.psql(
        database_url,
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--command",
        "select current_database()",
    )
    assert database_name.returncode == 0, database_name.stderr
    assert database_name.stdout.strip().startswith("memphant_scratch_")

    reset = runner.psql(database_url, "--command", "drop schema memphant cascade")
    assert reset.returncode == 0, reset.stderr
    runner.apply_migration(database_url, BOOTSTRAP)

    insert_file_sync = (
        "insert into memphant.tenant (id, slug, plan, region) "
        "values ('00000000-0000-0000-0000-000000000001', 'upgrade-test', 'test', 'local'); "
        "insert into memphant.mutation_ledger "
        "(tenant_id, verb, idempotency_key, data_subject_id, subject_generation, "
        "request_hash, state) values "
        "('00000000-0000-0000-0000-000000000001', 'file_sync', 'upgrade-test', "
        "'00000000-0000-0000-0000-000000000002', 0, decode(repeat('00', 32), 'hex'), "
        "'pending')"
    )
    rejected = runner.psql(database_url, "--command", insert_file_sync)
    assert rejected.returncode != 0
    assert "mutation_ledger_verb_check" in rejected.stderr

    upgrade = subprocess.run(
        [
            "python3",
            "scripts/apply_memphant_migrations.py",
            "--database-url",
            database_url,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    assert "migration_apply=complete applied=2 skipped=1" in upgrade.stdout

    accepted = runner.psql(database_url, "--command", insert_file_sync)
    assert accepted.returncode == 0, accepted.stderr
    readback = runner.psql(
        database_url,
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--command",
        "select count(*) from memphant.schema_migrations "
        "where version in ('20260703_001_wsa_bootstrap', "
        "'20260723_002_file_sync_mutation_verb', "
        "'20260724_003_worker_claim_throughput')",
    )
    assert readback.returncode == 0, readback.stderr
    assert readback.stdout.strip() == "3"


def test_wsa_bootstrap_has_schema_migration_compat_floor() -> None:
    sql = sql_text().lower()

    assert "memphant.schema_migrations" in sql
    assert "schema_compat_revision" in sql
    assert "version text primary key" in sql


def test_tenant_scoped_tables_have_rls_and_tenant_indexes() -> None:
    sql = sql_text().lower()

    missing_tenant = [
        table
        for table in sorted(TENANT_SCOPED_TABLES)
        if table != "tenant" and f"create table if not exists memphant.{table}" in sql
        and "tenant_id" not in _table_block(sql, table)
    ]
    missing_rls = [
        table
        for table in sorted(TENANT_SCOPED_TABLES)
        if f"alter table memphant.{table} enable row level security" not in sql
    ]
    missing_index = [
        table
        for table in sorted(TENANT_SCOPED_TABLES)
        if table != "tenant" and f"create index if not exists memphant_{table}_tenant" not in sql
    ]

    assert missing_tenant == []
    assert missing_rls == []
    assert missing_index == []


def test_wsa_bootstrap_locks_down_browser_roles() -> None:
    sql = sql_text().lower()

    forbidden_grants = [
        "grant select on",
        "grant insert on",
        "grant update on",
        "grant delete on",
        "grant all on",
    ]
    browser_roles = ["anon", "authenticated", "authenticator"]

    for grant in forbidden_grants:
        for role in browser_roles:
            assert f"{grant} memphant." not in sql or f" to {role}" not in sql

    for role in browser_roles:
        assert f"revoke all on schema memphant from {role}" in sql


def test_bootstrap_has_scope_bound_bitemporal_subject_exclusion() -> None:
    sql = sql_text().lower()

    assert "memphant_memory_unit_tenant_open_subject_idx" not in sql
    start = sql.index("add constraint memphant_memory_unit_subject_valid_excl")
    stanza = sql[start : sql.index(";", start)]
    assert "scope_id" in stanza
    assert "kind in ('semantic', 'belief')" in stanza
    assert "transaction_to is null" in stanza
    assert "tstzrange(valid_from, valid_to, '[)') with &&" in stanza
    assert "memphant_memory_unit_history_idx" in sql


def test_bootstrap_adds_api_key_and_forgotten_source_with_rls() -> None:
    sql = sql_text().lower()

    for table in ("api_key", "forgotten_source"):
        assert f"create table if not exists memphant.{table}" in sql
        assert f"alter table memphant.{table} enable row level security" in sql
        assert f"create policy memphant_{table}_tenant_isolation" in sql

    api_key_block = _table_block(sql, "api_key")
    assert "key_hash text not null unique" in api_key_block
    assert "max_trust" in api_key_block
    assert "revoked_at" in api_key_block

    forgotten_block = _table_block(sql, "forgotten_source")
    assert "source_kind text not null check (source_kind in ('episode','resource','memory_unit'))" in forgotten_block
    assert (
        "primary key (tenant_id, data_subject_id, subject_generation, source_kind, source_id)"
        in forgotten_block
    )


def test_auxiliary_data_plane_tables_are_subject_context_owned() -> None:
    sql = sql_text().lower()
    owned = {
        "memory_edge",
        "embedding",
        "citation",
        "trust_event",
        "event_outbox",
        "deletion_generation",
        "blob_ledger",
        "belief_observation",
        "forgotten_source",
        "scope_block",
    }

    for table in owned:
        block = " ".join(_table_block(sql, table).split())
        for column in (
            "data_subject_id uuid not null",
            "scope_id uuid not null",
            "agent_node_id uuid not null",
            "subject_generation bigint not null",
        ):
            assert column in block, f"{table} lacks {column}"
        assert (
            "foreign key (tenant_id, data_subject_id) references memphant.subject"
            in block
        ), table
        assert "on delete cascade" in block, table

    profile = _table_block(sql, "embedding_profile")
    assert "data_subject_id" not in profile


def test_auxiliary_unit_references_are_full_context_cascades() -> None:
    sql = " ".join(sql_text().lower().split())
    unit_fk = (
        "references memphant.memory_unit (tenant_id, data_subject_id, scope_id, "
        "agent_node_id, subject_generation, id) on delete cascade"
    )
    for table in ("memory_edge", "embedding", "citation", "belief_observation"):
        assert unit_fk in " ".join(_table_block(sql_text().lower(), table).split()), table

    for columns in (
        "(tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, src_id, kind)",
        "(tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, dst_id, kind)",
        "(tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, embedding_profile_id, memory_unit_id)",
        "(tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, memory_unit_id)",
        "(tenant_id, data_subject_id, subject_generation, scope_id, agent_node_id, source_kind, forgotten_at)",
    ):
        assert columns in sql


def test_api_keys_are_either_tenant_wide_or_fully_subject_scoped() -> None:
    block = " ".join(_table_block(sql_text().lower(), "api_key").split())
    for column in (
        "data_subject_id uuid",
        "subject_generation bigint",
        "actor_id uuid",
        "scope_id uuid",
        "agent_node_id uuid",
    ):
        assert column in block
    assert (
        "data_subject_id is null and subject_generation is null and actor_id is null "
        "and scope_id is null and agent_node_id is null"
    ) in block
    assert (
        "foreign key (tenant_id, data_subject_id, scope_id, agent_node_id) "
        "references memphant.agent_node(tenant_id, data_subject_id, scope_id, id) on delete cascade"
    ) in block


def test_bootstrap_has_final_review_event_shape() -> None:
    sql = sql_text().lower()

    assert "drop table memphant.review_event" not in sql
    review_block = _table_block(sql, "review_event")
    assert "trace_id uuid not null" in review_block
    assert "caller_id text not null" in review_block
    for column in (
        "data_subject_id uuid not null",
        "subject_generation bigint not null",
        "scope_id uuid not null",
        "actor_id uuid not null",
        "agent_node_id uuid not null",
    ):
        assert column in review_block
    assert "unique (tenant_id, trace_id, caller_id)" in review_block
    assert "foreign key (tenant_id, data_subject_id, subject_generation, scope_id," in review_block
    assert "references memphant.retrieval_trace(" in review_block

    review_pk = _table_block(sql, "review_event")
    assert "primary key (tenant_id, id)" in review_pk

    join_block = _table_block(sql, "review_event_unit")
    assert "review_event_id uuid not null" in join_block
    assert "actor_id uuid not null" in join_block
    assert "foreign key (tenant_id, data_subject_id, subject_generation, scope_id," in join_block
    assert "references memphant.review_event(" in join_block
    assert "primary key (tenant_id, review_event_id, memory_unit_id)" in join_block
    assert "data_subject_id uuid not null" in join_block
    assert "subject_generation bigint not null" in join_block
    assert "references memphant.memory_unit(tenant_id, data_subject_id, scope_id, agent_node_id," in join_block
    assert "alter table memphant.review_event_unit enable row level security" in sql


def test_bootstrap_has_final_job_state_shape() -> None:
    sql = sql_text().lower()

    assert "create table if not exists memphant.reflect_job" not in sql
    assert "claimed_at timestamptz" in _table_block(sql, "job_state")
    assert "'dead'" in sql
    job_block = _table_block(sql, "job_state")
    assert "queue_order bigint generated always as identity" in job_block
    assert "actor_id uuid not null" in job_block
    assert "foreign key (tenant_id, data_subject_id, actor_id)" in job_block
    assert "foreign key (tenant_id, data_subject_id, scope_id, agent_node_id)" in job_block
    assert "references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)" in job_block
    assert "(tenant_id, data_subject_id, scope_id, agent_node_id, state, run_after)" in sql
    claim = sql.split("create or replace function memphant.claim_reflect_jobs", 1)[1]
    assert "subject.generation = job.subject_generation" in claim
    assert "not exists (" in claim
    assert "select 1 from memphant.job_state earlier" in claim
    assert "order by job.queue_order" in claim
    assert "(earlier.created_at, earlier.id)" not in claim
    assert "for update of job skip locked" in claim


def test_worker_claim_optimization_is_owned_by_forward_migration() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8").lower()
    forward = WORKER_CLAIM_FORWARD.read_text(encoding="utf-8").lower()

    assert "blocking_predecessors as materialized" not in bootstrap
    assert "select 1 from memphant.job_state earlier" in bootstrap
    assert "blocking_predecessors as materialized" in forward
    assert "min(blocker.queue_order) as first_queue_order" in forward
    assert "job.queue_order < blocker.first_queue_order" in forward
    assert "not exists (\n        select 1 from memphant.job_state earlier" not in forward


def test_resource_and_source_lineage_are_subject_agent_generation_owned() -> None:
    sql = " ".join(sql_text().lower().split())
    resource = " ".join(_table_block(sql_text().lower(), "resource").split())
    for column in (
        "data_subject_id uuid not null",
        "agent_node_id uuid not null",
        "subject_generation bigint not null",
    ):
        assert column in resource
    assert (
        "foreign key (tenant_id, data_subject_id, scope_id, agent_node_id) "
        "references memphant.agent_node (tenant_id, data_subject_id, scope_id, id)"
    ) in resource
    assert (
        "foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, "
        "subject_generation, source_episode_id) references memphant.episode "
        "(tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, id)"
    ) in sql


def test_episode_dedup_and_worker_claim_lanes_are_exact_context_bound() -> None:
    sql = " ".join(sql_text().lower().split())
    episode = " ".join(_table_block(sql_text().lower(), "episode").split())
    assert (
        "unique (tenant_id, data_subject_id, subject_generation, scope_id, "
        "agent_node_id, actor_id, dedup_key)"
    ) in episode
    claim = " ".join(WORKER_CLAIM_FORWARD.read_text().lower().split())
    for predicate in (
        # Lane ownership is serialized by a BLOCKING per-lane advisory lock taken
        # in a plpgsql loop BEFORE the claim query, carried in via the
        # reflect_lane_key[] array. A skip-locked/try-lock in-query gate is NOT
        # sufficient (snapshot-vs-lock ordering leaves a ~0.3% lane split) — these
        # assertions lock the correct structure in against a regression to it.
        "pg_advisory_xact_lock",
        "locked_lane_keys",
        "unnest(locked_lane_keys)",
        "lane.data_subject_id = job.data_subject_id",
        "lane.subject_generation = job.subject_generation",
        "lane.scope_id = job.scope_id",
        "lane.agent_node_id = job.agent_node_id",
        "blocker.data_subject_id = job.data_subject_id",
        "blocker.subject_generation = job.subject_generation",
        "blocker.agent_node_id = job.agent_node_id",
    ):
        assert predicate in claim

    store = (ROOT / "crates/memphant-store-postgres/src/store.rs").read_text().lower()
    enqueue = store.split("async fn enqueue_reflect", 1)[1].split(
        "async fn fetch_recall_candidates", 1
    )[0]
    assert "on conflict" in enqueue and "do nothing" in enqueue
    assert "set state" not in enqueue
    assert "select id from memphant.job_state" in enqueue
    assert (
        "foreign key (tenant_id, data_subject_id, scope_id, agent_node_id, "
        "subject_generation, source_resource_id) references memphant.resource "
        "(tenant_id, data_subject_id, scope_id, agent_node_id, subject_generation, id)"
    ) in sql


def test_subject_and_agent_lead_memory_hot_path_indexes() -> None:
    sql = sql_text().lower()

    for columns in (
        "(tenant_id, data_subject_id, scope_id, agent_node_id, source_kind, last_observed_at)",
        "(tenant_id, data_subject_id, actor_id, last_observed_at)",
        "(tenant_id, data_subject_id, scope_id, agent_node_id, kind, valid_to)",
        "(tenant_id, data_subject_id, scope_id, agent_node_id, fact_key)",
        "(tenant_id, data_subject_id, scope_id, agent_node_id, transaction_from)",
    ):
        assert columns in sql

    exclusion = sql.split("memphant_memory_unit_subject_valid_excl", 1)[1].split("create index", 1)[0]
    assert "data_subject_id with =" in exclusion
    assert "agent_node_id with =" in exclusion


def test_bootstrap_declares_capability_roles_force_rls_and_default_deny() -> None:
    sql = sql_text().lower()

    for role in (
        "memphant_owner",
        "memphant_app",
        "memphant_worker",
        "memphant_authn",
        "memphant_readonly",
        "memphant_provisioner",
    ):
        assert f"create role {role} nologin" in sql

    for table in TENANT_SCOPED_TABLES | {"api_key", "forgotten_source", "review_event_unit"}:
        assert f"alter table memphant.{table} force row level security" in sql

    assert "alter default privileges for role memphant_owner in schema memphant revoke all on tables from public" in sql
    assert "alter default privileges for role memphant_owner in schema memphant revoke all on functions from public" in sql


def test_bootstrap_exposes_only_narrow_security_definer_capabilities() -> None:
    sql = sql_text().lower()

    for function in (
        "authenticate_api_key",
        "claim_reflect_jobs",
        "dead_letter_count",
        "provision_tenant",
        "provision_api_key",
        "revoke_api_key",
    ):
        start = sql.index(f"function memphant.{function}")
        stanza = sql[start : sql.index("$$;", start)]
        assert "security definer" in stanza
        assert "set search_path = memphant, pg_catalog" in stanza

    assert "grant execute on function memphant.authenticate_api_key(text) to memphant_authn" in sql
    assert "grant execute on function memphant.claim_reflect_jobs(integer, uuid, uuid, integer) to memphant_worker" in sql
    assert "claim_reflect_jobs(integer, uuid, uuid, integer) to memphant_app" not in sql
    assert "to memphant_provisioner" in sql


def test_boundary_checker_allows_drops_only_under_rewrite_header(tmp_path: Path) -> None:
    def run_boundary(sql: str) -> subprocess.CompletedProcess[str]:
        migrations = tmp_path / "versions"
        migrations.mkdir(exist_ok=True)
        for stale in migrations.glob("*.sql"):
            stale.unlink()
        (migrations / "20990101_900_case.sql").write_text(sql, encoding="utf-8")
        return subprocess.run(
            [
                "python3",
                "scripts/check_memphant_migration_boundary.py",
                "--migrations-dir",
                str(migrations),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    denied = run_boundary("drop table memphant.review_event;\n")
    assert denied.returncode == 1
    assert "drop_table_without_rewrite_header" in denied.stdout

    denied_index = run_boundary("drop index memphant.some_idx;\n")
    assert denied_index.returncode == 1
    assert "drop_index_without_rewrite_header" in denied_index.stdout

    allowed = run_boundary(
        "-- migration_kind: rewrite\n"
        "drop table memphant.review_event;\n"
        "drop index memphant.some_idx;\n"
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "migration_boundary=clean" in allowed.stdout

    still_denied = run_boundary(
        "-- migration_kind: rewrite\n"
        "create table public.leak (id int);\n"
    )
    assert still_denied.returncode == 1
    assert "public_schema_reference" in still_denied.stdout


def _table_block(sql: str, table: str) -> str:
    marker = f"create table if not exists memphant.{table} ("
    start = sql.index(marker)
    next_marker = sql.find("create table if not exists", start + len(marker))
    if next_marker == -1:
        return sql[start:]
    return sql[start:next_marker]
