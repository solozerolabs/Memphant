import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "docs/build-log/artifacts/next-evidence/authorization-request.json"
OLD_PACKET = ROOT / "docs/build-log/artifacts/rung7-packing-reader-gate/authorization-request.json"
OLD_REPORT = ROOT / "docs/build-log/2026-07-23-rung7-packing-reader-authorization.md"
FORGETTING_SCREEN = ROOT / "docs/build-log/2026-07-24-forgeteval-next-evidence.md"
AUTHORIZATION_REPORT = ROOT / "docs/build-log/2026-07-24-next-evidence-authorization.md"
CLEAN_HANDOFF = ROOT / "docs/build-log/2026-07-24-next-evidence-clean-landing.md"
TERMINAL_MANIFEST = ROOT / "docs/build-log/2026-07-24-tri-sota-terminal-reconciliation.md"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_combined_packet_closes_all_paid_campaigns() -> None:
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    assert packet["status"] == "COMPLETED_PAID_EXECUTION"
    assert packet["paid_calls_executed"] == 310
    assert packet["metered_provider_calls_executed"] == 298
    assert packet["subscription_task_calls_executed"] == 12
    assert packet["settled_cost_usd"] == "0.529098125"
    assert packet["maximum_currently_authorizable_usd"] == "0"
    assert packet["authorizable_campaigns"] == []
    assert packet["campaigns"]["packing"]["authorization"] is None
    forgetting = packet["campaigns"]["forgetting_proposals"]
    assert forgetting["status"] == "COMPLETED_PAID_PROPOSALS"
    assert forgetting["authorization"] is None
    assert forgetting["completion"]["full_provider_attempts"] == 258
    assert forgetting["completion"]["unsettled_cost_usd"] == "0"
    coding = packet["campaigns"]["swe_contextbench"]
    assert coding["status"] == "COMPLETED_REJECTED_AT_FIRST_TRANCHE_BASELINE_CEILING"
    assert coding["official_resolved_baselines"] == 3
    assert coding["maximum_possible_related_gain"] == 1
    assert coding["remaining_task_calls_executed"] == 0
    assert coding["authoritative_child_packet_sha256"] == sha256_file(
        ROOT / coding["authoritative_child_packet"]
    )
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
    assert child["status"] == "COMPLETED_PAID_PROPOSALS"
    assert child["paid_calls_executed"] == 258
    assert child["settled_cost_usd"] == "0.3632000"
    assert child["completion"]["proposal_output_sha256"] == sha256_file(
        ROOT / child["execution"]["output"]
    )
    assert child["completion"]["attempt_ledger_sha256"] == sha256_file(
        ROOT / child["execution"]["attempt_ledger"]
    )
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
    assert child["code"]["provider_attempt_journal_sha256"] == (
        "4c581a109a3a4887a5e3b7a34ebe32b784e9086af4815d394fff14dc74204fdd"
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


def test_terminal_handoff_cannot_regress_to_pre_execution_accounting() -> None:
    forgetting_screen = FORGETTING_SCREEN.read_text(encoding="utf-8")
    authorization_report = AUTHORIZATION_REPORT.read_text(encoding="utf-8")
    handoff = CLEAN_HANDOFF.read_text(encoding="utf-8")
    manifest = TERMINAL_MANIFEST.read_text(encoding="utf-8")

    assert "Superseded historical screen" in forgetting_screen
    assert "293 provider calls" in forgetting_screen
    assert "$0.4150225 settled cost" in forgetting_screen
    assert "294 proposals" in authorization_report
    assert "293 provider calls" in authorization_report
    assert "310 calls" in handoff
    assert "$0.529098125" in handoff
    assert "c70feead5b748383c587fc5f46b15161ff92af12" in handoff
    assert "ac9c1c979cc525220213474f93facc021a025016" in handoff
    assert "No model call was made and settled cost is $0" not in handoff
    assert "AWAITING_EXPLICIT_PAID_AUTHORIZATION" not in handoff
    for commit in (
        "8ef48afd",
        "ee69e9aa",
        "017078c5",
        "c70feead",
    ):
        assert commit in manifest
