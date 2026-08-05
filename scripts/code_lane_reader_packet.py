#!/usr/bin/env python3
"""Mint the frozen paid-authorization packet for a coding-lane reader-QA run.

`scripts/derive_phase2_packet.py` cannot be reused: it emits schema_version 3
with an `ISSUED_UNAUTHORIZED` status, a flat (not arm-keyed) `hard_limits`, no
`execution` block, no `campaign` block, and ~19 extra top-level keys — none of
which `run_reader.validate_paid_authorization` accepts, and the extra keys alone
would break `provider_attempts.open_campaign_ledger`'s scope hash. It is also
hardwired to two LongMemEval arms.

This mints the schema-2 packet both validators accept, for an arbitrary number
of arms, and prices the ceiling the same way the v3 packet does: one byte of the
serialized request body counted as one prompt token, at the recorded provider
maxima, plus a completion-token cap per call. Every input is measured from the
files on disk — no ceiling here is estimated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

HARD_CEILING_NANOS = 200_000_000_000
UNALLOCATED_RESERVE_NANOS = 10_000_000_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def widest_prompt_bytes(evidence_path: Path) -> int:
    """The widest single reader prompt this arm can produce, in bytes.

    Measured, not assumed: the largest evidence row's bodies plus the question,
    which is the row that bounds the per-call prompt liability.
    """
    widest = 0
    # Split on '\n' only: chat bodies can embed U+2028/U+2029, which
    # str.splitlines() would treat as line breaks mid-JSON-record — the same
    # convention run_reader.py's evidence loader already uses. splitlines()
    # here silently shredded LME session rows and aborted packet minting.
    for line in evidence_path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        size = len(row.get("question", "")) + len(str(row.get("gold_answer", "")))
        size += sum(len(item["body"]) for item in row["evidence"])
        widest = max(widest, size)
    return widest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", action="append", required=True,
                        metavar="NAME=EVIDENCE.jsonl:RETRIEVAL.json:OUTPUT.json",
                        help="repeatable; retrieval report may be '-' for a minted arm")
    parser.add_argument("--out", required=True, type=Path,
                        help="packet path; the ledger is written beside it")
    parser.add_argument("--ledger-name", default="attempts.jsonl")
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--reader-model", required=True)
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--judge-profile", default="rag-supported-v1")
    parser.add_argument("--prompt-version", type=int, default=1)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--price-prompt", required=True,
                        help="provider max_price ceiling, USD per million prompt tokens")
    parser.add_argument("--price-completion", required=True,
                        help="provider max_price ceiling, USD per million completion tokens")
    parser.add_argument("--calls-per-question", type=int, default=2,
                        help="logical calls per question (reader + judge)")
    parser.add_argument("--authorized-by", required=True)
    parser.add_argument("--authorized-at", required=True)
    parser.add_argument("--spend-headroom", default="3.0",
                        help="multiple of the derived ceiling used as the hard --max-spend-usd")
    args = parser.parse_args()

    out = args.out.resolve()
    ledger = out.parent / args.ledger_name
    price_prompt = Decimal(args.price_prompt)
    price_completion = Decimal(args.price_completion)

    arms: dict[str, dict] = {}
    for spec in args.arm:
        name, _, rest = spec.partition("=")
        evidence_s, retrieval_s, output_s = rest.split(":")
        evidence = Path(evidence_s).resolve()
        retrieval = None if retrieval_s == "-" else Path(retrieval_s).resolve()
        arms[name] = {
            "evidence": evidence,
            "retrieval": retrieval,
            "output": Path(output_s).resolve(),
            "n_rows": sum(
                # Split on '\n' only (see widest_prompt_bytes): splitlines()
                # over-counts rows whose bodies embed U+2028/U+2029, inflating
                # the logical-call count and the derived spend ceiling.
                1 for line in evidence.read_text(encoding="utf-8").split("\n") if line.strip()
            ),
            "widest_prompt_bytes": widest_prompt_bytes(evidence),
        }

    frozen = {
        "reader_runner_sha256": sha256_file(ROOT / "scripts/run_reader.py"),
        "provider_attempts_sha256": sha256_file(ROOT / "scripts/provider_attempts.py"),
    }
    for name, arm in arms.items():
        frozen[f"{name}_evidence_sha256"] = sha256_file(arm["evidence"])
        frozen[f"{name}_retrieval_sha256"] = (
            sha256_file(arm["retrieval"]) if arm["retrieval"] else None
        )

    models = {
        "provider": "OpenRouter",
        "reader": args.reader_model,
        "reader_reasoning_effort": args.reasoning_effort,
        "judge": args.judge_model,
        "judge_profile": args.judge_profile,
        "prompt_version": args.prompt_version,
        "max_output_tokens_per_request": args.max_output_tokens,
        "provider_max_price_usd_per_million": {
            "prompt": str(price_prompt),
            "completion": str(price_completion),
        },
    }

    hard_limits: dict[str, dict] = {}
    execution: dict[str, dict] = {}
    derivation: dict[str, dict] = {}
    for name, arm in arms.items():
        calls = arm["n_rows"] * args.calls_per_question
        # One byte of request body = one prompt token, the v3 packet's convention.
        # The judge prompt re-renders the same evidence, so the widest reader row
        # bounds the widest judge row too; +4096 covers the fixed scaffolding.
        per_call = (
            (Decimal(arm["widest_prompt_bytes"] + 4096) * price_prompt
             + Decimal(args.max_output_tokens) * price_completion)
            / Decimal(1_000_000)
        )
        ceiling = (per_call * Decimal(calls)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        hard = (ceiling * Decimal(args.spend_headroom)).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        hard_limits[name] = {
            "max_logical_calls": calls,
            # retries are provider attempts, not logical calls; 4 tries per call
            "max_provider_attempts": calls * 4,
            "max_spend_usd": str(hard),
        }
        execution[name] = {
            "run_id": name,
            "attempt_ledger": str(ledger.relative_to(ROOT)),
            "cache_dir": str(args.cache_dir.resolve().relative_to(ROOT)),
            "output": str(arm["output"].relative_to(ROOT)),
        }
        derivation[name] = {
            "n_rows": arm["n_rows"],
            "logical_calls": calls,
            "widest_prompt_bytes_measured": arm["widest_prompt_bytes"],
            "per_call_ceiling_usd": str(per_call),
            "arm_ceiling_usd": str(ceiling),
            "hard_max_spend_usd": str(hard),
        }

    campaign = {
        "journal_path": args.ledger_name,
        "hard_ceiling_nanos": HARD_CEILING_NANOS,
        "unallocated_reserve_nanos": UNALLOCATED_RESERVE_NANOS,
        "opening_liability_nanos": 0,
        "opening_reservations": [],
    }

    scope = {
        "frozen_inputs": frozen,
        "models": models,
        "hard_limits": hard_limits,
        "execution": execution,
        "campaign": campaign,
    }
    packet = {
        "schema_version": 2,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        **scope,
        "authorization": {
            "authorized_by": args.authorized_by,
            "authorized_at": args.authorized_at,
            "authorization_scope_sha256": sha256_json(scope),
        },
    }
    # Both validators must agree, and open_campaign_ledger hashes EVERY
    # top-level key except schema_version/status/authorization — so an extra
    # key here silently breaks the ledger. Assert the two hashes agree now.
    ledger_scope = {
        k: v for k, v in packet.items()
        if k not in {"schema_version", "status", "authorization"}
    }
    if sha256_json(ledger_scope) != packet["authorization"]["authorization_scope_sha256"]:
        raise RuntimeError("packet has a top-level key the ledger scope hash would reject")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet, indent=2) + "\n")
    # Do NOT create the journal. `_replay_journal` treats a zero-byte file as
    # truncated and refuses to open the campaign; the ledger writes its own
    # genesis event on first use, and an absent path is the valid empty state.
    print(json.dumps({
        "packet": str(out),
        "ledger": str(ledger),
        "total_derived_ceiling_usd": str(
            sum(Decimal(d["arm_ceiling_usd"]) for d in derivation.values())
        ),
        "derivation": derivation,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
