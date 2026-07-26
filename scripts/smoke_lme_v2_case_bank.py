#!/usr/bin/env python3
"""Secret-free real-binary proof for the LongMemEval-V2 case-bank lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts/run_lme_v2_state_aware.py"
DATA_ROOT = Path.home() / ".cache/memphant/longmemeval-v2"
FORBIDDEN_PROVIDER_ENV = {
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "DEEPINFRA_API_KEY",
}


def _runner():
    spec = importlib.util.spec_from_file_location("lme_state_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load state-aware runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    environment = env if env is not None else {"PATH": os.environ.get("PATH", "")}
    if FORBIDDEN_PROVIDER_ENV.intersection(environment):
        raise RuntimeError("synthetic smoke environment contains a provider credential")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({command[0]}): {completed.stderr.strip()}")
    return completed.stdout


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_server(
    server: Path, database_url: str, extra_environment: dict[str, str]
) -> tuple[subprocess.Popen[bytes], str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "MEMPHANT_APP_DATABASE_URL": database_url,
        "MEMPHANT_AUTHN_DATABASE_URL": database_url,
        "MEMPHANT_BIND": f"127.0.0.1:{port}",
        **extra_environment,
    }
    if FORBIDDEN_PROVIDER_ENV.intersection(environment):
        raise RuntimeError("smoke server environment contains a provider credential")
    process = subprocess.Popen(
        [str(server)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(120):
        if process.poll() is not None:
            stderr = process.stderr.read().decode(errors="replace")
            raise RuntimeError(f"server exited during startup: {stderr}")
        try:
            with urllib.request.urlopen(base_url + "/v1/health", timeout=1):
                return process, base_url
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    process.terminate()
    raise RuntimeError("server did not become healthy")


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _api(
    base_url: str,
    key: str,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"case-bank-smoke-{time.time_ns()}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"API {method} {path} failed: HTTP {error.code}: "
            f"{error.read().decode(errors='replace')}"
        ) from error


def _admin_key(cli: Path, database_url: str, tenant_id: str) -> str:
    key = _run(
        [
            str(cli),
            "admin",
            "create-key",
            "--tenant",
            tenant_id,
            "--max-trust",
            "trusted_system",
            "--database-url",
            database_url,
        ]
    ).strip().splitlines()[-1]
    if not key:
        raise RuntimeError("key creation failed")
    return key


def _emit_cache_fixture(
    *,
    root: Path,
    construction: dict[str, object],
    body: str,
    quote: str,
    authorization_sha256: str | None = None,
    campaign_sha256: str | None = None,
    namespace: str = "scratch-cache-fixture-v1",
) -> dict[str, object]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "MEMPHANT_TEST_CACHE_FIXTURE_ROOT": str(root),
        "MEMPHANT_TEST_CACHE_FIXTURE_PROMPT": str(ROOT / str(construction["prompt_path"])),
        "MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER": str(DATA_ROOT / str(construction["tokenizer_path"])),
        "MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER_CONFIG": str(DATA_ROOT / str(construction["tokenizer_config_path"])),
        "MEMPHANT_TEST_CACHE_FIXTURE_BODY": body,
        "MEMPHANT_TEST_CACHE_FIXTURE_QUOTE": quote,
        "MEMPHANT_TEST_CACHE_NAMESPACE": namespace,
    }
    if authorization_sha256 is not None:
        environment["MEMPHANT_TEST_CACHE_AUTHORIZATION_SHA256"] = authorization_sha256
    if campaign_sha256 is not None:
        environment["MEMPHANT_TEST_CACHE_CAMPAIGN_SHA256"] = campaign_sha256
    _run(
        [
            "cargo",
            "test",
            "-q",
            "-p",
            "memphant-runtime",
            "emit_cache_only_resource_fixture_for_scratch_campaign",
            "--",
            "--ignored",
        ],
        env=environment,
    )
    return json.loads((root / "fixture.json").read_text(encoding="utf-8"))


def _synthetic_authority(
    runner,
    *,
    artifact_root: Path,
    canonical_manifest: dict[str, object],
    plan_fixture: dict[str, object],
    source_body: str,
) -> dict[str, object]:
    paths = {
        key: Path(value)
        for key, value in runner._campaign_artifact_paths(artifact_root).items()
    }
    for directory in (
        paths["observation_cache"],
        paths["cache_hits"],
        paths["construction_bindings"],
    ):
        directory.mkdir(parents=True, exist_ok=True)
    paths["journal"].write_bytes(b"")
    construction = json.loads(json.dumps(canonical_manifest["construction"]))
    construction["prompt_sha256"] = runner._sha256_file(ROOT / construction["prompt_path"])
    construction["code_sha256s"] = {
        relative: runner._sha256_file(ROOT / relative)
        for relative in construction["code_paths"]
    }
    manifest = {**canonical_manifest, "construction": construction}
    manifest_path = artifact_root / "SYNTHETIC-MANIFEST.json"
    _write_json(manifest_path, manifest)
    input_row = {
        "source_kind": "resource",
        "source_body": source_body,
        "source_body_sha256": hashlib.sha256(source_body.encode()).hexdigest(),
    }
    paths["construction_input"].write_bytes(runner.canonical_json(input_row) + b"\n")
    plan = {
        "extraction_key": plan_fixture["extraction_key"],
        "request_sha256": plan_fixture["request_sha256"],
        "per_attempt_reservation_nanos": plan_fixture["per_attempt_reservation_nanos"],
        "requested_model": construction["model"],
        "maximum_attempts": plan_fixture["maximum_attempts"],
        "source_kind": plan_fixture["source_kind"],
        "source_body_sha256": plan_fixture["source_body_sha256"],
        "batch_index": plan_fixture["batch_index"],
        "evidence_slices_sha256": plan_fixture["evidence_slices_sha256"],
    }
    plans, first_liability, plans_sha256 = runner._plan_inventory([plan])
    census_core = {
        "schema_version": 1,
        "benchmark": {"name": "synthetic-case-bank-smoke", "questions": 1},
        "construction": {
            "plan_inventory": plans,
            "plan_inventory_sha256": plans_sha256,
            "processed_plans": 1,
            "first_attempt_liability_nanos": first_liability,
            "construction_identity_sha256": runner.sha256_json(construction),
            "input_manifest_sha256": runner._sha256_file(paths["construction_input"]),
        },
        "manifest_sha256": runner._sha256_file(manifest_path),
        "paid_models_run": False,
        "spend_nanos": 0,
    }
    census = {**census_core, "census_sha256": runner.sha256_json(census_core)}
    census_path = artifact_root / "SYNTHETIC-CENSUS.json"
    _write_json(census_path, census)
    wave_core = {
        "schema_version": 1,
        "campaign_census_sha256": census["census_sha256"],
        "ordered_plans_sha256": plans_sha256,
        "plans": plans,
    }
    wave = {**wave_core, "wave_sha256": runner.sha256_json(wave_core)}
    _write_json(paths["construction_wave"], wave)
    artifact_paths = {key: str(value.resolve()) for key, value in paths.items()}
    scope = {
        "campaign": {
            "journal_path": paths["journal"].name,
            "hard_ceiling_nanos": 200_000_000_000,
            "opening_liability_nanos": 0,
            "unallocated_reserve_nanos": 10_000_000_000,
            "opening_reservations": [],
            "aggregate_construction_reservation_nanos": first_liability,
        },
        "inputs": {
            "census_path": str(census_path.resolve()),
            "census_file_sha256": runner._sha256_file(census_path),
            "census_sha256": census["census_sha256"],
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": runner._sha256_file(manifest_path),
            "plan_inventory_sha256": plans_sha256,
            "plan_count": 1,
        },
        "provider_authority": {
            "synthetic": True,
            "transport": "in-process-fake-no-network",
            "requested_model": construction["model"],
            "served_model": construction["response_model"],
            "served_provider": "DeepInfra",
        },
        "artifacts": artifact_paths,
        "execution": {
            "cache_namespace": "scratch-cache-fixture-v1",
            "construction_max_workers": 1,
            "construction_hidden_retries": 0,
        },
    }
    authorization = {
        "schema_version": 1,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        **scope,
        "authorization": {"authorization_scope_sha256": runner.sha256_json(scope)},
    }
    authorization_path = artifact_root / "SYNTHETIC-AUTHORIZATION.json"
    _write_json(authorization_path, authorization)
    return {
        "paths": paths,
        "plans": plans,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "census": census,
        "census_path": census_path,
        "wave_path": paths["construction_wave"],
        "authorization": authorization,
        "authorization_path": authorization_path,
    }


def _public_binding_projection(binding: dict[str, object]) -> dict[str, object]:
    core = {
        "schema_version": 1,
        "binding_sha256": binding["binding_sha256"],
        "authorization": binding["authorization"],
        "selection": binding["selection"],
        "compiler": binding["compiler"],
        "provider": binding["provider"],
        "cache": {"namespace": binding["cache"]["namespace"]},
        "ledger": {
            key: binding["ledger"][key]
            for key in (
                "source_ledger_prefix_bytes",
                "source_ledger_prefix_sha256",
                "before_event_sha256",
                "campaign_journal_sha256",
            )
        },
        "coverage": binding["coverage"],
    }
    return {**core, "projection_sha256": hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def _redacted_toolchain(runner, toolchain: dict[str, object]) -> dict[str, object]:
    identities = {}
    for name, identity in toolchain["identities"].items():
        core = {
            **{key: value for key, value in identity.items() if key not in {"identity_sha256", "path"}},
            "path": f"postgres-client://{name}/{identity['version'].split()[-2]}",
        }
        identities[name] = {**core, "identity_sha256": runner.sha256_json(core)}
    core = {"identities": identities}
    return {**core, "toolchain_sha256": runner.sha256_json(core)}


def _assert_public_artifacts_clean(root: Path) -> None:
    forbidden = re.compile(r"(?:/Users/|/home/|/private/var/|/tmp/|\\Users\\|\.codex/worktrees)")
    placeholder = re.compile(r'"([0-9a-f])\1{63}"')
    for path in root.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        if forbidden.search(text):
            raise RuntimeError(f"public smoke artifact leaks a local path: {path.name}")
        if placeholder.search(text):
            raise RuntimeError(f"public smoke artifact contains a placeholder identity: {path.name}")


def smoke(base_database_url: str, *, artifact_root: Path | None = None) -> dict[str, object]:
    runner = _runner()
    adapter = runner._load_adapter()
    question_id = "synthetic-cache-only-resource-case"
    contract = runner.scratch_case_database_contract(base_database_url, question_id)
    canonical_manifest = json.loads(
        (ROOT / "benchmarks/manifests/longmemeval_v2.state_aware_full.v1.json").read_text()
    )
    construction = canonical_manifest["construction"]
    config = json.loads(
        (ROOT / "benchmarks/longmemeval_v2/memphant.fast.memory.json").read_text()
    )
    trajectory = {
        "id": "synthetic-trajectory",
        "goal": "Remember the user's stated city.",
        "states": [
            {
                "url": "https://example.invalid/profile",
                "action": "read profile",
                "thought": "Record only the stated location.",
                "text": "I live in Oslo.",
            }
        ],
        "outcome": "The location was recorded.",
    }
    _, _, canonical_body, fragments, trajectory_sha256 = adapter._validate_trajectory(trajectory, [])
    if len(fragments) != 1:
        raise RuntimeError("synthetic trajectory must produce exactly one resource")
    source_body = f"Trajectory fragment 1/1\n\n{fragments[0]}"
    server = ROOT / "target/debug/memphant-server"
    worker = ROOT / "target/debug/memphant-worker"
    cli = ROOT / "target/debug/memphant-cli"
    _run(["cargo", "build", "-q", "-p", "memphant-server", "-p", "memphant-worker", "-p", "memphant-cli"])
    source_name = contract["databases"]["source"]
    source_url = runner._database_url_for_name(base_database_url, source_name)
    cleanup_names = list(contract["databases"].values())
    source_server = None
    with tempfile.TemporaryDirectory(prefix="memphant-lme-case-bank-smoke-") as temporary:
        root = Path(temporary)
        planning_fixture = _emit_cache_fixture(
            root=root / "planning",
            construction=construction,
            body=source_body,
            quote="I live in Oslo.",
        )
        campaign_root = root / "campaign"
        authority = _synthetic_authority(
            runner,
            artifact_root=campaign_root,
            canonical_manifest=canonical_manifest,
            plan_fixture=planning_fixture,
            source_body=source_body,
        )
        fixture = _emit_cache_fixture(
            root=campaign_root,
            construction=authority["manifest"]["construction"],
            body=source_body,
            quote="I live in Oslo.",
            authorization_sha256=authority["authorization"]["authorization"]["authorization_scope_sha256"],
            campaign_sha256=authority["census"]["census_sha256"],
        )
        if fixture.get("provider_credentials_read") is not False:
            raise RuntimeError("fixture generator credential contract drift")
        binding_path, binding = runner._build_construction_binding(
            authorization_path=authority["authorization_path"],
            census_path=authority["census_path"],
            manifest_path=authority["manifest_path"],
            wave_path=authority["wave_path"],
            binding_root=authority["paths"]["construction_bindings"],
            plans=authority["plans"],
        )
        runner._create_json(binding_path, binding)
        binding_authority = {
            "authorization_path": authority["authorization_path"],
            "census_path": authority["census_path"],
            "manifest_path": authority["manifest_path"],
            "wave_path": authority["wave_path"],
            "binding_root": authority["paths"]["construction_bindings"],
        }
        if runner._load_canonical_construction_binding(binding_path, **binding_authority) != binding:
            raise RuntimeError("synthetic canonical binding validation drift")
        try:
            _run(["dropdb", f"--maintenance-db={base_database_url}", "--if-exists", "--force", source_name])
            _run(["createdb", f"--maintenance-db={base_database_url}", source_name])
            _run([sys.executable, str(ROOT / "scripts/apply_memphant_migrations.py"), "--database-url", source_url])
            cache_environment = runner.cache_only_construction_environment(
                binding_path=binding_path,
                binding=binding,
                manifest=authority["manifest"],
                data_root=DATA_ROOT,
                database_url=source_url,
            )
            source_server, base_url = _start_server(server, source_url, cache_environment)
            adapter.CANONICAL_AUTHORIZATION_PATH = authority["authorization_path"]
            adapter.CANONICAL_CENSUS_PATH = authority["census_path"]
            adapter.CANONICAL_MANIFEST_PATH = authority["manifest_path"]
            adapter.CANONICAL_WAVE_PATH = authority["wave_path"]
            adapter.CANONICAL_BINDING_ROOT = authority["paths"]["construction_bindings"]
            adapter_environment = {
                **cache_environment,
                "MEMPHANT_SCRATCH_ACTIVE": "1",
                "MEMPHANT_TEST_DATABASE_URL": source_url,
                "MEMPHANT_LME_SERVER_URL": base_url,
                "MEMPHANT_CLI_BIN": str(cli),
                "MEMPHANT_LME_SERVER_BIN": str(server),
                "MEMPHANT_LME_WORKER_BIN": str(worker),
                "MEMPHANT_LME_PROOF_DIR": str(campaign_root / "proof"),
                "MEMPHANT_LME_RUN_ID": "synthetic-case-bank-smoke",
                "MEMPHANT_RESOURCE_CHUNKS": "on",
            }
            previous = {key: os.environ.get(key) for key in adapter_environment}
            previous_provider = {key: os.environ.get(key) for key in FORBIDDEN_PROVIDER_ENV}
            try:
                for key in FORBIDDEN_PROVIDER_ENV:
                    os.environ.pop(key, None)
                os.environ.update(adapter_environment)
                memory = adapter.MemphantMemory(config["memory_params"])
                memory.get_query_context = lambda: {"question_id": question_id}
                memory.insert(trajectory)
                proof = memory.prepare()
                recalled = memory.query("Where does the user live?")
                if "Oslo" not in json.dumps(recalled):
                    raise RuntimeError("adapter cache-only recall did not return Oslo")
            finally:
                for key, value in {**previous, **previous_provider}.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            proof_path = next((campaign_root / "proof").glob("construction.*.v2.json"))
            if json.loads(proof_path.read_text()) != proof:
                raise RuntimeError("adapter construction proof artifact drift")
            _stop(source_server)
            source_server = None
            runner.assert_case_source_quiescent(source_url)
            logical_inventory = runner.database_logical_inventory(source_url)
            tool_identities = {
                tool: runner.postgres_tool_identity(source_url, tool)
                for tool in ("pg_dump", "pg_restore")
            }
            toolchain_core = {"identities": tool_identities}
            postgres_toolchain = {**toolchain_core, "toolchain_sha256": runner.sha256_json(toolchain_core)}
            archive = root / "case-bank.dump"
            _run(runner.case_bank_dump_command(source_url, archive, pg_dump=tool_identities["pg_dump"]["path"]))
            bank_manifest = runner.write_case_bank_manifest(
                archive=archive,
                output=root / "case-bank.json",
                contract=contract,
                binding_path=binding_path,
                binding_authority=binding_authority,
                construction_proof_path=proof_path,
                materialization={
                    "trajectory_count": 1,
                    "trajectory_ids_sha256": runner.sha256_json([trajectory["id"]]),
                    "trajectory_content_sha256": trajectory_sha256,
                },
                logical_inventory=logical_inventory,
                postgres_toolchain=postgres_toolchain,
            )
            clone = runner.restore_case_bank_pair(
                base_database_url=base_database_url,
                question_id=question_id,
                archive=archive,
                manifest=bank_manifest,
                pg_restore=tool_identities["pg_restore"]["path"],
            )
            context_fields = proof["isolation"]["context"]
            tenant_id = proof["isolation"]["tenant_id"]
            query = urllib.parse.urlencode(context_fields)
            clone_traces = {}
            for arm, database_name in clone["databases"].items():
                database_url = runner._database_url_for_name(base_database_url, database_name)
                key = _admin_key(cli, database_url, tenant_id)
                process, clone_base = _start_server(server, database_url, {})
                try:
                    response = _api(clone_base, key, "POST", "/v1/recall", {**context_fields, "query": "Where does the user live?"})
                    if "Oslo" not in json.dumps(response.get("items")):
                        raise RuntimeError(f"{arm} clone recall failed")
                    trace = _api(clone_base, key, "GET", f"/v1/traces/{response['trace_id']}?{query}")
                    clone_traces[arm] = bool(trace)
                finally:
                    _stop(process)
            public_toolchain = _redacted_toolchain(runner, postgres_toolchain)
            report = {
                "status": "PASS",
                "provider_credentials_read": False,
                "canonical_binding_valid": True,
                "canonical_receipts_valid": True,
                "cache_hit_receipts": len(list(Path(binding["cache"]["source_receipts_path"]).glob("*.json"))),
                "binding_sha256": binding["binding_sha256"],
                "construction_proof_sha256": proof["construction_proof_sha256"],
                "runtime_case_bank_sha256": bank_manifest["case_bank_sha256"],
                "clone_sha256": clone["clone_sha256"],
                "clone_traces": clone_traces,
                "postgres_toolchain": public_toolchain,
                "postgres_toolchain_sha256": public_toolchain["toolchain_sha256"],
                "trajectory_body_sha256": hashlib.sha256(canonical_body.encode()).hexdigest(),
                "source_body_sha256": hashlib.sha256(source_body.encode()).hexdigest(),
            }
            if artifact_root is not None:
                artifact_root.mkdir(parents=True, exist_ok=True)
                for obsolete in artifact_root.glob("*.json"):
                    obsolete.unlink()
                _write_json(artifact_root / "SYNTHETIC-CACHE-FIXTURE.json", fixture)
                _write_json(artifact_root / "CONSTRUCTION-BINDING-PROJECTION.json", _public_binding_projection(binding))
                _write_json(artifact_root / "CONSTRUCTION-PROOF.v2.json", proof)
                _write_json(artifact_root / "CASE-BANK-PROJECTION.json", {
                    "schema_version": 1,
                    "runtime_case_bank_sha256": bank_manifest["case_bank_sha256"],
                    "archive": bank_manifest["archive"],
                    "construction": bank_manifest["construction"],
                    "materialization": bank_manifest["materialization"],
                    "logical_inventory": bank_manifest["logical_inventory"],
                    "logical_inventory_sha256": bank_manifest["logical_inventory_sha256"],
                    "postgres_toolchain": public_toolchain,
                })
                _write_json(artifact_root / "SMOKE-REPORT.json", report)
                _assert_public_artifacts_clean(artifact_root)
            return report
        finally:
            _stop(source_server)
            for database_name in cleanup_names:
                subprocess.run(
                    ["dropdb", f"--maintenance-db={base_database_url}", "--if-exists", "--force", database_name],
                    cwd=ROOT,
                    env={"PATH": os.environ.get("PATH", "")},
                    capture_output=True,
                    check=False,
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-database-url", default="postgresql://memphant:memphant@localhost:5432/memphant")
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(smoke(args.base_database_url, artifact_root=args.artifact_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
