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


def _hit(items):
    return _fake_caller(on_call=lambda _p: _recall_body({"state": "hit", "response": {"items": items}}))


def test_hit_injects_the_single_card():
    items = [{"unit_id": "u1", "body": "Run make deploy.", "inclusion_reason": "validated_procedure"}]
    out, err = _run(_event(), _hit(items))
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    # Codex injects the same memori advisory block as every other adapter.
    injected = out["hookSpecificOutput"]["additionalContext"]
    assert injected == hook.build_block("how do we deploy?", "/repo", _hit(items))
    assert "<memphant_memory>" in injected and "Run make deploy." in injected
    assert "[unconfirmed]" not in injected  # confirmed items carry no label
    assert err == ""


def test_hit_injects_n_cards_in_served_order_with_labels(tmp_path):
    items = [
        {"unit_id": "u1", "body": "Run make deploy.", "inclusion_reason": "validated_procedure"},
        {"unit_id": "u2", "body": "Migrations go first.", "inclusion_reason": "belief captured_unconfirmed"},
        {"unit_id": "u3", "body": "Then smoke-test.", "inclusion_reason": "belief captured_confirmed"},
    ]
    event = {"prompt": "how do we deploy?", "cwd": str(tmp_path), "session_id": "s1"}
    out, err = _run(event, _hit(items))
    injected = out["hookSpecificOutput"]["additionalContext"]
    assert injected.count("\n- ") == 3, injected
    assert injected.index("Run make deploy.") < injected.index("Migrations go first.") < injected.index("Then smoke-test.")
    assert "- [unconfirmed] Migrations go first." in injected
    assert "[unconfirmed] Run make deploy." not in injected
    assert "[unconfirmed] Then smoke-test." not in injected
    assert err == ""
    # Exposure receipt: the Stop hook reads which units were actually served.
    receipt = (tmp_path / ".memphant" / ".served.json").read_text().splitlines()
    assert len(receipt) == 1
    record = json.loads(receipt[0])
    assert record["unit_ids"] == ["u1", "u2", "u3"]
    assert record["labels"] == {"u1": "confirmed", "u2": "unconfirmed", "u3": "confirmed"}


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
