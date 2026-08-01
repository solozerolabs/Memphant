#!/usr/bin/env bash
# S4 — the paid stages, in the order the preregistration fixed them.
#
# Harness rules this file exists to obey, each one already paid for in this
# program: capture rc FIRST (`echo "DONE rc=$?"` captures the echo, not the
# command); assert every artifact exists and is non-empty; abort the chain on
# the first failure; log to a real file rather than a buffering pipe.
#
# Usage:  doppler run --project syndai --config dev -- scripts/s4_controls_run.sh <stage>
# Stages: oncu | pilot | full | compare
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$HOME/.memphant-private/track-r-paraphrase/run-s4"
CORPUS="$HOME/.memphant-private/track-r/artifacts/corpus.jsonl"
GOLDEN="$HOME/.memphant-private/track-r-paraphrase/track_r_paraphrase_golden.jsonl"
TRUNK="$HOME/.memphant-private/track-r-paraphrase/run-fusion/fusion_probe-provenance.json"
BM25="$HOME/.memphant-private/track-r-paraphrase/run-trunk/bm25-scoped-provenance.json"
mkdir -p "$RUN"

die() { echo "S4 ABORT: $*" >&2; exit 1; }

require_nonempty() {
  for path in "$@"; do
    [ -s "$path" ] || die "expected artifact missing or empty: $path"
  done
}

# No braces in this message: the first `}` would close the parameter expansion
# and the remainder would be appended to $1, silently corrupting the stage name.
stage="${1:?usage: s4_controls_run.sh oncu|pilot|full|compare}"

case "$stage" in
oncu)
  python3 "$ROOT/scripts/s4_oncu_probe.py" \
    --corpus "$CORPUS" --golden "$GOLDEN" --n 20 \
    --out "$RUN/oncu-probe.json" >"$RUN/oncu.log" 2>&1
  rc=$?
  tail -5 "$RUN/oncu.log"
  [ "$rc" -eq 0 ] || die "oncu probe rc=$rc"
  require_nonempty "$RUN/oncu-probe.json"
  ;;

pilot)
  python3 "$ROOT/scripts/code_lane_run_agentic_control.py" \
    --corpus "$CORPUS" --golden "$GOLDEN" --engine openrouter \
    --only-ids "$RUN/pilot30.json" --label agentic-pilot30 \
    --out-evidence "$RUN/agentic-pilot-evidence.jsonl" \
    --out-provenance "$RUN/agentic-pilot-provenance.json" \
    >"$RUN/agentic-pilot.log" 2>&1
  rc=$?
  tail -8 "$RUN/agentic-pilot.log"
  [ "$rc" -eq 0 ] || die "agentic pilot rc=$rc"
  require_nonempty "$RUN/agentic-pilot-provenance.json" "$RUN/agentic-pilot-evidence.jsonl"
  ;;

full)
  # Resumes from the pilot: its 30 rows are carried, not re-billed, and any
  # errored row is re-run rather than carried (an errored row scores incorrect).
  python3 "$ROOT/scripts/code_lane_run_agentic_control.py" \
    --corpus "$CORPUS" --golden "$GOLDEN" --engine openrouter \
    --resume-from "$RUN/agentic-pilot-provenance.json" --label agentic-full180 \
    --out-evidence "$RUN/agentic-full-evidence.jsonl" \
    --out-provenance "$RUN/agentic-full-provenance.json" \
    >"$RUN/agentic-full.log" 2>&1
  rc=$?
  tail -8 "$RUN/agentic-full.log"
  [ "$rc" -eq 0 ] || die "agentic full rc=$rc"
  require_nonempty "$RUN/agentic-full-provenance.json" "$RUN/agentic-full-evidence.jsonl"
  ;;

compare)
  require_nonempty "$TRUNK" "$BM25" \
    "$RUN/dense_control-provenance.json" "$RUN/agentic-full-provenance.json"
  python3 "$ROOT/scripts/s4_controls_compare.py" \
    --treatment "$TRUNK" \
    --control "agentic_grep=$RUN/agentic-full-provenance.json" \
    --control "dense_rag=$RUN/dense_control-provenance.json" \
    --control "bm25_scoped=$BM25" \
    --out "$ROOT/docs/build-log/artifacts/s4-controls/analysis.json" \
    >"$RUN/compare.log" 2>&1
  rc=$?
  cat "$RUN/compare.log"
  [ "$rc" -eq 0 ] || die "compare rc=$rc"
  require_nonempty "$ROOT/docs/build-log/artifacts/s4-controls/analysis.json"
  ;;

*) die "unknown stage: $stage" ;;
esac

echo "S4 stage '$stage' OK"
