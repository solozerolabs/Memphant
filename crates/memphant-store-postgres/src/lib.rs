use std::fmt;
use std::str::FromStr;

mod store;

pub use store::{
    DEFAULT_DATABASE_MAX_CONNECTIONS, MAX_WORKER_DATABASE_MAX_CONNECTIONS, PgStore, PgTxn,
};

pub const STORE_NAME: &str = "postgres";

const WSA_BOOTSTRAP_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260703_001_wsa_bootstrap.sql");
const FILE_SYNC_MUTATION_VERB_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260723_002_file_sync_mutation_verb.sql");
const WORKER_CLAIM_THROUGHPUT_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260724_003_worker_claim_throughput.sql");

/// Newest migration understood by this binary. Readiness permits a newer
/// database head only while its recorded compatibility floor remains here.
pub const MIGRATION_HEAD: &str = "20260724_003_worker_claim_throughput";

/// Bundled migrations in apply order.
pub const MIGRATIONS: &[(&str, &str)] = &[
    ("20260703_001_wsa_bootstrap", WSA_BOOTSTRAP_SQL),
    (
        "20260723_002_file_sync_mutation_verb",
        FILE_SYNC_MUTATION_VERB_SQL,
    ),
    (MIGRATION_HEAD, WORKER_CLAIM_THROUGHPUT_SQL),
];

const REQUIRED_TABLES: &[&str] = &[
    "tenant",
    "subject",
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
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Provider {
    PlainPostgres,
    Supabase,
    Neon,
}

impl fmt::Display for Provider {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let value = match self {
            Self::PlainPostgres => "plain-postgres",
            Self::Supabase => "supabase",
            Self::Neon => "neon",
        };
        formatter.write_str(value)
    }
}

impl FromStr for Provider {
    type Err = LintError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "plain-postgres" => Ok(Self::PlainPostgres),
            "supabase" => Ok(Self::Supabase),
            "neon" => Ok(Self::Neon),
            other => Err(LintError {
                findings: vec![format!("provider:unsupported:{other}")],
            }),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LintError {
    findings: Vec<String>,
}

impl LintError {
    pub fn findings(&self) -> &[String] {
        &self.findings
    }
}

impl fmt::Display for LintError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.findings.join("\n"))
    }
}

impl std::error::Error for LintError {}

const FINAL_TABLES: &[&str] = &[
    "api_key",
    "forgotten_source",
    "review_event",
    "review_event_unit",
];

const CAPABILITY_ROLES: &[&str] = &[
    "memphant_owner",
    "memphant_app",
    "memphant_worker",
    "memphant_authn",
    "memphant_readonly",
    "memphant_provisioner",
];

const SECURITY_DEFINER_FUNCTIONS: &[&str] = &[
    "authenticate_api_key",
    "claim_reflect_jobs",
    "dead_letter_count",
    "provision_tenant",
    "provision_api_key",
    "revoke_api_key",
];

pub fn lint_migrations(provider: Provider) -> Result<(), LintError> {
    let sql = normalize(
        &MIGRATIONS
            .iter()
            .map(|(_, migration)| *migration)
            .collect::<Vec<_>>()
            .join("\n"),
    );
    let mut findings = lint_sql(&sql, provider);
    for table in REQUIRED_TABLES {
        if !sql.contains(&format!("create table if not exists memphant.{table}")) {
            findings.push(format!("{table}:missing_table"));
        }
    }
    if !table_block(&sql, "schema_migrations").contains("schema_compat_revision") {
        findings.push("schema_migrations:missing_schema_compat_revision".to_string());
    }

    for table in FINAL_TABLES {
        if !sql.contains(&format!("create table if not exists memphant.{table}")) {
            findings.push(format!("{table}:missing_table"));
        }
    }
    for role in CAPABILITY_ROLES {
        if !sql.contains(&format!("create role {role} nologin")) {
            findings.push(format!("{role}:missing_capability_role"));
        }
    }
    for function in SECURITY_DEFINER_FUNCTIONS {
        let marker = format!("function memphant.{function}");
        let block = sql
            .find(&marker)
            .map(|start| sql[start..].chars().take(900).collect::<String>())
            .unwrap_or_default();
        if !block.contains("security definer") {
            findings.push(format!("{function}:missing_security_definer"));
        }
    }
    for object in ["tables", "sequences", "functions"] {
        if !sql.contains(&format!(
            "alter default privileges for role memphant_owner in schema memphant revoke all on {object} from public"
        )) {
            findings.push(format!("default_privileges:{object}:missing_public_revoke"));
        }
    }
    finish(findings)
}

pub fn lint_migration_sql(sql: &str, provider: Provider) -> Result<(), LintError> {
    finish(lint_sql(&normalize(sql), provider))
}

/// Drops are allowed only when the migration declares
/// `-- migration_kind: rewrite` within its first few header lines.
fn declares_rewrite(sql: &str) -> bool {
    sql.lines()
        .take(5)
        .any(|line| line.trim() == "-- migration_kind: rewrite")
}

fn lint_sql(sql: &str, provider: Provider) -> Vec<String> {
    let mut findings = Vec::new();
    let rewrite = declares_rewrite(sql);
    if sql.contains("drop table") && !rewrite {
        findings.push("boundary:drop_table".to_string());
    }
    if sql.contains("drop index") && !rewrite {
        findings.push("boundary:drop_index".to_string());
    }
    if sql.contains("public.") {
        findings.push("boundary:public_schema_reference".to_string());
    }
    if sql.contains("syndai.") {
        findings.push("boundary:syndai_schema_reference".to_string());
    }

    for role in ["anon", "authenticated", "authenticator"] {
        if grants_to_role(sql, role) {
            findings.push(format!("{role}:browser_role_grant"));
        }
        if provider == Provider::Supabase
            && !sql.contains(&format!("revoke all on schema memphant from {role}"))
        {
            findings.push(format!("{role}:missing_schema_revoke"));
        }
    }

    for table in created_tables(sql) {
        let block = table_block(sql, &table);
        let tenant_scoped = table == "tenant" || block.contains("tenant_id");
        if !tenant_scoped || table == "schema_migrations" {
            continue;
        }
        if !sql.contains(&format!(
            "alter table memphant.{table} enable row level security"
        )) {
            findings.push(format!("{table}:missing_rls"));
        }
        if !sql.contains(&format!(
            "alter table memphant.{table} force row level security"
        )) {
            findings.push(format!("{table}:missing_force_rls"));
        }
        if table != "tenant"
            && !sql.contains(&format!(
                "create index if not exists memphant_{table}_tenant"
            ))
        {
            findings.push(format!("{table}:missing_tenant_index"));
        }
        if table != "tenant"
            && !sql.contains(&format!("create policy memphant_{table}_tenant_isolation"))
        {
            findings.push(format!("{table}:missing_tenant_policy"));
        }
    }

    for function in [
        "current_tenant_id",
        "bind_tenant",
        "set_updated_at",
        "authenticate_api_key",
        "claim_reflect_jobs",
        "dead_letter_count",
        "provision_tenant",
        "provision_api_key",
        "revoke_api_key",
    ] {
        if let Some(index) = sql.find(&format!("function memphant.{function}"))
            && !sql[index..]
                .chars()
                .take(500)
                .collect::<String>()
                .contains("set search_path = memphant, pg_catalog")
        {
            findings.push(format!("{function}:missing_search_path"));
        }
    }

    findings
}

fn finish(findings: Vec<String>) -> Result<(), LintError> {
    if findings.is_empty() {
        Ok(())
    } else {
        Err(LintError { findings })
    }
}

fn normalize(sql: &str) -> String {
    sql.to_lowercase()
}

fn created_tables(sql: &str) -> Vec<String> {
    sql.split("create table if not exists memphant.")
        .skip(1)
        .filter_map(|tail| {
            tail.chars()
                .take_while(|ch| ch.is_ascii_alphanumeric() || *ch == '_')
                .collect::<String>()
                .into()
        })
        .collect()
}

fn table_block(sql: &str, table: &str) -> String {
    let marker = format!("create table if not exists memphant.{table}");
    let Some(start) = sql.find(&marker) else {
        return String::new();
    };
    let rest = &sql[start + marker.len()..];
    let end = rest
        .find("create table if not exists")
        .map(|offset| start + marker.len() + offset)
        .unwrap_or(sql.len());
    sql[start..end].to_string()
}

fn grants_to_role(sql: &str, role: &str) -> bool {
    let mut remainder = sql;
    while let Some(index) = remainder.find("grant ") {
        let tail = &remainder[index..];
        let end = tail.find(';').unwrap_or(tail.len());
        let statement = &tail[..end];
        if statement.contains(" on memphant.") && statement.contains(&format!(" to {role}")) {
            return true;
        }
        remainder = &tail[end..];
        if remainder.is_empty() {
            break;
        }
    }
    false
}
