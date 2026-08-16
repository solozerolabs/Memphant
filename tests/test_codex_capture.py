"""Behavioral test for the Codex session-capture hook (stdlib, no sockets, no LLM).

Load the hook by path and drive `run()` with a fake stdin and a STUBBED `build`
(the shared capture core) so the adapter's transcript-normalization and envelope
are exercised without summarizing or opening a socket.
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
    / "session_capture.py"
)
_spec = importlib.util.spec_from_file_location("session_capture", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


class _Result:
    def __init__(self, code):
        self.code = code
        self.posted = code == "posted"


def _stub_build(record):
    def build(payload):
        record.append(payload)
        return _Result("posted")

    return build


def _run(event, build):
    stdin = io.StringIO(json.dumps(event))
    out, err = io.StringIO(), io.StringIO()
    code = hook.run(stdin, out, err, build)
    return code, out.getvalue(), err.getvalue()


def test_stop_event_with_inline_messages_builds_a_summary_payload():
    record = []
    event = {
        "cwd": "/repo",
        "messages": [
            {"role": "user", "content": "how do we release?"},
            {"role": "assistant", "content": "We build with cargo and deploy make deploy."},
        ],
    }
    code, _out, err = _run(event, _stub_build(record))
    assert code == 0
    assert len(record) == 1
    payload = record[0]
    assert payload["source"] == "summary"
    assert payload["cwd"] == "/repo"
    assert payload["messages"] == event["messages"]
    assert "code=posted" in err


def test_stop_event_reads_transcript_path_when_no_inline_messages(tmp_path):
    record = []
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"role": "user", "content": "q"}}),
                json.dumps(
                    {"type": "assistant", "message": {"role": "assistant", "content": "an answer"}}
                ),
            ]
        ),
        encoding="utf-8",
    )
    code, _out, _err = _run({"transcript_path": str(transcript)}, _stub_build(record))
    assert code == 0
    assert len(record) == 1
    assert record[0]["messages"][-1]["content"] == "an answer"


def test_no_transcript_is_a_silent_noop():
    record = []
    code, _out, err = _run({"cwd": "/repo"}, _stub_build(record))
    assert code == 0
    assert record == []
    assert "no_transcript" in err


def test_bad_stdin_never_raises_and_does_not_build():
    record = []
    stdin = io.StringIO("{ not json")
    out, err = io.StringIO(), io.StringIO()
    code = hook.run(stdin, out, err, _stub_build(record))
    assert code == 0
    assert record == []
    assert "bad_stdin" in err.getvalue()


def test_build_exception_is_swallowed():
    def exploding(_payload):
        raise RuntimeError("boom")

    event = {"messages": [{"role": "assistant", "content": "something substantive here"}]}
    stdin = io.StringIO(json.dumps(event))
    out, err = io.StringIO(), io.StringIO()
    code = hook.run(stdin, out, err, exploding)
    assert code == 0
    assert "internal" in err.getvalue()


# --- session-end tail: errfix + survival witness + projection ---------------


def _rollout_lines(cwd, fail_then_fix=True):
    def call(cid, cmd):
        return {"timestamp": "2026-08-15T10:00:00.000Z", "type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": cid, "arguments": json.dumps({"cmd": cmd})}}

    def out(cid, code, text):
        return {"type": "response_item", "payload": {"type": "function_call_output", "call_id": cid, "output": f"Exit code: {code}\nOutput:\n{text}"}}

    lines = [
        {"timestamp": "2026-08-15T10:00:00.000Z", "type": "session_meta", "payload": {"cwd": cwd}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "make the nightly job run on time please"}]}},
        call("c1", "python3 job.py"), out("c1", 1, "ValueError: naive datetime; TZ not set"),
    ]
    if fail_then_fix:
        lines += [call("c2", "export TZ=UTC"), out("c2", 0, ""), call("c3", "python3 job.py"), out("c3", 0, "ran")]
    lines.append({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "The job needed TZ=UTC in the container environment; it now runs on schedule."}]}})
    return lines


def _write_rollout(tmp_path, lines):
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return path


def _receipt(cwd, records):
    d = cwd / ".memphant"
    d.mkdir(exist_ok=True)
    (d / ".served.json").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return d / ".served.json"


def test_stop_hook_posts_errfix_marks_success_and_projects(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    rollout = _write_rollout(tmp_path, _rollout_lines(str(cwd)))
    receipt = _receipt(cwd, [{"ts": "2026-08-15T10:01:00+00:00", "query_sha256": "q", "unit_ids": ["u1", "u2"], "labels": {}},
                             {"ts": "2026-08-15T09:00:00+00:00", "unit_ids": ["stale-before-session"]}])
    posted, marks, projected = [], [], []
    stdin = io.StringIO(json.dumps({"cwd": str(cwd), "transcript_path": str(rollout)}))
    err = io.StringIO()
    code = hook.run(stdin, io.StringIO(), err, _stub_build([]), poster=posted.append,
                    marker=lambda o, ids, trace_id=None: marks.append((o, ids)), projector=lambda c: projected.append(c) or "projected")
    assert code == 0
    assert len(posted) == 1 and posted[0]["source"] == "errfix" and posted[0]["kind"] == "procedural"
    assert "TZ=UTC" in posted[0]["body"]
    assert marks == [("success", ["u1", "u2"])]
    assert receipt.read_text() == ""
    assert projected == [str(cwd)]
    out = err.getvalue()
    assert "errfix=1" in out and "survival=marked_success" in out and "projection=projected" in out


def test_stop_hook_marks_corrected_when_last_user_message_corrects(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    lines = _rollout_lines(str(cwd)) + [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "No, that's wrong — revert it."}]}},
    ]
    rollout = _write_rollout(tmp_path, lines)
    _receipt(cwd, [{"ts": "2026-08-15T10:01:00+00:00", "unit_ids": ["u1"]}])
    marks = []
    hook.run(io.StringIO(json.dumps({"cwd": str(cwd), "transcript_path": str(rollout)})), io.StringIO(), io.StringIO(),
             _stub_build([]), poster=lambda i: None, marker=lambda o, ids, trace_id=None: marks.append((o, ids)))
    assert marks == [("corrected", ["u1"])]


def test_stop_hook_marks_failure_when_last_tool_result_failed(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    rollout = _write_rollout(tmp_path, _rollout_lines(str(cwd), fail_then_fix=False))
    _receipt(cwd, [{"ts": "2026-08-15T10:01:00+00:00", "unit_ids": ["u1"]}])
    posted, marks = [], []
    hook.run(io.StringIO(json.dumps({"cwd": str(cwd), "transcript_path": str(rollout)})), io.StringIO(), io.StringIO(),
             _stub_build([]), poster=posted.append, marker=lambda o, ids, trace_id=None: marks.append((o, ids)))
    assert posted == []  # no fix pair
    assert marks == [("failure", ["u1"])]


def test_stop_hook_without_receipt_is_a_noop_and_never_raises(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    rollout = _write_rollout(tmp_path, _rollout_lines(str(cwd)))
    marks = []

    def exploding_projector(_c):
        raise RuntimeError("boom")

    def exploding_poster(_i):
        raise RuntimeError("boom")

    err = io.StringIO()
    code = hook.run(io.StringIO(json.dumps({"cwd": str(cwd), "transcript_path": str(rollout)})), io.StringIO(), err,
                    _stub_build([]), poster=exploding_poster, marker=lambda o, ids, trace_id=None: marks.append(1), projector=exploding_projector)
    assert code == 0 and marks == []
    assert "survival=no_receipt" in err.getvalue() and "errfix=0" in err.getvalue()
    assert "projection=projection_error" in err.getvalue()
