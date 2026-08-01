#!/usr/bin/env bash
# Kill-gate (b) arm launcher — the $0 retrieval-endpoint leg of the R6 decision.
#
# Two arms differing in EXACTLY one variable, each ingesting the pinned Syndai
# docs corpus once and recalling both golden sets (v1 + v2) against that one
# ingest:
#
# DEVIATION FROM THE §K.1 ARM SPEC, forced and recorded: §K.1 says "deep mode".
# `--mode deep` no longer means "exhaustive retrieval" — that RecallMode was
# removed (memphant-types asserts `"exhaustive"` no longer deserializes) and
# `deep` now routes through the L4 agentic provider, which returns
# 503 deep_unavailable unless MEMPHANT_DEEP=on with a paid OpenRouter model
# configured. A paid provider is incompatible with the $0 premise of this gate,
# and the L4 pass is not what (b) is testing: the cross-encoder rerank runs in
# the SHARED retrieval path (memphant-core `cross_rerank_candidates`, over the
# top `recall_pool_depth`=64 fused candidates) before packing, identically under
# either mode. So both arms run `--mode fast`, which is the retrieval endpoint
# the gate asks for and the only one reachable at $0.
#
#   base    modernbert, fast, --resource-chunks, k=10, budget 8192
#   rerank  identical + --cross-rerank --reranker byo --rerank-granularity chunk
#           --rerank-candidate-limit 64 --rerank-max-length 512
#
# RELEASE ONLY. A latency figure measured on target/debug is worthless, and the
# first attempt at this gate (2026-08-01, §K.4) silently did exactly that, so
# the profile is asserted here rather than assumed: the binaries must live under
# target/release and must not be older than the sources. `caffeinate -is` keeps
# the machine awake — sleep is what killed the previous attempt mid-drain.
#
# Corpus: the pinned tree 96a26f1f git-archived into SYNDAI_PIN and verified
# 114/114 files byte-identical against benchmarks/manifests/syndai_docs_gate.lock.json
# by gate_common.verify_corpus_contract at run start. The live Syndai checkout is
# never written to and has drifted off the pin; re-pinning is NOT available here
# because it would re-mine the goldens the whole comparison rests on.
#
# Usage: ARM=base|rerank bash run_arms.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../../.." && pwd)"
OUT="$(cd "$(dirname "$0")" && pwd)"
EV_DIR="${EV_DIR:?set EV_DIR (evidence dir; bodies are raw corpus text and stay out of git)}"
SYNDAI_PIN="${SYNDAI_PIN:?set SYNDAI_PIN (git-archive of the pinned corpus tree)}"
MINILM_DIR="${MINILM_DIR:-$HOME/.cache/memphant-byo-minilm}"
ARM="${ARM:?set ARM to base or rerank}"
PORT="${PORT:-39418}"

SERVER="$ROOT/target/release/memphant-server"
WORKER="$ROOT/target/release/memphant-worker"
CLI="$ROOT/target/release/memphant-cli"
for bin in "$SERVER" "$WORKER" "$CLI"; do
  [ -x "$bin" ] || { echo "missing release binary: $bin (cargo build --release --features fastembed)" >&2; exit 2; }
  case "$bin" in */target/release/*) ;; *) echo "refusing a non-release binary: $bin" >&2; exit 2;; esac
done

mkdir -p "$EV_DIR"
COMMON=(
  --syndai-root "$SYNDAI_PIN"
  --server-bin "$SERVER" --worker-bin "$WORKER" --cli-bin "$CLI"
  --embed-model modernbert --mode fast --k 10 --budget-tokens 8192
  --resource-chunks --port "$PORT" --label "$ARM"
  --golden "$ROOT/benchmarks/data/syndai_docs_golden.jsonl"
  --out-evidence "$EV_DIR/ev-$ARM-v1.jsonl" --out-provenance "$OUT/prov-$ARM-v1.json"
  --golden "$ROOT/benchmarks/data/syndai_docs_golden_v2.jsonl"
  --out-evidence "$EV_DIR/ev-$ARM-v2.jsonl" --out-provenance "$OUT/prov-$ARM-v2.json"
)

if [ "$ARM" = "rerank" ]; then
  export MEMPHANT_RERANK_BYO_DIR="$MINILM_DIR"
  export MEMPHANT_RERANK_TIMEOUT_MS=0   # never let a timeout silently degrade the arm
  COMMON+=(--cross-rerank --reranker byo --rerank-granularity chunk
           --rerank-candidate-limit 64 --rerank-max-length 512)
fi

# loadavg/cpu_count beside every latency figure: sibling worktrees share this host.
{ echo "arm=$ARM start=$(date -u +%FT%TZ) cpu_count=$(sysctl -n hw.ncpu)"; uptime; } \
  | tee "$OUT/host-$ARM.txt"

exec caffeinate -is python3 "$ROOT/scripts/gate_run_memphant.py" "${COMMON[@]}"
