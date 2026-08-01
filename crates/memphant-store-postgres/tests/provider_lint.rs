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

/// The compatibility floor must be strictly older than the head — a binary
/// whose floor IS its head refuses every database but the one it just shipped.
///
/// This test used to hardcode the full migration list and the literal value of
/// `SCHEMA_COMPAT_REVISION`. That did not merely go stale, it PINNED A BUG:
/// migration `20260731_006` moved the floor to itself, and this assertion kept
/// claiming the floor was still `002`, so the mismatch that made `ping` reject
/// a correctly-migrated database read as intentional. Hardcoding a second copy
/// of a list is how the first copy stops being checked.
///
/// The list, the head, and the floor's value are now derived from
/// `memphant_migrations/versions/` and from the migrations' own SQL in
/// `tests/migrations_manifest.rs`. Only the relational invariant lives here.
#[test]
fn the_compatibility_floor_is_older_than_the_migration_head() {
    assert_eq!(
        MIGRATIONS.last().expect("MIGRATIONS is non-empty").0,
        MIGRATION_HEAD
    );
    assert!(
        MIGRATION_HEAD > SCHEMA_COMPAT_REVISION,
        "MIGRATION_HEAD {MIGRATION_HEAD:?} must be strictly newer than \
         SCHEMA_COMPAT_REVISION {SCHEMA_COMPAT_REVISION:?}"
    );
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
