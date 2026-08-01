#!/usr/bin/env bash
# Two paired code-lane arms on the Track R paraphrase bank, differing in ONE
# variable: the W8 cross-encoder rerank stage. Both are `bm25-code + dense`, the
# best arm in the W0.2 trunk ladder, so the contrast isolates the reranker.
#
# $0 spend: `fastembed` is the LOCAL bge-reranker-base ONNX model out of the
# on-disk cache. No hosted reranker, no API key, no paid call.
#
# LIMIT_ATTEMPTS=1 keeps every gold-referenced attempt and drops the rest. It is
# INERT for retrieval, not a sample: `bind_attempt_context` gives each attempt
# its own subject/scope/actor/agent_node, and recall is bound to that context,
# so a non-gold attempt's units can never enter any golden's candidate pool.
# The proof is free and is checked rather than asserted — the rerank-OFF arm
# must reproduce the full-corpus banked per-question vector exactly.
#
# MEMPHANT_FACT_EXTRACTION=0 is lineage, not a workaround: trunk's default-ON
# fact extraction breaks full-corpus drain on the memory_unit exclusion
# constraint (2026-07-31-w02-trunk-arms.md §6), and the arms this run must be
# comparable to all ran with it off.
set -euo pipefail
cd "$(dirname "$0")/.."

R="${RERANK_RUN_DIR:-$HOME/.memphant-private/track-r-paraphrase/run-rerank}"
mkdir -p "$R"
export MEMPHANT_FACT_EXTRACTION=0

arm() {
  local name="$1" port="$2"
  shift 2
  python3 scripts/code_lane_run_memphant.py \
    --database-url postgres://memphant:memphant@localhost:5432/memphant \
    --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
    --golden benchmarks/data/track_r_paraphrase_golden.jsonl \
    --out-evidence "$R/$name-evidence.jsonl" \
    --out-provenance "$R/$name-provenance.json" \
    --embed-model small --mode fast --k 10 --budget-tokens 8192 \
    --limit-attempts "${LIMIT_ATTEMPTS:-1}" \
    --lexical-scorer bm25-code --label "$name" --port "$port" \
    --server-bin target/release/memphant-server \
    --worker-bin target/release/memphant-worker \
    --cli-bin target/release/memphant-cli \
    "$@" >"$R/$name.log" 2>&1
  echo "$name rc=$?"
}

git rev-parse HEAD >"$R/git-head.txt"
shasum -a 256 target/release/memphant-server target/release/memphant-worker \
  target/release/memphant-cli >"$R/binaries.sha256"

arm rerank_off 39851 &
sleep 120
arm rerank_bge 39852 --cross-rerank --reranker fastembed &
wait
echo "both arms finished"
