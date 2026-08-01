# RETRACTION — the 2026-07-03/04 launch scorecards were fabricated fixtures

**Status: permanent retraction receipt. Do not delete this file.**

Three gate scorecards lived in `docs/launch/` until 2026-07-31. Every number in
them was produced from answer-seeded synthetic fixtures — no reader ran, no
scorer ran, no Postgres-backed runtime produced a byte of it. On 2026-07-09 they
were marked `"status": "invalid_synthetic_fixture"` /
`"source_status": "fabricated_fixture_20260703"` and kept as an audit trail. On
2026-07-31 the JSON payloads were deleted under the one-plan Phase C rule that a
fabricated scorecard in-repo is a liability, and replaced by this receipt.

| Deleted file | id | Claimed |
|---|---|---|
| `docs/launch/public-launch-scorecard.json` | `public_launch_gate_2026_07_04` | 8 gate criteria, all with "proofs"; `sota_claim.claim_made: false` |
| `docs/launch/restraint-launch-scorecard.json` | `restraint_launch_gate_2026_07_04` | `ps-bench`, `measured_drop 0.0`, `sample_count 50`, CI `[0.0, 0.0]` |
| `docs/launch/gatemem-conditional-scorecard.json` | `gatemem_conditional_gate_2026_07_04` | GateMem, all three axes `pass`, `sample_count 60` |

The full JSON text remains recoverable from git history at `main@89cc22c4` and
earlier; nothing about the record is lost, only its presence as a live artifact.

## What this retraction still binds

1. **The four gates stay unchecked.** `Public launch gate`, `Restraint launch
   gate`, `GateMem conditional gate`, and `Dogfood gate` may not be checked in
   `docs/superpowers/specs/memphant/STATUS.md` on this evidence. Enforced by
   `tests/test_launch_evidence_contract.py::test_status_ledger_reopened_synthetic_promotions`.
2. **No scorecard JSON may reappear in `docs/launch/` claiming these gates**
   without a Postgres-runtime provenance record. Enforced by
   `tests/test_launch_evidence_contract.py::test_retracted_fixture_scorecards_do_not_return`.
3. **Promotion-provenance rule (2026-07-09) stands:** synthetic fixtures gate
   regressions, never promotions.

## What was deliberately kept

- `docs/build-log/2026-07-03-public-launch-gate.md` and
  `docs/build-log/2026-07-03-restraint-launch-gate.md` — the build logs of the
  invalidated run, as audit trail.
- `docs/build-log/artifacts/real-launch-evidence-20260704-v1/` — the artifacts
  the scorecards pointed at, likewise as audit trail. Their recorded statuses do
  not govern anything.
- `docs/launch/standing-quality-bars.json` — not a fixture; carries no
  `fabricated_fixture` marker.
