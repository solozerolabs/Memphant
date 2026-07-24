import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "docs/build-log/artifacts/c3-public-code-lane-v3"


def test_rejected_c3_probe_receipt_binds_sealed_provenance() -> None:
    receipt = json.loads((ARTIFACT_DIR / "rejection-receipt.json").read_text())
    provenance = ARTIFACT_DIR / receipt["artifact"]

    assert receipt["schema"] == "memphant.eval.rejection-receipt.v1"
    assert receipt["status"] == "rejected_candidate_bank"
    assert hashlib.sha256(provenance.read_bytes()).hexdigest() == receipt["artifact_sha256"]
    assert receipt["non_claims"]
