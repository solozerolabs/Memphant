#!/usr/bin/env bash
# S8 — the retrieve-then-rank sweep, in the order the preregistration fixed it.
#
# Harness rules this file exists to obey, each already paid for in this program:
# capture rc FIRST (`echo "DONE rc=$?"` captures the echo, not the command);
# assert every artifact exists and is non-empty; abort the chain on the first
# failure; log to a real file rather than a buffering pipe; reap servers by
# ABSOLUTE worktree path (five lanes share this host and an unscoped `pkill`
# killed three sibling arm runs today).
#
# Usage:  doppler run --project syndai --config dev -- scripts/s8_hybrid_run.sh <stage> [N]
# Stages: pool | stub | sweep N | confirm N
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$HOME/.memphant-private/track-r-paraphrase/run-s8"
CORPUS="$HOME/.memphant-private/track-r/artifacts/corpus.jsonl"
GOLDEN="$HOME/.memphant-private/track-r-paraphrase/track_r_paraphrase_golden.jsonl"
SUBSET="$ROOT/docs/build-log/artifacts/s8-hybrid/sweep-subset.json"
POOL="$RUN/pool-dump.jsonl"
PORT="${S8_PORT:-39591}"
mkdir -p "$RUN"

# The sweep subset is committed as an artifact with its seed and method; the
# runner takes a bare JSON list, so it is projected here rather than kept as a
# second, driftable copy of the same ids.
SUBSET_IDS="$RUN/sweep-ids.json"
python3 -c "import json,sys; json.dump(json.load(open(sys.argv[1]))['question_ids'], open(sys.argv[2],'w'))" \
  "$SUBSET" "$SUBSET_IDS"

die() { echo "S8 ABORT: $*" >&2; exit 1; }

require_nonempty() {
  for path in "$@"; do
    [ -s "$path" ] || die "expected artifact missing or empty: $path"
  done
}

stage="${1:?usage: s8_hybrid_run.sh pool|stub|sweep N|confirm N}"

case "$stage" in
pool)
  # The only stage that runs a MemPhant server. It is $0 and it is the whole
  # haystack for every paid stage below: one shipped-default recall per golden,
  # banking the fused candidate pool WITH bodies. --dump-pool is capture only;
  # the served request is byte-identical to the shipped default arm, so this
  # run's own packed top-10 is a check on the banked 106/180 rather than a new
  # configuration.
  # The runner re-execs itself through with_scratch_db.sh; wrapping it again
  # here would nest a second scratch DB inside the first.
  python3 "$ROOT/scripts/code_lane_run_memphant.py" \
      --corpus "$CORPUS" --golden "$GOLDEN" \
      --embed-model small --mode fast --k 10 --budget-tokens 8192 \
      --lexical-scorer bm25-code --port "$PORT" --label s8_pool \
      --limit-attempts 1 \
      --server-bin "$ROOT/target/release/memphant-server" \
      --worker-bin "$ROOT/target/release/memphant-worker" \
      --cli-bin "$ROOT/target/release/memphant-cli" \
      --dump-pool "$POOL" \
      --out-evidence "$RUN/memphant-evidence.jsonl" \
      --out-provenance "$RUN/memphant-provenance.json" \
    >"$RUN/pool.log" 2>&1
  rc=$?
  tail -12 "$RUN/pool.log"
  [ "$rc" -eq 0 ] || die "pool dump rc=$rc"
  require_nonempty "$POOL" "$RUN/memphant-provenance.json"
  ;;

stub)
  # $0 round trip through the FULL arm contract before any money moves: tool
  # dispatch, argument validation, an out-of-range item, truncation, budget
  # ceiling, selection resolution, pool containment and scoring.
  python3 "$ROOT/scripts/code_lane_run_hybrid_rank.py" \
    --corpus "$CORPUS" --golden "$GOLDEN" --pool-dump "$POOL" \
    --pool-depth 64 --engine stub --only-ids "$SUBSET_IDS" --label stub64 \
    --out-evidence "$RUN/stub64-evidence.jsonl" \
    --out-provenance "$RUN/stub64-provenance.json" \
    >"$RUN/stub64.log" 2>&1
  rc=$?
  tail -6 "$RUN/stub64.log"
  [ "$rc" -eq 0 ] || die "stub rc=$rc"
  require_nonempty "$RUN/stub64-provenance.json" "$RUN/stub64-evidence.jsonl"
  ;;

sweep)
  N="${2:?sweep needs N}"
  python3 "$ROOT/scripts/code_lane_run_hybrid_rank.py" \
    --corpus "$CORPUS" --golden "$GOLDEN" --pool-dump "$POOL" \
    --pool-depth "$N" --engine openrouter --only-ids "$SUBSET_IDS" \
    --label "sweep-n$N" --concurrency 6 \
    --out-evidence "$RUN/sweep-n$N-evidence.jsonl" \
    --out-provenance "$RUN/sweep-n$N-provenance.json" \
    >"$RUN/sweep-n$N.log" 2>&1
  rc=$?
  tail -4 "$RUN/sweep-n$N.log"
  [ "$rc" -eq 0 ] || die "sweep N=$N rc=$rc"
  require_nonempty "$RUN/sweep-n$N-provenance.json"
  ;;

confirm)
  N="${2:?confirm needs N}"
  # Full n=180. Resumes from the sweep run at the SAME N so its rows are carried
  # rather than re-billed; the sweep is the first slice of the confirmation, not
  # a discarded one. An errored row is re-run, never carried.
  RESUME=()
  [ -s "$RUN/sweep-n$N-provenance.json" ] && RESUME=(--resume-from "$RUN/sweep-n$N-provenance.json")
  python3 "$ROOT/scripts/code_lane_run_hybrid_rank.py" \
    --corpus "$CORPUS" --golden "$GOLDEN" --pool-dump "$POOL" \
    --pool-depth "$N" --engine openrouter "${RESUME[@]}" \
    --label "confirm-n$N" --concurrency 6 \
    --out-evidence "$RUN/confirm-n$N-evidence.jsonl" \
    --out-provenance "$RUN/confirm-n$N-provenance.json" \
    >"$RUN/confirm-n$N.log" 2>&1
  rc=$?
  tail -4 "$RUN/confirm-n$N.log"
  [ "$rc" -eq 0 ] || die "confirm N=$N rc=$rc"
  require_nonempty "$RUN/confirm-n$N-provenance.json"
  ;;

*) die "unknown stage: $stage" ;;
esac

echo "S8 stage '$stage' OK"
