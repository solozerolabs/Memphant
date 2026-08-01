# MemPhant Quickstart

**There is no dashboard.** MemPhant's surfaces are the REST API, the MCP server,
the CLI, the Python SDK, and the compiled file plane. A `web/` directory used to
hold a ten-route "launch surface"; every page in it rendered from a committed
JSON fixture, its Correct/Forget buttons were permanently disabled because
nothing ever set `MEMPHANT_API_BASE`, and it depicted supersession working in a
way the shipped product does not. It was deleted on 2026-07-31. This page is its
replacement: the same operations, against a real server.

Every command below runs against a server you started yourself. Nothing here is
a fixture.

## 1. Bring up Postgres and the server

```bash
docker compose up -d
python3 scripts/apply_memphant_migrations.py --database-url "$DATABASE_URL"
curl -fsS http://127.0.0.1:3000/v1/health
```

Provider-specific bootstrap and exposure checks are in
[`deployment/self-host.md`](deployment/self-host.md) and
[`deployment/byoc-supabase.md`](deployment/byoc-supabase.md):

```bash
cargo run -p memphant-cli -- db bootstrap-check --provider plain-postgres
```

## 2. Bind a context

Every call is scoped by the same five identifiers. Get them once from the
context-binding endpoint rather than inventing UUIDs:

```bash
export MEMPHANT_URL=http://127.0.0.1:3000
export MEMPHANT_API_KEY=replace-with-a-scoped-key

curl -fsS -X PUT "$MEMPHANT_URL/v1/context-bindings/my-client-ref" \
  -H "Authorization: Bearer $MEMPHANT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

The response carries `subject_id`, `scope_id`, `actor_id`, `agent_node_id`, and
`subject_generation`. Export them; the rest of this page assumes they are set.

## 3. Retain an episode

`POST /v1/episodes` stores the recoverable raw episode. Requires
`subject_id`, `scope_id`, `actor_id`, `agent_node_id`, `subject_generation`,
`source_ref`, `observed_at`, `payload`.

```bash
curl -fsS -X POST "$MEMPHANT_URL/v1/episodes" \
  -H "Authorization: Bearer $MEMPHANT_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"subject_id\":\"$SUBJECT_ID\",\"scope_id\":\"$SCOPE_ID\",
       \"actor_id\":\"$ACTOR_ID\",\"agent_node_id\":\"$AGENT_NODE_ID\",
       \"subject_generation\":$SUBJECT_GENERATION,
       \"source_ref\":\"chat:1\",\"observed_at\":\"2026-07-31T00:00:00Z\",
       \"payload\":{\"body\":\"The checkout service uses token v2.\"}}"
```

Units are compiled by the reflect stage, not synchronously by this call. Run
`POST /v1/reflect` (same five identifiers) to compile, or let the worker do it.

## 4. Recall

`POST /v1/recall` requires the five identifiers plus `query`. Optional:
`limit`, `budget_tokens`, `mode`, `include_beliefs`, `valid_at`,
`transaction_as_of`, `aggregation_window`.

```bash
curl -fsS -X POST "$MEMPHANT_URL/v1/recall" \
  -H "Authorization: Bearer $MEMPHANT_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"subject_id\":\"$SUBJECT_ID\",\"scope_id\":\"$SCOPE_ID\",
       \"actor_id\":\"$ACTOR_ID\",\"agent_node_id\":\"$AGENT_NODE_ID\",
       \"subject_generation\":$SUBJECT_GENERATION,
       \"query\":\"which token does checkout use?\"}"
```

The response carries the packed context, the cited units, and a `trace_id`.

## 5. Inspect what was retrieved and why

```bash
curl -fsS "$MEMPHANT_URL/v1/traces/$TRACE_ID"      -H "Authorization: Bearer $MEMPHANT_API_KEY"
curl -fsS "$MEMPHANT_URL/v1/scopes/$SCOPE_ID/memory"     -H "Authorization: Bearer $MEMPHANT_API_KEY"
curl -fsS "$MEMPHANT_URL/v1/scopes/$SCOPE_ID/projection" -H "Authorization: Bearer $MEMPHANT_API_KEY"
```

The trace records the candidate pool, the drops and their reasons, the policies
applied, and the citations. This is the honest version of what the deleted
"trace explorer" page pretended to show.

## 6. Correct and forget

Both require the five identifiers plus a `selector`; `correct` also takes
`correction`, `forget` also takes `reason`.

```bash
curl -fsS -X POST "$MEMPHANT_URL/v1/correct" \
  -H "Authorization: Bearer $MEMPHANT_API_KEY" -H "Content-Type: application/json" \
  -d "{\"subject_id\":\"$SUBJECT_ID\",\"scope_id\":\"$SCOPE_ID\",
       \"actor_id\":\"$ACTOR_ID\",\"agent_node_id\":\"$AGENT_NODE_ID\",
       \"subject_generation\":$SUBJECT_GENERATION,
       \"selector\":{\"unit_id\":\"$UNIT_ID\"},
       \"correction\":{\"body\":\"Checkout uses token v3.\"}}"

curl -fsS -X POST "$MEMPHANT_URL/v1/forget" \
  -H "Authorization: Bearer $MEMPHANT_API_KEY" -H "Content-Type: application/json" \
  -d "{\"subject_id\":\"$SUBJECT_ID\",\"scope_id\":\"$SCOPE_ID\",
       \"actor_id\":\"$ACTOR_ID\",\"agent_node_id\":\"$AGENT_NODE_ID\",
       \"subject_generation\":$SUBJECT_GENERATION,
       \"selector\":{\"unit_id\":\"$UNIT_ID\"},
       \"reason\":\"user request\"}"
```

**Honesty note.** Supersession is real in the state machine but is only fed when
something produces a fact key. On a corpus where no key is produced, a
correction records the new unit without superseding the old one. See
`docs/superpowers/plans/2026-07-31-one-plan.md` §7. The deleted web fixture
depicted this always working; it does not always work.

## 7. The file plane

For coding agents this, not HTTP, is the primary human surface: `memphant
compile` projects canonical memory into an editable Markdown tree and `memphant
sync` writes edits back with a dry-run plan by default. See
[the file-plane quickstart in `README.md`](../README.md#file-plane-quickstart).

## Other surfaces

| Surface | Where |
|---|---|
| REST schema | `openapi/memphant.v1.json` |
| MCP tools | `mcp/memphant.tools.v1.json` |
| CLI | `cargo run -p memphant-cli -- --help` |
| Python SDK | `bindings/python/memphant/`, example in `bindings/python/examples/roundtrip.py` |
| Eval harness | `cargo run -p memphant-eval -- --help`, specs in `examples/evals/` |
| Self-host | `docs/deployment/self-host.md` |
