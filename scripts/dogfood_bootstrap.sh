#!/usr/bin/env bash
# One-time turnkey setup for the Codex/Syndai MemPhant dogfood.
#
# Builds an ISOLATED `memphant_dogfood` database (never the shared campaign
# base), applies migrations, mints non-superuser served login roles, binds one
# coding context, and mints a coding-agent key (no owner capabilities). Prints
# the exact env + .codex/config.toml to wire Codex, and the report command.
#
# The HTTP server runs only transiently here to create the context binding.
# At coding time the `memphant-mcp stdio` binary talks to Postgres directly, so
# nothing stays running. Coding-lane `remember` mints compact units
# synchronously, so no worker is needed either.
#
# Usage:
#   ADMIN_DATABASE_URL=postgres://memphant:memphant@localhost:5432/postgres \
#     bash scripts/dogfood_bootstrap.sh
#
# ADMIN_DATABASE_URL is a migrator/superuser credential on the target cluster,
# pointed at ANY existing database (it is used to `createdb memphant_dogfood`).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN_BASE="${ADMIN_DATABASE_URL:-postgres://memphant:memphant@localhost:5432/postgres}"
DB_NAME="memphant_dogfood"
HOSTPART="${ADMIN_BASE#*@}"; HOSTPART="${HOSTPART%%/*}"
SCHEME="${ADMIN_BASE%%://*}"
CRED="${ADMIN_BASE#*://}"; CRED="${CRED%%@*}"
ADMIN_URL="${SCHEME}://${CRED}@${HOSTPART}/${DB_NAME}"
PORT="${PORT:-8091}"
BASE="http://127.0.0.1:${PORT}"

SERVER="$ROOT/target/debug/memphant-server"
CLI="$ROOT/target/debug/memphant-cli"
MCP="$ROOT/target/debug/memphant-mcp"
SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

say() { printf '\n### %s\n' "$*"; }
fail() { printf 'BOOTSTRAP FAILED: %s\n' "$*" >&2; exit 1; }
jget() { python3 -c "import json,sys;print(json.load(sys.stdin)$1)"; }
login_url() { printf '%s://%s:%s@%s/%s' "$SCHEME" "$1" "$2" "$HOSTPART" "$DB_NAME"; }

say "build binaries (debug)"
(cd "$ROOT" && cargo build -q -p memphant-server -p memphant-cli -p memphant-mcp)

say "create isolated database $DB_NAME (idempotent)"
psql "$ADMIN_BASE" -tAc "select 1 from pg_database where datname='$DB_NAME'" | grep -q 1 \
  || psql "$ADMIN_BASE" -q -c "create database $DB_NAME" || fail "createdb"

say "apply migrations to head"
python3 "$ROOT/scripts/apply_memphant_migrations.py" --database-url "$ADMIN_URL" | tail -1

say "mint non-superuser served login roles (app, authn)"
PW="dogfood$(uuidgen | tr -dc 'A-Za-z0-9')"
for pair in "app:memphant_app" "authn:memphant_authn"; do
  cap="${pair#*:}"; login="mp_dogfood_${pair%%:*}"
  psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q \
    -c "do \$\$ begin if not exists (select 1 from pg_roles where rolname='$login') then create role \"$login\" login noinherit; end if; end \$\$" \
    -c "alter role \"$login\" password '$PW'" \
    -c "grant $cap to \"$login\"" \
    -c "revoke all on schema memphant from \"$login\"" || fail "role $login"
done
APP_URL=$(login_url "mp_dogfood_app" "$PW")
AUTHN_URL=$(login_url "mp_dogfood_authn" "$PW")

say "provision tenant + bootstrap key"
TENANT=$("$CLI" admin create-tenant --name "dogfood-$RANDOM" --database-url "$ADMIN_URL" | sed -n 's/^tenant_created id=\([^ ]*\).*/\1/p')
[ -n "$TENANT" ] || fail "create-tenant"
BOOT_KEY=$("$CLI" admin create-key --tenant "$TENANT" --max-trust trusted_system --database-url "$ADMIN_URL" | tail -1)
[ -n "$BOOT_KEY" ] || fail "bootstrap key"

say "start transient server on :$PORT"
env -u DATABASE_URL MEMPHANT_APP_DATABASE_URL="$APP_URL" MEMPHANT_AUTHN_DATABASE_URL="$AUTHN_URL" \
  MEMPHANT_BIND="127.0.0.1:${PORT}" "$SERVER" & SERVER_PID=$!
for _ in $(seq 1 120); do curl -sf "$BASE/v1/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "$BASE/v1/health" >/dev/null 2>&1 || fail "server not healthy on :$PORT"

say "bind coding context"
BIND=$(curl -s -X PUT -H "Authorization: Bearer $BOOT_KEY" -H "Idempotency-Key: dogfood-$(uuidgen)" \
  -H 'content-type: application/json' \
  -d '{"subject":{"external_ref":"subject:codex-dogfood","kind":"user"},"actor":{"external_ref":"actor:codex-dogfood","kind":"system"},"scope":{"external_ref":"scope:codex-dogfood","kind":"user_root"},"agent_node":{"external_ref":"agent:codex-dogfood"}}' \
  "$BASE/v1/context-bindings/codex-dogfood")
SUBJ=$(echo "$BIND" | jget "['subject_id']") || fail "bind: $BIND"
SCOPE=$(echo "$BIND" | jget "['scope_id']")
ACTOR=$(echo "$BIND" | jget "['actor_id']")
AGENT=$(echo "$BIND" | jget "['agent_node_id']")
GEN=$(echo "$BIND" | jget "['subject_generation']")

say "mint coding-agent key (can_forget=false by default)"
CODING_KEY=$("$CLI" admin create-key --tenant "$TENANT" --max-trust trusted_system \
  --subject-id "$SUBJ" --subject-generation "$GEN" --scope "$SCOPE" --actor "$ACTOR" \
  --agent-node "$AGENT" --database-url "$ADMIN_URL" | tail -1)
[ -n "$CODING_KEY" ] || fail "coding key"

cat <<EOF

======================================================================
DOGFOOD READY. Capture these — the passwords are not stored anywhere.
======================================================================

# Export in the shell that launches Codex:
export MEMPHANT_API_KEY='$CODING_KEY'
export MEMPHANT_APP_DATABASE_URL='$APP_URL'
export MEMPHANT_AUTHN_DATABASE_URL='$AUTHN_URL'

# Coding subject (use with the adherence report):
export DOGFOOD_SUBJECT='$SUBJ'
export DOGFOOD_DB='$ADMIN_URL'

# .codex/config.toml (stdio) for the Syndai project:
[mcp_servers.memphant]
command = "$MCP"
args = ["stdio"]
env_vars = ["MEMPHANT_API_KEY", "MEMPHANT_APP_DATABASE_URL", "MEMPHANT_AUTHN_DATABASE_URL"]
required = true
enabled_tools = ["recall"]

# Read adherence after some coding sessions:
psql "\$DOGFOOD_DB" -v subject="'\$DOGFOOD_SUBJECT'" \\
  -v since="'2026-08-15 00:00:00+00'" -f scripts/mcp_usage_report.sql

# Teardown: kill no server (none persists); drop the DB + roles with:
#   psql "$ADMIN_BASE" -c 'drop database $DB_NAME' \\
#     -c 'drop role mp_dogfood_app' -c 'drop role mp_dogfood_authn'
======================================================================
EOF
