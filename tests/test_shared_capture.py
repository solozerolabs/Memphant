"""Behavioral tests for the shared capture core (stdlib, no sockets, no LLM).

The core lives under plugins/_shared/; load it by path and drive `build_capture`
with a STUBBED summarizer and a STUBBED poster so every filter branch and the
summarize->post happy path are exercised without a server or a model. Secret
redaction is asserted to run BEFORE the summarizer and before the poster ever
sees a body.
"""

import importlib.util
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
