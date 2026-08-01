use std::fmt;
use std::str::FromStr;

mod store;

pub use store::{
    APP_ROLE, AUTHN_ROLE, DEFAULT_DATABASE_MAX_CONNECTIONS, MAX_WORKER_DATABASE_MAX_CONNECTIONS,
    PROVISIONER_ROLE, PgStore, PgTxn, WORKER_ROLE,
};

pub const STORE_NAME: &str = "postgres";

const WSA_BOOTSTRAP_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260703_001_wsa_bootstrap.sql");
const FILE_SYNC_MUTATION_VERB_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260723_002_file_sync_mutation_verb.sql");
const WORKER_CLAIM_THROUGHPUT_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260724_003_worker_claim_throughput.sql");
const SERVED_LOGIN_ROLES_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260730_004_served_login_roles.sql");
const PENDING_WORKER_JOB_COUNT_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260730_005_pending_worker_job_count.sql");
const PREFERENCE_MEMORY_KIND_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260731_006_preference_memory_kind.sql");
const SEMANTIC_ONLY_SUBJECT_EXCLUSION_SQL: &str = include_str!(
    "../../../memphant_migrations/versions/20260731_007_semantic_only_subject_exclusion.sql"
);
const DROP_RETENTION_TIER_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260801_008_drop_retention_tier.sql");
const DROP_DEAD_SCHEMA_SQL: &str =
    include_str!("../../../memphant_migrations/versions/20260801_009_drop_dead_schema.sql");

/// Newest migration understood by this binary. Readiness permits a newer
/// database head only while its recorded compatibility floor remains here.
pub const MIGRATION_HEAD: &str = "20260801_009_drop_dead_schema";

/// Bundled migrations in apply order.
///
/// This list had silently fallen three migrations behind
/// `memphant_migrations/versions/`, which is not a cosmetic drift: readiness
/// compares the applied head against `MIGRATIONS.last()`, so a scratch DB at
/// the real head reported as not-ready and left
/// `ping_rejects_bootstrap_only_schema_until_required_revision_is_applied`
/// permanently red; and `lint_migrations` lints only what is embedded here, so
/// provider bootstrap-check could not see the unembedded migrations at all.
/// `migrations_list_matches_the_versions_directory` below now fails the build
/// the moment the two disagree — adding a `.sql` file is no longer enough.
pub const MIGRATIONS: &[(&str, &str)] = &[
    ("20260703_001_wsa_bootstrap", WSA_BOOTSTRAP_SQL),
    (
        "20260723_002_file_sync_mutation_verb",
        FILE_SYNC_MUTATION_VERB_SQL,
    ),
    (
        "20260724_003_worker_claim_throughput",
        WORKER_CLAIM_THROUGHPUT_SQL,
    ),
    ("20260730_004_served_login_roles", SERVED_LOGIN_ROLES_SQL),
    (
        "20260730_005_pending_worker_job_count",
        PENDING_WORKER_JOB_COUNT_SQL,
    ),
    (
        "20260731_006_preference_memory_kind",
        PREFERENCE_MEMORY_KIND_SQL,
    ),
    (
        "20260731_007_semantic_only_subject_exclusion",
        SEMANTIC_ONLY_SUBJECT_EXCLUSION_SQL,
    ),
    ("20260801_008_drop_retention_tier", DROP_RETENTION_TIER_SQL),
    (MIGRATION_HEAD, DROP_DEAD_SCHEMA_SQL),
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
    // Per-migration first: each migration's drops are judged against its OWN
    // header, which the concatenated pass below structurally cannot see.
    let mut findings: Vec<String> = MIGRATIONS
        .iter()
        .flat_map(|(name, migration)| {
            let normalized = normalize(migration);
            let rewrite = declares_rewrite(&normalized);
            let mut per = Vec::new();
            if normalized.contains("drop table") && !rewrite {
                per.push(format!("{name}:boundary:drop_table"));
            }
            if normalized.contains("drop index") && !rewrite {
                per.push(format!("{name}:boundary:drop_index"));
            }
            per
        })
        .collect();
    findings.extend(lint_sql(&sql, provider, false));
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
    finish(lint_sql(&normalize(sql), provider, true))
}

/// Drops are allowed only when the migration declares
/// `-- migration_kind: rewrite` within its first few header lines.
fn declares_rewrite(sql: &str) -> bool {
    sql.lines()
        .take(5)
        .any(|line| line.trim() == "-- migration_kind: rewrite")
}

/// `check_drops` is false for the corpus pass. The drop gate is a PER-MIGRATION
/// rule — it asks whether the migration doing the dropping declared itself a
/// rewrite — but the corpus pass lints every migration concatenated, where
/// `declares_rewrite` can only ever see the FIRST migration's header. So a
/// migration that correctly declares `-- migration_kind: rewrite` still tripped
/// the gate, and the concatenated pass could never pass once any migration
/// contained a drop. It went unnoticed only because no embedded migration had
/// one until `20260801_008`/`_009` — which were themselves not embedded, so the
/// lint had never seen them. `lint_migrations` now runs this gate per migration
/// and leaves the corpus pass to the structural checks that genuinely need the
/// whole concatenation.
fn lint_sql(sql: &str, provider: Provider, check_drops: bool) -> Vec<String> {
    let mut findings = Vec::new();
    let rewrite = declares_rewrite(sql);
    if check_drops && sql.contains("drop table") && !rewrite {
        findings.push("boundary:drop_table".to_string());
    }
    if check_drops && sql.contains("drop index") && !rewrite {
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
