# opencode-memphant

Portable MemPhant coding-agent memory for **opencode**. It injects at most one
compact MemPhant memory card as a delimited advisory block into the system
prompt, just before the model call — no tool call, no voluntary uptake required.

Injection only. Capture is out of scope.

## How it works

opencode plugins are TypeScript and cannot import Python, so this adapter shells
out to the shared recall core (`plugins/_shared/memphant_recall.py`) as a CLI:

```
python3 plugins/_shared/memphant_recall.py --prompt "" --cwd "<session dir>"
```

The CLI prints the memori advisory block (or zero bytes on honest-empty / any
failure). The plugin pushes that block onto `output.system` in the
`experimental.chat.system.transform` hook:

```
<memphant_memory>
Advisory context from prior sessions — use only if relevant to the current task.
{card_body}
</memphant_memory>
```

The recall is scoped to the session directory (a cold-start scope), because the
system-prompt transform fires before a user prompt is available in a stable
shape.

Fail-safe: any recall error (or an unconfigured endpoint) pushes zero bytes and
never breaks the turn.

## Experimental-API uncertainty

`experimental.chat.system.transform` is an **experimental** opencode surface and
is not documented in the public plugin reference (https://opencode.ai/docs/plugins)
at the time of writing. This adapter implements it to the shape used by the
working memsearch reference plugin: `(input, output) => void`, where
`output.system` is a string array of system-prompt entries. If a future opencode
release changes the hook name or the `output.system` shape, update the single
hook in `index.ts`. Everything else (recall, wrapping, fail-safe) is unaffected.

## Configuration

The shared CLI reads two environment variables (inherited by the plugin's
`spawnSync`) and injects nothing if either is unset:

- `MEMPHANT_MCP_URL` — the MemPhant Streamable-HTTP MCP endpoint.
- `MEMPHANT_API_KEY` — the bearer token.

`MEMPHANT_RECALL_CLI` overrides the CLI path (used by the test harness).

## Install

Symlink or reference `index.ts` from opencode's plugin directory
(`~/.config/opencode/plugins/`), or add the package to `opencode.json`'s
`plugin` array. Keep `plugins/_shared/memphant_recall.py` reachable at
`../_shared/` relative to `index.ts` (as in this repo), or set
`MEMPHANT_RECALL_CLI` to its absolute path.

## Manual verification

```
node --test plugins/opencode-memphant/index.test.ts
```

The smoke test runs the real hook against a stubbed recall CLI and asserts the
block is pushed onto `output.system`. For an end-to-end check, set the env vars
against a running MemPhant endpoint and run the CLI directly (see above).

## Tests

Executable node-stdlib smoke test (no framework): `node --test index.test.ts`.
It stubs the recall CLI via `MEMPHANT_RECALL_CLI` and asserts the push behavior
plus the fail-safe path.

## Memory capture (write side)

Two capture hooks shell out to the shared capture CLI
(`plugins/_shared/memphant_capture.py`), the write-side twin of recall:

- `session.idle` — summarizes the last turn and posts it tagged `source=summary`.
- `tool.execute.before` (`write`/`edit`) — when the target is a memory file
  (`MEMORY.md`, `AGENTS.md`, or the `MEMPHANT_CAPTURE_MIRROR_FILES` set), copies
  the content tagged `source=mirror`. ALLOW-AND-COPY: it never blocks the write.

Both are async and fail-safe. `session.idle` and `tool.execute.before` payload
shapes are experimental opencode surfaces (read defensively; update the single
seam in `index.ts` if opencode changes them). Capture config mirrors the
Claude Code plugin (`MEMPHANT_CAPTURE_URL`, `MEMPHANT_CAPTURE_SUMMARIZER_CMD`,
and the bound identity env vars).

Capture smoke test: `node --test capture.test.ts` (stubs the capture CLI via
`MEMPHANT_CAPTURE_CLI`).
