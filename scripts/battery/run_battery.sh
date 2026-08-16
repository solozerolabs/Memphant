#!/usr/bin/env bash
# Local worktree A/B run battery: same yurivan coding task through Codex twice —
# arm `bare` (no memory) vs arm `memphant` (injection + capture hooks) — scored
# by yurivan's own gate (the terminal, fully-observed outcome). Memory accrues
# across rounds via capture; a later round measures whether captured conventions
# let the memphant arm pass the gate where the bare arm can't.
#
# NOT a passive read of organic traffic (Syndai has none) — we MANUFACTURE the
# runs, so N = matched pairs we drive, and every pair is difficulty-controlled.
#
# ponytail: isolated throwaway DB (memphant_battery), superuser for both served
# roles — RLS bypass is irrelevant for a single-subject measurement harness; if
# this ever measures cross-tenant leakage, mint non-superuser roles like dogfood.
#
# Subcommands:
#   bootstrap   isolated DB + migrate + bind subject + key + start 2 servers + wire both arms
#   seed "<f>"  POST one non-repo convention memory (smoke/warm-start only; real memory = capture)
#   task <id> <arm>   one run: worktree → codex → gate → result json
#   round <n>   run every task in tasks.txt through BOTH arms
#   read        paired McNemar + capture→recall linkage
#   smoke       bootstrap + seed + one memphant task; assert injection fired + capture wrote + gate ran
#   teardown    stop servers, drop DB, prune worktrees
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE="$ROOT/scripts/battery/.state"
ENVF="$STATE/env.sh"
TASKS="$ROOT/scripts/battery/tasks.txt"
YURIVAN="${YURIVAN_REPO:-/Users/sidsharma/yurivan}"
ADMIN="${ADMIN_DATABASE_URL:-postgres://memphant:memphant@localhost:5432/postgres}"
DB="memphant_battery"
REST_PORT="${REST_PORT:-8091}"
MCP_PORT="${MCP_PORT:-8092}"
CODEX_MODEL="${CODEX_MODEL:-gpt-5.6-terra}"
# Capture summarizer (measured winner: gemini-3.1-flash-lite via OpenRouter + fallback
# chain — see summarize.py). Needs OPENROUTER_API_KEY in env (run the battery under
# `doppler run -p syndai -c dev --`). Override MEMPHANT_CAPTURE_SUMMARIZER_CMD to swap.
SUMMARIZER="${MEMPHANT_CAPTURE_SUMMARIZER_CMD:-bash $ROOT/scripts/battery/summarize.sh}"

SERVER="$ROOT/target/debug/memphant-server"
CLI="$ROOT/target/debug/memphant-cli"
MCP="$ROOT/target/debug/memphant-mcp"
mkdir -p "$STATE"
say(){ printf '\n### %s\n' "$*"; }
fail(){ printf 'BATTERY FAIL: %s\n' "$*" >&2; exit 1; }
adminurl(){ local h="${ADMIN#*@}"; h="${h%%/*}"; echo "${ADMIN%%://*}://${ADMIN#*://}" | sed "s#@$h/.*#@$h/$DB#"; }
ADMIN_URL="$(adminurl)"
jget(){ python3 -c "import json,sys;print(json.load(sys.stdin)$1)"; }

bootstrap(){
  say "build binaries"; (cd "$ROOT" && cargo build -q -p memphant-server -p memphant-cli -p memphant-mcp)
  say "isolated DB $DB"
  psql "$ADMIN" -tAc "select 1 from pg_database where datname='$DB'" | grep -q 1 \
    || psql "$ADMIN" -q -c "create database $DB"
  python3 "$ROOT/scripts/apply_memphant_migrations.py" --database-url "$ADMIN_URL" | tail -1
  say "tenant + bootstrap key"
  local tenant bootkey
  tenant=$("$CLI" admin create-tenant --name "battery-$RANDOM" --database-url "$ADMIN_URL" | sed -n 's/^tenant_created id=\([^ ]*\).*/\1/p')
  [ -n "$tenant" ] || fail "create-tenant"
  bootkey=$("$CLI" admin create-key --tenant "$tenant" --max-trust trusted_system --database-url "$ADMIN_URL" | tail -1)
  say "start REST server :$REST_PORT (superuser both roles)"
  env -u DATABASE_URL MEMPHANT_APP_DATABASE_URL="$ADMIN_URL" MEMPHANT_AUTHN_DATABASE_URL="$ADMIN_URL" \
    MEMPHANT_BIND="127.0.0.1:$REST_PORT" "$SERVER" >"$STATE/server.log" 2>&1 & echo $! >"$STATE/server.pid"
  local base="http://127.0.0.1:$REST_PORT"
  for _ in $(seq 1 120); do curl -sf "$base/v1/health" >/dev/null 2>&1 && break; sleep 0.5; done
  curl -sf "$base/v1/health" >/dev/null 2>&1 || fail "REST server not healthy (see $STATE/server.log)"
  say "bind coding context (captures ALL identity ids the capture hook needs)"
  local bind
  bind=$(curl -s -X PUT -H "Authorization: Bearer $bootkey" -H "Idempotency-Key: battery-$(uuidgen)" \
    -H 'content-type: application/json' \
    -d '{"subject":{"external_ref":"subject:yurivan-battery","kind":"user"},"actor":{"external_ref":"actor:yurivan-battery","kind":"system"},"scope":{"external_ref":"scope:yurivan-battery","kind":"user_root"},"agent_node":{"external_ref":"agent:yurivan-battery"}}' \
    "$base/v1/context-bindings/yurivan-battery")
  local subj scope actor agent gen key
  subj=$(echo "$bind" | jget "['subject_id']") || fail "bind failed: $bind"
  scope=$(echo "$bind" | jget "['scope_id']"); actor=$(echo "$bind" | jget "['actor_id']")
  agent=$(echo "$bind" | jget "['agent_node_id']"); gen=$(echo "$bind" | jget "['subject_generation']")
  key=$("$CLI" admin create-key --tenant "$tenant" --max-trust trusted_system \
    --subject-id "$subj" --subject-generation "$gen" --scope "$scope" --actor "$actor" \
    --agent-node "$agent" --database-url "$ADMIN_URL" | tail -1)
  say "start MCP streamable-http :$MCP_PORT (binds this coding key's tenant at startup)"
  env -u DATABASE_URL MEMPHANT_APP_DATABASE_URL="$ADMIN_URL" MEMPHANT_AUTHN_DATABASE_URL="$ADMIN_URL" \
    MEMPHANT_API_KEY="$key" MEMPHANT_MCP_BIND="127.0.0.1:$MCP_PORT" "$MCP" streamable-http \
    >"$STATE/mcp.log" 2>&1 & echo $! >"$STATE/mcp.pid"
  for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$MCP_PORT/mcp" && break; sleep 0.25; done
  say "start reflect worker (drains episode→unit jobs so captured memory becomes recallable)"
  env -u DATABASE_URL MEMPHANT_WORKER_DATABASE_URL="$ADMIN_URL" \
    MEMPHANT_APP_DATABASE_URL="$ADMIN_URL" MEMPHANT_AUTHN_DATABASE_URL="$ADMIN_URL" \
    "$ROOT/target/debug/memphant-worker" >"$STATE/worker.log" 2>&1 & echo $! >"$STATE/worker.pid"
  cat >"$ENVF" <<EOF
export BATTERY_DB='$ADMIN_URL'
export BATTERY_REST='$base'
export MEMPHANT_API_KEY='$key'
export MEMPHANT_MCP_URL='http://127.0.0.1:$MCP_PORT/mcp'
export MEMPHANT_CAPTURE_URL='$base/v1/episodes'
export MEMPHANT_SUBJECT_ID='$subj'
export MEMPHANT_SCOPE_ID='$scope'
export MEMPHANT_ACTOR_ID='$actor'
export MEMPHANT_AGENT_NODE_ID='$agent'
export MEMPHANT_SUBJECT_GENERATION='$gen'
EOF
  wire_arm bare; wire_arm memphant
  say "READY. env=$ENVF  subject=$subj"
}

# Two isolated CODEX_HOMEs. bare = auth only. memphant = auth + MCP server + injection/capture hooks.
wire_arm(){
  local arm="$1" home="$STATE/codex-home-$1"
  rm -rf "$home"; mkdir -p "$home"
  cp ~/.codex/auth.json "$home/auth.json" 2>/dev/null || fail "no ~/.codex/auth.json"
  if [ "$arm" = "bare" ]; then
    : >"$home/config.toml"   # nothing: no memphant MCP, no hooks
    return
  fi
  # shellcheck disable=SC1090
  . "$ENVF"
  cat >"$home/config.toml" <<EOF
[mcp_servers.memphant]
command = "$MCP"
args = ["stdio"]
env_vars = ["MEMPHANT_API_KEY", "MEMPHANT_APP_DATABASE_URL", "MEMPHANT_AUTHN_DATABASE_URL"]
EOF
  cat >"$home/hooks.json" <<EOF
{ "hooks": {
  "UserPromptSubmit": [ { "hooks": [ { "type": "command",
    "command": "python3 \"$ROOT/plugins/codex-memphant/hooks/user_prompt_submit.py\"", "timeout": 8 } ] } ],
  "Stop": [ { "hooks": [ { "type": "command",
    "command": "python3 \"$ROOT/plugins/codex-memphant/hooks/session_capture.py\"", "timeout": 20 } ] } ]
} }
EOF
}

# Env every codex run in the memphant arm needs (hooks read these).
memphant_env(){
  # shellcheck disable=SC1090
  . "$ENVF"
  export MEMPHANT_API_KEY MEMPHANT_MCP_URL MEMPHANT_CAPTURE_URL \
    MEMPHANT_SUBJECT_ID MEMPHANT_SCOPE_ID MEMPHANT_ACTOR_ID MEMPHANT_AGENT_NODE_ID MEMPHANT_SUBJECT_GENERATION \
    MEMPHANT_APP_DATABASE_URL="$BATTERY_DB" MEMPHANT_AUTHN_DATABASE_URL="$BATTERY_DB"
  export MEMPHANT_CAPTURE_SUMMARIZER_CMD="$SUMMARIZER"
  export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"   # summarizer creds → reach the capture hook
}

# Warm-start / smoke only: one hand-authored non-repo convention so recall has
# something to deliver. Real battery memory comes from capture, not this.
# Seeds a PROCEDURAL compact unit (the coding lane serves procedural, not belief)
# via MCP remember — see seed.py for why.
seed(){
  # shellcheck disable=SC1090
  . "$ENVF"; export MEMPHANT_MCP_URL MEMPHANT_API_KEY
  SEED_TRIGGER="adding or scaffolding a Supabase edge function in yurivan" \
    python3 "$ROOT/scripts/battery/seed.py" "${1:?seed needs a fact}"
}

# yurivan terminal outcome: types + antipatterns (fast, convention-bearing).
# Returns 0 = pass. lint/vitest addable once the fast gate proves out.
gate(){ local wt="$1"; ( cd "$wt" && ln -sfn "$YURIVAN/web/node_modules" web/node_modules 2>/dev/null; \
  make types >/dev/null 2>&1 && make check-antipatterns >/dev/null 2>&1 ); }

task(){
  local id="$1" arm="$2" prompt
  prompt="$(awk -F'\t' -v id="$id" '$1==id{sub(/^[^\t]*\t/,"");print;exit}' "$TASKS")"
  [ -n "$prompt" ] || fail "task $id not in $TASKS"
  local wt="$STATE/wt-$id-$arm" home="$STATE/codex-home-$arm"
  git -C "$YURIVAN" worktree add -q --detach "$wt" HEAD 2>/dev/null || { rm -rf "$wt"; git -C "$YURIVAN" worktree prune; git -C "$YURIVAN" worktree add -q --detach "$wt" HEAD; }
  # yurivan needs its 1.2G node_modules symlinked in; harmless/skip for other repos.
  [ -d "$wt/web" ] && ln -sfn "$YURIVAN/web/node_modules" "$wt/web/node_modules" 2>/dev/null || true
  say "task $id arm $arm — codex run"
  ( cd "$wt"
    [ "$arm" = "memphant" ] && memphant_env
    # --dangerously-bypass-hook-trust: run the wired hooks.json without an interactive
    # trust prompt (memphant arm's injection+capture hooks); harmless on the bare arm.
    CODEX_HOME="$home" codex exec "$prompt" -m "$CODEX_MODEL" \
      --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust \
      >"$STATE/run-$id-$arm.log" 2>&1 || true )
  # Outcome is NOT a validator — save the produced diff + final message as artifacts;
  # an in-session judge (Claude/codex) scores each bare-vs-memphant pair by hand.
  local changed diff="$STATE/diff-$id-$arm.patch"
  # fastembed writes .fastembed_cache/ into cwd (the MCP server is spawned with the
  # worktree as cwd); keep it + node_modules out of the artifact. A worktree's .git is
  # a FILE, so its exclude lives at rev-parse --git-path, NOT $wt/.git/info/exclude.
  printf '.fastembed_cache/\nnode_modules\n' >>"$(git -C "$wt" rev-parse --git-path info/exclude)" 2>/dev/null || true
  git -C "$wt" add -A 2>/dev/null || true
  changed=$(git -C "$wt" diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
  git -C "$wt" diff --cached >"$diff" 2>/dev/null || true
  # informational only (never the score): whether the change trips the repo gate
  local gate_info=skip; gate "$wt" && gate_info=pass || gate_info=fail
  # Efficiency metrics (the real signal): tokens/turns/tool-calls this run spent
  # reaching its fix, read off the run's own rollout. Δ(bare vs memphant) = the value.
  local eff; eff="$(python3 "$ROOT/scripts/battery/metrics.py" --codex-home "$home" 2>/dev/null || echo '{}')"
  EFF="$eff" python3 -c "import json,os;m=json.loads(os.environ.get('EFF') or '{}');print(json.dumps({'task':'$id','arm':'$arm','files_changed':int('$changed'),'diff':'$diff','gate_info':'$gate_info','round':'${ROUND:-1}','total_tokens':m.get('total_tokens',0),'output_tokens':m.get('output_tokens',0),'tool_calls':m.get('tool_calls',0),'turns':m.get('turns',0)}))" | tee -a "$STATE/results.jsonl"
  git -C "$YURIVAN" worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
}

round(){ local n="${1:-1}"; say "ROUND $n"; while IFS=$'\t' read -r id _; do [ -z "$id" ] && continue; case "$id" in \#*) continue;; esac; task "$id" bare; task "$id" memphant; done <"$TASKS"; }

read_results(){
  # shellcheck disable=SC1090
  . "$ENVF"
  say "paired EFFICIENCY delta (bare - memphant; positive = memphant is cheaper to the same fix)"
  python3 - "$STATE/results.jsonl" <<'PY'
import json,sys,collections,os,statistics
rows=[json.loads(l) for l in open(sys.argv[1])] if os.path.exists(sys.argv[1]) else []
by=collections.defaultdict(dict)
for r in rows: by[r['task']][r['arm']]=r
FIELDS=[("total_tokens","tokens"),("output_tokens","out_tok"),("tool_calls","tools"),("turns","turns")]
deltas=collections.defaultdict(list)
print(f"{'task':<18}{'gate b/m':<10}" + "".join(f"Δ{lbl:<9}" for _,lbl in FIELDS))
for t,a in sorted(by.items()):
  if 'bare' not in a or 'memphant' not in a: continue
  g=f"{a['bare'].get('gate_info','?')[:1]}/{a['memphant'].get('gate_info','?')[:1]}"
  cells=""
  for key,_ in FIELDS:
    d=a['bare'].get(key,0)-a['memphant'].get(key,0); deltas[key].append(d); cells+=f"{d:<10}"
  print(f"{t[:17]:<18}{g:<10}{cells}")
n=len(deltas["total_tokens"])
print(f"\npaired tasks: {n}")
if n:
  for key,lbl in FIELDS:
    d=deltas[key]; med=statistics.median(d); wins=sum(1 for x in d if x>0)
    print(f"  Δ{lbl:<9} median={med:<8.0f} mean={statistics.mean(d):<8.1f} memphant-cheaper on {wins}/{n}")
  print("interpretation: consistently positive Δtokens/Δtools = memphant saved re-derivation (the value).")
  print("                gate b/m is INFORMATIONAL only — both arms usually reach a correct fix.")
PY
  say "capture→recall linkage (units captured, then served)"
  psql "$BATTERY_DB" -tAc "select count(*) filter (where payload ? 'capture') as captured_units, count(*) from memphant.memory_unit;" 2>/dev/null || true
}

smoke(){
  bootstrap
  seed "yurivan convention: every new Supabase edge function MUST be registered in the KV binding map or make check-antipatterns fails."
  # shellcheck disable=SC1090
  . "$ENVF"
  say "SMOKE: one memphant-arm run — asserts injection fired + capture wrote + gate ran"
  echo "smoke	Add a one-line code comment to web/README or any doc noting you checked the KV binding convention, then stop." >"$TASKS.smoke"
  TASKS="$TASKS.smoke" task smoke memphant || true
  say "assert: injection delivered a card?"
  grep -qiE "kv binding|edge function|memphant" "$STATE/run-smoke-memphant.log" && echo "  INJECTION: card reached the agent ✓" || echo "  INJECTION: no card visible in transcript (check $STATE/mcp.log)"
  say "assert: capture wrote an episode?"
  psql "$BATTERY_DB" -tAc "select count(*) from memphant.episode;" | xargs -I{} echo "  EPISODES rows: {}"
  say "assert: MCP recall reachable?"
  grep -c "recall" "$STATE/mcp.log" 2>/dev/null | xargs -I{} echo "  MCP recall hits in log: {}"
}

teardown(){
  for svc in server mcp worker; do [ -f "$STATE/$svc.pid" ] && kill "$(cat "$STATE/$svc.pid")" 2>/dev/null || true; done
  git -C "$YURIVAN" worktree prune 2>/dev/null || true
  psql "$ADMIN" -q -c "drop database if exists $DB" 2>/dev/null || true
  rm -rf "$STATE"; echo "torn down"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  bootstrap) bootstrap ;;
  seed) seed "$@" ;;
  task) task "$@" ;;
  round) round "$@" ;;
  read) read_results ;;
  smoke) smoke ;;
  teardown) teardown ;;
  *) echo "usage: run_battery.sh {bootstrap|seed <fact>|task <id> <arm>|round <n>|read|smoke|teardown}"; exit 2 ;;
esac
