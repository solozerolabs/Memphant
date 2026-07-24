#!/usr/bin/env python3
"""Shared MemPhant-server runtime harness for the embedder-bakeoff engine
runners: server/worker process lifecycle, tenant provisioning, a small
retrying HTTP client, and the API-arm key map. Factored out of
``gate_run_memphant.py`` (the docs-lane runner) so
``code_lane_run_memphant.py`` (R0-T6) doesn't copy-paste the ~150-line
process-management block. ``gate_run_memphant.py`` keeps its own process
classes (it is a live, already-running script during R0) but imports the
API-key arm map from here — the one piece where a per-script copy already
drifted once (see ``API_KEY_ENV_BY_ARM``).

This module includes the ``check_port_free``/extended-health-wait/log-capture
robustness landed in ``gate_run_memphant.py`` by commit ``cf17b35`` (a
concurrent fix during R0: ``memphant-server`` can take minutes to download
embedding-model weights on first boot, and the original 20s health wait would
time out mid-download and leak the child, which then held the port and
cascaded bind failures across an arm queue) — ported here rather than only in
the docs-lane script, since this runner spawns the identical server binary
and would hit the identical failure mode.

Everything here is stdlib-only except real subprocess/HTTP calls against a
packaged ``memphant-server``/``memphant-worker``/``memphant-cli`` — no DB
driver, no network library beyond ``http.client``.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HEALTH_WAIT_TIMEOUT_S = 600.0  # first boot may download embedding model weights (up to 1.5GB)
HEALTH_POLL_INTERVAL_S = 0.5
LOG_TAIL_LINES = 15

# The API-arm ids from memphant-runtime's `embedder_from_id` grammar
# (crates/memphant-runtime/src/lib.rs) and the provider key each one needs.
# SINGLE SOURCE OF TRUTH for every gate runner: gate_run_memphant.py and
# code_lane_run_memphant.py both import this map (a per-script copy drifted
# once already — voyage-4-large landed in one script's copy but not the
# other's, silently disabling the fail-fast for that arm). Mirrors
# `api_embeddings::require_key`'s error text so a missing key fails the same
# way here as it would inside the Rust binary — just before spending time on
# tenant/server setup instead of after the ingest has already started.
# Pinned against the Rust grammar by tests/test_gate_runtime.py.
API_KEY_ENV_BY_ARM = {
    "voyage-4": "VOYAGE_API_KEY",
    "voyage-4-lite": "VOYAGE_API_KEY",
    "voyage-4-large": "VOYAGE_API_KEY",
    "voyage-code-3": "VOYAGE_API_KEY",
    "voyage-context-4": "VOYAGE_API_KEY",
    "gemini-embedding-001": "GEMINI_API_KEY",
    "gemini-embedding-2": "GEMINI_API_KEY",
    "openai-text-embedding-3-small": "OPENAI_API_KEY",
    "jina-v5-small": "JINA_API_KEY",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repository_identity(root: Path) -> dict:
    """Content-bound Git identity shared by sealed benchmark runners."""
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--stage", "-z"],
        check=True, capture_output=True,
    ).stdout.split(b"\0")
    digest = hashlib.sha256()
    count = 0
    for entry in tracked:
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0]
        path = raw_path.decode("utf-8", errors="surrogateescape")
        digest.update(mode + b"\0" + raw_path + b"\0")
        digest.update(hashlib.sha256((root / path).read_bytes()).digest())
        count += 1
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--binary", "HEAD", "--"],
        check=True, capture_output=True,
    ).stdout
    return {
        "git_head": head,
        "tracked_file_count": count,
        "tracked_worktree_sha256": digest.hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "tracked_dirty": bool(diff),
    }


def migration_identity(root: Path) -> dict:
    rows = [
        {"path": str(path.relative_to(root)), "sha256": sha256_file(path)}
        for path in sorted((root / "memphant_migrations" / "versions").glob("*.sql"))
    ]
    return {
        "files": rows,
        "aggregate_sha256": hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def portable_path(path: Path, root: Path) -> str:
    """Return a stable evidence label, never a machine-local absolute path."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return f"<external>/{resolved.name}"


def portable_command(argv: list[str], root: Path) -> tuple[list[str], str]:
    portable = [
        portable_path(Path(value), root) if Path(value).is_absolute() else value
        for value in argv
    ]
    return portable, shlex.join(["python3", *portable])


def episode_retain_payload(
    context: dict,
    *,
    source_ref: str,
    observed_at: str,
    source_kind: str,
    body: str,
) -> dict:
    """Build the one strict public episode-retain shape used by benchmark
    runners. Context is server-bound; tenant and internal compiler hints are
    deliberately impossible to add through this helper."""
    required = {
        "subject_id", "scope_id", "actor_id", "agent_node_id",
        "subject_generation",
    }
    if set(context) != required:
        raise ValueError("episode retain context must contain exactly the five bound fields")
    if source_kind not in {"user", "agent", "tool", "web", "resource", "system"}:
        raise ValueError(f"unmapped episode source kind: {source_kind!r}")
    if not source_ref or not observed_at or not body:
        raise ValueError("episode retain source_ref, observed_at, and body must be nonempty")
    payload = {
        **context,
        "source_ref": source_ref,
        "observed_at": observed_at,
        "payload": {"episode": {"source_kind": source_kind, "body": body}},
    }
    return payload


def check_embed_model_key(embed_model: str | None) -> None:
    """Fail fast when an API embedder arm is selected but its provider key is
    missing from the parent env. Local arms (small/base/modernbert/gemma/
    qwen3) and off/noop need no key and are silently allowed through."""
    if not embed_model:
        return
    var = API_KEY_ENV_BY_ARM.get(embed_model)
    if var is None:
        return
    if not os.environ.get(var, "").strip():
        raise RuntimeError(
            f"--embed-model {embed_model}: {var} is not set (required to "
            "construct this API embedding provider)"
        )


def sh(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def provision_tenant(cli_bin: str, database_url: str, name_prefix: str = "gate") -> tuple[str, str]:
    """Creates a fresh tenant + a ``trusted_system``-max API key against
    ``database_url`` via the packaged CLI's admin commands. Returns
    ``(tenant_id, api_key)``."""
    name = f"{name_prefix}-{int(time.time())}"
    out = sh([cli_bin, "admin", "create-tenant", "--name", name, "--database-url", database_url])
    if out.returncode != 0:
        raise RuntimeError(f"create-tenant failed: {out.stderr.strip()}")
    match = re.search(r"tenant_created id=(\S+)", out.stdout)
    if not match:
        raise RuntimeError(f"could not parse tenant id from: {out.stdout}")
    tenant_id = match.group(1)
    return tenant_id, provision_api_key(cli_bin, database_url, tenant_id)


def provision_api_key(cli_bin: str, database_url: str, tenant_id: str) -> str:
    out = sh(
        [cli_bin, "admin", "create-key", "--tenant", tenant_id,
         "--max-trust", "trusted_system", "--database-url", database_url]
    )
    if out.returncode != 0:
        raise RuntimeError(f"create-key failed: {out.stderr.strip()}")
    api_key = out.stdout.strip().splitlines()[-1].strip()
    if not api_key.startswith("mk_"):
        raise RuntimeError(f"could not parse api key from: {out.stdout}")
    return api_key


def check_port_free(port: int) -> None:
    """Refuse to spawn a server on ``port`` if something is already
    LISTENing there. A prior run that leaked its server child can otherwise
    sit on the port forever, silently dropping every subsequent server on an
    instant bind failure. Best effort: if ``lsof`` is unavailable, proceed
    without the check."""
    try:
        check = sh(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    except OSError:
        return
    if check.returncode != 0 or not check.stdout.strip():
        return
    lines = [line for line in check.stdout.strip().splitlines() if line.strip()]
    pid = None
    for line in lines[1:]:
        parts = line.split()
        if len(parts) > 1:
            pid = parts[1]
            break
    pid_msg = f" held by PID {pid}" if pid else " (PID undiscoverable)"
    raise RuntimeError(
        f"port {port} is already in LISTEN state{pid_msg} — refusing to start "
        f"a new server; a leaked process from a prior run may still be bound "
        f"here. lsof output:\n{chr(10).join(lines)}"
    )


class Server:
    """The packaged ``memphant-server`` binary, started against a scratch
    Postgres database with an optional ``MEMPHANT_EMBEDDINGS`` arm selector
    (R0-T3's ``--embed-model`` seam). A 10-minute health wait (first boot can
    download embedding-model weights) with a fast-fail if the child exits
    early, optional stdout+stderr capture to ``log_path`` (tailed into any
    raised error), and a port-conflict check before spawning."""

    def __init__(
        self,
        server_bin: str,
        database_url: str,
        port: int,
        embed_model: str | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.server_bin = server_bin
        self.database_url = database_url
        self.port = port
        self.embed_model = embed_model
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self._log_file = None

    def _tail_log(self, n: int = LOG_TAIL_LINES) -> str:
        if self.log_path is None or not self.log_path.exists():
            return ""
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        tail = lines[-n:]
        if not tail:
            return ""
        return f"--- last {len(tail)} lines of {self.log_path} ---\n" + "\n".join(tail)

    def _terminate(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    def start(self) -> None:
        check_port_free(self.port)
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)
        env["MEMPHANT_APP_DATABASE_URL"] = self.database_url
        env["MEMPHANT_AUTHN_DATABASE_URL"] = self.database_url
        env["MEMPHANT_BIND"] = f"127.0.0.1:{self.port}"
        env.setdefault("RUST_LOG", "warn")
        if self.embed_model:
            env["MEMPHANT_EMBEDDINGS"] = self.embed_model
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.log_path, "w")
            stdout_target: object = self._log_file
            stderr_target: object = self._log_file
        else:
            stdout_target = subprocess.DEVNULL
            stderr_target = subprocess.DEVNULL
        self.proc = subprocess.Popen(
            [self.server_bin], env=env,
            stdout=stdout_target, stderr=stderr_target,
        )
        deadline = time.time() + HEALTH_WAIT_TIMEOUT_S
        while time.time() < deadline:
            if self.proc.poll() is not None:
                code = self.proc.returncode
                tail = self._tail_log()
                self._terminate()
                msg = (
                    f"memphant-server child exited (code={code}) before "
                    f"becoming healthy on :{self.port}"
                )
                raise RuntimeError(f"{msg}\n{tail}" if tail else msg)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                conn.request("GET", "/v1/health")
                if conn.getresponse().status == 200:
                    conn.close()
                    return
            except OSError:
                pass
            time.sleep(HEALTH_POLL_INTERVAL_S)
        tail = self._tail_log()
        self._terminate()
        msg = (
            f"server did not become healthy on :{self.port} within "
            f"{HEALTH_WAIT_TIMEOUT_S:.0f}s (timed out waiting for /v1/health)"
        )
        raise RuntimeError(f"{msg}\n{tail}" if tail else msg)

    def stop(self) -> None:
        self._terminate()


class ApiClient:
    """Minimal retrying JSON/HTTP client against a running ``Server``."""

    def __init__(self, port: int, api_key: str, tenant_id: str) -> None:
        self.port = port
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.tenant_id = tenant_id
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload) if payload is not None else None
        headers = dict(self.headers)
        if method in {"POST", "PUT", "DELETE"} and path in {
            "/v1/episodes",
            "/v1/reflect",
            "/v1/correct",
            "/v1/forget",
            "/v1/mark",
        }:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(f"{method}\n{path}\n{canonical}".encode()).hexdigest()
            headers["Idempotency-Key"] = f"memphant-gate-{digest}"
        for attempt in range(3):
            try:
                self.conn.request(method, path, body=body, headers=headers)
                response = self.conn.getresponse()
                data = response.read()
                if response.status >= 400:
                    raise RuntimeError(f"{path} -> HTTP {response.status}: {data[:300]!r}")
                return json.loads(data)
            except (http.client.HTTPException, OSError) as error:
                self.conn.close()
                self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
                if attempt == 2:
                    raise RuntimeError(f"{path} failed after retries: {error}")
        raise AssertionError("unreachable")

    def post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, payload)

    def put(self, path: str, payload: dict) -> dict:
        return self._request("PUT", path, payload)

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def bind_context(
        self,
        client_ref: str,
        *,
        subject_ref: str,
        subject_kind: str = "user",
        actor_ref: str,
        actor_kind: str = "system",
        scope_ref: str,
        scope_kind: str = "user_root",
        agent_node_ref: str,
    ) -> dict:
        """Resolve external refs into the identity every verb requires
        (PUT /v1/context-bindings). Returns the five ids + subject_generation as
        a dict ready to spread into a retain/recall body. The strict landed
        contract validates these on every call; there is no tenant_id path."""
        response = self.put(
            f"/v1/context-bindings/{client_ref}",
            {
                "subject": {"external_ref": subject_ref, "kind": subject_kind},
                "actor": {"external_ref": actor_ref, "kind": actor_kind},
                "scope": {"external_ref": scope_ref, "kind": scope_kind},
                "agent_node": {"external_ref": agent_node_ref},
            },
        )
        return {
            "subject_id": response["subject_id"],
            "scope_id": response["scope_id"],
            "actor_id": response["actor_id"],
            "agent_node_id": response["agent_node_id"],
            "subject_generation": response["subject_generation"],
        }


def reexec_through_scratch_db(base_url: str) -> None:
    """Re-exec this runner through ``scripts/with_scratch_db.sh`` so the bench
    operates on a fresh, migrated, auto-dropped scratch DB minted from
    ``base_url`` — never a shared named DB whose foreign ``job_state`` debris
    could starve (or be starved by) the run's global oldest-first worker ticks.
    Mirrors ``scripts/e2e_probe.sh``'s self re-exec: ``MEMPHANT_SCRATCH_ACTIVE``
    guards the recursion (set it, with ``DATABASE_URL`` already on an isolated
    DB, to skip). Returns (no-op) in the scratch-active child; in the parent it
    never returns (``os.execvp`` replaces the process). The scratch URL reaches
    the child as ``DATABASE_URL``; with_scratch_db.sh drops the DB on its EXIT
    trap, so even a killed bench leaves no debris behind."""
    if os.environ.get("MEMPHANT_SCRATCH_ACTIVE"):
        return
    helper = str(Path(__file__).resolve().parent / "with_scratch_db.sh")
    os.environ["MEMPHANT_SCRATCH_ACTIVE"] = "1"
    os.execvp("bash", ["bash", helper, base_url, "DATABASE_URL", sys.executable, *sys.argv])


def drain_worker(
    worker_bin: str,
    database_url: str,
    embed_model: str | None = None,
    max_ticks: int = 4000,
    structured_attempt_ledger: Path | None = None,
    structured_requested_model: str | None = None,
) -> int:
    """Run worker ticks until the database confirms no jobs remain.

    A zero-completion tick is not proof of an empty queue: a provider failure
    leaves its job claimed for retry.  Fail closed in that case instead of
    generating answers from a partially compiled corpus.
    """
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("MEMPHANT_WORKER_ONCE", None)
    env.pop("MEMPHANT_WORKER_DRAIN", None)
    env["MEMPHANT_WORKER_DATABASE_URL"] = database_url
    env.setdefault("RUST_LOG", "warn")
    if embed_model:
        env["MEMPHANT_EMBEDDINGS"] = embed_model
    if structured_attempt_ledger is not None:
        structured_attempt_ledger.parent.mkdir(parents=True, exist_ok=True)
        env["MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER"] = str(
            structured_attempt_ledger
        )
    if (structured_attempt_ledger is None) != (structured_requested_model is None):
        raise ValueError(
            "structured attempt ledger and requested model must be provided together"
        )
    if structured_attempt_ledger is None:
        # The worker owns the queue-empty and dead-letter invariants in drain
        # mode. Keep one process (and one pool) alive for large local corpora;
        # spawning a new binary per four-job tick turns 64k events into more
        # than 16k process launches. Structured-provider campaigns retain the
        # tick loop below so their append-only attempt ledger is inspected
        # after every provider-bearing tick.
        env["MEMPHANT_WORKER_DRAIN"] = "1"
        # Free/no-provider compilation has no remote-rate or spend constraint.
        # Use the runtime's bounded maximum so realistic-volume gates do not
        # inherit the conservative provider-prefetch default of four.
        env["MEMPHANT_WORKER_COMPILE_CONCURRENCY"] = "64"
        env["MEMPHANT_WORKER_BATCH_SIZE"] = "256"
        env["MEMPHANT_WORKER_DATABASE_MAX_CONNECTIONS"] = "32"
        out = sh([worker_bin], env=env)
        if out.returncode != 0:
            raise RuntimeError(f"worker drain failed: {out.stderr.strip()[:300]}")
        match = re.fullmatch(
            r"memphant-worker: drain completed=(0|[1-9]\d*)\n?", out.stdout
        )
        if match is None:
            raise RuntimeError(
                f"worker drain completion output is malformed: {out.stdout[:300]!r}"
            )
        return int(match.group(1))

    env["MEMPHANT_WORKER_ONCE"] = "1"
    total = 0
    worker_stderr: list[str] = []
    for _tick in range(max_ticks):
        out = sh([worker_bin], env=env)
        if out.stderr.strip():
            worker_stderr.append(out.stderr.strip())
        if out.returncode != 0:
            raise RuntimeError(f"worker tick failed: {out.stderr.strip()[:300]}")
        match = re.search(r"completed=(\d+)", out.stdout)
        completed = int(match.group(1)) if match else 0
        total += completed
        if structured_attempt_ledger is not None:
            structured_summary = structured_extractor_attempt_summary(
                structured_attempt_ledger, structured_requested_model,
                require_episode_coverage=True,
            )
            if (
                structured_summary["terminal_decode_errors"]
                or structured_summary["terminal_rejected_operations"]
            ):
                raise RuntimeError(
                    "structured extractor failed benchmark admission: "
                    f"decode_errors={structured_summary['decode_errors']} "
                    f"rejected_operations={structured_summary['rejected_operations']} "
                    f"rejection_reasons={structured_summary['rejection_reasons']}"
                )
        if completed == 0:
            pending = sh([
                "psql", "--no-psqlrc", "--tuples-only", "--no-align",
                "--set", "ON_ERROR_STOP=1", database_url, "--command",
                "select count(*) from memphant.job_state "
                "where state in ('queued', 'running')",
            ])
            if pending.returncode != 0:
                raise RuntimeError(
                    f"could not verify worker queue was drained: {pending.stderr.strip()[:300]}"
                )
            try:
                pending_count = int(pending.stdout.strip())
            except ValueError as error:
                raise RuntimeError("could not parse pending worker job count") from error
            if pending_count:
                worker_error = " | ".join(worker_stderr[-4:])
                diagnostics = sh([
                    "psql", "--no-psqlrc", "--tuples-only", "--no-align",
                    "--set", "ON_ERROR_STOP=1", database_url, "--command",
                    "select coalesce(json_agg(row_to_json(q)), '[]'::json) from ("
                    "select target_id::text, attempts, last_error "
                    "from memphant.job_state "
                    "where state in ('queued', 'running') and last_error is not null "
                    "order by updated_at desc limit 5) q",
                ])
                job_error = (
                    diagnostics.stdout.strip()
                    if diagnostics.returncode == 0
                    else f"diagnostic query failed: {diagnostics.stderr.strip()[:300]}"
                )
                raise RuntimeError(
                    f"worker made no progress with {pending_count} pending retryable jobs"
                    + (f": {worker_error[:1000]}" if worker_error else "")
                    + (f"; pending_job_errors={job_error[:2000]}" if job_error else "")
                )
            return total
    raise RuntimeError(f"worker did not drain within {max_ticks} ticks (total={total})")


def structured_extractor_attempt_summary(
    path: Path, requested_model: str, *, require_attempts: bool = True,
    require_episode_coverage: bool = False,
) -> dict:
    """Validate the worker's append-only OpenRouter attempt ledger.

    A durable ``started`` event precedes each network call. Every observed
    result must pair with one start; every 2xx response must also pair with a
    nonsecret decode outcome carrying accepted/rejected operation counts.
    """
    if not path.is_file():
        if require_attempts:
            raise RuntimeError("structured extractor attempt ledger is missing")
        rows = []
        raw = b""
    else:
        raw = path.read_bytes()
        try:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("structured extractor attempt ledger is malformed") from error
    if require_attempts and not rows:
        raise RuntimeError("structured extractor attempt ledger is empty")

    starts = {}
    results = {}
    decodes = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("schema_version") not in {1, 2}:
            raise RuntimeError("structured extractor attempt ledger schema drifted")
        attempt_id = row.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise RuntimeError("structured extractor attempt id is missing")
        if row.get("requested_model") != requested_model:
            raise RuntimeError("structured extractor requested model drifted")
        if row["schema_version"] == 2:
            attempt = row.get("attempt")
            max_attempts = row.get("max_attempts")
            if (
                type(attempt) is not int
                or type(max_attempts) is not int
                or max_attempts not in {1, 3}
                or not 1 <= attempt <= max_attempts
                or row.get("retry_index") != attempt - 1
                or not isinstance(row.get("request_sha256"), str)
                or len(row["request_sha256"]) != 64
            ):
                raise RuntimeError("structured extractor strict attempt metadata is malformed")
            if row.get("event") != "started" and (
                isinstance(row.get("elapsed_seconds"), bool)
                or not isinstance(row.get("elapsed_seconds"), (int, float))
                or row["elapsed_seconds"] < 0
                or not isinstance(row.get("parse_status"), str)
                or not row["parse_status"]
            ):
                raise RuntimeError("structured extractor strict timing/parse proof is malformed")
        event = row.get("event")
        if event == "started":
            if attempt_id in starts or attempt_id in results or attempt_id in decodes:
                raise RuntimeError("structured extractor attempt id is duplicated")
            starts[attempt_id] = row
        elif event == "result":
            if attempt_id not in starts or attempt_id in results:
                raise RuntimeError("structured extractor result is unpaired")
            results[attempt_id] = row
        elif event == "decode":
            if attempt_id not in results or attempt_id in decodes:
                raise RuntimeError("structured extractor decode outcome is unpaired")
            status = results[attempt_id].get("http_status")
            if not isinstance(status, int) or not 200 <= status < 300:
                raise RuntimeError("structured extractor decode outcome lacks a successful response")
            decodes[attempt_id] = row
        else:
            raise RuntimeError("structured extractor attempt event is invalid")

    priced = 0
    cost = 0.0
    successful = 0
    accepted_ops = 0
    rejected_ops = 0
    rejection_reasons = {}
    decode_errors = 0
    terminal_decode_errors = 0
    transient_no_content_attempts = 0
    transient_transport_attempts = 0
    transient_http_attempts = 0
    terminal_provenance_errors = 0
    successful_decodes = 0
    successful_episode_ids = set()
    providers = set()
    for attempt_id, row in results.items():
        status = row.get("http_status")
        if row.get("error") == "generation_stats_lookup_failed":
            if (
                row.get("schema_version") != 2
                or not isinstance(status, int)
                or not 200 <= status < 300
                or not isinstance(row.get("result_sha256"), str)
                or len(row["result_sha256"]) != 64
            ):
                raise RuntimeError("structured extractor reconciliation failure proof is malformed")
            terminal_provenance_errors += 1
            continue
        if isinstance(status, int) and 200 <= status < 300:
            served_model = row.get("served_model")
            if not isinstance(served_model, str) or not (
                served_model == requested_model
                or served_model.startswith(requested_model + "-")
            ):
                raise RuntimeError("structured extractor served model drifted")
            if not isinstance(row.get("response_id"), str) or not row["response_id"]:
                raise RuntimeError("structured extractor response id is missing")
            provider = row.get("provider")
            if not isinstance(provider, str) or not provider.strip():
                raise RuntimeError("structured extractor provider is missing")
            providers.add(provider)
            if row.get("schema_version") == 2 and (
                row.get("parse_status") != "generation_stats_reconciled"
                or not isinstance(row.get("result_sha256"), str)
                or len(row["result_sha256"]) != 64
            ):
                raise RuntimeError("structured extractor reconciliation proof is malformed")
            usage = row.get("usage")
            if not isinstance(usage, dict):
                raise RuntimeError("structured extractor usage is missing")
            decode = decodes.get(attempt_id)
            if decode is None:
                raise RuntimeError("structured extractor decode outcome is missing")
            zero_no_content = (
                all(usage.get(key) == 0 for key in (
                    "prompt_tokens", "completion_tokens", "total_tokens"
                ))
                and usage.get("cost") == 0
                and decode.get("error") == "response_decode_error"
                and decode.get("accepted_op_count") == 0
                and decode.get("rejected_op_count") == 0
                and decode.get("rejection_reasons") == {}
            )
            if zero_no_content:
                transient_no_content_attempts += 1
                decode_errors += 1
                continue
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if type(usage.get(key)) is not int or usage[key] <= 0:
                    raise RuntimeError("structured extractor token usage is malformed")
            if usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]:
                raise RuntimeError("structured extractor token usage is inconsistent")
            reported = usage.get("cost")
            if isinstance(reported, bool) or not isinstance(reported, (int, float)) or reported <= 0:
                raise RuntimeError("structured extractor response cost is missing")
            priced += 1
            cost += float(reported)
            accepted = decode.get("accepted_op_count")
            rejected = decode.get("rejected_op_count")
            reasons = decode.get("rejection_reasons")
            if type(accepted) is not int or accepted < 0 or type(rejected) is not int or rejected < 0:
                raise RuntimeError("structured extractor decode counts are malformed")
            if not isinstance(reasons, dict) or any(
                not isinstance(reason, str) or not reason
                or type(count) is not int or count <= 0
                for reason, count in reasons.items()
            ) or sum(reasons.values()) != rejected:
                raise RuntimeError("structured extractor rejection proof is malformed")
            if decode.get("error") not in {None, "response_decode_error"}:
                raise RuntimeError("structured extractor decode error is invalid")
            if decode.get("error") == "response_decode_error":
                if accepted or rejected or reasons:
                    raise RuntimeError("structured extractor failed decode has operation counts")
                decode_errors += 1
                terminal_decode_errors += 1
            elif rejected == 0:
                successful += 1
                successful_decodes += 1
                successful_episode_ids.add(row.get("episode_id", attempt_id))
            accepted_ops += accepted
            rejected_ops += rejected
            for reason, count in reasons.items():
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + count
        elif row.get("error") == "transport_error":
            transient_transport_attempts += 1
        elif row.get("error") == "http_error":
            transient_http_attempts += 1
        else:
            raise RuntimeError("structured extractor failed attempt has no error proof")

    interrupted = len(starts) - len(results)
    if interrupted:
        raise RuntimeError(
            f"structured extractor cost proof is incomplete: {interrupted} interrupted attempts"
        )
    unpriced = len(starts) - priced
    transient_attempts = (
        transient_no_content_attempts
        + transient_transport_attempts
        + transient_http_attempts
    )
    if unpriced != transient_attempts + terminal_provenance_errors:
        raise RuntimeError(
            "structured extractor cost proof has unclassified unpriced attempts"
        )
    episodes = {}
    retried_episodes = 0
    semantic_repair_attempts = 0
    recovered_rejected_operations = 0
    if require_episode_coverage:
        for attempt_id, start in starts.items():
            episode_id = start.get("episode_id")
            attempt = start.get("attempt")
            max_attempts = start.get("max_attempts")
            if (
                not isinstance(episode_id, str) or not episode_id
                or type(attempt) is not int or attempt < 1
                or max_attempts not in (
                    {1, 3} if start.get("schema_version") == 2 else {3}
                )
            ):
                raise RuntimeError("structured extractor episode attempt metadata is malformed")
            result = results[attempt_id]
            if any(
                result.get(key) != start.get(key)
                for key in (
                    "schema_version", "episode_id", "attempt", "max_attempts",
                    "requested_model", "retry_index", "request_sha256",
                )
                if key in start or key in result
            ):
                raise RuntimeError("structured extractor cross-event metadata drifted")
            decode = decodes.get(attempt_id)
            if decode is not None and any(
                decode.get(key) != start.get(key)
                for key in (
                    "schema_version", "episode_id", "attempt", "max_attempts",
                    "requested_model", "retry_index", "request_sha256",
                )
                if key in start or key in decode
            ):
                raise RuntimeError("structured extractor decode metadata drifted")
            episodes.setdefault(episode_id, []).append((attempt, attempt_id))
        for episode_id, attempts in episodes.items():
            attempts.sort()
            if [attempt for attempt, _ in attempts] != list(range(1, len(attempts) + 1)):
                raise RuntimeError("structured extractor episode attempts are not contiguous")
            if len(attempts) > starts[attempts[0][1]]["max_attempts"]:
                raise RuntimeError("structured extractor episode exceeded retry budget")
            episode_successes = [
                attempt_id for _, attempt_id in attempts
                if attempt_id in decodes
                and decodes[attempt_id].get("error") is None
                and decodes[attempt_id].get("rejected_op_count") == 0
                and results[attempt_id].get("http_status") == 200
            ]
            if len(episode_successes) != 1 or episode_successes[0] != attempts[-1][1]:
                episode_rejections = {
                    reason: sum(
                        decodes[attempt_id].get("rejection_reasons", {}).get(reason, 0)
                        for _, attempt_id in attempts if attempt_id in decodes
                    )
                    for reason in {
                        reason
                        for _, attempt_id in attempts if attempt_id in decodes
                        for reason in decodes[attempt_id].get("rejection_reasons", {})
                    }
                }
                episode_outcomes = [
                    {
                        "attempt": attempt,
                        "http_status": results[attempt_id].get("http_status"),
                        "error": results[attempt_id].get("error")
                        or decodes.get(attempt_id, {}).get("error"),
                    }
                    for attempt, attempt_id in attempts
                ]
                raise RuntimeError(
                    f"structured extractor episode {episode_id} lacks one final successful "
                    f"decode; rejection_reasons={episode_rejections}; "
                    f"attempt_outcomes={episode_outcomes}"
                )
            for _, attempt_id in attempts[:-1]:
                result = results[attempt_id]
                decode = decodes.get(attempt_id)
                if result.get("schema_version") == 2:
                    status = result.get("http_status")
                    retryable = result.get("error") == "http_error" and (
                        status == 429 or type(status) is int and status >= 500
                    )
                else:
                    retryable = result.get("error") in {
                        "transport_error", "http_error"
                    } or (
                        decode is not None
                        and decode.get("error") == "response_decode_error"
                        and result.get("usage", {}).get("prompt_tokens") == 0
                        and result.get("usage", {}).get("completion_tokens") == 0
                        and result.get("usage", {}).get("total_tokens") == 0
                        and result.get("usage", {}).get("cost") == 0
                    ) or (
                        decode is not None
                        and decode.get("error") is None
                        and decode.get("rejected_op_count", 0) > 0
                        and set(decode.get("rejection_reasons", {}))
                        <= {"evidence_grounding", "duplicate_state_identity"}
                    )
                if not retryable:
                    raise RuntimeError("structured extractor retried a terminal attempt")
                if (
                    decode is not None
                    and set(decode.get("rejection_reasons", {}))
                    <= {"evidence_grounding", "duplicate_state_identity"}
                ):
                    semantic_repair_attempts += 1
                    recovered_rejected_operations += decode["rejected_op_count"]
            if len(attempts) > 1:
                retried_episodes += 1
    return {
        "provider_attempts": len(starts),
        "completed_attempts": len(results),
        "interrupted_attempts": interrupted,
        "unpriced_attempts": unpriced,
        "successful_responses": successful,
        "decode_outcomes": len(decodes),
        "decode_errors": decode_errors,
        "terminal_decode_errors": terminal_decode_errors,
        "successful_decodes": successful_decodes,
        "successful_episodes": len(successful_episode_ids),
        "episodes": len(episodes) if require_episode_coverage else len(successful_episode_ids),
        "retried_episodes": retried_episodes,
        "transient_attempts": transient_attempts,
        "transient_no_content_attempts": transient_no_content_attempts,
        "transient_transport_attempts": transient_transport_attempts,
        "transient_http_attempts": transient_http_attempts,
        "terminal_provenance_errors": terminal_provenance_errors,
        "accepted_operations": accepted_ops,
        "rejected_operations": rejected_ops,
        "terminal_rejected_operations": rejected_ops - recovered_rejected_operations,
        "semantic_repair_attempts": semantic_repair_attempts,
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "priced_responses": priced,
        "reported_cost_usd": cost,
        "requested_model": requested_model,
        "providers": sorted(providers),
        "cost_status": (
            "all_provider_attempts_priced"
            if unpriced == 0
            else "reported_cost_is_lower_bound"
        ),
        "ledger_sha256": hashlib.sha256(raw).hexdigest(),
    }


def recall_query(
    client: ApiClient, ctx: dict, query: str, k: int, budget_tokens: int, mode: str
) -> tuple[list[str], bool]:
    """Calls ``/v1/recall`` and returns ``(item_bodies_by_rank, degraded)``.
    ``ctx`` is a bound context (from ``ApiClient.bind_context``) carrying the
    strict-contract identity; there is no tenant_id path."""
    payload = {
        **ctx,
        "query": query,
        "limit": k,
        "budget_tokens": budget_tokens,
        "mode": mode,
    }
    response = client.post("/v1/recall", payload)
    bodies = [item["body"] for item in response.get("items", [])]
    return bodies, bool(response.get("degraded", False))
