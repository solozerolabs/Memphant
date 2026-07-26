from __future__ import annotations

import importlib.util
import fcntl
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import urllib.parse

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_IDS = {
    "19367bc7", "21f3228c", "2c45ecbb", "52dd33bb", "658fa827", "6fdda2fc",
    "86fa86eb", "8e21c6e5", "aedd338d", "b05cf470", "dae9f7e9", "f2b221fd",
}


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_lme_v2_p1_t6", ROOT / "scripts" / "run_lme_v2_p1_t6.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_synthetic_root(campaign, output: Path, manifest: dict) -> None:
    campaign._fingerprint = lambda path: {
        "path": str(path.resolve()), "bytes": 1, "sha256": "f" * 64
    }
    binaries = {
        name: campaign._fingerprint(campaign._binary_path(name))
        for name in ("server", "worker", "cli")
    }
    cases = {}
    for case in manifest["selection"]["cases"]:
        case_id = case["id"]
        memory_contracts = {
            mode: {
                "config_sha256": campaign.canonical_sha256({
                    "case_id": case_id, "mode": mode,
                }),
                "memory_params_sha256": campaign.canonical_sha256({
                    "case_id": case_id, "mode": mode,
                }),
                "top_k": 5,
                "budget_tokens": 4096,
                "mode": mode,
                "recall_request_timeout_seconds": 600,
            }
            for mode in ("fast", "deep")
        }
        cases[case_id] = {
            "synthetic": case_id,
            "fast_config_sha256": memory_contracts["fast"]["config_sha256"],
            "deep_config_sha256": memory_contracts["deep"]["config_sha256"],
            "memory_contracts": memory_contracts,
        }
    campaign.atomic_write_json(output / "pre-execution-proof.json", {
        "manifest_sha256": campaign.sha256_file(campaign.CAMPAIGN_MANIFEST),
        "endpoint_hashes": {}, "run_order_sha256": campaign.canonical_sha256(
            campaign.expanded_run_order(manifest)
        ),
        "outputs_observed_before_freeze": False,
        "git_commit": campaign.subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=campaign.ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip(),
        "binaries": binaries,
        "binary_profile": campaign.PRODUCTION_BINARY_PROFILE,
        "archive_tools": {
            "server_major": 17,
            "pg_dump": {"binary": "/pg_dump", "major": 17, "server_major": 17},
            "pg_restore": {"binary": "/pg_restore", "major": 17, "server_major": 17},
        },
        "deep_prompt_sha256": manifest["protocol"]["deep_prompt_sha256"],
        "deep_config_hashes": {
            name: candidate["config_sha256"]
            for name, candidate in manifest["protocol"]["deep_candidates"].items()
        },
        "selected_deep_arm": manifest["protocol"]["selected_deep_arm"],
        "memory_adapter_sha256": campaign.sha256_file(
            campaign.ROOT / "benchmarks/longmemeval_v2/memphant_memory.py"
        ),
        "python_environment": {"synthetic": True},
        "environment_contract_sha256": campaign.canonical_sha256(
            campaign._clean_environment()
        ),
        "materialization": {"proof_sha256": "a" * 64, "cases": cases},
    })


def _write_synthetic_case_banks(campaign, output: Path, rows: list[dict]) -> None:
    root = json.loads((output / "pre-execution-proof.json").read_text())
    for case_id in sorted({row["question_id"] for row in rows}):
        bank = output / "case-banks" / case_id
        bank.mkdir(parents=True)
        context = {
            "subject_id": f"subject-{case_id}",
            "scope_id": f"scope-{case_id}",
            "actor_id": f"actor-{case_id}",
            "agent_node_id": f"node-{case_id}",
            "subject_generation": 0,
        }
        construction = {
            "schema_version": 1,
            "contract": {
                "adapter_sha256": root["memory_adapter_sha256"],
                "construction_params_sha256": "b" * 64,
                "binaries": root["binaries"],
            },
            "isolation": {
                "tenant_id": f"tenant-{case_id}",
                "instance_id": f"construction-{case_id}",
                "context": context,
            },
            "pairing": {
                "trajectory_count": 500,
                "resource_count": 670,
                "worker": {
                    "completed_sources": 670,
                    "stdout_sha256": "1" * 64,
                    "stderr_sha256": "2" * 64,
                },
                "retains": [{"trajectory_id": case_id}],
            },
        }
        construction["construction_proof_sha256"] = campaign.canonical_sha256(
            construction
        )
        materialization = root["materialization"]["cases"][case_id]
        memory_contracts = materialization["memory_contracts"]
        case_contract = {
            "question_id": case_id,
            "materialization": materialization,
            "materialization_sha256": campaign.canonical_sha256(materialization),
            "memory_contracts": memory_contracts,
            "adapter_sha256": root["memory_adapter_sha256"],
            "binaries": root["binaries"],
            "manifest_sha256": campaign.sha256_file(campaign.CAMPAIGN_MANIFEST),
            "selected_deep_arm": "sonnet",
        }
        manifest = {
            "archive_sha256": "a" * 64,
            "logical_identity": {"sha256": "e" * 64},
            "case_contract": case_contract,
            "case_contract_sha256": campaign.canonical_sha256(case_contract),
            "construction": construction,
            "construction_proof_sha256": construction[
                "construction_proof_sha256"
            ],
            "construction_duration_ms": 10_000,
        }
        campaign.atomic_write_json(bank / "manifest.json", manifest)
        attempt = output / "case-construction" / case_id / "attempt-0001"
        campaign.atomic_write_json(attempt / "attempt.json", {
            "schema_version": 1,
            "attempt_id": "attempt-0001",
            "case_id": case_id,
            "classification": "free_local_construction",
            "complete": False,
        })
        campaign.atomic_write_json(attempt / "complete.json", {
            "schema_version": 1,
            "attempt_id": "attempt-0001",
            "case_id": case_id,
            "construction_proof_sha256": construction[
                "construction_proof_sha256"
            ],
            "construction_duration_ms": manifest["construction_duration_ms"],
            "complete": True,
        })
        seal = campaign._case_bank_seal(bank / "manifest.json")
        row_hashes = {}
        for row in [item for item in rows if item["question_id"] == case_id]:
            row_dir = output / row["row_id"]
            campaign.atomic_write_json(row_dir / "case-bank-seal.json", seal)
            proof_path = row_dir / "row-proof.json"
            proof = json.loads(proof_path.read_text())
            proof["case_bank_seal_sha256"] = seal["seal_sha256"]
            proof["scratch_database_identity"] = (
                f"memphant_p1t6_{case_id}_12345678_{row['arm']}"
            )
            proof["artifact_hashes"] = campaign.artifact_hashes(
                row_dir, exclude={"row-proof.json"}
            )
            campaign.atomic_write_json(proof_path, proof)
            row_hashes[row["arm"]] = campaign.sha256_file(proof_path)
        campaign.atomic_write_json(bank / "archive-retirement.json", {
            "archive_sha256": manifest["archive_sha256"],
            "case_bank_seal_sha256": seal["seal_sha256"],
            "manifest_sha256": seal["manifest_sha256"],
            "reason": "both_immutable_arm_rows_complete",
            "row_proof_sha256": row_hashes,
        })


def _refresh_synthetic_case_bank_retirements(
    campaign, output: Path, rows: list[dict]
) -> None:
    for case_id in sorted({row["question_id"] for row in rows}):
        bank = output / "case-banks" / case_id
        manifest_path = bank / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        seal = campaign._case_bank_seal(manifest_path)
        campaign.atomic_write_json(bank / "archive-retirement.json", {
            "archive_sha256": manifest["archive_sha256"],
            "case_bank_seal_sha256": seal["seal_sha256"],
            "manifest_sha256": seal["manifest_sha256"],
            "reason": "both_immutable_arm_rows_complete",
            "row_proof_sha256": {
                row["arm"]: campaign.sha256_file(
                    output / row["row_id"] / "row-proof.json"
                )
                for row in rows if row["question_id"] == case_id
            },
        })


def _load_memory_adapter(monkeypatch):
    package = types.ModuleType("memory_modules")
    memory = types.ModuleType("memory_modules.memory")

    class Memory:
        def __init__(self, params):
            self.params = params

    memory.Memory = Memory
    memory.MemoryContextItem = dict
    memory.register_memory = lambda cls: cls
    monkeypatch.setitem(sys.modules, "memory_modules", package)
    monkeypatch.setitem(sys.modules, "memory_modules.memory", memory)
    spec = importlib.util.spec_from_file_location(
        "p1_t6_memory_adapter", ROOT / "benchmarks/longmemeval_v2/memphant_memory.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AnswerTrap(dict):
    def __getitem__(self, key):
        if key not in {"id", "domain", "question_type"}:
            raise AssertionError(f"selector read forbidden field: {key}")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key not in {"id", "domain", "question_type"}:
            raise AssertionError(f"selector read forbidden field: {key}")
        return super().get(key, default)


def test_selector_is_answer_blind_deterministic_and_exact() -> None:
    campaign = _load()
    source = json.loads(
        (ROOT / "benchmarks/manifests/longmemeval_v2.p1_t6.selection-source.json").read_text()
    )
    rows = [AnswerTrap(row) for row in source["rows"]]
    selected = campaign.select_cases(rows)
    assert {row["id"] for row in selected} == EXPECTED_IDS
    assert campaign.canonical_sha256(selected) == campaign.SELECTION_SHA256
    assert campaign.SELECTION_SHA256 == (
        "d7762dbaffff7acfe779162d4993c8c09ef0440e3c1a25e0d3408127d73e25fa"
    )
    assert [row["domain"] for row in selected].count("web") == 6
    assert [row["domain"] for row in selected].count("enterprise") == 6
    counts = {ability: 0 for ability in campaign.ABILITIES}
    for row in selected:
        counts[row["ability"]] += 1
    assert max(counts.values()) - min(counts.values()) <= 1


def test_selector_rejects_invalid_rows_and_hash_amendment_is_explicit() -> None:
    campaign = _load()
    with pytest.raises(RuntimeError, match="duplicate question id"):
        campaign.select_cases(
            [
                {"id": "same", "domain": "web", "question_type": "procedure"},
                {"id": "same", "domain": "web", "question_type": "procedure"},
            ]
        )
    manifest = campaign.load_campaign_manifest()
    assert manifest["selection"]["sha256"] == campaign.SELECTION_SHA256
    assert manifest["selection"]["supersedes_underdefined_sha256"].startswith("ffe151")
    assert manifest["selection"]["outputs_observed_before_amendment"] is False


def test_campaign_is_single_candidate_paired_gate() -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    assert campaign.verify_campaign_manifest(manifest) == {
        "cases": 12, "rows": 24, "arms": 2, "constructions": 12,
    }
    assert manifest["run_order"]["arm_order_per_case"] == ["fast", "sonnet"]
    assert manifest["protocol"]["selected_deep_arm"] == "sonnet"
    rows = campaign.expanded_run_order(manifest)
    assert [row["sequence"] for row in rows] == list(range(1, 25))
    assert {row["question_id"] for row in rows} == EXPECTED_IDS
    for question_id in sorted(EXPECTED_IDS):
        question_rows = [row for row in rows if row["question_id"] == question_id]
        assert [row["arm"] for row in question_rows] == ["fast", "sonnet"]
        assert len({row["row_id"] for row in question_rows}) == 2


def test_minimal_acquisition_excludes_trajectory_screenshot_archives() -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    paths = set(manifest["acquisition"]["files"])
    assert paths == {
        "checksums.sha256",
        "questions.jsonl",
        "trajectories.jsonl",
        "haystacks/lme_v2_medium.json",
        "question_screenshots/8e21c6e5.png",
        "question_screenshots/f2b221fd.png",
    }
    assert not any("trajectory_screenshots" in path for path in paths)


def test_completed_rows_are_never_overwritten(tmp_path: Path) -> None:
    campaign = _load()
    row_dir = tmp_path / "0001-fast-19367bc7"
    row_dir.mkdir()
    (row_dir / "row-proof.json").write_text("{}\n")
    with pytest.raises(RuntimeError, match="immutable row already exists"):
        campaign.require_new_row_dir(row_dir)


def test_case_bank_contract_is_local_key_free_and_content_addressed(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    database_url = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"
    construction = {
        "schema_version": 1,
        "contract": {"adapter_sha256": "a" * 64, "binaries": {}},
        "isolation": {"tenant_id": "tenant"},
        "pairing": {
            "resource_count": 2,
            "worker": {"completed_sources": 2},
            "retains": [{"trajectory_id": "trajectory"}],
        },
    }
    construction["construction_proof_sha256"] = campaign.canonical_sha256(
        construction
    )
    case_contract = {"question_id": "19367bc7", "materialization_sha256": "m" * 64}
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setattr(
        campaign,
        "_postgres_tool_identity",
        lambda *_args: {"binary": "/usr/bin/pg_dump", "version": "PostgreSQL 18", "major": 18, "server_major": 18},
    )
    monkeypatch.setattr(
        campaign,
        "_database_schema_identity",
        lambda _url: {"schema_sha256": "s" * 64, "extensions_and_migrations_sha256": "e" * 64, "sha256": "d" * 64},
    )
    monkeypatch.setattr(
        campaign,
        "_database_bank_identity",
        lambda _url: {"tables": {"resource": {"rows": 2, "sha256": "r" * 64}}, "sha256": "l" * 64},
    )
    monkeypatch.setattr(campaign, "_database_key_count", lambda _url: 0)
    monkeypatch.setattr(campaign, "_job_state_counts", lambda _url: (0, 0, 0))

    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        destination = next(item.split("=", 1)[1] for item in command if item.startswith("--file="))
        Path(destination).write_bytes(b"frozen-bank")
        return campaign.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(campaign.subprocess, "run", run)
    manifest = campaign._dump_case_bank(
        database_url, tmp_path / "bank", construction, case_contract,
        construction_duration_ms=12_345,
    )
    archive = tmp_path / "bank" / manifest["archive"]
    assert archive.name == f"{campaign.sha256_file(archive)}.dump"
    assert manifest["archive_sha256"] == campaign.sha256_file(archive)
    assert manifest["construction_duration_ms"] == 12_345
    assert manifest["excluded_tables"] == list(campaign.BANK_EXCLUDED_TABLES)
    serialized = json.dumps(manifest)
    assert "secret" not in serialized and database_url not in serialized
    assert {
        item.removeprefix("--exclude-table-data=")
        for item in commands[0]
        if item.startswith("--exclude-table-data=")
    } == set(campaign.BANK_EXCLUDED_TABLES)


def test_clone_requires_quiescent_source_and_preserves_identity(
    monkeypatch,
) -> None:
    campaign = _load()
    source = "postgres://bench:secret@localhost:5432/memphant_scratch_1_2"
    expected = {"tables": {}, "sha256": "f" * 64}
    identities = []
    calls = []
    database_events = []
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")

    def identity(url):
        name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        identities.append(name)
        database_events.append("identity:" + name)
        return expected

    monkeypatch.setattr(campaign, "_database_bank_identity", identity)
    monkeypatch.setattr(
        campaign, "_database_key_count",
        lambda url: database_events.append(
            "key:" + urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        ) or 0,
    )
    monkeypatch.setattr(
        campaign, "_wait_for_source_quiescence",
        lambda _url: database_events.append("quiescence") or {
            "sample_count": 2, "consecutive_zero_samples": 2,
        },
    )
    monkeypatch.setattr(
        campaign.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command)
            or database_events.append(command[0])
            or campaign.subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    clone, quiescence = campaign._clone_case_source(
        source, "memphant_p1t6_19367bc7_deadbeef_fast", expected,
        include_quiescence_proof=True,
    )
    assert clone.endswith("/memphant_p1t6_19367bc7_deadbeef_fast")
    assert quiescence == {"sample_count": 2, "consecutive_zero_samples": 2}
    assert identities == ["memphant_scratch_1_2", "memphant_p1t6_19367bc7_deadbeef_fast"]
    assert database_events == [
        "key:memphant_scratch_1_2",
        "identity:memphant_scratch_1_2",
        "quiescence",
        "createdb",
        "identity:memphant_p1t6_19367bc7_deadbeef_fast",
        "key:memphant_p1t6_19367bc7_deadbeef_fast",
    ]
    assert calls[0][0] == "createdb" and "--template=memphant_scratch_1_2" in calls[0]
    with pytest.raises(RuntimeError, match="persistent source session"):
        monkeypatch.setattr(
            campaign, "_wait_for_source_quiescence",
            lambda _url: (_ for _ in ()).throw(
                RuntimeError("persistent source session")
            ),
        )
        campaign._clone_case_source(
            source, "memphant_p1t6_19367bc7_deadbeef_sonnet", expected
        )


def test_source_quiescence_wait_accepts_transient_session_after_two_zero_samples(
    monkeypatch,
) -> None:
    campaign = _load()
    samples = [
        [{"backend_type": "autovacuum worker", "state": "active", "application_name": ""}],
        [],
        [],
    ]
    monkeypatch.setattr(
        campaign, "_source_connection_diagnostics", lambda _url: samples.pop(0)
    )
    monkeypatch.setattr(campaign.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        campaign, "_source_maintenance_progress",
        lambda _url: {"vacuum": [{"phase": "scanning heap"}], "analyze": []},
    )

    proof = campaign._wait_for_source_quiescence(
        "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2",
        timeout_seconds=1,
        sample_interval_seconds=0,
    )

    assert proof["sample_count"] == 3
    assert proof["consecutive_zero_samples"] == 2
    assert proof["observed_connections"] == [{
        "backend_type": "autovacuum worker", "state": "active", "application_name": "",
    }]
    assert proof["observed_progress"][0]["vacuum"][0]["phase"] == "scanning heap"
    assert proof["unexpected_sessions"] == []
    assert proof["terminated_sessions"] == 0


def test_source_quiescence_wait_fails_closed_on_persistent_session(
    monkeypatch,
) -> None:
    campaign = _load()
    diagnostic = [{
        "backend_type": "client backend", "state": "idle",
        "application_name": "persistent-client",
    }]
    monkeypatch.setattr(
        campaign, "_source_connection_diagnostics", lambda _url: diagnostic
    )

    monkeypatch.setattr(
        campaign, "_source_maintenance_progress",
        lambda _url: pytest.fail("unexpected client reached maintenance wait"),
    )
    with pytest.raises(RuntimeError, match="unexpected source session.*persistent-client"):
        campaign._wait_for_source_quiescence(
            "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2",
            timeout_seconds=0,
            sample_interval_seconds=0,
        )


def test_source_quiescence_wait_bounds_expected_autovacuum_with_progress(
    monkeypatch,
) -> None:
    campaign = _load()
    monkeypatch.setattr(campaign, "_source_connection_diagnostics", lambda _url: [{
        "backend_type": "autovacuum worker", "state": "active", "application_name": "",
    }])
    monkeypatch.setattr(campaign, "_source_maintenance_progress", lambda _url: {
        "vacuum": [{"phase": "vacuuming indexes", "heap_blks_scanned": 42}],
        "analyze": [{"phase": "acquiring sample rows", "sample_blks_scanned": 7}],
    })

    with pytest.raises(RuntimeError, match="expected autovacuum worker.*vacuuming indexes"):
        campaign._wait_for_source_quiescence(
            "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2",
            timeout_seconds=0,
            sample_interval_seconds=0,
        )


def test_source_quiescence_samples_use_separate_admin_transactions(monkeypatch) -> None:
    campaign = _load()
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    calls = []
    monkeypatch.setattr(
        campaign, "_psql_json",
        lambda url, sql: calls.append((url, sql)) or [],
    )
    source = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"

    assert campaign._source_connection_diagnostics(source) == []
    assert campaign._source_connection_diagnostics(source) == []
    assert len(calls) == 2
    assert all(url.endswith("/postgres") for url, _sql in calls)


def test_source_maintenance_progress_uses_pg17_columns_and_separate_transactions(
    monkeypatch,
) -> None:
    campaign = _load()
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    calls = []
    monkeypatch.setattr(
        campaign, "_psql_json",
        lambda url, sql: calls.append((url, sql)) or [],
    )
    source = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"

    assert campaign._source_maintenance_progress(source) == {
        "vacuum": [], "analyze": [],
    }
    assert len(calls) == 2
    assert "pg_stat_progress_vacuum" in calls[0][1]
    assert "pg_stat_progress_analyze" in calls[1][1]
    assert all(url.endswith("/postgres") for url, _sql in calls)
    vacuum_select = calls[0][1].removeprefix("select ").split(" from ", 1)[0]
    vacuum_select = vacuum_select.replace("coalesce(phase, '') as phase", "phase")
    assert tuple(vacuum_select.split(", ")) == (
        "phase",
        "heap_blks_total",
        "heap_blks_scanned",
        "heap_blks_vacuumed",
        "index_vacuum_count",
        "max_dead_tuple_bytes",
        "dead_tuple_bytes",
        "num_dead_item_ids",
        "indexes_total",
        "indexes_processed",
    )
    analyze_select = calls[1][1].removeprefix("select ").split(" from ", 1)[0]
    analyze_select = analyze_select.replace("coalesce(phase, '') as phase", "phase")
    assert tuple(analyze_select.split(", ")) == (
        "phase",
        "sample_blks_total",
        "sample_blks_scanned",
        "ext_stats_total",
        "ext_stats_computed",
        "child_tables_total",
        "child_tables_done",
    )


def test_no_model_clone_hashes_retained_proof_after_phase_redaction(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign = _load()
    before_digest = []

    class Memory:
        def __init__(self, _params):
            pass

        def insert(self, _trajectory):
            pass

        def set_query_context(self, **_kwargs):
            pass

        def query(self, _question):
            return "context"

        def post_query_hook(self, **_kwargs):
            proof = tmp_path / "arms/fast/adapter.json"
            campaign.atomic_write_json(proof, {
                "contract": {"binaries": {
                    "cli": {"path": "/release/memphant-cli"},
                    "server": {"path": "/release/memphant-server"},
                    "worker": {"path": "/release/memphant-worker"},
                }},
                "pairing": {"query_only": True},
                "query": {"query_only": True},
            })
            before_digest.append(campaign.sha256_file(proof))
            return {
                "construction_proof_sha256": "a" * 64,
                "context_sha256": "b" * 64,
                "recall_duration_ms": 1,
            }

    def phase(_url, proof_dir, _run_id, action):
        proof_dir.mkdir(parents=True)
        result = action(types.SimpleNamespace(MemphantMemory=Memory))
        proof = next(proof_dir.glob("*.json"))
        proof.write_bytes(proof.read_bytes().replace(b"memphant", b"[REDACTED]"))
        return result

    monkeypatch.setattr(campaign, "_run_no_model_adapter_phase", phase)
    result = campaign._run_no_model_clone_query(
        "postgres://bench:secret@localhost:5432/memphant_p1t6_19367bc7_deadbeef_fast",
        tmp_path,
        "fast",
        {
            "case_id": "19367bc7",
            "memory_params": {},
            "trajectories": [{}],
            "question": "question",
        },
        tmp_path / "construction-proof.json",
    )
    retained = next((tmp_path / "arms/fast").glob("*.json"))
    assert result["adapter_proof_sha256"] == campaign.sha256_file(retained)
    assert result["adapter_proof_sha256"] != before_digest[0]
    assert retained.read_text().count("[REDACTED]") == 3


def _no_model_memory_proof(
    campaign, *, arm: str, construction: dict, construction_sha256: str,
    binaries: dict | None = None,
) -> dict:
    instance_id = {"fast": "1" * 32, "sonnet": "2" * 32}[arm]
    trace_id = {"fast": "trace-fast", "sonnet": "trace-sonnet"}[arm]
    isolation = {
        "tenant_id": "tenant-shared",
        "scope_id": "scope-shared",
        "actor_id": "actor-shared",
        "instance_id": instance_id,
    }
    return {
        "contract": {
            "mode": "fast",
            "gold_fields_consumed": [],
            "binaries": binaries or {
                name: {"path": f"/release/[REDACTED]-{name}"}
                for name in ("cli", "server", "worker")
            },
        },
        "isolation": isolation,
        "pairing": {
            "trajectory_count": 500,
            "resource_count": 670,
            "worker": construction["pairing"]["worker"],
            "construction_proof_sha256": construction_sha256,
            "query_only": True,
        },
        "public": {
            "trace": {
                "id": trace_id,
                "tenant_id": isolation["tenant_id"],
                "scope_id": isolation["scope_id"],
                "actor_id": isolation["actor_id"],
                "mode_requested": "fast",
                "mode_executed": "fast",
                "cost_micros": 0,
                "l4_sandbox_id": None,
                "l4_gathered_evidence_ids": [],
                "degradation": None,
                "escalation_reason": "none",
            },
            "recall_response": {"trace_id": trace_id, "degraded": False},
        },
        "query": {
            "question_id": f"no-model-19367bc7-{arm}",
            "query_sha256": "5" * 64,
            "recall_request_sha256": "6" * 64,
            "recall_response_sha256": {"fast": "7" * 64, "sonnet": "8" * 64}[arm],
            "context_sha256": "9" * 64,
            "recall_duration_ms": 1,
            "construction_proof_sha256": construction_sha256,
            "query_only": True,
        },
        "recall_mutation_proof": {
            "before": {
                "resource": {"rows": 670, "content_md5": "a"},
                "retrieval_trace": {"rows": 0, "content_md5": "b"},
            },
            "after": {
                "resource": {"rows": 670, "content_md5": "a"},
                "retrieval_trace": {"rows": 1, "content_md5": "c"},
            },
            "changed_tables": ["retrieval_trace"],
            "allowed_audit_rows_added": 1,
            "corpus_policy_job_tables_unchanged": True,
        },
    }


def _write_no_model_hash_repair_fixture(campaign, tmp_path: Path, monkeypatch):
    root = tmp_path / "p1-t6"
    output = root / "exact-hash-repair-target"
    output.mkdir(parents=True)
    worker = {
        "completed_sources": 670,
        "stdout_sha256": "1" * 64,
        "stderr_sha256": "2" * 64,
    }
    retains = [
        {"trajectory_id": f"trajectory-{index:03d}"} for index in range(500)
    ]
    construction_core = {
        "schema_version": 1,
        "pairing": {
            "trajectory_count": 500,
            "resource_count": 670,
            "worker": worker,
            "retains": retains,
        },
        "isolation": {
            "tenant_id": "tenant-shared",
            "instance_id": "construction-instance",
            "context": {
                "scope_id": "scope-shared", "actor_id": "actor-shared",
            },
        },
    }
    construction = {
        **construction_core,
        "construction_proof_sha256": campaign.canonical_sha256(construction_core),
    }
    contract = {
        "fixture": "exact", "case_id": "19367bc7", "input_sha256": "3" * 64,
    }
    logical_core = {"tables": {}, "sequences": {}}
    logical = {**logical_core, "sha256": campaign.canonical_sha256(logical_core)}
    archive_body = b"PGDMP\x01\x10synthetic-custom-format-bank"
    archive_sha = hashlib.sha256(archive_body).hexdigest()
    bank = output / "case-bank"
    bank.mkdir()
    (bank / f"{archive_sha}.dump").write_bytes(archive_body)
    pg_dump = {
        "binary": "/usr/local/bin/pg_dump", "major": 17, "server_major": 17,
    }
    schema = {"schema": "memphant", "sha256": "4" * 64}
    manifest = {
        "format_version": campaign.BANK_FORMAT_VERSION,
        "construction": construction,
        "construction_proof_sha256": construction["construction_proof_sha256"],
        "construction_duration_ms": 1,
        "case_contract": contract,
        "case_contract_sha256": campaign.canonical_sha256(contract),
        "logical_identity": logical,
        "database_schema_identity": schema,
        "postgres": pg_dump,
        "postgres_major": 17,
        "archive": f"{archive_sha}.dump",
        "archive_sha256": archive_sha,
        "excluded_tables": list(campaign.BANK_EXCLUDED_TABLES),
    }
    campaign.atomic_write_json(bank / "manifest.json", manifest)
    campaign.atomic_write_json(output / "construction-proof.json", construction)
    pre_recovery_inventory = campaign.artifact_hashes(output)

    def committed_controller_sha256(commit: str) -> str:
        completed = subprocess.run(
            ["git", "show", f"{commit}:scripts/run_lme_v2_p1_t6.py"],
            cwd=ROOT, capture_output=True, check=True,
        )
        return hashlib.sha256(completed.stdout).hexdigest()

    incident_trace = "synthetic captured pre-clone quiescence failure"
    incident = {
        "classification": "p1_t6_exact_pre_clone_quiescence_failure",
        "case_id": "19367bc7",
        "external_dispatches": 0,
        "measured_commit": "29c9eb53556139bdb1d651f3c79716586ab04cfd",
        "controller_sha256": committed_controller_sha256(
            "29c9eb53556139bdb1d651f3c79716586ab04cfd"
        ),
        "campaign_manifest_sha256": campaign.sha256_file(campaign.CAMPAIGN_MANIFEST),
        "failure_evidence": {
            "durably_captured_exception": True,
            "durably_captured_restore_return": True,
            "sanitized_trace_excerpt": incident_trace,
            "sanitized_trace_excerpt_sha256": hashlib.sha256(
                incident_trace.encode()
            ).hexdigest(),
        },
        "bank": {
            "archive_sha256": archive_sha,
            "manifest_sha256": campaign.sha256_file(bank / "manifest.json"),
            "construction_proof_file_sha256": campaign.sha256_file(
                output / "construction-proof.json"
            ),
            "construction_proof_sha256": construction["construction_proof_sha256"],
            "logical_identity_sha256": logical["sha256"],
            "case_contract_sha256": manifest["case_contract_sha256"],
        },
        "pre_recovery_inventory": pre_recovery_inventory,
        "initial_attempt": {
            "constructions": 1, "dumps": 1, "resets": 1, "restores": 1,
            "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
            "outcome": "pre_clone_quiescence_failure",
        },
        "diagnostic_archive_only_pg17_replay": {
            "constructions": 0, "dumps": 0, "resets": 0, "restores": 1,
            "clones": 0, "query_only_recalls": 0, "persistent_source_sessions": 0,
            "external_dispatches": 0,
            "outcome": "archive_restored_and_source_sessions_queried",
            "authorization_evidence": False,
        },
        "recovery": {
            "same_sealed_bank_required": True,
            "reconstruction_allowed": False,
            "redump_allowed": False,
            "executed": False,
        },
    }
    campaign.atomic_write_json(output / "RECOVERY-INCIDENT.json", incident)
    incident_sha = campaign.sha256_file(output / "RECOVERY-INCIDENT.json")
    failure_trace = "synthetic captured expected autovacuum timeout"
    first_failure = {
        "classification": "p1_t6_exact_first_recovery_failure",
        "producer_commit": "3a85d5189e2c7279692478d0eee7d6b563ea78c5",
        "producer_controller_sha256": committed_controller_sha256(
            "3a85d5189e2c7279692478d0eee7d6b563ea78c5"
        ),
        "case_id": "19367bc7",
        "failure_evidence": {
            "durably_captured_exception": True,
            "durably_captured_restore_return": True,
            "sanitized_trace_excerpt": failure_trace,
            "sanitized_trace_excerpt_sha256": hashlib.sha256(
                failure_trace.encode()
            ).hexdigest(),
        },
        "source_sessions": [{
            "backend_type": "autovacuum worker", "state": "active",
            "application_name": "",
        }],
        "attempt": {
            "constructions": 0, "dumps": 0, "resets": 0, "restores": 1,
            "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
            "outcome": "expected_autovacuum_timeout_pre_clone",
        },
        "bank": {
            "archive_sha256_before": archive_sha,
            "archive_sha256_after": archive_sha,
            "manifest_sha256_before": campaign.sha256_file(bank / "manifest.json"),
            "manifest_sha256_after": campaign.sha256_file(bank / "manifest.json"),
            "construction_proof_file_sha256_before": campaign.sha256_file(
                output / "construction-proof.json"
            ),
            "construction_proof_file_sha256_after": campaign.sha256_file(
                output / "construction-proof.json"
            ),
        },
        "prior_incident_sha256": incident_sha,
        "scratch_cleaned": True,
        "arm_artifacts": 0,
        "proof_written": False,
    }
    campaign.atomic_write_json(
        output / "FIRST-RECOVERY-FAILURE.json", first_failure
    )
    retained = {}
    retained_paths = {}
    execution_binaries = {
        name: {"path": f"/release/memphant-{name}"}
        for name in ("cli", "server", "worker")
    }
    old_hashes = {}
    for arm in ("fast", "sonnet"):
        relative = f"arms/{arm}/adapter.json"
        path = output / relative
        retained_binaries = {
                "cli": {"path": "/release/[REDACTED]-cli"},
                "server": {"path": "/release/[REDACTED]-server"},
                "worker": {"path": "/release/[REDACTED]-worker"},
        }
        campaign.atomic_write_json(
            path,
            _no_model_memory_proof(
                campaign, arm=arm, construction=construction,
                construction_sha256=construction["construction_proof_sha256"],
                binaries=retained_binaries,
            ),
        )
        retained[arm] = campaign.sha256_file(path)
        retained_paths[arm] = {"path": relative, "sha256": retained[arm]}
        old_hashes[arm] = hashlib.sha256(
            campaign._reconstruct_pre_redaction_adapter_proof(
                json.loads(path.read_text()), execution_binaries
            )
        ).hexdigest()
    accounting = campaign._no_model_attempt_accounting(
        True, incident, first_failure
    )
    accounting["recovery"]["incident_sha256"] = incident_sha
    accounting["recovery"]["first_recovery_failure_sha256"] = (
        campaign.sha256_file(output / "FIRST-RECOVERY-FAILURE.json")
    )
    archive_tools = {
        "server_major": 17,
        "pg_dump": pg_dump,
        "pg_restore": {
            "binary": "/usr/local/bin/pg_restore", "major": 17,
            "server_major": 17,
        },
    }
    original_inventory = campaign.artifact_hashes(output)
    proof = {
        "schema_version": 1,
        "classification": "original-classification",
        "git_commit": "a" * 40,
        "controller": {"path": "/controller", "bytes": 123, "sha256": "b" * 64},
        "binaries": execution_binaries,
        "fixture": {
            "name": "exact", "case_id": "19367bc7", "input_sha256": "3" * 64,
        },
        "archive_tools": archive_tools,
        "database_schema_identity": schema,
        **accounting,
        "construction_proof_sha256": construction["construction_proof_sha256"],
        "logical_identity_sha256": logical["sha256"],
        "cleanup": {
            "source_api_key_count": 0,
            "source_job_state": {"pending": 0, "dead": 0, "total": 0},
            "orphan_clone_count": 0,
            "force_drop_verified": True,
        },
        "external_dispatch": {
            "configured": False, "dispatches": 0, "deep_enabled": False,
        },
        "archive": {
            "sha256": archive_sha,
            "manifest_sha256": campaign.sha256_file(bank / "manifest.json"),
            "seal": campaign._case_bank_seal(bank / "manifest.json"),
        },
        "arms": [
            {
                "arm": arm,
                "adapter_proof_sha256": old_hashes[arm],
                "query_only": True,
                "verification_recall_mode": "fast",
                "construction_work": {"retains": 0, "worker_drains": 0},
                "construction_proof_sha256": construction[
                    "construction_proof_sha256"
                ],
                "context_sha256": "9" * 64,
                "timing_ms": {"recall": 1},
                "pre_query_logical_identity_sha256": logical["sha256"],
                "post_query_logical_identity_sha256": logical["sha256"],
                "api_key_count": 1,
                "job_state": {"pending": 0, "dead": 0, "total": 0},
                "clone_database": f"memphant_p1t6_19367bc7_deadbeef_{arm}",
                "source_quiescence": {
                    "policy": "only_exact_autovacuum_worker_may_wait",
                    "timeout_seconds": 180.0,
                    "sample_interval_seconds": 1.0,
                    "sample_count": 2,
                    "consecutive_zero_samples": 2,
                    "unexpected_sessions": [],
                    "terminated_sessions": 0,
                    "observed_connections": [],
                    "observed_progress": [],
                },
            }
            for arm in ("fast", "sonnet")
        ],
        "artifact_hashes": original_inventory,
        "unchanged_evidence": {"value": 7},
    }
    proof["proof_sha256"] = campaign.canonical_sha256(proof)
    campaign.atomic_write_json(output / "PROOF.json", proof)
    original_bytes = (output / "PROOF.json").read_bytes()
    target = {
        "basename": output.name,
        "case_id": "19367bc7",
        "proof_file_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "proof_sha256": proof["proof_sha256"],
        "classification": proof["classification"],
        "execution_git_commit": proof["git_commit"],
        "execution_controller": {
            "bytes": proof["controller"]["bytes"],
            "sha256": proof["controller"]["sha256"],
        },
        "old_explicit_hashes": old_hashes,
        "retained_artifacts": retained_paths,
        "original_artifact_count": len(original_inventory),
    }
    monkeypatch.setattr(campaign, "NO_MODEL_HASH_REPAIR_ROOT", root)
    monkeypatch.setattr(campaign, "NO_MODEL_HASH_REPAIR_TARGET", target)
    monkeypatch.setattr(
        campaign, "_committed_controller_fingerprint",
        lambda commit: (
            {"bytes": 456, "sha256": "d" * 64}
            if commit == "c" * 40
            else (_ for _ in ()).throw(RuntimeError("forged executor commit"))
        ),
    )
    monkeypatch.setattr(
        campaign, "_repair_executor_provenance",
        lambda: {
            "git_commit": "c" * 40,
            "controller": {
                "path": "/repair/run_lme_v2_p1_t6.py",
                "bytes": 456,
                "sha256": "d" * 64,
            },
        },
    )
    return output, proof, original_bytes, original_inventory, retained


def test_no_model_hash_repair_is_lineage_preserving_and_idempotent(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign = _load()
    output, original, original_bytes, original_inventory, retained = (
        _write_no_model_hash_repair_fixture(campaign, tmp_path, monkeypatch)
    )
    audit = campaign.repair_no_model_proof_hashes(output)
    predecessor = output / "PROOF.pre-hash-repair.json"
    record_path = output / "PROOF-HASH-REPAIR.json"
    repaired = json.loads((output / "PROOF.json").read_text())
    record = json.loads(record_path.read_text())

    assert predecessor.read_bytes() == original_bytes
    assert record["operations"] == {
        "database_connections": 0,
        "restores": 0,
        "clones": 0,
        "model_calls": 0,
        "external_dispatches": 0,
    }
    assert record["root_cause_evidence"]["secret_values_recorded"] is False
    assert record["root_cause_evidence"]["semantic_revalidation"][
        "bank_excluded_tables"
    ] == list(campaign.BANK_EXCLUDED_TABLES)
    assert all(
        len(paths) == 3
        for paths in record["root_cause_evidence"]["redacted_binary_path_fields"].values()
    )
    assert repaired["classification"] == campaign.NO_MODEL_HASH_REPAIRED_CLASSIFICATION
    assert {
        arm["arm"]: arm["adapter_proof_sha256"] for arm in repaired["arms"]
    } == retained
    assert repaired["artifact_hashes"] == campaign.artifact_hashes(
        output, exclude={"PROOF.json"}
    )
    assert repaired["proof_sha256"] == campaign.canonical_sha256({
        key: value for key, value in repaired.items() if key != "proof_sha256"
    })
    assert all(repaired["artifact_hashes"][path] == digest
               for path, digest in original_inventory.items())
    unchanged = set(original) - {
        "classification", "arms", "artifact_hashes", "proof_sha256",
    }
    assert all(repaired[key] == original[key] for key in unchanged)
    assert audit["database_connections"] == audit["model_calls"] == audit["paid_calls"] == 0
    assert audit["reused"] is False

    completed_hashes = campaign.artifact_hashes(output)
    reused = campaign.repair_no_model_proof_hashes(output)
    assert reused["reused"] is True and reused["paid_calls"] == 0
    assert campaign.artifact_hashes(output) == completed_hashes


@pytest.mark.parametrize("stop_after", ["predecessor", "record"])
def test_no_model_hash_repair_resumes_append_only_crash_states(
    tmp_path: Path, monkeypatch, stop_after: str,
) -> None:
    campaign = _load()
    output, _original, original_bytes, _inventory, _retained = (
        _write_no_model_hash_repair_fixture(campaign, tmp_path, monkeypatch)
    )
    with pytest.raises(RuntimeError, match=f"stop after hash repair {stop_after}"):
        campaign.repair_no_model_proof_hashes(output, _stop_after=stop_after)
    assert (output / "PROOF.json").read_bytes() == original_bytes
    assert (output / "PROOF.pre-hash-repair.json").read_bytes() == original_bytes
    if stop_after == "predecessor":
        assert not (output / "PROOF-HASH-REPAIR.json").exists()
    else:
        record_hash = campaign.sha256_file(output / "PROOF-HASH-REPAIR.json")

    campaign.repair_no_model_proof_hashes(output)
    if stop_after == "record":
        assert campaign.sha256_file(output / "PROOF-HASH-REPAIR.json") == record_hash
    assert json.loads((output / "PROOF.json").read_text())[
        "classification"
    ] == campaign.NO_MODEL_HASH_REPAIRED_CLASSIFICATION


def test_no_model_hash_repair_rejects_target_extra_symlink_and_sidecar_tamper(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign = _load()
    output, *_ = _write_no_model_hash_repair_fixture(campaign, tmp_path, monkeypatch)
    wrong = output.with_name("wrong-target")
    wrong.mkdir()
    with pytest.raises(RuntimeError, match="target root or basename"):
        campaign.repair_no_model_proof_hashes(wrong)

    extra = output / "unexpected.txt"
    extra.write_text("drift")
    with pytest.raises(RuntimeError, match="artifact inventory drift"):
        campaign.repair_no_model_proof_hashes(output)
    extra.unlink()

    symlink = output / "unexpected-link"
    symlink.symlink_to(output / "PROOF.json")
    with pytest.raises(RuntimeError, match="symlinked artifacts"):
        campaign.repair_no_model_proof_hashes(output)
    symlink.unlink()

    campaign.repair_no_model_proof_hashes(output)
    record_path = output / "PROOF-HASH-REPAIR.json"
    record = json.loads(record_path.read_text())
    record["root_cause"] = "tampered"
    campaign.atomic_write_json(record_path, record)
    with pytest.raises(RuntimeError, match="record drift"):
        campaign.repair_no_model_proof_hashes(output)


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--base-database-url", "postgres://localhost/forbidden"],
        ["--fixture", "exact"],
    ],
)
def test_no_model_hash_repair_cli_accepts_only_output(
    tmp_path: Path, monkeypatch, extra_args: list[str],
) -> None:
    campaign = _load()
    monkeypatch.setattr(
        campaign, "repair_no_model_proof_hashes",
        lambda _output: pytest.fail("invalid repair CLI reached repair helper"),
    )
    monkeypatch.setattr(
        sys, "argv",
        [
            "run_lme_v2_p1_t6.py",
            "repair-no-model-proof-hashes",
            "--output", str(tmp_path),
            *extra_args,
        ],
    )
    with pytest.raises(RuntimeError, match="accepts only --output"):
        campaign.main()


def test_no_model_hash_repair_requires_reconstructed_old_hash_correlation(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign = _load()
    output, *_ = _write_no_model_hash_repair_fixture(campaign, tmp_path, monkeypatch)
    proof_path = output / "PROOF.json"
    proof = json.loads(proof_path.read_text())
    forged = "e" * 64
    proof["arms"][0]["adapter_proof_sha256"] = forged
    proof["proof_sha256"] = campaign.canonical_sha256({
        key: value for key, value in proof.items() if key != "proof_sha256"
    })
    campaign.atomic_write_json(proof_path, proof)
    campaign.NO_MODEL_HASH_REPAIR_TARGET["old_explicit_hashes"]["fast"] = forged
    campaign.NO_MODEL_HASH_REPAIR_TARGET["proof_sha256"] = proof["proof_sha256"]
    campaign.NO_MODEL_HASH_REPAIR_TARGET["proof_file_sha256"] = (
        campaign.sha256_file(proof_path)
    )
    with pytest.raises(RuntimeError, match="reconstructed root cause mismatch"):
        campaign.repair_no_model_proof_hashes(output)


def test_no_model_hash_repair_rejects_forged_executor_and_resumes_exact_temps(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign = _load()
    output, *_ = _write_no_model_hash_repair_fixture(campaign, tmp_path, monkeypatch)
    (output / "PROOF.pre-hash-repair.json.tmp").write_text("partial")
    audit = campaign.repair_no_model_proof_hashes(output)
    assert audit["cleared_temporaries"] == ["PROOF.pre-hash-repair.json.tmp"]
    assert not (output / "PROOF.pre-hash-repair.json.tmp").exists()
    (output / "PROOF.json.tmp").write_text("partial-current")
    reused = campaign.repair_no_model_proof_hashes(output)
    assert reused["reused"] is True
    assert reused["cleared_temporaries"] == ["PROOF.json.tmp"]

    other_output, *_ = _write_no_model_hash_repair_fixture(
        campaign, tmp_path / "other", monkeypatch
    )
    with pytest.raises(RuntimeError, match="stop after hash repair record"):
        campaign.repair_no_model_proof_hashes(other_output, _stop_after="record")
    record_path = other_output / "PROOF-HASH-REPAIR.json"
    record = json.loads(record_path.read_text())
    record["repair_executor"]["git_commit"] = "f" * 40
    record["record_sha256"] = campaign.canonical_sha256({
        key: value for key, value in record.items() if key != "record_sha256"
    })
    campaign.atomic_write_json(record_path, record)
    with pytest.raises(RuntimeError, match="forged executor commit"):
        campaign.repair_no_model_proof_hashes(other_output)

    bounded_output, *_ = _write_no_model_hash_repair_fixture(
        campaign, tmp_path / "bounded", monkeypatch
    )
    unrecognized = bounded_output / "unrecognized.tmp"
    unrecognized.write_text("must-not-delete")
    with pytest.raises(RuntimeError, match="artifact inventory drift"):
        campaign.repair_no_model_proof_hashes(bounded_output)
    assert unrecognized.read_text() == "must-not-delete"


def test_no_model_hash_repair_lock_and_external_helpers_are_unreachable(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign = _load()
    output, *_ = _write_no_model_hash_repair_fixture(campaign, tmp_path, monkeypatch)
    lock_fd = os.open(output, os.O_RDONLY)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(RuntimeError, match="already active"):
            campaign.repair_no_model_proof_hashes(output)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    def unreachable(*_args, **_kwargs):
        pytest.fail("offline hash repair reached an external helper")

    for name in (
        "_psql_json", "_run_postgres_command", "_run_no_model_adapter_phase",
    ):
        monkeypatch.setattr(campaign, name, unreachable)
    monkeypatch.setattr(campaign.urllib.request, "urlopen", unreachable)
    audit = campaign.repair_no_model_proof_hashes(output)
    assert audit["database_connections"] == audit["model_calls"] == 0


@pytest.mark.parametrize("secret", [
    "postgres://user:password@example.invalid/db",
    "ANTHROPIC_API_KEY=anthropic-secret-value",
    "AZURE_OPENAI_API_KEY=azure-secret-value",
    "PROVIDER_API_KEY=provider-secret-value",
    "COHERE_API_KEY=cohere-secret-value",
    "Authorization: Bearer provider-bearer-token",
])
def test_no_model_hash_repair_secret_scan_fails_closed(
    tmp_path: Path, secret: str,
) -> None:
    campaign = _load()
    safe = tmp_path / "safe.json"
    safe.write_text('{"value":"[REDACTED]"}\n')
    inventory = {"safe.json": campaign.sha256_file(safe)}
    assert campaign._validate_no_model_no_secrets(tmp_path, inventory) == {
        "text_artifacts_scanned": 1,
        "binary_archives_hash_bound": 0,
        "binary_archive_policy": "pg_dump_custom_format_hash_bound_not_text_scanned",
    }
    safe.write_text(json.dumps({"value": secret}) + "\n")
    with pytest.raises(RuntimeError, match="secret scan failed"):
        campaign._validate_no_model_no_secrets(tmp_path, inventory)


def test_no_model_hash_repair_validates_pg_dump_magic_without_decoding(
    tmp_path: Path,
) -> None:
    campaign = _load()
    archive = tmp_path / "bank.dump"
    archive.write_bytes(b"PGDMP\x01\x10synthetic-custom-format")
    inventory = {"bank.dump": campaign.sha256_file(archive)}
    assert campaign._validate_no_model_no_secrets(tmp_path, inventory)[
        "binary_archives_hash_bound"
    ] == 1
    archive.write_bytes(b"not-a-custom-format-dump")
    with pytest.raises(RuntimeError, match="custom-format archive"):
        campaign._validate_no_model_no_secrets(tmp_path, inventory)


@pytest.mark.parametrize(
    "tamper,message",
    [
        (("contract", "mode", "deep"), "fast-only contract"),
        (("contract", "gold_fields_consumed", ["answer"]), "fast-only contract"),
        (("public", "trace", "mode_executed", "deep"), "trace contract"),
        (("public", "trace", "cost_micros", 1), "trace contract"),
        (("public", "trace", "deep", True), "trace contract"),
        (("public", "trace", "l4_sandbox_id", "sandbox"), "trace contract"),
        (("public", "recall_response", "degraded", True), "response contract"),
        (("public", "recall_response", "deep", True), "response contract"),
    ],
)
def test_query_only_memory_proof_rejects_non_fast_or_deep_evidence(
    tamper: tuple, message: str,
) -> None:
    campaign = _load()
    worker = {"completed_sources": 670, "stdout_sha256": "1" * 64,
              "stderr_sha256": "2" * 64}
    construction = {"pairing": {
        "trajectory_count": 500, "resource_count": 670, "worker": worker,
    }}
    construction_sha = "3" * 64
    manifest = {
        "construction": construction,
        "construction_proof_sha256": construction_sha,
    }
    memory = _no_model_memory_proof(
        campaign, arm="fast", construction=construction,
        construction_sha256=construction_sha,
    )
    *path, value = tamper
    target = memory
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match=message):
        campaign._validate_query_only_memory_proof(
            memory, manifest, require_no_model_fast=True
        )


@pytest.mark.parametrize(
    "tamper, message",
    [
        ("ledger", "1/1/4/2/2 ledger drift"),
        ("arm_mode", "query-only arm drift"),
        ("cleanup", "cleanup, identity, or dispatch drift"),
        ("dispatch", "cleanup, identity, or dispatch drift"),
        ("quiescence", "quiescence proof drift"),
        ("tenant", "isolation or construction context drift"),
        ("instance", "instance identities are not distinct"),
        ("query_hash", "cross-arm query evidence drift"),
        ("response_hash", "response identities are not distinct"),
    ],
)
def test_completed_no_model_semantics_rejects_authorization_field_tamper(
    tmp_path: Path, monkeypatch, tamper: str, message: str,
) -> None:
    campaign = _load()
    output = tmp_path / "semantic"
    output.mkdir()
    campaign.atomic_write_json(output / "RECOVERY-INCIDENT.json", {"incident": True})
    campaign.atomic_write_json(
        output / "FIRST-RECOVERY-FAILURE.json", {"failure": True}
    )
    worker = {
        "completed_sources": 670,
        "stdout_sha256": "1" * 64,
        "stderr_sha256": "2" * 64,
    }
    retains = [{"trajectory_id": f"trajectory-{index:03d}"} for index in range(500)]
    construction = {
        "pairing": {
            "trajectory_count": 500,
            "resource_count": 670,
            "worker": worker,
            "retains": retains,
        },
        "isolation": {
            "tenant_id": "tenant-shared",
            "instance_id": "construction-instance",
            "context": {
                "scope_id": "scope-shared", "actor_id": "actor-shared",
            },
        },
    }
    construction["construction_proof_sha256"] = campaign.canonical_sha256(
        construction
    )
    manifest = {
        "construction": construction,
        "construction_proof_sha256": construction["construction_proof_sha256"],
        "case_contract": {
            "fixture": "exact", "case_id": "19367bc7", "input_sha256": "3" * 64,
        },
        "logical_identity": {"sha256": "4" * 64},
    }
    incident = {
        "initial_attempt": {
            "constructions": 1, "dumps": 1, "resets": 1, "restores": 1,
            "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
        },
        "diagnostic_archive_only_pg17_replay": {
            "constructions": 0, "dumps": 0, "resets": 0, "restores": 1,
            "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
        },
    }
    failure = {"attempt": {
        "constructions": 0, "dumps": 0, "resets": 0, "restores": 1,
        "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
    }}
    accounting = campaign._no_model_attempt_accounting(True, incident, failure)
    accounting["recovery"]["incident_sha256"] = campaign.sha256_file(
        output / "RECOVERY-INCIDENT.json"
    )
    accounting["recovery"]["first_recovery_failure_sha256"] = campaign.sha256_file(
        output / "FIRST-RECOVERY-FAILURE.json"
    )
    retained = {}
    for arm in ("fast", "sonnet"):
        relative = f"arms/{arm}/adapter.json"
        campaign.atomic_write_json(
            output / relative,
            _no_model_memory_proof(
                campaign, arm=arm, construction=construction,
                construction_sha256=construction["construction_proof_sha256"],
            ),
        )
        retained[arm] = {"path": relative, "sha256": campaign.sha256_file(output / relative)}
    monkeypatch.setattr(
        campaign, "NO_MODEL_HASH_REPAIR_TARGET",
        {**campaign.NO_MODEL_HASH_REPAIR_TARGET,
         "case_id": "19367bc7", "retained_artifacts": retained},
    )
    monkeypatch.setattr(
        campaign, "_load_no_model_recovery",
        lambda *_args, **_kwargs: (
            construction, manifest, incident, failure, {"validated": True},
        ),
    )
    monkeypatch.setattr(
        campaign, "_validate_no_model_no_secrets", lambda *_args, **_kwargs: {
            "text_artifacts_scanned": 4,
            "binary_archives_hash_bound": 1,
            "binary_archive_policy": "pg_dump_custom_format_hash_bound_not_text_scanned",
        }
    )
    proof = {
        "fixture": {"name": "exact", "case_id": "19367bc7", "input_sha256": "3" * 64},
        "archive_tools": {},
        "database_schema_identity": {},
        **accounting,
        "construction_proof_sha256": construction["construction_proof_sha256"],
        "logical_identity_sha256": "4" * 64,
        "cleanup": {
            "source_api_key_count": 0,
            "source_job_state": {"pending": 0, "dead": 0, "total": 0},
            "orphan_clone_count": 0,
            "force_drop_verified": True,
        },
        "external_dispatch": {"configured": False, "dispatches": 0, "deep_enabled": False},
        "artifact_hashes": {},
        "arms": [{
            "arm": arm,
            "query_only": True,
            "verification_recall_mode": "fast",
            "construction_work": {"retains": 0, "worker_drains": 0},
            "construction_proof_sha256": construction["construction_proof_sha256"],
            "context_sha256": "9" * 64,
            "timing_ms": {"recall": 1},
            "pre_query_logical_identity_sha256": "4" * 64,
            "post_query_logical_identity_sha256": "4" * 64,
            "api_key_count": 1,
            "job_state": {"pending": 0, "dead": 0, "total": 0},
            "clone_database": f"memphant_p1t6_19367bc7_deadbeef_{arm}",
            "source_quiescence": {
                "policy": "only_exact_autovacuum_worker_may_wait",
                "timeout_seconds": 180.0,
                "sample_interval_seconds": 1.0,
                "sample_count": 2,
                "consecutive_zero_samples": 2,
                "unexpected_sessions": [],
                "terminated_sessions": 0,
                "observed_connections": [],
                "observed_progress": [],
            },
        } for arm in ("fast", "sonnet")],
    }
    assert campaign._validate_completed_no_model_semantics(
        output, proof, manifest
    )["recovery_lineage_verified"] is True
    if tamper == "ledger":
        proof["counts"]["restores"] = 3
    elif tamper == "arm_mode":
        proof["arms"][0]["verification_recall_mode"] = "sonnet"
    elif tamper == "cleanup":
        proof["cleanup"]["orphan_clone_count"] = 1
    elif tamper == "dispatch":
        proof["external_dispatch"]["dispatches"] = 1
    elif tamper == "quiescence":
        proof["arms"][0]["source_quiescence"]["unexpected_sessions"] = [
            {"backend_type": "client backend"}
        ]
    else:
        sonnet_path = output / retained["sonnet"]["path"]
        memory = json.loads(sonnet_path.read_text())
        if tamper == "tenant":
            memory["isolation"]["tenant_id"] = "other-tenant"
            memory["public"]["trace"]["tenant_id"] = "other-tenant"
        elif tamper == "instance":
            memory["isolation"]["instance_id"] = "1" * 32
        elif tamper == "query_hash":
            memory["query"]["query_sha256"] = "a" * 64
        else:
            memory["query"]["recall_response_sha256"] = "7" * 64
        campaign.atomic_write_json(sonnet_path, memory)
    with pytest.raises(RuntimeError, match=message):
        campaign._validate_completed_no_model_semantics(output, proof, manifest)


def test_arm_clone_cleanup_is_forceful_and_name_bounded(monkeypatch) -> None:
    campaign = _load()
    calls = []
    monkeypatch.setattr(
        campaign.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command)
            or campaign.subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    campaign._drop_local_database(
        "postgres://bench:secret@127.0.0.1:5432/memphant_p1t6_19367bc7_deadbeef_sonnet"
    )
    assert calls == [[
        "dropdb", "--force",
        "--maintenance-db=postgres://bench:secret@127.0.0.1:5432/postgres",
        "memphant_p1t6_19367bc7_deadbeef_sonnet",
    ]]
    with pytest.raises(RuntimeError, match="P1-T6 arm database name"):
        campaign._drop_local_database(
            "postgres://bench:secret@127.0.0.1:5432/memphant"
        )


def test_no_model_verifier_rejects_model_configuration_before_scratch_helper(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-dispatch")
    monkeypatch.setattr(
        campaign.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("scratch helper reached"),
    )
    with pytest.raises(RuntimeError, match="forbids external model configuration"):
        campaign.run_no_model_verifier_with_scratch(
            tmp_path / "proof",
            "postgres://bench:secret@127.0.0.1:5432/memphant",
        )
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="absolute directory and materialized"):
        campaign.run_no_model_verifier_with_scratch(
            tmp_path / "proof",
            "postgres://bench:secret@127.0.0.1:5432/memphant",
            fixture="exact", directory=Path("dataset"),
            materialized=Path("materialized"), case_id="19367bc7",
        )


def test_exact_no_model_wrapper_forwards_registered_absolute_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return campaign.subprocess.CompletedProcess(
            command, 0,
            json.dumps({"verified": True, "paid_calls": 0, "audit": {"ok": True}}),
            "",
        )

    monkeypatch.setattr(campaign.subprocess, "run", run)
    directory = tmp_path / "dataset"
    materialized = tmp_path / "materialized"
    result = campaign.run_no_model_verifier_with_scratch(
        tmp_path / "proof",
        "postgres://bench:secret@127.0.0.1:5432/memphant",
        fixture="exact", directory=directory, materialized=materialized,
        case_id="19367bc7",
    )

    assert result == {"ok": True}
    command = calls[0]
    assert command[command.index("--fixture") + 1] == "exact"
    assert command[command.index("--directory") + 1] == str(directory)
    assert command[command.index("--materialized") + 1] == str(materialized)
    assert command[command.index("--case-id") + 1] == "19367bc7"
    resume_output = tmp_path / "resume-proof"
    resume_output.mkdir()
    campaign.run_no_model_verifier_with_scratch(
        resume_output,
        "postgres://bench:secret@127.0.0.1:5432/memphant",
        fixture="exact", directory=directory, materialized=materialized,
        case_id="19367bc7", resume=True,
    )
    assert "--resume" in calls[1]


def test_exact_no_model_fixture_binds_registered_case_order_and_fast_config(
    tmp_path: Path,
) -> None:
    campaign = _load()
    case_id = campaign.load_campaign_manifest()["run_order"]["case_order"][0]
    assert case_id == "19367bc7"
    directory = tmp_path / "dataset"
    materialized = tmp_path / "materialized"
    case_dir = materialized / case_id
    (directory / "data").mkdir(parents=True)
    case_dir.mkdir(parents=True)
    trajectory_ids = [f"trajectory-{index:03d}" for index in range(500)]
    trajectories = [
        {
            "id": trajectory_id,
            "goal": "remember",
            "outcome": "success",
            "states": [{"url": "https://example.test", "accessibility_tree": trajectory_id}],
        }
        for trajectory_id in trajectory_ids
    ]
    (directory / "data/trajectories.jsonl").write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in trajectories) + "\n"
    )
    campaign.atomic_write_json(case_dir / "haystack.json", {case_id: trajectory_ids})
    campaign.atomic_write_json(case_dir / "questions.json", [{
        "id": case_id, "question": "What was remembered?", "answer": "not consumed",
    }])
    memory_config = json.loads(campaign.MEMORY_CONFIG.read_text())
    memory_config["memory_params"]["mode"] = "fast"
    memory_config["memory_params"]["compiler_version"] = "materialized-fast-fixture"
    campaign.atomic_write_json(case_dir / "memory.fast.json", memory_config)

    fixture = campaign._no_model_fixture(
        "exact", directory, materialized, case_id
    )

    assert fixture["case_id"] == "19367bc7"
    assert fixture["trajectory_ids"] == trajectory_ids
    assert [row["id"] for row in fixture["trajectories"]] == trajectory_ids
    assert fixture["memory_params"] == memory_config["memory_params"]
    with pytest.raises(RuntimeError, match="registered first case"):
        campaign._no_model_fixture("exact", directory, materialized, "21f3228c")
    campaign.atomic_write_json(
        case_dir / "haystack.json", {case_id: trajectory_ids[:-1]}
    )
    with pytest.raises(RuntimeError, match="exactly 500 ordered trajectories"):
        campaign._no_model_fixture("exact", directory, materialized, case_id)


def test_exact_no_model_classification_requires_frozen_500_670_pairing() -> None:
    campaign = _load()
    trajectory_ids = [f"trajectory-{index:03d}" for index in range(500)]
    fixture = {
        "name": "exact", "case_id": "19367bc7",
        "trajectory_ids": trajectory_ids,
    }
    construction = {
        "pairing": {
            "trajectory_count": 500,
            "resource_count": 670,
            "worker": {"completed_sources": 670},
            "retains": [{"trajectory_id": item} for item in trajectory_ids],
        }
    }
    assert campaign._no_model_proof_classification(fixture, construction) == (
        "no_model_exact_case_authorization_candidate"
    )
    wrong = json.loads(json.dumps(construction))
    wrong["pairing"]["resource_count"] = 669
    with pytest.raises(RuntimeError, match="500 trajectories and 670 resources"):
        campaign._no_model_proof_classification(fixture, wrong)
    assert campaign._no_model_proof_classification(
        {"name": "tiny", "case_id": "00000000"},
        {"pairing": {"trajectory_count": 1, "resource_count": 1}},
    ) == "no_model_clone_mechanics_smoke_not_authorization"


def test_exact_no_model_resume_skips_construction_dump_and_reset(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    source = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"
    output = tmp_path / "existing"
    output.mkdir()
    trajectory_ids = [f"trajectory-{index:03d}" for index in range(500)]
    data = {
        "name": "exact", "case_id": "19367bc7", "input_sha256": "a" * 64,
        "trajectory_ids": trajectory_ids, "trajectories": [],
        "question": "question", "memory_params": {"mode": "fast"},
    }
    construction = {"pairing": {
        "trajectory_count": 500, "resource_count": 670,
        "worker": {"completed_sources": 670},
        "retains": [{"trajectory_id": item} for item in trajectory_ids],
    }}
    manifest = {
        "logical_identity": {"sha256": "b" * 64},
        "construction_duration_ms": 123,
    }
    incident = {"classification": "p1_t6_exact_pre_clone_quiescence_failure"}
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setenv("MEMPHANT_TEST_DATABASE_URL", source)
    monkeypatch.setattr(campaign, "_assert_no_model_environment", lambda: None)
    monkeypatch.setattr(campaign, "_no_model_fixture", lambda *_args: data)
    monkeypatch.setattr(campaign, "_resolve_archive_tools", lambda _url: {
        "server_major": 17, "pg_dump": {"binary": "/pg_dump"},
        "pg_restore": {"binary": "/pg_restore"},
    })
    monkeypatch.setattr(campaign, "_fingerprint", lambda path: {
        "path": str(path), "bytes": 1, "sha256": "c" * 64,
    })
    monkeypatch.setattr(campaign, "_database_schema_identity", lambda _url: {
        "sha256": "d" * 64,
    })
    monkeypatch.setattr(campaign, "_database_exists", lambda _url: False)
    for name in ("_construct_no_model_source", "_dump_case_bank", "_reset_case_source"):
        monkeypatch.setattr(
            campaign, name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(
                f"resume called {_name}"
            ),
        )
    monkeypatch.setattr(
        campaign, "_load_no_model_recovery",
        lambda *_args: (
            construction, manifest, incident,
            {"attempt": {"restores": 1}}, {"snapshot": True},
        ),
    )
    restored = []
    monkeypatch.setattr(
        campaign, "_restore_case_bank",
        lambda *_args, **_kwargs: restored.append(True) or (_ for _ in ()).throw(
            RuntimeError("stop after recovery restore")
        ),
    )

    with pytest.raises(RuntimeError, match="stop after recovery restore"):
        campaign.run_no_model_verifier(
            output, fixture="exact", directory=tmp_path,
            materialized=tmp_path, case_id="19367bc7", resume=True,
        )
    assert restored == [True]


def test_exact_no_model_resume_holds_case_lease_for_entire_controller(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    output = tmp_path / "existing"
    output.mkdir()
    held = []

    def locked(*_args, **_kwargs):
        with pytest.raises(RuntimeError, match="case is already active"):
            with campaign._case_lease(output, "19367bc7"):
                pass
        held.append(True)
        return {"complete": True}

    monkeypatch.setattr(campaign, "_run_no_model_verifier_locked", locked)
    assert campaign.run_no_model_verifier(
        output, fixture="exact", directory=tmp_path,
        materialized=tmp_path, case_id="19367bc7", resume=True,
    ) == {"complete": True}
    assert held == [True]


def test_exact_no_model_recovery_rejects_arm_artifact_or_clone(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    source = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"
    output = tmp_path / "existing"
    (output / "arms").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="arm artifact"):
        campaign._require_no_model_recovery_start(source, output, "19367bc7")
    (output / "arms").rmdir()
    monkeypatch.setattr(campaign, "_database_exists", lambda _url: True)
    with pytest.raises(RuntimeError, match="unexpected arm clone"):
        campaign._require_no_model_recovery_start(source, output, "19367bc7")


def test_exact_no_model_recovery_rejects_wrong_bank_fixture(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    output = tmp_path / "existing"
    bank = output / "case-bank"
    bank.mkdir(parents=True)
    construction_path = output / "construction-proof.json"
    campaign.atomic_write_json(construction_path, {"pairing": {}})
    incident = {
        "classification": "p1_t6_exact_pre_clone_quiescence_failure",
        "case_id": "19367bc7",
        "archive_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "construction_proof_sha256": "c" * 64,
        "external_dispatches": 0,
        "initial_attempt": {"restores": 1},
    }
    campaign.atomic_write_json(output / "RECOVERY-INCIDENT.json", incident)
    monkeypatch.setattr(campaign, "_load_case_bank", lambda _bank: ({
        "case_contract": {"case_id": "wrong-case"},
        "construction": {"pairing": {}},
        "archive_sha256": "a" * 64,
        "database_schema_identity": {"sha256": "d" * 64},
        "postgres": {"binary": "/pg_dump"},
        "postgres_major": 17,
    }, bank / ("a" * 64 + ".dump")))

    with pytest.raises(RuntimeError, match="bank contract drift"):
        campaign._load_no_model_recovery(
            output,
            {"name": "exact", "case_id": "19367bc7"},
            {"case_id": "19367bc7"},
            {"server_major": 17, "pg_dump": {"binary": "/pg_dump"},
             "pg_restore": {"binary": "/pg_restore"}},
            {"sha256": "d" * 64},
        )


def test_exact_no_model_recovery_counts_diagnostic_restore() -> None:
    campaign = _load()
    incident = {
        "initial_attempt": {
            "constructions": 1, "dumps": 1, "resets": 1, "restores": 1,
            "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
            "outcome": "pre_clone_quiescence_failure",
        },
        "diagnostic_archive_only_pg17_replay": {
            "constructions": 0, "dumps": 0, "resets": 0, "restores": 1,
            "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
            "persistent_source_sessions": 0,
            "outcome": "archive_restored_and_source_sessions_queried",
            "authorization_evidence": False,
        },
    }

    failed_recovery = {"attempt": {
        "constructions": 0, "dumps": 0, "resets": 0, "restores": 1,
        "clones": 0, "query_only_recalls": 0, "external_dispatches": 0,
        "outcome": "expected_autovacuum_timeout_pre_clone",
    }}
    accounting = campaign._no_model_attempt_accounting(
        True, incident, failed_recovery
    )

    assert accounting["counts"]["restores"] == 4
    assert accounting["attempts"]["diagnostic"]["restores"] == 1
    assert accounting["attempts"]["recovery_1_failed"]["restores"] == 1
    assert accounting["attempts"]["recovery_2"]["restores"] == 1
    assert accounting["recovery"]["repeated_constructions"] == 0
    assert accounting["recovery"]["repeated_dumps"] == 0


@pytest.mark.parametrize(
    "relative",
    [
        "RECOVERY-INCIDENT.json",
        "FIRST-RECOVERY-FAILURE.json",
        "case-bank/manifest.json",
        "case-bank/archive.dump",
        "construction-proof.json",
    ],
)
def test_exact_no_model_recovery_rejects_invariant_mutation_after_arm(
    tmp_path: Path, relative: str
) -> None:
    campaign = _load()
    output = tmp_path / "recovery"
    paths = [
        "RECOVERY-INCIDENT.json",
        "FIRST-RECOVERY-FAILURE.json",
        "case-bank/manifest.json",
        "case-bank/archive.dump",
        "construction-proof.json",
    ]
    for item in paths:
        path = output / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original-" + item)
    invariants = {
        "incident_sha256": campaign.sha256_file(output / "RECOVERY-INCIDENT.json"),
        "pre_recovery_inventory": {
            item: campaign.sha256_file(output / item)
            for item in paths if item not in {
                "RECOVERY-INCIDENT.json", "FIRST-RECOVERY-FAILURE.json"
            }
        },
        "first_recovery_failure_sha256": campaign.sha256_file(
            output / "FIRST-RECOVERY-FAILURE.json"
        ),
        "case_bank_seal": {"seal_sha256": "a" * 64},
    }
    (output / relative).write_text("mutated-during-arm")

    with pytest.raises(RuntimeError, match="recovery .* drift"):
        campaign._revalidate_no_model_recovery(
            output, {"pairing": {}}, invariants
        )
    assert not (output / "PROOF.json").exists()


def test_no_model_verifier_builds_banks_restores_queries_and_cleans_two_clones(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    source = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"
    output = tmp_path / "proof"
    logical = {"tables": {}, "sequences": {}}
    logical["sha256"] = campaign.canonical_sha256(logical)
    construction = {
        "schema_version": 1,
        "contract": {},
        "isolation": {},
        "pairing": {
            "trajectory_count": 1,
            "resource_count": 1,
            "worker": {"completed_sources": 1},
            "retains": [{}],
        },
    }
    construction["construction_proof_sha256"] = campaign.canonical_sha256(
        construction
    )
    events = []
    clones = set()
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setenv("MEMPHANT_TEST_DATABASE_URL", source)
    monkeypatch.setattr(campaign, "_assert_no_model_environment", lambda: None)
    monkeypatch.setattr(campaign, "_fingerprint", lambda path: {
        "path": str(path), "bytes": 1, "sha256": "b" * 64,
    })
    monkeypatch.setattr(campaign, "_resolve_archive_tools", lambda _url: {
        "server_major": 17,
        "pg_dump": {"binary": "/pg17/pg_dump", "major": 17, "server_major": 17},
        "pg_restore": {"binary": "/pg17/pg_restore", "major": 17, "server_major": 17},
    })
    monkeypatch.setattr(campaign, "_database_schema_identity", lambda _url: {
        "sha256": "s" * 64,
    })
    monkeypatch.setattr(campaign, "_database_bank_identity", lambda _url: logical)
    monkeypatch.setattr(campaign, "_database_key_count", lambda url: 0 if url == source else 1)
    monkeypatch.setattr(campaign, "_job_state_counts", lambda _url: (0, 0, 0))
    monkeypatch.setattr(campaign, "_construct_no_model_source", lambda *_args: (
        events.append("construct") or (construction, 11)
    ))

    def dump(_url, bank, proof, contract, **_kwargs):
        events.append("dump")
        bank.mkdir(parents=True)
        archive = bank / ("a" * 64 + ".dump")
        archive.write_bytes(b"archive")
        manifest = {
            "archive": archive.name,
            "archive_sha256": campaign.sha256_file(archive),
            "logical_identity": logical,
            "construction": proof,
            "construction_proof_sha256": proof["construction_proof_sha256"],
            "case_contract_sha256": campaign.canonical_sha256(contract),
        }
        campaign.atomic_write_json(bank / "manifest.json", manifest)
        return manifest

    monkeypatch.setattr(campaign, "_dump_case_bank", dump)
    monkeypatch.setattr(campaign, "_reset_case_source", lambda _url: events.append("reset"))
    monkeypatch.setattr(
        campaign, "_restore_case_bank",
        lambda *_args, **_kwargs: events.append("restore") or json.loads(
            (output / "case-bank/manifest.json").read_text()
        ),
    )
    monkeypatch.setattr(
        campaign, "_case_bank_seal",
        lambda _path: {
            "manifest_sha256": "1" * 64,
            "archive_sha256": campaign.sha256_file(output / "case-bank" / ("a" * 64 + ".dump")),
            "logical_identity_sha256": logical["sha256"],
            "construction_proof_sha256": construction["construction_proof_sha256"],
            "case_contract_sha256": "2" * 64,
            "seal_sha256": "3" * 64,
        },
    )

    def clone(_url, name, _identity, **kwargs):
        events.append(("clone", name))
        clone_url = source.rsplit("/", 1)[0] + "/" + name
        clones.add(clone_url)
        assert kwargs == {"include_quiescence_proof": True}
        return clone_url, {
            "policy": "only_exact_autovacuum_worker_may_wait",
            "timeout_seconds": 180.0,
            "sample_interval_seconds": 1.0,
            "sample_count": 2,
            "consecutive_zero_samples": 2,
            "observed_connections": [],
            "observed_progress": [],
            "unexpected_sessions": [],
            "terminated_sessions": 0,
        }

    def query(clone_url, *_args):
        arm = clone_url.rsplit("_", 1)[-1]
        events.append(("query", arm))
        return {
            "arm": arm,
            "query_only": True,
            "verification_recall_mode": "fast",
            "construction_proof_sha256": construction["construction_proof_sha256"],
            "adapter_proof_sha256": ("4" if arm == "fast" else "5") * 64,
            "construction_work": {"retains": 0, "worker_drains": 0},
            "timing_ms": {"recall": 1},
        }

    monkeypatch.setattr(campaign, "_clone_case_source", clone)
    monkeypatch.setattr(campaign, "_run_no_model_clone_query", query)
    monkeypatch.setattr(
        campaign, "_drop_local_database",
        lambda url: (events.append(("drop", url)), clones.discard(url)),
    )
    monkeypatch.setattr(campaign, "_database_exists", lambda url: url in clones)
    monkeypatch.setattr(
        campaign.subprocess, "run",
        lambda command, **_kwargs: campaign.subprocess.CompletedProcess(
            command, 0, "deadbeef\n", ""
        ),
    )

    proof = campaign.run_no_model_verifier(output)

    assert events[:4] == ["construct", "dump", "reset", "restore"]
    assert [event[0] for event in events if isinstance(event, tuple)] == [
        "clone", "query", "drop", "clone", "query", "drop",
    ]
    assert proof["classification"] == "no_model_clone_mechanics_smoke_not_authorization"
    assert proof["counts"] == {
        "constructions": 1, "dumps": 1, "restores": 1,
        "clones": 2, "query_only_recalls": 2,
    }
    assert {arm["verification_recall_mode"] for arm in proof["arms"]} == {"fast"}
    assert all(arm["source_quiescence"]["consecutive_zero_samples"] == 2
               for arm in proof["arms"])
    assert proof["cleanup"]["orphan_clone_count"] == 0
    assert proof["archive"]["sha256"] == campaign.sha256_file(
        output / "case-bank" / ("a" * 64 + ".dump")
    )
    assert source not in json.dumps(proof)
    assert "secret" not in json.dumps(proof)


def test_archive_state_fails_closed_after_first_completed_row(tmp_path: Path) -> None:
    campaign = _load()
    bank = tmp_path / "bank"
    bank.mkdir()
    campaign.atomic_write_json(bank / "manifest.json", {
        "archive": "a" * 64 + ".dump", "archive_sha256": "a" * 64,
    })
    with pytest.raises(RuntimeError, match="completed billable row.*archive"):
        campaign._verify_case_archive_resume(bank, completed_rows=1)
    assert campaign._verify_case_archive_resume(bank, completed_rows=2) is None


@pytest.mark.parametrize("sonnet_operational", [True, False])
def test_run_case_builds_once_restores_then_runs_two_key_local_clones(
    tmp_path: Path, monkeypatch, sonnet_operational: bool
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    case_id = manifest["run_order"]["case_order"][0]
    output = tmp_path / "root"
    output.mkdir()
    source_url = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setenv("MEMPHANT_TEST_DATABASE_URL", source_url)
    events = []
    construction = {
        "construction_proof_sha256": "c" * 64,
        "isolation": {"tenant_id": "tenant"},
        "pairing": {"resource_count": 2, "worker": {"completed_sources": 2}},
    }
    logical = {"tables": {}, "sha256": "e" * 64}

    def construct(*_args):
        events.append("construct")
        return construction, 12_345

    def dump(_url, bank, proof, _contract, **kwargs):
        events.append("dump")
        assert kwargs["construction_duration_ms"] == 12_345
        bank.mkdir(parents=True, exist_ok=True)
        archive_body = b"archive"
        digest = hashlib.sha256(archive_body).hexdigest()
        (bank / (digest + ".dump")).write_bytes(archive_body)
        campaign.atomic_write_json(bank / "construction-proof.json", proof)
        result = {
            "format_version": campaign.BANK_FORMAT_VERSION,
            "archive": digest + ".dump", "archive_sha256": digest,
            "logical_identity": logical,
            "construction_proof_sha256": "c" * 64,
            "construction_duration_ms": 12_345,
            "case_contract_sha256": campaign.canonical_sha256(_contract),
        }
        campaign.atomic_write_json(bank / "manifest.json", result)
        return result

    def restore(_url, _bank, _contract, **_kwargs):
        events.append("restore")
        return {
            "logical_identity": logical,
            "archive": json.loads((tmp_path / "root/case-banks" / case_id / "manifest.json").read_text())["archive"],
            "archive_sha256": json.loads((tmp_path / "root/case-banks" / case_id / "manifest.json").read_text())["archive_sha256"],
        }

    def clone(_url, name, expected):
        events.append(("clone", name, expected["sha256"]))
        return source_url.rsplit("/", 1)[0] + "/" + name

    def execute(_directory, _materialized, root, row, _manifest, bank_seal):
        events.append((
            "execute", row["arm"],
            campaign.os.environ["MEMPHANT_LME_PREBUILT_PROOF"],
            campaign.os.environ["MEMPHANT_TEST_DATABASE_URL"],
        ))
        row_dir = root / row["row_id"]
        row_dir.mkdir()
        campaign.atomic_write_json(row_dir / "case-bank-seal.json", bank_seal)
        campaign.atomic_write_json(row_dir / "row-proof.json", {
            "complete": True, "execution_complete": True,
            "treatment_operational": row["arm"] == "fast" or sonnet_operational,
            "outcome": (
                "success"
                if row["arm"] == "fast" or sonnet_operational
                else "operational_failure"
            ),
            "row": row, "query_only": True,
            "case_bank_seal_sha256": bank_seal["seal_sha256"],
            "artifact_hashes": campaign.artifact_hashes(row_dir),
        })

    monkeypatch.setattr(campaign, "_recover_orphan_clones", lambda *_args: events.append("recover"))
    monkeypatch.setattr(campaign, "_case_archive_tools", lambda *_args: {
        "pg_dump": {"binary": "/pg_dump"}, "pg_restore": {"binary": "/pg_restore"},
    })
    monkeypatch.setattr(campaign, "_case_bank_contract", lambda *_args: {"question_id": case_id})
    monkeypatch.setattr(campaign, "_construct_case_source", construct)
    monkeypatch.setattr(campaign, "_dump_case_bank", dump)
    monkeypatch.setattr(campaign, "_reset_case_source", lambda _url: events.append("reset"))
    monkeypatch.setattr(campaign, "_restore_case_bank", restore)
    monkeypatch.setattr(campaign, "_clone_case_source", clone)
    monkeypatch.setattr(campaign, "_verify_case_bank_seal", lambda *_args: None)
    monkeypatch.setattr(campaign, "_execute_case_row", execute)
    monkeypatch.setattr(campaign, "_database_key_count", lambda url: 0 if url == source_url else 1)
    monkeypatch.setattr(campaign, "_drop_local_database", lambda url: events.append(("drop", url)))

    if sonnet_operational:
        result = campaign._run_case(tmp_path, tmp_path, output, case_id, manifest)
    else:
        with pytest.raises(RuntimeError, match="non-operational pair"):
            campaign._run_case(tmp_path, tmp_path, output, case_id, manifest)
        result = None
    assert events[:4] == ["recover", "construct", "dump", "reset"]
    assert events[4] == "restore"
    clones = [event for event in events if isinstance(event, tuple) and event[0] == "clone"]
    assert [event[1].rsplit("_", 1)[-1] for event in clones] == ["fast", "sonnet"]
    assert clones[0][1] != clones[1][1]
    executes = [event for event in events if isinstance(event, tuple) and event[0] == "execute"]
    assert [event[1] for event in executes] == ["fast", "sonnet"]
    assert all(event[2].endswith("construction-proof.json") for event in executes)
    assert executes[0][3] != executes[1][3]
    assert len([event for event in events if isinstance(event, tuple) and event[0] == "drop"]) == 2
    if sonnet_operational:
        assert result == {"case_id": case_id, "constructed": True, "completed_rows": 2}


def test_run_case_reuses_archive_after_interruption_and_drops_failed_clone(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    case_id = manifest["run_order"]["case_order"][0]
    output = tmp_path / "root"
    output.mkdir()
    rows = [row for row in campaign.expanded_run_order(manifest) if row["question_id"] == case_id]
    completed = output / rows[0]["row_id"]
    completed.mkdir()
    campaign.atomic_write_json(completed / "row-proof.json", {"complete": True, "row": rows[0]})
    source_url = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setenv("MEMPHANT_TEST_DATABASE_URL", source_url)
    bank = output / "case-banks" / case_id
    bank.mkdir(parents=True)
    archive_body = b"archive"
    archive_digest = hashlib.sha256(archive_body).hexdigest()
    archive = bank / (archive_digest + ".dump")
    archive.write_bytes(archive_body)
    construction = {"schema_version": 1}
    construction["construction_proof_sha256"] = campaign.canonical_sha256(
        construction
    )
    logical = {"tables": {}, "sequences": {}}
    logical["sha256"] = campaign.canonical_sha256({
        "tables": logical["tables"], "sequences": logical["sequences"],
    })
    case_contract = {"question_id": case_id}
    campaign.atomic_write_json(bank / "construction-proof.json", construction)
    campaign.atomic_write_json(bank / "manifest.json", {
        "format_version": campaign.BANK_FORMAT_VERSION,
        "archive": archive.name,
        "archive_sha256": campaign.sha256_file(archive),
        "excluded_tables": list(campaign.BANK_EXCLUDED_TABLES),
        "construction": construction,
        "construction_proof_sha256": construction["construction_proof_sha256"],
        "construction_duration_ms": 12_345,
        "case_contract": case_contract,
        "case_contract_sha256": campaign.canonical_sha256(case_contract),
        "postgres": {"major": 18, "server_major": 18},
        "postgres_major": 18,
        "logical_identity": logical,
    })
    attempt = output / "case-construction" / case_id / "attempt-0001"
    campaign.atomic_write_json(attempt / "attempt.json", {
        "schema_version": 1, "attempt_id": "attempt-0001", "case_id": case_id,
        "classification": "free_local_construction", "complete": False,
    })
    campaign.atomic_write_json(attempt / "complete.json", {
        "schema_version": 1, "attempt_id": "attempt-0001", "case_id": case_id,
        "construction_proof_sha256": construction["construction_proof_sha256"],
        "construction_duration_ms": 12_345, "complete": True,
    })
    bank_seal = campaign._case_bank_seal(bank / "manifest.json")
    campaign.atomic_write_json(completed / "case-bank-seal.json", bank_seal)
    completed_proof = json.loads((completed / "row-proof.json").read_text())
    completed_proof.update({
        "case_bank_seal_sha256": bank_seal["seal_sha256"],
        "artifact_hashes": campaign.artifact_hashes(
            completed, exclude={"row-proof.json"}
        ),
    })
    campaign.atomic_write_json(completed / "row-proof.json", completed_proof)
    events = []
    monkeypatch.setattr(campaign, "_recover_orphan_clones", lambda *_args: events.append("recover"))
    monkeypatch.setattr(campaign, "_case_archive_tools", lambda *_args: {
        "pg_dump": {"binary": "/pg_dump"}, "pg_restore": {"binary": "/pg_restore"},
    })
    monkeypatch.setattr(campaign, "_case_bank_contract", lambda *_args: {"question_id": case_id})
    monkeypatch.setattr(campaign, "_construct_case_source", lambda *_args: pytest.fail("archive resume rebuilt construction"))
    monkeypatch.setattr(campaign, "_dump_case_bank", lambda *_args: pytest.fail("archive resume redumped construction"))
    monkeypatch.setattr(campaign, "_restore_case_bank", lambda *_args, **_kwargs: events.append("restore") or json.loads((bank / "manifest.json").read_text()))
    monkeypatch.setattr(campaign, "_clone_case_source", lambda _url, name, _identity: source_url.rsplit("/", 1)[0] + "/" + name)
    monkeypatch.setattr(campaign, "_verify_case_bank_seal", lambda *_args: None)
    monkeypatch.setattr(campaign, "_database_key_count", lambda url: 0 if url == source_url else 1)
    monkeypatch.setattr(campaign, "_drop_local_database", lambda url: events.append(("drop", url)))
    monkeypatch.setattr(campaign, "_execute_case_row", lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic row failure")))
    with pytest.raises(RuntimeError, match="synthetic row failure"):
        campaign._run_case(tmp_path, tmp_path, output, case_id, manifest)
    assert events[0:2] == ["recover", "restore"]
    assert len([event for event in events if isinstance(event, tuple) and event[0] == "drop"]) == 1


def test_case_gate_rejects_non_operational_pair_before_next_case(tmp_path: Path) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    case_id = manifest["run_order"]["case_order"][0]
    rows = [
        row for row in campaign.expanded_run_order(manifest)
        if row["question_id"] == case_id
    ]
    for row in rows:
        row_dir = tmp_path / row["row_id"]
        row_dir.mkdir()
        campaign.atomic_write_json(row_dir / "row-proof.json", {
            "complete": True,
            "execution_complete": True,
            "treatment_operational": row["arm"] == "fast",
            "outcome": "success" if row["arm"] == "fast" else "operational_failure",
            "row": row,
        })

    with pytest.raises(RuntimeError, match="non-operational pair"):
        campaign._require_operational_case_rows(tmp_path, rows)


def test_run_campaign_uses_one_scratch_lifecycle_per_case(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    manifest["protocol"]["deep_prompt_sha256"] = campaign.sha256_file(
        campaign.ROOT / "config/deep-recall-v1.txt"
    )
    directory = tmp_path / "dataset"
    materialized = tmp_path / "materialized"
    output = tmp_path / "root"
    directory.mkdir()
    materialized.mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "judge-secret")
    monkeypatch.setattr(campaign, "preflight", lambda *_args: {
        "materialization": {"cases": {
            case_id: {"case_id": case_id}
            for case_id in manifest["run_order"]["case_order"]
        }},
        "python": {"packages_sha256": "p" * 64},
    })
    monkeypatch.setattr(campaign, "verify_endpoint_inventory", lambda _manifest: {})
    monkeypatch.setattr(campaign, "_resolve_archive_tools", lambda _url: {
        "server_major": 17,
        "pg_dump": {"binary": "/pg_dump", "major": 17, "server_major": 17},
        "pg_restore": {"binary": "/pg_restore", "major": 17, "server_major": 17},
    })
    monkeypatch.setattr(
        campaign,
        "_fingerprint",
        lambda path: {"path": str(path), "bytes": 1, "sha256": "f" * 64},
    )
    run_calls = []

    def run(command, **_kwargs):
        run_calls.append(command)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return campaign.subprocess.CompletedProcess(command, 0, "commit", "")
        # The P0.2 liveness preflight probes the base DB with `select 1`; the
        # stubbed server answers "1" so run_campaign proceeds past the guard.
        if command[-2:] == ["-tAc", "select 1"] or "select 1" in command:
            return campaign.subprocess.CompletedProcess(command, 0, "1", "")
        return campaign.subprocess.CompletedProcess(command, 0, "", "")

    case_commands = []

    class Process:
        def __init__(self, command, **_kwargs):
            case_commands.append(command)
            self.pid = 4321

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(campaign.subprocess, "run", run)
    monkeypatch.setattr(campaign.subprocess, "Popen", Process)
    result = campaign.run_campaign(
        directory, materialized, output,
        "postgres://bench:secret@127.0.0.1:5432/memphant", manifest,
    )
    assert result["rows"] == 24
    assert len(case_commands) == 12
    assert all("_run-case" in command and "_run-row" not in command for command in case_commands)
    assert [command[command.index("--case-id") + 1] for command in case_commands] == manifest["run_order"]["case_order"]


def test_run_campaign_rejects_remote_base_before_tools_or_helpers(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "judge-secret")
    reached = []
    monkeypatch.setattr(
        campaign, "_resolve_archive_tools",
        lambda _url: reached.append("resolver") or pytest.fail("resolver reached"),
    )
    monkeypatch.setattr(
        campaign, "preflight",
        lambda *_args: reached.append("preflight") or pytest.fail("preflight reached"),
    )
    monkeypatch.setattr(
        campaign.subprocess, "run",
        lambda *_args, **_kwargs: reached.append("subprocess") or pytest.fail("subprocess reached"),
    )
    monkeypatch.setattr(
        campaign.subprocess, "Popen",
        lambda *_args, **_kwargs: reached.append("helper") or pytest.fail("helper reached"),
    )
    with pytest.raises(RuntimeError, match="plain local PostgreSQL"):
        campaign.run_campaign(
            tmp_path, tmp_path, tmp_path / "output",
            "postgres://bench:secret@db.example.com:5432/memphant", manifest,
        )
    assert reached == []


def test_archive_tools_resolve_matching_major_before_construction(monkeypatch) -> None:
    campaign = _load()
    source = "postgres://bench:secret@127.0.0.1:5432/memphant_scratch_1_2"
    monkeypatch.setattr(campaign, "_postgres_server_major", lambda _url: 17)
    monkeypatch.setattr(campaign, "_archive_tool_candidates", lambda name, major: [
        f"/usr/bin/{name}", f"/opt/homebrew/opt/postgresql@{major}/bin/{name}",
    ])

    def identity(binary, _url):
        major = 17 if "postgresql@17" in binary else 14
        return {"binary": binary, "version": f"PostgreSQL {major}", "major": major, "server_major": 17}

    monkeypatch.setattr(campaign, "_postgres_tool_identity", identity)
    tools = campaign._resolve_archive_tools(source)
    assert tools["server_major"] == 17
    assert tools["pg_dump"]["binary"] == "/opt/homebrew/opt/postgresql@17/bin/pg_dump"
    assert tools["pg_restore"]["binary"] == "/opt/homebrew/opt/postgresql@17/bin/pg_restore"


def test_case_lease_rejects_concurrent_resume_before_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    monkeypatch.setattr(
        campaign, "_run_case_locked",
        lambda *_args: pytest.fail("concurrent resume reached orphan recovery"),
    )
    with campaign._case_lease(tmp_path, "19367bc7"):
        with pytest.raises(RuntimeError, match="case is already active"):
            campaign._run_case(
                tmp_path, tmp_path, tmp_path, "19367bc7", {}
            )


def test_completed_fast_row_rejects_coherent_case_bank_rewrite(tmp_path: Path) -> None:
    campaign = _load()
    row = {"question_id": "19367bc7", "arm": "fast", "row_id": "0001-fast-19367bc7"}
    row_dir = tmp_path / row["row_id"]
    row_dir.mkdir()
    old_manifest = tmp_path / "old-manifest.json"
    campaign.atomic_write_json(old_manifest, {
        "archive_sha256": "a" * 64,
        "logical_identity": {"sha256": "e" * 64},
        "construction_proof_sha256": "c" * 64,
        "case_contract_sha256": "f" * 64,
    })
    old_seal = campaign._case_bank_seal(old_manifest)
    campaign.atomic_write_json(row_dir / "case-bank-seal.json", old_seal)
    campaign.atomic_write_json(row_dir / "row-proof.json", {
        "complete": True,
        "row": row,
        "case_bank_seal_sha256": old_seal["seal_sha256"],
        "artifact_hashes": campaign.artifact_hashes(row_dir),
    })
    replacement = tmp_path / "replacement-manifest.json"
    campaign.atomic_write_json(replacement, {
        "archive_sha256": "b" * 64,
        "logical_identity": {"sha256": "e" * 64},
        "construction_proof_sha256": "d" * 64,
        "case_contract_sha256": "f" * 64,
    })
    with pytest.raises(RuntimeError, match="case bank seal drift"):
        campaign._validate_completed_case_row(
            tmp_path, row, campaign._case_bank_seal(replacement)
        )


def test_execution_paths_are_absolute_before_official_cwd_changes(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    monkeypatch.chdir(tmp_path)
    directory, materialized, output = campaign._resolve_execution_paths(
        Path("official"), Path("materialized"), Path("artifacts")
    )
    assert directory == tmp_path / "official"
    assert materialized == tmp_path / "materialized"
    assert output == tmp_path / "artifacts"
    assert all(path.is_absolute() for path in (directory, materialized, output))


def test_campaign_packages_production_release_binaries() -> None:
    campaign = _load()
    assert campaign.PRODUCTION_BINARY_PROFILE == "release"
    assert campaign._production_build_command() == [
        "cargo", "build", "--release", "-p", "memphant-server",
        "-p", "memphant-worker", "-p", "memphant-cli",
    ]
    for name in ("server", "worker", "cli"):
        assert campaign._binary_path(name) == (
            campaign.ROOT / "target" / "release" / f"memphant-{name}"
        )
    with pytest.raises(RuntimeError, match="unknown packaged binary"):
        campaign._binary_path("debug-helper")


def test_fast_and_deep_configs_differ_only_by_mode(tmp_path: Path) -> None:
    campaign = _load()
    base = json.loads(
        (ROOT / "benchmarks/longmemeval_v2/memphant.memory.json").read_text()
    )
    fast = campaign.write_memory_config(base, "fast", tmp_path / "fast.json")
    deep = campaign.write_memory_config(base, "deep", tmp_path / "deep.json")
    assert fast["memory_params"]["mode"] == "fast"
    assert deep["memory_params"]["mode"] == "deep"
    fast["memory_params"]["mode"] = "deep"
    assert fast == deep


def test_percentiles_use_preregistered_nearest_rank_for_n12() -> None:
    campaign = _load()
    values = list(range(1, 13))
    assert campaign._percentile(values, 0.50) == 6
    assert campaign._percentile(values, 0.95) == 12


def test_context_preflight_contract_rejects_empty_or_exact_token_overflow() -> None:
    campaign = _load()
    public = {"trace": {"token_estimate": 30_000}}
    with pytest.raises(RuntimeError, match="non-empty memory context"):
        campaign._context_contract_audit([], public, 0, 32_768)
    context = [{"type": "text", "value": "bounded evidence"}]
    with pytest.raises(RuntimeError, match="exact reader token budget"):
        campaign._context_contract_audit(context, public, 32_769, 32_768)
    audit = campaign._context_contract_audit(context, public, 31_000, 32_768)
    assert audit == {
        "context_items": 1,
        "runtime_token_estimate": 30_000,
        "exact_reader_tokens": 31_000,
        "budget_tokens": 32_768,
        "nonempty": True,
        "untruncated": True,
    }


def test_reader_route_probe_request_is_tiny_reasoning_enabled_and_pinned() -> None:
    campaign = _load()
    request = campaign._reader_route_probe_request()
    assert request == {
        "model": "Qwen/Qwen3.5-9B",
        "messages": [{
            "role": "user",
            "content": "Reply with exactly ROUTE_OK after reasoning internally.",
        }],
        "max_tokens": 64,
        "reasoning": {"enabled": True},
        "temperature": 0,
    }


def test_context_preflight_streams_only_selected_trajectories(tmp_path: Path) -> None:
    campaign = _load()
    source = tmp_path / "trajectories.jsonl"
    source.write_text(
        '\n'.join(json.dumps({"id": value, "payload": value * 10}, separators=(",", ":"))
                  for value in ("ignored", "wanted-b", "wanted-a")) + '\n'
    )
    selected = campaign._load_selected_trajectories(
        source, ["wanted-a", "wanted-b"]
    )
    assert set(selected) == {"wanted-a", "wanted-b"}
    assert selected["wanted-a"]["payload"] == "wanted-a" * 10
    with pytest.raises(RuntimeError, match="contains duplicates"):
        campaign._load_selected_trajectories(source, ["wanted-a", "wanted-a"])
    with pytest.raises(RuntimeError, match="are incomplete"):
        campaign._load_selected_trajectories(source, ["missing"])


def test_temporary_adapter_environment_restores_existing_and_missing_values(
    monkeypatch,
) -> None:
    campaign = _load()
    monkeypatch.setenv("MEMPHANT_TEST_EXISTING", "before")
    monkeypatch.delenv("MEMPHANT_TEST_MISSING", raising=False)
    with campaign._temporary_environment({
        "MEMPHANT_TEST_EXISTING": "during",
        "MEMPHANT_TEST_MISSING": "temporary",
    }):
        assert campaign.os.environ["MEMPHANT_TEST_EXISTING"] == "during"
        assert campaign.os.environ["MEMPHANT_TEST_MISSING"] == "temporary"
    assert campaign.os.environ["MEMPHANT_TEST_EXISTING"] == "before"
    assert "MEMPHANT_TEST_MISSING" not in campaign.os.environ


def test_trajectory_fragmentation_preserves_semantic_state_boundaries(monkeypatch) -> None:
    adapter = _load_memory_adapter(monkeypatch)
    trajectory = {
        "id": "t1", "goal": "ship", "outcome": "done",
        "states": [
            {"url": "https://one", "action": "open", "text": "A" * 60},
            {"url": "https://two", "action": "close", "text": "B" * 60},
        ],
    }
    blocks = [adapter._state_body(trajectory, state, index) for index, state in enumerate(trajectory["states"])]
    fragments = adapter._trajectory_fragments(trajectory, max(len(block.encode()) for block in blocks) + 1)
    assert fragments == blocks
    assert "\n\n---\n\n".join(fragments) == adapter._trajectory_body(trajectory)


def test_trajectory_fragmentation_losslessly_bounds_oversized_single_lines(monkeypatch) -> None:
    adapter = _load_memory_adapter(monkeypatch)
    trajectory = {
        "id": "t-long", "goal": "find outlook", "outcome": None,
        "states": [{"url": "https://one", "text": "Outlook," * 200}],
    }
    body = adapter._state_body(trajectory, trajectory["states"][0], 0)
    fragments = adapter._trajectory_fragments(trajectory, 128)
    assert len(fragments) > 1
    assert all(len(fragment.encode()) <= 128 for fragment in fragments)
    assert "".join(fragments) == body


def test_mutation_idempotency_keys_are_deterministic_and_domain_separated(monkeypatch) -> None:
    adapter = _load_memory_adapter(monkeypatch)
    payload = {"same": "body"}
    first = adapter._idempotency_key("POST", "/v1/episodes", payload)
    assert first == adapter._idempotency_key("POST", "/v1/episodes", payload)
    assert first != adapter._idempotency_key("PUT", "/v1/episodes", payload)
    assert first != adapter._idempotency_key("POST", "/v1/reflect", payload)


def test_manifest_rejects_order_and_spend_ceiling_drift() -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    manifest["run_order"]["case_order"] = list(reversed(manifest["run_order"]["case_order"]))
    with pytest.raises(RuntimeError, match="case-major order drift"):
        campaign.verify_campaign_manifest(manifest)
    manifest = campaign.load_campaign_manifest()
    manifest["campaign_spend"]["deep_max_liability_usd"] = 10.9
    with pytest.raises(RuntimeError, match="Deep campaign reserve drift"):
        campaign.verify_campaign_manifest(manifest)


def test_material_endpoint_predicate_ignores_additive_inventory_drift() -> None:
    campaign = _load()
    contract = {
        "name": "Azure | exact-model-20260709", "model_id": "exact-model",
        "provider_name": "Azure", "min_context_length": 100000,
        "min_completion_tokens": 4096,
        "required_parameters": ["tools", "tool_choice", "max_completion_tokens"],
        "prompt_price_micros_per_million_max": 2_000_000,
        "completion_price_micros_per_million_max": 10_000_000,
    }
    endpoint = {
        "name": contract["name"], "model_id": contract["model_id"],
        "provider_name": "Azure", "tag": "new-region", "quantization": "unknown",
        "context_length": 1_000_000, "max_completion_tokens": 128_000,
        "max_prompt_tokens": None,
        "supported_parameters": ["tools", "tool_choice", "max_completion_tokens", "new_parameter"],
        "pricing": {"prompt": "0.000002", "completion": "0.00001"},
        "name_not_in_contract": "additive metadata is harmless",
    }
    assert campaign._matching_endpoints([endpoint], contract) == [endpoint]
    endpoint["pricing"]["completion"] = "0.000010000001"
    assert campaign._matching_endpoints([endpoint], contract) == []


def test_resume_keeps_initial_inventory_evidence_when_material_contract_is_stable() -> None:
    campaign = _load()
    common = {
        "manifest_sha256": "a", "run_order_sha256": "b",
        "outputs_observed_before_freeze": False, "materialization": {"c": "d"},
        "git_commit": "e", "binaries": {"f": "g"}, "deep_prompt_sha256": "h",
        "deep_config_hashes": {"sonnet": "i"},
        "python_environment": {"packages_sha256": "p"},
        "environment_contract_sha256": "j",
        "binary_profile": "release",
        "archive_tools": {"server_major": 17},
        "preexisting_campaign_liability": {"total_micros": 320666},
        "selected_deep_arm": "sonnet",
        "memory_adapter_sha256": "adapter",
    }
    frozen = {**common, "endpoint_hashes": {
        "reader": {"inventory_sha256": "old", "material_contract_sha256": "stable"}
    }}
    current = {**common, "endpoint_hashes": {
        "reader": {"inventory_sha256": "new", "material_contract_sha256": "stable"}
    }}
    campaign.verify_resume_contract(frozen, current)
    current["endpoint_hashes"]["reader"]["material_contract_sha256"] = "drift"
    with pytest.raises(RuntimeError, match="material endpoint contract drift"):
        campaign.verify_resume_contract(frozen, current)


def test_decimal_cost_ceiling_never_rounds_liability_down() -> None:
    campaign = _load()
    assert campaign.usd_to_micros("0.0000001") == 1
    assert campaign.usd_to_micros("0.001234000001") == 1235
    assert campaign.token_price_to_micros_per_million("0.00000015") == 150000


def test_fresh_reservations_plus_prior_attempts_stay_below_campaign_ceiling() -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    reservations = [
        campaign._reservation(row, manifest)
        for row in campaign.expanded_run_order(manifest)
    ]
    fresh = sum(item["max_liability_micros"] for item in reservations)
    prior = manifest["campaign_spend"]["preexisting_liability"]
    assert fresh == 5_697_600
    assert prior == {
        "settled_micros": 28_350,
        "unsettled_upper_bound_micros": 316_142,
        "total_micros": 344_492,
        "proofs": prior["proofs"],
    }
    assert fresh + prior["total_micros"] == 6_042_092
    assert campaign.usd_to_micros(
        manifest["campaign_spend"]["hard_ceiling_usd"]
    ) - fresh - prior["total_micros"] == 207_908


def test_settled_proxy_cost_must_fit_its_pre_dispatch_reservation() -> None:
    campaign = _load()
    assert campaign._audit_cost({
        "audit_status": "settled",
        "max_liability_micros": 19,
        "total_cost": 0.0000116,
    }) == (12, 0)
    with pytest.raises(RuntimeError, match="exceeds its reservation"):
        campaign._audit_cost({
            "audit_status": "settled",
            "max_liability_micros": 11,
            "total_cost": 0.0000116,
        })


def test_reader_policy_enforces_frozen_bf16_and_price_caps_before_dispatch() -> None:
    campaign = _load()
    reader = campaign.load_campaign_manifest()["protocol"]["reader"]
    assert reader["provider_policy"] == {
        "only": ["deepinfra"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
        "quantizations": ["bf16"],
        "max_price": {"prompt": 0.1, "completion": 0.15},
    }


def test_clean_child_environment_drops_ambient_secrets_and_deep_overrides(
    monkeypatch,
) -> None:
    campaign = _load()
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    monkeypatch.setenv("UNRELATED_VENDOR_TOKEN", "must-not-cross")
    monkeypatch.setenv("MEMPHANT_DEEP_OPENROUTER_BASE_URL", "https://wrong.test/v1")
    monkeypatch.setenv("MEMPHANT_DEEP_MODEL", "wrong/model")
    monkeypatch.setenv("PATH", "/safe/bin")
    child = campaign._clean_environment({"EXPLICIT_VALUE": "allowed"})
    assert child["PATH"] == "/safe/bin"
    assert child["EXPLICIT_VALUE"] == "allowed"
    assert "AWS_SECRET_ACCESS_KEY" not in child
    assert "UNRELATED_VENDOR_TOKEN" not in child
    assert not any(key.startswith("MEMPHANT_DEEP") for key in child)


def test_python_harness_preflight_fails_closed_under_clean_environment(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    official = tmp_path / "official"
    official.mkdir()
    (official / "requirements.txt").write_text("openai-agents\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-cross")
    monkeypatch.setattr(
        campaign,
        "_fingerprint",
        lambda path: {"path": str(path), "bytes": 1, "sha256": "f" * 64},
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[2:4] == ["pip", "check"]:
            return campaign.subprocess.CompletedProcess(command, 0, "No broken requirements found.\n", "")
        if command[2:5] == ["pip", "freeze", "--all"]:
            return campaign.subprocess.CompletedProcess(
                command,
                0,
                "openai-agents==0.18.3\ntorch==2.13.0\ntorchvision==0.28.0\n",
                "",
            )
        return campaign.subprocess.CompletedProcess(
            command, 1, "", "ModuleNotFoundError: No module named 'agents'\n"
        )

    monkeypatch.setattr(campaign.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="official harness bootstrap import failed"):
        campaign.verify_python_harness(tmp_path)
    assert calls
    for _command, kwargs in calls:
        assert "OPENROUTER_API_KEY" not in kwargs["env"]


def test_python_harness_preflight_freezes_interpreter_and_packages(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    official = tmp_path / "official"
    official.mkdir()
    (official / "requirements.txt").write_text("openai-agents\n")
    monkeypatch.setattr(
        campaign,
        "_fingerprint",
        lambda path: {"path": str(path), "bytes": 1, "sha256": "f" * 64},
    )

    def run(command, **_kwargs):
        if command[2:4] == ["pip", "check"]:
            return campaign.subprocess.CompletedProcess(command, 0, "No broken requirements found.\n", "")
        if command[2:5] == ["pip", "freeze", "--all"]:
            return campaign.subprocess.CompletedProcess(
                command,
                0,
                "openai==2.46.0\nopenai-agents==0.18.3\n"
                "torch==2.13.0\ntorchvision==0.28.0\n",
                "",
            )
        return campaign.subprocess.CompletedProcess(command, 0, "usage: harness\n", "warning\n")

    monkeypatch.setattr(campaign.subprocess, "run", run)
    proof = campaign.verify_python_harness(tmp_path)
    assert proof["requirements_sha256"] == campaign.sha256_file(
        official / "requirements.txt"
    )
    assert proof["packages"] == [
        "openai-agents==0.18.3",
        "openai==2.46.0",
        "torch==2.13.0",
        "torchvision==0.28.0",
    ]
    assert proof["packages_sha256"] == campaign.canonical_sha256(proof["packages"])
    assert proof["bootstrap_import_verified"] is True


def test_python_harness_preflight_executes_real_qwen_processor_path(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    official = tmp_path / "official"
    official.mkdir()
    (official / "requirements.txt").write_text("transformers\n")
    campaign_requirements = tmp_path / "requirements-p1-t6.txt"
    campaign_requirements.write_text("torch==2.13.0\ntorchvision==0.28.0\n")
    processor_preflight = tmp_path / "processor_preflight.py"
    processor_preflight.write_text("raise SystemExit(0)\n")
    monkeypatch.setattr(campaign, "CAMPAIGN_PYTHON_REQUIREMENTS", campaign_requirements)
    monkeypatch.setattr(campaign, "PROCESSOR_PREFLIGHT", processor_preflight)
    monkeypatch.setattr(
        campaign,
        "_fingerprint",
        lambda path: {"path": str(path), "bytes": 1, "sha256": "f" * 64},
    )
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[2:4] == ["pip", "check"]:
            return campaign.subprocess.CompletedProcess(command, 0, "No broken requirements found.\n", "")
        if command[2:5] == ["pip", "freeze", "--all"]:
            return campaign.subprocess.CompletedProcess(
                command,
                0,
                "torch==2.13.0\ntorchvision==0.28.0\ntransformers==5.14.1\n",
                "",
            )
        return campaign.subprocess.CompletedProcess(command, 0, "processor-ready\n", "")

    monkeypatch.setattr(campaign.subprocess, "run", run)
    proof = campaign.verify_python_harness(tmp_path)
    assert [
        campaign.sys.executable,
        str(processor_preflight),
        "--official-dir",
        str(official),
    ] in calls
    assert proof["campaign_requirements_sha256"] == campaign.sha256_file(
        campaign_requirements
    )
    assert proof["processor_preflight_verified"] is True


def test_python_harness_preflight_rejects_missing_campaign_dependency(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    official = tmp_path / "official"
    official.mkdir()
    (official / "requirements.txt").write_text("transformers\n")
    campaign_requirements = tmp_path / "requirements-p1-t6.txt"
    campaign_requirements.write_text("torch==2.13.0\ntorchvision==0.28.0\n")
    monkeypatch.setattr(campaign, "CAMPAIGN_PYTHON_REQUIREMENTS", campaign_requirements)
    monkeypatch.setattr(
        campaign,
        "_fingerprint",
        lambda path: {"path": str(path), "bytes": 1, "sha256": "f" * 64},
    )

    def run(command, **_kwargs):
        if command[2:4] == ["pip", "check"]:
            return campaign.subprocess.CompletedProcess(command, 0, "", "")
        if command[2:5] == ["pip", "freeze", "--all"]:
            return campaign.subprocess.CompletedProcess(command, 0, "transformers==5.14.1\n", "")
        return campaign.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(campaign.subprocess, "run", run)
    with pytest.raises(
        RuntimeError,
        match="campaign Python dependency missing or drifted: torch==2.13.0",
    ):
        campaign.verify_python_harness(tmp_path)


def test_processor_preflight_executes_official_token_counter(tmp_path: Path) -> None:
    official = tmp_path / "official"
    evaluation = official / "evaluation"
    evaluation.mkdir(parents=True)
    (evaluation / "__init__.py").write_text("")
    (evaluation / "harness.py").write_text(
        "def count_memory_context_tokens(memory_context, loaded_images):\n"
        "    assert memory_context == "
        "[{'type': 'text', 'value': 'MemPhant processor preflight'}]\n"
        "    assert loaded_images == [None]\n"
        "    return 7\n"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "benchmarks/longmemeval_v2/processor_preflight.py"),
            "--official-dir",
            str(official),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "memory_context_tokens": 7,
        "processor_preflight": "passed",
    }


def test_secret_redaction_covers_nested_text_and_binary_artifacts(tmp_path: Path) -> None:
    campaign = _load()
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "stdout.log").write_text("prefix live-key suffix")
    (nested / "response.bin").write_bytes(b"before\x00live-key\x00after")
    campaign._redact_secrets(tmp_path, ["live-key"])
    assert "live-key" not in (tmp_path / "stdout.log").read_text()
    assert b"live-key" not in (nested / "response.bin").read_bytes()


def test_row_secret_values_redact_scratch_dsn_and_password_variants(tmp_path: Path) -> None:
    campaign = _load()
    database_url = "postgres://bench:sentinel%2Fpassword@db.test:5432/scratch"
    artifact = tmp_path / "server.stderr"
    artifact.write_text(
        f"dsn={database_url} password=sentinel/password "
        "authority=bench:sentinel%2Fpassword@db.test:5432"
    )
    campaign._redact_secrets(
        tmp_path,
        campaign._row_secret_values("router-key", "judge-key", database_url),
    )
    redacted = artifact.read_text()
    assert "sentinel/password" not in redacted
    assert "sentinel%2Fpassword" not in redacted
    assert database_url not in redacted


def test_forced_server_cleanup_reaps_child_before_artifact_redaction() -> None:
    campaign = _load()

    class Process:
        def __init__(self):
            self.events = []

        def terminate(self):
            self.events.append("terminate")

        def wait(self, timeout=None):
            self.events.append(("wait", timeout))
            if timeout is not None:
                raise campaign.subprocess.TimeoutExpired("server", timeout)
            return -9

        def kill(self):
            self.events.append("kill")

    process = Process()
    campaign._terminate_and_reap(process)
    assert process.events == [
        "terminate", ("wait", 10), "kill", ("wait", None),
    ]


def test_campaign_interrupt_terminates_and_reaps_scratch_process_group(
    monkeypatch,
) -> None:
    campaign = _load()
    signals = []
    monkeypatch.setattr(campaign.os, "killpg", lambda pid, signal: signals.append((pid, signal)))

    class Process:
        def __init__(self):
            self.events = []
            self.first_wait = True
            self.pid = 4321

        def wait(self, timeout=None):
            self.events.append(("wait", timeout))
            if self.first_wait:
                self.first_wait = False
                raise KeyboardInterrupt
            return -15

    process = Process()
    with pytest.raises(KeyboardInterrupt):
        campaign._wait_and_reap_on_interrupt(process)
    assert process.events == [("wait", None), ("wait", 10)]
    assert signals == [(4321, campaign.signal.SIGTERM)]


def test_official_harness_output_is_archived_per_row(tmp_path: Path) -> None:
    campaign = _load()
    completed = campaign._run_logged_harness(
        [
            sys.executable,
            "-c",
            "import sys; print('official-out'); print('official-err', file=sys.stderr)",
        ],
        cwd=tmp_path,
        environment=campaign._clean_environment(),
        row_dir=tmp_path,
    )
    assert completed.returncode == 0
    assert (tmp_path / "official.stdout").read_text() == "official-out\n"
    assert (tmp_path / "official.stderr").read_text() == "official-err\n"


def test_deep_receipts_must_exactly_reconcile_ids_route_tokens_and_cost(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    row = next(
        item for item in campaign.expanded_run_order(manifest) if item["arm"] == "sonnet"
    )
    reservation = campaign._reservation(row, manifest)
    (tmp_path / "memory-proofs").mkdir()
    candidate = manifest["protocol"]["deep_candidates"]["sonnet"]
    deep = {
        "generation_ids": ["gen-1"],
        "usage": {
            "context_tokens": 10,
            "spend_micros": 1_000,
            "unsettled_context_tokens_upper_bound": 0,
            "unsettled_spend_micros_upper_bound": 0,
        },
    }
    campaign.atomic_write_json(
        tmp_path / "memory-proofs/proof.json",
        {"public": {"recall_response": {"deep": deep}}},
    )
    receipt = {
        "audit_status": "settled",
        "generation_ids": ["gen-1"],
        "receipts": [{
            "id": "gen-1",
            "provider_name": "Azure",
            "model": candidate["model"],
            "tokens_prompt": 10,
            "tokens_completion": 2,
            "total_cost_micros": 1_000,
        }],
    }
    campaign.atomic_write_json(tmp_path / "deep-generation-receipts.json", receipt)
    settlement = campaign._row_settlement(
        tmp_path, row, reservation, orphaned=False
    )
    assert settlement["deep_settled_micros"] == 1_000
    assert settlement["deep_unsettled_upper_bound_micros"] == 0

    receipt["receipts"][0]["total_cost_micros"] = 999
    campaign.atomic_write_json(tmp_path / "deep-generation-receipts.json", receipt)
    settlement = campaign._row_settlement(
        tmp_path, row, reservation, orphaned=False
    )
    assert settlement["deep_settled_micros"] == 0
    assert settlement["deep_unsettled_upper_bound_micros"] == reservation[
        "deep_hard_cap_micros"
    ]


def test_late_deep_receipt_settles_truthful_runtime_reservation(tmp_path: Path) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    row = next(
        item for item in campaign.expanded_run_order(manifest) if item["arm"] == "sonnet"
    )
    candidate = manifest["protocol"]["deep_candidates"]["sonnet"]
    reservation = campaign._reservation(row, manifest)
    (tmp_path / "memory-proofs").mkdir()
    campaign.atomic_write_json(tmp_path / "memory-proofs/proof.json", {
        "public": {"recall_response": {"deep": {
            "generation_ids": ["gen-late"],
            "usage": {
                "context_tokens": 0,
                "spend_micros": 0,
                "unsettled_context_tokens_upper_bound": 6_525,
                "unsettled_spend_micros_upper_bound": 45_818,
            },
        }}},
    })
    campaign.atomic_write_json(tmp_path / "deep-generation-receipts.json", {
        "audit_status": "settled",
        "generation_ids": ["gen-late"],
        "receipts": [{
            "id": "gen-late",
            "provider_name": "Azure",
            "model": candidate["model"],
            "tokens_prompt": 512,
            "tokens_completion": 22,
            "total_cost_micros": 3_380,
        }],
    })

    settlement = campaign._row_settlement(
        tmp_path, row, reservation, orphaned=False
    )

    assert settlement["deep_settled_micros"] == 3_380
    assert settlement["deep_unsettled_upper_bound_micros"] == 0

    receipt_path = tmp_path / "deep-generation-receipts.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["receipts"][0]["total_cost_micros"] = 45_819
    campaign.atomic_write_json(receipt_path, receipt)
    settlement = campaign._row_settlement(
        tmp_path, row, reservation, orphaned=False
    )
    assert settlement["deep_settled_micros"] == 0
    assert settlement["deep_unsettled_upper_bound_micros"] == reservation[
        "deep_hard_cap_micros"
    ]


def test_manifest_binds_all_candidate_metadata_to_runtime_config_hashes() -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    protocol = manifest["protocol"]
    assert protocol["selected_deep_arm"] == "sonnet"
    assert {
        name: campaign._expected_deep_config_hash(candidate)
        for name, candidate in protocol["deep_candidates"].items()
    } == {
        name: candidate["config_sha256"]
        for name, candidate in protocol["deep_candidates"].items()
    }
    protocol["deep_candidates"]["luna"]["config_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="Deep runtime config hash drift: luna"):
        campaign.verify_campaign_manifest(manifest)


def test_deep_receipt_archive_is_sanitized_and_exact(tmp_path: Path, monkeypatch) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    row = next(
        item for item in campaign.expanded_run_order(manifest) if item["arm"] == "sonnet"
    )
    candidate = manifest["protocol"]["deep_candidates"]["sonnet"]
    (tmp_path / "memory-proofs").mkdir()
    campaign.atomic_write_json(tmp_path / "memory-proofs/proof.json", {
        "public": {"recall_response": {"deep": {
            "generation_ids": ["gen-1"],
            "usage": {
                "context_tokens": 20,
                "spend_micros": 1_235,
                "unsettled_context_tokens_upper_bound": 0,
                "unsettled_spend_micros_upper_bound": 0,
            },
        }}},
    })
    monkeypatch.setattr(campaign, "_json_url", lambda *_args: {"data": {
        "id": "gen-1",
        "provider_name": "Azure",
        "model": candidate["model"],
        "tokens_prompt": 20,
        "tokens_completion": 3,
        "total_cost": "0.001234000001",
        "prompt": "must not be archived",
        "upstream_secret": "must not be archived",
    }})
    campaign._archive_deep_generation_receipts(
        tmp_path, row, manifest, "secret-key"
    )
    receipt = json.loads((tmp_path / "deep-generation-receipts.json").read_text())
    assert receipt["audit_status"] == "settled"
    assert receipt["receipts"] == [{
        "id": "gen-1",
        "provider_name": "Azure",
        "model": candidate["model"],
        "tokens_prompt": 20,
        "tokens_completion": 3,
        "total_cost_micros": 1_235,
    }]
    archived = json.dumps(receipt)
    assert "must not be archived" not in archived
    assert "upstream_secret" not in archived
    assert "secret-key" not in archived


def test_synthetic_all_failure_aggregate_is_complete_and_zero_scored(tmp_path: Path) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_root(campaign, tmp_path, manifest)
    ledger = tmp_path / "spend-ledger"
    ledger.mkdir()
    for row in rows:
        reservation_path = ledger / f"{row['sequence']:04d}.json"
        campaign.atomic_write_json(reservation_path, campaign._reservation(row, manifest))
        row_dir = tmp_path / row["row_id"]
        row_dir.mkdir()
        campaign.atomic_write_json(row_dir / "failure.json", {"reason": "synthetic"})
        campaign._write_row_proof(
            row_dir, row, reservation_path, "operational_failure",
            {"failure_reason": "synthetic"}, orphaned=True,
        )
    _write_synthetic_case_banks(campaign, tmp_path, rows)
    aggregate = campaign.aggregate_campaign(tmp_path, manifest)
    assert aggregate["decision"] == "retire_deep_product_code"
    assert aggregate["advance_to_separate_confirmation"] == []
    assert set(aggregate["candidates"]) == {"sonnet"}
    assert all(not candidate["feasible"] for candidate in aggregate["candidates"].values())
    assert all(
        pair["deep_score"] == 0.0
        for candidate in aggregate["candidates"].values()
        for pair in candidate["pairs"]
    )


def _synthetic_campaign_memory(
    campaign, row: dict, manifest: dict, bank: dict,
) -> dict:
    case_id = row["question_id"]
    mode = "fast" if row["arm"] == "fast" else "deep"
    instance_id = f"{row['sequence']:032x}"
    trace_id = f"trace-{row['row_id']}"
    construction = bank["construction"]
    context = construction["isolation"]["context"]
    item = {
        "unit_id": f"unit-{row['row_id']}",
        "body": f"context for {row['row_id']}",
        "kind": "document",
        "derived_by": "fixture",
        "inclusion_reason": "ranked",
        "citation_episode_id": None,
        "citation_resource_id": f"resource-{row['row_id']}",
        "derived_from_unit_ids": [],
        "suppression_labels": [],
    }
    citation = {
        "unit_id": item["unit_id"],
        "episode_id": item["citation_episode_id"],
        "resource_id": item["citation_resource_id"],
        "derived_from_unit_ids": item["derived_from_unit_ids"],
    }
    deep = None
    trace = {
        "id": trace_id,
        "tenant_id": construction["isolation"]["tenant_id"],
        "scope_id": context["scope_id"],
        "actor_id": context["actor_id"],
        "query_hash": campaign.canonical_sha256({"question_id": case_id}),
        "mode_requested": mode,
        "mode_executed": mode,
        "context_items": [item],
        "citations": [citation],
        "deep": None,
    }
    if mode == "deep":
        candidate = manifest["protocol"]["deep_candidates"][row["arm"]]
        deep = {
            "status": "completed", "stop_reason": "completed",
            "generation_ids": [f"generation-{row['row_id']}"],
            "usage": {
                "context_tokens": 10, "spend_micros": 1000,
                "unsettled_spend_micros_upper_bound": 0,
                "unsettled_context_tokens_upper_bound": 0,
            },
        }
        trace.update({
            "deep": deep, "l4_model": candidate["model"],
            "l4_provider": "azure", "l4_observed_provider": "Azure",
            "l4_observed_model": candidate["model"],
            "l4_prompt_hash": manifest["protocol"]["deep_prompt_sha256"],
            "l4_config_hash": candidate["config_sha256"],
        })
    response = {
        "trace_id": trace_id,
        "items": [item],
        "citations": [citation],
        "degraded": False,
        "deep": deep,
    }
    memory_contract = bank["case_contract"]["memory_contracts"][mode]
    before = {
        "resource": {"rows": 670, "content_md5": "resource"},
        "retrieval_trace": {"rows": 0, "content_md5": "before"},
    }
    after = {
        "resource": dict(before["resource"]),
        "retrieval_trace": {"rows": 1, "content_md5": "after"},
    }
    return {
        "contract": {
            "adapter_sha256": construction["contract"]["adapter_sha256"],
            "memory_params_sha256": memory_contract["memory_params_sha256"],
            "top_k": memory_contract["top_k"],
            "budget_tokens": memory_contract["budget_tokens"],
            "mode": mode,
            "recall_request_timeout_seconds": memory_contract[
                "recall_request_timeout_seconds"
            ],
            "binaries": construction["contract"]["binaries"],
            "gold_fields_consumed": [],
        },
        "isolation": {
            "tenant_id": construction["isolation"]["tenant_id"],
            "scope_id": context["scope_id"],
            "actor_id": context["actor_id"],
            "instance_id": instance_id,
        },
        "public": {"recall_response": response, "trace": trace},
        "recall_mutation_proof": {
            "before": before, "after": after,
            "changed_tables": ["retrieval_trace"],
            "allowed_audit_rows_added": 1,
            "corpus_policy_job_tables_unchanged": True,
        },
        "query": {
            "question_id": case_id,
            "query_sha256": campaign.canonical_sha256({"question": case_id}),
            "query_image_present": False,
            "native_query_hash": trace["query_hash"],
            "recall_request_sha256": campaign.canonical_sha256({
                "question_id": case_id, "mode": mode,
            }),
            "recall_response_sha256": campaign.canonical_sha256(response),
            "trace_id": trace_id,
            "trace_sha256": campaign.canonical_sha256(trace),
            "context_sha256": campaign.canonical_sha256([
                {"type": "text", "value": item["body"]},
            ]),
            "recall_duration_ms": 1000,
            "construction_proof_sha256": bank["construction_proof_sha256"],
            "query_only": True,
        },
        "pairing": {
            "trajectory_count": 500,
            "resource_count": 670,
            "worker": construction["pairing"]["worker"],
            "construction_proof_sha256": bank["construction_proof_sha256"],
            "query_only": True,
        },
    }


def _write_synthetic_success_campaign(
    campaign, tmp_path: Path, manifest: dict, rows: list[dict]
) -> None:
    _write_synthetic_root(campaign, tmp_path, manifest)
    ledger = tmp_path / "spend-ledger"
    ledger.mkdir()
    for row in rows:
        reservation_path = ledger / f"{row['sequence']:04d}.json"
        campaign.atomic_write_json(reservation_path, campaign._reservation(row, manifest))
        row_dir = tmp_path / row["row_id"]
        (row_dir / "memory-proofs").mkdir(parents=True)
        deep = None
        trace = {"id": "trace", "deep": None}
        if row["arm"] != "fast":
            candidate = manifest["protocol"]["deep_candidates"][row["arm"]]
            deep = {
                "status": "completed", "stop_reason": "completed",
                "generation_ids": [f"generation-{row['row_id']}"],
                "usage": {"context_tokens": 10, "spend_micros": 1000,
                          "unsettled_spend_micros_upper_bound": 0,
                          "unsettled_context_tokens_upper_bound": 0},
            }
            trace.update({
                "deep": deep, "l4_model": candidate["model"], "l4_provider": "azure",
                "l4_observed_provider": "Azure", "l4_observed_model": candidate["model"],
                "l4_prompt_hash": manifest["protocol"]["deep_prompt_sha256"],
                "l4_config_hash": candidate["config_sha256"],
            })
        memory = {
            "contract": {"adapter_sha256": "a" * 64},
            "isolation": {"tenant_id": "tenant"},
            "public": {"recall_response": {"trace_id": "trace", "deep": deep}, "trace": trace},
            "recall_mutation_proof": {"corpus_policy_job_tables_unchanged": True},
            "query": {
                "recall_duration_ms": 1000,
                "construction_proof_sha256": "filled-after-bank-write",
                "query_only": True,
            },
            "pairing": {
                "trajectory_count": 500,
                "resource_count": 670,
                "worker": {
                    "completed_sources": 670,
                    "stdout_sha256": "1" * 64,
                    "stderr_sha256": "2" * 64,
                },
                "construction_proof_sha256": "filled-after-bank-write",
                "query_only": True,
            },
        }
        memory_path = row_dir / "memory-proofs/proof.json"
        campaign.atomic_write_json(memory_path, memory)
        if deep is not None:
            campaign.atomic_write_json(row_dir / "deep-generation-receipts.json", {
                "audit_status": "settled",
                "generation_ids": deep["generation_ids"],
                "receipts": [{
                    "id": deep["generation_ids"][0],
                    "provider_name": "Azure",
                    "model": candidate["model"],
                    "tokens_prompt": 10,
                    "tokens_completion": 2,
                    "total_cost_micros": 1000,
                }],
            })
        campaign.atomic_write_json(row_dir / "reader-route.json", {
            "audit_status": "settled", "max_liability_micros": 5000,
            "total_cost": "0.001", "provider_name": "DeepInfra",
            "model": "qwen/qwen3.5-9b",
            "provider_policy_sha256": campaign.canonical_sha256(
                manifest["protocol"]["reader"]["provider_policy"]
            ),
        })
        (row_dir / "judge-routes").mkdir()
        (row_dir / "official").mkdir()
        score_path = row_dir / "official/per_question.jsonl"
        score_path.write_text(json.dumps({
            "question_id": row["question_id"], "eval_function": "mc_choice_match",
            "score": 0.0 if row["arm"] == "fast" else 1.0,
            "memory_context_was_truncated": False,
        }) + "\n")
        campaign._write_row_proof(row_dir, row, reservation_path, "success", {
            "execution_complete": True, "treatment_operational": True,
            "binaries": json.loads((tmp_path / "pre-execution-proof.json").read_text())["binaries"],
            "memory_proof_sha256": campaign.sha256_file(memory_path),
            "reader_route_sha256": campaign.sha256_file(row_dir / "reader-route.json"),
            "judge_route_sha256": campaign.canonical_sha256([]),
            "official_score_sha256": campaign.sha256_file(score_path),
        })
    _write_synthetic_case_banks(campaign, tmp_path, rows)
    for row in rows:
        bank = json.loads(
            (tmp_path / "case-banks" / row["question_id"] / "manifest.json").read_text()
        )
        memory_path = tmp_path / row["row_id"] / "memory-proofs/proof.json"
        memory = _synthetic_campaign_memory(campaign, row, manifest, bank)
        campaign.atomic_write_json(memory_path, memory)
        row_dir = tmp_path / row["row_id"]
        proof_path = row_dir / "row-proof.json"
        proof = json.loads(proof_path.read_text())
        proof["memory_proof_sha256"] = campaign.sha256_file(memory_path)
        proof["artifact_hashes"] = campaign.artifact_hashes(
            row_dir, exclude={"row-proof.json"}
        )
        campaign.atomic_write_json(proof_path, proof)
    _refresh_synthetic_case_bank_retirements(campaign, tmp_path, rows)


def test_synthetic_success_aggregate_applies_registered_ranking(tmp_path: Path) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    aggregate = campaign.aggregate_campaign(tmp_path, manifest)
    assert all(candidate["feasible"] for candidate in aggregate["candidates"].values())
    assert all(candidate["predicates"]["no_context_truncation"]
               for candidate in aggregate["candidates"].values())
    assert set(aggregate["candidates"]) == {"sonnet"}
    assert aggregate["advance_to_separate_confirmation"] == ["sonnet"]
    assert aggregate["decision"] == "confirmation_manifest_required"


def _rewrite_synthetic_memory_binding(
    campaign, output: Path, rows: list[dict], row: dict, memory: dict,
    *, outcome: str = "success",
) -> None:
    row_dir = output / row["row_id"]
    memory_path = row_dir / "memory-proofs/proof.json"
    campaign.atomic_write_json(memory_path, memory)
    proof_path = row_dir / "row-proof.json"
    proof = json.loads(proof_path.read_text())
    proof["outcome"] = outcome
    proof["operational"] = outcome == "success"
    proof["memory_proof_sha256"] = campaign.sha256_file(memory_path)
    proof["artifact_hashes"] = campaign.artifact_hashes(
        row_dir, exclude={"row-proof.json"}
    )
    campaign.atomic_write_json(proof_path, proof)
    _refresh_synthetic_case_bank_retirements(campaign, output, rows)


@pytest.mark.parametrize("outcome", ["success", "operational_failure"])
@pytest.mark.parametrize(
    "tamper,message",
    [
        ("tenant", "isolation"),
        ("context", "isolation"),
        ("adapter", "adapter or binary contract"),
        ("mode", "row memory contract"),
        ("question", "question binding"),
        ("trace", "trace and response binding"),
        ("citations", "citation and context binding"),
        ("mutation", "allowed mutation contract"),
    ],
)
def test_aggregate_authenticates_memory_proof_for_success_and_failure_rows(
    tmp_path: Path, outcome: str, tamper: str, message: str,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    row = rows[0]
    memory_path = tmp_path / row["row_id"] / "memory-proofs/proof.json"
    memory = json.loads(memory_path.read_text())
    if tamper == "tenant":
        memory["isolation"]["tenant_id"] = "wrong-tenant"
        memory["public"]["trace"]["tenant_id"] = "wrong-tenant"
    elif tamper == "context":
        memory["isolation"]["scope_id"] = "wrong-scope"
        memory["public"]["trace"]["scope_id"] = "wrong-scope"
    elif tamper == "adapter":
        memory["contract"]["adapter_sha256"] = "f" * 64
    elif tamper == "mode":
        memory["contract"]["mode"] = "deep"
        memory["public"]["trace"]["mode_requested"] = "deep"
    elif tamper == "question":
        memory["query"]["question_id"] = "wrong-question"
    elif tamper == "trace":
        memory["public"]["trace"]["id"] = "wrong-trace"
    elif tamper == "citations":
        forged = [{
            "unit_id": "forged", "episode_id": None,
            "resource_id": "forged", "derived_from_unit_ids": [],
        }]
        memory["public"]["trace"]["citations"] = forged
        memory["public"]["recall_response"]["citations"] = forged
    else:
        memory["recall_mutation_proof"]["changed_tables"] = []
    memory["query"]["trace_id"] = memory["public"]["trace"]["id"]
    memory["query"]["trace_sha256"] = campaign.canonical_sha256(
        memory["public"]["trace"]
    )
    memory["query"]["recall_response_sha256"] = campaign.canonical_sha256(
        memory["public"]["recall_response"]
    )
    _rewrite_synthetic_memory_binding(
        campaign, tmp_path, rows, row, memory, outcome=outcome
    )
    with pytest.raises(RuntimeError, match=message):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_aggregate_requires_24_distinct_adapter_instances(tmp_path: Path) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    first = json.loads(
        (tmp_path / rows[0]["row_id"] / "memory-proofs/proof.json").read_text()
    )
    second_path = tmp_path / rows[1]["row_id"] / "memory-proofs/proof.json"
    second = json.loads(second_path.read_text())
    second["isolation"]["instance_id"] = first["isolation"]["instance_id"]
    _rewrite_synthetic_memory_binding(
        campaign, tmp_path, rows, rows[1], second
    )
    with pytest.raises(RuntimeError, match="24 distinct adapter instance"):
        campaign.aggregate_campaign(tmp_path, manifest)


@pytest.mark.parametrize("outcome", ["success", "operational_failure"])
def test_aggregate_authenticates_executed_mode_for_every_memory_proof(
    tmp_path: Path, outcome: str,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    row = rows[0]
    memory_path = tmp_path / row["row_id"] / "memory-proofs/proof.json"
    memory = json.loads(memory_path.read_text())
    memory["public"]["trace"]["mode_executed"] = "deep"
    memory["query"]["trace_sha256"] = campaign.canonical_sha256(
        memory["public"]["trace"]
    )
    _rewrite_synthetic_memory_binding(
        campaign, tmp_path, rows, row, memory, outcome=outcome
    )
    with pytest.raises(RuntimeError, match="trace isolation or mode"):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_construction_attempt_reuses_only_its_exact_completed_bank(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    case_id = rows[0]["question_id"]
    bank = json.loads(
        (tmp_path / "case-banks" / case_id / "manifest.json").read_text()
    )
    assert campaign._validate_case_construction_attempts(
        tmp_path, case_id, bank, allow_new=False
    ) == "reuse"
    with pytest.raises(RuntimeError, match="completed construction attempt.*bank"):
        campaign._validate_case_construction_attempts(
            tmp_path, case_id, None, allow_new=False
        )
    forged = json.loads(json.dumps(bank))
    forged["construction_duration_ms"] += 1
    with pytest.raises(RuntimeError, match="completed construction attempt.*bank"):
        campaign._validate_case_construction_attempts(
            tmp_path, case_id, forged, allow_new=False
        )


def test_construction_attempt_fails_closed_when_prior_attempt_is_incomplete(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    case_id = rows[0]["question_id"]
    attempt = tmp_path / "case-construction" / case_id / "attempt-0001"
    (attempt / "complete.json").unlink()
    bank = json.loads(
        (tmp_path / "case-banks" / case_id / "manifest.json").read_text()
    )
    with pytest.raises(RuntimeError, match="incomplete construction attempt"):
        campaign._validate_case_construction_attempts(
            tmp_path, case_id, bank, allow_new=True
        )


def test_construction_attempt_fails_closed_on_extra_completed_attempt(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    case_id = rows[0]["question_id"]
    attempt = tmp_path / "case-construction" / case_id / "attempt-0002"
    campaign.atomic_write_json(attempt / "attempt.json", {
        "schema_version": 1, "attempt_id": "attempt-0002", "case_id": case_id,
        "classification": "free_local_construction", "complete": False,
    })
    bank = json.loads(
        (tmp_path / "case-banks" / case_id / "manifest.json").read_text()
    )
    campaign.atomic_write_json(attempt / "complete.json", {
        "schema_version": 1, "attempt_id": "attempt-0002", "case_id": case_id,
        "construction_proof_sha256": bank["construction_proof_sha256"],
        "construction_duration_ms": bank["construction_duration_ms"],
        "complete": True,
    })
    with pytest.raises(RuntimeError, match="exactly one construction attempt"):
        campaign._validate_case_construction_attempts(
            tmp_path, case_id, bank, allow_new=False
        )


def test_aggregate_rejects_preserved_incomplete_case_bank(tmp_path: Path) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    preserved = tmp_path / "incomplete-case-banks" / "preserved-attempt"
    preserved.mkdir(parents=True)
    (preserved / "manifest.json").write_text("{}\n")
    with pytest.raises(RuntimeError, match="preserved incomplete case banks"):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_aggregate_rejects_unregistered_case_construction_history(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    unexpected = tmp_path / "case-construction" / "deadbeef" / "attempt-0001"
    campaign.atomic_write_json(unexpected / "attempt.json", {
        "schema_version": 1,
        "attempt_id": "attempt-0001",
        "case_id": "deadbeef",
        "classification": "free_local_construction",
        "complete": False,
    })
    campaign.atomic_write_json(unexpected / "complete.json", {
        "schema_version": 1,
        "attempt_id": "attempt-0001",
        "case_id": "deadbeef",
        "construction_proof_sha256": "f" * 64,
        "construction_duration_ms": 1,
        "complete": True,
    })
    with pytest.raises(RuntimeError, match="construction case inventory"):
        campaign.aggregate_campaign(tmp_path, manifest)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda campaign, root, row, bank, memory: memory["query"].update(
                {"query_only": False}
            ),
            "query-only",
        ),
        (
            lambda campaign, root, row, bank, memory: memory["pairing"].update(
                {"retains": [{"resource_id": "arm-retain"}]}
            ),
            "construction work",
        ),
        (
            lambda campaign, root, row, bank, memory: memory["query"].update(
                {"construction_duration_ms": 1000}
            ),
            "mixes construction",
        ),
        (
            lambda campaign, root, row, bank, memory: memory["query"].update(
                {"construction_proof_sha256": "0" * 64}
            ),
            "construction proof",
        ),
    ],
)
def test_aggregate_rejects_non_query_only_or_mixed_arm_evidence(
    tmp_path: Path, mutate, message: str
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    row = rows[0]
    row_dir = tmp_path / row["row_id"]
    memory_path = row_dir / "memory-proofs/proof.json"
    memory = json.loads(memory_path.read_text())
    bank = json.loads(
        (tmp_path / "case-banks" / row["question_id"] / "manifest.json").read_text()
    )
    mutate(campaign, tmp_path, row, bank, memory)
    campaign.atomic_write_json(memory_path, memory)
    proof_path = row_dir / "row-proof.json"
    proof = json.loads(proof_path.read_text())
    proof["memory_proof_sha256"] = campaign.sha256_file(memory_path)
    proof["artifact_hashes"] = campaign.artifact_hashes(
        row_dir, exclude={"row-proof.json"}
    )
    campaign.atomic_write_json(proof_path, proof)
    _refresh_synthetic_case_bank_retirements(campaign, tmp_path, rows)
    with pytest.raises(RuntimeError, match=message):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_aggregate_validates_memory_proof_on_operational_failure(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    row = rows[0]
    row_dir = tmp_path / row["row_id"]
    memory_path = row_dir / "memory-proofs/proof.json"
    memory = json.loads(memory_path.read_text())
    memory["query"]["query_only"] = False
    campaign.atomic_write_json(memory_path, memory)
    proof_path = row_dir / "row-proof.json"
    proof = json.loads(proof_path.read_text())
    proof["outcome"] = "operational_failure"
    proof["memory_proof_sha256"] = campaign.sha256_file(memory_path)
    proof["artifact_hashes"] = campaign.artifact_hashes(
        row_dir, exclude={"row-proof.json"}
    )
    campaign.atomic_write_json(proof_path, proof)
    _refresh_synthetic_case_bank_retirements(campaign, tmp_path, rows)
    with pytest.raises(RuntimeError, match="not query-only"):
        campaign.aggregate_campaign(tmp_path, manifest)


@pytest.mark.parametrize("outcome", ["success", "operational_failure"])
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"resource_count": 669}, "pairing differs from construction"),
        ({"construction_duration_ms": 1}, "construction timing or cost"),
    ],
)
def test_aggregate_rejects_pairing_drift_for_success_and_failure_rows(
    tmp_path: Path, outcome: str, mutation: dict, message: str
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    row = rows[0]
    row_dir = tmp_path / row["row_id"]
    memory_path = row_dir / "memory-proofs/proof.json"
    memory = json.loads(memory_path.read_text())
    memory["pairing"].update(mutation)
    campaign.atomic_write_json(memory_path, memory)
    proof_path = row_dir / "row-proof.json"
    proof = json.loads(proof_path.read_text())
    proof["outcome"] = outcome
    proof["memory_proof_sha256"] = campaign.sha256_file(memory_path)
    proof["artifact_hashes"] = campaign.artifact_hashes(
        row_dir, exclude={"row-proof.json"}
    )
    campaign.atomic_write_json(proof_path, proof)
    _refresh_synthetic_case_bank_retirements(campaign, tmp_path, rows)
    with pytest.raises(RuntimeError, match=message):
        campaign.aggregate_campaign(tmp_path, manifest)


@pytest.mark.parametrize("outcome", ["success", "operational_failure"])
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda memory: memory.update({"construction_duration_ms": 1}),
         "construction timing or cost"),
        (lambda memory: memory["public"].update(
            {"construction": {"duration_ms": 1}}
        ), "construction timing or cost"),
        (lambda memory: memory.update({"retains": []}), "retains"),
    ],
)
def test_aggregate_rejects_construction_evidence_anywhere_in_memory_proof(
    tmp_path: Path, outcome: str, mutation, message: str
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    row = rows[0]
    row_dir = tmp_path / row["row_id"]
    memory_path = row_dir / "memory-proofs/proof.json"
    memory = json.loads(memory_path.read_text())
    mutation(memory)
    campaign.atomic_write_json(memory_path, memory)
    proof_path = row_dir / "row-proof.json"
    proof = json.loads(proof_path.read_text())
    proof["outcome"] = outcome
    proof["memory_proof_sha256"] = campaign.sha256_file(memory_path)
    proof["artifact_hashes"] = campaign.artifact_hashes(
        row_dir, exclude={"row-proof.json"}
    )
    campaign.atomic_write_json(proof_path, proof)
    _refresh_synthetic_case_bank_retirements(campaign, tmp_path, rows)
    with pytest.raises(RuntimeError, match=message):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_aggregate_requires_12_unique_constructions_and_24_clone_databases(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    first, second = rows[1], rows[3]
    second_proof_path = tmp_path / second["row_id"] / "row-proof.json"
    second_proof = json.loads(second_proof_path.read_text())
    second_proof["scratch_database_identity"] = json.loads(
        (tmp_path / first["row_id"] / "row-proof.json").read_text()
    )["scratch_database_identity"]
    campaign.atomic_write_json(second_proof_path, second_proof)
    _refresh_synthetic_case_bank_retirements(campaign, tmp_path, rows)
    with pytest.raises(RuntimeError, match="clone database identity"):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_aggregate_rejects_reused_construction_proof_across_cases(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    first_case, second_case = manifest["run_order"]["case_order"][:2]
    first_manifest = json.loads(
        (tmp_path / "case-banks" / first_case / "manifest.json").read_text()
    )
    second_manifest_path = tmp_path / "case-banks" / second_case / "manifest.json"
    second_manifest = json.loads(second_manifest_path.read_text())
    second_manifest["construction"] = first_manifest["construction"]
    second_manifest["construction_proof_sha256"] = first_manifest[
        "construction_proof_sha256"
    ]
    campaign.atomic_write_json(second_manifest_path, second_manifest)
    second_attempt = (
        tmp_path / "case-construction" / second_case
        / "attempt-0001" / "complete.json"
    )
    second_complete = json.loads(second_attempt.read_text())
    second_complete["construction_proof_sha256"] = first_manifest[
        "construction_proof_sha256"
    ]
    campaign.atomic_write_json(second_attempt, second_complete)
    seal = campaign._case_bank_seal(second_manifest_path)
    for row in [item for item in rows if item["question_id"] == second_case]:
        row_dir = tmp_path / row["row_id"]
        memory_path = row_dir / "memory-proofs/proof.json"
        memory = json.loads(memory_path.read_text())
        memory["query"]["construction_proof_sha256"] = first_manifest[
            "construction_proof_sha256"
        ]
        memory["pairing"]["construction_proof_sha256"] = first_manifest[
            "construction_proof_sha256"
        ]
        memory["pairing"]["worker"] = first_manifest["construction"]["pairing"][
            "worker"
        ]
        campaign.atomic_write_json(memory_path, memory)
        campaign.atomic_write_json(row_dir / "case-bank-seal.json", seal)
        proof_path = row_dir / "row-proof.json"
        proof = json.loads(proof_path.read_text())
        proof["case_bank_seal_sha256"] = seal["seal_sha256"]
        proof["memory_proof_sha256"] = campaign.sha256_file(memory_path)
        proof["artifact_hashes"] = campaign.artifact_hashes(
            row_dir, exclude={"row-proof.json"}
        )
        campaign.atomic_write_json(proof_path, proof)
    _refresh_synthetic_case_bank_retirements(campaign, tmp_path, rows)
    with pytest.raises(RuntimeError, match="12 unique construction"):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_aggregate_rejects_inactive_candidate_row_directory(tmp_path: Path) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    (tmp_path / "0025-luna-19367bc7").mkdir()
    with pytest.raises(RuntimeError, match="missing or extra finalized rows"):
        campaign.aggregate_campaign(tmp_path, manifest)


def test_aggregate_reports_sealed_construction_latency_separately(
    tmp_path: Path,
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    rows = campaign.expanded_run_order(manifest)
    _write_synthetic_success_campaign(campaign, tmp_path, manifest, rows)
    aggregate = campaign.aggregate_campaign(tmp_path, manifest)
    assert aggregate["construction"] == {
        "case_count": 12,
        "cost_micros": 0,
        "duration_ms": {
            "total": 120_000,
            "p50": 10_000,
            "p95": 10_000,
            "max": 10_000,
        },
        "proof_sha256s": sorted(
            json.loads(path.read_text())["construction_proof_sha256"]
            for path in (tmp_path / "case-banks").glob("*/manifest.json")
        ),
    }
    assert aggregate["candidates"]["sonnet"]["latency_ms"] == {
        "p50": 1000,
        "p95": 1000,
        "max": 1000,
    }


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.body


def test_reader_returns_accepted_generation_before_async_receipt_reconciliation(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    original = b'{"id":"gen-1","model":"qwen/qwen3.5-9b","choices":[]}'
    calls = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append((timeout, json.loads(request.data)))
            return _FakeResponse(original)

    monkeypatch.setattr(campaign.urllib.request, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(
        campaign,
        "_json_url",
        lambda *_args: (_ for _ in ()).throw(AssertionError("receipt lookup ran on response path")),
    )
    manifest = campaign.load_campaign_manifest()
    server, base = campaign._reader_proxy("secret", tmp_path / "reader.json", manifest)
    try:
        connection = http.client.HTTPConnection(base.removeprefix("http://"))
        connection.request(
            "POST", "/chat/completions",
            body=json.dumps({"model": "Qwen/Qwen3.5-9B", "messages": []}),
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == original
        connection.request(
            "POST", "/chat/completions",
            body=json.dumps({"model": "Qwen/Qwen3.5-9B", "messages": []}),
            headers={"content-type": "application/json"},
        )
        retry = connection.getresponse()
        assert retry.status == 422
        retry.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    assert len(calls) == 1
    assert calls[0][0] == 600
    assert calls[0][1]["provider"] == manifest["protocol"]["reader"]["provider_policy"]
    assert json.loads((tmp_path / "reader.json").read_text())["audit_status"] == "receipt_pending"


def test_reader_receipt_reconciliation_waits_for_complete_async_stats(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    manifest = campaign.load_campaign_manifest()
    audit_path = tmp_path / "reader.json"
    campaign.atomic_write_json(audit_path, {
        "audit_status": "receipt_pending",
        "dispatch_count": 1,
        "generation_id": "gen-1",
        "max_liability_micros": 3084,
    })
    receipts = iter([
        {"data": {
            "provider_name": "DeepInfra", "model": "qwen/qwen3.5-9b-20260310",
            "tokens_prompt": None, "tokens_completion": None, "total_cost": None,
        }},
        {"data": {
            "provider_name": "DeepInfra", "model": "qwen/qwen3.5-9b-20260310",
            "tokens_prompt": 181, "tokens_completion": 5533, "total_cost": 0.000816,
        }},
    ])
    sleeps = []
    monkeypatch.setattr(campaign, "_json_url", lambda *_args: next(receipts))
    monkeypatch.setattr(campaign.time, "sleep", sleeps.append)
    reconciled = campaign._reconcile_reader_receipt(
        "secret", audit_path, manifest, attempts=3, delay_seconds=2
    )
    assert reconciled["audit_status"] == "settled"
    assert reconciled["provider_name"] == "DeepInfra"
    assert reconciled["model"] == "qwen/qwen3.5-9b-20260310"
    assert reconciled["tokens_prompt"] == 181
    assert reconciled["tokens_completion"] == 5533
    assert reconciled["total_cost"] == 0.000816
    assert sleeps == [2]
    assert json.loads(audit_path.read_text()) == reconciled


def test_reader_proxy_archives_upstream_rejection_without_hiding_status(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    rejected = b'{"error":{"message":"No endpoints satisfy the request policy","code":404}}'

    class Opener:
        def open(self, request, timeout=None):
            raise campaign.urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(rejected),
            )

    monkeypatch.setattr(campaign.urllib.request, "build_opener", lambda *_args: Opener())
    manifest = campaign.load_campaign_manifest()
    server, base = campaign._reader_proxy("secret", tmp_path / "reader.json", manifest)
    try:
        connection = http.client.HTTPConnection(base.removeprefix("http://"))
        connection.request(
            "POST",
            "/chat/completions",
            body=json.dumps({"model": "Qwen/Qwen3.5-9B", "messages": []}),
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 404
        assert response.read() == rejected
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    audit = json.loads((tmp_path / "reader.json").read_text())
    assert audit["audit_status"] == "rejected"
    assert audit["upstream_status"] == 404
    assert audit["upstream_error"] == {
        "message": "No endpoints satisfy the request policy",
        "code": 404,
    }
    assert audit["response_sha256"] == campaign.hashlib.sha256(rejected).hexdigest()


def test_reader_proxy_retries_explicit_pre_generation_429_with_bounded_backoff(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    rejected = b'{"error":{"message":"Provider returned error","code":429}}'
    accepted = b'{"id":"gen-1","model":"qwen/qwen3.5-9b","choices":[]}'
    calls = []
    sleeps = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise campaign.urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "Too Many Requests",
                    {"Retry-After": "2"},
                    io.BytesIO(rejected),
                )
            return _FakeResponse(accepted)

    monkeypatch.setattr(campaign.urllib.request, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(campaign.time, "sleep", sleeps.append)
    server, base = campaign._reader_proxy(
        "secret", tmp_path / "reader.json", campaign.load_campaign_manifest()
    )
    try:
        connection = http.client.HTTPConnection(base.removeprefix("http://"))
        connection.request(
            "POST", "/chat/completions",
            body=json.dumps({"model": "Qwen/Qwen3.5-9B", "messages": []}),
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == accepted
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    audit = json.loads((tmp_path / "reader.json").read_text())
    assert calls == [600, 600]
    assert sleeps == [2]
    assert audit["dispatch_count"] == 2
    assert audit["audit_status"] == "receipt_pending"
    assert audit["generation_id"] == "gen-1"
    assert audit["pre_generation_rejections"] == [{
        "attempt": 1,
        "generation_id": None,
        "response_sha256": campaign.hashlib.sha256(rejected).hexdigest(),
        "retry_after_seconds": 2,
        "status": 429,
    }]


def test_reader_proxy_never_retries_rejection_with_generation_id(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    rejected = b'{"error":{"message":"Provider returned error","code":429}}'
    calls = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append(timeout)
            raise campaign.urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "2", "X-Generation-Id": "gen-possibly-billed"},
                io.BytesIO(rejected),
            )

    monkeypatch.setattr(campaign.urllib.request, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(
        campaign.time, "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("paid rejection replayed")),
    )
    server, base = campaign._reader_proxy(
        "secret", tmp_path / "reader.json", campaign.load_campaign_manifest()
    )
    try:
        connection = http.client.HTTPConnection(base.removeprefix("http://"))
        connection.request(
            "POST", "/chat/completions",
            body=json.dumps({"model": "Qwen/Qwen3.5-9B", "messages": []}),
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 429
        assert response.read() == rejected
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    audit = json.loads((tmp_path / "reader.json").read_text())
    assert calls == [600]
    assert audit["dispatch_count"] == 1
    assert audit["audit_status"] == "rejected"
    assert audit["pre_generation_rejections"] == [{
        "attempt": 1,
        "generation_id": "gen-possibly-billed",
        "response_sha256": campaign.hashlib.sha256(rejected).hexdigest(),
        "retry_after_seconds": None,
        "status": 429,
    }]


def test_reader_proxy_exhausts_bounded_pre_generation_503_retries(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    rejected = b'{"error":{"message":"No available provider","code":503}}'
    calls = []
    sleeps = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append(timeout)
            raise campaign.urllib.error.HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                {},
                io.BytesIO(rejected),
            )

    monkeypatch.setattr(campaign.urllib.request, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(campaign.time, "sleep", sleeps.append)
    server, base = campaign._reader_proxy(
        "secret", tmp_path / "reader.json", campaign.load_campaign_manifest()
    )
    try:
        connection = http.client.HTTPConnection(base.removeprefix("http://"))
        connection.request(
            "POST", "/chat/completions",
            body=json.dumps({"model": "Qwen/Qwen3.5-9B", "messages": []}),
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 503
        assert response.read() == rejected
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    audit = json.loads((tmp_path / "reader.json").read_text())
    assert calls == [600, 600, 600]
    assert sleeps == [5, 15]
    assert audit["dispatch_count"] == 3
    assert audit["audit_status"] == "rejected"
    assert [row["status"] for row in audit["pre_generation_rejections"]] == [503, 503, 503]
    assert [row["retry_after_seconds"] for row in audit["pre_generation_rejections"]] == [
        5, 15, None,
    ]


def test_reader_retry_delay_honors_numeric_header_with_default_and_cap() -> None:
    campaign = _load()
    contract = campaign.load_campaign_manifest()["protocol"]["reader"]
    assert campaign._reader_retry_delay_seconds("2", 0, contract) == 2
    assert campaign._reader_retry_delay_seconds(None, 0, contract) == 5
    assert campaign._reader_retry_delay_seconds("not-a-delay", 1, contract) == 15
    assert campaign._reader_retry_delay_seconds("0", 0, contract) == 1
    assert campaign._reader_retry_delay_seconds("600", 1, contract) == 60


def test_reader_proxy_archives_transport_unknown_without_replay(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()

    class Opener:
        def open(self, _request, timeout=None):
            assert timeout == 600
            raise TimeoutError("provider exceeded local transport deadline")

    monkeypatch.setattr(campaign.urllib.request, "build_opener", lambda *_args: Opener())
    server, base = campaign._reader_proxy(
        "secret", tmp_path / "reader.json", campaign.load_campaign_manifest()
    )
    try:
        connection = http.client.HTTPConnection(base.removeprefix("http://"))
        connection.request(
            "POST", "/chat/completions",
            body=json.dumps({"model": "Qwen/Qwen3.5-9B", "messages": []}),
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 504
        assert b"outcome is unresolved" in response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    audit = json.loads((tmp_path / "reader.json").read_text())
    assert audit["dispatch_count"] == 1
    assert audit["audit_status"] == "transport_unknown"
    assert audit["audit_error"] == "reader_upstream_transport_failure"


def test_judge_post_acceptance_audit_failure_never_replays_or_changes_2xx(
    tmp_path: Path, monkeypatch
) -> None:
    campaign = _load()
    original = b'{"id":"judge-1","model":"wrong-snapshot","choices":[],"usage":{}}'
    calls = []

    class Opener:
        def open(self, _request, timeout=None):
            calls.append(timeout)
            return _FakeResponse(original)

    monkeypatch.setattr(campaign.urllib.request, "build_opener", lambda *_args: Opener())
    manifest = campaign.load_campaign_manifest()
    campaign.atomic_write_json(tmp_path / "reader-route.json", {
        "audit_status": "settled", "max_liability_micros": 1000, "total_cost": "0.001"
    })
    server, base = campaign._judge_proxy("secret", tmp_path / "judge", manifest)
    try:
        body = {
            "model": "gpt-5.2-2025-12-11", "reasoning_effort": "medium",
            "max_completion_tokens": 4096, "messages": [],
        }
        connection = http.client.HTTPConnection(base.removeprefix("http://"))
        connection.request(
            "POST", "/chat/completions", body=json.dumps(body),
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == original
        connection.request(
            "POST", "/chat/completions", body=json.dumps(body),
            headers={"content-type": "application/json"},
        )
        retry = connection.getresponse()
        assert retry.status == 422
        retry.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    assert len(calls) == 1
    assert json.loads((tmp_path / "judge/0001.json").read_text())["audit_status"] == "invalid"


def test_require_live_database_gates_before_paid_work() -> None:
    """Phase 0 P0.2: liveness must fail at row zero on a dead DB, not mid-root.

    Reproduces the run-65981e4f fault (a vanished container that only surfaced
    as an HTTP 503 mid-campaign) and proves the preflight now catches it before
    any billable call. The live arm uses the campaign Postgres if reachable and
    is skipped otherwise so the check stays runnable without a DB.
    """
    campaign = _load()

    # Dead DB: a valid-shape local URL on a port nothing listens on.
    with pytest.raises(RuntimeError, match="not reachable"):
        campaign._require_live_database(
            "postgres://memphant:memphant@localhost:59999/memphant_dead"
        )

    # Live DB: only assert the happy path when a real server answers, so this
    # test needs no fixture to run in CI's no-Postgres leg.
    live_url = "postgres://memphant:memphant@localhost:5432/memphant"
    probe = subprocess.run(
        ["psql", "--no-psqlrc", "-tAc", "select 1", live_url],
        capture_output=True, text=True,
    )
    if probe.returncode == 0 and probe.stdout.strip() == "1":
        campaign._require_live_database(live_url)  # must not raise
