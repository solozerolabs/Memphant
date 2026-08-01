from __future__ import annotations

from pathlib import Path

# The scorecard this file was built around (`docs/launch/public-launch-scorecard.json`)
# was a fabricated fixture and was deleted on 2026-07-31; see
# `docs/launch/RETRACTED-2026-07-03-fixture-scorecards.md` and
# `tests/test_launch_evidence_contract.py`. What survives here are the gate
# contracts that never depended on it: the release process and CI must actually
# run the commands the gate names, and the Supabase profile must fail closed.

ROOT = Path(__file__).resolve().parents[1]


def test_release_process_and_ci_run_public_launch_gates() -> None:
    release_process = (ROOT / "docs/release-process.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    required_commands = [
        "cargo fmt --check",
        "cargo clippy --all-targets --all-features -- -D warnings",
        # --workspace is the floor: `-p X --lib` excludes every tests/ file.
        "cargo test --workspace --all-targets --all-features",
        "cargo test --doc",
        "python -m pytest tests -q",
        "cargo run -p memphant-eval -- verify-golden examples/evals/golden.yaml",
        "cargo run -p memphant-eval -- run benchmarks/nightly-sampled.yaml",
        "cargo run -p memphant-eval -- security examples/evals/security-smoke.yaml",
        "cargo run -p memphant-cli -- db bootstrap-check --provider supabase",
    ]

    for command in required_commands:
        assert command in release_process
        assert command in workflow
    assert "python3 scripts/check_spec_drift.py" in release_process
    assert "scripts/check_spec_drift.py" not in workflow


def test_python_lineage_contracts_run_with_full_git_history() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    public_gates = workflow.split("  postgres-contracts:", maxsplit=1)[0]

    assert "fetch-depth: 0" in public_gates
    assert public_gates.index("fetch-depth: 0") < public_gates.index(
        "python -m pytest tests -q"
    )


def test_ci_cache_action_uses_node24_runtime() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "uses: actions/cache@v5" in workflow
    assert "uses: actions/cache@v4" not in workflow


def test_hosted_db_exposure_gate_is_fail_closed_for_supabase() -> None:
    supabase_profile = (ROOT / "deploy/provider-profiles/supabase.env.example").read_text(
        encoding="utf-8"
    )
    assert "MEMPHANT_SUPABASE_EXPOSED_SCHEMAS=public" in supabase_profile
    assert "MEMPHANT_SUPABASE_ANON_HAS_MEMPHANT_ACCESS=false" in supabase_profile
    assert "MEMPHANT_SUPABASE_AUTHENTICATED_HAS_MEMPHANT_ACCESS=false" in supabase_profile
    assert "MEMPHANT_SUPABASE_ADVISORS_REQUIRED=true" in supabase_profile
    assert "--fail-on warning" in supabase_profile


def test_public_sota_claim_policy_is_explicit() -> None:
    release_process = (ROOT / "docs/release-process.md").read_text(encoding="utf-8")

    # No scorecard exists to carry `sota_claim.claim_made: false` any more, so
    # the policy has to be stated where a human writing release notes will read
    # it. This is the whole claim guard now.
    assert "## Claim Policy" in release_process
    assert "No public SOTA claim is made by default." in release_process
