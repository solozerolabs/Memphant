#!/usr/bin/env bash
# Give the served capability roles usable credentials.
#
# Migration 20260730_004_served_login_roles creates `memphant_app_login`,
# `memphant_authn_login`, `memphant_worker_login` and
# `memphant_provisioner_login` as NOINHERIT members of the matching NOLOGIN
# capability role — deliberately WITHOUT passwords, because migrations live in
# git. This script is the provisioning step that makes them usable, and it is
# the only supported way to hand the server a non-superuser credential.
#
# Usage:
#   bash scripts/provision_login_roles.sh <admin_database_url> [role ...]
#
# `admin_database_url` must be the migrator/superuser credential. With no role
# arguments it provisions app, authn and worker (the three served processes);
# name roles explicitly to include `provisioner`.
#
# It prints ready-to-paste environment lines and never echoes a password twice.
# Passwords are generated here and NOT stored: capture the output.
#
# Managed providers that authenticate by IAM rather than password (Neon with
# IAM, RDS IAM) do not need this script — grant the provider's login role
# membership instead:  grant memphant_app to <provider_login>;
set -euo pipefail

ADMIN_URL="${1:?admin (migrator/superuser) database url required}"
shift || true
ROLES=("$@")
[ "${#ROLES[@]}" -gt 0 ] || ROLES=(app authn worker)

env_var_for() {
  case "$1" in
    app)         echo MEMPHANT_APP_DATABASE_URL ;;
    authn)       echo MEMPHANT_AUTHN_DATABASE_URL ;;
    worker)      echo MEMPHANT_WORKER_DATABASE_URL ;;
    provisioner) echo MEMPHANT_PROVISION_DATABASE_URL ;;
    *) echo "unknown capability: $1" >&2; exit 2 ;;
  esac
}

# Strip the credential from the admin URL and splice in the new one.
# ponytail: same plain-URL assumption as with_scratch_db.sh.
login_url() { # login_url <login> <password>
  local host="${ADMIN_URL#*@}"
  local scheme="${ADMIN_URL%%://*}"
  printf '%s://%s:%s@%s' "$scheme" "$1" "$2" "$host"
}

echo "# provisioned $(date -u +%Y-%m-%dT%H:%M:%SZ) — capture these now, they are not stored"
for capability in "${ROLES[@]}"; do
  login="memphant_${capability}_login"
  # MEMPHANT_LOGIN_PASSWORD exists so the Compose bootstrap can converge on a
  # password the server container already knows. LOCAL DEVELOPMENT ONLY — a
  # production bootstrap leaves it unset and captures the generated secrets.
  password="${MEMPHANT_LOGIN_PASSWORD:-$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40)}"
  psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q \
    -c "alter role \"$login\" login noinherit password '$password'"
  # Fail closed rather than hand back a credential that silently bypasses RLS.
  bypass="$(psql "$ADMIN_URL" -tAqc \
    "select rolsuper or rolbypassrls from pg_roles where rolname = '$login'")"
  if [ "$bypass" != "f" ]; then
    echo "refusing: $login is SUPERUSER or BYPASSRLS — it would bypass every tenant policy" >&2
    exit 1
  fi
  printf '%s=%s\n' "$(env_var_for "$capability")" "$(login_url "$login" "$password")"
done
