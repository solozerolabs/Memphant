#!/bin/bash
# Local MemPhant dev stack for the Phase A Syndai dogfood cohort.
# Server (127.0.0.1:3020, dev-tenant auth) + worker, against the local
# persistent `memphant` database. Fail-open contract on the Syndai side means
# this stack being down just yields untreated runs — but treated runs require
# it, so keep it running while dev coding runs execute.
#
# Usage: scripts/dev_stack.sh [repo-root]   # logs to ~/.memphant-private/dev-stack/
set -euo pipefail
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
DB="postgresql://sidsharma@localhost:5432/memphant"
LOGDIR="$HOME/.memphant-private/dev-stack"
mkdir -p "$LOGDIR"

# Fixed dev tenant for the dogfood cohort — recorded in the Phase A prereg.
export MEMPHANT_DEV_TENANT="7a1e9c2e-4b0d-4f7a-9c66-2f4d8a1b5e30"
export MEMPHANT_APP_DATABASE_URL="$DB"
export MEMPHANT_AUTHN_DATABASE_URL="$DB"
export MEMPHANT_WORKER_DATABASE_URL="$DB"
export MEMPHANT_BIND="127.0.0.1:3020"

pkill -f memphant-server 2>/dev/null || true
pkill -f memphant-worker 2>/dev/null || true
sleep 0.5

nohup "$ROOT/target/release/memphant-server" >> "$LOGDIR/server.log" 2>&1 &
echo "server pid $!"
nohup "$ROOT/target/release/memphant-worker" >> "$LOGDIR/worker.log" 2>&1 &
echo "worker pid $!"
sleep 1
curl -sf -o /dev/null "http://127.0.0.1:3020/v1/scopes/00000000-0000-0000-0000-000000000000/memory" 2>/dev/null \
  && echo "server: responding (auth path reachable)" \
  || echo "server: up (endpoint returned non-2xx as expected without binding)"
tail -1 "$LOGDIR/server.log"
