from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "docs" / "launch" / "public-launch-scorecard.json"


def load_scorecard() -> dict:
    return json.loads(SCORECARD.read_text(encoding="utf-8"))


def status_marks_public_launch_complete() -> bool:
    status = (ROOT / "docs/superpowers/specs/memphant/STATUS.md").read_text(encoding="utf-8")
    return "- [x] **Public launch gate**" in status


def test_public_launch_scorecard_covers_every_gate_criterion() -> None:
    scorecard = load_scorecard()
    criteria = {entry["name"]: entry for entry in scorecard["criteria"]}

    # Evidence reset (2026-07-09): the 2026-07-04 scorecard was produced from
    # synthetic answer-seeded fixtures and is kept only as an audit trail.
    assert scorecard["status"] == "invalid_synthetic_fixture"
    assert scorecard["source_status"] == "fabricated_fixture_20260703"
    assert not status_marks_public_launch_complete()
    assert set(criteria) == {
        "public_api_sdk_mcp_cli_docs_examples",
        "self_host_docker_compose",
        "security_policy_and_release_process",
        "golden_security_sampled_deletion_gates",
        "reproduced_public_benchmark_profile",
        "hosted_db_exposure",
        "no_hidden_syndai_only_behavior",
        "public_sota_claim_policy",
    }
    for entry in criteria.values():
        assert entry["proofs"], entry["name"]
        for proof in entry["proofs"]:
            assert (ROOT / proof).exists(), proof


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
        "npm test",
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


def test_public_benchmark_profile_kept_as_audit_trail_never_promotion_evidence() -> None:
    scorecard = load_scorecard()
    profile_path = ROOT / scorecard["profile"]["path"]
    manifest_path = ROOT / scorecard["profile"]["sample_manifest"]

    # The 2026-07-04 profile/manifest artifacts stay on disk as an audit trail
    # of the invalidated run; the scorecard that references them is marked
    # invalid_synthetic_fixture and cannot gate a launch.
    assert profile_path.exists()
    assert manifest_path.exists()
    assert scorecard["status"] == "invalid_synthetic_fixture"
    assert not status_marks_public_launch_complete()


def test_public_scorecard_cannot_pass_without_postgres_runtime() -> None:
    scorecard = load_scorecard()
    if scorecard["status"] == "pass":
        assert scorecard.get("runtime") == "postgres"


def test_hosted_db_exposure_gate_is_fail_closed_for_supabase() -> None:
    scorecard = load_scorecard()
    hosted = next(entry for entry in scorecard["criteria"] if entry["name"] == "hosted_db_exposure")
    assert hosted["critical_findings"] == []

    supabase_profile = (ROOT / "deploy/provider-profiles/supabase.env.example").read_text(
        encoding="utf-8"
    )
    assert "MEMPHANT_SUPABASE_EXPOSED_SCHEMAS=public" in supabase_profile
    assert "MEMPHANT_SUPABASE_ANON_HAS_MEMPHANT_ACCESS=false" in supabase_profile
    assert "MEMPHANT_SUPABASE_AUTHENTICATED_HAS_MEMPHANT_ACCESS=false" in supabase_profile
    assert "MEMPHANT_SUPABASE_ADVISORS_REQUIRED=true" in supabase_profile
    assert "--fail-on warning" in supabase_profile


def test_public_surfaces_have_no_hidden_syndai_only_fields() -> None:
    scorecard = load_scorecard()
    public_surface = next(
        entry for entry in scorecard["criteria"] if entry["name"] == "no_hidden_syndai_only_behavior"
    )

    for proof in public_surface["proofs"]:
        text = (ROOT / proof).read_text(encoding="utf-8").lower()
        assert "syndai" not in text, proof
        assert "dogfood" not in text, proof


def test_public_sota_claim_policy_is_explicit_and_bare_claims_are_guarded() -> None:
    scorecard = load_scorecard()
    claim = scorecard["sota_claim"]
    assert claim["claim_made"] is False
    assert claim["axis"] is None

    result = subprocess.run(
        ["npm", "test"],
        cwd=ROOT / "web",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
