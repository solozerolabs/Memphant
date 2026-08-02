#!/usr/bin/env bash
# S6 paired analyses. Launch DETACHED via scripts/detach_run.py (see
# s6_asof_arms.sh's header for why `nohup ... &` is not sufficient).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv-s6/bin/python"
ARMS="${1:?usage: s6_asof_analysis.sh <armsdir> <trivialdir>}"
TRIV="${2:?}"
OUT="$ARMS/analysis"; mkdir -p "$OUT"
STATUS="$OUT/STATUS"; : > "$STATUS"

pair() {
  local name="$1" a="$2" b="$3" claim="$4"; shift 4
  echo "START $name $(date -u +%FT%TZ)" >> "$STATUS"
  "$PY" "$ROOT/scripts/preference_lane_analysis.py" \
    --arm-a "$a" --arm-b "$b" --out "$OUT/$name.json" --claim "$claim" "$@" \
    > "$OUT/$name.log" 2>&1
  local rc=$?
  if [ $rc -ne 0 ] || [ ! -s "$OUT/$name.json" ]; then
    echo "FAILED $name rc=$rc $(date -u +%FT%TZ)" >> "$STATUS"; return 1
  fi
  echo "DONE $name rc=0 $(date -u +%FT%TZ)" >> "$STATUS"
}

# 1. THE DECISION QUANTITY (prereg 5.2): substrate vs the 20-line read rule.
pair p_vs_trivial "$ARMS/p.json" "$TRIV/t-asof_truncation.json" \
  "bitemporal supersession vs max(observed_at <= t), the honest trivial baseline"
# 2. The banked +3.01pp question, re-asked on a cut that does not flatter recency.
pair p_vs_parec "$ARMS/p.json" "$ARMS/p_arec.json" \
  "bitemporal vs A-recency, both oracle-keyed, as-of cut"
# 3. The banked +0.0583 question.
pair k_vs_aprime "$ARMS/k.json" "$ARMS/aprime.json" \
  "derived keys vs default ingest, as-of cut"
# 4. Arm K against the same trivial rule.
pair k_vs_trivial "$ARMS/k.json" "$TRIV/t-asof_truncation.json" \
  "derived keys vs max(observed_at <= t)"

echo "ALL_DONE $(date -u +%FT%TZ)" >> "$STATUS"
