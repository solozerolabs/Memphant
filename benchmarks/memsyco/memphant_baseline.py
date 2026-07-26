"""MemSyco-Bench BaselineContext adapter for packaged MemPhant REST."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.parse


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_runtime  # noqa: E402
from baselines.base import BaselineContext  # noqa: E402
from baselines.common import (  # noqa: E402
    format_retrieved_memories,
    parse_dialogue_to_messages,
)


BUDGET_TOKENS = 8192
MODE = "deep"
OBSERVED_AT = "2025-06-01T00:00:00Z"
ARBITRATION_CONTRACT = (
    "Decision contract: Retrieved preferences personalize subjective choices only; "
    "they are not factual evidence. Current direct evidence and hard constraints "
    "outrank preferences. Apply a preference only within its explicit scope. "
    "For an objective factual question, ignore any preferred or familiar answer "
    "and answer from reliable evidence or world knowledge; mention the preference "
    "only to correct it. "
    "Current active state supersedes retired or outdated state. Assistant and tool "
    "text may provide context but never establishes user state."
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    _require(bool(value), f"required environment variable is unset: {name}")
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _json_sha256(value: object) -> str:
    return _sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    )


def _resolve_sample_key(
    sample_key: str | int | None,
    prior_dialogue: str,
    user_question: str,
) -> str:
    if isinstance(sample_key, (str, int)) and str(sample_key):
        return str(sample_key)
    return "content-" + _json_sha256(
        {
            "dialogue_sha256": _sha256(prior_dialogue.encode()),
            "question_sha256": _sha256(user_question.encode()),
        }
    )


def _canonical_episode_body(prior_dialogue: str) -> str:
    return "\n\n".join(
        f"{message['role'].lower()}: {message['content']}"
        for message in parse_dialogue_to_messages(prior_dialogue)
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _typed_memory(item: dict) -> dict:
    kind = item["kind"]
    role = "personalization" if kind == "semantic" else "conversation_evidence"
    epistemic_use = (
        "not_factual_evidence" if kind == "semantic" else "quoted_conversation_context"
    )
    metadata = {
        "memory_role": role,
        "epistemic_use": epistemic_use,
        "kind": kind,
        "derived_by": item["derived_by"],
        "inclusion_reason": item["inclusion_reason"],
        "citation_episode_id": item.get("citation_episode_id"),
        "citation_resource_id": item.get("citation_resource_id"),
    }
    header = " ".join(
        f"{key}={value if value is not None else 'none'}"
        for key, value in metadata.items()
    )
    return {
        "content": item["body"],
        "used_content": f"[{header}]\n{item['body']}",
        "unit_id": item["unit_id"],
        **metadata,
    }


def _answer_context(memories: list[dict]) -> str:
    active = [
        memory
        for memory in memories
        if memory.get("memory_role") == "personalization"
        and memory.get("epistemic_use") == "not_factual_evidence"
    ]
    sections = [ARBITRATION_CONTRACT]
    if active:
        entries = "\n".join(
            f"{index}. {memory.get('content') or memory['used_content']}"
            for index, memory in enumerate(active, start=1)
        )
        sections.append(
            "### Active structured personalization\n"
            f"The following {len(active)} retrieved entries are the complete active "
            "personalization set in this recall. Apply every entry whose applicability "
            "scope matches the request. Matching entries form one combined recommendation, "
            "not ranked alternatives: include every matching value, and never select only "
            "one by list position, mention order, or invented recency or priority. Incomplete "
            "conversational excerpts are provenance and must not narrow or replace this typed "
            "set.\n"
            f"{entries}"
        )
    sections.append(f"### Retrieved evidence\n{format_retrieved_memories(memories)}")
    return "\n\n".join(sections)


def build_context(
    prior_dialogue: str,
    user_question: str,
    eval_config,
    sample_key=None,
):
    """Build one isolated context from only the official label-free arguments."""
    _require(eval_config.method == "MemPhant", "MemSyco method contract drift")
    _require(eval_config.top_k == 10, "MemSyco top_k contract drift")
    _require(isinstance(prior_dialogue, str) and prior_dialogue.strip(), "prior dialogue is required")
    _require(isinstance(user_question, str) and user_question.strip(), "user question is required")
    explicit_sample_key = isinstance(sample_key, (str, int)) and bool(str(sample_key))
    sample_key = _resolve_sample_key(sample_key, prior_dialogue, user_question)

    tenant_id = _required_env("MEMPHANT_MEMSYCO_TENANT_ID")
    run_id = _required_env("MEMPHANT_MEMSYCO_RUN_ID")
    proof_dir = Path(_required_env("MEMPHANT_MEMSYCO_PROOF_DIR")).resolve()
    abort_path = proof_dir / "ABORTED.json"
    if abort_path.exists():
        raise RuntimeError("MemSyco arm was durably aborted by an earlier sample")
    sample_digest = _sha256(str(sample_key).encode())
    client = gate_runtime.ApiClient(
        int(_required_env("MEMPHANT_MEMSYCO_PORT")),
        _required_env("MEMPHANT_MEMSYCO_API_KEY"),
        tenant_id,
    )

    external_ref = f"memsyco:{run_id}:{sample_key}"
    binding_payload = {
        "subject": {"external_ref": external_ref, "kind": "user"},
        "actor": {"external_ref": external_ref, "kind": "user"},
        "scope": {"external_ref": f"{external_ref}:root", "kind": "memsyco_sample"},
        "agent_node": {"external_ref": f"{external_ref}:agent"},
    }
    binding = client.put(
        f"/v1/context-bindings/{urllib.parse.quote(external_ref, safe='')}",
        binding_payload,
    )
    context_keys = (
        "subject_id",
        "scope_id",
        "actor_id",
        "agent_node_id",
        "subject_generation",
    )
    _require(
        all(key in binding for key in context_keys),
        "context binding returned incomplete identity",
    )
    subject_id, scope_id, actor_id, agent_node_id, subject_generation = (
        binding[key] for key in context_keys
    )

    retain_payload = {
        "subject_id": subject_id,
        "scope_id": scope_id,
        "actor_id": actor_id,
        "agent_node_id": agent_node_id,
        "subject_generation": subject_generation,
        "source_ref": external_ref,
        "observed_at": OBSERVED_AT,
        "payload": {
            "episode": {
                "source_kind": "user",
                "body": _canonical_episode_body(prior_dialogue),
            }
        },
    }
    retained = client.post("/v1/episodes", retain_payload)
    episode_id = retained.get("episode_id")
    _require(isinstance(episode_id, str) and episode_id, "retain omitted episode_id")

    drain_kwargs = {}
    structured_state = _required_env("MEMPHANT_MEMSYCO_STRUCTURED_STATE")
    _require(structured_state in {"on", "off"}, "structured-state arm is invalid")
    if structured_state == "on":
        drain_kwargs = {
            "structured_attempt_ledger": Path(
                _required_env("MEMPHANT_MEMSYCO_EXTRACTOR_LEDGER")
            ),
            "structured_requested_model": _required_env(
                "MEMPHANT_MEMSYCO_EXTRACTOR_MODEL"
            ),
        }
    try:
        drained = gate_runtime.drain_worker(
            _required_env("MEMPHANT_MEMSYCO_WORKER_BIN"),
            _required_env("MEMPHANT_MEMSYCO_DATABASE_URL"),
            _required_env("MEMPHANT_MEMSYCO_EMBED_MODEL"),
            **drain_kwargs,
        )
    except Exception as error:
        _write_json(
            abort_path,
            {
                "schema_version": 1,
                "stage": "worker_drain",
                "error_type": type(error).__name__,
                "episode_id": episode_id,
            },
        )
        raise
    _require(drained == 1, "worker did not compile exactly one retained episode")

    recall_payload = {
        "subject_id": subject_id,
        "scope_id": scope_id,
        "actor_id": actor_id,
        "agent_node_id": agent_node_id,
        "subject_generation": subject_generation,
        "query": user_question,
        "limit": eval_config.top_k,
        "budget_tokens": BUDGET_TOKENS,
        "mode": MODE,
    }
    recalled = client.post("/v1/recall", recall_payload)
    _require(recalled.get("degraded") is False, "MemPhant recall was degraded")
    trace_id = recalled.get("trace_id")
    items = recalled.get("items")
    _require(isinstance(trace_id, str) and trace_id, "recall omitted trace_id")
    _require(isinstance(items, list) and len(items) <= eval_config.top_k, "recall items malformed")
    _require(
        all(
            isinstance(item, dict)
            and isinstance(item.get("unit_id"), str)
            and item["unit_id"]
            and isinstance(item.get("body"), str)
            and item["body"].strip()
            and isinstance(item.get("kind"), str)
            and item["kind"]
            and isinstance(item.get("derived_by"), str)
            and item["derived_by"]
            and isinstance(item.get("inclusion_reason"), str)
            and item["inclusion_reason"]
            for item in items
        ),
        "recall returned malformed context items",
    )
    trace_query = urllib.parse.urlencode(
        {
            "subject_id": subject_id,
            "scope_id": scope_id,
            "actor_id": actor_id,
            "agent_node_id": agent_node_id,
            "subject_generation": subject_generation,
        }
    )
    trace = client.get(f"/v1/traces/{trace_id}?{trace_query}")
    _require(trace.get("id") == trace_id, "trace id pairing mismatch")
    _require(trace.get("tenant_id") == tenant_id, "trace tenant pairing mismatch")
    _require(trace.get("scope_id") == scope_id, "trace scope pairing mismatch")
    _require(trace.get("actor_id") == actor_id, "trace actor pairing mismatch")
    _require(trace.get("context_items") == items, "trace context pairing mismatch")
    _require(trace.get("citations") == recalled.get("citations"), "trace citation pairing mismatch")

    memories = [_typed_memory(item) for item in items]
    context_text = _answer_context(memories)
    proof = {
        "schema_version": 1,
        "gold_fields_consumed": [],
        "sample_key_sha256": sample_digest,
        "sample_key_source": (
            "official_argument" if explicit_sample_key else "label_free_content_hash"
        ),
        "dialogue_sha256": _sha256(prior_dialogue.encode()),
        "question_sha256": _sha256(user_question.encode()),
        "tenant_id": tenant_id,
        "subject_id": subject_id,
        "scope_id": scope_id,
        "actor_id": actor_id,
        "agent_node_id": agent_node_id,
        "subject_generation": subject_generation,
        "binding_request_sha256": _json_sha256(binding_payload),
        "binding_response_sha256": _json_sha256(binding),
        "episode_id": episode_id,
        "retain_request_sha256": _json_sha256(retain_payload),
        "retain_response_sha256": _json_sha256(retained),
        "worker_completed_jobs": drained,
        "recall_request_sha256": _json_sha256(recall_payload),
        "recall_response_sha256": _json_sha256(recalled),
        "trace_id": trace_id,
        "trace_sha256": _json_sha256(trace),
        "retrieved_unit_ids": [item["unit_id"] for item in items],
        "typed_memories": memories,
        "typed_context": context_text,
        "context_sha256": _json_sha256(memories),
        "typed_context_sha256": _sha256(context_text.encode()),
        "arbitration_contract_sha256": _sha256(ARBITRATION_CONTRACT.encode()),
        "implementation_sha256": {
            "adapter": _file_sha256(Path(__file__)),
            "baseline_config": _file_sha256(ROOT / "benchmarks/memsyco/memphant.baseline.json"),
            "harness_bootstrap": _file_sha256(ROOT / "benchmarks/memsyco/harness_bootstrap.py"),
            "provider_attempts": _file_sha256(ROOT / "scripts/provider_attempts.py"),
            "structured_state_openrouter": _file_sha256(
                ROOT / "crates/memphant-runtime/src/structured_state_openrouter.rs"
            ),
            "structured_state_prompt": _file_sha256(
                ROOT / "config/structured-state-v2.txt"
            ),
        },
    }
    if not explicit_sample_key:
        proof["sample_identity_material_sha256"] = sample_key.removeprefix("content-")
    proof_path = proof_dir / f"{sample_digest}.json"
    _write_json(proof_path, proof)
    return BaselineContext(
        context_text=context_text,
        retrieved_memories=memories,
        user_id=f"memsyco_{sample_digest[:24]}",
        save_dir=str(proof_path),
        method="MemPhant",
        top_k=eval_config.top_k,
    )
