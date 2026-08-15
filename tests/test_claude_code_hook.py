"""Behavioral test for the Claude Code memory-injection hook (stdlib, no sockets).

Load the hook by path and drive `run()` with a fake in-process caller so both
events (UserPromptSubmit, SessionStart) and the 10k injection cap are exercised
without a server.
"""

import importlib.util
import io
import json
from pathlib import Path

_HOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "claude-code-memphant"
    / "hooks"
    / "memphant_hook.py"
)
_spec = importlib.util.spec_from_file_location("cc_memphant_hook", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def _recall_body(structured):
    return json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": structured}}
    ).encode("utf-8")


def _fake_caller(*, on_call):
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


def _run(event, caller):
    stdin = io.StringIO(json.dumps(event))
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = hook.run(stdin, stdout, stderr, caller)
    assert code == 0
    return json.loads(stdout.getvalue()), stderr.getvalue()


def test_user_prompt_submit_injects_wrapped_block():
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "hit", "response": {"items": [{"body": "Run make deploy."}]}}
        )
    )
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "how do we deploy?", "cwd": "/repo"}
    out, err = _run(event, caller)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert hso["additionalContext"].startswith("<memphant_memory>")
    assert "Run make deploy." in hso["additionalContext"]
    assert err == ""


def test_session_start_uses_cwd_and_labels_event():
    # SessionStart carries no prompt; the query is the cwd alone.
    seen = {}

    def on_call(payload):
        seen["query"] = payload["params"]["arguments"]["query"]
        return _recall_body({"state": "hit", "response": {"items": [{"body": "Repo uses pnpm."}]}})

    caller = _fake_caller(on_call=on_call)
    event = {"hook_event_name": "SessionStart", "cwd": "/repo", "source": "startup"}
    out, err = _run(event, caller)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert "Repo uses pnpm." in hso["additionalContext"]
    # The query used the cwd (no prompt).
    assert seen["query"].strip() == "/repo"


def test_empty_injects_nothing_without_error():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "empty"}))
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "hello there", "cwd": "/repo"}
    out, err = _run(event, caller)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert err == ""


def test_failure_injects_nothing_and_logs_code():
    def raise_timeout(_payload, _session):
        raise hook.RecallError("timeout")

    event = {"hook_event_name": "UserPromptSubmit", "prompt": "q", "cwd": "/repo"}
    out, err = _run(event, raise_timeout)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "timeout" in err


def test_injection_is_capped_at_10k_and_stays_well_formed():
    big = "y" * 50000
    caller = _fake_caller(
        on_call=lambda _p: _recall_body(
            {"state": "hit", "response": {"items": [{"body": big}]}}
        )
    )
    event = {"hook_event_name": "SessionStart", "cwd": "/repo"}
    out, _err = _run(event, caller)
    block = out["hookSpecificOutput"]["additionalContext"]
    assert len(block) <= hook.INJECTION_CAP
    # Truncation keeps the block well-formed: opening and closing tags survive.
    assert block.startswith("<memphant_memory>")
    assert block.endswith("</memphant_memory>")


def test_missing_cwd_skips_cleanly():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "hit"}))
    out, err = _run({"hook_event_name": "UserPromptSubmit", "prompt": "no cwd"}, caller)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
    assert "missing_fields" in err


def test_unknown_event_falls_back_to_user_prompt_submit():
    caller = _fake_caller(on_call=lambda _p: _recall_body({"state": "empty"}))
    out, _err = _run({"prompt": "q", "cwd": "/repo"}, caller)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
