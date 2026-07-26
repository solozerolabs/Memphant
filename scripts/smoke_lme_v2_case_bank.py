#!/usr/bin/env python3
"""Secret-free real-binary smoke for the LongMemEval-V2 case-bank lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
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


def _runner():
    spec = importlib.util.spec_from_file_location("lme_state_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load state-aware runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    safe_environment = {"PATH": os.environ.get("PATH", "")}
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env if env is not None else safe_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({command[0]}): {completed.stderr.strip()}"
        )
    return completed.stdout


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _api(
    base_url: str,
    key: str,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=payload,
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
    if any("API_KEY" in key for key in environment):
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


def _admin_tenant_and_key(
    cli: Path, database_url: str, *, tenant_id: str | None = None
) -> tuple[str, str]:
    if tenant_id is None:
        output = _run(
            [
                str(cli),
                "admin",
                "create-tenant",
                "--name",
                f"lme-case-smoke-{time.time_ns()}",
                "--database-url",
                database_url,
            ]
        )
        match = re.search(r"tenant_created id=([^ ]+)", output)
        if match is None:
            raise RuntimeError("tenant creation omitted its identity")
        tenant_id = match.group(1)
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
    return tenant_id, key


def _validated_test_proof(
    runner, root: Path, binding_path: Path, tenant_id: str, fixture: dict[str, object]
) -> Path:
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binaries = {}
    for name in ("server", "cli", "worker"):
        path = ROOT / f"target/debug/memphant-{name}"
        binaries[name] = {
            "path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "sha256": runner._sha256_file(path),
        }
    events = [
        json.loads(line)
        for line in (root / "paid-source.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    result = next(event for event in events if event["event"] == "result")
    usage = result["usage"]
    settled_nanos = (
        int(usage["prompt_tokens"]) * 100_000_000
        + int(usage["completion_tokens"]) * 150_000_000
        + 999_999
    ) // 1_000_000
    core = {
        "schema_version": 2,
        "binding_sha256": binding["binding_sha256"],
        "authorization": {
            "authorization_sha256": fixture["authorization_sha256"],
            "campaign_sha256": fixture["campaign_sha256"],
            "screen_id": "state-aware-full",
        },
        "selection": {
            "selection_sha256": "c" * 64,
            "input_manifest_sha256": "d" * 64,
            "state_mode": "structured-resource-v1",
        },
        "compiler": {
            "adapter_sha256": runner._sha256_file(
                ROOT / "benchmarks/longmemeval_v2/memphant_memory.py"
            ),
            "construction_params_sha256": "6" * 64,
            "prompt_sha256": runner._sha256_file(
                ROOT / "config/structured-state-v1.txt"
            ),
            "schema_sha256": "1" * 64,
            "provider_code_sha256": runner._sha256_file(
                ROOT / "crates/memphant-runtime/src/structured_state_openrouter.rs"
            ),
            "binaries": binaries,
        },
        "provider": {
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": fixture["served_model"],
            "requested_provider": "deepinfra",
            "served_provider": fixture["served_provider"],
            "input_price_nanos_per_million": 100_000_000,
            "output_price_nanos_per_million": 150_000_000,
            "maximum_output_tokens": 4096,
            "maximum_attempts": 1,
        },
        "cache": {
            "namespace": fixture["cache_namespace"],
            "source_receipts_sha256": runner.sha256_json(
                sorted(
                    runner._sha256_file(path)
                    for path in (root / "cache-hits").glob("*.json")
                )
            ),
        },
        "ledger": {
            "attempt_ids": sorted({event["attempt_id"] for event in events}),
            "before_event_sha256": hashlib.sha256(b"").hexdigest(),
            "after_event_sha256": fixture["source_ledger_prefix_sha256"],
            "campaign_journal_sha256": hashlib.sha256(b"").hexdigest(),
            "settled_nanos": settled_nanos,
            "unresolved_nanos": 0,
        },
        "isolation": {"tenant_id": tenant_id, "scratch": True},
        "pairing": {"trajectory_count": 1, "resource_count": 1},
    }
    proof = {**core, "construction_proof_sha256": runner.sha256_json(core)}
    runner.validate_construction_proof_v2(proof)
    path = root / "construction-proof-v2.json"
    path.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def smoke(
    base_database_url: str, *, artifact_root: Path | None = None
) -> dict[str, object]:
    runner = _runner()
    question_id = "synthetic-cache-only-resource-case"
    contract = runner.scratch_case_database_contract(base_database_url, question_id)
    manifest = json.loads(
        (ROOT / "benchmarks/manifests/longmemeval_v2.state_aware_full.v1.json").read_text()
    )
    construction = manifest["construction"]
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
        fixture_env = {
            "PATH": os.environ.get("PATH", ""),
            "MEMPHANT_TEST_CACHE_FIXTURE_ROOT": str(root),
            "MEMPHANT_TEST_CACHE_FIXTURE_PROMPT": str(
                ROOT / construction["prompt_path"]
            ),
            "MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER": str(
                DATA_ROOT / construction["tokenizer_path"]
            ),
            "MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER_CONFIG": str(
                DATA_ROOT / construction["tokenizer_config_path"]
            ),
        }
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
            env=fixture_env,
        )
        fixture = json.loads((root / "fixture.json").read_text())
        if fixture != {**fixture, "provider_credentials_read": False}:
            raise RuntimeError("fixture generator credential contract drift")
        try:
            _run(["dropdb", f"--maintenance-db={base_database_url}", "--if-exists", "--force", source_name])
            _run(["createdb", f"--maintenance-db={base_database_url}", source_name])
            _run([sys.executable, str(ROOT / "scripts/apply_memphant_migrations.py"), "--database-url", source_url])
            tenant_id, key = _admin_tenant_and_key(cli, source_url)
            cache_environment = {
                "MEMPHANT_STRUCTURED_STATE": "on",
                "MEMPHANT_STRUCTURED_STATE_MODEL": "qwen/qwen3.5-9b-20260310",
                "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH": str(ROOT / construction["prompt_path"]),
                "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION": "100000000",
                "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION": "150000000",
                "MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH": str(DATA_ROOT / construction["tokenizer_path"]),
                "MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH": str(DATA_ROOT / construction["tokenizer_config_path"]),
                "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER": str(root / "paid-source.jsonl"),
                "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE": str(root / "observation-cache"),
                "MEMPHANT_STRUCTURED_STATE_CACHE_HITS": str(root / "cache-hits"),
                "MEMPHANT_STRUCTURED_STATE_AUTHORIZATION_SHA256": fixture["authorization_sha256"],
                "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_SHA256": fixture["campaign_sha256"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_NAMESPACE": fixture["cache_namespace"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER": str(root / "paid-source.jsonl"),
                "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_BYTES": str(fixture["source_ledger_prefix_bytes"]),
                "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_SHA256": fixture["source_ledger_prefix_sha256"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_MODEL": fixture["served_model"],
                "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_PROVIDER": fixture["served_provider"],
                "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS": "18446744073709551615",
                "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT": "1",
                "MEMPHANT_STRUCTURED_STATE_CACHE_ONLY": "on",
            }
            source_server, base_url = _start_server(server, source_url, cache_environment)
            context = _api(base_url, key, "PUT", "/v1/context-bindings/lme-case-smoke", {
                "subject": {"external_ref": "subject:lme-case-smoke", "kind": "user"},
                "actor": {"external_ref": "actor:lme-case-smoke", "kind": "system"},
                "scope": {"external_ref": "scope:lme-case-smoke", "kind": "user_root"},
                "agent_node": {"external_ref": "agent:lme-case-smoke"},
            })
            context_fields = {
                "subject_id": context["subject_id"],
                "subject_generation": context["subject_generation"],
                "scope_id": context["scope_id"],
                "actor_id": context["actor_id"],
                "agent_node_id": context["agent_node_id"],
            }
            body = fixture["source_body"]
            retained = _api(base_url, key, "POST", "/v1/episodes", {
                **context_fields,
                "source_ref": "smoke:cache-only-resource",
                "observed_at": "2026-07-26T00:00:00Z",
                "payload": {"resource": {
                    "uri": "repo://smoke/profile.txt",
                    "mime_type": "text/plain",
                    "content_hash": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                    "kind": "code",
                    "revision": "fixture-v1",
                    "body": body,
                }},
            })
            queued_before_worker = _run(
                [
                    "psql",
                    source_url,
                    "-Atqc",
                    "SELECT state || ':' || job_type FROM memphant.job_state ORDER BY queue_order",
                ]
            )
            worker_environment = {
                "PATH": os.environ.get("PATH", ""),
                "MEMPHANT_WORKER_DATABASE_URL": source_url,
                "MEMPHANT_WORKER_ONCE": "1",
                **cache_environment,
            }
            if any("API_KEY" in name for name in worker_environment):
                raise RuntimeError("worker environment contains a provider credential")
            worker_output = _run([str(worker)], env=worker_environment)
            jobs_after_worker = _run(
                [
                    "psql",
                    source_url,
                    "-Atqc",
                    (
                        "SELECT state || ':' || job_type || ':' || "
                        "(run_after <= now())::text || ':' || coalesce(last_error,'') "
                        "FROM memphant.job_state ORDER BY queue_order"
                    ),
                ]
            )
            recall = _api(base_url, key, "POST", "/v1/recall", {
                **context_fields,
                "query": "Where do I live?",
            })
            if not recall.get("items") or "Oslo" not in json.dumps(recall["items"]):
                raise RuntimeError(
                    "cache-only compiled observation was not recalled: "
                    + json.dumps(
                        {
                            "worker": worker_output,
                            "retained": retained,
                            "queued_before_worker": queued_before_worker,
                            "jobs_after_worker": jobs_after_worker,
                            "cache_hits": [
                                path.name for path in (root / "cache-hits").glob("*.json")
                            ],
                            "recall": recall,
                        },
                        sort_keys=True,
                    )
                )
            query = urllib.parse.urlencode(context_fields)
            trace = _api(base_url, key, "GET", f"/v1/traces/{recall['trace_id']}?{query}")
            if not trace:
                raise RuntimeError("source trace smoke failed")
            _stop(source_server)
            source_server = None
            runner.assert_case_source_quiescent(source_url)
            logical_inventory = runner.database_logical_inventory(source_url)
            tool_identities = {
                tool: runner.postgres_tool_identity(source_url, tool)
                for tool in ("pg_dump", "pg_restore")
            }
            toolchain_core = {"identities": tool_identities}
            postgres_toolchain = {
                **toolchain_core,
                "toolchain_sha256": runner.sha256_json(toolchain_core),
            }
            pg_dump = tool_identities["pg_dump"]["path"]
            pg_restore = tool_identities["pg_restore"]["path"]
            archive = root / "case-bank.dump"
            _run(
                runner.case_bank_dump_command(
                    source_url, archive, pg_dump=str(pg_dump)
                )
            )
            binding_core = {"schema_version": 1, "coverage": {"plans": [fixture["extraction_key"]]}}
            binding = {**binding_core, "binding_sha256": runner.sha256_json(binding_core)}
            binding_path = root / "binding.json"
            binding_path.write_text(json.dumps(binding), encoding="utf-8")
            proof_path = _validated_test_proof(runner, root, binding_path, tenant_id, fixture)
            bank_manifest = runner.write_case_bank_manifest(
                archive=archive,
                output=root / "case-bank.json",
                contract=contract,
                binding_path=binding_path,
                construction_proof_path=proof_path,
                materialization={
                    "trajectory_count": 1,
                    "trajectory_ids_sha256": runner.sha256_json(["synthetic-trajectory"]),
                    "trajectory_content_sha256": hashlib.sha256(body.encode()).hexdigest(),
                },
                logical_inventory=logical_inventory,
                postgres_toolchain=postgres_toolchain,
            )
            clone = runner.restore_case_bank_pair(
                base_database_url=base_database_url,
                question_id=question_id,
                archive=archive,
                manifest=bank_manifest,
                pg_restore=str(pg_restore),
            )
            clone_traces = {}
            for arm, database_name in clone["databases"].items():
                database_url = runner._database_url_for_name(base_database_url, database_name)
                _, clone_key = _admin_tenant_and_key(cli, database_url, tenant_id=tenant_id)
                process, clone_base = _start_server(server, database_url, {})
                try:
                    recalled = _api(clone_base, clone_key, "POST", "/v1/recall", {
                        **context_fields,
                        "query": "Where do I live?",
                    })
                    if not recalled.get("items") or "Oslo" not in json.dumps(recalled["items"]):
                        raise RuntimeError(f"{arm} clone recall failed")
                    traced = _api(clone_base, clone_key, "GET", f"/v1/traces/{recalled['trace_id']}?{query}")
                    clone_traces[arm] = bool(traced)
                finally:
                    _stop(process)
            report = {
                "status": "PASS",
                "provider_credentials_read": False,
                "cache_hit_receipts": len(list((root / "cache-hits").glob("*.json"))),
                "case_bank_sha256": bank_manifest["case_bank_sha256"],
                "clone_sha256": clone["clone_sha256"],
                "clone_traces": clone_traces,
                "postgres_toolchain_sha256": postgres_toolchain[
                    "toolchain_sha256"
                ],
                "postgres_toolchain": postgres_toolchain,
                "construction_proof_sha256": json.loads(
                    proof_path.read_text(encoding="utf-8")
                )["construction_proof_sha256"],
            }
            if artifact_root is not None:
                artifact_root.mkdir(parents=True, exist_ok=True)
                for source, name in (
                    (root / "fixture.json", "SYNTHETIC-CACHE-FIXTURE.json"),
                    (binding_path, "CONSTRUCTION-BINDING.json"),
                    (proof_path, "CONSTRUCTION-PROOF.v2.json"),
                    (root / "case-bank.json", "CASE-BANK-MANIFEST.json"),
                ):
                    shutil.copyfile(source, artifact_root / name)
                (artifact_root / "SMOKE-REPORT.json").write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
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
    parser.add_argument(
        "--base-database-url",
        default="postgresql://memphant:memphant@localhost:5432/memphant",
    )
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            smoke(args.base_database_url, artifact_root=args.artifact_root),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
