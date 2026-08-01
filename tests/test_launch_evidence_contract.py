from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "docs/superpowers/specs/memphant/STATUS.md"
RETRACTION = ROOT / "docs/launch/RETRACTED-2026-07-03-fixture-scorecards.md"
LAUNCH_DIR = ROOT / "docs/launch"
PUBLIC_LOG = ROOT / "docs/build-log/2026-07-03-public-launch-gate.md"
RESTRAINT_LOG = ROOT / "docs/build-log/2026-07-03-restraint-launch-gate.md"

# The three 2026-07-03/04 gate scorecards were deleted on 2026-07-31 (one-plan
# Phase C): every number in them came from answer-seeded synthetic fixtures and
# a fabricated scorecard in-repo is a liability. The RETRACTION receipt replaces
# them; the invariants they enforced are re-expressed below against STATUS.md,
# which is where they were actually load-bearing.
RETRACTED_SCORECARDS = [
    "public-launch-scorecard.json",
    "restraint-launch-scorecard.json",
    "gatemem-conditional-scorecard.json",
]

REOPENED_GATE_LABELS = [
    "Public launch gate",
    "Restraint launch gate",
    "GateMem conditional gate",
    "Dogfood gate",
]


def checked(label: str) -> bool:
    return f"- [x] **{label}**" in STATUS.read_text(encoding="utf-8")


def test_retracted_fixture_scorecards_do_not_return() -> None:
    receipt = RETRACTION.read_text(encoding="utf-8")

    for name in RETRACTED_SCORECARDS:
        assert not (LAUNCH_DIR / name).exists(), name
        assert name in receipt, name
    assert "fabricated_fixture_20260703" in receipt
    assert "invalid_synthetic_fixture" in receipt

    # No surviving artifact in docs/launch/ may carry the fixture marker.
    for path in LAUNCH_DIR.glob("*.json"):
        assert "fabricated_fixture" not in path.read_text(encoding="utf-8"), path.name


def test_status_ledger_reopened_synthetic_promotions() -> None:
    status = STATUS.read_text(encoding="utf-8")

    assert "**Promotion-provenance rule (2026-07-09):**" in status
    assert "Synthetic fixtures gate regressions, never promotions." in status
    assert "CURRENT PHASE: `RUNTIME COMPLETE — BENCHMARK EVIDENCE PENDING`" in status

    for label in REOPENED_GATE_LABELS:
        assert not checked(label), label
    assert "- [x] **WS-F**" not in status
    assert "- [x] **WS-G**" not in status
    # A reopened rung may re-check ONLY with real-runtime evidence: its row
    # must cite the real-retrieval campaign (Postgres runtime, pinned dataset),
    # never the invalidated 2026-07-03 synthetic profile artifacts as proof.
    for rung in range(4, 16):
        marker = f"[x] {rung} "
        if marker not in status:
            continue
        row = next(line for line in status.splitlines() if marker in line)
        assert "real-retrieval-20260710" in row or (
            "2026-07-10-real-retrieval-campaign.md" in row
        ), f"rung {rung} re-checked without real-runtime evidence"
        assert "2026-07-03-rung" not in row.split("proof:")[-1] or (
            "2026-07-10-real-retrieval-campaign.md" in row.split("proof:")[-1]
        ), f"rung {rung} cites synthetic artifacts as promotion proof"
    # Rung 14 is a retirement, not a promotion: if checked, it must say so.
    if "[x] 14 " in status:
        row = next(line for line in status.splitlines() if "[x] 14 " in line)
        assert "retirement stands" in row
    # Rungs whose advance-when was NOT met stay open unless their recorded
    # disable-when fired and the mechanism was actually deleted. Rung 9 is the
    # latter: a terminal rejection, never a synthetic promotion.
    for rung in (5, 6, 7, 8, 10, 11, 12, 13, 15):
        assert f"[x] {rung} " not in status, f"rung {rung} must stay reopened"
    rung_9 = next(line for line in status.splitlines() if "[x] 9 " in line)
    assert "REJECTED AND DELETED" in rung_9
    assert "terminal rejection, not a promotion" in rung_9
    # Rungs 0-3 remain built (built locally, not promoted from synthetic evidence).
    for rung in range(0, 4):
        assert f"[x] {rung} " in status, f"rung {rung} stays checked"
    assert "reopened 2026-07-09: promotion evidence was synthetic fixtures" in status


def test_launch_build_logs_remain_as_audit_trail() -> None:
    # The build logs of the invalidated 2026-07-03/04 run stay on disk, and so
    # do the artifacts they cite. Their recorded statuses no longer govern; the
    # retraction receipt says so explicitly and names both.
    receipt = RETRACTION.read_text(encoding="utf-8")

    assert PUBLIC_LOG.read_text(encoding="utf-8").strip()
    assert RESTRAINT_LOG.read_text(encoding="utf-8").strip()
    assert PUBLIC_LOG.name in receipt
    assert RESTRAINT_LOG.name in receipt
    assert "real-launch-evidence-20260704-v1" in receipt
    assert (ROOT / "docs/build-log/artifacts/real-launch-evidence-20260704-v1").is_dir()


def test_done_definition_explains_dormant_activation_gates() -> None:
    status = STATUS.read_text(encoding="utf-8")

    assert "DORMANT with unmet activation gate" in status
    assert "terminal for §5" in status
    assert "CURRENT PHASE: `COMPLETE`" not in status or (
        checked("Public launch gate") and checked("Restraint launch gate")
    )
