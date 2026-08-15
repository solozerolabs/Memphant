# MemPhant - API, SDK, and MCP Spec

## 0. Contract Rule

One public contract. REST, SDK, CLI, and MCP call the same operations.

## 1. Core Operations

```text
retain(input) -> RetainResult
recall(query) -> RecallResult
reflect(scope) -> ReflectResult
correct(selector, correction) -> CorrectResult
forget(selector) -> ForgetResult
trace(retrieval_id) -> RetrievalTrace
mark(trace_id, outcomes) -> MarkResult
```

No synonyms in the first public contract. The lifecycle concept is semantic revision; the public operation is `correct`.

`mark` (R77) is outcome feedback — a distinct job from `trace` (inspection): `mark(trace_id, {used_ids[], outcome: success|failure|corrected|ignored})` reports what the caller did with a recall pack. It is the sole producer of the trace's `outcome_label` (`05` §3.1) and writes graded `review_event` rows (`04` §8.2). Fire-and-forget semantics: idempotent per `(trace_id, caller)`, never blocks the caller, and a missing/expired trace_id is a silent no-op (`202`), not an error — feedback must be free to send. Outcome labels are tenant data (`06` privacy rules apply).

**Batch `retain` is all-or-error.** A batch write either durably lands every item or fails with an itemized `invalid_request`; a partial batch failure must never silently drop items (the Mem0 #5245 class — partial embedding failure silently losing memories). Per-item embedding/extraction failures downstream are recoverable by design (the raw episode is durable; `02` §3.0/§5.2); it is the *durable capture* that is atomic.

## 2. REST Sketch

```text
POST /v1/episodes
POST /v1/recall
POST /v1/reflect
POST /v1/correct
POST /v1/forget
POST /v1/mark
GET  /v1/traces/{id}
GET  /v1/events?cursor=...   # consolidation-event poll cursor (reserved-with-shape, `20` §3; build post-v1)
GET  /v1/scopes/{id}/memory
GET  /v1/scopes/{id}/stats   # read-only, gate-respecting per-scope stats (R91)
GET  /v1/scopes/{id}/block   # the pinned scope block, current version (04 §12)
PUT  /v1/scopes/{id}/block   # edit = INSERT version+1 (append-only, audited); trusted actors only
GET  /v1/health
```

All write endpoints require tenant/auth context. Read endpoints require tenant/auth unless explicitly running a local no-auth dev server.

**`GET /v1/scopes/{id}/stats` (R91)** returns, for the resolved scope (Stage-0 gates apply): unit counts by `kind × state`, episode count + `retention_tier` distribution, `consolidation_lag`, storage bytes (hot/warm/cold), quarantined count, open deletion generations, and the pinned block's version/token usage. Read-only; counts only, never content; consumers: the `19` §5 inspector scope overview, hosted quota UX (`21`), and integrator dashboards. Metric names align with `20`/`22`.

OpenAPI is canonical. SDKs and MCP schemas are generated or checked against the OpenAPI/JSON Schema definitions. A self-hosted server may serve that document at `GET /v1/openapi.json` for discovery, but that introspection route is not itself a generated SDK/MCP operation.

## 2.1 Error Envelope

```json
{
  "error": {
    "code": "scope_denied",
    "message": "Actor is not allowed to recall memory for this scope.",
    "request_id": "req_...",
    "details": {
      "scope_id": "scp_...",
      "policy_version": "pol_..."
    }
  }
}
```

Stable error catalog (HTTP status × retryable × MCP mapping). The same `code` strings are reused across REST and MCP so a client handles one vocabulary; in MCP they surface as `isError: true` with the code in `structuredContent.error.code` (a tool-level error, **not** a protocol error):

| `code` | HTTP | Retryable? | Meaning |
|---|---|---|---|
| `auth_required` | 401 | no (re-auth) | missing/invalid credential |
| `tenant_denied` | 403 | no | credential valid, wrong tenant |
| `scope_denied` | 403 | no | actor cannot access this scope |
| `policy_denied` | 403 | no | memory policy blocked the selector |
| `not_found` | 404 | no | unknown id |
| `conflict` | 409 | no | state conflict (e.g. already deleted) |
| `idempotency_conflict` | 409 | no | same `Idempotency-Key`, different body |
| `invalid_request` | 422 | no | schema/validation failure (`details` lists fields) |
| `rate_limited` | 429 | yes (after `Retry-After`) | tier quota exceeded (`15` §rate-limits) |
| `consolidation_lagged` | 200 + `degraded:true` | n/a | recall served in degraded mode; units un-extracted (`02` §3.1) |
| `backend_unavailable` | 503 | yes (backoff) | transient store/provider failure |

`consolidation_lagged` is **not** an HTTP error — recall still returns results, but the body carries `degraded: true` and the trace carries `consolidation_lag` so the caller knows the answer drew on raw episodes rather than consolidated units. Do not leak raw memory content in error messages.

## 3. Recall Request

```json
{
  "tenant_id": "tn_...",
  "subject_id": "sub_...",
  "scope": {"type": "project", "external_ref": "project:checkout-redesign"},
  "agent_node": {"id": "agent_...", "level": 0},
  "query": "What did we learn from the failed checkout integration?",
  "memory_kinds": ["episodic", "semantic", "procedural", "resource", "preference"],
  "trust_policy": "default",
  "mode": "fast",
  "limit": 8
}
```

Tenant/subject may come from auth context in hosted mode. They are explicit in examples to make adapter responsibilities visible.

### 3.0 Recall Request Parameters

`query` + `scope` are the only required fields; every other has a safe default (an agent sending only `{query, scope}` gets trust-gated `fast` recall):

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — required | NL or verbatim; redact/hash by tenant policy |
| `scope` | `{type, external_ref}` | — required | composes `scope ∪ admitted ancestors` (`04` §11.1); never widens |
| `agent_node` | `{id, level}` | auth identity | **server-derived from the key; a client-supplied `id`/`level` is advisory and validated against the key's binding — a mismatch is `scope_denied`, never honored** (so a child cannot claim `level:0`; L1+ inherits no protected categories) |
| `kinds` | string[] | `[episodic,semantic,procedural,resource,preference]` | **`belief` excluded unless listed** (`05` §1.3). `preference` minted 2026-07-31 (`04` §13.2a); it is accepted on the `unit` retain payload, where an explicit `fact_key` is already mandatory — which is what makes its supersession reachable |
| `mode` | `fast`\|`balanced`\|`deep` | `fast` | `fast` may auto-escalate; `deep` never auto |
| `arg_risk` | `none`\|`high` | `none` | `high` ⇒ server hard-excludes `high_risk_arg:false` (`06` §4.2) |
| `transaction_as_of` | RFC3339 | request evaluation time | what the system knew at the task snapshot; future values are rejected (§3.1) |
| `valid_at` | RFC3339 | resolved `transaction_as_of` | represented-world time to resolve inside that knowledge snapshot (§3.1) |
| `include_superseded` | bool | `false` | include history; stays citable, no default priority |
| `include_quarantined` | bool | `false` | analyst/admin only; ignored (not errored) otherwise |
| `budget` | `{tokens?, max_items?}` | tier default | Stage-7 pack budget; over-budget → `dropped[]` |
| `limit` | int | `8` | hard cap after packing |
| `breadth` | `context`\|`search` | `context` | `search` widens episodic/temporal windows for user-facing memory search **through the same policy-gated path** — one recall chokepoint, two scoping modes, never a second endpoint (the Syndai-proven pattern) |
| `delta_since` | trace_id | — | **rung-gated** (`recall_delta_enabled`, R80): return only the diff vs the archived pack — new units, superseded units (with the replacing generation), freshness downgrades, and a **count-only** `no_longer_available` (never names or describes removed content — forgotten memory must not be reconstructable from diffs; deletion-completeness eval covers this path) |
| `include_pinned_block` | bool | `true` | opt-out of the `04` §12 pinned block in Stage-7 packing (R88); when included it is guaranteed-present under its own sub-budget, `inclusion_reason: pinned_block` |

Unknown fields → `invalid_request` (422). There is no free-text `filters` escape hatch — every selector is a named, schema-checked field.

### 3.1 Bitemporal Recall ("what did we know, and when was it true?")

The two-clock model (`04` §7.3) is only a contract if callers can select both
axes independently. They are **`recall` parameters, not a new verb**. The
runtime resolves both once per request, normalizes them to UTC, echoes the
resolved values plus `evaluated_at` in the response/trace, and applies the
half-open predicates before every channel's top-N. The transaction-time
predicate applies to **every** unit:

`transaction_from <= transaction_as_of < transaction_to`

The valid-time predicate applies **only to units that carry a represented-world
window** — `semantic`/`belief` facts (`04` §7.3, the sole bitemporal kinds).
Episodic, procedural, and resource units have null `valid_*` (`04` schema) and
are matched on the transaction axis alone; `valid_at` never drops them:

`valid_from <= valid_at < valid_to`

A null bound is open and treated as −∞ (lower) / +∞ (upper) on **both** axes.
This is load-bearing on the transaction axis: an *open generation* (current
belief) is exactly `transaction_to IS NULL` (`04` §7.3a), so `transaction_to`
resolves to +∞ and the default `transaction_as_of` = `evaluated_at` selects it —
without this, `transaction_as_of < NULL` would be unsatisfiable and drop every
active unit. Likewise a null `valid_from`/`valid_to` makes an open-ended window
always contain `valid_at`.

| Intent | Request | Semantics |
|---|---|---|
| Current truth (default) | both omitted | open knowledge at request evaluation time whose validity window contains that same instant |
| Point-in-time world state using current knowledge | `valid_at` only | what current knowledge says was true then, including retroactive corrections |
| Audit / replay | `transaction_as_of` only | what MemPhant knew at that snapshot, resolved at the same represented-world instant |
| Full bitemporal query | both fields | what MemPhant knew at `transaction_as_of` about the world at `valid_at` |
| Fact history | `include_superseded: true` | active + superseded units on the `supersedes` chain, ordered by `valid_from` |

`include_superseded: true` (Fact history) is the one mode that **relaxes both
temporal gates**: it walks the `supersedes` chain and returns every generation
in full — closed ones (`transaction_to` in the past) and those whose validity
window does not contain `valid_at` — that a point-in-time snapshot would exclude.
History traversal is not a snapshot query; only Stage-0 authorization and
permanent forgetting still apply.

There is deliberately no legacy single-timestamp-plus-clock-selector form: it
cannot express both axes and makes replay ambiguous. Historical recall **still applies
current Stage-0 authorization and permanent forgetting** — a forgotten unit is
gone from every snapshot. A `transaction_as_of` after the request evaluation
time is rejected (`invalid_request`, 422) — there is no knowledge snapshot for a
future instant. The write-side transaction timestamp is database-assigned;
clients cannot backdate writes.

## 4. Recall Result

```json
{
  "retrieval_id": "ret_...",
  "items": [
    {
      "id": "mem_...",
      "kind": "semantic",
      "summary": "Checkout failed because the callback token was not signed for the dispatch surface.",
      "citation": "memphant://memory/mem_...",
      "source_refs": ["episode:ep_..."],
      "trust": "trusted_system",
      "score": 0.87,
      "eligibility": {"high_risk_arg": true, "citable_fact": true, "reason": null}
    }
  ],
  "context": "compact context block",
  "resolved_as_of": {
    "transaction_as_of": "2026-07-14T12:00:00Z",
    "valid_at": "2026-07-14T12:00:00Z",
    "evaluated_at": "2026-07-14T12:00:00Z"
  },
  "dropped": [{"id": "mem_...", "reason": "budget"}],
  "warnings": [{"type": "contradiction", "between": ["mem_a", "mem_b"]}],
  "degraded": false,
  "consolidation_lag_ms": 0,
  "trace_ref": "trace_..."
}
```

`resolved_as_of` echoes the two axes the runtime resolved for this request (`transaction_as_of`, `valid_at`) plus `evaluated_at` (the request evaluation instant): an omitted `transaction_as_of` defaults to `evaluated_at`, and an omitted `valid_at` defaults to the resolved `transaction_as_of` — so a caller and an archived trace read back exactly which bitemporal snapshot produced the result (§3.1).

`degraded: true` + a non-zero `consolidation_lag_ms` mean recall fell back to raw-episode/lexical retrieval because units were un-extracted (`02` §3.1) — the answer is honest about being stale rather than silently missing.

Every item carries a typed `eligibility` label (`high_risk_arg`, `citable_fact`, `reason`) — the enforceable suppression contract (`06` §4.2). `high_risk_arg` is computed (trust tier + corroboration), not advisory; a recall request may pass `arg_risk: high` to have MemPhant **hard-exclude** ineligible items server-side (they appear in `dropped[]` with `reason: trust`). There is no "raw" recall that returns trust-blind items.

`warnings[]` exposes the **typed contradiction/causal edges** touching the returned set (`contradicts`, `depends_on`, `same_subject`) so the calling agent can do implicit-conflict reasoning (the ActMem hook, `06` §10) — MemPhant surfaces the signal; the agent decides whether to act on it.

### 4.1 Citation Shape

```json
{
  "memory_unit_id": "mem_...",
  "citation_id": "cit_...",
  "episode_id": "ep_...",
  "resource_id": "res_...",
  "span": {"start": 120, "end": 188},
  "quote_hash": "sha256:...",
  "trust": "trusted_system",
  "validity": {"valid_from": "2026-06-01T00:00:00Z", "valid_to": null}
}
```

Citation payloads can omit snippets when tenant policy redacts content. IDs and hashes remain.

### 4.2 Worked Examples for the Other Verbs

Every verb gets a worked request → response so the contract is unambiguous (not just `recall`).

**`retain`** — `POST /v1/episodes` with exactly one episode, resource, or
direct-unit payload shape. Episode/resource writes return fast and consolidate
asynchronously; a trusted direct unit is admitted synchronously through the
same reflect policy.

```jsonc
// request
{ "scope": {"type": "project", "external_ref": "project:checkout"},
  "actor": {"id": "agent_7", "kind": "agent"},
  "source_kind": "tool", "source_trust": "verified_tool",
  "body": "npm test failed: callback token unsigned for dispatch surface",
  "idempotency_key": "evt_9f1c" }
// response 202
{ "episode_id": "ep_5a2", "retention_tier": "hot",
  "dedup": {"matched": false, "observation_count": 1},
  "enqueued": ["extract_episode"], "trace_ref": "trace_w_..." }
```

**`retain` has three mutually exclusive payload shapes on one endpoint:**
`episode` (default raw body → ground truth, async extract), `resource`
(`resource:{uri,mime,content_hash,...}` → registered resource, extractor
FSM runs; `04` §6.1), and trusted direct `unit`
(`unit:{kind,body,subject,predicate,churn_class?,valid_from?,valid_to?}` →
synchronous policy-checked reflect). Direct validity bounds are RFC3339 and
half-open; if both are present `valid_from < valid_to`. They are accepted only
for the bitemporal kinds that carry a represented-world window (`kind` in
`semantic`/`belief`, `04` §7.3); supplying `valid_*` with any other `kind` is
rejected (`invalid_request`, 422). They describe represented-world time
only—transaction time is assigned by the server and is never caller-settable. **Trust hints are advisory:** `source_trust` in the
request is capped by authenticated provenance (`06` §3.2/§2.2), and the
response echoes the assigned trust. **Batch:** `POST /v1/episodes:batch`
(`{episodes:[...]}`, ≤ tier cap) is the streaming-ingest path, all-or-nothing
per `Idempotency-Key`, with per-item idempotency for fine-grained replay.

**`reflect`** — `POST /v1/reflect`. Triggers/awaits consolidation for a scope; returns what it did (the §9 `04` contract made observable).

```jsonc
// request
{ "scope": {"type": "project", "external_ref": "project:checkout"}, "wait": false }
// response 200
{ "reflect_id": "rfl_88", "episodes_consumed": 12, "candidates_created": 5,
  "contradictions": [{"between": ["mem_a","mem_b"], "resolved": "newer_valid_from"}],
  "promotions": [{"unit": "mem_c", "from": "belief", "to": "semantic",
                  "corroborating_sources": [{"actor":"user_1","source_kind":"user"},
                                            {"actor":"tool_3","source_kind":"verified_tool"}],
                  "independent": true}],
  "trace_ref": "trace_r_..." }
```

**`correct`** — `POST /v1/correct`. First-class semantic revision; supersedes/invalidates, never silent overwrite.

```jsonc
// request — valid_from/valid_to are OPTIONAL; present ⇒ retroactive validity correction (04 §3.2/§7.3a)
{ "selector": {"memory_unit_id": "mem_old"},
  "correction": {"value": "Callback token v2 is current.", "reason": "stale_fact",
                 "valid_from": "2026-05-01T00:00:00Z" } }   // omit for a "true now" supersede
// response 200
{ "correction_id": "cor_31", "superseded": ["mem_old"], "created": ["mem_new"],
  "correction_kind": "retroactive",   // "current" when no valid_* given; created[0].validity echoes the window
  "edges": [{"kind": "supersedes", "from": "mem_new", "to": "mem_old"}],
  "trace_ref": "trace_c_..." }
```

`correct` is **append-only** (`04` §7.3a): it closes `mem_old`'s open generation and INSERTs `mem_new` — the supersedes-chain response is the application-time view of those two physical rows, so the public shape is unchanged. A retroactive correction does **not** rewrite already-emitted citations of `mem_old` (they remain reproducible with the archived trace's `transaction_as_of` and `valid_at`).

**Critical updates are read-back-confirmed, never fire-and-forget.** `correct` and `forget` confirm the new state by re-reading it before returning — the `superseded`/`created`/`invalidated` sets in the response are the *read-back*, not an optimistic echo. This closes the lost-write failure (Letta/MemGPT #689: a corrected fact reverts on the next turn because the write was never actually applied) — a write that didn't land is detected and surfaced as an error, not silently dropped. The mutation is a deterministic DB op (not an LLM tool-call the model can forget to make), so the confirmation is structural.

**`forget`** — `POST /v1/forget`. Security path; reports the policy applied.

```jsonc
// request
{ "selector": {"scope": {"type": "project", "external_ref": "project:checkout"}},
  "reason": "user_request" }
// response 200
{ "deletion_generation": 4412, "policy": "hard_delete",
  "invalidated": {"units": 37, "embeddings": 41, "edges": 22, "resources": 3, "blobs": 12},
  "verification": "no_recall_path_returns_forgotten", "trace_ref": "trace_f_..." }
```

**`trace`** — `GET /v1/traces/{id}`. Returns the full `retrieval_trace` (`05` §3.1) including `filter_selectivity` / `consolidation_lag`; large traces are a resource ref, not an inline dump.

## 5. MCP Tools

The MCP surface is the **five identity-free coding-agent tools** (R91 — the portable coding-agent lane, distinct from the seven REST verbs §4). The server derives tenant, subject, actor, scope, node, generation, trust, and reporter identity from the live bound key, so no tool carries a caller-supplied principal:

| Tool | Purpose |
|---|---|
| `recall` | Retrieve cited memory evidence (compact coding lane; `compact_only`). |
| `remember` | Store one compact coding memory as an Active unit. |
| `correct_memory` | Supersede a memory unit with an auditable bitemporal successor. |
| `invalidate_memory` | Close a memory unit's identity (`stale`/`harmful`) via an open tombstone. |
| `report_memory_use` | Report what the caller did with a recall pack (outcome feedback). |

MCP clients should get compact text and structured JSON. Large traces are returned as resources or trace references.

**Erasure has no MCP tool.** Permanent forget stays an owner-only HTTP/CLI path gated on the key's `can_forget` capability; history audit (as-of reads) is gated on `can_audit_history`. A coding-agent key carries neither. The REST surface keeps all seven verbs (§4); the MCP surface deliberately omits `retain`/`reflect`/`forget`/`trace` and folds outcome feedback into `report_memory_use` — identity-free tools an agent cannot misuse to widen its own scope. The general-lane `degraded:true`/`consolidation_lagged` recall contract (§4.2) is unchanged and shared.

### 5.1 MCP Tool Contract

Every MCP tool defines:

```text
name
description
inputSchema
outputSchema
content[]
structuredContent
isError
resource refs for large artifacts
```

Example `recall` output:

```json
{
  "content": [
    {"type": "text", "text": "Found 3 cited memories for checkout callback failure."}
  ],
  "structuredContent": {
    "state": "hit",
    "items": [],
    "trace_id": "ret_...",
    "trace_ref": "memphant://trace/ret_..."
  }
}
```

MCP is intentionally smaller than REST. It exposes agent-useful jobs, not admin inventory endpoints. **Implementation note (rmcp 2.x — R74):** the `rmcp` Rust SDK derives `inputSchema` (via `schemars`) but **not** `outputSchema` from the `#[tool]` macro alone. The canonical path is returning the **`Json<T>` wrapper** on the canonical response type — the framework then derives `outputSchema` from `T` and places the value in `structuredContent`, satisfying "do not hand-author parallel schemas" with no ceremony. The explicit `Tool::with_output_schema<T>()` where `T: JsonSchema` remains the fallback for tools whose return type cannot be the plain `Json<T>` wrapper (`02` §7). Target rmcp 2.x (2.0.0 aligned model types to MCP 2025-11-25).

**`outputSchema` is a published validation contract → response shapes are frozen-additive-only.** Because clients validate `structuredContent` against the declared `outputSchema`, adding a *required* output field post-tag is a breaking change; every field a future version might need must already exist (optional is fine) or the shape must be `additionalProperties`-tolerant. Get the response shapes (§4/§4.2) additive-safe **before the first tag**. **Tool annotations must be serialized explicitly**, because the MCP defaults are the wrong way for a memory store: `idempotentHint` defaults to `false` (so the mutating tools `remember`/`correct_memory`/`invalidate_memory`/`report_memory_use` must emit `idempotentHint: true`) and `destructiveHint` defaults to `true` (so the non-destructive writes must emit `destructiveHint: false`); `recall` carries `readOnlyHint: true`. Silence ships the wrong hint.

**Reserved-additive surfaces (named now so the later extension is coherent; NOT built at launch — and deferring is correct, since the 2026-07 stateless MCP RC moves *away* from held-open server push):** the **memory-event taxonomy is now reserved WITH shape** (R78; the shapes live in `20` §3: `memory.promoted`/`memory.superseded`/`memory.contradiction_detected`/`memory.quarantined`/`reflect.completed`) — delivery design is a **transactional outbox** written in the same commit as the trust-event/generation write, consumed via the `GET /v1/events?cursor` poll surface first; webhooks and any MCP `subscriptions/listen`/reverse-DNS extension (`ai.memphant/memory-events`) come later, built post-v1; server-driven `elicitation` for forget-confirm / contradiction-resolve (today HITL rides `destructiveHint`-driven client confirmation); memory-as-listable MCP `resources` + RFC 6570 templates. `reflect` sets `execution.taskSupport: forbidden` at launch, and its `reflect_id` is kept forward-compatible with a future MCP `tasks/get` handle so adopting the Tasks extension later is non-breaking.

### 5.1a File-Memory Compatibility Adapter (`memory_20250818`)

The 2026 de-facto agent-memory interface converged on **file operations, not a shared memory schema**: Anthropic's `memory` tool (`memory_20250818`, GA, no beta header) issues six client-side commands (`view`, `create`, `str_replace`, `insert`, `delete`, `rename`) against a `/memories` path prefix the *client's handler* maps onto real storage. Claude Code auto memory uses one concise `MEMORY.md` index plus sibling topic files; the first 200 loaded index lines or 25 KiB, whichever comes first, are the startup bound. MemPhant ships **one adapter to that convention** (R79 — deliberately not the rejected wide framework-adapter matrix, `26` §7; this is a platform convention, not a framework):

- The adapter projects the typed store as a **virtual text filesystem**: `/memories/MEMORY.md` is a deterministic generated index and bounded text-file paths are editable bodies. The default Claude Code shape remains Markdown topic files, while the GA six-command surface also supports bounded nested text paths, directory listing, atomic directory move, and recursive governed delete. Reads (`view`) render the canonical projection; writes (`create`/`str_replace`/`insert`) become one digest-bound `file_sync` retain/correct plan; `delete` becomes governed forget; `rename` is one atomic forget+retain plan. The same B2 unit IDs, body hashes, fixed-point snapshot, admission control, trust/provenance/policy gates, and serializable commit path remain authoritative. Binary/image files are not a canonical memory type and fail closed.
- `MEMORY.md` is read-only because it is derived; writable paths use bounded traversal-safe visible ASCII components. Default text views truncate the file text at 16,000 Unicode characters before line-number rendering; ranged views page the bounded topic body. The handler rejects `..`, encoded traversal, backslashes, query/fragment suffixes, hidden or `node_modules` components, file/directory collisions, oversized topics, ambiguous replacement text, stale snapshots, and writes without a fully resolved subject/scope/actor/agent/generation binding.
- The projection is **lossy by design in one direction only**: file-agents get durable, governed, multi-tenant memory for free; the typed surface (kinds, trust, bitemporality, citations) remains fully available through the native verbs. The adapter never bypasses Stage-0 gates or invariant #4.
- This is the answer to the local-first wedge (MemPalace/EverOS-class demand) without a second store: a Claude/OpenAI file-memory agent points its handler at MemPhant and inherits the substrate.
- The rejected B1 scope-block renderer remains dormant; B3 does not smuggle it back as a special `pinned.md` authority.

### 5.1b Harness Memory-Provider Adapters (Hermes first — R87, specced at an activation gate)

The second platform convention after file memory is the **harness memory-provider SPI**: Hermes Agent (207.8k★) ships a named provider slot with eight extant implementations — six of them MemPhant's mapped competitors (`13` §1.4). A provider adapter is NOT the rejected wide framework matrix (`26` §7): it is one thin adapter per *platform convention*, the same R79 rule that admitted `memory_20250818`.

- **Why below the tool layer:** §4.2's determinism principle ("a deterministic DB op, not an LLM tool-call the model can forget to make") applies to CAPTURE too — `01`'s durable-episode promise cannot be delivered by a tool the model must choose to invoke; a provider hook wired into the harness's auto-capture/auto-recall path can. Hermes built a provider SPI *despite MCP existing* — revealed preference by the largest harness that memory integration belongs below the tool layer.
- **Shape:** a thin mapping of the harness's provider calls onto the seven public verbs. Harness writes enter at `source_kind`-derived trust (harness/agent output is never `trusted_user` by default); every read passes Stage-0 gates; recall returns the standard evidence pack the harness renders. No MemPhant policy is delegated to the harness.
- **Timing:** specced now, **built at an activation gate** (first Hermes design partner or the launch window) — and deliberately NOT a frozen interface: the SPI belongs to Hermes and can drift; an unbuilt adapter has zero retrofit cost. OpenClaw has no provider plug point and is a recorded non-target (`13` §1.4).
- **Direction rule (`26`):** storage SPIs *below* MemPhant remain rejected; provider adapters *above* — MemPhant-as-provider — are this lane.

### 5.2 MCP Transports

| Transport | Use |
|---|---|
| stdio | local agent tools, CLI-launched sessions |
| Streamable HTTP | hosted/self-host MCP server |

Both use the same tool schemas and auth policy. **SSE is not a MemPhant launch transport** — new deployments expose stdio + Streamable HTTP only.

### 5.3 Tool Annotations and Resource URIs

**Annotations are part of each tool's contract** (MCP `ToolAnnotations`: `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`). Hints, not enforcement (server policy still gates) — a well-behaved client uses them to decide auto-call vs. confirm:

| Tool | readOnly | destructive | idempotent | Rationale |
|---|---|---|---|---|
| `recall` | true | — | — | pure read; safe to auto-call |
| `remember` | false | false | true (with key) | additive compact write |
| `correct_memory` | false | **false** | true | supersedes, never overwrites — old unit stays citable, so **not** destructive |
| `invalidate_memory` | false | **false** | true | closes an identity with an auditable tombstone; the closed unit stays citable, so **not** destructive |
| `report_memory_use` | false | false | true (per trace) | additive feedback write; safe to auto-call |

No MCP tool sets `destructiveHint: true`: irreversible erasure has no MCP tool (owner-only HTTP/CLI, gated on `can_forget`). `openWorldHint` stays false for all five — none fetches a server-side resource; resource-target ingest is REST `retain` only (`06` §3.1).

**The `memphant://` URI scheme is a frozen contract**: `memphant://memory/{unit_id}`, `memphant://trace/{retrieval_id}`, `memphant://episode/{episode_id}`, and `memphant://resource/{resource_id}`. MCP declares `resources: {}`, lists current projected memory units in deterministic pages of at most 100, advertises all four RFC 6570 templates, and resolves each URI through `resources/read`. Reads are text-only and capped at 64 KiB; larger content returns a typed bound error instead of silently truncating or leaking through another route. These URIs are **stable identifiers, not capability grants** — listing and reading require a fully context-bound API key, and every resolution reuses the tenant/scope/actor policy context. Resource access is read-only; mutations stay on the governed tool/file-sync routes.

MCP roots and sampling remain active client capabilities in the 2025-11-25 specification. B3 requests neither because this server needs only tools and resources. Only sampling's `includeContext` values are soft-deprecated; the roots capability itself is not deprecated.

## 6. SDKs

Python:

```python
from memphant import MemPhant

client = MemPhant(api_key="...", base_url="...")
items = client.recall(query="checkout failure", scope={"type": "project", "ref": "p1"})
```

TypeScript:

```ts
import { MemPhant } from "@memphant/sdk";

const client = new MemPhant({ apiKey: process.env.MEMPHANT_API_KEY });
const result = await client.recall({ query: "checkout failure", scope: { type: "project", ref: "p1" } });
```

Rust:

```rust
use memphant_core::RecallRequest;
```

Rust crate docs are the source of truth for native embedding.

**Generated vs hand-written:** transport + types are generated from OpenAPI; the seven verb methods + ergonomics (scope shorthand, env-var auth, cursor iterators) are a **thin hand-written wrapper**. The hand surface is deliberately tiny — one method per verb, no client-side query builder, no caching, no synonyms. If a method isn't one of the seven verbs (`retain`/`recall`/`reflect`/`correct`/`forget`/`trace`/`mark`), it doesn't exist in the SDK.

**Error → exception mapping** (catch by class, not by string-matching `code`): `auth_required`/`*_denied` → `MemPhantAuthError`; `not_found` → `MemPhantNotFound`; `conflict`/`idempotency_conflict` → `MemPhantConflict`; `invalid_request` → `MemPhantValidationError` (`.fields`); `rate_limited` → `MemPhantRateLimited` (`.retry_after`); `backend_unavailable` → `MemPhantUnavailable`. Retryable errors are retried by the generated client with backoff *before* the exception surfaces (the exception means retries were exhausted). `consolidation_lagged` is **never an exception** — it's a success result with `degraded:true`. MCP clients get the same `code` in `structuredContent.error.code` with `isError:true` — one vocabulary across SDK exceptions and MCP tool errors.

## 6.1 CLI

```bash
memphant retain --scope project:p1 --file transcript.jsonl
memphant recall --scope project:p1 "checkout callback failure"
memphant trace ret_123 --format json
memphant correct --memory mem_123 --value "Callback token v2 is current." --reason stale_fact
memphant forget --memory mem_123 --reason user_request
memphant eval run examples/evals/golden.yaml
memphant db lint --url "$DATABASE_URL"
memphant lock --out memphant.lock      # pin engine/trace/schema/methodology versions
memphant verify --lock memphant.lock   # exit 0 if live versions match the lock, nonzero on drift
```

CLI JSON output is schema-stable and snapshot-tested. Human output can change.

`memphant.lock` pins `engine_version` / `trace_schema_version` / `schema_revision` / `methodology_version`; `memphant verify` is a CI-friendly drift gate (exit 0 = match, nonzero = drift), the analog of EvalRank's `evalrank.lock`/`verify`.

## 7. Versioning

- API version is path-based: `/v1`.
- Trace schema has its own `trace_schema_version`.
- Retrieval engine has `engine_version`.
- The consolidation pipeline (the "memory compiler" that compiles raw episodes into derived units via `reflect`) has `compiler_version`. This is distinct from the *skill/procedure* compiler, which is rejected for v1 (`04` §4).
- Embedding profiles carry model, dimension, and `index_strategy`.
- Methodology has `methodology_version` (`24` refinement registry).
- Schema carries a migration head **and a `schema_compat_revision` boot-floor** (`25` §11b); `memphant.lock` pins both, and `memphant verify` fails when the live floor exceeds the binary's head (a too-old binary against a contracted schema) and warns on a deprecated-but-not-yet-contracted shape.

Before public launch, breaking changes are allowed when they simplify the contract. After public launch, breaking changes require a versioned API path and release note.

## 8. Idempotency and Pagination

- Mutating endpoints accept `Idempotency-Key`.
- Replays with the same key and body return the original result.
- Replays with same key and different body return `idempotency_conflict`.
- **Per-verb replay** (the verbs differ): `retain` returns the original `episode_id`/`dedup` (dedup is itself idempotent); `reflect` is naturally idempotent (stages converge); `correct` keyed-replay returns the original `correction_id`, but an unkeyed re-`correct` of an already-superseded unit returns `conflict` (409), not a second supersession; `forget` keyed-replay returns the original `deletion_generation`, and an unkeyed re-`forget` of already-forgotten content is a **success no-op** (returns the prior generation), never `conflict` — deletion is convergent and at-least-once delivery must not be punished.
- **`Idempotency-Key` retention is bounded + scoped, and that window is public contract** (clients build retry logic against it, so it is pinned, not implementation-defined): a key is honored for **24h** (echoed as `Idempotency-Key-TTL` on the response), scoped to **`(tenant, verb, key)`**. `idempotency_conflict` can fire **only within** the window; a replay *after* expiry is treated as a **fresh request**, never a `409`.
- List endpoints use opaque cursors.
- Cursors encode tenant/scope constraints and expire.
- Page responses include `next_cursor`, `has_more`, and `request_id`.
- **Recall answers; it never enumerates** (a deliberate, frozen split): `recall` is packed/budget-bounded (`05` §1.2) and does **not** paginate — items beyond `limit`/`budget` appear in `dropped[]`; the caller raises `budget` or narrows `query`. **Enumeration is a separate surface:** `GET /v1/scopes/{id}/memory?cursor=…` is the cursor-paginated list/inspector path (`next_cursor`/`has_more`), and `trace` is the other large case (`GET /v1/traces/{id}/candidates?cursor=…`). **No SSE/streaming recall** — streaming belongs to the answering model, not the substrate.

## 9. Auth Surface

| Context | Auth |
|---|---|
| hosted REST | API key or OAuth/session token |
| server-to-server Syndai | scoped service credential stored server-side |
| MCP local stdio | local config token or no-auth dev mode |
| MCP hosted | bearer token or gateway-injected identity |
| Python native local | local dev credentials |

Service/admin keys are never sent to browser/mobile clients or MCP tools.
