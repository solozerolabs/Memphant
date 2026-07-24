import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "docs/build-log/artifacts/next-evidence/authorization-request.json"
OLD_PACKET = ROOT / "docs/build-log/artifacts/rung7-packing-reader-gate/authorization-request.json"
OLD_REPORT = ROOT / "docs/build-log/2026-07-23-rung7-packing-reader-authorization.md"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_combined_packet_authorizes_only_the_forgetting_winner_expansion() -> None:
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    assert packet["status"] == "AUTHORIZED_FOR_PAID_EXECUTION"
    assert packet["paid_calls_executed"] == 0
    assert packet["settled_cost_usd"] == "0"
    assert packet["maximum_currently_authorizable_usd"] == "5.00"
    assert packet["authorizable_campaigns"] == ["forgetting_proposals"]
    assert packet["campaigns"]["packing"]["authorization"] is None
    assert packet["campaigns"]["forgetting_proposals"]["status"] == "AUTHORIZED_FOR_PAID_EXECUTION"
    assert packet["campaigns"]["forgetting_proposals"]["authorization"] == {
        "authoritative_child_packet": packet["campaigns"]["forgetting_proposals"]["authoritative_child_packet"]
    }
    assert packet["campaigns"]["swe_contextbench"]["status"] == "BLOCKED_NOT_AUTHORIZABLE"
    assert packet["campaigns"]["deep_swe_pairing"]["status"].startswith("REJECTED_")


def test_packing_rejection_binds_free_evidence_and_has_no_paid_execution_surface() -> None:
    campaign = json.loads(PACKET.read_text(encoding="utf-8"))["campaigns"]["packing"]
    assert campaign["status"] == "REJECTED_AT_FREE_EXACT_ABSTENTION_GATE_NOT_AUTHORIZABLE"
    assert campaign["frozen_inputs"]["case_manifest_sha256"] == sha256_file(
        ROOT / campaign["frozen_inputs"]["case_manifest"]
    )
    assert campaign["frozen_inputs"]["adapter_lock_sha256"] == sha256_file(
        ROOT / campaign["frozen_inputs"]["adapter_lock"]
    )
    gate = campaign["free_exact_abstention_gate"]
    assert gate["current_artifact_sha256"] == sha256_file(ROOT / gate["current_artifact"])
    assert gate["cap1200_artifact_sha256"] == sha256_file(ROOT / gate["cap1200_artifact"])
    assert not ({"execution", "models", "hard_limits", "code"} & set(campaign))
    for relative in campaign["deleted_execution_surface"]:
        assert not (ROOT / relative).exists()


def test_forgetting_child_packet_and_its_code_hashes_are_exact() -> None:
    campaign = json.loads(PACKET.read_text(encoding="utf-8"))["campaigns"]["forgetting_proposals"]
    child_path = ROOT / campaign["authoritative_child_packet"]
    assert campaign["authoritative_child_packet_sha256"] == sha256_file(child_path)
    child = json.loads(child_path.read_text(encoding="utf-8"))
    scope = {
        key: value
        for key, value in child.items()
        if key not in {"status", "authorization"}
    }
    assert child["authorization"]["authorization_scope_sha256"] == hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert child["code"]["proposal_generator_sha256"] == sha256_file(
        ROOT / child["code"]["proposal_generator"]
    )
    assert child["code"]["provider_attempt_journal_sha256"] == sha256_file(
        ROOT / "scripts/provider_attempts.py"
    )


def test_prior_packing_request_is_a_non_actionable_supersession_tombstone() -> None:
    packet = json.loads(OLD_PACKET.read_text(encoding="utf-8"))
    report = OLD_REPORT.read_text(encoding="utf-8")
    assert packet["status"] == "SUPERSEDED_REJECTED_BY_2026_07_24_FREE_EXACT_ABSTENTION_GATE"
    assert packet["authorization"] is None
    assert packet["superseded_by"] == "docs/build-log/artifacts/next-evidence/authorization-request.json"
    assert "superseded, rejected, and not authorizable" in report
    assert "doppler run" not in report
    assert "If separately authorized" not in report
