import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "docs/build-log/artifacts/next-evidence/authorization-request.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_bootstrap():
    path = ROOT / "benchmarks/longmemeval_v2/packing_harness_bootstrap.py"
    spec = importlib.util.spec_from_file_location("packing_harness_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_combined_packet_is_frozen_and_non_authorizing() -> None:
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    assert packet["status"] == "AWAITING_EXPLICIT_PAID_AUTHORIZATION"
    assert packet["paid_calls_executed"] == 0
    assert packet["settled_cost_usd"] == "0"
    assert packet["maximum_currently_authorizable_usd"] == "8.50"
    assert packet["campaigns"]["packing"]["authorization"] is None
    assert packet["campaigns"]["forgetting_proposals"]["authorization"] is None
    assert packet["campaigns"]["swe_contextbench"]["status"] == "BLOCKED_NOT_AUTHORIZABLE"
    assert packet["campaigns"]["deep_swe_pairing"]["status"].startswith("REJECTED_")


def test_packing_packet_binds_every_local_input() -> None:
    campaign = json.loads(PACKET.read_text(encoding="utf-8"))["campaigns"]["packing"]
    bindings = {
        "case_manifest_sha256": ROOT / campaign["frozen_inputs"]["case_manifest"],
        "adapter_lock_sha256": ROOT / campaign["frozen_inputs"]["adapter_lock"],
        "campaign_runner_sha256": ROOT / "scripts/run_longmemeval_v2_packing_campaign.py",
        "campaign_analyzer_sha256": ROOT / "scripts/analyze_longmemeval_v2_packing_campaign.py",
        "harness_runner_sha256": ROOT / "scripts/run_longmemeval_v2_packing.py",
        "bootstrap_sha256": ROOT / "benchmarks/longmemeval_v2/packing_harness_bootstrap.py",
        "paid_meter_sha256": ROOT / "benchmarks/longmemeval_v2/paid_meter.py",
        "provider_attempt_journal_sha256": ROOT / "scripts/provider_attempts.py",
        "acquisition_sha256": ROOT / "scripts/acquire_longmemeval_v2_packing.py",
        "slice_builder_sha256": ROOT / "scripts/prepare_longmemeval_v2_packing_slice.py",
    }
    for field, path in bindings.items():
        section = campaign["frozen_inputs"] if field in campaign["frozen_inputs"] else campaign["code"]
        assert section[field] == sha256_file(path)
    assert len(campaign["allowed_contexts"]) == 10
    assert len({json.dumps(row, sort_keys=True) for row in campaign["allowed_contexts"]}) == 10
    assert campaign["hard_limits"]["logical_calls"] == 90
    assert campaign["hard_limits"]["max_provider_attempts"] == 90
    assert campaign["hard_limits"]["sdk_retries"] == 0


def test_bootstrap_rejects_packet_before_provider_access(tmp_path: Path) -> None:
    module = load_bootstrap()
    campaign = json.loads(PACKET.read_text(encoding="utf-8"))["campaigns"]["packing"]
    with pytest.raises(RuntimeError, match="has not been explicitly authorized"):
        module._validate_authorization(
            PACKET,
            "packing",
            repo_root=ROOT,
            context=campaign["allowed_contexts"][0],
            attempt_ledger=ROOT / campaign["execution"]["attempt_ledger"],
            max_attempts=campaign["hard_limits"]["max_provider_attempts"],
            max_spend_usd=campaign["hard_limits"]["max_spend_usd"],
            prices=campaign["models"]["prices_usd_per_million"],
            output_caps=campaign["models"]["max_completion_tokens"],
        )
