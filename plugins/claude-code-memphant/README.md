# claude-code-memphant

Portable MemPhant coding-agent memory for **Claude Code**. When a turn starts,
it injects at most one compact MemPhant memory card at the prompt boundary as a
delimited advisory block — the injection path that made agents actually *use*
memory where voluntary MCP tool calls got zero uptake.

Injection only. Capture is out of scope.

## What it does

Two hooks, both calling one Python script (`hooks/memphant_hook.py`):

- **`UserPromptSubmit`** — reads `prompt` + `cwd` from the hook stdin, recalls a
  card scoped to that query, and injects it via
  `hookSpecificOutput.additionalContext`.
- **`SessionStart`** — no prompt is available, so the query is the project `cwd`
  alone (a cold-start recall).

The recalled card is wrapped in the memori advisory block:

```
<memphant_memory>
Advisory context from prior sessions — use only if relevant to the current task.
{card_body}
</memphant_memory>
```

On an honest-empty recall or ANY failure (auth, timeout, malformed, unavailable),
the hook injects **zero bytes** and never breaks the turn. The block is truncated
safely to Claude Code's 10,000-character `additionalContext` cap.

## Shared recall core

All recall logic lives in `plugins/_shared/memphant_recall.py` (Python stdlib,
one implementation shared by every harness adapter). This hook imports it via a
path relative to the plugin, so `plugins/_shared/` must sit alongside
`plugins/claude-code-memphant/` (as it does in this repo). A standalone install
should vendor `memphant_recall.py` next to the hook or keep the `_shared`
directory two levels up.

## Configuration

The hook reads two environment variables and no-ops (injects nothing) if either
is unset:

- `MEMPHANT_MCP_URL` — the MemPhant Streamable-HTTP MCP endpoint (e.g.
  `https://localhost:8787/mcp`).
- `MEMPHANT_API_KEY` — the bearer token; tenant identity is derived server-side.

## Install

One command, from your repo:

```
python3 /path/to/memphant/plugins/install.py
```

It auto-detects Claude Code (`~/.claude`) and Codex (`~/.codex`/`$CODEX_HOME`),
registers the recall + capture hooks (merged into the harness `hooks.json`, never
duplicating your own hooks), writes the stable MemPhant block into this repo's
`AGENTS.md`, and adds `.memphant/` to `.gitignore`. Idempotent — re-run any time.
Flags: `--repo DIR`, `--harness auto|codex|claude-code|none`, `--print-env`.

Then set the two deployment-specific values it prints (`MEMPHANT_MCP_URL` +
`MEMPHANT_API_KEY`, plus `MEMPHANT_CAPTURE_URL` for capture). Nothing else to edit:
the always-in-context `AGENTS.md` pointer makes the `file`/`cli` surfaces work even
before the hooks fire.

## Manual verification

With a running MemPhant MCP endpoint and the env vars set:

```
echo '{"hook_event_name":"UserPromptSubmit","prompt":"how do we deploy?","cwd":"/your/repo"}' \
  | python3 plugins/claude-code-memphant/hooks/memphant_hook.py
```

You should see a JSON envelope whose `additionalContext` is either empty (honest
empty / not configured) or the `<memphant_memory>` block.

## Tests

`python3 -m pytest tests/test_claude_code_hook.py -q` drives the hook with fake
stdin and an injected fake transport (no sockets), asserting the envelope for
both events and the 10k truncation.

## Memory capture (write side)

Two capture hooks feed memory back into MemPhant through the write seam (a
`retain` Episode tagged `source_ref = capture://<source>`); both are async,
invisible, and fail-safe (they never break a turn and never log secrets):

- `hooks/capture_session.py` (Stop / SessionEnd) — summarizes the transcript's
  LAST turn via a cheap-model shell-out and posts it tagged `source=summary`.
- `hooks/capture_file_mirror.py` (PreToolUse `Write|Edit|MultiEdit`) — when the
  agent writes a memory file (`MEMORY.md`, `AGENTS.md`, or the
  `MEMPHANT_CAPTURE_MIRROR_FILES` set), copies the content tagged `source=mirror`.
  ALLOW-AND-COPY: it never blocks the write.

Captured memories land as inert `Belief` candidates; the reflect job's cross-check
promotes a mirror+summary agreement to `corroborated`/recallable and quarantines a
divergence. All capture logic lives in the shared core
(`plugins/_shared/memphant_capture.py`).

Extra config for capture: `MEMPHANT_CAPTURE_URL` (the REST episodes endpoint),
`MEMPHANT_CAPTURE_SUMMARIZER_CMD` (the cheap-model shell-out; reads the turn on
stdin, writes bullets to stdout), and the bound identity
(`MEMPHANT_SUBJECT_ID` / `MEMPHANT_SCOPE_ID` / `MEMPHANT_ACTOR_ID` /
`MEMPHANT_AGENT_NODE_ID` / `MEMPHANT_SUBJECT_GENERATION`). Unconfigured ⇒ no-op.

Tests: `python3 -m pytest tests/test_claude_code_capture.py tests/test_shared_capture.py -q`.
