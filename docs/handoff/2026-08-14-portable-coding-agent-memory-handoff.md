# Portable coding-agent memory: implementation handoff

Current STATUS mirror: RUNTIME COMPLETE — BENCHMARK EVIDENCE PENDING

**Date:** 2026-08-14

**Repository:** MemPhant

**Worktree:** `/Users/sidsharma/Memphant/.worktrees/coding-agent-memory-mcp`

**Branch:** `codex/coding-agent-memory-mcp`

**Implementation baseline HEAD:** `2cbeccec9b0aff0279375867fb9b90e96719e7d6`

**Remote status:** local only; nothing in this workstream was pushed

**Next action:** implement Task 1 of the portable bidirectional flow, then stop

## 1. Executive judgment

MemPhant is not yet a complete coding-agent memory product. The branch has a
secure, provider-free, query-only MCP recall path and real PostgreSQL transport
proof. It does **not** yet have the portable agent write/lifecycle contract,
automatic task-boundary delivery, safe invalidation/no-resurrection, or true
owner erasure.

The previous natural Codex pair established that voluntary tool discovery is
not a viable default delivery mechanism: neither arm called recall. That does
not make the recall work wasted. It proved the secure read transport and exposed
the missing value chain. The approved next design closes that chain:

```text
agent remembers a compact memory
  -> PostgreSQL stores the governed current generation
  -> later agent gets at most one compact relevant memory automatically
  -> agent reports whether it used the memory
  -> agent can correct or invalidate it
  -> stale/harmful generations cannot return or resurrect
  -> an owner can audit history or permanently erase content
```

The implementation contract is
[`docs/flows/portable-bidirectional-coding-agent-memory.md`](../flows/portable-bidirectional-coding-agent-memory.md).
The reviewed design rationale is
[`docs/superpowers/specs/2026-08-14-portable-coding-agent-memory-design.md`](../superpowers/specs/2026-08-14-portable-coding-agent-memory-design.md).

## 2. Re-establish state before editing

Run these commands first:

```sh
cd /Users/sidsharma/Memphant/.worktrees/coding-agent-memory-mcp
git status --short
git branch --show-current
git rev-parse HEAD
python3 ~/.local/share/feature-flow/current/scripts/feature-flow-state.py get
```

Expected at handoff:

```text
branch: codex/coding-agent-memory-mcp
HEAD: 2cbeccec9b0aff0279375867fb9b90e96719e7d6
worktree: clean before this handoff document is committed
active feature: Portable bidirectional coding-agent memory
completed stages: understand, plan, review-plan
plan: docs/flows/portable-bidirectional-coding-agent-memory.md
```

There is also a completed historical flow at
[`docs/flows/outcome-coupled-evolution.md`](../flows/outcome-coupled-evolution.md).
It is evidence and regression history, not the next implementation plan. If a
session-level anchor names that older feature, verify the per-worktree Feature
Flow state above before acting. Do not reopen its validator-driven campaign.

Read, in order:

1. repository [`AGENTS.md`](../../AGENTS.md);
2. this handoff;
3. the portable implementation flow;
4. the portable design only when a rationale or rejected alternative is needed;
5. the exact code and callers named by the current task.

Do not start by editing README, STATUS, generated schemas, or the MCP router.
Those are later tasks and depend on lifecycle/security work landing first.

## 3. Authority model

Keep one durable control plane:

- PostgreSQL is the single memory authority.
- Markdown/file memory is a read-only projection, not a competing database.
- Git, repository files, `rg`, and LSP remain authoritative for current code.
- MemPhant stores experience that is not reliably recoverable from the current
  tree: procedures, incidents, historical intent, explicit preferences,
  versioned resources, semantic facts, and bounded episodes.
- Syndai is an optional client and source. MemPhant must work for Codex, Claude,
  and other MCP clients with no Syndai source tree, database, or credentials.

“Memory” is the umbrella over the six existing kinds at
[`crates/memphant-types/src/lib.rs:1058`](../../crates/memphant-types/src/lib.rs):

- `episodic`
- `semantic`
- `procedural`
- `resource`
- `preference`
- `belief`

Chat, external docs/KB, coding turns, repository history, and outcomes are
sources or evidence. They are not additional kinds. Bitemporality is the
lifecycle model, not another kind or storage substrate.

## 4. What is implemented and verified on this branch

### 4.1 Secure query-only MCP recall

The current MCP recall surface accepts exactly `{query}` at
[`crates/memphant-mcp/src/lib.rs:216`](../../crates/memphant-mcp/src/lib.rs).
Tenant, subject, generation, actor, scope, agent node, and trust are not supplied
by the agent.

The MCP process stores a bound startup principal at
[`crates/memphant-mcp/src/lib.rs:140`](../../crates/memphant-mcp/src/lib.rs), then
re-looks up the API key for every recall call. Revoked, missing, partially bound,
or drifted credentials fail closed. A lower live trust ceiling takes effect;
an increased ceiling requires process restart.

Recall is deliberately cheap and bounded:

- provider-free Fast mode;
- maximum one item;
- fixed 512-token budget at
  [`crates/memphant-mcp/src/lib.rs:212`](../../crates/memphant-mcp/src/lib.rs);
- no agent-controlled as-of time, identity, trust, top-k, Deep mode, or belief
  inclusion.

The 512-token value replaced an insufficient 128-token budget after a red-first
wire-contract test proved the old budget truncated a complete procedural card.
The product change is commit `40f2099a`.

### 4.2 Typed recall outcomes

The output union at
[`crates/memphant-mcp/src/lib.rs:241`](../../crates/memphant-mcp/src/lib.rs)
separates:

- `hit`: a complete recall response;
- `empty`: a successful search with no eligible memory;
- `unavailable`: a retryable backend/provider outage with redacted detail;
- `error`: a terminal auth, scope, policy, stale, erased, not-found, or invalid
  failure.

This distinction matters for UX. A backend outage must never be mislabeled as
“MemPhant knows nothing,” and terminal failures must not invite blind retries.

### 4.3 Real-process PostgreSQL proof

[`scripts/e2e_probe.sh`](../../scripts/e2e_probe.sh) provisions isolated scratch
contexts and exercises the real MCP binary against real PostgreSQL. The proof
covers:

- C0 exact empty recall;
- M1 exact unit, complete rendered body, verified citation, and trace linkage;
- provider-free recall;
- key revocation and principal drift;
- auth-store outage mapped to typed, redacted `unavailable`;
- cleanup through an ephemeral scratch database.

The detailed implementation record is
[`task-1-report.md`](../../.superpowers/sdd/coding-agent-memory-mcp/task-1-report.md).
The final focused recall commits are `601e2ce9`, `6b6d7ab3`, `b855859c`, and
`40f2099a`.

### 4.4 Existing reusable product primitives

Do not rebuild these:

- six memory kinds and lifecycle states:
  [`crates/memphant-types/src/lib.rs:1058`](../../crates/memphant-types/src/lib.rs)
  and [`:1088`](../../crates/memphant-types/src/lib.rs);
- current memory-unit types:
  [`crates/memphant-types/src/lib.rs:1303`](../../crates/memphant-types/src/lib.rs)
  and [`:1339`](../../crates/memphant-types/src/lib.rs);
- strict mutation ledger and verbs:
  [`crates/memphant-core/src/lib.rs:809`](../../crates/memphant-core/src/lib.rs);
- recall, trace, review/outcome, lineage, scope policy, canonical projection,
  and PostgreSQL vector support;
- service correction and forget entry points:
  [`crates/memphant-core/src/service.rs:4396`](../../crates/memphant-core/src/service.rs)
  and [`:4491`](../../crates/memphant-core/src/service.rs);
- MCP resources for bounded list/read inspection;
- read-only PostgreSQL-backed file projection generation.

Reuse these primitives. The next work is to make their policy coherent, not to
add another framework.

## 5. What the natural liveness pair established

The authoritative result is
[`task-2-final-report.md`](../../.superpowers/sdd/coding-agent-memory-mcp/task-2-final-report.md).

Exactly two fresh Codex CLI calls ran on Terra high: one bare C0 and one M1
scope containing one validated procedure card. Prompt, augmentation, MCP
discovery, configuration, model, and binary were held equal. Direct preflight
proved C0 empty and M1 able to return the intended complete card.

Neither coding trajectory called `recall` or any MCP/memory tool. Both agents
independently selected the same material implementation approach. Therefore:

- voluntary agentic pull is off by default;
- the chosen card/task pair demonstrated no marginal decision value;
- timing via automatic task-boundary delivery remains untested;
- no correctness, general-improvement, or SOTA claim is permitted;
- no paid replication or broader source/kind campaign should run yet.

M1 used less time, fewer commands, and fewer tokens, but without exposure those
differences cannot be attributed to memory. A post-run archive bug also made a
blind final-code correctness comparison impossible. It did not affect the
zero-call finding or transport parity.

The right response is not more paired validators. It is to build the missing
portable lifecycle and automatic delivery path, prove that path for $0, then
run one bounded natural pair only if the transport proof passes.

## 6. What remains unimplemented

At this handoff, the following are design/plan, not shipped behavior:

- agent `remember`, `correct_memory`, `invalidate_memory`, and
  `report_memory_use` intents;
- one live-principal resolver shared by every MCP tool and resource;
- explicit `can_forget` and `can_audit_history` key capabilities;
- explicit cross-scope write grants;
- direct compact `Active` memory writes;
- fresh correction provenance;
- bodyless stale/harmful invalidation tombstones;
- stable-identity and exact-body no-resurrection enforcement;
- actual whole-lineage owner erasure;
- one canonical normal-recall eligibility predicate applied before every SQL
  `LIMIT` and again before packing;
- removal of raw episodic fallback;
- exactly five coding MCP tools and read-only resources;
- native automatic Codex `UserPromptSubmit` delivery;
- standalone end-to-end lifecycle proof;
- final README, generated artifact, and STATUS parity.

Current code still exposes seven older tools and the writable file-memory
bridge:

- handler registrations begin around
  [`crates/memphant-mcp/src/lib.rs:554`](../../crates/memphant-mcp/src/lib.rs);
- `MemoryCommand` is at
  [`crates/memphant-mcp/src/file_memory.rs:33`](../../crates/memphant-mcp/src/file_memory.rs);
- its dispatcher is at
  [`crates/memphant-mcp/src/file_memory.rs:119`](../../crates/memphant-mcp/src/file_memory.rs).

The README describes that current legacy surface. Do not “fix” the docs ahead
of the product. Task 9 updates them after the real contract exists.

## 7. Final approved public coding-agent contract

The MCP should ultimately advertise exactly five tools:

```text
recall
remember
correct_memory
invalidate_memory
report_memory_use
```

The strict intent shapes are specified in
[`portable-bidirectional-coding-agent-memory.md:44`](../flows/portable-bidirectional-coding-agent-memory.md).
Key decisions:

- `recall` remains exactly `{query}`;
- mutations reuse the existing `McpMutation<T>` idempotency envelope;
- the service derives all identity, authority, transaction time, stable keys,
  and hashes;
- `target_scope_id` is optional applicability, not caller identity;
- cross-scope writes require an explicit write grant and canonical source/ACL;
- `forget` is not an agent tool;
- audit/history selectors are not agent fields;
- files/resources remain read-only inspection projections.

The agent may decide what memory kind and scope best fit, as it does with
repository learning files, but the server enforces authority. No human approval
is required for ordinary remember/correct/invalidate. Owners retain query,
inspection, audit, and permanent-erasure control.

## 8. Implementation order and rationale

Execute the nine tasks in
[`docs/flows/portable-bidirectional-coding-agent-memory.md:251`](../flows/portable-bidirectional-coding-agent-memory.md)
in order. Each task is one reviewable commit. Do not open a later task until the
current task's focused behavior passes.

### Task 1 — capabilities and one live principal resolver

Plan: [`flow:271`](../flows/portable-bidirectional-coding-agent-memory.md).

Add `can_forget`, `can_audit_history`, `scope_policy.allow_write`, and the
`invalidate` mutation-ledger verb through the existing migration/bootstrap,
store, runtime, CLI key-provisioning, server, and MCP layers. Replace the
recall-only split lookup with one shared per-call resolver for every future
tool/resource.

Why first: removing `forget` from MCP would be cosmetic while HTTP/CLI/curl can
still reach a deletion path without an explicit capability. Likewise, a write
tool is unsafe before the live principal and write authority are canonical.

Primary seams:

- API-key row: [`crates/memphant-core/src/lib.rs:1625`](../../crates/memphant-core/src/lib.rs)
- current MCP bound context: [`crates/memphant-mcp/src/lib.rs:140`](../../crates/memphant-mcp/src/lib.rs)
- current recall-only resolver: [`crates/memphant-mcp/src/lib.rs:467`](../../crates/memphant-mcp/src/lib.rs)
- PostgreSQL key lookup: [`crates/memphant-store-postgres/src/store.rs:5018`](../../crates/memphant-store-postgres/src/store.rs)
- server auth context: [`crates/memphant-server/src/lib.rs:144`](../../crates/memphant-server/src/lib.rs)
- bootstrap migration `scope_policy`, `mutation_ledger`, `api_key`, and
  `provision_api_key`: `memphant_migrations/versions/20260703_001_wsa_bootstrap.sql`
  around lines 218, 621, 733, and 1340.

Stop after Task 1 is focused-test green and committed. Do not proceed to Task 2
in the same opening session unless explicitly requested.

### Task 2 — compact agent intent types and direct Active writes

Plan: [`flow:343`](../flows/portable-bidirectional-coding-agent-memory.md).

Add identity-free DTOs and a narrow service path that resolves target scope,
derives stable identity/body digest, persists one compact `Active` unit, and
provides read-your-write plus mutation-ledger replay.

Why second: this creates the smallest real bidirectional product slice. It
reuses the existing table and `payload` JSONB; no new memory table or source
database is needed.

### Task 3 — correction, invalidation, and no resurrection

Plan: [`flow:423`](../flows/portable-bidirectional-coding-agent-memory.md).

Fix correction provenance, add bodyless stale/harmful tombstones, and route all
ingress through one shared open-tombstone check.

Why third: direct writes must exist before their lifecycle can be exercised,
but recall must not expand until harmful/stale content is guaranteed not to
sneak back through replay, compilation, vectors, or file sync.

Important current defects:

- correction staging begins at
  [`crates/memphant-store-postgres/src/store.rs:3381`](../../crates/memphant-store-postgres/src/store.rs)
  and currently copies citations around line 3500;
- compiled writes converge through service call sites around
  [`crates/memphant-core/src/service.rs:4066`](../../crates/memphant-core/src/service.rs)
  and `:5035`;
- store/core/runtime trait mappings must remain exhaustive.

### Task 4 — capability-gated whole-lineage owner erasure

Plan: [`flow:485`](../flows/portable-bidirectional-coding-agent-memory.md).

Extend the existing forget target and mutation ledger. Require `can_forget`,
then scrub selected content, all supersedes lineage, derived units, embeddings,
citations, chunks, excerpts, and payload copies in one transaction. Retain only
content-free tombstones and receipts.

Why fourth: an absent agent delete button is not a deletion security boundary,
and marking rows `Deleted` is not erasure. Historical audit must never recover
erased bytes.

Current seams:

- service: [`crates/memphant-core/src/service.rs:4491`](../../crates/memphant-core/src/service.rs)
- PostgreSQL: [`crates/memphant-store-postgres/src/store.rs:3577`](../../crates/memphant-store-postgres/src/store.rs)
- HTTP: [`crates/memphant-server/src/lib.rs:531`](../../crates/memphant-server/src/lib.rs)

### Task 5 — canonical eligibility and raw fallback retirement

Plan: [`flow:517`](../flows/portable-bidirectional-coding-agent-memory.md).

Create separate normal-recall and authorized-audit predicates. Apply normal
eligibility in every SQL candidate path before cursor/`LIMIT`, and again in
core before scoring/packing. Remove raw episodic fallback; return typed
`ConsolidationPending` only when raw source exists but no compact unit is ready.

Why fifth: the lifecycle states must be canonical before every retrieval path
can safely filter them. Filtering after `LIMIT` lets ineligible rows crowd out
eligible memory.

Current seams:

- core eligibility: [`crates/memphant-core/src/lib.rs:10578`](../../crates/memphant-core/src/lib.rs)
- lexical/exact candidates: [`crates/memphant-store-postgres/src/store.rs:2447`](../../crates/memphant-store-postgres/src/store.rs)
- vector candidates: [`crates/memphant-store-postgres/src/store.rs:2818`](../../crates/memphant-store-postgres/src/store.rs)
- service recall: [`crates/memphant-core/src/service.rs:4169`](../../crates/memphant-core/src/service.rs)
- internal recall: [`crates/memphant-core/src/service.rs:4207`](../../crates/memphant-core/src/service.rs)
- raw fallback helper: [`crates/memphant-core/src/service.rs:7237`](../../crates/memphant-core/src/service.rs)
- REST temporal selectors: [`crates/memphant-types/src/lib.rs:1974`](../../crates/memphant-types/src/lib.rs)

### Task 6 — replace the coding MCP router

Plan: [`flow:563`](../flows/portable-bidirectional-coding-agent-memory.md).

Register exactly the five intent tools, make all tools/resources use the shared
live principal, remove the old mutation handlers and writable `MemoryCommand`,
then regenerate MCP artifacts from the binary.

Why sixth: the public contract should change once its service and safety roots
exist, not before. No compatibility aliases are required in this pre-production
repo.

### Task 7 — automatic Codex boundary delivery

Plan: [`flow:596`](../flows/portable-bidirectional-coding-agent-memory.md).

Add one small Codex plugin using the native `UserPromptSubmit` hook. Its Python
stdlib client calls the same Streamable HTTP MCP endpoint and injects zero or
one already-packed card. It does not launch another server, parse transcripts,
or add a stage enum.

Why seventh: the natural pair proved voluntary recall is not a dependable UX.
Automatic delivery must reuse the completed secure lifecycle, stay bounded to
one 512-token card, and fail open to the coding task with a secret-free
diagnostic on memory outages.

### Task 8 — $0 standalone value-chain proof

Plan: [`flow:642`](../flows/portable-bidirectional-coding-agent-memory.md).

In scratch PostgreSQL, use real MCP/HTTP binaries to execute:

```text
remember -> recall -> correct -> recall -> invalidate -> recall
-> blocked replay -> report -> owner audit -> owner forget
```

Also prove C0 exact empty and M1 exactly one complete automatic card. Measure
cold process and warm service latency separately. Default automatic delivery
off if representative local p95 exceeds 1 second cold or 300 ms warm; optimize
the handshake/query before considering a cache.

Why eighth: paid usefulness work is meaningless until the actual write/read/
lifecycle/delivery chain works end to end without a model judge.

### Task 9 — docs, generated parity, and truthful ledger closure

Plan: [`flow:674`](../flows/portable-bidirectional-coding-agent-memory.md).

Update README, provider docs, generated artifacts, and STATUS only after Task 8
and the full repository harness pass. Update AGENTS only if implementation
reveals one concise, durable invariant missing from it.

Why last: documentation and STATUS must describe proven behavior, not the
approved design.

## 9. Bitemporal and stale/harmful guarantees

MemPhant has two time axes:

- **valid time:** when the memory was true in the world;
- **transaction time:** when MemPhant recorded each generation.

Normal coding recall serves only the current eligible compact generation.
Authorized owner audit may inspect a superseded predecessor only within its
half-open transaction interval. Audit never returns erased bytes.

Lifecycle transitions:

```text
Active compact
  -> correct
     predecessor closes as Superseded
     fresh Active successor gets fresh provenance

Active compact
  -> invalidate(stale|harmful)
     predecessor closes as Superseded
     current bodyless Invalidated tombstone blocks recall and replay

Invalidated tombstone
  -> remember/reflect/file sync/replay/re-embedding
     rejected for matching stable identity or exact body digest
  -> correct_memory
     tombstone closes; a fresh Active successor is allowed

Any lineage
  -> owner forget with can_forget
     all recoverable bytes and derived copies are scrubbed
```

Do not claim deterministic semantic-paraphrase detection. The enforceable
guarantee is stable identity plus exact compact-body digest. Ranking, decay,
use feedback, and model judgment may reduce semantic duplicates later, but they
must not be presented as an absolute safety barrier.

## 10. Storage and retrieval decisions

Keep:

- PostgreSQL tables and transactions as authority;
- pgvector only as one candidate channel inside the same authority;
- exact/lexical, vector, temporal, lineage/edge, and bounded deep channels;
- existing continuous decay/use signals;
- read-only Markdown/MCP resource projections.

Do not add now:

- graph database;
- separate vector database;
- warm/cold serving state;
- cache as a second authority;
- object-store dependency for compact memories;
- stage/phase enum;
- automatic raw transcript/session ingestion;
- automatic nomination or promotion;
- inferred preferences;
- a duplicate coding-agent REST/CLI/SDK mutation surface;
- another validation/evaluator framework.

Identical agent-visible bytes cannot establish a storage-substrate effect. Do
not spend model calls comparing PostgreSQL, graph, vector, and flat-file stores
when retrieval returns the same card. Consider another physical substrate only
after representative PostgreSQL traffic misses a written latency, scale, or
cost objective.

## 11. Development commands

### Build the current MCP binary

```sh
cargo build -p memphant-mcp
```

The current stdio binary requires these variable names:

```text
MEMPHANT_API_KEY
MEMPHANT_APP_DATABASE_URL
MEMPHANT_AUTHN_DATABASE_URL
```

Never print or commit their values.

### Provision a fully bound key

The current CLI shape is:

```sh
memphant-cli admin create-key \
  --tenant "$TENANT_ID" \
  --subject-id "$SUBJECT_ID" \
  --subject-generation "$GENERATION" \
  --scope "$SCOPE_ID" \
  --actor "$ACTOR_ID" \
  --agent-node "$AGENT_NODE_ID" \
  --database-url "$PROVISIONER_DATABASE_URL"
```

Task 1 extends this path with explicit capabilities. Coding keys default to no
forget and no history audit.

### Current Codex recall-only configuration

Until Tasks 6–7 land:

```toml
[mcp_servers.memphant]
command = "/absolute/path/to/target/debug/memphant-mcp"
args = ["stdio"]
env_vars = [
  "MEMPHANT_API_KEY",
  "MEMPHANT_APP_DATABASE_URL",
  "MEMPHANT_AUTHN_DATABASE_URL",
]
required = true
enabled_tools = ["recall"]
```

Inspect with:

```sh
codex mcp list
codex mcp get memphant
```

Current Claude local registration:

```sh
claude mcp add --scope local memphant -- \
  /absolute/path/to/target/debug/memphant-mcp stdio
```

Task 7 replaces manual voluntary recall as the default Codex UX. Explicit MCP
tools remain useful for agent writes and deliberate recall.

### Generate public MCP artifacts

Never hand-edit generated JSON:

```sh
cargo run -q -p memphant-mcp -- --list-tools-json \
  > mcp/memphant.tools.v1.json
cargo run -q -p memphant-mcp -- --list-resources-json \
  > mcp/memphant.resources.v1.json
```

Task 6 owns the MCP artifact change. Regenerate once after the router stabilizes.

## 12. Test strategy

### During Tasks 1–7

Use BDD/TDD at the narrowest shared seam:

1. write one focused behavior that fails for the missing contract;
2. capture the real red result;
3. implement the smallest root fix shared by all callers;
4. run the focused module/contracts;
5. inspect the diff and commit the task;
6. do not run the full harness on every edit.

These are product-contract tests, not agent-efficacy validators. They should
prove authorization, lifecycle, bitemporal selection, byte delivery, erasure,
and error behavior deterministically. Natural usefulness is judged later by a
fresh coding agent and human review, not by a new synthetic scoring harness.

### Scratch PostgreSQL contracts

Requires a reachable base PostgreSQL used only to provision an ephemeral
database:

```sh
bash scripts/with_scratch_db.sh \
  postgres://memphant:memphant@localhost:5432/memphant \
  MEMPHANT_TEST_DATABASE_URL \
  cargo test -p memphant-store-postgres -p memphant-worker \
    --all-targets -- --ignored --test-threads=1
```

The script creates, migrates, and drops the scratch database. It must never
point task data at a Syndai production database.

### Real binary end-to-end probe

With the compose PostgreSQL service listening on port 5432:

```sh
DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant \
  bash scripts/e2e_probe.sh
```

Task 8 extends this probe rather than creating a separate experiment harness.

### Final repository harness

Run only after implementation reaches Task 9:

```sh
python3 -m pytest tests/ -q
python3 scripts/check_spec_drift.py
python3 scripts/instrument_power.py --check
python3 scripts/check_evidence_contract.py
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
cargo test --doc
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant MEMPHANT_TEST_DATABASE_URL cargo test -p memphant-store-postgres -p memphant-worker --all-targets -- --ignored --test-threads=1
cargo run -p memphant-cli -- db lint --provider plain-postgres
cargo run -p memphant-cli -- db lint --provider supabase
cargo run -p memphant-cli -- db lint --provider neon
python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run
DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant bash scripts/e2e_probe.sh
```

Do not weaken `AGENTS.md` because the user rejected validators for agent-value
judgment. The evidence/spec/power checks remain repository integrity gates.

## 13. Secrets, databases, and caches

- Ordinary builds, unit tests, integration tests, provider lint, and no-model
  verification are secret-free. Do not wrap them in Doppler.
- Only an explicitly authorized secret-consuming or paid command may use:

  ```sh
  doppler run --project syndai --config dev -- <command>
  ```

- Never print, download, copy, or persist Doppler values.
- Never use Syndai production PostgreSQL for MemPhant tests or experiments
  without explicit authorization for that exact operation.
- Preserve unrelated dirty work.
- Do not delete `~/.cache/memphant-bench/`, `~/.cache/memphant/`, or the pinned
  local embedder cache. They contain expensive corpora and non-recoverable or
  paid campaign state.

## 14. Bounded post-implementation evidence

No paid call is authorized by this handoff. First complete Task 8's $0 proof.
If it passes and the user explicitly authorizes the tranche:

1. run one fresh Codex C0/M1 natural pair with automatic delivery;
2. run one Claude portability replay only if the Codex pair shows material
   memory use or decision value;
3. hard cap the tranche at three task calls and $15;
4. use a fresh real task and a source predating it;
5. keep current docs, `rg`, LSP, git, prompt, tools, base, and delivery overhead
   equal;
6. judge material task decisions/defects, treatment-only harm, source use,
   tokens, calls, and time;
7. treat a tie as no evidence and keep the feature default-off.

Do not create a validator or statistical campaign. Do not expand to memory
kinds, source lanes, automated nomination, or alternate stores without a new
decision and authorization.

## 15. Git and documentation discipline

The branch contains these relevant commits after `c3838d6c`:

```text
6ff2c22b docs: plan coding agent memory MCP
bf5383df feat(mcp): bind recall to live principal
601e2ce9 fix(mcp): isolate recall execution and principal
6b6d7ab3 test(mcp): close recall transport regressions
b855859c fix(mcp): declare recall failure envelopes
40f2099a fix(mcp): preserve complete procedure recall
f084b12a docs: close coding memory liveness gate
d0d8a57e test: close coding memory verification
39806163 docs: design portable coding agent memory
fd95c4d9 docs: close coding memory audit boundary
2cbeccec docs: plan portable coding agent memory
```

Commit each implementation task locally using the commit message named in the
flow. Do not push, merge, update STATUS, or mark the portable flow complete
unless the user explicitly requests it and the named proof exists.

This handoff and its companion prompt are documentation only. They do not
change the portable flow stage or make a product claim.

## 16. Definition of done for the next session

The next session is successful when Task 1 alone is complete:

- the migration/bootstrap has typed capability and write-grant roots;
- key provisioning defaults safely;
- every operation can use one live-principal resolver;
- forget/audit enforcement has no alternate service/HTTP/CLI bypass;
- in-memory and scratch PostgreSQL focused behaviors pass;
- generated artifacts are changed only if Task 1 owns them;
- the diff is reviewed for unrelated changes and secrets;
- one local commit is created;
- the session reports exact red/green commands and remaining Tasks 2–9;
- no paid calls, full quality campaign, push, or STATUS mutation occurred.

Use the companion copy-paste prompt:
[`2026-08-14-portable-coding-agent-memory-next-session-prompt.md`](2026-08-14-portable-coding-agent-memory-next-session-prompt.md).
