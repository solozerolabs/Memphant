# Portable bidirectional coding-agent memory

**Status:** design approved in conversation; implementation not started

**Date:** 2026-08-14
**Supersedes:** the product conclusions, not the evidence, in
`docs/flows/coding-agent-memory-mcp.md` that kept coding-agent MCP recall-only
and closed the write/lifecycle work after one voluntary-pull screen.

## 1. Outcome

MemPhant is a portable, governed experience layer for coding agents. Codex,
Claude Code, OpenCode, Cursor, Syndai, and other clients use the same public
contract to retrieve and write durable experience without depending on
Syndai, a particular transcript store, or a second retrieval database.

The product goal is narrower than general agent improvement:

- reduce tokens and unnecessary work;
- improve the accuracy of decisions that prior experience can inform;
- keep current code authoritative in repository files, Git, `rg`, and LSP;
- keep every recalled memory inspectable, correctable, suppressible, and
  attributable to evidence;
- remain useful when the original host or raw source is unavailable.

A useful memory changes an applicable decision or avoids unnecessary work. A
memory is not truth, a validator result, or an instruction to skip checking the
current repository.

## 2. Decisions

1. PostgreSQL remains the single authority. Markdown and agent-memory files
   remain projections.
2. Every recallable compact memory is self-contained. External raw evidence is
   optional enrichment, never a runtime dependency.
3. There is no host-versus-agent memory role. A host is another client that may
   automate calls to the same service layer.
4. The default coding-agent MCP surface contains five non-deleting tools:
   `recall`, `remember`, `correct_memory`, `invalidate_memory`, and
   `report_memory_use`.
5. Permanent `forget`, bulk ingestion, export, and administration do not exist
   on the coding-agent MCP server. They remain owner operations through the
   authenticated HTTP, SDK, CLI, or user interface surfaces. They require an
   explicit `can_forget` key capability that defaults to false and is never
   granted to a coding-agent credential.
6. Historical/as-of audit is also an owner operation. It requires a separate
   `can_audit_history` key capability that defaults to false and is never
   granted to a coding-agent credential; normal recall cannot use time-travel
   inputs to recover excluded versions.
7. Memories are typed fallible experience. No LLM, validator, agent, or human
   approval queue promotes them to truth.
8. An LLM may condense bounded evidence, select a memory kind, and propose the
   narrowest useful applicability. It does not certify correctness.
9. Bitemporal history applies to every durable memory. Corrections append a
   successor and close the prior interval; they never overwrite history.
10. Stale, harmful, superseded, expired, and deleted versions are hard-excluded
   before every normal retrieval path. Ranking cannot resurrect them.
11. Hot working state is runtime/client-owned and temporary. Durable current
    compact memories remain one continuously ranked corpus; there are no
    warm/cold serving states or stores.
12. Coding-agent recall returns zero or one compact memory within a fixed
    512-token response budget. A follow-up uses another, narrower query rather
    than expanding one response.
13. Default retrieval is provider-free. An LLM call is not required merely to
    choose a memory.
14. Coding-agent file/resource projections are read-only. Durable writes use
    the five intent tools; projection delete never maps to permanent forget.

## 3. System boundary

```text
Codex / Claude / OpenCode / Cursor / Syndai / other client
                         |
                  five MCP tools
                         |
                  MemPhant service
                         |
          PostgreSQL authority + native indexes
                         |
       compact memory + bitemporal history + provenance

Optional raw source: repository, host transcript store, object URI, docs/KB
```

MemPhant owns the durable compact memory, its lifecycle, its retrieval state,
and enough provenance to understand and verify it. A source owner may retain a
large transcript or document elsewhere. If a later agent cannot access that
source, the compact memory remains useful and the unavailable hydration is
reported honestly.

Syndai is one optional adapter. It may capture bounded evidence, trigger recall
at a coding boundary, and report an outcome, but it neither owns MemPhant's
memory model nor supplies a private retrieval path.

## 4. Memory model

### 4.1 Memory kinds

`memory` is the umbrella. The durable unit remains typed:

| Kind | Meaning | Typical source |
| --- | --- | --- |
| episodic | what happened, including a failure or decision | coding task, incident, repository history |
| semantic | a durable fact or architectural intent | ADR, KB, cross-repository fact |
| procedural | how to perform or avoid something | learning, successful fix, workflow |
| resource | versioned external knowledge | official docs, internal docs, KB page |
| preference | an explicit user-declared constraint | direct instruction or correction |
| belief | an explicitly uncertain hypothesis | rare; never included by default |

Chat, documents, KBs, repository history, and coding turns are sources, not
additional memory kinds. A time-varying fact is a semantic memory with valid
time, not a separate temporal kind. Outcomes are evidence about use, not
normally content shown to an agent.

### 4.2 Compact memory envelope

Every recallable memory has:

- `kind`;
- a concise, self-contained `body`;
- placement in the existing hierarchical scope tree: file, directory/module,
  repository, workspace, or an authorized cross-repository ancestor;
- a `trigger` describing when the experience matters;
- `verification` describing how the receiving agent should check current
  reality;
- provenance: source kind, reference, observed time, content hash, and an
  optional bounded excerpt;
- valid-time bounds;
- transaction-time history;
- lifecycle status and reason;
- stable lineage linking corrections and source evidence.

The rendered compact unit must fit the normal 512-token recall envelope. Longer
material is evidence, not a compact recall unit, until condensed.
Recallability also requires an explicit compact-envelope marker in the existing
unit payload. Copying a retained episode or resource body into an `Active` unit
does not make it compact or portable.

### 4.3 Authorization versus applicability

Authorization and semantic placement are different decisions but do not need
different storage models. Reuse the existing hierarchical `scope` rows
(`kind`, `external_ref`, `parent_scope_id`, and `materialized_path`) plus
`scope_policy`:

- the bound principal determines which scope tree it may read or mutate;
- `remember` names an existing target scope within that authorized tree;
- the coding agent or condensation model chooses the narrowest useful target,
  analogous to choosing the appropriate directory for an `AGENTS.md` or
  `LEARNINGS.md` entry;
- the server resolves the scope and enforces containment/policy before writing.

Read and write authorization are distinct. A different target scope requires
an owner-created `scope_policy` grant with explicit write permission for the
memory kind; the server derives the target agent node from that grant. A read
grant never authorizes placement. Cross-scope placement also requires a
canonical retained episode/resource source resolved in the bound context; a
resource ACL must authorize the target. Free-form source references are
informational and remain confined to the bound scope.

There is no second applicability table, taxonomy, or free-form authority field.
A repository-bound caller cannot write to a workspace ancestor. A workspace-
authorized caller may choose an allowed descendant repository/module scope or
an owner-created shared scope for genuinely cross-repository experience. The
caller may choose only among preexisting scopes and grants; it cannot create a
shared scope or grant merely because it can read two repositories. Until a
source resource's ACL participates in compact-memory eligibility, evidence
from an ACL-bearing source cannot be condensed or placed into a broader scope.

### 4.4 Lifecycle

Normal recall admits only the current version. Historical versions remain
queryable through an explicit audit/as-of surface.

```text
current --correct--> superseded + corrected current successor
current --invalidate(stale)--> superseded + Invalidated tombstone(stale)
current --invalidate(harmful)--> superseded + Invalidated tombstone(harmful)
current --valid_to elapsed--> expired archive
current --owner forget(can_forget)--> content erasure + deletion tombstone
```

Invalidation is an append-only bitemporal transition. Atomically close the
prior current row as `Superseded` by setting its transaction-time end, then
append a current, non-recallable `UnitState::Invalidated` tombstone with the
stable fact/source identity, lineage, `stale` or `harmful`, reason, source, and
observation time. Historical as-of queries still see the prior row during its
former transaction interval; normal recall sees neither it nor the tombstone.

Every creation path, including `remember`, reflect, file sync, and source
replay, rejects a new current unit while an open invalidation tombstone exists
for the same stable identity or exact compact-body digest. This is an exact
lineage/content guarantee, not a claim that deterministic code recognizes
every semantic paraphrase. Only `correct_memory` may select that tombstone,
close it, and atomically create a new `Active` successor. This is the sole
ordinary path by which corrected evidence can restore that identity.

A corrected successor has new bytes, its own provenance, and explicit lineage
to the superseded unit. Record `stale` or `harmful` plus the reason on the
invalidation tombstone rather than adding two states with identical serving
behavior.

The following may never re-enable an archived version:

- decay or reinforcement;
- vector re-embedding;
- reranking;
- fallback or degraded recall;
- source replay or re-ingestion;
- process restart;
- graph or edge traversal.

## 5. Coding-agent MCP

The MCP server is an agent experience surface, not a mirror of every service
verb.

### 5.1 `recall`

Retrieve cited compact experience for a query.

Input is caller-supplied task/query text, including any useful paths, symbols,
errors, source URIs, commit IDs, or boundary context. Identity and bounds are
service-derived.

The v1 coding-agent MCP input remains exactly `{query}`. ANALYZE, REPRODUCE,
EDIT, and VERIFY describe integration timing, not a public enum. A plugin that
knows the boundary includes the useful task context in the natural-language
query. A typed stage field is considered only if a later isolated comparison
shows that it changes relevant retrieval.

All coding-agent recall fixes the limit at one and the total rendered budget at
512 tokens. The result remains the existing honest typed envelope: hit, empty,
unavailable, or error. A hit carries provenance and a trace reference.

### 5.2 `remember`

Create exactly one self-contained typed memory. The agent supplies the compact
envelope, target scope, valid time, and source reference it actually knows. The
server derives tenant identity, transaction time, trust ceiling, stable keys,
and hashes after resolving that scope against policy.

The write creates `UnitState::Active`, including for procedural memory. The
coding tool does not mint `Candidate` or require `Validated`. Normal recall and
canonical projection must admit active procedural experience; no new “current”
state is introduced.

`remember` is not a raw transcript upload or a bulk import operation. An agent
that has read a large source writes the compact experience it wants future
agents to receive. Bulk or automatic source capture uses the ordinary
HTTP/SDK/CLI ingestion surface and the same service layer.

### 5.3 `correct_memory`

Select one current memory and create one corrected successor. The operation is
atomic and idempotent: close the selected transaction/valid interval as
appropriate, write the successor, connect explicit lineage, and prevent the
old version from normal recall.

The successor carries the correction's own source reference, hash/excerpt, and
observation time. Changed bytes never inherit the old unit's contextual chunks
or citations. Those are cleared or rebuilt from the correction source; any
bitemporal remainder that preserves the old bytes preserves the old evidence.

### 5.4 `invalidate_memory`

Select one current memory and mark it `stale` or `harmful`, with a reason,
source reference, and observation time. It creates no recallable replacement.
The status is enforced as an eligibility exclusion across all retrieval
channels.

The prior row is transaction-closed as `Superseded`; the appended current
tombstone uses the existing `Invalidated` state. `Stale` and `harmful` are
typed reasons on that tombstone and its mutation receipt.

This operation is available to coding agents because it is auditable and
reversible only through a new corrected memory; it does not erase history.

### 5.5 `report_memory_use`

Report which recalled units were used and whether the caller associated them
with success, failure, correction, or ignoring. Reports influence continuous
ranking. They never establish truth and never by themselves hard-invalidate a
memory; explicit `invalidate_memory` is required for that. Reporting is
optional and never blocks the coding task. The service derives reporter
identity from the live key ID and accepts at most one report for a given
trace/principal pair.

### 5.6 Not MCP tools

- Individual memories and traces remain inspectable through MCP resources and
  citations, so separate get/list/trace tools are unnecessary.
- The Anthropic/Claude file-memory projection is read-only on the coding-agent
  server. Its current create/edit/delete bridge is retired there; otherwise a
  projection delete could still invoke permanent `forget` outside the five-
  tool contract.
- Evidence retention and consolidation remain service/HTTP/SDK/CLI operations.
- `reflect` is background/internal processing, not an agent task verb.
- `forget`, bulk delete, entity delete, reset, export, and key administration
  remain owner surfaces. The coding-agent MCP router does not register them.

An agent therefore cannot discover `forget`, and a crafted tool call has no
registered handler. Owner authentication and deletion policy remain enforced
again on the administrative surface.

Removing the tool is only the first boundary. Every permanent-deletion entry
point must require an API key with `can_forget = true`; the capability defaults
to false and coding-agent credentials are never provisioned with it. This is a
single operation capability, not a host/agent role hierarchy. Even an agent
with shell access and its own bearer key therefore cannot bypass MCP with
`curl`.

The same rule applies to history without conflating it with deletion:
historical/as-of recall requires `can_audit_history = true`. Coding-agent keys
never receive it, the MCP recall contract exposes no time-travel input, and the
HTTP/service boundary rejects historical selectors before retrieval when the
live key lacks that capability. Owner audit can inspect superseded or
invalidated history, but erasure remains final: deleted content is unavailable
even to an authorized historical read.

Owner forget means erasure, not only lifecycle suppression. It removes the
selected MemPhant-held compact or source body, excerpt, citation payload,
embedding, derived chunks, blob/cache copies, and writable projections, while
retaining only a content-free deletion tombstone and audit receipt. Deleted
content is unavailable to historical as-of reads. Forgetting a compact unit
does not erase a separately retained source unless that source is also in the
owner's deletion selection, but the tombstone prevents rederiving the deleted
identity from replay.

All five tools and MCP resource reads use one shared per-call bound-context
resolver. It re-looks up the API key, checks revocation and every bound identity
field, resolves the requested target scope where applicable, clamps trust, and
returns the canonical context. No mutation accepts caller-supplied tenant,
subject, actor, agent-node, generation, authorization scope, or reporter as
identity. The resolver requires a fully bound, unrevoked coding key. Correction
and invalidation reject a target above the live key's trust ceiling, and every
successor's trust is clamped to that ceiling.

## 6. Retrieval and delivery

### 6.1 Retrieval inputs

MemPhant does not infer hidden application state. The agent, plugin, or host
supplies a task query with the repository identifiers and boundary context it
actually knows. MemPhant decides which eligible memory kinds and scopes best
match that query. Memory kind is a ranking signal, not a hard gate.

ANALYZE, REPRODUCE, EDIT, and VERIFY remain delivery boundaries. Their query
content naturally differs: intent and ADRs during analysis, failures and
episodes during reproduction, procedures and resources during editing, and
prior checks or failures during verification. This does not require a public
stage field or four retrieval modes.

### 6.2 Eligibility before ranking

The retrieval pipeline first enforces:

1. tenant and principal authorization;
2. current transaction-time visibility;
3. valid-time applicability;
4. hard exclusion of stale, harmful, superseded, expired, deleted, and policy-
   denied units;
5. explicit-belief opt-in.

Every bounded exact, lexical, vector, temporal, edge, deep, and degraded query
applies lifecycle eligibility before its cursor or `LIMIT`; otherwise an
invalidated candidate can consume a top-N slot and hide a valid memory. Core
filtering remains as defense in depth before fusion and packing. There is no
fallback that searches archived units.

Normal portable recall also has no raw-episode degraded fallback. If a retained
episode has not produced an eligible compact memory, recall returns the typed
`unavailable`/processing state instead of exposing an uncited or unbudgeted raw
episode. Direct `remember` provides immediate read-your-write behavior for a
compact unit.

### 6.3 Ranking

The default provider-free query combines the existing native signals:

- exact identifiers and stable keys;
- PostgreSQL lexical search;
- existing pgvector similarity when an embedding exists;
- valid-time and recency;
- applicability distance;
- memory-kind policy and query relevance;
- prior use, correction, ignored, and failure evidence;
- provenance/trust and contradiction state;
- relational lineage and source edges.

All eligible current compact memories are searched together and ranked with
continuous signals. A useful recent memory may score higher, while a strongly
relevant old memory can still win. Do not persist or compute a warm/cold tier.
MemPhant already deleted `episode.retention_tier` after finding zero readers,
zero writers, and 8,147 of 8,147 rows permanently left at the default `hot`.
Add a cache only after a measured latency or cost failure.

### 6.4 Delivery

- The first product slice must include one concrete Codex or Claude integration
  that performs automatic recall at an actual ANALYZE, REPRODUCE, EDIT, or
  VERIFY boundary.
- Zero or one compact card is injected. Honest empty is normal.
- The coding agent may issue a narrower follow-up `recall` query.
- Full-source hydration is explicit and occurs only after a compact card or
  citation identifies the source.
- If the source is unavailable to this caller, MemPhant returns that state and
  keeps the self-contained card usable.

The previous no-call experiment is evidence against relying exclusively on
voluntary tool discovery. It is not evidence against the write lifecycle or
host/plugin boundary delivery.

## 7. Capture and condensation

Raw coding sessions and chat transcripts are not ingested by default. They are
high-volume, privacy-sensitive evidence and previously produced noisy memory
paths.

The bounded default is:

- explicit `remember` by a coding agent; or
- at task completion or an explicit correction, a client or its integration
  may nominate at most one compact candidate from bounded evidence.

A condensation model may summarize only the evidence provided, choose kind and
target scope, and emit verification guidance. It must preserve source hashes
and uncertainty. It does not approve, validate, or promote the result. A
candidate that cannot be made self-contained within the compact budget is not
automatically recallable.

## 8. Storage substrates

### 8.1 Keep

- PostgreSQL tables as the only durable authority.
- Existing PostgreSQL lexical, vector, temporal, and relational-edge
  capabilities.
- Bitemporal rows and append-only correction/invalidation lineage.
- Bounded evidence excerpts and content hashes in PostgreSQL.
- Markdown/file projections for inspection and native agent compatibility.

### 8.2 Optional external source storage

Large raw bodies may remain in their owning system: a repository, Codex or
Claude history, Syndai storage, an object URI, or a documentation provider.
MemPhant stores a stable reference and hash but never assumes every caller can
hydrate it.

### 8.3 Reject for now

- a separate graph database;
- a separate vector database;
- a second memory cache database;
- an object store required for compact-card recall;
- current-code indexing that competes with Git, `rg`, or LSP.

An alternate physical store is considered only after PostgreSQL misses a
measured corpus-size, latency, or cost objective. Identical returned bytes do
not justify a model-call comparison between stores.

## 9. Existing code: keep, change, delete

### Keep

- the query-only recall path's principal binding and per-call revalidation;
- typed recall hit/empty/unavailable/error results;
- provider-free fast recall and the 512-token bounded response;
- `RetainPayload::{Episode, Resource, Unit}` inside the service/HTTP ingestion
  layer;
- correction rectangles, forget tombstone, feedback, trace, provenance,
  temporal, edge, `UnitState::Active`, `UnitState::Invalidated`, and
  idempotency primitives;
- PostgreSQL provider portability and file projection machinery.

### Change

- replace the seven backend-oriented coding-agent MCP verbs with the five
  intent-oriented tools;
- extract one shared per-call bound-context resolver and derive every mutation
  identity from it just as query-only recall now does;
- require an explicit `can_forget` capability on every permanent-deletion
  surface; it defaults false and is never present on coding-agent keys;
- require a separate `can_audit_history` capability on every historical/as-of
  read surface; it defaults false and is never present on coding-agent keys;
- make owner forget erase selected MemPhant-held content and derivatives while
  retaining only a content-free tombstone/receipt;
- project semantic placement onto the existing hierarchical scope and
  `scope_policy` model rather than adding applicability storage;
- restrict cross-repository placement to owner-created shared scopes with
  explicit grants, and reject ACL-bearing source promotion until its ACL is
  enforced by compact eligibility;
- make coding-agent memories immediately eligible as fallible current
  `Active` experience rather than requiring an unreachable `Validated` writer;
- admit active procedural memory in recall and canonical projections;
- represent correction and stale/harmful invalidation as distinct public
  intents;
- give correction successors fresh provenance and never clone citations or
  contextual chunks onto changed bytes;
- enforce lifecycle exclusion inside every bounded store query before its
  cursor/limit, then again defensively in core;
- replace in-place invalidation with a transaction-closed prior row plus an
  open non-recallable tombstone that blocks every creation/replay path until a
  correction closes it;
- remove raw-episode degraded fallback from normal portable recall;
- preserve the query-only MCP input while allowing an integration to describe
  its current boundary in the query;
- keep continuous access/outcome/decay ranking rather than restoring tiers.

### Remove from the coding-agent MCP router

- `retain`;
- `reflect`;
- `correct`;
- `forget`;
- `trace`;
- `mark`.

Also retire the writable `memory_20250818` file-command bridge from the coding-
agent server. Read-only projections and MCP resources remain.

Their necessary service primitives remain and are called by the new tools or
other public surfaces. `recall` remains query-only and principal-derived.

## 10. Security, privacy, and failure behavior

- Every read and mutation remains tenant-bound and principal-derived.
- A caller cannot broaden its access by choosing a broader target scope.
- Coding-agent credentials never carry permanent-delete authority: no delete
  tool exists on that server, and the same key lacks the `can_forget` capability
  required by every deletion endpoint.
- Coding-agent credentials cannot time-travel around lifecycle exclusion: they
  lack `can_audit_history`, and historical/as-of selectors are rejected before
  retrieval on every public surface.
- Access to two repositories does not confer permission to write or read a
  shared cross-repository memory; an owner-created scope and explicit grants do.
- Raw prompts, shell commands, patches, and full transcripts are not required
  by the compact-memory API.
- All mutations are idempotent and audit-linked.
- Every MCP tool call and resource read resolves one fully bound live principal;
  none accepts caller-supplied identity fields, and no operation may exceed the
  live key's trust ceiling.
- An open invalidation tombstone blocks direct writes, replay, and promotion for
  the same memory identity until an explicit correction closes it.
- Source authorization can narrow compact-memory eligibility but never broaden
  it; owner erasure overrides bitemporal history.
- Normal recall never returns raw source bodies or pending consolidation bytes.
- Backend failure is `unavailable`, never an empty result.
- Missing optional source hydration is explicit and does not discard the card.
- Stale/harmful exclusion is fail-closed across primary and degraded recall.
- User/owner inspection, history, correction, invalidation, and deletion remain
  available without an approval queue.

## 11. Peer API lessons

- Zep reduced its Memory MCP server from eight tools to three in June 2026:
  `search_graph`, `get_user_summary`, and `add_memory`. Borrow the small agent
  surface, not Zep's graph/database architecture.
- Mem0 exposes add, search, get/list, update, delete, bulk delete, entity delete,
  entities, and event tools. Borrow simple save/search wording; reject the
  destructive and administrative default surface.
- LangMem separates search from a combined manage-memory tool. Borrow the
  small namespace-aware interface; keep correction and invalidation separate
  because MemPhant has stronger lifecycle requirements.
- Current Letta uses git-backed MemFS and separates small always-in-context
  system memory from files loaded on demand. Borrow the compact-versus-on-demand
  UX boundary, not its file authority or agent runtime.
- Graphiti's experimental MCP exposes add/search plus episode deletion and
  graph clearing. Borrow temporal provenance concepts; reject destructive
  coding-agent tools and the additional graph substrate.
- Cognee's current MCP likewise keeps the core surface to `remember`, `recall`,
  and `forget`, with additional tools discoverable on demand. Borrow the small
  listed surface; reject agent-visible permanent deletion and the graph/LLM
  substrate cost.

Sources:

- <https://help.getzep.com/changelog/2026/6/29>
- <https://help.getzep.com/memory-mcp-server/connect>
- <https://docs.mem0.ai/platform/mem0-mcp>
- <https://langchain-ai.github.io/langmem/reference/tools/>
- <https://docs.letta.com/guides/get-started/intro>
- <https://help.getzep.com/graphiti/getting-started/mcp-server>
- <https://docs.cognee.ai/cognee-mcp/mcp-tools>

## 12. Rejected approaches

### Syndai or another host as the raw-source authority

Rejected. It makes portable memories unusable for agents without that host.
Hosts may retain optional raw sources, but recallable compact memory belongs to
MemPhant.

### Host and agent authorization profiles

Rejected. The same actor may ingest, distill, retrieve, and correct. The real
security boundary is coding-agent MCP versus owner administration.

### Expose all service verbs through MCP

Rejected. It leaks backend workflow into agent choice, increases tool-selection
cost, and exposes destructive operations. MCP tools represent agent intents.

### One generic mutation tool

Rejected. Create, correct, invalidate, feedback, and delete have different
consequences. The first four merit explicit intent; delete remains off MCP.

### Validator or approval-based promotion

Rejected. Prior experiments overfit to validators, approval adds user friction,
and neither makes experience universally true. Provenance, bitemporal history,
verification guidance, feedback, correction, and hard invalidation provide the
governance.

### Separate graph/vector/cache databases

Rejected until PostgreSQL fails a measured requirement. They add cost and
operational authority without changing the logical memory unit.

## 13. Design invariants

The implementation is unacceptable if any of these statements is false:

1. A fresh Codex or Claude client can remember, recall, correct, invalidate,
   and report usage without Syndai.
2. A recalled card remains actionable and verifiable when its raw-source host
   is unavailable.
3. A coding-agent client cannot discover permanent deletion through MCP or
   invoke it through any surface with the same credential.
4. Correcting a memory preserves the old bitemporal version and serves only the
   applicable successor.
5. Invalidating a memory as stale or harmful excludes it from every normal
   retrieval channel and fallback while preserving the prior transaction-time
   history.
6. Re-ingestion, reranking, decay, re-embedding, restart, or graph traversal
   cannot resurrect an invalidated identity while its tombstone is open.
7. Automatic recall is provider-free, returns at most one card, and never
   exceeds the fixed response budget.
8. Current compact memories remain one continuously ranked PostgreSQL corpus
   and one authority, with no serving tier.
9. Current repository facts are verified through native coding tools rather
   than trusted from memory.
10. No result from this work is described as general coding-agent improvement
    without separate outcome evidence.
11. Owner forget erases selected MemPhant-held content and derivatives; only a
    content-free tombstone and receipt remain.
12. Normal recall never substitutes raw retained episodes for missing compact
    memory.

These invariants are product and contract checks, not an efficacy validator.
Whether memory improves a coding task remains a bounded natural-task judgment,
not a synthetic score or promotion gate.

## 14. Sequencing constraints

Implementation planning must preserve this dependency order:

1. canonical memory/lifecycle and principal-derived mutation contracts;
2. shared hard-exclusion and bitemporal behavior;
3. five-tool portable MCP surface;
4. provider-free query retrieval, boundary delivery, and compact packing;
5. standalone Codex/Claude read-write dogfood;
6. optional host-triggered boundary delivery;
7. one source lane at a time: procedural, semantic, repository history,
   versioned resource, explicit preference;
8. only then bounded automatic task-end nomination and condensation;
9. only after measured PostgreSQL limits, alternate physical substrates.

The prior voluntary-pull pair remains a valid negative result for that exact
mechanism/card/task. It is not a kill gate for the portable write lifecycle,
automatic boundary delivery, or other memory kinds.

## 15. Non-goals

- no general smarter-agent or SOTA claim;
- no current-code replacement for Git, `rg`, LSP, or repository files;
- no raw-every-turn transcript ingestion;
- no automatic inferred user preferences;
- no coding-agent hard delete;
- no human approval queue;
- no LLM truth judge;
- no external graph/vector database;
- no broad paired-test or validator campaign;
- no backwards-compatibility layer for the old MCP tool names in this
  pre-production repo.
