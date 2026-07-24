from __future__ import annotations

import os

from memphant import MemPhant


client = MemPhant(
    base_url=os.environ.get("MEMPHANT_BASE_URL", "http://127.0.0.1:3000"),
    api_key=os.environ.get("MEMPHANT_API_KEY"),
)

# Resolve external refs into a bound context (tenant is bound by the API key).
ctx = client.bind_context(
    client_ref=os.environ["MEMPHANT_CLIENT_REF"],
    subject_ref=os.environ["MEMPHANT_SUBJECT_REF"],
    subject_kind="user",
    actor_ref=os.environ["MEMPHANT_ACTOR_REF"],
    actor_kind="agent",
    scope_ref=os.environ["MEMPHANT_SCOPE_REF"],
    scope_kind="agent",
    agent_node_ref=os.environ["MEMPHANT_AGENT_NODE_REF"],
)

retained = client.retain_episode(
    ctx=ctx,
    source_ref="release-note:region",
    observed_at="2025-06-01T00:00:00Z",
    source_kind="user",
    body="Release region is Taipei.",
    idempotency_key="roundtrip-retain-region-v1",
)
client.reflect(ctx=ctx, idempotency_key="roundtrip-reflect-region-v1")
recalled = client.recall(ctx=ctx, query="Where is the release region?")

print({"retained": retained["episode_id"], "trace_id": recalled["trace_id"]})
