#!/usr/bin/env bash
# S1 -- the similarity-unit swap. Four arms, one tree, one set of binaries, one
# corpus, one probe bank. See docs/build-log/2026-08-01-similarity-unit-swap.md
# for the preregistration; this script runs exactly the arms it names.
#
# Every hazard guarded here has already been paid for by this program:
#   * `rc` is captured FIRST -- `echo "DONE rc=$?"` captures the echo's status;
#   * each arm's artifact is asserted to exist and be non-empty before the arm
#     is called successful;
#   * EXIT/INT/TERM reap every server this script's children spawned -- two
#     leaked `memphant-server`s once held ports for 8h21m;
#   * progress goes to a real file per arm, never a buffering pipe, and each
#     arm writes a terminal `S1-ARM-DONE arm=<name> rc=<rc>` line so a watch
#     can match EVERY terminal state. Silence is never a valid outcome.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/docs/build-log/artifacts/2026-08-01-similarity-unit-swap}"
LOGS="${LOGS:-$OUT/logs}"
PY="${PY:?PY=<venv-with-pyarrow>/bin/python required}"
SRC="${SRC:-$HOME/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet}"
STAGGER="${STAGGER:-60}"
# Four arms queue on the serialized scratch-DB bootstrap. The queue is short now
# that the lock is released after migration rather than on exit, but the wait
# cap is given headroom anyway: dying at the queue instead of at the work is the
# most expensive way to fail.
export MEMPHANT_SCRATCH_LOCK_WAIT_SECONDS="${MEMPHANT_SCRATCH_LOCK_WAIT_SECONDS:-1800}"

mkdir -p "$OUT" "$LOGS"

PIDS=()
cleanup() {
  local rc=$?
  for pid in "${PIDS[@]:-}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  # Reap any server this run's children left holding a port. Matched on the
  # exact ports this script uses so a concurrent lane's server is never killed.
  for port in 39571 39572 39573 39574; do
    for spid in $(pgrep -f "memphant-server" 2>/dev/null); do
      if lsof -p "$spid" -a -i :"$port" >/dev/null 2>&1; then
        echo "[cleanup] reaping memphant-server pid=$spid port=$port" >&2
        kill -TERM "$spid" 2>/dev/null || true
      fi
    done
  done
  exit "$rc"
}
trap cleanup EXIT INT TERM

# arm <name> <port> <extra args...>
arm() {
  local name="$1" port="$2"; shift 2
  local art="$OUT/arm-$name.json" log="$LOGS/arm-$name.log"
  (
    "$PY" "$ROOT/scripts/external_instrument_adapter.py" \
      --instrument memorycode --arm structured --diagnostics \
      --source "$SRC" --out "$art" --port "$port" "$@" >>"$log" 2>&1
    rc=$?                                   # FIRST. Nothing between.
    if [ "$rc" -eq 0 ] && [ ! -s "$art" ]; then
      echo "[$name] FATAL: rc=0 but artifact missing or empty: $art" >>"$log"
      rc=90
    fi
    echo "S1-ARM-DONE arm=$name rc=$rc" >>"$log"
    echo "S1-ARM-DONE arm=$name rc=$rc"
  ) &
  local pid=$!            # not ${PIDS[-1]}: macOS ships bash 3.2, no negative index
  PIDS+=("$pid")
  echo "[launch] arm=$name port=$port pid=$pid log=$log"
}

echo "=== S1 unit swap: launching 4 arms at $(date -u +%FT%TZ) ==="
arm n-noop        39571 --structured-unit sentence --structured-threshold 2.0
sleep "$STAGGER"
arm s-body        39572 --structured-unit body     --structured-threshold 0.25
sleep "$STAGGER"
arm u-sentence    39573 --structured-unit sentence --structured-threshold 0.42
sleep "$STAGGER"
arm r3-random     39574 --structured-unit body     --structured-threshold 0.25 \
                        --structured-ablation random --structured-fire-rate 0.13828

fail=0
for pid in "${PIDS[@]}"; do
  wait "$pid" || fail=1
done
echo "=== S1 unit swap: all arms terminal at $(date -u +%FT%TZ) fail=$fail ==="
grep -h "^S1-ARM-DONE" "$LOGS"/arm-*.log
exit "$fail"
