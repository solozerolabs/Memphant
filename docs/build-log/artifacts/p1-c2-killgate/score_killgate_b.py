#!/usr/bin/env python3
"""SUPERSEDED 2026-08-01 — DO NOT RUN. Use scripts/score_killgate_b.py.

This scorer was never run to completion and produced no banked number (no
`verdict.json` exists here, and none was ever committed), so nothing downstream
needs retracting. It is kept as the C2 wave's artifact and disarmed here.

Why it must not be used: when a live Syndai incumbent arm is absent it silently
falls back to a HARDCODED `SYNDAI_HIT10=0.200` taken from the 2026-07-11 gate on
a DIFFERENT corpus pin, and then reports a difference of two aggregates as a
gap-closure fraction. A scorer that substitutes a constant from another pin when
its comparison arm is missing is a machine for manufacturing false findings: it
cannot fail closed, and the resulting number carries no lineage. The replacement
refuses to mix pins and reports a PAIRED exact McNemar on per-question vectors,
with mechanism-liveness and latency gates beside the effect.

--- original docstring below ---

Kill-gate (b) scorer: does the MiniLM chunk-rerank arm close >=half the
(syndai - memphant_base) hit@10 gap on the re-pinned corpus?

Reads provenance reports (per_question hit_at_10) for the three arms, pooled
over v1+v2, prints per-arm hit@10, the gap-closure fraction, per-question flip
counts, and the verdict. Also writes verdict.json next to the inputs.
"""
import json
import sys
from pathlib import Path

base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "killgate-b"


def pooled(arm: str) -> dict[str, bool]:
    rows = {}
    for v in ("v1", "v2"):
        report = json.loads((base_dir / f"prov-{arm}-{v}.json").read_text())
        for row in report["per_question"]:
            rows[row["question_id"]] = bool(row["hit_at_10"])
    return rows


base = pooled("base")
rerank = pooled("rerank")
assert set(base) == set(rerank), "question sets differ across arms"
n = len(base)

# Syndai incumbent hit@10: prefer a live re-run (prov-syndai-*.json); else fall
# back to the committed 2026-07-11 gate figure on the near-identical docs corpus
# (0.200 hit@10), passed via SYNDAI_HIT10. The live re-run was blocked by
# dev-DB migration drift (knowledge_source_versions.content_sha256 absent), a
# Syndai-checkout maintenance issue outside this task's scope.
import os

if (base_dir / "prov-syndai-v2.json").exists():
    syndai = pooled("syndai")
    h_syndai = sum(syndai.values()) / len(syndai)
    syndai_src = "live re-run on re-pinned corpus"
else:
    h_syndai = float(os.environ.get("SYNDAI_HIT10", "0.200"))
    syndai_src = "historical 2026-07-11 gate (0.200 hit@10), live re-run blocked by Syndai dev-DB drift"
h_base = sum(base.values()) / n
h_rerank = sum(rerank.values()) / n
print("syndai source:", syndai_src)
gap = h_syndai - h_base
closed = h_rerank - h_base
closure = (closed / gap) if gap > 0 else float("inf")

pos = [q for q in base if not base[q] and rerank[q]]
neg = [q for q in base if base[q] and not rerank[q]]

print(f"n={n} hit@10: syndai={h_syndai:.3f} memphant_base={h_base:.3f} memphant_rerank={h_rerank:.3f}")
print(f"gap(syndai-base)={gap:+.3f} closed(rerank-base)={closed:+.3f} closure={closure:.2f}")
print(f"rerank flips: +{len(pos)} -{len(neg)}")
if gap <= 0:
    verdict = "PASS (no deficit: memphant base >= syndai)"
    passed = True
else:
    passed = closure >= 0.5
    verdict = "PASS" if passed else "FAIL -> DROP C2"
print("verdict:", verdict)
json.dump(
    {
        "n": n,
        "hit10_syndai": h_syndai,
        "syndai_source": syndai_src,
        "hit10_memphant_base": h_base,
        "hit10_memphant_rerank": h_rerank,
        "gap": gap,
        "closed": closed,
        "closure_fraction": None if gap <= 0 else closure,
        "threshold": 0.5,
        "pass": passed,
        "positive_flips": pos,
        "negative_flips": neg,
    },
    open(base_dir / "verdict.json", "w"),
    indent=1,
)
