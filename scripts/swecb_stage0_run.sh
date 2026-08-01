#!/usr/bin/env bash
# Drive one SWE-ContextBench stage 0 arm with a worktree-scoped reaper.
#
# WHY THIS EXISTS. A first attempt used `pkill -f "target/release/memphant-server"`
# to reap leaked servers. That pattern is not worktree-scoped: several MemPhant
# lanes run concurrently on this host out of sibling worktrees, and the pattern
# matches every one of their server binaries. Reaping "my" processes with a
# relative path is reaching into other people's lanes.
#
# Everything here is scoped to this worktree's absolute path. The trap reaps only
# children this script started, matched on the absolute binary path.
#
# Usage: bash scripts/swecb_stage0_run.sh <patchfree|withpatch> <port> <python> <output.json> [full|lite]
set -euo pipefail

VARIANT="${1:?variant required: patchfree|withpatch}"
PORT="${2:?port required}"
PYTHON="${3:?python interpreter required}"
OUTPUT="${4:?output path required}"
SCOPE="${5:-full}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIRROR="$HOME/.memphant-private/w7-instruments/swe-contextbench"

reap() {
  # Absolute path only. Never a bare binary name, never a relative path.
  pkill -f "^${ROOT}/target/release/memphant-server" 2>/dev/null || true
  pkill -f "^${ROOT}/target/release/memphant-worker" 2>/dev/null || true
}
trap reap EXIT INT TERM

# Capture rc FIRST. A pipeline into tail reports tail's status, which is how a
# failed run previously reported EXITCODE=0.
set +e
"$PYTHON" "$ROOT/scripts/swecb_stage0_recall.py" \
  --mirror "$MIRROR" \
  --body "$VARIANT" \
  --scope "$SCOPE" \
  --port "$PORT" \
  --output "$OUTPUT"
rc=$?
set -e

echo "swecb_stage0_run: variant=$VARIANT scope=$SCOPE rc=$rc"
if [ "$rc" -ne 0 ]; then
  echo "swecb_stage0_run: ABORTED -- no number may be reported from this run" >&2
  exit "$rc"
fi

# Silence is not a valid outcome: assert the artifact exists and is non-empty.
[ -s "$OUTPUT" ] || { echo "swecb_stage0_run: artifact is missing or empty: $OUTPUT" >&2; exit 4; }
echo "swecb_stage0_run: artifact OK $(wc -c < "$OUTPUT") bytes"
