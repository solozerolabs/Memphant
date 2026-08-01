#!/usr/bin/env bash
# Run a command against a freshly-minted, migrated scratch Postgres database,
# then drop it — even on failure. Isolates job_state/tenant debris from the
# shared campaign DB (`memphant`) so a global oldest-first worker claim can
# never be starved by another process's foreign rows: an ephemeral DB has no
# foreign rows. This is the fix for the recurring "job_state debris starves a
# worker tick" incident (contract tests + killed benches vs. the e2e probe).
#
# Usage:
#   bash scripts/with_scratch_db.sh <base_database_url> <ENV_VAR> <cmd> [args...]
#
# The command runs with <ENV_VAR> set to the scratch DB's URL. Examples:
#   bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant \
#     DATABASE_URL bash scripts/e2e_probe.sh
#   bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant \
#     MEMPHANT_TEST_DATABASE_URL cargo test -p memphant-store-postgres -- --ignored
#
# ponytail: base_database_url must be a plain postgres://user:pass@host:port/db
# URL with no query string (?sslmode=...); the campaign/local URLs are plain.
# Add query-string handling only if a provider URL ever needs it here.
set -euo pipefail

BASE_URL="${1:?base database url required}"
ENV_VAR="${2:?target env var name required}"
shift 2
[ "$#" -gt 0 ] || { echo "with_scratch_db.sh: no command given" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Maintenance URL = same server, `postgres` database. Scratch URL = same
# server, unique DB name (pid+epoch keeps the DB NAMES from colliding).
PREFIX="${BASE_URL%/*}"
NAME="memphant_scratch_$$_$(date +%s)"
ADMIN_URL="$PREFIX/postgres"
SCRATCH_URL="$PREFIX/$NAME"

# SCRATCH DATABASES ARE ISOLATED; THEIR BOOTSTRAP IS NOT.
#
# `20260703_001_wsa_bootstrap.sql` runs six `create role` statements, and roles
# live in `pg_authid` — CLUSTER-wide, shared by every database on the server. So
# two harnesses bootstrapping different scratch DBs concurrently contend on the
# same catalog rows and both die with `ERROR: tuple concurrently updated` part
# way through migration 001. The unique DB name does not help: it was never the
# thing that collided.
#
# This bit a live bench run: two arms launched in parallel on separate ports and
# separate scratch DBs, both dead inside 001. "Ephemeral Postgres per harness"
# reads as though it makes runs independent, and for table data it does — for
# roles it does not.
#
# So serialize create+migrate across every concurrent invocation on this host,
# keyed by server so unrelated servers don't wait on each other. `mkdir` is the
# portable atomic test-and-set (macOS has no `flock`). The command itself runs
# OUTSIDE the lock — only bootstrap is serialized, so parallel benches still
# overlap for all but their first ~10s.
LOCK_DIR="${TMPDIR:-/tmp}/memphant-scratch-bootstrap-$(printf '%s' "$PREFIX" | shasum -a 256 | cut -c1-16).lock"
LOCK_HELD=""
acquire_bootstrap_lock() {
  local waited=0
  until mkdir "$LOCK_DIR" 2>/dev/null; do
    # Reap a lock orphaned by a killed harness (no live pid), then retry.
    if [ -f "$LOCK_DIR/pid" ] && ! kill -0 "$(cat "$LOCK_DIR/pid" 2>/dev/null)" 2>/dev/null; then
      rm -rf "$LOCK_DIR"
      continue
    fi
    if [ "$waited" -ge 300 ]; then
      echo "with_scratch_db.sh: timed out waiting for $LOCK_DIR" >&2
      exit 3
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "$$" > "$LOCK_DIR/pid"
  LOCK_HELD=1
}
release_bootstrap_lock() {
  [ -n "$LOCK_HELD" ] && rm -rf "$LOCK_DIR" && LOCK_HELD=""
  return 0
}

cleanup() {
  release_bootstrap_lock
  psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q \
    -c "drop database if exists \"$NAME\" with (force)" >/dev/null 2>&1 || true
}
trap cleanup EXIT

acquire_bootstrap_lock
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q -c "create database \"$NAME\"" >/dev/null
# client-min-messages=warning silences the migrations' idempotent
# `drop ... if exists` NOTICEs (~45 lines of noise on a fresh DB); real
# warnings/errors still surface.
PGOPTIONS='-c client-min-messages=warning' \
  python3 "$ROOT/scripts/apply_memphant_migrations.py" --database-url "$SCRATCH_URL" >/dev/null

export "$ENV_VAR=$SCRATCH_URL"
"$@"
