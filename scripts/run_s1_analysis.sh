#!/usr/bin/env bash
# S1 analysis. Runs the preregistered pairwise comparisons on both slices.
#
# Slicing is a FILTER ON `rows`, applied identically to both arms before
# `preference_lane_analysis.py` sees them -- the same mechanism B1 §11 used, and
# the analysis script itself refuses two arms whose probe banks differ, so a
# mis-sliced pair fails closed rather than reporting a wrong pairing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/docs/build-log/artifacts/2026-08-01-similarity-unit-swap}"
AN="$OUT/analysis"
PY="${PY:-python3}"
mkdir -p "$AN" "$OUT/slices"

# Confirmatory slice: sha256(group_id) % 4 != 0. The dev residue (== 0) is where
# B1's tau = 0.25 was calibrated, so any comparison INVOLVING ARM S is
# dev-contaminated on the full bank.
slice_arm() {
  local src="$1" dst="$2"
  "$PY" - "$src" "$dst" <<'EOF'
import hashlib, json, sys
src, dst = sys.argv[1], sys.argv[2]
report = json.loads(open(src).read())
h = lambda g: int(hashlib.sha256(g.encode()).hexdigest(), 16) % 4
report["rows"] = [r for r in report["rows"] if h(r["group_id"]) != 0]
report["slice"] = "confirmatory (sha256(group_id) %% 4 != 0)"
open(dst, "w").write(json.dumps(report))
print(f"{dst}: {len(report['rows'])} probes / "
      f"{len({r['group_id'] for r in report['rows']})} instances")
EOF
}

for a in n-noop s-body u-sentence r3-random; do
  [ -s "$OUT/arm-$a.json" ] || { echo "missing arm artifact: $OUT/arm-$a.json" >&2; exit 2; }
  slice_arm "$OUT/arm-$a.json" "$OUT/slices/arm-$a.confirmatory.json"
done

pair() {
  local a="$1" b="$2" tag="$3" claim="$4"
  for slice in full confirmatory; do
    if [ "$slice" = full ]; then
      pa="$OUT/arm-$a.json"; pb="$OUT/arm-$b.json"
    else
      pa="$OUT/slices/arm-$a.confirmatory.json"; pb="$OUT/slices/arm-$b.confirmatory.json"
    fi
    echo "=== $tag [$slice] ==="
    "$PY" "$ROOT/scripts/preference_lane_analysis.py" \
      --arm-a "$pa" --arm-b "$pb" --out "$AN/$tag.$slice.json" \
      --claim "$claim (slice: $slice)" \
      --notes "S1 similarity-unit swap. Same tree, same server/worker/cli sha256, same corpus sha256, same probe bank, same stage; the delta is within-run. MemoryCode's gold is recency-identified and wrong retirement costs a gold only 5.2% of the time, so this corpus COMPRESSES the effect: a positive is a lower bound and a null does not establish the swap is worthless."
  done
}

pair u-sentence s-body      u-vs-s  "The similarity UNIT swapped from whole session body to best-matching directive sentence, at a rate-matched threshold, holding everything else fixed."
pair u-sentence n-noop      u-vs-n  "Sentence-unit id-named supersession against its own no-op isolator."
pair u-sentence r3-random   u-vs-r3 "Sentence-unit supersession against a rate-matched random target policy -- the SEMANTIC increment."
pair s-body     n-noop      s-vs-n  "B1's arm S re-run on this tree against its no-op isolator."
pair s-body     r3-random   s-vs-r3 "B1's arm S re-run on this tree against the rate-matched random ablation."

echo "=== S1 analysis complete: $AN ==="
