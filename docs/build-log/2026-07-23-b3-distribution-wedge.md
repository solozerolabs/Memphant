# 2026-07-23 — B3 distribution-wedge gate

## Decision and boundary

**B3 is built and its distribution-wedge product gate passes.** MemPhant's
governed database-backed memory remains the sole authority. The Anthropic
memory-tool surface and MCP resources are bounded views over the same B2
canonical projection and mutation service: there is no file-side database,
watcher, reconciler, or second projection engine.

This result starts from the exact clean B2 HEAD
`9c52b8e4bd3f75420fb64df99e64245ff716336b`. It completes B3 only. It does not
land or deploy B2/B3, start B4, call a model, or alter the frozen Deep campaign.

## Current-contract research and architectural call

The implementation was checked against these primary sources on 2026-07-23:

- Anthropic's GA memory-tool documentation and Python reference implementation:
  <https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool> and
  <https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py>.
  The current GA tool is `memory_20250818`, is client-side, uses `/memories`, and
  exposes exactly `view`, `create`, `str_replace`, `insert`, `delete`, and
  `rename`. The TypeScript helper was also checked at
  <https://github.com/anthropics/anthropic-sdk-typescript/blob/main/examples/tools-helpers-memory.ts>.
- Claude Code memory documentation:
  <https://code.claude.com/docs/en/memory>. Auto memory remains a per-project
  `MEMORY.md` index with sibling topic files; startup loading is bounded to the
  first 200 lines or 25 KiB.
- MCP 2025-11-25 resources, tools, roots, and sampling specifications:
  <https://modelcontextprotocol.io/specification/2025-11-25/server/resources>,
  <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>,
  <https://modelcontextprotocol.io/specification/2025-11-25/client/roots>, and
  <https://modelcontextprotocol.io/specification/2025-11-25/client/sampling>.
  Resources require capability negotiation and bounded list/read behavior.
  Roots and sampling are not deprecated capabilities; only sampling's
  `includeContext` values are soft-deprecated. The plan/spec now state the
  current contract. B3 advertises tools and resources only because it neither
  needs filesystem roots nor requests model sampling.
- OpenViking architecture and repository:
  <https://docs.openviking.ai/en/concepts/01-architecture> and
  <https://github.com/volcengine/OpenViking/>. Its virtual filesystem,
  hierarchical L0/L1/L2 context, and AGFS authority are useful distribution
  pressure, but adopting it would introduce another authority and a model-heavy
  extraction pipeline.
- Official MCP reference servers:
  <https://github.com/modelcontextprotocol/servers>. The reference memory server
  is graph-oriented rather than a projection of MemPhant's governed units.

The durable call is therefore one projection adapter around B2's existing
`MemoryService`, not OpenViking, a graph-memory peer, or an on-disk replica.
`MEMORY.md` is a deterministic read-only index. Editable bounded topic files
map one-to-one to canonical unit IDs, and every mutation submits a
base-fingerprint- and plan-digest-bound serializable `file_sync` operation.

## Implemented public contracts

- The Anthropic handler declares `{"type":"memory_20250818","name":"memory"}`
  and strictly decodes the six GA commands. `/memories/MEMORY.md` is protected;
  paths use bounded visible-ASCII components with traversal, encoding, hidden
  name, file/directory collision, and length rejection. Claude Code gets its
  Markdown topic-file shape; the same projection also implements GA nested
  text files, two-level directory views, directory move, and recursive delete.
  Binary/image rendering is deliberately unsupported because canonical memory
  units are text. `view` truncates default file text at 16,000 Unicode
  characters before rendering six-column line numbers; ranged views page the
  bounded body. `insert`, replacement, and success/error responses follow the
  official semantics.
- The Claude Code projection bounds `MEMORY.md` at 25 KiB/200 lines and topic
  files at 64 KiB. Directory views, entries, default views, and change snippets
  are independently bounded.
- MCP now negotiates `tools` plus `resources`, declares four URI templates, and
  lists deterministic memory-unit resources in pages of at most 100. Cursors
  bind the projection fingerprint and fail closed when stale. Unit, episode,
  resource, and trace reads strictly parse UUID URIs and cap returned content
  at 64 KiB. Raw stored resources must be textual and currently fail closed
  when any persisted ACL is non-empty, until the canonical ACL evaluator is
  available.
- A public admin key can be minted with one complete server-resolved context
  (`subject_id`, generation, scope, actor, and agent node). Partial context is
  rejected. Handler construction revalidates that binding and clamps mutation
  trust to the API-key ceiling.
- `mcp/memphant.resources.v1.json` is owned by the MCP binary and its generated
  output is drift-tested. It was regenerated, never hand-edited.
- The real-binary E2E probe starts `memphant-mcp stdio`, negotiates resources,
  repeats listing for determinism, reads a resource, and guarantees child
  cleanup on failure.

## Focused product proof and review hardening

`cargo test -p memphant-mcp --test distribution_wedge` passed **8/8** tests.
Together with the four module tests and schema drift test, the coverage proves:

1. exact GA declaration/JSON decoding and typed failures for all six commands;
2. governed create/correct/insert/delete/rename state transitions and a
   byte-identical read-only fixed point;
3. bounded Claude Code index/topic shape plus path traversal, malformed path,
   collision, content-size, and uniqueness failures;
4. tools/resources capability declaration, deterministic identifiers/order,
   101-item pagination, stable repeat listing, stale cursor rejection, all four
   read URI kinds, malformed URI rejection, and oversized-read denial;
5. cross-tenant isolation and denial without a complete API-key context; and
6. API-key trust-ceiling enforcement for memory-tool mutations.
7. context-bound idempotency without same-tenant cross-context collisions,
   ACL fail-closed resource reads, textual MIME enforcement, UTF-8-safe
   Unicode-character long-view truncation, nested text directories, recursive
   directory operations, and exact directory-byte accounting.
8. fail-closed canonical projection when native governed writes attempt the
   reserved generated index or create file/directory prefix collisions; only
   `memory_file` units may decode the reserved virtual-path fact-key prefix.

Final review closed the substantive issues before the proof was refreshed:
mutation trust is clamped to the key ceiling; stored-resource ACLs fail closed;
binary MIME is never mislabeled as MCP text; idempotency binds the complete
context; `insert` and `view` use the official line-array behavior;
`str_replace` returns a bounded documented line-numbered snippet; long UTF-8
lines honor the 16,000-character contract, directory listings honor their byte
bounds; and the E2E probe reads
the exact unit it seeded and cleans up the MCP child even on failure. Backend
context-resolution failures also distinguish absence/scope denial from backend
unavailability without exposing raw database errors.

## Complete verification

Every result below is from the final source tree.

- `cargo test -p memphant-mcp --test distribution_wedge` — PASS, **8 passed / 0
  failed**.
- MCP schema/resource drift coverage — PASS, **5 passed / 0 failed**.
- `cargo test -p memphant-cli --test file_plane_n12 -- --nocapture` — PASS,
  **1/1 test representing 12/12 cases**. This re-proves the B2
  compile → sync → compile fixed point in the final combined B3 tree; B3 calls
  that same digest-bound `file_sync` service rather than duplicating it.
- `python3 -m pytest tests/ -q` — UNMET, **697 passed / 24 failed / 11
  skipped**. The failures are inherited from B2: 15 stale r15 runner-call
  contracts, six stale breadcrumb runner-call contracts, one stale embedder-arm
  pin, one intentionally frozen P1 adapter OpenAPI hash, and one live private
  Syndai corpus-span drift assertion. The 11 skips are eight absent private
  `coding_events_golden.jsonl` fixtures, one packaged-binary/scratch-Postgres
  opt-in, and two WSA scratch-Postgres opt-ins.
- The three public opt-in Python integrations were then run through an
  ephemeral scratch database with `MEMPHANT_LME_PACKAGED_INTEGRATION=1` — PASS,
  **3 passed / 0 failed**. The eight private-fixture tests remain unavailable;
  the repository Python gate remains unmet.
- `cd web && npm test` — PASS, **23 passed / 0 failed / 1 skipped** after a
  clean `npm ci`; the skipped browser check is reported, not counted as pass.
- `python3 scripts/check_spec_drift.py` — SKIPPED with exit 0,
  `private_specs_missing`; private mirror parity is not proven.
- `cargo fmt --check` — PASS.
- `cargo clippy --all-targets --all-features -- -D warnings` — PASS.
- `cargo test --all-targets --all-features` — PASS, **692 passed / 0 failed /
  92 ignored**.
- `cargo test --doc` — PASS, **0 tests / 0 failed**.
- The AGENTS.md scratch-Postgres store/worker command — PASS, **77 passed / 0
  failed / 0 ignored / 16 filtered**; the helper provisioned and dropped a
  separate migrated database.
- Provider lint for `plain-postgres`, `supabase`, and `neon` — PASS for all
  three.
- Migration dry-run — PASS, **2 ordered migrations planned**.
- `DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant bash scripts/e2e_probe.sh`
  — PASS, including `MCP PROBE: resources=1 read=ok deterministic=ok` and
  `E2E PROBE: ALL CHECKS PASSED`.

## Ledger predicate and non-claims

B3's scoped ledger predicate is complete: the current six-command schema and
bounded virtual text-file/Claude Code profile, plus tenant-bound MCP resources, are
built and proven in this commit. The broader repository gate is not green
because the inherited Python failures and unavailable private mirror/fixtures
above remain exact unmet predicates.

This is mechanism and local integration proof only. There was no push, merge,
PR, landing, deployment, paid/model call, production write, private Syndai
change, B4 work, accuracy promotion, Deep promotion, SOTA claim, or cutover.
The immutable `run-65981e4f` root and dirty P1 worktree were not changed. Before
the intended consolidated landing, B4 receipts and calibrated answers—and the
remaining preregistered tri-domain gates—still have to be completed and proven.
