"""LongMemEval-V2 packing adapter layered over the immutable MemPhant adapter.

Construction and public REST recall remain owned by ``memphant_memory.py``.
This adapter binds a query-only packing arm to trace feature flags, renders
compact receipt-backed provenance, and supplies the deliberately order-swapped
negative control without creating a second memory or retrieval engine.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

from memory_modules.memory import MemoryContextItem, register_memory


BASE_PATH = Path(__file__).with_name("memphant_memory.py")
_SPEC = importlib.util.spec_from_file_location("longmemeval_v2_memphant_base", BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"could not load immutable MemPhant adapter: {BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BASE)

PACK_FEATURE_PREFIXES = (
    "pack_render_cap:",
    "pack_session_quota:",
    "pack_submodular_ordering_enabled",
)
ARM_CONTRACTS = {
    "current": {"features": [], "reverse": False},
    "cap1200": {"features": ["pack_render_cap:1200"], "reverse": False},
    "submodular": {
        "features": ["pack_submodular_ordering_enabled"],
        "reverse": False,
    },
    "cap1200_submodular": {
        "features": [
            "pack_render_cap:1200",
            "pack_submodular_ordering_enabled",
        ],
        "reverse": False,
    },
    "order_swapped": {
        "features": [
            "pack_render_cap:1200",
            "pack_submodular_ordering_enabled",
        ],
        "reverse": True,
    },
}
EXTRA_PARAM_KEYS = {
    "packing_contract_version",
    "packing_arm",
    "reader_context_max_tokens",
}


def _validate_params(params: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    expected = _BASE.EXPECTED_PARAMS | EXTRA_PARAM_KEYS
    _BASE._require(set(params) == expected, "memphant packing memory_params contract drift")
    _BASE._require(
        params["packing_contract_version"] == "memphant.lme_v2.packing.v1",
        "unsupported packing contract",
    )
    arm = params["packing_arm"]
    _BASE._require(isinstance(arm, str) and arm in ARM_CONTRACTS, "unknown packing arm")
    budget = params["reader_context_max_tokens"]
    _BASE._require(
        isinstance(budget, int) and budget > 0,
        "reader_context_max_tokens must be positive",
    )
    _BASE._require(
        params["budget_tokens"] == budget,
        "MemPhant recall and official reader context budgets must match",
    )
    # The immutable base validator pins its historical query budget. That value
    # never participates in construction proof identity, so validate the base
    # contract with its historical ceiling and install this arm's exact query
    # budget immediately after construction.
    base_params = {key: params[key] for key in _BASE.EXPECTED_PARAMS}
    base_params["budget_tokens"] = 32768
    return base_params, dict(ARM_CONTRACTS[arm])


def _citation_for_unit(citations: object, unit_id: str) -> dict[str, object] | None:
    if not isinstance(citations, list):
        return None
    matches = [
        citation
        for citation in citations
        if isinstance(citation, dict) and citation.get("unit_id") == unit_id
    ]
    _BASE._require(len(matches) <= 1, f"duplicate citation for unit {unit_id}")
    return matches[0] if matches else None


def _resource_trajectory_map(construction: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    pairing = construction.get("pairing")
    _BASE._require(isinstance(pairing, dict), "construction pairing is missing")
    retains = pairing.get("retains")
    _BASE._require(isinstance(retains, list), "construction retains are missing")
    for retain in retains:
        _BASE._require(isinstance(retain, dict), "construction retain is invalid")
        trajectory_id = retain.get("trajectory_id")
        fragments = retain.get("fragments")
        _BASE._require(
            isinstance(trajectory_id, str) and isinstance(fragments, list),
            "construction retain provenance is invalid",
        )
        for fragment in fragments:
            _BASE._require(isinstance(fragment, dict), "construction fragment is invalid")
            resource_id = fragment.get("resource_id")
            _BASE._require(
                isinstance(resource_id, str) and resource_id not in result,
                "construction resource provenance is duplicated",
            )
            result[resource_id] = trajectory_id
    return result


def _render_item(
    item: dict[str, object],
    citations: object,
    resource_trajectories: dict[str, str],
) -> tuple[MemoryContextItem, dict[str, object]]:
    unit_id = item.get("unit_id")
    body = item.get("body")
    _BASE._require(
        isinstance(unit_id, str) and isinstance(body, str),
        "packing item is malformed",
    )
    labels = item.get("suppression_labels", [])
    _BASE._require(
        isinstance(labels, list) and all(isinstance(label, str) for label in labels),
        "packing suppression labels are malformed",
    )
    citation = _citation_for_unit(citations, unit_id)
    verification = citation.get("verification") if citation else None
    verification_status = (
        verification.get("status") if isinstance(verification, dict) else None
    )
    if "unresolved_contradiction" in labels:
        status = "contradicts_premise"
    elif verification_status == "verified":
        status = "supported"
    else:
        status = "near_match"
    resource_id = citation.get("resource_id") if citation else None
    trajectory_id = (
        resource_trajectories.get(resource_id)
        if isinstance(resource_id, str)
        else None
    )
    verification_sha256 = (
        _BASE._sha256_json(verification) if isinstance(verification, dict) else None
    )
    provenance = {
        "status": status,
        "unit_id": unit_id,
        "resource_id": resource_id,
        "trajectory_id": trajectory_id,
        "verification_sha256": verification_sha256,
    }
    fields = [
        f"status={status}",
        f"unit={unit_id}",
        f"resource={resource_id or '-'}",
        f"trajectory={trajectory_id or '-'}",
        f"receipt={verification_sha256 or '-'}",
    ]
    return {"type": "text", "value": f"[memphant {' '.join(fields)}]\n{body}"}, provenance


@register_memory
class MemphantPackingMemory(_BASE.MemphantMemory):
    memory_type = "memphant_packing"

    def __init__(self, memory_params: dict[str, object]) -> None:
        base_params, arm_contract = _validate_params(memory_params)
        self.packing_params = dict(memory_params)
        self.arm_contract = arm_contract
        super().__init__(base_params)
        self.params["budget_tokens"] = memory_params["budget_tokens"]
        self.params["mode"] = memory_params["mode"]
        self._last_packing_query_proof: dict[str, object] | None = None

    def query(
        self, query: str, query_image: str | None = None
    ) -> list[MemoryContextItem]:
        base_context = super().query(query, query_image)
        _BASE._require(self._last_query_proof is not None, "base query proof is missing")
        base_proof_path = Path(self._last_query_proof["proof_path"])
        base_proof = json.loads(base_proof_path.read_text(encoding="utf-8"))
        public = base_proof.get("public")
        _BASE._require(isinstance(public, dict), "base public proof is missing")
        recalled = public.get("recall_response")
        trace = public.get("trace")
        _BASE._require(
            isinstance(recalled, dict) and isinstance(trace, dict),
            "base recall/trace proof is missing",
        )
        features = trace.get("feature_flags")
        _BASE._require(
            isinstance(features, list) and all(isinstance(flag, str) for flag in features),
            "trace feature flags are malformed",
        )
        observed_pack_features = sorted(
            flag for flag in features if flag.startswith(PACK_FEATURE_PREFIXES)
        )
        expected_pack_features = sorted(self.arm_contract["features"])
        _BASE._require(
            observed_pack_features == expected_pack_features,
            f"packing server arm mismatch: expected {expected_pack_features}, got {observed_pack_features}",
        )
        items = recalled.get("items")
        _BASE._require(isinstance(items, list), "recall items are malformed")
        resource_trajectories = _resource_trajectory_map(self.construction_proof)
        rendered = [
            _render_item(item, recalled.get("citations"), resource_trajectories)
            for item in items
            if isinstance(item, dict)
        ]
        _BASE._require(len(rendered) == len(items), "recall item is not an object")
        if self.arm_contract["reverse"]:
            rendered.reverse()
        memory_context = [context for context, _ in rendered]
        provenance = [proof for _, proof in rendered]
        disposition = "insufficient" if not memory_context else (
            "contradicts_premise"
            if any(item["status"] == "contradicts_premise" for item in provenance)
            else "near_match"
            if any(item["status"] == "near_match" for item in provenance)
            else "supported"
        )
        companion_core = {
            "schema_version": 1,
            "contract": {
                "adapter_sha256": _BASE._sha256_file(Path(__file__)),
                "memory_params_sha256": _BASE._sha256_json(self.packing_params),
                "packing_arm": self.packing_params["packing_arm"],
                "reader_context_max_tokens": self.packing_params[
                    "reader_context_max_tokens"
                ],
                "expected_pack_feature_flags": expected_pack_features,
                "order_swapped": self.arm_contract["reverse"],
            },
            "base": {
                "proof_path": str(base_proof_path),
                "proof_sha256": _BASE._sha256_file(base_proof_path),
                "context_sha256": _BASE._sha256_json(base_context),
            },
            "packing": {
                "context_sha256": _BASE._sha256_json(memory_context),
                "disposition": disposition,
                "items": provenance,
            },
        }
        companion = {
            **companion_core,
            "packing_proof_sha256": _BASE._sha256_json(companion_core),
        }
        question_id = self._last_query_proof["question_id"]
        companion_path = self.proof_dir / f"{question_id}.{self.instance_id}.packing.json"
        _BASE._atomic_write_json(companion_path, companion)
        self._last_packing_query_proof = {
            **self._last_query_proof,
            "base_context_sha256": self._last_query_proof["context_sha256"],
            "context_sha256": _BASE._sha256_json(memory_context),
            "packing_arm": self.packing_params["packing_arm"],
            "packing_disposition": disposition,
            "packing_proof_path": str(companion_path),
            "packing_proof_sha256": companion["packing_proof_sha256"],
        }
        return memory_context

    def post_query_hook(
        self,
        *,
        query: str,
        query_image: str | None,
        memory_context: list[MemoryContextItem],
    ) -> dict[str, object] | None:
        _BASE._require(
            self._last_packing_query_proof is not None,
            "packing query proof is missing",
        )
        _BASE._require(
            self._last_packing_query_proof["query_sha256"]
            == hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "post-query query pairing mismatch",
        )
        _BASE._require(
            self._last_packing_query_proof["context_sha256"]
            == _BASE._sha256_json(memory_context),
            "post-query packing context mismatch",
        )
        return dict(self._last_packing_query_proof)
