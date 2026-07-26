"""Official LongMemEval-V2 ``Memory`` adapter for packaged MemPhant REST.

One adapter instance owns one freshly provisioned tenant, scope, and actor and
accepts exactly one question. The official harness supplies trajectories via
``insert`` and the question via ``query``; gold answers and evaluator fields in
the harness query context are deliberately never read or transmitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from memory_modules.memory import Memory, MemoryContextItem, register_memory


EXPECTED_PARAMS = {
    "schema_version",
    "server_url_env",
    "database_url_env",
    "cli_bin_env",
    "server_bin_env",
    "worker_bin_env",
    "proof_dir_env",
    "run_id_env",
    "top_k",
    "budget_tokens",
    "mode",
    "source_kind",
    "source_trust",
    "compiler_version",
}
CONSTRUCTION_PARAM_KEYS = {
    "schema_version",
    "source_kind",
    "source_trust",
    "compiler_version",
}
FORBIDDEN_EVALUATION_KEYS = {
    "answer",
    "answer_gold",
    "eval_function",
    "gold",
    "reference",
}
TENANT_PATTERN = re.compile(r"tenant_created id=([0-9a-fA-F-]{36})")
# Keep each retain safely below the server request-body ceiling while preserving
# state boundaries. The runtime compiler owns bounded, complete chunk evidence;
# reducing this to chunk size would turn one large trajectory into thousands of
# resources and multiply writes/embeddings without improving source fidelity.
RESOURCE_FRAGMENT_BYTES = 1024 * 1024
MAX_SERIALIZED_RETAIN_BYTES = 1536 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120
# Correctness campaigns must not discard an otherwise valid row merely because
# a contended benchmark host exceeds the product-facing recall SLO. The proof
# still records the full recall duration; this wider client deadline is only a
# transport safety margin for the official benchmark adapter.
RECALL_REQUEST_TIMEOUT_SECONDS = 600


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _binary_fingerprint(path: str) -> dict[str, object]:
    binary = Path(path).resolve()
    _require(binary.is_file(), f"required packaged binary is missing: {binary}")
    return {
        "path": str(binary),
        "bytes": binary.stat().st_size,
        "sha256": _sha256_file(binary),
    }


def _required_env(name: object) -> str:
    _require(
        isinstance(name, str) and name, "environment variable name must be non-empty"
    )
    value = os.environ.get(name, "").strip()
    _require(bool(value), f"required environment variable is unset: {name}")
    return value


class _JsonClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> dict:
        body = None if payload is None else _canonical_json(payload)
        headers = dict(self.headers)
        if method != "GET":
            headers["Idempotency-Key"] = _idempotency_key(method, path, payload)
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read(512).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"MemPhant {path} returned HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"MemPhant {path} request failed: {error.reason}"
            ) from error
        except TimeoutError as error:
            raise RuntimeError(
                f"MemPhant {path} exceeded {timeout_seconds}s benchmark transport deadline"
            ) from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"MemPhant {path} returned invalid JSON") from error
        _require(isinstance(value, dict), f"MemPhant {path} response must be an object")
        return value


def _run_cli(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _idempotency_key(method: str, path: str, payload: object) -> str:
    digest = hashlib.sha256(
        method.encode() + b"\0" + path.encode() + b"\0" + _canonical_json(payload)
    ).hexdigest()
    return f"lme-v2-{digest}"


def _create_api_key(*, cli_bin: str, database_url: str, tenant_id: str) -> str:
    keyed = _run_cli(
        [
            cli_bin,
            "admin",
            "create-key",
            "--tenant",
            tenant_id,
            "--max-trust",
            "trusted_system",
            "--database-url",
            database_url,
        ]
    )
    _require(keyed.returncode == 0, f"create-key failed: {keyed.stderr.strip()}")
    api_key = keyed.stdout.strip().splitlines()[-1] if keyed.stdout.strip() else ""
    _require(api_key.startswith("mk_"), "create-key omitted MemPhant API key")
    return api_key


def _provision_tenant(*, cli_bin: str, database_url: str, name: str) -> tuple[str, str]:
    created = _run_cli(
        [
            cli_bin,
            "admin",
            "create-tenant",
            "--name",
            name,
            "--database-url",
            database_url,
        ]
    )
    _require(created.returncode == 0, f"create-tenant failed: {created.stderr.strip()}")
    match = TENANT_PATTERN.search(created.stdout)
    _require(match is not None, "create-tenant omitted tenant UUID")
    tenant_id = match.group(1)
    api_key = _create_api_key(
        cli_bin=cli_bin, database_url=database_url, tenant_id=tenant_id
    )
    return tenant_id, api_key


def _provision_context(client: _JsonClient, instance_id: str) -> dict[str, object]:
    bound = client.request(
        "PUT",
        f"/v1/context-bindings/lme-v2-{instance_id}",
        {
            "subject": {
                "external_ref": f"subject:lme-v2:{instance_id}",
                "kind": "user",
            },
            "actor": {"external_ref": f"actor:lme-v2:{instance_id}", "kind": "system"},
            "scope": {
                "external_ref": f"scope:lme-v2:{instance_id}",
                "kind": "user_root",
            },
            "agent_node": {"external_ref": f"agent:lme-v2:{instance_id}"},
        },
    )
    required = {
        "subject_id",
        "scope_id",
        "actor_id",
        "agent_node_id",
        "subject_generation",
    }
    _require(required <= set(bound), "context binding response is incomplete")
    return {key: bound[key] for key in sorted(required)}


def _validate_memory_params(memory_params: dict[str, object]) -> dict[str, object]:
    _require(
        set(memory_params) == EXPECTED_PARAMS, "memphant memory_params contract drift"
    )
    _require(
        memory_params["schema_version"] == 2, "unsupported memphant adapter schema"
    )
    _require(
        isinstance(memory_params["top_k"], int) and memory_params["top_k"] == 20,
        "LongMemEval-V2 top_k must remain fixed at 20",
    )
    _require(
        isinstance(memory_params["budget_tokens"], int)
        and memory_params["budget_tokens"] == 32768,
        "LongMemEval-V2 budget_tokens must remain fixed at 32768",
    )
    _require(
        memory_params["mode"] in {"fast", "deep"}, "recall mode must be fast or deep"
    )
    _require(
        memory_params["source_trust"] == "trusted_system", "source trust contract drift"
    )
    return dict(memory_params)


def _construction_params_sha256(params: dict[str, object]) -> str:
    return _sha256_json({key: params[key] for key in sorted(CONSTRUCTION_PARAM_KEYS)})


def _load_construction_proof(path_value: str) -> dict[str, object]:
    path = Path(path_value).resolve()
    _require(path.is_file(), f"prebuilt construction proof is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("prebuilt construction proof is not valid JSON") from error
    _require(isinstance(value, dict), "prebuilt construction proof must be an object")
    _require(
        set(value)
        == {
            "schema_version",
            "contract",
            "isolation",
            "pairing",
            "construction_proof_sha256",
        },
        "prebuilt construction proof contract drift",
    )
    expected_sha256 = value["construction_proof_sha256"]
    _require(
        isinstance(expected_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None,
        "construction proof sha256 is invalid",
    )
    core = {
        key: item for key, item in value.items() if key != "construction_proof_sha256"
    }
    _require(
        _sha256_json(core) == expected_sha256,
        "construction proof sha256 mismatch",
    )
    _require(value["schema_version"] == 1, "unsupported construction proof schema")

    contract = value["contract"]
    _require(isinstance(contract, dict), "construction proof contract is invalid")
    _require(
        set(contract) == {"adapter_sha256", "construction_params_sha256", "binaries"},
        "construction proof contract is invalid",
    )
    for key in ("adapter_sha256", "construction_params_sha256"):
        _require(
            isinstance(contract[key], str)
            and re.fullmatch(r"[0-9a-f]{64}", contract[key]) is not None,
            f"construction proof {key} is invalid",
        )
    binaries = contract["binaries"]
    _require(
        isinstance(binaries, dict) and set(binaries) == {"server", "cli", "worker"},
        "construction proof binary fingerprints are invalid",
    )
    for fingerprint in binaries.values():
        _require(
            isinstance(fingerprint, dict),
            "construction proof binary fingerprint is invalid",
        )
        _require(
            set(fingerprint) == {"path", "bytes", "sha256"}
            and isinstance(fingerprint["path"], str)
            and isinstance(fingerprint["bytes"], int)
            and fingerprint["bytes"] >= 0
            and isinstance(fingerprint["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", fingerprint["sha256"]) is not None,
            "construction proof binary fingerprint is invalid",
        )

    isolation = value["isolation"]
    _require(isinstance(isolation, dict), "construction proof isolation is invalid")
    _require(
        set(isolation) == {"tenant_id", "instance_id", "context"},
        "construction proof isolation is invalid",
    )
    _require(
        isinstance(isolation["tenant_id"], str)
        and isinstance(isolation["instance_id"], str)
        and isolation["instance_id"],
        "construction proof identity is invalid",
    )
    context = isolation["context"]
    required_context = {
        "subject_id",
        "scope_id",
        "actor_id",
        "agent_node_id",
        "subject_generation",
    }
    _require(
        isinstance(context, dict) and set(context) == required_context,
        "construction proof context is invalid",
    )
    _require(
        all(
            isinstance(context[key], str) and context[key]
            for key in required_context - {"subject_generation"}
        )
        and isinstance(context["subject_generation"], int),
        "construction proof context is invalid",
    )

    pairing = value["pairing"]
    _require(isinstance(pairing, dict), "construction proof pairing is invalid")
    _require(
        set(pairing) == {"trajectory_count", "resource_count", "worker", "retains"},
        "construction proof pairing is invalid",
    )
    retains = pairing["retains"]
    _require(
        isinstance(retains, list) and retains, "construction proof retains are invalid"
    )
    trajectory_ids: list[str] = []
    fragment_count = 0
    for retain in retains:
        _require(isinstance(retain, dict), "construction proof retain is invalid")
        _require(
            set(retain)
            == {
                "trajectory_id",
                "trajectory_sha256",
                "state_count",
                "canonical_body_bytes",
                "canonical_body_sha256",
                "fragments",
            },
            "construction proof retain is invalid",
        )
        trajectory_id = retain["trajectory_id"]
        _require(
            isinstance(trajectory_id, str)
            and trajectory_id
            and trajectory_id not in trajectory_ids,
            "construction proof trajectory ids are invalid",
        )
        trajectory_ids.append(trajectory_id)
        for key in ("trajectory_sha256", "canonical_body_sha256"):
            _require(
                isinstance(retain[key], str)
                and re.fullmatch(r"[0-9a-f]{64}", retain[key]) is not None,
                f"construction proof {key} is invalid",
            )
        _require(
            isinstance(retain["state_count"], int)
            and retain["state_count"] > 0
            and isinstance(retain["canonical_body_bytes"], int)
            and retain["canonical_body_bytes"] > 0,
            "construction proof retain counts are invalid",
        )
        fragments = retain["fragments"]
        _require(
            isinstance(fragments, list) and fragments,
            "construction proof fragments are invalid",
        )
        for fragment in fragments:
            _require(
                isinstance(fragment, dict)
                and set(fragment)
                == {
                    "fragment_index",
                    "resource_id",
                    "body_bytes",
                    "serialized_request_bytes",
                    "resource_body_sha256",
                    "request_sha256",
                    "idempotency_key_sha256",
                    "response_sha256",
                },
                "construction proof fragment is invalid",
            )
            _require(
                isinstance(fragment["fragment_index"], int)
                and fragment["fragment_index"] > 0
                and isinstance(fragment["resource_id"], str)
                and fragment["resource_id"]
                and isinstance(fragment["body_bytes"], int)
                and fragment["body_bytes"] > 0
                and isinstance(fragment["serialized_request_bytes"], int)
                and fragment["serialized_request_bytes"] > 0,
                "construction proof fragment counts are invalid",
            )
            for key in (
                "resource_body_sha256",
                "request_sha256",
                "idempotency_key_sha256",
                "response_sha256",
            ):
                _require(
                    isinstance(fragment[key], str)
                    and re.fullmatch(r"[0-9a-f]{64}", fragment[key]) is not None,
                    f"construction proof fragment {key} is invalid",
                )
        fragment_count += len(fragments)
    _require(
        pairing["trajectory_count"] == len(retains),
        "construction proof trajectory count mismatch",
    )
    _require(
        pairing["resource_count"] == fragment_count,
        "construction proof resource count mismatch",
    )
    worker = pairing["worker"]
    _require(
        isinstance(worker, dict)
        and set(worker) == {"completed_sources", "stdout_sha256", "stderr_sha256"},
        "construction proof worker is invalid",
    )
    _require(
        worker.get("completed_sources") == pairing["resource_count"],
        "construction proof worker count mismatch",
    )
    for key in ("stdout_sha256", "stderr_sha256"):
        _require(
            isinstance(worker[key], str)
            and re.fullmatch(r"[0-9a-f]{64}", worker[key]) is not None,
            f"construction proof worker {key} is invalid",
        )
    return value


def _validate_trajectory(
    trajectory: dict[str, object], inserted_trajectory_ids: list[str]
) -> tuple[str, list[dict[str, object]], str, list[str], str]:
    _require(isinstance(trajectory, dict), "trajectory must be an object")
    forbidden = FORBIDDEN_EVALUATION_KEYS.intersection(trajectory)
    _require(
        not forbidden, f"trajectory contains evaluator fields: {sorted(forbidden)}"
    )
    trajectory_id = trajectory.get("id")
    goal = trajectory.get("goal")
    states = trajectory.get("states")
    outcome = trajectory.get("outcome")
    _require(
        isinstance(trajectory_id, str) and trajectory_id, "trajectory id is missing"
    )
    _require(
        trajectory_id not in inserted_trajectory_ids, "duplicate trajectory insert"
    )
    _require(isinstance(goal, str), f"trajectory goal is invalid: {trajectory_id}")
    _require(
        isinstance(states, list) and states,
        f"trajectory states are missing: {trajectory_id}",
    )
    _require(
        outcome is None or isinstance(outcome, str), "trajectory outcome is invalid"
    )
    _require(
        all(isinstance(state, dict) for state in states),
        "trajectory states are invalid",
    )
    body = _trajectory_body(trajectory)
    fragments = _trajectory_fragments(trajectory)
    return trajectory_id, states, body, fragments, _sha256_json(trajectory)


def _state_body(
    trajectory: dict[str, object], state: dict[str, object], index: int
) -> str:
    forbidden = FORBIDDEN_EVALUATION_KEYS.intersection(state)
    _require(
        not forbidden,
        f"trajectory state contains evaluator fields: {sorted(forbidden)}",
    )
    url = state.get("url")
    action = state.get("action")
    thought = state.get("thought", state.get("thoughts"))
    observation = state.get("accessibility_tree", state.get("text"))
    _require(isinstance(url, str) and url.strip(), "trajectory state URL is missing")
    _require(
        action is None or isinstance(action, str), "trajectory state action is invalid"
    )
    _require(
        thought is None or isinstance(thought, str),
        "trajectory state thought is invalid",
    )
    _require(isinstance(observation, str), "trajectory state text is missing")
    goal = trajectory["goal"]
    trajectory_id = trajectory["id"]
    lines = [
        f"Trajectory: {trajectory_id}",
        f"Goal: {goal}",
        f"Step: {index}",
        f"URL: {url}",
    ]
    if action:
        lines.append(f"Action: {action}")
    if thought:
        lines.append(f"Thought: {thought}")
    lines.append("Observation:\n" + observation)
    states = trajectory["states"]
    if index == len(states) - 1 and trajectory.get("outcome"):
        lines.append(f"Outcome: {trajectory['outcome']}")
    return "\n".join(lines)


def _trajectory_body(trajectory: dict[str, object]) -> str:
    states = trajectory["states"]
    _require(isinstance(states, list) and states, "trajectory states are missing")
    return "\n\n---\n\n".join(
        _state_body(trajectory, state, index)
        for index, state in enumerate(states)
        if isinstance(state, dict)
    )


def _split_utf8_bytes(value: str, max_bytes: int) -> list[str]:
    _require(max_bytes >= 4, "fragment limit must fit one UTF-8 scalar")
    pieces: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in value:
        encoded_bytes = len(character.encode())
        if current and current_bytes + encoded_bytes > max_bytes:
            pieces.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += encoded_bytes
    if current:
        pieces.append("".join(current))
    return pieces


def _pack_lines(value: str, max_bytes: int) -> list[str]:
    packed: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for line in value.splitlines(keepends=True):
        for piece in _split_utf8_bytes(line, max_bytes):
            piece_bytes = len(piece.encode())
            if current and current_bytes + piece_bytes > max_bytes:
                packed.append("".join(current))
                current = []
                current_bytes = 0
            current.append(piece)
            current_bytes += piece_bytes
    if current:
        packed.append("".join(current))
    return packed


def _trajectory_fragments(
    trajectory: dict[str, object], max_bytes: int = RESOURCE_FRAGMENT_BYTES
) -> list[str]:
    states = trajectory["states"]
    _require(isinstance(states, list) and states, "trajectory states are missing")
    blocks = [
        _state_body(trajectory, state, index) for index, state in enumerate(states)
    ]
    fragments: list[str] = []
    current: list[str] = []
    current_bytes = 0
    separator = "\n\n---\n\n"
    separator_bytes = len(separator.encode())
    for block in blocks:
        block_bytes = len(block.encode())
        if block_bytes > max_bytes:
            if current:
                fragments.append(separator.join(current))
                current = []
                current_bytes = 0
            fragments.extend(_pack_lines(block, max_bytes))
            continue
        candidate_bytes = (
            block_bytes
            if not current
            else current_bytes + separator_bytes + block_bytes
        )
        if current and candidate_bytes > max_bytes:
            fragments.append(separator.join(current))
            current = [block]
            current_bytes = block_bytes
        else:
            current.append(block)
            current_bytes = candidate_bytes
    if current:
        fragments.append(separator.join(current))
    return fragments


def _drain_worker(
    worker_bin: str, database_url: str, expected: int
) -> dict[str, object]:
    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)
    environment.update(
        {
            "MEMPHANT_WORKER_DATABASE_URL": database_url,
            "MEMPHANT_WORKER_DRAIN": "1",
            "MEMPHANT_RESOURCE_CHUNKS": "on",
            "MEMPHANT_STRUCTURED_STATE": "off",
        }
    )
    completed = subprocess.run(
        [worker_bin], env=environment, capture_output=True, text=True, check=False
    )
    proof_dir = Path(_required_env("MEMPHANT_LME_PROOF_DIR"))
    proof_dir.mkdir(parents=True, exist_ok=True)
    (proof_dir / "worker.stdout").write_text(completed.stdout, encoding="utf-8")
    (proof_dir / "worker.stderr").write_text(completed.stderr, encoding="utf-8")
    _require(
        completed.returncode == 0, f"worker drain failed: {completed.stderr.strip()}"
    )
    match = re.search(r"drain completed=(\d+)", completed.stdout)
    _require(match is not None, "worker drain omitted completed count")
    count = int(match.group(1))
    _require(count == expected, f"worker compiled {count} sources, expected {expected}")
    return {
        "completed_sources": count,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def _schema_snapshot(database_url: str) -> dict[str, dict[str, object]]:
    tables = subprocess.run(
        [
            "psql",
            database_url,
            "-At",
            "-c",
            "select tablename from pg_tables where schemaname='memphant' order by tablename",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    snapshot: dict[str, dict[str, object]] = {}
    for table in tables:
        _require(re.fullmatch(r"[a-z0-9_]+", table) is not None, "unsafe table name")
        statement = (
            f"SELECT count(*), coalesce(md5(string_agg(md5(row_to_json(t)::text), '' "
            f"ORDER BY md5(row_to_json(t)::text))), md5('')) FROM memphant.\"{table}\" t"
        )
        result = (
            subprocess.run(
                ["psql", database_url, "-At", "-F", "\t", "-c", statement],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .split("\t")
        )
        _require(len(result) == 2, f"schema snapshot failed for {table}")
        snapshot[table] = {"rows": int(result[0]), "content_md5": result[1]}
    return snapshot


def _prove_recall_mutations(
    before: dict[str, dict[str, object]], after: dict[str, dict[str, object]]
) -> dict[str, object]:
    _require(
        set(before) == set(after), "memphant schema table set changed during recall"
    )
    changed = sorted(table for table in before if before[table] != after[table])
    _require(
        changed == ["retrieval_trace"], f"recall mutated non-audit tables: {changed}"
    )
    _require(
        after["retrieval_trace"]["rows"] == before["retrieval_trace"]["rows"] + 1,
        "recall did not add exactly one audit trace",
    )
    return {
        "before": before,
        "after": after,
        "changed_tables": changed,
        "allowed_audit_rows_added": 1,
        "corpus_policy_job_tables_unchanged": True,
    }


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


@register_memory
class MemphantMemory(Memory):
    memory_type = "memphant"

    def __init__(self, memory_params: dict[str, object]) -> None:
        super().__init__(memory_params)
        self.params = _validate_memory_params(memory_params)
        _require(
            os.environ.get("MEMPHANT_SCRATCH_ACTIVE") == "1",
            "LongMemEval-V2 adapter requires an ephemeral scratch database",
        )
        server_url = _required_env(self.params["server_url_env"])
        database_url = _required_env(self.params["database_url_env"])
        cli_bin = _required_env(self.params["cli_bin_env"])
        server_bin = _required_env(self.params["server_bin_env"])
        worker_bin = _required_env(self.params["worker_bin_env"])
        self.binaries = {
            "server": _binary_fingerprint(server_bin),
            "cli": _binary_fingerprint(cli_bin),
            "worker": _binary_fingerprint(worker_bin),
        }
        self.proof_dir = Path(_required_env(self.params["proof_dir_env"])).resolve()
        run_id = _required_env(self.params["run_id_env"])
        instance_id = uuid.uuid4().hex
        prebuilt_path = os.environ.get("MEMPHANT_LME_PREBUILT_PROOF", "").strip()
        self.query_only = bool(prebuilt_path)
        self.construction_proof = (
            _load_construction_proof(prebuilt_path) if prebuilt_path else None
        )
        if self.construction_proof is not None:
            frozen_contract = self.construction_proof["contract"]
            _require(
                frozen_contract["adapter_sha256"] == _sha256_file(Path(__file__)),
                "construction proof adapter mismatch",
            )
            _require(
                frozen_contract["construction_params_sha256"]
                == _construction_params_sha256(self.params),
                "construction proof parameters mismatch",
            )
            _require(
                all(
                    frozen_contract["binaries"][name]["sha256"]
                    == self.binaries[name]["sha256"]
                    for name in self.binaries
                ),
                "construction proof binary mismatch",
            )
        if self.construction_proof is None:
            self.tenant_id, api_key = _provision_tenant(
                cli_bin=cli_bin,
                database_url=database_url,
                name=f"lme-v2-{run_id[:32]}-{instance_id[:12]}",
            )
        else:
            isolation = self.construction_proof["isolation"]
            _require(
                isinstance(isolation, dict), "construction proof isolation is invalid"
            )
            self.tenant_id = isolation["tenant_id"]
            api_key = _create_api_key(
                cli_bin=cli_bin,
                database_url=database_url,
                tenant_id=self.tenant_id,
            )
        self.client = _JsonClient(server_url, api_key)
        if self.construction_proof is None:
            self.context = _provision_context(self.client, instance_id)
        else:
            self.context = dict(self.construction_proof["isolation"]["context"])
        self.scope_id = self.context["scope_id"]
        self.actor_id = self.context["actor_id"]
        self.worker_bin = worker_bin
        self.database_url = database_url
        self.instance_id = instance_id
        self.inserted_trajectory_ids: list[str] = []
        self.retain_proofs: list[dict[str, object]] = []
        if self.construction_proof is None:
            self.worker_proof: dict[str, object] | None = None
            self.resource_count = 0
        else:
            self.worker_proof = dict(self.construction_proof["pairing"]["worker"])
            self.resource_count = self.construction_proof["pairing"]["resource_count"]
        self._queried_question_id: str | None = None
        self._last_query_proof: dict[str, object] | None = None

    def insert(self, trajectory: dict[str, object]) -> None:
        _require(self._queried_question_id is None, "cannot insert after query")
        _require(
            self.query_only or self.construction_proof is None,
            "cannot insert after construction is frozen",
        )
        (
            trajectory_id,
            states,
            body,
            fragments,
            trajectory_sha256,
        ) = _validate_trajectory(trajectory, self.inserted_trajectory_ids)
        if self.query_only:
            expected_retains = self.construction_proof["pairing"]["retains"]
            index = len(self.inserted_trajectory_ids)
            _require(
                index < len(expected_retains),
                "query-only received too many trajectories",
            )
            expected = expected_retains[index]
            _require(
                expected["trajectory_id"] == trajectory_id
                and expected["trajectory_sha256"] == trajectory_sha256,
                "query-only trajectory order or identity mismatch",
            )
            self.inserted_trajectory_ids.append(trajectory_id)
            return

        fragment_proofs: list[dict[str, object]] = []
        for fragment_index, fragment in enumerate(fragments, 1):
            fragment_body = (
                f"Trajectory fragment {fragment_index}/{len(fragments)}\n\n{fragment}"
            )
            payload = {
                **self.context,
                "source_ref": f"lme-v2:trajectory:{trajectory_id}:{fragment_index:04d}",
                "observed_at": "2026-05-17T00:00:00Z",
                "payload": {
                    "resource": {
                        "uri": f"lme-v2://trajectory/{trajectory_id}/{fragment_index:04d}",
                        "mime_type": "text/markdown",
                        "content_hash": "sha256:"
                        + hashlib.sha256(fragment_body.encode()).hexdigest(),
                        "kind": "document",
                        "revision": trajectory_id,
                        "body": fragment_body,
                    }
                },
            }
            serialized_bytes = len(_canonical_json(payload))
            _require(
                serialized_bytes <= MAX_SERIALIZED_RETAIN_BYTES,
                f"serialized retain exceeds safe Axum body budget: {trajectory_id}",
            )
            response = self.client.request("POST", "/v1/episodes", payload)
            resource_id = response.get("resource_id")
            _require(
                isinstance(resource_id, str) and resource_id,
                "retain omitted resource_id",
            )
            fragment_proofs.append(
                {
                    "fragment_index": fragment_index,
                    "resource_id": resource_id,
                    "body_bytes": len(fragment_body.encode()),
                    "serialized_request_bytes": serialized_bytes,
                    "resource_body_sha256": hashlib.sha256(
                        fragment_body.encode()
                    ).hexdigest(),
                    "request_sha256": _sha256_json(payload),
                    "idempotency_key_sha256": hashlib.sha256(
                        _idempotency_key("POST", "/v1/episodes", payload).encode()
                    ).hexdigest(),
                    "response_sha256": _sha256_json(response),
                }
            )
            self.resource_count += 1
        self.inserted_trajectory_ids.append(trajectory_id)
        self.retain_proofs.append(
            {
                "trajectory_id": trajectory_id,
                "trajectory_sha256": trajectory_sha256,
                "state_count": len(states),
                "canonical_body_bytes": len(body.encode()),
                "canonical_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "fragments": fragment_proofs,
            }
        )

    def prepare(self) -> dict[str, object]:
        _require(not self.query_only, "query-only construction is already frozen")
        _require(self._queried_question_id is None, "cannot prepare after query")
        _require(self.inserted_trajectory_ids, "cannot prepare empty MemPhant memory")
        if self.construction_proof is None:
            self.worker_proof = _drain_worker(
                self.worker_bin, self.database_url, self.resource_count
            )
            core = {
                "schema_version": 1,
                "contract": {
                    "adapter_sha256": _sha256_file(Path(__file__)),
                    "construction_params_sha256": _construction_params_sha256(
                        self.params
                    ),
                    "binaries": self.binaries,
                },
                "isolation": {
                    "tenant_id": self.tenant_id,
                    "instance_id": self.instance_id,
                    "context": self.context,
                },
                "pairing": {
                    "trajectory_count": len(self.inserted_trajectory_ids),
                    "resource_count": self.resource_count,
                    "worker": self.worker_proof,
                    "retains": self.retain_proofs,
                },
            }
            self.construction_proof = {
                **core,
                "construction_proof_sha256": _sha256_json(core),
            }
        return json.loads(json.dumps(self.construction_proof))

    def query(
        self, query: str, query_image: str | None = None
    ) -> list[MemoryContextItem]:
        _require(isinstance(query, str) and query.strip(), "query must be non-empty")
        _require(self.inserted_trajectory_ids, "cannot query empty MemPhant memory")
        context = self.get_query_context()
        question_id = context.get("question_id")
        _require(
            isinstance(question_id, str) and question_id,
            "question_id context is required",
        )
        _require(
            self._queried_question_id is None,
            "MemPhant instance cannot serve multiple questions",
        )
        if self.query_only:
            expected_count = self.construction_proof["pairing"]["trajectory_count"]
            _require(
                len(self.inserted_trajectory_ids) == expected_count,
                f"query-only validated {len(self.inserted_trajectory_ids)} trajectories, expected {expected_count}",
            )
        else:
            self.prepare()
        self._queried_question_id = question_id
        before_recall = _schema_snapshot(self.database_url)
        recall_payload = {
            **self.context,
            "query": query,
            "limit": self.params["top_k"],
            "budget_tokens": self.params["budget_tokens"],
            "mode": self.params["mode"],
        }
        recall_started = time.perf_counter()
        recalled = self.client.request(
            "POST",
            "/v1/recall",
            recall_payload,
            timeout_seconds=RECALL_REQUEST_TIMEOUT_SECONDS,
        )
        recall_duration_ms = int(round((time.perf_counter() - recall_started) * 1000))
        _require(recalled.get("degraded") is False, "MemPhant recall was degraded")
        trace_id = recalled.get("trace_id")
        items = recalled.get("items")
        _require(isinstance(trace_id, str) and trace_id, "recall omitted trace_id")
        _require(isinstance(items, list), "recall items must be a list")
        _require(len(items) <= self.params["top_k"], "recall exceeded fixed top_k")
        _require(
            all(
                isinstance(item, dict) and isinstance(item.get("body"), str)
                for item in items
            ),
            "recall returned malformed context items",
        )
        trace_query = urllib.parse.urlencode(self.context)
        trace = self.client.request("GET", f"/v1/traces/{trace_id}?{trace_query}")
        mutation_proof = _prove_recall_mutations(
            before_recall, _schema_snapshot(self.database_url)
        )
        _require(trace.get("id") == trace_id, "trace id pairing mismatch")
        _require(
            trace.get("tenant_id") == self.tenant_id, "trace tenant pairing mismatch"
        )
        _require(trace.get("scope_id") == self.scope_id, "trace scope pairing mismatch")
        _require(trace.get("actor_id") == self.actor_id, "trace actor pairing mismatch")
        _require(trace.get("context_items") == items, "trace context pairing mismatch")
        _require(
            trace.get("citations") == recalled.get("citations"),
            "trace citation pairing mismatch",
        )

        memory_context: list[MemoryContextItem] = [
            {"type": "text", "value": item["body"]} for item in items
        ]
        query_proof = {
            "question_id": question_id,
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "query_image_present": query_image is not None,
            "native_query_hash": trace.get("query_hash"),
            "recall_request_sha256": _sha256_json(recall_payload),
            "recall_response_sha256": _sha256_json(recalled),
            "trace_id": trace_id,
            "trace_sha256": _sha256_json(trace),
            "context_sha256": _sha256_json(memory_context),
            "recall_duration_ms": recall_duration_ms,
            "construction_proof_sha256": self.construction_proof[
                "construction_proof_sha256"
            ],
            "query_only": self.query_only,
        }
        pairing: dict[str, object] = {
            "trajectory_count": len(self.inserted_trajectory_ids),
            "resource_count": self.resource_count,
            "worker": self.worker_proof,
            "construction_proof_sha256": self.construction_proof[
                "construction_proof_sha256"
            ],
            "query_only": self.query_only,
        }
        if not self.query_only:
            pairing["retains"] = self.retain_proofs
        proof = {
            "contract": {
                "adapter_sha256": _sha256_file(Path(__file__)),
                "memory_params_sha256": _sha256_json(self.params),
                "top_k": self.params["top_k"],
                "budget_tokens": self.params["budget_tokens"],
                "mode": self.params["mode"],
                "recall_request_timeout_seconds": RECALL_REQUEST_TIMEOUT_SECONDS,
                "binaries": self.binaries,
                "gold_fields_consumed": [],
            },
            "isolation": {
                "tenant_id": self.tenant_id,
                "scope_id": self.scope_id,
                "actor_id": self.actor_id,
                "instance_id": self.instance_id,
            },
            "pairing": pairing,
            "recall_mutation_proof": mutation_proof,
            "public": {"recall_response": recalled, "trace": trace},
            "query": query_proof,
        }
        proof_path = self.proof_dir / f"{question_id}.{self.instance_id}.json"
        _atomic_write_json(proof_path, proof)
        query_proof["proof_path"] = str(proof_path)
        self._last_query_proof = query_proof
        return memory_context

    def post_query_hook(
        self,
        *,
        query: str,
        query_image: str | None,
        memory_context: list[MemoryContextItem],
    ) -> dict[str, object] | None:
        _require(self._last_query_proof is not None, "query proof is missing")
        _require(
            self._last_query_proof["query_sha256"]
            == hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "post-query query pairing mismatch",
        )
        _require(
            self._last_query_proof["context_sha256"] == _sha256_json(memory_context),
            "post-query context pairing mismatch",
        )
        return dict(self._last_query_proof)
