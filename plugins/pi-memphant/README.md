# pi-memphant

Portable MemPhant coding-agent memory for **pi**. It injects at most one compact
MemPhant memory card as a delimited advisory block into the system prompt when a
turn starts.

pi has **no MCP support**, so this extension is the *only* memory path on pi —
there is no voluntary tool-call fallback. Injection only; capture is out of
scope.

## How it works

pi extensions are TypeScript and cannot import Python, so this adapter shells out
to the shared recall core (`plugins/_shared/memphant_recall.py`) as a CLI:

```
python3 plugins/_shared/memphant_recall.py --prompt "<user prompt>" --cwd "<dir>"
```

The extension hooks `before_agent_start` — which fires after the user submits a
prompt and before the agent loop, and can modify the system prompt — and appends
the memori advisory block to `event.systemPrompt`:

```
<memphant_memory>
Advisory context from prior sessions — use only if relevant to the current task.
{card_body}
</memphant_memory>
```

The recall is scoped to the user prompt plus the working directory. Fail-safe:
any recall error (or an unconfigured endpoint) returns the system prompt
unchanged and never breaks the turn.

## API shape

Verified against the pi extension docs
(https://badlogic-pi-mono.mintlify.app/coding-agent/extensions):

- Default export: `export default (pi) => { ... }` — receives the extension API.
- `pi.on("before_agent_start", async (event, ctx) => ({ systemPrompt }))` — the
  event carries the current `systemPrompt`; returning `{ systemPrompt }` replaces
  it.

## Configuration

The shared CLI reads two environment variables (inherited by the extension's
`spawnSync`) and injects nothing if either is unset:

- `MEMPHANT_MCP_URL` — the MemPhant Streamable-HTTP MCP endpoint.
- `MEMPHANT_API_KEY` — the bearer token.

`MEMPHANT_RECALL_CLI` overrides the CLI path (used by the test harness).

## Install

pi discovers extensions from these locations:

- Global (all projects): `~/.pi/agent/extensions/*.ts` or
  `~/.pi/agent/extensions/*/index.ts`
- Project-local: `.pi/extensions/*.ts` or `.pi/extensions/*/index.ts`
- Ad-hoc for testing: `pi -e ./index.ts`

Install by symlinking this directory (or copying `index.ts`) into one of those
locations, e.g.:

```
mkdir -p ~/.pi/agent/extensions/memphant
ln -s "$PWD/plugins/pi-memphant/index.ts" ~/.pi/agent/extensions/memphant/index.ts
```

Keep `plugins/_shared/memphant_recall.py` reachable at `../_shared/` relative to
`index.ts`, or set `MEMPHANT_RECALL_CLI` to its absolute path. Set
`MEMPHANT_MCP_URL` and `MEMPHANT_API_KEY` in pi's environment.

## Manual verification

```
node --test plugins/pi-memphant/index.test.ts
```

The smoke test registers the extension against a fake `pi`, drives the
`before_agent_start` handler against a stubbed recall CLI, and asserts the block
is appended to the system prompt plus the fail-safe path. For an end-to-end
check, set the env vars against a running MemPhant endpoint and run the CLI
directly (see above).

## Tests

Executable node-stdlib smoke test (no framework): `node --test index.test.ts`.

## Memory capture (write side)

pi is the only harness with no MCP, so this extension is also the only capture
path. It shells out to the shared capture CLI (`plugins/_shared/memphant_capture.py`):

- `turn_end` / `agent_end` — reads history via `ctx.sessionManager.getEntries()`,
  summarizes the last turn, posts it tagged `source=summary`.
- `tool_call` (`write`/`edit`) — copies a memory-file write tagged `source=mirror`.
  ALLOW-AND-COPY: it never blocks the write.

Both are async and fail-safe. The `turn_end`/`agent_end`/`tool_call` payloads and
`ctx.sessionManager.getEntries()` are experimental pi surfaces (read defensively;
update the seams in `index.ts` if pi changes them). Capture config mirrors the
Claude Code plugin.

Capture smoke test: `node --test capture.test.ts` (stubs the capture CLI via
`MEMPHANT_CAPTURE_CLI`).
