#!/usr/bin/env bash
# S6 as-of re-cut: the substrate arms, one tree, same binaries, serialized.
#
# LAUNCH IT DETACHED, ALWAYS:
#   python3 scripts/detach_run.py <outdir>/chain.out bash scripts/s6_asof_arms.sh <outdir>
#
# Not `nohup ... &`. This chain lost a run to `rc=143` -- SIGTERM at exactly 60
# minutes when the launching agent's process group was reaped -- and then lost a
# SECOND one to a hand-rolled double-fork that reparented to launchd (ppid=1)
# while staying in the launching shell's process group, so a group-wide signal
# still reached it. **`pgid == pid` is the load-bearing assertion, not
# `ppid == 1`.** Verify after launch:
#   ps -o pid=,ppid=,pgid= -p <pid>   # want ppid 1 AND pgid equal to pid
# `scripts/detach_run.py` (trunk, tested) is the only supported launcher; do not
# grow a second implementation here.
#
# Server reaping is anchored to THIS worktree's absolute binary path. Six
# MemPhant lanes run concurrently on this host out of sibling worktrees, and an
# unanchored `pkill -f memphant-server` kills their in-flight measurements.
# The `^` anchor is load-bearing.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv-s6/bin/python"
SRC="$HOME/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet"
LOCK="$ROOT/benchmarks/manifests/memorycode.lock.json"
OUT="${1:?usage: s6_asof_arms.sh <outdir>}"
STATUS="$OUT/STATUS"
mkdir -p "$OUT"
: > "$STATUS"

reap() {
  # Only this worktree's servers. Never a sibling lane's.
  pkill -f "^${ROOT}/target/release/memphant-server" 2>/dev/null
}
trap reap EXIT INT TERM

export MEMPHANT_SCRATCH_LOCK_WAIT_SECONDS="${MEMPHANT_SCRATCH_LOCK_WAIT_SECONDS:-14400}"

run_arm() {
  local name="$1"; shift
  local out="$OUT/$name.json"
  echo "START $name $(date -u +%FT%TZ)" >> "$STATUS"
  "$PY" "$ROOT/scripts/external_instrument_adapter.py" \
    --instrument memorycode_asof --lock "$LOCK" --source "$SRC" \
    --out "$out" --diagnostics "$@" > "$OUT/$name.log" 2>&1
  local rc=$?                      # rc FIRST, before any pipe or echo
  if [ $rc -ne 0 ]; then
    echo "FAILED $name rc=$rc $(date -u +%FT%TZ)" >> "$STATUS"; return 1
  fi
  if [ ! -s "$out" ]; then
    echo "FAILED $name rc=0 but artifact empty $(date -u +%FT%TZ)" >> "$STATUS"; return 1
  fi
  echo "DONE $name rc=0 $(date -u +%FT%TZ)" >> "$STATUS"
  return 0
}

# Ports differ per arm so a leaked listener from one cannot silently serve the
# next -- and so a sibling lane's port choice cannot collide with ours.
run_arm aprime  --arm memphant  --port 39492                    || exit 1
run_arm p       --arm preference --bounded --port 39493          || exit 1
run_arm p_arec  --arm preference --bounded --a-recency --port 39494 || exit 1
run_arm k       --arm derived    --bounded --port 39495          || exit 1

echo "ALL_DONE $(date -u +%FT%TZ)" >> "$STATUS"
