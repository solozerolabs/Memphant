#!/usr/bin/env bash
# MemPhant-POWERED decision loop, end to end against the real binaries:
#   extract-shaped DECISION -> retain -> worker compiles -> recall on a future
#   task -> render the repo_profile turn-1 block from what MemPhant returned.
#
# This is the middle the adherence bench stubbed out: proves the injected block
# is produced BY MemPhant recall, not a literal string. Uses the same
# scratch-DB + non-superuser-role + server/worker setup as e2e_probe.sh.
#
# Usage: DATABASE_URL=postgres://memphant:memphant@localhost:5544/memphant \
#          bash scripts/decision_loop_demo.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATABASE_URL="${DATABASE_URL:-postgres://memphant:memphant@localhost:5544/memphant}"
if [ -z "${MEMPHANT_SCRATCH_ACTIVE:-}" ]; then
  exec env MEMPHANT_SCRATCH_ACTIVE=1 bash "$ROOT/scripts/with_scratch_db.sh" \
    "$DATABASE_URL" DATABASE_URL bash "$ROOT/scripts/$(basename "$0")"
fi
PORT="${MEMPHANT_DEMO_PORT:-39412}"; BASE="http://127.0.0.1:${PORT}"
SERVER="$ROOT/target/debug/memphant-server"; WORKER="$ROOT/target/debug/memphant-worker"; CLI="$ROOT/target/debug/memphant-cli"
SERVER_PID=""; PROBE_LOGINS=""
cleanup(){ [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null||true; for l in $PROBE_LOGINS; do psql "$DATABASE_URL" -qc "drop role if exists \"$l\"" >/dev/null 2>&1||true; done; }
trap cleanup EXIT
jget(){ python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)"; }
login_url(){ printf '%s://%s:%s@%s' "${DATABASE_URL%%://*}" "$1" "$2" "${DATABASE_URL#*@}"; }
api(){ local key="$1" method="$2" path="$3" body="${4:-}"; local idem="demo-$(uuidgen)"
  if [ -n "$body" ]; then curl -s -X "$method" -H "Authorization: Bearer $key" -H "Idempotency-Key: $idem" -H 'content-type: application/json' -d "$body" "$BASE$path"
  else curl -s -X "$method" -H "Authorization: Bearer $key" -H "Idempotency-Key: $idem" "$BASE$path"; fi; }

echo "### apply migrations"; python3 "$ROOT/scripts/apply_memphant_migrations.py" --database-url "$DATABASE_URL" | tail -1
echo "### mint non-superuser roles"; PW="demo$(uuidgen|tr -dc 'A-Za-z0-9')"
for pair in app:memphant_app authn:memphant_authn worker:memphant_worker; do
  cap="${pair#*:}"; login="mp_demo_${pair%%:*}_$$"; PROBE_LOGINS="$PROBE_LOGINS $login"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "create role \"$login\" login noinherit password '$PW'" -c "grant $cap to \"$login\"" -c "revoke all on schema memphant from \"$login\""
done
APP_URL=$(login_url "mp_demo_app_$$" "$PW"); AUTHN_URL=$(login_url "mp_demo_authn_$$" "$PW"); WORKER_URL=$(login_url "mp_demo_worker_$$" "$PW")
echo "### start server"
env -u DATABASE_URL MEMPHANT_APP_DATABASE_URL="$APP_URL" MEMPHANT_AUTHN_DATABASE_URL="$AUTHN_URL" MEMPHANT_BIND="127.0.0.1:${PORT}" "$SERVER" & SERVER_PID=$!
for _ in $(seq 1 120); do curl -sf "$BASE/v1/health" >/dev/null 2>&1 && break; sleep 0.5; done

TENANT=$("$CLI" admin create-tenant --name "demo-$RANDOM" --database-url "$DATABASE_URL" | sed -n 's/^tenant_created id=\([^ ]*\).*/\1/p')
KEY=$("$CLI" admin create-key --tenant "$TENANT" --max-trust trusted_system --database-url "$DATABASE_URL" | tail -1)
BIND=$(api "$KEY" PUT "/v1/context-bindings/repoX" "{\"subject\":{\"external_ref\":\"user:sid\",\"kind\":\"user\"},\"actor\":{\"external_ref\":\"actor:sid\",\"kind\":\"system\"},\"scope\":{\"external_ref\":\"repo:toobai\",\"kind\":\"user_root\"},\"agent_node\":{\"external_ref\":\"agent:coding\"}}")
echo "  KEY=${KEY:0:12}...  BIND=$BIND"
CTX="\"subject_id\":\"$(echo "$BIND"|jget "['subject_id']")\",\"actor_id\":\"$(echo "$BIND"|jget "['actor_id']")\",\"scope_id\":\"$(echo "$BIND"|jget "['scope_id']")\",\"agent_node_id\":\"$(echo "$BIND"|jget "['agent_node_id']")\",\"subject_generation\":$(echo "$BIND"|jget "['subject_generation']")"

# A real extracted decision (from scripts/extract_decisions.py on a live transcript).
DECISION="Maestro smoke test appId must be the real bundle id from ios/Runner.xcodeproj/project.pbxproj (com.example.toobaiProjectWebApp), NOT a placeholder like com.recme.app — a wrong appId means the smoke test cannot launch the app."
echo "### retain the DECISION as an episode"
R=$(api "$KEY" POST /v1/episodes "{$CTX,\"source_ref\":\"coding-decision:1\",\"observed_at\":\"2026-08-01T00:00:00Z\",\"payload\":{\"episode\":{\"source_kind\":\"user\",\"body\":\"$DECISION\"}}}")
echo "  RETAIN RESP=$R"
echo "### worker compiles"; env -u DATABASE_URL MEMPHANT_WORKER_DATABASE_URL="$WORKER_URL" MEMPHANT_WORKER_ONCE=1 "$WORKER" >/dev/null

echo "### FUTURE RUN recalls on a related task query (decision NOT in the query)"
QUERY="I'm adding a Maestro smoke test flow for this app. What appId should it launch?"
RECALL=$(api "$KEY" POST /v1/recall "{$CTX,\"query\":\"$QUERY\"}")
BODY=$(echo "$RECALL" | jget "['items'][0]['body']" 2>/dev/null || echo "")
echo "  recalled: $BODY"
echo "$BODY" | grep -q "project.pbxproj" || { echo "FAIL: decision not recalled from MemPhant"; exit 1; }

echo ""
echo "### repo_profile turn-1 block, rendered FROM MemPhant recall:"
echo "----------------------------------------------------------------"
echo "## Repo Runtime Profile (learned from prior runs)"
echo "- $BODY"
echo "----------------------------------------------------------------"
echo "PASS: MemPhant-powered decision loop (retain -> compile -> recall -> render) green."
