#!/usr/bin/env bash
# S8 stage 2 — the coarse sweep, every N, one after another, detached.
#
# Sequential rather than parallel on purpose: the arms share one OpenRouter
# account and one rate limit, and a lane that queues behind itself is cheaper to
# reason about than seven that interleave. Each N writes its own artifact and
# the chain continues past a failed N rather than dying, because six points on a
# curve are worth more than none — but every failure is printed, and the ledger
# at the end reports what actually landed.
#
# Usage: doppler run --project syndai --config dev -- scripts/s8_sweep_all.sh
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$HOME/.memphant-private/track-r-paraphrase/run-s8"
NS="${S8_SWEEP_NS:-4 8 16 32 64 128 0}"   # 0 = the whole pool

echo "S8 sweep starting $(date -u +%FT%TZ) over N in: $NS"
for N in $NS; do
  echo "=== N=$N $(date -u +%FT%TZ) ==="
  bash "$ROOT/scripts/s8_hybrid_run.sh" sweep "$N"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "!!! N=$N FAILED rc=$rc — continuing; the curve keeps its other points"
  fi
  # A running ledger after every N, so a run killed midway still says what it
  # spent. Silence must never be a valid outcome.
  python3 - "$RUN" <<'PY'
import glob, json, sys
total = 0.0
for path in sorted(glob.glob(f"{sys.argv[1]}/sweep-n*-provenance.json")):
    report = json.load(open(path))
    total += report["reported_spend_usd"]
    print(
        f"  ledger N={report['pool_depth_requested']:>4} "
        f"hits={report['hits_at_10']}/{report['golden_count']} "
        f"${report['reported_spend_usd']:.2f} "
        f"(${report['spend_usd_per_question']:.4f}/q) "
        f"errors={report['liveness']['rows_with_errors']}"
    )
print(f"  ledger TOTAL ${total:.2f} of the $180 ceiling")
PY
done
echo "S8 sweep done $(date -u +%FT%TZ)"
