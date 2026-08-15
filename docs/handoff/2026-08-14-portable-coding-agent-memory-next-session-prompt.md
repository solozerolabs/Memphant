# Initial prompt: implement portable coding-agent memory Task 1

Copy everything below into a fresh Codex session.

---

Work in this exact worktree:

`/Users/sidsharma/Memphant/.worktrees/coding-agent-memory-mcp`

You are taking forward MemPhant's portable bidirectional coding-agent memory
work. Be the implementation owner and final reviewer for this session. Keep the
work bounded to **Task 1 only**. Do not proceed to Task 2 without my explicit
approval.

First, re-establish truth from the repository. Run:

```sh
cd /Users/sidsharma/Memphant/.worktrees/coding-agent-memory-mcp
git status --short
git branch --show-current
git rev-parse HEAD
python3 ~/.local/share/feature-flow/current/scripts/feature-flow-state.py get
```

Expected starting branch is `codex/coding-agent-memory-mcp`; the handoff HEAD
before its documentation commit was
`2cbeccec9b0aff0279375867fb9b90e96719e7d6`. Preserve any user changes if the
worktree is no longer clean.

Read completely, in this order:

1. `/Users/sidsharma/Memphant/.worktrees/coding-agent-memory-mcp/AGENTS.md`
2. `/Users/sidsharma/Memphant/.worktrees/coding-agent-memory-mcp/docs/handoff/2026-08-14-portable-coding-agent-memory-handoff.md`
3. `/Users/sidsharma/Memphant/.worktrees/coding-agent-memory-mcp/docs/flows/portable-bidirectional-coding-agent-memory.md`
4. Task 1's exact callers, stores, migrations, tests, and generated ownership

The completed historical flow
`docs/flows/outcome-coupled-evolution.md` is evidence/regression history, not
this implementation plan. The active portable flow has completed understand,
plan, and review-plan; implementation is next.

## Product outcome

MemPhant must become a standalone, token-efficient read/write memory service
for Codex, Claude, and other coding agents. PostgreSQL is the single authority;
files are read-only projections; git, `rg`, LSP, and repository files remain
authoritative for current code. Syndai is an optional client, never a runtime
dependency.

The final agent contract will be exactly five tools:

```text
recall
remember
correct_memory
invalidate_memory
report_memory_use
```

Do not build those later tools in this session. Task 1 establishes the security
root they require.

## What is already real

The branch already has:

- query-only `recall({query})`;
- per-call key revalidation for recall;
- full principal binding and drift/revocation failure;
- provider-free Fast recall, limit one, 512-token budget;
- typed `hit|empty|unavailable|error` output;
- real scratch PostgreSQL C0/M1/auth-outage proof.

Do not rewrite this recall implementation. Generalize its live-principal seam
only as Task 1 requires.

The previous two-call Terra-high liveness pair found zero recall/MCP calls in
both arms. Voluntary pull is therefore off by default. This is not a reason to
run another pair. Automatic boundary delivery is Task 7, after lifecycle safety
exists.

## Implement Task 1 only

Follow
`docs/flows/portable-bidirectional-coding-agent-memory.md`, Task 1, exactly:

**Add operation capabilities and one live-principal resolver.**

Required product behavior:

1. Add durable API-key booleans `can_forget` and `can_audit_history`, both
   `NOT NULL DEFAULT false`.
2. Add explicit `scope_policy.allow_write`, default false. Existing read grants
   do not imply write authority.
3. Widen the mutation ledger to admit the `invalidate` verb needed by Task 3.
4. Add the two partial indexes required by the reviewed plan: one open compact
   generation per stable fact key, and the open invalidation exact-body digest
   lookup. Reuse PostgreSQL predicates; do not add an application-side index.
5. Update the canonical bootstrap/migration manifest, compatibility revision,
   provider-safe grants/functions, key row, store/runtime mappings, and
   `admin create-key` path. Do not create a second capability table.
6. Extract one shared live-principal resolver from the current MCP recall path.
   Every future MCP tool and resource must use it. It must re-look up the key on
   every call and validate exact key id/hash, tenant, full context binding,
   subject generation, and trust behavior.
7. Revoked/missing/partial/drifted credentials fail closed. A lower live trust
   ceiling applies immediately; a raised ceiling requires restart.
8. Enforce `can_forget` on every existing permanent-deletion HTTP/service/CLI
   path and `can_audit_history` on every explicit `transaction_as_of` or
   `valid_at` path. Do not rely on hiding an MCP tool.
9. Keep coding-agent key defaults without forget or audit capability.
10. Preserve tenant binding, `memphant` schema ownership, plain PostgreSQL,
   Supabase, and Neon portability.

Primary code pointers:

- `crates/memphant-core/src/lib.rs:809` — `MutationVerb`
- `crates/memphant-core/src/lib.rs:1625` — `ApiKeyRow`
- `crates/memphant-mcp/src/lib.rs:140` — `BoundTenant`
- `crates/memphant-mcp/src/lib.rs:452` — current startup binding
- `crates/memphant-mcp/src/lib.rs:467` — current recall-only live context
- `crates/memphant-store-postgres/src/store.rs:5018` — API-key lookup
- `crates/memphant-server/src/lib.rs:144` — `AuthedTenant`
- `crates/memphant-server/src/lib.rs:531` — forget handler
- `crates/memphant-types/src/lib.rs:1974` — recall temporal selectors
- `memphant_migrations/versions/20260703_001_wsa_bootstrap.sql` around lines 218,
  621, 733, and 1340 — scope policy, mutation ledger, API key, provisioning

Before editing any shared function, use `rg` to inspect all callers and all
store/runtime implementations. Fix the root once; keep mappings exhaustive.

## Engineering method

- Use KISS, DRY, BDD/TDD.
- Reuse existing structs, mutation ledger, scope policy, migration pattern, and
  auth flow. No compatibility aliases, capability framework, policy DSL, new
  service, or speculative abstraction.
- Write the smallest focused failing behavioral tests first. Capture a valid
  RED, implement the root fix, then capture GREEN.
- Test authorization at the public boundary and persistence in both in-memory
  and scratch PostgreSQL paths.
- No validators or LLM judges for product value. Deterministic tests are allowed
  and required for auth, migration, erasure gates, and bitemporal access.
- Do not run paid model calls.
- Do not run the full repository harness during iteration.
- Do not edit README, STATUS, the five-tool router, Codex plugin, lifecycle
  implementation, or generated MCP artifacts unless Task 1's owning generator
  truly changes them.
- Do not weaken AGENTS.md. Update it only if Task 1 reveals one concise durable
  invariant not already present.
- Do not use Doppler for ordinary work. Never print secrets. Use only scratch
  PostgreSQL, never Syndai production data.
- Preserve benchmark caches and unrelated dirty work.

Use parallel subagents only for independent read-only code audits or a final
reverse critique. Do not let multiple agents edit overlapping Task 1 files.
You are the final judge of all subagent output and must verify their claims from
the actual diff and focused commands.

## Focused verification and stop condition

Select the narrowest existing test modules named by Task 1. At minimum cover:

- migration manifest/head and provider-safe provisioning;
- both capability defaults and explicit grants;
- every live-principal drift/revocation branch;
- coding-key denial for forget and as-of audit with no mutation/read leakage;
- owner capability success;
- scope read grant not implying write;
- in-memory/store/runtime mapping parity;
- idempotent mutation-ledger handling of `invalidate` if the verb is added now.

Use the scratch wrapper for PostgreSQL behavior if needed:

```sh
bash scripts/with_scratch_db.sh \
  postgres://memphant:memphant@localhost:5432/memphant \
  MEMPHANT_TEST_DATABASE_URL \
  cargo test -p memphant-store-postgres -p memphant-worker \
    --all-targets -- --ignored --test-threads=1
```

Run `cargo fmt --check` and `git diff --check` before committing. Do not run the
full harness unless a newly discovered cross-cutting risk makes it necessary;
if so, explain why before running it.

When focused behavior is green:

1. inspect `git diff` and `git status`;
2. confirm no secret, private Syndai path/content, unrelated edit, or generated
   artifact drift;
3. commit locally as:

   `feat: bind coding memory operations to live capabilities`

4. stop after Task 1;
5. report the exact files changed, RED/GREEN commands and counts, commit hash,
   remaining risks, and why Tasks 2–9 remain unopened;
6. do not push, merge, update STATUS, or mark the portable flow complete.

No paid work is authorized. No general coding-agent improvement or SOTA claim
is authorized.

---
