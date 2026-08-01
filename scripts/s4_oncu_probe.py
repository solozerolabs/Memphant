#!/usr/bin/env python3
"""S4 — the ONCU (no-corpus, no-tools) contamination probe.

Preregistration A.7. Twenty questions answered by the same model the agentic
control uses, with **no corpus access and no tools**.

Why this is load-bearing here and not ceremony: the agentic control's tool is
`grep`. If the model already knows the answer from pretraining, it can grep for
the ANSWER STRING and land on the gold event without doing any search at all.
That would inflate C1 specifically — the arm whose win would be the expensive
finding — so the probe bounds a bias that runs against the treatment, which is
the direction that matters.

Grading reuses `gate_common.contains_gold`, the same normalized word-boundary
containment every other endpoint in this program grades with.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gate_common as gc  # noqa: E402
import s4_controls_common as s4  # noqa: E402
from code_lane_run_agentic_control import (  # noqa: E402
    MAX_PRICE_PER_MILLION,
    OpenRouterEngine,
)

SEED = 20260801
PROMPT = (
    "Answer from your own knowledge alone. You have no documents and no tools. "
    "If you do not know, reply exactly: UNKNOWN. Reply with the answer only, no "
    "explanation.\n\nQUESTION: "
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()

    import os

    _, goldens, lock = s4.load_contract(args.corpus, args.golden)
    sample = random.Random(SEED).sample(goldens, args.n)
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
        "OPEN_ROUTER_API_KEY"
    )
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY unset (use `doppler run`)")
    engine = OpenRouterEngine(api_key)

    rows = []
    for position, golden in enumerate(sample, 1):
        messages = [{"role": "user", "content": PROMPT + golden["question"]}]
        response = engine.complete_plain(messages)
        answer = (response["message"].get("content") or "").strip()
        correct = bool(answer) and gc.contains_gold(answer, str(golden["gold_answer"]))
        rows.append(
            {
                "question_id": golden["question_id"],
                "answer": answer[:400],
                "gold_answer": golden["gold_answer"],
                "answered_correctly_without_evidence": correct,
                "said_unknown": answer.upper().startswith("UNKNOWN"),
            }
        )
        print(f"[{position}/{len(sample)}] {golden['question_id']} correct={correct}", flush=True)

    contaminated = [row for row in rows if row["answered_correctly_without_evidence"]]
    spend = (
        Decimal(engine.usage["prompt_tokens"]) * MAX_PRICE_PER_MILLION["prompt"]
        + Decimal(engine.usage["completion_tokens"]) * MAX_PRICE_PER_MILLION["completion"]
    ) / Decimal(1_000_000)
    report = {
        "probe": "oncu_no_corpus_no_tools",
        "model": "anthropic/claude-opus-5",
        "seed": SEED,
        "n": len(rows),
        "contaminated": len(contaminated),
        "contaminated_rate": round(len(contaminated) / len(rows), 4),
        "contaminated_ids": [row["question_id"] for row in contaminated],
        "rows": rows,
        "usage": engine.usage,
        "reported_spend_usd": float(round(spend, 4)),
        "golden_sha256": lock["sha256"],
        "lineage": s4.lineage({"golden": args.golden}),
        "interpretation": (
            "A question answerable with no evidence lets a grep-driven control "
            "search for the ANSWER rather than search for the evidence. That "
            "bias inflates the agentic control, not the treatment."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("n", "contaminated", "reported_spend_usd")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
