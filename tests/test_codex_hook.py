"""Behavioral test for the Codex UserPromptSubmit hook (stdlib, no sockets).

The hook module lives under plugins/; load it by path and drive `run()` with a
fake in-process caller so every network/error branch is exercised without a
server: hit, empty, consolidation_pending, malformed, oversized, timeout, auth,
and secret-free diagnostics.
"""

import importlib.util
import io
import json
from pathlib import Path

_HOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "codex-memphant"
    / "hooks"
    / "user_prompt_submit.py"
)
_spec = importlib.util.spec_from_file_location("user_prompt_submit", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def _recall_body(structured):
    """A tools/call response body for `structured` recall content."""
    return json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": structured}}
    ).encode("utf-8")


def _fake_caller(*, on_call):
    """Build a caller that answers initialize/initialized/tools-call in order."""
    calls = {"n": 0}

    def caller(payload, session_id):
        calls["n"] += 1
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


def _run(event, caller):
    stdin = io.StringIO(json.dumps(event))
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = hook.run(stdin, stdout, stderr, caller)
    assert code == 0
    return json.loads(stdout.getvalue()), stderr.getvalue()


def _event():
    return {"prompt": "how do we deploy?", "cwd": "/repo", "session_id": "s1"}


def test_hit_injects_the_single_card():
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "hit", "response": {"items": [{"body": "Run make deploy."}]}}
        )
    )
    out, err = _run(_event(), caller)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert out["hookSpecificOutput"]["additionalContext"] == "Run make deploy."
    assert err == ""


def test_empty_injects_nothing_without_error():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "empty"}))
    out, err = _run(_event(), caller)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert err == ""  # an honest empty search is not a diagnostic


def test_consolidation_pending_injects_nothing_and_logs_code():
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "unavailable", "error": {"code": "consolidation_pending"}}
        )
    )
    out, err = _run(_event(), caller)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "consolidation_pending" in err  # not mislabeled as empty


def test_malformed_response_injects_nothing():
    caller = _fake_caller(on_call=lambda _p: b"not json at all")
    out, err = _run(_event(), caller)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "malformed" in err


def test_oversized_response_injects_nothing():
    huge = _recall_body(
        {"state": "hit", "response": {"items": [{"body": "x" * (hook.MAX_RESPONSE_BYTES + 10)}]}}
    )
    caller = _fake_caller(on_call=lambda _p: huge)
    out, err = _run(_event(), caller)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "oversized" in err


def test_timeout_and_auth_inject_nothing_with_codes():
    def raise_timeout(_payload, _session):
        raise hook.RecallError("timeout")

    out, err = _run(_event(), raise_timeout)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "timeout" in err

    def raise_auth(_payload, _session):
        raise hook.RecallError("auth")

    out, err = _run(_event(), raise_auth)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "auth" in err


def test_missing_fields_skip_cleanly():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "hit"}))
    out, err = _run({"prompt": "no cwd here"}, caller)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "missing_fields" in err


def test_diagnostics_never_leak_the_prompt_or_bearer():
    # Even on failure, stderr must not contain the prompt text.
    def raise_unavailable(_payload, _session):
        raise hook.RecallError("unavailable")

    event = {"prompt": "SECRET-PROMPT-TOKEN", "cwd": "/repo"}
    _out, err = _run(event, raise_unavailable)
    assert "SECRET-PROMPT-TOKEN" not in err
