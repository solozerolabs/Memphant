# MemPhant

MemPhant is the Apache-2.0, Rust-first memory substrate for long-running agents. It stores recoverable episodes, compiles cited memory units, retrieves scoped evidence, and keeps poisoning, tenant isolation, correction, and forgetting inside one auditable contract.

This repository is the public product boundary. It owns the Rust crates, Postgres and pgvector schema, public API, MCP server, CLI, SDKs, eval harness, synthetic fixtures, docs, and self-host packaging. The hosted control plane, credentials, private corpora, and Syndai adapter stay outside this repository.

## Repository Split

- Public MemPhant: product code, public schemas, public tests, synthetic examples, provider bootstrap and lint code.
- Syndai adapter: private dogfood integration work stays outside this repo until a surface is generalized; see `porting.md`.
- No hidden hosted behavior: hosted MemPhant must run the same public binary self-hosters run.

## Current State

Build state is tracked in `docs/superpowers/specs/memphant/STATUS.md`. WS-0 has an exit artifact, and the R83 Rust-vs-Python two-language spike kept the Rust-first posture: warm no-recompile Rust policy iteration measured at `0.073x` Python.

## Quickstart

[`docs/quickstart.md`](docs/quickstart.md) walks the HTTP surface end to end:
bring up Postgres and the server, bind a context, retain, recall, read the
trace, correct, forget. There is no dashboard — the `web/` launch surface was a
committed fixture and was deleted on 2026-07-31.

## File-plane quickstart

The file plane is an editable projection; Postgres remains canonical. Start with
the UUIDs and subject generation returned by `PUT /v1/context-bindings/{client_ref}`:

```bash
export MEMPHANT_URL=http://127.0.0.1:8080
export MEMPHANT_API_KEY=replace-with-a-scoped-key
export SUBJECT_ID=00000000-0000-0000-0000-000000000001
export SCOPE_ID=00000000-0000-0000-0000-000000000002
export ACTOR_ID=00000000-0000-0000-0000-000000000003
export AGENT_NODE_ID=00000000-0000-0000-0000-000000000004
export SUBJECT_GENERATION=0
export MEMORY_DIR=./memory

# Pin the CLI/server binary contract used by verification.
memphant lock --out memphant.lock

# Compile the canonical snapshot. This refuses to overwrite local edits.
memphant compile --subject-id "$SUBJECT_ID" --scope "$SCOPE_ID" \
  --actor "$ACTOR_ID" --agent-node "$AGENT_NODE_ID" \
  --subject-generation "$SUBJECT_GENERATION" --out "$MEMORY_DIR"

# Edit units/*.md, add new semantic facts to inbox/*.md, or delete a unit file.
# Dry-run is the default: this prints the exact JSON plan and changes nothing.
memphant sync --subject-id "$SUBJECT_ID" --scope "$SCOPE_ID" \
  --actor "$ACTOR_ID" --agent-node "$AGENT_NODE_ID" \
  --subject-generation "$SUBJECT_GENERATION" --out "$MEMORY_DIR"

# After reviewing that plan, apply the same local tree atomically.
memphant sync --subject-id "$SUBJECT_ID" --scope "$SCOPE_ID" \
  --actor "$ACTOR_ID" --agent-node "$AGENT_NODE_ID" \
  --subject-generation "$SUBJECT_GENERATION" --out "$MEMORY_DIR" --apply

# Verify the refreshed projection against the pinned binary contract.
memphant verify --lock memphant.lock --export "$MEMORY_DIR"
```

`MEMPHANT_HTTP_TIMEOUT_MS` optionally sets the request timeout in milliseconds
(default `30000`, allowed `1..=300000`). When replacement is needed, compile and
apply preserve replaced managed files in a reported `.memphant-recovery-*`
directory; do not delete it until the refreshed projection verifies clean.

Automation may branch on these stable stderr classes:

| Class | Safe response |
| --- | --- |
| `compile=dirty`, `sync=invalid` | Inspect or restore the local projection; do not overwrite it. |
| `sync=conflict` | Recompile the latest canonical snapshot, then recreate and review the edit. |
| `sync=unavailable` | No commit was reported; preserve the tree and retry the same dry-run. |
| `sync=outcome_unknown` | The request may have committed; do not construct or apply a different plan until canonical state is checked. |
| `sync=post_commit_error remote_committed=true` | Canonical memory committed; preserve recovery files and recompile before editing again. |
| `compile=error`, `sync=error` | Fix the reported configuration or request error, then rerun dry-run. |

## Agent distribution surfaces

`memphant-mcp` serves the five portable coding-agent memory tools — `recall`,
`remember`, `correct_memory`, `invalidate_memory`, `report_memory_use` — and
read-only MCP resources on the same stdio or Streamable HTTP session. The tools
are identity-free: the server derives tenant, subject, actor, scope, node,
generation, trust, and reporter identity from the live bound key. Permanent
erasure has no MCP tool; it is an owner-only HTTP/CLI path gated on `can_forget`. Resource listing requires an API key
bound to tenant, subject generation, actor, scope, and agent node; it returns
opaque pages of at most 100 `memphant://memory/{unit_id}` entries. Known
`memory`, `episode`, `resource`, and `trace` URIs are readable through the
advertised templates, but never grant access outside that key binding.

Rust hosts integrating Anthropic's GA client-side memory tool use
`anthropic_memory_tool()` for the exact
`{"type":"memory_20250818","name":"memory"}` declaration and dispatch the
six decoded `MemoryCommand` variants to `MemphantMcp::handle_memory_command`.
The virtual root matches Claude Code auto memory:
`/memories/MEMORY.md` is a generated bounded index and Markdown topic files
are governed projections. The GA handler also accepts bounded nested text-file
paths and implements virtual directory listing, move, and recursive delete;
binary/image files are not part of the canonical memory projection. Postgres
remains authoritative; edits commit through the same atomic file-sync path as
the CLI.

### Codex plugin (automatic one-card delivery)

`plugins/codex-memphant/` bundles the same five-tool MCP server plus a
`UserPromptSubmit` hook. Run the MCP server in Streamable-HTTP mode
(`memphant-mcp streamable-http`) and point the plugin at it:

```bash
export MEMPHANT_MCP_URL="https://localhost:8787/mcp"
export MEMPHANT_API_KEY="mk_…"   # a fully-bound coding key; no can_forget
```

The `.mcp.json` bearer transport gives Codex the explicit tools; the hook
(`hooks/user_prompt_submit.py`, Python stdlib only) calls `recall` at the prompt
boundary and injects at most one 512-token card via `additionalContext`. On an
empty scope it injects nothing; on auth/unavailable/timeout it injects nothing
and logs a terse, secret-free code. **Plugin-bundled hooks are non-managed:
Codex skips them until you review and trust the hook once** — "automatic" means
"automatic after that one-time trust prompt." The hook never parses transcripts,
starts a second service, or touches Postgres directly. Erasure has no hook and
no MCP tool; it stays an owner-only HTTP/CLI path.

After resolving a context binding, mint the MCP principal with the complete
server-issued context (the plaintext key is printed once):

```bash
memphant-cli admin create-key --tenant "$TENANT_ID" \
  --subject-id "$SUBJECT_ID" --subject-generation "$GENERATION" \
  --scope "$SCOPE_ID" --actor "$ACTOR_ID" --agent-node "$AGENT_NODE_ID" \
  --database-url "$PROVISIONER_DATABASE_URL"
```

Build the server once, export the scoped key and database URLs in the shell
that starts the coding agent, and point the client at the absolute binary path:

```bash
cargo build -p memphant-mcp
export MEMPHANT_API_KEY=replace-with-the-fully-bound-key
export MEMPHANT_APP_DATABASE_URL=postgres://...
export MEMPHANT_AUTHN_DATABASE_URL=postgres://...
```

For Codex, use a trusted project `.codex/config.toml`. Forward environment
variable names; never commit their values:

```toml
[mcp_servers.memphant]
command = "/absolute/path/to/target/debug/memphant-mcp"
args = ["stdio"]
env_vars = ["MEMPHANT_API_KEY", "MEMPHANT_APP_DATABASE_URL", "MEMPHANT_AUTHN_DATABASE_URL"]
required = true
enabled_tools = ["recall"]
```

Verify the effective configuration with `codex mcp list` and
`codex mcp get memphant`. Remove the project block to disconnect it; use
`codex mcp remove memphant` only for a configuration added through the CLI.

Claude Code can keep the secret values out of its configuration by inheriting
the same shell environment:

```bash
claude mcp add --scope local memphant -- \
  /absolute/path/to/target/debug/memphant-mcp stdio
claude mcp get memphant
claude mcp list
```

Claude discovers the server's full governed tool set, so restrict mutations
with the client's permission policy when the intended integration is
read-only recall. Disconnect with `claude mcp remove --scope local memphant`.

An honest empty recall proves the connection and scope binding, not that an
agent will voluntarily call memory or that memory improves a coding task.
Keep native `rg`, LSP, and Git authoritative for the current codebase.

Regenerate committed MCP artifacts only through their owner:

```bash
cargo run -q -p memphant-mcp -- --list-tools-json
cargo run -q -p memphant-mcp -- --list-resources-json
```

## Local Checks

```bash
python3 -m pytest tests/test_repo_contract.py -q
python3 scripts/check_spec_drift.py
~/.cargo/bin/cargo metadata --format-version 1 --no-deps
```
