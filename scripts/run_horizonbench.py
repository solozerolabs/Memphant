#!/usr/bin/env python3
"""Fail-closed HorizonBench acquisition, runtime, reader, and analysis runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import gate_runtime as gr  # noqa: E402
from instrument_power import min_detectable_effect  # noqa: E402
from provider_attempts import (  # noqa: E402
    CAMPAIGN_HARD_CEILING_NANOS,
    CAMPAIGN_UNALLOCATED_RESERVE_NANOS,
    open_campaign_ledger,
    provider_attempt_ledger_is_complete,
)
from run_reader import (  # noqa: E402
    CallBudgetExceeded,
    ProviderRefusal,
    ReaderCli,
    parse_reader_output,
    restore_spend_from_attempts,
)


DATASET_ID = "stellalisy/HorizonBench"
DATASET_REVISION = "50941f00f90c03a5a60219d76393869b757b835a"
DATASET_SERVER = "https://datasets-server.huggingface.co"
HUB_API = "https://huggingface.co/api/datasets"
SCORING_ONLY_FIELDS = (
    "correct_letter",
    "distractor_letter",
    "has_evolved",
    "preference_domain",
    "preference_evolution",
)
PROMPT_FIELDS = ("id", "user_id", "generator", "conversation", "options")
MAX_EPISODE_BYTES = 120_000
RECALL_QUERY_CHARS = 8_000
ESCAPED_CODEPOINT = re.compile(r"\\u([0-9a-fA-F]{4})")
PAID_ARMS = ("full_context", "fast", "selective_deep")
CONFIRMATION_ARMS = ("full_context", "fast")
READER_MODEL = "anthropic/claude-opus-4.5"
CONFIRMATION_READER_MODEL = "claude-opus-4-6"
READER_PROVIDER = "anthropic"
READER_MAX_SPEND_USD = Decimal("22")
DEEP_MODEL = "openai/gpt-5.6-luna-20260709"
DEEP_PROVIDER = "azure"
DEEP_MAX_SPEND_USD = Decimal("3")
COMBINED_MAX_SPEND_USD = Decimal("25")
CONFIRMATION_MAX_SPEND_USD = Decimal("140")
CONFIRMATION_MAX_OUTPUT_TOKENS = 1024
READER_SYSTEM_PROMPT = (
    "Choose the response A-E that best matches the user's current preference using "
    "only the supplied evidence. If the evidence is insufficient, set abstain=true "
    "and answer=null. Otherwise set abstain=false and answer to exactly one uppercase "
    "letter A-E. Return the required JSON object; notes must be brief."
)
BENCHMARK_GENERATOR_COUNTS = {
    "sonnet-4.5": 1052,
    "o3": 981,
    "gemini-3-flash": 2212,
}
BENCHMARK_SOURCE_FILES = (
    (
        "benchmark/test-00000-of-00006.parquet",
        375_406_101,
        "644c54d3bae551be4a4dc1e9ff1d9d15cd345187704188ecfa8974dea5ff12a2",
    ),
    (
        "benchmark/test-00001-of-00006.parquet",
        325_890_406,
        "dec20b351f452557341e44eb4a930c68c4d281e452ed9ed307b7980c87848639",
    ),
    (
        "benchmark/test-00002-of-00006.parquet",
        231_533_473,
        "f5c79c3b0a91102085226ccbd33d154a0c47ec8c5cccc3a7e327e58cc265d314",
    ),
    (
        "benchmark/test-00003-of-00006.parquet",
        221_745_352,
        "aa454abf71cb5eee0071cec4a66b45b23847303bbd8e4a7443e24ddd35fb0b91",
    ),
    (
        "benchmark/test-00004-of-00006.parquet",
        223_939_127,
        "44cc2e4a02c7be37cfcdbeb97e8e73d7ca516bb7d2b5d3f741200660522d7c98",
    ),
    (
        "benchmark/test-00005-of-00006.parquet",
        223_441_658,
        "d5c4b6a418ebcaa904956aba5adc2e200e953211ade258501c3a041309d1154e",
    ),
)
GRAPH_SOURCE_FILES = (
    (
        "mental_state_graphs/test-00000-of-00003.parquet",
        202_913_277,
        "c582dbb76fe3231636a32e940b4e63cb384a04e2bedf01128373e4c7ab887174",
    ),
    (
        "mental_state_graphs/test-00001-of-00003.parquet",
        190_023_603,
        "f02f674ff46d209cb41eb77d566779e34830f35f3c4d8f9edbafc8506d20f525",
    ),
    (
        "mental_state_graphs/test-00002-of-00003.parquet",
        169_945_684,
        "8a52a598b748b15c3c9c1ebc3392425a0a7c44d3ce65ed65573419ab6d2ec161",
    ),
)
EXPECTED_TIMELINE_DRIFT_USERS = {
    "gemini-3-flash/user_15",
    "gemini-3-flash/user_49",
}


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def authorization_packet(
    frozen_inputs: dict, *, authorized_by: str, authorized_at: str
) -> dict:
    packet = {
        "schema_version": 1,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        "frozen_inputs": frozen_inputs,
        "models": {
            "reader": READER_MODEL,
            "reader_provider": READER_PROVIDER,
            "reader_prompt_sha256": hashlib.sha256(
                READER_SYSTEM_PROMPT.encode()
            ).hexdigest(),
            "temperature": 0,
            "max_output_tokens": 256,
            "reader_price_usd_per_million": {
                "prompt": "5",
                "completion": "25",
            },
            "deep": DEEP_MODEL,
            "deep_provider": DEEP_PROVIDER,
            "deep_price_usd_per_million": {
                "prompt": "1.1",
                "completion": "6.6",
            },
        },
        "hard_limits": {
            "reader": {
                "max_logical_calls": 30,
                "max_provider_attempts": 60,
                "max_spend_usd": str(READER_MAX_SPEND_USD),
            },
            "deep": {
                "max_calls": 10,
                "max_spend_per_call_usd": "0.30",
                "max_spend_usd": str(DEEP_MAX_SPEND_USD),
            },
            "combined_max_spend_usd": str(COMBINED_MAX_SPEND_USD),
        },
        "execution": {
            "journal_path": "reader-attempts.jsonl",
            "cache_dir": "reader-cache",
            "raw_rows": "paid-rows.jsonl",
            "deep_cache": "deep-evidence.jsonl",
            "closure": "reader-closure.json",
            "census": "paid-census.json",
        },
        "campaign": {
            "journal_path": "reader-attempts.jsonl",
            "hard_ceiling_nanos": CAMPAIGN_HARD_CEILING_NANOS,
            "unallocated_reserve_nanos": CAMPAIGN_UNALLOCATED_RESERVE_NANOS,
            "opening_liability_nanos": 0,
            "opening_reservations": [],
        },
        "claim_boundary": "Ten-row diagnostic only; no SOTA or default promotion.",
    }
    scope = {
        key: value
        for key, value in packet.items()
        if key not in {"schema_version", "status", "authorization"}
    }
    packet["authorization"] = {
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "authorization_scope_sha256": sha256_json(scope),
    }
    return packet


def validate_pilot_authorization(packet: dict, expected_frozen: dict) -> None:
    authorization = packet.get("authorization") if isinstance(packet, dict) else None
    if (
        not isinstance(authorization, dict)
        or not isinstance(authorization.get("authorized_by"), str)
        or not authorization["authorized_by"].strip()
        or not isinstance(authorization.get("authorized_at"), str)
        or not authorization["authorized_at"].strip()
    ):
        raise ValueError("paid authorization is missing owner approval")
    expected = authorization_packet(
        expected_frozen,
        authorized_by=authorization["authorized_by"],
        authorized_at=authorization["authorized_at"],
    )
    if packet != expected:
        raise ValueError("paid authorization scope or frozen input drift")


def confirmation_authorization_packet(
    frozen_inputs: dict,
    *,
    preflight: dict,
    authorized_by: str,
    authorized_at: str,
) -> dict:
    if (
        preflight.get("status") != "passed"
        or Decimal(preflight["estimated_total_usd"])
        > Decimal(preflight["authorized_ceiling_usd"])
        or Decimal(preflight["authorized_ceiling_usd"])
        != CONFIRMATION_MAX_SPEND_USD
    ):
        raise ValueError("paid confirmation cost preflight did not pass")
    packet = {
        "schema_version": 1,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        "campaign_type": "horizonbench_confirmation",
        "frozen_inputs": frozen_inputs,
        "arms": list(CONFIRMATION_ARMS),
        "cost_preflight": preflight,
        "models": {
            "reader": CONFIRMATION_READER_MODEL,
            "reader_engine": "anthropic",
            "reader_provider": READER_PROVIDER,
            "reader_prompt_sha256": hashlib.sha256(
                READER_SYSTEM_PROMPT.encode()
            ).hexdigest(),
            "temperature": 0,
            "max_output_tokens": CONFIRMATION_MAX_OUTPUT_TOKENS,
            "reader_price_usd_per_million": {
                "prompt": "5",
                "completion": "25",
            },
        },
        "hard_limits": {
            "max_logical_calls": 240,
            "max_provider_attempts": 480,
            "max_spend_usd": str(CONFIRMATION_MAX_SPEND_USD),
        },
        "execution": {
            "journal_path": "reader-attempts.jsonl",
            "cache_dir": "reader-cache",
            "raw_rows": "paid-rows.jsonl",
            "closure": "reader-closure.json",
            "census": "paid-census.json",
        },
        "campaign": {
            "journal_path": "reader-attempts.jsonl",
            "hard_ceiling_nanos": CAMPAIGN_HARD_CEILING_NANOS,
            "unallocated_reserve_nanos": CAMPAIGN_UNALLOCATED_RESERVE_NANOS,
            "opening_liability_nanos": 0,
            "opening_reservations": [],
        },
        "claim_boundary": (
            "Sixty-user paired HorizonBench confirmation only; the complete "
            "4,245-item treatment and every cross-axis SOTA claim remain unauthorized."
        ),
    }
    scope = {
        key: value
        for key, value in packet.items()
        if key not in {"schema_version", "status", "authorization"}
    }
    packet["authorization"] = {
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "authorization_scope_sha256": sha256_json(scope),
    }
    return packet


def validate_confirmation_authorization(
    packet: dict, expected_frozen: dict, expected_preflight: dict
) -> None:
    authorization = packet.get("authorization") if isinstance(packet, dict) else None
    if (
        not isinstance(authorization, dict)
        or not isinstance(authorization.get("authorized_by"), str)
        or not authorization["authorized_by"].strip()
        or not isinstance(authorization.get("authorized_at"), str)
        or not authorization["authorized_at"].strip()
    ):
        raise ValueError("paid confirmation authorization is missing owner approval")
    expected = confirmation_authorization_packet(
        expected_frozen,
        preflight=expected_preflight,
        authorized_by=authorization["authorized_by"],
        authorized_at=authorization["authorized_at"],
    )
    if packet != expected:
        raise ValueError("paid confirmation authorization scope or frozen input drift")


def selective_route(fast_row: dict) -> str:
    if fast_row.get("status") != "completed":
        raise ValueError("selective routing requires a completed Fast reader row")
    if fast_row.get("abstain") is True and fast_row.get("answer") is None:
        return "deep"
    answer = fast_row.get("answer")
    if fast_row.get("abstain") is False and answer in list("ABCDE"):
        return "fast"
    raise ValueError("selective routing received an invalid Fast reader row")


def validate_deep_completion(row: dict) -> None:
    deep = row.get("deep")
    if (
        row.get("degraded") is not False
        or not isinstance(row.get("evidence"), list)
        or not row["evidence"]
        or not isinstance(deep, dict)
        or deep.get("status") != "completed"
        or type(deep.get("settled_micros")) is not int
        or not 0 <= deep["settled_micros"] <= 300_000
        or deep.get("unsettled_micros_upper_bound") != 0
    ):
        raise ValueError("Deep result is not completed, settled, non-degraded evidence")


def validate_terminal_rows(
    rows: list[dict],
    expected_ids: list[str],
    *,
    arms: tuple[str, ...] = PAID_ARMS,
) -> None:
    keys = [(row.get("id"), row.get("arm")) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("paid terminal rows contain a duplicate id/arm")
    expected = {(item_id, arm) for item_id in expected_ids for arm in arms}
    if set(keys) != expected or len(keys) != len(expected):
        raise ValueError("paid terminal rows do not match expected IDs and arms")
    if any(row.get("status") not in {"completed", "error"} for row in rows):
        raise ValueError("paid terminal rows contain a non-terminal status")


def _accuracy(rows: list[dict]) -> dict:
    correct = sum(row["correct"] for row in rows)
    return {
        "n": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else None,
    }


def _cluster_bootstrap_delta(
    per_item: list[dict],
    *,
    seed: int,
    samples: int,
    candidate: str = "selective_deep",
    control: str = "full_context",
) -> dict:
    if samples < 1:
        raise ValueError("bootstrap samples must be positive")
    clusters: dict[str, list[int]] = {}
    for row in per_item:
        clusters.setdefault(row["user_id"], []).append(
            int(row[f"{candidate}_correct"]) - int(row[f"{control}_correct"])
        )
    users = sorted(clusters)
    if not users:
        raise ValueError("bootstrap requires at least one user cluster")
    generator = random.Random(seed)
    values = []
    for _ in range(samples):
        sampled = [generator.choice(users) for _ in users]
        differences = [value for user in sampled for value in clusters[user]]
        values.append(sum(differences) / len(differences))
    values.sort()
    return {
        "method": "user-cluster percentile bootstrap",
        "seed": seed,
        "samples": samples,
        "low": values[int((samples - 1) * 0.025)],
        "high": values[int((samples - 1) * 0.975)],
    }


def analyze_paid_rows(
    source_rows: list[dict],
    terminal_rows: list[dict],
    *,
    bootstrap_seed: int = 20260803,
    bootstrap_samples: int = 20_000,
) -> dict:
    expected_ids = [row.get("id") for row in source_rows]
    if any(not isinstance(item_id, str) or not item_id for item_id in expected_ids):
        raise ValueError("scoring source contains an invalid ID")
    validate_terminal_rows(terminal_rows, expected_ids)
    predictions = {(row["id"], row["arm"]): row for row in terminal_rows}
    per_item = []
    for source in source_rows:
        gold = source.get("correct_letter")
        distractor = source.get("distractor_letter")
        evolved = source.get("has_evolved")
        if (
            gold not in list("ABCDE")
            or not isinstance(distractor, str)
            or type(evolved) is not bool
            or (evolved and distractor not in [*list("ABCDE"), ""])
        ):
            raise ValueError("HorizonBench scoring gold is malformed")
        item = {
            "id": source["id"],
            "user_id": source["user_id"],
            "has_evolved": evolved,
            "correct_letter": gold,
            "distractor_letter": distractor,
        }
        for arm in PAID_ARMS:
            prediction = predictions[(source["id"], arm)]
            answer = prediction.get("answer")
            item[f"{arm}_answer"] = answer
            item[f"{arm}_correct"] = (
                prediction.get("status") == "completed" and answer == gold
            )
        item["selective_route"] = predictions[(source["id"], "selective_deep")].get(
            "route"
        )
        per_item.append(item)

    arms = {}
    for arm in PAID_ARMS:
        scored = [
            {
                "correct": row[f"{arm}_correct"],
                "evolved": row["has_evolved"],
                "answer": row[f"{arm}_answer"],
                "distractor": row["distractor_letter"],
            }
            for row in per_item
        ]
        evolved_rows = [row for row in scored if row["evolved"]]
        static_rows = [row for row in scored if not row["evolved"]]
        arms[arm] = {
            **_accuracy(scored),
            "evolved": _accuracy(evolved_rows),
            "static": _accuracy(static_rows),
            "evolved_distractor_selections": sum(
                row["evolved"] and row["answer"] == row["distractor"] for row in scored
            ),
        }

    gains = sum(
        row["selective_deep_correct"] and not row["full_context_correct"]
        for row in per_item
    )
    losses = sum(
        row["full_context_correct"] and not row["selective_deep_correct"]
        for row in per_item
    )
    paired = {
        "gains": gains,
        "losses": losses,
        "discordant": gains + losses,
        "ties": len(per_item) - gains - losses,
        "accuracy_delta": (
            arms["selective_deep"]["accuracy"] - arms["full_context"]["accuracy"]
        ),
        "cluster_bootstrap_95_ci": _cluster_bootstrap_delta(
            per_item, seed=bootstrap_seed, samples=bootstrap_samples
        ),
    }
    gates = {
        "all_arms_complete": all(
            row.get("status") == "completed" for row in terminal_rows
        ),
        "no_more_evolved_distractors_than_full_context": (
            arms["selective_deep"]["evolved_distractor_selections"]
            <= arms["full_context"]["evolved_distractor_selections"]
        ),
        "noninferior_overall_accuracy": (
            arms["selective_deep"]["accuracy"] >= arms["full_context"]["accuracy"]
        ),
        "at_least_one_paired_gain": gains >= 1,
    }
    return {
        "arms": arms,
        "paired_selective_vs_full": paired,
        "selective_routing": {
            "fast": sum(row["selective_route"] == "fast" for row in per_item),
            "deep": sum(row["selective_route"] == "deep" for row in per_item),
            "other": sum(
                row["selective_route"] not in {"fast", "deep"} for row in per_item
            ),
        },
        "verdict": {
            "gates": gates,
            "outcome": ("advance_to_powered_plan" if all(gates.values()) else "stop"),
        },
        "per_item": per_item,
    }


def analyze_confirmation_rows(
    source_rows: list[dict],
    terminal_rows: list[dict],
    *,
    bootstrap_seed: int = 20260803,
    bootstrap_samples: int = 20_000,
) -> dict:
    expected_ids = [row.get("id") for row in source_rows]
    if any(not isinstance(item_id, str) or not item_id for item_id in expected_ids):
        raise ValueError("confirmation source contains an invalid ID")
    validate_terminal_rows(terminal_rows, expected_ids, arms=CONFIRMATION_ARMS)
    predictions = {(row["id"], row["arm"]): row for row in terminal_rows}
    per_item = []
    for source in source_rows:
        gold = source.get("correct_letter")
        distractor = source.get("distractor_letter")
        evolved = source.get("has_evolved")
        if (
            gold not in list("ABCDE")
            or not isinstance(distractor, str)
            or type(evolved) is not bool
            or (evolved and distractor not in [*list("ABCDE"), ""])
        ):
            raise ValueError("HorizonBench confirmation gold is malformed")
        item = {
            "id": source["id"],
            "user_id": source["user_id"],
            "has_evolved": evolved,
            "distractor_letter": distractor,
        }
        for arm in CONFIRMATION_ARMS:
            prediction = predictions[(source["id"], arm)]
            item[f"{arm}_answer"] = prediction.get("answer")
            item[f"{arm}_correct"] = (
                prediction.get("status") == "completed"
                and prediction.get("answer") == gold
            )
        per_item.append(item)
    arms = {}
    for arm in CONFIRMATION_ARMS:
        arm_rows = [
            {
                "correct": row[f"{arm}_correct"],
                "evolved": row["has_evolved"],
                "answer": row[f"{arm}_answer"],
                "distractor": row["distractor_letter"],
            }
            for row in per_item
        ]
        evolved_rows = [row for row in arm_rows if row["evolved"]]
        static_rows = [row for row in arm_rows if not row["evolved"]]
        arms[arm] = {
            **_accuracy(arm_rows),
            "evolved": _accuracy(evolved_rows),
            "static": _accuracy(static_rows),
            "evolved_distractor_selections": sum(
                row["evolved"] and row["answer"] == row["distractor"]
                for row in arm_rows
            ),
        }
    gains = sum(
        row["fast_correct"] and not row["full_context_correct"] for row in per_item
    )
    losses = sum(
        row["full_context_correct"] and not row["fast_correct"] for row in per_item
    )
    paired = {
        "gains": gains,
        "losses": losses,
        "discordant": gains + losses,
        "ties": len(per_item) - gains - losses,
        "accuracy_delta": arms["fast"]["accuracy"] - arms["full_context"]["accuracy"],
        "cluster_bootstrap_95_ci": _cluster_bootstrap_delta(
            per_item,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
            candidate="fast",
            control="full_context",
        ),
    }
    deltas = {
        "overall": arms["fast"]["accuracy"] - arms["full_context"]["accuracy"],
        "evolved": (
            arms["fast"]["evolved"]["accuracy"]
            - arms["full_context"]["evolved"]["accuracy"]
        ),
        "static": (
            arms["fast"]["static"]["accuracy"]
            - arms["full_context"]["static"]["accuracy"]
        ),
    }
    complete = all(row.get("status") == "completed" for row in terminal_rows)
    verdict = {
        "complete": complete,
        "overall_noninferior": deltas["overall"] >= 0,
        "evolved_positive": deltas["evolved"] > 0,
        "evolved_distractors_not_increased": (
            arms["fast"]["evolved_distractor_selections"]
            <= arms["full_context"]["evolved_distractor_selections"]
        ),
        "discordance_sufficient": paired["discordant"] >= 6,
    }
    verdict["outcome"] = "pass" if all(verdict.values()) else "stop"
    return {
        "arms": arms,
        "deltas": deltas,
        "paired_fast_vs_full": paired,
        "evolved_distractor_selections": {
            arm: arms[arm]["evolved_distractor_selections"] for arm in CONFIRMATION_ARMS
        },
        "verdict": verdict,
        "per_item": per_item,
    }


def pilot_evidence_contract(source_sha: str, analysis: dict) -> dict:
    paired = analysis["paired_selective_vs_full"]
    n = analysis.get("arms", {}).get("selective_deep", {}).get("n", 10)
    return {
        "schema_version": 1,
        "decisional": False,
        "claim": "The ten-row HorizonBench pilot is a diagnostic paired behavior result, not a promotion or SOTA result.",
        "power": {
            "test": "two-sided exact (conditional binomial) McNemar",
            "n": n,
            "b": paired["losses"],
            "c": paired["gains"],
            "n_d": paired["discordant"],
            "source": "docs/build-log/artifacts/horizonbench-pilot/result.json",
        },
        "mechanism_enabled": True,
        "probe_kind": "lever",
        "mechanism_evidence": "MemPhant Fast evidence was used on every treatment row; Deep remained explicit and gold-blind.",
        "harness": {
            "embed_model": "local sentence-unit embedder",
            "scorer": "exact released HorizonBench correct_letter",
            "k": 20,
            "budget": 16384,
            "flags": [
                "full_context",
                "fast",
                "selective_deep",
                "reader=anthropic/claude-opus-4.6",
            ],
        },
        "corpus": {
            "sha256": source_sha,
            "snapshot_id": f"{DATASET_ID}@{DATASET_REVISION}:sample/test",
            "n_items": n,
        },
        "instrument_verification": {
            "shipped_rows_verified": True,
            "rows_counted": n,
            "fields_counted": {
                "conversation": n,
                "options": n,
                "correct_letter": n,
                "has_evolved": n,
            },
            "license_id": "CC-BY-4.0",
            "license_source": "RECORD_METADATA",
            "license_evidence": "Pinned Hugging Face dataset metadata.",
        },
        "notes": "n=10 and n_d below the exact McNemar floor; process gate only.",
    }


def confirmation_result_contract(source_sha: str, analysis: dict) -> dict:
    paired = analysis["paired_fast_vs_full"]
    n = analysis["arms"]["fast"]["n"]
    psi = paired["discordant"] / n
    return {
        "schema_version": 1,
        "decisional": paired["discordant"] >= 6,
        "claim": "The frozen 60-user HorizonBench tranche is a held-out paired Fast-versus-full-context confirmation.",
        "power": {
            "test": "bootstrap paired difference",
            "n": n,
            "b": paired["losses"],
            "c": paired["gains"],
            "n_d": paired["discordant"],
            "psi_observed": psi,
            "mde_at_80": min_detectable_effect(n, psi),
            "computed_by": "scripts.instrument_power.min_detectable_effect",
        },
        "mechanism_enabled": True,
        "probe_kind": "lever",
        "mechanism_evidence": "Every Fast prediction used non-degraded evidence from the matching passed construction gate.",
        "harness": {
            "embed_model": "local sentence-unit embedder",
            "scorer": "exact released HorizonBench correct_letter",
            "k": 20,
            "budget": 16384,
            "flags": [
                "full_context",
                "fast",
                "reader=claude-opus-4-6",
                "provider=anthropic-first-party",
                "uncached_full_context",
            ],
        },
        "corpus": {
            "sha256": source_sha,
            "snapshot_id": f"{DATASET_ID}@{DATASET_REVISION}:benchmark/test:confirmation-v1",
            "n_items": n,
        },
        "instrument_verification": {
            "shipped_rows_verified": True,
            "rows_counted": n,
            "fields_counted": {
                "conversation": n,
                "options": n,
                "correct_letter": n,
                "has_evolved": n,
            },
            "license_id": "CC-BY-4.0",
            "license_source": "RECORD_METADATA",
            "license_evidence": "Pinned Hugging Face dataset metadata.",
        },
        "notes": "User-clustered bootstrap is primary; no complete-split or cross-axis SOTA claim.",
    }


def normalize_source_text(value: str) -> str:
    """Losslessly escape Postgres-forbidden controls and literal backslashes."""
    return "".join(
        f"\\u{ord(character):04x}"
        if character == "\\" or (ord(character) < 32 and character not in "\n\r\t")
        else character
        for character in value
    )


def restore_source_text(value: str) -> str:
    return ESCAPED_CODEPOINT.sub(lambda match: chr(int(match.group(1), 16)), value)


def parse_options(value: str | list[dict]) -> list[dict]:
    options = json.loads(value) if isinstance(value, str) else value
    if not isinstance(options, list) or not 3 <= len(options) <= 5:
        raise ValueError("HorizonBench options must contain three to five rows")
    letters = []
    normalized = []
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("HorizonBench option must be an object")
        if set(option) < {"letter", "value", "option"}:
            raise ValueError("HorizonBench option is missing a required field")
        letter = option["letter"]
        if letter not in "ABCDE" or not isinstance(option["option"], str):
            raise ValueError("HorizonBench option letter or body is invalid")
        letters.append(letter)
        normalized.append(
            {
                "letter": letter,
                "value": option["value"],
                "option": option["option"],
            }
        )
    if letters != list("ABCDE")[: len(letters)]:
        raise ValueError("HorizonBench option letters must be an ordered A-E prefix")
    return normalized


def parse_sessions(conversation_text: str) -> list[dict]:
    """Mirror the official parser without importing HorizonBench dependencies."""
    sessions: list[dict] = []
    current: dict | None = None
    for line in conversation_text.split("\n"):
        if line.startswith("Conversation History:"):
            continue
        if line.startswith("Date: "):
            if current is not None:
                sessions.append(current)
            current = {"date": line[6:], "scenario": "", "turns": []}
        elif line.startswith("Scenario: ") and current is not None:
            current["scenario"] = line[10:]
        elif line.startswith("User: ") and current is not None:
            current["turns"].append({"role": "user", "content": line[6:]})
        elif line.startswith("Assistant: ") and current is not None:
            current["turns"].append({"role": "assistant", "content": line[11:]})
        elif current is not None and current["turns"]:
            current["turns"][-1]["content"] += "\n" + line
    if current is not None:
        sessions.append(current)
    return sessions


def prompt_item(row: dict) -> dict:
    """Return the complete and exclusive pre-score view of one benchmark row."""
    missing = [field for field in PROMPT_FIELDS if field not in row]
    if missing:
        raise ValueError(f"HorizonBench prompt row is missing {missing}")
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "generator": row["generator"],
        "conversation": row["conversation"],
        "options": parse_options(row["options"]),
    }


def build_question(options: list[dict]) -> str:
    lines = [
        "Which of the following responses would align with the user's preferences the most?"
    ]
    lines.extend(
        f"{option['letter']}: {normalize_source_text(option['option'])}"
        for option in options
    )
    lines.append(
        "Please respond with the letter of the option that aligns with the user's preferences the most and nothing else."
    )
    return "\n".join(lines)


def _safe_ref(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")[:72]
    if safe == value and safe:
        return safe
    suffix = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{safe or 'item'}-{suffix}"


def _observed_times(sessions: list[dict]) -> list[str]:
    values = []
    previous: datetime | None = None
    for session in sessions:
        try:
            parsed = datetime.fromisoformat(session["date"].replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"invalid HorizonBench session date: {session['date']!r}"
            ) from error
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if previous is not None and parsed <= previous:
            parsed = previous + timedelta(microseconds=1)
        values.append(parsed.isoformat().replace("+00:00", "Z"))
        previous = parsed
    return values


def _session_body(session: dict) -> str:
    lines = [f"Date: {session['date']}"]
    if session["scenario"]:
        lines.append(f"Scenario: {session['scenario']}")
    for turn in session["turns"]:
        label = "User" if turn["role"] == "user" else "Assistant"
        lines.append(f"{label}: {turn['content']}")
    body = "\n".join(lines)
    if not body.strip() or len(body.encode()) > MAX_EPISODE_BYTES:
        raise ValueError(
            "HorizonBench session body is empty or exceeds retain boundary"
        )
    return body


def runtime_item(row: dict, *, context_ref: str | None = None) -> dict:
    view = prompt_item(row)
    sessions = parse_sessions(normalize_source_text(view["conversation"]))
    if not sessions:
        raise ValueError(f"HorizonBench row {view['id']} has no conversation sessions")
    ref = _safe_ref(view["id"])
    episode_ref = _safe_ref(context_ref) if context_ref else ref
    times = _observed_times(sessions)
    episodes = [
        {
            "source_ref": f"horizon:{episode_ref}:session:{index}",
            "observed_at": times[index],
            "body": _session_body(session),
        }
        for index, session in enumerate(sessions)
    ]
    question = build_question(view["options"])
    return {
        "id": view["id"],
        "user_id": view["user_id"],
        "generator": view["generator"],
        "context_ref": context_ref or f"horizon-{ref}",
        "episodes": episodes,
        "question": question,
        "recall_query": question[:RECALL_QUERY_CHARS],
        "options": view["options"],
    }


def confirmation_runtime_items(rows: list[dict]) -> list[dict]:
    by_user: dict[str, list[dict]] = {}
    for row in rows:
        by_user.setdefault(row["user_id"], []).append(row)
    if any(len(user_rows) != 2 for user_rows in by_user.values()):
        raise ValueError("HorizonBench confirmation requires two rows per user")
    items = []
    for user_id in sorted(by_user):
        ordered = sorted(
            by_user[user_id], key=lambda row: (len(row["conversation"]), row["id"])
        )
        views = [prompt_item(row) for row in ordered]
        early, late = [
            parse_sessions(normalize_source_text(view["conversation"]))
            for view in views
        ]
        if len(early) > len(late) or any(
            early[index]["turns"] != late[index]["turns"]
            for index in range(max(0, len(early) - 1))
        ):
            raise ValueError(f"HorizonBench timeline drift for user {user_id}")
        early_last = len(early) - 1
        early_turns = [
            {**turn, "content": turn["content"].rstrip()}
            for turn in early[early_last]["turns"]
        ]
        late_prefix = [
            {**turn, "content": turn["content"].rstrip()}
            for turn in late[early_last]["turns"][: len(early_turns)]
        ]
        if early_turns != late_prefix:
            raise ValueError(f"HorizonBench timeline drift for user {user_id}")
        context_ref = f"horizon-user-{_safe_ref(user_id)}"
        context_token = _safe_ref(context_ref)
        late_times = _observed_times(late)
        early_episodes = [
            {
                "source_ref": f"horizon:{context_token}:session:{index}",
                "observed_at": late_times[index],
                "body": _confirmation_session_body(session),
            }
            for index, session in enumerate(early[:-1])
        ]
        base_time = datetime.fromisoformat(
            late_times[early_last].replace("Z", "+00:00")
        )
        early_episodes.extend(
            _confirmation_turn_episode(
                context_token,
                early[early_last],
                session_index=early_last,
                turn_index=index,
                turn=turn,
                base_time=base_time,
            )
            for index, turn in enumerate(early_turns)
        )
        late_episodes = list(early_episodes)
        late_episodes.extend(
            _confirmation_turn_episode(
                context_token,
                late[early_last],
                session_index=early_last,
                turn_index=index,
                turn={**turn, "content": turn["content"].rstrip()},
                base_time=base_time,
            )
            for index, turn in enumerate(
                late[early_last]["turns"][len(early_turns) :],
                start=len(early_turns),
            )
        )
        late_episodes.extend(
            {
                "source_ref": f"horizon:{context_token}:session:{index}",
                "observed_at": late_times[index],
                "body": _confirmation_session_body(session),
            }
            for index, session in enumerate(late[len(early) :], start=len(early))
        )
        for view, episodes in zip(views, (early_episodes, late_episodes), strict=True):
            question = build_question(view["options"])
            items.append(
                {
                    "id": view["id"],
                    "user_id": view["user_id"],
                    "generator": view["generator"],
                    "context_ref": context_ref,
                    "episodes": episodes,
                    "question": question,
                    "recall_query": question[:RECALL_QUERY_CHARS],
                    "options": view["options"],
                }
            )
    return items


def _confirmation_session_body(session: dict) -> str:
    return _session_body(
        {
            **session,
            "turns": [
                {**turn, "content": turn["content"].rstrip()}
                for turn in session["turns"]
            ],
        }
    )


def _confirmation_turn_episode(
    context_token: str,
    session: dict,
    *,
    session_index: int,
    turn_index: int,
    turn: dict,
    base_time: datetime,
) -> dict:
    label = "User" if turn["role"] == "user" else "Assistant"
    body = [f"Date: {session['date']}"]
    if session["scenario"]:
        body.append(f"Scenario: {session['scenario']}")
    body.append(f"{label}: {turn['content']}")
    return {
        "source_ref": (
            f"horizon:{context_token}:session:{session_index}:turn:{turn_index}"
        ),
        "observed_at": (
            (base_time + timedelta(microseconds=turn_index))
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "body": "\n".join(body),
    }


def build_incremental_confirmation_evidence(
    client,
    items: list[dict],
    drain_worker,
    *,
    k: int,
    budget_tokens: int,
) -> tuple[list[dict], int, int]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["context_ref"], []).append(item)
    if any(len(group) != 2 for group in groups.values()):
        raise ValueError("HorizonBench confirmation requires two items per user")
    ordered_groups = []
    bound = {}
    for context_ref in sorted(groups):
        group = sorted(
            groups[context_ref], key=lambda item: (len(item["episodes"]), item["id"])
        )
        if group[1]["episodes"][: len(group[0]["episodes"])] != group[0]["episodes"]:
            raise ValueError(f"HorizonBench timeline drift for context {context_ref}")
        context = client.bind_context(
            context_ref,
            subject_ref=context_ref,
            actor_ref="horizonbench-runner",
            scope_ref=context_ref,
            agent_node_ref="horizonbench-runner",
        )
        for item in group:
            bound[item["id"]] = context
        ordered_groups.append(group)
    retained_counts = {group[0]["context_ref"]: 0 for group in ordered_groups}
    retained = 0
    compiled = 0
    evidence = []
    for stage in range(2):
        stage_items = []
        for group in ordered_groups:
            item = group[stage]
            context_ref = item["context_ref"]
            start = retained_counts[context_ref]
            for episode in item["episodes"][start:]:
                client.post(
                    "/v1/episodes",
                    gr.episode_retain_payload(
                        bound[item["id"]],
                        source_ref=episode["source_ref"],
                        observed_at=episode["observed_at"],
                        source_kind="user",
                        body=episode["body"],
                    ),
                )
                retained += 1
            retained_counts[context_ref] = len(item["episodes"])
            stage_items.append(item)
        compiled += drain_worker()
        evidence.extend(
            recall_runtime_items(client, stage_items, bound, "fast", k, budget_tokens)
        )
    return evidence, retained, compiled


def validate_evidence_rows(rows: list[dict], expected_ids: list[str], arm: str) -> None:
    ids = [row.get("id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"HorizonBench {arm} evidence has a duplicate id")
    if set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
        raise ValueError(f"HorizonBench {arm} evidence does not match expected IDs")
    for row in rows:
        if row.get("arm") != arm:
            raise ValueError(
                f"HorizonBench evidence row has wrong arm: {row.get('arm')!r}"
            )
        if row.get("degraded") is not False:
            raise ValueError(
                f"HorizonBench {arm} evidence is degraded for {row.get('id')}"
            )
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(
                f"HorizonBench {arm} has empty evidence for {row.get('id')}"
            )


def retain_runtime_items(client, items: list[dict]) -> tuple[dict[str, dict], int]:
    bound = {}
    retained = 0
    contexts: dict[str, tuple[list[dict], dict]] = {}
    for item in items:
        prior = contexts.get(item["context_ref"])
        if prior is not None:
            if item["episodes"] != prior[0]:
                raise ValueError(
                    f"HorizonBench timeline drift for context {item['context_ref']}"
                )
            bound[item["id"]] = prior[1]
            continue
        context = client.bind_context(
            item["context_ref"],
            subject_ref=item["context_ref"],
            actor_ref="horizonbench-runner",
            scope_ref=item["context_ref"],
            agent_node_ref="horizonbench-runner",
        )
        contexts[item["context_ref"]] = (item["episodes"], context)
        bound[item["id"]] = context
        for episode in item["episodes"]:
            payload = gr.episode_retain_payload(
                context,
                source_ref=episode["source_ref"],
                observed_at=episode["observed_at"],
                source_kind="user",
                body=episode["body"],
            )
            client.post("/v1/episodes", payload)
            retained += 1
    return bound, retained


def recall_runtime_items(
    client,
    items: list[dict],
    bound: dict[str, dict],
    arm: str,
    k: int,
    budget_tokens: int,
) -> list[dict]:
    mode = "deep" if arm == "deep" else "fast"
    rows = []
    for item in items:
        started = time.monotonic()
        response = client.post(
            "/v1/recall",
            {
                **bound[item["id"]],
                "query": item["recall_query"],
                "limit": k,
                "budget_tokens": budget_tokens,
                "mode": mode,
            },
        )
        rows.append(
            {
                "id": item["id"],
                "user_id": item["user_id"],
                "generator": item["generator"],
                "arm": arm,
                "question": item["question"],
                "options": item["options"],
                "evidence": response.get("items", []),
                "degraded": bool(response.get("degraded", False)),
                "trace_id": response.get("trace_id"),
                "deep": response.get("deep"),
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
            }
        )
    return rows


def validate_sample_rows(rows: list[dict]) -> dict:
    if len(rows) != 10:
        raise ValueError(
            f"HorizonBench sample must contain exactly 10 rows, got {len(rows)}"
        )
    ids = [row.get("id") for row in rows]
    if any(not isinstance(item_id, str) or not item_id for item_id in ids):
        raise ValueError("HorizonBench sample has a missing id")
    if len(set(ids)) != len(ids):
        raise ValueError("HorizonBench sample has a duplicate id")
    users = [row.get("user_id") for row in rows]
    if any(not isinstance(user_id, str) or not user_id for user_id in users):
        raise ValueError("HorizonBench sample has a missing user_id")
    if len(set(users)) != 10:
        raise ValueError("HorizonBench sample must contain exactly 10 unique users")
    for row in rows:
        view = prompt_item(row)
        sessions = parse_sessions(view["conversation"])
        if not sessions or any(
            not session["date"] or not session["turns"] for session in sessions
        ):
            raise ValueError(
                f"HorizonBench row {row['id']} has invalid conversation sessions"
            )
    return {
        "row_count": len(rows),
        "user_count": len(set(users)),
        "expected_ids": ids,
        "expected_user_ids": users,
    }


def validate_benchmark_rows(
    rows: list[dict],
    *,
    expected_rows: int,
    expected_users: int,
    expected_generator_counts: dict[str, int],
) -> dict:
    if len(rows) != expected_rows:
        raise ValueError(
            f"HorizonBench benchmark must contain {expected_rows} rows, got {len(rows)}"
        )
    ids = [row.get("id") for row in rows]
    if any(not isinstance(item_id, str) or not item_id for item_id in ids):
        raise ValueError("HorizonBench benchmark has a missing id")
    if len(ids) != len(set(ids)):
        raise ValueError("HorizonBench benchmark has a duplicate id")
    users: dict[str, list[dict]] = {}
    generator_counts: Counter[str] = Counter()
    option_cardinality_counts: Counter[int] = Counter()
    evolved_rows_without_distractor = 0
    evolved_rows = 0
    for row in rows:
        user_id = row.get("user_id")
        generator = row.get("generator")
        conversation = row.get("conversation")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("HorizonBench benchmark has a missing user_id")
        if not isinstance(generator, str) or not generator:
            raise ValueError("HorizonBench benchmark has a missing generator")
        if not isinstance(conversation, str) or not conversation:
            raise ValueError("HorizonBench benchmark has an empty conversation")
        options = parse_options(row.get("options"))
        option_cardinality_counts[len(options)] += 1
        letters = {option["letter"] for option in options}
        if row.get("correct_letter") not in letters:
            raise ValueError(f"HorizonBench row {row['id']} has invalid correct_letter")
        if type(row.get("has_evolved")) is not bool:
            raise ValueError(f"HorizonBench row {row['id']} has invalid has_evolved")
        evolved_rows += int(row["has_evolved"])
        distractor = row.get("distractor_letter")
        if row["has_evolved"] and distractor in {"", None}:
            evolved_rows_without_distractor += 1
        if row["has_evolved"] and distractor not in letters | {"", None}:
            raise ValueError(
                f"HorizonBench row {row['id']} has invalid distractor_letter"
            )
        if not row["has_evolved"] and distractor not in {"", None}:
            raise ValueError(f"HorizonBench row {row['id']} has static distractor")
        users.setdefault(user_id, []).append(row)
        generator_counts[generator] += 1
    if len(users) != expected_users:
        raise ValueError(
            f"HorizonBench benchmark must contain {expected_users} users, got {len(users)}"
        )
    if dict(generator_counts) != expected_generator_counts:
        raise ValueError(
            "HorizonBench benchmark generator counts drift: "
            f"{dict(generator_counts)} != {expected_generator_counts}"
        )
    inconsistent = inconsistent_timeline_users(rows)
    if inconsistent:
        raise ValueError(f"HorizonBench timeline drift for user {inconsistent[0]}")
    eligible: Counter[str] = Counter()
    for user_id, user_rows in users.items():
        strata = {row["has_evolved"] for row in user_rows}
        if strata == {False, True}:
            eligible[user_rows[0]["generator"]] += 1
    return {
        "row_count": len(rows),
        "user_count": len(users),
        "generator_counts": dict(generator_counts),
        "eligible_user_counts": dict(eligible),
        "option_cardinality_counts": {
            str(count): rows
            for count, rows in sorted(option_cardinality_counts.items())
        },
        "evolved_rows_without_distractor": evolved_rows_without_distractor,
        "stratum_counts": {
            "evolved": evolved_rows,
            "static": len(rows) - evolved_rows,
        },
    }


def inconsistent_timeline_users(rows: list[dict]) -> list[str]:
    conversations: dict[str, list[str]] = {}
    for row in rows:
        conversations.setdefault(row["user_id"], []).append(
            row["conversation"].rstrip()
        )
    inconsistent = []
    for user_id, values in conversations.items():
        ordered = sorted(values, key=len)
        if any(
            not later.startswith(earlier)
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        ):
            inconsistent.append(user_id)
    return sorted(inconsistent)


def _seeded_key(seed: str, *parts: object) -> str:
    return hashlib.sha256(
        "\0".join([seed, *(str(part) for part in parts)]).encode()
    ).hexdigest()


def select_confirmation_rows(
    rows: list[dict],
    *,
    excluded_user_ids: set[str],
    seed: str,
    users_per_generator: int,
) -> list[dict]:
    by_generator: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        by_generator.setdefault(row["generator"], {}).setdefault(
            row["user_id"], []
        ).append(row)
    selected: list[dict] = []
    for generator in sorted(by_generator):
        eligible = [
            user_id
            for user_id, user_rows in by_generator[generator].items()
            if user_id not in excluded_user_ids
            and {row["has_evolved"] for row in user_rows} == {False, True}
        ]
        ranked_users = sorted(
            eligible, key=lambda user_id: _seeded_key(seed, generator, user_id)
        )
        if len(ranked_users) < users_per_generator:
            raise ValueError(
                f"HorizonBench generator {generator} has only {len(ranked_users)} "
                f"eligible users after exclusions; need {users_per_generator}"
            )
        for user_id in ranked_users[:users_per_generator]:
            user_rows = by_generator[generator][user_id]
            for evolved in (False, True):
                candidates = [row for row in user_rows if row["has_evolved"] is evolved]
                selected.append(
                    min(
                        candidates,
                        key=lambda row: _seeded_key(
                            seed, generator, user_id, evolved, row["id"]
                        ),
                    )
                )
    return selected


def reconcile_graph_population(
    benchmark_rows: list[dict],
    graph_rows: list[dict],
    *,
    expected_graph_users: int,
) -> dict:
    if any(set(row) != {"user_id", "generator"} for row in graph_rows):
        raise ValueError("graph reconciliation accepts identity columns only")
    benchmark_users = {row["user_id"] for row in benchmark_rows}
    graph_users = {row["user_id"] for row in graph_rows}
    if len(graph_users) != expected_graph_users:
        raise ValueError(
            f"HorizonBench graph population must contain {expected_graph_users} users, "
            f"got {len(graph_users)}"
        )
    return {
        "benchmark_users": len(benchmark_users),
        "graph_users": len(graph_users),
        "benchmark_users_missing_from_graph": sorted(benchmark_users - graph_users),
        "graph_only_users": sorted(graph_users - benchmark_users),
    }


def verify_locked_file(path: Path, *, expected_size: int, expected_sha256: str) -> dict:
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise ValueError(
            f"HorizonBench source size drift for {path}: "
            f"{actual_size} != {expected_size}"
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"HorizonBench source sha256 drift for {path}: "
            f"{actual_sha256} != {expected_sha256}"
        )
    return {
        "path": str(path),
        "size": actual_size,
        "sha256": actual_sha256,
    }


def _source_url(relative_path: str) -> str:
    quoted = urllib.parse.quote(relative_path, safe="/")
    return (
        f"https://huggingface.co/datasets/{DATASET_ID}/resolve/"
        f"{DATASET_REVISION}/{quoted}?download=true"
    )


def _download_locked_file(cache_dir: Path, source: tuple[str, int, str]) -> dict:
    relative_path, expected_size, expected_sha256 = source
    destination = cache_dir / relative_path
    if destination.exists():
        return verify_locked_file(
            destination,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "MemPhant-HorizonBench/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(_source_url(relative_path), headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        append = offset > 0 and response.status == 206
        with partial.open("ab" if append else "wb") as output:
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
    os.replace(partial, destination)
    return verify_locked_file(
        destination,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
    )


def _parquet_rows(paths: list[Path], *, columns: list[str] | None = None):
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise RuntimeError(
            "census-full requires pyarrow; run with `uv run --with pyarrow`"
        ) from error
    for path in paths:
        parquet_file = parquet.ParquetFile(path)
        for batch in parquet_file.iter_batches(batch_size=16, columns=columns):
            yield from batch.to_pylist()


def _remote_graph_identity_rows() -> list[dict]:
    try:
        import fsspec
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise RuntimeError(
            "graph identity projection requires pyarrow and fsspec; run with "
            "`uv run --with pyarrow --with fsspec --with aiohttp`"
        ) from error
    rows = []
    for relative_path, _, _ in GRAPH_SOURCE_FILES:
        with fsspec.open(
            _source_url(relative_path), "rb", block_size=8 * 1024 * 1024
        ) as handle:
            parquet_file = parquet.ParquetFile(handle)
            for batch in parquet_file.iter_batches(
                batch_size=64, columns=["user_id", "generator"]
            ):
                rows.extend(batch.to_pylist())
    return rows


def census_full(args) -> dict:
    revision = fetch_source_revision()
    cache_dir = args.cache_root.expanduser().resolve() / revision
    with ThreadPoolExecutor(max_workers=3) as executor:
        verified_files = list(
            executor.map(
                lambda source: _download_locked_file(cache_dir, source),
                BENCHMARK_SOURCE_FILES,
            )
        )
    ids: set[str] = set()
    users: dict[str, dict] = {}
    generator_counts: Counter[str] = Counter()
    option_cardinality_counts: Counter[int] = Counter()
    evolved_rows_without_distractor = 0
    evolved_rows = 0
    index_rows = []
    benchmark_paths = [cache_dir / source[0] for source in BENCHMARK_SOURCE_FILES]
    for row in _parquet_rows(benchmark_paths):
        item_id = row.get("id")
        user_id = row.get("user_id")
        generator = row.get("generator")
        conversation = row.get("conversation")
        if not isinstance(item_id, str) or not item_id or item_id in ids:
            raise ValueError("HorizonBench benchmark has an invalid or duplicate id")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(f"HorizonBench row {item_id} has an invalid user_id")
        if (
            not isinstance(generator, str)
            or generator not in BENCHMARK_GENERATOR_COUNTS
        ):
            raise ValueError(f"HorizonBench row {item_id} has an invalid generator")
        if not isinstance(conversation, str) or not conversation:
            raise ValueError(f"HorizonBench row {item_id} has an empty conversation")
        options = parse_options(row.get("options"))
        option_cardinality_counts[len(options)] += 1
        letters = {option["letter"] for option in options}
        evolved = row.get("has_evolved")
        evolved_rows += int(evolved is True)
        if row.get("correct_letter") not in letters or type(evolved) is not bool:
            raise ValueError(f"HorizonBench row {item_id} has malformed gold")
        distractor = row.get("distractor_letter")
        if evolved and distractor in {"", None}:
            evolved_rows_without_distractor += 1
        if (evolved and distractor not in letters | {"", None}) or (
            not evolved and distractor not in {"", None}
        ):
            raise ValueError(f"HorizonBench row {item_id} has malformed distractor")
        conversation_sha = hashlib.sha256(conversation.encode()).hexdigest()
        user = users.setdefault(
            user_id,
            {
                "generator": generator,
                "latest_conversation": conversation.rstrip(),
                "conversation_hashes": set(),
                "strata": set(),
                "timeline_consistent": True,
            },
        )
        latest = user["latest_conversation"]
        current = conversation.rstrip()
        if user["generator"] != generator or not (
            current.startswith(latest) or latest.startswith(current)
        ):
            user["timeline_consistent"] = False
        if len(current) > len(latest):
            user["latest_conversation"] = current
        user["conversation_hashes"].add(conversation_sha)
        user["strata"].add(evolved)
        ids.add(item_id)
        generator_counts[generator] += 1
        index_rows.append(
            {
                "id": item_id,
                "user_id": user_id,
                "generator": generator,
                "has_evolved": evolved,
                "conversation_sha256": conversation_sha,
            }
        )
    if len(ids) != 4_245 or len(users) != 346:
        raise ValueError(
            f"HorizonBench census drift: rows={len(ids)}, users={len(users)}"
        )
    if dict(generator_counts) != BENCHMARK_GENERATOR_COUNTS:
        raise ValueError(
            f"HorizonBench generator counts drift: {dict(generator_counts)}"
        )
    timeline_drift_users = {
        user_id for user_id, value in users.items() if not value["timeline_consistent"]
    }
    if timeline_drift_users != EXPECTED_TIMELINE_DRIFT_USERS:
        raise ValueError(
            "HorizonBench timeline-drift population changed: "
            f"{sorted(timeline_drift_users)}"
        )
    eligible_counts = Counter(
        value["generator"]
        for value in users.values()
        if value["timeline_consistent"] and value["strata"] == {False, True}
    )
    index_path = cache_dir / "benchmark-index.jsonl"
    write_jsonl(index_path, sorted(index_rows, key=lambda row: row["id"]))

    graph_rows = _remote_graph_identity_rows()
    graph_report = reconcile_graph_population(
        index_rows, graph_rows, expected_graph_users=360
    )
    if graph_report["benchmark_users_missing_from_graph"]:
        raise ValueError(
            "HorizonBench benchmark users are missing from graph population"
        )
    report = {
        "schema_version": 1,
        "status": "passed",
        "dataset": DATASET_ID,
        "dataset_revision": revision,
        "config": "benchmark",
        "split": "test",
        "source_files": [
            {**verified, "path": source[0]}
            for verified, source in zip(
                verified_files, BENCHMARK_SOURCE_FILES, strict=True
            )
        ],
        "source_bytes": sum(source[1] for source in BENCHMARK_SOURCE_FILES),
        "row_count": len(ids),
        "user_count": len(users),
        "generator_counts": dict(generator_counts),
        "option_cardinality_counts": {
            str(count): rows
            for count, rows in sorted(option_cardinality_counts.items())
        },
        "evolved_rows_without_distractor": evolved_rows_without_distractor,
        "stratum_counts": {
            "evolved": evolved_rows,
            "static": len(ids) - evolved_rows,
        },
        "eligible_user_counts": dict(eligible_counts),
        "timeline_variant_count": sum(
            len(value["conversation_hashes"]) for value in users.values()
        ),
        "timeline_integrity": {
            "status": "qualified_with_exclusions",
            "monotone_users": len(users) - len(timeline_drift_users),
            "drift_users": sorted(timeline_drift_users),
            "selection_excludes_drift_users": True,
        },
        "index_path": str(index_path),
        "index_sha256": gr.sha256_file(index_path),
        "graph_reconciliation": {
            **graph_report,
            "identity_source": "remote Parquet projection of user_id and generator only",
            "source_objects": [
                {"path": path, "size": size, "sha256": sha256}
                for path, size, sha256 in GRAPH_SOURCE_FILES
            ],
        },
        "gold_quarantine": {
            "index_fields": [
                "id",
                "user_id",
                "generator",
                "has_evolved",
                "conversation_sha256",
            ],
            "mental_state_graphs_acquired": False,
        },
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": "The pinned HorizonBench benchmark release completed a full schema, identity, and integrity census.",
            "power": {
                "test": "descriptive-only (no test)",
                "n": 0,
                "b": 0,
                "c": 0,
                "n_d": 0,
            },
            "harness": {
                "embed_model": "none",
                "scorer": "full release census; no answer scoring",
                "k": "n/a",
                "budget": 0,
                "flags": ["gold-quarantined", "graph-identity-columns-only"],
            },
            "corpus": {
                "sha256": sha256_json([source[2] for source in BENCHMARK_SOURCE_FILES]),
                "snapshot_id": f"{DATASET_ID}@{revision}:benchmark/test",
                "n_items": 4_245,
            },
            "instrument_verification": {
                "shipped_rows_verified": True,
                "rows_counted": 4_245,
                "fields_counted": {
                    "conversation": 4_245,
                    "options": 4_245,
                    "correct_letter": 4_245,
                    "has_evolved": 4_245,
                },
                "license_id": "CC-BY-4.0",
                "license_source": "RECORD_METADATA",
                "license_evidence": "Pinned Hugging Face dataset metadata.",
            },
            "notes": "Qualification only; two inconsistent user timelines are excluded from held-out selection.",
        },
    }
    atomic_write_json(args.lock_out.resolve(), report)
    atomic_write_json(args.report_out.resolve(), report)
    return report


def select_confirmation(args) -> dict:
    full_lock = json.loads(args.full_lock.read_text(encoding="utf-8"))
    validate_source_revision(full_lock.get("dataset_revision"))
    index_path = Path(full_lock["index_path"])
    if gr.sha256_file(index_path) != full_lock.get("index_sha256"):
        raise ValueError("HorizonBench benchmark index hash drift")
    index_rows = load_jsonl(index_path)
    sample_lock = json.loads(args.sample_lock.read_text(encoding="utf-8"))
    excluded_users = set(sample_lock.get("expected_user_ids", [])) | set(
        full_lock.get("timeline_integrity", {}).get("drift_users", [])
    )
    selected_index = select_confirmation_rows(
        index_rows,
        excluded_user_ids=excluded_users,
        seed=args.seed,
        users_per_generator=20,
    )
    selected_ids = {row["id"] for row in selected_index}
    cache_dir = args.cache_root.expanduser().resolve() / DATASET_REVISION
    rows = [
        row
        for row in _parquet_rows(
            [cache_dir / source[0] for source in BENCHMARK_SOURCE_FILES]
        )
        if row.get("id") in selected_ids
    ]
    if len(rows) != 120:
        raise ValueError(f"HorizonBench confirmation extraction got {len(rows)} rows")
    rows_by_id = {row["id"]: row for row in rows}
    ordered = [rows_by_id[row["id"]] for row in selected_index]
    validate_benchmark_rows(
        ordered,
        expected_rows=120,
        expected_users=60,
        expected_generator_counts={
            generator: 40 for generator in BENCHMARK_GENERATOR_COUNTS
        },
    )
    source_raw = canonical_jsonl_bytes(ordered)
    atomic_write(args.out.expanduser().resolve(), source_raw)
    selected_users = sorted({row["user_id"] for row in ordered})
    report = {
        "schema_version": 1,
        "status": "frozen",
        "dataset": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "seed": args.seed,
        "source_jsonl_sha256": hashlib.sha256(source_raw).hexdigest(),
        "expected_ids": [row["id"] for row in ordered],
        "expected_user_ids": selected_users,
        "rows": 120,
        "users": 60,
        "users_per_generator": {
            generator: 20 for generator in BENCHMARK_GENERATOR_COUNTS
        },
        "rows_per_generator": {
            generator: 40 for generator in BENCHMARK_GENERATOR_COUNTS
        },
        "rows_per_stratum": {"evolved": 60, "static": 60},
        "evolved_distractor_coverage": {
            "present": sum(
                bool(row.get("distractor_letter"))
                for row in ordered
                if row["has_evolved"]
            ),
            "missing": sum(
                not bool(row.get("distractor_letter"))
                for row in ordered
                if row["has_evolved"]
            ),
        },
        "excluded_sample_user_ids_sha256": sha256_json(sorted(excluded_users)),
        "full_lock_sha256": gr.sha256_file(args.full_lock.resolve()),
        "gold_quarantine": {
            "selection_fields": ["id", "user_id", "generator", "has_evolved"],
            "selection_uses_correct_letter": False,
            "selection_uses_distractor_letter": False,
            "mental_state_graphs_acquired": False,
        },
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": "A deterministic gold-blind 60-user, 120-item HorizonBench confirmation tranche was frozen.",
            "power": {
                "test": "descriptive-only (no test)",
                "n": 0,
                "b": 0,
                "c": 0,
                "n_d": 0,
            },
            "harness": {
                "embed_model": "none",
                "scorer": "selection only; no answer scoring",
                "k": "n/a",
                "budget": 0,
                "flags": ["sample-users-excluded", "timeline-drift-users-excluded"],
            },
            "corpus": {
                "sha256": hashlib.sha256(source_raw).hexdigest(),
                "snapshot_id": f"{DATASET_ID}@{DATASET_REVISION}:benchmark/test:confirmation-v1",
                "n_items": 120,
            },
            "instrument_verification": {
                "shipped_rows_verified": True,
                "rows_counted": 120,
                "fields_counted": {
                    "conversation": 120,
                    "options": 120,
                    "correct_letter": 120,
                    "has_evolved": 120,
                },
                "license_id": "CC-BY-4.0",
                "license_source": "RECORD_METADATA",
                "license_evidence": "Pinned Hugging Face dataset metadata.",
            },
            "notes": "Selection used identity, generator, and has_evolved strata only; answer and distractor labels were not used.",
        },
    }
    atomic_write_json(args.report_out.resolve(), report)
    return report


def canonical_jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in rows
    )


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    atomic_write(path, canonical_jsonl_bytes(rows))


def evidence_prompt(evidence: list[dict], question: str) -> str:
    bodies = []
    for rank, item in enumerate(evidence, start=1):
        body = item.get("body") if isinstance(item, dict) else None
        if not isinstance(body, str) or not body.strip():
            raise ValueError("reader evidence contains an empty body")
        bodies.append(f"[{rank}] {body}")
    if not bodies:
        raise ValueError("reader evidence is empty")
    return "Evidence:\n" + "\n\n".join(bodies) + "\n\nQuestion:\n" + question


def full_context_prompt(row: dict) -> str:
    view = prompt_item(row)
    return (
        "Evidence:\n"
        + normalize_source_text(view["conversation"])
        + "\n\nQuestion:\n"
        + build_question(view["options"])
    )


def confirmation_reader_requests(rows: list[dict], fast_rows: list[dict]) -> list[dict]:
    by_user: dict[str, list[dict]] = {}
    for row in rows:
        by_user.setdefault(row["user_id"], []).append(row)
    if any(len(user_rows) != 2 for user_rows in by_user.values()):
        raise ValueError("confirmation requires exactly two rows per user")

    requests = sorted(
        (
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "arm": "full_context",
                "prompt": full_context_prompt(row),
            }
            for row in rows
        ),
        key=lambda row: len(row["prompt"]),
        reverse=True,
    )

    fast_by_id = {row["id"]: row for row in fast_rows}
    if set(fast_by_id) != {row["id"] for row in rows}:
        raise ValueError("confirmation Fast evidence IDs do not match source IDs")
    fast_requests = []
    for row in rows:
        fast = fast_by_id[row["id"]]
        prompt = evidence_prompt(fast["evidence"], fast["question"])
        fast_requests.append(
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "arm": "fast",
                "prompt": prompt,
            }
        )
    requests.extend(sorted(fast_requests, key=lambda row: len(row["prompt"]), reverse=True))
    return requests


def confirmation_cost_preflight(
    requests: list[dict],
    *,
    pilot_prompt_chars: int,
    pilot_prompt_tokens: int,
    max_output_tokens: int = CONFIRMATION_MAX_OUTPUT_TOKENS,
) -> dict:
    if pilot_prompt_chars <= 0 or pilot_prompt_tokens <= 0 or not requests:
        raise ValueError("confirmation cost preflight inputs must be positive")
    prompt_chars = sum(len(request["prompt"]) for request in requests)
    token_ratio = Decimal(pilot_prompt_tokens) / Decimal(pilot_prompt_chars)
    input_cost = (
        Decimal(prompt_chars) * token_ratio * Decimal("5") / Decimal(1_000_000)
    )
    completion_cost = (
        Decimal(len(requests) * max_output_tokens)
        * Decimal("25")
        / Decimal(1_000_000)
    )
    estimate = input_cost + completion_cost
    buffered = estimate * Decimal("1.05")
    return {
        "status": (
            "passed" if buffered <= CONFIRMATION_MAX_SPEND_USD else "blocked"
        ),
        "method": "pilot prompt-token ratio over exact uncached prompt characters, with 5% planning buffer",
        "prompt_chars": prompt_chars,
        "pilot_prompt_chars": pilot_prompt_chars,
        "pilot_prompt_tokens": pilot_prompt_tokens,
        "pilot_tokens_per_char": str(token_ratio),
        "estimated_input_usd": f"{input_cost:.6f}",
        "max_completion_usd": f"{completion_cost:.6f}",
        "estimated_total_usd_before_buffer": f"{estimate:.6f}",
        "estimated_total_usd": f"{buffered:.6f}",
        "authorized_ceiling_usd": str(CONFIRMATION_MAX_SPEND_USD),
        "calls": len(requests),
    }


def _validate_reader_metadata(
    metadata: dict | None, *, expected_model: str = READER_MODEL
) -> None:
    cost = (metadata.get("usage") or {}).get("cost") if isinstance(metadata, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("parse_status") != "provider_response_validated"
        or metadata.get("requested_model") != expected_model
        or str(metadata.get("provider", "")).lower() != READER_PROVIDER
        or not isinstance(metadata.get("usage"), dict)
        or not isinstance(cost, (int, float))
        or cost < 0
        or (cost == 0 and not isinstance(metadata.get("refusal"), dict))
    ):
        raise RuntimeError("reader provider/model/price provenance mismatch")


def confirmation_refusal_terminal(item_id: str, arm: str, metadata: dict) -> dict:
    _validate_reader_metadata(metadata, expected_model=CONFIRMATION_READER_MODEL)
    if not isinstance(metadata.get("refusal"), dict):
        raise RuntimeError("provider refusal metadata is missing")
    return {
        "id": item_id,
        "arm": arm,
        "status": "completed",
        "answer": None,
        "abstain": True,
        "notes": "provider_refusal",
        "provider_refusal": True,
        "provider": metadata,
    }


def reader_terminal(
    cli: ReaderCli,
    item_id: str,
    arm: str,
    prompt: str,
    *,
    expected_model: str = READER_MODEL,
) -> dict:
    reply = cli.call("reader", READER_SYSTEM_PROMPT, prompt)
    metadata = cli.last_call_metadata
    _validate_reader_metadata(metadata, expected_model=expected_model)
    try:
        parsed = parse_reader_output(reply)
        answer = parsed["answer"]
        if answer is not None:
            answer = answer.strip().upper()
        if not parsed["abstain"] and answer not in list("ABCDE"):
            raise ValueError("reader answer must be exactly one letter A-E")
        return {
            "id": item_id,
            "arm": arm,
            "status": "completed",
            "answer": answer,
            "abstain": parsed["abstain"],
            "notes": parsed["notes"],
            "provider": metadata,
        }
    except (json.JSONDecodeError, ValueError, TypeError) as error:
        return {
            "id": item_id,
            "arm": arm,
            "status": "error",
            "answer": None,
            "abstain": False,
            "error": f"reader_parse: {error}",
            "provider": metadata,
        }


def _append_terminal(
    rows: list[dict], output: Path, authorization_sha256: str, row: dict
) -> None:
    row["authorization_sha256"] = authorization_sha256
    rows.append(row)
    write_jsonl(output, rows)


def _expected_paid_frozen(args) -> dict:
    return {
        "source_jsonl_sha256": gr.sha256_file(args.source.resolve()),
        "lock_sha256": gr.sha256_file(args.lock.resolve()),
        "fast_evidence_sha256": gr.sha256_file(args.fast_evidence.resolve()),
        "fast_gate_sha256": gr.sha256_file(args.fast_gate.resolve()),
        "runner_sha256": gr.sha256_file(Path(__file__)),
        "provider_attempts_sha256": gr.sha256_file(
            SCRIPTS_DIR / "provider_attempts.py"
        ),
    }


def _expected_confirmation_frozen(args) -> dict:
    return {
        "source_jsonl_sha256": gr.sha256_file(args.source.resolve()),
        "selection_sha256": gr.sha256_file(args.selection.resolve()),
        "fast_evidence_sha256": gr.sha256_file(args.fast_evidence.resolve()),
        "fast_gate_sha256": gr.sha256_file(args.fast_gate.resolve()),
        "runner_sha256": gr.sha256_file(Path(__file__)),
        "run_reader_sha256": gr.sha256_file(SCRIPTS_DIR / "run_reader.py"),
        "provider_attempts_sha256": gr.sha256_file(
            SCRIPTS_DIR / "provider_attempts.py"
        ),
    }


def authorize_confirmation(args) -> dict:
    source_rows, selection = load_locked_confirmation(
        args.source.resolve(), args.selection.resolve()
    )
    fast_rows = load_jsonl(args.fast_evidence.resolve())
    validate_evidence_rows(fast_rows, selection["expected_ids"], "fast")
    fast_gate = json.loads(args.fast_gate.read_text(encoding="utf-8"))
    if fast_gate.get("status") != "passed" or fast_gate.get(
        "evidence_jsonl_sha256"
    ) != gr.sha256_file(args.fast_evidence.resolve()):
        raise ValueError("confirmation authorization requires the matching Fast gate")

    pilot_source = load_jsonl(args.pilot_source.resolve())
    pilot_paid = load_jsonl(args.pilot_paid_rows.resolve())
    pilot_by_id = {row["id"]: row for row in pilot_source}
    pilot_full = [row for row in pilot_paid if row.get("arm") == "full_context"]
    if len(pilot_full) != 10 or len(pilot_by_id) != 10:
        raise ValueError("confirmation preflight requires ten pilot full-context rows")
    pilot_prompt_chars = sum(
        len(full_context_prompt(pilot_by_id[row["id"]])) for row in pilot_full
    )
    try:
        pilot_prompt_tokens = sum(
            int(row["provider"]["usage"]["prompt_tokens"]) for row in pilot_full
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("pilot prompt-token accounting is incomplete") from error
    requests = confirmation_reader_requests(source_rows, fast_rows)
    preflight = confirmation_cost_preflight(
        requests,
        pilot_prompt_chars=pilot_prompt_chars,
        pilot_prompt_tokens=pilot_prompt_tokens,
    )
    preflight["pilot_source_sha256"] = gr.sha256_file(args.pilot_source.resolve())
    preflight["pilot_paid_rows_sha256"] = gr.sha256_file(
        args.pilot_paid_rows.resolve()
    )
    if preflight["status"] != "passed":
        raise ValueError("confirmation cost preflight exceeds the authorized ceiling")
    packet = confirmation_authorization_packet(
        _expected_confirmation_frozen(args),
        preflight=preflight,
        authorized_by=args.authorized_by,
        authorized_at=args.authorized_at,
    )
    atomic_write_json(args.out.resolve(), packet)
    return packet


def run_paid_confirmation(args) -> dict:
    source_rows, selection = load_locked_confirmation(
        args.source.resolve(), args.selection.resolve()
    )
    expected_ids = selection["expected_ids"]
    fast_rows = load_jsonl(args.fast_evidence.resolve())
    validate_evidence_rows(fast_rows, expected_ids, "fast")
    fast_gate = json.loads(args.fast_gate.read_text(encoding="utf-8"))
    if fast_gate.get("status") != "passed" or fast_gate.get(
        "evidence_jsonl_sha256"
    ) != gr.sha256_file(args.fast_evidence.resolve()):
        raise ValueError("paid confirmation requires the matching passed Fast gate")

    packet = json.loads(args.authorization.read_text(encoding="utf-8"))
    frozen = _expected_confirmation_frozen(args)
    validate_confirmation_authorization(packet, frozen, packet.get("cost_preflight", {}))
    authorization_sha = packet["authorization"]["authorization_scope_sha256"]
    execution = packet["execution"]
    artifact_dir = args.authorization.resolve().parent
    journal = artifact_dir / execution["journal_path"]
    cache_dir = artifact_dir / execution["cache_dir"]
    raw_output = artifact_dir / execution["raw_rows"]
    closure_path = artifact_dir / execution["closure"]
    census_path = artifact_dir / execution["census"]
    if args.output.resolve() != raw_output.resolve():
        raise ValueError("paid output differs from the authorized path")

    ledger = open_campaign_ledger(
        args.authorization,
        screen_id="horizonbench-confirmation",
        expected_journal_path=journal,
    )
    try:
        snapshot = ledger.snapshot()
        reported, unsettled = restore_spend_from_attempts(snapshot["attempts"])
        cli = ReaderCli(
            "anthropic",
            CONFIRMATION_READER_MODEL,
            CONFIRMATION_READER_MODEL,
            cache_dir,
            max_calls=240,
            max_spend_usd=CONFIRMATION_MAX_SPEND_USD,
            max_price_per_million={
                "prompt": Decimal("5"),
                "completion": Decimal("25"),
            },
            max_output_tokens=CONFIRMATION_MAX_OUTPUT_TOKENS,
        )
        cli.set_provider_attempt_ledger(ledger)
        cli.provider_attempts = len(snapshot["attempts"])
        cli.set_provider_attempt_limit(480)
        cli.restore_spend_state(
            reported_spend_usd=reported, unsettled_liability_usd=unsettled
        )

        terminal_rows = load_jsonl(raw_output)
        keys = [(row.get("id"), row.get("arm")) for row in terminal_rows]
        if len(keys) != len(set(keys)):
            raise ValueError("confirmation resume contains duplicate terminal rows")
        if any(
            row.get("authorization_sha256") != authorization_sha
            or row.get("id") not in expected_ids
            or row.get("arm") not in CONFIRMATION_ARMS
            for row in terminal_rows
        ):
            raise ValueError("confirmation resume row is outside authorized scope")
        by_key = {(row["id"], row["arm"]): row for row in terminal_rows}
        requests = confirmation_reader_requests(source_rows, fast_rows)
        for request in requests:
            key = (request["id"], request["arm"])
            if key in by_key:
                continue
            try:
                try:
                    row = reader_terminal(
                        cli,
                        request["id"],
                        request["arm"],
                        request["prompt"],
                        expected_model=CONFIRMATION_READER_MODEL,
                    )
                except ProviderRefusal:
                    row = confirmation_refusal_terminal(
                        request["id"], request["arm"], cli.last_call_metadata or {}
                    )
                if row["status"] != "completed":
                    raise RuntimeError(row.get("error") or "reader row did not complete")
            except (CallBudgetExceeded, RuntimeError) as error:
                _append_terminal(
                    terminal_rows,
                    raw_output,
                    authorization_sha,
                    {
                        "id": request["id"],
                        "arm": request["arm"],
                        "status": "error",
                        "answer": None,
                        "abstain": False,
                        "error": f"{type(error).__name__}: {error}",
                    },
                )
                raise
            _append_terminal(terminal_rows, raw_output, authorization_sha, row)
            by_key[key] = row

        validate_terminal_rows(
            terminal_rows, expected_ids, arms=CONFIRMATION_ARMS
        )
        snapshot = ledger.snapshot()
        if not provider_attempt_ledger_is_complete(snapshot):
            raise RuntimeError("confirmation reader ledger is incomplete or unpriced")
        reader_cost = Decimal(str(snapshot["reported_cost_usd"]))
        if reader_cost > CONFIRMATION_MAX_SPEND_USD:
            raise RuntimeError("confirmation spend ceiling exceeded")
        closure = ledger.close_campaign(closure_path)
        census = {
            "schema_version": 1,
            "status": "complete",
            "authorization_scope_sha256": authorization_sha,
            "terminal_rows": len(terminal_rows),
            "completed_rows": sum(
                row["status"] == "completed" for row in terminal_rows
            ),
            "error_rows": sum(row["status"] == "error" for row in terminal_rows),
            "reader": {
                "model": CONFIRMATION_READER_MODEL,
                "provider": READER_PROVIDER,
                "provider_attempts": snapshot["provider_attempts"],
                "priced_provider_attempts": snapshot["priced_provider_attempts"],
                "reported_cost_usd": str(reader_cost),
                "attempts_sha256": snapshot["attempts_sha256"],
            },
            "raw_rows_sha256": gr.sha256_file(raw_output),
            "journal_closure": closure,
            "lineage": {
                "repository": gr.repository_identity(REPO_ROOT),
                **frozen,
            },
        }
        atomic_write_json(census_path, census)
        return census
    finally:
        ledger.close()


def _configure_deep_runtime() -> None:
    os.environ.update(
        {
            "MEMPHANT_FACT_EXTRACTION": "0",
            "MEMPHANT_DEEP": "on",
            "MEMPHANT_DEEP_MODEL": DEEP_MODEL,
            "MEMPHANT_DEEP_RESPONSE_MODEL": DEEP_MODEL,
            "MEMPHANT_DEEP_PROVIDERS": DEEP_PROVIDER,
            "MEMPHANT_DEEP_INPUT_PRICE_MICROS_PER_MILLION": "1100000",
            "MEMPHANT_DEEP_OUTPUT_PRICE_MICROS_PER_MILLION": "6600000",
            "MEMPHANT_DEEP_PROMPT_PATH": str(
                REPO_ROOT / "config" / "deep-recall-v1.txt"
            ),
        }
    )


def run_paid_pilot(args) -> dict:
    source_rows, lock = load_locked_sample(args.source.resolve(), args.lock.resolve())
    expected_ids = lock["expected_ids"]
    fast_rows = load_jsonl(args.fast_evidence.resolve())
    validate_evidence_rows(fast_rows, expected_ids, "fast")
    fast_gate = json.loads(args.fast_gate.read_text(encoding="utf-8"))
    if fast_gate.get("status") != "passed" or fast_gate.get(
        "evidence_jsonl_sha256"
    ) != gr.sha256_file(args.fast_evidence.resolve()):
        raise ValueError("paid pilot requires the matching passed Fast gate")

    packet = json.loads(args.authorization.read_text(encoding="utf-8"))
    frozen = _expected_paid_frozen(args)
    validate_pilot_authorization(packet, frozen)
    authorization_sha = packet["authorization"]["authorization_scope_sha256"]
    execution = packet["execution"]
    artifact_dir = args.authorization.resolve().parent
    journal = artifact_dir / execution["journal_path"]
    cache_dir = artifact_dir / execution["cache_dir"]
    raw_output = artifact_dir / execution["raw_rows"]
    deep_cache_path = artifact_dir / execution["deep_cache"]
    closure_path = artifact_dir / execution["closure"]
    census_path = artifact_dir / execution["census"]
    if args.output.resolve() != raw_output.resolve():
        raise ValueError("paid output differs from the authorized path")

    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    _configure_deep_runtime()
    items = [runtime_item(row) for row in source_rows]
    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, database_url, name_prefix="horizon-paid"
    )
    server = gr.Server(
        args.server_bin,
        database_url,
        args.port,
        log_path=artifact_dir / "server-paid.log",
    )
    ledger = None
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        bound, retained = retain_runtime_items(client, items)
        compiled = gr.drain_worker(args.worker_bin, database_url)
        if retained != 943 or compiled != 943:
            raise RuntimeError(
                f"paid runtime lineage mismatch: retained={retained} compiled={compiled}"
            )

        ledger = open_campaign_ledger(
            args.authorization,
            screen_id="horizonbench-pilot",
            expected_journal_path=journal,
        )
        snapshot = ledger.snapshot()
        reported, unsettled = restore_spend_from_attempts(snapshot["attempts"])
        cli = ReaderCli(
            "openrouter",
            READER_MODEL,
            READER_MODEL,
            cache_dir,
            max_calls=30,
            max_spend_usd=READER_MAX_SPEND_USD,
            max_price_per_million={
                "prompt": Decimal("5"),
                "completion": Decimal("25"),
            },
            max_output_tokens=256,
        )
        cli.provider_only = [READER_PROVIDER]
        cli.set_provider_attempt_ledger(ledger)
        cli.provider_attempts = len(snapshot["attempts"])
        cli.set_provider_attempt_limit(60)
        cli.restore_spend_state(
            reported_spend_usd=reported, unsettled_liability_usd=unsettled
        )

        terminal_rows = load_jsonl(raw_output)
        terminal_keys = [(row.get("id"), row.get("arm")) for row in terminal_rows]
        if len(terminal_keys) != len(set(terminal_keys)):
            raise ValueError("paid resume contains duplicate terminal rows")
        if any(
            row.get("authorization_sha256") != authorization_sha
            or row.get("id") not in expected_ids
            or row.get("arm") not in PAID_ARMS
            for row in terminal_rows
        ):
            raise ValueError("paid resume row is outside the authorized scope")
        by_key = {(row["id"], row["arm"]): row for row in terminal_rows}
        source_by_id = {row["id"]: row for row in source_rows}
        fast_by_id = {row["id"]: row for row in fast_rows}

        for item_id in expected_ids:
            if (item_id, "full_context") not in by_key:
                try:
                    row = reader_terminal(
                        cli,
                        item_id,
                        "full_context",
                        full_context_prompt(source_by_id[item_id]),
                    )
                except (CallBudgetExceeded, ProviderRefusal, RuntimeError) as error:
                    _append_terminal(
                        terminal_rows,
                        raw_output,
                        authorization_sha,
                        {
                            "id": item_id,
                            "arm": "full_context",
                            "status": "error",
                            "answer": None,
                            "abstain": False,
                            "error": f"{type(error).__name__}: {error}",
                        },
                    )
                    raise
                _append_terminal(terminal_rows, raw_output, authorization_sha, row)
                by_key[(item_id, "full_context")] = row

        for item_id in expected_ids:
            if (item_id, "fast") not in by_key:
                try:
                    row = reader_terminal(
                        cli,
                        item_id,
                        "fast",
                        evidence_prompt(
                            fast_by_id[item_id]["evidence"],
                            fast_by_id[item_id]["question"],
                        ),
                    )
                except (CallBudgetExceeded, ProviderRefusal, RuntimeError) as error:
                    _append_terminal(
                        terminal_rows,
                        raw_output,
                        authorization_sha,
                        {
                            "id": item_id,
                            "arm": "fast",
                            "status": "error",
                            "answer": None,
                            "abstain": False,
                            "error": f"{type(error).__name__}: {error}",
                        },
                    )
                    raise
                _append_terminal(terminal_rows, raw_output, authorization_sha, row)
                by_key[(item_id, "fast")] = row

        deep_rows = load_jsonl(deep_cache_path)
        if len({row.get("id") for row in deep_rows}) != len(deep_rows):
            raise ValueError("Deep resume cache contains duplicate IDs")
        deep_by_id = {row["id"]: row for row in deep_rows}
        for row in deep_rows:
            if (
                row.get("authorization_sha256") != authorization_sha
                or row.get("id") not in expected_ids
            ):
                raise ValueError("Deep resume cache authorization or ID mismatch")
            validate_deep_completion(row)

        for item in items:
            item_id = item["id"]
            if (item_id, "selective_deep") in by_key:
                continue
            fast_answer = by_key[(item_id, "fast")]
            if fast_answer.get("status") != "completed":
                row = {
                    "id": item_id,
                    "arm": "selective_deep",
                    "status": "error",
                    "answer": None,
                    "abstain": False,
                    "route": "fast_error",
                    "error": "Fast reader row unavailable for selective routing",
                }
            elif selective_route(fast_answer) == "fast":
                row = {
                    "id": item_id,
                    "arm": "selective_deep",
                    "status": "completed",
                    "answer": fast_answer["answer"],
                    "abstain": False,
                    "route": "fast",
                    "provider": fast_answer["provider"],
                }
            else:
                deep_row = deep_by_id.get(item_id)
                if deep_row is None:
                    deep_row = recall_runtime_items(
                        client, [item], bound, "deep", args.k, args.budget_tokens
                    )[0]
                    deep_row["authorization_sha256"] = authorization_sha
                    validate_deep_completion(deep_row)
                    deep_rows.append(deep_row)
                    write_jsonl(deep_cache_path, deep_rows)
                    deep_by_id[item_id] = deep_row
                if len(deep_rows) > 10:
                    raise RuntimeError("Deep call ceiling exceeded")
                deep_micros = sum(row["deep"]["settled_micros"] for row in deep_rows)
                if deep_micros > 3_000_000:
                    raise RuntimeError("Deep spend ceiling exceeded")
                row = reader_terminal(
                    cli,
                    item_id,
                    "selective_deep",
                    evidence_prompt(deep_row["evidence"], deep_row["question"]),
                )
                row["route"] = "deep"
                row["deep"] = deep_row["deep"]
            _append_terminal(terminal_rows, raw_output, authorization_sha, row)
            by_key[(item_id, "selective_deep")] = row

        validate_terminal_rows(terminal_rows, expected_ids)
        snapshot = ledger.snapshot()
        if not provider_attempt_ledger_is_complete(snapshot):
            raise RuntimeError("paid reader ledger is incomplete or unpriced")
        reader_cost = Decimal(str(snapshot["reported_cost_usd"]))
        deep_cost = Decimal(
            sum(row["deep"]["settled_micros"] for row in deep_rows)
        ) / Decimal(1_000_000)
        if reader_cost + deep_cost > COMBINED_MAX_SPEND_USD:
            raise RuntimeError("combined paid pilot spend ceiling exceeded")
        closure = ledger.close_campaign(closure_path)
        census = {
            "schema_version": 1,
            "status": "complete",
            "authorization_scope_sha256": authorization_sha,
            "terminal_rows": len(terminal_rows),
            "completed_rows": sum(
                row["status"] == "completed" for row in terminal_rows
            ),
            "error_rows": sum(row["status"] == "error" for row in terminal_rows),
            "reader": {
                "model": READER_MODEL,
                "provider": READER_PROVIDER,
                "provider_attempts": snapshot["provider_attempts"],
                "priced_provider_attempts": snapshot["priced_provider_attempts"],
                "reported_cost_usd": str(reader_cost),
                "attempts_sha256": snapshot["attempts_sha256"],
            },
            "deep": {
                "model": DEEP_MODEL,
                "provider": DEEP_PROVIDER,
                "calls": len(deep_rows),
                "settled_cost_usd": str(deep_cost),
                "unsettled_cost_usd": "0",
            },
            "combined_cost_usd": str(reader_cost + deep_cost),
            "raw_rows_sha256": gr.sha256_file(raw_output),
            "deep_cache_sha256": (
                gr.sha256_file(deep_cache_path) if deep_cache_path.exists() else None
            ),
            "journal_closure": closure,
            "lineage": {
                "repository": gr.repository_identity(REPO_ROOT),
                **frozen,
                "server_sha256": gr.sha256_file(Path(args.server_bin)),
                "worker_sha256": gr.sha256_file(Path(args.worker_bin)),
                "cli_sha256": gr.sha256_file(Path(args.cli_bin)),
            },
        }
        atomic_write_json(census_path, census)
        return census
    finally:
        if ledger is not None:
            ledger.close()
        server.stop()


def load_locked_sample(source: Path, lock_path: Path) -> tuple[list[dict], dict]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != lock.get("jsonl_sha256"):
        raise ValueError("HorizonBench sample bytes do not match the lock")
    validate_source_revision(lock.get("dataset_revision"))
    rows = [json.loads(line) for line in raw.split(b"\n") if line.strip()]
    census = validate_sample_rows(rows)
    if census["expected_ids"] != lock.get("expected_ids"):
        raise ValueError("HorizonBench sample IDs do not match the lock")
    if census["expected_user_ids"] != lock.get("expected_user_ids"):
        raise ValueError("HorizonBench sample users do not match the lock")
    return rows, lock


def load_locked_confirmation(
    source: Path,
    selection_path: Path,
    *,
    expected_rows: int = 120,
    expected_users: int = 60,
) -> tuple[list[dict], dict]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("status") != "frozen":
        raise ValueError("HorizonBench confirmation selection is not frozen")
    validate_source_revision(selection.get("dataset_revision"))
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != selection.get("source_jsonl_sha256"):
        raise ValueError("HorizonBench confirmation source hash drift")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if (
        len(rows) != expected_rows
        or len({row.get("user_id") for row in rows}) != expected_users
    ):
        raise ValueError("HorizonBench confirmation row or user count drift")
    ids = [row.get("id") for row in rows]
    users = sorted({row.get("user_id") for row in rows})
    if ids != selection.get("expected_ids") or users != selection.get(
        "expected_user_ids"
    ):
        raise ValueError("HorizonBench confirmation IDs or users drift")
    validate_benchmark_rows(
        rows,
        expected_rows=expected_rows,
        expected_users=expected_users,
        expected_generator_counts=dict(Counter(row["generator"] for row in rows)),
    )
    return rows, selection


def validate_source_revision(actual: str) -> None:
    if actual != DATASET_REVISION:
        raise ValueError(
            f"HorizonBench dataset revision drift: {actual!r} != {DATASET_REVISION!r}"
        )


def fetch_source_revision() -> str:
    request = urllib.request.Request(
        f"{HUB_API}/{DATASET_ID}",
        headers={"User-Agent": "MemPhant-HorizonBench/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    revision = payload.get("sha")
    if not isinstance(revision, str):
        raise ValueError("HorizonBench Hub metadata omitted its source revision")
    validate_source_revision(revision)
    return revision


def fetch_sample(output: Path) -> dict:
    revision = fetch_source_revision()
    params = urllib.parse.urlencode(
        {
            "dataset": DATASET_ID,
            "config": "sample",
            "split": "test",
            "offset": 0,
            "length": 10,
        }
    )
    request = urllib.request.Request(
        f"{DATASET_SERVER}/rows?{params}",
        headers={"User-Agent": "MemPhant-HorizonBench/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    rows = [entry["row"] for entry in payload.get("rows", [])]
    census = validate_sample_rows(rows)
    raw = canonical_jsonl_bytes(rows)
    atomic_write(output, raw)
    return {
        "dataset": DATASET_ID,
        "dataset_revision": revision,
        "config": "sample",
        "split": "test",
        "source_url": request.full_url,
        "jsonl_sha256": hashlib.sha256(raw).hexdigest(),
        **census,
    }


def fact_extraction_flag() -> str:
    """Report what actually ran, matching `fact_extraction_from_env` in the runtime."""
    value = os.environ.get("MEMPHANT_FACT_EXTRACTION", "").strip().lower()
    return "fact_extraction=off" if value in {"0", "false", "off"} else "fact_extraction=on"


def fast_gate_evidence_contract(source_sha: str, k: int, budget_tokens: int) -> dict:
    return {
        "schema_version": 1,
        "decisional": False,
        "claim": "The pinned ten-row HorizonBench sample completed the gold-blind Fast construction gate.",
        "power": {
            "test": "descriptive-only (no test)",
            "n": 10,
            "b": 0,
            "c": 0,
            "n_d": 0,
        },
        "mechanism_enabled": True,
        "probe_kind": "gate",
        "mechanism_evidence": "Fast recall ran with MEMPHANT_DEEP=off and produced ten non-degraded evidence rows.",
        "harness": {
            "embed_model": "local sentence-unit embedder",
            "scorer": "construction completeness only; benchmark gold remained quarantined",
            "k": k,
            "budget": budget_tokens,
            "flags": ["fast", fact_extraction_flag(), "deep=off"],
        },
        "corpus": {
            "sha256": source_sha,
            "snapshot_id": f"{DATASET_ID}@{DATASET_REVISION}:sample/test",
            "n_items": 10,
        },
        "instrument_verification": {
            "shipped_rows_verified": True,
            "rows_counted": 10,
            "fields_counted": {
                "conversation": 10,
                "options": 10,
                "correct_letter": 10,
            },
            "license_id": "CC-BY-4.0",
            "license_source": "RECORD_METADATA",
            "license_evidence": "Pinned Hugging Face dataset metadata.",
        },
        "notes": "Non-decisional construction proof; no answer scoring or SOTA claim.",
    }


def build_fast_evidence(args) -> dict:
    source = args.source.expanduser().resolve()
    rows, lock = load_locked_sample(source, args.lock.resolve())
    items = [runtime_item(row) for row in rows]
    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    # ponytail: setdefault, not assignment, so the free ten-row sample can run
    # the extraction-on arm. The sealed confirmation path below stays hardcoded.
    os.environ.setdefault("MEMPHANT_FACT_EXTRACTION", "0")
    os.environ["MEMPHANT_DEEP"] = "off"
    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, database_url, name_prefix="horizon-sample"
    )
    server = gr.Server(
        args.server_bin,
        database_url,
        args.port,
        log_path=args.out.parent / "server-fast.log",
    )
    started = time.monotonic()
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        bound, retained = retain_runtime_items(client, items)
        compiled = gr.drain_worker(args.worker_bin, database_url)
        evidence = recall_runtime_items(
            client, items, bound, "fast", args.k, args.budget_tokens
        )
    finally:
        server.stop()
    validate_evidence_rows(evidence, lock["expected_ids"], "fast")
    evidence_raw = canonical_jsonl_bytes(evidence)
    atomic_write(args.out, evidence_raw)
    report = {
        "schema_version": 1,
        "status": "passed",
        "decisional": False,
        "claim": "The pinned ten-row HorizonBench sample completed the gold-blind Fast construction gate.",
        "source": {
            "dataset": DATASET_ID,
            "dataset_revision": DATASET_REVISION,
            "jsonl_sha256": lock["jsonl_sha256"],
            "expected_ids_sha256": hashlib.sha256(
                json.dumps(lock["expected_ids"], separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "runtime": {
            "arm": "fast",
            "items": len(items),
            "sessions_retained": retained,
            "jobs_compiled": compiled,
            "nonempty_evidence_rows": sum(bool(row["evidence"]) for row in evidence),
            "degraded_rows": sum(bool(row["degraded"]) for row in evidence),
            "k": args.k,
            "budget_tokens": args.budget_tokens,
            "latency_ms": [row["latency_ms"] for row in evidence],
        },
        "gold_quarantine": {
            "runtime_fields": list(PROMPT_FIELDS),
            "scoring_only_fields": list(SCORING_ONLY_FIELDS),
            "mental_state_graph_acquired": False,
        },
        "evidence_jsonl_sha256": hashlib.sha256(evidence_raw).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "evidence_contract": fast_gate_evidence_contract(
            lock["jsonl_sha256"], args.k, args.budget_tokens
        ),
        "lineage": {
            "repository": gr.repository_identity(REPO_ROOT),
            "migrations": gr.migration_identity(REPO_ROOT),
            "runner_sha256": gr.sha256_file(Path(__file__)),
            "test_sha256": gr.sha256_file(
                REPO_ROOT / "tests" / "test_horizonbench_contract.py"
            ),
            "server_sha256": gr.sha256_file(Path(args.server_bin)),
            "worker_sha256": gr.sha256_file(Path(args.worker_bin)),
            "cli_sha256": gr.sha256_file(Path(args.cli_bin)),
        },
    }
    atomic_write_json(args.report_out, report)
    return report


def confirmation_fast_gate_contract(
    source_sha: str, *, k: int, budget_tokens: int
) -> dict:
    return {
        "schema_version": 1,
        "decisional": False,
        "claim": (
            "The frozen 60-user HorizonBench confirmation completed its "
            "incremental gold-blind Fast construction gate."
        ),
        "power": {
            "test": "descriptive-only (no test)",
            "n": 0,
            "b": 0,
            "c": 0,
            "n_d": 0,
        },
        "mechanism_enabled": True,
        "probe_kind": "gate",
        "mechanism_evidence": (
            "Sixty user timelines were retained incrementally and each of 120 "
            "questions produced non-degraded Fast evidence before gold access."
        ),
        "harness": {
            "embed_model": "local sentence-unit embedder",
            "scorer": "construction completeness only; benchmark gold quarantined",
            "k": k,
            "budget": budget_tokens,
            "flags": ["fast", "fact_extraction=off", "deep=off"],
        },
        "corpus": {
            "sha256": source_sha,
            "snapshot_id": f"{DATASET_ID}@{DATASET_REVISION}:benchmark/test:confirmation-v1",
            "n_items": 120,
        },
        "instrument_verification": {
            "shipped_rows_verified": True,
            "rows_counted": 120,
            "fields_counted": {
                "conversation": 120,
                "options": 120,
                "correct_letter": 120,
                "has_evolved": 120,
            },
            "license_id": "CC-BY-4.0",
            "license_source": "RECORD_METADATA",
            "license_evidence": "Pinned Hugging Face dataset metadata.",
        },
        "notes": "Non-decisional construction proof; no answer scoring or SOTA claim.",
    }


def build_confirmation_evidence(args) -> dict:
    source = args.source.expanduser().resolve()
    rows, selection = load_locked_confirmation(source, args.selection.resolve())
    items = confirmation_runtime_items(rows)
    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    os.environ["MEMPHANT_FACT_EXTRACTION"] = "0"
    os.environ["MEMPHANT_DEEP"] = "off"
    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, database_url, name_prefix="horizon-confirmation"
    )
    server = gr.Server(
        args.server_bin,
        database_url,
        args.port,
        log_path=args.out.parent / "server-confirmation-fast.log",
    )
    started = time.monotonic()
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        evidence, retained, compiled = build_incremental_confirmation_evidence(
            client,
            items,
            lambda: gr.drain_worker(args.worker_bin, database_url),
            k=args.k,
            budget_tokens=args.budget_tokens,
        )
    finally:
        server.stop()
    if retained != compiled:
        raise RuntimeError(
            f"confirmation runtime lineage mismatch: retained={retained} compiled={compiled}"
        )
    validate_evidence_rows(evidence, selection["expected_ids"], "fast")
    evidence_raw = canonical_jsonl_bytes(evidence)
    atomic_write(args.out, evidence_raw)
    report = {
        "schema_version": 1,
        "status": "passed",
        "decisional": False,
        "claim": (
            "The frozen HorizonBench confirmation completed its incremental "
            "gold-blind Fast construction gate."
        ),
        "source": {
            "dataset": DATASET_ID,
            "dataset_revision": DATASET_REVISION,
            "jsonl_sha256": selection["source_jsonl_sha256"],
            "selection_sha256": gr.sha256_file(args.selection.resolve()),
            "rows": 120,
            "users": 60,
        },
        "runtime": {
            "arm": "fast",
            "items": len(items),
            "user_contexts": len({item["context_ref"] for item in items}),
            "turn_episodes_retained": retained,
            "jobs_compiled": compiled,
            "nonempty_evidence_rows": sum(bool(row["evidence"]) for row in evidence),
            "degraded_rows": sum(bool(row["degraded"]) for row in evidence),
            "k": args.k,
            "budget_tokens": args.budget_tokens,
            "latency_ms": [row["latency_ms"] for row in evidence],
        },
        "gold_quarantine": {
            "runtime_fields": list(PROMPT_FIELDS),
            "scoring_only_fields": list(SCORING_ONLY_FIELDS),
            "mental_state_graphs_acquired": False,
        },
        "evidence_jsonl_sha256": hashlib.sha256(evidence_raw).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "evidence_contract": confirmation_fast_gate_contract(
            selection["source_jsonl_sha256"],
            k=args.k,
            budget_tokens=args.budget_tokens,
        ),
        "lineage": {
            "repository": gr.repository_identity(REPO_ROOT),
            "migrations": gr.migration_identity(REPO_ROOT),
            "runner_sha256": gr.sha256_file(Path(__file__)),
            "test_sha256": gr.sha256_file(
                REPO_ROOT / "tests" / "test_horizonbench_contract.py"
            ),
            "server_sha256": gr.sha256_file(Path(args.server_bin)),
            "worker_sha256": gr.sha256_file(Path(args.worker_bin)),
            "cli_sha256": gr.sha256_file(Path(args.cli_bin)),
        },
    }
    atomic_write_json(args.report_out, report)
    return report


def build_analysis(args) -> dict:
    source_rows, lock = load_locked_sample(args.source.resolve(), args.lock.resolve())
    terminal_rows = load_jsonl(args.paid_rows.resolve())
    census = json.loads(args.paid_census.read_text(encoding="utf-8"))
    if (
        census.get("status") != "complete"
        or census.get("raw_rows_sha256") != gr.sha256_file(args.paid_rows.resolve())
        or census.get("terminal_rows") != 30
        or census.get("error_rows") != 0
    ):
        raise ValueError("analysis requires the complete hash-matched paid census")
    analysis = analyze_paid_rows(
        source_rows,
        terminal_rows,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_samples=args.bootstrap_samples,
    )
    result = {
        "schema_version": 1,
        "status": "complete",
        "decisional": False,
        "claim": "The ten-row HorizonBench sample passed its process gate but is underpowered and does not establish SOTA.",
        "source": {
            "dataset": DATASET_ID,
            "dataset_revision": DATASET_REVISION,
            "jsonl_sha256": lock["jsonl_sha256"],
            "rows": len(source_rows),
            "users": len({row["user_id"] for row in source_rows}),
        },
        "analysis": analysis,
        "published_reference": {
            "overall_accuracy": 0.528,
            "evolved_accuracy": 0.513,
            "comparison_status": "not_comparable_ten_row_post_release_reader_diagnostic",
            "near_sota": False,
        },
        "accounting": {
            "authorization_scope_sha256": census["authorization_scope_sha256"],
            "paid_census_sha256": gr.sha256_file(args.paid_census.resolve()),
            "combined_cost_usd": census["combined_cost_usd"],
            "reader_provider_attempts": census["reader"]["provider_attempts"],
            "deep_calls": census["deep"]["calls"],
            "unsettled_cost_usd": census["deep"]["unsettled_cost_usd"],
        },
        "claim_boundary": {
            "overall_sota": False,
            "preference_sota": False,
            "storage_sota": False,
            "code_sota": False,
            "next_authorized": "write a separately preregistered powered HorizonBench plan; do not run it yet",
        },
        "evidence_contract": pilot_evidence_contract(lock["jsonl_sha256"], analysis),
        "lineage": {
            "repository": gr.repository_identity(REPO_ROOT),
            "runner_sha256": gr.sha256_file(Path(__file__)),
            "test_sha256": gr.sha256_file(
                REPO_ROOT / "tests" / "test_horizonbench_contract.py"
            ),
            "paid_rows_sha256": gr.sha256_file(args.paid_rows.resolve()),
            "paid_census_sha256": gr.sha256_file(args.paid_census.resolve()),
        },
    }
    atomic_write_json(args.output, result)
    return result


def build_confirmation_analysis(args) -> dict:
    source_rows, selection = load_locked_confirmation(
        args.source.resolve(), args.selection.resolve()
    )
    terminal_rows = load_jsonl(args.paid_rows.resolve())
    census = json.loads(args.paid_census.read_text(encoding="utf-8"))
    if (
        census.get("status") != "complete"
        or census.get("raw_rows_sha256")
        != gr.sha256_file(args.paid_rows.resolve())
        or census.get("terminal_rows") != 240
        or census.get("error_rows") != 0
    ):
        raise ValueError(
            "confirmation analysis requires the complete hash-matched paid census"
        )
    analysis = analyze_confirmation_rows(
        source_rows,
        terminal_rows,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_samples=args.bootstrap_samples,
    )
    passed = analysis["verdict"]["outcome"] == "pass"
    result = {
        "schema_version": 1,
        "status": "complete",
        "decisional": analysis["paired_fast_vs_full"]["discordant"] >= 6,
        "claim": (
            "Fast passed the frozen held-out paired HorizonBench confirmation against full context."
            if passed
            else "Fast did not pass the frozen held-out paired HorizonBench confirmation against full context."
        ),
        "source": {
            "dataset": DATASET_ID,
            "dataset_revision": DATASET_REVISION,
            "jsonl_sha256": selection["source_jsonl_sha256"],
            "selection_sha256": gr.sha256_file(args.selection.resolve()),
            "rows": len(source_rows),
            "users": len({row["user_id"] for row in source_rows}),
        },
        "analysis": analysis,
        "published_reference": {
            "overall_accuracy": 0.528,
            "evolved_accuracy": 0.513,
            "comparison_status": "held_out_tranche_not_complete_official_split",
            "near_sota": False,
        },
        "accounting": {
            "authorization_scope_sha256": census["authorization_scope_sha256"],
            "paid_census_sha256": gr.sha256_file(args.paid_census.resolve()),
            "reader_cost_usd": census["reader"]["reported_cost_usd"],
            "provider_attempts": census["reader"]["provider_attempts"],
            "unsettled_cost_usd": "0",
        },
        "claim_boundary": {
            "held_out_preference_confirmation": passed,
            "official_full_split": False,
            "overall_sota": False,
            "cross_axis_near_sota": False,
            "next_authorized": "none; stop after confirmation verdict",
        },
        "evidence_contract": confirmation_result_contract(
            selection["source_jsonl_sha256"], analysis
        ),
        "lineage": {
            "repository": gr.repository_identity(REPO_ROOT),
            "runner_sha256": gr.sha256_file(Path(__file__)),
            "test_sha256": gr.sha256_file(
                REPO_ROOT / "tests" / "test_horizonbench_contract.py"
            ),
            "paid_rows_sha256": gr.sha256_file(args.paid_rows.resolve()),
            "paid_census_sha256": gr.sha256_file(args.paid_census.resolve()),
        },
    }
    atomic_write_json(args.output.resolve(), result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch-sample")
    fetch.add_argument("--out", required=True, type=Path)
    fetch.add_argument("--lock-out", type=Path)
    census = subparsers.add_parser("census-full")
    census.add_argument(
        "--cache-root",
        default=Path("~/.cache/memphant-bench/horizonbench"),
        type=Path,
    )
    census.add_argument("--lock-out", required=True, type=Path)
    census.add_argument("--report-out", required=True, type=Path)
    selection = subparsers.add_parser("select-confirmation")
    selection.add_argument("--full-lock", required=True, type=Path)
    selection.add_argument(
        "--sample-lock",
        default=REPO_ROOT / "benchmarks/manifests/horizonbench.sample.v1.json",
        type=Path,
    )
    selection.add_argument(
        "--cache-root",
        default=Path("~/.cache/memphant-bench/horizonbench"),
        type=Path,
    )
    selection.add_argument("--seed", required=True)
    selection.add_argument("--out", required=True, type=Path)
    selection.add_argument("--report-out", required=True, type=Path)
    evidence = subparsers.add_parser("build-fast-evidence")
    evidence.add_argument("--source", required=True, type=Path)
    evidence.add_argument(
        "--lock",
        default=REPO_ROOT / "benchmarks/manifests/horizonbench.sample.v1.json",
        type=Path,
    )
    evidence.add_argument("--out", required=True, type=Path)
    evidence.add_argument("--report-out", required=True, type=Path)
    evidence.add_argument("--k", type=int, default=20)
    evidence.add_argument("--budget-tokens", type=int, default=16384)
    evidence.add_argument("--port", type=int, default=39483)
    evidence.add_argument(
        "--database-url",
        default="postgres://memphant:memphant@localhost:5432/memphant",
    )
    evidence.add_argument(
        "--server-bin", default=str(REPO_ROOT / "target/release/memphant-server")
    )
    evidence.add_argument(
        "--worker-bin", default=str(REPO_ROOT / "target/release/memphant-worker")
    )
    evidence.add_argument(
        "--cli-bin", default=str(REPO_ROOT / "target/release/memphant-cli")
    )
    confirmation_evidence = subparsers.add_parser("build-confirmation-evidence")
    confirmation_evidence.add_argument("--source", required=True, type=Path)
    confirmation_evidence.add_argument("--selection", required=True, type=Path)
    confirmation_evidence.add_argument("--out", required=True, type=Path)
    confirmation_evidence.add_argument("--report-out", required=True, type=Path)
    confirmation_evidence.add_argument("--k", type=int, default=20)
    confirmation_evidence.add_argument("--budget-tokens", type=int, default=16384)
    confirmation_evidence.add_argument("--port", type=int, default=39485)
    confirmation_evidence.add_argument(
        "--database-url",
        default="postgres://memphant:memphant@localhost:5432/memphant",
    )
    confirmation_evidence.add_argument(
        "--server-bin", default=str(REPO_ROOT / "target/release/memphant-server")
    )
    confirmation_evidence.add_argument(
        "--worker-bin", default=str(REPO_ROOT / "target/release/memphant-worker")
    )
    confirmation_evidence.add_argument(
        "--cli-bin", default=str(REPO_ROOT / "target/release/memphant-cli")
    )
    confirmation_authorize = subparsers.add_parser("authorize-confirmation")
    confirmation_authorize.add_argument("--source", required=True, type=Path)
    confirmation_authorize.add_argument("--selection", required=True, type=Path)
    confirmation_authorize.add_argument("--fast-evidence", required=True, type=Path)
    confirmation_authorize.add_argument("--fast-gate", required=True, type=Path)
    confirmation_authorize.add_argument(
        "--pilot-source",
        default=Path.home()
        / ".cache/memphant-bench/horizonbench"
        / DATASET_REVISION
        / "sample.jsonl",
        type=Path,
    )
    confirmation_authorize.add_argument(
        "--pilot-paid-rows",
        default=REPO_ROOT
        / "docs/build-log/artifacts/horizonbench-pilot/paid-rows.jsonl",
        type=Path,
    )
    confirmation_authorize.add_argument("--authorized-by", required=True)
    confirmation_authorize.add_argument("--authorized-at", required=True)
    confirmation_authorize.add_argument("--out", required=True, type=Path)
    confirmation_paid = subparsers.add_parser("run-paid-confirmation")
    confirmation_paid.add_argument("--source", required=True, type=Path)
    confirmation_paid.add_argument("--selection", required=True, type=Path)
    confirmation_paid.add_argument("--fast-evidence", required=True, type=Path)
    confirmation_paid.add_argument("--fast-gate", required=True, type=Path)
    confirmation_paid.add_argument("--authorization", required=True, type=Path)
    confirmation_paid.add_argument("--output", required=True, type=Path)
    paid = subparsers.add_parser("run-paid-pilot")
    paid.add_argument("--source", required=True, type=Path)
    paid.add_argument(
        "--lock",
        default=REPO_ROOT / "benchmarks/manifests/horizonbench.sample.v1.json",
        type=Path,
    )
    paid.add_argument(
        "--fast-evidence",
        default=REPO_ROOT
        / "docs/build-log/artifacts/horizonbench-pilot/fast-evidence.jsonl",
        type=Path,
    )
    paid.add_argument(
        "--fast-gate",
        default=REPO_ROOT
        / "docs/build-log/artifacts/horizonbench-pilot/fast-gate.json",
        type=Path,
    )
    paid.add_argument("--authorization", required=True, type=Path)
    paid.add_argument("--output", required=True, type=Path)
    paid.add_argument("--k", type=int, default=20)
    paid.add_argument("--budget-tokens", type=int, default=16384)
    paid.add_argument("--port", type=int, default=39484)
    paid.add_argument(
        "--database-url",
        default="postgres://memphant:memphant@localhost:5432/memphant",
    )
    paid.add_argument(
        "--server-bin", default=str(REPO_ROOT / "target/release/memphant-server")
    )
    paid.add_argument(
        "--worker-bin", default=str(REPO_ROOT / "target/release/memphant-worker")
    )
    paid.add_argument(
        "--cli-bin", default=str(REPO_ROOT / "target/release/memphant-cli")
    )
    analysis = subparsers.add_parser("analyze")
    analysis.add_argument("--source", required=True, type=Path)
    analysis.add_argument(
        "--lock",
        default=REPO_ROOT / "benchmarks/manifests/horizonbench.sample.v1.json",
        type=Path,
    )
    analysis.add_argument("--paid-rows", required=True, type=Path)
    analysis.add_argument("--paid-census", required=True, type=Path)
    analysis.add_argument("--output", required=True, type=Path)
    analysis.add_argument("--bootstrap-seed", type=int, default=20260803)
    analysis.add_argument("--bootstrap-samples", type=int, default=20_000)
    confirmation_analysis = subparsers.add_parser("analyze-confirmation")
    confirmation_analysis.add_argument("--source", required=True, type=Path)
    confirmation_analysis.add_argument("--selection", required=True, type=Path)
    confirmation_analysis.add_argument("--paid-rows", required=True, type=Path)
    confirmation_analysis.add_argument("--paid-census", required=True, type=Path)
    confirmation_analysis.add_argument("--output", required=True, type=Path)
    confirmation_analysis.add_argument(
        "--bootstrap-seed", type=int, default=20260803
    )
    confirmation_analysis.add_argument(
        "--bootstrap-samples", type=int, default=20_000
    )
    args = parser.parse_args()
    if args.command == "fetch-sample":
        lock = fetch_sample(args.out)
        encoded = json.dumps(lock, indent=2, sort_keys=True).encode() + b"\n"
        if args.lock_out:
            atomic_write(args.lock_out, encoded)
        else:
            print(encoded.decode(), end="")
    elif args.command == "census-full":
        report = census_full(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "select-confirmation":
        report = select_confirmation(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "build-fast-evidence":
        report = build_fast_evidence(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "build-confirmation-evidence":
        report = build_confirmation_evidence(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "authorize-confirmation":
        report = authorize_confirmation(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "run-paid-confirmation":
        report = run_paid_confirmation(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "run-paid-pilot":
        report = run_paid_pilot(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "analyze":
        report = build_analysis(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "analyze-confirmation":
        report = build_confirmation_analysis(args)
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
