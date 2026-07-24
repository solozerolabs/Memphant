#!/usr/bin/env python3
"""MemPhant engine runner for the Syndai replacement gate (W10).

Ingests the pinned Syndai docs corpus into MemPhant as tri-domain RESOURCES via
the real runtime path (packaged ``memphant-server`` + ``memphant-worker`` +
``memphant-cli`` against Postgres), then calls ``/v1/recall`` (k=10) per golden
question and emits:
- an evidence JSONL in the exact shape ``scripts/run_reader.py`` consumes (so
  the SAME reader/judge scores this engine and Syndai);
- a provenance report with per-question span-level hit@5/hit@10 + R@5/R@10,
  graded by ``gate_common.provenance_hit`` (identical grading for both engines).

Ingest granularity: MemPhant's resource channel does not auto-chunk (one
resource body -> one whole-document unit), so to compare against Syndai's
internally-chunked search at the SAME granularity the gate pre-splits the corpus
into markdown sections (``gate_common``) and ingests each section as one
resource (``kind=document``). Every golden's source section is in the haystack,
so a perfect engine could hit @1.

Isolation: each run re-execs itself through ``scripts/with_scratch_db.sh``
(``gate_runtime.reexec_through_scratch_db``), so it operates on a fresh,
migrated, per-run scratch DB minted from ``--database-url`` (the campaign
*base* server, NOT a run DB) and dropped on exit — even if the bench is killed.
A freshly-minted tenant lives in that ephemeral DB. No shared named DB means
the worker's global oldest-first job-claim can never touch, or be starved by,
another harness's ``job_state`` debris. Mirrors the e2e probe + pg contract
tests, which already route through the same helper.

R0 embedder bakeoff (T3): ``--embed-model <id>`` threads ``MEMPHANT_EMBEDDINGS``
into BOTH the server and worker subprocess env, so one ingest can be scored
under any arm in the shared ``embedder_from_id`` grammar (memphant-runtime).
``--label`` tags stderr progress lines and the provenance header so artifacts
from a queue of arms are self-describing. Multiple golden sets (v1 + v2, which
share the identical pinned corpus) can be recalled against ONE ingest by
repeating ``--golden`` paired positionally with ``--out-evidence`` /
``--out-provenance`` — ingesting the corpus once and recalling N times, instead
of re-ingesting per golden set.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402

# The API-arm key map + fail-fast check live in the shared gate_runtime
# module (single source of truth for every gate runner — this script's copy
# and code_lane_run_memphant.py's copy drifted once already, so the map was
# centralized; pinned against the Rust `embedder_from_id` grammar by
# tests/test_gate_runtime.py). Names re-exported here unchanged so main()'s
# call site and any external reference keep working.
from gate_runtime import (  # noqa: E402, F401
    API_KEY_ENV_BY_ARM,
    ApiClient,
    check_embed_model_key,
    provision_tenant,
    reexec_through_scratch_db,
)

# Base campaign *server* URL to mint the per-run scratch DB from (with_scratch_db.sh
# only uses it to reach the server + the admin `postgres` DB; the named DB in it is
# never touched). The run itself uses a fresh ephemeral DB, never this one.
DEFAULT_BASE_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"
RECALL_E2E_P95_CEILING_MS = 1500
RETRIEVAL_BUDGET_TOKENS = 1_000_000
DEFAULT_SYNDAI_ROOT = Path("/Users/sidsharma/Syndai")
GOLDEN_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "syndai_docs_golden.jsonl"
# Strict-contract identity: every verb carries the five ids resolved by the
# ``bind_context`` handshake (C0; there is no tenant_id/raw-uuid path). All
# corpus sections share one fixed observed_at so relative ranking is unaffected.
OBSERVED_AT = "2026-07-01T00:00:00Z"
NEGATIVE_SCOPE_ADAPTER_MAPPING = {
    "wrong_tenant": "tenant_id",
    "wrong_user": "scope_id adapter mapping (not a native user dimension)",
    "wrong_project": "scope_id adapter mapping (not a native project dimension)",
    "wrong_agent": "scope_id adapter mapping (not a native agent dimension)",
}


def sh(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def check_port_free(port: int) -> None:
    """Refuse to spawn a server on `port` if something is already LISTENing
    there. A prior run that leaked its server child (e.g. died mid health-wait
    without being killed) can otherwise sit on the port forever, silently
    dropping every subsequent arm's server on an instant bind failure. Best
    effort: if ``lsof`` is unavailable, proceed without the check."""
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


HEALTH_WAIT_TIMEOUT_S = 600.0  # first boot may download embedding model weights (up to 1.5GB)
HEALTH_POLL_INTERVAL_S = 0.5
LOG_TAIL_LINES = 15


class Server:
    def __init__(
        self,
        server_bin: str,
        database_url: str,
        port: int,
        embed_model: str | None = None,
        log_path: Path | None = None,
        resource_chunks: bool = False,
        cross_rerank: bool = False,
        rerank_candidate_limit: int = 64,
        rerank_max_length: int = 512,
        rerank_batch_size: int = 256,
        cross_rerank_candidates: str = "fused-head",
        reranker: str = "fastembed",
        rerank_granularity: str | None = None,
    ) -> None:
        self.server_bin = server_bin
        self.database_url = database_url
        self.port = port
        self.embed_model = embed_model
        self.log_path = log_path
        self.resource_chunks = resource_chunks
        self.cross_rerank = cross_rerank
        self.rerank_candidate_limit = rerank_candidate_limit
        self.rerank_max_length = rerank_max_length
        self.rerank_batch_size = rerank_batch_size
        self.cross_rerank_candidates = cross_rerank_candidates
        self.reranker = reranker
        self.rerank_granularity = rerank_granularity
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

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)
        env.pop("MEMPHANT_CROSS_RERANK", None)
        env.pop("MEMPHANT_CROSS_RERANK_CANDIDATES", None)
        env.pop("MEMPHANT_RERANKER", None)
        env.pop("MEMPHANT_RERANK_MAX_LENGTH", None)
        env.pop("MEMPHANT_RERANK_BATCH_SIZE", None)
        env.pop("MEMPHANT_RERANK_GRANULARITY", None)
        env["MEMPHANT_APP_DATABASE_URL"] = self.database_url
        env["MEMPHANT_AUTHN_DATABASE_URL"] = self.database_url
        env["MEMPHANT_BIND"] = f"127.0.0.1:{self.port}"
        env.setdefault("RUST_LOG", "warn")
        if self.embed_model:
            env["MEMPHANT_EMBEDDINGS"] = self.embed_model
        if self.resource_chunks:
            env["MEMPHANT_RESOURCE_CHUNKS"] = "1"
        if self.cross_rerank:
            env["MEMPHANT_CROSS_RERANK"] = "1"
            env["MEMPHANT_RERANKER"] = self.reranker
        env["MEMPHANT_RERANK_CANDIDATE_LIMIT"] = str(self.rerank_candidate_limit)
        if self.reranker in ("fastembed", "byo"):
            env["MEMPHANT_RERANK_MAX_LENGTH"] = str(self.rerank_max_length)
            env["MEMPHANT_RERANK_BATCH_SIZE"] = str(self.rerank_batch_size)
        if self.rerank_granularity:
            env["MEMPHANT_RERANK_GRANULARITY"] = self.rerank_granularity
        env["MEMPHANT_CROSS_RERANK_CANDIDATES"] = self.cross_rerank_candidates
        return env

    def start(self) -> None:
        check_port_free(self.port)
        env = self.environment()
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


def bind_gate_context(client: ApiClient, label: str) -> dict:
    """One bound strict-contract context per gate scope. ``label`` picks the
    scope_ref, so 'active' and each negative scope kind resolve to distinct
    scopes under the same tenant (the scope_id adapter mapping recorded in
    ``NEGATIVE_SCOPE_ADAPTER_MAPPING``); subject/actor/agent refs are shared."""
    return client.bind_context(
        f"syndai-docs-gate:{label}",
        subject_ref="syndai-gate:subject",
        actor_ref="syndai-gate:actor",
        scope_ref=f"syndai-gate:scope:{label}",
        agent_node_ref="syndai-gate:agent",
    )


def ingest_section(
    client: ApiClient, ctx: dict, section: gc.Section, breadcrumb: bool = False
) -> str:
    """POSTs one section as a resource. ``breadcrumb=True`` prefixes the body
    with Syndai's "Section path: a > b" convention (``gate_common.
    breadcrumb_prefix`` — byte-identical to ``processing_chunks.py:84``)
    before hashing/posting it; the rest of the payload (uri, mime_type, kind,
    revision) is unaffected — only ``body`` (and the ``content_hash`` derived
    from it) changes."""
    body = section.body
    if breadcrumb:
        body = gc.breadcrumb_prefix(section.heading_path) + body
    payload = {
        **ctx,
        "source_ref": section.uri(),
        "observed_at": OBSERVED_AT,
        "payload": {
            "resource": {
                "uri": section.uri(),
                "mime_type": "text/markdown",
                "content_hash": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                "kind": "document",
                "revision": "syndai-gate",
                "body": body,
            }
        },
    }
    response = client.post("/v1/episodes", payload)
    return response.get("resource_id") or ""


def ingest_negative_document(client: ApiClient, ctx: dict, document: dict) -> str:
    body = document["body"]
    payload = {
        **ctx,
        "source_ref": f"memphant://negative/{document['document_id']}",
        "observed_at": document["valid_from"] or OBSERVED_AT,
    }
    if document["valid_to"] is not None:
        payload["payload"] = {
            "unit": {
                "kind": "semantic",
                "fact_key": f"negative:{document['document_id']}",
                "predicate": "fixture_value",
                "body": body,
                "confidence": 1.0,
                "valid_from": document["valid_from"],
                "valid_to": document["valid_to"],
            }
        }
    else:
        payload["payload"] = {
            "resource": {
                "uri": f"memphant://negative/{document['document_id']}",
                "mime_type": "text/plain",
                "content_hash": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                "kind": "document",
                "revision": "syndai-docs-negative-v1",
                "body": body,
            }
        }
    response = client.post("/v1/episodes", payload)
    if "unit" in payload["payload"]:
        unit_ids = response.get("unit_ids")
        if not isinstance(unit_ids, list) or len(unit_ids) != 1 or not isinstance(unit_ids[0], str):
            raise RuntimeError("stale negative direct retain did not create exactly one unit")
        return unit_ids[0]
    return response.get("resource_id") or ""


def drain_worker(
    worker_bin: str,
    database_url: str,
    embed_model: str | None = None,
    resource_chunks: bool = False,
) -> int:
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("MEMPHANT_CROSS_RERANK", None)
    env.pop("MEMPHANT_CROSS_RERANK_CANDIDATES", None)
    env.pop("MEMPHANT_RERANKER", None)
    env.pop("MEMPHANT_WORKER_ONCE", None)
    env["MEMPHANT_WORKER_DATABASE_URL"] = database_url
    env["MEMPHANT_WORKER_DRAIN"] = "1"
    env.setdefault("RUST_LOG", "warn")
    if embed_model:
        env["MEMPHANT_EMBEDDINGS"] = embed_model
    if resource_chunks:
        env["MEMPHANT_RESOURCE_CHUNKS"] = "1"
    out = sh([worker_bin], env=env)
    if out.returncode != 0:
        raise RuntimeError(f"worker drain failed: {out.stderr.strip()[:300]}")
    match = re.fullmatch(r"memphant-worker: drain completed=(0|[1-9]\d*)\n?", out.stdout)
    if match is None:
        raise RuntimeError(f"worker drain completion output is malformed: {out.stdout[:300]!r}")
    return int(match.group(1))


def _non_negative_int(value: object, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < int(positive):
        qualifier = "positive" if positive else "non-negative"
        raise RuntimeError(f"trace cross_rerank.{field} must be a {qualifier} integer")
    return value


def _cross_rerank_facts(
    trace: dict,
    enabled: bool,
    expected_config: dict[str, object] | None,
) -> dict | None:
    facts = trace.get("cross_rerank")
    if not enabled:
        return None
    if not isinstance(facts, dict):
        raise RuntimeError("trace cross_rerank facts are required for a cross-rerank arm")
    if expected_config is None:
        raise RuntimeError("requested config is required for a cross-rerank arm")

    required = {
        "provider", "model", "candidate_limit", "candidate_count", "max_length",
        "batch_size", "input_chars_p50", "input_chars_p95", "input_chars_max", "failure",
    }
    missing = sorted(required - facts.keys())
    if missing:
        raise RuntimeError(f"trace cross_rerank missing exact facts: {', '.join(missing)}")
    for field in ("provider", "model"):
        if not isinstance(facts[field], str) or not facts[field].strip():
            raise RuntimeError(f"trace cross_rerank.{field} must be a non-empty string")
    for field in ("candidate_limit", "max_length"):
        _non_negative_int(facts[field], field, positive=True)
    if facts["batch_size"] is not None:
        _non_negative_int(facts["batch_size"], "batch_size", positive=True)
    for field in ("candidate_count", "input_chars_p50", "input_chars_p95", "input_chars_max"):
        _non_negative_int(facts[field], field)
    if facts["candidate_count"] > facts["candidate_limit"]:
        raise RuntimeError("trace cross_rerank candidate_count exceeds candidate_limit")
    if facts["candidate_count"] == 0:
        raise RuntimeError("trace cross_rerank candidate_count must be positive")
    if any(facts[field] != expected_config.get(field) for field in expected_config):
        raise RuntimeError("trace cross_rerank facts do not match requested config")
    if not (
        facts["input_chars_p50"]
        <= facts["input_chars_p95"]
        <= facts["input_chars_max"]
    ):
        raise RuntimeError("trace cross_rerank input_chars percentiles are malformed")
    if facts["failure"] != "none":
        raise RuntimeError(f"reranker failure: {facts['failure']!r}")

    cross_rerank_ms = _non_negative_int(trace.get("cross_rerank_ms"), "cross_rerank_ms")
    return {field: facts[field] for field in sorted(required)} | {
        "cross_rerank_ms": cross_rerank_ms
    }


def recall(
    client: ApiClient,
    ctx: dict,
    question: str,
    k: int,
    budget_tokens: int,
    mode: str,
    *,
    cross_rerank: bool = False,
    expected_rerank_config: dict[str, object] | None = None,
    transaction_as_of: str | None = None,
    valid_at: str | None = None,
) -> tuple[list[str], str, dict | None, int, int, int]:
    payload = {
        **ctx,
        "query": question,
        "limit": k,
        # Raise the pack budget so the top-k ranked units are returned rather
        # than truncated to the default 512-token answer budget — this makes the
        # k=10 comparison against Syndai's raw top-k retrieval apples-to-apples.
        "budget_tokens": budget_tokens,
        "mode": mode,
    }
    if transaction_as_of is not None:
        payload["transaction_as_of"] = transaction_as_of
    if valid_at is not None:
        payload["valid_at"] = valid_at
    started_ns = time.perf_counter_ns()
    response = client.post("/v1/recall", payload)
    post_finished_ns = time.perf_counter_ns()
    if response.get("degraded") is not False:
        raise RuntimeError("recall gate is invalid: response is degraded or missing degraded=false")
    trace_id = response.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise RuntimeError("recall response is missing trace_id")
    items = response.get("items")
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("body"), str)
        for item in items
    ):
        raise RuntimeError("recall response items are malformed")
    # The strict trace endpoint resolves the same bound context as recall, so
    # the five ids + generation ride as query params (GET has no body).
    trace_query = urllib.parse.urlencode(
        {
            "subject_id": ctx["subject_id"],
            "scope_id": ctx["scope_id"],
            "actor_id": ctx["actor_id"],
            "agent_node_id": ctx["agent_node_id"],
            "subject_generation": ctx["subject_generation"],
        }
    )
    trace = client.get(f"/v1/traces/{trace_id}?{trace_query}")
    trace_finished_ns = time.perf_counter_ns()
    if not isinstance(trace, dict) or trace.get("id") != trace_id:
        raise RuntimeError(f"missing or mismatched recall trace for trace_id={trace_id}")
    reranker_facts = _cross_rerank_facts(
        trace, cross_rerank, expected_rerank_config
    )
    elapsed_ns = trace_finished_ns - started_ns
    if elapsed_ns < 0:
        raise RuntimeError("recall end-to-end monotonic clock moved backwards")
    recall_post_ms = (post_finished_ns - started_ns + 999_999) // 1_000_000
    trace_read_ms = (trace_finished_ns - post_finished_ns + 999_999) // 1_000_000
    recall_e2e_ms = (elapsed_ns + 999_999) // 1_000_000
    return (
        [item["body"] for item in items],
        trace_id,
        reranker_facts,
        recall_post_ms,
        trace_read_ms,
        recall_e2e_ms,
    )


def _percentile(values: list[int], percentile: int) -> int | float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


def corpus_revision(sections: list[gc.Section]) -> str:
    return gc.corpus_revision(sections)


def validate_golden_lock(goldens: list[dict], lock: dict, actual_sha: str) -> int:
    count = lock.get("count")
    if type(count) is not int or count <= 0 or count != len(goldens):
        raise RuntimeError(
            f"golden lock count is missing or invalid: lock={count!r} rows={len(goldens)}"
        )
    if lock.get("sha256") != actual_sha:
        raise RuntimeError(
            f"golden sha256 mismatch: file={actual_sha[:12]} "
            f"lock={str(lock.get('sha256'))[:12]}"
        )
    return count


def build_provenance_report(
    *,
    embed_model: str | None,
    label: str | None,
    breadcrumb: bool,
    golden_path: Path,
    resource_chunks: bool = False,
    cross_rerank: bool = False,
    database_url: str,
    k: int,
    mode: str,
    budget_tokens: int,
    haystack_len: int,
    golden_sha: str,
    provenance_rows: list[dict],
    generation_identity: dict | None = None,
    corpus_revision_id: str | None = None,
    expected_n: int | None = None,
    requested_rerank_config: dict[str, object] | None = None,
    cross_rerank_candidates: str = "fused-head",
) -> dict:
    """Assembles the self-describing provenance-report header + per-question
    rows. ``breadcrumb`` records whether ``--breadcrumb`` was set for this
    run (R1-T1); ``resource_chunks`` records whether ``--resource-chunks``
    was set (R1-T3); ``cross_rerank`` records whether ``--cross-rerank`` was
    set (R1.5-T1, the W8 cross-encoder rerank of the deep pool — distinct
    from the retired heuristic rerank) so artifacts are self-describing
    without re-deriving any of them from the label string."""
    n = len(provenance_rows)
    if expected_n is None:
        expected_n = 0
    if type(expected_n) is not int or expected_n != n:
        raise RuntimeError(f"authoritative expected_n={expected_n!r} does not match rows={n}")
    if n and not corpus_revision_id:
        raise RuntimeError("corpus_revision is required for a non-empty provenance report")
    recall_e2e_latencies = []
    recall_post_latencies = []
    trace_read_latencies = []
    for index, row in enumerate(provenance_rows):
        value = row.get("recall_e2e_ms")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(
                f"provenance row {index} recall_e2e_ms must be a non-negative integer"
            )
        recall_e2e_latencies.append(value)
        for field, target in (
            ("recall_post_ms", recall_post_latencies),
            ("trace_read_ms", trace_read_latencies),
        ):
            latency = row.get(field)
            if latency is not None:
                if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
                    raise RuntimeError(f"provenance row {index} {field} must be non-negative")
                target.append(latency)
    degraded_count = sum(row.get("degraded") is not False for row in provenance_rows)
    fallback_count = sum(row.get("fallback") is not False for row in provenance_rows)
    skipped_count = sum(row.get("skipped") is not False for row in provenance_rows)
    reranker_failure_count = sum(
        row.get("failure") != "none" for row in provenance_rows
    ) if cross_rerank else 0
    if degraded_count or fallback_count or skipped_count or reranker_failure_count:
        raise RuntimeError("provenance report contains degraded, fallback, skipped, or failed rows")
    golden_revision = "sha256:" + golden_sha
    r5 = sum(r["hit_at_5"] for r in provenance_rows) / n if n else 0.0
    r10 = sum(r["hit_at_10"] for r in provenance_rows) / n if n else 0.0
    reranker_config = None
    if cross_rerank and provenance_rows:
        static_fields = ("provider", "model", "candidate_limit", "max_length", "batch_size")
        configs = {
            tuple(row.get(field) for field in static_fields) for row in provenance_rows
        }
        if len(configs) != 1 or any(
            value is None
            for field, value in zip(static_fields, next(iter(configs)))
            if field != "batch_size"
        ):
            raise RuntimeError("cross-rerank provenance rows have missing or inconsistent static config")
        reranker_config = dict(zip(static_fields, next(iter(configs))))
        if requested_rerank_config is None or any(
            reranker_config[field] != requested_rerank_config.get(field)
            for field in static_fields
        ):
            raise RuntimeError("observed reranker config does not match requested config")
    runtime_config = {
        "runtime": "memphant-server resource ingest + /v1/recall",
        "embed_model": embed_model,
        "breadcrumb": breadcrumb,
        "resource_chunks": resource_chunks,
        "cross_rerank": cross_rerank,
        "cross_rerank_candidates": cross_rerank_candidates,
        "cross_reranker": reranker_config,
        "requested_cross_reranker": requested_rerank_config,
        "k": k,
        "recall_mode": mode,
        "budget_tokens": budget_tokens,
        "retrieval_budget_tokens": RETRIEVAL_BUDGET_TOKENS,
        "evidence_packer": gc.EVIDENCE_PACKER_CONFIG,
        "haystack_sections": haystack_len,
        "golden_revision": golden_revision,
        "corpus_revision": corpus_revision_id,
    }
    if generation_identity is not None:
        runtime_config["generation_identity"] = generation_identity
    fingerprint = hashlib.sha256(
        json.dumps(runtime_config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    latencies = [r["cross_rerank_ms"] for r in provenance_rows if "cross_rerank_ms" in r]
    recall_e2e_p95 = _percentile(recall_e2e_latencies, 95)
    recall_post_p95 = _percentile(recall_post_latencies, 95)
    trace_read_p95 = _percentile(trace_read_latencies, 95)
    oracle_hits = [
        row["candidate_oracle_hit_at_64"]
        for row in provenance_rows
        if "candidate_oracle_hit_at_64" in row
    ]
    char_p50s = [r["input_chars_p50"] for r in provenance_rows if "input_chars_p50" in r]
    char_p95s = [r["input_chars_p95"] for r in provenance_rows if "input_chars_p95" in r]
    char_maxes = [r["input_chars_max"] for r in provenance_rows if "input_chars_max" in r]
    return {
        "engine": "memphant",
        "runtime": "memphant-server resource ingest + /v1/recall",
        "embed_model": embed_model,
        "label": label,
        "breadcrumb": breadcrumb,
        "resource_chunks": resource_chunks,
        "cross_rerank": cross_rerank,
        "cross_rerank_candidates": cross_rerank_candidates,
        "golden_path": str(golden_path),
        "database_url_db": database_url.rsplit("/", 1)[-1],
        "k": k,
        "recall_mode": mode,
        "budget_tokens": budget_tokens,
        "haystack_sections": haystack_len,
        "golden_sha256": golden_sha,
        "golden_revision": golden_revision,
        "corpus_revision": corpus_revision_id,
        "golden_count": n,
        "expected_n": expected_n,
        "runtime_config": runtime_config,
        "runtime_config_fingerprint": fingerprint,
        "fallback_count": fallback_count,
        "degraded_count": degraded_count,
        "skipped_count": skipped_count,
        "reranker_failure_count": reranker_failure_count,
        "cross_rerank_ms_p50": statistics.median(latencies) if latencies else None,
        "cross_rerank_ms_p95": _percentile(latencies, 95),
        "recall_e2e_ms_p50": (
            statistics.median(recall_e2e_latencies) if recall_e2e_latencies else None
        ),
        "recall_e2e_ms_p95": recall_e2e_p95,
        "recall_post_ms_p50": (
            statistics.median(recall_post_latencies) if recall_post_latencies else None
        ),
        "recall_post_ms_p95": recall_post_p95,
        "trace_read_ms_p50": (
            statistics.median(trace_read_latencies) if trace_read_latencies else None
        ),
        "trace_read_ms_p95": trace_read_p95,
        "candidate_oracle_recall_at_64": (
            sum(oracle_hits) / len(oracle_hits) if len(oracle_hits) == n and n else None
        ),
        "recall_e2e_p95_ceiling_ms": RECALL_E2E_P95_CEILING_MS,
        "recall_e2e_p95_within_ceiling": (
            recall_e2e_p95 <= RECALL_E2E_P95_CEILING_MS
            if recall_e2e_p95 is not None
            else None
        ),
        "input_chars_p50": statistics.median(char_p50s) if char_p50s else None,
        "input_chars_p95": _percentile(char_p95s, 95),
        "input_chars_max": max(char_maxes) if char_maxes else None,
        "recall_at_5": r5,
        "recall_at_10": r10,
        "per_question": provenance_rows,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    def positive_int(value: str) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise argparse.ArgumentTypeError("must be positive")
        return parsed

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default=DEFAULT_BASE_DATABASE_URL,
        help="base campaign SERVER url to mint the per-run scratch DB from; the "
             "run uses a fresh ephemeral DB dropped on exit, never this one",
    )
    parser.add_argument("--syndai-root", default=str(DEFAULT_SYNDAI_ROOT))
    parser.add_argument(
        "--manifest",
        default=None,
        help=(
            "corpus manifest lock to verify against (default: the committed "
            "benchmarks/manifests/syndai_docs_gate.lock.json). Pass an archived "
            "manifest to reproduce a run against an OLD corpus pin (paired with "
            "--syndai-root at a checkout/archive of that pin)."
        ),
    )
    parser.add_argument(
        "--golden",
        action="append",
        default=None,
        help=(
            "golden JSONL path (default: the v1 syndai_docs_golden.jsonl). "
            "Repeatable: recall MULTIPLE golden sets against ONE ingest, "
            "paired positionally with --out-evidence/--out-provenance."
        ),
    )
    parser.add_argument(
        "--out-evidence",
        action="append",
        required=True,
        help="evidence JSONL output path; one per --golden (positional pairing)",
    )
    parser.add_argument(
        "--out-provenance",
        action="append",
        required=True,
        help="provenance report JSON output path; one per --golden (positional pairing)",
    )
    parser.add_argument("--negative-slice", help="hash-locked negative JSONL")
    parser.add_argument("--out-negative-evidence", help="separate abstention evidence JSONL")
    parser.add_argument(
        "--embed-model",
        default=None,
        help=(
            "MEMPHANT_EMBEDDINGS id (memphant-runtime embedder_from_id grammar) "
            "passed into BOTH the server and worker subprocess env; default "
            "unset = shipped default embedder"
        ),
    )
    parser.add_argument(
        "--label",
        default=None,
        help="arm label prefixed on stderr progress lines and recorded in the provenance header",
    )
    parser.add_argument(
        "--breadcrumb",
        action="store_true",
        help=(
            "prefix each ingested section body with Syndai's deterministic "
            "context-prefix convention (processing_chunks.py:84): "
            "'Section path: ' + ' > '.join(heading_path) + a blank line, "
            "byte-identical to Syndai's own embedding-input prefix. Sections "
            "with an empty heading_path get no prefix, mirroring Syndai's "
            "own check exactly. Recorded as 'breadcrumb' in the provenance "
            "header."
        ),
    )
    parser.add_argument(
        "--resource-chunks",
        action="store_true",
        help=(
            "enable the R1 docs-domain resource-chunk write path by setting "
            "MEMPHANT_RESOURCE_CHUNKS=1 for BOTH the server and worker "
            "subprocess env, so reflect mints per-resource contextual chunks "
            "for kind=document sections (the flag-gated, default-off twin of "
            "the promoted episode chunks). Recorded as 'resource_chunks' in the "
            "provenance header."
        ),
    )
    parser.add_argument(
        "--cross-rerank",
        action="store_true",
        help=(
            "enable the R1.5-T1 W8 cross-encoder rerank of the deep recall "
            "pool by setting MEMPHANT_CROSS_RERANK=1 for the server subprocess, "
            "so the configured candidate head of the fused pool "
            "candidates are reordered by a real (query, body) cross-encoder "
            "(bge-reranker-base) before packing — requires a server built "
            "with --features fastembed. The worker never loads a reranker. Distinct from the retired, "
            "measured-harmful heuristic rerank (never exposed by this "
            "script). Recorded as 'cross_rerank' in the provenance header."
        ),
    )
    parser.add_argument("--rerank-candidate-limit", type=positive_int, default=64)
    parser.add_argument("--rerank-max-length", type=positive_int, default=512)
    parser.add_argument("--rerank-batch-size", type=positive_int, default=256)
    parser.add_argument(
        "--reranker",
        choices=("fastembed", "byo", "voyage-rerank-2.5"),
        default="fastembed",
        help=(
            "construction-time cross-reranker implementation; no generic "
            "provider routing. 'byo' loads the local ONNX + tokenizer dir from "
            "MEMPHANT_RERANK_BYO_DIR (e.g. ms-marco-MiniLM-L6-v2 int8, the "
            "reranker-latency-spike winner)"
        ),
    )
    parser.add_argument(
        "--cross-rerank-candidates",
        choices=("fused-head", "vector-lexical-balanced"),
        default="fused-head",
    )
    parser.add_argument(
        "--rerank-granularity",
        choices=("body", "chunk"),
        default=None,
        help=(
            "MEMPHANT_RERANK_GRANULARITY: 'body' reranks whole section bodies "
            "(default); 'chunk' reranks each candidate's contextual_chunks and "
            "max-pools to the unit (needs --resource-chunks so sections carry "
            "chunks). The reranker-latency-spike fix for long docs past the "
            "512-token wall."
        ),
    )
    parser.add_argument(
        "--candidate-oracle-k",
        type=int,
        choices=(0, 64),
        default=0,
        help="0 disables; 64 runs a separate high-budget candidate-oracle recall",
    )
    parser.add_argument("--port", type=int, default=39412)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--budget-tokens", type=int, default=8192)
    parser.add_argument("--mode", default="deep", choices=("fast", "deep"))
    parser.add_argument("--limit-haystack", type=int, default=0, help="0 = full corpus")
    parser.add_argument("--server-bin", default=str(gc.MEMPHANT_ROOT / "target/debug/memphant-server"))
    parser.add_argument("--worker-bin", default=str(gc.MEMPHANT_ROOT / "target/debug/memphant-worker"))
    parser.add_argument("--cli-bin", default=str(gc.MEMPHANT_ROOT / "target/debug/memphant-cli"))
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # Re-exec through with_scratch_db.sh (unless already inside one): mints a
    # fresh migrated DB, drops it on exit. args.database_url then points at that
    # scratch DB for every downstream call (provision/server/worker/report).
    reexec_through_scratch_db(args.database_url)
    args.database_url = os.environ["DATABASE_URL"]

    golden_paths = [Path(p) for p in (args.golden or [str(GOLDEN_PATH)])]
    if not (len(golden_paths) == len(args.out_evidence) == len(args.out_provenance)):
        raise RuntimeError(
            "--golden / --out-evidence / --out-provenance counts must match "
            f"(got {len(golden_paths)}/{len(args.out_evidence)}/{len(args.out_provenance)}); "
            "each --golden is paired positionally with one --out-evidence and one --out-provenance"
        )

    check_embed_model_key(args.embed_model)
    label_prefix = f"[{args.label}] " if args.label else ""

    golden_sets: list[tuple[Path, list[dict], str, int]] = []
    for golden_path in golden_paths:
        goldens = gc.load_goldens(golden_path)
        lock = json.loads(gc.golden_lock_path(golden_path).read_text())
        actual_sha = gc.sha256_hex(golden_path.read_bytes())
        expected_n = validate_golden_lock(goldens, lock, actual_sha)
        print(
            f"{label_prefix}goldens={len(goldens)} path={golden_path.name} "
            f"sha256={actual_sha[:12]} (lock verified)",
            file=sys.stderr,
        )
        golden_sets.append((golden_path, goldens, actual_sha, expected_n))
    if bool(args.negative_slice) != bool(args.out_negative_evidence):
        raise RuntimeError("--negative-slice and --out-negative-evidence must be supplied together")
    negative_cases = None
    if args.negative_slice:
        negative_path = Path(args.negative_slice)
        positive_ids = {
            golden["question_id"] for _, goldens, _, _ in golden_sets for golden in goldens
        }
        negative_cases = gc.load_negative_cases(
            negative_path,
            gc.golden_lock_path(negative_path),
            disjoint_question_ids=positive_ids,
        )

    root = Path(args.syndai_root)
    if args.manifest:
        files, haystack, corpus_manifest = gc.load_pinned_corpus(
            root, manifest_path=Path(args.manifest)
        )
    else:
        files, haystack, corpus_manifest = gc.load_pinned_corpus(root)
    if args.limit_haystack:
        raise RuntimeError("--limit-haystack violates the full common-corpus contract")
    # Coverage assertion: every gold section (in every golden set) must be
    # ingestable.
    keys = {s.key() for s in haystack}
    for _, goldens, _, _ in golden_sets:
        for g in goldens:
            for part in g["source_section_key"].split("||"):
                if part not in keys:
                    raise RuntimeError(f"gold section not in haystack: {part}")
    print(f"{label_prefix}haystack sections={len(haystack)}", file=sys.stderr)
    corpus_revision_id = corpus_manifest["section_revision"]
    run_identity = gc.generation_identity(
        root=gc.MEMPHANT_ROOT,
        files={
            "runner": Path(__file__),
            "gate_common": Path(gc.__file__),
            "server": Path(args.server_bin),
            "worker": Path(args.worker_bin),
            "cli": Path(args.cli_bin),
        },
    )
    run_identity["database"] = gc.database_schema_identity(
        args.database_url,
        "select 'migration:' || version from memphant.schema_migrations",
    )
    run_identity["migration_sources"] = gc.sql_sources_identity(
        gc.MEMPHANT_ROOT / "memphant_migrations"
    )
    run_identity["sha256"] = gc.json_fingerprint(
        {key: value for key, value in run_identity.items() if key != "sha256"}
    )

    tenant_id, api_key = provision_tenant(
        args.cli_bin, args.database_url, name_prefix="syndai-gate"
    )
    other_tenant = (
        provision_tenant(args.cli_bin, args.database_url, name_prefix="syndai-gate-other")
        if negative_cases
        else None
    )
    print(f"{label_prefix}tenant={tenant_id}", file=sys.stderr)

    log_name = f"server-{args.label}.log" if args.label else "server.log"
    server_log_path = Path(args.out_provenance[0]).resolve().parent / log_name
    server = Server(
        args.server_bin, args.database_url, args.port, args.embed_model,
        log_path=server_log_path,
        resource_chunks=args.resource_chunks,
        cross_rerank=args.cross_rerank,
        rerank_candidate_limit=args.rerank_candidate_limit,
        rerank_max_length=args.rerank_max_length,
        rerank_batch_size=args.rerank_batch_size,
        cross_rerank_candidates=args.cross_rerank_candidates,
        reranker=args.reranker,
        rerank_granularity=args.rerank_granularity,
    )
    # Symmetric cleanup: start() and the ingest/recall body are both inside
    # this try so the server child is always killed on any exception path,
    # not just after a successful start (a failed start() already
    # self-terminates before raising; stop() here is then a safe no-op).
    try:
        server.start()
        client = ApiClient(args.port, api_key, tenant_id)
        other_client = (
            ApiClient(args.port, other_tenant[1], other_tenant[0]) if other_tenant else None
        )
        ctx = bind_gate_context(client, "active")
        negative_contexts = (
            {
                scope: bind_gate_context(client, scope)
                for scope in ("other_user", "other_project", "other_agent")
            }
            | {"active": ctx}
            if negative_cases
            else {}
        )
        other_ctx = bind_gate_context(other_client, "active") if other_client else None
        t0 = time.time()
        for i, section in enumerate(haystack):
            ingest_section(client, ctx, section, breadcrumb=args.breadcrumb)
            if (i + 1) % 500 == 0:
                print(f"{label_prefix}  ingested {i + 1}/{len(haystack)}", file=sys.stderr)
        if negative_cases:
            for case in negative_cases:
                for document in gc.negative_ingest_projection(case):
                    if document["scope"] == "other_tenant":
                        assert other_client is not None and other_ctx is not None
                        ingest_negative_document(other_client, other_ctx, document)
                    else:
                        ingest_negative_document(
                            client, negative_contexts[document["scope"]], document
                        )
        print(f"{label_prefix}ingest done in {time.time() - t0:.1f}s; draining worker...", file=sys.stderr)
        compiled = drain_worker(
            args.worker_bin, args.database_url, args.embed_model,
            resource_chunks=args.resource_chunks,
        )
        print(f"{label_prefix}worker drained: compiled={compiled} jobs", file=sys.stderr)

        if args.reranker == "voyage-rerank-2.5":
            requested_rerank_config = {
                "provider": "voyage",
                "model": "rerank-2.5",
                "candidate_limit": args.rerank_candidate_limit,
                "max_length": 32_000,
                "batch_size": None,
            }
        elif args.reranker == "byo":
            requested_rerank_config = {
                "provider": "byo",
                "model": "byo:"
                + os.environ.get("MEMPHANT_RERANK_BYO_ONNX", "model_quantized.onnx"),
                "candidate_limit": args.rerank_candidate_limit,
                "max_length": args.rerank_max_length,
                "batch_size": args.rerank_batch_size,
            }
        else:
            requested_rerank_config = {
                "provider": "fastembed",
                "model": "fastembed:bge-reranker-base",
                "candidate_limit": args.rerank_candidate_limit,
                "max_length": args.rerank_max_length,
                "batch_size": args.rerank_batch_size,
            }
        negative_summary = None
        if negative_cases:
            negative_evidence = []
            negative_rows = []
            for case in negative_cases:
                supported = True
                raw_bodies = []
                if supported:
                    query = gc.negative_query_projection(case)
                    raw_bodies, *_ = recall(
                        client,
                        ctx,
                        query["question"],
                        args.k,
                        RETRIEVAL_BUDGET_TOKENS,
                        args.mode,
                        cross_rerank=args.cross_rerank,
                        expected_rerank_config=requested_rerank_config,
                        transaction_as_of=query["transaction_as_of"],
                        valid_at=query["valid_at"],
                    )
                bodies, _ = gc.pack_evidence(
                    raw_bodies, k=args.k, budget_tokens=args.budget_tokens
                )
                negative_evidence.append(gc.negative_evidence_row(case, bodies, k=args.k))
                negative_rows.append(
                    gc.negative_result_row(case, raw_bodies[: args.k], supported=supported)
                )
            negative_evidence_path = Path(args.out_negative_evidence)
            gc.write_jsonl(negative_evidence_path, negative_evidence)
            negative_summary = gc.negative_report(negative_rows) | {
                "negative_evidence_sha256": gc.file_sha256(negative_evidence_path),
                "scope_adapter_mapping": NEGATIVE_SCOPE_ADAPTER_MAPPING,
            }

        for (golden_path, goldens, golden_sha, expected_n), out_evidence, out_provenance in zip(
            golden_sets, args.out_evidence, args.out_provenance
        ):
            evidence_rows = []
            provenance_rows = []
            for i, golden in enumerate(goldens):
                (
                    bodies,
                    trace_id,
                    reranker_facts,
                    recall_post_ms,
                    trace_read_ms,
                    recall_e2e_ms,
                ) = recall(
                    client, ctx, golden["question"], args.k, RETRIEVAL_BUDGET_TOKENS, args.mode,
                    cross_rerank=args.cross_rerank,
                    expected_rerank_config=requested_rerank_config,
                )
                oracle_hit = None
                if args.candidate_oracle_k:
                    oracle_bodies, *_ = recall(
                        client,
                        ctx,
                        golden["question"],
                        args.candidate_oracle_k,
                        1_000_000,
                        args.mode,
                        cross_rerank=args.cross_rerank,
                        expected_rerank_config=requested_rerank_config,
                    )
                    oracle_hit = gc.provenance_hit(
                        golden, oracle_bodies, args.candidate_oracle_k
                    )
                packed_bodies, pack_facts = gc.pack_evidence(
                    bodies, k=args.k, budget_tokens=args.budget_tokens
                )
                evidence_rows.append(gc.evidence_row(golden, packed_bodies, args.k))
                provenance_rows.append(
                    {
                        "question_id": golden["question_id"],
                        "question_type": golden["question_type"],
                        "multi_hop": golden["multi_hop"],
                        "trace_id": trace_id,
                        "recall_e2e_ms": recall_e2e_ms,
                        "recall_post_ms": recall_post_ms,
                        "trace_read_ms": trace_read_ms,
                        "returned_items": len(bodies),
                        "packed_items": len(packed_bodies),
                        "degraded": False,
                        "fallback": False,
                        "skipped": False,
                        "hit_at_5": gc.provenance_hit(golden, packed_bodies, 5),
                        "hit_at_10": gc.provenance_hit(golden, packed_bodies, min(10, args.k)),
                    }
                    | (
                        {"candidate_oracle_hit_at_64": oracle_hit}
                        if oracle_hit is not None
                        else {}
                    )
                    | pack_facts
                    | (reranker_facts or {})
                )
                if (i + 1) % 20 == 0:
                    print(
                        f"{label_prefix}  recalled {i + 1}/{len(goldens)} ({golden_path.name})",
                        file=sys.stderr,
                    )

            gc.write_jsonl(Path(out_evidence), evidence_rows)
            report = build_provenance_report(
                embed_model=args.embed_model,
                label=args.label,
                breadcrumb=args.breadcrumb,
                resource_chunks=args.resource_chunks,
                cross_rerank=args.cross_rerank,
                golden_path=golden_path,
                database_url=args.database_url,
                k=args.k,
                mode=args.mode,
                budget_tokens=args.budget_tokens,
                haystack_len=len(haystack),
                golden_sha=golden_sha,
                provenance_rows=provenance_rows,
                generation_identity=run_identity,
                corpus_revision_id=corpus_revision_id,
                expected_n=expected_n,
                requested_rerank_config=requested_rerank_config,
                cross_rerank_candidates=args.cross_rerank_candidates,
            )
            if negative_summary is not None:
                report["negative"] = negative_summary
            gc.finalize_provenance_report(report, Path(out_evidence))
            Path(out_provenance).write_text(json.dumps(report, indent=2) + "\n")
            print(
                f"{label_prefix}{golden_path.name} done: R@5={report['recall_at_5']:.3f} "
                f"R@10={report['recall_at_10']:.3f} n={report['golden_count']} "
                f"evidence={out_evidence} provenance={out_provenance}",
                file=sys.stderr,
            )
    finally:
        server.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
