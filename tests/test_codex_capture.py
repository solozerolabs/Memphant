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
