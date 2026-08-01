#!/usr/bin/env bash
# Drive one coding-lane reader-QA bank end to end: stage-equalize every arm,
# mint a frozen packet per arm, run the arms, emit the paired comparison.
#
# One packet and one ledger PER ARM, not per bank. The campaign ledger takes an
# exclusive flock on its journal, so arms sharing a journal are forced to run
# one after another; at ~30s per question that is ~90 minutes of avoidable
# serialisation per arm. Separate journals let the arms run concurrently, and
# nothing is lost: spend is summed across the ledgers, and the cache key already
# includes each packet's authorization_sha256, so no arm can read another's
# replies.
#
# Usage:
#   bash scripts/code_lane_reader_run.sh <bank> <run-dir> <memphant-evidence> \
#        <memphant-provenance> <control-evidence> <control-provenance> \
#        <corpus-sha> <golden-sha> [prompt-version]
set -euo pipefail

BANK="${1:?bank label}"
RUN="${2:?run dir (private, gitignored)}"
MEM_EV="${3:?memphant evidence}"
MEM_PR="${4:?memphant provenance}"
CTL_EV="${5:?control evidence}"
CTL_PR="${6:?control provenance}"
CORPUS_SHA="${7:?corpus sha256}"
GOLDEN_SHA="${8:?golden sha256}"
PROMPT_VERSION="${9:-3}"
LEAKAGE="${10:?bank leakage json}"
PROVENANCE="${11:?provenance class}"
CLAIM="${12:?the one sentence this artifact is cited for}"
SNAPSHOT="nebius/SWE-rebench-openhands-trajectories@35455389ab51bf5e2306bfd436ef72d0f98bf882"
N_ITEMS=64055

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="docs/build-log/artifacts/code-lane-reader/$BANK"
EQ="$RUN/equalized-$BANK"
mkdir -p "$OUT"

echo "[1/4] stage-equalizing every arm through one packer"
python3 scripts/code_lane_reader_prepare.py \
  --arm memphant="$MEM_EV" --arm bm25scoped="$CTL_EV" \
  --out-dir "$EQ" --k 10 --budget-tokens 8192 \
  --corpus-sha256 "$CORPUS_SHA" --golden-sha256 "$GOLDEN_SHA" \
  --binary server=target/release/memphant-server \
  --binary worker=target/release/memphant-worker \
  --binary cli=target/release/memphant-cli \
  --harness-env "MEMPHANT_FACT_EXTRACTION=shipped-default" \
  --harness-env "reader_prompt_version=$PROMPT_VERSION" \
  --harness-env "bank=$BANK"

mint_arm() {  # arm retrieval
  local arm="$1" retrieval="$2"
  # RESUME=1 keeps the reader cache and the attempt ledger. This is a money
  # property, not a convenience: the cache is the per-CALL checkpoint, so a run
  # killed at any point replays every completed reader and judge call for free
  # and re-bills only what it had not finished. Wiping it on a restart re-bills
  # the whole arm — the opposite of what a restart is for. The packet is
  # deterministic in its inputs, so the re-minted authorization hash (part of
  # the cache key) matches and the cache is genuinely readable.
  if [ "${RESUME:-0}" = "1" ]; then
    echo "RESUME=1: keeping cache-$arm ($(ls "$OUT/cache-$arm" 2>/dev/null | wc -l | tr -d ' ') cached calls) and the attempt ledger"
  else
    rm -f "$OUT/attempts-$arm.jsonl" "$OUT/attempts-$arm.jsonl.lock"
    rm -rf "$OUT/cache-$arm"
  fi
  python3 scripts/code_lane_reader_packet.py \
    --arm "$arm=$EQ/$arm-equalized-evidence.jsonl:$retrieval:$ROOT/$OUT/reader-$arm.json" \
    --out "$ROOT/$OUT/authorization-$arm.json" --ledger-name "attempts-$arm.jsonl" \
    --cache-dir "$ROOT/$OUT/cache-$arm" \
    --reader-model anthropic/claude-opus-5 --judge-model anthropic/claude-opus-5 \
    --prompt-version "$PROMPT_VERSION" --price-prompt 5 --price-completion 25 \
    --authorized-by "owner 2026-07-31: no ceiling, necessity established" \
    --authorized-at "2026-07-31T16:00:00-07:00" > "$OUT/packet-$arm.json"
}

run_arm() {  # arm profile retrieval
  local arm="$1" profile="$2" retrieval="$3" calls spend
  calls=$(python3 -c "import json;print(json.load(open('$OUT/authorization-$arm.json'))['hard_limits']['$arm']['max_logical_calls'])")
  spend=$(python3 -c "import json;print(json.load(open('$OUT/authorization-$arm.json'))['hard_limits']['$arm']['max_spend_usd'])")
  echo "LAUNCH $BANK/$arm profile=$profile calls=$calls cap=\$$spend $(date -u +%FT%TZ)"
  doppler run --project syndai --config dev -- python3 scripts/run_reader.py \
    --engine openrouter --model anthropic/claude-opus-5 \
    --judge-model anthropic/claude-opus-5 --judge-profile rag-supported-v1 \
    --prompt-version "$PROMPT_VERSION" --reader-profile "$profile" \
    --provider-only anthropic \
    --evidence "$EQ/$arm-equalized-evidence.jsonl" --retrieval-report "$retrieval" \
    --out "$OUT/reader-$arm.json" --label "$BANK-$arm" \
    --cache-dir "$OUT/cache-$arm" --attempt-ledger "$OUT/attempts-$arm.jsonl" \
    --authorization-manifest "$OUT/authorization-$arm.json" --authorization-arm "$arm" \
    --max-calls "$calls" --max-provider-attempts $((calls * 4)) --max-spend-usd "$spend" \
    --max-price-prompt-per-million 5 --max-price-completion-per-million 25 \
    --max-output-tokens 1024 > "$OUT/$arm.log" 2>&1
  echo "DONE $BANK/$arm rc=$? $(date -u +%FT%TZ)"
}

echo "[2/4] minting one frozen packet per arm"
mint_arm memphant   "$MEM_PR"
mint_arm bm25scoped "$CTL_PR"
mint_arm nomemory   "$EQ/stage-equalization.json"

echo "[3/4] running arms concurrently"
run_arm memphant   evidence    "$MEM_PR" &
run_arm bm25scoped evidence    "$CTL_PR" &
run_arm nomemory   closed-book "$EQ/stage-equalization.json" &
wait

echo "[4/4] paired comparison"
for endpoint in answer_correct correct; do
  python3 scripts/code_lane_reader_compare.py \
    --arm memphant="$OUT/reader-memphant.json" \
    --arm bm25scoped="$OUT/reader-bm25scoped.json" \
    --arm nomemory="$OUT/reader-nomemory.json" \
    --retrieval memphant="$MEM_PR" --retrieval bm25scoped="$CTL_PR" \
    --control bm25scoped --stage-manifest "$EQ/stage-equalization.json" \
    --bank "$BANK" --endpoint "$endpoint" \
    --claim "$CLAIM" --leakage "$LEAKAGE" --provenance-class "$PROVENANCE" \
    --corpus-snapshot-id "$SNAPSHOT" --corpus-n-items "$N_ITEMS" \
    --license-id CC-BY-4.0 --license-source HF_DATASET_CARD \
    --license-evidence "nebius/SWE-rebench-openhands-trajectories dataset card, pinned in benchmarks/data/track_r_paraphrase_golden.lock.json corpus block" \
    --out "docs/build-log/artifacts/code-lane-reader/phase3-reader-qa-$BANK-$endpoint.json"
done
echo "=== $BANK COMPLETE $(date -u +%FT%TZ) ==="
