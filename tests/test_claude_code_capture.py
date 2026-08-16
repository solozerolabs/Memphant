"""Behavioral tests for the Claude Code capture hooks (stdlib, no sockets, no LLM).

Covers BOTH Claude Code capture adapters:
- `capture_session.py`  — Stop/SessionEnd: reads the JSONL transcript, builds a
  `source=summary` payload.
- `capture_file_mirror.py` — PreToolUse Write|Edit|MultiEdit: ALLOW-AND-COPY of a
  memory-file write as `source=mirror`; must NEVER block the host write.

Each drives `run()` with a fake stdin and a STUBBED `build`.
"""

import importlib.util
import io
import json
from pathlib import Path


def _load(name):
    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "claude-code-memphant"
        / "hooks"
        / name
    )
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


session = _load("capture_session.py")
mirror = _load("capture_file_mirror.py")


class _Result:
    def __init__(self, code):
        self.code = code
        self.posted = code == "posted"


def _stub_build(record):
    def build(payload):
        record.append(payload)
        return _Result("posted")

    return build


def _run(module, event, build):
    stdin = io.StringIO(json.dumps(event))
    out, err = io.StringIO(), io.StringIO()
    code = module.run(stdin, out, err, build)
    return code, out.getvalue(), err.getvalue()


# --- session-summarize -----------------------------------------------------


def test_session_reads_transcript_path_and_builds_summary(tmp_path):
    record = []
    transcript = tmp_path / "conversation.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"role": "user", "content": "how do we deploy?"}}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Run make deploy on the release host."}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    code, _out, err = _run(session, {"cwd": "/repo", "transcript_path": str(transcript)}, _stub_build(record))
    assert code == 0
    assert len(record) == 1
    assert record[0]["source"] == "summary"
    last = record[0]["messages"][-1]
    assert last["content"][0]["text"] == "Run make deploy on the release host."
    assert "code=posted" in err


def test_session_subagent_marker_is_forwarded_for_the_core_to_skip(tmp_path):
    record = []
    transcript = tmp_path / "c.jsonl"
    transcript.write_text(
        json.dumps({"role": "assistant", "content": "some substantive content here that matters"}),
        encoding="utf-8",
    )
    _run(session, {"transcript_path": str(transcript), "agent_id": "sub-9"}, _stub_build(record))
    assert record and record[0]["agent_id"] == "sub-9"


def test_session_no_transcript_is_a_noop():
    record = []
    code, _out, err = _run(session, {"cwd": "/repo"}, _stub_build(record))
    assert code == 0
    assert record == []
    assert "no_transcript" in err


# --- file-mirror (ALLOW-AND-COPY) ------------------------------------------


def test_mirror_copies_a_memory_file_write():
    record = []
    event = {
        "cwd": "/repo",
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/MEMORY.md", "content": "The deploy token rotates on Fridays."},
    }
    code, _out, err = _run(mirror, event, _stub_build(record))
    assert code == 0, "mirror must return 0 (allow), never block"
    assert len(record) == 1
    assert record[0]["source"] == "mirror"
    assert "rotates on Fridays" in record[0]["content"]


def test_mirror_edit_uses_new_string():
    record = []
    event = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "AGENTS.md",
            "old_string": "old note",
            "new_string": "New standing rule: prefer trunk-based development.",
        },
    }
    _run(mirror, event, _stub_build(record))
    assert record and "trunk-based development" in record[0]["content"]


def test_mirror_multiedit_concatenates_new_strings():
    record = []
    event = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "MEMORY.md",
            "edits": [
                {"old_string": "a", "new_string": "first new rule line"},
                {"old_string": "b", "new_string": "second new rule line"},
            ],
        },
    }
    _run(mirror, event, _stub_build(record))
    assert record
    body = record[0]["content"]
    assert "first new rule line" in body and "second new rule line" in body


def test_mirror_ignores_non_memory_files():
    record = []
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/src/main.rs", "content": "fn main() {}"},
    }
    code, _out, _err = _run(mirror, event, _stub_build(record))
    assert code == 0, "must still allow the write"
    assert record == [], "a non-memory file is never mirrored"


def test_mirror_ignores_non_write_tools():
    record = []
    event = {"tool_name": "Bash", "tool_input": {"command": "cat MEMORY.md"}}
    code, _out, _err = _run(mirror, event, _stub_build(record))
    assert code == 0
    assert record == []


def test_mirror_never_blocks_on_bad_stdin():
    record = []
    stdin = io.StringIO("{ not json")
    out, err = io.StringIO(), io.StringIO()
    code = mirror.run(stdin, out, err, _stub_build(record))
    assert code == 0, "bad stdin must still allow the write"
    assert record == []


def test_mirror_respects_configured_file_set(monkeypatch):
    record = []
    monkeypatch.setenv("MEMPHANT_CAPTURE_MIRROR_FILES", "NOTES.md")
    # MEMORY.md is no longer in the set; NOTES.md now is.
    event_default = {
        "tool_name": "Write",
        "tool_input": {"file_path": "MEMORY.md", "content": "x" * 60},
    }
    _run(mirror, event_default, _stub_build(record))
    assert record == [], "MEMORY.md not mirrored when the set is overridden"
    event_notes = {
        "tool_name": "Write",
        "tool_input": {"file_path": "NOTES.md", "content": "A configured note about the release cadence."},
    }
    _run(mirror, event_notes, _stub_build(record))
    assert record and record[0]["content"].startswith("A configured note")


# --- session-end tail: survival witness + projection -------------------------


def _transcript(tmp_path, last_user_text, assistant_text):
    path = tmp_path / "transcript.jsonl"
    lines = [
        {"timestamp": "2026-08-15T10:00:00.000Z", "type": "user", "message": {"role": "user", "content": "please make the nightly job run on time"}},
        {"timestamp": "2026-08-15T10:00:05.000Z", "type": "assistant", "message": {"role": "assistant", "content": assistant_text}},
    ]
    if last_user_text:
        lines.append({"timestamp": "2026-08-15T10:00:09.000Z", "type": "user", "message": {"role": "user", "content": last_user_text}})
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return path


def _receipt(cwd, records):
    d = cwd / ".memphant"
    d.mkdir(exist_ok=True)
    (d / ".served.json").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return d / ".served.json"


def test_session_hook_marks_success_over_this_sessions_receipt_and_projects(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    transcript = _transcript(tmp_path, None, "The job needed TZ=UTC in the container environment; it now runs on schedule.")
    receipt = _receipt(cwd, [
        {"ts": "2026-08-15T09:00:00+00:00", "unit_ids": ["stale"]},
        {"ts": "2026-08-15T10:00:07+00:00", "query_sha256": "q", "unit_ids": ["u1", "u2"], "labels": {}},
    ])
    marks, projected = [], []
    err = io.StringIO()
    code = session.run(io.StringIO(json.dumps({"cwd": str(cwd), "transcript_path": str(transcript)})), io.StringIO(), err,
                       _stub_build([]), marker=lambda o, ids, trace_id=None: marks.append((o, ids)), projector=lambda c: projected.append(c) or "projected")
    assert code == 0
    assert marks == [("success", ["u1", "u2"])]
    assert receipt.read_text() == ""
    assert projected == [str(cwd)]
    assert "survival=marked_success" in err.getvalue() and "projection=projected" in err.getvalue()


def test_session_hook_marks_corrected_and_failure(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    for last_user, assistant, expected in (
        ("No, that's not it — undo that.", "Done, the job now uses TZ=UTC.", "corrected"),
        (None, "I couldn't get the scheduler to run; the error persists.", "failure"),
    ):
        transcript = _transcript(tmp_path, last_user, assistant)
        _receipt(cwd, [{"ts": "2026-08-15T10:00:07+00:00", "unit_ids": ["u1"]}])
        marks = []
        session.run(io.StringIO(json.dumps({"cwd": str(cwd), "transcript_path": str(transcript)})), io.StringIO(), io.StringIO(),
                    _stub_build([]), marker=lambda o, ids, trace_id=None: marks.append((o, ids)))
        assert marks == [(expected, ["u1"])]


def test_session_hook_missing_receipt_is_noop_and_fail_safe(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    transcript = _transcript(tmp_path, None, "The job needed TZ=UTC in the container environment; it now runs on schedule.")
    marks = []

    def exploding(_c):
        raise RuntimeError("boom")

    err = io.StringIO()
    code = session.run(io.StringIO(json.dumps({"cwd": str(cwd), "transcript_path": str(transcript)})), io.StringIO(), err,
                       _stub_build([]), marker=lambda o, ids, trace_id=None: marks.append(1), projector=exploding)
    assert code == 0 and marks == []
    assert "survival=no_receipt" in err.getvalue() and "projection=projection_error" in err.getvalue()
