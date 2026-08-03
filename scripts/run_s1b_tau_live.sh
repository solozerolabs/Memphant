#!/usr/bin/env bash
# S1b live arm: sentence-unit tau=0.53 against a freshly rebuilt tau=0.42
# reference. Same adapter, tree, binaries, corpus, probe bank, and stage.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/docs/build-log/artifacts/2026-08-02-s1b-tau-live}"
LOGS="$OUT/logs"
PY="${PY:?PY=<venv-with-pyarrow>/bin/python required}"
SRC="${SRC:-$HOME/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet}"
STAGGER="${STAGGER:-60}"
export MEMPHANT_SCRATCH_LOCK_WAIT_SECONDS="${MEMPHANT_SCRATCH_LOCK_WAIT_SECONDS:-1800}"

mkdir -p "$OUT" "$LOGS"
PIDS=()
cleanup() {
  local rc=$?
  for pid in "${PIDS[@]:-}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for port in 39631 39632; do
    for spid in $(pgrep -f "^$ROOT/target/release/memphant-server" 2>/dev/null); do
      if lsof -p "$spid" -a -i :"$port" >/dev/null 2>&1; then
        kill -TERM "$spid" 2>/dev/null || true
      fi
    done
  done
  exit "$rc"
}
trap cleanup EXIT INT TERM

arm() {
  local name="$1" port="$2" tau="$3"
  local artifact="$OUT/arm-$name.json" log="$LOGS/arm-$name.log"
  (
    "$PY" "$ROOT/scripts/external_instrument_adapter.py" \
      --instrument memorycode --arm structured --diagnostics \
      --source "$SRC" --out "$artifact" --port "$port" \
      --structured-unit sentence --structured-threshold "$tau" >>"$log" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ] && [ ! -s "$artifact" ]; then rc=90; fi
    echo "S1B-ARM-DONE arm=$name rc=$rc" >>"$log"
    echo "S1B-ARM-DONE arm=$name rc=$rc"
    exit "$rc"
  ) &
  PIDS+=("$!")
}

arm u42-sentence 39631 0.42
sleep "$STAGGER"
arm t53-sentence 39632 0.53

fail=0
for pid in "${PIDS[@]}"; do wait "$pid" || fail=1; done
[ "$fail" -eq 0 ] || exit 1

"$PY" "$ROOT/scripts/s1_liveness_gate.py" \
  --dir "$OUT" --out "$OUT/liveness-gate.json" \
  --arm u42-sentence --arm t53-sentence || exit 1

"$PY" "$ROOT/scripts/preference_lane_analysis.py" \
  --arm-a "$OUT/arm-t53-sentence.json" \
  --arm-b "$OUT/arm-u42-sentence.json" \
  --liveness-gate "$OUT/liveness-gate.json" \
  --out "$OUT/analysis-t53-vs-u42.json" \
  --prereg "docs/build-log/2026-08-02-s1b-tau-recalibration.md" \
  --claim "At the preregistered sentence-unit operating point, tau=0.53 latest-state-wins is compared with a fresh same-tree tau=0.42 reference." \
  --notes "S1b live tau arm. Same tree, served binaries, corpus, exact probe bank, stage, and sentence-unit pipeline; liveness passed before analysis. neither_returned is a paired endpoint. Deterministic local pipeline, zero paid or generative model calls."
