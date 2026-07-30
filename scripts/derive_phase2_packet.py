#!/usr/bin/env python3
"""Derive the Phase 2 reader-QA authorization packet (schema_version 3).

Phase 0 of docs/superpowers/plans/2026-07-27-accuracy-first-program.md. This
script exists so the packet's call counts and spend ceiling are *derived from
measured bytes on disk*, not transcribed. Rerunning it must reproduce the packet
byte-identically; that is the committed check.

Convention carried from the v2 packet: one-byte-per-prompt-token liability
(conservative — real tokenizers pack >1 byte/token), 1024 completion tokens per
call, priced at the provider's recorded maximum. Reader and judge are both
bounded at the reader's recorded max prices, and the runner must refuse to
launch if a live judge price exceeds them (see judge_price_guard).

Usage: python3 scripts/derive_phase2_packet.py [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "docs/build-log/artifacts/rung7-packing-reader-gate"
OUT = GATE / "authorization-request.v3.json"

# Phase 2 pool composition (plan §Phase 2).
FROZEN_ROWS = 178          # hash-pinned evidence already on disk, per arm
BURNED_ROWS = 60           # already-exposed questions, evidence emitted free
ABS_VARIANTS_MAX = 60      # trap-preserving _abs variants to mint (40-60)
ARMS = 2                   # baseline, cap-1200
COMPLETION_TOKENS = 1024

# Recorded provider maxima for openai/gpt-5.6-terra via OpenRouter, carried
# from the v2 packet's models.provider_max_price_usd_per_million.
PRICE_PROMPT_PER_M = 2.75
PRICE_COMPLETION_PER_M = 16.5


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure_arm(name: str) -> dict:
    """Measure the frozen evidence rows for one arm."""
    path = GATE / f"{name}-evidence.jsonl"
    # str.splitlines() also breaks on U+2028/U+0085/\x0b/\x0c, which occur
    # inside these evidence bodies; only \n delimits rows here.
    with path.open(encoding="utf-8", newline="\n") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    widths = [len(json.dumps(r, ensure_ascii=False).encode()) for r in rows]
    return {
        "evidence_file": path.name,
        "evidence_sha256": sha256(path),
        "rows": len(rows),
        "abstention_rows": sum(1 for r in rows if r["is_abstention"]),
        "max_row_bytes": max(widths),
        "mean_row_bytes": round(sum(widths) / len(widths)),
    }


def build() -> dict:
    arms = {name: measure_arm(name) for name in ("baseline", "rendercap1200")}

    # Worst-case prompt bound: the widest frozen row across both arms, rounded
    # up to the next 1000 bytes. The 60 burned rows and the minted variants are
    # drawn from the same corpus and pack to the same 8192-token budget, so the
    # measured maximum bounds them too.
    widest = max(a["max_row_bytes"] for a in arms.values())
    prompt_token_bound = -(-widest // 1000) * 1000

    per_arm_questions = FROZEN_ROWS + BURNED_ROWS + ABS_VARIANTS_MAX
    reader_calls = per_arm_questions * ARMS
    judge_calls = reader_calls                       # one adjudication per answer
    # Worst case: every pair is discordant and needs a swapped-order re-judging.
    paired_recheck_calls = per_arm_questions
    # Minting: one generation + one adjudication per candidate variant.
    minting_calls = ABS_VARIANTS_MAX * 2

    logical_calls = reader_calls + judge_calls + paired_recheck_calls + minting_calls

    per_call_usd = (
        prompt_token_bound * PRICE_PROMPT_PER_M / 1_000_000
        + COMPLETION_TOKENS * PRICE_COMPLETION_PER_M / 1_000_000
    )
    ceiling = logical_calls * per_call_usd

    # Scored-row accounting for the primary endpoint. The frozen 178 carry 12
    # natural _abs rows (measured); the 60 burned rows carry the remaining 5 of
    # the 17 natural unsealed _abs cases named in the plan.
    natural_abs_frozen = arms["baseline"]["abstention_rows"]
    natural_abs_total = 17
    pool_238 = FROZEN_ROWS + BURNED_ROWS
    scored_rows_238 = pool_238 - natural_abs_total

    return {
        "schema_version": 3,
        "supersedes": "authorization-request.json",
        "supersedes_status": "SUPERSEDED_REJECTED_BY_2026_07_24_FREE_EXACT_ABSTENTION_GATE",
        "reissue_basis": "docs/build-log/2026-07-30-packing-gate-amendment.md",
        "plan": "docs/superpowers/plans/2026-07-27-accuracy-first-program.md",
        "status": "ISSUED_UNAUTHORIZED",
        "authorization": None,
        "paid_calls_executed": 0,
        "settled_cost_usd": "0",
        "blocking_preconditions": [
            "Owner signature on this packet (authorization is null).",
            "Phase 0 landed: the gate amendment is committed.",
            "Phase 1 kill gates passed: the Track R golden bank met its preregistered bar, and either Phase 1b shows the per-item-cost pathology recurs on code bodies OR a decision-register entry names who valued the chat lane on its own.",
            "Analysis code committed before unblinding.",
        ],
        "lever_under_test": {
            "name": "MEMPHANT_PACK_RENDER_CAP",
            "arms": ["baseline (cap off)", "cap=1200"],
            "construction_time_only": True,
            "pack_budget_tokens": 8192,
            "k": 10,
            "recorded_free_retrieval_result": {
                "baseline_recall_at_10": 0.6144578313253012,
                "cap1200_recall_at_10": 0.8433734939759037,
                "source": "v2 packet free_packaged_rehearsal (unchanged)",
            },
        },
        "frozen_inputs": {
            "longmemeval_s_development_sha256": "e4667bed29565884b827ca0a75fbbec8d15f772c96011bb058ea5e2863d3a475",
            "baseline_retrieval_sha256": sha256(GATE / "baseline-retrieval.json"),
            "rendercap1200_retrieval_sha256": sha256(GATE / "rendercap1200-retrieval.json"),
            "arms": arms,
        },
        "pool": {
            "frozen_rows_per_arm": FROZEN_ROWS,
            "burned_rows_per_arm": BURNED_ROWS,
            "burned_rows_evidence_source": "free deterministic `bench-lme --emit-qa` run per arm",
            "current_exposure_pool": pool_238,
            "natural_abstention_rows_frozen_measured": natural_abs_frozen,
            "natural_abstention_rows_pool_total": natural_abs_total,
            "scored_rows_pool_238": scored_rows_238,
            "abs_variants_to_mint_max": ABS_VARIANTS_MAX,
            "abs_variants_to_mint_min": 40,
            "dual_analysis": [
                "frozen-178 subset (lattice-comparable to the recorded evidence)",
                "full-238 pool (powered)",
            ],
        },
        "endpoints": {
            "primary": {
                "name": "paired McNemar on answer correctness",
                "rows": scored_rows_238,
                "d_min_points": 7,
                "power_note": "~80% at psi~=0.15; 5pt is undecidable on unsealed material",
                "pre_commitment": "|delta| < 7pt is recorded as NO FLIP, not as a trend",
            },
            "secondary_abstention": {
                "name": "reader-judged abstention",
                "definition": "abstain=true AND answer=null",
                "construct": "answer session withheld, near-miss traps KEPT in the haystack",
                "rejected_construct": "gold-ablation 'abstain on empty evidence' probes",
                "rows": "17 natural unsealed + 40-60 minted variants",
                "blocking": "a net abstention regression blocks promotion regardless of the primary result",
                "adjudication": "every minted variant adjudicated before freezing",
            },
            "two_sided_naming": {
                "misapplication_rate": "memory applied when it should not be",
                "appropriate_application_rate": "memory applied when it should be",
                "reason": "so a suppression win cannot masquerade as an application win",
            },
        },
        "models": {
            "provider": "OpenRouter",
            "reader": "openai/gpt-5.6-terra",
            "reader_reasoning_effort": "medium",
            "judge": "anthropic/claude-sonnet-5",
            "judge_profile": "rag-supported-v1",
            "prompt_version": 3,
            "max_output_tokens_per_request": COMPLETION_TOKENS,
            "provider_max_price_usd_per_million": {
                "prompt": str(PRICE_PROMPT_PER_M),
                "completion": str(PRICE_COMPLETION_PER_M),
            },
            "judge_price_guard": "the runner must refuse to launch if the live judge price exceeds the reader maxima above; the ceiling prices every call, reader and judge alike, at those maxima",
            "lattice_override": "RECORDED. This lane keeps the terra/sonnet pair against the standing Sol-finalist judge designation, because the only frozen paired evidence lives on this pair and switching lattices orphans it. Same-lattice discipline holds within the lane, including the confirmation.",
        },
        "hard_limits": {
            "prompt_token_bound_per_call": prompt_token_bound,
            "widest_measured_frozen_row_bytes": widest,
            "reader_calls": reader_calls,
            "judge_calls": judge_calls,
            "paired_recheck_calls_worst_case": paired_recheck_calls,
            "minting_calls": minting_calls,
            "combined_max_logical_calls": logical_calls,
            "combined_max_provider_attempts": logical_calls,
            "max_usd_per_call": f"{per_call_usd:.8f}",
            "combined_max_spend_usd": f"{ceiling:.8f}",
            "realistic_expected_spend_usd": "30-60",
            "realistic_basis": "observed $0.02-0.03 per call on this lattice",
            "derivation": (
                "One-byte-per-prompt-token liability at the widest measured frozen "
                f"evidence row ({widest} bytes, rounded to {prompt_token_bound}) plus "
                f"{COMPLETION_TOKENS} completion tokens, priced at the provider maxima, "
                "times the enumerated call budget. Provider attempts equal logical "
                "calls, so retries cannot expand the authorized attempt count. "
                "Derived by scripts/derive_phase2_packet.py; rerun to verify."
            ),
        },
        "guardrails": {
            "latency": "promotion re-runs the $0 SLO harness (p50<200ms / p95<500ms). The cap is construction-time-only at an identical 8192 budget, so neutrality is expected -- but assumed-neutral is not preregistered-neutral.",
            "reader_tokens": "record the reader-token delta beside the accuracy endpoint",
            "scratch_db_only": True,
            "no_gold_or_answer_session_ids_in_prompts": True,
        },
        "promotion_requirements": {
            "confirmation": {
                "set": "sealed-259",
                "spend_policy": "spent exactly once; exposure recorded immediately after",
                "claim_language": "answer-session disjoint",
                "forbidden_claim": "fully held out (strict all-haystack-disjoint count is 0)",
            },
            "robustness_arm": {
                "reader": "claude-opus-5",
                "rationale": "the model Syndai's Claude Code executor serves as its workhorse default (backend/src/features/coding/harness_models.py:34)",
                "lattice": "frozen as its own lattice",
                "bar": "direction agreement, not significance",
                "reason": "a default that only wins on the eval-lattice reader is fragile and unrepresentative of deployed traffic",
                "pricing": "pinned at authorization time; not priced in the ceiling above, which covers the screening run only",
            },
        },
        "non_claims": [
            "This packet authorizes nothing; authorization is null.",
            "Re-issuing the packet does not promote pack_render_cap on any lane.",
            "The rescinded free exact-abstention sentinel is not evidence for or against the lever.",
            "No checkbox, default, cutover, deployment, or SOTA claim moves on issuance.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify the committed packet matches")
    args = parser.parse_args()

    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current != text:
            print(f"packet_drift={OUT}", file=sys.stderr)
            return 1
        print(f"packet_ok={OUT} sha256={hashlib.sha256(text.encode()).hexdigest()}")
        return 0
    OUT.write_text(text)
    print(f"wrote={OUT} sha256={hashlib.sha256(text.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
