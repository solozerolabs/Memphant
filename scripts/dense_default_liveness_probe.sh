#!/usr/bin/env bash
# Mechanism-liveness probe for the 2026-08-01 dense-default-on flip.
#
# An inert pass and a neutral pass produce the same number and mean opposite
# things, so this probe runs TWO arms against the SAME scratch database and
# requires them to DISAGREE on every axis:
#
#   default arm  — no MEMPHANT_EMBEDDINGS, no MEMPHANT_LEXICAL_SCORER.
#                  Must write embedding rows, must flag `lexical_scorer:bm25-code`,
#                  must run the `vector` stage, must produce a `vector`
#                  candidate with a non-zero score.
#   control arm  — MEMPHANT_EMBEDDINGS=off, MEMPHANT_LEXICAL_SCORER=overlap.
#                  Must write NO embedding rows, must NOT flag bm25-code, must
#                  trace `vector` as `disabled`, must produce NO vector candidate.
#
# It also times the compile (worker) phase of each arm so the dense ingest cost
# is measured here rather than asserted.
#
# Usage: DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant \
#          bash scripts/dense_default_liveness_probe.sh
# Re-execs itself through with_scratch_db.sh; NEVER touches a shared DB.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATABASE_URL="${DATABASE_URL:-postgres://memphant:memphant@localhost:5432/memphant}"

if [ -z "${MEMPHANT_SCRATCH_ACTIVE:-}" ]; then
  exec env MEMPHANT_SCRATCH_ACTIVE=1 \
    bash "$ROOT/scripts/with_scratch_db.sh" "$DATABASE_URL" DATABASE_URL \
    bash "$ROOT/scripts/$(basename "$0")"
fi

PORT="${MEMPHANT_PROBE_PORT:-39421}"
BASE="http://127.0.0.1:${PORT}"
SERVER="$ROOT/target/debug/memphant-server"
WORKER="$ROOT/target/debug/memphant-worker"
CLI="$ROOT/target/debug/memphant-cli"
SERVER_PID=""
OUT="${MEMPHANT_PROBE_OUT:-$ROOT/target/dense-liveness.json}"

fail() { printf 'LIVENESS PROBE FAILED: %s\n' "$*" >&2; exit 1; }
log()  { printf '\n### %s\n' "$*" >&2; }
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

jget() { python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)"; }

api() { # api KEY METHOD PATH [JSON]
  local key="$1" method="$2" path="$3" body="${4:-}"
  local idem="live-$(uuidgen)"
  if [ -n "$body" ]; then
    curl -s -X "$method" -H "Authorization: Bearer $key" -H "Idempotency-Key: $idem" \
      -H 'content-type: application/json' -d "$body" "$BASE$path"
  else
    curl -s -X "$method" -H "Authorization: Bearer $key" -H "Idempotency-Key: $idem" "$BASE$path"
  fi
}

start_server() { # start_server [env assignments...]
  env -u DATABASE_URL -u MEMPHANT_EMBEDDINGS -u MEMPHANT_LEXICAL_SCORER \
    MEMPHANT_APP_DATABASE_URL="$DATABASE_URL" \
    MEMPHANT_AUTHN_DATABASE_URL="$DATABASE_URL" \
    MEMPHANT_BIND="127.0.0.1:${PORT}" "$@" "$SERVER" >&2 &
  SERVER_PID=$!
  for _ in $(seq 1 240); do
    curl -sf "$BASE/v1/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  fail "server did not become healthy on :$PORT"
}

stop_server() { kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; SERVER_PID=""; }

# A drain that FAILED every job and a drain that had nothing to do both exit 0.
# Parse the worker's own `failed=` count AND ask the database whether the queue
# is empty — a silently partial compile would let this probe report a dense-on
# number against a corpus that never finished ingesting.
worker_drain() { # worker_drain [env assignments...]
  local out
  out=$(env -u DATABASE_URL -u MEMPHANT_EMBEDDINGS -u MEMPHANT_LEXICAL_SCORER \
    MEMPHANT_WORKER_DATABASE_URL="$DATABASE_URL" MEMPHANT_WORKER_DRAIN=1 \
    "$@" "$WORKER" 2>/dev/null)
  printf '%s\n' "$out" >&2
  printf '%s' "$out" | grep -Eq \
    '^memphant-worker: drain completed=[0-9]+( failed=0 retried=[0-9]+ deferred=[0-9]+)?$' \
    || fail "worker drain did not report a clean completion: $out"
  local pending
  pending=$(psql "$DATABASE_URL" -tAc \
    "select count(*) from memphant.job_state where state in ('queued','running')")
  [ "$pending" = "0" ] || fail "worker drain left $pending job(s) queued/running"
}

embedding_rows() { # embedding_rows TENANT
  psql "$DATABASE_URL" -tAc \
    "select count(*) from memphant.embedding where tenant_id = '$1'"
}

compiled_units() { # compiled_units TENANT
  psql "$DATABASE_URL" -tAc \
    "select count(*) from memphant.memory_unit where tenant_id = '$1'"
}

log "build binaries (debug, default features -> fastembed on)"
(cd "$ROOT" && cargo build -q -p memphant-server -p memphant-worker -p memphant-cli) >&2

# Bodies deliberately share vocabulary so BM25's IDF has something to do, and
# the query paraphrases the target instead of quoting it, so the dense channel
# has something to do that a lexical channel cannot.
BODIES=(
  "The staging rollout halts when the canary error budget is exhausted."
  "Invoices are reconciled nightly against the ledger snapshot."
  "The retry backoff doubles on each attempt up to a five minute ceiling."
  "Onboarding assigns every new hire a buddy for the first two weeks."
  "Database migrations run before the application containers are replaced."
  "Alert routing sends paging traffic to the on-call rotation after hours."
)
QUERY="what stops a gradual release when too many requests are failing?"

run_arm() { # run_arm LABEL [env assignments...]
  local label="$1"; shift
  log "arm=$label"
  local tenant key
  tenant=$("$CLI" admin create-tenant --name "live-$label-$RANDOM" --database-url "$DATABASE_URL" \
    | sed -n 's/^tenant_created id=\([^ ]*\).*/\1/p')
  key=$("$CLI" admin create-key --tenant "$tenant" --max-trust trusted_system --database-url "$DATABASE_URL" | tail -1)
  [ -n "$tenant" ] && [ -n "$key" ] || fail "$label: provisioning failed"

  start_server "$@"
  local bind ctx
  bind=$(api "$key" PUT "/v1/context-bindings/live-$label" \
    "{\"subject\":{\"external_ref\":\"subject:live-$label\",\"kind\":\"user\"},\"actor\":{\"external_ref\":\"actor:live-$label\",\"kind\":\"system\"},\"scope\":{\"external_ref\":\"scope:live-$label\",\"kind\":\"user_root\"},\"agent_node\":{\"external_ref\":\"agent:live-$label\"}}")
  ctx="\"subject_id\":\"$(echo "$bind" | jget "['subject_id']")\",\"scope_id\":\"$(echo "$bind" | jget "['scope_id']")\",\"actor_id\":\"$(echo "$bind" | jget "['actor_id']")\",\"agent_node_id\":\"$(echo "$bind" | jget "['agent_node_id']")\",\"subject_generation\":$(echo "$bind" | jget "['subject_generation']")"

  local index=0
  for body in "${BODIES[@]}"; do
    index=$((index + 1))
    api "$key" POST /v1/episodes \
      "{$ctx,\"source_ref\":\"live:$label:$index\",\"observed_at\":\"2026-07-15T00:00:00Z\",\"payload\":{\"episode\":{\"source_kind\":\"user\",\"body\":\"$body\"}}}" >/dev/null
  done

  # Ingest cost: the worker compile phase is where embeddings are produced.
  local started finished compile_ms
  started=$(python3 -c 'import time;print(time.time())')
  worker_drain "$@"
  finished=$(python3 -c 'import time;print(time.time())')
  compile_ms=$(python3 -c "print(round(($finished-$started)*1000))")

  local rows units recall trace_id trace
  rows=$(embedding_rows "$tenant")
  units=$(compiled_units "$tenant")
  [ "$units" = "${#BODIES[@]}" ] || fail "$label: compiled $units unit(s), expected ${#BODIES[@]} — partial ingest"
  recall=$(api "$key" POST /v1/recall "{$ctx,\"query\":\"$QUERY\"}")
  trace_id=$(echo "$recall" | jget "['trace_id']")
  trace=$(api "$key" GET "/v1/traces/$trace_id?subject_id=$(echo "$bind" | jget "['subject_id']")&subject_generation=$(echo "$bind" | jget "['subject_generation']")&scope_id=$(echo "$bind" | jget "['scope_id']")&actor_id=$(echo "$bind" | jget "['actor_id']")&agent_node_id=$(echo "$bind" | jget "['agent_node_id']")")
  stop_server

  ARM_JSON=$(python3 - "$label" "$rows" "$compile_ms" "$units" <<PY
import json, sys
label, rows, compile_ms, units = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
trace = json.loads('''$trace''')
recall = json.loads('''$recall''')
candidates = trace.get("candidates") or []
vector = [c for c in candidates if c.get("channel") == "vector"]
stages = {s["stage"]: s.get("detail") for s in (trace.get("channel_runs") or [])}
print(json.dumps({
    "arm": label,
    "embedding_rows": rows,
    "compiled_units": units,
    "worker_compile_ms": compile_ms,
    "feature_flags": trace.get("feature_flags") or [],
    "bm25_code_flagged": "lexical_scorer:bm25-code" in (trace.get("feature_flags") or []),
    "vector_stage_detail": stages.get("vector"),
    "vector_candidates": len(vector),
    "max_vector_score": max([c.get("channel_score") or 0.0 for c in vector], default=0.0),
    "top_item_body": (recall.get("items") or [{}])[0].get("body"),
    "items": len(recall.get("items") or []),
}))
PY
)
  echo "$ARM_JSON"
}

DEFAULT_ARM=$(run_arm default)
CONTROL_ARM=$(run_arm control MEMPHANT_EMBEDDINGS=off MEMPHANT_LEXICAL_SCORER=overlap)

python3 - "$OUT" <<PY
import json, sys
default = json.loads('''$DEFAULT_ARM''')
control = json.loads('''$CONTROL_ARM''')
problems = []
if default["embedding_rows"] <= 0:
    problems.append("default arm wrote no embedding rows — dense is INERT")
if control["embedding_rows"] != 0:
    problems.append("control arm wrote embedding rows — the off switch does not work")
if not default["bm25_code_flagged"]:
    problems.append("default arm did not flag lexical_scorer:bm25-code")
if control["bm25_code_flagged"]:
    problems.append("control arm flagged bm25-code")
if default["vector_stage_detail"] == "disabled":
    problems.append("default arm traced the vector stage as disabled")
if control["vector_stage_detail"] != "disabled":
    problems.append("control arm did not trace the vector stage as disabled")
if default["vector_candidates"] <= 0 or default["max_vector_score"] <= 0.0:
    problems.append("default arm produced no scoring vector candidate — dense did not contribute")
if control["vector_candidates"] != 0:
    problems.append("control arm produced vector candidates")
report = {"default": default, "control": control, "problems": problems,
          "ingest_cost_ratio_default_over_control":
              round(default["worker_compile_ms"] / max(control["worker_compile_ms"], 1), 3)}
open(sys.argv[1], "w").write(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
if problems:
    sys.exit(1)
PY

log "LIVENESS PROVEN — default arm is dense+bm25-code, control arm is inert; report at $OUT"
