"""Contract tests for the W10 Syndai replacement gate: the golden-set lock and
the evidence-row shape the two engine runners hand to run_reader.py.

These run under ``pytest tests/`` with no network or DB. The verbatim-span pin
(each answer span is present at its recorded char offsets in the real corpus)
is gated on the Syndai corpus being present on disk.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "benchmarks" / "data" / "syndai_docs_golden.jsonl"
GOLDEN_LOCK = ROOT / "benchmarks" / "data" / "syndai_docs_golden.lock.json"
MANIFEST = ROOT / "benchmarks" / "manifests" / "syndai_docs_gate.lock.json"

# v2 (R0-T3): a second, disjoint sample of the SAME pinned corpus (mined with
# --exclude-golden against v1). Tests below are parameterized over both golden
# files; a v2 case skips if the file is absent, same pattern as the existing
# Syndai-root skip in test_answer_spans_are_verbatim_in_the_pinned_corpus.
GOLDEN_V2 = ROOT / "benchmarks" / "data" / "syndai_docs_golden_v2.jsonl"
GOLDEN_V2_LOCK = ROOT / "benchmarks" / "data" / "syndai_docs_golden_v2.lock.json"
NEGATIVE = ROOT / "benchmarks" / "data" / "syndai_docs_negative.jsonl"
NEGATIVE_LOCK = ROOT / "benchmarks" / "data" / "syndai_docs_negative.lock.json"

GOLDEN_SETS = [
    pytest.param(GOLDEN, GOLDEN_LOCK, id="v1"),
    pytest.param(GOLDEN_V2, GOLDEN_V2_LOCK, id="v2"),
]


def _skip_if_absent(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} not present")


def _is_git_repo(path: Path) -> bool:
    """A candidate corpus root is usable only if it is a git checkout."""
    if not path.exists():
        return False
    return (
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
        ).returncode
        == 0
    )


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


REQUIRED_GOLDEN_KEYS = {
    "question_id",
    "question_type",
    "is_abstention",
    "question",
    "question_date",
    "gold_answer",
    "multi_hop",
    "provenance",
}
# The subset run_reader.py actually consumes from an evidence row.
REQUIRED_EVIDENCE_KEYS = {
    "question_id",
    "question_type",
    "is_abstention",
    "question",
    "question_date",
    "gold_answer",
    "evidence",
}


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gc():
    return _load("gate_common", "scripts/gate_common.py")


def _goldens() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().split("\n") if line.strip()]


@pytest.mark.parametrize("golden_path,lock_path", GOLDEN_SETS)
def test_golden_lock_sha256_and_counts_match(golden_path: Path, lock_path: Path) -> None:
    _skip_if_absent(golden_path)
    lock = json.loads(lock_path.read_text())
    raw = golden_path.read_bytes()
    import hashlib

    assert hashlib.sha256(raw).hexdigest() == lock["sha256"], "golden JSONL drifted from its lock"
    goldens = _rows(golden_path)
    assert len(goldens) == lock["count"]
    assert sum(1 for g in goldens if g["multi_hop"]) == lock["multi_hop_count"]


@pytest.mark.parametrize("golden_path,lock_path", GOLDEN_SETS)
def test_golden_rows_are_well_formed(golden_path: Path, lock_path: Path) -> None:
    _skip_if_absent(golden_path)
    goldens = _rows(golden_path)
    assert goldens, "golden set is empty"
    ids = set()
    for g in goldens:
        assert REQUIRED_GOLDEN_KEYS <= set(g), f"missing keys: {REQUIRED_GOLDEN_KEYS - set(g)}"
        assert g["question_id"] not in ids, "duplicate question_id"
        ids.add(g["question_id"])
        assert g["is_abstention"] is False
        assert isinstance(g["question"], str) and g["question"].strip()
        assert isinstance(g["gold_answer"], str) and g["gold_answer"].strip()
        prov = g["provenance"]
        assert isinstance(prov, list) and prov
        for entry in prov:
            assert {"role", "file", "heading_path", "span", "char_start", "char_end"} <= set(entry)
            assert entry["char_end"] > entry["char_start"]
            assert entry["span"].strip()
        if g["multi_hop"]:
            assert len(prov) == 2
            assert {e["role"] for e in prov} == {"bridge", "answer"}
        else:
            assert len(prov) == 1 and prov[0]["role"] == "answer"


def test_evidence_row_shape_is_consumable_by_run_reader() -> None:
    gc = _gc()
    reader = _load("run_reader", "scripts/run_reader.py")
    goldens = _goldens()
    golden = goldens[0]
    row = gc.evidence_row(golden, ["body one text", "body two text"], k=10)
    assert REQUIRED_EVIDENCE_KEYS <= set(row)
    assert [item["rank"] for item in row["evidence"]] == [1, 2]
    assert all({"rank", "session_id", "body"} <= set(item) for item in row["evidence"])
    # run_reader must be able to build a prompt from it without error.
    prompt = reader.build_reader_prompt(row)
    assert golden["question"] in prompt


def test_shared_evidence_packer_is_deterministic_and_records_real_truncation() -> None:
    gc = _gc()
    bodies = ["one two three", "four five six", "seven"]
    packed, facts = gc.pack_evidence(bodies, k=3, budget_tokens=5)
    assert packed == ["one two three", "four five"]
    assert facts == {
        "evidence_packer_sha256": gc.EVIDENCE_PACKER_CONFIG["sha256"],
        "evidence_budget_tokens": 5,
        "evidence_packed_tokens": 5,
        "evidence_truncated_items": 1,
        "evidence_dropped_items": 1,
    }


def test_syndai_runner_keeps_production_adaptive_query_and_observes_reranker() -> None:
    source = (ROOT / "scripts/gate_run_syndai.py").read_text()
    assert "build_adaptive_query\"" not in source
    assert "AsyncMock" not in source
    assert "knowledge_search_rerank_complete" in source
    assert "reranker success was not observed" in source


def test_provenance_hit_is_span_containment_and_multi_hop_needs_both() -> None:
    gc = _gc()
    single = {
        "multi_hop": False,
        "provenance": [{"role": "answer", "span": "Stripe and Payoneer"}],
    }
    assert gc.provenance_hit(single, ["...uses Stripe and Payoneer for payouts"], 10)
    assert not gc.provenance_hit(single, ["unrelated body", "another"], 10)
    # Rank cutoff is respected: a hit at rank 3 does not count at k=2.
    assert not gc.provenance_hit(single, ["x", "y", "Stripe and Payoneer"], 2)

    multi = {
        "multi_hop": True,
        "provenance": [
            {"role": "bridge", "span": "region Taipei"},
            {"role": "answer", "span": "value fifty"},
        ],
    }
    assert gc.provenance_hit(multi, ["region Taipei here", "the value fifty"], 10)
    # Only one of the two required spans present -> not a hit.
    assert not gc.provenance_hit(multi, ["region Taipei here", "no answer"], 10)


def test_corpus_manifest_is_well_formed() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["file_count"] == len(manifest["files"])
    assert manifest["git_commit"]
    assert manifest["excluded_prefixes"] == ["docs/superpowers/"]
    assert manifest["sectionizer"] == "markdown_heading_leaf_v1"
    # Re-pinned 2026-07-22 (C2 pre-check): Syndai docs at commit 96a26f1f842a
    # (docs content identical from 36a7f99d02), 114 files / 4920 sections.
    assert manifest["section_count"] == 4920
    assert manifest["mining_candidate_section_count"] == 3299
    assert manifest["section_revision"] == (
        "sha256:82a1eecacbf0414cc7e0da2923f8725ee948771784141d2994bb5ffbc4035885"
    )
    for entry in manifest["files"].values():
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] >= 0


def _fixture_manifest(gc, root: Path, files: list[str]) -> dict:
    sections = gc.all_sections(root, files)
    return {
        "file_count": len(files),
        "files": {
            rel: {
                "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(),
                "bytes": len((root / rel).read_bytes()),
            }
            for rel in files
        },
        "total_bytes": sum(len((root / rel).read_bytes()) for rel in files),
        "sectionizer": "markdown_heading_leaf_v1",
        "section_count": len(sections),
        "section_chars": sum(len(section.body) for section in sections),
        "section_revision": gc.corpus_revision(sections),
    }


def test_common_corpus_contract_returns_every_section(tmp_path: Path) -> None:
    gc = _gc()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("# One\n\nshort\n\n## Two\n\n" + "x" * 3300)
    files = ["docs/a.md"]
    manifest = _fixture_manifest(gc, tmp_path, files)

    sections = gc.verify_corpus_contract(tmp_path, files, manifest)

    assert len(sections) == 2
    # The full input contract must not reuse the miner's 240..3200-char filter.
    assert len(gc.candidate_sections(sections)) == 0


def test_common_corpus_contract_rejects_skipped_or_changed_input(tmp_path: Path) -> None:
    gc = _gc()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("# A\n\nbody")
    manifest = _fixture_manifest(gc, tmp_path, ["docs/a.md"])

    with pytest.raises(RuntimeError, match="file set mismatch"):
        gc.verify_corpus_contract(tmp_path, [], manifest)

    (tmp_path / "docs/a.md").write_text("# A\n\nchanged")
    with pytest.raises(RuntimeError, match="content mismatch"):
        gc.verify_corpus_contract(tmp_path, ["docs/a.md"], manifest)


def test_common_corpus_contract_rejects_sectionizer_revision_drift(tmp_path: Path) -> None:
    gc = _gc()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("# A\n\nbody")
    manifest = _fixture_manifest(gc, tmp_path, ["docs/a.md"])
    manifest["section_revision"] = "sha256:" + "0" * 64

    with pytest.raises(RuntimeError, match="section revision mismatch"):
        gc.verify_corpus_contract(tmp_path, ["docs/a.md"], manifest)


def test_syndai_gate_scratch_database_refuses_nonlocal_hosts() -> None:
    gc = _gc()
    for url in (
        "postgresql://user:pass@db.example.com/syndai",
        "postgresql://user:pass@project.supabase.com/postgres",
    ):
        with pytest.raises(RuntimeError, match="local Postgres"):
            gc.disposable_database_urls(url, "syndai_gate_test")
    with pytest.raises(RuntimeError, match="not connected"):
        gc.validate_disposable_database_child(
            "postgresql://user:pass@localhost:55432/syndai_local",
            "syndai_gate_test",
        )


def test_syndai_gate_scratch_database_always_drops_after_child_failure(monkeypatch) -> None:
    gc = _gc()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "createdb":
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "dropdb":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 7, "", "child failed")

    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    result = gc.run_in_disposable_database(
        "postgresql://user:pass@127.0.0.1:55432/syndai_local",
        "syndai_gate_test",
        ["python", "gate_run_syndai.py"],
        {"PATH": "/bin"},
    )

    assert result == 7
    assert [call[0][0] for call in calls] == ["createdb", "python", "dropdb"]
    child_env = calls[1][1]["env"]
    assert child_env["DATABASE_URL"].endswith("/syndai_gate_test")
    assert child_env[gc.DISPOSABLE_DATABASE_ENV] == "syndai_gate_test"
    assert child_env[gc.DISPOSABLE_TEMPLATE_ENV] == "syndai_local"
    assert "--template=syndai_local" in calls[0][0]
    assert "--force" in calls[2][0]


def test_syndai_gate_scratch_database_fails_if_cleanup_fails(monkeypatch) -> None:
    gc = _gc()

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1 if command[0] == "dropdb" else 0,
            "",
            "cleanup failed" if command[0] == "dropdb" else "",
        )

    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="drop disposable database failed"):
        gc.run_in_disposable_database(
            "postgresql://user:pass@localhost:55432/syndai_local",
            "syndai_gate_test",
            ["python", "gate_run_syndai.py"],
            {},
        )


def test_database_and_untracked_migration_identity_are_content_bound(tmp_path, monkeypatch) -> None:
    gc = _gc()
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001.sql").write_text("select 1;")
    first = gc.sql_sources_identity(migrations)
    (migrations / "untracked.sql").write_text("select 2;")
    second = gc.sql_sources_identity(migrations)
    assert first["sha256"] != second["sha256"]
    assert "migrations/untracked.sql" in second["files"]

    outputs = [b"CREATE TABLE example();", b"extension:vector=0.8\nmigration:head\n"]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, outputs.pop(0), b"")

    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    identity = gc.database_schema_identity(
        "postgresql://localhost/scratch",
        "select 'migration:' || version_num from alembic_version",
    )
    assert len(identity["schema_sha256"]) == 64
    assert len(identity["extensions_and_migrations_sha256"]) == 64
    assert identity["sha256"] == gc.json_fingerprint(
        {key: value for key, value in identity.items() if key != "sha256"}
    )
def test_database_identity_uses_catalog_sql_and_surfaces_stderr(monkeypatch) -> None:
    gc = _gc()
    commands = []

    def ok_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"facts", b"")

    monkeypatch.setattr(gc.subprocess, "run", ok_run)
    gc.database_schema_identity(
        "postgresql://localhost/db",
        "select 'migration:' || version_num from alembic_version",
    )
    assert [command[0] for command in commands] == ["psql", "psql"]
    assert "pg_get_constraintdef" in commands[0][-1]
    assert "pg_get_policy" not in commands[0][-1]

    def failed_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, b"", b"server said no")

    monkeypatch.setattr(gc.subprocess, "run", failed_run)
    with pytest.raises(RuntimeError, match="server said no"):
        gc.database_schema_identity(
            "postgresql://localhost/db",
            "select 'migration:' || version_num from alembic_version",
        )


def test_both_engine_runners_use_the_full_common_section_input() -> None:
    memphant = (ROOT / "scripts/gate_run_memphant.py").read_text()
    syndai = (ROOT / "scripts/gate_run_syndai.py").read_text()
    assert "gc.load_pinned_corpus(root)" in memphant
    assert "gc.load_pinned_corpus(root)" in syndai
    assert "gc.candidate_sections(gc.all_sections(root, files))" not in memphant
    assert "ingest_corpus(engine, corpus_sections, negative_cases)" in syndai


@pytest.mark.parametrize("golden_path,lock_path", GOLDEN_SETS)
def test_answer_spans_are_verbatim_in_the_pinned_corpus(golden_path: Path, lock_path: Path) -> None:
    """The strongest pin: every recorded span is present at its char offsets in
    the real corpus file (skipped when the golden file or the Syndai corpus is
    not on disk). v1 and v2 share the identical pinned corpus (MANIFEST)."""
    _skip_if_absent(golden_path)
    manifest = json.loads(MANIFEST.read_text())
    # Resolve the Syndai corpus checkout. The canonical layout is a sibling of
    # the Memphant checkout (ROOT.parent / "Syndai"); in a git worktree ROOT is
    # nested deeper, so also try the user home and MEMPHANT_SYNDAI_ROOT before
    # skipping — otherwise this strongest pin skips silently in a worktree while
    # the gate is being re-pinned there (the false-confidence trap the audit
    # flagged).
    candidates = [
        ROOT.parent / manifest["syndai_repo"],
        Path.home() / manifest["syndai_repo"],
    ]
    if env_root := __import__("os").environ.get("MEMPHANT_SYNDAI_ROOT"):
        candidates.insert(0, Path(env_root))
    # A directory that exists but is NOT a git checkout (e.g. a stale
    # /private/tmp/Syndai) cannot answer `git show`, and selecting it turned
    # this skip into a CalledProcessError. Require a real repo, then skip.
    root = next((path for path in candidates if _is_git_repo(path)), None)
    if root is None:
        pytest.skip(f"Syndai corpus not present as a git checkout at any of {candidates}")
    pinned_commit = manifest["git_commit"]
    for g in _rows(golden_path):
        for entry in g["provenance"]:
            # Offsets are meaningful only against the exact corpus commit
            # named by the manifest. Reading the live sister worktree made
            # ordinary later doc edits look like corruption of the frozen
            # golden and encouraged offset re-pins against the wrong bytes.
            result = subprocess.run(
                ["git", "-C", str(root), "show", f"{pinned_commit}:{entry['file']}"],
                check=True,
                capture_output=True,
            )
            text = result.stdout.decode("utf-8", errors="replace")
            excerpt = text[entry["char_start"] : entry["char_end"]]
            assert excerpt == entry["span"], (
                f"{g['question_id']} {entry['role']} span not verbatim at offsets in {entry['file']}"
            )


def test_v2_has_no_question_id_or_section_key_overlap_with_v1() -> None:
    """The R0-T3 mining sanity check, pinned permanently as a contract test:
    v2 was mined with --exclude-golden against v1, so the two sets must share
    zero question_ids and zero source_section_key values (skips if v2 absent,
    same pattern as the other v2-parameterized cases above)."""
    _skip_if_absent(GOLDEN_V2)
    v1_rows = _rows(GOLDEN)
    v2_rows = _rows(GOLDEN_V2)

    v1_ids = {g["question_id"] for g in v1_rows}
    v2_ids = {g["question_id"] for g in v2_rows}
    assert not (v1_ids & v2_ids), "v2 question_id collides with v1"

    def section_keys(rows: list[dict]) -> set[str]:
        keys: set[str] = set()
        for g in rows:
            keys.update(g["source_section_key"].split("||"))
        return keys

    v1_keys = section_keys(v1_rows)
    v2_keys = section_keys(v2_rows)
    assert not (v1_keys & v2_keys), "v2 source_section_key overlaps with v1"


def test_negative_slice_is_hash_locked_strict_and_disjoint() -> None:
    gc = _gc()
    rows = gc.load_negative_cases(NEGATIVE, NEGATIVE_LOCK)
    lock = json.loads(NEGATIVE_LOCK.read_text())

    assert len(rows) == 10
    assert {row["case_kind"] for row in rows} == {
        "unrelated",
        "lexical_collision",
        "plausible_absent",
        "wrong_tenant",
        "wrong_user",
        "wrong_project",
        "wrong_agent",
        "post_snapshot",
        "stale_superseded_only",
        "answerable_but_unsupported",
    }
    positive_ids = {
        row["question_id"] for path in (GOLDEN, GOLDEN_V2) for row in _rows(path)
    }
    assert not (positive_ids & {row["case_id"] for row in rows})
    canaries = [value for row in rows for value in row["forbidden"]]
    assert len(canaries) == len(set(canaries))
    assert all(value.startswith("MPH_NEG_") for value in canaries)
    assert lock["created_at"] == "2026-07-13T00:00:00Z"
    assert lock["corpus_revision"] == json.loads(MANIFEST.read_text())["section_revision"]
    assert set(lock["positive_lock_sha256"]) == {"v1", "v2"}
    assert all("recorded_at" not in document for row in rows for document in row["ingest"])
    historical = [row for row in rows if row["query"]["transaction_as_of"] is not None]
    assert [row["case_kind"] for row in historical] == ["post_snapshot"]
    post_snapshot = historical[0]
    assert "recorded_at" not in post_snapshot["ingest"][0]
    unrelated = next(row for row in rows if row["case_kind"] == "unrelated")
    assert not {"cobalt", "orchard", "protocol"} & set(
        unrelated["ingest"][0]["body"].lower().split()
    )
    with pytest.raises(ValueError, match="overlap positive"):
        gc.load_negative_cases(
            NEGATIVE,
            NEGATIVE_LOCK,
            disjoint_question_ids={rows[0]["case_id"]},
        )


def test_negative_semantic_validator_rejects_scope_noop(tmp_path: Path) -> None:
    gc = _gc()
    rows = _rows(NEGATIVE)
    next(row for row in rows if row["case_kind"] == "wrong_tenant")["ingest"][0][
        "scope"
    ] = "active"
    data = tmp_path / NEGATIVE.name
    data.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
    lock = json.loads(NEGATIVE_LOCK.read_text())
    lock["sha256"] = hashlib.sha256(data.read_bytes()).hexdigest()
    lock_path = tmp_path / NEGATIVE_LOCK.name
    lock_path.write_text(json.dumps(lock))

    with pytest.raises(ValueError, match="wrong_tenant ingest semantics"):
        gc.load_negative_cases(data, lock_path)


def test_negative_projections_do_not_leak_evaluator_labels_to_capture_stub() -> None:
    gc = _gc()
    rows = gc.load_negative_cases(NEGATIVE, NEGATIVE_LOCK)
    captured = {"ingest": [], "query": []}

    def ingest_stub(payload):
        captured["ingest"].append(payload)

    def query_stub(payload):
        captured["query"].append(payload)

    for row in rows:
        for document in gc.negative_ingest_projection(row):
            ingest_stub(document)
        query_stub(gc.negative_query_projection(row))

    serialized = json.dumps(captured, sort_keys=True)
    for label in ("gold", "case_kind", "forbidden", "expect"):
        assert f'"{label}"' not in serialized
    assert all(set(payload) <= {"document_id", "body", "scope", "valid_from", "valid_to"} for payload in captured["ingest"])
    assert all(set(payload) <= {"question", "transaction_as_of", "valid_at"} for payload in captured["query"])


def test_negative_report_fails_closed_when_runtime_cannot_honor_case_semantics() -> None:
    gc = _gc()
    cases = gc.load_negative_cases(NEGATIVE, NEGATIVE_LOCK)
    rows = [
        gc.negative_result_row(
            case,
            [],
            supported=case["case_kind"] != "post_snapshot",
            unsupported_reason=(
                "transaction_snapshot_contract_absent"
                if case["case_kind"] == "post_snapshot"
                else None
            ),
        )
        for case in cases
    ]

    report = gc.negative_report(rows)

    assert report["negative_case_count"] == 10
    assert report["negative_forbidden_hit_count"] == 0
    assert report["negative_forbidden_hit_rate"] == 0.0
    assert report["negative_unsupported_count"] == 1
    assert report["negative_promotion_eligible"] is False
    unsupported = next(row for row in report["negative_per_case"] if row["case_kind"] == "post_snapshot")
    assert unsupported["supported"] is False
    assert unsupported["unsupported_reason"] == "transaction_snapshot_contract_absent"
    assert unsupported["passed"] is False


def test_negative_report_counts_forbidden_canary_hits_separately() -> None:
    gc = _gc()
    case = gc.load_negative_cases(NEGATIVE, NEGATIVE_LOCK)[0]
    row = gc.negative_result_row(case, [f"leak {case['forbidden'][0]}"], supported=True)

    report = gc.negative_report([row])

    assert report["negative_forbidden_hit_count"] == 1
    assert report["negative_forbidden_hit_rate"] == 1.0
    assert report["negative_promotion_eligible"] is False


def test_negative_evidence_row_uses_abstention_contract() -> None:
    gc = _gc()
    reader = _load("run_reader_negative", "scripts/run_reader.py")
    case = gc.load_negative_cases(NEGATIVE, NEGATIVE_LOCK)[0]

    row = gc.negative_evidence_row(case, ["retrieved body"], k=10)

    assert row["question_id"] == case["case_id"]
    assert row["is_abstention"] is True
    assert row["gold_answer"] == "ABSTAIN"
    assert "retrieved body" in reader.build_reader_prompt(row)


def test_both_runners_expose_negative_slice_without_mixing_positive_provenance() -> None:
    for name in ("gate_run_memphant.py", "gate_run_syndai.py"):
        source = (ROOT / "scripts" / name).read_text()
        assert "--negative-slice" in source
        assert "--out-negative-evidence" in source
        assert "gc.negative_report" in source
        assert 'report["negative"]' in source
        assert "raw_bodies[: args.k]" in source
    syndai = (ROOT / "scripts" / "gate_run_syndai.py").read_text()
    assert '"stale_superseded_only": "natural-language dated query (no valid_at parameter)"' in syndai
