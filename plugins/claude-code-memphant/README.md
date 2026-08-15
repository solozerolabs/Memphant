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

Add the plugin to your Claude Code plugins. With the repo checked out:

```
# ~/.claude/settings.json (or project .claude/settings.json) — point at the plugin dir
```

Claude Code discovers `.claude-plugin/plugin.json` and registers the hooks in
`hooks/hooks.json`. Set `MEMPHANT_MCP_URL` and `MEMPHANT_API_KEY` in the
environment Claude Code runs in.

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
