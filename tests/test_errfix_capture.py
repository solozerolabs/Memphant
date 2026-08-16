"""Behavioral tests for the deterministic error->fix channel (stdlib, no LLM,
no sockets) over synthetic Codex rollouts."""

import importlib.util
import json
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[1] / "plugins" / "_shared"
sys.path.insert(0, str(_SHARED))
_spec = importlib.util.spec_from_file_location("errfix_capture", _SHARED / "errfix_capture.py")
errfix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(errfix)

CWD = "/work/myrepo"


def _call(call_id, cmd):
    return {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": call_id, "arguments": json.dumps({"cmd": cmd})}}


def _out(call_id, exit_code, text):
    return {"type": "response_item", "payload": {"type": "function_call_output", "call_id": call_id, "output": f"Exit code: {exit_code}\nWall time: 0.1 seconds\nOutput:\n{text}"}}


def _custom(call_id, cmd):
    js = 'const r = await tools.exec_command({"cmd":' + json.dumps(cmd) + ',"workdir":"/w"});\ntext(r.output);'
    return {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": call_id, "input": js}}


def _custom_out(call_id, ok, text):
    head = "Script completed" if ok else "Script failed"
    return {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": call_id, "output": [{"type": "input_text", "text": f"{head}\nWall time 0.0 seconds\nOutput:\n"}, {"type": "input_text", "text": text}]}}


def _patch(paths, ok=True):
    return {"type": "event_msg", "payload": {"type": "patch_apply_end", "success": ok, "changes": {p: {"type": "update"} for p in paths}}}


def _posted():
    items = []
    return items, items.append


def test_env_fix_pair_is_posted_as_procedural_errfix(monkeypatch):
    monkeypatch.setattr(errfix, "repo_slug", lambda cwd: "myrepo")
    records = [
        _call("c1", "pytest tests -q"),
        _out("c1", 1, "ModuleNotFoundError: No module named 'psycopg'\nfailed"),
        _call("c2", "pip install psycopg"),
        _out("c2", 0, "Successfully installed psycopg-3.2"),
        _call("c3", "pytest tests -q"),
        _out("c3", 0, "12 passed"),
    ]
    items, poster = _posted()
    assert errfix.capture_errfix(records, CWD, poster) == 1
    item = items[0]
    assert item["source"] == "errfix" and item["kind"] == "procedural"
    assert item["subject"].startswith("myrepo:errfix:") and len(item["subject"].split(":")[-1]) == 12
    assert item["body"].startswith("When `pytest tests -q` fails with `ModuleNotFoundError: No module named 'psycopg'`, the fix was: run `pip install psycopg`")
    assert len(item["body"]) < 700


def test_errfix_subject_is_stable_across_sessions_and_secrets_redacted(monkeypatch):
    monkeypatch.setattr(errfix, "repo_slug", lambda cwd: "myrepo")
    records = [
        _call("c1", "curl https://api.example.com/x"),
        _out("c1", 6, "curl: (6) Could not resolve host"),
        _call("c2", "export API_KEY=supersecretvalue123 && curl https://api.example.com/x"),
        _out("c2", 0, "ok"),
        _call("c3", "curl https://api.example.com/x"),
        _out("c3", 0, "ok"),
    ]
    a, pa = _posted()
    b, pb = _posted()
    errfix.capture_errfix(records, CWD, pa)
    errfix.capture_errfix(list(records), CWD, pb)
    assert a[0]["subject"] == b[0]["subject"]
    assert "supersecretvalue123" not in a[0]["body"]


def test_no_pair_when_command_never_succeeds_or_never_failed():
    records = [
        _call("c1", "cargo test"), _out("c1", 101, "error[E0425]: cannot find value"),
        _call("c2", "cargo test"), _out("c2", 101, "error[E0425]: cannot find value"),
        _call("c3", "ls"), _out("c3", 0, "a b c"),
    ]
    items, poster = _posted()
    assert errfix.capture_errfix(records, CWD, poster) == 0
    assert items == []


def test_in_repo_code_fix_is_skipped_as_grep_turf():
    records = [
        _custom("c1", "python3 -m pytest tests/test_x.py"),
        _custom_out("c1", False, "  File \"/work/myrepo/src/mod.py\", line 3\nSyntaxError: invalid syntax"),
        _patch(["/work/myrepo/src/mod.py"]),
        _custom("c2", "python3 -m pytest tests/test_x.py"),
        _custom_out("c2", True, "1 passed"),
    ]
    items, poster = _posted()
    assert errfix.capture_errfix(records, CWD, poster) == 0


def test_out_of_repo_edit_fix_is_kept(monkeypatch):
    monkeypatch.setattr(errfix, "repo_slug", lambda cwd: "myrepo")
    records = [
        _custom("c1", "make deploy"),
        _custom_out("c1", False, "error: no credentials in ~/.aws/config"),
        _patch(["/Users/me/.aws/config"]),
        _custom("c2", "make deploy"),
        _custom_out("c2", True, "deployed"),
    ]
    items, poster = _posted()
    assert errfix.capture_errfix(records, CWD, poster) == 1
    assert items[0]["body"].endswith("the fix was: edit config")


def test_tool_events_expose_ok_flags_for_session_outcome():
    records = [_call("c1", "x"), _out("c1", 1, "boom"), _custom("c2", "y"), _custom_out("c2", True, "fine"), _patch(["/a"], ok=False)]
    events = errfix.extract_tool_events(records)
    assert [(e["kind"], e["ok"]) for e in events] == [("cmd", False), ("cmd", True), ("patch", False)]
    assert errfix.normalise_command("/bin/zsh -lc \"cd /x &&   pytest   -q\"") == "pytest -q"


def test_json_output_shape_and_load_rollout(tmp_path):
    rec = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": json.dumps({"output": "nope", "metadata": {"exit_code": 2}})}}
    path = tmp_path / "rollout.jsonl"
    path.write_text(json.dumps(_call("c1", "true")) + "\nnot json\n" + json.dumps(rec) + "\n")
    events = errfix.extract_tool_events(errfix.load_rollout_records(str(path)))
    assert events == [{"kind": "cmd", "cmd": "true", "ok": False, "output": "nope"}]
    assert errfix.load_rollout_records(str(tmp_path / "missing.jsonl")) == []
