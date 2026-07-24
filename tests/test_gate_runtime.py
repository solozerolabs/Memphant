"""Contract tests for the shared gate-runner runtime module
(``scripts/gate_runtime.py``), centered on the API-arm key map: the map is
the SINGLE source of truth for every gate runner (per-script copies drifted
once — voyage-4-large landed in ``gate_run_memphant.py``'s copy but not
``code_lane_run_memphant.py``'s, silently disabling that arm's fail-fast),
so these tests pin (1) the map's keys against the API-arm ids accepted by
the Rust ``embedder_from_id`` grammar — both as an explicit constant and by
scraping the grammar's match arms out of the Rust source, so the NEXT arm
added to the grammar fails a test here until the map learns it — and
(2) that both runner scripts genuinely share the one map object rather than
carrying copies. Also pins ``reexec_through_scratch_db`` — the shared scratch-DB
isolation linchpin both runners call — since a broken recursion guard or a
reordered helper invocation would silently un-isolate every bench run (or
infinite-loop) with nothing else catching it. No DB, no network, no server
process.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUST_GRAMMAR = ROOT / "crates" / "memphant-runtime" / "src" / "lib.rs"

# The API-arm ids accepted by `embedder_from_id`
# (crates/memphant-runtime/src/lib.rs) — the `"<id>" => api(...)` match arms.
# Local arms (small/base/modernbert/gemma/qwen3) and off/noop need no key and
# are deliberately NOT in the key map.
EXPECTED_API_ARMS = {
    "voyage-4",
    "voyage-4-lite",
    "voyage-4-large",
    "voyage-code-3",
    "voyage-context-4",
    "gemini-embedding-001",
    "gemini-embedding-2",
    "openai-text-embedding-3-small",
    "jina-v5-small",
}

# `"<id>" => api(` — the exact shape of an API-arm match arm in
# embedder_from_id (local arms route through `local(...)`/`Ok(...)` instead).
RUST_API_ARM_RE = re.compile(r'"([a-z0-9.-]+)"\s*=>\s*api\(')


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def grt():
    return _load("gate_runtime", "scripts/gate_runtime.py")


def test_api_key_map_covers_exactly_the_expected_api_arms(grt):
    assert set(grt.API_KEY_ENV_BY_ARM) == EXPECTED_API_ARMS


def test_episode_retain_builder_is_context_bound_and_fail_closed(grt):
    context = {
        "subject_id": "subject",
        "scope_id": "scope",
        "actor_id": "actor",
        "agent_node_id": "agent",
        "subject_generation": 3,
    }
    assert grt.episode_retain_payload(
        context,
        source_ref="bench:episode:1",
        observed_at="2026-07-23T00:00:00Z",
        source_kind="tool",
        body="tool outcome",
    ) == {
        **context,
        "source_ref": "bench:episode:1",
        "observed_at": "2026-07-23T00:00:00Z",
        "payload": {"episode": {"source_kind": "tool", "body": "tool outcome"}},
    }
    with pytest.raises(ValueError, match="unmapped episode source kind"):
        grt.episode_retain_payload(
            context,
            source_ref="bench:episode:2",
            observed_at="2026-07-23T00:00:00Z",
            source_kind="tool_attempt.success",
            body="must map first",
        )


def test_replay_key_provisioning_reuses_existing_tenant_without_storing_key(
    grt, monkeypatch,
):
    commands = []

    class Result:
        returncode = 0
        stdout = "key_created id=id tenant=tenant max_trust=trusted_system\nmk_secret\n"
        stderr = ""

    monkeypatch.setattr(
        grt, "sh", lambda command: commands.append(command) or Result()
    )

    assert grt.provision_api_key("cli", "postgres://db", "tenant") == "mk_secret"
    assert commands == [[
        "cli", "admin", "create-key", "--tenant", "tenant",
        "--max-trust", "trusted_system", "--database-url", "postgres://db",
    ]]


def test_expected_api_arms_match_the_rust_grammar_source():
    """Scrapes the `\"<id>\" => api(...)` match arms out of embedder_from_id's
    Rust source, so adding an API arm to the grammar without extending
    API_KEY_ENV_BY_ARM (or this pin) fails here instead of silently
    no-op'ing the runners' fail-fast key check."""
    source = RUST_GRAMMAR.read_text(encoding="utf-8")
    scraped = set(RUST_API_ARM_RE.findall(source))
    assert scraped == EXPECTED_API_ARMS, (
        "Rust embedder_from_id API arms drifted from the pinned set — "
        "update EXPECTED_API_ARMS and gate_runtime.API_KEY_ENV_BY_ARM together"
    )


def test_every_arm_maps_to_a_provider_key_env_var(grt):
    for arm, var in grt.API_KEY_ENV_BY_ARM.items():
        assert var.endswith("_API_KEY"), f"{arm}: unexpected env var name {var!r}"


def test_both_runner_scripts_share_the_one_map_object():
    """The centralization pin: gate_run_memphant.py and
    code_lane_run_memphant.py must expose the SAME dict object imported from
    gate_runtime — not per-script copies (which is exactly how the
    voyage-4-large drift happened)."""
    docs_runner = _load("gate_run_memphant", "scripts/gate_run_memphant.py")
    code_runner = _load("code_lane_run_memphant", "scripts/code_lane_run_memphant.py")
    assert docs_runner.API_KEY_ENV_BY_ARM is code_runner.gr.API_KEY_ENV_BY_ARM


def test_check_embed_model_key_noop_for_none_and_local_arms(grt, monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    grt.check_embed_model_key(None)  # must not raise
    grt.check_embed_model_key("small")  # local arm, must not raise
    grt.check_embed_model_key("qwen3")  # local arm, must not raise


@pytest.mark.parametrize(
    "arm,var",
    [
        ("voyage-4-large", "VOYAGE_API_KEY"),
        ("gemini-embedding-001", "GEMINI_API_KEY"),
        ("gemini-embedding-2", "GEMINI_API_KEY"),
        ("jina-v5-small", "JINA_API_KEY"),
        ("openai-text-embedding-3-small", "OPENAI_API_KEY"),
    ],
)
def test_check_embed_model_key_fails_fast_when_key_missing(grt, monkeypatch, arm, var):
    monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match=re.escape(f"--embed-model {arm}: {var} is not set")):
        grt.check_embed_model_key(arm)


def test_check_embed_model_key_passes_when_key_present(grt, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    grt.check_embed_model_key("voyage-4-large")  # must not raise


def test_check_embed_model_key_rejects_whitespace_only_key(grt, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY is not set"):
        grt.check_embed_model_key("voyage-4")


def test_shared_api_client_supports_authenticated_trace_get(grt):
    calls = []

    class Response:
        status = 200

        def read(self):
            return b'{"id":"trace-1"}'

    class Connection:
        def request(self, method, path, body=None, headers=None):
            calls.append((method, path, body, headers))

        def getresponse(self):
            return Response()

    client = object.__new__(grt.ApiClient)
    client.conn = Connection()
    client.headers = {"Authorization": "Bearer mk_test"}
    client.port = 3000

    assert client.get("/v1/traces/trace-1") == {"id": "trace-1"}
    assert calls == [
        ("GET", "/v1/traces/trace-1", None, {"Authorization": "Bearer mk_test"})
    ]


def test_reexec_through_scratch_db_is_noop_when_already_active(grt, monkeypatch):
    """The recursion guard: inside a scratch DB (``MEMPHANT_SCRATCH_ACTIVE``
    set), the runner must NOT re-exec again — else `with_scratch_db.sh` nests
    forever, minting a DB per level."""
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setattr(grt.os, "execvp", lambda *a: pytest.fail(f"re-exec'd despite guard: {a}"))
    grt.reexec_through_scratch_db("postgres://memphant:memphant@localhost:5432/memphant")


def test_reexec_through_scratch_db_execs_through_helper(grt, monkeypatch):
    """First entry re-execs the CURRENT argv through with_scratch_db.sh with
    ENV_VAR=DATABASE_URL and the guard set, so the child mints/migrates/drops a
    fresh DB and the runner continues against it. Pins the exact argv order the
    helper's ``<base_url> <ENV_VAR> <cmd...>`` contract requires."""
    monkeypatch.delenv("MEMPHANT_SCRATCH_ACTIVE", raising=False)
    monkeypatch.setattr(grt.sys, "argv", ["scripts/gate_run_memphant.py", "--label", "x"])
    captured = {}

    def fake_execvp(file, argv):
        captured["file"], captured["argv"] = file, argv
        raise _Execed  # os.execvp never returns; stop the function here

    monkeypatch.setattr(grt.os, "execvp", fake_execvp)
    with pytest.raises(_Execed):
        grt.reexec_through_scratch_db("postgres://memphant:memphant@localhost:5432/memphant")

    assert captured["file"] == "bash"
    argv = captured["argv"]
    assert argv[0] == "bash"
    assert argv[1] == str(ROOT / "scripts" / "with_scratch_db.sh")
    assert argv[2] == "postgres://memphant:memphant@localhost:5432/memphant"  # base url
    assert argv[3] == "DATABASE_URL"  # env var the scratch url lands in
    assert argv[4] == grt.sys.executable
    assert argv[5:] == ["scripts/gate_run_memphant.py", "--label", "x"]  # current argv, verbatim
    # Guard set BEFORE exec so the re-exec'd child sees it and doesn't recurse.
    assert grt.os.environ["MEMPHANT_SCRATCH_ACTIVE"] == "1"


class _Execed(Exception):
    """Sentinel: stands in for os.execvp replacing the process (never returns)."""


def test_structured_extractor_summary_prices_and_pairs_every_attempt(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "openai/gpt-5.6-luna-pro"
    rows = [
        {"schema_version": 1, "event": "started", "attempt_id": "a", "requested_model": model},
        {
            "schema_version": 1, "event": "result", "attempt_id": "a",
            "requested_model": model, "http_status": 200, "response_id": "gen-0",
            "served_model": model, "provider": "OpenAI",
            "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9, "cost": 0.002},
        },
        {
            "schema_version": 1, "event": "decode", "attempt_id": "a",
            "requested_model": model, "accepted_op_count": 1,
            "rejected_op_count": 0, "rejection_reasons": {},
        },
        {"schema_version": 1, "event": "started", "attempt_id": "b", "requested_model": model},
        {
            "schema_version": 1, "event": "result", "attempt_id": "b",
            "requested_model": model, "http_status": 200, "response_id": "gen-1",
            "served_model": model, "provider": "OpenAI",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": 0.003},
        },
        {
            "schema_version": 1, "event": "decode", "attempt_id": "b",
            "requested_model": model, "accepted_op_count": 2,
            "rejected_op_count": 1, "rejection_reasons": {"quantity_shape": 1},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    summary = grt.structured_extractor_attempt_summary(path, model)

    assert summary["provider_attempts"] == 2
    assert summary["completed_attempts"] == 2
    assert summary["successful_responses"] == 1
    assert summary["decode_outcomes"] == 2
    assert summary["decode_errors"] == 0
    assert summary["accepted_operations"] == 3
    assert summary["rejected_operations"] == 1
    assert summary["rejection_reasons"] == {"quantity_shape": 1}
    assert summary["priced_responses"] == 2
    assert summary["reported_cost_usd"] == 0.005
    assert summary["providers"] == ["OpenAI"]
    assert summary["cost_status"] == "all_provider_attempts_priced"
    assert len(summary["ledger_sha256"]) == 64


def test_structured_extractor_summary_fails_closed_on_unpriced_response(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "openai/gpt-5.6-luna-pro"
    path.write_text(
        json.dumps({"schema_version": 1, "event": "started", "attempt_id": "a", "requested_model": model}) + "\n"
        + json.dumps({
            "schema_version": 1, "event": "result", "attempt_id": "a",
            "requested_model": model, "http_status": 200, "response_id": "gen-1",
            "served_model": model, "provider": "OpenAI", "usage": {
                "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2
            },
        }) + "\n"
        + json.dumps({
            "schema_version": 1, "event": "decode", "attempt_id": "a",
            "requested_model": model, "accepted_op_count": 0,
            "rejected_op_count": 0, "rejection_reasons": {},
        }) + "\n"
    )
    with pytest.raises(RuntimeError, match="cost is missing"):
        grt.structured_extractor_attempt_summary(path, model)


@pytest.mark.parametrize(
    ("usage", "message"),
    [
        ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0}, "token usage is malformed"),
        ({"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1, "cost": 0.001}, "token usage is malformed"),
        ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3, "cost": 0.001}, "token usage is inconsistent"),
        ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0}, "cost is missing"),
    ],
)
def test_structured_extractor_summary_rejects_non_fresh_paid_response(
    grt, tmp_path, usage, message
):
    path = tmp_path / "extractor.jsonl"
    model = "google/gemini-3.5-flash"
    rows = [
        {"schema_version": 1, "event": "started", "attempt_id": "a", "requested_model": model},
        {
            "schema_version": 1, "event": "result", "attempt_id": "a",
            "requested_model": model, "http_status": 200, "response_id": "gen-zero",
            "served_model": model, "provider": "Google",
            "usage": usage,
        },
        {
            "schema_version": 1, "event": "decode", "attempt_id": "a",
            "requested_model": model, "accepted_op_count": 0,
            "rejected_op_count": 0, "rejection_reasons": {},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(RuntimeError, match=message):
        grt.structured_extractor_attempt_summary(path, model)


def test_structured_extractor_summary_fails_closed_without_decode_outcome(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "openai/gpt-5.6-luna-pro"
    rows = [
        {"schema_version": 1, "event": "started", "attempt_id": "a", "requested_model": model},
        {
            "schema_version": 1, "event": "result", "attempt_id": "a",
            "requested_model": model, "http_status": 200, "response_id": "gen-1",
            "served_model": model, "provider": "OpenAI",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.001},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(RuntimeError, match="decode outcome is missing"):
        grt.structured_extractor_attempt_summary(path, model)


def test_structured_extractor_summary_fails_closed_on_interrupted_attempt(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "openai/gpt-5.6-luna-pro"
    path.write_text(json.dumps({
        "schema_version": 1, "event": "started", "attempt_id": "a",
        "requested_model": model,
    }) + "\n")

    with pytest.raises(RuntimeError, match="1 interrupted attempts"):
        grt.structured_extractor_attempt_summary(path, model)


def test_structured_extractor_summary_admits_recovered_no_content_retry(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "google/gemini-3.5-flash"
    episode = "00000000-0000-0000-0000-000000000001"
    common = {"schema_version": 1, "episode_id": episode, "max_attempts": 3,
              "requested_model": model}
    rows = [
        common | {"event": "started", "attempt_id": "a", "attempt": 1},
        common | {"event": "result", "attempt_id": "a", "attempt": 1,
                  "http_status": 200, "response_id": "gen-empty",
                  "served_model": model, "provider": "Google AI Studio",
                  "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                            "total_tokens": 0, "cost": 0}},
        common | {"event": "decode", "attempt_id": "a", "attempt": 1,
                  "error": "response_decode_error", "accepted_op_count": 0,
                  "rejected_op_count": 0, "rejection_reasons": {}},
        common | {"event": "started", "attempt_id": "b", "attempt": 2},
        common | {"event": "result", "attempt_id": "b", "attempt": 2,
                  "http_status": 200, "response_id": "gen-ok",
                  "served_model": model, "provider": "Google AI Studio",
                  "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                            "total_tokens": 12, "cost": 0.01}},
        common | {"event": "decode", "attempt_id": "b", "attempt": 2,
                  "accepted_op_count": 1, "rejected_op_count": 0,
                  "rejection_reasons": {}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    summary = grt.structured_extractor_attempt_summary(
        path, model, require_episode_coverage=True
    )
    assert summary["episodes"] == 1
    assert summary["successful_episodes"] == 1
    assert summary["retried_episodes"] == 1
    assert summary["provider_attempts"] == 2
    assert summary["successful_responses"] == 1
    assert summary["transient_no_content_attempts"] == 1
    assert summary["successful_decodes"] == 1
    assert summary["cost_status"] == "reported_cost_is_lower_bound"
    assert summary["reported_cost_usd"] == 0.01


def test_structured_extractor_summary_admits_schema_v2_recovered_http_retry(
    grt, tmp_path
):
    path = tmp_path / "extractor.jsonl"
    model = "deepseek/deepseek-v4-flash"
    common = {
        "schema_version": 2,
        "episode_id": "episode-1",
        "max_attempts": 3,
        "requested_model": model,
        "request_sha256": "1" * 64,
    }
    first = common | {"attempt_id": "a", "attempt": 1, "retry_index": 0}
    second = common | {"attempt_id": "b", "attempt": 2, "retry_index": 1}
    rows = [
        first | {"event": "started"},
        first | {
            "event": "result", "http_status": 429, "error": "http_error",
            "elapsed_seconds": 0.1, "parse_status": "http_error",
        },
        second | {"event": "started"},
        second | {
            "event": "result", "http_status": 200, "response_id": "gen-ok",
            "served_model": model, "provider": "DeepInfra",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                      "total_tokens": 12, "cost": 0.01},
            "elapsed_seconds": 1.0, "parse_status": "generation_stats_reconciled",
            "result_sha256": "2" * 64,
        },
        second | {
            "event": "decode", "accepted_op_count": 1,
            "rejected_op_count": 0, "rejection_reasons": {},
            "elapsed_seconds": 1.1, "parse_status": "decoded",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    summary = grt.structured_extractor_attempt_summary(
        path, model, require_episode_coverage=True
    )

    assert summary["provider_attempts"] == 2
    assert summary["transient_http_attempts"] == 1
    assert summary["retried_episodes"] == 1
    assert summary["successful_episodes"] == 1
    assert summary["reported_cost_usd"] == 0.01


def test_structured_extractor_summary_admits_recovered_evidence_grounding_retry(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "google/gemini-3.5-flash"
    episode = "00000000-0000-0000-0000-000000000001"
    common = {"schema_version": 1, "episode_id": episode, "max_attempts": 3,
              "requested_model": model}
    rows = [
        common | {"event": "started", "attempt_id": "a", "attempt": 1},
        common | {"event": "result", "attempt_id": "a", "attempt": 1,
                  "http_status": 200, "response_id": "gen-bad-quote",
                  "served_model": model, "provider": "Google AI Studio",
                  "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                            "total_tokens": 12, "cost": 0.01}},
        common | {"event": "decode", "attempt_id": "a", "attempt": 1,
                  "accepted_op_count": 1, "rejected_op_count": 1,
                  "rejection_reasons": {"evidence_grounding": 1}},
        common | {"event": "started", "attempt_id": "b", "attempt": 2},
        common | {"event": "result", "attempt_id": "b", "attempt": 2,
                  "http_status": 200, "response_id": "gen-repaired",
                  "served_model": model, "provider": "Google AI Studio",
                  "usage": {"prompt_tokens": 12, "completion_tokens": 2,
                            "total_tokens": 14, "cost": 0.012}},
        common | {"event": "decode", "attempt_id": "b", "attempt": 2,
                  "accepted_op_count": 2, "rejected_op_count": 0,
                  "rejection_reasons": {}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    summary = grt.structured_extractor_attempt_summary(
        path, model, require_episode_coverage=True
    )
    assert summary["episodes"] == 1
    assert summary["successful_episodes"] == 1
    assert summary["successful_responses"] == 1
    assert summary["successful_decodes"] == 1
    assert summary["semantic_repair_attempts"] == 1
    assert summary["rejected_operations"] == 1
    assert summary["rejection_reasons"] == {"evidence_grounding": 1}
    assert summary["priced_responses"] == 2
    assert summary["reported_cost_usd"] == 0.022


def test_structured_extractor_summary_admits_recovered_duplicate_identity_retry(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "openai/gpt-5.6-luna-pro"
    episode = "00000000-0000-0000-0000-000000000001"
    common = {"schema_version": 1, "episode_id": episode, "max_attempts": 3,
              "requested_model": model}
    rows = [
        common | {"event": "started", "attempt_id": "a", "attempt": 1},
        common | {"event": "result", "attempt_id": "a", "attempt": 1,
                  "http_status": 200, "response_id": "gen-duplicate",
                  "served_model": model, "provider": "OpenAI",
                  "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                            "total_tokens": 12, "cost": 0.01}},
        common | {"event": "decode", "attempt_id": "a", "attempt": 1,
                  "accepted_op_count": 1, "rejected_op_count": 1,
                  "rejection_reasons": {"duplicate_state_identity": 1}},
        common | {"event": "started", "attempt_id": "b", "attempt": 2},
        common | {"event": "result", "attempt_id": "b", "attempt": 2,
                  "http_status": 200, "response_id": "gen-repaired",
                  "served_model": model, "provider": "OpenAI",
                  "usage": {"prompt_tokens": 12, "completion_tokens": 2,
                            "total_tokens": 14, "cost": 0.012}},
        common | {"event": "decode", "attempt_id": "b", "attempt": 2,
                  "accepted_op_count": 2, "rejected_op_count": 0,
                  "rejection_reasons": {}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    summary = grt.structured_extractor_attempt_summary(
        path, model, require_episode_coverage=True
    )
    assert summary["successful_episodes"] == 1
    assert summary["semantic_repair_attempts"] == 1
    assert summary["terminal_rejected_operations"] == 0
    assert summary["rejection_reasons"] == {"duplicate_state_identity": 1}


def test_structured_extractor_summary_fails_closed_on_transport_error(grt, tmp_path):
    path = tmp_path / "extractor.jsonl"
    model = "openai/gpt-5.6-luna-pro"
    rows = [
        {"schema_version": 1, "event": "started", "attempt_id": "a", "requested_model": model},
        {"schema_version": 1, "event": "result", "attempt_id": "a", "requested_model": model, "error": "transport_error"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    summary = grt.structured_extractor_attempt_summary(path, model)
    assert summary["transient_transport_attempts"] == 1
    assert summary["unpriced_attempts"] == 1
    assert summary["cost_status"] == "reported_cost_is_lower_bound"


def test_structured_extractor_summary_classifies_missing_response_id_as_provenance_error(
    grt, tmp_path
):
    path = tmp_path / "extractor.jsonl"
    model = "deepseek/deepseek-v4-flash"
    common = {
        "schema_version": 2,
        "episode_id": "episode-1",
        "attempt": 1,
        "max_attempts": 1,
        "retry_index": 0,
        "requested_model": model,
        "request_sha256": "1" * 64,
        "attempt_id": "a",
    }
    rows = [
        common | {"event": "started"},
        common
        | {
            "event": "result",
            "http_status": 200,
            "error": "generation_stats_lookup_failed",
            "elapsed_seconds": 1.0,
            "parse_status": "generation_stats_lookup_failed",
            "result_sha256": "2" * 64,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    summary = grt.structured_extractor_attempt_summary(path, model)
    assert summary["terminal_provenance_errors"] == 1
    assert summary["unpriced_attempts"] == 1
    assert summary["cost_status"] == "reported_cost_is_lower_bound"


def test_drain_worker_uses_one_binary_drain_without_structured_provider(grt, monkeypatch):
    calls = []
    environments = []

    def fake_sh(command, **kwargs):
        calls.append(command)
        environments.append(kwargs["env"])
        return grt.subprocess.CompletedProcess(
            command, 0, "memphant-worker: drain completed=0\n", ""
        )

    monkeypatch.setattr(grt, "sh", fake_sh)

    assert grt.drain_worker("worker", "postgres://fixture") == 0
    assert calls == [["worker"]]
    assert environments[0]["MEMPHANT_WORKER_DRAIN"] == "1"
    assert environments[0]["MEMPHANT_WORKER_COMPILE_CONCURRENCY"] == "64"
    assert environments[0]["MEMPHANT_WORKER_BATCH_SIZE"] == "256"
    assert environments[0]["MEMPHANT_WORKER_DATABASE_MAX_CONNECTIONS"] == "32"
    assert "MEMPHANT_STRUCTURED_STATE_CONCURRENCY" not in environments[0]
    assert "MEMPHANT_WORKER_ONCE" not in environments[0]


def test_drain_worker_stops_on_zero_execution_before_next_tick(grt, monkeypatch, tmp_path):
    model = "google/gemini-3.5-flash"
    path = tmp_path / "extractor.jsonl"
    common = {"schema_version": 1, "episode_id": "episode-1", "attempt": 1,
              "max_attempts": 3, "requested_model": model}
    rows = [
        common | {"event": "started", "attempt_id": "a"},
        {
            **common, "event": "result", "attempt_id": "a",
            "http_status": 200, "response_id": "gen-zero",
            "served_model": model, "provider": "Google",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0},
        },
        {
            **common, "event": "decode", "attempt_id": "a", "accepted_op_count": 0,
            "rejected_op_count": 0, "rejection_reasons": {},
            "error": "response_decode_error",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    calls = 0

    def fake_sh(command, **_kwargs):
        nonlocal calls
        calls += 1
        return grt.subprocess.CompletedProcess(command, 0, "completed=1\n", "")

    monkeypatch.setattr(grt, "sh", fake_sh)
    with pytest.raises(RuntimeError, match="lacks one final successful decode"):
        grt.drain_worker(
            "worker", "postgres://fixture",
            structured_attempt_ledger=path,
            structured_requested_model=model,
        )
    assert calls == 1


def test_drain_worker_stops_on_semantic_rejection_before_next_tick(grt, monkeypatch, tmp_path):
    model = "google/gemini-3.5-flash"
    path = tmp_path / "extractor.jsonl"
    common = {"schema_version": 1, "episode_id": "episode-1", "attempt": 1,
              "max_attempts": 3, "requested_model": model}
    rows = [
        common | {"event": "started", "attempt_id": "a"},
        {
            **common, "event": "result", "attempt_id": "a",
            "http_status": 200, "response_id": "gen-duplicate",
            "served_model": model, "provider": "Google",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": 0.01},
        },
        {
            **common, "event": "decode", "attempt_id": "a", "accepted_op_count": 1,
            "rejected_op_count": 1,
            "rejection_reasons": {"duplicate_state_identity": 1},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    calls = 0

    def fake_sh(command, **_kwargs):
        nonlocal calls
        calls += 1
        return grt.subprocess.CompletedProcess(command, 0, "completed=1\n", "")

    monkeypatch.setattr(grt, "sh", fake_sh)
    with pytest.raises(RuntimeError, match="duplicate_state_identity"):
        grt.drain_worker(
            "worker", "postgres://fixture",
            structured_attempt_ledger=path,
            structured_requested_model=model,
        )
    assert calls == 1


def test_drain_worker_rejects_zero_tick_with_pending_retry(
    grt, monkeypatch, tmp_path
):
    worker_calls = 0

    def fake_sh(command, **_kwargs):
        nonlocal worker_calls
        if command == ["worker"]:
            worker_calls += 1
            if worker_calls == 1:
                return grt.subprocess.CompletedProcess(
                    command, 0, "completed=1\n", "job fixture failed: exact cause"
                )
            return grt.subprocess.CompletedProcess(
                command, 0, "completed=0\n", "memphant-worker: store=postgres"
            )
        if "json_agg" in command[-1]:
            return grt.subprocess.CompletedProcess(
                command,
                0,
                '[{"target_id":"episode-1","attempts":1,'
                '"last_error":"duplicate structured identity"}]\n',
                "",
            )
        return grt.subprocess.CompletedProcess(command, 0, "1\n", "")

    monkeypatch.setattr(grt, "sh", fake_sh)
    monkeypatch.setattr(
        grt,
        "structured_extractor_attempt_summary",
        lambda *_args, **_kwargs: {
            "terminal_decode_errors": 0,
            "terminal_rejected_operations": 0,
        },
    )
    ledger = tmp_path / "attempts.jsonl"
    ledger.write_text("")

    with pytest.raises(
        RuntimeError,
        match=(
            "1 pending retryable jobs: job fixture failed: exact cause"
            " \\| memphant-worker: store=postgres"
            ".*pending_job_errors=.*duplicate structured identity"
        ),
    ):
        grt.drain_worker(
            "worker",
            "postgres://fixture",
            structured_attempt_ledger=ledger,
            structured_requested_model="fixture/model",
        )


@pytest.mark.parametrize(
    "stdout",
    ["", "completed=4\n", "memphant-worker: drain completed=-1\n"],
)
def test_drain_worker_rejects_malformed_drain_completion(
    grt, monkeypatch, stdout
):
    monkeypatch.setattr(
        grt,
        "sh",
        lambda command, **_kwargs: grt.subprocess.CompletedProcess(
            command, 0, stdout, ""
        ),
    )
    with pytest.raises(RuntimeError, match="drain completion"):
        grt.drain_worker("worker", "postgres://fixture")


def test_portable_runtime_identity_never_records_machine_absolute_paths(grt) -> None:
    argv, command = grt.portable_command(
        [
            "scripts/run_forgeteval.py",
            "--server-bin",
            str(ROOT / "target/release/memphant-server"),
            "--official-dir",
            "/private/tmp/lethe",
        ],
        ROOT,
    )

    assert argv == [
        "scripts/run_forgeteval.py",
        "--server-bin",
        "target/release/memphant-server",
        "--official-dir",
        "<external>/lethe",
    ]
    assert command.startswith("python3 scripts/run_forgeteval.py")
    assert str(ROOT) not in command
    assert "/private/tmp" not in command
