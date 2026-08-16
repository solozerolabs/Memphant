#!/usr/bin/env python3
"""Deterministic error->fix capture channel (ZERO LLM) over a Codex rollout.

Reads the session's tool events in order and detects PAIRS: a shell command whose
output shows failure (non-zero exit / traceback / `error:` line) followed LATER
by the same normalised command succeeding, or by a successful edit to a file the
error names. Each pair becomes ONE terse procedural memory:

    When `<normalised command>` fails with `<first error line>`, the fix was: <...>

posted through the shared `http_poster` as `source=errfix` (`capture://errfix`)
with `payload.episode.kind = "procedural"` and subject
`<repo_slug>:errfix:<sha256(error_signature)[:12]>` — the same error always maps
to the same unit, however it was worded.

Repo-recoverable pairs are SKIPPED (grep's turf): a fix that is a code edit
inside cwd's repo AND whose error names a repo path. Env/external/tooling fixes
(missing env var, wrong flag, dependency version, network, sandbox rules) are
kept — that is what is NOT in the repo.

Rollout shapes handled (Codex `$CODEX_HOME/sessions/**/rollout-*.jsonl`):
  response_item/function_call        name exec_command|shell|shell_command|apply_patch
  response_item/function_call_output output = "Exit code: N ..." | JSON {output, metadata.exit_code}
  response_item/custom_tool_call     name exec, input JS with tools.exec_command({"cmd":...})
  response_item/custom_tool_call_output  output [{text}], first text "Script completed|failed"
  event_msg/patch_apply_end          {success, changes: {path: {...}}}
Fail-safe: any error ⇒ nothing captured; secrets are redacted before posting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memphant_capture import redact_secrets, repo_slug  # noqa: E402

MAX_ERROR_LINE_CHARS = 200
MAX_FIX_CHARS = 400
MAX_ERRFIX_PER_SESSION = 5

_SHELL_TOOLS = {"exec_command", "shell", "shell_command"}
_ERROR_LINE = re.compile(
    r"(traceback|error\b|exception|fatal|not found|no such file|denied|failed|"
    r"cannot |can't |unrecognized|invalid|missing|refused|timed out|timeout)",
    re.I,
)
_EXIT_CODE = re.compile(r"(?:exit code|exited with code)[:\s]+(-?\d+)", re.I)
_TOOL_NOISE = re.compile(r"^(?:Exit code|Wall time|Output|Chunk ID|Original token count|Process exited|Script (?:completed|failed|error))\b", re.I)
_CUSTOM_CMD = re.compile(r'"cmd"\s*:\s*("(?:[^"\\]|\\.)*")')
_SHELL_WRAPPER = re.compile(r"^(?:/bin/)?(?:ba|z)?sh\s+-l?c\s+", re.I)
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", re.M)
_PATH_TOKEN = re.compile(r"(?:~|\.{1,2})?/?[\w.@-]+(?:/[\w.@-]+)+")


# --- rollout parsing -------------------------------------------------------

def load_rollout_records(path: str) -> list:
    """Every JSON record of a rollout file, in order. Unreadable ⇒ []."""
    records = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


def _text_of(output) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(
            part.get("text", "") for part in output if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _parse_output(raw) -> tuple[Optional[int], str]:
    """(exit_code | None, visible output text) across the known output shapes."""
    text = _text_of(raw)
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            meta = obj.get("metadata") or {}
            code = meta.get("exit_code")
            return (int(code) if isinstance(code, int) else None), str(obj.get("output") or "")
        except (ValueError, TypeError, AttributeError):
            pass
    if stripped.startswith("Script failed") or "Script error:" in stripped:
        return 1, text
    if stripped.startswith("Script completed"):
        return 0, text
    match = _EXIT_CODE.search(text)
    return (int(match.group(1)) if match else None), text


def _command_of_call(payload: dict) -> Optional[str]:
    name = payload.get("name")
    ptype = payload.get("type")
    if ptype == "function_call" and name in _SHELL_TOOLS:
        try:
            args = json.loads(payload.get("arguments") or "{}")
        except (ValueError, TypeError):
            return None
        cmd = args.get("cmd") or args.get("command")
        if isinstance(cmd, list):
            cmd = " ".join(str(part) for part in cmd)
        return cmd if isinstance(cmd, str) and cmd.strip() else None
    if ptype == "custom_tool_call" and name == "exec":
        match = _CUSTOM_CMD.search(payload.get("input") or "")
        if match:
            try:
                return json.loads(match.group(1))
            except (ValueError, TypeError):
                return None
    return None


def extract_tool_events(records: list) -> list:
    """Ordered tool events: {"kind":"cmd","cmd","ok","output"} for shell calls,
    {"kind":"patch","paths":[...],"ok"} for file edits."""
    pending = {}  # call_id -> command
    events = []
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        rtype, ptype = record.get("type"), payload.get("type")
        if rtype == "event_msg" and ptype == "patch_apply_end":
            changes = payload.get("changes") or {}
            events.append({"kind": "patch", "paths": sorted(changes.keys()), "ok": bool(payload.get("success"))})
            continue
        if rtype != "response_item":
            continue
        if ptype in ("function_call", "custom_tool_call"):
            if ptype == "function_call" and payload.get("name") == "apply_patch":
                try:
                    args = json.loads(payload.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}
                paths = _PATCH_FILE.findall(str(args.get("input") or ""))
                pending[payload.get("call_id")] = ("patch", paths)
                continue
            cmd = _command_of_call(payload)
            if cmd:
                pending[payload.get("call_id")] = ("cmd", cmd)
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            call = pending.pop(payload.get("call_id"), None)
            if not call:
                continue
            code, text = _parse_output(payload.get("output"))
            if call[0] == "patch":
                events.append({"kind": "patch", "paths": sorted(call[1]), "ok": code == 0 or text.lstrip().startswith("Success")})
                continue
            if code is None:
                ok = not any(_ERROR_LINE.search(ln) and not _TOOL_NOISE.match(ln) for ln in text.splitlines()[:40])
            else:
                ok = code == 0
            events.append({"kind": "cmd", "cmd": call[1], "ok": ok, "output": text})
    return events


# --- pairing ---------------------------------------------------------------

def normalise_command(cmd: str) -> str:
    """Whitespace-collapsed, shell-wrapper-stripped, `cd x &&` prefix dropped."""
    cmd = _SHELL_WRAPPER.sub("", (cmd or "").strip())
    cmd = cmd.strip("'\"")
    cmd = re.sub(r"^cd\s+\S+\s*&&\s*", "", cmd)
    return " ".join(cmd.split())[:MAX_ERROR_LINE_CHARS]


def first_error_line(output: str) -> str:
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip() and not _TOOL_NOISE.match(ln.strip())]
    for line in lines:
        if _ERROR_LINE.search(line):
            return line[:MAX_ERROR_LINE_CHARS]
    return (lines[-1] if lines else "")[:MAX_ERROR_LINE_CHARS]


def _under(path: str, cwd: str) -> bool:
    if not cwd:
        return False
    try:
        return os.path.abspath(os.path.join(cwd, os.path.expanduser(path))).startswith(os.path.abspath(cwd).rstrip("/") + "/")
    except (OSError, ValueError):
        return False


def _error_names_repo_path(error_text: str, cwd: str, patched: list) -> bool:
    """True when the failing output names a path under cwd or one of the
    patched files — the error is anchored in the repo, so the fix is grep's."""
    basenames = {os.path.basename(p) for p in patched if p}
    for token in _PATH_TOKEN.findall(error_text or ""):
        if _under(token, cwd) or os.path.basename(token) in basenames:
            return True
    return any(b and b in error_text for b in basenames)


def _fix_for(events: list, start: int, sig: str, error_text: str, cwd: str) -> Optional[str]:
    """What made the failed command at `events[start]` pass, or None when it
    never did (or the fix is repo-recoverable). Scans forward from the failure:
      - a later run of the SAME command that succeeds ⇒ the fix is whatever
        happened in between (the last successful patch, else the last different
        successful command, else "rerun");
      - a successful edit to a file the error NAMES ⇒ the fix is that edit.
    A patch under cwd whose error names a repo path is grep's turf ⇒ None."""
    last_patch, last_cmd = None, None
    for later in events[start + 1:]:
        kind, ok = later.get("kind"), later.get("ok")
        if kind == "patch" and ok:
            last_patch = later
            if any(os.path.basename(p) in error_text for p in later.get("paths", []) if p):
                return _patch_fix(later, error_text, cwd, rerun=None)
        elif kind == "cmd":
            later_sig = normalise_command(later["cmd"])
            if later_sig == sig:
                if not ok:
                    last_patch, last_cmd = None, None  # still failing: whatever preceded was not the fix
                    continue
                if last_patch is not None:
                    return _patch_fix(last_patch, error_text, cwd, rerun=sig)
                if last_cmd:
                    return f"run `{last_cmd}`, then rerun `{sig}`"
                return f"rerun `{sig}`"
            if ok:
                last_cmd = later_sig
    return None


def _patch_fix(patch: dict, error_text: str, cwd: str, rerun: Optional[str]) -> Optional[str]:
    paths = [p for p in patch.get("paths", []) if p]
    if paths and all(_under(p, cwd) for p in paths) and _error_names_repo_path(error_text, cwd, paths):
        return None  # in-repo code fix named by an in-repo error: repo-recoverable
    fix = "edit " + ", ".join(os.path.basename(p) for p in paths)
    return f"{fix}, then rerun `{rerun}`" if rerun else fix


def find_errfix_pairs(events: list, cwd: str) -> list:
    """Failure→fix pairs, in order, repo-recoverable ones dropped."""
    pairs, seen = [], set()
    for i, ev in enumerate(events):
        if ev.get("kind") != "cmd" or ev.get("ok"):
            continue
        sig = normalise_command(ev["cmd"])
        error_line = first_error_line(ev.get("output", ""))
        if not sig or not error_line:
            continue
        fix = _fix_for(events, i, sig, ev.get("output", "")[:4000], cwd)
        if not fix:
            continue
        signature = f"{sig} :: {error_line}"
        if signature in seen:
            continue
        seen.add(signature)
        pairs.append({"command": sig, "error_line": error_line, "fix": fix[:MAX_FIX_CHARS], "signature": signature})
    return pairs


def errfix_items(pairs: list, cwd: str) -> list:
    """Poster items for `http_poster` (source=errfix, kind=procedural)."""
    slug = repo_slug(cwd)
    items = []
    for pair in pairs[:MAX_ERRFIX_PER_SESSION]:
        body = redact_secrets(
            f"When `{pair['command']}` fails with `{pair['error_line']}`, the fix was: {pair['fix']}"
        )
        digest = hashlib.sha256(pair["signature"].encode("utf-8")).hexdigest()[:12]
        items.append({
            "source": "errfix",
            "kind": "procedural",
            "subject": f"{slug}:errfix:{digest}",
            "body": body,
            "cwd": cwd,
        })
    return items


def capture_errfix(records: list, cwd: str, poster: Callable[[dict], None]) -> int:
    """Detect pairs in `records` and post each; returns the count posted. Never raises."""
    try:
        items = errfix_items(find_errfix_pairs(extract_tool_events(records), cwd), cwd)
        for item in items:
            poster(item)
        return len(items)
    except Exception:
        return 0
