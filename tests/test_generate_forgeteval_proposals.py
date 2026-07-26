from __future__ import annotations

import copy
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAINTAINED_ENTRYPOINT = ROOT / "scripts/run_forgeteval_proposals.py"
SPEC = importlib.util.spec_from_file_location(
    "generate_forgeteval_proposals",
    ROOT / "scripts/generate_forgeteval_proposals.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_maintained_entrypoint_uses_canonical_campaign_authority() -> None:
    source = MAINTAINED_ENTRYPOINT.read_text(encoding="utf-8")
    assert "open_campaign_ledger" in source
    assert "ProviderAttemptLedger(" not in source
    assert "generate_forgeteval_proposals.py" not in source


def proposal_input(operation="supersede"):
    value = {
        "case_id": "case-a",
        "mutation_index": 1,
        "operation": operation,
        "query": "user employer" if operation == "supersede" else "everything about Ada",
        "new_text": "Ada now works at Anthropic." if operation == "supersede" else None,
        "candidates": [
            {"index": 0, "body": "Ada plays cello.", "body_sha256": "a" * 64},
            {"index": 1, "body": "Ada works at Stripe.", "body_sha256": "b" * 64},
        ],
    }
    value["input_sha256"] = module.sha256_json(value)
    return value


def test_parse_supersession_proposal_is_strict_and_hash_bound() -> None:
    value = proposal_input()
    parsed = module.parse_proposal(
        '{"selected_indices":[1],"replacement_text":"Ada now works at Anthropic."}',
        value,
    )
    assert parsed["selected_body_sha256"] == ["b" * 64]
    assert parsed["confirmed"] is False
    assert len(parsed["proposal_sha256"]) == 64


@pytest.mark.parametrize(
    "reply,match",
    [
        ("```json\n{}\n```", "not strict JSON"),
        ('{"selected_indices":[0,0],"replacement_text":"x"}', "unique integers"),
        ('{"selected_indices":[2],"replacement_text":"x"}', "outside"),
        ('{"selected_indices":[],"replacement_text":"x"}', "exactly one"),
        ('{"selected_indices":[0],"replacement_text":""}', "nonempty"),
    ],
)
def test_parse_proposal_fails_closed(reply, match) -> None:
    with pytest.raises(ValueError, match=match):
        module.parse_proposal(reply, proposal_input(),)


def test_release_proposal_allows_empty_selection() -> None:
    parsed = module.parse_proposal('{"selected_indices":[]}', proposal_input("release"))
    assert parsed["selected_body_sha256"] == []
    assert parsed["replacement_text"] is None


def test_authorization_must_be_explicit_and_exact(tmp_path) -> None:
    input_path = tmp_path / "inputs.json"
    input_path.write_text("{}")
    execution = {
        "input_sha256": module.sha256_file(input_path),
        "output": str(tmp_path / "out.json"),
        "attempt_ledger": str(tmp_path / "attempts.jsonl"),
        "cache_dir": str(tmp_path / "cache"),
        "model": "openai/gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_calls": 16,
        "max_provider_attempts": 32,
        "max_output_tokens": 256,
        "max_spend_usd": "0.50",
        "max_price_prompt_per_million": "2.75",
        "max_price_completion_per_million": "16.5",
        "system_prompt_sha256": module.sha256_bytes(module.SYSTEM_PROMPT.encode()),
    }
    kwargs = {
        "input_path": input_path,
        "output_path": tmp_path / "out.json",
        "attempt_ledger": tmp_path / "attempts.jsonl",
        "cache_dir": tmp_path / "cache",
        "model": "openai/gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_calls": 16,
        "max_provider_attempts": 32,
        "max_output_tokens": 256,
        "max_spend_usd": Decimal("0.50"),
        "prompt_price": Decimal("2.75"),
        "completion_price": Decimal("16.5"),
    }
    code = {
        "proposal_generator": "scripts/generate_forgeteval_proposals.py",
        "proposal_generator_sha256": module.sha256_file(module.Path(module.__file__)),
        "reader_client_sha256": module.sha256_file(ROOT / "scripts/run_reader.py"),
        "provider_attempt_journal_sha256": module.sha256_file(
            ROOT / "scripts/provider_attempts.py"
        ),
        "proposal_system_prompt_sha256": module.sha256_bytes(
            module.SYSTEM_PROMPT.encode()
        ),
    }
    scope = {"execution": execution, "code": code}
    authorized = {
        "status": "AUTHORIZED_FOR_PAID_EXECUTION",
        **scope,
        "authorization": {
            "authorized_by": "test",
            "authorized_at": "2026-07-24T00:00:00Z",
            "authorization_scope_sha256": module.sha256_json(scope),
        },
    }
    with pytest.raises(ValueError, match="not explicitly authorized"):
        module.validate_authorization(
            {"status": "AWAITING_EXPLICIT_PAID_AUTHORIZATION", **scope},
            **kwargs,
        )
    module.validate_authorization(authorized, **kwargs)
    drifted = copy.deepcopy(authorized)
    drifted["execution"]["max_calls"] = 17
    drifted_scope = {
        key: value for key, value in drifted.items() if key not in {"status", "authorization"}
    }
    drifted["authorization"]["authorization_scope_sha256"] = module.sha256_json(
        drifted_scope
    )
    with pytest.raises(ValueError, match="max_calls"):
        module.validate_authorization(drifted, **kwargs)

    code_drift = copy.deepcopy(authorized)
    code_drift["code"]["provider_attempt_journal_sha256"] = "0" * 64
    code_scope = {
        key: value for key, value in code_drift.items() if key not in {"status", "authorization"}
    }
    code_drift["authorization"]["authorization_scope_sha256"] = module.sha256_json(
        code_scope
    )
    with pytest.raises(ValueError, match="code hash mismatch"):
        module.validate_authorization(code_drift, **kwargs)
