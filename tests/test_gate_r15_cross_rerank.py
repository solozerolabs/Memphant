"""Unit tests for the R1.5-T1 ``--cross-rerank`` lever (docs-gate harness).

``gate_run_memphant.py --cross-rerank`` sets ``MEMPHANT_CROSS_RERANK=1`` for
the server subprocess only (so recall reorders the top
``recall_pool_depth`` fused candidates via the W8 cross-encoder,
``bge-reranker-base``, before packing), and records ``cross_rerank`` in the
self-describing provenance header. Mirrors
``test_gate_r1_resource_chunks.py`` exactly — this is a DIFFERENT, distinctly
named lever from the retired heuristic rerank (never exposed by this script).

All pure functions here (argparse + header assembly): no network, no DB, no
subprocess spawn — same constraint as ``test_gate_r1_resource_chunks.py``.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CTX = {
    "subject_id": "00000000-0000-0000-0000-000000000011",
    "scope_id": "00000000-0000-0000-0000-000000000012",
    "actor_id": "00000000-0000-0000-0000-000000000013",
    "agent_node_id": "00000000-0000-0000-0000-000000000014",
    "subject_generation": 0,
}


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gr():
    return _load("gate_run_memphant", "scripts/gate_run_memphant.py")


# --- --cross-rerank argparse wiring ----------------------------------------


def test_cross_rerank_flag_defaults_false(gr):
    parser = gr.build_arg_parser()
    args = parser.parse_args(["--out-evidence", "e.jsonl", "--out-provenance", "p.json"])
    assert args.cross_rerank is False


def test_cross_rerank_flag_sets_true(gr):
    parser = gr.build_arg_parser()
    args = parser.parse_args(
        ["--out-evidence", "e.jsonl", "--out-provenance", "p.json", "--cross-rerank"]
    )
    assert args.cross_rerank is True


def test_rerank_config_flags_have_pinned_defaults_and_accept_overrides(gr):
    parser = gr.build_arg_parser()
    defaults = parser.parse_args(["--out-evidence", "e", "--out-provenance", "p"])
    assert (
        defaults.rerank_candidate_limit,
        defaults.rerank_max_length,
        defaults.rerank_batch_size,
    ) == (64, 512, 256)
    custom = parser.parse_args(
        [
            "--out-evidence", "e", "--out-provenance", "p", "--cross-rerank",
            "--rerank-candidate-limit", "16", "--rerank-max-length", "128",
            "--rerank-batch-size", "8",
        ]
    )
    assert (
        custom.rerank_candidate_limit,
        custom.rerank_max_length,
        custom.rerank_batch_size,
    ) == (16, 128, 8)


def test_voyage_reranker_is_an_explicit_construction_time_arm(gr):
    parser = gr.build_arg_parser()
    args = parser.parse_args([
        "--out-evidence", "e", "--out-provenance", "p", "--cross-rerank",
        "--reranker", "voyage-rerank-2.5", "--rerank-candidate-limit", "32",
    ])
    assert args.reranker == "voyage-rerank-2.5"
    server = gr.Server(
        "srv", "postgres://x/db", 39412, cross_rerank=True,
        reranker=args.reranker, rerank_candidate_limit=32,
    )
    env = server.environment()
    assert env["MEMPHANT_RERANKER"] == "voyage-rerank-2.5"
    assert env["MEMPHANT_RERANK_CANDIDATE_LIMIT"] == "32"
    assert "MEMPHANT_RERANK_MAX_LENGTH" not in env
    assert "MEMPHANT_RERANK_BATCH_SIZE" not in env


def test_candidate_selection_and_oracle_flags_are_explicit(gr):
    parser = gr.build_arg_parser()
    defaults = parser.parse_args(["--out-evidence", "e", "--out-provenance", "p"])
    assert defaults.cross_rerank_candidates == "fused-head"
    assert defaults.candidate_oracle_k == 0
    selected = parser.parse_args([
        "--out-evidence", "e", "--out-provenance", "p",
        "--cross-rerank-candidates", "vector-lexical-balanced",
        "--candidate-oracle-k", "64",
    ])
    assert selected.cross_rerank_candidates == "vector-lexical-balanced"
    assert selected.candidate_oracle_k == 64


# --- server / worker env pass-through --------------------------------------


def test_server_carries_cross_rerank_flag(gr):
    server = gr.Server("srv", "postgres://x/db", 39412, cross_rerank=True)
    assert server.cross_rerank is True
    default = gr.Server("srv", "postgres://x/db", 39412)
    assert default.cross_rerank is False


def test_server_env_pins_requested_config_and_worker_clears_inherited_reranker(gr, monkeypatch):
    monkeypatch.setenv("MEMPHANT_CROSS_RERANK", "1")
    monkeypatch.setenv("MEMPHANT_WORKER_ONCE", "1")
    server = gr.Server(
        "srv", "postgres://x/db", 39412, cross_rerank=True,
        rerank_candidate_limit=16, rerank_max_length=128, rerank_batch_size=8,
    )
    env = server.environment()
    assert env["MEMPHANT_CROSS_RERANK"] == "1"
    assert env["MEMPHANT_RERANKER"] == "fastembed"
    assert env["MEMPHANT_RERANK_CANDIDATE_LIMIT"] == "16"
    assert env["MEMPHANT_RERANK_MAX_LENGTH"] == "128"
    assert env["MEMPHANT_RERANK_BATCH_SIZE"] == "8"
    assert env["MEMPHANT_CROSS_RERANK_CANDIDATES"] == "fused-head"
    assert "cross_rerank" not in inspect.signature(gr.drain_worker).parameters
    assert "MEMPHANT_CROSS_RERANK" not in gr.Server("srv", "db", 1).environment()

    worker_envs = []

    psql_commands = []

    def fake_sh(command, **kwargs):
        if command[0] == "psql":
            # The independent, bench-credential queue-empty gate.
            psql_commands.append(command)
            return type(
                "Result", (), {"returncode": 0, "stdout": "0|0\n", "stderr": ""}
            )()
        worker_envs.append(kwargs["env"])
        return type(
            "Result", (),
            {"returncode": 0, "stdout": "memphant-worker: drain completed=0\n", "stderr": ""},
        )()

    monkeypatch.setattr(gr, "sh", fake_sh)
    # `assert_worker_queue_empty` is gate_runtime's, so it shells out through
    # gate_runtime's `sh`, not this module's copy.
    monkeypatch.setattr(sys.modules[gr.assert_worker_queue_empty.__module__], "sh", fake_sh)
    assert gr.drain_worker("worker", "db") == 0
    assert len(psql_commands) == 1 and "job_state" in psql_commands[0][-1]
    assert "MEMPHANT_CROSS_RERANK" not in worker_envs[0]
    assert "MEMPHANT_RERANKER" not in worker_envs[0]
    assert worker_envs[0]["MEMPHANT_WORKER_DRAIN"] == "1"
    assert "MEMPHANT_WORKER_ONCE" not in worker_envs[0]
    assert len(worker_envs) == 1


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "completed=4\n",
        "memphant-worker: drain completed=-1\n",
        "memphant-worker: drain completed=4 extra\n",
        "memphant-worker: drain completed=4\nmemphant-worker: drain completed=0\n",
    ],
)
def test_worker_drain_fails_closed_on_malformed_or_missing_completion(gr, monkeypatch, stdout):
    monkeypatch.setattr(
        gr,
        "sh",
        lambda command, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""}
        )(),
    )
    with pytest.raises(RuntimeError, match="drain completion"):
        gr.drain_worker("worker", "db")


def test_worker_drain_accepts_the_tick_honesty_line_and_raises_on_failed_jobs(gr, monkeypatch, tmp_path):
    """This runner keeps a SECOND copy of the drain parser (gate_runtime.py has
    the other). The bare-form-only regex here silently discarded a completed
    53-minute 4920-section docs ingest that had already reported
    `completed=4920 failed=0 retried=0 deferred=0`. A duplicated parser is a
    duplicated outage, so both copies carry this contract."""
    def fake_sh(command, **kwargs):
        if command and command[0] == "psql":
            return type("Result", (), {"returncode": 0, "stdout": "0|0\n", "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": fake_sh.stdout, "stderr": ""})()

    monkeypatch.setattr(gr, "sh", fake_sh)
    monkeypatch.setattr(sys.modules[gr.assert_worker_queue_empty.__module__], "sh", fake_sh)

    fake_sh.stdout = "memphant-worker: drain completed=4920 failed=0 retried=0 deferred=0\n"
    assert gr.drain_worker("worker", "db") == 4920

    fake_sh.stdout = "memphant-worker: drain completed=4918 failed=2 retried=0 deferred=0\n"
    with pytest.raises(RuntimeError, match="2 FAILED jobs"):
        gr.drain_worker("worker", "db")


def test_worker_drain_fails_closed_on_process_failure(gr, monkeypatch):
    monkeypatch.setattr(
        gr,
        "sh",
        lambda command, **kwargs: type(
            "Result", (), {"returncode": 7, "stdout": "", "stderr": "boom"}
        )(),
    )
    with pytest.raises(RuntimeError, match="worker drain failed.*boom"):
        gr.drain_worker("worker", "db")


# --- provenance-header records the cross_rerank flag -----------------------


def test_build_provenance_report_records_cross_rerank_true(gr):
    report = gr.build_provenance_report(
        embed_model="small",
        label="r15-docs-small+xr",
        breadcrumb=False,
        resource_chunks=False,
        cross_rerank=True,
        golden_path=Path("benchmarks/data/syndai_docs_golden.jsonl"),
        database_url="postgres://memphant:memphant@localhost:5432/memphant_scratch_1_2",
        k=10,
        mode="deep",
        budget_tokens=8192,
        haystack_len=42,
        golden_sha="deadbeef",
        provenance_rows=[],
    )
    assert report["cross_rerank"] is True
    # The independently-set levers are still recorded independently.
    assert report["breadcrumb"] is False
    assert report["resource_chunks"] is False


def test_build_provenance_report_defaults_cross_rerank_false(gr):
    report = gr.build_provenance_report(
        embed_model="small",
        label="r15-docs-small",
        breadcrumb=False,
        golden_path=Path("benchmarks/data/syndai_docs_golden.jsonl"),
        database_url="postgres://memphant:memphant@localhost:5432/memphant_scratch_1_2",
        k=10,
        mode="deep",
        budget_tokens=8192,
        haystack_len=42,
        golden_sha="deadbeef",
        provenance_rows=[],
    )
    assert report["cross_rerank"] is False


def _trace(*, failure="none"):
    return {
        "id": "00000000-0000-0000-0000-000000000123",
        "cross_rerank_ms": 17,
        "cross_rerank": {
            "provider": "fastembed",
            "model": "BAAI/bge-reranker-base",
            "candidate_limit": 32,
            "candidate_count": 24,
            "max_length": 512,
            "batch_size": 8,
            "input_chars_p50": 900,
            "input_chars_p95": 1800,
            "input_chars_max": 2200,
            "failure": failure,
        },
    }


class _Client:
    def __init__(self, recall_response, trace_response=None):
        self.tenant_id = "00000000-0000-0000-0000-000000000001"
        self.recall_response = recall_response
        self.trace_response = trace_response
        self.get_paths = []

    def post(self, path, payload):
        assert path == "/v1/recall"
        return self.recall_response

    def get(self, path):
        self.get_paths.append(path)
        return self.trace_response


CTX = {
    "subject_id": "11111111-1111-4111-8111-111111111111",
    "scope_id": "22222222-2222-4222-8222-222222222222",
    "actor_id": "33333333-3333-4333-8333-333333333333",
    "agent_node_id": "44444444-4444-4444-8444-444444444444",
    "subject_generation": 0,
}


def test_recall_fetches_trace_and_returns_exact_reranker_facts(gr, monkeypatch):
    trace = _trace()
    client = _Client(
        {
            "trace_id": trace["id"],
            "items": [{"body": "answer"}],
            "degraded": False,
        },
        trace,
    )

    ticks = iter((1_000_000, 4_100_000, 6_100_000))
    monkeypatch.setattr(gr.time, "perf_counter_ns", lambda: next(ticks))
    bodies, trace_id, facts, post_ms, trace_read_ms, recall_e2e_ms = gr.recall(
        client, CTX, "question", 10, 8192, "deep", cross_rerank=True,
        expected_rerank_config={
            "candidate_limit": 32, "max_length": 512, "batch_size": 8,
        },
    )

    assert bodies == ["answer"]
    assert trace_id == trace["id"]
    assert client.get_paths == [
        f"/v1/traces/{trace['id']}?subject_id={CTX['subject_id']}"
        f"&scope_id={CTX['scope_id']}&actor_id={CTX['actor_id']}"
        f"&agent_node_id={CTX['agent_node_id']}&subject_generation=0"
    ]
    assert facts == {**trace["cross_rerank"], "cross_rerank_ms": 17}
    assert post_ms == 4
    assert trace_read_ms == 2
    assert recall_e2e_ms == 6


def test_voyage_trace_has_no_fake_batch_or_truncation_knob(gr):
    trace = _trace()
    trace["cross_rerank"].update({
        "provider": "voyage",
        "model": "rerank-2.5",
        "max_length": 32_000,
        "batch_size": None,
    })
    facts = gr._cross_rerank_facts(
        trace,
        True,
        {
            "provider": "voyage",
            "model": "rerank-2.5",
            "candidate_limit": 32,
            "max_length": 32_000,
            "batch_size": None,
        },
    )
    assert facts["batch_size"] is None
    assert facts["max_length"] == 32_000


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda response, trace: response.pop("trace_id"), "trace_id"),
        (lambda response, trace: trace.pop("cross_rerank"), "cross_rerank"),
        (lambda response, trace: trace["cross_rerank"].pop("model"), "model"),
        (
            lambda response, trace: trace["cross_rerank"].__setitem__("candidate_count", "24"),
            "candidate_count",
        ),
        (
            lambda response, trace: trace["cross_rerank"].__setitem__("input_chars_p95", 3000),
            "input_chars",
        ),
    ],
)
def test_cross_rerank_recall_fails_closed_on_missing_or_malformed_trace_facts(
    gr, mutate, match
):
    trace = _trace()
    response = {"trace_id": trace["id"], "items": [], "degraded": False}
    mutate(response, trace)
    client = _Client(response, trace)

    with pytest.raises(RuntimeError, match=match):
        gr.recall(
            client, CTX, "question", 10, 8192, "deep", cross_rerank=True,
            expected_rerank_config={
                "candidate_limit": 32, "max_length": 512, "batch_size": 8,
            },
        )


@pytest.mark.parametrize("failure", ["error", "empty", "invalid_score_count", "non_finite_score"])
def test_cross_rerank_recall_fails_closed_on_reranker_failure(gr, failure):
    trace = _trace(failure=failure)
    client = _Client(
        {"trace_id": trace["id"], "items": [], "degraded": False}, trace
    )

    with pytest.raises(RuntimeError, match="reranker failure"):
        gr.recall(
            client, CTX, "question", 10, 8192, "deep", cross_rerank=True,
            expected_rerank_config={
                "candidate_limit": 32, "max_length": 512, "batch_size": 8,
            },
        )


def test_recall_fails_closed_when_response_is_degraded(gr):
    trace = _trace()
    client = _Client(
        {"trace_id": trace["id"], "items": [], "degraded": True}, trace
    )

    with pytest.raises(RuntimeError, match="degraded"):
        gr.recall(
            client, CTX, "question", 10, 8192, "deep", cross_rerank=True,
            expected_rerank_config={
                "candidate_limit": 32, "max_length": 512, "batch_size": 8,
            },
        )


def test_rerank_off_allows_absent_optional_facts_but_still_requires_trace(gr):
    trace_id = "00000000-0000-0000-0000-000000000124"
    client = _Client(
        {"trace_id": trace_id, "items": [{"body": "answer"}], "degraded": False},
        {"id": trace_id, "cross_rerank_ms": 0, "cross_rerank": None},
    )

    bodies, actual_trace_id, facts, post_ms, trace_read_ms, recall_e2e_ms = gr.recall(
        client, CTX, "question", 10, 8192, "deep", cross_rerank=False
    )

    assert bodies == ["answer"]
    assert actual_trace_id == trace_id
    assert facts is None
    assert isinstance(post_ms, int) and post_ms >= 0
    assert isinstance(trace_read_ms, int) and trace_read_ms >= 0
    assert isinstance(recall_e2e_ms, int) and recall_e2e_ms >= 0


def test_cross_rerank_rejects_zero_candidates_and_requested_config_mismatch(gr):
    for mutate, match in [
        (lambda facts: facts.__setitem__("candidate_count", 0), "candidate_count"),
        (lambda facts: facts.__setitem__("candidate_limit", 64), "requested config"),
    ]:
        trace = _trace()
        mutate(trace["cross_rerank"])
        client = _Client(
            {"trace_id": trace["id"], "items": [], "degraded": False}, trace
        )
        with pytest.raises(RuntimeError, match=match):
            gr.recall(
                client, CTX, "question", 10, 8192, "deep", cross_rerank=True,
                expected_rerank_config={
                    "candidate_limit": 32, "max_length": 512, "batch_size": 8,
                },
            )


def test_provenance_report_aggregates_reranker_facts_and_fingerprints_config(gr):
    static = {
        "provider": "fastembed",
        "model": "fastembed:bge-reranker-base",
        "candidate_limit": 32,
        "max_length": 512,
        "batch_size": 8,
    }
    rows = [
        {
            **static,
            "degraded": False,
            "fallback": False,
            "skipped": False,
            "failure": "none",
            "hit_at_5": True,
            "hit_at_10": True,
            "cross_rerank_ms": 10,
            "recall_e2e_ms": 1200,
            "recall_post_ms": 900,
            "trace_read_ms": 300,
            "candidate_oracle_hit_at_64": True,
            "input_chars_p50": 800,
            "input_chars_p95": 1600,
            "input_chars_max": 2000,
        },
        {
            **static,
            "degraded": False,
            "fallback": False,
            "skipped": False,
            "failure": "none",
            "hit_at_5": False,
            "hit_at_10": True,
            "cross_rerank_ms": 30,
            "recall_e2e_ms": 1400,
            "recall_post_ms": 1000,
            "trace_read_ms": 400,
            "candidate_oracle_hit_at_64": False,
            "input_chars_p50": 1200,
            "input_chars_p95": 2400,
            "input_chars_max": 3000,
        },
    ]
    kwargs = dict(
        embed_model="small",
        label="xr",
        breadcrumb=False,
        resource_chunks=False,
        cross_rerank=True,
        golden_path=Path("benchmarks/data/syndai_docs_golden.jsonl"),
        database_url="postgres://memphant:memphant@localhost:5432/scratch",
        k=10,
        mode="deep",
        budget_tokens=8192,
        haystack_len=4870,
        golden_sha="deadbeef",
        corpus_revision_id="sha256:corpus",
        expected_n=2,
        requested_rerank_config={
            **static,
        },
        provenance_rows=rows,
    )

    report = gr.build_provenance_report(**kwargs)
    repeated = gr.build_provenance_report(**kwargs)

    assert report["runtime_config"] == {
        "runtime": "memphant-server resource ingest + /v1/recall",
        "embed_model": "small",
        # In the fingerprint on purpose: two docs-lane arms that differ ONLY in
        # the lexical scorer must not share a runtime_config_fingerprint, or the
        # artifacts cannot be told apart after the fact.
        "lexical_scorer": "bm25-code",
        "breadcrumb": False,
        "resource_chunks": False,
        "cross_rerank": True,
        "cross_rerank_candidates": "fused-head",
        "cross_reranker": static,
        "requested_cross_reranker": {
            **static,
        },
        "k": 10,
        "recall_mode": "deep",
            "budget_tokens": 8192,
            "retrieval_budget_tokens": 1_000_000,
            "evidence_packer": gr.gc.EVIDENCE_PACKER_CONFIG,
        "haystack_sections": 4870,
        "golden_revision": "sha256:deadbeef",
        "corpus_revision": "sha256:corpus",
    }
    assert len(report["runtime_config_fingerprint"]) == 64
    assert report["runtime_config_fingerprint"] == repeated["runtime_config_fingerprint"]
    assert report["cross_rerank_ms_p50"] == 20
    assert report["cross_rerank_ms_p95"] == 29
    assert report["recall_e2e_ms_p50"] == 1300
    assert report["recall_e2e_ms_p95"] == 1390
    assert report["recall_post_ms_p95"] == 995
    assert report["trace_read_ms_p95"] == 395
    assert report["candidate_oracle_recall_at_64"] == 0.5
    assert report["recall_e2e_p95_ceiling_ms"] == 1500
    assert report["recall_e2e_p95_within_ceiling"] is True
    assert report["input_chars_p50"] == 1000
    assert report["input_chars_p95"] == 2360
    assert report["input_chars_max"] == 3000
    assert report["fallback_count"] == 0
    assert report["degraded_count"] == 0
    assert report["skipped_count"] == 0
    assert report["reranker_failure_count"] == 0


def test_provenance_report_rejects_inconsistent_static_reranker_config(gr):
    rows = [
        {
            "hit_at_5": True,
            "hit_at_10": True,
            "degraded": False,
            "fallback": False,
            "skipped": False,
            "failure": "none",
            "provider": "fastembed",
            "model": "model-a",
            "candidate_limit": 32,
            "max_length": 512,
            "batch_size": 8,
            "recall_e2e_ms": 100,
        },
        {
            "hit_at_5": True,
            "hit_at_10": True,
            "degraded": False,
            "fallback": False,
            "skipped": False,
            "failure": "none",
            "provider": "fastembed",
            "model": "model-b",
            "candidate_limit": 32,
            "max_length": 512,
            "batch_size": 8,
            "recall_e2e_ms": 100,
        },
    ]

    with pytest.raises(RuntimeError, match="inconsistent static config"):
        gr.build_provenance_report(
            embed_model="small",
            label="xr",
            breadcrumb=False,
            cross_rerank=True,
            golden_path=Path("golden.jsonl"),
            database_url="postgres://x/scratch",
            k=10,
            mode="deep",
            budget_tokens=8192,
            haystack_len=4870,
            golden_sha="deadbeef",
            corpus_revision_id="sha256:corpus",
            expected_n=2,
            provenance_rows=rows,
        )


def test_corpus_revision_is_deterministic_and_content_sensitive(gr):
    one = gr.gc.Section("docs/a.md", "root", ["A"], 0, 6, "# A\none")
    changed = gr.gc.Section("docs/a.md", "root", ["A"], 0, 6, "# A\ntwo")

    assert gr.corpus_revision([one]) == gr.corpus_revision([one])
    assert gr.corpus_revision([one]) != gr.corpus_revision([changed])


def test_provenance_report_rejects_nonzero_or_missing_gate_health_facts(gr):
    with pytest.raises(RuntimeError, match="degraded, fallback, skipped, or failed"):
        gr.build_provenance_report(
            embed_model="small",
            label="control",
            breadcrumb=False,
            golden_path=Path("golden.jsonl"),
            database_url="postgres://x/scratch",
            k=10,
            mode="deep",
            budget_tokens=8192,
            haystack_len=1,
            golden_sha="deadbeef",
            corpus_revision_id="sha256:corpus",
            provenance_rows=[
                {"hit_at_5": True, "hit_at_10": True, "recall_e2e_ms": 1}
            ],
            expected_n=1,
        )


@pytest.mark.parametrize("value", [None, -1, 1.5, "10", True])
def test_provenance_report_requires_complete_nonnegative_e2e_latency(gr, value):
    row = {
        "hit_at_5": True,
        "hit_at_10": True,
        "degraded": False,
        "fallback": False,
        "skipped": False,
    }
    if value is not None:
        row["recall_e2e_ms"] = value
    with pytest.raises(RuntimeError, match="recall_e2e_ms"):
        gr.build_provenance_report(
            embed_model="small",
            label="control",
            breadcrumb=False,
            golden_path=Path("golden.jsonl"),
            database_url="postgres://x/scratch",
            k=10,
            mode="deep",
            budget_tokens=8192,
            haystack_len=4870,
            golden_sha="deadbeef",
            corpus_revision_id="sha256:corpus",
            provenance_rows=[row],
            expected_n=1,
        )


@pytest.mark.parametrize("count", [None, True, 0, 1])
def test_authoritative_golden_lock_count_is_required_and_must_match(gr, count):
    lock = {"sha256": "deadbeef"}
    if count is not None:
        lock["count"] = count
    with pytest.raises(RuntimeError, match="lock count"):
        gr.validate_golden_lock([{"question_id": "q1"}, {"question_id": "q2"}], lock, "deadbeef")


def test_negative_recall_wires_public_bitemporal_query_fields_without_labels(gr):
    class CaptureClient:
        tenant_id = "tenant"

        def __init__(self):
            self.payload = None

        def post(self, path, payload):
            assert path == "/v1/recall"
            self.payload = payload
            return {"degraded": False, "trace_id": "trace-1", "items": []}

        def get(self, path):
            assert path.startswith("/v1/traces/trace-1?")
            return {"id": "trace-1"}

    client = CaptureClient()
    gr.recall(
        client,
        CTX,
        "question only",
        10,
        100,
        "fast",
        transaction_as_of="2026-01-01T00:00:00Z",
        valid_at="2025-01-01T00:00:00Z",
    )

    assert client.payload["transaction_as_of"] == "2026-01-01T00:00:00Z"
    assert client.payload["valid_at"] == "2025-01-01T00:00:00Z"
    assert not ({"gold", "case_kind", "forbidden", "expect"} & set(client.payload))


def test_negative_slice_requires_its_own_evidence_output(gr):
    parser = gr.build_arg_parser()
    args = parser.parse_args(
        [
            "--out-evidence", "positive.jsonl",
            "--out-provenance", "report.json",
            "--negative-slice", "negative.jsonl",
            "--out-negative-evidence", "negative-evidence.jsonl",
        ]
    )
    assert args.negative_slice == "negative.jsonl"
    assert args.out_negative_evidence == "negative-evidence.jsonl"


def test_post_snapshot_ingest_never_accepts_caller_supplied_transaction_time(gr):
    assert "recorded_at" not in inspect.getsource(gr.ingest_negative_document)


def test_generic_public_scope_cases_are_explicit_adapter_mappings(gr):
    assert gr.NEGATIVE_SCOPE_ADAPTER_MAPPING == {
        "wrong_tenant": "tenant_id",
        "wrong_user": "scope_id adapter mapping (not a native user dimension)",
        "wrong_project": "scope_id adapter mapping (not a native project dimension)",
        "wrong_agent": "scope_id adapter mapping (not a native agent dimension)",
    }


def test_stale_fixture_uses_public_direct_unit_valid_interval(gr):
    class CaptureClient:
        tenant_id = "tenant"

        def post(self, path, payload):
            self.path = path
            self.payload = payload
            return {"unit_ids": ["unit-1"]}

    client = CaptureClient()
    gr.ingest_negative_document(
        client,
        CTX,
        {
            "document_id": "stale",
            "body": "old canary",
            "scope": "active",
            "valid_from": "2025-01-01T00:00:00Z",
            "valid_to": "2025-06-01T00:00:00Z",
        },
    )

    assert client.path == "/v1/episodes"
    assert "resource" not in client.payload["payload"]
    assert client.payload["payload"]["unit"]["valid_from"] == "2025-01-01T00:00:00Z"
    assert client.payload["payload"]["unit"]["valid_to"] == "2025-06-01T00:00:00Z"
