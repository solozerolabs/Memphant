from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS_PHASE_RE = re.compile(r"^>?\s*# CURRENT PHASE: `([^`]+)`$", re.MULTILINE)
REMOVED_PRIVATE_WORKTREE = "/Users/sidsharma/Syndai/.wt/codex-memphant-cross-repo"
REMOVED_LINKED_MANIFEST = ".codex/linked-repos.json"
CAMPAIGN_PATH_RE = re.compile(r"docs/build-log/artifacts/p1-t6/[A-Za-z0-9._/-]+")


def test_memphant_lock_has_required_schema_keys() -> None:
    lock = json.loads((ROOT / "memphant.lock").read_text())

    assert sorted(lock) == [
        "compiler_version",
        "engine_version",
        "export_schema_version",
        "methodology_version",
        "schema_compat_revision",
        "trace_schema_version",
    ]


def test_porting_doc_replaces_linked_worktree_manifest() -> None:
    porting = (ROOT / "porting.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert not (ROOT / REMOVED_LINKED_MANIFEST).exists()
    assert "porting.md" in agents
    assert "porting.md" in readme
    assert "Do not track a local Syndai worktree path" in porting


def test_live_docs_use_porting_doc_instead_of_linked_worktree_manifest() -> None:
    live_docs = [
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "docs" / "handoff" / "2026-07-04-completion-handoff.md",
        ROOT / "docs" / "superpowers" / "specs" / "memphant" / "STATUS.md",
    ]

    offenders = [
        str(path.relative_to(ROOT))
        for path in live_docs
        if any(
            removed in path.read_text(encoding="utf-8")
            for removed in [REMOVED_PRIVATE_WORKTREE, REMOVED_LINKED_MANIFEST]
        )
    ]

    assert offenders == []


def test_required_memphant_specs_are_present() -> None:
    required = [
        "STATUS.md",
        "29-implementation-plan.md",
        "26-decision-register.md",
        "00-relations-graph.md",
        "03-engineering-spec.md",
        "04-memory-model-spec.md",
        "05-retrieval-and-eval-spec.md",
        "02-architecture-spec.md",
        "08-api-sdk-mcp-spec.md",
        "06-trust-security-spec.md",
        "25-db-provider-byoc-and-app-surface-spec.md",
        "14-ingestion-seeding-and-ops-spec.md",
        "27-sota-ladder-and-validation.md",
        "07-syndai-integration-spec.md",
        "28-syndai-code-contract.md",
    ]
    spec_dir = ROOT / "docs" / "superpowers" / "specs" / "memphant"

    missing = [name for name in required if not (spec_dir / name).exists()]

    assert missing == []


def test_spec_drift_check_passes_against_linked_syndai_docs() -> None:
    result = subprocess.run(
        ["python3", "scripts/check_spec_drift.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "spec_drift=clean" in result.stdout or (
        "spec_drift=skipped reason=private_specs_missing" in result.stdout
    )


def test_governance_docs_pin_public_private_boundary() -> None:
    required = ["LICENSE", "SECURITY.md", "CONTRIBUTING.md", "README.md"]

    missing = [name for name in required if not (ROOT / name).exists()]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert missing == []
    assert "Apache-2.0" in readme
    assert "Syndai adapter" in readme
    assert "DCO" in contributing


def test_repo_hygiene_files_keep_generated_and_private_state_out() -> None:
    required = [
        "Cargo.lock",
        ".editorconfig",
        ".env.example",
        ".gitattributes",
        ".gitignore",
        "rust-toolchain.toml",
        "rustfmt.toml",
    ]

    missing = [name for name in required if not (ROOT / name).exists()]
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    toolchain = (ROOT / "rust-toolchain.toml").read_text(encoding="utf-8")

    assert missing == []
    assert "/target/" in gitignore
    assert "__pycache__/" in gitignore
    assert ".pytest_cache/" in gitignore
    assert ".env.*" in gitignore
    assert "!.env.example" in gitignore
    assert ".codex/feature-flow/" in gitignore
    assert 'channel = "1.96.1"' in toolchain

    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".codex/feature-flow/example.json"],
        cwd=ROOT,
        check=False,
    )

    assert ignored.returncode == 0


def test_live_postgres_ci_gate_excludes_unrelated_doctests() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    command = (
        "cargo test -p memphant-store-postgres -p memphant-worker --all-targets "
        "-- --ignored --test-threads=1"
    )
    assert command in workflow
    assert command in agents


def test_handoff_docs_mirror_status_phase() -> None:
    status = (ROOT / "docs/superpowers/specs/memphant/STATUS.md").read_text(encoding="utf-8")
    status_phase = STATUS_PHASE_RE.search(status)

    assert status_phase is not None

    stale_active_phrases = [
        "COMPLETE banner dishonest",
        "Gaps that keep STATUS.md",
        "candidate_pass behind a checked box",
    ]
    for handoff in (ROOT / "docs/handoff").glob("*.md"):
        text = handoff.read_text(encoding="utf-8")
        active_text = text.split("## Archived Pre-Reconciliation Snapshot", 1)[0]

        assert f"Current STATUS mirror: {status_phase.group(1)}" in text, handoff
        for phrase in stale_active_phrases:
            assert phrase not in active_text, f"{handoff}: {phrase}"


def test_authoritative_handoff_references_existing_campaign_paths() -> None:
    handoff = (ROOT / "docs/handoff/NEXT-SESSION-PROMPT.md").read_text(encoding="utf-8")
    missing = sorted(
        path for path in set(CAMPAIGN_PATH_RE.findall(handoff)) if not (ROOT / path).exists()
    )

    assert ".codex/worktrees/" not in handoff
    assert missing == []


def test_status_cannot_claim_complete_while_runtime_is_in_memory() -> None:
    status = (ROOT / "docs/superpowers/specs/memphant/STATUS.md").read_text(encoding="utf-8")
    phase = STATUS_PHASE_RE.search(status)

    assert phase is not None

    runtime_gaps = {
        "server_in_memory": "AppState::new_in_memory()"
        in (ROOT / "crates/memphant-server/src/main.rs").read_text(encoding="utf-8"),
        "mcp_in_memory": "McpRuntime::new_in_memory()"
        in (ROOT / "crates/memphant-mcp/src/main.rs").read_text(encoding="utf-8"),
        "worker_stub": "memphant-worker ws0"
        in (ROOT / "crates/memphant-worker/src/main.rs").read_text(encoding="utf-8"),
        "postgres_store_lint_only": "impl MemoryStore for PgStore"
        not in (
            (ROOT / "crates/memphant-store-postgres/src/lib.rs").read_text(encoding="utf-8")
            + (ROOT / "crates/memphant-store-postgres/src/store.rs").read_text(encoding="utf-8")
        ),
    }
    postgres_manifest = (ROOT / "crates/memphant-store-postgres/Cargo.toml").read_text(
        encoding="utf-8"
    )

    if any(runtime_gaps.values()):
        assert phase.group(1) != "COMPLETE", runtime_gaps
        assert "- [x] **Public launch gate**" not in status
        assert "- [x] **WS-D** Public surfaces" not in status
        assert "- [x] **WS-H** BYOC + hosted packaging" not in status
        assert "- [x] Hot-path SLO holding" not in status

    if phase.group(1) == "COMPLETE":
        assert any(driver in postgres_manifest for driver in ("sqlx", "tokio-postgres"))
