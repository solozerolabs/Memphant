import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_longmemeval_v2.py"
LOCK = ROOT / "benchmarks/manifests/longmemeval_v2.lock.json"
EVOMEM_AUDIT = ROOT / "benchmarks/manifests/evomembench.release-audit.json"
MEMPHANT_ADAPTER = ROOT / "benchmarks/longmemeval_v2/memphant_memory.py"
MEMPHANT_CONFIG = ROOT / "benchmarks/longmemeval_v2/memphant.memory.json"
MEMPHANT_FAST_CONFIG = ROOT / "benchmarks/longmemeval_v2/memphant.fast.memory.json"
MEMPHANT_BOOTSTRAP = ROOT / "benchmarks/longmemeval_v2/harness_bootstrap.py"
MATERIALIZER = ROOT / "scripts/materialize_longmemeval_v2_runtime.py"
MEMPHANT_ADAPTER_LOCK = (
    ROOT / "benchmarks/manifests/longmemeval_v2_memphant_adapter.lock.json"
)


def load_adapter():
    spec = importlib.util.spec_from_file_location("run_longmemeval_v2", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def install_construction_binding(adapter, monkeypatch, tmp_path):
    extraction_key = "8" * 64
    artifact_root = tmp_path / "campaign"
    artifact_root.mkdir()
    subledger = artifact_root / "CONSTRUCTION-ATTEMPTS.jsonl"
    request_sha256 = "7" * 64
    source_body_sha256 = "6" * 64
    evidence_slices_sha256 = "5" * 64
    plan = {
        "extraction_key": extraction_key,
        "request_sha256": request_sha256,
        "per_attempt_reservation_nanos": 10,
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "maximum_attempts": 3,
        "source_kind": "resource",
        "source_body_sha256": source_body_sha256,
        "batch_index": 0,
        "evidence_slices_sha256": evidence_slices_sha256,
    }
    events = [
        {
            "event": "started",
            "attempt_id": "attempt-1",
            "campaign_attempt": 1,
            "extraction_key": extraction_key,
            "request_sha256": request_sha256,
            "source_kind": "resource",
            "source_body_sha256": source_body_sha256,
            "batch_index": 0,
            "requested_model": "qwen/qwen3.5-9b-20260310",
        },
        {
            "event": "result",
            "attempt_id": "attempt-1",
            "campaign_attempt": 1,
            "extraction_key": extraction_key,
            "request_sha256": request_sha256,
            "source_kind": "resource",
            "source_body_sha256": source_body_sha256,
            "batch_index": 0,
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": "qwen/qwen3.5-9b",
            "served_provider": "DeepInfra",
            "reservation_status": "settled",
            "parse_status": "decoded",
            "error": None,
            "usage": {"cost": "0.000000001"},
        },
    ]
    subledger.write_text("".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events))
    campaign_journal = artifact_root / "CAMPAIGN-ATTEMPTS.jsonl"
    campaign_journal.write_text("aggregate-reserved\n")
    observation_cache = artifact_root / "observation-cache"
    observation_cache.mkdir()
    cache_hits_root = artifact_root / "cache-hits"
    cache_hits_root.mkdir()
    binding_root = artifact_root / "CONSTRUCTION-BINDINGS"
    binding_root.mkdir()
    manifest = {
        "construction": {
            "state_mode": "structured-resource-v1",
            "model": "qwen/qwen3.5-9b-20260310",
            "response_model": "qwen/qwen3.5-9b",
            "provider": "deepinfra",
            "prompt_sha256": "e" * 64,
            "code_sha256s": {
                "crates/memphant-runtime/src/structured_state_openrouter.rs": "1" * 64
            },
            "maximum_output_tokens": 4096,
            "maximum_attempts": 3,
            "input_price_nanos_per_million": 100_000_000,
            "output_price_nanos_per_million": 150_000_000,
        }
    }
    manifest_path = artifact_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    census_core = {
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "construction": {
            "plan_inventory": [plan],
            "plan_inventory_sha256": _sha256_json([plan]),
            "input_manifest_sha256": "d" * 64,
            "construction_identity_sha256": _sha256_json(manifest["construction"]),
        },
    }
    census = {**census_core, "census_sha256": _sha256_json(census_core)}
    census_path = artifact_root / "CAMPAIGN-CENSUS.json"
    census_path.write_text(json.dumps(census))
    wave_core = {
        "schema_version": 1,
        "campaign_census_sha256": census["census_sha256"],
        "ordered_plans_sha256": census["construction"]["plan_inventory_sha256"],
        "plans": [plan],
    }
    wave = {**wave_core, "wave_sha256": _sha256_json(wave_core)}
    wave_path = artifact_root / "CONSTRUCTION-WAVE.json"
    wave_path.write_text(json.dumps(wave))
    artifact_paths = {
        "journal": str(campaign_journal.resolve()),
        "construction_subledger": str(subledger.resolve()),
        "observation_cache": str(observation_cache.resolve()),
        "cache_hits": str(cache_hits_root.resolve()),
    }
    scope = {
        "inputs": {
            "census_sha256": census["census_sha256"],
            "census_file_sha256": hashlib.sha256(census_path.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
        "artifacts": artifact_paths,
        "execution": {"cache_namespace": "fixture-v1"},
    }
    authorization_sha256 = _sha256_json(scope)
    packet = {
        "schema_version": 1,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        **scope,
        "authorization": {"authorization_scope_sha256": authorization_sha256},
    }
    authorization_path = artifact_root / "CAMPAIGN-AUTHORIZATION.json"
    authorization_path.write_text(json.dumps(packet))
    plan_subset_sha256 = _sha256_json([plan])
    cache_hits = cache_hits_root / plan_subset_sha256
    cache_hits.mkdir()
    ledger_prefix_sha256 = hashlib.sha256(subledger.read_bytes()).hexdigest()
    authority = {
        "authorization_path": str(authorization_path.resolve()),
        "authorization_file_sha256": hashlib.sha256(authorization_path.read_bytes()).hexdigest(),
        "authorization_scope_sha256": authorization_sha256,
        "census_path": str(census_path.resolve()),
        "census_file_sha256": hashlib.sha256(census_path.read_bytes()).hexdigest(),
        "census_sha256": census["census_sha256"],
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "wave_path": str(wave_path.resolve()),
        "wave_file_sha256": hashlib.sha256(wave_path.read_bytes()).hexdigest(),
        "wave_sha256": wave["wave_sha256"],
        "plan_inventory_sha256": census["construction"]["plan_inventory_sha256"],
        "plan_subset_sha256": plan_subset_sha256,
        "canonical_artifact_paths_sha256": _sha256_json(artifact_paths),
        "binding_path": str((binding_root / f"{plan_subset_sha256}.json").resolve()),
    }
    binding_core = {
        "schema_version": 1,
        "authority": authority,
        "authorization": {
            "authorization_sha256": authorization_sha256,
            "campaign_sha256": census["census_sha256"],
            "screen_id": "state-aware-full",
        },
        "selection": {
            "selection_sha256": plan_subset_sha256,
            "input_manifest_sha256": "d" * 64,
            "state_mode": "structured-resource-v1",
        },
        "compiler": {
            "prompt_sha256": "e" * 64,
            "schema_sha256": _sha256_json(
                {
                    "construction_identity_sha256": census["construction"][
                        "construction_identity_sha256"
                    ],
                    "provider_code_sha256": "1" * 64,
                    "contract": "structured-state-response-schema-v1",
                }
            ),
            "provider_code_sha256": "1" * 64,
        },
        "provider": {
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": "qwen/qwen3.5-9b",
            "requested_provider": "deepinfra",
            "served_provider": "DeepInfra",
            "input_price_nanos_per_million": 100_000_000,
            "output_price_nanos_per_million": 150_000_000,
            "maximum_output_tokens": 4096,
            "maximum_attempts": 3,
        },
        "cache": {
            "namespace": "fixture-v1",
            "observation_cache_path": str(observation_cache.resolve()),
            "source_receipts_path": str(cache_hits.resolve()),
        },
        "ledger": {
            "subledger_path": str(subledger.resolve()),
            "campaign_journal_path": str(campaign_journal.resolve()),
            "source_ledger_prefix_bytes": len(subledger.read_bytes()),
            "source_ledger_prefix_sha256": ledger_prefix_sha256,
            "before_event_sha256": ledger_prefix_sha256,
            "campaign_journal_sha256": hashlib.sha256(campaign_journal.read_bytes()).hexdigest(),
        },
        "coverage": {
            "plans": [plan],
            "expected_extraction_keys": [extraction_key],
            "expected_extraction_keys_sha256": _sha256_json([extraction_key]),
        },
    }
    binding = {**binding_core, "binding_sha256": _sha256_json(binding_core)}
    path = binding_root / f"{plan_subset_sha256}.json"
    path.write_text(json.dumps(binding), encoding="utf-8")
    for name, value in {
        "CANONICAL_AUTHORIZATION_PATH": authorization_path,
        "CANONICAL_CENSUS_PATH": census_path,
        "CANONICAL_MANIFEST_PATH": manifest_path,
        "CANONICAL_WAVE_PATH": wave_path,
        "CANONICAL_BINDING_ROOT": binding_root,
    }.items():
        monkeypatch.setattr(adapter, name, value)
    monkeypatch.setenv("MEMPHANT_LME_CONSTRUCTION_BINDING", str(path))
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_AUTHORIZATION_SHA256", authorization_sha256)
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CAMPAIGN_SHA256", census["census_sha256"])
    monkeypatch.setenv(
        "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE", str(observation_cache.resolve())
    )
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CACHE_HITS", str(cache_hits))
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CACHE_NAMESPACE", "fixture-v1")
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER", str(subledger))
    monkeypatch.setenv("MEMPHANT_CAMPAIGN_ATTEMPT_LEDGER", str(campaign_journal))
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER", str(subledger.resolve()))
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_BYTES", str(len(subledger.read_bytes())))
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_SHA256", ledger_prefix_sha256)
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_MODEL", "qwen/qwen3.5-9b")
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_PROVIDER", "DeepInfra")
    return binding


def test_self_minted_construction_binding_is_rejected_before_worker(
    monkeypatch, tmp_path
):
    adapter, _registry = load_memphant_adapter(monkeypatch)
    binding = install_construction_binding(adapter, monkeypatch, tmp_path)
    path = Path(os.environ["MEMPHANT_LME_CONSTRUCTION_BINDING"])
    alternate = tmp_path / "self-minted.json"
    alternate.write_bytes(path.read_bytes())
    monkeypatch.setenv("MEMPHANT_LME_CONSTRUCTION_BINDING", str(alternate))

    with pytest.raises(RuntimeError, match="canonical authority"):
        adapter._load_construction_binding()


def test_canonical_binding_allows_only_later_source_ledger_suffixes(
    monkeypatch, tmp_path
):
    adapter, _registry = load_memphant_adapter(monkeypatch)
    binding = install_construction_binding(adapter, monkeypatch, tmp_path)

    assert adapter._load_construction_binding() == binding
    with Path(binding["ledger"]["subledger_path"]).open("a", encoding="utf-8") as handle:
        handle.write('{"event":"later-authorized-attempt"}\n')

    assert adapter._load_construction_binding() == binding


def test_binding_with_wrong_canonical_wave_hash_is_rejected_before_worker(
    monkeypatch, tmp_path
):
    adapter, _registry = load_memphant_adapter(monkeypatch)
    binding = install_construction_binding(adapter, monkeypatch, tmp_path)
    wave_path = adapter.CANONICAL_WAVE_PATH
    wave = json.loads(wave_path.read_text())
    wave["wave_sha256"] = "0" * 64
    wave_path.write_text(json.dumps(wave))
    binding["authority"]["wave_sha256"] = wave["wave_sha256"]
    binding["authority"]["wave_file_sha256"] = hashlib.sha256(
        wave_path.read_bytes()
    ).hexdigest()
    core = {key: value for key, value in binding.items() if key != "binding_sha256"}
    binding["binding_sha256"] = _sha256_json(core)
    Path(os.environ["MEMPHANT_LME_CONSTRUCTION_BINDING"]).write_text(json.dumps(binding))

    with pytest.raises(RuntimeError, match="wave drift"):
        adapter._load_construction_binding()


def test_binding_with_changed_subledger_path_is_rejected_before_worker(
    monkeypatch, tmp_path
):
    adapter, _registry = load_memphant_adapter(monkeypatch)
    binding = install_construction_binding(adapter, monkeypatch, tmp_path)
    alternate = tmp_path / "alternate-ledger.jsonl"
    alternate.write_bytes(Path(binding["ledger"]["subledger_path"]).read_bytes())
    binding["ledger"]["subledger_path"] = str(alternate.resolve())
    core = {key: value for key, value in binding.items() if key != "binding_sha256"}
    binding["binding_sha256"] = _sha256_json(core)
    Path(os.environ["MEMPHANT_LME_CONSTRUCTION_BINDING"]).write_text(json.dumps(binding))

    with pytest.raises(RuntimeError, match="ledger path drift"):
        adapter._load_construction_binding()


def test_alternate_observation_cache_is_rejected_before_tenant_or_worker(
    monkeypatch, tmp_path
):
    adapter, registry = load_memphant_adapter(monkeypatch)
    install_construction_binding(adapter, monkeypatch, tmp_path)
    alternate = tmp_path / "alternate-observation-cache"
    alternate.mkdir()
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE", str(alternate))
    for name, body in {
        "MEMPHANT_CLI_BIN": b"cli",
        "MEMPHANT_LME_SERVER_BIN": b"server",
        "MEMPHANT_LME_WORKER_BIN": b"worker",
    }.items():
        path = tmp_path / name.lower()
        path.write_bytes(body)
        monkeypatch.setenv(name, str(path))
    for name, value in {
        "MEMPHANT_SCRATCH_ACTIVE": "1",
        "MEMPHANT_TEST_DATABASE_URL": "postgres://fixture",
        "MEMPHANT_LME_SERVER_URL": "http://fixture",
        "MEMPHANT_LME_PROOF_DIR": str(tmp_path / "proof"),
        "MEMPHANT_LME_RUN_ID": "fixture",
    }.items():
        monkeypatch.setenv(name, value)
    reached = []

    def provision_reached(**_kwargs):
        reached.append("tenant")
        raise RuntimeError("tenant provisioning reached")

    monkeypatch.setattr(adapter, "_provision_tenant", provision_reached)
    monkeypatch.setattr(
        adapter,
        "_drain_worker",
        lambda *_args, **_kwargs: reached.append("worker"),
    )

    with pytest.raises(RuntimeError, match="cache path or namespace drift"):
        registry["memphant"](
            json.loads(MEMPHANT_CONFIG.read_text())["memory_params"]
        )
    assert reached == []


def test_observation_cache_environment_is_required_and_resolved(
    monkeypatch, tmp_path
):
    adapter, _registry = load_memphant_adapter(monkeypatch)
    binding = install_construction_binding(adapter, monkeypatch, tmp_path)
    monkeypatch.delenv("MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE")
    with pytest.raises(RuntimeError, match="MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE"):
        adapter._load_construction_binding()

    canonical = Path(binding["cache"]["observation_cache_path"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE",
        os.path.relpath(canonical, tmp_path),
    )
    assert adapter._load_construction_binding() == binding

    alias = tmp_path / "canonical-observation-cache-alias"
    alias.symlink_to(canonical, target_is_directory=True)
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE", str(alias))
    assert adapter._load_construction_binding() == binding


def test_longmemeval_v2_release_is_immutably_pinned_and_native_scored():
    lock = json.loads(LOCK.read_text())

    assert lock["code"]["commit"] == "6f020ac2fc3275e46c706d3406e02c3ed79b7be2"
    assert lock["dataset"]["revision"] == "f152293e235517d504809563c833d7190b8c713b"
    assert lock["code"]["license"] == lock["dataset"]["license"] == "Apache-2.0"
    assert lock["protocol"]["generation_and_scoring"] == (
        "official evaluation/harness.py at code.commit"
    )
    assert lock["dataset"]["files"]["trajectories.jsonl"]["bytes"] == 1_195_604_539
    assert (
        lock["dataset"]["files"]["trajectory_screenshots/web_screenshots.tar.gz"][
            "bytes"
        ]
        == 2_562_302_847
    )


def test_release_urls_are_revision_pinned():
    adapter = load_adapter()
    lock = json.loads(LOCK.read_text())

    urls = adapter.release_urls(lock)

    assert lock["code"]["commit"] in urls["code_archive"]
    assert lock["dataset"]["revision"] in urls["dataset_revision"]
    assert "/resolve/main/" not in urls["dataset_revision"]


def test_verify_dataset_fails_closed_on_any_locked_file_drift(tmp_path):
    adapter = load_adapter()
    data = tmp_path / "data"
    data.mkdir()
    (data / "checksums.sha256").write_text("abc  questions.jsonl\n")
    (data / "questions.jsonl").write_text("drift")
    expected = {
        "checksums_file": {"path": "checksums.sha256", "sha256": "0" * 64},
        "files": {},
    }

    with pytest.raises(RuntimeError, match="checksums file sha256 mismatch"):
        adapter.verify_dataset(data, expected)


def test_verify_dataset_accepts_only_an_exact_locked_upstream_checksum_exception(tmp_path):
    adapter = load_adapter()
    data = tmp_path / "data"
    data.mkdir()
    snapshot = b"immutable snapshot\n"
    upstream_sha = hashlib.sha256(b"pre-release content\n").hexdigest()
    snapshot_sha = hashlib.sha256(snapshot).hexdigest()
    checksums = f"{upstream_sha}  README.md\n"
    (data / "README.md").write_bytes(snapshot)
    (data / "checksums.sha256").write_text(checksums)
    expected = {
        "checksums_file": {
            "path": "checksums.sha256",
            "sha256": hashlib.sha256(checksums.encode()).hexdigest(),
            "entries": 1,
        },
        "checksum_exceptions": {
            "README.md": {
                "upstream_sha256": upstream_sha,
                "snapshot_sha256": snapshot_sha,
            }
        },
        "files": {},
    }

    assert adapter.verify_dataset(data, expected) == {
        "upstream_checksum_entries": 1,
        "upstream_checksum_exceptions": 1,
        "separately_locked_files": 0,
    }

    expected["checksum_exceptions"]["README.md"]["snapshot_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="dataset sha256 mismatch: README.md"):
        adapter.verify_dataset(data, expected)


def test_native_command_delegates_generation_and_scoring_to_official_harness(tmp_path):
    adapter = load_adapter()
    official = tmp_path / "official"
    for relative in (
        "evaluation/harness.py",
        "evaluation/qa_eval_metrics.py",
        "memory_modules/memory.py",
    ):
        path = official / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")

    command = adapter.native_harness_command(
        official_dir=official,
        domain="web",
        questions_path=tmp_path / "questions.json",
        haystack_path=tmp_path / "haystack.json",
        trajectories_path=tmp_path / "trajectories.jsonl",
        memory_config_path=tmp_path / "memory.json",
        output_dir=tmp_path / "out",
        reader_model="reader",
        reader_base_url="http://reader/v1",
        evaluator_model="judge",
        evaluator_base_url="http://judge/v1",
        python="python3",
    )

    assert command[:2] == ["python3", str(official / "evaluation/harness.py")]
    assert "--memory-config-path" in command
    assert command[command.index("--memory-context-max-tokens") + 1] == "200000"
    assert "--model" in command and "reader" in command
    assert "--evaluator-model" in command and "judge" in command
    assert command[command.index("--temperature") + 1] == "0.6"
    assert command[command.index("--top-p") + 1] == "0.95"
    assert command[command.index("--top-k") + 1] == "20"
    assert command[command.index("--evaluator-reasoning-effort") + 1] == "medium"
    assert not any("run_longmemeval_v2.py" in part for part in command)


def test_evomembench_is_fail_closed_until_repo_level_license_exists():
    audit = json.loads(EVOMEM_AUDIT.read_text())

    assert audit["code"]["commit"] == "aa4cea8fd936b76b2d3591d3ef897030617dc43a"
    assert audit["public_execution_ready"] is False
    assert audit["blockers"]["repository_license"] == "missing"
    assert audit["decision"] == "do_not_acquire_or_integrate"


def load_memphant_adapter(monkeypatch):
    registry = {}

    class Memory:
        def __init__(self, memory_params):
            self.memory_params = memory_params
            self._context = {}

        def set_query_context(self, **kwargs):
            self._context = kwargs

        def get_query_context(self):
            return dict(self._context)

        def clear_query_context(self):
            self._context = {}

    def register_memory(cls):
        registry[cls.memory_type] = cls
        return cls

    package = types.ModuleType("memory_modules")
    memory_module = types.ModuleType("memory_modules.memory")
    memory_module.Memory = Memory
    memory_module.MemoryContextItem = dict
    memory_module.register_memory = register_memory
    monkeypatch.setitem(sys.modules, "memory_modules", package)
    monkeypatch.setitem(sys.modules, "memory_modules.memory", memory_module)
    spec = importlib.util.spec_from_file_location(
        "fixture_memphant_memory", MEMPHANT_ADAPTER
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, registry


def test_worker_drain_archives_stdout_and_stderr_before_count_validation(
    monkeypatch, tmp_path
):
    adapter, _registry = load_memphant_adapter(monkeypatch)
    proof_dir = tmp_path / "proof"
    monkeypatch.setenv("MEMPHANT_LME_PROOF_DIR", str(proof_dir))
    completed = adapter.subprocess.CompletedProcess(
        ["worker"],
        0,
        "memphant-worker: drain completed=139\n",
        "memphant-worker: job fixture failed: root cause\n",
    )
    monkeypatch.setattr(adapter.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError, match="worker compiled 139 sources, expected 670"):
        adapter._drain_worker("worker", "postgres://fixture", 670)

    assert (proof_dir / "worker.stdout").read_text() == completed.stdout
    assert (proof_dir / "worker.stderr").read_text() == completed.stderr


def test_memphant_recall_uses_a_separate_benchmark_deadline(monkeypatch):
    adapter, _registry = load_memphant_adapter(monkeypatch)
    observed_timeouts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(_request, *, timeout):
        observed_timeouts.append(timeout)
        return Response()

    monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
    client = adapter._JsonClient("http://fixture", "mk_fixture")

    client.request("GET", "/v1/health")
    client.request(
        "POST",
        "/v1/recall",
        {"query": "fixture"},
        timeout_seconds=adapter.RECALL_REQUEST_TIMEOUT_SECONDS,
    )

    assert observed_timeouts == [
        adapter.DEFAULT_REQUEST_TIMEOUT_SECONDS,
        adapter.RECALL_REQUEST_TIMEOUT_SECONDS,
    ]
    assert adapter.RECALL_REQUEST_TIMEOUT_SECONDS == 600


def test_memphant_memory_uses_isolated_rest_scope_and_emits_trace_proof(
    monkeypatch, tmp_path
):
    adapter, registry = load_memphant_adapter(monkeypatch)
    binding = install_construction_binding(adapter, monkeypatch, tmp_path)
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setenv("MEMPHANT_TEST_DATABASE_URL", "postgres://fixture")
    monkeypatch.setenv("MEMPHANT_LME_SERVER_URL", "http://fixture")
    monkeypatch.setenv("MEMPHANT_LME_PROOF_DIR", str(tmp_path / "proof"))
    cli_bin = tmp_path / "memphant-cli"
    server_bin = tmp_path / "memphant-server"
    worker_bin = tmp_path / "memphant-worker"
    cli_bin.write_bytes(b"fixture-cli")
    server_bin.write_bytes(b"fixture-server")
    worker_bin.write_bytes(b"fixture-worker")
    monkeypatch.setenv("MEMPHANT_CLI_BIN", str(cli_bin))
    monkeypatch.setenv("MEMPHANT_LME_SERVER_BIN", str(server_bin))
    monkeypatch.setenv("MEMPHANT_LME_WORKER_BIN", str(worker_bin))
    monkeypatch.setenv("MEMPHANT_LME_RUN_ID", "fixture-run")

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    cli_calls = []

    def fake_run(command, **kwargs):
        cli_calls.append(command)
        if "create-tenant" in command:
            return Completed("tenant_created id=00000000-0000-0000-0000-000000000101\n")
        return Completed("mk_fixture_key\n")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    requests = []
    resource_count = 0

    def fake_request(method, path, payload=None, *, timeout_seconds=None):
        nonlocal resource_count
        requests.append((method, path, payload, timeout_seconds))
        if path.startswith("/v1/context-bindings/"):
            return {
                "subject_id": "00000000-0000-0000-0000-000000000201",
                "scope_id": "00000000-0000-0000-0000-000000000202",
                "actor_id": "00000000-0000-0000-0000-000000000203",
                "agent_node_id": "00000000-0000-0000-0000-000000000204",
                "subject_generation": 0,
            }
        if path == "/v1/episodes":
            resource_count += 1
            return {
                "resource_id": f"resource-{resource_count}",
                "enqueued": ["compile"],
            }
        if path == "/v1/recall":
            return {
                "trace_id": "00000000-0000-0000-0000-000000000404",
                "items": [
                    {
                        "unit_id": "unit-1",
                        "body": "The retained answer evidence.",
                        "kind": "episode",
                        "derived_by": "fixture",
                        "inclusion_reason": "ranked",
                        "suppression_labels": [],
                    }
                ],
                "citations": [{"unit_id": "unit-1", "resource_id": "resource-1"}],
                "candidate_whitelist": ["unit-1"],
                "abstention": False,
                "degraded": False,
                "suppression_labels": [],
            }
        assert method == "GET"
        return {
            "id": "00000000-0000-0000-0000-000000000404",
            "tenant_id": "00000000-0000-0000-0000-000000000101",
            "scope_id": memory.scope_id,
            "actor_id": memory.actor_id,
            "query_hash": "native-query-hash",
            "context_items": [
                {
                    "unit_id": "unit-1",
                    "body": "The retained answer evidence.",
                    "kind": "episode",
                    "derived_by": "fixture",
                    "inclusion_reason": "ranked",
                    "suppression_labels": [],
                }
            ],
            "citations": [{"unit_id": "unit-1", "resource_id": "resource-1"}],
        }

    monkeypatch.setattr(
        adapter._JsonClient, "request", lambda self, *a, **k: fake_request(*a, **k)
    )
    worker_calls = []

    def fake_drain_worker(worker_bin, database_url, expected):
        worker_calls.append((worker_bin, database_url, expected))
        return {
            "completed_sources": expected,
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
        }

    monkeypatch.setattr(adapter, "_drain_worker", fake_drain_worker)
    schema_snapshots = iter(
        [
            {
                "resource": {"rows": 1, "content_md5": "resource-stable"},
                "retrieval_trace": {"rows": 0, "content_md5": "trace-before"},
            },
            {
                "resource": {"rows": 1, "content_md5": "resource-stable"},
                "retrieval_trace": {"rows": 1, "content_md5": "trace-after"},
            },
        ]
    )
    monkeypatch.setattr(
        adapter, "_schema_snapshot", lambda database_url: next(schema_snapshots)
    )
    config = json.loads(MEMPHANT_CONFIG.read_text())["memory_params"]
    memory = registry["memphant"](config)
    memory.insert(
        {
            "id": "trajectory-1",
            "goal": "Find the setting",
            "outcome": "success",
            "start_url": "https://example.test",
            "states": [
                {
                    "url": "https://example.test/one",
                    "action": "click settings",
                    "thought": "look for the control",
                    "accessibility_tree": "Settings page",
                    "screenshot": "screenshots/one.png",
                },
                {
                    "url": "https://example.test/two",
                    "action": "read value",
                    "thought": None,
                    "accessibility_tree": "Value is retained",
                    "screenshot": "screenshots/two.png",
                },
            ],
        }
    )
    memory.set_query_context(
        question_id="question-1",
        question_item={"answer": "GOLD MUST NOT LEAK", "eval_function": "secret"},
    )
    construction = memory.prepare()
    context = memory.query("What value was retained?")
    metadata = memory.post_query_hook(
        query="What value was retained?", query_image=None, memory_context=context
    )

    assert context == [{"type": "text", "value": "The retained answer evidence."}]
    retain_payloads = [
        payload for _, path, payload, _ in requests if path == "/v1/episodes"
    ]
    assert len(retain_payloads) == 1
    assert retain_payloads[0]["scope_id"] == memory.scope_id
    assert retain_payloads[0]["subject_id"] == memory.context["subject_id"]
    assert retain_payloads[0]["payload"]["resource"]["kind"] == "document"
    assert "tenant_id" not in retain_payloads[0]
    assert "GOLD MUST NOT LEAK" not in json.dumps(requests)
    recall = next(payload for _, path, payload, _ in requests if path == "/v1/recall")
    assert recall["limit"] == 20
    assert recall["budget_tokens"] == 32768
    assert "allowed_scope_ids" not in recall
    assert (
        next(timeout for _, path, _, timeout in requests if path == "/v1/recall") == 600
    )
    assert metadata["trace_id"] == "00000000-0000-0000-0000-000000000404"
    assert len(metadata["trace_sha256"]) == len(metadata["context_sha256"]) == 64
    proof = next(
        candidate
        for candidate in (
            json.loads(path.read_text()) for path in (tmp_path / "proof").glob("*.json")
        )
        if "query" in candidate
    )
    assert proof["pairing"]["trajectory_count"] == 1
    assert proof["pairing"]["resource_count"] == 1
    assert proof["pairing"]["worker"]["completed_sources"] == 1
    assert proof["pairing"]["retains"][0]["fragments"][0]["resource_id"] == "resource-1"
    assert proof["query"]["question_id"] == "question-1"
    assert proof["query"]["trace_sha256"] == metadata["trace_sha256"]
    assert proof["recall_mutation_proof"]["changed_tables"] == ["retrieval_trace"]
    assert proof["public"]["recall_response"]["trace_id"] == metadata["trace_id"]
    assert proof["public"]["trace"]["id"] == metadata["trace_id"]
    assert set(proof["contract"]["binaries"]) == {"server", "cli", "worker"}
    assert (
        proof["contract"]["binaries"]["server"]["sha256"]
        == hashlib.sha256(b"fixture-server").hexdigest()
    )
    assert proof["contract"]["recall_request_timeout_seconds"] == 600
    assert any("create-tenant" in call for call in cli_calls)
    assert len(worker_calls) == 1
    assert construction["pairing"]["trajectory_count"] == 1
    assert construction["pairing"]["resource_count"] == 1
    assert construction["pairing"]["worker"]["completed_sources"] == 1
    assert construction["binding_sha256"] == binding["binding_sha256"]
    assert construction["isolation"]["tenant_id"] == memory.tenant_id
    assert construction["isolation"]["context"] == memory.context
    assert "api_key" not in json.dumps(construction)


def test_memphant_query_only_reuses_frozen_construction_without_writes(
    monkeypatch, tmp_path
):
    adapter, registry = load_memphant_adapter(monkeypatch)
    install_construction_binding(adapter, monkeypatch, tmp_path)
    cli_bin = tmp_path / "cli"
    server_bin = tmp_path / "server"
    worker_bin = tmp_path / "worker"
    cli_bin.write_bytes(b"fixture-cli")
    server_bin.write_bytes(b"fixture-server")
    worker_bin.write_bytes(b"fixture-worker")
    for key, value in {
        "MEMPHANT_SCRATCH_ACTIVE": "1",
        "MEMPHANT_TEST_DATABASE_URL": "postgres://fixture",
        "MEMPHANT_LME_SERVER_URL": "http://fixture",
        "MEMPHANT_LME_PROOF_DIR": str(tmp_path / "proof"),
        "MEMPHANT_CLI_BIN": str(cli_bin),
        "MEMPHANT_LME_SERVER_BIN": str(server_bin),
        "MEMPHANT_LME_WORKER_BIN": str(worker_bin),
        "MEMPHANT_LME_RUN_ID": "fixture",
    }.items():
        monkeypatch.setenv(key, value)

    tenant_id = "00000000-0000-0000-0000-000000000111"
    frozen_context = {
        "subject_id": "00000000-0000-0000-0000-000000000201",
        "scope_id": "00000000-0000-0000-0000-000000000202",
        "actor_id": "00000000-0000-0000-0000-000000000203",
        "agent_node_id": "00000000-0000-0000-0000-000000000204",
        "subject_generation": 0,
    }
    cli_actions = []

    def fake_provision_tenant(**_kwargs):
        cli_actions.append("create-tenant")
        return tenant_id, "mk_source"

    def fake_create_api_key(**kwargs):
        cli_actions.append(("create-key", kwargs["tenant_id"]))
        return "mk_clone"

    monkeypatch.setattr(adapter, "_provision_tenant", fake_provision_tenant)
    monkeypatch.setattr(adapter, "_create_api_key", fake_create_api_key)
    monkeypatch.setattr(
        adapter,
        "_provision_context",
        lambda _client, _instance_id: dict(frozen_context),
    )
    worker_calls = []
    monkeypatch.setattr(
        adapter,
        "_drain_worker",
        lambda worker_bin, database_url, expected: worker_calls.append(expected)
        or {
            "completed_sources": expected,
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
        },
    )
    requests = []

    def fake_request(_self, method, path, payload=None, *, timeout_seconds=None):
        requests.append((method, path, payload, timeout_seconds))
        if path == "/v1/episodes":
            return {"resource_id": "resource-1", "enqueued": ["compile"]}
        if path == "/v1/recall":
            return {
                "trace_id": "00000000-0000-0000-0000-000000000404",
                "items": [],
                "citations": [],
                "degraded": False,
            }
        assert path.startswith("/v1/traces/")
        return {
            "id": "00000000-0000-0000-0000-000000000404",
            "tenant_id": tenant_id,
            "scope_id": frozen_context["scope_id"],
            "actor_id": frozen_context["actor_id"],
            "query_hash": "native-query-hash",
            "context_items": [],
            "citations": [],
        }

    monkeypatch.setattr(adapter._JsonClient, "request", fake_request)
    snapshots = iter(
        [
            {"retrieval_trace": {"rows": 0, "content_md5": "before"}},
            {"retrieval_trace": {"rows": 1, "content_md5": "after"}},
        ]
    )
    monkeypatch.setattr(
        adapter, "_schema_snapshot", lambda _database_url: next(snapshots)
    )
    trajectory = {
        "id": "trajectory-1",
        "goal": "Find the setting",
        "outcome": "success",
        "start_url": "https://example.test",
        "states": [
            {
                "url": "https://example.test/one",
                "action": "read value",
                "thought": None,
                "accessibility_tree": "Value is retained",
                "screenshot": "unused.png",
            }
        ],
    }
    source_config = json.loads(MEMPHANT_CONFIG.read_text())["memory_params"]
    source_config["mode"] = "fast"
    source = registry["memphant"](source_config)
    source.insert(trajectory)
    construction = source.prepare()
    construction_path = tmp_path / "construction.json"
    construction_path.write_text(json.dumps(construction))
    monkeypatch.setenv("MEMPHANT_LME_PREBUILT_PROOF", str(construction_path))

    clone_config = dict(source_config)
    clone_config["mode"] = "deep"
    clone = registry["memphant"](clone_config)
    assert clone.tenant_id == source.tenant_id
    assert clone.context == source.context
    clone.insert(trajectory)
    clone.set_query_context(
        question_id="question-1", question_item={"answer": "secret"}
    )
    context = clone.query("What value was retained?")
    metadata = clone.post_query_hook(
        query="What value was retained?", query_image=None, memory_context=context
    )

    assert worker_calls == [1]
    assert [path for _, path, _, _ in requests].count("/v1/episodes") == 1
    assert cli_actions == ["create-tenant", ("create-key", tenant_id)]
    assert metadata["query_only"] is True
    assert (
        metadata["construction_proof_sha256"]
        == construction["construction_proof_sha256"]
    )
    proof = next(
        candidate
        for candidate in (
            json.loads(path.read_text()) for path in (tmp_path / "proof").glob("*.json")
        )
        if candidate.get("pairing", {}).get("query_only") is True
    )
    assert proof["pairing"]["query_only"] is True
    assert proof["pairing"]["resource_count"] == 1
    assert proof["pairing"]["worker"]["completed_sources"] == 1
    assert (
        proof["pairing"]["construction_proof_sha256"]
        == construction["construction_proof_sha256"]
    )
    assert "retains" not in proof["pairing"]


def test_memphant_query_only_fails_closed_on_tampered_or_out_of_order_proof(
    monkeypatch, tmp_path
):
    adapter, registry = load_memphant_adapter(monkeypatch)
    cli_bin = tmp_path / "cli"
    server_bin = tmp_path / "server"
    worker_bin = tmp_path / "worker"
    for path in (cli_bin, server_bin, worker_bin):
        path.write_bytes(b"fixture")
    for key, value in {
        "MEMPHANT_SCRATCH_ACTIVE": "1",
        "MEMPHANT_TEST_DATABASE_URL": "postgres://fixture",
        "MEMPHANT_LME_SERVER_URL": "http://fixture",
        "MEMPHANT_LME_PROOF_DIR": str(tmp_path / "proof"),
        "MEMPHANT_CLI_BIN": str(cli_bin),
        "MEMPHANT_LME_SERVER_BIN": str(server_bin),
        "MEMPHANT_LME_WORKER_BIN": str(worker_bin),
        "MEMPHANT_LME_RUN_ID": "fixture",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(adapter, "_create_api_key", lambda **_kwargs: "mk_clone")
    trajectory = {
        "id": "trajectory-1",
        "goal": "goal",
        "outcome": None,
        "states": [
            {
                "url": "https://example.test",
                "action": None,
                "thought": None,
                "accessibility_tree": "state",
            }
        ],
    }
    config = json.loads(MEMPHANT_CONFIG.read_text())["memory_params"]
    core = {
        "schema_version": 2,
        "binding_sha256": "9" * 64,
        "authorization": {
            "authorization_sha256": "a" * 64,
            "campaign_sha256": "b" * 64,
            "screen_id": "state-aware-full",
        },
        "selection": {
            "selection_sha256": "c" * 64,
            "input_manifest_sha256": "d" * 64,
            "state_mode": "structured-resource-v1",
        },
        "compiler": {
            "adapter_sha256": hashlib.sha256(MEMPHANT_ADAPTER.read_bytes()).hexdigest(),
            "construction_params_sha256": adapter._construction_params_sha256(config),
            "prompt_sha256": "e" * 64,
            "schema_sha256": "f" * 64,
            "provider_code_sha256": "1" * 64,
            "binaries": {
                "server": adapter._binary_fingerprint(str(server_bin)),
                "cli": adapter._binary_fingerprint(str(cli_bin)),
                "worker": adapter._binary_fingerprint(str(worker_bin)),
            },
        },
        "provider": {
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": "qwen/qwen3.5-9b",
            "requested_provider": "deepinfra",
            "served_provider": "DeepInfra",
            "input_price_nanos_per_million": 100_000_000,
            "output_price_nanos_per_million": 150_000_000,
            "maximum_output_tokens": 4096,
            "maximum_attempts": 3,
        },
        "cache": {"namespace": "fixture-v1", "source_receipts_sha256": "2" * 64},
        "ledger": {
            "attempt_ids": ["attempt-1"],
            "before_event_sha256": "3" * 64,
            "after_event_sha256": "4" * 64,
            "campaign_journal_sha256": "5" * 64,
            "settled_nanos": 1,
            "unresolved_nanos": 0,
        },
        "isolation": {
            "tenant_id": "00000000-0000-0000-0000-000000000111",
            "instance_id": "frozen-instance",
            "context": {
                "subject_id": "00000000-0000-0000-0000-000000000201",
                "scope_id": "00000000-0000-0000-0000-000000000202",
                "actor_id": "00000000-0000-0000-0000-000000000203",
                "agent_node_id": "00000000-0000-0000-0000-000000000204",
                "subject_generation": 0,
            },
        },
        "pairing": {
            "trajectory_count": 1,
            "resource_count": 1,
            "worker": {
                "completed_sources": 1,
                "stdout_sha256": "d" * 64,
                "stderr_sha256": "e" * 64,
            },
            "retains": [
                {
                    "trajectory_id": "trajectory-2",
                    "trajectory_sha256": adapter._sha256_json(
                        {**trajectory, "id": "trajectory-2"}
                    ),
                    "state_count": 1,
                    "canonical_body_bytes": 1,
                    "canonical_body_sha256": "f" * 64,
                    "fragments": [
                        {
                            "fragment_index": 1,
                            "resource_id": "resource-1",
                            "body_bytes": 1,
                            "serialized_request_bytes": 1,
                            "resource_body_sha256": "1" * 64,
                            "request_sha256": "2" * 64,
                            "idempotency_key_sha256": "3" * 64,
                            "response_sha256": "4" * 64,
                        }
                    ],
                }
            ],
        },
    }
    sealed = {
        **core,
        "construction_proof_sha256": adapter._sha256_json(core),
    }
    path = tmp_path / "construction.json"
    path.write_text(json.dumps(sealed))
    monkeypatch.setenv("MEMPHANT_LME_PREBUILT_PROOF", str(path))
    memory = registry["memphant"](config)

    with pytest.raises(RuntimeError, match="trajectory order or identity mismatch"):
        memory.insert(trajectory)

    sealed["pairing"]["resource_count"] = 2
    path.write_text(json.dumps(sealed))
    with pytest.raises(RuntimeError, match="construction proof sha256 mismatch"):
        registry["memphant"](config)

    sealed["pairing"]["resource_count"] = 1
    sealed["compiler"]["adapter_sha256"] = "0" * 64
    sealed["construction_proof_sha256"] = adapter._sha256_json(
        {
            key: value
            for key, value in sealed.items()
            if key != "construction_proof_sha256"
        }
    )
    path.write_text(json.dumps(sealed))
    with pytest.raises(RuntimeError, match="construction proof adapter mismatch"):
        registry["memphant"](config)


def test_memphant_memory_fails_closed_when_worker_pairing_is_incomplete(
    monkeypatch, tmp_path
):
    adapter, registry = load_memphant_adapter(monkeypatch)
    install_construction_binding(adapter, monkeypatch, tmp_path)
    cli_bin = tmp_path / "cli"
    server_bin = tmp_path / "server"
    worker_bin = tmp_path / "worker"
    cli_bin.write_bytes(b"fixture-cli")
    server_bin.write_bytes(b"fixture-server")
    worker_bin.write_bytes(b"fixture-worker")
    for key, value in {
        "MEMPHANT_SCRATCH_ACTIVE": "1",
        "MEMPHANT_TEST_DATABASE_URL": "postgres://fixture",
        "MEMPHANT_LME_SERVER_URL": "http://fixture",
        "MEMPHANT_LME_PROOF_DIR": str(tmp_path),
        "MEMPHANT_CLI_BIN": str(cli_bin),
        "MEMPHANT_LME_SERVER_BIN": str(server_bin),
        "MEMPHANT_LME_WORKER_BIN": str(worker_bin),
        "MEMPHANT_LME_RUN_ID": "fixture",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        adapter,
        "_provision_tenant",
        lambda **kwargs: ("00000000-0000-0000-0000-000000000111", "mk_key"),
    )
    monkeypatch.setattr(
        adapter,
        "_provision_context",
        lambda client, instance_id: {
            "subject_id": "00000000-0000-0000-0000-000000000201",
            "scope_id": "00000000-0000-0000-0000-000000000202",
            "actor_id": "00000000-0000-0000-0000-000000000203",
            "agent_node_id": "00000000-0000-0000-0000-000000000204",
            "subject_generation": 0,
        },
    )
    memory = registry["memphant"](
        json.loads(MEMPHANT_CONFIG.read_text())["memory_params"]
    )
    monkeypatch.setattr(
        memory.client,
        "request",
        lambda method, path, payload=None: {
            "resource_id": "resource",
            "enqueued": ["compile"],
        },
    )
    monkeypatch.setattr(
        adapter,
        "_drain_worker",
        lambda worker_bin, database_url, expected: (_ for _ in ()).throw(
            RuntimeError("worker compiled 0 sources, expected 1")
        ),
    )
    memory.insert(
        {
            "id": "trajectory",
            "goal": "goal",
            "outcome": None,
            "start_url": "https://example.test",
            "states": [
                {
                    "url": "https://example.test",
                    "action": None,
                    "thought": None,
                    "accessibility_tree": "state",
                    "screenshot": "unused.png",
                }
            ],
        }
    )
    memory.set_query_context(question_id="q", question_item={"answer": "secret"})

    with pytest.raises(RuntimeError, match="worker compiled 0 sources, expected 1"):
        memory.query("query")


def test_memphant_harness_command_bootstraps_adapter_without_patching_upstream(
    tmp_path,
):
    adapter = load_adapter()
    command = adapter.memphant_harness_command(
        official_dir=tmp_path / "official",
        domain="enterprise",
        questions_path=tmp_path / "questions.json",
        haystack_path=tmp_path / "haystack.json",
        trajectories_path=tmp_path / "trajectories.jsonl",
        memory_config_path=MEMPHANT_CONFIG,
        output_dir=tmp_path / "out",
        reader_model="reader",
        reader_base_url="http://reader/v1",
        evaluator_model="judge",
        evaluator_base_url="http://judge/v1",
        python="python3",
    )

    assert command[:2] == ["python3", str(MEMPHANT_BOOTSTRAP)]
    assert command[command.index("--official-dir") + 1] == str(tmp_path / "official")
    assert command[command.index("--memory-config-path") + 1] == str(MEMPHANT_CONFIG)
    assert command[command.index("--memory-context-max-tokens") + 1] == "200000"


def test_execution_matrix_requires_complete_paired_domains_tiers_and_binary_proof():
    adapter = load_adapter()
    digest = "a" * 64
    runs = []
    for domain in ("web", "enterprise"):
        for tier in ("small", "medium"):
            for arm in ("memphant_fast", "memphant_deep", "no_retrieval"):
                row = {
                    "domain": domain,
                    "tier": tier,
                    "arm": arm,
                    "question_count": 10,
                    "completed_questions": 10,
                    "error_count": 0,
                    "question_ids_sha256": digest,
                    "reader_contract_sha256": digest,
                    "judge_contract_sha256": digest,
                    "memory_context_max_tokens": 200000,
                    "output_sha256": digest,
                }
                if arm.startswith("memphant_"):
                    row["binaries"] = {
                        "server": {"path": "/bin/server", "bytes": 1, "sha256": digest},
                        "cli": {"path": "/bin/cli", "bytes": 1, "sha256": digest},
                    }
                runs.append(row)
    matrix = {
        "schema_version": 1,
        "benchmark": "LongMemEval-V2",
        "upstream_release_lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
        "runs": runs,
    }

    assert adapter.verify_execution_matrix(matrix) == {"runs": 12, "paired_cells": 4}
    incomplete = json.loads(json.dumps(matrix))
    incomplete["runs"].pop()
    with pytest.raises(RuntimeError, match="incomplete"):
        adapter.verify_execution_matrix(incomplete)
    drifted = json.loads(json.dumps(matrix))
    drifted["runs"][0]["reader_contract_sha256"] = "b" * 64
    with pytest.raises(RuntimeError, match="not paired|reader contract drift"):
        adapter.verify_execution_matrix(drifted)


def test_memphant_adapter_artifacts_match_immutable_contract():
    lock = json.loads(MEMPHANT_ADAPTER_LOCK.read_text())
    for relative, expected in lock["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
    assert (
        hashlib.sha256(
            (ROOT / "benchmarks/manifests/longmemeval_v2.lock.json").read_bytes()
        ).hexdigest()
        == lock["upstream_release_lock_sha256"]
    )
    # This is an immutable historical campaign lock, not the current public
    # schema drift gate. The campaign was frozen against the OpenAPI bytes at
    # MemPhant f5e90dc0; current OpenAPI evolution is independently enforced by
    # memphant-server's generator snapshot. Requiring today's generated file to
    # retain this historical digest made every legitimate public contract
    # addition look like campaign corruption and invited a forbidden re-pin.
    assert lock["openapi_sha256"] == (
        "a5bac765d7c4c862a342d95b49049c27d3af57aea9f80af6d3a0a489ac055271"
    )
    assert lock["paid_models_run"] is False
    fast = json.loads(MEMPHANT_FAST_CONFIG.read_text())
    deep = json.loads(MEMPHANT_CONFIG.read_text())
    assert fast["memory_params"] == deep["memory_params"] | {"mode": "fast"}


def test_runtime_materializer_uses_official_selection_and_proves_complete_pairing(
    tmp_path,
):
    official = tmp_path / "official"
    data_package = official / "data"
    data_package.mkdir(parents=True)
    (data_package / "__init__.py").write_text("")
    (data_package / "public_data.py").write_text(
        """
import json

def materialize_runtime_questions(*, data_root, domain, question_ids, limit, output_path):
    assert question_ids == ["q1"] and limit is None
    rows = [{"id": "q1", "domain": domain, "question": "query", "answer": "SECRET_REFERENCE_VALUE"}]
    output_path.write_text(json.dumps(rows))
    return rows

def materialize_runtime_haystack(*, data_root, tier, selected_questions, output_path):
    value = {"q1": ["t1", "t2"]}
    output_path.write_text(json.dumps(value))
    return value
""".strip()
        + "\n"
    )
    data_root = tmp_path / "dataset"
    data_root.mkdir()
    (data_root / "trajectories.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"id": "t1", "domain": "web", "states": [{}]}),
                json.dumps({"id": "t2", "domain": "web", "states": [{}]}),
            ]
        )
        + "\n"
    )
    manifest = tmp_path / "fixture.lock.json"
    manifest.write_text(
        json.dumps(
            {
                "code": {"commit": "fixture", "files": {}},
                "dataset": {
                    "revision": "fixture",
                    "files": {
                        "trajectories.jsonl": {
                            "bytes": (data_root / "trajectories.jsonl").stat().st_size,
                            "sha256": hashlib.sha256(
                                (data_root / "trajectories.jsonl").read_bytes()
                            ).hexdigest(),
                        }
                    },
                },
            }
        )
    )
    output = tmp_path / "runtime"
    result = os.spawnv(
        os.P_WAIT,
        sys.executable,
        [
            sys.executable,
            str(MATERIALIZER),
            "--official-dir",
            str(official),
            "--data-root",
            str(data_root),
            "--domain",
            "web",
            "--tier",
            "small",
            "--question-id",
            "q1",
            "--output-dir",
            str(output),
            "--manifest",
            str(manifest),
        ],
    )

    assert result == 0
    pairing = json.loads((output / "pairing.json").read_text())
    assert pairing["question_id"] == "q1"
    assert pairing["trajectory_count"] == 2
    assert [row["trajectory_id"] for row in pairing["trajectories"]] == ["t1", "t2"]
    assert "SECRET_REFERENCE_VALUE" not in json.dumps(pairing)
    assert json.loads((output / "memory_config.json").read_text()) == json.loads(
        MEMPHANT_CONFIG.read_text()
    )


@pytest.mark.skipif(
    os.environ.get("MEMPHANT_LME_PACKAGED_INTEGRATION") != "1",
    reason="requires packaged binaries and an ephemeral migrated Postgres database",
)
def test_memphant_memory_tiny_packaged_rest_dry_run(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import gate_runtime

    database_url = os.environ["MEMPHANT_TEST_DATABASE_URL"]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = gate_runtime.Server(
        str(ROOT / "target/debug/memphant-server"),
        database_url,
        port,
        log_path=tmp_path / "server.log",
    )
    server.start()
    try:
        adapter, registry = load_memphant_adapter(monkeypatch)
        monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
        monkeypatch.setenv("MEMPHANT_LME_SERVER_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("MEMPHANT_LME_PROOF_DIR", str(tmp_path / "proof"))
        monkeypatch.setenv("MEMPHANT_CLI_BIN", str(ROOT / "target/debug/memphant-cli"))
        monkeypatch.setenv(
            "MEMPHANT_LME_SERVER_BIN", str(ROOT / "target/debug/memphant-server")
        )
        monkeypatch.setenv(
            "MEMPHANT_LME_WORKER_BIN", str(ROOT / "target/debug/memphant-worker")
        )
        monkeypatch.setenv("MEMPHANT_LME_RUN_ID", "packaged-dry-run")
        memory_params = json.loads(MEMPHANT_CONFIG.read_text())["memory_params"]
        memory_params["mode"] = "fast"
        memory = registry["memphant"](memory_params)
        memory.insert(
            {
                "id": "fixture-trajectory",
                "goal": "Remember the launch code",
                "outcome": "success",
                "start_url": "https://example.test",
                "states": [
                    {
                        "url": "https://example.test/code",
                        "action": "read launch code",
                        "thought": "store the exact value",
                        "accessibility_tree": "The launch code is ORCHID-17.",
                        "screenshot": "not-consumed.png",
                    }
                ],
            }
        )
        memory.set_query_context(
            question_id="fixture-question",
            question_item={"answer": "ORCHID-17", "eval_function": "exact"},
        )
        context = memory.query("What is the launch code?")
        metadata = memory.post_query_hook(
            query="What is the launch code?", query_image=None, memory_context=context
        )
        assert context and "ORCHID-17" in context[0]["value"]
        assert metadata["trace_id"]
        assert next((tmp_path / "proof").glob("*.json")).is_file()
    finally:
        server.stop()
