from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_forgeteval_benchmark_confirmation",
    ROOT / "scripts/build_forgeteval_benchmark_confirmation.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fixture_documents(replacement: str) -> tuple[dict, dict, list]:
    old = "User works at Stripe."
    new = "User now works at Anthropic."
    source = {
        "case_id": "case-a",
        "mutation_index": 1,
        "operation": "supersede",
        "query": "user employer",
        "new_text": new,
        "candidates": [
            {"index": 0, "body": old, "body_sha256": module.sha256_bytes(old.encode())}
        ],
    }
    source["input_sha256"] = module.sha256_json(source)
    proposal = {
        "input_sha256": source["input_sha256"],
        "case_id": "case-a",
        "mutation_index": 1,
        "operation": "supersede",
        "selected_body_sha256": [source["candidates"][0]["body_sha256"]],
        "replacement_text": replacement,
        "proposal_sha256": "a" * 64,
    }
    case = SimpleNamespace(
        id="case-a",
        setup_facts=[old],
        mutations=[("supersede", "user employer", new)],
        must_contain=["Anthropic"],
        must_not_contain=["Stripe"],
    )
    return {"proposals": [proposal]}, {"inputs": [source]}, [case]


def build(proposals: dict, inputs: dict, cases: list, overrides: dict) -> dict:
    return module.build_confirmation(
        proposals,
        inputs,
        cases,
        overrides,
        reviewed_by="reviewer",
        reviewed_at="2026-07-24T00:00:00Z",
        proposals_path="proposals.json",
        proposals_sha256="b" * 64,
        inputs_path="inputs.json",
        inputs_sha256="c" * 64,
    )


def test_hash_bound_override_removes_prompt_label_and_preserves_transition() -> None:
    proposals, inputs, cases = fixture_documents(
        "NEW_FACT: User now works at Anthropic."
    )
    with pytest.raises(ValueError, match="exact NEW_FACT"):
        build(proposals, inputs, cases, {"overrides": {}})

    result = build(
        proposals,
        inputs,
        cases,
        {
            "overrides": {
                "a" * 64: {
                    "replacement_text": "User now works at Anthropic.",
                    "reason": "remove label",
                }
            }
        },
    )
    assert result["review_summary"] == {
        "proposal_count": 1,
        "override_count": 1,
        "transition_chain_failures": 0,
        "state_oracle_passed": 1,
        "state_oracle_failed": 0,
        "state_oracle_failures": [],
    }
    assert result["confirmations"][0]["replacement_text"] == (
        "User now works at Anthropic."
    )


def test_exact_new_fact_policy_discards_model_rewrite() -> None:
    proposals, inputs, cases = fixture_documents(
        "User now works at Anthropic. User still works at Stripe."
    )
    result = module.build_confirmation(
        proposals,
        inputs,
        cases,
        {"overrides": {}},
        reviewed_by="reviewer",
        reviewed_at="2026-07-24T00:00:00Z",
        proposals_path="proposals.json",
        proposals_sha256="b" * 64,
        inputs_path="inputs.json",
        inputs_sha256="c" * 64,
        replacement_policy="exact_new_fact",
    )
    assert result["replacement_policy"] == "exact_new_fact"
    assert result["review_summary"]["state_oracle_passed"] == 1
    assert result["confirmations"][0]["replacement_text"] == (
        "User now works at Anthropic."
    )


def test_incomplete_supported_transition_chain_fails_closed() -> None:
    proposals, inputs, cases = fixture_documents("User now works at Anthropic.")
    cases[0].mutations = [
        ("supersede", "user employer", "User now works at Anthropic."),
        ("supersede", "user employer", "User now works at OpenAI."),
    ]

    with pytest.raises(ValueError, match="incomplete transition chain"):
        build(proposals, inputs, cases, {"overrides": {}})


def test_lineage_completion_targets_the_previous_transition_exactly() -> None:
    prior = "User now works at Anthropic."
    distractor = "Quarterly planning is Friday."
    second = {
        "case_id": "case-a",
        "mutation_index": 2,
        "operation": "supersede",
        "query": "user employer Anthropic",
        "new_text": "User now works at OpenAI.",
        "candidates": [
            {
                "index": 0,
                "body": distractor,
                "body_sha256": "8622127570615d608b7957aa32fd6bdea25d5a71a85239ae97ccec0397d86431",
            },
            {
                "index": 1,
                "body": prior,
                "body_sha256": "e135a7433c2b8b6236bbc6ed8afbec2b595046de2cc06bfd25877d6a7dc80eb7",
            },
        ],
    }
    second["input_sha256"] = module.sha256_json(second)
    base = {
        "schema_version": 1,
        "confirmations": [
            {
                "input_sha256": "a" * 64,
                "case_id": "case-a",
                "mutation_index": 1,
                "operation": "supersede",
                "confirmed": True,
                "confirmed_by": "reviewer",
                "selected_body_sha256": ["b" * 64],
                "replacement_text": prior,
                "proposal_sha256": "c" * 64,
            }
        ],
    }
    case = SimpleNamespace(
        id="case-a",
        mutations=[
            ("supersede", "user employer", prior),
            ("supersede", "user employer Anthropic", "User now works at OpenAI."),
        ],
    )

    result = module.extend_lineage_confirmations(
        base,
        {"inputs": [second]},
        [case],
        reviewed_by="reviewer",
        reviewed_at="2026-07-24T00:00:00Z",
        inputs_path="pass1.json",
        inputs_sha256="d" * 64,
    )

    completed = result["confirmations"][1]
    assert completed["selected_body_sha256"] == [
        "e135a7433c2b8b6236bbc6ed8afbec2b595046de2cc06bfd25877d6a7dc80eb7"
    ]
    assert completed["replacement_text"] == "User now works at OpenAI."
    assert completed["selection_source"] == "deterministic_previous_transition"
