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
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
from fractions import Fraction
import hashlib
import json
from math import comb
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any
import urllib.request

from benchmarks.longmemeval_v2.construction_authority import (
    load_canonical_binding as _load_canonical_construction_binding,
    validate_cache_receipt as _validate_exact_cache_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
OPENING_NANOS = 4_258_002_400
CONTINGENCY_NANOS = 10_000_000_000
HARD_CEILING_NANOS = 200_000_000_000
QUESTION_COUNT = 451
FORMULA = "4258002400+C+2*R_sum+451*S+10000000000<=200000000000"
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
CAMPAIGN_ARTIFACT_ROOT = ROOT / "docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot"
CANONICAL_CAMPAIGN_CENSUS = CAMPAIGN_ARTIFACT_ROOT / "CAMPAIGN-CENSUS.json"
CANONICAL_CAMPAIGN_MANIFEST = ROOT / "benchmarks/manifests/longmemeval_v2.state_aware_full.v1.json"
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
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _campaign_artifact_paths(root: Path) -> dict[str, str]:
    names = {
        "journal": "CAMPAIGN-ATTEMPTS.jsonl",
        "construction_subledger": "CONSTRUCTION-ATTEMPTS.jsonl",
        "construction_wave": "CONSTRUCTION-WAVE.json",
        "construction_progress": "CONSTRUCTION-PROGRESS.json",
        "construction_input": "CONSTRUCTION-RESOURCES.jsonl",
        "prefix_plans": "PREFIX-12-CONSTRUCTION-PLANS.json",
        "construction_settlement": "CONSTRUCTION-SETTLEMENT.json",
        "observation_cache": "observation-cache",
        "cache_hits": "cache-hits",
        "construction_bindings": "CONSTRUCTION-BINDINGS",
        "scratch": "scratch",
        "case_banks": "case-banks",
        "private_reader_outputs": "private-reader-outputs",
        "sealed_prefix": "PREFIX-12.sealed",
        "public_prefix_status": "PREFIX-12-STATUS.json",
        "remaining_commitment": "REMAINING-439-COMMITMENT.json",
        "judge_outputs": "private-judge-outputs",
        "official_metrics": "OFFICIAL-METRICS.json",
        "native_package": "NATIVE-OFFICIAL-PACKAGE.json",
        "closure": "CAMPAIGN-CLOSURE.json",
    }
    return {key: str((root / name).resolve()) for key, name in names.items()}


def campaign_artifact_paths() -> dict[str, str]:
    """The sole canonical path set; paid entrypoints must not accept overrides."""
    return _campaign_artifact_paths(CAMPAIGN_ARTIFACT_ROOT)


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
    required_parameters = {"seed", "response_format", "structured_outputs", "max_tokens"}
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
        "status": endpoint.get("status"),
    }
    if normalized_qwen != {
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "response_model": "qwen/qwen3.5-9b",
        "provider": "DeepInfra",
        "input_price_usd_per_token": "0.0000001",
        "output_price_usd_per_token": "0.00000015",
        "context_length": 262144,
        "max_completion_tokens": 81920,
        "required_parameters_supported": True,
        "status": 0,
    }:
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
    if not _valid_sha256(status.get("remaining_commitment_sha256")) or not _valid_sha256(status.get("sealed_blob_sha256")):
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
    public_rows_path: Path,
    sealed_output: Path,
    public_status_path: Path,
    remaining_commitment_sha256: str,
    passphrase_env: str,
) -> dict[str, object]:
    if not private_results.is_file():
        raise RuntimeError("private prefix result file is missing")
    if not _valid_sha256(remaining_commitment_sha256):
        raise RuntimeError("remaining commitment sha256 is invalid")
    try:
        rows = json.loads(public_rows_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("public prefix rows are invalid") from error
    if not isinstance(rows, list):
        raise RuntimeError("public prefix rows must be a list")
    passphrase = os.environ.get(passphrase_env, "")
    if not passphrase:
        raise RuntimeError(f"sealed prefix passphrase is unset: {passphrase_env}")
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl is required to seal prefix results")
    sealed_output.parent.mkdir(parents=True, exist_ok=True)
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
        os.replace(temporary, sealed_output)
    finally:
        temporary.unlink(missing_ok=True)
    status = {
        "schema_version": 1,
        "prefix_count": 12,
        "remaining_count": 439,
        "remaining_commitment_sha256": remaining_commitment_sha256,
        "sealed_blob_sha256": _sha256_file(sealed_output),
        "rows": rows,
    }
    validate_public_prefix_status(status)
    _atomic_write_json(public_status_path, status)
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


def _materialize_reader_shapes(data_root: Path, output: Path) -> dict[str, object]:
    prompts = _literal_assignment(
        data_root / "upstream/evaluation/harness.py", "DOMAIN_SYSTEM_PROMPTS"
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
    return {
        "fixture_sha256": _sha256_file(output),
        "rows": rows,
        "image_rows": image_rows,
        "image_manifest_sha256": _sha256_file(checksum_path),
        "image_inventory_sha256": sha256_json(image_inventory),
        "image_inventory": image_inventory,
        "question_source_sha256": _sha256_file(data_root / "questions.jsonl"),
        "question_ids_sha256": sha256_json(sorted(question_ids)),
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
) -> dict[str, object]:
    harness_path = data_root / "upstream/evaluation/harness.py"
    qa_path = data_root / "upstream/evaluation/qa_eval_metrics.py"
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
                "judge_liability_nanos": judge_nanos,
                "per_arm_liability_nanos": reader_nanos + judge_nanos,
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
    with tempfile.TemporaryDirectory(prefix="memphant-reader-authority-") as temporary:
        temporary_root = Path(temporary)
        fixture_path = temporary_root / "reader-shapes.jsonl"
        proof_path = temporary_root / "reader-processor-proof.json"
        fixture = _materialize_reader_shapes(data_root, fixture_path)
        proof = _run_reader_processor_census(
            reader, data_root, fixture_path, proof_path
        )
        request_shapes = _official_request_shape_bounds(
            data_root, proof, fixture, reader
        )
    processor_rows = request_shapes.pop("reader_processor_rows")
    inventory = _reader_liability_inventory(processor_rows, reader, judge)
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
        reader_fixture = _materialize_reader_shapes(data_root, reader_jsonl)
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
    request_shapes = _official_request_shape_bounds(
        data_root, reader_proof, reader_fixture, reader
    )
    processor_rows = request_shapes.pop("reader_processor_rows")
    if judge.get("maximum_fixed_serialized_bytes") != request_shapes["judge_maximum_fixed_serialized_bytes"]:
        raise RuntimeError("judge request maximum drift")
    reader_inventory = _reader_liability_inventory(processor_rows, reader, judge)
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
        processor_rows, reader, judge
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
    if (
        terms != {"C": c_term, "R_sum": r_sum, "S": s_term}
        or admission != expected_admission
        or total > HARD_CEILING_NANOS
        or retry_pool > maximum_retry_pool
        or construction.get("construction_liability_nanos") != c_term
        or construction.get("maximum_retry_pool_nanos") != maximum_retry_pool
        or construction.get("construction_identity_sha256")
        != sha256_json(manifest_construction)
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
            "cache_namespace": "longmemeval-v2-construction-v1",
            "resume_key": "extraction_key",
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
            "served_provider": manifest_construction["provider"],
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
    current_refresh = refresh_campaign_provider_authority()
    if current_refresh["normalized_sha256"] != packet["provider_authority"]["normalized_sha256"]:
        raise RuntimeError("provider route or price authority changed after authorization")
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
    prefix_plans = [
        plan for plan in plans if plan.get("source_body_sha256") in prefix_source_hashes
    ]
    if not prefix_plans or any(
        plan.get("source_body_sha256") not in prefix_source_hashes for plan in prefix_plans
    ):
        raise RuntimeError("sealed prefix plan subset is empty or foreign")
    _create_or_validate_json(paths["prefix_plans"], prefix_plans)
    remaining = {
        "schema_version": 1,
        "prefix_ids_sha256": sha256_json(prefix_ids),
        "remaining_count": len(remaining_ids),
        "remaining_ids_sha256": sha256_json(remaining_ids),
        "full_plan_inventory_sha256": census["construction"]["plan_inventory_sha256"],
    }
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
                "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH": str(ROOT / construction["prompt_path"]),
                "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION": str(construction["input_price_nanos_per_million"]),
                "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION": str(construction["output_price_nanos_per_million"]),
                "MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH": str(resolved_data_root / construction["tokenizer_path"]),
                "MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH": str(resolved_data_root / construction["tokenizer_config_path"]),
                "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER": str(paths["construction_subledger"]),
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
        completed = subprocess.run(
            [
                str(binary), "structured-state", "execute",
                "--input-jsonl", str(paths["construction_input"]),
                "--allowed-plans-json", str(paths["prefix_plans"]),
                "--max-workers", str(packet["execution"]["construction_max_workers"]),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("sealed prefix construction prewarm failed: " + completed.stderr.strip())
        progress = json.loads(completed.stdout)
        _atomic_write_json(paths["construction_progress"], {
            **progress,
            "authorization_sha256": authorization_sha256,
            "campaign_census_sha256": census["census_sha256"],
            "prefix_ids_sha256": remaining["prefix_ids_sha256"],
        })

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
    subparsers.add_parser("authorize")
    prewarm_parser = subparsers.add_parser("prewarm-prefix")
    prewarm_parser.add_argument("--authorization", type=Path, required=True)
    prewarm_parser.add_argument("--sealed-prefix", type=int, choices=[12], required=True)
    prewarm_parser.add_argument("--data-root", type=Path)
    seal_parser = subparsers.add_parser("seal-prefix")
    seal_parser.add_argument("--private-results", type=Path, required=True)
    seal_parser.add_argument("--public-rows", type=Path, required=True)
    seal_parser.add_argument("--sealed-output", type=Path, required=True)
    seal_parser.add_argument("--public-status", type=Path, required=True)
    seal_parser.add_argument("--remaining-commitment-sha256", required=True)
    seal_parser.add_argument(
        "--passphrase-env", default="MEMPHANT_LME_PREFIX_SEAL_PASSPHRASE"
    )
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
    if args.command == "seal-prefix":
        status = seal_prefix(
            args.private_results,
            args.public_rows,
            args.sealed_output,
            args.public_status,
            args.remaining_commitment_sha256,
            args.passphrase_env,
        )
        print(json.dumps(status, sort_keys=True))
        return 0
    if args.command == "authorize":
        packet = mint_campaign_authorization()
        print(json.dumps(packet, sort_keys=True))
        return 0
    if args.command == "prewarm-prefix":
        result = prewarm_sealed_prefix(
            args.authorization, data_root=args.data_root
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
