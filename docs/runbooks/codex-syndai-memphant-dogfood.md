# Runbook: dogfood MemPhant on real Codex/Syndai coding, then read adherence

Goal: find out whether a coding agent **voluntarily uses** the MCP memory on
real work — not whether a cold run "improves" (a cold recall is empty and equals
bare). The instrument is the existing Postgres audit trail; no PostHog/Axiom is
needed for v1.

## 0. One-time: dedicated coding subject (so usage is attributable)

Point all dogfood sessions at ONE coding subject/scope. Then
`scripts/mcp_usage_report.sql` isolates that agent's calls from everything else.

1. Bring up Postgres + server + worker and resolve a context binding
   (`PUT /v1/context-bindings/{client_ref}`) — see [`../quickstart.md`](../quickstart.md).
   Record the returned `subject_id`, `scope_id`, `actor_id`, `agent_node_id`,
   `subject_generation`.
2. Mint a coding key for that context (owner capabilities are off by default —
   `can_forget=false`, `can_audit_history=false`, exactly a coding-agent key):

   ```bash
   memphant-cli admin create-key --tenant "$TENANT_ID" \
     --subject-id "$SUBJECT_ID" --subject-generation "$GENERATION" \
     --scope "$SCOPE_ID" --actor "$ACTOR_ID" --agent-node "$AGENT_NODE_ID" \
     --database-url "$PROVISIONER_DATABASE_URL"
   ```

   The plaintext key prints once. Export it as `MEMPHANT_API_KEY`.

## 1. Wire the MCP into Codex

Build the binary and add the stdio server to the Syndai project's
`.codex/config.toml` exactly as in the [README](../../README.md#codex-plugin-automatic-one-card-delivery)
("For Codex, use a trusted project `.codex/config.toml`"). Forward env-var
names, never values. Keep `enabled_tools` to what you're testing — start with
`["recall"]`, widen to include `remember`/`correct_memory`/`report_memory_use`
once recall usage shows up.

Confirm the wiring with `codex mcp list` and `codex mcp get memphant`. An honest
empty recall here proves connection + scope binding only.

## 2. Do real coding sessions

Use Codex on terra (`codex exec -m gpt-5.6-terra …`) for actual Syndai tasks.
Do not seed or script memory — the question is what the agent does on its own.

## 3. Read adherence

```bash
psql "$DATABASE_URL" \
  -v subject="'$SUBJECT_ID'" \
  -v since="'2026-08-15 00:00:00+00'" \
  -f scripts/mcp_usage_report.sql
```

Three tables come back: recall calls (served vs honest-empty), completed writes
by verb (`retain`=remember / `correct` / `invalidate`), and `report_memory_use`
outcomes.

## 4. Interpret — the honest ladder

- **recall `calls` = 0** → the agent never reached for memory. Stop here; the
  gap is adherence (it won't call the tool), not retrieval. No amount of recall
  quality matters until this is non-zero. This is the most likely first result
  (our OctoBench injection look was flat, +0.9pp, on a saturated frontier model).
- **`calls > 0` but all `honest_empty`** → it calls, but the scope has nothing
  yet. Expected early; accumulate more sessions, then re-read.
- **`served_ge1_candidate > 0` and non-zero writes/outcomes** → the loop is
  live: the agent recalls, uses, and contributes back. Only now is a seeded
  cross-session or measured A/B worth paying for.

Do not read a single session as a measurement. This is a usage signal, not a
quality claim; n=1 proves nothing.
