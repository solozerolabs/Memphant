//! The embedded `MIGRATIONS` list must match `memphant_migrations/versions/`.
//!
//! It silently fell three behind once (005/006/007 shipped as files but were
//! never embedded). That is not cosmetic: readiness compares the applied head
//! against `MIGRATIONS.last()`, so a scratch DB at the real head reported
//! not-ready and left a live-Postgres contract test permanently red; and
//! `lint_migrations` lints only the embedded subset, so provider
//! bootstrap-check never saw the unembedded files.
//!
//! These tests need no database, so they run in the ordinary
//! `cargo test --workspace` rather than behind `--ignored` — the drift they
//! catch is a build-time fact, and the whole point is to fail before anyone
//! reaches a Postgres suite.

use std::fs;
use std::path::PathBuf;

use memphant_store_postgres::{MIGRATION_HEAD, MIGRATIONS};

/// Migration names on disk, in apply order. Filenames are date-and-sequence
/// prefixed, so lexicographic order IS apply order — that is the naming
/// convention, and `migrations_are_in_lexicographic_apply_order` pins it.
fn versions_on_disk() -> Vec<String> {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../memphant_migrations/versions")
        .canonicalize()
        .expect("memphant_migrations/versions must exist");
    let mut names: Vec<String> = fs::read_dir(&dir)
        .expect("versions directory must be readable")
        .map(|entry| entry.expect("readable dir entry").path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "sql"))
        .map(|path| {
            path.file_stem()
                .expect("sql file has a stem")
                .to_string_lossy()
                .into_owned()
        })
        .collect();
    names.sort();
    assert!(!names.is_empty(), "no .sql migrations found in {dir:?}");
    names
}

#[test]
fn migrations_list_matches_the_versions_directory() {
    let embedded: Vec<String> = MIGRATIONS
        .iter()
        .map(|(name, _)| name.to_string())
        .collect();
    let on_disk = versions_on_disk();

    // Report the difference in both directions rather than just "not equal":
    // the failure a reader needs is "which file did you forget to embed".
    let missing: Vec<&String> = on_disk.iter().filter(|n| !embedded.contains(n)).collect();
    let extra: Vec<&String> = embedded.iter().filter(|n| !on_disk.contains(n)).collect();
    assert!(
        missing.is_empty(),
        "migrations on disk but NOT embedded in MIGRATIONS: {missing:?}\n\
         Add an `include_str!` and a MIGRATIONS tuple in apply order \
         (crates/memphant-store-postgres/src/lib.rs), and move MIGRATION_HEAD \
         if the new file is the newest."
    );
    assert!(
        extra.is_empty(),
        "migrations embedded in MIGRATIONS but NOT on disk: {extra:?}"
    );
    assert_eq!(
        embedded, on_disk,
        "MIGRATIONS and the versions directory hold the same names in a \
         different order; MIGRATIONS must be in apply order"
    );
}

#[test]
fn migration_head_is_the_last_embedded_migration() {
    let (last_name, _) = MIGRATIONS.last().expect("MIGRATIONS is non-empty");
    assert_eq!(
        *last_name, MIGRATION_HEAD,
        "MIGRATION_HEAD must name the newest bundled migration; readiness \
         compares the applied database head against it"
    );
}

#[test]
fn migrations_are_in_lexicographic_apply_order() {
    let embedded: Vec<&str> = MIGRATIONS.iter().map(|(name, _)| *name).collect();
    let mut sorted = embedded.clone();
    sorted.sort_unstable();
    assert_eq!(
        embedded, sorted,
        "migration names are date-and-sequence prefixed, so apply order must \
         equal lexicographic order"
    );
}

/// Pull the `schema_compat_revision` a migration records for itself, from the
/// `insert into memphant.schema_migrations (version, schema_compat_revision,
/// migration_kind) values ('<version>', '<compat>', '<kind>')` block every
/// migration ends with. Second quoted string after the insert.
fn declared_compat_revision(sql: &str) -> String {
    let insert = sql
        .find("insert into memphant.schema_migrations")
        .expect("every migration records itself in schema_migrations");
    let values = sql[insert..]
        .find("values")
        .expect("the self-record insert has a values clause")
        + insert;
    let quoted: Vec<&str> = sql[values..]
        .split('\'')
        .skip(1)
        .step_by(2)
        .take(2)
        .collect();
    assert_eq!(
        quoted.len(),
        2,
        "could not read (version, schema_compat_revision) from the self-record insert"
    );
    quoted[1].to_string()
}

#[test]
fn schema_compat_revision_matches_the_newest_migration() {
    let (head_name, head_sql) = MIGRATIONS.last().expect("MIGRATIONS is non-empty");
    let declared = declared_compat_revision(head_sql);
    assert_eq!(
        declared,
        memphant_types::SCHEMA_COMPAT_REVISION,
        "the newest embedded migration ({head_name}) records compatibility \
         floor {declared:?}, but this binary declares \
         {:?}. `PgStore::ping` requires a schema_migrations row matching BOTH \
         MIGRATION_HEAD and SCHEMA_COMPAT_REVISION, so a mismatch makes a \
         correctly-migrated database report as incompatible and the server \
         never becomes ready. A breaking migration must bump \
         memphant_types::SCHEMA_COMPAT_REVISION.",
        memphant_types::SCHEMA_COMPAT_REVISION
    );
}

#[test]
fn the_compatibility_floor_is_itself_an_embedded_migration() {
    let names: Vec<&str> = MIGRATIONS.iter().map(|(name, _)| *name).collect();
    assert!(
        names.contains(&memphant_types::SCHEMA_COMPAT_REVISION),
        "SCHEMA_COMPAT_REVISION {:?} names no embedded migration; it must be \
         one of {names:?}",
        memphant_types::SCHEMA_COMPAT_REVISION
    );
}

#[test]
fn every_embedded_migration_carries_its_own_sql() {
    // Guards the copy-paste failure the tuple shape invites: pairing a new
    // name with a previously-defined `*_SQL` constant. Bodies must be
    // non-empty and pairwise distinct.
    for (name, sql) in MIGRATIONS {
        assert!(!sql.trim().is_empty(), "{name} embeds empty SQL");
    }
    for (i, (name, sql)) in MIGRATIONS.iter().enumerate() {
        for (other_name, other_sql) in &MIGRATIONS[i + 1..] {
            assert!(
                sql != other_sql,
                "{name} and {other_name} embed byte-identical SQL — \
                 a name was probably paired with the wrong constant"
            );
        }
    }
}
