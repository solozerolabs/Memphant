#!/usr/bin/env bash
# Drive one coding-lane reader-QA bank end to end: stage-equalize every arm,
# mint the frozen packet, then run each arm through run_reader.py and emit the
# paired comparison.
#
# The ledger takes an exclusive flock per journal, so arms run SEQUENTIALLY on
# purpose -- one campaign, one spend ledger, one resumable journal.
#
# Usage:
#   bash scripts/code_lane_reader_run.sh <bank> <run-dir> <memphant-evidence> \
#        <memphant-provenance> <control-evidence> <control-provenance> \
#        <corpus-sha> <golden-sha> <prompt-version>
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

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="docs/build-log/artifacts/track-r-paraphrase/reader/$BANK"
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
  --harness-env "bank=$BANK"

echo "[2/4] minting the frozen authorization packet"
rm -f "$OUT/attempts.jsonl" "$OUT/attempts.jsonl.lock"
rm -rf "$OUT/cache"
python3 scripts/code_lane_reader_packet.py \
  --arm memphant="$EQ/memphant-equalized-evidence.jsonl:$MEM_PR:$ROOT/$OUT/reader-memphant.json" \
  --arm bm25scoped="$EQ/bm25scoped-equalized-evidence.jsonl:$CTL_PR:$ROOT/$OUT/reader-bm25scoped.json" \
  --arm nomemory="$EQ/nomemory-equalized-evidence.jsonl:$EQ/stage-equalization.json:$ROOT/$OUT/reader-nomemory.json" \
  --out "$ROOT/$OUT/authorization.json" --cache-dir "$ROOT/$OUT/cache" \
  --reader-model anthropic/claude-opus-5 --judge-model anthropic/claude-opus-5 \
  --prompt-version "$PROMPT_VERSION" --price-prompt 5 --price-completion 25 \
  --authorized-by "owner 2026-07-31: no ceiling, necessity established" \
  --authorized-at "2026-07-31T16:00:00-07:00" | tee "$OUT/packet-derivation.json"

CALLS=$(python3 -c "import json;print(json.load(open('$OUT/authorization.json'))['hard_limits']['memphant']['max_logical_calls'])")
ATTEMPTS=$((CALLS * 4))

run_arm() {
  local arm="$1" profile="$2" retrieval="$3"
  local spend
  spend=$(python3 -c "import json;print(json.load(open('$OUT/authorization.json'))['hard_limits']['$arm']['max_spend_usd'])")
  echo "[3/4] arm=$arm profile=$profile spend_cap=$spend $(date -u +%FT%TZ)"
  doppler run --project syndai --config dev -- python3 scripts/run_reader.py \
    --engine openrouter --model anthropic/claude-opus-5 \
    --judge-model anthropic/claude-opus-5 --judge-profile rag-supported-v1 \
    --prompt-version "$PROMPT_VERSION" --reader-profile "$profile" \
    --provider-only anthropic \
    --evidence "$EQ/$arm-equalized-evidence.jsonl" --retrieval-report "$retrieval" \
    --out "$OUT/reader-$arm.json" --label "$BANK-$arm" \
    --cache-dir "$OUT/cache" --attempt-ledger "$OUT/attempts.jsonl" \
    --authorization-manifest "$OUT/authorization.json" --authorization-arm "$arm" \
    --max-calls "$CALLS" --max-provider-attempts "$ATTEMPTS" --max-spend-usd "$spend" \
    --max-price-prompt-per-million 5 --max-price-completion-per-million 25 \
    --max-output-tokens 1024
}

run_arm memphant   evidence    "$MEM_PR"
run_arm bm25scoped evidence    "$CTL_PR"
run_arm nomemory   closed-book "$EQ/stage-equalization.json"

echo "[4/4] paired comparison"
for endpoint in answer_correct correct; do
  python3 scripts/code_lane_reader_compare.py \
    --arm memphant="$OUT/reader-memphant.json" \
    --arm bm25scoped="$OUT/reader-bm25scoped.json" \
    --arm nomemory="$OUT/reader-nomemory.json" \
    --control bm25scoped --stage-manifest "$EQ/stage-equalization.json" \
    --bank "$BANK" --endpoint "$endpoint" \
    --out "docs/build-log/artifacts/track-r-paraphrase/phase3-reader-qa-$BANK-$endpoint.json"
done
echo "=== $BANK COMPLETE $(date -u +%FT%TZ) ==="
