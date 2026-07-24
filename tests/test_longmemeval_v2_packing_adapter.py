from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "benchmarks/longmemeval_v2/memphant_packing_memory.py"
CONFIG_DIR = ROOT / "benchmarks/longmemeval_v2"
PACKING_LOCK = ROOT / "benchmarks/manifests/longmemeval_v2_packing_adapter.lock.json"


def _install_memory_module(monkeypatch):
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
    module = types.ModuleType("memory_modules.memory")
    module.Memory = Memory
    module.MemoryContextItem = dict
    module.register_memory = register_memory
    monkeypatch.setitem(sys.modules, "memory_modules", package)
    monkeypatch.setitem(sys.modules, "memory_modules.memory", module)
    return registry


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(name: str) -> dict[str, object]:
    return json.loads((CONFIG_DIR / name).read_text())["memory_params"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packing_adapter_lock_binds_every_implementation_file():
    lock = json.loads(PACKING_LOCK.read_text())
    assert lock["paid_models_run"] is False
    assert lock["status"] == "REJECTED_AT_FREE_EXACT_ABSTENTION_GATE_NOT_AUTHORIZABLE"
    assert lock["base_adapter_sha256"] == _sha256(
        ROOT / "benchmarks/longmemeval_v2/memphant_memory.py"
    )
    assert lock["upstream_release_lock_sha256"] == _sha256(
        ROOT / "benchmarks/manifests/longmemeval_v2.lock.json"
    )
    assert lock["base_adapter_lock_sha256"] == _sha256(
        ROOT / "benchmarks/manifests/longmemeval_v2_memphant_adapter.lock.json"
    )
    for relative, expected in lock["files"].items():
        assert _sha256(ROOT / relative) == expected, relative


def test_packing_configs_bind_equal_positive_recall_and_reader_budgets(monkeypatch):
    _install_memory_module(monkeypatch)
    adapter = _load(ADAPTER, "packing_adapter_config")
    for path in CONFIG_DIR.glob("memphant.packing-*.memory.json"):
        params = json.loads(path.read_text())["memory_params"]
        base, arm = adapter._validate_params(params)
        assert base["budget_tokens"] == 32768
        assert params["budget_tokens"] == params["reader_context_max_tokens"] == 8192
        assert arm == adapter.ARM_CONTRACTS[params["packing_arm"]]
    drifted = _config("memphant.packing-current.memory.json")
    drifted["reader_context_max_tokens"] = 4096
    with pytest.raises(RuntimeError, match="budgets must match"):
        adapter._validate_params(drifted)


def test_packing_query_binds_trace_flags_and_emits_receipt_provenance(monkeypatch, tmp_path):
    _install_memory_module(monkeypatch)
    adapter = _load(ADAPTER, "packing_adapter_query")
    memory = object.__new__(adapter.MemphantPackingMemory)
    memory.packing_params = _config("memphant.packing-cap1200.memory.json")
    memory.arm_contract = dict(adapter.ARM_CONTRACTS["cap1200"])
    memory.proof_dir = tmp_path
    memory.instance_id = "fixture"
    memory.tenant_id = "tenant-1"
    memory.construction_proof = {
        "pairing": {
            "retains": [
                {
                    "trajectory_id": "trajectory-1",
                    "fragments": [{"resource_id": "resource-1"}],
                }
            ]
        }
    }
    memory._last_query_proof = None
    memory._last_packing_query_proof = None
    verification = {"status": "verified", "receipt": {"contract_revision": "fixture"}}

    def fake_base_query(self, query, query_image=None):
        base_context = [{"type": "text", "value": "Evidence body"}]
        proof_path = tmp_path / "base.json"
        proof_path.write_text(
            json.dumps(
                {
                    "public": {
                        "recall_response": {
                            "items": [
                                {
                                    "unit_id": "unit-1",
                                    "body": "Evidence body",
                                    "suppression_labels": [],
                                }
                            ],
                            "citations": [
                                {
                                    "unit_id": "unit-1",
                                    "resource_id": "resource-1",
                                    "verification": verification,
                                }
                            ],
                        },
                        "trace": {
                            "feature_flags": [
                                "context_packing_abstention_enabled",
                                "pack_render_cap:1200",
                            ]
                        },
                    }
                }
            )
        )
        self._last_query_proof = {
            "question_id": "question-1",
            "query_sha256": adapter.hashlib.sha256(query.encode()).hexdigest(),
            "context_sha256": adapter._BASE._sha256_json(base_context),
            "proof_path": str(proof_path),
        }
        return base_context

    monkeypatch.setattr(adapter._BASE.MemphantMemory, "query", fake_base_query)
    context = memory.query("What is supported?")
    memory._queried_question_id = "question-1"
    metadata = memory.post_query_hook(
        query="What is supported?", query_image=None, memory_context=context
    )

    assert context[0]["value"].startswith("[memphant status=supported unit=unit-1")
    assert "resource=resource-1" in context[0]["value"]
    assert "trajectory=trajectory-1" in context[0]["value"]
    assert metadata["packing_disposition"] == "supported"
    companion = json.loads(Path(metadata["packing_proof_path"]).read_text())
    assert companion["packing"]["items"][0]["verification_sha256"]
    assert Path(companion["base"]["construction_proof_path"]).is_file()
    assert companion["contract"]["expected_pack_feature_flags"] == [
        "pack_render_cap:1200"
    ]
    assert memory._queried_question_id is None
    second_context = memory.query("What is supported again?")
    memory._queried_question_id = "question-2"
    second_metadata = memory.post_query_hook(
        query="What is supported again?",
        query_image=None,
        memory_context=second_context,
    )
    assert second_metadata["packing_disposition"] == "supported"
    assert memory._queried_question_id is None


def test_packing_query_fails_closed_on_wrong_server_arm(monkeypatch, tmp_path):
    _install_memory_module(monkeypatch)
    adapter = _load(ADAPTER, "packing_adapter_mismatch")
    memory = object.__new__(adapter.MemphantPackingMemory)
    memory.packing_params = _config("memphant.packing-cap1200.memory.json")
    memory.arm_contract = dict(adapter.ARM_CONTRACTS["cap1200"])
    memory.proof_dir = tmp_path
    memory.instance_id = "fixture"
    memory.tenant_id = "tenant-1"
    memory.construction_proof = {"pairing": {"retains": []}}
    memory._last_query_proof = None
    memory._last_packing_query_proof = None

    def fake_base_query(self, query, query_image=None):
        proof_path = tmp_path / "base.json"
        proof_path.write_text(
            json.dumps(
                {
                    "public": {
                        "recall_response": {"items": [], "citations": []},
                        "trace": {"feature_flags": []},
                    }
                }
            )
        )
        self._last_query_proof = {
            "question_id": "q",
            "query_sha256": adapter.hashlib.sha256(query.encode()).hexdigest(),
            "context_sha256": adapter._BASE._sha256_json([]),
            "proof_path": str(proof_path),
        }
        return []

    monkeypatch.setattr(adapter._BASE.MemphantMemory, "query", fake_base_query)
    with pytest.raises(RuntimeError, match="packing server arm mismatch"):
        memory.query("query")


@pytest.mark.skipif(
    os.environ.get("MEMPHANT_LME_PACKAGED_INTEGRATION") != "1",
    reason="requires packaged binaries and an ephemeral migrated Postgres database",
)
def test_cap1200_packing_adapter_tiny_packaged_rest_dry_run(monkeypatch, tmp_path):
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
        pack_render_cap=1200,
    )
    server.start()
    try:
        registry = _install_memory_module(monkeypatch)
        _load(ADAPTER, "packing_adapter_packaged")
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
        monkeypatch.setenv("MEMPHANT_LME_RUN_ID", "packing-cap1200-dry-run")
        memory_params = _config("memphant.packing-cap1200.memory.json")
        memory_params["mode"] = "fast"
        memory = registry["memphant_packing"](memory_params)
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
            query="What is the launch code?",
            query_image=None,
            memory_context=context,
        )

        assert context and "ORCHID-17" in context[0]["value"]
        assert context[0]["value"].startswith("[memphant status=supported")
        assert metadata["packing_arm"] == "cap1200"
        companion = json.loads(Path(metadata["packing_proof_path"]).read_text())
        assert companion["contract"]["expected_pack_feature_flags"] == [
            "pack_render_cap:1200"
        ]
        assert companion["packing"]["disposition"] == "supported"
        assert companion["packing"]["items"][0]["verification_sha256"]
    finally:
        server.stop()
