#!/usr/bin/env bash
# End-to-end durability/auth/tri-domain probe (plan Task 12).
#
# Proves, against a real Postgres and the real binaries:
#   retain -> worker compiles -> recall -> restart -> recall persists,
#   cross-tenant trace denial, correct, forget (no resurrection), mark,
#   resource (code) ingest with revision identity, health reporting.
#
# Usage: DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant \
#          bash scripts/e2e_probe.sh
# Exits non-zero on the first failed assertion, printing the transcript.
#
# DATABASE_URL is the *base* campaign server; the probe runs against an
# ephemeral scratch database minted from it (created, migrated, and dropped
# here), NEVER the shared `memphant` DB directly. That isolation is what makes
# the probe immune to foreign job_state debris: the worker's global claim is
# oldest-first across all tenants, so debris from the contract tests or a
# killed bench in a shared DB would starve the probe's fresh job on a single
# tick. An ephemeral DB has no foreign rows, so it cannot be starved.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATABASE_URL="${DATABASE_URL:-postgres://memphant:memphant@localhost:5432/memphant}"

# Re-exec once through the scratch-DB helper (which points DATABASE_URL at a
# fresh migrated DB and drops it on exit). MEMPHANT_SCRATCH_ACTIVE guards the
# recursion; set it to run the probe against DATABASE_URL as-is (e.g. an
# already-isolated DB).
if [ -z "${MEMPHANT_SCRATCH_ACTIVE:-}" ]; then
  exec env MEMPHANT_SCRATCH_ACTIVE=1 \
    bash "$ROOT/scripts/with_scratch_db.sh" "$DATABASE_URL" DATABASE_URL \
    bash "$ROOT/scripts/$(basename "$0")"
fi
PORT="${MEMPHANT_PROBE_PORT:-39411}"
BASE="http://127.0.0.1:${PORT}"
SERVER="$ROOT/target/debug/memphant-server"
WORKER="$ROOT/target/debug/memphant-worker"
CLI="$ROOT/target/debug/memphant-cli"
MCP="$ROOT/target/debug/memphant-mcp"
SERVER_PID=""

log()  { printf '\n### %s\n' "$*"; }
fail() { printf 'PROBE FAILED: %s\n' "$*" >&2; exit 1; }
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

jget() { python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)"; }

start_server() {
  env -u DATABASE_URL MEMPHANT_APP_DATABASE_URL="$DATABASE_URL" MEMPHANT_AUTHN_DATABASE_URL="$DATABASE_URL" MEMPHANT_BIND="127.0.0.1:${PORT}" "$SERVER" &
  SERVER_PID=$!
  # 60s window: first boot loads embedding weights and a loaded machine
  # (parallel cargo builds) can push startup past the old 10s budget.
  for _ in $(seq 1 120); do
    curl -sf "$BASE/v1/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  fail "server did not become healthy on :$PORT"
}

worker_once() { env -u DATABASE_URL MEMPHANT_WORKER_DATABASE_URL="$DATABASE_URL" MEMPHANT_WORKER_ONCE=1 "$WORKER" >/dev/null; }

api() { # api KEY METHOD PATH [JSON]
  local key="$1" method="$2" path="$3" body="${4:-}"
  # Every mutating verb requires a unique Idempotency-Key; it is ignored by the
  # read verbs, so send it unconditionally. Derived per-call via uuidgen:
  # `api` runs inside $(...) subshells, so a shared IDEM_SEQ counter never
  # increments in the parent and every call would silently reuse key #1.
  local idem="probe-$(uuidgen)"
  if [ -n "$body" ]; then
    curl -s -X "$method" -H "Authorization: Bearer $key" -H "Idempotency-Key: $idem" \
      -H 'content-type: application/json' -d "$body" "$BASE$path"
  else
    curl -s -X "$method" -H "Authorization: Bearer $key" -H "Idempotency-Key: $idem" "$BASE$path"
  fi
}

# Bind a tenant's context (subject/actor/scope/agent-node) and echo the binding
# JSON. All verbs resolve their memory context from these server-assigned ids.
bind_context() { # bind_context KEY REF
  api "$1" PUT "/v1/context-bindings/$2" \
    "{\"subject\":{\"external_ref\":\"subject:$2\",\"kind\":\"user\"},\"actor\":{\"external_ref\":\"actor:$2\",\"kind\":\"system\"},\"scope\":{\"external_ref\":\"scope:$2\",\"kind\":\"user_root\"},\"agent_node\":{\"external_ref\":\"agent:$2\"}}"
}
api_status() { # like api, but prints only the HTTP status
  local key="$1" method="$2" path="$3"
  curl -s -o /dev/null -w '%{http_code}' -X "$method" -H "Authorization: Bearer $key" "$BASE$path"
}

log "build binaries (debug)"
(cd "$ROOT" && cargo build -q -p memphant-server -p memphant-worker -p memphant-cli -p memphant-mcp)

log "apply migrations (idempotent)"
python3 "$ROOT/scripts/apply_memphant_migrations.py" --database-url "$DATABASE_URL" | tail -1

log "provision tenants + keys via admin CLI"
TENANT_A=$("$CLI" admin create-tenant --name "probe-a-$RANDOM" --database-url "$DATABASE_URL" | sed -n 's/^tenant_created id=\([^ ]*\).*/\1/p')
TENANT_B=$("$CLI" admin create-tenant --name "probe-b-$RANDOM" --database-url "$DATABASE_URL" | sed -n 's/^tenant_created id=\([^ ]*\).*/\1/p')
KEY_A=$("$CLI" admin create-key --tenant "$TENANT_A" --max-trust trusted_system --database-url "$DATABASE_URL" | tail -1)
KEY_B=$("$CLI" admin create-key --tenant "$TENANT_B" --max-trust trusted_system --database-url "$DATABASE_URL" | tail -1)
[ -n "$TENANT_A" ] && [ -n "$KEY_A" ] && [ -n "$KEY_B" ] || fail "provisioning failed"
echo "tenant_a=$TENANT_A tenant_b=$TENANT_B"

start_server
log "health reports postgres"
api "$KEY_A" GET /v1/health | tee /dev/stderr | grep -q '"store":"postgres"' || fail "health lacks store=postgres"

log "bind context for both tenants"
BIND_A=$(bind_context "$KEY_A" "probe-a")
SUBJ_A=$(echo "$BIND_A" | jget "['subject_id']") || fail "context binding A failed: $BIND_A"
SCOPE_A=$(echo "$BIND_A" | jget "['scope_id']")
ACTOR_A=$(echo "$BIND_A" | jget "['actor_id']")
AGENT_A=$(echo "$BIND_A" | jget "['agent_node_id']")
GEN_A=$(echo "$BIND_A" | jget "['subject_generation']")
CTX_A="\"subject_id\":\"$SUBJ_A\",\"scope_id\":\"$SCOPE_A\",\"actor_id\":\"$ACTOR_A\",\"agent_node_id\":\"$AGENT_A\",\"subject_generation\":$GEN_A"
QS_A="subject_id=$SUBJ_A&subject_generation=$GEN_A&scope_id=$SCOPE_A&actor_id=$ACTOR_A&agent_node_id=$AGENT_A"
MCP_KEY=$(
  "$CLI" admin create-key --tenant "$TENANT_A" --max-trust trusted_system \
    --subject-id "$SUBJ_A" --subject-generation "$GEN_A" --scope "$SCOPE_A" \
    --actor "$ACTOR_A" --agent-node "$AGENT_A" --database-url "$DATABASE_URL" | tail -1
)
[ -n "$MCP_KEY" ] || fail "scoped MCP key provisioning failed"

BIND_B=$(bind_context "$KEY_B" "probe-b")
SUBJ_B=$(echo "$BIND_B" | jget "['subject_id']") || fail "context binding B failed: $BIND_B"
QS_B="subject_id=$SUBJ_B&subject_generation=$(echo "$BIND_B" | jget "['subject_generation']")&scope_id=$(echo "$BIND_B" | jget "['scope_id']")&actor_id=$(echo "$BIND_B" | jget "['actor_id']")&agent_node_id=$(echo "$BIND_B" | jget "['agent_node_id']")"

log "retain episode (A)"
RETAIN=$(api "$KEY_A" POST /v1/episodes "{$CTX_A,\"source_ref\":\"probe:episode:1\",\"observed_at\":\"2026-07-15T00:00:00Z\",\"payload\":{\"episode\":{\"source_kind\":\"user\",\"body\":\"Release region is Taipei.\"}}}")
EPISODE_ID=$(echo "$RETAIN" | jget "['episode_id']")
[ -n "$EPISODE_ID" ] || fail "retain returned no episode_id: $RETAIN"

log "read-your-own-writes: recall before worker runs -> degraded hit"
RECALL0=$(api "$KEY_A" POST /v1/recall "{$CTX_A,\"query\":\"Where is the release region?\"}")
echo "$RECALL0" | jget "['degraded']" | grep -qi true || fail "expected degraded read-your-own-writes: $RECALL0"

log "worker tick compiles"
worker_once
RECALL1=$(api "$KEY_A" POST /v1/recall "{$CTX_A,\"query\":\"Where is the release region?\"}")
echo "$RECALL1" | jget "['items'][0]['body']" | grep -q "Taipei" || fail "recall missed compiled unit: $RECALL1"
echo "$RECALL1" | jget "['degraded']" | grep -qi false || fail "recall still degraded after compile"
TRACE_ID=$(echo "$RECALL1" | jget "['trace_id']")
UNIT_ID=$(echo "$RECALL1" | jget "['items'][0]['unit_id']")

log "retain code resource (A) with commit revision"
RES=$(api "$KEY_A" POST /v1/episodes "{$CTX_A,\"source_ref\":\"probe:resource:1\",\"observed_at\":\"2026-07-15T00:00:00Z\",\"payload\":{\"resource\":{\"uri\":\"repo://demo/src/main.rs\",\"mime_type\":\"text/x-rust\",\"content_hash\":\"sha256:probe\",\"kind\":\"code\",\"revision\":\"abc123def\",\"body\":\"fn deploy() { /* canary first, then roll forward */ }\"}}}")
echo "$RES" | jget "['enqueued'][0]" | grep -q reflect_resource || fail "resource retain not enqueued: $RES"
worker_once
RECALL_RES=$(api "$KEY_A" POST /v1/recall "{$CTX_A,\"query\":\"canary deploy roll forward\"}")
echo "$RECALL_RES" | python3 -c "import json,sys;d=json.load(sys.stdin);assert any(i['kind']=='resource' for i in d['items']),d" || fail "resource-derived unit not recalled"

log "real MCP stdio binary lists and reads the bound canonical projection"
api "$KEY_A" POST /v1/episodes "{$CTX_A,\"source_ref\":\"probe:mcp:unit\",\"observed_at\":\"2026-07-15T00:00:00Z\",\"payload\":{\"unit\":{\"kind\":\"semantic\",\"fact_key\":\"mcp-probe\",\"predicate\":\"memory_file\",\"body\":\"Real MCP resource body\",\"confidence\":1.0}}}" >/dev/null
env -u DATABASE_URL \
  MEMPHANT_APP_DATABASE_URL="$DATABASE_URL" \
  MEMPHANT_AUTHN_DATABASE_URL="$DATABASE_URL" \
  MEMPHANT_API_KEY="$MCP_KEY" \
  MEMPHANT_MCP_PROBE_BINARY="$MCP" \
  python3 - <<'PY'
import atexit
import json
import os
import select
import subprocess

process = subprocess.Popen(
    [os.environ["MEMPHANT_MCP_PROBE_BINARY"], "stdio"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)

def stop_process():
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

atexit.register(stop_process)

def send(message):
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()

def receive(request_id):
    while True:
        ready, _, _ = select.select([process.stdout], [], [], 20)
        if not ready:
            process.terminate()
            raise AssertionError(f"MCP response {request_id} timed out")
        line = process.stdout.readline()
        if not line:
            error = process.stderr.read()
            raise AssertionError(f"MCP exited before response {request_id}: {error}")
        response = json.loads(line)
        if response.get("id") == request_id:
            assert "error" not in response, response
            return response["result"]

send({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "memphant-b3-probe", "version": "1"},
    },
})
initialized = receive(1)
assert "resources" in initialized["capabilities"], initialized
assert "tools" in initialized["capabilities"], initialized
send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
send({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
first = receive(2)
assert first["resources"], first
send({"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
second = receive(3)
assert [item["uri"] for item in first["resources"]] == [
    item["uri"] for item in second["resources"]
]
target = next(
    item for item in first["resources"]
    if item.get("name") == "mcp-probe" or item.get("title") == "mcp-probe"
)
uri = target["uri"]
send({
    "jsonrpc": "2.0",
    "id": 4,
    "method": "resources/read",
    "params": {"uri": uri},
})
read = receive(4)
assert read["contents"][0]["uri"] == uri, read
assert read["contents"][0]["text"] == "Real MCP resource body", read
process.stdin.close()
process.wait(timeout=10)
assert process.returncode == 0, process.stderr.read()
print(f"MCP PROBE: resources={len(first['resources'])} read=ok deterministic=ok")
PY

log "cross-tenant: B fetching A's trace must 404"
STATUS_B=$(api_status "$KEY_B" GET "/v1/traces/$TRACE_ID?$QS_B")
[ "$STATUS_B" = "404" ] || fail "tenant B got $STATUS_B for tenant A's trace (must be 404)"
STATUS_A=$(api_status "$KEY_A" GET "/v1/traces/$TRACE_ID?$QS_A")
[ "$STATUS_A" = "200" ] || fail "tenant A cannot read own trace ($STATUS_A)"

# Cross-tenant EPISODIC read isolation (app + tenant-GUC layer). NOTE: this
# proves the application + `current_tenant_id()` GUC filter, NOT the Postgres
# RLS backstop — the server connects as the scratch-DB superuser login
# (rolbypassrls=true), so FORCE RLS never fires here. The RLS swap itself is
# proven under the real `memphant_app` role by
# `crates/memphant-store-postgres/tests/episodic_rls_leakage.rs`.
log "cross-tenant: B's episode is invisible to A's recall (app+GUC layer)"
CTX_B="\"subject_id\":\"$SUBJ_B\",\"scope_id\":\"$(echo "$BIND_B" | jget "['scope_id']")\",\"actor_id\":\"$(echo "$BIND_B" | jget "['actor_id']")\",\"agent_node_id\":\"$(echo "$BIND_B" | jget "['agent_node_id']")\",\"subject_generation\":$(echo "$BIND_B" | jget "['subject_generation']")"
api "$KEY_B" POST /v1/episodes "{$CTX_B,\"source_ref\":\"probe:episode:b\",\"observed_at\":\"2026-07-15T00:00:00Z\",\"payload\":{\"episode\":{\"source_kind\":\"user\",\"body\":\"Tenant B private secret is Zurich.\"}}}" >/dev/null
worker_once
RECALL_B=$(api "$KEY_B" POST /v1/recall "{$CTX_B,\"query\":\"Where is the private secret?\"}")
echo "$RECALL_B" | jget "['items'][0]['body']" | grep -q "Zurich" || fail "tenant B cannot recall own episode: $RECALL_B"
RECALL_A_XT=$(api "$KEY_A" POST /v1/recall "{$CTX_A,\"query\":\"Where is the private secret Zurich?\"}")
echo "$RECALL_A_XT" | python3 -c "import json,sys;d=json.load(sys.stdin);assert not any('Zurich' in i['body'] for i in d['items']),d" || fail "tenant A recalled tenant B's private episode (cross-tenant leak): $RECALL_A_XT"

log "restart durability"
kill "$SERVER_PID"; wait "$SERVER_PID" 2>/dev/null || true; SERVER_PID=""
start_server
RECALL2=$(api "$KEY_A" POST /v1/recall "{$CTX_A,\"query\":\"Where is the release region?\"}")
echo "$RECALL2" | jget "['items'][0]['body']" | grep -q "Taipei" || fail "memory lost across restart: $RECALL2"
[ "$(api_status "$KEY_A" GET "/v1/traces/$TRACE_ID?$QS_A")" = "200" ] || fail "trace lost across restart"

log "correct supersedes"
api "$KEY_A" POST /v1/correct "{$CTX_A,\"selector\":{\"memory_unit_id\":\"$UNIT_ID\"},\"correction\":{\"value\":\"Release region is Osaka.\",\"reason\":\"probe correction\",\"source_ref\":\"probe:correction\",\"observed_at\":\"2026-07-15T00:00:00Z\"}}" >/dev/null
RECALL3=$(api "$KEY_A" POST /v1/recall "{$CTX_A,\"query\":\"Where is the release region?\"}")
echo "$RECALL3" | jget "['items'][0]['body']" | grep -q "Osaka" || fail "correction not reflected: $RECALL3"

log "forget episode + no resurrection"
FORGET=$(api "$KEY_A" POST /v1/forget "{$CTX_A,\"selector\":{\"episode_id\":\"$EPISODE_ID\",\"scope_id\":\"$SCOPE_A\"},\"reason\":\"probe forget\"}")
echo "$FORGET" | jget "['verification']" | grep -q "authorized_transaction_committed" || fail "forget verification not clean: $FORGET"
api "$KEY_A" POST /v1/reflect "{$CTX_A}" >/dev/null
worker_once
RECALL4=$(api "$KEY_A" POST /v1/recall "{$CTX_A,\"query\":\"release region Taipei Osaka\"}")
echo "$RECALL4" | python3 -c "import json,sys;d=json.load(sys.stdin);assert not any('egion is' in i['body'] for i in d['items']),d" || fail "forgotten memory resurfaced: $RECALL4"

log "mark outcome feedback"
MARK=$(api "$KEY_A" POST /v1/mark "{$CTX_A,\"trace_id\":\"$TRACE_ID\",\"caller_id\":\"e2e-probe\",\"used_ids\":[],\"outcome\":\"success\"}")
echo "$MARK" | jget "['accepted']" | grep -qi true || fail "mark rejected: $MARK"

log "unauthenticated request is refused"
STATUS_NOKEY=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' -d '{}' "$BASE/v1/recall")
[ "$STATUS_NOKEY" = "401" ] || fail "missing key got $STATUS_NOKEY (must be 401)"

echo
echo "E2E PROBE: ALL CHECKS PASSED"
