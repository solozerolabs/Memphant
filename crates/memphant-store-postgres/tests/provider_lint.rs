use memphant_store_postgres::{
    MIGRATION_HEAD, MIGRATIONS, Provider, lint_migration_sql, lint_migrations,
};
use memphant_types::SCHEMA_COMPAT_REVISION;

#[test]
fn bundled_wsa_migration_passes_all_provider_lints() {
    for provider in [Provider::PlainPostgres, Provider::Supabase, Provider::Neon] {
        lint_migrations(provider).expect("bundled migration should pass");
    }
}

#[test]
fn bundled_migrations_are_ordered_through_worker_claim_forward_migration() {
    let versions: Vec<_> = MIGRATIONS.iter().map(|(version, _)| *version).collect();
    assert_eq!(
        versions,
        [
            "20260703_001_wsa_bootstrap",
            "20260723_002_file_sync_mutation_verb",
            "20260724_003_worker_claim_throughput",
        ]
    );
    assert_eq!(MIGRATIONS.last().unwrap().0, MIGRATION_HEAD);
    assert_eq!(
        SCHEMA_COMPAT_REVISION,
        "20260723_002_file_sync_mutation_verb"
    );
    assert!(MIGRATION_HEAD > SCHEMA_COMPAT_REVISION);
}

#[test]
fn provider_lint_rejects_drops_without_rewrite_header() {
    let bad_sql = "drop table memphant.review_event;";
    let error = lint_migration_sql(bad_sql, Provider::PlainPostgres).expect_err("drop must fail");
    assert!(error.to_string().contains("boundary:drop_table"));

    let bad_index_sql = "drop index memphant.some_idx;";
    let error =
        lint_migration_sql(bad_index_sql, Provider::PlainPostgres).expect_err("drop must fail");
    assert!(error.to_string().contains("boundary:drop_index"));
}

#[test]
fn provider_lint_allows_drops_under_rewrite_header() {
    let rewrite_sql = "-- migration_kind: rewrite\ndrop table memphant.review_event;\ndrop index memphant.some_idx;";
    lint_migration_sql(rewrite_sql, Provider::PlainPostgres)
        .expect("rewrite-declared drops should pass");
}

#[test]
fn provider_lint_rejects_browser_role_grants() {
    let bad_sql = r#"
        create table if not exists memphant.memory_unit (
          id uuid not null,
          tenant_id uuid not null,
          scope_id uuid not null,
          primary key (tenant_id, id)
        );
        alter table memphant.memory_unit enable row level security;
        create index if not exists memphant_memory_unit_tenant_idx on memphant.memory_unit (tenant_id);
        grant select on memphant.memory_unit to authenticated;
    "#;

    let error = lint_migration_sql(bad_sql, Provider::Supabase).expect_err("grant must fail");

    assert!(error.to_string().contains("browser_role_grant"));
}

#[test]
fn provider_lint_rejects_missing_rls() {
    let bad_sql = r#"
        create table if not exists memphant.memory_unit (
          id uuid not null,
          tenant_id uuid not null,
          scope_id uuid not null,
          primary key (tenant_id, id)
        );
        create index if not exists memphant_memory_unit_tenant_idx on memphant.memory_unit (tenant_id);
    "#;

    let error = lint_migration_sql(bad_sql, Provider::PlainPostgres).expect_err("RLS must fail");

    assert!(error.to_string().contains("memory_unit:missing_rls"));
}
