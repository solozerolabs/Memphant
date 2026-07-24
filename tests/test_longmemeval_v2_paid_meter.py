from __future__ import annotations

from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
METER = ROOT / "benchmarks/longmemeval_v2/paid_meter.py"


def _load():
    spec = importlib.util.spec_from_file_location("lme_paid_meter", METER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Ledger:
    def __init__(self, attempts=None, cost=0):
        self.attempts = list(attempts or [])
        self.cost = cost

    def snapshot(self):
        return {"attempts": self.attempts, "reported_cost_usd": self.cost}


def _caps(module, ledger, *, attempts=2, spend="1"):
    return module.PaidRunCaps(
        ledger,
        context={"arm": "cap1200", "domain": "web"},
        max_attempts=attempts,
        max_spend_usd=Decimal(spend),
        model_prices={
            "reader": {"prompt": Decimal("0.17"), "completion": Decimal("0.25")}
        },
        model_output_caps={"reader": 1024},
    )


def test_paid_caps_reserve_once_and_block_attempt_overrun():
    module = _load()
    caps = _caps(module, Ledger(), attempts=1)
    request = {"model": "reader", "messages": [{"role": "user", "content": "x"}], "max_completion_tokens": 1024}

    reservation = caps.reserve(request)
    with pytest.raises(RuntimeError, match="duplicate paid request|attempt cap"):
        caps.reserve(request)
    caps.release(reservation)


def test_paid_caps_persist_conservative_liability_metadata():
    module = _load()
    request = {
        "model": "reader",
        "messages": [{"role": "user", "content": "x"}],
        "max_tokens": 256,
    }
    metadata = _caps(module, Ledger()).attempt_metadata(request)
    assert Decimal(metadata["reserved_liability_usd"]) > 0
    assert metadata["input_token_upper_bound"] > 0
    assert metadata["authorized_output_limit"] == 256


def test_paid_caps_block_output_model_spend_and_interrupted_resume():
    module = _load()
    request = {"model": "reader", "messages": [{"role": "user", "content": "x"}], "max_completion_tokens": 1025}
    with pytest.raises(RuntimeError, match="output cap"):
        _caps(module, Ledger()).reserve(request)
    with pytest.raises(RuntimeError, match="outside paid authorization"):
        _caps(module, Ledger()).reserve({**request, "model": "other", "max_completion_tokens": 1})
    with pytest.raises(RuntimeError, match="spend cap"):
        _caps(module, Ledger(cost=0.99999), spend="1").reserve({**request, "max_completion_tokens": 1})
    with pytest.raises(RuntimeError, match="interrupted attempt"):
        _caps(module, Ledger(attempts=[{"status": "error"}])).reserve({**request, "max_completion_tokens": 1})


def test_shared_ledger_fingerprint_is_campaign_scoped(tmp_path):
    provider_path = ROOT / "scripts/provider_attempts.py"
    spec = importlib.util.spec_from_file_location("provider_attempts", provider_path)
    assert spec and spec.loader
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    ledger_path = tmp_path / "attempts.jsonl"
    first = provider.ProviderAttemptLedger(
        ledger_path,
        provider._sha256_json(
            {"schema_version": 2, "context": {"campaign": "packing"}}
        ),
    )
    first.close()
    second = provider.ProviderAttemptLedger(
        ledger_path,
        provider._sha256_json(
            {"schema_version": 2, "context": {"campaign": "packing"}}
        ),
    )
    second.close()
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        provider.ProviderAttemptLedger(
            ledger_path,
            provider._sha256_json(
                {"schema_version": 2, "context": {"campaign": "other"}}
            ),
        )


def test_provider_evidence_hashes_exact_reader_text():
    provider_path = ROOT / "scripts/provider_attempts.py"
    spec = importlib.util.spec_from_file_location("provider_attempts_text", provider_path)
    assert spec and spec.loader
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    response = {
        "id": "response-1",
        "model": "reader",
        "provider": "fixture",
        "choices": [{"message": {"content": "  exact answer  "}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.1},
    }
    evidence = provider.provider_response_evidence(
        response, "reader", 0.1, "a" * 64
    )
    assert evidence["response_text_sha256"] == provider.hashlib.sha256(
        b"exact answer"
    ).hexdigest()
