from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "memphant_migrations" / "versions"

REQUIRED_TABLES = {
    "tenant",
    "subject",
    "subject_tombstone",
    "actor",
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
    "retrieval_trace",
    "deletion_generation",
    "job_state",
    "review_event",
    "review_event_unit",
    "api_key",
    "forgotten_source",
    "mutation_ledger",
    "schema_migrations",
}

TENANT_SCOPED = REQUIRED_TABLES - {"schema_migrations"}


def read_sql() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MIGRATIONS.glob("*.sql"))
    ).lower()


def table_block(sql: str, table: str) -> str:
    match = re.search(rf"create table if not exists memphant\.{table}\s*\(", sql)
    if not match:
        return ""
    start = match.start()
    next_match = re.search(r"create table if not exists", sql[match.end():])
    if next_match is None:
        return sql[start:]
    return sql[start:match.end() + next_match.start()]


def main() -> int:
    sql = read_sql()
    findings: list[str] = []

    for table in sorted(REQUIRED_TABLES):
        if f"create table if not exists memphant.{table}" not in sql:
            findings.append(f"{table}:missing_table")

    if "schema_compat_revision" not in table_block(sql, "schema_migrations"):
        findings.append("schema_migrations:missing_schema_compat_revision")

    for table in sorted(TENANT_SCOPED):
        block = table_block(sql, table)
        if table != "tenant" and "tenant_id" not in block:
            findings.append(f"{table}:missing_tenant_id")
        if f"alter table memphant.{table} enable row level security" not in sql:
            findings.append(f"{table}:missing_rls")
        if f"alter table memphant.{table} force row level security" not in sql:
            findings.append(f"{table}:missing_force_rls")
        if table != "tenant" and f"create index if not exists memphant_{table}_tenant" not in sql:
            findings.append(f"{table}:missing_tenant_index")
        policy_marker = f"create policy memphant_{table}_tenant_isolation"
        if table != "tenant" and policy_marker not in sql:
            findings.append(f"{table}:missing_tenant_policy")

    for role in ("anon", "authenticated", "authenticator"):
        if f"revoke all on schema memphant from {role}" not in sql:
            findings.append(f"{role}:missing_schema_revoke")
        if re.search(rf"grant\s+(select|insert|update|delete|all).*?\s+to\s+{role}\b", sql, re.S):
            findings.append(f"{role}:browser_role_grant")

    for function in (
        "current_tenant_id",
        "bind_tenant",
        "set_updated_at",
        "authenticate_api_key",
        "claim_reflect_jobs",
        "dead_letter_count",
        "provision_tenant",
        "provision_api_key",
        "revoke_api_key",
    ):
        block = sql[sql.find(f"function memphant.{function}"):]
        if not block or "set search_path = memphant, pg_catalog" not in block[:500]:
            findings.append(f"{function}:missing_search_path")

    for role in (
        "memphant_owner",
        "memphant_app",
        "memphant_worker",
        "memphant_authn",
        "memphant_readonly",
        "memphant_provisioner",
    ):
        if f"create role {role} nologin" not in sql:
            findings.append(f"{role}:missing_capability_role")

    for function in (
        "authenticate_api_key",
        "claim_reflect_jobs",
        "dead_letter_count",
        "provision_tenant",
        "provision_api_key",
        "revoke_api_key",
    ):
        block = sql[sql.find(f"function memphant.{function}"):]
        if not block or "security definer" not in block[:900]:
            findings.append(f"{function}:missing_security_definer")

    if findings:
        print("migration_contract=dirty")
        for finding in findings:
            print(finding)
        return 1

    print("migration_contract=clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
