# Cross-harness memory injection adapters

## Spec

**Outcome.** Generalize the proven prompt-boundary memory injection (Codex
`UserPromptSubmit` hook → `additionalContext`) to FOUR coding-agent harnesses —
Codex, Claude Code, opencode, pi — all sharing ONE recall implementation. When a
turn starts, each harness injects at most one MemPhant compact card as a
delimited advisory block. Voluntary MCP tool calls got zero uptake; boundary
injection makes the agent actually use memory.

**Shared core.** Extract the harness-agnostic recall core that already lives in
`plugins/codex-memphant/hooks/user_prompt_submit.py` (`RecallError`,
`_bounded_query`, `_parse_body`, `http_caller`, `recall_card`) into an importable
Python-stdlib module with a CLI entrypoint at
`plugins/_shared/memphant_recall.py`:
- As a library: `recall_card(prompt, cwd, caller) -> str` — unchanged behavior
  (raw card body on a hit, `""` on an honest empty, `RecallError` on any
  failure/pending state).
- As a CLI: `python3 memphant_recall.py --prompt "..." --cwd "..."` reads env
  `MEMPHANT_MCP_URL` + `MEMPHANT_API_KEY`, prints the injection block to stdout
  (empty string on honest-empty or ANY failure), writes secret-free diagnostics
  to stderr, and exits 0 always so it can never break a host turn.

**memori principle.** The shared core formats a non-empty card as a delimited
advisory block, never raw text:
```
<memphant_memory>
Advisory context from prior sessions — use only if relevant to the current task.
{card_body}
</memphant_memory>
```
On honest-empty it emits zero bytes (no empty block). This is `advisory_block()`
in the shared core; the CLI and the three new adapters emit it.

**Adapters** (each owns ONLY input-parsing + its injection envelope):
1. Codex (`plugins/codex-memphant/`) — refactor the existing hook to import the
   shared core and delete the now-duplicated functions. Behavior preserved
   exactly (raw card in `additionalContext`) so its existing test passes with an
   import-path change only; the memori wrapper is a shared-core capability the
   new adapters adopt.
2. Claude Code (`plugins/claude-code-memphant/`) — a CC plugin: `plugin.json` +
   `hooks.json` registering `UserPromptSubmit` (stdin `prompt`+`cwd`) and
   `SessionStart` (no prompt → query from `cwd`). One Python hook script imports
   the shared core, emits `hookSpecificOutput.{hookEventName,additionalContext}`
   for the event on stdin, wraps the card in the advisory block, and truncates
   the block safely to the 10,000-char injection cap.
3. opencode (`plugins/opencode-memphant/`) — a TS plugin using
   `experimental.chat.system.transform` → `output.system.push(block)`. Shells out
   to the shared recall CLI (`python3 .../memphant_recall.py`).
4. pi (`plugins/pi-memphant/`) — a TS extension:
   `export default (pi) => pi.on("before_agent_start", async (event) => ({ systemPrompt: event.systemPrompt + "\n" + block }))`.
   Shells out to the shared recall CLI. pi has NO MCP support, so this extension
   is the only memory path on pi.

**Non-goals.** Capture is out of scope (injection only). No backwards-compat
shims (pre-production repo). No new heavy deps: Python stdlib only; TS adapters
use only node built-ins (`child_process`) + the harness's own plugin types. No
vitest/jest/bundler.

**Trade-off priority.** Accuracy > cost > speed, good UX above all. Every adapter
MUST fail safe: any recall error injects zero bytes and never breaks the host
turn.

## Plan

1. Create `plugins/_shared/memphant_recall.py`: move the core verbatim from the
   Codex hook, add `ADVISORY_HEADER` + `advisory_block(card)`, and add a
   `build_block()` + argparse CLI `main()` (reads env, prints the wrapped block,
   exit 0 always, secret-free stderr).
2. Add `tests/test_shared_recall.py`: fake-caller tests for `recall_card`
   (hit/empty/pending/malformed/oversized/auth/timeout), `advisory_block`
   wrapping (present on hit, zero bytes on empty), and the CLI via a fake
   transport (subprocess with a stub, or in-process `main` with monkeypatched
   caller).
3. Refactor `plugins/codex-memphant/hooks/user_prompt_submit.py` to import the
   shared core (add `_shared` to `sys.path` from `__file__`), delete the
   duplicated functions, keep `run()`/`main()`/envelope. Confirm
   `tests/test_codex_hook.py` passes (import-path update only).
4. Create `plugins/claude-code-memphant/`: `.claude-plugin/plugin.json`,
   `hooks/hooks.json` (UserPromptSubmit + SessionStart), `hooks/memphant_hook.py`
   (imports shared core, injectable `run()`, event-aware envelope, 10k cap),
   `README.md`.
5. Add `tests/test_claude_code_hook.py`: fake stdin + injected fake caller;
   assert the CC envelope for both events and the 10k truncation.
6. Create `plugins/opencode-memphant/`: `index.ts` (type-only import of `Plugin`,
   `experimental.chat.system.transform` shelling out to the CLI, fail-safe),
   `package.json`, `index.test.ts` (node-stdlib smoke test with a stubbed CLI via
   `MEMPHANT_RECALL_CLI` env override), `README.md`.
7. Create `plugins/pi-memphant/`: `index.ts` (`before_agent_start` handler
   shelling out to the CLI, returns `{systemPrompt}`), `index.test.ts` (node
   smoke test with a stubbed CLI), `README.md` documenting install locations.
8. Run the Harness block; complete verify; complete learn.

## Harness

```sh
python3 -m pytest tests/test_shared_recall.py tests/test_codex_hook.py tests/test_claude_code_hook.py -q
node --test plugins/opencode-memphant/index.test.ts
node --test plugins/pi-memphant/index.test.ts
python3 -m json.tool plugins/codex-memphant/hooks/hooks.json
python3 -m json.tool plugins/claude-code-memphant/.claude-plugin/plugin.json
python3 -m json.tool plugins/claude-code-memphant/hooks/hooks.json
```
