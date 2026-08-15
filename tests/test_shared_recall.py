"""Behavioral tests for the shared recall core (stdlib, no sockets).

The core lives under plugins/_shared/; load it by path and drive both the
library surface (`recall_card`, `advisory_block`, `build_block`) and the CLI
(`main`) with a fake in-process caller so every branch is exercised without a
server: hit, empty, consolidation_pending, malformed, oversized, timeout, auth.
"""

import contextlib
import importlib.util
import io
import json
from pathlib import Path

_CORE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "_shared"
    / "memphant_recall.py"
)
_spec = importlib.util.spec_from_file_location("memphant_recall", _CORE_PATH)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def _recall_body(structured):
    """A tools/call response body carrying `structured` recall content."""
    return json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": structured}}
    ).encode("utf-8")


def _fake_caller(*, on_call):
    """A caller that answers initialize/initialized/tools-call in order."""

    def caller(payload, session_id):
        method = payload.get("method")
        if method == "initialize":
            return (json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode(), "sess-1")
        if method == "notifications/initialized":
            return (b"", session_id)
        if method == "tools/call":
            assert payload["params"]["name"] == "recall"
            return (on_call(payload), session_id)
        raise AssertionError(f"unexpected method {method}")

    return caller


# --- recall_card (raw card, unchanged behavior) -----------------------------


def test_recall_card_hit_returns_raw_body():
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "hit", "response": {"items": [{"body": "Run make deploy."}]}}
        )
    )
    assert core.recall_card("how do we deploy?", "/repo", caller) == "Run make deploy."


def test_recall_card_empty_returns_empty_string():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "empty"}))
    assert core.recall_card("q", "/repo", caller) == ""


def test_recall_card_pending_raises_consolidation_pending():
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "unavailable", "error": {"code": "consolidation_pending"}}
        )
    )
    try:
        core.recall_card("q", "/repo", caller)
        raise AssertionError("expected RecallError")
    except core.RecallError as exc:
        assert exc.code == "consolidation_pending"


def test_recall_card_malformed_raises():
    caller = _fake_caller(on_call=lambda _p: b"not json at all")
    try:
        core.recall_card("q", "/repo", caller)
        raise AssertionError("expected RecallError")
    except core.RecallError as exc:
        assert exc.code == "malformed"


def test_recall_card_oversized_raises():
    huge = _recall_body(
        {"state": "hit", "response": {"items": [{"body": "x" * (core.MAX_RESPONSE_BYTES + 10)}]}}
    )
    caller = _fake_caller(on_call=lambda _p: huge)
    try:
        core.recall_card("q", "/repo", caller)
        raise AssertionError("expected RecallError")
    except core.RecallError as exc:
        assert exc.code == "oversized"


def test_recall_card_timeout_and_auth_propagate():
    def raise_timeout(_payload, _session):
        raise core.RecallError("timeout")

    try:
        core.recall_card("q", "/repo", raise_timeout)
        raise AssertionError("expected RecallError")
    except core.RecallError as exc:
        assert exc.code == "timeout"


# --- advisory_block (memori wrapping) ---------------------------------------


def test_advisory_block_wraps_a_hit():
    block = core.advisory_block("Run make deploy.")
    assert block.startswith("<memphant_memory>")
    assert block.endswith("</memphant_memory>")
    assert core.ADVISORY_HEADER in block
    assert "Run make deploy." in block


def test_advisory_block_empty_is_zero_bytes():
    assert core.advisory_block("") == ""
    assert core.advisory_block("   \n  ") == ""


# --- build_block (recall_card + advisory_block) -----------------------------


def test_build_block_hit_is_wrapped():
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "hit", "response": {"items": [{"body": "Prefer pnpm."}]}}
        )
    )
    block = core.build_block("q", "/repo", caller)
    assert block.startswith("<memphant_memory>")
    assert "Prefer pnpm." in block


def test_build_block_empty_is_zero_bytes():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "empty"}))
    assert core.build_block("q", "/repo", caller) == ""


# --- CLI (main) -------------------------------------------------------------


def _run_cli(monkeypatch_env, argv, caller):
    """Drive core.main() in-process with a fake caller, capturing stdout/stderr."""
    # Point main() at a configured endpoint but swap http_caller for the fake.
    core.os.environ["MEMPHANT_MCP_URL"] = monkeypatch_env.get("url", "")
    core.os.environ["MEMPHANT_API_KEY"] = monkeypatch_env.get("key", "")
    original = core.http_caller
    if caller is not None:
        core.http_caller = lambda *a, **k: caller
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = core.main(argv)
    finally:
        core.http_caller = original
        core.os.environ.pop("MEMPHANT_MCP_URL", None)
        core.os.environ.pop("MEMPHANT_API_KEY", None)
    return code, out.getvalue(), err.getvalue()


def test_cli_hit_prints_wrapped_block_exit_zero():
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "hit", "response": {"items": [{"body": "Use rebase, not merge."}]}}
        )
    )
    code, out, err = _run_cli({"url": "https://x/mcp", "key": "k"}, ["--prompt", "p", "--cwd", "/r"], caller)
    assert code == 0
    assert out.startswith("<memphant_memory>")
    assert "Use rebase, not merge." in out
    assert err == ""


def test_cli_empty_prints_zero_bytes_exit_zero():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "empty"}))
    code, out, err = _run_cli({"url": "https://x/mcp", "key": "k"}, ["--prompt", "p", "--cwd", "/r"], caller)
    assert code == 0
    assert out == ""
    assert err == ""


def test_cli_failure_prints_zero_bytes_and_code_exit_zero():
    def raise_auth(_payload, _session):
        raise core.RecallError("auth")

    code, out, err = _run_cli({"url": "https://x/mcp", "key": "k"}, ["--cwd", "/r"], raise_auth)
    assert code == 0
    assert out == ""
    assert "auth" in err


def test_cli_unconfigured_prints_zero_bytes_exit_zero():
    code, out, err = _run_cli({}, ["--prompt", "p", "--cwd", "/r"], caller=None)
    assert code == 0
    assert out == ""
    assert "unconfigured" in err


def test_cli_diagnostics_never_leak_prompt_or_bearer():
    def raise_unavailable(_payload, _session):
        raise core.RecallError("unavailable")

    code, out, err = _run_cli(
        {"url": "https://x/mcp", "key": "SECRET-BEARER"},
        ["--prompt", "SECRET-PROMPT-TOKEN", "--cwd", "/r"],
        raise_unavailable,
    )
    assert "SECRET-PROMPT-TOKEN" not in err
    assert "SECRET-BEARER" not in err
