#!/usr/bin/env python3
"""Shared MemPhant CAPTURE core for the cross-harness write-side adapters.

The write-side twin of `memphant_recall.py`: ONE implementation behind thin
per-harness adapters. Python hooks import this module; TypeScript adapters
(opencode, pi) shell out to its CLI (`build_capture`). Stdlib only.

Two capture channels feed the Stage A trust ladder through the Part-1 write seam
(a `retain` Episode tagged `source_ref = capture://<source>`):

- **summary** — a session-end LLM summary of the transcript's LAST turn.
- **mirror**  — an explicit in-repo memory-file write (MEMORY.md, AGENTS.md, ...),
  copied verbatim. No LLM.

Both land as inert `Belief` candidates at `AgentOutput` trust; the reflect job's
cross-check ladders them (mirror + summary agree → corroborated; diverge →
quarantined). Capture is invisible and fail-safe: it NEVER breaks a host turn and
NEVER logs secrets, prompts, or bodies.

Mandatory write-time exclusions, applied in this order (cheap, pre-store, no LLM):
1. **secret redaction FIRST** — tokens are scrubbed before any other processing,
   so a secret can never reach a filter, a summarizer, stderr, or the store.
2. drop the turn when it is a subagent session (`agent_id` present).
3. length-gate trivial turns.
4. drop phatic/filler turns.
5. drop assistant echoes of the user.
6. skip repo-recoverable content (grep's turf — our differentiated exclusion).

As a library:
    build_capture(payload, *, summarizer, poster) -> CaptureResult
        The orchestrator. `payload` is the normalized capture request (see
        `CLI` below). `summarizer` and `poster` are INJECTED so the test path
        never calls a live LLM or opens a socket.

As a CLI (the entrypoint the TS adapters shell out to):
    python3 memphant_capture.py            # reads one JSON request on stdin
        Reads env MEMPHANT_CAPTURE_SUMMARIZER_CMD (the cheap-model shell-out) and
        the retain endpoint config; prints a one-line secret-free status to
        stderr; ALWAYS exits 0.

Guarantees: no secret/prompt/body ever reaches stderr; on any error, capture is
a silent no-op; a skipped turn is a normal outcome, not a failure.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Callable, Optional

# --- ceilings -------------------------------------------------------------

# A captured body is bounded so a runaway transcript cannot flood the store.
MAX_CAPTURE_BODY_CHARS = 8192
MAX_SUMMARIZER_INPUT_CHARS = 32 * 1024
DEFAULT_SUMMARIZER_TIMEOUT_SECONDS = 20.0
DEFAULT_POST_TIMEOUT_SECONDS = 3.0

# Length gate: a turn shorter than this (after redaction + phatic strip) is too
# trivial to be worth a memory.
MIN_MEANINGFUL_CHARS = 40

CAPTURE_SOURCES = ("mirror", "summary")


# --- secret redaction (runs BEFORE anything else) -------------------------

# Ordered, conservative token patterns. Each is redacted to a fixed sentinel so
# the fact "a secret was here" survives while the value never does.
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}\b"),  # GitHub PAT / OAuth / server / refresh
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # OpenAI-style
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),  # bearer tokens
    re.compile(r"\beyJ[A-Za-z0-9._-]{20,}\b"),  # JWT
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\s*[:=]\s*\S{6,}"),
]

_SECRET_SENTINEL = "[REDACTED]"


def redact_secrets(text: str) -> str:
    """Scrub known secret shapes. Runs FIRST, before any filter or summarizer,
    so a secret can never leak downstream. Idempotent."""
    redacted = text or ""
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_SECRET_SENTINEL, redacted)
    return redacted


# --- transcript parsing (last turn only) ----------------------------------

# Content-block types stripped from summarizer input (tool noise / hidden CoT).
_STRIPPED_BLOCK_TYPES = {"tool_use", "tool_result", "thinking", "redacted_thinking"}
_TEXT_ROLES = {"user", "assistant"}


def _block_text(block) -> str:
    """Extract visible text from one Anthropic-style content block, dropping
    tool-call / tool-result / thinking blocks entirely."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    if block.get("type") in _STRIPPED_BLOCK_TYPES:
        return ""
    # A text block, or a shape carrying `text`/`content`.
    text = block.get("text")
    if isinstance(text, str):
        return text
    inner = block.get("content")
    if isinstance(inner, str):
        return inner
    return ""


def _message_text(message: dict) -> str:
    """Flatten one message's content to visible text (tool noise stripped)."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(part for part in (_block_text(b) for b in content) if part).strip()
    return ""


def extract_last_turn(messages: list) -> tuple[str, str]:
    """Return (user_text, assistant_text) for the transcript's LAST turn only.

    The last turn = the final assistant message and the most recent user message
    before it. System/tool messages are ignored; tool-call/tool-result/thinking
    blocks inside a message are stripped. Either half may be empty.
    """
    if not isinstance(messages, list):
        return "", ""
    turns = [m for m in messages if isinstance(m, dict) and m.get("role") in _TEXT_ROLES]
    assistant_text = ""
    last_assistant_idx = None
    for idx in range(len(turns) - 1, -1, -1):
        if turns[idx].get("role") == "assistant":
            assistant_text = _message_text(turns[idx])
            last_assistant_idx = idx
            break
    user_text = ""
    upper = last_assistant_idx if last_assistant_idx is not None else len(turns)
    for idx in range(upper - 1, -1, -1):
        if turns[idx].get("role") == "user":
            user_text = _message_text(turns[idx])
            break
    return user_text, assistant_text


# --- exclusion filters ----------------------------------------------------

_PHATIC = {
    "sure", "ok", "okay", "k", "thanks", "thank you", "thx", "got it", "gotcha",
    "done", "yes", "yep", "yeah", "no", "nope", "no problem", "np", "sounds good",
    "great", "perfect", "cool", "nice", "hi", "hello", "hey", "hi there",
    "you're welcome", "welcome", "of course", "understood", "will do", "on it",
}

# Filler words that carry no capturable knowledge. A turn is phatic when nothing
# SUBSTANTIVE survives their removal — this catches longer pleasantries ("Sure,
# no problem at all, happy to help you out!") that the ≤4-word rule misses.
_FILLER = {
    "sure", "ok", "okay", "thanks", "thank", "thx", "got", "gotcha", "done",
    "yes", "yep", "yeah", "no", "nope", "np", "great", "perfect", "cool", "nice",
    "hi", "hello", "hey", "welcome", "course", "understood", "will", "on", "it",
    "problem", "help", "helping", "happy", "glad", "please", "here", "there",
    "out", "all", "at", "to", "you", "your", "i", "we", "let", "know", "with",
    "for", "and", "is", "that", "this", "of", "up", "so", "just", "really",
    "very", "much", "a", "an", "the", "sounds", "good", "any", "me", "my",
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def is_phatic(text: str) -> bool:
    """True when the text is only an acknowledgement / greeting / filler — i.e.
    nothing SUBSTANTIVE remains once filler words are removed."""
    stripped = (text or "").strip().strip(".!,;: ").lower()
    if not stripped:
        return True
    if stripped in _PHATIC:
        return True
    words = _tokens(stripped)
    if not words:
        return True
    # A short turn made entirely of phatic words (e.g. "ok, thanks!").
    if len(words) <= 4 and all(w in _PHATIC or len(w) <= 2 for w in words):
        return True
    # A longer pleasantry with no substantive token left after filler removal.
    substantive = [w for w in words if w not in _FILLER and len(w) > 2]
    return not substantive


def is_echo(user_text: str, assistant_text: str) -> bool:
    """True when the assistant text merely restates the user's own words — no new
    knowledge to capture. High token overlap in either direction counts."""
    u = set(_tokens(user_text))
    a = set(_tokens(assistant_text))
    if not a or not u:
        return False
    overlap = len(u & a)
    # Assistant almost entirely contained in the user's words, or vice versa.
    return overlap / len(a) >= 0.8 or overlap / len(u) >= 0.9


# Lines that look like code / shell / paths — repo-recoverable (grep's turf).
_CODE_LINE = re.compile(
    r"""^\s*(
        [$#>]\s              # shell prompt
        | (?:import|from|def|class|fn|func|package|const|let|var|return|public|private|async)\b
        | (?:git|cargo|npm|pnpm|yarn|pip|python3?|node|make|docker|kubectl|bash|sh|sudo)\s
        | [\w./-]+/[\w./-]+  # a path
        | [{}\[\]();]        # a bare bracket line
        | //|/\*|\*|--\s     # comment markers
    )""",
    re.X,
)


def is_repo_recoverable(text: str) -> bool:
    """Best-effort: True when the content is predominantly code / shell / paths,
    which `grep` recovers from the repo far better than memory can. Conservative
    — only fires when the MAJORITY of non-empty lines look like code, or the whole
    body is a single fenced code block."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    fenced = (text or "").strip().count("```")
    stripped = (text or "").strip()
    if stripped.startswith("```") and fenced >= 2:
        # A body that is essentially one code block.
        non_fence = [ln for ln in lines if not ln.strip().startswith("```")]
        code = sum(1 for ln in non_fence if _CODE_LINE.match(ln))
        if non_fence and code / len(non_fence) >= 0.5:
            return True
    code_lines = sum(1 for ln in lines if _CODE_LINE.match(ln))
    return code_lines / len(lines) > 0.6


class CaptureResult:
    """The outcome of one capture attempt. `posted` is True only when a body was
    actually POSTed. `code` is a short, secret-free status for stderr. A plain
    class (not a dataclass) so the module loads cleanly under importlib
    `spec_from_file_location` without being registered in `sys.modules`."""

    __slots__ = ("posted", "code", "source", "subject")

    def __init__(self, posted: bool, code: str, source: Optional[str] = None, subject: Optional[str] = None):
        self.posted = posted
        self.code = code
        self.source = source
        self.subject = subject

    def __repr__(self) -> str:
        return f"CaptureResult(posted={self.posted}, code={self.code!r}, source={self.source!r})"


def _skip(code: str, source: Optional[str] = None) -> CaptureResult:
    return CaptureResult(posted=False, code=code, source=source)


def _subject_key(body: str) -> str:
    """A stable, subject-like key derived from the captured body's first
    meaningful line — so a mirror and a summary expressing the same fact tend to
    share a `fact_key` and can be cross-checked. Lexical and deterministic."""
    for line in (body or "").splitlines():
        words = _tokens(line)
        if words:
            return " ".join(words[:8])
    return " ".join(_tokens(body)[:8])


# --- the orchestrator -----------------------------------------------------

def build_capture(payload: dict, *, summarizer: Callable[[str], str], poster: Callable[[dict], None]) -> CaptureResult:
    """Run one capture. `payload` fields:
        source: "mirror" | "summary"        (required)
        cwd:    project directory            (optional, provenance)
        agent_id / is_subagent:              subagent marker -> skip
        For "mirror":  content: the file body being mirrored.
        For "summary": messages: [{role, content}, ...] transcript.

    `summarizer(text) -> str` produces the summary bullets (INJECTED). `poster(
    item) -> None` writes the capture through the Part-1 seam (INJECTED). Returns
    a `CaptureResult`; never raises for a normal skip. Applies every mandatory
    exclusion, secrets FIRST.
    """
    if not isinstance(payload, dict):
        return _skip("bad_payload")
    source = payload.get("source")
    if source not in CAPTURE_SOURCES:
        return _skip("bad_source")

    # 2. Subagent sessions never capture (avoid fan-out of derivative memories).
    if payload.get("agent_id") or payload.get("is_subagent"):
        return _skip("subagent", source)

    if source == "mirror":
        raw = payload.get("content")
        if not isinstance(raw, str):
            return _skip("no_content", source)
        # 1. SECRETS FIRST.
        body = redact_secrets(raw).strip()
        if len(body) < MIN_MEANINGFUL_CHARS:  # 3. length gate
            return _skip("too_short", source)
        # A memory file the user deliberately maintains IS the memory — no
        # repo-recoverable filter here (unlike a summarized transcript).
        body = body[:MAX_CAPTURE_BODY_CHARS]
        item = {"source": "mirror", "subject": _subject_key(body), "body": body, "cwd": payload.get("cwd", "")}
        poster(item)
        return CaptureResult(posted=True, code="posted", source="mirror", subject=item["subject"])

    # source == "summary"
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return _skip("no_messages", source)
    user_text, assistant_text = extract_last_turn(messages)
    # 1. SECRETS FIRST — redact both halves before any inspection.
    user_text = redact_secrets(user_text)
    assistant_text = redact_secrets(assistant_text)

    # The captured knowledge lives in what the ASSISTANT concluded this turn.
    candidate = assistant_text.strip()
    if len(candidate) < MIN_MEANINGFUL_CHARS:  # 3. length gate
        return _skip("too_short", source)
    if is_phatic(candidate):  # 4. filler
        return _skip("phatic", source)
    if is_echo(user_text, assistant_text):  # 5. echo of the user
        return _skip("echo", source)
    if is_repo_recoverable(candidate):  # 6. grep's turf
        return _skip("repo_recoverable", source)

    summarizer_input = f"{user_text}\n\n{assistant_text}".strip()[:MAX_SUMMARIZER_INPUT_CHARS]
    try:
        summary = summarizer(summarizer_input)
    except Exception:
        # A summarizer failure is a silent no-op, never a broken turn.
        return _skip("summarizer_error", source)
    # The summary is model output: redact again defensively, then bound it.
    summary = redact_secrets((summary or "").strip())
    if len(summary) < MIN_MEANINGFUL_CHARS:
        return _skip("empty_summary", source)
    summary = summary[:MAX_CAPTURE_BODY_CHARS]
    item = {"source": "summary", "subject": _subject_key(summary), "body": summary, "cwd": payload.get("cwd", "")}
    poster(item)
    return CaptureResult(posted=True, code="posted", source="summary", subject=item["subject"])


# --- live summarizer + poster (production wiring; stubbed in tests) --------

# The cheap-model shell-out. Documented default: a small, fast model. The command
# receives the last-turn text on stdin and must print the summary to stdout. An
# external-observer prompt (2-10 bullets, "do not answer the question, record
# what was decided") should be baked into the command itself.
DEFAULT_SUMMARIZER_CMD = (
    # A documented, cheap default. Operators override via
    # MEMPHANT_CAPTURE_SUMMARIZER_CMD. Example (Anthropic Haiku via a wrapper):
    #   claude -p 'Summarize what was decided as 2-10 terse bullets; do not answer.'
    ""
)


def shell_summarizer(command: str, timeout: float = DEFAULT_SUMMARIZER_TIMEOUT_SECONDS) -> Callable[[str], str]:
    """A summarizer that pipes the turn text through a shell command's stdin and
    returns its stdout. Kept separate so tests inject a fake."""

    def summarize(text: str) -> str:
        result = subprocess.run(
            command,
            shell=True,
            input=text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError("summarizer_nonzero")
        return result.stdout

    return summarize


def http_poster(
    url: str,
    bearer: str,
    identity: dict,
    timeout: float = DEFAULT_POST_TIMEOUT_SECONDS,
) -> Callable[[dict], None]:
    """A poster that writes a capture through the Part-1 seam: a `retain` Episode
    POST to the REST episodes endpoint, tagged `source_ref = capture://<source>`.

    `identity` supplies the context the REST retain requires (subject_id,
    scope_id, actor_id, agent_node_id, subject_generation) — a live agent
    deployment configures these once; capture never invents identity. Kept
    separate so tests inject a fake and no socket opens on the test path.
    """

    def post(item: dict) -> None:
        request_body = {
            "subject_id": identity["subject_id"],
            "scope_id": identity["scope_id"],
            "actor_id": identity["actor_id"],
            "agent_node_id": identity["agent_node_id"],
            "subject_generation": identity["subject_generation"],
            "source_ref": f"capture://{item['source']}",
            "observed_at": identity.get("observed_at", ""),
            "payload": {
                "episode": {
                    "source_kind": "agent",
                    "body": item["body"],
                    "subject": item["subject"],
                }
            },
        }
        data = json.dumps(request_body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}",
            "Idempotency-Key": f"capture:{item['source']}:{item['subject']}",
        }
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)  # drain; we do not surface the body

    return post


def load_transcript_messages(path: str) -> list:
    """Read a JSONL transcript file into a normalized `[{role, content}, ...]`
    list. Handles the Claude Code / Codex line shapes:
      - `{"type": "user"|"assistant", "message": {"role", "content"}}`
      - `{"role", "content"}` (already normalized)
    Unknown line shapes and unreadable files degrade to an empty list — capture
    is a best-effort observer, never a hard dependency. Bounded so a giant
    transcript cannot blow up memory: only the last `_TRANSCRIPT_TAIL` lines are
    parsed (the last turn is all `extract_last_turn` needs).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    messages = []
    for line in lines[-_TRANSCRIPT_TAIL:]:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        inner = record.get("message")
        if isinstance(inner, dict) and "role" in inner:
            messages.append({"role": inner.get("role"), "content": inner.get("content")})
        elif "role" in record and "content" in record:
            messages.append({"role": record.get("role"), "content": record.get("content")})
    return messages


# Only the tail of a transcript is needed (the last turn); cap the parse so a
# multi-megabyte JSONL never has to be fully materialized.
_TRANSCRIPT_TAIL = 200


def resolve_capture_config(require_summarizer: bool) -> Optional[dict]:
    """Assemble the live capture config from env, or None when unconfigured.
    `require_summarizer=True` (session-summarize adapters) also demands a
    summarizer command; a file-mirror adapter passes False (mirror needs no LLM).
    """
    url = os.environ.get("MEMPHANT_CAPTURE_URL")
    bearer = os.environ.get("MEMPHANT_API_KEY")
    identity = _load_identity()
    if not url or not bearer or not identity:
        return None
    summarizer_cmd = os.environ.get("MEMPHANT_CAPTURE_SUMMARIZER_CMD", DEFAULT_SUMMARIZER_CMD)
    if require_summarizer and not summarizer_cmd:
        return None
    return {"url": url, "bearer": bearer, "identity": identity, "summarizer_cmd": summarizer_cmd}


def make_live_summarizer(config: dict) -> Callable[[str], str]:
    """A live shell-out summarizer from a resolved config, or a no-op when no
    command is configured (a mirror-only deployment)."""
    command = config.get("summarizer_cmd")
    if not command:
        return lambda _text: ""
    return shell_summarizer(command)


def _load_identity() -> Optional[dict]:
    """Assemble the retain identity from env, or None when unconfigured."""
    keys = {
        "subject_id": "MEMPHANT_SUBJECT_ID",
        "scope_id": "MEMPHANT_SCOPE_ID",
        "actor_id": "MEMPHANT_ACTOR_ID",
        "agent_node_id": "MEMPHANT_AGENT_NODE_ID",
        "subject_generation": "MEMPHANT_SUBJECT_GENERATION",
    }
    identity = {}
    for field_name, env in keys.items():
        value = os.environ.get(env)
        if not value:
            return None
        if field_name == "subject_generation":
            try:
                value = int(value)
            except ValueError:
                return None
        identity[field_name] = value
    identity["observed_at"] = os.environ.get("MEMPHANT_CAPTURE_OBSERVED_AT", "")
    return identity


def main(argv=None, stdin=None, stderr=None) -> int:
    """CLI entrypoint. Reads one JSON capture request on stdin, runs the live
    summarizer + poster, prints a secret-free status line, and ALWAYS exits 0."""
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    try:
        raw = stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        print("memphant-capture: skip code=bad_stdin", file=stderr)
        return 0

    # A summary capture needs a summarizer; a mirror capture does not.
    require_summarizer = payload.get("source") == "summary"
    config = resolve_capture_config(require_summarizer=require_summarizer)
    if config is None:
        print("memphant-capture: skip code=unconfigured", file=stderr)
        return 0
    summarizer = make_live_summarizer(config)
    poster = http_poster(config["url"], config["bearer"], config["identity"])
    try:
        result = build_capture(payload, summarizer=summarizer, poster=poster)
    except Exception:
        # Never break a host turn; never surface a body.
        print("memphant-capture: no-capture code=internal", file=stderr)
        return 0
    print(f"memphant-capture: code={result.code}", file=stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
