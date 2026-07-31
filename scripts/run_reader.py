#!/usr/bin/env python3
"""Reader-scored QA lane over bench-lme evidence JSONL.

Input: the ``--emit-qa`` JSONL written by ``memphant-eval bench-lme`` (one row
per question: question, question_date, gold answer, top-k evidence bodies with
provenance). This script drives an external reader and judge through a
headless CLI engine and writes a labeled QA report.

Engines (``--engine``):
- ``claude`` (default): ``claude -p`` headless, no tools, no session
  persistence (the original lane).
- ``codex``: ``codex exec - -m <model> -s read-only --ephemeral
  --skip-git-repo-check --ignore-user-config -o <file>`` with the prompt on
  stdin; only the agent's final message is read (``-o``), so any tool use is
  stripped by construction, and the read-only sandbox plus an explicit
  "answer directly, no commands" instruction suppress it at the source.
- ``openrouter``: direct HTTPS POST to
  ``https://openrouter.ai/api/v1/chat/completions`` (no CLI, no quota tied to
  a coding-agent subscription). ``--model``/``--judge-model`` must be full
  OpenRouter model ids (e.g. ``openai/gpt-5.6-terra``,
  ``anthropic/claude-sonnet-5``). Requires ``OPENROUTER_API_KEY`` in the
  environment (never read from a flag, never printed, never persisted); run
  via Doppler so the key stays out of shell history and process args:
  ``doppler run --project syndai --config dev -- python3 scripts/run_reader.py
  --engine openrouter ...``.

``--judge-model`` lets the judge use a different (stronger) model than the
reader; both model ids and the engine are recorded in the report header.

Honesty contract:
- the reader returns one strict JSON object; only its answer field is judged;
- non-abstention answers use the canonical task-specific LongMemEval judge;
- abstention scores correct only for ``abstain=true`` plus ``answer=null``;
- parse, reader, and judge failures score incorrect with distinct reasons;
- a hard call budget aborts with partial results recorded and promotion blocked;
- every reply is cached by sha256(engine + model + kind + prompt) so reruns
  and identical evidence packs across runs never re-spend budget. Schema and
  decoding identity are part of the key, so pre-schema replies are never reused.

This script never fabricates: every failure is recorded and counted incorrect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from provider_attempts import (
    ProviderAttemptLedger,
    open_campaign_ledger,
    openrouter_generation_lookup,
    provider_response_evidence,
)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ENGINES = ("claude", "codex", "openrouter")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# $0 dry-run target. Three external instruments in this program failed at first
# contact on OUR side, two of them after money was already authorized, and the
# instrument register's single highest-leverage governance recommendation is a
# stub round trip against the current contract before any paid authorization.
# MEMPHANT_OPENROUTER_STUB_URL points the whole paid path — manifest validation,
# ledger, reservation, request body, response_format, parsing, judging, report —
# at a local stub so that round trip costs nothing.
#
# It is deliberately loopback-only and deliberately runs WITHOUT the real API
# key: a stub URL that could be any host, carrying a live bearer token, is an
# exfiltration primitive, and a stub run that authenticates for real is one
# typo away from being a paid run that nobody accounted for.
OPENROUTER_STUB_ENV = "MEMPHANT_OPENROUTER_STUB_URL"
STUB_API_KEY = "stub-no-credential"


def openrouter_endpoint() -> tuple[str, bool]:
    """Returns (url, is_stub). Refuses any stub target that is not loopback."""
    stub = os.environ.get(OPENROUTER_STUB_ENV)
    if not stub:
        return OPENROUTER_URL, False
    host = urllib.parse.urlsplit(stub).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            f"{OPENROUTER_STUB_ENV} must be a loopback URL; refusing to send "
            f"reader traffic to {host!r}"
        )
    return stub, True


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_paid_authorization(
    manifest_path: Path,
    *,
    arm: str,
    evidence_path: Path,
    retrieval_report_path: Path | None,
    reader_model: str,
    judge_model: str,
    judge_profile: str,
    prompt_version: int,
    reasoning_effort: str | None,
    max_calls: int,
    max_provider_attempts: int | None,
    max_output_tokens: int,
    max_spend_usd: Decimal | None,
    max_price_prompt_per_million: Decimal | None,
    max_price_completion_per_million: Decimal | None,
    attempt_ledger_path: Path,
    cache_dir: Path,
    output_path: Path,
) -> dict:
    """Validate a frozen paid packet before any provider credential is read."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("paid authorization manifest is unreadable") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise ValueError("paid authorization manifest schema mismatch")
    frozen = manifest.get("frozen_inputs")
    models = manifest.get("models")
    limits = manifest.get("hard_limits", {}).get(arm)
    execution = manifest.get("execution", {}).get(arm)
    if not all(isinstance(value, dict) for value in (frozen, models, limits, execution)):
        raise ValueError("paid authorization manifest arm is missing")
    # The chat lane's two historical arm names keep their historical frozen-input
    # prefixes so every packet already on disk still validates byte-for-byte. Any
    # other arm name is its own prefix, which is what lets a lane with more than
    # two arms (the coding lane has three) freeze them in one packet instead of
    # splitting into several manifests whose ledgers cannot see each other's spend.
    arm_prefix = {
        "baseline": "baseline",
        "treatment_and_paired_adjudication": "treatment",
    }.get(arm, arm)
    expected = {
        f"{arm_prefix}_evidence_sha256": _file_sha256(evidence_path),
        f"{arm_prefix}_retrieval_sha256": (
            _file_sha256(retrieval_report_path) if retrieval_report_path else None
        ),
        "reader_runner_sha256": _file_sha256(Path(__file__)),
        "provider_attempts_sha256": _file_sha256(
            SCRIPTS_DIR / "provider_attempts.py"
        ),
    }
    for field, actual in expected.items():
        if frozen.get(field) != actual:
            raise ValueError(f"paid authorization {field} drift")
    model_expectations = {
        "provider": "OpenRouter",
        "reader": reader_model,
        "reader_reasoning_effort": reasoning_effort,
        "judge": judge_model,
        "judge_profile": judge_profile,
        "prompt_version": prompt_version,
        "max_output_tokens_per_request": max_output_tokens,
        "provider_max_price_usd_per_million": {
            "prompt": str(max_price_prompt_per_million),
            "completion": str(max_price_completion_per_million),
        },
    }
    for field, actual in model_expectations.items():
        if models.get(field) != actual:
            raise ValueError(f"paid authorization model field {field} drift")
    limit_expectations = {
        "max_logical_calls": max_calls,
        "max_provider_attempts": max_provider_attempts,
        "max_spend_usd": str(max_spend_usd),
    }
    for field, actual in limit_expectations.items():
        if limits.get(field) != actual:
            raise ValueError(f"paid authorization limit {field} drift")
    execution_expectations = {
        "run_id": arm,
        "attempt_ledger": str(attempt_ledger_path.resolve().relative_to(REPO_ROOT)),
        "cache_dir": str(cache_dir.resolve().relative_to(REPO_ROOT)),
        "output": str(output_path.resolve().relative_to(REPO_ROOT)),
    }
    for field, actual in execution_expectations.items():
        if execution.get(field) != actual:
            raise ValueError(f"paid authorization execution field {field} drift")
    campaign = manifest.get("campaign")
    if not isinstance(campaign, dict):
        raise ValueError("paid authorization campaign authority is missing")
    scope = {
        "frozen_inputs": frozen,
        "models": models,
        "hard_limits": manifest["hard_limits"],
        "execution": manifest["execution"],
        "campaign": campaign,
    }
    authorization = manifest.get("authorization")
    if (
        manifest.get("status") != "AUTHORIZED_STATE_MEMORY_CAMPAIGN"
        or not isinstance(authorization, dict)
        or not isinstance(authorization.get("authorized_by"), str)
        or not authorization["authorized_by"].strip()
        or not isinstance(authorization.get("authorized_at"), str)
        or not authorization["authorized_at"].strip()
        or authorization.get("authorization_scope_sha256")
        != sha256_text(json.dumps(scope, sort_keys=True, separators=(",", ":")))
    ):
        raise ValueError("paid execution has not been explicitly authorized")
    return manifest
OPENROUTER_TIMEOUT = 180
OPENROUTER_RETRY_DELAYS = (2, 8, 30)  # 4 tries total: 3 backoff sleeps between them
FLASH_MODEL = "google/gemini-3.5-flash"
FLASH_PROVIDER = "google-ai-studio"
OPENAI_GPT_56_PREFIX = "openai/gpt-5.6-"
# The codex engine has no separate system-prompt channel; the system prompt is
# prepended to the user prompt with this no-tool-use guard.
CODEX_NO_TOOLS_GUARD = (
    "Do not run any commands or use any tools; answer directly from this "
    "prompt in your final message."
)
# Prompt components shared across v1/v2/v3 (--prompt-version), so the reader
# prompts are composed rather than triplicated.
READER_BASE_INSTRUCTION = (
    "You answer questions using ONLY the evidence provided in the prompt."
)
READER_TERSE_INSTRUCTION = (
    "Be terse: put only the concise answer, a short phrase with no preamble, "
    "in the answer field."
)
# v1's plain abstention line. Used by v1 only; v2 and v3 use the calibrated
# abstention instruction below instead.
READER_V1_ABSTENTION = (
    "If the evidence is insufficient to answer, set abstain=true and answer=null."
)
# v2's enumerate-then-compute chain-of-thought instruction.
READER_COT_INSTRUCTION = (
    "First, identify every evidence item that bears on the question, even "
    "partially. Then reason step by step over those items: for questions "
    "that require combining values, doing arithmetic, or counting "
    "occurrences, work the calculation through explicitly in notes before answering."
)
# v2's calibrated-abstention instruction: fixes over-abstention (replying
# "I don't know" when the pack did contain the answer). A pure win, 6/6, on
# the v2 campaign — kept in every --prompt-version 3 route (W7 requirement 1).
READER_CALIBRATED_ABSTENTION = (
    "Abstain only if NO evidence item bears on the question at all; if at "
    "least one item is partially relevant, give your best-supported answer "
    "instead of abstaining."
)
# v2's instruction to isolate the final answer from its CoT reasoning.
READER_FINAL_LINE_INSTRUCTION = (
    "Put only the concise answer in the answer field; put reasoning only in notes."
)
READER_OUTPUT_CONTRACT = (
    'Return exactly one JSON object with this schema and no other text: '
    '{"notes": string, "answer": string|null, "abstain": boolean}. '
    'Use notes for reasoning. For an answer, set abstain=false and answer to a '
    'nonempty string. To abstain, set abstain=true and answer=null.'
)

READER_SYSTEM_PROMPT = " ".join(
    [
        READER_BASE_INSTRUCTION,
        READER_TERSE_INSTRUCTION,
        READER_V1_ABSTENTION,
        READER_OUTPUT_CONTRACT,
    ]
)
# v2 (--prompt-version 2): enumerate-then-compute reasoning with calibrated
# abstention. Fixes three n=100-campaign failure modes: multi-item arithmetic
# answered wrong despite both operands being present, enumerable ("how many
# ...") questions answered from partial recall instead of counting the
# packed items, and over-abstention (replying "I don't know" when the pack
# did contain the answer). The strict output contract keeps reasoning in notes
# and the concise answer in its own field.
READER_SYSTEM_PROMPT_V2 = " ".join(
    [
        READER_BASE_INSTRUCTION,
        READER_COT_INSTRUCTION,
        READER_CALIBRATED_ABSTENTION,
        READER_FINAL_LINE_INSTRUCTION,
        READER_OUTPUT_CONTRACT,
    ]
)
# v3 (--prompt-version 3) terse route: v1-style terse phrasing, but v2's
# calibrated-abstention instruction (W7 requirement 1) instead of v1's plain
# one. Used for every question NOT routed to the v2 CoT prompt.
READER_SYSTEM_PROMPT_V3_TERSE = " ".join(
    [
        READER_BASE_INSTRUCTION,
        READER_TERSE_INSTRUCTION,
        READER_CALIBRATED_ABSTENTION,
        READER_OUTPUT_CONTRACT,
    ]
)
READER_SYSTEM_PROMPTS = {
    1: READER_SYSTEM_PROMPT,
    2: READER_SYSTEM_PROMPT_V2,
}

# --reader-profile closed-book: the no-memory baseline arm.
#
# A no-memory arm run under the evidence-grounded prompts above is INERT, not
# neutral: every evidence-profile prompt orders the reader to answer from the
# evidence only, and the calibrated-abstention line then tells it to abstain
# when no item bears on the question — which, with an empty pack, is always. It
# would abstain on every row, score 0 by construction, and answer nothing about
# whether the reader already knew. The saturation question ("does the reader
# resolve these without any memory at all?") requires a prompt that permits a
# parametric answer, so the closed-book arm gets one, and the difference is
# recorded in the report as `reader_profile` rather than hidden.
#
# This makes the closed-book arm NOT prompt-identical to the scored arms. That
# is a property of the question it answers, not a defect: it is a saturation
# probe, never the paired comparator for a memory claim.
READER_CLOSED_BOOK_INSTRUCTION = (
    "You answer from your own knowledge. No retrieved evidence is available "
    "for this question."
)
READER_CLOSED_BOOK_ABSTENTION = (
    "Abstain only if you genuinely cannot produce a specific answer; give your "
    "best-supported answer otherwise."
)
READER_SYSTEM_PROMPT_CLOSED_BOOK = " ".join(
    [
        READER_CLOSED_BOOK_INSTRUCTION,
        READER_TERSE_INSTRUCTION,
        READER_CLOSED_BOOK_ABSTENTION,
        READER_OUTPUT_CONTRACT,
    ]
)

# v3 (--prompt-version 3): stratum-routed prompt. Evidence: v2's CoT +
# calibrated abstention moved temporal-reasoning 0.52->0.78 but regressed
# multi-session 0.44->0.26 on the same lattice — the CoT reasoning helps
# where it's needed (temporal ordering, counting/arithmetic) and hurts where
# terse recall was already working. v3 routes per question: the v2 CoT
# prompt where the win is real (the temporal-reasoning stratum, or a
# counting question in any stratum), the terse route elsewhere — but keeps
# the calibrated-abstention instruction (a pure win, 6/6) in both routes.
COUNTING_CUE_PATTERN = re.compile(
    r"\b(how many|how much|how often|number of|total|count)\b", re.IGNORECASE
)


def is_counting_question(question: str) -> bool:
    """True if the question text matches a deterministic counting cue ("how
    many", "how much", "how often", "number of", "total", "count"),
    word-boundary matched so "totally"/"discount"/"recount" don't
    false-positive."""
    return COUNTING_CUE_PATTERN.search(question) is not None


def route_v3(question_type: str, question: str) -> tuple[str, str]:
    """Router for --prompt-version 3. Returns (route_name, system_prompt):
    "cot" (the v2 CoT prompt) for temporal-reasoning questions and counting
    questions in any stratum; "terse" (READER_SYSTEM_PROMPT_V3_TERSE)
    otherwise."""
    if question_type == "temporal-reasoning" or is_counting_question(question):
        return "cot", READER_SYSTEM_PROMPT_V2
    return "terse", READER_SYSTEM_PROMPT_V3_TERSE


JUDGE_SYSTEM_PROMPT = "Grade the response. Return only the required yes/no verdict."
READER_OUTPUT_KEYS = {"notes", "answer", "abstain"}
READER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "string"},
        "answer": {"type": ["string", "null"]},
        "abstain": {"type": "boolean"},
    },
    "required": ["notes", "answer", "abstain"],
    "additionalProperties": False,
}
JUDGE_JSON_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["verdict"],
    "additionalProperties": False,
}
RAG_SUPPORTED_JUDGE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer_correct": {"type": "boolean"},
        "fully_supported": {"type": "boolean"},
        "supporting_evidence_ranks": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
        },
    },
    "required": [
        "answer_correct",
        "fully_supported",
        "supporting_evidence_ranks",
    ],
    "additionalProperties": False,
}
PAIRED_RAG_JUDGE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["a", "b", "both", "neither"],
        }
    },
    "required": ["verdict"],
    "additionalProperties": False,
}
# Golden-miner generator kinds (scripts/gate_mine_goldens.py). Registered here
# because response_contract() fails closed on unknown kinds.
GENERATE_SINGLE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "answer_span": {"type": "string"},
    },
    "required": ["question", "answer_span"],
    "additionalProperties": False,
}
GENERATE_MULTI_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "bridge_span": {"type": "string"},
        "answer_span": {"type": "string"},
    },
    "required": ["question", "bridge_span", "answer_span"],
    "additionalProperties": False,
}
FORGET_SUPERSEDE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "selected_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "minItems": 1,
            "maxItems": 1,
        },
        "replacement_text": {"type": "string", "minLength": 1},
    },
    "required": ["selected_indices", "replacement_text"],
    "additionalProperties": False,
}
FORGET_RELEASE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "selected_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
        }
    },
    "required": ["selected_indices"],
    "additionalProperties": False,
}
PACKING_SUFFICIENCY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "selected_ranks": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "sufficient": {"type": "boolean"},
        "negative_transfer_ranks": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "missing_evidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reason": {"type": "string"},
    },
    "required": [
        "selected_ranks",
        "sufficient",
        "negative_transfer_ranks",
        "missing_evidence",
        "reason",
    ],
    "additionalProperties": False,
}
RAG_SUPPORTED_SCHEMA_ID = "rag-supported-v1"
RAG_SUPPORTED_JUDGE_SYSTEM_PROMPT = (
    "Grade whether the answer is correct and fully supported by its retrieved "
    "evidence. Return only the required JSON object."
)
PAIRED_RAG_JUDGE_SYSTEM_PROMPT = (
    "Compare two answer-and-evidence bundles without favoring their position. "
    "Return only the required JSON object."
)
OPENROUTER_DECODING = {"temperature": 0, "max_tokens": 8192}
BOOTSTRAP_RESAMPLES = 1000


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def atomic_write_json(path: Path, value: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            json.dump(value, handle)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reader_report_fingerprint(report: dict) -> str:
    return sha256_text(
        json.dumps(
            {key: value for key, value in report.items() if key != "reader_report_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def contains_gold(reply: str, gold: str) -> bool:
    """Word-boundary containment: short numeric golds (e.g. "2") must appear
    as whole tokens in the reply, never inside another token (e.g. "32")."""
    gold_norm = normalize(gold)
    if not gold_norm:
        return False
    pattern = r"(?<!\w)" + re.escape(gold_norm) + r"(?!\w)"
    return re.search(pattern, normalize(reply)) is not None


class CallBudgetExceeded(Exception):
    pass


def positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal number") from error
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def restore_spend_from_attempts(attempts: list[dict]) -> tuple[Decimal, Decimal]:
    """Reconstruct settled cost and fail-closed liability from a durable ledger."""
    reported = Decimal("0")
    unsettled = Decimal("0")
    for attempt in attempts:
        start = attempt.get("start") if isinstance(attempt, dict) else None
        if not isinstance(start, dict):
            raise ValueError("provider-attempt ledger is missing start metadata")
        try:
            liability = Decimal(start["max_liability_nanos"]) / Decimal(1_000_000_000)
        except (KeyError, InvalidOperation, TypeError) as error:
            raise ValueError("provider-attempt ledger is missing spend liability") from error
        result = attempt.get("result")
        response = result.get("response") if isinstance(result, dict) else None
        usage = response.get("usage") if isinstance(response, dict) else None
        cost = usage.get("cost") if isinstance(usage, dict) else None
        if (
            attempt.get("status") == "result"
            and not isinstance(cost, bool)
            and isinstance(cost, (int, float))
            and cost >= 0
        ):
            reported += Decimal(str(cost))
        else:
            unsettled += liability
    return reported, unsettled


class JudgeFailure(RuntimeError):
    pass


def parse_reader_output(reply: str) -> dict:
    output = json.loads(reply)
    if not isinstance(output, dict) or set(output) != READER_OUTPUT_KEYS:
        raise ValueError("reader output must be an object with the exact output keys")
    if not isinstance(output["notes"], str):
        raise ValueError("reader notes must be a string")
    if not isinstance(output["abstain"], bool):
        raise ValueError("reader abstain must be a boolean")
    answer = output["answer"]
    if output["abstain"]:
        if answer is not None:
            raise ValueError("abstention requires answer=null")
    elif not isinstance(answer, str) or not answer.strip():
        raise ValueError("non-abstention requires a nonempty string answer")
    return output


def parse_judge_output(reply: str, engine: str) -> str:
    if engine == "openrouter":
        try:
            output = json.loads(reply)
        except (json.JSONDecodeError, TypeError) as error:
            raise JudgeFailure("judge output must be a strict JSON object") from error
        if (
            not isinstance(output, dict)
            or set(output) != {"verdict"}
            or output["verdict"] not in ("yes", "no")
        ):
            raise JudgeFailure("judge output must match the strict verdict schema")
        return output["verdict"]
    normalized = normalize(reply)
    if normalized not in ("yes", "no"):
        raise JudgeFailure(f"judge verdict must be exactly yes or no: {reply!r}")
    return normalized


def parse_rag_supported_judge_output(
    reply: str, evidence_ranks: set[int]
) -> dict:
    try:
        output = json.loads(reply)
    except (json.JSONDecodeError, TypeError) as error:
        raise JudgeFailure("RAG judge output must be a strict JSON object") from error
    expected = {
        "answer_correct",
        "fully_supported",
        "supporting_evidence_ranks",
    }
    if not isinstance(output, dict) or set(output) != expected:
        raise JudgeFailure("RAG judge output must match the strict schema")
    if type(output["answer_correct"]) is not bool or type(output["fully_supported"]) is not bool:
        raise JudgeFailure("RAG judge booleans must be exact")
    ranks = output["supporting_evidence_ranks"]
    if (
        not isinstance(ranks, list)
        or any(type(rank) is not int for rank in ranks)
        or len(ranks) != len(set(ranks))
        or any(rank not in evidence_ranks for rank in ranks)
        or (output["fully_supported"] and not ranks)
    ):
        raise JudgeFailure("RAG judge evidence ranks are invalid")
    return output


def parse_paired_rag_judge_output(reply: str) -> str:
    try:
        output = json.loads(reply)
    except (json.JSONDecodeError, TypeError) as error:
        raise JudgeFailure("paired RAG judge output must be strict JSON") from error
    if (
        not isinstance(output, dict)
        or set(output) != {"verdict"}
        or output["verdict"] not in ("a", "b", "both", "neither")
    ):
        raise JudgeFailure("paired RAG judge output must match the strict schema")
    return output["verdict"]


def openrouter_decoding(model: str | None = None) -> dict:
    decoding = dict(OPENROUTER_DECODING)
    # OpenRouter's live GPT-5.6 contracts accept token limits and reasoning but
    # not temperature. With require_parameters=true, sending temperature makes
    # every otherwise-compatible endpoint ineligible.
    if model and model.startswith(OPENAI_GPT_56_PREFIX):
        decoding.pop("temperature")
    return decoding


def response_contract(engine: str, kind: str, model: str | None = None) -> dict:
    schemas = {
        "reader": READER_JSON_SCHEMA,
        "judge": JUDGE_JSON_SCHEMA,
        "rag_judge": RAG_SUPPORTED_JUDGE_JSON_SCHEMA,
        "pair_judge": PAIRED_RAG_JUDGE_JSON_SCHEMA,
        "generate_single": GENERATE_SINGLE_JSON_SCHEMA,
        "generate_multi": GENERATE_MULTI_JSON_SCHEMA,
        "forget_supersede": FORGET_SUPERSEDE_JSON_SCHEMA,
        "forget_release": FORGET_RELEASE_JSON_SCHEMA,
        "packing_sufficiency": PACKING_SUFFICIENCY_JSON_SCHEMA,
    }
    try:
        schema = schemas[kind]
    except KeyError as error:
        raise ValueError(f"unknown response kind: {kind}") from error
    if engine == "openrouter":
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{kind}_output",
                    "strict": True,
                    "schema": schema,
                },
            },
            "decoding": openrouter_decoding(model),
        }
    return {
        "response_format": (
            "prompt_enforced_enum" if kind == "judge" else "prompt_enforced_json"
        ),
        "parser": (
            "normalized_exact_yes_no_v1" if kind == "judge" else "strict_json_v1"
        ),
        "decoding": {"provider_defaults": True},
    }


def openrouter_provider_preferences(
    model: str,
    max_price_per_million: dict[str, Decimal] | None = None,
) -> dict:
    preferences = {"require_parameters": True}
    if max_price_per_million is not None:
        preferences["max_price"] = {
            key: float(value) for key, value in max_price_per_million.items()
        }
    if model == FLASH_MODEL:
        preferences |= {
            "only": [FLASH_PROVIDER],
            "allow_fallbacks": True,
        }
    return preferences


class ReaderCli:
    """Serialized, cached headless CLI calls with a hard budget shared across
    reader and judge (which may use different models on the same engine)."""

    def __init__(
        self,
        engine: str,
        model: str,
        judge_model: str,
        cache_dir: Path,
        max_calls: int,
        reasoning_effort: str | None = None,
        max_spend_usd: Decimal | None = None,
        max_price_per_million: dict[str, Decimal] | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        if engine not in ENGINES:
            raise ValueError(f"unknown engine: {engine} (known: {ENGINES})")
        if reasoning_effort is not None and engine not in ("codex", "openrouter"):
            raise ValueError("--reasoning-effort is codex/openrouter-only")
        self.engine = engine
        self.model = model
        self.judge_model = judge_model
        self.reasoning_effort = reasoning_effort
        self.cache_dir = cache_dir
        self.max_calls = max_calls
        self.fresh_calls = 0
        self.cached_calls = 0
        self.provider_attempts = 0
        self.provider_attempt_log: list[dict] = []
        self.provider_attempt_limit: int | None = None
        self.provider_attempt_ledger: ProviderAttemptLedger | None = None
        if (max_spend_usd is None) != (max_price_per_million is None):
            raise ValueError(
                "OpenRouter spend control requires both a total ceiling and "
                "prompt/completion max prices"
            )
        if max_spend_usd is not None and engine != "openrouter":
            raise ValueError("spend control is openrouter-only")
        self.max_spend_usd = max_spend_usd
        self.max_price_per_million = max_price_per_million
        self.max_output_tokens = max_output_tokens or OPENROUTER_DECODING["max_tokens"]
        if self.max_output_tokens < 1:
            raise ValueError("max output tokens must be at least 1")
        self.reported_spend_usd = Decimal("0")
        self.unsettled_liability_usd = Decimal("0")
        self._active_cache_key: str | None = None
        self.last_call_metadata: dict | None = None
        self._openrouter_generation_lookup = None

    def model_for(self, kind: str) -> str:
        return (
            self.judge_model
            if kind in {"judge", "rag_judge", "pair_judge"}
            else self.model
        )

    def cache_model_for(self, kind: str) -> str:
        """Cache identity of the model: reasoning effort changes replies, so
        it is part of the key (None = the engine's configured default)."""
        model = self.model_for(kind)
        if self.reasoning_effort is not None:
            return f"{model}@{self.reasoning_effort}"
        return model

    def response_contract_for(self, kind: str) -> dict:
        contract = response_contract(self.engine, kind, self.model_for(kind))
        if self.engine == "openrouter":
            contract["decoding"]["max_tokens"] = self.max_output_tokens
        return contract

    def _cache_path(self, kind: str, system_prompt: str, prompt: str) -> Path:
        contract_identity = {
            "response": self.response_contract_for(kind),
            "provenance_schema": 2,
        }
        if self.provider_attempt_ledger is not None:
            contract_identity["authorization_sha256"] = (
                self.provider_attempt_ledger.authorization_sha256
            )
        if self.engine == "openrouter":
            contract_identity["provider"] = openrouter_provider_preferences(
                self.model_for(kind), self.max_price_per_million
            )
        contract = json.dumps(contract_identity, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(
            "\x1e".join(
                [
                    self.engine,
                    self.cache_model_for(kind),
                    kind,
                    contract,
                    system_prompt,
                    prompt,
                ]
            ).encode()
        ).hexdigest()
        return self.cache_dir / f"{key}.json"

    def call(self, kind: str, system_prompt: str, prompt: str) -> str:
        if self.engine == "openrouter":
            if self.provider_attempt_ledger is None:
                raise RuntimeError(
                    "openrouter call requires an authorized campaign ledger"
                )
            self.provider_attempt_ledger.assert_open()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_path(kind, system_prompt, prompt)
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if self.engine == "openrouter":
                self._validate_cached_attempt(cache_path.name, cached)
            self.cached_calls += 1
            self.last_call_metadata = cached.get("metadata")
            return cached["reply"]
        if self.fresh_calls >= self.max_calls:
            raise CallBudgetExceeded(
                f"{self.engine} CLI call budget exhausted ({self.max_calls})"
            )
        self.fresh_calls += 1
        self._active_cache_key = cache_path.name
        try:
            if self.engine == "claude":
                reply = self._call_claude(kind, system_prompt, prompt)
            elif self.engine == "codex":
                reply = self._call_codex(kind, system_prompt, prompt)
            else:
                reply = self._call_openrouter(kind, system_prompt, prompt)
        finally:
            self._active_cache_key = None
        cache_entry = {
                "kind": kind,
                "prompt": prompt,
                "reply": reply,
                "metadata": self.last_call_metadata,
            }
        if self.engine == "openrouter":
            cache_entry.update(self._cache_attempt_provenance(cache_path.name))
        atomic_write_json(cache_path, cache_entry)
        return reply

    def set_provider_attempt_limit(self, limit: int | None) -> None:
        if limit is not None and limit < self.provider_attempts:
            raise ValueError("provider attempt limit is below attempts already used")
        self.provider_attempt_limit = limit

    def set_provider_attempt_ledger(self, ledger: ProviderAttemptLedger) -> None:
        ledger.assert_open()
        self.provider_attempt_ledger = ledger

    @staticmethod
    def _attempt_value_sha256(value: dict) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _cache_attempt_provenance(self, request_key: str) -> dict:
        assert self.provider_attempt_ledger is not None
        for attempt in reversed(self.provider_attempt_ledger.attempts):
            if attempt["request_key"] == request_key and attempt["status"] == "result":
                return {
                    "authorization_sha256": self.provider_attempt_ledger.authorization_sha256,
                    "attempt_id": attempt["attempt_id"],
                    "attempt_start_sha256": self._attempt_value_sha256(attempt["start"]),
                    "attempt_result_sha256": self._attempt_value_sha256(attempt["result"]),
                }
        raise RuntimeError("provider response has no durable result attempt")

    def _validate_cached_attempt(self, request_key: str, cached: dict) -> None:
        assert self.provider_attempt_ledger is not None
        if (
            cached.get("authorization_sha256")
            != self.provider_attempt_ledger.authorization_sha256
        ):
            raise RuntimeError("cached provider response authorization mismatch")
        attempt_id = cached.get("attempt_id")
        for attempt in self.provider_attempt_ledger.attempts:
            if (
                attempt["attempt_id"] == attempt_id
                and attempt["request_key"] == request_key
                and attempt["status"] == "result"
            ):
                if (
                    cached.get("attempt_start_sha256")
                    == self._attempt_value_sha256(attempt["start"])
                    and cached.get("attempt_result_sha256")
                    == self._attempt_value_sha256(attempt["result"])
                ):
                    return
                break
        raise RuntimeError("cached provider response attempt provenance mismatch")

    def restore_spend_state(
        self, *, reported_spend_usd: Decimal, unsettled_liability_usd: Decimal
    ) -> None:
        """Restore durable reservations before a resumed paid invocation."""
        if reported_spend_usd < 0 or unsettled_liability_usd < 0:
            raise ValueError("restored spend state cannot be negative")
        self.reported_spend_usd = reported_spend_usd
        self.unsettled_liability_usd = unsettled_liability_usd
        if (
            self.max_spend_usd is not None
            and reported_spend_usd + unsettled_liability_usd > self.max_spend_usd
        ):
            raise ValueError("restored spend state exceeds the configured ceiling")

    def _attempt_liability_usd(self, request_body: bytes) -> Decimal:
        if self.max_price_per_million is None:
            return Decimal("0")
        # A byte is a conservative upper bound for an input token for the
        # byte-backed tokenizers served here; counting the complete JSON body
        # also covers chat framing. Completion tokens are hard-capped in the
        # provider request. Provider max_price enforces the matching rates.
        prompt_bound = Decimal(len(request_body))
        completion_bound = Decimal(self.max_output_tokens)
        return (
            prompt_bound * self.max_price_per_million["prompt"]
            + completion_bound * self.max_price_per_million["completion"]
        ) / Decimal(1_000_000)

    def _reserve_attempt(self, request_body: bytes) -> Decimal:
        liability = self._attempt_liability_usd(request_body)
        if self.max_spend_usd is None:
            return liability
        projected = (
            self.reported_spend_usd + self.unsettled_liability_usd + liability
        )
        if projected > self.max_spend_usd:
            raise CallBudgetExceeded(
                "openrouter spend ceiling would be exceeded "
                f"({projected} > {self.max_spend_usd} USD)"
            )
        self.unsettled_liability_usd += liability
        return liability

    def _settle_attempt(self, liability: Decimal, cost: object) -> None:
        if self.max_spend_usd is None:
            return
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            raise RuntimeError("OpenRouter response is missing a valid usage.cost")
        settled = Decimal(str(cost))
        if settled > liability:
            raise RuntimeError("OpenRouter response cost exceeds reserved liability")
        self.unsettled_liability_usd -= liability
        self.reported_spend_usd += settled

    def _provider_attempt_event(self, event: str, payload: dict | None = None) -> None:
        if self.provider_attempt_ledger is not None:
            self.provider_attempt_ledger.record(
                event,
                self._active_cache_key or "direct-openrouter-call",
                payload,
            )

    def _call_claude(self, kind: str, system_prompt: str, prompt: str) -> str:
        result = subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--model",
                self.model_for(kind),
                "--system-prompt",
                system_prompt,
                "--tools",
                "",
                "--no-session-persistence",
                "--setting-sources",
                "",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude -p failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )
        return result.stdout.strip()

    def _call_codex(self, kind: str, system_prompt: str, prompt: str) -> str:
        full_prompt = f"Instructions: {system_prompt} {CODEX_NO_TOOLS_GUARD}\n\n{prompt}"
        effort_args = (
            ["-c", f'model_reasoning_effort="{self.reasoning_effort}"']
            if self.reasoning_effort is not None
            else []
        )
        with tempfile.NamedTemporaryFile(
            mode="r", suffix=".txt", prefix="codex-last-msg-"
        ) as last_message:
            result = subprocess.run(
                [
                    "codex",
                    "exec",
                    "-",
                    "-m",
                    self.model_for(kind),
                    *effort_args,
                    "-s",
                    "read-only",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--color",
                    "never",
                    "-o",
                    last_message.name,
                ],
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"codex exec failed (exit {result.returncode}): "
                    f"{result.stderr.strip()[:500]}"
                )
            reply = Path(last_message.name).read_text().strip()
        if not reply:
            raise RuntimeError("codex exec returned an empty final message")
        return reply

    def _call_openrouter(self, kind: str, system_prompt: str, prompt: str) -> str:
        self.last_call_metadata = None
        model = self.model_for(kind)
        decoding = openrouter_decoding(model)
        decoding["max_tokens"] = self.max_output_tokens
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            **decoding,
            "response_format": self.response_contract_for(kind)["response_format"],
            "provider": openrouter_provider_preferences(
                self.model_for(kind), self.max_price_per_million
            ),
        }
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        body = json.dumps(payload).encode()
        last_error: Exception | None = None
        for attempt, delay in enumerate((0, *OPENROUTER_RETRY_DELAYS)):
            if (
                self.provider_attempt_limit is not None
                and self.provider_attempts >= self.provider_attempt_limit
            ):
                raise CallBudgetExceeded(
                    f"openrouter provider attempt budget exhausted "
                    f"({self.provider_attempt_limit})"
                )
            if delay:
                time.sleep(delay)
            liability = self._reserve_attempt(body)
            max_liability_nanos = int(
                (liability * Decimal(1_000_000_000)).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            self._provider_attempt_event(
                "start",
                {
                    "retry_index": attempt,
                    "requested_model": model,
                    "request_sha256": hashlib.sha256(body).hexdigest(),
                    "max_liability_nanos": max_liability_nanos,
                },
            )
            self.provider_attempts += 1
            attempt_started = time.monotonic()
            try:
                endpoint, is_stub = openrouter_endpoint()
                api_key = STUB_API_KEY if is_stub else os.environ.get("OPENROUTER_API_KEY")
                if not api_key:
                    raise RuntimeError(
                        "--engine openrouter requires OPENROUTER_API_KEY in the "
                        "environment; run via Doppler"
                    )
                if self._openrouter_generation_lookup is None:
                    # base_url is passed ONLY when stubbing, so the live call
                    # signature is unchanged and test doubles that replace this
                    # function keep working.
                    self._openrouter_generation_lookup = (
                        openrouter_generation_lookup(
                            api_key,
                            base_url=endpoint.rsplit("/chat/completions", 1)[0],
                        )
                        if is_stub
                        else openrouter_generation_lookup(api_key)
                    )
                request = urllib.request.Request(
                    endpoint,
                    data=body,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/memphant",
                        "X-OpenRouter-Metadata": "enabled",
                        "X-Title": "memphant-bench-reader",
                    },
                )
                with urllib.request.urlopen(
                    request, timeout=OPENROUTER_TIMEOUT
                ) as response:
                    data = json.loads(response.read())
                available = (
                    (data.get("openrouter_metadata") or {})
                    .get("endpoints", {})
                    .get("available", [])
                )
                selected = [
                    endpoint
                    for endpoint in available
                    if isinstance(endpoint, dict) and endpoint.get("selected") is True
                ]
                provider = data.get("provider")
                if provider is None and len(selected) == 1:
                    provider = selected[0].get("provider")
                response_id = data.get("id")
                request_sha256 = hashlib.sha256(body).hexdigest()
                metadata = provider_response_evidence(
                    data,
                    model,
                    time.monotonic() - attempt_started,
                    request_sha256,
                    retry_index=attempt,
                    provider=provider,
                )
                stats = {}
                if response_id:
                    try:
                        stats = self._openrouter_generation_lookup(response_id)
                    except Exception as error:
                        metadata["parse_status"] = "generation_stats_lookup_failed"
                        error_payload = {
                            "type": type(error).__name__,
                            "message": str(error),
                            "elapsed_seconds": time.monotonic() - attempt_started,
                            "retry_index": attempt,
                            "response": metadata,
                        }
                        self.provider_attempt_log.append(error_payload)
                        self._provider_attempt_event("error", error_payload)
                        raise RuntimeError(
                            "OpenRouter generation statistics lookup failed"
                        ) from error
                usage = data.get("usage")
                if isinstance(usage, dict):
                    usage = dict(usage)
                    cost = usage.get("cost")
                    if not isinstance(cost, (int, float)) or cost <= 0:
                        usage["cost"] = stats.get("total_cost")
                metadata["served_model"] = stats.get("model") or metadata["served_model"]
                metadata["provider"] = stats.get("provider_name") or metadata["provider"]
                metadata["usage"] = usage
                self._settle_attempt(
                    liability, usage.get("cost") if isinstance(usage, dict) else None
                )
                content = (
                    (data.get("choices") or [{}])[0].get("message", {}).get("content")
                )
                if not content:
                    last_error = RuntimeError(
                        f"openrouter returned empty content (attempt "
                        f"{attempt + 1}/4): {json.dumps(data)[:500]}"
                    )
                    error_payload = {
                        "error": "empty_content",
                        "elapsed_seconds": time.monotonic() - attempt_started,
                        "retry_index": attempt,
                    }
                    self.provider_attempt_log.append(error_payload)
                    self._provider_attempt_event("error", error_payload)
                    continue
                self.provider_attempt_log.append({"response": metadata})
                self._provider_attempt_event("result", {"response": metadata})
                self.last_call_metadata = metadata
                return content.strip()
            except urllib.error.HTTPError as error:
                body_text = error.read().decode(errors="replace")[:500]
                last_error = RuntimeError(
                    f"openrouter request failed (HTTP {error.code}, attempt "
                    f"{attempt + 1}/4): {body_text}"
                )
                error_payload = {
                    "error": f"http_{error.code}",
                    "elapsed_seconds": time.monotonic() - attempt_started,
                    "retry_index": attempt,
                }
                self.provider_attempt_log.append(error_payload)
                self._provider_attempt_event("error", error_payload)
                if error.code != 429 and error.code < 500:
                    raise last_error from error
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                # OSError covers ssl.SSLError/socket resets that urlopen can
                # surface raw; ValueError covers malformed JSON bodies. Both
                # must retry, then land as RuntimeError so the per-question
                # handler records the row instead of killing the run.
                last_error = RuntimeError(
                    f"openrouter request failed (attempt {attempt + 1}/4): {error}"
                )
                error_payload = {
                    "error": type(error).__name__,
                    "elapsed_seconds": time.monotonic() - attempt_started,
                    "retry_index": attempt,
                }
                self.provider_attempt_log.append(error_payload)
                self._provider_attempt_event("error", error_payload)
        assert last_error is not None
        raise last_error


def build_reader_prompt(row: dict) -> str:
    lines = ["Evidence (retrieved memory items, most relevant first):", ""]
    for item in row["evidence"]:
        lines.append(f"--- evidence item {item['rank']} ---")
        lines.append(item["body"].strip())
        lines.append("")
    if not row["evidence"]:
        lines.append("(no evidence was retrieved)")
        lines.append("")
    question_date = row.get("question_date") or "unknown"
    lines.append(f"Question date: {question_date}")
    lines.append(f"Question: {row['question']}")
    return "\n".join(lines)


def build_judge_prompt(question_type: str, question: str, gold: str, answer: str) -> str:
    if question_type in (
        "single-session-user",
        "single-session-assistant",
        "multi-session",
    ):
        instruction = (
            "Answer yes if the model response contains or is equivalent to the "
            "correct answer, including all required information; answer no if it "
            "contains only a subset."
        )
        gold_label = "Correct Answer"
    elif question_type == "temporal-reasoning":
        instruction = (
            "Answer yes if the model response contains or is equivalent to the "
            "correct answer, including all required information. Do not penalize "
            "off-by-one errors in durations measured in days, weeks, or months."
        )
        gold_label = "Correct Answer"
    elif question_type == "knowledge-update":
        instruction = (
            "Answer yes if the model response gives the updated correct answer. "
            "Previous information may also appear only when the required updated "
            "answer is still clear."
        )
        gold_label = "Correct Answer"
    elif question_type == "single-session-preference":
        instruction = (
            "Answer yes if the model response satisfies the personalized-response "
            "rubric by correctly recalling and using the user's information; it "
            "need not reflect every rubric point."
        )
        gold_label = "Rubric"
    else:
        raise ValueError(f"unknown LongMemEval question type: {question_type}")
    return (
        f"{instruction}\n\nQuestion: {question}\n\n{gold_label}: {gold}\n\n"
        f"Model Response: {answer}\n\nIs the model response correct? Answer yes or no only."
    )


def _render_ranked_evidence(evidence: list[dict]) -> str:
    if not evidence:
        return "(no evidence was retrieved)"
    return "\n\n".join(
        f"--- evidence item {item['rank']} ---\n{item['body'].strip()}"
        for item in evidence
    )


def build_rag_supported_judge_prompt(row: dict, answer: str) -> str:
    return (
        "Judge the candidate answer on two independent requirements. "
        "answer_correct is true only when it is semantically correct relative "
        "to the reference answer and answers the question. fully_supported is "
        "true only when every material factual claim in the candidate answer "
        "is entailed by the retrieved evidence below. The reference answer is "
        "not evidence. Cite every evidence rank used.\n\n"
        f"Question: {row['question']}\n\n"
        f"Reference answer: {row['gold_answer']}\n\n"
        f"Candidate answer: {answer}\n\n"
        "Retrieved evidence:\n"
        f"{_render_ranked_evidence(row['evidence'])}\n\n"
        "Return exactly one JSON object with answer_correct, fully_supported, "
        "and supporting_evidence_ranks."
    )


def _rag_audit_defaults() -> dict:
    return {
        "answer_correct": None,
        "fully_supported": None,
        "supporting_evidence_ranks": [],
        "judge_raw_response": None,
        "judge_parse_status": "not_called",
        "judge_schema_id": RAG_SUPPORTED_SCHEMA_ID,
        "judge_fallback_used": False,
        "judge_error": None,
    }


def judge_rag_row(cli: ReaderCli, row: dict, output: dict) -> dict:
    audit = _rag_audit_defaults()
    if row["is_abstention"]:
        audit.update(
            {
                "correct": output["abstain"] and output["answer"] is None,
                "judge_method": "abstention_exact",
            }
        )
        return audit
    if output["abstain"]:
        audit.update({"correct": False, "judge_method": "abstention_exact"})
        return audit
    try:
        reply = cli.call(
            "rag_judge",
            RAG_SUPPORTED_JUDGE_SYSTEM_PROMPT,
            build_rag_supported_judge_prompt(row, output["answer"]),
        )
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        audit.update(
            {
                "correct": False,
                "judge_method": "rag_supported_llm_judge",
                "judge_error": str(error),
            }
        )
        return audit
    audit["judge_raw_response"] = reply
    try:
        parsed = parse_rag_supported_judge_output(
            reply, {item["rank"] for item in row["evidence"]}
        )
    except JudgeFailure as error:
        audit.update(
            {
                "correct": False,
                "judge_method": "rag_supported_llm_judge",
                "judge_parse_status": "invalid",
                "judge_error": str(error),
            }
        )
        return audit
    audit.update(parsed)
    audit.update(
        {
            "correct": parsed["answer_correct"] and parsed["fully_supported"],
            "judge_method": "rag_supported_llm_judge",
            "judge_parse_status": "strict_valid",
        }
    )
    return audit


def build_paired_rag_prompt(common: dict, bundle_a: dict, bundle_b: dict) -> str:
    def render(label: str, bundle: dict) -> str:
        return (
            f"Answer {label}: {bundle['answer']}\n"
            f"Evidence {label}:\n{_render_ranked_evidence(bundle['evidence'])}"
        )

    return (
        "Choose which answer is both correct relative to the reference answer "
        "and fully supported by its own evidence bundle. The reference answer "
        "is not evidence. Return a for A only, b for B only, both when both "
        "pass, or neither when neither passes.\n\n"
        f"Question: {common['question']}\n\n"
        f"Reference answer: {common['gold_answer']}\n\n"
        f"{render('A', bundle_a)}\n\n{render('B', bundle_b)}\n\n"
        "Return exactly one JSON object with verdict."
    )


def _bundle_sha256(bundle: dict) -> str:
    return sha256_text(json.dumps(bundle, sort_keys=True, separators=(",", ":")))


def _canonical_pair_verdict(verdict: str, a_label: str, b_label: str) -> str:
    if verdict == "a":
        return a_label
    if verdict == "b":
        return b_label
    return verdict


def adjudicate_supported_flip(
    cli: ReaderCli,
    common: dict,
    current: dict,
    baseline: dict,
    *,
    seed: int,
) -> dict:
    first_current = int(
        sha256_text(f"{seed}\x1e{common['question_id']}")[-1], 16
    ) % 2 == 0
    orders = [
        [("current", current), ("baseline", baseline)],
        [("baseline", baseline), ("current", current)],
    ]
    if not first_current:
        orders.reverse()
    raw_responses = []
    parsed_verdicts = []
    canonical_verdicts = []
    order_labels = []
    error = None
    for order in orders:
        (a_label, bundle_a), (b_label, bundle_b) = order
        order_labels.append({"a": a_label, "b": b_label})
        try:
            reply = cli.call(
                "pair_judge",
                PAIRED_RAG_JUDGE_SYSTEM_PROMPT,
                build_paired_rag_prompt(common, bundle_a, bundle_b),
            )
            raw_responses.append(reply)
            verdict = parse_paired_rag_judge_output(reply)
            parsed_verdicts.append(verdict)
            canonical_verdicts.append(
                _canonical_pair_verdict(verdict, a_label, b_label)
            )
        except (CallBudgetExceeded, JudgeFailure, RuntimeError, subprocess.TimeoutExpired) as caught:
            error = str(caught)
            break
    expected = "current" if current["correct"] else "baseline"
    if error is not None or len(canonical_verdicts) != 2:
        status = "error"
    elif canonical_verdicts[0] != canonical_verdicts[1]:
        status = "position_disagreement"
    elif canonical_verdicts[0] != expected:
        status = "absolute_disagreement"
    else:
        status = "resolved"
    return {
        "question_id": common["question_id"],
        "status": status,
        "expected_winner": expected,
        "orders": order_labels,
        "raw_responses": raw_responses,
        "parsed_verdicts": parsed_verdicts,
        "canonical_verdicts": canonical_verdicts,
        "current_bundle_sha256": _bundle_sha256(current),
        "baseline_bundle_sha256": _bundle_sha256(baseline),
        "judge_fallback_used": False,
        "error": error,
    }


def judge_row(cli: ReaderCli, row: dict, output: dict) -> tuple[bool, str]:
    """Returns (correct, judge_method)."""
    gold = str(row["gold_answer"])
    if row["is_abstention"]:
        return output["abstain"] and output["answer"] is None, "abstention_exact"
    if output["abstain"]:
        return False, "abstention_exact"
    answer = output["answer"]
    verdict = cli.call(
        "judge",
        JUDGE_SYSTEM_PROMPT,
        build_judge_prompt(row["question_type"], row["question"], gold, answer),
    )
    return parse_judge_output(verdict, cli.engine) == "yes", "llm_judge"


def bootstrap_ci(deltas: list[float], resamples: int, seed: int) -> dict:
    n = len(deltas)
    mean = sum(deltas) / n if n else 0.0
    if n == 0:
        return {
            "mean": mean,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
            "ci_excludes_zero": False,
        }
    rng = random.Random(seed)
    means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    )
    low = means[min(int(resamples * 0.025), resamples - 1)]
    high = means[min(max(int(resamples * 0.975 + 0.999999) - 1, 0), resamples - 1)]
    return {
        "mean": mean,
        "ci95_low": low,
        "ci95_high": high,
        "ci_excludes_zero": low > 0.0 or high < 0.0,
    }


def accuracy(rows: list[dict]) -> dict:
    scored = [r for r in rows if r.get("correct") is not None]
    correct = [r for r in scored if r["correct"]]
    return {
        "n": len(rows),
        "n_scored": len(scored),
        "qa_accuracy": (len(correct) / len(scored)) if scored else None,
    }


def _indexed_rows(report: dict, kind: str) -> dict[str, dict]:
    rows = report.get("per_question")
    if not isinstance(rows, list):
        raise ValueError(f"{kind} report is missing per_question rows")
    indexed: dict[str, dict] = {}
    for row in rows:
        qid = row.get("question_id") if isinstance(row, dict) else None
        if not isinstance(qid, str) or not qid or qid in indexed:
            raise ValueError(f"{kind} report has missing or duplicate question IDs")
        indexed[qid] = row
    if not indexed:
        raise ValueError(f"{kind} report has no question IDs")
    return indexed


def validate_and_pair_reports(
    left: dict, right: dict, kind: str
) -> list[tuple[str, dict, dict]]:
    """Fail-closed validation and exact question-ID pairing for promotion gates."""
    if kind not in ("reader", "provenance"):
        raise ValueError(f"unknown report kind: {kind}")
    indexed = [_indexed_rows(report, kind) for report in (left, right)]
    if set(indexed[0]) != set(indexed[1]):
        raise ValueError(f"{kind} reports have unequal question IDs")
    if kind == "reader":
        for report, rows in zip((left, right), indexed):
            if report.get("smoke_only") is not False:
                raise ValueError("reader smoke report is not promotion eligible")
            errors = report.get("errors")
            if (
                report.get("promotion_ineligible") is True
                or report.get("complete") is not True
                or report.get("aborted") is not None
                or report.get("expected_n") != len(rows)
                or report.get("evaluated_expected_n") != len(rows)
                or report.get("source_expected_n") != len(rows)
                or not isinstance(report.get("source_evidence_sha256"), str)
                or not report["source_evidence_sha256"]
                or report.get("evaluated_evidence_sha256")
                != report.get("source_evidence_sha256")
                or not isinstance(errors, dict)
                or set(errors) != {"reader", "parse", "judge"}
                or any(not isinstance(value, int) or value != 0 for value in errors.values())
                or any(type(row.get("correct")) is not bool for row in rows.values())
            ):
                raise ValueError("reader report is incomplete, aborted, or erroring")
        question_set_sha = left.get("question_set_sha256")
        if (
            not isinstance(question_set_sha, str)
            or not question_set_sha
            or question_set_sha != right.get("question_set_sha256")
        ):
            raise ValueError("reader question-set fingerprints differ")
        for report in (left, right):
            fingerprint = report.get("evaluator_fingerprint")
            if not isinstance(fingerprint, dict) or not fingerprint.get("sha256"):
                raise ValueError("reader evaluator fingerprint is missing")
            payload = {key: value for key, value in fingerprint.items() if key != "sha256"}
            expected_sha256 = sha256_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":"))
            )
            if fingerprint["sha256"] != expected_sha256:
                raise ValueError("reader evaluator fingerprint hash is invalid")
            for field in (
                "engine",
                "reader_model_id",
                "judge_model_id",
                "reasoning_effort",
                "source_evidence_sha256",
                "retrieval_report_sha256",
            ):
                if fingerprint.get(field) != report.get(field):
                    raise ValueError(f"reader evaluator {field} does not match report")
            if not re.fullmatch(
                r"[0-9a-f]{64}", str(fingerprint.get("evaluator_source_sha256", ""))
            ):
                raise ValueError("reader evaluator source hash is invalid")
        input_binding_fields = {
            "sha256",
            "source_evidence_sha256",
            "retrieval_report_sha256",
        }
        if {
            key: value
            for key, value in left["evaluator_fingerprint"].items()
            if key not in input_binding_fields
        } != {
            key: value
            for key, value in right["evaluator_fingerprint"].items()
            if key not in input_binding_fields
        }:
            raise ValueError("reader evaluator fingerprints differ")
        for qid in indexed[0]:
            left_row, right_row = indexed[0][qid], indexed[1][qid]
            for row in (left_row, right_row):
                if (
                    not isinstance(row.get("question"), str)
                    or not isinstance(row.get("question_type"), str)
                    or not row["question_type"]
                    or type(row.get("is_abstention")) is not bool
                    or "question_date" not in row
                    or "gold_answer" not in row
                ):
                    raise ValueError(f"reader immutable evaluation inputs missing for {qid}")
            for field in (
                "question",
                "question_date",
                "question_type",
                "is_abstention",
                "gold_answer",
            ):
                if left_row.get(field) != right_row.get(field):
                    raise ValueError(f"reader {field} differs for {qid}")
            gold_list_fields = {
                key
                for row in (left_row, right_row)
                for key, value in row.items()
                if "gold" in key and isinstance(value, list)
            }
            for field in gold_list_fields:
                if left_row.get(field) != right_row.get(field):
                    raise ValueError(f"reader {field} differs for {qid}")
        for report in (left, right):
            if report.get("reader_report_sha256") != reader_report_fingerprint(report):
                raise ValueError("reader report fingerprint is invalid")
    else:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import gate_common

        strict_rows = [gate_common.validate_provenance_report(report) for report in (left, right)]
        if any(rows != strict for rows, strict in zip(indexed, strict_rows)):
            raise ValueError("provenance strict validation changed question rows")
        for field in ("golden_revision", "corpus_revision"):
            if not left.get(field) or left.get(field) != right.get(field):
                raise ValueError(f"provenance {field} differs")
    return [(qid, indexed[0][qid], indexed[1][qid]) for qid in sorted(indexed[0])]


def load_bound_evidence_rows(report: dict) -> dict[str, dict]:
    path_value = report.get("evidence_path")
    expected_sha = report.get("source_evidence_sha256")
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("reader report evidence_path is missing")
    raw = Path(path_value).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("reader report evidence bytes do not match source hash")
    rows = [json.loads(line) for line in raw.split(b"\n") if line.strip()]
    indexed = {}
    for row in rows:
        qid = row.get("question_id") if isinstance(row, dict) else None
        if not isinstance(qid, str) or not qid or qid in indexed:
            raise ValueError("reader evidence has missing or duplicate question IDs")
        indexed[qid] = row
    return indexed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, help="bench-lme --emit-qa JSONL")
    parser.add_argument("--out", required=True, help="output reader report JSON")
    parser.add_argument("--label", required=True, help="run label, e.g. session-rerank-off")
    parser.add_argument("--retrieval-report", help="path of the paired bench-lme retrieval report (recorded in header)")
    parser.add_argument("--baseline", help="baseline reader report JSON for paired QA deltas")
    parser.add_argument(
        "--engine",
        choices=ENGINES,
        default="claude",
        help="headless CLI engine driving reader and judge calls",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--prompt-version",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help=(
            "reader system prompt version: 1 (default, today's "
            "READER_SYSTEM_PROMPT verbatim), 2 (enumerate-then-compute "
            "reasoning, calibrated abstention), or 3 (stratum router: the "
            "v2 CoT prompt for temporal-reasoning questions and counting "
            "questions in any stratum, the terse route elsewhere, "
            "calibrated abstention kept in both routes); part of the "
            "reader cache key and recorded in the report as prompt_version; "
            "v3 also records its per-row routing breakdown"
        ),
    )
    parser.add_argument(
        "--reader-profile",
        choices=("evidence", "closed-book"),
        default="evidence",
        help=(
            "'evidence' (default) answers from the packed evidence only; "
            "'closed-book' mints the no-memory saturation arm — it answers from "
            "parametric knowledge, overrides --prompt-version's system prompt, "
            "and is NOT prompt-identical to the evidence arms"
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="judge model id (defaults to --model; lets a stronger model judge)",
    )
    parser.add_argument(
        "--judge-profile",
        choices=("longmemeval", RAG_SUPPORTED_SCHEMA_ID),
        default="longmemeval",
        help="judge contract; rag-supported-v1 requires correctness and evidence support",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help=(
            "codex/openrouter-only: reasoning effort override (low|medium|"
            "high|...) — codex model_reasoning_effort or OpenRouter "
            "reasoning.effort; part of the cache key"
        ),
    )
    parser.add_argument("--cache-dir", default="docs/build-log/artifacts/real-retrieval-20260710/reader-cache")
    parser.add_argument("--max-calls", type=int, default=150, help="hard fresh-call budget for this invocation")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=OPENROUTER_DECODING["max_tokens"],
        help="hard OpenRouter completion-token cap per request",
    )
    parser.add_argument(
        "--max-provider-attempts",
        type=int,
        help="hard OpenRouter attempt budget, including internal retries",
    )
    parser.add_argument(
        "--max-spend-usd",
        type=positive_decimal,
        help="hard OpenRouter campaign ceiling in USD",
    )
    parser.add_argument(
        "--max-price-prompt-per-million",
        type=positive_decimal,
        help="OpenRouter provider.max_price prompt-token ceiling in USD/million",
    )
    parser.add_argument(
        "--max-price-completion-per-million",
        type=positive_decimal,
        help="OpenRouter provider.max_price completion-token ceiling in USD/million",
    )
    parser.add_argument(
        "--attempt-ledger",
        help="durable OpenRouter attempt ledger (required with spend control)",
    )
    parser.add_argument(
        "--authorization-manifest",
        help="frozen paid authorization packet (required for OpenRouter)",
    )
    parser.add_argument(
        "--authorization-arm",
        help=(
            "hard-limit arm in the paid authorization packet. 'baseline' and "
            "'treatment_and_paired_adjudication' keep their historical "
            "frozen-input prefixes; any other name is its own prefix"
        ),
    )
    parser.add_argument("--limit", type=int, help="only process the first N evidence rows (smoke)")
    parser.add_argument("--seed", type=int, default=20260710, help="bootstrap seed")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    spend_values = (
        args.max_spend_usd,
        args.max_price_prompt_per_million,
        args.max_price_completion_per_million,
    )
    if any(value is not None for value in spend_values) and not all(
        value is not None for value in spend_values
    ):
        parser.error(
            "--max-spend-usd and both --max-price-*-per-million values are required together"
        )
    if args.max_spend_usd is not None:
        if args.engine != "openrouter":
            parser.error("spend control is supported only with --engine openrouter")
        if not args.attempt_ledger:
            parser.error("--attempt-ledger is required with spend control")
        if args.max_provider_attempts is None:
            parser.error("--max-provider-attempts is required with spend control")
    if args.max_provider_attempts is not None and args.max_provider_attempts < 1:
        parser.error("--max-provider-attempts must be at least 1")
    if args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be at least 1")
    if args.engine == "openrouter" and (
        not args.authorization_manifest or not args.authorization_arm
    ):
        parser.error(
            "--authorization-manifest and --authorization-arm are required with --engine openrouter"
        )

    # Split on '\n' only: chat bodies can embed U+2028/U+2029, which
    # str.splitlines() would treat as line breaks mid-JSON-record.
    evidence_path = Path(args.evidence)
    evidence_bytes = evidence_path.read_bytes()
    retrieval_report_sha256 = (
        hashlib.sha256(Path(args.retrieval_report).read_bytes()).hexdigest()
        if args.retrieval_report
        else None
    )
    raw_lines = evidence_bytes.split(b"\n")
    source_lines = [
        line + (b"\n" if index < len(raw_lines) - 1 else b"")
        for index, line in enumerate(raw_lines)
        if line.strip()
    ]
    source_rows = [json.loads(line.decode()) for line in source_lines]
    smoke_only = args.limit is not None
    rows = source_rows[: args.limit] if smoke_only else source_rows
    evaluated_evidence_bytes = b"".join(source_lines[: args.limit]) if smoke_only else evidence_bytes

    judge_model = args.judge_model or args.model
    max_price_per_million = (
        {
            "prompt": args.max_price_prompt_per_million,
            "completion": args.max_price_completion_per_million,
        }
        if args.max_spend_usd is not None
        else None
    )
    if args.engine == "openrouter":
        if not args.retrieval_report:
            parser.error("--retrieval-report is required with --engine openrouter")
        try:
            validate_paid_authorization(
                Path(args.authorization_manifest),
                arm=args.authorization_arm,
                evidence_path=evidence_path,
                retrieval_report_path=Path(args.retrieval_report),
                reader_model=args.model,
                judge_model=judge_model,
                judge_profile=args.judge_profile,
                prompt_version=args.prompt_version,
                reasoning_effort=args.reasoning_effort,
                max_calls=args.max_calls,
                max_provider_attempts=args.max_provider_attempts,
                max_output_tokens=args.max_output_tokens,
                max_spend_usd=args.max_spend_usd,
                max_price_prompt_per_million=args.max_price_prompt_per_million,
                max_price_completion_per_million=args.max_price_completion_per_million,
                attempt_ledger_path=Path(args.attempt_ledger),
                cache_dir=Path(args.cache_dir),
                output_path=Path(args.out),
            )
        except ValueError as error:
            parser.error(str(error))
    attempt_ledger = None
    if args.attempt_ledger:
        attempt_ledger = open_campaign_ledger(
            Path(args.authorization_manifest),
            screen_id=f"reader-{args.authorization_arm}",
            expected_journal_path=Path(args.attempt_ledger),
        )
    cli = ReaderCli(
        args.engine,
        args.model,
        judge_model,
        Path(args.cache_dir),
        args.max_calls,
        reasoning_effort=args.reasoning_effort,
        max_spend_usd=args.max_spend_usd,
        max_price_per_million=max_price_per_million,
        max_output_tokens=args.max_output_tokens,
    )
    if attempt_ledger is not None:
        reported, unsettled = restore_spend_from_attempts(attempt_ledger.attempts)
        cli.restore_spend_state(
            reported_spend_usd=reported, unsettled_liability_usd=unsettled
        )
        cli.provider_attempts = len(attempt_ledger.attempts)
        cli.set_provider_attempt_ledger(attempt_ledger)
    if args.max_provider_attempts is not None:
        cli.set_provider_attempt_limit(args.max_provider_attempts)
    reader_system_prompt = READER_SYSTEM_PROMPTS.get(args.prompt_version)
    closed_book = args.reader_profile == "closed-book"
    if closed_book:
        reader_system_prompt = READER_SYSTEM_PROMPT_CLOSED_BOOK
    routing_counts = (
        {"cot": 0, "terse": 0} if args.prompt_version == 3 and not closed_book else None
    )
    per_question: list[dict] = []
    aborted_reason = None
    for index, row in enumerate(rows):
        record = {
            "question_id": row["question_id"],
            "question_type": row["question_type"],
            "is_abstention": row["is_abstention"],
            "question": row["question"],
            "question_date": row.get("question_date"),
            "gold_answer": row["gold_answer"],
            "notes": None,
            "answer": None,
            "abstain": None,
            "judge_method": None,
            "correct": False,
            "reader_error": None,
            "parse_error": None,
            "judge_error": None,
        }
        if args.judge_profile == RAG_SUPPORTED_SCHEMA_ID:
            record.update(_rag_audit_defaults())
        if args.prompt_version == 3 and not closed_book:
            route, system_prompt = route_v3(row["question_type"], row["question"])
            routing_counts[route] += 1
        else:
            system_prompt = reader_system_prompt
        try:
            reply = cli.call("reader", system_prompt, build_reader_prompt(row))
            try:
                output = parse_reader_output(reply)
            except (json.JSONDecodeError, ValueError, TypeError) as error:
                record["parse_error"] = str(error)
                per_question.append(record)
                continue
            record.update(output)
            if args.judge_profile == RAG_SUPPORTED_SCHEMA_ID:
                record.update(judge_rag_row(cli, row, output))
            else:
                try:
                    correct, method = judge_row(cli, row, output)
                except (JudgeFailure, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
                    record["judge_error"] = str(error)
                    per_question.append(record)
                    continue
                record["correct"] = correct
                record["judge_method"] = method
        except CallBudgetExceeded as error:
            aborted_reason = str(error)
            per_question.append(record)
            print(f"ABORT at row {index + 1}/{len(rows)}: {error}", file=sys.stderr)
            break
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            record["reader_error"] = str(error)
        per_question.append(record)
        print(
            f"reader [{index + 1}/{len(rows)}] {row['question_id']} "
            f"correct={record['correct']} method={record['judge_method']}",
            file=sys.stderr,
        )

    strata = sorted({r["question_type"] for r in per_question})
    engine_desc = {
        "claude": "claude -p headless",
        "codex": "codex exec headless (read-only sandbox, final message only)",
        "openrouter": "openrouter chat/completions API",
    }[args.engine]
    errors = {
        name: sum(bool(row[f"{name}_error"]) for row in per_question)
        for name in ("reader", "parse", "judge")
    }
    question_ids = sorted(row["question_id"] for row in rows)
    prompt_hashes = {
        "v1": sha256_text(READER_SYSTEM_PROMPT),
        "v2": sha256_text(READER_SYSTEM_PROMPT_V2),
        "v3_terse": sha256_text(READER_SYSTEM_PROMPT_V3_TERSE),
    }
    if args.judge_profile == RAG_SUPPORTED_SCHEMA_ID:
        judge_hashes = {
            RAG_SUPPORTED_SCHEMA_ID: sha256_text(
                build_rag_supported_judge_prompt(
                    {
                        "question": "{question}",
                        "gold_answer": "{gold}",
                        "evidence": [{"rank": 1, "body": "{evidence}"}],
                    },
                    "{answer}",
                )
            )
        }
        judge_system_prompt = RAG_SUPPORTED_JUDGE_SYSTEM_PROMPT
        response_contracts = {
            kind: cli.response_contract_for(kind)
            for kind in ("reader", "rag_judge", "pair_judge")
        }
    else:
        judge_hashes = {
            question_type: sha256_text(
                build_judge_prompt(question_type, "{question}", "{gold}", "{answer}")
            )
            for question_type in (
                "single-session-user",
                "single-session-assistant",
                "multi-session",
                "temporal-reasoning",
                "knowledge-update",
                "single-session-preference",
            )
        }
        judge_system_prompt = JUDGE_SYSTEM_PROMPT
        response_contracts = {
            kind: cli.response_contract_for(kind) for kind in ("reader", "judge")
        }
    evaluator = {
        "engine": args.engine,
        "reader_model_id": args.model,
        "judge_model_id": judge_model,
        "judge_profile": args.judge_profile,
        "fallback_policy": "none_fail_closed",
        "reasoning_effort": args.reasoning_effort,
        "evaluator_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "retrieval_report_sha256": retrieval_report_sha256,
        "prompt_version": args.prompt_version,
        "reader_profile": args.reader_profile,
        "active_reader_prompt_sha256": (
            {
                "cot": sha256_text(READER_SYSTEM_PROMPT_V2),
                "terse": sha256_text(READER_SYSTEM_PROMPT_V3_TERSE),
            }
            if args.prompt_version == 3 and not closed_book
            else sha256_text(reader_system_prompt)
        ),
        "judge_system_prompt_sha256": sha256_text(judge_system_prompt),
        "prompt_sha256": prompt_hashes,
        "response_contract": response_contracts,
        "judge_prompt_sha256": judge_hashes,
    }
    if args.judge_profile == RAG_SUPPORTED_SCHEMA_ID:
        evaluator["rag_supported_judge_schema_sha256"] = sha256_text(
            json.dumps(
                RAG_SUPPORTED_JUDGE_JSON_SCHEMA,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        evaluator["paired_rag_judge_schema_sha256"] = sha256_text(
            json.dumps(
                PAIRED_RAG_JUDGE_JSON_SCHEMA,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    evaluator["sha256"] = sha256_text(
        json.dumps(evaluator, sort_keys=True, separators=(",", ":"))
    )
    complete = len(per_question) == len(rows) and aborted_reason is None
    report = {
        "benchmark": "longmemeval_reader_qa",
        "engine": args.engine,
        "reader": f"{args.model} ({engine_desc})",
        "judge": (
            f"{judge_model} (rag-supported-v1; correctness plus retrieved-evidence "
            "support; no fallback)"
            if args.judge_profile == RAG_SUPPORTED_SCHEMA_ID
            else f"{judge_model} (canonical task-specific LongMemEval prompt; "
            "answer field only; abstention = abstain true plus answer null)"
        ),
        "reader_model_id": args.model,
        "judge_model_id": judge_model,
        "judge_profile": args.judge_profile,
        "prompt_version": args.prompt_version,
        "reader_profile": args.reader_profile,
        "routing": routing_counts,
        "reasoning_effort": args.reasoning_effort,
        "runtime": "postgres",
        "label": args.label,
        "evidence_path": args.evidence,
        "retrieval_report": args.retrieval_report,
        "retrieval_report_sha256": retrieval_report_sha256,
        "command": " ".join(sys.argv),
        "generated_at_unix": int(time.time()),
        "expected_n": len(rows),
        "source_expected_n": len(source_rows),
        "evaluated_expected_n": len(rows),
        "smoke_only": smoke_only,
        # A stub run must never be mistakable for a paid one, in either
        # direction: the flag is recorded and it disqualifies promotion.
        "openrouter_stub_url": os.environ.get(OPENROUTER_STUB_ENV),
        "complete": complete,
        "promotion_ineligible": (
            smoke_only
            or not complete
            or any(errors.values())
            or bool(os.environ.get(OPENROUTER_STUB_ENV))
        ),
        "errors": errors,
        "evidence_sha256": hashlib.sha256(evaluated_evidence_bytes).hexdigest(),
        "source_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "evaluated_evidence_sha256": hashlib.sha256(evaluated_evidence_bytes).hexdigest(),
        "question_set_sha256": sha256_text(
            json.dumps(question_ids, separators=(",", ":"))
        ),
        "evaluator_fingerprint": evaluator,
        "aborted": aborted_reason,
        "fresh_calls": cli.fresh_calls,
        "cached_calls": cli.cached_calls,
        "provider_attempts": cli.provider_attempts,
        "spend_control": {
            "max_spend_usd": str(args.max_spend_usd),
            "max_price_per_million": {
                key: str(value) for key, value in (max_price_per_million or {}).items()
            },
            "reported_spend_usd": str(cli.reported_spend_usd),
            "unsettled_liability_usd": str(cli.unsettled_liability_usd),
            "max_provider_attempts": args.max_provider_attempts,
            "max_output_tokens": args.max_output_tokens,
            "attempt_ledger": args.attempt_ledger,
        },
        "overall": accuracy(per_question),
        "strata": {
            stratum: accuracy(
                [r for r in per_question if r["question_type"] == stratum]
            )
            for stratum in strata
        },
        "per_question": per_question,
        "paired_vs_baseline": None,
        "baseline_validation_error": None,
        "paired_adjudication_invalid": False,
    }

    report["reader_report_sha256"] = reader_report_fingerprint(report)
    if args.baseline:
        try:
            baseline = json.loads(Path(args.baseline).read_text())
            paired = validate_and_pair_reports(report, baseline, "reader")
            deltas = [
                float(current["correct"]) - float(base["correct"])
                for _, current, base in paired
            ]
            report["paired_vs_baseline"] = {
                "baseline_path": args.baseline,
                "baseline_label": baseline.get("label"),
                "n_paired": len(deltas),
                "delta_qa_accuracy": bootstrap_ci(deltas, BOOTSTRAP_RESAMPLES, args.seed),
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": args.seed,
            }
            if args.judge_profile == RAG_SUPPORTED_SCHEMA_ID:
                current_evidence = {
                    row["question_id"]: row for row in source_rows
                }
                baseline_evidence = load_bound_evidence_rows(baseline)
                if set(current_evidence) != set(baseline_evidence):
                    raise ValueError("paired RAG evidence question IDs differ")
                adjudications = []
                for qid, current, base in paired:
                    if current["correct"] == base["correct"]:
                        continue
                    common = {
                        "question_id": qid,
                        "question": current["question"],
                        "gold_answer": current["gold_answer"],
                    }
                    current_bundle = {
                        "answer": current["answer"],
                        "evidence": current_evidence[qid]["evidence"],
                        "correct": current["correct"],
                    }
                    baseline_bundle = {
                        "answer": base["answer"],
                        "evidence": baseline_evidence[qid]["evidence"],
                        "correct": base["correct"],
                    }
                    adjudications.append(
                        adjudicate_supported_flip(
                            cli,
                            common,
                            current_bundle,
                            baseline_bundle,
                            seed=args.seed,
                        )
                    )
                report["paired_vs_baseline"]["supported_flip_adjudication"] = adjudications
                if any(item["status"] != "resolved" for item in adjudications):
                    report["paired_adjudication_invalid"] = True
                    report["promotion_ineligible"] = True
        except (OSError, json.JSONDecodeError, ValueError) as error:
            report["baseline_validation_error"] = str(error)
            report["paired_vs_baseline"] = {
                "baseline_path": args.baseline,
                "decision": f"HOLD/INVALID: {error}",
            }
            report["promotion_ineligible"] = True

    report["reader_report_sha256"] = reader_report_fingerprint(report)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    overall = report["overall"]
    print(
        f"reader=done label={args.label} n={overall['n']} "
        f"n_scored={overall['n_scored']} qa_accuracy={overall['qa_accuracy']} "
        f"fresh_calls={cli.fresh_calls} cached_calls={cli.cached_calls} "
        f"aborted={aborted_reason} out={args.out}"
    )
    return 1 if (
        aborted_reason
        or report["baseline_validation_error"]
        or report["paired_adjudication_invalid"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
