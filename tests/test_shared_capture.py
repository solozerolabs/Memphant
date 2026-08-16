"""Behavioral tests for the shared capture core (stdlib, no sockets, no LLM).

The core lives under plugins/_shared/; load it by path and drive `build_capture`
with a STUBBED summarizer and a STUBBED poster so every filter branch and the
summarize->post happy path are exercised without a server or a model. Secret
redaction is asserted to run BEFORE the summarizer and before the poster ever
sees a body.
"""

import importlib.util
import json
from pathlib import Path

_CORE_PATH = (
    Path(__file__).resolve().parents[1] / "plugins" / "_shared" / "memphant_capture.py"
)
_spec = importlib.util.spec_from_file_location("memphant_capture", _CORE_PATH)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def _collector():
    """A stub poster that records every item it is handed."""
    posted = []
    return posted, (lambda item: posted.append(item))


def _summary_of(bullets):
    """A stub summarizer that returns a fixed body and records its input."""
    seen = {}

    def summarize(text):
        seen["input"] = text
        return bullets

    return seen, summarize


def _turn(user, assistant):
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


# --- secret redaction (runs FIRST) ----------------------------------------


def test_redact_scrubs_known_secret_shapes():
    raw = (
        "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 and "
        "sk-abcdefghijklmnopqrstuvwxyz012345 and AKIAIOSFODNN7EXAMPLE "
        "Bearer abcdefghijklmnop.qrstuv-wx and xoxb-1234567890-abcdEFGH"
    )
    out = core.redact_secrets(raw)
    for leak in ["ghp_", "sk-abcdef", "AKIAIOSFODNN7", "xoxb-1234", "abcdefghijklmnop.qrstuv"]:
        assert leak not in out, f"secret survived redaction: {leak}"
    assert core._SECRET_SENTINEL in out


def test_summary_redacts_secret_before_summarizer_sees_it():
    posted, poster = _collector()
    seen, summarizer = _summary_of("- deploy key rotates weekly\n- store it in the vault")
    messages = _turn(
        "how do we auth?",
        "Use the deploy key ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 for the release job every week.",
    )
    result = core.build_capture(
        {"source": "summary", "messages": messages}, summarizer=summarizer, poster=poster
    )
    assert result.posted
    # The summarizer NEVER saw the raw token.
    assert "ghp_" not in seen["input"]
    assert core._SECRET_SENTINEL in seen["input"]
    # And the poster body is clean.
    assert "ghp_" not in posted[0]["body"]


def test_mirror_redacts_secret_before_posting():
    posted, poster = _collector()
    content = "# Memory\nDeploy uses AKIAIOSFODNN7EXAMPLE and it rotates every Friday afternoon."
    result = core.build_capture(
        {"source": "mirror", "content": content}, summarizer=None, poster=poster
    )
    assert result.posted
    assert "AKIA" not in posted[0]["body"]
    assert posted[0]["source"] == "mirror"


# --- exclusion filters -----------------------------------------------------


def test_summary_skips_subagent_session():
    posted, poster = _collector()
    _, summarizer = _summary_of("- something learned here that is long enough to matter")
    result = core.build_capture(
        {"source": "summary", "agent_id": "sub-1", "messages": _turn("q", "a" * 80)},
        summarizer=summarizer,
        poster=poster,
    )
    assert not result.posted
    assert result.code == "subagent"
    assert posted == []


def test_summary_length_gates_trivial_turn():
    posted, poster = _collector()
    _, summarizer = _summary_of("should never be called")
    result = core.build_capture(
        {"source": "summary", "messages": _turn("hi", "ok done")},
        summarizer=summarizer,
        poster=poster,
    )
    assert not result.posted
    assert result.code == "too_short"
    assert posted == []


def test_summary_drops_phatic_turn():
    posted, poster = _collector()
    _, summarizer = _summary_of("nope")
    # Long enough to pass the length gate but purely phatic.
    result = core.build_capture(
        {"source": "summary", "messages": _turn("thanks a lot!", "Sure, no problem at all, happy to help you out here!")},
        summarizer=summarizer,
        poster=poster,
    )
    assert not result.posted
    assert result.code == "phatic"
    assert posted == []


def test_summary_drops_assistant_echo_of_user():
    posted, poster = _collector()
    _, summarizer = _summary_of("nope")
    echoed = "We deploy the release build to the staging host on Fridays before noon."
    result = core.build_capture(
        {"source": "summary", "messages": _turn(echoed, echoed)},
        summarizer=summarizer,
        poster=poster,
    )
    assert not result.posted
    assert result.code == "echo"
    assert posted == []


def test_summary_skips_repo_recoverable_code():
    posted, poster = _collector()
    _, summarizer = _summary_of("nope")
    code = (
        "def deploy():\n"
        "    subprocess.run(['make', 'deploy'])\n"
        "    return git_push('origin', 'main')\n"
        "import os\n"
    )
    result = core.build_capture(
        {"source": "summary", "messages": _turn("show me the deploy fn", code)},
        summarizer=summarizer,
        poster=poster,
    )
    assert not result.posted
    assert result.code == "repo_recoverable"
    assert posted == []


# --- happy paths -----------------------------------------------------------


def test_summary_happy_path_summarizes_then_posts():
    posted, poster = _collector()
    seen, summarizer = _summary_of("- release uses cargo build --release\n- deploy runs make deploy")
    messages = [
        {"role": "user", "content": "what's our release process?"},
        {"role": "assistant", "content": [{"type": "thinking", "text": "hidden CoT"}, {"type": "text", "text": "We build with cargo build --release, then deploy by running make deploy on the release host."}]},
    ]
    result = core.build_capture(
        {"source": "summary", "cwd": "/repo", "messages": messages},
        summarizer=summarizer,
        poster=poster,
    )
    assert result.posted
    assert result.source == "summary"
    # Thinking block was stripped from the summarizer input.
    assert "hidden CoT" not in seen["input"]
    assert "cargo build --release" in seen["input"]
    assert posted[0]["source"] == "summary"
    assert posted[0]["subject"]  # a non-empty subject key
    assert "make deploy" in posted[0]["body"]


def test_mirror_happy_path_posts_content_verbatim_no_summarizer():
    posted, poster = _collector()

    def exploding_summarizer(_text):
        raise AssertionError("mirror must not call the summarizer")

    content = "# MEMORY\nThe staging deploy token rotates every Friday and lives in the vault."
    result = core.build_capture(
        {"source": "mirror", "content": content},
        summarizer=exploding_summarizer,
        poster=poster,
    )
    assert result.posted
    assert posted[0]["source"] == "mirror"
    assert "rotates every Friday" in posted[0]["body"]


def test_summarizer_failure_is_a_silent_noop():
    posted, poster = _collector()

    def failing(_text):
        raise RuntimeError("boom")

    result = core.build_capture(
        {"source": "summary", "messages": _turn("q that is long enough to matter here", "A genuinely substantive answer about the deploy pipeline and its ordering.")},
        summarizer=failing,
        poster=poster,
    )
    assert not result.posted
    assert result.code == "summarizer_error"
    assert posted == []


def test_bad_source_is_rejected():
    posted, poster = _collector()
    result = core.build_capture({"source": "nope"}, summarizer=None, poster=poster)
    assert not result.posted
    assert result.code == "bad_source"
    assert posted == []


# --- CLI (main) ------------------------------------------------------------


def test_cli_unconfigured_exits_zero_without_posting():
    import io

    saved = {}
    for env in ["MEMPHANT_CAPTURE_URL", "MEMPHANT_API_KEY"]:
        saved[env] = core.os.environ.pop(env, None)
    err = io.StringIO()
    try:
        code = core.main(stdin=io.StringIO('{"source":"summary","messages":[]}'), stderr=err)
    finally:
        for env, value in saved.items():
            if value is not None:
                core.os.environ[env] = value
    assert code == 0
    assert "unconfigured" in err.getvalue()


# --- P0-B idempotency = body hash, subject = identity ----------------------


def _capture_requests(monkeypatch):
    """Intercept urllib so http_poster's request (headers + body) is inspected
    without a socket."""
    seen = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, _n=-1):
            return b""

    def fake_urlopen(request, timeout=None):
        seen.append({"headers": dict(request.header_items()), "body": json.loads(request.data.decode())})
        return _Resp()

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    return seen


_IDENTITY = {"subject_id": "s", "scope_id": "sc", "actor_id": "a", "agent_node_id": "n", "subject_generation": 1, "observed_at": "2026-08-15T00:00:00+00:00"}


def test_idempotency_key_is_body_hash_not_subject(monkeypatch):
    seen = _capture_requests(monkeypatch)
    post = core.http_poster("http://x/v1/episodes", "k", _IDENTITY)
    post({"source": "summary", "subject": "repo:tz gotcha", "body": "first body about tz"})
    post({"source": "summary", "subject": "repo:tz gotcha", "body": "second body about tz"})
    post({"source": "summary", "subject": "repo:tz gotcha", "body": "first body about tz"})
    keys = [s["headers"]["Idempotency-key"] for s in seen]
    assert keys[0] != keys[1], "same subject + new body must get a NEW idempotency key"
    assert keys[0] == keys[2], "same body must be idempotent"
    assert keys[0].startswith("capture:summary:")
    assert seen[0]["body"]["payload"]["episode"]["subject"] == "repo:tz gotcha"
    assert "kind" not in seen[0]["body"]["payload"]["episode"]


def test_poster_uses_the_channel_as_the_kind_hint_never_a_kind_field(monkeypatch):
    # The server mints Procedural from `capture://errfix` (the CHANNEL is the hint);
    # the episode payload is deny_unknown_fields, so a `kind` field would 422 the
    # whole capture. Perturbation: an item carrying `kind` still must not forward it.
    seen = _capture_requests(monkeypatch)
    post = core.http_poster("http://x/v1/episodes", "k", _IDENTITY)
    post({"source": "errfix", "kind": "procedural", "subject": "repo:errfix:abc", "body": "When x fails, do y"})
    assert seen[0]["body"]["source_ref"] == "capture://errfix"
    assert "kind" not in seen[0]["body"]["payload"]["episode"]


# --- P1-B stable TOPIC subject -------------------------------------------


def test_topic_line_is_parsed_normalised_and_stripped(monkeypatch):
    monkeypatch.setattr(core, "repo_slug", lambda cwd: "myrepo")
    posted, poster = _collector()
    _seen, summarizer = _summary_of("TOPIC: Postgres  Advisory-Locks!! gotcha extra words beyond six\n- lock loop must block before claim\n- try-lock leaves a split")
    result = core.build_capture(
        {"source": "summary", "cwd": "/repo", "messages": _turn("why does claim split?", "The claim splits because the try-lock is evaluated before the snapshot; a blocking lock loop fixes it.")},
        summarizer=summarizer, poster=poster,
    )
    assert result.posted
    assert posted[0]["subject"] == "myrepo:postgres advisory locks gotcha extra words"
    assert not posted[0]["body"].lower().startswith("topic")
    assert posted[0]["body"].startswith("- lock loop")


def test_same_topic_different_bullets_share_a_subject(monkeypatch):
    monkeypatch.setattr(core, "repo_slug", lambda cwd: "myrepo")
    subjects = []
    for bullets in ("- the TZ env var must be UTC for the cron\n- else jobs drift", "- set TZ=UTC or the scheduler misfires by an hour"):
        posted, poster = _collector()
        _s, summarizer = _summary_of(f"TOPIC: cron timezone gotcha\n{bullets}")
        core.build_capture({"source": "summary", "cwd": "/r", "messages": _turn("why is cron late?", "Cron ran an hour late because the container TZ was not UTC; setting it fixed the drift.")}, summarizer=summarizer, poster=poster)
        subjects.append(posted[0]["subject"])
    assert subjects[0] == subjects[1] == "myrepo:cron timezone gotcha"


def test_no_topic_line_falls_back_to_legacy_first_eight_tokens():
    posted, poster = _collector()
    _s, summarizer = _summary_of("- release uses cargo build then make deploy on the host\n- second bullet")
    core.build_capture({"source": "summary", "messages": _turn("what is our process for shipping?", "We build with cargo build --release, then deploy by running make deploy on the release host.")}, summarizer=summarizer, poster=poster)
    assert posted[0]["subject"] == "release uses cargo build then make deploy on"


def test_none_or_topic_only_summary_is_empty():
    for out in ("", "NONE", "TOPIC: something\n"):
        posted, poster = _collector()
        _s, summarizer = _summary_of(out)
        result = core.build_capture({"source": "summary", "messages": _turn("what is our process for shipping?", "We build with cargo build --release, then deploy by running make deploy on the release host.")}, summarizer=summarizer, poster=poster)
        assert not result.posted and result.code == "empty_summary" and posted == []


def test_repo_slug_prefers_git_toplevel_then_basename(tmp_path):
    repo = tmp_path / "toprepo"
    (repo / "sub").mkdir(parents=True)
    core.subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert core.repo_slug(str(repo / "sub")) == "toprepo"
    plain = tmp_path / "plaindir"
    plain.mkdir()
    # Not under a repo → basename; empty → global.
    assert core.repo_slug("") == "global"
    assert core.repo_slug("/definitely/not/a/dir/xyz") == "xyz"


# --- P0-C summary ceiling fits the 512-token card --------------------------


def test_summary_is_capped_at_a_bullet_boundary():
    bullets = "\n".join(f"- bullet number {i} carries roughly sixty characters of durable text here." for i in range(60))
    posted, poster = _collector()
    _s, summarizer = _summary_of("TOPIC: long summary\n" + bullets)
    core.build_capture({"source": "summary", "messages": _turn("q?", "A genuinely substantive answer about the deploy pipeline and its ordering.")}, summarizer=summarizer, poster=poster)
    body = posted[0]["body"]
    assert len(body) <= core.MAX_SUMMARY_BODY_CHARS
    assert body.endswith("text here."), "must cut at a bullet boundary, never mid-bullet"
    assert core.MAX_SUMMARY_BODY_CHARS < 1600  # ~512 tokens at chars/3
    assert core.truncate_at_boundary("x" * 50, 10) == "x" * 10  # single overlong line: hard cut


# --- survival witness -------------------------------------------------------


def test_session_outcome_rules():
    ok = [{"role": "user", "content": "please fix the flaky test"}, {"role": "assistant", "content": "Done: the retry loop now blocks."}]
    assert core.session_outcome(ok) == "success"
    corrected = ok + [{"role": "user", "content": "No, that's not it — revert that."}]
    assert core.session_outcome(corrected) == "corrected"
    failing = [{"role": "user", "content": "fix it"}, {"role": "assistant", "content": "I couldn't reproduce it; the error persists."}]
    assert core.session_outcome(failing) == "failure"
    assert core.session_outcome(ok, [{"kind": "cmd", "ok": True}, {"kind": "cmd", "ok": False}]) == "failure"
    assert core.session_outcome(ok, [{"kind": "cmd", "ok": False}, {"kind": "cmd", "ok": True}]) == "success"


def _receipt(tmp_path, records):
    d = tmp_path / ".memphant"
    d.mkdir(exist_ok=True)
    (d / ".served.json").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return d / ".served.json"


def test_survival_mark_posts_used_ids_then_clears_receipt(tmp_path):
    path = _receipt(tmp_path, [
        {"ts": "2026-08-15T10:00:00+00:00", "query_sha256": "q", "unit_ids": ["u1", "u2"], "labels": {}},
        {"ts": "2026-08-15T10:05:00+00:00", "query_sha256": "q2", "unit_ids": ["u2", "u3"], "labels": {}},
    ])
    marks = []
    code = core.post_survival_mark(str(tmp_path), "success", lambda outcome, ids, trace_id=None: marks.append((outcome, ids)))
    assert code == "marked_success"
    assert marks == [("success", ["u1", "u2", "u3"])]
    assert path.read_text() == ""


def test_survival_mark_respects_session_start_and_outcome(tmp_path):
    _receipt(tmp_path, [
        {"ts": "2026-08-15T09:00:00+00:00", "unit_ids": ["old"]},
        {"ts": "2026-08-15T10:05:00+00:00", "unit_ids": ["new"]},
    ])
    marks = []
    core.post_survival_mark(str(tmp_path), "corrected", lambda o, ids, trace_id=None: marks.append((o, ids)), since="2026-08-15T10:00:00+00:00")
    assert marks == [("corrected", ["new"])]


def test_survival_mark_missing_receipt_or_error_is_noop(tmp_path):
    marks = []
    assert core.post_survival_mark(str(tmp_path), "success", lambda o, ids, trace_id=None: marks.append(1)) == "no_receipt"
    assert marks == []
    _receipt(tmp_path, [{"ts": "t", "unit_ids": ["u"]}])

    def boom(_o, _ids):
        raise RuntimeError("socket")

    assert core.post_survival_mark(str(tmp_path), "success", boom) == "mark_error"


def test_http_marker_posts_mark_request_shape(monkeypatch):
    seen = _capture_requests(monkeypatch)
    assert core.mark_url_from_capture_url("http://x/v1/episodes") == "http://x/v1/mark"
    core.http_marker("http://x/v1/mark", "k", _IDENTITY)("success", ["u1"])
    body = seen[0]["body"]
    assert body["outcome"] == "success" and body["used_ids"] == ["u1"]
    assert set(body) == {"subject_id", "scope_id", "actor_id", "agent_node_id", "subject_generation", "trace_id", "caller_id", "used_ids", "outcome"}
    assert seen[0]["headers"]["Authorization"] == "Bearer k"
