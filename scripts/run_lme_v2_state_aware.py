#!/usr/bin/env python3
"""No-model LongMemEval-V2 state-aware census and proof validators.

The paired risk-difference lower bound is a conservative exact construction:
one-sided Clopper-Pearson bounds for the two discordant marginal probabilities
use alpha/2 each, then ``lower(p10) - upper(p01)``.  Each discordant cell count
is marginally binomial and Bonferroni gives simultaneous coverage of at least
95% without assuming the paired cells are independent.  Decimal bisection only
locates the exact binomial-tail inversions; it does not replace them with a
normal or unpaired approximation.
"""

from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
from fractions import Fraction
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from math import comb
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import threading
from datetime import datetime, timezone
from typing import Any
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit

import fcntl

from benchmarks.longmemeval_v2.construction_authority import (
    derive_construction_receipts as _derive_canonical_construction_receipts,
    load_canonical_binding as _load_canonical_construction_binding,
    rust_json as _rust_json,
    validate_cache_receipt as _validate_exact_cache_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
OPENING_NANOS = 5_141_664_250
CONTINGENCY_NANOS = 10_000_000_000
HARD_CEILING_NANOS = 200_000_000_000
QUESTION_COUNT = 451
CONSTRUCTION_CANARY_PLAN_COUNT = 64
CONSTRUCTION_CANARY_QUANTILES = 4
CONSTRUCTION_CANARY_ALPHA = Decimal("0.05")
CONSTRUCTION_CANARY_FAILURE_LIMIT = Decimal("0.15")
CONSTRUCTION_CANARY_MAX_TRANSIENT_RETRIES = 1
FORMULA = "5141664250+C+2*R_sum+451*S+10000000000<=200000000000"
SHA256 = re.compile(r"[0-9a-f]{64}")
ORACLE_KEYS = {
    "answer",
    "answer_gold",
    "correct",
    "deep_correct",
    "eval_function",
    "fast_correct",
    "gold",
    "judge_score",
    "reference",
    "score",
}
V1_CAMPAIGN_ARTIFACT_ROOT = ROOT / "docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot"
V1_ABANDONMENT_PROOF = (
    ROOT
    / "docs/build-log/artifacts/state-memory-sota/longmemeval-v2-v1-abandonment.json"
)
V1_FAILED_SOURCE_INVENTORY = (
    ROOT / "benchmarks/manifests/longmemeval_v2.v1_failed_sources.json"
)
CAMPAIGN_ARTIFACT_ROOT = ROOT / "docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot-v2"
CANONICAL_CAMPAIGN_CENSUS = CAMPAIGN_ARTIFACT_ROOT / "CAMPAIGN-CENSUS.json"
CANONICAL_CAMPAIGN_MANIFEST = ROOT / "benchmarks/manifests/longmemeval_v2.state_aware_full.v2.json"
CANONICAL_CAMPAIGN_AUTHORIZATION = CAMPAIGN_ARTIFACT_ROOT / "CAMPAIGN-AUTHORIZATION.json"
QWEN_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/qwen/qwen3.5-9b/endpoints"
OPENAI_GPT52_URL = "https://developers.openai.com/api/docs/models/gpt-5.2"

# These builders mirror the two pinned official qa_eval_metrics.py prompt
# shapes.  Their source file hash is checked before they are used; keeping the
# shapes here lets the census measure requests without importing evaluator code
# or parsing any question/answer field.
ABSTENTION_JUDGE_SYSTEM_PROMPT = (
    "You are a strict grader for flawed-premise (abstention) questions. Judge whether a model answer correctly identifies that the question premise is wrong, consistent with the reference answer. If the model follows the flawed premise and gives a concrete answer under that premise, it must be graded 0. If the model's final answer is just UNKNOWN / cannot determine without identifying the flaw, grade 0. If the model is contradictory (both rejects premise and also gives a concrete premise-following answer), grade 0. Paraphrases are allowed when they preserve the same core flaw described by the reference answer."
)
GOTCHAS_JUDGE_SYSTEM_PROMPT = (
    "You are a strict grader for gotchas-style insight questions. The reference answer describes the key insight(s). Grade 1 if the model response includes at least one correct insight point from the reference answer (paraphrase allowed), and does not contradict any reference point. If the model's direction is wrong, or it contains contradictions against any reference point, grade 0. If the model gives multiple points, partial coverage is enough for 1 as long as no contradictions appear."
)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_rust_json(value: object) -> str:
    return hashlib.sha256(_rust_json(value)).hexdigest()


def _campaign_artifact_paths(root: Path) -> dict[str, str]:
    names = {
        "journal": "CAMPAIGN-ATTEMPTS.jsonl",
        "construction_subledger": "CONSTRUCTION-ATTEMPTS.jsonl",
        "construction_dispatches": "private-construction-dispatches",
        "construction_wave": "CONSTRUCTION-WAVE.json",
        "construction_progress": "CONSTRUCTION-PROGRESS.json",
        "remaining_construction_progress": "REMAINING-CONSTRUCTION-PROGRESS.json",
        "construction_input": "CONSTRUCTION-RESOURCES.jsonl",
        "prefix_plans": "PREFIX-12-CONSTRUCTION-PLANS.json",
        "remaining_construction_plans": "REMAINING-439-CONSTRUCTION-PLANS.json",
        "construction_settlement": "CONSTRUCTION-SETTLEMENT.json",
        "construction_retries": "CONSTRUCTION-RETRIES",
        "construction_canary_plans": "CONSTRUCTION-CANARY-PLANS.json",
        "construction_canary_progress": "CONSTRUCTION-CANARY-PROGRESS.json",
        "construction_canary_gate": "CONSTRUCTION-CANARY-GATE.json",
        "observation_cache": "observation-cache",
        "cache_hits": "cache-hits",
        "construction_bindings": "CONSTRUCTION-BINDINGS",
        "scratch": "scratch",
        "case_banks": "case-banks",
        "execution_plan": "EXECUTION-PLAN.json",
        "reservation_plan": "ROW-RESERVATION-PLAN.json",
        "private_reader_outputs": "private-reader-outputs",
        "private_prefix": "private-reader-outputs/PREFIX-12-PRIVATE.json",
        "sealed_prefix": "PREFIX-12.sealed",
        "public_prefix_status": "PREFIX-12-STATUS.json",
        "remaining_commitment": "REMAINING-439-COMMITMENT.json",
        "judge_outputs": "private-judge-outputs",
        "official_derivation": "NATIVE-OFFICIAL-DERIVATION.json",
        "official_metrics": "OFFICIAL-METRICS.json",
        "native_package": "NATIVE-OFFICIAL-PACKAGE.json",
        "closure": "CAMPAIGN-CLOSURE.json",
    }
    return {key: str((root / name).resolve()) for key, name in names.items()}


def campaign_artifact_paths() -> dict[str, str]:
    """The sole canonical path set; paid entrypoints must not accept overrides."""
    return _campaign_artifact_paths(CAMPAIGN_ARTIFACT_ROOT)


def acquire_official_runtime_code(data_root: Path) -> tuple[Path, dict[str, object]]:
    """Acquire only pinned upstream code; never invoke its dataset downloader."""
    lock_path = ROOT / "benchmarks/manifests/longmemeval_v2.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    commit = lock.get("code", {}).get("commit")
    files = lock.get("code", {}).get("files")
    if (
        not isinstance(commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or not isinstance(files, dict)
        or "memory_modules/memory.py" not in files
    ):
        raise RuntimeError("official runtime code lock is incomplete")
    runtime_root = data_root.resolve() / "runtime-code" / commit
    official_dir = runtime_root / "official"
    archive = runtime_root / "official.tar.gz"
    proof_path = runtime_root / "RUNTIME-CODE.json"

    def verify_checkpoint() -> dict[str, object]:
        sys.path.insert(0, str(ROOT / "scripts"))
        import run_longmemeval_v2 as adapter

        adapter.verify_code(official_dir, files)
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        core = {key: value for key, value in proof.items() if key != "proof_sha256"}
        if (
            proof.get("proof_sha256") != sha256_json(core)
            or proof.get("commit") != commit
            or proof.get("release_lock_sha256") != _sha256_file(lock_path)
            or proof.get("archive", {}).get("bytes") != archive.stat().st_size
            or proof.get("archive", {}).get("sha256") != _sha256_file(archive)
            or proof.get("files") != files
            or proof.get("files_sha256") != sha256_json(files)
        ):
            raise RuntimeError("official runtime code checkpoint drift")
        return proof

    if runtime_root.exists():
        if not (official_dir.is_dir() and archive.is_file() and proof_path.is_file()):
            raise RuntimeError("official runtime code checkpoint is incomplete")
        return official_dir, verify_checkpoint()
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    staging = runtime_root.parent / (".staging-" + commit)
    staging.mkdir(mode=0o700)
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import run_longmemeval_v2 as adapter

        staged_archive = staging / "official.tar.gz"
        adapter._download(adapter.release_urls(lock)["code_archive"], staged_archive)
        extracted = staging / "extracted"
        extracted.mkdir()
        extracted_root = adapter._extract_archive(staged_archive, extracted)
        adapter.verify_code(extracted_root, files)
        extracted_root.replace(staging / "official")
        shutil.rmtree(extracted)
        core = {
            "schema_version": 1,
            "commit": commit,
            "release_lock_sha256": _sha256_file(lock_path),
            "archive": {
                "bytes": staged_archive.stat().st_size,
                "sha256": _sha256_file(staged_archive),
            },
            "files": files,
            "files_sha256": sha256_json(files),
        }
        _create_json(staging / "RUNTIME-CODE.json", {
            **core, "proof_sha256": sha256_json(core)
        })
        os.replace(staging, runtime_root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return official_dir, verify_checkpoint()


def _fetch_public_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "memphant-campaign-authority/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"provider authority refresh failed: HTTP {response.status}")
        return response.read()


def refresh_campaign_provider_authority(fetch=_fetch_public_bytes) -> dict[str, object]:
    """Refresh public route/price authority without reading provider credentials."""
    qwen_bytes = fetch(QWEN_ENDPOINTS_URL)
    openai_bytes = fetch(OPENAI_GPT52_URL)
    qwen = json.loads(qwen_bytes)
    endpoints = qwen.get("data", {}).get("endpoints", [])
    deepinfra = [
        endpoint
        for endpoint in endpoints
        if isinstance(endpoint, dict) and endpoint.get("provider_name") == "DeepInfra"
    ]
    if len(deepinfra) != 1:
        raise RuntimeError("Qwen DeepInfra route authority is missing or ambiguous")
    endpoint = deepinfra[0]
    required_parameters = {
        "seed",
        "response_format",
        "structured_outputs",
        "max_tokens",
        "reasoning",
    }
    reasoning = qwen.get("data", {}).get("reasoning")
    supported_efforts = (
        reasoning.get("supported_efforts") if isinstance(reasoning, dict) else None
    )
    reasoning_none_permitted = (
        isinstance(reasoning, dict)
        and reasoning.get("mandatory") is False
        and (
            supported_efforts is None
            or (
                isinstance(supported_efforts, list)
                and "none" in supported_efforts
            )
        )
    )
    normalized_qwen = {
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "response_model": qwen.get("data", {}).get("id"),
        "provider": endpoint.get("provider_name"),
        "input_price_usd_per_token": endpoint.get("pricing", {}).get("prompt"),
        "output_price_usd_per_token": endpoint.get("pricing", {}).get("completion"),
        "context_length": endpoint.get("context_length"),
        "max_completion_tokens": endpoint.get("max_completion_tokens"),
        "required_parameters_supported": required_parameters.issubset(
            set(endpoint.get("supported_parameters", []))
        ),
        "reasoning_supported_efforts": (
            sorted(supported_efforts)
            if isinstance(supported_efforts, list)
            else supported_efforts
        ),
        "reasoning_mandatory": (
            reasoning.get("mandatory") if isinstance(reasoning, dict) else None
        ),
        "reasoning_none_permitted": reasoning_none_permitted,
        "reasoning_metadata": reasoning,
        "status": endpoint.get("status"),
    }
    expected_qwen = {
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "response_model": "qwen/qwen3.5-9b",
        "provider": "DeepInfra",
        "input_price_usd_per_token": "0.0000001",
        "output_price_usd_per_token": "0.00000015",
        "context_length": 262144,
        "max_completion_tokens": 81920,
        "required_parameters_supported": True,
        "status": 0,
    }
    if (
        {key: normalized_qwen.get(key) for key in expected_qwen} != expected_qwen
        or normalized_qwen["required_parameters_supported"] is not True
        or normalized_qwen["reasoning_none_permitted"] is not True
    ):
        raise RuntimeError("Qwen DeepInfra route or price authority drift")
    openai = openai_bytes.decode("utf-8")
    required_openai_fragments = (
        "gpt-5.2-2025-12-11",
        "400,000<!-- --> context window",
        "128,000<!-- --> max output tokens",
        "$1.75",
        "$14.00",
        "Reasoning.effort supports: none (default), low, medium, high and xhigh.",
    )
    if any(fragment not in openai for fragment in required_openai_fragments):
        raise RuntimeError("native GPT-5.2 route or price authority drift")
    normalized_openai = {
        "requested_model": "gpt-5.2-2025-12-11",
        "provider": "OpenAI",
        "input_price_usd_per_million": "1.75",
        "output_price_usd_per_million": "14.00",
        "context_length": 400000,
        "max_output_tokens": 128000,
        "reasoning_effort": "medium",
    }
    normalized = {"qwen_deepinfra": normalized_qwen, "openai_native_judge": normalized_openai}
    return {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "normalized": normalized,
        "normalized_sha256": sha256_json(normalized),
        "sources": {
            QWEN_ENDPOINTS_URL: hashlib.sha256(qwen_bytes).hexdigest(),
            OPENAI_GPT52_URL: hashlib.sha256(openai_bytes).hexdigest(),
        },
    }


def _validate_runtime_provider_authority(packet: dict[str, object]) -> None:
    authority = packet.get("provider_authority")
    if not isinstance(authority, dict):
        raise RuntimeError("campaign provider authority is missing")
    current = refresh_campaign_provider_authority()
    if (
        current.get("normalized_sha256") != authority.get("normalized_sha256")
        or current.get("normalized") != authority.get("normalized")
    ):
        raise RuntimeError("provider route, price, or reasoning authority changed")


def exact_mcnemar(wins: int, losses: int) -> Fraction:
    if type(wins) is not int or type(losses) is not int or wins < 0 or losses < 0:
        raise RuntimeError("McNemar discordant counts must be non-negative integers")
    discordant = wins + losses
    if discordant == 0:
        return Fraction(1, 1)
    return Fraction(sum(comb(discordant, k) for k in range(wins, discordant + 1)), 2**discordant)


def _binomial_cdf(k: int, n: int, probability: Decimal) -> Decimal:
    if k < 0:
        return Decimal(0)
    if k >= n:
        return Decimal(1)
    complement = Decimal(1) - probability
    return sum(
        Decimal(comb(n, index))
        * probability**index
        * complement ** (n - index)
        for index in range(k + 1)
    )


def _binomial_survival(k: int, n: int, probability: Decimal) -> Decimal:
    if k <= 0:
        return Decimal(1)
    if k > n:
        return Decimal(0)
    complement = Decimal(1) - probability
    return sum(
        Decimal(comb(n, index))
        * probability**index
        * complement ** (n - index)
        for index in range(k, n + 1)
    )


def _clopper_pearson_lower(successes: int, total: int, tail: Decimal) -> Decimal:
    if successes == 0:
        return Decimal(0)
    lo, hi = Decimal(0), Decimal(1)
    for _ in range(96):
        mid = (lo + hi) / 2
        if _binomial_survival(successes, total, mid) < tail:
            lo = mid
        else:
            hi = mid
    return lo


def _clopper_pearson_upper(successes: int, total: int, tail: Decimal) -> Decimal:
    if successes == total:
        return Decimal(1)
    lo, hi = Decimal(0), Decimal(1)
    for _ in range(96):
        mid = (lo + hi) / 2
        if _binomial_cdf(successes, total, mid) > tail:
            lo = mid
        else:
            hi = mid
    return hi


def _maximum_failures_below_exact_upper_bound(
    total: int, alpha: Decimal, limit: Decimal
) -> int:
    accepted = [
        failures
        for failures in range(total + 1)
        if _clopper_pearson_upper(failures, total, alpha) < limit
    ]
    return max(accepted, default=-1)


def paired_risk_difference_lower_bound(
    wins: int, losses: int, total: int = QUESTION_COUNT
) -> Decimal:
    if wins < 0 or losses < 0 or wins + losses > total:
        raise RuntimeError("paired risk-difference counts are invalid")
    with localcontext() as context:
        context.prec = 80
        bonferroni_tail = Decimal("0.025")
        return +(
            _clopper_pearson_lower(wins, total, bonferroni_tail)
            - _clopper_pearson_upper(losses, total, bonferroni_tail)
        )


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def validate_paired_results(package: dict[str, object]) -> dict[str, object]:
    pairs = package.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != QUESTION_COUNT:
        raise RuntimeError("paired result must contain exactly 451 pairs")
    ids: set[str] = set()
    wins = losses = premise_regressions = 0
    fully_settled = True
    for row in pairs:
        if not isinstance(row, dict):
            raise RuntimeError("paired result row is invalid")
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id or question_id in ids:
            raise RuntimeError("paired result question ids are missing or duplicated")
        ids.add(question_id)
        fast = row.get("fast_correct")
        deep = row.get("deep_correct")
        if type(fast) is not bool or type(deep) is not bool:
            raise RuntimeError("paired result has a missing native-judge score")
        wins += int(deep and not fast)
        losses += int(fast and not deep)
        premise_regressions += int(
            row.get("ability") == "premise_awareness" and fast and not deep
        )
        fully_settled &= (
            row.get("native_judge_valid") is True
            and row.get("settled") is True
            and _valid_sha256(row.get("receipt_sha256"))
        )

    p_value = exact_mcnemar(wins, losses)
    effect = Fraction(wins - losses, QUESTION_COUNT)
    lower = paired_risk_difference_lower_bound(wins, losses)
    lafs_gain = Decimal(str(package.get("lafs_gain")))
    internal = (
        fully_settled
        and p_value <= Fraction(1, 20)
        and lower > 0
        and effect >= Fraction(1, 20)
        and premise_regressions == 0
        and lafs_gain > 0
    )
    scores = package.get("published_leaderboard_scores")
    if not isinstance(scores, list) or not scores:
        scores = []
    submission_score = Decimal(str(package.get("submission_score")))
    external_sota = (
        internal
        and package.get("accepted_submission") is True
        and bool(scores)
        and all(submission_score > Decimal(str(score)) for score in scores)
    )
    return {
        "pairs": QUESTION_COUNT,
        "wins": wins,
        "losses": losses,
        "discordant": wins + losses,
        "mcnemar_p_numerator": p_value.numerator,
        "mcnemar_p_denominator": p_value.denominator,
        "effect_numerator": effect.numerator,
        "effect_denominator": effect.denominator,
        "paired_risk_difference_lower_bound": str(lower),
        "paired_risk_difference_method": "Bonferroni simultaneous one-sided Clopper-Pearson marginal bounds, alpha/2 per discordant cell",
        "premise_regressions": premise_regressions,
        "fully_settled": fully_settled,
        "positive_lafs": lafs_gain > 0,
        "internal_benchmark_success": internal,
        "external_sota": external_sota,
    }


PROOF_KEYS = {
    "schema_version",
    "binding_sha256",
    "authorization",
    "selection",
    "compiler",
    "provider",
    "cache",
    "ledger",
    "isolation",
    "pairing",
    "construction_proof_sha256",
}
PROOF_SECTION_KEYS = {
    "authorization": {"authorization_sha256", "campaign_sha256", "screen_id"},
    "selection": {"selection_sha256", "input_manifest_sha256", "state_mode"},
    "compiler": {
        "adapter_sha256",
        "construction_params_sha256",
        "prompt_sha256",
        "schema_sha256",
        "provider_code_sha256",
        "binaries",
    },
    "provider": {
        "requested_model",
        "served_model",
        "requested_provider",
        "served_provider",
        "input_price_nanos_per_million",
        "output_price_nanos_per_million",
        "maximum_output_tokens",
        "maximum_attempts",
    },
    "cache": {"namespace", "source_receipts_sha256"},
    "ledger": {
        "attempt_ids",
        "before_event_sha256",
        "after_event_sha256",
        "campaign_journal_sha256",
        "settled_nanos",
        "unresolved_nanos",
    },
}


def validate_construction_proof_v2(proof: object) -> dict[str, object]:
    if not isinstance(proof, dict) or set(proof) != PROOF_KEYS:
        raise RuntimeError("construction proof v2 shape is invalid")
    if proof.get("schema_version") != 2:
        raise RuntimeError("construction proof schema must be v2")
    core = {key: value for key, value in proof.items() if key != "construction_proof_sha256"}
    if proof.get("construction_proof_sha256") != sha256_json(core):
        raise RuntimeError("construction proof sha256 mismatch")
    for section, keys in PROOF_SECTION_KEYS.items():
        value = proof.get(section)
        if not isinstance(value, dict) or set(value) != keys:
            raise RuntimeError(f"construction proof {section} shape is invalid")
    hashes = [
        proof["binding_sha256"],
        proof["authorization"]["authorization_sha256"],
        proof["authorization"]["campaign_sha256"],
        proof["selection"]["selection_sha256"],
        proof["selection"]["input_manifest_sha256"],
        proof["compiler"]["adapter_sha256"],
        proof["compiler"]["construction_params_sha256"],
        proof["compiler"]["prompt_sha256"],
        proof["compiler"]["schema_sha256"],
        proof["compiler"]["provider_code_sha256"],
        proof["cache"]["source_receipts_sha256"],
        proof["ledger"]["before_event_sha256"],
        proof["ledger"]["after_event_sha256"],
        proof["ledger"]["campaign_journal_sha256"],
    ]
    if not all(_valid_sha256(value) for value in hashes):
        raise RuntimeError("construction proof contains an invalid sha256")
    binaries = proof["compiler"]["binaries"]
    if (
        not isinstance(binaries, dict)
        or set(binaries) != {"server", "cli", "worker"}
        or not all(
            isinstance(item, dict)
            and set(item) == {"path", "bytes", "sha256"}
            and isinstance(item["path"], str)
            and item["path"]
            and type(item["bytes"]) is int
            and item["bytes"] >= 0
            and _valid_sha256(item["sha256"])
            for item in binaries.values()
        )
    ):
        raise RuntimeError("construction proof binary identities are invalid")
    if not isinstance(proof["isolation"], dict) or not proof["isolation"] or not isinstance(proof["pairing"], dict) or not proof["pairing"]:
        raise RuntimeError("construction proof isolation or pairing is invalid")
    provider = proof["provider"]
    if not all(
        isinstance(provider[key], str) and provider[key]
        for key in ("requested_model", "served_model", "requested_provider", "served_provider")
    ) or not all(
        type(provider[key]) is int and provider[key] > 0
        for key in (
            "input_price_nanos_per_million",
            "output_price_nanos_per_million",
            "maximum_output_tokens",
            "maximum_attempts",
        )
    ):
        raise RuntimeError("construction proof provider identity or bounds are invalid")
    ledger = proof["ledger"]
    if (
        not isinstance(ledger["attempt_ids"], list)
        or len(set(ledger["attempt_ids"])) != len(ledger["attempt_ids"])
        or not all(isinstance(value, str) and value for value in ledger["attempt_ids"])
        or type(ledger["settled_nanos"]) is not int
        or ledger["settled_nanos"] < 0
        or type(ledger["unresolved_nanos"]) is not int
        or ledger["unresolved_nanos"] < 0
    ):
        raise RuntimeError("construction proof ledger binding is invalid")
    return proof


def _contains_oracle_key(value: object) -> bool:
    if isinstance(value, dict):
        return bool(ORACLE_KEYS.intersection(value)) or any(
            _contains_oracle_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_oracle_key(item) for item in value)
    return False


def validate_public_prefix_status(status: object) -> dict[str, object]:
    if not isinstance(status, dict) or _contains_oracle_key(status):
        raise RuntimeError("public prefix status contains oracle-bearing fields")
    if status.get("prefix_count") != 12 or not isinstance(status.get("rows"), list) or len(status["rows"]) != 12:
        raise RuntimeError("sealed operational prefix must contain 12 rows")
    if status.get("remaining_count") != 439:
        raise RuntimeError("sealed prefix requires the remaining 439-case commitment")
    if not all(
        _valid_sha256(status.get(key))
        for key in (
            "remaining_commitment_sha256",
            "execution_plan_sha256",
            "reservation_plan_sha256",
            "sealed_blob_sha256",
        )
    ):
        raise RuntimeError("sealed prefix commitments are invalid")
    for sequence, row in enumerate(status["rows"], 1):
        if not isinstance(row, dict) or row != {
            "sequence": sequence,
            "structurally_valid": True,
            "receipt_valid": True,
            "settled": True,
        }:
            raise RuntimeError("sealed prefix exposes invalid public row state")
    return status


def _derive_public_prefix_rows(
    private: object,
    *,
    execution_plan_sha256: str,
    reservation_plan_sha256: str,
) -> list[dict[str, object]]:
    if (
        not isinstance(private, dict)
        or private.get("schema_version") != 1
        or private.get("execution_plan_sha256") != execution_plan_sha256
        or private.get("reservation_plan_sha256") != reservation_plan_sha256
        or not isinstance(private.get("cases"), list)
        or len(private["cases"]) != 12
    ):
        raise RuntimeError("private prefix output authority is invalid")
    question_ids: set[str] = set()
    public_rows = []
    for sequence, case in enumerate(private["cases"], 1):
        rows = case.get("rows") if isinstance(case, dict) else None
        question_id = case.get("question_id") if isinstance(case, dict) else None
        if (
            not isinstance(case, dict)
            or case.get("sequence") != sequence
            or not isinstance(question_id, str)
            or not question_id
            or question_id in question_ids
            or not isinstance(rows, list)
            or len(rows) != 2
            or {row.get("arm") for row in rows if isinstance(row, dict)}
            != {"fast", "deep"}
        ):
            raise RuntimeError("private prefix output case structure is invalid")
        question_ids.add(question_id)
        for row in rows:
            arm = row.get("arm")
            required_fields = {
                "arm", "row_key", "answer", "output_sha256",
                "receipt_sha256", "structurally_valid", "receipt_valid",
                "settled", "official_row", "provider_record",
            }
            if (
                set(row)
                not in (required_fields, required_fields | {"deep_provider_record"})
                or row.get("row_key") != f"{question_id}:{arm}"
                or not isinstance(row.get("answer"), str)
                or not row["answer"]
                or not _valid_sha256(row.get("output_sha256"))
                or not _valid_sha256(row.get("receipt_sha256"))
                or row.get("structurally_valid") is not True
                or row.get("receipt_valid") is not True
                or row.get("settled") is not True
                or not isinstance(row.get("official_row"), dict)
                or row["official_row"].get("question_id") != question_id
                or not isinstance(row.get("provider_record"), dict)
                or (
                    arm == "deep"
                    and not isinstance(row.get("deep_provider_record"), dict)
                )
            ):
                raise RuntimeError("private prefix output row structure is invalid")
        public_rows.append(
            {
                "sequence": sequence,
                "structurally_valid": True,
                "receipt_valid": True,
                "settled": True,
            }
        )
    return public_rows


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def seal_prefix(
    private_results: Path,
    sealed_output: Path,
    public_status_path: Path,
    remaining_commitment_sha256: str,
    passphrase_env: str,
    *,
    execution_plan_sha256: str,
    reservation_plan_sha256: str,
    plaintext_paths: list[Path] | None = None,
) -> dict[str, object]:
    if not private_results.is_file():
        raise RuntimeError("private prefix result file is missing")
    if not _valid_sha256(remaining_commitment_sha256):
        raise RuntimeError("remaining commitment sha256 is invalid")
    if not all(
        _valid_sha256(value)
        for value in (execution_plan_sha256, reservation_plan_sha256)
    ):
        raise RuntimeError("sealed prefix execution authority is invalid")
    try:
        private = json.loads(private_results.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("private prefix results are invalid") from error
    # This directory is the canonical crash-recovery boundary for the sole
    # paid plaintext.  Tighten it before touching the file so a failed seal
    # never leaves evidence readable through a permissive inherited umask.
    private_results.parent.chmod(0o700)
    private_results.chmod(0o600)
    rows = _derive_public_prefix_rows(
        private,
        execution_plan_sha256=execution_plan_sha256,
        reservation_plan_sha256=reservation_plan_sha256,
    )
    passphrase = os.environ.get(passphrase_env, "")
    if not passphrase:
        raise RuntimeError(f"sealed prefix passphrase is unset: {passphrase_env}")
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl is required to seal prefix results")
    if public_status_path.exists() and not sealed_output.is_file():
        raise RuntimeError("sealed prefix status exists without ciphertext")
    sealed_output.parent.mkdir(parents=True, exist_ok=True)
    if not sealed_output.exists():
        with tempfile.NamedTemporaryFile(dir=sealed_output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        temporary.chmod(0o600)
        try:
            completed = subprocess.run(
                [
                    openssl,
                    "enc",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-iter",
                    "600000",
                    "-salt",
                    "-in",
                    str(private_results),
                    "-out",
                    str(temporary),
                    "-pass",
                    f"env:{passphrase_env}",
                ],
                capture_output=True,
                check=False,
                env={passphrase_env: passphrase},
            )
            if completed.returncode != 0:
                raise RuntimeError("openssl failed to seal prefix results")
            try:
                os.link(temporary, sealed_output)
            except FileExistsError as error:
                raise RuntimeError(
                    "immutable sealed prefix artifact already exists"
                ) from error
        finally:
            temporary.unlink(missing_ok=True)
    directory = os.open(sealed_output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    with tempfile.NamedTemporaryFile(
        dir=private_results.parent, delete=False
    ) as handle:
        verification = Path(handle.name)
    verification.chmod(0o600)
    try:
        completed = subprocess.run(
            [
                openssl,
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-iter",
                "600000",
                "-in",
                str(sealed_output),
                "-out",
                str(verification),
                "-pass",
                f"env:{passphrase_env}",
            ],
            capture_output=True,
            check=False,
            env={passphrase_env: passphrase},
        )
        if (
            completed.returncode != 0
            or _sha256_file(verification) != _sha256_file(private_results)
        ):
            # The plaintext is still present and fsynced, so an unverifiable
            # ciphertext (new or left by a crashed prior attempt) is not an
            # authority. Remove it to make the next invocation a clean retry.
            sealed_output.unlink(missing_ok=True)
            sealed_directory = os.open(sealed_output.parent, os.O_RDONLY)
            try:
                os.fsync(sealed_directory)
            finally:
                os.close(sealed_directory)
            raise RuntimeError("sealed prefix ciphertext verification failed")
    finally:
        verification.unlink(missing_ok=True)
    status = {
        "schema_version": 1,
        "prefix_count": 12,
        "remaining_count": 439,
        "remaining_commitment_sha256": remaining_commitment_sha256,
        "execution_plan_sha256": execution_plan_sha256,
        "reservation_plan_sha256": reservation_plan_sha256,
        "sealed_blob_sha256": _sha256_file(sealed_output),
        "rows": rows,
    }
    validate_public_prefix_status(status)
    if public_status_path.exists():
        try:
            existing_status = json.loads(public_status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("sealed prefix status is unreadable") from error
        if existing_status != status:
            raise RuntimeError("immutable sealed prefix status drift")
    else:
        _atomically_create_json(public_status_path, status)
    # Public status is the durable proof that the ciphertext was verified.
    # Only after it is fsynced may any paid plaintext copy be removed.
    private_root = private_results.parent.resolve()
    for plaintext_path in plaintext_paths or []:
        unresolved = plaintext_path.absolute()
        if (
            unresolved == private_results.absolute()
            or not unresolved.is_relative_to(private_root)
            or unresolved.is_symlink()
        ):
            raise RuntimeError("sealed prefix plaintext cleanup path is unsafe")
        if unresolved.is_dir():
            shutil.rmtree(unresolved)
        else:
            unresolved.unlink(missing_ok=True)
    cleanup_directory = os.open(private_root, os.O_RDONLY)
    try:
        os.fsync(cleanup_directory)
    finally:
        os.close(cleanup_directory)
    private_results.unlink()
    private_directory = os.open(private_results.parent, os.O_RDONLY)
    try:
        os.fsync(private_directory)
    finally:
        os.close(private_directory)
    return status


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


BINARY_PROVENANCE_FIELDS = {
    "binary_sha256",
    "cargo_lock_sha256",
    "source_set_sha256",
    "rustc_vv_sha256",
    "cargo_version_sha256",
    "build_profile",
    "cargo_locked",
    "package",
}


def _validate_census_binary_provenance(
    expected: dict[str, object], actual: dict[str, object]
) -> None:
    if (
        set(expected) != BINARY_PROVENANCE_FIELDS
        or set(actual) != BINARY_PROVENANCE_FIELDS
        or any(expected.get(key) != actual.get(key) for key in BINARY_PROVENANCE_FIELDS)
        or any(
            not isinstance(actual.get(key), str) or not SHA256.fullmatch(actual[key])
            for key in (
                "binary_sha256",
                "cargo_lock_sha256",
                "source_set_sha256",
                "rustc_vv_sha256",
                "cargo_version_sha256",
            )
        )
        or actual.get("build_profile") != "release"
        or actual.get("cargo_locked") is not True
        or actual.get("package") != "memphant-cli"
    ):
        raise RuntimeError("census binary provenance drift")


def _verify_selected_census_binary(fresh_sha256: str, selected_binary: Path) -> None:
    if (
        not SHA256.fullmatch(fresh_sha256)
        or not selected_binary.is_file()
        or _sha256_file(selected_binary) != fresh_sha256
    ):
        raise RuntimeError("selected census binary differs from the fresh locked build")


def _tool_output(command: list[str]) -> bytes:
    completed = subprocess.run(
        command, capture_output=True, check=False, env=os.environ.copy()
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError(f"build identity command failed: {command[0]}")
    return completed.stdout.strip()


def _current_build_provenance_inputs(
    construction: dict[str, object], build: dict[str, object]
) -> dict[str, object]:
    source_paths = build.get("source_paths")
    if (
        build.get("package") != "memphant-cli"
        or build.get("profile") != "release"
        or build.get("cargo_locked") is not True
        or not isinstance(source_paths, list)
        or not source_paths
        or not all(isinstance(path, str) and path for path in source_paths)
    ):
        raise RuntimeError("census binary build contract is incomplete")
    source_hashes = {}
    for relative in source_paths:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"census binary source is missing: {relative}")
        source_hashes[relative] = _sha256_file(path)
    declared_code = construction.get("code_sha256s")
    if not isinstance(declared_code, dict) or any(
        source_hashes.get(relative) != expected
        for relative, expected in declared_code.items()
    ):
        raise RuntimeError("census binary source identity drift")
    cargo = shutil.which("cargo")
    rustc = shutil.which("rustc")
    if cargo is None or rustc is None:
        raise RuntimeError("cargo and rustc are required for the census authority build")
    cargo_version = _tool_output([cargo, "--version"])
    rustc_vv = _tool_output([rustc, "-Vv"])
    actual = {
        "cargo_lock_sha256": _sha256_file(ROOT / "Cargo.lock"),
        "source_set_sha256": sha256_json(source_hashes),
        "rustc_vv_sha256": hashlib.sha256(rustc_vv).hexdigest(),
        "cargo_version_sha256": hashlib.sha256(cargo_version).hexdigest(),
    }
    for key, value in actual.items():
        if build.get(key) != value:
            raise RuntimeError(f"census binary build identity drift: {key}")
    return {**actual, "cargo": cargo}


def _build_census_binary(
    manifest: dict[str, object], selected_binary: Path | None = None
) -> tuple[Path, dict[str, object]]:
    construction = manifest.get("construction")
    if not isinstance(construction, dict):
        raise RuntimeError("census construction contract is missing")
    build = construction.get("census_binary_build")
    if not isinstance(build, dict):
        raise RuntimeError("census binary build contract is missing")
    identity = _current_build_provenance_inputs(construction, build)
    with tempfile.TemporaryDirectory(prefix="memphant-census-build-") as temporary:
        target_dir = Path(temporary) / "target"
        environment = os.environ.copy()
        environment.pop("RUSTFLAGS", None)
        environment.pop("CARGO_ENCODED_RUSTFLAGS", None)
        environment.pop("CARGO_TARGET_DIR", None)
        environment["CARGO_INCREMENTAL"] = "0"
        environment["SOURCE_DATE_EPOCH"] = "0"
        completed = subprocess.run(
            [
                identity["cargo"],
                "build",
                "--locked",
                "--release",
                "-p",
                "memphant-cli",
                "--target-dir",
                str(target_dir),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "fresh locked census binary build failed: " + completed.stderr.strip()
            )
        built = target_dir / "release" / (
            "memphant-cli.exe" if sys.platform == "win32" else "memphant-cli"
        )
        if not built.is_file():
            raise RuntimeError("fresh locked census build produced no memphant-cli binary")
        built_sha256 = _sha256_file(built)
        if selected_binary is not None:
            _verify_selected_census_binary(built_sha256, selected_binary)
        stable_dir = ROOT / "target/state-memory-census-bin" / built_sha256
        stable_dir.mkdir(parents=True, exist_ok=True)
        stable = stable_dir / built.name
        if stable.exists() and _sha256_file(stable) != built_sha256:
            raise RuntimeError("content-addressed census binary was substituted")
        if not stable.exists():
            with tempfile.NamedTemporaryFile(dir=stable_dir, delete=False) as handle:
                temporary_binary = Path(handle.name)
                with built.open("rb") as source:
                    shutil.copyfileobj(source, handle)
            temporary_binary.chmod(0o500)
            os.replace(temporary_binary, stable)
        if _sha256_file(stable) != built_sha256:
            raise RuntimeError("content-addressed census binary hash drift")
    provenance = {
        "binary_sha256": built_sha256,
        "cargo_lock_sha256": identity["cargo_lock_sha256"],
        "source_set_sha256": identity["source_set_sha256"],
        "rustc_vv_sha256": identity["rustc_vv_sha256"],
        "cargo_version_sha256": identity["cargo_version_sha256"],
        "build_profile": "release",
        "cargo_locked": True,
        "package": "memphant-cli",
    }
    return stable, provenance


def _acquire_file(
    url: str,
    destination: Path,
    expected_bytes: int | None,
    expected_sha256: str,
) -> None:
    if (
        destination.is_file()
        and (expected_bytes is None or destination.stat().st_size == expected_bytes)
        and _sha256_file(destination) == expected_sha256
    ):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MemPhant-state-aware-census-v1"})
            with urllib.request.urlopen(request) as response:
                shutil.copyfileobj(response, handle)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    if (
        (expected_bytes is not None and temporary.stat().st_size != expected_bytes)
        or _sha256_file(temporary) != expected_sha256
    ):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded artifact drift: {destination.name}")
    os.replace(temporary, destination)


def _load_adapter():
    import importlib.util
    import types

    package = types.ModuleType("memory_modules")
    module = types.ModuleType("memory_modules.memory")

    class Memory:
        def __init__(self, memory_params):
            self.memory_params = memory_params

    module.Memory = Memory
    module.MemoryContextItem = dict
    module.register_memory = lambda cls: cls
    sys.modules["memory_modules"] = package
    sys.modules["memory_modules.memory"] = module
    path = ROOT / "benchmarks/longmemeval_v2/memphant_memory.py"
    spec = importlib.util.spec_from_file_location("memphant_lme_v2_census_adapter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the pinned MemPhant adapter")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def _materialize_cli_input(data_root: Path, output: Path) -> dict[str, object]:
    haystack = json.loads((data_root / "haystacks/lme_v2_medium.json").read_text(encoding="utf-8"))
    if not isinstance(haystack, dict) or len(haystack) != QUESTION_COUNT:
        raise RuntimeError("official Medium haystack must contain all 451 question pairs")
    trajectory_uses: Counter[str] = Counter()
    for question_id, ids in haystack.items():
        if not isinstance(question_id, str) or not isinstance(ids, list) or not ids:
            raise RuntimeError("official Medium haystack is malformed")
        for trajectory_id in ids:
            if not isinstance(trajectory_id, str) or not trajectory_id:
                raise RuntimeError("official Medium trajectory identity is malformed")
            trajectory_uses[trajectory_id] += 1
    case_order = sorted(haystack)

    adapter = _load_adapter()
    found: set[str] = set()
    resource_rows = 0
    resource_uses = 0
    with (data_root / "trajectories.jsonl").open(encoding="utf-8") as source, output.open("w", encoding="utf-8") as target:
        for line_number, line in enumerate(source, 1):
            trajectory = json.loads(line)
            if not isinstance(trajectory, dict):
                raise RuntimeError(f"trajectory line {line_number} is not an object")
            trajectory_id = trajectory.get("id")
            uses = trajectory_uses.get(trajectory_id, 0)
            if not uses:
                continue
            if trajectory_id in found:
                raise RuntimeError("official trajectory ids are duplicated")
            found.add(trajectory_id)
            for row in adapter.census_resource_rows(trajectory, uses=uses):
                target.write(canonical_json(row).decode("utf-8") + "\n")
                resource_rows += 1
                resource_uses += uses
    missing = sorted(set(trajectory_uses) - found)
    if missing:
        raise RuntimeError(f"official trajectories are missing: {missing[:3]}")
    return {
        "question_pairs": len(haystack),
        "trajectory_uses": sum(trajectory_uses.values()),
        "unique_trajectories": len(trajectory_uses),
        "resource_rows": resource_rows,
        "resource_uses": resource_uses,
        "case_order_sha256": sha256_json(case_order),
        "sealed_prefix_ids_sha256": sha256_json(case_order[:12]),
        "remaining_ids_sha256": sha256_json(case_order[12:]),
        "input_jsonl_sha256": _sha256_file(output),
    }


def build_execution_plan(
    census: dict[str, object], data_root: Path
) -> dict[str, object]:
    """Build the oracle-free, census-bound paired row order.

    The haystack map is the sole case-identity authority used here.  In
    particular this function never opens the questions file, so neither query
    text nor reference answers can influence construction or run order.
    """
    enumeration = census.get("enumeration")
    if not isinstance(enumeration, dict):
        raise RuntimeError("campaign census enumeration is missing")
    try:
        haystack = json.loads(
            (data_root / "haystacks/lme_v2_medium.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("official Medium haystack is unavailable") from error
    if (
        not isinstance(haystack, dict)
        or len(haystack) != QUESTION_COUNT
        or any(
            not isinstance(question_id, str)
            or not question_id
            or not isinstance(trajectory_ids, list)
            or not trajectory_ids
            or any(
                not isinstance(trajectory_id, str) or not trajectory_id
                for trajectory_id in trajectory_ids
            )
            for question_id, trajectory_ids in haystack.items()
        )
    ):
        raise RuntimeError("official Medium haystack must contain 451 valid cases")
    case_order = sorted(haystack)
    expected = {
        "question_pairs": QUESTION_COUNT,
        "case_order_sha256": sha256_json(case_order),
        "sealed_prefix_ids_sha256": sha256_json(case_order[:12]),
        "remaining_ids_sha256": sha256_json(case_order[12:]),
    }
    if any(enumeration.get(key) != value for key, value in expected.items()):
        raise RuntimeError("execution inventory differs from census")
    rows = [
        {
            "sequence": sequence,
            "question_id": question_id,
            "arm": arm,
            "row_key": f"{question_id}:{arm}",
        }
        for sequence, (question_id, arm) in enumerate(
            (
                (question_id, arm)
                for question_id in case_order
                for arm in ("fast", "deep")
            ),
            1,
        )
    ]
    core = {
        "schema_version": 1,
        "benchmark": "LongMemEval-V2/medium",
        "case_count": QUESTION_COUNT,
        "row_count": QUESTION_COUNT * 2,
        "case_order_sha256": expected["case_order_sha256"],
        "prefix": {
            "count": 12,
            "ids_sha256": expected["sealed_prefix_ids_sha256"],
            "row_count": 24,
        },
        "remaining": {
            "count": QUESTION_COUNT - 12,
            "ids_sha256": expected["remaining_ids_sha256"],
            "row_count": (QUESTION_COUNT - 12) * 2,
        },
        "rows": rows,
        "rows_sha256": sha256_json(rows),
    }
    if _contains_oracle_key(core):
        raise RuntimeError("execution plan contains oracle-bearing fields")
    return {**core, "execution_plan_sha256": sha256_json(core)}


def build_row_reservation_plan(
    execution_plan: dict[str, object], census: dict[str, object]
) -> dict[str, object]:
    """Decompose the frozen census into exact append-before-call row bounds."""
    rows = execution_plan.get("rows")
    execution_core = {
        key: value
        for key, value in execution_plan.items()
        if key != "execution_plan_sha256"
    }
    if (
        execution_plan.get("execution_plan_sha256") != sha256_json(execution_core)
        or execution_plan.get("rows_sha256") != sha256_json(rows)
    ):
        raise RuntimeError("campaign execution plan identity drift")
    derivation = census.get("liability_derivation")
    terms = census.get("terms")
    reader_inventory = (
        derivation.get("reader_inventory") if isinstance(derivation, dict) else None
    )
    reader_rows = reader_inventory.get("rows") if isinstance(reader_inventory, dict) else None
    if (
        execution_plan.get("row_count") != QUESTION_COUNT * 2
        or not isinstance(rows, list)
        or len(rows) != QUESTION_COUNT * 2
        or not isinstance(reader_inventory, dict)
        or reader_inventory.get("row_count") != QUESTION_COUNT
        or not isinstance(reader_rows, list)
        or reader_inventory.get("inventory_sha256") != sha256_json(reader_rows)
        or not isinstance(terms, dict)
        or type(terms.get("R_sum")) is not int
        or type(terms.get("S")) is not int
        or terms["R_sum"] <= 0
        or terms["S"] <= 0
    ):
        raise RuntimeError("campaign row reservation authority is incomplete")
    by_question = {
        row.get("question_id"): row for row in reader_rows if isinstance(row, dict)
    }
    if len(by_question) != QUESTION_COUNT:
        raise RuntimeError("campaign reader reservation inventory is incomplete")
    planned_rows = []
    for expected_sequence, row in enumerate(rows, 1):
        if (
            not isinstance(row, dict)
            or row.get("sequence") != expected_sequence
            or row.get("arm") not in {"fast", "deep"}
            or row.get("question_id") not in by_question
            or not isinstance(row.get("row_key"), str)
            or not row["row_key"]
        ):
            raise RuntimeError("campaign paired row order is malformed")
        authority = by_question[row["question_id"]]
        reader_nanos = authority.get("reader_liability_nanos")
        judge_nanos = authority.get("judge_liability_nanos")
        native_judge_required = authority.get("native_judge_required")
        if (
            type(reader_nanos) is not int
            or reader_nanos <= 0
            or type(judge_nanos) is not int
            or judge_nanos < 0
            or type(native_judge_required) is not bool
            or (judge_nanos > 0) != native_judge_required
            or authority.get("per_arm_liability_nanos")
            != reader_nanos + judge_nanos
        ):
            raise RuntimeError("campaign row liability decomposition drift")
        components = {"reader": reader_nanos}
        if native_judge_required:
            components["judge"] = judge_nanos
        if row["arm"] == "deep":
            components = {"deep_recall": terms["S"], **components}
        planned_rows.append(
            {
                "sequence": expected_sequence,
                "row_key": row["row_key"],
                "question_id": row["question_id"],
                "arm": row["arm"],
                "native_judge_required": native_judge_required,
                "components": components,
                "maximum_liability_nanos": sum(components.values()),
            }
        )
    expected_total = 2 * terms["R_sum"] + QUESTION_COUNT * terms["S"]
    if sum(row["maximum_liability_nanos"] for row in planned_rows) != expected_total:
        raise RuntimeError("campaign row liabilities do not reproduce census terms")
    core = {
        "schema_version": 1,
        "execution_plan_sha256": execution_plan["execution_plan_sha256"],
        "census_sha256": census["census_sha256"],
        "row_count": len(planned_rows),
        "rows": planned_rows,
        "rows_sha256": sha256_json(planned_rows),
        "total_liability_nanos": expected_total,
    }
    return {**core, "reservation_plan_sha256": sha256_json(core)}


class RowExecutionStateMachine:
    """Resolve every paid row component from one frozen reservation plan.

    The provider-attempt journal remains the sole spend authority. This wrapper
    reconstructs row state from that journal on every transition, so a restart
    cannot duplicate a completed or unresolved provider request.
    """

    REQUEST_PREFIX = "lme-v2-row"

    def __init__(
        self,
        reservation_plan: dict[str, object],
        ledger: object,
        *,
        admitted_case_count: int,
    ) -> None:
        core = {
            key: value
            for key, value in reservation_plan.items()
            if key != "reservation_plan_sha256"
        }
        rows = reservation_plan.get("rows")
        if (
            reservation_plan.get("reservation_plan_sha256") != sha256_json(core)
            or reservation_plan.get("schema_version") != 1
            or reservation_plan.get("row_count") != QUESTION_COUNT * 2
            or not isinstance(rows, list)
            or len(rows) != QUESTION_COUNT * 2
            or reservation_plan.get("rows_sha256") != sha256_json(rows)
            or admitted_case_count not in {12, QUESTION_COUNT}
        ):
            raise RuntimeError("campaign row reservation plan identity drift")
        by_key: dict[str, dict[str, object]] = {}
        total = 0
        for sequence, row in enumerate(rows, 1):
            components = row.get("components") if isinstance(row, dict) else None
            native_judge_required = (
                row.get("native_judge_required") if isinstance(row, dict) else None
            )
            expected_components = {"reader"}
            if native_judge_required is True:
                expected_components.add("judge")
            if isinstance(row, dict) and row.get("arm") == "deep":
                expected_components.add("deep_recall")
            row_key = row.get("row_key") if isinstance(row, dict) else None
            if (
                not isinstance(row, dict)
                or row.get("sequence") != sequence
                or row.get("arm") not in {"fast", "deep"}
                or type(native_judge_required) is not bool
                or not isinstance(row.get("question_id"), str)
                or not row["question_id"]
                or not isinstance(row_key, str)
                or not row_key
                or row_key in by_key
                or not isinstance(components, dict)
                or set(components) != expected_components
                or any(type(value) is not int or value <= 0 for value in components.values())
                or row.get("maximum_liability_nanos") != sum(components.values())
            ):
                raise RuntimeError("campaign row reservation inventory is malformed")
            by_key[row_key] = row
            total += row["maximum_liability_nanos"]
        if total != reservation_plan.get("total_liability_nanos"):
            raise RuntimeError("campaign row reservation total drift")
        self.reservation_plan = reservation_plan
        self.ledger = ledger
        self.admitted_case_count = admitted_case_count
        self._rows = by_key

    def _component(self, row_key: str, component: str) -> tuple[dict[str, object], int, str]:
        row = self._rows.get(row_key)
        components = row.get("components") if isinstance(row, dict) else None
        if row is None or not isinstance(components, dict) or component not in components:
            raise RuntimeError("row component is outside the frozen reservation plan")
        case_index = (row["sequence"] - 1) // 2
        if case_index >= self.admitted_case_count:
            raise RuntimeError("row component is outside the admitted case prefix")
        request_key = (
            f"{self.REQUEST_PREFIX}:{row['sequence']}:{row_key}:{component}"
        )
        return row, components[component], request_key

    def _attempt(self, request_key: str) -> dict[str, object] | None:
        snapshot = self.ledger.snapshot()
        attempts = snapshot.get("attempts") if isinstance(snapshot, dict) else None
        if not isinstance(attempts, list):
            raise RuntimeError("campaign provider ledger snapshot is malformed")
        matches = [
            attempt
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("request_key") == request_key
        ]
        if len(matches) > 1:
            raise RuntimeError("row component has duplicate provider attempts")
        return matches[0] if matches else None

    def component_status(self, row_key: str, component: str) -> str:
        _, _, request_key = self._component(row_key, component)
        attempt = self._attempt(request_key)
        return "pending" if attempt is None else str(attempt.get("status"))

    def _require_prerequisites(self, row: dict[str, object], component: str) -> None:
        row_key = str(row["row_key"])
        if component == "reader" and row["arm"] == "deep":
            if self.component_status(row_key, "deep_recall") != "result":
                raise RuntimeError("deep reader requires settled deep_recall")
        if component == "judge":
            if self.admitted_case_count != QUESTION_COUNT:
                raise RuntimeError("native judge requires the committed full census")
            incomplete = [
                candidate["row_key"]
                for candidate in self._rows.values()
                if self.component_status(str(candidate["row_key"]), "reader") != "result"
            ]
            if incomplete:
                raise RuntimeError("native judge requires all 902 settled reader rows")

    def start(
        self,
        row_key: str,
        component: str,
        *,
        requested_model: str,
        request_sha256: str,
    ) -> str:
        row, liability_nanos, request_key = self._component(row_key, component)
        expected_model = (
            "gpt-5.2-2025-12-11"
            if component == "judge"
            else "qwen/qwen3.5-9b-20260310"
        )
        if requested_model != expected_model or not _valid_sha256(request_sha256):
            raise RuntimeError("row component request identity drift")
        attempt = self._attempt(request_key)
        if attempt is not None:
            suffix = (
                "an unresolved attempt"
                if attempt.get("status") == "started"
                else "a terminal attempt"
            )
            raise RuntimeError(f"row component already has {suffix}")
        self._require_prerequisites(row, component)
        self.ledger.record(
            "start",
            request_key,
            {
                "max_liability_nanos": liability_nanos,
                "retry_index": 0,
                "requested_model": requested_model,
                "request_sha256": request_sha256,
            },
        )
        return request_key

    def result(
        self,
        row_key: str,
        component: str,
        response: dict[str, object],
    ) -> None:
        _, liability_nanos, request_key = self._component(row_key, component)
        attempt = self._attempt(request_key)
        if attempt is None or attempt.get("status") != "started":
            raise RuntimeError("row result has no exact durable reservation")
        usage = response.get("usage") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("response_id"), str)
            or not response["response_id"]
            or not isinstance(usage, dict)
            or type(usage.get("prompt_tokens")) is not int
            or type(usage.get("completion_tokens")) is not int
            or type(usage.get("total_tokens")) is not int
            or usage["prompt_tokens"] <= 0
            or usage["completion_tokens"] <= 0
            or usage["total_tokens"]
            != usage["prompt_tokens"] + usage["completion_tokens"]
            or _cost_nanos_from_reported_usage(usage) > liability_nanos
            or response.get("retry_index") != 0
            or not _valid_sha256(response.get("request_sha256"))
            or not _valid_sha256(response.get("result_sha256"))
            or response.get("parse_status") != "provider_response_validated"
            or response.get("request_sha256")
            != attempt.get("start", {}).get("request_sha256")
            or response.get("requested_model")
            != attempt.get("start", {}).get("requested_model")
        ):
            raise RuntimeError("row result is unpriced or exceeds its reservation")
        self.ledger.record("result", request_key, {"response": response})

    def error(self, row_key: str, component: str, error_type: str, route: str) -> None:
        _, _, request_key = self._component(row_key, component)
        attempt = self._attempt(request_key)
        if attempt is None or attempt.get("status") != "started":
            raise RuntimeError("row error has no exact durable reservation")
        self.ledger.record(
            "error", request_key, {"error_type": error_type, "route": route}
        )


def build_remaining_commitment(
    execution_plan: dict[str, object],
    reservation_plan: dict[str, object],
) -> dict[str, object]:
    """Bind the exact 439-case tail before any sealed output can be inspected."""
    execution_core = {
        key: value
        for key, value in execution_plan.items()
        if key != "execution_plan_sha256"
    }
    reservation_core = {
        key: value
        for key, value in reservation_plan.items()
        if key != "reservation_plan_sha256"
    }
    execution_rows = execution_plan.get("rows")
    reservation_rows = reservation_plan.get("rows")
    if (
        execution_plan.get("execution_plan_sha256") != sha256_json(execution_core)
        or execution_plan.get("rows_sha256") != sha256_json(execution_rows)
        or reservation_plan.get("reservation_plan_sha256")
        != sha256_json(reservation_core)
        or reservation_plan.get("execution_plan_sha256")
        != execution_plan.get("execution_plan_sha256")
        or reservation_plan.get("rows_sha256") != sha256_json(reservation_rows)
        or not isinstance(execution_rows, list)
        or not isinstance(reservation_rows, list)
        or len(execution_rows) != QUESTION_COUNT * 2
        or len(reservation_rows) != QUESTION_COUNT * 2
        or execution_plan.get("row_count") != QUESTION_COUNT * 2
        or reservation_plan.get("row_count") != QUESTION_COUNT * 2
    ):
        raise RuntimeError("remaining commitment execution authority drift")
    for execution_row, reservation_row in zip(execution_rows, reservation_rows):
        if any(
            execution_row.get(key) != reservation_row.get(key)
            for key in ("sequence", "question_id", "arm", "row_key")
        ):
            raise RuntimeError("remaining commitment row authority drift")
    remaining_rows = reservation_rows[24:]
    remaining_keys = [row["row_key"] for row in remaining_rows]
    core = {
        "schema_version": 1,
        "status": "IRREVOCABLY_COMMITTED_439",
        "execution_plan_sha256": execution_plan["execution_plan_sha256"],
        "reservation_plan_sha256": reservation_plan["reservation_plan_sha256"],
        "prefix_count": 12,
        "prefix_row_count": 24,
        "remaining_count": 439,
        "remaining_row_count": 878,
        "remaining_rows_sha256": sha256_json(remaining_rows),
        "remaining_row_keys_sha256": sha256_json(remaining_keys),
    }
    if _contains_oracle_key(core):
        raise RuntimeError("remaining commitment contains oracle-bearing fields")
    return {**core, "remaining_commitment_sha256": sha256_json(core)}


def _validate_remaining_commitment(
    commitment: dict[str, object], reservation_plan: dict[str, object]
) -> None:
    core = {
        key: value
        for key, value in commitment.items()
        if key != "remaining_commitment_sha256"
    }
    rows = reservation_plan.get("rows")
    remaining_rows = rows[24:] if isinstance(rows, list) else None
    remaining_keys = (
        [row.get("row_key") for row in remaining_rows]
        if isinstance(remaining_rows, list)
        else None
    )
    if (
        _contains_oracle_key(commitment)
        or commitment.get("remaining_commitment_sha256") != sha256_json(core)
        or commitment.get("status") != "IRREVOCABLY_COMMITTED_439"
        or commitment.get("reservation_plan_sha256")
        != reservation_plan.get("reservation_plan_sha256")
        or commitment.get("execution_plan_sha256")
        != reservation_plan.get("execution_plan_sha256")
        or commitment.get("prefix_count") != 12
        or commitment.get("prefix_row_count") != 24
        or commitment.get("remaining_count") != 439
        or commitment.get("remaining_row_count") != 878
        or commitment.get("remaining_rows_sha256") != sha256_json(remaining_rows)
        or commitment.get("remaining_row_keys_sha256") != sha256_json(remaining_keys)
    ):
        raise RuntimeError("remaining commitment identity drift")


def remaining_resume_actions(
    commitment: dict[str, object],
    reservation_plan: dict[str, object],
    ledger: object,
) -> list[dict[str, object]]:
    """Return only the next safe deterministic action for each tail row."""
    _validate_remaining_commitment(commitment, reservation_plan)
    machine = RowExecutionStateMachine(
        reservation_plan, ledger, admitted_case_count=QUESTION_COUNT
    )
    rows = reservation_plan["rows"]
    for row in rows[:24]:
        required = (
            ("reader",)
            if row["arm"] == "fast"
            else ("deep_recall", "reader")
        )
        if any(
            machine.component_status(row["row_key"], component) != "result"
            for component in required
        ):
            raise RuntimeError("sealed prefix is not fully settled")
    actions = []
    for row in rows[24:]:
        if row["arm"] == "deep":
            deep_status = machine.component_status(row["row_key"], "deep_recall")
            if deep_status in {"started", "error"}:
                raise RuntimeError("remaining Deep row has unresolved terminal state")
            component = "deep_recall" if deep_status == "pending" else "reader"
        else:
            component = "reader"
        status = machine.component_status(row["row_key"], component)
        if status in {"started", "error"}:
            raise RuntimeError("remaining reader row has unresolved terminal state")
        if status == "result":
            continue
        _, _, request_key = machine._component(row["row_key"], component)
        actions.append(
            {
                "sequence": row["sequence"],
                "row_key": row["row_key"],
                "component": component,
                "request_key": request_key,
            }
        )
    return actions


def validate_complete_row_settlement(
    reservation_plan: dict[str, object], ledger_snapshot: dict[str, object]
) -> dict[str, object]:
    """Prove exact, priced completion for every recall/reader/judge component."""
    plan_core = {
        key: value
        for key, value in reservation_plan.items()
        if key != "reservation_plan_sha256"
    }
    rows = reservation_plan.get("rows")
    attempts = ledger_snapshot.get("attempts")
    if (
        reservation_plan.get("reservation_plan_sha256") != sha256_json(plan_core)
        or not isinstance(rows, list)
        or len(rows) != QUESTION_COUNT * 2
        or not isinstance(attempts, list)
    ):
        raise RuntimeError("complete row settlement authority drift")
    row_attempts = [
        attempt
        for attempt in attempts
        if isinstance(attempt, dict)
        and isinstance(attempt.get("request_key"), str)
        and attempt["request_key"].startswith(
            RowExecutionStateMachine.REQUEST_PREFIX + ":"
        )
    ]
    expected: dict[str, tuple[dict[str, object], str]] = {}
    for row in rows:
        components = ["reader"]
        if "judge" in row["components"]:
            components.append("judge")
        if row["arm"] == "deep":
            components.insert(0, "deep_recall")
        for component in components:
            key = f"lme-v2-row:{row['sequence']}:{row['row_key']}:{component}"
            expected[key] = (row, component)
    expected_attempt_count = QUESTION_COUNT * 3 + 156 * 2
    if len(expected) != expected_attempt_count or len(row_attempts) != expected_attempt_count:
        raise RuntimeError(
            f"complete row settlement requires exactly {expected_attempt_count} attempts"
        )
    found: set[str] = set()
    response_ids: set[str] = set()
    settled_nanos = 0
    latest_prejudge_result_sequence = -1
    earliest_judge_start_sequence = sys.maxsize
    for attempt in attempts:
        request_key = attempt.get("request_key") if isinstance(attempt, dict) else None
        authority = expected.get(request_key)
        if authority is None:
            continue
        if request_key in found:
            raise RuntimeError("complete row settlement duplicates one component")
        found.add(request_key)
        row, component = authority
        start = attempt.get("start")
        result = attempt.get("result")
        response = result.get("response") if isinstance(result, dict) else None
        usage = response.get("usage") if isinstance(response, dict) else None
        requested_model = (
            "gpt-5.2-2025-12-11"
            if component == "judge"
            else "qwen/qwen3.5-9b-20260310"
        )
        response_id = response.get("response_id") if isinstance(response, dict) else None
        start_sequence = attempt.get("start_sequence")
        result_sequence = attempt.get("result_sequence")
        if (
            attempt.get("status") != "result"
            or type(start_sequence) is not int
            or type(result_sequence) is not int
            or not 0 < start_sequence < result_sequence
            or not isinstance(start, dict)
            or start.get("max_liability_nanos")
            != row["components"][component]
            or start.get("retry_index") != 0
            or start.get("requested_model") != requested_model
            or not _valid_sha256(start.get("request_sha256"))
            or not isinstance(response, dict)
            or response.get("requested_model") != requested_model
            or response.get("request_sha256") != start.get("request_sha256")
            or response.get("retry_index") != 0
            or response.get("parse_status") != "provider_response_validated"
            or not _valid_sha256(response.get("result_sha256"))
            or not isinstance(response_id, str)
            or not response_id
            or response_id in response_ids
            or not isinstance(usage, dict)
            or type(usage.get("prompt_tokens")) is not int
            or type(usage.get("completion_tokens")) is not int
            or type(usage.get("total_tokens")) is not int
            or usage["prompt_tokens"] <= 0
            or usage["completion_tokens"] <= 0
            or usage["total_tokens"]
            != usage["prompt_tokens"] + usage["completion_tokens"]
        ):
            raise RuntimeError("complete row settlement contains invalid attempt proof")
        cost = _cost_nanos_from_reported_usage(usage)
        if cost > row["components"][component]:
            raise RuntimeError("complete row settlement exceeds a frozen reservation")
        settled_nanos += cost
        response_ids.add(response_id)
        if component == "judge":
            earliest_judge_start_sequence = min(
                earliest_judge_start_sequence, start_sequence
            )
        else:
            latest_prejudge_result_sequence = max(
                latest_prejudge_result_sequence, result_sequence
            )
    if found != set(expected):
        raise RuntimeError("complete row settlement lacks exact component coverage")
    if latest_prejudge_result_sequence >= earliest_judge_start_sequence:
        raise RuntimeError("native judge started before all reader outputs settled")
    core = {
        "schema_version": 1,
        "reservation_plan_sha256": reservation_plan["reservation_plan_sha256"],
        "row_attempt_count": len(row_attempts),
        "settled_nanos": settled_nanos,
        "unresolved_nanos": 0,
        "row_attempts_sha256": sha256_json(row_attempts),
        "response_ids_sha256": sha256_json(sorted(response_ids)),
        "all_readers_before_native_judge": True,
        "exact_component_coverage": True,
    }
    return {**core, "row_settlement_sha256": sha256_json(core)}


def _checked_private_provider_record(
    record: object,
    *,
    row: dict[str, object],
    component: str,
    attempts_by_key: dict[str, dict[str, object]],
    pinned: dict[str, str],
    reservation_plan: dict[str, object],
) -> dict[str, object]:
    if not isinstance(record, dict):
        raise RuntimeError("official derivation lacks a private provider record")
    core = {key: value for key, value in record.items() if key != "private_output_sha256"}
    authority = record.get("authority")
    receipt = record.get("receipt")
    response = record.get("response")
    request_key = f"lme-v2-row:{row['sequence']}:{row['row_key']}:{component}"
    attempt = attempts_by_key.get(request_key)
    attempt_receipt = (
        attempt.get("result", {}).get("response") if isinstance(attempt, dict) else None
    )
    if (
        record.get("private_output_sha256") != sha256_json(core)
        or record.get("status") != "PRIVATE_OUTPUT_FSYNCED"
        or record.get("row_key") != row["row_key"]
        or record.get("component") != component
        or not isinstance(authority, dict)
        or set(authority) != PRIVATE_OUTPUT_AUTHORITY_FIELDS
        or not all(_valid_sha256(authority.get(key)) for key in authority)
        or authority.get("execution_plan_sha256")
        != reservation_plan["execution_plan_sha256"]
        or authority.get("reservation_plan_sha256")
        != reservation_plan["reservation_plan_sha256"]
        or authority.get("official_harness_sha256") != pinned["official_harness_sha256"]
        or authority.get("official_scorer_sha256") != pinned["official_scorer_sha256"]
        or not isinstance(response, dict)
        or not isinstance(receipt, dict)
        or receipt.get("result_sha256") != sha256_json(response)
        or receipt != attempt_receipt
    ):
        raise RuntimeError("private provider checkpoint differs from campaign authority")
    return record


def derive_native_official_artifact(
    *,
    reservation_plan: dict[str, object],
    ledger_snapshot: dict[str, object],
    private_rows: dict[str, dict[str, object]],
    judge_root: Path,
    official_dir: Path,
    runtime_code: dict[str, object],
    output_path: Path | None = None,
) -> dict[str, object]:
    """Derive the sole package authority from immutable private checkpoints."""
    row_settlement = validate_complete_row_settlement(reservation_plan, ledger_snapshot)
    release_lock = json.loads(
        (ROOT / "benchmarks/manifests/longmemeval_v2.lock.json").read_text(encoding="utf-8")
    )
    pinned = {
        "code_commit": release_lock["code"]["commit"],
        "dataset_revision": release_lock["dataset"]["revision"],
        "official_harness_sha256": release_lock["code"]["files"]["evaluation/harness.py"],
        "official_scorer_sha256": release_lock["code"]["files"]["evaluation/qa_eval_metrics.py"],
        "compute_lafs_sha256": release_lock["code"]["files"]["leaderboard/compute_lafs.py"],
    }
    runtime_core = {
        key: value for key, value in runtime_code.items() if key != "proof_sha256"
    }
    if (
        runtime_code.get("commit") != pinned["code_commit"]
        or runtime_code.get("proof_sha256") != sha256_json(runtime_core)
        or runtime_code.get("release_lock_sha256")
        != _sha256_file(ROOT / "benchmarks/manifests/longmemeval_v2.lock.json")
        or runtime_code.get("files") != release_lock["code"]["files"]
        or runtime_code.get("files_sha256") != sha256_json(release_lock["code"]["files"])
        or _sha256_file(official_dir / "evaluation/harness.py")
        != pinned["official_harness_sha256"]
        or _sha256_file(official_dir / "evaluation/qa_eval_metrics.py")
        != pinned["official_scorer_sha256"]
        or _sha256_file(official_dir / "leaderboard/compute_lafs.py")
        != pinned["compute_lafs_sha256"]
    ):
        raise RuntimeError("pinned official derivation code identity drift")
    attempts_by_key = {
        attempt["request_key"]: attempt
        for attempt in ledger_snapshot["attempts"]
        if isinstance(attempt, dict) and isinstance(attempt.get("request_key"), str)
    }
    harness = _load_official_harness(
        official_dir,
        {
            "official_harness_sha256": pinned["official_harness_sha256"],
            "official_scorer_sha256": pinned["official_scorer_sha256"],
        },
    )
    sources = []
    scored = []
    allowed_categories = {
        "static", "dynamic", "procedure", "static-abs", "dynamic-abs",
        "procedure-abs", "gotchas",
    }
    for row in reservation_plan["rows"]:
        private = private_rows.get(row["row_key"])
        official = private.get("official_row") if isinstance(private, dict) else None
        if (
            not isinstance(private, dict)
            or private.get("row_key") != row["row_key"]
            or private.get("arm") != row["arm"]
            or not isinstance(official, dict)
            or official.get("question_id") != row["question_id"]
        ):
            raise RuntimeError("official reader checkpoint identity drift")
        reader_record = _checked_private_provider_record(
            private.get("provider_record"), row=row, component="reader",
            attempts_by_key=attempts_by_key, pinned=pinned,
            reservation_plan=reservation_plan,
        )
        reader_response = reader_record.get("response", {}).get("response", {})
        reader_choices = (
            reader_response.get("choices")
            if isinstance(reader_response, dict)
            else None
        )
        reader_answer = (
            reader_choices[0].get("message", {}).get("content")
            if isinstance(reader_choices, list)
            and len(reader_choices) == 1
            and isinstance(reader_choices[0], dict)
            else None
        )
        if (
            not isinstance(reader_answer, str)
            or not reader_answer
            or official.get("response_raw") != reader_answer
        ):
            raise RuntimeError(
                "official reader output differs from its durable provider response"
            )
        deep_record = None
        if row["arm"] == "deep":
            deep_record = _checked_private_provider_record(
                private.get("deep_provider_record"), row=row, component="deep_recall",
                attempts_by_key=attempts_by_key, pinned=pinned,
                reservation_plan=reservation_plan,
            )
        score_path = judge_root.resolve() / f"{int(row['sequence']):04d}" / "SCORE.private.json"
        score = json.loads(score_path.read_text(encoding="utf-8"))
        score_core = {key: value for key, value in score.items() if key != "score_sha256"}
        if (
            score.get("score_sha256") != sha256_json(score_core)
            or score.get("row_key") != row["row_key"]
            or score.get("official_row_sha256") != sha256_json(official)
            or type(score.get("score_bool")) is not bool
            or score.get("score") != int(score["score_bool"])
            or type(score.get("is_unknown")) is not bool
            or not isinstance(score.get("eval_name"), str)
            or not score["eval_name"]
            or score.get("native_judge_required") is not row["native_judge_required"]
            or score.get("native_judge_settled") is not True
        ):
            raise RuntimeError("official score checkpoint differs from its private row")
        judge_record = None
        evaluator_identity = {
            "kind": "pinned_deterministic",
            "eval_name": official.get("eval_name"),
            "eval_function_sha256": sha256_json(official.get("eval_function")),
            "official_harness_sha256": pinned["official_harness_sha256"],
            "official_scorer_sha256": pinned["official_scorer_sha256"],
        }
        eval_config = {}
        replay_proxy = None
        if row["native_judge_required"]:
            judge_record = _checked_private_provider_record(
                json.loads(
                    (score_path.parent / "judge-provider.private.json").read_text(
                        encoding="utf-8"
                    )
                ),
                row=row, component="judge", attempts_by_key=attempts_by_key,
                pinned=pinned, reservation_plan=reservation_plan,
            )
            judge_receipt = judge_record["receipt"]
            judge_response = judge_record["response"]
            expected_request_sha256 = judge_receipt.get("request_sha256")

            def replay_judge(payload):
                if sha256_json(payload) != expected_request_sha256:
                    raise RuntimeError(
                        "official derivation judge replay request identity drift"
                    )
                return judge_response

            replay_proxy, evaluator_base_url = _start_metered_proxy(replay_judge)
            eval_config = _native_judge_eval_config(evaluator_base_url)
            evaluator_contract = {
                key: eval_config[key]
                for key in (
                    "evaluator_model",
                    "evaluator_reasoning_effort",
                    "evaluator_max_completion_tokens",
                    "evaluator_timeout_seconds",
                )
            }
            evaluator_identity = {
                "kind": "pinned_native_judge_replay",
                "eval_name": official.get("eval_name"),
                "eval_function_sha256": sha256_json(official.get("eval_function")),
                "official_harness_sha256": pinned["official_harness_sha256"],
                "official_scorer_sha256": pinned["official_scorer_sha256"],
                "requested_model": judge_receipt.get("requested_model"),
                "served_model": judge_receipt.get("served_model"),
                "provider": judge_receipt.get("provider"),
                "response_id": judge_receipt.get("response_id"),
                "request_sha256": expected_request_sha256,
                "result_sha256": judge_receipt.get("result_sha256"),
                "evaluator_contract": evaluator_contract,
                "evaluator_contract_sha256": sha256_json(evaluator_contract),
            }
        try:
            recomputed_bool, recomputed_eval_name, recomputed_unknown = (
                harness.score_prediction(official, eval_config)
            )
        finally:
            if replay_proxy is not None:
                replay_proxy.shutdown()
                replay_proxy.server_close()
        if (
            type(recomputed_bool) is not bool
            or type(recomputed_unknown) is not bool
            or not isinstance(recomputed_eval_name, str)
            or not recomputed_eval_name
            or recomputed_eval_name != official.get("eval_name")
        ):
            raise RuntimeError("pinned official scorer returned an invalid score tuple")
        recomputed = {
            "score_bool": recomputed_bool,
            "score": int(recomputed_bool),
            "eval_name": recomputed_eval_name,
            "is_unknown": recomputed_unknown,
        }
        if (
            replay_proxy is not None
            and (
                replay_proxy.dispatch_count != 1
                or replay_proxy._request_sha256
                != evaluator_identity["request_sha256"]
                or sha256_json(replay_proxy._response)
                != evaluator_identity["result_sha256"]
            )
        ):
            raise RuntimeError("official derivation judge replay did not settle exactly once")
        if any(score.get(key) != value for key, value in recomputed.items()):
            raise RuntimeError(
                "official score checkpoint differs from pinned scorer recomputation"
            )
        category = official.get("category")
        is_abstention = official.get("is_abstention_problem")
        if (
            category not in allowed_categories
            or type(is_abstention) is not bool
            or is_abstention != str(category).endswith("-abs")
        ):
            raise RuntimeError("official premise-awareness category drift")
        scored_official = dict(official)
        scored_official.update(
            score=recomputed["score"], score_bool=recomputed["score_bool"],
            is_unknown=recomputed["is_unknown"],
        )
        authoritative_score = {
            **score,
            **recomputed,
            "recomputed_score_sha256": sha256_json(recomputed),
        }
        scored.append(
            {
                "planned": row,
                "score": authoritative_score,
                "official": scored_official,
            }
        )
        sources.append(
            {
                "sequence": row["sequence"],
                "row_key": row["row_key"],
                "official_row_sha256": sha256_json(official),
                "reader_private_sha256": reader_record["private_output_sha256"],
                "deep_private_sha256": (
                    deep_record["private_output_sha256"] if deep_record else None
                ),
                "score_sha256": score["score_sha256"],
                "recomputed_score": recomputed,
                "recomputed_score_sha256": sha256_json(recomputed),
                "evaluator_identity": evaluator_identity,
                "evaluator_identity_sha256": sha256_json(evaluator_identity),
                "judge_private_sha256": (
                    judge_record["private_output_sha256"] if judge_record else None
                ),
            }
        )
    arm_metrics = _aggregate_scored_official_rows(scored, harness)
    lafs = official_lafs_summary(official_dir, arm_metrics)
    by_question = {}
    for item in scored:
        by_question.setdefault(item["planned"]["question_id"], {})[
            item["planned"]["arm"]
        ] = item
    pairs = []
    for row in reservation_plan["rows"][::2]:
        pair = by_question.get(row["question_id"], {})
        if set(pair) != {"fast", "deep"}:
            raise RuntimeError("official derivation row pairing is incomplete")
        official = pair["fast"]["official"]
        pairs.append(
            {
                "question_id": row["question_id"],
                "ability": (
                    "premise_awareness"
                    if official["is_abstention_problem"]
                    else official["category"]
                ),
                "fast_correct": pair["fast"]["score"]["score_bool"],
                "deep_correct": pair["deep"]["score"]["score_bool"],
                "native_judge_valid": all(
                    item["score"]["native_judge_settled"] for item in pair.values()
                ),
                "settled": True,
                "receipt_sha256": sha256_json(
                    [
                        private_rows[pair[arm]["planned"]["row_key"]][
                            "provider_record"
                        ]
                        for arm in ("fast", "deep")
                    ]
                ),
            }
        )
    core = {
        "schema_version": 1,
        "execution_plan_sha256": reservation_plan["execution_plan_sha256"],
        "reservation_plan_sha256": reservation_plan["reservation_plan_sha256"],
        "row_settlement": row_settlement,
        "runtime_code_proof_sha256": runtime_code["proof_sha256"],
        "pinned": pinned,
        "sources": sources,
        "sources_sha256": sha256_json(sources),
        "pairs": pairs,
        "pairs_sha256": sha256_json(pairs),
        "arms": arm_metrics,
        "arms_sha256": sha256_json(arm_metrics),
        "lafs": lafs,
    }
    artifact = {**core, "derivation_sha256": sha256_json(core)}
    if output_path is not None:
        _create_or_validate_json(output_path, artifact)
    return artifact


def official_metrics_from_derivation(derivation: dict[str, object]) -> dict[str, object]:
    core = {
        "schema_version": 1,
        "derivation_sha256": derivation["derivation_sha256"],
        "runtime_code_proof_sha256": derivation["runtime_code_proof_sha256"],
        "runtime_code_commit": derivation["pinned"]["code_commit"],
        "arms": derivation["arms"],
        "lafs": derivation["lafs"],
    }
    return {**core, "official_metrics_sha256": sha256_json(core)}


def _build_native_official_package_from_derivation(
    *,
    derivation_artifact: dict[str, object],
    reservation_plan: dict[str, object],
    ledger_snapshot: dict[str, object],
) -> dict[str, object]:
    derivation_core = {
        key: value
        for key, value in derivation_artifact.items()
        if key != "derivation_sha256"
    }
    row_settlement = validate_complete_row_settlement(reservation_plan, ledger_snapshot)
    release_lock = json.loads(
        (ROOT / "benchmarks/manifests/longmemeval_v2.lock.json").read_text(encoding="utf-8")
    )
    expected_pinned = {
        "code_commit": release_lock["code"]["commit"],
        "dataset_revision": release_lock["dataset"]["revision"],
        "official_harness_sha256": release_lock["code"]["files"]["evaluation/harness.py"],
        "official_scorer_sha256": release_lock["code"]["files"]["evaluation/qa_eval_metrics.py"],
        "compute_lafs_sha256": release_lock["code"]["files"]["leaderboard/compute_lafs.py"],
    }
    lafs = derivation_artifact.get("lafs")
    lafs_core = (
        {key: value for key, value in lafs.items() if key != "lafs_proof_sha256"}
        if isinstance(lafs, dict)
        else None
    )
    if (
        derivation_artifact.get("derivation_sha256") != sha256_json(derivation_core)
        or derivation_artifact.get("reservation_plan_sha256")
        != reservation_plan.get("reservation_plan_sha256")
        or derivation_artifact.get("row_settlement") != row_settlement
        or derivation_artifact.get("sources_sha256")
        != sha256_json(derivation_artifact.get("sources"))
        or derivation_artifact.get("pairs_sha256")
        != sha256_json(derivation_artifact.get("pairs"))
        or derivation_artifact.get("arms_sha256")
        != sha256_json(derivation_artifact.get("arms"))
        or derivation_artifact.get("pinned") != expected_pinned
        or not isinstance(derivation_artifact.get("sources"), list)
        or len(derivation_artifact["sources"]) != QUESTION_COUNT * 2
        or not isinstance(derivation_artifact.get("pairs"), list)
        or len(derivation_artifact["pairs"]) != QUESTION_COUNT
        or not isinstance(lafs, dict)
        or lafs.get("compute_lafs_sha256") != expected_pinned["compute_lafs_sha256"]
        or lafs.get("lafs_proof_sha256") != sha256_json(lafs_core)
    ):
        raise RuntimeError("native official derivation artifact is invalid")
    pairs = derivation_artifact["pairs"]
    arms = derivation_artifact["arms"]
    lafs = derivation_artifact["lafs"]
    metrics = validate_paired_results(
        {
            "pairs": pairs,
            "lafs_gain": lafs["summary"].get("lafs_gain"),
            "submission_score": arms["deep"]["overall"].get("overall_full_set"),
            "accepted_submission": False,
            "published_leaderboard_scores": [],
        }
    )
    official_metrics_artifact = official_metrics_from_derivation(derivation_artifact)
    core = {
        "schema_version": 1,
        "benchmark": "LongMemEval-V2/medium-native",
        "upstream": {
            "code_commit": derivation_artifact["pinned"]["code_commit"],
            "dataset_revision": derivation_artifact["pinned"]["dataset_revision"],
            "native_harness_sha256": derivation_artifact["pinned"][
                "official_harness_sha256"
            ],
        },
        "execution_plan_sha256": reservation_plan["execution_plan_sha256"],
        "reservation_plan_sha256": reservation_plan["reservation_plan_sha256"],
        "row_settlement": row_settlement,
        "official_derivation_sha256": derivation_artifact["derivation_sha256"],
        "source_manifest_sha256": derivation_artifact["sources_sha256"],
        "pairs": pairs,
        "pairs_sha256": sha256_json(pairs),
        "official_metrics": metrics,
        "official_metrics_artifact_sha256": official_metrics_artifact["official_metrics_sha256"],
        "claims": {
            "official_package_complete": True,
            "internal_benchmark_success": metrics["internal_benchmark_success"],
            "positive_lafs": metrics["positive_lafs"],
            "external_sota": metrics["external_sota"],
        },
    }
    return {**core, "native_package_sha256": sha256_json(core)}


def build_native_official_package(
    *,
    reservation_plan: dict[str, object],
    ledger_snapshot: dict[str, object],
    private_rows: dict[str, dict[str, object]],
    judge_root: Path,
    official_dir: Path,
    runtime_code: dict[str, object],
    derivation_path: Path,
) -> dict[str, object]:
    """Build only by rederiving the immutable checkpoint-to-package authority."""
    derivation = derive_native_official_artifact(
        reservation_plan=reservation_plan,
        ledger_snapshot=ledger_snapshot,
        private_rows=private_rows,
        judge_root=judge_root,
        official_dir=official_dir,
        runtime_code=runtime_code,
        output_path=derivation_path,
    )
    return _build_native_official_package_from_derivation(
        derivation_artifact=derivation,
        reservation_plan=reservation_plan,
        ledger_snapshot=ledger_snapshot,
    )


def close_completed_row_campaign(
    *,
    ledger: object,
    reservation_plan: dict[str, object],
    native_package: dict[str, object],
    derivation_artifact: dict[str, object],
    official_metrics_artifact: dict[str, object],
    closure_path: Path,
) -> dict[str, object]:
    package_core = {
        key: value
        for key, value in native_package.items()
        if key != "native_package_sha256"
    }
    if (
        native_package.get("native_package_sha256") != sha256_json(package_core)
        or native_package.get("reservation_plan_sha256")
        != reservation_plan.get("reservation_plan_sha256")
        or native_package.get("row_settlement")
        != validate_complete_row_settlement(reservation_plan, ledger.snapshot())
        or native_package.get("official_derivation_sha256")
        != derivation_artifact.get("derivation_sha256")
        or derivation_artifact.get("derivation_sha256")
        != sha256_json(
            {
                key: value
                for key, value in derivation_artifact.items()
                if key != "derivation_sha256"
            }
        )
        or native_package.get("official_metrics", {}).get("fully_settled") is not True
        or official_metrics_artifact.get("official_metrics_sha256")
        != native_package.get("official_metrics_artifact_sha256")
        or official_metrics_artifact.get("official_metrics_sha256")
        != sha256_json(
            {
                key: value
                for key, value in official_metrics_artifact.items()
                if key != "official_metrics_sha256"
            }
        )
        or native_package.get("official_metrics", {}).get("positive_lafs")
        != (
            Decimal(
                str(
                    official_metrics_artifact.get("lafs", {})
                    .get("summary", {})
                    .get("lafs_gain")
                )
            )
            > 0
        )
    ):
        raise RuntimeError("campaign closure requires the exact native package")
    snapshot = ledger.snapshot()
    if (
        snapshot.get("unresolved_max_liability_nanos", 0) != 0
        or snapshot.get("total_liability_nanos", 0) > HARD_CEILING_NANOS
    ):
        raise RuntimeError("campaign closure requires full financial settlement")
    closure = ledger.close_campaign(closure_path)
    if (
        not isinstance(closure, dict)
        or closure.get("unresolved_max_liability_nanos") != 0
        or closure.get("total_liability_nanos", 0) > HARD_CEILING_NANOS
        or not _valid_sha256(closure.get("journal_sha256"))
    ):
        raise RuntimeError("campaign closure projection is invalid")
    return closure


def _cost_nanos_from_reported_usage(usage: object) -> int:
    if not isinstance(usage, dict):
        raise RuntimeError("provider response usage is missing")
    try:
        cost = Decimal(str(usage.get("cost")))
    except InvalidOperation as error:
        raise RuntimeError("provider response cost is invalid") from error
    if not cost.is_finite() or cost <= 0:
        raise RuntimeError("provider response cost is invalid")
    return int(
        (cost * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_CEILING)
    )


def _cost_string_from_nanos(cost_nanos: int) -> str:
    return format(Decimal(cost_nanos) / Decimal(1_000_000_000), "f")


def execute_strict_reader_call(
    *,
    payload: dict[str, object],
    row_key: str,
    row_state: RowExecutionStateMachine,
    transport,
    persist_private,
) -> dict[str, object]:
    """Execute one no-retry Qwen reader call through DeepInfra only."""
    provider = payload.get("provider")
    if (
        payload.get("model") != "qwen/qwen3.5-9b-20260310"
        or payload.get("max_tokens") != 20_000
        or payload.get("temperature") != 0.6
        or payload.get("top_p") != 0.95
        or payload.get("top_k") != 20
        or not isinstance(payload.get("messages"), list)
        or provider
        != {
            "only": ["deepinfra"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
            "quantizations": ["bf16"],
            "max_price": {"prompt": 0.1, "completion": 0.15},
        }
    ):
        raise RuntimeError("strict Qwen DeepInfra reader request drift")
    request_sha256 = sha256_json(payload)
    private_persisted = False
    row_state.start(
        row_key,
        "reader",
        requested_model="qwen/qwen3.5-9b-20260310",
        request_sha256=request_sha256,
    )
    started = time.monotonic()
    try:
        result = transport(payload)
        response = result.get("response") if isinstance(result, dict) else None
        generation = result.get("generation") if isinstance(result, dict) else None
        usage = response.get("usage") if isinstance(response, dict) else None
        choices = response.get("choices") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict)
            or not isinstance(generation, dict)
            or response.get("id") != generation.get("id")
            or response.get("model") != "qwen/qwen3.5-9b"
            or generation.get("model") != "qwen/qwen3.5-9b"
            or generation.get("provider_name") != "DeepInfra"
            or not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get("message"), dict)
            or not isinstance(choices[0]["message"].get("content"), str)
            or not choices[0]["message"]["content"]
            or not isinstance(usage, dict)
            or type(usage.get("prompt_tokens")) is not int
            or type(usage.get("completion_tokens")) is not int
            or type(usage.get("total_tokens")) is not int
            or usage["prompt_tokens"] <= 0
            or usage["completion_tokens"] <= 0
            or usage["total_tokens"]
            != usage["prompt_tokens"] + usage["completion_tokens"]
            or generation.get("tokens_prompt") != usage["prompt_tokens"]
            or generation.get("tokens_completion") != usage["completion_tokens"]
        ):
            raise RuntimeError("strict Qwen DeepInfra reader route response drift")
        response_cost = _cost_nanos_from_reported_usage(usage)
        generation_cost = _cost_nanos_from_reported_usage(
            {"cost": generation.get("total_cost")}
        )
        if generation_cost != response_cost:
            raise RuntimeError("strict Qwen DeepInfra reader cost reconciliation drift")
        raw_provider_record = {"response": response, "generation": generation}
        receipt = {
            "response_id": response["id"],
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": response["model"],
            "provider": generation["provider_name"],
            "usage": usage,
            "elapsed_seconds": time.monotonic() - started,
            "retry_index": 0,
            "parse_status": "provider_response_validated",
            "request_sha256": request_sha256,
            "result_sha256": sha256_json(raw_provider_record),
        }
        persist_private(raw_provider_record, receipt)
        private_persisted = True
        row_state.result(row_key, "reader", receipt)
        return response
    except BaseException as error:
        if not private_persisted:
            row_state.error(
                row_key,
                "reader",
                type(error).__name__,
                "qwen-deepinfra-reader",
            )
        raise


def execute_native_judge_call(
    *,
    payload: dict[str, object],
    row_key: str,
    row_state: RowExecutionStateMachine,
    transport,
    persist_private,
) -> dict[str, object]:
    """Execute one native OpenAI GPT-5.2 medium judge call without retry."""
    if (
        payload.get("model") != "gpt-5.2-2025-12-11"
        or payload.get("max_completion_tokens") != 2048
        or payload.get("reasoning_effort") != "medium"
        or not isinstance(payload.get("messages"), list)
        or not payload["messages"]
    ):
        raise RuntimeError("native GPT-5.2 medium judge request drift")
    request_sha256 = sha256_json(payload)
    private_persisted = False
    row_state.start(
        row_key,
        "judge",
        requested_model="gpt-5.2-2025-12-11",
        request_sha256=request_sha256,
    )
    started = time.monotonic()
    try:
        response = transport(payload)
        usage = response.get("usage") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict)
            or response.get("model") != "gpt-5.2-2025-12-11"
            or not isinstance(response.get("id"), str)
            or not response["id"]
            or not isinstance(response.get("choices"), list)
            or len(response["choices"]) != 1
            or not isinstance(response["choices"][0], dict)
            or not isinstance(response["choices"][0].get("message"), dict)
            or not isinstance(
                response["choices"][0]["message"].get("content"), str
            )
            or not response["choices"][0]["message"]["content"]
            or not isinstance(usage, dict)
            or type(usage.get("prompt_tokens")) is not int
            or type(usage.get("completion_tokens")) is not int
            or type(usage.get("total_tokens")) is not int
            or usage["prompt_tokens"] <= 0
            or usage["completion_tokens"] <= 0
            or usage["total_tokens"]
            != usage["prompt_tokens"] + usage["completion_tokens"]
        ):
            raise RuntimeError("native GPT-5.2 medium judge response drift")
        settled_nanos = (
            _ceil_cost(usage["prompt_tokens"], 1_750_000_000)
            + _ceil_cost(usage["completion_tokens"], 14_000_000_000)
        )
        receipt = {
            "response_id": response["id"],
            "requested_model": "gpt-5.2-2025-12-11",
            "served_model": response["model"],
            "provider": "OpenAI",
            "usage": {
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
                "cost": _cost_string_from_nanos(settled_nanos),
            },
            "elapsed_seconds": time.monotonic() - started,
            "retry_index": 0,
            "parse_status": "provider_response_validated",
            "request_sha256": request_sha256,
            "result_sha256": sha256_json(response),
        }
        persist_private(response, receipt)
        private_persisted = True
        row_state.result(row_key, "judge", receipt)
        return response
    except BaseException as error:
        if not private_persisted:
            row_state.error(
                row_key,
                "judge",
                type(error).__name__,
                "openai-native-judge",
            )
        raise


class _MeteredChatProxy(ThreadingHTTPServer):
    """One logical paid request with byte-stable local retry replay."""

    daemon_threads = True

    def __init__(self, dispatch) -> None:
        self._dispatch = dispatch
        self._lock = threading.Lock()
        self._request_sha256: str | None = None
        self._response: dict[str, object] | None = None
        self.dispatch_count = 0
        super().__init__(("127.0.0.1", 0), _MeteredChatHandler)

    def dispatch(self, payload: dict[str, object]) -> dict[str, object]:
        request_sha256 = sha256_json(payload)
        with self._lock:
            if self._request_sha256 is not None:
                if request_sha256 != self._request_sha256:
                    raise RuntimeError("local SDK retry request identity drift")
                if self._response is None:
                    raise RuntimeError("local SDK retry raced an unresolved request")
                return self._response
            self._request_sha256 = request_sha256
            self.dispatch_count += 1
            response = self._dispatch(payload)
            if not isinstance(response, dict):
                raise RuntimeError("metered proxy dispatch returned invalid JSON")
            self._response = response
            return response


class _MeteredChatHandler(BaseHTTPRequestHandler):
    server: _MeteredChatProxy

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", ""))
            if length <= 0 or length > 64 * 1024 * 1024:
                raise RuntimeError("metered proxy request size is invalid")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise RuntimeError("metered proxy request is not an object")
            response = self.server.dispatch(payload)
            body = canonical_json(response)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BaseException as error:
            body = canonical_json(
                {"error": {"type": type(error).__name__, "message": "metered proxy rejected request"}}
            )
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _start_metered_proxy(dispatch) -> tuple[_MeteredChatProxy, str]:
    server = _MeteredChatProxy(dispatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


def start_reader_proxy(
    *,
    row_key: str,
    row_state: RowExecutionStateMachine,
    transport,
    persist_private,
    before_start=lambda: None,
) -> tuple[_MeteredChatProxy, str]:
    """Expose the official OpenAI-compatible reader wire to one metered call."""

    def dispatch(official_payload: dict[str, object]) -> dict[str, object]:
        extra = official_payload.get("extra_body")
        top_k = official_payload.get("top_k")
        if isinstance(extra, dict):
            top_k = extra.get("top_k", top_k)
        allowed = {
            "model", "messages", "max_tokens", "temperature", "top_p",
            "top_k", "extra_body",
        }
        if (
            set(official_payload) - allowed
            or official_payload.get("model") != "Qwen/Qwen3.5-9B"
            or official_payload.get("max_tokens") != 20_000
            or official_payload.get("temperature") != 0.6
            or official_payload.get("top_p") != 0.95
            or top_k != 20
            or not isinstance(official_payload.get("messages"), list)
            or not official_payload["messages"]
            or (
                extra is not None
                and (not isinstance(extra, dict) or set(extra) != {"top_k"})
            )
        ):
            raise RuntimeError("official Qwen reader wire request drift")
        before_start()
        payload = {
            "model": "qwen/qwen3.5-9b-20260310",
            "messages": official_payload.get("messages"),
            "max_tokens": official_payload.get("max_tokens"),
            "temperature": official_payload.get("temperature"),
            "top_p": official_payload.get("top_p"),
            "top_k": top_k,
            "provider": {
                "only": ["deepinfra"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
                "quantizations": ["bf16"],
                "max_price": {"prompt": 0.1, "completion": 0.15},
            },
        }
        return execute_strict_reader_call(
            payload=payload,
            row_key=row_key,
            row_state=row_state,
            transport=transport,
            persist_private=persist_private,
        )

    return _start_metered_proxy(dispatch)


def start_judge_proxy(
    *,
    row_key: str,
    row_state: RowExecutionStateMachine,
    transport,
    persist_private,
) -> tuple[_MeteredChatProxy, str]:
    """Expose the pinned synchronous Chat Completions judge wire."""

    def dispatch(payload: dict[str, object]) -> dict[str, object]:
        return execute_native_judge_call(
            payload=payload,
            row_key=row_key,
            row_state=row_state,
            transport=transport,
            persist_private=persist_private,
        )

    return _start_metered_proxy(dispatch)


def _provider_json_request(
    url: str,
    *,
    api_key: str,
    payload: dict[str, object] | None = None,
    timeout: float = 43_200.0,
) -> dict[str, object]:
    if not api_key:
        raise RuntimeError("provider credential is missing")
    request = urllib.request.Request(
        url,
        data=None if payload is None else canonical_json(payload),
        method="GET" if payload is None else "POST",
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
    )
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectHandler()
        )
        with opener.open(request, timeout=timeout) as response:
            body = json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        raise RuntimeError("provider request failed") from error
    if not isinstance(body, dict):
        raise RuntimeError("provider response is not a JSON object")
    return body


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeepRecallProxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        api_key: str,
        *,
        journal_path: Path,
        private_root: Path,
        row_key: str,
        authority: dict[str, object],
        transport=None,
    ) -> None:
        if not api_key:
            raise RuntimeError("Deep proxy requires OPENROUTER_API_KEY")
        if (
            set(authority) != PRIVATE_OUTPUT_AUTHORITY_FIELDS
            or not all(_valid_sha256(authority.get(key)) for key in authority)
            or not row_key
        ):
            raise RuntimeError("Deep attempt journal authority is invalid")
        self.api_key = api_key
        self.row_key = row_key
        self.authority = authority
        self.transport = transport or _provider_raw_request
        self.started = time.monotonic()
        self.journal_path = journal_path.absolute()
        self.private_root = private_root.absolute()
        self.private_root.mkdir(parents=True, exist_ok=True)
        self.private_root.chmod(0o700)
        if (
            self.private_root.is_symlink()
            or not self.journal_path.is_relative_to(self.private_root)
        ):
            raise RuntimeError("Deep attempt journal escapes private authority")
        cursor = self.private_root
        for part in self.journal_path.parent.relative_to(self.private_root).parts:
            cursor /= part
            if cursor.exists() and cursor.is_symlink():
                raise RuntimeError("Deep attempt journal parent is a symlink")
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.parent.chmod(0o700)
        self._events = self._load_journal()
        self._dispatch_results = [
            event for event in self._events if event.get("event") == "dispatch_result"
        ]
        self.dispatch_count = len(
            [event for event in self._events if event.get("event") == "dispatch_intent"]
        )
        self.generation_ids = [
            str(event["generation_id"])
            for event in self._dispatch_results
            if isinstance(event.get("generation_id"), str)
            and event["generation_id"]
        ]
        self.generations: dict[str, dict[str, object]] = {}
        for event in self._events:
            if event.get("event") == "generation_record":
                record = event.get("generation")
                generation_id = event.get("generation_id")
                if isinstance(record, dict) and isinstance(generation_id, str):
                    self.generations[generation_id] = record
        self._replay_cursor = 0
        self._terminal_rejection = any(
            event.get("event") == "terminal_rejection" for event in self._events
        ) or self.dispatch_count != len(self._dispatch_results)
        self._lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _DeepRecallHandler)

    def _load_journal(self) -> list[dict[str, object]]:
        if not self.journal_path.exists():
            self._append_event(
                "journal_open",
                {"row_key": self.row_key, "authority": self.authority},
                existing=[],
            )
        if self.journal_path.is_symlink() or not self.journal_path.is_file():
            raise RuntimeError("Deep attempt journal path is invalid")
        events: list[dict[str, object]] = []
        previous_sha256 = "0" * 64
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise RuntimeError("Deep attempt journal is unreadable") from error
        for sequence, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError("Deep attempt journal is corrupt") from error
            core = {key: value for key, value in event.items() if key != "event_sha256"}
            if (
                not isinstance(event, dict)
                or event.get("sequence") != sequence
                or event.get("previous_sha256") != previous_sha256
                or event.get("event_sha256") != sha256_json(core)
            ):
                raise RuntimeError("Deep attempt journal hash chain is invalid")
            events.append(event)
            previous_sha256 = event["event_sha256"]
        if (
            not events
            or events[0].get("event") != "journal_open"
            or events[0].get("row_key") != self.row_key
            or events[0].get("authority") != self.authority
        ):
            raise RuntimeError("Deep attempt journal authority drift")
        intents: dict[int, dict[str, object]] = {}
        results: dict[int, dict[str, object]] = {}
        for event in events[1:]:
            turn = event.get("turn")
            if event.get("event") == "dispatch_intent":
                if (
                    type(turn) is not int
                    or turn != len(intents) + 1
                    or turn in intents
                    or not _valid_sha256(event.get("request_sha256"))
                ):
                    raise RuntimeError("Deep dispatch intent journal is invalid")
                intents[turn] = event
            elif event.get("event") == "dispatch_result":
                encoded = event.get("response_body_base64")
                try:
                    decoded = base64.b64decode(str(encoded), validate=True)
                except (ValueError, TypeError) as error:
                    raise RuntimeError("Deep dispatch response journal is invalid") from error
                headers = event.get("response_headers")
                generation_id = event.get("generation_id")
                if (
                    type(turn) is not int
                    or turn in results
                    or turn not in intents
                    or event.get("request_sha256")
                    != intents[turn].get("request_sha256")
                    or type(event.get("status")) is not int
                    or not isinstance(headers, dict)
                    or hashlib.sha256(decoded).hexdigest()
                    != event.get("response_sha256")
                    or headers.get("x-generation-id") != generation_id
                ):
                    raise RuntimeError("Deep dispatch response journal is invalid")
                results[turn] = event
        return events

    def _append_event(
        self,
        event: str,
        payload: dict[str, object],
        *,
        existing: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        events = self._events if existing is None else existing
        previous_sha256 = events[-1]["event_sha256"] if events else "0" * 64
        core = {
            "schema_version": 1,
            "sequence": len(events) + 1,
            "previous_sha256": previous_sha256,
            "event": event,
            **payload,
        }
        record = {**core, "event_sha256": sha256_json(core)}
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        descriptor = os.open(self.journal_path, flags, 0o600)
        try:
            os.write(descriptor, canonical_json(record) + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.journal_path.chmod(0o600)
        directory = os.open(self.journal_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        events.append(record)
        return record

    def _reject_terminally(self, reason: str) -> None:
        if not self._terminal_rejection:
            self._append_event("terminal_rejection", {"reason": reason})
        self._terminal_rejection = True

    def dispatch(
        self, payload: dict[str, object]
    ) -> tuple[int, dict[str, str], bytes]:
        self.validate_payload(payload)
        request_sha256 = sha256_json(payload)
        with self._lock:
            if self._terminal_rejection:
                raise RuntimeError("Deep attempt journal is terminally rejected")
            if self._replay_cursor < len(self._dispatch_results):
                prior = self._dispatch_results[self._replay_cursor]
                if prior.get("request_sha256") != request_sha256:
                    self._reject_terminally("replayed Deep request drift")
                    raise RuntimeError("Deep replay request drift")
                self._replay_cursor += 1
                return (
                    int(prior["status"]),
                    dict(prior["response_headers"]),
                    base64.b64decode(str(prior["response_body_base64"]), validate=True),
                )
            turn = self.dispatch_count + 1
            self._append_event(
                "dispatch_intent",
                {"turn": turn, "request_sha256": request_sha256},
            )
            self.dispatch_count += 1
        try:
            status, headers, body = self.transport(
                "https://openrouter.ai/api/v1/chat/completions",
                api_key=self.api_key,
                body=canonical_json(payload),
            )
        except BaseException:
            with self._lock:
                self._reject_terminally("ambiguous provider dispatch")
            raise
        generation_id = next(
            (
                value
                for key, value in headers.items()
                if key.casefold() == "x-generation-id"
            ),
            None,
        )
        normalized_headers = {
            "content-type": headers.get("Content-Type", headers.get("content-type", "text/event-stream")),
            **({"x-generation-id": generation_id} if generation_id else {}),
        }
        result = {
            "turn": turn,
            "request_sha256": request_sha256,
            "status": status,
            "generation_id": generation_id,
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "response_headers": normalized_headers,
            "response_body_base64": base64.b64encode(body).decode("ascii"),
        }
        with self._lock:
            event = self._append_event("dispatch_result", result)
            self._dispatch_results.append(event)
            self._replay_cursor += 1
            if isinstance(generation_id, str) and generation_id:
                if generation_id in self.generation_ids:
                    self._reject_terminally("duplicate provider generation id")
                    raise RuntimeError("Deep generation id was replayed")
                self.generation_ids.append(generation_id)
            else:
                self._reject_terminally("dispatch lacks generation id")
        return status, normalized_headers, body

    def reconcile_generation(self, generation_id: str) -> dict[str, object] | None:
        if generation_id in self.generations:
            return self.generations[generation_id]
        status, _, body = self.transport(
            "https://openrouter.ai/api/v1/generation?id=" + generation_id,
            api_key=self.api_key,
            body=None,
        )
        if status != 200:
            return None
        try:
            envelope = json.loads(body)
        except json.JSONDecodeError:
            return None
        candidate = envelope.get("data") if isinstance(envelope, dict) else None
        if not isinstance(candidate, dict) or candidate.get("total_cost") is None:
            return None
        with self._lock:
            if generation_id not in self.generations:
                self._append_event(
                    "generation_record",
                    {
                        "generation_id": generation_id,
                        "generation_sha256": sha256_json(candidate),
                        "generation": candidate,
                    },
                )
                self.generations[generation_id] = candidate
        return candidate

    def validate_payload(self, payload: dict[str, object]) -> None:
        provider = payload.get("provider")
        if (
            payload.get("model") != "qwen/qwen3.5-9b-20260310"
            or payload.get("max_completion_tokens") != 4096
            or payload.get("stream") is not True
            or payload.get("tool_choice") != "required"
            or not isinstance(payload.get("messages"), list)
            or not payload["messages"]
            or not isinstance(payload.get("tools"), list)
            or not payload["tools"]
            or provider
            != {
                "only": ["deepinfra"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
                "quantizations": ["bf16"],
                "max_price": {"prompt": 0.1, "completion": 0.15},
            }
        ):
            raise RuntimeError("Deep Qwen request route drift")

    def receipt(self, request_sha256: str) -> dict[str, object]:
        for generation_id in list(self.generation_ids):
            self.reconcile_generation(generation_id)
        with self._lock:
            generations = [self.generations.get(key) for key in self.generation_ids]
        if (
            not generations
            or any(not isinstance(item, dict) for item in generations)
            or self.dispatch_count != len(set(self.generation_ids))
            or self.dispatch_count != len(generations)
            or self._replay_cursor != len(self._dispatch_results)
            or len({item.get("id") for item in generations}) != len(generations)
        ):
            with self._lock:
                self._reject_terminally("dispatch/generation settlement mismatch")
            raise RuntimeError("Deep dispatch/generation settlement mismatch")
        normalized = []
        prompt_tokens = 0
        completion_tokens = 0
        settled_nanos = 0
        for item in generations:
            assert isinstance(item, dict)
            if (
                item.get("model") != "qwen/qwen3.5-9b"
                or item.get("provider_name") != "DeepInfra"
                or type(item.get("tokens_prompt")) is not int
                or type(item.get("tokens_completion")) is not int
                or item["tokens_prompt"] <= 0
                or item["tokens_completion"] <= 0
            ):
                raise RuntimeError("Deep generation route receipt drift")
            cost = _cost_nanos_from_reported_usage({"cost": item.get("total_cost")})
            prompt_tokens += item["tokens_prompt"]
            completion_tokens += item["tokens_completion"]
            settled_nanos += cost
            normalized.append(
                {
                    "id": item["id"],
                    "model": item["model"],
                    "provider_name": item["provider_name"],
                    "tokens_prompt": item["tokens_prompt"],
                    "tokens_completion": item["tokens_completion"],
                    "total_cost": str(item["total_cost"]),
                }
            )
        core = {
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_models": ["qwen/qwen3.5-9b"],
            "served_providers": ["DeepInfra"],
            "allow_fallbacks": False,
            "attempt_count": len(normalized),
            "dispatch_count": self.dispatch_count,
            "generation_ids": list(self.generation_ids),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "settled_nanos": settled_nanos,
            "request_sha256": request_sha256,
            "elapsed_seconds": time.monotonic() - self.started,
            "generation_receipts": normalized,
            "generation_receipts_sha256": sha256_json(normalized),
            "deep_attempt_journal_sha256": _sha256_file(self.journal_path),
        }
        return {**core, "receipt_sha256": sha256_json(core)}


def _provider_raw_request(
    url: str, *, api_key: str, body: bytes | None
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        method="GET" if body is None else "POST",
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler()
    )
    try:
        response = opener.open(request, timeout=43_200)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    with response:
        return response.status, dict(response.headers.items()), response.read()


class _DeepRecallHandler(BaseHTTPRequestHandler):
    server: _DeepRecallProxy

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", ""))
            raw = self.rfile.read(length)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise RuntimeError("Deep proxy body is invalid")
            self.server.validate_payload(payload)
            status, headers, body = self.server.dispatch(payload)
            generation_id = headers.get("x-generation-id")
            self.send_response(status)
            self.send_header("content-type", headers.get("Content-Type", "text/event-stream"))
            self.send_header("content-length", str(len(body)))
            if generation_id:
                self.send_header("x-generation-id", generation_id)
            self.end_headers()
            self.wfile.write(body)
        except BaseException:
            self.send_error(502)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path.rstrip("/") != "/generation" or not parsed.query.startswith("id="):
            self.send_error(404)
            return
        generation_id = parsed.query.removeprefix("id=")
        if generation_id not in self.server.generation_ids:
            self.send_error(409)
            return
        candidate = self.server.reconcile_generation(generation_id)
        status = 200 if candidate is not None else 409
        body = canonical_json({"data": candidate}) if candidate is not None else b"{}"
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_deep_recall_proxy(
    api_key: str,
    *,
    journal_path: Path,
    private_root: Path,
    row_key: str,
    authority: dict[str, object],
) -> tuple[_DeepRecallProxy, str]:
    server = _DeepRecallProxy(
        api_key,
        journal_path=journal_path,
        private_root=private_root,
        row_key=row_key,
        authority=authority,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def openrouter_reader_transport(
    api_key: str, payload: dict[str, object]
) -> dict[str, object]:
    response = _provider_json_request(
        "https://openrouter.ai/api/v1/chat/completions",
        api_key=api_key,
        payload=payload,
    )
    generation_id = response.get("id")
    if not isinstance(generation_id, str) or not generation_id:
        raise RuntimeError("OpenRouter reader response omitted generation id")
    deadline = time.monotonic() + 600
    generation: dict[str, object] | None = None
    while time.monotonic() < deadline:
        envelope = _provider_json_request(
            "https://openrouter.ai/api/v1/generation?id=" + generation_id,
            api_key=api_key,
            timeout=30,
        )
        candidate = envelope.get("data")
        if isinstance(candidate, dict) and candidate.get("total_cost") is not None:
            generation = candidate
            break
        time.sleep(2)
    if generation is None:
        raise RuntimeError("OpenRouter reader generation settlement timed out")
    return {"response": response, "generation": generation}


def openai_judge_transport(
    api_key: str, payload: dict[str, object]
) -> dict[str, object]:
    return _provider_json_request(
        "https://api.openai.com/v1/chat/completions",
        api_key=api_key,
        payload=payload,
    )


PRIVATE_OUTPUT_AUTHORITY_FIELDS = {
    "authorization_sha256",
    "execution_plan_sha256",
    "reservation_plan_sha256",
    "case_bank_sha256",
    "clone_sha256",
    "binary_set_sha256",
    "official_harness_sha256",
    "official_scorer_sha256",
}


def persist_private_provider_output(
    path: Path,
    *,
    private_root: Path,
    row_key: str,
    component: str,
    response: dict[str, object],
    receipt: dict[str, object],
    authority: dict[str, object],
) -> dict[str, object]:
    """Create the durable middle state before a provider result is settled."""
    original_path = path.absolute()
    private_root = private_root.absolute()
    private_root.mkdir(parents=True, exist_ok=True)
    if private_root.is_symlink():
        raise RuntimeError("canonical private root is a symlink")
    private_root.chmod(0o700)
    if not original_path.is_relative_to(private_root):
        raise RuntimeError("private provider output escapes canonical root")
    relative_parent = original_path.parent.relative_to(private_root)
    cursor = private_root
    for part in relative_parent.parts:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise RuntimeError("private provider output parent is a symlink")
    if original_path.is_symlink():
        raise RuntimeError("private provider output path is a symlink")
    path = original_path
    parent = path.parent
    if (
        set(authority) != PRIVATE_OUTPUT_AUTHORITY_FIELDS
        or not all(_valid_sha256(authority.get(key)) for key in authority)
        or component not in {"deep_recall", "reader", "judge"}
        or not isinstance(row_key, str)
        or not row_key
        or not isinstance(response, dict)
        or not isinstance(receipt, dict)
        or receipt.get("result_sha256") != sha256_json(response)
    ):
        raise RuntimeError("private provider output authority is invalid")
    parent.mkdir(parents=True, exist_ok=True)
    parent.chmod(0o700)
    core = {
        "schema_version": 1,
        "status": "PRIVATE_OUTPUT_FSYNCED",
        "row_key": row_key,
        "component": component,
        "authority": authority,
        "response": response,
        "receipt": receipt,
    }
    record = {**core, "private_output_sha256": sha256_json(core)}
    _atomically_create_json(path, record)
    path.chmod(0o600)
    directory = os.open(parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return record


def reconcile_private_provider_output(
    path: Path,
    *,
    row_state: RowExecutionStateMachine,
    row_key: str,
    component: str,
    authority: dict[str, object],
) -> dict[str, object]:
    """Settle a crash-left durable response without another provider call."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("private provider output is unavailable") from error
    core = {
        key: value for key, value in record.items() if key != "private_output_sha256"
    }
    response = record.get("response")
    receipt = record.get("receipt")
    if (
        record.get("private_output_sha256") != sha256_json(core)
        or record.get("status") != "PRIVATE_OUTPUT_FSYNCED"
        or record.get("row_key") != row_key
        or record.get("component") != component
        or record.get("authority") != authority
        or not isinstance(response, dict)
        or not isinstance(receipt, dict)
        or receipt.get("result_sha256") != sha256_json(response)
        or row_state.component_status(row_key, component) != "started"
    ):
        raise RuntimeError("private provider output cannot reconcile campaign state")
    row_state.result(row_key, component, receipt)
    return record


def reserve_deep_recall(
    *, row_key: str, row_state: RowExecutionStateMachine, request_sha256: str
) -> str:
    return row_state.start(
        row_key,
        "deep_recall",
        requested_model="qwen/qwen3.5-9b-20260310",
        request_sha256=request_sha256,
    )


def settle_deep_recall(
    *,
    row_key: str,
    row_state: RowExecutionStateMachine,
    receipt: dict[str, object],
    persist_private,
) -> None:
    """Settle the aggregate Deep liability from its server-owned receipt."""
    private_persisted = False
    try:
        settled_nanos = receipt.get("settled_nanos")
        generation_ids = receipt.get("generation_ids")
        generation_receipts = receipt.get("generation_receipts")
        receipt_core = {
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        }
        if (
            receipt.get("requested_model") != "qwen/qwen3.5-9b-20260310"
            or receipt.get("served_models") != ["qwen/qwen3.5-9b"]
            or receipt.get("served_providers") != ["DeepInfra"]
            or receipt.get("allow_fallbacks") is not False
            or type(receipt.get("attempt_count")) is not int
            or not 1 <= receipt["attempt_count"] <= 72
            or receipt.get("dispatch_count") != receipt["attempt_count"]
            or not isinstance(generation_ids, list)
            or len(generation_ids) != len(set(generation_ids))
            or len(generation_ids) != receipt["attempt_count"]
            or not all(isinstance(value, str) and value for value in generation_ids)
            or not isinstance(generation_receipts, list)
            or len(generation_receipts) != receipt["attempt_count"]
            or [record.get("id") for record in generation_receipts if isinstance(record, dict)]
            != generation_ids
            or receipt.get("generation_receipts_sha256")
            != sha256_json(generation_receipts)
            or not _valid_sha256(receipt.get("deep_attempt_journal_sha256"))
            or type(receipt.get("prompt_tokens")) is not int
            or receipt["prompt_tokens"] <= 0
            or type(receipt.get("completion_tokens")) is not int
            or receipt["completion_tokens"] <= 0
            or receipt.get("total_tokens")
            != receipt["prompt_tokens"] + receipt["completion_tokens"]
            or type(settled_nanos) is not int
            or settled_nanos <= 0
            or not _valid_sha256(receipt.get("request_sha256"))
            or not _valid_sha256(receipt.get("receipt_sha256"))
            or receipt["receipt_sha256"] != sha256_json(receipt_core)
        ):
            raise RuntimeError("Deep recall route receipt drift")
        response = {
            "response_id": f"deep-recall:{receipt['receipt_sha256']}",
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": "qwen/qwen3.5-9b",
            "provider": "DeepInfra",
            "attempt_count": receipt["attempt_count"],
            "receipt_sha256": receipt["receipt_sha256"],
            "usage": {
                "prompt_tokens": receipt["prompt_tokens"],
                "completion_tokens": receipt["completion_tokens"],
                "total_tokens": receipt["total_tokens"],
                "cost": _cost_string_from_nanos(settled_nanos),
            },
            "elapsed_seconds": receipt.get("elapsed_seconds", 0),
            "retry_index": 0,
            "parse_status": "provider_response_validated",
            "request_sha256": receipt["request_sha256"],
            "result_sha256": sha256_json(receipt),
        }
        persist_private(receipt, response)
        private_persisted = True
        row_state.result(row_key, "deep_recall", response)
    except BaseException as error:
        if not private_persisted:
            row_state.error(
                row_key,
                "deep_recall",
                type(error).__name__,
                "qwen-deepinfra-recall",
            )
        raise


CASE_BANK_EXCLUDED_TABLES = (
    "memphant.api_key",
    "memphant.event_outbox",
    "memphant.job_state",
    "memphant.retrieval_trace",
    "memphant.review_event",
    "memphant.review_event_unit",
    # Every clone is migrated before restore. Restoring this table would
    # duplicate its primary keys and, worse, disguise schema drift.
    "memphant.schema_migrations",
)


def _parsed_scratch_postgres_url(
    database_url: str, *, require_base: bool
) -> tuple[object, str]:
    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise RuntimeError("local scratch Postgres base is invalid") from error
    database = parsed.path.removeprefix("/")
    allowed_database = (
        database == "memphant"
        if require_base
        else database.startswith("memphant_lme2_")
    )
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or port != 5432
        or not allowed_database
        or "/" in database
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "local scratch Postgres base must be localhost:5432/memphant"
        )
    return parsed, database


def scratch_case_database_contract(
    base_database_url: str, question_id: str
) -> dict[str, object]:
    """Return credential-free identities for one isolated case bank and pair."""
    parsed, database = _parsed_scratch_postgres_url(
        base_database_url, require_base=True
    )
    if not isinstance(question_id, str) or not question_id:
        raise RuntimeError("case bank question identity is invalid")
    case_key = hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:16]
    databases = {
        arm: f"memphant_lme2_{case_key}_{arm}"
        for arm in ("source", "fast", "deep")
    }
    core = {
        "schema_version": 1,
        "case_key": case_key,
        "base_identity": {
            "scheme": "postgresql",
            "host": parsed.hostname,
            "port": parsed.port,
            "database": database,
        },
        "databases": databases,
    }
    return {**core, "contract_sha256": sha256_json(core)}


def case_bank_dump_command(
    database_url: str, archive: Path, *, pg_dump: str = "pg_dump"
) -> list[str]:
    """Build the canonical data-only archive command for a quiescent source."""
    _, database = _parsed_scratch_postgres_url(database_url, require_base=False)
    if not database.endswith("_source"):
        raise RuntimeError("case bank dump requires the isolated source database")
    return [
        pg_dump,
        "--format=custom",
        "--data-only",
        "--schema=memphant",
        *[
            f"--exclude-table-data={table}"
            for table in CASE_BANK_EXCLUDED_TABLES
        ],
        f"--file={archive.resolve()}",
        database_url,
    ]


def postgres_tool_identity(
    database_url: str,
    tool: str,
    *,
    run_command=subprocess.run,
    allow_base: bool = False,
) -> dict[str, object]:
    """Resolve a client whose major version exactly matches scratch Postgres."""
    if tool not in {"pg_dump", "pg_restore"}:
        raise RuntimeError("unsupported Postgres case-bank tool")
    _parsed_scratch_postgres_url(database_url, require_base=allow_base)
    server = _run_campaign_command(
        ["psql", database_url, "-Atqc", "SHOW server_version_num"],
        run_command=run_command,
    ).stdout.strip()
    if not server.isdigit() or int(server) < 100_000:
        raise RuntimeError("scratch Postgres server version is invalid")
    server_major = int(server) // 10_000
    candidates = [
        shutil.which(tool),
        f"/opt/homebrew/opt/postgresql@{server_major}/bin/{tool}",
        f"/usr/local/opt/postgresql@{server_major}/bin/{tool}",
        f"/usr/lib/postgresql/{server_major}/bin/{tool}",
    ]
    seen: set[str] = set()
    for candidate_value in candidates:
        if not candidate_value:
            continue
        candidate = Path(candidate_value).resolve()
        if str(candidate) in seen or not candidate.is_file():
            continue
        seen.add(str(candidate))
        completed = run_command(
            [str(candidate), "--version"],
            cwd=ROOT,
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            check=False,
        )
        match = re.search(r"\(PostgreSQL\)\s+(\d+)(?:\.|\b)", completed.stdout)
        if completed.returncode != 0 or match is None or int(match.group(1)) != server_major:
            continue
        core = {
            "tool": tool,
            "path": str(candidate),
            "bytes": candidate.stat().st_size,
            "sha256": _sha256_file(candidate),
            "version": completed.stdout.strip(),
            "server_version_num": int(server),
        }
        return {**core, "identity_sha256": sha256_json(core)}
    raise RuntimeError(
        f"Postgres {server_major} {tool} is required for the scratch server"
    )


def cache_only_construction_environment(
    *,
    binding_path: Path,
    binding: dict[str, object],
    manifest: dict[str, object],
    data_root: Path,
    database_url: str,
    base_environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a fail-closed construction environment with no provider secret.

    Only a narrow benign process environment is inherited.  This makes the
    cache-only guarantee independent of whichever provider credentials happen
    to be present in the invoking shell.
    """
    _parsed_scratch_postgres_url(database_url, require_base=False)
    try:
        on_disk = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("construction binding is unavailable") from error
    if on_disk != binding:
        raise RuntimeError("construction binding differs from its immutable file")
    construction = manifest.get("construction")
    authorization = binding.get("authorization")
    provider = binding.get("provider")
    cache = binding.get("cache")
    ledger = binding.get("ledger")
    coverage = binding.get("coverage")
    if not all(
        isinstance(value, dict)
        for value in (
            construction,
            authorization,
            provider,
            cache,
            ledger,
            coverage,
        )
    ):
        raise RuntimeError("construction binding cache contract is incomplete")
    plans = coverage.get("plans")
    if (
        construction.get("model") != "qwen/qwen3.5-9b-20260310"
        or provider.get("requested_model") != construction.get("model")
        or provider.get("served_model") != "qwen/qwen3.5-9b"
        or str(provider.get("served_provider", "")).casefold()
        != "deepinfra"
        or not all(
            _valid_sha256(authorization.get(field))
            for field in ("authorization_sha256", "campaign_sha256")
        )
        or not isinstance(plans, list)
        or not plans
        or any(
            not isinstance(plan, dict)
            or type(plan.get("per_attempt_reservation_nanos")) is not int
            or plan["per_attempt_reservation_nanos"] <= 0
            for plan in plans
        )
    ):
        raise RuntimeError("construction binding route or liability is invalid")
    reservation = sum(plan["per_attempt_reservation_nanos"] for plan in plans)
    inherited = base_environment if base_environment is not None else os.environ
    environment = {
        key: inherited[key]
        for key in ("PATH", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "RUST_BACKTRACE")
        if inherited.get(key)
    }
    environment.update(
        {
            "MEMPHANT_APP_DATABASE_URL": database_url,
            "MEMPHANT_AUTHN_DATABASE_URL": database_url,
            "MEMPHANT_WORKER_DATABASE_URL": database_url,
            "MEMPHANT_STRUCTURED_STATE": "on",
            "MEMPHANT_STRUCTURED_STATE_MODEL": str(construction["model"]),
            "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH": str(
                (ROOT / str(construction["prompt_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION": str(
                provider["input_price_nanos_per_million"]
            ),
            "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION": str(
                provider["output_price_nanos_per_million"]
            ),
            "MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH": str(
                (data_root / str(construction["tokenizer_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH": str(
                (data_root / str(construction["tokenizer_config_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER": str(
                Path(str(ledger["subledger_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_DISPATCH_ROOT": str(
                Path(str(ledger["subledger_path"]))
                .resolve()
                .with_name("private-construction-dispatches")
            ),
            "MEMPHANT_CAMPAIGN_ATTEMPT_LEDGER": str(
                Path(str(ledger["campaign_journal_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE": str(
                Path(str(cache["observation_cache_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_CACHE_HITS": str(
                Path(str(cache["source_receipts_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_AUTHORIZATION_SHA256": str(
                authorization["authorization_sha256"]
            ),
            "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_SHA256": str(
                authorization["campaign_sha256"]
            ),
            "MEMPHANT_STRUCTURED_STATE_CACHE_NAMESPACE": str(cache["namespace"]),
            "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER": str(
                Path(str(ledger["subledger_path"])).resolve()
            ),
            "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_BYTES": str(
                ledger["source_ledger_prefix_bytes"]
            ),
            "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_SHA256": str(
                ledger["source_ledger_prefix_sha256"]
            ),
            "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_MODEL": str(
                provider["served_model"]
            ),
            "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_PROVIDER": str(
                provider["served_provider"]
            ),
            "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS": str(
                reservation
            ),
            "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT": "1",
            "MEMPHANT_STRUCTURED_STATE_CACHE_ONLY": "on",
            "MEMPHANT_LME_CONSTRUCTION_BINDING": str(binding_path.resolve()),
        }
    )
    if any("API_KEY" in key for key in environment):
        raise RuntimeError("cache-only construction environment contains a credential")
    return environment


def write_case_bank_manifest(
    *,
    archive: Path,
    output: Path,
    contract: dict[str, object],
    binding_path: Path,
    binding_authority: dict[str, Path],
    construction_proof_path: Path,
    materialization: dict[str, object],
    logical_inventory: dict[str, int],
    postgres_toolchain: dict[str, object],
) -> dict[str, object]:
    """Seal one cache-only source database into an immutable bank manifest."""
    if not archive.is_file() or archive.stat().st_size <= 0:
        raise RuntimeError("case bank archive is missing or empty")
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        construction_proof = json.loads(
            construction_proof_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("case bank construction authority is unavailable") from error
    binding_core = {
        key: value for key, value in binding.items() if key != "binding_sha256"
    }
    binding_sha256 = binding.get("binding_sha256")
    if (
        not isinstance(binding, dict)
        or not _valid_sha256(binding_sha256)
        or binding_sha256 != sha256_json(binding_core)
    ):
        raise RuntimeError("case bank construction binding is invalid")
    required_authority = {
        "authorization_path",
        "census_path",
        "manifest_path",
        "wave_path",
        "binding_root",
    }
    if set(binding_authority) != required_authority or any(
        not isinstance(path, Path) for path in binding_authority.values()
    ):
        raise RuntimeError("case bank canonical binding authority is incomplete")
    canonical_binding = _load_canonical_construction_binding(
        binding_path, **binding_authority
    )
    if canonical_binding != binding:
        raise RuntimeError("case bank binding differs from canonical authority")
    validate_construction_proof_v2(construction_proof)
    derived_cache, derived_ledger = _derive_canonical_construction_receipts(binding)
    proof_compiler = construction_proof.get("compiler")
    if (
        construction_proof.get("binding_sha256") != binding_sha256
        or construction_proof.get("authorization") != binding.get("authorization")
        or construction_proof.get("selection") != binding.get("selection")
        or construction_proof.get("provider") != binding.get("provider")
        or construction_proof.get("cache") != derived_cache
        or construction_proof.get("ledger") != derived_ledger
        or not isinstance(proof_compiler, dict)
        or any(
            proof_compiler.get(key) != binding.get("compiler", {}).get(key)
            for key in ("prompt_sha256", "schema_sha256", "provider_code_sha256")
        )
    ):
        raise RuntimeError("case bank construction proof differs from binding")
    if (
        set(materialization)
        != {
            "trajectory_count",
            "trajectory_ids_sha256",
            "trajectory_content_sha256",
        }
        or type(materialization.get("trajectory_count")) is not int
        or materialization["trajectory_count"] <= 0
        or not _valid_sha256(materialization.get("trajectory_ids_sha256"))
        or not _valid_sha256(materialization.get("trajectory_content_sha256"))
        or _contains_oracle_key(materialization)
    ):
        raise RuntimeError("case bank materialization identity is invalid")
    if (
        not isinstance(logical_inventory, dict)
        or logical_inventory.get("schema_migrations", 0) <= 0
        or logical_inventory.get("tenant") != 1
        or any(
            not isinstance(table, str)
            or not table
            or type(count) is not int
            or count < 0
            for table, count in logical_inventory.items()
        )
        or any(table.removeprefix("memphant.") in {
            excluded.removeprefix("memphant.")
            for excluded in CASE_BANK_EXCLUDED_TABLES
        } - {"schema_migrations"} for table in logical_inventory)
    ):
        raise RuntimeError("case bank logical inventory is invalid")
    toolchain_core = {
        key: value
        for key, value in postgres_toolchain.items()
        if key != "toolchain_sha256"
    }
    identities = toolchain_core.get("identities")
    if (
        postgres_toolchain.get("toolchain_sha256") != sha256_json(toolchain_core)
        or not isinstance(identities, dict)
        or set(identities) != {"pg_dump", "pg_restore"}
        or any(
            not isinstance(identity, dict)
            or identity.get("tool") != name
            or identity.get("executable") != name
            or "path" in identity
            or identity.get("identity_sha256")
            != sha256_json(
                {
                    key: value
                    for key, value in identity.items()
                    if key != "identity_sha256"
                }
            )
            for name, identity in identities.items()
        )
        or identities["pg_dump"].get("server_version_num")
        != identities["pg_restore"].get("server_version_num")
    ):
        raise RuntimeError("case bank Postgres toolchain identity is invalid")
    contract_core = {
        key: value for key, value in contract.items() if key != "contract_sha256"
    }
    if (
        contract.get("contract_sha256") != sha256_json(contract_core)
        or _contains_oracle_key(contract)
    ):
        raise RuntimeError("case bank scratch contract is invalid")
    core = {
        "schema_version": 1,
        "contract": contract,
        "archive": {
            "bytes": archive.stat().st_size,
            "sha256": _sha256_file(archive),
            "format": "pg_dump-custom-data-only-v1",
        },
        "construction": {
            "binding_sha256": binding_sha256,
            "proof_sha256": construction_proof["construction_proof_sha256"],
        },
        "materialization": materialization,
        "logical_inventory": dict(sorted(logical_inventory.items())),
        "logical_inventory_sha256": sha256_json(
            dict(sorted(logical_inventory.items()))
        ),
        "postgres_toolchain": postgres_toolchain,
    }
    if _contains_oracle_key(core):
        raise RuntimeError("case bank manifest contains oracle-bearing fields")
    manifest = {**core, "case_bank_sha256": sha256_json(core)}
    _create_json(output, manifest)
    return manifest


def _database_url_for_name(base_database_url: str, database_name: str) -> str:
    parsed, _ = _parsed_scratch_postgres_url(
        base_database_url, require_base=True
    )
    if not re.fullmatch(r"memphant_lme2_[0-9a-f]{16}_(?:source|fast|deep)", database_name):
        raise RuntimeError("scratch database name is outside the case contract")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{database_name}", "", "")
    )


def _run_campaign_command(
    command: list[str], *, run_command=subprocess.run
) -> subprocess.CompletedProcess[str]:
    completed = run_command(
        command,
        cwd=ROOT,
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() if isinstance(completed.stderr, str) else ""
        raise RuntimeError(
            f"scratch case-bank command failed: {command[0]}: {stderr}"
        )
    return completed


def database_logical_inventory(
    database_url: str, *, run_command=subprocess.run
) -> dict[str, int]:
    """Count every durable bank table without reading row contents."""
    _parsed_scratch_postgres_url(database_url, require_base=False)
    tables_result = _run_campaign_command(
        [
            "psql",
            database_url,
            "-Atqc",
            (
                "select tablename from pg_catalog.pg_tables "
                "where schemaname='memphant' order by tablename"
            ),
        ],
        run_command=run_command,
    )
    tables = [line.strip() for line in tables_result.stdout.splitlines() if line.strip()]
    if not tables or any(re.fullmatch(r"[a-z][a-z0-9_]*", table) is None for table in tables):
        raise RuntimeError("scratch database memphant table inventory is invalid")
    excluded = {
        table.removeprefix("memphant.") for table in CASE_BANK_EXCLUDED_TABLES
    } - {"schema_migrations"}
    inventory: dict[str, int] = {}
    for table in tables:
        if table in excluded:
            continue
        result = _run_campaign_command(
            [
                "psql",
                database_url,
                "-Atqc",
                f'SELECT count(*) FROM memphant."{table}"',
            ],
            run_command=run_command,
        )
        try:
            count = int(result.stdout.strip())
        except ValueError as error:
            raise RuntimeError("scratch database row count is invalid") from error
        if count < 0:
            raise RuntimeError("scratch database row count is invalid")
        inventory[table] = count
    if inventory.get("schema_migrations", 0) <= 0 or inventory.get("tenant") != 1:
        raise RuntimeError("scratch case database is not a migrated single-tenant bank")
    return inventory


def assert_case_source_quiescent(
    database_url: str, *, run_command=subprocess.run
) -> dict[str, int]:
    """Require worker drain and stopped server before taking a case archive."""
    _parsed_scratch_postgres_url(database_url, require_base=False)
    result = _run_campaign_command(
        [
            "psql",
            database_url,
            "-Atqc",
            (
                "SELECT (SELECT count(*) FROM memphant.job_state "
                "WHERE state IN ('queued','running')) || '|' || "
                "(SELECT count(*) FROM pg_catalog.pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid())"
            ),
        ],
        run_command=run_command,
    )
    try:
        active_jobs, other_connections = (
            int(value) for value in result.stdout.strip().split("|", 1)
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("scratch case quiescence proof is invalid") from error
    proof = {
        "active_jobs": active_jobs,
        "other_connections": other_connections,
    }
    if proof != {"active_jobs": 0, "other_connections": 0}:
        raise RuntimeError("scratch case source is not quiescent")
    return proof


@contextmanager
def _campaign_environment(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _free_loopback_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _wait_server_health(base_url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("MemPhant server exited before health readiness")
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("MemPhant server health readiness timed out")


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _campaign_binaries() -> dict[str, Path]:
    binaries = {
        name: ROOT / "target/release" / f"memphant-{name}"
        for name in ("server", "worker", "cli")
    }
    if any(not path.is_file() for path in binaries.values()):
        raise RuntimeError("authorized release binaries are unavailable")
    return binaries


def _selected_trajectories(
    data_root: Path, trajectory_ids: list[str]
) -> dict[str, dict[str, object]]:
    wanted = set(trajectory_ids)
    selected: dict[str, dict[str, object]] = {}
    with (data_root / "trajectories.jsonl").open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if isinstance(row, dict) and row.get("id") in wanted:
                selected[str(row["id"])] = row
    if set(selected) != wanted:
        raise RuntimeError("case-bank trajectory selection is incomplete")
    return selected


def _case_construction_plans(
    census: dict[str, object],
    trajectories: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    adapter = _load_adapter()
    source_hashes = {
        hashlib.sha256(row["source_body"].encode()).hexdigest()
        for trajectory in trajectories.values()
        for row in adapter.census_resource_rows(trajectory, uses=1)
    }
    plans = census.get("construction", {}).get("plan_inventory", [])
    selected = [
        plan
        for plan in plans
        if isinstance(plan, dict)
        and plan.get("source_body_sha256") in source_hashes
    ]
    if (
        not selected
        or {plan.get("source_body_sha256") for plan in selected} != source_hashes
    ):
        raise RuntimeError("case-bank construction plan coverage is incomplete")
    return selected


def ensure_case_bank(
    *,
    authorization_path: Path,
    census: dict[str, object],
    data_root: Path,
    base_database_url: str,
    question_id: str,
    bank_root: Path,
) -> tuple[dict[str, object], Path, Path]:
    """Build one immutable cache-only case bank or validate its checkpoint."""
    contract = scratch_case_database_contract(base_database_url, question_id)
    case_key = contract["case_key"]
    bank_dir = bank_root.resolve() / case_key
    manifest_path = bank_dir / "manifest.json"
    archive_path = bank_dir / "bank.dump"
    proof_path = bank_dir / "CONSTRUCTION-PROOF.v2.json"
    if manifest_path.is_file() and archive_path.is_file() and proof_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        manifest_core = {
            key: value for key, value in manifest.items() if key != "case_bank_sha256"
        }
        validate_construction_proof_v2(proof)
        if (
            manifest.get("case_bank_sha256") != sha256_json(manifest_core)
            or manifest.get("contract") != contract
            or manifest.get("archive", {}).get("bytes") != archive_path.stat().st_size
            or manifest.get("archive", {}).get("sha256") != _sha256_file(archive_path)
            or manifest.get("construction", {}).get("proof_sha256")
            != proof.get("construction_proof_sha256")
            or manifest.get("logical_inventory_sha256")
            != sha256_json(manifest.get("logical_inventory"))
            or manifest.get("postgres_toolchain", {}).get("toolchain_sha256")
            != sha256_json(
                {
                    key: value
                    for key, value in manifest.get("postgres_toolchain", {}).items()
                    if key != "toolchain_sha256"
                }
            )
        ):
            raise RuntimeError("immutable case bank checkpoint drift")
        return manifest, archive_path, proof_path
    if bank_dir.exists():
        raise RuntimeError("incomplete case bank requires explicit adjudication")

    haystack = json.loads(
        (data_root / "haystacks/lme_v2_medium.json").read_text(encoding="utf-8")
    )
    trajectory_ids = haystack.get(question_id)
    if not isinstance(trajectory_ids, list) or not trajectory_ids:
        raise RuntimeError("case-bank haystack is missing")
    trajectories = _selected_trajectories(data_root, trajectory_ids)
    plans = _case_construction_plans(census, trajectories)
    binding_path = create_construction_binding(authorization_path, plans)
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    source_name = contract["databases"]["source"]
    source_url = _database_url_for_name(base_database_url, source_name)
    staging = bank_root.resolve() / (".staging-" + case_key)
    staging.mkdir(parents=True, exist_ok=False)
    staging.chmod(0o700)
    binaries = _campaign_binaries()
    server: subprocess.Popen | None = None
    try:
        _run_campaign_command(
            ["dropdb", f"--maintenance-db={base_database_url}", "--if-exists", "--force", source_name]
        )
        _run_campaign_command(
            ["createdb", f"--maintenance-db={base_database_url}", source_name]
        )
        _run_campaign_command(
            [sys.executable, str(ROOT / "scripts/apply_memphant_migrations.py"), "--database-url", source_url]
        )
        environment = cache_only_construction_environment(
            binding_path=binding_path,
            binding=binding,
            manifest=json.loads(CANONICAL_CAMPAIGN_MANIFEST.read_text(encoding="utf-8")),
            data_root=data_root,
            database_url=source_url,
        )
        port = _free_loopback_port()
        server_url = f"http://127.0.0.1:{port}"
        environment.update(
            {
                "MEMPHANT_BIND": f"127.0.0.1:{port}",
                "MEMPHANT_RESOURCE_CHUNKS": "on",
                "MEMPHANT_DEEP": "off",
            }
        )
        stdout = (staging / "server.stdout").open("wb")
        stderr = (staging / "server.stderr").open("wb")
        server = subprocess.Popen(
            [str(binaries["server"])], env=environment, stdout=stdout, stderr=stderr
        )
        stdout.close()
        stderr.close()
        _wait_server_health(server_url, server)
        proof_dir = staging / "proof"
        proof_dir.mkdir(mode=0o700)
        adapter_environment = {
            **environment,
            "MEMPHANT_SCRATCH_ACTIVE": "1",
            "MEMPHANT_LME_SERVER_URL": server_url,
            "MEMPHANT_TEST_DATABASE_URL": source_url,
            "MEMPHANT_CLI_BIN": str(binaries["cli"]),
            "MEMPHANT_LME_SERVER_BIN": str(binaries["server"]),
            "MEMPHANT_LME_WORKER_BIN": str(binaries["worker"]),
            "MEMPHANT_LME_PROOF_DIR": str(proof_dir),
            "MEMPHANT_LME_RUN_ID": f"state-aware-bank-{question_id}",
            "MEMPHANT_LME_CONSTRUCTION_BINDING": str(binding_path),
        }
        adapter = _load_adapter()
        config = json.loads(
            (ROOT / "benchmarks/longmemeval_v2/memphant.fast.memory.json").read_text()
        )
        with _campaign_environment(adapter_environment):
            memory = adapter.MemphantMemory(config["memory_params"])
            for trajectory_id in trajectory_ids:
                memory.insert(trajectories[trajectory_id])
            construction_proof = memory.prepare()
        _atomic_write_json(staging / "CONSTRUCTION-PROOF.v2.json", construction_proof)
        _terminate_process(server)
        server = None
        (staging / "server.stdout").unlink(missing_ok=True)
        (staging / "server.stderr").unlink(missing_ok=True)
        _run_campaign_command(
            ["psql", source_url, "-v", "ON_ERROR_STOP=1", "-c", "DELETE FROM memphant.api_key"]
        )
        assert_case_source_quiescent(source_url)
        pg_dump_identity = postgres_tool_identity(source_url, "pg_dump")
        pg_restore_identity = postgres_tool_identity(source_url, "pg_restore")
        archive = staging / "bank.dump"
        _run_campaign_command(
            case_bank_dump_command(source_url, archive, pg_dump=pg_dump_identity["path"])
        )
        logical_inventory = database_logical_inventory(source_url)
        def public_tool_identity(identity: dict[str, object]) -> dict[str, object]:
            core = {
                "tool": identity["tool"],
                "executable": identity["tool"],
                "bytes": identity["bytes"],
                "sha256": identity["sha256"],
                "version": identity["version"],
                "server_version_num": identity["server_version_num"],
            }
            return {**core, "identity_sha256": sha256_json(core)}

        toolchain_core = {
            "identities": {
                "pg_dump": public_tool_identity(pg_dump_identity),
                "pg_restore": public_tool_identity(pg_restore_identity),
            }
        }
        toolchain = {
            **toolchain_core,
            "toolchain_sha256": sha256_json(toolchain_core),
        }
        trajectory_content = [trajectories[key] for key in trajectory_ids]
        materialization = {
            "trajectory_count": len(trajectory_ids),
            "trajectory_ids_sha256": sha256_json(trajectory_ids),
            "trajectory_content_sha256": sha256_json(trajectory_content),
        }
        binding_authority = {
            "authorization_path": authorization_path,
            "census_path": CANONICAL_CAMPAIGN_CENSUS,
            "manifest_path": CANONICAL_CAMPAIGN_MANIFEST,
            "wave_path": Path(campaign_artifact_paths()["construction_wave"]),
            "binding_root": Path(campaign_artifact_paths()["construction_bindings"]),
        }
        manifest = write_case_bank_manifest(
            archive=archive,
            output=staging / "manifest.json",
            contract=contract,
            binding_path=binding_path,
            binding_authority=binding_authority,
            construction_proof_path=staging / "CONSTRUCTION-PROOF.v2.json",
            materialization=materialization,
            logical_inventory=logical_inventory,
            postgres_toolchain=toolchain,
        )
        os.replace(staging, bank_dir)
        return manifest, bank_dir / "bank.dump", bank_dir / "CONSTRUCTION-PROOF.v2.json"
    finally:
        if server is not None:
            _terminate_process(server)
        _run_campaign_command(
            ["dropdb", f"--maintenance-db={base_database_url}", "--if-exists", "--force", source_name],
            run_command=lambda command, **kwargs: subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, check=False
            ),
        )


def restore_case_bank_pair(
    *,
    base_database_url: str,
    question_id: str,
    archive: Path,
    manifest: dict[str, object],
    run_command=subprocess.run,
    pg_restore: str = "pg_restore",
) -> dict[str, object]:
    """Restore an immutable data bank into independent Fast and Deep DBs."""
    contract = scratch_case_database_contract(base_database_url, question_id)
    manifest_core = {
        key: value for key, value in manifest.items() if key != "case_bank_sha256"
    }
    archive_proof = manifest.get("archive")
    if (
        manifest.get("case_bank_sha256") != sha256_json(manifest_core)
        or manifest.get("contract") != contract
        or not isinstance(archive_proof, dict)
        or archive_proof.get("format") != "pg_dump-custom-data-only-v1"
        or not archive.is_file()
        or archive_proof.get("bytes") != archive.stat().st_size
        or archive_proof.get("sha256") != _sha256_file(archive)
        or manifest.get("logical_inventory_sha256")
        != sha256_json(manifest.get("logical_inventory"))
        or not isinstance(manifest.get("postgres_toolchain"), dict)
        or manifest["postgres_toolchain"].get("toolchain_sha256")
        != sha256_json(
            {
                key: value
                for key, value in manifest["postgres_toolchain"].items()
                if key != "toolchain_sha256"
            }
        )
        or _contains_oracle_key(manifest)
    ):
        raise RuntimeError("case bank archive or manifest identity drift")
    restore_identity = manifest["postgres_toolchain"].get("identities", {}).get(
        "pg_restore"
    )
    resolved_restore = Path(shutil.which(pg_restore) or pg_restore).resolve()
    if (
        not isinstance(restore_identity, dict)
        or not resolved_restore.is_file()
        or restore_identity.get("executable") != "pg_restore"
        or resolved_restore.stat().st_size != restore_identity.get("bytes")
        or _sha256_file(resolved_restore) != restore_identity.get("sha256")
    ):
        raise RuntimeError("case bank pg_restore binary identity drift")
    restored: dict[str, str] = {}
    created_databases: list[str] = []
    try:
        for arm in ("fast", "deep"):
            database_name = contract["databases"][arm]
            database_url = _database_url_for_name(
                base_database_url, database_name
            )
            _run_campaign_command(
                [
                    "dropdb",
                    f"--maintenance-db={base_database_url}",
                    "--if-exists",
                    "--force",
                    database_name,
                ],
                run_command=run_command,
            )
            created_databases.append(database_name)
            _run_campaign_command(
                ["createdb", f"--maintenance-db={base_database_url}", database_name],
                run_command=run_command,
            )
            _run_campaign_command(
                [
                    sys.executable,
                    str(ROOT / "scripts/apply_memphant_migrations.py"),
                    "--database-url",
                    database_url,
                ],
                run_command=run_command,
            )
            _run_campaign_command(
                [
                    str(resolved_restore),
                    "--exit-on-error",
                    "--single-transaction",
                    "--data-only",
                    f"--dbname={database_url}",
                    str(archive.resolve()),
                ],
                run_command=run_command,
            )
            restored_inventory = database_logical_inventory(
                database_url, run_command=run_command
            )
            if restored_inventory != manifest["logical_inventory"]:
                raise RuntimeError("restored case bank logical inventory drift")
            restored[arm] = database_name
    except BaseException:
        for database_name in created_databases:
            run_command(
                [
                    "dropdb",
                    f"--maintenance-db={base_database_url}",
                    "--if-exists",
                    "--force",
                    database_name,
                ],
                cwd=ROOT,
                env={"PATH": os.environ.get("PATH", "")},
                capture_output=True,
                text=True,
                check=False,
            )
        raise
    core = {
        "schema_version": 1,
        "case_bank_sha256": manifest["case_bank_sha256"],
        "archive_sha256": archive_proof["sha256"],
        "databases": restored,
        "logical_inventory_sha256": manifest["logical_inventory_sha256"],
    }
    return {**core, "clone_sha256": sha256_json(core)}


def _binary_set_sha256(binaries: dict[str, Path]) -> str:
    return sha256_json(
        {
            name: {"bytes": path.stat().st_size, "sha256": _sha256_file(path)}
            for name, path in sorted(binaries.items())
        }
    )


def _private_output_authority(
    *,
    authorization_sha256: str,
    execution_plan: dict[str, object],
    reservation_plan: dict[str, object],
    case_manifest: dict[str, object],
    clone: dict[str, object],
    binaries: dict[str, Path],
    data_root: Path,
    official_dir: Path | None = None,
) -> dict[str, object]:
    pinned_official_dir = official_dir or data_root / "upstream"
    return {
        "authorization_sha256": authorization_sha256,
        "execution_plan_sha256": execution_plan["execution_plan_sha256"],
        "reservation_plan_sha256": reservation_plan["reservation_plan_sha256"],
        "case_bank_sha256": case_manifest["case_bank_sha256"],
        "clone_sha256": clone["clone_sha256"],
        "binary_set_sha256": _binary_set_sha256(binaries),
        "official_harness_sha256": _sha256_file(
            pinned_official_dir / "evaluation/harness.py"
        ),
        "official_scorer_sha256": _sha256_file(
            pinned_official_dir / "evaluation/qa_eval_metrics.py"
        ),
    }


def _one_question_inputs(
    data_root: Path, official_dir: Path, question_id: str, output: Path
) -> tuple[Path, Path, str]:
    question: dict[str, object] | None = None
    with (data_root / "questions.jsonl").open(encoding="utf-8") as source:
        for line in source:
            candidate = json.loads(line)
            if isinstance(candidate, dict) and candidate.get("id") == question_id:
                if question is not None:
                    raise RuntimeError("official question id is duplicated")
                question = candidate
    if question is None:
        raise RuntimeError("official one-question input is incomplete")
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    questions_path = output / "questions.private.json"
    haystack_path = output / "haystack.private.json"
    public_data_path = official_dir / "data/public_data.py"
    spec = importlib.util.spec_from_file_location(
        "memphant_pinned_lme_public_data", public_data_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("pinned official public-data materializer is unavailable")
    public_data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(public_data)
    domain = question.get("domain")
    if domain not in {"web", "enterprise"}:
        raise RuntimeError("official question domain is invalid")
    selected = public_data.materialize_runtime_questions(
        data_root=data_root,
        domain=domain,
        question_ids=[question_id],
        limit=None,
        output_path=questions_path,
    )
    public_data.materialize_runtime_haystack(
        data_root=data_root,
        tier="medium",
        selected_questions=selected,
        output_path=haystack_path,
    )
    if len(selected) != 1 or selected[0].get("id") != question_id:
        raise RuntimeError("official one-question materialization drift")
    raw_image = question.get("image")
    runtime_question = selected[0].get("question")
    if raw_image is not None:
        image = runtime_question.get("image") if isinstance(runtime_question, dict) else None
        image_path = Path(image) if isinstance(image, str) else None
        checksums = {
            relative: digest
            for digest, relative in (
                line.split(maxsplit=1)
                for line in (data_root / "checksums.sha256").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )
        }
        if (
            image_path is None
            or not image_path.is_absolute()
            or not image_path.is_file()
            or checksums.get(raw_image) != _sha256_file(image_path)
        ):
            raise RuntimeError("official question image materialization drift")
    elif not isinstance(runtime_question, str):
        raise RuntimeError("text-only official question materialization drift")
    questions_path.chmod(0o600)
    haystack_path.chmod(0o600)
    return questions_path, haystack_path, str(domain)


def execute_reader_arm(
    *,
    row: dict[str, object],
    clone_database_url: str,
    construction_proof_path: Path,
    row_state: RowExecutionStateMachine,
    private_root: Path,
    authority: dict[str, object],
    data_root: Path,
    official_dir: Path | None = None,
    binaries: dict[str, Path],
    openrouter_key: str,
) -> dict[str, object]:
    """Run the pinned official prompt/reader passes for one restored clone."""
    pinned_official_dir = official_dir or data_root / "upstream"
    sequence = row["sequence"]
    row_key = str(row["row_key"])
    question_id = str(row["question_id"])
    arm = str(row["arm"])
    row_dir = private_root.resolve() / f"{sequence:04d}"
    if row_dir.exists():
        reader_checkpoint = row_dir / "official/READER-OUTPUT.private.json"
        provider_checkpoint = row_dir / "reader-provider.private.json"
        relevant_components = ["reader"] if arm == "fast" else ["deep_recall", "reader"]
        statuses = {
            component: row_state.component_status(row_key, component)
            for component in relevant_components
        }
        if all(status == "pending" for status in statuses.values()) and not any(
            (row_dir / name).exists()
            for name in (
                "reader-provider.private.json",
                "deep-provider.private.json",
                "deep-attempts.private.jsonl",
            )
        ):
            # Directory creation precedes the first durable paid reservation.
            # A crash in that interval is safe to rebuild deterministically.
            shutil.rmtree(row_dir)
        else:
            deep_checkpoint = row_dir / "deep-provider.private.json"
            if (
                arm == "deep"
                and deep_checkpoint.is_file()
                and statuses["deep_recall"] == "started"
            ):
                reconcile_private_provider_output(
                    deep_checkpoint,
                    row_state=row_state,
                    row_key=row_key,
                    component="deep_recall",
                    authority=authority,
                )
                statuses["deep_recall"] = "result"
            if provider_checkpoint.is_file() and statuses["reader"] == "started":
                reconcile_private_provider_output(
                    provider_checkpoint,
                    row_state=row_state,
                    row_key=row_key,
                    component="reader",
                    authority=authority,
                )
                statuses["reader"] = "result"
            if reader_checkpoint.is_file() and statuses["reader"] == "result":
                return json.loads(reader_checkpoint.read_text(encoding="utf-8"))
            raise RuntimeError("incomplete private row checkpoint requires adjudication")
    row_dir.mkdir(parents=True, mode=0o700)
    inputs = row_dir / "inputs"
    questions_path, haystack_path, domain = _one_question_inputs(
        data_root, pinned_official_dir, question_id, inputs
    )
    proof_dir = row_dir / "memory-proofs"
    proof_dir.mkdir(mode=0o700)
    official_output = row_dir / "official"
    official_output.mkdir(mode=0o700)
    reader_provider_path = row_dir / "reader-provider.private.json"
    deep_provider_path = row_dir / "deep-provider.private.json"
    deep_journal_path = row_dir / "deep-attempts.private.jsonl"
    deep_proxy: _DeepRecallProxy | None = None
    deep_finalized = arm == "fast"
    deep_request_sha256 = sha256_json(
        {
            "row_key": row_key,
            "component": "deep_recall",
            "case_bank_sha256": authority["case_bank_sha256"],
            "clone_sha256": authority["clone_sha256"],
            "runtime_code_sha256": _sha256_file(
                ROOT / "crates/memphant-runtime/src/deep_recall_openrouter.rs"
            ),
        }
    )
    if arm == "deep":
        reserve_deep_recall(
            row_key=row_key,
            row_state=row_state,
            request_sha256=deep_request_sha256,
        )
        deep_proxy, deep_base_url = start_deep_recall_proxy(
            openrouter_key,
            journal_path=deep_journal_path,
            private_root=private_root,
            row_key=row_key,
            authority=authority,
        )
    else:
        deep_base_url = ""

    def finalize_deep() -> None:
        nonlocal deep_finalized
        if deep_finalized:
            return
        assert deep_proxy is not None
        receipt = deep_proxy.receipt(deep_request_sha256)
        settle_deep_recall(
            row_key=row_key,
            row_state=row_state,
            receipt=receipt,
            persist_private=lambda raw, ledger_receipt: persist_private_provider_output(
                deep_provider_path,
                private_root=private_root,
                row_key=row_key,
                component="deep_recall",
                response=raw,
                receipt=ledger_receipt,
                authority=authority,
            ),
        )
        deep_finalized = True

    reader_proxy, reader_base_url = start_reader_proxy(
        row_key=row_key,
        row_state=row_state,
        transport=lambda payload: openrouter_reader_transport(openrouter_key, payload),
        before_start=finalize_deep,
        persist_private=lambda raw, receipt: persist_private_provider_output(
                reader_provider_path,
                private_root=private_root,
                row_key=row_key,
                component="reader",
                response=raw,
                receipt=receipt,
                authority=authority,
            ),
    )
    port = _free_loopback_port()
    server_url = f"http://127.0.0.1:{port}"
    server_environment = {
        "PATH": os.environ.get("PATH", ""),
        "MEMPHANT_APP_DATABASE_URL": clone_database_url,
        "MEMPHANT_AUTHN_DATABASE_URL": clone_database_url,
        "MEMPHANT_BIND": f"127.0.0.1:{port}",
        "MEMPHANT_RESOURCE_CHUNKS": "on",
        "MEMPHANT_STRUCTURED_STATE": "off",
        "MEMPHANT_DEEP": "on" if arm == "deep" else "off",
    }
    if arm == "deep":
        server_environment.update(
            {
                "OPENROUTER_API_KEY": "loopback-route-bound",
                "MEMPHANT_DEEP_MODEL": "qwen/qwen3.5-9b-20260310",
                "MEMPHANT_DEEP_RESPONSE_MODEL": "qwen/qwen3.5-9b",
                "MEMPHANT_DEEP_PROMPT_PATH": str(ROOT / "config/deep-recall-v1.txt"),
                "MEMPHANT_DEEP_PROVIDERS": "deepinfra",
                "MEMPHANT_DEEP_INPUT_PRICE_MICROS_PER_MILLION": "100000",
                "MEMPHANT_DEEP_OUTPUT_PRICE_MICROS_PER_MILLION": "150000",
                "MEMPHANT_DEEP_OPENROUTER_BASE_URL": deep_base_url,
            }
        )
    stdout = stderr = None
    try:
        stdout = (row_dir / "server.stdout").open("wb")
        stderr = (row_dir / "server.stderr").open("wb")
        server = subprocess.Popen(
            [str(binaries["server"])],
            env=server_environment,
            stdout=stdout,
            stderr=stderr,
        )
    except BaseException:
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()
        reader_proxy.shutdown()
        reader_proxy.server_close()
        if deep_proxy is not None:
            deep_proxy.shutdown()
            deep_proxy.server_close()
        raise
    stdout.close()
    stderr.close()
    try:
        _wait_server_health(server_url, server)
        sys.path.insert(0, str(ROOT / "scripts"))
        import run_longmemeval_v2 as official_adapter

        memory_config = (
            ROOT
            / "benchmarks/longmemeval_v2"
            / ("memphant.memory.json" if arm == "deep" else "memphant.fast.memory.json")
        )
        command = official_adapter.memphant_harness_command(
            official_dir=pinned_official_dir,
            domain=domain,
            questions_path=questions_path,
            haystack_path=haystack_path,
            trajectories_path=data_root / "trajectories.jsonl",
            memory_config_path=memory_config,
            output_dir=official_output,
            reader_model="Qwen/Qwen3.5-9B",
            reader_base_url=reader_base_url,
            evaluator_model="gpt-5.2-2025-12-11",
            evaluator_base_url="http://127.0.0.1:1/v1",
        )
        deferred_proof = official_output / "DEFERRED-SCORING.json"
        command += [
            "--memphant-defer-scoring-proof", str(deferred_proof),
            "--api-key-env", "LME_READER_PROXY_KEY",
            "--evaluator-api-key-env", "LME_NO_JUDGE_KEY",
            "--prompt-build-max-workers", "1",
            "--reader-max-concurrent-requests", "1",
        ]
        child_environment = {
            **server_environment,
            "MEMPHANT_SCRATCH_ACTIVE": "1",
            "MEMPHANT_TEST_DATABASE_URL": clone_database_url,
            "MEMPHANT_LME_SERVER_URL": server_url,
            "MEMPHANT_CLI_BIN": str(binaries["cli"]),
            "MEMPHANT_LME_SERVER_BIN": str(binaries["server"]),
            "MEMPHANT_LME_WORKER_BIN": str(binaries["worker"]),
            "MEMPHANT_LME_PROOF_DIR": str(proof_dir),
            "MEMPHANT_LME_RUN_ID": row_key,
            "MEMPHANT_LME_PREBUILT_PROOF": str(construction_proof_path),
            "MEMPHANT_LME_PRIVATE_ROOT": str(private_root.resolve()),
            "LME_READER_PROXY_KEY": "loopback-route-bound",
            "LME_NO_JUDGE_KEY": "unused",
        }
        completed = subprocess.run(
            command,
            cwd=pinned_official_dir,
            env=child_environment,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("official deferred-reader harness failed")
        finalize_deep()
        proof = json.loads(deferred_proof.read_text(encoding="utf-8"))
        if (
            proof.get("status") != "READER_COMPLETE_SCORING_DEFERRED"
            or proof.get("question_id") != question_id
            or row_state.component_status(row_key, "reader") != "result"
        ):
            raise RuntimeError("official deferred-reader checkpoint is invalid")
        return json.loads(
            (official_output / "READER-OUTPUT.private.json").read_text(encoding="utf-8")
        )
    finally:
        _terminate_process(server)
        reader_proxy.shutdown()
        reader_proxy.server_close()
        if deep_proxy is not None:
            deep_proxy.shutdown()
            deep_proxy.server_close()


def seal_completed_reader_prefix(
    *,
    paths: dict[str, Path],
    execution_plan: dict[str, object],
    reservation_plan: dict[str, object],
    commitment: dict[str, object],
    passphrase_env: str = "MEMPHANT_LME_PREFIX_SEAL_PASSPHRASE",
) -> dict[str, object]:
    private_root = paths["private_reader_outputs"].resolve()
    prefix_row_dirs = [private_root / f"{sequence:04d}" for sequence in range(1, 25)]
    if paths["public_prefix_status"].is_file() and not paths["private_prefix"].is_file():
        # A crash after public-status fsync is safe to finish without rebuilding
        # from row dirs: decrypting validates the already-published ciphertext.
        with open_sealed_reader_prefix(
            paths=paths,
            execution_plan=execution_plan,
            reservation_plan=reservation_plan,
            passphrase_env=passphrase_env,
        ):
            pass
        for row_dir in prefix_row_dirs:
            if row_dir.is_dir() and not row_dir.is_symlink():
                shutil.rmtree(row_dir)
        return json.loads(paths["public_prefix_status"].read_text(encoding="utf-8"))
    if paths["private_prefix"].is_file():
        return seal_prefix(
            paths["private_prefix"],
            paths["sealed_prefix"],
            paths["public_prefix_status"],
            commitment["remaining_commitment_sha256"],
            passphrase_env,
            execution_plan_sha256=execution_plan["execution_plan_sha256"],
            reservation_plan_sha256=reservation_plan["reservation_plan_sha256"],
            plaintext_paths=[path for path in prefix_row_dirs if path.exists()],
        )
    cases = []
    cleanup: list[Path] = []
    for case_index in range(12):
        paired = reservation_plan["rows"][case_index * 2 : case_index * 2 + 2]
        case_rows = []
        question_id = paired[0]["question_id"]
        for row in paired:
            row_dir = private_root / f"{row['sequence']:04d}"
            official_path = row_dir / "official/READER-OUTPUT.private.json"
            provider_path = row_dir / "reader-provider.private.json"
            if not official_path.is_file() or not provider_path.is_file():
                raise RuntimeError("sealed prefix reader checkpoint is incomplete")
            official = json.loads(official_path.read_text(encoding="utf-8"))
            provider = json.loads(provider_path.read_text(encoding="utf-8"))
            response = provider.get("response", {}).get("response", {})
            choices = response.get("choices") if isinstance(response, dict) else None
            answer = (
                choices[0].get("message", {}).get("content")
                if isinstance(choices, list) and len(choices) == 1
                else None
            )
            if (
                official.get("question_id") != question_id
                or not isinstance(answer, str)
                or not answer
                or provider.get("status") != "PRIVATE_OUTPUT_FSYNCED"
            ):
                raise RuntimeError("sealed prefix private row identity drift")
            case_rows.append(
                {
                    "arm": row["arm"],
                    "row_key": row["row_key"],
                    "answer": answer,
                    "output_sha256": provider["private_output_sha256"],
                    "receipt_sha256": provider["receipt"]["result_sha256"],
                    "structurally_valid": True,
                    "receipt_valid": True,
                    "settled": True,
                    "official_row": official,
                    "provider_record": provider,
                    **(
                        {
                            "deep_provider_record": json.loads(
                                (row_dir / "deep-provider.private.json").read_text(
                                    encoding="utf-8"
                                )
                            )
                        }
                        if row["arm"] == "deep"
                        else {}
                    ),
                }
            )
            cleanup.append(row_dir)
        cases.append(
            {
                "sequence": case_index + 1,
                "question_id": question_id,
                "rows": case_rows,
            }
        )
    private = {
        "schema_version": 1,
        "execution_plan_sha256": execution_plan["execution_plan_sha256"],
        "reservation_plan_sha256": reservation_plan["reservation_plan_sha256"],
        "cases": cases,
    }
    _create_or_validate_json(paths["private_prefix"], private)
    paths["private_prefix"].chmod(0o600)
    return seal_prefix(
        paths["private_prefix"],
        paths["sealed_prefix"],
        paths["public_prefix_status"],
        commitment["remaining_commitment_sha256"],
        passphrase_env,
        execution_plan_sha256=execution_plan["execution_plan_sha256"],
        reservation_plan_sha256=reservation_plan["reservation_plan_sha256"],
        plaintext_paths=cleanup,
    )


@contextmanager
def open_sealed_reader_prefix(
    *,
    paths: dict[str, Path],
    execution_plan: dict[str, object],
    reservation_plan: dict[str, object],
    passphrase_env: str = "MEMPHANT_LME_PREFIX_SEAL_PASSPHRASE",
):
    """Decrypt the validated prefix into one 0600 temporary and erase it."""
    status = json.loads(paths["public_prefix_status"].read_text(encoding="utf-8"))
    validate_public_prefix_status(status)
    if (
        status["execution_plan_sha256"] != execution_plan["execution_plan_sha256"]
        or status["reservation_plan_sha256"]
        != reservation_plan["reservation_plan_sha256"]
        or status["sealed_blob_sha256"] != _sha256_file(paths["sealed_prefix"])
    ):
        raise RuntimeError("sealed reader prefix differs from execution authority")
    passphrase = os.environ.get(passphrase_env, "")
    openssl = shutil.which("openssl")
    if not passphrase or openssl is None:
        raise RuntimeError("sealed reader prefix cannot be opened")
    private_root = paths["private_reader_outputs"].resolve()
    private_root.mkdir(parents=True, exist_ok=True)
    private_root.chmod(0o700)
    with tempfile.NamedTemporaryFile(
        dir=private_root, prefix=".prefix-open-", delete=False
    ) as handle:
        plaintext = Path(handle.name)
    plaintext.chmod(0o600)
    try:
        completed = subprocess.run(
            [
                openssl, "enc", "-d", "-aes-256-cbc", "-pbkdf2",
                "-iter", "600000", "-in", str(paths["sealed_prefix"]),
                "-out", str(plaintext), "-pass", f"env:{passphrase_env}",
            ],
            env={passphrase_env: passphrase},
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("sealed reader prefix decryption failed")
        private = json.loads(plaintext.read_text(encoding="utf-8"))
        _derive_public_prefix_rows(
            private,
            execution_plan_sha256=execution_plan["execution_plan_sha256"],
            reservation_plan_sha256=reservation_plan["reservation_plan_sha256"],
        )
        yield private
    finally:
        plaintext.unlink(missing_ok=True)
        directory = os.open(private_root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _drop_case_clones(base_database_url: str, clone: dict[str, object]) -> None:
    databases = clone.get("databases") if isinstance(clone, dict) else None
    if not isinstance(databases, dict) or set(databases) != {"fast", "deep"}:
        raise RuntimeError("case clone cleanup authority is invalid")
    for arm in ("fast", "deep"):
        database_name = databases[arm]
        _database_url_for_name(base_database_url, database_name)
        _run_campaign_command(
            [
                "dropdb", f"--maintenance-db={base_database_url}",
                "--if-exists", "--force", database_name,
            ]
        )


def execute_reader_case(
    *,
    question_id: str,
    rows: list[dict[str, object]],
    authorization_path: Path,
    census: dict[str, object],
    data_root: Path,
    official_dir: Path,
    base_database_url: str,
    bank_root: Path,
    row_state: RowExecutionStateMachine,
    private_root: Path,
    binaries: dict[str, Path],
    openrouter_key: str,
    ensure_bank=ensure_case_bank,
    restore_pair=restore_case_bank_pair,
    execute_arm=execute_reader_arm,
    drop_pair=_drop_case_clones,
) -> list[dict[str, object]]:
    """Build/restore one bank and settle its Fast and Deep readers."""
    if (
        len(rows) != 2
        or {row.get("arm") for row in rows} != {"fast", "deep"}
        or any(row.get("question_id") != question_id for row in rows)
    ):
        raise RuntimeError("reader case pair differs from execution plan")
    manifest, archive, construction_proof = ensure_bank(
        authorization_path=authorization_path,
        census=census,
        data_root=data_root,
        base_database_url=base_database_url,
        question_id=question_id,
        bank_root=bank_root,
    )
    clone = restore_pair(
        base_database_url=base_database_url,
        question_id=question_id,
        archive=archive,
        manifest=manifest,
    )
    authority = _private_output_authority(
        authorization_sha256=json.loads(
            authorization_path.read_text(encoding="utf-8")
        )["authorization"]["authorization_scope_sha256"],
        execution_plan={
            "execution_plan_sha256": row_state.reservation_plan[
                "execution_plan_sha256"
            ]
        },
        reservation_plan=row_state.reservation_plan,
        case_manifest=manifest,
        clone=clone,
        binaries=binaries,
        data_root=data_root,
        official_dir=official_dir,
    )
    outputs = []
    try:
        for row in sorted(rows, key=lambda item: int(item["sequence"])):
            database_name = clone["databases"][row["arm"]]
            output = execute_arm(
                row=row,
                clone_database_url=_database_url_for_name(
                    base_database_url, database_name
                ),
                construction_proof_path=construction_proof,
                row_state=row_state,
                private_root=private_root,
                authority=authority,
                data_root=data_root,
                official_dir=official_dir,
                binaries=binaries,
                openrouter_key=openrouter_key,
            )
            outputs.append(output)
        return outputs
    finally:
        drop_pair(base_database_url, clone)


def execute_reader_wave(
    *,
    rows: list[dict[str, object]],
    case_count: int,
    execute_case,
    max_workers: int = 1,
) -> list[dict[str, object]]:
    """Execute exact adjacent Fast/Deep pairs in frozen question order."""
    if len(rows) != case_count * 2:
        raise RuntimeError("reader wave row count differs from admitted cases")
    if type(max_workers) is not int or not 1 <= max_workers <= 8:
        raise RuntimeError("reader wave worker count is outside frozen authority")
    pairs: list[tuple[str, list[dict[str, object]]]] = []
    first_sequence = rows[0].get("sequence") if rows else None
    if type(first_sequence) is not int:
        raise RuntimeError("reader wave sequence drift")
    for index in range(case_count):
        pair = rows[index * 2 : index * 2 + 2]
        if pair[0].get("sequence") != first_sequence + index * 2:
            raise RuntimeError("reader wave sequence drift")
        question_id = pair[0].get("question_id")
        if (
            not isinstance(question_id, str)
            or pair[1].get("question_id") != question_id
        ):
            raise RuntimeError("reader wave pair identity drift")
        pairs.append((question_id, pair))
    if max_workers == 1:
        return [
            output
            for question_id, pair in pairs
            for output in execute_case(question_id, pair)
        ]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(execute_case, question_id, pair)
            for question_id, pair in pairs
        ]
        try:
            # Consume in frozen pair order even though independent cases run in
            # parallel; this keeps downstream private aggregation deterministic.
            return [output for future in futures for output in future.result()]
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def _load_official_harness(official_dir: Path, authority: dict[str, object]):
    harness_path = official_dir / "evaluation/harness.py"
    scorer_path = official_dir / "evaluation/qa_eval_metrics.py"
    if (
        _sha256_file(harness_path) != authority["official_harness_sha256"]
        or _sha256_file(scorer_path) != authority["official_scorer_sha256"]
    ):
        raise RuntimeError("official scoring code differs from reader authority")
    sys.path.insert(0, str(official_dir))
    name = "memphant_pinned_lme_v2_harness"
    spec = importlib.util.spec_from_file_location(name, harness_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("official scoring harness cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reader_records_from_private(
    *,
    reservation_plan: dict[str, object],
    prefix: dict[str, object],
    private_root: Path,
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for case in prefix["cases"]:
        for row in case["rows"]:
            records[row["row_key"]] = row
    for row in reservation_plan["rows"][24:]:
        row_dir = private_root / f"{row['sequence']:04d}"
        official = json.loads(
            (row_dir / "official/READER-OUTPUT.private.json").read_text(
                encoding="utf-8"
            )
        )
        provider = json.loads(
            (row_dir / "reader-provider.private.json").read_text(encoding="utf-8")
        )
        record = {
            "arm": row["arm"],
            "row_key": row["row_key"],
            "official_row": official,
            "provider_record": provider,
        }
        if row["arm"] == "deep":
            record["deep_provider_record"] = json.loads(
                (row_dir / "deep-provider.private.json").read_text(encoding="utf-8")
            )
        records[row["row_key"]] = record
    if set(records) != {row["row_key"] for row in reservation_plan["rows"]}:
        raise RuntimeError("private reader record inventory is incomplete")
    return records


def _native_judge_eval_config(evaluator_base_url: str) -> dict[str, object]:
    return {
        "evaluator_model": "gpt-5.2-2025-12-11",
        "evaluator_base_url": evaluator_base_url,
        "evaluator_api_key": "loopback-route-bound",
        "evaluator_reasoning_effort": "medium",
        "evaluator_max_completion_tokens": 2048,
        "evaluator_timeout_seconds": 43_200.0,
    }


def score_official_reader_row(
    *,
    planned_row: dict[str, object],
    private_row: dict[str, object],
    row_state: RowExecutionStateMachine,
    authority: dict[str, object],
    judge_root: Path,
    openai_key: str,
    score_prediction,
    judge_transport=openai_judge_transport,
) -> dict[str, object]:
    """Run the pinned deterministic scorer or one native paid judge."""
    row_key = str(planned_row["row_key"])
    official_row = private_row.get("official_row")
    if not isinstance(official_row, dict):
        raise RuntimeError("official private reader row is unavailable")
    row_dir = judge_root.resolve() / f"{int(planned_row['sequence']):04d}"
    row_dir.mkdir(parents=True, exist_ok=True)
    row_dir.chmod(0o700)
    checkpoint = row_dir / "SCORE.private.json"
    if checkpoint.is_file():
        score = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (
            score.get("row_key") != row_key
            or score.get("official_row_sha256") != sha256_json(official_row)
        ):
            raise RuntimeError("official score checkpoint drift")
        return score
    eval_config: dict[str, object] = {}
    proxy: _MeteredChatProxy | None = None
    provider_path = row_dir / "judge-provider.private.json"
    if planned_row["native_judge_required"]:
        status = row_state.component_status(row_key, "judge")
        if provider_path.is_file() and status == "started":
            reconcile_private_provider_output(
                provider_path,
                row_state=row_state,
                row_key=row_key,
                component="judge",
                authority=authority,
            )
            status = "result"
        if status == "result":
            if not provider_path.is_file():
                raise RuntimeError("settled judge lacks private provider checkpoint")
            persisted = json.loads(provider_path.read_text(encoding="utf-8"))
            cached_response = persisted["response"]
            expected_request_sha256 = persisted["receipt"]["request_sha256"]

            def replay(payload):
                if sha256_json(payload) != expected_request_sha256:
                    raise RuntimeError("resumed official judge request drift")
                return cached_response

            proxy, evaluator_base_url = _start_metered_proxy(replay)
        elif status == "pending":
            proxy, evaluator_base_url = start_judge_proxy(
                row_key=row_key,
                row_state=row_state,
                transport=lambda payload: judge_transport(openai_key, payload),
                persist_private=lambda raw, receipt: persist_private_provider_output(
                    provider_path,
                    private_root=judge_root,
                    row_key=row_key,
                    component="judge",
                    response=raw,
                    receipt=receipt,
                    authority=authority,
                ),
            )
        else:
            raise RuntimeError("native judge has unresolved terminal state")
        eval_config = _native_judge_eval_config(evaluator_base_url)
    try:
        score_bool, eval_name, is_unknown = score_prediction(
            official_row, eval_config
        )
    finally:
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
    core = {
        "schema_version": 1,
        "row_key": row_key,
        "official_row_sha256": sha256_json(official_row),
        "score_bool": bool(score_bool),
        "score": int(bool(score_bool)),
        "eval_name": eval_name,
        "is_unknown": bool(is_unknown),
        "native_judge_required": planned_row["native_judge_required"],
        "native_judge_settled": (
            not planned_row["native_judge_required"]
            or row_state.component_status(row_key, "judge") == "result"
        ),
    }
    score = {**core, "score_sha256": sha256_json(core)}
    _atomically_create_json(checkpoint, score)
    checkpoint.chmod(0o600)
    return score


def score_all_official_rows(
    *,
    reservation_plan: dict[str, object],
    private_rows: dict[str, dict[str, object]],
    row_state: RowExecutionStateMachine,
    authority_for_row,
    judge_root: Path,
    openai_key: str,
    data_root: Path,
    official_dir: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Score exactly 902 rows only after the reader barrier is complete."""
    if any(
        row_state.component_status(row["row_key"], "reader") != "result"
        for row in reservation_plan["rows"]
    ):
        raise RuntimeError("official scoring requires all 902 reader results")
    first_authority = authority_for_row(reservation_plan["rows"][0])
    del data_root
    harness = _load_official_harness(official_dir, first_authority)
    scored = []
    for row in reservation_plan["rows"]:
        score = score_official_reader_row(
            planned_row=row,
            private_row=private_rows[row["row_key"]],
            row_state=row_state,
            authority=authority_for_row(row),
            judge_root=judge_root,
            openai_key=openai_key,
            score_prediction=harness.score_prediction,
        )
        official = dict(private_rows[row["row_key"]]["official_row"])
        official.update(
            {
                "score": score["score"],
                "score_bool": score["score_bool"],
                "is_unknown": score["is_unknown"],
            }
        )
        scored.append({"planned": row, "score": score, "official": official})
    return scored, _aggregate_scored_official_rows(scored, harness)


def _aggregate_scored_official_rows(scored, harness) -> dict[str, object]:
    """Recompute both official arm aggregates from checked per-row scores."""
    metrics = {}
    for arm in ("fast", "deep"):
        records = [
            item["official"] for item in scored if item["planned"]["arm"] == arm
        ]
        durations = [float(row["memory_query_duration_seconds"]) for row in records]
        if len(durations) != QUESTION_COUNT or any(
            not value >= 0 or not Decimal(str(value)).is_finite()
            for value in durations
        ):
            raise RuntimeError("official memory-query latency inventory is invalid")
        ordered = sorted(durations)
        aggregate = harness.aggregate_metrics(records)
        official_memory_query = {
            "avg_seconds": sum(durations) / len(durations),
            "p50_seconds": ordered[len(ordered) // 2],
            "p95_seconds": ordered[
                min(len(ordered) - 1, int(0.95 * len(ordered)))
            ],
            "max_seconds": ordered[-1],
            "total_seconds": sum(durations),
        }
        if aggregate.get("memory_query") not in (None, official_memory_query):
            raise RuntimeError("official memory-query aggregate drift")
        aggregate["memory_query"] = official_memory_query
        metrics[arm] = aggregate
    return metrics


def official_lafs_summary(
    official_dir: Path, arm_metrics: dict[str, object]
) -> dict[str, object]:
    path = official_dir / "leaderboard/compute_lafs.py"
    spec = importlib.util.spec_from_file_location("memphant_pinned_lme_lafs", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("pinned official LAFS implementation is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    points = []
    for arm in ("fast", "deep"):
        metrics = arm_metrics.get(arm)
        try:
            accuracy = float(metrics["overall"]["overall_full_set"]) * 100
            latency = float(metrics["memory_query"]["avg_seconds"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("official LAFS operating point is incomplete") from error
        if latency <= 0:
            raise RuntimeError("official LAFS latency must be positive")
        points.append(
            module.Point(
                name=f"MemPhant state-aware {arm}",
                acc=accuracy,
                latency=latency,
            )
        )
    summary = module.lafs_summary_for_submission("medium", points)
    core = {
        "schema_version": 1,
        "compute_lafs_sha256": _sha256_file(path),
        "summary": summary,
    }
    return {**core, "lafs_proof_sha256": sha256_json(core)}


def _materialize_reader_shapes(
    data_root: Path, output: Path, *, official_dir: Path | None = None
) -> dict[str, object]:
    pinned_official_dir = official_dir or data_root / "upstream"
    prompts = _literal_assignment(
        pinned_official_dir / "evaluation/harness.py", "DOMAIN_SYSTEM_PROMPTS"
    )
    if (
        not isinstance(prompts, dict)
        or not prompts
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in prompts.items())
    ):
        raise RuntimeError("pinned official reader system prompts are invalid")
    question_ids: set[str] = set()
    checksum_path = data_root / "checksums.sha256"
    if not checksum_path.is_file():
        raise RuntimeError("official question screenshot checksum manifest is missing")
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if (
            len(parts) != 2
            or SHA256.fullmatch(parts[0]) is None
            or not parts[1].strip()
            or parts[1].strip() in checksums
        ):
            raise RuntimeError(
                f"official checksum manifest line {line_number} is malformed"
            )
        checksums[parts[1].strip()] = parts[0]
    rows = 0
    image_rows = 0
    image_inventory: list[dict[str, object]] = []
    native_judge_bindings: list[dict[str, str]] = []
    with (data_root / "questions.jsonl").open(encoding="utf-8") as source, output.open(
        "w", encoding="utf-8"
    ) as target:
        for line_number, line in enumerate(source, 1):
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise RuntimeError(f"question line {line_number} is not an object")
            question_id = raw.get("id")
            domain = raw.get("domain")
            question = raw.get("question")
            image = raw.get("image")
            evaluation = raw.get("eval_function")
            if (
                not isinstance(question_id, str)
                or not question_id
                or question_id in question_ids
                or domain not in prompts
                or not isinstance(question, str)
                or not question.strip()
                or (image is not None and (not isinstance(image, str) or not image.strip()))
            ):
                raise RuntimeError(f"question line {line_number} has an invalid reader shape")
            if not isinstance(evaluation, str) or not evaluation:
                raise RuntimeError("official question evaluation identity is missing")
            evaluation_name = evaluation.split("|", 1)[0]
            if evaluation_name in {
                "llm_abstention_checker",
                "llm_gotchas_checker",
            }:
                native_judge_bindings.append(
                    {"question_id": question_id, "evaluation": evaluation_name}
                )
            question_ids.add(question_id)
            question_image = None
            if image is not None:
                relative = Path(image)
                if (
                    relative.is_absolute()
                    or relative.parts[:1] != ("question_screenshots",)
                    or ".." in relative.parts
                ):
                    raise RuntimeError("official question screenshot path is invalid")
                image_path = (data_root / relative).resolve()
                if not image_path.is_relative_to(data_root.resolve()):
                    raise RuntimeError("official question screenshot escapes data root")
                if not image_path.is_file():
                    raise RuntimeError("question screenshot is missing")
                expected_sha256 = checksums.get(image)
                if expected_sha256 is None or _sha256_file(image_path) != expected_sha256:
                    raise RuntimeError("question screenshot checksum drift")
                width, height = _png_dimensions(image_path)
                question_image = {
                    "path": image,
                    "sha256": expected_sha256,
                    "bytes": image_path.stat().st_size,
                    "width": width,
                    "height": height,
                    "mime_type": "image/png",
                }
                image_inventory.append(
                    {"question_id": question_id, "question_image": question_image}
                )
            has_image = question_image is not None
            image_rows += int(has_image)
            target.write(
                canonical_json(
                    {
                        "question_id": question_id,
                        "system_prompt": prompts[domain],
                        "question_text": question,
                        "question_image": question_image,
                    }
                ).decode("utf-8")
                + "\n"
            )
            rows += 1
    if rows != QUESTION_COUNT:
        raise RuntimeError("official questions file must contain exactly 451 reader shapes")
    if image_rows != 29:
        raise RuntimeError("official questions file must bind exactly 29 screenshots")
    native_judge_bindings.sort(key=lambda row: row["question_id"])
    native_judge_ids = [row["question_id"] for row in native_judge_bindings]
    if len(native_judge_ids) != 156 or len(set(native_judge_ids)) != 156:
        raise RuntimeError("official native-judge subset drift")
    return {
        "fixture_sha256": _sha256_file(output),
        "rows": rows,
        "image_rows": image_rows,
        "image_manifest_sha256": _sha256_file(checksum_path),
        "image_inventory_sha256": sha256_json(image_inventory),
        "image_inventory": image_inventory,
        "question_source_sha256": _sha256_file(data_root / "questions.jsonl"),
        "question_ids_sha256": sha256_json(sorted(question_ids)),
        "native_judge_rows": len(native_judge_ids),
        "native_judge_question_ids": native_judge_ids,
        "native_judge_question_ids_sha256": sha256_json(native_judge_ids),
        "native_judge_contract_sha256": sha256_json(native_judge_bindings),
    }


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if (
        len(header) != 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        raise RuntimeError("question screenshot is not a PNG with an IHDR")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise RuntimeError("question screenshot dimensions are invalid")
    return width, height


def _validated_reader_processor_proof(
    proof: dict[str, object], expected: dict[str, object]
) -> list[dict[str, object]]:
    bindings = {
        "reader_shape_fixture_sha256": "fixture_sha256",
        "reader_shape_rows": "rows",
        "reader_shape_image_inventory_sha256": "image_inventory_sha256",
        "reader_shape_image_manifest_sha256": "image_manifest_sha256",
        "reader_tokenizer_sha256": "tokenizer_sha256",
        "reader_chat_template_sha256": "chat_template_sha256",
        "reader_preprocessor_config_sha256": "preprocessor_config_sha256",
        "reader_processor_source_sha256": "processor_source_sha256",
        "reader_image_processor_source_sha256": "image_processor_source_sha256",
        "reader_processor_toolchain_sha256": "processor_toolchain_sha256",
    }
    if any(proof.get(actual) != expected.get(bound) for actual, bound in bindings.items()):
        raise RuntimeError("reader processor proof drift")
    core = {key: value for key, value in proof.items() if key != "proof_sha256"}
    if proof.get("proof_sha256") is not None and proof.get("proof_sha256") != sha256_json(core):
        raise RuntimeError("reader processor proof drift")
    rows = proof.get("rows")
    if not isinstance(rows, list) or len(rows) != QUESTION_COUNT:
        raise RuntimeError("reader processor proof drift")
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("reader processor proof drift")
        question_id = row.get("question_id")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in ids
            or type(row.get("has_image")) is not bool
            or type(row.get("local_processor_input_tokens")) is not int
            or row["local_processor_input_tokens"] <= 0
        ):
            raise RuntimeError("reader processor proof drift")
        ids.add(question_id)
    if (
        sum(int(row["has_image"]) for row in rows) != 29
        or proof.get("reader_row_token_inventory_sha256") != sha256_json(rows)
        or proof.get("reader_local_processor_maximum_input_tokens")
        != max(row["local_processor_input_tokens"] for row in rows)
        or proof.get("paid_models_run") not in (None, False)
        or proof.get("spend_nanos") not in (None, 0)
    ):
        raise RuntimeError("reader processor proof drift")
    return rows


def _ceil_cost(tokens: int, price_nanos_per_million: int) -> int:
    if type(tokens) is not int or tokens <= 0 or type(price_nanos_per_million) is not int or price_nanos_per_million <= 0:
        raise RuntimeError("token and price maxima must be positive integers")
    return (tokens * price_nanos_per_million + 999_999) // 1_000_000


def _literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if name in names:
                return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise RuntimeError(f"pinned upstream assignment is missing: {name}")


def _judge_messages(kind: str) -> list[dict[str, str]]:
    if kind == "abstention":
        system = ABSTENTION_JUDGE_SYSTEM_PROMPT
        user = (
            "Evaluate whether the model correctly handled a flawed-premise question.\n\n"
            "Question:\n\n\nReference answer (the expected flaw-aware conclusion):\n\n\n"
            "Model full response:\n\n\nModel extracted final answer:\n\n\nScoring rubric:\n"
            "- Label 1 only if the model clearly identifies the flawed premise and reaches a conclusion equivalent to the reference answer.\n"
            "- Label 1 also if the model explicitly says it lacks access to the user's specific live environment/instance/configuration and therefore cannot verify, provided it does not give a concrete premise-following answer.\n"
            "- Label 0 if the model follows the flawed premise and gives a concrete answer under that premise.\n"
            "- Label 0 for generic UNKNOWN/insufficient-info replies that do not identify a flaw and do not make the explicit environment-access limitation clear.\n"
            "- Label 0 if contradictory.\n\nOutput JSON only:\n"
            '{"label": 0 or 1, "reason": "short rationale"}'
        )
    elif kind == "gotchas":
        system = GOTCHAS_JUDGE_SYSTEM_PROMPT
        user = (
            "Evaluate whether the model answer captures the gotcha insight.\n\nQuestion:\n\n\n"
            "Reference answer (insight points):\n\n\nModel full response:\n\n\n"
            "Model extracted final answer:\n\n\nScoring rubric:\n"
            "- Label 1 if the model includes at least one correct insight point from the reference answer (paraphrase acceptable), and does not contradict any reference point.\n"
            "- Label 1 even if only part of a multi-point reference answer is covered, as long as there is no contradiction.\n"
            "- Label 0 if direction is wrong (suggests opposite action/cause), even if some wording overlaps.\n"
            "- Label 0 if any point in the model response contradicts any reference point.\n"
            "- Label 0 if the response is irrelevant or generic without insight.\n\nOutput JSON only:\n"
            '{"label": 0 or 1, "reason": "short rationale"}'
        )
    else:
        raise AssertionError(kind)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _official_request_shape_bounds(
    data_root: Path,
    reader_proof: dict[str, object],
    reader_fixture: dict[str, object],
    reader_config: dict[str, object],
    *,
    official_dir: Path | None = None,
) -> dict[str, object]:
    pinned_official_dir = official_dir or data_root / "upstream"
    harness_path = pinned_official_dir / "evaluation/harness.py"
    qa_path = pinned_official_dir / "evaluation/qa_eval_metrics.py"
    prompts = _literal_assignment(harness_path, "DOMAIN_SYSTEM_PROMPTS")
    if not isinstance(prompts, dict) or not prompts or not all(isinstance(value, str) for value in prompts.values()):
        raise RuntimeError("pinned official reader system prompts are invalid")
    if _literal_assignment(qa_path, "_ABSTENTION_JUDGE_SYSTEM_PROMPT") != ABSTENTION_JUDGE_SYSTEM_PROMPT or _literal_assignment(qa_path, "_GOTCHAS_JUDGE_SYSTEM_PROMPT") != GOTCHAS_JUDGE_SYSTEM_PROMPT:
        raise RuntimeError("pinned official judge request shape drift")
    question_path = data_root / "questions.jsonl"
    maximum_question_record_bytes = 0
    question_rows = 0
    with question_path.open("rb") as handle:
        for raw in handle:
            question_rows += 1
            maximum_question_record_bytes = max(maximum_question_record_bytes, len(raw.rstrip(b"\r\n")))
    if question_rows != QUESTION_COUNT:
        raise RuntimeError("official questions file must contain exactly 451 opaque rows")
    reader_empty_shapes = []
    for system in prompts.values():
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "### Memory context:\n"},
                    {"type": "text", "text": "\n\n### Question to answer:\n"},
                ],
            },
        ]
        reader_empty_shapes.append(len(canonical_json(messages)))
    judge_fixed = max(len(canonical_json(_judge_messages(kind))) for kind in ("abstention", "gotchas"))
    processor_rows = _validated_reader_processor_proof(
        reader_proof,
        {
            "fixture_sha256": reader_fixture["fixture_sha256"],
            "rows": QUESTION_COUNT,
            "image_inventory_sha256": reader_fixture["image_inventory_sha256"],
            "image_manifest_sha256": reader_fixture["image_manifest_sha256"],
            "tokenizer_sha256": reader_config.get("tokenizer_sha256"),
            "chat_template_sha256": reader_config.get("chat_template_sha256"),
            "preprocessor_config_sha256": reader_config.get(
                "preprocessor_config_sha256"
            ),
            "processor_source_sha256": reader_config.get(
                "processor_source_sha256"
            ),
            "image_processor_source_sha256": reader_config.get(
                "image_processor_source_sha256"
            ),
            "processor_toolchain_sha256": reader_config.get(
                "processor_toolchain_sha256"
            ),
        },
    )
    return {
        "question_rows": question_rows,
        "maximum_question_record_bytes": maximum_question_record_bytes,
        # Raw JSON-line bytes conservatively bind all escaped question fields
        # without decoding or selecting target/oracle fields.
        "reader_maximum_nonmemory_serialized_bytes": max(reader_empty_shapes) + maximum_question_record_bytes,
        "reader_shape_fixture_sha256": reader_fixture["fixture_sha256"],
        "reader_shape_question_source_sha256": reader_fixture[
            "question_source_sha256"
        ],
        "reader_shape_question_ids_sha256": reader_fixture["question_ids_sha256"],
        "native_judge_rows": reader_fixture["native_judge_rows"],
        "native_judge_question_ids": reader_fixture[
            "native_judge_question_ids"
        ],
        "native_judge_question_ids_sha256": reader_fixture[
            "native_judge_question_ids_sha256"
        ],
        "native_judge_contract_sha256": reader_fixture[
            "native_judge_contract_sha256"
        ],
        "reader_shape_rows": reader_fixture["rows"],
        "reader_shape_image_rows": reader_fixture["image_rows"],
        "reader_shape_image_manifest_sha256": reader_fixture[
            "image_manifest_sha256"
        ],
        "reader_shape_image_inventory_sha256": reader_fixture[
            "image_inventory_sha256"
        ],
        "reader_shape_image_inventory": reader_fixture["image_inventory"],
        "reader_tokenizer_sha256": reader_config.get("tokenizer_sha256"),
        "reader_chat_template_sha256": reader_config.get("chat_template_sha256"),
        "reader_preprocessor_config_sha256": reader_config.get(
            "preprocessor_config_sha256"
        ),
        "reader_processor_source_sha256": reader_config.get(
            "processor_source_sha256"
        ),
        "reader_image_processor_source_sha256": reader_config.get(
            "image_processor_source_sha256"
        ),
        "reader_processor_toolchain_sha256": reader_config.get(
            "processor_toolchain_sha256"
        ),
        "reader_local_processor_maximum_input_tokens": reader_proof[
            "reader_local_processor_maximum_input_tokens"
        ],
        "reader_row_token_inventory_sha256": reader_proof[
            "reader_row_token_inventory_sha256"
        ],
        "reader_processor_rows": processor_rows,
        # Exact canonical official judge messages across the 156 rows whose
        # eval functions invoke the native LLM judge, with response fields empty.
        "judge_maximum_fixed_serialized_bytes": 2_412,
        "llm_judged_rows": 156,
    }


def _judge_liability(judge: dict[str, object]) -> int:
    required_strings = ("model", "provider", "pricing_source", "pricing_sha256")
    if not all(
        isinstance(judge.get(key), str) and judge[key] for key in required_strings
    ):
        raise RuntimeError("judge identity or pricing source is missing")
    attempts = judge.get("maximum_attempts")
    fixed = judge.get("maximum_fixed_serialized_bytes")
    insertions = judge.get("reader_response_insertions")
    response = judge.get("reader_maximum_output_tokens")
    if (
        type(attempts) is not int
        or attempts <= 0
        or not all(
            type(part) is int and part > 0
            for part in (fixed, insertions, response)
        )
    ):
        raise RuntimeError("judge official request-shape primitives are missing")
    derived_input = fixed + insertions * response
    if judge.get("maximum_input_reservation_units") != derived_input:
        raise RuntimeError("judge request maximum drift")
    return attempts * (
        _ceil_cost(derived_input, judge.get("input_price_nanos_per_million"))
        + _ceil_cost(
            judge.get("maximum_output_tokens"),
            judge.get("output_price_nanos_per_million"),
        )
    )


def _reader_liability_inventory(
    processor_rows: list[dict[str, object]],
    reader: dict[str, object],
    judge: dict[str, object],
    *,
    native_judge_question_ids: set[str],
) -> dict[str, object]:
    required_strings = (
        "model",
        "provider",
        "pricing_source",
        "pricing_sha256",
        "provider_prompt_ceiling_source",
        "provider_prompt_ceiling_source_sha256",
    )
    if not all(
        isinstance(reader.get(key), str) and reader[key] for key in required_strings
    ):
        raise RuntimeError("reader identity, pricing, or prompt ceiling source is missing")
    attempts = reader.get("maximum_attempts")
    memory_context = reader.get("memory_context_max_tokens")
    prompt_ceiling = reader.get("multimodal_provider_prompt_ceiling_tokens")
    if (
        type(attempts) is not int
        or attempts != 1
        or memory_context != 200_000
        or prompt_ceiling != 262_144
    ):
        raise RuntimeError("provider prompt ceiling drift")
    if not isinstance(processor_rows, list) or not processor_rows:
        raise RuntimeError("reader row token inventory is missing")
    processor_question_ids = {
        row.get("question_id") for row in processor_rows if isinstance(row, dict)
    }
    if (
        not isinstance(native_judge_question_ids, set)
        or not all(
            isinstance(question_id, str) and question_id
            for question_id in native_judge_question_ids
        )
        or not native_judge_question_ids.issubset(processor_question_ids)
        or (
            len(processor_rows) == QUESTION_COUNT
            and len(native_judge_question_ids) != 156
        )
    ):
        raise RuntimeError("native judge question subset is invalid")
    judge_nanos = _judge_liability(judge)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for source in processor_rows:
        if not isinstance(source, dict):
            raise RuntimeError("reader row token inventory is malformed")
        question_id = source.get("question_id")
        has_image = source.get("has_image")
        local_tokens = source.get("local_processor_input_tokens")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in seen
            or type(has_image) is not bool
            or type(local_tokens) is not int
            or local_tokens <= 0
        ):
            raise RuntimeError("reader row token inventory is malformed")
        seen.add(question_id)
        input_units = prompt_ceiling if has_image else memory_context + local_tokens
        if input_units > prompt_ceiling:
            raise RuntimeError("reader row exceeds the provider prompt ceiling")
        reader_nanos = attempts * (
            _ceil_cost(input_units, reader.get("input_price_nanos_per_million"))
            + _ceil_cost(
                reader.get("maximum_output_tokens"),
                reader.get("output_price_nanos_per_million"),
            )
        )
        native_judge_required = question_id in native_judge_question_ids
        row_judge_nanos = judge_nanos if native_judge_required else 0
        rows.append(
            {
                "question_id": question_id,
                "has_image": has_image,
                "local_processor_input_tokens": local_tokens,
                "billing_authority": (
                    "provider_prompt_ceiling"
                    if has_image
                    else "pinned_local_text_processor"
                ),
                "input_reservation_units": input_units,
                "reader_liability_nanos": reader_nanos,
                "native_judge_required": native_judge_required,
                "judge_liability_nanos": row_judge_nanos,
                "per_arm_liability_nanos": reader_nanos + row_judge_nanos,
            }
        )
    image_rows = sum(int(row["has_image"]) for row in rows)
    if len(rows) == QUESTION_COUNT and image_rows != 29:
        raise RuntimeError("reader liability inventory must bind all 29 image rows")
    return {
        "schema_version": 1,
        "rows": rows,
        "row_count": len(rows),
        "text_rows": len(rows) - image_rows,
        "image_rows": image_rows,
        "native_judge_rows": len(native_judge_question_ids),
        "native_judge_question_ids_sha256": sha256_json(
            sorted(native_judge_question_ids)
        ),
        "reader_arm_liability_nanos": sum(
            row["per_arm_liability_nanos"] for row in rows
        ),
        "inventory_sha256": sha256_json(rows),
    }


def _deep_liability(config: dict[str, object]) -> dict[str, int]:
    required_strings = ("model", "response_model", "provider", "pricing_source", "pricing_sha256", "runtime_code_sha256")
    required_maxima = (
        "input_price_nanos_per_million",
        "output_price_nanos_per_million",
        "maximum_context_tokens",
        "maximum_output_tokens_per_turn",
        "maximum_tool_iterations",
        "maximum_retries_per_turn",
        "maximum_attempts_per_turn",
        "maximum_spend_micros",
        "maximum_liability_nanos",
    )
    if not all(isinstance(config.get(key), str) and config[key] for key in required_strings) or not all(type(config.get(key)) is int and config[key] > 0 for key in required_maxima):
        raise RuntimeError("Deep identity, pricing, or recall-wide maximum is missing")
    if config["maximum_attempts_per_turn"] != config["maximum_retries_per_turn"] + 1:
        raise RuntimeError("Deep attempt/retry maximum drift")
    context = config["maximum_context_tokens"]
    output = config["maximum_output_tokens_per_turn"]
    # Every dispatch reserves its full completion before transport.  At least
    # one serialized request byte accompanies it, so both the context ceiling
    # and the tool-turn ceiling bound the number of chargeable generations.
    maximum_dispatches = min(
        config["maximum_tool_iterations"] + 1,
        context // (output + 1),
    )
    output_units = maximum_dispatches * output
    input_units = context - output_units
    token_mix = _ceil_cost(input_units, config["input_price_nanos_per_million"]) + _ceil_cost(
        output_units, config["output_price_nanos_per_million"]
    )
    hard_stop = config["maximum_spend_micros"] * 1_000
    derived = min(token_mix, hard_stop)
    if config["maximum_liability_nanos"] != derived:
        raise RuntimeError("Deep liability drift")
    return {
        "maximum_dispatches": maximum_dispatches,
        "maximum_input_reservation_units": input_units,
        "maximum_output_reservation_units": output_units,
        "token_mix_liability_nanos": token_mix,
        "hard_spend_stop_nanos": hard_stop,
        "maximum_liability_nanos": derived,
    }


def _validate_deep_runtime_identity(config: dict[str, object]) -> None:
    runtime_path = ROOT / str(config.get("runtime_code_path", ""))
    if not runtime_path.is_file() or _sha256_file(runtime_path) != config.get("runtime_code_sha256"):
        raise RuntimeError("Deep production runtime identity drift")
    source = runtime_path.read_text(encoding="utf-8")
    expected_constants = {
        "DEFAULT_MAX_COMPLETION_TOKENS": config["maximum_output_tokens_per_turn"],
        "DEFAULT_MAX_TOOL_ITERATIONS": config["maximum_tool_iterations"],
        "DEFAULT_MAX_CONTEXT_TOKENS": config["maximum_context_tokens"],
        "DEFAULT_MAX_SPEND_MICROS": config["maximum_spend_micros"],
    }
    for name, expected in expected_constants.items():
        match = re.search(rf"const {name}: [^=]+ = ([0-9_]+);", source)
        if match is None or int(match.group(1).replace("_", "")) != expected:
            raise RuntimeError(f"Deep production limit drift: {name}")
    retry_match = re.search(r"\n\s*max_retries: ([0-9_]+),", source)
    if retry_match is None or int(retry_match.group(1).replace("_", "")) != config["maximum_retries_per_turn"]:
        raise RuntimeError("Deep production limit drift: max_retries")
    model_match = re.search(r'const LME_V2_QWEN_MODEL: &str = "([^"]+)";', source)
    if (
        model_match is None
        or model_match.group(1) != config["model"]
        or config.get("provider") != "deepinfra"
        or config.get("allow_fallbacks") is not False
        or '"allow_fallbacks": false' not in source
        or '"deepinfra"' not in source
    ):
        raise RuntimeError("Deep Qwen route identity drift")


def _run_reader_processor_census(
    reader: dict[str, object],
    data_root: Path,
    fixture: Path,
    output: Path,
) -> dict[str, object]:
    runtime = reader.get("processor_runtime")
    if not isinstance(runtime, dict):
        raise RuntimeError("reader processor runtime identity is missing")
    script = ROOT / str(runtime.get("script_path", ""))
    requirements = ROOT / str(runtime.get("requirements_path", ""))
    uv = shutil.which("uv")
    if (
        uv is None
        or not script.is_file()
        or _sha256_file(script) != runtime.get("script_sha256")
        or not requirements.is_file()
        or _sha256_file(requirements) != runtime.get("requirements_sha256")
    ):
        raise RuntimeError("reader processor runtime identity drift")
    uv_version = subprocess.run(
        [uv, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if hashlib.sha256(uv_version.encode("utf-8")).hexdigest() != runtime.get(
        "uv_version_sha256"
    ):
        raise RuntimeError("reader processor uv identity drift")
    python_requirement = runtime.get("python")
    if not isinstance(python_requirement, str) or not python_requirement:
        raise RuntimeError("reader processor Python identity is missing")
    environment = {
        key: os.environ[key]
        for key in ("HOME", "PATH", "TMPDIR", "UV_CACHE_DIR")
        if key in os.environ
    }
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    command = [
        uv,
        "run",
        "--quiet",
        "--no-project",
        "--python",
        python_requirement,
        "--with-requirements",
        str(requirements),
        "python",
        str(script),
        "--fixture-jsonl",
        str(fixture),
        "--data-root",
        str(data_root),
        "--model-dir",
        str(data_root / "qwen"),
        "--checksums",
        str(data_root / "checksums.sha256"),
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, env=environment
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"reader processor census failed: {completed.stderr.strip()}"
        )
    proof = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(proof, dict):
        raise RuntimeError("reader processor census output is malformed")
    return proof


def _acquire_manifest_data(manifest: dict[str, object], data_root: Path) -> None:
    data = manifest.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise RuntimeError("campaign data manifest is missing")
    for relative, record in data["files"].items():
        if not isinstance(relative, str) or not isinstance(record, dict):
            raise RuntimeError("campaign data manifest is malformed")
        _acquire_file(
            record["url"],
            data_root / relative,
            record["bytes"],
            record["sha256"],
        )
    screenshot_set = data.get("question_screenshots")
    if not isinstance(screenshot_set, dict):
        raise RuntimeError("question screenshot acquisition manifest is missing")
    base_url = screenshot_set.get("base_url")
    screenshot_files = screenshot_set.get("files")
    if (
        not isinstance(base_url, str)
        or not base_url.endswith("/")
        or not isinstance(screenshot_files, dict)
        or len(screenshot_files) != 29
        or any(
            not isinstance(relative, str)
            or not relative.startswith("question_screenshots/")
            or not _valid_sha256(expected)
            for relative, expected in screenshot_files.items()
        )
    ):
        raise RuntimeError("question screenshot acquisition manifest is malformed")
    checksum_entries: dict[str, str] = {}
    for line in (data_root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, relative = line.split(maxsplit=1)
            if relative.startswith("question_screenshots/"):
                checksum_entries[relative] = digest
    if checksum_entries != screenshot_files:
        raise RuntimeError("question screenshot acquisition checksum drift")
    for relative, expected in screenshot_files.items():
        _acquire_file(base_url + relative, data_root / relative, None, expected)


def _recompute_reader_authority(
    manifest: dict[str, object], data_root: Path
) -> tuple[dict[str, object], dict[str, object]]:
    _acquire_manifest_data(manifest, data_root)
    reader_judge = manifest.get("reader_judge")
    if not isinstance(reader_judge, dict):
        raise RuntimeError("reader and judge identities are required")
    reader = reader_judge.get("reader")
    judge = reader_judge.get("judge")
    if not isinstance(reader, dict) or not isinstance(judge, dict):
        raise RuntimeError("reader and judge identities are required")
    official_dir, _ = acquire_official_runtime_code(data_root)
    with tempfile.TemporaryDirectory(prefix="memphant-reader-authority-") as temporary:
        temporary_root = Path(temporary)
        fixture_path = temporary_root / "reader-shapes.jsonl"
        proof_path = temporary_root / "reader-processor-proof.json"
        fixture = _materialize_reader_shapes(
            data_root, fixture_path, official_dir=official_dir
        )
        proof = _run_reader_processor_census(
            reader, data_root, fixture_path, proof_path
        )
        request_shapes = _official_request_shape_bounds(
            data_root, proof, fixture, reader, official_dir=official_dir
        )
    processor_rows = request_shapes.pop("reader_processor_rows")
    inventory = _reader_liability_inventory(
        processor_rows,
        reader,
        judge,
        native_judge_question_ids=set(
            request_shapes["native_judge_question_ids"]
        ),
    )
    if inventory["row_count"] != QUESTION_COUNT:
        raise RuntimeError("reader liability inventory must contain all 451 rows")
    return request_shapes, inventory


def _full_census(
    manifest: dict[str, object],
    manifest_path: Path,
    data_root: Path,
    cli_bin: Path,
    binary_provenance: dict[str, object],
) -> dict[str, object]:
    _acquire_manifest_data(manifest, data_root)
    official_dir, runtime_code = acquire_official_runtime_code(data_root)
    construction = manifest["construction"]
    prompt = ROOT / construction["prompt_path"]
    code_paths = [ROOT / path for path in construction["code_paths"]]
    if _sha256_file(prompt) != construction["prompt_sha256"] or any(
        _sha256_file(path) != construction["code_sha256s"][str(path.relative_to(ROOT))]
        for path in code_paths
    ):
        raise RuntimeError("production construction planner identity drift")
    reader = manifest["reader_judge"]["reader"]
    judge = manifest["reader_judge"]["judge"]
    _validate_deep_runtime_identity(manifest["deep_recall"])
    with tempfile.TemporaryDirectory(prefix="memphant-lme-v2-census-") as temporary:
        temporary_root = Path(temporary)
        expected_binary_sha256 = binary_provenance.get("binary_sha256")
        if (
            not isinstance(expected_binary_sha256, str)
            or not SHA256.fullmatch(expected_binary_sha256)
        ):
            raise RuntimeError("census binary provenance is malformed")
        execution_cli = temporary_root / f"memphant-cli-{expected_binary_sha256}"
        shutil.copyfile(cli_bin, execution_cli)
        execution_cli.chmod(0o500)
        _verify_selected_census_binary(expected_binary_sha256, execution_cli)
        input_jsonl = temporary_root / "resources.jsonl"
        reader_jsonl = temporary_root / "reader-shapes.jsonl"
        reader_proof_json = temporary_root / "reader-processor-proof.json"
        enumeration = _materialize_cli_input(data_root, input_jsonl)
        reader_fixture = _materialize_reader_shapes(
            data_root, reader_jsonl, official_dir=official_dir
        )
        reader_proof = _run_reader_processor_census(
            reader, data_root, reader_jsonl, reader_proof_json
        )
        command = [
            str(execution_cli),
            "structured-state",
            "census",
            "--input-jsonl",
            str(input_jsonl),
            "--model",
            construction["model"],
            "--prompt-file",
            str(prompt),
            "--input-price-nanos-per-million",
            str(construction["input_price_nanos_per_million"]),
            "--output-price-nanos-per-million",
            str(construction["output_price_nanos_per_million"]),
            "--tokenizer-file",
            str(data_root / construction["tokenizer_path"]),
            "--tokenizer-config-file",
            str(data_root / construction["tokenizer_config_path"]),
        ]
        if construction.get("reasoning_effort"):
            command += ["--reasoning-effort", construction["reasoning_effort"]]
        completed = subprocess.run(command, capture_output=True, text=True, check=False, env={})
        _verify_selected_census_binary(expected_binary_sha256, execution_cli)
        if completed.returncode != 0:
            raise RuntimeError(f"production census planner failed: {completed.stderr.strip()}")
        planned = json.loads(completed.stdout)
    plan_inventory = planned.get("plan_inventory")
    if (
        not isinstance(plan_inventory, list)
        or len(plan_inventory) != planned.get("unique_extraction_keys")
        or planned.get("plan_inventory_sha256") != sha256_json(plan_inventory)
    ):
        raise RuntimeError("production census plan inventory drift")
    construction_canary = derive_construction_canary(plan_inventory)
    if construction_canary["plan_count"] != CONSTRUCTION_CANARY_PLAN_COUNT:
        raise RuntimeError("production construction canary requires exactly 64 plans")
    request_shapes = _official_request_shape_bounds(
        data_root,
        reader_proof,
        reader_fixture,
        reader,
        official_dir=official_dir,
    )
    processor_rows = request_shapes.pop("reader_processor_rows")
    if judge.get("maximum_fixed_serialized_bytes") != request_shapes["judge_maximum_fixed_serialized_bytes"]:
        raise RuntimeError("judge request maximum drift")
    reader_inventory = _reader_liability_inventory(
        processor_rows,
        reader,
        judge,
        native_judge_question_ids=set(
            request_shapes["native_judge_question_ids"]
        ),
    )
    if reader_inventory["row_count"] != QUESTION_COUNT:
        raise RuntimeError("reader liability inventory must contain all 451 rows")
    r_sum = reader_inventory["reader_arm_liability_nanos"]
    deep_derivation = _deep_liability(manifest["deep_recall"])
    s_term = deep_derivation["maximum_liability_nanos"]
    wave_policy = construction.get("wave_policy")
    if (
        not isinstance(wave_policy, dict)
        or wave_policy.get("maximum_internal_attempts") != 1
        or wave_policy.get("maximum_campaign_waves") != planned.get("maximum_attempts")
        or wave_policy.get("first_wave_reservation")
        != "sum_exact_per_attempt_reservation_nanos"
        or wave_policy.get("retry_wave_reservation")
        != "campaign-ledger-authorized-aggregate"
        or wave_policy.get("requires_exact_subledger_coverage") is not True
        or wave_policy.get("rust_subledger_aggregate_cap_env")
        != "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS"
        or wave_policy.get("rust_subledger_campaign_attempt_env")
        != "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT"
        or wave_policy.get("not_charged_pre_generation_http_statuses")
        != [429, 502, 503]
        or not isinstance(wave_policy.get("error_contract_source"), str)
        or not isinstance(wave_policy.get("billing_contract_source"), str)
    ):
        raise RuntimeError("construction aggregate-wave policy is incomplete")
    first_attempt = planned.get("first_attempt_liability_nanos")
    full_three_wave = planned.get("full_three_wave_liability_nanos")
    retry_pool = wave_policy.get("retry_pool_nanos")
    if (
        type(first_attempt) is not int
        or first_attempt <= 0
        or full_three_wave != first_attempt * planned["maximum_attempts"]
        or type(retry_pool) is not int
        or retry_pool < 0
    ):
        raise RuntimeError("construction wave liability decomposition drift")
    fixed_without_retries = (
        OPENING_NANOS
        + first_attempt
        + 2 * r_sum
        + QUESTION_COUNT * s_term
        + CONTINGENCY_NANOS
    )
    maximum_retry_pool = HARD_CEILING_NANOS - fixed_without_retries
    if retry_pool > maximum_retry_pool:
        raise RuntimeError("construction retry pool exceeds campaign headroom")
    if (
        construction_canary["liability"][
            "maximum_transient_retry_reservation_nanos"
        ]
        > retry_pool
    ):
        raise RuntimeError("construction canary retry reserve exceeds retry pool")
    c_term = first_attempt + retry_pool
    total = (
        OPENING_NANOS
        + c_term
        + 2 * r_sum
        + QUESTION_COUNT * s_term
        + CONTINGENCY_NANOS
    )
    planned = {
        **planned,
        "construction_liability_nanos": c_term,
        "retry_pool_nanos": retry_pool,
        "maximum_retry_pool_nanos": maximum_retry_pool,
        "maximum_internal_attempts": 1,
        "requested_model": construction["model"],
        "response_model": construction["response_model"],
        "requested_provider": construction["provider"],
        "construction_identity_sha256": sha256_json(construction),
        "census_binary_provenance": binary_provenance,
        "parent_full_census_required_for_recost": False,
        "construction_canary": construction_canary,
    }
    core = {
        "schema_version": 1,
        "benchmark": {
            "name": "LongMemEval-V2",
            "tier": "medium",
            "questions": QUESTION_COUNT,
            "memory_context_max_tokens": 200000,
            "code_commit": manifest["upstream"]["code_commit"],
            "dataset_revision": manifest["upstream"]["dataset_revision"],
        },
        "upstream_runtime_code": runtime_code,
        "enumeration": enumeration,
        "construction": planned,
        "terms": {"C": c_term, "R_sum": r_sum, "S": s_term},
        "liability_derivation": {
            "request_shapes": request_shapes,
            "reader_inventory": reader_inventory,
            "deep": deep_derivation,
        },
        "admission": {
            "formula": FORMULA,
            "opening_liability_nanos": OPENING_NANOS,
            "contingency_nanos": CONTINGENCY_NANOS,
            "hard_ceiling_nanos": HARD_CEILING_NANOS,
            "maximum_retry_pool_nanos": maximum_retry_pool,
            "total_nanos": total,
            "missing_bounds": [],
            "missing_identities": [],
            "authorized": total <= HARD_CEILING_NANOS,
        },
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "paid_models_run": False,
        "spend_nanos": 0,
    }
    return {**core, "census_sha256": sha256_json(core)}


def _validate_census_hash(census: dict[str, object], *, label: str) -> None:
    claimed = census.get("census_sha256")
    core = {key: value for key, value in census.items() if key != "census_sha256"}
    if not isinstance(claimed, str) or claimed != sha256_json(core):
        raise RuntimeError(f"{label} census sha256 mismatch")


def recost_census_values(
    parent: dict[str, object],
    *,
    reader_inventory: dict[str, object],
    s_term: int,
    retry_pool_nanos: int,
    manifest_path: str,
    manifest_sha256: str,
    runtime_hashes: dict[str, str],
) -> dict[str, object]:
    """Reprice a frozen construction census without reconstructing its corpus.

    The parent census priced three possible attempts for every extraction key.
    Production now permits one HTTP attempt per campaign-ledger wave.  The
    mandatory first wave is therefore the exact one-attempt factor; separately
    authorized retry waves share one explicit aggregate pool.  The independent
    $10 contingency remains untouched.
    """
    _validate_census_hash(parent, label="parent")
    if parent.get("paid_models_run") is not False or parent.get("spend_nanos") != 0:
        raise RuntimeError("recost parent must be a no-model zero-spend census")
    construction = parent.get("construction")
    enumeration = parent.get("enumeration")
    benchmark = parent.get("benchmark")
    if not all(isinstance(value, dict) for value in (construction, enumeration, benchmark)):
        raise RuntimeError("recost parent census is incomplete")
    attempts = construction.get("maximum_attempts")
    full_retry_liability = construction.get("construction_liability_nanos")
    if attempts != 3 or type(full_retry_liability) is not int or full_retry_liability <= 0:
        raise RuntimeError("parent construction attempt factor is not the frozen three-wave bound")
    if full_retry_liability % attempts:
        raise RuntimeError("parent construction liability is not exactly factorizable by attempts")
    first_attempt = full_retry_liability // attempts
    if (
        not isinstance(reader_inventory, dict)
        or reader_inventory.get("row_count") != QUESTION_COUNT
        or not isinstance(reader_inventory.get("rows"), list)
        or reader_inventory.get("inventory_sha256")
        != sha256_json(reader_inventory.get("rows"))
        or type(reader_inventory.get("reader_arm_liability_nanos")) is not int
        or reader_inventory["reader_arm_liability_nanos"] <= 0
        or type(s_term) is not int
        or s_term <= 0
    ):
        raise RuntimeError("recost reader/judge and Deep terms must be positive")
    if type(retry_pool_nanos) is not int or retry_pool_nanos < 0:
        raise RuntimeError("construction retry pool must be a non-negative integer")
    if not SHA256.fullmatch(manifest_sha256) or not runtime_hashes or not all(
        isinstance(value, str) and SHA256.fullmatch(value)
        for value in runtime_hashes.values()
    ):
        raise RuntimeError("recost manifest and runtime hashes must be exact SHA-256 values")

    r_sum = reader_inventory["reader_arm_liability_nanos"]
    fixed_without_retries = (
        OPENING_NANOS
        + first_attempt
        + 2 * r_sum
        + QUESTION_COUNT * s_term
        + CONTINGENCY_NANOS
    )
    maximum_retry_pool = HARD_CEILING_NANOS - fixed_without_retries
    if retry_pool_nanos > maximum_retry_pool:
        raise RuntimeError("construction retry pool exceeds admitted campaign headroom")
    c_term = first_attempt + retry_pool_nanos
    total = fixed_without_retries + retry_pool_nanos
    parent_construction_sha256 = sha256_json(construction)
    construction_proof = {
        **construction,
        "full_three_wave_liability_nanos": full_retry_liability,
        "first_attempt_liability_nanos": first_attempt,
        "retry_pool_nanos": retry_pool_nanos,
        "maximum_retry_pool_nanos": maximum_retry_pool,
        "maximum_internal_attempts": 1,
        "maximum_campaign_waves": attempts,
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "response_model": "qwen/qwen3.5-9b",
        "requested_provider": "deepinfra",
        "construction_identity_sha256": parent_construction_sha256,
        "construction_liability_nanos": c_term,
        "parent_construction_sha256": parent_construction_sha256,
    }
    plan_inventory = construction_proof.get("plan_inventory")
    if isinstance(plan_inventory, list) and plan_inventory:
        construction_canary = derive_construction_canary(plan_inventory)
        if (
            construction_canary["liability"][
                "maximum_transient_retry_reservation_nanos"
            ]
            > retry_pool_nanos
        ):
            raise RuntimeError("construction canary retry reserve exceeds retry pool")
        construction_proof["construction_canary"] = construction_canary
    core = {
        "schema_version": 2,
        "benchmark": benchmark,
        "enumeration": enumeration,
        "construction": construction_proof,
        "terms": {"C": c_term, "R_sum": r_sum, "S": s_term},
        "liability_derivation": {
            "reader_inventory": reader_inventory,
            "construction": {
                "method": "exact-parent-three-wave-factor-plus-bounded-retry-pool",
                "parent_full_retry_liability_nanos": full_retry_liability,
                "campaign_wave_factor": attempts,
                "first_attempt_liability_nanos": first_attempt,
                "retry_pool_nanos": retry_pool_nanos,
            }
        },
        "admission": {
            "formula": FORMULA,
            "opening_liability_nanos": OPENING_NANOS,
            "contingency_nanos": CONTINGENCY_NANOS,
            "hard_ceiling_nanos": HARD_CEILING_NANOS,
            "maximum_retry_pool_nanos": maximum_retry_pool,
            "total_nanos": total,
            "missing_bounds": [],
            "missing_identities": [],
            "authorized": total <= HARD_CEILING_NANOS,
        },
        "derivation": {
            "parent_census_sha256": parent["census_sha256"],
            "parent_construction_sha256": parent_construction_sha256,
            "current_manifest_sha256": manifest_sha256,
            "runtime_code_sha256s": dict(sorted(runtime_hashes.items())),
        },
        "manifest_path": manifest_path,
        "manifest_sha256": manifest_sha256,
        "paid_models_run": False,
        "spend_nanos": 0,
    }
    return {**core, "census_sha256": sha256_json(core)}


def _plan_inventory(plans: list[dict[str, object]]) -> tuple[list[dict[str, object]], int, str]:
    normalized = []
    seen = set()
    total = 0
    for plan in plans:
        key = plan.get("extraction_key")
        request_hash = plan.get("request_sha256")
        reservation = plan.get("per_attempt_reservation_nanos")
        requested_model = plan.get("requested_model")
        maximum_attempts = plan.get("maximum_attempts")
        optional_source = {
            "source_kind": plan.get("source_kind"),
            "source_body_sha256": plan.get("source_body_sha256"),
            "batch_index": plan.get("batch_index"),
            "evidence_slices_sha256": plan.get("evidence_slices_sha256"),
        }
        has_source_identity = any(value is not None for value in optional_source.values())
        if (
            not isinstance(key, str)
            or not SHA256.fullmatch(key)
            or key in seen
            or not isinstance(request_hash, str)
            or not SHA256.fullmatch(request_hash)
            or type(reservation) is not int
            or reservation <= 0
            or not isinstance(requested_model, str)
            or not requested_model
            or type(maximum_attempts) is not int
            or maximum_attempts <= 0
        ):
            raise RuntimeError("construction wave plan inventory is malformed")
        if has_source_identity and (
            optional_source["source_kind"] not in {"episode", "resource"}
            or not _valid_sha256(optional_source["source_body_sha256"])
            or type(optional_source["batch_index"]) is not int
            or optional_source["batch_index"] < 0
            or not _valid_sha256(optional_source["evidence_slices_sha256"])
        ):
            raise RuntimeError("construction wave plan source identity is malformed")
        seen.add(key)
        normalized_plan = {
                "extraction_key": key,
                "request_sha256": request_hash,
                "per_attempt_reservation_nanos": reservation,
                "requested_model": requested_model,
                "maximum_attempts": maximum_attempts,
            }
        if has_source_identity:
            normalized_plan.update(optional_source)
        normalized.append(normalized_plan)
        total += reservation
    if not normalized:
        raise RuntimeError("construction wave plan inventory is empty")
    normalized.sort(key=lambda plan: plan["extraction_key"])
    return normalized, total, sha256_json(normalized)


def _v1_failed_source_body_sha256s() -> tuple[set[str], str]:
    value = json.loads(V1_FAILED_SOURCE_INVENTORY.read_text(encoding="utf-8"))
    hashes = value.get("source_body_sha256s") if isinstance(value, dict) else None
    abandonment = json.loads(V1_ABANDONMENT_PROOF.read_text(encoding="utf-8"))
    if (
        not isinstance(hashes, list)
        or hashes != sorted(set(hashes))
        or not all(_valid_sha256(item) for item in hashes)
        or value.get("unique_source_body_count") != len(hashes)
        or value.get("source_body_inventory_sha256") != sha256_json(hashes)
        or value.get("contains_source_bodies") is not False
        or value.get("source_construction_ledger_sha256")
        != abandonment.get("ledger", {}).get("sha256")
    ):
        raise RuntimeError("v1 failed-source canary inventory drift")
    return set(hashes), _sha256_file(V1_FAILED_SOURCE_INVENTORY)


def derive_construction_canary(plans: list[dict[str, object]]) -> dict[str, object]:
    """Choose a stable, reservation-stratified preflight subset."""
    normalized, _reservation, inventory_sha256 = _plan_inventory(plans)
    preferred, preferred_inventory_sha256 = _v1_failed_source_body_sha256s()
    target = min(CONSTRUCTION_CANARY_PLAN_COUNT, len(normalized))
    groups: dict[str, list[dict[str, object]]] = {}
    source_kinds = sorted(
        {str(plan.get("source_kind") or "unspecified") for plan in normalized}
    )
    for source_kind in source_kinds:
        members = [
            plan
            for plan in normalized
            if str(plan.get("source_kind") or "unspecified") == source_kind
        ]
        members.sort(
            key=lambda plan: (
                plan["per_attempt_reservation_nanos"],
                plan["extraction_key"],
            )
        )
        for index, plan in enumerate(members):
            quantile = min(
                CONSTRUCTION_CANARY_QUANTILES - 1,
                index * CONSTRUCTION_CANARY_QUANTILES // len(members),
            )
            groups.setdefault(f"{source_kind}:q{quantile + 1}", []).append(plan)
    for candidates in groups.values():
        candidates.sort(
            key=lambda plan: (
                plan.get("source_body_sha256") not in preferred,
                plan["extraction_key"],
            )
        )
    selected_with_stratum: list[tuple[str, dict[str, object]]] = []
    while len(selected_with_stratum) < target:
        advanced = False
        for label in sorted(groups):
            if groups[label] and len(selected_with_stratum) < target:
                selected_with_stratum.append((label, groups[label].pop(0)))
                advanced = True
        if not advanced:
            raise RuntimeError("construction canary stratification is incomplete")
    group_counts = Counter(label for label, _plan in selected_with_stratum)
    selected = [plan for _label, plan in selected_with_stratum]
    selected.sort(key=lambda plan: plan["extraction_key"])
    selected_reservation = sum(
        plan["per_attempt_reservation_nanos"] for plan in selected
    )
    core = {
        "schema_version": 1,
        "plan_count": target,
        "selection_method": "source-kind-x-within-kind-reservation-quartile-round-robin-v1",
        "full_plan_inventory_sha256": inventory_sha256,
        "plans": selected,
        "plans_sha256": sha256_json(selected),
        "stratum_counts": dict(sorted(group_counts.items())),
        "preferred_v1_failed_source_count": sum(
            plan.get("source_body_sha256") in preferred for plan in selected
        ),
        "preferred_v1_failed_source_inventory_file_sha256": preferred_inventory_sha256,
        "gate": {
            "failure_definition": "any non-transient schema-or-semantic decode failure",
            "maximum_semantic_failures": 0,
            "maximum_statistical_failures": _maximum_failures_below_exact_upper_bound(
                target,
                CONSTRUCTION_CANARY_ALPHA,
                CONSTRUCTION_CANARY_FAILURE_LIMIT,
            ),
            "one_sided_alpha": str(CONSTRUCTION_CANARY_ALPHA),
            "decode_failure_rate_upper_limit": str(
                CONSTRUCTION_CANARY_FAILURE_LIMIT
            ),
            "maximum_transient_retries_per_plan": CONSTRUCTION_CANARY_MAX_TRANSIENT_RETRIES,
        },
        "liability": {
            "first_attempt_reservation_nanos": selected_reservation,
            "maximum_transient_retry_reservation_nanos": selected_reservation,
            "first_attempt_is_subset_of_construction_first_wave": True,
            "transient_retry_is_subset_of_construction_retry_pool": True,
        },
    }
    return {**core, "canary_sha256": sha256_json(core)}


def evaluate_construction_canary(
    canary: dict[str, object], events: list[dict[str, object]]
) -> dict[str, object]:
    """Apply the preregistered fail-closed canary gate to exact ledger rows."""
    core = {key: value for key, value in canary.items() if key != "canary_sha256"}
    if canary.get("canary_sha256") != sha256_json(core):
        raise RuntimeError("construction canary identity drift")
    plans = canary.get("plans")
    normalized, _reservation, plans_sha256 = _plan_inventory(plans)
    if plans_sha256 != canary.get("plans_sha256"):
        raise RuntimeError("construction canary plan inventory drift")
    results: dict[tuple[str, str], list[dict[str, object]]] = {}
    for event in events:
        if event.get("event") != "result":
            continue
        pair = (event.get("extraction_key"), event.get("request_sha256"))
        if pair in {
            (plan["extraction_key"], plan["request_sha256"])
            for plan in normalized
        }:
            results.setdefault(pair, []).append(event)
    semantic_failures = []
    incomplete_failures = []
    transient_pending = []
    decoded = []
    for plan in normalized:
        pair = (plan["extraction_key"], plan["request_sha256"])
        attempts = sorted(
            results.get(pair, []), key=lambda event: event.get("campaign_attempt", 0)
        )
        if not attempts:
            incomplete_failures.append(plan)
            continue
        latest = attempts[-1]
        if (
            latest.get("reservation_status") == "settled"
            and latest.get("parse_status") == "decoded"
            and latest.get("error") is None
        ):
            decoded.append(plan)
        elif (
            latest.get("reservation_status") == "not_charged"
            and latest.get("parse_status") == "http_error"
            and latest.get("http_status") in {429, 502, 503}
            and latest.get("campaign_attempt", 0)
            <= 1 + CONSTRUCTION_CANARY_MAX_TRANSIENT_RETRIES
        ):
            transient_pending.append(plan)
        else:
            semantic_failures.append(plan)
    failure_upper = _clopper_pearson_upper(
        len(semantic_failures) + len(incomplete_failures),
        len(normalized),
        CONSTRUCTION_CANARY_ALPHA,
    )
    accepted = (
        len(normalized) == canary.get("plan_count")
        and not semantic_failures
        and not incomplete_failures
        and not transient_pending
        and len(decoded) == len(normalized)
        and failure_upper < CONSTRUCTION_CANARY_FAILURE_LIMIT
    )
    gate_core = {
        "schema_version": 1,
        "canary_sha256": canary["canary_sha256"],
        "plan_count": len(normalized),
        "decoded_count": len(decoded),
        "semantic_failure_count": len(semantic_failures),
        "semantic_failure_plan_sha256": sha256_json(semantic_failures),
        "incomplete_failure_count": len(incomplete_failures),
        "incomplete_failure_plan_sha256": sha256_json(incomplete_failures),
        "observed_failure_count": len(semantic_failures) + len(incomplete_failures),
        "transient_pending_count": len(transient_pending),
        "transient_pending_plans": transient_pending,
        "transient_pending_plans_sha256": sha256_json(transient_pending),
        "one_sided_clopper_pearson_upper": format(failure_upper, "f"),
        "decode_failure_rate_upper_limit": str(
            CONSTRUCTION_CANARY_FAILURE_LIMIT
        ),
        "accepted": accepted,
    }
    return {**gate_core, "gate_sha256": sha256_json(gate_core)}


def bind_construction_canary_cache(
    gate: dict[str, object],
    canary: dict[str, object],
    cache_root: Path,
) -> dict[str, object]:
    """Bind every accepted canary success to its immutable canonical cache row."""
    if gate.get("accepted") is not True:
        return gate
    plans = canary.get("plans")
    normalized, _reservation, _plans_sha256 = _plan_inventory(plans)
    inventory = []
    for plan in normalized:
        path = cache_root / f"{plan['extraction_key']}.json"
        if not path.is_file():
            raise RuntimeError("accepted construction canary is missing canonical cache")
        inventory.append(
            {
                "extraction_key": plan["extraction_key"],
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    core = {
        key: value
        for key, value in gate.items()
        if key not in {"gate_sha256", "canonical_cache"}
    }
    core["canonical_cache"] = {
        "namespace": "longmemeval-v2-construction-v2",
        "entries": inventory,
        "entries_sha256": sha256_json(inventory),
    }
    return {**core, "gate_sha256": sha256_json(core)}


def _validate_current_manifest_identities(manifest: dict[str, object]) -> None:
    construction = manifest.get("construction")
    if not isinstance(construction, dict):
        raise RuntimeError("campaign construction identity is missing")
    prompt = ROOT / str(construction.get("prompt_path", ""))
    code_paths = construction.get("code_paths")
    code_hashes = construction.get("code_sha256s")
    if (
        not prompt.is_file()
        or _sha256_file(prompt) != construction.get("prompt_sha256")
        or not isinstance(code_paths, list)
        or not code_paths
        or not isinstance(code_hashes, dict)
        or any(
            not isinstance(relative, str)
            or not (ROOT / relative).is_file()
            or _sha256_file(ROOT / relative) != code_hashes.get(relative)
            for relative in code_paths
        )
    ):
        raise RuntimeError("campaign production identity drift")
    _validate_deep_runtime_identity(manifest.get("deep_recall", {}))


def validate_campaign_authorization(
    census_path: Path, manifest_path: Path
) -> dict[str, object]:
    census = json.loads(census_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(census, dict) or not isinstance(manifest, dict):
        raise RuntimeError("campaign authorization packet is malformed")
    _validate_census_hash(census, label="campaign")
    if census.get("manifest_sha256") != _sha256_file(manifest_path):
        raise RuntimeError("campaign manifest identity drift")
    if census.get("paid_models_run") is not False or census.get("spend_nanos") != 0:
        raise RuntimeError("construction wave requires a no-model campaign census")
    benchmark = census.get("benchmark")
    manifest_benchmark = manifest.get("benchmark")
    if (
        not isinstance(benchmark, dict)
        or not isinstance(manifest_benchmark, dict)
        or benchmark.get("questions") != QUESTION_COUNT
        or benchmark.get("memory_context_max_tokens") != 200_000
        or manifest_benchmark.get("questions") != QUESTION_COUNT
        or manifest_benchmark.get("memory_context_max_tokens") != 200_000
    ):
        raise RuntimeError("campaign official benchmark identity drift")
    construction = census.get("construction")
    manifest_construction = manifest.get("construction")
    terms = census.get("terms")
    admission = census.get("admission")
    if not all(
        isinstance(value, dict)
        for value in (construction, manifest_construction, terms, admission)
    ):
        raise RuntimeError("campaign admission equation drift")
    first_attempt = construction.get("first_attempt_liability_nanos")
    retry_pool = construction.get("retry_pool_nanos")
    if type(first_attempt) is not int or first_attempt <= 0 or type(retry_pool) is not int or retry_pool < 0:
        raise RuntimeError("campaign admission equation drift")
    c_term = first_attempt + retry_pool
    reader_judge = manifest.get("reader_judge")
    derivation = census.get("liability_derivation")
    if not isinstance(reader_judge, dict) or not isinstance(derivation, dict):
        raise RuntimeError("campaign admission equation drift")
    reader = reader_judge.get("reader")
    judge = reader_judge.get("judge")
    request_shapes = derivation.get("request_shapes")
    reader_inventory = derivation.get("reader_inventory")
    if (
        not isinstance(reader, dict)
        or not isinstance(judge, dict)
        or not isinstance(request_shapes, dict)
        or not isinstance(request_shapes.get("native_judge_question_ids"), list)
        or request_shapes.get("native_judge_rows") != 156
        or request_shapes.get("native_judge_question_ids_sha256")
        != sha256_json(request_shapes.get("native_judge_question_ids"))
        or not isinstance(reader_inventory, dict)
        or not isinstance(reader_inventory.get("rows"), list)
    ):
        raise RuntimeError("campaign reader liability inventory drift")
    processor_rows = [
        {
            "question_id": row.get("question_id"),
            "has_image": row.get("has_image"),
            "local_processor_input_tokens": row.get(
                "local_processor_input_tokens"
            ),
        }
        for row in reader_inventory["rows"]
        if isinstance(row, dict)
    ]
    derived_reader_inventory = _reader_liability_inventory(
        processor_rows,
        reader,
        judge,
        native_judge_question_ids=set(
            request_shapes["native_judge_question_ids"]
        ),
    )
    if derived_reader_inventory != reader_inventory:
        raise RuntimeError("campaign reader liability inventory drift")
    r_sum = reader_inventory["reader_arm_liability_nanos"]
    s_term = _deep_liability(manifest.get("deep_recall", {}))[
        "maximum_liability_nanos"
    ]
    fixed_without_retries = (
        OPENING_NANOS
        + first_attempt
        + 2 * r_sum
        + QUESTION_COUNT * s_term
        + CONTINGENCY_NANOS
    )
    maximum_retry_pool = HARD_CEILING_NANOS - fixed_without_retries
    total = (
        OPENING_NANOS
        + c_term
        + 2 * r_sum
        + QUESTION_COUNT * s_term
        + CONTINGENCY_NANOS
    )
    expected_admission = {
        "formula": FORMULA,
        "opening_liability_nanos": OPENING_NANOS,
        "contingency_nanos": CONTINGENCY_NANOS,
        "hard_ceiling_nanos": HARD_CEILING_NANOS,
        "maximum_retry_pool_nanos": maximum_retry_pool,
        "total_nanos": total,
        "missing_bounds": [],
        "missing_identities": [],
        "authorized": total <= HARD_CEILING_NANOS,
    }
    plans = construction.get("plan_inventory")
    expected_canary = (
        derive_construction_canary(plans) if isinstance(plans, list) else None
    )
    if (
        terms != {"C": c_term, "R_sum": r_sum, "S": s_term}
        or admission != expected_admission
        or total > HARD_CEILING_NANOS
        or retry_pool > maximum_retry_pool
        or construction.get("construction_liability_nanos") != c_term
        or construction.get("maximum_retry_pool_nanos") != maximum_retry_pool
        or construction.get("construction_identity_sha256")
        != sha256_json(manifest_construction)
        or construction.get("construction_canary") != expected_canary
        or not isinstance(expected_canary, dict)
        or expected_canary["plan_count"] != CONSTRUCTION_CANARY_PLAN_COUNT
        or expected_canary["liability"][
            "maximum_transient_retry_reservation_nanos"
        ]
        > retry_pool
    ):
        raise RuntimeError("campaign admission equation drift")
    data_root = Path(
        os.environ.get(
            "MEMPHANT_LME_V2_DATA_ROOT",
            Path.home() / ".cache/memphant/longmemeval-v2",
        )
    )
    current_request_shapes, current_reader_inventory = _recompute_reader_authority(
        manifest, data_root
    )
    if (
        request_shapes != current_request_shapes
        or reader_inventory != current_reader_inventory
    ):
        raise RuntimeError("campaign reader processor or liability inventory drift")
    _validate_current_manifest_identities(manifest)
    actual_binary, actual_provenance = _build_census_binary(manifest)
    del actual_binary
    expected_provenance = construction.get("census_binary_provenance")
    if not isinstance(expected_provenance, dict):
        raise RuntimeError("campaign census binary provenance is missing")
    _validate_census_binary_provenance(expected_provenance, actual_provenance)
    return census


def derive_v1_abandonment_proof(
    ledger_path: Path = V1_CAMPAIGN_ARTIFACT_ROOT / "CONSTRUCTION-ATTEMPTS.jsonl",
    campaign_ledger_path: Path = V1_CAMPAIGN_ARTIFACT_ROOT
    / "CAMPAIGN-ATTEMPTS.jsonl",
    dispatch_root: Path = V1_CAMPAIGN_ARTIFACT_ROOT / "private-construction-dispatches",
) -> dict[str, object]:
    """Settle and cryptographically freeze the abandoned v1 paid inventory."""
    rows = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    starts = [row for row in rows if row.get("event") == "started"]
    terminals = [row for row in rows if row.get("event") == "result"]
    if len(starts) + len(terminals) != len(rows):
        raise RuntimeError("v1 abandonment ledger contains an unknown event")

    def identity(row: dict[str, object]) -> tuple[object, object]:
        return row.get("campaign_attempt"), row.get("attempt_id")

    started = {identity(row): row for row in starts}
    terminal_ids = {identity(row) for row in terminals}
    if len(started) != len(starts) or len(terminal_ids) != len(terminals):
        raise RuntimeError("v1 abandonment ledger contains duplicate attempts")
    if not terminal_ids.issubset(started):
        raise RuntimeError("v1 abandonment ledger has a terminal without a start")
    unmatched = [started[key] for key in sorted(set(started) - terminal_ids)]
    settled_nanos = sum(
        _cost_nanos_from_reported_usage(row["usage"])
        for row in terminals
        if isinstance(row.get("usage"), dict)
    )
    unmatched_reservation_nanos = sum(
        int(row["per_attempt_reservation_nanos"]) for row in unmatched
    )

    inventory = []
    captured_unmatched_responses = 0
    unmatched_keys = {row.get("extraction_key") for row in unmatched}
    kind_counts: Counter[str] = Counter()
    dispatch_pattern = re.compile(
        r"^[1-3]-([0-9a-f]{64})-(generation|response)\.json$"
    )
    for path in sorted(dispatch_root.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        match = dispatch_pattern.fullmatch(path.name)
        if match is None:
            raise RuntimeError("v1 abandonment dispatch inventory has an unknown file")
        kind = match.group(2)
        kind_counts[kind] += 1
        if kind == "response" and match.group(1) in unmatched_keys:
            captured_unmatched_responses += 1
        inventory.append(
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    expected = {
        "terminal_count": 180,
        "settled_nanos": 728_696_150,
        "unmatched_start_count": 32,
        "captured_unmatched_response_count": 2,
        "unmatched_reservation_nanos": 154_965_700,
        "total_new_liability_nanos": 883_661_850,
    }
    actual = {
        "terminal_count": len(terminals),
        "settled_nanos": settled_nanos,
        "unmatched_start_count": len(unmatched),
        "captured_unmatched_response_count": captured_unmatched_responses,
        "unmatched_reservation_nanos": unmatched_reservation_nanos,
        "total_new_liability_nanos": settled_nanos + unmatched_reservation_nanos,
    }
    if actual != expected:
        raise RuntimeError(f"v1 abandonment settlement drift: {actual!r}")
    ledger_bytes = ledger_path.read_bytes()
    campaign_ledger_bytes = campaign_ledger_path.read_bytes()
    core = {
        "schema_version": 1,
        "status": "ABANDONED_NEVER_RESUME",
        "campaign_namespace": "longmemeval-v2-pilot-v1",
        "settlement": actual,
        "ledger": {
            "relative_path": str(ledger_path.relative_to(ROOT)),
            "bytes": len(ledger_bytes),
            "line_count": len(rows),
            "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        },
        "campaign_ledger": {
            "relative_path": str(campaign_ledger_path.relative_to(ROOT)),
            "bytes": len(campaign_ledger_bytes),
            "line_count": len(campaign_ledger_bytes.splitlines()),
            "sha256": hashlib.sha256(campaign_ledger_bytes).hexdigest(),
        },
        "private_dispatch_inventory": {
            "relative_root": str(dispatch_root.relative_to(ROOT)),
            "file_count": len(inventory),
            "generation_count": kind_counts["generation"],
            "response_count": kind_counts["response"],
            "inventory_sha256": sha256_json(inventory),
        },
        "unmatched_attempt_inventory_sha256": sha256_json(
            sorted(
                (
                    {
                        "attempt_id": row["attempt_id"],
                        "campaign_attempt": row["campaign_attempt"],
                        "extraction_key": row["extraction_key"],
                        "per_attempt_reservation_nanos": row[
                            "per_attempt_reservation_nanos"
                        ],
                        "request_sha256": row["request_sha256"],
                    }
                    for row in unmatched
                ),
                key=lambda row: (row["campaign_attempt"], row["attempt_id"]),
            )
        ),
        "privacy": {
            "private_bodies_committed": False,
            "large_input_committed": False,
            "inventory_hashes_only": True,
        },
    }
    return {**core, "proof_sha256": sha256_json(core)}


def _opening_reservations() -> list[dict[str, object]]:
    evidence = [
        (
            "stale-settled-cost",
            424_530_400,
            ROOT / "docs/build-log/artifacts/state-memory-sota/stale-pilot/run-2/current/proof.json.attempts.json",
            ROOT / "docs/build-log/artifacts/state-memory-sota/stale-pilot/AUTHORIZATION-2-CLOSURE.json",
        ),
        (
            "stale-deep-unreconciled-liability",
            3_600_000_000,
            ROOT / "docs/build-log/artifacts/state-memory-sota/stale-pilot/run-2/current/proof.json",
            ROOT / "docs/build-log/2026-07-25-state-memory-terminal-reconciliation.md",
        ),
        (
            "structured-http400-unpriced-liability",
            233_472_000,
            ROOT / "docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/FEASIBILITY.json",
            ROOT / "docs/build-log/2026-07-25-state-memory-terminal-reconciliation.md",
        ),
        (
            "longmemeval-v2-v1-abandoned-liability",
            883_661_850,
            V1_ABANDONMENT_PROOF,
            V1_ABANDONMENT_PROOF,
        ),
    ]
    reservations = []
    for reservation_id, amount, receipt, proof in evidence:
        if not receipt.is_file() or not proof.is_file():
            raise RuntimeError("campaign opening-liability evidence is missing")
        reservations.append(
            {
                "reservation_id": reservation_id,
                "reserved_nanos": amount,
                "receipt_sha256": _sha256_file(receipt),
                "proof_sha256": _sha256_file(proof),
            }
        )
    if sum(item["reserved_nanos"] for item in reservations) != OPENING_NANOS:
        raise RuntimeError("campaign opening-liability evidence does not sum exactly")
    return reservations


def _build_campaign_authorization(
    census: dict[str, object],
    census_path: Path,
    manifest_path: Path,
    refresh: dict[str, object],
    artifact_root: Path,
    opening_reservations: list[dict[str, object]],
) -> dict[str, object]:
    _validate_census_hash(census, label="campaign")
    construction = census.get("construction")
    admission = census.get("admission")
    if (
        not isinstance(construction, dict)
        or not isinstance(admission, dict)
        or admission.get("authorized") is not True
        or admission.get("total_nanos") > HARD_CEILING_NANOS
        or census.get("paid_models_run") is not False
        or census.get("spend_nanos") != 0
    ):
        raise RuntimeError("campaign authorization requires admitted zero-spend census")
    plans = construction.get("plan_inventory")
    normalized, first_reservation, plans_sha256 = _plan_inventory(plans)
    if (
        normalized != plans
        or plans_sha256 != construction.get("plan_inventory_sha256")
        or len(normalized) != construction.get("processed_plans")
        or first_reservation != construction.get("first_attempt_liability_nanos")
    ):
        raise RuntimeError("campaign authorization plan inventory drift")
    construction_canary = derive_construction_canary(normalized)
    if (
        construction.get("construction_canary") != construction_canary
        or construction_canary["liability"][
            "maximum_transient_retry_reservation_nanos"
        ]
        > construction.get("retry_pool_nanos", -1)
    ):
        raise RuntimeError("campaign authorization construction canary drift")
    normalized_refresh = refresh.get("normalized")
    if (
        not isinstance(normalized_refresh, dict)
        or refresh.get("normalized_sha256") != sha256_json(normalized_refresh)
        or normalized_refresh.get("qwen_deepinfra", {}).get("requested_model")
        != construction.get("requested_model")
        or normalized_refresh.get("qwen_deepinfra", {}).get("response_model")
        != construction.get("response_model")
        or normalized_refresh.get("qwen_deepinfra", {}).get("provider").casefold()
        != construction.get("requested_provider", "").casefold()
    ):
        raise RuntimeError("campaign provider refresh differs from census route")
    paths = _campaign_artifact_paths(artifact_root)
    campaign = {
        "journal_path": Path(paths["journal"]).name,
        "hard_ceiling_nanos": HARD_CEILING_NANOS,
        "opening_liability_nanos": OPENING_NANOS,
        "unallocated_reserve_nanos": CONTINGENCY_NANOS,
        "opening_reservations": opening_reservations,
        "aggregate_construction_reservation_nanos": construction[
            "construction_liability_nanos"
        ],
    }
    scope = {
        "campaign": campaign,
        "inputs": {
            "census_path": str(census_path.resolve()),
            "census_file_sha256": _sha256_file(census_path),
            "census_sha256": census["census_sha256"],
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": _sha256_file(manifest_path),
            "plan_inventory_sha256": plans_sha256,
            "plan_count": len(normalized),
        },
        "provider_authority": refresh,
        "artifacts": paths,
        "execution": {
            "construction_max_workers": 32,
            "construction_hidden_retries": 0,
            "reader_max_workers": 8,
            "sealed_prefix_count": 12,
            "remaining_count": 439,
            "official_question_count": QUESTION_COUNT,
            "cache_namespace": "longmemeval-v2-construction-v2",
            "resume_key": "extraction_key",
            "construction_canary": construction_canary,
        },
    }
    return {
        "schema_version": 1,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        **scope,
        "authorization": {"authorization_scope_sha256": sha256_json(scope)},
    }


def _create_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise RuntimeError(f"immutable campaign artifact already exists: {path}") from error


def _atomically_create_json(path: Path, value: dict[str, object]) -> None:
    """Publish a fully fsynced immutable file with one create-only link."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise RuntimeError(
                f"immutable campaign artifact already exists: {path}"
            ) from error
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def mint_campaign_authorization() -> dict[str, object]:
    """Mint the sole immutable packet after a live public provider refresh."""
    census = validate_campaign_authorization(
        CANONICAL_CAMPAIGN_CENSUS, CANONICAL_CAMPAIGN_MANIFEST
    )
    refresh = refresh_campaign_provider_authority()
    packet = _build_campaign_authorization(
        census,
        CANONICAL_CAMPAIGN_CENSUS,
        CANONICAL_CAMPAIGN_MANIFEST,
        refresh,
        CAMPAIGN_ARTIFACT_ROOT,
        _opening_reservations(),
    )
    _create_json(CANONICAL_CAMPAIGN_AUTHORIZATION, packet)
    return packet


def _create_or_validate_json(path: Path, value: object) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise RuntimeError(f"immutable campaign artifact drift: {path}")
        return
    _create_json(path, value)


def _ledger_prefix_identity(path: Path) -> dict[str, object]:
    body = path.read_bytes() if path.is_file() else b""
    return {
        "path": str(path.resolve()),
        "prefix_bytes": len(body),
        "prefix_sha256": hashlib.sha256(body).hexdigest(),
    }


def _build_construction_binding(
    *,
    authorization_path: Path,
    census_path: Path,
    manifest_path: Path,
    wave_path: Path,
    binding_root: Path,
    plans: list[dict[str, object]],
) -> tuple[Path, dict[str, object]]:
    """Derive the sole pre-worker binding from frozen campaign authorities."""
    packet = json.loads(authorization_path.read_text(encoding="utf-8"))
    census = json.loads(census_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wave = json.loads(wave_path.read_text(encoding="utf-8"))
    scope = {
        key: value
        for key, value in packet.items()
        if key not in {"schema_version", "status", "authorization"}
    }
    authorization_sha256 = packet.get("authorization", {}).get(
        "authorization_scope_sha256"
    )
    normalized, _, plan_subset_sha256 = _plan_inventory(plans)
    construction = census.get("construction", {})
    manifest_construction = manifest.get("construction", {})
    inventory = construction.get("plan_inventory")
    inventory_by_key = {
        plan.get("extraction_key"): plan for plan in inventory or []
    }
    artifacts = packet.get("artifacts")
    if (
        packet.get("status") != "AUTHORIZED_STATE_MEMORY_CAMPAIGN"
        or authorization_sha256 != sha256_json(scope)
        or census.get("census_sha256")
        != sha256_json({key: value for key, value in census.items() if key != "census_sha256"})
        or packet.get("inputs", {}).get("census_sha256") != census.get("census_sha256")
        or packet.get("inputs", {}).get("census_file_sha256") != _sha256_file(census_path)
        or packet.get("inputs", {}).get("manifest_sha256") != _sha256_file(manifest_path)
        or census.get("manifest_sha256") != _sha256_file(manifest_path)
        or not isinstance(artifacts, dict)
        or not isinstance(inventory, list)
        or construction.get("plan_inventory_sha256") != sha256_json(inventory)
        or len(inventory_by_key) != len(inventory)
        or any(inventory_by_key.get(plan["extraction_key"]) != plan for plan in normalized)
        or wave.get("plans") != inventory
        or wave.get("ordered_plans_sha256") != construction.get("plan_inventory_sha256")
        or wave.get("campaign_census_sha256") != census.get("census_sha256")
    ):
        raise RuntimeError("construction binding authority chain drift")
    wave_core = {
        key: value
        for key, value in wave.items()
        if key not in {"wave_sha256", "ledger_request_key"}
    }
    if wave.get("wave_sha256") != sha256_json(wave_core):
        raise RuntimeError("construction binding wave identity drift")
    expected_paths = {
        "authorization_path": authorization_path.resolve(),
        "census_path": census_path.resolve(),
        "manifest_path": manifest_path.resolve(),
        "wave_path": wave_path.resolve(),
        "binding_root": binding_root.resolve(),
    }
    subledger = Path(artifacts["construction_subledger"]).resolve()
    campaign_journal = Path(artifacts["journal"]).resolve()
    cache_root = Path(artifacts["cache_hits"]).resolve()
    observation_cache = Path(artifacts["observation_cache"]).resolve()
    source_receipts = cache_root / plan_subset_sha256
    source_receipts.mkdir(parents=True, exist_ok=True)
    prefix = _ledger_prefix_identity(subledger)
    binding_path = binding_root.resolve() / f"{plan_subset_sha256}.json"
    runtime_path = "crates/memphant-runtime/src/structured_state_openrouter.rs"
    provider_code_sha256 = manifest_construction.get("code_sha256s", {}).get(runtime_path)
    if not _valid_sha256(provider_code_sha256):
        raise RuntimeError("construction binding provider compiler identity is missing")
    schema_authority = {
        "construction_identity_sha256": construction.get("construction_identity_sha256"),
        "provider_code_sha256": provider_code_sha256,
        "contract": "structured-state-response-schema-v1",
    }
    authority = {
        "authorization_path": str(expected_paths["authorization_path"]),
        "authorization_file_sha256": _sha256_file(authorization_path),
        "authorization_scope_sha256": authorization_sha256,
        "census_path": str(expected_paths["census_path"]),
        "census_file_sha256": _sha256_file(census_path),
        "census_sha256": census["census_sha256"],
        "manifest_path": str(expected_paths["manifest_path"]),
        "manifest_sha256": _sha256_file(manifest_path),
        "wave_path": str(expected_paths["wave_path"]),
        "wave_file_sha256": _sha256_file(wave_path),
        "wave_sha256": wave["wave_sha256"],
        "plan_inventory_sha256": construction["plan_inventory_sha256"],
        "plan_subset_sha256": plan_subset_sha256,
        "canonical_artifact_paths_sha256": sha256_json(artifacts),
        "binding_path": str(binding_path),
    }
    keys = [plan["extraction_key"] for plan in normalized]
    core = {
        "schema_version": 1,
        "authority": authority,
        "authorization": {
            "authorization_sha256": authorization_sha256,
            "campaign_sha256": census["census_sha256"],
            "screen_id": "state-aware-full",
        },
        "selection": {
            "selection_sha256": plan_subset_sha256,
            "input_manifest_sha256": construction["input_manifest_sha256"],
            "state_mode": manifest_construction["state_mode"],
        },
        "compiler": {
            "prompt_sha256": manifest_construction["prompt_sha256"],
            "schema_sha256": sha256_json(schema_authority),
            "provider_code_sha256": provider_code_sha256,
        },
        "provider": {
            "requested_model": manifest_construction["model"],
            "served_model": manifest_construction["response_model"],
            "requested_provider": manifest_construction["provider"],
            # The request policy uses the canonical lower-case provider key;
            # reconciled OpenRouter generation metadata uses this exact name.
            "served_provider": "DeepInfra",
            "input_price_nanos_per_million": manifest_construction[
                "input_price_nanos_per_million"
            ],
            "output_price_nanos_per_million": manifest_construction[
                "output_price_nanos_per_million"
            ],
            "maximum_output_tokens": manifest_construction["maximum_output_tokens"],
            "maximum_attempts": manifest_construction["maximum_attempts"],
        },
        "cache": {
            "namespace": packet["execution"]["cache_namespace"],
            "observation_cache_path": str(observation_cache),
            "source_receipts_path": str(source_receipts),
        },
        "ledger": {
            "subledger_path": str(subledger),
            "campaign_journal_path": str(campaign_journal),
            "source_ledger_prefix_bytes": prefix["prefix_bytes"],
            "source_ledger_prefix_sha256": prefix["prefix_sha256"],
            "before_event_sha256": prefix["prefix_sha256"],
            "campaign_journal_sha256": (
                _sha256_file(campaign_journal)
                if campaign_journal.is_file()
                else hashlib.sha256(b"").hexdigest()
            ),
        },
        "coverage": {
            "plans": normalized,
            "expected_extraction_keys": keys,
            "expected_extraction_keys_sha256": sha256_json(keys),
        },
    }
    return binding_path, {**core, "binding_sha256": sha256_json(core)}


def create_construction_binding(
    authorization_path: Path,
    plans: list[dict[str, object]],
) -> Path:
    """Create, once, the canonical adapter binding for an exact plan subset."""
    if authorization_path.resolve() != CANONICAL_CAMPAIGN_AUTHORIZATION.resolve():
        raise RuntimeError("construction binding requires the canonical authorization path")
    artifacts = campaign_artifact_paths()
    binding_path, binding = _build_construction_binding(
        authorization_path=authorization_path,
        census_path=CANONICAL_CAMPAIGN_CENSUS,
        manifest_path=CANONICAL_CAMPAIGN_MANIFEST,
        wave_path=Path(artifacts["construction_wave"]),
        binding_root=Path(artifacts["construction_bindings"]),
        plans=plans,
    )
    if binding_path.exists():
        if json.loads(binding_path.read_text(encoding="utf-8")) != binding:
            raise RuntimeError("immutable construction binding drift")
    else:
        _atomically_create_json(binding_path, binding)
    _load_canonical_construction_binding(
        binding_path,
        authorization_path=authorization_path,
        census_path=CANONICAL_CAMPAIGN_CENSUS,
        manifest_path=CANONICAL_CAMPAIGN_MANIFEST,
        wave_path=Path(artifacts["construction_wave"]),
        binding_root=Path(artifacts["construction_bindings"]),
    )
    return binding_path


def _prefix_source_hashes(data_root: Path, prefix_count: int) -> tuple[list[str], list[str], set[str]]:
    haystack = json.loads(
        (data_root / "haystacks/lme_v2_medium.json").read_text(encoding="utf-8")
    )
    case_order = sorted(haystack)
    if len(case_order) != QUESTION_COUNT or prefix_count != 12:
        raise RuntimeError("construction prewarm requires the sealed 12/439 split")
    prefix_ids = case_order[:prefix_count]
    trajectory_ids = {
        trajectory_id
        for question_id in prefix_ids
        for trajectory_id in haystack[question_id]
    }
    adapter = _load_adapter()
    found = set()
    source_hashes = set()
    with (data_root / "trajectories.jsonl").open(encoding="utf-8") as source:
        for line in source:
            trajectory = json.loads(line)
            trajectory_id = trajectory.get("id") if isinstance(trajectory, dict) else None
            if trajectory_id not in trajectory_ids:
                continue
            found.add(trajectory_id)
            for row in adapter.census_resource_rows(trajectory, uses=1):
                source_hashes.add(hashlib.sha256(row["source_body"].encode()).hexdigest())
    if found != trajectory_ids:
        raise RuntimeError("sealed prefix trajectory selection is incomplete")
    return prefix_ids, case_order[prefix_count:], source_hashes


def prewarm_sealed_prefix(
    authorization_path: Path,
    *,
    data_root: Path | None = None,
) -> dict[str, object]:
    """Reserve the full construction envelope once, then prewarm only prefix keys."""
    if authorization_path.resolve() != CANONICAL_CAMPAIGN_AUTHORIZATION.resolve():
        raise RuntimeError("prefix prewarm requires the canonical authorization path")
    packet = json.loads(authorization_path.read_text(encoding="utf-8"))
    scope = {
        key: value
        for key, value in packet.items()
        if key not in {"schema_version", "status", "authorization"}
    }
    authorization_sha256 = packet.get("authorization", {}).get(
        "authorization_scope_sha256"
    )
    if (
        packet.get("status") != "AUTHORIZED_STATE_MEMORY_CAMPAIGN"
        or authorization_sha256 != sha256_json(scope)
        or packet.get("artifacts") != campaign_artifact_paths()
        or packet.get("execution", {}).get("sealed_prefix_count") != 12
        or packet.get("execution", {}).get("construction_hidden_retries") != 0
    ):
        raise RuntimeError("prefix prewarm authorization is not active and canonical")
    _validate_runtime_provider_authority(packet)
    census = validate_campaign_authorization(
        CANONICAL_CAMPAIGN_CENSUS, CANONICAL_CAMPAIGN_MANIFEST
    )
    if (
        packet["inputs"]["census_file_sha256"] != _sha256_file(CANONICAL_CAMPAIGN_CENSUS)
        or packet["inputs"]["census_sha256"] != census["census_sha256"]
        or packet["inputs"]["manifest_sha256"] != _sha256_file(CANONICAL_CAMPAIGN_MANIFEST)
    ):
        raise RuntimeError("prefix prewarm input authority drift")
    paths = {key: Path(value) for key, value in packet["artifacts"].items()}
    resolved_data_root = data_root or Path(
        os.environ.get(
            "MEMPHANT_LME_V2_DATA_ROOT",
            Path.home() / ".cache/memphant/longmemeval-v2",
        )
    )
    paths["construction_input"].parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=paths["construction_input"].parent, delete=False
    ) as handle:
        temporary_input = Path(handle.name)
    try:
        enumeration = _materialize_cli_input(resolved_data_root, temporary_input)
        if enumeration["input_jsonl_sha256"] != census["construction"]["input_manifest_sha256"]:
            raise RuntimeError("prefix prewarm resource input differs from census")
        if paths["construction_input"].exists():
            if _sha256_file(paths["construction_input"]) != _sha256_file(temporary_input):
                raise RuntimeError("immutable construction resource input drift")
        else:
            os.replace(temporary_input, paths["construction_input"])
    finally:
        temporary_input.unlink(missing_ok=True)
    prefix_ids, remaining_ids, prefix_source_hashes = _prefix_source_hashes(
        resolved_data_root, 12
    )
    plans = census["construction"]["plan_inventory"]
    construction_canary = census["construction"]["construction_canary"]
    if packet["execution"].get("construction_canary") != construction_canary:
        raise RuntimeError("prefix prewarm canary authority drift")
    canary_plans = construction_canary["plans"]
    canary_keys = {
        (plan["extraction_key"], plan["request_sha256"]) for plan in canary_plans
    }
    prefix_candidates = [
        plan for plan in plans if plan.get("source_body_sha256") in prefix_source_hashes
    ]
    if not prefix_candidates:
        raise RuntimeError("sealed prefix plan subset is empty or foreign")
    prefix_plans = [
        plan
        for plan in prefix_candidates
        if (plan["extraction_key"], plan["request_sha256"]) not in canary_keys
    ]
    _create_or_validate_json(paths["prefix_plans"], prefix_plans)
    _create_or_validate_json(paths["construction_canary_plans"], canary_plans)
    execution_plan = build_execution_plan(census, resolved_data_root)
    reservation_plan = build_row_reservation_plan(execution_plan, census)
    remaining = build_remaining_commitment(execution_plan, reservation_plan)
    _create_or_validate_json(paths["execution_plan"], execution_plan)
    _create_or_validate_json(paths["reservation_plan"], reservation_plan)
    _create_or_validate_json(paths["remaining_commitment"], remaining)
    sys.path.insert(0, str(ROOT / "scripts"))
    from provider_attempts import open_campaign_ledger

    ledger = open_campaign_ledger(
        authorization_path,
        screen_id="longmemeval-v2-state-aware",
        expected_journal_path=paths["journal"],
    )
    manifest = json.loads(CANONICAL_CAMPAIGN_MANIFEST.read_text(encoding="utf-8"))
    construction = manifest["construction"]
    binary_sha256 = census["construction"]["census_binary_provenance"]["binary_sha256"]
    binary = ROOT / "target/state-memory-census-bin" / binary_sha256 / (
        "memphant-cli.exe" if sys.platform == "win32" else "memphant-cli"
    )
    if not binary.is_file() or _sha256_file(binary) != binary_sha256:
        raise RuntimeError("authorized construction binary is unavailable")

    def launch() -> None:
        source_ledger = _ledger_prefix_identity(paths["construction_subledger"])
        environment = os.environ.copy()
        environment.update(
            {
                "MEMPHANT_STRUCTURED_STATE": "on",
                "MEMPHANT_STRUCTURED_STATE_MODEL": construction["model"],
                "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT": construction[
                    "reasoning_effort"
                ],
                "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH": str(ROOT / construction["prompt_path"]),
                "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION": str(construction["input_price_nanos_per_million"]),
                "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION": str(construction["output_price_nanos_per_million"]),
                "MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH": str(resolved_data_root / construction["tokenizer_path"]),
                "MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH": str(resolved_data_root / construction["tokenizer_config_path"]),
                "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER": str(paths["construction_subledger"]),
                "MEMPHANT_STRUCTURED_STATE_DISPATCH_ROOT": str(paths["construction_dispatches"]),
                "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE": str(paths["observation_cache"]),
                "MEMPHANT_STRUCTURED_STATE_CACHE_HITS": str(paths["cache_hits"]),
                "MEMPHANT_STRUCTURED_STATE_AUTHORIZATION_SHA256": authorization_sha256,
                "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_SHA256": census["census_sha256"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_NAMESPACE": packet["execution"]["cache_namespace"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER": source_ledger["path"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_BYTES": str(source_ledger["prefix_bytes"]),
                "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_SHA256": source_ledger["prefix_sha256"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_MODEL": construction["response_model"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_PROVIDER": "DeepInfra",
                "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS": str(census["construction"]["construction_liability_nanos"]),
                "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT": "1",
                "MEMPHANT_STRUCTURED_STATE_CACHE_ONLY": "off",
            }
        )

        def execute_subset(plans_path: Path, progress_path: Path) -> None:
            if progress_path.is_file():
                return
            completed = subprocess.run(
                [
                    str(binary), "structured-state", "execute",
                    "--input-jsonl", str(paths["construction_input"]),
                    "--allowed-plans-json", str(plans_path),
                    "--max-workers", str(packet["execution"]["construction_max_workers"]),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "construction prewarm failed: " + completed.stderr.strip()
                )
            _atomically_create_json(
                progress_path,
                {
                    "processor_result": json.loads(completed.stdout),
                    "authorization_sha256": authorization_sha256,
                    "campaign_census_sha256": census["census_sha256"],
                },
            )

        gate_path = paths["construction_canary_gate"]
        if gate_path.is_file():
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            rebound = bind_construction_canary_cache(
                gate, construction_canary, paths["observation_cache"]
            )
            if gate.get("accepted") is not True or rebound != gate:
                raise RuntimeError("construction canary is rejected or has drifted")
        else:
            execute_subset(
                paths["construction_canary_plans"],
                paths["construction_canary_progress"],
            )
            gate = evaluate_construction_canary(
                construction_canary,
                _construction_subledger_events(paths["construction_subledger"]),
            )
            if (
                not gate["semantic_failure_count"]
                and not gate["incomplete_failure_count"]
                and gate["transient_pending_count"]
            ):
                execute_construction_retry_shard(
                    authorization_path,
                    data_root=resolved_data_root,
                    eligible_plans=gate["transient_pending_plans"],
                    phase="canary",
                )
                gate = evaluate_construction_canary(
                    construction_canary,
                    _construction_subledger_events(paths["construction_subledger"]),
                )
            gate = bind_construction_canary_cache(
                gate, construction_canary, paths["observation_cache"]
            )
            _atomically_create_json(gate_path, gate)
            if gate["accepted"] is not True:
                raise RuntimeError("construction canary rejected the campaign")
        if prefix_plans:
            execute_subset(paths["prefix_plans"], paths["construction_progress"])

    wave = authorize_or_resume_construction_wave(
        ledger, census, plans, paths["construction_wave"], launch=launch
    )
    return {
        "schema_version": 1,
        "phase": "sealed-prefix-construction-prewarm",
        "wave_sha256": wave["wave_sha256"],
        "prefix_plan_count": len(prefix_plans),
        "remaining_count": len(remaining_ids),
        "progress_path": str(paths["construction_progress"]),
    }


def prewarm_remaining_construction(
    authorization_path: Path,
    *,
    data_root: Path,
) -> dict[str, object]:
    """Populate the exact committed 439 tail inside the original reservation."""
    if authorization_path.resolve() != CANONICAL_CAMPAIGN_AUTHORIZATION.resolve():
        raise RuntimeError("remaining prewarm requires canonical authorization")
    packet = json.loads(authorization_path.read_text(encoding="utf-8"))
    paths = {key: Path(value) for key, value in packet.get("artifacts", {}).items()}
    if packet.get("artifacts") != campaign_artifact_paths():
        raise RuntimeError("remaining prewarm artifact authority drift")
    _validate_runtime_provider_authority(packet)
    status = json.loads(paths["public_prefix_status"].read_text(encoding="utf-8"))
    validate_public_prefix_status(status)
    execution_plan = json.loads(paths["execution_plan"].read_text(encoding="utf-8"))
    reservation_plan = json.loads(paths["reservation_plan"].read_text(encoding="utf-8"))
    commitment = json.loads(paths["remaining_commitment"].read_text(encoding="utf-8"))
    _validate_remaining_commitment(commitment, reservation_plan)
    if (
        status["remaining_commitment_sha256"]
        != commitment["remaining_commitment_sha256"]
        or status["execution_plan_sha256"]
        != execution_plan["execution_plan_sha256"]
    ):
        raise RuntimeError("remaining prewarm differs from sealed prefix authority")
    census = validate_campaign_authorization(
        CANONICAL_CAMPAIGN_CENSUS, CANONICAL_CAMPAIGN_MANIFEST
    )
    prefix_ids, remaining_ids, prefix_hashes = _prefix_source_hashes(data_root, 12)
    del prefix_ids, remaining_ids
    plans = census["construction"]["plan_inventory"]
    canary_keys = {
        (plan["extraction_key"], plan["request_sha256"])
        for plan in census["construction"]["construction_canary"]["plans"]
    }
    remaining_plans = [
        plan
        for plan in plans
        if plan.get("source_body_sha256") not in prefix_hashes
        and (plan["extraction_key"], plan["request_sha256"]) not in canary_keys
    ]
    if not remaining_plans or len(remaining_plans) >= len(plans):
        raise RuntimeError("remaining construction subset is invalid")
    progress_path = paths["remaining_construction_progress"]
    if progress_path.is_file():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if (
            progress.get("status") != "REMAINING_CONSTRUCTION_COMPLETE"
            or progress.get("plans_sha256") != sha256_json(remaining_plans)
        ):
            raise RuntimeError("remaining construction checkpoint drift")
        return progress
    manifest = json.loads(CANONICAL_CAMPAIGN_MANIFEST.read_text(encoding="utf-8"))
    construction = manifest["construction"]
    binary_sha256 = census["construction"]["census_binary_provenance"]["binary_sha256"]
    binary = ROOT / "target/state-memory-census-bin" / binary_sha256 / (
        "memphant-cli.exe" if sys.platform == "win32" else "memphant-cli"
    )
    if not binary.is_file() or _sha256_file(binary) != binary_sha256:
        raise RuntimeError("authorized construction binary is unavailable")
    remaining_plans_path = paths["remaining_construction_plans"]
    _create_or_validate_json(remaining_plans_path, remaining_plans)
    source_ledger = _ledger_prefix_identity(paths["construction_subledger"])
    environment = os.environ.copy()
    environment.update(
        {
            "MEMPHANT_STRUCTURED_STATE": "on",
            "MEMPHANT_STRUCTURED_STATE_MODEL": construction["model"],
            "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT": construction[
                "reasoning_effort"
            ],
            "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH": str(ROOT / construction["prompt_path"]),
            "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION": str(construction["input_price_nanos_per_million"]),
            "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION": str(construction["output_price_nanos_per_million"]),
            "MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH": str(data_root / construction["tokenizer_path"]),
            "MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH": str(data_root / construction["tokenizer_config_path"]),
            "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER": str(paths["construction_subledger"]),
            "MEMPHANT_STRUCTURED_STATE_DISPATCH_ROOT": str(paths["construction_dispatches"]),
            "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE": str(paths["observation_cache"]),
            "MEMPHANT_STRUCTURED_STATE_CACHE_HITS": str(paths["cache_hits"]),
            "MEMPHANT_STRUCTURED_STATE_AUTHORIZATION_SHA256": packet["authorization"]["authorization_scope_sha256"],
            "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_SHA256": census["census_sha256"],
            "MEMPHANT_STRUCTURED_STATE_CACHE_NAMESPACE": packet["execution"]["cache_namespace"],
            "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER": source_ledger["path"],
            "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_BYTES": str(source_ledger["prefix_bytes"]),
            "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_SHA256": source_ledger["prefix_sha256"],
            "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_MODEL": construction["response_model"],
            "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_PROVIDER": "DeepInfra",
            "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS": str(census["construction"]["construction_liability_nanos"]),
            "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT": "1",
            "MEMPHANT_STRUCTURED_STATE_CACHE_ONLY": "off",
        }
    )
    completed = subprocess.run(
        [
            str(binary), "structured-state", "execute",
            "--input-jsonl", str(paths["construction_input"]),
            "--allowed-plans-json", str(remaining_plans_path),
            "--max-workers", str(packet["execution"]["construction_max_workers"]),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("remaining construction prewarm failed")
    core = {
        "schema_version": 1,
        "status": "REMAINING_CONSTRUCTION_COMPLETE",
        "plans_sha256": sha256_json(remaining_plans),
        "plan_count": len(remaining_plans),
        "remaining_commitment_sha256": commitment["remaining_commitment_sha256"],
        "processor_result_sha256": sha256_json(json.loads(completed.stdout)),
    }
    progress = {**core, "progress_sha256": sha256_json(core)}
    _atomically_create_json(progress_path, progress)
    return progress


def _construction_subledger_events(path: Path) -> list[dict[str, object]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("construction subledger is unavailable") from error


def _failed_construction_plans(
    plans: list[dict[str, object]],
    events: list[dict[str, object]],
    campaign_attempt: int,
) -> list[dict[str, object]]:
    by_key = {
        (plan["extraction_key"], plan["request_sha256"]): plan for plan in plans
    }
    results: dict[tuple[object, object], dict[str, object]] = {}
    starts: set[tuple[object, object]] = set()
    for event in events:
        if event.get("campaign_attempt") != campaign_attempt:
            continue
        key = (event.get("extraction_key"), event.get("request_sha256"))
        if key not in by_key:
            continue
        if event.get("event") == "started":
            if key in starts:
                raise RuntimeError("construction retry duplicated a planned start")
            starts.add(key)
        elif event.get("event") == "result":
            if key in results:
                raise RuntimeError("construction retry duplicated a planned result")
            results[key] = event
    if starts != set(by_key) or set(results) != set(by_key):
        raise RuntimeError("construction wave is incomplete and must resume in place")
    unresolved = [
        key
        for key, result in results.items()
        if result.get("reservation_status") == "unresolved"
    ]
    if unresolved:
        raise RuntimeError(
            "construction wave contains an ambiguous non-dispatchable attempt; "
            "manual adjudication is required"
        )
    return [
        plan
        for plan in plans
        if results[(plan["extraction_key"], plan["request_sha256"])].get(
            "parse_status"
        )
        != "decoded"
    ]


def execute_construction_retry_shard(
    authorization_path: Path,
    *,
    data_root: Path,
    eligible_plans: list[dict[str, object]],
    phase: str,
) -> list[dict[str, object]]:
    """Retry only exact failed construction keys within the frozen retry pool."""
    packet = json.loads(authorization_path.read_text(encoding="utf-8"))
    if packet.get("artifacts") != campaign_artifact_paths():
        raise RuntimeError("construction retry artifact authority drift")
    _validate_runtime_provider_authority(packet)
    paths = {key: Path(value) for key, value in packet["artifacts"].items()}
    wave = json.loads(paths["construction_wave"].read_text(encoding="utf-8"))
    census = validate_campaign_authorization(
        CANONICAL_CAMPAIGN_CENSUS, CANONICAL_CAMPAIGN_MANIFEST
    )
    manifest = json.loads(CANONICAL_CAMPAIGN_MANIFEST.read_text(encoding="utf-8"))
    construction = manifest["construction"]
    binary_sha256 = census["construction"]["census_binary_provenance"]["binary_sha256"]
    binary = ROOT / "target/state-memory-census-bin" / binary_sha256 / (
        "memphant-cli.exe" if sys.platform == "win32" else "memphant-cli"
    )
    if not binary.is_file() or _sha256_file(binary) != binary_sha256:
        raise RuntimeError("authorized construction binary is unavailable")
    if phase not in {"canary", "prefix", "tail"}:
        raise RuntimeError("construction retry phase is invalid")
    wave_keys = {
        (plan["extraction_key"], plan["request_sha256"]) for plan in wave["plans"]
    }
    if not eligible_plans or any(
        (plan["extraction_key"], plan["request_sha256"]) not in wave_keys
        for plan in eligible_plans
    ):
        raise RuntimeError("construction retry shard contains foreign plans")
    retries: list[dict[str, object]] = []
    candidate_plans = eligible_plans
    campaign_attempts = (2,) if phase == "canary" else (2, 3)
    for campaign_attempt in campaign_attempts:
        events = _construction_subledger_events(paths["construction_subledger"])
        failed = _failed_construction_plans(
            candidate_plans, events, campaign_attempt - 1
        )
        if not failed:
            return retries
        retry = plan_construction_retry_wave(wave, failed, retries)
        retry_root = paths["construction_retries"]
        retry_root.mkdir(parents=True, exist_ok=True)
        retry_path = retry_root / f"{phase}-ATTEMPT-{campaign_attempt}.json"
        plans_path = retry_root / f"{phase}-ATTEMPT-{campaign_attempt}-PLANS.json"
        progress_path = retry_root / f"{phase}-ATTEMPT-{campaign_attempt}-PROGRESS.json"
        shard_core = {
            "schema_version": 1,
            "phase": phase,
            "campaign_attempt": campaign_attempt,
            "campaign_wave_sha256": wave["wave_sha256"],
            "reservation_nanos": retry["reservation_nanos"],
            "plans": failed,
            "plans_sha256": sha256_json(failed),
        }
        shard = {**shard_core, "shard_sha256": sha256_json(shard_core)}
        _create_or_validate_json(retry_path, shard)
        _create_or_validate_json(plans_path, failed)
        other_shards = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in retry_root.glob("*-ATTEMPT-[23].json")
            if path != retry_path
        ]
        if sum(item["reservation_nanos"] for item in other_shards) + retry["reservation_nanos"] > wave["retry_pool_nanos"]:
            raise RuntimeError("construction retry shards exceed prepaid pool")
        if progress_path.is_file():
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            if progress.get("shard_sha256") != shard["shard_sha256"]:
                raise RuntimeError("construction retry progress drift")
        else:
            source_ledger = _ledger_prefix_identity(paths["construction_subledger"])
            environment = os.environ.copy()
            environment.update(
                {
                    "MEMPHANT_STRUCTURED_STATE": "on",
                    "MEMPHANT_STRUCTURED_STATE_MODEL": construction["model"],
                    "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT": construction[
                        "reasoning_effort"
                    ],
                    "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH": str(ROOT / construction["prompt_path"]),
                    "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION": str(construction["input_price_nanos_per_million"]),
                    "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION": str(construction["output_price_nanos_per_million"]),
                    "MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH": str(data_root / construction["tokenizer_path"]),
                    "MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH": str(data_root / construction["tokenizer_config_path"]),
                    "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER": str(paths["construction_subledger"]),
                    "MEMPHANT_STRUCTURED_STATE_DISPATCH_ROOT": str(paths["construction_dispatches"]),
                    "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE": str(paths["observation_cache"]),
                    "MEMPHANT_STRUCTURED_STATE_CACHE_HITS": str(paths["cache_hits"]),
                    "MEMPHANT_STRUCTURED_STATE_AUTHORIZATION_SHA256": packet["authorization"]["authorization_scope_sha256"],
                    "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_SHA256": census["census_sha256"],
                    "MEMPHANT_STRUCTURED_STATE_CACHE_NAMESPACE": packet["execution"]["cache_namespace"],
                    "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER": source_ledger["path"],
                    "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_BYTES": str(source_ledger["prefix_bytes"]),
                    "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_SHA256": source_ledger["prefix_sha256"],
                    "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_MODEL": construction["response_model"],
                    "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_PROVIDER": "DeepInfra",
                    "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS": str(wave["aggregate_reservation_nanos"]),
                    "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT": str(campaign_attempt),
                    "MEMPHANT_STRUCTURED_STATE_CACHE_ONLY": "off",
                }
            )
            completed = subprocess.run(
                [
                    str(binary), "structured-state", "execute",
                    "--input-jsonl", str(paths["construction_input"]),
                    "--allowed-plans-json", str(plans_path),
                    "--max-workers", str(packet["execution"]["construction_max_workers"]),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("construction retry execution failed")
            _atomically_create_json(
                progress_path,
                {
                    "schema_version": 1,
                    "shard_sha256": shard["shard_sha256"],
                    "processor_result_sha256": sha256_json(json.loads(completed.stdout)),
                },
            )
        retries.append(retry)
        candidate_plans = failed
    events = _construction_subledger_events(paths["construction_subledger"])
    if phase != "canary" and _failed_construction_plans(
        candidate_plans, events, campaign_attempts[-1]
    ):
        raise RuntimeError("construction retry pool exhausted with failed keys")
    return retries


def construction_retry_wave_unions(
    paths: dict[str, Path], wave: dict[str, object]
) -> list[dict[str, object]]:
    retry_root = paths["construction_retries"]
    prior: list[dict[str, object]] = []
    ordered = {
        (plan["extraction_key"], plan["request_sha256"]): index
        for index, plan in enumerate(wave["plans"])
    }
    for campaign_attempt in (2, 3):
        shards = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in retry_root.glob(f"*-ATTEMPT-{campaign_attempt}.json")
        ] if retry_root.is_dir() else []
        if not shards:
            break
        plans = [plan for shard in shards for plan in shard["plans"]]
        keys = [(plan["extraction_key"], plan["request_sha256"]) for plan in plans]
        if len(keys) != len(set(keys)):
            raise RuntimeError("construction retry shards overlap")
        plans.sort(key=lambda plan: ordered[(plan["extraction_key"], plan["request_sha256"])])
        union = plan_construction_retry_wave(wave, plans, prior)
        if union["campaign_attempt"] != campaign_attempt:
            raise RuntimeError("construction retry union attempt drift")
        prior.append(union)
    return prior


def authorize_construction_wave(
    ledger: object,
    census_path: Path,
    manifest_path: Path,
    plans: list[dict[str, object]],
    *,
    wave_kind: str,
    launch,
) -> dict[str, object]:
    """Validate immutable authority, reserve, then invoke the launcher."""
    census = validate_campaign_authorization(census_path, manifest_path)
    return _authorize_validated_construction_wave(
        ledger,
        census,
        plans,
        wave_kind=wave_kind,
        launch=launch,
    )


def _authorize_validated_construction_wave(
    ledger: object,
    census: dict[str, object],
    plans: list[dict[str, object]],
    *,
    wave_kind: str,
    launch,
) -> dict[str, object]:
    """Reserve an aggregate wave before invoking a credential-bearing launcher."""
    wave = _build_validated_construction_wave(census, plans, wave_kind=wave_kind)
    request_key = f"state-aware-construction-wave:{wave['wave_sha256']}"
    ledger.record("start", request_key, _construction_start_payload(wave))
    launch()
    return {**wave, "ledger_request_key": request_key}


def _build_validated_construction_wave(
    census: dict[str, object],
    plans: list[dict[str, object]],
    *,
    wave_kind: str,
) -> dict[str, object]:
    """Build the deterministic full reservation artifact without side effects."""
    _validate_census_hash(census, label="campaign")
    if census.get("paid_models_run") is not False or census.get("spend_nanos") != 0:
        raise RuntimeError("construction wave requires a no-model campaign census")
    admission = census.get("admission")
    construction = census.get("construction")
    if not isinstance(admission, dict) or admission.get("authorized") is not True:
        raise RuntimeError("construction wave requires an admitted campaign census")
    if not isinstance(construction, dict) or construction.get("maximum_internal_attempts") != 1:
        raise RuntimeError("construction wave requires the single-attempt runtime contract")
    reader_inventory = census.get("liability_derivation", {}).get(
        "reader_inventory", {}
    )
    if (
        not isinstance(reader_inventory, dict)
        or not _valid_sha256(reader_inventory.get("inventory_sha256"))
        or reader_inventory.get("inventory_sha256")
        != sha256_json(reader_inventory.get("rows"))
        or reader_inventory.get("row_count") != QUESTION_COUNT
    ):
        raise RuntimeError("construction wave requires the exact reader inventory")
    if wave_kind != "first_attempt":
        raise RuntimeError("the sole campaign-ledger start must be the first construction wave")
    normalized, reservation, plans_sha256 = _plan_inventory(plans)
    expected_model = construction.get("requested_model")
    expected_response_model = construction.get("response_model")
    expected_provider = construction.get("requested_provider")
    campaign_waves = construction.get("maximum_attempts")
    if (
        not isinstance(expected_model, str)
        or not isinstance(expected_response_model, str)
        or not isinstance(expected_provider, str)
        or type(campaign_waves) is not int
        or campaign_waves != 3
        or any(
            plan["requested_model"] != expected_model
            or plan["maximum_attempts"] != campaign_waves
            for plan in normalized
        )
    ):
        raise RuntimeError("construction wave route or campaign-attempt identity drift")
    if (
        reservation != construction.get("first_attempt_liability_nanos")
        or len(normalized) != construction.get("processed_plans")
        or (
            construction.get("plan_inventory_sha256") is not None
            and plans_sha256 != construction.get("plan_inventory_sha256")
        )
    ):
        raise RuntimeError("first construction wave differs from the exact census")
    aggregate_reservation = construction.get("construction_liability_nanos")
    retry_pool = construction.get("retry_pool_nanos")
    if (
        type(aggregate_reservation) is not int
        or type(retry_pool) is not int
        or aggregate_reservation != reservation + retry_pool
    ):
        raise RuntimeError("construction aggregate reservation does not bind its retry pool")
    wave_core = {
        "schema_version": 1,
        "campaign_census_sha256": census["census_sha256"],
        "reader_liability_inventory_sha256": reader_inventory[
            "inventory_sha256"
        ],
        "reader_arm_liability_nanos": reader_inventory[
            "reader_arm_liability_nanos"
        ],
        "construction_identity_sha256": construction["construction_identity_sha256"],
        "wave_kind": wave_kind,
        "plan_count": len(normalized),
        "ordered_plans_sha256": plans_sha256,
        "reservation_nanos": reservation,
        "campaign_attempt": 1,
        "maximum_attempts": campaign_waves,
        "requested_model": expected_model,
        "response_model": expected_response_model,
        "requested_provider": expected_provider,
        "retry_pool_nanos": retry_pool,
        "aggregate_reservation_nanos": aggregate_reservation,
        "required_launch_env": {
            "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS": str(aggregate_reservation),
            "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT": "1",
        },
        "plans": normalized,
    }
    wave = {**wave_core, "wave_sha256": sha256_json(wave_core)}
    return wave


def _construction_start_payload(wave: dict[str, object]) -> dict[str, object]:
    return {
        "max_liability_nanos": wave["aggregate_reservation_nanos"],
        "retry_index": 0,
        "context": {
            "campaign_census_sha256": wave["campaign_census_sha256"],
            "reader_liability_inventory_sha256": wave[
                "reader_liability_inventory_sha256"
            ],
            "reader_arm_liability_nanos": wave["reader_arm_liability_nanos"],
            "wave_kind": wave["wave_kind"],
            "ordered_plans_sha256": wave["ordered_plans_sha256"],
            "plan_count": wave["plan_count"],
            "campaign_attempt": 1,
            "retry_pool_nanos": wave["retry_pool_nanos"],
        },
    }


def authorize_or_resume_construction_wave(
    ledger: object,
    census: dict[str, object],
    plans: list[dict[str, object]],
    wave_path: Path,
    *,
    launch,
) -> dict[str, object]:
    """Create+fsync authority, reserve once, and resume the same launch safely."""
    wave = _build_validated_construction_wave(census, plans, wave_kind="first_attempt")
    if wave_path.exists():
        try:
            prior = json.loads(wave_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("construction wave artifact is unreadable") from error
        if prior != wave:
            raise RuntimeError("construction wave artifact differs from current authority")
    else:
        _create_json(wave_path, wave)
    request_key = f"state-aware-construction-wave:{wave['wave_sha256']}"
    payload = _construction_start_payload(wave)
    snapshot = ledger.snapshot()
    attempts = snapshot.get("attempts") if isinstance(snapshot, dict) else None
    if not isinstance(attempts, list):
        raise RuntimeError("campaign ledger snapshot is malformed")
    matches = [attempt for attempt in attempts if attempt.get("request_key") == request_key]
    if not matches:
        ledger.record("start", request_key, payload)
    elif len(matches) != 1 or matches[0].get("start") != payload:
        raise RuntimeError("construction aggregate reservation resume identity drift")
    launch()
    return {**wave, "ledger_request_key": request_key}


def plan_construction_retry_wave(
    campaign: dict[str, object],
    plans: list[dict[str, object]],
    prior_retry_waves: list[dict[str, object]],
) -> dict[str, object]:
    """Bind a retry subset inside the already prepaid aggregate reservation."""
    campaign_core = {
        key: value
        for key, value in campaign.items()
        if key not in {"wave_sha256", "ledger_request_key"}
    }
    if campaign.get("wave_sha256") != sha256_json(campaign_core):
        raise RuntimeError("construction campaign wave sha256 mismatch")
    if len(prior_retry_waves) >= campaign["maximum_attempts"] - 1:
        raise RuntimeError("construction campaign permits at most two retry waves")
    normalized, reservation, plans_sha256 = _plan_inventory(plans)
    campaign_plans = {
        (plan["extraction_key"], plan["request_sha256"]): plan
        for plan in campaign["plans"]
    }
    if any(
        (plan["extraction_key"], plan["request_sha256"]) not in campaign_plans
        or plan != campaign_plans[(plan["extraction_key"], plan["request_sha256"])]
        for plan in normalized
    ):
        raise RuntimeError("construction retry wave is not a subset of the frozen first wave")
    prior_reservation = sum(wave["reservation_nanos"] for wave in prior_retry_waves)
    if prior_reservation + reservation > campaign["retry_pool_nanos"]:
        raise RuntimeError("cumulative construction retry waves exceed their prepaid pool")
    core = {
        "schema_version": 1,
        "campaign_wave_sha256": campaign["wave_sha256"],
        "campaign_attempt": len(prior_retry_waves) + 2,
        "plan_count": len(normalized),
        "ordered_plans_sha256": plans_sha256,
        "reservation_nanos": reservation,
        "plans": normalized,
        "required_launch_env": {
            "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS": str(
                campaign["aggregate_reservation_nanos"]
            ),
            "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT": str(
                len(prior_retry_waves) + 2
            ),
        },
    }
    return {**core, "retry_wave_sha256": sha256_json(core)}


def _usage_cost_nanos(usage: object) -> int:
    if not isinstance(usage, dict):
        raise RuntimeError("construction subledger result lacks authoritative usage")
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    try:
        cost = Decimal(str(usage.get("cost")))
    except InvalidOperation as error:
        raise RuntimeError("construction subledger result has invalid cost") from error
    if (
        type(prompt) is not int
        or prompt <= 0
        or type(completion) is not int
        or completion <= 0
        or total != prompt + completion
        or not cost.is_finite()
        or cost <= 0
    ):
        raise RuntimeError("construction subledger result lacks authoritative usage")
    return int((cost * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_CEILING))


def _validated_construction_cache_hits(
    wave: dict[str, object],
    planned: dict[tuple[object, object], dict[str, object]],
    receipts: list[dict[str, object]],
    authorization_sha256: str | None,
    source_events: list[dict[str, object]],
    cache_namespace: str | None,
) -> dict[tuple[object, object], dict[str, object]]:
    if receipts and (
        not _valid_sha256(authorization_sha256)
        or not isinstance(cache_namespace, str)
        or not cache_namespace
    ):
        raise RuntimeError("construction cache hits require exact authorization identity")
    validated = {}
    for receipt in receipts:
        core = receipt.get("core") if isinstance(receipt, dict) else None
        if not isinstance(core, dict):
            raise RuntimeError("construction cache-hit receipt identity drift")
        pair = (core.get("extraction_key"), core.get("request_sha256"))
        plan = planned.get(pair)
        if plan is None or pair in validated:
            raise RuntimeError("construction paid/cache union has duplicate or foreign coverage")
        binding = {
            "authorization": {
                "authorization_sha256": authorization_sha256,
                "campaign_sha256": wave.get("campaign_census_sha256"),
            },
            "cache": {"namespace": cache_namespace},
            "provider": {
                "requested_model": wave.get("requested_model"),
                "served_model": wave.get("response_model"),
                "served_provider": wave.get("requested_provider"),
            },
        }
        validated[pair] = _validate_exact_cache_receipt(
            receipt, binding=binding, plan=plan, source_events=source_events
        )
    return validated


def validate_and_settle_construction_wave(
    ledger: object,
    wave: dict[str, object],
    subledger_events: list[dict[str, object]],
    retry_waves: list[dict[str, object]] | None = None,
    cache_hit_receipts: list[dict[str, object]] | None = None,
    *,
    authorization_sha256: str | None = None,
    cache_namespace: str | None = None,
) -> dict[str, object]:
    """Settle after the exact union of paid chains and validated cache hits."""
    wave_core = {
        key: value
        for key, value in wave.items()
        if key not in {"wave_sha256", "ledger_request_key"}
    }
    if wave.get("wave_sha256") != sha256_json(wave_core):
        raise RuntimeError("construction wave sha256 mismatch")
    retry_waves = list(retry_waves or [])
    if len(retry_waves) > wave["maximum_attempts"] - 1:
        raise RuntimeError("construction campaign permits at most two retry waves")
    planned = {
        (plan["extraction_key"], plan["request_sha256"]): plan
        for plan in wave["plans"]
    }
    cache_hits = _validated_construction_cache_hits(
        wave,
        planned,
        list(cache_hit_receipts or []),
        authorization_sha256,
        subledger_events,
        cache_namespace,
    )
    # Every cache hit is authenticated by a settled source result in this same
    # authorization-bound construction ledger. Hits prove zero-cost reuse; they
    # never erase the paid source attempt from campaign settlement.
    paid_pairs = set(planned)
    expected_attempts = {(pair, 1) for pair in paid_pairs}
    cumulative_retry_reservation = 0
    for index, retry_wave in enumerate(retry_waves, start=2):
        core = {key: value for key, value in retry_wave.items() if key != "retry_wave_sha256"}
        if (
            retry_wave.get("retry_wave_sha256") != sha256_json(core)
            or retry_wave.get("campaign_wave_sha256") != wave["wave_sha256"]
            or retry_wave.get("campaign_attempt") != index
        ):
            raise RuntimeError("construction retry wave identity drift")
        normalized, reservation, plans_sha256 = _plan_inventory(retry_wave.get("plans", []))
        if (
            reservation != retry_wave.get("reservation_nanos")
            or plans_sha256 != retry_wave.get("ordered_plans_sha256")
            or any(
                (plan["extraction_key"], plan["request_sha256"]) not in planned
                or plan != planned[(plan["extraction_key"], plan["request_sha256"])]
                for plan in normalized
            )
        ):
            raise RuntimeError("construction retry wave differs from the frozen first wave")
        cumulative_retry_reservation += reservation
        expected_attempts.update(
            ((plan["extraction_key"], plan["request_sha256"]), index)
            for plan in retry_wave["plans"]
        )
    if cumulative_retry_reservation > wave["retry_pool_nanos"]:
        raise RuntimeError("construction retry waves exceed their prepaid pool")
    started = {}
    results = {}
    response_ids = set()
    settled_nanos = 0
    prompt_tokens = completion_tokens = 0
    for event in subledger_events:
        pair = (event.get("extraction_key"), event.get("request_sha256"))
        campaign_attempt = event.get("campaign_attempt")
        attempt_key = (pair, campaign_attempt)
        attempt_id = event.get("attempt_id")
        if attempt_key not in expected_attempts or not isinstance(attempt_id, str) or not attempt_id:
            raise RuntimeError("construction subledger lacks exact planned-key coverage")
        if event.get("event") == "started":
            if (
                attempt_key in started
                or event.get("attempt") != 1
                or event.get("maximum_attempts") != planned[pair]["maximum_attempts"]
                or event.get("requested_model") != wave["requested_model"]
                or event.get("per_attempt_reservation_nanos")
                != planned[pair]["per_attempt_reservation_nanos"]
            ):
                raise RuntimeError("construction subledger lacks exact planned-key coverage")
            started[attempt_key] = attempt_id
        elif event.get("event") == "result":
            response_id = event.get("response_id")
            if (
                attempt_key in results
                or started.get(attempt_key) != attempt_id
                or event.get("attempt") != 1
                or event.get("maximum_attempts") != planned[pair]["maximum_attempts"]
                or event.get("requested_model") != wave["requested_model"]
                or not isinstance(event.get("result_sha256"), str)
                or not SHA256.fullmatch(event["result_sha256"])
            ):
                raise RuntimeError("construction subledger lacks exact planned-key coverage")
            reservation_status = event.get("reservation_status")
            if reservation_status == "not_charged":
                if (
                    event.get("http_status") not in {429, 502, 503}
                    or event.get("parse_status") != "http_error"
                    or event.get("error") != "http_error"
                    or response_id is not None
                    or event.get("usage") is not None
                    or event.get("served_model") is not None
                    or event.get("served_provider") is not None
                ):
                    raise RuntimeError("not-charged construction error is not typed pre-generation")
                decoded = False
            elif reservation_status == "settled":
                if (
                    not isinstance(response_id, str)
                    or not response_id
                    or response_id in response_ids
                    or not isinstance(event.get("served_provider"), str)
                    or event["served_provider"].casefold()
                    != wave["requested_provider"].casefold()
                    or event.get("served_model") != wave["response_model"]
                ):
                    raise RuntimeError("construction settled route or generation identity drift")
                decoded = event.get("parse_status") == "decoded" and event.get("error") is None
                usage = event.get("usage")
                attempt_cost = _usage_cost_nanos(usage)
                if attempt_cost > planned[pair]["per_attempt_reservation_nanos"]:
                    raise RuntimeError("construction subledger settled cost exceeds plan reservation")
                settled_nanos += attempt_cost
                prompt_tokens += usage["prompt_tokens"]
                completion_tokens += usage["completion_tokens"]
                response_ids.add(response_id)
            else:
                raise RuntimeError("unresolved construction attempt blocks aggregate settlement")
            if decoded:
                if (
                    not isinstance(event.get("observation_sha256"), str)
                    or not SHA256.fullmatch(event["observation_sha256"])
                    or type(event.get("observation_count")) is not int
                    or event["observation_count"] < 0
                ):
                    raise RuntimeError("decoded construction result lacks observation proof")
            results[attempt_key] = {
                "attempt_id": attempt_id,
                "decoded": decoded,
            }
        else:
            raise RuntimeError("construction subledger lacks exact planned-key coverage")
    if set(started) != expected_attempts or set(results) != expected_attempts:
        raise RuntimeError("construction subledger lacks exact planned-key coverage")
    for pair in paid_pairs:
        attempts = sorted(
            attempt for candidate, attempt in expected_attempts if candidate == pair
        )
        decoded_attempts = [
            attempt for attempt in attempts if results[(pair, attempt)]["decoded"]
        ]
        if decoded_attempts != [attempts[-1]]:
            raise RuntimeError("construction retries must end in exactly one decoded result")
    if settled_nanos > wave["aggregate_reservation_nanos"]:
        raise RuntimeError("construction wave settled cost exceeds aggregate reservation")
    aggregate = {
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": format(Decimal(settled_nanos) / Decimal(1_000_000_000), "f"),
        }
    }
    ledger.record("result", wave["ledger_request_key"], {"response": aggregate})
    proof_core = {
        "schema_version": 1,
        "wave_sha256": wave["wave_sha256"],
        "ordered_plans_sha256": wave["ordered_plans_sha256"],
        "settled_nanos": settled_nanos,
        "response_ids_sha256": sha256_json(sorted(response_ids)),
        "paid_key_count": len(paid_pairs),
        "cache_hit_key_count": len(cache_hits),
        "cache_hit_receipts_sha256": sha256_json(
            sorted(receipt["cache_hit_sha256"] for receipt in (cache_hit_receipts or []))
        ),
        "exact_planned_key_coverage": True,
    }
    return {**proof_core, "wave_settlement_sha256": sha256_json(proof_core)}


def recost_census(
    parent_census_path: Path,
    parent_manifest_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    parent = json.loads(parent_census_path.read_text(encoding="utf-8"))
    parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha256_file(parent_manifest_path) != parent.get("manifest_sha256"):
        raise RuntimeError("parent manifest does not match the frozen census")
    if parent_manifest.get("benchmark") != manifest.get("benchmark") or parent_manifest.get("upstream") != manifest.get("upstream") or parent_manifest.get("data") != manifest.get("data"):
        raise RuntimeError("recost cannot change benchmark or construction data inputs")
    old_construction = parent_manifest.get("construction")
    construction = manifest.get("construction")
    if not isinstance(old_construction, dict) or not isinstance(construction, dict):
        raise RuntimeError("recost construction manifest is missing")
    comparable_old = {
        key: value
        for key, value in old_construction.items()
        if key != "code_sha256s"
    }
    comparable_current = {
        key: value
        for key, value in construction.items()
        if key not in {"code_sha256s", "wave_policy", "response_model"}
    }
    if comparable_old != comparable_current:
        raise RuntimeError("recost cannot change construction request, batching, or pricing inputs")
    if parent.get("construction", {}).get("input_manifest_sha256") != manifest["data"]["files"]["longmemeval_v2_medium_200k.json"]["sha256"]:
        raise RuntimeError("parent census input manifest differs from the frozen dataset")
    for relative, expected in construction.get("code_sha256s", {}).items():
        if _sha256_file(ROOT / relative) != expected:
            raise RuntimeError(f"current construction runtime identity drift: {relative}")
    policy = construction.get("wave_policy")
    if (
        not isinstance(policy, dict)
        or policy.get("maximum_internal_attempts") != 1
        or policy.get("maximum_campaign_waves") != construction.get("maximum_attempts")
        or policy.get("first_wave_reservation") != "sum_exact_per_attempt_reservation_nanos"
        or policy.get("retry_wave_reservation") != "campaign-ledger-authorized-aggregate"
        or policy.get("requires_exact_subledger_coverage") is not True
        or policy.get("parent_census_sha256") != parent.get("census_sha256")
        or policy.get("parent_construction_sha256") != sha256_json(parent["construction"])
    ):
        raise RuntimeError("construction aggregate-wave policy is incomplete or unbound")
    retry_pool = policy.get("retry_pool_nanos")
    data_root = Path(
        os.environ.get(
            "MEMPHANT_LME_V2_DATA_ROOT",
            Path.home() / ".cache/memphant/longmemeval-v2",
        )
    )
    request_shapes, reader_inventory = _recompute_reader_authority(
        manifest, data_root
    )
    _validate_deep_runtime_identity(manifest["deep_recall"])
    deep_derivation = _deep_liability(manifest["deep_recall"])
    runtime_hashes = {
        "structured_state": construction["code_sha256s"]["crates/memphant-runtime/src/structured_state_openrouter.rs"],
        "deep_recall": manifest["deep_recall"]["runtime_code_sha256"],
        "recost_runner": construction["code_sha256s"]["scripts/run_lme_v2_state_aware.py"],
    }
    result = recost_census_values(
        parent,
        reader_inventory=reader_inventory,
        s_term=deep_derivation["maximum_liability_nanos"],
        retry_pool_nanos=retry_pool,
        manifest_path=str(manifest_path),
        manifest_sha256=_sha256_file(manifest_path),
        runtime_hashes=runtime_hashes,
    )
    result.pop("census_sha256")
    result["liability_derivation"]["request_shapes"] = request_shapes
    result["liability_derivation"]["deep"] = deep_derivation
    result["derivation"]["parent_census_path"] = str(parent_census_path)
    result["derivation"]["parent_census_file_sha256"] = _sha256_file(parent_census_path)
    result["derivation"]["parent_manifest_path"] = str(parent_manifest_path)
    result["derivation"]["parent_manifest_sha256"] = _sha256_file(parent_manifest_path)
    return {**result, "census_sha256": sha256_json(result)}


def census(
    manifest_path: Path,
    *,
    data_root: Path | None = None,
    cli_bin: Path | None = None,
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    benchmark = manifest.get("benchmark", {})
    if benchmark.get("questions") != QUESTION_COUNT or benchmark.get("memory_context_max_tokens") != 200000:
        raise RuntimeError("census requires the official 451-question 200K profile")
    construction = manifest.get("construction", {})
    reader_judge = manifest.get("reader_judge", {})
    deep_recall = manifest.get("deep_recall", {})
    if isinstance(manifest.get("data"), dict) and isinstance(manifest.get("upstream"), dict):
        resolved_data_root = data_root or Path(os.environ.get("MEMPHANT_LME_V2_DATA_ROOT", Path.home() / ".cache/memphant/longmemeval-v2"))
        resolved_cli, binary_provenance = _build_census_binary(manifest, cli_bin)
        return _full_census(
            manifest,
            manifest_path,
            resolved_data_root,
            resolved_cli,
            binary_provenance,
        )
    required = {
        "C": construction.get("maximum_liability_nanos"),
        "R_sum": reader_judge.get("maximum_arm_liability_sum_nanos"),
        "S": deep_recall.get("maximum_liability_nanos"),
    }
    missing = sorted(name for name, value in required.items() if type(value) is not int or value <= 0)
    identities = []
    for section_name, section in (
        ("construction", construction),
        ("reader_judge", reader_judge),
        ("deep_recall", deep_recall),
    ):
        for field in ("model", "provider", "pricing_source", "pricing_sha256"):
            if not section.get(field):
                identities.append(f"{section_name}.{field}")
    total = None
    if not missing and not identities:
        total = (
            OPENING_NANOS
            + required["C"]
            + 2 * required["R_sum"]
            + QUESTION_COUNT * required["S"]
            + CONTINGENCY_NANOS
        )
    authorized = total is not None and total <= HARD_CEILING_NANOS
    core = {
        "schema_version": 1,
        "benchmark": {
            "questions": QUESTION_COUNT,
            "memory_context_max_tokens": 200000,
        },
        "terms": required,
        "admission": {
            "formula": FORMULA,
            "opening_liability_nanos": OPENING_NANOS,
            "contingency_nanos": CONTINGENCY_NANOS,
            "hard_ceiling_nanos": HARD_CEILING_NANOS,
            "total_nanos": total,
            "missing_bounds": missing,
            "missing_identities": sorted(identities),
            "authorized": authorized,
        },
        "paid_models_run": False,
        "spend_nanos": 0,
    }
    return {**core, "census_sha256": sha256_json(core)}


@contextmanager
def _campaign_run_lease(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("state-memory campaign coordinator is already active") from error
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _settle_completed_construction(
    *,
    ledger: object,
    paths: dict[str, Path],
    authorization_sha256: str,
    cache_namespace: str,
    retry_waves: list[dict[str, object]],
) -> dict[str, object]:
    wave = json.loads(paths["construction_wave"].read_text(encoding="utf-8"))
    request_key = f"state-aware-construction-wave:{wave['wave_sha256']}"
    attempts = [
        attempt
        for attempt in ledger.snapshot().get("attempts", [])
        if attempt.get("request_key") == request_key
    ]
    if len(attempts) != 1:
        raise RuntimeError("construction aggregate reservation is missing")
    settlement_path = paths["construction_settlement"]
    if attempts[0].get("status") == "result":
        if not settlement_path.is_file():
            raise RuntimeError("settled construction lacks immutable proof")
        settlement = json.loads(settlement_path.read_text(encoding="utf-8"))
        core = {
            key: value
            for key, value in settlement.items()
            if key != "wave_settlement_sha256"
        }
        if settlement.get("wave_settlement_sha256") != sha256_json(core):
            raise RuntimeError("construction settlement proof drift")
        return settlement
    if attempts[0].get("status") != "started":
        raise RuntimeError("construction aggregate reservation is terminal")
    events = _construction_subledger_events(paths["construction_subledger"])
    settlement = validate_and_settle_construction_wave(
        ledger,
        {**wave, "ledger_request_key": request_key},
        events,
        retry_waves=retry_waves,
        authorization_sha256=authorization_sha256,
        cache_namespace=cache_namespace,
    )
    _atomically_create_json(settlement_path, settlement)
    return settlement


def run_authorized_campaign(
    authorization_path: Path,
    *,
    sealed_prefix: int,
    data_root: Path,
    base_database_url: str,
) -> dict[str, object]:
    """Crash-resumable full native LongMemEval-V2 state-memory campaign."""
    if (
        authorization_path.resolve() != CANONICAL_CAMPAIGN_AUTHORIZATION.resolve()
        or sealed_prefix != 12
    ):
        raise RuntimeError("campaign run requires canonical authority and prefix 12")
    _parsed_scratch_postgres_url(base_database_url, require_base=True)
    lease_path = CAMPAIGN_ARTIFACT_ROOT / "CAMPAIGN-RUN.lock"
    with _campaign_run_lease(lease_path):
        packet = json.loads(authorization_path.read_text(encoding="utf-8"))
        paths = {
            key: Path(value) for key, value in packet["artifacts"].items()
        }
        census_authority = validate_campaign_authorization(
            CANONICAL_CAMPAIGN_CENSUS, CANONICAL_CAMPAIGN_MANIFEST
        )
        official_dir, runtime_code = acquire_official_runtime_code(data_root)
        binaries = _campaign_binaries()
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        seal_passphrase = os.environ.get(
            "MEMPHANT_LME_PREFIX_SEAL_PASSPHRASE", ""
        )
        if not openrouter_key or not openai_key or not seal_passphrase:
            raise RuntimeError("campaign provider credentials or seal passphrase are missing")
        if shutil.which("openssl") is None:
            raise RuntimeError("campaign prefix sealing requires openssl")
        _run_campaign_command(["psql", base_database_url, "-Atqc", "SELECT 1"])
        postgres_tool_identity(base_database_url, "pg_dump", allow_base=True)
        postgres_tool_identity(base_database_url, "pg_restore", allow_base=True)
        for private_path in (
            paths["private_reader_outputs"], paths["judge_outputs"],
            paths["scratch"], paths["case_banks"], paths["construction_dispatches"],
        ):
            private_path.mkdir(parents=True, exist_ok=True)
            private_path.chmod(0o700)
            with tempfile.NamedTemporaryFile(dir=private_path, delete=True):
                pass
        # All credential, runtime, database, toolchain, binary and filesystem
        # checks above are no-cost. The construction launch is the first paid
        # operation in this entrypoint.
        completed_paths = (
            paths["closure"], paths["native_package"], paths["official_derivation"],
            paths["official_metrics"],
            paths["reservation_plan"], paths["journal"],
        )
        if all(path.is_file() for path in completed_paths):
            sys.path.insert(0, str(ROOT / "scripts"))
            from provider_attempts import open_campaign_ledger

            completed_ledger = open_campaign_ledger(
                authorization_path,
                screen_id="longmemeval-v2-state-aware",
                expected_journal_path=paths["journal"],
            )
            try:
                completed_plan = json.loads(
                    paths["reservation_plan"].read_text(encoding="utf-8")
                )
                completed_execution = json.loads(
                    paths["execution_plan"].read_text(encoding="utf-8")
                )
                completed_package = json.loads(
                    paths["native_package"].read_text(encoding="utf-8")
                )
                completed_metrics = json.loads(
                    paths["official_metrics"].read_text(encoding="utf-8")
                )
                completed_closure = json.loads(
                    paths["closure"].read_text(encoding="utf-8")
                )
                package_core = {
                    key: value
                    for key, value in completed_package.items()
                    if key != "native_package_sha256"
                }
                snapshot = completed_ledger.snapshot()
                with open_sealed_reader_prefix(
                    paths=paths,
                    execution_plan=completed_execution,
                    reservation_plan=completed_plan,
                ) as completed_prefix:
                    completed_private = _reader_records_from_private(
                        reservation_plan=completed_plan,
                        prefix=completed_prefix,
                        private_root=paths["private_reader_outputs"].resolve(),
                    )
                    rebuilt_package = build_native_official_package(
                        reservation_plan=completed_plan,
                        ledger_snapshot=snapshot,
                        private_rows=completed_private,
                        judge_root=paths["judge_outputs"],
                        official_dir=official_dir,
                        runtime_code=runtime_code,
                        derivation_path=paths["official_derivation"],
                    )
                completed_derivation = json.loads(
                    paths["official_derivation"].read_text(encoding="utf-8")
                )
                rebuilt_metrics = official_metrics_from_derivation(
                    completed_derivation
                )
                if (
                    completed_package.get("native_package_sha256")
                    != sha256_json(package_core)
                    or completed_package != rebuilt_package
                    or completed_metrics != rebuilt_metrics
                    or completed_package.get("official_metrics_artifact_sha256")
                    != completed_metrics.get("official_metrics_sha256")
                    or completed_package.get("row_settlement")
                    != validate_complete_row_settlement(completed_plan, snapshot)
                    or completed_closure.get("journal_sha256")
                    != snapshot.get("journal_sha256")
                    or completed_closure.get("unresolved_max_liability_nanos") != 0
                ):
                    raise RuntimeError("completed campaign checkpoint drift")
                return {
                    "schema_version": 1,
                    "status": "COMPLETE",
                    "native_package_sha256": completed_package[
                        "native_package_sha256"
                    ],
                    "closure_journal_sha256": completed_closure["journal_sha256"],
                    "external_sota": completed_package["claims"]["external_sota"],
                }
            finally:
                completed_ledger.close()
        prewarm_sealed_prefix(authorization_path, data_root=data_root)
        prefix_retry_plans = json.loads(
            paths["prefix_plans"].read_text(encoding="utf-8")
        )
        prefix_retry_shards = (
            execute_construction_retry_shard(
                authorization_path,
                data_root=data_root,
                eligible_plans=prefix_retry_plans,
                phase="prefix",
            )
            if prefix_retry_plans
            else []
        )
        del prefix_retry_shards
        execution_plan = json.loads(paths["execution_plan"].read_text(encoding="utf-8"))
        reservation_plan = json.loads(
            paths["reservation_plan"].read_text(encoding="utf-8")
        )
        commitment = json.loads(
            paths["remaining_commitment"].read_text(encoding="utf-8")
        )
        sys.path.insert(0, str(ROOT / "scripts"))
        from provider_attempts import open_campaign_ledger

        ledger = open_campaign_ledger(
            authorization_path,
            screen_id="longmemeval-v2-state-aware",
            expected_journal_path=paths["journal"],
        )
        private_root = paths["private_reader_outputs"].resolve()

        def run_case(machine, question_id, pair):
            return execute_reader_case(
                question_id=question_id,
                rows=pair,
                authorization_path=authorization_path,
                census=census_authority,
                data_root=data_root,
                official_dir=official_dir,
                base_database_url=base_database_url,
                bank_root=paths["case_banks"],
                row_state=machine,
                private_root=private_root,
                binaries=binaries,
                openrouter_key=openrouter_key,
            )

        try:
            prefix_machine = RowExecutionStateMachine(
                reservation_plan, ledger, admitted_case_count=12
            )
            if not paths["public_prefix_status"].is_file():
                execute_reader_wave(
                    rows=reservation_plan["rows"][:24],
                    case_count=12,
                    execute_case=lambda question_id, pair: run_case(
                        prefix_machine, question_id, pair
                    ),
                    max_workers=packet["execution"]["reader_max_workers"],
                )
            seal_completed_reader_prefix(
                paths=paths,
                execution_plan=execution_plan,
                reservation_plan=reservation_plan,
                commitment=commitment,
            )
            prewarm_remaining_construction(
                authorization_path, data_root=data_root
            )
            execute_construction_retry_shard(
                authorization_path,
                data_root=data_root,
                eligible_plans=json.loads(
                    paths["remaining_construction_plans"].read_text(encoding="utf-8")
                ),
                phase="tail",
            )
            retry_waves = construction_retry_wave_unions(
                paths,
                json.loads(paths["construction_wave"].read_text(encoding="utf-8")),
            )
            _settle_completed_construction(
                ledger=ledger,
                paths=paths,
                authorization_sha256=packet["authorization"][
                    "authorization_scope_sha256"
                ],
                cache_namespace=packet["execution"]["cache_namespace"],
                retry_waves=retry_waves,
            )
            full_machine = RowExecutionStateMachine(
                reservation_plan, ledger, admitted_case_count=QUESTION_COUNT
            )
            execute_reader_wave(
                rows=reservation_plan["rows"][24:],
                case_count=439,
                execute_case=lambda question_id, pair: run_case(
                    full_machine, question_id, pair
                ),
                max_workers=packet["execution"]["reader_max_workers"],
            )
            with open_sealed_reader_prefix(
                paths=paths,
                execution_plan=execution_plan,
                reservation_plan=reservation_plan,
            ) as prefix_private:
                private_rows = _reader_records_from_private(
                    reservation_plan=reservation_plan,
                    prefix=prefix_private,
                    private_root=private_root,
                )

                def authority_for_row(row):
                    authority = private_rows[row["row_key"]].get(
                        "provider_record", {}
                    ).get("authority")
                    if not isinstance(authority, dict):
                        raise RuntimeError("private row authority is unavailable")
                    return authority

                score_all_official_rows(
                    reservation_plan=reservation_plan,
                    private_rows=private_rows,
                    row_state=full_machine,
                    authority_for_row=authority_for_row,
                    judge_root=paths["judge_outputs"],
                    openai_key=openai_key,
                    data_root=data_root,
                    official_dir=official_dir,
                )
                package = build_native_official_package(
                    reservation_plan=reservation_plan,
                    ledger_snapshot=ledger.snapshot(),
                    private_rows=private_rows,
                    judge_root=paths["judge_outputs"],
                    official_dir=official_dir,
                    runtime_code=runtime_code,
                    derivation_path=paths["official_derivation"],
                )
                derivation = json.loads(
                    paths["official_derivation"].read_text(encoding="utf-8")
                )
                metrics = official_metrics_from_derivation(derivation)
                _create_or_validate_json(paths["official_metrics"], metrics)
            _create_or_validate_json(paths["native_package"], package)
            closure = close_completed_row_campaign(
                ledger=ledger,
                reservation_plan=reservation_plan,
                native_package=package,
                derivation_artifact=derivation,
                official_metrics_artifact=metrics,
                closure_path=paths["closure"],
            )
            return {
                "schema_version": 1,
                "status": "COMPLETE",
                "native_package_sha256": package["native_package_sha256"],
                "closure_journal_sha256": closure["journal_sha256"],
                "external_sota": package["claims"]["external_sota"],
            }
        finally:
            ledger.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    census_parser = subparsers.add_parser("census")
    census_parser.add_argument("--manifest", type=Path, required=True)
    census_parser.add_argument("--output", type=Path, required=True)
    census_parser.add_argument("--data-root", type=Path)
    census_parser.add_argument("--cli-bin", type=Path)
    recost_parser = subparsers.add_parser("recost")
    recost_parser.add_argument("--parent-census", type=Path, required=True)
    recost_parser.add_argument("--parent-manifest", type=Path, required=True)
    recost_parser.add_argument("--manifest", type=Path, required=True)
    recost_parser.add_argument("--output", type=Path, required=True)
    subparsers.add_parser("abandon-v1")
    subparsers.add_parser("authorize")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--authorization", type=Path, required=True)
    run_parser.add_argument("--sealed-prefix", type=int, choices=[12], required=True)
    run_parser.add_argument("--data-root", type=Path)
    run_parser.add_argument("--base-database-url")
    args = parser.parse_args()
    if args.command == "census":
        result = census(args.manifest, data_root=args.data_root, cli_bin=args.cli_bin)
        _atomic_write_json(args.output, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "recost":
        result = recost_census(
            args.parent_census,
            args.parent_manifest,
            args.manifest,
        )
        _atomic_write_json(args.output, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "authorize":
        packet = mint_campaign_authorization()
        print(json.dumps(packet, sort_keys=True))
        return 0
    if args.command == "abandon-v1":
        proof = derive_v1_abandonment_proof()
        _create_json(V1_ABANDONMENT_PROOF, proof)
        print(json.dumps(proof, sort_keys=True))
        return 0
    if args.command == "run":
        data_root = args.data_root or Path(
            os.environ.get(
                "MEMPHANT_LME_V2_DATA_ROOT",
                Path.home() / ".cache/memphant/longmemeval-v2",
            )
        )
        base_database_url = args.base_database_url or os.environ.get(
            "MEMPHANT_LME_SCRATCH_DATABASE_URL", ""
        )
        if not base_database_url:
            raise RuntimeError("run requires MEMPHANT_LME_SCRATCH_DATABASE_URL")
        result = run_authorized_campaign(
            args.authorization,
            sealed_prefix=args.sealed_prefix,
            data_root=data_root,
            base_database_url=base_database_url,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
