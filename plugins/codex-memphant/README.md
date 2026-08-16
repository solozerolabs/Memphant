# codex-memphant

MemPhant coding-agent memory for Codex — recall on the way in, capture on the way
out. Thin adapters over the shared cores (`plugins/_shared/memphant_recall.py`,
`plugins/_shared/memphant_capture.py`); all logic lives there, one implementation
per concern.

## Install

One command, from your repo:

```
python3 /path/to/memphant/plugins/install.py
```

Auto-detects Codex (`~/.codex`/`$CODEX_HOME`), registers the UserPromptSubmit +
Stop hooks (merged into `hooks.json`), writes the stable MemPhant block into this
repo's `AGENTS.md`, and gitignores `.memphant/`. Idempotent. Then set the env vars
it prints (`--print-env`). The MCP server registration for the `hooks` surface
stays in `.mcp.json` (deployment-specific); the always-in-context `AGENTS.md`
pointer makes the `file`/`cli` surfaces work with no MCP at all.

## Recall (read side)

`hooks/user_prompt_submit.py` (UserPromptSubmit) injects at most one MemPhant
compact card as `additionalContext`. Config: `MEMPHANT_MCP_URL`,
`MEMPHANT_API_KEY`. Unconfigured ⇒ no-op.

## Capture (write side)

`hooks/session_capture.py` (Stop) summarizes the transcript's LAST turn via a
cheap-model shell-out and posts it through the write seam tagged `source=summary`.
Async, invisible, fail-safe (never breaks a session, never logs secrets).

**Codex is SUMMARIZE-ONLY — there is no file-mirror.** Every other harness gets a
high-precision file-mirror augment (a `PreToolUse`/`tool.execute.before` hook that
copies a `MEMORY.md`/`AGENTS.md` write). Codex cannot: its file edits go through
`apply_patch`, and Codex hooks fire for shell (`Bash`) tools only — `apply_patch`
edits are invisible to the hook layer. So Codex captures memory from the session
summary alone. This is a documented harness limitation, not a scope cut.

**Experimental-API note:** Codex's Stop-event payload shape is not covered by a
stable public contract at the time of writing; `session_capture.py` reads the
transcript defensively (`messages` / `transcript` / `transcript_path`) and no-ops
when none is present. Update `_messages_from_event` if Codex changes it.

Capture config: `MEMPHANT_CAPTURE_URL` (REST episodes endpoint),
`MEMPHANT_CAPTURE_SUMMARIZER_CMD` (cheap-model shell-out; turn text on stdin,
bullets on stdout), and the bound identity (`MEMPHANT_SUBJECT_ID` /
`MEMPHANT_SCOPE_ID` / `MEMPHANT_ACTOR_ID` / `MEMPHANT_AGENT_NODE_ID` /
`MEMPHANT_SUBJECT_GENERATION`).

## Tests

- Recall: `python3 -m pytest tests/test_codex_hook.py -q`
- Capture: `python3 -m pytest tests/test_codex_capture.py tests/test_shared_capture.py -q`
