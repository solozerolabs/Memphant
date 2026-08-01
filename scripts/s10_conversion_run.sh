#!/usr/bin/env bash
# S10 — does hit@k predict answer correctness?
#
# Reader-QA over ALREADY-BANKED packs. No retrieval is re-run, so the arms are
# exactly the ones whose hit@10 the program has been quoting, and the joint
# distribution of (gold retrieved, answer correct) is measurable per arm.
#
# This is a thin driver over the three existing primitives —
# code_lane_reader_prepare.py, code_lane_reader_packet.py,
# code_lane_reader_compare.py — with one substitution: the control arm is the
# agentic-grep arm (hit@10 0.9667), not deterministic BM25. It is kept separate
# from scripts/code_lane_reader_run.sh rather than folded into it because that
# script's positional contract is depended on by the Phase-3 lane.
#
# Usage: bash scripts/s10_conversion_run.sh <tag> <run-dir> <prompt-version> [limit]
set -euo pipefail

TAG="${1:?tag, e.g. pilot30 or full180}"
RUN="${2:?private run dir (gitignored)}"
PROMPT_VERSION="${3:-3}"
LIMIT="${4:-}"

MEM_EV="$HOME/.memphant-private/track-r-paraphrase/run-fusion/fusion_probe-evidence.jsonl"
MEM_PR="$HOME/.memphant-private/track-r-paraphrase/run-fusion/fusion_probe-provenance.json"
GREP_EV="$HOME/.memphant-private/track-r-paraphrase/run-s4/agentic-final-evidence.jsonl"
GREP_PR="$HOME/.memphant-private/track-r-paraphrase/run-s4/agentic-final-provenance.json"
LEAKAGE="$HOME/.memphant-private/track-r-paraphrase/leakage-paraphrase.json"
CORPUS_SHA="c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669"
GOLDEN_SHA="4aed8e99dbf13d942d0e1d79b637ca5ee37b3dc30707a65ea3e9ffcd22bf4326"
SNAPSHOT="nebius/SWE-rebench-openhands-trajectories@35455389ab51bf5e2306bfd436ef72d0f98bf882"
N_ITEMS=64055
CLAIM="On the Track-R paraphrase bank, answer correctness under a fixed reader is measured jointly with gold-span retrieval@10 for a MemPhant fused pack, an agentic-grep pack and an empty pack, to test whether hit@k predicts the outcome it is used as a proxy for."

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="docs/build-log/artifacts/s10-conversion/$TAG"
EQ="$RUN/equalized-$TAG"
mkdir -p "$OUT"

echo "[1/4] stage-equalizing every arm through one packer  $(date -u +%FT%TZ)"
python3 scripts/code_lane_reader_prepare.py \
  --arm memphant="$MEM_EV" --arm agenticgrep="$GREP_EV" \
  --out-dir "$EQ" --k 10 --budget-tokens 8192 \
  --corpus-sha256 "$CORPUS_SHA" --golden-sha256 "$GOLDEN_SHA" \
  --harness-env "reader_prompt_version=$PROMPT_VERSION" \
  --harness-env "bank=track-r-paraphrase" \
  --harness-env "arms=banked-packs-no-retrieval-rerun" \
  ${LIMIT:+--limit "$LIMIT"}

mint_arm() {  # arm retrieval
  local arm="$1" retrieval="$2"
  # RESUME=1 keeps the reader cache and the attempt ledger. The cache is the
  # per-CALL checkpoint: a killed run replays every completed reader and judge
  # call for free, so a lifecycle SIGTERM costs one call rather than the
  # campaign. Wiping it on a resume would re-bill the whole arm, which is the
  # opposite of what a restart is for. The packet is deterministic in its
  # inputs, so the re-minted authorization hash — part of the cache key —
  # matches and the cache is genuinely readable.
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
    --max-output-tokens 3000 \
    --spend-headroom 1.0 \
    --authorized-by "owner standing authority, ceiling \$150: S10 endpoint validity" \
    --authorized-at "2026-08-01T00:00:00-07:00" > "$OUT/packet-$arm.json"
}

run_arm() {  # arm profile retrieval
  local arm="$1" profile="$2" retrieval="$3" calls spend rc
  calls=$(python3 -c "import json;print(json.load(open('$OUT/authorization-$arm.json'))['hard_limits']['$arm']['max_logical_calls'])")
  spend=$(python3 -c "import json;print(json.load(open('$OUT/authorization-$arm.json'))['hard_limits']['$arm']['max_spend_usd'])")
  echo "LAUNCH $TAG/$arm profile=$profile calls=$calls cap=\$$spend $(date -u +%FT%TZ)"
  set +e
  doppler run --project syndai --config dev -- python3 scripts/run_reader.py \
    --engine openrouter --model anthropic/claude-opus-5 \
    --judge-model anthropic/claude-opus-5 --judge-profile rag-supported-v1 \
    --prompt-version "$PROMPT_VERSION" --reader-profile "$profile" \
    --provider-only anthropic \
    --evidence "$EQ/$arm-equalized-evidence.jsonl" --retrieval-report "$retrieval" \
    --out "$OUT/reader-$arm.json" --label "s10-$TAG-$arm" \
    --cache-dir "$OUT/cache-$arm" --attempt-ledger "$OUT/attempts-$arm.jsonl" \
    --authorization-manifest "$OUT/authorization-$arm.json" --authorization-arm "$arm" \
    --max-calls "$calls" --max-provider-attempts $((calls * 4)) --max-spend-usd "$spend" \
    --max-price-prompt-per-million 5 --max-price-completion-per-million 25 \
    --max-output-tokens 3000 > "$OUT/$arm.log" 2>&1
  rc=$?
  set -e
  echo "DONE $TAG/$arm rc=$rc $(date -u +%FT%TZ)"
}

echo "[2/4] minting one frozen packet per arm"
mint_arm memphant    "$MEM_PR"
mint_arm agenticgrep "$GREP_PR"
mint_arm nomemory    "$EQ/stage-equalization.json"

echo "[3/4] running arms concurrently  $(date -u +%FT%TZ)"
run_arm memphant    evidence    "$MEM_PR" &
run_arm agenticgrep evidence    "$GREP_PR" &
run_arm nomemory    closed-book "$EQ/stage-equalization.json" &
wait

echo "[4/4] paired comparison  $(date -u +%FT%TZ)"
for endpoint in answer_correct correct; do
  python3 scripts/code_lane_reader_compare.py \
    --arm memphant="$OUT/reader-memphant.json" \
    --arm agenticgrep="$OUT/reader-agenticgrep.json" \
    --arm nomemory="$OUT/reader-nomemory.json" \
    --retrieval memphant="$MEM_PR" --retrieval agenticgrep="$GREP_PR" \
    --control agenticgrep \
    --control-description "an agentic grep/read loop over the attempt's raw events (no index, no embeddings, no memory state)" \
    --stage-manifest "$EQ/stage-equalization.json" \
    --bank "track-r-paraphrase-$TAG" --endpoint "$endpoint" \
    --claim "$CLAIM" --leakage "$LEAKAGE" \
    --provenance-class authored_from_target \
    --corpus-snapshot-id "$SNAPSHOT" --corpus-n-items "$N_ITEMS" \
    --license-id CC-BY-4.0 --license-source RECORD_METADATA \
    --license-evidence "nebius/SWE-rebench-openhands-trajectories dataset card, pinned in benchmarks/data/track_r_paraphrase_golden.lock.json corpus block" \
    --out "docs/build-log/artifacts/s10-conversion/s10-$TAG-$endpoint.json"
done
echo "=== S10 $TAG COMPLETE $(date -u +%FT%TZ) ==="
